"""Compare the InvStock cache with the movement log.

Reports only. A mismatch means a write bypassed the ledger, and the fix is a
count, so nothing here repairs a row.
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func

from app import db
from app.models import InvMovement, InvStock


def find_balance_mismatches() -> list[dict]:
    from_log: dict[tuple[int, int], int] = defaultdict(int)
    counted = InvMovement.quantity.isnot(None)
    for item_id, container_id, total in (
        db.session.query(InvMovement.item_id, InvMovement.to_container_id,
                         func.sum(InvMovement.quantity))
        .filter(counted, InvMovement.to_container_id.isnot(None))
        .group_by(InvMovement.item_id, InvMovement.to_container_id)
    ):
        from_log[(item_id, container_id)] += total
    for item_id, container_id, total in (
        db.session.query(InvMovement.item_id, InvMovement.from_container_id,
                         func.sum(InvMovement.quantity))
        .filter(counted, InvMovement.from_container_id.isnot(None))
        .group_by(InvMovement.item_id, InvMovement.from_container_id)
    ):
        from_log[(item_id, container_id)] -= total

    mismatches, seen = [], set()
    for row in db.session.query(InvStock):
        key = (row.item_id, row.container_id)
        seen.add(key)
        if row.quantity != from_log.get(key, 0):
            mismatches.append({"item_id": row.item_id, "container_id": row.container_id,
                               "cached": row.quantity, "from_log": from_log.get(key, 0)})
    for (item_id, container_id), total in from_log.items():
        if (item_id, container_id) not in seen and total != 0:
            mismatches.append({"item_id": item_id, "container_id": container_id,
                               "cached": None, "from_log": total})
    return mismatches
