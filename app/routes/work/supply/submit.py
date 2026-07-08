"""
Supply order submit endpoint.

Registered at the literal /supply/order/<public_id>/submit segment (same
idiom as the rest of the cab), so it takes precedence over any generic
fallback route. Validation runs first via validate_order_for_submit — the
engine's submit_work_item (uses_dispatch=False branch) SILENTLY SKIPS lines
it can't route, so this cab-level gate is the loud failure the requester
actually sees.
"""
from flask import flash, redirect, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    build_work_item_perms,
    get_portfolio_context,
)
from .form_utils import validate_order_for_submit


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/submit")
def supply_order_submit(event: str, dept: str, public_id: str):
    """Submit a DRAFT supply order from the order detail page."""
    ctx = get_portfolio_context(event, dept, "supply")

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=ctx.portfolio.id,
            is_archived=False,
        )
        .options(
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.supply_detail)
                .joinedload(SupplyOrderLineDetail.item),
            joinedload(WorkItem.supply_order_detail),
        )
        .first()
    )

    detail_url_kwargs = dict(event=event, dept=dept, public_id=public_id)

    if not work_item:
        flash("Supply order not found.", "error")
        return redirect(url_for(
            "work.supply_portfolio_landing", event=event, dept=dept,
        ))

    detail_url = url_for("work.supply_order_detail", **detail_url_kwargs)

    perms = build_work_item_perms(work_item, ctx)
    if not perms.can_submit:
        flash("You cannot submit this supply order.", "error")
        return redirect(detail_url)

    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("Only DRAFT orders can be submitted.", "error")
        return redirect(detail_url)

    errors = validate_order_for_submit(work_item)
    if errors:
        for err in errors:
            flash(err, "error")
        return redirect(detail_url)

    user_ctx = get_user_ctx()

    from app.routes.work.helpers.lifecycle import submit_work_item

    submit_work_item(work_item, user_ctx)
    db.session.commit()

    try:
        from app.services.notifications import notify_work_item_submitted
        notify_work_item_submitted(work_item)
        db.session.commit()
    except Exception:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send submission notification for %s", work_item.public_id
        )

    flash("Supply order submitted! It's now with reviewers.", "success")
    return redirect(detail_url)
