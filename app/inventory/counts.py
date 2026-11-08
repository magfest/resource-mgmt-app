"""Counting a container: start, show what to count, submit.

A count sets each item's balance with an ADJUST, then logs thrown-out stock as a
DISCARD. The first count of a container is blind because no earlier number is
trustworthy. Nothing here commits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import contains_eager

from app import db
from app.inventory.printing import local_stamp
from app.inventory.ledger import (
    Actor, InventoryError, discard_stock, set_counted_quantity,
)
from app.models import (
    InvContainer, InvCount, InvCountLine, InvMovement, InvStationItem, InvStock, SupplyCategory,
    SupplyItem,
)
from app.models.constants import (
    INV_COUNT_LINE_APPLIED, INV_COUNT_LINE_STALE, INV_COUNT_LINE_UNCHANGED,
    INV_COUNT_LINE_UNDONE,
    INV_DISCARD_REASONS, INV_STATION_COUNT,
)

SERIALIZED_REFUSAL = "Count units at their own station."
ALREADY_FINISHED = "This count is already finished."
NOTHING_COUNTED = "Count at least one item first."
TOO_LARGE = "That number is too large."
MAX_QUANTITY = 100000


class CountFinished(InventoryError):
    """The count was saved already. The scanner clears its copy when `finished` is set."""

    finished = True


@dataclass(frozen=True)
class CountLineIn:
    """One item as the volunteer counted it. `kept` stays in the container."""
    item_id: int
    kept: int
    is_approximate: bool = False
    discarded: int = 0
    discard_reason: str | None = None


def _held_rows(container: InvContainer) -> list[InvStock]:
    return (db.session.query(InvStock).join(InvStock.item)
            .options(contains_eager(InvStock.item))
            .filter(InvStock.container_id == container.id, InvStock.quantity > 0,
                    SupplyItem.is_serialized.is_(False))
            .order_by(SupplyItem.item_name, SupplyItem.id).all())


def start_count(container: InvContainer, actor: Actor) -> InvCount:
    """Open a count and record what the ledger holds as each held item's expected quantity.

    A line with a NULL counted quantity is unanswered. Readers treat it as not counted.
    """
    count = InvCount(container_id=container.id, user_id=actor.user_id,
                     shift_id=actor.shift_id, device_id=actor.device_id,
                     started_at=datetime.utcnow())
    db.session.add(count)
    db.session.flush()
    for row in _held_rows(container):
        db.session.add(InvCountLine(count_id=count.id, item_id=row.item_id,
                                    expected_quantity=row.quantity))
    db.session.flush()
    return count


def not_undone():
    """Filter for counts that still stand; an undone count marks its lines UNDONE."""
    return ~InvCount.lines.any(InvCountLine.outcome == INV_COUNT_LINE_UNDONE)


def _is_blind(count: InvCount) -> bool:
    # An undone count leaves no trusted number behind, so it cannot end blindness.
    earlier = (db.session.query(InvCount.id)
               .filter(InvCount.container_id == count.container_id,
                       InvCount.submitted_at.isnot(None), InvCount.id != count.id,
                       not_undone()).first())
    return earlier is None


def _last_counted(count: InvCount) -> dict[int, datetime]:
    """Latest submit time per item over this container's other submitted counts.

    Only applied answers count; an untouched or stale item keeps its old date.
    """
    rows = (db.session.query(InvCountLine.item_id, func.max(InvCount.submitted_at))
            .join(InvCount, InvCount.id == InvCountLine.count_id)
            .filter(InvCount.container_id == count.container_id,
                    InvCount.submitted_at.isnot(None), InvCount.id != count.id,
                    InvCountLine.counted_quantity.isnot(None),
                    InvCountLine.outcome.isnot(INV_COUNT_LINE_STALE), not_undone())
            .group_by(InvCountLine.item_id).all())
    return dict(rows)


def count_payload(count: InvCount) -> dict:
    """Describe a count for the scanner. A blind count carries no quantities at all."""
    rows = _held_rows(count.container)
    blind = _is_blind(count)
    lines = []
    if not blind:
        last = _last_counted(count)
        lines = []
        for r in rows:
            line = {"item_id": r.item_id, "name": r.item.item_name, "expected": r.quantity,
                    "is_approximate": r.is_approximate}
            if last.get(r.item_id) is not None:
                line["last_counted"] = local_stamp(last[r.item_id])
            lines.append(line)
    usual = (db.session.query(InvStationItem).join(InvStationItem.item)
             .options(contains_eager(InvStationItem.item))
             .filter(InvStationItem.station_code == INV_STATION_COUNT,
                     SupplyItem.is_active.is_(True), SupplyItem.is_serialized.is_(False))
             .order_by(InvStationItem.position, InvStationItem.id).all())
    return {
        "count_id": count.id, "blind": blind, "lines": lines,
        "held": [{"item_id": r.item_id, "name": r.item.item_name,
                  "pack_size": r.item.pack_size} for r in rows],
        "usual": [{"item_id": u.item_id, "name": u.item.item_name,
                   "pack_size": u.item.pack_size} for u in usual],
    }


def _whole(value, minimum=0) -> bool:
    return type(value) is int and value >= minimum


def _validated(lines: list[CountLineIn]) -> dict[int, SupplyItem]:
    items = {}
    for line in lines:
        if line.item_id in items:
            raise InventoryError("Each item can be counted once.")
        item = db.session.get(SupplyItem, line.item_id) if type(line.item_id) is int else None
        if item is None:
            raise InventoryError("Pick an item to count.")
        if item.is_serialized:
            raise InventoryError(SERIALIZED_REFUSAL)
        if not _whole(line.kept) or not _whole(line.discarded):
            raise InventoryError("A count must be a whole number, zero or more.")
        if line.kept > MAX_QUANTITY or line.discarded > MAX_QUANTITY:
            raise InventoryError(TOO_LARGE)
        if line.discarded and line.discard_reason not in INV_DISCARD_REASONS:
            raise InventoryError(
                "A discard needs a reason: " + ", ".join(INV_DISCARD_REASONS) + ".")
        items[line.item_id] = item
    return items


def _is_stale(count: InvCount, item_id: int) -> bool:
    """True when this item moved in the container after the count started, by another write."""
    cid = count.container_id
    return (db.session.query(InvMovement.id)
            .filter(InvMovement.item_id == item_id,
                    InvMovement.created_at > count.started_at,
                    or_(InvMovement.count_id.is_(None), InvMovement.count_id != count.id),
                    or_(InvMovement.from_container_id == cid, InvMovement.to_container_id == cid))
            .first() is not None)


def submit_count(count: InvCount, lines: list[CountLineIn], actor: Actor) -> dict:
    """Apply the lines the volunteer touched. Returns `applied` and `stale` item names.

    Held items with no line are left alone: no movement, no counted value.
    A kept of 0 records an item as none. A stale line is skipped and recorded;
    the other lines still apply. Raises InventoryError on invalid input or no lines.
    """
    if count.submitted_at is not None:
        raise CountFinished(ALREADY_FINISHED)
    container = count.container
    if not container.is_active:
        raise InventoryError(f"{container.label_code} is inactive.")
    if not lines:
        raise InventoryError(NOTHING_COUNTED)
    items = _validated(lines)
    stored = {line.item_id: line for line in count.lines}
    live, stale = [], []
    for line in lines:
        row = stored.get(line.item_id)
        if row is None:
            row = InvCountLine(count_id=count.id, item_id=line.item_id, expected_quantity=0)
            db.session.add(row)
        row.is_approximate = line.is_approximate
        row.counted_quantity = line.kept + line.discarded
        row.discarded_quantity = line.discarded or None
        row.discard_reason = line.discard_reason if line.discarded else None
        if _is_stale(count, line.item_id):
            row.outcome = INV_COUNT_LINE_STALE
            stale.append(items[line.item_id].item_name)
        else:
            live.append((line, row))
    for line, row in live:
        moved = set_counted_quantity(items[line.item_id], container, line.kept + line.discarded,
                                     actor, count_id=count.id,
                                     is_approximate=line.is_approximate)
        row.outcome = INV_COUNT_LINE_APPLIED if moved or line.discarded \
            else INV_COUNT_LINE_UNCHANGED
    for line, _ in live:
        if line.discarded:
            discard_stock(items[line.item_id], container, line.discarded, line.discard_reason,
                          actor, count_id=count.id)
    count.submitted_at = datetime.utcnow()
    db.session.flush()
    return {"applied": len(live), "stale": stale}


def item_choice(item: SupplyItem) -> dict:
    return {"id": item.id, "name": item.item_name, "pack_size": item.pack_size}


def browse_categories() -> list[dict]:
    """Active categories that hold at least one active, non-serialized item."""
    rows = (db.session.query(SupplyCategory.id, SupplyCategory.name)
            .join(SupplyItem, SupplyItem.category_id == SupplyCategory.id)
            .filter(SupplyCategory.is_active.is_(True), SupplyItem.is_active.is_(True),
                    SupplyItem.is_serialized.is_(False))
            .group_by(SupplyCategory.id, SupplyCategory.name)
            .order_by(func.coalesce(SupplyCategory.sort_order, 1000000), SupplyCategory.name,
                      SupplyCategory.id).all())
    return [{"id": r.id, "name": r.name} for r in rows]


def browse_items(category_id: int) -> list[dict]:
    """Active, non-serialized items of one category by name. Carries no quantities."""
    rows = (db.session.query(SupplyItem)
            .filter(SupplyItem.category_id == category_id, SupplyItem.is_active.is_(True),
                    SupplyItem.is_serialized.is_(False))
            .order_by(SupplyItem.item_name, SupplyItem.id).all())
    return [item_choice(i) for i in rows]
