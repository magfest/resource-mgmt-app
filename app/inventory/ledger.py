"""Every write that changes where stock is or how much there is.

Each function writes an InvMovement and updates the InvStock cache or the
unit's location in the same transaction. Rows are locked before they change,
so two scanners acting on one bin get a fresh answer instead of a lost
update. Nothing here commits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.codes import normalize_code, normalize_identifier
from app.inventory.tree import would_cycle
from app.models import (
    InvContainer, InvContainerKind, InvMovement, InvStock, InvUnit,
    InvUnitIdentifier, SupplyItem,
)
from app.models.constants import (
    INV_ACTION_ADD, INV_ACTION_ADJUST, INV_ACTION_MOVE, INV_CONDITION_IN_SERVICE,
    INV_CONDITION_LOST, INV_CONDITION_REPAIR, INV_CONDITION_RETIRED,
    INV_IDENT_UNIQUE_KINDS, INV_IDENT_VENDOR_TAG,
)

INV_CONDITIONS = (INV_CONDITION_IN_SERVICE, INV_CONDITION_REPAIR,
                  INV_CONDITION_RETIRED, INV_CONDITION_LOST)


class InventoryError(Exception):
    """A refusal. The scanner shows the message to the operator verbatim."""


@dataclass(frozen=True)
class Actor:
    """Who performed a write: a signed-in user, or a named shift on a shared device."""
    user_id: str | None = None
    shift_id: int | None = None
    device_id: int | None = None


def _movement(action: str, actor: Actor, **columns) -> InvMovement:
    movement = InvMovement(
        action=action,
        performed_by_user_id=actor.user_id,
        shift_id=actor.shift_id,
        device_id=actor.device_id,
        **columns,
    )
    db.session.add(movement)
    db.session.flush()
    return movement


def _locked_stock(item_id: int, container_id: int) -> InvStock:
    """Return the stock row for update, creating it at zero if absent.

    Two scanners can create the same row at once; the loser of the unique
    constraint re-reads the winner's row.
    """
    # populate_existing: a row already in the session keeps stale values unless
    # the locked read overwrites them, which would lose another scanner's update.
    query = (db.session.query(InvStock)
             .filter_by(item_id=item_id, container_id=container_id)
             .with_for_update().populate_existing())
    row = query.first()
    if row is not None:
        return row
    try:
        with db.session.begin_nested():
            row = InvStock(item_id=item_id, container_id=container_id, quantity=0)
            db.session.add(row)
    except IntegrityError:
        row = query.one()
    return row


def _require_positive(quantity: int) -> None:
    if not isinstance(quantity, int) or quantity <= 0:
        raise InventoryError("Quantity must be a whole number above zero.")


def _require_counted(item: SupplyItem) -> None:
    if item.is_serialized:
        raise InventoryError(
            f"{item.item_name} is serialized; scan each unit's label instead.")


def stock_quantity(item: SupplyItem, container: InvContainer) -> int:
    row = db.session.query(InvStock).filter_by(
        item_id=item.id, container_id=container.id).first()
    return row.quantity if row else 0


def add_stock(item, container, quantity, actor) -> InvMovement:
    _require_positive(quantity)
    _require_counted(item)
    stock = _locked_stock(item.id, container.id)
    stock.quantity += quantity
    return _movement(INV_ACTION_ADD, actor, item_id=item.id, quantity=quantity,
                     to_container_id=container.id)


def move_stock(item, source, destination, quantity, actor) -> InvMovement:
    _require_positive(quantity)
    _require_counted(item)
    if source.id == destination.id:
        raise InventoryError("Source and destination are the same container.")
    # Lock in id order so two opposite moves between one pair cannot deadlock.
    rows = {c.id: _locked_stock(item.id, c.id)
            for c in sorted([source, destination], key=lambda c: c.id)}
    held = rows[source.id].quantity
    if held < quantity:
        raise InventoryError(
            f"{source.label_code} holds {held} {item.item_name}; cannot move "
            f"{quantity}. Count the container.")
    rows[source.id].quantity -= quantity
    rows[destination.id].quantity += quantity
    return _movement(INV_ACTION_MOVE, actor, item_id=item.id, quantity=quantity,
                     from_container_id=source.id, to_container_id=destination.id)


def set_counted_quantity(item, container, counted, actor, count_id=None):
    """Set a counted balance and log the difference as an ADJUST.

    Returns None when the count matches, since a zero-quantity movement is
    refused by ck_inv_movements_quantity_positive.
    """
    _require_counted(item)
    if not isinstance(counted, int) or counted < 0:
        raise InventoryError("A count must be a whole number, zero or more.")
    stock = _locked_stock(item.id, container.id)
    difference = counted - stock.quantity
    if difference == 0:
        return None
    stock.quantity = counted
    if difference > 0:
        return _movement(INV_ACTION_ADJUST, actor, item_id=item.id, quantity=difference,
                         to_container_id=container.id, count_id=count_id)
    return _movement(INV_ACTION_ADJUST, actor, item_id=item.id, quantity=-difference,
                     from_container_id=container.id, count_id=count_id)


def _already_taken(normalized: str) -> InventoryError:
    return InventoryError(f"{normalized} is already on another unit.")


def _is_taken(unit: InvUnit, kind: str, normalized: str) -> bool:
    """Report whether an active identifier already claims this code."""
    query = (db.session.query(InvUnitIdentifier.id)
             .filter(InvUnitIdentifier.kind == kind,
                     InvUnitIdentifier.normalized_value == normalized,
                     InvUnitIdentifier.retired_at.is_(None)))
    if kind == INV_IDENT_VENDOR_TAG:
        # A vendor tag is only unique within one vendor's numbering.
        query = (query.join(InvUnit, InvUnit.id == InvUnitIdentifier.unit_id)
                 .filter(InvUnit.vendor_name == unit.vendor_name))
    return query.first() is not None


def add_identifier(unit: InvUnit, kind: str, value: str) -> InvUnitIdentifier:
    try:
        normalized = normalize_identifier(kind, value or "")
    except ValueError as error:
        raise InventoryError(str(error)) from error
    if not normalized:
        raise InventoryError("That code is blank.")
    if (kind in INV_IDENT_UNIQUE_KINDS or kind == INV_IDENT_VENDOR_TAG) \
            and _is_taken(unit, kind, normalized):
        raise _already_taken(normalized)
    identifier = InvUnitIdentifier(unit_id=unit.id, kind=kind, value=value.strip(),
                                   normalized_value=normalized)
    # The check above can lose a race; the partial unique index decides, and the
    # savepoint keeps the caller's transaction usable after the loss.
    try:
        with db.session.begin_nested():
            db.session.add(identifier)
    except IntegrityError as error:
        raise _already_taken(normalized) from error
    # The identifiers collection may already be loaded and would miss this row.
    db.session.expire(unit, ["identifiers"])
    return identifier


def retire_identifier(identifier: InvUnitIdentifier) -> None:
    """Free an identifier's code for reuse. The row stays as history."""
    if identifier.retired_at is None:
        identifier.retired_at = datetime.utcnow()
        db.session.flush()


