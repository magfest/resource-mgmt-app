// Move station: scan where things are going, then scan each tote or asset placed there.
// "Move supplies" moves loose items from one place to the destination by quantity.
// The destination lives only on this page; a reload starts with none. Move has no undo,
// so the next move is the fix. A sign-in card never reaches this station, because the
// shell signs it in first. All text from the server goes in with textContent.
(function (root) {
  'use strict';

  var MESSAGES = {
    scanDestination: "Scan where it's going: a pallet, a tote or a location.",
    scanEach: 'Scan each thing as you put it there.',
    scanSource: 'Scan where they are coming from.',
    tapItem: 'Tap the item, or scan its barcode.',
    noItems: 'No item matches that.',
    failed: 'That scan failed. Scan it again.'
  };
  var CONTAINER_CODE = /^\s*C-?\d+\s*$/i;

  // Runs steps one at a time in call order, so a tap during a scan waits for it.
  function createGate() {
    var tail = null;
    return {
      run: function (fn) {
        var result = (tail || Promise.resolve()).then(fn);
        var mine = result.then(function () {}, function () {});
        tail = mine;
        mine.then(function () { if (tail === mine) { tail = null; } });
        return result;
      }
    };
  }

  var pure = {MESSAGES: MESSAGES, createGate: createGate};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');

  var destination = null;  // {code, title}, or null before the first scan
  var source = null;       // {code, title, usual} while moving supplies, else null
  var picking = false;     // Move supplies was tapped; the next scan names the source
  var gate = createGate();

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

  function refocus() { if (field) { field.focus(); } }

  function fail(text) {
    S.feedback('error');
    S.showMessage(text, 'error');
  }

  function destinationCode() { return destination ? destination.code : null; }

  function headNode() {
    var head = el('div', 'cnt-head');
    if (source) {
      head.appendChild(el('div', 'cnt-title', 'From ' + source.code + ' to ' + destination.code));
    } else if (destination) {
      head.appendChild(el('div', 'cnt-sub', 'Going to'));
      head.appendChild(el('div', 'cnt-title', destination.title));
    }
    return head;
  }

  // The shell's pad and choice list replace the panel; the header goes back on top.
  function withHead() {
    if (destination) { panel.insertBefore(headNode(), panel.firstChild); }
  }

  function render() {
    panel.textContent = '';
    S.setFlagSubject(destination ? {code: destination.code, label: destination.title} : null);
    var box = el('div', 'cnt');
    if (!destination) {
      box.appendChild(el('div', 'cnt-step', MESSAGES.scanDestination));
    } else if (picking) {
      box.appendChild(el('div', 'cnt-step', MESSAGES.scanSource));
      box.appendChild(button('Cancel', 'cnt-small cnt-cancel', backToDestination));
    } else if (source) {
      renderItems(box);
    } else {
      box.appendChild(headNode());
      box.appendChild(el('div', 'cnt-step', MESSAGES.scanEach));
      var foot = el('div', 'cnt-foot');
      foot.appendChild(button('Move supplies', 'cnt-submit', function () {
        picking = true;
        S.showMessage('', 'info');
        render();
        refocus();
      }));
      foot.appendChild(button('New destination', 'cnt-small', function () {
        destination = null;
        S.showMessage('', 'info');
        render();
        refocus();
      }));
      box.appendChild(foot);
    }
    panel.appendChild(box);
  }

  function renderItems(box) {
    box.appendChild(headNode());
    var usual = el('div', 'cnt-usual');
    usual.appendChild(button('Browse', 'cnt-item cnt-search', browse));
    usual.appendChild(button('Search', 'cnt-item cnt-search', search));
    source.usual.forEach(function (item) {
      usual.appendChild(button(item.name, 'cnt-item', function () {
        pickItem({id: item.item_id, name: item.name, pack_size: item.pack_size});
      }));
    });
    box.appendChild(usual);
    var foot = el('div', 'cnt-foot');
    foot.appendChild(button('Cancel', 'cnt-small cnt-cancel', backToDestination));
    box.appendChild(foot);
  }

  function backToDestination() {
    source = null;
    picking = false;
    S.showMessage('', 'info');
    render();
    refocus();
  }

  function pickItem(item) {
    if (!source) { return; }
    S.showMessage('', 'info');
    S.numberPad({packSize: item.pack_size || null, allowAbout: false,
                 lines: [item.name + ': how many are you moving?']}, function (answer) {
      if (!answer) { render(); refocus(); return; }
      moveSupplies(item, answer.quantity);
    });
    withHead();
  }

  function moveSupplies(item, quantity) {
    return gate.run(function () {
      if (!source) { return {ok: false}; }
      var body = {source_code: source.code, destination_code: destination.code,
                  item_id: item.id, quantity: quantity};
      return S.write(cfg.suppliesUrl, body).then(function (answer) {
        if (answer.need_name || answer.fatal) { return {ok: false}; }
        // A refusal keeps the source so a mistyped quantity needs only another tap.
        if (!answer.ok) { render(); fail(answer.message); refocus(); return {ok: false}; }
        source = null;
        picking = false;
        render();
        S.feedback('ok');
        S.showMessage(answer.message, 'info');
        refocus();
        return {ok: false};
      });
    }).catch(function () { fail(MESSAGES.failed); });
  }

  function chooseItem(items, note) {
    S.choose(items.map(function (item) { return {label: item.name, value: item}; }),
      function (item) { pickItem(item); }, note);
    panel.appendChild(button('Back', 'choice', function () { render(); refocus(); }));
    withHead();
    refocus();
  }

  function search() {
    S.askText('Item: part of the name', function (text) {
      if (text === null) { render(); refocus(); return; }
      gate.run(function () {
        return S.read(cfg.findUrl, {q: text}).then(function (answer) {
          if (answer.need_name || answer.fatal) { return; }
          if (!answer.ok) { fail(answer.message); render(); return; }
          if (!answer.items.length) { fail(MESSAGES.noItems); render(); return; }
          S.showMessage('', 'info');
          chooseItem(answer.items, 'Up to 20 shown. Search again to narrow.');
        });
      });
    });
  }

  // Categories, then one category's items. Back from the items returns to the categories.
  function browse() {
    gate.run(function () {
      return S.read(cfg.browseUrl, {}).then(function (answer) {
        if (answer.need_name || answer.fatal) { return; }
        if (!answer.ok) { fail(answer.message); render(); return; }
        if (!answer.categories.length) { fail(MESSAGES.noItems); render(); return; }
        S.showMessage('', 'info');
        showCategories(answer.categories);
      });
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
    gate.run(function () {
      return S.read(cfg.browseUrl, {category_id: category.id}).then(function (answer) {
        if (answer.need_name || answer.fatal) { return; }
        if (!answer.ok) { fail(answer.message); showCategories(categories); return; }
        if (!answer.items.length) { fail(MESSAGES.noItems); showCategories(categories); return; }
        S.choose(answer.items.map(function (item) { return {label: item.name, value: item}; }),
          function (item) { pickItem(item); }, category.name);
        panel.appendChild(button('Back', 'choice', function () { showCategories(categories); }));
        withHead();
        refocus();
      });
    });
  }

  function offerCreate(answer) {
    S.choose(answer.kinds.map(function (k) { return {label: k.name, value: k.id}; }),
      function (kindId) { createLabel(answer.code, kindId); },
      answer.code + ' is a new label. Pick what it is. ' + answer.where);
    panel.appendChild(button('Cancel', 'choice', function () { render(); refocus(); }));
    withHead();
    refocus();
  }

  function createLabel(code, kindId) {
    return gate.run(function () {
      return S.write(cfg.createUrl, {label_code: code, kind_id: kindId,
                                     destination_code: destinationCode()}).then(placed);
    }).catch(function () { fail(MESSAGES.failed); });
  }

  // Only a real change reports ok, so the shell's repeat guard records real moves.
  function placed(answer) {
    if (answer.need_name || answer.fatal) { return {ok: false}; }
    if (!answer.ok) { fail(answer.message); render(); return {ok: false}; }
    if (answer.unused) { offerCreate(answer); return {ok: false}; }
    S.feedback('ok');
    if (answer.destination) {
      destination = answer.destination;
      render();
      S.showMessage(answer.message, 'info');
      return {ok: true};
    }
    S.showMessage(answer.message, 'info');
    render();
    return {ok: !!answer.moved};
  }

  function scanSource(code) {
    return S.read(cfg.sourceUrl, {code: code, destination_code: destination.code}).then(
      function (answer) {
        if (answer.need_name || answer.fatal) { return {ok: false}; }
        if (!answer.ok) { fail(answer.message); return {ok: false}; }
        source = {code: answer.source.code, title: answer.source.title, usual: answer.usual || []};
        picking = false;
        S.feedback('ok');
        S.showMessage('', 'info');
        render();
        return {ok: false};
      });
  }

  // A barcode names an item to move; a container label has no meaning at this step.
  function scanItem(code) {
    if (CONTAINER_CODE.test(code)) { fail(MESSAGES.tapItem); return Promise.resolve({ok: false}); }
    return S.read(cfg.findUrl, {code: code}).then(function (answer) {
      if (answer.need_name || answer.fatal) { return {ok: false}; }
      if (!answer.ok) { fail(answer.message); return {ok: false}; }
      if (answer.items.length === 1) { pickItem(answer.items[0]); } else {
        chooseItem(answer.items, 'Two items share that barcode. Tap the one in your hand.');
      }
      return {ok: false};
    });
  }

  function onScan(code) {
    return gate.run(function () {
      if (picking) { return scanSource(code); }
      if (source) { return scanItem(code); }
      return S.write(cfg.scanUrl, {code: code, destination_code: destinationCode()}).then(placed);
    }).catch(function () {
      fail(MESSAGES.failed);
      return {ok: false};
    });
  }

  S.start({station: 'MOVE', onScan: onScan});
  render();
})(typeof window !== 'undefined' ? window : globalThis);
