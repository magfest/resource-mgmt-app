"""Validation for supply order submission.

The engine's submit_work_item SILENTLY SKIPS lines it can't route
(lifecycle.py:67-68) — this module is the loud gate that runs first.
"""
from app.routing.registry import get_approval_group_for_line


def validate_order_for_submit(work_item) -> list[str]:
    errors: list[str] = []
    detail = work_item.supply_order_detail

    if not work_item.lines:
        errors.append("Add at least one item to the order before submitting.")
    if detail is None or not detail.needed_by_date:
        errors.append("Set a needed-by date in Delivery details.")
    if detail is None or not (detail.delivery_location or "").strip():
        errors.append("Set a delivery location in Delivery details.")

    for line in sorted(work_item.lines, key=lambda l: l.line_number):
        d = line.supply_detail
        if d is None or d.item is None:
            errors.append(f"Line {line.line_number}: item is missing — remove and re-add it.")
            continue
        if not d.item.is_active:
            errors.append(
                f"Line {line.line_number}: '{d.item.item_name}' is no longer "
                "available — remove it to submit."
            )
        if d.item.notes_required and not (d.requester_notes or "").strip():
            errors.append(
                f"Line {line.line_number}: '{d.item.item_name}' requires a note "
                "explaining the request."
            )
        try:
            group = get_approval_group_for_line(line)
        except ValueError:
            group = None
        if group is None:
            errors.append(
                f"Line {line.line_number}: '{d.item.item_name}' can't be routed "
                "for review — its category has no reviewer group. Contact a "
                "supply admin."
            )
    return errors
