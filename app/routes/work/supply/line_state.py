"""Shared Supply line and order state, read by the review, admin and report cabs."""

# Prefix of the public line comment that carries an admin's add note.
ADDED_COMMENT_PREFIX = "[ADDED BY SUPPLY ADMIN] "


def is_line_removed(detail) -> bool:
    """A removed line keeps its row; `removed_at` is the only marker."""
    return detail is not None and detail.removed_at is not None


def is_order_closed(work_item) -> bool:
    detail = work_item.supply_order_detail
    return detail is not None and detail.closed_at is not None


def supply_display_status(work_item) -> str:
    """CLOSED overrides every workflow status; otherwise the shared summary decides."""
    if is_order_closed(work_item):
        return "CLOSED"
    # Imported here: computations imports models that import this package.
    from app.routes.work.helpers.computations import compute_line_status_summary
    return compute_line_status_summary(work_item).effective_status


def reviewer_quantity_label(detail, ag_review) -> str | None:
    """"Approved 6 of 10" once the group approved with a quantity, else None."""
    if detail is None or ag_review is None or detail.quantity_reviewed is None:
        return None
    if ag_review.status != "APPROVED":
        return None
    return f"Approved {detail.quantity_reviewed} of {detail.quantity_requested}"


def final_quantity_label(detail) -> str | None:
    """"Approved 6 of 10", or "Not approved" at 0, once finalize sets `quantity_approved`."""
    if detail is None or detail.quantity_approved is None:
        return None
    if detail.quantity_approved == 0:
        return "Not approved"
    return f"Approved {detail.quantity_approved} of {detail.quantity_requested}"


def admin_line_notes(lines) -> tuple[dict, dict]:
    """Return the latest quantity-change reason and the add note per line number.

    Each value is a dict of text, by and at. One query per kind for the whole
    page; the reason lives on the FIELD_CHANGE audit row and the add note on
    the line's public comment.
    """
    # Imported here: the models package imports this one.
    from app.models import (
        AUDIT_EVENT_FIELD_CHANGE, COMMENT_VISIBILITY_PUBLIC,
        WorkLineAuditEvent, WorkLineComment,
    )

    numbers = {line.id: line.line_number for line in lines}
    changed_ids = [line.id for line in lines
                   if line.supply_detail is not None
                   and line.supply_detail.department_quantity is not None]
    added_ids = [line.id for line in lines
                 if line.supply_detail is not None
                 and line.supply_detail.added_by_admin_user_id]

    changes = {}
    if changed_ids:
        rows = (
            WorkLineAuditEvent.query
            .filter(WorkLineAuditEvent.work_line_id.in_(changed_ids),
                    WorkLineAuditEvent.event_type == AUDIT_EVENT_FIELD_CHANGE,
                    WorkLineAuditEvent.field_name == "quantity_requested")
            .order_by(WorkLineAuditEvent.id)
            .all()
        )
        for row in rows:
            changes[numbers[row.work_line_id]] = {
                "text": row.note or "", "by": row.created_by_user_id, "at": row.created_at,
            }

    notes = {}
    if added_ids:
        rows = (
            WorkLineComment.query
            .filter(WorkLineComment.work_line_id.in_(added_ids),
                    WorkLineComment.visibility == COMMENT_VISIBILITY_PUBLIC,
                    WorkLineComment.body.startswith(ADDED_COMMENT_PREFIX))
            .order_by(WorkLineComment.id)
            .all()
        )
        for row in rows:
            notes.setdefault(numbers[row.work_line_id], {
                "text": row.body[len(ADDED_COMMENT_PREFIX):],
                "by": row.created_by_user_id, "at": row.created_at,
            })
    return changes, notes


def build_finalize_changes(work_item) -> list[dict]:
    """List what finalize changed from the department's request, by line number.

    Call after the finalize writes so `quantity_approved` is set. A removed
    or added line yields only that kind; `cut` and `raised` compare the final
    quantity with the current request, so an admin raise that finalizes as
    asked reports once, as `requested_changed`.
    """
    # Imported here: the models package imports this one.
    from app.models import AUDIT_EVENT_FIELD_CHANGE, WorkLineAuditEvent

    changes = []
    for line in sorted(work_item.lines, key=lambda l: l.line_number):
        detail = line.supply_detail
        if detail is None:
            continue
        item = detail.item
        base = {
            "line_number": line.line_number,
            "item_name": item.item_name if item else "",
            "unit": item.unit if item else "",
            "department_quantity": detail.department_quantity,
            "requested": detail.quantity_requested,
            "final": detail.quantity_approved,
            "reason": "",
        }
        if is_line_removed(detail):
            changes.append({**base, "kind": "removed", "reason": detail.removed_reason or ""})
            continue
        if detail.added_by_admin_user_id:
            changes.append({**base, "kind": "added"})
            continue
        if (detail.department_quantity is not None
                and detail.department_quantity != detail.quantity_requested):
            audit = (
                WorkLineAuditEvent.query
                .filter_by(work_line_id=line.id, event_type=AUDIT_EVENT_FIELD_CHANGE,
                           field_name="quantity_requested")
                .order_by(WorkLineAuditEvent.id.desc())
                .first()
            )
            changes.append({**base, "kind": "requested_changed",
                            "reason": (audit.note or "") if audit else ""})
        final = detail.quantity_approved
        if final is not None and final < detail.quantity_requested:
            changes.append({**base, "kind": "cut"})
        elif final is not None and final > detail.quantity_requested:
            changes.append({**base, "kind": "raised"})
    return changes
