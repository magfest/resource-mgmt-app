"""
Budget reports index: one page listing every budget report.

The Budget menu links here instead of listing nine reports. Each entry is
keyed by its Flask endpoint name, which is stable across URL changes; a
per-user favorites menu could store these keys later. Add a new report here
rather than as a hardcoded link.
"""
from __future__ import annotations

from dataclasses import dataclass

from flask import render_template

from app.routes import get_user_ctx
from . import admin_final_bp
from .helpers import require_budget_admin


@dataclass(frozen=True)
class ReportEntry:
    endpoint: str
    title: str
    description: str


REPORT_SECTIONS: list[tuple[str, list[ReportEntry]]] = [
    ("Financials", [
        ReportEntry("admin_final.master_ledger", "Master Ledger",
                    "Totals by GL account, split by where each request is in the pipeline."),
        ReportEntry("admin_final.department_summary", "Department Summary",
                    "Budget totals by department across pipeline stages."),
        ReportEntry("admin_final.income_report", "Income Report",
                    "Departments with expected income for an event."),
    ]),
    ("Request Detail", [
        ReportEntry("admin_final.expense_account_report", "Expense Account Lines",
                    "Every budget line for one expense account."),
        ReportEntry("admin_final.hotel_rooms_report", "Hotel Rooms",
                    "All hotel room lines, grouped by who pays."),
        ReportEntry("admin_final.warehouse_report", "Warehouse Lines",
                    "Lines for an event flagged for the warehouse."),
    ]),
    ("Workflow Health", [
        ReportEntry("admin_final.reviewer_group_report", "Reviewer Group Health",
                    "Lines per reviewer group, including expected load before dispatch."),
        ReportEntry("admin_final.workload_report", "Workload Report",
                    "Pending work by reviewer group, with how long it has waited."),
        ReportEntry("admin_final.missing_budgets_report", "Missing Budgets",
                    "Departments with no budget request for an event."),
    ]),
]


@admin_final_bp.get("/admin/budget/reports/")
def reports_index():
    require_budget_admin(get_user_ctx())
    return render_template("admin_final/reports_index.html", sections=REPORT_SECTIONS)
