// Receive station for owned gear: set up once, then scan each unit and stick on its label.
// The scan order is chosen at setup because tags have no format; the app cannot tell a tag
// from a serial by reading it. All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var WAIT_ABOVE = 5;   // more queued labels than this reads as "Waiting for printer"
  var STEPS = {BOTH: ['tag', 'serial'], TAG: ['tag'], SERIAL: ['serial']};
  var STEP_TEXT = {tag: 'Scan the ASSET TAG', serial: 'Scan the SERIAL'};
  var SKIP_TEXT = {tag: 'Skip asset tag', serial: 'Skip serial'};
  var MODE_TEXT = {BOTH: 'Asset tag, then serial', TAG: 'Asset tag only', SERIAL: 'Serial only'};
  var MESSAGES = {
    sameCode: 'That is the same code as the tag. Scan the SERIAL.',
    notStarted: 'Press Start scanning first.',
    needItem: 'Choose an item type first.',
    noItems: 'No serialized item matches that.',
    cancelled: 'Not saved.'
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

  // The print station is not polling, or the queue is long.
  function printerWaiting(answer) {
    return answer.station_seen === false || answer.waiting > WAIT_ABOVE;
  }

  var pure = {createLoop: createLoop, progress: progress, printerWaiting: printerWaiting,
              MESSAGES: MESSAGES};
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
  var submitting = false;  // a receive is in flight; Skip must not start another step

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
    return node;
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
      box.appendChild(button('Reprint ' + already.code, 'rcv-big', function () {
        reprint(already.unit_id, already.code);
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
    box.appendChild(el('div', 'rcv-step', loop.prompt()));
    var held = loop.held();
    if (held.tag) { box.appendChild(el('div', 'rcv-held', 'Asset tag: ' + held.tag + ' ✓')); }
    box.appendChild(button(loop.skipText(), 'rcv-skip', skip));
    box.appendChild(noteNode());
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
        locating = false;
        setup.location = null;
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
          function (item) { setup.item = item; renderSetup(); refocus(); },
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

  function skip() {
    if (submitting) { return; }
    handleStep(loop.skip());
    refocus();
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
    submitting = true;
    return S.write(cfg.receiveUrl, body).then(function (answer) {
      submitting = false;
      loop.reset();
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (answer.ok) {
        done += 1;
        last = {unit_id: answer.unit_id, code: answer.code};
        var waiting = printerWaiting(answer);
        note = {code: answer.code, waiting: waiting,
                text: waiting ? answer.code + ' is queued' : answer.code + ' sent to printer'};
        already = null;
        S.feedback('ok');
        if (!setup.bulk) { running = false; }
        render();
        return {ok: true};
      }
      S.feedback('error');
      if (answer.already) {
        already = answer.already;
        S.showMessage('Already received', 'error');
      } else {
        S.showMessage(answer.message, 'error');
      }
      render();
      return {ok: false};
    });
  }

  function onScan(code) {
    if (!running) { return setupScan(code); }
    if (already) { already = null; }
    return handleStep(loop.scan(code));
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
      var waiting = printerWaiting(answer);
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
