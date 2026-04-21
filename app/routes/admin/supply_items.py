"""
Admin routes for supply item (catalog) management.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    SupplyItem,
    SupplyCategory,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    track_changes,
    safe_int,
    safe_int_or_none,
    sort_with_override,
)

supply_items_bp = Blueprint('supply_items', __name__, url_prefix='/supply-items')


def _get_item_or_404(item_id: int) -> SupplyItem:
    """Get supply item by ID or abort with 404."""
    item = db.session.get(SupplyItem, item_id)
    if not item:
        abort(404, "Supply item not found")
    return item


def _get_form_context():
    """Get common form context data."""
    categories = (
        db.session.query(SupplyCategory)
        .filter(SupplyCategory.is_active == True)
        .order_by(*sort_with_override(SupplyCategory))
        .all()
    )
    return {
        "categories": categories,
    }


def _item_to_dict(item: SupplyItem) -> dict:
    """Convert supply item to dict for change tracking."""
    return {
        "category_id": item.category_id,
        "item_name": item.item_name,
        "unit": item.unit,
        "notes": item.notes,
        "image_url": item.image_url,
        "is_active": item.is_active,
        "is_limited": item.is_limited,
        "is_popular": item.is_popular,
        "is_expendable": item.is_expendable,
        "notes_required": item.notes_required,
        "internal_type": item.internal_type,
        "unit_cost_cents": item.unit_cost_cents,
        "qty_on_hand": item.qty_on_hand,
        "location_zone": item.location_zone,
        "bin_location": item.bin_location,
        "sort_order": item.sort_order,
    }


@supply_items_bp.get("/")
@require_super_admin
def list_supply_items():
    """List all supply items."""
    items = (
        db.session.query(SupplyItem)
        .join(SupplyCategory)
        .order_by(*sort_with_override(SupplyCategory), *sort_with_override(SupplyItem, name_attr=SupplyItem.item_name))
        .all()
    )

    # Group by category for display
    items_by_category = {}
    for item in items:
        cat_name = item.category.name
        if cat_name not in items_by_category:
            items_by_category[cat_name] = []
        items_by_category[cat_name].append(item)

    return render_admin_config_page(
        "admin/supply_items/list.html",
        items=items,
        items_by_category=items_by_category,
    )


@supply_items_bp.get("/new")
@require_super_admin
def new_supply_item():
    """Show form to create a new supply item."""
    return render_admin_config_page(
        "admin/supply_items/form.html",
        item=None,
        **_get_form_context(),
    )


@supply_items_bp.post("/new")
@require_super_admin
def create_supply_item():
    """Create a new supply item."""
    item_name = request.form.get("item_name", "").strip()
    category_id = safe_int_or_none(request.form.get("category_id"))
    unit = request.form.get("unit", "").strip()

    if not item_name or not category_id or not unit:
        flash("Item name, category, and unit are required.", "error")
        return redirect(url_for('.new_supply_item'))

    # Parse unit cost (in dollars, convert to cents)
    unit_cost = request.form.get("unit_cost", "").strip()
    unit_cost_cents = None
    if unit_cost:
        try:
            unit_cost_cents = int(Decimal(unit_cost) * 100)
        except (ValueError, InvalidOperation):
            flash("Invalid unit cost format.", "error")
            return redirect(url_for('.new_supply_item'))

    item = SupplyItem(
        category_id=category_id,
        item_name=item_name,
        unit=unit,
        notes=request.form.get("notes", "").strip() or None,
        image_url=request.form.get("image_url", "").strip() or None,
        is_active=bool(request.form.get("is_active")),
        is_limited=bool(request.form.get("is_limited")),
        is_popular=bool(request.form.get("is_popular")),
        is_expendable=bool(request.form.get("is_expendable")),
        notes_required=bool(request.form.get("notes_required")),
        internal_type=request.form.get("internal_type", "").strip() or None,
        unit_cost_cents=unit_cost_cents,
        qty_on_hand=safe_int_or_none(request.form.get("qty_on_hand")),
        location_zone=request.form.get("location_zone", "").strip() or None,
        bin_location=request.form.get("bin_location", "").strip() or None,
        sort_order=safe_int_or_none(request.form.get("sort_order")),
        created_by_user_id=h.get_active_user_id(),
    )
    db.session.add(item)
    db.session.flush()

    log_config_change("supply_item", item.id, CONFIG_AUDIT_CREATE, {}, _item_to_dict(item))
    db.session.commit()

    flash(f"Supply item '{item_name}' created.", "success")
    return redirect(url_for('.list_supply_items'))


@supply_items_bp.get("/<int:item_id>")
@require_super_admin
def edit_supply_item(item_id: int):
    """Show form to edit a supply item."""
    item = _get_item_or_404(item_id)
    return render_admin_config_page(
        "admin/supply_items/form.html",
        item=item,
        **_get_form_context(),
    )


@supply_items_bp.post("/<int:item_id>")
@require_super_admin
def update_supply_item(item_id: int):
    """Update a supply item."""
    item = _get_item_or_404(item_id)
    old_state = _item_to_dict(item)

    item_name = request.form.get("item_name", "").strip()
    category_id = safe_int_or_none(request.form.get("category_id"))
    unit = request.form.get("unit", "").strip()

    if not item_name or not category_id or not unit:
        flash("Item name, category, and unit are required.", "error")
        return redirect(url_for('.edit_supply_item', item_id=item_id))

    # Parse unit cost (in dollars, convert to cents)
    unit_cost = request.form.get("unit_cost", "").strip()
    unit_cost_cents = None
    if unit_cost:
        try:
            unit_cost_cents = int(Decimal(unit_cost) * 100)
        except (ValueError, InvalidOperation):
            flash("Invalid unit cost format.", "error")
            return redirect(url_for('.edit_supply_item', item_id=item_id))

    item.category_id = category_id
    item.item_name = item_name
    item.unit = unit
    item.notes = request.form.get("notes", "").strip() or None
    item.image_url = request.form.get("image_url", "").strip() or None
    item.is_active = bool(request.form.get("is_active"))
    item.is_limited = bool(request.form.get("is_limited"))
    item.is_popular = bool(request.form.get("is_popular"))
    item.is_expendable = bool(request.form.get("is_expendable"))
    item.notes_required = bool(request.form.get("notes_required"))
    item.internal_type = request.form.get("internal_type", "").strip() or None
    item.unit_cost_cents = unit_cost_cents
    item.qty_on_hand = safe_int_or_none(request.form.get("qty_on_hand"))
    item.location_zone = request.form.get("location_zone", "").strip() or None
    item.bin_location = request.form.get("bin_location", "").strip() or None
    item.sort_order = safe_int_or_none(request.form.get("sort_order"))
    item.updated_by_user_id = h.get_active_user_id()

    new_state = _item_to_dict(item)
    changes = track_changes(old_state, new_state)
    if changes:
        log_config_change("supply_item", item.id, CONFIG_AUDIT_UPDATE, old_state, new_state)

    db.session.commit()

    flash(f"Supply item '{item_name}' updated.", "success")
    return redirect(url_for('.list_supply_items'))
