

"use strict";
const SHELL = "memu-shell-v1";
const SCOPE = new URL(self.registration.scope);
const SKIP = /^(api\/|m\/|music\/|health|healthz)/;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function shellKey(request, url) {

  return request.mode === "navigate" ? new URL("./", SCOPE).href : url.href;
}

async function networkFirst(request, key) {
  const cache = await caches.open(SHELL);
  try {
    const response = await Promise.race([
      fetch(request),
      new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), request.mode === "navigate" ? 6000 : 8000)),
    ]);
    if (response.ok) {
      cache.put(key, response.clone()).catch(() => {});
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
  event.respondWith(networkFirst(request, shellKey(request, url)));
});
