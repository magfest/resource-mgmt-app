"""
AV request submit and recall endpoints.

POST /<event>/<dept>/av/item/<public_id>/submit transitions a DRAFT AV
request to SUBMITTED.  Because AV uses uses_dispatch=False, the engine
helper submit_work_item immediately creates the WorkLineReview row (routed
to AV_TEAM via the DIRECT strategy) and snapshots the approval group on
AVLineDetail.routed_approval_group_id.

POST /<event>/<dept>/av/item/<public_id>/recall reverses a SUBMITTED AV
request back to DRAFT, IFF the single line's WorkLineReview is still PENDING
and no AVRequestPlan has been published.  The pending WorkLineReview row is
deleted so a clean review is created on the next submit.

These endpoints are reached from the detail page action buttons.
The create form's action=submit path calls _do_submit directly from
create.py (same pattern as TechOps).
"""
from flask import flash, redirect, url_for

from app import db
from app.models import (
    ActivityEvent,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    REVIEW_STATUS_PENDING,
)
from app.models.av import AVLineDetail
from app.models.constants import (
    ACTIVITY_AV_REQUEST_SUBMITTED,
    ACTIVITY_AV_REQUEST_RECALLED,
)
from app.routes import get_user_ctx
from .. import work_bp
from .permissions import can_edit_av_request, require_edit_av_request
from ..helpers.lifecycle import submit_work_item, recall_to_draft


def _do_submit(work_item: WorkItem, user_ctx) -> bool:
    """Run the submit lifecycle for an AV request and commit.

    Calls the engine's submit_work_item helper (uses_dispatch=False path),
    which routes the single line to AV_TEAM, snapshots routed_approval_group_id
    on AVLineDetail, creates a PENDING WorkLineReview, and sets status=SUBMITTED.

    Notification failure is logged but does not roll back the submit.

    Returns True on success, False if submit_work_item raised an exception
    (leaves caller to handle redirect).
    """
    submit_work_item(work_item, user_ctx)

    # Log space-scoped activity event for the Space detail page activity feed.
    db.session.add(ActivityEvent(
        event_type=ACTIVITY_AV_REQUEST_SUBMITTED,
        work_item_id=work_item.id,
        space_id=work_item.av_request_detail.space_id,
        actor_user_id=user_ctx.user_id,
    ))

    db.session.commit()

    try:
        from app.services.notifications import notify_work_item_submitted
        notify_work_item_submitted(work_item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send submission notification for %s", work_item.public_id
        )

    return True


@work_bp.post("/<event>/<dept>/av/item/<public_id>/submit")
def av_request_submit(event: str, dept: str, public_id: str):
    """Submit a DRAFT AV request from the detail page."""
    from app.models import Department, EventCycle, WorkType

    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    from app.models import WorkPortfolio
    portfolio = WorkPortfolio.query.filter_by(
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        work_type_id=av_wt.id,
    ).first_or_404()

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=portfolio.id,
            is_archived=False,
        )
        .first_or_404()
    )

    # Eagerly load lines + av_line_detail so submit_work_item can iterate them
    from sqlalchemy.orm import selectinload, joinedload
    work_item = (
        WorkItem.query
        .filter_by(id=work_item.id)
        .options(
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.av_line_detail),
        )
        .first()
    )

    portfolio_url = url_for(
        "work.av_portfolio_landing", event=event, dept=dept,
    )

    if not user_ctx.is_super_admin and not can_edit_av_request(user_ctx, work_item):
        flash("You do not have permission to submit this AV request.", "error")
        return redirect(portfolio_url), 403

    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("Only DRAFT requests can be submitted.", "error")
        return redirect(portfolio_url), 400

    _do_submit(work_item, user_ctx)

    flash(
        "AV request submitted! The AV team will review it and may reach out with questions.",
        "success",
    )
    return redirect(portfolio_url)


@work_bp.post("/<event>/<dept>/av/item/<public_id>/recall")
def av_request_recall(event: str, dept: str, public_id: str):
    """Recall a SUBMITTED AV request back to DRAFT.

    Eligibility (checked in order):
    1. work_item.status == SUBMITTED
    2. The single line's latest WorkLineReview.status == PENDING
    3. No AVRequestPlan exists for this work_item (AV team hasn't published a plan)

    On success, the pending WorkLineReview row is deleted (so a fresh review is
    created on the next submit), and recall_to_draft() handles the status
    transition + audit event.
    """
    from app.models import Department, EventCycle, WorkType, WorkPortfolio
    from app.models.av import AVRequestPlan
    from sqlalchemy.orm import selectinload, joinedload

    user_ctx = get_user_ctx()

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first_or_404()
    department = Department.query.filter_by(code=dept.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    portfolio = WorkPortfolio.query.filter_by(
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        work_type_id=av_wt.id,
    ).first_or_404()

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=portfolio.id,
            is_archived=False,
        )
        .options(
            selectinload(WorkItem.lines).selectinload(WorkLine.reviews),
        )
        .first_or_404()
    )

    detail_url = url_for(
        "work.av_request_view",
        event=event,
        dept=dept,
        public_id=public_id,
    )
    portfolio_url = url_for(
        "work.av_portfolio_landing",
        event=event,
        dept=dept,
    )

    if not user_ctx.is_super_admin and not can_edit_av_request(user_ctx, work_item):
        from flask import abort
        abort(403)

    # ── Eligibility check 1: must be SUBMITTED ───────────────────────────
    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        flash(
            f"{public_id} is not in SUBMITTED status and cannot be recalled.",
            "error",
        )
        return redirect(detail_url)

    # ── Eligibility check 2: latest review must be PENDING ───────────────
    line = work_item.lines[0] if work_item.lines else None
    latest_review = (
        max(line.reviews, key=lambda r: r.id) if (line and line.reviews) else None
    )
    if latest_review is None or latest_review.status != REVIEW_STATUS_PENDING:
        flash(
            f"Cannot recall {public_id} — AV team has already started reviewing.",
            "error",
        )
        return redirect(detail_url)

    # ── Eligibility check 3: no AVRequestPlan published yet ──────────────
    plan_exists = (
        db.session.query(AVRequestPlan)
        .filter_by(work_item_id=work_item.id)
        .first()
    ) is not None
    if plan_exists:
        flash(
            f"Cannot recall {public_id} — a Plan has already been published by the AV team.",
            "error",
        )
        return redirect(detail_url)

    # ── Perform recall ────────────────────────────────────────────────────
    # Delete the pending WorkLineReview so a fresh one is created on re-submit.
    db.session.delete(latest_review)

    # Engine helper: sets status=DRAFT, clears submitted_at/submitted_by,
    # logs AUDIT_EVENT_RECALL_TO_DRAFT.
    recall_to_draft(work_item, user_ctx)

    # Log space-scoped activity event for the Space detail page activity feed.
    db.session.add(ActivityEvent(
        event_type=ACTIVITY_AV_REQUEST_RECALLED,
        work_item_id=work_item.id,
        space_id=work_item.av_request_detail.space_id,
        actor_user_id=user_ctx.user_id,
    ))

    db.session.commit()

    flash(f"{public_id} recalled to draft. You can now edit and resubmit it.", "success")
    return redirect(portfolio_url)
