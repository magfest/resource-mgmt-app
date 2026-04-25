"""
Admin routes for bulk data upload (departments, divisions, expense accounts).

Accepts CSV and Excel files, auto-generates codes if not provided,
and updates existing records (matched by code) or creates new ones.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import Blueprint, redirect, url_for, request, flash, Response

from app import db
from app.models import (
    Department,
    Division,
    ExpenseAccount,
    User,
    UserRole,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    EventCycle,
    ApprovalGroup,
    SpendType,
    FrequencyOption,
    WorkType,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    ROLE_SUPER_ADMIN,
    ROLE_APPROVER,
    ROLE_WORKTYPE_ADMIN,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    PROMPT_MODE_NONE,
    PROMPT_MODE_SUGGEST,
    PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    UI_GROUP_KNOWN_COSTS,
    UI_GROUP_HOTEL_SERVICES,
)
from app.routes import h
from .helpers import (
    require_super_admin,
    render_admin_config_page,
    log_config_change,
    CODE_MAX_LENGTH,
    validate_upload_file,
    safe_int_or_none,
    sort_with_override,
)

data_upload_bp = Blueprint('data_upload', __name__, url_prefix='/data-upload')

# Maximum rows to read from uploaded files (prevents memory exhaustion)
MAX_UPLOAD_ROWS = 10000


# ============================================================
# Column name mapping - supports flexible CSV formats
# ============================================================

# Department column aliases
DEPT_NAME_COLS = ['name', 'department', 'department_name', 'department/team', 'team']
DEPT_CODE_COLS = ['code', 'dept_code', 'department_code']
DEPT_DIV_COLS = ['division', 'division_name', 'division_code']
DEPT_DESC_COLS = ['description', 'desc', 'notes']
DEPT_EMAIL_COLS = ['mailing_list', 'email', 'contact_email', 'group_email']
DEPT_SLACK_COLS = ['slack_channel', 'slack', 'channel']
DEPT_QB_CLASS_COLS = ['qb_class', 'quickbooks_class', 'qb class']

# Division column aliases
DIV_NAME_COLS = ['name', 'division', 'division_name']
DIV_CODE_COLS = ['code', 'div_code', 'division_code']
DIV_DESC_COLS = ['description', 'desc', 'notes']
DIV_QB_CLASS_COLS = ['qb_class', 'quickbooks_class', 'qb class']

# Expense account column aliases
EA_NAME_COLS = ['name', 'account_name', 'expense_account', 'account']
EA_CODE_COLS = ['code', 'account_code', 'expense_code']
EA_DESC_COLS = ['description', 'desc', 'notes']
EA_QB_COLS = ['quickbooks', 'quickbooks_account', 'qb_account', 'quickbooks_account_name']
EA_APPROVAL_GROUP_COLS = ['approval_group', 'group', 'approval_group_code']
EA_SPEND_TYPE_COLS = ['spend_type', 'default_spend_type', 'spend_type_code']
EA_ALLOWED_SPEND_TYPES_COLS = ['allowed_spend_types', 'spend_types', 'allowed_types']
EA_SPEND_MODE_COLS = ['spend_type_mode', 'spend_mode']
EA_FIXED_COST_COLS = ['is_fixed_cost', 'fixed_cost', 'fixed']
EA_UNIT_PRICE_COLS = ['unit_price', 'default_unit_price', 'price']
EA_PRICE_LOCKED_COLS = ['unit_price_locked', 'price_locked', 'locked']
EA_VISIBILITY_COLS = ['visibility_mode', 'visibility', 'dept_visibility']
EA_CONTRACT_COLS = ['is_contract_eligible', 'contract_eligible', 'contract']
EA_FREQUENCY_COLS = ['frequency', 'default_frequency', 'frequency_code']
EA_FREQ_LOCKED_COLS = ['frequency_locked', 'freq_locked']
EA_WAREHOUSE_COLS = ['warehouse_default', 'warehouse']
EA_UI_GROUP_COLS = ['ui_display_group', 'ui_group', 'display_group']
EA_PROMPT_MODE_COLS = ['prompt_mode', 'prompt']
EA_SORT_COLS = ['sort_order', 'sort', 'order']
EA_ACTIVE_COLS = ['is_active', 'active']

# Valid spend type modes
SPEND_TYPE_MODES = {
    'SINGLE_LOCKED': SPEND_TYPE_MODE_SINGLE_LOCKED,
    'SINGLE': SPEND_TYPE_MODE_SINGLE_LOCKED,
    'LOCKED': SPEND_TYPE_MODE_SINGLE_LOCKED,
    'ALLOW_LIST': SPEND_TYPE_MODE_ALLOW_LIST,
    'LIST': SPEND_TYPE_MODE_ALLOW_LIST,
    'ALLOW': SPEND_TYPE_MODE_ALLOW_LIST,
}

# Valid visibility modes
VISIBILITY_MODES = {
    'ALL': VISIBILITY_MODE_ALL,
    'ALL_DEPARTMENTS': VISIBILITY_MODE_ALL,
    'RESTRICTED': VISIBILITY_MODE_RESTRICTED,
}

# Valid prompt modes
PROMPT_MODES = {
    'NONE': PROMPT_MODE_NONE,
    '': PROMPT_MODE_NONE,
    'SUGGEST': PROMPT_MODE_SUGGEST,
    'REQUIRE': PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    'REQUIRE_EXPLICIT_NA': PROMPT_MODE_REQUIRE_EXPLICIT_NA,
}

# User column aliases
USER_EMAIL_COLS = ['email', 'email_address', 'user_email']
USER_NAME_COLS = ['name', 'display_name', 'full_name', 'user_name']
USER_ID_COLS = ['id', 'user_id', 'username']

# Department membership column aliases
DM_USER_COLS = ['email', 'user_email', 'user', 'user_id']
DM_DEPT_COLS = ['department', 'dept', 'department_code', 'dept_code']
DM_CYCLE_COLS = ['event_cycle', 'cycle', 'event', 'event_code']
DM_HEAD_COLS = ['is_department_head', 'department_head', 'head', 'dh', 'is_head']

# Division membership column aliases
DVM_USER_COLS = ['email', 'user_email', 'user', 'user_id']
DVM_DIV_COLS = ['division', 'div', 'division_code', 'div_code']
DVM_CYCLE_COLS = ['event_cycle', 'cycle', 'event', 'event_code']
DVM_HEAD_COLS = ['is_division_head', 'division_head', 'head', 'is_head']

# User role column aliases
UR_USER_COLS = ['email', 'user_email', 'user', 'user_id']
UR_ROLE_COLS = ['role', 'role_code', 'role_name']
UR_APPROVAL_GROUP_COLS = ['approval_group', 'group', 'approval_group_code']

# Valid role codes for upload
VALID_ROLE_CODES = {
    'SUPER_ADMIN': ROLE_SUPER_ADMIN,
    'APPROVER': ROLE_APPROVER,
    'WORKTYPE_ADMIN': ROLE_WORKTYPE_ADMIN,
}


def _find_column(df: pd.DataFrame, aliases: list[str]) -> str | None:
    """Find a column in the DataFrame matching one of the aliases (case-insensitive)."""
    df_cols_lower = {col.lower().strip(): col for col in df.columns}
    for alias in aliases:
        if alias.lower() in df_cols_lower:
            return df_cols_lower[alias.lower()]
    return None


def _generate_code(name: str) -> str:
    """Generate a code from a name by removing special chars and uppercasing."""
    # Remove non-alphanumeric chars (except spaces), uppercase, take first N chars
    cleaned = re.sub(r'[^a-zA-Z0-9\s]', '', name)
    code = cleaned.upper().replace(' ', '_')[:CODE_MAX_LENGTH]
    return code or 'UNKNOWN'


def _read_uploaded_file(file) -> pd.DataFrame | None:
    """
    Validate and read an uploaded file (CSV or Excel) into a DataFrame.

    Validates file extension, MIME type, and size before reading.
    """
    import pandas as pd

    # Validate file first
    if not validate_upload_file(file):
        return None

    filename = file.filename.lower()
    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(file, dtype=str, keep_default_na=False, nrows=MAX_UPLOAD_ROWS)
        elif filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file, dtype=str, keep_default_na=False, nrows=MAX_UPLOAD_ROWS)
        else:
            flash("Unsupported file type. Please upload a CSV or Excel file.", "error")
            return None

        if len(df) == MAX_UPLOAD_ROWS:
            flash(f"Warning: File truncated to {MAX_UPLOAD_ROWS:,} rows.", "warning")
        return df
    except Exception as e:
        flash(f"Error reading file: {str(e)}", "error")
        return None


def _get_cell_value(row, col_name: str | None) -> str | None:
    """Safely get a trimmed string value from a DataFrame row."""
    import pandas as pd

    if col_name is None:
        return None
    val = row.get(col_name, '')
    if pd.isna(val):
        return None
    val = str(val).strip()
    return val if val else None


def _parse_bool(value: str | None) -> bool:
    """Parse a boolean value from CSV (handles various true/false representations)."""
    if not value:
        return False
    val_lower = value.lower().strip()
    return val_lower in ('true', 'yes', '1', 'x', 'y', 'on')


def _detect_work_type_columns(df: pd.DataFrame) -> dict:
    """
    Detect work type columns in the DataFrame.

    Looks for columns named {work_type_code}_view and {work_type_code}_edit
    (case-insensitive) for each active work type.

    Returns a dict mapping work_type_id to
    {"work_type": WorkType, "view_col": str|None, "edit_col": str|None}.
    Only includes work types that have at least one matching column.
    """
    work_types = db.session.query(WorkType).filter(WorkType.is_active == True).all()
    df_cols_lower = {col.lower().strip(): col for col in df.columns}
    result = {}
    for wt in work_types:
        code_lower = wt.code.lower()
        view_col = df_cols_lower.get(f"{code_lower}_view")
        edit_col = df_cols_lower.get(f"{code_lower}_edit")
        if view_col or edit_col:
            result[wt.id] = {
                "work_type": wt,
                "view_col": view_col,
                "edit_col": edit_col,
            }
    return result


def _apply_work_type_access(membership, access_model_class, membership_fk_name, wt_columns, row):
    """
    Create or update WorkTypeAccess records for a membership based on CSV row data.

    Args:
        membership: DepartmentMembership or DivisionMembership instance (must be flushed/have an id)
        access_model_class: DepartmentMembershipWorkTypeAccess or DivisionMembershipWorkTypeAccess
        membership_fk_name: FK column name, e.g. "department_membership_id"
        wt_columns: dict from _detect_work_type_columns()
        row: pandas DataFrame row
    """
    for wt_id, col_info in wt_columns.items():
        can_view = _parse_bool(_get_cell_value(row, col_info["view_col"]))
        can_edit = _parse_bool(_get_cell_value(row, col_info["edit_col"]))

        existing_access = (
            db.session.query(access_model_class)
            .filter_by(**{membership_fk_name: membership.id}, work_type_id=wt_id)
            .first()
        )

        if existing_access:
            existing_access.can_view = can_view
            existing_access.can_edit = can_edit
        elif can_view or can_edit:
            access = access_model_class(
                **{membership_fk_name: membership.id},
                work_type_id=wt_id,
                can_view=can_view,
                can_edit=can_edit,
            )
            db.session.add(access)


def _generate_user_id(email: str) -> str:
    """Generate a user ID from an email address."""
    # Use the local part of the email, prefixed with 'user:'
    local_part = email.split('@')[0].lower()
    # Clean up any special characters
    cleaned = re.sub(r'[^a-z0-9._-]', '', local_part)
    return f"user:{cleaned}"


def _find_user_by_identifier(identifier: str) -> User | None:
    """Find a user by email or ID."""
    if not identifier:
        return None
    # Try email first
    user = db.session.query(User).filter(User.email.ilike(identifier)).first()
    if user:
        return user
    # Try ID
    return db.session.get(User, identifier)


CENTS_PER_DOLLAR = 100


def _parse_dollars_to_cents(value: str | None) -> int | None:
    """Parse a dollar amount string to cents (integer)."""
    if not value:
        return None
    # Remove currency symbols and commas
    cleaned = value.replace('$', '').replace(',', '').strip()
    if not cleaned:
        return None
    try:
        dollars = Decimal(cleaned)
        return int(dollars * CENTS_PER_DOLLAR)
    except (ValueError, InvalidOperation):
        return None


def _parse_int(value: str | None) -> int | None:
    """Parse an integer value from CSV."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _build_lookup_dicts(model_class):
    """
    Build lookup dictionaries for a model, keyed by code and name.

    Returns a tuple of (by_code, by_name) dictionaries.
    - by_code: maps uppercase code to entity
    - by_name: maps lowercase name to entity

    Example:
        divisions_by_code, divisions_by_name = _build_lookup_dicts(Division)
    """
    all_items = db.session.query(model_class).all()
    by_code = {item.code.upper(): item for item in all_items}
    by_name = {item.name.lower(): item for item in all_items}
    return by_code, by_name


