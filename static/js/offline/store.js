/**
 * IndexedDB store for offline queue and employee-id cache.
 */
const DB_NAME = "myshop-offline";
const DB_VERSION = 1;
const QUEUE = "queue";
const CACHE = "cache";

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(QUEUE)) {
        db.createObjectStore(QUEUE, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(CACHE)) {
        db.createObjectStore(CACHE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
  });
  return dbPromise;
}

export async function queueAdd(item) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE, "readwrite");
    tx.objectStore(QUEUE).put(item);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function queueAll() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE, "readonly");
    const req = tx.objectStore(QUEUE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function queueRemove(id) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(QUEUE, "readwrite");
    tx.objectStore(QUEUE).delete(id);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function queueCount() {
  const items = await queueAll();
  return items.length;
}

const PENDING_SALES_PREFIX = "pending-sales:v1:";

function pendingSalesKey(shopId) {
  return `${PENDING_SALES_PREFIX}${Number(shopId) || 0}`;
}

/** Rebuild per-shop pending line qty from the offline checkout queue. */
export async function rebuildPendingSalesFromQueue(queue) {
  const byShop = new Map();
  for (const op of queue || []) {
    if (op?.type !== "complete_shop_checkout") continue;
    const payload = op.payload || {};
    const shopId = Number(payload.shop_id) || 0;
    if (!shopId) continue;
    const checkout = payload.checkout || payload;
    const lines = Array.isArray(checkout.lines) ? checkout.lines : [];
    if (!byShop.has(shopId)) byShop.set(shopId, []);
    byShop.get(shopId).push({
      clientId: String(payload.client_id || "").trim(),
      queueId: op.id,
      lines: lines.map((line) => ({
        id: line?.id,
        qty: line?.qty,
      })),
    });
  }
  for (const [shopId, entries] of byShop) {
    await cacheSet(pendingSalesKey(shopId), entries, 60 * 60 * 24 * 14);
  }
}

export async function getPendingSales(shopId) {
  const rows = await cacheGet(pendingSalesKey(shopId));
  return Array.isArray(rows) ? rows : [];
}

export async function pendingQuantitiesForShop(shopId) {
  const entries = await getPendingSales(shopId);
  const qty = {};
  for (const entry of entries) {
    for (const line of entry.lines || []) {
      const id = String(line?.id ?? "");
      const q = Math.max(0, Math.floor(Number(line?.qty) || 0));
      if (!id || !q) continue;
      qty[id] = (qty[id] || 0) + q;
    }
  }
  return qty;
}

/** Soft TTL for money/qty catalogs — prefer network; longer hard TTL kept only offline. */
export const CATALOG_CACHE_TTL_SECONDS = 60 * 15;
/** Offline-friendly TTL when intentionally caching for disconnected use. */
export const CATALOG_CACHE_OFFLINE_TTL_SECONDS = 60 * 60 * 12;

const CATALOG_PREFIXES = [
  "shop-catalog:",
  "stock-catalog:",
  "stock-catalog-preload:",
  "item-catalog:",
];

export async function cacheSet(key, value, ttlSeconds = 300) {
  const expiresAt = Date.now() + ttlSeconds * 1000;
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CACHE, "readwrite");
    tx.objectStore(CACHE).put({ key, value, expiresAt });
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function cacheGet(key) {
  const db = await openDb();
  const row = await new Promise((resolve, reject) => {
    const tx = db.transaction(CACHE, "readonly");
    const req = tx.objectStore(CACHE).get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  if (!row) return null;
  if (row.expiresAt && Date.now() > row.expiresAt) {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(CACHE, "readwrite");
      tx.objectStore(CACHE).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    return null;
  }
  return row.value;
}

export async function cacheDelete(key) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CACHE, "readwrite");
    tx.objectStore(CACHE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function cacheKeys() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(CACHE, "readonly");
    const req = tx.objectStore(CACHE).getAllKeys();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

export async function cacheDeletePrefix(prefix) {
  const needle = String(prefix || "");
  if (!needle) return 0;
  const keys = await cacheKeys();
  const matches = keys.filter((key) => String(key).startsWith(needle));
  if (!matches.length) return 0;
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(CACHE, "readwrite");
    const store = tx.objectStore(CACHE);
    matches.forEach((key) => store.delete(key));
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  return matches.length;
}

/**
 * Drop catalog snapshots so the next online fetch paints fresh qty/prices.
 * @param {{ scopes?: string[] }} [opts]
 *   scopes: "shop" | "stock" | "item" | "all" (default all)
 */
export async function invalidateCatalogCaches({ scopes } = {}) {
  const wanted = new Set(
    (Array.isArray(scopes) && scopes.length ? scopes : ["all"]).map((s) =>
      String(s || "").toLowerCase()
    )
  );
  const all = wanted.has("all");
  const prefixes = CATALOG_PREFIXES.filter((prefix) => {
    if (all) return true;
    if (prefix.startsWith("shop-catalog") && wanted.has("shop")) return true;
    if (prefix.startsWith("stock-catalog") && wanted.has("stock")) return true;
    if (prefix.startsWith("item-catalog") && wanted.has("item")) return true;
    return false;
  });
  let removed = 0;
  for (const prefix of prefixes) {
    removed += await cacheDeletePrefix(prefix);
  }
  return removed;
}

/**
 * Patch stock qty inside cached shop-catalog pages after a sale.
 * Keeps offline fallback aligned with the live DOM.
 */
export async function patchCachedShopCatalogStock(shopId, updates) {
  const id = String(shopId || "").trim();
  if (!id || !Array.isArray(updates) || !updates.length) return 0;
  const qtyById = new Map();
  updates.forEach((row) => {
    const itemId = String(row?.id ?? "").trim();
    if (!itemId) return;
    const qty = Math.max(0, Math.floor(Number(row.quantity) || 0));
    qtyById.set(itemId, qty);
  });
  if (!qtyById.size) return 0;

  const prefix = `shop-catalog:${id}:`;
  const keys = (await cacheKeys()).filter((key) => String(key).startsWith(prefix));
  let patched = 0;
  for (const key of keys) {
    const db = await openDb();
    const row = await new Promise((resolve, reject) => {
      const tx = db.transaction(CACHE, "readonly");
      const req = tx.objectStore(CACHE).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    if (!row?.value?.ok || !Array.isArray(row.value.items)) continue;
    let changed = false;
    const items = row.value.items.map((item) => {
      const itemId = String(item?.id ?? "");
      if (!qtyById.has(itemId)) return item;
      changed = true;
      return { ...item, stock: qtyById.get(itemId) };
    });
    if (!changed) continue;
    const ttlMs = Math.max(0, (row.expiresAt || Date.now()) - Date.now());
    const ttlSeconds = Math.max(60, Math.ceil(ttlMs / 1000));
    await cacheSet(key, { ...row.value, items }, ttlSeconds);
    patched += 1;
  }
  return patched;
}

export async function cacheEmployeeIdCheck(code, result) {
  return cacheSet(`emp_id:${code}`, result, 60 * 60 * 24);
}

export async function getCachedEmployeeIdCheck(code) {
  return cacheGet(`emp_id:${code}`);
}

export async function cacheStaffVerify(code, result, ttlSeconds = 60 * 60 * 24) {
  return cacheSet(`staff_verify:${String(code || "").trim()}`, result, ttlSeconds);
}

export async function getCachedStaffVerify(code) {
  return cacheGet(`staff_verify:${String(code || "").trim()}`);
}

export async function cacheShopSerials(shopId, itemId, serials, ttlSeconds = 60 * 60 * 12) {
  const key = `shop-serials:${shopId}:${itemId}`;
  const list = [
    ...new Set(
      (serials || [])
        .map((serial) => String(serial || "").trim().toUpperCase())
        .filter(Boolean)
    ),
  ];
  return cacheSet(key, list, ttlSeconds);
}

export async function getCachedShopSerials(shopId, itemId) {
  const list = await cacheGet(`shop-serials:${shopId}:${itemId}`);
  return Array.isArray(list) ? list : [];
}

export async function mergeCachedShopSerials(shopId, itemId, serials) {
  const existing = await getCachedShopSerials(shopId, itemId);
  const extra = (serials || [])
    .map((serial) => String(serial || "").trim().toUpperCase())
    .filter(Boolean);
  const next = [...new Set([...existing, ...extra])];
  await cacheShopSerials(shopId, itemId, next);
  return next;
}

/** Drop sold/transferred serials from the offline serial cache. */
export async function removeCachedShopSerials(shopId, itemId, serials) {
  const drop = new Set(
    (serials || [])
      .map((serial) => String(serial || "").trim().toUpperCase())
      .filter(Boolean)
  );
  if (!drop.size) return getCachedShopSerials(shopId, itemId);
  const existing = await getCachedShopSerials(shopId, itemId);
  const next = existing.filter((serial) => !drop.has(serial));
  await cacheShopSerials(shopId, itemId, next);
  return next;
}
