"""Unit station rules: one pass over a unit on a CT60 scanner.

Find the unit, give it a number if it has none, check it, place it in the
scanner's home container and queue its info label. Nothing here commits; the
caller does, so a refusal rolls the whole pass back.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import selectinload

from app import db
from app.inventory.checks import finish_check, put_away, unit_code
from app.inventory.labels import PREFIX_UNIT, allocate
from app.inventory.ledger import (
    NUMBER_KINDS, Actor, InventoryError, add_identifier, move_unit, savepoint,
)
from app.inventory.printing import fields_for_unit, last_job_for, unit_number
from app.inventory.receive import label_job
from app.inventory.unit_fields import spec_line
from app.models import (
    InvCheck, InvContainer, InvGearKind, InvUnit, InvUnitIdentifier, InvUnitValue, SupplyItem,
)
from app.models.constants import (
    INV_CHECK_FAILED, INV_CHECK_PASSED, INV_CONDITION_RETIRED, INV_IDENT_FESTOPS_LABEL,
    INV_JOB_FAILED, INV_OWNERSHIP_OWNED,
)

NOT_THE_LAST = "Scan the unit again, then its pallet."
RETIRED_NOT_PLACED = "A retired unit is not placed."


def digit_candidates(digits: str, limit: int = 10) -> list[InvUnit]:
    """Live, unnumbered units whose name ends in the typed number.

    Matches the last digit run of the name as an integer, so "3" finds TV-X-003
    and not TV-X-013. A unit holding any number, a U- label included, is found
    by scanning that label instead.
    """
    digits = (digits or "").strip()
    if not digits.isascii() or not digits.isdigit():
        return []
    stripped = digits.lstrip("0")
    if not stripped:
        return []
    numbered = (db.session.query(InvUnitIdentifier.id)
                .filter(InvUnitIdentifier.unit_id == InvUnit.id,
                        InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                        InvUnitIdentifier.retired_at.is_(None)).exists())
    # LIKE only narrows the rows; the integer comparison below decides.
    rows = (db.session.query(InvUnit)
            .options(selectinload(InvUnit.item).selectinload(SupplyItem.gear_kind)
                     .selectinload(InvGearKind.fields),
                     selectinload(InvUnit.values).selectinload(InvUnitValue.field),
                     selectinload(InvUnit.values).selectinload(InvUnitValue.choice))
            .filter(InvUnit.name_key.like(f"%{stripped}%"),
                    InvUnit.condition != INV_CONDITION_RETIRED, ~numbered)
            .order_by(InvUnit.name_key, InvUnit.id).all())
    wanted = int(stripped)
    found = []
    for unit in rows:
        runs = re.findall(r"\d+", unit.name or "")
        if runs and int(runs[-1]) == wanted:
            found.append(unit)
            if len(found) == limit:
                break
    return found


def number_refusal(unit: InvUnit) -> str | None:
    """Why this unit cannot get a new U- number, or None when it can."""
    if unit.condition == INV_CONDITION_RETIRED:
        return "A retired unit cannot be given a number."
    if unit.ownership != INV_OWNERSHIP_OWNED:
        return "A rented unit cannot be given a U- number."
    # Queried, not read from unit.identifiers, which may be loaded and stale.
    if (db.session.query(InvUnitIdentifier.id)
            .filter(InvUnitIdentifier.unit_id == unit.id,
                    InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                    InvUnitIdentifier.retired_at.is_(None)).first()):
        return "This unit already has a number."
    return None


def give_unit_number(unit: InvUnit) -> str:
    """Add a new U- label to a unit with no number and return its code."""
    refusal = number_refusal(unit)
    if refusal:
        raise InventoryError(refusal)
    code = allocate(PREFIX_UNIT, 1)[0]
    # allocate holds the sequence lock, so a second click reaches add_identifier after
    # the first commits and is refused there; replace_number would swap the number.
    add_identifier(unit, INV_IDENT_FESTOPS_LABEL, code)
    return code


def needs_info_label(unit: InvUnit) -> bool:
    """True when the unit has a number and no current, usable info label job."""
    number = unit_number(unit)
    if number is None:
        return False
    job = last_job_for(unit=unit)
    return (job is None or job.status == INV_JOB_FAILED or job.code != number
            or job.fields != fields_for_unit(unit))


def finish_unit(check: InvCheck, answers: dict, actor: Actor, home: InvContainer | None,
                failed_condition: str | None = None, note: str | None = None,
                line_ids: list | None = None, client_request_id: str | None = None) -> dict:
    """Finish the check, then place the unit at `home` and queue its info label.

    A label failure, such as no print route, does not undo the check; its text comes
    back in `label`. A retired unit is neither placed nor labelled.
    """
    unit = check.unit
    # Retiring retires the numbers, so the code is read first.
    code = unit_code(unit)
    check = finish_check(check, answers, actor, failed_condition, note, line_ids)
    placed = None
    label = "none"
    if unit.condition != INV_CONDITION_RETIRED:
        if home is not None:
            if unit.current_container_id != home.id:
                move_unit(unit, home, actor)
            placed = home.name
        if needs_info_label(unit):
            try:
                with savepoint():
                    label_job(unit, actor, client_request_id)
                label = "queued"
            except InventoryError as error:
                label = str(error)
    return {"status": check.status, "condition": unit.condition, "code": code,
            "name": unit.name or code, "placed": placed, "label": label}


def place_last(actor: Actor, container_code: str, unit_id: int) -> str:
    """Put the unit this scanner finished last into a scanned container; return its name."""
    query = db.session.query(InvCheck).filter(
        InvCheck.status.in_((INV_CHECK_PASSED, INV_CHECK_FAILED)))
    if actor.shift_id is not None:
        query = query.filter(InvCheck.device_id == actor.device_id,
                             InvCheck.shift_id == actor.shift_id)
    else:
        query = query.filter(InvCheck.performed_by_user_id == actor.user_id,
                             InvCheck.shift_id.is_(None))
    newest = query.order_by(InvCheck.id.desc()).first()
    if newest is None or newest.unit_id != unit_id:
        raise InventoryError(NOT_THE_LAST)
    unit = newest.unit
    if unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError(RETIRED_NOT_PLACED)
    refusal = put_away(unit, container_code, actor)
    if refusal:
        raise InventoryError(refusal)
    return unit.current_container.name


def unit_card(unit: InvUnit) -> dict:
    """The facts the station shows for a unit."""
    code = unit_code(unit)
    return {"unit_id": unit.id, "name": unit.name or code, "type": unit.item.item_name,
            "spec": spec_line(unit), "code": code}
