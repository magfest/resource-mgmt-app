"""
Admin Final Review line review routes.
"""
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, request, abort, flash, jsonify

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineComment,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_RESET,
    COMMENT_VISIBILITY_PUBLIC,
    COMMENT_VISIBILITY_ADMIN,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import (
    get_portfolio_context,
    require_budget_work_type,
    format_currency,
    get_comment_visibility,
)
from . import admin_final_bp
from .helpers import (
    require_budget_admin,
    get_approval_group_review,
    get_admin_final_review,
    apply_admin_final_decision,
    reset_line_for_rereview,
)


def _get_work_item_and_line(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Get work item and line, validating they exist."""
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    line = WorkLine.query.filter_by(
        work_item_id=work_item.id,
        line_number=line_num,
    ).first()

    if not line:
        abort(404, f"Line not found: {line_num}")

    return work_item, line, ctx


@admin_final_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-review")
@admin_final_bp.get("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-review")
def line_review(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """
    Admin final review page for a specific line.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Get reviews
    ag_review = get_approval_group_review(line)
    admin_review = get_admin_final_review(line)

    # Get line details
    detail = line.budget_detail

    # Calculate line total
    if detail:
        line_total = detail.unit_price_cents * int(detail.quantity)
    else:
        line_total = 0

    # Get recommended amount from approval group review
    if ag_review:
        recommended_amount = ag_review.approved_amount_cents
    else:
        recommended_amount = None

    # Get comments
    comments = line.comments

    # Get audit events
    audit_events = line.audit_events

    return render_template(
        "admin_final/line_review.html",
        ctx=ctx,
        user_ctx=user_ctx,
        work_item=work_item,
        line=line,
        detail=detail,
        ag_review=ag_review,
        admin_review=admin_review,
        line_total=line_total,
        recommended_amount=recommended_amount,
        comments=comments,
        audit_events=audit_events,
        format_currency=format_currency,
    )


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-approve")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-approve")
def line_approve(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Admin approve a line."""
    return _handle_admin_decision(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_APPROVE)


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-reject")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-reject")
def line_reject(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Admin reject a line."""
    return _handle_admin_decision(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_REJECT)


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-needs-info")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-needs-info")
def line_needs_info(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Admin request more info for a line."""
    return _handle_admin_decision(event, dept, public_id, line_num, work_type_slug, REVIEW_ACTION_NEEDS_INFO)


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-reset")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-reset")
def line_reset(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Admin reset a line for re-review."""
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    success, error = reset_line_for_rereview(line, user_ctx)

    if not success:
        flash(error, "error")
    else:
        flash(f"Line {line_num} reset for re-review.", "success")
        db.session.commit()

    return redirect(url_for(
        "admin_final.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num
    ))


def _handle_admin_decision(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str, action: str):
    """
    Common handler for admin final review decisions.
    Returns JSON if ajax=1 in form data, otherwise redirects.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    # Check if this is an AJAX request
    is_ajax = request.form.get("ajax") == "1"

    # Get form data
    note = (request.form.get("note") or "").strip()

    # Parse amount if provided
    amount_cents = None
    amount_str = (request.form.get("approved_amount") or "").strip()
    if amount_str:
        try:
            amount_dollars = Decimal(amount_str.replace(",", "").replace("$", ""))
            amount_cents = int(amount_dollars * 100)
        except (ValueError, InvalidOperation):
            if is_ajax:
                return jsonify({"success": False, "error": "Invalid amount format."})
            flash("Invalid amount format.", "error")
            return redirect(url_for(
                "admin_final.line_review",
                event=event,
                dept=dept,
                public_id=public_id,
                line_num=line_num
            ))

    # Apply decision
    success, error = apply_admin_final_decision(
        line=line,
        work_item=work_item,
        action=action,
        approved_amount_cents=amount_cents,
        note=note,
        user_ctx=user_ctx,
    )

    if not success:
        if is_ajax:
            return jsonify({"success": False, "error": error})
        flash(error, "error")
    else:
        action_labels = {
            REVIEW_ACTION_APPROVE: "approved",
            REVIEW_ACTION_REJECT: "rejected",
            REVIEW_ACTION_NEEDS_INFO: "marked as needing information",
        }

        # Add comment if note provided
        if note:
            prefix_map = {
                REVIEW_ACTION_APPROVE: "[ADMIN APPROVED]",
                REVIEW_ACTION_REJECT: "[ADMIN REJECTED]",
                REVIEW_ACTION_NEEDS_INFO: "[ADMIN INFO REQUESTED]",
            }
            visibility = get_comment_visibility(request.form, user_ctx.is_super_admin)
            comment = WorkLineComment(
                work_line_id=line.id,
                visibility=visibility,
                body=f"{prefix_map.get(action, '[ADMIN]')} {note}",
                created_by_user_id=user_ctx.user_id,
            )
            db.session.add(comment)

        db.session.commit()

        if is_ajax:
            return jsonify({
                "success": True,
                "line_num": line_num,
                "new_status": line.status,
                "message": f"Line {line_num} {action_labels.get(action, 'updated')}."
            })

        flash(f"Line {line_num} {action_labels.get(action, 'updated')}.", "success")

    return redirect(url_for(
        "admin_final.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num
    ))
