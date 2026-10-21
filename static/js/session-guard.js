/**
 * Session expiry redirect + per-mode single-tab lock.
 *
 * Shop and employee sessions may coexist (one tab each).
 * Duplicate shop+shop or employee+employee tabs are blocked.
 */

let redirecting = false;
let tabLockState = null;

const LOCK_KEY_PREFIX = "myshop-session-lock:";
const CHANNEL_PREFIX = "myshop-session-lock-";
const HEARTBEAT_MS = 1500;
const STALE_MS = 4000;
const CLAIM_CONFIRM_MS = 60;
const RECLAIM_MS = 1000;

function authRoot() {
  return document.querySelector("[data-workspace][data-auth-required]");
}

function fallbackLoginUrl() {
  const root = authRoot();
  return root?.getAttribute("data-login-url") || "/employees/login/";
}

export function redirectToLogin(loginUrl) {
  if (redirecting) return;
  const target = String(loginUrl || fallbackLoginUrl() || "").trim();
  if (!target) return;
  redirecting = true;
  window.location.replace(target);
}

function isSessionExpiredPayload(data) {
  if (!data || typeof data !== "object") return false;
  const error = String(data.error || "").toLowerCase();
  return (
    error === "session_expired" ||
    error === "auth_required" ||
    error.includes("session expired")
  );
}

export function handleSessionPayload(data, status) {
  if (!isSessionExpiredPayload(data)) return false;
  redirectToLogin(data.login_url || fallbackLoginUrl());
  return true;
}

function expectedAuthMode() {
  return authRoot()?.getAttribute("data-auth-mode") || "";
}

function authStillValid(data) {
  const expected = expectedAuthMode();
  if (!expected) return true;
  if (expected === "shop") {
    if (typeof data.shop_auth === "boolean") return data.shop_auth;
    return data.auth === "shop";
  }
  if (expected === "employee") {
    if (typeof data.employee_auth === "boolean") return data.employee_auth;
    return data.auth === "employee";
  }
  return data.auth && data.auth !== "none";
}

function onPingDetail(data) {
  if (!authRoot() || !data || data.ok === false) return;
  if (authStillValid(data)) return;
  const expected = expectedAuthMode();
  let loginUrl = data.login_url || fallbackLoginUrl();
  if (expected === "shop") loginUrl = "/employees/shops/login/";
  else if (expected === "employee") loginUrl = "/employees/login/";
  redirectToLogin(loginUrl);
}

function installFetchGuard() {
  if (typeof window.fetch !== "function" || window.fetch.__myshopSessionGuard) {
    return;
  }
  const originalFetch = window.fetch.bind(window);
  const wrapped = async (...args) => {
    const response = await originalFetch(...args);
    if (
      authRoot() &&
      (response.status === 401 || response.status === 403)
    ) {
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        try {
          const data = await response.clone().json();
          handleSessionPayload(data, response.status);
        } catch (_err) {
          /* ignore non-json */
        }
      }
    }
    return response;
  };
  wrapped.__myshopSessionGuard = true;
  window.fetch = wrapped;
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

// Unique per page load — sessionStorage is copied when a tab is duplicated,
// which would let the copy share the lock owner id and bypass the tab limit.
function getTabId() {
  const random =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `tab-${random}`;
}

function lockStorageKey(mode) {
  return `${LOCK_KEY_PREFIX}${mode}`;
}

function readLock(mode) {
  try {
    const raw = localStorage.getItem(lockStorageKey(mode));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.owner !== "string") return null;
    const ts = Number(parsed.ts) || 0;
    return { owner: parsed.owner, ts };
  } catch (_err) {
    return null;
  }
}

function writeLock(mode, owner) {
  const payload = JSON.stringify({ owner, ts: Date.now() });
  try {
    localStorage.setItem(lockStorageKey(mode), payload);
  } catch (_err) {
    /* private mode / quota */
  }
}

