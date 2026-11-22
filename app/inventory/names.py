"""Unit names. A name is unique among live units, compared by name_key."""
from __future__ import annotations

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvUnit
from app.models.constants import INV_CONDITION_RETIRED

MAX_NAME_LENGTH = 64


def name_key(raw: str | None) -> str | None:
    """Trim, collapse inner whitespace and casefold; None for a blank name.

    Writes and searches both use this, so a search matches what uniqueness compares.
    """
    collapsed = " ".join((raw or "").split())
    return collapsed.casefold() or None


def set_unit_name(unit: InvUnit, raw: str | None) -> bool:
    """Set or clear a unit's name; return True when it changed.

    The partial unique index on name_key backstops a race; the query here
    gives the volunteer the message first.
    """
    name = " ".join((raw or "").split()) or None
    if name is not None and len(name) > MAX_NAME_LENGTH:
        raise InventoryError(f"A name can be at most {MAX_NAME_LENGTH} characters.")
    key = name_key(name)
    if name == unit.name:
        return False
    if key is not None:
        query = (db.session.query(InvUnit)
                 .filter(InvUnit.name_key == key,
                         InvUnit.condition != INV_CONDITION_RETIRED))
        if unit.id is not None:
            query = query.filter(InvUnit.id != unit.id)
        holder = query.first()
        if holder is not None:
            raise InventoryError(f"{holder.name} is already the name of another asset.")
    unit.name = name
    unit.name_key = key
    return True


def find_by_name(text: str, limit: int = 20) -> list[InvUnit]:
    """Live units whose name contains the text, ordered by name."""
    key = name_key(text)
    if key is None:
        return []
    escaped = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return (db.session.query(InvUnit)
            .filter(InvUnit.name_key.like(f"%{escaped}%", escape="\\"),
                    InvUnit.condition != INV_CONDITION_RETIRED)
            .order_by(InvUnit.name_key, InvUnit.id).limit(limit).all())
