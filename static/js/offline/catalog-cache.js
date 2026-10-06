/**
 * Cross-page / cross-tab catalog freshness helpers.
 * Invalidates IndexedDB snapshots and notifies open catalogs to soft-refresh.
 */
import {
  CATALOG_CACHE_TTL_SECONDS,
  invalidateCatalogCaches,
  patchCachedShopCatalogStock,
  removeCachedShopSerials,
} from "./store.js";

export { CATALOG_CACHE_TTL_SECONDS };

const CHANNEL_NAME = "myshop-catalog";
const EVENT_NAME = "myshop:catalog-invalidate";

let channel = null;

function getChannel() {
  if (channel) return channel;
  if (typeof BroadcastChannel === "undefined") return null;
  try {
    channel = new BroadcastChannel(CHANNEL_NAME);
  } catch (_err) {
    channel = null;
  }
  return channel;
}

function emitLocal(detail) {
  try {
    document.dispatchEvent(new CustomEvent(EVENT_NAME, { detail }));
  } catch (_err) {
    /* ignore */
  }
}

/**
 * @param {{
 *   scopes?: string[],
 *   shopId?: string|number,
 *   stockUpdates?: Array<{id:string|number, quantity:number}>,
 *   soldSerials?: Array<{itemId:string|number, serials:string[]}>,
 *   reason?: string,
 *   softReload?: boolean,
 *   mode?: "wipe"|"sale",
 * }} [opts]
 */
export async function notifyCatalogChanged(opts = {}) {
  const scopes =
    Array.isArray(opts.scopes) && opts.scopes.length ? opts.scopes : ["all"];
  const shopId = opts.shopId != null ? String(opts.shopId) : "";
  const stockUpdates = Array.isArray(opts.stockUpdates) ? opts.stockUpdates : [];
  const soldSerials = Array.isArray(opts.soldSerials) ? opts.soldSerials : [];
  const softReload = opts.softReload !== false;
  const mode = opts.mode === "sale" ? "sale" : "wipe";

  try {
    if (mode === "sale") {
      if (shopId && stockUpdates.length) {
        await patchCachedShopCatalogStock(shopId, stockUpdates);
      }
      // Keep shop-catalog pages (patched); drop stock/item snapshots.
      await invalidateCatalogCaches({ scopes: ["stock", "item"] });
    } else {
      await invalidateCatalogCaches({ scopes });
    }
  } catch (_err) {
    /* cache optional */
  }

  if (soldSerials.length && shopId) {
    for (const row of soldSerials) {
      try {
        await removeCachedShopSerials(shopId, row.itemId, row.serials || []);
      } catch (_err) {
        /* optional */
      }
    }
  }

  const detail = {
    scopes,
    shopId,
    stockUpdates,
    reason: opts.reason || "mutation",
    softReload,
    mode,
    at: Date.now(),
  };
  emitLocal(detail);
  try {
    getChannel()?.postMessage(detail);
  } catch (_err) {
    /* ignore */
  }
  return detail;
}

/**
 * Subscribe to catalog invalidation (same tab + other tabs).
 * @param {(detail: object) => void} handler
 * @returns {() => void} unsubscribe
 */
export function onCatalogInvalidate(handler) {
  if (typeof handler !== "function") return () => {};

  const onLocal = (event) => handler(event.detail || {});
  document.addEventListener(EVENT_NAME, onLocal);

  const ch = getChannel();
  const onMessage = (event) => handler(event.data || {});
  ch?.addEventListener("message", onMessage);

  return () => {
    document.removeEventListener(EVENT_NAME, onLocal);
    ch?.removeEventListener("message", onMessage);
  };
}

/** Soft-reload catalogs when the tab becomes visible again (online). */
export function watchCatalogVisibility(reloadFn) {
  if (typeof reloadFn !== "function" || typeof document === "undefined") {
    return () => {};
  }
  let last = 0;
  const onVis = () => {
    if (document.visibilityState !== "visible") return;
    const now = Date.now();
    if (now - last < 4000) return;
    last = now;
    import("./net.js")
      .then(({ isAppOnline }) => isAppOnline())
      .then((online) => {
        if (online) reloadFn();
      })
      .catch(() => {});
  };
  document.addEventListener("visibilitychange", onVis);
  return () => document.removeEventListener("visibilitychange", onVis);
}
