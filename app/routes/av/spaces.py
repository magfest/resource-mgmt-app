"""Cross-dept AV Space pages: list and detail."""
from __future__ import annotations

from flask import Blueprint, abort, render_template
from sqlalchemy.orm import joinedload

from app import db
from app.models import ActivityEvent, ApprovalGroup, EventCycle, User, UserRole, WorkItem, WorkPortfolio
from app.models.av import AVRequestDetail
from app.models.space import Space, SpaceDepartmentAssignment
from app.routes import get_user_ctx
from app.routes.work.av.permissions import (
    can_create_av_request_for,
    can_view_av_space,
    is_av_admin,
)


bp = Blueprint("av_cross_dept", __name__)


@bp.get("/<event_code>/av/spaces/")
def space_list(event_code: str):
    user_ctx = get_user_ctx()
    event = EventCycle.query.filter_by(code=event_code).first_or_404()

    # All active spaces for this event
    spaces = (
        Space.query
        .filter_by(event_cycle_id=event.id, is_active=True)
        .order_by(Space.name)
        .options(
            joinedload(Space.assignments)
            .joinedload(SpaceDepartmentAssignment.department)
        )
        .all()
    )

    # Filter by per-Space visibility
    visible_spaces = [s for s in spaces if can_view_av_space(user_ctx, s)]

    # Build per-space metadata for template
    space_rows = []
    for s in visible_spaces:
        active_assignments = [a for a in s.assignments if a.unassigned_at is None]
        space_rows.append({
            "space": s,
            "active_assignments": active_assignments,
            "assigned_dept_codes": [a.department.code for a in active_assignments],
            # Phase 6/7 placeholders — will be filled in later
            "scope_state": None,   # Phase 6 will populate via AVScope query
            "ack_progress": None,  # Phase 7 will populate via AVAcknowledgment count
        })

    return render_template(
        "av/space_list.html",
        event=event,
        space_rows=space_rows,
    )


@bp.get("/<event_code>/av/spaces/<space_code>")
def space_detail(event_code: str, space_code: str):
    """Read-only detail page for a single AV Space (cross-dept view)."""
    user_ctx = get_user_ctx()
    event = EventCycle.query.filter_by(code=event_code).first_or_404()
    space = Space.query.filter_by(
        event_cycle_id=event.id, code=space_code,
    ).first_or_404()

    if not can_view_av_space(user_ctx, space):
        abort(403)

    # ---- File-for-dept CTAs ----
    # Collect depts where the current user has the right to file a new AV request
    user_can_file_for = []
    for assignment in space.assignments:
        if assignment.unassigned_at is not None:
            continue
        dept = assignment.department
        if can_create_av_request_for(user_ctx, space, dept):
            user_can_file_for.append(dept)

    # ---- Contributing requests (non-DRAFT, for this space) ----
    contributing = (
        db.session.query(WorkItem)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(AVRequestDetail, AVRequestDetail.work_item_id == WorkItem.id)
        .filter(
            AVRequestDetail.space_id == space.id,
            WorkItem.status != "DRAFT",
        )
        .options(
            joinedload(WorkItem.av_request_detail),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
            joinedload(WorkItem.lines),
        )
        .order_by(WorkItem.created_at)
        .all()
    )

    # Build row dicts with latest review status per request
    request_rows = []
    for wi in contributing:
        line = wi.lines[0] if wi.lines else None
        latest_review_status = None
        if line and line.reviews:
            latest_review_status = max(line.reviews, key=lambda r: r.id).status
        request_rows.append({
            "wi": wi,
            "latest_review_status": latest_review_status,
        })

    # ---- Dept filed-status sidebar ----
    # A dept "has filed" if any contributing_request belongs to that dept
    filed_dept_ids = {wi.portfolio.department_id for wi in contributing}
    active_assignments = [a for a in space.assignments if a.unassigned_at is None]

    # ---- Steward (AV_TEAM members) ----
    # Members are stored as UserRole rows with ROLE_APPROVER scoped to the AV_TEAM group
    av_work_type = ApprovalGroup.query.filter_by(code="AV_TEAM").first()
    steward_users: list[User] = []
    if av_work_type:
        member_user_ids = (
            db.session.query(UserRole.user_id)
            .filter_by(approval_group_id=av_work_type.id)
            .all()
        )
        if member_user_ids:
            ids = [row[0] for row in member_user_ids]
            steward_users = User.query.filter(User.id.in_(ids)).all()

    # ---- Activity feed (space-scoped events) ----
    activity = (
        ActivityEvent.query
        .filter_by(space_id=space.id)
        .order_by(ActivityEvent.occurred_at.desc())
        .limit(20)
        .all()
    )

    # ---- Phase 6 placeholder ----
    latest_scope = None  # Phase 6 will query AVScope here

    user_is_av_admin = is_av_admin(user_ctx)

    return render_template(
        "av/space_detail.html",
        event=event,
        space=space,
        user_can_file_for=user_can_file_for,
        request_rows=request_rows,
        active_assignments=active_assignments,
        filed_dept_ids=filed_dept_ids,
        steward_users=steward_users,
        activity=activity,
        latest_scope=latest_scope,
        user_is_av_admin=user_is_av_admin,
    )
