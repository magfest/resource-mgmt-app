"""Units page: find a unit by any identifier, then correct it or print its label."""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy import false, func, or_
from sqlalchemy.orm import selectinload

from app import db
from app.inventory.audit import (
    ACTION_ADD_CODE, ACTION_CONDITION, ACTION_FIELDS, ACTION_ITEM, ACTION_NOTES,
    ACTION_RETIRE_CODE,
    record_unit_change, unit_changes,
)
from app.inventory.checks import checked_serial, unit_code
from app.inventory.codes import normalize_code, normalize_identifier, normalize_mac
from app.inventory.ledger import (
    INV_CONDITIONS, NUMBER_KINDS, InventoryError, add_identifier, replace_number,
    retire_identifier, set_condition,
)
from app.inventory.cards import default_event
from app.inventory.csv_export import csv_response
from app.inventory.names import name_key, set_unit_name
from app.inventory.tree import display_name
from app.inventory.printing import (
    REPRINT_CURRENT_DETAILS, REPRINT_MODES, REPRINT_TODAY, REPRINT_ORIGINAL_DATE,
    local_iso, local_stamp, local_today, reprint, reprint_options,
)
from app.inventory.resolver import KIND_AMBIGUOUS, KIND_UNIT, resolve
from app.inventory.unit_fields import (
    active_fields, model_number, parse_value, set_unit_value, unit_field_rows,
)
from app.inventory.week import (
    SHOW_FILTERS, WEEK_WORDS, live_prep_rows, show_condition, week_status,
)
from app.inventory.unit_station import give_unit_number, number_refusal
from app.models import (
    InvContainer, InvField, InvFlag, InvGearKind, InvMovement, InvShift, InvUnit, InvUnitIdentifier,
    InvUnitValue, SupplyItem, User,
)
from app.inventory.describe import describe_actor, describe_movement
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_FIELD_BOOL, INV_FIELD_CHOICE, INV_FIELD_NUMBER,
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL,
    INV_IDENT_VENDOR_TAG,
)
from . import inventory_bp
from .admin_flags import flag_rows
from .helpers import (
    admin_actor, render_inventory_page, require_inventory_admin, require_inventory_operator,
)

SEARCH_LIMIT = 50
MAX_NOTES_LENGTH = 2000
IDENTIFIER_KINDS = ((INV_IDENT_MAGFEST_TAG, "MAGFest tag"), (INV_IDENT_FESTOPS_LABEL, "FestOps label"),
                    (INV_IDENT_VENDOR_TAG, "Vendor tag"), (INV_IDENT_SERIAL, "Serial"),
                    (INV_IDENT_MAC, "MAC address"))
KIND_WORDS = dict(IDENTIFIER_KINDS)
CONDITION_WORDS = {"IN_SERVICE": "In service", "REPAIR": "In repair", "LOST": "Lost",
                   "RETIRED": "Retired"}
REPRINT_WORDS = {REPRINT_CURRENT_DETAILS: "Print the current details",
                 REPRINT_TODAY: "Reprint, dated today",
                 REPRINT_ORIGINAL_DATE: "Reprint, original date"}


# The units table reads these on every row.
_ROW_LOADS = (selectinload(InvUnit.item),
              selectinload(InvUnit.current_container).selectinload(InvContainer.kind),
              selectinload(InvUnit.identifiers))


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_condition(q: str):
    """SQL condition for units the resolver, the name or an active identifier matches."""
    clauses = []
    resolution = resolve(q)
    if resolution.kind == KIND_UNIT:
        clauses.append(InvUnit.id == resolution.unit.id)
    elif resolution.kind == KIND_AMBIGUOUS:
        ids = [c.id for c in resolution.candidates if isinstance(c, InvUnit)]
        if ids:
            clauses.append(InvUnit.id.in_(ids))
    key = name_key(q)
    if key is not None:
        clauses.append(InvUnit.name_key.like(f"%{_like_escape(key)}%", escape="\\"))
    patterns = {normalize_code(q)}
    if normalize_mac(q):
        patterns.add(normalize_identifier(INV_IDENT_MAC, q))
    patterns.discard("")
    for pattern in patterns:
        clauses.append(db.session.query(InvUnitIdentifier.id).filter(
            InvUnitIdentifier.unit_id == InvUnit.id, InvUnitIdentifier.retired_at.is_(None),
            InvUnitIdentifier.normalized_value.like(
                f"%{_like_escape(pattern)}%", escape="\\")).exists())
    return or_(*clauses) if clauses else false()


