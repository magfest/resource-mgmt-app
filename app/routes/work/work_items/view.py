"""
Work item view routes - detail view, comments, quick review.
"""
from flask import render_template, redirect, url_for, request, flash

from app import db
from app.models import (
    WorkItemComment,
    WorkItemAuditEvent,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    COMMENT_VISIBILITY_ADMIN,
    COMMENT_VISIBILITY_PUBLIC,
    AUDIT_EVENT_VIEW,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    require_work_item_view,
    compute_work_item_totals,
    format_currency,
    friendly_status,
    get_comment_visibility,
    is_checked_out,
    _is_approver_for_work_item,
    filter_lines_for_user,
    get_kicked_back_lines_summary,
    get_unified_audit_events,
)
from app.routes.admin_final.helpers import (
    can_finalize_work_item,
    get_finalization_summary,
)
from .common import get_work_item_by_public_id


# ============================================================
# Work Item Detail/View Routes
# ============================================================

@work_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>")
@work_bp.get("/<event>/<dept>/budget/item/<public_id>")
def work_item_detail(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    View a work item and its lines.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)
    user_ctx = get_user_ctx()

    # Log view for non-draft items when viewed by someone other than the requester
    is_requester = work_item.created_by_user_id == user_ctx.user_id
    if work_item.status != WORK_ITEM_STATUS_DRAFT and not is_requester:
        view_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_VIEW,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(view_event)
        db.session.commit()

    # Compute totals (from ALL lines for context)
    totals = compute_work_item_totals(work_item)

    # Check if user is a department member (requester/dept member should see all lines)
    # This is different from perms.can_view which includes reviewer access
    has_dept_membership = (
        work_item.created_by_user_id == user_ctx.user_id or
        (ctx.membership and ctx.membership.can_view_work_type(ctx.work_type.id)) or
        (ctx.division_membership and ctx.division_membership.can_view_work_type(ctx.work_type.id))
    )

    # Get all lines and filter for display based on user access
    all_lines = list(work_item.lines)
    lines, lines_filtered = filter_lines_for_user(
        all_lines,
        user_ctx,
        is_worktype_admin=perms.is_worktype_admin,
        has_edit_access=has_dept_membership,  # Dept members/requesters see all lines
    )
    total_lines_count = len(all_lines)

    # Get kicked-back lines (NEEDS_INFO or NEEDS_ADJUSTMENT) with their review notes
    kicked_back_lines = get_kicked_back_lines_summary(lines)

    # Check if can finalize (for admins - allowed from AWAITING_DISPATCH or SUBMITTED)
    can_finalize = False
    finalization_summary = None
    if perms.is_worktype_admin and work_item.status in (WORK_ITEM_STATUS_AWAITING_DISPATCH, WORK_ITEM_STATUS_SUBMITTED):
        can_finalize, _ = can_finalize_work_item(work_item)
        finalization_summary = get_finalization_summary(work_item)

    # Filter comments for non-admins
    comments = list(work_item.comments)
    if not perms.is_worktype_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    # Check if user can add comments (admin OR reviewer for any line)
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_add_comment = perms.is_worktype_admin or is_approver_for_item

    # Fetch audit events for budget admins (super admin or worktype admin)
    can_view_audit = user_ctx.is_super_admin or perms.is_worktype_admin
    audit_events = get_unified_audit_events(work_item) if can_view_audit else []

    return render_template(
        "budget/work_item_detail.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        lines=lines,
        totals=totals,
        total_lines_count=total_lines_count,
        lines_filtered=lines_filtered,
        format_currency=format_currency,
        friendly_status=friendly_status,
        kicked_back_lines=kicked_back_lines,
        can_finalize=can_finalize,
        finalization_summary=finalization_summary,
        filtered_comments=comments,
        can_add_comment=can_add_comment,
        audit_events=audit_events,
        can_view_audit=can_view_audit,
        user_ctx=user_ctx,
    )