def _build_code_lookup(model_class) -> dict:
    """
    Build a simple lookup dictionary keyed by uppercase code.

    Use this for models where you only need to look up by code (e.g., EventCycle).

    Example:
        cycles_by_code = _build_code_lookup(EventCycle)
        cycle = cycles_by_code.get(code_value.upper())
    """
    all_items = db.session.query(model_class).all()
    return {item.code.upper(): item for item in all_items}


def _lookup_entity(value: str | None, by_code: dict, by_name: dict):
    """
    Look up an entity by code or name, returning the entity object.

    Tries to match by code first (case-insensitive), then by name.
    Returns None if no match found or value is empty.

    Example:
        dept = _lookup_entity(dept_value, depts_by_code, depts_by_name)
    """
    if not value:
        return None

    # Try matching by code first
    if value.upper() in by_code:
        return by_code[value.upper()]

    # Then try matching by name
    if value.lower() in by_name:
        return by_name[value.lower()]

    return None


def _lookup_entity_id(value: str | None, by_code: dict, by_name: dict) -> int | None:
    """
    Look up an entity by code or name, returning its ID.

    Tries to match by code first (case-insensitive), then by name.
    Returns None if no match found or value is empty.

    Example:
        division_id = _lookup_entity_id(div_value, divisions_by_code, divisions_by_name)
    """
    entity = _lookup_entity(value, by_code, by_name)
    return entity.id if entity else None


