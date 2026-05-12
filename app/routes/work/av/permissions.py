"""AV-specific permission helpers.

Layered on top of existing dept/div memberships and the
SpaceDepartmentAssignment table. See spec §6 for design.

All functions accept a UserContext (app.routes.UserContext) plus any
model objects needed for context.  They never abort — callers use
require_* gates (Task 16) for HTTP enforcement.
"""
from __future__ import annotations

from flask import abort

from app import db
from app.models import (
    Department,
    DepartmentMembership,
    DivisionMembership,
    WorkType,
    UserRole,
    ApprovalGroup,
    ROLE_WORKTYPE_ADMIN,
)
from app.models.space import Space, SpaceDepartmentAssignment
from app.models.av import AVScope


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_av_work_type() -> WorkType | None:
    """Return the AV WorkType row, or None if not yet seeded."""
    return db.session.query(WorkType).filter_by(code="AV").first()


def _is_dept_assigned(department: Department, space: Space) -> bool:
    """True if dept has an active (unassigned_at IS NULL) assignment to space."""
    if department is None or space is None:
        return False
    return db.session.query(SpaceDepartmentAssignment).filter(
        SpaceDepartmentAssignment.space_id == space.id,
        SpaceDepartmentAssignment.department_id == department.id,
        SpaceDepartmentAssignment.unassigned_at.is_(None),
    ).first() is not None


def _user_dept_ids_with_av_access(user_ctx, *, edit: bool = False) -> set[int]:
    """Return the set of department IDs the user has AV view (or edit) access to.

    Considers both direct DepartmentMembership rows and DivisionMembership
    rows (which cascade to every department in that division).  Both paths
    check per-work-type access via the WorkTypeAccess join tables.
    """
    av = _get_av_work_type()
    if av is None:
        return set()

    dept_ids: set[int] = set()

    # ---- Direct department memberships ----
    dept_memberships = db.session.query(DepartmentMembership).filter_by(
        user_id=user_ctx.user_id,
    ).all()
    for m in dept_memberships:
        if edit:
            if m.can_edit_work_type(av.id):
                dept_ids.add(m.department_id)
        else:
            if m.can_view_work_type(av.id) or m.can_edit_work_type(av.id):
                dept_ids.add(m.department_id)

    # ---- Division memberships (cascade to all depts in that division) ----
    div_memberships = db.session.query(DivisionMembership).filter_by(
        user_id=user_ctx.user_id,
    ).all()
    for div_m in div_memberships:
        if edit:
            if not div_m.can_edit_work_type(av.id):
                continue
        else:
            if not (div_m.can_view_work_type(av.id) or div_m.can_edit_work_type(av.id)):
                continue
        # Cascade: add all departments belonging to the division
        for dept in div_m.division.departments:
            dept_ids.add(dept.id)

    return dept_ids


def _is_av_team_member(user_ctx) -> bool:
    """True if user belongs to the AV_TEAM ApprovalGroup (via approval_group_ids)."""
    av = _get_av_work_type()
    if av is None:
        return False
    av_team = db.session.query(ApprovalGroup).filter_by(
        work_type_id=av.id, code="AV_TEAM",
    ).first()
    if av_team is None:
        return False
    return av_team.id in user_ctx.approval_group_ids


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def is_av_team_member(user_ctx) -> bool:
    """True if user is a member of the AV_TEAM ApprovalGroup (plain group membership check).

    Does NOT include AV admins or super-admins — use is_av_admin() for elevated
    roles, or require_av_team_member() for the combined permission gate.
    """
    return _is_av_team_member(user_ctx)


def is_av_admin(user_ctx) -> bool:
    """True if user holds WORKTYPE_ADMIN role scoped to the AV work type."""
    if user_ctx.is_super_admin:
        return True
    av = _get_av_work_type()
    if av is None:
        return False
    return db.session.query(UserRole).filter_by(
        user_id=user_ctx.user_id,
        role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=av.id,
    ).first() is not None


