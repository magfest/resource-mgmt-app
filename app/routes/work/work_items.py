"""
Work item routes - create, view, edit, and submit budget requests.
"""
from datetime import datetime

from flask import render_template, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkLineReview,
    BudgetLineDetail,
    WorkItemComment,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    COMMENT_VISIBILITY_PUBLIC,
    COMMENT_VISIBILITY_ADMIN,
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
    get_visible_expense_accounts,
    get_fixed_cost_expense_accounts,
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
                "budget.work_item_detail",
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
            "budget.work_item_detail",
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
        "budget.work_item_edit",
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

    # Compute totals
    totals = compute_work_item_totals(work_item)

    # Get lines with details
    lines = work_item.lines

    # Check if can finalize (for admins)
    can_finalize = False
    finalization_summary = None
    if perms.is_admin and work_item.status == WORK_ITEM_STATUS_SUBMITTED:
        can_finalize, _ = can_finalize_work_item(work_item)
        finalization_summary = get_finalization_summary(work_item)

    # Filter comments for non-admins
    comments = list(work_item.comments)
    if not perms.is_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    # Check if user can add comments (admin OR reviewer for any line)
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_add_comment = perms.is_admin or is_approver_for_item

    return render_template(
        "budget/work_item_detail.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        lines=lines,
        totals=totals,
        format_currency=format_currency,
        can_finalize=can_finalize,
        finalization_summary=finalization_summary,
        filtered_comments=comments,
        can_add_comment=can_add_comment,
        user_ctx=user_ctx,
    )


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/comment")
def work_item_comment(event: str, dept: str, public_id: str):
    """Add a standalone comment to a work item."""
    user_ctx = get_user_ctx()
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    # Permission check: must be admin OR approver for any line
    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    if not (perms.is_admin or is_approver_for_item):
        flash("You do not have permission to comment on this request.", "error")
        return redirect(url_for("work.work_item_detail", event=event, dept=dept,
                                public_id=public_id))

    comment_text = (request.form.get("comment") or "").strip()
    if not comment_text:
        flash("Comment text is required.", "error")
        return redirect(url_for("work.work_item_detail", event=event, dept=dept,
                                public_id=public_id))

    # Check if admin requested admin-only visibility
    # Both conditions must be true: checkbox selected AND user is admin
    admin_only_requested = request.form.get("admin_only") == "1"
    is_admin_only = admin_only_requested and user_ctx.is_admin

    if is_admin_only:
        visibility = COMMENT_VISIBILITY_ADMIN
    else:
        visibility = COMMENT_VISIBILITY_PUBLIC

    comment = WorkItemComment(
        work_item_id=work_item.id,
        visibility=visibility,
        body=f"[COMMENT] {comment_text}",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(comment)
    db.session.commit()

    flash("Comment added.", "success")
    return redirect(url_for("work.work_item_detail", event=event, dept=dept,
                            public_id=public_id))


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

    # Get lines - separate fixed-cost lines from regular lines
    all_lines = work_item.lines
    regular_lines = []
    fixed_cost_lines = []

    for line in all_lines:
        # Lines without budget details go to regular section
        if not line.budget_detail or not line.budget_detail.expense_account:
            regular_lines.append(line)
            continue

        # Lines with fixed-cost accounts go to fixed section
        if line.budget_detail.expense_account.is_fixed_cost:
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

    # Get fixed-cost expense accounts
    fixed_cost_accounts = get_fixed_cost_expense_accounts(
        department_id=ctx.department.id,
        event_cycle_id=ctx.event_cycle.id,
    )

    # Build fixed-cost data with effective settings and existing quantities
    fixed_cost_data = []
    existing_fixed_by_account_id = {
        line.budget_detail.expense_account_id: line
        for line in fixed_cost_lines
        if line.budget_detail
    }

    for acc in fixed_cost_accounts:
        settings = get_effective_fixed_cost_settings(acc, ctx.event_cycle.id)
        existing_line = existing_fixed_by_account_id.get(acc.id)

        # Get existing quantity if line exists with budget detail
        if existing_line and existing_line.budget_detail:
            existing_quantity = existing_line.budget_detail.quantity
        else:
            existing_quantity = None

        fixed_cost_data.append({
            "account": acc,
            "unit_price_cents": settings["unit_price_cents"],
            "frequency_id": settings["frequency_id"],
            "warehouse_default": settings["warehouse_default"],
            "existing_line": existing_line,
            "existing_quantity": existing_quantity,
        })

    return render_template(
        "budget/work_item_edit.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        lines=regular_lines,
        fixed_cost_lines=fixed_cost_lines,
        fixed_cost_data=fixed_cost_data,
        totals=totals,
        expense_accounts=expense_accounts,
        spend_types_by_account=spend_types_by_account,
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
        format_currency=format_currency,
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
        "budget.work_item_edit",
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
                    description=f"{expense_account.name}",
                )
                db.session.add(budget_detail)

    db.session.commit()

    flash("Fixed-cost items updated.", "success")
    return redirect(url_for(
        "budget.work_item_edit",
        event=event,
        dept=dept,
        public_id=public_id
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
            "budget.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: must have at least 1 line
    if len(work_item.lines) == 0:
        flash("Cannot submit: request has no lines.", "error")
        return redirect(url_for(
            "budget.work_item_edit",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    # Validate: all lines must have budget details and approval group routing
    for line in work_item.lines:
        if not line.budget_detail:
            flash(f"Cannot submit: line {line.line_number} is missing budget details.", "error")
            return redirect(url_for(
                "budget.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))
        # Check that the expense account has approval group routing
        expense_account = line.budget_detail.expense_account
        if not expense_account:
            flash(f"Cannot submit: line {line.line_number} has no expense account.", "error")
            return redirect(url_for(
                "budget.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))
        if not expense_account.approval_group_id:
            flash(
                f"Cannot submit: expense account '{expense_account.name}' has no approval group configured. "
                "Please contact an administrator.",
                "error"
            )
            return redirect(url_for(
                "budget.work_item_edit",
                event=event,
                dept=dept,
                public_id=public_id
            ))

    user_ctx = get_user_ctx()

    # Update work item status
    work_item.status = WORK_ITEM_STATUS_SUBMITTED
    work_item.submitted_at = datetime.utcnow()
    work_item.submitted_by_user_id = user_ctx.user_id

    # Snapshot routing: set routed_approval_group_id from expense account
    # and create WorkLineReview records for each line
    for line in work_item.lines:
        if line.budget_detail:
            detail = line.budget_detail
            expense_account = detail.expense_account
            if expense_account and expense_account.approval_group_id:
                detail.routed_approval_group_id = expense_account.approval_group_id

            # Set initial review stage
            line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP

            # Create WorkLineReview record for APPROVAL_GROUP stage
            review = WorkLineReview(
                work_line_id=line.id,
                stage=REVIEW_STAGE_APPROVAL_GROUP,
                approval_group_id=detail.routed_approval_group_id,
                status=REVIEW_STATUS_PENDING,
                created_by_user_id=user_ctx.user_id,
            )
            db.session.add(review)

    db.session.commit()

    flash("Budget request submitted for review.", "success")
    return redirect(url_for(
        "budget.work_item_detail",
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
                "budget.portfolio_landing",
                event=event,
                dept=dept,
            ))

        if existing.status != WORK_ITEM_STATUS_FINALIZED:
            flash("The Primary Budget Request must be finalized before creating a supplementary.", "warning")
            return redirect(url_for(
                "budget.work_item_detail",
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
            "budget.portfolio_landing",
            event=event,
            dept=dept,
        ))

    if existing_primary.status != WORK_ITEM_STATUS_FINALIZED:
        flash("The Primary Budget Request must be finalized before creating a supplementary.", "warning")
        return redirect(url_for(
            "budget.work_item_detail",
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
        "budget.work_item_edit",
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

    if not perms.can_checkout:
        flash("You cannot checkout this work item.", "error")
        return redirect(url_for(
            "budget.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()
    if checkout_work_item(work_item, user_ctx):
        db.session.commit()
        flash("Work item checked out. You have the lock for review.", "success")
    else:
        flash("Could not checkout work item.", "error")

    return redirect(url_for(
        "budget.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))


@work_bp.post("/<event>/<dept>/budget/item/<public_id>/checkin")
def work_item_checkin(event: str, dept: str, public_id: str):
    """
    Release checkout (check-in) on a work item.
    """
    work_item, ctx = get_work_item_by_public_id(event, dept, public_id)
    perms = require_work_item_view(work_item, ctx)

    if not perms.can_checkin:
        flash("You cannot release this checkout.", "error")
        return redirect(url_for(
            "budget.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    user_ctx = get_user_ctx()
    force = perms.is_admin and not perms.is_checked_out_by_current_user
    if checkin_work_item(work_item, user_ctx, force=force):
        db.session.commit()
        flash("Lock released.", "success")
    else:
        flash("Could not release lock.", "error")

    return redirect(url_for(
        "budget.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))


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
            "budget.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    message = (request.form.get("message") or "").strip()
    if not message:
        flash("A message is required when requesting information.", "error")
        return redirect(url_for(
            "budget.work_item_detail",
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

    # Release checkout
    checkin_work_item(work_item, user_ctx)

    db.session.commit()

    flash("Information requested. The requester has been notified.", "success")
    return redirect(url_for(
        "budget.work_item_detail",
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
            "budget.work_item_detail",
            event=event,
            dept=dept,
            public_id=public_id
        ))

    response = (request.form.get("response") or "").strip()
    if not response:
        flash("A response is required.", "error")
        return redirect(url_for(
            "budget.work_item_detail",
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

    db.session.commit()

    flash("Response submitted. The request is back in review.", "success")
    return redirect(url_for(
        "budget.work_item_detail",
        event=event,
        dept=dept,
        public_id=public_id
    ))
