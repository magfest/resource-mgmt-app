"""
Admin routes for supply item (catalog) management.
"""
from __future__ import annotations

import json
from io import BytesIO

from flask import Blueprint, redirect, url_for, request, abort, flash, send_file
from openpyxl import Workbook

from app import db
from app.models import (
    SupplyItem,
    SupplyCategory,
)
from app.routes import h
from .helpers import (
    require_supply_admin,
    render_supply_admin_page,
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
    "3.50", "50", "A1", "B2", "false", "true", "true", "false", "true", "true",
]


def _get_item_or_404(item_id: int) -> SupplyItem:
    """Get supply item by ID or abort with 404."""
    item = db.session.get(SupplyItem, item_id)
    if not item:
        abort(404, "Supply item not found")
    return item


@supply_items_bp.get("/")
@require_supply_admin
def list_supply_items():
    """List supply items; ?orderable=0 shows only models, ?orderable=1 only orderable items."""
    orderable = request.args.get("orderable")
    query = db.session.query(SupplyItem).join(SupplyCategory)
    if orderable == "0":
        query = query.filter(SupplyItem.is_orderable.is_(False))
    elif orderable == "1":
        query = query.filter(SupplyItem.is_orderable.is_(True))
    else:
        orderable = None
    items = (
        query
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
        orderable_filter=orderable,
    )


@supply_items_bp.get("/new")
@require_supply_admin
def new_supply_item():
    """Send the old new-item form to the Add an item form on the Items list."""
    return redirect(url_for("inventory.items", _anchor="add-item"))


@supply_items_bp.get("/<int:item_id>")
@require_supply_admin
def edit_supply_item(item_id: int):
    """Send the old edit form to the item page."""
    _get_item_or_404(item_id)
    return redirect(url_for("inventory.item_page", item_id=item_id))


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
