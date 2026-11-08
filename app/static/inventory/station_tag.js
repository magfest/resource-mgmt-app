// Tag station: scan a FEST tag, name the unit it belongs on, tap the match. The unit already
// has a name but no number; linking queues the combined label. Text from the server or from
// people goes in with textContent.
(function (root) {
  'use strict';

  var MESSAGES = {noMatch: 'No untagged unit has that name.',
                  saving: 'Wait for the link to finish.'};

  if (typeof document === 'undefined') { return; }

  var S = root.InvScan;
  var cfg = document.currentScript.dataset;
  var panel = document.getElementById('panel');
  var field = document.getElementById('scan-input');
  // One object per scan. A callback that finds a newer object in `current` drops its work,
  // so a scan during the name prompt or the choices starts over and links nothing.
  var current = null;
  var saving = false;  // a tag/link request is in flight; scans wait so its result is shown
  var note = null;   // {text, waiting, tag} outcome of the last link, shown while scanning

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function refocus() { field.focus(); }

  function renderScanning() {
    panel.textContent = '';
    var box = el('div', 'tag-station');
    if (note) {
      box.appendChild(el('div', 'tag-done', note.text));
      if (note.waiting) {
        box.appendChild(el('div', 'tag-wait', 'Waiting for printer'));
        box.appendChild(el('div', 'hint', 'The print station has not picked up ' + note.tag));
      }
    }
    box.appendChild(el('div', 'tag-step', 'Scan the FEST tag'));
    panel.appendChild(box);
  }

  function backToScanning() {
    renderScanning();
    refocus();
  }

  function fail(text) {
    S.feedback('error');
    S.showMessage(text, 'error');
  }

  function askName(mine) {
    S.showMessage('Tag ' + mine.tag, 'info');
    S.askText('Unit name', function (text) {
      if (current !== mine) { return; }
      if (text === null || text === '') { S.showMessage('', 'info'); backToScanning(); return; }
      S.read(cfg.candidatesUrl, {q: text}).then(function (answer) {
        if (current !== mine || answer.need_name || answer.fatal) { return; }
        if (!answer.ok) { fail(answer.message); backToScanning(); return; }
        if (!answer.units.length) { fail(MESSAGES.noMatch); backToScanning(); return; }
        showChoices(mine, answer.units);
      });
    });
  }

  function showChoices(mine, units) {
    S.showMessage('Tag ' + mine.tag, 'info');
    S.choose(units.map(function (unit) {
      return {label: unit.name, lines: [unit.name, unit.model, unit.details], value: unit};
    }), function (unit) { link(mine, unit); });
    var cancel = el('button', 'choice', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () {
      if (current !== mine) { return; }
      S.showMessage('', 'info');
      backToScanning();
    });
    panel.appendChild(cancel);
    refocus();
  }

  function link(mine, unit) {
    if (current !== mine || mine.linking) { return; }
    mine.linking = true;
    saving = true;
    S.write(cfg.linkUrl, {unit_id: unit.id, tag: mine.tag}).then(function (answer) {
      saving = false;
      if (answer.need_name || answer.fatal || answer.cancelled) { mine.linking = false; return; }
      if (!answer.ok) {
        if (current === mine) { fail(answer.message); backToScanning(); }
        return;
      }
      var name = answer.name || unit.name;
      var text = mine.tag + ' is now ' + name;
      note = {text: text, waiting: S.printerWaiting(answer), tag: mine.tag};
      S.setFlagSubject({unitId: unit.id, label: mine.tag});
      S.feedback('ok');
      S.showMessage(text, 'ok');
      S.offerUndo({kind: 'tag', name: mine.tag, url: cfg.undoUrl, payload: {unit_id: unit.id},
                   onUndone: function () { note = null; renderScanning(); }});
      renderScanning();
      refocus();
    });
  }

  function onScan(code) {
    if (saving) { fail(MESSAGES.saving); return Promise.resolve({ok: false}); }
    var mine = {tag: code, linking: false};
    current = mine;
    note = null;
    S.setFlagSubject(null);
    S.showMessage('', 'info');
    return S.read(cfg.checkUrl, {tag: code}).then(function (answer) {
      if (current !== mine || answer.need_name || answer.fatal) { return {ok: false}; }
      if (!answer.ok) { fail(answer.message); renderScanning(); return {ok: false}; }
      S.feedback('ok');
      askName(mine);
      return {ok: true};
    });
  }

  S.start({station: 'TAG', onScan: onScan});
  renderScanning();
})(typeof window !== 'undefined' ? window : globalThis);
