// In a browser tab, Chrome grants full screen only from a tap and drops it on every page
// load, so each tap asks again. The installed app is already full screen through its
// manifest; asking there makes Android show its "drag to exit" notice on every tap.
// Laptops are left alone.
(function (root) {
  'use strict';

  function enterFullScreen() {
    var doc = root.document;
    if (doc.fullscreenElement || doc.webkitFullscreenElement) { return; }
    if (!root.matchMedia || !root.matchMedia('(pointer: coarse)').matches) { return; }
    if (root.matchMedia('(display-mode: fullscreen)').matches ||
        root.matchMedia('(display-mode: standalone)').matches) { return; }
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
