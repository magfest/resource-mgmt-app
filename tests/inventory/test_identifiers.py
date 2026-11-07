"""Identifier normalization and units that start from any identifier."""
import pytest

from app import db
from app.inventory.codes import normalize_identifier, normalize_mac
from app.inventory.describe import describe_movement, unit_tag
from app.inventory.ledger import (
    Actor, InventoryError, add_identifier, add_unit, replace_number,
    retire_identifier, retire_received_unit, set_condition,
)
from app.inventory import ledger
from app.inventory.resolver import KIND_UNIT, KIND_UNKNOWN, resolve
from app.models import InvMovement, InvUnit
from app.models.constants import INV_OWNERSHIP_RENTED
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:invadmin")


@pytest.mark.parametrize("raw,expected", [
    (" lat100081 ", "LAT100081"), ("FEST00001", "FEST00001"),
    ("net0001", "NET0001"), ("NET00001", "NET00001"), ("A1-B2", "A1-B2")])
def test_a_magfest_tag_is_the_text_on_the_barcode(raw, expected):
    assert normalize_identifier("MAGFEST_TAG", raw) == expected


def test_mac_accepts_separators_and_refuses_junk():
    assert normalize_mac("aa:bb:cc:dd:ee:0f") == "AABBCCDDEE0F"
    assert normalize_mac("AABB.CCDD.EE0F") == "AABBCCDDEE0F"
    assert normalize_mac("AABBCC") is None


def test_normalize_identifier_speaks_to_the_operator():
    assert normalize_identifier("MAC", "aa-bb-cc-dd-ee-0f") == "AABBCCDDEE0F"
    assert normalize_identifier("SERIAL", " abc 123 ") == "ABC 123"
    with pytest.raises(ValueError, match="AABBCC is not a MAC address"):
        normalize_identifier("MAC", "AABBCC")


def test_unit_from_a_tag_alone_and_tag_reuse_refused(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    unit = add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "tech00001")])
    assert unit_tag(unit) == "TECH00001"
    with pytest.raises(InventoryError, match="already on another unit"):
        add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "TECH00001")])
    with pytest.raises(InventoryError, match="at least one"):
        add_unit(inv.laptop, bin_, ACTOR, [])


def test_a_blank_tag_surfaces_as_an_inventory_error(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    with pytest.raises(InventoryError, match="blank"):
        add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "   ")])


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
    unit.ownership = INV_OWNERSHIP_RENTED
    add_identifier(unit, "MAGFEST_TAG", "net7")
    assert unit_tag(unit) == "NET7"
    for identifier in list(unit.identifiers):
        retire_identifier(identifier)
    assert unit_tag(unit) == f"unit {unit.id}"


def test_retired_tag_can_be_reused(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    retire_identifier(unit.identifiers[0])
    other = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    assert unit_tag(other) == "NET1"


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
    db.session.commit()
    with pytest.raises(InventoryError, match="already on another unit"):
        add_unit(inv.laptop, None, ACTOR, [("SERIAL", "x"), ("MAGFEST_TAG", "net1")])
    # SQLite has no savepoint here; the caller's rollback removes the half-built unit.
    db.session.rollback()
    assert InvUnit.query.count() == 1
    assert InvMovement.query.count() == 1


def test_losing_the_uniqueness_race_is_an_inventory_error(inv, monkeypatch):
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    db.session.commit()
    monkeypatch.setattr(ledger, "_is_taken", lambda *args: False)
    with pytest.raises(InventoryError, match="NET1 is already on another unit"):
        add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net1")])
    # SQLite has no savepoint here, so the caller rolls back, as a route does.
    db.session.rollback()
    assert InvUnit.query.count() == 1
    assert unit_tag(InvUnit.query.one()) == "NET1"


def test_rollback_after_the_first_write_removes_the_unit(inv):
    # pysqlite commits a SAVEPOINT opened before any write, which would make
    # the unit survive the caller's rollback.
    db.session.commit()
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "net9")])
    db.session.rollback()
    assert InvUnit.query.count() == 0


