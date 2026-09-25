/**
 * Online/offline detection and UI status updates.
 *
 * Design goals:
 * - Stay optimistic while MY-SHOP is reachable.
 * - Ignore one-off timeouts (dev server reload, busy runserver, brief adapter flicker).
 * - Only show the "You're offline" toast after a long, confirmed outage (not a blip).
 */

// navigator.onLine only reports whether the browser has a network interface. It
// is not proof that MY-SHOP is reachable (especially after sleep, VPN changes,
// or adapter changes), so begin optimistic and confirm through the ping endpoint.
let online = true;
let knownState = null;
let toastRemoveTimer = null;
let toastConfirmTimer = null;
let offlineSince = 0;
const listeners = new Set();

const OFFLINE_TOAST_OUT_MS = 220;
/** Toast appears only after the app has stayed unreachable this long. */
const OFFLINE_TOAST_CONFIRM_MS = 45_000;
const PING_INTERVAL_MS = 45_000;
const PING_TIMEOUT_MS = 8_000;
/** Minimum time between probe attempts (avoids burst failures). */
const MIN_PING_GAP_MS = 5_000;
/** Consecutive failures before we consider marking offline (with duration). */
const FAILED_PINGS_FOR_OFFLINE = 5;
/** Must be unreachable at least this long (and enough fails) before "offline". */
const MIN_OUTAGE_MS_BEFORE_OFFLINE = 22_000;
/** Many rapid failures without recovery — treat as offline even if window is short. */
const HARD_FAILS_FOR_OFFLINE = 9;
/** After a recent successful ping, ignore one slow/timeout probe (not a full outage). */
const RECENT_OK_GRACE_MS = 20_000;
/** User dismissed the toast — stay quiet for a while unless still offline. */
const TOAST_SNOOZE_AFTER_DISMISS_MS = 15 * 60 * 1000;
/** Brief offline→online flap — suppress toast replays for a few minutes. */
const TOAST_MUTE_AFTER_BLIP_MS = 4 * 60 * 1000;
/** Shorter offline episodes than this are treated as blips when recovering. */
const OFFLINE_BLIP_MAX_MS = 35_000;

