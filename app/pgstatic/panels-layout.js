/* MemuAI · Панели — места на окне и режим «Изменить расположение» (Round 33, r33-panels).
 *
 * Before Round 33 a panel could live in three places (the grid under the message box, the sidebar, the top bar)
 * and the home grid scrolled with the native thin scrollbar (arrows and a grey track on Windows). This file adds:
 *
 *   • five more zones (app/panels_layout.py is the source of truth for the ids):
 *       top            — a strip above the greeting of the start screen;
 *       composer       — a strip right above the message box;
 *       left / right   — columns beside the chat area (fold into a rail of chips when the window is narrow);
 *       sidebar_bottom — docked at the bottom of the sidebar, above the profile card;
 *     each with its own look (cards | chips) and, for the start-screen zones, «also in chats»;
 *   • soft scrollbars: the panel areas hide the native bar; a thin overlay thumb shows on hover / while scrolling
 *     (draggable), the edges fade, a strip scrolls sideways with the mouse wheel;
 *   • «Edit layout» mode: every zone is shown as a labelled drop target with «+» (gallery for that zone), cards get
 *     a small toolbar (size presets S·M·L·W, settings, «remove from here»), a corner grip that snaps the width to
 *     the grid columns, the side columns get a width handle; drag between zones with highlight and auto-scroll;
 *     Undo / Redo (Ctrl+Z / Ctrl+Shift+Z, Ctrl+Y), «Reset», Esc or «Done» exits; arrows move the focused card,
 *     Delete takes it out of this place;
 *   • the zone map — a small drawing of the window where places are toggled (panel form, «More places…»).
 *
 * Public API (window.MemuPanelsLayout, also for the Round 33 theme / layout editor):
 *   ZONES                       {id: {kind, views, chats, width, start}} (same as panels_layout.ZONES)
 *   zoneName(id) / zoneIcon(id) localized name / icon name
 *   zones()                     [{id, kind, name, view, chats, width, count, visible}] — the current state
 *   viewOf(id) / sized(id)      «cards»|«chips» / whether the panel size matters there
 *   setZone(id, patch)          change a zone's settings ({view, chats, width}) with undo; returns a promise
 *   movePanel(id, to, {from, index, copy}) · setSize(id, size) · togglePlace(id, zone)   (with undo)
 *   enter(focusId?) / exit() / editing() / toggle()   «Edit layout» mode
 *   undo() / redo() / reset() · remember() (push the current layout to the undo stack)
 *   placesField(selected[]) / zoneMapHtml(selected[], opts) / openPlaces(id, anchor)
 *   scrollify(el, {axis}) — the soft scrollbar for any scroller of the window
 *   on(fn) — called with {type: "enter"|"exit"|"change"} ; returns an unsubscribe function
 */
