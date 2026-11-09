"""Unit checks, check lines for a model, and the balance report.

A check records answers to a model's checklist and the unit's prep status for the
default event. It never moves a unit; only the optional put-away does. Nothing
here commits. The balance report compares the InvStock cache with the movement log. It
reports only. A mismatch means a write bypassed the ledger, and the fix is a
count, so nothing here repairs a row.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import func

from app import db
from app.inventory import labels
from app.inventory.cards import default_event
from app.inventory.codes import normalize_code
from app.inventory.describe import unit_tag
from app.inventory.last_check import latest_check_rows
from app.inventory.ledger import (
    NUMBER_KINDS, Actor, InventoryError, move_unit, set_condition,
)
from app.inventory.printing import local_stamp, unit_number
from app.inventory.receive import MAX_SERIAL, container_for_code
from app.inventory.resolver import KIND_ITEM, resolve
from app.models import (
    InvCheck, InvCheckAnswer, InvCheckItem, InvMovement, InvPrepStatus, InvStock, InvUnit,
    InvUnitIdentifier,
)
from app.models.constants import (
    INV_CHECK_FAILED, INV_CHECK_OPEN, INV_CHECK_PASSED, INV_CHECK_SET_ASIDE,
    INV_CHECK_STATIONS, INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR,
    INV_CONDITION_RETIRED, INV_IDENT_SERIAL, INV_PREP_CHECKED, INV_PREP_FAILED_CHECK,
)

NO_EVENT = "No default event is set. Ask an inventory admin."
NOTE_MAX = InvCheck.note.type.length
FAILED_CONDITIONS = (INV_CONDITION_REPAIR, INV_CONDITION_RETIRED)


def active_check_items(item) -> list[InvCheckItem]:
    """Return the model's active check lines in position order."""
    return (db.session.query(InvCheckItem)
            .filter_by(item_id=item.id, is_active=True)
            .order_by(InvCheckItem.position, InvCheckItem.id)
            .all())


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


class CheckFinished(InventoryError):
    """The check is no longer open. The scanner drops it and starts over."""

    finished = True


class CheckBlocked(InventoryError):
    """Another unit's check is open on this shift. `extra` rides in the answer."""

    def __init__(self, check: InvCheck):
        who = unit_phrase(check.unit)
        super().__init__(f"{who[0].upper()}{who[1:]} is not finished. Finish it, or set it aside?")
        self.extra = {"open_check": {"check_id": check.id, "code": unit_code(check.unit),
                                     "name": check.unit.item.item_name}}


class ChecklistChanged(InventoryError):
    """The model's checklist changed after the screen loaded it. Rescanning reloads the lines."""

    def __init__(self):
        super().__init__("That checklist changed. Scan the unit again.")
        self.extra = {"changed": True}


def _default_event():
    event = default_event()
    if event is None:
        raise InventoryError(NO_EVENT)
    return event


def unit_code(unit: InvUnit) -> str | None:
    """The unit's number, or its best other identifier; None when it has neither."""
    return unit_number(unit) or unit_tag(unit)


def unit_phrase(unit: InvUnit) -> str:
    """Name a unit inside a sentence: its code, else its name, else a plain phrase."""
    return unit_code(unit) or unit.name or "a unit with no number yet"


def open_check_for(actor: Actor) -> InvCheck | None:
    query = db.session.query(InvCheck).filter(InvCheck.status == INV_CHECK_OPEN)
    if actor.shift_id is not None:
        query = query.filter(InvCheck.device_id == actor.device_id,
                             InvCheck.shift_id == actor.shift_id)
    else:
        query = query.filter(InvCheck.performed_by_user_id == actor.user_id,
                             InvCheck.shift_id.is_(None))
    return query.order_by(InvCheck.id.desc()).first()


def blocking_check(actor: Actor, unit: InvUnit) -> InvCheck | None:
    """The actor's open check on a different unit, or None. The same unit resumes, so it never blocks."""
    open_one = open_check_for(actor)
    return open_one if open_one is not None and open_one.unit_id != unit.id else None


