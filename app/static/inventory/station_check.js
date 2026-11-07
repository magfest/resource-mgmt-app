// TV and printer check: scan a unit, check its serial, answer each line, finish once.
// Nothing is preselected. All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var WORDS = {TV_CHECK: 'TV', PRINTER_CHECK: 'printer'};
  var MESSAGES = {
    answerEvery: 'Answer every line.',
    sending: 'Saving. Wait for it to finish.',
    containerOnly: 'Scan a container label, or tap Skip.',
    alreadySaved: 'This check was already saved. Scan a unit to start the next one.',
    noteNeeded: 'Add a note saying what failed.',
    asideAsk: 'Set the first one aside and check this one?'
  };
  var CONTAINER_CODE = /^\s*C-?\d+\s*$/i;

  function word(station) { return WORDS[station] || 'unit'; }

  function idleText(station) {
    return 'Scan a ' + word(station) + ': its asset tag, label or serial.';
  }

  // The answers to one open check. version() changes on every edit, so a repeat tap of an
  // unchanged Finish can reuse its request id and an edited one cannot.
  function createCheck(open) {
    var lines = (open.lines || []).slice();
    var answers = {};
    var floor = null;       // 'pass' or 'fail' for a model with no lines
    var note = null;
    var condition = null;
    var edits = 0;

    function failed() {
      if (!lines.length) { return floor === 'fail'; }
      return lines.some(function (l) { return l.fails && answers[l.id] === 'NO'; });
    }
    return {
      id: open.check_id,
      code: open.code,
      name: open.name,
      lines: lines,
      serialDone: !!open.serial_checked,
      hasLines: function () { return lines.length > 0; },
      answer: function (id, value) { answers[id] = value; condition = null; edits += 1; },
      answerFor: function (id) { return answers[id] || null; },
      answeredCount: function () {
        return lines.filter(function (l) { return answers[l.id]; }).length;
      },
      setFloor: function (result, text) {
        floor = result;
        note = result === 'fail' ? text : null;
        condition = null;
        edits += 1;
      },
      floor: function () { return floor; },
      setCondition: function (value) { condition = value; edits += 1; },
      condition: function () { return condition; },
      complete: function () {
        return lines.length ? this.answeredCount() === lines.length : floor !== null;
      },
      isFailed: failed,
      needsCondition: function () { return failed() && condition === null; },
      // A retired unit has no number left to put away.
      canPutAway: function () { return condition !== 'RETIRED'; },
      version: function () { return edits; },
      body: function (putAwayCode) {
        var out = {check_id: open.check_id, answers: {}};
        lines.forEach(function (l) { if (answers[l.id]) { out.answers[l.id] = answers[l.id]; } });
        if (failed()) { out.failed_condition = condition; }
        if (!lines.length && floor === 'fail') { out.note = note; }
        if (putAwayCode) { out.put_away_code = putAwayCode; }
        return out;
      }
    };
  }

  // Runs steps one at a time in call order, so a scan during a save waits for it.
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

  function resultText(answer) {
    if (answer.status === 'PASSED') { return 'PASSED'; }
    return answer.condition === 'RETIRED' ? 'FAILED: retired' : 'FAILED: needs repair';
  }

  var pure = {createCheck: createCheck, createGate: createGate, resultText: resultText,
              idleText: idleText, word: word, MESSAGES: MESSAGES};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');

  var check = null;        // the open check, or null
  var label = null;        // an unused U- label waiting for its serial, or null
  var phase = 'idle';      // idle, label, serial, lines, putaway, result
  var result = null;       // the finished answer shown until the next scan
  var sending = false;     // a finish is in flight; edits and a second tap wait
  var finishId = null;     // {key, id}: reused for a repeat tap of an unchanged finish
  var asideId = null;      // {check, id}: reused for a repeat tap of Set aside
  var gate = createGate();

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function button(text, className, onClick) {
    var b = el('button', className, text);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function refocus() { field.focus(); }

  function fail(text) {
    S.feedback('error');
    S.showMessage(text, 'error');
  }

  function stopped(answer) { return answer.need_name || answer.fatal || answer.cancelled; }

  function headNode() {
    var head = el('div', 'chk-head');
    head.appendChild(el('div', 'chk-name', check.name));
    head.appendChild(el('div', 'chk-code', check.code));
    if (phase === 'lines' && check.hasLines()) {
      head.appendChild(el('div', 'chk-sub',
        check.answeredCount() + ' of ' + check.lines.length + ' answered'));
    }
    return head;
  }

  function render() {
    panel.textContent = '';
    if (phase === 'result') { renderResult(); return; }
    if (phase === 'label') { renderLabel(); return; }
    if (!check) { panel.appendChild(el('p', 'chk-idle', idleText(cfg.station))); return; }
    var box = el('div', 'chk');
    box.appendChild(headNode());
    if (phase === 'serial') { renderSerial(box); }
    else if (phase === 'putaway') { renderPutAway(box); }
    else { renderLines(box); }
    panel.appendChild(box);
  }

  function renderLabel() {
    var box = el('div', 'chk');
    box.appendChild(el('div', 'chk-name', 'New label ' + label.code));
    box.appendChild(el('div', 'chk-step', 'Scan this unit\'s serial barcode.'));
    box.appendChild(button('Cancel', 'chk-big', function () {
      label = null;
      phase = 'idle';
      S.showMessage('', 'info');
      render();
      refocus();
    }));
    panel.appendChild(box);
  }

  function renderSerial(box) {
    box.appendChild(el('div', 'chk-step', 'Scan the serial barcode.'));
    box.appendChild(button('No serial barcode', 'chk-big', function () {
      check.serialDone = true;
      phase = 'lines';
      S.showMessage('', 'info');
      render();
      refocus();
    }));
    box.appendChild(button('Set aside', 'chk-small', setAside));
  }

  function renderLines(box) {
    if (check.hasLines()) {
      check.lines.forEach(function (line) { box.appendChild(lineRow(line)); });
    } else {
      box.appendChild(floorRow());
    }
    var foot = el('div', 'chk-foot');
    foot.appendChild(button('Finish', 'chk-finish', finishTap));
    foot.appendChild(button('Set aside', 'chk-small', setAside));
    box.appendChild(foot);
  }

  function yesNo(current, onPick, yes, no) {
    var pair = el('div', 'chk-yn');
    [[yes || 'YES', 'YES', 'chk-yes'], [no || 'NO', 'NO', 'chk-no']].forEach(function (spec) {
      pair.appendChild(button(spec[0], 'chk-btn ' + spec[2] + (current === spec[1] ? ' on' : ''),
        function () { if (!sending) { onPick(spec[1]); } }));
    });
    return pair;
  }

  function lineRow(line) {
    var row = el('div', 'chk-line');
    row.appendChild(el('div', 'chk-prompt', line.prompt));
    row.appendChild(yesNo(check.answerFor(line.id), function (value) {
      check.answer(line.id, value);
      render();
      refocus();
    }));
    return row;
  }

  function floorRow() {
    var row = el('div', 'chk-line');
    row.appendChild(el('div', 'chk-prompt', 'No checklist for this model. Pass it, or fail it with a note.'));
    var current = check.floor() === 'pass' ? 'YES' : check.floor() === 'fail' ? 'NO' : null;
    row.appendChild(yesNo(current, function (value) {
      if (value === 'YES') { check.setFloor('pass'); render(); refocus(); return; }
      S.askText('What failed?', function (text) {
        if (text === null) { render(); refocus(); return; }
        if (!text) { fail(MESSAGES.noteNeeded); render(); refocus(); return; }
        check.setFloor('fail', text);
        render();
        refocus();
      });
    }, 'PASSED', 'FAILED'));
    return row;
  }

  function renderPutAway(box) {
    box.appendChild(el('div', 'chk-step', 'Scan where it goes, or Skip.'));
    box.appendChild(button('Skip', 'chk-big', function () { send(null); }));
    box.appendChild(button('Back', 'chk-small', function () {
      if (sending) { return; }
      phase = 'lines';
      render();
      refocus();
    }));
  }

  function renderResult() {
    var box = el('div', 'chk');
    box.appendChild(el('div', 'chk-name', result.name));
    box.appendChild(el('div', 'chk-code', result.code));
    box.appendChild(el('div', 'chk-result ' + (result.status === 'PASSED' ? 'pass' : 'fail'),
      resultText(result)));
    if (result.put_away_error) {
      box.appendChild(el('div', 'chk-warn', 'Not moved: ' + result.put_away_error));
    }
    box.appendChild(el('div', 'chk-step', idleText(cfg.station)));
    panel.appendChild(box);
  }

  function startCheck(answer) {
    label = null;
    result = null;
    finishId = null;
    asideId = null;
    check = createCheck(answer);
    phase = answer.serial_checked ? 'lines' : 'serial';
    S.showMessage('', 'info');
    S.feedback('ok');
    render();
    refocus();
  }

  function dropCheck(message) {
    check = null;
    label = null;
    phase = 'idle';
    result = null;
    S.feedback('error');
    S.showMessage(message, 'info');
    render();
    refocus();
  }

  function openWrite(code, extra) {
    return S.write(cfg.openUrl, Object.assign({code: code, station_code: cfg.station}, extra || {}))
      .then(function (answer) { return openAnswer(code, answer); });
  }

  // Opening a check finishes nothing, so it reports ok:false and the shell's repeat guard
  // stays empty; a unit set aside can be scanned again at once.
  function openAnswer(code, answer) {
    if (stopped(answer)) { return {ok: false}; }
    if (!answer.ok) {
      if (answer.wrong_station) { wrongGear(answer); }
      else if (answer.open_check) { askAside(answer.open_check, code); }
      else if (answer.finished) { dropCheck(MESSAGES.alreadySaved); }
      else { fail(answer.message); }
      return {ok: false};
    }
    if (answer.unused) { return startLabel(answer); }
    if (answer.create) { offerCreate(answer); return {ok: false}; }
    if (answer.candidates) { chooseUnit(code, answer.candidates); return {ok: false}; }
    if (answer.already_checked) { askAgain(code, answer); return {ok: false}; }
    if (check && check.id === answer.check_id) {
      S.showMessage('You are already checking this unit.', 'info');
      return {ok: false};
    }
    startCheck(answer);
    return {ok: false};
  }

  function startLabel(answer) {
    label = {code: answer.code};
    phase = 'label';
    S.showMessage('', 'info');
    render();
    refocus();
    return {ok: false};
  }

  function backToScreen() { render(); refocus(); }

  function wrongGear(answer) {
    if (check) {
      fail('This is a ' + answer.what + '. Finish this check first.');
      return;
    }
    if (!answer.can_switch) { fail(answer.message); return; }
    var rid = S.requestId();
    S.feedback('error');
    S.choose([{label: 'Switch', value: 'switch'}, {label: 'Cancel', value: 'cancel'}],
      function (pick) {
        if (pick !== 'switch') { backToScreen(); return; }
        gate.run(function () {
          return S.write(cfg.stationUrl, {station_code: answer.wrong_station}, rid)
            .then(function (a) {
              if (stopped(a)) { return; }
              if (!a.ok) { fail(a.message); backToScreen(); return; }
              root.location.assign(cfg.homeUrl);
            });
        });
      }, answer.message);
  }

  function askAside(open, code) {
    var rid = S.requestId();
    S.feedback('error');
    S.choose([{label: 'Set aside', value: 'aside'}, {label: 'Keep checking', value: 'keep'}],
      function (pick) {
        if (pick !== 'aside') { backToScreen(); return; }
        gate.run(function () {
          return S.write(cfg.setAsideUrl, {check_id: open.check_id}, rid).then(function (a) {
            if (stopped(a)) { return; }
            if (!a.ok && !a.finished) { fail(a.message); backToScreen(); return; }
            if (check && check.id === open.check_id) { check = null; phase = 'idle'; }
            S.showMessage('', 'info');
            return openWrite(code);
          });
        });
      }, open.code + ' is not finished. ' + MESSAGES.asideAsk);
  }

  function askAgain(code, answer) {
    var when = answer.already_checked;
    S.choose([{label: 'Check it again', value: 'again'}, {label: 'Cancel', value: 'cancel'}],
      function (pick) {
        if (pick !== 'again') { backToScreen(); return; }
        gate.run(function () { return openWrite(code, {again: true}); });
      }, answer.name + ' ' + answer.code + ' was checked: ' + when.result + ' on ' + when.when +
         '. Check it again?');
  }

  function chooseUnit(code, candidates) {
    S.choose(candidates.map(function (c) { return {label: c.name + ' ' + c.code, value: c.unit_id}; })
      .concat([{label: 'Cancel', value: null}]), function (unitId) {
        if (unitId === null) { backToScreen(); return; }
        gate.run(function () { return openWrite(code, {unit_id: unitId}); });
      }, 'Two units match: tap the one in your hand');
  }

  function offerCreate(answer) {
    S.choose(answer.models.map(function (m) { return {label: m.name, value: m.id}; })
      .concat([{label: 'Cancel', value: null}]), function (itemId) {
        if (itemId === null) { backToScreen(); return; }
        gate.run(function () {
          return S.write(cfg.createUrl, {label_code: answer.label_code, serial: answer.serial,
                                         item_id: itemId}).then(function (a) {
            if (stopped(a)) { return; }
            if (!a.ok) { fail(a.message); startLabel({code: answer.label_code}); return; }
            startCheck(a);
          });
        });
      }, answer.serial + ' is not on any unit. Pick what this is.');
  }

  function bindWrite(serial) {
    return S.write(cfg.bindUrl, {label_code: label.code, serial: serial}).then(function (answer) {
      if (stopped(answer)) { return {ok: false}; }
      if (!answer.ok) {
        if (answer.wrong_station) { wrongGear(answer); } else { fail(answer.message); }
        return {ok: false};
      }
      if (answer.create) { offerCreate(answer); return {ok: false}; }
      startCheck(answer);
      return {ok: true};
    });
  }

  function serialWrite(code) {
    var mine = check;
    return S.write(cfg.serialUrl, {check_id: mine.id, code: code}).then(function (answer) {
      if (stopped(answer)) { return {ok: false}; }
      if (!answer.ok) {
        if (answer.finished) { dropCheck(MESSAGES.alreadySaved); } else { fail(answer.message); }
        return {ok: false};
      }
      if (check !== mine) { return {ok: false}; }
      check.serialDone = true;
      phase = 'lines';
      S.feedback('ok');
      S.showMessage(answer.message, answer.result === 'matches' ? 'ok' : 'info');
      render();
      refocus();
      return {ok: true};
    });
  }

  function setAside() {
    if (!check || sending) { return; }
    var mine = check;
    if (!asideId || asideId.check !== mine.id) { asideId = {check: mine.id, id: S.requestId()}; }
    gate.run(function () {
      return S.write(cfg.setAsideUrl, {check_id: mine.id}, asideId.id).then(function (answer) {
        if (stopped(answer)) { return; }
        if (!answer.ok && !answer.finished) { fail(answer.message); return; }
        check = null;
        phase = 'idle';
        S.showMessage('Set aside. Scan the next unit.', 'info');
        render();
        refocus();
      });
    });
  }

  function finishTap() {
    if (!check || sending) {
      if (sending) { S.showMessage(MESSAGES.sending, 'info'); }
      return;
    }
    if (!check.complete()) { fail(MESSAGES.answerEvery); return; }
    if (check.needsCondition()) { askCondition(); return; }
    next();
  }

  function next() {
    if (check.canPutAway()) {
      phase = 'putaway';
      render();
      refocus();
      return;
    }
    send(null);
  }

  function askCondition() {
    S.choose([
      {label: 'Needs repair', value: 'REPAIR'},
      {label: 'Retire it (its number stops working)', value: 'RETIRED'},
      {label: 'Back', value: null}
    ], function (value) {
      if (value === null) { backToScreen(); return; }
      check.setCondition(value);
      next();
    }, 'It failed. What happens to it?');
  }

  // A tap queues the save behind any scan in flight. `sending` is set at once, so a second
  // tap of Finish or Skip is ignored instead of queued.
  function send(putAwayCode) {
    if (sending) { S.showMessage(MESSAGES.sending, 'info'); return Promise.resolve({ok: false}); }
    if (!check) { return Promise.resolve({ok: false}); }
    sending = true;
    return gate.run(function () { return transmit(putAwayCode); })
      .catch(function () { sending = false; return {ok: false}; });
  }

  // The same payload reuses one request id, so a retry saves once.
  function transmit(putAwayCode) {
    var mine = check;
    if (!mine) { sending = false; return {ok: false}; }
    var key = mine.version() + '|' + (putAwayCode || '');
    if (!finishId || finishId.key !== key) { finishId = {key: key, id: S.requestId()}; }
    var rid = finishId.id;
    var body = mine.body(putAwayCode);
    return S.write(cfg.finishUrl, body, rid).then(function (answer) {
      sending = false;
      return finished(mine, answer);
    });
  }

  function finished(mine, answer) {
    if (stopped(answer)) { render(); return {ok: false}; }
    if (!answer.ok) {
      if (answer.finished) { dropCheck(MESSAGES.alreadySaved); return {ok: false}; }
      phase = 'lines';
      fail(answer.message);
      render();
      return {ok: false};
    }
    result = Object.assign({name: mine.name, code: answer.code || mine.code}, answer);
    check = null;
    phase = 'result';
    finishId = null;
    if (answer.put_away_error) { fail('Check saved. Not moved: ' + answer.put_away_error); }
    else { S.feedback('ok'); S.showMessage('', 'info'); }
    render();
    refocus();
    return {ok: true};
  }

  function onScan(code) {
    return gate.run(function () {
      if (phase === 'label') { return bindWrite(code); }
      if (phase === 'serial' && check) { return serialWrite(code); }
      if (phase === 'putaway' && check) {
        if (!CONTAINER_CODE.test(code)) { fail(MESSAGES.containerOnly); return {ok: false}; }
        sending = true;
        return transmit(code);
      }
      return openWrite(code);
    }).catch(function () {
      fail('That scan failed. Scan it again.');
      return {ok: false};
    });
  }

  S.holdLeave(function () { return !!check; });
  S.start({station: cfg.station, onScan: onScan});
  render();
})(typeof window !== 'undefined' ? window : globalThis);
