"""Close a submitted Supply order, or reopen a finalized or closed one.

Boundary: these functions change department orders, send email to every department member, and write audit rows.
A closed order stays FINALIZED with `SupplyOrderDetail.closed_at` set; CLOSED is
derived for display and never stored. None of these functions commits. The route
commits once, and rolls back on SupplyAdminError.
"""
from __future__ import annotations

import logging
from datetime import datetime

from flask import current_app

from app import db
from app.line_details import set_line_routing_approval_group
from app.models import (
    EmailOutbox,
    SupplyOrderDetail,
    WorkItemAuditEvent,
    WorkItemComment,
    WorkLineReview,
    AUDIT_EVENT_ORDER_CLOSED,
    AUDIT_EVENT_ORDER_REOPENED,
    COMMENT_VISIBILITY_PUBLIC,
    OUTBOX_CLAIMABLE_STATUSES,
    OUTBOX_STATUS_CANCELLED,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_REJECTED,
)
from app.routing.registry import get_approval_group_for_line
from app.services.notifications import enqueue_savepoint, notify_supply_closed
from .admin_lines import _OPEN_GROUP_STATUSES, SupplyAdminError, normalize_reason
from .line_state import is_line_removed, is_order_closed

logger = logging.getLogger(__name__)

CLOSED_REVIEW_NOTE = "Order closed: {reason}"
REOPENED_COMMENT = "[REOPENED] {reason}"

def _live_lines(work_item):
    lines = sorted(work_item.lines, key=lambda l: l.line_number)
    return [l for l in lines if not is_line_removed(l.supply_detail)]


def close_order(work_item, reason: str, user_ctx) -> None:
    """Close a submitted order as the department's mistake; every live line is rejected at 0.

    Removed lines are left as they are. The department is emailed the reason.

    Raises:
        SupplyAdminError: The order is not SUBMITTED, is already closed, or the reason is invalid.
    """
    if work_item.status != WORK_ITEM_STATUS_SUBMITTED or is_order_closed(work_item):
        raise SupplyAdminError("Only a submitted order that is not closed can be closed.")
    reason = normalize_reason(reason, required=True)
    now = datetime.utcnow()
    note = CLOSED_REVIEW_NOTE.format(reason=reason)

    prior_statuses = {}
    for line in _live_lines(work_item):
        prior_statuses[str(line.line_number)] = line.status
        for review in line.reviews:
            if review.stage == REVIEW_STAGE_APPROVAL_GROUP:
                # A group's APPROVED or REJECTED stays on record.
                if review.status not in _OPEN_GROUP_STATUSES:
                    continue
            elif review.stage == REVIEW_STAGE_ADMIN_FINAL:
                # A line added at finalize carries an admin approval; it no longer stands.
                review.approved_amount_cents = 0
            else:
                continue
            review.status = REVIEW_STATUS_REJECTED
            review.note = note
            review.decided_at = now
            review.decided_by_user_id = user_ctx.user_id
        if line.supply_detail is not None:
            line.supply_detail.quantity_approved = 0
        line.status = WORK_LINE_STATUS_REJECTED
        line.approved_amount_cents = 0
        line.needs_requester_action = False
        line.status_changed_at = now
        line.status_changed_by_user_id = user_ctx.user_id

    detail = work_item.supply_order_detail
    if detail is None:
        detail = SupplyOrderDetail(work_item=work_item, created_by_user_id=user_ctx.user_id)
        db.session.add(detail)
    detail.closed_at = now
    detail.closed_by_user_id = user_ctx.user_id
    detail.closed_reason = reason

    work_item.status = WORK_ITEM_STATUS_FINALIZED
    work_item.finalized_at = now
    work_item.finalized_by_user_id = user_ctx.user_id

    db.session.add(WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_ORDER_CLOSED,
        old_value=WORK_ITEM_STATUS_SUBMITTED,
        new_value="CLOSED",
        reason=reason,
        snapshot={"prior_line_statuses": prior_statuses},
        created_by_user_id=user_ctx.user_id,
    ))

    # Membership lookups and resolve_template_key run outside the per-recipient
    # savepoint; an email failure must not undo the close.
    try:
        with enqueue_savepoint():
            notify_supply_closed(work_item, reason, now)
    except Exception:
        current_app.logger.exception(
            f"Could not queue the closed email for {work_item.public_id}; "
            f"the close stands and the department must be told by hand."
        )


