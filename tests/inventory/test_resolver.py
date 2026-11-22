"""The scan resolver: one answer per scanned value, or a request to choose."""
from app import db
from app.inventory import labels
from app.inventory.ledger import Actor, add_unit, retire_identifier
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_ITEM, KIND_UNIT, KIND_UNKNOWN,
    KIND_UNUSED, resolve,
)
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:invadmin")


def test_labels_tags_and_serials_each_find_their_thing(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    tv = add_unit(inv.laptop, bin_, ACTOR, [("MAGFEST_TAG", "tech00412"), ("SERIAL", "SN4471")])
    assert resolve("c-0001").kind == KIND_CONTAINER
    assert resolve("c-0001").container == bin_
    assert resolve("TECH00412").unit == tv
    assert resolve("TECH412").kind == KIND_UNKNOWN
    assert resolve("sn4471").unit == tv


def test_a_festops_unit_label_finds_the_unit(inv):
    labels.allocate("U", 1)
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-00001")])
    assert resolve(" u-00001 ").unit == unit


def test_letters_and_digits_serial_is_not_a_tag(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "ABC123")])
    result = resolve("ABC123")
    assert result.kind == KIND_UNIT and result.unit == unit


def test_shared_serial_is_ambiguous_and_unknown_says_so(inv):
    one = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "X1")])
    two = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "X1")])
    result = resolve("x1")
    assert result.kind == KIND_AMBIGUOUS and result.unit is None
    assert set(result.candidates) == {one, two}
    assert resolve("ZZZ999").kind == KIND_UNKNOWN


def test_a_vendor_tag_shared_by_two_vendors_is_ambiguous(inv):
    one = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "a")])
    two = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "b")])
    one.vendor_name, two.vendor_name = "Acme", "Other"
    db.session.flush()
    from app.inventory.ledger import add_identifier
    add_identifier(one, "VENDOR_TAG", "A1234")
    add_identifier(two, "VENDOR_TAG", "A1234")
    result = resolve("a1234")
    assert result.kind == KIND_AMBIGUOUS
    assert set(result.candidates) == {one, two}


def test_tag_outranks_a_serial_with_the_same_text(inv):
    tagged = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "T100")])
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "T100")])
    assert resolve("T100").unit == tagged


def test_mac_matches_with_any_separator(inv):
    unit = add_unit(inv.wap, None, ACTOR, [("MAC", "aa:bb:cc:dd:ee:ff")])
    assert resolve("AA-BB-CC-DD-EE-FF").unit == unit


def test_retired_identifiers_do_not_resolve(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "T200"), ("SERIAL", "S200")])
    for identifier in unit.identifiers:
        retire_identifier(identifier)
    assert resolve("T200").kind == KIND_UNKNOWN
    assert resolve("S200").kind == KIND_UNKNOWN


def test_a_retired_unit_label_is_not_offered_as_unused(inv):
    labels.allocate("U", 1)
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-00001")])
    retire_identifier(unit.identifiers[0])
    assert resolve("U-00001").kind == KIND_UNKNOWN


def test_issued_unbound_label_is_unused_and_unissued_is_unknown(inv):
    labels.allocate("U", 3)
    labels.allocate("C", 1)
    result = resolve("U-00003")
    assert result.kind == KIND_UNUSED and result.prefix == "U"
    assert resolve("C-0001").prefix == "C"
    assert resolve("C-9999").kind == KIND_UNKNOWN
    assert resolve("").kind == KIND_UNKNOWN


def test_an_item_label_or_barcode_finds_the_item(inv):
    labels.allocate("I", 1)
    inv.pens.barcode = "I-0001"
    inv.laptop.barcode = "012345678905"
    db.session.flush()
    assert resolve("i-0001").kind == KIND_ITEM
    assert resolve("i-0001").item == inv.pens
    assert resolve("012345678905").item == inv.laptop


def test_a_typed_label_resolves_to_its_canonical_code(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    assert resolve("c-1").container == bin_
    labels.allocate("U", 3)
    db.session.flush()
    result = resolve("U-3")
    assert result.kind == KIND_UNUSED and result.code == "U-00003"


def test_a_serial_shaped_like_a_label_still_resolves(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    unit = add_unit(inv.laptop, bin_, ACTOR, [("SERIAL", "C-1042")])
    assert resolve("C-1042").unit == unit


def test_a_serial_and_mac_on_two_units_are_ambiguous(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    add_unit(inv.laptop, bin_, ACTOR, [("SERIAL", "AABBCCDDEEFF")])
    add_unit(inv.laptop, bin_, ACTOR, [("MAC", "AA:BB:CC:DD:EE:FF")])
    result = resolve("aa-bb-cc-dd-ee-ff")
    assert result.kind == KIND_AMBIGUOUS and len(result.candidates) == 2

