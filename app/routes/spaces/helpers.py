"""Shared helpers for the spaces routes."""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, render_template
from sqlalchemy import or_

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space,
    SpaceAssignment, SpaceEventOverride,
)
from app.routes import get_user_ctx


def get_effective_space_name(space, event_cycle_id: int) -> str:
    """Return the space's name for one event.

    An override row renames a space for a single event; Expo Hall B is
    "Consoles" at Super MAGFest 2027. No row, or a row with no alias, means
    the venue name applies.
    """
    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id,
        event_cycle_id=event_cycle_id,
    ).first()

    if override and override.alias:
        return override.alias
    return space.name


def flash_if_too_long(value: str | None, field: str, limit: int = 128) -> bool:
    """Flash an error and return True when value exceeds limit.

    Every free-text field here writes to a String(128) column. SQLite does
    not enforce that length, so an unguarded write fails only on Postgres.
    """
    if value and len(value) > limit:
        flash(f"That {field} is too long; {limit} characters is the limit",
              "error")
        return True
    return False


def parse_optional_int(raw: str | None) -> tuple[int | None, bool]:
    """Parse a form field that may be blank or a whole number.

    Returns (None, True) for a blank field, meaning clear the value.
    Returns (None, False) when the field is non-empty and not an integer,
    so the caller can reject "1,200" instead of silently storing None.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, True
    try:
        return int(raw), True
    except ValueError:
        return None, False


def is_space_admin(user_ctx) -> bool:
    """True for space admins and for super admins.

    No query. active_user_roles() already returns every role code the user
    holds regardless of scope, and this role sets no scope columns.
    """
    if user_ctx.is_super_admin:
        return True
    return ROLE_SPACE_ADMIN in user_ctx.roles


def require_space_admin(f):
    """Decorator requiring space admin access."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import redirect, url_for, session, current_app

        if not session.get("active_user_id") and not current_app.config.get("DEV_LOGIN_ENABLED"):
            return redirect(url_for("auth.login_page"))

        user_ctx = get_user_ctx()
        if user_ctx.user_id is None:
            return redirect(url_for("auth.login_page"))

        if not is_space_admin(user_ctx):
            abort(403, "Space admin access required")
        return f(*args, **kwargs)
    return decorated_function


def render_space_admin_page(template: str, **ctx):
    """Render a spaces page with user context. Requires space admin access."""
    user_ctx = get_user_ctx()
    if not is_space_admin(user_ctx):
        abort(403, "Space admin access required")
    return render_template(template, user_ctx=user_ctx, **ctx)


def resolve_event_cycle(code: str | None):
    """Return the requested event cycle, or the default when none is asked for."""
    query = db.session.query(EventCycle).filter(EventCycle.is_active.is_(True))
    if code:
        return query.filter(EventCycle.code == code).first()
    return (
        query.filter(EventCycle.is_default.is_(True)).first()
        or query.order_by(EventCycle.sort_order, EventCycle.code).first()
    )


