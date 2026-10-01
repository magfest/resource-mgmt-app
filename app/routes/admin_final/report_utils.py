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
from app.line_details import compute_line_amount_cents  # noqa: F401  re-exported for reports
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
    REVIEW_STAGE_ADMIN_FINAL,
)


# ============================================================
# Common Dataclasses
# ============================================================

@dataclass
class PipelineTotals:
    """Budget line amounts split into the buckets assigned by get_pipeline_bucket_expr.

    Draft is held apart and is not part of requested_cents; a draft has not
    been asked for yet. The other four buckets reconcile: requested equals
    in-review plus approved plus reduced plus rejected. Every field is in
    requested dollars except approved_cents, which holds what the budget admin
    approved.
    """
    draft_cents: int = 0
    in_review_cents: int = 0
    approved_requested_cents: int = 0
    approved_cents: int = 0
    rejected_cents: int = 0

    @property
    def requested_cents(self) -> int:
        return self.in_review_cents + self.approved_requested_cents + self.rejected_cents

    @property
    def reduced_cents(self) -> int:
        """Return how far approved amounts fell below what was requested.

        Negative when the budget admin approved more than the request.
        """
        return self.approved_requested_cents - self.approved_cents

    @property
    def ceiling_cents(self) -> int:
        """Return the most this scope can still cost: approved plus in review."""
        return self.approved_cents + self.in_review_cents

    def add(self, other: "PipelineTotals") -> "PipelineTotals":
        """Add another PipelineTotals to this one, returning a new instance."""
        return PipelineTotals(**{
            name: getattr(self, name) + getattr(other, name)
            for name in PIPELINE_FIELDS
        })


PIPELINE_FIELDS = (
    "draft_cents",
    "in_review_cents",
    "approved_requested_cents",
    "approved_cents",
    "rejected_cents",
)


def pipeline_fields_from_row(row) -> dict:
    """Pull the pipeline sums out of a query row built with get_pipeline_sum_columns."""
    return {name: getattr(row, name) for name in PIPELINE_FIELDS}


# Shared by every report CSV so the column order cannot drift between reports.
PIPELINE_CSV_HEADERS = [
    "Draft",
    "Requested",
    "In Review",
    "Approved",
    "Reduced",
    "Rejected",
    "Current Ceiling",
]


def pipeline_csv_values(totals: PipelineTotals, format_cents) -> list:
    """Return one CSV row segment in PIPELINE_CSV_HEADERS order."""
    return [
        format_cents(totals.draft_cents),
        format_cents(totals.requested_cents),
        format_cents(totals.in_review_cents),
        format_cents(totals.approved_cents),
        format_cents(totals.reduced_cents),
        format_cents(totals.rejected_cents),
        format_cents(totals.ceiling_cents),
    ]


@dataclass
class ReportFilters:
    """
    Common filter values resolved from request parameters.
    """
    event_code: str = ""
    dept_code: str = ""
    request_kind: str = ""  # PRIMARY, SUPPLEMENTARY, or "" for all
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

    @property
    def has_request_kind(self) -> bool:
        return self.request_kind in ("PRIMARY", "SUPPLEMENTARY")


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
    request_kind = request.args.get("kind", "").strip().upper()

    # Validate request_kind
    if request_kind not in ("PRIMARY", "SUPPLEMENTARY"):
        request_kind = ""

    event_cycle = None
    department = None

    if event_code:
        event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()

    if dept_code:
        department = Department.query.filter_by(code=dept_code.upper()).first()

    return ReportFilters(
        event_code=event_code,
        dept_code=dept_code,
        request_kind=request_kind,
        event_cycle=event_cycle,
        department=department,
    )


# ============================================================
# SQL Building Blocks - CASE Expressions for Pipeline Stages
# ============================================================

def get_line_amount_expr():
    """
    Returns SQLAlchemy expression for line amount (unit_price_cents * quantity).

    Rounds the product, not the quantity, to match compute_line_amount_cents.
    Casting quantity first rounded 1.5 to 2 on PostgreSQL and truncated it to 1
    on SQLite; neither matched the requested amount.
    """
    return func.cast(
        func.round(BudgetLineDetail.unit_price_cents * BudgetLineDetail.quantity),
        db.Integer,
    )


def get_pipeline_bucket_expr():
    """Return a CASE expression naming the report bucket for each budget line.

    One ordered CASE, not a predicate per bucket, so every line lands in exactly
    one bucket. Separate predicates once dropped lines the budget admin had
    approved but not yet finalized, and dropped PAUSED items entirely.

    A line is decided once the budget admin approves or rejects it at
    ADMIN_FINAL, or once its item is finalized. finalize_work_item uses the same
    test to decide which lines it may skip. A reviewer's approval or rejection
    is a recommendation the admin can overturn, so it stays IN_REVIEW.
    """
    decided = db.or_(
        WorkLine.current_review_stage == REVIEW_STAGE_ADMIN_FINAL,
        WorkItem.status == WORK_ITEM_STATUS_FINALIZED,
    )
    return case(
        (WorkItem.status == WORK_ITEM_STATUS_DRAFT, "DRAFT"),
        (db.and_(decided, WorkLine.status == WORK_LINE_STATUS_APPROVED), "APPROVED"),
        (db.and_(decided, WorkLine.status == WORK_LINE_STATUS_REJECTED), "REJECTED"),
        else_="IN_REVIEW",
    )


def get_pipeline_sum_columns():
    """
    Returns labeled sum columns for each pipeline bucket, named as PIPELINE_FIELDS.

    Use these in a query's select() to get aggregated totals:
        query = db.session.query(
            SomeEntity.id,
            *get_pipeline_sum_columns()
        ).group_by(SomeEntity.id)
    """
    bucket = get_pipeline_bucket_expr()
    requested = get_line_amount_expr()
    approved = func.coalesce(WorkLine.approved_amount_cents, requested)

    def bucket_sum(name, amount, label):
        return func.coalesce(
            func.sum(case((bucket == name, amount), else_=0)), 0
        ).label(label)

    return [
        bucket_sum("DRAFT", requested, "draft_cents"),
        bucket_sum("IN_REVIEW", requested, "in_review_cents"),
        bucket_sum("APPROVED", requested, "approved_requested_cents"),
        bucket_sum("APPROVED", approved, "approved_cents"),
        bucket_sum("REJECTED", requested, "rejected_cents"),
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
    """Sum the PIPELINE_FIELDS of each row into one PipelineTotals."""
    totals = PipelineTotals()
    for row in rows:
        totals = totals.add(PipelineTotals(**{
            name: getattr(row, name, 0) or 0 for name in PIPELINE_FIELDS
        }))
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
