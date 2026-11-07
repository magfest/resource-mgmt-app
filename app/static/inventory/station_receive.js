// Receive station for owned gear: set up once, then scan each unit and stick on its label.
// The scan order is chosen at setup because tags have no format; the app cannot tell a tag
// from a serial by reading it. All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var MAX_SERIAL = 64;  // the label prints at most 64 characters and must never cut a serial
  var STEPS = {BOTH: ['tag', 'serial'], TAG: ['tag'], SERIAL: ['serial']};
  var STEP_TEXT = {tag: 'Scan the ASSET TAG', serial: 'Scan the SERIAL'};
  var SKIP_TEXT = {tag: 'Skip asset tag', serial: 'Skip serial'};
  var MODE_TEXT = {BOTH: 'Asset tag, then serial', TAG: 'Asset tag only', SERIAL: 'Serial only'};
  var MESSAGES = {
    sameCode: 'That is the same code as the tag. Scan the SERIAL.',
    notStarted: 'Press Start scanning first.',
    needItem: 'Choose an item type first.',
    noItems: 'No serialized item matches that.',
    cancelled: 'Not saved.',
    serialLong: 'That serial is longer than ' + MAX_SERIAL + ' characters. It cannot print whole.'
  };

  function sameCode(a, b) {
    return String(a).trim().toUpperCase() === String(b).trim().toUpperCase();
  }

  // The codes for one unit. scan() and skip() return {kind: 'hold'}, {kind: 'refuse', message},
  // or {kind: 'submit', payload: {tag, serial}}. A skipped step sends no value; the server
  // then makes a U- number for a missing tag.
  function createLoop(mode) {
    var steps = STEPS[mode] || STEPS.BOTH;
    var index = 0;
    var held = {tag: null, serial: null};

    function advance(step, value) {
      held[step] = value;
      index += 1;
      if (index < steps.length) { return {kind: 'hold'}; }
      return {kind: 'submit', payload: {tag: held.tag, serial: held.serial}};
    }
    return {
      step: function () { return steps[index]; },
      prompt: function () { return STEP_TEXT[steps[index]]; },
      skipText: function () { return SKIP_TEXT[steps[index]]; },
      held: function () { return {tag: held.tag, serial: held.serial}; },
      scan: function (code) {
        var step = steps[index];
        if (step === 'serial' && held.tag && sameCode(held.tag, code)) {
          return {kind: 'refuse', message: MESSAGES.sameCode};
        }
        if (step === 'serial' && String(code).trim().length > MAX_SERIAL) {
          return {kind: 'refuse', message: MESSAGES.serialLong};
        }
        return advance(step, code);
      },
      skip: function () { return advance(steps[index], null); },
      reset: function () { index = 0; held = {tag: null, serial: null}; }
    };
  }

  function progress(done, target) {
    if (!target) { return {text: done + ' done', over: false}; }
    return {text: done + ' of ' + target, over: done > target};
  }

  // Runs steps one at a time in call order. A step that starts while another is running waits
  // for it, so a scan during a submit applies to the next unit and is never dropped.
  function createGate() {
    var tail = null;
    return {
      busy: function () { return tail !== null; },
      run: function (fn) {
        var result = (tail || Promise.resolve()).then(fn);
        var mine = result.then(function () {}, function () {});
        tail = mine;
        mine.then(function () { if (tail === mine) { tail = null; } });
        return result;
      }
    };
  }

  var pure = {createLoop: createLoop, createGate: createGate, progress: progress,
              MESSAGES: MESSAGES, MAX_SERIAL: MAX_SERIAL};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');

  var setup = {item: null, mode: 'BOTH', location: null, target: null, bulk: true};
  var running = false;     // the scan loop is showing; false while setup is
  var locating = false;    // setup is waiting for a container scan
  var loop = createLoop(setup.mode);
  var done = 0;
  var last = null;         // {unit_id, code} of the unit received last
  var note = null;         // {code, text, waiting} outcome shown above the buttons
  var already = null;      // the answer's `already` while the Already received screen shows
  var busy = false;        // a Reprint or Undo is in flight
  var gate = createGate(); // scans and Skip run through it, one at a time

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function button(label, className, onClick) {
    var b = el('button', className, label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function refocus() { field.focus(); }

  function progressNode() {
    var p = progress(done, setup.target);
    var node = el('div', 'rcv-progress' + (p.over ? ' over' : ''), p.text);
    if (p.over) { node.appendChild(el('span', 'rcv-flag', ' over the target')); }
    if (!setup.target) { return node; }
    var wrap = el('div');
    var bar = el('div', 'rcv-bar' + (p.over ? ' over' : ''));
    var fill = el('i');
    fill.style.width = Math.min(100, Math.round(done / setup.target * 100)) + '%';
    bar.appendChild(fill);
    wrap.appendChild(node);
    wrap.appendChild(bar);
    return wrap;
  }

  function summary() {
    var parts = [setup.item ? setup.item.name : ''];
    if (setup.location) { parts.push(setup.location.code); }
    return parts.filter(Boolean).join(' · ');
  }

  function noteNode() {
    var box = el('div', 'rcv-note');
    if (!note) { return box; }
    box.appendChild(el('div', 'rcv-note-line', note.text));
    if (note.waiting) {
      box.appendChild(el('div', 'rcv-wait', 'Waiting for printer'));
      if (note.code) {
        box.appendChild(el('div', 'rcv-sub', 'The print station has not picked up ' + note.code));
      }
    }
    return box;
  }

  function footer() {
    var row = el('div', 'rcv-foot');
    var reprint = button('Reprint last', 'rcv-small', reprintLast);
    var undo = button('Undo last', 'rcv-small', undoLast);
    reprint.disabled = undo.disabled = !last;
    row.appendChild(reprint);
    row.appendChild(undo);
    return row;
  }

  function render() {
    if (running) { renderLoop(); } else { renderSetup(); }
  }

  function renderLoop() {
    panel.textContent = '';
    var box = el('div', 'rcv');
    if (already) {
      box.appendChild(el('div', 'rcv-head', summary()));
      box.appendChild(progressNode());
      box.appendChild(el('div', 'rcv-step bad', 'Already received'));
      box.appendChild(el('div', 'rcv-sub', 'This is ' + already.code +
        (already.item_name ? ', ' + already.item_name : '')));
      box.appendChild(el('div', 'rcv-sub', 'Nothing was created'));
      // A scan clears `already` while this screen shows; the button keeps its own copy.
      var shown = already;
      box.appendChild(button('Reprint ' + shown.code, 'rcv-big', function () {
        reprint(shown.unit_id, shown.code);
      }));
      box.appendChild(button('Keep scanning', 'rcv-big', function () {
        already = null;
        render();
        refocus();
      }));
      panel.appendChild(box);
      return;
    }
    box.appendChild(el('div', 'rcv-head', summary()));
    box.appendChild(progressNode());
    box.appendChild(noteNode());
    box.appendChild(el('div', 'rcv-step', loop.prompt()));
    var held = loop.held();
    if (held.tag) { box.appendChild(el('div', 'rcv-held', 'Asset tag: ' + held.tag + ' ✓')); }
    box.appendChild(button(loop.skipText(), 'rcv-skip', skip));
    box.appendChild(footer());
    box.appendChild(button('Change setup', 'rcv-link', function () {
      running = false;
      already = null;
      loop.reset();
      render();
    }));
    panel.appendChild(box);
  }

  function row(label, value, onClick, extra) {
    var wrap = el('div', 'rcv-row');
    wrap.appendChild(el('div', 'rcv-label', label));
    wrap.appendChild(button(value, 'rcv-pick', onClick));
    if (extra) { wrap.appendChild(extra); }
    return wrap;
  }

  function renderSetup() {
    panel.textContent = '';
    var box = el('div', 'rcv');
    box.appendChild(el('div', 'rcv-title', 'Receive owned gear'));
    if (done > 0) { box.appendChild(progressNode()); }
    box.appendChild(noteNode());
    box.appendChild(row('Item type', setup.item ? setup.item.name : 'Choose item type', chooseItem));
    var modes = el('div', 'rcv-row');
    modes.appendChild(el('div', 'rcv-label', 'Scan for each unit'));
    Object.keys(MODE_TEXT).forEach(function (mode) {
      var b = button(MODE_TEXT[mode], 'rcv-pick' + (setup.mode === mode ? ' on' : ''), function () {
        setup.mode = mode;
        renderSetup();
      });
      b.setAttribute('aria-pressed', setup.mode === mode ? 'true' : 'false');
      modes.appendChild(b);
    });
    box.appendChild(modes);
    var locText = locating ? 'Scan the location label now'
      : setup.location ? setup.location.code + (setup.location.name ? ' ' + setup.location.name : '')
      : 'Skip: no location';
    var locExtra = null;
    if (locating || setup.location) {
      locExtra = button(locating ? 'Cancel' : 'Clear location', 'rcv-link', function () {
        // Cancel abandons the re-scan; only Clear location drops the one already set.
        if (!locating) { setup.location = null; }
        locating = false;
        renderSetup();
      });
    }
    box.appendChild(row('Location (optional)', locText, function () {
      locating = true;
      S.showMessage('', 'info');
      renderSetup();
    }, locExtra));
    var tgtExtra = setup.target ? button('Clear target', 'rcv-link', function () {
      setup.target = null;
      renderSetup();
    }) : null;
    box.appendChild(row('Target count (optional)', setup.target ? String(setup.target) : 'No target',
      function () {
        S.numberPad({}, function (result) {
          if (result) { setup.target = result.quantity; }
          renderSetup();
        });
      }, tgtExtra));
    var style = el('div', 'rcv-row');
    style.appendChild(el('div', 'rcv-label', 'After each label'));
    [[true, 'Keep scanning'], [false, 'Back to setup']].forEach(function (opt) {
      var b = button(opt[1], 'rcv-pick' + (setup.bulk === opt[0] ? ' on' : ''), function () {
        setup.bulk = opt[0];
        renderSetup();
      });
      b.setAttribute('aria-pressed', setup.bulk === opt[0] ? 'true' : 'false');
      style.appendChild(b);
    });
    box.appendChild(style);
    box.appendChild(button('Start scanning', 'rcv-go', start));
    box.appendChild(footer());
    panel.appendChild(box);
  }

  function start() {
    if (!setup.item) {
      S.feedback('error');
      S.showMessage(MESSAGES.needItem, 'error');
      return;
    }
    S.showMessage('', 'info');
    locating = false;
    running = true;
    already = null;
    note = null;
    loop = createLoop(setup.mode);
    render();
    refocus();
  }

  function chooseItem() {
    S.askText('Item type: part of the name', function (text) {
      if (text === null) { renderSetup(); refocus(); return; }
      S.read(cfg.itemsUrl, {q: text}).then(function (answer) {
        if (answer.need_name) { return; }
        if (!answer.ok) {
          S.feedback('error');
          S.showMessage(answer.message, 'error');
          renderSetup();
          return;
        }
        if (!answer.items.length) {
          S.feedback('error');
          S.showMessage(MESSAGES.noItems, 'error');
          renderSetup();
          return;
        }
        S.showMessage('', 'info');
        S.choose(answer.items.map(function (item) { return {label: item.name, value: item}; }),
          function (item) {
            // The count and target belong to one item type; a new type starts both over.
            if (!setup.item || setup.item.id !== item.id) { done = 0; setup.target = null; }
            setup.item = item;
            renderSetup();
            refocus();
          },
          'Up to 20 shown. Search again to narrow.');
        panel.appendChild(button('Back', 'choice', function () { renderSetup(); refocus(); }));
        refocus();
      });
    });
  }

  function setupScan(code) {
    if (!locating) {
      S.feedback('error');
      S.showMessage(MESSAGES.notStarted, 'error');
      return Promise.resolve({ok: false});
    }
    return S.read(cfg.locationUrl, {code: code}).then(function (answer) {
      if (answer.need_name) { return {ok: false}; }
      if (!answer.ok) {
        S.feedback('error');
        S.showMessage(answer.message, 'error');
        return {ok: false};
      }
      S.feedback('ok');
      S.showMessage('', 'info');
      setup.location = {code: answer.code, name: answer.name};
      locating = false;
      renderSetup();
      return {ok: false};
    });
  }

  // Skip does nothing while a scan or submit is in flight; a second tap must not skip the next step.
  function skip() {
    if (gate.busy()) { return; }
    gate.run(function () { return handleStep(loop.skip()); });
    refocus();
  }

  function showAlready(found) {
    already = found;
    S.feedback('error');
    S.showMessage('Already received', 'error');
    render();
  }

  function handleStep(act) {
    if (act.kind === 'refuse') {
      S.feedback('error');
      S.showMessage(act.message, 'error');
      return Promise.resolve({ok: false});
    }
    S.showMessage('', 'info');
    if (act.kind === 'hold') {
      S.feedback('ok');
      render();
      return Promise.resolve({ok: false});
    }
    return submit(act.payload);
  }

  function submit(payload) {
    var body = {item_id: setup.item.id, tag: payload.tag, serial: payload.serial,
                container_code: setup.location ? setup.location.code : null};
    return S.write(cfg.receiveUrl, body).then(function (answer) {
      loop.reset();
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (answer.ok) {
        done += 1;
        last = {unit_id: answer.unit_id, code: answer.code};
        var waiting = S.printerWaiting(answer);
        note = {code: answer.code, waiting: waiting,
                text: waiting ? answer.code + ' is queued' : answer.code + ' sent to printer'};
        already = null;
        S.feedback('ok');
        if (!setup.bulk) { running = false; }
        render();
        return {ok: true};
      }
      if (answer.already) {
        showAlready(answer.already);
        return {ok: false};
      }
      S.feedback('error');
      S.showMessage(answer.message, 'error');
      render();
      return {ok: false};
    });
  }

  // In BOTH mode the server checks the tag before it is held, so a bad tag is refused at the
  // tag step. If the check cannot reach the server the tag is held and receive decides.
  function scanStep(code) {
    if (setup.mode !== 'BOTH' || loop.step() !== 'tag') { return handleStep(loop.scan(code)); }
    return S.read(cfg.checkTagUrl, {tag: code}).then(function (answer) {
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (answer.ok || answer.message === S.messages.offline) {
        return handleStep(loop.scan(code));
      }
      if (answer.already) {
        showAlready(answer.already);
        return {ok: false};
      }
      S.feedback('error');
      S.showMessage(answer.message, 'error');
      return {ok: false};
    });
  }

  function onScan(code) {
    return gate.run(function () {
      if (!running) { return setupScan(code); }
      if (already) { already = null; }
      return scanStep(code);
    });
  }

  // Reprint and Undo run one at a time; a second tap while one is in flight does nothing.
  function guarded(path, payload, onOk) {
    if (busy) { return; }
    busy = true;
    S.write(path, payload).then(function (answer) {
      busy = false;
      if (answer.need_name || answer.fatal) { return; }
      if (answer.ok) { onOk(answer); return; }
      S.feedback('error');
      S.showMessage(answer.message, 'error');
    });
  }

  function reprint(unitId, code) {
    guarded(cfg.reprintUrl, {unit_id: unitId}, function (answer) {
      var waiting = S.printerWaiting(answer);
      note = {code: answer.code, waiting: waiting, text: 'Reprinting ' + (code || answer.code)};
      already = null;
      S.feedback('ok');
      S.showMessage('', 'info');
      render();
      refocus();
    });
  }

  function reprintLast() {
    if (last) { reprint(last.unit_id, last.code); }
  }

  function undoLast() {
    if (!last) { return; }
    guarded(cfg.undoUrl, {unit_id: last.unit_id}, function (answer) {
      done = Math.max(0, done - 1);
      note = {code: null, waiting: false, text: last.code + ' undone. ' + answer.message};
      last = null;
      S.feedback('ok');
      S.showMessage('', 'info');
      render();
      refocus();
    });
  }

  S.start({station: 'RECEIVE', onScan: onScan});
  renderSetup();
})(typeof window !== 'undefined' ? window : globalThis);
