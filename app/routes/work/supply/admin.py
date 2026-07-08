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

from flask import abort, flash, redirect, render_template, request, url_for
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
from .. import work_bp

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


def _ag_reviews_by_line(line_ids: list[int]) -> dict[int, WorkLineReview]:
    """Map work_line_id -> its APPROVAL_GROUP review (one per line per stage)."""
    if not line_ids:
        return {}
    reviews = WorkLineReview.query.filter(
        WorkLineReview.work_line_id.in_(line_ids),
        WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP,
    ).all()
    return {r.work_line_id: r for r in reviews}


@work_bp.get("/admin/supply/queue/")
def supply_admin_queue():
    """FestOps finalize queue: SUBMITTED supply orders, oldest-submitted first.

    Per order, surfaces decided/total APPROVAL_GROUP review progress and a
    ready-to-finalize badge when every line has a terminal review decision.
    """
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)

    orders = (
        WorkItem.query
        .filter(
            WorkItem.is_archived == False,  # noqa: E712
            WorkItem.status == WORK_ITEM_STATUS_SUBMITTED,
        )
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
        .options(
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.department),
            joinedload(WorkItem.portfolio).joinedload(WorkPortfolio.event_cycle),
            joinedload(WorkItem.supply_order_detail),
            selectinload(WorkItem.lines),
        )
        .all()
    )

    all_line_ids: list[int] = []
    for order in orders:
        all_line_ids.extend(line.id for line in order.lines)
    ag_reviews = _ag_reviews_by_line(all_line_ids)

    rows = []
    for order in orders:
        total = 0
        decided = 0
        for line in order.lines:
            review = ag_reviews.get(line.id)
            if review is None:
                continue
            total += 1
            if review.status in (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED):
                decided += 1
        order_detail = order.supply_order_detail
        rows.append({
            "work_item": order,
            "department": order.portfolio.department,
            "event_cycle": order.portfolio.event_cycle,
            "pickup_time": order_detail.pickup_time if order_detail else None,
            "decided": decided,
            "total": total,
            "ready": total > 0 and decided == total,
        })

    # Oldest-submitted first. No pickup-slot urgency sorting — warehouse
    # staging tools are Phase 2 (spec: 2026-07-08 pickup-time design).
    rows.sort(key=lambda r: (
        r["work_item"].submitted_at is None,
        r["work_item"].submitted_at or r["work_item"].created_at,
    ))

    return render_template(
        "supply/admin_queue.html",
        user_ctx=user_ctx,
        rows=rows,
        friendly_status=friendly_status,
    )


def _is_line_kicked_back(line: WorkLine, ag_review: WorkLineReview | None) -> bool:
    """True while a line awaits a requester response to a kickback.

    Mirrors BUDGET's can_finalize_work_item guard (admin_final/helpers.py:
    265-267): finalize must hard-block until the requester resolves it.
    """
    if line.needs_requester_action:
        return True
    return ag_review is not None and ag_review.status in (
        REVIEW_STATUS_NEEDS_INFO,
        REVIEW_STATUS_NEEDS_ADJUSTMENT,
    )


def _build_finalize_view(work_item: WorkItem):
    """Assemble per-line rows for the finalize screen (GET + POST re-render)."""
    lines = sorted(work_item.lines, key=lambda l: l.line_number)
    ag_reviews = _ag_reviews_by_line([line.id for line in lines])

    line_rows = []
    for line in lines:
        detail = line.supply_detail
        item = detail.item if detail else None
        ag_review = ag_reviews.get(line.id)
        requested = detail.quantity_requested if detail else 0

        # Default approved qty: requested for approved/pending lines, 0 for
        # a line the review group rejected.
        if ag_review is not None and ag_review.status == REVIEW_STATUS_REJECTED:
            default_qty = 0
        else:
            default_qty = requested

        unit_cost = item.unit_cost_cents if item else None
        line_rows.append({
            "line": line,
            "item": item,
            "category": item.category if item else None,
            "requested": requested,
            "default_qty": default_qty,
            "unit_cost_cents": unit_cost,
            "ag_review": ag_review,
            "decided_by": ag_review.decided_by_user_id if ag_review else None,
            "kicked_back": _is_line_kicked_back(line, ag_review),
        })
    return line_rows


@work_bp.get("/admin/supply/order/<public_id>/finalize")
def supply_admin_finalize_view(public_id: str):
    """Finalize screen: per line, requested qty + review-group outcome + an
    editable approved qty (defaulted) and an override-note field."""
    user_ctx = get_user_ctx()
    supply_wt = _require_supply_admin(user_ctx)
    work_item = _load_supply_order(public_id, supply_wt)

    line_rows = _build_finalize_view(work_item)

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
        is_finalized=work_item.status == WORK_ITEM_STATUS_FINALIZED,
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
    work_item = _load_supply_order(public_id, supply_wt)

    # Lock the work item row to prevent concurrent finalization (mirrors
    # BUDGET's finalize_work_item, admin_final/helpers.py:476) and re-check
    # status after acquiring the lock in case another request finalized it
    # between _load_supply_order and here.
    db.session.query(WorkItem).with_for_update().get(work_item.id)

    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        flash("Only submitted supply orders can be finalized.", "error")
        return redirect(url_for("work.supply_admin_queue"))

    lines = sorted(work_item.lines, key=lambda l: l.line_number)
    ag_reviews = _ag_reviews_by_line([line.id for line in lines])

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
        ag_review = ag_reviews.get(line.id)
        requested = detail.quantity_requested if detail else 0

        raw = request.form.get(f"approved_qty_{line.line_number}")
        if raw is None or raw.strip() == "":
            # Missing field -> fall back to the same default the form shows.
            if ag_review is not None and ag_review.status == REVIEW_STATUS_REJECTED:
                qty = 0
            else:
                qty = requested
        else:
            try:
                qty = int(raw)
            except (TypeError, ValueError):
                errors.append(f"Line {line.line_number}: approved quantity must be a whole number.")
                continue

        if qty < 0:
            errors.append(f"Line {line.line_number}: approved quantity cannot be negative.")
            continue

        note = (request.form.get(f"note_{line.line_number}") or "").strip()

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
        line_rows = _build_finalize_view(work_item)
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
            is_finalized=False,
        )

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
    db.session.commit()

    flash(f"Supply order {work_item.public_id} finalized.", "success")
    return redirect(url_for("work.supply_admin_queue"))
