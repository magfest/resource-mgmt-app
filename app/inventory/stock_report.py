"""Supplies on hand: one row per counted-out item, summed across containers.

Reads InvStock, the cached balance the ledger keeps, and the newest submitted
count that still stands. Read-only; nothing here commits.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app import db
from app.inventory.counts import not_undone
from app.inventory.tree import display_name
from app.models import InvContainer, InvCount, InvCountLine, InvStock


def _last_counted() -> dict[int, datetime]:
    """Newest submitted, not-undone count time by item id, in one grouped query."""
    rows = (db.session.query(InvCountLine.item_id, func.max(InvCount.submitted_at))
            .join(InvCount, InvCount.id == InvCountLine.count_id)
            .filter(InvCount.submitted_at.isnot(None), not_undone())
            .group_by(InvCountLine.item_id).all())
    return {item_id: moment for item_id, moment in rows}


def supplies_on_hand() -> list[dict]:
    """Return one dict per item with stock above zero, sorted by item name.

    `places` holds (container name, quantity, approximate) largest first.
    """
    stock = (db.session.query(InvStock)
             .options(joinedload(InvStock.item),
                      joinedload(InvStock.container).joinedload(InvContainer.kind))
             .filter(InvStock.quantity > 0).all())
    by_item: dict[int, dict] = {}
    for row in stock:
        entry = by_item.setdefault(row.item_id, {
            "item_id": row.item_id, "name": row.item.item_name, "unit": row.item.unit,
            "total": 0, "approximate": False, "last_counted": None, "places": []})
        entry["total"] += row.quantity
        entry["approximate"] = entry["approximate"] or bool(row.is_approximate)
        entry["places"].append((display_name(row.container), row.quantity,
                                bool(row.is_approximate)))
    counted = _last_counted()
    for entry in by_item.values():
        entry["last_counted"] = counted.get(entry["item_id"])
        entry["places"].sort(key=lambda p: (-p[1], p[0]))
    return sorted(by_item.values(), key=lambda e: (e["name"], e["item_id"]))
