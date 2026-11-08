(function () {
  var input = document.getElementById('where-search');
  var root = document.getElementById('where-tree');
  if (!input || !root) { return; }

  function allDetails() { return root.querySelectorAll('details'); }

  function clearHits() {
    root.querySelectorAll('.where-hit').forEach(function (el) { el.classList.remove('where-hit'); });
  }

  function restoreDefault() {
    clearHits();
    allDetails().forEach(function (d) { d.open = d.hasAttribute('data-top'); });
  }

  input.addEventListener('input', function () {
    var query = input.value.trim().toLowerCase();
    if (!query) { restoreDefault(); return; }
    clearHits();
    allDetails().forEach(function (d) { d.open = false; });
    root.querySelectorAll('[data-search]').forEach(function (el) {
      if (el.getAttribute('data-search').indexOf(query) === -1) { return; }
      el.classList.add('where-hit');
      var parent = el.parentElement;
      while (parent && parent !== root) {
        if (parent.tagName === 'DETAILS') { parent.open = true; }
        parent = parent.parentElement;
      }
      if (el.tagName === 'DETAILS') { el.open = true; }
    });
  });

  document.getElementById('where-open').addEventListener('click', function () {
    allDetails().forEach(function (d) { d.open = true; });
  });
  document.getElementById('where-close').addEventListener('click', function () {
    allDetails().forEach(function (d) { d.open = false; });
  });
})();
