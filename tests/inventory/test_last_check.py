"""A unit's latest check and last-seen time, across events."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event as sa_event

from app import db
from app.inventory.last_check import (
    CHECK_BROKEN, CHECK_WORKS, check_filter, check_totals, check_word, default_since,
    last_seen_dates, latest_check_rows, parse_since, since_start,
)
from app.inventory.ledger import Actor, add_unit
from app.inventory.printing import local_day, local_today
from app.models import EventCycle, InvCheck, InvPrepStatus, InvUnit

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def smf(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.commit()
    return cycle


@pytest.fixture
def west(inv):
    cycle = EventCycle(code="MWEST2027", name="MAGWest 2027", is_active=True, is_default=False)
    db.session.add(cycle)
    db.session.commit()
    return cycle


def _unit(inv, name, number=None):
    ids = [("FESTOPS_LABEL", number)] if number else []
    unit = add_unit(inv.laptop, None, ACTOR, ids, name=name)
    db.session.flush()
    return unit


def _prep(unit, event, status, check_status=None, at=None, finished=None):
    check_id = None
    if check_status:
        check = InvCheck(unit_id=unit.id, event_cycle_id=event.id, station_code="UNIT",
                         status=check_status, finished_at=finished)
        if at:
            check.created_at = at
        db.session.add(check)
        db.session.flush()
        check_id = check.id
    row = InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status=status,
                        check_id=check_id)
    if at:
        row.created_at = at
    db.session.add(row)
    db.session.flush()


def test_latest_check_reads_across_events(inv, smf, west):
    u = _unit(inv, "Cross event")
    _prep(u, smf, "CHECKED", "PASSED")
    _prep(u, west, "FAILED_CHECK", "FAILED")
    assert check_word(latest_check_rows([u.id])[u.id]) == CHECK_BROKEN


def test_an_undone_check_leaves_the_one_before_it(inv, smf, west):
    u = _unit(inv, "Undone")
    _prep(u, smf, "CHECKED", "PASSED")
    _prep(u, west, "FAILED_CHECK", "VOIDED")
    assert check_word(latest_check_rows([u.id])[u.id]) == CHECK_WORKS


def test_a_unit_whose_only_check_was_undone_has_no_latest_row(inv, smf):
    u = _unit(inv, "Undone only")
    _prep(u, smf, "CHECKED", "VOIDED")
    _prep(u, smf, "VOIDED", "VOIDED")
    assert latest_check_rows([u.id]) == {}


def test_a_unit_never_checked_is_absent(inv, smf):
    u = _unit(inv, "Never")
    assert latest_check_rows([u.id]) == {}


def test_check_word_of_nothing_is_none():
    assert check_word(None) is None


def test_checked_and_not_checked_split_on_the_since_date(inv, smf):
    old, new, never = _unit(inv, "Old"), _unit(inv, "New"), _unit(inv, "Never")
    _prep(old, smf, "CHECKED", "PASSED", at=datetime(2026, 11, 1, 15))
    _prep(new, smf, "CHECKED", "PASSED", at=datetime(2026, 11, 20, 15))
    since = since_start(date(2026, 11, 10))
    names = lambda show: {u.name for u in db.session.query(InvUnit).filter(check_filter(show, since))}
    assert names("checked") == {"New"}
    assert names("not_checked") == {"Old", "Never"}


def test_broken_ignores_the_since_date(inv, smf):
    u = _unit(inv, "Broke long ago")
    _prep(u, smf, "FAILED_CHECK", "FAILED", at=datetime(2025, 1, 1, 15))
    since = since_start(date(2026, 11, 10))
    assert u in db.session.query(InvUnit).filter(check_filter("broken", since)).all()


def test_no_number_and_unknown_filters(inv, smf):
    bare, numbered = _unit(inv, "Bare"), _unit(inv, "Numbered", "U-0001")
    since = since_start(date(2026, 11, 10))
    names = lambda show: {u.name for u in db.session.query(InvUnit).filter(check_filter(show, since))}
    assert names("no_number") == {"Bare"}
    assert names("all") == {"Bare", "Numbered"}


def test_retired_lost_and_rented_units_are_not_counted(inv, smf):
    kept = _unit(inv, "Kept")
    for name, change in (("Retired", "RETIRED"), ("Lost", "LOST")):
        _unit(inv, name).condition = change
    _unit(inv, "Rented").ownership = "RENTED"
    db.session.flush()
    totals = check_totals(since_start(date(2026, 11, 10)))
    assert totals["counted"] == 1 and totals["not_checked"] == 1


def test_last_seen_takes_the_newer_of_movement_and_check(inv, smf):
    u = _unit(inv, "Seen")
    _prep(u, smf, "CHECKED", "PASSED", at=datetime(2030, 1, 1, 15), finished=datetime(2030, 1, 1, 15))
    assert last_seen_dates([u.id])[u.id] == datetime(2030, 1, 1, 15)


def test_last_seen_ignores_open_and_voided_checks(inv, smf):
    u = _unit(inv, "Open")
    _prep(u, smf, "CHECKED", "OPEN", finished=None)
    _prep(u, smf, "CHECKED", "VOIDED", finished=datetime(2030, 1, 1, 15))
    assert last_seen_dates([u.id])[u.id] < datetime(2029, 1, 1)


def test_parse_since_falls_back_on_junk_and_future_dates():
    assert parse_since(None) == (default_since(), False)
    assert parse_since("2026-11-01") == (date(2026, 11, 1), False)
    assert parse_since("nope") == (default_since(), True)
    assert parse_since(f"{local_today() + timedelta(days=1):%Y-%m-%d}") == (default_since(), True)


def test_since_start_is_local_midnight_as_utc():
    assert since_start(date(2026, 12, 7)) == datetime(2026, 12, 7, 5, 0)


def test_local_day():
    assert local_day(datetime(2026, 11, 7, 22, 53)) == "Nov 7"


def _selects(fn):
    seen = [0]

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    sa_event.listen(db.engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", count)
    return seen[0]


def test_readers_run_in_a_fixed_number_of_selects(inv, smf):
    since = since_start(date(2026, 11, 10))

    def measure(n):
        units = [_unit(inv, f"Bulk {n} {i}") for i in range(n)]
        for u in units:
            _prep(u, smf, "CHECKED", "PASSED", finished=datetime(2026, 11, 12))
        db.session.commit()
        ids = [u.id for u in units]
        db.session.expire_all()
        return tuple(_selects(f) for f in (
            lambda: latest_check_rows(ids), lambda: last_seen_dates(ids),
            lambda: check_totals(since)))

    assert measure(3) == measure(30)
