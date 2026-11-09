"""Units page: find a unit by any identifier, then correct it or print its label."""
from datetime import datetime

from flask import abort, flash, redirect, request, url_for
from sqlalchemy import false, func, or_
from sqlalchemy.orm import selectinload

from app import db
from app.routes import get_user_ctx
from app.inventory.audit import (
    ACTION_ADD_CODE, ACTION_CONDITION, ACTION_FIELDS, ACTION_ITEM,
    ACTION_RETIRE_CODE,
    record_unit_change,
)
from app.inventory.checks import checked_serial, unit_code
from app.inventory.codes import normalize_code, normalize_identifier, normalize_mac
from app.inventory.ledger import (
    INV_CONDITIONS, NUMBER_KINDS, InventoryError, add_identifier, replace_number,
    retire_identifier, set_condition,
)
from app.inventory.cards import default_event
from app.inventory.csv_export import csv_response
from app.inventory.notes import NOTE_TYPE_WORDS, SOURCE_WORDS, add_note, unit_notes
from app.inventory.names import name_key, set_unit_name
from app.inventory.tree import display_name
from app.inventory.printing import (
    REPRINT_CURRENT_DETAILS, REPRINT_MODES, REPRINT_TODAY, REPRINT_ORIGINAL_DATE,
    local_day, local_iso, local_today, local_when, reprint, reprint_options,
)
from app.inventory.resolver import KIND_AMBIGUOUS, KIND_UNIT, resolve
from app.inventory.unit_fields import (
    active_fields, details_line, model_number, parse_value, set_unit_value, unit_field_rows,
)
from app.inventory.last_check import (
    CHECK_FILTERS, CHECK_WORDS, check_filter, check_word, last_seen_dates,
    latest_check_rows, parse_since, since_start,
)
from app.inventory.unit_station import give_unit_number, number_refusal
from app.models import (
    InvContainer, InvField, InvFlag, InvGearKind, InvUnit, InvUnitIdentifier,
    InvUnitValue, SupplyItem,
)
from app.inventory.history import CONDITION_WORDS
from app.inventory.people import actor_name, names_for
from app.inventory.history import unit_history
from app.inventory.unit_about import detail_rows, find_by_rows, placed_line, status_card, where_sentence
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_FIELD_BOOL, INV_FIELD_CHOICE, INV_FIELD_NUMBER,
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL,
    INV_IDENT_VENDOR_TAG,
)
from . import inventory_bp
from .admin_flags import flag_rows
from .helpers import (
    admin_actor, note_types_for, render_inventory_page, require_inventory_admin, require_inventory_operator,
)

SEARCH_LIMIT = 50
IDENTIFIER_KINDS = ((INV_IDENT_MAGFEST_TAG, "MAGFest tag"), (INV_IDENT_FESTOPS_LABEL, "FestOps label"),
                    (INV_IDENT_VENDOR_TAG, "Vendor tag"), (INV_IDENT_SERIAL, "Serial"),
                    (INV_IDENT_MAC, "MAC address"))