def _reset_review(review: WorkLineReview) -> None:
    review.status = REVIEW_STATUS_PENDING
    review.decided_at = None
    review.decided_by_user_id = None
    review.approved_amount_cents = None
    review.note = None


def _return_line_to_review(line, user_ctx, now) -> None:
    """Send a live line on a closed order back to its review group as PENDING."""
    group_review = None
    for review in line.reviews:
        if review.stage == REVIEW_STAGE_APPROVAL_GROUP:
            group_review = review
            _reset_review(review)
        elif review.stage == REVIEW_STAGE_ADMIN_FINAL:
            _reset_review(review)

    if group_review is None:
        try:
            group = get_approval_group_for_line(line)
        except ValueError:
            group = None
        if group is None:
            # Refusing would leave the order closed for good. Unrouted, the
            # line skips the group and finalize books its requested quantity.
            logger.warning(
                f"Reopened line {line.line_number} on {line.work_item.public_id} "
                f"has no review group to return to"
            )
        else:
            set_line_routing_approval_group(line, group.id)
            db.session.add(WorkLineReview(
                work_line_id=line.id,
                stage=REVIEW_STAGE_APPROVAL_GROUP,
                approval_group_id=group.id,
                status=REVIEW_STATUS_PENDING,
                created_by_user_id=user_ctx.user_id,
            ))

    detail = line.supply_detail
    if detail is not None:
        detail.quantity_approved = None
        detail.quantity_reviewed = None
    line.status = WORK_LINE_STATUS_PENDING
    line.approved_amount_cents = None
    line.needs_requester_action = False
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    line.status_changed_at = now
    line.status_changed_by_user_id = user_ctx.user_id


def _cancel_unsent_order_emails(work_item) -> int:
    """Cancel this order's queued finalized and closed emails, which no longer describe it.

    Uses the drainer's terminal-state helper so each cancellation writes a
    NotificationLog row and frees its dedup key. A SENDING row is left to the
    drainer; a second writer here would race it.
    """
    # Imported here: email_drainer pulls in app.services.email.
    from app.services.email_drainer import _apply_terminal_state
    from app.services.email_enqueue import resolve_template_key

    work_type = work_item.portfolio.work_type if work_item.portfolio else None
    code = work_type.code if work_type else None
    keys = {resolve_template_key(kind, code) for kind in ("finalized", "closed")}
    rows = (
        EmailOutbox.query
        .filter(EmailOutbox.work_item_id == work_item.id)
        .filter(EmailOutbox.template_key.in_(keys))
        .filter(EmailOutbox.status.in_(OUTBOX_CLAIMABLE_STATUSES))
        .all()
    )
    for row in rows:
        _apply_terminal_state(
            row, OUTBOX_STATUS_CANCELLED, "Reopen withdrew this email before it sent.",
        )
    return len(rows)


def reopen_order(work_item, reason: str, user_ctx) -> None:
    """Return a finalized or closed order to SUBMITTED, with a public comment giving the reason.

    A finalized order keeps every line decision. A closed order sends every
    live line back to its review group. No email is sent, and any unsent
    finalized or closed email is cancelled.

    Raises:
        SupplyAdminError: The order is not FINALIZED, or the reason is invalid.
    """
    if work_item.status != WORK_ITEM_STATUS_FINALIZED:
        raise SupplyAdminError("Only a finalized or closed order can be reopened.")
    reason = normalize_reason(reason, required=True)
    now = datetime.utcnow()
    was_closed = is_order_closed(work_item)

    prior_statuses = {}
    if was_closed:
        for line in _live_lines(work_item):
            prior_statuses[str(line.line_number)] = line.status
            _return_line_to_review(line, user_ctx, now)
        detail = work_item.supply_order_detail
        detail.closed_at = None
        detail.closed_by_user_id = None
        detail.closed_reason = None

    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.finalized_at = None
    work_item.finalized_by_user_id = None

    db.session.add(WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_ORDER_REOPENED,
        old_value="CLOSED" if was_closed else WORK_ITEM_STATUS_FINALIZED,
        new_value=WORK_ITEM_STATUS_SUBMITTED,
        reason=reason,
        snapshot={"was_closed": was_closed, "prior_line_statuses": prior_statuses},
        created_by_user_id=user_ctx.user_id,
    ))
    db.session.add(WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=REOPENED_COMMENT.format(reason=reason),
        created_by_user_id=user_ctx.user_id,
    ))
    _cancel_unsent_order_emails(work_item)
