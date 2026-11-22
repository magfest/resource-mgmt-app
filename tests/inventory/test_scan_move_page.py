"""Move scanner screen: the page route and the station script on a stub DOM under node."""
import os
import shutil
import subprocess

import pytest

from app import db
from app.inventory.shifts import start_shift
from app.models import InvDevice
from tests.inventory.helpers import enrolled_client

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")


def test_the_page_needs_the_move_station_and_a_shift(client, inv):
    enrolled_client(client, station_code="MOVE")
    assert "/scan/who" in client.get("/inv/scan/move").headers["Location"]
    device = InvDevice.query.one()
    start_shift(device, "test:operator")
    db.session.commit()
    assert client.get("/inv/scan/").headers["Location"].endswith("/inv/scan/move")
    page = client.get("/inv/scan/move")
    assert page.status_code == 200 and b"station_move.js" in page.data
    assert b"nonce=" in page.data
    for name in (b"data-scan-url", b"data-create-url", b"data-source-url", b"data-find-url",
                 b"data-browse-url", b"data-supplies-url"):
        assert name in page.data


def test_another_station_is_sent_away(client, inv):
    enrolled_client(client, station_code="COUNT")
    device = InvDevice.query.one()
    start_shift(device, "test:operator")
    db.session.commit()
    response = client.get("/inv/scan/move")
    assert response.status_code == 302 and response.headers["Location"].endswith("/inv/scan/")


def test_a_signed_in_operator_may_open_the_page(client, inv):
    from tests.inventory.helpers import login
    login(client, "test:operator")
    assert client.get("/inv/scan/move").status_code == 200


PRELUDE = r"""
const assert = require('assert');
function Node(tag) {
  this.tag = tag; this.children = []; this.className = ''; this.style = {};
  this.handlers = {}; this._text = ''; this.disabled = false;
}
Node.prototype = {
  get textContent() { return this._text; },
  set textContent(v) { this._text = v; if (v === '') { this.children = []; } },
  appendChild(c) { this.children.push(c); return c; },
  insertBefore(c) { this.children.unshift(c); return c; },
  get firstChild() { return this.children[0] || null; },
  addEventListener(t, f) { this.handlers[t] = f; },
  setAttribute() {}, focus() {}
};
function all(n, out) { out.push(n); n.children.forEach((c) => all(c, out)); return out; }
function texts(n) { return all(n, []).map((x) => x._text); }
function find(n, label) { return all(n, []).find((x) => x.tag === 'button' && x._text === label); }
const panel = new Node('div');
globalThis.document = {
  currentScript: {dataset: {scanUrl: '/scan', createUrl: '/create', sourceUrl: '/source',
                            findUrl: '/find', browseUrl: '/browse', suppliesUrl: '/supplies'}},
  getElementById: (id) => id === 'panel' ? panel : new Node('input'),
  createElement: (t) => new Node(t)
};
const writes = [];
const reads = [];
const messages = [];
const subjects = [];
let onScan;
let padCb = null;
let padOpts = null;
let chosen = null;
globalThis.InvScan = {
  start: (cfg) => { onScan = cfg.onScan; },
  holdLeave: () => {}, onActivity: () => {}, offerUndo: () => {},
  setFlagSubject: (s) => subjects.push(s),
  requestId: (() => { let n = 0; return () => 'rid-' + (++n); })(),
  feedback() {}, showMessage: (t) => messages.push(t),
  numberPad: (opts, cb) => { padOpts = opts; padCb = cb; },
  askText: (l, cb) => cb(null),
  choose: (opts, pick, note) => { chosen = {opts, pick, note}; panel.textContent = ''; },
  read: (path, body) => new Promise((resolve) => reads.push({path, body, resolve})),
  write: (path, body, rid) => new Promise((resolve) => writes.push({path, body, rid, resolve}))
};
require(process.env.MOVE_JS);
const tick = () => new Promise((r) => setTimeout(r, 0));
"""

