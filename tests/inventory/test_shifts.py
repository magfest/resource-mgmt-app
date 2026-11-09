from datetime import datetime, timedelta

import pytest
from flask import g

from app import db
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor
from app.inventory.people import actor_name, names_for
from app.inventory.shifts import current_shift, end_shift, end_user_shifts, start_shift
from app.inventory.sign_in import new_card, new_pin
from app.models import InvDevice, InvShift, User, UserRole
from app.inventory.labels import allocate
from app.routes.inventory.scan_auth import ScanPrincipal, scan_actor
from tests.inventory.helpers import enrolled_client, login

T0 = datetime(2026, 10, 20, 12, 0)
JSON = {"content_type": "application/json"}
SIGN_IN = "/inv/scan/api/sign-in"


@pytest.fixture
def two_devices(inv):
    return [enroll_device(name, "test:invadmin")[0] for name in ("Scanner 1", "Scanner 2")]


def test_idle_shift_closes_and_the_station_asks_who(client, inv):
    device = enrolled_client(client)
    shift = start_shift(device, "test:operator", now=T0)
    assert current_shift(device, now=T0 + timedelta(minutes=16)) is None
    assert db.session.get(InvShift, shift.id).end_reason == "IDLE"


def test_a_new_shift_replaces_the_open_one(client, inv):
    device = enrolled_client(client)
    first = start_shift(device, "test:operator", now=T0)
    start_shift(device, "test:invadmin", now=T0 + timedelta(minutes=1))
    assert db.session.get(InvShift, first.id).end_reason == "REPLACED"


def test_signing_in_starts_a_shift_and_ending_it_asks_again(client, inv):
    pin = new_pin(db.session.get(User, "test:operator")); db.session.commit()
    enrolled_client(client, station_code="COUNT")
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/who")
    assert client.post(SIGN_IN, json={"pin": pin}).get_json()["ok"]
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/count")
    assert client.post("/inv/scan/api/shift/end", json={}).get_json()["ok"]
    assert client.get("/inv/scan/").status_code == 302


def test_the_who_page_never_errors(client, inv):
    enrolled_client(client)
    assert client.get("/inv/scan/who").status_code == 200


def test_look_up_needs_no_name(client, inv):
    enrolled_client(client, station_code="LOOKUP")
    assert client.get("/inv/scan/").status_code == 200


def test_a_print_device_is_refused_by_the_scan_guard(client, inv):
    enrolled_client(client, station_code="PRINT")
    r = client.post(SIGN_IN, json={"pin": "123456"})
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


def test_sign_in_api_refuses_non_json_and_signed_out(client, inv):
    assert client.post(SIGN_IN, json={"pin": "123456"}).status_code == 403
    enrolled_client(client)
    assert client.post(SIGN_IN, data={"pin": "123456"}).status_code == 415


def test_an_operator_has_no_shift_to_start(client, inv):
    login(client, "test:operator")
    assert client.get("/inv/scan/who").status_code == 200
    r = client.post(SIGN_IN, json={"pin": "123456"}).get_json()
    assert r == {"ok": False, "message": "You are signed in to RMS already."}


def test_starting_or_ending_a_shift_closes_every_open_one(client, inv):
    device = enrolled_client(client)
    for name in ("A", "B"):
        db.session.add(InvShift(device_id=device.id, person_name=name,
                                started_at=T0, last_activity_at=T0))
    db.session.flush()
    start_shift(device, "test:operator", now=T0)
    assert InvShift.query.filter_by(ended_at=None).count() == 1
    end_shift(device, "ENDED", now=T0)
    assert InvShift.query.filter_by(ended_at=None).count() == 0


def _idle_shift(device):
    shift = start_shift(device, "test:operator", now=datetime.utcnow() - timedelta(minutes=30))
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
    assert r == {"ok": False, "message": "Sign in first.", "need_name": True}
    db.session.rollback()
    assert db.session.get(InvShift, shift_id).end_reason == "IDLE"