def can_view_av_space(user_ctx, space: Space) -> bool:
    """View access on a Space (and consequently all requests within it).

    Grants:
    - SUPER_ADMIN or AV admin — see everything.
    - AV_TEAM ApprovalGroup member — see everything (reviewers need full visibility).
    - Dept/div member with AV view access — only if their dept is assigned to this space.
    """
    if user_ctx.is_super_admin:
        return True
    if is_av_admin(user_ctx):
        return True
    if _is_av_team_member(user_ctx):
        return True

    user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=False)
    if not user_dept_ids:
        return False

    assigned = db.session.query(SpaceDepartmentAssignment).filter(
        SpaceDepartmentAssignment.space_id == space.id,
        SpaceDepartmentAssignment.unassigned_at.is_(None),
        SpaceDepartmentAssignment.department_id.in_(user_dept_ids),
    ).first()
    return assigned is not None


def can_create_av_request_for(user_ctx, space: Space, department: Department) -> bool:
    """Can the user file an AV request for (space, department)?

    Requires:
    - dept is currently assigned to the space (active assignment).
    - user has AV edit access for that dept (via dept or div membership).

    SUPER_ADMIN bypasses membership checks entirely (they can act on any dept).
    Note: WORKTYPE_ADMIN(AV) does NOT bypass — they manage spaces/assignments,
    not file requests on behalf of departments (spec §6).
    """
    if user_ctx.is_super_admin:
        return True
    if not _is_dept_assigned(department, space):
        return False
    user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=True)
    return department.id in user_dept_ids


def can_edit_av_request(user_ctx, request) -> bool:
    """Edit access on a specific AV request.

    `request` must expose `request.portfolio.department` and
    `request.av_request_detail.space`.

    SUPER_ADMIN bypasses membership checks entirely.
    Note: WORKTYPE_ADMIN(AV) does NOT bypass — they manage spaces/assignments,
    not edit requests on behalf of departments (spec §6).
    """
    if user_ctx.is_super_admin:
        return True
    dept = request.portfolio.department
    space = request.av_request_detail.space
    user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=True)
    if dept.id not in user_dept_ids:
        return False
    return _is_dept_assigned(dept, space)


def can_view_av_request(user_ctx, request) -> bool:
    """View access on a specific AV request — delegates to space visibility.

    `request` must expose `request.av_request_detail.space`.
    """
    return can_view_av_space(user_ctx, request.av_request_detail.space)


def can_ack_av_scope(user_ctx, scope: AVScope, department: Department) -> bool:
    """Can user submit an acknowledgment on scope on behalf of department?

    Requires:
    - scope.state == "OPEN_FOR_INPUT"
    - user has AV edit access for department.
    - department is currently assigned to the scope's space.
    """
    if scope.state != "OPEN_FOR_INPUT":
        return False
    user_dept_ids = _user_dept_ids_with_av_access(user_ctx, edit=True)
    if department.id not in user_dept_ids:
        return False
    # scope.space is a relationship on AVScope → Space
    return _is_dept_assigned(department, scope.space)


# ---------------------------------------------------------------------------
# Require gates (abort 403 on failure)
# ---------------------------------------------------------------------------

def require_av_admin(user_ctx):
    if not is_av_admin(user_ctx) and not user_ctx.is_super_admin:
        abort(403)


def require_view_av_space(user_ctx, space):
    if not can_view_av_space(user_ctx, space):
        abort(403)


def require_view_av_request(user_ctx, request):
    if not can_view_av_request(user_ctx, request):
        abort(403)


def require_edit_av_request(user_ctx, request):
    if not can_edit_av_request(user_ctx, request):
        abort(403)


def require_av_team_member(user_ctx):
    if not _is_av_team_member(user_ctx) and not is_av_admin(user_ctx) and not user_ctx.is_super_admin:
        abort(403)
