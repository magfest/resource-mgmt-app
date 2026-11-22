"""
Supply admin-final routes — FestOps queue + finalize screen.

SUPPLY runs uses_dispatch=False + has_admin_final=True, so an order that
clears review (all APPROVAL_GROUP reviews decided, or none — the engine's
try_auto_finalize deliberately no-ops while has_admin_final is True) sits in
SUBMITTED until a FestOps admin finalizes it here. This is the terminal
stage: the admin sets the authoritative approved quantity per line and locks
the order to FINALIZED.

Lives on the shared work_bp at literal /admin/supply/... segments (same idiom
as app/routes/work/techops/admin.py) so Flask's matcher prefers these over
the generic <work_type_slug> fallback rule. Access is gated to SUPPLY
worktype admins (or super-admins) by _require_supply_admin.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    Department,
    EventCycle,
    SupplyItem,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WorkLineAuditEvent,
    WorkItemAuditEvent,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    AUDIT_EVENT_ADMIN_FINAL,
    AUDIT_EVENT_FINALIZE,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
)
from app.routes import get_user_ctx
from app.routes.approvals.helpers import (
    get_active_departments,
    get_active_event_cycles,
)
from app.routes.work.helpers import (
    format_currency,
    friendly_status,
    is_worktype_admin,
)
from app.services.notifications import enqueue_savepoint, notify_supply_finalized
from .. import work_bp
from .admin_lines import (
    SupplyAdminError,
    admin_add_line,
    admin_change_requested_quantity,
    admin_item_choices,
    admin_remove_line,
    normalize_reason,
    parse_admin_quantity,
    require_editable_order,
)
from .form_utils import MAX_APPROVED_QUANTITY
from .line_state import (
    build_finalize_changes, is_line_removed, is_order_closed, reviewer_quantity_label,
)
from .order_lifecycle import close_order, reopen_order

_PER_PAGE = 25


def _require_supply_admin(user_ctx) -> WorkType:
    """Resolve the SUPPLY WorkType row and gate to admins of it."""
    supply_wt = WorkType.query.filter_by(code="SUPPLY").first()
    if not supply_wt:
        abort(404, "Supply work type not configured.")
    if not is_worktype_admin(user_ctx, supply_wt.id):
        abort(403, "Supply admin access required.")
    return supply_wt


@work_bp.get("/admin/supply/")
def supply_admin_home():
    """Supply admin landing page — SUPPLY worktype admins + super admins.

    Mirrors admin_final.budget_admin_home's purpose (a shaped landing page
    of quick links plus one cheap headline count) but scoped to what SUPPLY
    actually has: no dispatch stage and no per-event department-progress
    grid (that's BUDGET-specific machinery this work type doesn't use).
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)

    submitted_count = (
        WorkItem.query
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
        .filter(WorkItem.status == WORK_ITEM_STATUS_SUBMITTED)
        .filter(WorkItem.is_archived == False)  # noqa: E712
        .count()
    )

    return render_template(
        "supply/admin_home.html",
        user_ctx=user_ctx,
        submitted_count=submitted_count,
    )


