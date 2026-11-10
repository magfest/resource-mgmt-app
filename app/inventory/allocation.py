"""Allocation rules: which assets may fill which order lines.

This module holds the lookups and the allocate, move and release rules. It
reads department demand and never writes it. Every write appends an
InvAllocationEvent for History. Nothing here commits; the calling route owns
the transaction and rolls back after a refusal.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

from app import db
from app.inventory.checks import unit_phrase
from app.inventory.demand import (
    LINE_OPEN, SOURCE_SUPPLY, DemandLine, asset_warning, demand_line, line_label, line_words,
)
from app.inventory.ledger import InventoryError, savepoint
from app.models import InvAllocation, InvAllocationEvent, InvUnit, WorkLine
from app.models.constants import (
    INV_ALLOCATION_ALLOCATE, INV_ALLOCATION_MOVE, INV_ALLOCATION_RELEASE,
    INV_CONDITION_IN_SERVICE,
)

NOT_OPEN = "That line is not open for allocation. Reload the page."
LOST_RACE = "Someone allocated one of those assets just now. Reload and try again."
# The page's value for an asset held on a deleted line. None already means Not allocated.
REMOVED_LINE = "removed"


def active_allocation_count(item_id: int) -> int:
    """Count allocations held by assets of one item."""
    return (db.session.query(InvAllocation).join(InvUnit, InvUnit.id == InvAllocation.unit_id)
            .filter(InvUnit.item_id == item_id).count())


def current_allocation(unit_id: int) -> InvAllocation | None:
    """Return the asset's allocation, or None when it is free."""
    return db.session.query(InvAllocation).filter_by(unit_id=unit_id).first()


def allocation_sentence(unit: InvUnit) -> str:
    """Return the asset page line for the unit's allocation, from live line words when the line exists."""
    row = current_allocation(unit.id)
    if row is None:
        return "Allocated to: nothing"
    return (f"Allocated to: {row.department.name} for {row.event_cycle.code}"
            f" \u00b7 {_live_words(row)}")


def line_lock_query(work_line_id: int) -> Query:
    """Return the query that locks one work line until the transaction ends.

    Every explicit lock here takes lines first, in id order when there are
    several, then allocation rows in unit id order. One order in every function
    keeps two writers from deadlocking. The query has no joins because Postgres
    refuses a row lock on the nullable side of an outer join.
    """
    # key_share emits FOR NO KEY UPDATE on Postgres; two allocators of one line still wait.
    # Plain FOR UPDATE would also block foreign-key checks from rows that reference the line.
    # A release's event insert would then wait on a line lock and could deadlock with a move.
    # populate_existing: a line already in the session would otherwise keep stale values.
    return (db.session.query(WorkLine).filter(WorkLine.id == work_line_id)
            .with_for_update(key_share=True).populate_existing())


def _allocation_lock_query(unit_id: int):
    return (db.session.query(InvAllocation).filter_by(unit_id=unit_id)
            .with_for_update().populate_existing())


def _open_line(work_line_id: int) -> DemandLine:
    """Lock the line, then read it as demand; refuse a missing or unopen line."""
    if line_lock_query(work_line_id).one_or_none() is None:
        raise InventoryError(NOT_OPEN)
    # fresh: a detail or request read earlier in this session could hold a stale cap or status.
    line = demand_line(work_line_id, fresh=True)
    if line is None or line.state != LINE_OPEN:
        raise InventoryError(NOT_OPEN)
    return line


def _held_label(row: InvAllocation) -> str:
    """Name the line an allocation sits on, from its snapshot once the line is gone."""
    return f"{row.department.name} · {_live_words(row)}"


def _live_words(row: InvAllocation) -> str:
    # Dev SQLite enforces no foreign keys, so the id can outlive its line.
    line = db.session.get(WorkLine, row.work_line_id) if row.work_line_id is not None else None
    return row.line_label if line is None else line_words(line)


def _check_unit(unit: InvUnit, line: DemandLine) -> None:
    """Refuse an asset whose item is switched off, that misfits the line, or is out of service."""
    item = unit.item
    if not (item.is_serialized and item.needs_pre_allocation):
        raise InventoryError(f"{item.item_name} is not set up for pre-allocation.")
    if line.source == SOURCE_SUPPLY:
        fills = line.item_id == unit.item_id
    else:
        fills = (line.service_type_id is not None
                 and line.service_type_id == item.techops_service_type_id)
    if not fills:
        raise InventoryError(f"{unit_phrase(unit)} does not fill that line.")
    if unit.condition != INV_CONDITION_IN_SERVICE:
        # FOUND is refused too: a found asset needs a Check before it goes out again.
        warning = asset_warning(unit) or f"{unit_phrase(unit)} is not in service"
        raise InventoryError(f"{warning}, so it cannot be allocated.")


def _check_room(line: DemandLine, count: int) -> None:
    # A line cut below its allocations has 0 left, so it takes nothing more.
    if line.allocated + count > line.requested:
        raise InventoryError(
            f"{line_label(line)} has {line.left} left, and you picked {count}.")


def _point_at(row: InvAllocation, line: DemandLine) -> None:
    row.work_line_id = line.work_line_id
    row.event_cycle_id = line.event_cycle_id
    row.department_id = line.department_id
    row.line_label = line.words


def _event(action: str, row: InvAllocation, words: str, user_id: str) -> None:
    db.session.add(InvAllocationEvent(
        unit_id=row.unit_id, action=action, work_line_id=row.work_line_id,
        event_cycle_id=row.event_cycle_id, department_id=row.department_id,
        line_label=words, performed_by_user_id=user_id))


