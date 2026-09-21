"""Spaces pages: the room catalog and per-event assignments."""
from __future__ import annotations

from flask import abort, flash, redirect, request, url_for
from sqlalchemy import or_

from app import db
from app.models import (
    CONFIG_AUDIT_ARCHIVE, CONFIG_AUDIT_CREATE, CONFIG_AUDIT_RESTORE,
    CONFIG_AUDIT_UPDATE, Department, EventCycle, Space, SpaceAssignment,
    SpaceCombinationMember, SpaceEventOverride, SPACE_KIND_COMBO,
    SPACE_KIND_FREEFORM, SPACE_KIND_ROOM, SPACE_KIND_SLICE, SPACE_KINDS,
)
from app.routes import h
from . import spaces_bp
from .helpers import (
    build_space_rows, build_space_stats, flash_if_too_long,
    parse_optional_int, render_space_admin_page, require_space_admin,
    resolve_event_cycle,
)

# Kind labels shown in the Spaces page's Kind select. Keyed off SPACE_KINDS
# so a new kind fails loudly here instead of rendering with no label.
SPACE_KIND_LABELS = {
    SPACE_KIND_ROOM: "Room",
    SPACE_KIND_SLICE: "Slice of a room",
    SPACE_KIND_COMBO: "Combination (event-scoped, built via Combine spaces)",
    SPACE_KIND_FREEFORM: "Free-form pop-up space",
}


@spaces_bp.get("/")
@require_space_admin
def list_spaces():
    """The Spaces page for one event."""
    cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active.is_(True))
        .order_by(EventCycle.sort_order, EventCycle.code)
        .all()
    )
    cycle = resolve_event_cycle(request.args.get("event"))
    show_archived = request.args.get("show_archived") == "1"
    rows = build_space_rows(cycle, include_archived=show_archived)
    departments = (
        db.session.query(Department)
        .filter(Department.is_active.is_(True))
        .order_by(Department.name)
        .all()
    )

    return render_space_admin_page(
        "spaces/list.html",
        cycles=cycles,
        cycle=cycle,
        rows=rows,
        stats=build_space_stats(rows),
        edit_id=request.args.get("edit", type=int),
        departments=departments,
        show_archived=show_archived,
        space_kinds=[(k, SPACE_KIND_LABELS[k]) for k in SPACE_KINDS],
        SPACE_KIND_ROOM=SPACE_KIND_ROOM,
        SPACE_KIND_COMBO=SPACE_KIND_COMBO,
        SPACE_KIND_SLICE=SPACE_KIND_SLICE,
    )


def _load_space_for_cycle(space_id: int, cycle, *, require_active: bool | None = None):
    """Load a space and confirm it belongs to the cycle's venue and event.

    404s when the space is missing, or when require_active is set and does
    not match. 404s too on a venue mismatch, or when the space's event does
    not match the cycle. event_cycle_id NULL means permanent; it passes the
    event check for any cycle at its venue. A combination or pop-up from
    another event fails the check and is treated as missing. A cycle that
    failed to resolve has nothing to compare against, so both checks are
    skipped.
    """
    space = db.session.get(Space, space_id)
    if space is None:
        abort(404)
    if require_active is not None and space.is_active != require_active:
        abort(404)
    if cycle is not None:
        if space.venue_id != cycle.venue_id:
            abort(404)
        if space.event_cycle_id is not None and space.event_cycle_id != cycle.id:
            abort(404)
    return space


def _code_taken(venue_id, code, event_cycle_id, exclude_space_id=None):
    """True when this code is already used where it would be seen.

    A permanent row and an event row render in the same table, so a code
    shared across that boundary shows one name on two rooms.
    """
    query = db.session.query(Space).filter(
        Space.venue_id == venue_id,
        Space.code == code,
    )
    if exclude_space_id is not None:
        query = query.filter(Space.id != exclude_space_id)
    # An event row is seen beside the permanent rows and its own event's
    # rows. A permanent row is seen in every event, so it clashes with any.
    if event_cycle_id is not None:
        query = query.filter(or_(
            Space.event_cycle_id == event_cycle_id,
            Space.event_cycle_id.is_(None),
        ))
    return query.first() is not None