def test_an_ok_answer_touches_the_shift_and_a_read_with_no_shift_still_works(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift = start_shift(device, "test:operator", now=datetime.utcnow() - timedelta(minutes=10))
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
    shift = start_shift(device, "test:operator", now=datetime.utcnow() - timedelta(minutes=10))
    db.session.commit()
    before = shift.last_activity_at
    r = client.post("/inv/scan/api/container", json={"client_request_id": "t-1",
                                                     "label_code": "C-0099", "kind_id": 1})
    assert r.get_json()["ok"] is False
    db.session.rollback()
    assert db.session.get(InvShift, shift.id).last_activity_at == before


def test_scan_actor_carries_the_user(app, client, inv):
    device = enrolled_client(client, station_code="COUNT")
    shift = start_shift(device, "test:operator"); db.session.commit()
    with app.test_request_context():
        g.scan_principal = ScanPrincipal(None, device)
        assert scan_actor() == Actor(user_id="test:operator", shift_id=shift.id,
                                     device_id=device.id)


def test_role_removed_ends_the_shift_on_next_request(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:operator"); db.session.commit()
    db.session.query(UserRole).filter_by(user_id="test:operator").delete(); db.session.commit()
    assert current_shift(device) is None
    assert db.session.query(InvShift).one().end_reason == "REVOKED"


def test_a_typed_name_shift_has_no_eligibility_check(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    now = datetime.utcnow()
    shift = InvShift(device_id=device.id, person_name="Invented Person",
                     started_at=now, last_activity_at=now)
    db.session.add(shift); db.session.commit()
    assert current_shift(device).id == shift.id


def test_end_user_shifts_closes_every_device(two_devices):
    for d in two_devices:
        start_shift(d, "test:operator")
    start_shift(enroll_device("Scanner 3", "test:invadmin")[0], "test:invadmin")
    assert end_user_shifts("test:operator") == 2
    assert {s.end_reason for s in InvShift.query.filter_by(user_id="test:operator")} == {"REVOKED"}
    assert InvShift.query.filter_by(ended_at=None).one().user_id == "test:invadmin"


def test_one_person_on_two_scanners(two_devices):
    a, b = (start_shift(d, "test:operator") for d in two_devices)
    assert a.ended_at is None and b.ended_at is None


def test_sign_in_by_pin(client, inv):
    pin = new_pin(db.session.get(User, "test:operator")); db.session.commit()
    enrolled_client(client, station_code="COUNT")
    r = client.post(SIGN_IN, json={"pin": pin}).get_json()
    assert r["ok"] and r["name"] == "Operator"


def test_card_mid_shift_asks_before_switching(client, inv):
    new_pin(db.session.get(User, "test:invadmin"))
    card = new_card(db.session.get(User, "test:operator")); db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:invadmin"); db.session.commit()
    r = client.post(SIGN_IN, json={"card": card}).get_json()
    assert r == {"ok": False, "message": "This is Operator's card.", "switch_to": "Operator",
                 "switch_first": "Operator", "current_first": "Invadmin"}
    assert current_shift(device).user_id == "test:invadmin"
    r = client.post(SIGN_IN, json={"card": card, "switch": True}).get_json()
    assert r["ok"] and current_shift(device).user_id == "test:operator"


def test_card_after_idle_signs_in_without_asking(client, inv):
    card = new_card(db.session.get(User, "test:operator")); db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    start_shift(device, "test:invadmin", now=datetime.utcnow() - timedelta(minutes=16))
    db.session.commit()
    assert client.post(SIGN_IN, json={"card": card}).get_json()["ok"]


def test_locked_keypad_still_takes_a_card(client, inv):
    card = new_card(db.session.get(User, "test:operator")); db.session.commit()
    enrolled_client(client, station_code="COUNT")
    for _ in range(5):
        client.post(SIGN_IN, json={"pin": "999999"})
    r = client.post(SIGN_IN, json={"pin": "999999"}).get_json()
    assert r == {"ok": False, "locked": True,
                 "message": "Too many wrong PINs. Scan your card, or wait 5 minutes."}
    assert client.post(SIGN_IN, json={"card": card}).get_json()["ok"]


def test_a_wrong_pin_is_counted_and_a_right_one_clears_the_count(client, inv):
    pin = new_pin(db.session.get(User, "test:operator")); db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    r = client.post(SIGN_IN, json={"pin": "999999"}).get_json()
    assert r == {"ok": False, "message": "That PIN did not match. Try again."}
    db.session.expire_all()
    assert db.session.get(InvDevice, device.id).pin_failures == 1
    assert client.post(SIGN_IN, json={"pin": pin}).get_json()["ok"]
    db.session.expire_all()
    assert db.session.get(InvDevice, device.id).pin_failures == 0


@pytest.mark.parametrize("body, message", [
    ({}, "Scan your card or type your PIN."),
    ({"pin": "123456", "card": "OP-0000000000AA"}, "Scan your card or type your PIN."),
    ({"card": "OP-0000000000AA"}, "This card no longer works. Ask a lead for a new one."),
])
def test_sign_in_refusals(client, inv, body, message):
    enrolled_client(client, station_code="COUNT")
    assert client.post(SIGN_IN, json=body).get_json() == {"ok": False, "message": message}
    assert InvShift.query.count() == 0


def test_the_same_person_signing_in_again_keeps_the_shift(client, inv):
    card = new_card(db.session.get(User, "test:operator")); db.session.commit()
    device = enrolled_client(client, station_code="COUNT")
    shift = start_shift(device, "test:operator"); db.session.commit()
    r = client.post(SIGN_IN, json={"card": card}).get_json()
    assert r == {"ok": True, "already": True, "name": "Operator"}
    assert current_shift(device).id == shift.id


def test_typed_name_endpoint_is_gone(client, inv):
    enrolled_client(client, station_code="COUNT")
    assert client.post("/inv/scan/api/shift", json={"person_name": "Sam"}).status_code == 404


def test_card_for_an_inactive_user_is_refused(client, inv):
    user = db.session.get(User, "test:operator")
    card = new_card(user)
    user.is_active = False
    db.session.commit()
    enrolled_client(client, station_code="COUNT")
    r = client.post(SIGN_IN, json={"card": card}).get_json()
    assert r == {"ok": False, "message": "This account cannot use scanners. Ask a lead."}
    assert InvShift.query.count() == 0


def test_history_names_old_typed_shifts_as_before(two_devices):
    shift = InvShift(device_id=two_devices[0].id, person_name="Invented Person",
                     started_at=T0, last_activity_at=T0)
    db.session.add(shift)
    db.session.flush()
    assert actor_name(None, shift, {}) == "Invented Person (scanner)"
    assert names_for([f"shift:{shift.id}"]) == {f"shift:{shift.id}": "Invented Person (scanner)"}
