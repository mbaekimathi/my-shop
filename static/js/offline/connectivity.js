/**
 * Online/offline detection and UI status updates.
 */

let online = typeof navigator !== "undefined" ? navigator.onLine : true;
const listeners = new Set();

function refreshLucideIcons() {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
}

function updateConnectivityIndicators() {
  const label = online ? "Online" : "Offline";
  const icon = online ? "wifi" : "wifi-off";
  const title = online
    ? "You are online — live sync active"
    : "You are offline — changes queue locally";

  document.querySelectorAll("[data-connectivity-indicator]").forEach((el) => {
    el.classList.toggle("connectivity-indicator--online", online);
    el.classList.toggle("connectivity-indicator--offline", !online);
    el.setAttribute("aria-label", `Connection status: ${label}`);
    el.setAttribute("title", title);
    el.dataset.connectivityLabel = label;

    const iconEl = el.querySelector("[data-connectivity-icon]");
    if (iconEl) {
      iconEl.setAttribute("data-lucide", icon);
    }
  });

  document.querySelectorAll("[data-offline-status]").forEach((el) => {
    el.textContent = label;
    el.classList.toggle("offline-status--online", online);
    el.classList.toggle("offline-status--offline", !online);
  });

  document.querySelectorAll("[data-offline-hint]").forEach((el) => {
    el.hidden = online;
  });

  document.querySelectorAll("[data-online-only]").forEach((el) => {
    el.toggleAttribute("disabled", !online);
  });

  document.querySelectorAll("[data-offline-bar]").forEach((bar) => {
    const pending = bar.querySelector("[data-sync-pending]");
    const hasPending = pending && !pending.hidden;
    bar.hidden = online && !hasPending;
  });

  refreshLucideIcons();
}

function notify() {
  listeners.forEach((fn) => {
    try {
      fn(online);
    } catch (_e) {
      /* ignore */
    }
  });
  document.documentElement.classList.toggle("is-offline", !online);
  updateConnectivityIndicators();
}

export function isOnline() {
  return online;
}

export function onConnectivityChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function initConnectivity() {
  window.addEventListener("online", () => {
    online = true;
    notify();
  });
  window.addEventListener("offline", () => {
    online = false;
    notify();
  });

  const ping = async () => {
    if (!navigator.onLine) {
      online = false;
      notify();
      return;
    }
    try {
      const res = await fetch("/employees/api/ping/", {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
      });
      online = res.ok;
    } catch (_e) {
      online = false;
    }
    notify();
  };

  ping();
  setInterval(ping, 30000);
  notify();
}
