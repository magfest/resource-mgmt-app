"""Ledger invariants: the cache always equals the log, and refusals write nothing."""
import pytest
from sqlalchemy import text

from app import db
from app.inventory.checks import find_balance_mismatches
from app.inventory.ledger import (
    Actor, InventoryError, add_stock, add_unit, move_container, move_stock,
    move_unit, stock_quantity,
)
from app.models import InvMovement, InvStock
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:invadmin")


def test_add_then_move_keeps_cache_equal_to_log(inv):
    a = make_container(inv, "C-0001", kind="BIN")
    b = make_container(inv, "C-0002", kind="BIN")
    add_stock(inv.pens, a, 10, ACTOR)
    move_stock(inv.pens, a, b, 4, ACTOR)
    db.session.commit()

    assert stock_quantity(inv.pens, a) == 6
    assert stock_quantity(inv.pens, b) == 4
    assert find_balance_mismatches() == []


def test_moving_more_than_held_is_refused_and_writes_nothing(inv):
    a = make_container(inv, "C-0001", kind="BIN")
    b = make_container(inv, "C-0002", kind="BIN")
    add_stock(inv.pens, a, 3, ACTOR)
    db.session.commit()

    with pytest.raises(InventoryError, match="holds 3"):
        move_stock(inv.pens, a, b, 5, ACTOR)
    db.session.rollback()

    assert stock_quantity(inv.pens, a) == 3
    assert InvMovement.query.count() == 1


def test_container_cannot_move_inside_its_own_descendant(inv):
    pallet = make_container(inv, "C-0001")
    bin_ = make_container(inv, "C-0002", kind="BIN", parent=pallet)
    with pytest.raises(InventoryError, match="inside itself"):
        move_container(pallet, bin_, ACTOR)


def test_serialized_item_is_refused_as_counted_stock(inv):
    pallet = make_container(inv, "C-0001")
    with pytest.raises(InventoryError, match="serialized"):
        add_stock(inv.laptop, pallet, 1, ACTOR)
    unit = add_unit(inv.laptop, pallet, ACTOR,
                    [("FESTOPS_LABEL", "U-00001"), ("SERIAL", "abc 123")])
    assert unit.identifiers[1].kind == "SERIAL"
    assert unit.identifiers[1].value == "abc 123"
    assert unit.identifiers[1].normalized_value == "ABC 123"


def test_stock_lock_rereads_a_quantity_changed_underneath(inv):
    a = make_container(inv, "C-0001", kind="BIN")
    b = make_container(inv, "C-0002", kind="BIN")
    add_stock(inv.pens, a, 10, ACTOR)
    db.session.commit()
    # Hold the row so it stays in the identity map, which is weakly referenced.
    row = InvStock.query.filter_by(container_id=a.id).one()
    assert row.quantity == 10

    # Another scanner's committed change; raw SQL leaves the identity map stale.
    db.session.execute(text("UPDATE inv_stock SET quantity = 1 WHERE container_id = :c"),
                       {"c": a.id})

    with pytest.raises(InventoryError, match="holds 1"):
        move_stock(inv.pens, a, b, 5, ACTOR)


def test_unit_move_records_the_location_it_really_left(inv):
    a = make_container(inv, "C-0001")
    b = make_container(inv, "C-0002")
    c = make_container(inv, "C-0003")
    unit = add_unit(inv.laptop, a, ACTOR, [("FESTOPS_LABEL", "U-00001")])
    db.session.commit()
    assert unit.current_container_id == a.id

    db.session.execute(text("UPDATE inv_units SET current_container_id = :b WHERE id = :u"),
                       {"b": b.id, "u": unit.id})

    movement = move_unit(unit, c, ACTOR)
    assert movement.from_container_id == b.id
