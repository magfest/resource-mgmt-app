"""Identifier normalization and units that start from any identifier."""
import pytest

from app import db
from app.inventory.codes import magfest_tag, normalize_identifier, normalize_mac
from app.inventory.describe import describe_movement, unit_tag
from app.inventory.ledger import (
    Actor, InventoryError, add_identifier, add_unit, retire_identifier,
    set_condition,
)
from app.inventory import ledger
from app.models import InvMovement, InvUnit
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:invadmin")


@pytest.mark.parametrize("raw,expected", [
    ("net0001", "NET00001"), ("NET00001", "NET00001"), (" tech412 ", "TECH00412"),
    ("ABC123", None), ("NET123456", None), ("NET0", None), ("C-0001", None),
    (None, None)])
def test_magfest_tag(raw, expected):
    assert magfest_tag(raw) == expected


def test_mac_accepts_separators_and_refuses_junk():
    assert normalize_mac("aa:bb:cc:dd:ee:0f") == "AABBCCDDEE0F"
    assert normalize_mac("AABB.CCDD.EE0F") == "AABBCCDDEE0F"
    assert normalize_mac("AABBCC") is None


def test_normalize_identifier_speaks_to_the_operator():
    assert normalize_identifier("MAGFEST_TAG", "net0001") == "NET00001"
    assert normalize_identifier("MAC", "aa-bb-cc-dd-ee-0f") == "AABBCCDDEE0F"
    assert normalize_identifier("SERIAL", " abc 123 ") == "ABC 123"
    with pytest.raises(ValueError, match="net0 is not a MAGFest tag"):
        normalize_identifier("MAGFEST_TAG", "net0")
    with pytest.raises(ValueError, match="AABBCC is not a MAC address"):
        normalize_identifier("MAC", "AABBCC")


def test_unit_from_a_tag_alone_and_tag_reuse_refused(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    unit = add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "tech0001")])
    assert unit_tag(unit) == "TECH00001"
    with pytest.raises(InventoryError, match="already on another unit"):
        add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "TECH00001")])
    with pytest.raises(InventoryError, match="at least one"):
        add_unit(inv.laptop, bin_, ACTOR, [])


def test_invalid_tag_surfaces_as_an_inventory_error(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    with pytest.raises(InventoryError, match="not a MAGFest tag"):
        add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "net0")])


def test_unit_can_start_without_a_container(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "sn-1")])
    assert unit.current_container_id is None
    movement = InvMovement.query.filter_by(unit_id=unit.id).one()
    assert movement.to_container_id is None
    assert "not placed yet" in describe_movement(movement)


def test_counted_item_is_still_refused(inv):
    with pytest.raises(InventoryError, match="counted stock"):
        add_unit(inv.pens, None, ACTOR, [("SERIAL", "sn-1")])


def test_unit_tag_prefers_tag_then_label_then_serial(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("SERIAL", "sn-1"), ("FESTOPS_LABEL", "U-00001")])
    assert unit_tag(unit) == "U-00001"
    add_identifier(unit, "MAGFEST_TAG", "net7")
    assert unit_tag(unit) == "NET00007"
    for identifier in list(unit.identifiers):
        retire_identifier(identifier)
    assert unit_tag(unit) == f"unit {unit.id}"


def test_retired_tag_can_be_reused(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    retire_identifier(unit.identifiers[0])
    other = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    assert unit_tag(other) == "NET00001"


def test_vendor_tag_is_unique_per_vendor_only(inv):
    first = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "a")])
    second = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "b")])
    third = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "c")])
    first.vendor_name = second.vendor_name = "Acme"
    third.vendor_name = "Other"
    db.session.flush()
    add_identifier(first, "VENDOR_TAG", "v-1")
    add_identifier(third, "VENDOR_TAG", "v-1")
    with pytest.raises(InventoryError, match="V-1 is already on another unit"):
        add_identifier(second, "VENDOR_TAG", "v-1")


def test_serials_may_repeat(inv):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "same")])
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "same")])


def test_retiring_a_unit_frees_its_identifiers(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("MAGFEST_TAG", "net2"), ("SERIAL", "sn-2")])
    set_condition(unit, "RETIRED")
    assert unit.condition == "RETIRED"
    assert all(i.retired_at is not None for i in unit.identifiers)
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net2")])


def test_other_conditions_keep_identifiers_and_unknown_is_refused(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net3")])
    set_condition(unit, "REPAIR")
    assert unit.condition == "REPAIR"
    assert unit.identifiers[0].retired_at is None
    with pytest.raises(InventoryError, match="not a condition"):
        set_condition(unit, "BROKEN")


def test_refused_unit_leaves_nothing_behind(inv):
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    with pytest.raises(InventoryError, match="already on another unit"):
        add_unit(inv.laptop, None, ACTOR, [("SERIAL", "x"), ("MAGFEST_TAG", "net1")])
    assert InvUnit.query.count() == 1
    assert InvMovement.query.count() == 1


def test_losing_the_uniqueness_race_is_an_inventory_error(inv, monkeypatch):
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    monkeypatch.setattr(ledger, "_is_taken", lambda *args: False)
    with pytest.raises(InventoryError, match="NET00001 is already on another unit"):
        add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    assert InvUnit.query.count() == 1