@work_bp.get("/admin/supply/orders/")
def supply_all_orders():
    """Cross-department admin view of every SUPPLY order.

    Mirrors techops_all_requests's shape (app/routes/work/techops/admin.py)
    but SUPPLY-shaped: no monetary column, an item-mix summary (top item
    names by line count) instead of a service-mix summary, and a pickup-time
    column since pickup timing matters more for warehouse orders than it
    does for TechOps requests.
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)

    search_query = request.args.get("q", "").strip()
    event_code = request.args.get("event", "").strip()
    dept_code = request.args.get("dept", "").strip()
    status_filter = request.args.get("status", "").strip()
    page = request.args.get("page", 1, type=int)

    query = (
        WorkItem.query
        .filter(WorkItem.is_archived == False)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .join(EventCycle, WorkPortfolio.event_cycle_id == EventCycle.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
        .options(
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.supply_order_detail),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.supply_detail)
                .joinedload(SupplyOrderLineDetail.item),
        )
    )

    if event_code:
        query = query.filter(EventCycle.code == event_code.upper())
    if dept_code:
        query = query.filter(Department.code == dept_code.upper())
    if status_filter:
        query = query.filter(WorkItem.status == status_filter.upper())
    if search_query:
        pattern = f"%{search_query}%"
        query = query.filter(
            db.or_(
                WorkItem.public_id.ilike(pattern),
                Department.name.ilike(pattern),
                Department.code.ilike(pattern),
            )
        )

    query = query.order_by(WorkItem.updated_at.desc())
    pagination = query.paginate(page=page, per_page=_PER_PAGE, error_out=False)

    orders_data = []
    for wi in pagination.items:
        portfolio = wi.portfolio
        # Item-mix summary per order: ordered Counter of item names so
        # admins can scan "this dept ordered 3 Sharpies + 1 tape roll"
        # at a glance without opening each order.
        item_counts: Counter[str] = Counter()
        for line in wi.lines:
            d = line.supply_detail
            if d and d.item:
                item_counts[d.item.item_name] += 1

        # Stable sorted list of (name, count) tuples for template rendering.
        item_mix = sorted(
            item_counts.items(),
            key=lambda kv: (-kv[1], kv[0]),
        )

        order_detail = wi.supply_order_detail
        orders_data.append({
            "work_item": wi,
            "portfolio": portfolio,
            "event_cycle": portfolio.event_cycle,
            "department": portfolio.department,
            "line_count": len(wi.lines),
            "item_mix": item_mix,
            "pickup_time": order_detail.pickup_time if order_detail else None,
            # Only a closed order differs from the stored status.
            "display_status": "CLOSED" if is_order_closed(wi) else wi.status,
        })

    event_cycles = get_active_event_cycles()
    departments = get_active_departments()

    # SUPPLY lifecycle skips AWAITING_DISPATCH (uses_dispatch=False, same
    # as TechOps) and has no item-level NEEDS_INFO status of its own —
    # kickbacks live at the line level (needs_requester_action), not as a
    # WorkItem status here.
    statuses = [
        ("DRAFT", "Draft"),
        ("SUBMITTED", "Under Review"),
        ("FINALIZED", "Finalized"),
    ]

    return render_template(
        "supply/all_orders.html",
        user_ctx=user_ctx,
        orders_data=orders_data,
        pagination=pagination,
        event_cycles=event_cycles,
        departments=departments,
        statuses=statuses,
        selected_event=event_code,
        selected_dept=dept_code,
        selected_status=status_filter,
        search_query=search_query,
        friendly_status=friendly_status,
    )


def _load_supply_order(public_id: str, supply_wt: WorkType) -> WorkItem:
    """Fetch a SUPPLY order by public_id with lines/details/reviews eager-loaded."""
    work_item = (
        WorkItem.query
        .filter_by(public_id=public_id, is_archived=False)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
        .options(
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.supply_order_detail),
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.supply_detail)
                .joinedload(SupplyOrderLineDetail.item)
                .joinedload(SupplyItem.category),
        )
        .first()
    )
    if not work_item:
        abort(404, f"Supply order not found: {public_id}")
    return work_item


def _lock_supply_order(public_id: str, supply_wt: WorkType) -> WorkItem:
    """Lock the order row, then read the order and its lines again under the lock.

    Every Supply admin write takes this lock first, so the re-read sees any
    edit, removal or finalize committed by a request that held it earlier.
    """
    work_item = _load_supply_order(public_id, supply_wt)
    db.session.execute(
        select(WorkItem.id).where(WorkItem.id == work_item.id).with_for_update()
    )
    db.session.expire_all()
    return _load_supply_order(public_id, supply_wt)


def _reviews_by_line(line_ids: list[int], stage: str) -> dict[int, WorkLineReview]:
    """Map work_line_id -> its review at `stage` (one per line per stage)."""
    if not line_ids:
        return {}
    reviews = WorkLineReview.query.filter(
        WorkLineReview.work_line_id.in_(line_ids),
        WorkLineReview.stage == stage,
    ).all()
    return {r.work_line_id: r for r in reviews}


def _ag_reviews_by_line(line_ids: list[int]) -> dict[int, WorkLineReview]:
    return _reviews_by_line(line_ids, REVIEW_STAGE_APPROVAL_GROUP)


def _ready_counts(lines, ag_reviews) -> tuple[int, int, bool]:
    """(decided, total, ready) over live lines that have an APPROVAL_GROUP review.

    Ready means at least one such line and every one APPROVED or REJECTED.
    Removed lines are left out; they never block finalize.
    """
    total = 0
    decided = 0
    for line in lines:
        if is_line_removed(line.supply_detail):
            continue
        review = ag_reviews.get(line.id)
        if review is None:
            continue
        total += 1
        if review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED):
            decided += 1
    return decided, total, total > 0 and decided == total


def _submitted_supply_orders(supply_wt: WorkType, event_cycle_id: int | None = None):
    """SUBMITTED, unarchived Supply orders, optionally limited to one event."""
    query = (
        WorkItem.query
        .filter(
            WorkItem.is_archived == False,  # noqa: E712
            WorkItem.status == WORK_ITEM_STATUS_SUBMITTED,
        )
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
    )
    if event_cycle_id is not None:
        query = query.filter(WorkPortfolio.event_cycle_id == event_cycle_id)
    return query


def _queue_sort_key(order: WorkItem):
    return (order.submitted_at is None, order.submitted_at or order.created_at)


def next_ready_order(event_cycle_id: int, exclude_id: int) -> WorkItem | None:
    """Oldest-submitted ready order in the event with no kicked-back line."""
    supply_wt = WorkType.query.filter_by(code="SUPPLY").first()
    if supply_wt is None:
        return None
    orders = (
        _submitted_supply_orders(supply_wt, event_cycle_id)
        .filter(WorkItem.id != exclude_id)
        .options(selectinload(WorkItem.lines).joinedload(WorkLine.supply_detail))
        .all()
    )
    ag_reviews = _ag_reviews_by_line([l.id for o in orders for l in o.lines])
    for order in sorted(orders, key=_queue_sort_key):
        if not _ready_counts(order.lines, ag_reviews)[2]:
            continue
        if any(_is_line_kicked_back(l, ag_reviews.get(l.id)) for l in order.lines):
            continue
        return order
    return None


@work_bp.get("/admin/supply/queue/")
def supply_admin_queue():
    """FestOps finalize queue: SUBMITTED supply orders, oldest-submitted first.

    Per order, surfaces decided/total APPROVAL_GROUP review progress and a
    ready-to-finalize badge when every line has a terminal review decision.
    `?event=CODE` limits the list to one event; an unknown code shows an
    empty list and an error, never all events.
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)

    event_code = (request.args.get("event") or "").strip()
    event_cycle_id = None
    cycle = None
    unknown_event = False
    if event_code:
        cycle = EventCycle.query.filter_by(code=event_code).first()
        if cycle is None:
            unknown_event = True
            flash(f"Unknown event: {event_code}", "error")
        else:
            event_cycle_id = cycle.id

    if unknown_event:
        orders = []
    else:
        orders = (
            _submitted_supply_orders(supply_wt, event_cycle_id)
            .options(
                joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
                joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
                joinedload(WorkItem.supply_order_detail),
                selectinload(WorkItem.lines).joinedload(WorkLine.supply_detail),
            )
            .all()
        )

    event_options = (
        db.session.query(EventCycle)
        .join(WorkPortfolio, WorkPortfolio.event_cycle_id == EventCycle.id)
        .join(WorkItem, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(
            WorkPortfolio.work_type_id == supply_wt.id,
            WorkItem.status == WORK_ITEM_STATUS_SUBMITTED,
            WorkItem.is_archived == False,  # noqa: E712
        )
        .distinct()
        .order_by(EventCycle.code)
        .all()
    )
    # Finalize-and-next often empties the filtered event; keep it selectable
    # so the dropdown shows the filter in force, not "All events".
    if cycle is not None and cycle not in event_options:
        event_options.append(cycle)

    all_line_ids: list[int] = []
    for order in orders:
        all_line_ids.extend(line.id for line in order.lines)
    ag_reviews = _ag_reviews_by_line(all_line_ids)

    rows = []
    for order in orders:
        decided, total, ready = _ready_counts(order.lines, ag_reviews)
        order_detail = order.supply_order_detail
        rows.append({
            "work_item": order,
            "department": order.portfolio.department,
            "event_cycle": order.portfolio.event_cycle,
            "pickup_time": order_detail.pickup_time if order_detail else None,
            "decided": decided,
            "total": total,
            "ready": ready,
        })

    # Oldest-submitted first. No pickup-slot urgency sorting — warehouse
    # staging tools are Phase 2 (spec: 2026-07-08 pickup-time design).
    rows.sort(key=lambda r: _queue_sort_key(r["work_item"]))

    return render_template(
        "supply/admin_queue.html",
        user_ctx=user_ctx,
        rows=rows,
        event_options=event_options,
        selected_event=event_code,
        friendly_status=friendly_status,
    )


def _is_line_kicked_back(line: WorkLine, ag_review: WorkLineReview | None) -> bool:
    """True while a live line awaits a requester response to a kickback.

    Mirrors BUDGET's can_finalize_work_item guard (admin_final/helpers.py:
    265-267): finalize must hard-block until the requester resolves it.
    A removed line is never kicked back, whatever a stale review says.
    """
    if is_line_removed(line.supply_detail):
        return False
    if line.needs_requester_action:
        return True
    return ag_review is not None and ag_review.status in (
        REVIEW_STATUS_NEEDS_INFO,
        REVIEW_STATUS_NEEDS_ADJUSTMENT,
    )


def _finalize_defaults(detail, ag_review, admin_review) -> tuple[int, str]:
    """Return the (approved quantity, note) prefill for the finalize form and the POST fallback.

    A decided admin review wins, so refinalizing a reopened order as shown keeps
    the admin's cuts and notes. A review reset to PENDING by a close or an
    order-page change falls through to the review group's decision.
    """
    if (admin_review is not None and admin_review.decided_at is not None
            and admin_review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED)):
        note = admin_review.note or ""
        if admin_review.status == REVIEW_STATUS_REJECTED:
            return 0, note
        if detail is not None and detail.quantity_approved is not None:
            return detail.quantity_approved, note
        return _default_approved_qty(detail, ag_review), note
    return _default_approved_qty(detail, ag_review), ""


