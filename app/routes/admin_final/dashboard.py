"""
Admin Final Review dashboard routes.
"""
from flask import render_template, redirect, url_for, request, flash, abort
from sqlalchemy.orm import selectinload, joinedload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkPortfolio,
    BudgetLineDetail,
    EventCycle,
    Department,
    ApprovalGroup,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency, friendly_status, get_budget_work_type
from . import admin_final_bp
from .helpers import (
    require_admin,
    require_budget_admin,
    build_admin_queues,
    get_active_event_cycles,
    get_active_departments,
    get_finalization_summary,
    finalize_work_item,
    unfinalize_work_item,
    get_budget_admin_stats,
    get_budget_approval_groups,
)


def _require_has_admin_final(work_item: WorkItem) -> None:
    """Abort 404 unless the work item is BUDGET's admin_final flow.

    admin_final is BUDGET-scoped in practice (per CLAUDE.md): SUPPLY also
    has WorkTypeConfig.has_admin_final=True, but it finalizes through its
    own admin surface (app/routes/work/supply/admin.py's
    supply_admin_finalize), which writes quantity_approved on
    SupplyOrderLineDetail -- a field this BUDGET-only flow never touches.
    Without the BUDGET check, a SUBMITTED supply order reaching this route
    would flip to FINALIZED with quantity_approved left unset.
    """
    work_type = work_item.portfolio.work_type
    config = work_type.config
    if config is None or not config.has_admin_final or work_type.code != "BUDGET":
        abort(404)


@admin_final_bp.get("/admin/final-review/")
def dashboard():
    """
    Admin Final Review dashboard.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    # Get filter values from query params
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()

    # Resolve filters to IDs
    event_cycle_id = None
    department_id = None

    if event_code:
        event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()
        if event_cycle:
            event_cycle_id = event_cycle.id

    if dept_code:
        department = Department.query.filter_by(code=dept_code.upper()).first()
        if department:
            department_id = department.id

    # Build queues
    queues = build_admin_queues(
        event_cycle_id=event_cycle_id,
        department_id=department_id,
    )

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    return render_template(
        "admin_final/dashboard.html",
        user_ctx=user_ctx,
        queues=queues,
        event_cycles=event_cycles,
        departments=departments,
        selected_event=event_code,
        selected_dept=dept_code,
        format_currency=format_currency,
        friendly_status=friendly_status,
        get_finalization_summary=get_finalization_summary,
    )


@admin_final_bp.post("/admin/final-review/finalize/<int:work_item_id>")
def finalize(work_item_id: int):
    """
    Finalize a work item.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).get_or_404(work_item_id)
    _require_has_admin_final(work_item)
    note = (request.form.get("note") or "").strip()

    success, error = finalize_work_item(work_item, user_ctx, note)

    if not success:
        flash(error, "error")
    else:
        flash(f"Work item {work_item.public_id} finalized.", "success")
        db.session.commit()

        # Send notification to department members (non-blocking)
        try:
            from app.services.notifications import notify_work_item_finalized
            notify_work_item_finalized(work_item)
            db.session.commit()  # Commit notification log
        except Exception:
            db.session.rollback()
            import logging
            logging.getLogger(__name__).exception(
                "Failed to send finalization notification for %s", work_item.public_id
            )

    # Redirect back to referrer or dashboard
    from app.routes.admin.helpers import safe_redirect_url
    referrer = safe_redirect_url(
        request.form.get("referrer"),
        fallback=url_for("admin_final.dashboard"),
    )
    return redirect(referrer)


