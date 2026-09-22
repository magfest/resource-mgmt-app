"""Spaces pages: the room catalog and per-event assignments."""
from __future__ import annotations

from flask import abort, flash, redirect, request, url_for
from sqlalchemy import or_

from app import db
from app.models import (
    CONFIG_AUDIT_ARCHIVE, CONFIG_AUDIT_CREATE, CONFIG_AUDIT_RESTORE,
    CONFIG_AUDIT_UPDATE, Department, EventCycle, Space, SpaceAssignment,
    SpaceEventOverride, SPACE_KIND_FREEFORM, SPACE_KIND_ROOM,
    SPACE_KIND_SLICE,
)
from app.routes import h
from . import spaces_bp
from .catalog import MAX_SPACE_ID, _name_taken, _read_space_form
from .catalog_parse import MAX_CODE_LENGTH, _normalize_name
from .helpers import (
    build_space_rows, build_space_stats, flash_if_too_long,
    parse_optional_int, render_space_admin_page, require_space_admin,
    resolve_event_cycle,
)


def _list_cycles():
    return (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active.is_(True))
        .order_by(EventCycle.sort_order, EventCycle.code)
        .all()
    )


def _render_spaces_list(cycle, cycles, *, show_archived=False, edit_id=None,
                        combine_room_id=None, combine_errors=None,
                        combine_selected=None):
    """Render the Spaces page for one event.

    Shared by the GET route and a rejected combine-grid submission. A
    rejected submission re-renders here instead of redirecting, so the
    room's grid keeps what the operator chose and shows what was wrong.
    """
    rows = build_space_rows(cycle, include_archived=show_archived)
    departments = (
        db.session.query(Department)
        .filter(Department.is_active.is_(True))
        .order_by(Department.name)
        .all()
    )

    # Only the room's own slices, in catalog order. A FREEFORM can carry a
    # parent_id too (the add-space form does not forbid it), so kind is
    # checked here rather than trusting parent_id alone.
    combine_slices = [
        r for r in rows
        if r["parent_id"] == combine_room_id and r["space"].kind == SPACE_KIND_SLICE
    ] if combine_room_id else []

    if combine_selected is None:
        # First open of the grid: preselect each slice's saved target.
        # A rejected resubmission passes its own map instead, so the
        # operator's last choice survives the re-render.
        combine_selected = {
            r["space"].id: r["combined_into_space_id"] for r in combine_slices
        }

    return render_space_admin_page(
        "spaces/list.html",
        cycles=cycles,
        cycle=cycle,
        rows=rows,
        rows_by_id={r["space"].id: r for r in rows},
        stats=build_space_stats(rows),
        edit_id=edit_id,
        departments=departments,
        show_archived=show_archived,
        SPACE_KIND_ROOM=SPACE_KIND_ROOM,
        SPACE_KIND_SLICE=SPACE_KIND_SLICE,
        combine_room_id=combine_room_id,
        combine_slices=combine_slices,
        combine_errors=combine_errors or {},
        combine_selected=combine_selected,
    )


@spaces_bp.get("/")
@require_space_admin
def list_spaces():
    """The Spaces page for one event."""
    cycle = resolve_event_cycle(request.args.get("event"))
    return _render_spaces_list(
        cycle, _list_cycles(),
        show_archived=request.args.get("show_archived") == "1",
        edit_id=request.args.get("edit", type=int),
        combine_room_id=request.args.get("combine", type=int),
    )