def _default_approved_qty(detail, ag_review) -> int:
    """Prefill from the review group's decision alone.

    Orders decided before quantity_reviewed existed have it NULL; they keep
    the requested quantity.
    """
    requested = detail.quantity_requested if detail else 0
    if ag_review is None:
        return requested
    if ag_review.status == REVIEW_STATUS_REJECTED:
        return 0
    if (
        ag_review.status == REVIEW_STATUS_APPROVED
        and detail is not None
        and detail.quantity_reviewed is not None
    ):
        return detail.quantity_reviewed
    return requested


def _build_finalize_view(work_item: WorkItem):
    """Assemble per-line rows for the finalize screen (GET + POST re-render)."""
    lines = sorted(work_item.lines, key=lambda l: l.line_number)
    ag_reviews = _ag_reviews_by_line([line.id for line in lines])
    admin_reviews = _reviews_by_line([line.id for line in lines], REVIEW_STAGE_ADMIN_FINAL)
    closed = is_order_closed(work_item)

    line_rows = []
    for line in lines:
        detail = line.supply_detail
        item = detail.item if detail else None
        ag_review = ag_reviews.get(line.id)
        requested = detail.quantity_requested if detail else 0

        if closed:
            # Close books every live line at 0; a reviewer's quantity would read as approved.
            final = detail.quantity_approved if detail else None
            default_qty, default_note = (final if final is not None else 0), ""
        else:
            default_qty, default_note = _finalize_defaults(
                detail, ag_review, admin_reviews.get(line.id))

        unit_cost = item.unit_cost_cents if item else None
        line_rows.append({
            "line": line,
            "detail": detail,
            "removed": is_line_removed(detail),
            "item": item,
            "category": item.category if item else None,
            "requested": requested,
            "default_qty": default_qty,
            "default_note": default_note,
            "unit_cost_cents": unit_cost,
            "ag_review": ag_review,
            "decided_by": ag_review.decided_by_user_id if ag_review else None,
            "kicked_back": _is_line_kicked_back(line, ag_review),
            "reviewer_label": (
                None if closed or is_line_removed(detail)
                else reviewer_quantity_label(detail, ag_review)
            ),
        })
    return line_rows