"use strict";
(() => {
  if (window.MEMU_PANELS_EMBED || window.MemuPanelsLayout) return;
  const P = () => (window.MemuPanels && window.MemuPanels._lay) || null;
  const $q = (sel, root = document) => root.querySelector(sel);
  const $$q = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const t = (ru, en) => (P() ? P().t(ru, en) : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ico = (name, cls) => (P() ? P().ico(name, cls) : "");

  const ZONES = {
    appbar: { kind: "bar", views: ["chips"], chats: false, width: false, start: false },
    top: { kind: "strip", views: ["cards", "chips"], chats: true, width: false, start: true },
    composer: { kind: "strip", views: ["chips", "cards"], chats: true, width: false, start: true },
    home: { kind: "grid", views: ["cards"], chats: false, width: false, start: true },
    left: { kind: "column", views: ["cards", "chips"], chats: true, width: true, start: true },
    right: { kind: "column", views: ["cards", "chips"], chats: true, width: true, start: true },
    sidebar: { kind: "stack", views: ["cards", "chips"], chats: false, width: false, start: false },
    sidebar_bottom: { kind: "stack", views: ["chips", "cards"], chats: false, width: false, start: false },
  };
  const ZONE_IDS = Object.keys(ZONES);
  const OWN = ["top", "composer", "left", "right", "sidebar_bottom"];      // zones whose mounts this file owns
  const WIDTH_MIN = 200, WIDTH_MAX = 480, DEFAULT_WIDTH = { left: 280, right: 300 };
  const CHAT_MIN = 560;          // the conversation never gets narrower than this because of the columns
  const RAIL_W = 60;
  const STRIP_W = { s: 196, m: 268, l: 340, w: 440 };
  const SIZES = ["s", "m", "l", "w"];
  const zoneName = (id) => (P() ? P().placeName(id) : id);
  const zoneIcon = (id) => (P() ? P().placeIcon(id) : "dot");
  const ZONE_HINTS = () => ({
    appbar: t("Значения прямо в верхней панели, щелчок открывает карточку", "Values right in the top bar; a click opens the card"),
    top: t("Лента карточек над приветствием стартового экрана", "A strip of cards above the greeting of the start screen"),
    composer: t("Ряд прямо над полем ввода — удобно для значков", "A row right above the message box — good for chips"),
    home: t("Сетка под полем ввода; размер S·M·L·W задаёт ширину", "The grid below the message box; S·M·L·W sets the width"),
    left: t("Колонка слева от чата; в узком окне — полоса значков", "A column left of the chat; a rail of chips in a narrow window"),
    right: t("Колонка справа от чата; в узком окне — полоса значков", "A column right of the chat; a rail of chips in a narrow window"),
    sidebar: t("Под разделами боковой панели", "Under the sections of the sidebar"),
    sidebar_bottom: t("Прикреплено к низу боковой панели, над профилем", "Docked at the bottom of the sidebar, above the profile"),
  });

  const lay = {
    editing: false, undo: [], redo: [], listeners: new Set(), hover: null, resize: null, pop: null, saving: 0,
    fold: { left: "", right: "" }, lastChange: 0, scrollers: new Set(),
  };

  /* --------------------------------------------------------- settings */
  function zoneCfg(id) {
    const z = ZONES[id];
    const raw = (P()?.state.settings?.layout?.zones || {})[id] || {};
    const out = { view: z.views.includes(raw.view) ? raw.view : z.views[0] };
    if (z.chats) out.chats = raw.chats === true;
    if (z.width) out.width = Math.max(WIDTH_MIN, Math.min(WIDTH_MAX, +raw.width || DEFAULT_WIDTH[id]));
    return out;
  }
  const viewOf = (id) => (ZONES[id] ? zoneCfg(id).view : "cards");
  /** The panel size matters in the grid, the strips and the columns when they show cards. */
  const sized = (id) => !!ZONES[id] && ["grid", "strip", "column"].includes(ZONES[id].kind) && viewOf(id) === "cards";
  const placed = (id) => (P() ? P().placed(id) : []);
  function emit(type, extra = {}) { lay.listeners.forEach((fn) => { try { fn({ type, ...extra }); } catch (e) { console.warn("[MemuPanelsLayout]", e); } }); }

  /* ------------------------------------------------------------ mounts */
  function wrapHtml(id) {
    return `<div class="mp-zone-head" data-zone-head="${id}"></div><div class="mp-zone-body mp-drop-zone" data-mp-zone="${id}"></div>`;
  }
  function mount(id, tag, cls, place) {
    let el = document.getElementById("panels-z-" + id);
    if (el && el.isConnected) return el;
    el = document.createElement(tag);
    el.id = "panels-z-" + id;
    el.className = "mp-zone-wrap " + cls;
    el.dataset.zoneWrap = id;
    el.setAttribute("aria-label", zoneName(id));
    el.innerHTML = wrapHtml(id);
    if (!place(el)) return null;
    return el;
  }
  function ensureMounts() {
    const wrap = $q("#chat-view-wrap");
    if (wrap) {
      mount("top", "section", "mp-zone-strip", (el) => { const chat = $q("#chat", wrap); if (!chat || chat.parentElement !== wrap) return false; wrap.insertBefore(el, chat); return true; });
      mount("composer", "section", "mp-zone-strip", (el) => { const c = $q(":scope > .composer-wrap", wrap); if (!c) return false; wrap.insertBefore(el, c); return true; });
      const main = wrap.parentElement;
      if (main) {
        mount("left", "aside", "mp-zone-col", (el) => { main.insertBefore(el, wrap); return true; });
        mount("right", "aside", "mp-zone-col", (el) => { wrap.after(el); return true; });
      }
    }
    const bottom = $q(".side-bottom");
    if (bottom) mount("sidebar_bottom", "section", "mp-zone-sbottom", (el) => { bottom.before(el); return true; });
  }

  /* ------------------------------------------------------------ render */
  function zoneEmptyHtml(id) {
    return `<div class="mp-zone-end" aria-hidden="true"><span>${esc(t("Перетащите панель сюда", "Drop a panel here"))}</span></div>`;
  }
  function itemsHtml(id, list, view) {
    const L = P();
    return list.map((p, i) => {
      const enter = L.isNew(id, p.id);
      if (view === "chips") return L.chipHtml(p, { enter, index: i, place: id });
      const zsize = p.size || "m";
      const mode = id === "sidebar_bottom" ? "sidebar" : "home";
      const size = mode === "sidebar" ? "s" : (zsize === "w" ? "l" : zsize);
      return L.cardHtml(p, mode, { enter, index: i, place: id, size, zsize });
    }).join("");
  }
  function headHtml(id) {
    const z = ZONES[id], cfg = zoneCfg(id);
    const views = z.views.length > 1 ? `<div class="mp-zone-seg" role="group" aria-label="${esc(t("Вид", "Look"))}">${["cards", "chips"].filter((v) => z.views.includes(v)).map((v) =>
      `<button type="button" data-zone-view="${v}" aria-pressed="${cfg.view === v}" title="${esc(v === "cards" ? t("Карточки целиком", "Full cards") : t("Значки в одну строку, щелчок открывает карточку", "One-line chips; a click opens the card"))}">${esc(v === "cards" ? t("Карточки", "Cards") : t("Значки", "Chips"))}</button>`).join("")}</div>` : "";
    const chats = z.chats ? `<button type="button" class="mp-zone-tog" data-zone-chats aria-pressed="${!!cfg.chats}" title="${esc(t("Показывать это место и внутри чатов, а не только на стартовом экране", "Show this place inside chats too, not only on the start screen"))}"><i class="mp-tbox" aria-hidden="true">${cfg.chats ? ico("check") : ""}</i>${esc(t("И в чатах", "In chats too"))}</button>` : "";
    return `<span class="mp-zone-name">${ico(zoneIcon(id))}<b>${esc(zoneName(id))}</b></span><span class="grow"></span>${views}${chats}
      <button type="button" class="mp-zone-add" data-zone-add="${id}" title="${esc(t("Добавить панель сюда", "Add a panel here"))}" aria-label="${esc(t("Добавить панель сюда", "Add a panel here"))}">${ico("plus")}</button>`;
  }
  function renderZones() {
    const L = P();
    if (!L) return;
    ensureMounts();
    measureFold();
    OWN.forEach((id) => {
      const wrap = document.getElementById("panels-z-" + id);
      if (!wrap) return;
      const cfg = zoneCfg(id), z = ZONES[id];
      const list = placed(id);
      const fold = z.kind === "column" ? lay.fold[id] : "";
      const view = fold === "rail" ? "chips" : cfg.view;
      wrap.dataset.count = String(list.length);
      wrap.dataset.view = view;
      wrap.dataset.fold = fold;
      wrap.dataset.chats = cfg.chats ? "1" : "0";
      wrap.dataset.zoneLabel = zoneName(id);
      if (cfg.width) wrap.style.setProperty("--mp-col-w", ((lay.colW || {})[id] || cfg.width) + "px");
      const body = $q(".mp-zone-body", wrap);
      if (!body) { wrap.innerHTML = wrapHtml(id); }
      const box = $q(".mp-zone-body", wrap);
      box.dataset.view = view;
      box.dataset.axis = z.kind === "strip" ? "x" : "y";
      box.dataset.flow = view === "chips" && z.kind !== "strip" && fold !== "rail" ? "wrap" : "";
      box.dataset.ph = view === "chips" ? "chip" : z.kind === "strip" ? "strip" : "stack";
      box.setAttribute("aria-label", zoneName(id));
      box.innerHTML = itemsHtml(id, list, view) + zoneEmptyHtml(id);
      const head = $q(".mp-zone-head", wrap);
      head.innerHTML = lay.editing ? headHtml(id) : "";
      if (z.kind === "column") {
        let grip = $q(":scope > .mp-col-grip", wrap);
        if (!grip) {
          grip = document.createElement("div");
          grip.className = "mp-col-grip";
          grip.setAttribute("role", "separator");
          grip.setAttribute("aria-orientation", "vertical");
          grip.tabIndex = 0;
          wrap.appendChild(grip);
        }
        grip.title = t("Ширина колонки — тяните; двойной щелчок — по умолчанию", "Column width — drag; double-click resets");
        grip.setAttribute("aria-label", t("Ширина колонки", "Column width"));
        grip.setAttribute("aria-valuenow", String(cfg.width));
        grip.setAttribute("aria-valuemin", String(WIDTH_MIN));
        grip.setAttribute("aria-valuemax", String(WIDTH_MAX));
      }
      scrollify(box, { axis: z.kind === "strip" ? "x" : "y" });
    });
    // the classic three: labels and heads for «Edit layout»
    const cvw = $q("#chat-view-wrap");
    if (cvw) scrollify(cvw, { axis: "y", soft: true });     // the start screen itself (Focus design, «Edit layout»)
    const home = $q("#panels-home");
    if (home) { home.dataset.zoneLabel = zoneName("home"); home.dataset.zoneWrap = "home"; scrollify(home, { axis: "y" }); keepHomeMin(); }
    const side = $q("#group-panels");
    if (side) { side.dataset.zoneLabel = zoneName("sidebar"); side.dataset.zoneWrap = "sidebar"; if (lay.editing) side.hidden = false; }
    const bar = $q("#panels-appbar");
    if (bar) { bar.dataset.zoneLabel = zoneName("appbar"); }
    if (lay.editing) decorateClassic();
    placeChrome();
  }
  /** The grid under the message box never shrinks to nothing (before Round 33 it got 0 px in a low window and could
   *  not be reached): it keeps its head and a height floor; the start screen scrolls for the rest. */
  function keepHomeMin() {
    const home = $q("#panels-home");
    if (!home) return;
    home.style.minHeight = "";
    if (home.hidden || !home.getClientRects().length) return;
    const head = $q(".panels-home-head", home);
    // a low window keeps the message box whole first: the floor grows with the height (56 px at 650, ~250 at 1080)
    const floor = Math.max(56, Math.min(innerHeight * 0.28, (innerHeight - 560) * 0.6));
    const need = Math.min(home.scrollHeight, Math.round(floor) + (head ? head.offsetHeight : 0));
    if (need > 0) home.style.minHeight = need + "px";
  }
  /** In «Edit layout» the sidebar group and the home grid get the same zone head as the new zones. */
  function decorateClassic() {
    const side = $q("#group-panels");
    if (side && !$q(":scope > .mp-zone-head", side)) {
      const head = document.createElement("div");
      head.className = "mp-zone-head";
      side.insertBefore(head, $q(".mp-side-list", side));
    }
    if (side) $q(":scope > .mp-zone-head", side).innerHTML = headHtml("sidebar");
  }

  /* ----------------------------------------------- columns: fold to rail */
  function measureFold() {
    const main = $q("#main-content") || $q("#chat-view-wrap")?.parentElement;
    if (!main) return;
    const W = main.clientWidth - ($q("#code-explorer:not([hidden])", main)?.offsetWidth || 0);
    const cols = ["left", "right"].filter((id) => colShown(id));
    let need = CHAT_MIN;
    cols.forEach((id) => { need += zoneCfg(id).width; });
    const next = {};
    lay.colW = {};
    const SQUEEZE_MIN = 216;
    if (W >= need || !cols.length) cols.forEach((id) => { next[id] = ""; });
    else if (W - CHAT_MIN >= cols.length * SQUEEZE_MIN) {
      // a bit narrower than asked (never below 216 px) before folding into a rail: a 1366 px laptop keeps both columns
      const room = W - CHAT_MIN, asked = need - CHAT_MIN;
      cols.forEach((id) => { next[id] = ""; lay.colW[id] = Math.max(SQUEEZE_MIN, Math.floor(zoneCfg(id).width * room / asked)); });
    } else if (W >= CHAT_MIN - 120 + cols.length * RAIL_W) cols.forEach((id) => { next[id] = "rail"; });
    else cols.forEach((id) => { next[id] = "hide"; });
    ["left", "right"].forEach((id) => { lay.fold[id] = next[id] || ""; });
  }
  /** Would the column be on screen (start screen, or «in chats too» inside a chat) — CSS has the final word. */
  function colShown(id) {
    if (!placed(id).length && !lay.editing && !document.body.classList.contains("mp-dragging")) return false;
    const wrap = $q("#chat-view-wrap");
    if (!wrap) return false;
    return wrap.classList.contains("empty-chat") || zoneCfg(id).chats || lay.editing;
  }
  let foldRaf = 0;
  function refold() {
    cancelAnimationFrame(foldRaf);
    foldRaf = requestAnimationFrame(() => {
      const before = JSON.stringify([lay.fold, lay.colW]);
      measureFold();
      if (JSON.stringify([lay.fold, lay.colW]) !== before) P()?.render();
    });
  }

  /* ------------------------------------------------- soft scrollbars */
  /** The panel areas hide the native bar; a thin thumb overlays the edge on hover / while scrolling and can be
   *  dragged. Edges fade (mask) where there is more to scroll. A horizontal strip scrolls with the wheel. */
  function scrollify(el, { axis = "y", soft = false } = {}) {
    if (!el || el.dataset.mpScroll) { if (el) updateScroll(el); return; }
    el.dataset.mpScroll = axis;
    el.classList.add("mp-softscroll");
    if (soft) el.dataset.mpSoft = "1";
    const thumb = document.createElement("div");
    thumb.className = "mp-sthumb";
    thumb.dataset.axis = axis;
    thumb.setAttribute("aria-hidden", "true");
    document.body.appendChild(thumb);
    el._mpThumb = thumb;
    lay.scrollers.add(el);
    let hideTimer = 0;
    const show = (on) => {
      clearTimeout(hideTimer);
      thumb.classList.toggle("on", !!on);
      if (on && !thumb.classList.contains("drag")) hideTimer = setTimeout(() => { if (!el.matches(":hover") && !thumb.matches(":hover")) thumb.classList.remove("on"); }, 1100);
    };
    el.addEventListener("scroll", () => { updateScroll(el); show(true); }, { passive: true });
    el.addEventListener("pointerenter", () => { updateScroll(el); if (canScroll(el)) show(true); });
    el.addEventListener("pointerleave", () => show(false));
    thumb.addEventListener("pointerenter", () => show(true));
    thumb.addEventListener("pointerleave", () => show(false));
    if (axis === "x") {
      el.addEventListener("wheel", (event) => {
        if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return;
        if (el.scrollWidth <= el.clientWidth + 1) return;
        const max = el.scrollWidth - el.clientWidth;
        if ((event.deltaY < 0 && el.scrollLeft <= 0) || (event.deltaY > 0 && el.scrollLeft >= max - 1)) return;
        event.preventDefault();
        el.scrollLeft += event.deltaY * (event.deltaMode === 1 ? 32 : 1);
      }, { passive: false });
    }
    thumb.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const start = axis === "x" ? event.clientX : event.clientY;
      const from = axis === "x" ? el.scrollLeft : el.scrollTop;
      const track = axis === "x" ? el.clientWidth : el.clientHeight;
      const total = axis === "x" ? el.scrollWidth : el.scrollHeight;
      const ratio = total / Math.max(1, track);
      thumb.classList.add("drag", "on");
      try { thumb.setPointerCapture(event.pointerId); } catch (e) { /* ok */ }
      const move = (e) => {
        const d = ((axis === "x" ? e.clientX : e.clientY) - start) * ratio;
        if (axis === "x") el.scrollLeft = from + d; else el.scrollTop = from + d;
      };
      const up = () => { thumb.classList.remove("drag"); show(true); thumb.removeEventListener("pointermove", move); thumb.removeEventListener("pointerup", up); thumb.removeEventListener("pointercancel", up); };
      thumb.addEventListener("pointermove", move);
      thumb.addEventListener("pointerup", up);
      thumb.addEventListener("pointercancel", up);
    });
    if (typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(() => updateScroll(el));
      ro.observe(el);
      el._mpRO = ro;
    }
    updateScroll(el);
  }
  const canScroll = (el) => (el.dataset.mpScroll === "x" ? el.scrollWidth > el.clientWidth + 1 : el.scrollHeight > el.clientHeight + 1);
  function updateScroll(el) {
    const thumb = el._mpThumb;
    if (!thumb) return;
    if (!el.isConnected) { thumb.remove(); el._mpRO?.disconnect(); lay.scrollers.delete(el); return; }
    const axis = el.dataset.mpScroll;
    const r = el.getBoundingClientRect();
    const visible = r.width > 0 && r.height > 0 && el.getClientRects().length > 0;
    const scrollable = visible && canScroll(el);
    const pos = axis === "x" ? el.scrollLeft : el.scrollTop;
    const total = axis === "x" ? el.scrollWidth : el.scrollHeight;
    const track = axis === "x" ? el.clientWidth : el.clientHeight;
    el.dataset.fadeStart = scrollable && pos > 2 ? "1" : "0";
    el.dataset.fadeEnd = scrollable && pos < total - track - 2 ? "1" : "0";
    thumb.hidden = !scrollable;
    if (!scrollable) return;
    const inset = 4;
    const len = Math.max(28, (track - inset * 2) * (track / total));
    const off = inset + (track - inset * 2 - len) * (pos / Math.max(1, total - track));
    if (axis === "x") {
      thumb.style.left = (r.left + off) + "px"; thumb.style.width = len + "px";
      thumb.style.top = (r.bottom - 7) + "px"; thumb.style.height = "";
    } else {
      thumb.style.top = (r.top + off) + "px"; thumb.style.height = len + "px";
      thumb.style.left = (r.right - 7) + "px"; thumb.style.width = "";
    }
  }
  function updateAllScroll() { lay.scrollers.forEach((el) => updateScroll(el)); }

  /* ------------------------------------------------------- auto-scroll */
  let autoTimer = 0, autoState = null;
  function scrollableAt(x, y) {
    const out = [];
    let el = document.elementFromPoint(x, y);
    while (el && el !== document.body) {
      const cs = getComputedStyle(el);
      if (/(auto|scroll)/.test(cs.overflowY + cs.overflowX) && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1)) out.push(el);
      el = el.parentElement;
    }
    return out;
  }
  /** Called from dragover (fires every ~50 ms even when the pointer rests): near an edge of a scroller under the
   *  pointer, keep scrolling it, faster closer to the edge. */
  function autoScroll(event) {
    const x = event.clientX, y = event.clientY;
    const EDGE = 56;
    let target = null, dx = 0, dy = 0;
    for (const el of scrollableAt(x, y)) {
      const r = el.getBoundingClientRect();
      const sy = el.scrollHeight > el.clientHeight + 1, sx = el.scrollWidth > el.clientWidth + 1;
      const k = (d) => Math.ceil(((EDGE - d) / EDGE) * 18);
      if (sy && y - r.top < EDGE && el.scrollTop > 0) dy = -k(y - r.top);
      else if (sy && r.bottom - y < EDGE && el.scrollTop < el.scrollHeight - el.clientHeight) dy = k(r.bottom - y);
      if (sx && x - r.left < EDGE && el.scrollLeft > 0) dx = -k(x - r.left);
      else if (sx && r.right - x < EDGE && el.scrollLeft < el.scrollWidth - el.clientWidth) dx = k(r.right - x);
      if (dx || dy) { target = el; break; }
    }
    autoState = target ? { el: target, dx, dy, at: performance.now() } : null;
    if (autoState && !autoTimer) {
      const step = () => {
        if (!autoState || performance.now() - autoState.at > 160) { autoTimer = 0; autoState = null; return; }
        autoState.el.scrollBy(autoState.dx, autoState.dy);
        autoTimer = requestAnimationFrame(step);
      };
      autoTimer = requestAnimationFrame(step);
    }
  }

  /* ------------------------------------------------------- undo / save */
  function snapshot() {
    const L = P();
    return {
      items: L.state.panels.map((p) => ({ id: p.id, placements: [...(p.placements || [])], order: { ...(p.order || {}) }, size: p.size || "m" })),
      layout: JSON.parse(JSON.stringify(L.state.settings.layout || { zones: {} })),
    };
  }
  function remember() {
    if (!P()) return;
    lay.undo.push(snapshot());
    if (lay.undo.length > 60) lay.undo.shift();
    lay.redo = [];
    lay.lastChange = Date.now();
    updateBar();
  }
  function applyLocal(snap) {
    const L = P();
    const by = new Map(snap.items.map((it) => [it.id, it]));
    L.state.panels.forEach((p) => { const it = by.get(p.id); if (it) { p.placements = [...it.placements]; p.order = { ...it.order }; p.size = it.size; } });
    L.state.settings.layout = JSON.parse(JSON.stringify(snap.layout));
  }
  async function save(snap = snapshot()) {
    const L = P();
    lay.saving += 1;
    updateBar();
    try {
      await L.act(L.call("/api/pg/panels-layout", { method: "POST", body: { panels: snap.items, layout: snap.layout } }));
    } finally { lay.saving -= 1; updateBar(); emit("change"); }
  }
  async function undo() {
    if (!lay.undo.length || !P()) return;
    const prev = lay.undo.pop();
    lay.redo.push(snapshot());
    applyLocal(prev);
    P().render();
    P().notify(t("Отменено", "Undone"), "info");
    await save(prev);
  }
  async function redo() {
    if (!lay.redo.length || !P()) return;
    const next = lay.redo.pop();
    lay.undo.push(snapshot());
    applyLocal(next);
    P().render();
    await save(next);
  }
  /** One local change + save, with undo. */
  async function commit(fn) {
    const L = P();
    if (!L) return;
    remember();
    fn(L.state);
    L.render();
    await save();
  }
  function orderIn(zone) { return placed(zone).map((p) => p.id); }
  function setOrder(zone, ids) {
    const L = P();
    ids.forEach((pid, i) => { const p = L.byId(pid); if (p) p.order = { ...(p.order || {}), [zone]: i }; });
  }
  function movePanel(id, to, { from = "", index = -1, copy = false } = {}) {
    return commit(() => {
      const p = P().byId(id);
      if (!p || !ZONES[to]) return;
      const set = new Set(p.placements || []);
      if (from && from !== to && !copy) set.delete(from);
      set.add(to);
      p.placements = ZONE_IDS.filter((z) => set.has(z));
      const ids = orderIn(to).filter((x) => x !== id);
      ids.splice(index < 0 ? ids.length : Math.min(index, ids.length), 0, id);
      setOrder(to, ids);
    });
  }
  function setSize(id, size) {
    if (!SIZES.includes(size)) return Promise.resolve();
    const p = P()?.byId(id);
    if (!p || p.size === size) return Promise.resolve();
    return commit(() => { p.size = size; });
  }
  function togglePlace(id, zone) {
    return commit(() => {
      const p = P().byId(id);
      if (!p || !ZONES[zone]) return;
      const set = new Set(p.placements || []);
      if (set.has(zone)) set.delete(zone);
      else {
        set.add(zone);
        const ids = orderIn(zone).filter((x) => x !== id);
        ids.push(id);
        setOrder(zone, ids);
      }
      p.placements = ZONE_IDS.filter((z) => set.has(z));
    });
  }
  function setZone(id, patch) {
    if (!ZONES[id]) return Promise.resolve();
    return commit((state) => {
      const layout = state.settings.layout || (state.settings.layout = { v: 1, zones: {} });
      layout.zones = layout.zones || {};
      const cur = { ...zoneCfg(id), ...(patch || {}) };
      if (ZONES[id].width) cur.width = Math.max(WIDTH_MIN, Math.min(WIDTH_MAX, Math.round(+cur.width || DEFAULT_WIDTH[id])));
      if (!ZONES[id].views.includes(cur.view)) cur.view = ZONES[id].views[0];
      layout.zones[id] = cur;
    });
  }
  /** «Reset»: every panel back where Rounds 21–32 put them — the zones of Round 33 are emptied into the grid under
   *  the message box (the classic three stay), every zone gets its default look. Undo brings it all back. */
  function reset() {
    return commit((state) => {
      state.panels.forEach((p) => {
        const set = new Set(p.placements || []);
        const moved = OWN.some((z) => set.has(z));
        OWN.forEach((z) => set.delete(z));
        if (moved && !set.has("home")) set.add("home");
        p.placements = ZONE_IDS.filter((z) => set.has(z));
      });
      state.settings.layout = { v: 1, zones: {} };
    });
  }

  /* ----------------------------------------------------- edit mode */
  function onStartScreen() {
    const wrap = $q("#chat-view-wrap");
    return !!wrap && wrap.classList.contains("empty-chat") && !document.body.classList.contains("code-mode")
      && !$q("#chat.work-mode, #chat.code-mode") && !$q("#temp-guide:not([hidden])");
  }
  function goStart() {
    if (onStartScreen()) return;
    try {
      if (document.body.classList.contains("code-mode") || $q("#chat.work-mode, #chat.code-mode")) $q("#nav-chat")?.click();
      if (!onStartScreen() && typeof newChat === "function") newChat();
    } catch (e) { console.warn("[MemuPanelsLayout]", e); }
  }
  function barHtml() {
    return `<div class="mp-editbar-in">
      <span class="mp-editbar-title">${ico("arrange")}<b>${esc(t("Расположение панелей", "Panel layout"))}</b></span>
      <span class="mp-editbar-hint">${esc(t("Перетаскивайте панели · угол — размер · Esc — готово", "Drag panels around · corner — size · Esc — done"))}</span>
      <span class="grow"></span>
      <button type="button" class="mp-eb-btn" data-eb="undo" title="${esc(t("Отменить — Ctrl+Z", "Undo — Ctrl+Z"))}" aria-label="${esc(t("Отменить", "Undo"))}">${ico("undo")}</button>
      <button type="button" class="mp-eb-btn" data-eb="redo" title="${esc(t("Повторить — Ctrl+Shift+Z", "Redo — Ctrl+Shift+Z"))}" aria-label="${esc(t("Повторить", "Redo"))}">${ico("redo")}</button>
      <button type="button" class="mp-eb-btn wide" data-eb="add" title="${esc(t("Добавить панель", "Add a panel"))}">${ico("plus")}<span>${esc(t("Панель", "Panel"))}</span></button>
      <button type="button" class="mp-eb-btn wide" data-eb="reset" title="${esc(t("Вернуть места и вид по умолчанию (можно отменить)", "Back to the default places and look (can be undone)"))}">${ico("reset")}<span>${esc(t("Сбросить", "Reset"))}</span></button>
      <button type="button" class="mp-eb-done" data-eb="done">${ico("check")}<span>${esc(t("Готово", "Done"))}</span></button></div>`;
  }
  function updateBar() {
    const bar = $q(".mp-editbar");
    if (!bar) return;
    const u = $q('[data-eb="undo"]', bar), r = $q('[data-eb="redo"]', bar);
    if (u) u.disabled = !lay.undo.length;
    if (r) r.disabled = !lay.redo.length;
    bar.classList.toggle("saving", lay.saving > 0);
  }
  function enter(focusId = "") {
    const L = P();
    if (!L || lay.editing) { if (focusId) focusCard(focusId); return; }
    L.closeMenu(); L.closePop();
    // from Settings → Panels: the settings window closes (its stack too) — the layout is edited on the window itself
    try { for (let i = 0; i < 4 && $q("#panel-back.visible") && typeof closePanel === "function"; i += 1) closePanel("close"); } catch (e) { /* ok */ }
    goStart();
    lay.editing = true;
    document.body.classList.add("mp-layout-edit");
    let bar = $q(".mp-editbar");
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "mp-editbar";
      bar.setAttribute("role", "toolbar");
      bar.setAttribute("aria-label", t("Расположение панелей", "Panel layout"));
      document.body.appendChild(bar);
    }
    bar.innerHTML = barHtml();
    updateBar();
    L.render();
    setTimeout(() => { L.render(); updateAllScroll(); }, 60);     // after the start screen appeared
    emit("enter");
    chromeLoop();
    if (focusId) setTimeout(() => focusCard(focusId), 80);
    else setTimeout(() => $q('.mp-editbar [data-eb="done"]')?.focus({ preventScroll: true }), 80);
  }
  function exit() {
    if (!lay.editing) return;
    lay.editing = false;
    closeLayPop();
    document.body.classList.remove("mp-layout-edit");
    $q(".mp-editbar")?.remove();
    hideChrome();
    $$q("#group-panels > .mp-zone-head").forEach((h) => h.remove());
    P()?.render();
    emit("exit");
  }
  function focusCard(id) {
    const el = $q(`[data-panel-id="${CSS.escape(id)}"][data-place]:not(.mp-pop *)`);
    if (el) { el.scrollIntoView({ block: "nearest", inline: "nearest" }); el.focus({ preventScroll: true }); P()?.flash(id); }
  }

  /* --------------------------------------- card chrome (edit mode only) */
  function chromeEl() {
    let el = $q(".mp-edchrome");
    if (!el) {
      el = document.createElement("div");
      el.className = "mp-edchrome";
      el.innerHTML = `<div class="mp-edc-tools"></div><button type="button" class="mp-edgrip" aria-hidden="true" tabindex="-1">${ico("resize")}</button>`;
      document.body.appendChild(el);
    }
    return el;
  }
  function hideChrome() { const el = $q(".mp-edchrome"); if (el) el.hidden = true; lay.hover = null; lay.hoverKey = null; }
  /** While editing, the chrome follows its card every frame: the grid re-flows after a size change (masonry is
   *  measured a frame later), live cards re-render, the start screen scrolls. One rect read per frame. */
  let chromeRaf = 0;
  function chromeLoop() {
    cancelAnimationFrame(chromeRaf);
    if (!lay.editing) return;
    chromeRaf = requestAnimationFrame(() => {
      const c = $q(".mp-edchrome");
      if (c && !c.hidden && lay.hoverKey && !lay.resize) {
        let el = lay.hover;
        if (!el || !el.isConnected) {
          el = $q(`[data-panel-id="${CSS.escape(lay.hoverKey.id)}"][data-place="${CSS.escape(lay.hoverKey.place)}"]`);
          if (el) lay.hover = el;
        }
        if (!el) hideChrome();
        else {
          const r = el.getBoundingClientRect();
          if (!r.width) hideChrome();
          else if (c.style.left !== r.left + "px" || c.style.top !== r.top + "px" || c.style.width !== r.width + "px" || c.style.height !== r.height + "px") {
            c.style.left = r.left + "px"; c.style.top = r.top + "px"; c.style.width = r.width + "px"; c.style.height = r.height + "px";
          }
        }
      }
      chromeLoop();
    });
  }
  function itemOf(node) {
    const el = node?.closest?.("[data-panel-id][data-place]");
    if (!el || el.closest(".mp-pop, .mp-modal, #messages, .mp-laypop")) return null;
    return el;
  }
  function placeChrome(el = lay.hover && lay.hover.isConnected ? lay.hover : null) {
    if (!lay.editing) { hideChrome(); return; }
    if (!el && lay.hover) {
      // the card re-rendered: find it again by id + place
      const old = lay.hoverKey;
      if (old) el = $q(`[data-panel-id="${CSS.escape(old.id)}"][data-place="${CSS.escape(old.place)}"]`);
    }
    if (!el || lay.resize) { if (!lay.resize) hideChrome(); return; }
    const c = chromeEl();
    const zone = el.dataset.place;
    const p = P().byId(el.dataset.panelId);
    if (!p) { hideChrome(); return; }
    lay.hover = el;
    lay.hoverKey = { id: p.id, place: zone };
    const isChip = el.classList.contains("mp-chip");
    const canSize = !isChip && sized(zone);
    const tools = $q(".mp-edc-tools", c);
    const sig = [p.id, zone, p.size, canSize, isChip, P().en()].join("|");
    if (tools.dataset.sig !== sig) {
      tools.dataset.sig = sig;
      const names = P().sizeNames(), hints = P().sizeHints();
      tools.innerHTML = `${canSize ? `<div class="mp-edc-seg" role="group" aria-label="${esc(t("Размер", "Size"))}">${SIZES.map((s) => `<button type="button" data-edc-size="${s}" aria-pressed="${p.size === s}" title="${esc(names[s] + " — " + hints[s])}">${s.toUpperCase()}</button>`).join("")}</div>` : ""}
        <button type="button" class="mp-edc-btn" data-edc="settings" title="${esc(t("Настройки панели и места", "Panel settings and places"))}" aria-label="${esc(t("Настройки", "Settings"))}">${ico("gear")}</button>
        <button type="button" class="mp-edc-btn" data-edc="unpin" title="${esc(t("Убрать из этого места", "Remove from this place"))} — Del" aria-label="${esc(t("Убрать отсюда", "Remove from here"))}">${ico("unpin")}</button>`;
    }
    const r = el.getBoundingClientRect();
    if (!r.width || r.bottom < 0 || r.top > innerHeight) { hideChrome(); return; }
    c.hidden = false;
    c.dataset.chip = isChip ? "1" : "";
    c.style.left = r.left + "px"; c.style.top = r.top + "px"; c.style.width = r.width + "px"; c.style.height = r.height + "px";
    const grip = $q(".mp-edgrip", c);
    grip.hidden = !(canSize && ["grid", "strip"].includes(ZONES[zone].kind));
    grip.title = t("Тяните — ширина по сетке (S·M·W)", "Drag — width snaps to the grid (S·M·W)");
  }

  /* ------------------------------------------------------ resize grip */
  function startResize(event) {
    const el = lay.hover;
    if (!el) return;
    event.preventDefault(); event.stopPropagation();
    const zone = el.dataset.place, id = el.dataset.panelId;
    const p = P().byId(id);
    if (!p) return;
    const before = snapshot();
    lay.resize = { id, zone, size: p.size, changed: false };
    document.body.classList.add("mp-resizing");
    const c = chromeEl();
    c.classList.add("resizing");
    const label = document.createElement("div");
    label.className = "mp-resize-tag";
    document.body.appendChild(label);
    const pick = (x) => {
      const card = $q(`[data-panel-id="${CSS.escape(id)}"][data-place="${CSS.escape(zone)}"]`);
      if (!card) return p.size;
      const r = card.getBoundingClientRect();
      const w = Math.max(40, x - r.left);
      if (ZONES[zone].kind === "grid") {
        const grid = card.parentElement;
        const cols = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length || 4;
        const gap = parseFloat(getComputedStyle(grid).columnGap) || 10;
        const colW = (grid.clientWidth - gap * (cols - 1)) / cols;
        const span = Math.max(1, Math.min(cols, Math.round((w + gap) / (colW + gap))));
        if (span <= 1) return "s";
        if (span >= cols && cols > 2) return "w";
        return (P().byId(id) || p).size === "l" ? "l" : "m";
      }
      // strip: nearest preset width
      let best = "m", bestD = Infinity;
      SIZES.forEach((s) => { const d = Math.abs(STRIP_W[s] - w); if (d < bestD) { bestD = d; best = s; } });
      return best;
    };
    const move = (e) => {
      const size = pick(e.clientX);
      label.textContent = `${size.toUpperCase()} · ${P().sizeNames()[size]}`;
      label.style.left = (e.clientX + 14) + "px"; label.style.top = (e.clientY + 14) + "px";
      // the panel object of this moment: a background reload replaces state.panels while the grip is held
      const cur = P().byId(id);
      lay.resize.size = size;
      if (cur && size !== cur.size) {
        cur.size = size; lay.resize.changed = true;
        P().render();
        const card = $q(`[data-panel-id="${CSS.escape(id)}"][data-place="${CSS.escape(zone)}"]`);
        if (card) { const r = card.getBoundingClientRect(); c.style.left = r.left + "px"; c.style.top = r.top + "px"; c.style.width = r.width + "px"; c.style.height = r.height + "px"; }
      }
    };
    const up = () => {
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      document.body.classList.remove("mp-resizing");
      c.classList.remove("resizing");
      label.remove();
      const changed = lay.resize.changed, finalSize = lay.resize.size;
      lay.resize = null;
      if (changed) {
        const cur = P().byId(id);
        if (cur && finalSize && cur.size !== finalSize) { cur.size = finalSize; P().render(); }
        lay.undo.push(before); lay.redo = []; lay.lastChange = Date.now(); updateBar();
        save();
        P().flash(id);
      }
      placeChrome();
    };
    move(event);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
  }
  function startColResize(event, grip) {
    const wrap = grip.closest("[data-zone-wrap]");
    const id = wrap?.dataset.zoneWrap;
    if (!id || !ZONES[id]?.width) return;
    event.preventDefault();
    const before = snapshot();
    const startX = event.clientX, from = zoneCfg(id).width;
    const L = P();
    document.body.classList.add("mp-col-resizing");
    let width = from;
    const move = (e) => {
      const d = (e.clientX - startX) * (id === "left" ? 1 : -1);
      width = Math.max(WIDTH_MIN, Math.min(WIDTH_MAX, Math.round((from + d) / 4) * 4));
      wrap.style.setProperty("--mp-col-w", width + "px");
      grip.setAttribute("aria-valuenow", String(width));
      L.queueLayout();
    };
    const up = () => {
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      document.body.classList.remove("mp-col-resizing");
      if (width !== from) {
        lay.undo.push(before); lay.redo = []; updateBar();
        const layout = L.state.settings.layout || (L.state.settings.layout = { v: 1, zones: {} });
        layout.zones = layout.zones || {};
        layout.zones[id] = { ...zoneCfg(id), width };
        L.render();
        save();
      }
    };
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
  }

  /* -------------------------------------------------------- zone map */
  const MAP_ORDER = ["appbar", "sidebar", "sidebar_bottom", "left", "top", "composer", "home", "right"];
  function zoneMapHtml(selected = [], { name = "", compact = false } = {}) {
    const sel = new Set(selected);
    const short = {
      appbar: t("Верхняя панель", "Top bar"), sidebar: t("Боковая", "Sidebar"), sidebar_bottom: t("Низ", "Bottom"),
      left: t("Слева", "Left"), right: t("Справа", "Right"), top: t("Над приветствием", "Above the greeting"),
      composer: t("Над полем ввода", "Above the box"), home: t("Под полем ввода", "Below the box"),
    };
    const cells = MAP_ORDER.map((z) => `<label class="mz" data-mz="${z}" title="${esc(zoneName(z) + " — " + ZONE_HINTS()[z])}">
        <input type="checkbox" data-mp-place="${z}"${sel.has(z) ? " checked" : ""}${name ? ` name="${esc(name)}"` : ""} aria-label="${esc(zoneName(z))}"><span>${esc(short[z])}</span></label>`).join("");
    return `<div class="mp-zmap${compact ? " compact" : ""}" role="group" aria-label="${esc(t("Где показывать", "Where to show"))}">
      <div class="mp-zmap-win">${cells}<div class="mz-box" aria-hidden="true"><i></i>${esc(t("Сообщение…", "Message…"))}</div></div>
      <div class="mp-zmap-names" aria-live="polite">${esc(selectedNames(selected))}</div></div>`;
  }
  function selectedNames(list) {
    const names = ZONE_IDS.filter((z) => list.includes(z)).map(zoneName);
    return names.length ? names.join(" · ") : t("Нигде — панель только в списке «Настройки → Панели»", "Nowhere — only in Settings → Panels");
  }
  function placesField(selected) {
    return `<div class="mp-field"><span>${esc(t("Где показывать", "Where to show"))}</span>${zoneMapHtml(selected)}
      <small>${esc(t("Щёлкните места на схеме окна — можно несколько. Расставить точнее: «Расположение» над панелями.", "Click places on the window sketch — several are fine. To fine-tune: «Layout» above the panels."))}</small></div>`;
  }
  function syncMapNames(map) {
    const list = $$q("input[data-mp-place]", map).filter((c) => c.checked).map((c) => c.dataset.mpPlace);
    const names = $q(".mp-zmap-names", map);
    if (names) names.textContent = selectedNames(list);
  }
  document.addEventListener("change", (event) => {
    const map = event.target.closest?.(".mp-zmap");
    if (map) syncMapNames(map);
  });

  /* ----------------------------------------- settings / places popover */
  function closeLayPop() { if (lay.pop) { lay.pop.el.remove(); lay.pop = null; } }
  function openPlaces(id, anchor, { full = false } = {}) {
    const L = P();
    const p = L?.byId(id);
    if (!p) return;
    closeLayPop(); L.closeMenu(); L.closePop();
    const el = document.createElement("div");
    el.className = "mp-laypop";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", p.title);
    const draw = () => {
      const cur = L.byId(id) || p;
      const canSize = (cur.placements || []).some(sized);
      const names = L.sizeNames(), hints = L.sizeHints();
      el.innerHTML = `<div class="mp-lp-head">${ico("gear")}<b>${esc(full ? t("Панель", "Panel") : t("Где показывать", "Where to show"))}</b><span class="grow"></span>
          <button type="button" class="mp-x" data-lp="close" aria-label="${esc(t("Закрыть", "Close"))}">${ico("close")}</button></div>
        ${full ? `<label class="mp-lp-row"><span>${esc(t("Название", "Title"))}</span><input class="mp-input" data-lp-title maxlength="80" value="${esc(cur.title)}"></label>` : ""}
        ${zoneMapHtml(cur.placements || [], { compact: true })}
        ${canSize ? `<div class="mp-lp-row"><span>${esc(t("Размер", "Size"))}</span><div class="mp-edc-seg big" role="group">${SIZES.map((s) => `<button type="button" data-lp-size="${s}" aria-pressed="${cur.size === s}" title="${esc(names[s] + " — " + hints[s])}">${s.toUpperCase()}<small>${esc(names[s])}</small></button>`).join("")}</div></div>` : ""}
        <div class="mp-lp-acts">
          <button type="button" class="mp-btn" data-lp="editor">${ico("sliders")}${esc(t("Все настройки…", "All settings…"))}</button>
          ${lay.editing ? "" : `<button type="button" class="mp-btn" data-lp="arrange">${ico("arrange")}${esc(t("Расположение", "Layout"))}</button>`}
          <span class="grow"></span>
          <button type="button" class="mp-btn danger" data-lp="remove" title="${esc(t("Убрать панель совсем (можно вернуть)", "Remove the panel (can be restored)"))}">${ico("trash")}</button></div>`;
    };
    draw();
    document.body.appendChild(el);
    const rect = anchor?.getBoundingClientRect ? anchor.getBoundingClientRect() : new DOMRect(innerWidth / 2, innerHeight / 3, 0, 0);
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = Math.min(Math.max(8, rect.left), innerWidth - w - 8);
    let top = rect.bottom + 6;
    if (top + h > innerHeight - 8) top = Math.max(8, rect.top - h - 6);
    if (top + h > innerHeight - 8) top = Math.max(8, innerHeight - h - 8);
    el.style.left = left + "px"; el.style.top = top + "px";
    lay.pop = { el, id, anchor };
    el.addEventListener("change", (event) => {
      const box = event.target.closest("input[data-mp-place]");
      if (box) { togglePlace(id, box.dataset.mpPlace).then(() => { if (lay.pop?.el === el) { draw(); } }); }
    });
    el.addEventListener("click", (event) => {
      const b = event.target.closest("[data-lp], [data-lp-size]");
      if (!b) return;
      if (b.dataset.lpSize) { setSize(id, b.dataset.lpSize).then(() => { if (lay.pop?.el === el) draw(); }); return; }
      const a = b.dataset.lp;
      if (a === "close") closeLayPop();
      else if (a === "editor") { closeLayPop(); L.openEditor(id); }
      else if (a === "arrange") { closeLayPop(); enter(id); }
      else if (a === "remove") { closeLayPop(); L.remove(id); }
    });
    const title = $q("[data-lp-title]", el);
    if (title) {
      const commitTitle = () => {
        const v = title.value.trim();
        const cur = L.byId(id);
        if (!v || !cur || v === cur.title) return;
        cur.title = v; L.render();
        L.act(L.call(`/api/pg/panels/${id}`, { method: "PATCH", body: { title: v } }));
      };
      title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); commitTitle(); title.blur(); } });
      title.addEventListener("change", commitTitle);
    }
    requestAnimationFrame(() => ($q("[data-lp-title]", el) || $q("input[data-mp-place]", el))?.focus({ preventScroll: true }));
  }

  /* ----------------------------------------------------------- events */
  function bind() {
    // Edit mode: a press on a card's inside does nothing (no To-Do toggles, links, chip popovers) — it is a drag
    // handle; the chrome buttons and the zone heads work.
    document.addEventListener("click", (event) => {
      if (lay.pop && !event.target.closest(".mp-laypop") && !event.target.closest(".mp-edchrome")) closeLayPop();
      const eb = event.target.closest(".mp-editbar [data-eb]");
      if (eb) {
        const a = eb.dataset.eb;
        if (a === "done") exit();
        else if (a === "undo") undo();
        else if (a === "redo") redo();
        else if (a === "reset") reset();
        else if (a === "add") P()?.openGallery("home");
        return;
      }
      const edc = event.target.closest(".mp-edchrome [data-edc], .mp-edchrome [data-edc-size]");
      if (edc && lay.hover) {
        const id = lay.hover.dataset.panelId, zone = lay.hover.dataset.place;
        if (edc.dataset.edcSize) { setSize(id, edc.dataset.edcSize).then(() => placeChrome()); return; }
        if (edc.dataset.edc === "settings") { openPlaces(id, edc, { full: true }); return; }
        if (edc.dataset.edc === "unpin") { unpin(id, zone); return; }
      }
      const head = event.target.closest(".mp-zone-head");
      if (head) {
        const zone = head.closest("[data-zone-wrap]")?.dataset.zoneWrap;
        const add = event.target.closest("[data-zone-add]");
        if (add) { P()?.openGallery(add.dataset.zoneAdd); return; }
        const view = event.target.closest("[data-zone-view]");
        if (view && zone) { setZone(zone, { view: view.dataset.zoneView }); return; }
        if (event.target.closest("[data-zone-chats]") && zone) { setZone(zone, { chats: !zoneCfg(zone).chats }); return; }
      }
      if (lay.editing) {
        const item = itemOf(event.target);
        if (item && !event.target.closest("[data-mp-menu]")) { event.preventDefault(); event.stopPropagation(); item.focus({ preventScroll: true }); }
      }
    }, true);
    document.addEventListener("dblclick", (event) => {
      const grip = event.target.closest(".mp-col-grip");
      if (grip) { const id = grip.closest("[data-zone-wrap]")?.dataset.zoneWrap; if (id) setZone(id, { width: DEFAULT_WIDTH[id] }); }
    });
    document.addEventListener("pointerdown", (event) => {
      if (event.target.closest(".mp-edgrip")) { startResize(event); return; }
      const grip = event.target.closest(".mp-col-grip");
      if (grip && event.button === 0) startColResize(event, grip);
    }, true);
    document.addEventListener("pointermove", (event) => {
      if (!lay.editing || lay.resize || document.body.classList.contains("mp-dragging")) return;
      if (event.target.closest?.(".mp-edchrome")) return;
      const item = itemOf(event.target);
      if (item && item !== lay.hover) placeChrome(item);
      else if (!item && lay.hover) {
        const r = lay.hover.getBoundingClientRect();
        if (event.clientX < r.left - 4 || event.clientX > r.right + 4 || event.clientY < r.top - 4 || event.clientY > r.bottom + 4) hideChrome();
      }
    }, { passive: true });
    document.addEventListener("focusin", (event) => {
      if (!lay.editing) return;
      const item = itemOf(event.target);
      if (item) placeChrome(item);
    });
    document.addEventListener("dragstart", () => { if (lay.editing) hideChrome(); });
    document.addEventListener("dragend", () => setTimeout(() => { renderZonesAfterDrag(); }, 30));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (lay.pop) { event.preventDefault(); event.stopImmediatePropagation(); closeLayPop(); return; }
        if (lay.editing && !document.querySelector(".mp-modal-back, .mcm, .mp-pop")) { event.preventDefault(); exit(); return; }
      }
      const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
      const mod = event.ctrlKey || event.metaKey;
      if (mod && !event.altKey && (event.code === "KeyZ" || event.code === "KeyY") && !typing && !$q(".mp-modal-back")) {
        // In «Edit layout» always; outside it only right after a layout change (2 min) — never in a text field
        if (!lay.editing && Date.now() - lay.lastChange > 120000) return;
        const isRedo = event.code === "KeyY" || event.shiftKey;
        if (isRedo ? !lay.redo.length : !lay.undo.length) return;
        event.preventDefault(); event.stopImmediatePropagation();
        if (isRedo) redo(); else undo();
        return;
      }
      if (!lay.editing || typing) return;
      const item = itemOf(document.activeElement);
      if (!item || document.activeElement !== item) return;
      const id = item.dataset.panelId, zone = item.dataset.place;
      if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); event.stopImmediatePropagation(); unpin(id, zone); return; }
      const dir = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[event.key];
      if (dir && !event.altKey) {
        event.preventDefault(); event.stopImmediatePropagation();
        const ids = orderIn(zone);
        const i = ids.indexOf(id), j = i + dir;
        if (i < 0 || j < 0 || j >= ids.length) return;
        commit(() => { ids.splice(i, 1); ids.splice(j, 0, id); setOrder(zone, ids); }).then(() => focusCard(id));
        setTimeout(() => focusCard(id), 30);
        return;
      }
      if ((event.key === "+" || event.key === "=" || event.key === "-") && sized(zone)) {
        event.preventDefault();
        const p = P().byId(id);
        const k = SIZES.indexOf(p?.size || "m") + (event.key === "-" ? -1 : 1);
        if (p && k >= 0 && k < SIZES.length) setSize(id, SIZES[k]).then(() => focusCard(id));
      }
    }, true);
    window.addEventListener("resize", () => { refold(); keepHomeMin(); updateAllScroll(); placeChrome(); closeLayPop(); });
    // Round 36 (perf): any scroll (the chat, the sidebar) re-measured every zone scroller per event — now once a frame.
    // r36-perf-js-2: and only the scrollers the scrolled box can move (itself or inside it) — scrolling the chat or the
    // sidebar measured the home zones' scrollers every frame (getBoundingClientRect = forced style+layout, ~400 ms per
    // 60 wheel steps on a long chat).
    let scrollFrame = 0, scrollAll = false;
    const scrolled = new Set();
    document.addEventListener("scroll", (event) => {
      if (!lay.scrollers.size && !lay.editing) return;
      const t = event.target;
      if (!(t instanceof Element) || t === document.documentElement || t === document.body) scrollAll = true;
      else if (![...lay.scrollers].some((el) => el === t || t.contains(el)) && !lay.editing) return;
      else scrolled.add(t);
      if (scrollFrame) return;
      scrollFrame = requestAnimationFrame(() => {
        scrollFrame = 0;
        if (scrollAll) updateAllScroll();
        else lay.scrollers.forEach((el) => { for (const t of scrolled) if (el === t || t.contains(el)) { updateScroll(el); break; } });
        scrollAll = false; scrolled.clear();
        if (lay.editing) placeChrome();
      });
    }, { capture: true, passive: true });
    if (typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(() => { refold(); updateAllScroll(); });
      const attach = () => { const m = $q("#main-content"); if (m) ro.observe(m); else setTimeout(attach, 500); };
      attach();
    }
    // the start screen ↔ a chat: the columns' fold and the scrollers' fades follow
    const watch = () => {
      const wrap = $q("#chat-view-wrap");
      if (!wrap) { setTimeout(watch, 400); return; }
      new MutationObserver(() => { refold(); requestAnimationFrame(() => { keepHomeMin(); updateAllScroll(); }); if (lay.editing && !onStartScreen()) placeChrome(); }).observe(wrap, { attributes: true, attributeFilter: ["class"] });
    };
    watch();
  }
  function renderZonesAfterDrag() { refold(); updateAllScroll(); placeChrome(); }
  function unpin(id, zone) {
    const p = P()?.byId(id);
    if (!p) return;
    commit(() => { p.placements = (p.placements || []).filter((z) => z !== zone); }).then(() => {
      P().notify(t(`«${p.title}» убрана из места «${zoneName(zone)}» — Ctrl+Z вернёт`, `“${p.title}” removed from “${zoneName(zone)}” — Ctrl+Z brings it back`), "info");
    });
    hideChrome();
  }

  function zones() {
    return ZONE_IDS.map((id) => {
      const cfg = zoneCfg(id);
      const wrap = id === "home" ? $q("#panels-home") : id === "sidebar" ? $q("#group-panels") : id === "appbar" ? $q("#panels-appbar") : document.getElementById("panels-z-" + id);
      return { id, kind: ZONES[id].kind, name: zoneName(id), view: cfg.view, chats: !!cfg.chats, width: cfg.width || null,
        fold: lay.fold[id] || "", count: placed(id).length, visible: !!wrap && wrap.getClientRects().length > 0 };
    });
  }

  const api = {
    ready: true, ZONES, ZONE_IDS, zoneName, zoneIcon, zones, viewOf, sized, ensureMounts, renderZones,
    enter, exit, toggle: () => (lay.editing ? exit() : enter()), editing: () => lay.editing,
    undo, redo, reset, remember, movePanel, setSize, togglePlace, setZone,
    zoneMapHtml, placesField, openPlaces, scrollify, autoScroll,
    on: (fn) => { lay.listeners.add(fn); return () => lay.listeners.delete(fn); },
    _lay: lay,
  };
  window.MemuPanelsLayout = api;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind); else bind();
  // panels.js may have rendered before this file arrived: draw the new zones now
  const kick = () => { if (P() && P().state.loaded) P().render(); else setTimeout(kick, 300); };
  kick();
})();
