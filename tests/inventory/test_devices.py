import re
from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory.devices import (
    COOKIE_NAME, ENROLL_CODE_LIFETIME, UNIT_NEEDS_HOME, create_enroll_code, enroll_device,
    redeem_enroll_code, revoke_device, set_station,
)
from app.inventory.ledger import InventoryError
from app.models.constants import INV_STATION_UNIT
from app.inventory.printing import local_stamp
from app.inventory.shifts import start_shift
from app.models import InvDevice, InvEnrollCode, InvPrinter, InvPrintJob, InvShift
from app.models.constants import INV_STATION_PRINT
from app.routes.inventory.admin_devices import device_rows
from sqlalchemy import event
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
    device = enrolled_client(client, name="Scanner 3", station_code="RECEIVE")
    assert device.station_code == "RECEIVE"
    login(client, "test:invadmin")
    client.post(f"/inv/admin/devices/{device.id}/station", data={"station_code": "COUNT"})
    assert db.session.get(InvDevice, device.id).station_code == "COUNT"


def test_removed_stations_cannot_be_assigned(inv):
    home = make_container(inv, "LOC-0002", kind="LOCATION")
    device, _ = enroll_device("CT60 old", "test:invadmin", station_code=INV_STATION_UNIT,
                              home_container=home)
    for code in ("TAG", "TV_CHECK", "PRINTER_CHECK"):
        with pytest.raises(InventoryError):
            set_station(device, code, keep_home=True)
        with pytest.raises(InventoryError):
            create_enroll_code("CT60 new", "test:invadmin", station_code=code)
    assert device.station_code == "UNIT"


def test_removed_station_names_still_render_for_history():
    from app.models.constants import INV_STATION_NAMES
    assert INV_STATION_NAMES["TV_CHECK"] == "TV check"
    assert INV_STATION_NAMES["PRINTER_CHECK"] == "Printer check"
    assert INV_STATION_NAMES["TAG"] == "Tag"


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


def test_unit_station_needs_a_home_container(inv):
    with pytest.raises(InventoryError, match="A Unit station needs a home container"):
        enroll_device("CT60 one", "test:invadmin", station_code=INV_STATION_UNIT)
    with pytest.raises(InventoryError, match=UNIT_NEEDS_HOME):
        create_enroll_code("CT60 one", "test:invadmin", station_code=INV_STATION_UNIT)


def test_unit_station_enrolls_with_a_home(inv):
    home = make_container(inv, "LOC-0001", kind="LOCATION")
    device, _ = enroll_device("CT60 one", "test:invadmin", station_code=INV_STATION_UNIT,
                              home_container=home)
    assert device.station_code == "UNIT"
    create_enroll_code("CT60 two", "test:invadmin", station_code=INV_STATION_UNIT,
                       home_container=home)


def test_set_station_to_unit_keeps_an_existing_home(inv):
    home = make_container(inv, "LOC-0001", kind="LOCATION")
    device, _ = enroll_device("CT60 one", "test:invadmin", home_container=home)
    set_station(device, INV_STATION_UNIT, keep_home=True)
    assert device.station_code == "UNIT"


def test_set_station_to_unit_without_a_home_is_refused(inv):
    home = make_container(inv, "LOC-0001", kind="LOCATION")
    device, _ = enroll_device("CT60 one", "test:invadmin", home_container=home)
    with pytest.raises(InventoryError, match=UNIT_NEEDS_HOME):
        set_station(device, INV_STATION_UNIT)
    bare, _ = enroll_device("CT60 two", "test:invadmin")
    with pytest.raises(InventoryError, match=UNIT_NEEDS_HOME):
        set_station(bare, INV_STATION_UNIT, keep_home=True)
    assert bare.station_code == "LOOKUP"


NOW = datetime(2026, 12, 7, 15, 0)


def _row(device, now=NOW):
    return next(r for r in device_rows(now) if r["device"].id == device.id)


@pytest.fixture
def scanner(inv):
    device, _ = enroll_device("Scanner A", "test:invadmin")
    device.last_seen_at = NOW - timedelta(seconds=10)
    db.session.commit()
    return device


def _station(name="Print PC"):
    device, _ = enroll_device(name, "test:invadmin", station_code=INV_STATION_PRINT)
    return device


def _printer(device, name="Zebra 1"):
    printer = InvPrinter(name=name, dpi=203, width_in=2, height_in=1,
                         device_id=device.id, device_name=name)
    db.session.add(printer)
    db.session.flush()
    return printer


def test_row_shows_who_is_signed_in(scanner):
    start_shift(scanner, "Robin Test", now=NOW)
    row = _row(scanner)
    assert row["person"] == "Robin Test" and row["stale"] is False


