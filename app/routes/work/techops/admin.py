"""TechOps cross-department admin views under /admin/techops/.

The all-requests view mirrors BUDGET's admin_final.all_requests, without a
money column. The admin home, the planning worklist and its CSV open to
every TechOps staffer, including plain group members. Routes live on
work_bp, not a separate blueprint, beside the rest of the TechOps work-type
routing.
"""
from __future__ import annotations

from collections import Counter
from datetime import date

from flask import Response, abort, render_template, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    EventCycle,
    TechOpsLineDetail,
    TechOpsServiceType,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
)
from app.routes import get_user_ctx
from app.routes.approvals.helpers import (
    get_active_departments,
    get_active_event_cycles,
    get_reviewable_groups,
)
from app.routes.work.helpers import friendly_status, is_worktype_admin, is_worktype_viewer
from app.routes.work.helpers.review_state import group_progress_for_items
from .. import work_bp
from .line_grain import SERVICE_NO_SERVICES
from .preview import PHONE_LINE_NOT_CHOSEN
from .home import departments_not_submitted, team_queues, unfinished_request_count
from .worklist import (
    GROUP_BYS, STATUS_FILTERS, WorklistFilters, group_rows, parse_filters,
    worklist_rows, worklist_totals,
)
from .worklist_csv import render_csv


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


def _require_techops_staffer(user_ctx) -> list[ApprovalGroup]:
    """Return the TechOps groups this user may review; 403 when there are none.

    Wider than _require_techops_staff: plain group members pass too. Holding a
    group, the TechOps admin or viewer role, or super admin all qualify.
    """
    groups = get_reviewable_groups(user_ctx, "TECHOPS")
    if not groups:
        abort(403, "TechOps staff access required.")
    return groups


def _techops_groups() -> list[ApprovalGroup]:
    """Every active TechOps group; any staffer may filter to any team."""
    return (
        ApprovalGroup.query
        .join(WorkType, WorkType.id == ApprovalGroup.work_type_id)
        .filter(WorkType.code == "TECHOPS", ApprovalGroup.is_active.is_(True))
        .order_by(ApprovalGroup.sort_order.asc(), ApprovalGroup.name.asc())
        .all()
    )


def _resolve_event(event_code: str):
    """The named active event (404 if unknown), else the default, else the first active."""
    if event_code:
        cycle = EventCycle.query.filter_by(code=event_code.upper(), is_active=True).first()
        if cycle is None:
            abort(404, "Unknown event.")
        return cycle
    return (
        EventCycle.query.filter_by(is_default=True, is_active=True).first()
        or EventCycle.query.filter_by(is_active=True)
        .order_by(EventCycle.sort_order.asc(), EventCycle.name.asc()).first()
    )


def _worklist_query(user_ctx, reviewable) -> dict:
    """Resolve the event and filters from request.args and fetch the rows."""
    event = _resolve_event(request.args.get("event", "").strip())
    teams = _techops_groups()
    services = (
        TechOpsServiceType.query
        .filter(TechOpsServiceType.is_active.is_(True),
                TechOpsServiceType.code != SERVICE_NO_SERVICES)
        .order_by(TechOpsServiceType.sort_order.asc(), TechOpsServiceType.name.asc())
        .all()
    )
    departments = get_active_departments(event.id) if event else []
    own = tuple(g.code for g in reviewable if g.id in (user_ctx.approval_group_ids or ()))
    filters, group_by, team_choice = parse_filters(
        request.args, own_team_codes=own,
        team_codes=tuple(g.code for g in teams),
        service_codes=tuple(s.code for s in services),
        dept_codes=tuple(d.code for d in departments),
    )
    rows = worklist_rows(event, filters) if event else []
    return {
        "event": event, "teams": teams, "services": services,
        "departments": departments, "own_team_codes": own, "filters": filters,
        "group_by": group_by, "team_choice": team_choice, "rows": rows,
    }


@work_bp.get("/admin/techops/worklist/")
def techops_worklist():
    """Planning view of every submitted TechOps line in one event."""
    user_ctx = get_user_ctx()
    reviewable = _require_techops_staffer(user_ctx)
    query = _worklist_query(user_ctx, reviewable)
    csv_args = {k: v for k, v in request.args.items() if k != "group"}
    return render_template(
        "techops/worklist.html",
        user_ctx=user_ctx,
        event_cycles=get_active_event_cycles(),
        groups=group_rows(query["rows"], query["group_by"]),
        totals=worklist_totals(query["rows"]),
        status_filters=STATUS_FILTERS,
        group_bys=GROUP_BYS,
        csv_args=csv_args,
        phone_line_not_chosen=PHONE_LINE_NOT_CHOSEN,
        **query,
    )


@work_bp.get("/admin/techops/worklist.csv")
def techops_worklist_csv():
    """CSV of the worklist's current filters, in the page's sort order."""
    user_ctx = get_user_ctx()
    reviewable = _require_techops_staffer(user_ctx)
    query = _worklist_query(user_ctx, reviewable)
    event = query["event"]
    if event is None:
        abort(404, "There are no active events.")

    def link_for(row):
        return url_for(
            "approvals.line_review", event=event.code, dept=row.department.code,
            work_type_slug="techops", public_id=row.work_item.public_id,
            line_num=row.line.line_number, _external=True,
        )

    filename = f"techops-worklist-{event.code}-{date.today():%Y%m%d}.csv"
    return Response(
        render_csv(query["rows"], link_for),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@work_bp.get("/admin/techops/")
def techops_home():
    """TechOps admin home: links, what needs me, who hasn't asked, and scale."""
    user_ctx = get_user_ctx()
    reviewable = _require_techops_staffer(user_ctx)
    techops_wt = WorkType.query.filter_by(code="TECHOPS").first()
    can_list_requests = bool(techops_wt) and (
        is_worktype_admin(user_ctx, techops_wt.id)
        or is_worktype_viewer(user_ctx, techops_wt.id)
    )
    event = _resolve_event(request.args.get("event", "").strip())
    all_departments = request.args.get("all_departments") == "1"

    context = {"event": event, "queues": [], "unfinished": 0,
               "no_request": [], "draft_only": [], "totals": []}
    if event:
        no_request, draft_only = departments_not_submitted(event, all_departments)
        context.update(
            queues=team_queues(event, reviewable),
            unfinished=unfinished_request_count(event),
            no_request=no_request,
            draft_only=draft_only,
            totals=worklist_totals(worklist_rows(event, WorklistFilters())),
        )
    return render_template(
        "techops/admin_home.html",
        user_ctx=user_ctx,
        event_cycles=get_active_event_cycles(),
        reviewable=reviewable,
        can_list_requests=can_list_requests,
        all_departments=all_departments,
        **context,
    )
