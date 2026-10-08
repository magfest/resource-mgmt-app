"""Every write that changes where stock is or how much there is.

Each function writes an InvMovement and updates the InvStock cache or the
unit's location in the same transaction. Rows are locked before they change,
so two scanners acting on one bin get a fresh answer instead of a lost
update. Nothing here commits.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.codes import normalize_code
from app.inventory.tree import would_cycle
from app.models import (
    InvContainer, InvContainerKind, InvMovement, InvStock, InvUnit,
    InvUnitIdentifier, SupplyItem,
)
from app.models.constants import (
    INV_ACTION_ADD, INV_ACTION_ADJUST, INV_ACTION_MOVE, INV_IDENT_FESTOPS_LABEL,
    INV_IDENT_VENDOR_SERIAL,
)


class InventoryError(Exception):
    """A refusal. The scanner shows the message to the operator verbatim."""


@dataclass(frozen=True)
class Actor:
    """Who performed a write: a signed-in user, or a badge on a shared device."""
    user_id: str | None = None
    badge_session_id: int | None = None
    device_id: int | None = None


def _movement(action: str, actor: Actor, **columns) -> InvMovement:
    movement = InvMovement(
        action=action,
        performed_by_user_id=actor.user_id,
        badge_session_id=actor.badge_session_id,
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


def add_identifier(unit: InvUnit, kind: str, value: str) -> InvUnitIdentifier:
    normalized = normalize_code(value)
    if not normalized:
        raise InventoryError("That code is blank.")
    if kind == INV_IDENT_FESTOPS_LABEL:
        taken = db.session.query(InvUnitIdentifier).filter_by(
            kind=INV_IDENT_FESTOPS_LABEL, normalized_value=normalized, retired_at=None,
        ).first()
        if taken is not None:
            raise InventoryError(f"{normalized} is already on another unit.")
    identifier = InvUnitIdentifier(unit_id=unit.id, kind=kind, value=value.strip(),
                                   normalized_value=normalized)
    db.session.add(identifier)
    db.session.flush()
    return identifier


def add_unit(item, container, actor, label_code, vendor_serial=None) -> InvUnit:
    if not item.is_serialized:
        raise InventoryError(
            f"{item.item_name} is counted stock; scan the item and enter a quantity.")
    unit = InvUnit(item_id=item.id, current_container_id=container.id,
                   created_by_user_id=actor.user_id)
    db.session.add(unit)
    db.session.flush()
    add_identifier(unit, INV_IDENT_FESTOPS_LABEL, label_code)
    if vendor_serial:
        add_identifier(unit, INV_IDENT_VENDOR_SERIAL, vendor_serial)
    _movement(INV_ACTION_ADD, actor, item_id=item.id, unit_id=unit.id,
              to_container_id=container.id)
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