def _lookup_mode_value(value: str | None, modes_dict: dict, default):
    """
    Look up a mode/enum value from a mapping dictionary.

    Normalizes the input (uppercase, underscores for spaces) before lookup.
    Returns the default if no match found or value is empty.

    Example:
        spend_mode = _lookup_mode_value(mode_str, SPEND_TYPE_MODES, SPEND_TYPE_MODE_ALLOW_LIST)
    """
    if not value:
        return default

    normalized = value.upper().replace(' ', '_')
    return modes_dict.get(normalized, default)


# ============================================================
# Index page
# ============================================================

@data_upload_bp.get("/")
@require_super_admin
def index():
    """Show data upload options."""
    return render_admin_config_page("admin/data_upload/index.html")


# ============================================================
# Department Upload
# ============================================================

@data_upload_bp.get("/departments")
@require_super_admin
def departments_form():
    """Show department upload form."""
    return render_admin_config_page("admin/data_upload/departments.html")


@data_upload_bp.post("/departments")
@require_super_admin
def departments_upload():
    """Process department upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.departments_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.departments_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.departments_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.departments_form'))

    # Find columns
    name_col = _find_column(df, DEPT_NAME_COLS)
    code_col = _find_column(df, DEPT_CODE_COLS)
    div_col = _find_column(df, DEPT_DIV_COLS)
    desc_col = _find_column(df, DEPT_DESC_COLS)
    email_col = _find_column(df, DEPT_EMAIL_COLS)
    slack_col = _find_column(df, DEPT_SLACK_COLS)
    qb_class_col = _find_column(df, DEPT_QB_CLASS_COLS)

    if not name_col:
        flash(f"Could not find a name column. Expected one of: {', '.join(DEPT_NAME_COLS)}", "error")
        return redirect(url_for('.departments_form'))

    # Build division lookup for matching by code or name
    divisions_by_code, divisions_by_name = _build_lookup_dicts(Division)

    created = 0
    updated = 0
    errors = []
    user_id = h.get_active_user_id()

    for idx, row in df.iterrows():
        name = _get_cell_value(row, name_col)
        if not name:
            continue

        code = _get_cell_value(row, code_col)
        if not code:
            code = _generate_code(name)

        code = code.upper()[:CODE_MAX_LENGTH]

        # Find division if provided
        div_value = _get_cell_value(row, div_col)
        division_id = _lookup_entity_id(div_value, divisions_by_code, divisions_by_name)

        # Check for existing department
        existing = db.session.query(Department).filter_by(code=code).first()

        if existing:
            # Update existing
            old_name = existing.name
            existing.name = name
            existing.division_id = division_id
            if desc_col:
                existing.description = _get_cell_value(row, desc_col)
            if email_col:
                existing.mailing_list = _get_cell_value(row, email_col)
            if slack_col:
                existing.slack_channel = _get_cell_value(row, slack_col)
            if qb_class_col:
                existing.qb_class = _get_cell_value(row, qb_class_col)
            existing.updated_by_user_id = user_id

            log_config_change("department", existing.id, CONFIG_AUDIT_UPDATE, {
                "name": {"old": old_name, "new": name},
                "source": "bulk_upload",
            })
            updated += 1
        else:
            # Create new
            dept = Department(
                code=code,
                name=name,
                division_id=division_id,
                description=_get_cell_value(row, desc_col),
                mailing_list=_get_cell_value(row, email_col),
                slack_channel=_get_cell_value(row, slack_col),
                qb_class=_get_cell_value(row, qb_class_col),
                is_active=True,
                sort_order=0,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
            db.session.add(dept)
            db.session.flush()

            log_config_change("department", dept.id, CONFIG_AUDIT_CREATE, {
                "source": "bulk_upload",
            })
            created += 1

    db.session.commit()

    flash(f"Departments: {created} created, {updated} updated", "success")
    return redirect(url_for('admin_config.departments.list_departments'))


# ============================================================
# Division Upload
# ============================================================

@data_upload_bp.get("/divisions")
@require_super_admin
def divisions_form():
    """Show division upload form."""
    return render_admin_config_page("admin/data_upload/divisions.html")


@data_upload_bp.post("/divisions")
@require_super_admin
def divisions_upload():
    """Process division upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.divisions_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.divisions_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.divisions_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.divisions_form'))

    # Find columns
    name_col = _find_column(df, DIV_NAME_COLS)
    code_col = _find_column(df, DIV_CODE_COLS)
    desc_col = _find_column(df, DIV_DESC_COLS)
    qb_class_col = _find_column(df, DIV_QB_CLASS_COLS)

    if not name_col:
        flash(f"Could not find a name column. Expected one of: {', '.join(DIV_NAME_COLS)}", "error")
        return redirect(url_for('.divisions_form'))

    created = 0
    updated = 0
    user_id = h.get_active_user_id()

    for idx, row in df.iterrows():
        name = _get_cell_value(row, name_col)
        if not name:
            continue

        code = _get_cell_value(row, code_col)
        if not code:
            code = _generate_code(name)

        code = code.upper()[:CODE_MAX_LENGTH]

        # Check for existing division
        existing = db.session.query(Division).filter_by(code=code).first()

        if existing:
            # Update existing
            old_name = existing.name
            existing.name = name
            if desc_col:
                existing.description = _get_cell_value(row, desc_col)
            if qb_class_col:
                existing.qb_class = _get_cell_value(row, qb_class_col)

            log_config_change("division", existing.id, CONFIG_AUDIT_UPDATE, {
                "name": {"old": old_name, "new": name},
                "source": "bulk_upload",
            })
            updated += 1
        else:
            # Create new
            div = Division(
                code=code,
                name=name,
                description=_get_cell_value(row, desc_col),
                qb_class=_get_cell_value(row, qb_class_col),
                is_active=True,
                sort_order=0,
            )
            db.session.add(div)
            db.session.flush()

            log_config_change("division", div.id, CONFIG_AUDIT_CREATE, {
                "source": "bulk_upload",
            })
            created += 1

    db.session.commit()

    flash(f"Divisions: {created} created, {updated} updated", "success")
    return redirect(url_for('admin_config.divisions.list_divisions'))


