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

export async function cacheEmployeeIdCheck(code, result) {
  return cacheSet(`emp_id:${code}`, result, 60 * 60 * 24);
}

export async function getCachedEmployeeIdCheck(code) {
  return cacheGet(`emp_id:${code}`);
}
