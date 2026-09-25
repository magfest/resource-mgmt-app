"""
Shared helpers for work item routes.
"""
from flask import abort
from sqlalchemy.orm import selectinload, joinedload

from app.models import (
    TechOpsLineDetail,
    WorkItem,
    WorkLine,
    BudgetLineDetail,
)
from ..helpers import get_portfolio_context


def get_work_item_by_public_id(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Get a work item by public_id and verify it belongs to the correct portfolio.

    Returns tuple of (work_item, ctx) or aborts with 404.

    Polymorphic across worktypes — used by worktype-agnostic action
    handlers (checkout, checkin, needs-info request/respond) plus the
    BUDGET-specific submit/edit handlers. The BUDGET eager-load chain
    is preserved because most callers are still BUDGET; non-BUDGET
    worktypes get harmless empty joins (LEFT OUTER) and the per-
    worktype line detail relationships are loaded too so callers
    using get_line_detail() don't N+1.

    Worktype-specific validation lives in the per-action handler (e.g.
    work_item_submit asserts budget_detail before proceeding); this
    helper just locates the row.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        # BUDGET-specific eager loads (kept for BUDGET callers; harmless
        # outer joins for other worktypes).
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.expense_account),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.spend_type),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.confidence_level),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.frequency),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.priority),
        # Polymorphic: load all detail types so callers using
        # get_line_detail() / get_line_amount_cents() don't N+1.
        selectinload(WorkItem.lines).joinedload(WorkLine.contract_detail),
        selectinload(WorkItem.lines).joinedload(WorkLine.supply_detail),
        selectinload(WorkItem.lines).joinedload(WorkLine.techops_detail)
            .joinedload(TechOpsLineDetail.space),
        selectinload(WorkItem.lines).joinedload(WorkLine.techops_detail)
            .joinedload(TechOpsLineDetail.parent_line),
        # Eager load comments
        selectinload(WorkItem.comments),
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    return work_item, ctx


def calculate_event_nights(start_date, end_date):
    """Calculate the number of nights between start and end dates."""
    if not start_date or not end_date:
        return None
    return max(0, (end_date - start_date).days)