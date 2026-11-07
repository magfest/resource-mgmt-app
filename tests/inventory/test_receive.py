"""Owned receive: units, labels, refusals, undo and reprint."""
import os
import shutil
import subprocess
from datetime import datetime

import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.shifts import start_shift
from app.models import (
    InvDevice, InvItemTotal, InvMovement, InvPrinter, InvPrintJob, InvPrintRoute,
    InvStock, InvUnit, InvUnitIdentifier, SupplyItem,
)
from app.models.constants import INV_JOB_OWNED_UNIT
from tests.inventory.helpers import enrolled_client, make_container

RECEIVE = "/inv/scan/api/receive"
_n = [0]


def _rid():
    _n[0] += 1
    return f"rcv-{_n[0]}"


def _printer(seen=True):
    station, _ = enroll_device("Print PC", "test:invadmin", station_code="PRINT")
    if seen:
        station.last_seen_at = datetime.utcnow()
    printer = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=3,
                         device_id=station.id, device_name="Perm")
    db.session.add(printer)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=printer.id))
    db.session.commit()
    return printer


@pytest.fixture
def rc(client, inv):
    _printer()
    device = enrolled_client(client, station_code="RECEIVE")
    start_shift(device, "Sam")
    db.session.commit()
    return inv


def _post(client, inv, **kw):
    body = {"client_request_id": _rid(), "item_id": inv.laptop.id}
    body.update(kw)
    return client.post(RECEIVE, json=body).get_json()


def test_tag_and_serial_create_the_unit_and_queue_its_label(client, rc):
    r = _post(client, rc, tag="FEST00001", serial="S1")
    assert r["ok"] is True and r["code"] == "FEST00001" and r["station_seen"] is True
    unit = db.session.get(InvUnit, r["unit_id"])
    kinds = {i.kind: i.value for i in unit.identifiers}
    assert kinds == {"MAGFEST_TAG": "FEST00001", "SERIAL": "S1"}
    job = InvPrintJob.query.one()
    assert job.code == "FEST00001" and job.source == "SCAN" and job.id == r["job_id"]
    assert job.printed_date is not None and job.unit_id == unit.id
    assert ["Serial", "S1"] in job.fields["rows"]


def test_skipping_the_tag_generates_a_u_number(client, rc):
    r = _post(client, rc, serial="S1")
    assert r["code"] == "U-00001"
    kinds = {i.kind for i in db.session.get(InvUnit, r["unit_id"]).identifiers}
    assert kinds == {"FESTOPS_LABEL", "SERIAL"}
    assert _post(client, rc)["code"] == "U-00002"


def test_a_received_unit_is_reported_and_nothing_is_created(client, rc):
    first = _post(client, rc, tag="FEST00001", serial="S1")
    again = _post(client, rc, tag="FEST00002", serial="s1")
    assert again["ok"] is False
    assert again["already"]["code"] == "FEST00001"
    assert again["already"]["unit_id"] == first["unit_id"]
    assert InvUnit.query.count() == 1 and InvPrintJob.query.count() == 1
    from app.models import InvScanRequest
    assert InvScanRequest.query.count() == 1


def test_a_tag_on_another_item_names_that_unit(client, rc):
    first = _post(client, rc, tag="FEST00001")
    other = _post(client, rc, tag="FEST00001", item_id=rc.wap.id)
    assert other["ok"] is False and other["already"]["unit_id"] == first["unit_id"]
    assert other["already"]["item_name"] == "Laptop"
    assert InvUnit.query.count() == 1


def test_the_same_serial_on_a_different_item_is_allowed(client, rc):
    _post(client, rc, serial="S1")
    assert _post(client, rc, serial="S1", item_id=rc.wap.id)["ok"] is True


def test_a_retired_unit_does_not_block_its_tag(client, rc):
    first = _post(client, rc, tag="FEST00001")
    client.post(RECEIVE + "/undo", json={"client_request_id": _rid(), "unit_id": first["unit_id"]})
    assert _post(client, rc, tag="FEST00001")["ok"] is True


def test_the_same_code_in_both_steps_is_refused(client, rc):
    r = _post(client, rc, tag="ABC123", serial="abc123")
    assert r["ok"] is False and InvUnit.query.count() == 0


def test_a_tag_no_barcode_can_hold_is_refused(client, rc):
    r = _post(client, rc, tag="T" * 21)
    assert r == {"ok": False, "message": "This tag cannot be printed as a barcode."}
    assert InvUnit.query.count() == 0 and InvPrintJob.query.count() == 0


def test_a_counted_item_is_refused(client, rc):
    r = _post(client, rc, item_id=rc.pens.id, serial="S1")
    assert r["ok"] is False and "counted stock" in r["message"]
    assert InvUnit.query.count() == 0


