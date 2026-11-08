// Unit station: scan a unit, give it a number if it has none, check it, finish once.
// The server places the unit in this scanner's home container. A scan of a pallet after a
// finished unit moves that unit. All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var MESSAGES = {
    answerEvery: 'Answer every line.',
    sending: 'Saving. Wait for it to finish.',
    alreadySaved: 'This check was already saved. Scan a unit to start the next one.',
    asideAsk: 'Set the first one aside and check this one?',
    idle: 'Scan the asset tag, U- label or serial.',
    which: 'Which unit is this? Tap the number written on it.',
    tagRequired: 'Put an asset tag on it and scan the tag.',
    orTag: 'Or scan its asset tag.',
    next: 'Scan its pallet, or scan the next unit.',
    nextRetired: 'Scan the next unit.',
    notPlaced: 'Not placed: only a shared scanner places units.',
    noMatch: 'No unit ends in that number.'
  };

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
      unitId: open.unit_id,
      code: open.code,
      name: open.name,
      card: open.card || null,
      labelQueued: !!open.labelQueued,
      lines: lines,
      stale: false,
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
      version: function () { return edits; },
      body: function () {
        var out = {check_id: open.check_id, answers: {}, line_ids: lines.map(function (l) { return l.id; })};
        lines.forEach(function (l) { if (answers[l.id]) { out.answers[l.id] = answers[l.id]; } });
        if (failed()) { out.failed_condition = condition; }
        if (!lines.length && floor === 'fail' && note) { out.note = note; }
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

  // Touches the server shift while the screen sends nothing. A touch needs a tap since the
  // last one, so an abandoned screen still lets the shift expire. Same rule as the count page.
  var KEEPALIVE_MS = 5 * 60 * 1000;
  function createKeepAlive(options) {
    var tapped = false;
    var last = options.now();
    return {
      tap: function () { tapped = true; },
      reset: function () { tapped = false; last = options.now(); },
      check: function () {
        if (!tapped || options.now() - last < options.everyMs) { return false; }
        tapped = false;
        last = options.now();
        options.send();
        return true;
      }
    };
  }

  // The sentence a finished unit shows. `answer` is the finish answer.
  function doneText(answer) {
    var name = answer.name;
    if (answer.condition === 'RETIRED') { return name + ' retired.'; }
    var where = answer.placed ? ' In the ' + answer.placed + '.' : ' ' + MESSAGES.notPlaced;
    if (answer.status === 'PASSED') { return name + ' works.' + where; }
    return name + ' is broken: in repair.' + where;
  }

  // The label line under the done sentence, or null when no label was involved.
  function labelText(answer, alreadyQueued) {
    if (answer.condition === 'RETIRED') { return null; }
    if (answer.label === 'queued' || (alreadyQueued && answer.label === 'none')) {
      return 'Info label sent to the printer.';
    }
    return answer.label && answer.label !== 'none' ? answer.label : null;
  }

  var pure = {createCheck: createCheck, createGate: createGate, createKeepAlive: createKeepAlive,
              KEEPALIVE_MS: KEEPALIVE_MS, doneText: doneText, labelText: labelText,
              MESSAGES: MESSAGES};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');

  var check = null;        // the open check, or null
  var held = null;         // {card, tagRequired} a unit found with no number, or null
  var offered = null;      // {item} a model picked for a new unit that still needs a tag, or null
  var phase = 'idle';      // idle, number, createTag, check, result
  var result = null;       // the finished answer shown until the next scan
  var lastUnitId = null;   // the newest finished unit; a pallet scan places it
  var sending = false;     // a finish is in flight; edits and a second tap wait
  var finishId = null;     // {key, id}: reused for a repeat tap of an unchanged finish
  var asideId = null;      // {check, id}: reused for a repeat tap of Set aside
  var tapIds = {};         // action key -> request id: a repeat tap of one button saves once
  var gate = createGate();
  var keepAlive = createKeepAlive({now: Date.now, everyMs: KEEPALIVE_MS, send: function () {
    S.read(cfg.keepAliveUrl, {});
  }});

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
  function backToScreen() { render(); refocus(); }

  function fail(text) {
    S.feedback('error');
    S.showMessage(text, 'error');
  }

  function stopped(answer) { return answer.need_name || answer.fatal || answer.cancelled; }

  function tapId(key) {
    if (!tapIds[key]) { tapIds[key] = S.requestId(); }
    return tapIds[key];
  }

  // A model with no spec fields reports its own name as the spec; showing it twice adds nothing.
  function specOf(card) { return card.spec && card.spec !== card.type ? card.spec : null; }

  function cardLines(c) { return [c.name, c.type, specOf(c), c.code]; }

  // The card leads with the name, then type and spec, then the code.
  function cardNode(card, withSub) {
    var head = el('div', 'chk-head');
    head.appendChild(el('div', 'chk-name', card.name));
    if (card.type) { head.appendChild(el('div', 'chk-sub', card.type)); }
    if (specOf(card)) { head.appendChild(el('div', 'chk-sub', specOf(card))); }
    head.appendChild(card.code ? el('div', 'chk-code', card.code)
                               : el('div', 'chk-code muted', 'No number yet'));
    if (withSub) { head.appendChild(el('div', 'chk-sub', withSub)); }
    return head;
  }

  function noCodeButton() {
    return button('No code on it', 'chk-big', function () { startWhich(null); });
  }

  function render() {
    panel.textContent = '';
    var subject = check ? check.card : held ? held.card : null;
    S.setFlagSubject(subject && subject.unit_id != null
      ? {unitId: subject.unit_id, label: subject.name} : null);
    if (phase === 'result') { renderResult(); return; }
    var box = el('div', 'chk');
    if (phase === 'number') { renderNumber(box); }
    else if (phase === 'createTag') { renderCreateTag(box); }
    else if (phase === 'check' && check) { renderCheck(box); }
    else {
      box.appendChild(el('p', 'chk-idle', MESSAGES.idle));
      box.appendChild(noCodeButton());
    }
    panel.appendChild(box);
  }

  function renderNumber(box) {
    box.appendChild(cardNode(held.card));
    if (held.tagRequired) {
      box.appendChild(el('div', 'chk-step', MESSAGES.tagRequired));
    } else {
      box.appendChild(button('Give it a U- number', 'chk-big', giveNumber));
      box.appendChild(el('div', 'chk-step', MESSAGES.orTag));
    }
    box.appendChild(button('Cancel', 'chk-small', cancelHeld));
  }

  function renderCreateTag(box) {
    box.appendChild(el('div', 'chk-name', offered.name));
    box.appendChild(el('div', 'chk-step', MESSAGES.tagRequired));
    box.appendChild(button('Cancel', 'chk-small', cancelHeld));
  }

  function cancelHeld() {
    held = null;
    offered = null;
    phase = 'idle';
    S.showMessage('', 'info');
    backToScreen();
  }

  function renderCheck(box) {
    var sub = check.hasLines() ? check.answeredCount() + ' of ' + check.lines.length + ' answered' : null;
    box.appendChild(cardNode(check.card || {name: check.name, code: check.code}, sub));
    if (check.hasLines()) {
      check.lines.forEach(function (line) { box.appendChild(lineRow(line)); });
      var foot = el('div', 'chk-foot');
      foot.appendChild(button('Finish', 'chk-finish', finishTap));
      foot.appendChild(button('Set aside', 'chk-small', setAside));
      box.appendChild(foot);
      return;
    }
    var pair = el('div', 'chk-yn');
    pair.appendChild(button('Works', 'chk-btn chk-yes', worksTap));
    pair.appendChild(button('Broken', 'chk-btn chk-no', brokenTap));
    box.appendChild(pair);
    box.appendChild(button('Set aside', 'chk-small', setAside));
  }

  function yesNo(current, onPick) {
    var pair = el('div', 'chk-yn');
    [['YES', 'chk-yes'], ['NO', 'chk-no']].forEach(function (spec) {
      pair.appendChild(button(spec[0], 'chk-btn ' + spec[1] + (current === spec[0] ? ' on' : ''),
        function () { if (!sending) { onPick(spec[0]); } }));
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

  function renderResult() {
    var box = el('div', 'chk');
    box.appendChild(cardNode(result.card));
    var failedUnit = result.status !== 'PASSED';
    box.appendChild(el('div', 'chk-result ' + (failedUnit ? 'fail' : 'pass'), doneText(result)));
    var label = labelText(result, result.labelQueued);
    if (label) {
      box.appendChild(el(
        'div', label === 'Info label sent to the printer.' ? 'chk-sub' : 'chk-warn', label));
    }
    box.appendChild(el('div', 'chk-step',
      result.condition === 'RETIRED' ? MESSAGES.nextRetired : MESSAGES.next));
    box.appendChild(noCodeButton());
    panel.appendChild(box);
  }

  function startCheck(answer, extra) {
    held = null;
    offered = null;
    result = null;
    finishId = null;
    asideId = null;
    check = createCheck(Object.assign({}, answer, extra || {}));
    keepAlive.reset();
    phase = 'check';
    S.showMessage('', 'info');
    S.feedback('ok');
    render();
    refocus();
  }

  function dropCheck(message) {
    check = null;
    held = null;
    phase = 'idle';
    result = null;
    S.feedback('error');
    S.showMessage(message, 'info');
    render();
    refocus();
  }

  // Every request that can open a check answers with the same set of cases.
  // `again` is what a repeat of the request must add; `code` finds the unit again.
  function openAnswer(answer, code, unitId) {
    if (stopped(answer)) { return {ok: false}; }
    if (!answer.ok) {
      if (answer.open_check) { askAside(answer.open_check, code, unitId); }
      else if (answer.finished) { dropCheck(MESSAGES.alreadySaved); }
      else { fail(answer.message); }
      return {ok: false};
    }
    if (answer.placed) { return placedAnswer(answer); }
    if (answer.candidates) { chooseUnit(code, answer.candidates); return {ok: false}; }
    if (answer.needs_number) {
      held = {card: answer.card, tagRequired: !!answer.asset_tag_required};
      phase = 'number';
      S.showMessage('', 'info');
      S.feedback('ok');
      render();
      refocus();
      return {ok: false};
    }
    if (answer.which) { startWhich(answer.tag); return {ok: false}; }
    if (answer.already_checked) { askAgain(code, answer, unitId); return {ok: false}; }
    if (check && check.id === answer.check_id) {
      S.showMessage('You are already checking this unit.', 'info');
      return {ok: false};
    }
    startCheck(answer, {labelQueued: answer.label === 'queued'});
    return {ok: false};
  }

  function placedAnswer(answer) {
    var card = answer.card || {};
    held = null;
    phase = 'idle';
    result = null;
    S.setFlagSubject(null);
    S.feedback('ok');
    S.showMessage((card.name || 'The unit') + ' is on ' + answer.placed + '.', 'ok');
    render();
    refocus();
    return {ok: true};
  }

  function scanWrite(code, extra) {
    var body = Object.assign({code: code, last_unit_id: lastUnitId}, extra || {});
    return S.write(cfg.scanUrl, body).then(function (answer) {
      return openAnswer(answer, code, extra && extra.unit_id);
    });
  }

  function askAside(open, code, unitId) {
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
            return scanWrite(code, unitId != null ? {unit_id: unitId} : null);
          });
        });
      }, (open.code || open.name) + ' is not finished. ' + MESSAGES.asideAsk);
  }

  function askAgain(code, answer, unitId) {
    var when = answer.already_checked;
    var again = {again: true};
    var id = unitId != null ? unitId : answer.card && answer.card.unit_id;
    if (id != null) { again.unit_id = id; }
    var rescan = answer.code || code;
    S.choose([{label: 'Check it again', value: 'again'}, {label: 'Cancel', value: 'cancel'}],
      function (pick) {
        if (pick !== 'again') { backToScreen(); return; }
        gate.run(function () { return scanWrite(rescan, again); });
      }, (answer.card ? answer.card.name : answer.name) + ' was checked: ' + when.result +
         ' on ' + when.when + '. Check it again?');
  }

  function chooseUnit(code, candidates) {
    S.choose(candidates.map(function (c) {
      return {label: c.name, lines: cardLines(c), value: c.unit_id};
    }).concat([{label: 'Cancel', value: null}]), function (unitId) {
      if (unitId === null) { backToScreen(); return; }
      gate.run(function () { return scanWrite(code, {unit_id: unitId}); });
    }, 'Two units match: tap the one in your hand');
  }

  // Step 2: the volunteer taps the number written on the unit. `tag` is the asset tag that
  // matched no unit, or null after No code on it.
  function startWhich(tag) {
    held = null;
    offered = null;
    check = null;
    phase = 'idle';
    S.showMessage(MESSAGES.which, 'info');
    S.numberPad({}, function (entered) {
      if (entered === null) { cancelHeld(); return; }
      S.read(cfg.candidatesUrl, {digits: String(entered.entered)}).then(function (answer) {
        if (stopped(answer)) { return; }
        if (!answer.ok) { fail(answer.message); showMatches(tag, []); return; }
        showMatches(tag, answer.candidates);
      });
    });
    refocus();
  }

  function showMatches(tag, cards) {
    var options = cards.map(function (c) {
      return {label: c.name, lines: cardLines(c), value: c};
    });
    options.push({label: 'Type the name', value: 'name'});
    options.push({label: 'Not on the list', value: 'new'});
    options.push({label: 'Cancel', value: 'cancel'});
    S.showMessage(cards.length ? MESSAGES.which : MESSAGES.noMatch, 'info');
    S.choose(options, function (pick) {
      if (pick === 'cancel') { cancelHeld(); return; }
      if (pick === 'name') { askName(tag); return; }
      if (pick === 'new') { listModels(tag); return; }
      picked(tag, pick);
    });
    refocus();
  }

  function askName(tag) {
    S.askText('Unit name', function (text) {
      if (text === null || text === '') { showMatches(tag, []); return; }
      S.read(cfg.candidatesUrl, {name: text}).then(function (answer) {
        if (stopped(answer)) { return; }
        if (!answer.ok) { fail(answer.message); showMatches(tag, []); return; }
        showMatches(tag, answer.candidates);
      });
    });
  }

  function picked(tag, card) {
    if (tag) { gate.run(function () { return linkWrite(card, tag); }); return; }
    held = {card: card, tagRequired: !!card.asset_tag_required};
    phase = 'number';
    S.showMessage('', 'info');
    render();
    refocus();
  }

  function linkWrite(card, tag) {
    var rid = tapId('link|' + card.unit_id + '|' + tag);
    return S.write(cfg.linkUrl, {unit_id: card.unit_id, tag: tag}, rid).then(function (answer) {
      if (stopped(answer)) { return {ok: false}; }
      if (!answer.ok) {
        fail(answer.message);
        if (phase === 'number') { render(); } else { showMatches(tag, [card]); }
        return {ok: false};
      }
      return openAnswer(answer, tag, card.unit_id);
    });
  }

  function giveNumber() {
    if (!held) { return; }
    var mine = held;
    gate.run(function () {
      var rid = tapId('give|' + mine.card.unit_id);
      return S.write(cfg.giveNumberUrl, {unit_id: mine.card.unit_id}, rid).then(function (answer) {
        if (stopped(answer)) { return; }
        if (!answer.ok) { fail(answer.message); return; }
        openAnswer(answer, answer.code, mine.card.unit_id);
      });
    });
  }

  function listModels(tag) {
    S.read(cfg.modelsUrl, {}).then(function (answer) {
      if (stopped(answer)) { return; }
      if (!answer.ok) { fail(answer.message); showMatches(tag, []); return; }
      S.showMessage('Pick what this is.', 'info');
      S.choose(answer.models.map(function (m) { return {label: m.name, value: m}; })
        .concat([{label: 'Cancel', value: null}]), function (model) {
          if (model === null) { cancelHeld(); return; }
          if (model.needs_asset_tag && !tag) {
            offered = model;
            phase = 'createTag';
            S.showMessage('', 'info');
            render();
            refocus();
            return;
          }
          gate.run(function () { return createWrite(model, tag); });
        });
      refocus();
    });
  }

  function createWrite(model, tag) {
    var rid = tapId('create|' + model.id + '|' + (tag || ''));
    var body = {item_id: model.id};
    if (tag) { body.tag = tag; }
    return S.write(cfg.createUrl, body, rid).then(function (answer) {
      if (stopped(answer)) { return {ok: false}; }
      if (!answer.ok) {
        fail(answer.message);
        if (phase === 'createTag') { render(); } else { showMatches(tag, []); }
        return {ok: false};
      }
      return openAnswer(answer, tag, null);
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

  function worksTap() {
    if (!check || sending) { return; }
    check.setFloor('pass');
    send();
  }

  // Broken with no checklist: repair or retire, then a note that may be skipped.
  function brokenTap() {
    if (!check || sending) { return; }
    askCondition(function (condition) {
      S.choose([{label: 'Add a note', value: 'note'}, {label: 'Skip', value: 'skip'}],
        function (pick) {
          if (pick === 'skip') { commitBroken(condition, null); return; }
          S.askText('What is wrong?', function (text) { commitBroken(condition, text || null); });
        }, 'Add a note about what is wrong, or skip.');
    });
  }

  function commitBroken(condition, text) {
    check.setFloor('fail', text);
    check.setCondition(condition);
    send();
  }

  function askCondition(onPick) {
    S.choose([
      {label: 'Repair', value: 'REPAIR'},
      {label: 'Retire', value: 'RETIRED'},
      {label: 'Back', value: null}
    ], function (value) {
      if (value === null) { backToScreen(); return; }
      onPick(value);
    }, 'It is broken. What happens to it?');
  }

  function finishTap() {
    if (!check || sending) {
      if (sending) { S.showMessage(MESSAGES.sending, 'info'); }
      return;
    }
    if (!check.complete()) { fail(MESSAGES.answerEvery); return; }
    if (check.needsCondition()) {
      askCondition(function (value) { check.setCondition(value); send(); });
      return;
    }
    send();
  }

  // A tap queues the save behind any scan in flight. `sending` is set at once, so a second
  // tap is ignored instead of queued.
  function send() {
    if (sending) { S.showMessage(MESSAGES.sending, 'info'); return Promise.resolve({ok: false}); }
    if (!check) { return Promise.resolve({ok: false}); }
    sending = true;
    return gate.run(transmit).catch(function () { sending = false; return {ok: false}; });
  }

  // The same payload reuses one request id, so a retry saves once.
  function transmit() {
    var mine = check;
    if (!mine) { sending = false; return {ok: false}; }
    var key = String(mine.version());
    if (!finishId || finishId.key !== key) { finishId = {key: key, id: S.requestId()}; }
    return S.write(cfg.finishUrl, mine.body(), finishId.id).then(function (answer) {
      sending = false;
      return finished(mine, answer);
    });
  }

  function finished(mine, answer) {
    if (stopped(answer)) { render(); return {ok: false}; }
    if (!answer.ok) {
      if (answer.finished) { dropCheck(MESSAGES.alreadySaved); return {ok: false}; }
      if (answer.changed) { mine.stale = true; }
      fail(answer.message);
      render();
      return {ok: false};
    }
    result = Object.assign({labelQueued: mine.labelQueued}, answer);
    if (!result.card) { result.card = mine.card || {name: mine.name, code: mine.code}; }
    lastUnitId = mine.unitId;
    check = null;
    phase = 'result';
    finishId = null;
    S.feedback('ok');
    S.showMessage('', 'info');
    S.offerUndo({kind: 'check', name: result.code || result.name, onUndone: function () {
      result = null;
      lastUnitId = null;
      phase = 'idle';
      render();
    }});
    render();
    refocus();
    return {ok: true};
  }

  function onScan(code) {
    return gate.run(function () {
      if (phase === 'number' && held) {
        return linkWrite(held.card, code);
      }
      if (phase === 'createTag' && offered) { return createWrite(offered, code); }
      return scanWrite(code);
    }).catch(function () {
      fail('That scan failed. Scan it again.');
      return {ok: false};
    });
  }

  panel.addEventListener('click', function () { if (check) { keepAlive.tap(); } });
  setInterval(function () { if (check) { keepAlive.check(); } }, 60 * 1000);
  S.onActivity(function () { if (check) { keepAlive.tap(); } });
  S.holdLeave(function () { return !!check; });
  S.start({station: cfg.station, onScan: onScan});
  render();
})(typeof window !== 'undefined' ? window : globalThis);
