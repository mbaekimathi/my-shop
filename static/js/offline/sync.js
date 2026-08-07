/**
 * Replay offline queue when connectivity returns.
 */

import * as store from "./store.js";
import { isOnline, onConnectivityChange } from "./connectivity.js";

let syncing = false;

function uuid() {
  if (crypto?.randomUUID) return crypto.randomUUID();
  return `op-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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
    const onlineNow = typeof navigator !== "undefined" && navigator.onLine;
    bar.hidden = onlineNow && count === 0;
  });
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

export async function syncNow() {
  if (syncing || !isOnline()) return { ok: true, skipped: true };
  const queue = await store.queueAll();
  if (!queue.length) return { ok: true, applied: 0 };

  syncing = true;
  document.documentElement.classList.add("is-syncing");

  const authOps = queue.filter((op) => op.type !== "register_employee");
  const regOps = queue.filter((op) => op.type === "register_employee");

  try {
    if (authOps.length) {
      const syncUrl =
        document.body.dataset.syncUrl || "/employees/api/sync/";
      const { data } = await postJson(syncUrl, { operations: authOps });
      if (data?.results) {
        for (const result of data.results) {
          if (result.ok) await store.queueRemove(result.id);
        }
      }
    }

    if (regOps.length) {
      const regUrl =
        document.body.dataset.syncRegisterUrl ||
        "/employees/api/sync/register/";
      const { data } = await postJson(regUrl, { operations: regOps });
      if (data?.results) {
        for (const result of data.results) {
          if (result.ok) await store.queueRemove(result.id);
        }
      }
    }
  } finally {
    syncing = false;
    document.documentElement.classList.remove("is-syncing");
    await updatePendingBadge();
  }

  return { ok: true };
}

export function initAutoSync() {
  onConnectivityChange((online) => {
    if (online) syncNow();
  });
  updatePendingBadge();
}
