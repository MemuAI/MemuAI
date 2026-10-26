

(function () {
  "use strict";
  const root = document.documentElement;
  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const bus = window.MemuQuickBus;
  const reduced = () => root.dataset.motion === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const PANEL_SIZES = { s: [2, 2], m: [4, 2], l: [4, 4], w: [8, 2] };
  const SNAP = 10;
  const DRAG_START = 5;
  const LONG_PRESS = 520;
  const FIT_MIN_ZOOM = 0.6;
  const UNDO_MAX = 40;
  const UI = {
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M7 7l10 10M17 7 7 17"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="6" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="18" cy="12" r="1.7"/></svg>',
    resize: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M11 5 5 11M11 8.5 8.5 11"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 7 4 12l5 5"/><path d="M4 12h10.5a5.5 5.5 0 0 1 0 11H12"/></svg>',
    redo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 7 5 5-5 5"/><path d="M20 12H9.5a5.5 5.5 0 0 0 0 11H12"/></svg>',
    move: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18M3 12h18"/><path d="m9 6 3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3"/></svg>',
  };
  const S = {
    lang: root.lang === "en" ? "en" : "ru", data: null, monitor: null, keepout: {}, placed: [], hiddenCount: 0,
    visible: false, edit: false, glass: false, shownAt: 0, drag: null, press: null, menu: null, modal: false,
    runs: new Map(), poll: 0, idlePolls: 0, panelsJs: null, panelsReady: false, panelIds: "",
    cell: 72, gap: 8, margin: 12, spacing: 8, pad: 14, regionKey: "", regionRaf: 0, focusId: "", toastTimer: 0,
    loading: null, saving: null, undo: [], redo: [], down: null, fitRaf: 0, fitQueue: new Set(), fit: new Map(),
  };
  const t = (ru, en) => (S.lang === "en" ? en : ru);
  const settings = () => (S.data && S.data.settings) || { enabled: false };
  const actionsById = () => new Map(((S.data && S.data.actions) || []).map((a) => [a.id, a]));
  const panelsList = () => (window.MemuPanels && MemuPanels.state ? MemuPanels.state.panels || [] : []);
  const panelById = (id) => panelsList().find((p) => p.id === id) || null;
  const unitPx = (u) => u * S.cell + (u - 1) * S.gap;
  const itemNode = (id) => (id ? $("ql-items").querySelector(`.ql-item[data-item="${CSS.escape(id)}"]`) : null);
  const placedById = (id) => S.placed.find((i) => i.id === id) || null;
  const SIZE_NAMES = () => ({ s: t("Малая", "Small"), m: t("Средняя", "Medium"), l: t("Большая", "Large"), w: t("Широкая", "Wide") });

  async function api(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const r = await fetch(path, init);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
    return data;
  }

  function faceHtml(action) {
    const ic = action.icon || { kind: "builtin", value: "bolt" };
    if (ic.kind === "emoji") return `<span class="ql-emoji">${esc(ic.value)}</span>`;
    if ((ic.kind === "image" || ic.kind === "app") && /^data:image\//.test(ic.value || "")) return `<img alt="" src="${esc(ic.value)}" draggable="false">`;
    return window.MemuCanvasIcons ? MemuCanvasIcons.svg(ic.value) : "";
  }
  function faceStyle(action) {
    const c1 = /^#[0-9a-f]{6}$/i.test(action.color || "") ? action.color : "";
    const c2 = /^#[0-9a-f]{6}$/i.test(action.color2 || "") ? action.color2 : "";
    return c1 ? `--ql-c1:${c1};--ql-c2:${c2 || c1};` : "";
  }
  const tipOf = (a) => [a.label, a.tooltip, a.hotkey].filter(Boolean).join(" · ") || t("Кнопка", "Button");

  const hits = (x, y, w, h, o) => x < o.x + o.w - 0.01 && o.x < x + w - 0.01 && y < o.y + o.h - 0.01 && o.y < y + h - 0.01;
  const inflate = (r, p) => ({ x: r.x - p, y: r.y - p, w: r.w + 2 * p, h: r.h + 2 * p });
  const asRect = (a) => (Array.isArray(a) ? { x: +a[0], y: +a[1], w: +a[2], h: +a[3] } : a);
  function bounds(w, h, area, margin) {
    const lx = area.w >= w + 2 * margin ? [margin, area.w - margin - w] : [0, area.w - w];
    const ly = area.h >= h + 2 * margin ? [margin, area.h - margin - h] : [0, area.h - h];
    return { lx, ly };
  }

  function nearestFree(x, y, w, h, obstacles, area, margin = S.margin) {
    if (w > area.w || h > area.h) return null;
    const { lx, ly } = bounds(w, h, area, margin);
    const clampV = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    const xs = new Set([clampV(x, lx[0], lx[1]), lx[0], lx[1]]);
    const ys = new Set([clampV(y, ly[0], ly[1]), ly[0], ly[1]]);
    for (const o of obstacles) { xs.add(o.x + o.w); xs.add(o.x - w); ys.add(o.y + o.h); ys.add(o.y - h); }
    const X = [...xs].filter((v) => v >= lx[0] - 1e-6 && v <= lx[1] + 1e-6).sort((a, b) => Math.abs(a - x) - Math.abs(b - x));
    const Y = [...ys].filter((v) => v >= ly[0] - 1e-6 && v <= ly[1] + 1e-6).sort((a, b) => Math.abs(a - y) - Math.abs(b - y));
    let best = null, bestD = Infinity;
    for (const cy of Y) {
      if ((cy - y) ** 2 >= bestD) break;
      for (const cx of X) {
        const d = (cx - x) ** 2 + (cy - y) ** 2;
        if (d >= bestD) break;
        if (!obstacles.some((o) => hits(cx, cy, w, h, o))) { best = { x: cx, y: cy }; bestD = d; break; }
      }
    }
    return best;
  }

  function snapXY(x, y, w, h, others, area) {
    const tx = [[S.margin, S.margin], [area.w - S.margin - w, area.w - S.margin], [(area.w - w) / 2, area.w / 2]];
    const ty = [[S.margin, S.margin], [area.h - S.margin - h, area.h - S.margin], [(area.h - h) / 2, area.h / 2]];
    for (const o of others) {
      tx.push([o.x, o.x], [o.x + o.w - w, o.x + o.w], [o.x + o.w + S.spacing, o.x + o.w + S.spacing / 2], [o.x - S.spacing - w, o.x - S.spacing / 2], [o.x + (o.w - w) / 2, o.x + o.w / 2]);
      ty.push([o.y, o.y], [o.y + o.h - h, o.y + o.h], [o.y + o.h + S.spacing, o.y + o.h + S.spacing / 2], [o.y - S.spacing - h, o.y - S.spacing / 2], [o.y + (o.h - h) / 2, o.y + o.h / 2]);
    }
    const pick = (v, list) => { let b = v, line = null, bd = SNAP + 0.001; for (const [c, l] of list) { const d = Math.abs(c - v); if (d < bd) { bd = d; b = c; line = l; } } return [b, line]; };
    const [sx, gx] = pick(x, tx);
    const [sy, gy] = pick(y, ty);
    return { x: sx, y: sy, gx: gx == null ? [] : [gx], gy: gy == null ? [] : [gy] };
  }
  function obstaclesFor(id, { soft = true } = {}) {
    const out = S.placed.filter((p) => p.id !== id && !p.hidden).map((p) => inflate(p, S.spacing));
    const ko = S.keepout || {};
    if (ko.hard) out.push(inflate(asRect(ko.hard), S.pad));
    if (soft && ko.soft) out.push(inflate(asRect(ko.soft), S.pad));
    return out;
  }
  function freeSpot(id, x, y, w, h) {
    return nearestFree(x, y, w, h, obstaclesFor(id), S.monitor) || nearestFree(x, y, w, h, obstaclesFor(id, { soft: false }), S.monitor);
  }

  async function loadData() {
    try { S.data = await api("/api/quick/canvas?lang=" + S.lang); } catch (e) { S.data = S.data || null; }
    return S.data;
  }
  async function resolve() {
    if (!S.monitor) return;
    const body = { monitor: S.monitor, keepout: S.keepout };
    if (S.panelsReady) body.panels = panelsList().map((p) => p.id);
    try {
      const out = await api("/api/quick/layer/resolve", body);
      applyResolved(out);
    } catch (e) { toast(t("Не удалось разложить панели: ", "Could not lay out the panels: ") + e.message); }
  }
  function applyResolved(out) {
    S.placed = (out.items || []).map((i) => ({ ...i }));
    S.hiddenCount = out.hidden || 0;
    S.cell = out.cell || S.cell; S.gap = out.gap || S.gap; S.margin = out.margin || S.margin;
    S.spacing = out.spacing || S.spacing; S.pad = out.keepout_pad || S.pad;
    render();
    bus && bus.send("quick", "layer-state", publicState());
  }
  function publicState() {
    return { visible: S.visible, edit: S.edit, shown: S.placed.filter((p) => !p.hidden).length, hidden: S.hiddenCount, monitor: S.monitor };
  }
  function ensurePanels() {
    if (!settings().panels || !S.placed.some((p) => p.type === "panel")) return Promise.resolve(false);
    if (S.panelsJs) return S.panelsJs;
    window.MEMU_PANELS_EMBED = true;
    window.MEMU_PANELS_HOST = {
      toast: (text) => toast(text),
      ask: (prompt) => { bus && bus.send("quick", "ask", { prompt: String(prompt || "") }); },
    };
    S.panelsJs = new Promise((resolveP) => {
      if (window.MemuPanels) return resolveP(true);
      const s = document.createElement("script");
      s.src = "/panels.js?v=1.7-r30"; s.async = true;
      s.onload = () => resolveP(!!window.MemuPanels); s.onerror = () => resolveP(false);
      document.head.appendChild(s);

      const m = document.createElement("script");
      m.src = "/module-integration.js?v=1.1-r38"; m.async = true;
      document.head.appendChild(m);
    }).then((ok) => {
      if (!ok || typeof MemuPanels.subscribe !== "function") return false;
      MemuPanels.subscribe(() => {
        S.panelsReady = true;
        const ids = panelsList().map((p) => p.id).sort().join(",");
        if (ids !== S.panelIds) { S.panelIds = ids; if (S.visible) resolve(); return; }
        renderPanels();
      });
      if (!S.visible && MemuPanels.setPaused) MemuPanels.setPaused(true);
      return true;
    });
    return S.panelsJs;
  }

  function panelSize(p) {
    const [w, h] = p.units || [2, 2];
    if (w <= 2) return "s";
    if (h >= 4) return "l";
    if (w >= 6) return "w";
    return "m";
  }
  function sizeKeyOf(p) {
    const u = p.units || [2, 2];
    const hit = Object.entries(PANEL_SIZES).find(([, v]) => v[0] === u[0] && v[1] === u[1]);
    return hit ? hit[0] : "";
  }
  function runBadge(id) {
    const run = S.runs.get(id);
    if (!run) return "";
    const pct = run.total ? Math.round(((run.status === "done" ? run.total : Math.max(0, run.step)) / run.total) * 100) : 0;
    return `<i class="ql-ring" data-status="${esc(run.status)}" style="--ql-p:${pct}" aria-hidden="true"></i>`;
  }
  function chromeHtml(p) {
    const panel = p.type === "panel";
    return `<button type="button" class="ql-cx" data-item-act="remove" tabindex="-1" title="${esc(p.type === "action" ? t("Удалить кнопку", "Delete the button") : t("Убрать с экрана", "Remove from the screen"))}" aria-label="${esc(t("Убрать", "Remove"))}">${UI.x}</button>
      <button type="button" class="ql-more" data-item-act="menu" tabindex="-1" title="${esc(t("Действия · правый щелчок", "Actions · right click"))}" aria-label="${esc(t("Действия", "Actions"))}" aria-haspopup="menu" aria-expanded="false">${UI.more}</button>
      <span class="ql-rz" data-rz="se" title="${esc(t("Потяните, чтобы изменить размер", "Drag to resize"))}">${UI.resize}</span>
      ${panel ? '<span class="ql-edge" data-rz="e" aria-hidden="true"></span><span class="ql-edge" data-rz="s" aria-hidden="true"></span>' : ""}
      <span class="ql-badge" aria-hidden="true" title="${esc(t("Содержимое не помещается — увеличьте панель или «Подогнать размер» в меню", "The content does not fit — make the panel bigger or use “Fit the size to the content” in the menu"))}">${esc(t("Тесно", "Too small"))}</span>`;
  }
  function buttonHtml(a, p) {
    return `<button type="button" class="ql-act" data-run="${esc(a.id)}" data-shape="${esc(a.shape)}" data-big="${p.units[0] === 2 ? 1 : 0}" style="${faceStyle(a)}" title="${esc(tipOf(a))}" tabindex="-1">
      <span class="ql-face">${faceHtml(a)}${runBadge(a.id)}</span>${a.label && settings().labels !== false ? `<span class="ql-label">${esc(a.label)}</span>` : ""}</button>`;
  }

  const lookOf = (p) => (window.MemuPanelLook ? MemuPanelLook.resolve(S.data, p.ref) : { style: "card" });
  function panelHtml(p) {
    const panel = panelById(p.ref);
    if (!panel || !window.MemuPanels) return `<div class="ql-skel" aria-hidden="true"><i></i><i></i><i></i></div>`;
    const face = window.MemuPanelLook ? MemuPanelLook.faceHtml(panel, lookOf(p), { place: "screen" }) : null;
    return face || MemuPanels.cardHtml(panel, "home", { size: panelSize(p), static: true, place: "quick" });
  }

  function ensureNode(box, p) {
    let node = itemNode(p.id);
    if (!node) {
      node = document.createElement("div");
      node.className = "ql-item";
      node.dataset.item = p.id;
      node.innerHTML = `<div class="ql-body"></div><div class="ql-chrome">${chromeHtml(p)}</div>`;
      box.appendChild(node);
      node.addEventListener("animationend", () => scheduleRegion());
    } else if (node.dataset.type !== p.type) {
      node.querySelector(".ql-chrome").innerHTML = chromeHtml(p);
    }
    return node;
  }
  function setBody(node, html) {
    const body = node.querySelector(".ql-body");
    if (body.dataset.html === html) return false;
    const editing = document.activeElement && body.contains(document.activeElement) && document.activeElement.matches("input, textarea, [contenteditable]");
    if (editing) return false;
    body.dataset.html = html;
    const modules = window.MemuModInt && html.includes("mmi-slot");
    if (modules) MemuModInt.park();
    body.innerHTML = html;
    if (modules) MemuModInt.mount();
    return true;
  }
  function render() {
    const box = $("ql-items");
    const ql = $("ql");
    ql.dataset.edit = S.edit ? "1" : "0";
    ql.dataset.visible = S.visible ? "1" : "0";
    ql.dataset.glass = S.glass ? "1" : "0";
    ql.dataset.drag = S.drag ? "1" : "0";
    if (S.monitor) { ql.style.width = S.monitor.w + "px"; ql.style.height = S.monitor.h + "px"; }
    if (!S.visible) { box.innerHTML = ""; S.fit.clear(); scheduleRegion(); return; }
    const acts = actionsById();
    const keep = new Set();
    let n = 0;
    for (const p of S.placed) {
      if (p.hidden) continue;
      const a = p.type === "action" ? acts.get(p.ref) : null;
      if (p.type === "action" && !a) continue;
      keep.add(p.id);
      const node = ensureNode(box, p);
      node.dataset.type = p.type;
      node.dataset.size = p.type === "panel" ? panelSize(p) : (p.units[0] === 2 ? "big" : "normal");
      node.tabIndex = 0;
      node.style.setProperty("--ql-i", String(n++));
      if (!S.drag || S.drag.id !== p.id) Object.assign(node.style, { left: p.x + "px", top: p.y + "px", width: p.w + "px", height: p.h + "px" });
      if (p.type === "panel" && window.MemuPanelLook) MemuPanelLook.apply(node, lookOf(p), { place: "screen" });
      const changed = setBody(node, a ? buttonHtml(a, p) : panelHtml(p));
      if (p.type === "panel" && (changed || !S.fit.has(p.id) || S.fit.get(p.id).w !== p.w || S.fit.get(p.id).h !== p.h)) queueFit(node);
      node.setAttribute("role", a ? "button" : "group");
      node.setAttribute("aria-label", a ? tipOf(a) : (panelById(p.ref) || {}).title || t("Панель", "Panel"));
      node.setAttribute("aria-roledescription", t("перемещаемый элемент", "movable item"));
    }
    box.querySelectorAll(".ql-item").forEach((node) => { if (!keep.has(node.dataset.item)) { S.fit.delete(node.dataset.item); node.remove(); } });
    renderZone();
    renderBar();
    scheduleRegion();
    panelsLive();
  }
  function renderPanels() {
    if (!S.visible) return;
    for (const p of S.placed) {
      if (p.type !== "panel" || p.hidden) continue;
      const node = itemNode(p.id);
      if (!node) continue;
      if (setBody(node, panelHtml(p))) queueFit(node);
    }
    scheduleRegion();
    panelsLive();
  }

  function panelsLive() {
    if (S.visible && window.MemuPanels && typeof MemuPanels.syncLive === "function") MemuPanels.syncLive();
  }
  function renderRings() {
    $("ql-items").querySelectorAll("[data-run]").forEach((btn) => {
      const old = btn.querySelector(".ql-ring");
      const html = runBadge(btn.dataset.run);
      if (old) old.remove();
      if (html) btn.querySelector(".ql-face").insertAdjacentHTML("beforeend", html);
      btn.setAttribute("aria-busy", html && !/data-status="(done|error|cancelled)"/.test(html) ? "true" : "false");
    });
  }

  function renderZone() {
    const zone = $("ql-zone");
    const soft = S.keepout && S.keepout.soft;
    if (!(S.edit || S.drag) || !soft) { zone.hidden = true; return; }
    const r = asRect(soft);
    zone.hidden = false;
    Object.assign(zone.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px" });
    const hard = S.keepout.hard ? asRect(S.keepout.hard) : null;
    const below = hard ? Math.max(10, hard.y + hard.h - r.y + 12) : 10;
    const html = `<span style="top:${below}px">${esc(t("Место быстрого доступа и его ответа — сюда ничего не ставится", "Room of Quick Ask and its answer — nothing goes here"))}</span>`;
    if (zone.dataset.html !== html) { zone.dataset.html = html; zone.innerHTML = html; }
  }
  function renderBar() {
    const bar = $("ql-bar");
    if (!S.edit) { bar.hidden = true; return; }
    bar.hidden = false;
    const hidden = S.hiddenCount ? ` · ${esc(t(`не поместилось: ${S.hiddenCount}`, `did not fit: ${S.hiddenCount}`))}` : "";
    const html = `<span class="ql-bar-ico" aria-hidden="true">${UI.move}</span><span class="ql-bar-text"><b>${esc(t("Расстановка", "Arrange"))}</b><span>${esc(t("Тяните · угол и края — размер · Alt — без прилипания", "Drag · corner and edges resize · Alt — no snapping"))}${hidden}</span></span>
      <span class="ql-bar-sep" aria-hidden="true"></span>
      <button type="button" class="ql-bbtn ql-bbtn-i" data-bar="undo" title="${esc(t("Отменить · Ctrl+Z", "Undo · Ctrl+Z"))}" aria-label="${esc(t("Отменить", "Undo"))}"${S.undo.length ? "" : " disabled"}>${UI.undo}</button>
      <button type="button" class="ql-bbtn ql-bbtn-i" data-bar="redo" title="${esc(t("Вернуть · Ctrl+Shift+Z", "Redo · Ctrl+Shift+Z"))}" aria-label="${esc(t("Вернуть", "Redo"))}"${S.redo.length ? "" : " disabled"}>${UI.redo}</button>
      <button type="button" class="ql-bbtn" data-bar="add">${UI.plus}<span>${esc(t("Добавить", "Add"))}</span></button>
      <button type="button" class="ql-bbtn ql-bbtn-p" data-bar="done">${UI.check}<span>${esc(t("Готово", "Done"))}</span><kbd>Esc</kbd></button>`;
    if (bar.dataset.html !== html) { bar.dataset.html = html; bar.innerHTML = html; }

    const soft = S.keepout && S.keepout.soft ? asRect(S.keepout.soft) : null;
    const hard = S.keepout && S.keepout.hard ? asRect(S.keepout.hard) : null;
    const room = soft && hard ? soft.y + soft.h - (hard.y + hard.h) : 0;
    if (soft && hard && room >= 70) {
      bar.dataset.at = "zone";
      const top = soft.y + soft.h - 62 > hard.y + hard.h + 10 ? soft.y + soft.h - 62 : hard.y + hard.h + 12;
      Object.assign(bar.style, { left: soft.x + soft.w / 2 + "px", top: top + "px", bottom: "auto", maxWidth: Math.max(300, soft.w - 16) + "px" });
    } else {
      bar.dataset.at = "bottom";
      Object.assign(bar.style, { left: "", top: "", bottom: "", maxWidth: "" });
    }
  }

  function queueFit(node) {
    if (!node) return;
    S.fitQueue.add(node);
    if (!S.fitRaf) S.fitRaf = requestAnimationFrame(flushFit);
  }
  function flushFit() {
    S.fitRaf = 0;
    const nodes = Array.from(S.fitQueue);
    S.fitQueue.clear();
    nodes.forEach((n) => { if (n.isConnected) fitCard(n); });
    scheduleRegion();
  }
  const overflows = (el) => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;

  function fitCard(node) {
    const card = node.querySelector(".ql-body > .mp-card");
    const id = node.dataset.item;
    if (!card) return null;
    const p = placedById(id);
    const W = node.clientWidth || (p && p.w) || 0, H = node.clientHeight || (p && p.h) || 0;
    card.style.zoom = ""; card.style.width = ""; card.style.height = "";
    card.dataset.fit = "0";
    card.querySelectorAll("[data-ql-cut]").forEach((r) => { r.style.display = ""; delete r.dataset.qlCut; });
    card.querySelectorAll(".mp-list-more[data-ql-text]").forEach((b) => { const s = b.querySelector(":scope > span:not(.mp-ico)"); if (s) s.textContent = b.dataset.qlText; delete b.dataset.qlText; });
    const need = card.scrollHeight;
    let level = 0;
    while (overflows(card) && level < 3) { level += 1; card.dataset.fit = String(level); }

    const list = card.querySelector(".mp-list");
    if (list && overflows(card) && card.clientHeight / Math.max(1, card.scrollHeight) < 0.9) {
      const rows = Array.from(list.children).filter((r) => !r.matches(".mp-list-label, .mp-list-more"));
      let cut = 0;
      while (overflows(card) && rows.length - cut > 1) { cut += 1; rows[rows.length - cut].style.display = "none"; rows[rows.length - cut].dataset.qlCut = "1"; }
      const more = list.querySelector(".mp-list-more:not(.ghost) > span:not(.mp-ico)");
      if (cut && more) {
        const btn = more.parentElement, was = more.textContent, left = parseInt(was.replace(/\D+/g, ""), 10) || 0;
        btn.dataset.qlText = was;
        more.textContent = (document.documentElement.lang === "en" || /more/i.test(was)) ? `${left + cut} more` : `Ещё ${left + cut}`;
      }
    }
    let zoom = 1;
    if (overflows(card)) {
      const ratio = Math.min(card.clientHeight / Math.max(1, card.scrollHeight), card.clientWidth / Math.max(1, card.scrollWidth));
      zoom = Math.max(FIT_MIN_ZOOM, Math.floor(ratio * 100) / 100);
      for (let i = 0; i < 8; i++) {
        card.style.zoom = String(zoom);
        card.style.width = W / zoom + "px";
        card.style.height = H / zoom + "px";
        if (!overflows(card) || zoom <= FIT_MIN_ZOOM) break;
        zoom = Math.max(FIT_MIN_ZOOM, Math.round((zoom - 0.04) * 100) / 100);
      }
    }
    const clipped = overflows(card);
    card.dataset.clipped = clipped ? "1" : "0";
    node.dataset.overflow = clipped || zoom < 0.8 ? "1" : "0";
    const out = { level, zoom, clipped, need, w: p ? p.w : W, h: p ? p.h : H };
    S.fit.set(id, out);
    observeCard(card);
    if (cardObserver) cardObserver.takeRecords();
    return out;
  }
  let cardObserver = null;
  function observeCard(card) {
    if (!window.MutationObserver) return;
    if (!cardObserver) {
      cardObserver = new MutationObserver((list) => {
        const nodes = new Set();
        for (const m of list) { const n = m.target.closest && m.target.closest(".ql-item"); if (n) nodes.add(n); else if (m.target.parentElement) { const q = m.target.parentElement.closest(".ql-item"); if (q) nodes.add(q); } }
        nodes.forEach((n) => queueFit(n));
      });
    }
    if (card.dataset.qlObserved) return;
    card.dataset.qlObserved = "1";
    cardObserver.observe(card, { childList: true, subtree: true, characterData: true });
  }

  function fitUnits(p) {
    const f = S.fit.get(p.id);
    if (!f) return null;
    const m = maxUnits();
    const hu = Math.max(2, Math.min(m.h, Math.ceil((f.need + 2 + S.gap) / (S.cell + S.gap))));
    return [p.units[0], hu];
  }

  function scheduleRegion() {
    cancelAnimationFrame(S.regionRaf);
    S.regionRaf = requestAnimationFrame(reportRegion);
  }

  function regionRects() {
    const out = [];
    const pad = S.glass ? 2 : 1;
    const addBox = (x, y, w, h, radius) => { if (w >= 1 && h >= 1) out.push([Math.floor(x) - pad, Math.floor(y) - pad, Math.ceil(w) + 2 * pad, Math.ceil(h) + 2 * pad, radius]); };
    const add = (el, radius) => {
      const r = el.getBoundingClientRect();
      addBox(r.left, r.top, r.width, r.height, radius != null ? radius : parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0);
    };
    document.querySelectorAll(".ql-item").forEach((el) => {
      const inner = el.querySelector(".ql-body > .mp-card, .ql-body > .ql-act, .ql-body > .ql-skel");
      const radius = inner ? parseFloat(getComputedStyle(inner).borderTopLeftRadius) || 14 : 14;
      addBox(el.offsetLeft, el.offsetTop, el.offsetWidth, el.offsetHeight, radius);
    });
    document.querySelectorAll(".mcm, .ql-toast.on, .mp-pop, .mp-menu").forEach((el) => add(el));
    return out;
  }
  function reportRegion() {
    if (!S.visible) return;
    const menuFull = !!S.menu && S.glass;
    const full = S.edit || S.modal || !!S.drag || menuFull;
    const rects = full ? [] : regionRects();
    const view = { w: innerWidth, h: innerHeight, dpr: Math.round((window.devicePixelRatio || 1) * 1000) / 1000 };
    const key = JSON.stringify([full, rects, view]);
    if (key === S.regionKey) return;
    S.regionKey = key;
    api("/api/quick/layer/region", { rects, full, view }).catch(() => { if (S.regionKey === key) S.regionKey = ""; });
  }

  function toast(text, undo, opts = {}) {
    const el = $("ql-toast");
    clearTimeout(S.toastTimer);
    el.innerHTML = `<span>${esc(text)}</span>${undo ? `<button type="button" class="ql-bbtn" data-toast="undo">${UI.undo}<span>${esc(t("Отменить", "Undo"))}</span></button>` : ""}`;
    el._undo = undo || null;
    el.classList.add("on");
    scheduleRegion();
    S.toastTimer = setTimeout(() => { el.classList.remove("on"); el._undo = null; scheduleRegion(); }, opts.ms || (undo ? 6000 : 3500));
  }
  function hideToast() { const el = $("ql-toast"); clearTimeout(S.toastTimer); el.classList.remove("on"); el._undo = null; scheduleRegion(); }

  const snapshot = () => S.placed.filter((p) => !p.hidden).map((p) => ({ id: p.id, x: p.x, y: p.y, w: p.w, h: p.h, units: p.units.slice() }));
  function applySnapshot(snap) {
    for (const s of snap) {
      const p = placedById(s.id);
      if (p) Object.assign(p, { x: s.x, y: s.y, w: s.w, h: s.h, units: s.units.slice() });
    }
    render();
    return save();
  }

  function pushLayoutUndo(before, label) {
    const after = snapshot();
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    pushUndo({ label, undo: () => applySnapshot(before), redo: () => applySnapshot(after) });
  }
  function pushUndo(entry) {
    S.undo.push(entry);
    if (S.undo.length > UNDO_MAX) S.undo.shift();
    S.redo = [];
    renderBar();
  }
  async function undo() {
    const e = S.undo.pop();
    if (!e) { toast(t("Нечего отменять", "Nothing to undo"), null, { ms: 1600 }); return; }
    try { await e.undo(); S.redo.push(e); toast(t("Отменено: ", "Undone: ") + e.label, null, { ms: 1800 }); }
    catch (err) { toast(err.message); }
    renderBar();
  }
  async function redo() {
    const e = S.redo.pop();
    if (!e) return;
    try { await e.redo(); S.undo.push(e); toast(t("Возвращено: ", "Redone: ") + e.label, null, { ms: 1800 }); }
    catch (err) { toast(err.message); }
    renderBar();
  }

  function closeMenu(reason) {
    if (!S.menu) return;
    const h = S.menu;
    S.menu = null;
    if (h.handle) h.handle.close(reason || "api");
    scheduleRegion();
  }
  function menuItems(p) {
    const items = [];
    if (p.type === "action") {
      const a = actionsById().get(p.ref) || {};
      items.push({ type: "head", label: a.label || t("Кнопка", "Button"), sub: t("Быстрое действие", "Quick action"), icon: "bolt" },
        { id: "run", label: t("Запустить", "Run"), icon: "play", hint: "Enter" },
        { id: "edit", label: t("Изменить кнопку…", "Edit the button…"), icon: "edit" },
        { id: "duplicate", label: t("Дублировать", "Duplicate"), icon: "copy" },
        { type: "seg", id: "bsize", label: t("Размер", "Size"), icon: "size", options: [
          { id: "1", label: t("Обычная", "Normal"), pressed: p.units[0] !== 2 }, { id: "2", label: t("Большая", "Large"), pressed: p.units[0] === 2 }] });
    } else {
      const panel = panelById(p.ref) || {};
      const key = sizeKeyOf(p);
      const sub = (key ? SIZE_NAMES()[key] : t("Свой размер", "Custom size")) + ` · ${p.units[0]}×${p.units[1]}`;
      items.push({ type: "head", label: panel.title || t("Панель", "Panel"), sub, icon: "grid" },
        { type: "seg", id: "size", label: t("Размер", "Size"), icon: "size", options: Object.keys(PANEL_SIZES).map((k) => ({ id: k, label: k.toUpperCase(), title: SIZE_NAMES()[k], pressed: key === k })) });
      const f = S.fit.get(p.id);
      const fu = fitUnits(p);
      if (f && fu && (fu[1] !== p.units[1]) && (f.level > 0 || f.zoom < 1 || f.clipped)) {
        items.push({ id: "fit", label: t("Подогнать размер под содержимое", "Fit the size to the content"), icon: "fit", title: t(`Высота ${fu[1]} ед. — всё видно без уменьшения`, `Height ${fu[1]} units — everything shows unscaled`) });
      }
      if (window.MemuPanelLook) {
        const look = lookOf(p);
        const own = !!(S.data && S.data.looks && S.data.looks[p.ref]);
        const embedded = ((S.data && S.data.embed && S.data.embed.items) || []).some((e) => e.ref === p.ref && !e.hidden);
        items.push(MemuPanelLook.menuSeg(look));
        if (own) items.push({ id: "look-reset", label: t("Вид как у всех панелей", "Look like all panels"), icon: "reset" });
        items.push({ id: "copy-value", label: t("Копировать значение", "Copy the value"), icon: "copy" },
          { id: "embed", label: embedded ? t("Убрать из окна быстрого доступа", "Remove from the Quick Access window") : t("Встроить в окно быстрого доступа", "Build into the Quick Access window"), icon: "sidebar" });
      }
      items.push({ type: "sep" });
      if (!["todo", "notes", "clock", "timers"].includes(panel.kind)) items.push({ id: "refresh", label: t("Обновить сейчас", "Refresh now"), icon: "refresh" });
      items.push({ id: "options", label: t("Параметры панели…", "Panel options…"), icon: "sliders" },
        { id: "memuai", label: t("Открыть в MemuAI", "Open in MemuAI"), icon: "open" });
    }
    items.push({ type: "sep" },
      { id: "arrange", label: S.edit ? t("Закончить расстановку", "Finish arranging") : t("Расставить всё", "Arrange everything"), icon: "move", hint: "Ctrl+E" });
    if (S.undo.length) items.push({ id: "undo", label: t("Отменить: ", "Undo: ") + S.undo[S.undo.length - 1].label, icon: "undo", hint: "Ctrl+Z" });
    items.push({ type: "sep" },
      { id: "remove", label: p.type === "action" ? t("Удалить кнопку", "Delete the button") : t("Убрать с экрана", "Remove from the screen"), icon: "trash", danger: true, hint: "Del" });
    return items;
  }
  function openMenu(itemId, where = {}) {
    const p = placedById(itemId);
    if (!p) return;
    if (!window.MemuCtxMenu) return;
    closeMenu("replaced");
    const node = itemNode(itemId);
    const owner = node ? node.querySelector(".ql-more") : null;
    S.focusId = itemId;
    const opts = {
      items: menuItems(p), owner, returnFocus: node || undefined, keyboard: !!where.keyboard, container: $("ql"),
      label: t("Действия", "Actions"), swallowOutside: true,
      boundary: { left: 0, top: 0, right: S.monitor ? S.monitor.w : innerWidth, bottom: S.monitor ? S.monitor.h : innerHeight },
      onPick: (id, item, opt) => { itemCommand(itemId, item.type === "seg" ? item.id + ":" + opt : id); },
      onClose: () => { if (S.menu && S.menu.id === itemId) S.menu = null; $("ql").dataset.menu = "0"; scheduleRegion(); },
    };
    if (where.x != null) { opts.x = where.x; opts.y = where.y; } else opts.anchor = owner && getComputedStyle(owner).opacity !== "0" ? owner : node;
    const handle = MemuCtxMenu.open(opts);
    if (!handle) return;
    S.menu = { id: itemId, handle };
    $("ql").dataset.menu = "1";
    scheduleRegion();
  }
  async function itemCommand(itemId, cmd) {
    const p = placedById(itemId);
    if (!p) return;
    if (cmd === "arrange") return setEdit(!S.edit, true);
    if (cmd === "remove") return removeItem(p);
    if (cmd === "undo") return undo();
    if (p.type === "action") {
      if (cmd === "edit") { bus && bus.send("quick", "edit-button", { id: p.ref }); return; }
      if (cmd === "run") return run(p.ref, true);
      if (cmd === "duplicate") {
        try { await api("/api/quick/canvas/actions/" + encodeURIComponent(p.ref) + "/duplicate", { lang: S.lang }); await reload(true); }
        catch (e) { toast(e.message); }
        return;
      }
      if (cmd.startsWith("bsize:")) { const u = cmd.endsWith(":2") ? 2 : 1; return resizeTo(p, u, u); }
      if (cmd === "size") { const u = p.units[0] === 2 ? 1 : 2; return resizeTo(p, u, u); }
    } else {
      if (cmd.startsWith("size:")) { const v = PANEL_SIZES[cmd.slice(5)]; if (v) return resizeTo(p, v[0], v[1]); return; }
      if (cmd === "fit") { const fu = fitUnits(p); if (fu) return resizeTo(p, fu[0], fu[1]); return; }
      if (cmd.startsWith("pl-style:") || cmd === "look-reset" || cmd === "embed") return lookCommand(p, cmd);
      if (cmd === "copy-value") {
        const text = window.MemuPanelLook ? MemuPanelLook.valueText(panelById(p.ref), lookOf(p)) : "";
        try { await navigator.clipboard.writeText(text); toast(t("Скопировано", "Copied"), null, { ms: 1400 }); } catch (e) { toast(t("Не удалось скопировать", "Could not copy")); }
        return;
      }
      if (cmd === "refresh" && window.MemuPanels) { MemuPanels.refreshNow(p.ref); toast(t("Обновляю…", "Refreshing…"), null, { ms: 1500 }); return; }
      if (cmd === "options" && window.MemuPanels) { MemuPanels.openEditor(p.ref); return; }
      if (cmd === "memuai") { api("/api/quick/window", { action: "handoff", handoff: { open: "panels", settings: "panels" } }).catch(() => {}); return; }
    }
  }
  async function lookCommand(p, cmd) {
    const body = { panel: p.ref };
    if (cmd === "look-reset") body.reset = true;
    else if (cmd === "embed") {
      const items = (((S.data && S.data.embed) || {}).items || []).map((e) => ({ ...e }));
      const row = items.find((e) => e.ref === p.ref);
      if (row && !row.hidden) row.hidden = true; else if (row) row.hidden = false; else items.push({ ref: p.ref, hidden: false });
      const place = (S.data.embed || {}).place;
      body.embed = { items, place: place && place !== "off" ? place : "below" };
      delete body.panel;
    } else body.look = { style: cmd.slice(9) };
    try { await api("/api/quick/canvas/look", body); await reload(true); }
    catch (e) { toast(e.message); }
  }
  async function removeItem(p) {
    if (p.type === "action") {
      try {
        const resp = await fetch("/api/quick/canvas/actions/" + encodeURIComponent(p.ref) + "?lang=" + S.lang, { method: "DELETE" });
        const out = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(out.error || "HTTP " + resp.status);
        await reload(true);
        let removed = out.removed;
        const entry = {
          label: t("удаление кнопки", "deleting the button"),
          undo: async () => { await api("/api/quick/canvas/actions/restore", { removed, lang: S.lang }); await reload(true); },
          redo: async () => {
            const r2 = await fetch("/api/quick/canvas/actions/" + encodeURIComponent(p.ref) + "?lang=" + S.lang, { method: "DELETE" });
            const o2 = await r2.json().catch(() => ({}));
            if (!r2.ok) throw new Error(o2.error || "HTTP " + r2.status);
            removed = o2.removed;
            await reload(true);
          },
        };
        pushUndo(entry);
        toast(t("Кнопка удалена", "Button deleted"), () => { const i = S.undo.indexOf(entry); if (i >= 0) S.undo.splice(i, 1); entry.undo().then(() => { S.redo.push(entry); renderBar(); }).catch((e) => toast(e.message)); });
      } catch (e) { toast(e.message); }
      return;
    }
    const before = ((S.data && S.data.layout && S.data.layout.items) || []).map((i) => ({ ...i }));
    const without = before.filter((i) => i.id !== p.id);
    try {
      await api("/api/quick/canvas/layout", { items: without });
      await reload(true);
      const entry = {
        label: t("удаление панели", "removing the panel"),
        undo: async () => { await api("/api/quick/canvas/layout", { items: before }); await reload(true); },
        redo: async () => { await api("/api/quick/canvas/layout", { items: without }); await reload(true); },
      };
      pushUndo(entry);
      toast(t("Панель убрана с экрана (в MemuAI она осталась)", "Panel removed from the screen (it stays in MemuAI)"), () => { const i = S.undo.indexOf(entry); if (i >= 0) S.undo.splice(i, 1); entry.undo().then(() => { S.redo.push(entry); renderBar(); }).catch((e) => toast(e.message)); });
    } catch (e) { toast(e.message); }
  }

  function focusItem(id) {
    const node = itemNode(id);
    if (node) { S.focusId = id; node.focus({ preventScroll: true }); }
  }
  function maxUnits() {
    const fit = (px) => Math.max(1, Math.floor((px - 2 * S.margin + S.gap) / (S.cell + S.gap)));
    return { w: Math.min(8, fit(S.monitor.w)), h: Math.min(8, fit(S.monitor.h)) };
  }
  function resizeTo(p, wu, hu, { record = true } = {}) {
    const m = maxUnits();
    if (p.type === "panel") { wu = Math.max(2, Math.min(wu, m.w)); hu = Math.max(2, Math.min(hu, m.h)); }
    else { wu = hu = wu >= 2 ? 2 : 1; }
    if (wu === p.units[0] && hu === p.units[1]) return null;
    const before = snapshot();
    const w = unitPx(wu), h = unitPx(hu);
    const spot = freeSpot(p.id, p.x, p.y, w, h);
    if (!spot) { toast(t("Здесь не хватает места", "There is no room for that here")); return null; }
    Object.assign(p, { units: [wu, hu], w, h, x: spot.x, y: spot.y });
    render();
    if (record) pushLayoutUndo(before, t("размер", "size"));
    return save();
  }
  function save() {
    const items = S.placed.filter((p) => !p.hidden).map((p) => ({ id: p.id, x: p.x, y: p.y, units: p.units }));
    const body = { monitor: S.monitor, keepout: S.keepout, items };
    if (S.panelsReady) body.panels = panelsList().map((p) => p.id);
    S.saving = api("/api/quick/layer/move", body)
      .then((out) => { if (!S.drag) applyResolved(out); if (S.data && out.layout) S.data.layout = out.layout; })
      .catch((e) => toast(t("Не удалось сохранить расположение: ", "Could not save the arrangement: ") + e.message))
      .finally(() => { S.saving = null; });
    return S.saving;
  }

  const TEXT_FIELD = "input, textarea, select, [contenteditable=''], [contenteditable='true']";
  function onPointerDown(e) {
    const node = e.target.closest && e.target.closest(".ql-item");
    if (node) S.down = { id: node.dataset.item, x: e.clientX, y: e.clientY, at: performance.now() };
    if (e.button !== 0 || !node || S.drag) return;
    if (e.target.closest("[data-item-act]")) return;
    const rz = e.target.closest("[data-rz]");
    if (rz) { e.preventDefault(); startDrag(node.dataset.item, e, rz.dataset.rz); return; }
    if (e.target.closest(TEXT_FIELD)) return;
    clearPress();
    const id = node.dataset.item;
    S.press = { id, x: e.clientX, y: e.clientY, pointerId: e.pointerId, timer: setTimeout(() => {
      if (!S.press || S.press.id !== id) return;
      S.press.long = true;
      S.suppressClickUntil = performance.now() + 60000;
      node.classList.add("ql-lifted");
      if (!S.edit) setEdit(true, true);
    }, LONG_PRESS) };
  }
  function clearPress() {
    if (S.press) { clearTimeout(S.press.timer); const n = itemNode(S.press.id); if (n) n.classList.remove("ql-lifted"); }
    S.press = null;
  }
  function startDrag(id, e, resize, from) {
    const p = placedById(id);
    const node = itemNode(id);
    if (!p || !node) return;
    closeMenu("drag");
    S.suppressClickUntil = 0;
    const sx = from ? from.x : e.clientX, sy = from ? from.y : e.clientY;
    S.drag = { id, resize: resize || "", sx, sy, origin: { ...p, units: p.units.slice() }, before: snapshot(), moved: false, pointerId: e.pointerId,
      target: { x: p.x, y: p.y, w: p.w, h: p.h, units: p.units.slice() } };
    node.classList.add(resize ? "ql-resizing" : "ql-dragging");
    node.classList.remove("ql-lifted");
    try { $("ql").setPointerCapture(e.pointerId); } catch (err) {  }
    let ghost = $("ql").querySelector(".ql-ghost");
    if (!ghost) { ghost = document.createElement("div"); ghost.className = "ql-ghost"; $("ql-items").appendChild(ghost); }
    placeGhost(S.drag.target);
    $("ql").dataset.drag = "1";
    renderZone();
    scheduleRegion();
  }
  function placeGhost(r) {
    const ghost = $("ql").querySelector(".ql-ghost");
    if (ghost) Object.assign(ghost.style, { left: r.x + "px", top: r.y + "px", width: r.w + "px", height: r.h + "px", display: r.bad ? "none" : "" });
  }
  function drawGuides(gx, gy) {
    const box = $("ql-guides");
    if (!box) return;
    const html = gx.map((x) => `<i class="ql-guide v" style="left:${Math.round(x)}px"></i>`).join("") + gy.map((y) => `<i class="ql-guide h" style="top:${Math.round(y)}px"></i>`).join("");
    if (box.dataset.html !== html) { box.dataset.html = html; box.innerHTML = html; }
  }
  function onPointerMove(e) {
    if (S.press && !S.drag) {
      const dist = Math.abs(e.clientX - S.press.x) + Math.abs(e.clientY - S.press.y);
      if (dist >= DRAG_START) {
        const press = S.press;
        clearTimeout(press.timer);
        S.press = null;
        startDrag(press.id, e, "", { x: press.x, y: press.y });
      } else return;
    }
    const d = S.drag;
    if (!d) return;
    const node = itemNode(d.id);
    if (!node) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
    const o = d.origin;
    const others = S.placed.filter((p) => p.id !== d.id && !p.hidden);
    let tg;
    if (d.resize) {
      const ddx = d.resize === "s" ? 0 : dx, ddy = d.resize === "e" ? 0 : dy;
      if (o.type === "action") {
        const big = o.w + ddx > unitPx(1) * 1.5 || o.h + ddy > unitPx(1) * 1.5;
        const u = big ? 2 : 1;
        tg = { units: [u, u], w: unitPx(u), h: unitPx(u) };
      } else {
        const m = maxUnits();
        const wu = Math.max(2, Math.min(m.w, Math.round((o.w + ddx + S.gap) / (S.cell + S.gap))));
        const hu = Math.max(2, Math.min(m.h, Math.round((o.h + ddy + S.gap) / (S.cell + S.gap))));
        tg = { units: [wu, hu], w: unitPx(wu), h: unitPx(hu) };
      }
      node.style.width = Math.max(o.type === "action" ? tg.w : unitPx(2), o.w + ddx) + "px";
      node.style.height = Math.max(o.type === "action" ? tg.h : unitPx(2), o.h + ddy) + "px";
      const spot = freeSpot(d.id, o.x, o.y, tg.w, tg.h);
      tg = spot ? { ...tg, x: spot.x, y: spot.y } : { ...tg, x: o.x, y: o.y, bad: true };
      drawGuides([], []);
      queueFit(node);
    } else {
      const x = o.x + dx, y = o.y + dy;
      node.style.left = x + "px"; node.style.top = y + "px";
      const snapped = e.altKey ? { x, y, gx: [], gy: [] } : snapXY(x, y, o.w, o.h, others, S.monitor);
      if (!e.altKey && (snapped.gx.length || snapped.gy.length)) { node.style.left = snapped.x + "px"; node.style.top = snapped.y + "px"; }
      const spot = freeSpot(d.id, snapped.x, snapped.y, o.w, o.h);
      const exact = spot && Math.abs(spot.x - snapped.x) < 0.5 && Math.abs(spot.y - snapped.y) < 0.5;
      drawGuides(exact ? snapped.gx : [], exact ? snapped.gy : []);
      tg = spot ? { x: spot.x, y: spot.y, w: o.w, h: o.h, units: o.units.slice() } : { x: o.x, y: o.y, w: o.w, h: o.h, units: o.units.slice() };
    }
    d.target = tg;
    placeGhost(tg);
  }
  function onPointerUp(e) {
    if (S.press) {
      const long = S.press.long;
      clearPress();
      if (long) { S.suppressClickUntil = performance.now() + 250; if (e && e.type === "pointerup") { const n = e.target.closest && e.target.closest(".ql-item"); if (n) focusItem(n.dataset.item); } }
      return;
    }
    const d = S.drag;
    if (!d) return;
    S.drag = null;
    try { $("ql").releasePointerCapture(d.pointerId); } catch (err) {  }
    const node = itemNode(d.id);
    if (node) node.classList.remove("ql-dragging", "ql-resizing");
    const ghost = $("ql").querySelector(".ql-ghost");
    if (ghost) ghost.remove();
    drawGuides([], []);
    $("ql").dataset.drag = "0";
    if (d.moved) S.suppressClickUntil = performance.now() + 250;
    const p = placedById(d.id);
    if (d.moved && p && !d.target.bad && (e == null || e.type !== "pointercancel")) {
      Object.assign(p, { x: d.target.x, y: d.target.y, w: d.target.w, h: d.target.h, units: d.target.units });
      render();
      pushLayoutUndo(d.before, d.resize ? t("размер", "size") : t("перемещение", "move"));
      save();
      if (!S.edit && S.undo.length === 1) toast(t("Перемещено. Ctrl+Z — отменить, Ctrl+E — расставить всё", "Moved. Ctrl+Z undoes, Ctrl+E arranges everything"), null, { ms: 3200 });
      focusItem(d.id);
      return;
    }
    render();
    if (node) queueFit(node);
  }
  function nudge(id, dx, dy) {
    const p = placedById(id);
    if (!p) return;
    const spot = freeSpot(p.id, p.x + dx, p.y + dy, p.w, p.h);
    if (!spot) return;
    if (!S.nudgeBefore) S.nudgeBefore = snapshot();
    p.x = spot.x; p.y = spot.y;
    render();
    clearTimeout(S.nudgeTimer);
    S.nudgeTimer = setTimeout(() => { const before = S.nudgeBefore; S.nudgeBefore = null; pushLayoutUndo(before, t("перемещение", "move")); save(); }, 350);
    focusItem(id);
  }

  function run(actionId, explicit) {
    if (!explicit && performance.now() - S.shownAt < 300) return;
    const r = S.runs.get(actionId);
    if (r && ["running", "confirm"].includes(r.status)) { toast(t("Уже выполняется", "Already running")); return; }
    bus && bus.send("quick", "run", { id: actionId });
    pollRuns(true);
  }
  function pollRuns(kick) {
    if (kick) S.idlePolls = 0;
    if (S.poll || !S.visible) return;
    const tick = async () => {
      S.poll = 0;
      if (!S.visible) return;
      let runs = [];
      try { runs = (await api("/api/quick/canvas/runs")).runs || []; } catch (e) { runs = []; }
      const active = new Map(runs.map((r) => [r.action, r]));
      for (const [id, r] of S.runs) {
        if (!active.has(id) && ["running", "confirm"].includes(r.status)) S.runs.set(id, { ...r, status: "done", step: r.total });
      }
      for (const [id, r] of active) S.runs.set(id, r);
      renderRings();
      setTimeout(() => { for (const [id] of S.runs) if (!active.has(id)) S.runs.delete(id); renderRings(); }, 1400);
      S.idlePolls = active.size ? 0 : S.idlePolls + 1;
      if (S.idlePolls < 4) S.poll = setTimeout(tick, active.size ? 350 : 700);
    };
    S.poll = setTimeout(tick, 200);
  }

  function setEdit(on, tellNative) {
    on = !!on;
    if (on === S.edit) return;
    S.edit = on;
    closeMenu("mode");
    if (tellNative) api("/api/quick/layer", { action: "edit", on }).catch(() => {});
    render();
    bus && bus.send("quick", "layer-state", publicState());
    if (on) {
      const first = S.placed.find((p) => !p.hidden);
      if (first && !S.focusId) S.focusId = first.id;
    }
  }

  function onClick(e) {
    if (S.suppressClickUntil && performance.now() < S.suppressClickUntil) { e.preventDefault(); e.stopPropagation(); S.suppressClickUntil = 0; return; }
    const tgt = e.target;
    const tb = tgt.closest("[data-toast]");
    if (tb) { const el = $("ql-toast"); const u = el._undo; hideToast(); if (u) u(); return; }
    const bar = tgt.closest("[data-bar]");
    if (bar) {
      const k = bar.dataset.bar;
      if (k === "done") setEdit(false, true);
      if (k === "add") bus && bus.send("quick", "add", {});
      if (k === "undo") undo();
      if (k === "redo") redo();
      return;
    }
    const act = tgt.closest("[data-item-act]");
    if (act) {
      const id = act.closest(".ql-item").dataset.item;
      const p = placedById(id);
      if (act.dataset.itemAct === "remove" && p) return removeItem(p);
      if (act.dataset.itemAct === "menu") { if (S.menu && S.menu.id === id) closeMenu("toggle"); else openMenu(id, {}); }
      return;
    }
    if (tgt.closest("[data-rz]")) return;
    const btn = tgt.closest("[data-run]");
    if (btn && !S.edit) { run(btn.dataset.run, false); return; }
    const item = tgt.closest(".ql-item");
    if (item && S.edit && item.dataset.type === "action") {
      const p = placedById(item.dataset.item);
      if (p) bus && bus.send("quick", "edit-button", { id: p.ref });
    }
  }

  function itemAt(e) {
    let node = e.target && e.target.isConnected && e.target.closest ? e.target.closest(".ql-item") : null;
    if (!node && S.down && performance.now() - S.down.at < 3000 && Math.abs(S.down.x - e.clientX) + Math.abs(S.down.y - e.clientY) < 12) node = itemNode(S.down.id);
    if (!node && e.clientX != null) { const under = document.elementFromPoint(e.clientX, e.clientY); node = under && under.closest ? under.closest(".ql-item") : null; }
    return node;
  }
  function onContextMenu(e) {
    e.preventDefault();
    if (e.target.closest && e.target.closest(".mcm")) return;
    const node = itemAt(e);
    if (!node) { closeMenu("outside"); return; }
    const field = e.target.isConnected && e.target.closest && e.target.closest("input, textarea");
    if (field && (field.value || "").length && field.selectionStart !== field.selectionEnd) return;
    clearPress();
    openMenu(node.dataset.item, { x: e.clientX, y: e.clientY });
  }
  function onKey(e) {
    const inField = e.target.matches && e.target.matches("input, textarea, select, [contenteditable]");
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Escape") {
      if (S.menu || document.querySelector(".mp-modal-back, .mcm")) return;
      if (S.drag) { e.preventDefault(); cancelDrag(); return; }
      if (inField) return;
      e.preventDefault();
      if (S.edit) { setEdit(false, true); return; }
      api("/api/quick/window", { action: "hide", restore_focus: true }).catch(() => {});
      return;
    }
    if (mod && !e.shiftKey && !e.altKey && e.code === "KeyE") { e.preventDefault(); setEdit(!S.edit, true); return; }
    if (mod && !e.shiftKey && !e.altKey && e.code === "KeyB") { e.preventDefault(); api("/api/quick/layer", { action: "hide" }).catch(() => {}); return; }
    if (inField) return;
    if (mod && !e.altKey && e.code === "KeyZ") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && !e.altKey && !e.shiftKey && e.code === "KeyY") { e.preventDefault(); redo(); return; }
    const item = e.target.closest && e.target.closest(".ql-item");
    if (!item) return;
    const p = placedById(item.dataset.item);
    if (!p) return;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[e.key] && e.target === item) {
      const [ax, ay] = arrows[e.key];
      if (mod) {
        e.preventDefault();
        if (p.type === "action") resizeTo(p, ax + ay > 0 ? 2 : 1, ax + ay > 0 ? 2 : 1);
        else resizeTo(p, p.units[0] + ax, p.units[1] + ay);
        focusItem(p.id);
        return;
      }
      if (S.edit || e.altKey) {
        e.preventDefault();
        const step = e.shiftKey ? 32 : 8;
        nudge(item.dataset.item, ax * step, ay * step);
        return;
      }
    }
    if ((e.key === "Enter" || e.key === " ") && e.target === item) {
      e.preventDefault();
      if (S.edit || p.type === "panel") openMenu(p.id, { keyboard: true }); else run(p.ref, true);
      return;
    }
    if ((e.key === "Delete" || (e.key === "Backspace" && S.edit)) && e.target === item) {
      e.preventDefault();
      removeItem(p);
      return;
    }
    if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") { e.preventDefault(); openMenu(item.dataset.item, { keyboard: true }); }
  }
  function cancelDrag() {
    const d = S.drag;
    if (!d) return;
    const p = placedById(d.id);
    d.moved = false;
    if (p) Object.assign(p, { x: d.origin.x, y: d.origin.y, w: d.origin.w, h: d.origin.h, units: d.origin.units.slice() });
    onPointerUp(null);
  }
  function bind() {
    const ql = $("ql");
    ql.addEventListener("click", onClick);
    ql.addEventListener("pointerdown", onPointerDown);
    ql.addEventListener("pointermove", onPointerMove);
    ql.addEventListener("pointerup", onPointerUp);
    ql.addEventListener("pointercancel", onPointerUp);
    ql.addEventListener("lostpointercapture", () => { if (S.drag && !S.press) setTimeout(() => { if (S.drag) onPointerUp({ type: "pointerup" }); }, 0); });
    ql.addEventListener("contextmenu", onContextMenu);
    ql.addEventListener("dragstart", (e) => { if (e.target.closest && e.target.closest(".ql-item")) e.preventDefault(); });
    ql.addEventListener("focusin", (e) => { const item = e.target.closest && e.target.closest(".ql-item"); if (item) S.focusId = item.dataset.item; });
    ql.addEventListener("transitionend", (e) => { if (e.target.classList && e.target.classList.contains("ql-item")) scheduleRegion(); });
    document.addEventListener("keydown", onKey);
    window.addEventListener("focus", () => { if (S.visible) api("/api/quick/layer", { action: "focused" }).catch(() => {}); });
    window.addEventListener("blur", () => { if (S.press) clearPress(); });

    new MutationObserver(() => {
      const modal = !!document.querySelector(".mp-modal-back");
      if (modal !== S.modal) { S.modal = modal; S.regionKey = ""; }
      scheduleRegion();
    }).observe(document.body, { childList: true, subtree: false });
    window.addEventListener("resize", () => {
      S.regionKey = "";
      clearTimeout(S.viewTimer);
      S.viewTimer = setTimeout(syncViewport, 220);
      scheduleRegion();
    });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { document.querySelectorAll(".ql-item[data-type='panel']").forEach(queueFit); });
    if (bus) {
      bus.on("edit", (d) => { if (S.visible) setEdit(!!(d && d.on), false); });
      bus.on("reload", () => { if (S.visible) reload(true); });
      bus.on("toast", (d) => { if (S.visible && d && d.text) toast(String(d.text)); });
    }
  }

  function syncViewport() {
    const nom = S.nominal;
    if (!S.visible || !nom || root.dataset.native !== "1" || innerWidth < 200 || innerHeight < 200) return;
    const w = Math.abs(innerWidth - nom.w) > 2 ? innerWidth : nom.w;
    const h = Math.abs(innerHeight - nom.h) > 2 ? innerHeight : nom.h;
    if (w === S.monitor.w && h === S.monitor.h) return;
    S.monitor = { ...nom, w, h };
    if (!S.drag) resolve();
  }
  async function reload(tell) {
    await loadData();
    await resolve();
    if (tell && bus) bus.send("quick", "changed", {});
  }

  const MemuLayer = {
    async onShow(payload) {
      payload = payload || {};
      const wasVisible = S.visible;
      S.visible = true;
      if (!wasVisible || payload.fresh) S.shownAt = performance.now();
      const mon = payload.monitor || S.monitor || { key: "default", w: innerWidth, h: innerHeight, scale: 1 };
      S.nominal = { ...mon };
      S.monitor = { ...mon };
      clearTimeout(S.viewTimer);
      S.viewTimer = setTimeout(syncViewport, 600);
      S.keepout = payload.keepout || {};
      S.glass = !!payload.glass;
      S.regionKey = "";
      try { S.lang = localStorage.getItem("memu_lang") === "en" ? "en" : "ru"; root.lang = S.lang; } catch (e) {  }
      if (!wasVisible) { S.edit = false; closeMenu("hidden"); S.undo = []; S.redo = []; }
      S.loading = (async () => {
        await loadData();
        if (!S.data) return;
        await resolve();
        if (S.placed.some((p) => p.type === "panel")) {
          const ok = await ensurePanels();
          if (ok && window.MemuPanels && MemuPanels.setPaused) MemuPanels.setPaused(false);
          renderPanels();
        }
        if ((S.data.runs || []).length) pollRuns(true);
      })().finally(() => { S.loading = null; });
      return S.loading;
    },
    onHide() {
      S.visible = false;
      S.edit = false;
      if (S.drag) { S.drag = null; drawGuides([], []); }
      clearPress();
      closeMenu("hidden");
      if (window.MemuCtxMenu) MemuCtxMenu.close("hidden");
      hideToast();
      clearTimeout(S.poll); S.poll = 0;
      S.undo = []; S.redo = [];
      if (window.MemuPanels && MemuPanels.setPaused) MemuPanels.setPaused(true);
      render();
    },
    setKeepout(keepout) {
      S.keepout = keepout || {};
      if (S.visible && !S.drag) resolve();
    },
    setEdit(on) { if (S.visible) setEdit(!!on, false); },
    reload: () => reload(false),
    state: () => S,
    openMenu: (id, where) => openMenu(id, where || {}),
    undo, redo, fitCard: (id) => { const n = itemNode(id); return n ? fitCard(n) : null; },
    nearestFree, snapXY, regionRects,
  };
  window.MemuLayer = MemuLayer;
  bind();
})();