def _load_space_for_cycle(space_id: int, cycle, *, require_active: bool | None = None):
    """Load a space and confirm it belongs to the cycle's venue and event.

    404s when the space is missing, or when require_active is set and does
    not match. 404s too on a venue mismatch, or when the space's event does
    not match the cycle. event_cycle_id NULL means permanent; it passes the
    event check for any cycle at its venue. A pop-up from another event
    fails the check and is treated as missing. A cycle that
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


def _popup_name_taken(venue_id, event_cycle_id, name, exclude_space_id=None):
    """True when a pop-up's name would collide with a permanent space at
    this venue or another pop-up already on this event's page.

    Not symmetric with catalog.py's _name_taken, on purpose: a permanent
    space is never blocked by a pop-up's name there, because a pop-up
    built for one event must not reserve a name forever. This is the
    other direction. It is blocked, because two rows reading "Green Room"
    on one event page is what confuses an operator.
    """
    if _name_taken(venue_id, name, exclude_space_id=exclude_space_id):
        return True
    query = db.session.query(Space).filter(
        Space.venue_id == venue_id,
        Space.event_cycle_id == event_cycle_id,
    )
    if exclude_space_id is not None:
        query = query.filter(Space.id != exclude_space_id)
    target = _normalize_name(name)
    return any(_normalize_name(s.name) == target for s in query.all())


@spaces_bp.post("/space/new")
@require_space_admin
def create_space():
    """Create a pop-up space for the active event.

    This page has no Add space for a permanent room or slice; those come
    from the venue catalog (catalog.py), which owns their name and code
    rules. Every space this route writes is a FREEFORM row scoped to the
    event, using the shared field reader and name rule those own so this
    page cannot drift from them.
    """
    # Imported here, not at module level: app/routes/spaces/ is not an admin
    # package, and a module-level admin import from a non-admin route
    # breaks the `h` helper proxy.
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None or cycle.venue_id is None:
        abort(400, "Pick an event cycle with a venue first")

    back = url_for("spaces.list_spaces", event=cycle.code)

    fields = _read_space_form(request.form)
    if fields is None:
        return redirect(back)

    code = (request.form.get("code") or "").strip().upper()
    if not code:
        flash("Code is required", "error")
        return redirect(back)
    # Space.code is String(32). SQLite does not enforce that, so the check
    # is here rather than left to the column.
    if flash_if_too_long(code, "code", limit=MAX_CODE_LENGTH):
        return redirect(back)

    if _popup_name_taken(cycle.venue_id, cycle.id, fields["name"]):
        flash(f"A space named '{fields['name']}' already exists at this "
              "event", "error")
        return redirect(back)

    # A pop-up belongs to the event that created it, so it stops showing up
    # at this venue in later cycles. _code_taken already checks both a
    # permanent row and this event's own rows.
    if _code_taken(cycle.venue_id, code, cycle.id):
        flash(f"A space with code '{code}' already exists at this venue", "error")
        return redirect(back)

    actor = h.get_active_user_id()
    space = Space(
        venue_id=cycle.venue_id,
        parent_id=None,
        event_cycle_id=cycle.id,
        name=fields["name"],
        code=code,
        kind=SPACE_KIND_FREEFORM,
        dimensions=fields["dimensions"],
        area_sqft=fields["area_sqft"],
        location_note=(request.form.get("location_note") or "").strip() or None,
        is_active=True,
        created_by_user_id=actor,
        updated_by_user_id=actor,
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

    # 1. Space core fields: name, code, dimensions, area. Read and written
    # only for a pop-up (event_cycle_id set). A permanent space's catalog
    # row owns these; the catalog has its own name-uniqueness and bounds
    # rules that this route does not enforce, so a permanent space ignores
    # all four here, including on a crafted POST that names them.
    if space.event_cycle_id is not None:
        code = (request.form.get("code") or "").strip().upper()
        if code and code != space.code:
            if _code_taken(space.venue_id, code, space.event_cycle_id,
                           exclude_space_id=space.id):
                flash(f"A space with code '{code}' already exists at this venue",
                      "error")
                return redirect(back)
            space.code = code

        # Entry is 145 rows by hand, so a typo has to be fixable in place.
        # A blank name would render a row nobody can identify, so it is
        # refused outright rather than silently kept, which would flash
        # success over the old name.
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("A space needs a name", "error")
            return redirect(back)
        if flash_if_too_long(name, "name"):
            return redirect(back)
        space.name = name

        # Unlike name, dimensions and area clear to None when submitted
        # blank. That is intentional; only the name cannot go empty.
        space.dimensions = (request.form.get("dimensions") or "").strip() or None
        area_sqft, area_ok = parse_optional_int(request.form.get("area_sqft"))
        if not area_ok:
            flash("Area (sq ft) must be a whole number", "error")
            return redirect(back)
        space.area_sqft = area_sqft

    space.updated_by_user_id = actor

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


def _load_room_for_combine(room_id: int, cycle):
    """Return the room, or None when it is not a legal combining target.

    A parent must be at the venue, kind ROOM, parent_id NULL, active, and
    permanent, per the global constraint every parent check in this
    package repeats. room_id past MAX_SPACE_ID is refused ahead of the
    lookup; SQLite tolerates it, Postgres's int4 column does not.
    """
    if room_id > MAX_SPACE_ID:
        return None
    room = db.session.get(Space, room_id)
    if (room is None or room.kind != SPACE_KIND_ROOM
            or room.parent_id is not None
            or room.venue_id != cycle.venue_id
            or not room.is_active
            or room.event_cycle_id is not None):
        return None
    return room


@spaces_bp.post("/room/<int:room_id>/combine")
@require_space_admin
def combine_room(room_id: int):
    """Save one room's slice groupings for one event.

    Every slice of the room is submitted in one grid, since a slice's only
    legal target is another slice in the same room. The whole submission is
    validated before any override is written or deleted; one bad row
    refuses all of them, and the grid re-renders with what was chosen and
    each offending row marked.
    """
    from app.routes.admin.helpers import log_config_change

    cycle = resolve_event_cycle(request.form.get("event"))
    if cycle is None:
        abort(400, "Pick an event cycle first")

    room = _load_room_for_combine(room_id, cycle)
    if room is None:
        abort(404)

    slices = (
        db.session.query(Space)
        .filter(Space.parent_id == room.id, Space.kind == SPACE_KIND_SLICE,
                Space.is_active.is_(True))
        .order_by(Space.sort_order, Space.code)
        .all()
    )
    slice_ids = {s.id for s in slices}
    by_id = {s.id: s for s in slices}

    # Parse every choice before validating any of them. A value past
    # MAX_SPACE_ID or not a whole number can never name a slice in this
    # room, so it is folded into the same "not here" refusal the dropdown
    # itself cannot produce; it never reaches db.session.get.
    selections: dict[int, int | None] = {}
    for slice_ in slices:
        raw = (request.form.get(f"combined_into_{slice_.id}") or "").strip()
        if not raw:
            selections[slice_.id] = None
            continue
        try:
            target_id = int(raw)
        except ValueError:
            target_id = -1
        if not (0 < target_id <= MAX_SPACE_ID):
            target_id = -1
        selections[slice_.id] = target_id

    # Refuse per the global constraints: a target outside this room or not
    # a slice, a target already combined into something (a chain), and a
    # slice others already point at being combined into a third.
    targets_used = {t for t in selections.values() if t and t in slice_ids}
    errors: dict[int, str] = {}
    for slice_id, target_id in selections.items():
        if not target_id:
            continue
        if target_id not in slice_ids:
            errors[slice_id] = "Pick a slice in this room, or leave it not combined"
            continue
        if selections.get(target_id):
            errors[slice_id] = (f"{by_id[target_id].name} is already combined "
                                "into another slice")
    for slice_id in slice_ids:
        if slice_id in targets_used and selections.get(slice_id):
            errors.setdefault(slice_id, (
                "Other slices are already combined into this one; it cannot "
                "also combine into another slice"))

    if errors:
        return _render_spaces_list(
            cycle, _list_cycles(), combine_room_id=room.id,
            combine_errors=errors, combine_selected=selections,
        )

    actor = h.get_active_user_id()
    existing = {
        o.space_id: o
        for o in db.session.query(SpaceEventOverride)
        .filter(SpaceEventOverride.event_cycle_id == cycle.id,
                SpaceEventOverride.space_id.in_(slice_ids))
        .all()
    } if slice_ids else {}

    groupings = {}
    for slice_ in slices:
        target_id = selections[slice_.id]
        override = existing.get(slice_.id)
        groupings[slice_.code] = by_id[target_id].code if target_id else None

        if target_id:
            if override is None:
                override = SpaceEventOverride(
                    space_id=slice_.id, event_cycle_id=cycle.id,
                    created_by_user_id=actor,
                )
                db.session.add(override)
                existing[slice_.id] = override
            override.combined_into_space_id = target_id
            override.updated_by_user_id = actor
        elif override is not None:
            override.combined_into_space_id = None
            override.updated_by_user_id = actor
            # Sparse table: a row that says nothing else is dropped rather
            # than kept as a no-op, matching save_space's own alias/
            # availability rule.
            if (not override.alias and override.is_available
                    and not override.unavailable_reason):
                db.session.delete(override)

    log_config_change("space", room.id, CONFIG_AUDIT_UPDATE, {
        "event_cycle": cycle.code, "combined": groupings,
    })
    db.session.commit()
    flash(f"Saved groupings for {room.name}", "success")
    return redirect(url_for("spaces.list_spaces", event=cycle.code))


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


@spaces_bp.post("/copy-layout")
@require_space_admin
def copy_layout():
    """Copy another event's layout into this one.

    Copies pop-ups, per-event names, and combining. Never assignments: a
    copied assignment makes the page look finished and hides the rooms
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
    # Only event-scoped spaces (pop-ups) match this query. A permanent ROOM
    # or SLICE has event_cycle_id NULL, so it already exists for every
    # event and is never copied. old_to_new maps each copied space's
    # source id to its new id, for remapping aliases below.
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

    # A row worth copying carries an alias or a fold; an availability-only
    # row is left out on purpose, below.
    carrying = db.session.query(SpaceEventOverride).filter(
        SpaceEventOverride.event_cycle_id == source.id,
    ).filter(or_(
        SpaceEventOverride.alias.isnot(None),
        SpaceEventOverride.combined_into_space_id.isnot(None),
    )).all()
    overrides_copied = 0
    for old in carrying:
        # A permanent space keeps its id. An override on a pop-up must
        # follow that space's copy; the source event's row is not on this
        # event's page.
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

        combined_into_id = None
        if old.combined_into_space_id is not None:
            # A folded slice is always permanent (constraint 2), so this
            # branch never fires in practice; it exists so a future kind
            # that could be combined does not silently copy the wrong id.
            fold_target = old.combined_into
            if fold_target is not None and fold_target.event_cycle_id is None:
                combined_into_id = old.combined_into_space_id
            elif fold_target is not None:
                combined_into_id = old_to_new.get(old.combined_into_space_id)
                if combined_into_id is None:
                    skipped.append(old.space.code)
                    continue

        # Availability resets. A room out of service last year is not
        # assumed to be out of service this year.
        db.session.add(SpaceEventOverride(
            space_id=space_id, event_cycle_id=target.id,
            alias=old.alias, is_available=True, unavailable_reason=None,
            combined_into_space_id=combined_into_id,
            created_by_user_id=actor, updated_by_user_id=actor,
        ))
        overrides_copied += 1

    # target is an EventCycle, not a space; entity_type says so rather
    # than logging an EventCycle id under "space".
    log_config_change("event_cycle", target.id, CONFIG_AUDIT_CREATE, {
        "copied_from": source.code, "spaces_created": created,
        "overrides_copied": overrides_copied, "skipped": skipped,
    })
    db.session.commit()

    message = (f"Copied {created} spaces and {overrides_copied} names or "
              f"groupings from {source.name}")
    if skipped:
        message += f". Skipped: {', '.join(sorted(set(skipped)))}"
    flash(message, "success")
    return redirect(back)