KIND_WORDS = dict(IDENTIFIER_KINDS)
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
                 chosen: dict[int, str], show: str, since: datetime):
    """Units matching the search text, kind, field answers and check filter.

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
        query = query.filter(check_filter(show, since))
    return query


def _unit_rows(units: list[InvUnit]) -> list[dict]:
    ids = [u.id for u in units]
    counts = (dict(db.session.query(InvFlag.unit_id, func.count(InvFlag.id))
                   .filter(InvFlag.unit_id.in_(ids), InvFlag.resolved_at.is_(None))
                   .group_by(InvFlag.unit_id).all()) if ids else {})
    preps = latest_check_rows(ids)
    seen = last_seen_dates(ids)
    rows = []
    for u in units:
        prep = preps.get(u.id)
        word = check_word(prep)
        rows.append({
            "unit": u, "number": unit_code(u), "open_flags": counts.get(u.id, 0),
            "check_word": CHECK_WORDS.get(word, ""),
            "checked_at": prep.created_at if word else None,
            "seen_at": seen.get(u.id),
            "checked_day": local_day(prep.created_at) if word else "",
            "seen_day": local_day(seen[u.id]) if u.id in seen else "",
            "where": display_name(u.current_container) if u.current_container else "Not placed yet"})
    return rows


def _csv_rows(rows: list[dict]) -> list[list]:
    return [[r["unit"].name or "", r["number"] or "", r["unit"].item.item_name,
             model_number(r["unit"]) or "", r["check_word"] or "Never",
             local_iso(r["checked_at"]) if r["checked_at"] else "",
             local_iso(r["seen_at"]) if r["seen_at"] else "Never",
             CONDITION_WORDS.get(r["unit"].condition, r["unit"].condition), r["where"],
             r["open_flags"]]
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
    day, _ = parse_since(request.args.get("since"))
    start = since_start(day)
    show = request.args.get("show", "")
    if show not in CHECK_FILTERS:
        show = ""
    query = _units_query(q, kind, fields, chosen, show, start)
    # NULLs sort first on SQLite and last on PostgreSQL; nulls_last() makes both agree.
    ordered = query.order_by(InvUnit.name_key.asc().nulls_last(), InvUnit.id)
    if request.args.get("format") == "csv":
        found = ordered.options(*_ROW_LOADS, selectinload(InvUnit.values)
                                .selectinload(InvUnitValue.field)).all()
        return csv_response(
            f"units-{local_today():%Y-%m-%d}.csv",
            ["Name", "Tag or label", "Inventory item group", "Model", "Last checked",
             "Last checked at", "Last seen", "Condition", "Where", "Open flags"],
            _csv_rows(_unit_rows(found)))
    total = query.count()
    checked = query.filter(check_filter("checked", start)).count()
    broken = query.filter(check_filter("broken", start)).count()
    pages = max(1, -(-total // SEARCH_LIMIT))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    found = (ordered.options(*_ROW_LOADS).limit(SEARCH_LIMIT)
             .offset((page - 1) * SEARCH_LIMIT).all())
    kinds = db.session.query(InvGearKind).order_by(InvGearKind.name).all()
    keep = {k: v for k, v in request.args.items() if k not in ("page", "format") and v}
    return render_inventory_page(
        "inventory/units.html", q=q, rows=_unit_rows(found), kinds=kinds, kind=kind,
        total=total, checked=checked, broken=broken, show=show, page=page, pages=pages,
        since=day.isoformat(), since_word=local_day(start),
        keep=keep, filter_fields=fields, chosen=chosen, condition_words=CONDITION_WORDS,
        show_buttons=(("checked", "Checked"), ("not_checked", "Not checked"),
                      ("broken", "Broken"), ("no_number", "No number yet")))


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
    editing = request.args.get("tab") == "edit"
    user_ctx = get_user_ctx()
    context = {"unit": unit, "number": unit_code(unit), "editing": editing,
               "note_types": note_types_for(user_ctx), "note_type_words": NOTE_TYPE_WORDS}
    if editing:
        options = reprint_options(unit)
        context.update(
            model=model_number(unit),
            identifiers=sorted(unit.identifiers, key=lambda i: (i.retired_at is not None, i.id)),
            kind_words=KIND_WORDS, kinds=IDENTIFIER_KINDS, conditions=INV_CONDITIONS,
            condition_words=CONDITION_WORDS, items=_selectable_items(unit),
            print_modes=[(m, REPRINT_WORDS[m]) for m in _print_modes(options)],
            print_options=options, retired_condition=INV_CONDITION_RETIRED,
            field_inputs=_field_inputs(unit), has_gear_kind=unit.item.gear_kind is not None,
            can_give_number=_can_give_number(unit))
        return render_inventory_page("inventory/unit.html", **context)

    flags = (db.session.query(InvFlag).filter(InvFlag.unit_id == unit.id)
             .order_by(InvFlag.created_at.desc(), InvFlag.id.desc()).all())
    open_rows = [r for r in flag_rows(flags) if r["flag"].resolved_at is None]
    notes = unit_notes(unit.id)
    names = names_for(n.created_by_user_id for n in notes)
    note_views = [{"badge": SOURCE_WORDS.get(n.source) or NOTE_TYPE_WORDS.get(n.note_type, n.note_type),
                   "body": n.body,
                   "byline": (f"{local_when(n.created_at)} \u00b7 "
                              f"{actor_name(n.created_by_user_id, None, names)}")
                   if n.source not in SOURCE_WORDS else None}
                  for n in notes]
    history = [{"stamp": local_when(line.at), "who": line.who, "text": line.text, "note": line.note}
               for line in unit_history(unit)]
    kind = unit.item.gear_kind
    subline = " \u00b7 ".join(part for part in (unit.item.item_name, kind.name if kind else "",
                                               details_line(unit)) if part)
    context.update(
        subline=subline, status=status_card(unit, default_event()),
        where=where_sentence(unit), placed=placed_line(unit),
        find_by=find_by_rows(unit), details=detail_rows(unit),
        notes=note_views, open_rows=open_rows, history=history)
    return render_inventory_page("inventory/unit.html", **context)


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


def _back(unit: InvUnit, tab: str | None = None, anchor: str | None = None):
    url = url_for("inventory.unit_page", unit_id=unit.id, tab=tab)
    return redirect(f"{url}#{anchor}" if anchor else url)


def _active_codes(unit: InvUnit) -> list[str]:
    # Queried, not read from unit.identifiers, which may be loaded and stale.
    rows = (db.session.query(InvUnitIdentifier)
            .filter(InvUnitIdentifier.unit_id == unit.id, InvUnitIdentifier.retired_at.is_(None))
            .order_by(InvUnitIdentifier.id).all())
    return [f"{KIND_WORDS.get(i.kind, i.kind)} {i.value}" for i in rows]


def _snapshot(unit: InvUnit) -> dict:
    """What the Changes table compares. Field values are keyed by the field's name."""
    snap = {"codes": _active_codes(unit), "condition": unit.condition,
            "item": unit.item.item_name, "name": unit.name or ""}
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