@work_bp.get("/admin/supply/order/<public_id>/finalize")
def supply_admin_finalize_view(public_id: str):
    """Finalize screen: per line, requested qty + review-group outcome + an
    editable approved qty (defaulted) and an override-note field."""
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)
    work_item = _load_supply_order(public_id, supply_wt)

    return _render_finalize(work_item, user_ctx,
                            is_finalized=work_item.status == WORK_ITEM_STATUS_FINALIZED)


def _can_edit_lines(work_item: WorkItem) -> bool:
    return work_item.status == WORK_ITEM_STATUS_SUBMITTED and not is_order_closed(work_item)


def _render_finalize(work_item: WorkItem, user_ctx, *, is_finalized: bool):
    line_rows = _build_finalize_view(work_item)
    can_edit_lines = _can_edit_lines(work_item)
    return render_template(
        "supply/admin_finalize.html",
        user_ctx=user_ctx,
        work_item=work_item,
        order_detail=work_item.supply_order_detail,
        department=work_item.portfolio.department,
        event_cycle=work_item.portfolio.event_cycle,
        line_rows=line_rows,
        has_kickbacks=any(row["kicked_back"] for row in line_rows),
        friendly_status=friendly_status,
        format_currency=format_currency,
        is_finalized=is_finalized,
        is_closed=is_order_closed(work_item),
        # The finalize screen is reached only by Supply admins.
        is_supply_admin=True,
        can_edit_lines=can_edit_lines,
        admin_items=admin_item_choices() if can_edit_lines else [],
        edit_lines=[row["line"] for row in line_rows if not row["removed"]],
    )


