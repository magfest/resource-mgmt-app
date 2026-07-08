"""
Engine-level lifecycle transitions for work items.

These helpers handle the status transitions that branch on per-worktype
WorkTypeConfig flags (uses_dispatch, has_admin_final). Route handlers do
their own worktype-specific validation, then call into these helpers for
the engine work.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app import db
from app.line_details import set_line_routing_approval_group
from app.models import (
    WorkItemAuditEvent,
    WorkLine,
    WorkLineReview,
    AUDIT_EVENT_FINALIZE,
    AUDIT_EVENT_RECALL_TO_DRAFT,
    AUDIT_EVENT_SUBMIT,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routing.registry import get_approval_group_for_line
from app.routes.work.helpers.computations import compute_work_item_totals

if TYPE_CHECKING:
    from app.models import WorkItem
    from app.routes.context_types import UserContext


def submit_work_item(work_item: "WorkItem", user_ctx: "UserContext") -> str:
    """
    Apply the submit transition to a work item, branching on uses_dispatch.

    Returns the new WorkItem.status value so the caller can adjust flash text.
    Caller is responsible for committing the session.

    Branches:
    - uses_dispatch=True (BUDGET, CONTRACT): status → AWAITING_DISPATCH;
      dispatch is a separate later step that creates the WorkLineReview rows.
    - uses_dispatch=False (SUPPLY, TECHOPS, AV): routes each line via the
      worktype's strategy, snapshots the routed approval group onto each
      line's detail row, creates WorkLineReview rows inline, status → SUBMITTED.

    Lines with no resolvable routing are left without reviews; the caller's
    validation is expected to have caught that case before this helper runs.
    """
    config = work_item.portfolio.work_type.config

    work_item.submitted_at = datetime.utcnow()
    work_item.submitted_by_user_id = user_ctx.user_id

    if config.uses_dispatch:
        work_item.status = WORK_ITEM_STATUS_AWAITING_DISPATCH
    else:
        for line in work_item.lines:
            group = get_approval_group_for_line(line)
            if group is None:
                continue

            set_line_routing_approval_group(line, group.id)
            line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP

            review = WorkLineReview(
                work_line_id=line.id,
                stage=REVIEW_STAGE_APPROVAL_GROUP,
                approval_group_id=group.id,
                status=REVIEW_STATUS_PENDING,
                created_by_user_id=user_ctx.user_id,
            )
            db.session.add(review)

        work_item.status = WORK_ITEM_STATUS_SUBMITTED

    totals = compute_work_item_totals(work_item)
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_SUBMIT,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "line_count": len(work_item.lines),
            "total_requested_cents": totals.get("requested", 0),
        },
    )
    db.session.add(audit_event)

    return work_item.status


def recall_to_draft(work_item: "WorkItem", user_ctx: "UserContext") -> None:
    """
    Reverse a submit while still in AWAITING_DISPATCH.

    Caller is responsible for committing the session and for gating
    eligibility (status == AWAITING_DISPATCH, uses_dispatch=True, and the
    user holds either portfolio edit rights or worktype-admin role).

    The original AUDIT_EVENT_SUBMIT row is preserved — the audit log will
    read SUBMIT → RECALL_TO_DRAFT → SUBMIT if the requester resubmits later.
    """
    prior_status = work_item.status

    work_item.status = WORK_ITEM_STATUS_DRAFT
    work_item.submitted_at = None
    work_item.submitted_by_user_id = None

    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_RECALL_TO_DRAFT,
        created_by_user_id=user_ctx.user_id,
        snapshot={"from_status": prior_status},
    )
    db.session.add(audit_event)


def try_auto_finalize(work_item: "WorkItem", user_ctx: "UserContext") -> bool:
    """
    For worktypes with has_admin_final=False, transition to FINALIZED when
    all approval-group reviews have a terminal decision. No-op otherwise.

    Called after each review decision so the last reviewer to decide a line
    on a non-admin-final worktype completes the request automatically.

    Returns True if the auto-finalize transition fired, False otherwise.
    """
    if work_item.status == WORK_ITEM_STATUS_FINALIZED:
        return False

    portfolio = work_item.portfolio
    if not portfolio or not portfolio.work_type or not portfolio.work_type.config:
        return False

    config = portfolio.work_type.config
    if config.has_admin_final:
        return False

    reviews_query = (
        db.session.query(WorkLineReview)
        .join(WorkLine, WorkLine.id == WorkLineReview.work_line_id)
        .filter(WorkLine.work_item_id == work_item.id)
        .filter(WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP)
    )

    # Don't auto-finalize a work item that has zero approval-group reviews —
    # something pathological happened (or submit hasn't run yet).
    if reviews_query.count() == 0:
        return False

    pending_count = reviews_query.filter(
        WorkLineReview.status.in_([
            REVIEW_STATUS_PENDING,
            REVIEW_STATUS_NEEDS_INFO,
            REVIEW_STATUS_NEEDS_ADJUSTMENT,
        ])
    ).count()

    if pending_count > 0:
        return False

    work_item.status = WORK_ITEM_STATUS_FINALIZED
    work_item.finalized_at = datetime.utcnow()
    work_item.finalized_by_user_id = user_ctx.user_id

    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_FINALIZE,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "auto_finalized": True,
            "trigger": "last_review_decided",
        },
    )
    db.session.add(audit_event)

    return True
