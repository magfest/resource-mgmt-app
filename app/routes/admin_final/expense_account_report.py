"""
Expense Account Lines Report - every budget request line for one expense account.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort, request

from app import db
from app.models import (
    BudgetLineDetail, WorkLine, WorkItem, WorkPortfolio,
    ExpenseAccount, Department,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency
from . import admin_final_bp
from .helpers import (
    require_budget_admin,
    get_active_event_cycles,
    get_active_departments,
    get_active_expense_accounts,
)
from .report_utils import resolve_report_filters, compute_line_amount_cents
from .report_exports import (
    make_csv_response, format_currency_csv, generate_timestamp_filename,
)


@dataclass
class ExpenseAccountLineRow:
    department_code: str
    department_name: str
    work_item_id: int
    work_item_public_id: str
    line_number: int
    description: Optional[str]
    quantity: object
    unit_price_cents: int
    requested_cents: int
    approved_amount_cents: Optional[int]
    line_status: str


def get_expense_account_line_data(
    event_cycle_id: int,
    expense_account_id: int,
    department_id: Optional[int] = None,
) -> List[ExpenseAccountLineRow]:
    """Return one row per budget line for the given expense account + event."""
    q = (
        db.session.query(
            Department.code.label("department_code"),
            Department.name.label("department_name"),
            WorkItem.id.label("work_item_id"),
            WorkItem.public_id.label("work_item_public_id"),
            WorkLine.line_number.label("line_number"),
            WorkLine.status.label("line_status"),
            WorkLine.approved_amount_cents.label("approved_amount_cents"),
            BudgetLineDetail.description.label("description"),
            BudgetLineDetail.quantity.label("quantity"),
            BudgetLineDetail.unit_price_cents.label("unit_price_cents"),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(BudgetLineDetail.expense_account_id == expense_account_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        .order_by(Department.code.asc(), WorkItem.public_id.asc(), WorkLine.line_number.asc())
    )
    if department_id:
        q = q.filter(WorkPortfolio.department_id == department_id)

    rows = []
    for r in q.all():
        rows.append(
            ExpenseAccountLineRow(
                department_code=r.department_code,
                department_name=r.department_name,
                work_item_id=r.work_item_id,
                work_item_public_id=r.work_item_public_id,
                line_number=r.line_number,
                description=r.description,
                quantity=r.quantity,
                unit_price_cents=r.unit_price_cents,
                requested_cents=compute_line_amount_cents(r.unit_price_cents, r.quantity),
                approved_amount_cents=r.approved_amount_cents,
                line_status=r.line_status,
            )
        )
    return rows


def _resolve_account() -> Optional[ExpenseAccount]:
    code = request.args.get("account", "").strip()
    if not code:
        return None
    return ExpenseAccount.query.filter_by(code=code.upper()).first()


def _summarize(rows: List[ExpenseAccountLineRow]) -> dict:
    return {
        "line_count": len(rows),
        "department_count": len({r.department_code for r in rows}),
        "request_count": len({r.work_item_public_id for r in rows}),
        "requested_cents": sum(r.requested_cents for r in rows),
        "approved_cents": sum(r.approved_amount_cents or 0 for r in rows),
    }


@admin_final_bp.get("/admin/budget/expense-account/")
def expense_account_report():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    account = _resolve_account()

    rows = []
    summary = None
    if filters.has_event and account is not None:
        rows = get_expense_account_line_data(
            filters.event_cycle_id, account.id, filters.department_id
        )
        summary = _summarize(rows)

    return render_template(
        "admin_final/expense_account_report.html",
        user_ctx=user_ctx,
        rows=rows,
        summary=summary,
        event_cycles=get_active_event_cycles(),
        departments=get_active_departments(),
        expense_accounts=get_active_expense_accounts(),
        selected_event=filters.event_code,
        selected_dept=filters.dept_code,
        selected_account=account.code if account else "",
        selected_event_cycle=filters.event_cycle,
        selected_department=filters.department,
        selected_account_obj=account,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/expense-account/export")
def expense_account_report_export():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    account = _resolve_account()
    if not filters.has_event or account is None:
        abort(400, "Event cycle and expense account are required for export")

    rows = get_expense_account_line_data(
        filters.event_cycle_id, account.id, filters.department_id
    )

    headers = [
        "Department", "Request", "Line", "Description",
        "Qty", "Unit Price", "Requested", "Approved", "Status",
    ]
    csv_rows = []
    for r in rows:
        csv_rows.append([
            r.department_name,
            r.work_item_public_id,
            r.line_number,
            r.description or "",
            r.quantity,
            format_currency_csv(r.unit_price_cents),
            format_currency_csv(r.requested_cents),
            format_currency_csv(r.approved_amount_cents) if r.approved_amount_cents is not None else "",
            r.line_status,
        ])

    filename = generate_timestamp_filename(
        "expense_account", filters.event_code, account.code
    )
    return make_csv_response(filename, headers, csv_rows)
