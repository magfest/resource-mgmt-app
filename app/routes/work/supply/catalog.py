"""
Supply catalog — browse items and add them to a draft order (the cart).

Requester-facing: never surfaces prices/costs (no unit_cost_cents is passed
to the template). Every add creates a NEW WorkLine, even for an item
already in the cart — intentionally never merged/deduped, since requesters
distinguish duplicate adds via per-line notes (e.g. two spools of gaffer
tape, one noted "for tech booth", one "for panels").
"""
from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    SupplyCategory,
    SupplyItem,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
)
from .. import work_bp
from ..helpers import (
    get_portfolio_context,
    require_portfolio_view,
    require_work_item_view,
)


def _load_order(event: str, dept: str, public_id: str):
    """Load a supply order (any status) with what the catalog needs.

    Shared by both the GET catalog view and the POST add-to-cart handler so
    the 404/permission behavior stays identical between them.
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
        )
        .first()
    )

    if not work_item:
        abort(404, f"Supply order not found: {public_id}")

    perms = require_work_item_view(work_item, ctx)
    return work_item, ctx, perms


def _catalog_items(q: str):
    """Shared category/item/popular-strip queries for both the in-order
    catalog and the standalone browse view (Task 16)."""
    categories = (
        SupplyCategory.query
        .filter_by(is_active=True)
        .order_by(SupplyCategory.sort_order.asc().nulls_last(), SupplyCategory.name.asc())
        .all()
    )

    items_query = SupplyItem.query.filter_by(is_active=True).options(
        joinedload(SupplyItem.category)
    )
    if q:
        like = f"%{q}%"
        items_query = items_query.filter(
            db.or_(
                SupplyItem.item_name.ilike(like),
                SupplyItem.notes.ilike(like),
            )
        )
    items = items_query.order_by(
        SupplyItem.sort_order.asc().nulls_last(), SupplyItem.item_name.asc()
    ).all()

    items_by_category: dict[int, list[SupplyItem]] = {}
    for item in items:
        items_by_category.setdefault(item.category_id, []).append(item)

    # Popular strip is skipped entirely while searching.
    popular_items = []
    if not q:
        popular_items = [
            item for item in
            SupplyItem.query.filter_by(
                is_active=True, is_popular=True,
            ).order_by(SupplyItem.item_name.asc()).all()
        ]

    return categories, items_by_category, popular_items


@work_bp.get("/<event>/<dept>/supply/order/<public_id>/catalog")
def supply_catalog(event: str, dept: str, public_id: str):
    """Browse the item catalog and add items to this draft order."""
    work_item, ctx, perms = _load_order(event, dept, public_id)

    # Add-forms only render for a DRAFT order and a viewer who can edit it
    # (mirrors order.py's own can_edit gate for mutations on this cab).
    can_add = work_item.status == WORK_ITEM_STATUS_DRAFT and perms.can_edit

    # Per-item "already in this order" summary for the badge. Duplicate
    # adds are separate lines by design (see module docstring), so the
    # badge shows both the line count and the summed quantity.
    in_cart: dict[int, dict[str, int]] = {}
    for line in work_item.lines:
        d = line.supply_detail
        if d is None:
            continue
        entry = in_cart.setdefault(d.item_id, {"lines": 0, "qty": 0})
        entry["lines"] += 1
        entry["qty"] += d.quantity_requested or 0

    q = request.args.get("q", "").strip()
    categories, items_by_category, popular_items = _catalog_items(q)

    def catalog_url(**kwargs):
        return url_for(
            "work.supply_catalog", event=event, dept=dept,
            public_id=public_id, **kwargs,
        )

    def item_detail_url(item_id):
        return url_for(
            "work.supply_item_detail", event=event, dept=dept,
            item_id=item_id, order=public_id,
        )

    return render_template(
        "supply/catalog.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        can_add=can_add,
        categories=categories,
        items_by_category=items_by_category,
        popular_items=popular_items,
        q=q,
        cart_count=len(work_item.lines),
        catalog_url=catalog_url,
        item_detail_url=item_detail_url,
        in_cart=in_cart,
    )


@work_bp.get("/<event>/<dept>/supply/catalog")
def supply_catalog_browse(event: str, dept: str):
    """Standalone catalog browse — no order attached, view-only.

    Lets any viewer (not just editors) learn what's in the catalog, what
    units mean, what's limited, and what must be returned, without first
    starting an order. Reuses supply/catalog.html with work_item=None and
    can_add=False so add-to-cart forms and the cart strip are hidden;
    search, jump-nav, and item name links to the detail page (Task 17)
    all still work.
    """
    ctx = get_portfolio_context(event, dept, "supply")
    perms = require_portfolio_view(ctx)

    q = request.args.get("q", "").strip()
    categories, items_by_category, popular_items = _catalog_items(q)

    def catalog_url(**kwargs):
        return url_for("work.supply_catalog_browse", event=event, dept=dept, **kwargs)

    def item_detail_url(item_id):
        return url_for(
            "work.supply_item_detail", event=event, dept=dept, item_id=item_id,
        )

    return render_template(
        "supply/catalog.html",
        ctx=ctx,
        perms=perms,
        work_item=None,
        can_add=False,
        categories=categories,
        items_by_category=items_by_category,
        popular_items=popular_items,
        q=q,
        cart_count=0,
        catalog_url=catalog_url,
        item_detail_url=item_detail_url,
        in_cart={},
    )


@work_bp.get("/<event>/<dept>/supply/catalog/item/<int:item_id>")
def supply_item_detail(event: str, dept: str, item_id: int):
    """Standalone item detail page (replaces the rejected details popover).

    Optional ?order=<public_id> resolves that work item within this
    portfolio and, when it is an editable DRAFT, shows the add-to-cart
    form (same can_add gate as the in-order catalog); otherwise the page
    is read-only.
    """
    ctx = get_portfolio_context(event, dept, "supply")
    perms = require_portfolio_view(ctx)

    item = (
        SupplyItem.query
        .filter_by(id=item_id, is_active=True)
        .options(joinedload(SupplyItem.category))
        .first()
    )
    if not item:
        abort(404, f"Supply catalog item not found: {item_id}")

    order_public_id = request.args.get("order", "").strip()
    work_item = None
    can_add = False
    if order_public_id:
        work_item, _order_ctx, order_perms = _load_order(event, dept, order_public_id)
        can_add = work_item.status == WORK_ITEM_STATUS_DRAFT and order_perms.can_edit

    if work_item is not None and can_add:
        back_url = url_for(
            "work.supply_catalog", event=event, dept=dept,
            public_id=work_item.public_id, _anchor=f"item-{item.id}",
        )
    else:
        back_url = url_for(
            "work.supply_catalog_browse", event=event, dept=dept,
            _anchor=f"item-{item.id}",
        )

    return render_template(
        "supply/item_detail.html",
        ctx=ctx,
        perms=perms,
        item=item,
        work_item=work_item,
        can_add=can_add,
        back_url=back_url,
    )


@work_bp.post("/<event>/<dept>/supply/order/<public_id>/lines/add")
def supply_line_add(event: str, dept: str, public_id: str):
    """Add one catalog item to the draft order as a NEW line (always —
    duplicate items are intentional; notes distinguish them)."""
    work_item, ctx, perms = _load_order(event, dept, public_id)

    if work_item.status != WORK_ITEM_STATUS_DRAFT or not perms.can_edit:
        abort(403, "You do not have permission to edit this supply order.")

    catalog_url = url_for(
        "work.supply_catalog", event=event, dept=dept, public_id=public_id,
    )

    item_id = request.form.get("item_id", type=int)
    item = None
    if item_id is not None:
        item = SupplyItem.query.filter_by(id=item_id, is_active=True).first()
    if item is None:
        flash("That catalog item could not be found.", "error")
        return redirect(catalog_url)

    anchor_url = url_for(
        "work.supply_catalog", event=event, dept=dept, public_id=public_id,
        _anchor=f"item-{item.id}",
    )

    quantity = request.form.get("quantity", type=int)
    if quantity is None or quantity < 1:
        flash("Quantity must be a whole number of at least 1.", "error")
        return redirect(anchor_url)

    # Normalize textarea/text-input CRLF to LF before measuring/storing.
    notes = request.form.get("notes", "").replace("\r\n", "\n").strip()

    if item.notes_required and not notes:
        flash(f"Notes are required for {item.item_name}.", "error")
        return redirect(anchor_url)

    line_number = 1 + max(
        (l.line_number for l in work_item.lines), default=0
    )
    line = WorkLine(line_number=line_number, status=WORK_LINE_STATUS_PENDING)
    work_item.lines.append(line)
    db.session.flush()

    db.session.add(SupplyOrderLineDetail(
        work_line_id=line.id,
        item_id=item.id,
        quantity_requested=quantity,
        requester_notes=notes or None,
    ))
    db.session.commit()

    flash(f"Added {item.item_name} ×{quantity} to your order.", "success")
    return redirect(anchor_url)
