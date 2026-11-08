"""Unit station rules: finding by digits, numbering, one-pass finish and pallet placement."""
from datetime import datetime

import pytest

from app import db
from app.inventory.checks import finish_check, open_check
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, InventoryError, add_identifier, add_unit
from app.inventory.receive import label_job
from app.inventory.unit_station import (
    digit_candidates, finish_unit, give_unit_number, needs_info_label, number_refusal,
    place_last, unit_card,
)
from app.models import EventCycle, InvCheck, InvPrinter, InvPrintRoute
from app.models.constants import (
    INV_CHECK_FAILED, INV_CHECK_VOIDED, INV_CONDITION_REPAIR, INV_CONDITION_RETIRED,
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG, INV_JOB_FAILED, INV_JOB_OWNED_UNIT,
    INV_OWNERSHIP_RENTED, INV_STATION_UNIT,
)
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:operator")


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.flush()
    return cycle


@pytest.fixture
def printer(inv):
    station, _ = enroll_device("Print PC", "test:invadmin", station_code="PRINT")
    station.last_seen_at = datetime.utcnow()
    p = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=3,
                   device_id=station.id, device_name="Perm")
    db.session.add(p)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=p.id))
    db.session.commit()
    return p


@pytest.fixture
def warehouse(inv):
    return make_container(inv, "C-WH01", kind="LOCATION", name="Warehouse")


def _units(inv, *names):
    units = [add_unit(inv.laptop, None, ACTOR, [], name=n) for n in names]
    db.session.commit()
    return units


def _numbered(inv, name="TV-X-050"):
    unit = _units(inv, name)[0]
    add_identifier(unit, INV_IDENT_MAGFEST_TAG, "FEST91050")
    db.session.commit()
    return unit


def _check(unit, event):
    return open_check(unit, INV_STATION_UNIT, ACTOR, event)


def test_digits_match_the_number_part_exactly(inv):
    tv3, tv13, tv30 = _units(inv, "TV-X-003", "TV-X-013", "TV-X-030")
    assert digit_candidates("3") == [tv3]
    assert digit_candidates("003") == [tv3]


def test_digits_ignore_non_digit_input(inv):
    _units(inv, "TV-X-003")
    assert digit_candidates("") == []
    assert digit_candidates("0") == []
    assert digit_candidates("3a") == []


def test_digits_skip_units_that_have_a_number(inv):
    unit = _units(inv, "TV-X-004")[0]
    add_identifier(unit, INV_IDENT_MAGFEST_TAG, "FEST90004")
    assert digit_candidates("4") == []


def test_digits_skip_units_with_a_u_label_and_retired_units(inv):
    labelled, retired = _units(inv, "TV-X-005", "TV-X-006")
    add_identifier(labelled, INV_IDENT_FESTOPS_LABEL, "U-000005")
    retired.condition = INV_CONDITION_RETIRED
    db.session.commit()
    assert digit_candidates("5") == [] and digit_candidates("6") == []


def test_number_refusal_messages(inv):
    unit = _units(inv, "TV-X-007")[0]
    assert number_refusal(unit) is None
    unit.ownership = INV_OWNERSHIP_RENTED
    assert number_refusal(unit) == "A rented unit cannot be given a U- number."
    unit.ownership = "OWNED"
    add_identifier(unit, INV_IDENT_MAGFEST_TAG, "FEST90007")
    assert number_refusal(unit) == "This unit already has a number."
    unit.condition = INV_CONDITION_RETIRED
    assert number_refusal(unit) == "A retired unit cannot be given a number."


def test_give_unit_number_adds_a_u_label_once(inv):
    unit = _units(inv, "TV-X-008")[0]
    code = give_unit_number(unit)
    assert code.startswith("U-")
    with pytest.raises(InventoryError, match="already has a number"):
        give_unit_number(unit)


def test_info_label_needed_when_never_printed(inv, printer):
    assert needs_info_label(_numbered(inv)) is True


def test_unnumbered_unit_needs_no_info_label(inv):
    assert needs_info_label(_units(inv, "TV-X-009")[0]) is False


def test_info_label_not_queued_twice_while_waiting(inv, printer):
    unit = _numbered(inv)
    label_job(unit, ACTOR, None)
    assert needs_info_label(unit) is False


def test_failed_print_counts_as_not_printed(inv, printer):
    unit = _numbered(inv)
    label_job(unit, ACTOR, None).status = INV_JOB_FAILED
    assert needs_info_label(unit) is True


def test_changed_details_need_a_new_label(inv, printer):
    unit = _numbered(inv)
    label_job(unit, ACTOR, None)
    unit.name = "TV-X-051"
    assert needs_info_label(unit) is True


