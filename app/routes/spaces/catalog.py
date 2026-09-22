"""
The venue catalog: wave one, where a venue's rooms and slices are entered.

This page knows nothing about events. No assignments, no aliases, no
combining, and no pop-ups; a pop-up belongs to one event and is created on
the event page instead.
"""
from __future__ import annotations

from flask import abort, flash, redirect, request, url_for

from app import db
from app.models import (
    CONFIG_AUDIT_ARCHIVE, CONFIG_AUDIT_CREATE, CONFIG_AUDIT_RESTORE,
    CONFIG_AUDIT_UPDATE, Space, SPACE_KIND_ROOM, SPACE_KIND_SLICE, Venue,
)
from app.routes import h
from . import spaces_bp
from .catalog_parse import (
    COLUMNS, MAX_AREA_SQFT, MAX_CODE_LENGTH, MAX_NAME_LENGTH, ParsedRow,
    _normalize_name, generate_code, implied_room_code, is_slice_coded,
    parse_rows, validate_parent, validate_row,
)
from .helpers import (
    flash_if_too_long, parse_optional_int, render_space_admin_page,
    require_space_admin,
)


# Space.id and Venue.id are a plain Integer, which Postgres stores as
# int4. On SQLite, db.session.get already 404s cleanly past int4; it
# raises OverflowError only near 2**63. int4 is still the right ceiling,
# since Postgres enforces it and would reject a value SQLite tolerates.
MAX_SPACE_ID = 2147483647


# A real venue sheet runs to a few hundred rows at most.
# Confirm's cost past that point is per-field form lookups. Preview pays
# more: parsing the paste and rendering that many rows of five inputs
# each, before an operator can act on any of it. Both routes enforce the
# same limit.
MAX_PASTE_ROWS = 1000


def build_catalog_rows(venue, include_archived: bool = False) -> list[dict]:
    """Rows for the catalog table, rooms followed by their slices.

    Permanent rows only. An event-scoped space belongs to one event and is
    managed on the event page.
    """
    query = (
        db.session.query(Space)
        .filter(Space.venue_id == venue.id)
        .filter(Space.event_cycle_id.is_(None))
    )
    if not include_archived:
        query = query.filter(Space.is_active.is_(True))
    spaces = query.order_by(Space.sort_order, Space.code).all()

    by_id = {s.id: s for s in spaces}
    children: dict[int, list] = {}
    for space in spaces:
        if space.parent_id in by_id:
            children.setdefault(space.parent_id, []).append(space)

    def to_row(space, depth: int) -> dict:
        kids = children.get(space.id, [])
        return {
            "space": space,
            "depth": depth,
            "child_codes": [c.code for c in kids],
            "has_children": bool(kids),
        }

    ordered: list[dict] = []
    seen: set[int] = set()
    for space in spaces:
        if space.parent_id in by_id:
            continue
        ordered.append(to_row(space, 0))
        seen.add(space.id)
        for child in children.get(space.id, []):
            ordered.append(to_row(child, 1))
            seen.add(child.id)

    # A slice whose room was archived still needs a row. A silently missing
    # room reads as data loss.
    for space in spaces:
        if space.id not in seen:
            ordered.append(to_row(space, 0))
            seen.add(space.id)
    return ordered


def _get_venue_or_404(venue_id: int):
    # <int:venue_id> has no upper bound. Past MAX_SPACE_ID, refuse before
    # db.session.get rather than let a rare huge value reach it.
    if venue_id > MAX_SPACE_ID:
        abort(404)
    venue = db.session.get(Venue, venue_id)
    if venue is None:
        abort(404)
    return venue


def _venue_catalog_url(venue, show_archived: bool) -> str:
    """Build the catalog URL for one venue, omitting show_archived when
    false so the common path keeps a clean URL."""
    if show_archived:
        return url_for("spaces.venue_catalog", venue_id=venue.id,
                       show_archived=1)
    return url_for("spaces.venue_catalog", venue_id=venue.id)


def _venue_repair_slices_url(venue, show_archived: bool) -> str:
    """Build the repair-slices URL for one venue, matching
    _venue_catalog_url's show_archived rule."""
    if show_archived:
        return url_for("spaces.catalog_repair_slices", venue_id=venue.id,
                       show_archived=1)
    return url_for("spaces.catalog_repair_slices", venue_id=venue.id)


