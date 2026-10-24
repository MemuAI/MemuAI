/* MemuAI module host.
 * One window shell, one bridge, one settings workspace. The three shipped apps
 * are packages on this surface, not screens of the chat.
 */
"use strict";

const MemuModules = (() => {
  let catalog = [];
  let openId = "";
  let policy = null;
  let templates = null;
  let openJob = 0;

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  // Round 33: this page is a module's own window (/?module_app=<id>, module-apps.js) — no geometry/«open» bookkeeping.
  const appMode = () => (typeof document !== "undefined" && document.documentElement && document.documentElement.dataset
    && document.documentElement.dataset.moduleApp) || "";
  const MA = () => (typeof window !== "undefined" ? window.MemuModuleApps : null);
  const t = (ru, english) => (en() ? english : ru);
  const $ = (selector, node) => (node || document).querySelector(selector);
  const $$ = (selector, node) => Array.from((node || document).querySelectorAll(selector));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

  const SLOTS = [
    { id: "sidebar", ru: "Боковая панель", en: "Sidebar" },
    { id: "chat-list", ru: "Над списком чатов", en: "Above the chat list" },
    { id: "topbar", ru: "Верхняя полоса", en: "Top bar" },
    { id: "composer", ru: "У поля ввода", en: "Composer" },
  ];

  // Действие моста → нужное право. Совпадает с ACTION_PERMISSION в
  // app/modules_host.py; тест modules_security_test сверяет обе таблицы.
  const ACTION_PERMISSION = {
    request: "chat", stream: "chat", chat_history: "chat",
    image: "media", video: "media", speak: "media", transcribe: "media",
    record: "media", music: "media", monitor: "background", save: "storage", models: "models", extract: "files",
    download: "download", handoff: "download", notify: "notify",
    open: "external", search: "web", fetch: "web",
    clipboard: "clipboard", clipboard_read: "clipboard",
    complete: "chat", store: "storage",
    net_import: "net-import",
    music_call: "music",
    learn_call: "learn",   // r40-learn: MemuLearn (app/learn/api.py)
  };

  const KNOWN_ICONS = new Set(["text", "chat", "sparkle", "zap", "image", "work", "bot",
    "settings", "palette", "search", "globe", "list", "clock", "bell", "keyboard", "user",
    "users", "monitor", "terminal", "target", "mic", "headphones", "volume", "download",
    "folder", "info", "play", "undo", "redo", "scissors", "clipboard", "branch", "pin",
    "edit", "trash", "copy", "retry", "attach", "send", "stop", "focus", "brain", "export",
    "check", "temp", "menu", "plus", "more", "close", "moon", "sun", "unread", "sidebar",
    "back", "forward", "retry"]);

  function moduleIcon(name, fallback = "work") {
    // Round 39: a module row → its own icon (module-icons.js: picture or glyph squircle, the freshest catalogue row).
    if (name && typeof name === "object") {
      const row = catalog.find((item) => item.id === name.id) || name;
      if (window.MemuModuleIcon) return window.MemuModuleIcon.html(row);
      name = row.icon;
    }
    const key = KNOWN_ICONS.has(String(name || "")) ? String(name) : fallback;
    return typeof icon === "function" ? icon(key) : "";
  }

  function note(text, type = "ok") {
    try {
      if (typeof toast === "function") toast(text, type);
    } catch (error) { console.warn(text); }
  }

  function titleOf(row) {
    return en() ? (row.title_en || row.title) : (row.title || row.title_en);
  }

  function summaryOf(row) {
    return en() ? (row.summary_en || row.summary) : (row.summary || row.summary_en);
  }

  function permissionList() {
    return (templates && templates.permissions) || ["chat", "media", "storage", "models",
      "files", "download", "notify", "external", "web", "clipboard", "background"];
  }

  function templateList() {
    return (templates && templates.templates) || [];
  }

  function localizedTitle(row) {
    return en() ? (row.title_en || row.title_ru || row.id) : (row.title_ru || row.title_en || row.id);
  }

  function localizedSummary(row) {
    return en() ? (row.summary_en || row.summary_ru || "") : (row.summary_ru || row.summary_en || "");
  }

  function slotList() {
    return (templates && templates.slots && templates.slots.length) ? templates.slots.map((id) => (
      SLOTS.find((slot) => slot.id === id) || { id, ru: id, en: id })) : SLOTS;
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: { "Content-Type": "application/json" },
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  }

  /* --------------------------------- среда --------------------------------- */

  function hostTokens() {
    const style = getComputedStyle(document.documentElement);
    // Computed values, so a user theme reaches the module exactly as painted.
    const names = ["--bg", "--bg-deep", "--panel", "--panel-2", "--border",
      "--control-border", "--text", "--muted", "--accent", "--accent-soft",
      "--on-accent", "--danger", "--ok", "--radius", "--radius-sm", "--radius-md", "--radius-lg",
      "--shadow", "--sans", "--serif", "--mono", "--code-bg", "--code-text", "--code-header",
      "--link", "--selection", "--tok-kw", "--tok-type", "--tok-str", "--tok-com", "--tok-num",
      "--tok-fn", "--tok-op", "--tok-lit", "--tok-tag", "--tok-attr", "--tok-prop", "--tok-var",
      "--tok-meta", "--tok-punct"];
    const tokens = {};
    names.forEach((name) => {
      const value = style.getPropertyValue(name).trim();
      if (value) tokens[name] = value;
    });
    return tokens;
  }

  function hostEnv() {
    const root = document.documentElement;
    const preferences = (typeof S !== "undefined" && S.preferences) || {};
    const theme = root.dataset.theme === "light" ? "light" : "dark";
    return {
      lang: en() ? "en" : "ru",
      theme,
      accent_name: root.dataset.accent || "terracotta",
      surface: preferences[`surface_${theme}`] || root.dataset.surface || "",
      density: root.dataset.density || preferences.ui_density || "normal",
      font_scale: Number(preferences.font_scale) || 100,
      tokens: hostTokens(),
    };
  }

  /* Окна модулей. Каждое открытое окно — запись в wins: своё iframe, своя
     политика размера и своя геометрия. openId — окно, которое сейчас активно. */
  const wins = new Map();
  const closingFrames = new Map();
  let zTop = 10;

  /* Round 30: ?chrome=host — the page knows the window draws its actions
     (Memu.toolbar), so it does not paint its own header. Stale actions of the
     previous page go away until the new one registers its own. */
  function documentUrl(win, reload) {
    if (win) { win.toolbar = null; renderWinToolbar(win); }
    const id = win ? win.id : "";
    return "/api/pg/modules/" + encodeURIComponent(id) + "/document?chrome=host" + (reload ? "&r=" + Date.now() : "");
  }

  function postTo(win, message) {
    if (!win || !win.frame || !win.frame.contentWindow) return;
    try { win.frame.contentWindow.postMessage(message, "*"); } catch (error) { /* окно грузится */ }
  }

  function broadcastTheme() {
    const payload = hostEnv();
    wins.forEach((win) => postTo(win, { type: "memu-module-host", action: "theme", payload }));
    if (window.MemuModInt && window.MemuModInt.theme) window.MemuModInt.theme(payload);   // Round 38: module panels
  }

  function broadcastEvent(name, data) {
    wins.forEach((win) => postTo(win, { type: "memu-module-host", action: "host-event",
      payload: { name, data: data || {} } }));
  }

  /* --------------------------------- каталог -------------------------------- */

  let removedBuiltins = [];

  async function refresh() {
    try {
      const data = await apiJson("/api/pg/modules");
      catalog = data.modules || [];
      removedBuiltins = data.removed || [];
    } catch (error) {
      catalog = [];
    }
    renderLaunchers();
    renderOpenState();
    registerSettingsSearch();
  }

  /* Round 26: the Modules page is drawn only when it is opened, so the settings search (built from
     the DOM) found neither «Создать модуль» nor a module by its name. */
  function registerSettingsSearch() {
    const K = window.MemuSettingsKit;
    if (!K?.registerSearch) return;
    K.registerSearch([
      { page: "modules", label_ru: "Создать модуль", label_en: "Create module", desc_ru: "Своё приложение: HTML, CSS и JavaScript", desc_en: "Your own app: HTML, CSS and JavaScript", keywords: "module модуль create создать app приложение studio", anchor: '#pane-modules [data-modules-tab="create"]' },
      { page: "modules", label_ru: "Импорт модуля", label_en: "Import a module", keywords: "import импорт module модуль file файл", anchor: '#pane-modules [data-modules-tab="import"]' },
      ...catalog.filter((row) => row.title || row.title_en).map((row) => ({ page: "modules", label_ru: row.title || row.title_en, label_en: row.title_en || row.title,
        desc_ru: row.summary || "", desc_en: row.summary_en || row.summary || "", keywords: `module модуль ${row.id}`, anchor: `#pane-modules [data-module-card="${String(row.id).replace(/["\\]/g, "")}"]` })),
    ]);
  }

  async function loadTemplates() {
    if (templates) return templates;
    try {
      templates = await apiJson("/api/pg/modules/templates");
    } catch (error) {
      templates = { templates: [], permissions: [], slots: [], icons: [] };
    }
    return templates;
  }

  function renderLaunchers() {
    const slots = {
      sidebar: $("#module-slot-sidebar"),
      "chat-list": $("#module-slot-chats"),
      topbar: $("#module-slot-topbar"),
      composer: $("#module-slot-composer"),
    };
    Object.values(slots).forEach((node) => { if (node) node.replaceChildren(); });
    catalog
      .filter((row) => row.enabled && row.launcher && !row.launcher.hidden)
      .sort((a, b) => (a.launcher.order || 0) - (b.launcher.order || 0))
      .forEach((row) => {
        const host = slots[row.launcher.slot] || slots.sidebar;
        if (!host) return;
        const button = document.createElement("button");
        button.type = "button";
        const inline = row.launcher.slot !== "sidebar";
        button.className = inline ? "module-launch inline" : "side-btn module-launch";
        button.dataset.moduleOpen = row.id;
        button.title = summaryOf(row) || titleOf(row);
        button.innerHTML = `${moduleIcon(row)}<span class="module-launch-label">${esc(titleOf(row))}</span>`;
        button.onclick = () => open(row.id);
        button.oncontextmenu = (event) => { event.preventDefault(); moduleMenu(event, row); };
        host.appendChild(button);
      });
    markOpenLaunchers();
  }

  function markOpenLaunchers() {
    $$("[data-module-open].module-launch").forEach((button) => {
      button.classList.toggle("is-open", wins.has(button.dataset.moduleOpen));
    });
  }

  function popMenu(event, items) {
    if (typeof showContextMenu !== "function") return;
    // Клик по кнопке всплывает до document, а тот закрывает любое меню.
    if (event && event.stopPropagation) event.stopPropagation();
    if (event && event.preventDefault && event.type === "contextmenu") event.preventDefault();
    let x = event && event.clientX, y = event && event.clientY;
    const anchor = event && event.currentTarget && event.currentTarget.getBoundingClientRect
      ? event.currentTarget : null;
    if (anchor && (event.type === "click" || !x)) {
      const rect = anchor.getBoundingClientRect();
      x = rect.left;
      y = rect.bottom + 4;
    }
    showContextMenu(items, x || 0, y || 0, { anchor });
  }

  function moduleMenu(event, row) {
    const win = wins.get(row.id);
    popMenu(event, [
      { label: win ? t("Показать окно", "Show the window") : t("Открыть", "Open"), icon: "play", run: () => open(row.id) },
      ...(MA() ? MA().menuItems(row, win ? win.id : "") : []),
      ...moduleMenuItems(row),
    ]);
  }

  function moduleMenuItems(row) {
    return [
      { label: t("Настройки модуля", "Module settings"), icon: "settings", run: () => {
        currentSettingsTab = "modules";
        if (typeof showSettings === "function") showSettings();
      } },
      { label: row.resizable === false ? t("Разрешить менять размер", "Allow resizing")
        : t("Запретить менять размер", "Lock the size"), icon: "focus", run: () => toggleResizable(row) },
      { label: t("Вернуть в центр", "Move to the centre"), icon: "target", run: () => recenter(row.id) },
      { label: t("Сменить иконку…", "Change icon…"), icon: "image", run: () => window.MemuModuleIcon && window.MemuModuleIcon.picker(row) },
      { label: t("Скрыть кнопку", "Hide button"), icon: "close", run: async () => {
        try {
          await configure(row.id, { hidden: true });
          await refresh();  // иначе кнопка оставалась до следующего обновления
          note(t("Кнопка скрыта", "Button hidden"), "ok");
        } catch (error) { note(error.message, "err"); }
      } },
    ];
  }

  function reloadWindow(win) {
    if (!win) return;
    if (win.parts.loading) win.parts.loading.hidden = false;
    setStatus(t("Загружаю модуль…", "Loading the module…"), win);
    if (recording && recording.module === win.id) cancelRecording();
    cancelModuleStreams(win.id);
    win.frame.src = documentUrl(win, true);
  }

  /* Round 30: the window's own ⋯ — reload and layouts live here instead of two
     more buttons in the title row (one header: name, module actions, ⋯ — × ). */
  function windowMenu(win, event, inHead) {
    const row = catalog.find((item) => item.id === win.id) || win.row;
    const anchor = event && event.currentTarget;
    const items = [
      { label: t("Перезагрузить модуль", "Reload the module"), icon: "retry", run: () => reloadWindow(win) },
      { label: t("Расположение окон…", "Window layout…"), icon: "sidebar", run: () => { if (anchor && anchor.isConnected) showLayoutMenu(win, anchor); } },
    ];
    if (appMode()) {
      // Round 33: a module's own window — Windows' caption does minimize/maximize/close.
      if (MA()) items.splice(1, 1, ...MA().windowMenuItems(win.id, row));
      items.push({ label: t("Закрыть", "Close"), icon: "close", danger: true,
        hint: hotkeyLabel("mod_close", "Ctrl+W"), run: () => requestClose(win) });
      popMenu(event, items);
      return;
    }
    if (isFullscreen(win)) items.push({ label: t("Обычное окно", "Normal window"), icon: "focus", hint: "Esc", run: () => toggleFull(win) });
    items.push({ label: t("Свернуть на панель модулей", "Minimize to the module bar"), icon: "minus",
      hint: isFullscreen(win) ? "" : "Esc", run: () => minimizeWindow(win) });
    if (MA()) items.push(...MA().menuItems(row || win.row, win.id));
    items.push({ label: t("Закрыть модуль", "Close the module"), icon: "close", danger: true,
      hint: hotkeyLabel("mod_close", "Ctrl+W"), run: () => requestClose(win) });
    items.push({ sep: true }, ...(row ? moduleMenuItems(row) : []));
    popMenu(event, items);
  }

  async function toggleResizable(row) {
    const next = row.resizable === false;
    try {
      await configure(row.id, { resizable: next });
      await refresh();
      const win = wins.get(row.id);
      if (win) {
        const data = await apiJson("/api/pg/modules/window", { method: "POST", body: { id: row.id } });
        win.policy = fitToViewport(data.window);
        refreshResizers(win);
      }
      if ($("#pane-modules [data-view='list']")) renderList($("#pane-modules"));
      note(next ? t("Размер окна можно менять", "The window can be resized")
        : t("Размер окна закреплён", "The window size is locked"), "ok");
    } catch (error) { note(error.message, "err"); }
  }

  function recenter(moduleId) {
    const win = wins.get(moduleId);
    if (!win) return;
    unsnap(win);
    win.el.dataset.moved = "";
    applyRect(win, { center: true });
    persistBox(win).catch(() => {});
  }

  function renderOpenState() {
    wins.forEach((win) => {
      const row = catalog.find((item) => item.id === win.id);
      if (!row || !row.enabled) {
        // Выключенный модуль закрыт насовсем: без open:false окно само
        // открывалось при следующем запуске после повторного включения.
        apiJson("/api/pg/modules/window", { method: "POST", body: { id: win.id, box: currentBox(win), open: false } }).catch(() => {});
        closeWindow(win, true);
      }
    });
  }

  /* ---------------------------------- окно --------------------------------- */

  function layerArea() {
    const layer = $("#module-layer");
    const rect = layer && layer.getBoundingClientRect ? layer.getBoundingClientRect() : null;
    return rect && rect.width ? { width: rect.width, height: rect.height }
      : { width: window.innerWidth, height: window.innerHeight };
  }

  /* ------------------------- рабочая область окон (Раунд 21) ----------------
     Слой окон больше не равен всему окну MemuAI:
     - при собственной строке заголовка (html[data-titlebar="custom"]) верхняя
       полоса до низа верхней панели зарезервирована — окно модуля никогда не
       оказывается под кнопками «свернуть / развернуть / закрыть»;
     - «развёрнутое» окно — это лист в области содержимого рядом с боковой
       панелью (#main-content) с отступами и скруглением, а не весь MemuAI;
     - привязки к краям и раскладки делят ту же область содержимого. */
  const SHEET_MARGIN = 10;
  const TASKBAR_ROOM = 54;

  function visibleNode(node) {
    return !!(node && node.getClientRects && node.getClientRects().length);
  }

  /** Высота зарезервированной полосы заголовка (px от верха окна). */
  function chromeBand() {
    const root = document.documentElement;
    if (!root || root.dataset.titlebar !== "custom") return 0;
    let bottom = 0;
    const bar = document.querySelector(".topbar.appbar");
    if (visibleNode(bar)) bottom = bar.getBoundingClientRect().bottom;
    const controls = document.getElementById("memu-wincontrols");
    // Над полноэкранным оверлеем кнопки уходят в угловую полосу 32 px; полосу
    // берём от верхней панели, чтобы окна не прыгали при открытии настроек.
    if (visibleNode(controls) && root.dataset.titlebarOverlay !== "true") {
      bottom = Math.max(bottom, controls.getBoundingClientRect().bottom);
    }
    return Math.max(32, Math.round(bottom));
  }

  /** Область для развёрнутого листа и привязок — в координатах слоя. */
  function workArea() {
    const layer = $("#module-layer");
    const lr = layer && layer.getBoundingClientRect ? layer.getBoundingClientRect() : null;
    const lw = lr && lr.width ? lr.width : window.innerWidth;
    const lh = lr && lr.height ? lr.height : window.innerHeight;
    const ox = lr && lr.width ? lr.left : 0;
    const oy = lr && lr.width ? lr.top : 0;
    let x = 0, y = 0, right = lw, bottom = lh;
    const content = document.getElementById("main-content") || document.querySelector(".main");
    if (visibleNode(content)) {
      const r = content.getBoundingClientRect();
      x = r.left - ox; y = r.top - oy; right = r.right - ox; bottom = r.bottom - oy;
    }
    x = Math.max(0, x); y = Math.max(0, y);
    right = Math.min(lw, right); bottom = Math.min(lh, bottom);
    if (layer && layer.dataset && layer.dataset.taskbar === "1") bottom = Math.min(bottom, lh - TASKBAR_ROOM);
    // Слишком узкая область (узкое окно, свёрнутое содержимое): лист занимает
    // ширину слоя, иначе модуль стал бы нечитаемым.
    if (right - x < 420) { x = 0; right = lw; }
    if (bottom - y < 280) { y = 0; bottom = layer && layer.dataset && layer.dataset.taskbar === "1" ? lh - TASKBAR_ROOM : lh; }
    return { x: Math.round(x), y: Math.round(y), width: Math.round(right - x), height: Math.round(bottom - y), layerWidth: lw, layerHeight: lh };
  }

  /** Прямоугольник развёрнутого листа. */
  function expandedRect(win) {
    const area = workArea();
    const m = SHEET_MARGIN;
    let x = area.x + m, width = area.width - m * 2;
    const y = area.y + m, height = Math.max(160, area.height - m * 2);
    // Авторский минимум ширины важнее боковой панели: лист растёт влево.
    const minWidth = Math.min(Number(win && win.policy && win.policy.min_width) || 0, area.layerWidth - m * 2);
    if (width < minWidth) {
      const right = x + width;
      width = minWidth;
      x = Math.max(m, right - width);
    }
    return { x: Math.round(x), y: Math.round(y), width: Math.round(Math.max(200, width)), height: Math.round(height) };
  }

  function applyExpanded(win) {
    if (!win || !win.el || !win.el.style || !win.el.style.setProperty) return;
    const rect = expandedRect(win);
    win.el.style.setProperty("--mx-x", rect.x + "px");
    win.el.style.setProperty("--mx-y", rect.y + "px");
    win.el.style.setProperty("--mx-w", rect.width + "px");
    win.el.style.setProperty("--mx-h", rect.height + "px");
  }

  /** Режим движения: off — без анимации, reduced — только короткое затухание. */
  function motionMode() {
    const root = document.documentElement;
    try {
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return "off";
    } catch (error) { /* старый движок */ }
    if (root.dataset.motion === "off" || (document.body && document.body.classList.contains("reduce-motion"))) return "off";
    if (root.dataset.motion === "reduced") return "reduced";
    return "full";
  }

  function motionToken(name, fallback) {
    try {
      const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return value || fallback;
    } catch (error) { return fallback; }
  }

  /** FLIP-переход окна между состояниями: замер → изменение → анимация. */
  function morph(win, mutate) {
    const el = win && win.el;
    if (!el || !el.getBoundingClientRect) { mutate(); return; }
    const mode = motionMode();
    const first = el.hidden ? null : el.getBoundingClientRect();
    mutate();
    if (mode === "off" || !first || !first.width || typeof el.animate !== "function") return;
    const last = el.getBoundingClientRect();
    if (!last.width || !last.height) return;
    if (el._memuMorph) { try { el._memuMorph.cancel(); } catch (error) { /* уже закончилась */ } }
    if (mode === "reduced") {
      el._memuMorph = el.animate([{ opacity: 0.55 }, { opacity: 1 }], { duration: 140, easing: "ease-out" });
      return;
    }
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    const sx = first.width / last.width;
    const sy = first.height / last.height;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;
    const duration = parseFloat(motionToken("--m-dur-3", "240ms")) || 240;
    const easing = motionToken("--m-ease-emph", "cubic-bezier(.05, .7, .1, 1)");
    shieldFrames(true);
    let anim;
    try {
      anim = el.animate([
        { transformOrigin: "0 0", transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})` },
        { transformOrigin: "0 0", transform: "none" },
      ], { duration, easing });
    } catch (error) {
      anim = el.animate([
        { transformOrigin: "0 0", transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})` },
        { transformOrigin: "0 0", transform: "none" },
      ], { duration, easing: "cubic-bezier(.05, .7, .1, 1)" });
    }
    el._memuMorph = anim;
    const done = () => { if (el._memuMorph === anim) el._memuMorph = null; shieldFrames(false); };
    anim.onfinish = done;
    anim.oncancel = done;
  }

  /** Слой: верхняя полоса, развёрнутые и привязанные окна, панель задач. */
  let layoutQueued = false;
  function queueLayout() {
    if (layoutQueued) return;
    layoutQueued = true;
    // Node runners (tests/modules_*_runner.cjs) have no rAF and no real layout.
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => { layoutQueued = false; layoutLayer(); });
    else setTimeout(() => { layoutQueued = false; try { layoutLayer(); } catch (_) { /* no DOM layout here */ } }, 16);
  }

  function layoutLayer() {
    const layer = $("#module-layer");
    if (!layer || !layer.style) return;
    const band = chromeBand();
    const top = band ? band + "px" : "";
    if (layer.style.top !== top) layer.style.top = top;
    layer.dataset.band = band ? "1" : "";
    wins.forEach((win) => {
      if (win.minimized) return;
      if (isFullscreen(win)) { applyExpanded(win); return; }
      if (win.snap && SNAPS[win.snap]) {
        const rect = fracRect(SNAPS[win.snap]);
        Object.assign(win.el.style, { left: rect.x + "px", top: rect.y + "px", width: rect.width + "px", height: rect.height + "px" });
      }
    });
    const bar = $("#module-taskbar");
    if (bar && bar.style && !bar.hidden) {
      const area = workArea();
      bar.style.left = Math.round(area.x + area.width / 2) + "px";
    }
  }

  let chromeWatch = null;
  function watchChrome() {
    if (chromeWatch || typeof MutationObserver === "undefined") return;
    chromeWatch = new MutationObserver(queueLayout);
    chromeWatch.observe(document.documentElement, { attributes: true,
      attributeFilter: ["data-titlebar", "data-titlebar-overlay", "data-titlebar-max", "data-design", "data-density"] });
    document.addEventListener("memu-titlebar", queueLayout);
    document.addEventListener("transitionend", (event) => {
      if (!wins.size) return;
      const target = event.target;
      if (target instanceof Element && target.matches && target.matches(".side, .main, #main-content, #app, .topbar.appbar")) queueLayout();
    }, true);
    if (typeof ResizeObserver !== "undefined") {
      const ro = new ResizeObserver(() => { if (wins.size) queueLayout(); });
      [document.getElementById("main-content"), document.querySelector(".topbar.appbar"), document.querySelector(".side")]
        .forEach((node) => { if (node) ro.observe(node); });
    }
  }

  function fitToViewport(source) {
    // Окно никогда не больше рабочей области: иначе ручки размера и кнопки
    // уезжают за экран. Авторский размер остаётся в политике.
    const area = layerArea();
    const margin = 16;
    const maxWidth = Math.max(240, Math.round(area.width) - margin);
    const maxHeight = Math.max(200, Math.round(area.height) - margin);
    return {
      ...source,
      width: Math.max(Math.min(source.min_width, maxWidth), Math.min(source.width, maxWidth)),
      height: Math.max(Math.min(source.min_height, maxHeight), Math.min(source.height, maxHeight)),
    };
  }

  function clippedByViewport(next, source) {
    return next.width !== source.width || next.height !== source.height;
  }

  /* Части окна. Первое окно — разметка из index.html (у неё есть id), каждое
     следующее — её копия без id; части ищутся по data-win. */
  function parts(el) {
    const base = el === $("#module-window");
    const pick = (name, id) => (base ? document.getElementById(id) : null)
      || (el.querySelector ? el.querySelector(`[data-win="${name}"]`) : null);
    return {
      bar: pick("bar", "module-window-bar"),
      icon: pick("icon", "module-window-icon"),
      title: pick("title", "module-window-title"),
      subtitle: pick("subtitle", "module-window-subtitle"),
      status: pick("status", "module-window-status"),
      actions: pick("actions", "module-window-actions"),
      menu: pick("menu", "module-window-menu"),
      min: pick("min", "module-window-min"),
      full: pick("full", "module-window-full"),
      close: pick("close", "module-window-close"),
      frame: pick("frame", "module-frame"),
      loading: pick("loading", "module-window-loading"),
      resizers: el.querySelectorAll ? $$(".module-resize", el) : [],
      base,
    };
  }

  function createShell() {
    const base = $("#module-window");
    const used = [...wins.values()].some((win) => win.el === base);
    if (base && !used) return base;
    const clone = base.cloneNode(true);
    clone.removeAttribute("id");
    clone.querySelectorAll("[id]").forEach((node) => node.removeAttribute("id"));
    clone.removeAttribute("aria-labelledby");
    delete clone.dataset.bound;
    const frame = clone.querySelector('[data-win="frame"]');
    if (frame) frame.src = "about:blank";
    $("#module-layer").insertBefore(clone, $("#module-taskbar"));
    return clone;
  }

  function setStatus(text, win = wins.get(openId), kind = "") {
    const node = win && win.parts.status;
    if (!node) return;
    node.textContent = text || "";
    node.hidden = !text;
    // «module» — the module's own quiet line (Memu.toolbar.status); otherwise MemuAI's loading pill.
    node.dataset.kind = kind;
  }

  function isFullscreen(win = wins.get(openId)) {
    return !!win && win.el.dataset.full === "1";
  }

  /* ------------------------ единая шапка (Раунд 30) ------------------------
     Действия модуля (Memu.toolbar) рисует окно: в обычном окне — в его
     единственной строке заголовка, у развёрнутого листа — прямо в верхней
     панели MemuAI (#module-headbar), а строка листа прячется. Так над модулем
     всегда одна шапка, а не три ряда кнопок. */
  const TB = () => window.MemuModuleToolbar;

  function setToolbar(moduleId, payload) {
    const win = wins.get(moduleId);
    if (!win || !TB()) throw new Error(t("Окно модуля закрыто", "The module window is closed"));
    win.toolbar = TB().normalize(payload);
    renderWinToolbar(win);
    return { ok: true, state: win.el.dataset.state || "floating" };
  }

  function pickToolbar(win, item, value) {
    postTo(win, { type: "memu-module-host", action: "host-event",
      payload: { name: "toolbar", data: { id: item.id, value: value || "", kind: item.kind } } });
  }

  function renderWinToolbar(win) {
    if (!win || !win.parts) return;
    const spec = win.toolbar || { title: "", status: "", items: [] };
    const p = win.parts;
    if (p.subtitle) { p.subtitle.textContent = spec.title; p.subtitle.hidden = !spec.title; }
    if (p.actions && TB()) TB().render(p.actions, spec, (item, value) => pickToolbar(win, item, value));
    if (!p.loading || p.loading.hidden) setStatus(spec.status, win, "module");
    if (headWin === win) syncHeadbar(true);
  }

  let headWin = null;

  function headbarNode() {
    let node = document.getElementById("module-headbar");
    if (node) return node;
    const bar = document.querySelector(".topbar.appbar");
    if (!bar) return null;
    node = document.createElement("div");
    node.id = "module-headbar";
    node.className = "module-headbar";
    node.hidden = true;
    node.innerHTML = `
      <span class="mhb-id"><span class="mhb-icon" aria-hidden="true"></span><b class="mhb-title"></b></span>
      <span class="mhb-meta"><span class="mhb-sub" hidden></span><span class="mhb-status" hidden></span></span>
      <span class="mhb-grow"></span>
      <div class="mhb-actions mtb" role="toolbar"></div>
      <span class="mhb-divider" aria-hidden="true"></span>
      <div class="mhb-tools module-ctl-group" role="group">
        <button type="button" class="module-esc mhb-esc" data-mhb="esc"></button>
        <button type="button" class="mhb-btn" data-mhb="menu" aria-haspopup="menu"></button>
        <button type="button" class="mhb-btn module-ctl-min" data-mhb="min"><span class="mhb-min-ico"></span><span class="mhb-min-text"></span></button>
        <button type="button" class="mhb-btn module-ctl-close" data-mhb="close"><span class="mhb-close-ico"></span><span class="mhb-close-text"></span></button>
      </div>`;
    const grow = bar.querySelector(":scope > .grow");
    bar.insertBefore(node, grow || null);
    node.querySelector('[data-mhb="menu"]').innerHTML = typeof icon === "function" ? icon("more") : "";
    node.querySelector(".mhb-min-ico").innerHTML = CTRL_ICONS.minimize;
    node.querySelector(".mhb-close-ico").innerHTML = CTRL_ICONS.close;
    node.querySelector('[data-mhb="esc"]').addEventListener("click", () => { if (headWin && isFullscreen(headWin)) toggleFull(headWin); });
    node.querySelector('[data-mhb="menu"]').addEventListener("click", (event) => { if (headWin) windowMenu(headWin, event, true); });
    node.querySelector('[data-mhb="min"]').addEventListener("click", () => { if (headWin) minimizeWindow(headWin); });
    node.querySelector('[data-mhb="close"]').addEventListener("click", () => { if (headWin) requestClose(headWin); });
    // Пустое место и имя модуля остаются заголовком MemuAI (titlebar.js): их
    // тянут и разворачивают двойным щелчком, как остальную верхнюю панель.
    return node;
  }

  /** Самый верхний видимый развёрнутый лист отдаёт свою шапку верхней панели. */
  function syncHeadbar(force) {
    const bar = document.querySelector(".topbar.appbar");
    let next = null;
    if (bar && visibleNode(bar)) {
      wins.forEach((win) => {
        if (win.minimized || !isFullscreen(win)) return;
        if (!next || Number(win.el.style.zIndex || 0) > Number(next.el.style.zIndex || 0)) next = win;
      });
    }
    wins.forEach((win) => { win.el.dataset.headbar = win === next ? "1" : ""; });
    const root = document.documentElement;
    if (!next) {
      headWin = null;
      const node = document.getElementById("module-headbar");
      if (node) node.hidden = true;
      if (root.dataset.moduleHead) delete root.dataset.moduleHead;
      return;
    }
    const node = headbarNode();
    if (!node) return;
    if (headWin === next && !force && !node.hidden) return;
    headWin = next;
    const spec = next.toolbar || { title: "", status: "", items: [] };
    node.dataset.module = next.id;
    node.querySelector(".mhb-icon").innerHTML = moduleIcon(next.row || "");
    node.querySelector(".mhb-title").textContent = titleOf(next.row || {});
    const sub = node.querySelector(".mhb-sub");
    sub.textContent = spec.title;
    sub.hidden = !spec.title;
    const status = node.querySelector(".mhb-status");
    status.textContent = spec.status;
    status.hidden = !spec.status;
    const escChip = node.querySelector('[data-mhb="esc"]');
    escChip.innerHTML = `<kbd>Esc</kbd><span>${esc(t("окном", "window"))}</span>`;
    escChip.title = t("Вернуть обычное окно (Esc)", "Back to a normal window (Esc)");
    escChip.setAttribute("aria-label", escChip.title);
    const menu = node.querySelector('[data-mhb="menu"]');
    menu.title = t("Окно модуля: перезагрузка, раскладка, настройки", "Module window: reload, layout, settings");
    menu.setAttribute("aria-label", menu.title);
    const labels = controlLabels(true);
    const minBtn = node.querySelector('[data-mhb="min"]');
    minBtn.title = labels.min;
    minBtn.setAttribute("aria-label", labels.min);
    minBtn.querySelector(".mhb-min-text").textContent = labels.minShort;
    const closeBtn = node.querySelector('[data-mhb="close"]');
    closeBtn.title = labels.close;
    closeBtn.setAttribute("aria-label", labels.close);
    closeBtn.querySelector(".mhb-close-text").textContent = labels.closeShort;
    const actions = node.querySelector(".mhb-actions");
    actions.setAttribute("aria-label", t("Действия модуля", "Module actions"));
    if (TB()) TB().render(actions, spec, (item, value) => pickToolbar(next, item, value));
    node.querySelector(".mhb-divider").hidden = !spec.items.length;
    node.hidden = false;
    root.dataset.moduleHead = "1";
  }

  async function open(moduleId, options) {
    // Round 33: a module that lives in its own window is shown there, not opened twice.
    if (!appMode() && !(options && options.docked) && MA() && MA().redirectOpen(moduleId)) return;
    const row = catalog.find((item) => item.id === moduleId);
    if (!row) return note(t("Модуль не найден", "Module not found"), "err");
    if (!row.enabled) {
      note(t("Сначала включите модуль в настройках", "Enable the module in settings first"), "warn");
      return;
    }
    const existing = wins.get(moduleId);
    if (existing) {
      // Уже открытое окно не перезагружается — оно выходит вперёд.
      if (existing.minimized) restoreWindow(existing);
      focusWindow(existing);
      return;
    }
    const job = ++openJob;
    let data;
    try {
      data = await apiJson("/api/pg/modules/window", { method: "POST", body: { id: moduleId } });
    } catch (error) {
      return note(error.message, "err");
    }
    // Пока шёл запрос, окно этого модуля мог открыть другой вызов (двойной
    // клик, восстановление после запуска). Второе окно было бы сиротой.
    const again = wins.get(moduleId);
    if (again) { focusWindow(again); return; }
    if (job !== openJob && wins.has(moduleId)) return;
    const layer = $("#module-layer");
    const el = createShell();
    if (!layer || !el) return;
    // Слой показываем до расчёта геометрии: размеры окна и полоса заголовка
    // считаются от настоящего прямоугольника слоя, а не от всего окна.
    if (layer.hidden) { layer.hidden = false; watchChrome(); }
    layoutLayer();
    const win = { id: moduleId, row, el, parts: parts(el), policy: fitToViewport(data.window),
      savedBox: { width: 0, height: 0 }, minimized: false, snap: "", restore: null };
    win.frame = win.parts.frame;
    wins.set(moduleId, win);
    el.hidden = false;
    el.dataset.module = moduleId;
    el.dataset.fixed = win.policy.fixed ? "1" : "";
    el.dataset.full = win.policy.fullscreen ? "1" : "";
    el.dataset.small = (win.policy.width <= 620 || win.policy.height <= 460) ? "1" : "";
    el.dataset.resized = win.policy.size_source === "user" ? "1" : "";
    el.style.width = win.policy.width + "px";
    el.style.height = win.policy.height + "px";
    if (win.parts.title) win.parts.title.textContent = titleOf(row);
    if (win.parts.icon) win.parts.icon.innerHTML = moduleIcon(row);
    bindWindow(win);
    // Новое окно не ложится ровно поверх уже открытого: сдвигаем каскадом.
    const others = wins.size - 1;
    if (others > 0 && !(win.policy.x >= 0 && win.policy.y >= 0) && !win.policy.fullscreen) {
      const area = layerArea();
      const step = 28 * others;
      applyRect(win, { x: Math.max(0, (area.width - win.policy.width) / 2 + step),
        y: Math.max(0, (area.height - win.policy.height) / 2 + step) });
      el.dataset.moved = "";
    } else {
      applyRect(win, win.policy);
    }
    if (win.policy.fullscreen) applyExpanded(win);
    refreshResizers(win);
    if (win.parts.full) win.parts.full.hidden = !win.policy.fullscreen_allowed;
    syncFullButton(win);
    if (win.parts.loading) win.parts.loading.hidden = false;
    setStatus(t("Загружаю модуль…", "Loading the module…"), win);
    if (recording && recording.module === moduleId) cancelRecording();
    win.frame.src = documentUrl(win, false);
    layer.hidden = false;
    layer.dataset.open = "1";
    document.body.classList.add("module-open");
    focusWindow(win);
    queueLayout();
    if (!appMode()) persistBox(win, { open: true }).catch(() => {});
    if (typeof recordView === "function") recordView({ type: "panel", name: "module:" + moduleId });
    // Automated browsers (Playwright/WebDriver tests) never get the one-time hint on their own:
    // it would cover what the test clicks. MemuModules.showHint() still shows it.
    const automated = typeof navigator !== "undefined" && navigator && navigator.webdriver === true;
    if (!automated && !appMode()) loadHints().then(() => { if (!uiHint("window")) setTimeout(() => showCoach(win), 700); });
  }

  function focusWindow(win) {
    if (!win) return;
    zTop += 1;
    win.el.style.zIndex = String(zTop);
    openId = win.id;
    policy = win.policy;
    wins.forEach((other) => { other.el.dataset.active = other === win ? "1" : ""; });
    renderTaskbar();
  }

  function topWindow() {
    let best = null;
    wins.forEach((win) => {
      if (win.minimized) return;
      if (!best || Number(win.el.style.zIndex || 0) > Number(best.el.style.zIndex || 0)) best = win;
    });
    return best;
  }

  function refreshResizers(win) {
    if (!win) return;
    if (win.parts.bar) win.parts.bar.dataset.draggable = win.policy.draggable ? "1" : "";
    const full = isFullscreen(win);
    win.parts.resizers.forEach((edge) => {
      const axis = edge.dataset.resize || "se";
      let allowed = win.policy.resizable && !full;
      if (/[ew]/.test(axis) && !/[ns]/.test(axis)) allowed = allowed && win.policy.max_width > win.policy.min_width;
      if (/[ns]/.test(axis) && !/[ew]/.test(axis)) allowed = allowed && win.policy.max_height > win.policy.min_height;
      edge.hidden = !allowed;
    });
  }

  let lastKnownViewport = { width: 0, height: 0 };

  function applyRect(win, source) {
    // Позиция — в пикселях от левого верхнего угла MemuAI. Несдвинутое окно
    // открывается по центру; сохранённая позиция подгоняется под экран.
    const el = win.el;
    const area = layerArea();
    const width = parseFloat(el.style.width) || (source && source.width) || 760;
    const height = parseFloat(el.style.height) || (source && source.height) || 540;
    const maxLeft = Math.max(0, area.width - width - 8);
    const maxTop = Math.max(0, area.height - height - 8);
    const x = Number(source && source.x);
    const y = Number(source && source.y);
    const moved = !(source && source.center) && x >= 0 && y >= 0;
    el.style.transform = "";
    el.style.right = "auto";
    el.dataset.centered = moved ? "" : "1";
    el.dataset.moved = moved ? "1" : "";
    el.style.left = Math.round(moved ? Math.min(x, maxLeft) : Math.max(0, (area.width - width) / 2)) + "px";
    el.style.top = Math.round(moved ? Math.min(y, maxTop) : Math.max(0, (area.height - height) / 2)) + "px";
  }

  function closeWindow(win, instant) {
    if (!win || !wins.has(win.id)) return Promise.resolve();
    const finish = () => {
      if (!wins.has(win.id)) return;
      wins.delete(win.id);
      cancelModuleStreams(win.id);
      if (recording && recording.module === win.id) cancelRecording();
      // Уходящая страница успевает отправить последние правки (pagehide):
      // ещё три секунды принимаем от неё сохранение, иначе они терялись.
      if (win.frame && win.frame.contentWindow) {
        const source = win.frame.contentWindow;
        closingFrames.set(source, { id: win.id, frame: win.frame, row: win.row });
        setTimeout(() => closingFrames.delete(source), 3000);
      }
      if (win.frame) win.frame.src = "about:blank";
      if (win.el === $("#module-window")) win.el.hidden = true;
      else if (win.el.remove) win.el.remove();
      const layer = $("#module-layer");
      if (!wins.size) {
        if (layer) { layer.hidden = true; layer.dataset.open = ""; }
        document.body.classList.remove("module-open");
        openId = "";
        policy = null;
      } else if (openId === win.id) {
        const next = topWindow();
        if (next) focusWindow(next);
        else { openId = ""; policy = null; }
      }
      renderTaskbar();
      if (appMode() && !wins.size && MA()) MA().lastClosed(win.id);
    };
    if (instant || appMode()) { finish(); return Promise.resolve(); }
    const box = currentBox(win);
    finish();
    return apiJson("/api/pg/modules/window", { method: "POST", body: { id: win.id, box, open: false } })
      .catch(() => null);
  }

  /* Round 31: a close the person asked for (×, Ctrl+W, the taskbar). A module
     that is generating, recording or reported unsaved work asks first; Memu.close()
     from the module itself and switching the module off never ask. */
  function closeReason(win) {
    if (!win) return "";
    if (win.unsaved) {
      return typeof win.unsaved === "string" ? win.unsaved
        : t("В модуле есть несохранённые изменения.", "The module has unsaved changes.");
    }
    if (recording && recording.module === win.id) return t("Идёт запись с микрофона.", "The microphone is recording.");
    if (busyCount.get(win.id)) return t("Модуль ещё работает: ответ или генерация не завершены.", "The module is still working: an answer or a generation has not finished.");
    return "";
  }

  function askCloseModule(win, reason) {
    const title = t(`Закрыть «${titleOf(win.row)}»?`, `Close “${titleOf(win.row)}”?`);
    const text = reason + " " + t("Если закрыть окно, это пропадёт. «Свернуть» — модуль продолжит работать.",
      "Closing the window loses it. “Minimize” keeps the module running.");
    return new Promise((resolve) => {
      if (typeof panel !== "function") return resolve(window.confirm(title + "\n\n" + text) ? "close" : "cancel");
      let settled = false;
      const finish = (answer) => {
        if (settled) return;
        settled = true;
        if (typeof closePanel === "function") closePanel(answer === "close" ? "confirm" : "cancel");
        document.getElementById("panel-back")?.classList.remove("over-modules");
        resolve(answer);
      };
      panel(`<div class="module-close-ask">
        <div class="net-import-ask-head"><span class="net-import-ask-icon">${moduleIcon(win.row || "")}</span>
          <div><h2>${esc(title)}</h2><p class="sub">${esc(text)}</p></div></div>
        <div class="row-end">
          <button class="pbtn ghost" id="module-close-cancel">${esc(t("Отмена", "Cancel"))}</button>
          <button class="pbtn ghost" id="module-close-min">${esc(t("Свернуть", "Minimize"))}</button>
          <button class="pbtn danger" id="module-close-yes">${esc(t("Закрыть модуль", "Close the module"))}</button>
        </div></div>`, "module-close-panel", { onClose: () => finish("cancel") });
      document.getElementById("panel-back")?.classList.add("over-modules");
      document.getElementById("module-close-cancel").onclick = () => finish("cancel");
      document.getElementById("module-close-min").onclick = () => finish("minimize");
      document.getElementById("module-close-yes").onclick = () => finish("close");
      setTimeout(() => document.getElementById("module-close-min")?.focus(), 30);
    });
  }

  async function requestClose(win) {
    if (!win || !wins.has(win.id)) return false;
    const reason = closeReason(win);
    if (reason) {
      const answer = await askCloseModule(win, reason);
      if (answer === "minimize") { minimizeWindow(win); return false; }
      if (answer !== "close") return false;
    }
    await closeWindow(win);
    return true;
  }

  async function requestCloseAll() {
    for (const win of [...wins.values()]) {
      if (!(await requestClose(win)) && wins.has(win.id)) break;
    }
  }

  // Совместимость: close() закрывает активное окно.
  async function close(instant) {
    const win = wins.get(openId);
    if (!win) {
      if (!wins.size) {
        const layer = $("#module-layer");
        if (layer) layer.hidden = true;
      }
      return;
    }
    await closeWindow(win, instant);
  }

  function closeAll() {
    [...wins.values()].forEach((win) => closeWindow(win));
  }

  function currentBox(win) {
    const el = win.el;
    const rect = el.getBoundingClientRect();
    const full = isFullscreen(win);
    const box = {
      // В полном экране и в привязке сохраняем прежний размер, а не весь экран.
      width: full ? (win.savedBox.width || win.policy.width || Math.round(rect.width)) : Math.round(rect.width),
      height: full ? (win.savedBox.height || win.policy.height || Math.round(rect.height)) : Math.round(rect.height),
      fullscreen: full,
    };
    box.moved = el.dataset.moved === "1";
    box.resized = el.dataset.resized === "1";
    box.x = box.moved ? Math.max(0, Math.round(parseFloat(el.style.left) || rect.left)) : -1;
    box.y = box.moved ? Math.max(0, Math.round(parseFloat(el.style.top) || rect.top)) : -1;
    return box;
  }

  function snapshotBox(win) {
    if (!win || isFullscreen(win)) return;
    const rect = win.el.getBoundingClientRect();
    const live = { width: Math.round(rect.width), height: Math.round(rect.height) };
    if (clippedByViewport(live, win.policy)) return;
    win.savedBox.width = live.width;
    win.savedBox.height = live.height;
  }

  function persistBox(win = wins.get(openId), extra) {
    if (!win || !wins.has(win.id) || appMode()) return Promise.resolve();
    const rect = win.el.getBoundingClientRect();
    const live = { width: Math.round(rect.width), height: Math.round(rect.height) };
    const clipped = !win.el.dataset.resized && clippedByViewport(live, win.policy);
    snapshotBox(win);
    const box = { ...currentBox(win), keep_size: clipped };
    return apiJson("/api/pg/modules/window", { method: "POST", body: { id: win.id, box, ...(extra || {}) } })
      .then((data) => {
        if (wins.get(win.id) === win && data && data.window) {
          win.policy = { ...fitToViewport(data.window), width: live.width, height: live.height };
          if (openId === win.id) policy = win.policy;
        }
        return data;
      })
      .catch(() => null);
  }

  /* ------------------------------ свернуть и развернуть --------------------- */

  function minimizeWindow(win) {
    if (!win) return;
    // Round 33: a module's own window minimizes to the Windows taskbar, like any app.
    if (appMode()) { if (MA()) MA().minimizeNative(); return; }
    win.minimized = true;
    win.el.hidden = true;
    const firstTime = !uiHint("minimize");
    if (openId === win.id) {
      const next = topWindow();
      if (next) focusWindow(next);
      else renderTaskbar();
    } else renderTaskbar();
    if (firstTime) {
      // The first minimize says where the module went — a bubble over the module bar itself.
      markHint("minimize");
      const bar = $("#module-taskbar");
      const layer = $("#module-layer");
      if (bar && layer && !bar.hidden && bar.getBoundingClientRect && layer.appendChild && layer.getBoundingClientRect
          && typeof document.createElement === "function") {
        const old = typeof document.getElementById === "function" ? document.getElementById("module-task-tip") : null;
        if (old) old.remove();
        const tip = document.createElement("div");
        tip.id = "module-task-tip";
        tip.className = "module-task-tip";
        tip.setAttribute("role", "status");
        tip.textContent = t(`«${titleOf(win.row)}» свёрнут и работает дальше. Вернуть — щелчком здесь, закрыть — ×`,
          `“${titleOf(win.row)}” is minimized and keeps running. Click it here to bring it back, × to close`);
        layer.appendChild(tip);
        const rect = bar.getBoundingClientRect(), base = layer.getBoundingClientRect();
        tip.style.left = Math.round(rect.left - base.left + rect.width / 2) + "px";
        tip.style.top = Math.round(rect.top - base.top - 10) + "px";
        const drop = () => tip.remove();
        setTimeout(drop, 6500);
        tip.addEventListener("click", drop);
      }
    }
  }

  function restoreWindow(win) {
    if (!win) return;
    win.minimized = false;
    win.el.hidden = false;
    focusWindow(win);
  }

  /* Round 31: the module's own window controls look different from the MemuAI
     window buttons (— □ × in the corner): «minimize» is an arrow into the
     module bar, «close» a × in a red-on-hover pill, and every tooltip names the
     module and the shortcut. */
  const CTRL_ICONS = {
    // Round 33 (#7): the old arrow-into-a-line looked exactly like the modules' own «Download» button next to it.
    // A minimize bar (the shape every window uses) + the word «Свернуть» when the header has room.
    minimize: '<span class="ui-icon"><svg viewBox="0 0 24 24"><path d="M6 16.5h12"/></svg></span>',
    close: '<span class="ui-icon"><svg viewBox="0 0 24 24"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg></span>',
  };

  function hotkeyLabel(id, fallback) {
    const HK = window.MemuHotkeys;
    let combo = fallback;
    try { if (HK && typeof HK.value === "function") combo = HK.value(id, fallback); } catch (error) { combo = fallback; }
    try { if (combo && HK && typeof HK.label === "function") return HK.label(combo); } catch (error) { /* plain text */ }
    return combo || "";
  }

  function controlLabels(expanded) {
    const close = hotkeyLabel("mod_close", "Ctrl+W");
    return {
      min: expanded ? t("Свернуть модуль — он продолжит работать на панели модулей внизу",
        "Minimize the module — it keeps running on the module bar below")
        : t("Свернуть модуль (Esc) — он продолжит работать на панели модулей внизу",
          "Minimize the module (Esc) — it keeps running on the module bar below"),
      minShort: t("Свернуть", "Minimize"),
      close: t("Закрыть модуль", "Close the module") + (close ? ` (${close})` : ""),
      closeShort: t("Закрыть", "Close"),
    };
  }

  function labelControls(p) {
    const text = controlLabels();
    [[p.min, text.min, text.minShort], [p.close, text.close, text.closeShort]].forEach(([button, label, short]) => {
      if (!button || typeof button.setAttribute !== "function") return;
      button.title = label;
      button.setAttribute("aria-label", label);
      // Round 33 (#7): the word next to the glyph (shown when the header has room, modules.css).
      const word = button.querySelector ? button.querySelector(".module-ctl-text") : null;
      if (word) word.textContent = short;
      // The static data-en-* labels of index.html would overwrite these on a language switch.
      if (typeof button.removeAttribute === "function") { button.removeAttribute("data-en-title"); button.removeAttribute("data-en-label"); }
    });
  }

  /** Esc: an expanded sheet goes back to a window; a window (Esc pressed inside
   *  the module or on its header) goes down to the module bar — «back to chat». */
  function escapeWindow(win, fromModule) {
    if (typeof document.getElementById === "function" && document.getElementById("module-coach")) { hideCoach(); markHint("window"); return true; }
    if (!win || win.minimized || overlayOpen()) return false;
    if (isFullscreen(win)) { toggleFull(win); return true; }
    const active = document.activeElement;
    const inside = fromModule || (active && win.el.contains && win.el.contains(active) && active !== document.body);
    if (!inside) return false;
    minimizeWindow(win);
    return true;
  }

  /** Window shortcuts (the hotkeys settings page) — from MemuAI or forwarded by the bridge. */
  function windowHotkey(win, event) {
    if (!win) return false;
    const HK = window.MemuHotkeys;
    const hit = (id, fallback) => (HK ? HK.match(id, event, fallback) : false);
    const plain = !HK;
    const key = String(event.key || ""), code = String(event.code || "");
    const ctrl = !!(event.ctrlKey || event.metaKey);
    if (hit("mod_close", "Ctrl+W") || (plain && ctrl && !event.altKey && !event.shiftKey && (code === "KeyW" || key.toLowerCase() === "w"))) {
      if (win.minimized) return false;
      requestClose(win);
      return true;
    }
    if (win.minimized) return false;
    const snaps = { mod_snap_left: "left", mod_snap_right: "right", mod_maximize: "max", mod_snap_bottom: "bottom" };
    const found = HK && Object.keys(snaps).find((id) => HK.match(id, event));
    if (found) { snapWindow(win, snaps[found]); return true; }
    if (HK && HK.match("mod_minimize", event)) { minimizeWindow(win); return true; }
    if (plain && event.altKey && event.shiftKey && !ctrl) {
      const map = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "max", ArrowDown: "bottom" };
      if (map[key]) { snapWindow(win, map[key]); return true; }
      if (code === "KeyM") { minimizeWindow(win); return true; }
    }
    return false;
  }

  /* Round 31: one-time hints, remembered on the server (modules/ui.json). */
  let hintsSeen = null;
  let hintsLoading = null;
  function loadHints() {
    if (hintsSeen) return Promise.resolve(hintsSeen);
    if (!hintsLoading) {
      hintsLoading = apiJson("/api/pg/modules/ui-hints")
        .then((data) => { hintsSeen = new Set((data && data.seen) || []); return hintsSeen; })
        // No answer — behave as if it was seen: a hint must never nag on every start.
        .catch(() => { hintsSeen = new Set(["window", "minimize"]); return hintsSeen; });
    }
    return hintsLoading;
  }
  function uiHint(name) { return !hintsSeen || hintsSeen.has(name); }
  function markHint(name) {
    if (hintsSeen) hintsSeen.add(name);
    apiJson("/api/pg/modules/ui-hints", { method: "POST", body: { name } }).catch(() => {});
  }

  function hideCoach() {
    if (typeof document.getElementById !== "function") return;
    const old = document.getElementById("module-coach");
    if (old) old.remove();
    document.removeEventListener("keydown", coachKey, true);
    document.removeEventListener("pointerdown", coachOutside, true);
  }
  function coachOutside(event) {
    const box = document.getElementById("module-coach");
    if (box && !(event.target instanceof Node && box.contains(event.target))) hideCoach();
  }
  function coachKey(event) {
    if (event.key === "Escape" && document.getElementById("module-coach")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      hideCoach();
    }
  }

  /** The first module window explains its buttons once: minimize ≠ close, and
   *  the × in the very corner belongs to MemuAI itself. */
  function showCoach(win, force) {
    if (!win || !wins.has(win.id) || win.minimized) return;
    if (!force && uiHint("window")) return;
    if (!win.el.querySelector || typeof document.createElement !== "function") return;
    hideCoach();
    const head = headWin === win ? document.getElementById("module-headbar") : null;
    const tools = head ? head.querySelector(".mhb-tools") : win.el.querySelector(".module-window-tools");
    if (!tools || !tools.getBoundingClientRect) return;
    const rect = tools.getBoundingClientRect();
    if (!rect.width) return;
    const appClose = document.documentElement.dataset.titlebar === "custom";
    const box = document.createElement("div");
    box.id = "module-coach";
    box.className = "module-coach";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", t("Как свернуть и закрыть модуль", "How to minimize and close a module"));
    const close = hotkeyLabel("mod_close", "Ctrl+W");
    box.innerHTML = `<b class="module-coach-title">${esc(t("Свернуть или закрыть?", "Minimize or close?"))}</b>
      <ul>
        <li><span class="module-coach-ico">${CTRL_ICONS.minimize}</span><span><b>${esc(t("Свернуть", "Minimize"))}</b> — ${esc(t("модуль уходит на панель модулей внизу и работает дальше. Вернуть — щелчком по нему.", "the module goes to the module bar below and keeps running. Click it there to bring it back."))} <kbd>Esc</kbd></span></li>
        <li><span class="module-coach-ico danger">${CTRL_ICONS.close}</span><span><b>${esc(t("Закрыть модуль", "Close the module"))}</b> — ${esc(t("окно закрывается, данные модуля сохраняются.", "the window closes, the module's data is kept."))} <kbd>${esc(close)}</kbd></span></li>
        ${appClose ? `<li><span class="module-coach-ico app">${CTRL_ICONS.close}</span><span>${esc(t("Крестик в самом углу окна закрывает весь MemuAI, а не модуль.", "The × in the very corner of the window closes MemuAI itself, not the module."))}</span></li>` : ""}
      </ul>
      <div class="module-coach-row"><button type="button" class="pbtn" data-coach="ok">${esc(t("Понятно", "Got it"))}</button></div>`;
    document.body.appendChild(box);
    const width = box.offsetWidth || 320;
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width));
    box.style.left = Math.round(left) + "px";
    box.style.top = Math.round(rect.bottom + 10) + "px";
    box.style.setProperty("--coach-arrow", Math.round(Math.max(16, Math.min(width - 16, rect.right - 22 - left))) + "px");
    box.querySelector('[data-coach="ok"]').addEventListener("click", () => { hideCoach(); markHint("window"); });
    document.addEventListener("keydown", coachKey, true);
    document.addEventListener("pointerdown", coachOutside, true);
    if (!force) markHint("window");
  }

  /** The MemuAI window's own × (titlebar.js) says what it closes while a module is open. */
  function syncAppCloseHint() {
    if (typeof document.querySelector !== "function") return;
    const button = document.querySelector("#memu-wincontrols .memu-wc-close");
    if (!button) return;
    const base = t("Закрыть", "Close");
    const text = wins.size ? t("Закрыть окно MemuAI (не модуль: у модуля свой ×)", "Close the MemuAI window (not the module: it has its own ×)") : base;
    if (button.title !== text) button.title = text;
    button.dataset.moduleOpen = wins.size ? "1" : "";
  }
  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("memu-titlebar", () => setTimeout(syncAppCloseHint, 0));
  }

  const FULL_ICONS = {
    expand: '<span class="ui-icon"><svg viewBox="0 0 24 24"><path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/></svg></span>',
    collapse: '<span class="ui-icon"><svg viewBox="0 0 24 24"><path d="M20 10h-6V4M4 14h6v6M14 10l6.5-6.5M10 14l-6.5 6.5"/></svg></span>',
  };

  /** Кнопка «развернуть / вернуть размер», подсказка Esc и событие для модуля. */
  function syncFullButton(win) {
    if (!win) return;
    const full = isFullscreen(win);
    const button = win.parts.full;
    if (button) {
      button.setAttribute("aria-pressed", full ? "true" : "false");
      const label = full ? t("Вернуть размер (Esc)", "Restore size (Esc)") : t("Развернуть в MemuAI", "Expand in MemuAI");
      button.title = label;
      button.setAttribute("aria-label", label);
      if (button.dataset.icon !== (full ? "collapse" : "expand")) {
        button.dataset.icon = full ? "collapse" : "expand";
        button.innerHTML = full ? FULL_ICONS.collapse : FULL_ICONS.expand;
      }
    }
    const chip = win.parts.esc || (win.el.querySelector && win.el.querySelector('[data-win="esc"]'));
    if (chip) chip.hidden = !full;
    const nextState = full ? "expanded" : (win.snap ? "snapped" : "floating");
    // The one-time hint points at buttons that move when the window changes state.
    if (win.el.dataset.state && win.el.dataset.state !== nextState) hideCoach();
    win.el.dataset.state = nextState;
    syncHeadbar();
    if (win.frame && win.frame.contentWindow && win.lastState !== win.el.dataset.state) {
      win.lastState = win.el.dataset.state;
      postTo(win, { type: "memu-module-host", action: "host-event",
        payload: { name: "window", data: { state: win.el.dataset.state } } });
    }
  }

  function toggleFull(win) {
    if (!win) return;
    const full = win.el.dataset.full === "1";
    if (!full) snapshotBox(win);
    morph(win, () => {
      if (!full) { unsnap(win, true); applyExpanded(win); }
      win.el.dataset.full = full ? "" : "1";
      syncFullButton(win);
      refreshResizers(win);
    });
    if (full && win.parts.full && document.activeElement && win.el.contains && win.el.contains(document.activeElement)) {
      // Фокус остаётся на кнопке: повторный Enter снова разворачивает.
      try { win.parts.full.focus({ preventScroll: true }); } catch (error) { /* старый движок */ }
    }
    persistBox(win).catch(() => {});
  }

  /** Esc сворачивает развёрнутый лист активного модуля. */
  function overlayOpen() {
    return !!($(".module-layout-menu")
      || ($("#panel-back") && $("#panel-back").classList.contains("visible"))
      || ($("#image-viewer") && $("#image-viewer").classList.contains("visible"))
      || ($("#context-menu") && $("#context-menu").classList.contains("visible"))
      || $(".pretty-select-menu.visible")
      || ($("#model-dd-pop") && $("#model-dd-pop").style.display === "block")
      || ($("#app-menu-pop") && $("#app-menu-pop").classList.contains("visible")));
  }

  function escapeExpanded(win) {
    if (!win || win.minimized || !isFullscreen(win) || overlayOpen()) return false;
    toggleFull(win);
    return true;
  }

  /* --------------------------------- раскладки ------------------------------ */

  // Прямоугольники в долях рабочей области: x, y, ширина, высота.
  const SNAPS = {
    left: [0, 0, 0.5, 1], right: [0.5, 0, 0.5, 1], top: [0, 0, 1, 0.5], bottom: [0, 0.5, 1, 0.5],
    "top-left": [0, 0, 0.5, 0.5], "top-right": [0.5, 0, 0.5, 0.5],
    "bottom-left": [0, 0.5, 0.5, 0.5], "bottom-right": [0.5, 0.5, 0.5, 0.5],
    "left-wide": [0, 0, 2 / 3, 1], "right-narrow": [2 / 3, 0, 1 / 3, 1],
    "left-narrow": [0, 0, 1 / 3, 1], "right-wide": [1 / 3, 0, 2 / 3, 1],
    "third-1": [0, 0, 1 / 3, 1], "third-2": [1 / 3, 0, 1 / 3, 1], "third-3": [2 / 3, 0, 1 / 3, 1],
    max: [0, 0, 1, 1],
  };
  const GAP = 8;

  function fracRect(frac) {
    // Раскладки делят область содержимого рядом с боковой панелью (та же,
    // что у развёрнутого листа); полоса панели задач в неё не входит.
    const area = workArea();
    const [fx, fy, fw, fh] = frac;
    const x = Math.round(area.x + area.width * fx + GAP / 2 + (fx === 0 ? GAP / 2 : 0));
    const y = Math.round(area.y + area.height * fy + GAP / 2 + (fy === 0 ? GAP / 2 : 0));
    const right = Math.round(area.x + area.width * (fx + fw) - GAP / 2 - (fx + fw >= 0.999 ? GAP / 2 : 0));
    const bottom = Math.round(area.y + area.height * (fy + fh) - GAP / 2 - (fy + fh >= 0.999 ? GAP / 2 : 0));
    return { x, y, width: Math.max(160, right - x), height: Math.max(120, bottom - y) };
  }

  function placeWindow(win, rect, snapName) {
    if (win.minimized) { win.minimized = false; win.el.hidden = false; }
    const wasFull = win.el.dataset.full === "1";
    if (wasFull) win.el.dataset.full = "";
    if (!win.snap) {
      // Из развёрнутого листа возвращаемся к прежнему размеру окна, а не к листу.
      const now = wasFull && win.savedBox.width ? win.savedBox : win.el.getBoundingClientRect();
      win.restore = { width: Math.round(now.width), height: Math.round(now.height) };
    }
    win.snap = snapName || "custom";
    win.el.dataset.snap = win.snap;
    // Раскладка важнее авторских границ: окно занимает ровно выбранную долю.
    win.el.style.width = rect.width + "px";
    win.el.style.height = rect.height + "px";
    win.el.style.left = rect.x + "px";
    win.el.style.top = rect.y + "px";
    win.el.style.right = "auto";
    win.el.dataset.moved = "1";
    win.el.dataset.centered = "";
    win.el.dataset.resized = "1";
    refreshResizers(win);
    syncFullButton(win);
  }

  function unsnap(win, keepSize) {
    if (!win || !win.snap) return;
    win.snap = "";
    delete win.el.dataset.snap;
    if (win.el.dataset.state === "snapped") win.el.dataset.state = "floating";
    if (!keepSize && win.restore) {
      win.el.style.width = win.restore.width + "px";
      win.el.style.height = win.restore.height + "px";
    }
    win.restore = null;
  }

  function snapWindow(win, name) {
    if (!win || !SNAPS[name]) return;
    if (name === "max") { if (win.el.dataset.full !== "1") toggleFull(win); return; }
    morph(win, () => placeWindow(win, fracRect(SNAPS[name]), name));
    focusWindow(win);
    persistBox(win).catch(() => {});
  }

  function visibleWindows(activeFirst) {
    const list = [...wins.values()].filter((win) => !win.minimized);
    list.sort((a, b) => Number(a.el.style.zIndex || 0) - Number(b.el.style.zIndex || 0));
    if (activeFirst) {
      const active = wins.get(openId);
      if (active && !active.minimized) return [active, ...list.filter((win) => win !== active)];
    }
    return list;
  }

  /* Раскладка всех открытых окон сразу. */
  function arrange(preset) {
    const minimized = [...wins.values()].filter((win) => win.minimized);
    if (preset !== "focus") minimized.forEach((win) => { win.minimized = false; win.el.hidden = false; });
    const list = visibleWindows(true);
    const n = list.length;
    if (!n) return;
    const place = (win, frac, name) => placeWindow(win, fracRect(frac), name);
    if (preset === "columns") list.forEach((win, i) => place(win, [i / n, 0, 1 / n, 1], `col-${i}`));
    else if (preset === "rows") list.forEach((win, i) => place(win, [0, i / n, 1, 1 / n], `row-${i}`));
    else if (preset === "grid") {
      const cols = Math.ceil(Math.sqrt(n));
      const rows = Math.ceil(n / cols);
      list.forEach((win, i) => {
        const r = Math.floor(i / cols);
        const inRow = r === rows - 1 ? n - cols * (rows - 1) : cols;
        const c = i % cols;
        place(win, [c / inRow, r / rows, 1 / inRow, 1 / rows], `grid-${i}`);
      });
    } else if (preset === "main") {
      if (n === 1) place(list[0], [0, 0, 1, 1], "main");
      else {
        place(list[0], [0, 0, 0.6, 1], "main");
        list.slice(1).forEach((win, i) => place(win, [0.6, i / (n - 1), 0.4, 1 / (n - 1)], `stack-${i}`));
      }
    } else if (preset === "cascade") {
      const area = workArea();
      const width = Math.round(Math.min(area.width * 0.62, 1100));
      const height = Math.round(Math.min(area.height * 0.72, 800));
      list.slice().reverse().forEach((win, i) => {
        const step = 34 * (n - 1 - i);
        placeWindow(win, { x: area.x + 24 + step, y: area.y + 20 + step, width, height }, "");
        win.snap = "";
        delete win.el.dataset.snap;
        syncFullButton(win);
      });
    } else if (preset === "focus") {
      list.slice(1).forEach((win) => minimizeWindow(win));
      if (list[0] && list[0].el.dataset.full !== "1") toggleFull(list[0]);
    }
    visibleWindows(false).forEach((win) => persistBox(win).catch(() => {}));
    const active = wins.get(openId);
    if (active) focusWindow(active);
    renderTaskbar();
  }

  /* Меню раскладок: схемы, как в Windows, — для этого окна и для всех. */
  function layoutIcon(rects, highlight) {
    return `<svg viewBox="0 0 48 32" aria-hidden="true">${rects.map((r, i) =>
      `<rect x="${1 + r[0] * 46}" y="${1 + r[1] * 30}" width="${Math.max(2, r[2] * 46 - 2)}" height="${Math.max(2, r[3] * 30 - 2)}" rx="2.5"
        class="${highlight === undefined || highlight === i ? "on" : ""}"/>`).join("")}</svg>`;
  }

  function showLayoutMenu(win, anchor) {
    hideLayoutMenu();
    const single = [
      ["left", t("Слева", "Left")], ["right", t("Справа", "Right")], ["top", t("Сверху", "Top")], ["bottom", t("Снизу", "Bottom")],
      ["top-left", t("Слева сверху", "Top left")], ["top-right", t("Справа сверху", "Top right")],
      ["bottom-left", t("Слева снизу", "Bottom left")], ["bottom-right", t("Справа снизу", "Bottom right")],
      ["left-wide", t("Слева, ⅔", "Left, ⅔")], ["right-narrow", t("Справа, ⅓", "Right, ⅓")],
      ["left-narrow", t("Слева, ⅓", "Left, ⅓")], ["right-wide", t("Справа, ⅔", "Right, ⅔")],
    ];
    const count = [...wins.values()].length;
    const all = [
      ["columns", t("Колонками", "Columns"), Array.from({ length: Math.max(2, count) }, (_, i) => [i / Math.max(2, count), 0, 1 / Math.max(2, count), 1])],
      ["rows", t("Строками", "Rows"), Array.from({ length: Math.max(2, count) }, (_, i) => [0, i / Math.max(2, count), 1, 1 / Math.max(2, count)])],
      ["grid", t("Сеткой", "Grid"), [[0, 0, .5, .5], [.5, 0, .5, .5], [0, .5, .5, .5], [.5, .5, .5, .5]]],
      ["main", t("Главное + стопка", "Main + stack"), [[0, 0, .6, 1], [.6, 0, .4, .5], [.6, .5, .4, .5]]],
      ["cascade", t("Каскадом", "Cascade"), [[0, 0, .7, .7], [.15, .15, .7, .7], [.3, .3, .7, .7]]],
      ["focus", t("Только это окно", "Only this one"), [[0, 0, 1, 1]]],
    ];
    const menu = document.createElement("div");
    menu.className = "module-layout-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = `
      <div class="mlm-title">${esc(t("Это окно", "This window"))}</div>
      <div class="mlm-grid">${single.map(([name, label]) =>
        `<button type="button" role="menuitem" data-snap="${name}" title="${esc(label)}" aria-label="${esc(label)}">${layoutIcon([SNAPS[name]])}</button>`).join("")}</div>
      <div class="mlm-row">
        <button type="button" role="menuitem" data-snap="max">${esc(t("На весь MemuAI", "Fill MemuAI"))}</button>
        <button type="button" role="menuitem" data-snap="center">${esc(t("По центру", "Centre"))}</button>
      </div>
      ${count > 1 ? `<div class="mlm-title">${esc(t(`Все окна · ${count}`, `All windows · ${count}`))}</div>
      <div class="mlm-grid wide">${all.map(([name, label, rects]) =>
        `<button type="button" role="menuitem" data-arrange="${name}" title="${esc(label)}">${layoutIcon(rects, name === "focus" ? 0 : undefined)}<span>${esc(label)}</span></button>`).join("")}</div>` : ""}
      <div class="mlm-hint">${esc(t("Подсказка: перетащите окно к краю или в угол — оно встанет само.", "Tip: drag a window to an edge or a corner and it snaps."))}</div>`;
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    const width = menu.offsetWidth || 300;
    menu.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width)) + "px";
    menu.style.top = Math.min(window.innerHeight - menu.offsetHeight - 8, rect.bottom + 6) + "px";
    menu.addEventListener("click", (event) => {
      const snap = event.target.closest("[data-snap]");
      const all2 = event.target.closest("[data-arrange]");
      if (snap) {
        if (snap.dataset.snap === "center") recenter(win.id);
        else snapWindow(win, snap.dataset.snap);
      } else if (all2) {
        focusWindow(win);
        arrange(all2.dataset.arrange);
      } else return;
      hideLayoutMenu();
    });
    setTimeout(() => {
      document.addEventListener("pointerdown", layoutOutside, true);
      document.addEventListener("keydown", layoutEscape, true);
    }, 0);
    const first = menu.querySelector("button");
    if (first) first.focus();
  }

  function layoutOutside(event) {
    if (!event.target.closest(".module-layout-menu") && !event.target.closest('[data-win="menu"], [data-mhb="menu"], [data-taskbar-layout]')) hideLayoutMenu();
  }
  function layoutEscape(event) {
    if (event.key === "Escape") { event.preventDefault(); hideLayoutMenu(); }
  }
  function hideLayoutMenu() {
    $$(".module-layout-menu").forEach((node) => node.remove());
    document.removeEventListener("pointerdown", layoutOutside, true);
    document.removeEventListener("keydown", layoutEscape, true);
  }

  /* ------------------------------- панель задач ------------------------------ */

  function renderTaskbar() {
    try { document.dispatchEvent(new CustomEvent("memu-modules-changed")); } catch (error) { /* old engine */ }
    markOpenLaunchers();
    syncHeadbar();
    syncAppCloseHint();
    if (!wins.size || ![...wins.values()].some((win) => !win.minimized)) hideCoach();
    const bar = $("#module-taskbar");
    if (!bar || !bar.appendChild) return;
    const list = [...wins.values()];
    const show = list.length > 1 || list.some((win) => win.minimized);
    bar.hidden = !show;
    const layer = $("#module-layer");
    if (layer && layer.dataset && layer.dataset.taskbar !== (show ? "1" : "")) {
      layer.dataset.taskbar = show ? "1" : "";
      // Развёрнутый лист и привязки уступают место панели задач.
      if (typeof queueLayout === "function") queueLayout();
    }
    if (!show) { bar.innerHTML = ""; return; }
    // Round 31: every open module is a pill with its own × (close just this
    // one); a minimized module is marked and says how to bring it back.
    const pill = (win) => {
      const name = titleOf(win.row);
      const active = win.id === openId && !win.minimized;
      const tip = win.minimized ? t(`${name} — свёрнут, работает. Нажмите, чтобы вернуть`, `${name} — minimized, still running. Click to bring it back`)
        : active ? t(`${name} — нажмите, чтобы свернуть`, `${name} — click to minimize`) : t(`${name} — показать`, `${name} — show`);
      const closeTip = t(`Закрыть «${name}»`, `Close “${name}”`);
      return `<span class="module-task-pill ${active ? "active" : ""} ${win.minimized ? "minimized" : ""}" data-pill="${esc(win.id)}">
        <button type="button" class="module-task ${active ? "active" : ""} ${win.minimized ? "minimized" : ""}"
          data-task="${esc(win.id)}" title="${esc(tip)}" aria-label="${esc(tip)}" aria-pressed="${win.minimized ? "false" : "true"}">
          ${moduleIcon(win.row)}<span>${esc(name)}</span>${win.minimized ? `<i class="module-task-badge">${esc(t("свёрнут", "minimized"))}</i>` : ""}</button>
        <button type="button" class="module-task-x" data-task-close="${esc(win.id)}" title="${esc(closeTip)}" aria-label="${esc(closeTip)}">${CTRL_ICONS.close}</button></span>`;
    };
    const many = list.length > 1;
    bar.innerHTML = `<span class="module-task-label" aria-hidden="true">${esc(t("Модули", "Modules"))}</span>`
      + list.map(pill).join("")
      + (many ? `<span class="module-task-sep"></span>
      <button type="button" class="module-task tool" data-taskbar-layout title="${esc(t("Расположить окна", "Arrange windows"))}" aria-label="${esc(t("Расположить окна", "Arrange windows"))}">${layoutIcon([[0, 0, .5, 1], [.5, 0, .5, .5], [.5, .5, .5, .5]])}</button>
      <button type="button" class="module-task tool" data-taskbar-close title="${esc(t("Закрыть все модули", "Close all modules"))}" aria-label="${esc(t("Закрыть все модули", "Close all modules"))}">${typeof icon === "function" ? icon("close") : "×"}</button>` : "");
    bar.querySelectorAll("[data-task]").forEach((button) => {
      button.onclick = () => {
        const win = wins.get(button.dataset.task);
        if (!win) return;
        if (win.minimized) restoreWindow(win);
        else if (win.id === openId) minimizeWindow(win);
        else focusWindow(win);
      };
      button.oncontextmenu = (event) => {
        event.preventDefault();
        const win = wins.get(button.dataset.task);
        if (!win) return;
        popMenu(event, [
          { label: win.minimized ? t("Развернуть", "Restore") : t("Свернуть", "Minimize"), icon: "focus",
            run: () => (win.minimized ? restoreWindow(win) : minimizeWindow(win)) },
          { label: t("Вернуть в центр", "Move to the centre"), icon: "target", run: () => { restoreWindow(win); recenter(win.id); } },
          { label: t("Закрыть", "Close"), icon: "close", danger: true, hint: hotkeyLabel("mod_close", "Ctrl+W"), run: () => requestClose(win) },
        ]);
      };
    });
    bar.querySelectorAll("[data-task-close]").forEach((button) => {
      button.onclick = (event) => {
        event.stopPropagation();
        requestClose(wins.get(button.dataset.taskClose));
      };
    });
    const layoutButton = bar.querySelector("[data-taskbar-layout]");
    if (layoutButton) layoutButton.onclick = () => {
      const win = wins.get(openId) || topWindow() || list[0];
      if (win) showLayoutMenu(win, layoutButton);
    };
    const closeButton = bar.querySelector("[data-taskbar-close]");
    if (closeButton) closeButton.onclick = () => requestCloseAll();
  }

  /* --------------------------------- перетаскивание -------------------------- */

  function shieldFrames(on) {
    // Пока окно тянут, iframe не должен забирать события мыши: иначе движение
    // над содержимым модуля терялось, окно «залипало» и дёргалось.
    const layer = $("#module-layer");
    if (layer && layer.classList) layer.classList.toggle("dragging", !!on);
  }

  function snapZone(clientX, clientY) {
    const layer = $("#module-layer");
    const rect = layer.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const edge = 14;
    const corner = 90;
    const nearLeft = x <= edge, nearRight = x >= rect.width - edge, nearTop = y <= edge, nearBottom = y >= rect.height - edge;
    const topBand = y <= corner, bottomBand = y >= rect.height - corner, leftBand = x <= corner, rightBand = x >= rect.width - corner;
    if ((nearLeft && topBand) || (nearTop && leftBand)) return "top-left";
    if ((nearRight && topBand) || (nearTop && rightBand)) return "top-right";
    if ((nearLeft && bottomBand) || (nearBottom && leftBand)) return "bottom-left";
    if ((nearRight && bottomBand) || (nearBottom && rightBand)) return "bottom-right";
    if (nearTop) return "max";
    if (nearLeft) return "left";
    if (nearRight) return "right";
    return "";
  }

  function showSnapPreview(zone) {
    const preview = $("#module-snap");
    if (!preview) return;
    if (!zone) { preview.hidden = true; return; }
    const rect = fracRect(SNAPS[zone]);
    preview.style.left = rect.x + "px";
    preview.style.top = rect.y + "px";
    preview.style.width = rect.width + "px";
    preview.style.height = rect.height + "px";
    preview.hidden = false;
  }

  function startDrag(win, event) {
    const el = win.el;
    const bar = win.parts.bar;
    const startX = event.clientX;
    const startY = event.clientY;
    let rect = el.getBoundingClientRect();
    const layerRect = $("#module-layer").getBoundingClientRect();
    let left = rect.left - layerRect.left;
    let top = rect.top - layerRect.top;
    let dragged = false;
    let zone = "";
    let frame = 0;
    let pending = null;
    const clampAxis = (value, size, extent, margin) => {
      const room = extent - size - margin;
      if (room >= 0) return Math.max(0, Math.min(room, value));
      return Math.max(room, Math.min(0, value));
    };
    const apply = () => {
      frame = 0;
      if (!pending) return;
      const ev = pending;
      pending = null;
      el.style.left = clampAxis(left + ev.clientX - startX, rect.width, layerRect.width, 8) + "px";
      el.style.top = clampAxis(top + ev.clientY - startY, rect.height, layerRect.height, 8) + "px";
      el.style.right = "auto";
      zone = snapZone(ev.clientX, ev.clientY);
      showSnapPreview(zone);
    };
    const move = (ev) => {
      // Простой щелчок по заголовку — не перемещение.
      if (!dragged && Math.abs(ev.clientX - startX) < 3 && Math.abs(ev.clientY - startY) < 3) return;
      if (!dragged) {
        dragged = true;
        shieldFrames(true);
        bar.dataset.dragging = "1";
        if (el.dataset.full === "1" || win.snap) {
          // Как в Windows: привязанное окно при перетаскивании возвращает
          // свой размер и остаётся под курсором в той же доле ширины.
          const ratio = (startX - rect.left) / Math.max(1, rect.width);
          if (el.dataset.full === "1") {
            el.dataset.full = "";
            const size = win.savedBox.width ? win.savedBox : win.policy;
            el.style.width = size.width + "px";
            el.style.height = size.height + "px";
            refreshResizers(win);
            syncFullButton(win);
          } else unsnap(win);
          rect = el.getBoundingClientRect();
          left = startX - layerRect.left - rect.width * ratio;
          top = startY - layerRect.top - 18;
        }
      }
      pending = ev;
      if (!frame) frame = requestAnimationFrame(apply);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      if (frame) cancelAnimationFrame(frame);
      if (pending) apply();
      shieldFrames(false);
      showSnapPreview("");
      bar.dataset.dragging = "";
      if (!dragged) return;
      if (zone) { snapWindow(win, zone); return; }
      el.dataset.moved = "1";
      el.dataset.centered = "";
      persistBox(win).catch(() => {});
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  function startResize(win, edge, event) {
    const el = win.el;
    const axis = edge.dataset.resize || "se";
    const startX = event.clientX;
    const startY = event.clientY;
    const layerRect = $("#module-layer").getBoundingClientRect();
    const rect = el.getBoundingClientRect();
    const start = { left: rect.left - layerRect.left, top: rect.top - layerRect.top, width: rect.width, height: rect.height };
    // Нижняя граница — авторский минимум, но не больше рабочей области;
    // верхняя — авторский максимум, но не дальше края MemuAI.
    const minW = Math.min(win.policy.min_width, layerRect.width - 16);
    const minH = Math.min(win.policy.min_height, layerRect.height - 16);
    let frame = 0;
    let pending = null;
    try { edge.setPointerCapture(event.pointerId); } catch (error) { /* старый движок */ }
    shieldFrames(true);
    unsnap(win, true);
    const apply = () => {
      frame = 0;
      if (!pending) return;
      const ev = pending;
      pending = null;
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      let { left, top, width, height } = start;
      if (axis.includes("e")) width = Math.min(start.width + dx, layerRect.width - start.left);
      if (axis.includes("s")) height = Math.min(start.height + dy, layerRect.height - start.top);
      if (axis.includes("w")) {
        width = Math.min(start.width - dx, start.left + start.width);
        width = Math.max(minW, Math.min(win.policy.max_width, width));
        left = start.left + start.width - width;
      }
      if (axis.includes("n")) {
        height = Math.min(start.height - dy, start.top + start.height);
        height = Math.max(minH, Math.min(win.policy.max_height, height));
        top = start.top + start.height - height;
      }
      width = Math.max(minW, Math.min(win.policy.max_width, width));
      height = Math.max(minH, Math.min(win.policy.max_height, height));
      el.style.width = Math.round(width) + "px";
      el.style.height = Math.round(height) + "px";
      if (axis.includes("w") || axis.includes("n")) {
        el.style.left = Math.round(left) + "px";
        el.style.top = Math.round(top) + "px";
        el.dataset.moved = "1";
        el.dataset.centered = "";
      }
    };
    const move = (ev) => {
      pending = ev;
      if (!frame) frame = requestAnimationFrame(apply);
    };
    const up = (ev) => {
      edge.removeEventListener("pointermove", move);
      edge.removeEventListener("pointerup", up);
      edge.removeEventListener("pointercancel", up);
      try { edge.releasePointerCapture(ev.pointerId); } catch (error) { /* уже отпущено */ }
      if (frame) cancelAnimationFrame(frame);
      if (pending) apply();
      shieldFrames(false);
      el.dataset.resized = "1";
      persistBox(win).catch(() => {});
    };
    edge.addEventListener("pointermove", move);
    edge.addEventListener("pointerup", up);
    edge.addEventListener("pointercancel", up);
  }

  function bindWindow(win) {
    const el = win.el;
    if (el.dataset.bound === "1") return;
    el.dataset.bound = "1";
    const p = win.parts;
    const toolIcons = { menu: "more", min: "undo", full: "focus", close: "close" };
    Object.keys(toolIcons).forEach((name) => {
      const button = p[name];
      if (button && !String(button.innerHTML || "").trim() && typeof icon === "function") button.innerHTML = icon(toolIcons[name]);
    });
    if (p.min && p.min.innerHTML !== undefined) p.min.innerHTML = CTRL_ICONS.minimize + '<span class="module-ctl-text"></span>';
    if (p.close && p.close.innerHTML !== undefined) p.close.innerHTML = CTRL_ICONS.close + '<span class="module-ctl-text"></span>';
    if (p.close && p.close.classList) p.close.classList.add("module-ctl-close");
    if (p.min && p.min.classList) p.min.classList.add("module-ctl-min");
    labelControls(p);
    // Подсказка «Esc» у развёрнутого листа: она же кнопка возврата.
    const tools = el.querySelector ? el.querySelector(".module-window-tools") : null;
    if (tools && !tools.querySelector('[data-win="esc"]')) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "module-esc";
      chip.dataset.win = "esc";
      chip.hidden = true;
      tools.insertBefore(chip, tools.firstChild);
    }
    p.esc = tools ? tools.querySelector('[data-win="esc"]') : null;
    if (p.esc) {
      p.esc.innerHTML = `<kbd>Esc</kbd><span>${esc(t("окном", "window"))}</span>`;
      p.esc.title = t("Вернуть обычное окно (Esc)", "Back to a normal window (Esc)");
      p.esc.setAttribute("aria-label", p.esc.title);
    }
    const current = () => wins.get(el.dataset.module);
    if (p.frame) {
      p.frame.addEventListener("load", () => {
        const w = current();
        if (!w) return;
        if (w.parts.loading) w.parts.loading.hidden = true;
        setStatus(w.toolbar ? w.toolbar.status : "", w, "module");
        postTo(w, { type: "memu-module-host", action: "theme", payload: hostEnv() });
        w.lastState = "";
        syncFullButton(w);
        const nav = pendingNavigate.get(w.id);
        if (nav) {
          pendingNavigate.delete(w.id);
          // Модуль успевает подписаться на события до первой отрисовки.
          setTimeout(() => postTo(w, { type: "memu-module-host", action: "host-event", payload: { name: "navigate", data: nav } }), 60);
        }
      });
    }
    el.addEventListener("pointerdown", () => { const w = current(); if (w && w.id !== openId) focusWindow(w); }, true);
    if (p.close) p.close.addEventListener("click", () => requestClose(current()));
    if (p.min) p.min.addEventListener("click", () => minimizeWindow(current()));
    if (p.full) p.full.addEventListener("click", () => toggleFull(current()));
    if (p.esc) p.esc.addEventListener("click", () => { const w = current(); if (w && isFullscreen(w)) toggleFull(w); });
    if (p.menu) p.menu.addEventListener("click", (event) => { const w = current(); if (w) windowMenu(w, event); });
    const bar = p.bar;
    if (!bar) return;
    // Двойной щелчок ловим сами: pointerdown отменяет действие по умолчанию, и
    // браузер может не прислать dblclick. Двойной щелчок разворачивает окно
    // на весь MemuAI и обратно, как в Windows.
    let lastPress = { at: 0, x: 0, y: 0 };
    bar.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      if (event.target.closest("button, .module-window-tools")) return;
      const w = current();
      if (!w || !w.policy.draggable) return;
      event.preventDefault();
      const now = Date.now();
      const repeat = now - lastPress.at < 400 && Math.abs(event.clientX - lastPress.x) < 5
        && Math.abs(event.clientY - lastPress.y) < 5;
      lastPress = { at: repeat ? 0 : now, x: event.clientX, y: event.clientY };
      if (repeat) {
        if (w.policy.fullscreen_allowed) toggleFull(w);
        else recenter(w.id);
        return;
      }
      startDrag(w, event);
    });
    p.resizers.forEach((edge) => {
      edge.addEventListener("pointerdown", (event) => {
        const w = current();
        if (!w || !w.policy.resizable || w.el.dataset.full === "1" || event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        startResize(w, edge, event);
      });
    });
  }

  let layerBound = false;
  function bindLayer() {
    if (layerBound) return;
    layerBound = true;
    lastKnownViewport = { width: window.innerWidth, height: window.innerHeight };
    window.addEventListener("message", onModuleMessage);
    window.addEventListener("resize", () => {
      const shrankX = window.innerWidth < lastKnownViewport.width;
      const shrankY = window.innerHeight < lastKnownViewport.height;
      lastKnownViewport = { width: window.innerWidth, height: window.innerHeight };
      if (wins.size) layoutLayer();
      wins.forEach((win) => {
        const el = win.el;
        if (el.dataset.full === "1") { applyExpanded(win); return; }
        if (win.minimized) return;
        if (win.snap && SNAPS[win.snap]) {
          // Привязанное окно держит свою долю экрана при любом размере MemuAI.
          const rect = fracRect(SNAPS[win.snap]);
          Object.assign(el.style, { left: rect.x + "px", top: rect.y + "px", width: rect.width + "px", height: rect.height + "px" });
          return;
        }
        if (el.dataset.moved !== "1") { applyRect(win, { center: true }); return; }
        const rect = el.getBoundingClientRect();
        const area = layerArea();
        let left = parseFloat(el.style.left) || 0;
        let top = parseFloat(el.style.top) || 0;
        let changed = false;
        if (shrankX && left + rect.width > area.width) { left = Math.max(0, area.width - rect.width - 8); changed = true; }
        if (shrankY && top + rect.height > area.height) { top = Math.max(0, area.height - rect.height - 8); changed = true; }
        if (rect.width > area.width - 8) { el.style.width = Math.max(240, area.width - 16) + "px"; changed = true; }
        if (rect.height > area.height - 8) { el.style.height = Math.max(200, area.height - 16) + "px"; changed = true; }
        if (changed) {
          el.style.left = Math.round(left) + "px";
          el.style.top = Math.round(top) + "px";
          persistBox(win).catch(() => {});
        }
      });
    });
    // Esc, пока фокус в самом MemuAI, сворачивает развёрнутый лист. Слушаем в
    // фазе захвата на window: иначе общий обработчик app.js принял бы Esc за
    // «остановить ответ». Поля ввода и открытые меню Esc не теряют.
    window.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof Element && target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true']")) return;
      if (escapeWindow(wins.get(openId), false)) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    }, true);
    // Горячие клавиши окон, пока фокус в самом MemuAI: Alt+Shift+стрелки
    // привязывают активное окно к краю, Alt+Shift+M сворачивает его.
    // Round 29: сочетания из реестра hotkeys.js (Настройки → Горячие клавиши), по умолчанию Alt+Shift+….
    // Round 31: + Ctrl+W закрывает активный модуль (то же пересылает мост из окна модуля).
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.repeat) return;
      const win = wins.get(openId);
      if (!win || overlayOpen()) return;
      if (windowHotkey(win, event)) event.preventDefault();
    });
  }

/* --------------------------------- мост ---------------------------------- */

  /* Предпросмотр в мастерской — тот же мост, но без записи на диск и без
     обращений к моделям: раньше он не отвечал вовсе, и Memu.models() в
     предпросмотре висел двадцать секунд до тайм-аута. */
  function onPreviewMessage(event, preview) {
    const data = event.data || {};
    if (data.type !== "memu-module") return;
    const reply = (result, error) => {
      try {
        preview.contentWindow.postMessage({ type: "memu-module-result", id: data.id,
          result: result === undefined ? null : result, error: error ? String(error) : "" }, "*");
      } catch (problem) { /* предпросмотр перерисован */ }
    };
    const payload = data.payload || {};
    const permissions = codeState && Array.isArray(codeState.previewPermissions) ? codeState.previewPermissions : [];
    const needed = ACTION_PERMISSION[data.action];
    if (needed && !permissions.includes(needed)) {
      return reply(null, t(`Модулю не выдано право «${needed}»`, `The module lacks the “${needed}” permission`));
    }
    const moduleId = (codeState && codeState.id) || "preview";
    switch (data.action) {
      case "env": return reply(hostEnv());
      case "log": return reply(logModule(moduleId, payload));
      case "save": return reply({ ...(payload || {}) });
      case "resize": case "close": case "cancel": case "unsaved": return reply({ ok: true, preview: true });
      case "routes": return apiJson("/api/pg/modules/routes").then((value) => reply(value), (error) => reply(null, error.message));
      case "models": return apiJson("/api/pg/modules/models", { method: "POST" })
        .then((value) => reply(value.models || []), (error) => reply(null, error.message));
      case "clipboard": return Promise.resolve(typeof copyText === "function"
        ? copyText(String(payload.text || ""), payload.notify !== false) : navigator.clipboard.writeText(String(payload.text || "")))
        .then(() => reply({ ok: true }), (error) => reply(null, error.message));
      default:
        return reply(null, t("В предпросмотре это действие не выполняется: сохраните код и откройте окно модуля",
          "This action does not run in the preview: save the code and open the module window"));
    }
  }

  async function onModuleMessage(event) {
    const data = event.data || {};
    if (data.type === "memu-module-key") {
      // Esc внутри модуля, который сам его не обработал (мост шлёт только
      // необработанное нажатие вне полей ввода), сворачивает развёрнутый лист.
      const win = [...wins.values()].find((item) => item.frame && item.frame.contentWindow
        && event.source === item.frame.contentWindow);
      if (!win || String(data.from || "") !== win.id) return;
      if (data.key === "Escape") escapeWindow(win, true);
      else if (data.key === "combo" && data.combo && typeof data.combo === "object") {
        // Round 38: in a full-screen module the main window's shortcuts (Ctrl+K, settings…) play in MemuAI
        // and the module steps aside into its pill (module-focus.js).
        if (windowHotkey(win, data.combo)) return;
        if (window.MemuModuleAppbar && window.MemuModuleAppbar.key(data.combo)) return;
        if (window.MemuModuleFocus) window.MemuModuleFocus.replayKey(win.id, data.combo);
      }
      return;
    }
    const preview = document.getElementById("mw-preview");
    if (preview && preview.contentWindow && event.source === preview.contentWindow) return onPreviewMessage(event, preview);
    // Окно-отправитель узнаём по самому источнику сообщения: открыто может
    // быть несколько модулей, и отвечать нужно именно тому, кто спросил.
    let owner = [...wins.values()].find((win) => win.frame && win.frame.contentWindow
      && event.source === win.frame.contentWindow);
    if (!owner && closingFrames.has(event.source) && (["save", "log"].includes(data.action)
        || data.action === "music" && data.payload?.action === "cancel")) {
      owner = closingFrames.get(event.source);
    }
    if (!owner) return;
    const frame = owner.frame;
    const replyTo = event.source;
    if (data.type !== "memu-module") return;
    const reply = (result, error) => {
      try {
        replyTo.postMessage({
          type: "memu-module-result",
          id: data.id,
          result: result === undefined ? null : result,
          error: error ? String(error) : "",
        }, "*");
      } catch (error) { /* окно закрыто */ }
    };
    const payload = data.payload || {};
    // Идентификатор берём из сообщения, а не из «открытого модуля»: во время
    // перехода на другой модуль страница прежнего ещё может успеть отправить
    // отложенное сохранение, и тогда данные попали бы в чужой файл.
    const claimed = String(data.from || "");
    if (claimed && claimed !== owner.id) {
      return reply(null, t("Модуль уже закрыт: данные не сохранены",
        "This module is already closed: nothing was saved"));
    }
    const moduleId = owner.id;
    const row = catalog.find((item) => item.id === moduleId) || {};
    if (!moduleId) return reply(null, t("Окно модуля закрыто", "The module window is closed"));
    // Часть действий не доходит до маршрута с проверкой прав (буфер обмена,
    // список моделей, история чата), поэтому права проверяются и здесь.
    const needed = ACTION_PERMISSION[data.action];
    if (needed && !(row.permissions || []).includes(needed)) {
      return reply(null, t(`Модулю не выдано право «${needed}»`, `The module lacks the “${needed}” permission`));
    }
    const long = LONG_ACTIONS.has(data.action);
    if (long) busyCount.set(moduleId, (busyCount.get(moduleId) || 0) + 1);
    try {
      reply(await dispatchAction(data.action, payload, moduleId, row, data.id, frame));
    } catch (error) {
      reply(null, error && error.message ? error.message : String(error));
    } finally {
      if (long) {
        const left = (busyCount.get(moduleId) || 1) - 1;
        if (left > 0) busyCount.set(moduleId, left); else busyCount.delete(moduleId);
      }
    }
  }

  /* Round 31: what a module is busy with right now — a closing window asks
     first when a generation would be lost or the module reported unsaved work. */
  const LONG_ACTIONS = new Set(["stream", "complete", "request", "image", "video", "transcribe", "music"]);
  const busyCount = new Map();

  async function dispatchAction(action, payload, moduleId, row, messageId, frame) {
    switch (action) {
      case "save":
        return (await apiJson("/api/pg/modules/persist", { method: "POST",
          body: { id: moduleId, fields: payload } })).saved;
      case "models":
        return (await apiJson("/api/pg/modules/models", { method: "POST" })).models || [];
      case "routes":
        return await apiJson("/api/pg/modules/routes");
      case "env":
        return hostEnv();
      case "log":
        return logModule(moduleId, payload);
      case "chat_history":
        return await apiJson("/api/pg/modules/" + encodeURIComponent(moduleId) + "/chat");
      case "clipboard": {
        const text = String(payload.text || "");
        if (typeof copyText === "function") await copyText(text, payload.notify !== false);
        else await navigator.clipboard.writeText(text);
        return { ok: true };
      }
      case "clipboard_read": {
        // Окно модуля — песочница без доступа к буферу обмена: читает хост.
        const text = await navigator.clipboard.readText();
        return { ok: true, text: String(text || "").slice(0, 2_000_000) };
      }
      case "close":
        setTimeout(() => closeWindow(wins.get(moduleId)), 0);
        return { ok: true };
      case "unsaved": {
        // Round 31: the module reports unsaved work; closing it from MemuAI then asks first.
        const win = wins.get(moduleId);
        if (win) win.unsaved = payload.unsaved ? String(payload.text || "").slice(0, 200) || true : false;
        return { ok: true };
      }
      case "toolbar":
        return setToolbar(moduleId, payload);
      case "integration":
        // Round 38: the module's own widget / panel settings in MemuAI (module-integration.js).
        return window.MemuModInt ? await window.MemuModInt.moduleSettings(moduleId, payload) : { modules: [] };
      case "appbar":
        // Round 38: colours / drag areas of the module's own app window bar (module-appbar.js).
        return window.MemuModuleAppbar ? window.MemuModuleAppbar.fromModule(moduleId, payload) : { ok: true, active: false };
      case "host":
        return await runHostCommand(moduleId, payload);
      case "resize":
        return await resizeWindow(moduleId, payload);
      case "request":
        return await runRequest(moduleId, payload, row);
      case "stream":
        return await runStream(moduleId, payload, row, messageId, frame);
      case "complete":
        // Round 29: full message array + sampler parameters (app/characters_api.py).
        return await runStream(moduleId, payload, row, messageId, frame, "/api/pg/modules/complete");
      case "store":
        return await apiJson("/api/pg/modules/store", { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "music_call":
        // Round 37: MemuMusic (app/music/api.py) — the answer is {result}.
      {
        const body = { op: String(payload.op || ""), args: payload.args || {}, module_id: moduleId, lang: en() ? "en" : "ru" };
        // r40-lag: the 25 s long-poll «player.wait» goes over a WebSocket — off the 6-connection HTTP pool
        if (body.op === "player.wait" && window.MemuLive) return (await MemuLive.post("/api/pg/music/call", body)).result;
        return (await apiJson("/api/pg/music/call", { method: "POST", body })).result;
      }
      case "learn_call":
        // r40-learn: MemuLearn (app/learn/api.py) — the answer is {result}.
        return (await apiJson("/api/pg/learn/call", { method: "POST",
          body: { op: String(payload.op || ""), args: payload.args || {}, module_id: moduleId, lang: en() ? "en" : "ru" } })).result;
      case "net_import":
        // Round 29: import by link (app/character_import.py); MemuAI asks the person first.
        return await runNetImport(moduleId, payload, row);
      case "asset":
        return await apiJson("/api/pg/modules/asset", { method: "POST",
          body: { name: String(payload.name || ""), module_id: moduleId } });
      case "cancel": {
        // Отменить можно только свой поток: чужие id предсказуемы.
        const id = String(payload.id || "");
        const job = activeStreams.get(id);
        if (!job || job.moduleId !== moduleId) return { ok: true, cancelled: false };
        return cancelJob(id);
      }
      case "image":
      case "video":
        return await runMedia(moduleId, action, payload);
      case "extract":
        return await apiJson("/api/pg/modules/extract", { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "speak":
        return await apiJson("/api/pg/modules/speak", { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "music":
      case "monitor":
        return await apiJson(`/api/pg/modules/${action}`, { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "transcribe":
        return await apiJson("/api/pg/modules/transcribe", { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "record":
        return await hostRecord(moduleId, payload);
      case "handoff":
        return await runHandoff(moduleId, payload);
      case "download":
        return await apiJson("/api/pg/modules/download", { method: "POST",
          body: { ...payload, module_id: moduleId } });
      case "notify":
        return await runNotify(moduleId, payload);
      case "open":
        return await runOpen(moduleId, payload);
      case "search":
      case "fetch":
        return await apiJson("/api/pg/modules/search", { method: "POST",
          body: { ...payload, action, module_id: moduleId } });
      default:
        throw new Error(t(`Неизвестное действие модуля: ${action}`,
          `Unknown module action: ${action}`));
    }
  }

  /* Запись с микрофона идёт в окне MemuAI: у страницы модуля в песочнице нет
     своего источника, и браузер не даёт ей микрофон ни при каких правах. */
  let recording = null;

  function stopTracks(stream) {
    try { stream.getTracks().forEach((track) => track.stop()); } catch (error) { /* уже закрыт */ }
  }

  function cancelRecording() {
    if (!recording) return;
    const current = recording;
    recording = null;
    try { if (current.recorder.state !== "inactive") current.recorder.stop(); } catch (error) { /* уже закрыт */ }
    stopTracks(current.stream);
  }

  async function hostRecord(moduleId, payload) {
    const action = String(payload.action || "start");
    if (action === "cancel") {
      if (recording && recording.module === moduleId) cancelRecording();
      return { ok: true, recording: false };
    }
    if (action === "start") {
      if (recording) {
        if (recording.module !== moduleId) throw new Error(t("Микрофон занят другим модулем", "Another module is using the microphone"));
        return { ok: true, recording: true, started: recording.started };
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
        throw new Error(t("Запись с микрофона недоступна", "Microphone recording is unavailable"));
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        throw new Error(error && error.name === "NotAllowedError"
          ? t("Нет доступа к микрофону", "Microphone access was denied")
          : t("Микрофон не найден или занят", "No microphone is available"));
      }
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (event) => { if (event.data && event.data.size) chunks.push(event.data); };
      recording = { module: moduleId, recorder, stream, chunks, started: Date.now() };
      recorder.start(250);
      return { ok: true, recording: true, started: recording.started };
    }
    if (action !== "stop") throw new Error(t("Неизвестное действие записи", "Unknown recording action"));
    if (!recording || recording.module !== moduleId) throw new Error(t("Запись не идёт", "Nothing is being recorded"));
    const current = recording;
    recording = null;
    const finished = new Promise((resolve) => {
      current.recorder.onstop = resolve;
      setTimeout(resolve, 3000);
    });
    try { if (current.recorder.state !== "inactive") current.recorder.stop(); } catch (error) { /* уже закрыт */ }
    await finished;
    stopTracks(current.stream);
    const blob = new Blob(current.chunks, { type: current.recorder.mimeType || "audio/webm" });
    if (!blob.size) throw new Error(t("Запись пустая", "The recording is empty"));
    return { audio: await readBlob(blob), mime: blob.type, bytes: blob.size, ms: Date.now() - current.started };
  }

  const moduleLogs = new Map();
  function moduleLog(moduleId) {
    return moduleLogs.get(moduleId) || [];
  }

  function logModule(moduleId, payload) {
    const rows = moduleLogs.get(moduleId) || [];
    rows.push({
      at: Date.now(),
      level: String(payload.level || "info"),
      text: String(payload.text || "").slice(0, 2000),
      line: Number(payload.line) || 0,
    });
    while (rows.length > 200) rows.shift();
    moduleLogs.set(moduleId, rows);
    if (payload.level === "error") console.warn(`[module ${moduleId}] ${payload.text}`);
    return { ok: true };
  }

  async function resizeWindow(moduleId, payload) {
    const win = wins.get(moduleId);
    if (!win) throw new Error(t("Окно не открыто", "No window is open"));
    const rules = win.policy;
    if (rules.fixed) throw new Error(t("Автор запретил менять размер", "The author locked the size"));
    const width = Math.round(Number(payload.width) || 0);
    const height = Math.round(Number(payload.height) || 0);
    const area = layerArea();
    unsnap(win, true);
    if (width) win.el.style.width = Math.max(rules.min_width, Math.min(rules.max_width, area.width - 16, width)) + "px";
    if (height) win.el.style.height = Math.max(rules.min_height, Math.min(rules.max_height, area.height - 16, height)) + "px";
    if (win.el.dataset.moved !== "1") applyRect(win, { center: true });
    await persistBox(win).catch(() => {});
    const rect = win.el.getBoundingClientRect();
    return { width: Math.round(rect.width), height: Math.round(rect.height) };
  }

  function absorbChat(chat) {
    if (!chat || typeof S === "undefined") return;
    const existing = (S.chats || []).find((item) => item.id === chat.id);
    if (existing) existing.messages = chat.messages;
    else S.chats.unshift(chat);
    if (typeof saveLocalChats === "function") saveLocalChats();
    if (typeof renderChats === "function") renderChats();
  }

  async function runRequest(moduleId, payload, row) {
    const result = await apiJson("/api/pg/modules/request", { method: "POST", body: {
      ...payload, module_id: moduleId, title: titleOf(row), execute: true,
    } });
    absorbChat(result.chat);
    return result;
  }

  const activeStreams = new Map();

  function runStream(moduleId, payload, row, messageId, frame, path) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const send = (message) => {
        try {
          frame.contentWindow.postMessage({ type: "memu-module-event", id: messageId, ...message }, "*");
        } catch (error) { /* окно закрыто */ }
      };
      const finish = (error, result) => {
        if (settled) return;
        settled = true;
        activeStreams.delete(messageId);
        if (error) reject(new Error(String(error)));
        else resolve(result);
      };
      // Остановка обрывает и сам запрос: иначе модель продолжала писать
      // ответ в пустоту, а следующий запрос ждал своей очереди.
      const abort = new AbortController();
      activeStreams.set(messageId, { moduleId, cancel: () => {
        finish(t("Остановлено", "Stopped"));
        try { abort.abort(); } catch (error) { /* уже закрыт */ }
      } });
      send({ event: "status", status: "queued" });
      (async () => {
        let response;
        try {
          response = await fetch(path || "/api/pg/modules/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, module_id: moduleId, title: titleOf(row) }),
            signal: abort.signal,
          });
        } catch (error) {
          return finish(error.message || String(error));
        }
        if (!response.ok) {
          let message = `HTTP ${response.status}`;
          try {
            const data = await response.json();
            message = data.error || message;
          } catch (error) { /* тело не JSON */ }
          return finish(message);
        }
        try {
          for await (const chunk of readSSE(response.body)) {
            if (settled) return;
            let item;
            try { item = JSON.parse(chunk); } catch (error) { continue; }
            if (item.status) send({ event: "status", status: item.status });
            if (item.event === "delta") send({ event: "delta", delta: item.delta });
            else if (item.event === "reasoning") send({ event: "reasoning", delta: item.delta });
            else if (item.event === "usage") send({ event: "usage", usage: item.usage });
            else if (item.event === "error") return finish(item.error || t("Ошибка модели", "Model error"));
            else if (item.event === "done") {
              const result = item.result || {};
              absorbChat(result.chat);
              send({ event: "done", result });
              return finish("", result);
            }
          }
          if (!settled) finish(t("Поток ответа прервался", "The answer stream ended unexpectedly"));
        } catch (error) {
          finish(error.message || String(error));
        }
      })();
    });
  }

  /* Закрытое или перезагруженное окно не должно дописывать ответ в чат. */
  function cancelModuleStreams(moduleId) {
    activeStreams.forEach((job, id) => { if (job.moduleId === moduleId) cancelJob(id); });
  }

  function cancelJob(messageId) {
    const job = activeStreams.get(messageId);
    if (job) {
      job.cancel();
      return { ok: true, cancelled: true };
    }
    return { ok: true, cancelled: false };
  }

  function errorText(value) {
    if (typeof value === "string") return value.trim();
    if (value && typeof value === "object") {
      const nested = value.message != null ? value.message : value.detail;
      if (typeof nested === "string") return nested.trim();
      if (nested && typeof nested === "object") return errorText(nested);
    }
    return "";
  }

  // proxy_sse отдаёт ошибку админки как HTTP 200 и кадр {"error":{"message"}} без [DONE].
  function mediaFailureMessage(item) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return "";
    const fromError = errorText(item.error);
    if (fromError) return fromError;
    if (item.status === "error") return errorText(item.message) || t("Ошибка генерации", "Generation error");
    return "";
  }

  function consumeMediaSse(text) {
    const state = { urls: [], failure: "", done: false, ms: 0, status: "" };
    String(text || "").split(/\r?\n/).forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) return;
      const raw = trimmed.slice(5).trim();
      if (!raw || raw === "[DONE]") return;
      let item;
      try { item = JSON.parse(raw); } catch (error) { return; }
      const failure = mediaFailureMessage(item);
      if (failure) state.failure = failure;
      if (item.status) state.status = String(item.status);
      if (Number(item.ms)) state.ms = Number(item.ms);
      if (item.status === "done") {
        state.done = true;
        state.urls = Array.isArray(item.urls) ? item.urls : [];
      }
    });
    if (state.failure) throw new Error(state.failure);
    if (!state.done) throw new Error(t("Генерация не завершилась", "Generation did not finish"));
    return { urls: state.urls, ms: state.ms };
  }

  async function runMedia(moduleId, action, payload) {
    // Идём через маршрут модуля, а не через общий /api/pg/image: только он
    // проверяет право media, без которого генерация обязана быть запрещена.
    const response = await fetch("/api/pg/modules/media", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, action, module_id: moduleId }),
    });
    if (!response.ok || !response.body) {
      let data = {};
      try { data = await response.json(); } catch (error) { data = {}; }
      throw new Error(errorText(data && data.error) || errorText(data) || `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = "";
    while (true) {
      const step = await reader.read();
      if (step.done) break;
      text += decoder.decode(step.value, { stream: true });
    }
    text += decoder.decode();
    const media = consumeMediaSse(text);
    media.urls = await inlineMedia(media.urls, moduleId);
    return media;
  }

  function readBlob(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(reader.error || new Error(t("Не удалось прочитать файл", "Could not read the file")));
      reader.readAsDataURL(blob);
    });
  }

  async function inlineMedia(urls, moduleId) {
    const ready = [];
    for (const url of urls || []) {
      const value = String(url || "");
      if (!value) continue;
      if (value.startsWith("data:")) { ready.push(value); continue; }
      const href = value.startsWith("/") ? value
        : "/api/pg/modules/media-file?module_id=" + encodeURIComponent(moduleId || openId || "")
          + "&url=" + encodeURIComponent(value);
      const response = await fetch(href);
      if (!response.ok) {
        throw new Error(t("Не удалось загрузить результат в окно модуля",
          "Could not load the result into the module window"));
      }
      ready.push(await readBlob(await response.blob()));
    }
    return ready;
  }

  /* Round 29 phase 2 — «Import from a link» (Memu.fetchImport, permission «net-import»).
     The download runs in MemuAI (app/character_import.py) with SSRF checks; before the first download
     from a site the person decides: once, always (kept per module) or no. The body is built here, so a
     module can never mark its own request as approved. */
  async function netImportCall(body) {
    const response = await fetch("/api/pg/modules/net-import", { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error((en() ? data.message_en : data.message_ru) || data.error || `HTTP ${response.status}`);
      error.code = data.code || "";
      throw error;
    }
    return data;
  }

  function askNetImport(row, target) {
    return new Promise((resolve) => {
      if (typeof panel !== "function") {
        return resolve(window.confirm(t(`Разрешить загрузку с ${target.label}?`, `Allow downloading from ${target.label}?`)) ? "once" : "deny");
      }
      let settled = false;
      const finish = (answer) => {
        if (settled) return;
        settled = true;
        if (typeof closePanel === "function") closePanel(answer === "deny" ? "cancel" : "confirm");
        document.getElementById("panel-back")?.classList.remove("over-modules");
        resolve(answer);
      };
      const hosts = (target.hosts || []).filter(Boolean);
      // "Chub (chub.ai)" → "Chub" on the button (the full name is in the facts above), so the three answers fit one row.
      const shortLabel = String(target.label || target.source || "").replace(/\s*\([^)]*\)\s*$/, "") || String(target.source || "");
      const what = String(target.url || target.id || "");
      panel(`<div class="net-import-ask">
        <div class="net-import-ask-head"><span class="net-import-ask-icon">${moduleIcon(row && row.id ? row : (row.icon || "download"))}</span>
          <div><h2>${esc(t("Загрузить из интернета?", "Download from the internet?"))}</h2>
          <p class="sub">${esc(t(`Модуль «${titleOf(row)}» просит скачать ${target.kind === "lorebook" ? "лорбук" : "карточку персонажа"}`,
            `The “${titleOf(row)}” module asks to download ${target.kind === "lorebook" ? "a lorebook" : "a character card"}`))}</p></div></div>
        <dl class="net-import-ask-facts">
          <dt>${esc(t("Источник", "Source"))}</dt><dd><b>${esc(target.label || target.source)}</b></dd>
          <dt>${esc(t("Что", "What"))}</dt><dd><code>${esc(what.length > 160 ? what.slice(0, 157) + "…" : what)}</code></dd>
          ${hosts.length ? `<dt>${esc(t("Адреса", "Hosts"))}</dt><dd>${hosts.map((h) => `<code>${esc(h)}</code>`).join(" ")}</dd>` : ""}
        </dl>
        <p class="module-hint">${esc(t("Файл скачивает MemuAI (только публичные адреса HTTPS) и отдаёт только этому модулю. Решение «всегда» можно отменить: Настройки → Модули → ⋯ → Источники импорта.",
          "MemuAI downloads the file (public HTTPS addresses only) and hands it to this module only. You can undo “always”: Settings → Modules → ⋯ → Import sources."))}</p>
        <div class="row-end net-import-ask-actions">
          <button class="pbtn ghost" id="net-import-no">${esc(t("Не разрешать", "Don't allow"))}</button>
          <button class="pbtn ghost" id="net-import-once">${esc(t("Один раз", "Only this time"))}</button>
          <button class="pbtn" id="net-import-always">${esc(t(`Всегда для ${shortLabel}`, `Always for ${shortLabel}`))}</button>
        </div></div>`, "net-import-panel", { onClose: () => finish("deny") });
      // The question comes from a module window, so it is drawn above the module layer (modules.css).
      document.getElementById("panel-back")?.classList.add("over-modules");
      document.getElementById("net-import-no").onclick = () => finish("deny");
      document.getElementById("net-import-once").onclick = () => finish("once");
      document.getElementById("net-import-always").onclick = () => finish("always");
      setTimeout(() => document.getElementById("net-import-once")?.focus(), 30);
    });
  }

  async function runNetImport(moduleId, payload, row) {
    const body = { module_id: moduleId, input: String(payload.input || "").slice(0, 2048),
      kind: ["character", "lorebook"].includes(payload.kind) ? payload.kind : "auto" };
    const target = await netImportCall({ ...body, op: "resolve" });
    if (payload.op === "resolve") return target;
    if (!target.trusted) {
      const answer = await askNetImport(row, target);
      if (answer === "deny") {
        const error = new Error(t("Загрузка не разрешена", "The download was not allowed"));
        error.code = "denied";
        throw error;
      }
      if (answer === "always") {
        await apiJson("/api/pg/modules/net-import/settings", { method: "POST",
          body: { id: moduleId, trust: target.source } }).catch(() => {});
      }
    }
    return await netImportCall({ ...body, op: "fetch", approved: true });
  }

  /* Settings → Modules → ⋯ → «Import sources»: the allow list of direct-link hosts (like SillyTavern's
     whitelistImportDomains) and the sites that no longer ask. Only the person changes this, never a module. */
  async function netImportSettings(row) {
    let state;
    try {
      state = await apiJson("/api/pg/modules/net-import/settings", { method: "POST", body: { id: row.id } });
    } catch (error) { return note(error.message, "err"); }
    const draw = () => {
      panel(`<h2>${esc(t("Источники импорта", "Import sources"))} · ${esc(titleOf(row))}</h2>
        <p class="sub">${esc(t("Откуда модуль может скачивать карточки и лорбуки по ссылке. Загрузка идёт через MemuAI, только по публичным адресам HTTPS.",
          "Where the module may download cards and lorebooks by link. Downloads go through MemuAI, public HTTPS addresses only."))}</p>
        <div class="net-import-sources">${(state.sources || []).filter((key) => key !== "generic").map((key) => `
          <label class="module-check"><input type="checkbox" data-net-trust="${esc(key)}" ${state.trusted.includes(key) ? "checked" : ""}>
            ${esc(state.labels[key] || key)} — ${esc(t("не спрашивать", "don't ask"))}</label>`).join("")}
          ${(state.sources || []).includes("generic") ? `<label class="module-check"><input type="checkbox" data-net-trust="generic" ${state.trusted.includes("generic") ? "checked" : ""}>
            ${esc(t("Прямые ссылки на разрешённые сайты — не спрашивать", "Direct links to allowed hosts — don't ask"))}</label>` : ""}
        </div>
        ${(state.sources || []).includes("generic") ? `<label class="field-label">${esc(t("Разрешённые сайты для прямых ссылок на PNG/JSON (по одному в строке, *.example.org — с поддоменами)",
          "Allowed hosts for direct PNG/JSON links (one per line, *.example.org includes subdomains)"))}
          <textarea class="ptexta" id="net-import-hosts" rows="5" spellcheck="false">${esc((state.hosts || []).join("\n"))}</textarea></label>
        <p class="module-hint">${esc(t("IP-адреса, localhost и локальные имена не принимаются.", "IP addresses, localhost and local names are not accepted."))}
          ${state.custom_hosts ? esc(t(" Сейчас действует ваш список.", " Your own list is in effect.")) : ""}</p>` : ""}
        <div class="row-end">
          ${state.custom_hosts ? `<button class="pbtn ghost" id="net-import-reset">${icon("undo")} ${esc(t("Как в модуле", "Module default"))}</button>` : ""}
          <span class="mu-grow"></span>
          <button class="pbtn ghost" id="net-import-cancel">${esc(t("Отмена", "Cancel"))}</button>
          <button class="pbtn" id="net-import-save">${esc(t("Сохранить", "Save"))}</button>
        </div>`, "net-import-settings");
      const save = async (extra) => {
        const hostsBox = document.getElementById("net-import-hosts");
        const body = { id: row.id, trusted: [...document.querySelectorAll("[data-net-trust]")].filter((box) => box.checked).map((box) => box.dataset.netTrust) };
        if (hostsBox && !(extra && extra.reset_hosts)) body.hosts = hostsBox.value.split(/[\s,]+/).filter(Boolean);
        try {
          state = await apiJson("/api/pg/modules/net-import/settings", { method: "POST", body: { ...body, ...(extra || {}) } });
          note(t("Сохранено", "Saved"), "ok");
          if (extra && extra.reset_hosts) return draw();
          if (typeof closePanel === "function") closePanel("confirm");
        } catch (error) { note(error.message, "err"); }
      };
      document.getElementById("net-import-save").onclick = () => save();
      document.getElementById("net-import-cancel").onclick = () => typeof closePanel === "function" && closePanel("cancel");
      const reset = document.getElementById("net-import-reset");
      if (reset) reset.onclick = () => save({ reset_hosts: true });
    };
    draw();
  }

  async function runHandoff(moduleId, payload) {
    const bundle = await apiJson("/api/pg/modules/handoff", { method: "POST",
      body: { ...payload, module_id: moduleId } });
    const saved = await saveBundle(bundle, moduleId);
    return { ...bundle, saved, paths: saved.map((item) => item.path) };
  }

  async function saveBundle(bundle, moduleId) {
    if (typeof saveToDownloads !== "function") {
      throw new Error(t("Сохранение файла недоступно", "Saving files is unavailable"));
    }
    // Каждая передача — своей папкой: README внутри говорит «откройте
    // index.html в этой папке», а второй пакет не смешивается с первым.
    const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 19);
    const folder = `design-${stamp}`;
    const saved = [];
    const saveText = async (name, text) => {
      const result = await saveToDownloads({ name, text: String(text || ""), folder });
      if (!result || !result.path) throw new Error(t("Не удалось сохранить файл пакета", "Could not save a package file"));
      saved.push(result);
    };
    // Изображения кладём настоящими файлами: пакет должен отдаваться другой ИИ
    // целиком, а не ссылками на data:, которые вне MemuAI ничего не значат.
    const images = bundle.images || [];
    for (let index = 0; index < images.length; index += 1) {
      const name = imageName(images[index], index);
      const result = await apiJson("/api/pg/modules/download", { method: "POST",
        body: { module_id: moduleId, url: images[index], name, folder } });
      if (!result || !result.path) {
        throw new Error(t("Не удалось сохранить изображение пакета",
          "Could not save a bundle image"));
      }
      saved.push(result);
    }
    for (const file of bundle.files || []) await saveText(file.name || "file.txt", file.content || "");
    await saveText("prompt.txt", bundle.prompt || "");
    await saveText("README.md", bundle.readme || "");
    await saveText("design-handoff.json", JSON.stringify({
      prompt: bundle.prompt || "", images: bundle.images || [], files: bundle.files || [],
    }, null, 2));
    if (typeof savedToast === "function") savedToast(saved[saved.length - 1]);
    return saved;
  }

  function imageName(source, index) {
    // Определяем расширение по настоящему типу данных, а не по угадыванию.
    const header = String(source || "").slice(0, 64).toLowerCase();
    let extension = "png";
    if (header.includes("image/jpeg") || header.includes("image/jpg")) extension = "jpg";
    else if (header.includes("image/webp")) extension = "webp";
    else if (header.includes("image/gif")) extension = "gif";
    else if (header.includes("image/svg")) extension = "svg";
    return "design-image-" + String(index + 1).padStart(2, "0") + "." + extension;
  }

  async function runNotify(moduleId, payload) {
    const title = String(payload.title || t("Модуль", "Module"));
    const body = String(payload.text || "");
    try {
      await apiJson("/api/pg/modules/notify", { method: "POST",
        body: { title, text: body, module_id: moduleId } });
    } catch (error) { /* внутренний тост всё равно покажем */ }
    note(title + (body ? ": " + body : ""), "info");
    return { ok: true };
  }

  /* Действие «host» (свободное, без права — список FREE_ACTIONS): переходы
     внутри MemuAI, которые пользователь сам нажал в модуле.
     - { command: "chat", models: [id, name…] } — новый чат с подключённой в
       Proxy моделью, если такая есть; окно модуля сворачивается;
     - { command: "module", id, query, model } — открыть другой (включённый)
       модуль и передать ему событие «navigate» { query, model, from }.
     Ничего не отправляется и не покупается: только выбор в интерфейсе. */
  const pendingNavigate = new Map();
  // Open (or focus) a module and hand it a «navigate» event; also used by global search results.
  async function navigateModule(target, detail) {
    const open_ = wins.get(target);
    if (open_ && open_.parts.loading && open_.parts.loading.hidden) {
      if (open_.minimized) restoreWindow(open_);
      focusWindow(open_);
      postTo(open_, { type: "memu-module-host", action: "host-event", payload: { name: "navigate", data: detail } });
    } else {
      pendingNavigate.set(target, detail);
      await open(target);
    }
  }
  function normalizeModelName(value) {
    return String(value || "").toLowerCase().replace(/^models\//, "").split("/").pop()
      .replace(/:[a-z0-9-]+$/, "").replace(/[^a-z0-9]+/g, "");
  }
  function findConnectedModel(names) {
    const list = (typeof S !== "undefined" && Array.isArray(S.models)) ? S.models : [];
    const wanted = names.map(normalizeModelName).filter((name) => name.length >= 3);
    if (!wanted.length) return null;
    let best = null;
    for (const model of list) {
      const keys = [model.id, model.model, model.name].map(normalizeModelName).filter(Boolean);
      for (const want of wanted) {
        const exact = keys.includes(want);
        const tail = keys.some((key) => key.endsWith(want));
        if (!exact && !tail) continue;
        const score = (exact ? 0 : 1) * 1000 + String(model.id).length;
        if (!best || score < best.score) best = { score, id: model.id };
      }
    }
    return best && best.id;
  }
  async function runHostCommand(moduleId, payload) {
    const command = String(payload.command || "");
    if (command === "chat") {
      const names = (Array.isArray(payload.models) ? payload.models : [payload.model]).map((item) => String(item || "")).slice(0, 6);
      const id = findConnectedModel(names);
      if (!id) return { ok: false, reason: "not_connected" };
      if (typeof newChat === "function") newChat(false);
      if (typeof setCurrentModel === "function") setCurrentModel(id);
      const win = wins.get(moduleId);
      if (win) minimizeWindow(win);
      const input = document.getElementById("input");
      if (input) setTimeout(() => input.focus(), 0);
      if (typeof toast === "function") toast(t("Новый чат с моделью ", "New chat with ") + id, "ok");
      return { ok: true, model: id };
    }
    if (command === "module") {
      const target = String(payload.id || "");
      const row = catalog.find((item) => item.id === target);
      if (!row) return { ok: false, reason: "missing" };
      if (!row.enabled) return { ok: false, reason: "disabled" };
      const detail = { query: String(payload.query || "").slice(0, 300), model: String(payload.model || "").slice(0, 300), from: moduleId };
      await navigateModule(target, detail);
      return { ok: true };
    }
    throw new Error(t("Неизвестная команда host", "Unknown host command"));
  }

  async function runOpen(moduleId, payload) {
    return await apiJson("/api/pg/modules/open", { method: "POST",
      body: { url: payload.url, module_id: moduleId } });
  }
/* ------------------------------- настройки ------------------------------- */

  let currentPane = "list";
  let draft = null;
  let codeState = null;
  let codeEditorObserver = null;

  async function mountSettings(pane) {
    const host = $("#pane-modules");
    if (!host) return;
    if (!host.dataset.tabsBound) {
      host.dataset.tabsBound = "1";
      host.addEventListener("click", (event) => {
        const docs = event.target.closest("[data-open-docs]");
        if (docs && host.contains(docs)) {
          if (window.MemuDocs) MemuDocs.open(docs.dataset.openDocs);
          return;
        }
        const button = event.target.closest("[data-modules-tab]");
        if (!button || !host.contains(button) || button.onclick) return;
        mountSettings(button.dataset.modulesTab);
      });
    }
    // Несохранённые правки мастерской переживают перерисовку раздела
    // (переход на другую вкладку настроек и обратно, повторное открытие).
    if (codeState && codeState.dirty && document.getElementById("mw-html")) {
      codeState.draft = currentCode();
    }
    if (codeEditorObserver) { codeEditorObserver.disconnect(); codeEditorObserver = null; }
    const previousPane = currentPane;
    if (pane) currentPane = pane;
    await refresh();
    await loadTemplates();
    if (previousPane !== currentPane) {
      // Новый экран раздела начинается сверху: иначе мастерская открывалась
      // прокрученной вниз, как был прокручен мастер создания.
      let scroller = host;
      while (scroller && scroller !== document.body && scroller.scrollHeight <= scroller.clientHeight) {
        scroller = scroller.parentElement;
      }
      if (scroller && scroller !== document.body) scroller.scrollTop = 0;
    }
    if (currentPane === "create") return renderCreate(host);
    if (currentPane === "code") return renderCode(host);
    if (currentPane === "import") return renderImport(host);
    currentPane = "list";
    renderList(host);
  }

  function moduleCounts() {
    const enabled = catalog.filter((row) => row.enabled).length;
    const builtin = catalog.filter((row) => row.builtin).length;
    return { total: catalog.length, enabled, builtin, custom: catalog.length - builtin };
  }

  function head(title, subtitle, actions) {
    return `
      <header class="modules-head">
        <div class="modules-head-main">
          <div class="settings-title">${title}</div>
          <p class="sub">${subtitle}</p>
        </div>
        <div class="modules-head-actions">${actions || ""}</div>
      </header>`;
  }

  function backButton(target = "list", label) {
    return `<button type="button" class="pbtn ghost" data-modules-tab="${target}">${icon("back")} ${label || t("К списку", "Back to list")}</button>`;
  }

  function renderList(host) {
    const counts = moduleCounts();
    host.innerHTML = `
      <div class="modules-pane" data-view="list">
        ${head(t("Модули", "Modules"),
          t("Своё приложение внутри MemuAI: HTML, CSS и JavaScript с доступом к моделям через мост MemuAI. Встроенные модули выключены, пока вы их не включите.",
            "An app inside MemuAI: HTML, CSS and JavaScript with model access through the MemuAI bridge. Built-in modules stay off until you enable them."),
          `<button type="button" class="pbtn ghost" data-open-docs="modules" title="${esc(t("Раздел «Модули» в документации MemuAI", "The Modules section of the MemuAI documentation"))}">${icon("info")} ${t("Документация", "Documentation")}</button>
           <button type="button" class="pbtn ghost" data-modules-tab="import">${icon("attach")} ${t("Импорт", "Import")}</button>
           <button type="button" class="pbtn" data-modules-tab="create">${icon("plus")} ${t("Создать модуль", "Create module")}</button>`)}
        <div class="modules-stats">
          <div><b>${counts.total}</b><span>${t("всего", "total")}</span></div>
          <div><b>${counts.enabled}</b><span>${t("включено", "enabled")}</span></div>
          <div><b>${counts.custom}</b><span>${t("своих", "custom")}</span></div>
          <div><b>${counts.builtin}</b><span>${t("встроенных", "built-in")}</span></div>
        </div>
        <div class="modules-toolbar">
          <label class="modules-search">${icon("search")}
            <input type="search" id="modules-filter" placeholder="${t("Найти модуль", "Find a module")}" autocomplete="off" spellcheck="false">
          </label>
          <label class="module-check compact"><input type="checkbox" id="modules-only-enabled"> ${t("Только включённые", "Enabled only")}</label>
        </div>
        <div class="module-list" id="module-list">
          ${catalog.map((row) => moduleCard(row)).join("")}
          <div class="module-list-empty" hidden>${t("Ничего не найдено", "Nothing found")}</div>
        </div>
        ${removedBuiltins.length ? `<section class="modules-removed">
          <div><b>${t("Удалённые встроенные модули", "Removed built-in modules")}</b>
            <span>${t("Их можно вернуть в исходном виде — выключенными и без сохранённых данных.",
              "They come back as shipped — disabled and without saved data.")}</span></div>
          <div class="modules-removed-list">${removedBuiltins.map((row) => `
            <button type="button" class="pbtn small ghost" data-module-restore="${esc(row.id)}">${moduleIcon(row)} ${esc(en() ? (row.title_en || row.title) : row.title)} · ${t("вернуть", "restore")}</button>`).join("")}</div>
        </section>` : ""}
      </div>`;

    const filter = $("#modules-filter");
    const onlyEnabled = $("#modules-only-enabled");
    const applyFilter = () => {
      const needle = String(filter && filter.value || "").trim().toLowerCase();
      const enabledOnly = !!(onlyEnabled && onlyEnabled.checked);
      let visible = 0;
      $$("[data-module-card]", host).forEach((card) => {
        const row = catalog.find((item) => item.id === card.dataset.moduleCard);
        if (!row) return;
        const haystack = `${row.id} ${row.title} ${row.title_en} ${row.summary} ${row.summary_en}`.toLowerCase();
        const matches = (!needle || haystack.includes(needle)) && (!enabledOnly || row.enabled);
        card.hidden = !matches;
        if (matches) visible += 1;
      });
      const empty = $(".module-list-empty", host);
      if (empty) empty.hidden = visible > 0;
    };
    if (filter) filter.addEventListener("input", applyFilter);
    if (onlyEnabled) onlyEnabled.addEventListener("change", applyFilter);
    bindList(host);
    // Round 33: «Modules as apps» (installed apps, what closing MemuAI does with open modules) — module-apps.js.
    if (MA()) MA().renderSettings(host);
    // Round 39 (r39-integ): «Виджеты модулей» — dock / mini player / Quick Access widgets (module-integration.js)
    if (window.MemuModInt && MemuModInt.renderWidgetSettings) MemuModInt.renderWidgetSettings(host);
    $$("[data-module-restore]", host).forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await apiJson("/api/pg/modules/restore", { method: "POST", body: { id: button.dataset.moduleRestore } });
          note(t("Модуль возвращён", "Module restored"), "ok");
          await mountSettings("list");
        } catch (error) { button.disabled = false; note(error.message, "err"); }
      };
    });
  }

  function moduleCard(row) {
    const place = SLOTS.find((slot) => slot.id === (row.launcher || {}).slot) || SLOTS[0];
    return `
      <article class="module-card ${row.enabled ? "is-on" : ""}" data-module-card="${esc(row.id)}">
        <div class="module-card-head">
          <span class="module-card-icon" data-module-icon-edit="${esc(row.id)}" role="button" tabindex="0" title="${esc(t("Сменить иконку", "Change icon"))}" aria-label="${esc(t("Сменить иконку", "Change icon"))}">${moduleIcon(row)}</span>
          <div class="module-card-title">
            <b>${esc(titleOf(row))}</b>
            <span class="module-card-meta">
              ${row.builtin ? t("встроенный", "built-in") : t("свой", "custom")}
              · v${esc(row.version || "1.0")}${row.author ? " · " + esc(row.author) : ""}
              ${row.edited ? `<em class="module-tag">${t("изменён", "edited")}</em>` : ""}
            </span>
          </div>
          <label class="module-switch" title="${t("Включить модуль", "Enable the module")}">
            <input type="checkbox" data-module-enabled="${esc(row.id)}" ${row.enabled ? "checked" : ""}>
            <i></i>
          </label>
        </div>
        <p class="module-card-sub">${esc(summaryOf(row)) || `<em>${t("Без описания", "No description")}</em>`}</p>
        <div class="module-card-perms">${(row.permissions || []).slice(0, 6)
      .map((name) => `<span class="module-perm">${esc(name)}</span>`).join("")}</div>
        <div class="module-card-controls">
          <label class="module-check"><input type="checkbox" data-module-hidden="${esc(row.id)}" ${row.launcher && row.launcher.hidden ? "checked" : ""}> ${t("Скрыть кнопку", "Hide button")}</label>
          <label class="module-check" title="${t("Можно тянуть окно за края", "The window edges can be dragged")}"><input type="checkbox" data-module-resizable="${esc(row.id)}" ${row.resizable === false ? "" : "checked"}> ${t("Менять размер", "Resizable")}</label>
          <select class="module-slot" data-module-slot="${esc(row.id)}" aria-label="${t("Где кнопка", "Launcher slot")}">
            ${slotList().map((slot) => `<option value="${slot.id}" ${place.id === slot.id ? "selected" : ""}>${t(slot.ru, slot.en)}</option>`).join("")}
          </select>
        </div>
        <div class="module-card-actions">
          <button type="button" class="pbtn small" data-module-open="${esc(row.id)}" ${row.enabled ? "" : "disabled"}>${icon("play")} ${t("Открыть", "Open")}</button>
          <button type="button" class="pbtn small ghost" data-module-code="${esc(row.id)}">${icon("terminal")} ${t("Код", "Code")}</button>
          <span class="mu-grow"></span>
          <button type="button" class="pbtn small ghost icon-only" data-module-more="${esc(row.id)}" aria-label="${t("Ещё", "More")}">${icon("more")}</button>
        </div>
      </article>`;
  }

  function bindList(host) {
    $$("[data-module-enabled]", host).forEach((control) => {
      control.onchange = async () => {
        try {
          await configure(control.dataset.moduleEnabled, { enabled: control.checked });
          if (control.checked) note(t("Модуль включён", "Module enabled"), "ok");
        } catch (error) {
          control.checked = !control.checked;
          note(error.message, "err");
        }
        await mountSettings("list");
      };
    });
    $$("[data-module-hidden]", host).forEach((control) => {
      control.onchange = async () => {
        try { await configure(control.dataset.moduleHidden, { hidden: control.checked }); }
        catch (error) {
          control.checked = !control.checked;
          note(error.message, "err");
        }
        await refresh();
      };
    });
    $$("[data-module-resizable]", host).forEach((control) => {
      control.onchange = () => {
        const row = catalog.find((item) => item.id === control.dataset.moduleResizable);
        if (row) toggleResizable({ ...row, resizable: !control.checked });
      };
    });
    $$("[data-module-slot]", host).forEach((control) => {
      control.onchange = async () => {
        try { await configure(control.dataset.moduleSlot, { slot: control.value }); }
        catch (error) { note(error.message, "err"); }
        await refresh();
      };
    });
    $$("[data-module-open]", host).forEach((button) => {
      button.onclick = () => open(button.dataset.moduleOpen);
    });
    $$("[data-module-code]", host).forEach((button) => {
      button.onclick = () => editCode(button.dataset.moduleCode);
    });
    $$("[data-module-more]", host).forEach((button) => {
      button.onclick = (event) => {
        const row = catalog.find((item) => item.id === button.dataset.moduleMore);
        if (row) moreMenu(event, row);
      };
    });
    $$("[data-modules-tab]", host).forEach((button) => {
      button.onclick = () => mountSettings(button.dataset.modulesTab);
    });
  }

  function moreMenu(event, row) {
    const items = [
      { label: t("Дублировать", "Duplicate"), icon: "copy", run: () => duplicateModule(row) },
      { label: t("Экспорт файлом", "Export as a file"), icon: "download", run: () => exportModule(row) },
      { label: t("Сменить иконку…", "Change icon…"), icon: "image", run: () => window.MemuModuleIcon && window.MemuModuleIcon.picker(row) },
      { label: t("Очистить сохранённое", "Clear saved fields"), icon: "trash", run: () => clearSaved(row) },
    ];
    if (MA()) items.unshift(...MA().menuItems(row, wins.has(row.id) ? row.id : ""));
    if ((row.permissions || []).includes("net-import")) {
      items.push({ label: t("Источники импорта", "Import sources"), icon: "globe", run: () => netImportSettings(row) });
    }
    if (row.builtin) {
      items.push({ label: t("Сбросить к заводской", "Reset to shipped"), icon: "undo", run: () => resetModule(row) });
    }
    items.push({ label: t("Удалить модуль", "Delete module"), icon: "trash", danger: true,
      run: () => deleteModule(row) });
    popMenu(event, items);
  }

  async function configure(moduleId, body) {
    return await apiJson("/api/pg/modules/configure", { method: "POST",
      body: { id: moduleId, ...body } });
  }

  /* Вопрос с полем ввода в окне MemuAI. window.prompt в окне приложения
     блокирует всё и выглядит чужеродно, а в некоторых оболочках не работает. */
  function askText(title, text, value, action) {
    return new Promise((resolve) => {
      if (typeof panel !== "function") return resolve(window.prompt(title, value || ""));
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (typeof closePanel === "function") closePanel(result === null ? "cancel" : "confirm");
        resolve(result);
      };
      panel(`<h2>${esc(title)}</h2>${text ? `<p class="sub">${esc(text)}</p>` : ""}
        <input class="pinput" id="ask-text-input" maxlength="41" spellcheck="false" value="${esc(value || "")}">
        <div class="row-end"><button class="pbtn ghost" id="ask-text-no">${t("Отмена", "Cancel")}</button>
          <button class="pbtn" id="ask-text-yes">${esc(action || t("Готово", "Done"))}</button></div>`, "", {
        onClose: () => finish(null),
      });
      const input = document.getElementById("ask-text-input");
      document.getElementById("ask-text-no").onclick = () => finish(null);
      document.getElementById("ask-text-yes").onclick = () => finish(input.value.trim());
      input.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); finish(input.value.trim()); } };
      setTimeout(() => { input.focus(); input.select(); }, 30);
    });
  }

  function freeCopyId(base) {
    const taken = new Set(catalog.map((item) => item.id));
    const stem = String(base || "module").replace(/-copy(-\d+)?$/, "").slice(0, 32);
    let candidate = `${stem}-copy`;
    for (let index = 2; taken.has(candidate) && index < 100; index += 1) candidate = `${stem}-copy-${index}`;
    return candidate.slice(0, 41);
  }

  async function duplicateModule(row) {
    const newId = await askText(t("Копия модуля", "Duplicate the module"),
      t("Идентификатор копии: латиница в нижнем регистре, цифры и дефис.", "The copy's id: lowercase latin letters, digits and hyphens."),
      freeCopyId(row.id), t("Создать копию", "Create copy"));
    if (!newId) return;
    try {
      await apiJson("/api/pg/modules/duplicate", { method: "POST",
        body: { id: row.id, new_id: newId, title: titleOf(row) + " 2" } });
      note(t("Копия создана", "Copy created"), "ok");
      await mountSettings("list");
    } catch (error) { note(error.message, "err"); }
  }

  async function exportModule(row) {
    try {
      const response = await fetch("/api/pg/modules/" + encodeURIComponent(row.id) + "/export");
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      const text = await response.text();
      const result = await downloadTextFile(`${row.id}.memu-module.json`, text, "application/json");
      if (!result || !result.path) note(t("Файл не сохранён", "The file was not saved"), "warn");
    } catch (error) { note(error.message, "err"); }
  }

  async function clearSaved(row) {
    try {
      await apiJson("/api/pg/modules/persist", { method: "POST", body: { id: row.id, clear: true } });
      note(t("Сохранённые поля очищены", "Saved fields cleared"), "ok");
    } catch (error) { note(error.message, "err"); }
  }

  async function resetModule(row) {
    const confirmed = typeof askConfirm === "function"
      ? await askConfirm(t("Сбросить код модуля?", "Reset the module code?"),
        t("Ваша версия кода будет удалена, вернётся заводская. Сохранённые поля останутся.",
          "Your code version is removed and the shipped one returns. Saved fields are kept."),
        t("Сбросить", "Reset"))
      : true;
    if (!confirmed) return;
    try {
      await apiJson("/api/pg/modules/reset", { method: "POST", body: { id: row.id } });
      note(t("Код возвращён к заводскому", "Code reset to shipped"), "ok");
      await mountSettings("list");
    } catch (error) { note(error.message, "err"); }
  }

  async function deleteModule(row) {
    const confirmed = typeof askConfirm === "function"
      ? await askConfirm(t("Удалить модуль?", "Delete the module?"),
        `${titleOf(row)} · ${row.id}\n${row.builtin
          ? t("Ваши правки кода, настройки и сохранённые поля удалятся. Сам модуль можно вернуть внизу списка, в разделе «Удалённые встроенные».",
            "Your code edits, settings and saved fields are removed. The module itself can be brought back at the bottom of the list, under “Removed built-ins”.")
          : t("Код модуля и сохранённые поля будут удалены безвозвратно.",
            "The module code and its saved fields are removed permanently.")}`,
        t("Удалить", "Delete"))
      : true;
    if (!confirmed) return;
    try {
      await apiJson("/api/pg/modules/delete", { method: "POST", body: { id: row.id } });
      note(t("Модуль удалён", "Module deleted"), "ok");
      await mountSettings("list");
    } catch (error) { note(error.message, "err"); }
  }

  /* --------------------------- мастер создания ----------------------------- */

  function renderCreate(host) {
    const list = templateList();
    if (!draft) {
      const first = list[0] || {};
      draft = {
        id: "", title: "", template: first.id || "blank",
        width: 760, height: 560, fixed: false, resizable: true, size_mode: "user",
        persist: (first.persist || []).join(", "),
        permissions: (first.permissions || ["chat", "storage"]).slice(),
        slot: "sidebar", hidden: false, instructions: "",
      };
    }
    host.innerHTML = `
      <div class="modules-pane" data-view="create">
        ${head(t("Новый модуль", "New module"),
          t("Выберите шаблон — он даст рабочий код, а не заглушку. Всё остальное можно изменить позже.",
            "Pick a template: it gives working code, not a stub. Everything can still be changed later."),
          backButton())}
        <div class="module-wizard">
          <section class="module-wizard-card">
            <h4>1. ${t("Название и идентификатор", "Name and id")}</h4>
            <div class="module-wizard-grid">
              <label class="field-label">${t("Название", "Title")}
                <input class="pinput" id="mw-title" maxlength="80" value="${esc(draft.title)}" placeholder="${t("Мои заметки", "My notes")}"></label>
              <label class="field-label">${t("Идентификатор", "Id")}
                <input class="pinput" id="mw-id" maxlength="41" value="${esc(draft.id)}" placeholder="my-notes" spellcheck="false"></label>
            </div>
            <p class="module-hint">${t("Латиница в нижнем регистре, цифры и дефис. Идентификатор нельзя изменить после создания.",
              "Lowercase latin letters, digits and hyphens. The id cannot change after creation.")}</p>
          </section>
          <section class="module-wizard-card">
            <h4>2. ${t("Шаблон", "Template")}</h4>
            <div class="module-template-grid">
              ${templateList().map((item) => `
                <button type="button" class="module-template ${draft.template === item.id ? "active" : ""}" data-template="${esc(item.id)}">
                  <span class="module-template-icon">${moduleIcon(item.icon, "text")}</span>
                  <b>${esc(localizedTitle(item))}</b>
                  <span>${esc(localizedSummary(item))}</span>
                </button>`).join("") || `<div class="mu-empty">${t("Шаблоны недоступны", "Templates are unavailable")}</div>`}
            </div>
          </section>
          <section class="module-wizard-card">
            <h4>3. ${t("Окно и кнопка", "Window and button")}</h4>
            <div class="module-wizard-grid">
              <label class="field-label">${t("Ширина", "Width")}
                <input class="pinput" id="mw-width" type="number" min="240" max="2400" value="${draft.width}"></label>
              <label class="field-label">${t("Высота", "Height")}
                <input class="pinput" id="mw-height" type="number" min="200" max="1600" value="${draft.height}"></label>
              <label class="field-label">${t("Размер", "Size")}
                <select class="pinput" id="mw-size-mode">
                  <option value="user" ${draft.size_mode === "user" ? "selected" : ""}>${t("Сначала размер человека", "Prefer the person's size")}</option>
                  <option value="authored" ${draft.size_mode === "authored" ? "selected" : ""}>${t("Всегда размер автора", "Always the authored size")}</option>
                </select></label>
              <label class="field-label">${t("Место кнопки", "Launcher slot")}
                <select class="pinput" id="mw-slot">
                  ${slotList().map((slot) => `<option value="${slot.id}" ${draft.slot === slot.id ? "selected" : ""}>${t(slot.ru, slot.en)}</option>`).join("")}
                </select></label>
            </div>
            <div class="module-check-row">
              <label class="module-check"><input type="checkbox" id="mw-fixed" ${draft.fixed ? "checked" : ""}> ${t("Фиксированный размер", "Fixed size")}</label>
              <label class="module-check"><input type="checkbox" id="mw-resizable" ${draft.resizable ? "checked" : ""}> ${t("Можно менять размер", "Resizable")}</label>
              <label class="module-check"><input type="checkbox" id="mw-hidden" ${draft.hidden ? "checked" : ""}> ${t("Скрыть кнопку", "Hide button")}</label>
            </div>
          </section>
          <section class="module-wizard-card">
            <h4>4. ${t("Возможности и память", "Capabilities and memory")}</h4>
            <div class="module-perm-grid">
              ${permissionList().map((name) => `
                <label class="module-check"><input type="checkbox" data-perm="${esc(name)}" ${draft.permissions.includes(name) ? "checked" : ""}> ${esc(name)}</label>`).join("")}
            </div>
            <p class="module-hint">${t("Права можно поменять в любой момент. Без права модуль не сможет вызвать соответствующий метод моста.",
              "Permissions can change at any time. Without one the module cannot call the matching bridge method.")}</p>
            <label class="field-label">${t("Поля, которые сохраняются", "Fields that survive closing")}
              <input class="pinput" id="mw-persist" value="${esc(draft.persist)}" placeholder="draft, source, model"></label>
            <label class="field-label">${t("Инструкции для модели по умолчанию", "Default model instructions")}
              <textarea class="ptexta" id="mw-instructions" rows="3" placeholder="${t("Необязательно", "Optional")}">${esc(draft.instructions)}</textarea></label>
          </section>
          <div class="module-wizard-foot">
            <span class="module-hint" id="mw-status"></span>
            <span class="mu-grow"></span>
            <button type="button" class="pbtn" id="mw-create">${icon("plus")} ${t("Создать и открыть код", "Create and open the code")}</button>
          </div>
        </div>
      </div>`;
    bindCreate(host);
  }

  function bindCreate(host) {
    const readForm = () => {
      if (!draft) return;
      draft.title = $("#mw-title", host).value.trim();
      draft.id = $("#mw-id", host).value.trim().toLowerCase();
      draft.width = Number($("#mw-width", host).value) || 760;
      draft.height = Number($("#mw-height", host).value) || 560;
      draft.size_mode = $("#mw-size-mode", host).value;
      draft.slot = $("#mw-slot", host).value;
      draft.fixed = $("#mw-fixed", host).checked;
      draft.resizable = $("#mw-resizable", host).checked;
      draft.hidden = $("#mw-hidden", host).checked;
      draft.persist = $("#mw-persist", host).value;
      draft.instructions = $("#mw-instructions", host).value;
      draft.permissions = $$("[data-perm]", host).filter((box) => box.checked).map((box) => box.dataset.perm);
    };
    $$("[data-template]", host).forEach((button) => {
      button.onclick = () => {
        readForm();
        draft.template = button.dataset.template;
        const chosen = templateList().find((item) => item.id === draft.template);
        if (chosen && !draft.title) draft.title = localizedTitle(chosen);
        // Шаблону нужны свои права и поля: у «Мини-чата» без models не
        // работает выбор модели, у «Инструмента» без storage — сохранение.
        if (chosen && Array.isArray(chosen.permissions)) draft.permissions = chosen.permissions.slice();
        if (chosen && Array.isArray(chosen.persist)) draft.persist = chosen.persist.join(", ");
        renderCreate(host);
      };
    });
    $$("[data-modules-tab]", host).forEach((button) => {
      button.onclick = () => { readForm(); draft = null; mountSettings(button.dataset.modulesTab); };
    });
    $("#mw-create", host).onclick = async () => {
      readForm();
      const status = $("#mw-status", host);
      if (!draft.id) { status.textContent = t("Введите идентификатор", "Enter an id"); return; }
      if (!/^[a-z][a-z0-9-]{0,40}$/.test(draft.id)) {
        status.textContent = t("Идентификатор: латиница в нижнем регистре, цифры и дефис, начинается с буквы",
          "Id: lowercase latin, digits and hyphens, starting with a letter");
        return;
      }
      if (!draft.title) { status.textContent = t("Введите название", "Enter a title"); return; }
      status.innerHTML = `<span class="mu-spinner"></span> ${t("Создаю…", "Creating…")}`;
      const createdId = draft.id;
      try {
        await apiJson("/api/pg/modules", { method: "POST", body: {
          id: draft.id, title: draft.title, template: draft.template,
          // Сервер читает размеры окна только из window{}: верхние ключи
          // игнорировались, и модуль создавался 760×540.
          window: { default_width: draft.width, default_height: draft.height,
            size_mode: draft.size_mode, fixed: draft.fixed, resizable: draft.resizable },
          launcher: { hidden: draft.hidden, slot: draft.slot },
          persist: draft.persist.split(",").map((item) => item.trim()).filter(Boolean),
          permissions: draft.permissions,
          bridge: { instructions: draft.instructions },
        } });
        draft = null;
        note(t("Модуль создан", "Module created"), "ok");
        await refresh();
        await editCode(createdId);
      } catch (error) { status.textContent = error.message; }
    };
  }

  /* ------------------------------- мастерская ------------------------------ */

  async function editCode(moduleId) {
    codeState = { id: moduleId, source: null, previewTimer: null, problems: [] };
    currentPane = "code";
    if (typeof showSettings === "function" && !$("#pane-modules")) {
      await showSettings(false);
    }
    currentSettingsTab = "modules";
    $$(".settings-tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.setTab === "modules"));
    $$(".settings-tab-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-modules"));
    await mountSettings("code");
  }

  async function renderCode(host) {
    if (!codeState) return renderList(host);
    let source;
    try {
      source = await apiJson("/api/pg/modules/" + encodeURIComponent(codeState.id) + "/source");
    } catch (error) {
      host.innerHTML = `<div class="modules-pane">${head(t("Модуль", "Module"), "", backButton())}
        <div class="mu-note warn">${esc(error.message)}</div></div>`;
      return;
    }
    codeState.source = source;
    host.innerHTML = `
      <div class="modules-pane modules-workbench" data-view="code">
        ${head(`${moduleIcon(source)} ${esc(titleOf(source))}`,
          `${esc(summaryOf(source)) || t("Без описания", "No description")} · <code>${esc(source.id)}</code>${source.builtin ? " · " + t("встроенный модуль", "built-in module") : ""}${source.edited ? " · " + t("есть ваша версия кода", "your code version exists") : ""}`,
          `${backButton()}${`<button type="button" class="pbtn ghost" id="mw-export">${icon("download")} ${t("Экспорт", "Export")}</button>`}
           <button type="button" class="pbtn" id="mw-save" title="Ctrl+S">${icon("check")} ${t("Сохранить", "Save")}</button>`)}
        <div class="workbench-body">
          <div class="workbench-editors">
            <div class="workbench-tabs" role="tablist">
              <button type="button" class="workbench-tab active" data-editor="html">HTML</button>
              <button type="button" class="workbench-tab" data-editor="css">CSS</button>
              <button type="button" class="workbench-tab" data-editor="js">JS</button>
              <button type="button" class="workbench-tab" data-editor="i18n" title="${esc(t("i18n.json — тексты модуля на каждом языке", "i18n.json — the module's strings in every language"))}">${t("Переводы", "Translations")}</button>
              <span class="workbench-dirty" id="mw-dirty" hidden>${t("есть несохранённые правки", "unsaved changes")}</span>
              <span class="mu-grow"></span>
              <button type="button" class="pbtn small ghost" id="mw-i18n-fill" hidden title="${esc(t("Найти ключи data-i18n и Memu.t в коде и добавить их во все языки", "Find data-i18n and Memu.t keys in the code and add them to every locale"))}">${icon("plus")} ${t("Недостающие ключи", "Missing keys")}</button>
              <button type="button" class="pbtn small ghost" id="mw-check">${icon("sparkle")} ${t("Проверить", "Check")}</button>
            </div>
            ${[["html", "HTML", "html"], ["css", "CSS", "css"], ["js", "JavaScript", "js"], ["i18n", "i18n.json", "json"]].map(([name, label, lang]) => `
              <div class="workbench-code" data-code-editor="${name}" ${name === "html" ? "" : "hidden"}>
                <pre class="workbench-highlight" aria-hidden="true"><code></code></pre>
                <textarea class="workbench-editor" id="mw-${name}" data-code-language="${lang}" spellcheck="false"
                  autocapitalize="off" autocomplete="off" autocorrect="off" wrap="off" aria-label="${label}"
                  ${name === "html" ? "" : "hidden"} ${name === "i18n" ? `placeholder='{ "ru": { "title": "Мой модуль" }, "en": { "title": "My module" } }'` : ""}></textarea>
              </div>`).join("")}
            <div class="workbench-problems" id="mw-problems" hidden></div>
          </div>
          <div class="workbench-preview">
            <div class="workbench-preview-head">
              <span>${t("Предпросмотр", "Preview")}</span>
              <span class="mu-grow"></span>
              <select class="pinput compact" id="mw-preview-lang" title="${esc(t("Язык предпросмотра", "Preview language"))}" aria-label="${esc(t("Язык предпросмотра", "Preview language"))}">
                <option value="">${t("Как в MemuAI", "As in MemuAI")}</option><option value="ru">Русский</option><option value="en">English</option>
              </select>
              <button type="button" class="pbtn small ghost" id="mw-preview-refresh">${icon("retry")} ${t("Обновить", "Refresh")}</button>
              <button type="button" class="pbtn small ghost" id="mw-preview-open">${icon("play")} ${t("Открыть окно", "Open window")}</button>
            </div>
            <iframe id="mw-preview" title="${t("Предпросмотр модуля", "Module preview")}" sandbox="allow-scripts"></iframe>
          </div>
        </div>
        <div class="workbench-meta">
          <section>
            <h4>${t("Описание", "Description")}</h4>
            <label class="field-label">${t("Название", "Title")}<input class="pinput" id="mw-title"></label>
            <label class="field-label">${t("Краткое описание", "Summary")}<input class="pinput" id="mw-summary"></label>
            <label class="field-label">${t("Инструкции для модели", "Model instructions")}<textarea class="ptexta" id="mw-instructions" rows="3"></textarea></label>
            <label class="field-label" title="${esc(t("Если для языка MemuAI перевода нет, модуль покажет этот язык", "When there is no translation for the MemuAI language, the module falls back to this one"))}">${t("Язык по умолчанию", "Fallback language")}<input class="pinput" id="mw-locale" maxlength="10" placeholder="ru" spellcheck="false"></label>
          </section>
          <section>
            <h4>${t("Окно", "Window")}</h4>
            <label class="field-label">${t("Ширина", "Width")}<input class="pinput" id="mw-width" type="number" min="240" max="2400"></label>
            <label class="field-label">${t("Высота", "Height")}<input class="pinput" id="mw-height" type="number" min="200" max="1600"></label>
            <label class="field-label">${t("Размер", "Size")}
              <select class="pinput" id="mw-size-mode">
                <option value="user">${t("Сначала размер человека", "Prefer the person's size")}</option>
                <option value="authored">${t("Всегда размер автора", "Always the authored size")}</option>
              </select></label>
            <div class="module-check-row">
              <label class="module-check"><input type="checkbox" id="mw-fixed"> ${t("Фиксированный", "Fixed")}</label>
              <label class="module-check"><input type="checkbox" id="mw-resizable"> ${t("Изменяемый", "Resizable")}</label>
            </div>
          </section>
          <section>
            <h4>${t("Права и память", "Permissions and memory")}</h4>
            <div class="module-perm-grid compact">
              ${permissionList().map((name) => `
                <label class="module-check"><input type="checkbox" data-perm="${esc(name)}"> ${esc(name)}</label>`).join("")}
            </div>
            <label class="field-label">${t("Сохраняемые поля", "Saved fields")}<input class="pinput" id="mw-persist" placeholder="draft, model"></label>
          </section>
        </div>
      </div>`;
    fillCode(source);
    if (codeState.draft && codeState.draft.id === codeState.id) {
      const draft = codeState.draft;
      $("#mw-html").value = draft.html; $("#mw-css").value = draft.css; $("#mw-js").value = draft.js;
      $("#mw-i18n").value = draft.i18n; $("#mw-locale").value = draft.default_locale;
      $("#mw-title").value = en() ? draft.title_en : draft.title;
      $("#mw-summary").value = en() ? draft.summary_en : draft.summary;
      $("#mw-instructions").value = draft.bridge.instructions || "";
      $("#mw-width").value = draft.window.default_width; $("#mw-height").value = draft.window.default_height;
      $("#mw-size-mode").value = draft.window.size_mode; $("#mw-fixed").checked = draft.window.fixed;
      $("#mw-resizable").checked = draft.window.resizable; $("#mw-persist").value = draft.persist.join(", ");
      $$("[data-perm]").forEach((box) => { box.checked = draft.permissions.includes(box.dataset.perm); });
      codeState.draft = null;
      markDirty(true);
    }
    bindCode(host, source);
    updatePreview();
    if (source.builtin && !source.edited) {
      setProblems([{ level: "info", field: "",
        ru: "Это встроенный модуль. Ваши изменения сохранятся отдельно и не тронут файлы установки.",
        en: "This is a built-in module. Your changes are stored separately and never touch the installed files." }]);
    }
  }

  function fillCode(source) {
    $("#mw-html").value = source.html || "";
    $("#mw-css").value = source.css || "";
    $("#mw-js").value = source.js || "";
    $("#mw-i18n").value = source.i18n && Object.keys(source.i18n).length ? JSON.stringify(source.i18n, null, 2) : "";
    $("#mw-locale").value = source.default_locale || "ru";
    // Поле названия — на языке интерфейса: в английском правится title_en.
    $("#mw-title").value = en() ? (source.title_en || source.title || "") : (source.title || "");
    $("#mw-summary").value = en() ? (source.summary_en || source.summary || "") : (source.summary || "");
    $("#mw-instructions").value = (source.bridge || {}).instructions || "";
    const win = source.window || {};
    $("#mw-width").value = win.default_width || 760;
    $("#mw-height").value = win.default_height || 540;
    $("#mw-size-mode").value = win.size_mode === "authored" ? "authored" : "user";
    $("#mw-fixed").checked = !!win.fixed;
    $("#mw-resizable").checked = win.resizable !== false;
    $("#mw-persist").value = (source.persist || []).join(", ");
    $$("[data-perm]").forEach((box) => {
      box.checked = (source.permissions || []).includes(box.dataset.perm);
    });
  }

  function bindCode(host, source) {
    $$("[data-editor]", host).forEach((tab) => {
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", tab.classList.contains("active") ? "true" : "false");
      tab.setAttribute("aria-controls", "mw-" + tab.dataset.editor);
      tab.onclick = () => {
        $$("[data-editor]", host).forEach((item) => {
          item.classList.toggle("active", item === tab);
          item.setAttribute("aria-selected", item === tab ? "true" : "false");
        });
        ["html", "css", "js", "i18n"].forEach((name) => {
          $("#mw-" + name).hidden = name !== tab.dataset.editor;
          $("[data-code-editor='" + name + "']", host).hidden = name !== tab.dataset.editor;
        });
        $("#mw-i18n-fill", host).hidden = tab.dataset.editor !== "i18n";
        const area = $("#mw-" + tab.dataset.editor, host);
        paintCodeEditor(area);
        area.focus({ preventScroll: true });
      };
    });
    $$("[data-modules-tab]", host).forEach((button) => {
      button.onclick = async () => {
        if (!(await leaveCode())) return;
        codeState = null;
        mountSettings(button.dataset.modulesTab);
      };
    });
    $("#mw-save", host).onclick = () => saveCode();
    $("#mw-export", host).onclick = () => exportModule(source);
    $("#mw-check", host).onclick = () => checkCode();
    $("#mw-i18n-fill", host).onclick = () => fillMissingKeys();
    $("#mw-preview-lang", host).onchange = () => updatePreview(true);
    $("#mw-preview-refresh", host).onclick = () => updatePreview(true);
    $("#mw-preview-open", host).onclick = () => openFromWorkbench(source.id);
    const pane = $(".modules-workbench", host);
    const schedulePreview = () => {
      if (!codeState) return;
      markDirty(true);
      clearTimeout(codeState.previewTimer);
      codeState.previewTimer = setTimeout(() => updatePreview(), 700);
    };
    pane.addEventListener("input", schedulePreview);
    pane.addEventListener("change", schedulePreview);
    pane.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyS") {
        event.preventDefault();
        saveCode();
      }
    });
    ["#mw-html", "#mw-css", "#mw-js", "#mw-i18n"].forEach((selector) => {
      const area = $(selector, host);
      if (!area) return;
      paintCodeEditor(area);
      area.addEventListener("scroll", () => syncCodeScroll(area));
      area.addEventListener("input", () => {
        if (area._highlightFrame) cancelAnimationFrame(area._highlightFrame);
        area._highlightFrame = requestAnimationFrame(() => {
          area._highlightFrame = 0;
          if (area.isConnected) paintCodeEditor(area);
        });
      });
      area.addEventListener("keydown", (event) => {
        if (event.key === "Tab" && !event.ctrlKey && !event.altKey && !event.metaKey && !event.isComposing) {
          event.preventDefault();
          indentCodeEditor(area, event.shiftKey);
        }
      });
    });
    // Native textarea scrollbars can change size after an edit or a pane resize.
    // The highlight viewport follows the input's client box, never its scrollbar.
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(() => {
        if (!pane.isConnected) {
          observer.disconnect();
          if (codeEditorObserver === observer) codeEditorObserver = null;
          return;
        }
        $$(".workbench-editor", pane).forEach(syncCodeScroll);
      });
      codeEditorObserver = observer;
      $$(".workbench-code", pane).forEach((editor) => observer.observe(editor));
    }
  }

  function syncCodeScroll(area) {
    const preview = area && area.parentElement.querySelector(".workbench-highlight");
    if (!preview || area.hidden) return;
    preview.style.width = area.clientWidth + "px";
    preview.style.height = area.clientHeight + "px";
    preview.scrollTop = area.scrollTop;
    preview.scrollLeft = area.scrollLeft;
  }

  function paintCodeEditor(area) {
    if (!area) return;
    const layer = area.parentElement.querySelector(".workbench-highlight code");
    if (!layer) return;
    let rendered = esc(area.value);
    if (window.MemuSyntax) {
      try { rendered = MemuSyntax.highlight(area.value, area.dataset.codeLanguage).join("\n"); }
      catch (error) { /* Keep plain escaped text when a tokenizer cannot parse a draft. */ }
    }
    // An empty last line needs a glyph to keep pre's scroll range equal to textarea's.
    layer.innerHTML = rendered + "\n ";
    area.parentElement.classList.add("highlighted");
    syncCodeScroll(area);
  }

  function replaceCodeRange(area, start, end, text) {
    const top = area.scrollTop, left = area.scrollLeft;
    area.focus({ preventScroll: true });
    area.setSelectionRange(start, end);
    // WebView/Chromium records insertText in textarea's native undo stack.
    // setRangeText alone would make Tab indentation impossible to undo.
    let inserted = false;
    try { inserted = document.execCommand("insertText", false, text); }
    catch (error) { /* Older embedded browsers use the plain input fallback. */ }
    if (!inserted) {
      area.setRangeText(text, start, end, "end");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    }
    area.scrollTop = top;
    area.scrollLeft = left;
    paintCodeEditor(area);
  }

  function indentCodeEditor(area, outdent) {
    const value = area.value;
    const start = area.selectionStart, end = area.selectionEnd;
    if (!outdent && start === end) { replaceCodeRange(area, start, end, "  "); return; }
    const first = start === 0 ? 0 : value.lastIndexOf("\n", start - 1) + 1;
    const lastPosition = end > start && value[end - 1] === "\n" ? end - 1 : end;
    const next = value.indexOf("\n", lastPosition);
    const last = next < 0 ? value.length : next;
    const lines = value.slice(first, last).split("\n");
    const removals = lines.map((line) => outdent ? ((line.match(/^(?: {1,2}|\t)/) || [""])[0].length) : 0);
    const replacement = lines.map((line, index) => outdent ? line.slice(removals[index]) : "  " + line).join("\n");
    if (replacement === value.slice(first, last)) return;
    const adjust = (position) => {
      let offset = first, shift = 0;
      for (let index = 0; index < lines.length; index += 1) {
        if (position <= offset + lines[index].length) {
          return offset + shift + (outdent ? Math.max(0, position - offset - removals[index]) : position - offset + 2);
        }
        shift += outdent ? -removals[index] : 2;
        offset += lines[index].length + 1;
      }
      return position + shift;
    };
    const selection = [adjust(start), adjust(end), area.selectionDirection];
    replaceCodeRange(area, first, last, replacement);
    area.setSelectionRange(...selection);
    syncCodeScroll(area);
  }

  /* Keys the code asks for — the same rules as i18n_keys_used() on the server. */
  function usedTranslationKeys(html, js) {
    const keys = [];
    const push = (key) => { if (key && !keys.includes(key)) keys.push(key); };
    for (const match of String(html || "").matchAll(/data-i18n(?:-[a-z-]+)?\s*=\s*["']([^"']+)["']/g)) push(match[1]);
    for (const match of String(js || "").matchAll(/\bMemu\.(?:t|i18n\.t)\(\s*(["'`])([^"'`$]+)\1\s*[,)]/g)) push(match[2]);
    for (const match of String(js || "").matchAll(/data-i18n(?:-[a-z-]+)?\s*=\s*\\?["']([A-Za-z0-9_.-]+)\\?["']/g)) push(match[1]);
    return keys;
  }

  function fillMissingKeys() {
    const area = $("#mw-i18n");
    let tables = {};
    try { tables = area.value.trim() ? JSON.parse(area.value) : {}; }
    catch (error) { return note(t("Сначала исправьте JSON переводов: ", "Fix the translations JSON first: ") + error.message, "err"); }
    if (!tables || typeof tables !== "object" || Array.isArray(tables)) tables = {};
    if (!Object.keys(tables).length) { tables.ru = {}; tables.en = {}; }
    const keys = usedTranslationKeys($("#mw-html").value, $("#mw-js").value);
    let added = 0;
    Object.keys(tables).forEach((locale) => {
      const table = tables[locale] && typeof tables[locale] === "object" ? tables[locale] : (tables[locale] = {});
      keys.forEach((key) => { if (!(key in table)) { table[key] = ""; added += 1; } });
    });
    replaceCodeRange(area, 0, area.value.length, JSON.stringify(tables, null, 2));
    note(added ? t(`Добавлено пустых строк: ${added}. Заполните их переводом.`, `Added ${added} empty strings. Fill in the translations.`)
      : t("Все ключи уже есть в каждом языке", "Every key is already present in every locale"), added ? "ok" : "ok");
  }

  function markDirty(value) {
    if (!codeState) return;
    codeState.dirty = !!value;
    const badge = document.getElementById("mw-dirty");
    if (badge) badge.hidden = !codeState.dirty;
  }

  /* Уход из мастерской с несохранёнными правками — только с подтверждением:
     раньше кнопка «К списку» молча выбрасывала весь набранный код. */
  async function leaveCode() {
    if (!codeState || !codeState.dirty) return true;
    if (typeof askConfirm !== "function") return true;
    return await askConfirm(t("Выйти без сохранения?", "Leave without saving?"),
      t("Правки кода и настроек этого модуля не сохранены и пропадут.",
        "Your changes to this module's code and settings are not saved and will be lost."),
      t("Выйти", "Leave"));
  }

  async function openFromWorkbench(moduleId) {
    const row = catalog.find((item) => item.id === moduleId);
    if (row && !row.enabled) {
      try {
        await configure(moduleId, { enabled: true });
        await refresh();
        note(t("Модуль включён, чтобы открыть окно", "The module was enabled to open its window"), "ok");
      } catch (error) { return note(error.message, "err"); }
    }
    if (codeState && codeState.dirty) {
      note(t("Окно показывает сохранённую версию — сохраните правки (Ctrl+S)",
        "The window shows the saved version — save your changes (Ctrl+S)"), "warn");
    }
    await open(moduleId);
  }

  /* Название и описание на обоих языках. Правка идёт в язык интерфейса;
     второй язык, если он был просто копией первого, следует за ним. */
  function localizedTitles(title, summary) {
    const source = (codeState && codeState.source) || {};
    if (en()) {
      return { title: source.title || title, title_en: title,
        summary: source.summary || summary, summary_en: summary };
    }
    const followTitle = !source.title_en || source.title_en === source.title;
    const followSummary = !source.summary_en || source.summary_en === source.summary;
    return { title, title_en: followTitle ? title : source.title_en,
      summary, summary_en: followSummary ? summary : source.summary_en };
  }

  function currentCode() {
    return {
      id: codeState.id,
      html: $("#mw-html").value,
      css: $("#mw-css").value,
      js: $("#mw-js").value,
      i18n: $("#mw-i18n").value,
      default_locale: $("#mw-locale").value.trim() || "ru",
      ...localizedTitles($("#mw-title").value, $("#mw-summary").value),
      persist: $("#mw-persist").value.split(",").map((item) => item.trim()).filter(Boolean),
      permissions: $$("[data-perm]").filter((box) => box.checked).map((box) => box.dataset.perm),
      window: {
        default_width: Number($("#mw-width").value) || 760,
        default_height: Number($("#mw-height").value) || 540,
        fixed: $("#mw-fixed").checked,
        resizable: $("#mw-resizable").checked,
        size_mode: $("#mw-size-mode").value,
      },
      bridge: { ...((codeState.source || {}).bridge || {}), instructions: $("#mw-instructions").value },
    };
  }

  async function saveCode() {
    if (!codeState || codeState.saving) return;
    codeState.saving = true;
    const button = document.getElementById("mw-save");
    if (button) button.disabled = true;
    try {
      await saveCodeNow();
    } finally {
      if (codeState) codeState.saving = false;
      if (button) button.disabled = false;
    }
  }

  async function saveCodeNow() {
    const body = currentCode();
    try {
      const checked = await apiJson("/api/pg/modules/validate", { method: "POST", body });
      setProblems(checked.problems || []);
      await apiJson("/api/pg/modules/code", { method: "POST", body });
      note(t("Код сохранён", "Code saved"), "ok");
      codeState.source = { ...(codeState.source || {}), ...body, edited: true };
      markDirty(false);
      await refresh();
      updatePreview(true);
      const opened = wins.get(body.id);
      if (opened && opened.frame) opened.frame.src = documentUrl(opened, true);
    } catch (error) {
      setProblems([{ level: "warn", field: "", ru: error.message, en: error.message }]);
      note(error.message, "err");
    }
  }

  async function checkCode() {
    if (!codeState) return;
    try {
      const checked = await apiJson("/api/pg/modules/validate", { method: "POST", body: currentCode() });
      setProblems(checked.problems || []);
      if (!(checked.problems || []).length) note(t("Замечаний нет", "No problems found"), "ok");
    } catch (error) { note(error.message, "err"); }
  }

  function setProblems(rows) {
    const node = $("#mw-problems");
    if (!node || !codeState) return;
    codeState.problems = rows || [];
    if (!codeState.problems.length) {
      node.hidden = true;
      node.innerHTML = "";
      return;
    }
    node.hidden = false;
    node.innerHTML = codeState.problems.map((item) => `
      <div class="workbench-problem ${esc(item.level || "info")}">
        <b>${esc(item.field || t("Общее", "General"))}</b>
        <span>${esc(en() ? (item.en || item.ru) : (item.ru || item.en))}</span>
      </div>`).join("");
  }

  function updatePreview(force) {
    const frame = $("#mw-preview");
    if (!frame || !codeState || !codeState.source) return;
    const body = currentCode();
    codeState.previewPermissions = body.permissions.slice();
    const payload = {
      id: codeState.id,
      title: body.title || codeState.source.title || codeState.id,
      title_en: body.title_en,
      summary: body.summary,
      summary_en: body.summary_en,
      icon: codeState.source.icon,
      accent: codeState.source.accent,
      window: body.window,
      persist: body.persist,
      permissions: body.permissions,
      bridge: body.bridge,
      builtin: codeState.source.builtin,
      html: body.html,
      css: body.css,
      js: body.js,
      i18n: body.i18n,
      default_locale: body.default_locale,
      saved: {},
      env: { ...hostEnv(), lang: ($("#mw-preview-lang") && $("#mw-preview-lang").value) || hostEnv().lang },
    };
    // Медленный старый ответ не должен затереть предпросмотр нового кода.
    const seq = (codeState.previewSeq = (codeState.previewSeq || 0) + 1);
    const request = fetch("/api/pg/modules/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((response) => (response.ok ? response.text() : ""))
      .catch(() => "");
    request.then((html) => {
      if (!codeState || seq !== codeState.previewSeq) return;
      if (!html) {
        if (force) setProblems([{ level: "warn", field: "",
          ru: "Предпросмотр недоступен", en: "The preview is unavailable" }]);
        return;
      }
      frame.srcdoc = html;
    });
  }

  /* --------------------------- импорт и документация ------------------------ */

  function renderImport(host) {
    host.innerHTML = `
      <div class="modules-pane" data-view="import">
        ${head(t("Импорт модуля", "Import a module"),
          t("Пакет модуля — один JSON-файл. Он переносит описание и весь код между компьютерами и профилями.",
            "A module package is a single JSON file. It carries the description and all the code between computers and profiles."),
          backButton())}
        <div class="module-wizard-card">
          <h4>${t("Файл пакета", "Package file")}</h4>
          <p class="module-hint">${t("Подойдёт файл, сохранённый кнопкой «Экспорт». Внутри — описание и код модуля.",
            "A file saved with the Export button works. Inside are the description and the module code.")}</p>
          <div class="module-wizard-grid">
            <label class="field-label">${t("Новый идентификатор", "New id")}
              <input class="pinput" id="mi-id" maxlength="41" placeholder="${t("Оставьте пустым, чтобы взять из файла", "Leave empty to use the id from the file")}" spellcheck="false"></label>
            <label class="field-label">${t("Новое название", "New title")}
              <input class="pinput" id="mi-title" maxlength="80" placeholder="${t("Необязательно", "Optional")}"></label>
          </div>
          <div class="mu-row">
            <input type="file" id="mi-file" accept=".json,application/json" hidden>
            <button type="button" class="pbtn" id="mi-pick">${icon("attach")} ${t("Выбрать файл", "Choose a file")}</button>
            <span class="module-hint" id="mi-status"></span>
          </div>
        </div>
        <div class="module-wizard-card">
          <h4>${t("Вставкой из буфера", "Paste instead")}</h4>
          <textarea class="ptexta" id="mi-text" rows="6" placeholder='{"format":"memu-module","manifest":{...},"files":{...}}'></textarea>
          <div class="mu-row">
            <span class="mu-grow"></span>
            <button type="button" class="pbtn ghost" id="mi-paste-import">${icon("clipboard")} ${t("Импортировать вставленное", "Import the pasted text")}</button>
          </div>
        </div>
      </div>`;
    const status = $("#mi-status", host);
    const doImport = async (payload) => {
      status.innerHTML = `<span class="mu-spinner"></span> ${t("Импортирую…", "Importing…")}`;
      try {
        await apiJson("/api/pg/modules/import", { method: "POST", body: {
          package: payload,
          new_id: $("#mi-id", host).value.trim(),
          title: $("#mi-title", host).value.trim(),
        } });
        note(t("Модуль импортирован", "Module imported"), "ok");
        await mountSettings("list");
      } catch (error) { status.textContent = error.message; }
    };
    $("#mi-pick", host).onclick = () => $("#mi-file", host).click();
    $("#mi-file", host).onchange = async (event) => {
      const file = event.target.files && event.target.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        await doImport(JSON.parse(text));
      } catch (error) { status.textContent = t("Файл не разобран: ", "Could not parse the file: ") + error.message; }
    };
    $("#mi-paste-import", host).onclick = async () => {
      try {
        await doImport(JSON.parse($("#mi-text", host).value));
      } catch (error) { status.textContent = t("Текст не разобран: ", "Could not parse the text: ") + error.message; }
    };
    // Round 29: «Проверить и импортировать» — AI answer / ZIP / folder (modules-aipack.js).
    if (window.MemuModuleTools) MemuModuleTools.enhanceImport(host, { doImport });
    $$("[data-modules-tab]", host).forEach((button) => {
      button.onclick = () => mountSettings(button.dataset.modulesTab);
    });
  }

  /* --------------------------- открытие окна из трея и запуск --------------- */

  /** The MemuAI language changed: every open module re-translates at once. */
  function broadcastLanguage() {
    const lang = en() ? "en" : "ru";
    wins.forEach((win) => {
      postTo(win, { type: "memu-module-host", action: "language", payload: { lang } });
      if (win.parts && win.parts.title) win.parts.title.textContent = titleOf(win.row);
      if (win.parts) labelControls(win.parts);
    });
    renderLaunchers();
    renderTaskbar();
    syncHeadbar(true);
  }

  function handleHostEvent(name, data) {
    if (name === "theme") broadcastTheme();
    else if (name === "language") broadcastLanguage();
    else broadcastEvent(name, data);
  }

  async function openFromState() {
    // После перезапуска возвращаются все окна, что были открыты (не больше шести).
    const rows = catalog.filter((item) => item.open && item.enabled).slice(0, 6);
    for (const row of rows) {
      if (!wins.has(row.id)) await open(row.id);
    }
  }

  async function boot() {
    bindLayer();
    await refresh();
    if (appMode()) {
      // Round 33: the module's own window shows that one module; the main window polls events.
      if (MA()) MA().bootModuleApp(open);
      return;
    }
    openFromState();
    pollModuleEvents();
    setInterval(pollModuleEvents, 15000);
  }

  /* Уведомления фоновых модулей. Раньше курсор «что уже показано» жил в
     localStorage, а окно MemuAI (pywebview, WebView2 в приватном режиме по
     умолчанию) теряет localStorage при каждом запуске: курсор становился 0, и
     последние уведомления «Новые модели API» всплывали снова после каждого
     старта. Теперь подтверждение хранится на сервере рядом с событиями
     (background.json): pending=1 отдаёт только непоказанные, ack запоминает. */
  let eventPolling = false;
  let shownUpTo = 0;
  async function pollModuleEvents() {
    if (eventPolling) return;
    eventPolling = true;
    try {
      const data = await apiJson("/api/pg/modules/events?pending=1");
      const cursor = Math.max(0, Number(data.cursor) || 0);
      const events = (data.events || []).filter((event) => Number(event.id) > shownUpTo);
      for (const event of events.slice(-5)) {
        if (typeof toast === "function") toast(`${event.title}: ${String(event.text || "").slice(0, 180)}`, "info", {
          label: t("Открыть модуль", "Open module"), run: () => open(event.module_id),
        });
        broadcastEvent("notification", event);
      }
      shownUpTo = Math.max(shownUpTo, cursor);
      if (cursor > (Number(data.acknowledged) || 0)) {
        await apiJson("/api/pg/modules/events/ack", { method: "POST", body: { cursor } }).catch(() => null);
      }
    } catch (_) { /* Proxy may be starting; durable events remain available. */ }
    finally { eventPolling = false; }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { boot(); }, { once: true });
  } else {
    boot();
  }

  return {
    refresh, open, close, mountSettings, editCode,
    handleModuleMessage: onModuleMessage,
    handleHostEvent, broadcastTheme, hostEnv,
    moduleLog,
    mediaConsumer: consumeMediaSse,
    get catalog() { return catalog.slice(); },
    get openId() { return openId; },
    get openIds() { return [...wins.keys()]; },
    arrange, closeAll,
    navigate: async (moduleId, detail) => {
      const row = catalog.find((item) => item.id === moduleId);
      if (!row || !row.enabled) { if (typeof toast === "function") toast(t("Модуль выключен — включите его в настройках модулей", "The module is off — turn it on in the module settings"), "info"); return false; }
      await navigateModule(moduleId, Object.assign({ query: "", model: "", from: "host" }, detail || {}));
      return true;
    },
    snap: (moduleId, name) => snapWindow(wins.get(moduleId || openId), name),
    minimize: (moduleId) => minimizeWindow(wins.get(moduleId || openId)),
    restore: (moduleId) => restoreWindow(wins.get(moduleId || openId)),
    // Round 31: a close the person asked for (asks first when work would be lost).
    // Does not wait for the answer (the question may stay open): true when there was a window to close.
    requestClose: (moduleId) => { const win = wins.get(moduleId || openId); if (win) requestClose(win); return !!win; },
    closeReason: (moduleId) => closeReason(wins.get(moduleId || openId)),
    showHint: (moduleId) => showCoach(wins.get(moduleId || openId), true),
    loadHints: () => loadHints().then((seen) => [...seen]),
    // Round 33 (module-apps.js): close without a question (the module moved to its own window) and its size.
    dropWindow: (moduleId) => closeWindow(wins.get(moduleId)),
    windowBox: (moduleId) => { const win = wins.get(moduleId); return win ? currentBox(win) : null; },
    windowTitle: (moduleId) => { const win = wins.get(moduleId); return win ? titleOf(win.row) : ""; },
    windowState: (moduleId) => {
      const win = wins.get(moduleId || openId);
      return win ? { id: win.id, minimized: !!win.minimized, state: win.el.dataset.state || "floating",
        unsaved: !!win.unsaved, busy: busyCount.get(win.id) || 0 } : null;
    },
  };
})();
window.MemuModules = MemuModules;
// Global search results with open.action = "character" (Characters module provider) open that character.
(function registerCharacterSearch(tries) {
  if (window.MemuSearch && typeof window.MemuSearch.registerAction === "function") {
    window.MemuSearch.registerAction("character", (o) => MemuModules.navigate(o.module_id || "characters", { query: String(o.id || ""), from: "search" }));
  } else if (tries < 40) setTimeout(() => registerCharacterSearch(tries + 1), 250);
})(0);
