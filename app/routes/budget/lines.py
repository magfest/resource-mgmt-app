"""
Line routes - add budget lines to work items.
"""
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    BudgetLineDetail,
    ExpenseAccount,
    SpendType,
    ConfidenceLevel,
    FrequencyOption,
    PriorityLevel,
    WORK_LINE_STATUS_PENDING,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
)
from app.routes import get_user_ctx
from . import budget_bp
from .helpers import (
    get_portfolio_context,
    require_work_item_edit,
    build_work_item_perms,
    get_visible_expense_accounts,
    get_allowed_spend_types,
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
    get_next_line_number,
    format_currency,
)


def get_work_item_by_public_id(event: str, dept: str, public_id: str):
    """
    Get a work item by public_id and verify it belongs to the correct portfolio.

    Returns tuple of (work_item, ctx) or aborts with 404.
    """
    ctx = get_portfolio_context(event, dept)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    return work_item, ctx


# ============================================================
# Line Creation Routes
# ============================================================

@budget_bp.get("/<event>/<dept>/budget/item/<public_id>/lines/new")
def line_new(event: str, dept: str, public_id: str):
    """
    Show form for adding a new budget line.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Get expense accounts for dropdown
    expense_accounts = get_visible_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
        exclude_fixed=True,
    )

    # Build spend types data for JavaScript
    spend_types_by_account = {}
    for acc in expense_accounts:
        spend_types = get_allowed_spend_types(acc)
        spend_types_by_account[acc.id] = {
            "mode": acc.spend_type_mode,
            "default_id": acc.default_spend_type_id,
            "types": [{"id": st.id, "name": st.name} for st in spend_types]
        }

    return render_template(
        "budget/line_form.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        expense_accounts=expense_accounts,
        spend_types_by_account=spend_types_by_account,
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
        line=None,  # New line, no existing data
        is_edit=False,
    )


@budget_bp.post("/<event>/<dept>/budget/item/<public_id>/lines")
def line_create(event: str, dept: str, public_id: str):
    """
    Create a new budget line.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    user_ctx = get_user_ctx()

    # Parse form data
    expense_account_id_str = request.form.get("expense_account_id", "").strip()
    spend_type_id_str = request.form.get("spend_type_id", "").strip()
    quantity_str = request.form.get("quantity", "1").strip()
    unit_price_str = request.form.get("unit_price", "0").strip()
    confidence_level_id_str = request.form.get("confidence_level_id", "").strip()
    frequency_id_str = request.form.get("frequency_id", "").strip()
    priority_id_str = request.form.get("priority_id", "").strip()
    warehouse_flag = request.form.get("warehouse_flag") == "on"
    description = request.form.get("description", "").strip()

    errors = []

    # Validate expense account
    expense_account = None
    if not expense_account_id_str:
        errors.append("Expense account is required.")
    else:
        try:
            expense_account_id = int(expense_account_id_str)
            expense_account = ExpenseAccount.query.get(expense_account_id)
            if not expense_account:
                errors.append("Invalid expense account.")
            elif not expense_account.is_active:
                errors.append("Selected expense account is not active.")
            elif expense_account.is_fixed_cost:
                errors.append("Fixed-cost expense accounts cannot be used in this form.")
        except ValueError:
            errors.append("Invalid expense account ID.")

    # Validate expense account visibility
    if expense_account:
        visible_accounts = get_visible_expense_accounts(
            department_id=ctx.department.id,
            event_cycle_id=ctx.event_cycle.id,
            exclude_fixed=True,
        )
        visible_ids = {acc.id for acc in visible_accounts}
        if expense_account.id not in visible_ids:
            errors.append("Selected expense account is not available for this department.")

    # Validate spend type
    spend_type = None
    if expense_account:
        allowed_spend_types = get_allowed_spend_types(expense_account)
        allowed_ids = {st.id for st in allowed_spend_types}

        if expense_account.spend_type_mode == SPEND_TYPE_MODE_SINGLE_LOCKED:
            # Auto-select the default spend type
            if expense_account.default_spend_type_id:
                spend_type = expense_account.default_spend_type
            else:
                errors.append("Expense account has no default spend type configured.")
        else:
            # ALLOW_LIST mode - require selection
            if not spend_type_id_str:
                errors.append("Spend type is required.")
            else:
                try:
                    spend_type_id = int(spend_type_id_str)
                    if spend_type_id not in allowed_ids:
                        errors.append("Selected spend type is not allowed for this expense account.")
                    else:
                        spend_type = SpendType.query.get(spend_type_id)
                except ValueError:
                    errors.append("Invalid spend type ID.")

    # Validate quantity
    quantity = Decimal("1")
    if quantity_str:
        try:
            quantity = Decimal(quantity_str)
            if quantity <= 0:
                errors.append("Quantity must be greater than 0.")
        except InvalidOperation:
            errors.append("Invalid quantity value.")

    # Validate unit price (convert dollars to cents)
    unit_price_cents = 0
    if unit_price_str:
        try:
            unit_price_dollars = Decimal(unit_price_str)
            if unit_price_dollars < 0:
                errors.append("Unit price cannot be negative.")
            else:
                unit_price_cents = int(unit_price_dollars * 100)
        except InvalidOperation:
            errors.append("Invalid unit price value.")

    # Validate optional references
    confidence_level = None
    if confidence_level_id_str:
        try:
            confidence_level_id = int(confidence_level_id_str)
            confidence_level = ConfidenceLevel.query.get(confidence_level_id)
            if not confidence_level or not confidence_level.is_active:
                errors.append("Invalid confidence level.")
        except ValueError:
            errors.append("Invalid confidence level ID.")

    frequency = None
    if frequency_id_str:
        try:
            frequency_id = int(frequency_id_str)
            frequency = FrequencyOption.query.get(frequency_id)
            if not frequency or not frequency.is_active:
                errors.append("Invalid frequency option.")
        except ValueError:
            errors.append("Invalid frequency option ID.")

    priority = None
    if priority_id_str:
        try:
            priority_id = int(priority_id_str)
            priority = PriorityLevel.query.get(priority_id)
            if not priority or not priority.is_active:
                errors.append("Invalid priority level.")
        except ValueError:
            errors.append("Invalid priority level ID.")

    # If errors, re-render form with errors
    if errors:
        expense_accounts = get_visible_expense_accounts(
            department_id=ctx.department.id,
            event_cycle_id=ctx.event_cycle.id,
            exclude_fixed=True,
        )
        spend_types_by_account = {}
        for acc in expense_accounts:
            spend_types = get_allowed_spend_types(acc)
            spend_types_by_account[acc.id] = {
                "mode": acc.spend_type_mode,
                "default_id": acc.default_spend_type_id,
                "types": [{"id": st.id, "name": st.name} for st in spend_types]
            }

        for error in errors:
            flash(error, "error")

        return render_template(
            "budget/line_form.html",
            ctx=ctx,
            perms=perms,
            work_item=work_item,
            expense_accounts=expense_accounts,
            spend_types_by_account=spend_types_by_account,
            confidence_levels=get_confidence_levels(),
            frequency_options=get_frequency_options(),
            priority_levels=get_priority_levels(),
            line=None,
            is_edit=False,
            # Preserve form values
            form_data={
                "expense_account_id": expense_account_id_str,
                "spend_type_id": spend_type_id_str,
                "quantity": quantity_str,
                "unit_price": unit_price_str,
                "confidence_level_id": confidence_level_id_str,
                "frequency_id": frequency_id_str,
                "priority_id": priority_id_str,
                "warehouse_flag": warehouse_flag,
                "description": description,
            },
        )

    # Create the work line
    line_number = get_next_line_number(work_item)
    work_line = WorkLine(
        work_item_id=work_item.id,
        line_number=line_number,
        status=WORK_LINE_STATUS_PENDING,
        updated_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_line)
    db.session.flush()  # Get the work_line.id

    # Create the budget line detail
    budget_detail = BudgetLineDetail(
        work_line_id=work_line.id,
        expense_account_id=expense_account.id,
        spend_type_id=spend_type.id,
        unit_price_cents=unit_price_cents,
        quantity=quantity,
        confidence_level_id=confidence_level.id if confidence_level else None,
        frequency_id=frequency.id if frequency else None,
        priority_id=priority.id if priority else None,
        warehouse_flag=warehouse_flag,
        description=description,
    )
    db.session.add(budget_detail)
    db.session.commit()

    flash("Budget line added successfully.", "success")
    return redirect(url_for(
        "budget.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
    ))
