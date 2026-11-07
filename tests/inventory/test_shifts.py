from datetime import datetime, timedelta

from app import db
from app.inventory.devices import enroll_device
from app.inventory.shifts import current_shift, recent_names, start_shift
from app.models import InvShift
from tests.inventory.helpers import enrolled_client, login

T0 = datetime(2026, 10, 20, 12, 0)
JSON = {"content_type": "application/json"}


def test_idle_shift_closes_and_the_station_asks_who(client, inv):
    device = enrolled_client(client)
    shift = start_shift(device, "Sam", now=T0)
    assert current_shift(device, now=T0 + timedelta(minutes=16)) is None
    assert db.session.get(InvShift, shift.id).end_reason == "IDLE"


def test_a_new_shift_replaces_the_open_one(client, inv):
    device = enrolled_client(client)
    first = start_shift(device, "Sam", now=T0)
    start_shift(device, "Alex", now=T0 + timedelta(minutes=1))
    assert db.session.get(InvShift, first.id).end_reason == "REPLACED"


def test_recent_names_are_this_devices_last_week(client, inv):
    device = enrolled_client(client)
    other, _ = enroll_device("Scanner 2", "test:invadmin")
    start_shift(device, "Old", now=T0 - timedelta(days=8))
    start_shift(device, "Alex", now=T0 - timedelta(days=1))
    start_shift(other, "Zed", now=T0)
    start_shift(device, "Sam", now=T0)
    assert recent_names(device, now=T0) == ["Sam", "Alex"]


def test_writes_need_a_name_but_the_who_page_never_errors(client, inv):
    enrolled_client(client)
    r = client.post("/inv/scan/api/shift", json={"person_name": "  "})
    assert r.get_json()["ok"] is False
    assert client.get("/inv/scan/who").status_code == 200


def test_picking_a_name_starts_a_shift_and_ending_it_asks_again(client, inv):
    enrolled_client(client, station_code="COUNT")
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/who")
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).get_json()["ok"]
    assert client.get("/inv/scan/").status_code == 200
    assert client.post("/inv/scan/api/shift/end", json={}).get_json()["ok"]
    assert client.get("/inv/scan/").status_code == 302


def test_look_up_needs_no_name(client, inv):
    enrolled_client(client, station_code="LOOKUP")
    assert client.get("/inv/scan/").status_code == 200


def test_a_print_device_is_refused_by_the_scan_guard(client, inv):
    enrolled_client(client, station_code="PRINT")
    r = client.post("/inv/scan/api/shift", json={"person_name": "Sam"})
    assert r.status_code == 403
    assert r.get_json()["ok"] is False
    assert client.get("/inv/scan/who").status_code == 403


def test_shift_api_refuses_non_json_and_signed_out(client, inv):
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).status_code == 403
    enrolled_client(client)
    assert client.post("/inv/scan/api/shift", data={"person_name": "Sam"}).status_code == 415


def test_an_operator_has_no_shift_to_start(client, inv):
    login(client, "test:operator")
    assert client.get("/inv/scan/who").status_code == 200
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).get_json()["ok"] is False
