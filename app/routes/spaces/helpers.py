"""Shared helpers for the spaces routes."""
from __future__ import annotations

from functools import wraps

from flask import abort, flash, render_template
from sqlalchemy import or_

from app import db
from app.models import (
    Department, EventCycle, ROLE_SPACE_ADMIN, Space, SPACE_KIND_COMBO,
    SpaceAssignment, SpaceCombinationMember, SpaceEventOverride,
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
    """Rows for the Spaces table, parents followed by their slices.

    Loads assignments and overrides in two queries rather than per row. A
    fifty-room venue with thirty departments makes per-row lookups costly.
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

    # Two maps from one query: what each combination holds, and which
    # combinations hold each space.
    members_by_combo: dict[int, list] = {}
    member_of: dict[int, list] = {}
    combo_ids = [s.id for s in spaces if s.kind == SPACE_KIND_COMBO]
    if combo_ids:
        member_rows = (
            db.session.query(SpaceCombinationMember, Space)
            .join(Space, Space.id == SpaceCombinationMember.member_space_id)
            .filter(SpaceCombinationMember.combination_space_id.in_(combo_ids))
            .order_by(Space.sort_order, Space.code)
            .all()
        )
        for link, member_space in member_rows:
            members_by_combo.setdefault(
                link.combination_space_id, []).append(member_space)
            member_of.setdefault(
                link.member_space_id, []).append(link.combination_space_id)

    by_id = {s.id: s for s in spaces}

    # The display map places combinations under a room. Coverage needs the
    # physical tree instead, so a slice's real parent is tracked separately.
    real_children: dict[int, list] = {}
    for space in spaces:
        if space.parent_id:
            real_children.setdefault(space.parent_id, []).append(space)

    def is_accounted_for(space) -> bool:
        """True when this space has been dealt with for this event.

        A slice covered by an assigned room, and a room split across
        assigned slices, both count. Otherwise assigning Chesapeake A/B/C
        would leave its three slices in the Unassigned card.
        """
        if assigned.get(space.id):
            return True
        if space.parent_id and assigned.get(space.parent_id):
            return True
        if any(assigned.get(c.id) for c in real_children.get(space.id, [])):
            return True
        return any(assigned.get(cid) for cid in member_of.get(space.id, []))

    def display_parent_id(space):
        """Where this space renders. A combination sits under the room its
        first member belongs to, so its relationship to the slices is
        visible. A combination spanning rooms renders under the first.
        """
        if space.kind == SPACE_KIND_COMBO:
            members = members_by_combo.get(space.id, [])
            if members:
                first = members[0]
                return first.parent_id or first.id
            return None
        return space.parent_id

    children: dict[int, list] = {}
    for space in spaces:
        parent = display_parent_id(space)
        if parent is not None and parent in by_id:
            children.setdefault(parent, []).append(space)

    # A combination renders below its room's slices, not interleaved among
    # them by code. Reading the slices first and then what was built from
    # them is the order the page is meant to tell.
    for bucket in children.values():
        bucket.sort(key=lambda s: (s.kind == SPACE_KIND_COMBO,
                                   s.sort_order or 0, s.code))

    def to_row(space, depth: int) -> dict:
        override = overrides.get(space.id)
        departments = assigned.get(space.id, [])
        return {
            "space": space,
            "depth": depth,
            "parent_id": display_parent_id(space),
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
            # "Made up of" lists what a room divides into. A combination is
            # built from those pieces, not one of them.
            "child_codes": [c.code for c in children.get(space.id, [])
                            if c.kind != SPACE_KIND_COMBO],
            "member_names": [m.name for m in members_by_combo.get(space.id, [])],
            "member_of_names": [by_id[c].name for c in member_of.get(space.id, [])
                                if c in by_id],
            "is_accounted_for": is_accounted_for(space),
        }

    ordered: list[dict] = []
    seen: set[int] = set()
    for space in spaces:
        if display_parent_id(space) in by_id:
            continue
        ordered.append(to_row(space, 0))
        seen.add(space.id)
        for child in children.get(space.id, []):
            ordered.append(to_row(child, 1))
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
