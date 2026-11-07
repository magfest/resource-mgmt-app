// Count station: scan a container, count each item, submit once.
// The first count of a container is blind; the server sends no expected numbers then.
// All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var REASONS = [
    {value: 'DRIED_OUT', label: 'Dried out'}, {value: 'BROKEN', label: 'Broken'},
    {value: 'EXPIRED', label: 'Expired'}, {value: 'OTHER', label: 'Other'}
  ];
  var MESSAGES = {
    startFirst: 'Scan a container label to start counting.',
    idle: 'Count: scan a container label to start.',
    finishFirst: 'Submit this count before starting another.',
    submitting: 'Submitting. Wait for it to finish.',
    nothingCounted: 'Count at least one item first.',
    noItems: 'No item matches that.',
    alreadySaved: 'This count was already saved. Scan a container to count again.',
    cancelAsk: 'Discard this count? Nothing in it has been sent.'
  };
  var KEEPALIVE_MS = 5 * 60 * 1000;
  var CONTAINER_CODE = /^\s*C-?\d+\s*$/i;

  function reasonLabel(value) {
    var hit = REASONS.filter(function (r) { return r.value === value; })[0];
    return hit ? hit.label : value;
  }

  function staleLine(name) { return name + ' changed while you counted. Count it again.'; }

  function staleText(names) { return names.map(staleLine).join(' '); }

  function lineText(line) {
    var text = (line.kept === 0 && !line.discarded) ? 'none'
      : (line.is_approximate ? 'about ' : '') + line.kept;
    if (line.discarded) {
      text += ', ' + line.discarded + ' thrown out (' + reasonLabel(line.discard_reason) + ')';
    }
    return text;
  }

  function headTitle(c) {
    return (c.blind ? 'First count: ' : 'Recount: ') + c.code + (c.name ? ' ' + c.name : '');
  }

  // Expected number and when it was last counted, for a held item. Empty on a blind count.
  function heldNote(expected, when) {
    if (expected === null || expected === undefined) { return ''; }
    return when ? expected + ' on ' + when : String(expected);
  }

  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  // The lines of one open count. unresolved() lists held items with no line, which block
  // Submit. version() changes on every edit, so a repeat tap of an unchanged Submit can
  // reuse its request id and an edited one cannot.
  function createCount(start) {
    var held = (start.held || []).slice();
    var expected = {};
    var lastCounted = {};
    (start.lines || []).forEach(function (l) {
      expected[l.item_id] = l.expected;
      if (l.last_counted) { lastCounted[l.item_id] = l.last_counted; }
    });
    var order = [];
    var lines = {};
    var edits = 0;

    function nameOf(id) {
      var hit = held.filter(function (h) { return h.item_id === id; })[0];
      return hit ? hit.name : '';
    }
    function ensure(id, name) {
      if (!lines[id]) {
        lines[id] = {item_id: id, name: name || nameOf(id), kept: 0, is_approximate: false,
                     discarded: 0, discard_reason: null};
        order.push(id);
      }
      return lines[id];
    }
    return {
      id: start.count_id,
      code: start.code,
      name: start.name || '',
      blind: !!start.blind,
      usual: start.usual || [],
      lastCountedFor: function (id) { return lastCounted[id] || null; },
      expectedFor: function (id) { return expected[id] === undefined ? null : expected[id]; },
      setLine: function (item, kept, about) {
        var line = ensure(item.id, item.name);
        line.kept = kept;
        line.is_approximate = !!about;
        edits += 1;
      },
      markNone: function (id) {
        var line = ensure(id);
        line.kept = 0;
        line.is_approximate = false;
        edits += 1;
      },
      setDiscard: function (id, quantity, reason) {
        if (!lines[id]) { return false; }
        lines[id].discarded = quantity;
        lines[id].discard_reason = quantity ? reason : null;
        edits += 1;
        return true;
      },
      line: function (id) { return lines[id] || null; },
      lines: function () { return order.map(function (id) { return lines[id]; }); },
      lineCount: function () { return order.length; },
      held: function () { return held.slice(); },
      addHeld: function (items) {
        items.forEach(function (item) {
          if (!held.some(function (h) { return h.item_id === item.item_id; })) { held.push(item); }
        });
        edits += 1;
      },
      unresolved: function () {
        return held.filter(function (h) { return !lines[h.item_id]; });
      },
      unsent: function () { return order.length > 0; },
      version: function () { return edits; },
      payload: function () {
        return order.map(function (id) {
          var l = lines[id];
          return {item_id: id, kept: l.kept, is_approximate: l.is_approximate,
                  discarded: l.discarded, discard_reason: l.discarded ? l.discard_reason : null};
        });
      }
    };
  }

  // Runs steps one at a time in call order, so a scan during a submit waits for it.
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

  // Touches the server shift during a count whose taps never reach it. A touch needs a tap
  // since the last one, so an abandoned screen still lets the shift expire.
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

  var pure = {headTitle: headTitle, heldNote: heldNote, createKeepAlive: createKeepAlive, createCount: createCount, createGate: createGate, staleText: staleText,
              staleLine: staleLine, lineText: lineText, REASONS: REASONS, MESSAGES: MESSAGES,
              KEEPALIVE_MS: KEEPALIVE_MS};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');

  var count = null;        // the open count, or null
  var result = null;       // {applied, stale} shown after a submit
  var submitting = false;  // a submit is in flight; edits and a second Submit wait
  var submitId = null;     // {id, version}: reused for a repeat tap of an unchanged Submit
  var gate = createGate(); // scans and Submit run through it, one at a time
  var keepAlive = createKeepAlive({now: Date.now, everyMs: KEEPALIVE_MS, send: function () {
    S.read(cfg.keepAliveUrl, {});
  }});

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

  function fail(text) {
    S.feedback('error');
    S.showMessage(text, 'error');
  }

  function headNode() {
    var head = el('div', 'cnt-head');
    head.appendChild(el('div', 'cnt-title', headTitle(count)));
    head.appendChild(el('div', 'cnt-sub', plural(count.lineCount(), 'line') + ' counted'));
    return head;
  }

  // The shell's pad and choice list replace the panel; the header goes back on top.
  function withHead() {
    if (count) { panel.insertBefore(headNode(), panel.firstChild); }
  }

  function render() {
    panel.textContent = '';
    S.setFlagSubject(count ? {code: count.code, label: count.code + (count.name ? ' ' + count.name : '')}
                           : null);
    if (!count) { renderIdle(); return; }
    var box = el('div', 'cnt');
    box.appendChild(headNode());

    var usual = el('div', 'cnt-usual');
    usual.appendChild(button('Browse', 'cnt-item cnt-search', browse));
    usual.appendChild(button('Search', 'cnt-item cnt-search', search));
    count.usual.forEach(function (item) {
      var line = count.line(item.item_id);
      usual.appendChild(button(item.name, 'cnt-item' + (line ? ' done' : ''), function () {
        pickItem({id: item.item_id, name: item.name, pack_size: item.pack_size});
      }));
    });
    box.appendChild(usual);

    var open = count.unresolved();
    if (open.length) {
      box.appendChild(el('div', 'cnt-section', 'Last count: confirm or correct (' + open.length + ')'));
      open.forEach(function (h) { box.appendChild(openRow(h)); });
    }
    var done = count.lines();
    if (done.length) {
      box.appendChild(el('div', 'cnt-section', 'Counted'));
      done.forEach(function (l) { box.appendChild(lineRow(l)); });
    }
    var foot = el('div', 'cnt-foot');
    foot.appendChild(button('Submit count (' + plural(count.lineCount(), 'line') + ')',
                            'cnt-submit', submit));
    foot.appendChild(button('Cancel count', 'cnt-small cnt-cancel', cancelCount));
    box.appendChild(foot);
    panel.appendChild(box);
  }

  function packSizeOf(id) {
    var hit = count.held().filter(function (h) { return h.item_id === id; })[0];
    return hit && hit.pack_size ? hit.pack_size : null;
  }

  function openRow(h) {
    var row = el('div', 'cnt-row');
    var expected = count.expectedFor(h.item_id);
    row.appendChild(el('div', 'cnt-name', h.name));
    if (expected !== null) {
      row.appendChild(el('div', 'cnt-note', heldNote(expected, count.lastCountedFor(h.item_id))));
    }
    var actions = el('div', 'cnt-actions');
    actions.appendChild(button('Count', 'cnt-small', function () {
      pickItem({id: h.item_id, name: h.name, pack_size: packSizeOf(h.item_id)});
    }));
    if (expected !== null) {
      actions.appendChild(button('Matches', 'cnt-small', function () {
        edit(function () { count.setLine({id: h.item_id, name: h.name}, expected, false); });
      }));
    }
    actions.appendChild(button('None here', 'cnt-small', function () {
      edit(function () { count.markNone(h.item_id); });
    }));
    row.appendChild(actions);
    return row;
  }

  function lineRow(l) {
    var row = el('div', 'cnt-row');
    row.appendChild(el('div', 'cnt-name', l.name));
    row.appendChild(el('div', 'cnt-note', lineText(l)));
    var actions = el('div', 'cnt-actions');
    actions.appendChild(button('Recount', 'cnt-small', function () {
      pickItem({id: l.item_id, name: l.name, pack_size: packSizeOf(l.item_id)});
    }));
    actions.appendChild(button('None', 'cnt-small', function () {
      edit(function () { count.markNone(l.item_id); });
    }));
    actions.appendChild(button('Discard', 'cnt-small', function () { discard(l); }));
    if (l.discarded) {
      actions.appendChild(button('Clear discard', 'cnt-small', function () {
        edit(function () { count.setDiscard(l.item_id, 0, null); });
      }));
    }
    row.appendChild(actions);
    return row;
  }

  function renderIdle() {
    var box = el('div', 'cnt');
    if (result) {
      box.appendChild(el('div', 'cnt-title', 'Count saved: ' + plural(result.applied, 'line')));
      result.stale.forEach(function (name) {
        box.appendChild(el('div', 'cnt-stale', staleLine(name)));
      });
    }
    box.appendChild(el('div', 'cnt-step', MESSAGES.idle));
    panel.appendChild(box);
  }

  // Row buttons change the count only while no submit is in flight.
  function edit(change) {
    if (!count) { return; }
    if (submitting) { fail(MESSAGES.submitting); return; }
    change();
    S.showMessage('', 'info');
    render();
    refocus();
  }

  function pickItem(item) {
    if (!count) { return; }
    S.showMessage(item.name + ': how many?', 'info');
    S.numberPad({packSize: item.pack_size, allowAbout: true}, function (answer) {
      if (!answer) { S.showMessage('', 'info'); render(); refocus(); return; }
      edit(function () { count.setLine(item, answer.quantity, answer.about); });
      S.feedback('ok');
    });
    withHead();
  }

  function discard(l) {
    if (submitting) { fail(MESSAGES.submitting); return; }
    S.showMessage(l.name + ': how many thrown out?', 'info');
    S.numberPad({}, function (answer) {
      if (!answer) { S.showMessage('', 'info'); render(); refocus(); return; }
      S.showMessage('Why was ' + l.name + ' thrown out?', 'info');
      S.choose(REASONS, function (reason) {
        edit(function () { count.setDiscard(l.item_id, answer.quantity, reason); });
      });
      panel.appendChild(button('Back', 'choice', function () { render(); refocus(); }));
      withHead();
    });
    withHead();
  }

  function search() {
    S.askText('Item: part of the name', function (text) {
      if (text === null) { render(); refocus(); return; }
      S.read(cfg.findUrl, {q: text}).then(function (answer) {
        if (answer.need_name) { return; }
        if (!answer.ok) { fail(answer.message); render(); return; }
        if (!answer.items.length) { fail(MESSAGES.noItems); render(); return; }
        S.showMessage('', 'info');
        chooseItem(answer.items, 'Up to 20 shown. Search again to narrow.');
      });
    });
  }

  // Categories, then one category's items. Back from the items returns to the categories.
  function browse() {
    S.read(cfg.browseUrl, {}).then(function (answer) {
      if (answer.need_name) { return; }
      if (!answer.ok) { fail(answer.message); render(); return; }
      if (!answer.categories.length) { fail(MESSAGES.noItems); render(); return; }
      S.showMessage('', 'info');
      showCategories(answer.categories);
    });
  }

  function showCategories(categories) {
    S.choose(categories.map(function (c) { return {label: c.name, value: c}; }),
      function (category) { browseItems(category, categories); }, 'Pick a category.');
    panel.appendChild(button('Back', 'choice', function () { render(); refocus(); }));
    withHead();
    refocus();
  }

  function browseItems(category, categories) {
    S.read(cfg.browseUrl, {category_id: category.id}).then(function (answer) {
      if (answer.need_name) { return; }
      if (!answer.ok) { fail(answer.message); showCategories(categories); return; }
      if (!answer.items.length) { fail(MESSAGES.noItems); showCategories(categories); return; }
      S.choose(answer.items.map(function (item) { return {label: item.name, value: item}; }),
        function (item) { pickItem(item); }, category.name);
      panel.appendChild(button('Back', 'choice', function () { showCategories(categories); }));
      withHead();
      refocus();
    });
  }

  function chooseItem(items, note) {
    S.choose(items.map(function (item) { return {label: item.name, value: item}; }),
      function (item) { pickItem(item); }, note);
    panel.appendChild(button('Back', 'choice', function () { render(); refocus(); }));
    withHead();
    refocus();
  }

  // Discards the unsent count on this screen. The server keeps an unsubmitted count
  // harmlessly: it neither changes stock nor ends blindness, and a new start opens another.
  function cancelCount() {
    if (!count) { return; }
    if (submitting) { fail(MESSAGES.submitting); return; }
    if (!root.confirm(MESSAGES.cancelAsk)) { return; }
    count = null;
    submitId = null;
    S.showMessage('', 'info');
    render();
    refocus();
  }

  function startedCount(answer) {
    count = createCount(answer);
    keepAlive.reset();
    result = null;
    submitId = null;
    S.showMessage('', 'info');
    S.feedback('ok');
    render();
    return {ok: true};
  }

  function offerCreate(answer) {
    var where = answer.home ? 'It goes in ' + answer.home + '.'
      : 'This scanner has no home container, so it goes at the top level.';
    S.choose(answer.kinds.map(function (k) { return {label: k.name, value: k.id}; }),
      function (kindId) { createThenStart(answer.code, kindId); },
      answer.code + ' is a new label. Pick what it is. ' + where);
    panel.appendChild(button('Cancel', 'choice', function () { render(); refocus(); }));
    refocus();
  }

  function createThenStart(code, kindId) {
    return gate.run(function () {
      if (count) { return {ok: false}; }
      return S.write(cfg.containerUrl, {label_code: code, kind_id: kindId}).then(function (a) {
        if (a.need_name || a.fatal) { return {ok: false}; }
        if (!a.ok) { fail(a.message); render(); return {ok: false}; }
        return startWrite(code);
      });
    });
  }

  function startWrite(code) {
    return S.write(cfg.startUrl, {container_code: code}).then(function (answer) {
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (!answer.ok) { fail(answer.message); return {ok: false}; }
      if (answer.unused) { offerCreate(answer); return {ok: false}; }
      return startedCount(answer);
    });
  }

  function scanStart(code) {
    if (!CONTAINER_CODE.test(code)) { fail(MESSAGES.startFirst); return Promise.resolve({ok: false}); }
    return startWrite(code);
  }

  // An item scan opens the pad and finishes nothing, so it never reports ok. That keeps
  // the shell's repeat guard empty and lets the same item be counted twice in a row.
  function scanInCount(code) {
    return S.read(cfg.findUrl, {code: code}).then(function (answer) {
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (!answer.ok) { fail(answer.message); return {ok: false}; }
      if (!count) { return {ok: false}; }
      if (submitting) { fail(MESSAGES.submitting); return {ok: false}; }
      if (answer.items.length === 1) { pickItem(answer.items[0]); } else {
        chooseItem(answer.items, 'Two items share that barcode. Tap the one in your hand.');
      }
      return {ok: false};
    });
  }

  function onScan(code) {
    return gate.run(function () {
      return count ? scanInCount(code) : scanStart(code);
    }).catch(function () {
      fail('That scan failed. Scan it again.');
      return {ok: false};
    });
  }

  function submit() {
    if (!count || submitting) { if (submitting) { S.showMessage(MESSAGES.submitting, 'info'); } return; }
    var open = count.unresolved();
    if (open.length) {
      fail('Confirm or correct first: ' + open.map(function (h) { return h.name; }).join(', '));
      render();
      return;
    }
    if (!count.lineCount()) { fail(MESSAGES.nothingCounted); return; }
    submitting = true;
    var mine = count;
    var lines = count.payload();
    if (!submitId || submitId.version !== count.version()) {
      submitId = {id: S.requestId(), version: count.version()};
    }
    gate.run(function () {
      return S.write(cfg.submitUrl, {count_id: mine.id, lines: lines}, submitId.id).then(
        function (answer) { submitting = false; finish(answer); });
    }).catch(function () { submitting = false; });
    refocus();
  }

  function finish(answer) {
    if (answer.need_name || answer.fatal || answer.cancelled) { return; }
    submitId = null;
    if (!answer.ok && answer.unresolved) {
      count.addHeld(answer.unresolved_items || []);
      fail(answer.message);
      render();
      return;
    }
    if (!answer.ok && answer.finished) {
      count = null;
      result = null;
      S.feedback('error');
      S.showMessage(MESSAGES.alreadySaved, 'info');
      render();
      refocus();
      return;
    }
    if (!answer.ok) { fail(answer.message); return; }
    var counted = count.code;
    count = null;
    result = {applied: answer.applied, stale: answer.stale || []};
    if (result.stale.length) { fail(staleText(result.stale)); } else {
      S.feedback('ok');
      S.showMessage('', 'info');
    }
    S.offerUndo({kind: 'count', name: counted, onUndone: function () {
      result = null;
      render();
    }});
    render();
    refocus();
  }

  panel.addEventListener('click', function () { if (count) { keepAlive.tap(); } });
  setInterval(function () { if (count) { keepAlive.check(); } }, 60 * 1000);
  S.onActivity(function () { if (count) { keepAlive.tap(); } });
  S.holdLeave(function () { return !!count && count.unsent(); });
  S.start({station: 'COUNT', onScan: onScan});
  render();
})(typeof window !== 'undefined' ? window : globalThis);