def build_space_rows(cycle, include_archived: bool = False) -> list[dict]:
    """Rows for the Spaces table, rooms followed by their slices.

    Loads assignments and overrides in two queries rather than per row. A
    fifty-room venue with thirty departments makes per-row lookups costly.
    Every space renders exactly once; combining is a flag on a slice's
    override, not a row of its own.
    """
    if cycle is None or cycle.venue_id is None:
        return []

    query = (
        db.session.query(Space)
        .filter(Space.venue_id == cycle.venue_id)
        # A pop-up belongs to one event. A permanent room has no event.
        .filter(or_(Space.event_cycle_id.is_(None),
                    Space.event_cycle_id == cycle.id))
    )
    if not include_archived:
        query = query.filter(Space.is_active.is_(True))
    spaces = query.order_by(Space.sort_order, Space.code).all()

    overrides = {
        o.space_id: o
        for o in db.session.query(SpaceEventOverride)
        .filter(SpaceEventOverride.event_cycle_id == cycle.id)
        .all()
    }

    assigned: dict[int, list] = {}
    rows = (
        db.session.query(SpaceAssignment, Department)
        .join(Department, Department.id == SpaceAssignment.department_id)
        .filter(SpaceAssignment.event_cycle_id == cycle.id)
        .order_by(Department.name)
        .all()
    )
    for assignment, department in rows:
        assigned.setdefault(assignment.space_id, []).append(department)

    by_id = {s.id: s for s in spaces}

    children: dict[int, list] = {}
    for space in spaces:
        if space.parent_id:
            children.setdefault(space.parent_id, []).append(space)

    # A primary is whatever a fold points at. Built from the overrides
    # already loaded above; spaces is already in catalog order, so a
    # primary's group lists in that same order.
    combined_groups: dict[int, list] = {}
    for space in spaces:
        override = overrides.get(space.id)
        if override and override.combined_into_space_id:
            combined_groups.setdefault(
                override.combined_into_space_id, []).append(space)

    def is_accounted_for(space) -> bool:
        """True when this space has been dealt with for this event.

        A slice covered by an assigned room, a room split across assigned
        slices, and a slice folded into an assigned primary all count.
        Otherwise assigning Chesapeake A/B/C would leave its three slices
        in the Unassigned card.
        """
        if assigned.get(space.id):
            return True
        if space.parent_id and assigned.get(space.parent_id):
            return True
        if any(assigned.get(c.id) for c in children.get(space.id, [])):
            return True
        override = overrides.get(space.id)
        if override and override.combined_into_space_id:
            return bool(assigned.get(override.combined_into_space_id))
        return False

    def to_row(space, depth: int) -> dict:
        override = overrides.get(space.id)
        departments = assigned.get(space.id, [])
        group = combined_groups.get(space.id, [])
        # A primary's area is the sum of itself and its group. An
        # incomplete sum is worse than none: the number is read to size an
        # event, and a group missing one slice's area would understate
        # itself while looking authoritative. Unknown until every member
        # has one.
        areas = [space.area_sqft] + [m.area_sqft for m in group]
        combined_area_sqft = (sum(areas)
                              if all(a is not None for a in areas) else None)
        return {
            "space": space,
            "depth": depth,
            "parent_id": space.parent_id,
            "has_children": bool(children.get(space.id)),
            # The base venue name is the stable heading; the alias pill
            # carries the override instead of replacing it. The admin table
            # is where Event Ops manages overrides, so both stay visible.
            "display_name": space.name,
            "alias": override.alias if override else None,
            "departments": departments,
            "is_shared": len(departments) >= 2,
            "is_available": override.is_available if override else True,
            "unavailable_reason": (override.unavailable_reason
                                   if override else None),
            # "Made up of" lists what a room divides into.
            "child_codes": [c.code for c in children.get(space.id, [])],
            "combined_into_space_id": (override.combined_into_space_id
                                       if override else None),
            "combined_codes": [m.code for m in group],
            "combined_area_sqft": combined_area_sqft,
            "is_accounted_for": is_accounted_for(space),
            # Set below, once the room's own row exists to point at.
            "covered_by": None,
        }

    def covering_room(slice_row, room_row):
        """The room row whose departments this slice inherits, or None.

        This writes nothing. Coverage reads the room's one SpaceAssignment,
        so unassigning the room clears every slice at once. A slice with
        departments of its own keeps them; the room never overwrites an
        assignment an admin recorded.
        """
        if slice_row["departments"] or not room_row["departments"]:
            return None
        return room_row

    ordered: list[dict] = []
    seen: set[int] = set()
    for space in spaces:
        if space.parent_id in by_id:
            continue
        parent_row = to_row(space, 0)
        ordered.append(parent_row)
        seen.add(space.id)
        for child in children.get(space.id, []):
            child_row = to_row(child, 1)
            child_row["covered_by"] = covering_room(child_row, parent_row)
            ordered.append(child_row)
            seen.add(child.id)

    # Archiving a room does not cascade to its slices. A slice whose parent
    # got filtered out is surfaced at top level instead of dropped, because a
    # silently missing room reads as data loss.
    for space in spaces:
        if space.id not in seen:
            ordered.append(to_row(space, 0))
            seen.add(space.id)

    return ordered


def build_space_stats(rows: list[dict]) -> dict:
    """Counts for the four stat cards.

    Unassigned excludes unavailable spaces. Counting a closed room as
    unassigned nags Event Ops to assign it on every page load. It also
    excludes a space covered by an assigned parent, slice, or combination.
    Assigning a room whole then does not strand its slices in the count.
    """
    return {
        "total": len(rows),
        "assigned": sum(1 for r in rows if r["departments"]),
        "shared": sum(1 for r in rows if r["is_shared"]),
        "unassigned": sum(1 for r in rows
                          if not r["is_accounted_for"] and r["is_available"]),
    }
