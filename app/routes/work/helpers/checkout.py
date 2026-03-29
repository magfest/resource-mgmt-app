"""
Checkout/checkin functionality for work items.

Handles locking work items for review and building work item permissions.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app

from app import db
from app.models import (
    WorkItem,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_NEEDS_INFO,
)
from app.routes import UserContext
from app.line_details import get_line_routing_approval_group
from .context import (
    PortfolioContext,
    WorkItemPerms,
    build_portfolio_perms,
)


# ============================================================
# Checkout Configuration
# ============================================================

DEFAULT_CHECKOUT_TIMEOUTS = {
    "APPROVER": 30,      # 30 minutes for approvers
    "SUPER_ADMIN": 120,  # 2 hours for super admins
    "DEFAULT": 30,       # Default fallback
}


def get_checkout_timeouts() -> dict:
    """Get checkout timeouts from config or defaults."""
    return current_app.config.get('CHECKOUT_TIMEOUTS', DEFAULT_CHECKOUT_TIMEOUTS)


def get_checkout_timeout_minutes(user_ctx: UserContext) -> int:
    """Get checkout timeout in minutes based on user role (respects role override)."""
    timeouts = get_checkout_timeouts()
    if user_ctx.is_super_admin:
        return timeouts.get("SUPER_ADMIN", 120)
    if user_ctx.approval_group_ids:
        return timeouts.get("APPROVER", 30)
    return timeouts.get("DEFAULT", 30)


# ============================================================
# Checkout Status Functions
# ============================================================

def is_checked_out(work_item: WorkItem) -> bool:
    """Check if a work item is currently checked out (lock not expired)."""
    if not work_item.checked_out_by_user_id:
        return False
    if not work_item.checked_out_expires_at:
        return False
    return work_item.checked_out_expires_at > datetime.utcnow()


def get_checkout_info(work_item: WorkItem) -> dict | None:
    """
    Get checkout information for a work item.

    Returns dict with:
        - user_id: Who has checkout
        - checked_out_at: When checkout started
        - expires_at: When checkout expires
        - is_expired: Whether checkout has expired

    Returns None if not checked out.
    """
    if not work_item.checked_out_by_user_id:
        return None

    is_expired = (
        work_item.checked_out_expires_at is None or
        work_item.checked_out_expires_at <= datetime.utcnow()
    )

    return {
        "user_id": work_item.checked_out_by_user_id,
        "checked_out_at": work_item.checked_out_at,
        "expires_at": work_item.checked_out_expires_at,
        "is_expired": is_expired,
    }


# ============================================================
# Checkout/Checkin Operations
# ============================================================

def can_checkout(work_item: WorkItem, user_ctx: UserContext) -> tuple[bool, str]:
    """
    Check if user can checkout a work item.

    Returns (can_checkout, reason) tuple.
    """
    # Must be SUBMITTED status
    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        return False, "Only SUBMITTED requests can be checked out for review."

    # Must be a reviewer (admin or approver) - respects role override
    if not user_ctx.is_super_admin and not user_ctx.approval_group_ids:
        return False, "Only reviewers can checkout work items."

    # Cannot checkout if already checked out (unless expired)
    if is_checked_out(work_item):
        if work_item.checked_out_by_user_id == user_ctx.user_id:
            return False, "You already have this item checked out."
        return False, "This item is already checked out by another reviewer."

    return True, "OK"


def checkout_work_item(work_item: WorkItem, user_ctx: UserContext) -> bool:
    """
    Checkout a work item for review.

    Returns True if checkout successful, False otherwise.
    """
    # Lock the work item row to prevent concurrent checkout
    locked_item = db.session.query(WorkItem).with_for_update().get(work_item.id)

    can_do, _reason = can_checkout(locked_item, user_ctx)
    if not can_do:
        return False

    timeout_minutes = get_checkout_timeout_minutes(user_ctx)
    now = datetime.utcnow()

    locked_item.checked_out_by_user_id = user_ctx.user_id
    locked_item.checked_out_at = now
    locked_item.checked_out_expires_at = now + timedelta(minutes=timeout_minutes)

    return True


def checkin_work_item(work_item: WorkItem, user_ctx: UserContext, force: bool = False) -> bool:
    """
    Release checkout on a work item.

    Args:
        work_item: The work item to release
        user_ctx: Current user context
        force: If True, admin can force release any checkout

    Returns True if checkin successful, False otherwise.
    """
    # Lock the work item row to prevent concurrent checkin
    locked_item = db.session.query(WorkItem).with_for_update().get(work_item.id)

    if not locked_item.checked_out_by_user_id:
        return False  # Nothing to release

    # Check permission
    is_current_holder = locked_item.checked_out_by_user_id == user_ctx.user_id
    is_admin = user_ctx.is_super_admin

    if not is_current_holder and not (is_admin and force):
        return False

    locked_item.checked_out_by_user_id = None
    locked_item.checked_out_at = None
    locked_item.checked_out_expires_at = None

    return True


def release_expired_checkouts() -> int:
    """
    Release all expired checkouts.

    Returns count of checkouts released.
    """
    now = datetime.utcnow()
    expired_items = WorkItem.query.filter(
        WorkItem.checked_out_by_user_id.isnot(None),
        WorkItem.checked_out_expires_at <= now,
    ).all()

    count = 0
    for item in expired_items:
        item.checked_out_by_user_id = None
        item.checked_out_at = None
        item.checked_out_expires_at = None
        count += 1

    return count


# ============================================================
# Work Item Permission Building
# ============================================================

def _is_approver_for_work_item(work_item: WorkItem, user_ctx: UserContext) -> bool:
    """
    Check if user is an approver who can review this work item.

    Returns True if user has approval group access and is in an approval group
    that has lines routed to it in this work item. Respects role override.
    Works with any line detail type (budget, contract, supply).
    """
    # Check approval group IDs (already respects role override)
    if not user_ctx.approval_group_ids:
        return False

    # Check if any lines in this work item are routed to user's approval groups
    for line in work_item.lines:
        routed_group = get_line_routing_approval_group(line)
        if routed_group and routed_group.id in user_ctx.approval_group_ids:
            return True

    return False


def build_work_item_perms(item: WorkItem, ctx: PortfolioContext) -> WorkItemPerms:
    """Build permission flags for a work item."""
    portfolio_perms = build_portfolio_perms(ctx)

    is_draft = item.status == WORK_ITEM_STATUS_DRAFT
    is_submitted = item.status == WORK_ITEM_STATUS_SUBMITTED
    is_needs_info = item.status == WORK_ITEM_STATUS_NEEDS_INFO

    # Check if user is a reviewer (admin or approver for lines in this item)
    is_approver_for_item = _is_approver_for_work_item(item, ctx.user_ctx)
    is_reviewer = portfolio_perms.is_worktype_admin or is_approver_for_item

    # View: portfolio can_view OR is a reviewer for this item
    can_view = portfolio_perms.can_view or is_reviewer

    # Edit: (admin or membership.can_edit) AND status == DRAFT
    can_edit = portfolio_perms.can_edit and is_draft

    # Has lines?
    has_lines = len(item.lines) > 0

    # Submit: can_edit AND has_lines
    can_submit = can_edit and has_lines

    # Add lines: can_edit (implies DRAFT only)
    can_add_lines = can_edit

    # Delete: can_edit but PRIMARY is never deletable
    can_delete = can_edit and (item.request_kind != REQUEST_KIND_PRIMARY)

    # Checkout permissions
    item_is_checked_out = is_checked_out(item)
    is_checked_out_by_current_user = (
        item_is_checked_out and
        item.checked_out_by_user_id == ctx.user_ctx.user_id
    )

    # Can checkout: is a reviewer AND item is SUBMITTED AND not already checked out
    can_checkout_item = is_reviewer and is_submitted and not item_is_checked_out

    # Can checkin: current user has checkout OR admin can force release
    can_checkin_item = is_checked_out_by_current_user or (portfolio_perms.is_worktype_admin and item_is_checked_out)

    # Can request info: has checkout on item (current user)
    can_request_info = is_checked_out_by_current_user and is_submitted

    # Can respond to info: is requester/editor AND status is NEEDS_INFO
    can_respond_to_info = portfolio_perms.can_edit and is_needs_info

    return WorkItemPerms(
        can_view=can_view,
        can_edit=can_edit,
        can_submit=can_submit,
        can_add_lines=can_add_lines,
        can_delete=can_delete,
        can_checkout=can_checkout_item,
        can_checkin=can_checkin_item,
        can_request_info=can_request_info,
        can_respond_to_info=can_respond_to_info,
        is_worktype_admin=portfolio_perms.is_worktype_admin,
        is_draft=is_draft,
        is_checked_out=item_is_checked_out,
        is_checked_out_by_current_user=is_checked_out_by_current_user,
    )