function clearLockIfOwned(mode, owner) {
  const lock = readLock(mode);
  if (lock && lock.owner === owner) {
    try {
      localStorage.removeItem(lockStorageKey(mode));
    } catch (_err) {
      /* ignore */
    }
  }
}

function lockIsFresh(lock) {
  return Boolean(lock && Date.now() - lock.ts <= STALE_MS);
}

function isHeldByOther(mode, tabId) {
  const lock = readLock(mode);
  if (!lock || lock.owner === tabId) return false;
  return lockIsFresh(lock);
}

function openLockChannel(mode) {
  if (typeof BroadcastChannel === "undefined") return null;
  try {
    return new BroadcastChannel(`${CHANNEL_PREFIX}${mode}`);
  } catch (_err) {
    return null;
  }
}

function modeLabel(mode) {
  return mode === "shop" ? "shop" : "employee";
}

// The other portal's login page: a blocked employee tab offers the shop
// login (and vice versa) so the user can run one tab of each kind.
function altLoginUrl(mode) {
  const root = authRoot();
  const declared = root?.getAttribute("data-alt-login-url") || "";
  if (declared) return declared;
  return mode === "shop"
    ? "/employees/login/?switch=1"
    : "/employees/shops/login/?switch=1";
}

function blockPanel() {
  return document.querySelector("[data-session-tab-block]");
}

function setBlocked(blocked, mode) {
  const panel = blockPanel();
  const root = authRoot();
  document.body.classList.toggle("session-tab-blocked", blocked);
  if (root) root.classList.toggle("is-session-tab-blocked", blocked);
  if (!panel) return;

  panel.hidden = !blocked;
  panel.setAttribute("aria-hidden", blocked ? "false" : "true");

  const title = panel.querySelector("[data-session-tab-block-title]");
  const detail = panel.querySelector("[data-session-tab-block-detail]");
  const altLogin = panel.querySelector("[data-session-tab-alt-login]");
  const label = modeLabel(mode);
  if (title) {
    title.textContent =
      label === "shop"
        ? "Shop session already open"
        : "Employee session already open";
  }
  if (detail) {
    detail.textContent =
      label === "shop"
        ? "This shop session is open in another tab. You can keep one shop tab and one employee tab at the same time, but not two shop tabs."
        : "This employee session is open in another tab. You can keep one employee tab and one shop tab at the same time, but not two employee tabs.";
  }
  if (altLogin) {
    altLogin.setAttribute("href", altLoginUrl(mode));
    altLogin.textContent =
      label === "shop" ? "Log in as an employee" : "Log in to a shop";
  }
}

async function claimLock(mode, tabId, { force = false } = {}) {
  if (!force && isHeldByOther(mode, tabId)) return false;
  writeLock(mode, tabId);
  await sleep(CLAIM_CONFIRM_MS);
  const lock = readLock(mode);
  if (!lock || lock.owner !== tabId) {
    if (force) {
      writeLock(mode, tabId);
      await sleep(CLAIM_CONFIRM_MS);
      const again = readLock(mode);
      return Boolean(again && again.owner === tabId);
    }
    return false;
  }
  return true;
}

function broadcast(channel, message) {
  if (!channel) return;
  try {
    channel.postMessage(message);
  } catch (_err) {
    /* ignore */
  }
}

function startHeartbeat(mode, tabId, channel) {
  writeLock(mode, tabId);
  return window.setInterval(() => {
    if (!tabLockState || tabLockState.blocked) return;
    const lock = readLock(mode);
    if (lock && lock.owner !== tabId && lockIsFresh(lock)) {
      becomeBlocked(mode);
      return;
    }
    writeLock(mode, tabId);
    broadcast(channel, { type: "heartbeat", owner: tabId, mode });
  }, HEARTBEAT_MS);
}

