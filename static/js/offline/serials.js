/**
 * Shop-floor serial search with IndexedDB fallback for offline selling.
 */

import * as store from "./store.js";
import { isOnline } from "./connectivity.js";

export function filterCachedSerials(
  serials,
  { query = "", match = "contains", exclude = [], limit = 12 } = {}
) {
  const q = String(query || "").trim().toUpperCase();
  const ex = new Set(
    (exclude || [])
      .map((serial) => String(serial || "").trim().toUpperCase())
      .filter(Boolean)
  );
  let rows = (serials || [])
    .map((serial) => String(serial || "").trim().toUpperCase())
    .filter((serial) => serial && !ex.has(serial));

  if (q) {
    if (match === "last4" || match === "endswith" || match === "suffix") {
      rows = rows.filter((serial) => serial.slice(-4).includes(q));
      rows.sort((a, b) => {
        const rank = (serial) => {
          const suffix = serial.slice(-4);
          if (suffix === q) return 0;
          if (suffix.startsWith(q)) return 1;
          if (serial.endsWith(q)) return 2;
          return 3;
        };
        return rank(a) - rank(b) || a.localeCompare(b);
      });
    } else {
      rows = rows.filter((serial) => serial.includes(q));
      rows.sort((a, b) => {
        const rank = (serial) => {
          if (serial === q) return 0;
          if (serial.startsWith(q)) return 1;
          if (serial.endsWith(q)) return 2;
          return 3;
        };
        return rank(a) - rank(b) || a.localeCompare(b);
      });
    }
  } else {
    rows.sort();
  }

  const cap = Math.max(1, Number(limit) || 12);
  return rows.slice(0, cap);
}

export async function searchSerialsOnlineOrCache({
  url,
  itemId,
  shopId,
  query = "",
  match = "contains",
  exclude = [],
  limit = 12,
} = {}) {
  if (!url || !itemId || !shopId) {
    return { ok: false, results: [], error: "unavailable" };
  }

  const params = new URLSearchParams({
    item_id: String(itemId),
    shop_id: String(shopId),
    q: query || "",
    match: match || "contains",
    limit: String(limit || 12),
  });
  (exclude || []).forEach((serial) => params.append("exclude", serial));

  if (isOnline()) {
    try {
      const response = await fetch(`${url}?${params.toString()}`, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      const data = await response.json().catch(() => ({}));
      const results = Array.isArray(data.results) ? data.results : [];
      if (response.status === 503 || data.offline) {
        throw new Error("offline");
      }
      if (response.ok) {
        try {
          if (!query) {
            await store.cacheShopSerials(shopId, itemId, results);
          } else {
            await store.mergeCachedShopSerials(shopId, itemId, results);
          }
        } catch (_cacheErr) {
          /* cache optional */
        }
        return { ok: true, results, fromCache: false };
      }
      return {
        ok: false,
        results: [],
        error: data.error || "Could not search serials.",
      };
    } catch (_err) {
      /* fall through to cache */
    }
  }

  try {
    const cached = await store.getCachedShopSerials(shopId, itemId);
    if (cached.length) {
      return {
        ok: true,
        results: filterCachedSerials(cached, {
          query,
          match,
          exclude,
          limit: 12,
        }),
        fromCache: true,
      };
    }
  } catch (_cacheErr) {
    /* ignore */
  }

  return {
    ok: false,
    offline: true,
    results: [],
    error: "offline_serial_miss",
  };
}