FLOW = PRELUDE + r"""
(async () => {
  const dest = {ok: true, destination: {code: 'C-0040', title: 'Pallet C-0040'},
    message: 'Going to Pallet C-0040. Scan each thing as you put it there.'};
  assert.ok(texts(panel).includes("Scan where it's going: a pallet, a tote or a location."));

  let p = onScan('C-0040');
  await tick();
  assert.equal(writes[0].path, '/scan');
  assert.deepEqual(writes[0].body, {code: 'C-0040', destination_code: null});
  writes[0].resolve(dest);
  assert.deepEqual(await p, {ok: true});
  assert.ok(texts(panel).includes('Pallet C-0040'));
  assert.ok(find(panel, 'Move supplies') && find(panel, 'New destination'));
  assert.equal(find(panel, 'New destination').className, 'cnt-submit');
  assert.equal(find(panel, 'Move supplies').className, 'cnt-small');
  assert.deepEqual(subjects[subjects.length - 1], {code: 'C-0040', label: 'Pallet C-0040'});

  p = onScan('C-0042');
  await tick();
  assert.equal(writes[1].body.destination_code, 'C-0040');
  writes[1].resolve({ok: true, moved: true, message: 'C-0042 is on C-0040.'});
  assert.deepEqual(await p, {ok: true});
  assert.equal(messages[messages.length - 1], 'C-0042 is on C-0040.');
  assert.ok(texts(panel).includes('Pallet C-0040'));

  p = onScan('C-0042');
  await tick();
  writes[2].resolve({ok: true, moved: false, message: 'C-0042 is on C-0040 already.'});
  assert.deepEqual(await p, {ok: false});

  p = onScan('C-0050');
  await tick();
  writes[3].resolve({ok: true, unused: true, code: 'C-0050', kinds: [{id: 2, name: 'Pallet'}],
    where: 'It goes on C-0040.'});
  await p;
  assert.deepEqual(chosen.opts.map((o) => o.label), ['Pallet']);
  assert.equal(chosen.note, 'C-0050 is a new label. Pick what it is. It goes on C-0040.');
  assert.ok(find(panel, 'Cancel'));
  chosen.pick(chosen.opts[0].value);
  await tick();
  assert.equal(writes[4].path, '/create');
  assert.deepEqual(writes[4].body, {label_code: 'C-0050', kind_id: 2, destination_code: 'C-0040'});
  writes[4].resolve({ok: true, moved: true, message: 'C-0050 is on C-0040.'});
  await tick();

  find(panel, 'Move supplies').handlers.click();
  assert.ok(texts(panel).includes('Scan where they are coming from.'));
  p = onScan('C-0041');
  await tick();
  assert.equal(reads[0].path, '/source');
  assert.deepEqual(reads[0].body, {code: 'C-0041', destination_code: 'C-0040'});
  reads[0].resolve({ok: true, source: {code: 'C-0041', title: 'Pallet C-0041'},
    usual: [{item_id: 1, name: 'Pens', pack_size: 12, prompt: 'How many Pens?'}]});
  await p;
  assert.ok(texts(panel).includes('From C-0041 to C-0040'));

  const nReads = reads.length, nWrites = writes.length;
  p = onScan('C-0099');
  assert.deepEqual(await p, {ok: false});
  assert.equal(reads.length, nReads);
  assert.equal(writes.length, nWrites);
  assert.equal(messages[messages.length - 1], 'Tap the item, or scan its barcode.');

  find(panel, 'Pens').handlers.click();
  assert.equal(padOpts.packSize, 12);
  assert.equal(padOpts.allowAbout, false);
  padCb({quantity: 24, entered: 2, packs: true, about: false});
  await tick();
  assert.equal(writes[nWrites].path, '/supplies');
  assert.deepEqual(writes[nWrites].body,
    {source_code: 'C-0041', destination_code: 'C-0040', item_id: 1, quantity: 24});
  writes[nWrites].resolve({ok: true, message: 'Moved 24 Pens from C-0041 to C-0040.', short: false});
  await tick();
  assert.equal(messages[messages.length - 1], 'Moved 24 Pens from C-0041 to C-0040.');
  assert.ok(find(panel, 'New destination'));
  assert.ok(texts(panel).includes('Pallet C-0040'));

  find(panel, 'New destination').handlers.click();
  assert.ok(texts(panel).includes("Scan where it's going: a pallet, a tote or a location."));
  p = onScan('C-0041');
  await tick();
  assert.equal(writes[writes.length - 1].body.destination_code, null);
  console.log('move ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_move_flow_under_node(tmp_path):
    """Drives the page script on a stub DOM under node; no browser is involved."""
    path = tmp_path / "move_flow.js"
    path.write_text(FLOW)
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "MOVE_JS": os.path.abspath(os.path.join(STATIC, "station_move.js"))})
    assert result.returncode == 0, result.stderr
    assert "move ok" in result.stdout


REFUSED = PRELUDE + r"""
(async () => {
  let p = onScan('C-0040');
  await tick();
  writes[0].resolve({ok: true, destination: {code: 'C-0040', title: 'Pallet C-0040'},
    message: 'Going to Pallet C-0040.'});
  await p;
  find(panel, 'Move supplies').handlers.click();
  p = onScan('C-0041');
  await tick();
  reads[0].resolve({ok: true, source: {code: 'C-0041', title: 'Pallet C-0041'},
    usual: [{item_id: 1, name: 'Pens', pack_size: 12, prompt: 'How many Pens?'}]});
  await p;

  find(panel, 'Pens').handlers.click();
  padCb({quantity: 999999, entered: 999999, packs: false, about: false});
  await tick();
  assert.equal(writes[1].path, '/supplies');
  writes[1].resolve({ok: false, message: 'That number is too large.'});
  await tick();
  assert.equal(messages[messages.length - 1], 'That number is too large.');
  assert.ok(texts(panel).includes('From C-0041 to C-0040'), 'still on the item step');
  assert.ok(find(panel, 'Pens') && find(panel, 'Browse') && find(panel, 'Search'));

  find(panel, 'Pens').handlers.click();
  padCb({quantity: 24, entered: 2, packs: true, about: false});
  await tick();
  assert.equal(writes[2].path, '/supplies');
  assert.equal(writes[2].body.source_code, 'C-0041');
  console.log('refused ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


def _node(tmp_path, script, name):
    path = tmp_path / name
    path.write_text(script)
    return subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "MOVE_JS": os.path.abspath(os.path.join(STATIC, "station_move.js"))})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_refused_supplies_move_keeps_the_item_step(tmp_path):
    result = _node(tmp_path, REFUSED, "move_refused.js")
    assert result.returncode == 0, result.stderr
    assert "refused ok" in result.stdout


STALE = PRELUDE + r"""
(async () => {
  let p = onScan('C-0040');
  await tick();
  writes[0].resolve({ok: true, destination: {code: 'C-0040', title: 'Pallet C-0040'},
    message: 'Going to Pallet C-0040.'});
  await p;
  p = onScan('C-0041');
  await tick();
  assert.equal(writes[1].body.destination_code, 'C-0040');
  writes[1].resolve({ok: false, message: "Scan where it's going again."});
  await p;
  assert.equal(messages[messages.length - 1], "Scan where it's going again.");
  assert.ok(texts(panel).includes("Scan where it's going: a pallet, a tote or a location."));
  assert.ok(!find(panel, 'New destination'));
  p = onScan('C-0050');
  await tick();
  assert.equal(writes[2].body.destination_code, null);
  console.log('stale ok');
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_dead_destination_is_cleared_so_a_new_one_can_be_scanned(tmp_path):
    result = _node(tmp_path, STALE, "move_stale.js")
    assert result.returncode == 0, result.stderr
    assert "stale ok" in result.stdout
