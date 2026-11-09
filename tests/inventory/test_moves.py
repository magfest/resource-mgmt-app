"""Rules for the Move station: placing containers and assets by scan, moving loose supplies."""
import pytest

from app import db
from app.inventory import labels
from app.inventory.checks import find_balance_mismatches
from app.inventory.codes import SIGN_IN_CARD
from app.inventory.counts import MAX_QUANTITY
from app.inventory.flags import raise_flag
from app.inventory.ledger import (
    Actor, InventoryError, add_stock, add_unit, set_counted_quantity, stock_quantity,
)
from app.inventory.moves import (
    ASSETS_ONE_BY_ONE, DESTINATION_AGAIN, SCAN_PLACE_FIRST, SOURCE_NOT_A_PLACE, UNKNOWN_CODE,
    USE_SUPPLIES, destination_from, move_create, move_scan, move_supplies, place_title,
    supplies_source,
)
from app.models import InvContainer, InvFlag, InvMovement
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:operator")
CARD = "OP-0123456789AB"


def _zone(inv):
    return make_container(inv, "C-0005", kind="LOCATION", name="Zone A")


def _moves():
    return InvMovement.query.filter_by(action="MOVE").count()


def test_place_title_names_kind_code_and_name(inv):
    assert place_title(make_container(inv, "C-0040")) == "Pallet C-0040"
    assert place_title(_zone(inv)) == "Location C-0005 Zone A"


def test_first_scan_sets_the_destination_and_writes_nothing(inv):
    make_container(inv, "C-0040")
    answer = move_scan("C-0040", None, ACTOR, None)
    assert answer["destination"] == {"code": "C-0040", "title": "Pallet C-0040"}
    assert answer["message"] == "Going to Pallet C-0040. Scan each thing as you put it there."
    assert InvMovement.query.count() == 0


