"""Which spaces a supply order may name, for the order page and its submit rule.

The department's assigned spaces come first as a checklist. Any other space
at the event's venue is offered through a picker, because space allocation
is not always right before ordering opens. This mirrors the TechOps escape
hatch in techops/spaces.py without its per-space answers.
"""
from __future__ import annotations

from app.routes.spaces.queries import offerable_spaces, spaces_for_department


def order_space_choices(work_item) -> dict:
    """Return the checklist, picker options, and nameable spaces for an order.

    Returns:
        A dict with `checklist` (dicts of `space_id`, `label`, `is_assigned`,
        `checked`), `picker` (`(space_id, name)` pairs not on the checklist),
        `labels` (every space id this order may save, mapped to the name to
        snapshot), and `has_spaces`, False only when the event offers none.
    """
    portfolio = work_item.portfolio
    cycle = portfolio.event_cycle
    assigned = spaces_for_department(portfolio.department_id, cycle.id)
    offerable = offerable_spaces(cycle)
    held = {row.space_id: row for row in work_item.supply_order_spaces}

    checklist = []
    for entry in assigned:
        space_id = entry["space"].id
        checklist.append({
            "space_id": space_id,
            "label": entry["display_name"],
            "is_assigned": True,
            "checked": space_id in held,
        })
    assigned_ids = {item["space_id"] for item in checklist}
    # A space picked on an earlier save stays listed even after it is archived
    # or moved off the venue, so saving the form again does not drop it.
    for space_id, row in held.items():
        if space_id not in assigned_ids:
            checklist.append({
                "space_id": space_id,
                "label": row.space_label,
                "is_assigned": False,
                "checked": True,
            })

    # A slice inside an assigned room is already named by that room's entry.
    covered_ids = {child.id for entry in assigned for child in entry["covers"]}
    shown_ids = {item["space_id"] for item in checklist}
    picker = [
        (space.id, space.name) for space in offerable
        if space.id not in shown_ids and space.id not in covered_ids
    ]

    labels = {item["space_id"]: item["label"] for item in checklist}
    labels.update(dict(picker))

    return {
        "checklist": checklist,
        "picker": picker,
        "labels": labels,
        "has_spaces": bool(assigned or offerable),
    }
