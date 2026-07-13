"""
Shared helpers for admin configuration routes.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import wraps
from typing import Any

from flask import abort, render_template, flash, request

# Maximum length for entity codes (departments, divisions, approval groups, etc.)
CODE_MAX_LENGTH = 16
MAX_FREEFORM_TEXT_LENGTH = 1000

from app import db
from app.models import (
    ConfigAuditEvent,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes import h, get_user_ctx


def require_super_admin(f):
    """Decorator to require SUPER_ADMIN role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import redirect, url_for, session, current_app

        # Check if user is authenticated
        if not session.get('active_user_id') and not current_app.config.get('DEV_LOGIN_ENABLED'):
            return redirect(url_for('auth.login_page'))

        user_ctx = get_user_ctx()
        if user_ctx.user_id is None:
            return redirect(url_for('auth.login_page'))

        if not user_ctx.is_super_admin:
            abort(403, "Super admin access required")
        return f(*args, **kwargs)
    return decorated_function


def require_budget_admin(f):
    """Decorator to require budget admin access (SUPER_ADMIN or WORKTYPE_ADMIN for budget)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import redirect, url_for, session, current_app
        from app.routes.work.helpers import is_budget_admin

        # Check if user is authenticated
        if not session.get('active_user_id') and not current_app.config.get('DEV_LOGIN_ENABLED'):
            return redirect(url_for('auth.login_page'))

        user_ctx = get_user_ctx()
        if user_ctx.user_id is None:
            return redirect(url_for('auth.login_page'))

        if not is_budget_admin(user_ctx):
            abort(403, "Budget admin access required")
        return f(*args, **kwargs)
    return decorated_function


def require_supply_admin(f):
    """Decorator to require supply admin access (SUPER_ADMIN or WORKTYPE_ADMIN for supply).

    Mirrors require_budget_admin: super admins pass automatically because
    is_worktype_admin() short-circuits on user_ctx.is_super_admin.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import redirect, url_for, session, current_app
        from app.routes.work.helpers import get_work_type_by_code, is_worktype_admin

        # Check if user is authenticated
        if not session.get('active_user_id') and not current_app.config.get('DEV_LOGIN_ENABLED'):
            return redirect(url_for('auth.login_page'))

        user_ctx = get_user_ctx()
        if user_ctx.user_id is None:
            return redirect(url_for('auth.login_page'))

        supply_wt = get_work_type_by_code("SUPPLY")  # 404s if not configured
        if not is_worktype_admin(user_ctx, supply_wt.id):
            abort(403, "Supply admin access required")
        return f(*args, **kwargs)
    return decorated_function


