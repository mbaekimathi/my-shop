/**
 * Unified online/offline fetch helpers.
 */

import * as store from "./store.js";
import { isOnline } from "./connectivity.js";
import { queueOperation } from "./sync.js";

export async function fetchJson(url, options = {}) {
  const { offlineQueue, cacheKey, cacheTtl = 300, ...fetchOptions } = options;

  if (!isOnline()) {
    if (cacheKey) {
      const cached = await store.cacheGet(cacheKey);
      if (cached) return { ok: true, data: cached, fromCache: true };
    }
    if (offlineQueue) {
      await queueOperation(offlineQueue.type, offlineQueue.payload);
      return {
        ok: true,
        queued: true,
        data: offlineQueue.queuedResponse || { ok: true, queued: true },
      };
    }
    throw new Error("offline");
  }

  try {
    const res = await fetch(url, {
      credentials: "same-origin",
      ...fetchOptions,
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && cacheKey) {
      await store.cacheSet(cacheKey, data, cacheTtl);
    }
    return { ok: res.ok, status: res.status, data, fromCache: false };
  } catch (err) {
    if (cacheKey) {
      const cached = await store.cacheGet(cacheKey);
      if (cached) return { ok: true, data: cached, fromCache: true };
    }
    throw err;
  }
}

export async function checkEmployeeIdOffline(url, code) {
  const cacheKey = `emp_id_check:${code}`;
  const cached = await store.getCachedEmployeeIdCheck(code);
  if (cached) return cached;

  if (!isOnline()) {
    return {
      available: null,
      message: "Offline — availability will be verified when you reconnect.",
      offline: true,
    };
  }

  try {
    const res = await fetch(`${url}?code=${encodeURIComponent(code)}`, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    const data = await res.json();
    await store.cacheEmployeeIdCheck(code, data);
    return data;
  } catch (_e) {
    return {
      available: null,
      message: "Could not verify code. Try again when online.",
      offline: true,
    };
  }
}
