"""
Missing Budgets Report - departments without any budget request for an event cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort

from app import db
from app.models import (
    WorkItem,
    WorkPortfolio,
    Department,
    Division,
    EventCycle,
)
from app.routes.work.helpers import get_enabled_department_ids_for_event
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
class MissingBudgetRow:
    """One row per department without a budget request."""
    department_id: int
    department_code: str
    department_name: str
    division_code: Optional[str] = None
    division_name: Optional[str] = None


def get_departments_without_budgets(event_cycle_id: int) -> List[MissingBudgetRow]:
    """
    Get all active and enabled departments that do NOT have any budget work items
    (neither primary nor supplementary) for the given event cycle.
    """
    # Get enabled department IDs for this event
    enabled_dept_ids = get_enabled_department_ids_for_event(event_cycle_id)

    # Subquery: departments that DO have a budget request
    departments_with_budgets = (
        db.session.query(WorkPortfolio.department_id)
        .join(WorkItem, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        .distinct()
        .subquery()
    )

    # Query active AND enabled departments NOT in that list
    query = (
        db.session.query(
            Department.id.label("department_id"),
            Department.code.label("department_code"),
            Department.name.label("department_name"),
            Division.code.label("division_code"),
            Division.name.label("division_name"),
        )
        .outerjoin(Division, Department.division_id == Division.id)
        .filter(Department.is_active == True)
        .filter(Department.id.in_(enabled_dept_ids))  # Only enabled departments
        .filter(~Department.id.in_(departments_with_budgets.select()))
        .order_by(
            Division.name.asc().nulls_last(),
            Department.name.asc(),
        )
    )

    rows = []
    for row in query.all():
        rows.append(
            MissingBudgetRow(
                department_id=row.department_id,
                department_code=row.department_code,
                department_name=row.department_name,
                division_code=row.division_code,
                division_name=row.division_name,
            )
        )

    return rows


@admin_final_bp.get("/admin/budget/missing-budgets/")
def missing_budgets_report():
    """
    Missing Budgets Report - shows departments without any budget request
    for a selected event cycle.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    from flask import request
    event_code = request.args.get("event", "").strip().upper()

    rows = []
    selected_event_cycle = None

    # Get filter options
    event_cycles = get_active_event_cycles()

    # If no event specified but there are active cycles, default to first
    if not event_code and event_cycles:
        event_code = event_cycles[0].code

    # Query data if event cycle is selected
    if event_code:
        selected_event_cycle = EventCycle.query.filter_by(code=event_code).first()
        if selected_event_cycle:
            rows = get_departments_without_budgets(selected_event_cycle.id)

    return render_template(
        "admin_final/missing_budgets_report.html",
        user_ctx=user_ctx,
        rows=rows,
        event_cycles=event_cycles,
        selected_event=event_code,
        selected_event_cycle=selected_event_cycle,
    )


@admin_final_bp.get("/admin/budget/missing-budgets/export")
def missing_budgets_export():
    """
    Export Missing Budgets Report as CSV.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    from flask import request
    event_code = request.args.get("event", "").strip().upper()

    if not event_code:
        abort(400, "Event cycle is required for export")

    event_cycle = EventCycle.query.filter_by(code=event_code).first()
    if not event_cycle:
        abort(400, "Invalid event cycle")

    # Get report data
    rows = get_departments_without_budgets(event_cycle.id)

    # Build CSV headers
    headers = [
        "Department",
        "Department Code",
        "Division",
    ]

    # Build CSV rows
    csv_rows = []
    for row in rows:
        csv_rows.append([
            row.department_name,
            row.department_code,
            row.division_name or "",
        ])

    # Add summary row
    csv_rows.append([])
    csv_rows.append([f"Total: {len(rows)} department(s) without budget requests"])

    # Generate filename
    filename = generate_timestamp_filename("missing_budgets", event_code)

    return make_csv_response(filename, headers, csv_rows)
