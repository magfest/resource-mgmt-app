"""
Admin Final Review line review routes.
"""
from decimal import Decimal, InvalidOperation

from flask import redirect, url_for, request, abort, flash, jsonify

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineComment,
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    COMMENT_VISIBILITY_PUBLIC,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import (
    get_portfolio_context,
    require_budget_work_type,
)
from app.routes.work.helpers.checkout import user_holds_checkout
from app.routes.work.helpers.review_state import (
    get_line_review_state,
    AWAITING_ADMIN,
    AWAITING_REVIEWER_GROUP,
)
from . import admin_final_bp
from .helpers import (
    require_budget_admin,
    apply_admin_final_decision,
    reset_line_for_rereview,
    return_line_to_reviewer_group,
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

    if not user_holds_checkout(work_item, user_ctx):
        flash("You must check out this item before making an admin decision.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    success, error = reset_line_for_rereview(line, user_ctx)

    if not success:
        flash(error, "error")
    else:
        flash(f"Line {line_num} reset for re-review.", "success")
        db.session.commit()

    return redirect(url_for(
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
    ))


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/admin-return-to-group")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/admin-return-to-group")
def line_return_to_group(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Admin sends a line back to the reviewer group: reset the AG review and
    clear the admin decision."""
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    if not user_holds_checkout(work_item, user_ctx):
        flash("You must check out this item before making an admin decision.", "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    success, error = return_line_to_reviewer_group(line, user_ctx)

    if not success:
        flash(error, "error")
    else:
        flash(f"Line {line_num} returned to the reviewer group.", "success")
        db.session.commit()

    return redirect(url_for(
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
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

    if not user_holds_checkout(work_item, user_ctx):
        error = "You must check out this item before making an admin decision."
        if is_ajax:
            return jsonify({"success": False, "error": error})
        flash(error, "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

    state = get_line_review_state(line)
    if state.awaiting not in (AWAITING_ADMIN, AWAITING_REVIEWER_GROUP):
        error = "This line is not currently awaiting an admin decision."
        if is_ajax:
            return jsonify({"success": False, "error": error})
        flash(error, "error")
        return redirect(url_for(
            "approvals.line_review",
            event=event,
            dept=dept,
            public_id=public_id,
            line_num=line_num,
            work_type_slug=work_type_slug,
        ))

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
                "approvals.line_review",
                event=event,
                dept=dept,
                public_id=public_id,
                line_num=line_num,
                work_type_slug=work_type_slug,
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
            # Decision rationale is always public (transparency). Non-public notes go
            # through the standalone comment form, which keeps its admin-only option.
            visibility = COMMENT_VISIBILITY_PUBLIC
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
        "approvals.line_review",
        event=event,
        dept=dept,
        public_id=public_id,
        line_num=line_num,
        work_type_slug=work_type_slug,
    ))
