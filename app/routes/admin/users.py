"""
Admin routes for user management.
"""
from __future__ import annotations

import uuid
from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    User,
    UserRole,
    WorkType,
    ApprovalGroup,
    DepartmentMembership,
    DivisionMembership,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROLE_APPROVER,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes import h, get_user_ctx
from .helpers import (
    require_super_admin,
    require_user_admin,
    render_admin_config_page,
    render_admin_page,
    log_config_change,
    track_changes,
    sort_with_override,
    can_edit_user_identity,
    can_assign_roles,
)

users_bp = Blueprint('users', __name__, url_prefix='/users')


def _get_user_or_404(user_id: str) -> User:
    """Get user by ID or abort with 404."""
    user = db.session.get(User, user_id)
    if not user:
        abort(404, "User not found")
    return user


def _user_to_dict(user: User) -> dict:
    """Convert user to dict for change tracking."""
    return {
        "email": user.email,
        "display_name": user.display_name,
        "is_active": user.is_active,
    }


def _get_role_context():
    """Get work types and approval groups for role assignment."""
    work_types = (
        db.session.query(WorkType)
        .filter(WorkType.is_active == True)
        .order_by(*sort_with_override(WorkType))
        .all()
    )
    approval_groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)
        .order_by(*sort_with_override(ApprovalGroup))
        .all()
    )
    return {
        "work_types": work_types,
        "approval_groups": approval_groups,
        "role_codes": [
            (ROLE_SUPER_ADMIN, "Super Admin", "Full system access"),
            (ROLE_WORKTYPE_ADMIN, "Work Type Admin", "Admin for specific work type (e.g., Budget)"),
            (ROLE_APPROVER, "Approver", "Can review/approve lines in assigned approval groups"),
        ],
    }


@users_bp.get("/")
@require_user_admin
def list_users():
    """List all users."""
    q = (request.args.get("q") or "").strip()
    show_inactive = request.args.get("show_inactive") == "1"
    sort_by = request.args.get("sort_by", "display_name")
    sort_dir = request.args.get("sort_dir", "asc")
    role_filter = request.args.get("role", "")
    linked_filter = request.args.get("linked", "")

    query = db.session.query(User)

    if not show_inactive:
        query = query.filter(User.is_active == True)

    if q:
        like = f"%{q}%"
        query = query.filter(
            (User.email.ilike(like)) |
            (User.display_name.ilike(like))
        )

    # Role filtering
    if role_filter == "super_admin":
        query = query.filter(
            User.roles.any(UserRole.role_code == ROLE_SUPER_ADMIN)
        )
    elif role_filter == "worktype_admin":
        query = query.filter(
            User.roles.any(UserRole.role_code == ROLE_WORKTYPE_ADMIN)
        )
    elif role_filter == "approver":
        query = query.filter(
            User.roles.any(UserRole.role_code == ROLE_APPROVER)
        )
    elif role_filter == "no_roles":
        query = query.filter(~User.roles.any())

    # Google account linked filtering
    if linked_filter == "linked":
        query = query.filter(User.auth_subject.isnot(None))
    elif linked_filter == "not_linked":
        query = query.filter(User.auth_subject.is_(None))

    # Sortable columns whitelist
    sortable = {
        "display_name": User.display_name,
        "email": User.email,
    }

    if sort_by in sortable:
        col = sortable[sort_by]
        order = col.desc() if sort_dir == "desc" else col.asc()
        query = query.order_by(order)
    else:
        query = query.order_by(User.display_name)

    users = query.all()

    # Use render_admin_page (no extra permission check) since the route
    # decorator @require_user_admin already enforces access. The default
    # render_admin_config_page would re-check super_admin and 403 Staff Ops.
    return render_admin_page(
        "admin/users/list.html",
        users=users,
        q=q,
        show_inactive=show_inactive,
        sort_by=sort_by,
        sort_dir=sort_dir,
        role_filter=role_filter,
        linked_filter=linked_filter,
    )


@users_bp.get("/new")
@require_user_admin
def new_user():
    """Show new user form."""
    return render_admin_page(
        "admin/users/form.html",
        user=None,
        **_get_role_context(),
    )


