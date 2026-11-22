"""Owned receive: units, labels, refusals, undo and reprint."""
import os
import shutil
import subprocess
from datetime import datetime

import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, replace_number, set_condition
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
    start_shift(device, "test:operator")
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


def test_a_sign_in_card_is_refused_as_a_tag_or_a_serial(client, rc):
    for kw in ({"tag": "OP-ABCDEFGHJKMN", "serial": "S1"}, {"serial": "op-abcdefghjkmn"}):
        r = _post(client, rc, **kw)
        assert r == {"ok": False, "message": "That is a sign-in card."}, kw
    assert InvUnit.query.count() == 0 and InvUnitIdentifier.query.count() == 0
    assert InvPrintJob.query.count() == 0


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


RETIRED_TAG = "FEST00001 belongs to a retired asset. Ask a lead."


def _retire(unit_id):
    set_condition(db.session.get(InvUnit, unit_id), "RETIRED")
    db.session.commit()


def test_a_retired_assets_tag_is_refused_and_creates_nothing(client, rc):
    first = _post(client, rc, tag="FEST00001")
    _retire(first["unit_id"])
    assert _post(client, rc, tag="FEST00001") == {"ok": False, "message": RETIRED_TAG}
    assert client.post(CHECK, json={"tag": "fest00001"}).get_json() == \
        {"ok": False, "message": RETIRED_TAG}
    assert InvUnit.query.count() == 1


def test_a_tag_received_again_after_an_undo_then_retired_is_refused(client, rc):
    first = _post(client, rc, tag="FEST00001")
    _undo(client, first["unit_id"])
    second = _post(client, rc, tag="FEST00001")
    _retire(second["unit_id"])
    assert _post(client, rc, tag="FEST00001") == {"ok": False, "message": RETIRED_TAG}


def test_an_undone_receipt_brought_back_then_retired_keeps_its_tag(client, rc):
    first = _post(client, rc, tag="FEST00001")
    _undo(client, first["unit_id"])
    unit = db.session.get(InvUnit, first["unit_id"])
    set_condition(unit, "IN_SERVICE")
    db.session.commit()
    _retire(first["unit_id"])
    assert _post(client, rc, tag="FEST00001") == {"ok": False, "message": RETIRED_TAG}


def test_a_tag_a_lead_moved_off_a_live_asset_is_free(client, rc):
    first = _post(client, rc, tag="FEST00001")
    unit = db.session.get(InvUnit, first["unit_id"])
    replace_number(unit, "MAGFEST_TAG", "FEST00002", Actor(user_id="test:invadmin"))
    db.session.commit()
    assert _post(client, rc, tag="FEST00001")["ok"] is True


def test_the_same_code_in_both_steps_is_refused(client, rc):
    r = _post(client, rc, tag="ABC123", serial="abc123")
    assert r["ok"] is False and InvUnit.query.count() == 0


def test_a_tag_no_barcode_can_hold_is_refused(client, rc):
    r = _post(client, rc, tag="T" * 21)
    assert r == {"ok": False, "message": "This asset tag cannot be printed as a barcode."}
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


def _undo(client, unit_id):
    return client.post(RECEIVE + "/undo",
                       json={"client_request_id": _rid(), "unit_id": unit_id}).get_json()


def test_undo_cancels_every_queued_label_for_the_unit(client, rc):
    a = _post(client, rc, tag="FEST00001")
    client.post(RECEIVE + "/reprint", json={"client_request_id": _rid(), "unit_id": a["unit_id"]})
    assert InvPrintJob.query.filter_by(status="QUEUED").count() == 2
    r = _undo(client, a["unit_id"])
    assert r["ok"] is True and r["message"] == "Label cancelled."
    assert InvPrintJob.query.filter_by(status="QUEUED").count() == 0


@pytest.mark.parametrize("state", ["CLAIMED", "SENT", "FAILED"])
def test_undo_says_throw_it_away_when_the_original_was_collected(client, rc, state):
    a = _post(client, rc, tag="FEST00001")
    InvPrintJob.query.one().status = state
    db.session.commit()
    client.post(RECEIVE + "/reprint", json={"client_request_id": _rid(), "unit_id": a["unit_id"]})
    r = _undo(client, a["unit_id"])
    assert r["ok"] is True and r["message"] == "Throw that label away."
    assert InvPrintJob.query.filter_by(status="QUEUED").count() == 0


def test_a_serial_over_the_label_limit_is_refused(client, rc):
    ok = _post(client, rc, serial="S" * 64)
    assert ok["ok"] is True
    r = _post(client, rc, serial="S" * 65)
    assert r["ok"] is False and "64" in r["message"]
    assert InvUnit.query.count() == 1