# ============================================================
# Expense Account Upload
# ============================================================

@data_upload_bp.get("/expense-accounts")
@require_super_admin
def expense_accounts_form():
    """Show expense account upload form."""
    return render_admin_config_page("admin/data_upload/expense_accounts.html")


@data_upload_bp.post("/expense-accounts")
@require_super_admin
def expense_accounts_upload():
    """Process expense account upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.expense_accounts_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.expense_accounts_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.expense_accounts_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.expense_accounts_form'))

    # Find columns
    name_col = _find_column(df, EA_NAME_COLS)
    code_col = _find_column(df, EA_CODE_COLS)
    desc_col = _find_column(df, EA_DESC_COLS)
    qb_col = _find_column(df, EA_QB_COLS)
    approval_group_col = _find_column(df, EA_APPROVAL_GROUP_COLS)
    spend_type_col = _find_column(df, EA_SPEND_TYPE_COLS)
    allowed_spend_types_col = _find_column(df, EA_ALLOWED_SPEND_TYPES_COLS)
    spend_mode_col = _find_column(df, EA_SPEND_MODE_COLS)
    fixed_cost_col = _find_column(df, EA_FIXED_COST_COLS)
    unit_price_col = _find_column(df, EA_UNIT_PRICE_COLS)
    price_locked_col = _find_column(df, EA_PRICE_LOCKED_COLS)
    visibility_col = _find_column(df, EA_VISIBILITY_COLS)
    contract_col = _find_column(df, EA_CONTRACT_COLS)
    frequency_col = _find_column(df, EA_FREQUENCY_COLS)
    freq_locked_col = _find_column(df, EA_FREQ_LOCKED_COLS)
    warehouse_col = _find_column(df, EA_WAREHOUSE_COLS)
    ui_group_col = _find_column(df, EA_UI_GROUP_COLS)
    prompt_mode_col = _find_column(df, EA_PROMPT_MODE_COLS)
    sort_col = _find_column(df, EA_SORT_COLS)
    active_col = _find_column(df, EA_ACTIVE_COLS)

    if not name_col:
        flash(f"Could not find a name column. Expected one of: {', '.join(EA_NAME_COLS)}", "error")
        return redirect(url_for('.expense_accounts_form'))

    # Build lookups for matching related entities by code or name
    groups_by_code, groups_by_name = _build_lookup_dicts(ApprovalGroup)
    spend_types_by_code, spend_types_by_name = _build_lookup_dicts(SpendType)
    frequencies_by_code, frequencies_by_name = _build_lookup_dicts(FrequencyOption)

    created = 0
    updated = 0
    user_id = h.get_active_user_id()

    for idx, row in df.iterrows():
        name = _get_cell_value(row, name_col)
        if not name:
            continue

        code = _get_cell_value(row, code_col)
        if not code:
            code = _generate_code(name)

        # Expense account codes can be longer (64 chars in model)
        code = code.upper()[:64]

        # Look up related entities by code or name
        group_value = _get_cell_value(row, approval_group_col)
        approval_group_id = _lookup_entity_id(group_value, groups_by_code, groups_by_name)

        spend_type_value = _get_cell_value(row, spend_type_col)
        default_spend_type_id = _lookup_entity_id(spend_type_value, spend_types_by_code, spend_types_by_name)

        frequency_value = _get_cell_value(row, frequency_col)
        default_frequency_id = _lookup_entity_id(frequency_value, frequencies_by_code, frequencies_by_name)

        # Parse mode/enum fields
        spend_mode_value = _get_cell_value(row, spend_mode_col)
        spend_type_mode = _lookup_mode_value(spend_mode_value, SPEND_TYPE_MODES, SPEND_TYPE_MODE_ALLOW_LIST)

        visibility_value = _get_cell_value(row, visibility_col)
        visibility_mode = _lookup_mode_value(visibility_value, VISIBILITY_MODES, VISIBILITY_MODE_ALL)

        prompt_value = _get_cell_value(row, prompt_mode_col)
        prompt_mode = _lookup_mode_value(prompt_value, PROMPT_MODES, PROMPT_MODE_NONE)

        # Parse boolean and numeric fields
        is_fixed_cost = _parse_bool(_get_cell_value(row, fixed_cost_col)) if fixed_cost_col else False
        unit_price_locked = _parse_bool(_get_cell_value(row, price_locked_col)) if price_locked_col else False
        is_contract_eligible = _parse_bool(_get_cell_value(row, contract_col)) if contract_col else False
        frequency_locked = _parse_bool(_get_cell_value(row, freq_locked_col)) if freq_locked_col else False
        warehouse_default = _parse_bool(_get_cell_value(row, warehouse_col)) if warehouse_col else False
        is_active = _parse_bool(_get_cell_value(row, active_col)) if active_col else True

        default_unit_price_cents = _parse_dollars_to_cents(_get_cell_value(row, unit_price_col))
        sort_order = _parse_int(_get_cell_value(row, sort_col)) or 0

        # UI display group
        ui_display_group = _get_cell_value(row, ui_group_col)
        if ui_display_group:
            ui_display_group = ui_display_group.upper().replace(' ', '_')

        # Check for existing expense account
        existing = db.session.query(ExpenseAccount).filter_by(code=code).first()

        if existing:
            # Update existing
            existing.name = name
            existing.description = _get_cell_value(row, desc_col)
            existing.quickbooks_account_name = _get_cell_value(row, qb_col)
            existing.approval_group_id = approval_group_id
            existing.default_spend_type_id = default_spend_type_id
            existing.spend_type_mode = spend_type_mode
            existing.visibility_mode = visibility_mode
            existing.is_fixed_cost = is_fixed_cost
            existing.default_unit_price_cents = default_unit_price_cents
            existing.unit_price_locked = unit_price_locked
            existing.is_contract_eligible = is_contract_eligible
            existing.default_frequency_id = default_frequency_id
            existing.frequency_locked = frequency_locked
            existing.warehouse_default = warehouse_default
            existing.ui_display_group = ui_display_group
            existing.prompt_mode = prompt_mode
            existing.sort_order = sort_order
            existing.is_active = is_active
            existing.updated_by_user_id = user_id

            log_config_change("expense_account", existing.id, CONFIG_AUDIT_UPDATE, {
                "source": "bulk_upload",
            })
            updated += 1
            account = existing
        else:
            # Create new
            account = ExpenseAccount(
                code=code,
                name=name,
                description=_get_cell_value(row, desc_col),
                quickbooks_account_name=_get_cell_value(row, qb_col),
                approval_group_id=approval_group_id,
                default_spend_type_id=default_spend_type_id,
                spend_type_mode=spend_type_mode,
                visibility_mode=visibility_mode,
                is_fixed_cost=is_fixed_cost,
                default_unit_price_cents=default_unit_price_cents,
                unit_price_locked=unit_price_locked,
                is_contract_eligible=is_contract_eligible,
                default_frequency_id=default_frequency_id,
                frequency_locked=frequency_locked,
                warehouse_default=warehouse_default,
                ui_display_group=ui_display_group,
                prompt_mode=prompt_mode,
                sort_order=sort_order,
                is_active=is_active,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
            )
            db.session.add(account)
            db.session.flush()

            log_config_change("expense_account", account.id, CONFIG_AUDIT_CREATE, {
                "source": "bulk_upload",
            })
            created += 1

        # Process allowed_spend_types if column is present
        allowed_types_value = _get_cell_value(row, allowed_spend_types_col)
        if allowed_types_value:
            # Parse comma or pipe separated spend type codes
            type_codes = [c.strip() for c in allowed_types_value.replace('|', ',').split(',') if c.strip()]
            # Clear existing and add new
            account.allowed_spend_types.clear()
            for type_code in type_codes:
                spend_type = _lookup_entity(type_code, spend_types_by_code, spend_types_by_name)
                if spend_type:
                    account.allowed_spend_types.append(spend_type)

    db.session.commit()

    flash(f"Expense Accounts: {created} created, {updated} updated", "success")
    return redirect(url_for('admin_config.expense_accounts.list_expense_accounts'))


# ============================================================
# User Upload
# ============================================================

@data_upload_bp.get("/users")
@require_super_admin
def users_form():
    """Show user upload form."""
    return render_admin_config_page("admin/data_upload/users.html")


@data_upload_bp.post("/users")
@require_super_admin
def users_upload():
    """Process user upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.users_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.users_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.users_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.users_form'))

    # Find columns
    email_col = _find_column(df, USER_EMAIL_COLS)
    name_col = _find_column(df, USER_NAME_COLS)
    id_col = _find_column(df, USER_ID_COLS)

    if not email_col:
        flash(f"Could not find an email column. Expected one of: {', '.join(USER_EMAIL_COLS)}", "error")
        return redirect(url_for('.users_form'))

    if not name_col:
        flash(f"Could not find a name column. Expected one of: {', '.join(USER_NAME_COLS)}", "error")
        return redirect(url_for('.users_form'))

    created = 0
    updated = 0

    for idx, row in df.iterrows():
        email = _get_cell_value(row, email_col)
        name = _get_cell_value(row, name_col)

        if not email or not name:
            continue

        email = email.lower().strip()

        # Get or generate user ID
        user_id = _get_cell_value(row, id_col)
        if not user_id:
            user_id = _generate_user_id(email)

        # Check for existing user by email first, then by ID
        existing = db.session.query(User).filter(User.email.ilike(email)).first()
        if not existing:
            existing = db.session.get(User, user_id)

        if existing:
            # Update existing
            existing.display_name = name
            existing.email = email
            updated += 1
        else:
            # Create new
            user = User(
                id=user_id,
                email=email,
                display_name=name,
                is_active=True,
            )
            db.session.add(user)
            created += 1

    db.session.commit()

    flash(f"Users: {created} created, {updated} updated", "success")
    return redirect(url_for('admin_config.users.list_users'))


