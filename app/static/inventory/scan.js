// Shared scan shell for CT60 stations. Exposes window.InvScan.
// A CT60 XP reports keydown as "Unidentified" and may deliver a whole code in one
// `input` event, so capture reads the field value on `input` and not the key events.
// All text from people goes in with textContent or esc().
(function (root) {
  'use strict';

  var IDLE_MS = 80;          // quiet time after the last input that ends a code
  var REPEAT_MS = 5000;      // the same code inside this window posts nothing
  var REFOCUS_MS = 250;
  var REQUEST_TIMEOUT_MS = 15000;

  // The error vocabulary every station shares. Wording matches the design spec.
  var MESSAGES = {
    unknown: 'Not a MAGFest code: scan the white asset tag',
    ambiguous: 'Two units match: tap the one in your hand',
    repeat: function (seconds) {
      return 'Already done, ' + seconds + (seconds === 1 ? ' second' : ' seconds') + ' ago';
    },
    wrongKind: function (what, checks) {
      return 'This is ' + what + '; this station checks ' + checks;
    },
    otherStation: function (what, stationName) {
      return 'This is ' + what + '. Switch this scanner to ' + stationName + '?';
    },
    offline: 'Could not reach the server.',
    notSaved: 'NOT SAVED'
  };

  function esc(text) {
    return String(text == null ? '' : text).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }

  // Turns `input` events into whole codes. A code ends on Enter, on a newline inside
  // the value, or after idleMs with no further input. Timers are injected for testing.
  function createCapture(opts) {
    var pending = '';
    var timer = null;
    var idleMs = opts.idleMs == null ? IDLE_MS : opts.idleMs;

    function flush() {
      if (timer !== null) { opts.cancel(timer); timer = null; }
      var code = pending.trim();
      pending = '';
      opts.clear();
      if (code) { opts.deliver(code); }
    }
    return {
      input: function (value) {
        var cut = /[\r\n]/.exec(value);
        if (cut) {
          pending = value.slice(0, cut.index);
          flush();
          return;
        }
        pending = value;
        if (timer !== null) { opts.cancel(timer); }
        timer = opts.schedule(flush, idleMs);
      },
      enter: function (value) {
        if (typeof value === 'string') { pending = value; }
        flush();
      }
    };
  }

  // Remembers the last accepted code. check() returns whole seconds since the same
  // code was accepted inside the window, or null when the scan should go through.
  function createRepeatGuard(windowMs) {
    var last = {code: null, at: 0};
    return {
      check: function (code, nowMs) {
        if (last.code === code && nowMs - last.at < windowMs) {
          return Math.max(1, Math.floor((nowMs - last.at) / 1000));
        }
        last = {code: code, at: nowMs};
        return null;
      },
      forget: function (code) {
        if (last.code === code) { last = {code: null, at: 0}; }
      }
    };
  }

  var pure = {createCapture: createCapture, createRepeatGuard: createRepeatGuard,
              MESSAGES: MESSAGES, esc: esc, IDLE_MS: IDLE_MS, REPEAT_MS: REPEAT_MS};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var body = document.getElementById('scan-body');
  var field = document.getElementById('scan-input');
  var msg = document.getElementById('msg');
  var panel = document.getElementById('panel');
  var paused = document.getElementById('paused');
  var doneBtn = document.getElementById('done-btn');
  var guard = createRepeatGuard(REPEAT_MS);
  var asking = false;       // an askText field owns focus
  var blocked = false;      // a write failed; scanning waits for Try again
  var chain = Promise.resolve();
  var audio = null;

  function newId() {
    if (root.crypto && root.crypto.randomUUID) { return root.crypto.randomUUID(); }
    return 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  // Resolves with the parsed JSON, including ok:false answers. Rejects when the
  // request fails, times out, or the answer is not JSON from a working server.
  function post(path, payload) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
    return fetch(path, {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    }).then(function (r) {
      clearTimeout(timer);
      if (r.status >= 500) { throw new Error('server'); }
      return r.json();
    }, function (err) { clearTimeout(timer); throw err; });
  }

  function clearPanel() { panel.textContent = ''; }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = text; }
    return node;
  }

  function showMessage(text, tone) {
    msg.textContent = text || '';
    msg.className = 'msg ' + (tone || 'info');
  }

  function showPanel(html) { panel.innerHTML = html; }

  function beep(freq, ms, when) {
    try {
      var Ctx = root.AudioContext || root.webkitAudioContext;
      if (!Ctx) { return; }
      audio = audio || new Ctx();
      if (audio.state === 'suspended') { audio.resume(); }
      var osc = audio.createOscillator();
      var gain = audio.createGain();
      osc.frequency.value = freq;
      gain.gain.value = 0.15;
      osc.connect(gain);
      gain.connect(audio.destination);
      var start = audio.currentTime + (when || 0);
      osc.start(start);
      osc.stop(start + ms / 1000);
    } catch (e) { /* sound is a nicety; the flash and vibration still happen */ }
  }

  function feedback(kind) {
    var ok = kind === 'ok';
    body.classList.remove('flash-ok', 'flash-error');
    body.classList.add(ok ? 'flash-ok' : 'flash-error');
    setTimeout(function () { body.classList.remove('flash-ok', 'flash-error'); }, 250);
    if (navigator.vibrate) { navigator.vibrate(ok ? 60 : [200, 100, 200]); }
    if (ok) { beep(880, 80); } else { beep(220, 200); beep(220, 200, 0.3); }
  }

  function showNotSaved(retry) {
    blocked = true;
    clearPanel();
    var box = el('div', 'not-saved');
    box.appendChild(el('h2', null, MESSAGES.notSaved));
    box.appendChild(el('p', null, MESSAGES.offline + ' This was not saved. Try again before scanning more.'));
    var again = el('button', 'choice', 'Try again');
    again.addEventListener('click', function () { blocked = false; clearPanel(); retry(); });
    box.appendChild(again);
    panel.appendChild(box);
    feedback('error');
  }

  function send(path, payload) {
    return post(path, payload).catch(function () {
      return new Promise(function (resolve) {
        showNotSaved(function () { resolve(send(path, payload)); });
      });
    });
  }

  // Writes go one at a time in call order. A retry reuses the request id, so the
  // server stores the answer once.
  function write(path, data) {
    var payload = Object.assign({}, data, {client_request_id: newId()});
    var result = chain.then(function () { return send(path, payload); });
    chain = result.then(function () {}, function () {});
    return result;
  }

  function read(path, data) {
    return post(path, data || {}).catch(function () {
      return {ok: false, message: MESSAGES.offline};
    });
  }

  function choose(options, onPick) {
    clearPanel();
    options.forEach(function (option) {
      var label = typeof option === 'string' ? option : option.label;
      var value = typeof option === 'string' ? option : option.value;
      var button = el('button', 'choice', label);
      button.type = 'button';
      button.addEventListener('click', function () { onPick(value); });
      panel.appendChild(button);
    });
  }

  function focusCapture() { if (!asking) { field.focus(); } }

  function askText(label, onDone) {
    asking = true;
    paused.hidden = true;
    clearPanel();
    var wrap = el('form', 'text-ask');
    var name = el('label', null, label);
    var input = el('input');
    input.type = 'text';
    input.autocomplete = 'off';
    name.appendChild(input);
    var ok = el('button', 'choice', 'OK');
    ok.type = 'submit';
    var cancel = el('button', 'choice', 'Cancel');
    cancel.type = 'button';
    wrap.appendChild(name);
    wrap.appendChild(ok);
    wrap.appendChild(cancel);
    panel.appendChild(wrap);
    function close(value) {
      asking = false;
      clearPanel();
      focusCapture();
      onDone(value);
    }
    wrap.addEventListener('submit', function (e) { e.preventDefault(); close(input.value.trim()); });
    cancel.addEventListener('click', function () { close(null); });
    input.focus();
  }

  // Calls onDone({quantity, entered, packs, about}) or onDone(null) on Cancel.
  // quantity is in base units: entered times packSize when Packs is on.
  function numberPad(opts, onDone) {
    var digits = '';
    var packs = false;
    var about = false;
    clearPanel();
    var pad = el('div', 'pad');
    var readout = el('div', 'readout', '0');
    pad.appendChild(readout);
    function refresh() {
      var text = digits || '0';
      if (packs) { text += ' packs of ' + opts.packSize; }
      if (about) { text = 'about ' + text; }
      readout.textContent = text;
    }
    var keys = el('div', 'keys');
    function key(label, handler) {
      var b = el('button', null, label);
      b.type = 'button';
      b.addEventListener('click', handler);
      keys.appendChild(b);
    }
    ['7', '8', '9', '4', '5', '6', '1', '2', '3'].forEach(function (d) {
      key(d, function () { if (digits.length < 7) { digits += d; refresh(); } });
    });
    key('Back', function () { digits = digits.slice(0, -1); refresh(); });
    key('0', function () { if (digits && digits.length < 7) { digits += '0'; refresh(); } });
    key('OK', function () {
      if (!digits) { return; }
      var entered = parseInt(digits, 10);
      onDone({quantity: packs ? entered * opts.packSize : entered, entered: entered,
              packs: packs, about: about});
    });
    pad.appendChild(keys);
    var extras = el('div', 'actions');
    if (opts.packSize) {
      var p = el('button', null, 'Packs of ' + opts.packSize);
      p.type = 'button';
      p.addEventListener('click', function () { packs = !packs; refresh(); });
      extras.appendChild(p);
    }
    if (opts.allowAbout) {
      var a = el('button', null, 'About');
      a.type = 'button';
      a.addEventListener('click', function () { about = !about; refresh(); });
      extras.appendChild(a);
    }
    var cancel = el('button', null, 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', function () { onDone(null); });
    extras.appendChild(cancel);
    pad.appendChild(extras);
    panel.appendChild(pad);
  }

  function start(config) {
    var capture = createCapture({
      deliver: function (code) {
        if (blocked) { feedback('error'); return; }
        var ago = guard.check(code, Date.now());
        if (ago !== null) { showMessage(MESSAGES.repeat(ago), 'info'); return; }
        config.onScan(code);
      },
      schedule: function (fn, ms) { return setTimeout(fn, ms); },
      cancel: function (handle) { clearTimeout(handle); },
      clear: function () { field.value = ''; }
    });
    field.addEventListener('input', function () { capture.input(field.value); });
    field.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); capture.enter(field.value); }
    });
    field.addEventListener('blur', function () { if (!asking) { paused.hidden = false; } });
    field.addEventListener('focus', function () { paused.hidden = true; });
    paused.addEventListener('click', focusCapture);
    document.addEventListener('click', focusCapture);
    setInterval(function () { if (!asking && document.activeElement !== field) { field.focus(); } },
                REFOCUS_MS);
    if (doneBtn) {
      doneBtn.addEventListener('click', function () {
        read(body.dataset.endUrl, {}).then(function (answer) {
          if (answer.ok) { root.location.href = body.dataset.homeUrl; }
          else { showMessage(answer.message, 'error'); }
        });
      });
    }
    field.focus();
  }

  root.InvScan = {
    start: start, write: write, read: read, feedback: feedback, showMessage: showMessage,
    showPanel: showPanel, choose: choose, numberPad: numberPad, askText: askText, esc: esc,
    messages: MESSAGES, forget: function (code) { guard.forget(code); }
  };
})(typeof window !== 'undefined' ? window : globalThis);
