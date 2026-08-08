/**
 * Online/offline detection and UI status updates.
 */

let online = typeof navigator !== "undefined" ? navigator.onLine : true;
let knownState = null;
let toastRemoveTimer = null;
const listeners = new Set();

const OFFLINE_TOAST_OUT_MS = 220;

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
