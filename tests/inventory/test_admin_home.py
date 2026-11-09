"""The inventory home page: tiles counted from a chosen date, and grouped links."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory.ledger import Actor, add_unit
from app.inventory.printing import queue_summary
from app.models import EventCycle, InvCheck, InvPrepStatus, InvPrintJob
from app.models.constants import INV_JOB_QUEUED
from tests.inventory.helpers import login
from tests.inventory.test_admin_printers import _printer, _station

ACTOR = Actor(user_id="test:invadmin")


def _unit(inv, name, number=None):
    ids = [("FESTOPS_LABEL", number)] if number else []
    unit = add_unit(inv.laptop, None, ACTOR, ids, name=name)
    db.session.flush()
    return unit


def _prep(unit, event, status, check_status):
    check = InvCheck(unit_id=unit.id, event_cycle_id=event.id, station_code="UNIT",
                     status=check_status)
    db.session.add(check)
    db.session.flush()
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status=status,
                                 check_id=check.id))


def _day_ago(n):
    return datetime.utcnow() - timedelta(days=n)


@pytest.fixture
def week(inv):
    """Two units checked under a non-default event, one 20 days ago and one 2 days ago."""
    event = EventCycle(code="OLD2026", name="Old 2026", is_active=True, is_default=False)
    db.session.add(event)
    db.session.flush()
    old = _unit(inv, "Old check", "U-0001")
    recent = _unit(inv, "Recent check", "U-0002")
    _prep(old, event, "CHECKED", "PASSED")
    _prep(recent, event, "CHECKED", "PASSED")
    db.session.query(InvPrepStatus).filter_by(unit_id=old.id).update({"created_at": _day_ago(20)})
    db.session.query(InvPrepStatus).filter_by(unit_id=recent.id).update({"created_at": _day_ago(2)})
    db.session.commit()
    return event


def _queue(printer, n=1):
    for i in range(n):
        db.session.add(InvPrintJob(printer_id=printer.id, status=INV_JOB_QUEUED,
                                   source="SCAN",
                                   job_type="UNIT", code=f"U-{i}", fields={}))
    db.session.flush()


def _chosen(days):
    from app.inventory.printing import local_today
    return local_today() - timedelta(days=days)


def test_home_counts_checks_since_the_chosen_date(client, week):
    from app.inventory.last_check import since_start
    from app.inventory.printing import local_day
    day = _chosen(10)
    login(client, "test:invadmin")
    page = client.get("/inv/", query_string={"since": day.isoformat()}).data.decode()
    assert f"Checked since {local_day(since_start(day))}" in page
    assert "1 of 2" in page
    assert f'value="{day.isoformat()}"' in page
    for word in ("This week", "Seen", "Not seen", "No default event is set."):
        assert word not in page
    assert "That date could not be used" not in page


def test_home_tiles_link_to_the_list_with_the_date(client, week):
    day = _chosen(10)
    login(client, "test:invadmin")
    page = client.get("/inv/", query_string={"since": day.isoformat()}).data.decode()
    for show in ("checked", "not_checked", "broken", "no_number"):
        assert f'href="/inv/admin/units?show={show}&amp;since={day.isoformat()}"' in page
    assert "0 waiting" in page
    assert "Print station offline" not in page


def test_home_without_since_shows_the_default_day_and_no_fallback(client, week):
    from app.inventory.last_check import default_since
    login(client, "test:invadmin")
    page = client.get("/inv/").data.decode()
    assert f'value="{default_since().isoformat()}"' in page
    assert "That date could not be used" not in page
    assert "Day to day" in page and "<h1>Inventory</h1>" in page


def test_home_falls_back_on_a_bad_date(client, week):
    login(client, "test:invadmin")
    page = client.get("/inv/?since=nope").data.decode()
    assert "That date could not be used, so this shows the last 30 days." in page


def test_queue_summary_counts_waiting_and_flags_a_stale_station(app, inv):
    seen = _station("Seen PC")
    seen.last_seen_at = datetime.utcnow()
    stale = _station("Stale PC")
    stale.last_seen_at = datetime.utcnow() - timedelta(minutes=5)
    a = _printer("A", seen)
    b = _printer("B", stale)
    _queue(a, 2)
    _queue(b, 1)
    assert queue_summary() == {"waiting": 3, "station_online": False}
    b.is_active = False
    db.session.flush()
    assert queue_summary() == {"waiting": 2, "station_online": True}


def test_queue_summary_with_no_printers_is_quiet(app, inv):
    assert queue_summary() == {"waiting": 0, "station_online": True}


def test_home_warns_when_a_station_is_offline(client, week):
    stale = _station("Stale PC")
    _queue(_printer("B", stale), 2)
    db.session.commit()
    login(client, "test:invadmin")
    page = client.get("/inv/").data.decode()
    assert "2 waiting" in page
    assert '<span class="pill pill-warning">Print station offline</span>' in page


def test_groups_are_headed_and_admin_sees_set_up_links(client, week):
    login(client, "test:invadmin")
    page = client.get("/inv/").data.decode()
    for heading in ("Day to day", "Set up", "History"):
        assert heading in page
    assert "Scanners and print stations" in page and "Import old tech lists" in page


def test_operator_does_not_see_admin_links(client, week):
    login(client, "test:operator")
    page = client.get("/inv/").data.decode()
    assert "Day to day" in page
    for href in ("/inv/admin/items", "/inv/admin/devices", "/inv/admin/printers",
                 "/inv/admin/operators", "/inv/admin/movements"):
        assert f'href="{href}"' not in page
    assert "Import old tech lists" not in page
