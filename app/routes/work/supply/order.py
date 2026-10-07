"""
Supply order creation — starting a new draft supply order (the cart) —
plus cart editing: the order page's single save route, and per-line update
for the catalog, item page, and kicked-back lines.

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
    SupplyOrderSpace,
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
from app.models.supply import QUANTITY_CONFIDENCE_OPTIONS
from .form_utils import PICKUP_TIME_OPTIONS
from .spaces import order_space_choices
from .submit import submit_order


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


# supply_line_update's return_to field names a page, never a URL, so a crafted
# form cannot send the requester off the site after a save.
def _update_return_url(return_to: str, event: str, dept: str, public_id: str, item_id: int) -> str:
    if return_to == "catalog":
        return url_for("work.supply_catalog", event=event, dept=dept,
                       public_id=public_id, _anchor=f"item-{item_id}")
    if return_to == "item":
        return url_for("work.supply_item_detail", event=event, dept=dept,
                       item_id=item_id, order=public_id)
    return url_for("work.supply_order_detail", event=event, dept=dept, public_id=public_id)


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/lines/<int:line_number>/update")
def supply_line_update(event: str, dept: str, public_id: str, line_number: int):
    """Update a line's quantity, quantity confidence, and notes.

    Gate: normally DRAFT + can_edit — EXCEPT a kicked-back line, per
    is_line_kickback_editable() above. Redirects per return_to; see
    _update_return_url.
    """
    work_item, ctx, perms = _load_order(event, dept, public_id)
    line = _find_line(work_item, line_number)
    if not line or not line.supply_detail:
        abort(404, f"Line not found: {line_number}")

    user_ctx = get_user_ctx()
    is_kickback_editable = is_line_kickback_editable(line, work_item, ctx, user_ctx)

    if not perms.can_edit and not is_kickback_editable:
        abort(403, "You do not have permission to edit this line.")

    back_url = _update_return_url(
        request.form.get("return_to", ""), event, dept, public_id,
        line.supply_detail.item_id,
    )

    # supply_line_add reuses the highest line number after a delete, so a stale
    # page can name a line that now holds another item. Refuse, never overwrite.
    posted_item_id = request.form.get("item_id", type=int)
    if posted_item_id is not None and posted_item_id != line.supply_detail.item_id:
        flash("That line changed after this page loaded. Nothing was saved; please try again.", "error")
        return redirect(_update_return_url(
            request.form.get("return_to", ""), event, dept, public_id, posted_item_id,
        ))

    quantity = request.form.get("quantity", type=int)
    if quantity is None or quantity < 1:
        flash("Quantity must be a whole number of at least 1.", "error")
        return redirect(back_url)

    # Normalize textarea/text-input CRLF to LF before measuring/storing.
    notes = request.form.get("notes", "").replace("\r\n", "\n").strip()

    item = line.supply_detail.item
    if item and item.notes_required and not notes:
        flash(f"Notes are required for {item.item_name}.", "error")
        return redirect(back_url)

    # Only the order page posts this field. The catalog and item pages post
    # here without it, and must not clear an answer given on the order page.
    has_confidence = "quantity_confidence" in request.form
    confidence = (request.form.get("quantity_confidence") or "").strip()
    if confidence and confidence not in QUANTITY_CONFIDENCE_OPTIONS:
        flash("Choose how sure you are of the quantity from the list.", "error")
        return redirect(back_url)

    line.supply_detail.quantity_requested = quantity
    line.supply_detail.requester_notes = notes or None
    if has_confidence:
        line.supply_detail.quantity_confidence = confidence or None
    db.session.commit()

    flash("Line updated.", "success")
    return redirect(back_url)


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/save")
def supply_order_save(event: str, dept: str, public_id: str):
    """Save the whole draft order page, then act on the button pressed.

    The page is one form so an edit in one row is never lost by saving
    another. `action` is "save", "submit" (save, then submit), or "catalog"
    (save, then add more items); `remove_line` saves, then removes that line.
    Any invalid value refuses the whole save, so nothing is half-written.
    Kicked-back lines on a submitted order still use supply_line_update.
    """
    work_item, ctx, perms = _load_order(event, dept, public_id)
    detail_url = url_for(
        "work.supply_order_detail", event=event, dept=dept, public_id=public_id,
    )

    # A page left open in another tab after submit lands here; say so
    # instead of a permission error the requester cannot explain.
    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        flash("This order was already submitted, so the changes on that page "
              "were not saved.", "error")
        return redirect(detail_url)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this supply order.")

    remove_line = request.form.get("remove_line", type=int)

    # Validate everything before writing anything.
    line_updates = []
    for line in work_item.lines:
        detail = line.supply_detail
        prefix = f"line-{line.line_number}-"
        # A line added in another tab after this page loaded is not on the
        # form; leave it alone rather than treat it as cleared.
        if detail is None or f"{prefix}qty" not in request.form:
            continue
        # Line numbers are reused after a delete, so a stale page can name a
        # line that now holds another item. Refuse, never overwrite.
        if request.form.get(f"{prefix}item", type=int) != detail.item_id:
            flash("Your order changed after this page loaded. Nothing was "
                  "saved; please check it and try again.", "error")
            return redirect(detail_url)
        if line.line_number == remove_line:
            continue
        quantity = request.form.get(f"{prefix}qty", type=int)
        if quantity is None or quantity < 1:
            flash(f"Line {line.line_number}: quantity must be a whole number "
                  "of at least 1. Nothing was saved.", "error")
            return redirect(detail_url)
        confidence = (request.form.get(f"{prefix}confidence") or "").strip()
        if confidence and confidence not in QUANTITY_CONFIDENCE_OPTIONS:
            flash("Choose how sure you are of each quantity from the list.", "error")
            return redirect(detail_url)
        # Blank required notes are saved; the submit check reports them, so
        # one missing note does not throw away every other edit.
        notes = request.form.get(f"{prefix}notes", "").replace("\r\n", "\n").strip()
        line_updates.append((detail, quantity, confidence or None, notes or None))

    pickup_time = (request.form.get("pickup_time") or "").strip()
    if pickup_time and pickup_time not in PICKUP_TIME_OPTIONS:
        flash("Choose a pickup time from the list.", "error")
        return redirect(detail_url)

    additional_notes = (
        request.form.get("additional_notes", "").replace("\r\n", "\n").strip()
    )

    # Checked boxes and the "add another space" picker share the field name.
    # The picker posts "" when left on its placeholder.
    space_labels = order_space_choices(work_item)["labels"]
    space_ids = set()
    for raw in request.form.getlist("space_ids"):
        if not raw:
            continue
        try:
            space_id = int(raw)
        except ValueError:
            space_id = None
        if space_id not in space_labels:
            flash("Choose spaces from the list.", "error")
            return redirect(detail_url)
        space_ids.add(space_id)

    user_ctx = get_user_ctx()

    for detail, quantity, confidence, notes in line_updates:
        detail.quantity_requested = quantity
        detail.quantity_confidence = confidence
        detail.requester_notes = notes

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

    for row in list(work_item.supply_order_spaces):
        if row.space_id not in space_ids:
            work_item.supply_order_spaces.remove(row)
    held_ids = {row.space_id for row in work_item.supply_order_spaces}
    for space_id in sorted(space_ids - held_ids):
        work_item.supply_order_spaces.append(SupplyOrderSpace(
            space_id=space_id,
            space_label=space_labels[space_id],
            created_by_user_id=user_ctx.user_id,
        ))

    removed = _find_line(work_item, remove_line) if remove_line is not None else None
    removed_name = None
    if removed is not None:
        item = removed.supply_detail.item if removed.supply_detail else None
        removed_name = item.item_name if item else f"Line {removed.line_number}"
        # Remaining lines keep their numbers; numbers are never reassigned.
        # The line's supply detail goes with it (delete-orphan cascade).
        work_item.lines.remove(removed)

    db.session.commit()

    action = request.form.get("action", "save")
    if removed_name:
        flash(f"Removed {removed_name} from your order.", "success")
    elif action == "catalog":
        return redirect(url_for(
            "work.supply_catalog", event=event, dept=dept, public_id=public_id,
        ))
    elif action == "submit":
        # submit_order reports an empty order by name, so no can_submit
        # check here; edit rights were checked above.
        errors = submit_order(work_item)
        if errors:
            flash("Your changes were saved, but the order was not submitted yet.", "error")
            for err in errors:
                flash(err, "error")
            return redirect(detail_url)
        flash("Supply order submitted! It's now with reviewers.", "success")
    else:
        flash("Order saved.", "success")
    return redirect(detail_url)
