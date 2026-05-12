"""
AV request detail page.

GET /<event>/<dept>/av/item/<public_id>

Read-only view of an AV request: submitted form data, AV team's plans,
status timeline, and contextual action buttons.

Permission: anyone with view access on the request's Space may view it
(cross-dept visibility for depts assigned to the same space).
Super admins always have access.
"""
from __future__ import annotations

from flask import abort, render_template

from app import db
from app.models import (
    Department,
    EventCycle,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
)
from app.models.constants import REVIEW_STATUS_LOGGED
from app.models.av import AVLineDetail, AVRequestDetail
from app.routes import get_user_ctx
from .permissions import (
    can_view_av_request,
    can_edit_av_request,
    is_av_admin,
    is_av_team_member,
)
from .timeline import build_status_timeline
from .. import work_bp


@work_bp.get("/<event>/<dept>/av/item/<public_id>")
def av_request_view(event: str, dept: str, public_id: str):
    """Read-only detail page for an AV request."""
    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=av_wt.id,
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        is_archived=False,
    ).first_or_404()

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=portfolio.id,
            is_archived=False,
        )
        .options(
            db.joinedload(WorkItem.av_request_detail).joinedload(AVRequestDetail.space),
            db.selectinload(WorkItem.lines)
                .joinedload(WorkLine.av_line_detail),
            db.selectinload(WorkItem.lines)
                .selectinload(WorkLine.reviews),
        )
        .first_or_404()
    )

    if not user_ctx.is_super_admin and not can_view_av_request(user_ctx, work_item):
        abort(403)

    # Pull out key sub-objects
    detail = work_item.av_request_detail
    line = work_item.lines[0] if work_item.lines else None
    line_detail = line.av_line_detail if line else None

    # Plans sorted oldest → newest; latest plan shown prominently
    plans = sorted(work_item.av_plans, key=lambda p: p.revision)
    latest_plan = plans[-1] if plans else None

    # Latest review (highest id)
    latest_review = None
    if line and line.reviews:
        latest_review = max(line.reviews, key=lambda r: r.id)

    # Status timeline
    timeline_events = build_status_timeline(work_item)

    # --- Action button gates ---
    can_edit = can_edit_av_request(user_ctx, work_item) or user_ctx.is_super_admin

    can_edit_draft = (
        can_edit
        and work_item.status == WORK_ITEM_STATUS_DRAFT
    )

    can_recall = (
        can_edit
        and work_item.status == WORK_ITEM_STATUS_SUBMITTED
        and latest_review is not None
        and latest_review.status == REVIEW_STATUS_PENDING
    )

    can_respond_to_kickback = (
        line is not None
        and latest_review is not None
        and latest_review.status in (REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT)
    )

    # AV-team-side action flags.
    # Base gate: viewer is on the AV team (or is an admin) AND WorkItem is SUBMITTED.
    _is_av_team_or_admin = is_av_admin(user_ctx) or is_av_team_member(user_ctx)
    _is_submitted = work_item.status == WORK_ITEM_STATUS_SUBMITTED

    # can_publish_plan: publish Plan rev 1 (Task 31) or revision rev N+1 (Task 34).
    # Allowed when latest review is PENDING (first publish) or LOGGED (revision).
    can_publish_plan = (
        _is_av_team_or_admin
        and _is_submitted
        and latest_review is not None
        and latest_review.status in (REVIEW_STATUS_PENDING, REVIEW_STATUS_LOGGED)
    )

    # can_kickback: send NEEDS_INFO or NEEDS_ADJUSTMENT (Task 32).
    # Only allowed when latest review is PENDING — NOT relaxed for revision case.
    can_kickback = (
        _is_av_team_or_admin
        and _is_submitted
        and latest_review is not None
        and latest_review.status == REVIEW_STATUS_PENDING
    )

    # can_reject: terminal reject action (Task 33).
    # Only allowed when latest review is PENDING — NOT relaxed for revision case.
    can_reject = (
        _is_av_team_or_admin
        and _is_submitted
        and latest_review is not None
        and latest_review.status == REVIEW_STATUS_PENDING
    )

    return render_template(
        "av/request_detail.html",
        event=event_cycle,
        dept=department,
        work_item=work_item,
        detail=detail,
        line=line,
        line_detail=line_detail,
        plans=plans,
        latest_plan=latest_plan,
        existing_plans_count=len(plans),
        latest_review=latest_review,
        can_edit_draft=can_edit_draft,
        can_recall=can_recall,
        can_respond_to_kickback=can_respond_to_kickback,
        can_publish_plan=can_publish_plan,
        can_kickback=can_kickback,
        can_reject=can_reject,
        timeline_events=timeline_events,
    )
