/* Service worker for the phone app. Served from /sw.js, not /static/, because a
 * worker's scope defaults to its own directory and one under /static could
 * never control /m.
 *
 * The shell is cached so the app opens without a network. The API never is:
 * /api/counterpoint/packs looks cacheable but reflects which packs and voice
 * are switched on in the database, so a cached copy would have the phone
 * insisting on a fact count the desktop just changed.
 */
const CACHE = "counterpoint-shell-v1";
const SHELL = ["/m", "/static/m.css", "/static/m.js", "/manifest.webmanifest", "/static/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()));  // a missing asset must not wedge the install
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;                     // POSTs go straight to the network
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;             // never cache an answer or a setting

  if (request.mode === "navigate") {
    // Network first, or a shell update would never land.
    event.respondWith(fetch(request).catch(() => caches.match("/m")));
    return;
  }
  if (SHELL.includes(url.pathname)) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
