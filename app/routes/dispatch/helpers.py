"""
Dispatch queue helper functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkPortfolio,
    BudgetLineDetail,
    EventCycle,
    Department,
    ApprovalGroup,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
)


@dataclass(frozen=True)
class DispatchQueueItem:
    """A work item in the dispatch queue."""
    work_item: WorkItem
    event_cycle: EventCycle
    department: Department
    line_count: int
    assigned_count: int
    total_cents: int


def get_dispatch_queue(
    event_cycle_id: Optional[int] = None,
    department_id: Optional[int] = None,
) -> List[DispatchQueueItem]:
    """
    Get all work items awaiting dispatch.

    Returns list of DispatchQueueItem with summary info.
    """
    # Base query for work items awaiting dispatch
    query = WorkItem.query.filter(
        WorkItem.status == WORK_ITEM_STATUS_AWAITING_DISPATCH,
        WorkItem.is_archived == False,
    ).join(
        WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id
    ).filter(
        WorkPortfolio.is_archived == False,
    )

    # Apply filters
    if event_cycle_id:
        query = query.filter(WorkPortfolio.event_cycle_id == event_cycle_id)
    if department_id:
        query = query.filter(WorkPortfolio.department_id == department_id)

    # Order by submitted_at
    query = query.order_by(WorkItem.submitted_at.asc())

    # Eager load related data to avoid N+1 queries
    query = query.options(
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    )

    work_items = query.all()
    result = []

    for wi in work_items:
        portfolio = wi.portfolio
        line_count = len(wi.lines)
        assigned_count = 0
        total_cents = 0

        for line in wi.lines:
            if line.budget_detail:
                total_cents += line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
                if line.budget_detail.routed_approval_group_id:
                    assigned_count += 1

        result.append(DispatchQueueItem(
            work_item=wi,
            event_cycle=portfolio.event_cycle,
            department=portfolio.department,
            line_count=line_count,
            assigned_count=assigned_count,
            total_cents=total_cents,
        ))

    return result


def get_dispatch_queue_count() -> int:
    """Get count of items awaiting dispatch."""
    return WorkItem.query.filter(
        WorkItem.status == WORK_ITEM_STATUS_AWAITING_DISPATCH,
        WorkItem.is_archived == False,
    ).count()


def get_active_approval_groups() -> List[ApprovalGroup]:
    """Get all active approval groups for dropdown."""
    return ApprovalGroup.query.filter_by(
        is_active=True
    ).order_by(
        ApprovalGroup.sort_order.asc(),
        ApprovalGroup.name.asc()
    ).all()


def get_active_event_cycles() -> List[EventCycle]:
    """Get active event cycles for filter dropdown."""
    return EventCycle.query.filter_by(is_active=True).order_by(
        EventCycle.sort_order.asc(),
        EventCycle.name.asc()
    ).all()


def get_active_departments() -> List[Department]:
    """Get active departments for filter dropdown."""
    return Department.query.filter_by(is_active=True).order_by(
        Department.sort_order.asc(),
        Department.name.asc()
    ).all()
