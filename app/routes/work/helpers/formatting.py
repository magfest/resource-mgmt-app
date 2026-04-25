"""
Formatting and utility helpers.

Status labels, currency formatting, public ID generation, line filtering, and misc utilities.
"""
from __future__ import annotations

import secrets

from app import db
from app.models import (
    WorkType,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkItemAuditEvent,
    WorkLineAuditEvent,
    User,
    COMMENT_VISIBILITY_ADMIN,
    COMMENT_VISIBILITY_PUBLIC,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
)
from app.routes import UserContext


# ============================================================
# Public ID Generation
# ============================================================

def generate_public_id(prefix: str = "BUD") -> str:
    """Generate unique public ID like BUD-A3F9K2."""
    while True:
        # Generate 6 random alphanumeric characters
        random_part = secrets.token_urlsafe(4).upper().replace("-", "").replace("_", "")[:6]
        candidate = f"{prefix}-{random_part}"

        # Check for uniqueness
        exists = db.session.query(WorkItem.id).filter_by(public_id=candidate).first()
        if not exists:
            return candidate


def generate_public_id_for_work_type(work_type: WorkType) -> str:
    """
    Generate a public ID using the work type's configured prefix.

    Args:
        work_type: The work type to generate an ID for

    Returns:
        A unique public ID like "BUD-A3F9K2" or "CON-X7Y8Z9"
    """
    prefix = "REQ"  # Default fallback
    if work_type.config:
        prefix = work_type.config.public_id_prefix
    return generate_public_id(prefix)


def generate_public_id_for_portfolio(portfolio) -> str:
    """
    Generate a meaningful public ID like SMF27-TECHOPS-BUD-1.

    Format: {EVENT_CODE}-{DEPT_CODE}-{WORKTYPE_PREFIX}-{SEQ}
    Sequence is shared across PRIMARY and SUPPLEMENTARY requests within
    the same portfolio.

    Args:
        portfolio: The WorkPortfolio to generate an ID for

    Returns:
        A deterministic public ID like "SMF27-TECHOPS-BUD-1"
    """
    event_code = portfolio.event_cycle.code
    dept_code = portfolio.department.code

    # Get work type prefix from config, fallback to "REQ"
    work_type_prefix = "REQ"
    if portfolio.work_type and portfolio.work_type.config:
        work_type_prefix = portfolio.work_type.config.public_id_prefix

    # Lock the portfolio row for atomic sequence increment
    locked_portfolio = db.session.query(WorkPortfolio).with_for_update().get(portfolio.id)
    seq = locked_portfolio.next_public_id_seq or 1
    locked_portfolio.next_public_id_seq = seq + 1

    return f"{event_code}-{dept_code}-{work_type_prefix}-{seq}"


# ============================================================
# Formatting Helpers
# ============================================================

def format_currency(cents: int) -> str:
    """Format cents as currency string."""
    dollars = cents / 100
    return f"${dollars:,.2f}"


# User-friendly status label mapping
STATUS_LABELS = {
    "DRAFT": "Draft",
    "AWAITING_DISPATCH": "Pending Review",
    "SUBMITTED": "Under Review",
    "UNDER_REVIEW": "Under Review",
    "NEEDS_INFO": "Info Requested",
    "NEEDS_ADJUSTMENT": "Changes Requested",
    "NEEDS_RESPONSE": "Response Needed",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
    "FINALIZED": "Finalized",
    "PAUSED": "Paused",
    "PENDING": "Pending",
}


def friendly_status(status: str) -> str:
    """Convert a status code to a user-friendly label."""
    if not status:
        return ""
    return STATUS_LABELS.get(status.upper(), status)


# ============================================================
# Comment Visibility
# ============================================================

def get_comment_visibility(form_data, is_worktype_admin: bool) -> str:
    """
    Determine comment visibility based on form input and user permissions.

    Args:
        form_data: Flask request.form or similar dict-like object
        is_worktype_admin: Whether the current user is a worktype admin

    Returns:
        COMMENT_VISIBILITY_ADMIN if admin requested admin-only and is admin,
        otherwise COMMENT_VISIBILITY_PUBLIC
    """
    admin_only_requested = form_data.get("admin_only") == "1"
    if admin_only_requested and is_worktype_admin:
        return COMMENT_VISIBILITY_ADMIN
    return COMMENT_VISIBILITY_PUBLIC


def get_next_line_number(work_item: WorkItem) -> int:
    """Get the next available line number for a work item."""
    if not work_item.lines:
        return 1

    max_num = max(line.line_number for line in work_item.lines)
    return max_num + 1


# ============================================================
# Line Filtering for Approval Group Access
# ============================================================

