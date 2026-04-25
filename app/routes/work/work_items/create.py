"""
Work item creation routes - PRIMARY and SUPPLEMENTARY requests.
"""
from flask import render_template, redirect, url_for, abort, flash, request

from app import db
from app.models import (
    WorkItem,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    get_portfolio_context,
    require_budget_work_type,
    require_portfolio_view,
    require_portfolio_edit,
    generate_public_id_for_portfolio,
)


# ============================================================
# Create PRIMARY Routes
# ============================================================

@work_bp.get("/<event>/<dept>/<work_type_slug>/primary/new")
@work_bp.get("/<event>/<dept>/budget/primary/new")
def primary_new(event: str, dept: str, work_type_slug: str = "budget"):
    """
    Show confirmation page for creating a PRIMARY request.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)
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


@work_bp.post("/<event>/<dept>/<work_type_slug>/primary")
@work_bp.post("/<event>/<dept>/budget/primary")
def primary_create(event: str, dept: str, work_type_slug: str = "budget"):
    """
    Create a new PRIMARY work item.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)
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
        public_id=generate_public_id_for_portfolio(ctx.portfolio),
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
# Create SUPPLEMENTARY Routes
# ============================================================

@work_bp.get("/<event>/<dept>/<work_type_slug>/supplementary/new")
@work_bp.get("/<event>/<dept>/budget/supplementary/new")
def supplementary_new(event: str, dept: str, work_type_slug: str = "budget"):
    """
    Show confirmation page for creating a SUPPLEMENTARY request.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)
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


@work_bp.post("/<event>/<dept>/<work_type_slug>/supplementary")
@work_bp.post("/<event>/<dept>/budget/supplementary")
def supplementary_create(event: str, dept: str, work_type_slug: str = "budget"):
    """
    Create a new SUPPLEMENTARY work item.
    """
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)
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

    # Get optional reason from form
    reason = (request.form.get("reason") or "").strip()
    if len(reason) > 256:
        reason = reason[:256]

    # Create the work item (uses shared portfolio sequence for ID)
    user_ctx = get_user_ctx()
    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id_for_portfolio(ctx.portfolio),
        created_by_user_id=user_ctx.user_id,
        reason=reason if reason else None,
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
