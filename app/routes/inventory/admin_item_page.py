"""One page per item, with tabs shown by what the viewer may see.

The rights live in app/inventory/item_rights.py; this module only reads them.
"""
from flask import abort, flash, redirect, request, url_for

from app import db
from app.inventory.item_rights import item_rights
from app.inventory.stock_report import supplies_on_hand
from app.models import (
    CONFIG_AUDIT_UPDATE, InvStock, InvUnit, SupplyCategory, SupplyItem,
)
from app.routes import get_user_ctx

from . import inventory_bp
from .helpers import render_inventory_page, require_item_viewer, require_shared_editor


def _status_line(item, rights) -> str:
    parts = ["Orderable" if item.is_orderable else "Not orderable",
             "Tracked as assets" if item.is_serialized else "Counted"]
    if rights.sees_stock:
        if item.is_serialized:
            count = db.session.query(InvUnit).filter_by(item_id=item.id).count()
            parts.append(f"{count} asset{'' if count == 1 else 's'}")
        else:
            row = next((r for r in supplies_on_hand() if r["item_id"] == item.id), None)
            parts.append(f"{row['total'] if row else 0} on hand")
    return " · ".join(parts)


def item_state(item) -> dict:
    """Return every field the About tab writes, for the config audit diff."""
    return {
        "category_id": item.category_id, "item_name": item.item_name, "unit": item.unit,
        "image_url": item.image_url, "is_active": item.is_active,
        "is_orderable": item.is_orderable, "is_serialized": item.is_serialized,
    }


@inventory_bp.get("/admin/items/<int:item_id>")
@require_item_viewer
def item_page(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    rights = item_rights(get_user_ctx())
    tracked = bool(item.is_serialized)
    tabs = [
        {"id": "about", "label": "About"},
        {"id": "ordering", "label": "Ordering",
         "when": rights.sees_ordering and item.is_orderable},
        {"id": "inventory", "label": "Inventory",
         "when": rights.sees_inventory and tracked},
        {"id": "assets", "label": "Assets",
         "when": rights.sees_inventory and tracked},
        {"id": "on_hand", "label": "On hand",
         "when": rights.sees_stock and not tracked},
    ]
    return render_inventory_page(
        "inventory/item.html", item=item, rights=rights, tabs=tabs,
        status_line=_status_line(item, rights), active_tab=request.args.get("tab"),
        categories=(db.session.query(SupplyCategory).filter_by(is_active=True)
                    .order_by(SupplyCategory.name).all()
                    if rights.can_edit_shared else []))


@inventory_bp.post("/admin/items/<int:item_id>/about")
@require_shared_editor
def save_item_about(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    rights = item_rights(get_user_ctx())
    form = request.form
    # A switch the user may not set is refused outright; it is never read as "off".
    if (("is_orderable" in form and not rights.can_set_orderable)
            or ("is_serialized" in form and not rights.can_set_tracked)):
        abort(403, "You may not set that switch")

    back = redirect(url_for("inventory.item_page", item_id=item.id, tab="about"))
    name = form.get("item_name", "").strip()
    unit = form.get("unit", "").strip()
    category = db.session.get(SupplyCategory, form.get("category_id", type=int) or 0)
    if not name or category is None or not unit:
        flash("Item name, category, and unit of measure are required.", "error")
        return back
    if len(name) > 256 or len(unit) > 32:
        flash("An item name is at most 256 characters and a unit of measure at most 32.", "error")
        return back

    tracked = item.is_serialized
    if rights.can_set_tracked:
        tracked = form.get("is_serialized") == "1"
        if tracked and not item.is_serialized and db.session.query(InvStock).filter(
                InvStock.item_id == item.id, InvStock.quantity > 0).first():
            flash(f"{item.item_name} has counted stock. Count it to zero before "
                  f"tracking each piece as an asset.", "error")
            return back
        if not tracked and item.is_serialized and db.session.query(InvUnit).filter_by(
                item_id=item.id).first():
            flash(f"{item.item_name} has assets and must stay tracked as assets.", "error")
            return back

    # Imported here: app.routes.admin.helpers is circular at module level.
    from app.routes.admin.helpers import log_config_change, track_changes
    old_state = item_state(item)
    item.item_name, item.unit, item.category_id = name, unit, category.id
    item.is_active = bool(form.get("is_active"))
    if rights.can_set_orderable:
        item.is_orderable = form.get("is_orderable") == "1"
    item.is_serialized = tracked
    item.updated_by_user_id = get_user_ctx().user_id

    # Imported here so the image service loads only when the form is used.
    from app.services.images import (
        ImageStorageError, ImageValidationError, delete_item_image,
        process_and_upload_item_image,
    )
    old_image_url = item.image_url
    image_file = request.files.get("image_file")
    if form.get("remove_image"):
        if old_image_url:
            delete_item_image(old_image_url)
        item.image_url = None
    elif image_file and image_file.filename:
        try:
            item.image_url = process_and_upload_item_image(image_file, item.id)
            if old_image_url and old_image_url != item.image_url:
                delete_item_image(old_image_url)
        except (ImageValidationError, ImageStorageError) as exc:
            # The fields still save; a picture is never required.
            flash(f"Image not saved: {exc}", "error")

    changes = track_changes(old_state, item_state(item))
    if changes:
        log_config_change("supply_item", item.id, CONFIG_AUDIT_UPDATE, changes)
    db.session.commit()
    flash(f"Saved {item.item_name}.", "success")
    return back
