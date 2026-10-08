"""Inventory flags on SupplyItem, editable by inventory admins.

The Supply item form is gated to Supply admins, so the three inventory
columns get their own page. Deploy-only items are created here because they
never belong in the Supply catalog.
"""
from flask import abort, flash, redirect, request, url_for

from app import db
from app.inventory.codes import normalize_code
from app.models import InvStock, InvUnit, SupplyCategory, SupplyItem

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
    return render_inventory_page("inventory/items.html", items=rows, categories=categories)


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
    error = _barcode_error(item.id, barcode)
    if error:
        flash(error, "error")
        return redirect(url_for("inventory.items"))

    item.is_serialized = serialized
    item.is_orderable = request.form.get("is_orderable") == "1"
    item.barcode = barcode
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
