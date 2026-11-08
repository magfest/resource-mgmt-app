// Look up station: scan anything, see what it is, where it is, and what happened to it.
// Every string from the server goes through InvScan.esc before it reaches the page.
(function () {
  'use strict';
  var S = window.InvScan;
  var L = window.InvLabel;
  var resolveUrl = document.currentScript.dataset.resolveUrl;
  var FOUND = ['CONTAINER', 'UNIT', 'ITEM'];

  function cardHtml(card) {
    var html = '<section class="card"><h2>' + S.esc(card.name || card.title) + '</h2>';
    if (card.name) { html += '<div class="item">' + S.esc(card.title) + '</div>'; }
    if (card.tag) { html += '<div class="tag">' + S.esc(card.tag) + '</div>'; }
    if (card.path) { html += '<div class="path">' + S.esc(card.path) + '</div>'; }
    if (card.lines.length) {
      html += '<ul>' + card.lines.map(function (line) {
        return '<li>' + S.esc(line) + '</li>';
      }).join('') + '</ul>';
    }
    if (card.fields && card.fields.length) {
      html += '<ul class="fields">' + card.fields.map(function (f) {
        return '<li>' + S.esc(f[0]) + ': ' + S.esc(f[1]) + '</li>';
      }).join('') + '</ul>';
    }
    if (card.notes) { html += '<p class="notes">' + S.esc(card.notes) + '</p>'; }
    if (card.history.length) {
      html += '<h3>Last ' + card.history.length + ' movements</h3><ul class="history">' +
        card.history.map(function (h) {
          return '<li>' + S.esc(h.text) + ' <span class="who">(' + S.esc(h.who) + ', ' +
            S.esc(h.when) + ')</span></li>';
        }).join('') + '</ul>';
    }
    return html + '</section>';
  }

  // Flag offers the card on screen. An item has no row to flag, so it clears the subject.
  function flaggable(card, target) {
    var label = [card.title, card.tag].filter(Boolean).join(' ');
    if (!target || target.item) { S.setFlagSubject(null); return; }
    S.setFlagSubject(target.unitId != null ? {unitId: target.unitId, label: label}
                                           : {code: target.code, label: label});
  }

  // `target` names what Print label prints: {code, unitId}. Items that share a barcode have none.
  function showCard(card, target) {
    S.showMessage('', 'info');
    flaggable(card, target);
    S.showPanel(cardHtml(card));
    if (!target) { return; }
    var print = document.createElement('button');
    print.type = 'button';
    print.className = 'choice';
    print.textContent = 'Print label';
    print.addEventListener('click', function () {
      L.show(target, function () { showCard(card, target); });
    });
    document.getElementById('panel').appendChild(print);
  }

  function unknownTag(answer) {
    S.setFlagSubject(null);
    S.showPanel('');
    S.showMessage(S.messages.unknown, 'error');
    if (!answer.can_create_tag) { return; }
    var add = document.createElement('button');
    add.type = 'button';
    add.className = 'choice';
    add.textContent = 'Add ' + answer.code + ' as a new asset tag';
    add.addEventListener('click', function () {
      L.newTag(answer.code, function () { unknownTag(answer); });
    });
    document.getElementById('panel').appendChild(add);
  }

  function candidateLabel(card) {
    return [card.title, card.tag, card.path].filter(Boolean).join(' | ');
  }

  function unusedText(answer) {
    return answer.code + ' is a label with nothing attached to it yet.';
  }

  // The shell records the code in its repeat guard only when this answer holds ok: true.
  function handle(answer) {
    L.reset();
    if (answer.need_name) { return; }
    if (!answer.ok) {
      S.feedback('error');
      S.showMessage(answer.message, 'error');
      return;
    }
    if (answer.kind === 'UNKNOWN') {
      S.feedback('error');
      unknownTag(answer);
    } else if (answer.kind === 'UNUSED') {
      S.feedback('error');
      S.setFlagSubject(null);
      S.showPanel('');
      S.showMessage(unusedText(answer), 'info');
    } else if (answer.kind === 'AMBIGUOUS') {
      S.feedback('error');
      S.setFlagSubject(null);
      S.showMessage(S.messages.ambiguous, 'info');
      S.choose(answer.candidates.map(function (c) {
        return {label: candidateLabel(c.card), value: c};
      }), function (c) {
        showCard(c.card, c.unit_id == null ? null : {code: answer.code, unitId: c.unit_id});
      }, answer.more ? 'and ' + answer.more + ' more' : null);
    } else {
      S.feedback('ok');
      // A unit found by name carries unit_id, since its typed text resolves no code.
      showCard(answer.card, {code: answer.code, unitId: answer.unit_id == null ? null : answer.unit_id,
                             item: answer.kind === 'ITEM'});
    }
  }

  S.start({
    station: 'LOOKUP',
    onScan: function (code) {
      return S.read(resolveUrl, {code: code}).then(function (answer) {
        handle(answer);
        // Only a found thing counts as done, so a rescan of anything else shows its message again.
        return {ok: answer.ok === true && FOUND.indexOf(answer.kind) !== -1};
      });
    }
  });
})();
