/* MY-SHOP service worker — offline shell with network-first when online */
const CACHE_VERSION = "myshop-v3";

const PRECACHE = [
  "/static/css/main.css",
  "/static/js/main.js",
  "/static/js/auth.js",
  "/static/js/register.js",
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

const cacheResponse = async (request, response) => {
  if (!response?.ok || !isSameOrigin(new URL(request.url))) return;
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

  if (isNavigation(request)) {
    event.respondWith(networkFirst(request, "/"));
    return;
  }

  event.respondWith(networkFirst(request));
});
