/* Round 30 — MemuAI panels built into the Quick Access window itself (plugin "canvas-embed", data: app/quick_panel_look.py).

   embed.place: «above» (over the input), «below» (under the input, before the canvas strip) or «side» (a column on
   the right of the window; a window narrower than 560 px shows it below instead). embed.items — the user's order,
   a hidden item keeps its place. Each tile has the panel's effective look (MemuPanelLook: global → embed.style → the
   panel's own). Right click / Shift+F10 / the context-menu key on a tile: look, copy the value, refresh, move left /
   right, hide from the window. Above / below the section folds away while an answer is on screen (the canvas
   setting «fold on answer»); the side column stays. Panels are pgstatic/panels.js (embed mode), shared with the
   canvas plugin: the data, refresh and live updates are the same. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const root = document.documentElement;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NARROW = 560;
  const SIDE_MAX = 560;              // without a known window maximum
  const S = { open: false, inChat: false, place: "off", lookLoaded: null, menu: null, html: "" };
  let box = null;           // the section element (moved between the three places)
  let slotEl = null;        // the «below» host (a MemuQuick panel in the top slot)

  const C = () => window.MemuQuickCanvas || null;
  const doc = () => { const c = C(); return c && c.state ? c.state().data : null; };
  const panelsList = () => (window.MemuPanels && MemuPanels.state ? MemuPanels.state.panels || [] : []);
  const panelById = (id) => panelsList().find((p) => p.id === id) || null;

  function ensureLook() {
    if (window.MemuPanelLook) return Promise.resolve(true);
    if (S.lookLoaded) return S.lookLoaded;
    S.lookLoaded = new Promise((resolve) => {
      if (!document.querySelector("link[data-pl-css]")) {
        const link = document.createElement("link");
        link.rel = "stylesheet"; link.href = "/quick/panel-look.css?v=1.1-r32"; link.dataset.plCss = "1";
        document.head.appendChild(link);
      }
      const s = document.createElement("script");
      s.src = "/quick/panel-look.js?v=1.1-r32";
      s.onload = () => resolve(!!window.MemuPanelLook);
      s.onerror = () => resolve(false);
      document.head.appendChild(s);
    });
    return S.lookLoaded;
  }

  function wanted() {
    const d = doc();
    if (!d || !d.embed || d.embed.place === "off" || !d.settings || !d.settings.enabled || d.settings.panels === false) return [];
    const max = d.embed.max || 6;
    return d.embed.items.filter((e) => !e.hidden).slice(0, max);
  }
  function placeNow() {
    const d = doc();
    let place = (d && d.embed && d.embed.place) || "off";
    if (place === "side" && (document.getElementById("qa-card") || document.body).clientWidth < NARROW) place = "below";
    return place;
  }

  function tileHtml(entry, d) {
    const panel = panelById(entry.ref);
    if (!panel) return "";
    const look = MemuPanelLook.resolve(d, panel.id, { embed: true });
    const face = MemuPanelLook.faceHtml(panel, look, { place: "window" })
      || MemuPanels.cardHtml(panel, "home", { size: "s", static: true, place: "quick" });
    return `<div class="qpe-tile" role="group" tabindex="0" data-ref="${esc(panel.id)}" data-look="${esc(JSON.stringify(look))}" aria-label="${esc(panel.title)}">${face}</div>`;
  }

  function mount(place) {
    if (!box) {
      box = document.createElement("section");
      box.className = "qpe";
      box.setAttribute("aria-label", t("Панели в окне", "Panels in the window"));
      box.addEventListener("contextmenu", onContext);
      box.addEventListener("keydown", onKey);
    }
    box.dataset.place = place;
    if (place === "above") {
      const form = document.getElementById("qa-form");
      if (form && box.nextElementSibling !== form) form.parentElement.insertBefore(box, form);
    } else if (place === "below") {
      if (slotEl && box.parentElement !== slotEl) slotEl.appendChild(box);
    } else if (place === "side") {
      const card = document.getElementById("qa-card");
      if (card && box.parentElement !== card) card.appendChild(box);
    }
  }

  function render() {
    const d = doc();
    const items = wanted();
    const place = placeNow();
    const showFlow = !(S.inChat && d && d.settings && d.settings.collapse_on_answer !== false);
    // while a panel's text is being edited in the window, the row above / below the input steps aside (the editor has
    // its own live preview at the tile's size) so the window does not outgrow its maximum height
    const editing = !!S.textRef && place !== "side";
    const visible = S.open && items.length && place !== "off" && (place === "side" || showFlow) && !editing
      && window.MemuPanels && window.MemuPanelLook;
    if (!visible) {
      if (box) { box.hidden = true; box.innerHTML = ""; S.html = ""; }
      root.removeAttribute("data-qpe");
      root.style.removeProperty("--qpe-side-min");
      if (slotEl) Q.hidePanel("canvas-embed");
      Q.resize();
      return;
    }
    mount(place);
    if (place === "below") Q.showPanel("canvas-embed"); else Q.hidePanel("canvas-embed");
    const html = items.map((e) => tileHtml(e, d)).join("");
    if (html !== S.html && !(document.activeElement && box.contains(document.activeElement) && document.activeElement.matches("input, textarea"))) {
      S.html = html;
      box.innerHTML = html;
      box.querySelectorAll(".qpe-tile").forEach((tile) => {
        let look = {};
        try { look = JSON.parse(tile.dataset.look || "{}"); } catch (e) { look = {}; }
        MemuPanelLook.apply(tile, look, { place: "window" });
      });
      if (window.MemuPanels && typeof MemuPanels.syncLive === "function") MemuPanels.syncLive();
    }
    box.hidden = !box.innerHTML;
    root.setAttribute("data-qpe", place);
    if (place === "side") sideFit(); else root.style.removeProperty("--qpe-side-min");
    watchSizes();
    Q.resize();
  }
  /** The window grows to show the whole side column, up to its own maximum height (then the column scrolls). */
  function sideFit() {
    if (!box || box.hidden || box.dataset.place !== "side") return false;
    const cap = parseFloat(getComputedStyle(root).getPropertyValue("--qa-max-h")) || SIDE_MAX;
    const cs = getComputedStyle(box);
    const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0) + 2;
    const gap = parseFloat(cs.rowGap) || 8;
    const kids = Array.from(box.children);
    const need = Math.min(cap, Math.ceil(kids.reduce((h, c) => h + c.getBoundingClientRect().height, 0) + gap * Math.max(0, kids.length - 1) + pad));
    const was = root.style.getPropertyValue("--qpe-side-min");
    root.style.setProperty("--qpe-side-min", need + "px");
    return was !== need + "px";
  }
  // Round 32: a tile can change its height after it was drawn (a web font arrives, the text templates load, a live
  // value gets longer): the side column is measured again and the window follows, instead of cutting the last tile.
  let sizeObs = null, sizeRaf = 0;
  function watchSizes() {
    if (typeof ResizeObserver !== "function" || !box) return;
    if (!sizeObs) sizeObs = new ResizeObserver(() => {
      cancelAnimationFrame(sizeRaf);
      sizeRaf = requestAnimationFrame(() => { if (sideFit()) Q.resize(); else if (box && box.dataset.place !== "side") Q.resize(); });
    });
    sizeObs.disconnect();
    Array.from(box.children).forEach((c) => sizeObs.observe(c));
  }
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (S.open) { if (sideFit()) Q.resize(); } });

  /* ---------- the tile menu */
  function tileOf(target) { return target && target.closest ? target.closest(".qpe-tile") : null; }
  function onContext(e) {
    const tile = tileOf(e.target);
    if (!tile || !window.MemuCtxMenu) return;
    e.preventDefault();
    openMenu(tile, { x: e.clientX, y: e.clientY });
  }
  function onKey(e) {
    const tile = tileOf(e.target);
    if (!tile) return;
    if ((e.shiftKey && e.key === "F10") || e.key === "ContextMenu") { e.preventDefault(); openMenu(tile, { keyboard: true }); return; }
    if (e.key === "ArrowRight" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowUp") {
      const tiles = Array.from(box.querySelectorAll(".qpe-tile"));
      const i = tiles.indexOf(tile) + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1);
      if (tiles[i]) { e.preventDefault(); tiles[i].focus(); }
    }
  }
  function openMenu(tile, where) {
    const ref = tile.dataset.ref;
    const d = doc();
    const panel = panelById(ref) || {};
    const look = MemuPanelLook.resolve(d, ref, { embed: true });
    const order = (d.embed.items || []).filter((x) => !x.hidden).map((x) => x.ref);
    const at = order.indexOf(ref);
    const items = [{ type: "head", label: panel.title || t("Панель", "Panel"), sub: t("В окне быстрого доступа", "In the Quick Access window"), icon: "grid" },
      MemuPanelLook.menuSeg(look)];
    if (d.looks && d.looks[ref]) items.push({ id: "look-reset", label: t("Вид как у всех панелей", "Look like all panels"), icon: "reset" });
    // Round 32: the «text» look is a template the user writes: values as placeholders, placed anywhere in the panel
    items.push({ id: "text-edit", label: look.style === "text" ? t("Изменить текст…", "Edit the text…") : t("Сделать текстом…", "Make it text…"), icon: "edit" });
    items.push({ type: "sep" }, { id: "copy", label: t("Копировать значение", "Copy the value"), icon: "copy" });
    if (!["todo", "notes", "clock", "timers"].includes(panel.kind)) items.push({ id: "refresh", label: t("Обновить сейчас", "Refresh now"), icon: "refresh" });
    if (at > 0) items.push({ id: "left", label: t("Сдвинуть раньше", "Move earlier"), icon: "move" });
    if (at >= 0 && at < order.length - 1) items.push({ id: "right", label: t("Сдвинуть дальше", "Move later"), icon: "move" });
    items.push({ type: "sep" }, { id: "hide", label: t("Скрыть из окна", "Hide from the window"), icon: "hide" });
    const opts = {
      items, keyboard: !!where.keyboard, owner: tile, returnFocus: tile, label: t("Действия", "Actions"),
      onPick: (id, item, opt) => command(ref, item && item.type === "seg" ? "pl-style:" + opt : id, look),
      onClose: () => { S.menu = null; Q.resize(); },
    };
    if (where.x != null) { opts.x = where.x; opts.y = where.y; } else opts.anchor = tile;
    S.menu = MemuCtxMenu.open(opts);
    Q.resize();
  }
  async function save(body) {
    try {
      const out = await Q.api("/api/quick/canvas/look", { body });
      const d = doc();
      if (d && out) { d.look = out.look; d.looks = out.looks; d.embed = out.embed; }
      S.html = "";
      render();
      const c = C(); if (c && c.render) c.render();
      if (window.MemuQuickBus) MemuQuickBus.send("layer", "reload", {});
    } catch (e) { Q.toast(e.message, "error"); }
  }
  async function command(ref, cmd, look) {
    const d = doc();
    if (cmd.startsWith("pl-style:")) return save({ panel: ref, look: { style: cmd.slice(9) } });
    if (cmd === "look-reset") return save({ panel: ref, reset: true });
    if (cmd === "text-edit") return openText(ref);
    if (cmd === "copy") {
      const text = MemuPanelLook.valueText(panelById(ref), look);
      try { await navigator.clipboard.writeText(text); Q.toast(t("Скопировано", "Copied"), "ok"); } catch (e) { Q.toast(t("Не удалось скопировать", "Could not copy"), "error"); }
      return;
    }
    if (cmd === "refresh" && window.MemuPanels) { MemuPanels.refreshNow(ref); return; }
    const items = (d.embed.items || []).map((x) => ({ ...x }));
    if (cmd === "hide") { const row = items.find((x) => x.ref === ref); if (row) row.hidden = true; return save({ embed: { items } }); }
    if (cmd === "left" || cmd === "right") {
      const vis = items.filter((x) => !x.hidden);
      const i = vis.findIndex((x) => x.ref === ref), j = i + (cmd === "left" ? -1 : 1);
      if (i < 0 || !vis[j]) return;
      const a = items.indexOf(vis[i]), b = items.indexOf(vis[j]);
      [items[a], items[b]] = [items[b], items[a]];
      return save({ embed: { items } });
    }
  }

  /* ---------- Round 32: the text template editor (panel-text-editor.js) as a panel of the window */
  let textEl = null, textEd = null;
  const TEXT_V = "?v=1.1-r34";
  function ensureTextEditor() {
    if (window.MemuPanelTextEditor && window.MemuPanelText) return Promise.resolve(true);
    if (S.textLoaded) return S.textLoaded;
    S.textLoaded = new Promise((resolve) => {
      if (!document.querySelector("link[data-pte-css]")) {
        const link = document.createElement("link");
        link.rel = "stylesheet"; link.href = "/quick/panel-text-editor.css" + TEXT_V; link.dataset.pteCss = "1";
        document.head.appendChild(link);
      }
      const need = [];
      if (!window.MemuPanelText && !document.querySelector("script[data-pt-js]")) need.push("/quick/panel-text.js");
      need.push("/quick/panel-text-editor.js");
      let left = need.length;
      const done = () => { if (--left <= 0) { const wait = () => (window.MemuPanelText ? resolve(!!window.MemuPanelTextEditor) : setTimeout(wait, 50)); wait(); } };
      need.forEach((src) => { const s = document.createElement("script"); s.src = src + TEXT_V; s.async = false; s.onload = done; s.onerror = done; document.head.appendChild(s); });
    });
    return S.textLoaded;
  }
  async function openText(ref) {
    const panel = panelById(ref);
    if (!panel || !textEl) return;
    if (!(await ensureTextEditor())) { Q.toast(t("Не удалось открыть редактор текста", "Could not open the text editor"), "error"); return; }
    const d = doc();
    const look = MemuPanelLook.resolve(d, ref, { embed: true });
    const tile = box && box.querySelector(`.qpe-tile[data-ref="${CSS.escape(ref)}"]`);
    const width = Math.round((tile && tile.getBoundingClientRect().width) || (placeNow() === "side" ? 188 : 240));
    closeText();
    S.textRef = ref;
    textEd = MemuPanelTextEditor.mount(textEl, {
      panel, look, text: (d.looks && d.looks[ref] && d.looks[ref].text) || null, lang: Q.t("ru", "en"),
      width: Math.max(140, width), backdrop: "window", place: "window",
      onSave: async (text) => { await save({ panel: ref, look: { style: "text", text } }); closeText(); },
      onCancel: () => closeText(),
    });
    Q.showPanel("panel-text-edit");
    render();
    setTimeout(() => { const first = textEl.querySelector(".pte-stage .pt-b"); if (first) first.focus({ preventScroll: true }); Q.resize(); }, 30);
  }
  function closeText() {
    if (textEd) { textEd.destroy(); textEd = null; }
    const was = S.textRef;
    S.textRef = "";
    if (textEl) Q.hidePanel("panel-text-edit");
    if (was) render(); else Q.resize();
  }
  Q.registerPanel("panel-text-edit", (el) => { textEl = el; el.classList.add("qpt-host", "pte-host"); return {}; }, { slot: "top", order: 5, hidden: true });
  // the text templates arrive after panel-look.js (it loads them): draw the tiles again with real text
  window.addEventListener("memu-panel-text", () => { S.html = ""; if (S.open) render(); const c = C(); if (c && c.render) try { c.render(); } catch (e) { /* canvas */ } });

  /* ---------- wiring */
  Q.registerPanel("canvas-embed", (el) => { slotEl = el; el.classList.add("qpe-host"); return {}; }, { slot: "top", order: 4, hidden: true });

  async function refresh() {
    if (!wanted().length) { render(); return; }
    const c = C();
    const [ok] = await Promise.all([c && c.ensurePanels ? c.ensurePanels() : Promise.resolve(false), ensureLook()]);
    if (ok && window.MemuPanels && !S.sub) S.sub = MemuPanels.subscribe(() => { render(); if (textEd && S.textRef) { const p = panelById(S.textRef); if (p) textEd.refresh(p); } });
    render();
  }
  Q.on("open", () => { S.open = true; S.inChat = !!((Q.state().turns || []).length); setTimeout(refresh, 80); });
  Q.on("close", () => { S.open = false; if (S.menu && S.menu.close) S.menu.close("api"); closeText(); render(); });
  Q.on("answer-start", () => { S.inChat = true; render(); });
  Q.on("chat-new", () => { S.inChat = false; render(); });
  Q.on("chat-open", () => { S.inChat = true; render(); });
  window.addEventListener("resize", () => { if (S.open) render(); });
  if (window.MemuQuickBus) MemuQuickBus.on("changed", () => setTimeout(refresh, 150));
  let syncRaf = 0;
  /** Called by canvas.js after each of its renders (its data may have been reloaded). */
  function sync() { cancelAnimationFrame(syncRaf); syncRaf = requestAnimationFrame(() => { if (S.open) refresh(); }); }
  // The plugins load after the page: when the window is already on screen, «open» has been missed.
  if (Q.state().shown) { S.open = true; S.inChat = !!((Q.state().turns || []).length); setTimeout(refresh, 80); }
  window.MemuQuickEmbed = { render, refresh, sync, state: () => S, openText, closeText };
})();