def _filter_fields(kind: InvGearKind | None) -> list[InvField]:
    """Active CHOICE and BOOL fields of the kind; the only ones the page can filter on."""
    return [f for f in active_fields(kind)
            if f.field_type in (INV_FIELD_CHOICE, INV_FIELD_BOOL)]


def _selected_filters(fields: list[InvField]) -> dict[int, str]:
    """Valid f_<field_id> answers by field id. Unknown fields and answers are dropped."""
    chosen = {}
    for field in fields:
        raw = (request.args.get(f"f_{field.id}") or "").strip()
        if field.field_type == INV_FIELD_BOOL and raw in ("yes", "no"):
            chosen[field.id] = raw
        elif (field.field_type == INV_FIELD_CHOICE and raw.isascii() and raw.isdigit()
              and len(raw) <= 9):
            if int(raw) in {c.id for c in field.choices}:
                chosen[field.id] = raw
    return chosen


def _value_matches(field: InvField, answer: str):
    if field.field_type == INV_FIELD_BOOL:
        column_test = InvUnitValue.bool_value.is_(answer == "yes")
    else:
        column_test = InvUnitValue.choice_id == int(answer)
    return (db.session.query(InvUnitValue.id)
            .filter(InvUnitValue.unit_id == InvUnit.id, InvUnitValue.field_id == field.id,
                    column_test).exists())


def _units_query(q: str, kind: InvGearKind | None, fields: list[InvField],
                 chosen: dict[int, str], show: str, event):
    """Units matching the search text, kind, field answers and week filter.

    Retired units are listed only when the search text finds them.
    """
    query = db.session.query(InvUnit).join(SupplyItem, SupplyItem.id == InvUnit.item_id)
    if kind is not None:
        query = query.filter(SupplyItem.gear_kind_id == kind.id)
    if q:
        query = query.filter(_search_condition(q))
    else:
        query = query.filter(InvUnit.condition != INV_CONDITION_RETIRED)
    for field in fields:
        if field.id in chosen:
            query = query.filter(_value_matches(field, chosen[field.id]))
    if show:
        query = query.filter(show_condition(event, show))
    return query


def _unit_rows(units: list[InvUnit], event) -> list[dict]:
    ids = [u.id for u in units]
    counts = (dict(db.session.query(InvFlag.unit_id, func.count(InvFlag.id))
                   .filter(InvFlag.unit_id.in_(ids), InvFlag.resolved_at.is_(None))
                   .group_by(InvFlag.unit_id).all()) if ids else {})
    preps = live_prep_rows(event, ids)
    rows = []
    for u in units:
        prep = preps.get(u.id)
        status = week_status(prep)
        if event is None:
            where = ""
        elif prep is None:
            where = "Not seen"
        else:
            where = display_name(u.current_container) if u.current_container else "Not placed"
        rows.append({"unit": u, "number": unit_code(u), "open_flags": counts.get(u.id, 0),
                     "week": status, "week_word": WEEK_WORDS[status] if event else "", "where": where,
                     "checked_at": prep.created_at if prep else None})
    return rows


def _csv_rows(rows: list[dict]) -> list[list]:
    return [[r["number"] or "", r["unit"].name or "", r["unit"].item.item_name,
             model_number(r["unit"]) or "", r["week_word"],
             CONDITION_WORDS.get(r["unit"].condition, r["unit"].condition), r["where"],
             r["open_flags"], local_iso(r["checked_at"]) if r["checked_at"] else ""]
            for r in rows]


