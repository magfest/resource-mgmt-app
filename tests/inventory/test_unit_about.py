"""The facts the About tab shows for one unit."""
import pytest

from app import db
from app.inventory.ledger import (
    Actor, add_identifier, add_unit, create_container, move_unit, retire_identifier,
)
from app.inventory.unit_about import added_line, check_line, find_by_rows, owner_line, placed_line, where_sentence

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def places(inv):
    warehouse = create_container("C-9001", inv.kinds["LOCATION"], ACTOR, name="Warehouse")
    pallet = create_container("C-9012", inv.kinds["PALLET"], ACTOR, parent=warehouse,
                              name="Pallet 3")
    unnamed = create_container("C-9013", inv.kinds["PALLET"], ACTOR, parent=warehouse)
    db.session.commit()
    return warehouse, pallet, unnamed


def _unit(inv, container=None, name=None, ids=(("FESTOPS_LABEL", "U-9001"),)):
    unit = add_unit(inv.laptop, container, ACTOR, list(ids), name=name)
    db.session.commit()
    return unit


def test_where_on_a_pallet(inv, places):
    unit = _unit(inv, places[1])
    assert where_sentence(unit) == "Pallet 3 (C-9012), in the Warehouse"


def test_where_directly_in_the_warehouse(inv, places):
    unit = _unit(inv, places[0])
    assert where_sentence(unit) == "In the Warehouse, exact location not assigned"


def test_where_with_no_container(inv, places):
    assert where_sentence(_unit(inv)) == "Not placed yet"


def test_where_in_an_unnamed_pallet(inv, places):
    unit = _unit(inv, places[2])
    assert where_sentence(unit).startswith("C-9013, in the Warehouse")


def test_check_line_never_checked(inv):
    assert check_line(_unit(inv)) == {"word": None, "text": "Never"}


def test_owner_line_rented(inv):
    unit = _unit(inv)
    assert owner_line(unit) == "Owned by FestOps"
    unit.ownership = "RENTED"
    assert owner_line(unit) == "Rented"
    unit.vendor_name = "Invented AV Co"
    assert owner_line(unit) == "Rented from Invented AV Co"


def test_added_line_uses_the_oldest_movement(inv, places):
    assert added_line(_unit(inv, places[1])).startswith("Added ")
    assert " by " in added_line(_unit(inv, places[1], ids=(("FESTOPS_LABEL", "U-9002"),)))


def test_find_by_rows_words_and_order(inv):
    unit = _unit(inv, name="Invented TV", ids=(
        ("MAGFEST_TAG", "FEST90001"), ("SERIAL", "SN0TEST1"), ("VENDOR_TAG", "VT-1")))
    old = add_identifier(unit, "SERIAL", "SN0OLD")
    retire_identifier(old)
    db.session.commit()
    assert find_by_rows(unit) == [
        ("Name", "Invented TV"), ("FEST tag", "FEST90001"), ("Vendor tag", "VT-1"),
        ("Serial number", "SN0TEST1")]


def test_placed_line(inv, places):
    unit = _unit(inv)
    move_unit(unit, places[1], ACTOR)
    db.session.commit()
    line = placed_line(unit)
    assert line.startswith("Placed ")
    assert line.endswith(" by Invadmin")
