"""The Items list, Add an item, and item-page posts for assets.

Supply admins and inventory admins both use this module. New items default to
orderable. The asset type and defaults posts here serve the Inventory tab of
the item page in admin_item_page.
"""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy.orm import joinedload

from app import db
from app.inventory.ledger import InventoryError
from app.inventory.unit_fields import active_fields, set_model_default
from app.inventory.item_rights import item_rights
from app.models import (
    CONFIG_AUDIT_CREATE, InvGearKind, SupplyCategory, SupplyItem,
)

from app.routes import get_user_ctx

from . import inventory_bp
from .admin_item_page import item_state
from .helpers import (
    render_inventory_page, require_inventory_admin, require_item_viewer,
    require_shared_editor,
)


@inventory_bp.get("/admin/items")
@require_item_viewer
def items():
    show_inactive = request.args.get("show") == "inactive"
    rows = (db.session.query(SupplyItem).join(SupplyCategory)
            .options(joinedload(SupplyItem.gear_kind))
            .filter(SupplyItem.is_active.is_(not show_inactive))
            .order_by(SupplyCategory.name, SupplyItem.item_name).all())
    categories = (db.session.query(SupplyCategory).filter_by(is_active=True)
                  .order_by(SupplyCategory.name).all())
    return render_inventory_page("inventory/items.html", items=rows, categories=categories,
                                 show_inactive=show_inactive,
                                 rights=item_rights(get_user_ctx()))


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


@inventory_bp.post("/admin/items/new")
@require_shared_editor
def create_deploy_item():
    rights = item_rights(get_user_ctx())
    # A switch the user may not set is refused; an unchecked box from a permitted user means off.
    if (("is_orderable" in request.form and not rights.can_set_orderable)
            or ("is_serialized" in request.form and not rights.can_set_tracked)):
        abort(403, "You may not set that switch")
    name = (request.form.get("item_name") or "").strip()
    unit = (request.form.get("unit") or "each").strip()
    category = db.session.get(SupplyCategory, request.form.get("category_id", type=int) or 0)
    if not name or len(name) > 256 or category is None or not unit or len(unit) > 32:
        flash("An item needs a name, a category, and a unit of measure.", "error")
        return redirect(url_for("inventory.items"))
    orderable = request.form.get("is_orderable") == "1" if rights.can_set_orderable else True
    tracked = request.form.get("is_serialized") == "1" if rights.can_set_tracked else False
    item = SupplyItem(
        category_id=category.id, item_name=name, unit=unit,
        is_serialized=tracked, is_orderable=orderable, is_expendable=not tracked,
        updated_by_user_id=get_user_ctx().user_id,
    )
    db.session.add(item)
    db.session.flush()
    # Imported here: app.routes.admin.helpers is circular at module level.
    from app.routes.admin.helpers import log_config_change
    log_config_change("supply_item", item.id, CONFIG_AUDIT_CREATE, item_state(item))
    db.session.commit()
    flash(f"Added {name}.", "success")
    return redirect(url_for("inventory.item_page", item_id=item.id))


@inventory_bp.post("/admin/items/<int:item_id>/gear-kind")
@require_inventory_admin
def set_gear_kind(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    if not item.is_serialized:
        flash("Only items tracked as assets have an asset type.", "error")
        return redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))
    raw = request.form.get("gear_kind_id", type=int)
    kind = db.session.get(InvGearKind, raw) if raw else None
    if raw and kind is None:
        flash("Pick an asset type from the list.", "error")
        return redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))
    item.gear_kind_id = kind.id if kind else None
    db.session.commit()
    flash(f"Saved the asset type for {item.item_name}.", "success")
    return redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))


@inventory_bp.post("/admin/items/<int:item_id>/defaults")
@require_inventory_admin
def set_model_defaults(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    if not item.is_serialized or item.gear_kind is None:
        flash("Defaults need an item tracked as assets with an asset type.", "error")
        return redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))
    fields = [f for f in active_fields(item.gear_kind) if f"field_{f.id}" in request.form]
    try:
        for field in fields:
            set_model_default(item, field, request.form.get(f"field_{field.id}"))
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        db.session.commit()
        flash(f"Saved the defaults for {item.item_name}. New assets start with them.", "success")
    return redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))