@inventory_bp.get("/admin/units")
@require_inventory_operator
def units():
    q = (request.args.get("q") or "").strip()
    kind_code = (request.args.get("kind") or "").strip()
    kind = (db.session.query(InvGearKind).filter_by(code=kind_code).first()
            if kind_code else None)
    fields = _filter_fields(kind)
    chosen = _selected_filters(fields)
    event = default_event()
    show = request.args.get("show", "")
    if show not in SHOW_FILTERS or event is None and show != "no_number":
        show = ""
    query = _units_query(q, kind, fields, chosen, show, event)
    # NULLs sort first on SQLite and last on PostgreSQL; nulls_last() makes both agree.
    ordered = query.order_by(InvUnit.name_key.asc().nulls_last(), InvUnit.id)
    if request.args.get("format") == "csv":
        found = ordered.options(*_ROW_LOADS, selectinload(InvUnit.values)
                                .selectinload(InvUnitValue.field)).all()
        stamp = f"{local_today():%Y-%m-%d}"
        return csv_response(
            f"units-{event.code if event else 'no-event'}-{stamp}.csv",
            ["Number", "Name", "Type", "Model", "This week", "Condition", "Where",
             "Open flags", "Last checked"], _csv_rows(_unit_rows(found, event)))
    total = query.count()
    seen = query.filter(show_condition(event, "seen")).count() if event else 0
    broken = query.filter(show_condition(event, "broken")).count() if event else 0
    pages = max(1, -(-total // SEARCH_LIMIT))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    found = (ordered.options(*_ROW_LOADS).limit(SEARCH_LIMIT)
             .offset((page - 1) * SEARCH_LIMIT).all())
    kinds = db.session.query(InvGearKind).order_by(InvGearKind.name).all()
    keep = {k: v for k, v in request.args.items() if k not in ("page", "format") and v}
    return render_inventory_page(
        "inventory/units.html", q=q, rows=_unit_rows(found, event), kinds=kinds, kind=kind,
        total=total, seen=seen, broken=broken, event=event, show=show, page=page, pages=pages,
        keep=keep, filter_fields=fields, chosen=chosen, condition_words=CONDITION_WORDS,
        show_buttons=(("seen", "Seen"), ("not_seen", "Not seen"), ("broken", "Broken"),
                      ("no_number", "No number yet")))


def _page_where(unit: InvUnit, event, seen: bool) -> str:
    """Return the unit page's Where line, naming the recorded container even when unseen.

    The Units list column and its CSV keep the shorter `_unit_rows` wording.
    """
    place = display_name(unit.current_container) if unit.current_container else None
    if event is None or seen:
        return f"Where: {place or 'Not placed'}"
    if place:
        return f"Where: Not seen this week. Last recorded in {place}."
    return "Where: Not seen this week."


def _unit_or_404(unit_id: int) -> InvUnit:
    unit = db.session.get(InvUnit, unit_id)
    if unit is None:
        abort(404)
    return unit


def _print_modes(options: dict) -> list[str]:
    modes = [REPRINT_CURRENT_DETAILS]
    if options["quick_reprint"]:
        extra = [REPRINT_TODAY, REPRINT_ORIGINAL_DATE]
        modes = modes + extra if options["details_changed"] else extra + modes
    return modes


def _selectable_items(unit: InvUnit) -> list[SupplyItem]:
    return (db.session.query(SupplyItem)
            .filter(SupplyItem.is_active.is_(True), SupplyItem.is_serialized.is_(True))
            .order_by(SupplyItem.item_name).all())


@inventory_bp.get("/admin/units/<int:unit_id>")
@require_inventory_operator
def unit_page(unit_id: int):
    unit = _unit_or_404(unit_id)
    flags = (db.session.query(InvFlag).filter(InvFlag.unit_id == unit.id)
             .order_by(InvFlag.created_at.desc(), InvFlag.id.desc()).all())
    rows = flag_rows(flags)
    movements = (db.session.query(InvMovement).filter(InvMovement.unit_id == unit.id)
                 .options(selectinload(InvMovement.item),
                          selectinload(InvMovement.unit).selectinload(InvUnit.identifiers),
                          selectinload(InvMovement.subject_container),
                          selectinload(InvMovement.from_container),
                          selectinload(InvMovement.to_container),
                          selectinload(InvMovement.shift), selectinload(InvMovement.device))
                 .order_by(InvMovement.id.desc()).limit(20).all())
    options = reprint_options(unit)
    event = default_event()
    summary = _unit_rows([unit], event)[0]
    return render_inventory_page(
        "inventory/unit.html", unit=unit, number=unit_code(unit), event=event,
        summary=summary, model=model_number(unit),
        where_text=_page_where(unit, event, seen=summary["checked_at"] is not None),
        checked_text=local_iso(summary["checked_at"]) if summary["checked_at"] else "",
        open_rows=[r for r in rows if r["flag"].resolved_at is None],
        resolved_rows=[r for r in rows if r["flag"].resolved_at is not None],
        movements=[(local_stamp(m.created_at), describe_movement(m), describe_actor(m))
                   for m in movements],
        identifiers=sorted(unit.identifiers, key=lambda i: (i.retired_at is not None, i.id)),
        kind_words=KIND_WORDS, kinds=IDENTIFIER_KINDS, conditions=INV_CONDITIONS,
        condition_words=CONDITION_WORDS, items=_selectable_items(unit),
        print_modes=[(m, REPRINT_WORDS[m]) for m in _print_modes(options)],
        print_options=options, retired_condition=INV_CONDITION_RETIRED,
        changes=_change_rows(unit), field_inputs=_field_inputs(unit),
        has_gear_kind=unit.item.gear_kind is not None, can_give_number=_can_give_number(unit))


def _can_give_number(unit: InvUnit) -> bool:
    return number_refusal(unit) is None


def _field_inputs(unit: InvUnit) -> list[dict]:
    """Fields with the text a form control starts on."""
    inputs = []
    for field, text in unit_field_rows(unit):
        number = field.field_type == INV_FIELD_NUMBER
        value = text
        if number and field.unit_label and text.endswith(f" {field.unit_label}"):
            value = text[: -len(field.unit_label) - 1]
        inputs.append({
            "field": field, "value": value, "text": text,
            "choices": ([c.label for c in field.choices if c.is_active]
                        if field.field_type == INV_FIELD_CHOICE else []),
            "is_choice": field.field_type == INV_FIELD_CHOICE,
            "is_bool": field.field_type == INV_FIELD_BOOL, "is_number": number})
    return inputs


CHANGE_FIELDS = {"codes": "Codes", "condition": "Condition", "item": "Item type",
                 "name": "Name", "notes": "Notes"}


def _change_rows(unit: InvUnit) -> list[dict]:
    events = unit_changes(unit.id)
    ids = {row.created_by_user_id for row, _ in events}
    names = {u.id: u.display_name or u.id
             for u in db.session.query(User).filter(User.id.in_(ids))} if ids else {}

    shifts = {}
    for raw in ids:
        if isinstance(raw, str) and raw.startswith("shift:") and raw[6:].isdigit():
            shift = db.session.get(InvShift, int(raw[6:]))
            if shift is not None:
                shifts[raw] = f"{shift.person_name} (scanner)"

    def who(user_id):
        return names.get(user_id) or shifts.get(user_id) or user_id

    def words(field, value):
        if field == "codes":
            return ", ".join(value) or "none"
        if field == "condition":
            return CONDITION_WORDS.get(value, value)
        return value or "none"

    return [{"when": local_stamp(row.created_at),
             "who": who(row.created_by_user_id),
             "what": [f"{CHANGE_FIELDS.get(f, f)}: {words(f, c['old'])} to {words(f, c['new'])}"
                      for f, c in changes.items()]}
            for row, changes in events]


def _back(unit: InvUnit):
    return redirect(url_for("inventory.unit_page", unit_id=unit.id))


def _active_codes(unit: InvUnit) -> list[str]:
    # Queried, not read from unit.identifiers, which may be loaded and stale.
    rows = (db.session.query(InvUnitIdentifier)
            .filter(InvUnitIdentifier.unit_id == unit.id, InvUnitIdentifier.retired_at.is_(None))
            .order_by(InvUnitIdentifier.id).all())
    return [f"{KIND_WORDS.get(i.kind, i.kind)} {i.value}" for i in rows]


def _snapshot(unit: InvUnit) -> dict:
    """What the Changes table compares. Field values are keyed by the field's name."""
    snap = {"codes": _active_codes(unit), "condition": unit.condition,
            "item": unit.item.item_name, "name": unit.name or "", "notes": unit.notes or ""}
    for field, text in unit_field_rows(unit):
        snap[field.name] = text
    return snap


def _audited(action, unit: InvUnit, audit_action: str):
    """Run a correction and record what it changed; nothing is recorded if nothing changed."""
    def run():
        before = _snapshot(unit)
        action()
        db.session.expire(unit, ["item"])
        after = _snapshot(unit)
        changes = {key: {"old": before.get(key, ""), "new": after.get(key, "")}
                   for key in {**before, **after} if before.get(key, "") != after.get(key, "")}
        if changes:
            record_unit_change(unit.id, audit_action, changes, admin_actor().user_id)
    return run


def _commit_or_flash(action, success: str, unit: InvUnit):
    try:
        action()
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        db.session.commit()
        flash(success, "success")
    return _back(unit)


@inventory_bp.post("/admin/units/<int:unit_id>/identifiers")
@require_inventory_admin
def unit_add_identifier(unit_id: int):
    unit = _unit_or_404(unit_id)
    kind = request.form.get("kind", "")
    value = request.form.get("value", "")

    def add():
        if kind not in KIND_WORDS:
            raise InventoryError("Pick what kind of code this is.")
        if kind in NUMBER_KINDS:
            # An owned unit keeps one number, so a new tag or label retires the old one.
            replace_number(unit, kind, value, admin_actor())
        elif kind == INV_IDENT_SERIAL:
            add_identifier(unit, kind, checked_serial(unit, value))
        else:
            add_identifier(unit, kind, value.strip())

    return _commit_or_flash(_audited(add, unit, ACTION_ADD_CODE), "Code saved.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/identifiers/<int:identifier_id>/retire")
@require_inventory_admin
def unit_retire_identifier(unit_id: int, identifier_id: int):
    unit = _unit_or_404(unit_id)
    identifier = db.session.get(InvUnitIdentifier, identifier_id)
    if identifier is None or identifier.unit_id != unit.id:
        abort(404)
    return _commit_or_flash(_audited(lambda: retire_identifier(identifier), unit, ACTION_RETIRE_CODE),
                            "Code retired.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/condition")
@require_inventory_admin
def unit_set_condition(unit_id: int):
    unit = _unit_or_404(unit_id)
    condition = request.form.get("condition", "")
    return _commit_or_flash(_audited(lambda: set_condition(unit, condition), unit, ACTION_CONDITION),
                            "Condition saved.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/item")
@require_inventory_admin
def unit_set_item(unit_id: int):
    unit = _unit_or_404(unit_id)
    item = db.session.get(SupplyItem, request.form.get("item_id", type=int) or 0)

    def change():
        if item is None or not item.is_active or not item.is_serialized:
            raise InventoryError("Pick an active serialized item.")
        unit.item_id = item.id
        db.session.flush()

    return _commit_or_flash(_audited(change, unit, ACTION_ITEM), "Item type changed.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/details")
@require_inventory_admin
def unit_set_details(unit_id: int):
    unit = _unit_or_404(unit_id)
    rows = unit_field_rows(unit)
    notes = (request.form.get("notes") or "").replace("\r\n", "\n").strip() or None
    raws = {field.id: request.form.get(f"field_{field.id}") for field, _ in rows
            if f"field_{field.id}" in request.form}

    def change():
        if notes is not None and len(notes) > MAX_NOTES_LENGTH:
            raise InventoryError(f"Notes can be at most {MAX_NOTES_LENGTH} characters.")
        # Parse every field before writing any, so one bad value saves nothing.
        for field, _ in rows:
            if field.id in raws:
                parse_value(field, raws[field.id])
        set_unit_name(unit, request.form.get("name"))
        unit.notes = notes
        for field, _ in rows:
            if field.id in raws:
                set_unit_value(unit, field, raws[field.id])
        db.session.flush()

    return _commit_or_flash(_audited(change, unit, ACTION_FIELDS), "Details saved.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/imported-notes/clear")
@require_inventory_admin
def unit_clear_imported_notes(unit_id: int):
    unit = _unit_or_404(unit_id)

    def clear():
        old = unit.imported_notes
        if old is None:
            return
        unit.imported_notes = None
        db.session.flush()
        # The Changes table keeps the cleared text; nothing else does.
        record_unit_change(unit.id, ACTION_NOTES, {"Old notes": {"old": old, "new": None}},
                           admin_actor().user_id)

    return _commit_or_flash(clear, "Old notes cleared.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/give-number")
@require_inventory_admin
def unit_give_number(unit_id: int):
    """Add a new U- label to a unit with no number, then queue its label."""
    unit = _unit_or_404(unit_id)
    added = []

    def give():
        added.append(give_unit_number(unit))

    try:
        _audited(give, unit, ACTION_ADD_CODE)()
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
        return _back(unit)
    # The number is saved first, so a missing printer route does not cost the unit its number.
    db.session.commit()
    code = added[0]
    try:
        reprint(unit, REPRINT_CURRENT_DETAILS, admin_actor())
    except InventoryError as error:
        db.session.rollback()
        flash(f"{code} added, but the label could not be sent. {error}", "error")
    else:
        db.session.commit()
        flash(f"{code} added and sent to the printer.", "success")
    return _back(unit)


@inventory_bp.post("/admin/units/<int:unit_id>/print")
@require_inventory_operator
def unit_print(unit_id: int):
    unit = _unit_or_404(unit_id)
    mode = request.form.get("mode", "")
    if mode not in REPRINT_MODES:
        flash("Pick how to print the label.", "error")
        return _back(unit)
    return _commit_or_flash(lambda: reprint(unit, mode, admin_actor()),
                            "Label sent to the print queue.", unit)
