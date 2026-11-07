"""Flags raised at a scanner: the model functions, the API, and the flow logic under node."""
import json
import os
import shutil
import subprocess

import pytest

from app import db
from app.inventory import labels
from app.inventory.cards import card_for, default_event
from app.inventory.flags import FLAG_WORDS, raise_flag, resolve_flag
from app.inventory.ledger import Actor, InventoryError, add_unit, set_condition
from app.inventory.resolver import resolve
from app.inventory.shifts import start_shift
from app.models import EventCycle, InvDevice, InvFlag, InvScanRequest
from app.models.constants import INV_CONDITION_RETIRED, INV_FLAG_REASONS
from tests.inventory.helpers import enrolled_client, login, make_container

ACTOR = Actor(user_id="test:invadmin")
FLAG = "/inv/scan/api/flag"
_n = [0]


def _rid():
    _n[0] += 1
    return f"flag-{_n[0]}"


def _post(client, **body):
    body.setdefault("client_request_id", _rid())
    return client.post(FLAG, json=body).get_json()


@pytest.fixture
def device(client, inv):
    dev = enrolled_client(client, station_code="COUNT")
    start_shift(dev, "Sam")
    db.session.commit()
    return dev


def test_station_flag_reaches_look_up_and_a_lead_clears_it(client, inv, device):
    db.session.add(EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True))
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "TECH412")])
    db.session.commit()
    answer = _post(client, code="tech412", reason="WRONG_DATA")
    assert answer["ok"] is True
    flag = InvFlag.query.one()
    assert (flag.station_code, flag.reason) == ("COUNT", "WRONG_DATA")
    assert flag.shift_id is not None and flag.device_id == device.id
    card = card_for(resolve("TECH412"), default_event())
    assert "Flag: Wrong details" in card["lines"]
    resolve_flag(flag, "test:invadmin", "Serial corrected")
    db.session.commit()
    card = card_for(resolve("TECH412"), default_event())
    assert not any(line.startswith("Flag:") for line in card["lines"])
    assert (flag.resolved_by_user_id, flag.resolution_note) == ("test:invadmin", "Serial corrected")
    with pytest.raises(InventoryError):
        resolve_flag(flag, "test:invadmin", "again")


def test_flag_needs_exactly_one_subject_and_a_known_reason(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    tote = make_container(inv, "C-0001", kind="BIN")
    with pytest.raises(InventoryError):
        raise_flag(ACTOR, "LOOKUP", "DAMAGED")
    with pytest.raises(InventoryError):
        raise_flag(ACTOR, "LOOKUP", "DAMAGED", unit=unit, container=tote)
    with pytest.raises(InventoryError):
        raise_flag(ACTOR, "LOOKUP", "BROKEN", unit=unit)
    with pytest.raises(InventoryError):
        raise_flag(ACTOR, "LOOKUP", "OTHER", unit=unit)
    assert InvFlag.query.count() == 0
    assert raise_flag(ACTOR, "LOOKUP", "DAMAGED", container=tote).container_id == tote.id


def test_reason_words_cover_every_reason_and_match_look_up():
    assert set(FLAG_WORDS) == set(INV_FLAG_REASONS)
    assert FLAG_WORDS["UNKNOWN_ITEM"] == "Item not in the system"


def test_an_item_an_unused_label_and_an_unknown_code_point_to_the_container(client, inv, device):
    unused = labels.allocate(labels.PREFIX_UNIT, 1)[0]
    inv.pens.barcode = "PENBAR"
    db.session.commit()
    for code in ("PENBAR", unused, "nothing-here"):
        answer = _post(client, code=code, reason="UNKNOWN_ITEM")
        assert answer["ok"] is False
        assert answer["message"] == "Flag the container it is in instead. Scan the container label."
    assert InvFlag.query.count() == 0


def test_a_retired_unit_and_an_inactive_container_can_be_flagged(client, inv, device):
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "OLD1")])
    unit_id = unit.id
    set_condition(unit, INV_CONDITION_RETIRED)
    tote = make_container(inv, "C-0001", kind="BIN")
    tote.is_active = False
    db.session.commit()
    # Retiring a unit retires its identifiers, so only its id still names it.
    assert _post(client, unit_id=unit_id, reason="DAMAGED")["ok"] is True
    assert _post(client, code="C-0001", reason="DAMAGED")["ok"] is True
    assert InvFlag.query.count() == 2


def test_two_units_sharing_a_code_need_a_unit_id(client, inv, device):
    a = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "DUP")])
    b = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "DUP")])
    b_id = b.id
    db.session.commit()
    assert _post(client, code="DUP", reason="DAMAGED")["ok"] is False
    assert _post(client, unit_id=b_id, reason="DAMAGED")["ok"] is True
    assert InvFlag.query.one().unit_id == b_id and a.id != b_id


