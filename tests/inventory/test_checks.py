"""Check rules: opening, finishing, setting aside and the last-result lookup."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app import db
from app.inventory.checks import (
    active_check_items, unit_code, finish_check, last_result, open_check, prep_status, set_aside,
)
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.models import EventCycle, InvCheck, InvCheckItem, SupplyItem
from tests.inventory.helpers import make_container

ACTOR = Actor(user_id="test:invadmin")
NO_EVENT = "No default event is set. Ask an inventory admin."


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.flush()
    return cycle


@pytest.fixture
def tv_checklist(inv, event):
    """A TV model with two lines, one TV with a tag and serial, and a second TV."""
    tv_item = SupplyItem(category_id=inv.category.id, item_name="Samsung 55 TV", unit="each",
                         is_serialized=True, is_expendable=False)
    printer_item = SupplyItem(category_id=inv.category.id, item_name="Zebra Printer",
                              unit="each", is_serialized=True, is_expendable=False)
    db.session.add_all([tv_item, printer_item])
    db.session.flush()
    power = InvCheckItem(item_id=tv_item.id, position=0, prompt="Powered on?",
                         missing_means="FAIL", is_active=True)
    remote = InvCheckItem(item_id=tv_item.id, position=1, prompt="Remote present?",
                          missing_means="NOTE", is_active=True)
    db.session.add_all([power, remote])
    where = make_container(inv, "C-0001", kind="BIN")
    tv = add_unit(tv_item, where, ACTOR, [("MAGFEST_TAG", "TECH00412"), ("SERIAL", "SN4471")])
    tv2 = add_unit(tv_item, where, ACTOR, [("MAGFEST_TAG", "TECH00413")])
    printer = add_unit(printer_item, where, ACTOR,
                       [("MAGFEST_TAG", "TECH00500"), ("SERIAL", "PS1")])
    db.session.commit()
    return SimpleNamespace(tv_item=tv_item, printer_item=printer_item, power=power,
                           remote=remote, tv=tv, tv2=tv2, printer=printer, where=where)


# ---- rules ----

def test_nothing_passes_by_default_and_note_lines_pass_with_a_note(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    with pytest.raises(InventoryError, match="Answer every line"):
        finish_check(check, {t.power.id: "YES"}, ACTOR)
    finish_check(check, {t.power.id: "YES", t.remote.id: "NO"}, ACTOR)
    assert check.status == "PASSED" and "Remote present?" in check.note
    assert prep_status(t.tv, event) == "CHECKED"


def test_a_value_other_than_yes_or_no_does_not_count_as_an_answer(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    with pytest.raises(InventoryError, match="Answer every line"):
        finish_check(check, {t.power.id: "YES", t.remote.id: "MAYBE"}, ACTOR)
    assert check.status == "OPEN"


def test_failed_check_changes_condition_never_location(inv, event, tv_checklist):
    t = tv_checklist
    where = t.tv.current_container_id
    check = open_check(t.tv, "UNIT", ACTOR, event)
    finish_check(check, {t.power.id: "NO", t.remote.id: "YES"}, ACTOR, failed_condition="REPAIR")
    assert t.tv.condition == "REPAIR" and t.tv.current_container_id == where
    assert check.previous_condition == "IN_SERVICE"
    assert prep_status(t.tv, event) == "FAILED_CHECK"
    with pytest.raises(InventoryError, match="repair or retire"):
        finish_check(open_check(t.tv2, "UNIT", ACTOR, event),
                     {t.power.id: "NO", t.remote.id: "YES"}, ACTOR)


def test_a_failed_check_with_a_condition_on_a_passed_check_is_refused(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    with pytest.raises(InventoryError, match="checklist changed"):
        finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR,
                     failed_condition="REPAIR")
    assert t.tv.condition == "IN_SERVICE"


def test_a_model_with_no_lines_takes_a_pass_or_a_fail_with_a_condition(inv, event, tv_checklist):
    t = tv_checklist
    InvCheckItem.query.filter_by(item_id=t.tv_item.id).delete()
    db.session.commit()
    assert active_check_items(t.tv_item) == []
    check = open_check(t.tv, "UNIT", ACTOR, event)
    finish_check(check, {}, ACTOR)
    assert check.status == "PASSED"
    second = open_check(t.tv2, "UNIT", ACTOR, event)
    finish_check(second, {}, ACTOR, failed_condition="RETIRED", note="Cracked panel")
    assert second.status == "FAILED" and second.note == "Cracked panel"
    assert t.tv2.condition == "RETIRED"


def test_a_second_open_check_names_the_first(inv, event, tv_checklist):
    t = tv_checklist
    open_check(t.tv, "UNIT", ACTOR, event)
    with pytest.raises(InventoryError, match="TECH00412 is not finished"):
        open_check(t.tv2, "UNIT", ACTOR, event)


def test_scanning_the_open_unit_again_resumes_its_check(inv, event, tv_checklist):
    t = tv_checklist
    first = open_check(t.tv, "UNIT", ACTOR, event)
    assert open_check(t.tv, "UNIT", ACTOR, event).id == first.id
    assert InvCheck.query.count() == 1


def test_set_aside_leaves_the_unit_unchecked_and_frees_the_shift(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    set_aside(check, ACTOR)
    assert check.status == "SET_ASIDE" and prep_status(t.tv, event) is None
    assert open_check(t.tv2, "UNIT", ACTOR, event).status == "OPEN"


def test_only_the_unit_station_can_open_a_check(inv, event, tv_checklist):
    t = tv_checklist
    for old in ("TV_CHECK", "PRINTER_CHECK", "TAG", "COUNT"):
        with pytest.raises(InventoryError, match="is not a check station"):
            open_check(t.tv, old, ACTOR, event)
    assert InvCheck.query.count() == 0
    laptop = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "LAP1")])
    assert open_check(laptop, "UNIT", ACTOR, event).status == "OPEN"



def test_every_write_refuses_without_a_default_event(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    event.is_default = False
    db.session.commit()
    with pytest.raises(InventoryError, match=NO_EVENT):
        open_check(t.tv2, "UNIT", ACTOR, None)
    with pytest.raises(InventoryError, match=NO_EVENT):
        finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR)
    with pytest.raises(InventoryError, match=NO_EVENT):
        set_aside(check, ACTOR)


def test_a_pass_returns_a_repaired_or_lost_unit_to_service(inv, event, tv_checklist):
    t = tv_checklist
    yes = {t.power.id: "YES", t.remote.id: "YES"}
    for start in ("REPAIR", "LOST"):
        t.tv.condition = start
        check = open_check(t.tv, "UNIT", ACTOR, event)
        finish_check(check, yes, ACTOR)
        assert t.tv.condition == "IN_SERVICE" and check.previous_condition == start
    check = open_check(t.tv, "UNIT", ACTOR, event)
    finish_check(check, yes, ACTOR)
    assert t.tv.condition == "IN_SERVICE" and check.previous_condition == "IN_SERVICE"


def test_already_checked_shows_the_local_time_not_utc(inv, event, tv_checklist):
    t = tv_checklist
    check = open_check(t.tv, "UNIT", ACTOR, event)
    finish_check(check, {t.power.id: "YES", t.remote.id: "YES"}, ACTOR)
    check.finished_at = datetime(2026, 11, 8, 2, 23)
    db.session.commit()
    # INV_LOCAL_TZ is US Eastern, five hours behind UTC in November.
    assert last_result(t.tv, event)["when"] == "Nov 7, 9:23 PM"


def test_a_unit_with_only_a_serial_is_named_by_its_serial(inv, event, tv_checklist):
    bare = add_unit(tv_checklist.tv_item, tv_checklist.where, ACTOR, [("SERIAL", "SN7777")])
    db.session.commit()
    assert unit_code(bare) == "SN7777"
