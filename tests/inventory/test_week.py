"""This week's status for many units at once."""
import pytest

from app import db
from app.inventory.ledger import Actor, add_unit
from app.inventory.week import (
    WEEK_BROKEN, WEEK_NOT_SEEN, WEEK_WORKS, show_condition, week_statuses, week_totals,
)
from app.models import EventCycle, InvCheck, InvPrepStatus, InvUnit

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.commit()
    return cycle


def _unit(inv, name, number=None):
    ids = [("FESTOPS_LABEL", number)] if number else []
    unit = add_unit(inv.laptop, None, ACTOR, ids, name=name)
    db.session.flush()
    return unit


def _prep(unit, event, status, check_status=None):
    check_id = None
    if check_status:
        check = InvCheck(unit_id=unit.id, event_cycle_id=event.id, station_code="UNIT",
                         status=check_status)
        db.session.add(check)
        db.session.flush()
        check_id = check.id
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status=status,
                                 check_id=check_id))
    db.session.flush()


@pytest.fixture
def mixed(inv, event):
    works = _unit(inv, "Works", "U-0001")
    broken = _unit(inv, "Broken", "U-0002")
    unseen = _unit(inv, "Unseen", "U-0003")
    unnumbered = _unit(inv, "Unnumbered")
    _prep(works, event, "CHECKED", "PASSED")
    _prep(broken, event, "FAILED_CHECK", "FAILED")
    db.session.commit()
    return works, broken, unseen, unnumbered


def test_statuses_for_many_units(app, event, mixed):
    works, broken, unseen, _ = mixed
    assert week_statuses(event, [works.id, broken.id, unseen.id]) == {
        works.id: WEEK_WORKS, broken.id: WEEK_BROKEN, unseen.id: WEEK_NOT_SEEN}


def test_voided_check_counts_as_not_seen(inv, event):
    unit = _unit(inv, "Undone", "U-0009")
    _prep(unit, event, "CHECKED", "VOIDED")
    _prep(unit, event, "VOIDED", "VOIDED")
    db.session.commit()
    assert week_statuses(event, [unit.id]) == {unit.id: WEEK_NOT_SEEN}


def test_voided_recheck_leaves_the_earlier_check_standing(inv, event):
    unit = _unit(inv, "Rechecked", "U-0010")
    _prep(unit, event, "CHECKED", "PASSED")
    _prep(unit, event, "FAILED_CHECK", "VOIDED")
    db.session.commit()
    assert week_statuses(event, [unit.id]) == {unit.id: WEEK_WORKS}


def test_another_events_rows_do_not_count(inv, event):
    other = EventCycle(code="OLD", name="Old", is_active=True, is_default=False)
    db.session.add(other)
    db.session.flush()
    unit = _unit(inv, "Old check", "U-0011")
    _prep(unit, other, "CHECKED", "PASSED")
    db.session.commit()
    assert week_statuses(event, [unit.id]) == {unit.id: WEEK_NOT_SEEN}


def test_week_totals(app, event, mixed):
    assert week_totals(event) == {"owned": 4, "seen": 2, "not_seen": 2,
                                  "broken": 1, "no_number": 1}


@pytest.mark.parametrize("show,expected", [
    ("seen", {"Works", "Broken"}), ("not_seen", {"Unseen", "Unnumbered"}),
    ("broken", {"Broken"}), ("no_number", {"Unnumbered"})])
def test_show_condition(app, event, mixed, show, expected):
    rows = db.session.query(InvUnit).filter(show_condition(event, show)).all()
    assert {u.name for u in rows} == expected


def test_csv_guard_covers_tab_and_carriage_return():
    from app.inventory.csv_export import csv_response
    body = csv_response("x.csv", ["a"], [["\tx"], ["\rx"], ["=1"], [None]]).get_data(as_text=True)
    assert "'\tx" in body and "'\rx" in body and "'=1" in body
