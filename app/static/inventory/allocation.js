// Allocation page Save bar: marks changed rows, counts them, undoes them, and warns before leaving.
// The page saves without this script; the bar then shows only Save changes.
(function () {
  var form = document.getElementById('alloc-save');
  if (!form) { return; }
  var selects = Array.prototype.slice.call(form.querySelectorAll('select[name^="to_"]'));
  var count = form.querySelector('.alloc-count');
  var undo = form.querySelector('.alloc-undo');
  var changes = 0;
  var submitting = false;

  function wasOf(select) {
    return form.elements['was_' + select.name.slice(3)].value;
  }

  function refresh() {
    changes = 0;
    selects.forEach(function (select) {
      var changed = select.value !== wasOf(select);
      select.closest('li').classList.toggle('changed', changed);
      if (changed) { changes += 1; }
    });
    count.textContent = changes + (changes === 1 ? ' change' : ' changes');
    count.hidden = changes === 0;
    undo.hidden = changes === 0;
  }

  form.addEventListener('change', function (event) {
    if (selects.indexOf(event.target) !== -1) { refresh(); }
  });

  undo.addEventListener('click', function () {
    selects.forEach(function (select) { select.value = wasOf(select); });
    refresh();
  });

  form.addEventListener('submit', function () { submitting = true; });

  window.addEventListener('beforeunload', function (event) {
    if (changes > 0 && !submitting) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  // iOS Safari never shows a beforeunload prompt, so links and other forms ask first.
  // A yes disarms beforeunload too, so desktop browsers do not ask twice.
  function leaveAnyway(event) {
    if (changes === 0) { return; }
    if (window.confirm('You have unsaved changes. Leave without saving?')) {
      submitting = true;
    } else {
      event.preventDefault();
    }
  }

  function samePageAnchor(link) {
    return link.getAttribute('href').charAt(0) === '#'
      || (link.hash && link.href.split('#')[0] === window.location.href.split('#')[0]);
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0) { return; }
    if (event.ctrlKey || event.metaKey || event.shiftKey) { return; }
    if (link.target === '_blank' || link.hasAttribute('download') || samePageAnchor(link)) { return; }
    leaveAnyway(event);
  });

  document.addEventListener('submit', function (event) {
    if (event.target !== form) { leaveAnyway(event); }
  });

  // A re-render after a refused save arrives with picks already differing.
  refresh();
})();