def test_a_second_open_flag_on_the_same_subject_and_reason_is_refused(client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert _post(client, code="S1", reason="DAMAGED")["ok"] is True
    again = _post(client, code="S1", reason="DAMAGED")
    assert again == {"ok": False, "message": "That is already flagged."}
    assert _post(client, code="S1", reason="WRONG_DATA")["ok"] is True
    resolve_flag(InvFlag.query.filter_by(reason="DAMAGED").one(), "test:invadmin", "Fixed")
    db.session.commit()
    assert _post(client, code="S1", reason="DAMAGED")["ok"] is True


def test_other_needs_a_note_and_a_note_is_capped_and_normalized(client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert _post(client, code="S1", reason="OTHER")["ok"] is False
    assert _post(client, code="S1", reason="OTHER", note="  \r\n ")["ok"] is False
    long_note = "a\r\nb\rc" + "x" * 400
    assert _post(client, code="S1", reason="OTHER", note=long_note)["ok"] is True
    note = InvFlag.query.one().note
    assert note.startswith("a\nb\nc") and "\r" not in note and len(note) == 256


def test_a_bad_reason_or_a_non_text_note_is_refused(client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert _post(client, code="S1", reason="BROKEN")["ok"] is False
    assert _post(client, code="S1", reason=["DAMAGED"])["ok"] is False
    assert _post(client, code="S1", reason="OTHER", note=5)["ok"] is False
    assert _post(client, code="S1", reason="DAMAGED", unit_id="3")["ok"] is False
    assert InvFlag.query.count() == 0


def test_a_repeat_request_id_writes_once(client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    first = _post(client, client_request_id="same", code="S1", reason="DAMAGED")
    second = _post(client, client_request_id="same", code="S1", reason="DAMAGED")
    assert first == second and first["ok"] is True
    assert InvFlag.query.count() == 1
    assert InvScanRequest.query.filter_by(client_request_id="same").count() == 1


def test_a_device_without_a_shift_is_told_to_pick_a_name(client, inv):
    dev = enrolled_client(client, station_code="RECEIVE")
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert dev.station_code == "RECEIVE"
    answer = _post(client, code="S1", reason="DAMAGED")
    assert answer["ok"] is False and answer["need_name"] is True
    assert InvFlag.query.count() == 0


def test_the_station_comes_from_the_device_never_the_client(client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    _post(client, code="S1", reason="DAMAGED", station_code="PRINT")
    assert InvFlag.query.one().station_code == "COUNT"


def test_a_signed_in_operator_flags_from_look_up(client, inv):
    login(client, "test:operator")
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert _post(client, code="S1", reason="DAMAGED")["ok"] is True
    flag = InvFlag.query.one()
    assert flag.station_code == "LOOKUP" and flag.performed_by_user_id == "test:operator"
    assert flag.shift_id is None and flag.device_id is None


def test_a_print_device_a_stranger_and_non_json_are_refused(client, inv):
    assert client.post(FLAG, json={}).status_code == 403
    enrolled_client(client, station_code="PRINT")
    r = client.post(FLAG, json={"client_request_id": "x", "code": "S1", "reason": "DAMAGED"})
    assert r.status_code == 403 and r.get_json()["ok"] is False


def test_flag_needs_json_and_skips_csrf(app, client, inv, device):
    add_unit(inv.laptop, None, ACTOR, [("SERIAL", "S1")])
    db.session.commit()
    assert client.post(FLAG, data={"code": "S1"}).status_code == 415
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        r = client.post(FLAG, json={"client_request_id": "c1", "code": "S1", "reason": "DAMAGED"})
    finally:
        app.config["WTF_CSRF_ENABLED"] = False
    assert r.status_code == 200 and r.get_json()["ok"] is True


# ---- flow logic and page wiring, run under node; no browser is driven ----

STATIC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app", "static",
                                      "inventory"))

FLOW_SCRIPT = r"""
const assert = require('assert');
const F = require(process.env.FLAG_JS);
const log = [];
function make(opts) {
  const views = [];
  const flow = F.createFlagFlow(Object.assign({
    mint: (() => { let n = 0; return () => 'id' + (++n); })(),
    lookup: (code) => Promise.resolve({subject: {code: code, label: 'Thing ' + code}}),
    save: (payload, id) => { log.push([payload, id]); return Promise.resolve({ok: true}); },
    show: (v) => views.push(v),
    done: (r) => views.push({done: r})
  }, opts || {}));
  return {flow, views, last: () => views[views.length - 1]};
}
(async () => {
  // A reason list in the agreed order and words.
  assert.deepStrictEqual(F.REASONS.map(r => r.code), ['WRONG_DATA', 'DAMAGED', 'UNKNOWN_ITEM', 'OTHER']);
  assert.deepStrictEqual(F.REASONS.map(r => r.label),
    ['Wrong details', 'Damaged', 'Item not in the system', 'Other']);

  // With a subject the flow offers it; with none it asks for a scan.
  let t = make();
  t.flow.open({code: 'C-1', label: 'C-0001'});
  assert.strictEqual(t.last().step, 'offer');
  assert.strictEqual(t.last().label, 'C-0001');
  t.flow.cancel();
  assert.strictEqual(t.flow.isOpen(), false);
  t = make();
  t.flow.open(null);
  assert.strictEqual(t.last().step, 'scan');
  assert.strictEqual(t.last().message, 'Scan what you are flagging.');

  // A scan while the flow is open is taken by the flow and settles with ok false.
  let reached = 0;
  const answer = await t.flow.scan('S1');
  assert.deepStrictEqual(answer, {ok: false});
  assert.strictEqual(t.last().step, 'reason');
  assert.strictEqual(t.last().label, 'Thing S1');
  assert.strictEqual(reached, 0);

  // A scan that arrives on the reason screen changes nothing and still settles.
  const stray = await t.flow.scan('S2');
  assert.deepStrictEqual(stray, {ok: false});
  assert.strictEqual(t.last().step, 'reason');
  assert.strictEqual(t.last().label, 'Thing S1');

  // A scan during a lookup is ignored; the first lookup wins.
  let release;
  const slow = make({lookup: (code) => new Promise(r => { release = () => r({subject: {code, label: code}}); })});
  slow.flow.open(null);
  const first = slow.flow.scan('A');
  const second = await slow.flow.scan('B');
  assert.deepStrictEqual(second, {ok: false});
  release();
  await first;
  assert.strictEqual(slow.last().label, 'A');

  // Cancel during a lookup drops the late answer.
  const late = make({lookup: (code) => new Promise(r => { release = () => r({subject: {code, label: code}}); })});
  late.flow.open(null);
  const pending = late.flow.scan('A');
  late.flow.cancel();
  release();
  await pending;
  assert.strictEqual(late.flow.isOpen(), false);
  assert.strictEqual(late.last().done.saved, false);

  // A refused lookup keeps the scan screen open with the reason.
  const refused = make({lookup: () => Promise.resolve({message: 'Flag the container it is in instead.'})});
  refused.flow.open(null);
  await refused.flow.scan('X');
  assert.strictEqual(refused.last().step, 'scan');
  assert.strictEqual(refused.last().message, 'Flag the container it is in instead.');

  // Scan something else from the offer screen takes the next scan as the subject.
  const swap = make();
  swap.flow.open({code: 'C-1', label: 'C-0001'});
  swap.flow.scanElse();
  assert.strictEqual(swap.last().step, 'scan');
  await swap.flow.scan('S9');
  assert.strictEqual(swap.last().label, 'Thing S9');

  // A scan on the offer screen picks the scanned thing instead of the offered one.
  const offered = make();
  offered.flow.open({code: 'C-1', label: 'C-0001'});
  await offered.flow.scan('S8');
  assert.strictEqual(offered.last().label, 'Thing S8');

  // Three reasons save at once with no note; the save reuses one id per subject and reason.
  const save = make();
  save.flow.open({code: 'C-1', label: 'C-0001'});
  save.flow.useSubject();
  assert.strictEqual(save.last().step, 'reason');
  await save.flow.pickReason('DAMAGED');
  assert.deepStrictEqual(save.last().done, {saved: true, text: 'Flagged for a lead. Damaged.'});
  assert.strictEqual(log[0][0].reason, 'DAMAGED');
  assert.strictEqual(log[0][0].note, undefined);
  assert.strictEqual(save.flow.isOpen(), false);

  // A save that fails stays on the reason screen; a retry of the same reason reuses its id.
  const ids = [];
  let ok = false;
  const retry = make({save: (p, id) => { ids.push(id); return Promise.resolve(ok ? {ok: true} : {ok: false, message: 'That is already flagged.'}); }});
  retry.flow.open({code: 'C-1', label: 'C-0001'});
  retry.flow.useSubject();
  await retry.flow.pickReason('DAMAGED');
  assert.strictEqual(retry.last().step, 'reason');
  assert.strictEqual(retry.last().message, 'That is already flagged.');
  ok = true;
  await retry.flow.pickReason('DAMAGED');
  assert.strictEqual(ids[0], ids[1]);
  assert.strictEqual(retry.flow.isOpen(), false);

  // Other asks for a note, requires it, and cancel at the note returns to the reasons.
  const other = make();
  other.flow.open({code: 'C-1', label: 'C-0001'});
  other.flow.useSubject();
  await other.flow.pickReason('OTHER');
  assert.strictEqual(other.last().step, 'note');
  await other.flow.submitNote('');
  assert.strictEqual(other.last().step, 'note');
  assert.strictEqual(other.last().message, 'Say what is wrong.');
  await other.flow.submitNote(null);
  assert.strictEqual(other.last().step, 'reason');
  await other.flow.pickReason('OTHER');
  await other.flow.submitNote('Cracked lid');
  assert.strictEqual(log[log.length - 1][0].note, 'Cracked lid');
  assert.strictEqual(other.flow.isOpen(), false);

  // A unit id travels instead of a code.
  const unit = make();
  unit.flow.open({unitId: 7, label: 'Laptop'});
  unit.flow.useSubject();
  await unit.flow.pickReason('WRONG_DATA');
  assert.deepStrictEqual(log[log.length - 1][0], {unit_id: 7, reason: 'WRONG_DATA'});

  // A need-name or fatal answer closes the flow without a message of its own.
  const gone = make({save: () => Promise.resolve({ok: false, need_name: true})});
  gone.flow.open({code: 'C-1', label: 'C-0001'});
  gone.flow.useSubject();
  await gone.flow.pickReason('DAMAGED');
  assert.strictEqual(gone.last().done.saved, false);
  assert.strictEqual(gone.flow.isOpen(), false);

  // A write the volunteer cancelled stays on the reasons with the shell's words.
  const cancelled = make({save: () => Promise.resolve({ok: false, cancelled: true, message: 'Not saved.'})});
  cancelled.flow.open({code: 'C-1', label: 'C-0001'});
  cancelled.flow.useSubject();
  await cancelled.flow.pickReason('DAMAGED');
  assert.strictEqual(cancelled.last().step, 'reason');
  assert.strictEqual(cancelled.last().message, 'Not saved.');

  // Taps while a save is in flight do nothing.
  let finish;
  const busy = make({save: () => new Promise(r => { finish = () => r({ok: true}); })});
  busy.flow.open({code: 'C-1', label: 'C-0001'});
  busy.flow.useSubject();
  const saving = busy.flow.pickReason('DAMAGED');
  const again = await busy.flow.pickReason('DAMAGED');
  assert.strictEqual(again, undefined);
  const during = await busy.flow.scan('S1');
  assert.deepStrictEqual(during, {ok: false});
  finish();
  await saving;
  assert.strictEqual(busy.flow.isOpen(), false);
  console.log('flow ok');
})().catch(e => { console.error(e); process.exit(1); });
"""


def _node(tmp_path, script, name, **env):
    path = tmp_path / name
    path.write_text(script)
    return subprocess.run(["node", str(path)], capture_output=True, text=True,
                          env={**os.environ, **env})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_flag_flow_logic_under_node(tmp_path):
    """Runs the pure flow logic under node; it does not drive a browser."""
    result = _node(tmp_path, FLOW_SCRIPT, "flow_check.js",
                   FLAG_JS=os.path.join(STATIC, "scan_flag.js"))
    assert result.returncode == 0, result.stderr
    assert "flow ok" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_screen_reasons_match_the_server_words(tmp_path):
    script = ("const F = require(process.env.FLAG_JS);"
              "console.log(JSON.stringify(F.REASONS));")
    result = _node(tmp_path, script, "reasons.js", FLAG_JS=os.path.join(STATIC, "scan_flag.js"))
    reasons = json.loads(result.stdout)
    assert {r["code"]: r["label"] for r in reasons} == FLAG_WORDS


def test_every_station_page_has_the_flag_button_and_the_flow_script(client, inv):
    login(client, "test:operator")
    for path in ("/inv/scan/", "/inv/scan/count", "/inv/scan/receive", "/inv/scan/check",
                 "/inv/scan/check?station=PRINTER_CHECK"):
        html = client.get(path).get_data(as_text=True)
        assert 'id="flag-btn"' in html, path
        assert html.index("inventory/scan_flag.js") < html.index("inventory/scan.js"), path


def test_the_flag_button_is_absent_for_a_print_station_and_the_name_page(client, inv):
    enrolled_client(client, station_code="PRINT")
    assert 'id="flag-btn"' not in client.get("/inv/scan/print").get_data(as_text=True)
    assert 'id="flag-btn"' not in client.get("/inv/scan/who").get_data(as_text=True)
    assert 'id="flag-btn"' not in client.get("/inv/scan/enroll").get_data(as_text=True)
