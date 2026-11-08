"""Units page: find a unit by any identifier, then correct it or print its label."""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import db
from app.inventory.checks import checked_serial, display_number
from app.inventory.codes import normalize_code, normalize_identifier, normalize_mac
from app.inventory.ledger import (
    INV_CONDITIONS, NUMBER_KINDS, InventoryError, add_identifier, replace_number,
    retire_identifier, set_condition,
)
from app.inventory.printing import (
    REPRINT_CURRENT_DETAILS, REPRINT_MODES, REPRINT_TODAY, REPRINT_ORIGINAL_DATE,
    local_stamp, reprint, reprint_options,
)
from app.inventory.resolver import KIND_AMBIGUOUS, KIND_UNIT, resolve
from app.models import InvFlag, InvMovement, InvUnit, InvUnitIdentifier, SupplyItem
from app.inventory.describe import describe_actor, describe_movement
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG,
    INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG,
)
from . import inventory_bp
from .admin_flags import flag_rows
from .helpers import (
    admin_actor, render_inventory_page, require_inventory_admin, require_inventory_operator,
)

SEARCH_LIMIT = 50
IDENTIFIER_KINDS = ((INV_IDENT_MAGFEST_TAG, "MAGFest tag"), (INV_IDENT_FESTOPS_LABEL, "FestOps label"),
                    (INV_IDENT_VENDOR_TAG, "Vendor tag"), (INV_IDENT_SERIAL, "Serial"),
                    (INV_IDENT_MAC, "MAC address"))
KIND_WORDS = dict(IDENTIFIER_KINDS)
CONDITION_WORDS = {"IN_SERVICE": "In service", "REPAIR": "In repair", "LOST": "Lost",
                   "RETIRED": "Retired"}
REPRINT_WORDS = {REPRINT_CURRENT_DETAILS: "Print the current details",
                 REPRINT_TODAY: "Reprint, dated today",
                 REPRINT_ORIGINAL_DATE: "Reprint, original date"}


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search(q: str) -> list[InvUnit]:
    """Units the resolver finds, then units with an active identifier containing the text."""
    found: dict[int, InvUnit] = {}
    resolution = resolve(q)
    if resolution.kind == KIND_UNIT:
        found[resolution.unit.id] = resolution.unit
    elif resolution.kind == KIND_AMBIGUOUS:
        for candidate in resolution.candidates:
            if isinstance(candidate, InvUnit):
                found[candidate.id] = candidate
    # normalize_identifier is per kind; only a MAC differs, so try both forms.
    patterns = {normalize_code(q)}
    mac = normalize_mac(q)
    if mac:
        patterns.add(normalize_identifier(INV_IDENT_MAC, q))
    patterns.discard("")
    for pattern in patterns:
        rows = (db.session.query(InvUnit)
                .join(InvUnitIdentifier, InvUnitIdentifier.unit_id == InvUnit.id)
                .filter(InvUnitIdentifier.retired_at.is_(None),
                        InvUnitIdentifier.normalized_value.like(
                            f"%{_like_escape(pattern)}%", escape="\\"))
                .order_by(InvUnit.id).limit(SEARCH_LIMIT).all())
        for unit in rows:
            found.setdefault(unit.id, unit)
    return list(found.values())[:SEARCH_LIMIT]


@inventory_bp.get("/admin/units")
@require_inventory_operator
def units():
    q = (request.args.get("q") or "").strip()
    rows = []
    if q:
        found = _search(q)
        ids = [u.id for u in found]
        counts = {}
        if ids:
            counts = dict(db.session.query(InvFlag.unit_id, func.count(InvFlag.id))
                          .filter(InvFlag.unit_id.in_(ids), InvFlag.resolved_at.is_(None))
                          .group_by(InvFlag.unit_id).all())
        rows = [{"unit": u, "number": display_number(u), "open_flags": counts.get(u.id, 0)}
                for u in found]
    return render_inventory_page("inventory/units.html", q=q, rows=rows,
                                 condition_words=CONDITION_WORDS)


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
    return render_inventory_page(
        "inventory/unit.html", unit=unit, number=display_number(unit),
        open_rows=[r for r in rows if r["flag"].resolved_at is None],
        resolved_rows=[r for r in rows if r["flag"].resolved_at is not None],
        movements=[(local_stamp(m.created_at), describe_movement(m), describe_actor(m))
                   for m in movements],
        identifiers=sorted(unit.identifiers, key=lambda i: (i.retired_at is not None, i.id)),
        kind_words=KIND_WORDS, kinds=IDENTIFIER_KINDS, conditions=INV_CONDITIONS,
        condition_words=CONDITION_WORDS, items=_selectable_items(unit),
        print_modes=[(m, REPRINT_WORDS[m]) for m in _print_modes(options)],
        print_options=options, retired_condition=INV_CONDITION_RETIRED)


def _back(unit: InvUnit):
    return redirect(url_for("inventory.unit_page", unit_id=unit.id))


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

    return _commit_or_flash(add, "Code saved.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/identifiers/<int:identifier_id>/retire")
@require_inventory_admin
def unit_retire_identifier(unit_id: int, identifier_id: int):
    unit = _unit_or_404(unit_id)
    identifier = db.session.get(InvUnitIdentifier, identifier_id)
    if identifier is None or identifier.unit_id != unit.id:
        abort(404)
    return _commit_or_flash(lambda: retire_identifier(identifier), "Code retired.", unit)


@inventory_bp.post("/admin/units/<int:unit_id>/condition")
@require_inventory_admin
def unit_set_condition(unit_id: int):
    unit = _unit_or_404(unit_id)
    return _commit_or_flash(lambda: set_condition(unit, request.form.get("condition", "")),
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
        db.session.expire(unit, ["item"])

    return _commit_or_flash(change, "Item type changed.", unit)


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