def set_condition(unit: InvUnit, condition: str) -> None:
    """Set a unit's lasting condition; retiring a unit retires its identifiers."""
    if condition not in INV_CONDITIONS:
        raise InventoryError(f"{condition} is not a condition.")
    unit.condition = condition
    if condition == INV_CONDITION_RETIRED:
        for identifier in unit.identifiers:
            retire_identifier(identifier)
    db.session.flush()


def add_unit(item, container, actor, identifiers: list[tuple[str, str]]) -> InvUnit:
    """Create a unit from one or more (kind, value) identifiers.

    The container may be None for gear that has not been shelved yet. A
    refusal rolls back the unit it had started.
    """
    if not item.is_serialized:
        raise InventoryError(
            f"{item.item_name} is counted stock; scan the item and enter a quantity.")
    if not identifiers:
        raise InventoryError("A unit needs at least one identifier.")
    container_id = container.id if container else None
    with db.session.begin_nested():
        unit = InvUnit(item_id=item.id, current_container_id=container_id,
                       created_by_user_id=actor.user_id)
        db.session.add(unit)
        db.session.flush()
        for kind, value in identifiers:
            add_identifier(unit, kind, value)
        _movement(INV_ACTION_ADD, actor, item_id=item.id, unit_id=unit.id,
                  to_container_id=container_id)
    return unit


def move_unit(unit, destination, actor, action=INV_ACTION_MOVE, count_id=None) -> InvMovement:
    """Move one unit. Counts pass action=ADJUST for a unit found elsewhere."""
    (db.session.query(InvUnit).filter_by(id=unit.id)
     .with_for_update().populate_existing().one())
    if unit.current_container_id == destination.id:
        raise InventoryError(f"That unit is already in {destination.label_code}.")
    source_id = unit.current_container_id
    unit.current_container_id = destination.id
    return _movement(action, actor, item_id=unit.item_id, unit_id=unit.id,
                     from_container_id=source_id, to_container_id=destination.id,
                     count_id=count_id)


def create_container(label_code, kind: InvContainerKind, actor, parent=None,
                     name=None) -> InvContainer:
    """Create a container. The caller has already checked the label was issued."""
    code = normalize_code(label_code)
    if db.session.query(InvContainer).filter_by(label_code=code).first():
        raise InventoryError(f"{code} is already a container.")
    container = InvContainer(label_code=code, kind_id=kind.id,
                             parent_id=parent.id if parent else None,
                             name=(name or "").strip() or None,
                             created_by_user_id=actor.user_id)
    db.session.add(container)
    db.session.flush()
    _movement(INV_ACTION_ADD, actor, subject_container_id=container.id,
              to_container_id=parent.id if parent else None)
    return container


def move_container(container, new_parent, actor) -> InvMovement:
    if would_cycle(container, new_parent):
        raise InventoryError(
            f"{new_parent.label_code} is inside {container.label_code}; a container "
            f"cannot go inside itself.")
    (db.session.query(InvContainer).filter_by(id=container.id)
     .with_for_update().populate_existing().one())
    old_parent_id = container.parent_id
    container.parent_id = new_parent.id if new_parent else None
    return _movement(INV_ACTION_MOVE, actor, subject_container_id=container.id,
                     from_container_id=old_parent_id,
                     to_container_id=new_parent.id if new_parent else None)