def _find_flagged_spaces(venue) -> list[Space]:
    """Active, permanent rooms at this venue whose code reads as a slice.

    Only the code and a missing parent decide this. A standalone room
    with an unmarked code never appears here. Children are not checked;
    a hand-typed code like CHE-S for "Chesapeake South" is still
    possible, and the warning must surface it too. See is_slice_coded
    for why this is advisory, not a rule the database enforces.
    """
    candidates = (
        db.session.query(Space)
        .filter(Space.venue_id == venue.id, Space.event_cycle_id.is_(None),
                Space.parent_id.is_(None), Space.is_active.is_(True))
        .order_by(Space.sort_order, Space.code)
        .all()
    )
    return [s for s in candidates if is_slice_coded(s.code)]


def _suggest_parent(venue, space):
    """The active permanent room this space's own code implies, or None.

    None means the code gave no hint, or the implied code matches no
    room at this venue. The repair screen leaves the picker unselected
    rather than guessing.
    """
    implied = implied_room_code(space.code)
    if implied is None:
        return None
    return (
        db.session.query(Space)
        .filter(Space.venue_id == venue.id, Space.code == implied,
                Space.kind == SPACE_KIND_ROOM,
                Space.parent_id.is_(None), Space.is_active.is_(True),
                Space.event_cycle_id.is_(None))
        .first()
    )


def _render_catalog(venue, *, show_archived: bool = False, edit_id=None,
                    form_values=None, edit_values=None, preview=None):
    """Render the catalog page for one venue.

    Shared by the GET route, the write routes' failure paths, and the paste
    preview. A rejected add or save re-renders here with its panel open and
    what was typed kept, rather than redirecting and losing both. Do not
    turn a failed add or save back into a redirect; a refresh reposts the
    rejected data and fails again, which is intentional.
    """
    rows = build_catalog_rows(venue, include_archived=show_archived)
    # The parent picker never offers an archived room, whatever the toggle
    # shows. catalog_add_space enforces the same rule server-side: a slice
    # parented to a retired room is a dead branch either way.
    #
    # A parent must also sit at the top of the tree, not merely be kind
    # ROOM. The event page (views.py) can write a permanent ROOM row that
    # already has a parent, and kind alone would let that row become a
    # parent here too, producing a three-level tree build_catalog_rows
    # cannot render. Every parent check in this module repeats both
    # conditions; see catalog_add_space's parent check for the others.
    active_rooms = [r for r in build_catalog_rows(venue)
                    if r["space"].kind == SPACE_KIND_ROOM
                    and r["space"].parent_id is None]

    return render_space_admin_page(
        "spaces/catalog.html",
        venue=venue,
        rows=rows,
        show_archived=show_archived,
        edit_id=edit_id,
        rooms=active_rooms,
        form_values=form_values or {},
        edit_values=edit_values or {},
        paste_columns=COLUMNS,
        preview=preview,
        # How many boxes are ticked right now, the number pressing
        # Create would attempt. A fresh parse_rows preview starts every
        # error-free row ticked and every problem row unticked, so this
        # equals the error-free count there. A rejected confirm keeps
        # whatever was actually submitted, where a still-ticked row can
        # carry an error, and the count must include it or the button
        # promises a number submitting cannot deliver.
        create_count=sum(1 for r in preview if r.checked) if preview else 0,
        problem_count=sum(1 for r in preview if r.error is not None) if preview else 0,
        flagged_count=len(_find_flagged_spaces(venue)),
        repair_url=_venue_repair_slices_url(venue, show_archived),
    )


@spaces_bp.get("/venues/<int:venue_id>/")
@require_space_admin
def venue_catalog(venue_id: int):
    """The catalog for one venue."""
    venue = _get_venue_or_404(venue_id)
    return _render_catalog(
        venue,
        show_archived=request.args.get("show_archived") == "1",
        edit_id=request.args.get("edit", type=int),
    )


