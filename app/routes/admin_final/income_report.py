"""
Income Report - departments with expected income for an event cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort, request

from app import db
from app.models import (
    WorkItem,
    WorkPortfolio,
    Department,
    Division,
    EventCycle,
)
from app.routes import get_user_ctx
from . import admin_final_bp
from .helpers import (
    require_budget_admin,
    get_active_event_cycles,
)
from .report_exports import (
    make_csv_response,
    generate_timestamp_filename,
)


@dataclass
class IncomeRow:
    """One row per work item with income information."""
    department_name: str
    department_code: str
    division_name: Optional[str]
    public_id: str
    request_kind: str
    income_estimate_cents: int
    income_notes: Optional[str]
    status: str


def get_income_data(event_cycle_id: int) -> List[IncomeRow]:
    """
    Get all work items with income information for a given event cycle.
    Returns rows ordered by division, then department.
    """
    query = (
        db.session.query(
            Department.name.label("department_name"),
            Department.code.label("department_code"),
            Division.name.label("division_name"),
            WorkItem.public_id,
            WorkItem.request_kind,
            WorkItem.income_estimate_cents,
            WorkItem.income_notes,
            WorkItem.status,
        )
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .outerjoin(Division, Department.division_id == Division.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(
            db.or_(
                WorkItem.income_estimate_cents.isnot(None),
                WorkItem.income_notes.isnot(None),
            )
        )
        .order_by(
            Division.name.asc().nulls_last(),
            Department.name.asc(),
            WorkItem.public_id.asc(),
        )
    )

    return [
        IncomeRow(
            department_name=row.department_name,
            department_code=row.department_code,
            division_name=row.division_name,
            public_id=row.public_id,
            request_kind=row.request_kind,
            income_estimate_cents=row.income_estimate_cents or 0,
            income_notes=row.income_notes,
            status=row.status,
        )
        for row in query.all()
    ]


@admin_final_bp.get("/admin/budget/income/")
def income_report():
    """
    Income Report - shows departments with expected income
    for a selected event cycle.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    event_code = request.args.get("event", "").strip().upper()

    rows = []
    selected_event_cycle = None
    total_income_cents = 0

    event_cycles = get_active_event_cycles()

    if not event_code and event_cycles:
        event_code = event_cycles[0].code

    if event_code:
        selected_event_cycle = EventCycle.query.filter_by(code=event_code).first()
        if selected_event_cycle:
            rows = get_income_data(selected_event_cycle.id)
            total_income_cents = sum(r.income_estimate_cents for r in rows)

    return render_template(
        "admin_final/income_report.html",
        user_ctx=user_ctx,
        rows=rows,
        event_cycles=event_cycles,
        selected_event=event_code,
        selected_event_cycle=selected_event_cycle,
        total_income_cents=total_income_cents,
    )


@admin_final_bp.get("/admin/budget/income/export")
def income_export():
    """Export Income Report as CSV."""
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    event_code = request.args.get("event", "").strip().upper()

    if not event_code:
        abort(400, "Event cycle is required for export")

    event_cycle = EventCycle.query.filter_by(code=event_code).first()
    if not event_cycle:
        abort(400, "Invalid event cycle")

    rows = get_income_data(event_cycle.id)

    headers = [
        "Division",
        "Department",
        "Request ID",
        "Type",
        "Status",
        "Estimated Income",
        "Income Notes",
    ]

    csv_rows = []
    for row in rows:
        csv_rows.append([
            row.division_name or "",
            row.department_name,
            row.public_id,
            row.request_kind,
            row.status,
            f"{row.income_estimate_cents / 100:.2f}" if row.income_estimate_cents else "",
            row.income_notes or "",
        ])

    total_cents = sum(r.income_estimate_cents for r in rows)
    csv_rows.append([])
    csv_rows.append([
        "",
        f"Total: {len(rows)} request(s) with income",
        "",
        "",
        "",
        f"{total_cents / 100:.2f}",
        "",
    ])

    filename = generate_timestamp_filename("income_report", event_code)
    return make_csv_response(filename, headers, csv_rows)
