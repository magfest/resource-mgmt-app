"""The inventory home page: this week's tiles and grouped links."""
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


@pytest.fixture
def week(inv):
    event = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(event)
    db.session.flush()
    works = _unit(inv, "Works", "U-0001")
    broken = _unit(inv, "Broken", "U-0002")
    _unit(inv, "Unseen", "U-0003")
    _unit(inv, "Unnumbered")
    _prep(works, event, "CHECKED", "PASSED")
    _prep(broken, event, "FAILED_CHECK", "FAILED")
    db.session.commit()
    return event


def _queue(printer, n=1):
    for i in range(n):
        db.session.add(InvPrintJob(printer_id=printer.id, status=INV_JOB_QUEUED,
                                   source="SCAN",
                                   job_type="UNIT", code=f"U-{i}", fields={}))
    db.session.flush()


def test_home_shows_tiles_and_hrefs(client, week):
    login(client, "test:invadmin")
    page = client.get("/inv/").data.decode()
    assert "2 of 4" in page
    for show in ("seen", "not_seen", "broken", "no_number"):
        assert f'href="/inv/admin/units?show={show}"' in page
    assert "0 waiting" in page
    assert "Print station offline" not in page


def test_home_without_default_event_says_so(client, inv):
    login(client, "test:invadmin")
    page = client.get("/inv/").data.decode()
    assert "No default event is set." in page
    assert "units?show=" not in page
    assert "Open flags" in page and "Print queue" in page


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
    for heading in ("During the week", "Set up", "History"):
        assert heading in page
    assert "Scanners and print stations" in page and "Import old tech lists" in page


def test_operator_does_not_see_admin_links(client, week):
    login(client, "test:operator")
    page = client.get("/inv/").data.decode()
    assert "During the week" in page
    for href in ("/inv/admin/items", "/inv/admin/devices", "/inv/admin/printers",
                 "/inv/admin/operators", "/inv/admin/movements"):
        assert f'href="{href}"' not in page
    assert "Import old tech lists" not in page