# ============================================================
# Department Membership Upload
# ============================================================

@data_upload_bp.get("/department-memberships")
@require_super_admin
def department_memberships_form():
    """Show department membership upload form."""
    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .order_by(*sort_with_override(EventCycle))
        .all()
    )
    return render_admin_config_page(
        "admin/data_upload/department_memberships.html",
        event_cycles=event_cycles,
    )


@data_upload_bp.post("/department-memberships")
@require_super_admin
def department_memberships_upload():
    """Process department membership upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.department_memberships_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.department_memberships_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.department_memberships_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.department_memberships_form'))

    # Check for default event cycle from form
    default_cycle_id = safe_int_or_none(request.form.get('default_event_cycle'))
    default_cycle = None
    if default_cycle_id:
        default_cycle = db.session.get(EventCycle, default_cycle_id)

    # Find columns
    user_col = _find_column(df, DM_USER_COLS)
    dept_col = _find_column(df, DM_DEPT_COLS)
    cycle_col = _find_column(df, DM_CYCLE_COLS)
    head_col = _find_column(df, DM_HEAD_COLS)

    if not user_col:
        flash(f"Could not find a user column. Expected one of: {', '.join(DM_USER_COLS)}", "error")
        return redirect(url_for('.department_memberships_form'))

    if not dept_col:
        flash(f"Could not find a department column. Expected one of: {', '.join(DM_DEPT_COLS)}", "error")
        return redirect(url_for('.department_memberships_form'))

    # Build lookups for matching by code or name
    depts_by_code, depts_by_name = _build_lookup_dicts(Department)
    cycles_by_code = _build_code_lookup(EventCycle)

    # Detect work type columns (e.g. budget_view, budget_edit, contract_view, ...)
    wt_columns = _detect_work_type_columns(df)

    created = 0
    updated = 0
    skipped = 0

    for idx, row in df.iterrows():
        user_identifier = _get_cell_value(row, user_col)
        dept_value = _get_cell_value(row, dept_col)

        if not user_identifier or not dept_value:
            skipped += 1
            continue

        # Find user
        user = _find_user_by_identifier(user_identifier)
        if not user:
            skipped += 1
            continue

        # Find department by code or name
        dept = _lookup_entity(dept_value, depts_by_code, depts_by_name)
        if not dept:
            skipped += 1
            continue

        # Find event cycle (use default if not specified)
        cycle_value = _get_cell_value(row, cycle_col)
        if cycle_value:
            cycle = cycles_by_code.get(cycle_value.upper(), default_cycle)
        else:
            cycle = default_cycle

        if not cycle:
            skipped += 1
            continue

        # Parse permissions
        is_head = _parse_bool(_get_cell_value(row, head_col)) if head_col else False

        # Check for existing membership
        existing = db.session.query(DepartmentMembership).filter_by(
            user_id=user.id,
            department_id=dept.id,
            event_cycle_id=cycle.id,
        ).first()

        if existing:
            existing.is_department_head = is_head
            membership = existing
            updated += 1
        else:
            membership = DepartmentMembership(
                user_id=user.id,
                department_id=dept.id,
                event_cycle_id=cycle.id,
                is_department_head=is_head,
            )
            db.session.add(membership)
            db.session.flush()  # Ensure membership.id is available for FK
            created += 1

        # Apply work type access if columns were present
        if wt_columns:
            _apply_work_type_access(
                membership, DepartmentMembershipWorkTypeAccess,
                "department_membership_id", wt_columns, row,
            )

    db.session.commit()

    msg = f"Department Memberships: {created} created, {updated} updated"
    if skipped:
        msg += f", {skipped} skipped (missing user/department/cycle)"
    flash(msg, "success")
    return redirect(url_for('.department_memberships_form'))


# ============================================================
# Division Membership Upload
# ============================================================

@data_upload_bp.get("/division-memberships")
@require_super_admin
def division_memberships_form():
    """Show division membership upload form."""
    event_cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active == True)
        .order_by(*sort_with_override(EventCycle))
        .all()
    )
    return render_admin_config_page(
        "admin/data_upload/division_memberships.html",
        event_cycles=event_cycles,
    )


@data_upload_bp.post("/division-memberships")
@require_super_admin
def division_memberships_upload():
    """Process division membership upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.division_memberships_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.division_memberships_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.division_memberships_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.division_memberships_form'))

    # Check for default event cycle from form
    default_cycle_id = safe_int_or_none(request.form.get('default_event_cycle'))
    default_cycle = None
    if default_cycle_id:
        default_cycle = db.session.get(EventCycle, default_cycle_id)

    # Find columns
    user_col = _find_column(df, DVM_USER_COLS)
    div_col = _find_column(df, DVM_DIV_COLS)
    cycle_col = _find_column(df, DVM_CYCLE_COLS)
    head_col = _find_column(df, DVM_HEAD_COLS)

    if not user_col:
        flash(f"Could not find a user column. Expected one of: {', '.join(DVM_USER_COLS)}", "error")
        return redirect(url_for('.division_memberships_form'))

    if not div_col:
        flash(f"Could not find a division column. Expected one of: {', '.join(DVM_DIV_COLS)}", "error")
        return redirect(url_for('.division_memberships_form'))

    # Build lookups for matching by code or name
    divs_by_code, divs_by_name = _build_lookup_dicts(Division)
    cycles_by_code = _build_code_lookup(EventCycle)

    # Detect work type columns (e.g. budget_view, budget_edit, contract_view, ...)
    wt_columns = _detect_work_type_columns(df)

    created = 0
    updated = 0
    skipped = 0

    for idx, row in df.iterrows():
        user_identifier = _get_cell_value(row, user_col)
        div_value = _get_cell_value(row, div_col)

        if not user_identifier or not div_value:
            skipped += 1
            continue

        # Find user
        user = _find_user_by_identifier(user_identifier)
        if not user:
            skipped += 1
            continue

        # Find division by code or name
        div = _lookup_entity(div_value, divs_by_code, divs_by_name)
        if not div:
            skipped += 1
            continue

        # Find event cycle (use default if not specified)
        cycle_value = _get_cell_value(row, cycle_col)
        if cycle_value:
            cycle = cycles_by_code.get(cycle_value.upper(), default_cycle)
        else:
            cycle = default_cycle

        if not cycle:
            skipped += 1
            continue

        # Parse permissions
        is_head = _parse_bool(_get_cell_value(row, head_col)) if head_col else False

        # Check for existing membership
        existing = db.session.query(DivisionMembership).filter_by(
            user_id=user.id,
            division_id=div.id,
            event_cycle_id=cycle.id,
        ).first()

        if existing:
            existing.is_division_head = is_head
            membership = existing
            updated += 1
        else:
            membership = DivisionMembership(
                user_id=user.id,
                division_id=div.id,
                event_cycle_id=cycle.id,
                is_division_head=is_head,
            )
            db.session.add(membership)
            db.session.flush()  # Ensure membership.id is available for FK
            created += 1

        # Apply work type access if columns were present
        if wt_columns:
            _apply_work_type_access(
                membership, DivisionMembershipWorkTypeAccess,
                "division_membership_id", wt_columns, row,
            )

    db.session.commit()

    msg = f"Division Memberships: {created} created, {updated} updated"
    if skipped:
        msg += f", {skipped} skipped (missing user/division/cycle)"
    flash(msg, "success")
    return redirect(url_for('.division_memberships_form'))