def test_finish_places_the_unit_home_and_queues_its_label(inv, event, printer, warehouse):
    unit = _numbered(inv)
    out = finish_unit(_check(unit, event), {}, ACTOR, home=warehouse)
    assert out["placed"] == "Warehouse" and out["label"] == "queued"
    assert out["code"] == "FEST91050" and out["name"] == "TV-X-050"
    assert unit.current_container_id == warehouse.id


def test_second_finish_does_not_queue_a_second_label(inv, event, printer, warehouse):
    unit = _numbered(inv)
    finish_unit(_check(unit, event), {}, ACTOR, home=warehouse)
    out = finish_unit(_check(unit, event), {}, ACTOR, home=warehouse)
    assert out["label"] == "none"


def test_retired_unit_is_not_placed_and_gets_no_label(inv, event, printer, warehouse):
    unit = _numbered(inv)
    out = finish_unit(_check(unit, event), {}, ACTOR, home=warehouse,
                      failed_condition=INV_CONDITION_RETIRED)
    assert out["placed"] is None and out["label"] == "none"
    assert out["code"] == "FEST91050"
    assert unit.current_container_id is None


def test_broken_needs_no_note(inv, event, printer, warehouse):
    unit = _numbered(inv)
    out = finish_unit(_check(unit, event), {}, ACTOR, home=warehouse,
                      failed_condition=INV_CONDITION_REPAIR)
    assert out["status"] == INV_CHECK_FAILED and out["condition"] == INV_CONDITION_REPAIR


def test_no_printer_route_keeps_the_check_and_reports_the_error(inv, event, warehouse):
    unit = _numbered(inv)
    check = _check(unit, event)
    out = finish_unit(check, {}, ACTOR, home=warehouse)
    db.session.commit()
    assert "No printer is set" in out["label"]
    assert db.session.get(InvCheck, check.id).status == "PASSED"
    assert unit.current_container_id == warehouse.id


def test_finish_without_a_home_leaves_the_unit_where_it_is(inv, event, printer):
    unit = _numbered(inv)
    out = finish_unit(_check(unit, event), {}, ACTOR, home=None)
    assert out["placed"] is None and unit.current_container_id is None


def _finished(inv, event, name, tag):
    unit = _units(inv, name)[0]
    add_identifier(unit, INV_IDENT_MAGFEST_TAG, tag)
    finish_check(_check(unit, event), {}, ACTOR)
    db.session.commit()
    return unit


def test_place_last_puts_the_last_unit_on_the_pallet(inv, event):
    unit = _finished(inv, event, "TV-X-060", "FEST91060")
    pallet = make_container(inv, "C-0002", name="Pallet 1")
    assert place_last(ACTOR, "C-0002", unit.id) == "Pallet 1"
    assert unit.current_container_id == pallet.id


def test_place_last_refuses_a_unit_that_is_not_the_last_one(inv, event):
    first = _finished(inv, event, "TV-X-061", "FEST91061")
    _finished(inv, event, "TV-X-062", "FEST91062")
    make_container(inv, "C-0002")
    with pytest.raises(InventoryError, match="Scan the unit again"):
        place_last(ACTOR, "C-0002", first.id)


def test_place_last_ignores_a_voided_check(inv, event):
    first = _finished(inv, event, "TV-X-063", "FEST91063")
    second = _finished(inv, event, "TV-X-064", "FEST91064")
    InvCheck.query.filter_by(unit_id=second.id).one().status = INV_CHECK_VOIDED
    db.session.commit()
    make_container(inv, "C-0002")
    place_last(ACTOR, "C-0002", first.id)


def test_place_last_refuses_a_retired_unit(inv, event):
    unit = _numbered(inv)
    finish_unit(_check(unit, event), {}, ACTOR, home=None, failed_condition=INV_CONDITION_RETIRED)
    make_container(inv, "C-0002")
    with pytest.raises(InventoryError, match="A retired unit is not placed."):
        place_last(ACTOR, "C-0002", unit.id)


def test_place_last_raises_the_container_refusal(inv, event):
    unit = _finished(inv, event, "TV-X-065", "FEST91065")
    with pytest.raises(InventoryError):
        place_last(ACTOR, "C-9999", unit.id)


def test_open_check_needs_no_check_station(inv, event):
    unit = _units(inv, "TV-X-070")[0]
    assert inv.laptop.check_station is None
    assert _check(unit, event).station_code == "UNIT"


def test_unit_card(inv):
    unit = _numbered(inv)
    card = unit_card(unit)
    assert card["unit_id"] == unit.id and card["name"] == "TV-X-050"
    assert card["type"] == "Laptop" and card["code"] == "FEST91050"
    assert set(card) == {"unit_id", "name", "type", "spec", "code"}
