"""
Budget routes helpers - context, permissions, and utility functions.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Set

from flask import abort
from sqlalchemy import or_

from app import db
from app.models import (
    EventCycle,
    Department,
    DepartmentMembership,
    WorkType,
    WorkPortfolio,
    WorkItem,
    WorkLine,
    BudgetLineDetail,
    ExpenseAccount,
    SpendType,
    ConfidenceLevel,
    FrequencyOption,
    PriorityLevel,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_APPROVED,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROLE_APPROVER,
)
from app.routes import get_user_ctx, UserContext


# ============================================================
# Checkout Configuration
# ============================================================

CHECKOUT_TIMEOUTS = {
    "APPROVER": 30,      # 30 minutes for approvers
    "SUPER_ADMIN": 120,  # 2 hours for super admins
    "DEFAULT": 30,       # Default fallback
}


# ============================================================
# Context and Permission Dataclasses
# ============================================================

@dataclass(frozen=True)
class PortfolioContext:
    """Context for a portfolio view/action."""
    event_cycle: EventCycle
    department: Department
    portfolio: WorkPortfolio
    work_type: WorkType
    user_ctx: UserContext
    membership: DepartmentMembership | None


@dataclass(frozen=True)
class PortfolioPerms:
    """Permission flags for portfolio-level actions."""
    can_view: bool
    can_edit: bool
    can_create_primary: bool
    can_create_supplementary: bool
    is_admin: bool


@dataclass(frozen=True)
class WorkItemPerms:
    """Permission flags for work item actions."""
    can_view: bool
    can_edit: bool
    can_submit: bool
    can_add_lines: bool
    can_delete: bool
    can_checkout: bool
    can_checkin: bool
    can_request_info: bool
    can_respond_to_info: bool
    is_admin: bool
    is_draft: bool
    is_checked_out: bool
    is_checked_out_by_current_user: bool


# ============================================================
# Context Building Functions
# ============================================================

def get_budget_work_type() -> WorkType:
    """Get or create the BUDGET work type."""
    work_type = WorkType.query.filter_by(code="BUDGET").first()
    if not work_type:
        work_type = WorkType(
            code="BUDGET",
            name="Budget",
            is_active=True,
            sort_order=0,
        )
        db.session.add(work_type)
        db.session.flush()
    return work_type


def get_portfolio_context(event_code: str, dept_code: str) -> PortfolioContext:
    """
    Build context for a portfolio view.

    Looks up event cycle and department by their codes.
    Creates the portfolio if it doesn't exist.
    Returns PortfolioContext with all needed objects.
    """
    user_ctx = get_user_ctx()

    # Look up event cycle
    event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event_code}")

    # Look up department
    department = Department.query.filter_by(code=dept_code.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept_code}")

    # Get work type
    work_type = get_budget_work_type()

    # Get or create portfolio
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=work_type.id,
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        is_archived=False,
    ).first()

    if not portfolio:
        portfolio = WorkPortfolio(
            work_type_id=work_type.id,
            event_cycle_id=event_cycle.id,
            department_id=department.id,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(portfolio)
        db.session.flush()

    # Get user's membership for this department/cycle
    membership = DepartmentMembership.query.filter_by(
        user_id=user_ctx.user_id,
        department_id=department.id,
        event_cycle_id=event_cycle.id,
    ).first()

    return PortfolioContext(
        event_cycle=event_cycle,
        department=department,
        portfolio=portfolio,
        work_type=work_type,
        user_ctx=user_ctx,
        membership=membership,
    )


# ============================================================
# Permission Building Functions
# ============================================================

def is_budget_admin(user_ctx: UserContext, work_type_id: int | None = None) -> bool:
    """Check if user is a budget admin (SUPER_ADMIN or WORKTYPE_ADMIN for BUDGET)."""
    if user_ctx.is_admin:
        return True

    from app.models import UserRole
    if work_type_id is None:
        work_type = get_budget_work_type()
        work_type_id = work_type.id

    admin_role = UserRole.query.filter_by(
        user_id=user_ctx.user_id,
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=work_type_id,
    ).first()

    return admin_role is not None


def build_portfolio_perms(ctx: PortfolioContext) -> PortfolioPerms:
    """Build permission flags for a portfolio."""
    is_admin = is_budget_admin(ctx.user_ctx, ctx.work_type.id)

    # Membership permissions
    m_can_view = bool(ctx.membership and ctx.membership.can_view)
    m_can_edit = bool(ctx.membership and ctx.membership.can_edit)

    # View: admin or membership.can_view
    can_view = is_admin or m_can_view

    # Edit: admin or membership.can_edit
    can_edit = is_admin or m_can_edit

    # Check for existing PRIMARY
    existing_primary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    # Can create PRIMARY: can_edit AND no existing PRIMARY
    can_create_primary = can_edit and (existing_primary is None)

    # Can create SUPPLEMENTARY: can_edit AND PRIMARY is FINALIZED
    can_create_supplementary = can_edit and (
        existing_primary is not None and
        existing_primary.status == WORK_ITEM_STATUS_FINALIZED
    )

    return PortfolioPerms(
        can_view=can_view,
        can_edit=can_edit,
        can_create_primary=can_create_primary,
        can_create_supplementary=can_create_supplementary,
        is_admin=is_admin,
    )


# ============================================================
# Checkout Helper Functions
# ============================================================

def get_checkout_timeout_minutes(user_ctx: UserContext) -> int:
    """Get checkout timeout in minutes based on user role."""
    if ROLE_SUPER_ADMIN in user_ctx.roles:
        return CHECKOUT_TIMEOUTS["SUPER_ADMIN"]
    if ROLE_APPROVER in user_ctx.roles:
        return CHECKOUT_TIMEOUTS["APPROVER"]
    return CHECKOUT_TIMEOUTS["DEFAULT"]


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


def can_checkout(work_item: WorkItem, user_ctx: UserContext) -> tuple[bool, str]:
    """
    Check if user can checkout a work item.

    Returns (can_checkout, reason) tuple.
    """
    # Must be SUBMITTED status
    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        return False, "Only SUBMITTED requests can be checked out for review."

    # Must be a reviewer (admin or approver)
    if not user_ctx.is_admin and ROLE_APPROVER not in user_ctx.roles:
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
    can_do, _reason = can_checkout(work_item, user_ctx)
    if not can_do:
        return False

    timeout_minutes = get_checkout_timeout_minutes(user_ctx)
    now = datetime.utcnow()

    work_item.checked_out_by_user_id = user_ctx.user_id
    work_item.checked_out_at = now
    work_item.checked_out_expires_at = now + timedelta(minutes=timeout_minutes)

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
    if not work_item.checked_out_by_user_id:
        return False  # Nothing to release

    # Check permission
    is_current_holder = work_item.checked_out_by_user_id == user_ctx.user_id
    is_admin = user_ctx.is_admin

    if not is_current_holder and not (is_admin and force):
        return False

    work_item.checked_out_by_user_id = None
    work_item.checked_out_at = None
    work_item.checked_out_expires_at = None

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


def _is_approver_for_work_item(work_item: WorkItem, user_ctx: UserContext) -> bool:
    """
    Check if user is an approver who can review this work item.

    Returns True if user has APPROVER role and is in an approval group
    that has lines routed to it in this work item.
    """
    if ROLE_APPROVER not in user_ctx.roles:
        return False

    # Get approval groups the user can review
    if not user_ctx.approval_group_ids:
        return False

    # Check if any lines in this work item are routed to user's approval groups
    for line in work_item.lines:
        if line.budget_detail and line.budget_detail.routed_approval_group_id:
            if line.budget_detail.routed_approval_group_id in user_ctx.approval_group_ids:
                return True

    return False


def build_work_item_perms(item: WorkItem, ctx: PortfolioContext) -> WorkItemPerms:
    """Build permission flags for a work item."""
    portfolio_perms = build_portfolio_perms(ctx)

    is_draft = item.status == WORK_ITEM_STATUS_DRAFT
    is_submitted = item.status == WORK_ITEM_STATUS_SUBMITTED
    is_needs_info = item.status == WORK_ITEM_STATUS_NEEDS_INFO

    # View: portfolio can_view
    can_view = portfolio_perms.can_view

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

    # Can checkout: is a reviewer (admin or approver) AND item is SUBMITTED AND not already checked out
    is_reviewer = portfolio_perms.is_admin or _is_approver_for_work_item(item, ctx.user_ctx)
    can_checkout_item = is_reviewer and is_submitted and not item_is_checked_out

    # Can checkin: current user has checkout OR admin can force release
    can_checkin_item = is_checked_out_by_current_user or (portfolio_perms.is_admin and item_is_checked_out)

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
        is_admin=portfolio_perms.is_admin,
        is_draft=is_draft,
        is_checked_out=item_is_checked_out,
        is_checked_out_by_current_user=is_checked_out_by_current_user,
    )


# ============================================================
# Permission Enforcement Functions
# ============================================================

def require_portfolio_view(ctx: PortfolioContext) -> PortfolioPerms:
    """Abort 403 if user cannot view the portfolio."""
    perms = build_portfolio_perms(ctx)
    if not perms.can_view:
        abort(403, "You do not have permission to view this portfolio.")
    return perms


def require_portfolio_edit(ctx: PortfolioContext) -> PortfolioPerms:
    """Abort 403 if user cannot edit the portfolio."""
    perms = build_portfolio_perms(ctx)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this portfolio.")
    return perms


def require_work_item_view(item: WorkItem, ctx: PortfolioContext) -> WorkItemPerms:
    """Abort 403 if user cannot view the work item."""
    perms = build_work_item_perms(item, ctx)
    if not perms.can_view:
        abort(403, "You do not have permission to view this work item.")
    return perms


def require_work_item_edit(item: WorkItem, ctx: PortfolioContext) -> WorkItemPerms:
    """Abort 403 if user cannot edit the work item."""
    perms = build_work_item_perms(item, ctx)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this work item.")
    return perms


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


# ============================================================
# Expense Account Helpers
# ============================================================

def get_visible_expense_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
    exclude_fixed: bool = True,
) -> list[ExpenseAccount]:
    """
    Get expense accounts visible to a department.

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides (not used in Chunk A)
        exclude_fixed: If True, excludes fixed-cost accounts (Chunk A only)
    """
    query = ExpenseAccount.query.filter(ExpenseAccount.is_active == True)

    if exclude_fixed:
        query = query.filter(ExpenseAccount.is_fixed_cost == False)

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_fixed_cost_expense_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
) -> list[ExpenseAccount]:
    """
    Get fixed-cost expense accounts visible to a department.

    These are accounts where is_fixed_cost=True, meaning the unit price
    is predetermined and users only specify quantity.

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides
    """
    query = ExpenseAccount.query.filter(
        ExpenseAccount.is_active == True,
        ExpenseAccount.is_fixed_cost == True,
    )

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_effective_fixed_cost_settings(
    expense_account: ExpenseAccount,
    event_cycle_id: int | None = None,
) -> dict:
    """
    Get effective fixed-cost settings for an expense account,
    considering event-specific overrides.

    Returns dict with:
        - unit_price_cents: The locked unit price
        - frequency_id: Default frequency (if any)
        - warehouse_default: Default warehouse flag
    """
    # Start with base account settings
    unit_price_cents = expense_account.default_unit_price_cents or 0
    frequency_id = expense_account.default_frequency_id
    warehouse_default = expense_account.warehouse_default

    # Check for event-specific override
    if event_cycle_id:
        override = next(
            (o for o in expense_account.event_overrides if o.event_cycle_id == event_cycle_id),
            None
        )
        if override:
            if override.default_unit_price_cents is not None:
                unit_price_cents = override.default_unit_price_cents
            if override.default_frequency_id is not None:
                frequency_id = override.default_frequency_id
            if override.warehouse_default is not None:
                warehouse_default = override.warehouse_default

    return {
        "unit_price_cents": unit_price_cents,
        "frequency_id": frequency_id,
        "warehouse_default": warehouse_default,
    }


def get_allowed_spend_types(expense_account: ExpenseAccount) -> list[SpendType]:
    """
    Get valid spend types for an expense account.

    For SINGLE_LOCKED mode, returns only the default spend type.
    For ALLOW_LIST mode, returns the allowed_spend_types list.
    """
    if expense_account.spend_type_mode == SPEND_TYPE_MODE_SINGLE_LOCKED:
        if expense_account.default_spend_type:
            return [expense_account.default_spend_type]
        return []

    # ALLOW_LIST mode
    return list(expense_account.allowed_spend_types)


# ============================================================
# Dropdown Data Helpers
# ============================================================

def get_confidence_levels() -> list[ConfidenceLevel]:
    """Get active confidence levels for dropdown."""
    return ConfidenceLevel.query.filter_by(is_active=True).order_by(
        ConfidenceLevel.sort_order.asc(),
        ConfidenceLevel.name.asc()
    ).all()


def get_frequency_options() -> list[FrequencyOption]:
    """Get active frequency options for dropdown."""
    return FrequencyOption.query.filter_by(is_active=True).order_by(
        FrequencyOption.sort_order.asc(),
        FrequencyOption.name.asc()
    ).all()


def get_priority_levels() -> list[PriorityLevel]:
    """Get active priority levels for dropdown."""
    return PriorityLevel.query.filter_by(is_active=True).order_by(
        PriorityLevel.sort_order.asc(),
        PriorityLevel.name.asc()
    ).all()


def get_spend_types() -> list[SpendType]:
    """Get active spend types for dropdown."""
    return SpendType.query.filter_by(is_active=True).order_by(
        SpendType.sort_order.asc(),
        SpendType.name.asc()
    ).all()


# ============================================================
# Totals Computation
# ============================================================

def compute_portfolio_totals(portfolio: WorkPortfolio) -> dict:
    """
    Compute totals for a portfolio.

    Returns dict with:
        - requested: Total requested amount in cents
        - approved: Total approved amount in cents
        - pending: requested - approved
    """
    requested = 0
    approved = 0

    for item in portfolio.work_items:
        if item.is_archived:
            continue

        for line in item.lines:
            if line.budget_detail:
                detail = line.budget_detail
                line_total = detail.unit_price_cents * int(detail.quantity)
                requested += line_total

                if line.status == WORK_LINE_STATUS_APPROVED:
                    approved += line.approved_amount_cents or 0

    return {
        "requested": requested,
        "approved": approved,
        "pending": requested - approved,
    }


def compute_work_item_totals(item: WorkItem) -> dict:
    """
    Compute totals for a single work item.

    Returns dict with:
        - requested: Total requested amount in cents
        - approved: Total approved amount in cents
        - line_count: Number of lines
    """
    requested = 0
    approved = 0
    line_count = 0

    for line in item.lines:
        line_count += 1
        if line.budget_detail:
            detail = line.budget_detail
            line_total = detail.unit_price_cents * int(detail.quantity)
            requested += line_total

            if line.status == WORK_LINE_STATUS_APPROVED:
                approved += line.approved_amount_cents or 0

    return {
        "requested": requested,
        "approved": approved,
        "line_count": line_count,
    }


# ============================================================
# Formatting Helpers
# ============================================================

def format_currency(cents: int) -> str:
    """Format cents as currency string."""
    dollars = cents / 100
    return f"${dollars:,.2f}"


def get_next_line_number(work_item: WorkItem) -> int:
    """Get the next available line number for a work item."""
    if not work_item.lines:
        return 1

    max_num = max(line.line_number for line in work_item.lines)
    return max_num + 1
