// Look up station: scan anything, see what it is, where it is, and what happened to it.
// Every string from the server goes through InvScan.esc before it reaches the page.
(function () {
  'use strict';
  var S = window.InvScan;
  var resolveUrl = document.currentScript.dataset.resolveUrl;

  function cardHtml(card) {
    var html = '<section class="card"><h2>' + S.esc(card.title) + '</h2>';
    if (card.tag) { html += '<div class="tag">' + S.esc(card.tag) + '</div>'; }
    if (card.path) { html += '<div class="path">' + S.esc(card.path) + '</div>'; }
    if (card.lines.length) {
      html += '<ul>' + card.lines.map(function (line) {
        return '<li>' + S.esc(line) + '</li>';
      }).join('') + '</ul>';
    }
    if (card.history.length) {
      html += '<h3>Last ' + card.history.length + ' movements</h3><ul class="history">' +
        card.history.map(function (h) {
          return '<li>' + S.esc(h.text) + ' <span class="who">(' + S.esc(h.who) + ', ' +
            S.esc(h.when) + ')</span></li>';
        }).join('') + '</ul>';
    }
    return html + '</section>';
  }

  function showCard(card) {
    S.showMessage('', 'info');
    S.showPanel(cardHtml(card));
  }

  function candidateLabel(card) {
    return [card.title, card.tag, card.path].filter(Boolean).join(' | ');
  }

  function unusedText(answer) {
    return answer.code + ' is a label with nothing attached to it yet.';
  }

  function handle(answer, code) {
    if (!answer.ok) {
      S.forget(code);
      S.feedback('error');
      S.showMessage(answer.message, 'error');
      return;
    }
    if (answer.kind === 'UNKNOWN') {
      S.feedback('error');
      S.showPanel('');
      S.showMessage(S.messages.unknown, 'error');
    } else if (answer.kind === 'UNUSED') {
      S.feedback('error');
      S.showPanel('');
      S.showMessage(unusedText(answer), 'info');
    } else if (answer.kind === 'AMBIGUOUS') {
      S.feedback('error');
      S.showMessage(S.messages.ambiguous, 'info');
      S.choose(answer.candidates.map(function (c) {
        return {label: candidateLabel(c.card), value: c.card};
      }), showCard);
    } else {
      S.feedback('ok');
      showCard(answer.card);
    }
  }

  S.start({
    station: 'LOOKUP',
    onScan: function (code) {
      S.read(resolveUrl, {code: code}).then(function (answer) { handle(answer, code); });
    }
  });
})();