let failedPings = 0;
let firstFailAt = 0;
let pingInFlight = null;
let pingSequence = 0;
let initialized = false;
let lastSuccessAt = 0;
let lastPingEndedAt = 0;
let toastSnoozedUntil = 0;
let toastMutedUntil = 0;

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
  toast.setAttribute("aria-live", "polite");
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
        <strong class="offline-toast__title">You're offline</strong>
        <span class="offline-toast__sub">Changes will queue locally until the connection returns</span>
      </div>
      <button type="button" class="offline-toast__dismiss" data-offline-toast-dismiss aria-label="Dismiss offline notice">
        <i data-lucide="x" aria-hidden="true"></i>
      </button>
    </div>
  `;
  toast
    .querySelector("[data-offline-toast-dismiss]")
    ?.addEventListener("click", () => {
      toastSnoozedUntil = Date.now() + TOAST_SNOOZE_AFTER_DISMISS_MS;
      hideOfflineToast(toast);
    });
  document.body.appendChild(toast);
  refreshLucideIcons();
  return toast;
}

function hideOfflineToast(toast = document.querySelector("[data-offline-toast]")) {
  window.clearTimeout(toastConfirmTimer);
  toastConfirmTimer = null;
  if (!toast || toast.hidden) return;
  toast.classList.add("is-hiding");
  window.clearTimeout(toastRemoveTimer);
  toastRemoveTimer = window.setTimeout(() => {
    toast.hidden = true;
    toast.classList.remove("is-hiding", "is-live");
  }, OFFLINE_TOAST_OUT_MS);
}

function showOfflineToast() {
  const now = Date.now();
  if (now < toastSnoozedUntil || now < toastMutedUntil) return;

  const toast = ensureOfflineToast();
  if (!toast.hidden && toast.classList.contains("is-live")) return;

  window.clearTimeout(toastRemoveTimer);
  toast.hidden = false;
  toast.classList.remove("is-hiding", "is-live");
  toast.style.animation = "none";
  void toast.offsetWidth;
  toast.style.animation = "";
  toast.classList.add("is-live");
  refreshLucideIcons();
}

function scheduleOfflineToast() {
  if (toastConfirmTimer) return;
  const elapsed = offlineSince ? Date.now() - offlineSince : 0;
  const wait = Math.max(0, OFFLINE_TOAST_CONFIRM_MS - elapsed);
  toastConfirmTimer = window.setTimeout(() => {
    toastConfirmTimer = null;
    if (!online) showOfflineToast();
  }, wait);
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
    offlineSince = Date.now();
    scheduleOfflineToast();
  } else if (wentOnline) {
    const blip =
      offlineSince > 0 && Date.now() - offlineSince < OFFLINE_BLIP_MAX_MS;
    offlineSince = 0;
    hideOfflineToast();
    if (blip) {
      toastMutedUntil = Date.now() + TOAST_MUTE_AFTER_BLIP_MS;
    }
  }
}

export function isOnline() {
  return online;
}

export function onConnectivityChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function markOnline() {
  const wasOffline = !online;
  failedPings = 0;
  firstFailAt = 0;
  lastSuccessAt = Date.now();
  if (wasOffline) {
    online = true;
    notify();
  }
}

function shouldMarkOffline() {
  if (failedPings >= HARD_FAILS_FOR_OFFLINE) return true;
  if (failedPings < FAILED_PINGS_FOR_OFFLINE) return false;
  if (!firstFailAt) return false;
  return Date.now() - firstFailAt >= MIN_OUTAGE_MS_BEFORE_OFFLINE;
}

function markOfflineCandidate() {
  if (failedPings === 0) firstFailAt = Date.now();
  failedPings += 1;

  if (online && shouldMarkOffline()) {
    online = false;
    notify();
  }
}

async function ping() {
  if (pingInFlight) return pingInFlight;

  const gap = Date.now() - lastPingEndedAt;
  if (lastPingEndedAt > 0 && gap < MIN_PING_GAP_MS) {
    return Promise.resolve();
  }

  const sequence = ++pingSequence;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), PING_TIMEOUT_MS);

  const run = fetch("/employees/api/ping/", {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    signal: controller.signal,
  })
    .then((response) => {
      if (!response.ok) throw new Error(`Ping failed (HTTP ${response.status})`);
      return response.json().catch(() => ({}));
    })
    .then((data) => {
      if (data?.ok === false) throw new Error("Ping was rejected");
      if (sequence !== pingSequence) return;
      markOnline();
    })
    .catch((err) => {
      if (sequence !== pingSequence) return;
      if (document.visibilityState === "hidden" && err?.name === "AbortError") {
        return;
      }
      const recentlyOk =
        Number.isFinite(lastSuccessAt) &&
        Date.now() - lastSuccessAt < RECENT_OK_GRACE_MS;
      if (recentlyOk && err?.name === "AbortError") return;
      markOfflineCandidate();
    })
    .finally(() => {
      window.clearTimeout(timeout);
      lastPingEndedAt = Date.now();
      if (sequence === pingSequence) pingInFlight = null;
    });

  pingInFlight = run;
  return run;
}

export function checkConnectivity() {
  lastPingEndedAt = 0;
  return ping();
}

export function initConnectivity() {
  if (initialized) return;
  initialized = true;

  window.addEventListener("online", () => {
    window.setTimeout(() => ping(), 800);
  });
  window.addEventListener("offline", () => {
    window.setTimeout(() => ping(), 3_000);
  });

  lastSuccessAt = Date.now();
  ping();
  notify();
  window.setInterval(() => ping(), PING_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") ping();
  });
}
