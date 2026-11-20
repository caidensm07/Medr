// Medr service worker: makes Medr installable and opens instantly. Always tries the network first,
// so updates show up right away; falls back to the saved copy only when offline. Never caches research data.
const CACHE = "medr-shell-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/vendor/marked.js", "/static/vendor/purify.js",
  "/static/themes.json", "/static/icons/icon-192.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {})); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && (url.pathname === "/" || url.pathname.startsWith("/static/"))) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then(m => m || caches.match("/"))));
});
