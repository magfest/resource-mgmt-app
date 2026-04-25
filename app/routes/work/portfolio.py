"""
Portfolio routes - landing page for department portfolios.

The portfolio_landing handler is registered under two URL patterns:
- Legacy: /<event>/<dept>/budget   (kept so existing links keep working)
- Generic: /<event>/<dept>/<work_type_slug>   (added for multi-work-type support)

Flask's URL matcher prefers literal segments, so /budget hits the legacy
rule and other slugs (e.g., techops, av) hit the generic rule. Both go
through the same handler; the slug is captured into work_type_slug.
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
    get_work_type_by_slug,
    require_portfolio_view,
    build_portfolio_perms,
    compute_portfolio_totals,
    compute_work_item_totals,
    compute_line_status_summary,
    format_currency,
    friendly_status,
)


# Coming-soon copy per non-budget work type code. Used when a portfolio
# landing page is requested for a work type whose UI isn't built yet.
_COMING_SOON_DETAILS = {
    "CONTRACT": {
        "description": "Contract management and vendor agreements",
        "contact_team": "the Business Team",
        "contact_email": "biz@magfest.org",
    },
    "SUPPLY": {
        "description": "Warehouse inventory and supply requests",
        "contact_team": "FestOps",
        "contact_email": "festops@magfest.org",
    },
    "TECHOPS": {
        "description": "Radio assignments, networking, and technical operations support",
        "contact_team": "TechOps",
        "contact_email": "techops@magfest.org",
    },
    "AV": {
        "description": "Audio/visual equipment and AV staffing requests",
        "contact_team": "the AV Team",
        "contact_email": "av@magfest.org",
    },
}


def _render_coming_soon(work_type_slug: str, event: str, dept: str):
    """Render the coming-soon page for a non-budget work type.

    Looks up event and department directly so we don't auto-create a
    portfolio for an unimplemented work type via get_portfolio_context.
    """
    work_type = get_work_type_by_slug(work_type_slug)

    event_cycle = EventCycle.query.filter_by(code=event.upper()).first()
    if not event_cycle:
        abort(404, f"Event cycle not found: {event}")

    department = Department.query.filter_by(code=dept.upper()).first()
    if not department:
        abort(404, f"Department not found: {dept}")

    details = _COMING_SOON_DETAILS.get(work_type.code, {
        "description": "This work type is currently in development.",
        "contact_team": "the team",
        "contact_email": "info@magfest.org",
    })

    return render_page(
        "budget/coming_soon.html",
        event_cycle=event_cycle,
        department=department,
        work_type_name=work_type.name,
        work_type_description=details["description"],
        contact_team=details["contact_team"],
        contact_email=details["contact_email"],
    )


@work_bp.get("/<event>/<dept>/<work_type_slug>")
@work_bp.get("/<event>/<dept>/budget")
def portfolio_landing(event: str, dept: str, work_type_slug: str = "budget"):
    """
    Portfolio landing page.

    For BUDGET: shows the department's budget portfolio (header, totals,
    PRIMARY work item, SUPPLEMENTARY items).

    For other work types: renders coming-soon page (UI not yet built).
    """
    # Branch on slug before touching the portfolio so we don't auto-create
    # a portfolio row for a work type whose UI doesn't exist yet.
    if work_type_slug.lower() != "budget":
        return _render_coming_soon(work_type_slug, event, dept)

    # Build context and check permissions
    ctx = get_portfolio_context(event, dept, work_type_slug)
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
    # Order by created_at ASC to assign sequential numbers (#1, #2, etc.)
    supplementary = WorkItem.query.filter_by(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        is_archived=False,
    ).options(
        selectinload(WorkItem.lines).joinedload(WorkLine.budget_detail),
    ).order_by(WorkItem.created_at.asc()).all()

    # Build a map of supplemental numbers (1-indexed, based on creation order)
    supplementary_numbers = {item.id: idx + 1 for idx, item in enumerate(supplementary)}

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
        supplementary_numbers=supplementary_numbers,
        totals=totals,
        format_currency=format_currency,
        friendly_status=friendly_status,
    )


# ============================================================
# Placeholder routes for future work types
# ============================================================
# These remain because Flask's URL matcher prefers literal segments over
# variables, so /<event>/<dept>/contracts hits these (with their specific
# copy) rather than the generic <work_type_slug> rule. They will be
# removed in PR 4 (cleanup).

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