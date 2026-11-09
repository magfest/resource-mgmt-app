"""The scan shell: stored retries, the resolve endpoint, and the look-up card."""
import json
import os
import shutil
import subprocess

import pytest

from app import db
from app.inventory.cards import card_for, default_event
from app.inventory.ledger import (
    Actor, InventoryError, add_unit, create_container, set_condition,
)
from app.inventory.notes import add_note
from app.inventory.receipts import run_once
from app.inventory.resolver import resolve
from app.inventory.labels import allocate
from app.inventory.shifts import start_shift
from app.models import (
    EventCycle, InvCheck, InvContainer, InvFlag, InvPrepStatus, InvScanRequest,
)
from tests.inventory.helpers import enrolled_client, login, make_container

ACTOR = Actor(user_id="test:invadmin")


def _event(default=True):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=default)
    db.session.add(cycle)
    db.session.flush()
    return cycle


def test_retry_writes_once(client, inv):
    enrolled_client(client)
    from app.models import InvDevice
    start_shift(InvDevice.query.one(), "Sam")
    body = {"client_request_id": "r-1", "label_code": allocate("C", 1)[0],
            "kind_id": inv.kinds["BIN"].id}
    db.session.commit()
    first = client.post("/inv/scan/api/container", json=body).get_json()
    second = client.post("/inv/scan/api/container", json=body).get_json()
    assert first["ok"] is True
    assert first == second and InvContainer.query.count() == 1


def test_container_is_created_under_the_home_container(client, inv):
    home = make_container(inv, "C-0001", kind="LOCATION")
    code = allocate("C", 2)[1]
    device = enrolled_client(client, station_code="COUNT")
    device.home_container_id = home.id
    start_shift(device, "Sam")
    db.session.commit()
    r = client.post("/inv/scan/api/container", json={
        "client_request_id": "r-2", "label_code": code, "kind_id": inv.kinds["BIN"].id}).get_json()
    assert r["ok"] is True
    assert InvContainer.query.filter_by(label_code=code).one().parent_id == home.id


def test_a_write_without_a_name_is_refused_and_not_stored(client, inv):
    enrolled_client(client, station_code="COUNT")
    code = allocate("C", 1)[0]
    db.session.commit()
    r = client.post("/inv/scan/api/container", json={
        "client_request_id": "r-3", "label_code": code, "kind_id": inv.kinds["BIN"].id}).get_json()
    assert r["ok"] is False and "name" in r["message"]
    assert InvScanRequest.query.count() == 0 and InvContainer.query.count() == 0


def test_a_label_never_issued_is_refused(client, inv):
    enrolled_client(client, station_code="COUNT")
    from app.models import InvDevice
    start_shift(InvDevice.query.one(), "Sam")
    db.session.commit()
    r = client.post("/inv/scan/api/container", json={
        "client_request_id": "r-4", "label_code": "C-0099",
        "kind_id": inv.kinds["BIN"].id}).get_json()
    assert r["ok"] is False
    assert InvContainer.query.count() == 0


def test_run_once_stores_a_success_and_skips_a_refusal(app, inv):
    calls = []

    def good():
        calls.append(1)
        return {"ok": True, "n": len(calls)}

    def bad():
        raise InventoryError("Nope")

    assert run_once("a", good) == run_once("a", good) == {"ok": True, "n": 1}
    assert run_once("b", bad) == {"ok": False, "message": "Nope"}
    assert InvScanRequest.query.filter_by(client_request_id="b").count() == 0
    assert json.loads(InvScanRequest.query.filter_by(client_request_id="a").one().response_json)["n"] == 1


def test_run_once_returns_the_stored_answer_when_the_first_copy_already_saved(app, inv):
    def first_copy_wins():
        db.session.add(InvScanRequest(client_request_id="dup",
                                      response_json=json.dumps({"ok": True, "n": 1})))
        db.session.commit()
        raise InventoryError("Already exists")

    assert run_once("dup", first_copy_wins) == {"ok": True, "n": 1}


