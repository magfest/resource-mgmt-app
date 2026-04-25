"""
Context and permission building for work routes.

Contains dataclasses for context/permissions and functions to build them.
"""
from __future__ import annotations

from dataclasses import dataclass

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
    UserRole,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_FINALIZED,
    ROLE_WORKTYPE_ADMIN,
)
from app.routes import get_user_ctx, UserContext
from .event_enablement import is_department_enabled_for_event


# ============================================================
# Context and Permission Dataclasses
# ============================================================

@dataclass(frozen=True)
class PortfolioContext:
    """Context for a portfolio view/action."""
    event_cycle: EventCycle
    department: Department
    portfolio: WorkPortfolio
    work_type: WorkType
    user_ctx: UserContext
    membership: DepartmentMembership | None
    division_membership: DivisionMembership | None  # Division-level access

    @property
    def work_type_slug(self) -> str:
        return self.work_type.config.url_slug


@dataclass(frozen=True)
class PortfolioPerms:
    """Permission flags for portfolio-level actions.

    Attributes:
        is_worktype_admin: True if user is admin for this work type
            (either SUPER_ADMIN or WORKTYPE_ADMIN for this work type)
    """
    can_view: bool
    can_edit: bool
    can_create_primary: bool
    can_create_supplementary: bool
    is_worktype_admin: bool


@dataclass(frozen=True)
class WorkItemPerms:
    """Permission flags for work item actions.

    Attributes:
        is_worktype_admin: True if user is admin for this work type
            (either SUPER_ADMIN or WORKTYPE_ADMIN for this work type)
    """
    can_view: bool
    can_edit: bool
    can_submit: bool
    can_add_lines: bool
    can_delete: bool
    can_checkout: bool
    can_checkin: bool
    can_request_info: bool
    can_respond_to_info: bool
    is_worktype_admin: bool
    is_draft: bool
    is_checked_out: bool
    is_checked_out_by_current_user: bool


# ============================================================
# Context Building Functions
# ============================================================

def get_budget_work_type() -> WorkType:
    """Get or create the BUDGET work type."""
    work_type = WorkType.query.filter_by(code="BUDGET").first()
    if not work_type:
        work_type = WorkType(
            code="BUDGET",
            name="Budget",
            is_active=True,
            sort_order=0,
        )
        db.session.add(work_type)
        db.session.flush()
    return work_type


def get_work_type_by_slug(url_slug: str) -> WorkType:
    """
    Get a work type by its URL slug.

    Args:
        url_slug: The URL slug (e.g., "budget", "contracts", "supply")

    Returns:
        The WorkType with matching config url_slug

    Raises:
        404 if not found
    """
    config = WorkTypeConfig.query.filter_by(url_slug=url_slug.lower()).first()
    if not config:
        abort(404, f"Work type not found: {url_slug}")
    return config.work_type


def get_work_type_by_code(code: str) -> WorkType:
    """
    Get a work type by its code.

    Args:
        code: The work type code (e.g., "BUDGET", "CONTRACT", "SUPPLY")

    Returns:
        The WorkType with matching code

    Raises:
        404 if not found
    """
    work_type = WorkType.query.filter_by(code=code.upper()).first()
    if not work_type:
        abort(404, f"Work type not found: {code}")
    return work_type


def get_active_work_types() -> list[WorkType]:
    """Get all active work types with configs."""
    from app.routes.admin.helpers import sort_with_override

    return WorkType.query.join(WorkTypeConfig).filter(
        WorkType.is_active == True
    ).order_by(*sort_with_override(WorkType)).all()


