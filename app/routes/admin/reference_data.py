"""
Backend reference data editor.

This is a super-admin tool for editing simple lookup tables that rarely change.
Intentionally styled differently from regular admin pages to make it clear
this is a backend tool.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, flash

from app import db
from app.models import (
    SpendType,
    FrequencyOption,
    ConfidenceLevel,
    PriorityLevel,
    CONFIG_AUDIT_UPDATE,
)
from app.routes import h
from .helpers import require_super_admin, render_admin_config_page, log_config_change, track_changes, safe_int


reference_data_bp = Blueprint('reference_data', __name__, url_prefix='/reference-data')

# Registry of editable reference data tables
# Easy to extend: just add new entries here
REFERENCE_TABLES = {
    "spend_types": {
        "model": SpendType,
        "label": "Spend Types",
        "description": "Payment methods (Divvy, Bank, etc.)",
        "audit_key": "spend_type",
    },
    "frequency_options": {
        "model": FrequencyOption,
        "label": "Frequency Options",
        "description": "Budget frequency choices (One-time, Recurring, etc.)",
        "audit_key": "frequency_option",
    },
    "confidence_levels": {
        "model": ConfidenceLevel,
        "label": "Confidence Levels",
        "description": "Cost estimate confidence (Confirmed, Estimated, Placeholder)",
        "audit_key": "confidence_level",
    },
    "priority_levels": {
        "model": PriorityLevel,
        "label": "Priority Levels",
        "description": "Request priority ratings (Critical, High, Medium, Low)",
        "audit_key": "priority_level",
    },
}


def _record_to_dict(record) -> dict:
    """Convert a reference record to dict for change tracking."""
    return {
        "code": record.code,
        "name": record.name,
        "description": record.description,
        "is_active": record.is_active,
        "sort_order": record.sort_order,
    }


@reference_data_bp.get("/")
@require_super_admin
def index():
    """Show all reference data tables."""
    tables_data = {}

    for table_key, table_info in REFERENCE_TABLES.items():
        model = table_info["model"]
        records = (
            db.session.query(model)
            .order_by(model.sort_order, model.name)
            .all()
        )
        tables_data[table_key] = {
            **table_info,
            "records": records,
        }

    return render_admin_config_page(
        "admin/reference_data.html",
        tables=tables_data,
    )


@reference_data_bp.post("/<table_key>/<int:record_id>")
@require_super_admin
def update_record(table_key: str, record_id: int):
    """Update a single reference data record."""
    if table_key not in REFERENCE_TABLES:
        flash(f"Unknown table: {table_key}", "error")
        return redirect(url_for(".index"))

    table_info = REFERENCE_TABLES[table_key]
    model = table_info["model"]

    record = db.session.get(model, record_id)
    if not record:
        flash("Record not found", "error")
        return redirect(url_for(".index"))

    # Track old values
    old_values = _record_to_dict(record)

    # Update fields (code is read-only, never changes)
    record.name = (request.form.get("name") or "").strip()
    record.description = (request.form.get("description") or "").strip() or None
    record.is_active = request.form.get("is_active") == "1"
    record.sort_order = safe_int(request.form.get("sort_order"))
    record.updated_by_user_id = h.get_active_user_id()

    # Validate
    if not record.name:
        flash("Name is required", "error")
        return redirect(url_for(".index"))

    # Track and log changes
    new_values = _record_to_dict(record)
    changes = track_changes(old_values, new_values)

    if changes:
        log_config_change(
            table_info["audit_key"],
            record.id,
            CONFIG_AUDIT_UPDATE,
            changes,
        )
        db.session.commit()
        flash(f"Updated {table_info['label']}: {record.name}", "success")
    else:
        flash("No changes detected", "info")

    return redirect(url_for(".index") + f"#{table_key}")