@spaces_bp.post("/space/new")
@require_space_admin
def create_space():
    """Create a space at the active event's venue.

    The panel offers an explicit Kind select rather than deriving kind
    from the parent picker. This form can also produce a FREEFORM pop-up,
    so an empty parent would not say which was meant. The venue catalog's
    add form derives kind, because it offers no FREEFORM.
    """
    # Imported here, not at module level: app/routes/spaces/ is not an admin
    # package, and a module-level admin import from a non-admin route
    # breaks the `h` helper proxy.
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None or cycle.venue_id is None:
        abort(400, "Pick an event cycle with a venue first")

    back = url_for("spaces.list_spaces", event=cycle.code)

    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip().upper()
    kind = (request.form.get("kind") or "").strip().upper()

    if not name or not code:
        flash("Name and code are required", "error")
        return redirect(back)
    if flash_if_too_long(name, "name"):
        return redirect(back)

    if kind not in SPACE_KINDS:
        flash("Pick a space kind", "error")
        return redirect(back)

    # A COMBO is always event-scoped, and Combine spaces is what scopes it.
    # A COMBO created here would carry no event and break the rule that a
    # space row outlives the event only if it describes physical structure.
    if kind == SPACE_KIND_COMBO:
        flash("Use Combine spaces to build a combination", "error")
        return redirect(back)

    # A pop-up belongs to the event that created it, so it stops showing up
    # at this venue in later cycles. A permanent room has no event.
    event_cycle_id = cycle.id if kind == SPACE_KIND_FREEFORM else None
    if _code_taken(cycle.venue_id, code, event_cycle_id):
        flash(f"A space with code '{code}' already exists at this venue", "error")
        return redirect(back)

    parent_id = request.form.get("parent_id", type=int)
    if parent_id is not None:
        # SQLite does not enforce this FK. A bad or cross-venue id would
        # otherwise be stored silently and 500 on Postgres instead. Only a
        # ROOM can be a parent; a COMBO parent would let dissolving it null
        # out a slice's parent_id, since Space.children has no cascade.
        parent = db.session.get(Space, parent_id)
        if (parent is None or parent.venue_id != cycle.venue_id
                or parent.kind != SPACE_KIND_ROOM):
            flash("Pick a parent room at this venue", "error")
            return redirect(back)

    area_sqft, area_ok = parse_optional_int(request.form.get("area_sqft"))
    if not area_ok:
        flash("Area (sq ft) must be a whole number", "error")
        return redirect(back)

    space = Space(
        venue_id=cycle.venue_id,
        parent_id=parent_id,
        event_cycle_id=event_cycle_id,
        name=name,
        code=code,
        kind=kind,
        dimensions=(request.form.get("dimensions") or "").strip() or None,
        area_sqft=area_sqft,
        location_note=(request.form.get("location_note") or "").strip() or None,
        is_active=True,
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )
    db.session.add(space)
    db.session.flush()

    log_config_change("space", space.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Added {space.name}", "success")
    return redirect(back)


@spaces_bp.post("/space/<int:space_id>")
@require_space_admin
def save_space(space_id: int):
    """Save one row of the Spaces table.

    Writes three tables in one transaction. The space carries its code, the
    override carries the per-event name and availability, and the assignment
    rows carry the departments. This is deliberate, not an accident of
    layout: all three are what "edit this room for this event" means.
    """
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None:
        abort(400, "Pick an event cycle first")

    # A space from another venue is not on this event's page at all; treat
    # it as missing rather than let a crafted POST write orphaned rows.
    space = _load_space_for_cycle(space_id, cycle, require_active=True)

    back = url_for("spaces.list_spaces", event=cycle.code)
    actor = h.get_active_user_id()

    # 1. Space core fields.
    code = (request.form.get("code") or "").strip().upper()
    if code and code != space.code:
        if _code_taken(space.venue_id, code, space.event_cycle_id,
                       exclude_space_id=space.id):
            flash(f"A space with code '{code}' already exists at this venue",
                  "error")
            return redirect(back)
        space.code = code
    space.updated_by_user_id = actor

    # Entry is 145 rows by hand, so a typo has to be fixable in place.
    # A blank name would render a row nobody can identify, so it is refused
    # outright rather than silently kept, which would flash success over
    # the old name.
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("A space needs a name", "error")
        return redirect(back)
    if flash_if_too_long(name, "name"):
        return redirect(back)
    space.name = name

    # Unlike name, dimensions and area clear to None when submitted blank.
    # That is intentional; only the name cannot go empty.
    space.dimensions = (request.form.get("dimensions") or "").strip() or None
    area_sqft, area_ok = parse_optional_int(request.form.get("area_sqft"))
    if not area_ok:
        flash("Area (sq ft) must be a whole number", "error")
        return redirect(back)
    space.area_sqft = area_sqft

    # 2. Per-event override, written only when it says something.
    alias = (request.form.get("alias") or "").strip() or None
    if flash_if_too_long(alias, "alias"):
        return redirect(back)
    is_available = request.form.get("is_available") == "1"
    reason = (request.form.get("unavailable_reason") or "").strip() or None

    override = db.session.query(SpaceEventOverride).filter_by(
        space_id=space.id, event_cycle_id=cycle.id).first()

    if alias or not is_available:
        if override is None:
            override = SpaceEventOverride(
                space_id=space.id,
                event_cycle_id=cycle.id,
                created_by_user_id=actor,
            )
            db.session.add(override)
        override.alias = alias
        override.is_available = is_available
        override.unavailable_reason = reason if not is_available else None
        override.updated_by_user_id = actor
    elif override is not None:
        # Back to the venue name and available. Drop the row rather than
        # storing a row that says nothing; the table is meant to stay sparse.
        db.session.delete(override)

    # 3. Assignments, diffed against the submitted checkboxes.
    selected = {int(v) for v in request.form.getlist("department_ids") if v}
    current = {
        a.department_id: a
        for a in db.session.query(SpaceAssignment).filter_by(
            space_id=space.id, event_cycle_id=cycle.id).all()
    }

    for department_id in selected - current.keys():
        db.session.add(SpaceAssignment(
            space_id=space.id,
            event_cycle_id=cycle.id,
            department_id=department_id,
            created_by_user_id=actor,
            updated_by_user_id=actor,
        ))
    for department_id in current.keys() - selected:
        db.session.delete(current[department_id])

    log_config_change("space", space.id, CONFIG_AUDIT_UPDATE, {
        "event_cycle": cycle.code,
        "name": space.name,
        "dimensions": space.dimensions,
        "area_sqft": space.area_sqft,
        "alias": alias,
        "is_available": is_available,
        "department_ids": sorted(selected),
    })

    db.session.commit()
    flash(f"Saved {space.name}", "success")
    return redirect(back)


def _set_space_active(space_id: int, active: bool, action: str):
    """Archive or restore a space. Retires it at the venue, for every event.

    Per-event unavailability is a SpaceEventOverride row instead; archiving
    is for a room that no longer exists or is never used again.
    """
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    space = _load_space_for_cycle(space_id, cycle)

    space.is_active = active
    space.updated_by_user_id = h.get_active_user_id()
    log_config_change("space", space.id, action)
    db.session.commit()

    flash(f"{'Restored' if active else 'Archived'} {space.name}", "success")
    return redirect(url_for("spaces.list_spaces",
                            event=cycle.code if cycle else None))


@spaces_bp.post("/space/<int:space_id>/archive")
@require_space_admin
def archive_space(space_id: int):
    return _set_space_active(space_id, False, CONFIG_AUDIT_ARCHIVE)


@spaces_bp.post("/space/<int:space_id>/restore")
@require_space_admin
def restore_space(space_id: int):
    return _set_space_active(space_id, True, CONFIG_AUDIT_RESTORE)


@spaces_bp.post("/combine")
@require_space_admin
def combine_spaces():
    """Create a combination this event uses as one space.

    Nothing checks that members are adjacent, share a room, or belong to no
    other combination. All three happen at real events and the admin owns
    correctness.
    """
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None or cycle.venue_id is None:
        abort(400, "Pick an event cycle with a venue first")

    back = url_for("spaces.list_spaces", event=cycle.code)
    name = (request.form.get("name") or "").strip()
    code = (request.form.get("code") or "").strip().upper()
    member_ids = {int(v) for v in request.form.getlist("member_ids") if v}

    if not name or not code:
        flash("Name and code are required", "error")
        return redirect(back)
    if flash_if_too_long(name, "name"):
        return redirect(back)
    if not member_ids:
        flash("Pick at least one space to combine", "error")
        return redirect(back)

    members = db.session.query(Space).filter(
        Space.id.in_(member_ids),
        Space.venue_id == cycle.venue_id,
    ).all()
    if len(members) != len(member_ids):
        flash("One of those spaces is not at this event's venue", "error")
        return redirect(back)

    if _code_taken(cycle.venue_id, code, cycle.id):
        flash(f"A space with code '{code}' already exists at this venue",
              "error")
        return redirect(back)

    actor = h.get_active_user_id()
    combo = Space(
        venue_id=cycle.venue_id,
        event_cycle_id=cycle.id,
        name=name,
        code=code,
        kind=SPACE_KIND_COMBO,
        is_active=True,
        created_by_user_id=actor,
        updated_by_user_id=actor,
    )
    db.session.add(combo)
    db.session.flush()

    for member in members:
        db.session.add(SpaceCombinationMember(
            combination_space_id=combo.id,
            member_space_id=member.id,
            created_by_user_id=actor,
        ))

    log_config_change("space", combo.id, CONFIG_AUDIT_CREATE, {
        "event_cycle": cycle.code,
        "member_ids": sorted(member_ids),
    })
    db.session.commit()
    flash(f"Combined {len(members)} spaces into {combo.name}", "success")
    return redirect(back)


@spaces_bp.post("/combination/<int:space_id>/dissolve")
@require_space_admin
def dissolve_combination(space_id: int):
    """Delete a combination: its membership, override, and assignment rows.

    The combination is the thing that was assigned, so its assignment cannot
    outlive it. The member spaces are untouched and return to unassigned.
    _load_space_for_cycle already 404s a combination from another event.
    """
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None:
        abort(400, "Pick an event cycle first")

    combo = _load_space_for_cycle(space_id, cycle)
    if combo.kind != SPACE_KIND_COMBO:
        abort(404)

    name = combo.name
    db.session.query(SpaceAssignment).filter_by(
        space_id=combo.id, event_cycle_id=cycle.id).delete()
    db.session.query(SpaceCombinationMember).filter_by(
        combination_space_id=combo.id).delete()
    # Belt and braces: the model's cascade="all, delete-orphan" covers this
    # on the ORM path, but deleting it here keeps the intent readable.
    db.session.query(SpaceEventOverride).filter_by(space_id=combo.id).delete()
    log_config_change("space", combo.id, CONFIG_AUDIT_ARCHIVE, {
        "event_cycle": cycle.code, "dissolved": True,
    })
    # A combination is the only space this app hard-deletes. Everything
    # pointing at one, its overrides, assignments, and membership rows,
    # has to go with it or the FK either orphans a row or raises
    # IntegrityError.
    db.session.delete(combo)
    db.session.commit()

    flash(f"Dissolved {name}", "success")
    return redirect(url_for("spaces.list_spaces", event=cycle.code))


@spaces_bp.post("/copy-layout")
@require_space_admin
def copy_layout():
    """Copy another event's layout into this one.

    Copies combinations, pop-ups and per-event names. Never assignments:
    a copied assignment makes the page look finished and hides the rooms
    whose owner should have changed this year.
    """
    from app.routes.admin.helpers import log_config_change

    target = resolve_event_cycle(request.form.get("event"))
    source = resolve_event_cycle(request.form.get("source_event"))
    if target is None or target.venue_id is None:
        abort(400, "Pick an event cycle with a venue first")

    back = url_for("spaces.list_spaces", event=target.code)
    if source is None or source.id == target.id:
        flash("Pick a different event to copy from", "error")
        return redirect(back)
    if source.venue_id != target.venue_id:
        flash("That event is at a different venue", "error")
        return redirect(back)

    actor = h.get_active_user_id()
    source_spaces = db.session.query(Space).filter(
        Space.event_cycle_id == source.id,
        Space.is_active.is_(True),
    ).all()

    created = 0
    skipped: list[str] = []
    # Only event-scoped spaces (combos, pop-ups) match this query. A
    # permanent ROOM or SLICE has event_cycle_id NULL, so it already
    # exists for every event and is never copied. old_to_new maps each
    # copied space's source id to its new id, for remapping membership
    # and aliases below.
    old_to_new: dict[int, int] = {}
    for old in source_spaces:
        # Scoped the same way as the inline checks: a code already used by
        # a permanent row or by this target event is a real collision.
        if _code_taken(target.venue_id, old.code, target.id):
            skipped.append(old.code)
            continue
        new = Space(
            venue_id=old.venue_id, parent_id=old.parent_id,
            event_cycle_id=target.id, name=old.name, code=old.code,
            kind=old.kind, dimensions=old.dimensions,
            area_sqft=old.area_sqft, location_note=old.location_note,
            sort_order=old.sort_order, is_active=True,
            created_by_user_id=actor, updated_by_user_id=actor,
        )
        db.session.add(new)
        db.session.flush()
        old_to_new[old.id] = new.id
        created += 1

    for old in source_spaces:
        if old.kind != SPACE_KIND_COMBO or old.id not in old_to_new:
            continue
        new_combo_id = old_to_new[old.id]
        for link in old.combination_members:
            # A member archived since last event is skipped by name, not
            # silently, so the admin knows the copy is incomplete.
            if not link.member.is_active:
                skipped.append(link.member.code)
                continue
            if link.member.event_cycle_id is None:
                # A permanent ROOM or SLICE keeps its id across events.
                member_id = link.member_space_id
            else:
                # An event-scoped member, typically a pop-up, was copied
                # under a new id above; point at that copy, not the
                # source event's row.
                member_id = old_to_new.get(link.member_space_id)
                if member_id is None:
                    skipped.append(link.member.code)
                    continue
            db.session.add(SpaceCombinationMember(
                combination_space_id=new_combo_id,
                member_space_id=member_id,
                created_by_user_id=actor,
            ))

    aliases = db.session.query(SpaceEventOverride).filter(
        SpaceEventOverride.event_cycle_id == source.id,
        SpaceEventOverride.alias.isnot(None),
    ).all()
    aliases_copied = 0
    for old in aliases:
        # A permanent space keeps its id. An alias on a combo or pop-up
        # must follow that space's copy; the source event's row is not
        # on this event's page.
        if old.space.event_cycle_id is None:
            space_id = old.space_id
        else:
            space_id = old_to_new.get(old.space_id)
            if space_id is None:
                skipped.append(old.space.code)
                continue

        exists = db.session.query(SpaceEventOverride).filter_by(
            space_id=space_id, event_cycle_id=target.id).first()
        if exists:
            # Named the same way a skipped space is, so the summary does
            # not go quiet about the one category it left out.
            skipped.append(old.space.code)
            continue
        # Availability resets. A room out of service last year is not
        # assumed to be out of service this year.
        db.session.add(SpaceEventOverride(
            space_id=space_id, event_cycle_id=target.id,
            alias=old.alias, is_available=True, unavailable_reason=None,
            created_by_user_id=actor, updated_by_user_id=actor,
        ))
        aliases_copied += 1

    # target is an EventCycle, not a space; entity_type says so rather
    # than logging an EventCycle id under "space".
    log_config_change("event_cycle", target.id, CONFIG_AUDIT_CREATE, {
        "copied_from": source.code, "spaces_created": created,
        "aliases_copied": aliases_copied, "skipped": skipped,
    })
    db.session.commit()

    message = f"Copied {created} spaces and {aliases_copied} names from {source.name}"
    if skipped:
        message += f". Skipped: {', '.join(sorted(set(skipped)))}"
    flash(message, "success")
    return redirect(back)