def _commit_or_flash(action, success: str, unit: InvUnit, anchor: str | None = None):
    try:
        action()
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
        return _back(unit, tab="edit")
    db.session.commit()
    flash(success, "success")
    return _back(unit, anchor=anchor)


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
    raws = {field.id: request.form.get(f"field_{field.id}") for field, _ in rows
            if f"field_{field.id}" in request.form}

    def change():
        # Parse every field before writing any, so one bad value saves nothing.
        for field, _ in rows:
            if field.id in raws:
                parse_value(field, raws[field.id])
        set_unit_name(unit, request.form.get("name"))
        for field, _ in rows:
            if field.id in raws:
                set_unit_value(unit, field, raws[field.id])
        db.session.flush()

    return _commit_or_flash(_audited(change, unit, ACTION_FIELDS), "Details saved.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/notes")
@require_inventory_operator
def unit_add_note(unit_id: int):
    unit = _unit_or_404(unit_id)
    user_ctx = get_user_ctx()
    note_type = request.form.get("note_type", "")

    def add():
        if note_type not in note_types_for(user_ctx):
            raise InventoryError("You cannot add that kind of note.")
        add_note(unit, note_type, request.form.get("body"), user_ctx.user_id)

    return _commit_or_flash(add, "Note added.", unit, anchor="notes")


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
        return _back(unit, tab="edit")
    # The number is saved first, so a missing printer route does not cost the unit its number.
    db.session.commit()
    code = added[0]
    try:
        reprint(unit, REPRINT_CURRENT_DETAILS, admin_actor())
    except InventoryError as error:
        db.session.rollback()
        flash(f"{code} added, but the label could not be sent. {error}", "error")
        return _back(unit, tab="edit")
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
        return _back(unit, tab="edit")
    return _commit_or_flash(lambda: reprint(unit, mode, admin_actor()),
                            "Label sent to the print queue.", unit)