# ============================================================
# User Role Upload
# ============================================================

@data_upload_bp.get("/user-roles")
@require_super_admin
def user_roles_form():
    """Show user role upload form."""
    approval_groups = (
        db.session.query(ApprovalGroup)
        .filter(ApprovalGroup.is_active == True)
        .order_by(*sort_with_override(ApprovalGroup))
        .all()
    )
    return render_admin_config_page(
        "admin/data_upload/user_roles.html",
        approval_groups=approval_groups,
    )


@data_upload_bp.post("/user-roles")
@require_super_admin
def user_roles_upload():
    """Process user role upload."""
    if 'file' not in request.files:
        flash("No file uploaded", "error")
        return redirect(url_for('.user_roles_form'))

    file = request.files['file']
    if not file.filename:
        flash("No file selected", "error")
        return redirect(url_for('.user_roles_form'))

    df = _read_uploaded_file(file)
    if df is None:
        return redirect(url_for('.user_roles_form'))

    if df.empty:
        flash("File is empty", "error")
        return redirect(url_for('.user_roles_form'))

    # Find columns
    user_col = _find_column(df, UR_USER_COLS)
    role_col = _find_column(df, UR_ROLE_COLS)
    group_col = _find_column(df, UR_APPROVAL_GROUP_COLS)

    if not user_col:
        flash(f"Could not find a user column. Expected one of: {', '.join(UR_USER_COLS)}", "error")
        return redirect(url_for('.user_roles_form'))

    if not role_col:
        flash(f"Could not find a role column. Expected one of: {', '.join(UR_ROLE_COLS)}", "error")
        return redirect(url_for('.user_roles_form'))

    # Build lookups for matching by code or name
    groups_by_code, groups_by_name = _build_lookup_dicts(ApprovalGroup)

    created = 0
    skipped = 0

    for idx, row in df.iterrows():
        user_identifier = _get_cell_value(row, user_col)
        role_value = _get_cell_value(row, role_col)

        if not user_identifier or not role_value:
            skipped += 1
            continue

        # Find user
        user = _find_user_by_identifier(user_identifier)
        if not user:
            skipped += 1
            continue

        # Validate role code
        role_upper = role_value.upper().replace(' ', '_')
        if role_upper not in VALID_ROLE_CODES:
            skipped += 1
            continue
        role_code = VALID_ROLE_CODES[role_upper]

        # Find approval group (required for APPROVER role)
        group_value = _get_cell_value(row, group_col)
        approval_group_id = _lookup_entity_id(group_value, groups_by_code, groups_by_name)

        # APPROVER role requires an approval group
        if role_code == ROLE_APPROVER and not approval_group_id:
            skipped += 1
            continue

        # Check for existing role
        existing_query = db.session.query(UserRole).filter_by(
            user_id=user.id,
            role_code=role_code,
        )
        if approval_group_id:
            existing_query = existing_query.filter_by(approval_group_id=approval_group_id)
        else:
            existing_query = existing_query.filter(UserRole.approval_group_id.is_(None))

        existing = existing_query.first()

        if not existing:
            role = UserRole(
                user_id=user.id,
                role_code=role_code,
                approval_group_id=approval_group_id,
            )
            db.session.add(role)
            created += 1

    db.session.commit()

    msg = f"User Roles: {created} created"
    if skipped:
        msg += f", {skipped} skipped (missing user/invalid role/missing approval group for approver)"
    flash(msg, "success")
    return redirect(url_for('.user_roles_form'))