def open_check(unit: InvUnit, station_code: str, actor: Actor, event_cycle) -> InvCheck:
    """Open a check, or return this shift's open check on the same unit.

    Refuses when another unit's check is open on this shift, naming that unit.
    """
    default = _default_event()
    event = event_cycle or default
    if station_code not in INV_CHECK_STATIONS:
        raise InventoryError(f"{station_code or 'That'} is not a check station.")
    if unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError("That unit is retired.")
    _set_aside_ended_shifts(actor)
    other = blocking_check(actor, unit)
    if other is not None:
        raise CheckBlocked(other)
    existing = open_check_for(actor)
    if existing is not None:
        return existing
    check = InvCheck(unit_id=unit.id, event_cycle_id=event.id, station_code=station_code,
                     status=INV_CHECK_OPEN, performed_by_user_id=actor.user_id,
                     shift_id=actor.shift_id, device_id=actor.device_id)
    db.session.add(check)
    db.session.flush()
    return check


def _set_aside_ended_shifts(actor: Actor) -> None:
    """Close this device's checks left OPEN by a shift that has ended; they can never finish."""
    if actor.device_id is None or actor.shift_id is None:
        return
    for stale in (db.session.query(InvCheck)
                  .filter(InvCheck.status == INV_CHECK_OPEN, InvCheck.device_id == actor.device_id,
                          InvCheck.shift_id != actor.shift_id).all()):
        stale.status = INV_CHECK_SET_ASIDE
        stale.finished_at = datetime.utcnow()
    db.session.flush()


def _locked(check: InvCheck) -> InvCheck:
    return (db.session.query(InvCheck).filter_by(id=check.id)
            .with_for_update().populate_existing().one())


def _require_open(check: InvCheck) -> None:
    if check.status != INV_CHECK_OPEN:
        raise CheckFinished("This check is already finished.")


def set_aside(check: InvCheck, actor: Actor) -> None:
    """Close an open check without a result. The unit stays unchecked."""
    _default_event()
    check = _locked(check)
    _require_open(check)
    check.status = INV_CHECK_SET_ASIDE
    check.finished_at = datetime.utcnow()
    db.session.flush()


def _clean_serial(raw) -> tuple[str, str]:
    text = raw.strip() if isinstance(raw, str) else ""
    if not text:
        raise InventoryError("Scan the serial barcode.")
    if len(text) > MAX_SERIAL:
        raise InventoryError(f"That serial is longer than {MAX_SERIAL} characters.")
    return text, normalize_code(text)


def _units_with_serial(normalized: str, exclude: InvUnit | None = None) -> list[InvUnit]:
    query = (db.session.query(InvUnit)
             .join(InvUnitIdentifier, InvUnitIdentifier.unit_id == InvUnit.id)
             .filter(InvUnitIdentifier.kind == INV_IDENT_SERIAL,
                     InvUnitIdentifier.normalized_value == normalized,
                     InvUnitIdentifier.retired_at.is_(None),
                     InvUnit.condition != INV_CONDITION_RETIRED))
    if exclude is not None:
        query = query.filter(InvUnit.id != exclude.id)
    return list(dict.fromkeys(query.order_by(InvUnit.id).all()))


def _is_a_number(normalized: str) -> bool:
    return (db.session.query(InvUnitIdentifier.id)
            .filter(InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                    InvUnitIdentifier.normalized_value == normalized,
                    InvUnitIdentifier.retired_at.is_(None)).first() is not None)


def _require_serial_shape(raw: str, normalized: str) -> None:
    """Refuse a scan that is a unit number or a model barcode before anything is written.

    A mis-scan must not turn a tag or a UPC into a serial.
    """
    if labels.parse(normalized) is not None or _is_a_number(normalized):
        raise InventoryError(f"{raw} is a unit number, not a serial. Scan the SERIAL.")
    if resolve(raw).kind == KIND_ITEM:
        raise InventoryError("That is the model barcode. Scan the serial (S/N).")


