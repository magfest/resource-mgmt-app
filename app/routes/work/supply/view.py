"""
Supply order detail view — the requester's cart / order view.

Requester-facing: never surfaces prices or costs — no totals are computed
or passed. format_currency IS passed, but solely for the admin-gated audit
log macro (SUBMIT-event snapshots render a cost via format_currency); the
audit section only renders when can_view_audit is true, so requesters
never see it.
"""
from flask import abort, redirect, render_template, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    SupplyItem,
    SupplyOrderLineDetail,
    WorkItem,
    WorkItemAuditEvent,
    WorkLine,
    AUDIT_EVENT_VIEW,
    COMMENT_VISIBILITY_ADMIN,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    _is_approver_for_work_item,
    format_currency,
    friendly_status,
    get_portfolio_context,
    get_unified_audit_events,
    require_work_item_view,
)
from .form_utils import PICKUP_TIME_OPTIONS, validate_order_for_submit
from .order import is_line_kickback_editable


@work_bp.get("/<event>/<dept>/supply/item/<public_id>")
def supply_work_item_detail_redirect(event: str, dept: str, public_id: str):
    """Redirect the generic .../supply/item/<public_id> URL shape to the
    canonical .../supply/order/<public_id> route.

    Registered at the literal /supply/item/... segment, so Flask's URL
    matcher prefers it over BUDGET's generic /<work_type_slug>/item/...
    pattern (mirrors how techops claims its literal item URL at
    techops/view.py:34) -- but unlike TechOps, SUPPLY's canonical detail
    view already lives at a different path (/supply/order/...), so a
    redirect is sufficient instead of rendering here directly. Supply
    reviewer-queue links (approvals/_queue_table.html, approvals/dashboard.html)
    are built via url_for('work.work_item_detail', work_type_slug='supply', ...),
    which resolves to this URL string.
    """
    return redirect(
        url_for("work.supply_order_detail", event=event, dept=dept, public_id=public_id),
        code=302,
    )


@work_bp.get("/<event>/<dept>/supply/order/<public_id>")
def supply_order_detail(event: str, dept: str, public_id: str):
    """View a supply order (the cart/order detail).

    Registered at the literal /supply/order/... segment, so Flask's URL
    matcher prefers it over BUDGET's generic /<work_type_slug>/item/...
    pattern.
    """
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
                .joinedload(SupplyOrderLineDetail.item)
                .joinedload(SupplyItem.category),
            selectinload(WorkItem.comments),
            joinedload(WorkItem.supply_order_detail),
        )
        .first()
    )

    if not work_item:
        abort(404, f"Supply order not found: {public_id}")

    perms = require_work_item_view(work_item, ctx)
    user_ctx = get_user_ctx()

    # Edit widgets (line update/delete, delivery-details form) only render
    # for a DRAFT order and a viewer who can edit it — mirrors catalog.py's
    # own can_edit gate for mutations on this cab.
    can_edit = work_item.status == WORK_ITEM_STATUS_DRAFT and perms.can_edit

    # Log a VIEW event when a non-draft order is opened by someone other
    # than the requester (mirrors the BUDGET/TechOps detail-view pattern).
    is_requester = work_item.created_by_user_id == user_ctx.user_id
    if work_item.status != WORK_ITEM_STATUS_DRAFT and not is_requester:
        db.session.add(WorkItemAuditEvent(
            work_item_id=work_item.id,
            event_type=AUDIT_EVENT_VIEW,
            created_by_user_id=user_ctx.user_id,
        ))
        db.session.commit()

    # Filter admin-only comments away from non-admin viewers
    comments = list(work_item.comments)
    if not perms.is_worktype_admin:
        comments = [c for c in comments if c.visibility != COMMENT_VISIBILITY_ADMIN]

    is_approver_for_item = _is_approver_for_work_item(work_item, user_ctx)
    can_add_comment = perms.is_worktype_admin or is_approver_for_item

    can_view_audit = user_ctx.is_super_admin or perms.is_worktype_admin
    audit_events = get_unified_audit_events(work_item) if can_view_audit else []

    lines = sorted(work_item.lines, key=lambda line: line.line_number)

    # Kicked-back lines re-open their edit widgets even on a non-DRAFT
    # order. Same predicate as the supply_line_update POST gate (imported
    # from order.py) so the UI and the route gate can't drift apart.
    kickback_editable_line_numbers = {
        line.line_number
        for line in lines
        if is_line_kickback_editable(line, work_item, ctx, user_ctx)
    }

    # Pre-submit validation checklist — only meaningful while still DRAFT
    # (a submitted/finalized order has nothing left to validate).
    submit_errors = (
        validate_order_for_submit(work_item)
        if work_item.status == WORK_ITEM_STATUS_DRAFT
        else []
    )

    return render_template(
        "supply/order_detail.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        order_detail=work_item.supply_order_detail,
        pickup_time_options=PICKUP_TIME_OPTIONS,
        lines=lines,
        can_edit=can_edit,
        kickback_editable_line_numbers=kickback_editable_line_numbers,
        submit_errors=submit_errors,
        friendly_status=friendly_status,
        # Admin-chrome only: consumed by the shared audit_log macro for
        # SUBMIT-event snapshots (total_requested_cents). The requester-
        # facing cart never renders currency.
        format_currency=format_currency,
        filtered_comments=comments,
        can_add_comment=can_add_comment,
        audit_events=audit_events,
        can_view_audit=can_view_audit,
        user_ctx=user_ctx,
    )
