"""The laptop units page: search, the unit page, corrections and label printing."""
import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.flags import raise_flag
from app.inventory.ledger import Actor, add_unit, retire_identifier
from app.inventory.resolver import KIND_UNIT, resolve
from app.models import InvPrinter, InvPrintJob, InvPrintRoute, InvUnit, InvUnitIdentifier
from app.models.constants import INV_JOB_OWNED_UNIT, INV_STATION_PRINT
from tests.inventory.helpers import login

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def unit(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("FESTOPS_LABEL", "U-0001"), ("SERIAL", "SN-ABC123"), ("MAC", "aa:bb:cc:dd:ee:ff")])
    db.session.commit()
    return unit


@pytest.fixture
def printer(inv):
    device, _ = enroll_device("Print PC", "test:invadmin", station_code=INV_STATION_PRINT)
    p = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=1,
                   device_id=device.id, device_name="Perm")
    db.session.add(p)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=p.id))
    db.session.commit()


def _search(client, q):
    return client.get("/inv/admin/units", query_string={"q": q}).get_data(as_text=True)


def test_search_by_tag_serial_fragment_and_mac(client, unit):
    login(client, "test:operator")
    for q in ("U-0001", "abc12", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"):
        page = _search(client, q)
        assert f"/inv/admin/units/{unit.id}" in page, q
        assert "Laptop" in page


def test_empty_search_lists_nothing_and_unknown_finds_nothing(client, unit):
    login(client, "test:operator")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "zzzz")


def test_retired_identifier_no_longer_resolves(client, unit):
    ident = next(i for i in unit.identifiers if i.kind == "SERIAL")
    retire_identifier(ident)
    db.session.commit()
    assert resolve("SN-ABC123").kind != KIND_UNIT
    login(client, "test:operator")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "SN-ABC123")


def test_row_shows_open_flag_count_and_unit_page_lists_flags(client, unit):
    raise_flag(ACTOR, "COUNT", "DAMAGED", unit=unit, note="Dented")
    db.session.commit()
    login(client, "test:operator")
    assert "1 open" in _search(client, "U-0001")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Dented" in page and "SN-ABC123" in page


def test_admin_corrects_serial_and_item_then_prints_new_text(client, inv, unit, printer):
    login(client, "test:invadmin")
    old = next(i for i in unit.identifiers if i.kind == "SERIAL")
    base = f"/inv/admin/units/{unit.id}"
    client.post(f"{base}/identifiers/{old.id}/retire")
    client.post(f"{base}/identifiers", data={"kind": "SERIAL", "value": "SN-NEW999"})
    client.post(f"{base}/item", data={"item_id": str(inv.wap.id)})
    resp = client.post(f"{base}/print", data={"mode": "CURRENT_DETAILS"})
    assert resp.status_code == 302
    jobs = db.session.query(InvPrintJob).all()
    assert len(jobs) == 1
    rows = dict(map(tuple, jobs[0].fields["rows"]))
    assert rows["Serial"] == "SN-NEW999" and rows["Type"] == "Wireless Access Point"
    assert jobs[0].unit_id == unit.id


def test_condition_needs_confirm_wording_and_retires_identifiers(client, unit):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "REPAIR"})
    assert db.session.get(InvUnit, unit.id).condition == "REPAIR"
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "RETIRED"})
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).condition == "RETIRED"
    assert all(i.retired_at for i in db.session.get(InvUnit, unit.id).identifiers)
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "BOGUS"})
    assert db.session.get(InvUnit, unit.id).condition == "RETIRED"


def test_operator_prints_but_cannot_correct(client, inv, unit, printer):
    login(client, "test:operator")
    base = f"/inv/admin/units/{unit.id}"
    ident = unit.identifiers[0]
    assert client.get(base).status_code == 200
    assert client.post(f"{base}/print", data={"mode": "CURRENT_DETAILS"}).status_code == 302
    assert db.session.query(InvPrintJob).count() == 1
    assert client.post(f"{base}/identifiers",
                       data={"kind": "SERIAL", "value": "X1"}).status_code == 403
    assert client.post(f"{base}/identifiers/{ident.id}/retire").status_code == 403
    assert client.post(f"{base}/condition", data={"condition": "LOST"}).status_code == 403
    assert client.post(f"{base}/item", data={"item_id": str(inv.wap.id)}).status_code == 403
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).condition == "IN_SERVICE"
    assert db.session.get(InvUnit, unit.id).item_id == inv.laptop.id


def test_nobody_gets_403(client, unit):
    login(client, "test:nobody")
    assert client.get("/inv/admin/units").status_code == 403
    assert client.get(f"/inv/admin/units/{unit.id}").status_code == 403


def test_label_shaped_serial_is_refused_and_saves_nothing(client, unit):
    login(client, "test:invadmin")
    before = db.session.query(InvUnitIdentifier).count()
    resp = client.post(f"/inv/admin/units/{unit.id}/identifiers",
                       data={"kind": "SERIAL", "value": "U-0099"}, follow_redirects=True)
    assert "not a serial" in resp.get_data(as_text=True)
    assert db.session.query(InvUnitIdentifier).count() == before
