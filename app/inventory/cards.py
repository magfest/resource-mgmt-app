"""The look-up card: what a scan is, where it is, and what happened to it."""
from __future__ import annotations

from app import db
from app.inventory.flags import FLAG_WORDS
from app.inventory.history import CONDITION_WORDS
from app.inventory.last_check import CHECK_WORDS, check_word, latest_check_rows
from app.inventory.notes import latest_note
from app.inventory.describe import describe_actor, describe_movement, unit_tag
from app.inventory.resolver import (
    KIND_CONTAINER, KIND_ITEM, KIND_UNIT, Resolution,
)
from app.inventory.unit_fields import unit_field_rows
from app.inventory.printing import local_day
from app.inventory.tree import display_name, path_text
from app.models import (
    EventCycle, InvContainer, InvFlag, InvMovement, InvStock, InvUnit, SupplyItem,
)
from app.models.constants import INV_OWNERSHIP_RENTED

HISTORY_LIMIT = 10
STOCK_LIMIT = 5


def default_event() -> EventCycle | None:
    """The inventory event: the cycle marked default, or None when none is."""
    return (db.session.query(EventCycle)
            .filter(EventCycle.is_default.is_(True), EventCycle.is_active.is_(True))
            .order_by(EventCycle.id).first())


def _history(*criteria) -> list[dict]:
    rows = (db.session.query(InvMovement).filter(*criteria)
            .order_by(InvMovement.created_at.desc(), InvMovement.id.desc())
            .limit(HISTORY_LIMIT).all())
    return [{"text": describe_movement(m), "who": describe_actor(m),
             "when": f"{m.created_at:%b %d %H:%M} UTC"} for m in rows]


def _flag_lines(*criteria) -> list[str]:
    flags = (db.session.query(InvFlag).filter(InvFlag.resolved_at.is_(None), *criteria)
             .order_by(InvFlag.id).all())
    return [f"Flag: {FLAG_WORDS.get(f.reason, f.reason)}" + (f", {f.note}" if f.note else "")
            for f in flags]


def _check_line(unit: InvUnit) -> str:
    prep = latest_check_rows([unit.id]).get(unit.id)
    word = check_word(prep)
    if word is None:
        return "Not checked"
    return f"{CHECK_WORDS[word]} \u00b7 {local_day(prep.created_at)}"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _container_card(container: InvContainer) -> dict:
    children = db.session.query(InvContainer).filter_by(parent_id=container.id).count()
    units = db.session.query(InvUnit).filter_by(current_container_id=container.id).count()
    lines = [container.kind.name]
    if not container.is_active:
        lines.append("Inactive")
    lines.append(f"Holds {_plural(children, 'container')} and {_plural(units, 'unit')}")
    lines += _flag_lines(InvFlag.container_id == container.id)
    return {"title": display_name(container), "tag": container.label_code,
            "path": path_text(container), "lines": lines,
            "history": _history(InvMovement.subject_container_id == container.id)}


def _unit_card(unit: InvUnit) -> dict:
    lines = [CONDITION_WORDS.get(unit.condition, unit.condition), _check_line(unit)]
    if unit.ownership == INV_OWNERSHIP_RENTED:
        lines.append(f"Rented from {unit.vendor_name}" if unit.vendor_name else "Rented")
    if not unit.item.is_active:
        lines.append("Inactive")
    lines += _flag_lines(InvFlag.unit_id == unit.id)
    container = unit.current_container
    fields = [[field.name, text] for field, text in unit_field_rows(unit) if text]
    return {"title": unit.item.item_name, "tag": unit_tag(unit) or "",
            "numberless": unit_tag(unit) is None, "name": unit.name, "fields": fields, "notes": note.body if (note := latest_note(unit.id)) else None,
            "path": path_text(container) if container else "Not placed yet",
            "lines": lines, "history": _history(InvMovement.unit_id == unit.id)}


def _item_card(item: SupplyItem) -> dict:
    lines = []
    if not item.is_active:
        lines.append("Inactive")
    if item.is_serialized:
        lines.append("Tracked by unit; scan a unit's tag")
    else:
        lines.append("Counted by quantity")
        stock = (db.session.query(InvStock).filter(InvStock.item_id == item.id,
                                                   InvStock.quantity > 0)
                 .order_by(InvStock.quantity.desc(), InvStock.id).limit(STOCK_LIMIT).all())
        lines += [f"{s.container.label_code}: {'about ' if s.is_approximate else ''}{s.quantity}"
                  for s in stock]
    return {"title": item.item_name, "tag": item.barcode or "", "path": "",
            "lines": lines,
            "history": _history(InvMovement.item_id == item.id, InvMovement.unit_id.is_(None),
                                InvMovement.subject_container_id.is_(None))}


def card_for(resolution: Resolution, event_cycle: EventCycle | None) -> dict:
    """Build the card for a resolved container, unit, or item.

    Keys: `title`, `tag`, `path`, `lines` (strings), `history` (newest first, at most 10).
    A unit card adds `name`, `fields` ([name, text] pairs, set values only) and `notes`.
    """
    if resolution.kind == KIND_CONTAINER:
        return _container_card(resolution.container)
    if resolution.kind == KIND_UNIT:
        return _unit_card(resolution.unit)
    if resolution.kind == KIND_ITEM:
        return _item_card(resolution.item)
    raise ValueError(f"No card for a {resolution.kind} scan")


def candidate_card(candidate, event_cycle: EventCycle | None) -> dict:
    """Card for one candidate of an ambiguous scan: a unit, or an item sharing a barcode."""
    if isinstance(candidate, InvUnit):
        return _unit_card(candidate)
    return _item_card(candidate)
