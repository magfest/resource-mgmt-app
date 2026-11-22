// Flag flow for the scan shell: pick what is wrong with an asset or container, in two taps.
// Pure state logic. The shell draws each view and routes scans here while the flow is open.
(function (root) {
  'use strict';

  // The codes are stored values. The words match FLAG_WORDS in app/inventory/flags.py.
  var REASONS = [
    {code: 'WRONG_DATA', label: 'Wrong details'},
    {code: 'DAMAGED', label: 'Damaged'},
    {code: 'UNKNOWN_ITEM', label: 'Item not in the system'},
    {code: 'OTHER', label: 'Other'}
  ];
  var MESSAGES = {
    scan: 'Scan what you are flagging.',
    noteNeeded: 'Say what is wrong.',
    pickReason: 'Pick a reason, or Cancel.',
    notSaved: 'Not saved. Try again.',
    saved: 'Flagged for a lead.',
    container: 'Flag the container it is in instead. Scan the container label.',
    pickUnit: 'Two assets match: tap the one in your hand'
  };

  function reasonLabel(code) {
    var hit = REASONS.filter(function (r) { return r.code === code; })[0];
    return hit ? hit.label : code;
  }

  // opts: mint() makes a request id; lookup(code) resolves {subject}, {choices} or {message};
  // save(payload, id) resolves the server answer; show(view) draws; done({saved, text}) closes.
  // A subject is {code} or {unitId}, plus a label to show. The flow never touches the page.
  function createFlagFlow(opts) {
    var state = 'closed';     // closed, offer, scan, looking, choices, reason, note, saving
    var offered = null;       // the subject open() was given, if any
    var subject = null;
    var generation = 0;       // bumps on every open and cancel, so a late lookup is dropped
    var idle = null;          // the step a refused lookup returns to
    var request = null;       // {key, id}: reused so a repeat tap of one save sends one request
    var reason = null;

    function show(step, extra) {
      opts.show(Object.assign({step: step, label: subject ? subject.label : null}, extra || {}));
    }
    function close(result) {
      state = 'closed';
      generation += 1;
      opts.done(result);
    }
    function backToIdle(message) {
      state = idle;
      if (state === 'offer') { show('offer', {label: offered.label, message: message}); }
      else { show('scan', {message: message || MESSAGES.scan}); }
    }
    function payload(note) {
      var body = subject.unitId != null ? {unit_id: subject.unitId} : {code: subject.code};
      body.reason = reason;
      if (note != null) { body.note = note; }
      return body;
    }
    function subjectKey() {
      return subject.unitId != null ? 'u' + subject.unitId : 'c' + subject.code;
    }
    function toReasons(message) {
      state = 'reason';
      show('reason', {message: message || null, reasons: REASONS});
    }

    function save(note) {
      var key = subjectKey() + '|' + reason + '|' + (note || '');
      if (!request || request.key !== key) { request = {key: key, id: opts.mint()}; }
      var mine = generation;
      state = 'saving';
      show('saving');
      return Promise.resolve(opts.save(payload(note), request.id)).then(function (answer) {
        if (mine !== generation) { return; }
        if (answer && answer.ok) {
          close({saved: true, text: MESSAGES.saved + ' ' + reasonLabel(reason) + '.'});
        } else if (answer && (answer.need_name || answer.fatal)) {
          close({saved: false, silent: true});
        } else {
          toReasons(answer && answer.message ? answer.message : MESSAGES.notSaved);
        }
      }, function () {
        if (mine === generation) { toReasons(MESSAGES.notSaved); }
      });
    }

    return {
      isOpen: function () { return state !== 'closed'; },
      step: function () { return state; },
      // `given` is the station's current subject, or null.
      open: function (given) {
        generation += 1;
        request = null;
        reason = null;
        subject = null;
        offered = given || null;
        if (offered) { state = 'offer'; idle = 'offer'; show('offer', {label: offered.label}); }
        else { state = 'scan'; idle = 'scan'; show('scan', {message: MESSAGES.scan}); }
      },
      cancel: function () {
        if (state === 'closed' || state === 'saving') { return; }
        close({saved: false});
      },
      useSubject: function () {
        if (state !== 'offer') { return; }
        subject = offered;
        toReasons();
      },
      scanElse: function () {
        if (state !== 'offer') { return; }
        state = 'scan';
        idle = 'scan';
        show('scan', {message: MESSAGES.scan});
      },
      chooseSubject: function (picked) {
        if (state !== 'choices') { return; }
        subject = picked;
        toReasons();
      },
      // Takes every scan while the flow is open. Always settles with ok false: a scan here
      // finishes nothing the station should remember.
      scan: function (code) {
        var none = {ok: false};
        if (state !== 'offer' && state !== 'scan') {
          return Promise.resolve(none);
        }
        var mine = generation;
        state = 'looking';
        show('looking');
        return Promise.resolve(opts.lookup(code)).then(function (found) {
          if (mine !== generation) { return none; }
          if (found && found.subject) { subject = found.subject; toReasons(); }
          else if (found && found.choices) {
            state = 'choices';
            show('choices', {choices: found.choices});
          } else {
            backToIdle(found && found.message ? found.message : MESSAGES.container);
          }
          return none;
        }, function () {
          if (mine === generation) { backToIdle(MESSAGES.notSaved); }
          return none;
        });
      },
      pickReason: function (code) {
        if (state !== 'reason' || !REASONS.some(function (r) { return r.code === code; })) { return undefined; }
        reason = code;
        if (code === 'OTHER') {
          state = 'note';
          show('note');
          return Promise.resolve();
        }
        return save(null);
      },
      // text is the note, or null when the volunteer cancelled the note field.
      submitNote: function (text) {
        if (state !== 'note') { return undefined; }
        if (text === null) { toReasons(); return Promise.resolve(); }
        if (!text) { show('note', {message: MESSAGES.noteNeeded}); return Promise.resolve(); }
        return save(text);
      }
    };
  }

  var pure = {createFlagFlow: createFlagFlow, REASONS: REASONS, MESSAGES: MESSAGES};
  if (typeof module !== 'undefined' && module.exports) { module.exports = pure; }
  root.InvFlagFlow = pure;
})(typeof window !== 'undefined' ? window : globalThis);
