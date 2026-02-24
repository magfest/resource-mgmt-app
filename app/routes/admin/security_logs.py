"""
Admin routes for viewing security audit logs.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from flask import Blueprint, request

from app import db
from app.models import SecurityAuditLog, User
from app.security_audit import (
    CATEGORY_AUTH,
    CATEGORY_ADMIN,
    CATEGORY_ACCESS,
    CATEGORY_SECURITY,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SEVERITY_ALERT,
)
from .helpers import require_super_admin, render_admin_config_page

security_logs_bp = Blueprint("security_logs", __name__, url_prefix="/admin/security-logs")

# Constants
RECORDS_PER_PAGE = 50

# Available filter options
CATEGORIES = [
    (CATEGORY_AUTH, "Authentication"),
    (CATEGORY_ADMIN, "Admin Actions"),
    (CATEGORY_ACCESS, "Access Control"),
    (CATEGORY_SECURITY, "Security Events"),
]

SEVERITIES = [
    (SEVERITY_INFO, "Info"),
    (SEVERITY_WARNING, "Warning"),
    (SEVERITY_ALERT, "Alert"),
]

# Display labels for categories and severities (colors are in CSS)
SEVERITY_LABELS = {
    SEVERITY_INFO: "Info",
    SEVERITY_WARNING: "Warning",
    SEVERITY_ALERT: "Alert",
}

CATEGORY_LABELS = {
    CATEGORY_AUTH: "Auth",
    CATEGORY_ADMIN: "Admin",
    CATEGORY_ACCESS: "Access",
    CATEGORY_SECURITY: "Security",
}


def _get_event_types():
    """Get distinct event types from the database."""
    results = (
        db.session.query(SecurityAuditLog.event_type)
        .distinct()
        .order_by(SecurityAuditLog.event_type)
        .all()
    )
    return [(r[0], r[0].replace("_", " ").title()) for r in results]


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse a date string in YYYY-MM-DD format."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d")
    except ValueError:
        return None


@security_logs_bp.get("/")
@require_super_admin
def list_logs():
    """List security audit logs with filtering and pagination."""
    # Get filter params
    category = request.args.get("category", "").strip()
    event_type = request.args.get("event_type", "").strip()
    severity = request.args.get("severity", "").strip()
    user_id = request.args.get("user_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    page = max(1, int(request.args.get("page", 1) or 1))

    # Build query
    query = db.session.query(SecurityAuditLog)

    # Apply filters
    if category:
        query = query.filter(SecurityAuditLog.event_category == category)
    if event_type:
        query = query.filter(SecurityAuditLog.event_type == event_type)
    if severity:
        query = query.filter(SecurityAuditLog.severity == severity)
    if user_id:
        query = query.filter(SecurityAuditLog.user_id.ilike(f"%{user_id}%"))

    date_from_dt = _parse_date(date_from)
    date_to_dt = _parse_date(date_to)
    if date_from_dt:
        query = query.filter(SecurityAuditLog.timestamp >= date_from_dt)
    if date_to_dt:
        # Include the entire day
        query = query.filter(SecurityAuditLog.timestamp < date_to_dt + timedelta(days=1))

    # Get total count before pagination
    total_count = query.count()

    # Sort by newest first and paginate
    query = query.order_by(SecurityAuditLog.timestamp.desc())
    offset = (page - 1) * RECORDS_PER_PAGE
    logs = query.offset(offset).limit(RECORDS_PER_PAGE).all()

    # Calculate pagination
    total_pages = (total_count + RECORDS_PER_PAGE - 1) // RECORDS_PER_PAGE
    start_record = offset + 1 if total_count > 0 else 0
    end_record = min(offset + RECORDS_PER_PAGE, total_count)

    # Get user emails for display
    user_ids = {log.user_id for log in logs if log.user_id}
    users = {}
    if user_ids:
        user_records = db.session.query(User).filter(User.id.in_(user_ids)).all()
        users = {u.id: u for u in user_records}

    # Get available event types for dropdown
    event_types = _get_event_types()

    return render_admin_config_page(
        "admin/security_logs/list.html",
        logs=logs,
        users=users,
        # Filter values
        categories=CATEGORIES,
        severities=SEVERITIES,
        event_types=event_types,
        selected_category=category,
        selected_event_type=event_type,
        selected_severity=severity,
        selected_user_id=user_id,
        selected_date_from=date_from,
        selected_date_to=date_to,
        # Display helpers (colors are in template CSS)
        severity_labels=SEVERITY_LABELS,
        category_labels=CATEGORY_LABELS,
        # Pagination
        page=page,
        total_pages=total_pages,
        total_count=total_count,
        start_record=start_record,
        end_record=end_record,
        records_per_page=RECORDS_PER_PAGE,
    )
