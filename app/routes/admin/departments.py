"""
Admin routes for department management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    Department,
    DepartmentMembership,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    track_changes,
)

departments_bp = Blueprint('departments', __name__, url_prefix='/departments')


def _get_department_or_404(dept_id: int) -> Department:
    """Get department by ID or abort with 404."""
    dept = db.session.get(Department, dept_id)
    if not dept:
        abort(404, "Department not found")
    return dept


def _dept_to_dict(dept: Department) -> dict:
    """Convert department to dict for change tracking."""
    return {
        "code": dept.code,
        "name": dept.name,
        "description": dept.description,
        "mailing_list": dept.mailing_list,
        "slack_channel": dept.slack_channel,
        "is_active": dept.is_active,
        "sort_order": dept.sort_order,
    }


@departments_bp.get("/")
@require_super_admin
def list_departments():
    """List all departments."""
    show_inactive = request.args.get("show_inactive") == "1"

    query = db.session.query(Department)
    if not show_inactive:
        query = query.filter(Department.is_active == True)

    departments = query.order_by(Department.sort_order, Department.name).all()

    # Get member counts per department
    member_counts = {}
    for dept in departments:
        count = (
            db.session.query(DepartmentMembership)
            .filter(DepartmentMembership.department_id == dept.id)
            .count()
        )
        member_counts[dept.id] = count

    return render_admin_config_page(
        "admin/departments/list.html",
        departments=departments,
        member_counts=member_counts,
        show_inactive=show_inactive,
    )


@departments_bp.get("/new")
@require_super_admin
def new_department():
    """Show new department form."""
    return render_admin_config_page(
        "admin/departments/form.html",
        department=None,
    )


@departments_bp.post("/")
@require_super_admin
def create_department():
    """Create a new department."""
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".new_department"))

    # Check for duplicate code
    existing = db.session.query(Department).filter_by(code=code).first()
    if existing:
        flash(f"A department with code '{code}' already exists", "error")
        return redirect(url_for(".new_department"))

    dept = Department(
        code=code,
        name=name,
        description=(request.form.get("description") or "").strip() or None,
        mailing_list=(request.form.get("mailing_list") or "").strip() or None,
        slack_channel=(request.form.get("slack_channel") or "").strip() or None,
        is_active=request.form.get("is_active") == "1",
        sort_order=int(request.form.get("sort_order") or 0),
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )

    db.session.add(dept)
    db.session.flush()

    log_config_change("department", dept.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created department: {dept.name}", "success")
    return redirect(url_for(".list_departments"))


@departments_bp.get("/<int:dept_id>")
@require_super_admin
def edit_department(dept_id: int):
    """Show edit form for department."""
    dept = _get_department_or_404(dept_id)

    # Get member count
    member_count = (
        db.session.query(DepartmentMembership)
        .filter(DepartmentMembership.department_id == dept_id)
        .count()
    )

    return render_admin_config_page(
        "admin/departments/form.html",
        department=dept,
        member_count=member_count,
    )


@departments_bp.post("/<int:dept_id>")
@require_super_admin
def update_department(dept_id: int):
    """Update a department."""
    dept = _get_department_or_404(dept_id)

    old_values = _dept_to_dict(dept)

    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".edit_department", dept_id=dept_id))

    # Check for duplicate code
    existing = db.session.query(Department).filter(
        Department.code == code,
        Department.id != dept_id
    ).first()
    if existing:
        flash(f"A department with code '{code}' already exists", "error")
        return redirect(url_for(".edit_department", dept_id=dept_id))

    dept.code = code
    dept.name = name
    dept.description = (request.form.get("description") or "").strip() or None
    dept.mailing_list = (request.form.get("mailing_list") or "").strip() or None
    dept.slack_channel = (request.form.get("slack_channel") or "").strip() or None
    dept.is_active = request.form.get("is_active") == "1"
    dept.sort_order = int(request.form.get("sort_order") or 0)
    dept.updated_by_user_id = h.get_active_user_id()

    new_values = _dept_to_dict(dept)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("department", dept.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated department: {dept.name}", "success")
    return redirect(url_for(".list_departments"))


@departments_bp.post("/<int:dept_id>/archive")
@require_super_admin
def archive_department(dept_id: int):
    """Archive (soft-delete) a department."""
    dept = _get_department_or_404(dept_id)

    if not dept.is_active:
        flash("Department is already archived", "warning")
        return redirect(url_for(".list_departments"))

    dept.is_active = False
    dept.updated_by_user_id = h.get_active_user_id()

    log_config_change("department", dept.id, CONFIG_AUDIT_ARCHIVE)

    db.session.commit()
    flash(f"Archived department: {dept.name}", "success")
    return redirect(url_for(".list_departments"))


@departments_bp.post("/<int:dept_id>/restore")
@require_super_admin
def restore_department(dept_id: int):
    """Restore an archived department."""
    dept = _get_department_or_404(dept_id)

    if dept.is_active:
        flash("Department is already active", "warning")
        return redirect(url_for(".list_departments"))

    dept.is_active = True
    dept.updated_by_user_id = h.get_active_user_id()

    log_config_change("department", dept.id, CONFIG_AUDIT_RESTORE)

    db.session.commit()
    flash(f"Restored department: {dept.name}", "success")
    return redirect(url_for(".list_departments"))