@work_bp.post("/admin/supply/order/<public_id>/finalize")
def supply_admin_finalize(public_id: str):
    """Apply admin-final decisions per line and lock the order to FINALIZED.

    Overriding a review-group decision (zeroing an approved line, or approving
    a rejected one) requires a note. Any validation failure re-renders the
    finalize screen (200) with a flash and writes nothing. Lines still PENDING
    at APPROVAL_GROUP are auto-approved at the requested quantity (the default
    the form submits — matching BUDGET finalize semantics).
    """
    # Lazy import: module-level import of admin helpers from non-admin routes
    # breaks the app's `h` proxy. This is the one sanctioned exception.
    from app.routes.admin_final.helpers import get_or_create_admin_review

    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)
    # Lock the work item row to prevent concurrent finalization (mirrors
    # BUDGET's finalize_work_item, admin_final/helpers.py:476) and re-check
    # status after acquiring the lock in case another request finalized it,
    # or an admin removed or added a line, before the lock was taken.
    work_item = _lock_supply_order(public_id, supply_wt)

    if work_item.status != WORK_ITEM_STATUS_SUBMITTED or is_order_closed(work_item):
        flash("Only submitted supply orders can be finalized.", "error")
        return redirect(url_for("work.supply_admin_queue"))

    lines = sorted(work_item.lines, key=lambda l: l.line_number)
    ag_reviews = _ag_reviews_by_line([line.id for line in lines])
    admin_reviews = _reviews_by_line([line.id for line in lines], REVIEW_STAGE_ADMIN_FINAL)

    errors = []

    # BUDGET semantics: hard-block finalization while any line is kicked back
    # awaiting a requester response (can_finalize_work_item's guard,
    # admin_final/helpers.py:265-267). Write-free error path, same as
    # override-without-note.
    for line in lines:
        if _is_line_kicked_back(line, ag_reviews.get(line.id)):
            errors.append(
                f"Line {line.line_number} is awaiting requester response "
                "— resolve the kickback before finalizing."
            )

    plan = []  # (line, approved_qty, note)
    for line in lines:
        detail = line.supply_detail
        # A stale form may still post a quantity for a removed line; it stays at 0.
        if is_line_removed(detail):
            continue
        ag_review = ag_reviews.get(line.id)
        default_qty, default_note = _finalize_defaults(
            detail, ag_review, admin_reviews.get(line.id))

        raw = request.form.get(f"approved_qty_{line.line_number}")
        if raw is None or raw.strip() == "":
            # Missing field -> fall back to the same default the form shows.
            qty = default_qty
        else:
            try:
                qty = int(raw)
            except (TypeError, ValueError):
                errors.append(f"Line {line.line_number}: approved quantity must be a whole number.")
                continue

        if qty < 0:
            errors.append(f"Line {line.line_number}: approved quantity cannot be negative.")
            continue
        if qty > MAX_APPROVED_QUANTITY:
            errors.append(f"Line {line.line_number}: enter an approved quantity of 100,000 or less.")
            continue

        # Only an absent field falls back; a posted blank is the admin clearing the note.
        raw_note = request.form.get(f"note_{line.line_number}")
        note = (default_note if raw_note is None else raw_note).strip()

        ag_approved = ag_review is not None and ag_review.status == REVIEW_STATUS_APPROVED
        ag_rejected = ag_review is not None and ag_review.status == REVIEW_STATUS_REJECTED
        overriding = (ag_approved and qty == 0) or (ag_rejected and qty > 0)
        if overriding and not note:
            errors.append(
                f"Line {line.line_number}: overriding the review group's "
                "decision requires a note."
            )

        plan.append((line, qty, note))

    if errors:
        for err in errors:
            flash(err, "error")
        return _render_finalize(work_item, user_ctx, is_finalized=False)

    event_code = work_item.portfolio.event_cycle.code
    event_cycle_id = work_item.portfolio.event_cycle_id
    now = datetime.utcnow()
    for line, qty, note in plan:
        prior_status = line.status
        detail = line.supply_detail
        item = detail.item if detail else None
        unit_cost = item.unit_cost_cents if item else None

        admin_review, _created = get_or_create_admin_review(line, user_ctx)
        admin_review.status = REVIEW_STATUS_APPROVED if qty > 0 else REVIEW_STATUS_REJECTED
        admin_review.approved_amount_cents = (qty * unit_cost) if unit_cost is not None else None
        admin_review.note = note or None
        admin_review.decided_at = now
        admin_review.decided_by_user_id = user_ctx.user_id

        if detail is not None:
            detail.quantity_approved = qty

        line.status = WORK_LINE_STATUS_APPROVED if qty > 0 else WORK_LINE_STATUS_REJECTED
        line.approved_amount_cents = admin_review.approved_amount_cents
        line.status_changed_at = now
        line.status_changed_by_user_id = user_ctx.user_id
        line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL

        db.session.add(WorkLineAuditEvent(
            work_line_id=line.id,
            event_type=AUDIT_EVENT_ADMIN_FINAL,
            field_name="status",
            old_value=prior_status,
            new_value=f"approved qty {qty}",
            note=note or None,
            created_by_user_id=user_ctx.user_id,
        ))

    old_status = work_item.status
    work_item.status = WORK_ITEM_STATUS_FINALIZED
    work_item.finalized_at = now
    work_item.finalized_by_user_id = user_ctx.user_id

    db.session.add(WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_FINALIZE,
        old_value=old_status,
        new_value=WORK_ITEM_STATUS_FINALIZED,
        created_by_user_id=user_ctx.user_id,
        snapshot={"line_count": len(lines)},
    ))
    # Queued in this transaction; the commit below sends nothing itself. The
    # lookups ahead of the per-recipient savepoint can fail too, and an email
    # failure must not undo the finalize.
    try:
        with enqueue_savepoint():
            notify_supply_finalized(work_item, build_finalize_changes(work_item), now)
    except Exception:
        current_app.logger.exception(
            f"Could not queue the finalized email for {work_item.public_id}; "
            f"the finalize stands and the department must be told by hand."
        )
    db.session.commit()

    flash(f"Supply order {work_item.public_id} finalized.", "success")
    if request.form.get("and_next"):
        following = next_ready_order(event_cycle_id, work_item.id)
        if following is not None:
            return redirect(url_for(
                "work.supply_admin_finalize_view", public_id=following.public_id,
            ))
        flash(f"No more orders are ready to finalize in {event_code}.", "info")
        return redirect(url_for("work.supply_admin_queue", event=event_code))
    return redirect(url_for("work.supply_admin_queue"))