# ============================================================
# Template Downloads
# ============================================================

def _make_csv_response(csv_content: str, filename: str) -> Response:
    """Create a CSV download response."""
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@data_upload_bp.get("/templates/divisions.csv")
@require_super_admin
def download_divisions_template():
    """Download a CSV template for divisions."""
    csv_content = """code,name,description,qb_class
OPERATIONS,Operations,"Departments that handle event operations",Operations
ENTERTAINMENT,Entertainment,"Departments focused on attendee entertainment",Entertainment
SUPPORT,Support Services,"Back-office and support departments",Support
"""
    return _make_csv_response(csv_content, 'divisions_template.csv')


@data_upload_bp.get("/templates/departments.csv")
@require_super_admin
def download_departments_template():
    """Download a CSV template for departments."""
    csv_content = """code,name,division,description,mailing_list,slack_channel,qb_class
TECHOPS,TechOps,OPERATIONS,"Technical operations and infrastructure",techops@example.org,#techops,TechOps
REGISTRATION,Registration,OPERATIONS,"Attendee registration and badges",reg@example.org,#registration,Registration
PANELS,Panels,ENTERTAINMENT,"Panel programming and scheduling",panels@example.org,#panels,Panels
GUESTS,Guests,ENTERTAINMENT,"Guest relations and management",guests@example.org,#guests,Guests
FINANCE,Finance,SUPPORT,"Financial operations and budgeting",finance@example.org,#finance,Finance
"""
    return _make_csv_response(csv_content, 'departments_template.csv')


