"""The look-up card: what a scan is, where it is, and what happened to it."""
from __future__ import annotations

from sqlalchemy import or_

from app import db
from app.inventory.flags import FLAG_WORDS
from app.inventory.notes import latest_note
from app.inventory.describe import describe_actor, describe_movement, unit_tag
from app.inventory.resolver import (
    KIND_CONTAINER, KIND_ITEM, KIND_UNIT, Resolution,
)
from app.inventory.unit_fields import unit_field_rows
from app.inventory.tree import display_name, path_text
from app.models import (
    EventCycle, InvCheck, InvContainer, InvFlag, InvMovement, InvPrepStatus, InvStock, InvUnit,
    SupplyItem,
)
from app.models.constants import (
    INV_CHECK_VOIDED, INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR,
    INV_CONDITION_RETIRED, INV_OWNERSHIP_RENTED, INV_PREP_CHECKED, INV_PREP_FAILED_CHECK,
    INV_PREP_VOIDED,
)

HISTORY_LIMIT = 10
STOCK_LIMIT = 5

_CONDITION_WORDS = {
    INV_CONDITION_IN_SERVICE: "In service", INV_CONDITION_REPAIR: "Needs repair",
    INV_CONDITION_RETIRED: "Retired", INV_CONDITION_LOST: "Lost",
}
_PREP_WORDS = {INV_PREP_CHECKED: "Checked", INV_PREP_FAILED_CHECK: "Failed check"}


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


def live_prep_row(unit: InvUnit, event_cycle) -> InvPrepStatus | None:
    """The unit's newest prep row for the event that no undo has voided.

    VOIDED rows stay as history. A row whose check was voided is skipped too, so
    undoing a re-check leaves the earlier valid check standing.
    """
    if event_cycle is None:
        return None
    return (db.session.query(InvPrepStatus)
            .outerjoin(InvCheck, InvCheck.id == InvPrepStatus.check_id)
            .filter(InvPrepStatus.unit_id == unit.id,
                    InvPrepStatus.event_cycle_id == event_cycle.id,
                    InvPrepStatus.status != INV_PREP_VOIDED,
                    or_(InvPrepStatus.check_id.is_(None),
                        InvCheck.status != INV_CHECK_VOIDED))
            .order_by(InvPrepStatus.id.desc()).first())


def _prep_line(unit: InvUnit, event: EventCycle | None) -> str:
    if event is None:
        return "Not checked"
    newest = live_prep_row(unit, event)
    return _PREP_WORDS.get(newest.status, "Not checked") if newest else "Not checked"


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


def _unit_card(unit: InvUnit, event: EventCycle | None) -> dict:
    lines = [_CONDITION_WORDS.get(unit.condition, unit.condition), _prep_line(unit, event)]
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
        return _unit_card(resolution.unit, event_cycle)
    if resolution.kind == KIND_ITEM:
        return _item_card(resolution.item)
    raise ValueError(f"No card for a {resolution.kind} scan")


def candidate_card(candidate, event_cycle: EventCycle | None) -> dict:
    """Card for one candidate of an ambiguous scan: a unit, or an item sharing a barcode."""
    if isinstance(candidate, InvUnit):
        return _unit_card(candidate, event_cycle)
    return _item_card(candidate)
