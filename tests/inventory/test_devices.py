import re
from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory.devices import (
    COOKIE_NAME, ENROLL_CODE_LIFETIME, create_enroll_code, enroll_device,
    redeem_enroll_code, revoke_device, set_station,
)
from app.inventory.ledger import InventoryError
from app.models import InvDevice, InvEnrollCode, InvShift
from tests.inventory.helpers import enrolled_client, login, make_container


REFUSED = "That code did not work. Ask an inventory admin for a new one."


def _code_from(response):
    match = re.search(r"\b([2-9A-Z]{4}-[2-9A-Z]{4})\b", response.get_data(as_text=True))
    assert match, "no code on the page"
    return match.group(1)


def test_admin_creates_a_code_and_the_device_redeems_it(app, inv):
    bin_ = make_container(inv, "BIN-0001", kind="BIN")
    db.session.commit()
    admin = app.test_client()
    login(admin, "test:invadmin")
    response = admin.post("/inv/admin/devices/codes", data={
        "name": "Scanner 3", "station_code": "COUNT", "home_container_code": "bin-0001"})
    assert response.status_code == 200
    assert "Type this on the device within 10 minutes. It works once." in response.get_data(as_text=True)
    code = _code_from(response)
    assert db.session.query(InvDevice).count() == 0

    device_client = app.test_client()
    response = device_client.post("/inv/scan/enroll", data={"code": code})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inv/scan/")
    device = InvDevice.query.one()
    assert (device.name, device.station_code, device.home_container_id) == (
        "Scanner 3", "COUNT", bin_.id)
    assert device_client.get_cookie(COOKIE_NAME, path="/inv/scan/") is not None
    # A COUNT station asks for a name before it opens.
    assert device_client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/who")
    assert device_client.get("/inv/scan/who").status_code == 200
    # The page that showed the code does not show it again.
    assert code not in admin.get("/inv/admin/devices").get_data(as_text=True)


def test_a_code_works_once(client, inv):
    raw = create_enroll_code("Scanner 1", "test:invadmin")
    db.session.commit()
    assert client.post("/inv/scan/enroll", data={"code": raw}).status_code == 302
    second = client.post("/inv/scan/enroll", data={"code": raw})
    assert REFUSED in second.get_data(as_text=True)
    assert InvDevice.query.count() == 1


def test_an_expired_code_is_refused(client, inv):
    now = datetime(2026, 12, 1, 9, 0)
    raw = create_enroll_code("Scanner 1", "test:invadmin", now=now)
    db.session.commit()
    with pytest.raises(InventoryError, match="did not work"):
        redeem_enroll_code(raw, now=now + ENROLL_CODE_LIFETIME + timedelta(seconds=1))
    assert InvDevice.query.count() == 0
    device, _ = redeem_enroll_code(raw, now=now + timedelta(minutes=9))
    assert device.name == "Scanner 1"


def test_a_wrong_code_gets_the_same_message_as_a_used_one(client, inv):
    raw = create_enroll_code("Scanner 1", "test:invadmin")
    db.session.commit()
    client.post("/inv/scan/enroll", data={"code": raw})
    used = client.post("/inv/scan/enroll", data={"code": raw}).get_data(as_text=True)
    wrong = client.post("/inv/scan/enroll", data={"code": "ZZZZ-ZZZZ"}).get_data(as_text=True)
    assert REFUSED in used and REFUSED in wrong
    assert InvDevice.query.count() == 1


def test_only_an_admin_creates_a_code(app, inv):
    operator = app.test_client()
    login(operator, "test:operator")
    response = operator.post("/inv/admin/devices/codes", data={"name": "S", "station_code": "COUNT"})
    assert response.status_code == 403
    assert InvEnrollCode.query.count() == 0


def test_a_timed_out_enroll_page_asks_for_the_code_again(app, client, inv):
    raw = create_enroll_code("Scanner 1", "test:invadmin")
    db.session.commit()
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post("/inv/scan/enroll", data={"code": raw})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 400
    assert "Type the code again" in response.get_data(as_text=True)
    assert InvDevice.query.count() == 0
    assert InvEnrollCode.query.one().used_at is None


def test_a_fresh_enroll_page_redeems_with_its_form_token(app, client, inv):
    raw = create_enroll_code("Scanner 1", "test:invadmin")
    db.session.commit()
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        html = client.get("/inv/scan/enroll").get_data(as_text=True)
        token = html.split('name="csrf_token" value="')[1].split('"')[0]
        response = client.post("/inv/scan/enroll", data={"code": raw, "csrf_token": token})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert response.status_code == 302
    assert InvDevice.query.count() == 1


