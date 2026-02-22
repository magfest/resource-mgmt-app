"""
Admin routes for contract type management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    ContractType,
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

contract_types_bp = Blueprint('contract_types', __name__, url_prefix='/contract-types')


def _get_contract_type_or_404(ct_id: int) -> ContractType:
    """Get contract type by ID or abort with 404."""
    ct = db.session.get(ContractType, ct_id)
    if not ct:
        abort(404, "Contract type not found")
    return ct


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


def _ct_to_dict(ct: ContractType) -> dict:
    """Convert contract type to dict for change tracking."""
    return {
        "code": ct.code,
        "name": ct.name,
        "description": ct.description,
        "approval_group_id": ct.approval_group_id,
        "is_active": ct.is_active,
        "sort_order": ct.sort_order,
    }


@contract_types_bp.get("/")
@require_super_admin
def list_contract_types():
    """List all contract types."""
    contract_types = (
        db.session.query(ContractType)
        .order_by(ContractType.sort_order, ContractType.name)
        .all()
    )
    return render_admin_config_page(
        "admin/contract_types/list.html",
        contract_types=contract_types,
    )


@contract_types_bp.get("/new")
@require_super_admin
def new_contract_type():
    """Show form to create a new contract type."""
    return render_admin_config_page(
        "admin/contract_types/form.html",
        contract_type=None,
        **_get_form_context(),
    )


@contract_types_bp.post("/new")
@require_super_admin
def create_contract_type():
    """Create a new contract type."""
    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()

    if not code or not name:
        flash("Code and name are required.", "error")
        return redirect(url_for('.new_contract_type'))

    if not validate_code_length(code):
        flash(f"Code must be {CODE_MAX_LENGTH} characters or less.", "error")
        return redirect(url_for('.new_contract_type'))

    # Check for duplicate code
    existing = db.session.query(ContractType).filter_by(code=code).first()
    if existing:
        flash(f"Contract type with code '{code}' already exists.", "error")
        return redirect(url_for('.new_contract_type'))

    ct = ContractType(
        code=code,
        name=name,
        description=request.form.get("description", "").strip() or None,
        approval_group_id=safe_int_or_none(request.form.get("approval_group_id")),
        is_active=bool(request.form.get("is_active")),
        sort_order=safe_int(request.form.get("sort_order"), 0),
        created_by_user_id=h.get_active_user_id(),
    )
    db.session.add(ct)
    db.session.flush()

    log_config_change("contract_type", ct.id, CONFIG_AUDIT_CREATE, {}, _ct_to_dict(ct))
    db.session.commit()

    flash(f"Contract type '{name}' created.", "success")
    return redirect(url_for('.list_contract_types'))


@contract_types_bp.get("/<int:ct_id>")
@require_super_admin
def edit_contract_type(ct_id: int):
    """Show form to edit a contract type."""
    ct = _get_contract_type_or_404(ct_id)
    return render_admin_config_page(
        "admin/contract_types/form.html",
        contract_type=ct,
        **_get_form_context(),
    )


@contract_types_bp.post("/<int:ct_id>")
@require_super_admin
def update_contract_type(ct_id: int):
    """Update a contract type."""
    ct = _get_contract_type_or_404(ct_id)
    old_state = _ct_to_dict(ct)

    code = request.form.get("code", "").strip().upper()
    name = request.form.get("name", "").strip()

    if not code or not name:
        flash("Code and name are required.", "error")
        return redirect(url_for('.edit_contract_type', ct_id=ct_id))

    if not validate_code_length(code):
        flash(f"Code must be {CODE_MAX_LENGTH} characters or less.", "error")
        return redirect(url_for('.edit_contract_type', ct_id=ct_id))

    # Check for duplicate code (excluding self)
    existing = db.session.query(ContractType).filter(
        ContractType.code == code,
        ContractType.id != ct_id
    ).first()
    if existing:
        flash(f"Contract type with code '{code}' already exists.", "error")
        return redirect(url_for('.edit_contract_type', ct_id=ct_id))

    ct.code = code
    ct.name = name
    ct.description = request.form.get("description", "").strip() or None
    ct.approval_group_id = safe_int_or_none(request.form.get("approval_group_id"))
    ct.is_active = bool(request.form.get("is_active"))
    ct.sort_order = safe_int(request.form.get("sort_order"), 0)
    ct.updated_by_user_id = h.get_active_user_id()

    new_state = _ct_to_dict(ct)
    changes = track_changes(old_state, new_state)
    if changes:
        log_config_change("contract_type", ct.id, CONFIG_AUDIT_UPDATE, old_state, new_state)

    db.session.commit()

    flash(f"Contract type '{name}' updated.", "success")
    return redirect(url_for('.list_contract_types'))