# Line edits. Each route locks the order, re-checks it under the lock, and
# commits once; admin_lines refuses by raising SupplyAdminError.

def _return_to_value(raw: str | None) -> str:
    """Normalize a posted or queried origin to "finalize" or "order"."""
    return "finalize" if raw == "finalize" else "order"


def _origin_url(work_item: WorkItem, at_finalize: bool) -> str:
    """Build the finalize screen or order page URL; never a URL from the request."""
    if at_finalize:
        return url_for("work.supply_admin_finalize_view", public_id=work_item.public_id)
    portfolio = work_item.portfolio
    return url_for(
        "work.supply_order_detail",
        event=portfolio.event_cycle.code,
        dept=portfolio.department.code,
        public_id=work_item.public_id,
    )


def _line_edit_redirect(work_item: WorkItem, at_finalize: bool):
    """Redirect to the finalize screen or the order page."""
    return redirect(_origin_url(work_item, at_finalize))


def _locked_line(work_item: WorkItem, line_number: int) -> WorkLine:
    line = next((l for l in work_item.lines if l.line_number == line_number), None)
    if line is None:
        abort(404, f"Line not found: {line_number}")
    return line


def _flash_and_return(work_item: WorkItem, at_finalize: bool, message: str):
    """Flash a refusal and return to the origin page."""
    flash(message, "error")
    return _line_edit_redirect(work_item, at_finalize)


