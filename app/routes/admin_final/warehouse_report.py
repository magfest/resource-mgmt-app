"""
Warehouse Report - Provides a report of all lines for an event that have the warehouse flag as true
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort

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
    get_active_departments
)
from .report_utils import resolve_report_filters, compute_line_amount_cents
from .report_exports import (
    make_csv_response, format_currency_csv, generate_timestamp_filename,
)


@dataclass
class WarehouseLineRow:
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
    expense_account_code: str
    expense_account_name: str


def get_warehouse_line_data(
    event_cycle_id: int,
    department_id: Optional[int] = None,
) -> List[WarehouseLineRow]:
    """Gets all budget request lines that have the warehouse flag set as true"""
    q = (
        db.session.query(
            ExpenseAccount.code.label("expense_account_code"),
            ExpenseAccount.name.label("expense_account_name"),
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
        .join(ExpenseAccount, ExpenseAccount.id == BudgetLineDetail.expense_account_id)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .filter(BudgetLineDetail.warehouse_flag == True)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        .order_by(Department.code.asc(), WorkItem.public_id.asc(), WorkLine.line_number.asc())
    )
    if department_id:
        q = q.filter(WorkPortfolio.department_id == department_id)

    rows = []
    for r in q.all():
        rows.append(
            WarehouseLineRow(
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
                expense_account_code=r.expense_account_code,
                expense_account_name=r.expense_account_name,
            )
        )
    return rows


def _summarize(rows: List[WarehouseLineRow]) -> dict:
    return {
        "line_count": len(rows),
        "department_count": len({r.department_code for r in rows}),
        "request_count": len({r.work_item_public_id for r in rows}),
        "requested_cents": sum(r.requested_cents for r in rows),
        "approved_cents": sum(r.approved_amount_cents or 0 for r in rows),
    }


@admin_final_bp.get("/admin/budget/warehouse/")
def warehouse_report():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()

    rows = []
    summary = None
    if filters.has_event:
        rows = get_warehouse_line_data(
            filters.event_cycle_id, filters.department_id
        )
        summary = _summarize(rows)

    return render_template(
        "admin_final/warehouse_report.html",
        user_ctx=user_ctx,
        rows=rows,
        summary=summary,
        event_cycles=get_active_event_cycles(),
        departments=get_active_departments(),
        selected_event=filters.event_code,
        selected_dept=filters.dept_code,
        selected_event_cycle=filters.event_cycle,
        selected_department=filters.department,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/warehouse/export")
def warehouse_report_export():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    rows = get_warehouse_line_data(
        filters.event_cycle_id, filters.department_id
    )

    headers = [
        "Account Code", "Account Name", "Department", "Request", "Line", "Description",
        "Qty", "Unit Price", "Requested", "Approved", "Status",
    ]
    csv_rows = []
    for r in rows:
        csv_rows.append([
            r.expense_account_code,
            r.expense_account_name,
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
        "warehouse", filters.event_code, filters.dept_code if filters.has_department else None
    )
    return make_csv_response(filename, headers, csv_rows)
