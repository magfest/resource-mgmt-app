"""Supply admin line edits: add a line, change a requested quantity, remove a line.

Boundary: these functions change department orders, and every change writes an audit row.
None of them commits. The route commits once, and rolls back on SupplyAdminError.
An edit from the finalize screen skips the review group; one from the order
page sends the line back to it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from app import db
from app.line_details import set_line_routing_approval_group
from app.models import (
    SupplyCategory,
    SupplyItem,
    SupplyOrderLineDetail,
    WorkItemAuditEvent,
    WorkLine,
    WorkLineAuditEvent,
    WorkLineComment,
    WorkLineReview,
    AUDIT_EVENT_FIELD_CHANGE,
    AUDIT_EVENT_LINE_CREATED,
    AUDIT_EVENT_LINE_REMOVED,
    AUDIT_EVENT_REVIEW_DECISION,
    COMMENT_VISIBILITY_PUBLIC,
    REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_REJECTED,
)
from app.routing.registry import get_approval_group_for_line
from .form_utils import (
    MAX_APPROVED_QUANTITY,
    QUANTITY_TOO_LARGE,
    QUANTITY_ZERO,
    parse_whole_quantity,
)
from .line_state import is_line_removed, is_order_closed

MAX_REASON_LENGTH = 1000

REMOVED_REVIEW_NOTE = "Removed by Supply admin: {reason}"

# A removal closes these group reviews; APPROVED and REJECTED stay as decided.
_OPEN_GROUP_STATUSES = (
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
)


class SupplyAdminError(Exception):
    """A refused edit. The message is shown to the admin as is."""


def parse_admin_quantity(raw: str | None) -> int:
    """Parse an added or changed quantity, or raise SupplyAdminError."""
    value, code = parse_whole_quantity(raw)
    if code == QUANTITY_ZERO:
        raise SupplyAdminError(
            "Enter a quantity of at least 1. Use Remove to take a line off the order."
        )
    if code == QUANTITY_TOO_LARGE:
        raise SupplyAdminError("Enter a quantity of 100,000 or less.")
    if code is not None:
        raise SupplyAdminError("Enter a whole-number quantity.")
    return value


def normalize_reason(raw: str | None, *, required: bool) -> str | None:
    """Return the reason with CRLF as LF and outer whitespace stripped.

    The department reads this text, so it is measured after normalizing.
    """
    text = (raw or "").replace("\r\n", "\n").strip()
    if not text:
        if required:
            raise SupplyAdminError("Give a reason; the department sees it.")
        return None
    if len(text) > MAX_REASON_LENGTH:
        raise SupplyAdminError(
            f"Keep the reason to {MAX_REASON_LENGTH} characters or fewer."
        )
    return text


def admin_item_choices() -> list[tuple[SupplyCategory, list[SupplyItem]]]:
    """Active, orderable items grouped by active category, for the add picker."""
    items = (
        SupplyItem.query
        .join(SupplyCategory, SupplyItem.category_id == SupplyCategory.id)
        .filter(
            SupplyItem.is_active == True,  # noqa: E712
            SupplyItem.is_orderable == True,  # noqa: E712
            SupplyCategory.is_active == True,  # noqa: E712
        )
        .order_by(
            SupplyCategory.sort_order, SupplyCategory.name,
            SupplyItem.sort_order, SupplyItem.item_name,
        )
        .all()
    )
    grouped: dict[int, tuple[SupplyCategory, list[SupplyItem]]] = {}
    for item in items:
        grouped.setdefault(item.category_id, (item.category, []))[1].append(item)
    return list(grouped.values())


def require_editable_order(work_item) -> None:
    """Refuse an edit unless the order is SUBMITTED and not closed."""
    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        raise SupplyAdminError(
            "Only submitted orders can be edited. Reopen a finalized order first."
        )
    if is_order_closed(work_item):
        raise SupplyAdminError("This order is closed. Reopen it before editing lines.")


def _require_live_line(line: WorkLine) -> SupplyOrderLineDetail:
    detail = line.supply_detail
    if detail is None:
        raise SupplyAdminError(f"Line {line.line_number} has no supply item.")
    if is_line_removed(detail):
        raise SupplyAdminError(f"Line {line.line_number} is already removed.")
    return detail


def _check_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or quantity < 1 or quantity > MAX_APPROVED_QUANTITY:
        raise SupplyAdminError("Enter a quantity from 1 to 100,000.")


def _group_review(line: WorkLine) -> WorkLineReview | None:
    return (
        WorkLineReview.query
        .filter_by(work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP)
        .populate_existing()
        .first()
    )


def _admin_final_review(line: WorkLine) -> WorkLineReview | None:
    return (
        WorkLineReview.query
        .filter_by(work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL)
        .populate_existing()
        .first()
    )


def _route_to_group(line: WorkLine, user_ctx) -> WorkLineReview:
    """Route the line by category and open a PENDING group review, as submit does."""
    try:
        group = get_approval_group_for_line(line)
    except ValueError:
        group = None
    if group is None:
        raise SupplyAdminError(
            "This item's category has no reviewer group, so the line cannot be "
            "sent for review. Add it from the finalize screen instead."
        )
    set_line_routing_approval_group(line, group.id)
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    review = WorkLineReview(
        work_line_id=line.id,
        stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=group.id,
        status=REVIEW_STATUS_PENDING,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(review)
    return review


def _reopen_review(review: WorkLineReview, line: WorkLine, reason: str, user_ctx) -> None:
    """Send a review back to PENDING and record the reset on the line."""
    old_status = review.status
    review.status = REVIEW_STATUS_PENDING
    review.decided_at = None
    review.decided_by_user_id = None
    review.approved_amount_cents = None
    review.note = None
    if old_status != REVIEW_STATUS_PENDING:
        db.session.add(WorkLineAuditEvent(
            work_line_id=line.id,
            event_type=AUDIT_EVENT_REVIEW_DECISION,
            field_name=f"{review.stage.lower()}_review",
            old_value=old_status,
            new_value=REVIEW_STATUS_PENDING,
            note=f"Quantity changed by Supply admin: {reason}",
            created_by_user_id=user_ctx.user_id,
        ))


def admin_add_line(work_item, item: SupplyItem, quantity: int, notes: str | None,
                   user_ctx, *, at_finalize: bool) -> WorkLine:
    """Add a line to a submitted order.

    From the finalize screen it lands approved with no group review. From the
    order page it is routed to its category's group as PENDING. `notes` is the
    admin's optional reason; it goes to the audit row and a public comment,
    never to `requester_notes`, which holds only the department's words.
    """
    # Imported here: admin_final.helpers must not load with the work routes.
    from app.routes.admin_final.helpers import get_or_create_admin_review

    require_editable_order(work_item)
    _check_quantity(quantity)
    if item is None or not item.is_active or not item.is_orderable:
        raise SupplyAdminError("That item is not available to order.")

    # Removed lines keep their numbers, so the max covers them too.
    last = (
        db.session.query(func.max(WorkLine.line_number))
        .filter(WorkLine.work_item_id == work_item.id)
        .scalar()
    ) or 0
    now = datetime.utcnow()
    line = WorkLine(
        work_item=work_item,
        line_number=last + 1,
        status=WORK_LINE_STATUS_PENDING,
        updated_by_user_id=user_ctx.user_id,
    )
    db.session.add(line)
    detail = SupplyOrderLineDetail(
        work_line=line,
        item=item,
        quantity_requested=quantity,
        added_by_admin_user_id=user_ctx.user_id,
    )
    db.session.add(detail)
    db.session.flush()

    if at_finalize:
        admin_review, _created = get_or_create_admin_review(line, user_ctx)
        cost = item.unit_cost_cents
        admin_review.status = REVIEW_STATUS_APPROVED
        admin_review.approved_amount_cents = quantity * cost if cost is not None else None
        admin_review.decided_at = now
        admin_review.decided_by_user_id = user_ctx.user_id
        detail.quantity_approved = quantity
        line.status = WORK_LINE_STATUS_APPROVED
        line.approved_amount_cents = admin_review.approved_amount_cents
        line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL
    else:
        _route_to_group(line, user_ctx)
    line.status_changed_at = now
    line.status_changed_by_user_id = user_ctx.user_id

    db.session.add(WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_LINE_CREATED,
        field_name="line",
        new_value=f"{item.item_name} x {quantity}",
        note=notes,
        created_by_user_id=user_ctx.user_id,
    ))
    db.session.add(WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_LINE_CREATED,
        new_value=f"Line {line.line_number}: {item.item_name} x {quantity}",
        reason=notes,
        snapshot={
            "line_number": line.line_number,
            "item_id": item.id,
            "quantity": quantity,
            "at_finalize": at_finalize,
        },
        created_by_user_id=user_ctx.user_id,
    ))
    if notes:
        db.session.add(WorkLineComment(
            work_line_id=line.id,
            visibility=COMMENT_VISIBILITY_PUBLIC,
            body=f"[ADDED BY SUPPLY ADMIN] {notes}",
            created_by_user_id=user_ctx.user_id,
        ))
    return line


def admin_change_requested_quantity(line: WorkLine, new_quantity: int, reason: str,
                                    user_ctx, *, at_finalize: bool) -> None:
    """Change a line's requested quantity, keeping the department's original.

    From the order page the line goes back to its group as PENDING, because the
    group decided on a quantity that no longer stands. From the finalize screen
    the group's decision stays, and an admin approval moves to the new quantity.
    """
    require_editable_order(line.work_item)
    detail = _require_live_line(line)
    _check_quantity(new_quantity)
    reason = normalize_reason(reason, required=True)
    old_quantity = detail.quantity_requested
    if new_quantity == old_quantity:
        raise SupplyAdminError(f"Line {line.line_number} already asks for {old_quantity}.")

    if detail.department_quantity is None:
        detail.department_quantity = old_quantity
    detail.quantity_requested = new_quantity

    db.session.add(WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_FIELD_CHANGE,
        field_name="quantity_requested",
        old_value=str(old_quantity),
        new_value=str(new_quantity),
        note=reason,
        created_by_user_id=user_ctx.user_id,
    ))

    if at_finalize:
        # The finalize screen prefills a decided admin approval's quantity, so
        # it must follow the change; a rejection stays at 0 until overridden.
        admin_review = _admin_final_review(line)
        if (admin_review is not None and admin_review.decided_at is not None
                and admin_review.status == REVIEW_STATUS_APPROVED):
            cost = detail.item.unit_cost_cents if detail.item else None
            detail.quantity_approved = new_quantity
            admin_review.approved_amount_cents = new_quantity * cost if cost is not None else None
            line.approved_amount_cents = admin_review.approved_amount_cents
        return

    group_review = _group_review(line)
    if group_review is None:
        # A line added at finalize has never been routed.
        _route_to_group(line, user_ctx)
    else:
        _reopen_review(group_review, line, reason, user_ctx)
    # A line approved at finalize carries an admin decision that would block the group.
    admin_review = _admin_final_review(line)
    if admin_review is not None:
        _reopen_review(admin_review, line, reason, user_ctx)

    detail.quantity_reviewed = None
    detail.quantity_approved = None
    line.status = WORK_LINE_STATUS_PENDING
    line.approved_amount_cents = None
    line.needs_requester_action = False
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id


def admin_remove_line(line: WorkLine, reason: str, user_ctx) -> None:
    """Remove a line. It stays on the order, struck through, at quantity 0.

    An open group review is closed as REJECTED so the line leaves the group's
    queue and never blocks finalize. Inventory reads the REJECTED status and
    `quantity_approved` 0 as a rejected line.
    """
    require_editable_order(line.work_item)
    detail = _require_live_line(line)
    reason = normalize_reason(reason, required=True)
    now = datetime.utcnow()
    note = REMOVED_REVIEW_NOTE.format(reason=reason)
    prior_status = line.status

    detail.removed_at = now
    detail.removed_by_user_id = user_ctx.user_id
    detail.removed_reason = reason
    detail.quantity_approved = 0

    for review in (_group_review(line), _admin_final_review(line)):
        if review is None:
            continue
        if review.stage == REVIEW_STAGE_APPROVAL_GROUP and review.status not in _OPEN_GROUP_STATUSES:
            continue
        review.status = REVIEW_STATUS_REJECTED
        review.note = note
        review.decided_at = now
        review.decided_by_user_id = user_ctx.user_id
        if review.stage == REVIEW_STAGE_ADMIN_FINAL:
            review.approved_amount_cents = 0

    line.status = WORK_LINE_STATUS_REJECTED
    line.approved_amount_cents = 0
    line.needs_requester_action = False
    line.status_changed_at = now
    line.status_changed_by_user_id = user_ctx.user_id

    item_name = detail.item.item_name if detail.item else f"item {detail.item_id}"
    db.session.add(WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_LINE_REMOVED,
        field_name="status",
        old_value=prior_status,
        new_value=WORK_LINE_STATUS_REJECTED,
        note=reason,
        created_by_user_id=user_ctx.user_id,
    ))
    db.session.add(WorkItemAuditEvent(
        work_item_id=line.work_item_id,
        event_type=AUDIT_EVENT_LINE_REMOVED,
        new_value=f"Line {line.line_number}: {item_name}",
        reason=reason,
        snapshot={
            "line_number": line.line_number,
            "item_id": detail.item_id,
            "quantity_requested": detail.quantity_requested,
            "prior_status": prior_status,
        },
        created_by_user_id=user_ctx.user_id,
    ))
