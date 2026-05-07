"""
AV request kickback-response endpoint.

POST /<event>/<dept>/av/item/<public_id>/respond

Dept members respond to a NEEDS_INFO kickback from the AV team.  On a valid
response the latest WorkLineReview is reset to PENDING so the AV team can
continue their review with the new information.

For NEEDS_ADJUSTMENT the dept edits the request via the normal edit flow
(edit.py); edit.py resets the review to PENDING on save.  No separate
endpoint is needed for that path.
"""
from __future__ import annotations

from flask import abort, flash, redirect, request, url_for

from app import db
from app.models import (
    Department,
    EventCycle,
    WorkItem,
    WorkLine,
    WorkLineComment,
    WorkLineAuditEvent,
    WorkPortfolio,
    WorkType,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_PENDING,
    AUDIT_EVENT_REQUESTER_RESPONSE,
)
from app.routes import get_user_ctx
from app.routes.work.av.permissions import can_edit_av_request
from .. import work_bp


@work_bp.post("/<event>/<dept>/av/item/<public_id>/respond")
def av_request_respond(event: str, dept: str, public_id: str):
    """Submit a response to a NEEDS_INFO kickback from the AV team."""
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
            db.selectinload(WorkItem.lines)
                .selectinload(WorkLine.reviews),
        )
        .first_or_404()
    )

    if not user_ctx.is_super_admin and not can_edit_av_request(user_ctx, work_item):
        abort(403)

    redirect_to_detail = redirect(url_for(
        "work.av_request_view",
        event=event_cycle.code,
        dept=department.code,
        public_id=public_id,
    ))

    line = work_item.lines[0] if work_item.lines else None
    if line is None:
        flash("This request has no lines to respond to.", "error")
        return redirect_to_detail

    latest_review = max(line.reviews, key=lambda r: r.id) if line.reviews else None
    if latest_review is None or latest_review.status != REVIEW_STATUS_NEEDS_INFO:
        flash(
            f"{public_id} is not awaiting a response (expected NEEDS_INFO state).",
            "error",
        )
        return redirect_to_detail

    response_text = (request.form.get("response_text") or "").strip()
    if not response_text:
        flash("Please provide a response before submitting.", "error")
        return redirect_to_detail

    # Add a line-level comment with the response text (mirrors budget pattern:
    # WorkItemComment with [INFO RESPONSE] prefix, but at line level for AV).
    comment = WorkLineComment(
        work_line_id=line.id,
        visibility="PUBLIC",
        body=f"[DEPT RESPONSE] {response_text}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)

    # Reset the review back to PENDING so the AV team can continue.
    latest_review.status = REVIEW_STATUS_PENDING

    # Audit trail
    audit = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_REQUESTER_RESPONSE,
        note=response_text,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit)

    db.session.commit()

    flash(
        f"Response submitted for {public_id}. The AV team has been notified.",
        "success",
    )
    return redirect_to_detail
