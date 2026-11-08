"""Inventory flags on SupplyItem, editable by inventory admins.

The Supply item form is gated to Supply admins, so the inventory columns
get their own page. Deploy-only items are created here because they
never belong in the Supply catalog.
"""
from flask import abort, flash, redirect, request, url_for

from app import db
from app.inventory.codes import normalize_code
from app.inventory.ledger import InventoryError
from app.inventory.unit_fields import active_fields, display_value, set_model_default
from app.models import (
    InvGearKind, InvModelDefault, InvStock, InvUnit, SupplyCategory, SupplyItem,
)

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin


@inventory_bp.get("/admin/items")
@require_inventory_admin
def items():
    rows = (db.session.query(SupplyItem).join(SupplyCategory)
            .filter(SupplyItem.is_active.is_(True))
            .order_by(SupplyCategory.name, SupplyItem.item_name).all())
    categories = (db.session.query(SupplyCategory).filter_by(is_active=True)
                  .order_by(SupplyCategory.name).all())
    gear_kinds = db.session.query(InvGearKind).order_by(InvGearKind.name).all()
    defaults = {}
    for item in rows:
        if item.is_serialized and item.gear_kind is not None:
            held = {d.field_id: d for d in db.session.query(InvModelDefault)
                    .filter_by(item_id=item.id)}
            defaults[item.id] = [(f, display_value(f, held.get(f.id)),
                                  [c.label for c in f.choices if c.is_active])
                                 for f in active_fields(item.gear_kind)]
    return render_inventory_page("inventory/items.html", items=rows, categories=categories,
                                 gear_kinds=gear_kinds, defaults=defaults)


def _barcode_error(item_id: int | None, barcode: str | None) -> str | None:
    if not barcode:
        return None
    if len(barcode) > 64:
        return "A barcode is at most 64 characters."
    other = db.session.query(SupplyItem).filter(SupplyItem.barcode == barcode,
                                                SupplyItem.id != item_id).first()
    if other:
        return f"{barcode} is already on {other.item_name}."
    return None


def _parse_pack_size(raw: str | None) -> tuple[int | None, str | None]:
    """Return (pack size, error). Blank means no pack size."""
    text = (raw or "").strip()
    if not text:
        return None, None
    # The column is a 32-bit Integer; a larger value would fail at commit.
    if not text.isascii() or not text.isdigit() or not 0 < int(text) <= 2_147_483_647:
        return None, "A pack size is blank or a whole number above zero."
    return int(text), None


@inventory_bp.post("/admin/items/<int:item_id>")
@require_inventory_admin
def update_item(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    serialized = request.form.get("is_serialized") == "1"
    barcode = normalize_code(request.form.get("barcode")) or None

    if serialized and not item.is_serialized and db.session.query(InvStock).filter(
            InvStock.item_id == item.id, InvStock.quantity > 0).first():
        flash(f"{item.item_name} has counted stock. Count it to zero before "
              f"making it serialized.", "error")
        return redirect(url_for("inventory.items"))
    if not serialized and item.is_serialized and db.session.query(InvUnit).filter_by(
            item_id=item.id).first():
        flash(f"{item.item_name} has serialized units and must stay serialized.", "error")
        return redirect(url_for("inventory.items"))
    pack_size, error = _parse_pack_size(request.form.get("pack_size"))
    error = error or _barcode_error(item.id, barcode)
    if error:
        flash(error, "error")
        return redirect(url_for("inventory.items"))

    item.is_serialized = serialized
    item.is_orderable = request.form.get("is_orderable") == "1"
    item.barcode = barcode
    item.pack_size = pack_size
    item.needs_asset_tag = bool(request.form.get("needs_asset_tag"))
    db.session.commit()
    flash(f"Saved {item.item_name}.", "success")
    return redirect(url_for("inventory.items"))


@inventory_bp.post("/admin/items/new")
@require_inventory_admin
def create_deploy_item():
    name = (request.form.get("item_name") or "").strip()
    unit = (request.form.get("unit") or "each").strip()
    category = db.session.get(SupplyCategory, request.form.get("category_id", type=int) or 0)
    if not name or len(name) > 256 or category is None or not unit or len(unit) > 32:
        flash("A deploy-only item needs a name, a category, and a unit.", "error")
        return redirect(url_for("inventory.items"))
    serialized = request.form.get("is_serialized") == "1"
    db.session.add(SupplyItem(
        category_id=category.id, item_name=name, unit=unit,
        is_serialized=serialized, is_orderable=False, is_expendable=not serialized,
    ))
    db.session.commit()
    flash(f"Added {name}. It will not appear in the Supply catalog.", "success")
    return redirect(url_for("inventory.items"))


@inventory_bp.post("/admin/items/<int:item_id>/gear-kind")
@require_inventory_admin
def set_gear_kind(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    if not item.is_serialized:
        flash("Only serialized items have a gear kind.", "error")
        return redirect(url_for("inventory.items"))
    raw = request.form.get("gear_kind_id", type=int)
    kind = db.session.get(InvGearKind, raw) if raw else None
    if raw and kind is None:
        flash("Pick a gear kind from the list.", "error")
        return redirect(url_for("inventory.items"))
    item.gear_kind_id = kind.id if kind else None
    db.session.commit()
    flash(f"Saved the gear kind for {item.item_name}.", "success")
    return redirect(url_for("inventory.items"))


@inventory_bp.post("/admin/items/<int:item_id>/defaults")
@require_inventory_admin
def set_model_defaults(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    if not item.is_serialized or item.gear_kind is None:
        flash("Defaults need a serialized item with a gear kind.", "error")
        return redirect(url_for("inventory.items"))
    fields = [f for f in active_fields(item.gear_kind) if f"field_{f.id}" in request.form]
    try:
        for field in fields:
            set_model_default(item, field, request.form.get(f"field_{field.id}"))
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        db.session.commit()
        flash(f"Saved the defaults for {item.item_name}. New units start with them.", "success")
    return redirect(url_for("inventory.items"))