def test_the_loser_of_a_tag_race_gets_the_already_received_answer(client, rc, monkeypatch):
    first = _post(client, rc, tag="FEST00001")
    # The loser passed the pre-checks before the winner committed.
    monkeypatch.setattr("app.inventory.receive.check_tag", lambda tag: None)
    r = _post(client, rc, tag="FEST00001")
    assert r["ok"] is False and r["already"]["unit_id"] == first["unit_id"]
    assert InvUnit.query.count() == 1 and InvPrintJob.query.count() == 1


CHECK = RECEIVE + "/check-tag"


def test_check_tag_accepts_a_free_printable_tag_and_creates_nothing(client, rc):
    r = client.post(CHECK, json={"tag": "FEST00001"}).get_json()
    assert r == {"ok": True}
    assert InvUnit.query.count() == 0 and InvPrintJob.query.count() == 0


def test_check_tag_refuses_unprintable_label_shaped_and_held_tags(client, rc):
    assert client.post(CHECK, json={"tag": "T" * 21}).get_json()["message"] == \
        "This asset tag cannot be printed as a barcode."
    shaped = client.post(CHECK, json={"tag": "U-00001"}).get_json()
    assert shaped["ok"] is False and "tap Skip" in shaped["message"]
    first = _post(client, rc, tag="FEST00001")
    held = client.post(CHECK, json={"tag": "fest00001"}).get_json()
    assert held["ok"] is False and held["already"]["unit_id"] == first["unit_id"]
    assert client.post(CHECK, json={"tag": ""}).get_json()["ok"] is False
    assert client.post(CHECK, json={"tag": 5}).get_json()["ok"] is False


def test_receive_and_check_tag_agree_on_a_label_shaped_tag(client, rc):
    check = client.post(CHECK, json={"tag": "U-00001"}).get_json()
    assert _post(client, rc, tag="U-00001")["message"] == check["message"]


RECEIVE_ROUTES = [
    (RECEIVE + "/items", {"q": ""}),
    (RECEIVE + "/location", {"code": "C-0001"}),
    (CHECK, {"tag": "FEST00001"}),
    (RECEIVE, {"client_request_id": "g-1", "item_id": 1, "tag": "FEST00001"}),
    (RECEIVE + "/undo", {"client_request_id": "g-2", "unit_id": 1}),
    (RECEIVE + "/reprint", {"client_request_id": "g-3", "unit_id": 1}),
]


def test_receive_routes_refuse_a_print_device(client, inv):
    _printer()
    enrolled_client(client, station_code="PRINT")
    for path, body in RECEIVE_ROUTES:
        assert client.post(path, json=body).status_code == 403, path


def test_receive_routes_need_json(client, rc):
    for path, body in RECEIVE_ROUTES:
        assert client.post(path, data=body).status_code == 415, path


def test_receive_routes_are_csrf_exempt_with_csrf_on(app, client, rc):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        statuses = [client.post(path, json=body).status_code for path, body in RECEIVE_ROUTES]
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert statuses == [200] * len(RECEIVE_ROUTES)


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
    start_shift(device, "test:invadmin")
    db.session.commit()
    r = client.post(RECEIVE + "/undo", json={"client_request_id": _rid(),
                                             "unit_id": a["unit_id"]}).get_json()
    assert r["ok"] is False and db.session.get(InvUnit, a["unit_id"]).condition == "IN_SERVICE"


def test_receive_works_with_the_print_station_down(client, inv):
    _printer(seen=False)
    start_shift(enrolled_client(client, station_code="RECEIVE"), "test:operator")
    db.session.commit()
    r = _post(client, inv, tag="FEST00001")
    assert r["ok"] is True and r["station_seen"] is False and r["waiting"] == 1
    assert InvUnit.query.count() == 1


def test_a_missing_printer_route_refuses_the_whole_receive(client, inv):
    start_shift(enrolled_client(client, station_code="RECEIVE"), "test:operator")
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
    start_shift(device, "test:operator")
    db.session.commit()
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/receive")
    page = client.get("/inv/scan/receive")
    assert page.status_code == 200 and b"station_receive.js" in page.data
    device.station_code = "LOOKUP"
    db.session.commit()
    assert client.get("/inv/scan/receive").status_code == 302


FLOW_SCRIPT = r"""
const r = require(process.env.RECEIVE_JS);
const scan = require(process.env.SCAN_JS);
const assert = require('assert');
let l = r.createLoop('BOTH');
assert.equal(l.prompt(), 'Scan the ASSET TAG');
assert.deepEqual(l.scan('FEST1'), {kind: 'hold'});
assert.equal(l.scan('fest1').kind, 'refuse');
assert.equal(l.prompt(), 'Scan the SERIAL');
assert.equal(l.scan('S'.repeat(65)).kind, 'refuse');
assert.equal(l.prompt(), 'Scan the SERIAL');
assert.deepEqual(l.scan('S'.repeat(64)).kind, 'submit');
l.reset(); l.skip();
assert.deepEqual(l.skip(), {kind: 'submit', payload: {tag: null, serial: null}});
assert.deepEqual(r.createLoop('SERIAL').scan('S2'),
                 {kind: 'submit', payload: {tag: null, serial: 'S2'}});
assert.deepEqual(r.progress(38, 200), {text: '38 of 200', over: false});
assert.deepEqual(r.progress(38, null), {text: '38 done', over: false});
assert.equal(r.progress(205, 200).over, true);
assert.equal(scan.printerWaiting({station_seen: false, waiting: 1}), true);
assert.equal(scan.printerWaiting({station_seen: true, waiting: 6}), true);
assert.equal(scan.printerWaiting({station_seen: true, waiting: 5}), false);
console.log('flow ok');
"""