def test_run_once_forces_ok_true_on_a_stored_save(app, inv):
    answer = run_once("force", lambda: {"ok": False, "n": 2})
    assert answer == {"ok": True, "n": 2}
    assert json.loads(InvScanRequest.query.filter_by(client_request_id="force").one()
                      .response_json)["ok"] is True


def test_resolve_caps_candidates_at_ten(client, inv):
    from app.models import SupplyItem
    enrolled_client(client)
    for i in range(12):
        db.session.add(SupplyItem(category_id=inv.category.id, item_name=f"Cable {i}",
                                  unit="each", barcode="555"))
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "555"}).get_json()
    assert len(r["candidates"]) == 10 and r["more"] == 2
    few = client.post("/inv/scan/api/resolve", json={"code": "zzz"}).get_json()
    assert "more" not in few


def test_a_non_string_label_code_is_a_normal_refusal(client, inv):
    from app.models import InvDevice
    enrolled_client(client, station_code="COUNT")
    start_shift(InvDevice.query.one(), "Sam")
    db.session.commit()
    for bad in (123, ["C-0001"], {"a": 1}):
        r = client.post("/inv/scan/api/container", json={
            "client_request_id": "bad-" + str(type(bad).__name__), "label_code": bad,
            "kind_id": inv.kinds["BIN"].id})
        assert r.status_code == 200 and r.get_json()["ok"] is False


def _session_cookies(response):
    return [c for c in response.headers.getlist("Set-Cookie") if c.startswith("session=")]


def test_scanner_api_calls_do_not_renew_a_user_session(client, inv):
    login(client, "test:operator")
    response = client.post("/inv/scan/api/resolve", json={"code": "zzz"})
    assert response.status_code == 200 and not _session_cookies(response)


def test_the_enroll_page_still_sets_its_session(client, inv):
    assert _session_cookies(client.get("/inv/scan/enroll"))


def test_run_once_refuses_a_missing_request_id(app, inv):
    assert run_once("", lambda: {"ok": True})["ok"] is False
    assert run_once(None, lambda: {"ok": True})["ok"] is False


def test_the_container_route_is_csrf_exempt_with_csrf_on(app, client, inv):
    enrolled_client(client, station_code="COUNT")
    from app.models import InvDevice
    start_shift(InvDevice.query.one(), "Sam")
    code = allocate("C", 1)[0]
    db.session.commit()
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        r = client.post("/inv/scan/api/container", json={
            "client_request_id": "r-5", "label_code": code, "kind_id": inv.kinds["BIN"].id})
        resolved = client.post("/inv/scan/api/resolve", json={"code": "nothing"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert resolved.status_code == 200


def test_scan_routes_refuse_a_print_device_with_their_own_403(client, inv):
    enrolled_client(client, station_code="PRINT")
    r = client.post("/inv/scan/api/resolve", json={"code": "x"})
    assert r.status_code == 403 and r.get_json()["ok"] is False


def test_resolve_needs_json(client, inv):
    enrolled_client(client)
    assert client.post("/inv/scan/api/resolve", data={"code": "x"}).status_code == 415


def test_resolve_never_names_a_wrong_station(client, inv):
    inv.laptop.check_station = "PRINTER_CHECK"
    enrolled_client(client, station_code="UNIT")
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "P1")])
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "P1"}).get_json()
    assert "wrong_station" not in r
    assert r["kind"] == "UNIT" and r["card"]["title"] == "Laptop"


def test_resolve_reports_unknown_and_unused_codes(client, inv):
    enrolled_client(client)
    code = allocate("C", 1)[0]
    db.session.commit()
    unknown = client.post("/inv/scan/api/resolve", json={"code": "zzz"}).get_json()
    unused = client.post("/inv/scan/api/resolve", json={"code": code}).get_json()
    assert unknown["kind"] == "UNKNOWN" and "card" not in unknown
    assert unused["kind"] == "UNUSED" and unused["prefix"] == "C"


