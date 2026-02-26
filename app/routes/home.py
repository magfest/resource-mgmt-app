"""
Home page route - adapts based on user role.
"""
from __future__ import annotations

from datetime import date
from flask import Blueprint, redirect, url_for

from app import db
from app.models import (
    User,
    Department,
    Division,
    DepartmentMembership,
    DivisionMembership,
    EventCycle,
    ApprovalGroup,
    UserRole,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    BudgetLineDetail,
    ExpenseAccount,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
)
from app.routes.work.helpers import compute_portfolio_status_summary, get_active_work_types
from app.routes import h, get_user_ctx, render_page

home_bp = Blueprint('home', __name__)


@home_bp.get("/health")
def health_check():
    """Health check endpoint for AWS AppRunner / load balancers.

    Returns 200 OK if the app is running and can connect to the database.
    """
    from flask import jsonify

    try:
        # Verify database connectivity
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@home_bp.get("/")
def index():
    """Home page - shows personalized dashboard based on user role."""
    from flask import current_app

    # Check if user is authenticated
    user_ctx = get_user_ctx()
    if user_ctx.user_id is None:
        # Not logged in - redirect to login page
        return redirect(url_for('auth.login_page'))

    # Ensure demo users exist (only in dev mode)
    if current_app.config.get("DEV_LOGIN_ENABLED"):
        h.ensure_demo_users()

    user = user_ctx.user
    if not user:
        # User ID in session but user doesn't exist - clear session and redirect
        from flask import session
        session.pop('active_user_id', None)
        return redirect(url_for('auth.login_page'))

    # Get the default event cycle
    default_cycle = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_default == True)
        .first()
    )

    if not default_cycle:
        default_cycle = (
            db.session.query(EventCycle)
            .filter(EventCycle.is_active == True)
            .order_by(EventCycle.sort_order)
            .first()
        )

    # Build context based on user's access
    context = {
        "user": user,
        "default_cycle": default_cycle,
    }

    # Check if super admin (respects role override for testing)
    is_super_admin = user_ctx.is_admin
    context["is_super_admin"] = is_super_admin

    # Get approval groups user can review
    approval_groups = []
    if user_ctx.approval_group_ids:
        approval_groups = (
            db.session.query(ApprovalGroup)
            .filter(ApprovalGroup.id.in_(user_ctx.approval_group_ids))
            .filter(ApprovalGroup.is_active == True)
            .order_by(ApprovalGroup.sort_order)
            .all()
        )
    context["approval_groups"] = approval_groups

    # Get divisions user has access to (via division membership)
    div_memberships = []
    if default_cycle:
        div_memberships = (
            db.session.query(DivisionMembership)
            .join(Division)
            .filter(DivisionMembership.user_id == user_ctx.user_id)
            .filter(DivisionMembership.event_cycle_id == default_cycle.id)
            .filter(Division.is_active == True)
            .order_by(Division.sort_order, Division.name)
            .all()
        )
    context["div_memberships"] = div_memberships

    # Build a unified list of accessible departments
    # Combines direct department memberships + departments from division access
    accessible_depts = []  # List of dicts with dept info and access details
    dept_work_type_status = {}  # Maps (dept_id, work_type_id) -> line_summary
    dept_work_type_access = {}  # Maps (dept_id, work_type_id) -> {"can_view": bool, "can_edit": bool}
    seen_dept_ids = set()

    # Get all active work types for multi-type dashboard
    active_work_types = get_active_work_types()
    context["active_work_types"] = active_work_types

    if default_cycle:
        # First, get direct department memberships
        dept_memberships = (
            db.session.query(DepartmentMembership)
            .join(Department)
            .filter(DepartmentMembership.user_id == user_ctx.user_id)
            .filter(DepartmentMembership.event_cycle_id == default_cycle.id)
            .filter(Department.is_active == True)
            .all()
        )

        for dm in dept_memberships:
            seen_dept_ids.add(dm.department_id)
            # Check which work types this membership has access to
            has_any_wt_access = False
            for wt in active_work_types:
                can_view = dm.can_view_work_type(wt.id)
                can_edit = dm.can_edit_work_type(wt.id)
                if can_view or can_edit:
                    has_any_wt_access = True
                    dept_work_type_access[(dm.department_id, wt.id)] = {
                        "can_view": can_view,
                        "can_edit": can_edit,
                    }

            # Only add dept if user has access to at least one work type (or is admin)
            if has_any_wt_access or is_super_admin:
                accessible_depts.append({
                    "department": dm.department,
                    "membership": dm,
                    "access_source": "direct",
                    "access_source_name": None,
                    "is_head": dm.is_department_head,
                })

        # Then, add departments from division memberships (if not already added)
        for div_m in div_memberships:
            division_depts = (
                db.session.query(Department)
                .filter(Department.division_id == div_m.division_id)
                .filter(Department.is_active == True)
                .all()
            )

            for dept in division_depts:
                if dept.id not in seen_dept_ids:
                    seen_dept_ids.add(dept.id)
                    # Check which work types this division membership has access to
                    has_any_wt_access = False
                    for wt in active_work_types:
                        can_view = div_m.can_view_work_type(wt.id)
                        can_edit = div_m.can_edit_work_type(wt.id)
                        if can_view or can_edit:
                            has_any_wt_access = True
                            dept_work_type_access[(dept.id, wt.id)] = {
                                "can_view": can_view,
                                "can_edit": can_edit,
                            }

                    if has_any_wt_access or is_super_admin:
                        accessible_depts.append({
                            "department": dept,
                            "membership": None,
                            "access_source": "division",
                            "access_source_name": div_m.division.name,
                            "is_head": div_m.is_division_head,
                        })

        # Sort by division, then department
        accessible_depts.sort(key=lambda x: (
            x["department"].division.sort_order if x["department"].division else 999,
            x["department"].division.name if x["department"].division else "ZZZ",
            x["department"].sort_order,
            x["department"].name,
        ))

        # Get status for each work type for each accessible department (only if user has access)
        for work_type in active_work_types:
            for dept_info in accessible_depts:
                dept = dept_info["department"]

                # Check if user has access to this work type for this department
                access = dept_work_type_access.get((dept.id, work_type.id))
                if not access and not is_super_admin:
                    continue  # No access to this work type

                # Find the portfolio for this dept/cycle/work_type
                portfolio = WorkPortfolio.query.filter_by(
                    work_type_id=work_type.id,
                    event_cycle_id=default_cycle.id,
                    department_id=dept.id,
                    is_archived=False,
                ).first()

                if portfolio:
                    # Compute portfolio-level status (includes supplementary items)
                    portfolio_status = compute_portfolio_status_summary(portfolio)
                    if portfolio_status:
                        dept_work_type_status[(dept.id, work_type.id)] = portfolio_status

    context["accessible_depts"] = accessible_depts
    context["dept_work_type_status"] = dept_work_type_status
    context["dept_work_type_access"] = dept_work_type_access
    # Backward compatibility alias
    context["dept_budget_status"] = {
        dept_id: status
        for (dept_id, wt_id), status in dept_work_type_status.items()
        if any(wt.code == "BUDGET" and wt.id == wt_id for wt in active_work_types)
    }

    # Get stats for admins
    if is_super_admin:
        # Count submitted work items
        submitted_count = (
            db.session.query(WorkItem)
            .filter(WorkItem.status == "SUBMITTED")
            .count()
        )
        context["submitted_count"] = submitted_count

        # Count items awaiting dispatch
        dispatch_queue_count = (
            db.session.query(WorkItem)
            .filter(WorkItem.status == WORK_ITEM_STATUS_AWAITING_DISPATCH)
            .filter(WorkItem.is_archived == False)
            .count()
        )
        context["dispatch_queue_count"] = dispatch_queue_count

        # Count pending work lines
        pending_lines = (
            db.session.query(WorkLine)
            .filter(WorkLine.status == "PENDING")
            .count()
        )
        context["pending_lines"] = pending_lines

    # Get stats for approvers
    if approval_groups:
        # Count lines pending review in user's approval groups
        pending_for_approver = (
            db.session.query(WorkLine)
            .join(BudgetLineDetail, BudgetLineDetail.work_line_id == WorkLine.id)
            .join(ExpenseAccount, ExpenseAccount.id == BudgetLineDetail.expense_account_id)
            .filter(ExpenseAccount.approval_group_id.in_(user_ctx.approval_group_ids))
            .filter(WorkLine.status == "PENDING")
            .count()
        )
        context["pending_for_approver"] = pending_for_approver

    # Build milestones list from event cycle dates
    milestones = []
    today = date.today()

    if default_cycle:
        milestone_defs = [
            ("submission_deadline", "Submission Deadline", "Budget submissions due"),
            ("approval_target_date", "Reviews Started", "Target for completing reviews"),
            ("finalization_date", "Budget Finalization", "Budgets locked"),
            ("event_start_date", "Event Starts", default_cycle.name),
        ]

        for field, label, description in milestone_defs:
            milestone_date = getattr(default_cycle, field)
            if milestone_date:
                days_until = (milestone_date - today).days
                milestones.append({
                    "label": label,
                    "description": description,
                    "date": milestone_date,
                    "days_until": days_until,
                    "is_past": days_until < 0,
                    "is_soon": 0 <= days_until <= 14,
                    "is_today": days_until == 0,
                })

    context["milestones"] = milestones

    # Determine if user has any access
    has_any_access = (
        is_super_admin or
        bool(approval_groups) or
        bool(accessible_depts)
    )
    context["has_any_access"] = has_any_access

    return render_page("home.html", **context)
