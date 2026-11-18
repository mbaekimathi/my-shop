/**
 * Replay offline queue when connectivity returns.
 */

import * as store from "./store.js";
import { isOnline, onConnectivityChange } from "./connectivity.js";

let syncing = false;
let lastSyncError = "";

function uuid() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `op-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const SYNC_BATCH_SIZE = 50;
/** After this many server rejections an op stops auto-replaying. */
const STUCK_AFTER_ATTEMPTS = 5;

function readCsrfToken() {
  // Cookie first: a long-lived (or cache-restored) page can hold a stale
  // form token after re-login, while the cookie is refreshed by the server.
  const cookie = document.cookie
    .split(";")
    .map((row) => row.trim())
    .find((row) => row.startsWith("csrftoken="));
  if (cookie) return decodeURIComponent(cookie.split("=").slice(1).join("="));
  return document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
}

function setSyncError(message) {
  lastSyncError = String(message || "");
  document.querySelectorAll("[data-offline-sync-error]").forEach((el) => {
    if (lastSyncError) {
      el.textContent = lastSyncError;
      el.hidden = false;
    } else {
      el.textContent = "";
      el.hidden = true;
    }
  });
}

export async function queueOperation(type, payload) {
  const item = {
    id: uuid(),
    type,
    payload,
    createdAt: new Date().toISOString(),
  };
  await store.queueAdd(item);
  await store.rebuildPendingSalesFromQueue(await store.queueAll());
  notifyQueueChanged();
  updatePendingBadge();
  if (isOnline()) {
    await syncNow();
  }
  return item.id;
}

function notifyQueueChanged() {
  document.dispatchEvent(new CustomEvent("myshop-offline-queue-changed"));
}

function sortQueue(items) {
  return [...(items || [])].sort((a, b) => {
    const ta = String(a?.createdAt || "");
    const tb = String(b?.createdAt || "");
    if (ta && tb) return ta.localeCompare(tb);
    return String(a?.id || "").localeCompare(String(b?.id || ""));
  });
}

export async function getPendingCount() {
  return store.queueCount();
}

async function queueCounts() {
  const queue = await store.queueAll();
  const stuck = (queue || []).filter((op) => op?.stuck).length;
  return { active: (queue || []).length - stuck, stuck };
}

export async function updatePendingBadge() {
  const { active, stuck } = await queueCounts();
  document.querySelectorAll("[data-sync-pending]").forEach((el) => {
    el.textContent = String(active);
    el.hidden = active === 0;
  });
  document.querySelectorAll("[data-connectivity-pending-badge]").forEach((el) => {
    el.textContent = String(active + stuck);
    el.hidden = active + stuck === 0;
  });
  document.querySelectorAll("[data-sync-now]").forEach((el) => {
    el.hidden = active === 0;
  });
  document.querySelectorAll("[data-sync-stuck]").forEach((el) => {
    el.textContent = String(stuck);
    el.hidden = stuck === 0;
  });
  document.querySelectorAll("[data-sync-retry]").forEach((el) => {
    el.hidden = stuck === 0;
  });
  document.querySelectorAll("[data-sync-discard]").forEach((el) => {
    el.hidden = stuck === 0;
  });
  document.querySelectorAll("[data-offline-bar]").forEach((bar) => {
    bar.hidden = active === 0 && stuck === 0 && !lastSyncError;
  });
}

async function postJson(url, body) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    "X-Requested-With": "XMLHttpRequest",
  };
  const csrf = readCsrfToken();
  if (csrf) headers["X-CSRFToken"] = csrf;

  const res = await fetch(url, {
    method: "POST",
    headers,
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

async function applyResults(results, opsById) {
  let applied = 0;
  let failed = 0;
  let firstError = "";
  let queueChanged = false;
  for (const result of results || []) {
    if (result?.ok) {
      applied += 1;
      queueChanged = true;
      await store.queueRemove(result.id);
    } else {
      failed += 1;
      const message =
        result?.message || result?.error || "Sync failed for a queued change.";
      if (!firstError) firstError = message;
      const op = opsById?.get(result?.id);
      if (op) {
        const attempts = (Number(op.attempts) || 0) + 1;
        queueChanged = true;
        await store.queueUpdate({
          ...op,
          attempts,
          lastError: message,
          stuck: attempts >= STUCK_AFTER_ATTEMPTS,
        });
      }
    }
  }
  if (queueChanged) {
    await store.rebuildPendingSalesFromQueue(await store.queueAll());
    notifyQueueChanged();
  }
  return { applied, failed, firstError };
}

const chunk = (list, size) => {
  const out = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
};

/** Re-arm ops the server kept rejecting, then try syncing again. */
export async function retryStuckOps() {
  await store.queueRetryStuck();
  await updatePendingBadge();
  return syncNow();
}

/** Permanently drop ops the server kept rejecting. */
export async function discardStuckOps() {
  const removed = await store.queueDiscardStuck();
  if (removed > 0) {
    await store.rebuildPendingSalesFromQueue(await store.queueAll());
    notifyQueueChanged();
  }
  await updatePendingBadge();
  return removed;
}

export async function syncNow() {
  if (syncing || !isOnline()) return { ok: true, skipped: true };
  const queue = sortQueue(await store.queueAll());
  // Stuck ops keep their place in the queue but are not replayed until the
  // user retries or discards them from the offline bar.
  const active = queue.filter((op) => !op?.stuck);
  if (!active.length) {
    setSyncError("");
    await updatePendingBadge();
    return { ok: true, applied: 0 };
  }

  syncing = true;
  document.documentElement.classList.add("is-syncing");
  setSyncError("");

  const opsById = new Map(active.map((op) => [op.id, op]));
  const authOps = active.filter((op) => op.type !== "register_employee");
  const regOps = active.filter((op) => op.type === "register_employee");
  let applied = 0;
  let failed = 0;
  let firstError = "";

  try {
    if (authOps.length) {
      const syncUrl =
        document.body.dataset.syncUrl || "/employees/api/sync/";
      // Server caps a batch at 100 ops — chunk so a long outage's backlog
      // replays instead of bouncing as batch_too_large forever.
      for (const ops of chunk(authOps, SYNC_BATCH_SIZE)) {
        const { ok, status, data } = await postJson(syncUrl, {
          operations: ops,
        });
        if (!ok) {
          if (
            data?.error === "session_expired" ||
            data?.error === "auth_required"
          ) {
            if (data?.login_url) {
              window.location.replace(data.login_url);
            }
          }
          failed += ops.length;
          firstError =
            firstError ||
            data?.message ||
            (data?.error === "session_expired" || data?.error === "auth_required"
              ? "Sign in again, then tap Sync now to replay queued changes."
              : data?.error) ||
            (status === 403 || status === 401
              ? "Sync blocked (sign in again or refresh the page)."
              : `Sync failed (HTTP ${status}).`);
          break;
        }
        const summary = await applyResults(data?.results, opsById);
        applied += summary.applied;
        failed += summary.failed;
        firstError = firstError || summary.firstError;
      }
    }

    if (regOps.length) {
      const regUrl =
        document.body.dataset.syncRegisterUrl ||
        "/employees/api/sync/register/";
      for (const ops of chunk(regOps, SYNC_BATCH_SIZE)) {
        const { ok, status, data } = await postJson(regUrl, {
          operations: ops,
        });
        if (!ok) {
          failed += ops.length;
          firstError =
            firstError ||
            data?.error ||
            data?.message ||
            (status === 403
              ? "Registration sync blocked (refresh the page)."
              : `Registration sync failed (HTTP ${status}).`);
          break;
        }
        const summary = await applyResults(data?.results, opsById);
        applied += summary.applied;
        failed += summary.failed;
        firstError = firstError || summary.firstError;
      }
    }
  } catch (err) {
    failed += 1;
    firstError = err?.message || "Network error while syncing.";
  } finally {
    syncing = false;
    document.documentElement.classList.remove("is-syncing");
    setSyncError(failed ? firstError : "");
    await updatePendingBadge();
  }

  return { ok: failed === 0, applied, failed, error: firstError || "" };
}

export function initAutoSync() {
  onConnectivityChange((online) => {
    if (online) syncNow();
  });
  store.queueAll().then((queue) => store.rebuildPendingSalesFromQueue(queue));
  updatePendingBadge();
}
