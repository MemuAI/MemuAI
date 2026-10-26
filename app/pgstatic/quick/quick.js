

(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);

  function reveal(box, el) {
    if (!box || !el) return;
    const r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
    if (r.top < b.top) box.scrollTop -= b.top - r.top;
    else if (r.bottom > b.bottom) box.scrollTop += Math.min(r.bottom - b.bottom, r.top - b.top);
  }

  const pinPage = () => { const el = document.scrollingElement; if (el && (el.scrollTop || el.scrollLeft)) el.scrollTo(0, 0); };
  window.addEventListener("scroll", pinPage, { passive: true });
  const root = document.documentElement;
  const NATIVE = root.dataset.native === "1";
  const md = window.QuickMarkdown;
  const esc = md.esc;

  const ICON = {
    globe: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.8 3.9 5.8 3.9 9s-1.3 6.2-3.9 9c-2.6-2.8-3.9-5.8-3.9-9s1.3-6.2 3.9-9z"/></svg>',
    camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.6"/></svg>',
    image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/></svg>',
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 16v5"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg>',
    insert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M20 20v-7a4 4 0 0 0-4-4H4"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    retry: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
    spark: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9z"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    chats: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 8v4l3 2"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
    page: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9h18"/><circle cx="6.5" cy="6.5" r=".6" fill="currentColor"/><circle cx="9" cy="6.5" r=".6" fill="currentColor"/></svg>',
    bolt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.2 2.4 4.6 13.3c-.4.5 0 1.2.6 1.2h5.3l-1.3 7.1c-.1.7.8 1.1 1.2.5l8.6-10.9c.4-.5 0-1.2-.6-1.2h-5.3l1.3-7.1c.1-.7-.8-1.1-1.2-.5z"/></svg>',
    brain: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9.5 2.5 11.2 7.8 16.5 9.5 11.2 11.2 9.5 16.5 7.8 11.2 2.5 9.5 7.8 7.8z"/><path d="M17.5 12.5l1 2.9 2.9 1-2.9 1-1 2.9-1-2.9-2.9-1 2.9-1z"/></svg>',
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h4l9 10h5M3 17h4l2.6-2.9M14.4 9.9 16 7h5M18 4l3 3-3 3M18 14l3 3-3 3"/></svg>',
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1" fill="currentColor"/><circle cx="4.5" cy="12" r="1" fill="currentColor"/><circle cx="4.5" cy="18" r="1" fill="currentColor"/></svg>',
    translate: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h8M8 3v2c0 4-2 7-4.5 8.5M6 9c1 2 3 3.5 5.5 4"/><path d="m13 21 4-9 4 9M14.5 18h5"/></svg>',
    ask: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.8 7L4 20l1.1-4.6A8 8 0 1 1 21 12z"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5.5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="18.5" cy="12" r="1.7"/></svg>',
    back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18 9 12l6-6"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/></svg>',
    text: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 6h14M5 12h14M5 18h9"/></svg>',
    doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
    dock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/></svg>',
    tip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/></svg>',
  };

  const S = {
    prefs: {}, status: null, models: [], model: "", sessionModel: "", web: true,
    lang: root.lang === "en" ? "en" : "ru",
    turns: [],
    attachments: [],
    selection: "",
    selectionInfo: null,
    selWait: null,
    page: null,
    pageOn: false,
    busy: false, controller: null,
    history: [], historyIndex: -1, historyDraft: "", recentChats: [],
    pickerOpen: false, pickerIndex: 0, pickerFilter: "",
    chatsOpen: false, chatsItems: [], chatsIndex: 0, chatsQuery: "", chatsTotal: 0,
    chatId: "", chatCreated: 0,
    routing: "auto",
    maxHeight: 760, pinned: false, lastHeight: 0, shownAt: 0, renderTimer: 0,
    corner: "", radius: 0, dock: "",
    menuOpen: false, menuIndex: 0, menuFilter: "",
    listIndex: -1, listRows: [],
    ui: { onboarded: true },
    endpoint: null,
    efforts: {},
  };
  const en = () => S.lang === "en";
  const tr = (ru, enText) => (en() ? enText : ru);
  const newChatId = () => "qc" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  const listeners = new Map();
  const panels = new Map();
  const composerButtons = new Map();
  const clientTools = new Map();
  const pluginActions = new Map();
  const answerActions = new Map();
  const settingsSections = new Map();
  const requestContexts = new Map();
  function collectContexts() {
    const out = [];
    for (const fn of requestContexts.values()) {
      try { const row = fn(); if (row && typeof row === "object" && row.type) out.push(row); } catch (e) {  }
    }
    return out.slice(0, 8);
  }
  function emit(event, data) {
    const list = listeners.get(event);
    if (!list) return data;
    for (const fn of Array.from(list)) {
      try { fn(data); } catch (e) { console.warn("[MemuQuick]", event, e); }
    }
    return data;
  }
  function on(event, fn) {
    if (typeof fn !== "function") return () => {};
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event).add(fn);
    return () => off(event, fn);
  }
  function off(event, fn) { const list = listeners.get(event); if (list) list.delete(fn); }

  function registerTool(def) {
    const name = String((def && def.name) || "").trim().toLowerCase();
    if (!/^[a-z][a-z0-9_]{1,59}$/.test(name) || typeof def.run !== "function") throw new Error("MemuQuick.registerTool: bad definition");
    clientTools.set(name, { name, description: String(def.description || "").slice(0, 400), label: def.label, run: def.run });
    return () => clientTools.delete(name);
  }
  function registerPanel(id, mount, options = {}) {
    const key = String(id || "").trim();
    if (!key || typeof mount !== "function") throw new Error("MemuQuick.registerPanel: bad definition");
    if (panels.has(key)) unregisterPanel(key);
    const slot = ["top", "bottom", "overlay"].includes(options.slot) ? options.slot : "top";
    const el = document.createElement("section");
    el.className = "qa-plugin";
    el.dataset.plugin = key;
    el.dataset.slot = slot;
    el.style.order = String(+options.order || 0);
    const entry = { el, slot, api: null, visible: options.hidden !== true };
    el.hidden = !entry.visible;
    $("qa-slot-" + slot).appendChild(el);
    panels.set(key, entry);
    try { entry.api = mount(el, MemuQuick) || null; } catch (e) { console.warn("[MemuQuick] panel", key, e); }
    scheduleSize();
    return { show: () => showPanel(key), hide: () => hidePanel(key), remove: () => unregisterPanel(key), el };
  }
  function setPanel(id, visible) {
    const entry = panels.get(id);
    if (!entry) return false;
    entry.visible = !!visible;
    entry.el.hidden = !visible;
    try { if (entry.api && typeof entry.api[visible ? "show" : "hide"] === "function") entry.api[visible ? "show" : "hide"](); } catch (e) {  }
    scheduleSize();
    return true;
  }
  const showPanel = (id) => setPanel(id, true);
  const hidePanel = (id) => setPanel(id, false);
  const togglePanel = (id) => { const entry = panels.get(id); return entry ? setPanel(id, !entry.visible) : false; };
  function unregisterPanel(id) {
    const entry = panels.get(id);
    if (!entry) return;
    try { if (entry.api && typeof entry.api.unmount === "function") entry.api.unmount(); } catch (e) {  }
    entry.el.remove();
    panels.delete(id);
    scheduleSize();
  }
  function addComposerButton(def) {
    const id = String((def && def.id) || "").trim();
    if (!id || typeof def.onClick !== "function") throw new Error("MemuQuick.addComposerButton: bad definition");
    composerButtons.set(id, { order: 0, ...def, id });
    renderComposerButtons();
    return { update: renderComposerButtons, remove: () => { composerButtons.delete(id); renderComposerButtons(); } };
  }

  const BAR_PLUGINS = 2;
  function composerDefs() {
    const out = [];
    for (const def of Array.from(composerButtons.values()).sort((a, b) => (+a.order || 0) - (+b.order || 0))) {
      let hidden = false, pressed = null, title = "";
      try { hidden = typeof def.hidden === "function" ? !!def.hidden(MemuQuick) : !!def.hidden; } catch (e) { hidden = false; }
      if (hidden) continue;
      try { pressed = typeof def.pressed === "function" ? def.pressed(MemuQuick) : (def.pressed === undefined ? null : def.pressed); } catch (e) { pressed = null; }
      try { title = String((typeof def.title === "function" ? def.title(MemuQuick) : def.title) || ""); } catch (e) { title = ""; }
      let label = "";
      try { label = String((typeof def.label === "function" ? def.label(MemuQuick) : def.label) || ""); } catch (e) { label = ""; }
      out.push({ def, pressed, title, label: label || title.replace(/\s*\(.*$/, ""), placement: def.placement === "menu" ? "menu" : "bar" });
    }
    let bar = 0;
    for (const row of out) {
      if (row.placement === "bar" && bar < BAR_PLUGINS) { bar += 1; row.inBar = true; }
    }
    return out;
  }
  function runComposer(def, event) {
    try { def.onClick(event || new MouseEvent("click"), MemuQuick); } catch (e) { console.warn("[MemuQuick] button", def.id, e); }
    renderComposerButtons();
  }
  function renderComposerButtons() {
    const box = $("qa-plugin-buttons");
    if (!box) return;
    box.innerHTML = "";
    for (const row of composerDefs()) {
      if (!row.inBar) continue;
      const def = row.def;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "qa-icon qa-plugin-btn";
      btn.dataset.plugin = def.id;
      btn.innerHTML = String(def.icon || ICON.spark);
      if (row.title) { btn.title = row.title; btn.setAttribute("aria-label", row.title); }
      if (row.pressed !== null) btn.setAttribute("aria-pressed", String(!!row.pressed));
      btn.onclick = (event) => runComposer(def, event);
      box.appendChild(btn);
    }
    if (S.menuOpen) renderMenu();
    queueFitBar();
  }

  function addAction(def) {
    const id = String((def && def.id) || "").trim();
    if (!id || typeof def.run !== "function") throw new Error("MemuQuick.addAction: bad definition");
    pluginActions.set(id, { keys: [], ...def, id });
    if (S.menuOpen) renderMenu();
    return { remove: () => { pluginActions.delete(id); if (S.menuOpen) renderMenu(); } };
  }
  function addAnswerAction(def) {
    const id = String((def && def.id) || "").trim();
    if (!id || typeof def.run !== "function") throw new Error("MemuQuick.addAnswerAction: bad definition");
    answerActions.set(id, { ...def, id });
    renderActions();
    return { remove: () => { answerActions.delete(id); renderActions(); } };
  }
  const pluginValue = (value) => { try { return typeof value === "function" ? value(MemuQuick) : value; } catch (e) { return undefined; } };

  function addContext(text, options = {}) {
    const body = String(text || "").trim();
    if (!body) return false;
    const block = options.name ? `[${tr("Файл", "File")}: ${options.name}]\n${body}` : body;
    setSelection(S.selection ? S.selection + "\n\n" + block : block, contextInfo(options.name ? "file" : "text"));
    if (options.focus !== false) focusInput(true);
    return true;
  }

  const LONG_PASTE = 4000, MAX_CONTEXT_CHARS = 60000;
  function pasteLong(text) {
    const body = String(text || "");
    if (body.length < LONG_PASTE || !addContext(body)) return false;
    const n = body.length.toLocaleString(S.lang === "ru" ? "ru-RU" : "en-US");
    toast(body.length > MAX_CONTEXT_CHARS
      ? tr(`Длинный текст (${n} симв.) добавлен к вопросу — модель получит начало и конец, середина будет сокращена`, `Long text (${n} chars) attached — the model gets its beginning and end, the middle is shortened`)
      : tr(`Текст (${n} симв.) добавлен к вопросу — напишите, что с ним сделать`, `Text (${n} chars) attached — type what to do with it`), "ok", 3200);
    return true;
  }

  function contextInfo(source) {
    if (!S.selection) return { source };
    const prev = S.selectionInfo;
    if (!prev) return null;
    return prev.id ? { ...prev, attached: true } : prev;
  }
  function settingsSection(id, render, options = {}) {
    settingsSections.set(String(id), { render, options });
  }
  function snapshot() {
    return {
      native: NATIVE, lang: S.lang, busy: S.busy, model: effectiveModel(), routing: S.routing,
      chatId: S.chatId, page: S.page ? { ...S.page } : null, pinned: S.pinned,
      selection: S.selection, images: S.attachments.length,
      shown: !!S.shownOnce && !$("qa-card").classList.contains("qa-closed"),
      turns: S.turns.map((t) => ({ q: t.q, a: t.a, model: t.model, tier: t.tier, done: t.done, error: t.error, selectionAction: t.selectionAction || null })),
      prefs: { ...S.prefs },
    };
  }

  const MemuQuick = {
    version: 1,
    native: NATIVE,
    registerTool, registerPanel, showPanel, hidePanel, togglePanel, removePanel: unregisterPanel,
    on, off, emit, addComposerButton, settingsSection,
    send: (text, options = {}) => { if (text != null) setInput(String(text)); if (options.page === false) S.pageOn = false; return send(options); },
    setInput: (text, options = {}) => { setInput(String(text || "")); if (options.focus !== false) focusInput(true); },
    getInput: () => $("qa-input").value,
    newChat: () => newChat(),
    hide: () => closeWindow(),

    toast: (text, kind, ms) => toast(text, kind === "error" || kind === "warn" ? "err" : kind, ms),
    api: (path, options) => api(path, options),
    lang: () => S.lang,
    t: (ru, enText) => tr(ru, enText),
    state: snapshot,
    recordTurn: (row) => recordTurn(row),

    addAction, addAnswerAction, addContext,

    addRequestContext: (id, fn) => { const key = String(id || ""); if (!key || typeof fn !== "function") throw new Error("MemuQuick.addRequestContext: bad definition"); requestContexts.set(key, fn); return () => requestContexts.delete(key); },

    selection: () => ({ text: S.selection, info: S.selectionInfo ? { ...S.selectionInfo } : null }),
    setSelection: (text, info) => setSelection(text, info),
    addImage: (item) => { if (!item || !/^data:image\//.test(String(item.data || ""))) return false; addAttachment({ label: "", name: "image.png", ...item }); renderAll(); return true; },
    resize: () => scheduleSize(),

    keepOnTop: (on) => { S.keepOnTop = !!on; if (on) windowAction("active"); },

    models: () => usableModels().map((m) => ({ id: m.id, name: modelName(m.id), kind: "chat", caps: { ...(m.caps || {}) } })),
    modelName: (id) => modelName(id),
    vision: (id) => ({ sees: seesImages(id), known: visionKnown(id), guess: visionGuess(id), forced: forcedVision.has(id) }),
    forceVision: (id) => { if (id) forcedVision.add(String(id)); },
    probeVision: (id, btn) => probeVision(String(id || ""), btn || null),
    openPicker: () => openPicker(),
    icons: ICON,
  };
  window.MemuQuick = MemuQuick;

  async function loadPlugins() {
    let manifest = null;
    try {
      const response = await fetch("/quick/plugins/index.json", { cache: "no-store" });
      if (response.ok) manifest = await response.json();
    } catch (e) { manifest = null; }
    const rows = Array.isArray(manifest) ? manifest : (manifest && Array.isArray(manifest.plugins) ? manifest.plugins : []);
    for (const row of rows) {
      const entry = typeof row === "string" ? { src: row } : row;
      if (!entry || entry.enabled === false) continue;
      const files = [];
      if (entry.css && /^[\w./-]+\.css$/.test(entry.css) && !entry.css.includes("..")) files.push(["css", entry.css]);
      if (entry.src && /^[\w./-]+\.js$/.test(entry.src) && !entry.src.includes("..")) files.push(["js", entry.src]);
      for (const [kind, file] of files) {
        const url = "/quick/plugins/" + file + (entry.v ? "?v=" + encodeURIComponent(entry.v) : "");
        if (kind === "css") {
          const link = document.createElement("link");
          link.rel = "stylesheet"; link.href = url;
          document.head.appendChild(link);
        } else {
          await new Promise((resolve) => {
            const script = document.createElement("script");
            script.src = url;
            script.onload = resolve;
            script.onerror = () => { console.warn("[MemuQuick] plugin failed:", file); resolve(); };
            document.body.appendChild(script);
          });
        }
      }
    }
    emit("plugins-ready", { count: rows.length });
    renderComposerButtons();
    scheduleSize();
  }

  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: {} };
    if (options.body !== undefined) { init.method = options.method || "POST"; init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(options.body); }
    if (options.signal) init.signal = options.signal;
    const response = await fetch(path, init);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    return data;
  }
  const windowAction = (action, extra = {}) => (NATIVE ? api("/api/quick/window", { body: { action, ...extra } }) : Promise.resolve({ ok: false, native: false }))
    .catch(() => ({ ok: false }));

  const APPEARANCE_PLUS = [["ui_radius", "radius", ["sharp", "round"]], ["ui_glass", "glass", ["light", "strong"]],
    ["ui_contrast", "contrast", ["more"]], ["ui_density", "density", ["compact", "spacious"]], ["ui_motion_speed", "motionSpeed", ["slow", "fast"]]];
  function applyAppearance(prefs) {
    const theme = prefs.theme === "light" ? "light" : "dark";
    root.dataset.theme = theme;
    root.dataset.accent = /^[a-z]{2,24}$/.test(prefs.accent_color || "") ? prefs.accent_color : "terracotta";
    root.dataset.design = /^(aurora|focus|grid|glass|soft|terminal|brutal)$/.test(prefs.ui_design || "") ? prefs.ui_design : "classic";
    const surface = prefs["surface_" + theme] || "";
    if (surface) root.dataset.surface = surface; else delete root.dataset.surface;
    const motion = prefs.ui_motion === "off" ? "off" : (prefs.ui_motion === "reduced" ? "reduced" : "full");
    root.dataset.motion = motion;

    for (const [key, attr, allowed] of APPEARANCE_PLUS) {
      if (allowed.includes(prefs[key])) root.dataset[attr] = prefs[key]; else delete root.dataset[attr];
    }
    S.lang = prefs.language === "en" ? "en" : "ru";
    root.lang = S.lang;
    const scale = Math.max(85, Math.min(125, +prefs.font_scale || 100)) / 100;
    root.style.setProperty("--qa-font", (14.5 * scale).toFixed(2) + "px");
    root.style.setProperty("--qa-input-font", (17.5 * scale).toFixed(2) + "px");
    try {
      const css = localStorage.getItem("memu_user_theme_" + theme);
      $("memu-user-theme").textContent = css || "";
    } catch (e) {  }
    reportSurface();
  }

  let surfaceSent = "";
  function reportSurface() {
    if (!NATIVE) return;
    requestAnimationFrame(() => {
      const probe = document.createElement("div");
      probe.style.cssText = "position:absolute;width:0;height:0;background:var(--qa-window-bg)";
      document.body.appendChild(probe);
      const computed = getComputedStyle(probe).backgroundColor;
      probe.remove();

      let color = computed;
      try {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 1;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = computed;
        ctx.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
        if (a > 127) color = `rgb(${r}, ${g}, ${b})`;
      } catch (e) {  }
      if (color && color !== surfaceSent) { surfaceSent = color; windowAction("surface", { color }); }
    });
  }
  const reducedMotion = () => root.dataset.motion !== "full" || matchMedia("(prefers-reduced-motion: reduce)").matches;

  function applyStaticText() {
    const name = String(S.prefs.quick_ask_name || "").trim();
    const ctxSource = (S.selectionInfo || {}).source;
    const placeholder = S.selection
      ? (ctxSource === "file" ? tr("Что сделать с файлом?", "What should I do with the file?")
        : (ctxSource === "text" ? tr("Что сделать с этим текстом?", "What should I do with this text?")
          : tr("Что сделать с выделенным текстом?", "What should I do with the selected text?")))
      : (S.page && S.pageOn ? tr("Спросите об этой странице…", "Ask about this page…")
        : (S.turns.length ? tr("Спросите ещё…", "Ask a follow-up…")

          : (innerWidth < 520 ? (name ? tr(`Спросите ${name}…`, `Ask ${name}…`) : tr("Спросите что угодно…", "Ask anything…"))
            : (name ? tr(`Спросите ${name}… или / — команды`, `Ask ${name}… or / for commands`) : tr("Спросите что угодно… или / — команды", "Ask anything… or / for commands")))));

    if ($("qa-input").placeholder !== placeholder) { $("qa-input").placeholder = placeholder; if (!$("qa-input").value) autoGrow(); }
    $("qa-input").setAttribute("aria-label", tr("Вопрос", "Question"));
    $("qa-send").setAttribute("aria-label", S.busy ? tr("Остановить", "Stop") : tr("Отправить", "Send"));
    $("qa-send").title = S.busy ? tr("Остановить (Esc)", "Stop (Esc)") : tr("Отправить (Enter)", "Send (Enter)");
    $("qa-shot").innerHTML = ICON.camera;
    $("qa-shot").title = tr("Снимок экрана без окон MemuAI (Ctrl+Shift+S; с Shift — все мониторы)", "Screenshot without MemuAI windows (Ctrl+Shift+S; with Shift — all monitors)");
    $("qa-shot").setAttribute("aria-label", tr("Снимок экрана", "Screenshot"));
    $("qa-new").innerHTML = ICON.plus;
    $("qa-new").title = tr("Новый чат (Ctrl+N) — этот останется в истории", "New chat (Ctrl+N) — this one stays in history");
    $("qa-new").setAttribute("aria-label", tr("Новый чат", "New chat"));
    $("qa-history").innerHTML = ICON.chats;
    $("qa-history").title = tr("Чаты быстрого доступа (Ctrl+H)", "Quick Ask chats (Ctrl+H)");
    $("qa-history").setAttribute("aria-label", tr("Чаты быстрого доступа", "Quick Ask chats"));
    $("qa-pin").innerHTML = ICON.pin;
    $("qa-pin").title = S.pinned
      ? tr("Закреплено: поверх всех окон, Esc в других программах не закрывает — нажмите, чтобы открепить (Ctrl+P)",
        "Pinned: on top of every window, Escape in other programs does not close it — click to unpin (Ctrl+P)")
      : tr("Закрепить окно поверх всех окон (Ctrl+P)", "Pin the window on top of every window (Ctrl+P)");
    $("qa-pin").setAttribute("aria-label", S.pinned ? tr("Открепить окно", "Unpin the window") : tr("Закрепить окно", "Pin the window"));
    $("qa-more").innerHTML = `<span class="lbl">${esc(tr("Действия", "Actions"))}</span><kbd>Ctrl K</kbd>`;
    $("qa-more").title = tr("Все действия и настройки окна (Ctrl+K)", "All actions and window options (Ctrl+K)");
    $("qa-drag").title = tr("Перетащите, чтобы переместить; у края — прилипнет. Двойной щелчок — на место по умолчанию",
      "Drag to move; near an edge it docks. Double-click — back to the default spot");
    $("qa-card").setAttribute("aria-label", tr("MemuAI — быстрый доступ", "MemuAI — Quick Ask"));
    queueFitBar();
  }

  function renderHint() { queueFitBar(); }

  var fitQueued = false;
  function queueFitBar() {
    if (fitQueued) return;
    fitQueued = true;
    requestAnimationFrame(() => { fitQueued = false; fitBar(); });
  }
  function fitBar() {
    const bar = $("qa-bar");
    if (!bar || !bar.offsetWidth) return;
    bar.classList.remove("qa-bar-short", "qa-bar-tight", "qa-bar-tighter");
    const name = $("qa-model-name"), grow = $("qa-grow");
    const over = () => bar.scrollWidth > bar.clientWidth + 1;
    const nameClipped = () => !!(name && name.scrollWidth > name.clientWidth + 1);
    const full = () => !grow || grow.offsetWidth <= 8;
    const crowded = () => over() || (full() && nameClipped());
    if (crowded()) bar.classList.add("qa-bar-short");
    if (crowded()) bar.classList.add("qa-bar-tight");
    if (crowded()) bar.classList.add("qa-bar-tighter");
  }

  const modelRow = (id) => S.models.find((m) => m.id === id);

  const shortModelId = (id) => String(id || "").replace(/^[a-z][a-z-]*:\/\/[^/]+\/([^/]+)(?:\/[^/]*)?$/i, "$1");

  const autoModelName = (id) => {
    const row = modelRow(id);
    if (row && row.name) return row.name;
    try { if (window.MemuModelNames && id) return window.MemuModelNames.pretty(id) || shortModelId(id); } catch (e) {}
    return shortModelId(id);
  };
  const modelName = (id) => ((S.prefs.model_aliases || {})[id]) || autoModelName(id) || tr("модель не выбрана", "no model");

  const customSource = () => !!(S.endpoint && S.endpoint.source === "custom");
  function effectiveModel() {
    const wanted = customSource() ? [S.sessionModel, S.prefs.quick_ask_endpoint_model] : [S.sessionModel, S.prefs.quick_ask_model, S.prefs.default_model];
    return wanted.find(id => id && S.models.some(row => row.id === id && isChatModel(row))) || S.models.find(isChatModel)?.id || "";
  }
  const defaultModel = () => (customSource() ? String(S.prefs.quick_ask_endpoint_model || "") : String(S.prefs.quick_ask_model || S.prefs.default_model || ""));
  const advancedModel = () => {
    const id = String((customSource() ? S.prefs.quick_ask_endpoint_advanced_model : S.prefs.quick_ask_advanced_model) || "");
    return id && id !== effectiveModel() && S.models.some(row => row.id === id && isChatModel(row)) ? id : "";
  };

  const forcedVision = new Set();
  const seesImages = (id) => { if (forcedVision.has(id)) return true; const row = modelRow(id); return !(row && row.caps && row.caps.vision === false); };
  const visionGuess = (id) => { const row = modelRow(id); return !!(row && row.caps && row.caps.vision === false && row.caps.vision_guess); };
  const visionKnown = (id) => { const row = modelRow(id); return !!(row && row.caps && row.caps.vision === true); };
  const TIER = {
    auto: () => [ICON.auto, tr("Авто", "Auto"), tr("Авто: быстрая модель, сложное — умной (Alt+R)", "Auto: the fast model, hard tasks go to the smart one (Alt+R)")],
    fast: () => [ICON.bolt, tr("Быстро", "Fast"), tr("Всегда быстрая модель (Alt+R)", "Always the fast model (Alt+R)")],
    advanced: () => [ICON.brain, tr("Умно", "Smart"), tr("Всегда умная модель (Alt+R)", "Always the smart model (Alt+R)")],
  };

  const EFFORT_LABEL = { auto: ["Авто", "Auto"], off: ["Выкл", "Off"], on: ["Вкл", "On"], minimal: ["Минимум", "Minimal"], low: ["Низкий", "Low"],
    medium: ["Средний", "Medium"], high: ["Высокий", "High"], xhigh: ["Очень высокий", "Extra high"], max: ["Максимум", "Max"] };
  const effortLabel = (level) => tr(...(EFFORT_LABEL[level] || [level, level]));
  function effortOptions(id) {
    const prof = id && modelRow(id) && modelRow(id).caps && modelRow(id).caps.reasoning_profile;
    const options = prof && (prof.kind === "levels" || prof.kind === "toggle") && Array.isArray(prof.options) ? prof.options.filter((x) => EFFORT_LABEL[x]) : [];
    return options.length > 1 ? options : [];
  }
  function effortOf(id) {
    const level = S.efforts[id];
    return level && effortOptions(id).includes(level) ? level : "auto";
  }
  function setEffort(id, level) {
    if (!id) return;
    if (level === "auto") delete S.efforts[id]; else S.efforts[id] = level;
    api("/api/quick/ui", { body: { effort: { model: id, level } } }).catch(() => {});
    renderModelChip();
  }
  const shownModel = () => (S.routing === "advanced" && advancedModel() && !S.sessionModel ? advancedModel() : S.model);
  function sourceLabel() {
    if (!customSource()) return "MemuAI Proxy";
    return String(S.endpoint.label || S.endpoint.host || tr("свой эндпоинт", "own endpoint"));
  }
  function renderModelChip() {
    S.model = effectiveModel();
    const shown = S.routing === "advanced" && advancedModel() && !S.sessionModel ? advancedModel() : S.model;
    const chip = $("qa-model");
    $("qa-model-name").textContent = S.model ? modelName(shown) : (customSource() ? tr("Выберите модель", "Choose a model") : tr("Нет моделей", "No models"));
    $("qa-model-name").title = S.model ? shown : "";
    chip.dataset.source = customSource() ? "custom" : "proxy";
    chip.title = tr(`Модель (Alt+M) · источник: ${sourceLabel()}`, `Model (Alt+M) · source: ${sourceLabel()}`);
    chip.setAttribute("aria-expanded", String(!!S.pickerOpen));
    const tier = $("qa-tier");
    const show = !!advancedModel() && !S.sessionModel;
    tier.hidden = !show;
    if (show) {
      const [, label, title] = TIER[S.routing] ? TIER[S.routing]() : TIER.auto();
      tier.textContent = label;
      tier.title = title;
      tier.dataset.mode = S.routing;
    }
    const effort = $("qa-effort"), level = S.model ? effortOf(shown) : "auto";
    effort.hidden = level === "auto";
    if (level !== "auto") {
      const text = level === "off" ? tr("без размышлений", "no reasoning") : tr("размышления: ", "reasoning: ") + effortLabel(level).toLowerCase();
      effort.innerHTML = `${ICON.brain}<span>${esc(text)}</span>`;
      effort.title = tr(`Размышления: ${effortLabel(level)} (выбор — в списке моделей, Alt+M)`, `Reasoning: ${effortLabel(level)} (change it in the model list, Alt+M)`);
    }
    queueFitBar();
  }
  function cycleRouting() {
    if (!advancedModel()) { toast(tr("Умная модель выбирается в Настройках → Быстрый доступ", "The smart model is chosen in Settings → Quick Ask"), "", 3200); return; }
    const order = ["auto", "fast", "advanced"];
    S.routing = order[(order.indexOf(S.routing) + 1) % order.length];
    renderModelChip();
    toast(TIER[S.routing]()[2].replace(/ \(Alt\+R\)$/, ""));
  }
  function isChatModel(row) {
    const caps = row.caps || {}, kind = String(row.kind || caps.kind || "").toLowerCase();
    if (kind && !["chat", "text"].includes(kind)) return false;
    if (caps.chat === false || caps.text === false) return false;
    return caps.text === true || !["image_generation", "video", "music", "tts", "stt", "transcription", "embedding", "rerank", "decisions"].some(key => caps[key]);
  }
  function usableModels() {
    const hidden = customSource() ? new Set() : new Set(S.prefs.hidden_models || []);
    const pinned = customSource() ? [] : (S.prefs.pinned_models || []);
    return S.models.filter((m) => !hidden.has(m.id) && isChatModel(m))
      .sort((a, b) => {
        const rank = (m) => (m.id === S.model ? 8 : 0) + (m.id === defaultModel() ? 4 : 0) + (pinned.includes(m.id) ? 2 : 0);
        return rank(b) - rank(a) || modelName(a.id).localeCompare(modelName(b.id));
      });
  }

  function viewHead(id, placeholder, value) {
    return `<div class="qa-view-head"><button type="button" class="qa-icon qa-back" data-back title="${esc(tr("Назад (Esc)", "Back (Esc)"))}" aria-label="${esc(tr("Назад", "Back"))}">${ICON.back}</button>
      <label class="qa-search">${ICON.search}<input id="${id}" type="text" spellcheck="false" autocomplete="off" placeholder="${esc(placeholder)}" value="${esc(value || "")}"></label></div>`;
  }
  function listKeys(e, rows, getIndex, setIndex, onEnter, onEscape) {
    if (e.key === "ArrowDown") { e.preventDefault(); setIndex(Math.min(rows().length - 1, getIndex() + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIndex(Math.max(0, getIndex() - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); onEnter(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onEscape(); }

    else return undefined;
    return true;
  }

  function openPicker() {
    closeChats(false); closeMenu(false);
    S.pickerOpen = true; S.pickerFilter = ""; S.pickerIndex = 0;
    setView();
    const picker = $("qa-picker");
    picker.hidden = false;
    $("qa-answer").hidden = true; $("qa-suggest").hidden = true; $("qa-actions").hidden = true;
    const adv = advancedModel();
    picker.innerHTML = viewHead("qa-picker-q", tr("Найти модель…", "Find a model…"), "")
      + (adv && !S.sessionModel ? `<div class="qa-route-row"><div class="qa-seg" role="radiogroup" aria-label="${esc(tr("Какая модель отвечает", "Which model answers"))}">${["auto", "fast", "advanced"].map((mode) => {
        const [icon, label, title] = TIER[mode]();
        return `<button type="button" role="radio" class="qa-route ${S.routing === mode ? "on" : ""}" aria-checked="${S.routing === mode}" data-route="${mode}" title="${esc(title)}">${icon}<span>${esc(label)}</span></button>`;
      }).join("")}</div><span class="qa-route-note">${esc(tr("умная: ", "smart: "))}${esc(modelName(adv))}</span></div>` : "")
      + effortRow()
      + `<div class="qa-picker-list" id="qa-picker-list" role="listbox"></div>
      <div class="qa-picker-note">${tr(`Источник: <b>${esc(sourceLabel())}</b>. Выбор действует для этого окна; модель по умолчанию — в Настройках → Быстрый доступ.`,
        `Source: <b>${esc(sourceLabel())}</b>. The choice applies to this window; the default is in Settings → Quick Ask.`)} <span id="qa-picker-count"></span></div>`;
    picker.querySelectorAll("[data-route]").forEach((btn) => {
      btn.onclick = () => { S.routing = btn.dataset.route; S._routingTouched = true; picker.querySelectorAll("[data-route]").forEach((b) => { b.classList.toggle("on", b === btn); b.setAttribute("aria-checked", String(b === btn)); }); renderModelChip(); bindEffortRow(); };
    });
    bindEffortRow();
    picker.querySelector("[data-back]").onclick = () => closePicker();
    const count = $("qa-picker-count");
    if (count) {
      const hidden = customSource() ? 0 : S.models.length - usableModels().length;
      count.textContent = tr(`Моделей: ${usableModels().length}`, `Models: ${usableModels().length}`) + (hidden > 0 ? tr(` · скрыто в настройках: ${hidden}`, ` · hidden in settings: ${hidden}`) : "");
    }
    const input = $("qa-picker-q");
    input.oninput = () => { S.pickerFilter = input.value; S.pickerIndex = 0; renderPicker(); };
    input.onkeydown = (e) => { listKeys(e, pickerRows, () => S.pickerIndex, (i) => { S.pickerIndex = i; renderPicker(); },
      () => { const rows = pickerRows(); if (rows[S.pickerIndex]) chooseModel(rows[S.pickerIndex].id); }, closePicker); };
    renderPicker();
    renderModelChip();
    input.focus();
    scheduleSize();
  }
  function effortRow() {
    const id = shownModel(), options = effortOptions(id), current = effortOf(id);
    if (!options.length) return `<div class="qa-effort-row" id="qa-effort-row" hidden></div>`;
    return `<div class="qa-route-row qa-effort-row" id="qa-effort-row"><span class="qa-effort-label" title="${esc(tr(`Для модели ${modelName(id)}`, `For ${modelName(id)}`))}">${ICON.brain}${esc(tr("Размышления", "Reasoning"))}</span>`
      + `<div class="qa-seg" role="radiogroup" aria-label="${esc(tr("Глубина размышлений", "Reasoning depth"))}">${options.map((level) =>
        `<button type="button" role="radio" class="qa-route ${level === current ? "on" : ""}" aria-checked="${level === current}" data-effort="${level}" title="${esc(level === "auto" ? tr("Как решает быстрый доступ (модель по умолчанию)", "As Quick Ask decides (the model's default)") : effortLabel(level))}">${esc(effortLabel(level))}</button>`).join("")}</div></div>`;
  }
  function bindEffortRow() {
    const row = $("qa-effort-row");
    if (!row) return;
    row.outerHTML = effortRow();
    $("qa-effort-row").querySelectorAll("[data-effort]").forEach((btn) => {
      btn.onclick = () => { setEffort(shownModel(), btn.dataset.effort); bindEffortRow(); };
    });
    scheduleSize();
  }
  function pickerRows() {
    const needle = S.pickerFilter.trim().toLowerCase();

    return usableModels().filter((m) => !needle || modelName(m.id).toLowerCase().includes(needle) || m.id.toLowerCase().includes(needle)).slice(0, 600);
  }

  function visionTag(m) {

    if (!forcedVision.has(m.id) && m.caps && m.caps.vision_state === "unknown")
      return `<span class="tag guess" title="${esc(tr("Неизвестно, видит ли модель картинки: проверим при первой картинке", "Not known whether it sees images: checked with the first picture"))}">${ICON.eye}${esc(tr("зрение?", "vision?"))}</span>`;
    if (forcedVision.has(m.id) || (m.caps && m.caps.vision === true))
      return `<span class="tag eye" title="${esc(tr("Видит картинки и экран: снимки, вопросы по изображению, управление компьютером", "Sees images and the screen: screenshots, image questions, computer use"))}">${ICON.eye}${esc(tr("видит", "vision"))}</span>`;
    if (m.caps && m.caps.vision === false && m.caps.vision_guess)
      return `<span class="tag guess" title="${esc(tr("По названию — только текст, но это догадка: можно проверить при отправке картинки", "Text only by its name — a guess: you can check when you send a picture"))}">${esc(tr("текст?", "text?"))}</span>`;
    if (m.caps && m.caps.vision === false)
      return `<span class="tag" title="${esc(tr("Не видит картинки: без снимков экрана и управления компьютером", "Cannot see images: no screenshots, no computer use"))}">${esc(tr("только текст", "text only"))}</span>`;
    return "";
  }
  function renderPicker() {
    const list = $("qa-picker-list");
    if (!list) return;
    const rows = pickerRows();
    const adv = String((customSource() ? S.prefs.quick_ask_endpoint_advanced_model : S.prefs.quick_ask_advanced_model) || "");
    list.innerHTML = rows.length ? rows.map((m, i) => `<button type="button" role="option" class="qa-row qa-pick ${i === S.pickerIndex ? "active" : ""} ${m.id === S.model ? "sel" : ""}" data-id="${esc(m.id)}" data-i="${i}" aria-selected="${m.id === S.model}">
        <span class="ic">${m.id === S.model ? ICON.check : (m.id === adv ? ICON.brain : ICON.bolt)}</span>
        <span class="tx qa-mname"><span class="nm">${esc(modelName(m.id))}</span>${modelName(m.id) !== m.id ? `<span class="mid">${esc(m.id)}</span>` : ""}</span>
        ${(m.badges || []).includes("free") ? `<span class="tag free">${esc(tr("бесплатно", "free"))}</span>` : ""}
        ${m.id === defaultModel() ? `<span class="tag">${esc(tr("по умолчанию", "default"))}</span>` : ""}
        ${m.id === adv ? `<span class="tag adv">${esc(tr("умная", "smart"))}</span>` : ""}
        ${visionTag(m)}</button>`).join("")
      : `<div class="qa-chats-empty">${ICON.search}<span>${esc(S.models.length ? tr("Ничего не найдено", "Nothing found")
        : (customSource() ? tr("Эндпоинт не вернул моделей — укажите модель в Настройках → Быстрый доступ.", "The endpoint returned no models — set one in Settings → Quick Ask.")
          : tr("Нет моделей. Добавьте провайдера в MemuAI Proxy.", "No models. Add a provider in MemuAI Proxy.")))}</span></div>`;
    list.querySelectorAll(".qa-pick").forEach((btn) => {
      btn.onclick = () => chooseModel(btn.dataset.id);
      btn.onmousemove = () => { const i = +btn.dataset.i; if (i !== S.pickerIndex) { S.pickerIndex = i; list.querySelectorAll(".qa-pick").forEach((x) => x.classList.toggle("active", x === btn)); } };
    });
    reveal(list, list.querySelector(".active"));
    scheduleSize();
  }
  function chooseModel(id) {
    S.sessionModel = id === defaultModel() ? "" : id;
    renderModelChip();
    closePicker();
    if (S.attachments.length && !seesImages(S.model)) visionWarning();
  }
  function closePicker() {
    S.pickerOpen = false;
    $("qa-picker").hidden = true;
    $("qa-picker").innerHTML = "";
    renderModelChip();
    renderAll();
    focusInput();
  }
  function closePickerSilently() {
    if (!S.pickerOpen) return;
    S.pickerOpen = false; $("qa-picker").hidden = true; $("qa-picker").innerHTML = "";
    $("qa-model").setAttribute("aria-expanded", "false");
  }

  function menuItems() {
    const last = shownTurns().pop();
    const answered = !!(last && last.done && String(last.a || "").trim());
    const items = [];
    const add = (section, id, icon, label, keys, run, extra = {}) => items.push({ section, id, icon, label, keys, run, ...extra });
    const CHAT = tr("Чат", "Chat"), ADD = tr("Добавить", "Add"), WIN = tr("Окно и модель", "Window and model"), PLUG = tr("Голос и плагины", "Voice and plugins");
    add(CHAT, "new", ICON.plus, tr("Новый чат", "New chat"), ["Ctrl", "N"], () => newChat(), { disabled: S.busy });
    add(CHAT, "chats", ICON.chats, tr("Чаты быстрого доступа", "Quick Ask chats"), ["Ctrl", "H"], () => openChats());
    if (answered) add(CHAT, "copy", ICON.copy, tr("Копировать ответ", "Copy the answer"), ["Ctrl", "Shift", "C"], () => copyAnswer(null));
    if (answered) add(CHAT, "insert", ICON.insert, tr("Вставить ответ в программу", "Paste the answer into the program"), [], () => insertAnswer());
    if (answered && advancedModel() && last.tier !== "advanced" && !last.restored) add(CHAT, "advance", ICON.brain, tr("Спросить умную модель", "Ask the smart model"), ["Ctrl", "Shift", "A"], () => askAdvanced());
    if (window.QuickDFY) add(CHAT, "dfy", ICON.bolt, tr("Сделать за меня", "Do it for me"), ["Ctrl", "↵"], () => window.QuickDFY.run(), { sub: tr("агент DFY", "DFY agent") });
    if (S.turns.length || $("qa-input").value.trim()) add(CHAT, "open", ICON.open, tr("Открыть в MemuAI", "Open in MemuAI"), ["Ctrl", "Shift", "↵"], () => openInMemuAI());
    if (S.prefs.quick_ask_screenshot_tool !== false || NATIVE) {
      add(ADD, "shot", ICON.camera, tr("Снимок экрана", "Screenshot"), ["Ctrl", "Shift", "S"], () => takeScreenshot(false));
      add(ADD, "shot-all", ICON.dock, tr("Снимок всех мониторов", "Screenshot of all monitors"), [], () => takeScreenshot(true));
    }
    add(ADD, "file", ICON.image, tr("Изображение из файла…", "Image from a file…"), ["Ctrl", "V"], () => $("qa-file-input").click());
    if (S.page && !S.pageOn) add(ADD, "page", ICON.page, tr("Вернуть открытую страницу", "Use the open page again"), [], () => { S.pageOn = true; renderAttachments(); renderAll(); });
    add(WIN, "model", ICON.spark, tr("Сменить модель", "Change the model"), ["Alt", "M"], () => openPicker(), { sub: modelName(S.model) });
    if (effortOptions(shownModel()).length) add(WIN, "effort", ICON.brain, tr("Размышления", "Reasoning"), ["Alt", "M"], () => openPicker(), { sub: effortLabel(effortOf(shownModel())) });
    if (advancedModel() && !S.sessionModel) add(WIN, "route", ICON.auto, tr("Кто отвечает", "Who answers"), ["Alt", "R"], () => { S._routingTouched = true; cycleRouting(); }, { state: TIER[S.routing]()[1], keep: true });
    if (S.prefs.quick_ask_web_search !== false) add(WIN, "web", ICON.globe, tr("Веб-поиск для этого окна", "Web search for this window"), [], () => { S.web = !S.web; S._webTouched = true; }, { state: S.web ? tr("вкл", "on") : tr("выкл", "off"), on: S.web, keep: true });
    if (NATIVE) add(WIN, "pin", ICON.pin, S.pinned ? tr("Открепить окно", "Unpin the window") : tr("Закрепить окно", "Pin the window"), ["Ctrl", "P"], () => togglePin(), { state: S.pinned ? tr("закреплено", "pinned") : "", on: S.pinned, keep: true });
    if (NATIVE) add(WIN, "center", ICON.dock, tr("Вернуть окно на место", "Move the window back"), [], () => windowAction("dock", { dock: "center" }));
    if (NATIVE) add(WIN, "hide", ICON.x, tr("Скрыть окно", "Hide the window"), ["Esc"], () => closeWindow());
    add(WIN, "settings", ICON.gear, tr("Настройки быстрого доступа", "Quick Ask settings"), [], () => { windowAction("open_settings"); if (!NATIVE) toast(tr("Настройки открываются в окне MemuAI", "Settings open in the MemuAI window")); });
    for (const row of composerDefs()) {
      if (row.inBar && (row.def.id === "voice" || row.def.menu === false)) continue;
      add(PLUG, "plugin:" + row.def.id, String(row.def.icon || ICON.spark), row.label || row.def.id, [], () => runComposer(row.def),
        { state: row.pressed === null ? "" : (row.pressed ? tr("вкл", "on") : tr("выкл", "off")), on: !!row.pressed, keep: row.pressed !== null });
    }
    for (const def of pluginActions.values()) {
      if (pluginValue(def.hidden)) continue;
      add(String(pluginValue(def.section) || PLUG), "x:" + def.id, String(def.icon || ICON.spark), String(pluginValue(def.label) || def.id), def.keys || [], () => def.run(MemuQuick),
        { sub: pluginValue(def.sub) || "", stay: !!def.stay });
    }
    add(tr("Справка", "Help"), "keys", ICON.tip, tr("Сочетания клавиш", "Keyboard shortcuts"), [], () => showShortcuts(), { stay: true });

    const order = [...new Set(items.map((it) => it.section))];
    items.sort((a, b) => order.indexOf(a.section) - order.indexOf(b.section));
    const needle = S.menuFilter.trim().toLowerCase();
    return needle ? items.filter((it) => it.label.toLowerCase().includes(needle) || it.section.toLowerCase().includes(needle)) : items;
  }
  function openMenu() {
    closePickerSilently(); closeChats(false); closeMenu(false);
    S.menuOpen = true; S.menuFilter = ""; S.menuIndex = 0;
    setView();
    $("qa-more").setAttribute("aria-expanded", "true");
    const box = $("qa-menu");
    box.hidden = false;
    $("qa-answer").hidden = true; $("qa-suggest").hidden = true; $("qa-actions").hidden = true;
    box.innerHTML = viewHead("qa-menu-q", tr("Найти действие…", "Search actions…"), "") + '<div class="qa-menu-list" id="qa-menu-list" role="menu"></div>';
    box.querySelector("[data-back]").onclick = () => closeMenu();
    const input = $("qa-menu-q");
    input.oninput = () => { S.menuFilter = input.value; S.menuIndex = 0; renderMenu(); };
    input.onkeydown = (e) => { listKeys(e, () => menuItems().filter((it) => !it.disabled), () => S.menuIndex, (i) => { S.menuIndex = i; renderMenu(); },
      () => runMenuItem(S.menuIndex), () => closeMenu()); };
    renderMenu();
    input.focus();
    scheduleSize();
  }
  function renderMenu() {
    const list = $("qa-menu-list");
    if (!list) return;
    const items = menuItems();
    let section = "", n = -1;
    const html = [];
    for (const it of items) {
      if (it.section !== section) { section = it.section; html.push(`<div class="qa-section">${esc(section)}</div>`); }
      if (!it.disabled) n += 1;
      html.push(`<button type="button" role="menuitem" class="qa-row ${!it.disabled && n === S.menuIndex ? "active" : ""}" ${it.disabled ? 'aria-disabled="true"' : `data-n="${n}"`} data-act="${esc(it.id)}">
        <span class="ic">${it.icon}</span><span class="tx">${esc(it.label)}</span>${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ""}
        ${it.state ? `<span class="state ${it.on ? "on" : ""}">${esc(it.state)}</span>` : ""}
        ${(it.keys || []).length ? `<span class="keys">${it.keys.map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span>` : ""}</button>`);
    }
    list.innerHTML = html.join("") || `<div class="qa-chats-empty">${ICON.search}<span>${esc(tr("Нет такого действия", "No such action"))}</span></div>`;
    list.querySelectorAll("[data-n]").forEach((btn) => {
      btn.onclick = () => runMenuItem(+btn.dataset.n);
      btn.onmousemove = () => { const i = +btn.dataset.n; if (i !== S.menuIndex) { S.menuIndex = i; list.querySelectorAll("[data-n]").forEach((x) => x.classList.toggle("active", x === btn)); } };
    });
    reveal(list, list.querySelector(".active"));
    scheduleSize();
  }
  function runMenuItem(index) {
    const it = menuItems().filter((row) => !row.disabled)[index];
    if (!it) return;
    if (it.stay) { it.run(); return; }
    if (it.keep) { it.run(); renderModelChip(); renderAll(); renderMenu(); const q = $("qa-menu-q"); if (q) q.focus(); return; }
    closeMenu(false);
    renderAll();
    it.run();
    if (!S.pickerOpen && !S.chatsOpen) focusInput();
  }
  function closeMenu(rerender = true) {
    if (!S.menuOpen) return;
    S.menuOpen = false;
    $("qa-more").setAttribute("aria-expanded", "false");
    $("qa-menu").hidden = true; $("qa-menu").innerHTML = "";
    if (rerender) { renderAll(); focusInput(); }
  }
  function showShortcuts() {
    S.menuFilter = "";
    const list = $("qa-menu-list");
    if (!list) return;
    const rows = [
      [["↵"], tr("Отправить", "Send")], [["Shift", "↵"], tr("Новая строка", "New line")], [["Esc"], tr("Назад / стоп / скрыть окно (работает из любой программы)", "Back / stop / hide (works from any program)")],
      [["↑"], tr("Прошлые вопросы (в пустом поле)", "Previous questions (empty field)")], [["Ctrl", "K"], tr("Все действия", "All actions")],
      [["Ctrl", "N"], tr("Новый чат", "New chat")], [["Ctrl", "H"], tr("Чаты", "Chats")], [["Alt", "M"], tr("Модель", "Model")],
      [["Alt", "R"], tr("Авто / Быстро / Умно", "Auto / Fast / Smart")], [["Ctrl", "Shift", "A"], tr("Переспросить умную модель", "Ask the smart model again")],
      [["Ctrl", "Shift", "S"], tr("Снимок экрана", "Screenshot")],
      [["Ctrl", "Shift", "C"], tr("Копировать ответ", "Copy the answer")], [["Ctrl", "↵"], tr("Сделать за меня (DFY)", "Do it for me (DFY)")], [["Ctrl", "Shift", "↵"], tr("Открыть в MemuAI", "Open in MemuAI")],
      [["Ctrl", "P"], tr("Закрепить окно поверх всех окон", "Pin the window on top")], [["Ctrl", "Alt", "←↑→↓"], tr("Прилепить к краю экрана", "Dock to a screen edge")],
      [["Alt", "1…9"], tr("Пункт списка по номеру", "A list row by number")],
    ];
    if (window.MemuQuickVoice) rows.push([["Ctrl", "Space"], tr("Удерживайте и говорите", "Hold and speak")]);
    for (const def of pluginActions.values()) if ((def.keys || []).length && !pluginValue(def.hidden)) rows.push([def.keys, String(pluginValue(def.label) || def.id)]);
    list.innerHTML = `<div class="qa-section">${esc(tr("Сочетания клавиш", "Keyboard shortcuts"))}</div>` + rows.map(([keys, label]) =>
      `<div class="qa-row" style="cursor:default"><span class="tx">${esc(label)}</span><span class="keys">${keys.map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span></div>`).join("");
    scheduleSize();
  }

  const liveCard = (t) => !!(t && t.card && !t.restored);
  const shownTurns = () => S.turns.filter((t) => !liveCard(t));
  function recordTurn(row = {}) {
    const q = String(row.q || "").trim(), a = String(row.a || "").trim();
    if (!q || !a) return false;
    if (!S.chatId) { S.chatId = newChatId(); S.chatCreated = Date.now(); }
    S.turns.push({ q, a: a.slice(0, 20000), model: String(row.model || ""), tier: "fast", card: true, images: [], selection: "",
      page: null, sources: [], shots: [], error: "", errorCode: "", done: true, t: Date.now() });
    saveChat();
    renderAll();
    return true;
  }
  function chatTurns() {
    return S.turns.filter((t) => t.done && (t.q || t.a || t.error)).map((t) => ({
      q: t.q, a: t.a, model: t.model, tier: t.tier || "fast", t: t.t || Date.now(), images: (t.images || []).length,
      selection: t.selection || "", page: t.page ? { url: t.page.url, title: t.page.title } : null,
      sources: (t.sources || []).slice(0, 6), error: t.error || "",

      ...(t.prompt ? { prompt: t.prompt } : {}),
    }));
  }

  const saveTimers = new Map();
  function saveChat() {
    if (!S.chatId || S.prefs.quick_ask_history === false) return;
    const turns = chatTurns();
    if (!turns.some((t) => t.a)) return;
    const id = S.chatId, created = S.chatCreated || Date.now();
    clearTimeout(saveTimers.get(id));
    saveTimers.set(id, setTimeout(() => {
      saveTimers.delete(id);
      const first = turns.find((t) => t.q) || turns[0];

      const title = first.selection && first.q && first.q.length < 48 ? `${first.q}: ${first.selection.replace(/\s+/g, " ").trim().slice(0, 60)}` : first.q;
      api("/api/quick/chats", { body: { chat: { id, title: title || (first.page && first.page.title) || "", created, updated: Date.now(), turns } } })
        .then(() => loadRecentChats()).catch(() => {});
    }, 120));
  }
  function newChat(options = {}) {
    stop();
    if (S.turns.length) saveChat();
    S.turns = []; S.attachments = []; S.historyIndex = -1;
    S.chatId = newChatId(); S.chatCreated = Date.now();
    if (!options.keepInput) { $("qa-input").value = ""; autoGrow(); renderCalc(); }
    closePickerSilently(); closeChats(false); closeMenu(false);
    renderAttachments(); renderAll(); focusInput();
    emit("chat-new", { id: S.chatId });
    if (options.toast !== false) toast(tr("Новый чат", "New chat"));
  }
  async function openChats() {
    closePickerSilently(); closeMenu(false);
    S.chatsOpen = true; S.chatsIndex = 0;
    setView();
    $("qa-history").setAttribute("aria-pressed", "true");
    const box = $("qa-chats");
    box.hidden = false;
    $("qa-answer").hidden = true; $("qa-suggest").hidden = true; $("qa-actions").hidden = true;
    box.innerHTML = viewHead("qa-chats-q", tr("Поиск по чатам…", "Search chats…"), S.chatsQuery)
      + `<div class="qa-chats-list" id="qa-chats-list" role="listbox"><div class="qa-chats-empty">${esc(tr("Загружаю…", "Loading…"))}</div></div>
      <div class="qa-picker-note">${esc(tr("Чаты быстрого доступа хранятся отдельно от чатов MemuAI. «Открыть в MemuAI» переносит чат туда. Del — удалить.",
        "Quick Ask chats are kept apart from MemuAI chats. «Open in MemuAI» moves a chat there. Del — delete."))}</div>`;
    box.querySelector("[data-back]").onclick = () => closeChats();
    const input = $("qa-chats-q");
    let timer = 0;
    input.oninput = () => { S.chatsQuery = input.value; clearTimeout(timer); timer = setTimeout(loadChats, 140); };
    input.onkeydown = (e) => {
      if (e.key === "Delete" && !input.value) { e.preventDefault(); const row = S.chatsItems[S.chatsIndex]; if (row) deleteChat(row.id); return; }
      listKeys(e, () => S.chatsItems, () => S.chatsIndex, (i) => { S.chatsIndex = Math.max(0, i); renderChats(); },
        () => { const row = S.chatsItems[S.chatsIndex]; if (row) openChat(row.id); }, () => closeChats());
    };
    renderAll();
    input.focus();
    scheduleSize();
    await loadChats();
  }
  async function loadChats() {
    try {
      const data = await api("/api/quick/chats?q=" + encodeURIComponent(S.chatsQuery || ""));
      S.chatsItems = data.items || []; S.chatsTotal = data.total || 0;
    } catch (e) { S.chatsItems = []; }
    S.chatsIndex = Math.min(S.chatsIndex, Math.max(0, S.chatsItems.length - 1));
    renderChats();
  }
  function when(ms) {
    if (!ms) return "";
    const d = new Date(ms), now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const yesterday = new Date(now.getTime() - 86400000).toDateString() === d.toDateString();
    const time = d.toLocaleTimeString(S.lang, { hour: "2-digit", minute: "2-digit" });
    if (sameDay) return time;
    if (yesterday) return tr("вчера", "yesterday") + " " + time;
    return d.toLocaleDateString(S.lang, { day: "numeric", month: "short" });
  }
  function renderChats() {
    const list = $("qa-chats-list");
    if (!list) return;
    if (!S.chatsItems.length) {
      list.innerHTML = `<div class="qa-chats-empty">${ICON.chats}<span>${esc(S.chatsQuery ? tr("Ничего не нашлось", "Nothing found")
        : tr("Чатов пока нет — задайте первый вопрос", "No chats yet — ask your first question"))}</span></div>`;
      scheduleSize();
      return;
    }
    list.innerHTML = S.chatsItems.map((row, i) => `<div class="qa-row qa-chat ${i === S.chatsIndex ? "active" : ""} ${row.id === S.chatId ? "current" : ""}" role="option" aria-selected="${i === S.chatsIndex}" data-id="${esc(row.id)}" data-i="${i}">
      <span class="ic">${row.page ? ICON.page : ICON.ask}</span>
      <span class="tx"><b>${esc(row.title || tr("Без названия", "Untitled"))}</b>${row.preview ? `<small>${esc(row.preview)}</small>` : ""}</span>
      <span class="meta">${esc(when(row.updated))}</span>
      <button type="button" class="qa-chat-del" data-del="${esc(row.id)}" title="${esc(tr("Удалить (Del)", "Delete (Del)"))}" aria-label="${esc(tr("Удалить", "Delete"))}">${ICON.trash}</button></div>`).join("");
    list.querySelectorAll(".qa-chat").forEach((el) => {
      el.onclick = (e) => { if (e.target.closest("[data-del]")) return; openChat(el.dataset.id); };
      el.onmousemove = () => { const i = +el.dataset.i; if (i !== S.chatsIndex) { S.chatsIndex = i; list.querySelectorAll(".qa-chat").forEach((x) => x.classList.toggle("active", x === el)); } };
    });
    list.querySelectorAll("[data-del]").forEach((btn) => { btn.onclick = (e) => { e.stopPropagation(); deleteChat(btn.dataset.del); }; });
    reveal(list, list.querySelector(".active"));
    scheduleSize();
  }
  async function openChat(id) {
    try {
      const { chat } = await api("/api/quick/chats/" + encodeURIComponent(id));
      stop();
      if (S.turns.length && S.chatId !== id) saveChat();
      S.chatId = chat.id; S.chatCreated = chat.created || Date.now();
      S.turns = (chat.turns || []).map((t) => ({ q: t.q || "", a: t.a || "", model: t.model || "", tier: t.tier || "fast",
        images: Array.from({ length: t.images || 0 }, () => ""), storedImages: t.images || 0, selection: t.selection || "",
        page: t.page || null, sources: t.sources || [], shots: [], error: t.error || "", errorCode: "", done: true, t: t.t, restored: true,
        prompt: t.prompt || "" }));
      closeChats(false);
      renderAll();
      focusInput(true);
      emit("chat-open", { id: chat.id });
    } catch (e) { toast(tr("Чат не найден", "Chat not found"), "err"); loadChats(); }
  }
  async function deleteChat(id) {
    if (id === S.chatId) stop();
    clearTimeout(saveTimers.get(id)); saveTimers.delete(id);
    try { await api("/api/quick/chats/" + encodeURIComponent(id), { method: "DELETE" }); } catch (e) { toast(e.message, "err"); return; }
    if (id === S.chatId) { S.turns = []; S.chatId = newChatId(); S.chatCreated = Date.now(); }
    toast(tr("Чат удалён", "Chat deleted"));
    await loadChats();
    const input = $("qa-chats-q"); if (input) input.focus();
  }
  function closeChats(rerender = true) {
    if (!S.chatsOpen) return;
    S.chatsOpen = false;
    $("qa-history").setAttribute("aria-pressed", "false");
    $("qa-chats").hidden = true; $("qa-chats").innerHTML = "";
    if (rerender) { renderAll(); focusInput(); }
  }

  function addAttachment(item) {
    if (S.attachments.length >= 4) { toast(tr("Не больше четырёх изображений", "At most four images"), "err"); return; }
    S.attachments.push(item);
    renderAttachments();
    if (!seesImages(effectiveModel())) visionWarning();
  }
  function pageHost(page) {
    try { return new URL(page.url).host.replace(/^www\./, ""); } catch (e) { return ""; }
  }
  function renderAttachments() {
    const box = $("qa-attach");
    const parts = S.attachments.map((a, i) => `<div class="qa-thumb" data-i="${i}"><img alt="" src="${a.data}"><span>${esc(a.label || "")}</span>
      <button type="button" class="qa-x" data-remove="${i}" aria-label="${esc(tr("Убрать", "Remove"))}">${ICON.x}</button></div>`);
    if (S.selection) {
      const words = S.selection.trim().split(/\s+/).filter(Boolean).length;

      const chars = S.selection.length, info = S.selectionInfo || {};
      const size = chars < 1000 ? tr(`${chars} симв.`, `${chars} chars`) : tr(`${(chars / 1000).toFixed(1).replace(".", ",")} тыс. симв.`, `${(chars / 1000).toFixed(1)}k chars`);
      const from = info.app ? tr(`Выделено в ${info.app.replace(/\.exe$/i, "")}`, `Selected in ${info.app.replace(/\.exe$/i, "")}`) + "\n" : "";

      const files = info.source === "file" ? Array.from(S.selection.matchAll(/^\[(?:Файл|File): (.+)\]$/gm), (m) => m[1]) : [];
      const body = files.length ? `<span class="tl">${esc(files.join(", "))}</span>` : `<q>${esc(S.selection.replace(/\s+/g, " ").slice(0, 160))}</q>`;
      parts.unshift(`<div class="qa-chip-sel ${files.length ? "qa-chip-file" : ""}" title="${esc(from + S.selection.slice(0, 600))}"><span class="fav">${files.length ? ICON.doc : ICON.text}</span><b>${esc(info.source === "file" ? tr("Файл", "File") : (info.source === "text" ? tr("Текст", "Text") : tr("Выделено", "Selected")))}</b>
        ${body}<span class="n">${esc((files.length ? "" : tr(`${words} сл. · `, `${words} words · `)) + size + (info.truncated ? tr(" (начало)", " (start)") : ""))}</span>
        <button type="button" class="qa-icon" data-remove-sel aria-label="${esc(tr("Убрать выделенный текст", "Remove the selected text"))}" title="${esc(tr("Убрать", "Remove"))}">${ICON.x}</button></div>`);
    }
    if (S.page && S.pageOn) {
      const host = pageHost(S.page);
      parts.unshift(`<div class="qa-chip-page ${S.page.pending ? "pending" : ""}" title="${esc((S.page.title || "") + (S.page.url ? "\n" + S.page.url : ""))}">
        <span class="fav">${ICON.page}</span><b>${esc(tr("Страница", "Page"))}:</b><span class="tl">${esc(S.page.title || host || tr("вкладка браузера", "browser tab"))}</span>
        ${host ? `<span class="host">${esc(host)}</span>` : (S.page.pending ? `<span class="host">${esc(tr("узнаю адрес…", "reading the address…"))}</span>` : "")}
        <button type="button" class="qa-icon" data-remove-page aria-label="${esc(tr("Не использовать страницу", "Do not use the page"))}" title="${esc(tr("Не использовать страницу (Backspace)", "Do not use the page (Backspace)"))}">${ICON.x}</button></div>`);
    }
    box.innerHTML = parts.join("");
    box.hidden = !parts.length;
    box.querySelectorAll("[data-remove]").forEach((btn) => { btn.onclick = () => { S.attachments.splice(+btn.dataset.remove, 1); renderAttachments(); renderAll(); }; });
    const sel = box.querySelector("[data-remove-sel]");
    if (sel) sel.onclick = () => { setSelection(""); focusInput(); };
    const pg = box.querySelector("[data-remove-page]");
    if (pg) pg.onclick = () => { S.pageOn = false; renderAttachments(); renderAll(); focusInput(); };
    renderContextActions();
    scheduleSize();
  }

  function setSelection(text, info = null) {
    S.selection = String(text || "");
    S.selectionInfo = S.selection && info && typeof info === "object" ? { ...info } : null;
    renderAttachments(); renderAll(); applyStaticText();
    emit("selection", { text: S.selection, info: S.selectionInfo });
  }
  const selectionOwned = () => !!(S.selection && window.QuickSelection && window.QuickSelection.owns());
  function contextActions() {
    if (S.selection) {
      if (selectionOwned()) return [];
      return [
        [ICON.ask, tr("Объяснить", "Explain"), tr("Объясни простыми словами.", "Explain in simple words.")],
        [ICON.translate, tr("Перевести", "Translate"), tr("Переведи на русский (если текст уже на русском — на английский).", "Translate into English (if it is already English — into Russian).")],
        [ICON.check, tr("Исправить", "Fix"), tr("Исправь ошибки и опечатки, сохрани смысл и стиль. Верни только исправленный текст.", "Fix mistakes and typos, keep the meaning and style. Return only the corrected text.")],
        [ICON.list, tr("Кратко", "Summarize"), tr("Перескажи в 2–3 предложениях.", "Summarize in 2–3 sentences.")],
        [ICON.insert, tr("Ответить", "Reply"), tr("Напиши короткий вежливый ответ на это сообщение.", "Write a short polite reply to this message.")],
      ];
    }
    if (S.page && S.pageOn) {
      return [
        [ICON.list, tr("Кратко", "Summarize"), tr("Перескажи эту страницу кратко: суть в 3–5 предложениях.", "Summarize this page: the gist in 3–5 sentences."), true],
        [ICON.spark, tr("Главное", "Key points"), tr("Выдели главные мысли этой страницы списком (до 7 пунктов).", "List the key points of this page (up to 7 bullets)."), true],
        [ICON.translate, tr("Перевести", "Translate"), tr("Переведи основное содержание этой страницы на русский.", "Translate the main content of this page into English."), true],
        [ICON.ask, tr("Спросить о странице", "Ask about the page"), "", true],
      ];
    }
    return [];
  }

  function renderContextActions() {
    const box = $("qa-sel-actions");
    box.hidden = true; box.innerHTML = "";
  }
  function runContextAction(index) {
    const action = contextActions()[index];
    if (!action) return false;
    if (!action[2]) { focusInput(true); $("qa-input").placeholder = tr("Что узнать об этой странице?", "What do you want to know about this page?"); return true; }
    $("qa-input").value = action[2];
    send({ readPage: !!action[3] });
    return true;
  }

  async function probeVision(id, btn) {
    const label = btn ? btn.innerHTML : "";
    if (btn) { btn.disabled = true; btn.textContent = tr("Проверяю…", "Checking…"); }
    let r = null;
    try { r = await api("/api/quick/vision/probe", { body: { model: id, lang: S.lang } }); } catch (e) { r = { ok: false, error: e.message || String(e) }; }
    const restore = () => { if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = label; } };
    if (r && r.vision === true) {
      forcedVision.add(id);
      toast(tr(`«${modelName(id)}» видит картинки — запомнил`, `«${modelName(id)}» sees images — remembered`), "ok", 2800);
      loadConfig();
      restore();
      return true;
    }
    restore();
    if (r && r.vision === false) toast(tr(`«${modelName(id)}» не видит картинки — выберите другую модель`, `«${modelName(id)}» cannot see images — pick another model`), "err", 4600);
    else toast(tr("Не удалось проверить: ", "Could not check: ") + String((r && (r.error || r.answer)) || tr("неясный ответ модели", "unclear answer")).slice(0, 160), "err", 4600);
    return false;
  }
  function visionWarning() {
    if (visionGuess(effectiveModel())) { toast(tr("Похоже, эта модель видит только текст — при отправке можно проверить", "This model looks text-only — you can check it when you send"), "", 4200); return; }
    toast(tr("Эта модель не видит изображения — выберите модель с отметкой «видит»", "This model cannot see images — pick a model marked «vision»"), "err", 4200);
  }
  async function takeScreenshot(all = false) {
    const card = $("qa-card");
    try {
      const shot = await api("/api/quick/screenshot", { body: { monitor: all ? "all" : "cursor" } });
      if (!reducedMotion()) { card.classList.remove("qa-flash"); void card.offsetWidth; card.classList.add("qa-flash"); setTimeout(() => card.classList.remove("qa-flash"), 500); }
      addAttachment({ data: shot.data, width: shot.width, height: shot.height, label: all ? tr("все экраны", "all screens") : tr("экран", "screen"), name: "screenshot.jpg" });
      return true;
    } catch (e) {
      toast(NATIVE ? e.message : tr("Снимок экрана работает в приложении MemuAI для Windows", "Screenshots work in the MemuAI Windows app"), "err", 3600);
      return false;
    }
  }
  function readImageFile(file) {
    return new Promise((resolve) => {
      if (!file || !/^image\//.test(file.type) || file.size > 12 * 1024 * 1024) { resolve(null); return; }
      const reader = new FileReader();
      reader.onload = () => shrinkImage(String(reader.result || ""), file.size).then(resolve);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(file);
    });
  }

  const IMAGE_SIDE = 2048, IMAGE_KEEP_BYTES = 1.5 * 1024 * 1024;
  function shrinkImage(data, bytes = 0) {
    return new Promise((resolve) => {
      if (!data || /^data:image\/(gif|svg)/i.test(data)) { resolve(data || null); return; }
      const img = new Image();
      img.onload = () => {
        const w = img.naturalWidth, h = img.naturalHeight;
        if (!w || !h || (Math.max(w, h) <= IMAGE_SIDE && bytes <= IMAGE_KEEP_BYTES)) { resolve(data); return; }
        try {
          const k = Math.min(1, IMAGE_SIDE / Math.max(w, h));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(w * k)); canvas.height = Math.max(1, Math.round(h * k));
          const ctx = canvas.getContext("2d");
          ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

          let out = canvas.toDataURL("image/jpeg", 0.88);
          if (out.length >= data.length && Math.max(w, h) > IMAGE_SIDE) {
            const png = canvas.toDataURL("image/png");
            if (png.length < out.length) out = png;
          }
          resolve(out.length < data.length ? out : data);
        } catch (e) { resolve(data); }
      };
      img.onerror = () => resolve(data);
      img.src = data;
    });
  }

  const TEXT_FILE = /\.(txt|md|markdown|json|jsonl|csv|tsv|log|ya?ml|toml|ini|cfg|conf|xml|html?|css|js|mjs|cjs|ts|tsx|jsx|py|rb|go|rs|java|kt|c|h|cc|cpp|hpp|cs|php|sh|ps1|bat|sql|srt|vtt)$/i;

  const PACKED = /officedocument|opendocument|zip|epub/;
  function isTextFile(file) { return !!file && !PACKED.test(file.type || "") && (/^text\//.test(file.type) || /json|xml|javascript|x-sh|yaml/.test(file.type) || TEXT_FILE.test(file.name || "")); }
  async function addFiles(files) {
    for (const file of Array.from(files || [])) {
      if (/^image\//.test(file.type)) {
        let data = await readImageFile(file);
        if (data && data.length > 14000000) data = "";
        if (data) addAttachment({ data, label: tr("изображение", "image"), name: file.name || "image.png" });
        else toast(file.size > 10 * 1024 * 1024 ? tr("Картинка слишком большая (больше 10 МБ)", "The image is too large (over 10 MB)")
          : tr("Не удалось прочитать картинку", "Could not read the image"), "warn", 3200);
        continue;
      }

      let taken = false;
      if (!isTextFile(file) || file.size > 400 * 1024) emit("file", { file, preventDefault: () => { taken = true; } });
      if (taken) continue;
      if (!isTextFile(file)) { toast(tr("Можно добавить картинку или текстовый файл", "Drop an image or a text file"), "warn"); continue; }
      if (file.size > 400 * 1024) { toast(tr("Файл больше 400 КБ — слишком длинный для быстрого доступа", "The file is over 400 KB — too long for Quick Ask"), "warn", 3200); continue; }
      let text = "";
      try { text = await file.text(); } catch (_) { text = ""; }
      if (!text.trim()) continue;

      addContext(text, { name: file.name, focus: false });
      toast(tr("Файл добавлен к вопросу: ", "File attached: ") + file.name);
      focusInput(true);
    }
  }

  function tierChip(turn, inline = true, tools = "") {
    if (!turn.model || (!turn.done && !turn.a)) return tools ? `<div class="qa-meta">${tools}</div>` : "";
    const advanced = turn.tier === "advanced";
    const icon = advanced ? ICON.brain : ICON.bolt;
    const label = advanced ? tr("Умная", "Smart") : tr("Быстрая", "Fast");
    const why = turn.escalated ? tr(" · передано быстрой моделью", " · handed over by the fast model") : "";
    const secs = turn.ms && turn.done && !turn.restored ? ` · ${(turn.ms / 1000).toFixed(turn.ms < 10000 ? 1 : 0).replace(".", S.lang === "ru" ? "," : ".")} ${tr("с", "s")}` : "";
    const page = turn.page ? ` · ${turn.pageSource === "screen" ? tr("текст с экрана", "text from screen") : tr("по странице", "from the page")}` : "";
    const pi = turn.page && turn.pageInfo;
    const pageWhy = pi ? " · " + (pi.cached ? tr("страница из кэша", "page from cache") : tr("страница прочитана за ", "page read in ") + (pi.ms / 1000).toFixed(1).replace(".", S.lang === "ru" ? "," : ".") + " " + tr("с", "s"))
      + (pi.tokens ? ` · ~${pi.tokens} ${tr("токенов", "tokens")}` : "") + (pi.truncated ? tr(" · длинная — сокращена", " · long — shortened") : "") : "";

    const stopped = turn.stopped && turn.a ? tr(" · остановлено", " · stopped") : turn.cut && turn.a ? tr(" · ответ оборвался", " · cut off") : "";
    return `<div class="qa-meta" ${inline ? "" : 'data-last="1"'}><span class="qa-who ${advanced ? "adv" : ""}" title="${esc(label + " · " + modelName(turn.model) + why + pageWhy)}">${icon}<span>${esc(label)}</span><i>${esc(modelName(turn.model) + page + secs + stopped)}</i></span>${tools}</div>`;
  }

  function turnCopyTools(turn, index) {
    if (!turn.done || !String(turn.a || "").trim()) return "";
    return `<span class="qa-turn-tools"><button type="button" class="qa-tcopy" data-copy-turn="${index}" title="${esc(tr("Копировать ответ", "Copy the answer"))}">${ICON.copy}<span>${esc(tr("Копировать", "Copy"))}</span></button>`
      + `<button type="button" class="qa-tcopy qa-tcopy-more" data-copy-format="${index}" aria-haspopup="menu" aria-expanded="false" title="${esc(tr("Копировать как…", "Copy as…"))}" aria-label="${esc(tr("Копировать как…", "Copy as…"))}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m7 10 5 5 5-5"/></svg></button></span>`;
  }

  const authError = (turn) => turn.errorCode !== "vision" && /\bHTTP 40[13]\b|\b40[13]\b.*(key|ключ)|API[- ]?(key|ключ)/i.test(String(turn.error || ""));
  function turnHtml(turn, index, last) {
    const parts = [];
    const qText = turn.q || (turn.selection ? tr("Выделенный текст", "Selected text") : (turn.storedImages || (turn.images || []).length ? tr("Изображение", "Image") : ""));

    const pics = (turn.images || []).filter(Boolean).slice(0, 4);
    const picsHtml = pics.length ? `<span class="qa-q-pics" data-nocopy="1">${pics.map((src) => `<img src="${esc(src)}" alt="${esc(tr("Изображение", "Image"))}">`).join("")}</span>` : "";
    parts.push(`<div class="qa-q"><span>${esc(qText)}</span>${turn.storedImages ? `<em>🖼 ${turn.storedImages}</em>` : picsHtml}</div>`);
    if (turn.instant) parts.push(`<div class="qa-instant">${ICON.spark}<div><b>${esc(turn.instant.text || "")}</b>${turn.instant.source ? `<small>${esc(turn.instant.source)}${turn.instant.note ? " · " + esc(turn.instant.note) : ""}</small>` : ""}</div></div>`);
    if (turn.status && !turn.done && !turn.a) {
      const icon = turn.status.status === "searching" || turn.status.tool === "web_search" ? ICON.search
        : turn.status.tool === "take_screenshot" ? ICON.camera : turn.status.tool === "read_page" ? ICON.page
          : turn.status.tool === "escalate_to_advanced" ? ICON.brain : ICON.spark;
      parts.push(`<div class="qa-status">${icon}<span>${esc(turn.status.label || tr("Думаю…", "Thinking…"))}</span><span class="qa-dots"><i></i><i></i><i></i></span></div>`);

      if (turn.status.status === "reasoning" && turn.status.text) parts.push(`<div class="qa-think-live" aria-live="off">${esc(turn.status.text)}</div>`);
    }
    if (turn.askForms && turn.askForms.length && window.MemuAskForm) parts.push(`<div class="au-stack qa-ask">${turn.askForms.map((r) => MemuAskForm.html(r, { compact: true })).join("")}</div>`);
    if (turn.reasoning && turn.done) parts.push(`<details class="qa-think"${turn.thinkOpen ? " open" : ""}><summary>${ICON.brain}<span>${esc(tr("Ход мысли", "Reasoning"))}</span></summary><div class="qa-think-body">${esc(turn.reasoning)}</div></details>`);
    if (turn.a) {
      const html = mdHtml(turn);
      parts.push(`<div class="qa-md" ${!turn.done && last ? "data-streaming" : ""}>${html}</div>`);
    }
    for (const shot of turn.shots || []) parts.push(`<img class="qa-shot-img" alt="${esc(tr("Снимок экрана", "Screenshot"))}" src="${shot}">`);
    if (turn.sources && turn.sources.length) {
      parts.push(`<div class="qa-sources">${turn.sources.map((s, i) => {
        let host = ""; try { host = new URL(s.url).host.replace(/^www\./, ""); } catch (e) { host = s.url; }
        return `<a class="qa-source" href="${esc(s.url)}" target="_blank" rel="noreferrer" title="${esc(s.title || s.url)}"><i>${i + 1}</i><span>${esc(host)}</span></a>`;
      }).join("")}</div>`);
    }
    if (turn.error) {
      const vision = turn.errorCode === "vision";
      parts.push(`<div class="qa-error"><div><b>${esc(vision ? (turn.visionGuess ? tr("Возможно, модель не видит изображения", "The model may not see images") : tr("Модель не видит изображения", "The model cannot see images")) : tr("Не получилось", "Something went wrong"))}.</b> ${esc(turn.error)}</div>
        <div class="qa-err-actions">${vision ? `${turn.visionModel ? `<button type="button" class="qa-btn accent" data-err="probe">${esc(tr("Проверить зрение", "Check vision"))}</button><button type="button" class="qa-btn" data-err="force">${esc(tr("Отправить как есть", "Send anyway"))}</button>` : ""}<button type="button" class="qa-btn ${turn.visionModel ? "" : "accent"}" data-err="model">${esc(tr("Выбрать модель", "Choose a model"))}</button><button type="button" class="qa-btn" data-err="drop">${esc(tr("Без картинки", "Without the image"))}</button>`
          : `<button type="button" class="qa-btn" data-err="retry">${ICON.retry}${esc(tr("Повторить", "Retry"))}</button>`}
        ${turn.errorCode === "no_model" || authError(turn) ? `<button type="button" class="qa-btn" data-err="settings">${esc(tr("Открыть настройки", "Open settings"))}</button>` : ""}</div></div>`);
    }

    if (!turn.error && !(last && turn.done)) parts.push(tierChip(turn, true, turnCopyTools(turn, index)));
    return `<div class="qa-turn" data-turn="${index}">${parts.join("")}</div>`;
  }

  const mdCache = new WeakMap();
  function mdHtml(turn) {
    const key = (turn.done ? "1" : "0") + S.lang + turn.a;
    const hit = mdCache.get(turn);
    if (hit && hit.key === key) return hit.html;
    const html = md.render(turn.a, { streaming: !turn.done, labels: { copy: tr("Копировать", "Copy") } });
    mdCache.set(turn, { key, html });
    return html;
  }
  const nodeSrc = (n) => (n.nodeType === 1 ? n.outerHTML : n.nodeType === 3 ? "#" + n.data : "");
  const nodeShell = (n) => {
    if (n.nodeType !== 1) return "";
    const shell = n.cloneNode(false);
    shell.removeAttribute("data-streaming");
    return shell.outerHTML;
  };
  function markSrc(host, depth) {
    for (const n of host.childNodes) { n._qaSrc = nodeSrc(n); if (depth > 1 && n.nodeType === 1) markSrc(n, depth - 1); }
  }

  function patchHtml(host, html, depth = 2) {
    const tpl = document.createElement("template");
    tpl.innerHTML = html;
    const next = Array.from(tpl.content.childNodes);
    for (const n of next) n._qaSrc = nodeSrc(n);
    const cur = Array.from(host.childNodes).filter((n) => n.nodeType === 1 || n.nodeType === 3);
    const same = (a, b) => a._qaSrc !== undefined && a._qaSrc === b._qaSrc;
    const min = Math.min(cur.length, next.length);
    let head = 0;
    while (head < min && same(cur[head], next[head])) head += 1;
    let tail = 0;
    while (tail < min - head && same(cur[cur.length - 1 - tail], next[next.length - 1 - tail])) tail += 1;

    const curMid = cur.slice(head, cur.length - tail);
    const before = tail ? cur[cur.length - tail] : null;
    next.slice(head, next.length - tail).forEach((n, k) => {
      const c = curMid[k];
      if (c && depth > 0 && c.nodeType === 1 && n.nodeType === 1 && nodeShell(c) === nodeShell(n)) {
        patchHtml(c, n.innerHTML, depth - 1);
        c._qaSrc = n._qaSrc;
        if (n.hasAttribute("data-streaming")) c.setAttribute("data-streaming", ""); else c.removeAttribute("data-streaming");
      } else if (c) host.replaceChild(n, c);
      else host.insertBefore(n, before);
    });
    for (const c of curMid.slice(next.length - tail - head)) c.remove();
  }

  function syncTurns(box, items) {
    const kids = Array.from(box.children);
    items.forEach((it, i) => {
      let node = kids[i];
      if (node && node._qaGen === it.html) return;
      if (node && node.classList.contains("qa-turn") && node.dataset.turn === String(it.index)) {
        patchHtml(node, it.html.slice(it.html.indexOf(">") + 1, -"</div>".length), 2);
      } else {
        const tpl = document.createElement("template");
        tpl.innerHTML = it.html;
        const fresh = tpl.content.firstElementChild;
        markSrc(fresh, 2);
        if (node) box.replaceChild(fresh, node); else box.appendChild(fresh);
        node = fresh;
      }
      node._qaGen = it.html;
    });
    for (const extra of kids.slice(items.length)) extra.remove();
  }
  let renderCost = 0;

  function renderAnswer() {
    const started = performance.now();
    try { renderAnswerNow(); } finally { renderCost = performance.now() - started; }
  }
  function renderAnswerNow() {
    const box = $("qa-answer");
    const shown = shownTurns();
    if (!shown.length || S.pickerOpen || S.chatsOpen || S.menuOpen) { box.hidden = true; box.innerHTML = ""; box._qaHtml = ""; return; }

    if (shown.length !== answerFollow.turns) { answerFollow.turns = shown.length; answerFollow.on = true; }
    const stick = box.hidden || answerFollow.on;
    box.hidden = false;
    const lastShown = shown[shown.length - 1];

    box.setAttribute("aria-busy", String(!lastShown.done));
    const items = [];
    S.turns.forEach((turn, i) => { if (!liveCard(turn)) items.push({ index: i, html: turnHtml(turn, i, turn === lastShown) }); });
    const html = items.map((it) => it.html).join("");

    if (html === box._qaHtml && box.childElementCount) { if (stick) box.scrollTop = box.scrollHeight; return; }
    box._qaHtml = html;
    box.querySelectorAll(".qa-caret").forEach((n) => n.remove());
    syncTurns(box, items);

    if (window.MemuTables) MemuTables.hydrate(box, null, { compact: true, onSendBack: null });
    if (window.MemuDiagrams) MemuDiagrams.hydrate(box);
    box.querySelectorAll("[data-copy-code]").forEach((btn) => {
      btn.onclick = () => {
        const code = btn.closest(".code-block-wrap").querySelector("pre").innerText.replace(/\n$/, "");
        copy(code, false).then(() => { btn.textContent = tr("Скопировано", "Copied"); btn.classList.add("copied");
          setTimeout(() => { btn.textContent = tr("Копировать", "Copy"); btn.classList.remove("copied"); }, 1400); });
      };
    });
    box.querySelectorAll("[data-copy-turn]").forEach((btn) => { btn.onclick = () => copyTurn(S.turns[+btn.dataset.copyTurn], btn); });
    box.querySelectorAll("[data-copy-format]").forEach((btn) => { btn.onclick = () => copyFormats(S.turns[+btn.dataset.copyFormat], btn); });
    box.querySelectorAll("[data-err]").forEach((btn) => {
      btn.onclick = () => {
        const what = btn.dataset.err;
        if (what === "model") openPicker();
        else if (what === "drop") {

          const turn = S.turns.pop(); S.attachments = []; S.selection = turn.selection || "";
          if (turn.page) { S.page = turn.page; S.pageOn = true; }
          renderAttachments(); $("qa-input").value = turn.q; send({ readPage: !!turn.page });
        }
        else if (what === "retry") retry();
        else if (what === "settings") windowAction("open_settings");
        else if (what === "force") { const turn = S.turns[S.turns.length - 1]; if (turn && turn.visionModel) { forcedVision.add(turn.visionModel); retry(); } }
        else if (what === "probe") { const turn = S.turns[S.turns.length - 1]; if (turn && turn.visionModel) probeVision(turn.visionModel, btn).then((sees) => { if (sees) retry(); }); }
      };
    });
    box.querySelectorAll("details.qa-think").forEach((d) => {
      d.ontoggle = () => { const node = d.closest(".qa-turn[data-turn]"); const turn = node && S.turns[+node.dataset.turn]; if (turn) turn.thinkOpen = d.open; scheduleSize(); };
    });
    box.querySelectorAll("img").forEach((img) => { if (!img.complete) img.onload = scheduleSize; });
    placeCaret(box.querySelector(".qa-md[data-streaming]"));
    if (stick) box.scrollTop = box.scrollHeight;
    answerFollow.watch(box.lastElementChild);
  }

  const answerFollow = { on: true, turns: 0, watch() {} };
  (function pinAnswerOnResize() {
    const box = $("qa-answer");
    if (!box || typeof ResizeObserver !== "function") return;
    let userUntil = 0;
    const byUser = () => { userUntil = performance.now() + 700; };
    for (const ev of ["wheel", "touchmove", "pointerdown", "keydown"]) box.addEventListener(ev, byUser, { passive: true });
    box.addEventListener("scroll", () => {
      if (performance.now() < userUntil) answerFollow.on = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    }, { passive: true });
    const pin = () => { if (answerFollow.on && !box.hidden) box.scrollTop = box.scrollHeight; };
    const ro = new ResizeObserver(pin);
    ro.observe(box);
    let watched = null;
    answerFollow.watch = (el) => {
      if (watched === el) return;
      if (watched) ro.unobserve(watched);
      watched = el || null;
      if (watched) ro.observe(watched);
    };
  })();

  function placeCaret(container) {
    if (!container) return;
    let node = container;
    for (;;) {
      const last = node.lastElementChild;
      if (!last) break;
      if (last.classList.contains("code-block-wrap")) { node = last.querySelector("pre code") || last; break; }
      if (last.matches("p, li, ul, ol, blockquote, h3, h4, h5, h6, .table-wrap, table, tbody, tr, td")) { node = last; continue; }
      break;
    }
    node.insertAdjacentHTML("beforeend", '<span class="qa-caret" aria-hidden="true"></span>');
  }

  function renderActions() {
    const box = $("qa-actions");
    const last = shownTurns().pop();

    const show = !!(last && last.done && String(last.a || "").trim() && !S.busy && !S.pickerOpen && !S.chatsOpen && !S.menuOpen);
    box.hidden = !show;
    if (!show) { box.innerHTML = ""; return; }
    const hasText = !!String(last.a || "").trim();
    const canAdvance = !!advancedModel() && last.tier !== "advanced" && !last.restored && hasText;
    const extras = Array.from(answerActions.values()).filter((d) => hasText && !pluginValue(d.hidden));
    const act = (id, icon, label, title, cls = "") => `<button type="button" class="qa-act ${cls}" id="${id}" title="${esc(title)}" aria-label="${esc(label)}">${icon}<span>${esc(label)}</span></button>`;
    box.innerHTML = (last.error ? '<span class="qa-meta"></span>' : tierChip(last, false)) + [
      canAdvance ? act("qa-advance", ICON.brain, tr("Умнее", "Smarter"), tr(`Спросить умную модель · ${modelName(advancedModel())} (Ctrl+Shift+A)`, `Ask the smart model · ${modelName(advancedModel())} (Ctrl+Shift+A)`), "adv") : "",
      hasText ? `<span class="qa-split">${act("qa-copy", ICON.copy, tr("Копировать", "Copy"), tr("Копировать ответ (Ctrl+C или Ctrl+Shift+C)", "Copy the answer (Ctrl+C or Ctrl+Shift+C)"), "lbl")}`
        + `<button type="button" class="qa-act qa-act-caret" id="qa-copy-as" aria-haspopup="menu" aria-expanded="false" title="${esc(tr("Копировать как: с оформлением, Markdown, простой текст", "Copy as: formatted, Markdown, plain text"))}" aria-label="${esc(tr("Копировать как…", "Copy as…"))}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m7 10 5 5 5-5"/></svg></button></span>` : "",
      hasText && !(last.selectionAction && last.selectionAction.replace) ? act("qa-insert", ICON.insert, tr("Вставить", "Insert"), tr("Вставить ответ в программу, из которой открыто окно", "Paste the answer into the program you came from"), "lbl") : "",
      last.restored || last.error ? "" : act("qa-retry", ICON.retry, tr("Ещё раз", "Again"), tr("Спросить ещё раз", "Ask again")),

      ...extras.filter((d) => d.primary).map((d) => act("qa-x-" + d.id, String(d.icon || ICON.spark), String(pluginValue(d.label) || d.id), String(pluginValue(d.title) || pluginValue(d.label) || d.id), "lbl")),
      `<button type="button" class="qa-act" id="qa-act-more" aria-haspopup="menu" aria-expanded="false" title="${esc(tr("Ещё: открыть в MemuAI, стикер, заметка…", "More: open in MemuAI, sticker, note…"))}" aria-label="${esc(tr("Ещё действия с ответом", "More answer actions"))}">${ICON.more}</button>`,
    ].join("");
    if ($("qa-copy")) $("qa-copy").onclick = () => copyAnswer($("qa-copy"));
    if ($("qa-copy-as")) $("qa-copy-as").onclick = () => copyFormats(last, $("qa-copy-as"));
    if ($("qa-insert")) $("qa-insert").onclick = insertAnswer;
    if ($("qa-advance")) $("qa-advance").onclick = askAdvanced;
    if ($("qa-retry")) $("qa-retry").onclick = () => retry();
    const runExtra = (d) => { try { d.run(last, MemuQuick); } catch (e) { console.warn("[MemuQuick] answer action", d.id, e); } };
    for (const d of extras.filter((x) => x.primary)) { const btn = $("qa-x-" + d.id); if (btn) btn.onclick = () => runExtra(d); }

    $("qa-act-more").onclick = () => {
      const items = [{ id: "qa-open", icon: ICON.open, label: tr("Открыть в MemuAI", "Open in MemuAI"), keys: ["Ctrl", "Shift", "↵"], run: openInMemuAI },
        ...extras.filter((d) => !d.primary).map((d) => ({ id: "qa-x-" + d.id, icon: String(d.icon || ICON.spark), label: String(pluginValue(d.label) || d.id), sub: pluginValue(d.title) && pluginValue(d.title) !== pluginValue(d.label) ? String(pluginValue(d.title)) : "", run: () => runExtra(d) }))];
      if (hasText && window.QuickCopy) items.push({ id: "qa-copy-md", icon: ICON.text, label: tr("Копировать как Markdown", "Copy as Markdown"), run: () => QuickCopy.copyMarkdown(last.a, "markdown") });
      if (window.QuickCopy) QuickCopy.openMenu($("qa-act-more"), { items }); else openInMemuAI();
    };
    fitActions();
  }

  function fitActions() {
    const box = $("qa-actions");
    if (!box || box.hidden) return;
    box.classList.remove("qa-actions-tight");
    const meta = box.querySelector(".qa-meta");
    if (box.scrollWidth > box.clientWidth + 1 || (meta && meta.textContent.trim() && meta.getBoundingClientRect().width < 96)) box.classList.add("qa-actions-tight");
  }

  function starterIdeas() {
    const hour = new Date().getHours();
    const ideas = [
      { icon: ICON.camera, text: tr("Что у меня на экране?", "What's on my screen?"), kind: "screen" },
      { icon: ICON.globe, text: hour < 12 ? tr("Погода сегодня", "Weather today") : tr("Главные новости сегодня", "Today's top news"), kind: "ask" },
      { icon: ICON.translate, text: tr("Перевести текст…", "Translate text…"), kind: "fill", fill: tr("Переведи на английский: ", "Translate into Russian: ") },
    ];
    if (S.prefs.quick_ask_screenshot_tool === false || !NATIVE) ideas.shift();
    return ideas.slice(0, S.history.length ? 2 : 3);
  }

  const SLASH_ALIASES = {
    new: "new новый очистить clear", chats: "chats чаты история history", copy: "copy копировать", insert: "insert вставить paste",
    advance: "smart умнее умная", dfy: "do сделай dfy агент", open: "open открыть memuai", shot: "shot screen снимок скрин экран",
    "shot-all": "мониторы monitors", file: "file файл картинка image", model: "model модель", route: "route авто быстро умно",
    web: "web веб поиск search", pin: "pin закрепить", center: "center центр место", hide: "hide скрыть", settings: "settings настройки",
    keys: "keys клавиши help помощь справка", "x:computer-use": "computer компьютер пк мышь cu",
  };

  const slashQuery = () => { const v = $("qa-input").value; return /^\/[^\n]*$/.test(v) && !/^\/(image|img|video)\s/i.test(v) ? v.slice(1).trim().toLowerCase() : null; };
  function slashRows(query) {
    const saved = S.menuFilter;
    S.menuFilter = "";
    let items;
    try { items = menuItems(); } finally { S.menuFilter = saved; }
    const words = query.split(/\s+/).filter(Boolean);
    return items.filter((it) => !it.disabled && words.every((w) => `${it.label} ${it.section} ${it.sub || ""} ${SLASH_ALIASES[it.id] || ""}`.toLowerCase().includes(w)))
      .map((it) => ({ section: it.section, icon: it.icon, text: it.label, keys: it.keys || [], run: () => { setInput(""); it.run(); } }));
  }
  function suggestionRows() {
    const rows = [];
    const slash = slashQuery();
    if (slash !== null) return slashRows(slash);
    if (selectionOwned()) return rows;
    const ctx = contextActions();
    ctx.forEach(([icon, label, prompt], i) => rows.push({ section: S.selection ? tr("Выделенный текст", "Selected text") : tr("Эта страница", "This page"),
      icon, text: label, keys: i < 9 ? ["Alt", String(i + 1)] : [], run: () => runContextAction(i) }));
    if (!ctx.length) {

      if (S.recentChats.length) {
        S.recentChats.slice(0, 3).forEach((chat) => rows.push({ section: tr("Недавние", "Recent"), icon: chat.page ? ICON.page : ICON.clock,
          text: chat.title || chat.preview || tr("Без названия", "Untitled"), keys: [], run: () => openChat(chat.id) }));
      } else {

        S.history.filter((row, i, all) => all.findIndex((x) => x.q === row.q) === i).slice(0, 3).forEach((row, i) => rows.push({ section: tr("Недавние", "Recent"), icon: ICON.clock, text: row.q, keys: i === 0 ? ["↑"] : [],
          run: () => { setInput(row.q); focusInput(true); } }));
      }
      if (S.prefs.quick_ask_history !== false && (S.history.length || S.recentChats.length)) rows.push({ section: tr("Недавние", "Recent"), icon: ICON.chats, text: tr("Все чаты", "All chats"), keys: ["Ctrl", "H"], muted: true, run: () => openChats() });
      for (const idea of starterIdeas()) {
        rows.push({ section: tr("Попробуйте", "Try"), icon: idea.icon, text: idea.text.trim(), keys: [], run: () => {

          if (idea.kind === "screen") { takeScreenshot(false).then((ok) => { if (ok) { setInput(idea.text); send(); } }); return; }
          setInput(idea.fill || idea.text);
          if (idea.kind === "ask") send(); else focusInput(true);
        } });
      }
    }
    return rows;
  }
  function renderSuggestions() {
    const box = $("qa-suggest");
    const input = $("qa-input");
    const slash = slashQuery() !== null && !S.pickerOpen && !S.chatsOpen && !S.menuOpen && !S.busy;
    const idle = !shownTurns().length && !S.pickerOpen && !S.chatsOpen && !S.menuOpen && !S.busy;
    const show = slash || (idle && !input.value.trim());
    box.hidden = !show;
    if (!show) { box.innerHTML = ""; S.listRows = []; S.listIndex = -1; return; }
    const rows = suggestionRows();
    if (slash && !rows.length) {
      box.innerHTML = `<div class="qa-hints">${esc(tr("Нет такой команды — Enter отправит текст как вопрос", "No such command — Enter sends the text as a question"))}</div>`;
      S.listRows = []; S.listIndex = -1; return;
    }
    if (slash && S.listIndex < 0) S.listIndex = 0;
    if (!rows.length && S.ui.onboarded && selectionOwned()) { box.hidden = true; box.innerHTML = ""; S.listRows = []; S.listIndex = -1; return; }
    S.listRows = rows;
    if (S.listIndex >= rows.length) S.listIndex = rows.length - 1;
    const tip = !S.ui.onboarded ? `<div class="qa-tip" role="note"><span class="ic">${ICON.tip}</span><span class="tx"><b>${esc(tr("Спросите что угодно — или поручите дело", "Ask anything — or give it a task"))}</b>
      <span>${tr("<kbd>↵</kbd> отправить · <kbd>Ctrl K</kbd> все действия · <kbd>Esc</kbd> скрыть. Окно можно перетащить за пустое место.",
        "<kbd>↵</kbd> send · <kbd>Ctrl K</kbd> all actions · <kbd>Esc</kbd> hide. Drag the window by any empty spot.")}</span></span>
      <button type="button" data-tip-ok>${esc(tr("Понятно", "Got it"))}</button></div>` : "";
    let section = "";
    const html = [];
    rows.forEach((row, i) => {
      if (row.section !== section) { section = row.section; html.push(`<div class="qa-section">${esc(section)}</div>`); }
      html.push(`<button type="button" role="option" class="qa-row ${i === S.listIndex ? "active" : ""}" data-row="${i}" aria-selected="${i === S.listIndex}">
        <span class="ic">${row.icon}</span><span class="tx" ${row.muted ? 'style="color:var(--muted)"' : ""}>${esc(row.text)}</span>
        ${row.keys.length ? `<span class="keys">${row.keys.map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span>` : ""}</button>`);
    });

    const hints = !slash && S.ui.onboarded ? `<div class="qa-hints">${tr("<kbd>/</kbd> команды · <kbd>Ctrl K</kbd> все действия · перетащите файл или картинку · <kbd>Esc</kbd> скрыть",
      "<kbd>/</kbd> commands · <kbd>Ctrl K</kbd> all actions · drop a file or an image · <kbd>Esc</kbd> hide")}</div>` : "";
    box.innerHTML = tip + html.join("") + hints;
    const ok = box.querySelector("[data-tip-ok]");
    if (ok) ok.onclick = () => dismissTip();
    box.querySelectorAll("[data-row]").forEach((btn) => {
      btn.onclick = () => runRow(+btn.dataset.row);
      btn.onmousemove = () => { const i = +btn.dataset.row; if (i !== S.listIndex) { S.listIndex = i; box.querySelectorAll("[data-row]").forEach((x) => x.classList.toggle("active", x === btn)); } };
    });
    box.onmouseleave = () => { if (S.listIndex !== -1 && document.activeElement === input) { S.listIndex = -1; box.querySelectorAll("[data-row]").forEach((x) => x.classList.remove("active")); } };
    reveal(box, box.querySelector(".qa-row.active"));
  }
  function runRow(index) {
    const row = S.listRows[index];
    if (!row) return false;
    S.listIndex = -1;
    row.run();
    return true;
  }
  function moveList(step) {
    const n = S.listRows.length;
    if (!n || $("qa-suggest").hidden) return false;
    const next = S.listIndex + step;
    if (next < -1 || next >= n) return S.listIndex !== -1;
    S.listIndex = next;
    renderSuggestions();
    return true;
  }
  function dismissTip() {
    S.ui.onboarded = true;
    api("/api/quick/ui", { body: { onboarded: true } }).catch(() => {});
    renderSuggestions(); scheduleSize(); focusInput();
  }

  function renderAll() {
    const card = $("qa-card");
    card.dataset.busy = S.busy ? "1" : "0";
    $("qa-pin").setAttribute("aria-pressed", String(!!S.pinned));
    $("qa-pin").hidden = !NATIVE;
    card.dataset.pinned = S.pinned ? "1" : "0";
    const inChat = S.turns.length > 0;
    $("qa-new").hidden = !inChat;
    $("qa-new").disabled = S.busy;
    $("qa-history").hidden = inChat;
    $("qa-history").setAttribute("aria-pressed", String(!!S.chatsOpen));
    $("qa-shot").hidden = false;
    $("qa-send").disabled = !S.busy && !$("qa-input").value.trim() && !S.attachments.length && !S.selection;
    renderAnswer();
    renderActions();
    renderSuggestions();
    renderContextActions();
    applyStaticText();
    setView();
    scheduleSize();
  }

  function setView() {
    $("qa-card").dataset.view = S.pickerOpen ? "picker" : S.chatsOpen ? "chats" : S.menuOpen ? "menu" : S.turns.length > 0 ? "answer"
      : (!$("qa-suggest").hidden || !$("qa-attach").hidden || hasVisiblePanel() ? "list" : "bare");
  }
  function hasVisiblePanel() {
    for (const entry of panels.values()) if (entry.visible && entry.slot !== "overlay") return true;
    return false;
  }

  function scheduleRender() {
    if (S.renderTimer) return;

    S.renderTimer = setTimeout(() => { S.renderTimer = 0; renderAnswer(); scheduleSize(); }, Math.min(400, Math.max(40, renderCost * 3)));
  }

  let sizeQueued = false, sizeTimer = 0, lastSizeAt = 0;
  function contentHeight() {
    const inner = $("qa-inner");
    return inner ? Math.ceil(inner.getBoundingClientRect().height) : 0;
  }
  function scheduleSize() {
    queueFitBar();
    if (sizeQueued) return;
    sizeQueued = true;
    requestAnimationFrame(() => {
      sizeQueued = false;
      const wait = Math.max(0, 60 - (performance.now() - lastSizeAt));
      clearTimeout(sizeTimer);
      sizeTimer = setTimeout(reportSize, wait);
    });
  }
  function reportSize() {
    lastSizeAt = performance.now();
    const height = contentHeight();
    if (!height || Math.abs(height - S.lastHeight) < 2) return;
    S.lastHeight = height;
    if (!NATIVE) return;
    pinPage();

    const bridge = window.pywebview && window.pywebview.api;
    const viaServer = () => windowAction("resize", { height, animate: !reducedMotion() });
    if (bridge && typeof bridge.resize === "function") {
      try { Promise.resolve(bridge.resize(height, !reducedMotion())).then((ok) => { if (ok === false) viaServer(); }, viaServer); return; } catch (e) {  }
    }
    viaServer();
  }

  function setInput(text) {
    const input = $("qa-input");
    input.value = text;
    S.historyIndex = -1;
    autoGrow(); renderCalc(); renderSuggestions();
    $("qa-send").disabled = !S.busy && !input.value.trim() && !S.attachments.length && !S.selection;
    scheduleSize();
  }
  function autoGrow() {
    const input = $("qa-input");
    input.style.height = "auto";
    input.style.height = Math.min(168, input.scrollHeight) + "px";
  }
  function renderCalc() {
    const pill = $("qa-calc");
    const value = $("qa-input").value;
    const result = window.QuickCalc && !S.selection ? QuickCalc.evaluate(value, S.lang) : null;
    if (!result) { pill.hidden = true; pill.dataset.value = ""; return; }
    const changed = pill.dataset.value !== result.text;
    pill.dataset.value = result.text;
    pill.innerHTML = `= ${esc(result.text)} <small>${esc(tr("Tab — вставить", "Tab — insert"))}</small>`;
    pill.title = tr("Щелчок — копировать", "Click to copy");
    if (changed) { pill.hidden = true; void pill.offsetWidth; }
    pill.hidden = false;
  }
  function focusInput(toEnd = false) {
    const input = $("qa-input");
    if (S.pickerOpen && $("qa-picker-q")) { $("qa-picker-q").focus(); return; }
    if (S.chatsOpen && $("qa-chats-q")) { $("qa-chats-q").focus(); return; }
    if (S.menuOpen && $("qa-menu-q")) { $("qa-menu-q").focus(); return; }
    input.focus({ preventScroll: true });
    if (toEnd) { const n = input.value.length; input.setSelectionRange(n, n); }
  }

  function historyStep(direction) {
    if (!S.history.length) return false;
    const input = $("qa-input");
    if (S.historyIndex === -1) S.historyDraft = input.value;
    const next = S.historyIndex + direction;
    if (next < -1 || next >= S.history.length) return false;
    S.historyIndex = next;
    input.value = next === -1 ? S.historyDraft : S.history[next].q;
    autoGrow(); renderCalc();
    const n = input.value.length; input.setSelectionRange(n, n);
    renderAll();
    return true;
  }

  function conversationHistory() {
    const out = [];
    for (const turn of S.turns) {
      if (!turn.done || turn.error || !turn.a) continue;
      out.push({ role: "user", content: (turn.selection ? `<selected_text>\n${turn.selection}\n</selected_text>\n\n` : "") + (turn.prompt || turn.q) || tr("Что на изображении?", "What is in the image?"), turn });
      out.push({ role: "assistant", content: turn.a });
    }
    const recent = out.slice(-12);

    const withPics = recent.filter((row) => row.turn && (row.turn.images || []).some(Boolean)).pop();
    return recent.map((row) => {
      const { turn, ...plain } = row;
      return row === withPics ? { ...plain, images: turn.images.filter(Boolean) } : plain;
    });
  }

  async function send(options = {}) {

    if (S.busy) { if (performance.now() - (S.sentAt || 0) > 600) stop(); return; }
    const input = $("qa-input");
    const text = input.value.trim();
    if (!text && !S.attachments.length && !S.selection) return;
    closePickerSilently(); closeChats(false); closeMenu(false);

    const selAction = options.selectionAction && S.selection ? { ...options.selectionAction } : null;
    const page = S.page && S.pageOn && !selAction ? { ...S.page } : null;

    let prevented = false;

    if (!selAction && !options.direct) emit("submit", { text, images: S.attachments.length, selection: S.selection, page, preventDefault: () => { prevented = true; } });
    if (prevented) {
      if (text) S.history = [{ q: text }, ...S.history.filter((row) => row.q !== text)].slice(0, 60);
      input.value = ""; autoGrow(); renderCalc(); renderAll();
      return;
    }
    if (!S.chatId) { S.chatId = newChatId(); S.chatCreated = Date.now(); }
    const model = effectiveModel();
    const images = S.attachments.map((a) => a.data);

    const turn = { q: text, prompt: options.prompt ? String(options.prompt).slice(0, 20000) : "", images: images.slice(), selection: S.selection, selectionInfo: S.selectionInfo, selectionAction: selAction, page, a: "", model, tier: "fast",
      status: { status: "thinking", label: tr("Думаю…", "Thinking…") }, sources: [], shots: [], done: false, error: "", errorCode: "",
      startedAt: performance.now(), t: Date.now(), forced: options.tier || "" };
    const history = options.history || conversationHistory();
    S.turns.push(turn);
    S.attachments = []; S.selection = ""; S.selectionInfo = null; S.historyIndex = -1;
    input.value = ""; autoGrow(); renderCalc(); renderAttachments();
    if (text && !turn.prompt && !selAction) {
      S.history = [{ q: text }, ...S.history.filter((row) => row.q !== text)].slice(0, 60);
    }
    S.busy = true; S.controller = new AbortController(); S.sentAt = performance.now();
    renderAll();
    emit("answer-start", { turn: S.turns.length - 1, text });
    if (!model) {
      finishTurn(turn, { error: tr("Выберите модель: Настройки → Быстрый доступ или основная модель в Настройках → Модели.", "Choose a model: Settings → Quick Ask, or the default model in Settings → Models."), code: "no_model" });
      return;
    }
    const adv = advancedModel();
    const tier = options.tier || (S.sessionModel ? "fast" : S.routing);
    const visionModel = tier === "advanced" && adv ? adv : model;
    if (images.length && !seesImages(visionModel) && !(tier === "auto" && seesImages(model))) {
      turn.visionModel = visionModel;
      turn.visionGuess = visionGuess(visionModel);
      finishTurn(turn, { error: turn.visionGuess
        ? tr(`По названию «${modelName(visionModel)}» похожа на модель «только текст», но это лишь догадка. Проверьте зрение (один короткий запрос с картинкой) или отправьте как есть.`,
          `By its name «${modelName(visionModel)}» looks text-only, but that is only a guess. Check its vision (one short request with a picture) or send anyway.`)
        : tr("В быстром доступе нет отдельной модели для распознавания изображений — выберите модель, которая понимает и текст, и картинки, или уберите снимок.",
          "Quick Ask has no separate image-recognition model — pick a model that understands both text and images, or remove the image."), code: "vision" });
      return;
    }
    const row = modelRow(model), advRow = modelRow(adv);
    try {
      const response = await fetch("/api/quick/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: S.controller.signal,
        body: JSON.stringify({ text: turn.prompt || text, images, selection: turn.selection, model, model_explicit: !!S.sessionModel, history, lang: S.lang,
          tier, caps: { vision: seesImages(model), search: !!(row && row.caps && row.caps.search) && S.web },
          caps_advanced: { vision: adv ? seesImages(adv) : true, search: !!(advRow && advRow.caps && advRow.caps.search) && S.web },
          web: S.web, page, read_page: !!options.readPage, selection_action: selAction, contexts: selAction ? [] : collectContexts(),
          effort: effortOf(tier === "advanced" && adv ? adv : model),
          client_tools: Array.from(clientTools.values()).map((d) => ({ name: d.name, description: d.description })) }),
      });
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || ("HTTP " + response.status)); }
      await readEvents(response.body, (event) => handleEvent(turn, event));

      if (!turn.done) finishTurn(turn, turn.a ? { cut: true } : { error: tr("Ответ оборвался.", "The answer was cut off.") });
    } catch (e) {
      if (e.name === "AbortError") finishTurn(turn, { stopped: true });
      else if (e instanceof TypeError && /network|fetch|load failed/i.test(e.message || "")) {

        if (turn.a) finishTurn(turn, { cut: true });
        else finishTurn(turn, { error: tr("Связь с MemuAI прервалась. Проверьте, что MemuAI запущен, и повторите.", "The connection to MemuAI was lost. Make sure MemuAI is running and retry.") });
      }
      else finishTurn(turn, { error: e.message || String(e) });
    }
  }

  async function readEvents(body, onEvent) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const dispatch = (block) => {
      const data = block.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
      if (!data) return;
      try { onEvent(JSON.parse(data)); } catch (e) {  }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop();
      blocks.forEach(dispatch);
    }

    buffer += decoder.decode();
    if (buffer.trim()) dispatch(buffer);
  }

  async function runClientTool(event) {
    const def = clientTools.get(String(event.tool || ""));
    let result;
    try { result = def ? await def.run(event.args || {}, MemuQuick) : { error: "unknown tool" }; }
    catch (e) { result = { error: e.message || String(e) }; }
    api("/api/quick/tool_result", { body: { id: event.id, tool: String(event.tool || ""), result: result === undefined ? { ok: true } : result } }).catch(() => {});
  }

  function askUserForm(turn, event) {
    if (!window.MemuAskForm || !event.form) return;
    MemuAskForm.setLang(() => S.lang);
    const rec = { id: String(event.id), form: event.form, status: "pending", answers: null };
    (turn.askForms = turn.askForms || []).push(rec);
    turn.status = null;
    MemuAskForm.mount(rec, { compact: true, rerender: () => renderAll(), onSubmit: async (answers, skipped) => {
      const res = await fetch(`/api/pg/ask_user/${encodeURIComponent(rec.id)}/${skipped ? "skip" : "answer"}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(skipped ? {} : { answers }) });
      const data = await res.json().catch(() => ({}));
      if (res.status === 422) return { errors: data.errors || [] };
      if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
      rec.status = data.record?.status || (skipped ? "skipped" : "answered"); rec.answers = data.record?.answers || null;
      MemuAskForm.update(rec); renderAll();
      return data;
    } });
    setTimeout(() => { const el = document.querySelector(`[data-au-form="${CSS.escape(rec.id)}"] .au-chip, [data-au-form="${CSS.escape(rec.id)}"] .au-input`); if (el) el.focus({ preventScroll: true }); scheduleSize(); }, 50);
  }

  function handleEvent(turn, event) {
    switch (event.event) {
      case "status": turn.status = event; break;
      case "route": turn.tier = event.tier || "fast"; turn.model = event.model || turn.model; turn.escalated = !!event.escalated; turn.reason = event.reason || ""; break;
      case "page":
        turn.pageSource = event.source || "";

        turn.pageInfo = { ms: +event.ms || 0, tokens: +event.tokens || 0, truncated: !!event.truncated, cached: !!event.cached };

        if (event.url || event.title) turn.page = { ...(turn.page || {}), url: event.url || "", title: event.title || "" };
        break;
      case "delta": turn.a += event.text || ""; turn.status = null; emit("answer-delta", { text: event.text || "", turn: S.turns.indexOf(turn) }); break;
      case "reset": turn.a = ""; break;
      case "sources": {
        const known = new Set(turn.sources.map((s) => s.url));
        for (const item of event.items || []) if (item.url && !known.has(item.url)) { turn.sources.push(item); known.add(item.url); }
        turn.sources = turn.sources.slice(0, 6);
        break;
      }
      case "instant": turn.instant = event.answer; break;
      case "image": turn.shots.push(event.data); break;
      case "client_tool": turn.status = { status: "tool", label: event.label || event.tool }; runClientTool(event); break;
      case "tool_ui": turn.cardShown = true; emit("tool-ui", { tool: event.tool, ui: event.ui, turn: S.turns.indexOf(turn) }); break;

      case "ask_user": askUserForm(turn, event); break;
      case "ask_user_done": {
        const rec = (turn.askForms || []).find((r) => r.id === event.id);
        if (rec) { rec.status = event.status || "answered"; rec.answers = event.answers || null; if (window.MemuAskForm) MemuAskForm.update(rec); }
        turn.status = { status: "thinking", label: tr("Думаю…", "Thinking…") };
        break;
      }

      case "memory": toast(event.facts && event.facts.length ? (en() ? "Remembered: " : "Запомнил: ") + String(event.facts[0]).slice(0, 48)
        : (en() ? "A fact awaits your approval in Memory" : "Факт ждёт подтверждения в Памяти"), "ok", 2600); break;
      case "done":
        if (event.tier) turn.tier = event.tier; if (event.model) turn.model = event.model;
        if (event.reasoning) turn.reasoning = String(event.reasoning);

        if (!String(turn.a || "").trim() && !String(event.text || "").trim() && !turn.cardShown) {
          finishTurn(turn, { error: tr("Модель закончила без видимого ответа. Повторите или выберите другую модель.", "The model finished without a visible answer. Retry or pick another model."), code: "empty", ms: event.ms });
          return;
        }
        finishTurn(turn, { text: event.text, ms: event.ms }); return;
      case "error": if (event.reasoning) turn.reasoning = String(event.reasoning); finishTurn(turn, { error: event.error, code: event.code }); return;
      default: return;
    }
    scheduleRender();
  }

  function finishTurn(turn, { error = "", code = "", stopped = false, cut = false, ms = 0 } = {}) {
    if (turn.done) return;
    turn.done = true; turn.status = null;
    turn.error = error; turn.errorCode = code; turn.stopped = !!stopped; turn.cut = !!cut;
    turn.ms = ms || Math.round(performance.now() - turn.startedAt);
    if (stopped && !turn.a) turn.error = tr("Остановлено.", "Stopped.");
    S.busy = false; S.controller = null;
    renderAll();
    emit("answer-done", { text: turn.a, turn: S.turns.indexOf(turn), model: turn.model, tier: turn.tier, error: turn.error });
    if (!error && turn.q && turn.a && !turn.selectionAction && S.prefs.quick_ask_history !== false) {
      api("/api/quick/history", { body: { q: turn.q, a: turn.a.slice(0, 6000), model: turn.model } }).catch(() => {});
    }
    if (turn.a) saveChat();
  }

  function stop() {
    if (!S.controller) return;
    S.controller.abort();

    const running = S.turns.find((t) => !t.done && !t.card);
    if (running) finishTurn(running, { stopped: true });
  }
  function retry(options = {}) {
    if (S.busy) return;
    const last = shownTurns().pop();
    if (!last) return;
    S.turns.splice(S.turns.lastIndexOf(last), 1);
    $("qa-input").value = last.q;
    S.attachments = (last.images || []).filter(Boolean).map((data) => ({ data, label: "" }));
    S.selection = last.selection || "";
    S.selectionInfo = last.selectionInfo || null;
    if (last.page) { S.page = S.page && S.page.id === last.page.id ? S.page : last.page; S.pageOn = true; }
    renderAttachments();
    send({ ...(last.selectionAction ? { selectionAction: last.selectionAction } : {}), ...(last.prompt ? { prompt: last.prompt } : {}), ...options });
  }
  function askAdvanced() {
    if (!advancedModel()) return;
    const last = S.turns[S.turns.length - 1];
    if (!last || !last.done || S.busy || last.card) return;
    retry({ tier: "advanced", readPage: !!last.page });
  }
  function clearAll() {
    newChat({ toast: false });
  }

  async function copy(text, notify = true) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
      const r = await windowAction("copy", { text }); ok = !!r.ok;
      if (!ok) {
        const area = document.createElement("textarea"); area.value = text; document.body.appendChild(area); area.select();
        try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
        area.remove();
      }
    }
    if (notify) toast(ok ? tr("Скопировано", "Copied") : tr("Не удалось скопировать", "Could not copy"), ok ? "ok" : "err");
    return ok;
  }
  const lastAnswer = () => { const t = shownTurns().pop(); return t && t.done ? String(t.a || "").trim() : ""; };

  async function copyTurn(turn, btn) {
    const text = turn && turn.done ? String(turn.a || "").trim() : "";
    if (!text) return false;
    const ok = window.QuickCopy ? await QuickCopy.copyMarkdown(text) : await copy(text);
    if (ok && btn) {
      const html = btn.innerHTML;
      btn.classList.add("done"); btn.innerHTML = `${ICON.check}<span>${esc(tr("Скопировано", "Copied"))}</span>`;
      setTimeout(() => { if (btn.isConnected) { btn.classList.remove("done"); btn.innerHTML = html; } }, 1300);
    }
    return ok;
  }
  function copyFormats(turn, btn) {
    const text = turn && turn.done ? String(turn.a || "").trim() : "";
    if (text && window.QuickCopy) QuickCopy.openFormats(btn, text);
  }
  async function copyAnswer(btn) {
    const turn = shownTurns().pop();
    if (turn && turn.done) await copyTurn(turn, btn);
  }
  async function insertAnswer() {
    const text = lastAnswer();
    if (!text) return;
    const r = await windowAction("insert", { text });
    if (!r.ok) { await copy(text, false); toast(tr("Скопировано — вставьте Ctrl+V", "Copied — paste with Ctrl+V"), "ok"); }
  }

  async function openInMemuAI() {
    const messages = [];
    for (const turn of S.turns) {
      if (!turn.q && !(turn.images || []).filter(Boolean).length && !turn.selection) continue;
      const pageLine = turn.page && turn.page.url ? `[${turn.page.title || turn.page.url}](${turn.page.url})\n\n` : "";
      const user = { role: "user", content: pageLine + (turn.selection ? `> ${turn.selection.replace(/\n/g, "\n> ")}\n\n` : "") + turn.q };
      const imgs = (turn.images || []).filter(Boolean);

      if (imgs.length) user.attachments = imgs.map((data, i) => {
        const type = (/^data:(image\/[\w.+-]+)[;,]/.exec(data) || [])[1] || "image/jpeg";
        return { name: `image-${i + 1}.${({ "image/jpeg": "jpg", "image/svg+xml": "svg" })[type] || type.slice(6)}`, data, type };
      });
      messages.push(user);
      if (turn.a) messages.push({ role: "assistant", content: turn.a, model: turn.model });
    }
    const draft = $("qa-input").value.trim();
    if (!messages.length && draft) messages.push({ role: "user", content: draft });
    if (!messages.length) { await windowAction("handoff", { handoff: null }); return; }

    const answered = [...S.turns].reverse().find((t) => t.a && t.model);
    const handoff = { title: (S.turns[0] && S.turns[0].q) || draft, model: (answered && answered.model) || S.model, messages };

    if (S.busy) stop();
    const r = await windowAction("handoff", { handoff });
    if (!r.ok && !NATIVE) toast(tr("В приложении MemuAI этот разговор откроется как обычный чат", "In the MemuAI app this opens as a regular chat"), "ok");
  }

  let toastTimer = 0;
  function toast(text, kind = "", ms = 2200) {
    const el = $("qa-toast");
    el.textContent = text;
    el.className = "qa-toast show " + kind;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "qa-toast " + kind; }, ms);
  }

  const CLOSE_FADE_MS = 110, CLOSE_SAFETY_MS = 300, CLOSE_SLOW_MS = 500;
  let closing = null;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function bridgeHide() {
    const bridge = window.pywebview && window.pywebview.api;
    if (!bridge || typeof bridge.hide !== "function") return null;
    try { return Promise.resolve(bridge.hide(true)).then((ok) => ({ ok: ok !== false, via: "bridge" }), () => null); } catch (e) { return null; }
  }
  const serverHide = () => windowAction("hide").then((r) => ({ ok: !!(r && r.ok), via: "server" }));
  function fadeOut(card) {
    if (reducedMotion()) return Promise.resolve();
    card.classList.add("qa-leaving");
    return new Promise((resolve) => {
      const inner = $("qa-inner");
      const done = (e) => {
        if (e && e.target !== card && e.target !== inner) return;
        card.removeEventListener("transitionend", done); clearTimeout(timer); resolve();
      };
      const timer = setTimeout(done, CLOSE_FADE_MS + 40);
      card.addEventListener("transitionend", done);
    });
  }
  async function closeWindow() {
    if (!NATIVE) return;
    if (closing) return closing.promise;
    const card = $("qa-card");
    const started = performance.now();
    const state = {};
    closing = state;
    state.promise = (async () => {
      try {
        await fadeOut(card);
        let via = "bridge";
        let hidden = bridgeHide();
        if (!hidden) { via = "server"; hidden = serverHide(); }
        let result = await Promise.race([hidden, sleep(CLOSE_SAFETY_MS).then(() => null)]);
        if (!result && via === "bridge") {
          via = "bridge+server";
          hidden = Promise.race([hidden.then((r) => (r && r.ok ? r : new Promise(() => {}))), serverHide()]);
          result = await Promise.race([hidden, sleep(CLOSE_SAFETY_MS).then(() => null)]);
        }
        const report = (r, force = false) => {
          const took = performance.now() - started;
          if (force || took > CLOSE_SLOW_MS) {
            windowAction("close_slow", { ms: Math.round(took), via: (r && r.via) || via,
              reason: [S.busy && "busy", !(r && r.ok) && "not-hidden"].filter(Boolean).join(" ") });
          }
        };
        if (result && result.ok) { report(result); return; }

        card.classList.remove("qa-leaving");
        if (closing === state) closing = null;
        report(null, true);
        hidden.then((r) => r && r.ok && report(r), () => {});
      } finally {
        card.classList.remove("qa-leaving");
        if (closing === state) closing = null;
      }
    })();
    return state.promise;
  }
  async function togglePin() {
    S.pinned = !S.pinned;
    renderAll();
    const r = await windowAction("pin", { pinned: S.pinned });
    if (r && typeof r.pinned === "boolean") S.pinned = r.pinned;
    renderAll();

    const back = String(S.prefs.quick_ask_on_leave || "stay") === "back";
    toast(S.pinned ? tr("Закреплено поверх всех окон", "Pinned on top of every window")
      : (back ? tr("Откреплено — окно уходит назад при переходе в другую программу", "Unpinned — steps back when you switch programs")
        : tr("Откреплено — Esc в любой программе закроет окно", "Unpinned — Escape in any program closes it")), "", 2600);
  }

  const DOCK_LABEL = {
    top: ["Прилипнет к верхнему краю", "Docks to the top edge"], bottom: ["Прилипнет к нижнему краю", "Docks to the bottom edge"],
    left: ["Прилипнет к левому краю", "Docks to the left edge"], right: ["Прилипнет к правому краю", "Docks to the right edge"],
    "top-left": ["В левый верхний угол", "Top-left corner"], "top-right": ["В правый верхний угол", "Top-right corner"],
    "bottom-left": ["В левый нижний угол", "Bottom-left corner"], "bottom-right": ["В правый нижний угол", "Bottom-right corner"],
  };
  function onDrag(info) {
    const hint = $("qa-dock-hint");
    const card = $("qa-card");
    card.classList.toggle("qa-dragging", !!info);
    const dock = info && info.dock;
    hint.dataset.dock = dock || "";
    hint.textContent = dock && DOCK_LABEL[dock] ? tr(DOCK_LABEL[dock][0], DOCK_LABEL[dock][1]) : "";
    hint.classList.toggle("show", !!(dock && DOCK_LABEL[dock]));
    return true;
  }
  function onPlaced(info = {}) {
    S.dock = info.dock || "";
    if (info.maxHeight) { S.maxHeight = info.maxHeight; root.style.setProperty("--qa-max-h", info.maxHeight + "px"); }
    root.dataset.anchor = info.anchor || "top";
    S.lastHeight = 0;
    scheduleSize();
    return true;
  }

  async function loadConfig() {
    let config = null;
    try {
      config = await api("/api/quick/config");
      const first = !S.prefs || !Object.keys(S.prefs).length;
      S.prefs = config.prefs || {};
      S.status = config.status || null;
      S.endpoint = config.endpoint || null;
      if (config.ui && typeof config.ui === "object") {
        S.ui = { onboarded: config.ui.onboarded !== false };
        if (config.ui.efforts && typeof config.ui.efforts === "object") S.efforts = { ...config.ui.efforts };
      }
      applyAppearance(S.prefs);
      if (!S._webTouched) S.web = S.prefs.quick_ask_web_search !== false;
      if (first || !S._routingTouched) S.routing = ["auto", "fast", "advanced"].includes(S.prefs.quick_ask_routing) ? S.prefs.quick_ask_routing : "auto";
      if (config.status && typeof config.status.pinned === "boolean") S.pinned = config.status.pinned;
    } catch (e) {  }
    if (customSource()) {

      S.models = (S.endpoint.models || []).map((m) => (typeof m === "string" ? { id: m, caps: {} } : m)).filter((m) => m && m.id && isChatModel(m));
      const extra = [S.prefs.quick_ask_endpoint_model, S.prefs.quick_ask_endpoint_advanced_model].filter(Boolean);
      for (const id of extra) if (!S.models.some((m) => m.id === id)
        && !(S.endpoint.models || []).some(m => typeof m === "object" && m?.id === id && !isChatModel(m))) S.models.push({ id, caps: {} });
    } else {
      try {
        const state = await api("/api/pg/state");

        S.models = (state.models || []).filter((m) => isChatModel(m) && !(state.image_models || []).includes(m.id) && !(state.video_models || []).includes(m.id));
      } catch (e) {  }
    }
    if (S.sessionModel && !S.models.some((m) => m.id === S.sessionModel)) S.sessionModel = "";
    renderModelChip();
    renderAll();
    emit("config", { prefs: { ...S.prefs } });
  }
  async function loadHistory() {
    try { S.history = (await api("/api/quick/history")).items || []; } catch (e) { S.history = []; }
    await loadRecentChats();
  }
  async function loadRecentChats() {
    if (S.prefs.quick_ask_history === false) S.recentChats = [];
    else {
      try { S.recentChats = (await api("/api/quick/chats?limit=3")).items || []; } catch (e) { S.recentChats = []; }
    }
    renderSuggestions(); scheduleSize();
  }

  const QuickAsk = {
    onShow(payload = {}) {
      S.shownAt = performance.now();
      S.openSeq = typeof payload.seq === "number" ? payload.seq : null;
      const card = $("qa-card");
      card.classList.remove("qa-closed", "qa-leaving", "qa-enter");
      S.shownOnce = true;
      if (payload.backdrop) root.dataset.backdrop = payload.backdrop; else delete root.dataset.backdrop;
      root.dataset.rounded = payload.rounded === false ? "0" : "1";

      if (payload.corner) root.dataset.corner = payload.corner;
      if (typeof payload.radius === "number") { S.radius = payload.radius; root.style.setProperty("--qa-native-radius", payload.radius + "px"); }
      root.dataset.anchor = payload.anchor || "top";
      if (payload.maxHeight) { S.maxHeight = payload.maxHeight; root.style.setProperty("--qa-max-h", payload.maxHeight + "px"); }
      if (typeof payload.pinned === "boolean") S.pinned = payload.pinned;
      S.dock = payload.dock || "";
      if (payload.fresh) {
        stop();
        if (S.turns.length) saveChat();
        S.turns = []; S.attachments = []; S.sessionModel = ""; S._webTouched = false; S._routingTouched = false;
        S.web = S.prefs.quick_ask_web_search !== false; $("qa-input").value = "";
        S.chatId = newChatId(); S.chatCreated = Date.now();
        S.routing = ["auto", "fast", "advanced"].includes(S.prefs.quick_ask_routing) ? S.prefs.quick_ask_routing : "auto";
        emit("chat-new", { id: S.chatId });
      }
      if (!S.chatId) { S.chatId = newChatId(); S.chatCreated = Date.now(); }
      S.selection = payload.selection || "";
      S.selectionInfo = S.selection && payload.selection_info ? payload.selection_info : null;
      S.selWait = payload.selection_pending ? { turns: S.turns.length, at: performance.now() } : null;
      if (payload.source !== "api") { S.page = payload.page || null; S.pageOn = !!S.page; }
      S.historyIndex = -1; S.listIndex = -1;
      closePickerSilently(); closeChats(false); closeMenu(false);
      autoGrow(); renderCalc(); renderAttachments(); renderAll();
      void card.offsetWidth;
      card.classList.add("qa-enter");
      S.lastHeight = 0;
      loadConfig().then(() => {
        if (payload.source !== "api" && payload.source !== "restore" && S.prefs.quick_ask_auto_screenshot && !S.attachments.length) takeScreenshot(false);
      });
      loadHistory();
      emit("open", { source: payload.source || "", fresh: !!payload.fresh, page: S.page });
      emit("selection", { text: S.selection, info: S.selectionInfo });
      return true;
    },

    selectionPending() { S.selWait = { turns: S.turns.length, at: performance.now() }; return true; },
    onSelection(payload = {}) {
      if (payload.late) {
        const wait = S.selWait;
        S.selWait = null;
        if (!wait || S.turns.length !== wait.turns || (S.sentAt || 0) >= wait.at || S.selection) return false;
      }
      if (!payload.text) return false;
      setSelection(payload.text, payload.info || null);
      return true;
    },
    onHide(seq) {

      if (typeof seq === "number" && typeof S.openSeq === "number" && seq !== S.openSeq) return false;
      S.selWait = null;
      $("qa-card").classList.add("qa-closed");
      closePickerSilently(); closeChats(false); closeMenu(false);
      onDrag(null);
      emit("close", {});
      return true;
    },

    onPage(page, replace = false) {
      if (replace && !page) {
        if (S.page) { S.page = null; S.pageOn = false; renderAttachments(); applyStaticText(); emit("page", { page: null }); }
        return true;
      }
      if (!page) return true;
      if (!replace && S.page && page.id && S.page.id && page.id !== S.page.id) return true;
      const wasOn = S.pageOn || !S.page || (replace && S.page.id !== page.id);
      S.page = page; S.pageOn = wasOn;
      renderAttachments(); applyStaticText();
      emit("page", { page });
      return true;
    },
    onDrag, onPlaced,
    contentHeight,
    focusInput() { focusInput(true); return true; },

    state: () => S,
    render: () => { renderModelChip(); renderAttachments(); renderAll(); return contentHeight(); },
  };
  window.QuickAsk = QuickAsk;

  function bind() {
    const input = $("qa-input");
    $("qa-form").addEventListener("submit", (e) => { e.preventDefault(); send(); });
    input.addEventListener("input", () => {
      S.historyIndex = -1; S.listIndex = -1;
      autoGrow(); renderCalc();
      $("qa-send").disabled = !S.busy && !input.value.trim() && !S.attachments.length && !S.selection;
      const wasHidden = $("qa-suggest").hidden;
      renderSuggestions();
      if (wasHidden !== $("qa-suggest").hidden) renderAll(); else scheduleSize();
    });
    input.addEventListener("keydown", (e) => {
      if (e.isComposing) return;
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        if (S.listIndex >= 0 && !input.value.trim() && runRow(S.listIndex)) return;
        if (slashQuery() !== null && S.listRows.length && runRow(Math.max(0, S.listIndex))) return;

        if (S.busy) { toast(tr("Дождитесь ответа — или Esc, чтобы остановить", "Wait for the answer — or press Esc to stop"), "", 2600); return; }
        send();
        return;
      }
      if (e.key === "Tab" && !e.shiftKey && $("qa-calc").dataset.value && !$("qa-calc").hidden) {
        e.preventDefault(); input.value = $("qa-calc").dataset.value.replace(/\s(?=\d)/g, "").replace(/\u00a0/g, ""); autoGrow(); renderCalc(); return;
      }

      if (e.key === "ArrowDown" && S.historyIndex === -1 && (!input.value.trim() || slashQuery() !== null) && !e.shiftKey) {
        if (moveList(1)) { e.preventDefault(); return; }
      }
      if (e.key === "ArrowUp" && S.listIndex >= 0 && !e.shiftKey) { moveList(-1); e.preventDefault(); return; }
      if (e.key === "ArrowUp" && !e.shiftKey && (input.selectionStart === 0 || !input.value.includes("\n")) && (!input.value || S.historyIndex !== -1)) {
        if (historyStep(1)) e.preventDefault();
        return;
      }
      if (e.key === "ArrowDown" && S.historyIndex !== -1 && !input.value.slice(input.selectionEnd).includes("\n")) {
        if (historyStep(-1)) e.preventDefault();
      }
      if (e.key === "Backspace" && !input.value && S.page && S.pageOn && !S.attachments.length) { e.preventDefault(); S.pageOn = false; renderAttachments(); renderAll(); }
    });
    input.addEventListener("paste", (e) => {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => /^image\//.test(f.type));
      if (files.length) { e.preventDefault(); addFiles(files); return; }
      if (e.clipboardData && pasteLong(e.clipboardData.getData("text/plain"))) e.preventDefault();
    });
    document.addEventListener("keydown", (e) => {
      if (e.defaultPrevented) return;
      if (e.key === "Escape") {

        e.preventDefault();
        if (S.pickerOpen) { closePicker(); return; }
        if (S.chatsOpen) { closeChats(); return; }
        if (S.menuOpen) { closeMenu(); return; }
        if (S.listIndex >= 0) { S.listIndex = -1; renderSuggestions(); return; }
        let taken = false;
        emit("escape", { preventDefault: () => { taken = true; } });
        if (taken) return;
        if (S.busy) { stop(); return; }
        closeWindow();
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.altKey && /^Arrow/.test(e.key)) {
        e.preventDefault();
        const dock = { ArrowUp: "top", ArrowDown: "bottom", ArrowLeft: "left", ArrowRight: "right" }[e.key];
        windowAction("dock", { dock });
        return;
      }
      if (mod && !e.shiftKey && !e.altKey && e.code === "KeyN") { e.preventDefault(); newChat(); return; }
      if (mod && !e.shiftKey && !e.altKey && e.code === "KeyH") { e.preventDefault(); S.chatsOpen ? closeChats() : openChats(); return; }
      if (mod && !e.shiftKey && !e.altKey && e.code === "KeyP") { e.preventDefault(); if (NATIVE) togglePin(); return; }
      if (mod && !e.shiftKey && !e.altKey && e.code === "KeyK") { e.preventDefault(); S.menuOpen ? closeMenu() : openMenu(); return; }
      if (mod && e.shiftKey && e.code === "KeyK") { e.preventDefault(); clearAll(); return; }

      if (mod && e.shiftKey && e.key === "Enter") { e.preventDefault(); openInMemuAI(); return; }
      if (mod && e.key === "Enter") { e.preventDefault(); if (window.QuickDFY) window.QuickDFY.run(); else openInMemuAI(); return; }
      if (mod && e.shiftKey && e.code === "KeyC") { e.preventDefault(); copyAnswer(null); return; }
      if (mod && e.shiftKey && e.code === "KeyA") { e.preventDefault(); askAdvanced(); return; }
      if (e.altKey && !mod && e.code === "KeyM") { e.preventDefault(); S.pickerOpen ? closePicker() : openPicker(); return; }
      if (e.altKey && !mod && e.code === "KeyR") { e.preventDefault(); S._routingTouched = true; cycleRouting(); return; }
      if (e.altKey && !mod && /^Digit[1-9]$/.test(e.code)) {
        if (shownTurns().length || S.pickerOpen || S.chatsOpen || S.menuOpen) return;
        if (runContextAction(+e.code.slice(5) - 1)) e.preventDefault();
        return;
      }
      if (mod && e.shiftKey && e.code === "KeyS") { e.preventDefault(); takeScreenshot(false); return; }
    });

    const editable = (el) => !!(el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)));
    const ownUi = (el) => !!(el && el.closest && el.closest(".qa-slot, [role=menu], [role=dialog]:not(#qa-card), .qc-menu, .mt-modal-back"));
    document.addEventListener("keydown", (e) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || String(e.key || "").length !== 1) return;
      const a = document.activeElement;
      if (editable(a) || ownUi(a) || S.pickerOpen || S.chatsOpen || S.menuOpen) return;
      if (e.key === " " && a && /^(BUTTON|A|SUMMARY)$/.test(a.tagName)) return;
      focusInput(true);
    });
    document.addEventListener("paste", (e) => {
      const a = document.activeElement;
      if (e.defaultPrevented || editable(a) || ownUi(a) || !e.clipboardData) return;
      const files = Array.from(e.clipboardData.files || []).filter((f) => /^image\//.test(f.type));
      if (files.length) { e.preventDefault(); addFiles(files); return; }
      const text = e.clipboardData.getData("text/plain");
      if (!text || S.pickerOpen || S.chatsOpen || S.menuOpen) return;
      e.preventDefault();
      if (pasteLong(text)) return;
      const input = $("qa-input");
      focusInput(true);
      input.setRangeText(text, input.selectionStart, input.selectionEnd, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    $("qa-calc").onclick = () => copy($("qa-calc").dataset.value || "");
    $("qa-model").onclick = () => (S.pickerOpen ? closePicker() : openPicker());
    $("qa-more").onclick = () => (S.menuOpen ? closeMenu() : openMenu());
    $("qa-shot").onclick = (e) => takeScreenshot(e.shiftKey);
    $("qa-file-input").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
    $("qa-pin").onclick = togglePin;
    $("qa-new").onclick = () => newChat();
    $("qa-history").onclick = () => (S.chatsOpen ? closeChats() : openChats());
    const card = $("qa-card");
    card.addEventListener("dragover", (e) => { if (Array.from(e.dataTransfer.items || []).some((i) => i.kind === "file")) { e.preventDefault(); card.classList.add("qa-drop"); } });
    card.addEventListener("dragleave", (e) => { if (!card.contains(e.relatedTarget)) card.classList.remove("qa-drop"); });
    card.addEventListener("drop", () => card.classList.remove("qa-drop"));
    card.addEventListener("drop", (e) => { if (e.dataTransfer.files.length) { e.preventDefault(); addFiles(e.dataTransfer.files); } });

    const startDrag = (e) => {
      if (!NATIVE || e.button !== 0) return;
      e.preventDefault();
      windowAction("drag");
    };
    for (const handle of [$("qa-drag"), card.querySelector(".qa-orb"), $("qa-grow")]) {
      if (!handle) continue;
      handle.classList.add("qa-handle");
      handle.addEventListener("pointerdown", startDrag);
      handle.addEventListener("dblclick", (e) => { e.preventDefault(); windowAction("dock", { dock: "center" }); });
    }

    window.addEventListener("focus", () => {
      const a = document.activeElement;

      const sel = window.getSelection();
      const selected = !!(sel && !sel.isCollapsed && String(sel).trim() && !(a && /^(INPUT|TEXTAREA)$/.test(a.tagName)));
      if (!selected && (!a || a === document.body || a === $("qa-input"))) focusInput();
      if (NATIVE && !S.pinned) windowAction("active");
    });

    let awayTimer = 0;
    window.addEventListener("blur", () => {
      clearTimeout(awayTimer);
      if (!NATIVE || S.pinned || S.keepOnTop || String(S.prefs.quick_ask_on_leave || "stay") !== "back") return;
      awayTimer = setTimeout(() => { if (!document.hasFocus() && performance.now() - S.shownAt > 400) windowAction("inactive"); }, 160);
    });

    document.addEventListener("dragover", (e) => { if (Array.from((e.dataTransfer && e.dataTransfer.items) || []).some((i) => i.kind === "file")) e.preventDefault(); });
    document.addEventListener("drop", (e) => { if (e.dataTransfer && e.dataTransfer.files.length && !e.defaultPrevented) { e.preventDefault(); addFiles(e.dataTransfer.files); } });

    document.querySelectorAll(".qa-edge").forEach((edge) => {
      edge.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        edge.setPointerCapture(e.pointerId);
        const startX = e.screenX, startW = window.innerWidth, side = edge.dataset.edge === "left" ? -1 : 1;
        let width = startW, frame = 0;
        const move = (ev) => {
          width = Math.round(startW + side * (ev.screenX - startX) * 2);
          if (frame) return;
          frame = requestAnimationFrame(() => { frame = 0; windowAction("resize", { height: contentHeight(), width, animate: false }); });
        };
        const up = () => {
          edge.removeEventListener("pointermove", move); edge.removeEventListener("pointerup", up); edge.removeEventListener("pointercancel", up);
          cancelAnimationFrame(frame); frame = 0;
          windowAction("resize", { height: contentHeight(), width, animate: false, persist: true });
        };
        edge.addEventListener("pointermove", move); edge.addEventListener("pointerup", up); edge.addEventListener("pointercancel", up);
      });
    });
    if (window.ResizeObserver) new ResizeObserver(scheduleSize).observe($("qa-inner"));
    if (window.ResizeObserver) new ResizeObserver(() => queueFitBar()).observe($("qa-bar"));
    if (window.ResizeObserver) new ResizeObserver(() => fitActions()).observe($("qa-actions"));

    if (window.ResizeObserver) {
      let inputWidth = 0;
      new ResizeObserver(([entry]) => {
        const w = Math.round(entry.contentRect.width);
        if (w !== inputWidth) { inputWidth = w; if (!$("qa-input").value) autoGrow(); }
      }).observe($("qa-input"));
    }
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(queueFitBar).catch(() => {});
    window.addEventListener("resize", scheduleSize);
    const scheme = matchMedia("(prefers-color-scheme: dark)");
    if (scheme.addEventListener) scheme.addEventListener("change", reportSurface);
  }

  bind();
  applyStaticText();
  autoGrow();
  renderAll();
  loadConfig();
  loadHistory();
  focusInput();
  loadPlugins();
})();