def _read_space_form(form):
    """Read and validate the shared fields of the add and edit forms.

    Returns a dict on success, or None after flashing.
    """
    name = (form.get("name") or "").strip()
    if not name:
        flash("A space needs a name", "error")
        return None
    if flash_if_too_long(name, "name", limit=MAX_NAME_LENGTH):
        return None

    area, ok = parse_optional_int(form.get("area_sqft"))
    if not ok:
        flash("Area must be a whole number of square feet", "error")
        return None
    if area is not None and area > MAX_AREA_SQFT:
        flash(f"Area must be {MAX_AREA_SQFT:,} square feet or fewer", "error")
        return None
    if area is not None and area < 0:
        flash("Area must be zero or greater", "error")
        return None

    return {
        "name": name,
        "dimensions": (form.get("dimensions") or "").strip() or None,
        "area_sqft": area,
    }


def _name_taken(venue_id, name, exclude_space_id=None):
    """Return True when a permanent space at this venue holds this name.

    This does NOT mirror _code_taken in views.py, which lets an event row
    block a permanent one. A pop-up belongs to a single event, and the
    catalog cannot show it under any toggle, so one would otherwise
    reserve a name forever against a page that cannot explain why. The
    cost is that a live event can show a pop-up and a room sharing a name.

    Archived rows do block, because restoring one would produce the
    duplicate.

    Checked in Python, not SQL. A unique index cannot express a
    case-insensitive, whitespace-normalized comparison, and an existing
    database may already hold duplicates that a new constraint would
    reject on upgrade.
    """
    query = db.session.query(Space).filter(
        Space.venue_id == venue_id,
        Space.event_cycle_id.is_(None),
    )
    if exclude_space_id is not None:
        query = query.filter(Space.id != exclude_space_id)
    target = _normalize_name(name)
    return any(_normalize_name(s.name) == target for s in query.all())


@spaces_bp.post("/venues/<int:venue_id>/space/new")
@require_space_admin
def catalog_add_space(venue_id: int):
    """Add one room or slice by hand.

    Kind is derived from the parent picker: no parent makes a room, a
    chosen parent makes a slice. This form offers no FREEFORM option, so
    an empty parent is unambiguous. The event page's add form keeps an
    explicit Kind select for that reason.
    """
    from app.routes.admin.helpers import log_config_change

    venue = _get_venue_or_404(venue_id)
    show_archived = request.args.get("show_archived") == "1"
    form_values = {
        "name": request.form.get("name", ""),
        "code": request.form.get("code", ""),
        "parent_id": request.form.get("parent_id", ""),
        "dimensions": request.form.get("dimensions", ""),
        "area_sqft": request.form.get("area_sqft", ""),
    }

    def rejected():
        return _render_catalog(venue, show_archived=show_archived,
                               form_values=form_values)

    fields = _read_space_form(request.form)
    if fields is None:
        return rejected()

    code = (request.form.get("code") or "").strip().upper()

    parent_id_raw = (request.form.get("parent_id") or "").strip()
    parent_id = None
    if parent_id_raw:
        try:
            parent_id = int(parent_id_raw)
        except ValueError:
            parent_id = None
        # int() has no upper bound, so an oversized value survives the
        # parse and raises inside db.session.get instead of being refused.
        if parent_id is None or not (0 < parent_id <= MAX_SPACE_ID):
            flash("Pick a parent room at this venue", "error")
            return rejected()

    parent = None
    if parent_id is not None:
        parent = db.session.get(Space, parent_id)
        # A parent must be active and permanent, not just correctly kinded:
        # the picker already excludes an archived or event-scoped room, but
        # a direct POST bypasses the picker, so the check is repeated here.
        # kind == SPACE_KIND_ROOM alone is not the depth test: the event
        # page can write a permanent ROOM row that already has a parent,
        # so a parent must also sit at the top of the tree.
        if (parent is None or parent.venue_id != venue.id
                or parent.kind != SPACE_KIND_ROOM
                or parent.parent_id is not None
                or not parent.is_active
                or parent.event_cycle_id is not None):
            flash("Pick a parent room at this venue", "error")
            return rejected()
    kind = SPACE_KIND_SLICE if parent is not None else SPACE_KIND_ROOM

    if not code:
        # kind is already resolved above, from the parent picker, so the
        # generated code can carry it: -P- for a room, -S- for a slice.
        code = generate_code(fields["name"], kind)
        # A name with no ASCII letter or digit in its first token, a CJK
        # name for example, generates nothing. Reject rather than write an
        # empty code; the operator types one instead.
        if not code:
            flash("No code could be generated from this name; type one",
                  "error")
            return rejected()
    # Space.code is String(32). SQLite does not enforce that, so the check
    # is here rather than left to the column.
    if flash_if_too_long(code, "code", limit=MAX_CODE_LENGTH):
        return rejected()

    if _name_taken(venue.id, fields["name"]):
        flash(f"A space named '{fields['name']}' already exists at this "
              "venue", "error")
        return rejected()

    if db.session.query(Space).filter_by(venue_id=venue.id, code=code).first():
        flash(f"A space with code '{code}' already exists at this venue",
              "error")
        return rejected()

    actor = h.get_active_user_id()
    space = Space(
        venue_id=venue.id, parent_id=parent_id, event_cycle_id=None,
        name=fields["name"], code=code, kind=kind,
        dimensions=fields["dimensions"], area_sqft=fields["area_sqft"],
        is_active=True, created_by_user_id=actor, updated_by_user_id=actor,
    )
    db.session.add(space)
    db.session.flush()
    log_config_change("space", space.id, CONFIG_AUDIT_CREATE)
    db.session.commit()

    flash(f"Added {space.name}", "success")
    return redirect(_venue_catalog_url(venue, show_archived))