def test_resolve_lists_ambiguous_units_with_cards(client, inv):
    enrolled_client(client)
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "DUP")])
    add_unit(inv.wap, None, ACTOR, [("SERIAL", "DUP")])
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "DUP"}).get_json()
    assert r["kind"] == "AMBIGUOUS"
    assert sorted(c["card"]["title"] for c in r["candidates"]) == [
        "Laptop", "Wireless Access Point"]


def test_resolve_lists_items_that_share_a_barcode(client, inv):
    inv.pens.barcode = "123"
    inv.laptop.barcode = "123"
    enrolled_client(client)
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "123"}).get_json()
    assert r["kind"] == "AMBIGUOUS" and len(r["candidates"]) == 2


def test_an_inactive_container_still_resolves_and_the_card_says_so(client, inv):
    box = make_container(inv, "C-0001", kind="BIN")
    box.is_active = False
    enrolled_client(client)
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "C-0001"}).get_json()
    assert r["kind"] == "CONTAINER" and "Inactive" in r["card"]["lines"]


def test_an_inactive_item_card_says_so(client, inv):
    inv.pens.is_active = False
    inv.pens.barcode = "999"
    enrolled_client(client)
    db.session.commit()
    r = client.post("/inv/scan/api/resolve", json={"code": "999"}).get_json()
    assert r["kind"] == "ITEM" and "Inactive" in r["card"]["lines"]


def test_lookup_card_shows_path_condition_prep_and_history(app, inv):
    event = _event()
    outer = make_container(inv, "C-0001", kind="LOCATION")
    inner = make_container(inv, "C-0002", kind="BIN", parent=outer)
    unit = add_unit(inv.laptop, inner, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    card = card_for(resolve("S1"), event)
    assert card["path"].endswith("C-0002 (Bin)")
    assert "C-0001" in card["path"]
    assert "In service" in card["lines"] and "Not checked" in card["lines"]
    assert card["history"] and "Added Laptop" in card["history"][0]["text"]
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status="CHECKED"))
    set_condition(unit, "REPAIR")
    db.session.add(InvFlag(unit_id=unit.id, station_code="LOOKUP", reason="DAMAGED",
                           note="Cracked bezel"))
    db.session.commit()
    card = card_for(resolve("S1"), event)
    assert "Needs repair" in card["lines"] and any(x.startswith("Works \u00b7 ") for x in card["lines"])
    assert any("Cracked bezel" in line for line in card["lines"])


def test_a_voided_prep_row_reads_as_not_checked(app, inv):
    event = _event()
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    check = InvCheck(unit_id=unit.id, event_cycle_id=event.id, station_code="TV_CHECK",
                     status="VOIDED")
    db.session.add(check)
    db.session.flush()
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status="CHECKED",
                                 check_id=check.id))
    db.session.flush()
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status="VOIDED",
                                 check_id=check.id))
    db.session.commit()
    assert "Not checked" in card_for(resolve("S1"), event)["lines"]


def test_history_keeps_only_the_last_ten_movements(app, inv):
    outer = make_container(inv, "C-0001", kind="LOCATION")
    other = make_container(inv, "C-0002", kind="LOCATION")
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    from app.inventory.ledger import move_unit
    for _ in range(6):
        move_unit(unit, outer, ACTOR)
        move_unit(unit, other, ACTOR)
    db.session.commit()
    assert len(card_for(resolve("S1"), None)["history"]) == 10


