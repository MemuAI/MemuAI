/* Quick Ask plugin «canvas» (Round 27, r27-quick-canvas): MemuAI panels and «Быстрые действия» in the window.

   Contract: only window.MemuQuick (registerPanel in the "top" slot, addAction, on/emit, api, send, setInput…).
   Server: app/quick_canvas.py (/api/quick/canvas/*: layout, settings, buttons, runs) + app/quick_actions.py.

   * One grid for panels and buttons: COLS = 8 square columns (6 / 4 in a narrow window — a derived layout,
     first-fit without overlaps); a panel is ≥ 2×2 (S 2×2, M 4×2, L 4×4, W 8×2), a button 1×1 or 2×2.
   * Panels are rendered by pgstatic/panels.js itself, loaded lazily in embed mode (MEMU_PANELS_EMBED): the same
     cards, data, To-Do / notes / timers, refresh — /api/pg/panels is the one source for both windows.
   * «Изменить» (Ctrl+E, or a long press): items wiggle, drag snaps to the grid, the corner resizes, ✕ removes
     from the canvas (never deletes a MemuAI panel), ⚙ opens the button editor / the panel's options.
   * Unobtrusive: the window opens in the usual input mode unless the user chose otherwise; Ctrl+B (or the
     strip's toggle) folds the canvas; an answer folds it; the input keeps the focus; panels load after the
     window is on screen.
   * A button runs only on a click / Enter / its own hotkey — never on open (and not in the first 300 ms after
     the window appeared). Risky steps need the user's approval on the first run; the server enforces it.

   Round 28 — «screen mode» (S.screen, whenever the Windows app can show the screen layer: /api/quick/canvas →
   layer.available): the panels and buttons are NOT drawn in the window any more but anywhere on the screen by the
   layer (quick/layer.html, app/quick_layer.py). Here stay what needs the window: the strip, Ctrl+B (show/hide the
   layer), Ctrl+E (arrange it), «Add», the button editor, the approval sheet of a first run, confirmations, progress
   and results — a click on a layer button arrives through MemuQuickBus («run») and runs exactly like a click here.
   The in-window grid is kept as the fallback when the layer cannot be shown (not Windows, WebView2 failure). */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const root = document.documentElement;
  const reduced = () => root.dataset.motion === "off" || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COLS = 8, GAP = 8, PANEL_SIZES = { s: [2, 2], m: [4, 2], l: [4, 4], w: [8, 2] };
  const DENSITY = { compact: 58, normal: 72, large: 88 };
  const UI = {
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M7 7l10 10M17 7 7 17"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/></svg>',
    grid: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><circle cx="17" cy="17" r="3.6"/></svg>',
    chev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18 9 12l6-6"/></svg>',
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 16v5"/></svg>',
    resize: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M11 5 5 11M11 8.5 8.5 11"/></svg>',
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    dup: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
  };

  const S = {
    data: null, loading: null, open: false, edit: false, view: "grid",   // grid | add | editor | approve
    cols: COLS, cell: 64, rowH: 64, layout: [], display: [],
    panelsJs: null, panelsReady: false, unsub: null,
    runs: new Map(), polls: new Map(), handled: new Set(), notices: [],
    focusId: "", drag: null, press: null, editor: null, approve: null, catalog: null, shownAt: performance.now(),
    addTab: "buttons", inChat: false, saveTimer: 0, fit: new Set(), starting: new Set(),
    screen: false, layer: { available: false, visible: false, edit: false, shown: 0, hidden: 0 }, notifyTimer: 0,
  };
  const bus = () => window.MemuQuickBus || null;
  /** Round 28: the layer re-reads the buttons / layout after a change made in the window. */
  function notifyLayer() {
    if (!S.screen || !bus()) return;
    clearTimeout(S.notifyTimer);
    S.notifyTimer = setTimeout(() => bus().send("layer", "reload", {}), 120);
  }
  async function layerCommand(action, on) {
    try {
      const out = await Q.api("/api/quick/layer", { body: { action, on } });
      if (out && "visible" in out) S.layer = { ...S.layer, ...out };
      if (out && out.available === false) { S.screen = false; }
    } catch (e) { Q.toast(e.message, "error"); }
    render();
  }
  const L = () => Q.lang();
  const settings = () => (S.data && S.data.settings) || { enabled: false };
  const actionsById = () => new Map(((S.data && S.data.actions) || []).map((a) => [a.id, a]));
  const panelsList = () => (window.MemuPanels && MemuPanels.state ? MemuPanels.state.panels || [] : []);
  const panelById = (id) => panelsList().find((p) => p.id === id) || null;

  /* ------------------------------------------------------------ icons */
  let icons = window.MemuCanvasIcons || null;
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src; s.async = true; s.onload = resolve; s.onerror = () => reject(new Error("load " + src));
      document.head.appendChild(s);
    });
  }
  const iconsReady = icons ? Promise.resolve() : loadScript("/quick/plugins/canvas-icons.js?v=1.0-r27").then(() => { icons = window.MemuCanvasIcons; }).catch(() => {});
  // Round 29: one context menu for MemuAI panels, the screen layer and this grid (pgstatic/memu-ctxmenu.js)
  if (!window.MemuCtxMenu) loadScript("/memu-ctxmenu.js?v=1.1-r34").catch(() => {});  // Round 34: the menu buttons take --on-accent
  function faceHtml(action) {
    const ic = action.icon || { kind: "builtin", value: "bolt" };
    if (ic.kind === "emoji") return `<span class="qcv-emoji">${esc(ic.value)}</span>`;
    if ((ic.kind === "image" || ic.kind === "app") && /^data:image\//.test(ic.value || "")) return `<img alt="" src="${esc(ic.value)}" draggable="false">`;
    return icons ? icons.svg(ic.value) : "";
  }
  function faceStyle(action) {
    const c1 = /^#[0-9a-f]{6}$/i.test(action.color || "") ? action.color : "";
    const c2 = /^#[0-9a-f]{6}$/i.test(action.color2 || "") ? action.color2 : "";
    if (!c1) return "";
    return `--qcv-c1:${c1};--qcv-c2:${c2 || c1};`;
  }

  /* ------------------------------------------------------------ host */
  let el = null;
  let panelsButton = null;              // Round 30: the footer «Панели» button (set below, after the actions)
  const panel = Q.registerPanel("canvas", (box) => { el = box; box.classList.add("qcv-host"); return {}; }, { slot: "top", order: 5, hidden: true });

  /* ------------------------------------------------------------ data */
  async function load() {
    if (S.loading) return S.loading;
    S.loading = (async () => {
      try { S.data = await Q.api("/api/quick/canvas?lang=" + L()); }
      catch (e) { S.data = S.data || null; }
      finally { S.loading = null; }
      S.layout = ((S.data && S.data.layout && S.data.layout.items) || []).map((i) => ({ ...i }));
      // Round 28: the screen layer when the app can show it; the in-window grid otherwise (Round 27 fallback)
      const lay = (S.data && S.data.layer) || {};
      S.screen = !!lay.available;
      S.layer = { ...S.layer, available: !!lay.available, visible: !!lay.visible, edit: !!lay.edit };
      for (const run of (S.data && S.data.runs) || []) trackRun(run);
      return S.data;
    })();
    return S.loading;
  }
  function saveLayout() {
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(async () => {
      try {
        const out = await Q.api("/api/quick/canvas/layout", { body: { items: S.layout } });
        if (out && out.layout) { S.layout = out.layout.items.map((i) => ({ ...i })); if (S.data) S.data.layout = out.layout; }
        notifyLayer();
      } catch (e) { Q.toast(t("Не удалось сохранить раскладку: ", "Could not save the layout: ") + e.message, "error"); }
    }, 250);
  }
  const saveUi = (open) => Q.api("/api/quick/canvas/ui", { body: { open } }).catch(() => {});

  /* ------------------------------------------------------------ panels.js (lazy, embed mode) */
  function ensurePanels() {
    if (!settings().panels) return Promise.resolve(false);
    if (S.panelsJs) return S.panelsJs;
    S.panelsJs = (async () => {
      if (!document.querySelector('link[data-qcv-panels]')) {
        const link = document.createElement("link");
        link.rel = "stylesheet"; link.href = "/panels.css?v=1.4-r29"; link.dataset.qcvPanels = "1";
        document.head.appendChild(link);
      }
      if (!window.MemuPanels) {
        window.MEMU_PANELS_EMBED = true;
        try { await loadScript("/panels.js?v=1.7-r30"); } catch (e) { return false; }
      }
      if (!window.MemuPanelLook) {       // Round 30: the looks of the panels (card / compact / text / icon)
        if (!document.querySelector("link[data-pl-css]")) {
          const css = document.createElement("link");
          css.rel = "stylesheet"; css.href = "/quick/panel-look.css?v=1.1-r32"; css.dataset.plCss = "1";
          document.head.appendChild(css);
        }
        try { await loadScript("/quick/panel-look.js?v=1.1-r32"); } catch (e) { /* cards only */ }
      }
      if (!window.MemuPanels || typeof MemuPanels.subscribe !== "function") return false;
      S.unsub = MemuPanels.subscribe(() => { S.panelsReady = true; renderPanels(); renderStrip(); requestAnimationFrame(fitNewPanels); });
      if (MemuPanels.state && MemuPanels.state.loaded) S.panelsReady = true;
      return true;
    })();
    return S.panelsJs;
  }

  /* ------------------------------------------------------------ layout engine */
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  /** Visible items in reading order; items of panels that left MemuAI are skipped (kept until the user saves). */
  function visibleItems() {
    const acts = actionsById();
    const st = settings();
    return S.layout.filter((i) => {
      if (i.type === "action") return st.actions !== false && acts.has(i.ref);
      if (i.type === "panel") return st.panels !== false && (!S.panelsReady || !!panelById(i.ref));
      return false;
    });
  }
  /** cols = 8 shows the stored layout; fewer columns → every item keeps its order and size (clamped) and takes
   *  the first free place at or above its row — no overlaps, nothing outside the grid. */
  function derive(items, cols) {
    const sorted = items.slice().sort((a, b) => a.y - b.y || a.x - b.x);
    if (cols >= COLS) return sorted.map((i) => ({ ...i }));
    const out = [];
    for (const it of sorted) {
      const w = Math.min(it.w, cols), h = it.h;
      let best = null;
      for (let y = 0; y < 400 && !best; y++) {
        for (let x = 0; x + w <= cols; x++) {
          const probe = { x, y, w, h };
          if (!out.some((o) => overlaps(probe, o))) { best = probe; break; }
        }
      }
      out.push({ ...it, ...best });
    }
    return out;
  }
  /** Put `moving` at (x, y) and push everything it touches down (repeatedly), the rest stays where it is. */
  function placeAt(items, moving, x, y, w = moving.w, h = moving.h) {
    const others = items.filter((i) => i.id !== moving.id).map((i) => ({ ...i }));
    const placed = [{ ...moving, x, y, w, h }];
    for (const it of others.sort((a, b) => a.y - b.y || a.x - b.x)) {
      while (placed.some((p) => overlaps(it, p))) it.y += 1;
      placed.push(it);
    }
    return placed;
  }
  /** A narrow window shows sizes clamped to 6 / 4 columns: after a drag there, the OTHER items keep their stored
   *  width (a W panel stays 8 wide in the real layout) and are pushed down around the moved one. */
  function keepStoredSizes(items, movedId) {
    const stored = new Map(S.layout.map((i) => [i.id, i]));
    const moved = items.find((i) => i.id === movedId);
    const rest = items.filter((i) => i.id !== movedId).map((i) => {
      const w = Math.min(COLS, (stored.get(i.id) || i).w);
      return { ...i, w, x: Math.min(i.x, COLS - w) };
    });
    return placeAt([moved].concat(rest), moved, moved.x, moved.y, moved.w, moved.h);
  }
  function measure() {
    if (!el) return;
    const grid = el.querySelector(".qcv-grid");
    const width = (grid && grid.clientWidth) || (el.clientWidth - 24) || 600;
    S.cols = width >= 500 ? 8 : width >= 360 ? 6 : 4;
    S.cell = (width - GAP * (S.cols - 1)) / S.cols;
    S.rowH = Math.round(Math.min(S.cell, DENSITY[settings().density] || DENSITY.normal));
  }
  const px = (it) => ({ left: it.x * (S.cell + GAP), top: it.y * (S.rowH + GAP), width: it.w * S.cell + (it.w - 1) * GAP, height: it.h * S.rowH + (it.h - 1) * GAP });
  function panelRenderSize(it) {
    if (it.w <= 2) return "s";
    if (it.h >= 4) return "l";
    if (it.w >= S.cols && S.cols >= 6) return "w";
    return "m";
  }

  /* ------------------------------------------------------------ rendering */
  function shell() {
    el.innerHTML = `<div class="qcv" data-open="0" data-edit="0">
      <div class="qcv-strip" role="toolbar" aria-label="${esc(t("Закреплённые", "Pinned"))}"></div>
      <div class="qcv-notices" aria-live="polite"></div>
      <div class="qcv-body" hidden>
        <div class="qcv-head">
          <span class="qcv-title">${esc(t("Панели и действия", "Panels & actions"))}</span><span class="qcv-grow"></span>
          <button type="button" class="qcv-hbtn" data-qcv="add">${UI.plus}<span>${esc(t("Добавить", "Add"))}</span></button>
          <button type="button" class="qcv-hbtn" data-qcv="edit" aria-pressed="false"><span>${esc(t("Изменить", "Edit"))}</span></button>
          <button type="button" class="qcv-hbtn" data-qcv="arrange" aria-pressed="false" hidden><span>${esc(t("Расставить", "Arrange"))}</span></button>
          <button type="button" class="qcv-hbtn qcv-ico" data-qcv="close" title="${esc(t("Свернуть (Ctrl+B)", "Fold (Ctrl+B)"))}" aria-label="${esc(t("Свернуть", "Fold"))}">${UI.up}</button>
        </div>
        <div class="qcv-view"></div>
      </div></div>`;
  }
  function render() {
    if (!el) return;
    const st = settings();
    const on = !!(S.data && st.enabled && (st.panels || st.actions));
    if (!on) { Q.hidePanel("canvas"); root.removeAttribute("data-qcv-open"); if (panelsButton) panelsButton.update(); return; }
    if (!el.querySelector(".qcv")) shell();
    const box = el.querySelector(".qcv");
    box.dataset.open = S.open ? "1" : "0";
    box.dataset.edit = S.edit ? "1" : "0";
    box.dataset.labels = st.labels === false ? "0" : "1";
    box.dataset.view = S.view;
    box.dataset.screen = S.screen ? "1" : "0";
    const hidden = S.inChat && st.collapse_on_answer !== false && !S.open && !S.notices.length;
    if (hidden) { Q.hidePanel("canvas"); root.removeAttribute("data-qcv-open"); return; }
    Q.showPanel("canvas");
    if (S.open) root.setAttribute("data-qcv-open", ""); else root.removeAttribute("data-qcv-open");
    el.querySelector(".qcv-body").hidden = !S.open;
    const edit = el.querySelector('[data-qcv="edit"]');
    edit.setAttribute("aria-pressed", String(S.edit));
    edit.querySelector("span").textContent = S.edit ? t("Готово", "Done") : t("Изменить", "Edit");
    edit.hidden = S.screen || S.view !== "grid" || !visibleItems().length;
    el.querySelector('[data-qcv="add"]').hidden = S.view !== "grid";
    const arrange = el.querySelector('[data-qcv="arrange"]');
    arrange.hidden = !S.screen || S.view === "grid" || !visibleItems().length;   // the screen summary has its own
    arrange.setAttribute("aria-pressed", String(!!S.layer.edit));
    arrange.querySelector("span").textContent = S.layer.edit ? t("Готово", "Done") : t("Расставить", "Arrange");
    renderStrip();
    renderNotices();
    if (S.open) renderView();
    Q.resize();
    if (window.MemuQuickEmbed) MemuQuickEmbed.sync();       // Round 30: panels built into the window
  }
  function renderView() {
    const view = el.querySelector(".qcv-view");
    if (S.view === "add") return renderAdd(view);
    if (S.view === "editor") return renderEditor(view);
    if (S.view === "approve") return renderApprove(view);
    if (S.screen) return renderScreen(view);
    renderGrid(view);
  }
  /** Round 28: in screen mode the window shows what is on the screen and how to arrange it — not the items. */
  function renderScreen(view) {
    const items = visibleItems();
    if (!items.length) { renderGrid(view); return; }      // the empty state (create a button / add a panel)
    const shown = S.layer.visible ? (S.layer.shown != null ? S.layer.shown : items.length) : 0;
    const hidden = S.layer.visible ? +S.layer.hidden || 0 : 0;
    view.innerHTML = `<div class="qcv-screen">
      <div class="qcv-screen-art" aria-hidden="true"><i></i><b></b><b></b><span></span></div>
      <div class="qcv-screen-text"><b>${esc(t("Панели и кнопки — на экране", "Panels and buttons are on the screen"))}</b>
        <span>${esc(t("Стоят в любом месте экрана вокруг этого окна и прячутся вместе с ним. Расставить — Ctrl+E или долгое нажатие на элемент; скрыть или показать — Ctrl+B.",
          "They sit anywhere on the screen around this window and hide with it. Arrange — Ctrl+E or a long press on an item; hide or show — Ctrl+B."))}</span>
        <small>${esc(S.layer.visible ? t(`На экране: ${shown}`, `On the screen: ${shown}`) + (hidden ? t(` · не поместилось на этом мониторе: ${hidden}`, ` · did not fit on this monitor: ${hidden}`) : "")
          : t("Сейчас скрыты", "Hidden now"))}</small></div>
      <div class="qcv-screen-act"><button type="button" class="qcv-btn-p" data-qcv="layer-toggle">${esc(S.layer.visible ? t("Скрыть с экрана", "Hide from the screen") : t("Показать на экране", "Show on the screen"))}</button>
        <button type="button" class="qcv-btn-g" data-qcv="arrange">${esc(S.layer.edit ? t("Закончить расстановку", "Finish arranging") : t("Расставить", "Arrange"))}</button></div></div>`;
  }

  /* ---------- strip: pinned buttons and panel chips + the toggle */
  function renderStrip() {
    const strip = el && el.querySelector(".qcv-strip");
    if (!strip) return;
    const st = settings();
    const acts = actionsById();
    const pinned = st.strip === false ? [] : visibleItems().filter((i) => i.pinned);
    const parts = pinned.map((it) => {
      if (it.type === "action") {
        const a = acts.get(it.ref);
        return `<button type="button" class="qcv-sbtn" data-run="${esc(a.id)}" data-shape="${esc(a.shape)}" style="${faceStyle(a)}" title="${esc(tipOf(a))}" aria-label="${esc(a.label || tipOf(a))}">
          <span class="qcv-sface">${faceHtml(a)}</span>${a.label ? `<span class="qcv-slabel">${esc(a.label)}</span>` : ""}${runBadge(a.id)}</button>`;
      }
      const p = panelById(it.ref);
      if (!p || !window.MemuPanels) return "";
      return MemuPanels.chipHtml(p).replace(' draggable="true"', "").replace('class="mp-chip', 'data-qcv-chip="' + esc(it.id) + '" class="qcv-chip mp-chip');
    }).join("");
    // Round 30 (r30-quick-core-2): the strip is ONLY the pinned items — the «Панели Ctrl B» toggle that hung over every
    // view of the window lives in the footer now (panelsButton below); nothing pinned → no strip row at all.
    const html = `<div class="qcv-strip-items">${parts}</div>${S.screen && parts ? `<span class="qcv-grow"></span><button type="button" class="qcv-toggle qcv-more" data-qcv="manage" aria-expanded="${S.open}" title="${esc(t("Добавить и настроить", "Add and set up"))}" aria-label="${esc(t("Добавить и настроить", "Add and set up"))}">${UI.gear}</button>` : ""}`;
    if (strip.innerHTML !== html) strip.innerHTML = html;
    strip.dataset.empty = parts ? "0" : "1";
    strip.hidden = !parts;
    if (panelsButton) panelsButton.update();
  }
  const tipOf = (a) => [a.label, a.tooltip, a.hotkey].filter(Boolean).join(" · ") || t("Кнопка", "Button");
  function runBadge(id) {
    const run = S.runs.get(id);
    if (!run) return "";
    const pct = run.total ? Math.round(((run.status === "done" ? run.total : Math.max(0, run.step)) / run.total) * 100) : 0;
    return `<i class="qcv-ring" data-status="${esc(run.status)}" style="--qcv-p:${pct}" aria-hidden="true"></i>`;
  }

  /* ---------- notices: confirmations, results, errors of runs */
  function renderNotices() {
    const box = el && el.querySelector(".qcv-notices");
    if (!box) return;
    const rows = S.notices.map((n) => {
      if (n.kind === "undo") {
        return `<div class="qcv-note" data-kind="undo" data-note="${esc(n.id)}"><div class="qcv-note-body"><span>${esc(n.text)}</span></div>
          <div class="qcv-note-act"><button type="button" class="qcv-btn-g" data-note-act="undo-local">${esc(t("Отменить", "Undo"))}</button></div></div>`;
      }
      const run = n.run || {};
      const steps = (run.steps || []).length > 1 ? `<ol class="qcv-steps">${run.steps.map((s) => `<li data-status="${esc(s.status)}"><span>${esc(s.title)}</span>${s.message && s.status !== "done" ? `<small>${esc(s.message)}</small>` : ""}</li>`).join("")}</ol>` : "";
      if (n.kind === "confirm") {
        const c = run.confirm || {};
        const left = Math.max(0, Math.ceil(((n.readyAt || 0) - Date.now()) / 1000));
        return `<div class="qcv-note" data-kind="confirm" data-tier="${esc(c.tier || "confirm")}" data-note="${esc(n.id)}" role="alertdialog" aria-label="${esc(t("Подтверждение", "Confirmation"))}">
          <span class="qcv-note-ic">${UI.warn}</span><div class="qcv-note-body"><b>${esc(run.label || t("Кнопка", "Button"))}</b><span>${esc(c.message || "")}</span>${steps}</div>
          <div class="qcv-note-act"><button type="button" class="qcv-btn-p" data-note-act="accept"${left ? " disabled" : ""}>${esc(t("Подтвердить", "Confirm"))}${left ? ` (${left})` : ""}</button>
          <button type="button" class="qcv-btn-g" data-note-act="reject">${esc(t("Отмена", "Cancel"))}</button></div></div>`;
      }
      if (n.kind === "running") {
        return `<div class="qcv-note" data-kind="running" data-note="${esc(n.id)}"><span class="qcv-spin" aria-hidden="true"></span><div class="qcv-note-body"><b>${esc(run.label || "")}</b>
          <span>${esc((run.steps && run.steps[Math.max(0, run.step)] || {}).title || t("Выполняю…", "Working…"))}${run.total > 1 ? ` · ${Math.max(1, run.step + 1)}/${run.total}` : ""}</span></div>
          <div class="qcv-note-act"><button type="button" class="qcv-btn-g" data-note-act="cancel">${esc(t("Остановить", "Stop"))}</button></div></div>`;
      }
      const undo = (run.cards || []).map((c) => c.undo).filter(Boolean).pop();
      const ok = n.kind === "done";
      return `<div class="qcv-note" data-kind="${ok ? "done" : "error"}" data-note="${esc(n.id)}"><span class="qcv-note-ic">${ok ? (icons ? icons.svg("check", 2.4) : "✓") : UI.warn}</span>
        <div class="qcv-note-body"><b>${esc(run.label || "")}${ok ? "" : " — " + esc(run.status === "cancelled" ? t("остановлено", "stopped") : t("ошибка", "error"))}</b>
        ${n.output ? `<pre class="qcv-out">${esc(n.output)}</pre>` : `<span>${esc(ok ? lastMessage(run) : (run.error || lastMessage(run)))}</span>`}${ok && !(run.steps || []).some((x) => x.status === "error") ? "" : steps}</div>
        <div class="qcv-note-act">${n.output ? `<button type="button" class="qcv-btn-p" data-note-act="copy">${esc(t("Копировать", "Copy"))}</button><button type="button" class="qcv-btn-g" data-note-act="insert">${esc(t("Вставить", "Paste"))}</button>` : ""}
        ${undo ? `<button type="button" class="qcv-btn-g" data-note-act="undo" data-undo="${esc(undo)}">${esc(t("Вернуть", "Undo"))}</button>` : ""}
        <button type="button" class="qcv-btn-g qcv-ico" data-note-act="dismiss" aria-label="${esc(t("Скрыть", "Dismiss"))}">${UI.x}</button></div></div>`;
    }).join("");
    if (box.innerHTML !== rows) box.innerHTML = rows;
  }
  const lastMessage = (run) => { const done = (run.steps || []).filter((s) => s.message); return done.length ? done[done.length - 1].message : t("Готово", "Done"); };

  /* ---------- grid */
  function renderGrid(view) {
    measure();
    const items = visibleItems();
    if (!items.length) {
      view.innerHTML = `<div class="qcv-empty">
        <div class="qcv-empty-art" aria-hidden="true"><i></i><i></i><i></i><b></b></div>
        <b>${esc(t("Соберите своё пространство", "Make this space yours"))}</b>
        <span>${esc(t("Панели MemuAI — погода, задачи, курсы — и свои круглые кнопки: открыть программу, громкость, запрос ИИ, целый сценарий.",
          "MemuAI panels — weather, to-dos, rates — and your own round buttons: open an app, set the volume, ask AI, a whole routine."))}</span>
        <div class="qcv-empty-act">${settings().actions !== false ? `<button type="button" class="qcv-btn-p" data-qcv="new-button">${UI.plus}${esc(t("Кнопка", "Button"))}</button>` : ""}
        ${settings().panels !== false ? `<button type="button" class="qcv-btn-g" data-qcv="add-panels">${esc(t("Панель MemuAI", "MemuAI panel"))}</button>` : ""}
        ${settings().actions !== false ? `<button type="button" class="qcv-btn-g" data-qcv="templates">${esc(t("Шаблоны", "Templates"))}</button>` : ""}</div></div>`;
      return;
    }
    S.display = derive(items, S.cols);
    const rows = S.display.reduce((m, i) => Math.max(m, i.y + i.h), 0);
    let grid = view.querySelector(".qcv-grid");
    if (!grid) {
      view.innerHTML = `<div class="qcv-scroll"><div class="qcv-grid" role="group" aria-label="${esc(t("Панели и действия", "Panels & actions"))}"></div></div>`;
      grid = view.querySelector(".qcv-grid");
      grid.addEventListener("scroll", (e) => { if (e.target.classList && e.target.classList.contains("mp-card")) markMore(e.target); }, true);
      measure();
      S.display = derive(items, S.cols);
    }
    grid.style.height = (rows * (S.rowH + GAP) - GAP) + "px";
    grid.style.setProperty("--qcv-row", S.rowH + "px");
    const acts = actionsById();
    const keep = new Set();
    let n = 0;
    if (!S.focusId || !S.display.some((i) => i.id === S.focusId)) S.focusId = S.display[0] ? S.display[0].id : "";
    for (const it of S.display) {
      keep.add(it.id);
      let node = grid.querySelector(`[data-item="${CSS.escape(it.id)}"]`);
      if (!node) {
        node = document.createElement("div");
        node.className = "qcv-item";
        node.dataset.item = it.id;
        grid.appendChild(node);
      }
      node.dataset.type = it.type;
      node.setAttribute("role", it.type === "action" ? "button" : "group");
      node.dataset.w = it.w; node.dataset.h = it.h;
      node.tabIndex = it.id === S.focusId ? 0 : -1;
      if (!S.drag || S.drag.id !== it.id) {
        const r = px(it);
        node.style.cssText = `left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;--qcv-i:${n}`;
      }
      if (it.type === "action") {
        n += 1;
        const a = acts.get(it.ref);
        const html = buttonHtml(a, it, n);
        if (node.dataset.html !== html) { node.dataset.html = html; node.innerHTML = html; }   // 30 buttons: no DOM churn on every render
        node.setAttribute("aria-label", tipOf(a) + (n <= 9 ? ` (${n})` : ""));
      } else {
        if (window.MemuPanelLook) MemuPanelLook.apply(node, lookOf(it), { place: "window" });
        const html = panelCellHtml(it);
        if (node.dataset.html !== html && !(document.activeElement && node.contains(document.activeElement) && document.activeElement.matches("input, textarea"))) {
          node.dataset.html = html;
          node.innerHTML = html + editChrome(it);
        }
        const p = panelById(it.ref);
        node.setAttribute("aria-label", p ? p.title : t("Панель", "Panel"));
      }
      node.dataset.pinned = it.pinned ? "1" : "0";
    }
    grid.querySelectorAll(".qcv-item").forEach((node) => { if (!keep.has(node.dataset.item)) node.remove(); });
    requestAnimationFrame(() => grid.querySelectorAll('.qcv-item[data-type="panel"] > .mp-card').forEach(markMore));
  }
  /** Round 34: a panel taller than its cell scrolled with no hint (the scrollbar is hidden) — the last row looked cut
   *  off («Макс / мин 9° / 3°» half visible). A soft fade at the bottom says «there is more», gone at the end. */
  function markMore(card) {
    const more = card.scrollHeight - card.scrollTop - card.clientHeight > 4;
    if ((card.dataset.more === "1") !== more) card.dataset.more = more ? "1" : "";
  }
  function buttonHtml(a, it, n) {
    return `<button type="button" class="qcv-act" data-run="${esc(a.id)}" data-shape="${esc(a.shape)}" data-big="${it.w === 2 ? 1 : 0}" style="${faceStyle(a)}" title="${esc(tipOf(a))}" tabindex="-1">
      <span class="qcv-face">${faceHtml(a)}${runBadge(a.id)}</span>${a.label ? `<span class="qcv-label">${esc(a.label)}</span>` : ""}
      ${n <= 9 ? `<kbd class="qcv-num" aria-hidden="true">${n}</kbd>` : ""}</button>${editChrome(it)}`;
  }
  function panelCellHtml(it) {
    const p = panelById(it.ref);
    if (!p || !window.MemuPanels) return `<div class="qcv-skel" aria-hidden="true"><i></i><i></i><i></i></div>`;
    const face = window.MemuPanelLook ? MemuPanelLook.faceHtml(p, lookOf(it), { place: "window" }) : null;
    return face || MemuPanels.cardHtml(p, "home", { size: panelRenderSize(it), static: true, place: "quick" });
  }
  const lookOf = (it) => (window.MemuPanelLook ? MemuPanelLook.resolve(S.data, it.ref) : { style: "card" });
  function editChrome(it) {
    // mouse chrome; the keyboard has Delete / Shift+F10 on the focused item instead
    return `<span class="qcv-chrome" aria-hidden="true">
      <button type="button" class="qcv-cx" data-item-act="remove" tabindex="-1" title="${esc(t("Убрать с холста", "Remove from the canvas"))}" aria-label="${esc(t("Убрать", "Remove"))}">${UI.x}</button>
      <button type="button" class="qcv-cg" data-item-act="settings" tabindex="-1" title="${esc(t("Настроить", "Customize"))}" aria-label="${esc(t("Настроить", "Customize"))}">${UI.gear}</button>
      <span class="qcv-rz" data-item-act="resize" title="${esc(t("Потяните, чтобы изменить размер", "Drag to resize"))}">${UI.resize}</span></span>`;
  }
  /** A panel just added grows to the rows its content needs (up to 6) — once; after that the size is the user's. */
  function fitNewPanels() {
    if (!S.fit.size || !el) return;
    let changed = false;
    for (const id of Array.from(S.fit)) {
      const card = el.querySelector(`.qcv-item[data-item="${CSS.escape(id)}"] > .mp-card`);
      const it = S.layout.find((i) => i.id === id);
      if (!it) { S.fit.delete(id); continue; }
      if (!card) continue;
      S.fit.delete(id);
      const need = Math.min(6, Math.max(it.h, Math.ceil((card.scrollHeight + GAP) / (S.rowH + GAP))));
      if (need > it.h) { S.layout = placeAt(S.layout, it, it.x, it.y, it.w, need); changed = true; }
    }
    if (changed) { saveLayout(); render(); }
  }
  function renderPanels() {
    if (!S.open || S.view !== "grid" || !el) return;
    if (S.screen && visibleItems().length) return;        // Round 28: the items are on the screen, not here
    renderGrid(el.querySelector(".qcv-view"));
    Q.resize();
  }

  /* ---------- the item menu (⚙ or right click) */
  let menuEl = null, menuHandle = null;
  function closeItemMenu() {
    if (menuHandle) { const h = menuHandle; menuHandle = null; h.close("api"); }
    if (menuEl) { menuEl.remove(); menuEl = null; }
  }
  /** Round 29: the shared menu (closes on a press anywhere, Esc, blur, scroll; never leaves the window). */
  function openCtxItemMenu(it, anchor, point, keyboard) {
    const items = [];
    if (it.type === "action") {
      const a = actionsById().get(it.ref) || {};
      items.push({ type: "head", label: a.label || t("Кнопка", "Button"), sub: t("Быстрое действие", "Quick action"), icon: "bolt" },
        { id: "run", label: t("Запустить", "Run"), icon: "play" }, { id: "edit", label: t("Изменить кнопку…", "Edit the button…"), icon: "edit" },
        { id: "duplicate", label: t("Дублировать", "Duplicate"), icon: "copy" },
        { type: "seg", id: "bsize", label: t("Размер", "Size"), icon: "size", options: [{ id: "1", label: t("Обычная", "Normal"), pressed: it.w !== 2 }, { id: "2", label: t("Большая", "Large"), pressed: it.w === 2 }] });
    } else {
      const key = Object.entries(PANEL_SIZES).find(([, v]) => it.w === v[0] && it.h === v[1]);
      items.push({ type: "seg", id: "size", label: t("Размер", "Size"), icon: "size", options: Object.keys(PANEL_SIZES).map((k) => ({ id: k, label: k.toUpperCase(), pressed: !!key && key[0] === k })) },
        { type: "sep" }, { id: "refresh", label: t("Обновить сейчас", "Refresh now"), icon: "refresh" },
        { id: "options", label: t("Параметры панели…", "Panel options…"), icon: "sliders" }, { id: "memuai", label: t("Открыть в MemuAI", "Open in MemuAI"), icon: "open" });
      if (window.MemuPanelLook) {       // Round 30: this panel's look, its value, built into the window
        const embedded = ((S.data && S.data.embed && S.data.embed.items) || []).some((e) => e.ref === it.ref && !e.hidden);
        items.splice(1, 0, MemuPanelLook.menuSeg(lookOf(it)));
        if (S.data && S.data.looks && S.data.looks[it.ref]) items.splice(2, 0, { id: "look-reset", label: t("Вид как у всех панелей", "Look like all panels"), icon: "reset" });
        items.push({ id: "copy-value", label: t("Копировать значение", "Copy the value"), icon: "copy" },
          { id: "embed", label: embedded ? t("Убрать из окна", "Remove from the window") : t("Встроить в окно (над/под полем)", "Build into the window (above/below the input)"), icon: "sidebar" });
      }
    }
    items.push({ type: "sep" }, { id: "pin", label: it.pinned ? t("Открепить от строки", "Unpin from the strip") : t("Закрепить в строке под полем", "Pin to the strip under the input"), icon: "pin" },
      { id: "edit-mode", label: S.edit ? t("Закончить расстановку", "Finish arranging") : t("Расставить", "Arrange"), icon: "move" },
      { type: "sep" }, { id: "remove", label: it.type === "action" ? t("Удалить кнопку", "Delete the button") : t("Убрать из быстрого доступа", "Remove from Quick Ask"), icon: "trash", danger: true });
    const opts = {
      items, keyboard: !!keyboard, owner: anchor, returnFocus: el.querySelector(`.qcv-item[data-item="${CSS.escape(it.id)}"]`) || undefined,
      label: t("Действия", "Actions"),
      onPick: (id, item, opt) => {
        if (item.id === "bsize") { if (it.w !== (opt === "2" ? 2 : 1)) itemCommand(it.id, "size"); return; }
        if (item.id === "pl-style") { itemCommand(it.id, "pl-style:" + opt); return; }
        if (item.type === "seg") { itemCommand(it.id, "size:" + opt); return; }
        if (id === "edit-mode") { setEdit(!S.edit); return; }
        itemCommand(it.id, id);
      },
      onClose: () => { menuHandle = null; },
    };
    if (point) { opts.x = point.x; opts.y = point.y; } else opts.anchor = anchor;
    menuHandle = window.MemuCtxMenu.open(opts);
    Q.resize();
  }
  function openItemMenu(itemId, anchor, point, keyboard) {
    closeItemMenu();
    const it = S.layout.find((i) => i.id === itemId);
    if (!it) return;
    if (window.MemuCtxMenu) { openCtxItemMenu(it, anchor, point, keyboard); return; }
    const rows = [];
    if (it.type === "action") {
      rows.push(["edit", t("Изменить кнопку", "Edit the button")], ["run", t("Запустить", "Run")], ["duplicate", t("Дублировать", "Duplicate")],
        ["size", it.w === 2 ? t("Обычный размер", "Normal size") : t("Большая кнопка", "Large button")]);
    } else {
      for (const [k, v] of Object.entries(PANEL_SIZES)) rows.push(["size:" + k, ({ s: t("Малая · S", "Small · S"), m: t("Средняя · M", "Medium · M"), l: t("Большая · L", "Large · L"), w: t("Широкая · W", "Wide · W") })[k], it.w === v[0] && it.h === v[1]]);
      rows.push(["refresh", t("Обновить сейчас", "Refresh now")], ["options", t("Параметры панели…", "Panel options…")], ["memuai", t("Открыть в MemuAI", "Open in MemuAI")]);
    }
    rows.push(["pin", it.pinned ? t("Открепить от строки", "Unpin from the strip") : t("Закрепить в строке под полем", "Pin to the strip under the input")],
      ["remove", it.type === "action" ? t("Удалить кнопку", "Delete the button") : t("Убрать из быстрого доступа", "Remove from Quick Ask")]);
    menuEl = document.createElement("div");
    menuEl.className = "qcv-menu";
    menuEl.setAttribute("role", "menu");
    menuEl.innerHTML = rows.map(([k, label, on]) => `<button type="button" role="menuitem" data-menu="${esc(k)}"${on ? ' aria-checked="true"' : ""}${k === "remove" ? ' class="danger"' : ""}>${esc(label)}${on ? " ✓" : ""}</button>`).join("");
    el.querySelector(".qcv").appendChild(menuEl);
    const host = el.getBoundingClientRect(), r = anchor.getBoundingClientRect();
    const left = Math.min(Math.max(4, r.left - host.left), host.width - 228);
    menuEl.style.left = left + "px";
    menuEl.style.top = (r.bottom - host.top + 4) + "px";
    menuEl.onclick = (e) => {
      const b = e.target.closest("[data-menu]");
      if (!b) return;
      closeItemMenu();
      itemCommand(itemId, b.dataset.menu);
    };
    menuEl.querySelector("button").focus();
    menuEl.onkeydown = (e) => {
      const list = Array.from(menuEl.querySelectorAll("button"));
      const i = list.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeItemMenu(); focusItem(itemId); }
    };
    Q.resize();
  }
  async function itemCommand(itemId, cmd) {
    const it = S.layout.find((i) => i.id === itemId);
    if (!it) return;
    if (cmd === "pin") { it.pinned = !it.pinned; if (!it.pinned) delete it.pinned; saveLayout(); render(); return; }
    if (cmd === "remove") return removeItem(itemId);
    if (it.type === "action") {
      const a = actionsById().get(it.ref);
      if (cmd === "edit") return openEditor(a);
      if (cmd === "run") return runAction(a.id, true);
      if (cmd === "duplicate") return duplicate(a.id);
      if (cmd === "size") { resizeItem(it, it.w === 2 ? 1 : 2, it.w === 2 ? 1 : 2); return; }
    } else {
      if (cmd.startsWith("size:")) { const [w, h] = PANEL_SIZES[cmd.slice(5)]; resizeItem(it, Math.min(w, COLS), h); return; }
      if (cmd === "refresh" && window.MemuPanels) { MemuPanels.refreshNow(it.ref); return; }
      if (cmd.startsWith("pl-style:") || cmd === "look-reset" || cmd === "embed") return lookCommand(it, cmd);
      if (cmd === "copy-value" && window.MemuPanelLook) {
        try { await navigator.clipboard.writeText(MemuPanelLook.valueText(panelById(it.ref), lookOf(it))); Q.toast(t("Скопировано", "Copied"), "ok"); }
        catch (e) { Q.toast(t("Не удалось скопировать", "Could not copy"), "error"); }
        return;
      }
      if (cmd === "options" && window.MemuPanels) { MemuPanels.openEditor(it.ref); return; }
      if (cmd === "memuai") { Q.api("/api/quick/window", { body: { action: "handoff", handoff: { open: "panels", settings: "panels" } } }).catch(() => {}); return; }
    }
  }
  /** Round 30: a panel's look / built into the window — POST /api/quick/canvas/look, then the layer re-reads. */
  async function lookCommand(it, cmd) {
    const body = {};
    if (cmd === "embed") {
      const items = ((S.data.embed || {}).items || []).map((e) => ({ ...e }));
      const row = items.find((e) => e.ref === it.ref);
      if (row) row.hidden = !row.hidden; else items.push({ ref: it.ref, hidden: false });
      const place = (S.data.embed || {}).place;
      body.embed = { items, place: place && place !== "off" ? place : "below" };
    } else {
      body.panel = it.ref;
      if (cmd === "look-reset") body.reset = true; else body.look = { style: cmd.slice(9) };
    }
    try {
      const out = await Q.api("/api/quick/canvas/look", { body });
      Object.assign(S.data, { look: out.look, looks: out.looks, embed: out.embed });
      render();
      if (S.open && S.view === "grid" && !S.screen) renderGrid(el.querySelector(".qcv-view"));
      notifyLayer();
    } catch (e) { Q.toast(e.message, "error"); }
  }
  function resizeItem(it, w, h) {
    S.layout = placeAt(S.layout, it, Math.min(it.x, COLS - w), it.y, w, h);
    saveLayout(); render();
  }
  async function removeItem(itemId) {
    const it = S.layout.find((i) => i.id === itemId);
    if (!it) return;
    if (it.type === "action") {
      try {
        const out = await Q.api("/api/quick/canvas/actions/" + encodeURIComponent(it.ref) + "?lang=" + L(), { method: "DELETE" });
        await load(); render(); notifyLayer();
        undoToast(t("Кнопка удалена", "Button deleted"), async () => {
          await Q.api("/api/quick/canvas/actions/restore", { body: { removed: out.removed, lang: L() } });
          await load(); render(); notifyLayer();
        });
      } catch (e) { Q.toast(e.message, "error"); }
      return;
    }
    const before = S.layout.map((i) => ({ ...i }));
    S.layout = S.layout.filter((i) => i.id !== itemId);
    saveLayout(); render();
    undoToast(t("Панель убрана из быстрого доступа (в MemuAI она осталась)", "Panel removed from Quick Ask (it stays in MemuAI)"), () => { S.layout = before; saveLayout(); render(); });
  }
  function undoToast(text, undo) {
    S.notices = S.notices.filter((n) => n.kind !== "undo");
    const n = { id: "u" + Date.now(), kind: "undo", text, undo };
    S.notices.push(n);
    render();
    setTimeout(() => { if (S.notices.includes(n)) { S.notices = S.notices.filter((x) => x !== n); render(); } }, 7000);
  }
  async function duplicate(id) {
    try { await Q.api("/api/quick/canvas/actions/" + encodeURIComponent(id) + "/duplicate", { body: { lang: L() } }); await load(); render(); notifyLayer(); }
    catch (e) { Q.toast(e.message, "error"); }
  }

  /* ------------------------------------------------------------ open / fold */
  function setOpen(open, { save = true, focus = false } = {}) {
    if (!S.data || !settings().enabled) return;
    S.open = !!open;
    if (!S.open) { S.edit = false; S.view = "grid"; closeItemMenu(); }
    if (S.open) ensurePanels().then(() => renderPanels());
    if (save) saveUi(S.open);
    render();
    if (focus && S.open) focusItem(S.focusId);
  }
  function setEdit(on) {
    S.edit = !!on;
    if (S.edit && !S.open) setOpen(true);
    closeItemMenu();
    render();
  }
  // Round 28: Ctrl+B / the strip toggle show or hide the panels and buttons on the SCREEN (the window body only
  // opens for «Add», the editor and the approval sheet); without the layer — the Round 27 in-window canvas
  const toggle = () => (S.screen && visibleItems().length ? layerCommand("toggle") : setOpen(!S.open, { focus: false }));
  const arrange = () => layerCommand("edit", !S.layer.edit);

  /* ------------------------------------------------------------ runs */
  function pageContext() {
    const sel = Q.selection ? Q.selection() : { text: "" };
    const st = Q.state();
    return { selection: (sel && sel.text) || "", input: Q.getInput(), page_url: (st.page && st.page.url) || "", page_title: (st.page && st.page.title) || "" };
  }
  async function runAction(id, explicit = false, approve = false) {
    if (!explicit && performance.now() - S.shownAt < 300) return;   // a click that only finished opening the window
    const a = actionsById().get(id);
    if (!a) return;
    const active = S.runs.get(id);
    if ((active && ["running", "confirm"].includes(active.status)) || S.starting.has(id)) { Q.toast(t("Уже выполняется", "Already running")); return; }
    let out = null;
    S.starting.add(id);                     // a second press before the server answered the first one
    try { out = await Q.api("/api/quick/canvas/run", { body: { id, lang: L(), context: pageContext(), approve } }); }
    catch (e) { Q.toast(e.message, "error"); return; }
    finally { S.starting.delete(id); }
    if (out.approval) { S.approve = { id, risks: out.approval.risks || [], label: a.label }; S.view = "approve"; setOpen(true, { save: false }); return; }
    if (out.run) { if (S.data) a.approved = true; trackRun(out.run); }
  }
  function trackRun(run) {
    S.runs.set(run.action, run);
    if (S.polls.has(run.id)) return;
    const tick = async () => {
      let fresh = null;
      try { fresh = (await Q.api("/api/quick/canvas/runs/" + encodeURIComponent(run.id))).run; } catch (e) { fresh = null; }
      if (!fresh) { S.polls.delete(run.id); S.runs.delete(run.action); render(); return; }
      onRun(fresh);
      if (["running", "confirm"].includes(fresh.status)) S.polls.set(run.id, setTimeout(tick, document.hidden ? 1500 : fresh.status === "confirm" ? 500 : 280));
      else S.polls.delete(run.id);
    };
    onRun(run);
    S.polls.set(run.id, setTimeout(tick, 120));
  }
  function upsertNotice(n) {
    const i = S.notices.findIndex((x) => x.id === n.id);
    if (i >= 0) S.notices[i] = { ...S.notices[i], ...n }; else S.notices.unshift(n);
    S.notices = S.notices.slice(0, 4);
  }
  function dropNotice(id) { S.notices = S.notices.filter((n) => n.id !== id); }
  function onRun(run) {
    S.runs.set(run.action, run);
    for (const c of run.client || []) handleClient(run, c);
    const id = "n" + run.id;
    const prev = S.notices.find((n) => n.id === id);
    if (run.status === "confirm") {
      const countdown = (run.confirm && run.confirm.tier === "dangerous") ? Math.max(0, +run.confirm.countdown || 0) : 0;
      const readyAt = prev && prev.kind === "confirm" ? prev.readyAt : Date.now() + countdown * 1000;
      upsertNotice({ id, kind: "confirm", run, readyAt });
      if (countdown && !S.countdownTimer) S.countdownTimer = setInterval(() => { renderNotices(); if (!S.notices.some((n) => n.kind === "confirm" && n.readyAt > Date.now())) { clearInterval(S.countdownTimer); S.countdownTimer = 0; renderNotices(); } }, 500);
    } else if (run.status === "running") {
      // a one-step button reports in place (the ring); a macro shows its progress and «Stop»
      if (run.total > 1 || (prev && prev.kind !== "running")) upsertNotice({ id, kind: "running", run }); else dropNotice(id);
    } else {
      const silentAsk = run.output && (run.steps || []).length && !(run.client || []).length && lastStepShowsNothing(run);
      if (run.status === "done" && !silentAsk && !(run.cards || []).some((c) => c.undo) && run.total === 1) {
        dropNotice(id);
        flashButton(run.action, true);
      } else {
        upsertNotice({ id, kind: run.status === "done" ? "done" : "error", run, output: run.status === "done" && silentAsk ? run.output : "" });
        flashButton(run.action, run.status === "done");
        if (run.status === "done" && !silentAsk) setTimeout(() => { dropNotice(id); renderNotices(); Q.resize(); }, 6000);
      }
      setTimeout(() => { const r = S.runs.get(run.action); if (r && r.id === run.id) { S.runs.delete(run.action); renderRings(); } }, 1600);
    }
    renderRings();
    renderNotices();
    Q.resize();
  }
  function lastStepShowsNothing(run) {
    const a = actionsById().get(run.action);
    const last = a && a.steps[a.steps.length - 1];
    return !!last && last.type === "ask" && last.mode === "silent";
  }
  function renderRings() {
    if (!el) return;
    el.querySelectorAll("[data-run]").forEach((btn) => {
      const old = btn.querySelector(".qcv-ring");
      const html = runBadge(btn.dataset.run);
      if (old) old.remove();
      if (html) (btn.querySelector(".qcv-face, .qcv-sface") || btn).insertAdjacentHTML("beforeend", html);
      btn.setAttribute("aria-busy", html && !/data-status="(done|error|cancelled)"/.test(html) ? "true" : "false");
    });
  }
  function flashButton(id, ok) {
    if (!el || reduced()) return;
    el.querySelectorAll(`[data-run="${CSS.escape(id)}"]`).forEach((b) => { b.classList.remove("qcv-ok", "qcv-err"); void b.offsetWidth; b.classList.add(ok ? "qcv-ok" : "qcv-err"); });
  }
  async function handleClient(run, c) {
    const key = run.id + ":" + c.seq;
    if (S.handled.has(key)) return;
    S.handled.add(key);
    const reply = (ok, result = "", error = "") => Q.api("/api/quick/canvas/runs/" + encodeURIComponent(run.id) + "/client", { body: { seq: c.seq, ok, result, error } }).catch(() => {});
    if (c.type === "input") { Q.setInput(c.text || ""); reply(true); return; }
    if (c.type === "ask") {
      if (!String(c.text || "").trim()) { reply(false, "", t("Пустой запрос — буфер обмена или выделение пусты?", "Empty request — is the clipboard or the selection empty?")); return; }
      const go = () => {
        if (S.open && settings().collapse_on_answer !== false) setOpen(false, { save: false });
        const off = Q.on("answer-done", (e) => { off(); if (e && e.error) reply(false, "", String(e.error)); else reply(true, (e && e.text) || ""); });
        try { Q.send(c.text || "", { page: false, direct: true }); } catch (e) { off(); reply(false, "", e.message); }
      };
      // an answer is streaming: send() would STOP it (and its «done» would be taken for the button's answer) — wait for it
      if (Q.state().busy) { const offBusy = Q.on("answer-done", () => { offBusy(); setTimeout(go, 0); }); return; }
      go();
      return;
    }
    if (c.type === "card") {
      const card = c.card || {};
      const MC = window.MemuQuickCommands;
      if (card.client && card.client.type === "dfy" && window.QuickDFY && typeof window.QuickDFY.run === "function") {
        try { await window.QuickDFY.run(card.client.goal || ""); reply(true); } catch (e) { reply(false, "", e.message); }
        return;
      }
      if (MC && typeof MC.after === "function") { try { MC.after(card); reply(true); } catch (e) { reply(false, "", e.message); } return; }
      reply(false, "", t("Окно не умеет выполнить этот шаг", "The window cannot run this step"));
    }
  }
  async function noteAction(noteId, act, btn) {
    const n = S.notices.find((x) => x.id === noteId);
    if (!n) return;
    const run = n.run || {};
    if (act === "undo-local") { S.notices = S.notices.filter((x) => x !== n); render(); await n.undo(); return; }
    if (act === "accept" || act === "reject") {
      btn.disabled = true;
      try { onRun((await Q.api("/api/quick/canvas/runs/" + encodeURIComponent(run.id) + "/confirm", { body: { accept: act === "accept" } })).run); } catch (e) { Q.toast(e.message, "error"); }
      return;
    }
    if (act === "cancel") { try { onRun((await Q.api("/api/quick/canvas/runs/" + encodeURIComponent(run.id) + "/cancel", { body: {} })).run); } catch (e) { Q.toast(e.message, "error"); } return; }
    if (act === "copy") { Q.api("/api/quick/window", { body: { action: "copy", text: n.output } }).then((r) => { if (!r.ok && navigator.clipboard) navigator.clipboard.writeText(n.output).catch(() => {}); }).catch(() => navigator.clipboard && navigator.clipboard.writeText(n.output)); Q.toast(t("Скопировано", "Copied"), "ok"); return; }
    if (act === "insert") { Q.api("/api/quick/window", { body: { action: "insert", text: n.output } }).then((r) => { if (!r.ok) Q.toast(t("Вставка работает в приложении для Windows", "Pasting works in the Windows app"), "error"); }).catch(() => {}); return; }
    if (act === "undo") { try { await Q.api("/api/assistant/undo", { body: { undo_id: btn.dataset.undo, lang: L() } }); Q.toast(t("Вернул как было", "Reverted"), "ok"); } catch (e) { Q.toast(e.message, "error"); } }
    dropNotice(noteId); renderNotices(); Q.resize();
  }

  /* ------------------------------------------------------------ add view (buttons, templates, panels) */
  async function ensureCatalog() {
    if (S.catalog) return S.catalog;
    try { S.catalog = await Q.api("/api/quick/canvas/catalog?lang=" + L()); } catch (e) { S.catalog = null; }
    return S.catalog;
  }
  function renderAdd(view) {
    const st = settings();
    const tabs = [st.actions !== false ? ["buttons", t("Кнопки", "Buttons")] : null, st.panels !== false ? ["panels", t("Панели MemuAI", "MemuAI panels")] : null].filter(Boolean);
    if (!tabs.some((x) => x[0] === S.addTab)) S.addTab = tabs[0] ? tabs[0][0] : "buttons";
    const head = `<div class="qcv-sub"><button type="button" class="qcv-hbtn qcv-ico" data-qcv="back" aria-label="${esc(t("Назад", "Back"))}">${UI.back}</button>
      <div class="qcv-tabs" role="tablist">${tabs.map(([k, l]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${S.addTab === k}">${esc(l)}</button>`).join("")}</div></div>`;
    let body = "";
    if (S.addTab === "buttons") {
      const tpls = (S.catalog && S.catalog.templates) || null;
      body = `<div class="qcv-tpl-grid"><button type="button" class="qcv-tpl qcv-tpl-new" data-qcv="new-button"><span class="qcv-face">${UI.plus}</span><b>${esc(t("Своя кнопка", "Custom button"))}</b><small>${esc(t("Иконка, цвет, любые действия и сценарии", "Icon, colour, any actions and routines"))}</small></button>
        ${tpls ? tpls.map((tp, i) => `<button type="button" class="qcv-tpl" data-tpl="${i}" title="${esc(tp.tooltip || "")}"><span class="qcv-face" data-shape="${esc(tp.shape)}" style="${faceStyle(tp)}">${faceHtml(tp)}</span><b>${esc(tp.label)}</b><small>${esc(tp.tooltip || stepsSummary(tp))}</small></button>`).join("")
          : `<div class="qcv-skel" aria-hidden="true"><i></i><i></i></div>`}</div>
        <div class="qcv-io"><button type="button" class="qcv-link" data-qcv="export">${esc(t("Скопировать набор кнопок (JSON)", "Copy the button set (JSON)"))}</button>
        <button type="button" class="qcv-link" data-qcv="import">${esc(t("Вставить набор из буфера", "Import a set from the clipboard"))}</button></div>`;
      if (!tpls) ensureCatalog().then(() => { if (S.view === "add") render(); });
    } else {
      const placed = new Set(S.layout.filter((i) => i.type === "panel").map((i) => i.ref));
      const list = panelsList();
      body = !window.MemuPanels || !S.panelsReady ? `<div class="qcv-skel" aria-hidden="true"><i></i><i></i><i></i></div>`
        : `<div class="qcv-plist">${list.map((p) => `<div class="qcv-prow" data-pid="${esc(p.id)}"><span class="qcv-pname"><b>${esc(p.title)}</b><small>${esc(p.kind)}</small></span>
            ${placed.has(p.id) ? `<span class="qcv-badge">${esc(t("Уже здесь", "Added"))}</span>` : `<span class="qcv-sizes" role="group" aria-label="${esc(t("Размер", "Size"))}">${Object.keys(PANEL_SIZES).map((k) => `<button type="button" data-add-panel="${esc(p.id)}" data-size="${k}" title="${esc(t("Добавить", "Add"))} ${k.toUpperCase()}">${k.toUpperCase()}</button>`).join("")}</span>`}</div>`).join("")
          || `<p class="qcv-hint">${esc(t("В MemuAI пока нет панелей.", "There are no panels in MemuAI yet."))}</p>`}</div>
          <button type="button" class="qcv-link" data-qcv="memuai-panels">${UI.open}${esc(t("Создать новую панель в MemuAI", "Create a new panel in MemuAI"))}</button>`;
      if (!S.panelsReady) ensurePanels().then(() => { if (S.view === "add") render(); });
    }
    view.innerHTML = head + `<div class="qcv-scroll qcv-pad">${body}</div>`;
  }
  const stepsWord = (n) => (n % 10 === 1 && n % 100 !== 11 ? "шаг" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "шага" : "шагов");
  const stepsSummary = (a) => (a.steps || []).length > 1 ? t(`Сценарий: ${a.steps.length} ${stepsWord(a.steps.length)}`, `Routine: ${a.steps.length} steps`) : "";
  async function addPanel(pid, size) {
    try {
      const out = await Q.api("/api/quick/canvas/panels", { body: { panel: pid, size } });
      S.layout = out.layout.items.map((i) => ({ ...i }));
      if (S.data) S.data.layout = out.layout;
      if (out.item) S.fit.add(out.item.id);
      S.view = "grid";
      if (S.screen) { notifyLayer(); if (!S.layer.visible) layerCommand("show"); Q.toast(t("Панель на экране — её можно перетащить куда угодно", "The panel is on the screen — drag it anywhere"), "ok"); }
      render();
      requestAnimationFrame(fitNewPanels);
    } catch (e) { Q.toast(e.message, "error"); }
  }
  async function exportSet() {
    try {
      const data = await Q.api("/api/quick/canvas/export");
      const text = JSON.stringify(data, null, 2);
      const r = await Q.api("/api/quick/window", { body: { action: "copy", text } }).catch(() => ({ ok: false }));
      if (!r.ok && navigator.clipboard) await navigator.clipboard.writeText(text);
      Q.toast(t(`Скопировано кнопок: ${data.actions.length}`, `Copied ${data.actions.length} buttons`), "ok");
    } catch (e) { Q.toast(e.message, "error"); }
  }
  async function importSet() {
    let text = "";
    try { text = navigator.clipboard ? await navigator.clipboard.readText() : ""; } catch (e) { text = ""; }
    let data = null;
    try { data = JSON.parse(text); } catch (e) { data = null; }
    if (!data) { Q.toast(t("В буфере нет набора кнопок (JSON). Импорт файла — в Настройках MemuAI.", "No button set (JSON) in the clipboard. File import is in MemuAI Settings."), "error"); return; }
    try {
      const out = await Q.api("/api/quick/canvas/import", { body: { data, mode: "merge", lang: L() } });
      Q.toast(t(`Добавлено кнопок: ${out.added}`, `Added ${out.added} buttons`) + (out.skipped ? t(`, пропущено: ${out.skipped}`, `, skipped: ${out.skipped}`) : ""), "ok");
      await load(); S.view = "grid"; render(); notifyLayer();
    } catch (e) { Q.toast(e.message, "error"); }
  }

  /* ------------------------------------------------------------ approval (first run of a risky button) */
  function renderApprove(view) {
    const ap = S.approve || { risks: [] };
    view.innerHTML = `<div class="qcv-approve" role="alertdialog" aria-labelledby="qcv-ap-t">
      <span class="qcv-note-ic">${UI.warn}</span>
      <div><b id="qcv-ap-t">${esc(t(`Первый запуск «${ap.label || "кнопки"}»`, `First run of “${ap.label || "the button"}”`))}</b>
      <p>${esc(t("Эта кнопка выполнит действия, которые меняют систему. Проверьте их — согласие запомнится, пока шаги не изменятся.",
        "This button runs actions that change your system. Check them — the approval is kept until the steps change."))}</p>
      <ul>${ap.risks.map((r) => `<li data-tier="${esc(r.tier)}">${esc(r.text)}${r.tier === "dangerous" ? ` <em>${esc(t("— будет спрашивать каждый раз", "— will ask every time"))}</em>` : ""}</li>`).join("")}</ul>
      <div class="qcv-note-act"><button type="button" class="qcv-btn-p" data-qcv="approve">${esc(t("Выполнить и запомнить", "Run and remember"))}</button>
      <button type="button" class="qcv-btn-g" data-qcv="back">${esc(t("Отмена", "Cancel"))}</button></div></div></div>`;
    requestAnimationFrame(() => { const b = view.querySelector('[data-qcv="approve"]'); if (b) b.focus(); });
  }

  /* ------------------------------------------------------------ the button editor */
  const STEP_LABELS = () => ({
    open: t("Открыть", "Open"), command: t("Команда", "Command"), ask: t("Спросить ИИ", "Ask AI"), text: t("Текст", "Text"),
    system: t("Система", "System"), memuai: "MemuAI", wait: t("Пауза", "Wait"), keys: t("Клавиши", "Keys"),
    dfy: t("Сделай за меня", "Do it for me"), computer: t("Управление ПК", "Computer use"), shell: "PowerShell",
  });
  const DEFAULT_STEP = {
    open: { type: "open", target: "app", value: "" }, command: { type: "command", action: "volume.set", params: { level: 30 } },
    ask: { type: "ask", mode: "window", prompt: "" }, text: { type: "text", mode: "paste", text: "" }, system: { type: "system", op: "screenshot", value: "region" },
    memuai: { type: "memuai", target: "new_chat" }, wait: { type: "wait", ms: 1000 }, keys: { type: "keys", keys: "" },
    dfy: { type: "dfy", goal: "" }, computer: { type: "computer", task: "" }, shell: { type: "shell", command: "" },
  };
  function openEditor(action, template) {
    const base = action ? JSON.parse(JSON.stringify(action)) : template ? JSON.parse(JSON.stringify(template))
      : { label: "", tooltip: "", icon: { kind: "builtin", value: "bolt" }, color: "", color2: "", shape: "circle", hotkey: "", steps: [{ ...DEFAULT_STEP.open }] };
    delete base.risks; delete base.key; delete base.approved;
    const item = action ? S.layout.find((i) => i.type === "action" && i.ref === action.id) : null;
    S.editor = { action: base, id: action ? action.id : "", size: item && item.w === 2 ? "l" : "", iconTab: base.icon.kind === "app" ? "image" : base.icon.kind, error: "", risks: [], capturing: false };
    S.view = "editor";
    setOpen(true, { save: false });
    ensureCatalog().then(() => { if (S.view === "editor") { renderEditorPreserving(); validateSoon(); } });
  }
  function renderEditorPreserving() {
    const view = el.querySelector(".qcv-view");
    const scroll = view.querySelector(".qcv-scroll");
    const top = scroll ? scroll.scrollTop : 0;
    const active = document.activeElement && view.contains(document.activeElement) ? document.activeElement.dataset.f : "";
    renderEditor(view);
    const s2 = view.querySelector(".qcv-scroll");
    if (s2) s2.scrollTop = top;
    if (active) { const f = view.querySelector(`[data-f="${CSS.escape(active)}"]`); if (f) f.focus({ preventScroll: true }); }
    Q.resize();
  }
  function optionList(pairs, value) { return pairs.map(([v, l]) => `<option value="${esc(v)}"${String(v) === String(value) ? " selected" : ""}>${esc(l)}</option>`).join(""); }
  function stepFields(step, i) {
    const cat = S.catalog || {};
    const f = (name) => `data-f="s${i}.${name}"`;
    const ph = t("можно {clipboard}, {selection}, {date}…", "you can use {clipboard}, {selection}, {date}…");
    if (step.type === "open") {
      const targets = [["app", t("Программу", "App")], ["url", t("Ссылку", "Link")], ["path", t("Файл", "File")], ["folder", t("Папку", "Folder")], ["settings", t("Параметры Windows", "Windows Settings")]];
      let value = `<input ${f("value")} value="${esc(step.value || "")}" placeholder="${esc(step.target === "app" ? t("Telegram, Chrome, калькулятор…", "Telegram, Chrome, Calculator…") : step.target === "url" ? "https://…" : "C:\\Users\\…")}">`;
      if (step.target === "folder") value = `<select ${f("value")}>${optionList((cat.folders || []).map((x) => [x, x]), step.value)}</select>`;
      if (step.target === "settings") value = `<select ${f("value")}>${optionList([["", t("Главная", "Home")]].concat((cat.settings_pages || []).map((x) => [x, x])), step.value)}</select>`;
      return `<select ${f("target")}>${optionList(targets, step.target)}</select>${value}`;
    }
    if (step.type === "command") {
      const cmds = cat.commands || [];
      const cats = cat.categories || {};
      const groups = {};
      cmds.forEach((c) => { (groups[c.cat] = groups[c.cat] || []).push(c); });
      const row = cmds.find((c) => c.id === step.action) || null;
      const sel = `<select ${f("action")} class="qcv-wide">${Object.entries(groups).map(([g, list]) => `<optgroup label="${esc(cats[g] || g)}">${list.map((c) => `<option value="${esc(c.id)}"${c.id === step.action ? " selected" : ""}>${esc(c.title)}${c.tier !== "safe" ? " ⚠" : ""}</option>`).join("")}</optgroup>`).join("")}</select>`;
      const params = row ? Object.entries(row.params || {}).map(([k, spec]) => {
        const v = (step.params || {})[k];
        const label = `<span class="qcv-plabel">${esc(k)}${(row.required || []).includes(k) ? " *" : ""}</span>`;
        if (spec.enum) return `<label class="qcv-param">${label}<select data-f="s${i}.p.${esc(k)}">${optionList([["", "—"]].concat(spec.enum.filter(Boolean).map((x) => [x, x])), v == null ? "" : v)}</select></label>`;
        if (spec.type === "boolean") return `<label class="qcv-param qcv-check"><input type="checkbox" data-f="s${i}.p.${esc(k)}"${v ? " checked" : ""}>${label}</label>`;
        return `<label class="qcv-param">${label}<input data-f="s${i}.p.${esc(k)}" value="${esc(v == null ? "" : v)}" placeholder="${esc(String(spec.description || "").slice(0, 60))}"></label>`;
      }).join("") : "";
      return `${sel}${row && row.tier !== "safe" ? `<span class="qcv-tier" data-tier="${esc(row.tier)}">${esc(row.tier === "dangerous" ? t("опасное: подтверждение каждый раз", "dangerous: confirmed every time") : t("спросит при первом запуске", "asks on the first run"))}</span>` : ""}<div class="qcv-params">${params}</div>`;
    }
    if (step.type === "ask") {
      return `<select ${f("mode")}>${optionList([["window", t("Ответ в окне", "Answer in the window")], ["silent", t("Без окна → {prev}", "Silently → {prev}")]], step.mode)}</select>
        <textarea ${f("prompt")} rows="3" placeholder="${esc(t("Например: Переведи на английский: {clipboard}", "E.g.: Translate into Russian: {clipboard}"))}">${esc(step.prompt || "")}</textarea>`;
    }
    if (step.type === "text") {
      return `<select ${f("mode")}>${optionList([["paste", t("Вставить в программу", "Paste into the program")], ["copy", t("Скопировать в буфер", "Copy to the clipboard")], ["input", t("В поле быстрого доступа", "Into the Quick Ask field")]], step.mode)}</select>
        <textarea ${f("text")} rows="2" placeholder="${esc(ph)}">${esc(step.text || "")}</textarea>`;
    }
    if (step.type === "system") {
      const ops = [["screenshot", t("Скриншот", "Screenshot")], ["lock", t("Заблокировать", "Lock")], ["dnd", t("Не беспокоить", "Do not disturb")], ["theme", t("Тема Windows", "Windows theme")],
        ["mute", t("Звук", "Sound")], ["minimize_all", t("Свернуть все окна", "Minimize all")], ["media", t("Музыка/видео", "Media")]];
      const values = { dnd: [["toggle", t("Переключить", "Toggle")], ["on", t("Включить", "On")], ["off", t("Выключить", "Off")]],
        theme: [["toggle", t("Переключить", "Toggle")], ["dark", t("Тёмная", "Dark")], ["light", t("Светлая", "Light")]],
        mute: [["toggle", t("Переключить", "Toggle")], ["on", t("Выключить звук", "Mute")], ["off", t("Включить звук", "Unmute")]],
        screenshot: [["region", t("Область", "Region")], ["screen", t("Весь экран", "Full screen")], ["window", t("Окно", "Window")]],
        media: [["play_pause", t("Пауза/воспроизведение", "Play/pause")], ["next", t("Следующий", "Next")], ["prev", t("Предыдущий", "Previous")], ["stop", t("Стоп", "Stop")]] }[step.op];
      return `<select ${f("op")}>${optionList(ops, step.op)}</select>${values ? `<select ${f("value")}>${optionList(values, step.value)}</select>` : ""}`;
    }
    if (step.type === "memuai") {
      const targets = [["new_chat", t("Новый чат", "New chat")], ["main", t("Главное окно", "Main window")], ["chat", t("Отправить в чат…", "Send to a chat…")], ["panels", t("Панели", "Panels")]]
        .concat((cat.memuai_targets || []).filter((x) => !["new_chat", "main", "panels"].includes(x)).map((x) => [x, x]));
      return `<select ${f("target")}>${optionList(targets, step.target)}</select>${step.target === "chat" ? `<textarea ${f("text")} rows="2" placeholder="${esc(ph)}">${esc(step.text || "")}</textarea>` : ""}`;
    }
    if (step.type === "wait") return `<label class="qcv-param"><span class="qcv-plabel">${esc(t("секунд", "seconds"))}</span><input type="number" min="0.05" max="600" step="0.1" ${f("sec")} value="${(step.ms || 1000) / 1000}"></label>`;
    if (step.type === "keys") return `<input ${f("keys")} value="${esc(step.keys || "")}" placeholder="Ctrl+Shift+Esc"><span class="qcv-tier" data-tier="confirm">${esc(t("спросит при первом запуске", "asks on the first run"))}</span>`;
    if (step.type === "dfy") return `<textarea ${f("goal")} rows="2" placeholder="${esc(t("Что сделать в браузере", "What to do in the browser"))}">${esc(step.goal || "")}</textarea>`;
    if (step.type === "computer") return `<textarea ${f("task")} rows="2" placeholder="${esc(t("Что сделать на компьютере", "What to do on the computer"))}">${esc(step.task || "")}</textarea>`;
    if (step.type === "shell") {
      return `<div class="qcv-danger">${UI.warn}<span>${esc(t("Команда PowerShell выполняется от вашего имени и может изменить или удалить файлы. Работает, только если в настройках команд включён «Расширенный режим», и каждый раз спрашивает подтверждение с отсчётом.",
        "A PowerShell command runs as you and can change or delete files. It works only with “Advanced mode” on in Commands settings and asks for confirmation with a countdown every time."))}</span></div>
        <textarea ${f("command")} rows="2" spellcheck="false" placeholder="Get-Process | Sort CPU -desc | Select -First 5">${esc(step.command || "")}</textarea>`;
    }
    return "";
  }
  function renderEditor(view) {
    const ed = S.editor;
    if (!ed) { S.view = "grid"; return renderGrid(view); }
    const a = ed.action;
    const labels = STEP_LABELS();
    const tab = ed.iconTab === "app" ? "image" : ed.iconTab;
    const iconPane = tab === "builtin" ? `<div class="qcv-icons">${(icons ? icons.names : []).map((n) => `<button type="button" data-icon="${n}" aria-pressed="${a.icon.kind === "builtin" && a.icon.value === n}" title="${n}">${icons.svg(n)}</button>`).join("")}</div>`
      : tab === "emoji" ? `<div class="qcv-icons qcv-emojis">${(icons ? icons.EMOJI : []).map((e) => `<button type="button" data-emoji="${esc(e)}" aria-pressed="${a.icon.kind === "emoji" && a.icon.value === e}">${esc(e)}</button>`).join("")}</div>
          <input data-f="emoji" maxlength="16" value="${a.icon.kind === "emoji" ? esc(a.icon.value) : ""}" placeholder="${esc(t("или свой символ", "or your own symbol"))}">`
        : `<div class="qcv-imgpick"><button type="button" class="qcv-btn-g" data-qcv="pick-image">${esc(t("Выбрать картинку…", "Choose a picture…"))}</button>
          <small>${esc(t("PNG, JPG, WebP или значок .ico — уменьшится до 128 px", "PNG, JPG, WebP or an .ico icon — scaled to 128 px"))}</small><input type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/x-icon,.ico" data-f="image" hidden></div>
          <div class="qcv-imgpick qcv-appicon"><input data-f="appicon" value="${esc(ed.appIcon != null ? ed.appIcon : (a.icon.kind === "app" && a.icon.app) || firstApp(a))}" placeholder="${esc(t("Программа или путь к .exe", "An app or a path to an .exe"))}" aria-label="${esc(t("Программа для иконки", "App for the icon"))}">
          <button type="button" class="qcv-btn-g" data-qcv="app-icon"${ed.iconBusy ? " disabled" : ""}>${esc(ed.iconBusy ? t("Ищу…", "Looking…") : t("Иконка программы", "App icon"))}</button></div>`;
    const colors = icons ? icons.COLORS : [];
    const steps = a.steps.map((s, i) => `<div class="qcv-step" data-step="${i}">
        <div class="qcv-step-head"><span class="qcv-step-n">${i + 1}</span>
          <select data-f="s${i}.type" aria-label="${esc(t("Тип шага", "Step type"))}">${optionList(Object.entries(labels), s.type)}</select><span class="qcv-grow"></span>
          <button type="button" class="qcv-mini" data-step-act="up" ${i === 0 ? "disabled" : ""} aria-label="${esc(t("Выше", "Up"))}">${UI.up}</button>
          <button type="button" class="qcv-mini" data-step-act="down" ${i === a.steps.length - 1 ? "disabled" : ""} aria-label="${esc(t("Ниже", "Down"))}">${UI.down}</button>
          <button type="button" class="qcv-mini" data-step-act="remove" ${a.steps.length === 1 ? "disabled" : ""} aria-label="${esc(t("Удалить шаг", "Delete step"))}">${UI.trash}</button></div>
        <div class="qcv-step-body">${stepFields(s, i)}</div>
        ${a.steps.length > 1 || s.confirm || s.continue_on_error ? `<div class="qcv-step-opts"><label><input type="checkbox" data-f="s${i}.continue_on_error"${s.continue_on_error ? " checked" : ""}>${esc(t("Продолжить при ошибке", "Continue on error"))}</label>
          <label><input type="checkbox" data-f="s${i}.confirm"${s.confirm ? " checked" : ""}>${esc(t("Спрашивать перед шагом", "Ask before this step"))}</label></div>` : ""}</div>`).join("");
    view.innerHTML = `<div class="qcv-sub"><button type="button" class="qcv-hbtn qcv-ico" data-qcv="back" aria-label="${esc(t("Назад", "Back"))}">${UI.back}</button>
        <b class="qcv-title">${esc(ed.id ? t("Кнопка", "Button") : t("Новая кнопка", "New button"))}</b><span class="qcv-grow"></span>
        ${ed.id ? `<button type="button" class="qcv-hbtn" data-qcv="ed-dup" title="${esc(t("Дублировать", "Duplicate"))}">${UI.dup}</button><button type="button" class="qcv-hbtn danger" data-qcv="ed-del" title="${esc(t("Удалить", "Delete"))}">${UI.trash}</button>` : ""}
        <button type="button" class="qcv-btn-p" data-qcv="ed-save">${esc(t("Сохранить", "Save"))}</button></div>
      <div class="qcv-scroll qcv-pad qcv-editor">
        <div class="qcv-ed-top">
          <div class="qcv-preview"><span class="qcv-act" data-shape="${esc(a.shape)}" data-big="${ed.size === "l" ? 1 : 0}" style="${faceStyle(a)}"><span class="qcv-face">${faceHtml(a)}</span><span class="qcv-label">${esc(a.label || t("Подпись", "Label"))}</span></span></div>
          <div class="qcv-ed-fields">
            <label class="qcv-field"><span>${esc(t("Подпись", "Label"))}</span><input data-f="label" maxlength="40" value="${esc(a.label)}" placeholder="${esc(t("Например: Созвон", "E.g.: Call"))}"></label>
            <label class="qcv-field"><span>${esc(t("Подсказка", "Tooltip"))}</span><input data-f="tooltip" maxlength="200" value="${esc(a.tooltip || "")}" placeholder="${esc(t("Что делает кнопка", "What the button does"))}"></label>
            <label class="qcv-field"><span>${esc(t("Сочетание", "Hotkey"))}</span><input data-f="hotkey" readonly value="${esc(a.hotkey || "")}" placeholder="${esc(ed.capturing ? t("Нажмите сочетание…", "Press a combo…") : t("Щёлкните и нажмите, напр. Ctrl+Alt+1", "Click and press, e.g. Ctrl+Alt+1"))}"${ed.capturing ? ' data-capturing="1"' : ""}>${a.hotkey ? `<button type="button" class="qcv-mini" data-qcv="hk-clear" aria-label="${esc(t("Убрать сочетание", "Clear hotkey"))}">${UI.x}</button>` : ""}</label>
            ${a.hotkey ? `<label class="qcv-check qcv-global"><input type="checkbox" data-f="global"${a.global ? " checked" : ""}>${esc(t("Во всех программах — даже когда окно закрыто", "In every program — even with the window closed"))}</label>` : ""}
          </div></div>
        <div class="qcv-group"><div class="qcv-tabs" role="tablist">${[["builtin", t("Значки", "Icons")], ["emoji", t("Эмодзи", "Emoji")], ["image", t("Картинка", "Picture")]].map(([k, l]) => `<button type="button" role="tab" data-icon-tab="${k}" aria-selected="${tab === k}">${esc(l)}</button>`).join("")}</div>${iconPane}</div>
        <div class="qcv-group qcv-looks">
          <div class="qcv-swatches" role="group" aria-label="${esc(t("Цвет", "Colour"))}"><button type="button" data-color="" aria-pressed="${!a.color}" class="qcv-sw-accent" title="${esc(t("Цвет темы", "Theme colour"))}"></button>${colors.map((c) => `<button type="button" data-color="${c}" aria-pressed="${a.color === c}" style="--sw:${c}" title="${c}"></button>`).join("")}
            <label class="qcv-sw-custom" title="${esc(t("Свой цвет", "Custom colour"))}"><input type="color" data-f="color" value="${esc(a.color || "#6366f1")}"></label></div>
          <div class="qcv-seg-row">
            <label class="qcv-check"><input type="checkbox" data-f="gradient"${a.color2 ? " checked" : ""}${a.color ? "" : " disabled"}>${esc(t("Градиент", "Gradient"))}</label>${a.color2 ? `<input type="color" data-f="color2" value="${esc(a.color2)}">` : ""}
            <span class="qcv-seg" role="group">${[["circle", t("Круг", "Circle")], ["squircle", t("Скруглённая", "Rounded")]].map(([k, l]) => `<button type="button" data-shape-set="${k}" aria-pressed="${a.shape === k}">${esc(l)}</button>`).join("")}</span>
            <span class="qcv-seg" role="group">${[["", t("Обычная", "Normal")], ["l", t("Большая", "Large")]].map(([k, l]) => `<button type="button" data-size-set="${k}" aria-pressed="${ed.size === k}">${esc(l)}</button>`).join("")}</span></div></div>
        <div class="qcv-group"><div class="qcv-steps-head"><b>${esc(a.steps.length > 1 ? t("Сценарий", "Routine") : t("Действие", "Action"))}</b>
          <span class="qcv-hint">${esc(t("Шаги выполняются по очереди; {prev} — результат предыдущего.", "Steps run in order; {prev} is the previous step's result."))}</span></div>
          ${steps}
          <div class="qcv-add-step">${["open", "command", "ask", "text", "system", "wait", "memuai", "keys", "dfy", "computer", "shell"].map((k) => `<button type="button" data-add-step="${k}">${UI.plus}${esc(labels[k])}</button>`).join("")}</div>
          <div class="qcv-vars">${(S.catalog && S.catalog.placeholders || []).map((p) => `<button type="button" data-var="{${p}}" title="${esc(t("Вставить в поле", "Insert into the field"))}">{${p}}</button>`).join("")}</div></div>
        ${riskHtml(ed.risks)}
        ${ed.error ? `<div class="qcv-error" role="alert">${esc(ed.error)}</div>` : ""}
      </div>`;
  }
  /** What the button will ask: once (confirm-tier steps, remembered) and every time (dangerous ones). */
  function riskHtml(risks) {
    const once = risks.filter((r) => r.tier !== "dangerous").map((r) => r.text);
    const always = risks.filter((r) => r.tier === "dangerous").map((r) => r.text);
    const line = (label, list) => (list.length ? `<span><b>${esc(label)}</b> ${esc(list.join("; "))}</span>` : "");
    if (!once.length && !always.length) return "";
    return `<div class="qcv-risk">${UI.warn}<div>${line(t("Спросит при первом запуске:", "Asks on the first run:"), once)}${line(t("Подтверждение при каждом запуске:", "Confirmed on every run:"), always)}</div></div>`;
  }
  let validateTimer = 0, lastVarField = null;
  function validateSoon() {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(async () => {
      if (!S.editor) return;
      try {
        const out = await Q.api("/api/quick/canvas/actions/validate", { body: { action: S.editor.action, lang: L() } });
        const err = out.ok ? "" : out.error;
        const risks = out.ok ? out.risks || [] : S.editor.risks;
        if (err !== S.editor.error || JSON.stringify(risks) !== JSON.stringify(S.editor.risks)) {
          S.editor.error = err; S.editor.risks = risks;
          const view = el.querySelector(".qcv-view");
          const box = view.querySelector(".qcv-editor");
          if (box) {
            box.querySelectorAll(".qcv-risk, .qcv-error").forEach((x) => x.remove());
            if (risks.length) box.insertAdjacentHTML("beforeend", riskHtml(risks));
            if (err) box.insertAdjacentHTML("beforeend", `<div class="qcv-error" role="alert">${esc(err)}</div>`);
            Q.resize();
          }
        }
      } catch (e) { /* offline: Save reports it */ }
    }, 350);
  }
  function setPath(obj, path, value) {
    const parts = path.split(".");
    let o = obj;
    for (let i = 0; i < parts.length - 1; i++) { if (o[parts[i]] == null || typeof o[parts[i]] !== "object") o[parts[i]] = {}; o = o[parts[i]]; }
    o[parts[parts.length - 1]] = value;
  }
  function editorInput(target, structural) {
    const ed = S.editor;
    const f = target.dataset.f;
    if (!ed || !f) return;
    const a = ed.action;
    const value = target.type === "checkbox" ? target.checked : target.value;
    let rerender = structural;
    if (f === "label" || f === "tooltip") a[f] = value;
    else if (f === "appicon") { ed.appIcon = value; return; }
    else if (f === "global") a.global = !!value;
    else if (f === "emoji") { if (value.trim()) a.icon = { kind: "emoji", value: value.trim() }; rerender = false; updatePreview(); }
    else if (f === "color") { a.color = value; rerender = true; }
    else if (f === "color2") { a.color2 = value; rerender = true; }
    else if (f === "gradient") { a.color2 = value ? (a.color2 || shade(a.color || "#6366f1")) : ""; rerender = true; }
    else if (f === "image") { readImage(target.files && target.files[0]); return; }
    else if (/^s\d+\./.test(f)) {
      const i = +f.slice(1, f.indexOf("."));
      const key = f.slice(f.indexOf(".") + 1);
      const step = a.steps[i];
      if (!step) return;
      if (key === "type") { a.steps[i] = { ...JSON.parse(JSON.stringify(DEFAULT_STEP[value])), ...(step.continue_on_error ? { continue_on_error: true } : {}), ...(step.confirm ? { confirm: true } : {}) }; rerender = true; }
      else if (key === "sec") step.ms = Math.round(Math.max(0.05, Math.min(600, parseFloat(value) || 1)) * 1000);
      else if (key.startsWith("p.")) {
        const pk = key.slice(2);
        step.params = step.params || {};
        if (value === "" || value === false) delete step.params[pk]; else step.params[pk] = value;
      } else if (key === "action") { step.action = value; step.params = {}; rerender = true; }
      else {
        if ((key === "continue_on_error" || key === "confirm") && !value) delete step[key]; else step[key] = value;
        if (key === "target" || key === "op" || key === "mode") {
          if (key === "target" && step.type === "open") step.value = value === "folder" ? "downloads" : "";
          if (key === "op") step.value = "";
          rerender = true;
        }
      }
    }
    if (rerender) renderEditorPreserving(); else if (f === "label") updatePreview();
    validateSoon();
  }
  function updatePreview() {
    const p = el.querySelector(".qcv-preview");
    const a = S.editor.action;
    if (p) p.innerHTML = `<span class="qcv-act" data-shape="${esc(a.shape)}" data-big="${S.editor.size === "l" ? 1 : 0}" style="${faceStyle(a)}"><span class="qcv-face">${faceHtml(a)}</span><span class="qcv-label">${esc(a.label || t("Подпись", "Label"))}</span></span>`;
  }
  /** The default second colour of a gradient: the neighbour two steps along the palette (a related hue). */
  function shade(hex) {
    const list = (icons && icons.COLORS) || [];
    const i = list.indexOf(String(hex).toLowerCase());
    return i >= 0 ? list[(i + 2) % list.length] : "#8b5cf6";
  }
  function readImage(file) {
    if (!file || !(/^image\//.test(file.type) || /\.ico$/i.test(file.name))) return;
    if (file.size > 8 * 1024 * 1024) { Q.toast(t("Картинка больше 8 МБ", "The picture is over 8 MB"), "error"); return; }
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const side = 128, c = document.createElement("canvas");
      c.width = c.height = side;
      const k = Math.min(side / img.width, side / img.height);
      const w = img.width * k, h = img.height * k;
      c.getContext("2d").drawImage(img, (side - w) / 2, (side - h) / 2, w, h);
      URL.revokeObjectURL(url);
      let data = c.toDataURL("image/png");
      if (data.length > 120000) data = c.toDataURL("image/webp", 0.85);
      S.editor.action.icon = { kind: "image", value: data };
      renderEditorPreserving(); validateSoon();
    };
    img.onerror = () => { URL.revokeObjectURL(url); Q.toast(t("Не удалось открыть картинку", "Could not open the picture"), "error"); };
    img.src = url;
  }
  /** The program of the button's first «Открыть программу» step — the natural source of its icon. */
  const firstApp = (a) => { const st = (a.steps || []).find((x) => x.type === "open" && x.target === "app" && x.value); return st ? st.value : ""; };
  async function pickAppIcon() {
    const ed = S.editor;
    if (!ed || ed.iconBusy) return;
    const field = el.querySelector('[data-f="appicon"]');
    const app = ((field && field.value) || "").trim();
    ed.appIcon = app;
    if (!app) { ed.error = t("Укажите программу или путь к .exe", "Give an app or a path to an .exe"); renderEditorPreserving(); return; }
    ed.iconBusy = true; renderEditorPreserving();
    try {
      const out = await Q.api("/api/quick/canvas/app-icon", { body: { app, lang: L() } });
      if (S.editor !== ed) return;
      if (out.ok) { ed.action.icon = { kind: "app", value: out.data, app: out.app || app }; ed.error = ""; }
      else ed.error = out.error || t("Не удалось взять иконку", "Couldn't get the icon");
    } catch (e) { ed.error = e.message; }
    finally { ed.iconBusy = false; if (S.editor === ed) { renderEditorPreserving(); if (!ed.error) validateSoon(); } }   // the check would wipe the message
  }
  async function saveEditor() {
    const ed = S.editor;
    if (!ed) return;
    const action = { ...ed.action };
    if (ed.id) action.id = ed.id;
    try {
      const out = await Q.api("/api/quick/canvas/actions", { body: { action, size: ed.size, lang: L() } });
      // a size change of an existing button is a layout change
      await load();
      const it = S.layout.find((i) => i.type === "action" && i.ref === out.action.id);
      const want = ed.size === "l" ? 2 : 1;
      if (it && it.w !== want) { S.layout = placeAt(S.layout, it, Math.min(it.x, COLS - want), it.y, want, want); saveLayout(); }
      S.editor = null; S.view = "grid";
      if (S.screen) { setOpen(false, { save: false }); notifyLayer(); if (!S.layer.visible && out.result === "created") layerCommand("show"); }
      render();
      const hk = out.hotkey;
      if (out.action.global && hk && !hk.ok && hk.code !== "unsupported") Q.toast(t("Сохранено, но сочетание не заработало: ", "Saved, but the hotkey did not register: ") + (hk.error || hk.code || ""), "error");
      else Q.toast(out.result === "created" ? t("Кнопка добавлена", "Button added") : t("Сохранено", "Saved"), "ok");
    } catch (e) {
      ed.error = e.message;
      renderEditorPreserving();
    }
  }

  /* ------------------------------------------------------------ focus & keyboard */
  function focusItem(id) {
    if (!el) return;
    const node = id && el.querySelector(`.qcv-item[data-item="${CSS.escape(id)}"]`);
    if (!node) return;
    S.focusId = id;
    el.querySelectorAll(".qcv-item").forEach((n) => { n.tabIndex = n === node ? 0 : -1; });
    node.focus({ preventScroll: false });
  }
  function neighbour(fromId, dx, dy) {
    const from = S.display.find((i) => i.id === fromId);
    if (!from) return S.display[0] && S.display[0].id;
    const cx = from.x + from.w / 2, cy = from.y + from.h / 2;
    let best = null, bestScore = Infinity;
    for (const it of S.display) {
      if (it.id === fromId) continue;
      const ix = it.x + it.w / 2, iy = it.y + it.h / 2;
      const ddx = ix - cx, ddy = iy - cy;
      if (dx && Math.sign(ddx) !== dx) continue;
      if (dy && Math.sign(ddy) !== dy) continue;
      const score = dx ? Math.abs(ddx) + Math.abs(ddy) * 2.5 : Math.abs(ddy) + Math.abs(ddx) * 2.5;
      if (score < bestScore) { bestScore = score; best = it; }
    }
    return best ? best.id : fromId;
  }
  function moveFocused(dx, dy) {
    const it = S.layout.find((i) => i.id === S.focusId);
    if (!it) return;
    const x = Math.max(0, Math.min(COLS - it.w, it.x + dx)), y = Math.max(0, it.y + dy);
    S.layout = placeAt(S.layout, it, x, y);
    saveLayout(); render(); focusItem(it.id);
  }
  /** ↓ in the empty input (capture phase, before quick.js moves into the — hidden — idle list) enters the canvas. */
  function onCaptureKey(e) {
    // Esc folds the editor / leaves edit mode wherever the focus is (quick.js would close the window first)
    if (e.key === "Escape" && (S.edit || S.view !== "grid") && S.open && !menuEl && !document.querySelector(".mp-modal-back")
        && !(S.editor && S.editor.capturing) && !(el && el.contains(e.target))) {
      e.preventDefault(); e.stopPropagation();
      if (S.view !== "grid") { S.view = "grid"; S.editor = null; S.approve = null; render(); } else setEdit(false);
      return;
    }
    if (e.target.id !== "qa-input" || e.key !== "ArrowDown" || e.shiftKey || e.altKey || e.ctrlKey) return;
    if (!S.open || S.view !== "grid" || e.target.value.trim() || !S.display.length) return;
    e.preventDefault(); e.stopPropagation();
    focusItem(S.focusId || S.display[0].id);
  }
  function onHostKey(e) {
    if (S.editor && S.editor.capturing) return;
    const item = e.target.closest && e.target.closest(".qcv-item");
    const inField = e.target.matches && e.target.matches("input, textarea, select, [contenteditable]");
    if (e.key === "Escape") {
      if (menuEl) return;
      if (inField && S.view === "grid") return;      // a To-Do / note field handles its own Esc
      e.preventDefault(); e.stopPropagation();
      if (S.view !== "grid") { S.view = "grid"; S.editor = null; S.approve = null; render(); return; }
      if (S.edit) { setEdit(false); return; }
      Q.setInput(Q.getInput());                         // back to the input; the window stays
      return;
    }
    if (!item || inField || e.target.closest(".mp-card input, .mp-card textarea")) return;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (arrows[e.key]) {
      e.preventDefault(); e.stopPropagation();
      if (S.edit && e.altKey) { moveFocused(...arrows[e.key]); return; }
      const next = neighbour(item.dataset.item, ...arrows[e.key]);
      if (e.key === "ArrowUp" && next === item.dataset.item) { Q.setInput(Q.getInput()); return; }   // top row ↑ → the input
      focusItem(next);
      return;
    }
    if ((e.key === "Enter" || e.key === " ") && e.target === item) {
      e.preventDefault(); e.stopPropagation();
      const it = S.layout.find((i) => i.id === item.dataset.item);
      if (!it) return;
      if (S.edit || it.type === "panel") openItemMenu(it.id, item);
      else runAction(it.ref, true);
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && S.edit && e.target === item) { e.preventDefault(); e.stopPropagation(); removeItem(item.dataset.item); return; }
    if ((e.key === "F10" && e.shiftKey) || e.key === "ContextMenu") { e.preventDefault(); openItemMenu(item.dataset.item, item); return; }
    if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.altKey && !e.metaKey && !S.edit) {
      const acts = S.display.filter((i) => i.type === "action");
      const target = acts[+e.key - 1];
      if (target) { e.preventDefault(); e.stopPropagation(); runAction(target.ref, true); }
      return;
    }
    // any other printable key types into the question: the input never stops working
    if (e.key.length === 1 && !e.ctrlKey && !e.altKey && !e.metaKey && e.key !== " ") { e.preventDefault(); Q.setInput(Q.getInput() + e.key); }
  }
  /** The buttons' global combos are released while a combo is captured (Windows would take the press first). */
  function suspendHotkeys(on) {
    if (!(S.data && S.data.actions || []).some((a) => a.global)) return;
    Q.api("/api/quick/canvas/hotkeys", { body: { suspend: on } }).catch(() => {});
  }
  function comboOf(e) {
    const key = /^Key[A-Z]$/.test(e.code) ? e.code.slice(3) : /^Digit[0-9]$/.test(e.code) ? e.code.slice(5) : /^F([1-9]|1[0-9]|2[0-4])$/.test(e.key) ? e.key : "";
    if (!key) return "";
    return [e.ctrlKey || e.metaKey ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", key].filter(Boolean).join("+");
  }
  function onDocKey(e) {
    if (!S.data || !settings().enabled || e.defaultPrevented) return;
    if (S.editor && S.editor.capturing) {
      if (e.key === "Escape") { S.editor.capturing = false; suspendHotkeys(false); renderEditorPreserving(); e.preventDefault(); e.stopPropagation(); return; }
      if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
      e.preventDefault(); e.stopPropagation();
      const combo = comboOf(e);
      if (combo) { S.editor.action.hotkey = combo; S.editor.capturing = false; suspendHotkeys(false); renderEditorPreserving(); validateSoon(); }
      return;
    }
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey && e.code === "KeyB") { e.preventDefault(); toggle(); return; }
    if (mod && !e.shiftKey && !e.altKey && e.code === "KeyE" && S.screen && S.view !== "editor") { e.preventDefault(); arrange(); return; }
    if (mod && !e.shiftKey && !e.altKey && e.code === "KeyE" && S.open && S.view === "grid") { e.preventDefault(); setEdit(!S.edit); return; }
    if (e.repeat || settings().actions === false) return;
    if (e.getModifierState && e.getModifierState("AltGraph")) return;   // AltGr (= Ctrl+Alt) types characters on many layouts
    const combo = comboOf(e);
    if (!combo || (!mod && !e.altKey && !/^F\d/.test(combo.split("+").pop()))) return;
    if (S.view === "editor") return;                    // typing in the editor never fires a button
    for (const a of (S.data.actions || [])) {
      if (a.hotkey && a.hotkey === combo) { e.preventDefault(); e.stopPropagation(); runAction(a.id, true); return; }
    }
  }

  /* ------------------------------------------------------------ pointer: click, long press, drag, resize */
  function gridPoint(e) {
    const grid = el.querySelector(".qcv-grid");
    const r = grid.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top, grid };
  }
  function onPointerDown(e) {
    const hit = e.target.closest && e.target.closest(".qcv-item");
    S.down = hit ? { id: hit.dataset.item, at: performance.now() } : null;
    if (e.button !== 0 || S.view !== "grid") return;
    const node = e.target.closest(".qcv-item");
    if (!node || e.target.closest("[data-item-act='remove'], [data-item-act='settings']")) return;
    const resize = !!e.target.closest("[data-item-act='resize']");
    if (!S.edit && !resize) {
      // long press → edit mode and drag (buttons only; panels have their own controls)
      if (e.target.closest(".mp-card button, .mp-card input, .mp-card textarea, .mp-card a, .mp-card label")) return;
      S.press = { node, id: node.dataset.item, x: e.clientX, y: e.clientY, timer: setTimeout(() => {
        S.press = null; S.longPress = true;          // the release of a long press is not a click
        setEdit(true); startDrag(node.dataset.item, e, false);
      }, 480) };
      return;
    }
    if (S.edit && e.target.closest(".mp-card input, .mp-card textarea")) return;
    e.preventDefault();
    startDrag(node.dataset.item, e, resize);
  }
  function startDrag(id, e, resize) {
    const it = S.display.find((i) => i.id === id) || S.layout.find((i) => i.id === id);
    const node = el.querySelector(`.qcv-item[data-item="${CSS.escape(id)}"]`);
    if (!it || !node) return;
    const p = gridPoint(e);
    const r = px(it);
    S.drag = { id, resize, start: { x: p.x, y: p.y }, offset: { x: p.x - r.left, y: p.y - r.top }, origin: { ...it }, layout: S.display.map((i) => ({ ...i })), moved: false, target: { x: it.x, y: it.y, w: it.w, h: it.h } };
    node.classList.add(resize ? "qcv-resizing" : "qcv-dragging");
    S.focusId = id;
    node.focus({ preventScroll: true });
    try { node.setPointerCapture(e.pointerId); } catch (err) { /* synthetic */ }
    let ph = p.grid.querySelector(".qcv-ph");
    if (!ph) { ph = document.createElement("div"); ph.className = "qcv-ph"; p.grid.appendChild(ph); }
    placeholder(S.drag.target);
  }
  function placeholder(tg) {
    const ph = el.querySelector(".qcv-ph");
    if (!ph) return;
    const r = px(tg);
    ph.style.cssText = `left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px`;
  }
  function onPointerMove(e) {
    if (S.press) {
      // Round 29: hold the left button and move (5 px) — the item follows at once, no arrange mode needed
      if (Math.abs(e.clientX - S.press.x) + Math.abs(e.clientY - S.press.y) >= 5) {
        const press = S.press;
        clearTimeout(press.timer); S.press = null;
        closeItemMenu();
        startDrag(press.id, e, false);
      }
      if (!S.drag) return;
    }
    const d = S.drag;
    if (!d) return;
    const p = gridPoint(e);
    const node = el.querySelector(`.qcv-item[data-item="${CSS.escape(d.id)}"]`);
    if (!node) return;
    if (Math.abs(p.x - d.start.x) + Math.abs(p.y - d.start.y) > 3) d.moved = true;
    const stepX = S.cell + GAP, stepY = S.rowH + GAP;
    let tg;
    if (d.resize) {
      const o = d.origin;
      if (o.type === "action") {
        const big = (p.x - o.x * stepX) > stepX * 1.5 || (p.y - o.y * stepY) > stepY * 1.5;
        tg = { x: Math.min(o.x, S.cols - (big ? 2 : 1)), y: o.y, w: big ? 2 : 1, h: big ? 2 : 1 };
      } else {
        const w = Math.max(2, Math.min(S.cols - o.x, Math.round((p.x - o.x * stepX + GAP) / stepX)));
        const h = Math.max(2, Math.min(8, Math.round((p.y - o.y * stepY + GAP) / stepY)));
        tg = { x: o.x, y: o.y, w, h };
      }
      const r = px(tg);
      node.style.width = r.width + "px"; node.style.height = r.height + "px";
    } else {
      const left = p.x - d.offset.x, top = p.y - d.offset.y;
      node.style.left = left + "px"; node.style.top = top + "px";
      tg = { x: Math.max(0, Math.min(S.cols - d.origin.w, Math.round(left / stepX))), y: Math.max(0, Math.round(top / stepY)), w: d.origin.w, h: d.origin.h };
    }
    if (tg.x === d.target.x && tg.y === d.target.y && tg.w === d.target.w && tg.h === d.target.h) return;
    d.target = tg;
    placeholder(tg);
    // live reflow of the others (animated by CSS transitions on left/top)
    const next = placeAt(d.layout, d.origin, tg.x, tg.y, tg.w, tg.h);
    for (const it of next) {
      if (it.id === d.id) continue;
      const n2 = el.querySelector(`.qcv-item[data-item="${CSS.escape(it.id)}"]`);
      if (n2) { const r = px(it); n2.style.left = r.left + "px"; n2.style.top = r.top + "px"; }
    }
    const rows = next.reduce((m, i) => Math.max(m, i.y + i.h), 0);
    p.grid.style.height = (rows * stepY - GAP) + "px";
  }
  function onPointerUp() {
    if (S.press) { clearTimeout(S.press.timer); S.press = null; return; }
    const d = S.drag;
    if (!d) return;
    S.drag = null;
    const node = el.querySelector(`.qcv-item[data-item="${CSS.escape(d.id)}"]`);
    if (node) node.classList.remove("qcv-dragging", "qcv-resizing");
    const ph = el.querySelector(".qcv-ph");
    if (ph) ph.remove();
    if (d.moved || S.longPress) S.suppressClickUntil = performance.now() + 250;
    S.longPress = false;
    if (d.moved) {
      // what the user sees becomes the stored layout (in a narrow window — the derived one); items that are not
      // shown (a panel MemuAI does not have right now) keep their order below everything
      let next = placeAt(d.layout, d.origin, d.target.x, d.target.y, d.target.w, d.target.h);
      if (S.cols < COLS) next = keepStoredSizes(next, d.id);
      const bottom = next.reduce((m, i) => Math.max(m, i.y + i.h), 0);
      const hidden = S.layout.filter((i) => !next.some((n) => n.id === i.id)).map((i) => ({ ...i, y: bottom + i.y }));
      S.layout = next.concat(hidden);
      saveLayout();
    }
    render();
  }

  /* ------------------------------------------------------------ clicks */
  function onClick(e) {
    if (S.suppressClickUntil && performance.now() < S.suppressClickUntil) { e.preventDefault(); e.stopPropagation(); return; }
    const t0 = e.target;
    if (menuEl && !t0.closest(".qcv-menu")) closeItemMenu();
    const q = t0.closest("[data-qcv]");
    if (q) {
      const k = q.dataset.qcv;
      if (k === "toggle") return toggle();
      if (k === "layer-toggle") return layerCommand("toggle");
      if (k === "arrange") return arrange();
      if (k === "manage") return setOpen(!S.open);
      if (k === "close") return setOpen(false);
      if (k === "edit") return setEdit(!S.edit);
      if (k === "add") { S.view = "add"; return render(); }
      if (k === "back") { S.view = "grid"; S.editor = null; S.approve = null; return render(); }
      if (k === "new-button") return openEditor(null);
      if (k === "templates") { S.view = "add"; S.addTab = "buttons"; return render(); }
      if (k === "add-panels") { S.view = "add"; S.addTab = "panels"; return render(); }
      if (k === "approve") { const ap = S.approve; S.view = "grid"; S.approve = null; render(); if (ap) runAction(ap.id, true, true); return; }
      if (k === "export") return exportSet();
      if (k === "import") return importSet();
      if (k === "memuai-panels") { Q.api("/api/quick/window", { body: { action: "handoff", handoff: { open: "panels", settings: "panels" } } }).catch(() => {}); return; }
      if (k === "ed-save") return saveEditor();
      if (k === "ed-dup") { const id = S.editor.id; S.editor = null; S.view = "grid"; duplicate(id); return; }
      if (k === "ed-del") { const it = S.layout.find((i) => i.type === "action" && i.ref === S.editor.id); S.editor = null; S.view = "grid"; if (it) removeItem(it.id); else render(); return; }
      if (k === "hk-clear") { S.editor.action.hotkey = ""; S.editor.action.global = false; renderEditorPreserving(); validateSoon(); return; }
      if (k === "app-icon") return pickAppIcon();
      if (k === "pick-image") { const f = el.querySelector('[data-f="image"]'); if (f) f.click(); return; }
    }
    const noteBtn = t0.closest("[data-note-act]");
    if (noteBtn) return noteAction(noteBtn.closest("[data-note]").dataset.note, noteBtn.dataset.noteAct, noteBtn);
    const tab = t0.closest("[data-tab]");
    if (tab) { S.addTab = tab.dataset.tab; return render(); }
    const tpl = t0.closest("[data-tpl]");
    if (tpl && S.catalog) return openEditor(null, S.catalog.templates[+tpl.dataset.tpl]);
    const addP = t0.closest("[data-add-panel]");
    if (addP) return addPanel(addP.dataset.addPanel, addP.dataset.size);
    if (S.view === "editor" && S.editor) {
      const ed = S.editor, a = ed.action;
      const ib = t0.closest("[data-icon]"); if (ib) { a.icon = { kind: "builtin", value: ib.dataset.icon }; renderEditorPreserving(); return; }
      const eb = t0.closest("[data-emoji]"); if (eb) { a.icon = { kind: "emoji", value: eb.dataset.emoji }; renderEditorPreserving(); return; }
      const it = t0.closest("[data-icon-tab]"); if (it) { ed.iconTab = it.dataset.iconTab; renderEditorPreserving(); return; }
      const cb = t0.closest("[data-color]"); if (cb) { a.color = cb.dataset.color; if (!a.color) a.color2 = ""; renderEditorPreserving(); return; }
      const sh = t0.closest("[data-shape-set]"); if (sh) { a.shape = sh.dataset.shapeSet; renderEditorPreserving(); return; }
      const sz = t0.closest("[data-size-set]"); if (sz) { ed.size = sz.dataset.sizeSet; renderEditorPreserving(); return; }
      const as = t0.closest("[data-add-step]");
      if (as) { if (a.steps.length >= 30) return; a.steps.push(JSON.parse(JSON.stringify(DEFAULT_STEP[as.dataset.addStep]))); renderEditorPreserving(); validateSoon(); const last = el.querySelector(".qcv-step:last-of-type"); if (last) last.scrollIntoView({ block: "nearest" }); return; }
      const sa = t0.closest("[data-step-act]");
      if (sa) {
        const i = +sa.closest("[data-step]").dataset.step;
        if (sa.dataset.stepAct === "remove") a.steps.splice(i, 1);
        if (sa.dataset.stepAct === "up" && i > 0) [a.steps[i - 1], a.steps[i]] = [a.steps[i], a.steps[i - 1]];
        if (sa.dataset.stepAct === "down" && i < a.steps.length - 1) [a.steps[i + 1], a.steps[i]] = [a.steps[i], a.steps[i + 1]];
        renderEditorPreserving(); validateSoon(); return;
      }
      const v = t0.closest("[data-var]");
      if (v && lastVarField && document.contains(lastVarField)) {
        const f = lastVarField, s = f.selectionStart || f.value.length;
        f.value = f.value.slice(0, s) + v.dataset.var + f.value.slice(f.selectionEnd || s);
        f.focus(); f.setSelectionRange(s + v.dataset.var.length, s + v.dataset.var.length);
        editorInput(f, false); return;
      }
      if (t0.matches('[data-f="hotkey"]')) { ed.capturing = true; suspendHotkeys(true); renderEditorPreserving(); return; }
    }
    const act = t0.closest("[data-item-act]");
    if (act) {
      const id = act.closest(".qcv-item").dataset.item;
      if (act.dataset.itemAct === "remove") return removeItem(id);
      if (act.dataset.itemAct === "settings") return openItemMenu(id, act);
      return;
    }
    const chip = t0.closest("[data-qcv-chip]");
    if (chip) { const id = chip.dataset.qcvChip; setOpen(true); requestAnimationFrame(() => focusItem(id)); return; }
    const run = t0.closest("[data-run]");
    if (run && !S.edit) { runAction(run.dataset.run); return; }
    const item = t0.closest(".qcv-item");
    if (item && S.edit && item.dataset.type === "action") { const it = S.layout.find((i) => i.id === item.dataset.item); const a = it && actionsById().get(it.ref); if (a) openEditor(a); }
  }

  /* ------------------------------------------------------------ wiring */
  function bind() {
    el.addEventListener("click", onClick);
    el.addEventListener("keydown", onHostKey);
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("pointermove", onPointerMove);
    el.addEventListener("pointerup", onPointerUp);
    el.addEventListener("pointercancel", onPointerUp);
    el.addEventListener("contextmenu", (e) => {
      // Round 29: a live card may have re-rendered under the pointer (detached target) — use the press / the point
      let node = e.target.isConnected ? e.target.closest(".qcv-item") : null;
      if (!node && S.down && performance.now() - S.down.at < 3000) node = el.querySelector(`.qcv-item[data-item="${CSS.escape(S.down.id)}"]`);
      if (!node) { const under = document.elementFromPoint(e.clientX, e.clientY); node = under && under.closest ? under.closest(".qcv-item") : null; }
      if (!node || (e.target.isConnected && e.target.closest("a[href], input, textarea"))) return;
      e.preventDefault();
      openItemMenu(node.dataset.item, node, { x: e.clientX, y: e.clientY });
    });
    // text fields as you type; selects, checkboxes, files and colours on «change» (re-rendering under an open colour picker closes it)
    el.addEventListener("input", (e) => { if (S.view === "editor" && e.target.dataset.f && !["checkbox", "color", "file"].includes(e.target.type) && e.target.tagName !== "SELECT") editorInput(e.target, false); });
    el.addEventListener("change", (e) => { if (S.view === "editor" && e.target.dataset.f && (e.target.tagName === "SELECT" || e.target.type === "checkbox" || e.target.type === "file" || e.target.type === "color")) editorInput(e.target, false); });
    el.addEventListener("focusin", (e) => {
      if (e.target.matches && e.target.matches("[data-f]") && /^(INPUT|TEXTAREA)$/.test(e.target.tagName) && e.target.type !== "color" && !["hotkey", "appicon", "emoji"].includes(e.target.dataset.f)) lastVarField = e.target;
      const item = e.target.closest && e.target.closest(".qcv-item");
      if (item) S.focusId = item.dataset.item;
    });
    document.addEventListener("keydown", onDocKey);
    document.addEventListener("keydown", onCaptureKey, true);
    // grid columns follow the window width
    if (typeof ResizeObserver === "function") {
      let raf = 0, lastW = 0;
      new ResizeObserver(() => {
        const w = el.clientWidth;
        if (Math.abs(w - lastW) < 2) return;
        lastW = w;
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => { if (S.open && S.view === "grid" && !S.drag && !(S.screen && visibleItems().length)) { renderGrid(el.querySelector(".qcv-view")); Q.resize(); } });
      }).observe(el);
    }
    // panels.js editor modal: the window grows to its maximum height while the modal is open
    new MutationObserver(() => {
      const modal = document.querySelector(".mp-modal-back");
      if (modal) root.setAttribute("data-qcv-modal", ""); else root.removeAttribute("data-qcv-modal");
      Q.resize();
    }).observe(document.body, { childList: true });
  }

  Q.on("open", async (ev) => {
    S.shownAt = performance.now();
    S.inChat = !!(Q.state().turns || []).length;
    if (ev && ev.fresh) { S.view = "grid"; S.editor = null; S.approve = null; S.edit = false; }
    const first = !S.data;
    await load();
    if (!S.data) return;
    const st = settings();
    if (first || (ev && ev.fresh)) {
      const mode = st.open_mode || "input";
      // screen mode: the items are on the screen; the window body opens only for Add / the editor / approvals
      S.open = !S.screen && st.enabled && (mode === "canvas" || (mode === "last" && !!(S.data.ui && S.data.ui.open)));
    }
    render();
    // panels only after the window is on screen (and only when they are needed)
    if (st.enabled && st.panels && ((S.open && !S.screen) || visibleItems().some((i) => i.type === "panel" && i.pinned))) {
      setTimeout(() => ensurePanels().then(() => { renderPanels(); renderStrip(); }), 60);
    }
  });
  // hidden window: hide the cards too, so panels.js stops its live polling (PC load, timers) until the next open
  Q.on("close", () => { closeItemMenu(); S.edit = false; if (S.editor && S.editor.capturing) { S.editor.capturing = false; suspendHotkeys(false); } Q.hidePanel("canvas"); });
  Q.on("answer-start", () => {
    S.inChat = true;
    if (S.open && settings().collapse_on_answer !== false) { S.open = false; S.edit = false; S.view = "grid"; }
    render();
  });
  Q.on("chat-new", () => { S.inChat = false; render(); });
  Q.on("chat-open", () => { S.inChat = true; render(); });
  // Round 30: the footer button (next to the mic) — shows/hides the panels; pressed while they are shown
  panelsButton = typeof Q.addComposerButton === "function" ? Q.addComposerButton({
    id: "canvas", order: 4, icon: UI.grid, menu: false,
    label: () => t("Панели", "Panels"),
    title: () => (S.screen ? t("Панели и кнопки на экране — показать/скрыть (Ctrl+B)", "Panels and buttons on the screen — show/hide (Ctrl+B)")
      : S.open ? t("Свернуть панели (Ctrl+B)", "Fold the panels (Ctrl+B)") : t("Панели и быстрые действия (Ctrl+B)", "Panels and quick actions (Ctrl+B)")),
    pressed: () => (S.screen ? !!S.layer.visible : !!S.open),
    hidden: () => !S.data || !settings().enabled || !(settings().panels || settings().actions),
    onClick: () => toggle(),
  }) : null;
  Q.addAction({
    id: "canvas-toggle", section: () => t("Панели и действия", "Panels & actions"), icon: UI.grid, keys: ["Ctrl", "B"],
    label: () => (S.screen ? t("Панели и кнопки на экране — показать/скрыть", "Panels and buttons on the screen — show/hide") : t("Панели и быстрые действия", "Panels and quick actions")),
    hidden: () => !S.data || !settings().enabled,
    run: () => toggle(),
  });
  Q.addAction({
    id: "canvas-arrange", section: () => t("Панели и действия", "Panels & actions"), icon: UI.grid, keys: ["Ctrl", "E"],
    label: () => t("Расставить панели и кнопки на экране", "Arrange the panels and buttons on the screen"),
    hidden: () => !S.data || !settings().enabled || !S.screen,
    run: () => arrange(),
  });
  Q.addAction({
    id: "canvas-manage", section: () => t("Панели и действия", "Panels & actions"), icon: UI.gear,
    label: () => t("Добавить и настроить панели", "Add and set up panels"),
    hidden: () => !S.data || !settings().enabled || !S.screen,
    run: () => setOpen(true),
  });
  Q.addAction({
    id: "canvas-new-button", section: () => t("Панели и действия", "Panels & actions"), icon: UI.plus,
    label: () => t("Новая быстрая кнопка", "New quick button"),
    hidden: () => !S.data || !settings().enabled || settings().actions === false,
    run: () => openEditor(null),
  });

  /* ------------------------------------------------------------ Round 28: messages from the screen layer */
  function onBus() {
    const B = bus();
    if (!B) return;
    B.on("run", (d) => { if (d && d.id) runAction(String(d.id), true); });            // the layer's own 300 ms guard ran already
    B.on("edit-button", (d) => { const a = d && actionsById().get(String(d.id)); if (a) openEditor(a); });
    B.on("new-button", () => openEditor(null));
    B.on("add", () => { S.view = "add"; setOpen(true, { save: false }); });
    B.on("changed", () => { load().then(render); });
    B.on("ask", (d) => { if (d && d.prompt) { try { Q.send(String(d.prompt)); } catch (e) { Q.toast(e.message, "error"); } } });
    B.on("layer-state", (d) => { if (d) { S.layer = { ...S.layer, visible: !!d.visible, edit: !!d.edit, shown: d.shown, hidden: d.hidden }; render(); } });
  }
  /** Pushed by app/quick_ask.py after it showed / hid the layer for this opening. */
  function layerState(st) {
    if (!st) return;
    S.layer = { ...S.layer, ...st };
    if (st.available === false) S.screen = false;
    render();
  }

  iconsReady.then(() => { bind(); onBus(); load().then(render); });
  // test hooks (tests/round27_quick_canvas_shots.py)
  window.MemuQuickCanvas = { state: () => S, load, render, setOpen, setEdit, openEditor, runAction, derive, placeAt, visibleItems, ensurePanels, layerState, toggle };
})();
