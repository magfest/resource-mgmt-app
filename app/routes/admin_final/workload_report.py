"""
Approval Group Workload Report - pending work by approval group with aging metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from flask import render_template, abort
from sqlalchemy import func

from app import db
from app.models import (
    BudgetLineDetail,
    WorkLine,
    WorkItem,
    WorkPortfolio,
    WorkLineReview,
    ApprovalGroup,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_UNDER_REVIEW,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency
from . import admin_final_bp
from .helpers import (
    require_budget_admin,
    get_active_event_cycles,
    get_active_departments,
)
from .report_utils import (
    resolve_report_filters,
    get_line_amount_expr,
    calculate_days_waiting,
)
from .report_exports import (
    make_csv_response,
    format_currency_csv,
    generate_timestamp_filename,
)


@dataclass
class WorkloadRow:
    """One row per approval group showing pending workload metrics."""
    approval_group_id: int
    approval_group_code: str
    approval_group_name: str
    pending_lines: int
    needs_info_lines: int
    total_pending_cents: int
    oldest_submitted_at: Optional[datetime]
    avg_days_waiting: float

    @property
    def total_lines(self) -> int:
        return self.pending_lines + self.needs_info_lines

    @property
    def oldest_days(self) -> int:
        return calculate_days_waiting(self.oldest_submitted_at)


@dataclass
class WorkloadSummary:
    """Summary stats for the workload report."""
    total_approval_groups: int
    total_pending_lines: int
    total_needs_info_lines: int
    total_pending_cents: int
    oldest_days: int
    avg_days_waiting: float


def get_workload_data(
    event_cycle_id: int,
    department_id: Optional[int] = None,
) -> List[WorkloadRow]:
    """
    Get workload data aggregated by approval group.

    Returns a list of WorkloadRow dataclasses, one per approval group that has
    pending work for the selected event cycle.
    """
    line_amount = get_line_amount_expr()

    # Active work item statuses (in review pipeline)
    active_statuses = (
        WORK_ITEM_STATUS_SUBMITTED,
        WORK_ITEM_STATUS_UNDER_REVIEW,
        WORK_ITEM_STATUS_AWAITING_DISPATCH,
    )

    # Build the query
    query = (
        db.session.query(
            ApprovalGroup.id.label("approval_group_id"),
            ApprovalGroup.code.label("approval_group_code"),
            ApprovalGroup.name.label("approval_group_name"),
            # Count of lines pending review (at AG stage)
            func.sum(
                func.cast(
                    db.and_(
                        WorkLine.status == WORK_LINE_STATUS_PENDING,
                        WorkLine.current_review_stage.in_([REVIEW_STAGE_APPROVAL_GROUP, None]),
                    ),
                    db.Integer,
                )
            ).label("pending_lines"),
            # Count of lines needing info/adjustment
            func.sum(
                func.cast(
                    WorkLine.status.in_([
                        WORK_LINE_STATUS_NEEDS_INFO,
                        WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
                    ]),
                    db.Integer,
                )
            ).label("needs_info_lines"),
            # Total pending amount
            func.coalesce(
                func.sum(
                    func.cast(
                        db.and_(
                            WorkLine.status == WORK_LINE_STATUS_PENDING,
                            WorkLine.current_review_stage.in_([REVIEW_STAGE_APPROVAL_GROUP, None]),
                        ),
                        db.Integer,
                    ) * line_amount
                ),
                0,
            ).label("total_pending_cents"),
            # Oldest submitted date
            func.min(WorkItem.submitted_at).label("oldest_submitted_at"),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(ApprovalGroup, BudgetLineDetail.routed_approval_group_id == ApprovalGroup.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.status.in_(active_statuses))
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        # Only include lines that are still pending or kicked back
        .filter(
            db.or_(
                WorkLine.status == WORK_LINE_STATUS_PENDING,
                WorkLine.status.in_([WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT]),
            )
        )
    )

    # Apply department filter if specified
    if department_id:
        query = query.filter(WorkPortfolio.department_id == department_id)

    # Group by approval group
    query = query.group_by(
        ApprovalGroup.id,
        ApprovalGroup.code,
        ApprovalGroup.name,
    ).order_by(
        ApprovalGroup.sort_order.asc(),
        ApprovalGroup.name.asc(),
    )

    # Execute and convert to dataclasses
    rows = []
    now = datetime.utcnow()

    for row in query.all():
        # Calculate average days waiting
        oldest = row.oldest_submitted_at
        if oldest and row.pending_lines > 0:
            # Use oldest as proxy for average (more accurate would need per-line data)
            avg_days = calculate_days_waiting(oldest) / 2  # Rough approximation
        else:
            avg_days = 0.0

        rows.append(
            WorkloadRow(
                approval_group_id=row.approval_group_id,
                approval_group_code=row.approval_group_code,
                approval_group_name=row.approval_group_name,
                pending_lines=row.pending_lines or 0,
                needs_info_lines=row.needs_info_lines or 0,
                total_pending_cents=row.total_pending_cents or 0,
                oldest_submitted_at=row.oldest_submitted_at,
                avg_days_waiting=avg_days,
            )
        )

    return rows


def compute_workload_summary(rows: List[WorkloadRow]) -> WorkloadSummary:
    """Compute summary statistics from workload rows."""
    if not rows:
        return WorkloadSummary(
            total_approval_groups=0,
            total_pending_lines=0,
            total_needs_info_lines=0,
            total_pending_cents=0,
            oldest_days=0,
            avg_days_waiting=0.0,
        )

    total_pending = sum(r.pending_lines for r in rows)
    total_needs_info = sum(r.needs_info_lines for r in rows)
    total_cents = sum(r.total_pending_cents for r in rows)
    oldest_days = max(r.oldest_days for r in rows) if rows else 0

    # Weighted average of days waiting
    total_weighted = sum(r.avg_days_waiting * r.pending_lines for r in rows)
    avg_days = total_weighted / total_pending if total_pending > 0 else 0.0

    return WorkloadSummary(
        total_approval_groups=len(rows),
        total_pending_lines=total_pending,
        total_needs_info_lines=total_needs_info,
        total_pending_cents=total_cents,
        oldest_days=oldest_days,
        avg_days_waiting=round(avg_days, 1),
    )


@admin_final_bp.get("/admin/budget/workload/")
def workload_report():
    """
    Approval Group Workload Report - shows pending work by approval group
    with aging metrics for a selected event cycle.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    rows = []
    summary = None

    # Only query data if event cycle is selected
    if filters.has_event:
        rows = get_workload_data(filters.event_cycle_id, filters.department_id)
        summary = compute_workload_summary(rows)

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    return render_template(
        "admin_final/workload_report.html",
        user_ctx=user_ctx,
        rows=rows,
        summary=summary,
        event_cycles=event_cycles,
        departments=departments,
        selected_event=filters.event_code,
        selected_dept=filters.dept_code,
        selected_event_cycle=filters.event_cycle,
        selected_department=filters.department,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/workload/export")
def workload_report_export():
    """
    Export Approval Group Workload Report as CSV.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    # Get report data
    rows = get_workload_data(filters.event_cycle_id, filters.department_id)
    summary = compute_workload_summary(rows)

    # Build CSV headers
    headers = [
        "Reviewer Group Code",
        "Reviewer Group Name",
        "Pending Lines",
        "Needs Info Lines",
        "Total Lines",
        "Pending Value",
        "Oldest Item (Days)",
        "Avg Wait (Days)",
    ]

    # Build CSV rows
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row.approval_group_code,
            row.approval_group_name,
            row.pending_lines,
            row.needs_info_lines,
            row.total_lines,
            format_currency_csv(row.total_pending_cents),
            row.oldest_days,
            round(row.avg_days_waiting, 1),
        ])

    # Add totals row
    csv_rows.append([
        "TOTALS",
        "",
        summary.total_pending_lines,
        summary.total_needs_info_lines,
        summary.total_pending_lines + summary.total_needs_info_lines,
        format_currency_csv(summary.total_pending_cents),
        summary.oldest_days,
        summary.avg_days_waiting,
    ])

    # Generate filename
    filename = generate_timestamp_filename(
        "workload_report",
        filters.event_code,
        filters.dept_code if filters.has_department else None,
    )

    return make_csv_response(filename, headers, csv_rows)
