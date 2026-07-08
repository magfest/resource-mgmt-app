"""Validation for supply order submission.

The engine's submit_work_item SILENTLY SKIPS lines it can't route
(lifecycle.py:67-68) — this module is the loud gate that runs first.
"""
from app.routing.registry import get_approval_group_for_line

# Hardcoded for now (rarely changes; may become per-event config later).
# "(Select Pickup Time)" is the template's empty-value placeholder, NOT a
# member of this list. SupplyOrderDetail.pickup_time stores the chosen
# string verbatim.
PICKUP_TIME_OTHER = "Other, Please Add Preferred Date / Time to Order Notes"

PICKUP_TIME_OPTIONS = [
    "Tuesday Evening (after 6 PM)",
    "Wednesday Mid-day (after 12:01 PM)",
    "Wednesday Evening (after 6 PM)",
    "Thursday Morning (small orders only)",
    PICKUP_TIME_OTHER,
]


def validate_order_for_submit(work_item) -> list[str]:
    errors: list[str] = []
    detail = work_item.supply_order_detail

    if not work_item.lines:
        errors.append("Add at least one item to the order before submitting.")
    if detail is None or (detail.pickup_time or "") not in PICKUP_TIME_OPTIONS:
        errors.append("Select a pickup time in Pickup details.")
    elif detail.pickup_time == PICKUP_TIME_OTHER and not (detail.additional_notes or "").strip():
        errors.append(
            "Add your preferred pickup date/time to Additional notes — "
            "required when the 'Other' pickup option is selected."
        )

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