def checked_serial(unit: InvUnit, raw) -> str:
    """Return a serial an admin may add to this unit, or refuse with the scanner's message.

    Refuses label-shaped codes, unit numbers, model barcodes and a serial another
    active unit holds. The caller adds the identifier.
    """
    text, normalized = _clean_serial(raw)
    _require_serial_shape(text, normalized)
    holders = _units_with_serial(normalized, exclude=unit)
    if holders:
        raise InventoryError(f"{text} belongs to {unit_phrase(holders[0])}. Nothing was changed.")
    return text


def last_result(unit: InvUnit) -> dict | None:
    """The unit's latest check from any event, returned only when it passed."""
    prep = latest_check_rows([unit.id]).get(unit.id)
    if prep is None or prep.status != INV_PREP_CHECKED or prep.check_id is None:
        return None
    check = db.session.get(InvCheck, prep.check_id)
    if check is None or check.status != INV_CHECK_PASSED:
        return None
    return {"result": "Passed", "when": local_stamp(check.finished_at)}


def _joined_note(parts: list[str]) -> str | None:
    text = "; ".join(p for p in parts if p)
    if len(text) > NOTE_MAX:
        text = text[:NOTE_MAX - 3] + "..."
    return text or None


def finish_check(check: InvCheck, answers: dict, actor: Actor,
                 failed_condition: str | None = None, note: str | None = None,
                 line_ids: list | None = None) -> InvCheck:
    """Finish an open check from its answers and write the prep status.

    Every active line needs YES or NO. A NO on a FAIL line fails the check, which
    then needs a condition; a NO on a NOTE line passes and records the prompt. A
    model with no lines passes, or fails when `failed_condition` is sent.
    A pass returns a REPAIR or LOST unit to IN_SERVICE. `line_ids` lists the lines the
    screen showed; a different active set raises ChecklistChanged.
    """
    _default_event()
    check = _locked(check)
    _require_open(check)
    unit = check.unit
    if unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError("That unit is retired.")
    lines = active_check_items(unit.item)
    by_id = {line.id: line for line in lines}
    if not isinstance(answers, dict) or any(k not in by_id for k in answers):
        raise ChecklistChanged()
    if line_ids is not None and set(line_ids) != set(by_id):
        raise ChecklistChanged()
    if any(answers.get(line.id) not in ("YES", "NO") for line in lines):
        raise InventoryError("Answer every line.")
    clean_note = note.strip() if isinstance(note, str) and note.strip() else None
    if lines:
        failed = any(line.missing_means == "FAIL" and answers[line.id] == "NO" for line in lines)
    else:
        failed = failed_condition is not None
    if not failed and failed_condition is not None:
        raise ChecklistChanged()
    if failed and failed_condition not in FAILED_CONDITIONS:
        raise InventoryError("A failed check needs one more answer: repair or retire.")
    noted = [line.prompt for line in lines
             if line.missing_means == "NOTE" and answers[line.id] == "NO"]
    for line in lines:
        db.session.add(InvCheckAnswer(
            check_id=check.id, check_item_id=line.id, answer=answers[line.id],
            note=line.prompt if line.prompt in noted else None))
    check.note = _joined_note([check.note] + noted + [clean_note])
    check.status = INV_CHECK_FAILED if failed else INV_CHECK_PASSED
    check.finished_at = datetime.utcnow()
    check.previous_condition = unit.condition
    if failed:
        set_condition(unit, failed_condition)
    elif unit.condition in (INV_CONDITION_REPAIR, INV_CONDITION_LOST):
        set_condition(unit, INV_CONDITION_IN_SERVICE)
    db.session.add(InvPrepStatus(
        unit_id=unit.id, event_cycle_id=check.event_cycle_id,
        status=INV_PREP_FAILED_CHECK if failed else INV_PREP_CHECKED, check_id=check.id,
        performed_by_user_id=actor.user_id, shift_id=actor.shift_id, device_id=actor.device_id))
    db.session.flush()
    return check


def put_away(unit: InvUnit, container_code: str, actor: Actor) -> str | None:
    """Move a checked unit to a scanned container; return the refusal text, or None on success."""
    try:
        move_unit(unit, container_for_code(container_code), actor)
    except InventoryError as exc:
        return str(exc)
    return None
