"""
Line routes - add budget lines to work items.
"""
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, request, abort, flash
from sqlalchemy.orm import selectinload, joinedload

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
from . import work_bp
from .helpers import (
    get_portfolio_context,
    require_work_item_edit,
    build_work_item_perms,
    get_visible_expense_accounts,
    get_categorized_expense_accounts,
    get_effective_account_type,
    get_allowed_spend_types,
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
    get_next_line_number,
    format_currency,
    get_effective_description,
)


def get_work_item_by_public_id(event: str, dept: str, public_id: str):
    """
    Get a work item by public_id and verify it belongs to the correct portfolio.

    Returns tuple of (work_item, ctx) or aborts with 404.
    Eager loads lines with budget details.
    """
    ctx = get_portfolio_context(event, dept)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    return work_item, ctx


def build_spend_types_by_account(expense_accounts: list) -> dict:
    """
    Build a dictionary mapping expense account IDs to their spend type info.

    This data structure is used by the JavaScript in the line form
    to dynamically update the spend type dropdown when the expense
    account selection changes.

    Returns: {
        account_id: {
            "mode": spend_type_mode,
            "default_id": default_spend_type_id,
            "types": [{"id": id, "name": name}, ...]
        }
    }
    """
    result = {}
    for account in expense_accounts:
        spend_types = get_allowed_spend_types(account)

        # Build list of spend type options
        type_options = []
        for spend_type in spend_types:
            type_options.append({
                "id": spend_type.id,
                "name": spend_type.name
            })

        result[account.id] = {
            "mode": account.spend_type_mode,
            "default_id": account.default_spend_type_id,
            "types": type_options
        }

    return result


# ============================================================
# Line Creation Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/item/<public_id>/lines/new")
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

    # Build spend types data for JavaScript dropdown
    spend_types_by_account = build_spend_types_by_account(expense_accounts)

    # Build effective descriptions dictionary (considering event overrides)
    effective_descriptions = {
        acc.id: get_effective_description(acc, ctx.event_cycle.id)
        for acc in expense_accounts
    }

    return render_template(
        "budget/line_form.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        expense_accounts=expense_accounts,
        spend_types_by_account=spend_types_by_account,
        effective_descriptions=effective_descriptions,
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
        line=None,  # New line, no existing data
        is_edit=False,
    )


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/lines")
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
            else:
                is_fixed, _ = get_effective_account_type(expense_account, ctx.event_cycle.id)
                if is_fixed:
                    errors.append("Fixed-cost expense accounts cannot be used in this form.")
        except ValueError:
            errors.append("Invalid expense account ID.")

    # Validate expense account visibility (override-aware)
    if expense_account:
        categorized = get_categorized_expense_accounts(
            department_id=ctx.department.id,
            event_cycle_id=ctx.event_cycle.id,
        )
        standard_ids = {acc.id for acc in categorized["standard"]}
        if expense_account.id not in standard_ids:
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

    # Validate required references
    confidence_level = None
    if not confidence_level_id_str:
        errors.append("Confidence level is required.")
    else:
        try:
            confidence_level_id = int(confidence_level_id_str)
            confidence_level = ConfidenceLevel.query.get(confidence_level_id)
            if not confidence_level or not confidence_level.is_active:
                errors.append("Invalid confidence level.")
        except ValueError:
            errors.append("Invalid confidence level ID.")

    frequency = None
    if not frequency_id_str:
        errors.append("Frequency is required.")
    else:
        try:
            frequency_id = int(frequency_id_str)
            frequency = FrequencyOption.query.get(frequency_id)
            if not frequency or not frequency.is_active:
                errors.append("Invalid frequency option.")
        except ValueError:
            errors.append("Invalid frequency option ID.")

    priority = None
    if not priority_id_str:
        errors.append("Priority is required.")
    else:
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
        spend_types_by_account = build_spend_types_by_account(expense_accounts)
        effective_descriptions = {
            acc.id: get_effective_description(acc, ctx.event_cycle.id)
            for acc in expense_accounts
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
            effective_descriptions=effective_descriptions,
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
        confidence_level_id=confidence_level.id,
        frequency_id=frequency.id,
        priority_id=priority.id,
        warehouse_flag=warehouse_flag,
        description=description,
    )
    db.session.add(budget_detail)
    db.session.commit()

    flash("Budget line added successfully.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
    ))


