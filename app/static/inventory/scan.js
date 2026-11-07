// Shared scan shell for CT60 stations. Exposes window.InvScan.
// A CT60 XP reports keydown as "Unidentified" and may deliver a whole code in one
// `input` event, so capture reads the field value on `input` and not the key events.
// All text from people goes in with textContent or esc().
(function (root) {
  'use strict';

  var IDLE_MS = 80;          // quiet time after the last input that ends a code
  var REPEAT_MS = 5000;      // the same code inside this window posts nothing
  var REFOCUS_MS = 250;
  var PAUSE_SHOW_MS = 500;   // focus must stay lost this long before the paused banner shows
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
    notSaved: 'NOT SAVED',
    notEnrolled: 'This scanner is not enrolled.',
    cancelled: 'Not saved.',
    blockedDone: 'A scan is not saved yet. Try again or cancel it first.'
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

  // Remembers codes whose scan answered ok. check() returns whole seconds since the
  // same code was recorded inside the window, or null. It records nothing.
  function createRepeatGuard(windowMs) {
    var seen = {};
    return {
      check: function (code, nowMs) {
        if (Object.prototype.hasOwnProperty.call(seen, code) && nowMs - seen[code] < windowMs) {
          return Math.max(1, Math.floor((nowMs - seen[code]) / 1000));
        }
        return null;
      },
      record: function (code, nowMs) {
        Object.keys(seen).forEach(function (k) {
          if (nowMs - seen[k] >= windowMs) { delete seen[k]; }
        });
        seen[code] = nowMs;
      }
    };
  }

  // Runs scans one at a time. onScan(code) returns a promise; the next scan starts when
  // it settles. A code is recorded in the guard only when the answer holds ok: true.
  // submit() returns {queued: true}, {dropped: true} for a code already waiting or in
  // flight, or {repeat: seconds} for a code answered ok inside the guard window.
  function createScanQueue(opts) {
    var waiting = [];
    var active = null;
    var running = false;

    function pump() {
      if (running || !waiting.length) { return; }
      running = true;
      active = waiting.shift();
      var code = active;
      var result;
      try { result = Promise.resolve(opts.onScan(code)); } catch (e) { result = Promise.reject(e); }
      result.then(function (answer) {
        if (answer && answer.ok === true) { opts.guard.record(code, opts.now()); }
      }, function () { /* a failed scan is forgotten, so a rescan runs */ }).then(function () {
        running = false;
        active = null;
        pump();
      });
    }
    return {
      submit: function (code) {
        if (active === code || waiting.indexOf(code) !== -1) { return {dropped: true}; }
        var ago = opts.guard.check(code, opts.now());
        if (ago !== null) { return {repeat: ago}; }
        waiting.push(code);
        pump();
        return {queued: true};
      },
      size: function () { return waiting.length + (running ? 1 : 0); }
    };
  }

  // Writes go one at a time in call order. send(path, payload) resolves with an answer,
  // or rejects when the request failed; the write then blocks, and later writes wait,
  // until retry() resends the same payload or cancel() resolves it as not saved.
  function createWriteQueue(opts) {
    var jobs = [];
    var running = false;
    var blocked = null;
    var drains = [];

    function release(clear) { drains.splice(0).forEach(function (d) { d(clear); }); }
    function attempt(job) {
      opts.send(job.path, job.payload).then(function (answer) {
        jobs.shift();
        running = false;
        blocked = null;
        job.resolve(answer);
        if (!jobs.length) { release(true); }
        pump();
      }, function () {
        blocked = job;
        release(false);
        opts.onBlock(job);
      });
    }
    function pump() {
      if (running || !jobs.length) { return; }
      running = true;
      attempt(jobs[0]);
    }
    return {
      write: function (path, payload) {
        return new Promise(function (resolve) {
          jobs.push({path: path, payload: payload, resolve: resolve});
          pump();
        });
      },
      retry: function () {
        if (!blocked) { return; }
        var job = blocked;
        blocked = null;
        opts.onUnblock();
        attempt(job);
      },
      cancel: function () {
        if (!blocked) { return null; }
        var job = blocked;
        var answer = {ok: false, cancelled: true, message: MESSAGES.cancelled};
        blocked = null;
        jobs.shift();
        running = false;
        opts.onUnblock();
        job.resolve(answer);
        if (!jobs.length) { release(true); }
        pump();
        return answer;
      },
      pending: function () { return jobs.length; },
      isBlocked: function () { return blocked !== null; },
      // Resolves true when no write is left, or false if a write blocks first.
      drain: function () {
        if (blocked) { return Promise.resolve(false); }
        if (!jobs.length) { return Promise.resolve(true); }
        return new Promise(function (resolve) { drains.push(resolve); });
      }
    };
  }

  // Shows the paused banner only after focus has stayed lost for delayMs.
  // suspended() is true while a text field owns focus on purpose.
  function createPauseWatch(opts) {
    var timer = null;
    function clear() { if (timer !== null) { opts.cancel(timer); timer = null; } }
    return {
      lost: function () {
        clear();
        timer = opts.schedule(function () {
          timer = null;
          if (!opts.suspended()) { opts.show(); }
        }, opts.delayMs);
      },
      regained: function () { clear(); opts.hide(); },
      hide: function () { clear(); opts.hide(); }
    };
  }

  // Sorts a finished request into an answer, a fatal stop, or a retry. `data` is the
  // parsed body, or undefined when the body was not JSON.
  function interpretResponse(status, redirected, data) {
    if (status >= 500) { return {type: 'retry'}; }
    if (status === 403) {
      var enrolled = data && data.enrolled === false;
      var text = !enrolled && data && data.message ? data.message : MESSAGES.notEnrolled;
      return {type: 'fatal', message: text};
    }
    if (data === undefined) {
      return redirected ? {type: 'fatal', message: MESSAGES.notEnrolled} : {type: 'retry'};
    }
    return {type: 'answer', data: data};
  }

  var pure = {createCapture: createCapture, createRepeatGuard: createRepeatGuard,
              createScanQueue: createScanQueue, createWriteQueue: createWriteQueue,
              createPauseWatch: createPauseWatch, interpretResponse: interpretResponse,
              MESSAGES: MESSAGES, esc: esc, IDLE_MS: IDLE_MS, REPEAT_MS: REPEAT_MS};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  if (typeof document === 'undefined') { return; }

  var body = document.getElementById('scan-body');
  var field = document.getElementById('scan-input');
  var msg = document.getElementById('msg');
  var panel = document.getElementById('panel');
  var paused = document.getElementById('paused');
  var doneBtn = document.getElementById('done-btn');
  var notSaved = document.getElementById('not-saved');
  var asking = false;       // an askText field owns focus
  var closeAsk = null;      // closes the open askText with no answer
  var blocked = false;      // a write failed; scanning waits for Try again or Cancel
  var stopped = null;       // message shown once the server refuses this scanner
  var leaving = false;      // the page is navigating on purpose; skip the unload prompt
  var audio = null;

  function newId() {
    if (root.crypto && root.crypto.randomUUID) { return root.crypto.randomUUID(); }
    return 'r-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  }

  // Resolves with the parsed JSON, including ok:false answers. Rejects when the request
  // fails or times out. A refusal by the device guard rejects with err.fatal set.
  // The timer runs until the body has been read, so a stalled body also times out.
  function post(path, payload) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
    var finished = fetch(path, {
      method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().then(function (data) { return {r: r, data: data}; }, function (err) {
        if (controller.signal.aborted) { throw err; }
        return {r: r, data: undefined};
      });
    });
    return finished.then(function (res) {
      clearTimeout(timer);
      var verdict = interpretResponse(res.r.status, res.r.redirected, res.data);
      if (verdict.type === 'answer') { return verdict.data; }
      var err = new Error(verdict.type);
      if (verdict.type === 'fatal') { err.fatal = verdict.message; }
      throw err;
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

  function stopWith(text) {
    stopped = text;
    feedback('error');
    showMessage(text, 'error');
  }

  // An idle shift answers need_name. The server refused whatever was in progress, so
  // leaving loses nothing.
  function goToWho() {
    leaving = true;
    root.location.href = body.dataset.whoUrl;
  }

  function checked(answer) {
    if (answer && answer.need_name) { goToWho(); }
    return answer;
  }

  function sendOnce(path, payload) {
    return post(path, payload).then(checked, function (err) {
      if (err && err.fatal) {
        stopWith(err.fatal);
        return {ok: false, message: err.fatal, fatal: true};
      }
      throw err;
    });
  }

  function showNotSaved() {
    blocked = true;
    asking = false;
    if (closeAsk) { closeAsk(null); }
    notSaved.hidden = false;
    body.classList.add('not-saved-on');
    feedback('error');
  }

  function hideNotSaved() {
    blocked = false;
    notSaved.hidden = true;
    body.classList.remove('not-saved-on');
    if (msg.textContent === MESSAGES.blockedDone) { showMessage('', 'info'); }
  }

  var writes = createWriteQueue({send: sendOnce, onBlock: showNotSaved, onUnblock: hideNotSaved});

  // A retry reuses the payload and its request id, so the server stores the answer once.
  function write(path, data) {
    return writes.write(path, Object.assign({}, data, {client_request_id: newId()}));
  }

  function read(path, data) {
    return post(path, data || {}).then(checked, function (err) {
      if (err && err.fatal) {
        stopWith(err.fatal);
        return {ok: false, message: err.fatal, fatal: true};
      }
      return {ok: false, message: MESSAGES.offline};
    });
  }

  function choose(options, onPick, note) {
    clearPanel();
    options.forEach(function (option) {
      var label = typeof option === 'string' ? option : option.label;
      var value = typeof option === 'string' ? option : option.value;
      var button = el('button', 'choice', label);
      button.type = 'button';
      button.addEventListener('click', function () { onPick(value); });
      panel.appendChild(button);
    });
    if (note) { panel.appendChild(el('p', 'hint', note)); }
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
      closeAsk = null;
      clearPanel();
      focusCapture();
      onDone(value);
    }
    closeAsk = close;
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
    var scans = createScanQueue({
      onScan: config.onScan, guard: createRepeatGuard(REPEAT_MS), now: Date.now
    });
    var pauseWatch = createPauseWatch({
      delayMs: PAUSE_SHOW_MS,
      schedule: function (fn, ms) { return setTimeout(fn, ms); },
      cancel: function (handle) { clearTimeout(handle); },
      suspended: function () { return asking; },
      show: function () { paused.hidden = false; },
      hide: function () { paused.hidden = true; }
    });
    var capture = createCapture({
      deliver: function (code) {
        if (stopped) { feedback('error'); showMessage(stopped, 'error'); return; }
        if (blocked) { feedback('error'); return; }
        var outcome = scans.submit(code);
        if (outcome.repeat != null) {
          feedback('error');
          showMessage(MESSAGES.repeat(outcome.repeat), 'info');
        }
      },
      schedule: function (fn, ms) { return setTimeout(fn, ms); },
      cancel: function (handle) { clearTimeout(handle); },
      clear: function () { field.value = ''; }
    });
    field.addEventListener('input', function () { capture.input(field.value); });
    field.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); capture.enter(field.value); }
    });
    field.addEventListener('blur', function () { if (!asking) { pauseWatch.lost(); } });
    field.addEventListener('focus', function () { pauseWatch.regained(); });
    document.addEventListener('click', focusCapture);
    setInterval(function () { if (!asking && document.activeElement !== field) { field.focus(); } },
                REFOCUS_MS);
    document.getElementById('not-saved-again').addEventListener('click', function () {
      writes.retry();
    });
    document.getElementById('not-saved-cancel').addEventListener('click', function () {
      var answer = writes.cancel();
      if (answer) { showMessage(answer.message, 'error'); }
    });
    root.addEventListener('beforeunload', function (e) {
      if (!leaving && writes.pending() > 0) { e.preventDefault(); e.returnValue = ''; }
    });
    if (doneBtn) {
      doneBtn.addEventListener('click', function () {
        if (writes.isBlocked()) { showMessage(MESSAGES.blockedDone, 'error'); return; }
        writes.drain().then(function (clear) {
          if (!clear) { showMessage(MESSAGES.blockedDone, 'error'); return null; }
          return read(body.dataset.endUrl, {});
        }).then(function (answer) {
          if (!answer) { return; }
          if (answer.ok) { leaving = true; root.location.href = body.dataset.homeUrl; }
          else if (!answer.need_name) { showMessage(answer.message, 'error'); }
        });
      });
    }
    field.focus();
  }

  root.InvScan = {
    start: start, write: write, read: read, feedback: feedback, showMessage: showMessage,
    showPanel: showPanel, choose: choose, numberPad: numberPad, askText: askText, esc: esc,
    messages: MESSAGES
  };
})(typeof window !== 'undefined' ? window : globalThis);
