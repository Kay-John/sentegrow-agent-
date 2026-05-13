const CACHE = "sentegrow-v2";
const STATIC = [
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  const { request } = e;
  const url = new URL(request.url);

  // Static assets — cache first
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(request).then(cached => cached || fetch(request).then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(request, clone));
        return res;
      }))
    );
    return;
  }

  // Dashboard pages — network first, fall back to cache
  if (url.pathname.startsWith("/dashboard") || url.pathname === "/") {
    e.respondWith(
      fetch(request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(request, clone));
          return res;
        })
        .catch(() => caches.match(request).then(cached => cached || new Response(
          "<h2 style='font-family:sans-serif;padding:40px'>You're offline. Open the app when connected.</h2>",
          { headers: { "Content-Type": "text/html" } }
        )))
    );
    return;
  }

  // Everything else — network only
  e.respondWith(fetch(request));
});