def test_a_container_card_lists_its_contents_and_history(app, inv):
    kind = inv.kinds["BIN"]
    box = create_container("C-0001", kind, ACTOR)
    create_container("C-0002", kind, ACTOR, parent=box)
    add_unit(inv.laptop, box, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    card = card_for(resolve("C-0001"), None)
    assert card["tag"] == "C-0001"
    assert "Holds 1 container and 1 unit" in card["lines"]
    assert "Created C-0001" in card["history"][0]["text"]


def test_default_event_is_the_default_cycle_only(app, inv):
    assert default_event() is None
    _event(default=False)
    assert default_event() is None
    other = EventCycle(code="X", name="X", is_active=True, is_default=True)
    db.session.add(other)
    db.session.commit()
    assert default_event().code == "X"


def test_lookup_station_renders_the_shell_without_a_name(client, inv):
    enrolled_client(client)
    html = client.get("/inv/scan/").get_data(as_text=True)
    assert 'id="scan-input"' in html and "station_lookup.js" in html
    assert "No name picked" not in html


def test_an_operator_gets_the_look_up_shell(client, inv):
    login(client, "test:operator")
    r = client.get("/inv/scan/")
    assert r.status_code == 200 and "station_lookup.js" in r.get_data(as_text=True)


CAPTURE_SCRIPT = r"""
const assert = require('assert');
const S = require(process.env.SCAN_JS);
const {createCapture, createRepeatGuard, createScanQueue, createWriteQueue,
       createPauseWatch, interpretResponse, createPromptGate, flagScanSound} = S;
function rig() {
  let now = 0, timers = [], got = [], cleared = 0, id = 0;
  const cap = createCapture({
    deliver: c => got.push(c), clear: () => cleared++,
    schedule: (fn, ms) => { const t = {fn, at: now + ms, id: ++id}; timers.push(t); return t.id; },
    cancel: h => { timers = timers.filter(t => t.id !== h); },
  });
  const advance = ms => { now += ms; timers.filter(t => t.at <= now).forEach(t => { timers = timers.filter(x => x !== t); t.fn(); }); };
  return {cap, got, advance};
}
let r = rig();
r.cap.input('ABC12345'); r.advance(79); assert.deepStrictEqual(r.got, []); r.advance(1);
assert.deepStrictEqual(r.got, ['ABC12345']);
r = rig();
for (const v of ['A','AB','ABC','ABC1']) { r.cap.input(v); r.advance(10); }
assert.deepStrictEqual(r.got, []); r.advance(80); assert.deepStrictEqual(r.got, ['ABC1']);
r = rig(); r.cap.input('XYZ'); r.cap.enter('XYZ'); r.advance(500); assert.deepStrictEqual(r.got, ['XYZ']);
r = rig(); r.cap.input('QQ\n'); assert.deepStrictEqual(r.got, ['QQ']);
r = rig(); r.cap.input('A'); r.advance(80); r.cap.input('B'); r.advance(80); assert.deepStrictEqual(r.got, ['A','B']);
const g = createRepeatGuard(5000);
assert.strictEqual(g.check('a', 1000), null);
g.record('a', 1000);
assert.strictEqual(g.check('a', 5000), 4);
assert.strictEqual(g.check('a', 6001), null); assert.strictEqual(g.check('b', 6002), null);
g.record('b', 6002); g.record('a', 6003);
assert.strictEqual(g.check('b', 6500), 1); assert.strictEqual(g.check('a', 6500), 1);

const tick = () => new Promise(r => setImmediate(r));
const deferred = () => { let res; const p = new Promise(r => { res = r; }); return {p, res}; };

(async () => {
  // Scans run one at a time, in order; an older answer cannot land after a newer one.
  let now = 0, log = [], gates = {};
  const q = createScanQueue({guard: createRepeatGuard(5000), now: () => now,
    onScan: code => { log.push('start ' + code); gates[code] = deferred();
                      return gates[code].p.then(a => { log.push('end ' + code); return a; }); }});
  assert.deepStrictEqual(q.submit('A'), {queued: true});
  assert.deepStrictEqual(q.submit('B'), {queued: true});
  await tick(); assert.deepStrictEqual(log, ['start A']);
  // A rescan of a code in flight or waiting is dropped silently.
  assert.deepStrictEqual(q.submit('A'), {dropped: true});
  assert.deepStrictEqual(q.submit('B'), {dropped: true});
  gates.A.res({ok: true}); await tick(); await tick();
  assert.deepStrictEqual(log, ['start A', 'end A', 'start B']);
  gates.B.res({ok: false}); await tick(); await tick();
  // A is recorded (ok), B is forgotten (not ok), so only A is a repeat.
  now = 2000;
  assert.deepStrictEqual(q.submit('A'), {repeat: 2});
  assert.deepStrictEqual(q.submit('B'), {queued: true});
  await tick();
  gates.B.res(undefined); await tick(); await tick();
  assert.strictEqual(q.size(), 0);
  // A rejected scan is forgotten and the queue keeps going.
  const q2 = createScanQueue({guard: createRepeatGuard(5000), now: () => 0,
    onScan: code => code === 'X' ? Promise.reject(new Error('x')) : Promise.resolve({ok: true})});
  q2.submit('X'); q2.submit('Y'); await tick(); await tick(); await tick();
  assert.deepStrictEqual(q2.submit('X'), {queued: true});
  assert.deepStrictEqual(q2.submit('Y'), {repeat: 1});

  // Write queue: order, block, retry with the same payload, cancel, drain.
  let sent = [], fail = true, blocks = 0, unblocks = 0;
  const w = createWriteQueue({
    send: (path, payload) => { sent.push(payload.id); return (fail && payload.id === 1)
      ? Promise.reject(new Error('net')) : Promise.resolve({ok: true, id: payload.id}); },
    onBlock: () => { blocks++; }, onUnblock: () => { unblocks++; }});
  const r1 = w.write('/x', {id: 1}); const r2 = w.write('/x', {id: 2});
  await tick(); await tick();
  assert.deepStrictEqual(sent, [1]); assert.ok(w.isBlocked()); assert.strictEqual(w.pending(), 2);
  assert.strictEqual(await w.drain(), false);
  fail = false; w.retry(); assert.strictEqual(unblocks, 1);
  assert.deepStrictEqual(await r1, {ok: true, id: 1}); assert.deepStrictEqual(await r2, {ok: true, id: 2});
  assert.deepStrictEqual(sent, [1, 1, 2]); assert.strictEqual(w.pending(), 0);
  assert.strictEqual(await w.drain(), true);
  // Cancel resolves the blocked write as not saved; the next write then runs.
  fail = true; sent = [];
  const c1 = w.write('/x', {id: 1}); const c2 = w.write('/x', {id: 2});
  await tick(); await tick(); assert.ok(w.isBlocked());
  const cancelled = w.cancel();
  assert.deepStrictEqual(cancelled, {ok: false, cancelled: true, message: 'Not saved.'});
  assert.deepStrictEqual(await c1, cancelled);
  assert.deepStrictEqual(await c2, {ok: true, id: 2}); assert.ok(!w.isBlocked());

  // Pause watch: the banner needs more than 500 ms of lost focus.
  let shown = false, timers = [], tid = 0, suspended = false;
  const pw = createPauseWatch({delayMs: 500, suspended: () => suspended,
    schedule: (fn, ms) => { timers.push({fn, ms, id: ++tid}); return tid; },
    cancel: h => { timers = timers.filter(t => t.id !== h); },
    show: () => { shown = true; }, hide: () => { shown = false; }});
  pw.lost(); assert.strictEqual(timers[0].ms, 500); assert.ok(!shown);
  pw.regained(); assert.strictEqual(timers.length, 0); assert.ok(!shown);
  pw.lost(); timers.shift().fn(); assert.ok(shown);
  pw.regained(); assert.ok(!shown);
  suspended = true; pw.lost(); timers.shift().fn(); assert.ok(!shown);

  // Prompt gate: a station prompt arriving while the flag flow is open does not draw;
  // the latest one is handed back on close. The flow itself draws at once.
  const drawn = [];
  const gate = createPromptGate();
  gate.open();
  assert.strictEqual(gate.draw(() => drawn.push('count prompt')), false);
  assert.strictEqual(gate.draw(() => drawn.push('later prompt')), false);
  assert.strictEqual(gate.draw(() => drawn.push('flag note'), true), true);
  assert.deepStrictEqual(drawn, ['flag note']);
  gate.close()();
  assert.deepStrictEqual(drawn, ['flag note', 'later prompt']);
  assert.strictEqual(gate.close(), null);
  assert.strictEqual(gate.draw(() => drawn.push('after')), true);
  assert.strictEqual(drawn[drawn.length - 1], 'after');

  // A scan sounds ok only when it moved the flow from a scan step to a pick step.
  assert.strictEqual(flagScanSound('scan', 'reason'), 'ok');
  assert.strictEqual(flagScanSound('offer', 'choices'), 'ok');
  assert.strictEqual(flagScanSound('scan', 'scan'), 'error');
  assert.strictEqual(flagScanSound('reason', 'reason'), 'error');
  assert.strictEqual(flagScanSound('choices', 'choices'), 'error');

  // Response handling.
  assert.deepStrictEqual(interpretResponse(200, false, {ok: true}), {type: 'answer', data: {ok: true}});
  assert.deepStrictEqual(interpretResponse(500, false, undefined), {type: 'retry'});
  assert.deepStrictEqual(interpretResponse(403, false, {enrolled: false, message: 'x'}),
    {type: 'fatal', message: 'This scanner is not enrolled.'});
  assert.deepStrictEqual(interpretResponse(403, false, {enrolled: true, message: 'Prints only.'}),
    {type: 'fatal', message: 'Prints only.'});
  assert.strictEqual(interpretResponse(200, true, undefined).type, 'fatal');
  assert.strictEqual(interpretResponse(200, false, undefined).type, 'retry');
  assert.strictEqual(interpretResponse(200, false, {ok: false, need_name: true}).data.need_name, true);
  console.log('capture ok');
})().catch(e => { console.error(e); process.exit(1); });

"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_pure_scan_logic_under_node(tmp_path):
    """Runs the pure capture and repeat logic under node; it does not drive a browser."""
    script = tmp_path / "capture_check.js"
    script.write_text(CAPTURE_SCRIPT)
    path = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static",
                        "inventory", "scan.js")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                            env={**os.environ, "SCAN_JS": os.path.abspath(path)})
    assert result.returncode == 0, result.stderr
    assert "capture ok" in result.stdout


def test_look_up_card_shows_name_fields_and_notes(app, inv):
    from app.inventory.unit_fields import set_unit_value
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("TV", "TV", [
        ("SIZE", "Size", "NUMBER", "in"),
        ("RESOLUTION", "Resolution", "CHOICE", ["720p", "4k"]),
        ("HDMI", "HDMI", "BOOL", None)])
    inv.laptop.gear_kind_id = kind.id
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    unit.name, unit.name_key = "MAG-TV-107", "mag-tv-107"
    add_note(unit, "ADMIN", "no power cable", "test:invadmin")
    set_unit_value(unit, kind.fields[0], "50")
    set_unit_value(unit, kind.fields[1], "4k")
    db.session.commit()
    card = card_for(resolve("S1"), None)
    assert card["name"] == "MAG-TV-107"
    assert card["fields"] == [["Size", "50 in"], ["Resolution", "4k"]]
    assert card["notes"] == "no power cable"
    plain = add_unit(inv.wap, None, ACTOR, [("SERIAL", "S2")])
    db.session.commit()
    card = card_for(resolve("S2"), None)
    assert card["name"] is None and card["fields"] == [] and card["notes"] is None


def test_the_tag_and_check_pages_are_gone(client, inv):
    login(client, "test:operator")
    assert client.get("/inv/scan/tag").status_code == 404
    assert client.get("/inv/scan/check").status_code == 404
    assert client.post("/inv/scan/api/check/open", json={}).status_code == 404
    assert client.post("/inv/scan/api/tag/link", json={}).status_code == 404


def _named(inv, name, identifiers=()):
    unit = add_unit(inv.laptop, None, ACTOR, list(identifiers), name=name)
    db.session.commit()
    return unit


def test_look_up_finds_one_unit_by_part_of_its_name(client, inv):
    unit = _named(inv, "MAG-PNTR-007")
    enrolled_client(client)
    r = client.post("/inv/scan/api/resolve", json={"code": "pntr-007"}).get_json()
    assert r["ok"] is True and r["kind"] == "UNIT" and r["unit_id"] == unit.id
    assert r["card"]["name"] == "MAG-PNTR-007"


def test_look_up_offers_a_choice_when_several_names_match(client, inv):
    units = [_named(inv, f"MAG-PNTR-00{i}") for i in range(1, 4)]
    _named(inv, "MAG-TV-107")
    enrolled_client(client)
    r = client.post("/inv/scan/api/resolve", json={"code": "PNTR"}).get_json()
    assert r["kind"] == "AMBIGUOUS"
    assert [c["unit_id"] for c in r["candidates"]] == [u.id for u in units]
    assert [c["card"]["name"] for c in r["candidates"]] == [u.name for u in units]


def test_a_code_still_wins_over_a_name(client, inv):
    _named(inv, None, [("MAGFEST_TAG", "TECH412")])
    _named(inv, "TECH412")
    enrolled_client(client)
    r = client.post("/inv/scan/api/resolve", json={"code": "TECH412"}).get_json()
    assert r["kind"] == "UNIT" and r["card"]["tag"] == "TECH412"
    assert "unit_id" not in r


def test_a_retired_name_and_no_match_stay_unknown(client, inv):
    unit = _named(inv, "MAG-PNTR-007")
    set_condition(unit, "RETIRED")
    db.session.commit()
    enrolled_client(client)
    r = client.post("/inv/scan/api/resolve", json={"code": "PNTR-007"}).get_json()
    assert r["kind"] == "UNKNOWN"


def test_label_options_accept_a_unit_found_by_name(client, inv):
    unit = _named(inv, "MAG-PNTR-007")
    enrolled_client(client)
    r = client.post("/inv/scan/api/label/options",
                    json={"code": "PNTR-007", "unit_id": unit.id}).get_json()
    assert r["ok"] is True and r["kind"] == "UNIT", r


LOOKUP_SCRIPT = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const {flagLookup} = require(process.env.SCAN_JS);
const tick = () => new Promise(r => setImmediate(r));

// A name hit flags by unit id; a code hit keeps flagging by code.
assert.deepStrictEqual(flagLookup({ok: true, kind: 'UNIT', code: 'PNTR', unit_id: 7,
  card: {title: 'Printer', tag: ''}}), {subject: {unitId: 7, label: 'Printer'}});
assert.deepStrictEqual(flagLookup({ok: true, kind: 'UNIT', code: 'TECH412',
  card: {title: 'Laptop', tag: 'TECH412'}}), {subject: {code: 'TECH412', label: 'Laptop TECH412'}});
assert.strictEqual(flagLookup({ok: true, kind: 'UNKNOWN', code: 'X'}), null);

let flagged = null, printed = null, opts = null, answer = null;
const buttons = [];
const S = {esc: s => String(s), showMessage() {}, showPanel() {}, feedback() {},
  choose() {}, messages: {}, setFlagSubject: s => { flagged = s; },
  start: o => { opts = o; }, read: () => Promise.resolve(answer)};
const L = {reset() {}, newTag() {}, show: t => { printed = t; }};
const document = {currentScript: {dataset: {resolveUrl: '/r'}},
  getElementById: () => ({appendChild: b => buttons.push(b)}),
  createElement: () => ({handlers: {}, addEventListener(e, f) { this.handlers[e] = f; }})};
vm.runInNewContext(fs.readFileSync(process.env.LOOKUP_JS, 'utf8'),
  {window: {InvScan: S, InvLabel: L}, document});

(async () => {
  answer = {ok: true, kind: 'UNIT', code: 'PNTR', unit_id: 7,
    card: {title: 'Printer', name: 'MAG-PNTR-007', tag: '', lines: [], history: []}};
  assert.strictEqual((await opts.onScan('pntr')).ok, true);
  await tick();
  assert.strictEqual(flagged.unitId, 7);
  buttons[buttons.length - 1].handlers.click();
  assert.strictEqual(printed.unitId, 7);
  answer = {ok: true, kind: 'UNIT', code: 'TECH412',
    card: {title: 'Laptop', tag: 'TECH412', lines: [], history: []}};
  await opts.onScan('TECH412');
  assert.strictEqual(flagged.unitId, undefined);
  assert.strictEqual(flagged.code, 'TECH412');
  buttons[buttons.length - 1].handlers.click();
  assert.strictEqual(printed.unitId, null);
  console.log('lookup ok');
})().catch(e => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_look_up_name_hit_prints_and_flags_by_unit_id_under_node(tmp_path):
    """Runs station_lookup.js on a stub page under node; it does not drive a browser."""
    script = tmp_path / "lookup_check.js"
    script.write_text(LOOKUP_SCRIPT)
    static = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True,
        env={**os.environ, "SCAN_JS": os.path.abspath(os.path.join(static, "scan.js")),
             "LOOKUP_JS": os.path.abspath(os.path.join(static, "station_lookup.js"))})
    assert result.returncode == 0, result.stderr
    assert "lookup ok" in result.stdout


def test_look_up_card_for_a_unit_with_no_number_says_so(inv):
    from app.inventory.ledger import Actor, add_unit
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [], name="Lobby Laptop")
    db.session.commit()
    from app.inventory.resolver import KIND_UNIT, Resolution
    card = card_for(Resolution(kind=KIND_UNIT, code="", unit=unit), None)
    assert card["name"] == "Lobby Laptop"
    assert not card["tag"] and card["numberless"] is True
    assert f"unit {unit.id}" not in str(card)


def test_unit_device_opens_the_unit_station(client, inv):
    from app.inventory.devices import create_enroll_code
    from app.models import InvDevice
    _event()
    home = make_container(inv, "C-HOME", kind="LOCATION", name="Warehouse")
    raw = create_enroll_code("Unit 1", "test:invadmin", station_code="UNIT", home_container=home)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    assert client.post("/inv/scan/enroll", data={"code": raw}).status_code == 302
    assert "/scan/who" in client.get("/inv/scan/").headers["Location"]
    start_shift(InvDevice.query.filter_by(name="Unit 1").one(), "Sam")
    db.session.commit()
    page = client.get("/inv/scan/", follow_redirects=True)
    assert b"station_unit.js" in page.data and b'data-station="UNIT"' in page.data
    assert b"station_check.js" not in page.data


def test_unit_station_page_stops_when_the_device_has_no_home(client, inv):
    from app.models import InvDevice
    from app.inventory.devices import create_enroll_code
    _event()
    home = make_container(inv, "C-HOME", kind="LOCATION", name="Warehouse")
    raw = create_enroll_code("Unit 2", "test:invadmin", station_code="UNIT", home_container=home)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    client.post("/inv/scan/enroll", data={"code": raw})
    device = InvDevice.query.filter_by(name="Unit 2").one()
    start_shift(device, "Sam")
    device.home_container_id = None
    db.session.commit()
    page = client.get("/inv/scan/unit")
    assert b"This scanner has no home container. Ask an inventory admin." in page.data
    assert b"station_unit.js" not in page.data