# Loads the whole page script against a stub document and shell, and drives a Skip submit.
PAGE_SCRIPT = r"""
const assert = require('assert');
function Node(tag) {
  this.tag = tag; this.children = []; this.className = ''; this.style = {};
  this.handlers = {}; this._text = ''; this.disabled = false;
}
Node.prototype = {
  get textContent() { return this._text; },
  set textContent(v) { this._text = v; if (v === '') { this.children = []; } },
  appendChild(c) { this.children.push(c); return c; },
  addEventListener(t, f) { this.handlers[t] = f; },
  setAttribute() {}, focus() {}
};
function all(n, out) { out.push(n); n.children.forEach((c) => all(c, out)); return out; }
function texts(n) { return all(n, []).map((x) => x._text); }
function find(n, label) { return all(n, []).find((x) => x.tag === 'button' && x._text === label); }
const panel = new Node('div');
const nodes = {panel: panel, 'scan-input': new Node('input')};
globalThis.document = {
  currentScript: {dataset: {itemsUrl: '/items', checkTagUrl: '/check', receiveUrl: '/receive',
                            locationUrl: '/loc', undoUrl: '/undo', reprintUrl: '/reprint'}},
  getElementById: (id) => nodes[id],
  createElement: (t) => new Node(t)
};
const writes = [];
const reads = [];
let onScan;
const subjects = [];
globalThis.InvScan = {
  start: (cfg) => { onScan = cfg.onScan; },
  setFlagSubject: (subject) => subjects.push(subject),
  messages: {offline: 'offline'},
  printerWaiting: () => false,
  feedback() {}, showMessage() {}, numberPad() {},
  askText: (label, cb) => cb('lap'),
  choose: (opts, pick) => pick(opts[0].value),
  read: (path, body) => {
    reads.push([path, body]);
    if (path === '/items') { return Promise.resolve({ok: true, items: [{id: 1, name: 'Laptop'}]}); }
    return Promise.resolve({ok: true});
  },
  write: (path, body) => new Promise((resolve) => writes.push({path, body, resolve}))
};
require(process.env.RECEIVE_JS);
const tick = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  find(panel, 'Choose item').handlers.click();
  await tick();
  find(panel, 'Start scanning').handlers.click();
  await onScan('T1');
  await tick();
  find(panel, 'Skip serial').handlers.click();
  await tick();
  assert.equal(writes.length, 1, 'skip submitted one unit');
  const during = onScan('T2');
  await tick();
  assert.equal(writes.length, 1, 'a scan during the submit makes no second submit');
  writes[0].resolve({ok: true, unit_id: 1, code: 'U-00001', station_seen: true, waiting: 0});
  await during;
  assert.equal(writes.length, 1);
  assert.deepEqual(subjects[subjects.length - 1], {unitId: 1, label: 'U-00001'}, 'Flag offers the unit just received');
  assert.ok(texts(panel).includes('Asset tag: T2 ✓'), 'the scan is held for the next unit');
  assert.ok(reads.some((x) => x[0] === '/check' && x[1].tag === 'T2'));
  find(panel, 'Change setup').handlers.click();
  assert.strictEqual(subjects[subjects.length - 1], null, 'setup offers nothing to flag');
  console.log('page ok');
})().catch((e) => { console.error(e); process.exit(1); });
"""

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")


def _node(tmp_path, script, name, **env):
    path = tmp_path / name
    path.write_text(script)
    return subprocess.run(["node", str(path)], capture_output=True, text=True,
                          env={**os.environ, **env})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_receive_flow_logic_under_node(tmp_path):
    """Runs the pure step and progress logic under node; it does not drive a browser."""
    result = _node(tmp_path, FLOW_SCRIPT, "flow_check.js",
                   RECEIVE_JS=os.path.abspath(os.path.join(STATIC, "station_receive.js")),
                   SCAN_JS=os.path.abspath(os.path.join(STATIC, "scan.js")))
    assert result.returncode == 0, result.stderr
    assert "flow ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_scan_during_a_skip_submit_waits_for_the_next_unit(tmp_path):
    """Runs the page script on a stub DOM under node; it does not drive a browser."""
    result = _node(tmp_path, PAGE_SCRIPT, "page_check.js",
                   RECEIVE_JS=os.path.abspath(os.path.join(STATIC, "station_receive.js")))
    assert result.returncode == 0, result.stderr
    assert "page ok" in result.stdout