@users_bp.post("/")
@require_user_admin
def create_user():
    """Create a new user."""
    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip()

    if not email or not display_name:
        flash("Email and display name are required", "error")
        return redirect(url_for(".new_user"))

    # Check for duplicate email
    existing = db.session.query(User).filter_by(email=email).first()
    if existing:
        flash(f"A user with email '{email}' already exists", "error")
        return redirect(url_for(".new_user"))

    # Generate a UUID for the user ID
    user_id = str(uuid.uuid4())

    user = User(
        id=user_id,
        email=email,
        display_name=display_name,
        is_active=request.form.get("is_active") == "1",
        auth_subject=None,  # Will be set when user signs in via Google
    )

    db.session.add(user)
    db.session.flush()

    # Handle role assignments — only Super Admin can grant roles
    user_ctx = get_user_ctx()
    if can_assign_roles(user_ctx):
        _update_user_roles(user, request.form)

    log_config_change("user", user.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created user: {user.display_name}", "success")
    return redirect(url_for(".list_users"))


@users_bp.get("/<user_id>")
@require_user_admin
def edit_user(user_id: str):
    """Show edit form for user."""
    user = _get_user_or_404(user_id)

    # Get membership counts
    dept_membership_count = (
        db.session.query(DepartmentMembership)
        .filter(DepartmentMembership.user_id == user_id)
        .count()
    )
    div_membership_count = (
        db.session.query(DivisionMembership)
        .filter(DivisionMembership.user_id == user_id)
        .count()
    )

    return render_admin_page(
        "admin/users/form.html",
        user=user,
        dept_membership_count=dept_membership_count,
        div_membership_count=div_membership_count,
        **_get_role_context(),
    )


@users_bp.post("/<user_id>")
@require_user_admin
def update_user(user_id: str):
    """Update a user."""
    user = _get_user_or_404(user_id)
    user_ctx = get_user_ctx()
    can_change_email = can_edit_user_identity(user_ctx, user.id)

    old_values = _user_to_dict(user)

    email = (request.form.get("email") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip()

    if not email or not display_name:
        flash("Email and display name are required", "error")
        return redirect(url_for(".edit_user", user_id=user_id))

    # If caller can't change email, ignore submitted value — use existing.
    # This applies regardless of what the form actually posted (defense in depth
    # even though the template renders email read-only for non-Super-Admin).
    if not can_change_email:
        email = user.email

    # Check for duplicate email (against the effective email — own email is excluded by id filter)
    existing = db.session.query(User).filter(
        User.email == email,
        User.id != user_id
    ).first()
    if existing:
        flash(f"A user with email '{email}' already exists", "error")
        return redirect(url_for(".edit_user", user_id=user_id))

    user.email = email
    user.display_name = display_name
    user.is_active = request.form.get("is_active") == "1"

    # Handle role assignments — only Super Admin can grant roles
    if can_assign_roles(user_ctx):
        _update_user_roles(user, request.form)

    new_values = _user_to_dict(user)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("user", user.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated user: {user.display_name}", "success")
    return redirect(url_for(".list_users"))


@users_bp.post("/<user_id>/archive")
@require_user_admin
def archive_user(user_id: str):
    """Archive (deactivate) a user."""
    user = _get_user_or_404(user_id)

    if not user.is_active:
        flash("User is already inactive", "warning")
        return redirect(url_for(".list_users"))

    user.is_active = False

    log_config_change("user", user.id, CONFIG_AUDIT_ARCHIVE)

    db.session.commit()
    flash(f"Deactivated user: {user.display_name}", "success")
    return redirect(url_for(".list_users"))


@users_bp.post("/<user_id>/restore")
@require_user_admin
def restore_user(user_id: str):
    """Restore (reactivate) a user."""
    user = _get_user_or_404(user_id)

    if user.is_active:
        flash("User is already active", "warning")
        return redirect(url_for(".list_users"))

    user.is_active = True

    log_config_change("user", user.id, CONFIG_AUDIT_RESTORE)

    db.session.commit()
    flash(f"Reactivated user: {user.display_name}", "success")
    return redirect(url_for(".list_users"))


def _update_user_roles(user: User, form_data) -> None:
    """
    Update user roles based on form data.

    Form fields:
    - role_super_admin: "1" if checked
    - role_worktype_admin_<work_type_id>: "1" if checked
    - role_approver_<approval_group_id>: "1" if checked
    """
    # Clear existing roles
    user.roles.clear()
    db.session.flush()

    # Super Admin
    if form_data.get("role_super_admin") == "1":
        role = UserRole(
            user_id=user.id,
            role_code=ROLE_SUPER_ADMIN,
        )
        db.session.add(role)

    # Work Type Admin roles
    work_types = db.session.query(WorkType).filter(WorkType.is_active == True).all()
    for wt in work_types:
        if form_data.get(f"role_worktype_admin_{wt.id}") == "1":
            role = UserRole(
                user_id=user.id,
                role_code=ROLE_WORKTYPE_ADMIN,
                work_type_id=wt.id,
            )
            db.session.add(role)

    # Approver roles
    approval_groups = db.session.query(ApprovalGroup).filter(ApprovalGroup.is_active == True).all()
    for ag in approval_groups:
        if form_data.get(f"role_approver_{ag.id}") == "1":
            role = UserRole(
                user_id=user.id,
                role_code=ROLE_APPROVER,
                approval_group_id=ag.id,
            )
            db.session.add(role)