@data_upload_bp.get("/templates/expense-accounts.csv")
@require_super_admin
def download_expense_accounts_template():
    """Download a CSV template for expense accounts."""
    # ui_display_group options: KNOWN_COSTS (Fixed Costs tab), HOTEL_SERVICES (Hotel/Gaylord tab), or blank (standard)
    # spend_type_mode: SINGLE_LOCKED (one fixed type) or ALLOW_LIST (multiple allowed)
    # prompt_mode: NONE, SUGGEST, or REQUIRE
    # allowed_spend_types: comma-separated list of spend type codes (used when spend_type_mode is ALLOW_LIST)
    csv_content = """code,name,description,quickbooks_account_name,approval_group,spend_type,allowed_spend_types,spend_type_mode,is_fixed_cost,unit_price,price_locked,frequency,frequency_locked,warehouse_default,prompt_mode,visibility_mode,is_contract_eligible,ui_display_group,sort_order,is_active
RADIO_RENTAL,Radios (Rental),"Handheld radios rental for operations",Equipment Rental,TECH,DIVVY,,SINGLE_LOCKED,yes,50.00,yes,ONE_TIME,yes,no,NONE,ALL,no,KNOWN_COSTS,10,yes
LAPTOP_RENTAL,iPads / Laptops (Rental),"Hartford rental computing devices",Equipment Rental,TECH,DIVVY,,SINGLE_LOCKED,yes,150.00,yes,ONE_TIME,yes,no,NONE,ALL,no,KNOWN_COSTS,20,yes
HTL_ROOM_REG,Hotel Room - Regular,"Standard hotel room",Hotel Rooms,HOTEL,BANK,,SINGLE_LOCKED,yes,244.00,yes,ONE_TIME,yes,no,NONE,ALL,no,HOTEL_SERVICES,10,yes
PARKING_GNH,Parking - Gaylord,"Gaylord hotel parking per day",Hotel Parking,HOTEL,DIVVY,,SINGLE_LOCKED,yes,19.00,yes,ONE_TIME,yes,no,NONE,ALL,no,HOTEL_SERVICES,20,yes
OFFICE_SUPPLIES,Office Supplies,"General office supplies",Office Supplies,OTHER,BANK,"BANK,DIVVY",ALLOW_LIST,no,,,no,yes,SUGGEST,ALL,no,,0,yes
CONTRACTOR,Contractor Services,"External contractor payments",Professional Services,OTHER,,"BANK,CHECK,DIVVY",ALLOW_LIST,no,,,no,no,REQUIRE,ALL,yes,,0,yes
"""
    return _make_csv_response(csv_content, 'expense_accounts_template.csv')


@data_upload_bp.get("/templates/users.csv")
@require_super_admin
def download_users_template():
    """Download a CSV template for users."""
    csv_content = """email,name,id
alice@example.org,Alice Smith,user:alice
bob@example.org,Bob Johnson,user:bob
carol@example.org,Carol Williams,
david@example.org,David Brown,
"""
    return _make_csv_response(csv_content, 'users_template.csv')


@data_upload_bp.get("/templates/department-memberships.csv")
@require_super_admin
def download_department_memberships_template():
    """Download a CSV template for department memberships."""
    csv_content = """email,department,event_cycle,is_department_head,budget_view,budget_edit,contract_view,contract_edit
alice@example.org,TECHOPS,SMF2026,yes,yes,yes,yes,yes
bob@example.org,TECHOPS,SMF2026,no,yes,no,yes,no
carol@example.org,TECHOPS,SMF2026,no,yes,yes,no,no
david@example.org,REGISTRATION,SMF2026,yes,yes,yes,yes,yes
alice@example.org,REGISTRATION,SMF2026,no,yes,no,no,no
"""
    return _make_csv_response(csv_content, 'department_memberships_template.csv')


@data_upload_bp.get("/templates/division-memberships.csv")
@require_super_admin
def download_division_memberships_template():
    """Download a CSV template for division memberships."""
    csv_content = """email,division,event_cycle,is_division_head,budget_view,budget_edit,contract_view,contract_edit
alice@example.org,OPERATIONS,SMF2026,yes,yes,yes,yes,yes
bob@example.org,ENTERTAINMENT,SMF2026,yes,yes,yes,no,no
"""
    return _make_csv_response(csv_content, 'division_memberships_template.csv')


@data_upload_bp.get("/templates/user-roles.csv")
@require_super_admin
def download_user_roles_template():
    """Download a CSV template for user roles."""
    csv_content = """email,role,approval_group
alice@example.org,SUPER_ADMIN,
bob@example.org,APPROVER,TECH
carol@example.org,APPROVER,HOTEL
david@example.org,APPROVER,OTHER
"""
    return _make_csv_response(csv_content, 'user_roles_template.csv')