@spaces_bp.post("/venues/<int:venue_id>/paste/preview")
@require_space_admin
def catalog_paste_preview(venue_id: int):
    """Parse a pasted table and show what it would create.

    Writes nothing. Every field in the preview is editable, which is where
    a generated code gets fixed before it reaches the database.
    """
    venue = _get_venue_or_404(venue_id)
    show_archived = request.args.get("show_archived") == "1"

    pasted = request.form.get("pasted") or ""
    # Checked on the raw text, ahead of parse_rows, so an oversized paste
    # is refused before it is parsed at all. confirm's row_count bound
    # guards its own per-field form lookups; this one guards the larger
    # cost, parsing and rendering every row.
    if len(pasted.splitlines()) > MAX_PASTE_ROWS:
        flash(f"That's more rows than one paste holds; the limit is "
              f"{MAX_PASTE_ROWS}.", "error")
        return redirect(_venue_catalog_url(venue, show_archived))

    existing_codes = {
        c for (c,) in db.session.query(Space.code)
        .filter(Space.venue_id == venue.id).all()
    }
    # Permanent rows only. A pop-up built for one event must not reserve a
    # name forever, so it cannot block a paste the way a code still can;
    # see _name_taken for the same rule on the add/edit form.
    existing_names = {
        _normalize_name(n) for (n,) in db.session.query(Space.name)
        .filter(Space.venue_id == venue.id, Space.event_cycle_id.is_(None))
        .all()
    }
    # A narrower question than existing_codes: which of those codes may a
    # pasted child attach to. Mirrors the parent picker's own filter in
    # catalog_add_space: active, permanent, kind ROOM, and no parent of
    # its own, so a paste cannot parent a slice, an archived room, an
    # event pop-up, or a nested room the event page wrote.
    legal_parent_codes = {
        c for (c,) in db.session.query(Space.code).filter(
            Space.venue_id == venue.id, Space.kind == SPACE_KIND_ROOM,
            Space.is_active.is_(True), Space.event_cycle_id.is_(None),
            Space.parent_id.is_(None),
        ).all()
    }
    parsed = parse_rows(pasted, existing_codes,
                        existing_names, legal_parent_codes)

    if not parsed:
        flash("Nothing to preview", "error")
        return redirect(_venue_catalog_url(venue, show_archived))

    return _render_catalog(venue, show_archived=show_archived, preview=parsed)


