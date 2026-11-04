

"use strict";
const SHELL = "memu-shell-v2";
const SCOPE = new URL(self.registration.scope);
const SKIP = /^(api\/|m\/|music\/|health|healthz)/;

self.addEventListener("install", () => self.skipWaiting());
// Wave #9 (D-24): the cache used to be versioned "v1" forever and
// activate() never deleted anything — a stale shell could survive
// every update. Bump SHELL above on each shell change; activate()
// deletes every cache except the current one, then claims the clients.
self.addEventListener("activate", (event) => event.waitUntil((async () => {
  const names = await caches.keys();
  await Promise.all(names.filter((name) => name !== SHELL).map((name) => caches.delete(name)));
  await self.clients.claim();
})()));

function shellKey(request, url) {

  return request.mode === "navigate" ? new URL("./", SCOPE).href : url.href;
}

async function networkFirst(request, key, url) {
  const cache = await caches.open(SHELL);
  try {
    const response = await Promise.race([
      fetch(request),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), request.mode === "navigate" ? 6000 : 8000)),
    ]);
    if (response.ok) {
      // Wave #9: cache shell assets only (the entry page plus .js/.css/
      // .svg/.png/.ico). Anything else the network returns (JSON, a
      // signed media URL, an unexpected content type) is never stored,
      // so a bad response cannot poison the offline shell.
      const type = (response.headers.get("content-type") || "").toLowerCase();
      const shellAsset = request.mode === "navigate"
        || /\.(?:js|css|svg|png|ico)(?:[?#]|$)/i.test(url.pathname)
        || type.startsWith("text/html");
      if (shellAsset) cache.put(key, response.clone()).catch(() => {});
      return response;
    }
    if (response.status >= 500) {
      const copy = await cache.match(key, { ignoreSearch: request.mode !== "navigate" });
      if (copy) return copy;
    }
    return response;
  } catch (error) {
    const copy = await cache.match(key) || await cache.match(key, { ignoreSearch: true });
    if (copy) return copy;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin !== SCOPE.origin || !url.pathname.startsWith(SCOPE.pathname)) return;
  if (SKIP.test(url.pathname.slice(SCOPE.pathname.length))) return;
  event.respondWith(networkFirst(request, shellKey(request, url), url));
});
