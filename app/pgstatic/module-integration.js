/* Round 38 (r38-mod-integ): modules integrate with the MemuAI window (app/module_integration*.py).

   window.MemuModInt
   - Panels of modules («contributes.panels») in the panel gallery and on the home screen / sidebar: each card holds a
     sandbox iframe (/api/pg/modint/panel/<module>/<panel>, the module's own CSP and bridge). panels.js re-renders cards
     with innerHTML, so park() moves the live iframes aside (Element.moveBefore keeps them running; older engines reload
     them) and mount() puts them back into the new placeholders. Iframes are created lazily — only when the card is on
     screen (IntersectionObserver) — and told when they go off screen / the window hides, so their polling pauses.
     The bridge here is a small subset of modules.js: music_call, store, monitor (read), env, log, open_module.
   - Commands for the AI (chat): systemBlock() → the module commands block of the system prompt; the chat runs the
     tool module_command through runTool() (asks first for «danger» commands, then /api/pg/tools) and client() does
     what only this window can (start the host player — pgstatic/music-dock.js, open a module).
   - Widget «chat.composer» (MemuMusic: mini player above the message box while music plays): the player state comes
     from the host player (music-dock.js, event «memu-music-state») — no stream of its own, nothing runs while no music plays. */
(function () {
  "use strict";
  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en";
  const t = (ru, enText) => (en() ? enText : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const S = { modules: [], loaded: false, loading: null, prompt: "", frames: new Map(), cardMsgs: new Map(), parking: null, io: null, player: null, chatEl: null };
  const READ_MONITOR = new Set(["status", "events"]);
  const STORE_OPS = new Set(["get", "get_many", "set", "list"]);

  async function json(path, body) {
    const response = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
    return data;
  }
  function loadCss() {
    if (document.getElementById("memu-modint-css")) return;
    const link = document.createElement("link");
    link.id = "memu-modint-css"; link.rel = "stylesheet"; link.href = "/module-integration.css?v=1.2-r39";
    document.head.appendChild(link);
  }
  function load(force) {
    if (S.loading && !force) return S.loading;
    S.loading = Promise.all([
      json("/api/pg/modint?lang=" + (en() ? "en" : "ru")).catch(() => ({ modules: [] })),
      json("/api/pg/modint/skill?lang=" + (en() ? "en" : "ru")).catch(() => ({ prompt: "" })),
    ]).then(([data, skill]) => {
      S.modules = data.modules || []; S.prompt = skill.prompt || ""; S.loaded = true;
      if (S.modules.length) loadCss();
      syncChatWidget();
      document.dispatchEvent(new CustomEvent("memu-modint-changed"));   // Round 39: the dock re-reads its mode
      return S.modules;
    });
    return S.loading;
  }
  const panelSpec = (module, id) => ((S.modules.find((m) => m.module === module) || {}).panels || []).find((p) => p.id === id);

  /* -------------------------------------------------------------------------------------------- panels */
  function galleryHtml() {
    const rows = S.modules.filter((m) => (m.panels || []).length);
    if (!rows.length) return "";
    const ico = (name) => (window.MemuPanels && MemuPanels.ico ? MemuPanels.ico(name) : "");
    return `<div class="mp-gal-group mmi-gal"><div class="mp-section-title">${esc(t("Панели модулей", "Module panels"))}</div><div class="mp-gallery">${rows.flatMap((m) => m.panels.map((p) =>
      `<button type="button" class="mp-tpl" data-modpanel-add="${esc(m.module + ":" + p.id)}"><span class="mp-tpl-ico">${ico(p.icon || "sparkle") || ico("sparkle")}</span><b>${esc(en() ? p.title_en : p.title)}</b><small>${esc((en() ? p.summary_en : p.summary) || m.title)}</small><i class="mmi-tag">${esc(m.title)}</i></button>`)).join("")}</div></div>`;
  }
  async function addPanel(key, place) {
    const [module, panel] = String(key || "").split(":");
    const spec = panelSpec(module, panel) || {};
    const placements = [place && place !== "home" ? place : "home"];
    try {
      const out = await json("/api/pg/panels", { template: "module", params: { module, panel }, placements, size: spec.size || "m" });
      if (window.MemuPanels) await MemuPanels.load(true);
      if (typeof toast === "function") toast(t("Панель добавлена", "Panel added"), "ok");
      return out;
    } catch (error) {
      if (typeof toast === "function") toast(error.message || String(error), "err");
      return null;
    }
  }
  function slotHtml(b, size, ctx) {
    loadCss();
    const spec = panelSpec(b.module, b.panel);
    const pid = (ctx && ctx.panel && ctx.panel.id) || "";
    const place = (ctx && ctx.mode) || "home";
    const height = Math.max(96, Math.min(640, +(spec && spec.height) || 220));
    if (S.loaded && !spec) return `<div class="mmi-off">${esc(t("Модуль выключен — включите его в «Настройки → Модули».", "The module is off — turn it on in Settings → Modules."))}</div>`;
    return `<div class="mmi-slot" data-mmi="${esc(b.module)}:${esc(b.panel)}" data-mmi-key="${esc(pid + ":" + place)}" data-size="${esc(size)}" style="--mmi-h:${height}px"></div>`;
  }
  function park() {
    if (!S.frames.size) return;
    if (!S.parking) { S.parking = document.createElement("div"); S.parking.className = "mmi-parking"; S.parking.hidden = true; document.body.appendChild(S.parking); }
    for (const item of S.frames.values()) {
      if (!item.frame.isConnected || item.frame.parentNode === S.parking) continue;
      move(S.parking, item.frame);
    }
  }
  function move(parent, el) {
    if (typeof parent.moveBefore === "function" && el.isConnected && parent.isConnected) { try { parent.moveBefore(el, null); return; } catch (e) { /* fallback */ } }
    parent.appendChild(el);
  }
  function mount() {
    const slots = document.querySelectorAll(".mmi-slot[data-mmi]");
    const seen = new Set();
    if (slots.length && !S.loaded) { load().then(() => window.MemuPanels && MemuPanels.render && MemuPanels.render()); return; }
    slots.forEach((slot) => {
      const key = slot.dataset.mmiKey;
      seen.add(key);
      const item = S.frames.get(key);
      if (item && item.src === slot.dataset.mmi) { move(slot, item.frame); item.slot = slot; observe(slot); return; }
      slot.innerHTML = '<div class="mmi-skel" aria-hidden="true"><i></i><b></b><b></b></div>';
      observe(slot);
    });
    for (const [key, item] of S.frames) if (!seen.has(key)) { item.frame.remove(); S.frames.delete(key); }
  }
  function observe(slot) {
    if (!S.io) {
      S.io = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          const slot = entry.target;
          const key = slot.dataset.mmiKey;
          let item = S.frames.get(key);
          if (entry.isIntersecting && !item) item = create(slot);
          if (item) { item.visible = entry.isIntersecting; notifyVisible(item); }
        }
      }, { rootMargin: "120px" });
      document.addEventListener("visibilitychange", () => { for (const item of S.frames.values()) notifyVisible(item); });
    }
    S.io.observe(slot);
  }
  function create(slot) {
    const [module, panel] = slot.dataset.mmi.split(":");
    const frame = document.createElement("iframe");
    frame.className = "mmi-frame";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("allow", "autoplay");
    frame.title = (panelSpec(module, panel) || {}).title || module;
    frame.loading = "lazy";
    frame.src = `/api/pg/modint/panel/${encodeURIComponent(module)}/${encodeURIComponent(panel)}#` + encodeURIComponent(JSON.stringify({ size: slot.dataset.size, place: slot.dataset.mmiKey.split(":")[1] }));
    // Every load (a re-render without moveBefore reloads the frame) gets the theme and the visibility again.
    frame.addEventListener("load", () => { item.slot.querySelector(".mmi-skel")?.remove(); frame.classList.add("ready"); postTheme(item, hostEnv()); notifyVisible(item); });
    const item = { frame, module, src: slot.dataset.mmi, slot, visible: true };
    slot.appendChild(frame);
    S.frames.set(slot.dataset.mmiKey, item);
    return item;
  }
  /* The panel follows the MemuAI theme like a module window: modules.js tokens (light / dark, accent, surface). */
  function hostEnv() {
    if (window.MemuModules && MemuModules.hostEnv) return MemuModules.hostEnv();
    return { lang: en() ? "en" : "ru", theme: document.documentElement.dataset.theme === "light" ? "light" : "dark" };
  }
  function postTheme(item, payload) {
    item.frame.style.colorScheme = payload && payload.theme === "light" ? "light" : "dark";
    try { item.frame.contentWindow.postMessage({ type: "memu-module-host", action: "theme", payload }, "*"); } catch (e) { /* not loaded */ }
  }
  function theme(payload) { for (const item of S.frames.values()) postTheme(item, payload || hostEnv()); }
  function notifyVisible(item) {
    try { item.frame.contentWindow.postMessage({ type: "memu-panel-visible", visible: !!item.visible && !document.hidden }, "*"); } catch (e) { /* not loaded */ }
  }
  const frameOf = (source) => { for (const item of S.frames.values()) if (item.frame.contentWindow === source) return item; return null; };

  window.addEventListener("message", async (event) => {
    const data = event.data || {};
    if (data.type !== "memu-module") return;
    const item = frameOf(event.source);
    if (!item || String(data.from || "") !== item.module) return;
    event.stopImmediatePropagation();
    const reply = (result, error) => { try { event.source.postMessage({ type: "memu-module-result", id: data.id, result: result === undefined ? null : result, error: error ? String(error) : "" }, "*"); } catch (e) { /* gone */ } };
    try { reply(await panelAction(item, data.action, data.payload || {})); } catch (error) { reply(null, error && error.message ? error.message : String(error)); }
  }, true);

  function row(module) {
    // The main window knows the module catalogue; the Quick Access panel layer only has /api/pg/modint.
    return (window.MemuModules && MemuModules.catalog || []).find((r) => r.id === module) || S.modules.find((m) => m.module === module) || {};
  }
  async function panelAction(item, action, payload) {
    const perms = row(item.module).permissions || [];
    const need = (p) => { if (!perms.includes(p)) throw new Error(t(`Модулю не выдано право «${p}»`, `The module lacks the “${p}” permission`)); };
    switch (action) {
      case "env": return { ...hostEnv(), host: "MemuAI", place: "panel" };
      case "routes": return [];
      case "log": return { ok: true };
      case "resize": case "toolbar": case "unsaved": return { ok: true };
      case "open_module":
        if (window.MemuModules) await MemuModules.navigate(item.module, { query: "", from: "panel", ...(payload || {}) });
        else await json("/api/pg/modint/open", { module: item.module, detail: payload || {} });   // Quick Access → the main window
        return { ok: true };
      case "panel_data":
        return await json("/api/pg/modint/data", { module: item.module, panel: item.src.split(":")[1], args: payload.args || {}, lang: en() ? "en" : "ru" });
      case "music_call": {
        need("music");
        const op = String(payload.op || "");
        const body = { op, args: payload.args || {}, module_id: item.module, lang: en() ? "en" : "ru" };
        // r40-lag: the 25 s long-poll goes over a WebSocket — it no longer holds one of the 6 HTTP connections
        const out = (op === "player.wait" && window.MemuLive ? await MemuLive.post("/api/pg/music/call", body) : await json("/api/pg/music/call", body)).result;
        if (op === "player.command") {
          const a = payload.args || {};
          client({ music_boot: true, command: { cmd: a.cmd, args: a, target: "pc" } });
        }
        return out;
      }
      case "store":
        need("storage");
        if (!STORE_OPS.has(String(payload.op || "get"))) throw new Error(t("В панели это действие хранилища недоступно", "This storage action is not available in a panel"));
        return await json("/api/pg/modules/store", { ...payload, module_id: item.module });
      case "monitor":
        need("background");
        if (!READ_MONITOR.has(String(payload.action || ""))) throw new Error(t("Панель может только читать наблюдение", "A panel can only read the monitor"));
        return await json("/api/pg/modules/monitor", { ...payload, module_id: item.module });
      default:
        throw new Error(t(`«${action}» недоступно в панели — откройте модуль`, `“${action}” is not available in a panel — open the module`));
    }
  }

  /* ------------------------------------------------------------------------------- commands (chat, quick) */
  function systemBlock(mode) {
    if (mode === "work" || mode === "code") return "";
    if (!S.loaded) load();
    return S.prompt;
  }
  async function client(c) {
    if (!c || typeof c !== "object") return;
    if (c.modint_reload) { await load(true); if (window.MemuPanels && MemuPanels.render) MemuPanels.render(); return; }
    if (c.open_module && window.MemuModules) await MemuModules.navigate(String(c.open_module), { query: "", model: "", from: "command", ...(c.detail || {}) });
    if (c.music_boot && window.MemuMusicDock && MemuMusicDock.boot) {
      // The dock listens to the player once it runs; a command sent before it started is applied here once.
      const wasOn = !!MemuMusicDock.state().booted;
      if (!wasOn) await MemuMusicDock.boot(true);
      const moduleOpen = !!(window.MemuModules && (MemuModules.openIds || []).includes("music"));
      if (!wasOn && !moduleOpen && c.command && c.command.cmd && MemuMusicDock.apply) MemuMusicDock.apply({ ...c.command, from: "memuai" });
    }
  }
  /** Round 39: {tool: "music_play" | "music.play", args} → {tool: "module_command", args: {module, command, args}} (null if no such command). */
  function aliasCall(call) {
    const norm = (v) => String(v || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    const want = norm(call && call.tool);
    if (!want) return null;
    for (const m of S.modules) for (const c of m.commands || []) {
      const key = norm(m.module + "_" + c.id);
      if (want !== key && want !== key.replace(/_/g, "")) continue;
      const params = { ...((call && call.args) || {}) };
      const inner = params.args;
      delete params.args; delete params.module; delete params.command;
      return { tool: "module_command", args: { module: m.module, command: c.id, args: inner && typeof inner === "object" ? inner : params } };
    }
    return null;
  }
  async function runTool(call, opts = {}) {
    const args = call.args || {};
    const body = { tool: "module_command", args, lang: en() ? "en" : "ru" };
    let out = await json("/api/pg/tools", body).catch((e) => ({ tool: "module_command", ok: false, error: e.message }));
    if (out.needs_confirm) {
      const ok = typeof askConfirm === "function" && await askConfirm(t("Выполнить команду модуля?", "Run the module command?"),
        `${out.title || args.command} · ${args.module}\n${JSON.stringify(out.args || {}).slice(0, 300)}`, t("Выполнить", "Run"));
      if (!ok) return { tool: "module_command", ok: false, error: t("Пользователь не разрешил это действие", "The user did not allow this action") };
      out = await json("/api/pg/tools", { ...body, confirmed: true }).catch((e) => ({ tool: "module_command", ok: false, error: e.message }));
    }
    if (out.client) { client(out.client); delete out.client; }
    if (out.card && opts.message) {
      // The person sees the card under the answer; the model reads only the text (a cover can be a big data: URL).
      attachCard(opts.message, { module: args.module, command: args.command, ok: out.ok !== false, card: out.card });
      delete out.card;
    }
    return out;
  }

  /* --------------------------------------------- result cards in the chat (cover, title, buttons, list rows) */
  const ICONS = {
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 5.5v13l9-6.5zM17 5v14"/></svg>',
    prev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 5.5v13L9 12zM7 5v14"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  };
  ICONS.resume = ICONS.play;
  function slim(card) {
    // A saved chat keeps the card: data: covers (character thumbnails) are dropped past 60 KB.
    const big = (v) => /^data:/.test(String(v || "")) && String(v).length > 60000;
    return { ...card, cover: big(card.cover) ? "" : card.cover || "",
      items: (card.items || []).slice(0, 8).map((it) => ({ ...it, cover: big(it.cover) ? "" : it.cover || "" })) };
  }
  function attachCard(message, entry) {
    message.modCards = [...(message.modCards || []).slice(-3), { ...entry, card: slim(entry.card) }];
  }
  function cardHtml(entry, i, j) {
    const card = entry.card || {};
    const mod = S.modules.find((m) => m.module === entry.module) || {};
    const title = card.title || (entry.ok ? t("Готово", "Done") : t("Не получилось", "Did not work"));
    const hue = [...String(entry.module)].reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) >>> 0, 0) % 360;
    // Round 39 (r39-mod-icons): no cover — the module's own icon.
    const cover = card.cover ? `<img class="mmi-cover" src="${esc(card.cover)}" alt="" decoding="async">`
      : /^[a-z][a-z0-9-]{0,40}$/.test(String(entry.module || "")) && card.kind !== "track" ? `<img class="mmi-cover mmi-mico" src="/api/pg/modules/${esc(entry.module)}/icon" alt="" decoding="async">`
      : `<span class="mmi-cover mmi-gen" aria-hidden="true">${esc(String(title).trim().charAt(0).toUpperCase() || "•")}</span>`;
    const actions = (card.actions || []).map((a, k) => `<button type="button" class="mmi-act${a.toggle ? " main" : ""}${ICONS[a.icon] ? "" : " text"}" data-mmi-act="${k}" title="${esc(a.label)}" aria-label="${esc(a.label)}">${ICONS[a.icon] && a.icon !== "open" ? ICONS[a.icon] : ICONS[a.icon] ? ICONS.open + `<span>${esc(a.label)}</span>` : esc(a.label)}</button>`).join("");
    const items = (card.items || []).slice(0, 6).map((it, k) => `<button type="button" class="mmi-item" data-mmi-item="${k}"${it.command || it.open ? "" : " disabled"}>
      ${it.cover ? `<img src="${esc(it.cover)}" alt="">` : it.rank ? `<i class="mmi-rank" data-place="${+it.rank}">${+it.rank}</i>` : `<i class="mmi-mono">${esc(it.mono || String(it.title || "•").charAt(0).toUpperCase())}</i>`}
      <span><b>${esc(it.title)}</b>${it.subtitle ? `<small>${esc(it.subtitle)}</small>` : ""}</span>${it.value != null ? `<em>${esc(it.value)}</em>` : ""}</button>`).join("");
    const accent = /^#[0-9a-f]{3,8}$/i.test(card.accent || "") ? `--mmi-c:${card.accent};` : "";
    return `<article class="mmi-card" data-kind="${esc(card.kind || "info")}" data-ok="${entry.ok ? 1 : 0}" data-mmi-card="${i}:${j}" style="${accent}--mmi-h:${hue}">
      <div class="mmi-card-main">${cover}<div class="mmi-card-meta"><span class="mmi-card-from">${esc(mod.title || entry.module)}</span><b>${esc(title)}</b>${card.subtitle ? `<small>${esc(card.subtitle)}</small>` : ""}${card.note ? `<small class="mmi-note">${esc(card.note)}</small>` : ""}</div>
      ${actions ? `<div class="mmi-acts">${actions}</div>` : ""}</div>${items ? `<div class="mmi-items">${items}</div>` : ""}</article>`;
  }
  function cardsHtml(m, i) {
    if (!m || !(m.modCards || []).length) return "";
    const cardsOff = (widgetValues("chat.cards") || {}).enabled === false;   // Round 39: «Карточки команд в чате»
    if (cardsOff && m.modCards.every((entry) => entry.module === "music")) return "";
    if (!S.loaded) load();
    loadCss();
    S.cardMsgs.set(String(i), m);
    return `<div class="mmi-cards">${m.modCards.map((entry, j) => cardHtml(entry, i, j)).join("")}</div>`;
  }
  async function onCardClick(e) {
    const el = e.target.closest(".mmi-card [data-mmi-act], .mmi-card [data-mmi-item]");
    if (!el) return;
    const box = el.closest(".mmi-card");
    const [i, j] = String(box.dataset.mmiCard).split(":");
    const m = S.cardMsgs.get(i);
    const entry = m && (m.modCards || [])[+j];
    if (!entry) return;
    const spec = el.dataset.mmiAct != null ? (entry.card.actions || [])[+el.dataset.mmiAct] : (entry.card.items || [])[+el.dataset.mmiItem];
    if (!spec) return;
    if (spec.open) { if (window.MemuModules) MemuModules.navigate(entry.module, { query: "", from: "card", ...(spec.detail || {}) }); return; }
    if (!spec.command) return;
    if (spec.toggle) el.innerHTML = spec.command === "pause" ? ICONS.play : ICONS.pause;
    const out = await json("/api/pg/modint/command", { module: entry.module, command: spec.command, args: spec.args || {}, lang: en() ? "en" : "ru", surface: "chat" }).catch((err) => ({ ok: false, error: err.message }));
    if (out.client) client(out.client);
    if (out.card) {
      m.modCards[+j] = { ...entry, ok: out.ok !== false, command: spec.command, card: slim(out.card) };
      box.outerHTML = cardHtml(m.modCards[+j], i, +j);
    } else if (out.ok === false && typeof toast === "function") toast(out.error || t("Не получилось", "Did not work"), "err");
  }
  document.addEventListener("click", (e) => { if (e.target.closest && e.target.closest(".mmi-card")) onCardClick(e); });
  function summary(result) {
    if (!result) return "";
    return String(result.text || result.error || "").slice(0, 140);
  }

  /* --------------------------------------------------------------------- widget chat.composer (mini player) */
  /** Round 39: values of the first widget in a slot (enabled modules), null when no module offers it. */
  function widgetValues(slot) {
    for (const m of S.modules) for (const w of m.widgets || []) if (w.slot === slot) return w.values || {};
    return null;
  }
  function chatWidget() {
    for (const m of S.modules) for (const w of m.widgets || []) if (w.slot === "chat.composer" && (w.values || {}).enabled === true) return w;
    return null;
  }
  function syncChatWidget() {
    if (!document.querySelector(".composer-wrap")) return;   // the Quick Access panel layer has no chat
    if (!S.player && window.MemuMusicDock && MemuMusicDock.nowPlaying) S.player = MemuMusicDock.nowPlaying();
    renderChat();
  }
  // The player state comes from the host player (music-dock.js, «memu-music-state»): it already listens to the server while
  // there is music, so the page keeps no stream of its own for the widget.
  document.addEventListener("memu-music-state", (e) => { S.player = e.detail; if (S.loaded) syncChatWidget(); });
  function renderChat() {
    const w = chatWidget();
    const st = S.player;
    // while music plays; on pause too when the dock is hidden (by its setting) — otherwise nothing could resume it
    const on = !!(w && st && st.track && (st.playing || !st.dock) && !document.hidden);
    const wrap = document.querySelector(".composer-wrap");
    if (!on || !wrap) { if (S.chatEl) S.chatEl.hidden = true; return; }
    if (!S.chatEl || !S.chatEl.isConnected) {
      S.chatEl = document.createElement("div");
      S.chatEl.className = "mmi-chat-np";
      S.chatEl.addEventListener("click", onChatClick);
      wrap.prepend(S.chatEl);
    }
    const tr = st.track;
    const sig = [tr.id, st.playing, (w.values || {}).eq, en()].join("|");
    S.chatEl.hidden = false;
    if (S.chatEl.dataset.sig === sig) return;
    S.chatEl.dataset.sig = sig;
    S.chatEl.style.setProperty("--mmi-c", /^#[0-9a-f]{6}$/i.test(tr.accent || "") ? tr.accent : "var(--accent)");
    S.chatEl.classList.toggle("is-paused", !st.playing);
    S.chatEl.innerHTML = `${tr.cover ? `<img src="${esc(tr.cover)}" alt="">` : `<span class="mmi-gen">♪</span>`}
      <span class="mmi-np-meta"><b>${esc(tr.title || "—")}</b><small>${esc(tr.artist || "")}</small></span>
      ${(w.values || {}).eq !== false ? '<span class="mmi-eq" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>' : ""}
      <button type="button" data-np="prev" aria-label="${esc(t("Назад", "Previous"))}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 5.5v13L9 12zM7 5v14"/></svg></button>
      <button type="button" data-np="${st.playing ? "pause" : "resume"}" class="main" aria-label="${esc(st.playing ? t("Пауза", "Pause") : t("Играть", "Play"))}">${st.playing ? ICONS.pause : ICONS.play}</button>
      <button type="button" data-np="next" aria-label="${esc(t("Дальше", "Next"))}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 5.5v13l9-6.5zM17 5v14"/></svg></button>`;
  }
  async function onChatClick(e) {
    const b = e.target.closest("[data-np]");
    if (b) {
      // Round 39: the host player lives in this page — its own controls answer at once; the server only when it is not running yet
      const dock = window.MemuMusicDock;
      if (dock && dock.state().booted && dock.control) { dock.control(b.dataset.np === "resume" ? "toggle" : b.dataset.np === "pause" ? "toggle" : b.dataset.np); return; }
      await json("/api/pg/modint/command", { module: "music", command: b.dataset.np, args: {}, lang: en() ? "en" : "ru", surface: "chat" }).then((r) => client(r.client)).catch(() => {});
      return;
    }
    if (window.MemuModules) MemuModules.navigate("music", { query: "", from: "widget" });
  }

  /* The module panel editor (panels.js openModuleEditor): the panel's own settings from contributes.panels[].settings. */
  async function panelInfo(module, panel) {
    await load();
    const mod = S.modules.find((m) => m.module === module);
    const spec = panelSpec(module, panel);
    if (!mod || !spec) return null;
    const values = (spec.settings || []).length
      ? (await json(`/api/pg/modint/settings?module=${encodeURIComponent(module)}&kind=panel&id=${encodeURIComponent(panel)}`).catch(() => ({ values: {} }))).values || {} : {};
    const label = (x) => (en() ? x.label_en : x.label) || x.key;
    const field = (x) => {
      const v = values[x.key] ?? x.default;
      if (x.type === "bool") return `<label class="mp-check"><input type="checkbox" data-mmi-set="${esc(x.key)}"${v ? " checked" : ""}>${esc(label(x))}</label>`;
      if (x.type === "choice") return `<label class="mp-field"><span>${esc(label(x))}</span><select data-mmi-set="${esc(x.key)}">${x.choices.map((c) => `<option value="${esc(c.value)}"${String(v) === c.value ? " selected" : ""}>${esc(en() ? c.label_en : c.label)}</option>`).join("")}</select></label>`;
      return `<label class="mp-field"><span>${esc(label(x))}</span><input data-mmi-set="${esc(x.key)}" value="${esc(v ?? "")}" maxlength="200"></label>`;
    };
    const rows = (spec.settings || []).map(field).join("");
    const summary = en() ? spec.summary_en : spec.summary;
    return { module: mod.title, settingsHtml: `<div class="mp-field mmi-ed"><span>${esc(t("Панель модуля", "Module panel"))} · ${esc(mod.title)}</span>
      ${summary ? `<small>${esc(summary)}</small>` : ""}${rows || `<small>${esc(t("Остальное настраивается в самом модуле.", "Everything else is set up in the module itself."))}</small>`}
      <button type="button" class="mp-btn mmi-ed-open" data-mmi-open="${esc(module)}">${esc(t("Открыть модуль", "Open the module"))}</button></div>` };
  }
  async function savePanelSettings(module, panel, root) {
    const inputs = [...root.querySelectorAll("[data-mmi-set]")];
    if (!inputs.length) return;
    const values = Object.fromEntries(inputs.map((el) => [el.dataset.mmiSet, el.type === "checkbox" ? el.checked : el.value]));
    await json("/api/pg/modint/settings", { module, kind: "panel", id: panel, values });
    for (const item of S.frames.values()) if (item.src === module + ":" + panel) item.frame.src = item.frame.src;   // settings are baked into the page
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-mmi-open]");
    if (b && window.MemuModules) MemuModules.navigate(b.dataset.mmiOpen, { query: "", from: "panel-editor" });
  });

  /* Memu.integration.* of a module window: its own widgets / panels and their settings (only its own). */
  async function moduleSettings(moduleId, payload) {
    const op = String((payload || {}).op || "list");
    if (op === "set") {
      const kind = payload.kind === "panel" ? "panel" : "widget";
      const out = await json("/api/pg/modint/settings", { module: moduleId, kind, id: String(payload.id || ""), values: payload.values || {} });
      await load(true);
      if (window.MemuPanels && MemuPanels.render) MemuPanels.render();
      return out;
    }
    if (op !== "list") throw new Error(t("Неизвестное действие интеграции", "Unknown integration action"));
    await load();
    const row = S.modules.find((m) => m.module === moduleId) || {};
    return { widgets: row.widgets || [], panels: (row.panels || []).map((p) => ({ id: p.id, title: p.title, title_en: p.title_en, settings: p.settings || [] })) };
  }

  /* Round 39 (r39-integ): MemuAI → Settings → Modules — «Виджеты модулей»: every widget of the enabled modules with its
     settings (the same values MemuMusic → «Виджеты и интеграция» edits); a change applies at once in every window. */
  async function renderWidgetSettings(host) {
    if (!host) return;
    let box = host.querySelector(".mmi-wset");
    if (!box) { box = document.createElement("section"); box.className = "mmi-wset"; host.appendChild(box); }
    loadCss();
    await load(true);
    const rows = S.modules.filter((m) => (m.widgets || []).length);
    if (!rows.length) { box.hidden = true; return; }
    box.hidden = false;
    const label = (x) => (en() ? x.label_en || x.title_en : x.label || x.title) || x.key || "";
    const field = (m, w, x) => {
      const v = (w.values || {})[x.key] ?? x.default;
      const key = `${esc(m.module)}|${esc(w.id)}|${esc(x.key)}`;
      if (x.type === "bool") return `<label class="mmi-wset-sw"><input type="checkbox" data-mmi-w="${key}"${v ? " checked" : ""}><i aria-hidden="true"></i><span>${esc(label(x))}</span></label>`;
      if (x.type === "choice") return `<div class="mmi-wset-seg" role="radiogroup" aria-label="${esc(label(x))}">${x.choices.map((c) => `<button type="button" role="radio" data-mmi-w="${key}" data-v="${esc(c.value)}" aria-checked="${String(v) === c.value}">${esc(en() ? c.label_en : c.label)}</button>`).join("")}</div>`;
      return "";
    };
    box.innerHTML = `<h3 class="mmi-wset-h">${esc(t("Виджеты модулей", "Module widgets"))}</h3>
      <p class="mmi-wset-p">${esc(t("Что модули показывают в окнах MemuAI. Изменения применяются сразу.", "What modules show in the MemuAI windows. Changes apply at once."))}</p>
      ${rows.map((m) => `<div class="mmi-wset-mod"><b class="mmi-wset-name">${esc(m.title)}</b>${m.widgets.map((w) => `<div class="mmi-wset-row" data-mmi-row="${esc(w.id)}"><span class="mmi-wset-t">${esc(en() ? w.title_en : w.title)}</span><span class="mmi-wset-c">${(w.settings || []).map((x) => field(m, w, x)).join("")}</span></div>`).join("")}</div>`).join("")}`;
    if (box.dataset.bound) return;
    box.dataset.bound = "1";
    const save = async (el, value) => {
      const [module, id, key] = el.dataset.mmiW.split("|");
      try {
        await json("/api/pg/modint/settings", { module, kind: "widget", id, values: { [key]: value } });
        await load(true);
        if (window.MemuPanels && MemuPanels.render) MemuPanels.render();
        renderWidgetSettings(host);
      } catch (error) { if (typeof toast === "function") toast(error.message || String(error), "err"); }
    };
    box.addEventListener("change", (e) => { const el = e.target.closest("input[data-mmi-w]"); if (el) save(el, el.checked); });
    box.addEventListener("click", (e) => { const el = e.target.closest("button[data-mmi-w]"); if (el) save(el, el.dataset.v); });
  }

  document.addEventListener("memu-modules-changed", () => { if (S.loaded) load(true).then(() => window.MemuPanels && MemuPanels.render && MemuPanels.render()); });
  document.addEventListener("visibilitychange", () => { if (S.loaded) syncChatWidget(); });
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1200));
  setTimeout(() => idle(() => load(), { timeout: 4000 }), 1800);

  window.MemuModInt = { load, widgetValues, renderWidgetSettings, aliasCall, galleryHtml, addPanel, slotHtml, park, mount, systemBlock, runTool, summary, client, cardsHtml, moduleSettings, theme, panelInfo, savePanelSettings,
    state: () => ({ modules: S.modules.length, frames: S.frames.size, prompt: S.prompt.length }) };
})();