def test_a_scan_retry_creates_one_unit_and_one_job(client, rc):
    body = {"client_request_id": "same", "item_id": rc.laptop.id, "tag": "FEST00001"}
    a = client.post(RECEIVE, json=body).get_json()
    b = client.post(RECEIVE, json=body).get_json()
    assert a == b and a["ok"] is True
    assert InvUnit.query.count() == 1 and InvPrintJob.query.count() == 1


def test_receiving_never_changes_a_total(client, rc):
    db.session.add(InvItemTotal(item_id=rc.laptop.id, quantity=200))
    db.session.commit()
    r = _post(client, rc, tag="FEST00001")
    client.post(RECEIVE + "/undo", json={"client_request_id": _rid(), "unit_id": r["unit_id"]})
    assert InvItemTotal.query.one().quantity == 200 and InvStock.query.count() == 0


def test_undo_retires_the_unit_and_the_number_is_not_reused(client, rc):
    a = _post(client, rc, serial="S1")
    undone = client.post(RECEIVE + "/undo",
                         json={"client_request_id": _rid(), "unit_id": a["unit_id"]}).get_json()
    assert undone["ok"] is True and undone["message"] == "Label cancelled."
    assert InvPrintJob.query.one().status == "CANCELLED"
    unit = db.session.get(InvUnit, a["unit_id"])
    assert unit.condition == "RETIRED"
    assert all(i.retired_at for i in unit.identifiers)
    assert InvMovement.query.filter_by(unit_id=a["unit_id"]).count() == 1
    assert _post(client, rc, serial="S2")["code"] == "U-00002"


def test_undo_after_the_label_was_collected_says_to_throw_it_away(client, rc):
    a = _post(client, rc, tag="FEST00001")
    InvPrintJob.query.one().status = "CLAIMED"
    db.session.commit()
    r = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                             "unit_id": a["unit_id"]}).get_json()
    assert r["ok"] is True and r["message"] == "Throw that label away."
    assert InvPrintJob.query.one().status == "CLAIMED"


def test_undo_applies_only_to_the_last_unit_and_only_once(client, rc):
    a = _post(client, rc, tag="FEST00001")
    b = _post(client, rc, tag="FEST00002")
    refused = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                                   "unit_id": a["unit_id"]}).get_json()
    assert refused["ok"] is False
    ok = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                              "unit_id": b["unit_id"]}).get_json()
    assert ok["ok"] is True
    again = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                                 "unit_id": a["unit_id"]}).get_json()
    assert again["ok"] is False
    assert db.session.get(InvUnit, a["unit_id"]).condition == "IN_SERVICE"


def test_undo_refuses_a_unit_from_another_shift(client, rc):
    a = _post(client, rc, tag="FEST00001")
    device = InvDevice.query.filter_by(station_code="RECEIVE").one()
    start_shift(device, "Alex")
    db.session.commit()
    r = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                             "unit_id": a["unit_id"]}).get_json()
    assert r["ok"] is False and db.session.get(InvUnit, a["unit_id"]).condition == "IN_SERVICE"


def test_receive_works_with_the_print_station_down(client, inv):
    _printer(seen=False)
    start_shift(enrolled_client(client, station_code="RECEIVE"), "Sam")
    db.session.commit()
    r = _post(client, inv, tag="FEST00001")
    assert r["ok"] is True and r["station_seen"] is False and r["waiting"] == 1
    assert InvUnit.query.count() == 1


def test_a_missing_printer_route_refuses_the_whole_receive(client, inv):
    start_shift(enrolled_client(client, station_code="RECEIVE"), "Sam")
    db.session.commit()
    r = _post(client, inv, serial="S1")
    assert r["ok"] is False and "No printer" in r["message"]
    assert InvUnit.query.count() == 0 and InvPrintJob.query.count() == 0


def test_a_receive_without_a_name_is_refused(client, inv):
    _printer()
    enrolled_client(client, station_code="RECEIVE")
    r = _post(client, inv, tag="FEST00001")
    assert r["ok"] is False and r["need_name"] is True and InvUnit.query.count() == 0


def test_an_active_container_sets_the_location(client, rc):
    c = make_container(rc, "C-0001")
    db.session.commit()
    r = _post(client, rc, tag="FEST00001", container_code="c-0001")
    assert db.session.get(InvUnit, r["unit_id"]).current_container_id == c.id


def test_a_location_that_is_not_an_active_container_is_refused(client, rc):
    make_container(rc, "C-0001").is_active = False
    db.session.commit()
    first = _post(client, rc, tag="FEST00001")
    assert _post(client, rc, tag="FEST00002", container_code="C-0001")["ok"] is False
    assert _post(client, rc, tag="FEST00003", container_code="FEST00001")["ok"] is False
    assert _post(client, rc, tag="FEST00004", container_code="nothing")["ok"] is False
    assert InvUnit.query.count() == 1 and first["ok"]