def test_a_tote_scanned_with_a_destination_moves_onto_it(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    tote = make_container(inv, "C-0042", kind="BIN", parent=zone)
    answer = move_scan("C-0042", pallet, ACTOR, None)
    assert answer == {"moved": True, "message": "C-0042 is on C-0040."}
    move = InvMovement.query.filter_by(action="MOVE").one()
    assert (move.subject_container_id, move.from_container_id, move.to_container_id,
            move.performed_by_user_id) == (tote.id, zone.id, pallet.id, "test:operator")
    assert tote.parent_id == pallet.id


def test_a_fixed_location_always_becomes_the_destination(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    answer = move_scan("C-0005", pallet, ACTOR, None)
    assert answer["destination"] == {"code": "C-0005", "title": "Location C-0005 Zone A"}
    assert zone.parent_id is None
    assert InvMovement.query.count() == 0


def test_a_pallet_goes_into_zone_a(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    assert move_scan("C-0040", zone, ACTOR, None)["message"] == "C-0040 is in C-0005."
    assert pallet.parent_id == zone.id


def test_scanning_the_destination_again_only_says_so(inv):
    pallet = make_container(inv, "C-0040")
    assert move_scan("C-0040", pallet, ACTOR, None) == \
        {"same": True, "message": "C-0040 is where things are going."}
    assert InvMovement.query.count() == 0


def test_a_thing_already_on_the_destination_writes_nothing(inv):
    pallet = make_container(inv, "C-0040")
    make_container(inv, "C-0042", kind="BIN", parent=pallet)
    add_unit(inv.laptop, pallet, ACTOR, [("MAGFEST_TAG", "FEST10002")])
    assert move_scan("C-0042", pallet, ACTOR, None) == \
        {"moved": False, "message": "C-0042 is on C-0040 already."}
    assert move_scan("FEST10002", pallet, ACTOR, None) == \
        {"moved": False, "message": "FEST10002 is on C-0040 already."}
    assert _moves() == 0


def test_an_asset_moves_onto_the_destination(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    unit = add_unit(inv.laptop, zone, ACTOR, [("MAGFEST_TAG", "FEST10001")])
    assert move_scan("FEST10001", pallet, ACTOR, None) == \
        {"moved": True, "message": "FEST10001 is on C-0040."}
    assert unit.current_container_id == pallet.id


def test_an_asset_with_no_number_is_named_by_its_serial(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    unit = add_unit(inv.laptop, zone, ACTOR, [("SERIAL", "SN-INVENTED-1")])
    assert move_scan("SN-INVENTED-1", pallet, ACTOR, None) == \
        {"moved": True, "message": "SN-INVENTED-1 is on C-0040."}
    assert unit.current_container_id == pallet.id


def test_a_container_cannot_go_inside_something_it_holds(inv):
    pallet = make_container(inv, "C-0040")
    tote = make_container(inv, "C-0042", kind="BIN", parent=pallet)
    with pytest.raises(InventoryError, match="cannot go inside itself"):
        move_scan("C-0040", tote, ACTOR, None)
    assert pallet.parent_id is None and tote.parent_id == pallet.id
    assert _moves() == 0


@pytest.mark.parametrize("case", ["card", "card_no_dest", "asset_no_dest", "item",
                                  "unknown", "inactive", "inactive_dest"])
def test_move_scan_refusals(inv, case):
    pallet = make_container(inv, "C-0040")
    zone = _zone(inv)
    tote = make_container(inv, "C-0042", kind="BIN", parent=zone)
    add_unit(inv.laptop, zone, ACTOR, [("MAGFEST_TAG", "FEST10003")])
    inv.pens.barcode = "0712345678901"
    db.session.flush()
    before = InvMovement.query.count()
    if case == "card":
        with pytest.raises(InventoryError, match=SIGN_IN_CARD):
            move_scan(CARD, pallet, ACTOR, None)
    elif case == "card_no_dest":
        with pytest.raises(InventoryError, match=SIGN_IN_CARD):
            move_scan(CARD, None, ACTOR, None)
    elif case == "asset_no_dest":
        with pytest.raises(InventoryError, match=SCAN_PLACE_FIRST):
            move_scan("FEST10003", None, ACTOR, None)
    elif case == "item":
        with pytest.raises(InventoryError, match=USE_SUPPLIES):
            move_scan("0712345678901", pallet, ACTOR, None)
    elif case == "unknown":
        with pytest.raises(InventoryError) as raised:
            move_scan("NOPE-1", pallet, ACTOR, None)
        assert str(raised.value) == UNKNOWN_CODE
    elif case == "inactive":
        tote.is_active = False
        db.session.flush()
        with pytest.raises(InventoryError, match="C-0042 is inactive."):
            move_scan("C-0042", pallet, ACTOR, None)
        assert tote.parent_id == zone.id
    elif case == "inactive_dest":
        pallet.is_active = False
        db.session.flush()
        with pytest.raises(InventoryError) as raised:
            destination_from("C-0040")
        assert str(raised.value) == DESTINATION_AGAIN
    assert InvMovement.query.count() == before


def test_destination_from_returns_an_active_container(inv):
    pallet = make_container(inv, "C-0040")
    assert destination_from("C-0040") is pallet
    for code in (None, "NOPE-1"):
        with pytest.raises(InventoryError, match=DESTINATION_AGAIN):
            destination_from(code)


def test_a_new_label_offers_kinds_for_where_it_goes(inv):
    home = _zone(inv)
    pallet = make_container(inv, "C-0040")
    code = labels.allocate("C", 1)[0]
    as_dest = move_scan(code, None, ACTOR, home)
    assert as_dest["unused"] is True and as_dest["where"] == "It goes in C-0005 Zone A."
    assert as_dest["code"] == code
    assert {k["name"] for k in as_dest["kinds"]} == {"Location", "Pallet", "Bin"}
    as_thing = move_scan(code, pallet, ACTOR, home)
    assert as_thing["where"] == "It goes on C-0040."
    assert {k["name"] for k in as_thing["kinds"]} == {"Pallet", "Bin"}
    in_zone = move_scan(code, home, ACTOR, None)
    assert in_zone["where"] == "It goes in C-0005."
    assert {k["name"] for k in in_zone["kinds"]} == {"Pallet", "Bin"}
    no_home = move_scan(code, None, ACTOR, None)
    assert no_home["where"] == "This scanner has no home location, so it goes at the top level."
    assert InvContainer.query.filter_by(label_code=code).first() is None


def test_an_unused_asset_label_has_nothing_attached(inv):
    pallet = make_container(inv, "C-0040")
    code = labels.allocate("U", 1)[0]
    for destination in (None, pallet):
        with pytest.raises(InventoryError, match="This label has nothing attached to it yet."):
            move_scan(code, destination, ACTOR, None)


def test_a_new_destination_is_made_in_home_and_a_new_thing_on_the_destination(inv):
    home = _zone(inv)
    pallet = make_container(inv, "C-0040")
    a, b, c = labels.allocate("C", 3)
    assert move_create(a, inv.kinds["PALLET"], None, ACTOR, home)["destination"]["code"] == a
    assert move_create(b, inv.kinds["BIN"], pallet, ACTOR, home) == \
        {"moved": True, "message": f"{b} is on C-0040."}
    assert move_create(c, inv.kinds["PALLET"], None, ACTOR, None)["destination"]["code"] == c
    parents = {x.label_code: x.parent_id for x in InvContainer.query.all()}
    assert parents[a] == home.id and parents[b] == pallet.id and parents[c] is None


def test_a_new_thing_cannot_be_a_location(inv):
    home = _zone(inv)
    pallet = make_container(inv, "C-0040")
    code = labels.allocate("C", 1)[0]
    with pytest.raises(InventoryError, match="A location cannot go on something."):
        move_create(code, inv.kinds["LOCATION"], pallet, ACTOR, home)
    assert InvContainer.query.filter_by(label_code=code).first() is None


def test_move_create_refuses_a_used_label_and_a_missing_kind(inv):
    make_container(inv, "C-0040")
    code = labels.allocate("C", 1)[0]
    with pytest.raises(InventoryError, match="C-0040 is not an unused container label."):
        move_create("C-0040", inv.kinds["PALLET"], None, ACTOR, None)
    with pytest.raises(InventoryError, match="Pick a container kind."):
        move_create(code, None, None, ACTOR, None)
    with pytest.raises(InventoryError, match="Scan a C- label for a container."):
        move_create(None, inv.kinds["PALLET"], None, ACTOR, None)


def test_supplies_move_once_between_two_places(inv):
    pallet = make_container(inv, "C-0040")
    other = make_container(inv, "C-0041")
    add_stock(inv.pens, pallet, 24, ACTOR)
    assert move_supplies(inv.pens, pallet, other, 12, ACTOR) == \
        {"message": "Moved 12 Blue Pen from C-0040 to C-0041.", "short": False}
    assert stock_quantity(inv.pens, pallet) == 12 and stock_quantity(inv.pens, other) == 12
    assert InvFlag.query.count() == 0
    assert find_balance_mismatches() == []


def test_a_short_source_is_accepted_zeroed_and_flagged(inv):
    pallet = make_container(inv, "C-0040")
    other = make_container(inv, "C-0041")
    add_stock(inv.pens, pallet, 1, ACTOR)
    answer = move_supplies(inv.pens, pallet, other, 5, ACTOR)
    assert answer == {"short": True, "message": "Moved 5 Blue Pen from C-0040 to C-0041. "
                                                "C-0040 is flagged for a lead to recount."}
    flag = InvFlag.query.one()
    assert (flag.container_id, flag.reason, flag.station_code, flag.note) == (
        pallet.id, "WRONG_DATA", "MOVE",
        "Recount: moved 5 Blue Pen off, but only 1 was recorded here.")
    assert stock_quantity(inv.pens, pallet) == 0 and find_balance_mismatches() == []


def test_an_open_wrong_data_flag_stands_and_the_move_succeeds(inv):
    pallet = make_container(inv, "C-0040")
    other = make_container(inv, "C-0041")
    raise_flag(ACTOR, "COUNT", "WRONG_DATA", container=pallet, note="Invented note")
    answer = move_supplies(inv.pens, pallet, other, 3, ACTOR)
    assert answer["short"] is True
    assert InvFlag.query.count() == 1 and stock_quantity(inv.pens, other) == 3


def test_an_estimated_row_is_short_like_any_other(inv):
    pallet = make_container(inv, "C-0040")
    other = make_container(inv, "C-0041")
    set_counted_quantity(inv.pens, pallet, 2, ACTOR, is_approximate=True)
    assert move_supplies(inv.pens, pallet, other, 4, ACTOR)["short"] is True


def test_supplies_refusals(inv):
    pallet = make_container(inv, "C-0040")
    other = make_container(inv, "C-0041")
    add_stock(inv.pens, pallet, 10, ACTOR)
    before = InvMovement.query.count()
    cases = [
        ((inv.pens, pallet, pallet, 1),
         "C-0040 is where things are going. Scan where they are coming from."),
        ((inv.laptop, pallet, other, 1), ASSETS_ONE_BY_ONE),
        ((inv.pens, pallet, other, 0), "Quantity must be a whole number above zero."),
        ((inv.pens, pallet, other, "3"), "Quantity must be a whole number above zero."),
        ((inv.pens, pallet, other, MAX_QUANTITY + 1), "That number is too large."),
        ((None, pallet, other, 1), "Pick an item."),
    ]
    for (item, source, destination, quantity), message in cases:
        with pytest.raises(InventoryError) as raised:
            move_supplies(item, source, destination, quantity, ACTOR)
        assert str(raised.value) == message
    inv.pens.is_active = False
    other.is_active = False
    db.session.flush()
    with pytest.raises(InventoryError, match="Blue Pen is inactive."):
        move_supplies(inv.pens, pallet, other, 1, ACTOR)
    inv.pens.is_active = True
    db.session.flush()
    with pytest.raises(InventoryError, match="C-0041 is inactive."):
        move_supplies(inv.pens, pallet, other, 1, ACTOR)
    assert InvMovement.query.count() == before


def test_a_supplies_source_must_be_a_place_with_a_record(inv):
    zone = _zone(inv)
    pallet = make_container(inv, "C-0040")
    add_unit(inv.laptop, zone, ACTOR, [("MAGFEST_TAG", "FEST10004")])
    code = labels.allocate("C", 1)[0]
    cases = [
        (code, f"Nothing is recorded on {code} yet. Scan where they are coming from."),
        ("FEST10004", SOURCE_NOT_A_PLACE),
        ("NOPE-1", SOURCE_NOT_A_PLACE),
        (CARD, SIGN_IN_CARD),
        ("C-0040", "C-0040 is where things are going. Scan where they are coming from."),
    ]
    for scanned, message in cases:
        with pytest.raises(InventoryError) as raised:
            supplies_source(scanned, pallet)
        assert str(raised.value) == message
    answer = supplies_source("C-0005", pallet)
    assert answer["source"] == {"code": "C-0005", "title": "Location C-0005 Zone A"}
    assert answer["usual"] == []
