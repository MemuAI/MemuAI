

(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const S = { token: params.get("token") || "", lang: params.get("lang") === "en" ? "en" : "ru", start: null, rect: null, sent: false };
  const tr = (ru, en) => (S.lang === "en" ? en : ru);

  function hint() {
    $("rg-hint").innerHTML = `<b>${tr("Выделите область", "Drag to select a region")}</b>`
      + `<span><kbd>Enter</kbd>${tr("весь экран", "whole screen")}</span><span><kbd>Esc</kbd>${tr("отмена", "cancel")}</span>`;
    document.documentElement.lang = S.lang;
  }
  function load() {
    document.body.classList.remove("ready", "selecting");
    const img = $("rg-shot");
    img.onload = () => document.body.classList.add("ready");
    img.src = S.token ? "/api/quick/features/region/image?token=" + encodeURIComponent(S.token) + "&t=" + Date.now() : "";
  }
  function send(rect) {
    if (S.sent) return;
    S.sent = true;
    fetch("/api/quick/features/region/done", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: S.token, rect }) }).catch(() => {});
    if (window.RegionPick.onDone) window.RegionPick.onDone(rect);
  }
  function box(a, b) {
    return { left: Math.min(a.x, b.x), top: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) };
  }
  function draw() {
    const sel = $("rg-sel");
    if (!S.rect) { sel.hidden = true; return; }
    const r = S.rect;
    sel.hidden = false;
    Object.assign(sel.style, { left: r.left + "px", top: r.top + "px", width: r.width + "px", height: r.height + "px" });
    const img = $("rg-shot");
    const kx = (img.naturalWidth || innerWidth) / innerWidth, ky = (img.naturalHeight || innerHeight) / innerHeight;
    $("rg-size").textContent = `${Math.round(r.width * kx)} × ${Math.round(r.height * ky)}`;
    sel.classList.toggle("below", r.top < 30);
  }
  function cross(x, y) {
    $("rg-cx").style.top = y + "px";
    $("rg-cy").style.left = x + "px";
    document.body.classList.add("pointer");
  }
  addEventListener("pointerdown", (e) => {
    if (S.sent) return;
    if (e.button === 2) { e.preventDefault(); send(null); return; }
    if (e.button !== 0) return;
    S.start = { x: e.clientX, y: e.clientY };
    S.rect = box(S.start, S.start);
    document.body.classList.add("selecting");
    try { e.target.setPointerCapture(e.pointerId); } catch (_) {  }
    draw();
  });
  addEventListener("pointermove", (e) => {
    cross(e.clientX, e.clientY);
    if (!S.start) return;
    S.rect = box(S.start, { x: e.clientX, y: e.clientY });
    draw();
  });
  addEventListener("pointerup", (e) => {
    if (!S.start || e.button !== 0) return;
    const r = box(S.start, { x: e.clientX, y: e.clientY });
    S.start = null;
    if (r.width < 6 || r.height < 6) {
      S.rect = null; draw(); document.body.classList.remove("selecting"); return;
    }
    send({ x: r.left / innerWidth, y: r.top / innerHeight, w: r.width / innerWidth, h: r.height / innerHeight });
  });
  addEventListener("contextmenu", (e) => e.preventDefault());
  addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); send(null); }
    else if (e.key === "Enter") { e.preventDefault(); send({ x: 0, y: 0, w: 1, h: 1 }); }
  });

  window.RegionPick = {
    start(opts = {}) { S.token = String(opts.token || ""); S.lang = opts.lang === "en" ? "en" : "ru"; S.sent = false; S.start = null; S.rect = null; draw(); hint(); load(); return true; },
    focus() { window.focus(); return true; },
    reset() { S.sent = true; S.start = null; S.rect = null; draw(); $("rg-shot").removeAttribute("src"); document.body.classList.remove("ready", "selecting", "pointer"); return true; },
    onDone: null,
  };
  hint();
  load();
})();
