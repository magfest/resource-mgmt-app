"""
Department Budget Summary Report - budget totals by department across pipeline stages.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort

from app import db
from app.models import (
    BudgetLineDetail,
    WorkLine,
    WorkItem,
    WorkPortfolio,
    Department,
    Division,
    WorkType,
    WORK_ITEM_STATUS_DRAFT,
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
    PipelineTotals,
    resolve_report_filters,
    get_pipeline_sum_columns,
    pipeline_fields_from_row,
    pipeline_csv_values,
    PIPELINE_CSV_HEADERS,
    compute_pipeline_summary,
)
from .report_exports import (
    make_csv_response,
    format_currency_csv,
    generate_timestamp_filename,
)


@dataclass
class DepartmentRow(PipelineTotals):
    """One row per department showing amounts at each pipeline stage."""
    department_id: int = 0
    department_code: str = ""
    department_name: str = ""
    division_code: Optional[str] = None
    division_name: Optional[str] = None
    line_count: int = 0
    request_count: int = 0
    draft_request_count: int = 0


def get_department_data(
    event_cycle_id: int,
    department_id: Optional[int] = None,
    request_kind: Optional[str] = None,
) -> List[DepartmentRow]:
    """
    Get department summary data aggregated by department using SQL-level CASE expressions.

    Returns a list of DepartmentRow dataclasses, one per department that has
    budget lines or an unsubmitted draft for the selected event cycle.
    """
    from sqlalchemy import func, distinct

    # Build the query with pipeline sum columns
    query = (
        db.session.query(
            Department.id.label("department_id"),
            Department.code.label("department_code"),
            Department.name.label("department_name"),
            Division.code.label("division_code"),
            Division.name.label("division_name"),
            func.count(distinct(WorkLine.id)).label("line_count"),
            func.count(distinct(WorkItem.id)).label("request_count"),
            *get_pipeline_sum_columns(),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .outerjoin(Division, Department.division_id == Division.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
    )

    # Apply department filter if specified (for single department detail view)
    if department_id:
        query = query.filter(WorkPortfolio.department_id == department_id)

    # Apply request_kind filter if specified (PRIMARY or SUPPLEMENTARY)
    if request_kind:
        query = query.filter(WorkItem.request_kind == request_kind)

    # Group by department
    query = query.group_by(
        Department.id,
        Department.code,
        Department.name,
        Division.code,
        Division.name,
    ).order_by(
        Division.name.asc().nulls_last(),
        Department.name.asc(),
    )

    # Execute and convert to dataclasses
    rows = []
    for row in query.all():
        rows.append(
            DepartmentRow(
                department_id=row.department_id,
                department_code=row.department_code,
                department_name=row.department_name,
                division_code=row.division_code,
                division_name=row.division_name,
                line_count=row.line_count,
                request_count=row.request_count,
                **pipeline_fields_from_row(row),
            )
        )

    _attach_draft_requests(rows, event_cycle_id, department_id, request_kind)
    return rows


def _attach_draft_requests(rows, event_cycle_id, department_id, request_kind):
    """Count each department's unsubmitted budget drafts and add rows for draft-only departments.

    Counts requests, not lines; an empty draft is the likeliest sign a
    department forgot to submit. This query starts from WorkItem, not
    BudgetLineDetail, so it filters to the BUDGET work type explicitly.
    """
    from sqlalchemy import func

    query = (
        db.session.query(
            Department.id.label("department_id"),
            Department.code.label("department_code"),
            Department.name.label("department_name"),
            Division.code.label("division_code"),
            Division.name.label("division_name"),
            func.count(WorkItem.id).label("draft_count"),
        )
        .select_from(WorkItem)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(WorkType, WorkPortfolio.work_type_id == WorkType.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .outerjoin(Division, Department.division_id == Division.id)
        .filter(WorkType.code == "BUDGET")
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.status == WORK_ITEM_STATUS_DRAFT)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
    )
    if department_id:
        query = query.filter(WorkPortfolio.department_id == department_id)
    if request_kind:
        query = query.filter(WorkItem.request_kind == request_kind)
    query = query.group_by(
        Department.id, Department.code, Department.name, Division.code, Division.name,
    )

    by_id = {r.department_id: r for r in rows}
    added = False
    for d in query.all():
        if d.department_id in by_id:
            by_id[d.department_id].draft_request_count = d.draft_count
        else:
            rows.append(DepartmentRow(
                department_id=d.department_id,
                department_code=d.department_code,
                department_name=d.department_name,
                division_code=d.division_code,
                division_name=d.division_name,
                request_count=d.draft_count,
                draft_request_count=d.draft_count,
            ))
            added = True

    if added:
        # Match the SQL order: division name with no-division last, then department name.
        rows.sort(key=lambda r: (r.division_name is None, r.division_name or "", r.department_name))


@dataclass
class DepartmentSummaryStats:
    """Additional stats for the department report."""
    total_departments: int
    total_requests: int
    total_lines: int
    departments_with_drafts: int


def compute_department_stats(rows: List[DepartmentRow]) -> DepartmentSummaryStats:
    """Compute additional statistics from department rows."""
    return DepartmentSummaryStats(
        total_departments=len(rows),
        total_requests=sum(r.request_count for r in rows),
        total_lines=sum(r.line_count for r in rows),
        departments_with_drafts=sum(1 for r in rows if r.draft_request_count),
    )


@admin_final_bp.get("/admin/budget/departments/")
def department_summary():
    """
    Department Budget Summary Report - shows budget totals by department
    for a selected event cycle.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    rows = []
    summary = None
    stats = None

    # Only query data if event cycle is selected
    if filters.has_event:
        rows = get_department_data(
            filters.event_cycle_id,
            filters.department_id,
            filters.request_kind if filters.has_request_kind else None,
        )
        summary = compute_pipeline_summary(rows)
        stats = compute_department_stats(rows)

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    return render_template(
        "admin_final/department_report.html",
        user_ctx=user_ctx,
        rows=rows,
        summary=summary,
        stats=stats,
        event_cycles=event_cycles,
        departments=departments,
        selected_event=filters.event_code,
        selected_dept=filters.dept_code,
        selected_request_kind=filters.request_kind,
        selected_event_cycle=filters.event_cycle,
        selected_department=filters.department,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/departments/export")
def department_summary_export():
    """
    Export Department Budget Summary Report as CSV.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    # Get report data
    rows = get_department_data(
        filters.event_cycle_id,
        filters.department_id,
        filters.request_kind if filters.has_request_kind else None,
    )
    summary = compute_pipeline_summary(rows)
    stats = compute_department_stats(rows)

    # Build CSV headers
    headers = [
        "Department",
        "Department Code",
        "Division",
        "Requests",
        "Lines",
        "Unsubmitted Drafts",
        *PIPELINE_CSV_HEADERS,
    ]

    # Build CSV rows
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row.department_name,
            row.department_code,
            row.division_name or "",
            row.request_count,
            row.line_count,
            row.draft_request_count,
            *pipeline_csv_values(row, format_currency_csv),
        ])

    # Add totals row
    csv_rows.append([
        "TOTALS",
        "",
        "",
        stats.total_requests,
        stats.total_lines,
        sum(r.draft_request_count for r in rows),
        *pipeline_csv_values(summary, format_currency_csv),
    ])

    # Generate filename
    filename = generate_timestamp_filename(
        "department_summary",
        filters.event_code,
        filters.dept_code if filters.has_department else None,
    )

    return make_csv_response(filename, headers, csv_rows)
