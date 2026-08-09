/* MY-SHOP service worker — offline shell with network-first when online */
const CACHE_VERSION = "myshop-v7";

const PRECACHE = [
  "/static/css/main.css",
  "/static/js/main.js",
  "/static/js/auth.js",
  "/static/js/register.js",
  "/static/js/my-shop.js",
  "/static/js/shop-catalog.js",
  "/static/js/stock-catalog.js",
  "/static/js/item-catalog.js",
  "/static/js/shop-printer.js",
  "/static/js/shop-printer-session.js",
  "/static/js/offline/store.js",
  "/static/js/offline/connectivity.js",
  "/static/js/offline/sync.js",
  "/static/js/offline/client.js",
  "/static/js/offline/init.js",
  "/static/manifest.webmanifest",
  "/manifest.webmanifest",
];

const isApi = (url) => url.pathname.includes("/api/");

const isNavigation = (request) =>
  request.mode === "navigate" ||
  (request.method === "GET" &&
    request.headers.get("accept")?.includes("text/html"));

const isSameOrigin = (url) => url.origin === self.location.origin;

/** Auth HTML embeds CSRF tokens — never cache (stale token → 403 on POST). */
const isAuthPath = (url) => {
  const path = url.pathname.replace(/\/+$/, "") || "/";
  return (
    path === "/employees/login" ||
    path === "/employees/register" ||
    path === "/employees/logout" ||
    path === "/shops/login" ||
    path === "/shop/login" ||
    path.endsWith("/login") ||
    path.endsWith("/register") ||
    path.endsWith("/logout")
  );
};

const cacheResponse = async (request, response) => {
  if (!response?.ok || !isSameOrigin(new URL(request.url))) return;
  const url = new URL(request.url);
  if (isAuthPath(url) || isNavigation(request)) return;
  const cache = await caches.open(CACHE_VERSION);
  await cache.put(request, response.clone());
};

const networkFirst = (request, fallbackUrl) =>
  fetch(request)
    .then(async (response) => {
      await cacheResponse(request, response);
      return response;
    })
    .catch(async () => {
      const url = new URL(request.url);
      if (isAuthPath(url)) {
        return new Response("Offline — refresh when back online to sign in.", {
          status: 503,
          statusText: "Offline",
          headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
      }
      const cached = await caches.match(request);
      if (cached) return cached;
      if (fallbackUrl) {
        const fallback = await caches.match(fallbackUrl);
        if (fallback) return fallback;
      }
      return new Response("Offline", { status: 503, statusText: "Offline" });
    });

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_VERSION)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (!isSameOrigin(url)) return;

  if (isApi(url)) {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(JSON.stringify({ ok: false, offline: true }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    return;
  }

  // Never serve cached login/register HTML (CSRF tokens must be fresh).
  if (isAuthPath(url)) {
    event.respondWith(
      fetch(request).catch(
        () =>
          new Response("Offline — refresh when back online to sign in.", {
            status: 503,
            statusText: "Offline",
            headers: { "Content-Type": "text/plain; charset=utf-8" },
          })
      )
    );
    return;
  }

  if (isNavigation(request)) {
    // Network-first for HTML, but do not write navigations into Cache Storage.
    event.respondWith(
      fetch(request).catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        const fallback = await caches.match("/");
        if (fallback) return fallback;
        return new Response("Offline", { status: 503, statusText: "Offline" });
      })
    );
    return;
  }

  event.respondWith(networkFirst(request));
});
