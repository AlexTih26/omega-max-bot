const CACHE_NAME = "rumex-app-shell-v3";
const APP_SHELL_URLS = [
  "/rumex-accountant-login.html",
  "/rumex-accountant-registry.html",
  "/rumex-accountant-registry.css?v=6",
  "/rumex-accountant-registry.js?v=6",
  "/rumex-accountant-manifest.json",
  "/rumex-accountant-icon-192.png",
  "/rumex-accountant-icon-512.png",
  "/rumex-test-loading-login.html",
  "/rumex-test-loading-login.js?v=1",
  "/rumex-test-loading.html",
  "/rumex-test-loading.css?v=5",
  "/rumex-test-loading.js?v=7",
  "/rumex-test-dispatcher-manifest.json",
  "/rumex-test-dispatcher-icon-192.png",
  "/rumex-test-dispatcher-icon-512.png",
  "/rumex-pwa.js?v=2"
];
const APP_SHELL = new Set(APP_SHELL_URLS);

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(function (cache) { return cache.addAll(APP_SHELL_URLS); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.map(function (key) {
          return key.startsWith("rumex-app-shell-") && key !== CACHE_NAME
            ? caches.delete(key)
            : undefined;
        }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (!APP_SHELL.has(url.pathname + url.search)) return;

  event.respondWith(
    fetch(request)
      .then(function (response) {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(function (cache) { return cache.put(request, copy); });
        }
        return response;
      })
      .catch(function () {
        return caches.match(request).then(function (cached) {
          return cached || Response.error();
        });
      })
  );
});
