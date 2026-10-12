"""
TechOps admin routes — cross-department admin views.

Currently exposes a single all-requests view, mirroring BUDGET's
admin_final.all_requests but TechOps-shaped (no monetary column,
line and service-mix counts instead). Lives under work_bp at literal
URL /admin/techops/... so it stays alongside the rest of the TechOps
worktype routing without forcing a separate blueprint.

Future TechOps admin routes (admin home page, catalog config) can
be added here as the team's needs grow.
"""
from __future__ import annotations

from collections import Counter

from flask import abort, render_template, request
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    Department,
    EventCycle,
    TechOpsLineDetail,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
)
from app.routes import get_user_ctx
from app.routes.approvals.helpers import (
    get_active_departments,
    get_active_event_cycles,
)
from app.routes.work.helpers import friendly_status, is_worktype_admin, is_worktype_viewer
from app.routes.work.helpers.review_state import group_progress_for_items
from .. import work_bp


_PER_PAGE = 25


def _require_techops_staff(user_ctx) -> WorkType:
    """Resolve the TECHOPS WorkType row and gate to its admins and viewers."""
    techops_wt = WorkType.query.filter_by(code="TECHOPS").first()
    if not techops_wt:
        abort(404, "TechOps work type not configured.")
    if not (
        is_worktype_admin(user_ctx, techops_wt.id)
        or is_worktype_viewer(user_ctx, techops_wt.id)
    ):
        abort(403, "TechOps admin or viewer access required.")
    return techops_wt


@work_bp.get("/admin/techops/requests/")
def techops_all_requests():
    """Cross-department admin view of every TechOps request.

    Mirrors the shape of admin_final.all_requests but filtered to the
    TECHOPS worktype only and without monetary columns.
    """
    user_ctx = get_user_ctx()
    techops_wt = _require_techops_staff(user_ctx)

    search_query = request.args.get("q", "").strip()
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    query = (
        WorkItem.query
        .filter(WorkItem.is_archived == False)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .join(EventCycle, WorkPortfolio.event_cycle_id == EventCycle.id)
        .filter(WorkPortfolio.work_type_id == techops_wt.id)
        .options(
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.service_type),
        )
    )

    if event_code:
        query = query.filter(EventCycle.code == event_code.upper())
    if dept_code:
        query = query.filter(Department.code == dept_code.upper())
    if status_filter:
        query = query.filter(WorkItem.status == status_filter.upper())
    if search_query:
        pattern = f"%{search_query}%"
        query = query.filter(
            db.or_(
                WorkItem.public_id.ilike(pattern),
                Department.name.ilike(pattern),
                Department.code.ilike(pattern),
            )
        )

    query = query.order_by(WorkItem.updated_at.desc())
    pagination = query.paginate(page=page, per_page=_PER_PAGE, error_out=False)

    progress_by_item = group_progress_for_items(wi.id for wi in pagination.items)

    requests_data = []
    for wi in pagination.items:
        portfolio = wi.portfolio
        # Service-mix summary per request: ordered Counter of service
        # codes so reviewers can scan "this dept asked for 3 ETHERNET +
        # 1 PHONE" at a glance without opening each request.
        service_counts: Counter[str] = Counter()
        for line in wi.lines:
            d = line.techops_detail
            if d and d.service_type:
                service_counts[d.service_type.code] += 1

        # Stable sorted list of (code, count) tuples for template rendering.
        service_mix = sorted(
            service_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )

        requests_data.append({
            "work_item": wi,
            "portfolio": portfolio,
            "event_cycle": portfolio.event_cycle,
            "department": portfolio.department,
            "line_count": len(wi.lines),
            "service_mix": service_mix,
            "progress": progress_by_item.get(wi.id, []),
        })

    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    # No AWAITING_DISPATCH: TechOps has uses_dispatch=False. No NEEDS_INFO
    # either; it is a line status, and nothing writes it to work_item.status.
    statuses = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Under Review"),
        ("FINALIZED", "Finalized"),
    ]

    return render_template(
        "techops/all_requests.html",
        user_ctx=user_ctx,
        requests_data=requests_data,
        pagination=pagination,
        event_cycles=event_cycles,
        departments=departments,
        statuses=statuses,
        selected_event=event_code,
        selected_dept=dept_code,
        selected_status=status_filter,
        search_query=search_query,
        friendly_status=friendly_status,
    )
