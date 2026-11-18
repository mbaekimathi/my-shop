/**
 * Bootstrap offline layer: service worker, connectivity, auto-sync.
 */

import { initConnectivity } from "./connectivity.js";
import { initAutoSync } from "./sync.js";

const isLocalDevHost =
  typeof location !== "undefined" &&
  (location.hostname === "localhost" || location.hostname === "127.0.0.1");

const SW_UPDATE_MIN_MS = 60_000;
let lastSwUpdateAt = 0;

function maybeUpdateServiceWorker(registration) {
  const now = Date.now();
  if (now - lastSwUpdateAt < SW_UPDATE_MIN_MS) return;
  lastSwUpdateAt = now;
  registration.update();
}

export function initOffline() {
  initConnectivity();
  initAutoSync();

  // Signing out drops the cached shop-floor page so a shared device cannot
  // reopen the till under a stale copy of the last session.
  document.addEventListener("click", (event) => {
    const link = event.target?.closest?.("a[href]");
    if (!link || !navigator.serviceWorker?.controller) return;
    try {
      const path = new URL(link.href, location.href).pathname.replace(
        /\/+$/,
        ""
      );
      if (path.endsWith("/logout")) {
        navigator.serviceWorker.controller.postMessage({
          type: "CLEAR_PAGE_CACHE",
        });
      }
    } catch (_err) {
      /* malformed href — ignore */
    }
  });

  if (!("serviceWorker" in navigator)) return;

  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // Avoid full-page reload loops on localhost while runserver/SW both churn.
    if (refreshing || isLocalDevHost) return;
    refreshing = true;
    window.location.reload();
  });

  navigator.serviceWorker
    .register("/sw.js", { scope: "/", updateViaCache: "none" })
    .then((registration) => {
      lastSwUpdateAt = Date.now();
      maybeUpdateServiceWorker(registration);

      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        if (!worker) return;

        worker.addEventListener("statechange", () => {
          if (
            worker.state === "installed" &&
            navigator.serviceWorker.controller &&
            !isLocalDevHost
          ) {
            worker.postMessage({ type: "SKIP_WAITING" });
          }
        });
      });

      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          maybeUpdateServiceWorker(registration);
        }
      });
    })
    .catch(() => {
      /* SW optional in dev */
    });
}