@admin_final_bp.get("/admin/final-review/unfinalize/<int:work_item_id>")
def unfinalize_form(work_item_id: int):
    """
    Show unfinalize form.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
    ).get_or_404(work_item_id)
    _require_has_admin_final(work_item)

    return render_template(
        "admin_final/unfinalize.html",
        user_ctx=user_ctx,
        work_item=work_item,
        format_currency=format_currency,
        friendly_status=friendly_status,
        get_finalization_summary=get_finalization_summary,
    )


@admin_final_bp.post("/admin/final-review/unfinalize/<int:work_item_id>")
def unfinalize(work_item_id: int):
    """
    Unfinalize a work item.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).get_or_404(work_item_id)
    _require_has_admin_final(work_item)

    reason = (request.form.get("reason") or "").strip()
    reset_lines = request.form.get("reset_lines") == "yes"

    success, error = unfinalize_work_item(work_item, reason, reset_lines, user_ctx)

    if not success:
        flash(error, "error")
        return redirect(url_for("admin_final.unfinalize_form", work_item_id=work_item_id))
    else:
        flash(f"Work item {work_item.public_id} unfinalized.", "success")
        db.session.commit()

    return redirect(url_for("admin_final.dashboard"))


# ============================================================
# Super Admin Home / Landing Page
# ============================================================

@admin_final_bp.get("/admin/")
def admin_home():
    """
    Super-admin dashboard with system overview.
    """
    from app.models import User, Division

    user_ctx = get_user_ctx()
    require_admin(user_ctx)

    # Get default event cycle for quick links
    default_event = EventCycle.query.filter_by(is_default=True, is_active=True).first()
    if not default_event:
        default_event = EventCycle.query.filter_by(is_active=True).order_by(
            EventCycle.sort_order
        ).first()

    # System overview counts (app-level, cross-work-type)
    system_stats = {
        "users": User.query.filter(User.is_active.is_(True)).count(),
        "divisions": Division.query.filter(Division.is_active.is_(True)).count(),
        "departments": Department.query.filter(Department.is_active.is_(True)).count(),
        "active_events": EventCycle.query.filter(EventCycle.is_active.is_(True)).count(),
        "approval_groups": ApprovalGroup.query.filter(ApprovalGroup.is_active.is_(True)).count(),
    }

    return render_template(
        "admin_final/admin_home.html",
        user_ctx=user_ctx,
        default_event=default_event,
        system_stats=system_stats,
    )


# ============================================================
# Budget Admin Home
# ============================================================

@admin_final_bp.get("/admin/budget/")
def budget_admin_home():
    """
    Budget admin landing page - accessible by Budget Worktype Admins + Super Admins.
    """
    from app.routes.work.helpers import get_enabled_department_ids_for_event
    from app.routes.home import get_selected_event_cycle
    from app.models import WorkType

    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    budget_wt = WorkType.query.filter_by(code="BUDGET").first()
    selected_cycle, show_all_events = get_selected_event_cycle()

    # Stat tiles: BUDGET-only, scoped to the nav's selected event
    # ("All events" keeps the cross-event totals).
    stats = get_budget_admin_stats(selected_cycle, show_all_events)

    # Get BUDGET approval groups for quick links (other worktypes' groups
    # belong on their own admin pages).
    approval_groups = get_budget_approval_groups()

    # Get filter options for department navigation
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    # Department progress panel follows the selected event; falls back to
    # the default cycle when "All events" is selected.
    default_event = selected_cycle
    if default_event is None:
        default_event = EventCycle.query.filter_by(is_default=True, is_active=True).first()
        if not default_event:
            default_event = EventCycle.query.filter_by(is_active=True).order_by(
                EventCycle.sort_order
            ).first()

    event_progress = None
    if default_event:
        enabled_dept_ids = get_enabled_department_ids_for_event(default_event.id)
        enabled_count = len(enabled_dept_ids)

        depts_with_items = (
            db.session.query(db.func.count(db.distinct(WorkPortfolio.department_id)))
            .join(WorkItem, WorkItem.portfolio_id == WorkPortfolio.id)
            .filter(WorkPortfolio.event_cycle_id == default_event.id)
            .filter(WorkPortfolio.work_type_id == (budget_wt.id if budget_wt else -1))
            .filter(WorkPortfolio.is_archived.is_(False))
            .filter(WorkItem.is_archived.is_(False))
            .filter(WorkPortfolio.department_id.in_(enabled_dept_ids))
            .scalar()
        ) if enabled_dept_ids else 0

        status_counts = dict(
            db.session.query(WorkItem.status, db.func.count(WorkItem.id))
            .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
            .filter(WorkPortfolio.event_cycle_id == default_event.id)
            .filter(WorkPortfolio.work_type_id == (budget_wt.id if budget_wt else -1))
            .filter(WorkPortfolio.is_archived.is_(False))
            .filter(WorkItem.is_archived.is_(False))
            .filter(WorkPortfolio.department_id.in_(enabled_dept_ids))
            .group_by(WorkItem.status)
            .all()
        ) if enabled_dept_ids else {}

        event_progress = {
            "enabled_depts": enabled_count,
            "started_depts": depts_with_items,
            "draft": status_counts.get(WORK_ITEM_STATUS_DRAFT, 0),
            "awaiting_dispatch": status_counts.get(WORK_ITEM_STATUS_AWAITING_DISPATCH, 0),
            "submitted": status_counts.get(WORK_ITEM_STATUS_SUBMITTED, 0),
            "finalized": status_counts.get(WORK_ITEM_STATUS_FINALIZED, 0),
        }

    return render_template(
        "admin_final/budget_home.html",
        user_ctx=user_ctx,
        stats=stats,
        approval_groups=approval_groups,
        event_cycles=event_cycles,
        departments=departments,
        friendly_status=friendly_status,
        default_event=default_event,
        event_progress=event_progress,
        selected_cycle=selected_cycle,
        show_all_events=show_all_events,
    )


