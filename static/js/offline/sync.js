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

function readCsrfToken() {
  const field =
    document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
  if (field) return field;
  const cookie = document.cookie
    .split(";")
    .map((row) => row.trim())
    .find((row) => row.startsWith("csrftoken="));
  return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
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
  updatePendingBadge();
  if (isOnline()) {
    await syncNow();
  }
  return item.id;
}

export async function getPendingCount() {
  return store.queueCount();
}

export async function updatePendingBadge() {
  const count = await store.queueCount();
  document.querySelectorAll("[data-sync-pending]").forEach((el) => {
    el.textContent = String(count);
    el.hidden = count === 0;
  });
  document.querySelectorAll("[data-connectivity-pending-badge]").forEach((el) => {
    el.textContent = String(count);
    el.hidden = count === 0;
  });
  document.querySelectorAll("[data-sync-now]").forEach((el) => {
    el.hidden = count === 0;
  });
  document.querySelectorAll("[data-offline-bar]").forEach((bar) => {
    bar.hidden = count === 0 && !lastSyncError;
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

async function applyResults(results) {
  let applied = 0;
  let failed = 0;
  let firstError = "";
  for (const result of results || []) {
    if (result?.ok) {
      applied += 1;
      await store.queueRemove(result.id);
    } else {
      failed += 1;
      if (!firstError) {
        firstError =
          result?.message || result?.error || "Sync failed for a queued change.";
      }
    }
  }
  return { applied, failed, firstError };
}

export async function syncNow() {
  if (syncing || !isOnline()) return { ok: true, skipped: true };
  const queue = await store.queueAll();
  if (!queue.length) {
    setSyncError("");
    await updatePendingBadge();
    return { ok: true, applied: 0 };
  }

  syncing = true;
  document.documentElement.classList.add("is-syncing");
  setSyncError("");

  const authOps = queue.filter((op) => op.type !== "register_employee");
  const regOps = queue.filter((op) => op.type === "register_employee");
  let applied = 0;
  let failed = 0;
  let firstError = "";

  try {
    if (authOps.length) {
      const syncUrl =
        document.body.dataset.syncUrl || "/employees/api/sync/";
      const { ok, status, data } = await postJson(syncUrl, {
        operations: authOps,
      });
      if (!ok) {
        failed += authOps.length;
        firstError =
          data?.error ||
          data?.message ||
          (status === 403
            ? "Sync blocked (sign in again or refresh the page)."
            : `Sync failed (HTTP ${status}).`);
      } else {
        const summary = await applyResults(data?.results);
        applied += summary.applied;
        failed += summary.failed;
        firstError = firstError || summary.firstError;
      }
    }

    if (regOps.length) {
      const regUrl =
        document.body.dataset.syncRegisterUrl ||
        "/employees/api/sync/register/";
      const { ok, status, data } = await postJson(regUrl, {
        operations: regOps,
      });
      if (!ok) {
        failed += regOps.length;
        firstError =
          firstError ||
          data?.error ||
          data?.message ||
          (status === 403
            ? "Registration sync blocked (refresh the page)."
            : `Registration sync failed (HTTP ${status}).`);
      } else {
        const summary = await applyResults(data?.results);
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
  updatePendingBadge();
}