def _run_line_edit(public_id: str, edit, success_message, on_error):
    """Lock the order, apply one edit, and commit it or roll it back.

    `edit(work_item, user_ctx, at_finalize)` returns a value passed to
    `success_message` for the flash. A refusal rolls back and returns
    `on_error(work_item, at_finalize, message)`.
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)
    work_item = _lock_supply_order(public_id, supply_wt)
    at_finalize = _return_to_value(request.form.get("return_to")) == "finalize"
    try:
        require_editable_order(work_item)
        result = edit(work_item, user_ctx, at_finalize)
    except SupplyAdminError as exc:
        db.session.rollback()
        return on_error(work_item, at_finalize, str(exc))
    db.session.commit()
    flash(success_message(result), "success")
    return _line_edit_redirect(work_item, at_finalize)


def _render_add_form(work_item: WorkItem, return_to: str, *,
                     error: str | None = None, values: dict | None = None):
    """Render the Add line page for an order."""
    return render_template(
        "supply/admin_line_add.html",
        work_item=work_item,
        return_to=return_to,
        cancel_url=_origin_url(work_item, return_to == "finalize"),
        admin_items=admin_item_choices(),
        error=error,
        values=values or {},
    )


@work_bp.get("/admin/supply/order/<public_id>/lines/add")
def supply_admin_line_add_form(public_id: str):
    """Show the Add line page; a refused order returns to where the admin came from."""
    supply_wt = _require_supply_admin(get_user_ctx())
    work_item = _load_supply_order(public_id, supply_wt)
    return_to = _return_to_value(request.args.get("return_to"))
    try:
        require_editable_order(work_item)
    except SupplyAdminError as exc:
        return _flash_and_return(work_item, return_to == "finalize", str(exc))
    return _render_add_form(work_item, return_to)


@work_bp.post("/admin/supply/order/<public_id>/lines/add")
def supply_admin_line_add(public_id: str):
    """Add a line: approved at once from finalize, sent for review from the order page."""
    def edit(work_item, user_ctx, at_finalize):
        item = db.session.get(SupplyItem, request.form.get("item_id", type=int) or 0)
        if item is None:
            raise SupplyAdminError("Choose an item to add.")
        quantity = parse_admin_quantity(request.form.get("quantity"))
        notes = normalize_reason(request.form.get("notes"), required=False)
        return admin_add_line(work_item, item, quantity, notes, user_ctx,
                              at_finalize=at_finalize)

    return _run_line_edit(
        public_id, edit,
        lambda line: f"Line {line.line_number} added: {line.supply_detail.item.item_name}.",
        lambda wi, at_finalize, msg: _render_add_form(
            wi, "finalize" if at_finalize else "order", error=msg, values=request.form),
    )


@work_bp.post("/admin/supply/order/<public_id>/lines/<int:line_number>/quantity")
def supply_admin_line_quantity(public_id: str, line_number: int):
    """Change a line's requested quantity; the department's original is kept."""
    def edit(work_item, user_ctx, at_finalize):
        line = _locked_line(work_item, line_number)
        quantity = parse_admin_quantity(request.form.get("quantity"))
        admin_change_requested_quantity(
            line, quantity, request.form.get("reason"), user_ctx, at_finalize=at_finalize,
        )
        return line

    return _run_line_edit(
        public_id, edit,
        lambda line: f"Line {line.line_number} now asks for {line.supply_detail.quantity_requested}.",
        _flash_and_return,
    )


@work_bp.post("/admin/supply/order/<public_id>/lines/<int:line_number>/remove")
def supply_admin_line_remove(public_id: str, line_number: int):
    """Remove a line; it stays on the order, struck through, with the reason."""
    def edit(work_item, user_ctx, at_finalize):
        line = _locked_line(work_item, line_number)
        admin_remove_line(line, request.form.get("reason"), user_ctx)
        return line

    return _run_line_edit(
        public_id, edit, lambda line: f"Line {line.line_number} removed.",
        _flash_and_return,
    )


# Close and reopen. Same lock, re-check and single commit as the line edits.

def _render_reason_form(work_item: WorkItem, action: str, return_to: str, *,
                        error: str | None = None, reason: str = ""):
    """Render the Close order or Reopen order page; `action` is "close" or "reopen"."""
    return render_template(
        "supply/admin_order_reason.html",
        work_item=work_item,
        action=action,
        return_to=return_to,
        cancel_url=_origin_url(work_item, return_to == "finalize"),
        is_closed=is_order_closed(work_item),
        error=error,
        reason=reason,
    )


def _reason_form_page(public_id: str, action: str, allowed):
    """Show a reason page, or flash and return to the origin when the order cannot take it."""
    supply_wt = _require_supply_admin(get_user_ctx())
    work_item = _load_supply_order(public_id, supply_wt)
    return_to = _return_to_value(request.args.get("return_to"))
    if not allowed(work_item):
        message = ("Only a submitted order that is not closed can be closed."
                   if action == "close" else "Only a finalized or closed order can be reopened.")
        return _flash_and_return(work_item, return_to == "finalize", message)
    return _render_reason_form(work_item, action, return_to)


def _run_order_action(public_id: str, action_name: str, action, success_message: str):
    """Lock the order, apply close or reopen, and commit it or roll it back.

    A refusal rolls back and re-renders the page with the typed reason.
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)
    work_item = _lock_supply_order(public_id, supply_wt)
    return_to = _return_to_value(request.form.get("return_to"))
    try:
        action(work_item, request.form.get("reason"), user_ctx)
    except SupplyAdminError as exc:
        db.session.rollback()
        return _render_reason_form(
            work_item, action_name, return_to,
            error=str(exc), reason=request.form.get("reason", ""),
        )
    db.session.commit()
    flash(success_message.format(public_id=work_item.public_id), "success")
    return _line_edit_redirect(work_item, return_to == "finalize")


@work_bp.get("/admin/supply/order/<public_id>/close")
def supply_admin_close_form(public_id: str):
    """Show the Close order page for a submitted order that is not closed."""
    return _reason_form_page(
        public_id, "close",
        lambda wi: wi.status == WORK_ITEM_STATUS_SUBMITTED and not is_order_closed(wi),
    )


@work_bp.get("/admin/supply/order/<public_id>/reopen")
def supply_admin_reopen_form(public_id: str):
    """Show the Reopen order page; a closed order is FINALIZED with a closed marker."""
    return _reason_form_page(
        public_id, "reopen", lambda wi: wi.status == WORK_ITEM_STATUS_FINALIZED,
    )


@work_bp.post("/admin/supply/order/<public_id>/close")
def supply_admin_close(public_id: str):
    """Close a submitted order the department placed by mistake; the department is emailed the reason."""
    return _run_order_action(public_id, "close", close_order, "Supply order {public_id} closed.")


@work_bp.post("/admin/supply/order/<public_id>/reopen")
def supply_admin_reopen(public_id: str):
    """Reopen a finalized or closed order for review; the reason is posted as a public comment."""
    return _run_order_action(public_id, "reopen", reopen_order, "Supply order {public_id} reopened.")
