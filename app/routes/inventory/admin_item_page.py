"""One page per item, with tabs shown by what the viewer may see.

The rights live in app/inventory/item_rights.py; this module only reads them.
"""
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, request, url_for

from app import db
from app.inventory.allocation import active_allocation_count
from app.inventory.checks import unit_code
from app.inventory.codes import normalize_code
from app.inventory.item_rights import item_rights
from app.inventory.printing import local_stamp
from app.inventory.stock_report import supplies_on_hand
from app.inventory.unit_fields import active_fields, display_value
from app.models import (
    CONFIG_AUDIT_UPDATE, InvCheckItem, InvGearKind, InvModelDefault, InvStock, InvUnit,
    SupplyCategory, SupplyItem, TechOpsServiceType,
)
from app.routes import get_user_ctx

from . import inventory_bp
from .helpers import (
    render_inventory_page, require_inventory_admin, require_item_viewer,
    require_ordering_editor, require_shared_editor,
)


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
    """Return every field the item page tabs write, for the config audit diff."""
    return {
        "category_id": item.category_id, "item_name": item.item_name, "unit": item.unit,
        "image_url": item.image_url, "is_active": item.is_active,
        "is_orderable": item.is_orderable, "is_serialized": item.is_serialized,
        "unit_cost_cents": item.unit_cost_cents, "order_guidance": item.order_guidance,
        "notes": item.notes, "notes_required": item.notes_required,
        "is_popular": item.is_popular, "is_limited": item.is_limited,
        "is_expendable": item.is_expendable, "sort_order": item.sort_order,
        "internal_type": item.internal_type, "needs_asset_tag": item.needs_asset_tag,
        "barcode": item.barcode, "pack_size": item.pack_size,
        "needs_pre_allocation": item.needs_pre_allocation,
        "techops_service_type_id": item.techops_service_type_id,
    }


def _defaults(item) -> list:
    """Return (field, shown value, choice labels) per field of the item's asset type."""
    if item.gear_kind is None:
        return []
    held = {d.field_id: d for d in db.session.query(InvModelDefault).filter_by(item_id=item.id)}
    return [(f, display_value(f, held.get(f.id)), [c.label for c in f.choices if c.is_active])
            for f in active_fields(item.gear_kind)]


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
    extra = {}
    if rights.sees_inventory and tracked:
        extra["gear_kinds"] = db.session.query(InvGearKind).order_by(InvGearKind.name).all()
        extra["defaults"] = _defaults(item)
        extra["prompt_max"] = InvCheckItem.prompt.type.length
        extra["lines"] = (db.session.query(InvCheckItem).filter_by(item_id=item.id)
                          .order_by(InvCheckItem.position, InvCheckItem.id).all())
        units = (db.session.query(InvUnit).filter_by(item_id=item.id).all())
        extra["assets"] = sorted(
            ({"unit": u, "number": unit_code(u)} for u in units),
            key=lambda a: ((a["unit"].name or a["number"] or "").lower(), a["unit"].id))
    if rights.sees_stock and not tracked:
        stock = next((r for r in supplies_on_hand() if r["item_id"] == item.id), None)
        if stock:
            stock["counted_text"] = (local_stamp(stock["last_counted"])
                                     if stock["last_counted"] else "Never counted")
        extra["stock"] = stock
    if rights.can_set_tracked:
        # An inactive service stays listed only while this item points at it.
        extra["techops_services"] = [
            s for s in db.session.query(TechOpsServiceType)
            .order_by(TechOpsServiceType.sort_order, TechOpsServiceType.name).all()
            if s.is_active or s.id == item.techops_service_type_id]
    return render_inventory_page(
        "inventory/item.html", item=item, rights=rights, tabs=tabs, **extra,
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
            or (("is_serialized" in form or "needs_pre_allocation" in form
                 or "techops_service_type_id" in form) and not rights.can_set_tracked)):
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

    if item.is_active and not form.get("is_active"):
        held = active_allocation_count(item.id)
        if held:
            flash(f"{item.item_name} has {held} allocated asset{'' if held == 1 else 's'}. "
                  f"Release {'it' if held == 1 else 'them'} before making the item inactive.",
                  "error")
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

    pre_allocation = item.needs_pre_allocation
    service_id = item.techops_service_type_id
    if rights.can_set_tracked:
        pre_allocation = form.get("needs_pre_allocation") == "1"
        # The select still posts its value when the switch is off; the link means
        # nothing without the switch, so an unticked switch clears it.
        service_id = (form.get("techops_service_type_id", type=int) or None
                      if pre_allocation else None)
        if ((item.needs_pre_allocation and not pre_allocation)
                or service_id != item.techops_service_type_id):
            held = active_allocation_count(item.id)
            if held:
                flash(f"{item.item_name} has {held} allocated asset{'' if held == 1 else 's'}. "
                      f"Release them before turning off pre-allocation or changing its "
                      f"TechOps service.", "error")
                return back
        if pre_allocation and not tracked:
            flash("Needs pre-allocation is only for items tracked as assets.", "error")
            return back
        if service_id is not None and db.session.get(TechOpsServiceType, service_id) is None:
            flash("Pick a TechOps service from the list.", "error")
            return back

    # Imported here: app.routes.admin.helpers is circular at module level.
    from app.routes.admin.helpers import log_config_change, track_changes
    old_state = item_state(item)
    item.item_name, item.unit, item.category_id = name, unit, category.id
    item.is_active = bool(form.get("is_active"))
    if rights.can_set_orderable:
        item.is_orderable = form.get("is_orderable") == "1"
    item.is_serialized = tracked
    item.needs_pre_allocation = pre_allocation
    item.techops_service_type_id = service_id
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


