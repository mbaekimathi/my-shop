/* Keep the installed chatbot PWA on the latest deploy automatically.
   - Re-checks the service worker on load, focus, and periodically
   - Reloads once when a new worker takes control (new JS/CSS/HTML)
   - Manifest/icon updates are picked up by Chrome when the app is opened
     (icon URL changes trigger home-screen icon refresh on Android) */
(function () {
  "use strict";
  if (!("serviceWorker" in navigator)) return;

  var SW_URL = window.__CHATBOT_SW_URL__ || "/chatbot/sw.js";
  var SW_SCOPE = window.__CHATBOT_SW_SCOPE__ || "/chatbot/";
  var CHECK_MS = 5 * 60 * 1000;
  var reloading = false;

  function reloadOnce() {
    if (reloading) return;
    reloading = true;
    location.reload();
  }

  function watchWorker(worker) {
    if (!worker) return;
    worker.addEventListener("statechange", function () {
      if (worker.state === "activated" && navigator.serviceWorker.controller) {
        reloadOnce();
      }
    });
  }

  function check(reg) {
    if (!reg) return;
    try { reg.update(); } catch (e) {}
  }

  navigator.serviceWorker
    .register(SW_URL, { scope: SW_SCOPE })
    .then(function (reg) {
      check(reg);
      reg.addEventListener("updatefound", function () {
        watchWorker(reg.installing);
      });
      if (reg.waiting) {
        // A waiting worker means a deploy landed while this tab was open.
        try { reg.waiting.postMessage({ type: "SKIP_WAITING" }); } catch (e) {}
        watchWorker(reg.waiting);
      }
      setInterval(function () { check(reg); }, CHECK_MS);
      document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") check(reg);
      });
      window.addEventListener("focus", function () { check(reg); });
    })
    .catch(function () {});

  // Fired when a new SW takes over this page — apply the deploy immediately.
  navigator.serviceWorker.addEventListener("controllerchange", reloadOnce);
})();