def require_any_worktype_admin(f):
    """Decorator to require admin access for any work type (SUPER_ADMIN or any WORKTYPE_ADMIN).

    Use on shared admin routes (approval groups, email templates, dispatch
    dashboard) where an admin of any work type has legitimate access.
    Per-work-type-scoped routes should still use require_budget_admin or
    a future require_worktype_admin(work_type_id) check.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import redirect, url_for, session, current_app
        from app.routes.work.helpers import is_any_worktype_admin

        if not session.get('active_user_id') and not current_app.config.get('DEV_LOGIN_ENABLED'):
            return redirect(url_for('auth.login_page'))

        user_ctx = get_user_ctx()
        if user_ctx.user_id is None:
            return redirect(url_for('auth.login_page'))

        if not is_any_worktype_admin(user_ctx):
            abort(403, "Work type admin access required")
        return f(*args, **kwargs)
    return decorated_function


def render_admin_config_page(template: str, **ctx):
    """Render an admin config page with user context. Requires SUPER_ADMIN."""
    user_ctx = get_user_ctx()
    if not user_ctx.is_super_admin:
        abort(403, "Super admin access required")
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_budget_admin_page(template: str, **ctx):
    """Render a budget admin page with user context. Requires budget admin access."""
    from app.routes.work.helpers import is_budget_admin

    user_ctx = get_user_ctx()
    if not is_budget_admin(user_ctx):
        abort(403, "Budget admin access required")
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_supply_admin_page(template: str, **ctx):
    """Render a supply admin page with user context. Requires supply admin access."""
    from app.routes.work.helpers import get_work_type_by_code, is_worktype_admin

    user_ctx = get_user_ctx()
    supply_wt = get_work_type_by_code("SUPPLY")  # 404s if not configured
    if not is_worktype_admin(user_ctx, supply_wt.id):
        abort(403, "Supply admin access required")
    return render_template(template, user_ctx=user_ctx, **ctx)


def render_admin_page(template: str, **ctx):
    """Render an admin page with user context. Does NOT check permissions - caller must verify."""
    user_ctx = get_user_ctx()
    return render_template(template, user_ctx=user_ctx, **ctx)


def log_config_change(
    entity_type: str,
    entity_id: int,
    action: str,
    changes: dict[str, Any] | None = None,
    user_id: str | None = None,
):
    """
    Log a configuration change to the audit table.

    Args:
        entity_type: Type of entity (expense_account, approval_group, etc.)
        entity_id: ID of the entity
        action: Action performed (CREATE, UPDATE, ARCHIVE, RESTORE)
        changes: Dictionary of changes (for UPDATE actions)
        user_id: User who made the change (defaults to current user)
    """
    if user_id is None:
        user_id = h.get_active_user_id()

    changes_json = json.dumps(changes) if changes else None

    event = ConfigAuditEvent(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        changes_json=changes_json,
        created_by_user_id=user_id,
    )
    db.session.add(event)


def track_changes(old_values: dict, new_values: dict) -> dict:
    """
    Compare old and new values and return a dict of changes.

    Returns:
        Dict with keys that changed, containing {"old": ..., "new": ...}
    """
    changes = {}
    all_keys = set(old_values.keys()) | set(new_values.keys())

    for key in all_keys:
        old_val = old_values.get(key)
        new_val = new_values.get(key)

        # Normalize None vs empty string
        if old_val == "":
            old_val = None
        if new_val == "":
            new_val = None

        if old_val != new_val:
            changes[key] = {"old": old_val, "new": new_val}

    return changes


def flash_errors(form_errors: dict[str, list[str]]):
    """Flash form validation errors."""
    for field, errors in form_errors.items():
        for error in errors:
            flash(f"{field}: {error}", "error")


def safe_redirect_url(url: str | None, fallback: str = "/") -> str:
    """
    Validate a user-supplied redirect URL to prevent open redirect attacks.

    Only allows relative paths on the same host. Rejects external URLs,
    javascript: URIs, data: URIs, protocol-relative URLs (including the
    backslash and embedded tab/CR/LF variants browsers normalize away).

    Returns the URL if safe, otherwise returns the fallback.
    """
    if not url or not url.strip():
        return fallback

    url = url.strip()

    # Reject embedded tab/CR/LF: browsers strip these during URL parsing
    # (WHATWG), so "/\t/evil.com" would be read as protocol-relative.
    if any(ch in url for ch in "\t\r\n"):
        return fallback

    # Only allow paths that start with / (relative to our host).
    # Reject protocol-relative URLs (//evil.com), absolute URLs, schemes,
    # and the backslash variant (/\evil.com — browsers normalize \ to /).
    if not url.startswith("/") or url[1:2] in ("/", "\\"):
        return fallback

    return url


def sort_with_override(model, name_attr=None):
    """
    Return ORDER BY clauses for nullable sort_order with alphabetical fallback.

    Items with sort_order set appear first (ordered by sort_order ASC, then name ASC).
    Items with sort_order NULL appear after, ordered by name ASC.

    Usage: .order_by(*sort_with_override(Model))
    """
    name_col = name_attr or model.name
    return (model.sort_order.is_(None), model.sort_order.asc(), name_col.asc())


def safe_int(value: str | None, default: int = 0) -> int:
    """
    Safely convert a string to int, returning default on failure.

    Args:
        value: String value to convert (e.g., from request.form.get())
        default: Value to return if conversion fails

    Returns:
        The integer value, or default if conversion fails
    """
    if not value:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_int_or_none(value: str | None) -> int | None:
    """
    Safely convert a string to int, returning None on failure.

    Args:
        value: String value to convert (e.g., from request.form.get())

    Returns:
        The integer value, or None if conversion fails or value is empty
    """
    if not value:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


# File upload validation constants
ALLOWED_UPLOAD_EXTENSIONS = {'.csv', '.xlsx', '.xls'}
ALLOWED_UPLOAD_MIME_TYPES = {
    'text/csv',
    'text/plain',  # Some systems send CSV as text/plain
    'application/csv',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_upload_file(file, flash_errors: bool = True) -> bool:
    """
    Validate an uploaded file for allowed extensions, MIME types, and size.

    Args:
        file: Flask FileStorage object from request.files
        flash_errors: If True, flash error messages for validation failures

    Returns:
        True if file is valid, False otherwise
    """
    if not file or not file.filename:
        if flash_errors:
            flash("No file selected", "error")
        return False

    # Check file extension
    filename_lower = file.filename.lower()
    ext = None
    for allowed_ext in ALLOWED_UPLOAD_EXTENSIONS:
        if filename_lower.endswith(allowed_ext):
            ext = allowed_ext
            break

    if not ext:
        if flash_errors:
            allowed = ', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            flash(f"Invalid file type. Allowed types: {allowed}", "error")
        return False

    # Check MIME type (if provided by browser)
    if file.content_type:
        # Normalize the MIME type (some browsers add charset)
        mime_type = file.content_type.split(';')[0].strip().lower()
        if mime_type not in ALLOWED_UPLOAD_MIME_TYPES:
            # Allow through if extension is valid - some browsers misreport MIME types
            # but still validate that it's not something obviously wrong
            dangerous_mimes = {'application/x-executable', 'application/x-msdownload'}
            if mime_type in dangerous_mimes:
                if flash_errors:
                    flash("File type not allowed", "error")
                return False

    # Check file size
    # Seek to end to get size, then back to start
    file.seek(0, 2)  # Seek to end
    size = file.tell()
    file.seek(0)  # Seek back to start

    if size > MAX_UPLOAD_SIZE_BYTES:
        if flash_errors:
            max_mb = MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
            flash(f"File too large. Maximum size is {max_mb:.0f} MB", "error")
        return False

    if size == 0:
        if flash_errors:
            flash("File is empty", "error")
        return False

    return True


def validate_code_length(code: str, entity_name: str = "Code") -> bool:
    """
    Validate that a code doesn't exceed the maximum length.

    Args:
        code: The code to validate
        entity_name: Name to use in error message (e.g., "Department code")

    Returns:
        True if valid, False if too long (and flashes an error message)
    """
    if len(code) > CODE_MAX_LENGTH:
        flash(
            f"{entity_name} must be {CODE_MAX_LENGTH} characters or less (currently {len(code)})",
            "error"
        )
        return False
    return True


# ============================================================
# Membership Management Permission Helpers
# ============================================================

def is_division_head(user_ctx, division_id: int, event_cycle_id: int) -> bool:
    """Check if user is a Division Head for a specific division and event cycle."""
    from app.models import DivisionMembership
    return DivisionMembership.query.filter_by(
        user_id=user_ctx.user_id,
        division_id=division_id,
        event_cycle_id=event_cycle_id,
        is_division_head=True,
    ).first() is not None


def is_department_head(user_ctx, department_id: int, event_cycle_id: int) -> bool:
    """Check if user is a Department Head for a specific department and event cycle."""
    from app.models import DepartmentMembership
    return DepartmentMembership.query.filter_by(
        user_id=user_ctx.user_id,
        department_id=department_id,
        event_cycle_id=event_cycle_id,
        is_department_head=True,
    ).first() is not None


def get_division_for_department(department_id: int) -> int | None:
    """Get the division_id for a department, or None if not found/no division."""
    from app.models import Department
    dept = db.session.get(Department, department_id)
    return dept.division_id if dept else None


def can_manage_department_members(user_ctx, department_id: int, event_cycle_id: int) -> bool:
    """
    Check if user can manage members for a department.

    Access granted to:
    - SUPER_ADMIN (any department)
    - Division Head (any department in their division)
    - Department Head (their own department only)
    """
    # Super admin can do anything
    if user_ctx.is_super_admin:
        return True

    # Check if Div Head for this department's division
    division_id = get_division_for_department(department_id)
    if division_id and is_division_head(user_ctx, division_id, event_cycle_id):
        return True

    # Check if DH for this specific department
    if is_department_head(user_ctx, department_id, event_cycle_id):
        return True

    return False


def can_edit_department_info(user_ctx, department_id: int, event_cycle_id: int) -> bool:
    """
    Check if user can edit department info (description, mailing list, slack channel).

    Access granted to:
    - SUPER_ADMIN (any department)
    - Division Head (any department in their division)
    - Department Head (their own department only)
    """
    # Same permission as managing members
    return can_manage_department_members(user_ctx, department_id, event_cycle_id)


def can_set_department_head(user_ctx, department_id: int, event_cycle_id: int) -> bool:
    """
    Check if user can set the is_department_head flag for a department.

    Only Super Admin and Division Heads can promote someone to Department Head.
    Department Heads themselves cannot make other people Department Heads.
    """
    if user_ctx.is_super_admin:
        return True

    # Check if Div Head for this department's division
    division_id = get_division_for_department(department_id)
    if division_id and is_division_head(user_ctx, division_id, event_cycle_id):
        return True

    return False


def can_set_department_head_any_cycle(user_ctx, department_id: int) -> bool:
    """Check if user can set the is_department_head flag for any active event cycle."""
    from app.models import EventCycle

    if user_ctx.is_super_admin:
        return True

    # Check all active event cycles
    active_cycles = EventCycle.query.filter_by(is_active=True).all()
    for cycle in active_cycles:
        if can_set_department_head(user_ctx, department_id, cycle.id):
            return True

    return False


def can_manage_department_members_any_cycle(user_ctx, department_id: int) -> bool:
    """
    Check if user can manage members for a department in ANY active event cycle.

    Used for list views where we show all event cycles.
    """
    from app.models import EventCycle

    if user_ctx.is_super_admin:
        return True

    # Check all active event cycles
    active_cycles = EventCycle.query.filter_by(is_active=True).all()
    for cycle in active_cycles:
        if can_manage_department_members(user_ctx, department_id, cycle.id):
            return True

    return False


def get_manageable_departments_for_user(user_ctx, event_cycle_id: int) -> list:
    """
    Get list of departments the user can manage members for.

    Returns list of Department objects the user can manage.
    """
    from app.models import Department, DepartmentMembership, DivisionMembership

    if user_ctx.is_super_admin:
        # Super admin can manage all departments
        return Department.query.filter_by(is_active=True).order_by(
            *sort_with_override(Department)
        ).all()

    manageable_dept_ids = set()

    # Departments where user is DH
    dh_memberships = DepartmentMembership.query.filter_by(
        user_id=user_ctx.user_id,
        event_cycle_id=event_cycle_id,
        is_department_head=True,
    ).all()
    for m in dh_memberships:
        manageable_dept_ids.add(m.department_id)

    # Departments in divisions where user is Div Head
    div_head_memberships = DivisionMembership.query.filter_by(
        user_id=user_ctx.user_id,
        event_cycle_id=event_cycle_id,
        is_division_head=True,
    ).all()
    for dm in div_head_memberships:
        # Get all departments in this division
        depts_in_div = Department.query.filter_by(
            division_id=dm.division_id,
            is_active=True,
        ).all()
        for dept in depts_in_div:
            manageable_dept_ids.add(dept.id)

    if not manageable_dept_ids:
        return []

    return Department.query.filter(
        Department.id.in_(manageable_dept_ids),
        Department.is_active == True,
    ).order_by(*sort_with_override(Department)).all()
