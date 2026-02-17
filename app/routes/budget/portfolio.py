"""
Portfolio routes - landing page for department budget portfolios.
"""
from flask import render_template

from app.models import (
    WorkItem,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
)
from . import budget_bp
from .helpers import (
    get_portfolio_context,
    require_portfolio_view,
    build_portfolio_perms,
    compute_portfolio_totals,
    compute_work_item_totals,
    format_currency,
)


@budget_bp.get("/<event>/<dept>/budget")
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

    # Get PRIMARY work item if exists
    primary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    # Get SUPPLEMENTARY work items
    supplementary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        is_archived=False,
    ).order_by(WorkItem.created_at.desc()).all()

    # Compute totals
    totals = compute_portfolio_totals(ctx.portfolio)

    # Compute work item totals for cards
    primary_totals = compute_work_item_totals(primary) if primary else None
    supplementary_totals = {
        item.id: compute_work_item_totals(item) for item in supplementary
    }

    return render_template(
        "budget/portfolio_landing.html",
        ctx=ctx,
        perms=perms,
        primary=primary,
        primary_totals=primary_totals,
        supplementary=supplementary,
        supplementary_totals=supplementary_totals,
        totals=totals,
        format_currency=format_currency,
    )
