"""
Work item action routes - submit, checkout, checkin, needs_info.
"""
from datetime import datetime

from flask import redirect, url_for, request, flash

from app import db
from app.models import (
    WorkItemComment,
    WorkItemAuditEvent,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    COMMENT_VISIBILITY_PUBLIC,
    AUDIT_EVENT_NEEDS_INFO_REQUESTED,
    AUDIT_EVENT_NEEDS_INFO_RESPONDED,
    AUDIT_EVENT_CHECKOUT,
    AUDIT_EVENT_CHECKIN,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    require_work_item_view,
    require_work_item_edit,
    compute_work_item_totals,
    checkout_work_item,
    checkin_work_item,
)
from ..helpers.lifecycle import recall_to_draft, submit_work_item
from .common import get_work_item_by_public_id


# ============================================================
# Submit Route
# ============================================================

@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/submit")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/submit")
def work_item_submit(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Submit a DRAFT work item for review.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_edit(work_item, ctx)

    # Validate: status must be DRAFT
    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("Only DRAFT requests can be submitted.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: must have at least 1 line
    if len(work_item.lines) == 0:
        flash("Cannot submit: request has no lines.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: all lines must have budget details with expense accounts
    for line in work_item.lines:
        if not line.budget_detail:
            flash(f"Cannot submit: line {line.line_number} is missing budget details.", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))
        expense_account = line.budget_detail.expense_account
        if not expense_account:
            flash(f"Cannot submit: line {line.line_number} has no expense account.", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))

    user_ctx = get_user_ctx()

    # Apply the submit transition. Branches on uses_dispatch:
    # - True (BUDGET): status → AWAITING_DISPATCH; dispatch creates reviews later.
    # - False (TECHOPS): routes lines and creates reviews inline; status → SUBMITTED.
    submit_work_item(work_item, user_ctx)

    db.session.commit()

    # Send notification (non-blocking) — recipients depend on uses_dispatch
    try:
        from app.services.notifications import notify_work_item_submitted
        notify_work_item_submitted(work_item)
        db.session.commit()  # Commit notification log
    except Exception:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send submission notification for %s", work_item.public_id
        )

    # Send the BUDGET-only confirmation back to the submitting department
    # in a separate non-blocking block so a failure here cannot roll back
    # the admin-notification log committed above.
    try:
        from app.services.notifications import notify_submission_confirmation
        notify_submission_confirmation(work_item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send submission confirmation for %s", work_item.public_id
        )

    flash(
        "Budget request submitted! A budget admin will assign reviewers and "
        "dispatch it for approval. You'll be notified if any changes are needed.",
        "success"
    )
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    ))


# ============================================================
# Recall Route
# ============================================================

@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/recall")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/recall")
def work_item_recall(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Recall an AWAITING_DISPATCH work item back to DRAFT.

    Allowed for portfolio editors and worktype admins. The submit transition
    only set timestamps and a single audit row at this state — no reviews
    exist yet, so this is a clean reversal.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    redirect_to_detail = redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    ))

    if not perms.can_recall:
        flash("This request can't be recalled to draft right now.", "error")
        return redirect_to_detail

    user_ctx = get_user_ctx()
    recall_to_draft(work_item, user_ctx)
    db.session.commit()

    flash("Request recalled to draft. You can edit it now.", "success")
    return redirect_to_detail


# ============================================================
# Checkout Routes
# ============================================================

@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/checkout")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/checkout")
def work_item_checkout(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Checkout a work item for review.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    # Get optional return_to URL from form data
    from app.routes.admin.helpers import safe_redirect_url
    return_to = safe_redirect_url(request.form.get("return_to"), fallback="")

    default_redirect = url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    )

    if not perms.can_checkout:
        flash("You cannot start a review session for this budget request.", "error")
        return redirect(return_to or default_redirect)

    user_ctx = get_user_ctx()
    if checkout_work_item(work_item, user_ctx):
        # Create audit event for checkout
        audit_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_CHECKOUT,
            created_by_user_id=user_ctx.user_id,
            snapshot={
                "expires_at": work_item.checked_out_expires_at.isoformat() if work_item.checked_out_expires_at else None,
            },
        )
        db.session.add(audit_event)
        db.session.commit()
        flash("Review session started.", "success")
    else:
        flash("Could not start review session.", "error")

    return redirect(return_to or default_redirect)


@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/checkin")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/checkin")
def work_item_checkin(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Release checkout (check-in) on a work item.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    # Get optional return_to URL from form data
    from app.routes.admin.helpers import safe_redirect_url
    return_to = safe_redirect_url(request.form.get("return_to"), fallback="")

    default_redirect = url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    )

    if not perms.can_checkin:
        flash("You cannot end this review session.", "error")
        return redirect(return_to or default_redirect)

    user_ctx = get_user_ctx()
    force = perms.is_worktype_admin and not perms.is_checked_out_by_current_user

    # Capture who had checkout before releasing (for audit)
    previous_holder = work_item.checked_out_by_user_id

    if checkin_work_item(work_item, user_ctx, force=force):
        # Create audit event for checkin
        audit_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_CHECKIN,
            created_by_user_id=user_ctx.user_id,
            snapshot={
                "previous_holder": previous_holder,
                "forced": force,
            },
        )
        db.session.add(audit_event)
        db.session.commit()
        flash("Review session ended.", "success")
    else:
        flash("Could not end review session.", "error")

    return redirect(return_to or default_redirect)


# ============================================================
# NEEDS_INFO Routes
# ============================================================

@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/request-info")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/request-info")
def work_item_request_info(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Request information from the requester (sets status to NEEDS_INFO).
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    if not perms.can_request_info:
        flash("You cannot request information on this work item.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("A message is required when requesting information.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()

    # Add request-level comment
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=f"[INFO REQUESTED] {message}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)

    # Update work item status
    work_item.status = WORK_ITEM_STATUS_NEEDS_INFO
    work_item.needs_info_requested_at = datetime.utcnow()
    work_item.needs_info_requested_by_user_id = user_ctx.user_id

    # Create audit event
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_NEEDS_INFO_REQUESTED,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "message": message,
        },
    )
    db.session.add(audit_event)

    # Release checkout
    checkin_work_item(work_item, user_ctx)

    db.session.commit()

    flash("Information requested. The requester has been notified.", "success")
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    ))


@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/respond-info")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/respond-info")
def work_item_respond_info(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Respond to information request (sets status back to SUBMITTED).
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    if not perms.can_respond_to_info:
        flash("You cannot respond to this information request.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    response = (request.form.get("response") or "").strip()
    if not response:
        flash("A response is required.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()

    # Add request-level comment
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=f"[INFO RESPONSE] {response}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)

    # Update work item status back to SUBMITTED
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.needs_info_requested_at = None
    work_item.needs_info_requested_by_user_id = None

    # Create audit event
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_NEEDS_INFO_RESPONDED,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "response": response,
        },
    )
    db.session.add(audit_event)

    db.session.commit()

    flash("Response submitted. The request is back in review.", "success")
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id,
        work_type_slug=work_type_slug,
    ))
