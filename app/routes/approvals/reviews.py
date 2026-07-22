"""
Line review routes - individual line review with decision actions.
"""
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, request, abort, flash, jsonify
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineComment,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_APPROVE_NEEDS_REVIEW,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_NEEDS_ADJUSTMENT,
    REVIEW_ACTION_RESET,
    REVIEW_ACTION_RESPOND,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    COMMENT_VISIBILITY_PUBLIC,
    COMMENT_VISIBILITY_ADMIN,
)
from app.line_details import (
    get_line_amount_cents,
    get_line_detail,
    get_line_routing_approval_group,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import (
    get_portfolio_context,
    require_work_item_view,
    build_work_item_perms,
    format_currency,
    friendly_status,
    get_comment_visibility,
    is_checked_out,
)
from . import approvals_bp
from .helpers import (
    is_reviewer_for_line,
    can_respond_to_work_item,
    get_review_for_line,
    get_or_create_review,
    apply_review_decision,
    audit_line_field_changes,
)
from app.routes.admin_final.helpers import (
    get_admin_final_review,
    get_approval_group_review,
)


# ============================================================
# Helper Functions
# ============================================================

def get_work_item_and_line(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """
    Get work item and line, validating they exist and belong together.

    Polymorphic across worktypes — every line detail relationship is
    eager-loaded so callers can use get_line_detail() without N+1, and
    the worktype guard lives in the per-action handler (e.g. line_adjust
    is BUDGET-only, line_review is polymorphic).

    Returns tuple of (work_item, line, ctx).
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        joinedload(WorkItem.portfolio),
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    line = WorkLine.query.filter_by(
        work_item_id=work_item.id,
        line_number=line_num,
    ).options(
        joinedload(WorkLine.budget_detail),
        joinedload(WorkLine.contract_detail),
        joinedload(WorkLine.supply_detail),
        joinedload(WorkLine.techops_detail),
        selectinload(WorkLine.comments),
        selectinload(WorkLine.audit_events),
    ).first()

    if not line:
        abort(404, f"Line not found: {line_num}")

    return work_item, line, ctx


# ============================================================
# Line Review View
# ============================================================

@approvals_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/review")
@approvals_bp.get("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/review")
def line_review(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """
    View and review a specific budget line.
    """
    user_ctx = get_user_ctx()
    work_item, line, ctx = get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Check view permission
    perms = require_work_item_view(work_item, ctx)

    # Check if user can access this specific line (approval group filtering)
    # Polymorphic: get_line_routing_approval_group dispatches by detail type,
    # so this works for BUDGET / TECHOPS / future worktypes alike.
    if not user_ctx.is_super_admin:
        routed_group = get_line_routing_approval_group(line)
        routed_group_id = routed_group.id if routed_group else None
        is_in_routed_group = routed_group_id and routed_group_id in user_ctx.approval_group_ids
        is_requester = can_respond_to_work_item(work_item, ctx, user_ctx)

        if not is_in_routed_group and not is_requester:
            abort(403, "You do not have permission to view this line.")

    # Get or create review record
    review = get_review_for_line(line)

    # Check if user can review this line
    can_review = is_reviewer_for_line(line, user_ctx)
    has_checkout = work_item.checked_out_by_user_id == user_ctx.user_id
    can_decide = can_review and has_checkout and review and review.status == REVIEW_STATUS_PENDING

    # Check if user can respond to kicked-back line
    can_respond = (
        line.needs_requester_action and
        review and
        review.status in (REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT) and
        can_respond_to_work_item(work_item, ctx, user_ctx)
    )

    # Polymorphic line detail + total. For non-monetary worktypes
    # (TECHOPS) get_line_amount_cents returns 0, which the templates
    # that don't render an amount column simply ignore.
    detail = get_line_detail(line)
    line_total = get_line_amount_cents(line)

    # Get comments for this line (filter admin-only for non-admins)
    comments = line.comments
    # Reviewers of this line are a trusted group and may see ADMIN notes.
    # Requesters (and other non-reviewers) still cannot.
    can_see_admin_notes = user_ctx.is_super_admin or can_review
    if not can_see_admin_notes:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    # Get audit events for this line
    audit_events = line.audit_events

    # Admin Final review tab is BUDGET-only — non-BUDGET worktypes have
    # has_admin_final=False, so don't bother loading those review rows.
    admin_review = None
    ag_review = None
    if user_ctx.is_super_admin and work_type_slug == "budget":
        admin_review = get_admin_final_review(line)
        ag_review = get_approval_group_review(line)

    # Pick the per-worktype template. Each work_type/ directory owns its
    # own line_review.html (BUDGET has the multi-stage admin-final UI,
    # TECHOPS has a simpler service-shaped form, etc.).
    template_name = f"{work_type_slug}/line_review.html"

    return render_template(
        template_name,
        ctx=ctx,
        perms=perms,
        user_ctx=user_ctx,
        work_item=work_item,
        line=line,
        detail=detail,
        review=review,
        line_total=line_total,
        comments=comments,
        audit_events=audit_events,
        can_review=can_review,
        has_checkout=has_checkout,
        can_decide=can_decide,
        can_respond=can_respond,
        is_checked_out=is_checked_out(work_item),
        format_currency=format_currency,
        friendly_status=friendly_status,
        # Admin extras (None for non-BUDGET worktypes)
        admin_review=admin_review,
        ag_review=ag_review,
    )


# ============================================================
# Review Decision Actions
# ============================================================

@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/approve")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/approve")
def line_approve(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Approve a line."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_APPROVE)


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/approve-needs-review")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/approve-needs-review")
def line_approve_needs_review(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Approve a line but flag it for admin final review."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_APPROVE_NEEDS_REVIEW)


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/reject")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/reject")
def line_reject(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Reject a line."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_REJECT)


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/needs-info")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/needs-info")
def line_needs_info(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Request more information for a line."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_NEEDS_INFO)


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/needs-adjustment")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/needs-adjustment")
def line_needs_adjustment(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Request adjustment for a line."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_NEEDS_ADJUSTMENT)


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/reset")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/reset")
def line_reset(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Reset a line back to pending (admin only)."""
    return _handle_review_action(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_RESET)


def _handle_review_action(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str, action: str):
    """
    Common handler for all review actions.
    Returns JSON if ajax=1 in form data, otherwise redirects.
    """
    user_ctx = get_user_ctx()
    work_item, line, ctx = get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Check if this is an AJAX request
    is_ajax = request.form.get("ajax") == "1"

    # Get or create review
    review, _created = get_or_create_review(line, user_ctx)

    # Get form data
    note = (request.form.get("note") or "").strip()

    # Parse amount if provided (for approvals)
    amount_cents = None
    amount_str = (request.form.get("recommended_amount") or "").strip()
    if amount_str:
        try:
            # Parse as dollars, convert to cents
            amount_dollars = Decimal(amount_str.replace(",", "").replace("$", ""))
            amount_cents = int(amount_dollars * 100)
        except (ValueError, InvalidOperation):
            pass  # Ignore invalid amounts

    # Apply the decision
    success, error = apply_review_decision(
        review=review,
        line=line,
        work_item=work_item,
        action=action,
        note=note,
        amount_cents=amount_cents,
        user_ctx=user_ctx,
        ctx=ctx,
    )

    if not success:
        if is_ajax:
            return jsonify({"success": False, "error": error})
        flash(error, "error")
    else:
        action_labels = {
            REVIEW_ACTION_APPROVE: "approved",
            REVIEW_ACTION_APPROVE_NEEDS_REVIEW: "approved (flagged for admin review)",
            REVIEW_ACTION_REJECT: "rejected",
            REVIEW_ACTION_NEEDS_INFO: "marked as needing information",
            REVIEW_ACTION_NEEDS_ADJUSTMENT: "marked as needing adjustment",
            REVIEW_ACTION_RESET: "reset to pending",
        }

        # Add comment with the note if provided
        if note:
            prefix_map = {
                REVIEW_ACTION_APPROVE: "[APPROVED]",
                REVIEW_ACTION_APPROVE_NEEDS_REVIEW: "[APPROVED – NEEDS REVIEW]",
                REVIEW_ACTION_REJECT: "[REJECTED]",
                REVIEW_ACTION_NEEDS_INFO: "[INFO REQUESTED]",
                REVIEW_ACTION_NEEDS_ADJUSTMENT: "[ADJUSTMENT REQUESTED]",
                REVIEW_ACTION_RESET: "[RESET]",
            }
            # Determine comment visibility
            visibility = get_comment_visibility(
                request.form, user_ctx.is_super_admin or is_reviewer_for_line(line, user_ctx)
            )
            comment = WorkLineComment(
                work_line_id=line.id,
                visibility=visibility,
                body=f"{prefix_map.get(action, '[REVIEW]')} {note}",
                created_by_user_id=user_ctx.user_id,
            )
            db.session.add(comment)

        db.session.commit()

        # Send notification if line was kicked back (NEEDS_INFO or NEEDS_ADJUSTMENT)
        if action in (REVIEW_ACTION_NEEDS_INFO, REVIEW_ACTION_NEEDS_ADJUSTMENT):
            try:
                from app.services.notifications import notify_needs_attention
                notify_needs_attention(work_item)
                db.session.commit()  # Commit notification log
            except Exception:
                db.session.rollback()
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to send needs_attention notification for %s", work_item.public_id
                )

        if is_ajax:
            return jsonify({
                "success": True,
                "line_num": line_num,
                "new_status": line.status,
                "message": f"Line {line_num} {action_labels.get(action, 'updated')}."
            })

        flash(f"Line {line_num} {action_labels.get(action, 'updated')}.", "success")

    return redirect(url_for(
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
    ))


# ============================================================
# Requester Response Route
# ============================================================

@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/respond")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/respond")
def line_respond(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """
    Requester responds to NEEDS_INFO or NEEDS_ADJUSTMENT.
    """
    user_ctx = get_user_ctx()
    work_item, line, ctx = get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Get review
    review = get_review_for_line(line)
    if not review:
        flash("No review found for this line.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Validate that line needs requester action
    if review.status not in (REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT):
        flash("This line is not awaiting your response.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Get response text
    response_text = (request.form.get("response") or "").strip()
    if not response_text:
        flash("A response is required.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Capture the reviewer who asked for info BEFORE applying the response.
    # apply_review_decision overwrites review.decided_by_user_id with the
    # acting user (the responder), so reading it afterward would target the
    # requester instead of the reviewer we mean to notify.
    reviewer_user_id = review.decided_by_user_id

    # Apply the response
    success, error = apply_review_decision(
        review=review,
        line=line,
        work_item=work_item,
        action=REVIEW_ACTION_RESPOND,
        note=response_text,
        amount_cents=None,
        user_ctx=user_ctx,
        ctx=ctx,
    )

    if not success:
        flash(error, "error")
    else:
        flash("Response submitted. The line is back in review.", "success")

        # Add comment with the response
        visibility = get_comment_visibility(
            request.form, user_ctx.is_super_admin or is_reviewer_for_line(line, user_ctx)
        )
        comment = WorkLineComment(
            work_line_id=line.id,
            visibility=visibility,
            body=f"[RESPONSE] {response_text}",
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(comment)
        db.session.commit()

        # Notify the reviewer that a response was received (non-blocking)
        if reviewer_user_id:
            try:
                from app.services.notifications import notify_response_received
                notify_response_received(work_item, reviewer_user_id)
                db.session.commit()  # Commit notification log
            except Exception:
                db.session.rollback()
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to send response_received notification for %s", work_item.public_id
                )

    return redirect(url_for(
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
    ))


@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/adjust")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/adjust")
def line_adjust(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """
    Requester adjusts line details and responds to NEEDS_ADJUSTMENT.

    BUDGET-only — the form fields it edits (quantity, unit_price,
    description) are budget_detail-shaped. Other worktypes that need a
    similar requester-edits-line flow get their own per-worktype
    handler; for now they should use NEEDS_INFO + line_respond instead.
    """
    from decimal import Decimal, InvalidOperation

    user_ctx = get_user_ctx()
    work_item, line, ctx = get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Hard guard: this handler manipulates budget_detail directly. The
    # TechOps line_review template hides the NEEDS_ADJUSTMENT button so
    # this path shouldn't be reachable, but if a reviewer somehow
    # triggers it anyway, fail clearly rather than corrupting the line.
    if not line.budget_detail:
        flash(
            "Adjustment is not supported for this work type. "
            "Use 'Need Info' for a text-only response instead.",
            "error",
        )
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Get review
    review = get_review_for_line(line)
    if not review:
        flash("No review found for this line.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Validate that line is in NEEDS_ADJUSTMENT status
    if review.status != REVIEW_STATUS_NEEDS_ADJUSTMENT:
        flash("This line is not awaiting adjustment.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Validate user can respond
    if not can_respond_to_work_item(work_item, ctx, user_ctx):
        flash("You do not have permission to adjust this line.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Get form data
    response_text = (request.form.get("response") or "").strip()
    if not response_text:
        flash("Please describe what you changed.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Parse and validate line detail changes
    detail = line.budget_detail
    if not detail:
        flash("Line has no budget details.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    # Track what changed for the comment
    changes = []
    # Track structured changes for audit events
    audit_changes = []

    # Capture old values before mutations
    old_qty = detail.quantity
    old_price_cents = detail.unit_price_cents
    old_description = detail.description or ""

    # Quantity
    qty_str = (request.form.get("quantity") or "").strip()
    if qty_str:
        try:
            new_qty = Decimal(qty_str)
            if new_qty <= 0:
                flash("Quantity must be a positive number.", "error")
                return redirect(url_for(
                    "approvals.line_review",
                    event=event,
                    dept=dept,
                    public_id=public_id,
                    line_num=line_num,
                    work_type_slug=work_type_slug,
                ))
            if new_qty != detail.quantity:
                changes.append(f"Quantity: {detail.quantity} → {new_qty}")
                audit_changes.append(("quantity", str(old_qty), str(new_qty)))
                detail.quantity = new_qty
        except InvalidOperation:
            flash("Invalid quantity value.", "error")
            return redirect(url_for(
                "approvals.line_review",
                event=event,
                dept=dept,
                public_id=public_id,
                line_num=line_num,
                work_type_slug=work_type_slug,
            ))

    # Unit price
    price_str = (request.form.get("unit_price") or "").strip()
    if price_str:
        try:
            new_price_dollars = Decimal(price_str)
            new_price_cents = int(new_price_dollars * 100)
            if new_price_cents != detail.unit_price_cents:
                old_price = detail.unit_price_cents / 100
                changes.append(f"Unit price: ${old_price:.2f} → ${new_price_dollars:.2f}")
                audit_changes.append(("unit_price", f"${old_price_cents / 100:,.2f}", f"${new_price_cents / 100:,.2f}"))
                detail.unit_price_cents = new_price_cents
        except (ValueError, TypeError, InvalidOperation):
            flash("Invalid unit price value.", "error")
            return redirect(url_for(
                "approvals.line_review",
                event=event,
                dept=dept,
                public_id=public_id,
                line_num=line_num,
                work_type_slug=work_type_slug,
            ))

    # Description
    new_description = (request.form.get("description") or "").strip()
    if new_description != (detail.description or ""):
        if detail.description:
            changes.append("Description updated")
        else:
            changes.append("Description added")
        audit_changes.append(("description", old_description, new_description))
        detail.description = new_description or None

    # Capture the reviewer who requested the adjustment BEFORE applying the
    # response. apply_review_decision overwrites review.decided_by_user_id
    # with the acting user (the responder), so reading it afterward would
    # target the requester instead of the reviewer we mean to notify.
    reviewer_user_id = review.decided_by_user_id

    # Apply the status transition (back to PENDING)
    success, error = apply_review_decision(
        review=review,
        line=line,
        work_item=work_item,
        action=REVIEW_ACTION_RESPOND,
        note=response_text,
        amount_cents=None,
        user_ctx=user_ctx,
        ctx=ctx,
    )

    if not success:
        flash(error, "error")
    else:
        # Create structured audit events for field changes
        if audit_changes:
            audit_line_field_changes(line, audit_changes, user_ctx)

        flash("Adjustment submitted. The line is back in review.", "success")

        # Build comment body with changes
        changes_text = ", ".join(changes) if changes else "No field changes"
        comment_body = f"[ADJUSTMENT] {changes_text}\n\n{response_text}"

        visibility = get_comment_visibility(
            request.form, user_ctx.is_super_admin or is_reviewer_for_line(line, user_ctx)
        )
        comment = WorkLineComment(
            work_line_id=line.id,
            visibility=visibility,
            body=comment_body,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(comment)
        db.session.commit()

        # Notify the reviewer that a response was received (non-blocking)
        if reviewer_user_id:
            try:
                from app.services.notifications import notify_response_received
                notify_response_received(work_item, reviewer_user_id)
                db.session.commit()  # Commit notification log
            except Exception:
                db.session.rollback()
                import logging
                logging.getLogger(__name__).exception(
                    "Failed to send response_received notification for %s", work_item.public_id
                )

    return redirect(url_for(
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
    ))


# ============================================================
# Standalone Comment Route
# ============================================================

@approvals_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/comment")
@approvals_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/comment")
def line_comment(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Add a standalone comment to a line."""
    user_ctx = get_user_ctx()
    work_item, line, ctx = get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Permission check: must be reviewer for this line
    if not is_reviewer_for_line(line, user_ctx):
        flash("You do not have permission to comment on this line.", "error")
        return redirect(url_for("approvals.line_review", event=event, dept=dept,
                                public_id=public_id, line_num=line_num, work_type_slug=work_type_slug))

    comment_text = (request.form.get("comment") or "").strip()
    if not comment_text:
        flash("Comment text is required.", "error")
        return redirect(url_for("approvals.line_review", event=event, dept=dept,
                                public_id=public_id, line_num=line_num, work_type_slug=work_type_slug))

    visibility = get_comment_visibility(
        request.form, user_ctx.is_super_admin or is_reviewer_for_line(line, user_ctx)
    )
    comment = WorkLineComment(
        work_line_id=line.id,
        visibility=visibility,
        body=f"[COMMENT] {comment_text}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)
    db.session.commit()

    flash("Comment added.", "success")
    return redirect(url_for("approvals.line_review", event=event, dept=dept,
                            public_id=public_id, line_num=line_num, work_type_slug=work_type_slug))
