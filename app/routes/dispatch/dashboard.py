"""
Dispatch queue routes for budget requests.
"""
from datetime import datetime

from flask import render_template, redirect, url_for, request, abort, flash
from sqlalchemy.orm import selectinload, joinedload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkItemAuditEvent,
    BudgetLineDetail,
    ApprovalGroup,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    AUDIT_EVENT_DISPATCH,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency, friendly_status
from app.routes.admin_final.helpers import require_budget_admin
from . import dispatch_bp
from .helpers import (
    get_dispatch_queue,
    get_active_approval_groups,
    get_active_event_cycles,
    get_active_departments,
)


def require_dispatch_admin():
    """
    Require Budget worktype admin OR super admin for dispatch operations.
    Returns user_ctx for convenience.
    """
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
    return user_ctx


# ============================================================
# Dashboard Route
# ============================================================

@dispatch_bp.get("/")
def dashboard():
    """
    Dispatch queue dashboard - list all items awaiting dispatch.
    """
    require_dispatch_admin()

    # Get filter params
    selected_event = request.args.get("event", "")
    selected_dept = request.args.get("dept", "")

    # Resolve filter IDs
    event_cycle_id = None
    department_id = None

    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    if selected_event:
        for ec in event_cycles:
            if ec.code == selected_event:
                event_cycle_id = ec.id
                break

    if selected_dept:
        for d in departments:
            if d.code == selected_dept:
                department_id = d.id
                break

    # Get queue items
    queue_items = get_dispatch_queue(
        event_cycle_id=event_cycle_id,
        department_id=department_id,
    )

    return render_template(
        "dispatch/dashboard.html",
        queue_items=queue_items,
        event_cycles=event_cycles,
        departments=departments,
        selected_event=selected_event,
        selected_dept=selected_dept,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


# ============================================================
# Dispatch Item Routes
# ============================================================

@dispatch_bp.get("/item/<int:work_item_id>")
def dispatch_item(work_item_id: int):
    """
    Show dispatch form for a single work item.
    """
    require_dispatch_admin()

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines)
            .joinedload(WorkLine.budget_detail)
            .joinedload(BudgetLineDetail.expense_account),
        joinedload(WorkItem.portfolio),
    ).get_or_404(work_item_id)

    if work_item.status != WORK_ITEM_STATUS_AWAITING_DISPATCH:
        flash("This budget request is not pending review.", "error")
        return redirect(url_for("dispatch.dashboard"))

    portfolio = work_item.portfolio
    approval_groups = get_active_approval_groups()

    # Build line data with suggested approval groups
    lines_data = []
    for line in work_item.lines:
        detail = line.budget_detail
        if detail:
            expense_account = detail.expense_account
            suggested_group_id = expense_account.approval_group_id if expense_account else None
            assigned_group_id = detail.routed_approval_group_id

            line_total = detail.unit_price_cents * int(detail.quantity)

            lines_data.append({
                "line": line,
                "detail": detail,
                "expense_account": expense_account,
                "suggested_group_id": suggested_group_id,
                "assigned_group_id": assigned_group_id,
                "line_total": line_total,
            })

    # Count assigned
    assigned_count = sum(1 for ld in lines_data if ld["assigned_group_id"])
    all_assigned = assigned_count == len(lines_data)

    return render_template(
        "dispatch/dispatch_item.html",
        work_item=work_item,
        portfolio=portfolio,
        lines_data=lines_data,
        approval_groups=approval_groups,
        assigned_count=assigned_count,
        all_assigned=all_assigned,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


@dispatch_bp.post("/item/<int:work_item_id>/assign")
def assign_approval_groups(work_item_id: int):
    """
    Save approval group assignments for all lines (without dispatching).
    """
    user_ctx = require_dispatch_admin()

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).get_or_404(work_item_id)

    if work_item.status != WORK_ITEM_STATUS_AWAITING_DISPATCH:
        flash("This budget request is not pending review.", "error")
        return redirect(url_for("dispatch.dashboard"))

    # Process form data
    for line in work_item.lines:
        field_name = f"approval_group_{line.id}"
        group_id_str = request.form.get(field_name, "")

        if group_id_str:
            try:
                group_id = int(group_id_str)
                # Validate group exists
                group = ApprovalGroup.query.get(group_id)
                if group and line.budget_detail:
                    line.budget_detail.routed_approval_group_id = group_id
            except ValueError:
                pass

    db.session.commit()
    flash("Approval group assignments saved.", "success")

    return redirect(url_for("dispatch.dispatch_item", work_item_id=work_item_id))