# ============================================================
# All Requests View
# ============================================================

@admin_final_bp.get("/admin/requests/")
def all_requests():
    """
    View all BUDGET requests with search, filter, and pagination.

    Filtered to the BUDGET worktype only — this view existed before the
    multi-worktype refactor and was implicitly "all requests" because
    BUDGET was the only worktype. Now that TECHOPS is active (and AV is
    on the way), we have to scope explicitly. TechOps gets its own
    parallel view at work.techops_all_requests.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    budget_wt = get_budget_work_type()

    # Get filter/search params
    search_query = request.args.get("q", "").strip()
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 25

    # Build base query with eager loading to avoid N+1 queries
    query = WorkItem.query.filter(
        WorkItem.is_archived == False
    ).join(
        WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id
    ).join(
        Department, WorkPortfolio.department_id == Department.id
    ).join(
        EventCycle, WorkPortfolio.event_cycle_id == EventCycle.id
    ).filter(
        WorkPortfolio.work_type_id == budget_wt.id
    ).options(
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
        joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    )

    # Apply event filter
    if event_code:
        query = query.filter(EventCycle.code == event_code.upper())

    # Apply department filter
    if dept_code:
        query = query.filter(Department.code == dept_code.upper())

    # Apply status filter
    if status_filter:
        query = query.filter(WorkItem.status == status_filter.upper())

    # Apply search (search by public_id or department name)
    if search_query:
        search_pattern = f"%{search_query}%"
        query = query.filter(
            db.or_(
                WorkItem.public_id.ilike(search_pattern),
                Department.name.ilike(search_pattern),
                Department.code.ilike(search_pattern),
            )
        )

    # Order by most recent first
    query = query.order_by(WorkItem.updated_at.desc())

    # Paginate
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    items = pagination.items

    # Build item data with totals
    requests_data = []
    for wi in items:
        portfolio = wi.portfolio
        line_count = len(wi.lines)
        total_cents = sum(
            line.budget_detail.unit_price_cents * int(line.budget_detail.quantity)
            for line in wi.lines
            if line.budget_detail
        )
        requests_data.append({
            "work_item": wi,
            "portfolio": portfolio,
            "event_cycle": portfolio.event_cycle,
            "department": portfolio.department,
            "line_count": line_count,
            "total_cents": total_cents,
        })

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    # Status options (using friendly labels)
    statuses = [
        ("DRAFT", "Draft"),
        ("AWAITING_DISPATCH", "Waiting for Assignment"),
        ("SUBMITTED", "Under Review"),
        ("FINALIZED", "Finalized"),
    ]

    return render_template(
        "admin_final/all_requests.html",
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
        format_currency=format_currency,
        friendly_status=friendly_status,
    )