def test_enrolling_needs_no_login_and_leaves_a_signed_in_session_alone(client, inv):
    raw = create_enroll_code("Scanner 1", "test:invadmin")
    db.session.commit()
    login(client, "test:invadmin")
    assert "code" in client.get("/inv/scan/enroll").get_data(as_text=True).lower()
    assert client.post("/inv/scan/enroll", data={"code": raw}).status_code == 302
    with client.session_transaction() as sess:
        assert sess["active_user_id"] == "test:invadmin"


def test_the_enroll_and_devices_pages_render(app, inv):
    assert app.test_client().get("/inv/scan/enroll").status_code == 200
    admin = app.test_client()
    login(admin, "test:invadmin")
    assert admin.get("/inv/admin/devices").status_code == 200


def test_entry_is_forgiving(client, inv):
    raw = create_enroll_code("A", "test:invadmin")
    other = create_enroll_code("B", "test:invadmin")
    db.session.commit()
    spaced = " ".join([raw.replace("-", "")[:4].lower(), raw.replace("-", "")[4:].lower()])
    assert redeem_enroll_code(spaced)[0].name == "A"
    assert redeem_enroll_code(other)[0].name == "B"
    assert re.fullmatch(r"[2-9A-Z]{4}-[2-9A-Z]{4}", raw)


def test_the_database_holds_only_a_hash_of_the_code(inv):
    raw = create_enroll_code("A", "test:invadmin")
    db.session.commit()
    row = InvEnrollCode.query.one()
    assert len(row.code_hash) == 64
    assert raw.replace("-", "") not in row.code_hash


def test_device_cookie_is_refused_outside_the_scanner(client, inv):
    _, raw = enroll_device("Scanner 1", "test:invadmin")
    db.session.commit()
    client.set_cookie(COOKIE_NAME, raw)
    for path in ("/inv/admin/containers", "/inv/", "/admin/config/users/"):
        assert client.get(path).status_code != 200, path


def test_device_cookie_reaches_nothing_outside_the_scanner(client, inv):
    enrolled_client(client)
    assert client.get("/inv/scan/").status_code == 200
    # Sent from the site root, as a misconfigured path would send it.
    _, raw = enroll_device("Scanner 9", "test:invadmin")
    db.session.commit()
    client.set_cookie(COOKIE_NAME, raw, path="/")
    assert client.get("/inv/admin/kinds").status_code != 200


def test_revoked_device_is_refused(client, inv):
    device, raw = enroll_device("Scanner 2", "test:invadmin")
    revoke_device(device, "test:invadmin")
    db.session.commit()
    client.set_cookie(COOKIE_NAME, raw)
    assert client.get("/inv/scan/").status_code == 403


def test_revoking_closes_the_open_shift(client, inv):
    device = enrolled_client(client, station_code="COUNT")
    assert device.station_code == "COUNT"
    shift = InvShift(device_id=device.id, person_name="Sam")
    db.session.add(shift)
    db.session.commit()
    revoke_device(device, "test:invadmin")
    db.session.commit()
    shift = db.session.get(InvShift, shift.id)
    assert shift.end_reason == "REVOKED"
    assert isinstance(shift.ended_at, datetime)


def test_station_is_set_at_enrollment_and_changed_on_the_devices_page(client, inv):
    device = enrolled_client(client, name="Scanner 3", station_code="TV_CHECK")
    assert device.station_code == "TV_CHECK"
    login(client, "test:invadmin")
    client.post(f"/inv/admin/devices/{device.id}/station", data={"station_code": "COUNT"})
    assert db.session.get(InvDevice, device.id).station_code == "COUNT"


def test_unknown_station_is_refused(client, inv):
    device, _ = enroll_device("Scanner 4", "test:invadmin")
    with pytest.raises(InventoryError):
        set_station(device, "NOPE")


def test_home_container_is_set_from_its_label(client, inv):
    bin_ = make_container(inv, "BIN-0001", kind="BIN")
    db.session.commit()
    raw = create_enroll_code("S5", "test:invadmin", station_code="COUNT", home_container=bin_)
    db.session.commit()
    device, _ = redeem_enroll_code(raw)
    assert device.home_container_id == bin_.id
