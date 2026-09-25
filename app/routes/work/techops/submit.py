"""
TechOps detail-page submit endpoint.

The generic /<work_type_slug>/item/<id>/submit endpoint in
work_items/actions.py is currently BUDGET-only (it asserts
require_budget_work_type and walks line.budget_detail). Until that
endpoint is generalized, TechOps gets its own literal-segment submit
route so the detail-page Submit button works.

Used only when a TechOps draft already has at least one real line (the
detail page hides the Submit button otherwise via perms.can_submit).
The no-services-needed affirmation path goes through the edit form's
Submit action instead, which calls the same _do_submit helper.
"""
from flask import flash, redirect, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from app.models import (
    TechOpsLineDetail,
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
from .create import _do_submit


@work_bp.post("/<event>/<dept>/techops/item/<public_id>/submit")
def techops_request_submit(event: str, dept: str, public_id: str):
    """Submit a DRAFT TechOps request from the detail page."""
    ctx = get_portfolio_context(event, dept, "techops")

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=ctx.portfolio.id,
            is_archived=False,
        )
        .options(
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.service_type),
            joinedload(WorkItem.techops_detail),
        )
        .first()
    )

    detail_url = url_for(
        "work.techops_work_item_detail",
        event=event, dept=dept, public_id=public_id,
    )

    if not work_item:
        flash("TechOps request not found.", "error")
        return redirect(url_for("work.department_home", event=event, dept=dept))

    perms = build_work_item_perms(work_item, ctx)
    if not perms.can_submit:
        flash("You cannot submit this TechOps request.", "error")
        return redirect(detail_url)

    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("Only DRAFT requests can be submitted.", "error")
        return redirect(detail_url)

    user_ctx = get_user_ctx()
    if not _do_submit(work_item, user_ctx):
        return redirect(detail_url)

    flash(
        "TechOps request submitted! TechOps will reach out if any clarifications are needed.",
        "success",
    )
    return redirect(detail_url)
