"""The facts the About tab shows for one unit."""
import pytest

from app import db
from app.inventory.ledger import (
    Actor, add_identifier, add_unit, create_container, move_unit, retire_identifier,
)
from app.inventory.unit_about import (
    added_line, check_line, detail_rows, find_by_rows, placed_line, where_sentence,
)

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


def test_detail_rows_start_with_the_settings_that_drive_the_unit(inv):
    unit = _unit(inv)
    assert detail_rows(unit) == [
        ("Inventory item group", "Laptop"), ("Gear type", "Not set"),
        ("Needs an asset tag", "No"), ("Check", "Works or Broken"), ("Owned by", "FestOps")]


def test_detail_rows_name_the_gear_type_checklist_and_tag_rule(inv):
    from app.models import InvCheckItem
    from tests.inventory.helpers import seed_gear_kind
    inv.laptop.gear_kind_id = seed_gear_kind("LAPTOP", "Laptop", [("MODEL", "Model", "TEXT", None)]).id
    inv.laptop.needs_asset_tag = True
    db.session.add_all([InvCheckItem(item_id=inv.laptop.id, position=1, prompt="Powers on?",
                                     missing_means="FAIL"),
                        InvCheckItem(item_id=inv.laptop.id, position=2, prompt="Charger?",
                                     missing_means="NOTE"),
                        InvCheckItem(item_id=inv.laptop.id, position=3, prompt="Old line",
                                     missing_means="FAIL", is_active=False)])
    db.session.commit()
    rows = dict(detail_rows(_unit(inv)))
    assert rows["Gear type"] == "Laptop"
    assert rows["Needs an asset tag"] == "Yes"
    assert rows["Check"] == "2 checklist lines"


def test_detail_rows_for_a_rental(inv):
    unit = _unit(inv)
    unit.ownership = "RENTED"
    assert detail_rows(unit)[-1] == ("Rented from", "Vendor not recorded")
    unit.vendor_name = "Invented AV Co"
    unit.rental_reference = "ORD-0001"
    assert detail_rows(unit)[-2:] == [("Rented from", "Invented AV Co"),
                                      ("Rental reference", "ORD-0001")]


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
