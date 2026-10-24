/* Round 40 (r40-lag): long-lived streams of MemuAI over a WebSocket (server: app/live_ws.py).
 *
 * All MemuAI windows share one WebView2 profile, and Chromium gives that profile only 6 HTTP connections per host.
 * Every EventSource and long-poll held one of them; with six held, every other request of every window waited —
 * the «random» freezes of Quick Access and of MemuAI. A WebSocket does not count against that pool.
 *
 *   MemuLive.stream(url)      — an EventSource look-alike: onmessage / onerror / onopen, addEventListener(type, fn)
 *                               for named «event:» blocks, readyState, close(); reconnects by itself (like EventSource).
 *                               If the socket cannot be opened at all, a real EventSource is used instead.
 *   MemuLive.post(url, body)  — one POST answered over a socket (long-polls: MemuMusic «player.wait»); resolves to
 *                               the parsed JSON answer, rejects with Error(message) on a 4xx/5xx; falls back to fetch.
 * Nothing runs until a page asks for a stream.
 */
(function () {
  "use strict";
  if (window.MemuLive) return;
  const wsUrl = (path, method) => (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/pg/live?path="
    + encodeURIComponent(path) + (method ? "&method=" + method : "");
  const usable = () => typeof WebSocket !== "undefined" && /^https?:$/.test(location.protocol);
  let broken = false;   // the server has no /api/pg/live (an older build): plain HTTP from then on

  function stream(url) {
    if (!usable() || broken) return new EventSource(url);
    const target = new EventTarget();
    const es = {
      url, readyState: 0, onmessage: null, onerror: null, onopen: null,
      addEventListener(type, fn, opts) {
        if (!named.has(type)) { named.add(type); if (fallback) forward(type); }
        target.addEventListener(type, fn, opts);
      },
      removeEventListener: (type, fn, opts) => target.removeEventListener(type, fn, opts),
      close() { es.readyState = 2; clearTimeout(retry); if (ws) { try { ws.close(); } catch (_) { /* gone */ } ws = null; } if (fallback) fallback.close(); },
    };
    let ws = null, buf = "", retry = 0, everOpen = false, fallback = null;
    const named = new Set(["message", "open", "error"]);
    const forward = (type) => fallback.addEventListener(type, (e) => fire(type, e.data, e.lastEventId));
    const fire = (type, data, lastId) => {
      const ev = new MessageEvent(type, { data, lastEventId: lastId || "" });
      if (type === "message" && typeof es.onmessage === "function") es.onmessage(ev);
      target.dispatchEvent(ev);
    };
    const fail = () => { if (typeof es.onerror === "function") es.onerror(new Event("error")); target.dispatchEvent(new Event("error")); };
    function parse() {
      let cut;
      while ((cut = buf.search(/\r?\n\r?\n/)) >= 0) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut).replace(/^\r?\n\r?\n/, "");
        let type = "message", id = "";
        const data = [];
        for (const line of block.split(/\r?\n/)) {
          if (!line || line[0] === ":") continue;
          const at = line.indexOf(":");
          const key = at < 0 ? line : line.slice(0, at);
          const value = at < 0 ? "" : line.slice(at + 1).replace(/^ /, "");
          if (key === "data") data.push(value); else if (key === "event") type = value || "message"; else if (key === "id") id = value;
        }
        if (data.length) fire(type, data.join("\n"), id);
      }
    }
    function connect() {
      if (es.readyState === 2) return;
      buf = "";
      try { ws = new WebSocket(wsUrl(url)); } catch (_) { ws = null; }
      if (!ws) return useFallback();
      ws.onopen = () => {
        everOpen = true; es.readyState = 1;
        if (typeof es.onopen === "function") es.onopen(new Event("open"));
        target.dispatchEvent(new Event("open"));
      };
      ws.onmessage = (e) => { buf += String(e.data || ""); parse(); };
      ws.onclose = () => {
        ws = null;
        if (es.readyState === 2) return;
        if (!everOpen) { broken = true; return useFallback(); }
        es.readyState = 0;
        fail();
        if (es.readyState !== 2) retry = setTimeout(connect, 3000);   // the server restarts with MemuAI
      };
    }
    function useFallback() {
      if (es.readyState === 2) return;
      fallback = new EventSource(url);
      fallback.onopen = (e) => { es.readyState = 1; if (typeof es.onopen === "function") es.onopen(e); target.dispatchEvent(new Event("open")); };
      fallback.onmessage = (e) => fire("message", e.data, e.lastEventId);
      fallback.onerror = () => { es.readyState = fallback.readyState; fail(); };
      for (const type of named) if (!["message", "open", "error"].includes(type)) forward(type);   // named «event:» blocks
    }
    connect();
    return es;
  }

  async function viaFetch(url, body) {
    const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.detail || "HTTP " + res.status);
    return data;
  }
  function post(url, body) {
    if (!usable() || broken) return viaFetch(url, body);
    return new Promise((resolve, reject) => {
      let ws, answered = false, opened = false;
      try { ws = new WebSocket(wsUrl(url, "POST")); } catch (_) { viaFetch(url, body).then(resolve, reject); return; }
      ws.onopen = () => { opened = true; ws.send(JSON.stringify(body || {})); };
      ws.onmessage = (e) => {
        answered = true;
        let out = {}, data = {};
        try { out = JSON.parse(e.data); } catch (_) { /* broken frame */ }
        try { data = JSON.parse(out.body || "{}"); } catch (_) { data = {}; }
        try { ws.close(); } catch (_) { /* closed */ }
        if (out.status >= 200 && out.status < 300) resolve(data);
        else reject(new Error(data.error || data.detail || "HTTP " + (out.status || 0)));
      };
      ws.onclose = () => {
        if (answered) return;
        if (!opened) { broken = true; viaFetch(url, body).then(resolve, reject); return; }
        reject(new Error("closed"));
      };
    });
  }
  window.MemuLive = { stream, post };
})();
