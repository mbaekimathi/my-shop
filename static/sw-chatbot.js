/* Minimal service worker — enables PWA install + auto-update.
   No caching: every request hits the network so the app is never stale.
   VERSION bumps force browsers to fetch a new worker after each deploy. */
var SW_VERSION = "2026-11-08.3";

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", function (event) {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", function () {
  // pass-through — nothing cached
});