def get_portfolio_context(
    event_code: str,
    dept_code: str,
    work_type_slug: str = "budget",
) -> PortfolioContext:
    """
    Build context for a portfolio view.

    Looks up event cycle and department by their codes.
    Creates the portfolio if it doesn't exist.
    Returns PortfolioContext with all needed objects.

    Args:
        event_code: Event cycle code (e.g., "SMF2027")
        dept_code: Department code (e.g., "TECHOPS")
        work_type_slug: Work type URL slug (e.g., "budget", "contracts", "supply")
    """
    user_ctx = get_user_ctx()

    # Look up event cycle
    event_cycle = EventCycle.query.filter_by(code=event_code.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event_code}")

    # Look up department
    department = Department.query.filter_by(code=dept_code.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept_code}")

    # Check if department is enabled for this event (super admins bypass this)
    if not user_ctx.is_super_admin:
        if not is_department_enabled_for_event(department.id, event_cycle.id):
            abort(403, f"Department '{department.name}' is not enabled for this event.")

    # Get work type by slug (falls back to BUDGET for backward compatibility)
    if work_type_slug == "budget":
        work_type = get_budget_work_type()
    else:
        work_type = get_work_type_by_slug(work_type_slug)

    # Get or create portfolio
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=work_type.id,
        event_cycle_id=event_cycle.id,
        department_id=department.id,
        is_archived=False,
    ).first()

    if not portfolio:
        portfolio = WorkPortfolio(
            work_type_id=work_type.id,
            event_cycle_id=event_cycle.id,
            department_id=department.id,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(portfolio)
        db.session.flush()

    # Get user's membership for this department/cycle
    membership = DepartmentMembership.query.filter_by(
        user_id=user_ctx.user_id,
        department_id=department.id,
        event_cycle_id=event_cycle.id,
    ).first()

    # Get user's division membership (if department has a division)
    division_membership = None
    if department.division_id:
        division_membership = DivisionMembership.query.filter_by(
            user_id=user_ctx.user_id,
            division_id=department.division_id,
            event_cycle_id=event_cycle.id,
        ).first()

    return PortfolioContext(
        event_cycle=event_cycle,
        department=department,
        portfolio=portfolio,
        work_type=work_type,
        user_ctx=user_ctx,
        membership=membership,
        division_membership=division_membership,
    )


# ============================================================
# Permission Building Functions
# ============================================================

def is_worktype_admin(user_ctx: UserContext, work_type_id: int) -> bool:
    """Check if user is an admin for a specific work type (SUPER_ADMIN or WORKTYPE_ADMIN)."""
    if user_ctx.is_super_admin:
        return True

    admin_role = UserRole.query.filter_by(
        user_id=user_ctx.user_id,
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=work_type_id,
    ).first()

    return admin_role is not None


def is_budget_admin(user_ctx: UserContext, work_type_id: int | None = None) -> bool:
    """Check if user is a budget admin. Convenience wrapper for is_worktype_admin()."""
    if work_type_id is None:
        work_type = get_budget_work_type()
        work_type_id = work_type.id
    return is_worktype_admin(user_ctx, work_type_id)


def build_portfolio_perms(ctx: PortfolioContext) -> PortfolioPerms:
    """Build permission flags for a portfolio."""
    is_wt_admin = is_budget_admin(ctx.user_ctx, ctx.work_type.id)
    work_type_id = ctx.work_type.id

    # Department membership permissions - now scoped by work type
    m_can_view = False
    m_can_edit = False
    if ctx.membership:
        # Check work type-specific access
        m_can_view = ctx.membership.can_view_work_type(work_type_id)
        m_can_edit = ctx.membership.can_edit_work_type(work_type_id)

    # Division membership permissions - also scoped by work type
    dm = ctx.division_membership
    dm_can_view = False
    dm_can_edit = False
    if dm:
        dm_can_view = dm.can_view_work_type(work_type_id)
        dm_can_edit = dm.can_edit_work_type(work_type_id)

    # View: worktype admin OR department membership OR division membership (with work type access)
    can_view = is_wt_admin or m_can_view or dm_can_view

    # Edit: worktype admin OR department membership OR division membership
    can_edit = is_wt_admin or m_can_edit or dm_can_edit

    # Check for existing PRIMARY
    existing_primary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    # Can create PRIMARY: can_edit AND no existing PRIMARY
    can_create_primary = can_edit and (existing_primary is None)

    # Can create SUPPLEMENTARY: can_edit AND PRIMARY is FINALIZED
    can_create_supplementary = can_edit and (
        existing_primary is not None and
        existing_primary.status == WORK_ITEM_STATUS_FINALIZED
    )

    return PortfolioPerms(
        can_view=can_view,
        can_edit=can_edit,
        can_create_primary=can_create_primary,
        can_create_supplementary=can_create_supplementary,
        is_worktype_admin=is_wt_admin,
    )


# ============================================================
# Permission Enforcement Functions
# ============================================================

def require_budget_work_type(ctx: PortfolioContext) -> None:
    """Abort 404 if the portfolio's work type is not BUDGET.

    Used by handlers that still contain budget-specific logic (line CRUD,
    edit, submit, dispatch, review). Until those handlers are generalized
    per work type, they must reject non-budget portfolios cleanly so that
    URLs like /<event>/<dept>/techops/item/... return 404 rather than
    crash on BudgetLineDetail queries.
    """
    if ctx.work_type.code != "BUDGET":
        abort(404)


def require_portfolio_view(ctx: PortfolioContext) -> PortfolioPerms:
    """Abort 403 if user cannot view the portfolio."""
    perms = build_portfolio_perms(ctx)
    if not perms.can_view:
        abort(403, "You do not have permission to view this portfolio.")
    return perms


def require_portfolio_edit(ctx: PortfolioContext) -> PortfolioPerms:
    """Abort 403 if user cannot edit the portfolio."""
    perms = build_portfolio_perms(ctx)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this portfolio.")
    return perms


def require_work_item_view(item: WorkItem, ctx: PortfolioContext) -> "WorkItemPerms":
    """Abort 403 if user cannot view the work item."""
    from .checkout import build_work_item_perms
    perms = build_work_item_perms(item, ctx)
    if not perms.can_view:
        abort(403, "You do not have permission to view this work item.")
    return perms


def require_work_item_edit(item: WorkItem, ctx: PortfolioContext) -> "WorkItemPerms":
    """Abort 403 if user cannot edit the work item."""
    from .checkout import build_work_item_perms
    perms = build_work_item_perms(item, ctx)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this work item.")
    return perms
