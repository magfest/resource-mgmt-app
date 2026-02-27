"""
Department landing page - shows all work types for a department.
"""
from __future__ import annotations

from flask import abort

from app import db
from app.models import (
    EventCycle,
    Department,
    DepartmentMembership,
    DivisionMembership,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    WorkItem,
    REQUEST_KIND_PRIMARY,
)
from app.routes import get_user_ctx, render_page
from app.routes.work.helpers import (
    get_active_work_types,
    compute_line_status_summary,
    compute_work_item_totals,
    is_budget_admin,
)
from app.routes.admin.helpers import can_manage_department_members, can_edit_department_info
from . import work_bp


@work_bp.get("/<event>/<dept>/")
def department_home(event: str, dept: str):
    """
    Department landing page - shows cards/tabs for each work type.

    URL: /<event>/<dept>/
    """
    user_ctx = get_user_ctx()

    # Look up event cycle
    event_cycle = EventCycle.query.filter_by(code=event.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event}")

    # Look up department
    department = Department.query.filter_by(code=dept.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept}")

    # Get user's memberships
    dept_membership = DepartmentMembership.query.filter_by(
        user_id=user_ctx.user_id,
        department_id=department.id,
        event_cycle_id=event_cycle.id,
    ).first()

    div_membership = None
    if department.division_id:
        div_membership = DivisionMembership.query.filter_by(
            user_id=user_ctx.user_id,
            division_id=department.division_id,
            event_cycle_id=event_cycle.id,
        ).first()

    # Get all active work types
    active_work_types = get_active_work_types()

    # Build work type cards - only include ones user has access to
    work_type_cards = []

    for wt in active_work_types:
        # Check access via department membership
        has_access = False
        can_view = False
        can_edit = False

        # Check if super admin or work type admin
        is_admin = is_budget_admin(user_ctx, wt.id)
        if is_admin:
            has_access = True
            can_view = True
            can_edit = True

        # Check department membership work type access
        if dept_membership and not has_access:
            can_view = dept_membership.can_view_work_type(wt.id)
            can_edit = dept_membership.can_edit_work_type(wt.id)
            has_access = can_view or can_edit

        # Check division membership work type access
        if div_membership and not has_access:
            can_view = div_membership.can_view_work_type(wt.id)
            can_edit = div_membership.can_edit_work_type(wt.id)
            has_access = can_view or can_edit

        if not has_access:
            continue

        # Get portfolio and status for this work type
        portfolio = WorkPortfolio.query.filter_by(
            work_type_id=wt.id,
            event_cycle_id=event_cycle.id,
            department_id=department.id,
            is_archived=False,
        ).first()

        primary_item = None
        status_summary = None
        totals = None

        if portfolio:
            primary_item = WorkItem.query.filter_by(
                portfolio_id=portfolio.id,
                request_kind=REQUEST_KIND_PRIMARY,
                is_archived=False,
            ).first()

            if primary_item:
                status_summary = compute_line_status_summary(primary_item)
                totals = compute_work_item_totals(primary_item)

        # Build the card data
        config = wt.config
        card = {
            "work_type": wt,
            "config": config,
            "url_slug": config.url_slug if config else "budget",
            "name": config.item_plural if config else wt.name,
            "singular_name": config.item_singular if config else "Request",
            "can_view": can_view,
            "can_edit": can_edit,
            "portfolio": portfolio,
            "primary_item": primary_item,
            "status_summary": status_summary,
            "totals": totals,
        }
        work_type_cards.append(card)

    # Check if user has any access
    if not work_type_cards and not user_ctx.is_admin:
        abort(403, "You do not have access to any work types for this department.")

    # Check if user can manage department members (Div Head, DH, or Admin)
    can_manage_members = can_manage_department_members(
        user_ctx, department.id, event_cycle.id
    )

    # Check if user can edit department info (same permission)
    can_edit_info = can_edit_department_info(
        user_ctx, department.id, event_cycle.id
    )

    return render_page(
        "budget/department_home.html",
        event_cycle=event_cycle,
        department=department,
        dept_membership=dept_membership,
        div_membership=div_membership,
        work_type_cards=work_type_cards,
        can_manage_members=can_manage_members,
        can_edit_info=can_edit_info,
    )
