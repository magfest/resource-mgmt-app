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
    BudgetLineDetail,
    ExpenseAccount,
    ROLE_SUPER_ADMIN,
    ROLE_APPROVER,
    REQUEST_KIND_PRIMARY,
)
from app.routes.budget.helpers import compute_line_status_summary
from app.routes import h, get_user_ctx, render_page

home_bp = Blueprint('home', __name__)


@home_bp.get("/")
def index():
    """Home page - shows personalized dashboard based on user role."""
    h.ensure_demo_users()

    user_ctx = get_user_ctx()
    user = user_ctx.user

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

    # Check if super admin
    is_super_admin = ROLE_SUPER_ADMIN in user_ctx.roles
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
    dept_budget_status = {}  # Maps dept_id -> line_summary for primary budget
    seen_dept_ids = set()

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
            accessible_depts.append({
                "department": dm.department,
                "access_source": "direct",
                "access_source_name": None,
                "can_view": dm.can_view,
                "can_edit": dm.can_edit,
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
                    accessible_depts.append({
                        "department": dept,
                        "access_source": "division",
                        "access_source_name": div_m.division.name,
                        "can_view": div_m.can_view,
                        "can_edit": div_m.can_edit,
                        "is_head": div_m.is_division_head,
                    })

        # Sort by division, then department
        accessible_depts.sort(key=lambda x: (
            x["department"].division.sort_order if x["department"].division else 999,
            x["department"].division.name if x["department"].division else "ZZZ",
            x["department"].sort_order,
            x["department"].name,
        ))

        # Get budget status for each accessible department
        budget_work_type = WorkType.query.filter_by(code="BUDGET").first()
        if budget_work_type:
            for dept_info in accessible_depts:
                dept = dept_info["department"]
                # Find the portfolio for this dept/cycle
                portfolio = WorkPortfolio.query.filter_by(
                    work_type_id=budget_work_type.id,
                    event_cycle_id=default_cycle.id,
                    department_id=dept.id,
                    is_archived=False,
                ).first()

                if portfolio:
                    # Find the primary work item
                    primary = WorkItem.query.filter_by(
                        portfolio_id=portfolio.id,
                        request_kind=REQUEST_KIND_PRIMARY,
                        is_archived=False,
                    ).first()

                    if primary:
                        dept_budget_status[dept.id] = compute_line_status_summary(primary)

    context["accessible_depts"] = accessible_depts
    context["dept_budget_status"] = dept_budget_status

    # Get stats for admins
    if is_super_admin:
        # Count submitted work items
        submitted_count = (
            db.session.query(WorkItem)
            .filter(WorkItem.status == "SUBMITTED")
            .count()
        )
        context["submitted_count"] = submitted_count

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
