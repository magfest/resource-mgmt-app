"""
Supply portfolio landing — list of a department's supply orders for an
event.
"""
from flask import render_template
from sqlalchemy.orm import selectinload, joinedload

from app.models import (
    WorkItem,
    WorkLine,
    SupplyOrderLineDetail,
)
from .. import work_bp
from ..helpers import (
    get_portfolio_context,
    require_portfolio_view,
    compute_line_status_summary,
    friendly_status,
)


@work_bp.get("/<event>/<dept>/supply")
def supply_portfolio_landing(event: str, dept: str):
    """
    Landing page for a department's supply orders.

    Lists all non-archived supply-order work items, newest first. Supply
    is requester-facing and never shows prices/costs, so cards surface
    line counts and needed-by dates instead of totals.
    """
    ctx = get_portfolio_context(event, dept, "supply")
    perms = require_portfolio_view(ctx)
    # The template gates the "Start new order" CTA on perms.can_edit, NOT
    # perms.can_create_primary: supply allows unlimited independent orders,
    # so the engine's single-PRIMARY-per-portfolio gate doesn't apply here.

    # All work items in this portfolio. Eager load lines + their supply
    # detail (and the catalog item) plus the order-level delivery detail
    # so cards can show item/needed-by info without N+1 queries.
    work_items = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        is_archived=False,
    ).options(
        selectinload(WorkItem.lines)
            .joinedload(WorkLine.supply_detail)
            .joinedload(SupplyOrderLineDetail.item),
        joinedload(WorkItem.supply_order_detail),
    ).order_by(WorkItem.created_at.desc()).all()

    item_line_summaries = {
        item.id: compute_line_status_summary(item) for item in work_items
    }
    item_line_counts = {
        item.id: len(item.lines) for item in work_items
    }

    return render_template(
        "supply/portfolio_landing.html",
        ctx=ctx,
        perms=perms,
        work_items=work_items,
        item_line_summaries=item_line_summaries,
        item_line_counts=item_line_counts,
        friendly_status=friendly_status,
    )
