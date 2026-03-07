"""
Shared report utilities for budget admin reports.

This module provides reusable components for building reports:
- Common dataclasses for pipeline stage totals
- Shared query building blocks (joins, filters, CASE expressions)
- Filter resolution helpers
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple, List, Any

from flask import request
from sqlalchemy import func, case

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkPortfolio,
    BudgetLineDetail,
    EventCycle,
    Department,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_UNDER_REVIEW,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STAGE_APPROVAL_GROUP,
)


# ============================================================
# Common Dataclasses
# ============================================================

@dataclass
class PipelineTotals:
    """
    Standard totals for each pipeline stage.
    Reusable across any report that aggregates by pipeline status.
    """
    draft_cents: int = 0
    submitted_cents: int = 0
    reviewer_recommended_cents: int = 0
    final_approved_cents: int = 0
    rejected_cents: int = 0

    @property
    def total_cents(self) -> int:
        """Total across all active stages (excluding rejected)."""
        return (
            self.draft_cents
            + self.submitted_cents
            + self.reviewer_recommended_cents
            + self.final_approved_cents
        )

    @property
    def total_with_rejected_cents(self) -> int:
        """Total including rejected amounts."""
        return self.total_cents + self.rejected_cents

    def add(self, other: "PipelineTotals") -> "PipelineTotals":
        """Add another PipelineTotals to this one, returning a new instance."""
        return PipelineTotals(
            draft_cents=self.draft_cents + other.draft_cents,
            submitted_cents=self.submitted_cents + other.submitted_cents,
            reviewer_recommended_cents=self.reviewer_recommended_cents + other.reviewer_recommended_cents,
            final_approved_cents=self.final_approved_cents + other.final_approved_cents,
            rejected_cents=self.rejected_cents + other.rejected_cents,
        )


@dataclass
class ReportFilters:
    """
    Common filter values resolved from request parameters.
    """
    event_code: str = ""
    dept_code: str = ""
    event_cycle: Optional[EventCycle] = None
    department: Optional[Department] = None

    @property
    def event_cycle_id(self) -> Optional[int]:
        return self.event_cycle.id if self.event_cycle else None

    @property
    def department_id(self) -> Optional[int]:
        return self.department.id if self.department else None

    @property
    def has_event(self) -> bool:
        return self.event_cycle is not None

    @property
    def has_department(self) -> bool:
        return self.department is not None


# ============================================================
# Filter Resolution
# ============================================================

def resolve_report_filters() -> ReportFilters:
    """
    Resolve common report filters from request query parameters.
    Returns a ReportFilters dataclass with resolved objects.
    """
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()

    event_cycle = None
    department = None

    if event_code:
        event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()

    if dept_code:
        department = Department.query.filter_by(code=dept_code.upper()).first()

    return ReportFilters(
        event_code=event_code,
        dept_code=dept_code,
        event_cycle=event_cycle,
        department=department,
    )


# ============================================================
# SQL Building Blocks - CASE Expressions for Pipeline Stages
# ============================================================

def get_line_amount_expr():
    """
    Returns SQLAlchemy expression for line amount (unit_price_cents * quantity).
    """
    return BudgetLineDetail.unit_price_cents * func.cast(
        BudgetLineDetail.quantity, db.Integer
    )


def get_pipeline_case_expressions():
    """
    Returns a dict of SQLAlchemy CASE expressions for each pipeline stage.

    These can be used with func.sum() to aggregate amounts by stage.

    Returns:
        dict with keys: 'draft', 'submitted', 'ag_approved', 'final_approved', 'rejected'
        Each value is a CASE expression that returns line_amount or 0.
    """
    line_amount = get_line_amount_expr()

    return {
        # Draft: WorkItem.status == DRAFT
        'draft': case(
            (WorkItem.status == WORK_ITEM_STATUS_DRAFT, line_amount),
            else_=0,
        ),

        # Submitted: WorkItem in review states, line not yet approved/rejected
        'submitted': case(
            (
                db.and_(
                    WorkItem.status.in_([
                        WORK_ITEM_STATUS_SUBMITTED,
                        WORK_ITEM_STATUS_UNDER_REVIEW,
                        WORK_ITEM_STATUS_AWAITING_DISPATCH,
                    ]),
                    WorkLine.status != WORK_LINE_STATUS_APPROVED,
                    WorkLine.status != WORK_LINE_STATUS_REJECTED,
                ),
                line_amount,
            ),
            else_=0,
        ),

        # AG Approved: Line approved at approval group stage, not yet finalized
        'ag_approved': case(
            (
                db.and_(
                    WorkLine.status == WORK_LINE_STATUS_APPROVED,
                    WorkLine.current_review_stage == REVIEW_STAGE_APPROVAL_GROUP,
                    WorkItem.status != WORK_ITEM_STATUS_FINALIZED,
                ),
                func.coalesce(WorkLine.approved_amount_cents, line_amount),
            ),
            else_=0,
        ),

        # Final Approved: WorkItem finalized and line approved
        'final_approved': case(
            (
                db.and_(
                    WorkItem.status == WORK_ITEM_STATUS_FINALIZED,
                    WorkLine.status == WORK_LINE_STATUS_APPROVED,
                ),
                func.coalesce(WorkLine.approved_amount_cents, line_amount),
            ),
            else_=0,
        ),

        # Rejected: Line rejected at any stage
        'rejected': case(
            (WorkLine.status == WORK_LINE_STATUS_REJECTED, line_amount),
            else_=0,
        ),
    }


def get_pipeline_sum_columns():
    """
    Returns labeled sum columns for each pipeline stage.

    Use these in a query's select() to get aggregated totals:
        query = db.session.query(
            SomeEntity.id,
            *get_pipeline_sum_columns()
        ).group_by(SomeEntity.id)

    Returns:
        List of labeled column expressions
    """
    cases = get_pipeline_case_expressions()

    return [
        func.coalesce(func.sum(cases['draft']), 0).label('draft_cents'),
        func.coalesce(func.sum(cases['submitted']), 0).label('submitted_cents'),
        func.coalesce(func.sum(cases['ag_approved']), 0).label('reviewer_recommended_cents'),
        func.coalesce(func.sum(cases['final_approved']), 0).label('final_approved_cents'),
        func.coalesce(func.sum(cases['rejected']), 0).label('rejected_cents'),
    ]


# ============================================================
# Base Query Builder
# ============================================================

def build_budget_line_base_query():
    """
    Returns a base query starting from BudgetLineDetail with standard joins.

    Joins: BudgetLineDetail -> WorkLine -> WorkItem -> WorkPortfolio

    The returned query can be extended with:
        - Additional joins (ExpenseAccount, Department, etc.)
        - Filters
        - Group by
        - Select columns

    Returns:
        SQLAlchemy query object
    """
    return (
        db.session.query(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
    )


def apply_standard_filters(query, filters: ReportFilters):
    """
    Apply standard event cycle and department filters to a query.

    Assumes the query already has WorkPortfolio joined.

    Args:
        query: SQLAlchemy query with WorkPortfolio joined
        filters: ReportFilters instance

    Returns:
        Filtered query
    """
    if filters.event_cycle_id:
        query = query.filter(WorkPortfolio.event_cycle_id == filters.event_cycle_id)

    if filters.department_id:
        query = query.filter(WorkPortfolio.department_id == filters.department_id)

    return query


# ============================================================
# Summary Computation
# ============================================================

def compute_pipeline_summary(rows: List[Any]) -> PipelineTotals:
    """
    Compute totals from a list of rows that have pipeline amount attributes.

    Each row should have: draft_cents, submitted_cents, reviewer_recommended_cents,
    final_approved_cents, rejected_cents (either as attributes or properties).

    Args:
        rows: List of objects with pipeline amount attributes

    Returns:
        PipelineTotals with summed values
    """
    totals = PipelineTotals()

    for row in rows:
        totals = PipelineTotals(
            draft_cents=totals.draft_cents + (getattr(row, 'draft_cents', 0) or 0),
            submitted_cents=totals.submitted_cents + (getattr(row, 'submitted_cents', 0) or 0),
            reviewer_recommended_cents=totals.reviewer_recommended_cents + (getattr(row, 'reviewer_recommended_cents', 0) or 0),
            final_approved_cents=totals.final_approved_cents + (getattr(row, 'final_approved_cents', 0) or 0),
            rejected_cents=totals.rejected_cents + (getattr(row, 'rejected_cents', 0) or 0),
        )

    return totals


# ============================================================
# Workload/Aging Helpers
# ============================================================

@dataclass
class PendingLineInfo:
    """Information about a pending line for workload analysis."""
    work_line_id: int
    work_item_id: int
    line_amount_cents: int
    submitted_at: Optional[datetime]
    days_waiting: int = 0


def calculate_days_waiting(submitted_at: Optional[datetime]) -> int:
    """Calculate days since submission."""
    if not submitted_at:
        return 0
    delta = datetime.utcnow() - submitted_at
    return max(0, delta.days)


def get_pending_line_statuses() -> Tuple[str, ...]:
    """Return tuple of line statuses that indicate pending review."""
    return (
        WORK_LINE_STATUS_PENDING,
        WORK_LINE_STATUS_NEEDS_INFO,
        WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    )


def get_active_work_item_statuses() -> Tuple[str, ...]:
    """Return tuple of work item statuses that are actively in review."""
    return (
        WORK_ITEM_STATUS_SUBMITTED,
        WORK_ITEM_STATUS_UNDER_REVIEW,
        WORK_ITEM_STATUS_AWAITING_DISPATCH,
    )
