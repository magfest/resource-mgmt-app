"""Ledger invariants: the cache always equals the log, and refusals write nothing."""
import pytest
from sqlalchemy import text

from app import db
from app.inventory.checks import find_balance_mismatches
from app.inventory.describe import describe_movement
from app.inventory import ledger
from app.inventory.ledger import (
    Actor, InventoryError, add_stock, add_unit, create_container, discard_stock,
    move_container, move_stock, move_unit, set_counted_quantity, stock_quantity,
)
from app.models import InvMovement, InvStock
from app.models.constants import INV_ACTION_DISCARD
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
    with pytest.raises(InventoryError, match="tracked as assets"):
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


def test_discard_needs_a_reason_and_stock(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    add_stock(inv.pens, tote, 10, ACTOR)
    with pytest.raises(InventoryError, match="reason"):
        discard_stock(inv.pens, tote, 2, "", ACTOR)
    with pytest.raises(InventoryError, match="reason"):
        discard_stock(inv.pens, tote, 2, "BORED", ACTOR)
    with pytest.raises(InventoryError, match="holds 10"):
        discard_stock(inv.pens, tote, 11, "DRIED_OUT", ACTOR)
    movement = discard_stock(inv.pens, tote, 3, "DRIED_OUT", ACTOR)
    assert movement.action == INV_ACTION_DISCARD
    assert movement.reason == "DRIED_OUT"
    assert movement.from_container_id == tote.id
    assert movement.to_container_id is None
    assert stock_quantity(inv.pens, tote) == 7
    assert find_balance_mismatches() == []


def test_discard_refuses_a_bad_quantity_and_a_serialized_item(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    add_stock(inv.pens, tote, 10, ACTOR)
    with pytest.raises(InventoryError, match="whole number"):
        discard_stock(inv.pens, tote, 0, "BROKEN", ACTOR)
    with pytest.raises(InventoryError, match="tracked as assets"):
        discard_stock(inv.laptop, tote, 1, "BROKEN", ACTOR)
    assert stock_quantity(inv.pens, tote) == 10


def test_approximate_flag_follows_the_latest_count(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    set_counted_quantity(inv.pens, tote, 200, ACTOR, is_approximate=True)
    assert InvStock.query.one().is_approximate is True
    set_counted_quantity(inv.pens, tote, 198, ACTOR)
    assert InvStock.query.one().is_approximate is False


def test_a_matching_count_still_sets_the_approximate_flag(inv):
    tote = make_container(inv, "C-0001", kind="BIN")
    set_counted_quantity(inv.pens, tote, 50, ACTOR)
    assert set_counted_quantity(inv.pens, tote, 50, ACTOR, is_approximate=True) is None
    assert InvStock.query.one().is_approximate is True


def test_move_container_locks_before_the_cycle_check(inv, monkeypatch):
    pallet = make_container(inv, "C-0001")
    shelf = make_container(inv, "C-0002")
    calls = []
    real_lock, real_cycle = ledger._lock_container, ledger.would_cycle
    monkeypatch.setattr(ledger, "_lock_container",
                        lambda c: (calls.append("lock"), real_lock(c))[1])
    monkeypatch.setattr(ledger, "would_cycle",
                        lambda c, p: (calls.append("cycle"), real_cycle(c, p))[1])
    move_container(pallet, shelf, ACTOR)
    assert calls == ["lock", "cycle"]
    assert pallet.parent_id == shelf.id


def test_creating_a_container_twice_is_refused_cleanly(inv, monkeypatch):
    make_container(inv, "C-0001", kind="BIN")
    # Skip the pre-check so the unique constraint decides, as it does in a race.
    monkeypatch.setattr(ledger, "_container_exists", lambda code: False)
    with pytest.raises(InventoryError, match="C-0001 is already a container"):
        create_container("C-0001", inv.kinds["BIN"], ACTOR)
    db.session.rollback()


def test_a_new_container_is_created_and_logged(inv):
    container = create_container("c-0009", inv.kinds["BIN"], ACTOR)
    assert container.label_code == "C-0009"
    assert InvMovement.query.filter_by(subject_container_id=container.id).count() == 1


def _rows(inv):
    return InvMovement.query.filter_by(item_id=inv.pens.id).order_by(InvMovement.id).all()


def test_a_short_move_tops_up_the_source_then_moves_the_full_amount(inv):
    a = make_container(inv, "C-0001")
    b = make_container(inv, "C-0002")
    ledger.add_stock(inv.pens, a, 3, ACTOR)
    move, short = ledger.move_stock_covering_short(inv.pens, a, b, 10, ACTOR)
    assert short == 7 and move.quantity == 10
    assert stock_quantity(inv.pens, a) == 0 and stock_quantity(inv.pens, b) == 10
    rows = _rows(inv)
    assert [(m.action, m.quantity, m.from_container_id, m.to_container_id, m.note)
            for m in rows] == [("ADD", 3, None, a.id, None),
                               ("ADJUST", 7, None, a.id, "short move"),
                               ("MOVE", 10, a.id, b.id, None)]
    assert find_balance_mismatches() == []
    assert describe_movement(rows[1]) == \
        "Move added 7 Blue Pen in C-0001: more was moved out than was recorded"


def test_a_covered_move_with_enough_stock_writes_no_adjustment(inv):
    a = make_container(inv, "C-0001")
    b = make_container(inv, "C-0002")
    ledger.add_stock(inv.pens, a, 12, ACTOR)
    move, short = ledger.move_stock_covering_short(inv.pens, a, b, 5, ACTOR)
    assert short == 0
    assert [m.action for m in _rows(inv)] == ["ADD", "MOVE"]
    assert stock_quantity(inv.pens, a) == 7 and stock_quantity(inv.pens, b) == 5
    assert find_balance_mismatches() == []


def test_a_never_counted_source_ends_at_zero(inv):
    a = make_container(inv, "C-0001")
    b = make_container(inv, "C-0002")
    move, short = ledger.move_stock_covering_short(inv.pens, a, b, 4, ACTOR)
    assert short == 4
    assert stock_quantity(inv.pens, a) == 0 and stock_quantity(inv.pens, b) == 4
    assert find_balance_mismatches() == []


def test_a_covered_move_keeps_the_other_refusals(inv):
    a = make_container(inv, "C-0001")
    b = make_container(inv, "C-0002")
    for item, dest, qty in [(inv.pens, a, 1), (inv.laptop, b, 1), (inv.pens, b, 0)]:
        with pytest.raises(InventoryError):
            ledger.move_stock_covering_short(item, a, dest, qty, ACTOR)
    assert InvMovement.query.count() == 0


def test_move_unit_marks_a_lost_asset_found(inv):
    from app.inventory.history import unit_history
    bin_ = make_container(inv, "C-0001", kind="BIN")
    lost = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-0101")])
    repair = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-0102")])
    lost.condition, repair.condition = "LOST", "REPAIR"
    db.session.commit()

    move_unit(lost, bin_, ACTOR)
    move_unit(repair, bin_, ACTOR)
    db.session.commit()

    assert lost.condition == "FOUND"
    assert repair.condition == "REPAIR"
    lines = [(line.who, line.text) for line in unit_history(lost)]
    assert ("Invadmin", "changed the condition from Lost to Found") in lines
    assert not any("condition" in line.text for line in unit_history(repair))