@inventory_bp.post("/admin/items/<int:item_id>/ordering")
@require_ordering_editor
def save_item_ordering(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    form = request.form
    back = redirect(url_for("inventory.item_page", item_id=item.id, tab="ordering"))

    unit_cost = form.get("unit_cost", "").strip()
    unit_cost_cents = None
    if unit_cost:
        try:
            dollars = Decimal(unit_cost)
            cents = dollars * 100
            if not dollars.is_finite() or dollars < 0 or cents != cents.to_integral_value():
                raise ValueError(unit_cost)
            unit_cost_cents = int(cents)
        except (ValueError, InvalidOperation):
            flash("Invalid cost format.", "error")
            return back

    # Imported here: app.routes.admin.helpers is circular at module level.
    from app.routes.admin.helpers import log_config_change, safe_int_or_none, track_changes
    old_state = item_state(item)
    item.unit_cost_cents = unit_cost_cents
    item.order_guidance = form.get("order_guidance", "").strip() or None
    item.notes = form.get("notes", "").strip() or None
    item.notes_required = bool(form.get("notes_required"))
    item.is_popular = bool(form.get("is_popular"))
    item.is_limited = bool(form.get("is_limited"))
    item.is_expendable = bool(form.get("is_expendable"))
    item.sort_order = safe_int_or_none(form.get("sort_order"))
    item.internal_type = form.get("internal_type", "").strip() or None
    item.updated_by_user_id = get_user_ctx().user_id

    changes = track_changes(old_state, item_state(item))
    if changes:
        log_config_change("supply_item", item.id, CONFIG_AUDIT_UPDATE, changes)
    db.session.commit()
    flash(f"Saved {item.item_name}.", "success")
    return back


def _save_stock_fields(item, back, needs_asset_tag=None, pack_size_raw=None):
    """Validate and write the inventory-side fields one tab shows, then audit and commit."""
    # Imported here: admin_items imports item_state from this module.
    from .admin_items import _barcode_error, _parse_pack_size
    barcode = normalize_code(request.form.get("barcode")) or None
    pack_size, error = (_parse_pack_size(pack_size_raw) if pack_size_raw is not None
                        else (item.pack_size, None))
    error = error or _barcode_error(item.id, barcode)
    if error:
        flash(error, "error")
        return back
    # Imported here: app.routes.admin.helpers is circular at module level.
    from app.routes.admin.helpers import log_config_change, track_changes
    old_state = item_state(item)
    item.barcode = barcode
    item.pack_size = pack_size
    if needs_asset_tag is not None:
        item.needs_asset_tag = needs_asset_tag
    item.updated_by_user_id = get_user_ctx().user_id
    changes = track_changes(old_state, item_state(item))
    if changes:
        log_config_change("supply_item", item.id, CONFIG_AUDIT_UPDATE, changes)
    db.session.commit()
    flash(f"Saved {item.item_name}.", "success")
    return back


@inventory_bp.post("/admin/items/<int:item_id>/inventory")
@require_inventory_admin
def save_item_inventory(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    back = redirect(url_for("inventory.item_page", item_id=item.id, tab="inventory"))
    return _save_stock_fields(item, back, needs_asset_tag=bool(request.form.get("needs_asset_tag")))


@inventory_bp.post("/admin/items/<int:item_id>/on-hand")
@require_inventory_admin
def save_item_on_hand(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    back = redirect(url_for("inventory.item_page", item_id=item.id, tab="on_hand"))
    return _save_stock_fields(item, back, pack_size_raw=request.form.get("pack_size", ""))
