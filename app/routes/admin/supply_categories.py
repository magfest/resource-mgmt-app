"""
Admin routes for supply category management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    SupplyCategory,
    ApprovalGroup,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    track_changes,
    validate_code_length,
    CODE_MAX_LENGTH,
    safe_int,
    safe_int_or_none,
)

supply_categories_bp = Blueprint('supply_categories', __name__, url_prefix='/supply-categories')


def _get_category_or_404(cat_id: int) -> SupplyCategory:
    """Get supply category by ID or abort with 404."""
    cat = db.session.get(SupplyCategory, cat_id)
    if not cat:
        abort(404, "Supply category not found")
    return cat


def _get_form_context():
    """Get common form context data."""
    approval_groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)
        .order_by(ApprovalGroup.sort_order, ApprovalGroup.name)
        .all()
    )
    return {
        "approval_groups": approval_groups,
    }


def _cat_to_dict(cat: SupplyCategory) -> dict:
    """Convert supply category to dict for change tracking."""
    return {
        "code": cat.code,
        "name": cat.name,
        "description": cat.description,
        "approval_group_id": cat.approval_group_id,
        "is_active": cat.is_active,
        "sort_order": cat.sort_order,
    }


@supply_categories_bp.get("/")
@require_super_admin
def list_supply_categories():
    """List all supply categories."""
    categories = (
        db.session.query(SupplyCategory)
        .order_by(SupplyCategory.sort_order, SupplyCategory.name)
        .all()
    )
    return render_admin_config_page(
        "admin/supply_categories/list.html",
        categories=categories,
    )


@supply_categories_bp.get("/new")
@require_super_admin
def new_supply_category():
    """Show form to create a new supply category."""
    return render_admin_config_page(
        "admin/supply_categories/form.html",
        category=None,
        **_get_form_context(),
    )


@supply_categories_bp.post("/new")
@require_super_admin
def create_supply_category():
    """Create a new supply category."""
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()

    if not code or not name:
        flash("Code and name are required.", "error")
        return redirect(url_for('.new_supply_category'))

    if not validate_code_length(code):
        flash(f"Code must be {CODE_MAX_LENGTH} characters or less.", "error")
        return redirect(url_for('.new_supply_category'))

    # Check for duplicate code
    existing = db.session.query(SupplyCategory).filter_by(code=code).first()
    if existing:
        flash(f"Supply category with code '{code}' already exists.", "error")
        return redirect(url_for('.new_supply_category'))

    cat = SupplyCategory(
        code=code,
        name=name,
        description=request.form.get("description", "").strip() or None,
        approval_group_id=safe_int_or_none(request.form.get("approval_group_id")),
        is_active=bool(request.form.get("is_active")),
        sort_order=safe_int(request.form.get("sort_order"), 0),
        created_by_user_id=h.get_active_user_id(),
    )
    db.session.add(cat)
    db.session.flush()

    log_config_change("supply_category", cat.id, CONFIG_AUDIT_CREATE, {}, _cat_to_dict(cat))
    db.session.commit()

    flash(f"Supply category '{name}' created.", "success")
    return redirect(url_for('.list_supply_categories'))


@supply_categories_bp.get("/<int:cat_id>")
@require_super_admin
def edit_supply_category(cat_id: int):
    """Show form to edit a supply category."""
    cat = _get_category_or_404(cat_id)
    return render_admin_config_page(
        "admin/supply_categories/form.html",
        category=cat,
        **_get_form_context(),
    )


@supply_categories_bp.post("/<int:cat_id>")
@require_super_admin
def update_supply_category(cat_id: int):
    """Update a supply category."""
    cat = _get_category_or_404(cat_id)
    old_state = _cat_to_dict(cat)

    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()

    if not code or not name:
        flash("Code and name are required.", "error")
        return redirect(url_for('.edit_supply_category', cat_id=cat_id))

    if not validate_code_length(code):
        flash(f"Code must be {CODE_MAX_LENGTH} characters or less.", "error")
        return redirect(url_for('.edit_supply_category', cat_id=cat_id))

    # Check for duplicate code (excluding self)
    existing = db.session.query(SupplyCategory).filter(
        SupplyCategory.code == code,
        SupplyCategory.id != cat_id
    ).first()
    if existing:
        flash(f"Supply category with code '{code}' already exists.", "error")
        return redirect(url_for('.edit_supply_category', cat_id=cat_id))

    cat.code = code
    cat.name = name
    cat.description = request.form.get("description", "").strip() or None
    cat.approval_group_id = safe_int_or_none(request.form.get("approval_group_id"))
    cat.is_active = bool(request.form.get("is_active"))
    cat.sort_order = safe_int(request.form.get("sort_order"), 0)
    cat.updated_by_user_id = h.get_active_user_id()

    new_state = _cat_to_dict(cat)
    changes = track_changes(old_state, new_state)
    if changes:
        log_config_change("supply_category", cat.id, CONFIG_AUDIT_UPDATE, old_state, new_state)

    db.session.commit()

    flash(f"Supply category '{name}' updated.", "success")
    return redirect(url_for('.list_supply_categories'))
