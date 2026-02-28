"""
Portfolio routes - landing page for department budget portfolios.
"""
from flask import render_template, abort
from sqlalchemy.orm import selectinload, joinedload

from app import db
from app.models import (
    WorkItem,
    WorkLine,
    WorkTypeConfig,
    EventCycle,
    Department,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
)
from app.routes import get_user_ctx, render_page
from . import work_bp
from .helpers import (
    get_portfolio_context,
    require_portfolio_view,
    build_portfolio_perms,
    compute_portfolio_totals,
    compute_work_item_totals,
    compute_line_status_summary,
    format_currency,
    friendly_status,
)


@work_bp.get("/<event>/<dept>/budget")
def portfolio_landing(event: str, dept: str):
    """
    Portfolio landing page.

    Shows the department's budget portfolio for an event cycle:
    - Header with event/department info
    - Totals summary
    - PRIMARY work item (or create button)
    - SUPPLEMENTARY work items list
    """
    # Build context and check permissions
    ctx = get_portfolio_context(event, dept)
    perms = require_portfolio_view(ctx)

    # Get PRIMARY work item if exists - eager load lines with budget details
    primary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).first()

    # Get SUPPLEMENTARY work items - eager load lines with budget details
    supplementary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        is_archived=False,
    ).options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).order_by(WorkItem.created_at.desc()).all()

    # Compute totals
    totals = compute_portfolio_totals(ctx.portfolio)

    # Compute work item totals and line status summaries for cards
    primary_totals = compute_work_item_totals(primary) if primary else None
    primary_line_summary = compute_line_status_summary(primary) if primary else None
    supplementary_totals = {
        item.id: compute_work_item_totals(item) for item in supplementary
    }
    supplementary_line_summaries = {
        item.id: compute_line_status_summary(item) for item in supplementary
    }

    return render_template(
        "budget/portfolio_landing.html",
        ctx=ctx,
        perms=perms,
        primary=primary,
        primary_totals=primary_totals,
        primary_line_summary=primary_line_summary,
        supplementary=supplementary,
        supplementary_totals=supplementary_totals,
        supplementary_line_summaries=supplementary_line_summaries,
        totals=totals,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


# ============================================================
# Placeholder routes for future work types
# ============================================================

@work_bp.get("/<event>/<dept>/contracts")
def contracts_placeholder(event: str, dept: str):
    """
    Placeholder for Contracts work type - coming soon.
    """
    user_ctx = get_user_ctx()

    # Look up event cycle and department for context
    event_cycle = EventCycle.query.filter_by(code=event.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event}")

    department = Department.query.filter_by(code=dept.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept}")

    return render_page(
        "budget/coming_soon.html",
        event_cycle=event_cycle,
        department=department,
        work_type_name="Contracts",
        work_type_description="Contract management and vendor agreements",
        contact_team="the Business Team",
        contact_email="biz@magfest.org",
    )


@work_bp.get("/<event>/<dept>/supply")
def supply_placeholder(event: str, dept: str):
    """
    Placeholder for Supply Orders work type - coming soon.
    """
    user_ctx = get_user_ctx()

    # Look up event cycle and department for context
    event_cycle = EventCycle.query.filter_by(code=event.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event}")

    department = Department.query.filter_by(code=dept.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept}")

    return render_page(
        "budget/coming_soon.html",
        event_cycle=event_cycle,
        department=department,
        work_type_name="Supply Orders",
        work_type_description="Warehouse inventory and supply requests",
        contact_team="FestOps",
        contact_email="festops@magfest.org",
    )
