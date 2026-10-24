/* Round 28: a tiny message bus between the Quick Ask window (/quick/) and its screen layer (/quick/layer.html).

   send(to, event, data): POST /api/quick/layer/relay — the server pushes the message into the other window
   (evaluate_js → MemuQuickBus.receive). When it cannot (not the Windows app, the other window hidden; tests) the
   message goes over a BroadcastChannel instead (both pages share one browser profile). Every message has an id and
   is delivered once. The relay refuses unknown events; a «run» relayed in the first 300 ms after the layer
   appeared is dropped by the server (the click that only finished opening it). */
(function () {
  "use strict";
  if (window.MemuQuickBus) return;
  const SELF = document.documentElement.dataset.quickLayer === "1" ? "layer" : "quick";
  const handlers = new Map();
  const seen = new Set();
  let channel = null;
  try { channel = typeof BroadcastChannel === "function" ? new BroadcastChannel("memu-quick-layer") : null; } catch (e) { channel = null; }
  const rid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  function receive(msg) {
    if (!msg || typeof msg !== "object" || msg.to !== SELF || !msg.id || seen.has(msg.id)) return false;
    seen.add(msg.id);
    if (seen.size > 400) seen.delete(seen.values().next().value);
    (handlers.get(msg.event) || []).slice().forEach((fn) => { try { fn(msg.data, msg); } catch (e) { console.warn("[MemuQuickBus]", e); } });
    return true;
  }
  if (channel) channel.onmessage = (e) => receive(e.data);

  async function send(to, event, data) {
    const msg = { id: rid(), to, event, data: data === undefined ? null : data, from: SELF };
    let relayed = false, guarded = false;
    try {
      const r = await fetch("/api/quick/layer/relay", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(msg) });
      const out = await r.json().catch(() => ({}));
      relayed = !!out.relayed; guarded = !!out.guarded;
    } catch (e) { relayed = false; }
    if (!relayed && !guarded && channel) { try { channel.postMessage(msg); } catch (e) { /* closed */ } }
    return { relayed, guarded, id: msg.id };
  }
  function on(event, fn) {
    if (!handlers.has(event)) handlers.set(event, []);
    handlers.get(event).push(fn);
    return () => { const list = handlers.get(event) || []; const i = list.indexOf(fn); if (i >= 0) list.splice(i, 1); };
  }
  window.MemuQuickBus = { self: SELF, send, on, receive };
})();
