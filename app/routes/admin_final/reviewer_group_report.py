"""
Reviewer Group Health Check Report.

Overview of budget lines per approval (reviewer) group, matched by EFFECTIVE
group = routed_approval_group_id if set, else the expense account's default
approval_group_id (the suggested routing). This lets a dispatcher preview a
group's expected load BEFORE dispatching.

Aggregation is done in Python (not SQL): the COALESCE(effective group) plus the
per-status bucketing is awkward and non-portable as a SQL GROUP BY, and a single
event's line volume is small. Do not "optimize" this back into SQL without a
measured need.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from flask import render_template, abort, request

from app import db
from app.models import (
    BudgetLineDetail, WorkLine, WorkItem, WorkPortfolio,
    ExpenseAccount, Department, ApprovalGroup,
    WORK_LINE_STATUS_PENDING, WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT, WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW, WORK_LINE_STATUS_REJECTED,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency
from . import admin_final_bp
from .helpers import (
    require_budget_admin, get_active_event_cycles, get_budget_approval_groups,
)
from .report_utils import resolve_report_filters, compute_line_amount_cents
from .report_exports import (
    make_csv_response, format_currency_csv, generate_timestamp_filename,
)

# Sentinel key for lines that resolve to no group at all (no routed, no suggested).
UNASSIGNED_KEY = 0


@dataclass
class ReviewerGroupLineRow:
    department_name: str
    work_item_id: int
    work_item_public_id: str
    line_number: int
    expense_account_code: str
    expense_account_name: str
    description: Optional[str]
    quantity: object
    requested_cents: int
    line_status: str
    routed_group_id: Optional[int]
    suggested_group_id: Optional[int]
    effective_group_id: Optional[int]
    effective_group_code: str
    effective_group_name: str
    is_dispatched: bool


@dataclass
class ReviewerGroupOverviewRow:
    group_id: Optional[int]
    group_code: str
    group_name: str
    line_count: int = 0
    requested_cents: int = 0
    awaiting_dispatch_count: int = 0
    dispatched_count: int = 0
    pending_count: int = 0
    needs_info_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    _departments: Set[str] = field(default_factory=set, repr=False)

    @property
    def department_count(self) -> int:
        return len(self._departments)


def get_reviewer_group_lines(event_cycle_id: int) -> List[ReviewerGroupLineRow]:
    """Fetch every budget line for the event, with routed + suggested group ids."""
    q = (
        db.session.query(
            Department.name.label("department_name"),
            WorkItem.id.label("work_item_id"),
            WorkItem.public_id.label("work_item_public_id"),
            WorkLine.line_number.label("line_number"),
            WorkLine.status.label("line_status"),
            ExpenseAccount.code.label("expense_account_code"),
            ExpenseAccount.name.label("expense_account_name"),
            ExpenseAccount.approval_group_id.label("suggested_group_id"),
            BudgetLineDetail.description.label("description"),
            BudgetLineDetail.quantity.label("quantity"),
            BudgetLineDetail.unit_price_cents.label("unit_price_cents"),
            BudgetLineDetail.routed_approval_group_id.label("routed_group_id"),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .join(ExpenseAccount, BudgetLineDetail.expense_account_id == ExpenseAccount.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        .order_by(Department.code.asc(), WorkItem.public_id.asc(), WorkLine.line_number.asc())
    )

    # Resolve group names once.
    group_map = {g.id: g for g in ApprovalGroup.query.all()}

    rows = []
    for r in q.all():
        effective_id = r.routed_group_id or r.suggested_group_id
        grp = group_map.get(effective_id)
        rows.append(
            ReviewerGroupLineRow(
                department_name=r.department_name,
                work_item_id=r.work_item_id,
                work_item_public_id=r.work_item_public_id,
                line_number=r.line_number,
                expense_account_code=r.expense_account_code,
                expense_account_name=r.expense_account_name,
                description=r.description,
                quantity=r.quantity,
                requested_cents=compute_line_amount_cents(r.unit_price_cents, r.quantity),
                line_status=r.line_status,
                routed_group_id=r.routed_group_id,
                suggested_group_id=r.suggested_group_id,
                effective_group_id=effective_id,
                effective_group_code=grp.code if grp else "—",
                effective_group_name=grp.name if grp else "Unassigned",
                is_dispatched=r.routed_group_id is not None,
            )
        )
    return rows


def build_reviewer_group_overview(
    lines: List[ReviewerGroupLineRow],
) -> List[ReviewerGroupOverviewRow]:
    """Aggregate lines into one overview row per effective group."""
    buckets = {}
    for ln in lines:
        key = ln.effective_group_id or UNASSIGNED_KEY
        agg = buckets.get(key)
        if agg is None:
            agg = ReviewerGroupOverviewRow(
                group_id=ln.effective_group_id,
                group_code=ln.effective_group_code,
                group_name=ln.effective_group_name,
            )
            buckets[key] = agg
        agg.line_count += 1
        agg.requested_cents += ln.requested_cents
        agg._departments.add(ln.department_name)
        if ln.is_dispatched:
            agg.dispatched_count += 1
        else:
            agg.awaiting_dispatch_count += 1
        if ln.line_status == WORK_LINE_STATUS_PENDING:
            agg.pending_count += 1
        elif ln.line_status in (WORK_LINE_STATUS_NEEDS_INFO, WORK_LINE_STATUS_NEEDS_ADJUSTMENT):
            agg.needs_info_count += 1
        elif ln.line_status in (WORK_LINE_STATUS_APPROVED, WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW):
            # A flagged AG recommendation is still a positive recommendation
            # for tally purposes; fold it into the approved bucket so it
            # isn't dropped until the item finalizes.
            agg.approved_count += 1
        elif ln.line_status == WORK_LINE_STATUS_REJECTED:
            agg.rejected_count += 1

    # Sort: real groups by code, Unassigned last.
    return sorted(
        buckets.values(),
        key=lambda a: (a.group_id is None, a.group_code),
    )


def _resolve_group() -> Optional[ApprovalGroup]:
    code = request.args.get("group", "").strip()
    if not code:
        return None
    return ApprovalGroup.query.filter_by(code=code.upper()).first()


@admin_final_bp.get("/admin/budget/reviewer-group/")
def reviewer_group_report():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    selected_group = _resolve_group()

    overview = []
    drill_lines = []
    grand = None
    if filters.has_event:
        lines = get_reviewer_group_lines(filters.event_cycle_id)
        overview = build_reviewer_group_overview(lines)
        grand = {
            "line_count": sum(o.line_count for o in overview),
            "requested_cents": sum(o.requested_cents for o in overview),
            "awaiting_dispatch_count": sum(o.awaiting_dispatch_count for o in overview),
            "dispatched_count": sum(o.dispatched_count for o in overview),
        }
        if selected_group is not None:
            drill_lines = [
                ln for ln in lines if ln.effective_group_id == selected_group.id
            ]

    return render_template(
        "admin_final/reviewer_group_report.html",
        user_ctx=user_ctx,
        overview=overview,
        drill_lines=drill_lines,
        grand=grand,
        event_cycles=get_active_event_cycles(),
        approval_groups=get_budget_approval_groups(),
        selected_event=filters.event_code,
        selected_group=selected_group.code if selected_group else "",
        selected_group_obj=selected_group,
        selected_event_cycle=filters.event_cycle,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/reviewer-group/export")
def reviewer_group_report_export():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    lines = get_reviewer_group_lines(filters.event_cycle_id)
    overview = build_reviewer_group_overview(lines)

    headers = [
        "Reviewer Group", "Lines", "Departments", "Total Requested",
        "Awaiting Dispatch", "Dispatched",
        "Pending", "Needs Info", "Approved", "Rejected",
    ]
    csv_rows = []
    for o in overview:
        csv_rows.append([
            o.group_name, o.line_count, o.department_count,
            format_currency_csv(o.requested_cents),
            o.awaiting_dispatch_count, o.dispatched_count,
            o.pending_count, o.needs_info_count, o.approved_count, o.rejected_count,
        ])

    filename = generate_timestamp_filename("reviewer_group_health", filters.event_code)
    return make_csv_response(filename, headers, csv_rows)
