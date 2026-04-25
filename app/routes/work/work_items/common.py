"""
Shared helpers for work item routes.
"""
from flask import abort
from sqlalchemy.orm import selectinload, joinedload

from app.models import (
    WorkItem,
    WorkLine,
    BudgetLineDetail,
)
from ..helpers import get_portfolio_context, require_budget_work_type


def get_work_item_by_public_id(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Get a work item by public_id and verify it belongs to the correct portfolio.

    Returns tuple of (work_item, ctx) or aborts with 404.
    Eager loads lines with budget details, expense accounts, spend types, etc.

    Aborts 404 for non-budget work types since the eager-load pattern below
    is budget-specific (BudgetLineDetail joins). This guard is the seam
    where per-work-type handlers will plug in during Phase 2.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        # Eager load lines with all their related data
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.expense_account),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.spend_type),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.confidence_level),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.frequency),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.priority),
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