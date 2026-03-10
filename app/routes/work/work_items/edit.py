"""
Work item edit routes - edit form, save, fixed costs, hotel wizard.
"""
from decimal import Decimal, InvalidOperation

from flask import render_template, redirect, url_for, request, flash

from app import db
from app.models import (
    WorkLine,
    BudgetLineDetail,
    ExpenseAccount,
    UI_GROUP_HOTEL_SERVICES,
    WORK_LINE_STATUS_PENDING,
    COMMENT_VISIBILITY_ADMIN,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    require_work_item_edit,
    compute_work_item_totals,
    format_currency,
    friendly_status,
    get_visible_expense_accounts,
    get_fixed_cost_expense_accounts,
    get_hotel_service_expense_accounts,
    get_non_hotel_fixed_cost_accounts,
    get_effective_fixed_cost_settings,
    get_effective_description,
    get_allowed_spend_types,
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
    get_next_line_number,
)
from .common import get_work_item_by_public_id, calculate_event_nights


# ============================================================
# Work Item Edit Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/item/<public_id>/edit")
def work_item_edit(event: str, dept: str, public_id: str):
    """
    Edit form for a DRAFT work item.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Compute totals
    totals = compute_work_item_totals(work_item)

    # Get lines - separate into regular, fixed-cost, and hotel lines
    all_lines = work_item.lines
    regular_lines = []
    fixed_cost_lines = []
    hotel_lines = []

    for line in all_lines:
        # Lines without budget details go to regular section
        if not line.budget_detail or not line.budget_detail.expense_account:
            regular_lines.append(line)
            continue

        acc = line.budget_detail.expense_account
        # Lines with hotel service accounts go to hotel section
        if acc.is_fixed_cost and acc.ui_display_group == UI_GROUP_HOTEL_SERVICES:
            hotel_lines.append(line)
        # Lines with other fixed-cost accounts go to fixed section
        elif acc.is_fixed_cost:
            fixed_cost_lines.append(line)
        else:
            regular_lines.append(line)

    # Get expense accounts for dropdown (non-fixed)
    expense_accounts = get_visible_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
        exclude_fixed=True,
    )

    # Build spend types map for each expense account
    spend_types_by_account = {
        acc.id: get_allowed_spend_types(acc) for acc in expense_accounts
    }

    # Get non-hotel fixed-cost expense accounts (for Fixed Costs tab)
    non_hotel_fixed_accounts = get_non_hotel_fixed_cost_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
    )

    # Get hotel service expense accounts (for Hotel/Gaylord tab)
    hotel_accounts = get_hotel_service_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
    )

    # Build map of existing lines by account ID (for both fixed-cost and hotel)
    existing_fixed_by_account_id = {
        line.budget_detail.expense_account_id: line
        for line in fixed_cost_lines
        if line.budget_detail
    }
    existing_hotel_by_account_id = {
        line.budget_detail.expense_account_id: line
        for line in hotel_lines
        if line.budget_detail
    }

    # Build fixed-cost data (non-hotel) with effective settings and existing quantities
    fixed_cost_data = []
    for acc in non_hotel_fixed_accounts:
        settings = get_effective_fixed_cost_settings(acc, ctx.event_cycle.id)
        existing_line = existing_fixed_by_account_id.get(acc.id)

        # Get existing quantity and notes if line exists with budget detail
        if existing_line and existing_line.budget_detail:
            existing_quantity = existing_line.budget_detail.quantity
            existing_notes = existing_line.budget_detail.description or ""
        else:
            existing_quantity = None
            existing_notes = ""

        fixed_cost_data.append({
            "account": acc,
            "effective_description": get_effective_description(acc, ctx.event_cycle.id),
            "unit_price_cents": settings["unit_price_cents"],
            "frequency_id": settings["frequency_id"],
            "warehouse_default": settings["warehouse_default"],
            "existing_line": existing_line,
            "existing_quantity": existing_quantity,
            "existing_notes": existing_notes,
        })

    # Build hotel data with effective settings and existing quantities
    hotel_data = []
    for acc in hotel_accounts:
        settings = get_effective_fixed_cost_settings(acc, ctx.event_cycle.id)
        existing_line = existing_hotel_by_account_id.get(acc.id)

        # Get existing quantity and notes if line exists with budget detail
        if existing_line and existing_line.budget_detail:
            existing_quantity = existing_line.budget_detail.quantity
            existing_notes = existing_line.budget_detail.description or ""
        else:
            existing_quantity = None
            existing_notes = ""

        hotel_data.append({
            "account": acc,
            "effective_description": get_effective_description(acc, ctx.event_cycle.id),
            "unit_price_cents": settings["unit_price_cents"],
            "frequency_id": settings["frequency_id"],
            "warehouse_default": settings["warehouse_default"],
            "existing_line": existing_line,
            "existing_quantity": existing_quantity,
            "existing_notes": existing_notes,
        })

    # Calculate event nights for hotel calculator
    event_nights = calculate_event_nights(
        ctx.event_cycle.event_start_date,
        ctx.event_cycle.event_end_date
    )

    # Count items in each section for badge display
    fixed_cost_count = sum(1 for item in fixed_cost_data if item["existing_quantity"])
    hotel_count = sum(1 for item in hotel_data if item["existing_quantity"])

    # Get comments (filter admin-only for non-admins)
    user_ctx = get_user_ctx()
    comments = work_item.comments
    if not user_ctx.is_super_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    return render_template(
        "budget/work_item_edit.html",
        ctx=ctx,
        perms=perms,
        user_ctx=user_ctx,
        work_item=work_item,
        lines=regular_lines,
        fixed_cost_lines=fixed_cost_lines,
        hotel_lines=hotel_lines,
        fixed_cost_data=fixed_cost_data,
        hotel_data=hotel_data,
        fixed_cost_count=fixed_cost_count,
        hotel_count=hotel_count,
        event_nights=event_nights,
        event_start_date=ctx.event_cycle.event_start_date,
        event_end_date=ctx.event_cycle.event_end_date,
        totals=totals,
        comments=comments,
        expense_accounts=expense_accounts,
        spend_types_by_account=spend_types_by_account,
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/edit")
def work_item_edit_save(event: str, dept: str, public_id: str):
    """
    Save edits to a DRAFT work item (delete lines checked for deletion).
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Process line deletions
    lines_to_delete = request.form.getlist("delete_line")
    for line_id_str in lines_to_delete:
        try:
            line_id = int(line_id_str)
        except ValueError:
            continue

        line = WorkLine.query.filter_by(
            id=line_id,
            work_item_id=work_item.id,
        ).first()

        if line:
            # Delete budget detail first by querying directly
            # (required because work_line_id is the PK of BudgetLineDetail)
            detail = BudgetLineDetail.query.filter_by(work_line_id=line.id).first()
            if detail:
                db.session.delete(detail)
                db.session.flush()
            db.session.delete(line)
            db.session.flush()

    db.session.commit()

    flash("Changes saved.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
    ))


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/fixed-costs")
def work_item_fixed_costs_save(event: str, dept: str, public_id: str):
    """
    Save fixed-cost line items.

    For each fixed-cost expense account:
    - If quantity > 0 and no existing line: create line
    - If quantity > 0 and existing line: update quantity
    - If quantity = 0 and existing line: delete line
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)
    user_ctx = get_user_ctx()

    # Get available fixed-cost accounts for this department
    fixed_cost_accounts = get_fixed_cost_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
    )
    valid_account_ids = {acc.id for acc in fixed_cost_accounts}

    # Build map of existing fixed-cost line IDs by expense_account_id
    # Store IDs only to avoid SQLAlchemy relationship caching issues
    existing_line_ids_by_account = {}
    for line in work_item.lines:
        if line.budget_detail and line.budget_detail.expense_account_id:
            acc = line.budget_detail.expense_account
            if acc and acc.is_fixed_cost:
                existing_line_ids_by_account[acc.id] = line.id

    # Track next line number for new lines (must increment manually in loop)
    next_line_number = get_next_line_number(work_item)

    # Clear the session to avoid relationship caching issues
    db.session.expire_all()

    # Process form data
    # Form fields are: fixed_qty_<account_id>
    for key in request.form:
        if not key.startswith("fixed_qty_"):
            continue

        try:
            account_id = int(key.replace("fixed_qty_", ""))
        except ValueError:
            continue

        # Validate account is valid for this department
        if account_id not in valid_account_ids:
            continue

        # Parse quantity
        qty_str = request.form.get(key, "").strip()
        try:
            quantity = Decimal(qty_str) if qty_str else Decimal(0)
        except InvalidOperation:
            quantity = Decimal(0)

        # Parse notes
        notes_key = f"fixed_notes_{account_id}"
        notes = (request.form.get(notes_key) or "").strip()

        # Get the expense account
        expense_account = ExpenseAccount.query.get(account_id)
        if not expense_account:
            continue

        existing_line_id = existing_line_ids_by_account.get(account_id)

        if quantity <= 0:
            # Delete existing line if present
            if existing_line_id:
                # Query fresh to avoid relationship caching issues
                detail = BudgetLineDetail.query.filter_by(work_line_id=existing_line_id).first()
                if detail:
                    db.session.delete(detail)
                    db.session.flush()
                line_to_delete = WorkLine.query.get(existing_line_id)
                if line_to_delete:
                    db.session.delete(line_to_delete)
                    db.session.flush()
        else:
            # Get effective settings
            settings = get_effective_fixed_cost_settings(expense_account, ctx.event_cycle.id)

            if existing_line_id:
                # Update existing line - query fresh
                existing_line = WorkLine.query.get(existing_line_id)
                if existing_line:
                    detail = BudgetLineDetail.query.filter_by(work_line_id=existing_line_id).first()
                    if detail:
                        detail.quantity = quantity
                        detail.unit_price_cents = settings["unit_price_cents"]
                        detail.description = notes if notes else expense_account.name
                    existing_line.updated_by_user_id = user_ctx.user_id
            else:
                # Create new line
                # Get spend type (fixed-cost accounts should have a default)
                spend_type_id = expense_account.default_spend_type_id
                if not spend_type_id:
                    # Fall back to first allowed spend type
                    allowed = get_allowed_spend_types(expense_account)
                    if allowed:
                        spend_type_id = allowed[0].id
                    else:
                        flash(f"No spend type configured for {expense_account.name}", "error")
                        continue

                work_line = WorkLine(
                    work_item_id=work_item.id,
                    line_number=next_line_number,
                    status=WORK_LINE_STATUS_PENDING,
                    updated_by_user_id=user_ctx.user_id,
                )
                db.session.add(work_line)
                db.session.flush()

                # Increment for next new line
                next_line_number += 1

                budget_detail = BudgetLineDetail(
                    work_line_id=work_line.id,
                    expense_account_id=account_id,
                    spend_type_id=spend_type_id,
                    unit_price_cents=settings["unit_price_cents"],
                    quantity=quantity,
                    frequency_id=settings["frequency_id"],
                    warehouse_flag=settings["warehouse_default"],
                    description=notes if notes else expense_account.name,
                )
                db.session.add(budget_detail)

    db.session.commit()

    flash("Fixed-cost items updated.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id,
        tab="fixed-costs"
    ))


# ============================================================
# Hotel Wizard Route
# ============================================================

@work_bp.post("/<event>/<dept>/budget/item/<public_id>/hotel/add")
def hotel_wizard_add(event: str, dept: str, public_id: str):
    """
    Add a hotel room request via the wizard form.

    Maps wizard selections to the appropriate expense account and creates a line item.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)
    user_ctx = get_user_ctx()

    # Helper to safely parse integers from form data
    def safe_int(value, default=0):
        try:
            return int(value) if value else default
        except (ValueError, TypeError):
            return default

    # Parse form data
    purpose = request.form.get("purpose", "external_partner")  # external_partner, dept_operations, staff_crash
    who_pays = request.form.get("who_pays", "magfest")  # magfest, third_party
    room_type = request.form.get("room_type", "standard")  # standard, executive, hospitality
    room_count = safe_int(request.form.get("room_count"), 1)
    description = (request.form.get("description") or "").strip()

    # Calculate total nights
    event_nights = safe_int(request.form.get("event_nights"), 0)
    use_event_dates = request.form.get("use_event_dates") == "on"
    manual_nights = safe_int(request.form.get("manual_nights"), 4)

    base_nights = event_nights if use_event_dates else manual_nights

    early_arrival = request.form.get("early_arrival") == "on"
    early_nights = safe_int(request.form.get("early_nights"), 0) if early_arrival else 0

    late_departure = request.form.get("late_departure") == "on"
    late_nights = safe_int(request.form.get("late_nights"), 0) if late_departure else 0

    nights_per_room = base_nights + early_nights + late_nights
    total_nights = nights_per_room * room_count

    if total_nights <= 0:
        flash("Please specify at least one night.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Determine expense account code based on selections
    # Room type prefix
    room_type_codes = {
        "standard": "STD",
        "executive": "EXEC",
        "hospitality": "HOSP",
    }
    room_code = room_type_codes.get(room_type, "STD")

    # Determine suffix based on purpose/who_pays
    if purpose == "staff_crash":
        # Staff crash - only executive and hospitality allowed
        if room_type == "standard":
            flash("Standard rooms are not available for staff crash space. Please select a suite.", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))
        account_code = f"HTL_{room_code}_CRASH"
    elif purpose == "external_partner" and who_pays == "third_party":
        account_code = f"HTL_{room_code}_HELD"
    else:
        # MAGFest paid (dept operations or external partner with magfest covering)
        account_code = f"HTL_{room_code}_MAGPAID"

    # Look up the expense account
    expense_account = ExpenseAccount.query.filter_by(code=account_code, is_active=True).first()
    if not expense_account:
        flash(f"Hotel expense account not found: {account_code}. Please contact an administrator.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Get effective settings for this account
    settings = get_effective_fixed_cost_settings(expense_account, ctx.event_cycle.id)

    # Get spend type
    spend_type_id = expense_account.default_spend_type_id
    if not spend_type_id:
        allowed = get_allowed_spend_types(expense_account)
        if allowed:
            spend_type_id = allowed[0].id
        else:
            flash(f"No spend type configured for {expense_account.name}", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))

    # Build description if not provided
    if not description:
        if purpose == "external_partner":
            description = "Hotel room for external partner"
        elif purpose == "dept_operations":
            description = "Hotel room for department operations"
        else:
            description = "Hotel room for staff crash space"

    # Add details about room count and dates to description
    if room_count > 1:
        description = f"{room_count} rooms: {description}"
    if early_nights > 0 or late_nights > 0:
        date_note = []
        if early_nights > 0:
            date_note.append(f"+{early_nights} early")
        if late_nights > 0:
            date_note.append(f"+{late_nights} late")
        description = f"{description} ({', '.join(date_note)})"

    # Create the work line
    next_line_number = get_next_line_number(work_item)

    work_line = WorkLine(
        work_item_id=work_item.id,
        line_number=next_line_number,
        status=WORK_LINE_STATUS_PENDING,
        updated_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_line)
    db.session.flush()

    # Create budget detail
    budget_detail = BudgetLineDetail(
        work_line_id=work_line.id,
        expense_account_id=expense_account.id,
        spend_type_id=spend_type_id,
        unit_price_cents=settings["unit_price_cents"],
        quantity=Decimal(total_nights),
        frequency_id=settings["frequency_id"],
        warehouse_flag=False,
        description=description,
    )
    db.session.add(budget_detail)
    db.session.commit()

    # Calculate total for flash message
    total_cost = settings["unit_price_cents"] * total_nights
    if total_cost > 0:
        flash(f"Added {expense_account.name}: {total_nights} nights = ${total_cost / 100:,.2f}", "success")
    else:
        flash(f"Added {expense_account.name}: {total_nights} nights (no budget impact)", "success")

    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id,
        tab="hotel-services"
    ))
