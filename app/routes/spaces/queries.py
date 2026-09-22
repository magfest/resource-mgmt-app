"""Read-only accessors for other work types to consume the spaces subsystem.

This module imports nothing from `app.routes`, so any work type can import
it at module level without a circular import through the `h` proxy. It is
not Flask-free: `app.models` pulls Flask in, and every function here needs
an application context because it queries `db.session`. What it avoids is
the route layer, not the framework.

The alias, availability and fold rules here must agree with
`build_space_rows()` in `helpers.py`, which computes the same three things
for the Spaces admin page.
"""
from __future__ import annotations

from app import db
from app.models import Space, SpaceAssignment, SpaceEventOverride


def spaces_for_department(department_id: int, event_cycle_id: int) -> list[dict]:
    """Return the spaces one department directly holds at one event.

    One entry per space, ordered by Space.sort_order then Space.code,
    matching the Spaces admin page's listing order. Archived spaces
    (is_active False) are excluded, both as entries and from `covers`.

    Returns:
        Dicts with keys `space` (the Space row), `display_name` (this
        event's alias or space.name), `is_available`, `unavailable_reason`,
        and `covers`: a room's active slices, or a primary slice's active
        folded members, empty when the assignment carries neither.
    """
    assigned = (
        db.session.query(Space)
        .join(SpaceAssignment, SpaceAssignment.space_id == Space.id)
        .filter(
            SpaceAssignment.department_id == department_id,
            SpaceAssignment.event_cycle_id == event_cycle_id,
            Space.is_active.is_(True),
        )
        .order_by(Space.sort_order, Space.code)
        .all()
    )
    if not assigned:
        return []

    assigned_ids = [space.id for space in assigned]

    overrides_by_space_id = {
        override.space_id: override
        for override in db.session.query(SpaceEventOverride)
        .filter(
            SpaceEventOverride.event_cycle_id == event_cycle_id,
            SpaceEventOverride.space_id.in_(assigned_ids),
        )
        .all()
    }

    # A room's slices: active spaces whose parent is one of the held rooms.
    children_by_parent_id: dict[int, list[Space]] = {}
    for child in (
        db.session.query(Space)
        .filter(Space.parent_id.in_(assigned_ids), Space.is_active.is_(True))
        .order_by(Space.sort_order, Space.code)
        .all()
    ):
        children_by_parent_id.setdefault(child.parent_id, []).append(child)

    # A primary's folded members: active spaces whose override for this
    # event points combined_into_space_id at one of the held slices.
    folded_by_primary_id: dict[int, list[Space]] = {}
    fold_rows = (
        db.session.query(SpaceEventOverride, Space)
        .join(Space, Space.id == SpaceEventOverride.space_id)
        .filter(
            SpaceEventOverride.event_cycle_id == event_cycle_id,
            SpaceEventOverride.combined_into_space_id.in_(assigned_ids),
            Space.is_active.is_(True),
        )
        .order_by(Space.sort_order, Space.code)
        .all()
    )
    for override, member in fold_rows:
        folded_by_primary_id.setdefault(
            override.combined_into_space_id, []).append(member)

    entries = []
    for space in assigned:
        override = overrides_by_space_id.get(space.id)
        # A held space is a room or a slice, never both, so at most one of
        # these two lookups returns anything; the concatenation stays
        # ordered because each side is already sorted the same way.
        covers = (children_by_parent_id.get(space.id, [])
                 + folded_by_primary_id.get(space.id, []))
        entries.append({
            "space": space,
            "display_name": (override.alias if override and override.alias
                             else space.name),
            "is_available": override.is_available if override else True,
            "unavailable_reason": (override.unavailable_reason
                                   if override else None),
            "covers": covers,
        })
    return entries
