/* MemuAI Mini App — service worker (Round 37, MemuMusic offline).
 *
 * Keeps the app shell (index.html, scripts, styles, icons) so the Mini App still opens when the computer is off or the
 * phone has no internet, and «Музыка → Скачано» plays the tracks saved on the phone. Network first: while the computer
 * answers, everything comes fresh from it (no stale versions); the copy is only the fallback, also for the Cloudflare
 * error page (5xx) a tunnel returns while MemuAI Proxy is down. The API, module documents and the music streams are
 * never touched — they always go to the computer.
 * Limits: one copy per address (origin) — a new quick-tunnel address starts empty until it is opened once online;
 * iOS Telegram (WKWebView) has no service workers at all, there the offline mode works only while the app is open.
 */
"use strict";
const SHELL = "memu-shell-v1";
const SCOPE = new URL(self.registration.scope);
const SKIP = /^(api\/|m\/|music\/|health|healthz)/;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

function shellKey(request, url) {
  // every navigation (?tab=…, #tgWebAppData…) is the same page
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
  // same origin only (the worker's CSP is connect-src 'self'; telegram-web-app.js is vendored in vendor/)
  if (url.origin !== SCOPE.origin || !url.pathname.startsWith(SCOPE.pathname)) return;
  if (SKIP.test(url.pathname.slice(SCOPE.pathname.length))) return;
  event.respondWith(networkFirst(request, shellKey(request, url)));
});
