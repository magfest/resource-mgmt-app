"""
AV request submit endpoint.

POST /<event>/<dept>/av/item/<public_id>/submit transitions a DRAFT AV
request to SUBMITTED.  Because AV uses uses_dispatch=False, the engine
helper submit_work_item immediately creates the WorkLineReview row (routed
to AV_TEAM via the DIRECT strategy) and snapshots the approval group on
AVLineDetail.routed_approval_group_id.

This endpoint is reached from the detail page's Submit button.
The create form's action=submit path calls _do_submit directly from
create.py (same pattern as TechOps).
"""
from flask import flash, redirect, url_for

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WORK_ITEM_STATUS_DRAFT,
)
from app.models.av import AVLineDetail
from app.routes import get_user_ctx
from .. import work_bp
from .permissions import can_edit_av_request, require_edit_av_request
from ..helpers.lifecycle import submit_work_item


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
