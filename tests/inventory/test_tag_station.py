"""The Tag station page script, run on a stub DOM under node. It does not drive a browser."""
import os
import shutil
import subprocess

import pytest

STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "app", "static", "inventory")

SCRIPT = r"""
const assert = require('assert');
function Node(tag) {
  this.tag = tag; this.children = []; this.className = ''; this.style = {};
  this.handlers = {}; this._text = '';
}
Node.prototype = {
  get textContent() { return this._text; },
  set textContent(v) { this._text = v; if (v === '') { this.children = []; } },
  appendChild(c) { this.children.push(c); return c; },
  addEventListener(t, f) { this.handlers[t] = f; },
  setAttribute() {}, focus() {}
};
const panel = new Node('div');
globalThis.document = {
  currentScript: {dataset: {checkUrl: '/check', candidatesUrl: '/cand', linkUrl: '/link', undoUrl: '/undo'}},
  getElementById: (id) => (id === 'panel' ? panel : new Node('input')),
  createElement: (t) => new Node(t)
};
const reads = [], writes = [], asks = [], messages = [], undos = [], subjects = [];
let onScan, chooser, checkAnswer = {ok: true}, candidates = [], askAnswer = 'lap';
globalThis.InvScan = {
  start: (cfg) => { assert.equal(cfg.station, 'TAG'); onScan = cfg.onScan; },
  setFlagSubject: (s) => subjects.push(s),
  messages: {offline: 'offline'},
  printerWaiting: (a) => a.waiting === 9,
  feedback() {},
  showMessage: (text, kind) => messages.push([text, kind]),
  askText: (label, cb) => { asks.push(label); cb(askAnswer); },
  choose: (options, pick) => { chooser = {options, pick}; },
  offerUndo: (spec) => undos.push(spec),
  read: (path, body) => {
    reads.push([path, body]);
    if (path === '/check') { return Promise.resolve(checkAnswer); }
    return Promise.resolve({ok: true, units: candidates});
  },
  write: (path, body) => { writes.push([path, body]); return Promise.resolve(
    {ok: true, name: 'Lobby TV', code: 'FEST00007', station_seen: true, waiting: 0}); }
};
require(process.env.TAG_JS);
const tick = () => new Promise((r) => setTimeout(r, 0));
const texts = () => { const out = []; (function w(n) { out.push(n._text); n.children.forEach(w); })(panel); return out; };
(async () => {
  checkAnswer = {ok: false, message: 'FEST00001 is already on a unit.'};
  const refused = await onScan('FEST00001');
  assert.deepEqual(refused, {ok: false});
  assert.equal(asks.length, 0, 'a refused check never opens the name prompt');
  assert.deepEqual(messages[messages.length - 1], ['FEST00001 is already on a unit.', 'error']);

  checkAnswer = {ok: true};
  candidates = [];
  assert.deepEqual(await onScan('FEST00002'), {ok: true});
  await tick();
  assert.deepEqual(asks, ['Unit name']);
  assert.deepEqual(reads[reads.length - 1], ['/cand', {q: 'lap'}]);
  assert.deepEqual(messages[messages.length - 1], ['No untagged unit has that name.', 'error']);
  assert.equal(chooser, undefined, 'no match shows no choices');

  candidates = [{id: 5, name: 'Lobby TV', model: 'Sony X', details: '55 in, 4k'}];
  await onScan('FEST00003');
  await tick();
  assert.equal(chooser.options.length, 1, 'one match is still a choice');
  assert.equal(chooser.options[0].label, 'Lobby TV');
  assert.deepEqual(chooser.options[0].lines, ['Lobby TV', 'Sony X', '55 in, 4k']);
  assert.equal(writes.length, 0, 'nothing links before a tap');
  const pick = chooser.pick;
  pick(chooser.options[0].value);
  pick(chooser.options[0].value);
  await tick();
  assert.equal(writes.length, 1, 'a picked unit posts tag/link once');
  assert.equal(writes[0][0], '/link');
  assert.deepEqual(writes[0][1], {unit_id: 5, tag: 'FEST00003'});
  assert.ok(messages.some((m) => m[0] === 'FEST00003 is now Lobby TV'));
  assert.equal(undos.length, 1);
  assert.equal(undos[0].url, '/undo');
  assert.deepEqual(undos[0].payload, {unit_id: 5});
  assert.deepEqual(subjects[subjects.length - 1], {unitId: 5, label: 'FEST00003'});

  await onScan('FEST00004');
  assert.strictEqual(subjects[subjects.length - 1], null, 'a new scan clears the flag subject');
  await tick();
  chooser = null;
  const before = writes.length;
  candidates = [{id: 6, name: 'Hall TV', model: 'LG', details: ''}];
  await onScan('FEST00005');
  await tick();
  const stale = chooser;
  chooser = null;
  await onScan('FEST00006');
  await tick();
  stale.pick(stale.options[0].value);
  await tick();
  assert.equal(writes.length, before, 'a pick from a superseded scan links nothing');
  console.log('tag ok');
})().catch((e) => { console.error(e); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_tag_station_flow_under_node(tmp_path):
    path = tmp_path / "tag_check.js"
    path.write_text(SCRIPT)
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True,
        env={**os.environ, "TAG_JS": os.path.abspath(os.path.join(STATIC, "station_tag.js"))})
    assert result.returncode == 0, result.stderr
    assert "tag ok" in result.stdout