def test_the_location_check_answers_for_setup(client, rc):
    make_container(rc, "C-0001", name="Dock")
    db.session.commit()
    ok = client.post(RECEIVE + "/location", json={"code": "C-0001"}).get_json()
    assert ok["ok"] is True and ok["code"] == "C-0001"
    bad = client.post(RECEIVE + "/location", json={"code": "zzz"}).get_json()
    assert bad["ok"] is False


def test_item_search_lists_active_serialized_items_only(client, rc):
    rc.wap.is_active = False
    for i in range(25):
        db.session.add(SupplyItem(category_id=rc.category.id, item_name=f"Lap {i:02d}",
                                  unit="each", is_serialized=True))
    db.session.commit()
    r = client.post(RECEIVE + "/items", json={"q": "lap"}).get_json()
    assert r["ok"] and len(r["items"]) == 20
    assert all(i["name"].lower().count("lap") for i in r["items"])
    names = [i["name"] for i in client.post(RECEIVE + "/items", json={"q": ""}).get_json()["items"]]
    assert "Blue Pen" not in names and "Wireless Access Point" not in names


def test_reprint_queues_a_new_job_with_the_same_fields_and_date(client, rc):
    a = _post(client, rc, tag="FEST00001", serial="S1")
    first = InvPrintJob.query.one()
    first.printed_date = datetime(2026, 1, 2).date()
    db.session.commit()
    body = {"client_request_id": "rp-1", "unit_id": a["unit_id"]}
    r = client.post(RECEIVE + "/reprint", json=body).get_json()
    again = client.post(RECEIVE + "/reprint", json=body).get_json()
    assert r["ok"] and r == again and InvPrintJob.query.count() == 2
    job = db.session.get(InvPrintJob, r["job_id"])
    assert job.fields == first.fields and job.printed_date == first.printed_date
    assert job.code == "FEST00001" and job.source == "SCAN"


def test_reprint_refuses_a_retired_unit(client, rc):
    a = _post(client, rc, tag="FEST00001")
    client.post(RECEIVE + "/undo", json={"client_request_id": _rid(), "unit_id": a["unit_id"]})
    r = client.post(RECEIVE + "/reprint", json={"client_request_id": _rid(),
                                                "unit_id": a["unit_id"]}).get_json()
    assert r["ok"] is False


def test_the_page_needs_the_receive_station_and_a_shift(client, inv):
    enrolled_client(client, station_code="RECEIVE")
    assert client.get("/inv/scan/receive").status_code == 302
    assert "/scan/who" in client.get("/inv/scan/receive").headers["Location"]
    device = InvDevice.query.one()
    start_shift(device, "Sam")
    db.session.commit()
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/receive")
    page = client.get("/inv/scan/receive")
    assert page.status_code == 200 and b"station_receive.js" in page.data
    device.station_code = "LOOKUP"
    db.session.commit()
    assert client.get("/inv/scan/receive").status_code == 302


FLOW_SCRIPT = r"""
const r = require(process.env.RECEIVE_JS);
const assert = require('assert');
let l = r.createLoop('BOTH');
assert.equal(l.prompt(), 'Scan the ASSET TAG');
assert.deepEqual(l.scan('FEST1'), {kind: 'hold'});
assert.equal(l.scan('fest1').kind, 'refuse');
assert.equal(l.prompt(), 'Scan the SERIAL');
assert.deepEqual(l.scan('S1'), {kind: 'submit', payload: {tag: 'FEST1', serial: 'S1'}});
l.reset(); l.skip();
assert.deepEqual(l.skip(), {kind: 'submit', payload: {tag: null, serial: null}});
assert.deepEqual(r.createLoop('SERIAL').scan('S2'),
                 {kind: 'submit', payload: {tag: null, serial: 'S2'}});
assert.deepEqual(r.progress(38, 200), {text: '38 of 200', over: false});
assert.deepEqual(r.progress(38, null), {text: '38 done', over: false});
assert.equal(r.progress(205, 200).over, true);
assert.equal(r.printerWaiting({station_seen: false, waiting: 1}), true);
assert.equal(r.printerWaiting({station_seen: true, waiting: 6}), true);
assert.equal(r.printerWaiting({station_seen: true, waiting: 5}), false);
console.log('flow ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_receive_flow_logic_under_node(tmp_path):
    """Runs the pure step and progress logic under node; it does not drive a browser."""
    script = tmp_path / "flow_check.js"
    script.write_text(FLOW_SCRIPT)
    path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static",
                        "inventory", "station_receive.js")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                            env={**os.environ, "RECEIVE_JS": os.path.abspath(path)})
    assert result.returncode == 0, result.stderr
    assert "flow ok" in result.stdout
