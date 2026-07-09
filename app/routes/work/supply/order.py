"""
Supply order creation — starting a new draft supply order (the cart) —
plus cart-editing routes (line update/delete, pickup details save).

Supply is a repeat-ordering work type: every order is PRIMARY and a
department can place unlimited independent orders per event, so creation
is gated on require_portfolio_edit + can_edit rather than the engine's
perms.can_create_primary (which locks after the first PRIMARY exists per
portfolio — see the matching comment in portfolio.py).
"""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
)
from app.routes import get_user_ctx
from app.routes.approvals.helpers import can_respond_to_work_item
from .. import work_bp
from ..helpers import (
    generate_public_id_for_portfolio,
    get_portfolio_context,
    require_portfolio_edit,
    require_work_item_view,
)
from .form_utils import PICKUP_TIME_OPTIONS


def _load_order(event: str, dept: str, public_id: str):
    """Load a supply order (any status) with what cart-editing needs.

    Mirrors catalog.py's _load_order gate idiom so 404/permission behavior
    stays identical across the cab; also eager-loads the order-level
    pickup detail for the details-save route.
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
                .joinedload(SupplyOrderLineDetail.item),
            joinedload(WorkItem.supply_order_detail),
        )
        .first()
    )

    if not work_item:
        abort(404, f"Supply order not found: {public_id}")

    perms = require_work_item_view(work_item, ctx)
    return work_item, ctx, perms


@work_bp.post("/<event>/<dept>/supply/order/new")
def supply_order_new(event: str, dept: str):
    """Start a new draft supply order (the cart) and enter the catalog."""
    ctx = get_portfolio_context(event, dept, "supply")
    require_portfolio_edit(ctx)

    user_ctx = get_user_ctx()

    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id_for_portfolio(ctx.portfolio),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.flush()
    db.session.add(SupplyOrderDetail(
        work_item_id=work_item.id,
        created_by_user_id=user_ctx.user_id,
    ))
    db.session.commit()

    return redirect(url_for(
        "work.supply_catalog", event=event, dept=dept,
        public_id=work_item.public_id,
    ))


def _find_line(work_item: WorkItem, line_number: int):
    """Find a line by its public line_number within an already-loaded order."""
    return next(
        (l for l in work_item.lines if l.line_number == line_number), None
    )


def is_line_kickback_editable(line: WorkLine, work_item: WorkItem, ctx, user_ctx) -> bool:
    """True if a kicked-back line may be edited by this user even though
    the order is no longer DRAFT.

    Single source of truth for the kickback exception — used both by the
    supply_line_update POST gate below and by view.py to decide which rows
    render their edit widgets, so the UI and the route gate can't drift.

    Mirrors the predicate the generic per-line respond flow uses in
    app/routes/approvals/reviews.py's line_review() (`can_respond`): the
    same three conditions (needs_requester_action, a NEEDS_ADJUSTMENT-ish
    status, and requester/edit permission via can_respond_to_work_item)
    gate whether the requester may act on this specific line. We narrow
    the status check to NEEDS_ADJUSTMENT only (not NEEDS_INFO) because
    that's the status that means "fix this line's fields", per the brief.
    """
    return (
        line.needs_requester_action
        and line.status == WORK_LINE_STATUS_NEEDS_ADJUSTMENT
        and can_respond_to_work_item(work_item, ctx, user_ctx)
    )


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/lines/<int:line_number>/update")
def supply_line_update(event: str, dept: str, public_id: str, line_number: int):
    """Update a line's quantity/notes.

    Gate: normally DRAFT + can_edit — EXCEPT a kicked-back line, per
    is_line_kickback_editable() above.
    """
    work_item, ctx, perms = _load_order(event, dept, public_id)
    detail_url = url_for(
        "work.supply_order_detail", event=event, dept=dept, public_id=public_id,
    )

    line = _find_line(work_item, line_number)
    if not line or not line.supply_detail:
        abort(404, f"Line not found: {line_number}")

    user_ctx = get_user_ctx()
    is_kickback_editable = is_line_kickback_editable(line, work_item, ctx, user_ctx)

    if not perms.can_edit and not is_kickback_editable:
        abort(403, "You do not have permission to edit this line.")

    quantity = request.form.get("quantity", type=int)
    if quantity is None or quantity < 1:
        flash("Quantity must be a whole number of at least 1.", "error")
        return redirect(detail_url)

    # Normalize textarea/text-input CRLF to LF before measuring/storing.
    notes = request.form.get("notes", "").replace("\r\n", "\n").strip()

    item = line.supply_detail.item
    if item and item.notes_required and not notes:
        flash(f"Notes are required for {item.item_name}.", "error")
        return redirect(detail_url)

    line.supply_detail.quantity_requested = quantity
    line.supply_detail.requester_notes = notes or None
    db.session.commit()

    flash("Line updated.", "success")
    return redirect(detail_url)


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/lines/<int:line_number>/delete")
def supply_line_delete(event: str, dept: str, public_id: str, line_number: int):
    """Remove a line from a draft order.

    Draft-only (no kickback exception — a kicked-back line gets fixed via
    update, not removed). Remaining lines keep their existing line_number;
    numbers are never reassigned.
    """
    work_item, ctx, perms = _load_order(event, dept, public_id)
    detail_url = url_for(
        "work.supply_order_detail", event=event, dept=dept, public_id=public_id,
    )

    if not perms.can_edit:
        abort(403, "You do not have permission to edit this supply order.")

    line = _find_line(work_item, line_number)
    if not line:
        abort(404, f"Line not found: {line_number}")

    item = line.supply_detail.item if line.supply_detail else None
    item_name = item.item_name if item else f"Line {line_number}"

    # SupplyOrderLineDetail cascades (all, delete-orphan) via the
    # WorkLine.supply_detail backref, so deleting the line is sufficient.
    db.session.delete(line)
    db.session.commit()

    flash(f"Removed {item_name} from your order.", "success")
    return redirect(detail_url)


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/details")
def supply_order_details_save(event: str, dept: str, public_id: str):
    """Save order-level pickup details (pickup time, notes)."""
    work_item, ctx, perms = _load_order(event, dept, public_id)
    detail_url = url_for(
        "work.supply_order_detail", event=event, dept=dept, public_id=public_id,
    )

    if not perms.can_edit:
        abort(403, "You do not have permission to edit this supply order.")

    # Empty is allowed while drafting; submit validation enforces a choice.
    # Anything non-empty must be a known option (form tampering guard).
    pickup_time = (request.form.get("pickup_time") or "").strip()
    if pickup_time and pickup_time not in PICKUP_TIME_OPTIONS:
        flash("Choose a pickup time from the list.", "error")
        return redirect(detail_url)

    additional_notes = (
        request.form.get("additional_notes", "").replace("\r\n", "\n").strip()
    )

    user_ctx = get_user_ctx()
    order_detail = work_item.supply_order_detail
    if order_detail is None:
        order_detail = SupplyOrderDetail(
            work_item_id=work_item.id,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(order_detail)

    order_detail.pickup_time = pickup_time or None
    order_detail.additional_notes = additional_notes or None
    order_detail.updated_by_user_id = user_ctx.user_id

    db.session.commit()

    flash("Pickup details saved.", "success")
    return redirect(detail_url)