def _is_unit_unique_violation(exc: IntegrityError) -> bool:
    # Postgres names the constraint; SQLite names only the table and column.
    text = str(exc.orig)
    return "uq_inv_allocations_unit_id" in text or "inv_allocations.unit_id" in text


def allocate(units: list[InvUnit], work_line_id: int, user_id: str) -> DemandLine:
    """Allocate every asset to one open line, or none of them.

    The cap is checked under the line lock, so two allocators cannot overfill a
    line. An asset ticked twice counts once. Returns the line with its new rows.

    Raises:
        InventoryError: Any check failed, or another allocator took one of the
            assets after the check; nothing was written either way.
    """
    units = list({unit.id: unit for unit in units}.values())
    if not units:
        raise InventoryError("Tick at least one asset.")
    line = _open_line(work_line_id)
    for unit in units:
        _check_unit(unit, line)
        existing = current_allocation(unit.id)
        if existing is not None:
            raise InventoryError(
                f"{unit_phrase(unit)} is already allocated to {_held_label(existing)}.")
    _check_room(line, len(units))
    # One savepoint holds the whole batch, so a raced insert undoes every row of it.
    try:
        with savepoint():
            for unit in units:
                row = InvAllocation(unit_id=unit.id, allocated_by_user_id=user_id)
                _point_at(row, line)
                db.session.add(row)
                _event(INV_ALLOCATION_ALLOCATE, row, row.line_label, user_id)
            db.session.flush()
    except IntegrityError as exc:
        if not _is_unit_unique_violation(exc):
            raise
        raise InventoryError(LOST_RACE) from exc
    return demand_line(work_line_id)


def move(unit: InvUnit, work_line_id: int, user_id: str) -> DemandLine:
    """Move an allocated asset to another open line; returns the target line.

    An asset out of service keeps its allocation but cannot be placed on
    another line.

    Raises:
        InventoryError: Any check failed; nothing was written.
    """
    line = _open_line(work_line_id)
    row = _allocation_lock_query(unit.id).one_or_none()
    if row is None:
        raise InventoryError(f"{unit_phrase(unit)} is not allocated.")
    if row.work_line_id == work_line_id:
        raise InventoryError(f"{unit_phrase(unit)} is already on that line.")
    _check_unit(unit, line)
    _check_room(line, 1)
    _point_at(row, line)
    _event(INV_ALLOCATION_MOVE, row, row.line_label, user_id)
    db.session.flush()
    return demand_line(work_line_id)


def release(unit: InvUnit, user_id: str) -> InvAllocation:
    """Delete the asset's allocation and return the deleted row for the flash text.

    Raises:
        InventoryError: The asset holds no allocation.
    """
    row = _allocation_lock_query(unit.id).one_or_none()
    if row is None:
        raise InventoryError(f"{unit_phrase(unit)} is not allocated.")
    _event(INV_ALLOCATION_RELEASE, row, _live_words(row), user_id)
    db.session.delete(row)
    db.session.flush()
    return row


@dataclass(frozen=True)
class RowChange:
    """One page row: the line it showed and the line chosen.

    A line is a work line id, None for Not allocated, or REMOVED_LINE for a line
    that was deleted under the asset.
    """

    unit: InvUnit
    was_line_id: int | str | None
    to_line_id: int | str | None


@dataclass
class SaveResult:
    saved: int
    stale: list[InvUnit]


def save_changes(changes: list[RowChange], user_id: str) -> SaveResult:
    """Apply a batch of row changes through allocate, move and release.

    Every target line and every changed asset's allocation row is locked up
    front, lines first. Form order would otherwise interleave the locks, and two
    overlapping saves could deadlock. The stale check reads the locked rows.

    Releases run first, then moves in passes, then fills, so a change that
    frees room can serve a later one. A row whose current line is neither the
    one the page showed nor the one chosen is stale and skipped. Never commits.

    Raises:
        InventoryError: The first refusal; the caller rolls back.
    """
    changed = [c for c in changes if c.was_line_id != c.to_line_id]
    if any(c.to_line_id == REMOVED_LINE for c in changed):
        # The page offers a removed line only as the asset's own value; nothing can move onto one.
        raise InventoryError(NOT_OPEN)
    # A missing line is refused later, by the rule that writes to it.
    for line_id in sorted({c.to_line_id for c in changed if c.to_line_id is not None}):
        line_lock_query(line_id).one_or_none()
    held = {unit_id: _allocation_lock_query(unit_id).one_or_none()
            for unit_id in sorted({c.unit.id for c in changed})}
    stale: list[InvUnit] = []
    pending: list[RowChange] = []
    for change in changed:
        row = held[change.unit.id]
        if row is None:
            current = None
        else:
            current = REMOVED_LINE if row.work_line_id is None else row.work_line_id
        if current == change.was_line_id:
            pending.append(change)
        elif current != change.to_line_id:
            stale.append(change.unit)
    saved = 0
    for change in [c for c in pending if c.to_line_id is None]:
        release(change.unit, user_id)
        saved += 1
    moves = [c for c in pending if c.was_line_id is not None and c.to_line_id is not None]
    while moves:
        # A refused move writes nothing, so it can be retried after another move frees room.
        remaining, refusal = [], None
        for change in moves:
            try:
                move(change.unit, change.to_line_id, user_id)
            except InventoryError as error:
                remaining.append(change)
                refusal = refusal or error
            else:
                saved += 1
        if len(remaining) == len(moves):
            raise refusal
        moves = remaining
    fills: dict[int, list[InvUnit]] = {}
    for change in pending:
        if change.was_line_id is None and change.to_line_id is not None:
            fills.setdefault(change.to_line_id, []).append(change.unit)
    for line_id, units in fills.items():
        allocate(units, line_id, user_id)
        saved += len(units)
    return SaveResult(saved=saved, stale=stale)
