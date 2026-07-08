"""
Admin routes for supply item (catalog) management.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import Blueprint, redirect, url_for, request, abort, flash, send_file
from openpyxl import Workbook

from app import db
from app.models import (
    SupplyItem,
    SupplyCategory,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
)
from app.routes import h
from .helpers import (
    require_supply_admin,
    render_supply_admin_page,
    log_config_change,
    track_changes,
    safe_int,
    safe_int_or_none,
    sort_with_override,
    validate_upload_file,
)
from .supply_import_utils import (
    EXPECTED_COLUMNS,
    ImportParseError,
    apply_import,
    classify_rows,
    parse_catalog_upload,
)

supply_items_bp = Blueprint('supply_items', __name__, url_prefix='/supply-items')

# One example row for the downloadable template, in EXPECTED_COLUMNS order.
_TEMPLATE_EXAMPLE_ROW = [
    "OFFICE", "Ballpoint Pens (Box of 12)", "box", "Standard blue ink pens",
    "1 box lasts about a month for a 10-person team",
    "3.50", "50", "A1", "B2", "false", "true", "true", "false", "true",
]


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
        "order_guidance": item.order_guidance,
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
@require_supply_admin
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

    return render_supply_admin_page(
        "admin/supply_items/list.html",
        items=items,
        items_by_category=items_by_category,
    )


@supply_items_bp.get("/new")
@require_supply_admin
def new_supply_item():
    """Show form to create a new supply item."""
    return render_supply_admin_page(
        "admin/supply_items/form.html",
        item=None,
        **_get_form_context(),
    )


@supply_items_bp.post("/new")
@require_supply_admin
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
        order_guidance=request.form.get("order_guidance", "").strip() or None,
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

    # Image handling — lazy import so admin blueprint import order stays inert
    from app.services.images import (
        process_and_upload_item_image, delete_item_image,
        ImageValidationError, ImageStorageError,
    )

    old_image_url = item.image_url
    image_file = request.files.get("image_file")
    if request.form.get("remove_image"):
        if old_image_url:
            delete_item_image(old_image_url)
        item.image_url = None
    elif image_file and image_file.filename:
        try:
            item.image_url = process_and_upload_item_image(image_file, item.id)
            if old_image_url and old_image_url != item.image_url:
                delete_item_image(old_image_url)
        except (ImageValidationError, ImageStorageError) as exc:
            flash(f"Image not saved: {exc}", "error")
            # Item itself still saves — images are never load-bearing.

    log_config_change("supply_item", item.id, CONFIG_AUDIT_CREATE, _item_to_dict(item))
    db.session.commit()

    flash(f"Supply item '{item_name}' created.", "success")
    return redirect(url_for('.list_supply_items'))


@supply_items_bp.get("/<int:item_id>")
@require_supply_admin
def edit_supply_item(item_id: int):
    """Show form to edit a supply item."""
    item = _get_item_or_404(item_id)
    return render_supply_admin_page(
        "admin/supply_items/form.html",
        item=item,
        **_get_form_context(),
    )


@supply_items_bp.post("/<int:item_id>")
@require_supply_admin
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
    item.order_guidance = request.form.get("order_guidance", "").strip() or None
    # image_url is managed exclusively by the photo upload / remove handling
    # below — there is no direct form input for it.
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

    # Image handling — lazy import so admin blueprint import order stays inert
    from app.services.images import (
        process_and_upload_item_image, delete_item_image,
        ImageValidationError, ImageStorageError,
    )

    old_image_url = item.image_url
    image_file = request.files.get("image_file")
    if request.form.get("remove_image"):
        if old_image_url:
            delete_item_image(old_image_url)
        item.image_url = None
    elif image_file and image_file.filename:
        try:
            item.image_url = process_and_upload_item_image(image_file, item.id)
            if old_image_url and old_image_url != item.image_url:
                delete_item_image(old_image_url)
        except (ImageValidationError, ImageStorageError) as exc:
            flash(f"Image not saved: {exc}", "error")
            # Item itself still saves — images are never load-bearing.

    new_state = _item_to_dict(item)
    changes = track_changes(old_state, new_state)
    if changes:
        log_config_change("supply_item", item.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()

    flash(f"Supply item '{item_name}' updated.", "success")
    return redirect(url_for('.list_supply_items'))


# ============================================================
# Bulk import: upload -> preview -> confirm, plus template download
# ============================================================

@supply_items_bp.get("/import")
@require_supply_admin
def import_form():
    """Show the catalog upload form."""
    return render_supply_admin_page(
        "admin/supply_items/import.html",
        expected_columns=EXPECTED_COLUMNS,
    )


@supply_items_bp.post("/import")
@require_supply_admin
def import_upload():
    """Parse an uploaded catalog file and show a create/update/error preview.

    Nothing is written to the DB here -- only on /import/confirm. The raw
    parsed rows are round-tripped to the confirm step via a hidden form
    field so confirm can re-classify them against current DB state (see
    import_confirm for why).
    """
    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for('.import_form'))

    if not validate_upload_file(file):
        return redirect(url_for('.import_form'))

    try:
        rows = parse_catalog_upload(file)
    except ImportParseError:
        # ImportParseError may wrap a raw pandas exception message -- never
        # surface that to the page. The parser's own per-row problems
        # (surfaced via classify_rows -> preview.errors) are safe to show
        # and are rendered on the preview page instead.
        flash(
            "Could not read the uploaded file. Make sure it's a CSV or "
            "Excel file with the required columns "
            "(category_code, item_name, unit).",
            "error",
        )
        return redirect(url_for('.import_form'))

    preview = classify_rows(rows)
    payload_json = json.dumps(rows)
    categories_by_id = {c.id: c for c in db.session.query(SupplyCategory).all()}

    return render_supply_admin_page(
        "admin/supply_items/import_preview.html",
        creates=preview.creates,
        updates=preview.updates,
        errors=preview.errors,
        payload_json=payload_json,
        categories_by_id=categories_by_id,
    )


@supply_items_bp.post("/import/confirm")
@require_supply_admin
def import_confirm():
    """Apply a previously-previewed import.

    Re-runs classify_rows on the payload rows before applying -- this is a
    defense against a stale preview (e.g. a category was deleted, or an item
    was added/renamed, between the preview and the confirm click). Heroku
    dynos share no filesystem, so the payload is carried as form data rather
    than a temp file. Only clean rows (creates/updates) are applied; rows
    that error on re-classification are skipped and reported.
    """
    payload = request.form.get("payload", "")
    try:
        rows = json.loads(payload) if payload else []
    except (TypeError, ValueError):
        flash("Import payload was invalid or corrupted. Please re-upload the file.", "error")
        return redirect(url_for('.import_form'))

    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        flash("Import payload was invalid or corrupted. Please re-upload the file.", "error")
        return redirect(url_for('.import_form'))

    preview = classify_rows(rows)

    created_count, updated_count = apply_import(
        preview.creates, preview.updates, h.get_active_user_id()
    )
    db.session.commit()

    message = f"Import complete: {created_count} created, {updated_count} updated."
    if preview.errors:
        message += f" {len(preview.errors)} row(s) skipped due to errors."
    flash(message, "success")
    return redirect(url_for('.list_supply_items'))


@supply_items_bp.get("/import/template")
@require_supply_admin
def download_import_template():
    """Download an .xlsx template with the expected columns + one example row."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(EXPECTED_COLUMNS)
    sheet.append(_TEMPLATE_EXAMPLE_ROW)

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="supply_catalog_template.xlsx",
    )
