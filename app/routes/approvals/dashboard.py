"""
Approval dashboard routes - review queues and filtering.
"""
from flask import render_template, redirect, url_for, request, abort

from app.models import ApprovalGroup, EventCycle, Department
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency, friendly_status
from . import approvals_bp
from .helpers import (
    get_reviewable_groups,
    build_approval_queues,
    get_active_event_cycles,
    get_active_departments,
)


@approvals_bp.get("/approvals/")
def dashboard_home():
    """
    Dashboard home - redirect to first reviewable group or show selector.
    """
    user_ctx = get_user_ctx()

    # Get groups user can review
    groups = get_reviewable_groups(user_ctx)

    if not groups:
        abort(403, "You do not have permission to access the approvals dashboard.")

    # Redirect to first group
    return redirect(url_for("approvals.dashboard_group", group_code=groups[0].code))


@approvals_bp.get("/approvals/<group_code>")
def dashboard_group(group_code: str):
    """
    Group-specific dashboard with queues.
    """
    user_ctx = get_user_ctx()

    # Validate group access
    groups = get_reviewable_groups(user_ctx)
    if not groups:
        abort(403, "You do not have permission to access the approvals dashboard.")

    # Find requested group by code (case-insensitive)
    group = None
    for g in groups:
        if g.code.upper() == group_code.upper():
            group = g
            break

    if not group:
        abort(404, f"Approval group not found: {group_code}")

    # Get filter values from query params
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()

    # Resolve filters to IDs
    event_cycle_id = None
    department_id = None

    if event_code:
        event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()
        if event_cycle:
            event_cycle_id = event_cycle.id

    if dept_code:
        department = Department.query.filter_by(code=dept_code.upper()).first()
        if department:
            department_id = department.id

    # Build queues
    queues = build_approval_queues(
        group_id=group.id,
        event_cycle_id=event_cycle_id,
        department_id=department_id,
    )

    # Get filter options
    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    return render_template(
        "approvals/dashboard.html",
        user_ctx=user_ctx,
        groups=groups,
        current_group=group,
        queues=queues,
        event_cycles=event_cycles,
        departments=departments,
        selected_event=event_code,
        selected_dept=dept_code,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )
