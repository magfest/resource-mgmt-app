"""
Admin routes for expense account management.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    ExpenseAccount,
    ExpenseAccountEventOverride,
    ApprovalGroup,
    SpendType,
    Department,
    EventCycle,
    FrequencyOption,
    BudgetLineDetail,
    WorkItem,
    WorkLine,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    PROMPT_MODE_NONE,
    PROMPT_MODE_SUGGEST,
    PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    UI_GROUP_KNOWN_COSTS,
    WORK_ITEM_STATUS_DRAFT,
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

expense_accounts_bp = Blueprint('expense_accounts', __name__, url_prefix='/expense-accounts')


def _get_expense_account_or_404(account_id: int) -> ExpenseAccount:
    """Get expense account by ID or abort with 404."""
    account = db.session.get(ExpenseAccount, account_id)
    if not account:
        abort(404, "Expense account not found")
    return account


def _can_modify_expense_account(account_id: int) -> tuple[bool, str]:
    """
    Check if expense account can be modified.

    Returns (can_modify, reason) tuple.
    An account cannot be modified if it's referenced by any submitted (non-draft) request.
    """
    if account_id == "":
        return True, "New Account"
    else:
        count = (
        db.session.query(BudgetLineDetail)
        .join(WorkLine)
        .join(WorkItem)
        .filter(BudgetLineDetail.expense_account_id == account_id)
        .filter(WorkItem.status != WORK_ITEM_STATUS_DRAFT)
        .count()
    )
        if count > 0:
            #return False, f"Cannot modify: referenced by {count} submitted line(s)"
            return False, f"Should be false:( {count} {account_id})"
        return True, "Should be true"

def _get_form_context():
    """Get common form context data."""
    approval_groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)
        .order_by(ApprovalGroup.sort_order, ApprovalGroup.name)
        .all()
    )
    spend_types = (
        db.session.query(SpendType)
        .filter(SpendType.is_active == True)
        .order_by(SpendType.sort_order, SpendType.name)
        .all()
    )
    departments = (
        db.session.query(Department)
        .filter(Department.is_active == True)
        .order_by(Department.sort_order, Department.name)
        .all()
    )
    frequencies = (
        db.session.query(FrequencyOption)
        .filter(FrequencyOption.is_active == True)
        .order_by(FrequencyOption.sort_order, FrequencyOption.name)
        .all()
    )
    return {
        "approval_groups": approval_groups,
        "spend_types": spend_types,
        "departments": departments,
        "frequencies": frequencies,
        "spend_type_modes": [
            (SPEND_TYPE_MODE_SINGLE_LOCKED, "Single (Locked)"),
            (SPEND_TYPE_MODE_ALLOW_LIST, "Allow List"),
        ],
        "visibility_modes": [
            (VISIBILITY_MODE_ALL, "All Departments"),
            (VISIBILITY_MODE_RESTRICTED, "Restricted"),
        ],
        "prompt_modes": [
            (PROMPT_MODE_NONE, "None"),
            (PROMPT_MODE_SUGGEST, "Suggest"),
            (PROMPT_MODE_REQUIRE_EXPLICIT_NA, "Require Explicit N/A"),
        ],
    }


def _account_to_dict(account: ExpenseAccount) -> dict:
    """Convert expense account to dict for change tracking."""
    return {
        "code": account.code,
        "name": account.name,
        "description": account.description,
        "is_active": account.is_active,
        "is_contract_eligible": account.is_contract_eligible,
        "spend_type_mode": account.spend_type_mode,
        "default_spend_type_id": account.default_spend_type_id,
        "visibility_mode": account.visibility_mode,
        "approval_group_id": account.approval_group_id,
        "is_fixed_cost": account.is_fixed_cost,
        "default_unit_price_cents": account.default_unit_price_cents,
        "unit_price_locked": account.unit_price_locked,
        "default_frequency_id": account.default_frequency_id,
        "frequency_locked": account.frequency_locked,
        "warehouse_default": account.warehouse_default,
        "ui_display_group": account.ui_display_group,
        "prompt_mode": account.prompt_mode,
        "sort_order": account.sort_order,
    }


@expense_accounts_bp.get("/")
@require_super_admin
def list_expense_accounts():
    """List all expense accounts."""
    q = (request.args.get("q") or "").strip()
    show_inactive = request.args.get("show_inactive") == "1"
    approval_group_filter = request.args.get("approval_group")

    query = db.session.query(ExpenseAccount)

    if not show_inactive:
        query = query.filter(ExpenseAccount.is_active == True)

    if q:
        like = f"%{q}%"
        query = query.filter(
            (ExpenseAccount.code.ilike(like)) |
            (ExpenseAccount.name.ilike(like)) |
            (ExpenseAccount.description.ilike(like))
        )

    if approval_group_filter:
        query = query.filter(ExpenseAccount.approval_group_id == int(approval_group_filter))

    accounts = query.order_by(ExpenseAccount.sort_order, ExpenseAccount.name).all()

    approval_groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)
        .order_by(ApprovalGroup.sort_order, ApprovalGroup.name)
        .all()
    )

    return render_admin_config_page(
        "admin/expense_accounts/list.html",
        accounts=accounts,
        approval_groups=approval_groups,
        q=q,
        show_inactive=show_inactive,
        approval_group_filter=approval_group_filter,
    )


@expense_accounts_bp.get("/new")
@require_super_admin
def new_expense_account():
    """Show new expense account form."""
    return render_admin_config_page(
        "admin/expense_accounts/form.html",
        account=None,
        can_modify=True,
        modify_reason="new expense account",
        **_get_form_context(),
    )


@expense_accounts_bp.post("/")
@require_super_admin
def create_expense_account():
    """Create a new expense account."""
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    # Validate required fields
    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".new_expense_account"))

    # Check for duplicate code
    existing = db.session.query(ExpenseAccount).filter_by(code=code).first()
    if existing:
        flash(f"An expense account with code '{code}' already exists", "error")
        return redirect(url_for(".new_expense_account"))

    # Parse form data
    account = ExpenseAccount(
        code=code,
        name=name,
        description=(request.form.get("description") or "").strip() or None,
        is_active=request.form.get("is_active") == "1",
        is_contract_eligible=request.form.get("is_contract_eligible") == "1",
        spend_type_mode=request.form.get("spend_type_mode") or SPEND_TYPE_MODE_ALLOW_LIST,
        default_spend_type_id=int(request.form.get("default_spend_type_id")) if request.form.get("default_spend_type_id") else None,
        visibility_mode=request.form.get("visibility_mode") or VISIBILITY_MODE_ALL,
        approval_group_id=int(request.form.get("approval_group_id")) if request.form.get("approval_group_id") else None,
        is_fixed_cost=request.form.get("is_fixed_cost") == "1",
        default_unit_price_cents=_parse_price_cents(request.form.get("default_unit_price")),
        unit_price_locked=request.form.get("unit_price_locked") == "1",
        default_frequency_id=int(request.form.get("default_frequency_id")) if request.form.get("default_frequency_id") else None,
        frequency_locked=request.form.get("frequency_locked") == "1",
        warehouse_default=request.form.get("warehouse_default") == "1",
        ui_display_group=request.form.get("ui_display_group") or None,
        prompt_mode=request.form.get("prompt_mode") or PROMPT_MODE_NONE,
        sort_order=int(request.form.get("sort_order") or 0),
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )

    db.session.add(account)
    db.session.flush()

    # Handle allowed spend types
    allowed_spend_type_ids = request.form.getlist("allowed_spend_types")
    for st_id in allowed_spend_type_ids:
        st = db.session.get(SpendType, int(st_id))
        if st:
            account.allowed_spend_types.append(st)

    # Handle department restrictions
    if account.visibility_mode == VISIBILITY_MODE_RESTRICTED:
        dept_ids = request.form.getlist("visible_departments")
        for dept_id in dept_ids:
            dept = db.session.get(Department, int(dept_id))
            if dept:
                account.visible_to_departments.append(dept)

    # Log audit event
    log_config_change("expense_account", account.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash(f"Created expense account: {account.name}", "success")
    return redirect(url_for(".list_expense_accounts"))


@expense_accounts_bp.get("/<int:account_id>")
@require_super_admin
def edit_expense_account(account_id: int):
    """Show edit form for expense account."""
    account = _get_expense_account_or_404(account_id)
    can_modify, reason = _can_modify_expense_account(account_id)

    return render_admin_config_page(
        "admin/expense_accounts/form.html",
        account_id=f"accountid: {account_id}",
        account=account,
        can_modify=can_modify,
        modify_reason=reason,
        **_get_form_context(),
    )


@expense_accounts_bp.post("/<int:account_id>")
@require_super_admin
def update_expense_account(account_id: int):
    """Update an expense account."""
    account = _get_expense_account_or_404(account_id)

    # Check if can modify
    can_modify, reason = _can_modify_expense_account(account_id)
    if not can_modify:
        flash(reason, "error")
        return redirect(url_for(".edit_expense_account", account_id=account_id))

    # Track old values
    old_values = _account_to_dict(account)

    # Update fields
    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for(".edit_expense_account", account_id=account_id))

    # Check for duplicate code
    existing = db.session.query(ExpenseAccount).filter(
        ExpenseAccount.code == code,
        ExpenseAccount.id != account_id
    ).first()
    if existing:
        flash(f"An expense account with code '{code}' already exists", "error")
        return redirect(url_for(".edit_expense_account", account_id=account_id))

    account.code = code
    account.name = name
    account.description = (request.form.get("description") or "").strip() or None
    account.is_active = request.form.get("is_active") == "1"
    account.is_contract_eligible = request.form.get("is_contract_eligible") == "1"
    account.spend_type_mode = request.form.get("spend_type_mode") or SPEND_TYPE_MODE_ALLOW_LIST
    account.default_spend_type_id = int(request.form.get("default_spend_type_id")) if request.form.get("default_spend_type_id") else None
    account.visibility_mode = request.form.get("visibility_mode") or VISIBILITY_MODE_ALL
    account.approval_group_id = int(request.form.get("approval_group_id")) if request.form.get("approval_group_id") else None
    account.is_fixed_cost = request.form.get("is_fixed_cost") == "1"
    account.default_unit_price_cents = _parse_price_cents(request.form.get("default_unit_price"))
    account.unit_price_locked = request.form.get("unit_price_locked") == "1"
    account.default_frequency_id = int(request.form.get("default_frequency_id")) if request.form.get("default_frequency_id") else None
    account.frequency_locked = request.form.get("frequency_locked") == "1"
    account.warehouse_default = request.form.get("warehouse_default") == "1"
    account.ui_display_group = request.form.get("ui_display_group") or None
    account.prompt_mode = request.form.get("prompt_mode") or PROMPT_MODE_NONE
    account.sort_order = int(request.form.get("sort_order") or 0)
    account.updated_by_user_id = h.get_active_user_id()

    # Update allowed spend types
    account.allowed_spend_types.clear()
    allowed_spend_type_ids = request.form.getlist("allowed_spend_types")
    for st_id in allowed_spend_type_ids:
        st = db.session.get(SpendType, int(st_id))
        if st:
            account.allowed_spend_types.append(st)

    # Update department restrictions
    account.visible_to_departments.clear()
    if account.visibility_mode == VISIBILITY_MODE_RESTRICTED:
        dept_ids = request.form.getlist("visible_departments")
        for dept_id in dept_ids:
            dept = db.session.get(Department, int(dept_id))
            if dept:
                account.visible_to_departments.append(dept)

    # Track and log changes
    new_values = _account_to_dict(account)
    changes = track_changes(old_values, new_values)
    if changes:
        log_config_change("expense_account", account.id, CONFIG_AUDIT_UPDATE, changes)

    db.session.commit()
    flash(f"Updated expense account: {account.name}", "success")
    return redirect(url_for(".list_expense_accounts"))


@expense_accounts_bp.post("/<int:account_id>/archive")
@require_super_admin
def archive_expense_account(account_id: int):
    """Archive (soft-delete) an expense account."""
    account = _get_expense_account_or_404(account_id)

    if not account.is_active:
        flash("Account is already archived", "warning")
        return redirect(url_for(".list_expense_accounts"))

    account.is_active = False
    account.updated_by_user_id = h.get_active_user_id()

    log_config_change("expense_account", account.id, CONFIG_AUDIT_ARCHIVE)

    db.session.commit()
    flash(f"Archived expense account: {account.name}", "success")
    return redirect(url_for(".list_expense_accounts"))


@expense_accounts_bp.post("/<int:account_id>/restore")
@require_super_admin
def restore_expense_account(account_id: int):
    """Restore an archived expense account."""
    account = _get_expense_account_or_404(account_id)

    if account.is_active:
        flash("Account is already active", "warning")
        return redirect(url_for(".list_expense_accounts"))

    account.is_active = True
    account.updated_by_user_id = h.get_active_user_id()

    log_config_change("expense_account", account.id, CONFIG_AUDIT_RESTORE)

    db.session.commit()
    flash(f"Restored expense account: {account.name}", "success")
    return redirect(url_for(".list_expense_accounts"))


# --- Event Overrides ---

@expense_accounts_bp.get("/<int:account_id>/overrides")
@require_super_admin
def list_overrides(account_id: int):
    """List event overrides for an expense account."""
    account = _get_expense_account_or_404(account_id)

    overrides = (
        db.session.query(ExpenseAccountEventOverride)
        .filter(ExpenseAccountEventOverride.expense_account_id == account_id)
        .join(EventCycle)
        .order_by(EventCycle.sort_order, EventCycle.name)
        .all()
    )

    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .order_by(EventCycle.sort_order, EventCycle.name)
        .all()
    )

    # Find cycles without overrides
    override_cycle_ids = {o.event_cycle_id for o in overrides}
    available_cycles = [c for c in event_cycles if c.id not in override_cycle_ids]

    return render_admin_config_page(
        "admin/expense_accounts/overrides.html",
        account=account,
        overrides=overrides,
        available_cycles=available_cycles,
    )


@expense_accounts_bp.get("/<int:account_id>/overrides/new")
@require_super_admin
def new_override(account_id: int):
    """Show new event override form."""
    account = _get_expense_account_or_404(account_id)

    # Get cycles without existing overrides
    existing_cycle_ids = (
        db.session.query(ExpenseAccountEventOverride.event_cycle_id)
        .filter(ExpenseAccountEventOverride.expense_account_id == account_id)
        .all()
    )
    existing_cycle_ids = {r[0] for r in existing_cycle_ids}

    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .filter(~EventCycle.id.in_(existing_cycle_ids))
        .order_by(EventCycle.sort_order, EventCycle.name)
        .all()
    )

    if not event_cycles:
        flash("All active event cycles already have overrides", "warning")
        return redirect(url_for(".list_overrides", account_id=account_id))

    frequencies = (
        db.session.query(FrequencyOption)
        .filter(FrequencyOption.is_active == True)
        .order_by(FrequencyOption.sort_order)
        .all()
    )

    spend_types = (
        db.session.query(SpendType)
        .filter(SpendType.is_active == True)
        .order_by(SpendType.sort_order)
        .all()
    )

    return render_admin_config_page(
        "admin/expense_accounts/override_form.html",
        account=account,
        override=None,
        event_cycles=event_cycles,
        frequencies=frequencies,
        spend_types=spend_types,
    )


@expense_accounts_bp.post("/<int:account_id>/overrides")
@require_super_admin
def create_override(account_id: int):
    """Create a new event override."""
    account = _get_expense_account_or_404(account_id)

    event_cycle_id = request.form.get("event_cycle_id")
    if not event_cycle_id:
        flash("Event cycle is required", "error")
        return redirect(url_for(".new_override", account_id=account_id))

    # Check for existing override
    existing = (
        db.session.query(ExpenseAccountEventOverride)
        .filter_by(expense_account_id=account_id, event_cycle_id=int(event_cycle_id))
        .first()
    )
    if existing:
        flash("An override for this event cycle already exists", "error")
        return redirect(url_for(".new_override", account_id=account_id))

    override = ExpenseAccountEventOverride(
        expense_account_id=account_id,
        event_cycle_id=int(event_cycle_id),
        is_fixed_cost=_parse_optional_bool(request.form.get("is_fixed_cost")),
        default_unit_price_cents=_parse_price_cents(request.form.get("default_unit_price")),
        unit_price_locked=_parse_optional_bool(request.form.get("unit_price_locked")),
        default_frequency_id=int(request.form.get("default_frequency_id")) if request.form.get("default_frequency_id") else None,
        frequency_locked=_parse_optional_bool(request.form.get("frequency_locked")),
        warehouse_default=_parse_optional_bool(request.form.get("warehouse_default")),
        default_spend_type_id=int(request.form.get("default_spend_type_id")) if request.form.get("default_spend_type_id") else None,
        ui_display_group=request.form.get("ui_display_group") or None,
        prompt_mode=request.form.get("prompt_mode") or None,
    )

    db.session.add(override)
    db.session.flush()
    log_config_change("expense_account_override", override.id, CONFIG_AUDIT_CREATE)

    db.session.commit()
    flash("Created event override", "success")
    return redirect(url_for(".list_overrides", account_id=account_id))


@expense_accounts_bp.get("/<int:account_id>/overrides/<int:override_id>")
@require_super_admin
def edit_override(account_id: int, override_id: int):
    """Show edit form for event override."""
    account = _get_expense_account_or_404(account_id)

    override = db.session.get(ExpenseAccountEventOverride, override_id)
    if not override or override.expense_account_id != account_id:
        abort(404, "Override not found")

    frequencies = (
        db.session.query(FrequencyOption)
        .filter(FrequencyOption.is_active == True)
        .order_by(FrequencyOption.sort_order)
        .all()
    )

    spend_types = (
        db.session.query(SpendType)
        .filter(SpendType.is_active == True)
        .order_by(SpendType.sort_order)
        .all()
    )

    return render_admin_config_page(
        "admin/expense_accounts/override_form.html",
        account=account,
        override=override,
        event_cycles=None,  # Can't change cycle on edit
        frequencies=frequencies,
        spend_types=spend_types,
    )


@expense_accounts_bp.post("/<int:account_id>/overrides/<int:override_id>")
@require_super_admin
def update_override(account_id: int, override_id: int):
    """Update an event override."""
    account = _get_expense_account_or_404(account_id)

    override = db.session.get(ExpenseAccountEventOverride, override_id)
    if not override or override.expense_account_id != account_id:
        abort(404, "Override not found")

    override.is_fixed_cost = _parse_optional_bool(request.form.get("is_fixed_cost"))
    override.default_unit_price_cents = _parse_price_cents(request.form.get("default_unit_price"))
    override.unit_price_locked = _parse_optional_bool(request.form.get("unit_price_locked"))
    override.default_frequency_id = int(request.form.get("default_frequency_id")) if request.form.get("default_frequency_id") else None
    override.frequency_locked = _parse_optional_bool(request.form.get("frequency_locked"))
    override.warehouse_default = _parse_optional_bool(request.form.get("warehouse_default"))
    override.default_spend_type_id = int(request.form.get("default_spend_type_id")) if request.form.get("default_spend_type_id") else None
    override.ui_display_group = request.form.get("ui_display_group") or None
    override.prompt_mode = request.form.get("prompt_mode") or None

    log_config_change("expense_account_override", override.id, CONFIG_AUDIT_UPDATE)

    db.session.commit()
    flash("Updated event override", "success")
    return redirect(url_for(".list_overrides", account_id=account_id))


@expense_accounts_bp.post("/<int:account_id>/overrides/<int:override_id>/delete")
@require_super_admin
def delete_override(account_id: int, override_id: int):
    """Delete an event override."""
    account = _get_expense_account_or_404(account_id)

    override = db.session.get(ExpenseAccountEventOverride, override_id)
    if not override or override.expense_account_id != account_id:
        abort(404, "Override not found")

    db.session.delete(override)
    db.session.commit()

    flash("Deleted event override", "success")
    return redirect(url_for(".list_overrides", account_id=account_id))


# --- Helper Functions ---

def _parse_price_cents(value: str | None) -> int | None:
    """Parse a price string (e.g., "24.50") into cents."""
    if not value:
        return None

    value = value.strip().replace("$", "").replace(",", "")
    if not value:
        return None

    try:
        return int(float(value) * 100)
    except ValueError:
        return None


def _parse_optional_bool(value: str | None) -> bool | None:
    """Parse a checkbox value into optional bool (None if not checked, True/False otherwise)."""
    if value is None or value == "":
        return None
    return value == "1"