NUMBER_REFUSAL = "This unit already has a number. Replace it on the Units page."


def _active(unit):
    return sorted((i.kind, i.normalized_value) for i in unit.identifiers
                  if i.retired_at is None)


def test_an_owned_unit_never_has_both_a_tag_and_a_label(inv):
    labelled = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-00001")])
    with pytest.raises(InventoryError, match="already has a number"):
        add_identifier(labelled, "MAGFEST_TAG", "FEST00001")
    tagged = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "FEST00002")])
    with pytest.raises(InventoryError) as raised:
        add_identifier(tagged, "FESTOPS_LABEL", "U-00002")
    assert str(raised.value) == NUMBER_REFUSAL
    assert _active(labelled) == [("FESTOPS_LABEL", "U-00001")]


def test_add_unit_refuses_to_start_with_both_numbers(inv):
    with pytest.raises(InventoryError, match="already has a number"):
        add_unit(inv.laptop, None, ACTOR, [
            ("FESTOPS_LABEL", "U-00003"), ("MAGFEST_TAG", "FEST00003")])
    db.session.rollback()
    assert InvUnit.query.count() == 0


def test_replacing_a_u_label_with_a_tag_retires_the_old_number(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("FESTOPS_LABEL", "U-00001"), ("SERIAL", "sn-1")])
    number = replace_number(unit, "MAGFEST_TAG", "fest00001", ACTOR)
    assert number.kind == "MAGFEST_TAG" and number.retired_at is None
    assert _active(unit) == [("MAGFEST_TAG", "FEST00001"), ("SERIAL", "SN-1")]
    assert resolve("U-00001").kind == KIND_UNKNOWN
    found = resolve("FEST00001")
    assert found.kind == KIND_UNIT and found.unit.id == unit.id


def test_replacing_a_number_with_itself_changes_nothing(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "FEST00001")])
    original = unit.identifiers[0]
    assert replace_number(unit, "MAGFEST_TAG", " fest00001 ", ACTOR) is original
    assert len(unit.identifiers) == 1 and original.retired_at is None


def test_a_number_held_elsewhere_is_refused_and_the_unit_keeps_its_own(inv):
    add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "FEST00001")])
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-00002")])
    with pytest.raises(InventoryError, match="FEST00001 is already on another unit"):
        replace_number(unit, "MAGFEST_TAG", "FEST00001", ACTOR)
    assert _active(unit) == [("FESTOPS_LABEL", "U-00002")]


def test_replace_number_refuses_a_kind_that_is_not_a_number(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "FEST00001")])
    with pytest.raises(InventoryError, match="not a unit number"):
        replace_number(unit, "SERIAL", "sn-9", ACTOR)
    assert _active(unit) == [("MAGFEST_TAG", "FEST00001")]


def test_a_rented_unit_is_not_limited_by_this_rule(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "R-00001")])
    unit.ownership = INV_OWNERSHIP_RENTED
    db.session.flush()
    add_identifier(unit, "MAGFEST_TAG", "FEST00009")
    assert _active(unit) == [("FESTOPS_LABEL", "R-00001"), ("MAGFEST_TAG", "FEST00009")]


def test_retire_received_unit_retires_every_identifier_and_stops_resolving(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("MAGFEST_TAG", "FEST00001"), ("SERIAL", "sn-1")])
    assert retire_received_unit(unit, ACTOR) is None
    assert unit.condition == "RETIRED"
    assert _active(unit) == []
    assert resolve("FEST00001").kind == KIND_UNKNOWN
    assert resolve("sn-1").kind == KIND_UNKNOWN


def test_an_owned_unit_cannot_take_a_second_number_of_the_same_kind(inv):
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("MAGFEST_TAG", "FEST00001")])
    with pytest.raises(InventoryError, match="already has a number"):
        add_identifier(unit, "MAGFEST_TAG", "FEST00002")

