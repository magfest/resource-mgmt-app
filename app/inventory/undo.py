"""Undo the last finished check or submitted count on a shift.

Undo writes compensating rows and deletes nothing. The window is 30 seconds from
the finish and closes on any other write by the same shift, except a flag. The
scanner also closes it on the next scan; the server sees only writes, so a scan
that writes nothing leaves the server window open until the 30 seconds pass.
Nothing here commits.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.checks import display_number
from app.inventory.counts import not_undone
from app.inventory.ledger import (
    NUMBER_KINDS, Actor, InventoryError, _active_numbers, _is_taken, _locked_stock,
    reverse_movement,
    set_condition,
)
from app.models import (
    InvCheck, InvCount, InvMovement, InvPrepStatus, InvPrintJob, InvShift,
    InvUnit, InvUnitIdentifier,
)
from app.models.constants import (
    INV_ACTION_ADD, INV_ACTION_MOVE, INV_CHECK_FAILED, INV_CHECK_PASSED, INV_CHECK_VOIDED,
    INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR, INV_CONDITION_RETIRED,
    INV_COUNT_LINE_APPLIED, INV_COUNT_LINE_UNCHANGED, INV_COUNT_LINE_UNDONE,
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_SERIAL, INV_IDENT_UNIQUE_KINDS, INV_IDENT_VENDOR_TAG, INV_OWNERSHIP_OWNED,
    INV_PREP_CHECKED, INV_PREP_FAILED_CHECK, INV_PREP_VOIDED,
)

UNDO_WINDOW = timedelta(seconds=30)
TOO_LATE = "Too late to undo."
CHANGED = "Something changed since; nothing was undone."
NOTHING = "Nothing to undo."


def _actor(who) -> Actor:
    if isinstance(who, InvShift):
        return Actor(shift_id=who.id, device_id=who.device_id)
    return who


def _mine(model, actor: Actor):
    """Filter rows written by this shift, or by this signed-in user outside any shift."""
    user_column = model.user_id if model is InvCount else model.performed_by_user_id
    if actor.shift_id is not None:
        return and_(model.shift_id == actor.shift_id, model.device_id == actor.device_id)
    return and_(user_column == actor.user_id, model.shift_id.is_(None))


def _newest_check(actor: Actor) -> InvCheck | None:
    return (db.session.query(InvCheck)
            .filter(_mine(InvCheck, actor), InvCheck.finished_at.isnot(None),
                    InvCheck.status.in_((INV_CHECK_PASSED, INV_CHECK_FAILED, INV_CHECK_VOIDED)))
            .order_by(InvCheck.finished_at.desc(), InvCheck.id.desc()).first())


def _newest_count(actor: Actor) -> InvCount | None:
    return (db.session.query(InvCount)
            .filter(_mine(InvCount, actor), InvCount.submitted_at.isnot(None))
            .order_by(InvCount.submitted_at.desc(), InvCount.id.desc()).first())


def _other_write_since(actor: Actor, stamp: datetime, own_unit_id=None, own_count_id=None) -> bool:
    """True when this shift wrote anything but a flag after `stamp`.

    A movement on the check's own unit is its put-away, and a movement of the
    count's own is part of the count; neither ends the window.
    """
    moves = db.session.query(InvMovement.id).filter(_mine(InvMovement, actor),
                                                    InvMovement.created_at > stamp)
    if own_unit_id is not None:
        moves = moves.filter(or_(InvMovement.unit_id.is_(None),
                                 InvMovement.unit_id != own_unit_id))
    if own_count_id is not None:
        moves = moves.filter(or_(InvMovement.count_id.is_(None),
                                 InvMovement.count_id != own_count_id))
    checks = db.session.query(InvCheck.id).filter(
        _mine(InvCheck, actor), or_(InvCheck.started_at > stamp, InvCheck.finished_at > stamp))
    counts = db.session.query(InvCount.id).filter(
        _mine(InvCount, actor), or_(InvCount.started_at > stamp, InvCount.submitted_at > stamp))
    jobs = db.session.query(InvPrintJob.id).filter(_mine(InvPrintJob, actor),
                                                   InvPrintJob.created_at > stamp)
    return any(q.first() is not None for q in (moves, checks, counts, jobs))


def undo_last(who, now: datetime | None = None) -> dict:
    """Undo the newest finished check or submitted count of a shift or signed-in user.

    Accepts an InvShift or an Actor. Returns `{"undone": "check" | "count", "message"}`.
    Raises InventoryError when nothing qualifies, the window has closed, or
    something changed since; a refusal writes nothing.
    """
    actor = _actor(who)
    now = now or datetime.utcnow()
    check, count = _newest_check(actor), _newest_count(actor)
    if check is None and count is None:
        raise InventoryError(NOTHING)
    use_check = count is None or (check is not None and check.finished_at >= count.submitted_at)
    if use_check:
        check = (db.session.query(InvCheck).filter_by(id=check.id)
                 .with_for_update().populate_existing().one())
        if check.status == INV_CHECK_VOIDED:
            raise InventoryError(NOTHING)
        _require_window(now, check.finished_at,
                        _other_write_since(actor, check.finished_at, own_unit_id=check.unit_id))
        return _undo_check(check, actor)
    count = (db.session.query(InvCount).filter_by(id=count.id)
             .with_for_update().populate_existing().one())
    if not db.session.query(InvCount.id).filter(InvCount.id == count.id, not_undone()).first():
        raise InventoryError(NOTHING)
    _require_window(now, count.submitted_at,
                    _other_write_since(actor, count.submitted_at, own_count_id=count.id))
    return _undo_count(count, actor)


def _require_window(now: datetime, finished: datetime, other_write: bool) -> None:
    if now - finished > UNDO_WINDOW or other_write:
        raise InventoryError(TOO_LATE)


# ---- checks ----

def _expected_condition(check: InvCheck, unit: InvUnit, retired: list) -> bool:
    """True when the unit still has the condition this check left it in.

    A failed check stores no failed condition. RETIRED shows as identifiers this
    check retired; REPAIR is the only other failed condition.
    """
    if check.status == INV_CHECK_FAILED:
        if retired:
            return unit.condition == INV_CONDITION_RETIRED
        return unit.condition == INV_CONDITION_REPAIR
    previous = check.previous_condition
    if previous in (INV_CONDITION_REPAIR, INV_CONDITION_LOST):
        return unit.condition == INV_CONDITION_IN_SERVICE
    return unit.condition == previous


def _retired_by(check: InvCheck, unit: InvUnit, prep: InvPrepStatus) -> list[InvUnitIdentifier]:
    """The identifiers this check's RETIRED result retired.

    finish_check stamps finished_at, then set_condition stamps each retirement,
    then the prep row is created. Retirements inside that span belong to this
    check; earlier ones predate it and stay retired.
    """
    if check.status != INV_CHECK_FAILED or unit.condition != INV_CONDITION_RETIRED:
        return []
    return (db.session.query(InvUnitIdentifier)
            .filter(InvUnitIdentifier.unit_id == unit.id,
                    InvUnitIdentifier.retired_at.isnot(None),
                    InvUnitIdentifier.retired_at >= check.finished_at,
                    InvUnitIdentifier.retired_at <= prep.created_at)
            .order_by(InvUnitIdentifier.id).all())


def _reactivation_refusal(unit: InvUnit, identifiers: list[InvUnitIdentifier]) -> str | None:
    """Return the refusal text when re-activating these identifiers would clash, else None."""
    for identifier in identifiers:
        if identifier.kind in (INV_IDENT_SERIAL, INV_IDENT_MAC):
            # Not unique in the schema, so a second holder would go unnoticed.
            holder = (db.session.query(InvUnit.id)
                      .join(InvUnitIdentifier, InvUnitIdentifier.unit_id == InvUnit.id)
                      .filter(InvUnitIdentifier.kind == identifier.kind,
                              InvUnitIdentifier.normalized_value == identifier.normalized_value,
                              InvUnitIdentifier.retired_at.is_(None),
                              InvUnit.condition != INV_CONDITION_RETIRED,
                              InvUnit.id != unit.id).first())
            if holder is not None:
                return f"{identifier.value} is now on another unit; nothing was undone."
        unique = identifier.kind in INV_IDENT_UNIQUE_KINDS or identifier.kind == INV_IDENT_VENDOR_TAG
        if unique and _is_taken(unit, identifier.kind, identifier.normalized_value):
            return CHANGED
        if identifier.kind in NUMBER_KINDS and unit.ownership == INV_OWNERSHIP_OWNED \
                and _active_numbers(unit):
            return CHANGED
    return None


def _stays_text(check: InvCheck, unit: InvUnit, actor: Actor) -> str:
    """Name what undo leaves in place: a put-away, a serial change, a new or bound unit."""
    parts = []
    moved = (db.session.query(InvMovement.id)
             .filter(_mine(InvMovement, actor), InvMovement.unit_id == unit.id,
                     InvMovement.action == INV_ACTION_MOVE,
                     InvMovement.created_at >= check.finished_at).first())
    if moved is not None and unit.current_container is not None:
        parts.append(f"It stays in {unit.current_container.label_code}.")
    if check.note and "Serial changed from" in check.note:
        parts.append("The serial change stays.")
    if actor.shift_id is not None:
        shift = db.session.get(InvShift, actor.shift_id)
        created = (db.session.query(InvMovement.id)
                   .filter(_mine(InvMovement, actor), InvMovement.unit_id == unit.id,
                           InvMovement.action == INV_ACTION_ADD).first())
        bound = (db.session.query(InvUnitIdentifier.id)
                 .filter(InvUnitIdentifier.unit_id == unit.id,
                         InvUnitIdentifier.kind == INV_IDENT_FESTOPS_LABEL,
                         InvUnitIdentifier.created_at >= shift.started_at).first())
        # A bind writes no actor. A unit with an earlier check was bound before this one.
        earlier = (db.session.query(InvCheck.id)
                   .filter(InvCheck.unit_id == unit.id, InvCheck.id < check.id).first())
        if earlier is not None:
            bound = None
        if created is not None:
            parts.append("The new unit stays.")
        elif bound is not None:
            parts.append("The label stays on it.")
    return " ".join(parts)


def _undo_check(check: InvCheck, actor: Actor) -> dict:
    unit = (db.session.query(InvUnit).filter_by(id=check.unit_id)
            .with_for_update().populate_existing().one())
    prep = (db.session.query(InvPrepStatus)
            .filter_by(unit_id=unit.id, event_cycle_id=check.event_cycle_id)
            .order_by(InvPrepStatus.id.desc()).first())
    own_prep = prep is not None and prep.check_id == check.id \
        and prep.status in (INV_PREP_CHECKED, INV_PREP_FAILED_CHECK)
    if not own_prep or check.previous_condition is None:
        raise InventoryError(CHANGED)
    retired = _retired_by(check, unit, prep)
    if not _expected_condition(check, unit, retired):
        raise InventoryError(CHANGED)
    refusal = _reactivation_refusal(unit, retired)
    if refusal:
        raise InventoryError(refusal)
    try:
        for identifier in retired:
            identifier.retired_at = None
        db.session.flush()
    except IntegrityError as error:
        raise InventoryError(CHANGED) from error
    set_condition(unit, check.previous_condition)
    check.status = INV_CHECK_VOIDED
    db.session.add(InvPrepStatus(
        unit_id=unit.id, event_cycle_id=check.event_cycle_id, status=INV_PREP_VOIDED,
        check_id=check.id, performed_by_user_id=actor.user_id, shift_id=actor.shift_id,
        device_id=actor.device_id))
    db.session.flush()
    db.session.expire(unit, ["identifiers"])
    text = " ".join(p for p in (f"Check of {display_number(unit)} undone.",
                                _stays_text(check, unit, actor)) if p)
    return {"undone": "check", "message": text}


# ---- counts ----

def _undo_count(count: InvCount, actor: Actor) -> dict:
    lines = [l for l in count.lines
             if l.outcome in (INV_COUNT_LINE_APPLIED, INV_COUNT_LINE_UNCHANGED)]
    if not lines:
        raise InventoryError(NOTHING)
    # Lock the stock rows in item order before reading later movements, so a
    # concurrent write cannot slip between the check and the reversal.
    for item_id in sorted({l.item_id for l in lines}):
        _locked_stock(item_id, count.container_id)
    own = (db.session.query(InvMovement).filter(InvMovement.count_id == count.id)
           .order_by(InvMovement.id).all())
    later = (db.session.query(InvMovement.id)
             .filter(InvMovement.item_id.in_([l.item_id for l in lines]),
                     or_(InvMovement.from_container_id == count.container_id,
                         InvMovement.to_container_id == count.container_id),
                     or_(InvMovement.count_id.is_(None), InvMovement.count_id != count.id)))
    later = later.filter(InvMovement.id > own[-1].id) if own \
        else later.filter(InvMovement.created_at > count.submitted_at)
    if later.first() is not None:
        raise InventoryError(CHANGED)
    try:
        # Newest first, so a discard is put back before the adjustment above it is removed.
        for movement in reversed(own):
            reverse_movement(movement, actor)
    except InventoryError as error:
        raise InventoryError(CHANGED) from error
    for line in lines:
        line.outcome = INV_COUNT_LINE_UNDONE
    db.session.flush()
    return {"undone": "count", "message": f"Count of {count.container.label_code} undone."}