def test_nobody_signed_in_and_idle_shift_is_not_open(scanner):
    assert _row(scanner)["person"] is None
    start_shift(scanner, "Robin Test", now=NOW - timedelta(minutes=30))
    assert _row(scanner)["person"] is None


def test_unseen_device_is_stale(scanner):
    scanner.last_seen_at = NOW - timedelta(minutes=20)
    assert _row(scanner)["stale"] is True


def test_never_seen_is_stale(scanner):
    scanner.last_seen_at = None
    row = _row(scanner)
    assert row["stale"] is True and row["seen_text"] == "never"


def test_seen_text_forms(scanner):
    assert _row(scanner)["seen_text"] == "just now"
    scanner.last_seen_at = NOW - timedelta(minutes=5)
    assert _row(scanner)["seen_text"] == "5 min ago"
    scanner.last_seen_at = NOW - timedelta(hours=2)
    assert _row(scanner)["seen_text"] == local_stamp(NOW - timedelta(hours=2))


def test_since_time_is_local(scanner):
    now = datetime(2026, 12, 7, 15, 10)
    start_shift(scanner, "Robin Test", now=datetime(2026, 12, 7, 15, 5))
    scanner.last_seen_at = now
    assert _row(scanner, now)["since"] == "10:05"


def test_page_shows_who_and_since(app, scanner):
    start_shift(scanner, "Robin Test")
    scanner.last_seen_at = datetime.utcnow()
    db.session.commit()
    admin = app.test_client()
    login(admin, "test:invadmin")
    page = admin.get("/inv/admin/devices").get_data(as_text=True)
    assert "Robin Test, since " in page and "just now" in page


def test_print_station_lists_its_printer(inv):
    station = _station()
    station.last_seen_at = NOW - timedelta(minutes=2)
    _printer(station)
    db.session.commit()
    row = _row(station)
    assert row["person"] == "Print station"
    assert row["printers"] == [{"name": "Zebra 1", "online": False, "waiting": 0}]


def test_print_station_polling_is_online_with_waiting_count(app, inv):
    station = _station()
    station.last_seen_at = NOW - timedelta(seconds=5)
    printer = _printer(station)
    for n in range(2):
        db.session.add(InvPrintJob(printer_id=printer.id, job_type="CONTAINER",
                                   code=f"C{n}", fields={}, status="QUEUED", source="SCAN"))
    db.session.commit()
    assert _row(station)["printers"] == [{"name": "Zebra 1", "online": True, "waiting": 2}]
    station.last_seen_at = None
    db.session.commit()
    admin = app.test_client()
    login(admin, "test:invadmin")
    page = admin.get("/inv/admin/devices").get_data(as_text=True)
    assert "Print station" in page and "Zebra 1: Offline, 2 waiting" in page


def test_page_is_renamed_and_folds_revoked(app, scanner):
    revoke_device(scanner, "test:invadmin")
    db.session.commit()
    admin = app.test_client()
    login(admin, "test:invadmin")
    page = admin.get("/inv/admin/devices").get_data(as_text=True)
    assert "<h1>Scanners and print stations</h1>" in page
    assert "<summary>Revoked (1)</summary>" in page
    assert "Needed for a Unit station: scan or type the warehouse label." in page


def test_active_row_keeps_its_forms_and_change_is_folded(app, scanner):
    admin = app.test_client()
    login(admin, "test:invadmin")
    page = admin.get("/inv/admin/devices").get_data(as_text=True)
    for action in (f"/inv/admin/devices/{scanner.id}/station",
                   f"/inv/admin/devices/{scanner.id}/revoke", "/inv/admin/devices/codes"):
        assert f'action="{action}"' in page
    assert "<details>\n          <summary>Change</summary>" in page
    assert 'name="station_code"' in page and 'name="home_container_code"' in page
    assert page.count('name="csrf_token"') >= 3


def _count_queries(app, client):
    seen = []

    def count(conn, cursor, statement, *a):
        seen.append(statement)
    engine = db.engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        assert client.get("/inv/admin/devices").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", count)
    return len(seen)


def test_page_queries_do_not_grow_with_devices(app, inv):
    admin = app.test_client()
    login(admin, "test:invadmin")

    def add(n):
        for i in range(n):
            station = _station(f"PC {i}-{db.session.query(InvDevice).count()}")
            station.last_seen_at = datetime.utcnow()
            _printer(station, f"Zebra {i}-{station.id}")
            scanner, _ = enroll_device(f"S {station.id}", "test:invadmin")
            start_shift(scanner, "Robin Test")
        db.session.commit()

    add(1)
    login(admin, "test:invadmin")
    few = _count_queries(app, admin)
    add(4)
    login(admin, "test:invadmin")
    assert _count_queries(app, admin) == few