function startReclaim(mode) {
  if (!tabLockState || tabLockState.reclaimId) return;
  tabLockState.reclaimId = window.setInterval(async () => {
    if (!tabLockState || !tabLockState.blocked) return;
    const { tabId, channel } = tabLockState;
    if (isHeldByOther(mode, tabId)) return;
    const claimed = await claimLock(mode, tabId, { force: false });
    if (!claimed || !tabLockState || !tabLockState.blocked) return;
    becomeActive(mode, tabId, channel);
  }, RECLAIM_MS);
}

function becomeBlocked(mode) {
  if (!tabLockState) return;
  tabLockState.blocked = true;
  if (tabLockState.heartbeatId) {
    window.clearInterval(tabLockState.heartbeatId);
    tabLockState.heartbeatId = null;
  }
  setBlocked(true, mode);
  // Keep watching: when the owner tab closes or its lock goes stale, this tab
  // takes over automatically instead of staying blocked.
  startReclaim(mode);
}

function becomeActive(mode, tabId, channel) {
  if (!tabLockState) return;
  tabLockState.blocked = false;
  setBlocked(false, mode);
  if (tabLockState.reclaimId) {
    window.clearInterval(tabLockState.reclaimId);
    tabLockState.reclaimId = null;
  }
  if (tabLockState.heartbeatId) window.clearInterval(tabLockState.heartbeatId);
  tabLockState.heartbeatId = startHeartbeat(mode, tabId, channel);
  broadcast(channel, { type: "claim", owner: tabId, mode });
}

async function takeover(mode) {
  if (!tabLockState || tabLockState.mode !== mode) return;
  const { tabId, channel } = tabLockState;
  const ok = await claimLock(mode, tabId, { force: true });
  if (!ok) return;
  broadcast(channel, { type: "takeover", owner: tabId, mode });
  becomeActive(mode, tabId, channel);
  window.location.reload();
}

async function initTabLock() {
  const root = authRoot();
  if (!root) return;

  const mode = expectedAuthMode();
  if (mode !== "shop" && mode !== "employee") return;

  // localStorage required for cross-tab coordination
  try {
    const probe = "__myshop_lock_probe__";
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
  } catch (_err) {
    return;
  }

  const tabId = getTabId();
  const channel = openLockChannel(mode);

  const onStorage = (event) => {
    if (!tabLockState || event.key !== lockStorageKey(mode)) return;
    if (tabLockState.blocked) return;
    const lock = readLock(mode);
    if (lock && lock.owner !== tabId && lockIsFresh(lock)) {
      becomeBlocked(mode);
    }
  };

  const onPageHide = () => {
    if (!tabLockState) return;
    clearLockIfOwned(mode, tabId);
    broadcast(channel, { type: "release", owner: tabId, mode });
  };

  tabLockState = {
    mode,
    tabId,
    channel,
    blocked: false,
    heartbeatId: null,
    reclaimId: null,
    onStorage,
    onPageHide,
  };

  window.addEventListener("storage", onStorage);
  window.addEventListener("pagehide", onPageHide);

  if (channel) {
    channel.onmessage = (event) => {
      const data = event?.data;
      if (!data || data.mode !== mode || data.owner === tabId) return;
      if (data.type === "claim" || data.type === "takeover" || data.type === "heartbeat") {
        if (!tabLockState.blocked) becomeBlocked(mode);
      }
    };
  }

  const claimed = await claimLock(mode, tabId, { force: false });
  if (!claimed) {
    becomeBlocked(mode);
  } else {
    becomeActive(mode, tabId, channel);
  }

  const panel = blockPanel();
  panel
    ?.querySelector("[data-session-tab-takeover]")
    ?.addEventListener("click", (event) => {
      event.preventDefault();
      takeover(mode);
    });
}

export function initSessionGuard() {
  if (!authRoot()) return;
  installFetchGuard();
  document.addEventListener("myshop:ping", (event) => {
    onPingDetail(event.detail);
  });
  initTabLock();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSessionGuard, { once: true });
} else {
  initSessionGuard();
}
