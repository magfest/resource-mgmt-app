from datetime import datetime

import pytest

from app import db
from app.inventory.devices import COOKIE_NAME, enroll_device, revoke_device, set_station
from app.inventory.ledger import InventoryError
from app.models import InvDevice, InvShift
from tests.inventory.helpers import enrolled_client, login, make_container


def test_enrolling_sets_the_cookie_and_signs_the_admin_out(client, inv):
    login(client, "test:invadmin")
    response = client.post("/inv/scan/enroll", data={"name": "Scanner 3"})
    assert response.status_code == 302
    assert client.get_cookie(COOKIE_NAME, path="/inv/scan/") is not None
    with client.session_transaction() as sess:
        assert "active_user_id" not in sess
    assert client.get("/inv/scan/").status_code == 200


def test_device_cookie_is_refused_outside_the_scanner(client, inv):
    _, raw = enroll_device("Scanner 1", "test:invadmin")
    db.session.commit()
    client.set_cookie(COOKIE_NAME, raw)
    for path in ("/inv/admin/containers", "/inv/", "/admin/config/users/"):
        assert client.get(path).status_code != 200, path


def test_device_cookie_reaches_nothing_outside_the_scanner(client, inv):
    device = enrolled_client(client)
    assert client.get("/inv/scan/").status_code == 200
    # Sent from the site root, as a misconfigured path would send it.
    _, raw = enroll_device("Scanner 9", "test:invadmin")
    db.session.commit()
    client.set_cookie(COOKIE_NAME, raw, path="/")
    assert client.get("/inv/admin/kinds").status_code != 200
    assert device.station_code == "LOOKUP"


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
    login(client, "test:invadmin")
    client.post("/inv/scan/enroll", data={"name": "Scanner 3", "station_code": "TV_CHECK"})
    device = InvDevice.query.one()
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
    login(client, "test:invadmin")
    client.post("/inv/scan/enroll", data={"name": "S5", "station_code": "COUNT",
                                          "home_container_code": "bin-0001"})
    assert InvDevice.query.one().home_container_id == bin_.id
