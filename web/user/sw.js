const CACHE_NAME = "vskr-user-shell-v19";
const AUTH_PAGES = new Set(["/login", "/register"]);
const SHELL = [
  "/",
  "/login",
  "/register",
  "/manifest.webmanifest",
  "/shared/tokens.css",
  "/shared/api.js",
  "/user/assets/styles.css?v=chat-shell-r2",
  "/user/assets/app.js?v=chat-shell-r2",
  "/user/assets/login.js",
  "/user/assets/icons/icon-192.png",
  "/user/assets/icons/icon-512.png",
  "/user/assets/icons/icon.svg",
];
const SHELL_PATHS = new Set(SHELL.map((asset) => new URL(asset, self.location.href).pathname));

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
  if (AUTH_PAGES.has(url.pathname)) {
    event.respondWith(fetch(url.href, { cache: "no-store", credentials: "same-origin" }).then((response) => {
      if (response.ok) caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone()));
      return response;
    }).catch(() => caches.match(request)));
    return;
  }
  const isUserAsset = url.pathname === "/" || AUTH_PAGES.has(url.pathname)
    || url.pathname.startsWith("/user/assets/")
    || url.pathname.startsWith("/shared/");
  if (!isUserAsset || (url.pathname !== "/" && !SHELL_PATHS.has(url.pathname))) return;
  event.respondWith(fetch(request, { cache: "no-store" }).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
    }
    return response;
  }).catch(() => caches.match(request)));
});