# ============================================================
# Line Edit Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/item/<public_id>/lines/<int:line_num>/edit")
def line_edit(event: str, dept: str, public_id: str, line_num: int):
    """
    Show form for editing an existing budget line.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Get the line
    line = WorkLine.query.filter_by(
        work_item_id=work_item.id,
        line_number=line_num,
    ).first()

    if not line:
        abort(404, f"Line not found: {line_num}")

    detail = line.budget_detail
    if not detail:
        flash("This line has no budget details to edit.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Get expense accounts for dropdown
    expense_accounts = get_visible_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
        exclude_fixed=True,
    )

    # Build spend types data for JavaScript dropdown
    spend_types_by_account = build_spend_types_by_account(expense_accounts)

    # Build effective descriptions dictionary (considering event overrides)
    effective_descriptions = {
        acc.id: get_effective_description(acc, ctx.event_cycle.id)
        for acc in expense_accounts
    }

    # Build form_data from existing line
    form_data = {
        "expense_account_id": str(detail.expense_account_id) if detail.expense_account_id else "",
        "spend_type_id": str(detail.spend_type_id) if detail.spend_type_id else "",
        "quantity": str(detail.quantity),
        "unit_price": str(detail.unit_price_cents / 100) if detail.unit_price_cents else "",
        "confidence_level_id": str(detail.confidence_level_id) if detail.confidence_level_id else "",
        "frequency_id": str(detail.frequency_id) if detail.frequency_id else "",
        "priority_id": str(detail.priority_id) if detail.priority_id else "",
        "warehouse_flag": detail.warehouse_flag,
        "description": detail.description or "",
    }

    return render_template(
        "budget/line_form.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        expense_accounts=expense_accounts,
        spend_types_by_account=spend_types_by_account,
        effective_descriptions=effective_descriptions,
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
        line=line,
        is_edit=True,
        form_data=form_data,
    )


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/lines/<int:line_num>/edit")
def line_update(event: str, dept: str, public_id: str, line_num: int):
    """
    Update an existing budget line.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    user_ctx = get_user_ctx()

    # Get the line
    line = WorkLine.query.filter_by(
        work_item_id=work_item.id,
        line_number=line_num,
    ).first()

    if not line:
        abort(404, f"Line not found: {line_num}")

    detail = line.budget_detail
    if not detail:
        flash("This line has no budget details to edit.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

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
            else:
                is_fixed, _ = get_effective_account_type(expense_account, ctx.event_cycle.id)
                if is_fixed:
                    errors.append("Fixed-cost expense accounts cannot be used in this form.")
        except ValueError:
            errors.append("Invalid expense account ID.")

    # Validate expense account visibility (override-aware)
    if expense_account:
        categorized = get_categorized_expense_accounts(
            department_id=ctx.department.id,
            event_cycle_id=ctx.event_cycle.id,
        )
        standard_ids = {acc.id for acc in categorized["standard"]}
        if expense_account.id not in standard_ids:
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

    # Validate required references
    confidence_level = None
    if not confidence_level_id_str:
        errors.append("Confidence level is required.")
    else:
        try:
            confidence_level_id = int(confidence_level_id_str)
            confidence_level = ConfidenceLevel.query.get(confidence_level_id)
            if not confidence_level or not confidence_level.is_active:
                errors.append("Invalid confidence level.")
        except ValueError:
            errors.append("Invalid confidence level ID.")

    frequency = None
    if not frequency_id_str:
        errors.append("Frequency is required.")
    else:
        try:
            frequency_id = int(frequency_id_str)
            frequency = FrequencyOption.query.get(frequency_id)
            if not frequency or not frequency.is_active:
                errors.append("Invalid frequency option.")
        except ValueError:
            errors.append("Invalid frequency option ID.")

    priority = None
    if not priority_id_str:
        errors.append("Priority is required.")
    else:
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
        spend_types_by_account = build_spend_types_by_account(expense_accounts)
        effective_descriptions = {
            acc.id: get_effective_description(acc, ctx.event_cycle.id)
            for acc in expense_accounts
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
            effective_descriptions=effective_descriptions,
            confidence_levels=get_confidence_levels(),
            frequency_options=get_frequency_options(),
            priority_levels=get_priority_levels(),
            line=line,
            is_edit=True,
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

    # Update the budget line detail
    detail.expense_account_id = expense_account.id
    detail.spend_type_id = spend_type.id
    detail.unit_price_cents = unit_price_cents
    detail.quantity = quantity
    detail.confidence_level_id = confidence_level.id
    detail.frequency_id = frequency.id
    detail.priority_id = priority.id
    detail.warehouse_flag = warehouse_flag
    detail.description = description

    # Update the line's updated_by
    line.updated_by_user_id = user_ctx.user_id

    db.session.commit()

    flash("Budget line updated successfully.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
    ))


# ============================================================
# Line Delete Route
# ============================================================

@work_bp.post("/<event>/<dept>/budget/item/<public_id>/lines/<int:line_num>/delete")
def line_delete(event: str, dept: str, public_id: str, line_num: int):
    """
    Delete a budget line.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Get the line
    line = WorkLine.query.filter_by(
        work_item_id=work_item.id,
        line_number=line_num,
    ).first()

    if not line:
        flash(f"Line {line_num} not found.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Delete budget detail first
    detail = BudgetLineDetail.query.filter_by(work_line_id=line.id).first()
    if detail:
        db.session.delete(detail)
        db.session.flush()

    # Delete the line
    db.session.delete(line)
    db.session.commit()

    flash(f"Line {line_num} deleted.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
    ))