@work_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/comment")
@work_bp.post("/<event>/<dept>/budget/item/<public_id>/comment")
def work_item_comment(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """Add a standalone comment to a work item."""
    user_ctx = get_user_ctx()
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    # Get return URL (for redirecting back to edit page if that's where they came from)
    from app.routes.admin.helpers import safe_redirect_url
    return_to = safe_redirect_url(request.form.get("return_to"), fallback="")
    default_redirect = url_for("work.work_item_detail", event=event, dept=dept,
                               public_id=public_id)

    # Permission check: must be admin OR approver OR can edit (requester)
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_comment = perms.is_worktype_admin or is_approver_for_item or perms.can_edit
    if not can_comment:
        flash("You do not have permission to comment on this request.", "error")
        return redirect(return_to or default_redirect)

    comment_text = (request.form.get("comment") or "").strip()
    if not comment_text:
        flash("Comment text is required.", "error")
        return redirect(return_to or default_redirect)

    visibility = get_comment_visibility(request.form, user_ctx.is_super_admin)
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=visibility,
        body=comment_text,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)
    db.session.commit()

    flash("Comment added.", "success")
    # If returning to the edit page, keep the notes tab active
    if return_to and "edit" in return_to:
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id,
            tab="notes"
        ))
    return redirect(return_to or default_redirect)


# ============================================================
# Quick Review Route
# ============================================================

@work_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>/quick-review")
@work_bp.get("/<event>/<dept>/budget/item/<public_id>/quick-review")
def quick_review(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """
    Quick review page - shows all lines with inline action buttons.
    Designed for rapid review without navigating into each line.
    """
    user_ctx = get_user_ctx()
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id, work_type_slug)
    perms = require_work_item_view(work_item, ctx)

    # Must be a reviewer or admin to use quick review
    if not (perms.is_worktype_admin or _is_approver_for_work_item(work_item, user_ctx)):
        flash("You don't have permission to review this request.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Get checkout info
    checked_out = is_checked_out(work_item)
    has_checkout = work_item.checked_out_by_user_id == user_ctx.user_id
    can_checkout = perms.can_checkout

    # Filter lines for approval group users
    all_lines = list(work_item.lines)
    visible_lines, lines_filtered = filter_lines_for_user(
        all_lines,
        user_ctx,
        is_worktype_admin=user_ctx.is_super_admin,
        has_edit_access=False,  # Quick review is for reviewers only
    )
    total_lines_count = len(all_lines)

    # Batch load reviews for all visible lines (avoids N+1 queries)
    from app.routes.admin_final.helpers import batch_load_reviews_by_line
    visible_line_ids = [line.id for line in visible_lines]
    reviews_by_line = batch_load_reviews_by_line(visible_line_ids)

    # Build line data only for visible lines
    lines_data = []
    summary = {"pending": 0, "approved": 0, "kicked_back": 0, "rejected": 0}

    for line in visible_lines:
        detail = line.budget_detail
        # Get approval group review from batch-loaded data
        review = reviews_by_line.get(line.id, {}).get('ag')
        total_cents = detail.unit_price_cents * int(detail.quantity) if detail else 0

        # Update summary
        status = line.status.upper() if line.status else "PENDING"
        if status == "PENDING":
            summary["pending"] += 1
        elif status == "APPROVED":
            summary["approved"] += 1
        elif status in ("NEEDS_INFO", "NEEDS_ADJUSTMENT"):
            summary["kicked_back"] += 1
        elif status == "REJECTED":
            summary["rejected"] += 1

        lines_data.append({
            "line": line,
            "detail": detail,
            "review": review,
            "total_cents": total_cents,
        })

    return render_template(
        "budget/quick_review.html",
        ctx=ctx,
        perms=perms,
        user_ctx=user_ctx,
        work_item=work_item,
        lines=lines_data,
        total_lines_count=total_lines_count,
        lines_filtered=lines_filtered,
        summary=summary,
        is_checked_out=checked_out,
        has_checkout=has_checkout,
        can_checkout=can_checkout,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )
