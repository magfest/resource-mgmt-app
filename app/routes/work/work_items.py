"""
Work item routes - create, view, edit, and submit budget requests.
"""
from datetime import datetime

from flask import render_template, redirect, url_for, request, abort, flash
from sqlalchemy.orm import selectinload, joinedload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    BudgetLineDetail,
    WorkItemComment,
    WorkItemAuditEvent,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    COMMENT_VISIBILITY_PUBLIC,
    COMMENT_VISIBILITY_ADMIN,
    AUDIT_EVENT_SUBMIT,
    AUDIT_EVENT_NEEDS_INFO_REQUESTED,
    AUDIT_EVENT_NEEDS_INFO_RESPONDED,
    AUDIT_EVENT_CHECKOUT,
    AUDIT_EVENT_CHECKIN,
    AUDIT_EVENT_VIEW,
)
from app.routes import get_user_ctx
from . import work_bp
from .helpers import (
    get_portfolio_context,
    require_portfolio_view,
    require_portfolio_edit,
    require_work_item_view,
    require_work_item_edit,
    build_portfolio_perms,
    build_work_item_perms,
    generate_public_id,
    compute_work_item_totals,
    format_currency,
    friendly_status,
    get_comment_visibility,
    get_visible_expense_accounts,
    get_fixed_cost_expense_accounts,
    get_hotel_service_expense_accounts,
    get_non_hotel_fixed_cost_accounts,
    get_effective_fixed_cost_settings,
    get_allowed_spend_types,
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
    get_spend_types,
    get_next_line_number,
    is_checked_out,
    get_checkout_info,
    checkout_work_item,
    checkin_work_item,
    _is_approver_for_work_item,
    filter_lines_for_user,
    get_kicked_back_lines_summary,
    get_unified_audit_events,
)
from app.routes.admin_final.helpers import (
    can_finalize_work_item,
    get_finalization_summary,
)


# ============================================================
# Helper Functions
# ============================================================

def get_work_item_by_public_id(event: str, dept: str, public_id: str):
    """
    Get a work item by public_id and verify it belongs to the correct portfolio.

    Returns tuple of (work_item, ctx) or aborts with 404.
    Eager loads lines with budget details, expense accounts, spend types, etc.
    """
    ctx = get_portfolio_context(event, dept)

    work_item = WorkItem.query.filter_by(
        public_id=public_id,
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        # Eager load lines with all their related data
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.expense_account),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.spend_type),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.confidence_level),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.frequency),
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail).joinedload(BudgetLineDetail.priority),
        # Eager load comments
        selectinload(WorkItem.comments),
    ).first()

    if not work_item:
        abort(404, f"Work item not found: {public_id}")

    return work_item, ctx


# ============================================================
# Create PRIMARY Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/primary/new")
def primary_new(event: str, dept: str):
    """
    Show confirmation page for creating a PRIMARY request.
    """
    ctx = get_portfolio_context(event, dept)
    perms = require_portfolio_view(ctx)

    # Check if user can create primary
    if not perms.can_create_primary:
        # Check if PRIMARY already exists
        existing = WorkItem.query.filter_by(
            portfolio_id=ctx.portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            is_archived=False,
        ).first()

        if existing:
            flash("A Primary Budget Request already exists for this portfolio.", "warning")
            return redirect(url_for(
                "work.work_item_detail",
                event=event,
                dept=dept,
                public_id=existing.public_id
            ))

        abort(403, "You do not have permission to create a Primary Budget Request.")

    return render_template(
        "budget/primary_new.html",
        ctx=ctx,
        perms=perms,
    )


@work_bp.post("/<event>/<dept>/budget/primary")
def primary_create(event: str, dept: str):
    """
    Create a new PRIMARY work item.
    """
    ctx = get_portfolio_context(event, dept)
    perms = require_portfolio_edit(ctx)

    # Validate: no existing PRIMARY
    existing = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if existing:
        flash("A Primary Budget Request already exists for this portfolio.", "warning")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=existing.public_id
        ))

    if not perms.can_create_primary:
        abort(403, "You do not have permission to create a Primary Budget Request.")

    # Create the work item
    user_ctx = get_user_ctx()
    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id("BUD"),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.commit()

    flash("Primary Budget Request created successfully.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=work_item.public_id
    ))


# ============================================================
# Work Item Detail/View Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/item/<public_id>")
def work_item_detail(event: str, dept: str, public_id: str):
    """
    View a work item and its lines.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)
    user_ctx = get_user_ctx()

    # Log view for non-draft items when viewed by someone other than the requester
    is_requester = work_item.created_by_user_id == user_ctx.user_id
    if work_item.status != WORK_ITEM_STATUS_DRAFT and not is_requester:
        view_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_VIEW,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(view_event)
        db.session.commit()

    # Compute totals (from ALL lines for context)
    totals = compute_work_item_totals(work_item)

    # Check if user is a department member (requester/dept member should see all lines)
    # This is different from perms.can_view which includes reviewer access
    has_dept_membership = (
        work_item.created_by_user_id == user_ctx.user_id or
        (ctx.membership and ctx.membership.can_view_work_type(ctx.work_type.id)) or
        (ctx.division_membership and ctx.division_membership.can_view_work_type(ctx.work_type.id))
    )

    # Get all lines and filter for display based on user access
    all_lines = list(work_item.lines)
    lines, lines_filtered = filter_lines_for_user(
        all_lines,
        user_ctx,
        is_worktype_admin=perms.is_worktype_admin,
        has_edit_access=has_dept_membership,  # Dept members/requesters see all lines
    )
    total_lines_count = len(all_lines)

    # Get kicked-back lines (NEEDS_INFO or NEEDS_ADJUSTMENT) with their review notes
    kicked_back_lines = get_kicked_back_lines_summary(lines)

    # Check if can finalize (for admins - allowed from AWAITING_DISPATCH or SUBMITTED)
    can_finalize = False
    finalization_summary = None
    if perms.is_worktype_admin and work_item.status in (WORK_ITEM_STATUS_AWAITING_DISPATCH, WORK_ITEM_STATUS_SUBMITTED):
        can_finalize, _ = can_finalize_work_item(work_item)
        finalization_summary = get_finalization_summary(work_item)

    # Filter comments for non-admins
    comments = list(work_item.comments)
    if not perms.is_worktype_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    # Check if user can add comments (admin OR reviewer for any line)
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_add_comment = perms.is_worktype_admin or is_approver_for_item

    # Fetch audit events for budget admins (super admin or worktype admin)
    can_view_audit = user_ctx.is_super_admin or perms.is_worktype_admin
    audit_events = get_unified_audit_events(work_item) if can_view_audit else []

    return render_template(
        "budget/work_item_detail.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        lines=lines,
        totals=totals,
        total_lines_count=total_lines_count,
        lines_filtered=lines_filtered,
        format_currency=format_currency,
        friendly_status=friendly_status,
        kicked_back_lines=kicked_back_lines,
        can_finalize=can_finalize,
        finalization_summary=finalization_summary,
        filtered_comments=comments,
        can_add_comment=can_add_comment,
        audit_events=audit_events,
        can_view_audit=can_view_audit,
        user_ctx=user_ctx,
    )


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/comment")
def work_item_comment(event: str, dept: str, public_id: str):
    """Add a standalone comment to a work item."""
    user_ctx = get_user_ctx()
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    # Get return URL (for redirecting back to edit page if that's where they came from)
    return_to = (request.form.get("return_to") or "").strip()
    default_redirect = url_for("work.work_item_detail", event=event, dept=dept,
                               public_id=public_id)

    # Permission check: must be admin OR approver OR can edit (requester)
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_comment = perms.is_worktype_admin or is_approver_for_item or perms.can_edit
    if not can_comment:
        flash("You do not have permission to comment on this request.", "error")
        return redirect(return_to or default_redirect)

    comment_text = (request.form.get("comment") or "").strip()
    if not comment_text:
        flash("Comment text is required.", "error")
        return redirect(return_to or default_redirect)

    visibility = get_comment_visibility(request.form, user_ctx.is_super_admin)
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=visibility,
        body=comment_text,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)
    db.session.commit()

    flash("Comment added.", "success")
    # If returning to the edit page, keep the notes tab active
    if return_to and "edit" in return_to:
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id,
            tab="notes"
        ))
    return redirect(return_to or default_redirect)


# ============================================================
# Work Item Edit Routes
# ============================================================

def _calculate_event_nights(start_date, end_date):
    """Calculate the number of nights between start and end dates."""
    if not start_date or not end_date:
        return None
    return max(0, (end_date - start_date).days)


@work_bp.get("/<event>/<dept>/budget/item/<public_id>/edit")
def work_item_edit(event: str, dept: str, public_id: str):
    """
    Edit form for a DRAFT work item.
    """
    from app.models import UI_GROUP_HOTEL_SERVICES

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
            "unit_price_cents": settings["unit_price_cents"],
            "frequency_id": settings["frequency_id"],
            "warehouse_default": settings["warehouse_default"],
            "existing_line": existing_line,
            "existing_quantity": existing_quantity,
            "existing_notes": existing_notes,
        })

    # Calculate event nights for hotel calculator
    event_nights = _calculate_event_nights(
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
    from decimal import Decimal, InvalidOperation
    from app.models import ExpenseAccount, SpendType

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
    from decimal import Decimal
    from app.models import ExpenseAccount

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


# ============================================================
# Submit Route
# ============================================================

@work_bp.post("/<event>/<dept>/budget/item/<public_id>/submit")
def work_item_submit(event: str, dept: str, public_id: str):
    """
    Submit a DRAFT work item for review.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_edit(work_item, ctx)

    # Validate: status must be DRAFT
    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("Only DRAFT requests can be submitted.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: must have at least 1 line
    if len(work_item.lines) == 0:
        flash("Cannot submit: request has no lines.", "error")
        return redirect(url_for(
            "work.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: all lines must have budget details with expense accounts
    for line in work_item.lines:
        if not line.budget_detail:
            flash(f"Cannot submit: line {line.line_number} is missing budget details.", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))
        expense_account = line.budget_detail.expense_account
        if not expense_account:
            flash(f"Cannot submit: line {line.line_number} has no expense account.", "error")
            return redirect(url_for(
                "work.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))

    user_ctx = get_user_ctx()

    # Update work item status to AWAITING_DISPATCH
    # Approval group assignment and WorkLineReview creation happens during dispatch
    work_item.status = WORK_ITEM_STATUS_AWAITING_DISPATCH
    work_item.submitted_at = datetime.utcnow()
    work_item.submitted_by_user_id = user_ctx.user_id

    # Create audit event for submission
    totals = compute_work_item_totals(work_item)
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_SUBMIT,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "line_count": len(work_item.lines),
            "total_requested_cents": totals.get("requested", 0),
        },
    )
    db.session.add(audit_event)

    db.session.commit()

    # Send notification to budget admins
    from app.services.notifications import notify_budget_submitted
    notify_budget_submitted(work_item)
    db.session.commit()  # Commit notification log

    flash(
        "Budget request submitted! A budget admin will assign reviewers and "
        "dispatch it for approval. You'll be notified if any changes are needed.",
        "success"
    )
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))


# ============================================================
# Create SUPPLEMENTARY Routes
# ============================================================

@work_bp.get("/<event>/<dept>/budget/supplementary/new")
def supplementary_new(event: str, dept: str):
    """
    Show confirmation page for creating a SUPPLEMENTARY request.
    """
    ctx = get_portfolio_context(event, dept)
    perms = require_portfolio_view(ctx)

    # Check if user can create supplementary
    if not perms.can_create_supplementary:
        # Check if PRIMARY exists and is finalized
        existing = WorkItem.query.filter_by(
            portfolio_id=ctx.portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            is_archived=False,
        ).first()

        if not existing:
            flash("A Primary Budget Request must exist before creating a supplementary.", "warning")
            return redirect(url_for(
                "work.portfolio_landing",
                event=event,
                dept=dept,
            ))

        if existing.status != WORK_ITEM_STATUS_FINALIZED:
            flash("The Primary Budget Request must be finalized before creating a supplementary.", "warning")
            return redirect(url_for(
                "work.work_item_detail",
                event=event,
                dept=dept,
                public_id=existing.public_id
            ))

        abort(403, "You do not have permission to create a Supplementary Budget Request.")

    # Count existing supplementaries
    supp_count = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        is_archived=False,
    ).count()

    return render_template(
        "budget/supplementary_new.html",
        ctx=ctx,
        perms=perms,
        supplementary_number=supp_count + 1,
    )


@work_bp.post("/<event>/<dept>/budget/supplementary")
def supplementary_create(event: str, dept: str):
    """
    Create a new SUPPLEMENTARY work item.
    """
    ctx = get_portfolio_context(event, dept)
    perms = require_portfolio_edit(ctx)

    # Validate: PRIMARY must exist and be FINALIZED
    existing_primary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if not existing_primary:
        flash("A Primary Budget Request must exist before creating a supplementary.", "warning")
        return redirect(url_for(
            "work.portfolio_landing",
            event=event,
            dept=dept,
        ))

    if existing_primary.status != WORK_ITEM_STATUS_FINALIZED:
        flash("The Primary Budget Request must be finalized before creating a supplementary.", "warning")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=existing_primary.public_id
        ))

    if not perms.can_create_supplementary:
        abort(403, "You do not have permission to create a Supplementary Budget Request.")

    # Create the work item with SUP- prefix
    user_ctx = get_user_ctx()
    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id("SUP"),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.commit()

    flash("Supplementary Budget Request created successfully.", "success")
    return redirect(url_for(
        "work.work_item_edit",
        event=event,
        dept=dept,
        public_id=work_item.public_id
    ))


# ============================================================
# Checkout Routes
# ============================================================

@work_bp.post("/<event>/<dept>/budget/item/<public_id>/checkout")
def work_item_checkout(event: str, dept: str, public_id: str):
    """
    Checkout a work item for review.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    # Get optional return_to URL from form data
    return_to = (request.form.get("return_to") or "").strip()

    default_redirect = url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    )

    if not perms.can_checkout:
        flash("You cannot checkout this work item.", "error")
        return redirect(return_to or default_redirect)

    user_ctx = get_user_ctx()
    if checkout_work_item(work_item, user_ctx):
        # Create audit event for checkout
        audit_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_CHECKOUT,
            created_by_user_id=user_ctx.user_id,
            snapshot={
                "expires_at": work_item.checked_out_expires_at.isoformat() if work_item.checked_out_expires_at else None,
            },
        )
        db.session.add(audit_event)
        db.session.commit()
        flash("Work item checked out. You have the lock for review.", "success")
    else:
        flash("Could not checkout work item.", "error")

    return redirect(return_to or default_redirect)


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/checkin")
def work_item_checkin(event: str, dept: str, public_id: str):
    """
    Release checkout (check-in) on a work item.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    # Get optional return_to URL from form data
    return_to = (request.form.get("return_to") or "").strip()

    default_redirect = url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    )

    if not perms.can_checkin:
        flash("You cannot release this checkout.", "error")
        return redirect(return_to or default_redirect)

    user_ctx = get_user_ctx()
    force = perms.is_worktype_admin and not perms.is_checked_out_by_current_user

    # Capture who had checkout before releasing (for audit)
    previous_holder = work_item.checked_out_by_user_id

    if checkin_work_item(work_item, user_ctx, force=force):
        # Create audit event for checkin
        audit_event = WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_CHECKIN,
            created_by_user_id=user_ctx.user_id,
            snapshot={
                "previous_holder": previous_holder,
                "forced": force,
            },
        )
        db.session.add(audit_event)
        db.session.commit()
        flash("Lock released.", "success")
    else:
        flash("Could not release lock.", "error")

    return redirect(return_to or default_redirect)


# ============================================================
# Quick Review Route
# ============================================================

