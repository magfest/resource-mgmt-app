"""
Master Ledger Report - GL account aggregation by pipeline status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort
from sqlalchemy import func

from app import db
from app.models import (
    ExpenseAccount,
    ApprovalGroup,
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
    build_budget_line_base_query,
    apply_standard_filters,
    compute_pipeline_summary,
)
from .report_exports import (
    make_csv_response,
    format_currency_csv,
    generate_timestamp_filename,
)


@dataclass
class LedgerRow(PipelineTotals):
    """One row per expense account showing amounts at each pipeline stage."""
    expense_account_id: int = 0
    gl_code: str = ""
    account_name: str = ""
    approval_group_code: Optional[str] = None
    approval_group_name: Optional[str] = None


def get_ledger_data(
    event_cycle_id: int,
    department_id: Optional[int] = None,
) -> List[LedgerRow]:
    """
    Get ledger data aggregated by expense account using SQL-level CASE expressions.

    Returns a list of LedgerRow dataclasses, one per expense account that has
    budget lines for the selected event cycle.
    """
    from app.models import BudgetLineDetail, WorkLine, WorkItem, WorkPortfolio

    # Build the query with pipeline sum columns
    query = (
        db.session.query(
            ExpenseAccount.id.label("expense_account_id"),
            ExpenseAccount.code.label("gl_code"),
            ExpenseAccount.name.label("account_name"),
            ApprovalGroup.code.label("approval_group_code"),
            ApprovalGroup.name.label("approval_group_name"),
            *get_pipeline_sum_columns(),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(ExpenseAccount, BudgetLineDetail.expense_account_id == ExpenseAccount.id)
        .outerjoin(ApprovalGroup, ExpenseAccount.approval_group_id == ApprovalGroup.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
    )

    # Apply department filter if specified
    if department_id:
        query = query.filter(WorkPortfolio.department_id == department_id)

    # Group by expense account
    query = query.group_by(
        ExpenseAccount.id,
        ExpenseAccount.code,
        ExpenseAccount.name,
        ApprovalGroup.code,
        ApprovalGroup.name,
    ).order_by(
        ExpenseAccount.code.asc(),
    )

    # Execute and convert to dataclasses
    rows = []
    for row in query.all():
        rows.append(
            LedgerRow(
                expense_account_id=row.expense_account_id,
                gl_code=row.gl_code,
                account_name=row.account_name,
                approval_group_code=row.approval_group_code,
                approval_group_name=row.approval_group_name,
                draft_cents=row.draft_cents,
                submitted_cents=row.submitted_cents,
                ag_approved_cents=row.ag_approved_cents,
                final_approved_cents=row.final_approved_cents,
                rejected_cents=row.rejected_cents,
            )
        )

    return rows


@admin_final_bp.get("/admin/budget/ledger/")
def master_ledger():
    """
    Master Ledger Report - shows budget totals by expense account (GL account)
    for a selected event cycle.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    rows = []
    summary = None

    # Only query data if event cycle is selected
    if filters.has_event:
        rows = get_ledger_data(filters.event_cycle_id, filters.department_id)
        summary = compute_pipeline_summary(rows)

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    return render_template(
        "admin_final/ledger_report.html",
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


@admin_final_bp.get("/admin/budget/ledger/export")
def master_ledger_export():
    """
    Export Master Ledger Report as CSV.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Resolve filters from request
    filters = resolve_report_filters()

    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    # Get report data
    rows = get_ledger_data(filters.event_cycle_id, filters.department_id)
    summary = compute_pipeline_summary(rows)

    # Build CSV headers
    headers = [
        "GL Code",
        "Account Name",
        "Approval Group",
        "Draft",
        "Submitted",
        "AG Approved",
        "Final Approved",
        "Rejected",
        "Total",
    ]

    # Build CSV rows
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row.gl_code,
            row.account_name,
            row.approval_group_code or "",
            format_currency_csv(row.draft_cents),
            format_currency_csv(row.submitted_cents),
            format_currency_csv(row.ag_approved_cents),
            format_currency_csv(row.final_approved_cents),
            format_currency_csv(row.rejected_cents),
            format_currency_csv(row.total_cents),
        ])

    # Add totals row
    csv_rows.append([
        "TOTALS",
        "",
        "",
        format_currency_csv(summary.draft_cents),
        format_currency_csv(summary.submitted_cents),
        format_currency_csv(summary.ag_approved_cents),
        format_currency_csv(summary.final_approved_cents),
        format_currency_csv(summary.rejected_cents),
        format_currency_csv(summary.total_cents),
    ])

    # Generate filename
    filename = generate_timestamp_filename(
        "master_ledger",
        filters.event_code,
        filters.dept_code if filters.has_department else None,
    )

    return make_csv_response(filename, headers, csv_rows)
