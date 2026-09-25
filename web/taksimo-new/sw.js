const CACHE_NAME = "taksimo-new-shell-v2";
const APP_SHELL_URLS = [
  "/taksimo-new/index.html",
  "/taksimo-new/login.html",
  "/taksimo-new/app.css",
  "/taksimo-new/app.js",
  "/taksimo-new/pwa.js",
  "/taksimo-new/manifest.json",
  "/taksimo-new/icon.svg"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function (cache) { return cache.addAll(APP_SHELL_URLS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        return key.startsWith("taksimo-new-shell-") && key !== CACHE_NAME ? caches.delete(key) : undefined;
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (!APP_SHELL_URLS.includes(url.pathname)) return;
  event.respondWith(
    fetch(request).then(function (response) {
      if (response.ok) caches.open(CACHE_NAME).then(function (cache) { return cache.put(request, response.clone()); });
      return response;
    }).catch(function () {
      return caches.match(request).then(function (cached) { return cached || Response.error(); });
    })
  );
});