@spaces_bp.post("/venues/<int:venue_id>/paste/confirm")
@require_space_admin
def catalog_paste_confirm(venue_id: int):
    """Create the rows confirmed in the preview.

    The preview is editable, so a confirmed row may not match what the
    parser last saw; validate_row and validate_parent enforce every rule
    again here. All or nothing: nothing is added until every wanted row
    has passed, so one bad row creates zero. A rejected batch re-renders
    the preview with every row's own problem, the same way
    catalog_paste_preview does, instead of redirecting and losing every
    edit made there.
    """
    from app.routes.admin.helpers import log_config_change

    venue = _get_venue_or_404(venue_id)
    show_archived = request.args.get("show_archived") == "1"
    actor = h.get_active_user_id()

    count = request.form.get("row_count", type=int) or 0
    if count > MAX_PASTE_ROWS:
        flash(f"That's more rows than one paste holds; the limit is "
              f"{MAX_PASTE_ROWS}.", "error")
        return redirect(_venue_catalog_url(venue, show_archived))

    existing_codes = {
        c for (c,) in db.session.query(Space.code)
        .filter(Space.venue_id == venue.id).all()
    }
    existing_names = {
        _normalize_name(n) for (n,) in db.session.query(Space.name)
        .filter(Space.venue_id == venue.id, Space.event_cycle_id.is_(None))
        .all()
    }
    # See catalog_paste_preview's own legal_parent_codes for why
    # parent_id must be None here too.
    legal_parent_codes = {
        c for (c,) in db.session.query(Space.code).filter(
            Space.venue_id == venue.id, Space.kind == SPACE_KIND_ROOM,
            Space.is_active.is_(True), Space.event_cycle_id.is_(None),
            Space.parent_id.is_(None),
        ).all()
    }

    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    seen_room_codes: set[str] = set()
    rows: list[ParsedRow] = []

    for i in range(count):
        wanted = request.form.get(f"create_{i}") == "1"
        name = (request.form.get(f"name_{i}") or "").strip()
        code = (request.form.get(f"code_{i}") or "").strip().upper()
        dimensions = (request.form.get(f"dimensions_{i}") or "").strip() or None
        parent_code = (request.form.get(f"parent_{i}") or "").strip().upper() or None
        area_raw = request.form.get(f"area_{i}")
        # A hidden field on the preview form, not re-derived: this route
        # has no way to tell a generated code from a typed one on its own,
        # and the marker must survive a rejected confirm's re-render.
        generated = request.form.get(f"generated_{i}") == "1"

        if not wanted:
            # An unticked row is not validated or parsed. Its own typo,
            # area included, must not block the rows the operator did
            # want created, and must not be silently cleared either.
            rows.append(ParsedRow(
                name=name, code=code, dimensions=dimensions,
                area_sqft=None, area_text=area_raw or "",
                parent_code=parent_code,
                generated_code=generated, error=None, checked=False,
            ))
            continue

        area, area_ok = parse_optional_int(area_raw)
        if not area_ok:
            # The raw text survives the rejection. Clearing the box here
            # would tell the operator their value was both wrong and gone.
            rows.append(ParsedRow(
                name=name, code=code, dimensions=dimensions,
                area_sqft=None, area_text=area_raw or "",
                parent_code=parent_code, generated_code=generated,
                error=f"Area '{area_raw}' is not a whole number", checked=True,
            ))
            continue

        error = validate_row(
            name, code, area, parent_code,
            existing_codes=existing_codes, existing_names=existing_names,
            seen_codes=seen_codes, seen_names=seen_names,
            seen_room_codes=seen_room_codes,
        )
        rows.append(ParsedRow(
            name=name, code=code, dimensions=dimensions,
            area_sqft=area, area_text=area_raw or "",
            parent_code=parent_code, generated_code=generated,
            error=error, checked=True,
        ))

    # Second pass, mirroring parse_rows: a parent may be pasted after its
    # children, so parent legality is only checkable once every row's own
    # code is known.
    all_codes = seen_codes | existing_codes
    known_parents = legal_parent_codes | seen_room_codes
    for row in rows:
        if row.checked and row.error is None and row.parent_code:
            row.error = validate_parent(row.code, row.parent_code,
                                        all_codes, known_parents)

    if not any(r.checked for r in rows):
        flash("No rows were selected", "error")
        return redirect(_venue_catalog_url(venue, show_archived))

    if any(r.checked and r.error for r in rows):
        return _render_catalog(venue, show_archived=show_archived, preview=rows)

    wanted_rows = [r for r in rows if r.checked]

    # First pass creates every row unparented, so a parent may be pasted
    # after its children. Second pass links them by code.
    created: dict[str, Space] = {}
    for row in wanted_rows:
        space = Space(
            venue_id=venue.id, event_cycle_id=None, name=row.name,
            code=row.code, kind=SPACE_KIND_ROOM,
            dimensions=row.dimensions, area_sqft=row.area_sqft,
            is_active=True, created_by_user_id=actor, updated_by_user_id=actor,
        )
        db.session.add(space)
        created[row.code] = space
    db.session.flush()

    for row in wanted_rows:
        if not row.parent_code:
            continue
        parent = created.get(row.parent_code) or db.session.query(Space).filter_by(
            venue_id=venue.id, code=row.parent_code).first()
        if parent is None:
            # Legality was already checked above; only a concurrent edit
            # between that check and this write reaches here. Refuse
            # rather than dereference a parent that turned out not to
            # exist.
            db.session.rollback()
            flash(f"Parent code {row.parent_code} no longer exists. "
                  "Nothing was created.", "error")
            return redirect(_venue_catalog_url(venue, show_archived))
        child = created[row.code]
        child.parent_id = parent.id
        # A row with a parent is a slice of it. Kind follows from the
        # paste rather than being asked for a second time.
        child.kind = SPACE_KIND_SLICE

    for space in created.values():
        log_config_change("space", space.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created {len(created)} spaces", "success")
    return redirect(_venue_catalog_url(venue, show_archived))


def _get_catalog_space(venue, space_id: int):
    # <int:space_id> has no upper bound; see _get_venue_or_404 for why
    # MAX_SPACE_ID is checked before the lookup rather than after.
    if space_id > MAX_SPACE_ID:
        abort(404)
    space = db.session.get(Space, space_id)
    if (space is None or space.venue_id != venue.id
            or space.event_cycle_id is not None):
        abort(404)
    return space


@spaces_bp.post("/venues/<int:venue_id>/space/<int:space_id>")
@require_space_admin
def catalog_save_space(venue_id: int, space_id: int):
    """Correct a space. Kind and parent stay fixed; changing either
    re-parents a space that assignments already point at.

    Every check runs before any field is assigned. A rejection re-renders
    from these same objects, so a field assigned before a later check
    fails would show a change that was never saved.
    """
    from app.routes.admin.helpers import log_config_change

    venue = _get_venue_or_404(venue_id)
    space = _get_catalog_space(venue, space_id)
    show_archived = request.args.get("show_archived") == "1"
    edit_values = {
        "name": request.form.get("name", ""),
        "code": request.form.get("code", ""),
        "dimensions": request.form.get("dimensions", ""),
        "area_sqft": request.form.get("area_sqft", ""),
    }

    def rejected():
        return _render_catalog(venue, show_archived=show_archived,
                               edit_id=space.id, edit_values=edit_values)

    fields = _read_space_form(request.form)
    if fields is None:
        return rejected()

    if _name_taken(venue.id, fields["name"], exclude_space_id=space.id):
        flash(f"A space named '{fields['name']}' already exists at this "
              "venue", "error")
        return rejected()

    code = (request.form.get("code") or "").strip().upper()
    if code and code != space.code:
        if flash_if_too_long(code, "code", limit=MAX_CODE_LENGTH):
            return rejected()
        clash = db.session.query(Space).filter(
            Space.venue_id == venue.id, Space.code == code,
            Space.id != space.id,
        ).first()
        if clash:
            flash(f"A space with code '{code}' already exists at this venue",
                  "error")
            return rejected()
        space.code = code

    space.name = fields["name"]
    space.dimensions = fields["dimensions"]
    space.area_sqft = fields["area_sqft"]
    space.updated_by_user_id = h.get_active_user_id()

    log_config_change("space", space.id, CONFIG_AUDIT_UPDATE, {
        "name": space.name, "code": space.code,
    })
    db.session.commit()
    flash(f"Saved {space.name}", "success")
    return redirect(_venue_catalog_url(venue, show_archived))


def _set_catalog_space_active(venue_id: int, space_id: int, active: bool,
                              action: str):
    from app.routes.admin.helpers import log_config_change

    venue = _get_venue_or_404(venue_id)
    space = _get_catalog_space(venue, space_id)
    show_archived = request.args.get("show_archived") == "1"

    space.is_active = active
    space.updated_by_user_id = h.get_active_user_id()
    log_config_change("space", space.id, action)
    db.session.commit()

    flash(f"{'Restored' if active else 'Archived'} {space.name}", "success")
    return redirect(_venue_catalog_url(venue, show_archived))


@spaces_bp.get("/venues/<int:venue_id>/repair-slices")
@require_space_admin
def catalog_repair_slices(venue_id: int):
    """Review rooms whose code reads as a slice but which carry no
    parent, one suggested fix each.

    The suggestion comes from the space's own code, via
    implied_room_code. A code with no hint, or whose implied room does
    not exist at this venue, leaves the picker unselected instead of
    guessing.
    """
    venue = _get_venue_or_404(venue_id)
    show_archived = request.args.get("show_archived") == "1"
    active_rooms = [r for r in build_catalog_rows(venue)
                    if r["space"].kind == SPACE_KIND_ROOM
                    and r["space"].parent_id is None]

    # Every child at the venue, matching catalog_repair_slices_apply's own
    # query: a flagged space with a child, permanent or event-scoped,
    # cannot become a slice without a three-level tree. The GET and POST
    # must agree, or a row the POST refuses can still preview as clean.
    parents_with_children = {
        pid for (pid,) in db.session.query(Space.parent_id).filter(
            Space.venue_id == venue.id, Space.parent_id.isnot(None),
        ).all()
    }

    rows = []
    for space in _find_flagged_spaces(venue):
        if space.id in parents_with_children:
            rows.append({
                "space": space,
                "parent_id": None,
                "checked": False,
                "error": (f"{space.name} already has spaces under it; it "
                         "cannot become a slice"),
            })
            continue
        suggestion = _suggest_parent(venue, space)
        rows.append({
            "space": space,
            "parent_id": suggestion.id if suggestion else None,
            "checked": suggestion is not None,
            "error": None,
        })

    return render_space_admin_page(
        "spaces/repair_slices.html",
        venue=venue, rows=rows, rooms=active_rooms,
        show_archived=show_archived,
    )


@spaces_bp.post("/venues/<int:venue_id>/repair-slices")
@require_space_admin
def catalog_repair_slices_apply(venue_id: int):
    """Apply the parent fixes confirmed on the repair screen.

    Re-derives the flagged set from the database rather than trusting the
    form, so a space fixed elsewhere between GET and POST is no longer
    offered. Validates the resolved parent object directly, the way
    catalog_add_space does, rather than by code: Space.code is unique
    only within a venue, so a code check alone would accept another
    venue's room. All or nothing: a rejected row fails the whole batch,
    and the screen re-renders with what was chosen and a problem on each
    offending row.
    """
    from app.routes.admin.helpers import log_config_change

    venue = _get_venue_or_404(venue_id)
    show_archived = request.args.get("show_archived") == "1"
    actor = h.get_active_user_id()
    active_rooms = [r for r in build_catalog_rows(venue)
                    if r["space"].kind == SPACE_KIND_ROOM
                    and r["space"].parent_id is None]

    # Every child at the venue, not only permanent ones: the event page
    # parents a pop-up to any room here, including an unrepaired orphan,
    # so an event-scoped child must block that orphan from becoming a
    # slice just as a permanent one does.
    parents_with_children = {
        pid for (pid,) in db.session.query(Space.parent_id).filter(
            Space.venue_id == venue.id, Space.parent_id.isnot(None),
        ).all()
    }

    flagged = _find_flagged_spaces(venue)
    # Computed up front so every row's parent check can see the whole
    # batch's intent, not just its own. A space ticked here is about to
    # stop being a legal parent, even though it is still kind ROOM in the
    # database until the write below.
    ticked_ids = {
        space.id for space in flagged
        if request.form.get(f"apply_{space.id}") == "1"
    }

    rows = []
    for space in flagged:
        wanted = space.id in ticked_ids
        parent_raw = (request.form.get(f"parent_{space.id}") or "").strip()
        parent_id, parent_id_ok = parse_optional_int(parent_raw)
        if not parent_id_ok:
            parent_id = None
        # SQLite tolerates an id well past int4 and 404s cleanly through
        # db.session.get; it only raises OverflowError near 2**63. int4
        # stays the ceiling because Postgres makes Space.id int4 and
        # would reject what SQLite lets through.
        if parent_id is not None and not (0 < parent_id <= MAX_SPACE_ID):
            parent_id = None

        if not wanted:
            rows.append({"space": space, "parent_id": parent_id,
                        "checked": False, "error": None})
            continue

        parent = db.session.get(Space, parent_id) if parent_id else None
        if parent is None:
            error = "Pick a parent room to repair this space"
        elif parent.id == space.id:
            error = "A space cannot be its own parent"
        elif parent.id in ticked_ids:
            error = (f"{parent.name} is itself being turned into a slice "
                     "in this batch")
        elif (parent.venue_id != venue.id
                or parent.kind != SPACE_KIND_ROOM
                or parent.parent_id is not None
                or not parent.is_active
                or parent.event_cycle_id is not None):
            # Repeats catalog_add_space's object check on the resolved
            # parent, parent_id IS NULL included. The picker only offers
            # a room at this venue, but a direct POST bypasses it.
            error = "Pick a parent room at this venue"
        else:
            error = None
        if error is None and space.id in parents_with_children:
            error = (f"{space.name} already has spaces under it; it "
                     "cannot become a slice")

        rows.append({
            "space": space, "parent_id": parent_id,
            "checked": True, "error": error,
        })

    if not any(r["checked"] for r in rows):
        flash("No rows were selected", "error")
        return redirect(_venue_catalog_url(venue, show_archived))

    if any(r["checked"] and r["error"] for r in rows):
        return render_space_admin_page(
            "spaces/repair_slices.html",
            venue=venue, rows=rows, rooms=active_rooms,
            show_archived=show_archived,
        )

    fixed = [r for r in rows if r["checked"]]

    # Defensive only: the batch check above already rules a cycle out.
    # Walk the chain anyway and refuse instead of looping forever if one
    # is ever found.
    parent_of = {
        sid: pid for sid, pid in db.session.query(Space.id, Space.parent_id)
        .filter(Space.venue_id == venue.id).all()
    }
    for row in fixed:
        parent_of[row["space"].id] = row["parent_id"]

    for row in fixed:
        seen = {row["space"].id}
        current = parent_of.get(row["parent_id"])
        for _ in range(len(parent_of) + 1):
            if current is None:
                break
            if current in seen:
                row["error"] = ("A cycle was found in the parent chain; "
                                "nothing was repaired")
                break
            seen.add(current)
            current = parent_of.get(current)

    if any(row["error"] for row in fixed):
        return render_space_admin_page(
            "spaces/repair_slices.html",
            venue=venue, rows=rows, rooms=active_rooms,
            show_archived=show_archived,
        )

    for row in fixed:
        space = row["space"]
        space.parent_id = row["parent_id"]
        space.kind = SPACE_KIND_SLICE
        space.updated_by_user_id = actor
        log_config_change("space", space.id, CONFIG_AUDIT_UPDATE, {
            "parent_id": space.parent_id, "kind": space.kind,
        })

    db.session.commit()
    flash(f"Repaired {len(fixed)} space{'' if len(fixed) == 1 else 's'}",
         "success")
    return redirect(_venue_catalog_url(venue, show_archived))


@spaces_bp.post("/venues/<int:venue_id>/space/<int:space_id>/archive")
@require_space_admin
def catalog_archive_space(venue_id: int, space_id: int):
    return _set_catalog_space_active(venue_id, space_id, False,
                                     CONFIG_AUDIT_ARCHIVE)


@spaces_bp.post("/venues/<int:venue_id>/space/<int:space_id>/restore")
@require_space_admin
def catalog_restore_space(venue_id: int, space_id: int):
    return _set_catalog_space_active(venue_id, space_id, True,
                                     CONFIG_AUDIT_RESTORE)
