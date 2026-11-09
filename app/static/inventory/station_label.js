// Print and reprint a label from the Look up station. Loaded before station_lookup.js
// and exposed as window.InvLabel. All text from the server or from people goes in with textContent.
(function (root) {
  'use strict';

  var MESSAGES = {
    noItems: 'No serialized item matches that.',
    notSaved: 'Not saved.'
  };

  // Quick reprint is offered only while the last label's number is still the asset's number.
  function layout(answer) {
    if (answer.kind !== 'UNIT') { return {quick: false, current: 'Print label', changed: false}; }
    var quick = answer.has_history === true && answer.quick_reprint === true;
    return {
      quick: quick,
      current: quick ? 'Print current details' : (answer.has_history ? 'Print current details' : 'Print label'),
      changed: answer.has_history === true && answer.details_changed === true
    };
  }

  var pure = {layout: layout};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var epoch = 0;   // bumped by reset(); a late answer for an older screen shows no panel

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

  // Called when another code is scanned: whatever the label screen was doing is dropped.
  function reset() { epoch += 1; }

  function showResult(answer, back, text) {
    panel.textContent = '';
    var box = el('div', 'lbl');
    var waiting = S.printerWaiting(answer);
    box.appendChild(el('p', 'lbl-line', waiting ? answer.code + ' is queued' : answer.code + ' sent to printer'));
    if (waiting) {
      box.appendChild(el('p', 'lbl-wait', 'Waiting for printer'));
      box.appendChild(el('p', 'hint', 'The print station has not picked up ' + answer.code));
    }
    if (text) { box.appendChild(el('p', 'hint', text)); }
    box.appendChild(button('Back', 'choice', back));
    panel.appendChild(box);
  }

  // One tap sends one write. `mine.ids` holds one request id per mode for this opening of the
  // choices, so a repeat tap sends the same request and the server answers it once.
  function sender(target, mode, mine, back, after) {
    return function () {
      if (mine.sending) { return; }
      mine.sending = true;
      Array.prototype.forEach.call(panel.querySelectorAll('button'), function (b) { b.disabled = true; });
      var body = {code: target.code, mode: mode};
      if (target.unitId != null) { body.unit_id = target.unitId; }
      if (!mine.ids[mode]) { mine.ids[mode] = S.requestId(); }
      after(S.write(cfg.printUrl, body, mine.ids[mode]), mine);
    };
  }

  function finish(promise, mine, back) {
    promise.then(function (answer) {
      if (answer.need_name || answer.fatal) { return; }
      if (answer.ok) {
        if (mine.epoch === epoch) {
          S.feedback('ok');
          S.showMessage('', 'info');
          showResult(answer, back);
        } else if (!S.currentMessage()) {
          // A newer scan owns the screen. Its own message, an error above all, stays.
          S.showMessage(answer.code + (S.printerWaiting(answer) ? ' is queued' : ' sent to printer'),
            'info');
        }
        return;
      }
      mine.sending = false;
      S.feedback('error');
      S.showMessage(answer.message, 'error');
      if (mine.epoch === epoch) {
        Array.prototype.forEach.call(panel.querySelectorAll('button'), function (b) { b.disabled = false; });
      }
    });
  }

  function render(target, answer, back) {
    var model = layout(answer);
    var mine = {sending: false, epoch: epoch, ids: {}};
    var date = 'ORIGINAL_DATE';
    panel.textContent = '';
    var box = el('div', 'lbl');
    box.appendChild(el('h2', null, answer.title + (answer.code ? ' ' + answer.code : '')));
    if (answer.inactive) { box.appendChild(el('p', 'hint', 'Inactive. It still prints.')); }
    if (model.changed && !model.quick) {
      box.appendChild(el('p', 'lbl-warn', 'The number changed since the last label. Print its current details.'));
    } else if (model.changed) {
      box.appendChild(el('p', 'lbl-warn', 'Details changed since the last label.'));
    }
    var toggles = [];
    var quickButton = null;
    if (model.quick) {
      var original = answer.original_date ? 'Original date (' + answer.original_date + ')' : 'Original date';
      [['ORIGINAL_DATE', original], ['TODAY', 'Today']].forEach(function (opt) {
        var b = button(opt[1], 'choice' + (opt[0] === date ? ' on' : ''), function () {
          date = opt[0];
          toggles.forEach(function (t) {
            var on = t.mode === date;
            t.node.className = 'choice' + (on ? ' on' : '');
            t.node.setAttribute('aria-pressed', on ? 'true' : 'false');
          });
        });
        b.setAttribute('aria-pressed', opt[0] === date ? 'true' : 'false');
        toggles.push({mode: opt[0], node: b});
      });
      quickButton = button('Quick reprint', 'choice primary', function () {
        sender(target, date, mine, back, function (p) { finish(p, mine, back); })();
      });
      box.appendChild(quickButton);
      toggles.forEach(function (t) { box.appendChild(t.node); });
    }
    box.appendChild(button(model.current, model.quick ? 'choice' : 'choice primary',
      sender(target, 'CURRENT_DETAILS', mine, back, function (p) { finish(p, mine, back); })));
    box.appendChild(button('Back', 'choice', back));
    panel.appendChild(box);
  }

  // target is {code, unitId}. back() redraws the card the person came from.
  function show(target, back) {
    reset();
    var seen = epoch;
    var body = {code: target.code};
    if (target.unitId != null) { body.unit_id = target.unitId; }
    return S.read(cfg.optionsUrl, body).then(function (answer) {
      if (answer.need_name || answer.fatal || seen !== epoch) { return; }
      if (!answer.ok) {
        S.feedback('error');
        S.showMessage(answer.message, 'error');
        return;
      }
      S.showMessage('', 'info');
      render(target, answer, back);
    });
  }

  // An unknown asset tag: pick the item, optionally type a serial, then create and print.
  function newTag(code, back) {
    reset();
    var seen = epoch;
    S.askText('Item: part of the name', function (text) {
      if (text === null) { back(); return; }
      S.read(cfg.itemsUrl, {q: text}).then(function (answer) {
        if (answer.need_name || answer.fatal || seen !== epoch) { return; }
        if (!answer.ok) {
          S.feedback('error');
          S.showMessage(answer.message, 'error');
          back();
          return;
        }
        if (!answer.items.length) {
          S.feedback('error');
          S.showMessage(MESSAGES.noItems, 'error');
          back();
          return;
        }
        S.showMessage('', 'info');
        S.choose(answer.items.map(function (item) { return {label: item.name, value: item}; }),
          function (item) { askSerial(code, item, back, seen); },
          'Up to 20 shown. Search again to narrow.');
        panel.appendChild(button('Back', 'choice', back));
      });
    });
  }

  function askSerial(code, item, back, seen) {
    S.askText('Serial (leave blank to skip)', function (serial) {
      if (serial === null) { back(); return; }
      var body = {tag: code, item_id: item.id, serial: serial || null};
      if (seen === epoch) {
        panel.textContent = '';
        panel.appendChild(el('p', 'hint', 'Saving...'));
      }
      S.write(cfg.newTagUrl, body).then(function (answer) {
        if (answer.need_name || answer.fatal) { return; }
        if (answer.ok) {
          S.feedback('ok');
          S.showMessage('', 'info');
          if (seen === epoch) { showResult(answer, back, 'New asset created.'); }
          return;
        }
        S.feedback('error');
        S.showMessage(answer.already ? 'Already received as ' + answer.already.code : answer.message, 'error');
        if (seen === epoch) { back(); }
      });
    });
  }

  root.InvLabel = {show: show, newTag: newTag, reset: reset};
})(typeof window !== 'undefined' ? window : globalThis);
