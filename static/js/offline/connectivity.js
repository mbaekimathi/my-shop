/**
 * Online/offline detection and UI status updates.
 */

// navigator.onLine only reports whether the browser has a network interface. It
// is not proof that MY-SHOP is reachable (especially after sleep, VPN changes,
// or adapter changes), so begin optimistic and confirm through the ping endpoint.
let online = true;
let knownState = null;
let toastRemoveTimer = null;
const listeners = new Set();

const OFFLINE_TOAST_OUT_MS = 220;
const PING_INTERVAL_MS = 30_000;
const PING_TIMEOUT_MS = 7_000;
const FAILED_PINGS_FOR_OFFLINE = 2;

let failedPings = 0;
let pingInFlight = null;
let pingSequence = 0;
let initialized = false;

function refreshLucideIcons() {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
}

function ensureOfflineToast() {
  let toast = document.querySelector("[data-offline-toast]");
  if (toast) return toast;

  toast = document.createElement("div");
  toast.className = "offline-toast";
  toast.setAttribute("role", "status");
  toast.setAttribute("aria-live", "assertive");
  toast.setAttribute("data-offline-toast", "");
  toast.hidden = true;
  toast.innerHTML = `
    <div class="offline-toast__card">
      <span class="offline-toast__icon" aria-hidden="true">
        <span class="offline-toast__pulse"></span>
        <span class="offline-toast__pulse offline-toast__pulse--delay"></span>
        <i data-lucide="wifi-off" data-offline-toast-icon></i>
      </span>
      <div class="offline-toast__copy">
        <strong class="offline-toast__title">You're turning offline</strong>
        <span class="offline-toast__sub">Changes will queue locally</span>
      </div>
      <button type="button" class="offline-toast__dismiss" data-offline-toast-dismiss aria-label="Dismiss offline notice">
        <i data-lucide="x" aria-hidden="true"></i>
      </button>
    </div>
  `;
  toast
    .querySelector("[data-offline-toast-dismiss]")
    ?.addEventListener("click", () => hideOfflineToast(toast));
  document.body.appendChild(toast);
  refreshLucideIcons();
  return toast;
}

function hideOfflineToast(toast = document.querySelector("[data-offline-toast]")) {
  if (!toast || toast.hidden) return;
  toast.classList.add("is-hiding");
  window.clearTimeout(toastRemoveTimer);
  toastRemoveTimer = window.setTimeout(() => {
    toast.hidden = true;
    toast.classList.remove("is-hiding", "is-live");
  }, OFFLINE_TOAST_OUT_MS);
}

function showOfflineToast() {
  const toast = ensureOfflineToast();
  window.clearTimeout(toastRemoveTimer);
  toast.hidden = false;
  toast.classList.remove("is-hiding", "is-live");
  // Restart entrance + live pulse on every offline event
  toast.style.animation = "none";
  void toast.offsetWidth;
  toast.style.animation = "";
  toast.classList.add("is-live");
  refreshLucideIcons();
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
    const syncError = bar.querySelector("[data-offline-sync-error]");
    const hasError = syncError && !syncError.hidden;
    // Status toast covers going-offline; bar stays for queue / errors only
    bar.hidden = !hasPending && !hasError;
  });

  refreshLucideIcons();
}

function notify() {
  const wentOffline = knownState === true && online === false;
  const wentOnline = knownState === false && online === true;
  knownState = online;

  listeners.forEach((fn) => {
    try {
      fn(online);
    } catch (_e) {
      /* ignore */
    }
  });
  document.documentElement.classList.toggle("is-offline", !online);
  updateConnectivityIndicators();

  if (wentOffline) {
    showOfflineToast();
  } else if (wentOnline) {
    hideOfflineToast();
  }
}

export function isOnline() {
  return online;
}

export function onConnectivityChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function ping() {
  if (pingInFlight) return pingInFlight;

  const sequence = ++pingSequence;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PING_TIMEOUT_MS);

  pingInFlight = fetch("/employees/api/ping/", {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: controller.signal,
  })
    .then((response) => {
      // A successful application response is the only confirmation that the
      // browser can currently reach this MY-SHOP deployment.
      if (!response.ok) throw new Error(`Ping failed (HTTP ${response.status})`);
      return response.json().catch(() => ({}));
    })
    .then((data) => {
      if (data?.ok === false) throw new Error("Ping was rejected");
      if (sequence !== pingSequence) return;
      failedPings = 0;
      if (!online) {
        online = true;
        notify();
      }
    })
    .catch(() => {
      if (sequence !== pingSequence) return;
      failedPings += 1;
      // A single timeout or service-worker fallback must not make the entire
      // app appear offline. Confirm loss of reachability first.
      if (failedPings >= FAILED_PINGS_FOR_OFFLINE && online) {
        online = false;
        notify();
      }
    })
    .finally(() => {
      window.clearTimeout(timeout);
      if (sequence === pingSequence) pingInFlight = null;
    });

  return pingInFlight;
}

export function checkConnectivity() {
  return ping();
}

export function initConnectivity() {
  if (initialized) return;
  initialized = true;

  window.addEventListener("online", () => {
    // Do not mark the app online until MY-SHOP itself answers the health check.
    ping();
  });
  window.addEventListener("offline", () => {
    // This event can be false after adapter/VPN changes. Confirm it with ping.
    ping();
  });

  ping();
  notify();
  window.setInterval(ping, PING_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") ping();
  });
}