def filter_lines_for_user(
    lines: list,
    user_ctx: UserContext,
    is_worktype_admin: bool,
    has_edit_access: bool = False,
) -> tuple[list, bool]:
    """
    Filter lines based on user's approval group access.

    Args:
        lines: List of WorkLine objects
        user_ctx: Current user context
        is_worktype_admin: Whether user is a worktype admin
        has_edit_access: Whether user has edit access (requester/dept member)

    Returns:
        (visible_lines, was_filtered) tuple where:
        - visible_lines: Lines the user can see
        - was_filtered: True if some lines were hidden
    """
    all_lines = list(lines)

    # Worktype admins and requesters see all lines
    if is_worktype_admin or has_edit_access:
        return all_lines, False

    # Non-admin approval group users see only their routed lines
    if user_ctx.approval_group_ids:
        visible = [
            line for line in all_lines
            if line.budget_detail and
               line.budget_detail.routed_approval_group_id in user_ctx.approval_group_ids
        ]
        return visible, len(visible) != len(all_lines)

    # Users with no approval groups see all lines (shouldn't reach here normally)
    return all_lines, False


# ============================================================
# Work Item Detail Helpers
# ============================================================

def get_kicked_back_lines_summary(lines: list) -> list[dict]:
    """
    Get summary of kicked-back lines (NEEDS_INFO or NEEDS_ADJUSTMENT) with review notes.

    Uses batch loading to avoid N+1 queries.

    Args:
        lines: List of WorkLine objects to check

    Returns:
        List of dicts with line_number, status, detail, and note
    """
    # Filter to kicked-back lines first
    kicked_back = [
        line for line in lines
        if line.status in (WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT)
    ]

    if not kicked_back:
        return []

    # Batch load the most recent review for each line
    line_ids = [line.id for line in kicked_back]

    # Get all reviews for these lines, ordered by decided_at desc
    all_reviews = (
        WorkLineReview.query
        .filter(WorkLineReview.work_line_id.in_(line_ids))
        .order_by(WorkLineReview.decided_at.desc())
        .all()
    )

    # Build a map of line_id -> most recent review (first one we see due to ordering)
    review_by_line = {}
    for review in all_reviews:
        if review.work_line_id not in review_by_line:
            review_by_line[review.work_line_id] = review

    # Build the summary
    result = []
    for line in kicked_back:
        review = review_by_line.get(line.id)
        result.append({
            "line_number": line.line_number,
            "status": line.status,
            "detail": line.budget_detail,
            "note": review.note if review else None,
        })

    return result


def get_unified_audit_events(work_item: WorkItem) -> list[dict]:
    """
    Get unified audit log combining work item and line level events.

    Combines WorkItemAuditEvent and WorkLineAuditEvent into a single
    chronological list with user display names resolved.

    Args:
        work_item: The work item to get audit events for

    Returns:
        List of event dicts sorted by created_at descending, with keys:
        - created_at: datetime
        - event_type: str
        - created_by_user_id: str
        - old_value: str or None
        - new_value: str or None
        - reason: str or None
        - snapshot: dict or None
        - line_number: int or None (None for work item level)
        - is_line_event: bool
        - _user_display_name: str
    """
    # Get work item level events
    item_events = (
        WorkItemAuditEvent.query
        .filter_by(work_item_id=work_item.id)
        .all()
    )

    # Get line level events for all lines in this work item
    line_ids = [line.id for line in work_item.lines]
    line_events = []
    if line_ids:
        line_events = (
            WorkLineAuditEvent.query
            .filter(WorkLineAuditEvent.work_line_id.in_(line_ids))
            .all()
        )

    # Build line number lookup for line events
    line_number_map = {line.id: line.line_number for line in work_item.lines}

    # Normalize events into a unified format
    unified_events = []

    for e in item_events:
        unified_events.append({
            "created_at": e.created_at,
            "event_type": e.event_type,
            "created_by_user_id": e.created_by_user_id,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "reason": e.reason,
            "snapshot": e.snapshot,
            "line_number": None,  # Work item level
            "is_line_event": False,
        })

    for e in line_events:
        unified_events.append({
            "created_at": e.created_at,
            "event_type": e.event_type,
            "created_by_user_id": e.created_by_user_id,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "reason": e.note,  # Line events use 'note' field
            "snapshot": None,
            "field_name": e.field_name,
            "line_number": line_number_map.get(e.work_line_id),
            "is_line_event": True,
        })

    # Sort by timestamp descending
    unified_events.sort(key=lambda x: x["created_at"], reverse=True)

    # Batch load user display names
    user_ids = {e["created_by_user_id"] for e in unified_events if e["created_by_user_id"]}
    user_map = {}
    if user_ids:
        users = User.query.filter(User.id.in_(user_ids)).all()
        user_map = {u.id: u.display_name or u.email for u in users}

    for event in unified_events:
        event["_user_display_name"] = user_map.get(event["created_by_user_id"], str(event["created_by_user_id"]))

    return unified_events
