const CACHE_NAME = "vskr-user-shell-v6";
const SHELL = [
  "/login",
  "/manifest.webmanifest",
  "/shared/tokens.css",
  "/shared/api.js",
  "/user/assets/styles.css",
  "/user/assets/app.js",
  "/user/assets/login.js",
  "/user/assets/icons/icon-192.png",
  "/user/assets/icons/icon-512.png",
  "/user/assets/icons/icon.svg",
];

if (self.location.protocol === "http:" || self.location.protocol === "https:") self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith("vskr-user-shell-") && key !== CACHE_NAME).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  if (!SHELL.includes(url.pathname)) return;
  event.respondWith(caches.match(request).then((cached) => cached || fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
    }
    return response;
  })));
});