@work_bp.get("/<event>/<dept>/budget/item/<public_id>/quick-review")
def quick_review(event: str, dept: str, public_id: str):
    """
    Quick review page - shows all lines with inline action buttons.
    Designed for rapid review without navigating into each line.
    """
    user_ctx = get_user_ctx()
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    # Must be a reviewer or admin to use quick review
    if not (perms.is_worktype_admin or _is_approver_for_work_item(work_item, user_ctx)):
        flash("You don't have permission to review this request.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Get checkout info
    checked_out = is_checked_out(work_item)
    has_checkout = work_item.checked_out_by_user_id == user_ctx.user_id
    can_checkout = perms.can_checkout

    # Filter lines for approval group users
    all_lines = list(work_item.lines)
    visible_lines, lines_filtered = filter_lines_for_user(
        all_lines,
        user_ctx,
        is_worktype_admin=user_ctx.is_super_admin,
        has_edit_access=False,  # Quick review is for reviewers only
    )
    total_lines_count = len(all_lines)

    # Batch load reviews for all visible lines (avoids N+1 queries)
    from app.routes.admin_final.helpers import batch_load_reviews_by_line
    visible_line_ids = [line.id for line in visible_lines]
    reviews_by_line = batch_load_reviews_by_line(visible_line_ids)

    # Build line data only for visible lines
    lines_data = []
    summary = {"pending": 0, "approved": 0, "kicked_back": 0, "rejected": 0}

    for line in visible_lines:
        detail = line.budget_detail
        # Get approval group review from batch-loaded data
        review = reviews_by_line.get(line.id, {}).get('ag')
        total_cents = detail.unit_price_cents * int(detail.quantity) if detail else 0

        # Update summary
        status = line.status.upper() if line.status else "PENDING"
        if status == "PENDING":
            summary["pending"] += 1
        elif status == "APPROVED":
            summary["approved"] += 1
        elif status in ("NEEDS_INFO", "NEEDS_ADJUSTMENT"):
            summary["kicked_back"] += 1
        elif status == "REJECTED":
            summary["rejected"] += 1

        lines_data.append({
            "line": line,
            "detail": detail,
            "review": review,
            "total_cents": total_cents,
        })

    return render_template(
        "budget/quick_review.html",
        ctx=ctx,
        perms=perms,
        user_ctx=user_ctx,
        work_item=work_item,
        lines=lines_data,
        total_lines_count=total_lines_count,
        lines_filtered=lines_filtered,
        summary=summary,
        is_checked_out=checked_out,
        has_checkout=has_checkout,
        can_checkout=can_checkout,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


# ============================================================
# NEEDS_INFO Routes
# ============================================================

@work_bp.post("/<event>/<dept>/budget/item/<public_id>/request-info")
def work_item_request_info(event: str, dept: str, public_id: str):
    """
    Request information from the requester (sets status to NEEDS_INFO).
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    if not perms.can_request_info:
        flash("You cannot request information on this work item.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("A message is required when requesting information.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()

    # Add request-level comment
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=f"[INFO REQUESTED] {message}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)

    # Update work item status
    work_item.status = WORK_ITEM_STATUS_NEEDS_INFO
    work_item.needs_info_requested_at = datetime.utcnow()
    work_item.needs_info_requested_by_user_id = user_ctx.user_id

    # Create audit event
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_NEEDS_INFO_REQUESTED,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "message": message,
        },
    )
    db.session.add(audit_event)

    # Release checkout
    checkin_work_item(work_item, user_ctx)

    db.session.commit()

    flash("Information requested. The requester has been notified.", "success")
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/respond-info")
def work_item_respond_info(event: str, dept: str, public_id: str):
    """
    Respond to information request (sets status back to SUBMITTED).
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    if not perms.can_respond_to_info:
        flash("You cannot respond to this information request.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    response = (request.form.get("response") or "").strip()
    if not response:
        flash("A response is required.", "error")
        return redirect(url_for(
            "work.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()

    # Add request-level comment
    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=COMMENT_VISIBILITY_PUBLIC,
        body=f"[INFO RESPONSE] {response}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)

    # Update work item status back to SUBMITTED
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.needs_info_requested_at = None
    work_item.needs_info_requested_by_user_id = None

    # Create audit event
    audit_event = WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_NEEDS_INFO_RESPONDED,
        created_by_user_id=user_ctx.user_id,
        snapshot={
            "response": response,
        },
    )
    db.session.add(audit_event)

    db.session.commit()

    flash("Response submitted. The request is back in review.", "success")
    return redirect(url_for(
        "work.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))
