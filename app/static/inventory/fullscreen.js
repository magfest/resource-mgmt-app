// Chrome grants full screen only from a tap, over http as well as https, and drops it on
// every page load or back gesture. Asking on each tap restores it. Laptops are left alone.
(function (root) {
  'use strict';

  function enterFullScreen() {
    var doc = root.document;
    if (doc.fullscreenElement || doc.webkitFullscreenElement) { return; }
    if (!root.matchMedia || !root.matchMedia('(pointer: coarse)').matches) { return; }
    var el = doc.documentElement;
    var request = el.requestFullscreen || el.webkitRequestFullscreen;
    if (!request) { return; }
    try {
      var pending = request.call(el);
      if (pending && pending.catch) { pending.catch(function () {}); }
    } catch (e) { /* the browser refused; the page works the same without it */ }
  }

  root.document.addEventListener('click', enterFullScreen);
}(window));
