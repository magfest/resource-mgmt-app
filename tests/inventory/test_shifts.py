from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.ledger import InventoryError
from app.inventory.shifts import current_shift, end_shift, recent_names, start_shift
from app.models import InvShift
from app.inventory.labels import allocate
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


def test_a_print_device_is_sent_to_its_station_and_told_why_in_json(client, inv):
    enrolled_client(client, station_code="PRINT")
    page = client.get("/inv/scan/who")
    assert page.status_code == 302 and page.headers["Location"].endswith("/inv/scan/print/station")
    answer = client.post("/inv/scan/api/resolve", json={"code": "x"})
    assert answer.status_code == 403
    assert answer.get_json()["message"] == "This computer only prints labels."
    assert "revoked" not in answer.get_data(as_text=True)


def test_shift_api_refuses_non_json_and_signed_out(client, inv):
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).status_code == 403
    enrolled_client(client)
    assert client.post("/inv/scan/api/shift", data={"person_name": "Sam"}).status_code == 415


def test_an_operator_has_no_shift_to_start(client, inv):
    login(client, "test:operator")
    assert client.get("/inv/scan/who").status_code == 200
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).get_json()["ok"] is False


@pytest.mark.parametrize("name", ["\x01", "\u200b", "\x00", "\ud800", "  ", "Sam\x07"])
def test_a_name_must_be_visible_text(client, inv, name):
    device = enrolled_client(client)
    with pytest.raises(InventoryError):
        start_shift(device, name, now=T0)


def test_an_accented_name_is_accepted(client, inv):
    device = enrolled_client(client)
    assert start_shift(device, "  Zo\u00eb  O\u2019Neil ", now=T0).person_name == "Zo\u00eb O\u2019Neil"


def test_recent_names_count_case_variants_once_under_the_newest_spelling(client, inv):
    device = enrolled_client(client)
    start_shift(device, "sam", now=T0 - timedelta(days=2))
    start_shift(device, "Alex", now=T0 - timedelta(days=1))
    start_shift(device, "SAM", now=T0)
    assert recent_names(device, now=T0) == ["SAM", "Alex"]


def test_starting_or_ending_a_shift_closes_every_open_one(client, inv):
    device = enrolled_client(client)
    for name in ("A", "B"):
        db.session.add(InvShift(device_id=device.id, person_name=name,
                                started_at=T0, last_activity_at=T0))
    db.session.flush()
    start_shift(device, "C", now=T0)
    assert InvShift.query.filter_by(ended_at=None).count() == 1
    end_shift(device, "ENDED", now=T0)
    assert InvShift.query.filter_by(ended_at=None).count() == 0


def _idle_shift(device):
    shift = start_shift(device, "Sam", now=datetime.utcnow() - timedelta(minutes=30))
    db.session.commit()
    return shift.id


def test_the_who_page_persists_an_idle_close(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift_id = _idle_shift(device)
    assert client.get("/inv/scan/who").status_code == 200
    db.session.rollback()
    assert db.session.get(InvShift, shift_id).end_reason == "IDLE"


def test_the_look_up_page_persists_an_idle_close(client, inv):
    device = enrolled_client(client, station_code="LOOKUP")
    shift_id = _idle_shift(device)
    assert client.get("/inv/scan/").status_code == 200
    db.session.rollback()
    assert db.session.get(InvShift, shift_id).end_reason == "IDLE"


def test_an_idle_shift_answers_a_write_with_need_name_and_keeps_the_close(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift_id = _idle_shift(device)
    r = client.post("/inv/scan/api/container", json={
        "client_request_id": "n-1", "label_code": allocate("C", 1)[0],
        "kind_id": inv.kinds["BIN"].id}).get_json()
    assert r == {"ok": False, "message": "Pick your name first.", "need_name": True}
    db.session.rollback()
    assert db.session.get(InvShift, shift_id).end_reason == "IDLE"


def test_an_ok_answer_touches_the_shift_and_a_read_with_no_shift_still_works(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift = start_shift(device, "Sam", now=datetime.utcnow() - timedelta(minutes=10))
    db.session.commit()
    before = shift.last_activity_at
    assert client.post("/inv/scan/api/resolve", json={"code": "zzz"}).get_json()["ok"]
    db.session.rollback()
    assert db.session.get(InvShift, shift.id).last_activity_at > before
    client.post("/inv/scan/api/shift/end", json={})
    assert client.post("/inv/scan/api/resolve", json={"code": "zzz"}).get_json()["ok"]
    assert InvShift.query.filter_by(ended_at=None).count() == 0


def test_a_refusal_does_not_touch_the_shift(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift = start_shift(device, "Sam", now=datetime.utcnow() - timedelta(minutes=10))
    db.session.commit()
    before = shift.last_activity_at
    r = client.post("/inv/scan/api/container", json={"client_request_id": "t-1",
                                                     "label_code": "C-0099", "kind_id": 1})
    assert r.get_json()["ok"] is False
    db.session.rollback()
    assert db.session.get(InvShift, shift.id).last_activity_at == before