@dispatch_bp.post("/item/<int:work_item_id>/dispatch")
def dispatch_to_queue(work_item_id: int):
    """
    Dispatch work item to approval queue.

    Validates all lines have approval groups assigned,
    creates WorkLineReview records, and updates status.
    """
    user_ctx = require_dispatch_admin()

    work_item = WorkItem.query.options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).get_or_404(work_item_id)

    if work_item.status != WORK_ITEM_STATUS_AWAITING_DISPATCH:
        flash("This budget request is not pending review.", "error")
        return redirect(url_for("dispatch.dashboard"))

    # First save any pending assignments from the form
    for line in work_item.lines:
        field_name = f"approval_group_{line.id}"
        group_id_str = request.form.get(field_name, "")

        if group_id_str:
            try:
                group_id = int(group_id_str)
                group = ApprovalGroup.query.get(group_id)
                if group and line.budget_detail:
                    line.budget_detail.routed_approval_group_id = group_id
            except ValueError:
                pass

    db.session.flush()

    # Validate all lines have approval group assigned
    unassigned_lines = []
    for line in work_item.lines:
        if not line.budget_detail or not line.budget_detail.routed_approval_group_id:
            unassigned_lines.append(line.line_number)

    if unassigned_lines:
        flash(
            f"Cannot dispatch: lines {', '.join(map(str, unassigned_lines))} have no approval group assigned.",
            "error"
        )
        return redirect(url_for("dispatch.dispatch_item", work_item_id=work_item_id))

    # Create WorkLineReview records for each line
    for line in work_item.lines:
        detail = line.budget_detail

        # Set initial review stage
        line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP

        # Create WorkLineReview record for APPROVAL_GROUP stage
        review = WorkLineReview(
            work_line_id=line.id,
            stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=detail.routed_approval_group_id,
            status=REVIEW_STATUS_PENDING,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(review)

    # Update work item status
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.dispatched_at = datetime.utcnow()
    work_item.dispatched_by_user_id = user_ctx.user_id

    # Build approval groups summary for audit
    approval_groups_summary = {}
    for line in work_item.lines:
        if line.budget_detail and line.budget_detail.routed_approval_group_id:
            group_id = line.budget_detail.routed_approval_group_id
            if group_id not in approval_groups_summary:
                group = ApprovalGroup.query.get(group_id)
                approval_groups_summary[group_id] = {
                    "name": group.name if group else f"Group {group_id}",
                    "line_count": 0,
                }
            approval_groups_summary[group_id]["line_count"] += 1

    # Create audit event for dispatch
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_DISPATCH,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "line_count": len(work_item.lines),
            "approval_groups": list(approval_groups_summary.values()),
        },
    )
    db.session.add(audit_event)

    db.session.commit()

    # Collect unique approval group IDs for notification
    approval_group_ids = set()
    for line in work_item.lines:
        if line.budget_detail and line.budget_detail.routed_approval_group_id:
            approval_group_ids.add(line.budget_detail.routed_approval_group_id)

    # Send notification to approval group members
    from app.services.notifications import notify_budget_dispatched
    notify_budget_dispatched(work_item, list(approval_group_ids))
    db.session.commit()  # Commit notification log

    # Build redirect URL to work item detail
    portfolio = work_item.portfolio
    flash("Budget request sent to reviewer groups.", "success")

    return redirect(url_for(
        "work.work_item_detail",
        event=portfolio.event_cycle.code,
        dept=portfolio.department.code,
        public_id=work_item.public_id,
    ))
