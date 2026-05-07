"""AV portfolio landing page (per-dept)."""
from __future__ import annotations

from flask import abort, render_template

from app import db
from app.models import (
    Department,
    EventCycle,
    WorkItem,
    WorkPortfolio,
    WorkType,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.routes import get_user_ctx
from app.routes.work.av.permissions import _user_dept_ids_with_av_access
from .. import work_bp


@work_bp.get("/<event>/<dept>/av/")
def av_portfolio_landing(event: str, dept: str):
    """Department AV portfolio landing page.

    Lists all AV work items for this dept + the spaces they're assigned to.
    Permission: user must have AV view access for this dept (via dept/div
    membership) or be a super admin.
    """
    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    # Permission: user must have AV view access to this dept
    if not user_ctx.is_super_admin:
        user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=False)
        if department.id not in user_dept_ids:
            abort(403)

    # WorkPortfolio for this (event, dept, AV) — may not exist yet
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=av_wt.id,
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        is_archived=False,
    ).first()

    # Active assigned spaces for this dept in this event
    assigned_spaces = (
        db.session.query(Space)
        .join(SpaceDepartmentAssignment, SpaceDepartmentAssignment.space_id == Space.id)
        .filter(
            SpaceDepartmentAssignment.department_id == department.id,
            SpaceDepartmentAssignment.unassigned_at.is_(None),
            Space.event_cycle_id == event_cycle.id,
            Space.is_active.is_(True),
        )
        .order_by(Space.name)
        .all()
    )

    # Existing AV requests for this dept (if portfolio exists)
    requests = []
    if portfolio:
        requests = (
            WorkItem.query
            .filter_by(portfolio_id=portfolio.id, is_archived=False)
            .order_by(WorkItem.created_at.desc())
            .all()
        )

    return render_template(
        "av/portfolio_landing.html",
        event=event_cycle,
        dept=department,
        portfolio=portfolio,
        assigned_spaces=assigned_spaces,
        requests=requests,
        user_ctx=user_ctx,
    )
