

(function () {
  "use strict";
  if (window.MemuThemeEditor) return;

  const doc = document;
  const html = () => doc.documentElement;
  const isEn = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  const MA = () => (typeof MemuAppearance !== "undefined" ? MemuAppearance : null);
  const getPref = (key, def) => { try { return typeof pref === "function" ? pref(key, def) : def; } catch (_) { return def; } };
  const mode = () => (html().dataset.theme === "light" ? "light" : "dark");
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const note = (text, type) => { try { if (typeof toast === "function") toast(text, type); } catch (_) {} };

  const FONTS = {
    sans: '"Segoe UI Variable Text", "Segoe UI", Inter, system-ui, sans-serif',
    serif: 'Charter, "Sitka Text", "Iowan Old Style", Georgia, serif',
    mono: '"Cascadia Code", "JetBrains Mono", Consolas, monospace',
    system: "system-ui, -apple-system, sans-serif",
    rounded: 'Nunito, "Segoe UI Variable Text", ui-rounded, system-ui, sans-serif',
  };
  const SHADOWS = {
    none: "none",
    soft: "0 4px 14px -4px rgba(0, 0, 0, .22)",
    medium: "0 12px 32px -10px rgba(0, 0, 0, .34)",
    strong: "0 24px 60px -16px rgba(0, 0, 0, .5)",
    glow: "0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent), 0 0 26px -2px color-mix(in srgb, var(--accent) 45%, transparent)",
  };
  const GROUPS = [
    ["window", "Окно", "Window"], ["side", "Боковая панель", "Sidebar"], ["top", "Верхняя панель", "Top bar"],
    ["chat", "Диалог", "Conversation"], ["composer", "Поле ввода", "Message box"], ["pop", "Окна и меню", "Dialogs and menus"],
    ["panels", "Панели", "Panels"],
  ];
  const BOX = ["bg", "alpha", "border", "bw", "radius", "shadow", "blur"];
  const TEXT = ["fg", "font", "size", "weight"];

  const ELEMENTS = [
    { id: "send", g: "composer", ru: "Кнопка отправки", en: "Send button", sel: "#btn-send", props: ["bg", "fg", "radius", "shadow"] },
    { id: "cbtn", g: "composer", ru: "Кнопки поля ввода", en: "Message box buttons", sel: ".composer .cbtn", props: ["fg", "bg", "radius"] },
    { id: "input", g: "composer", ru: "Текст в поле ввода", en: "Message box text", sel: "#input", props: ["fg", "font", "size"] },
    { id: "hint", g: "composer", ru: "Подпись под полем", en: "Caption under the box", sel: ".composer-hint", props: ["fg", "size", "opacity"] },
    { id: "composer", g: "composer", ru: "Поле ввода", en: "Message box", sel: ".composer", props: BOX.concat(["fg", "pad"]), container: true },
    { id: "model", g: "top", ru: "Выбор модели", en: "Model picker", sel: "#model-dd-btn", props: ["bg", "fg", "border", "bw", "radius", "font", "size", "weight"] },
    { id: "search", g: "top", ru: "Поиск по чатам", en: "Chat search", sel: ".topbar .chat-search", props: ["bg", "fg", "border", "bw", "radius"] },
    { id: "topicons", g: "top", ru: "Кнопки верхней панели", en: "Top bar buttons", sel: ".topbar .iconbtn, .topbar .bar-icon", props: ["fg", "bg", "radius"] },
    { id: "topbar", g: "top", ru: "Верхняя панель", en: "Top bar", sel: ".topbar.appbar", props: BOX.concat(["fg"]), container: true },
    { id: "newchat", g: "side", ru: "Кнопка «Новый чат»", en: "“New chat” button", sel: ".btn-new", props: ["bg", "fg", "border", "bw", "radius", "shadow", "size", "weight"] },
    { id: "chatactive", g: "side", ru: "Открытый чат в списке", en: "Open chat in the list", sel: ".chat-item.active", props: ["bg", "fg", "border", "bw", "radius", "weight"] },
    { id: "chatitem", g: "side", ru: "Чаты в списке", en: "Chats in the list", sel: ".chat-item", props: ["fg", "radius", "size", "weight", "pad"] },
    { id: "navbtn", g: "side", ru: "Пункты боковой панели", en: "Sidebar items", sel: ".side-nav .side-btn, .mode-switch .side-btn", props: ["fg", "radius", "size", "weight"] },
    { id: "modes", g: "side", ru: "Переключатель Чат · Work · MemuCode", en: "Chat · Work · MemuCode switch", sel: ".mode-switch", props: ["bg", "border", "bw", "radius", "accent"] },
    { id: "seclabel", g: "side", ru: "Заголовки разделов", en: "Section headings", sel: ".side .side-label", props: ["fg", "size", "weight", "font"] },
    { id: "brand", g: "side", ru: "Логотип", en: "Logo", sel: ".side .brand", props: ["fg", "size", "font", "weight"] },
    { id: "profile", g: "side", ru: "Карточка профиля", en: "Profile card", sel: ".profile-card", props: ["bg", "fg", "border", "bw", "radius"] },
    { id: "side", g: "side", ru: "Боковая панель", en: "Sidebar", sel: ".side", props: BOX.concat(["fg", "accent", "size"]), container: true, at: { size: ".side .chat-item, .side .side-btn" } },
    { id: "link", g: "chat", ru: "Ссылки в ответах", en: "Links in answers", sel: ".msg-assistant .body a", props: ["fg", "weight"] },
    { id: "inlinecode", g: "chat", ru: "Код в тексте", en: "Inline code", sel: ".msg-assistant .body :not(pre) > code", props: ["bg", "fg", "radius"] },
    { id: "code", g: "chat", ru: "Блоки кода", en: "Code blocks", sel: ".code-block-wrap", props: ["bg", "border", "bw", "radius", "shadow", "font", "size"], at: { font: ".code-block-wrap pre, .code-block-wrap code", size: ".code-block-wrap pre, .code-block-wrap code" } },
    { id: "who", g: "chat", ru: "Подпись ответа", en: "Answer header", sel: ".msg-assistant .who", props: ["fg", "size", "font", "weight"] },
    { id: "ububble", g: "chat", ru: "Ваши сообщения", en: "Your messages", sel: ".msg-user .bubble", props: BOX.concat(["fg", "pad", "font", "size"]), container: true, at: { font: ".msg-user .user-text", size: ".msg-user .user-text" } },
    { id: "answer", g: "chat", ru: "Ответы MemuAI", en: "MemuAI answers", sel: ".msg-assistant .answer", props: BOX.concat(["fg", "pad", "font", "size"]), container: true, at: { font: ".msg-assistant .answer .body", size: ".msg-assistant .answer .body" } },
    { id: "chips", g: "chat", ru: "Подсказки на старте", en: "Starter suggestions", sel: ".chip-sug", props: ["bg", "fg", "border", "bw", "radius", "size"] },
    { id: "greetsub", g: "chat", ru: "Подзаголовок приветствия", en: "Greeting subtitle", sel: ".greeting-subtitle", props: ["fg", "size", "font"] },
    { id: "greeting", g: "chat", ru: "Приветствие", en: "Greeting", sel: ".greeting h1", props: ["fg", "font", "size", "weight"] },
    { id: "pcard", g: "panels", ru: "Карточки панелей", en: "Panel cards", sel: ".mp-card", props: BOX.concat(["fg"]), container: true },
    { id: "menus", g: "pop", ru: "Меню и выпадающие списки", en: "Menus and dropdowns", sel: ".context-menu, .dd-pop, .app-menu-pop, .plus-menu, .memu-help-menu, .mp-pop", props: BOX.concat(["fg"]), container: true, reveal: () => doc.getElementById("bar-menu")?.click() },
    { id: "dialog", g: "pop", ru: "Диалоги и настройки", en: "Dialogs and settings", sel: "#panel", props: ["bg", "alpha", "border", "bw", "radius", "shadow", "blur"] },
    { id: "tooltip", g: "pop", ru: "Подсказки", en: "Tooltips", sel: ".memu-tooltip", props: ["bg", "fg", "radius", "size"] },
    { id: "canvas", g: "window", ru: "Холст диалога", en: "Conversation canvas", sel: ".main", props: BOX },
    { id: "window", g: "window", ru: "Фон окна", en: "Window background", sel: "#app", props: ["bg"], rule: "#app, body" },
  ];
  const EL = Object.fromEntries(ELEMENTS.map((e) => [e.id, e]));
  const PROP_INFO = {
    bg: ["Заливка", "Fill"], alpha: ["Непрозрачность заливки", "Fill opacity"], fg: ["Цвет текста", "Text colour"],
    border: ["Цвет рамки", "Border colour"], bw: ["Толщина рамки", "Border width"], radius: ["Скругление", "Corners"],
    shadow: ["Тень", "Shadow"], blur: ["Размытие под элементом", "Background blur"], font: ["Шрифт", "Font"],
    size: ["Размер текста", "Text size"], weight: ["Насыщенность", "Weight"], pad: ["Внутренний отступ", "Padding"],
    accent: ["Акцент внутри", "Accent inside"], opacity: ["Непрозрачность", "Opacity"],
  };
  const COLOR_PROPS = new Set(["bg", "fg", "border", "accent"]);
  const RANGES = { alpha: [0, 100, "%"], bw: [0, 8, "px"], radius: [0, 48, "px"], blur: [0, 40, "px"], size: [9, 40, "px"], pad: [0, 48, "px"], opacity: [20, 100, "%"] };

  const TOOLBAR = [
    { id: "nav", sel: ".topbar > .compact-toolbar", ru: "Меню, назад/вперёд, поиск", en: "Menu, back/forward, search" },
    { id: "model", sel: ".topbar > #model-dd", ru: "Выбор модели", en: "Model picker" },
    { id: "context", sel: ".topbar > #context-meter", ru: "Счётчик контекста", en: "Context meter" },
    { id: "modules", sel: ".topbar > #module-slot-topbar", ru: "Кнопки модулей", en: "Module buttons" },
    { id: "spacer", sel: ".topbar > .grow", ru: "Свободное место", en: "Free space", spacer: true },
    { id: "panels", sel: ".topbar > #panels-appbar", ru: "Панели в верхней панели", en: "Panels in the top bar" },
    { id: "help", sel: ".topbar > #btn-help", ru: "Справка «?»", en: "Help “?”" },
    { id: "focus", sel: ".topbar > #btn-focus", ru: "Режим фокуса", en: "Focus mode" },
    { id: "theme", sel: ".topbar > #btn-theme", ru: "Светлая / тёмная", en: "Light / dark" },
  ];
  const HIDE_SEL = {
    nav: "#bar-undo, #bar-redo, #bar-sidebar", search: ".topbar .chat-search", model: "#model-dd", context: "#context-meter",
    modules: "#module-slot-topbar", panels: "#panels-appbar", help: "#btn-help", focus: "#btn-focus", theme: "#btn-theme",
    mic: "#btn-mic", live: "#btn-live", hint: ".composer-hint",
    brand: ".side .brand", modes: ".side .mode-switch", sched: "#nav-sched", skills: "#nav-skills", status: ".side .hub-status-row",
  };

  const BG_PRESETS = {
    aurora: ["Сияние", "Aurora", "radial-gradient(1100px 680px at 8% -12%, color-mix(in srgb, var(--accent) 42%, transparent), transparent 62%), radial-gradient(900px 640px at 105% 112%, color-mix(in srgb, var(--accent-2, var(--accent)) 38%, transparent), transparent 62%), var(--bg-deep)"],
    sunset: ["Закат", "Sunset", "linear-gradient(140deg, rgb(255 138 101 / .55), rgb(214 92 152 / .5) 48%, rgb(88 70 170 / .55))"],
    ocean: ["Океан", "Ocean", "linear-gradient(160deg, rgb(32 120 190 / .55), rgb(20 160 170 / .45) 55%, rgb(24 50 110 / .6))"],
    forest: ["Лес", "Forest", "linear-gradient(150deg, rgb(60 140 90 / .5), rgb(30 90 80 / .55) 60%, rgb(20 50 45 / .6))"],
    dusk: ["Сумерки", "Dusk", "linear-gradient(180deg, rgb(70 60 140 / .55), rgb(150 80 130 / .45) 60%, rgb(230 140 110 / .4))"],
    mono: ["Графит", "Graphite", "radial-gradient(1200px 800px at 50% -20%, color-mix(in srgb, var(--text) 14%, transparent), transparent 70%), var(--bg-deep)"],
    accent: ["Акцент", "Accent", "linear-gradient(135deg, color-mix(in srgb, var(--accent) 34%, var(--bg-deep)), color-mix(in srgb, var(--accent-2, var(--accent)) 26%, var(--bg-deep)))"],
  };

  const LOOK_CACHE = "memu_look_v1";
  const TE_ATTRS = ["teSide", "teSidemode", "teTopbar", "teComposer", "teBubbles", "teUalign", "teCw", "teChatw", "teBg"];
  const DEFAULT_LAYOUT = { side_pos: "left", side_mode: "full", topbar: "", composer: "", bubbles: "", user_align: "", side_width: 0, composer_width: 0, chat_px: 0 };
  const DEFAULT_BG = { kind: "", preset: "aurora", c1: "", c2: "", angle: 0, image: "", dim: 0, blur: 0, canvas: 0 };

  function normLook(raw) {
    const r = raw && typeof raw === "object" ? raw : {};
    const t = r.tokens && typeof r.tokens === "object" ? r.tokens : {};
    return {
      v: 1,
      layout: { ...DEFAULT_LAYOUT, ...(r.layout || {}) },
      toolbar: { order: Array.isArray(r.toolbar?.order) ? r.toolbar.order.slice() : [], hidden: Array.isArray(r.toolbar?.hidden) ? r.toolbar.hidden.slice() : [] },
      elements: r.elements && typeof r.elements === "object" ? clone(r.elements) : {},
      tokens: { dark: { ...(t.dark || {}) }, light: { ...(t.light || {}) } },
      bg: { ...DEFAULT_BG, ...(r.bg || {}) },
    };
  }
  const SAFE_VALUE = /^[a-zA-Z0-9#%().,\-+*/ ]{1,240}$/;
  const safe = (v) => (v !== undefined && v !== null && SAFE_VALUE.test(String(v)) && !/url\s*\(|\/\*|expression/i.test(String(v)) ? String(v) : "");

  function declarations(el, props) {
    const out = {};
    const push = (prop, decl) => { const target = (el.at && el.at[prop]) || el.rule || el.sel; (out[target] = out[target] || []).push(decl); };
    const num = (key) => (props[key] === undefined || props[key] === "" ? null : Number(props[key]));
    if (props.bg) {
      const a = num("alpha");
      const value = safe(props.bg);
      if (value) push("bg", `background: ${a !== null && a < 100 ? `color-mix(in srgb, ${value} ${a}%, transparent)` : value} !important`);
    }
    if (props.fg && safe(props.fg)) {
      push("fg", `color: ${props.fg} !important`);
      if (el.container) push("fg", `--text: ${props.fg}; --muted: color-mix(in srgb, ${props.fg} 70%, transparent)`);
    }
    if (props.accent && safe(props.accent)) push("accent", `--accent: ${props.accent}; --accent-soft: color-mix(in srgb, ${props.accent} 15%, transparent)`);
    if (props.border && safe(props.border)) {
      push("border", `border-color: ${props.border} !important; border-style: solid !important`);
      if (num("bw") === null) push("border", "border-width: 1px !important");
    }
    if (num("bw") !== null) push("bw", `border-width: ${num("bw")}px !important; border-style: solid !important`);
    if (num("radius") !== null) push("radius", `border-radius: ${num("radius")}px !important`);
    if (num("blur") !== null) {
      const b = num("blur");
      push("blur", b ? `backdrop-filter: blur(${b}px) saturate(1.25) !important; -webkit-backdrop-filter: blur(${b}px) saturate(1.25) !important` : "backdrop-filter: none !important");
    }
    if (props.shadow && SHADOWS[props.shadow]) push("shadow", `box-shadow: ${SHADOWS[props.shadow]} !important`);
    if (props.font && FONTS[props.font]) push("font", `font-family: ${FONTS[props.font]} !important`);
    if (num("size") !== null) push("size", `font-size: ${num("size")}px !important`);
    if (props.weight) push("weight", `font-weight: ${Number(props.weight) || 400} !important`);
    if (num("pad") !== null) push("pad", `padding: ${num("pad")}px !important`);
    if (num("opacity") !== null) push("opacity", `opacity: ${num("opacity") / 100} !important`);
    return out;
  }
  const scoped = (scope, selectors) => selectors.split(",").map((s) => `${scope} ${s.trim()}`).join(",\n");

  function elementsCss(elements, m, scope) {
    let css = "";
    for (const el of ELEMENTS) {
      const spec = elements && elements[el.id];
      if (!spec) continue;
      const props = { ...(spec.all || {}), ...(spec[m] || {}) };
      if (!Object.keys(props).length) continue;
      const decl = declarations(el, props);
      for (const [selector, list] of Object.entries(decl)) css += `${scoped(scope, selector)} { ${list.join("; ")}; }\n`;
    }
    return css;
  }
  function tokensCss(tokens, scope) {
    const rows = Object.entries(tokens || {}).filter(([k, v]) => /^--[a-z][a-z0-9-]*$/.test(k) && safe(v)).map(([k, v]) => `  ${k}: ${v} !important;`);
    return rows.length ? `${scope} {\n${rows.join("\n")}\n}\n` : "";
  }
  function bgValue(bg) {
    if (!bg || !bg.kind) return "";
    if (bg.kind === "solid") return safe(bg.c1) || "";
    if (bg.kind === "image") return /^\/api\/pg\/theme-editor\/asset\/[a-f0-9]{20}\.(png|jpg|webp)$/.test(bg.image || "") ? `url("${bg.image}") center / cover no-repeat` : "";
    if (bg.preset === "custom") {
      const c1 = safe(bg.c1) || "var(--accent)", c2 = safe(bg.c2) || "var(--bg-deep)";
      return `linear-gradient(${Number(bg.angle) || 135}deg, ${c1}, ${c2})`;
    }
    return (BG_PRESETS[bg.preset] || BG_PRESETS.aurora)[2];
  }
  function bgCss(bg) {
    const value = bgValue(bg);
    let css = "";
    if (value) {
      const dim = Number(bg.dim) || 0, blur = Number(bg.blur) || 0;
      const layers = (dim ? `linear-gradient(color-mix(in srgb, var(--bg) ${dim}%, transparent), color-mix(in srgb, var(--bg) ${dim}%, transparent)), ` : "") + value;
      css += `html:root[data-te-bg] body { background: var(--bg-deep) !important; }
html:root[data-te-bg] body::before { content: ""; position: fixed; inset: ${blur ? -blur * 2 : 0}px; z-index: 0; pointer-events: none; background: ${layers}; ${blur ? `filter: blur(${blur}px);` : ""} }
html:root[data-te-bg] #app { position: relative; z-index: 1; background: transparent !important; isolation: isolate; }
html:root[data-te-bg] #app::before, html:root[data-te-bg] #app::after { opacity: 0 !important; animation: none !important; }
`;
    }
    const canvas = Number(bg && bg.canvas) || 0;
    if (canvas) {
      css += `html:root .main { background: color-mix(in srgb, var(--canvas, var(--canvas-bg, var(--bg))) ${100 - canvas}%, transparent) !important; backdrop-filter: blur(22px) saturate(1.2); -webkit-backdrop-filter: blur(22px) saturate(1.2); }
html:root .composer-wrap::before { opacity: 0; }
`;
    }
    return css;
  }
  function toolbarCss(toolbar) {
    let css = "";
    const order = (toolbar.order || []).filter((id) => TOOLBAR.some((t) => t.id === id));
    if (order.length) {
      const full = order.concat(TOOLBAR.map((t) => t.id).filter((id) => !order.includes(id)));
      full.forEach((id, i) => { css += `html:root ${TOOLBAR.find((t) => t.id === id).sel} { order: ${(i + 1) * 10}; }\n`; });
      const spacer = (full.indexOf("spacer") + 1) * 10;

      css += `html:root .topbar > :not(${TOOLBAR.map((t) => t.sel.replace(".topbar > ", "")).join("):not(")}):not(#mobile-menu) { order: ${spacer - 5}; }\nhtml:root .topbar > #mobile-menu { order: -100; }\n`;
    }
    const hidden = (toolbar.hidden || []).filter((id) => HIDE_SEL[id]);
    if (hidden.length) css += `${hidden.map((id) => HIDE_SEL[id].split(",").map((s) => `html:root ${s.trim()}`).join(", ")).join(",\n")} { display: none !important; }\n`;
    return css;
  }

  function compile(rawLook) {
    const look = normLook(rawLook);
    const lay = look.layout, attrs = {};
    if (lay.side_pos === "right") attrs.teSide = "right";
    if (lay.side_mode === "compact") attrs.teSidemode = "compact";
    if (lay.topbar) attrs.teTopbar = lay.topbar;
    if (lay.composer) attrs.teComposer = lay.composer;
    if (lay.bubbles) attrs.teBubbles = lay.bubbles;
    if (lay.user_align) attrs.teUalign = lay.user_align;
    let css = "/* MemuAI look (theme editor) — generated from preferences «ui_look» by theme-editor.js */\n";
    const vars = [];
    if (+lay.composer_width) { attrs.teCw = "1"; vars.push(`--te-composer-w: ${+lay.composer_width}px`); }
    if (+lay.chat_px) { attrs.teChatw = "1"; vars.push(`--te-chat-w: ${+lay.chat_px}px`); }
    if (vars.length) css += `html:root { ${vars.join("; ")}; }\n`;
    if (bgValue(look.bg)) attrs.teBg = "1";
    for (const m of ["dark", "light"]) {
      css += tokensCss(look.tokens[m], `html:root[data-theme="${m}"]`);
      css += elementsCss(look.elements, m, `html:root[data-theme="${m}"]`);
    }
    css += toolbarCss(look.toolbar) + bgCss(look.bg);
    return { attrs, css, look };
  }

  let styleNode = null;
  function ensureStyle() {
    if (styleNode && styleNode.isConnected) return styleNode;
    styleNode = doc.getElementById("memu-look") || doc.createElement("style");
    styleNode.id = "memu-look";
    doc.head.appendChild(styleNode);
    return styleNode;
  }
  function put(compiled) {
    const r = html();
    for (const key of TE_ATTRS) { if (compiled.attrs[key]) r.dataset[key] = compiled.attrs[key]; else delete r.dataset[key]; }
    const node = ensureStyle();
    if (node.textContent !== compiled.css) node.textContent = compiled.css;
  }

  try {
    const cached = JSON.parse(localStorage.getItem(LOOK_CACHE) || "null");
    if (cached && typeof cached.css === "string" && cached.attrs && typeof cached.attrs === "object") put(cached);
  } catch (_) {  }

  let lastLook = null;
  function applyLook(prefFn) {
    const get = typeof prefFn === "function" ? prefFn : getPref;
    const compiled = compile(get("ui_look", null));
    put(compiled);
    lastLook = compiled.look;
    applySideWidth(compiled.look.layout);
    if (!window.__memuPrefOverlay) {
      try { localStorage.setItem(LOOK_CACHE, JSON.stringify({ attrs: compiled.attrs, css: compiled.css })); } catch (_) {}
    }
  }
  function applySideWidth(layout) {
    const side = doc.querySelector(".side");
    if (!side || !+layout.side_width) return;
    const w = clamp(+layout.side_width, 200, 480);
    side.style.width = w + "px";
    if (!window.__memuPrefOverlay) { try { localStorage.setItem("memu_sidebar_width", String(w)); } catch (_) {} }
  }

  doc.addEventListener("pointerdown", (event) => {
    if (!event.target.closest || !event.target.closest("#sidebar-resizer")) return;
    const done = () => {
      doc.removeEventListener("mouseup", done, true);
      setTimeout(() => {
        const side = doc.querySelector(".side");
        if (!side || side.classList.contains("hidden")) return;
        const w = Math.round(side.getBoundingClientRect().width);
        if (ED.on) ED.sideResized(w);
        else if (lastLook && +lastLook.layout.side_width && +lastLook.layout.side_width !== w && typeof savePreferences === "function") {
          const look = clone(lastLook); look.layout.side_width = clamp(w, 200, 480);
          savePreferences({ ui_look: look }); lastLook = look;
        }
      }, 30);
    };
    doc.addEventListener("mouseup", done, true);
  }, true);

  window.MemuLook = Object.freeze({ apply: applyLook, compile, elementsCss, tokensCss, ELEMENTS, TOOLBAR, HIDE_SEL, BG_PRESETS, FONTS, SHADOWS, normLook,
    get look() { return lastLook; } });

  const APPEAR_KEYS = ["accent_color", "surface_dark", "surface_light", "ui_design", "app_font", "font_scale", "ui_density",
    "chat_width", "ui_radius", "ui_glass", "ui_msg_size", "ui_contrast", "ui_motion", "ui_motion_speed", "ui_look"];
  const UI_CACHE = "memu_te_ui";
  const CONFIRM_AT = 4;
  const listeners = new Set();
  const ED = {
    on: false, orig: null, draft: null, past: [], future: [], lastKey: "", lastAt: 0,
    sel: null, hover: null, tab: "element", picking: true, comparing: false, confirm: false,
    panel: null, raf: 0, zoneTimer: 0, ui: { dock: "right", x: null, y: null },
    sideResized: () => {},
  };
  const emit = (type) => listeners.forEach((fn) => { try { fn({ type }); } catch (_) {} });

  function zonesState() {
    const P = window.MemuPanelsLayout;
    if (!P || typeof P.zones !== "function") return {};
    const out = {};
    try { for (const z of P.zones()) out[z.id] = { view: z.view, chats: !!z.chats, width: z.width || null }; } catch (_) {}
    return out;
  }
  function snapshot() {
    const prefs = {};
    for (const key of APPEAR_KEYS) prefs[key] = clone(getPref(key, null));
    prefs.ui_look = normLook(prefs.ui_look);
    const side = doc.querySelector(".side");
    return { prefs, theme: (typeof S !== "undefined" && S.theme) || mode(), sidebar: !(side && side.classList.contains("hidden")), zones: zonesState() };
  }
  const look = () => ED.draft.prefs.ui_look;
  const curMode = () => (ED.draft ? ED.draft.theme : mode());

  function tidy(state) {
    const lk = state.prefs.ui_look;
    for (const [id, spec] of Object.entries(lk.elements)) {
      for (const part of ["all", "dark", "light"]) {
        if (!spec[part]) continue;
        for (const [k, v] of Object.entries(spec[part])) if (v === "" || v === null || v === undefined) delete spec[part][k];
        if (!Object.keys(spec[part]).length) delete spec[part];
      }
      if (!Object.keys(spec).length) delete lk.elements[id];
    }
    for (const m of ["dark", "light"]) for (const [k, v] of Object.entries(lk.tokens[m])) if (!v) delete lk.tokens[m][k];
    lk.toolbar.hidden = [...new Set(lk.toolbar.hidden)];
    if (same(lk.toolbar.order, TOOLBAR.map((t) => t.id))) lk.toolbar.order = [];
  }
  function leaves(a, b) {
    let n = 0;
    const walk = (x, y) => {
      const ox = x && typeof x === "object" && !Array.isArray(x), oy = y && typeof y === "object" && !Array.isArray(y);
      if (ox || oy) { new Set([...Object.keys(ox ? x : {}), ...Object.keys(oy ? y : {})]).forEach((k) => walk(ox ? x[k] : undefined, oy ? y[k] : undefined)); }
      else if (!same(x ?? null, y ?? null)) n += 1;
    };
    walk(a, b);
    return n;
  }
  const comparable = (st) => ({ p: st.prefs, t: st.theme, s: st.sidebar, z: st.zones });
  const changes = () => (ED.orig && ED.draft ? leaves(comparable(ED.orig), comparable(ED.draft)) : 0);

  function syncZones(target, now) {
    clearTimeout(ED.zoneTimer);
    const run = () => {
      const P = window.MemuPanelsLayout;
      if (!P || typeof P.setZone !== "function" || !target) return;
      const cur = zonesState();
      for (const [id, cfg] of Object.entries(target)) {
        if (cur[id] && !same(cur[id], cfg)) { try { P.setZone(id, { view: cfg.view, chats: cfg.chats, ...(cfg.width ? { width: cfg.width } : {}) }); } catch (_) {} }
      }
    };
    if (now) run(); else ED.zoneTimer = setTimeout(run, 220);
  }

  function applyState(st, opts = {}) {
    window.__memuPrefOverlay = { ...st.prefs };
    if (typeof S !== "undefined" && S.theme !== st.theme && typeof setTheme === "function") setTheme(st.theme, false);
    const side = doc.querySelector(".side");
    if (side) {
      side.classList.toggle("hidden", !st.sidebar);
      if (!+st.prefs.ui_look.layout.side_width && ED.orig) side.style.width = ED.orig.sideInline || "";
    }
    if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
    else if (MA()) MA().apply(getPref);
    if (opts.zones !== false) syncZones(st.zones);
  }

  function change(fn, key = "", opts = {}) {
    if (!ED.on || ED.comparing) return;
    const now = Date.now();
    if (!(key && key === ED.lastKey && now - ED.lastAt < 900)) {
      ED.past.push(clone(ED.draft));
      if (ED.past.length > 200) ED.past.shift();
    }
    ED.future = [];
    ED.lastKey = key; ED.lastAt = now;
    fn(ED.draft);
    tidy(ED.draft);
    applyState(ED.draft);
    if (opts.render === false) updateHead(); else render();
  }
  function undo() {
    if (!ED.past.length || ED.comparing) return;
    ED.future.push(clone(ED.draft)); ED.draft = ED.past.pop(); ED.lastKey = "";
    applyState(ED.draft); render();
  }
  function redo() {
    if (!ED.future.length || ED.comparing) return;
    ED.past.push(clone(ED.draft)); ED.draft = ED.future.pop(); ED.lastKey = "";
    applyState(ED.draft); render();
  }
  function compare(on) {
    if (!ED.on || on === ED.comparing) return;
    ED.comparing = on;
    doc.body.dataset.teBefore = L("Как было — отпустите, чтобы вернуться", "Before — release to go back");
    doc.body.classList.toggle("te-comparing", on);
    applyState(on ? ED.orig : ED.draft, { zones: false });
    const btn = ED.panel && ED.panel.querySelector('[data-act="compare"]');
    if (btn) btn.setAttribute("aria-pressed", String(on));
  }

  function loadUi() {
    try { Object.assign(ED.ui, JSON.parse(localStorage.getItem(UI_CACHE) || "{}")); } catch (_) {}
    if (!["right", "left", "float"].includes(ED.ui.dock)) ED.ui.dock = "right";
    if (["element", "look", "layout", "themes"].includes(ED.ui.tab)) ED.tab = ED.ui.tab;
  }
  function saveUi() { try { localStorage.setItem(UI_CACHE, JSON.stringify({ ...ED.ui, tab: ED.tab })); } catch (_) {} }

  async function open(opts = {}) {
    if (ED.on) { if (opts.tab) setTab(opts.tab); return true; }
    if (window.MEMU_SAFE_MODE && window.MEMU_SAFE_MODE.active) {
      note(L("В безопасном режиме оформление не меняется — выйдите из него, чтобы открыть редактор тем.", "Safe mode keeps the built-in look — leave it to open the theme editor."), "err");
      return false;
    }
    try { if (typeof closePanel === "function" && doc.querySelector("#panel-back.visible")) closePanel(); } catch (_) {}
    try { window.MemuLayers && MemuLayers.closeAll("api"); } catch (_) {}
    try { if (window.MemuPanelsLayout && MemuPanelsLayout.editing()) MemuPanelsLayout.exit(); } catch (_) {}
    if (MA() && !MA().catalog) { try { await MA().load(); } catch (_) {} }
    loadUi();
    ED.tab = opts.tab || ED.tab || "element";
    ED.orig = snapshot();
    const side = doc.querySelector(".side");
    ED.orig.sideInline = side ? side.style.width : "";
    ED.draft = clone(ED.orig);
    delete ED.draft.sideInline;
    ED.past = []; ED.future = []; ED.sel = null; ED.hover = null; ED.confirm = false; ED.comparing = false; ED.picking = true;
    ED.on = true;
    ED.sideResized = (w) => change((d) => { d.prefs.ui_look.layout.side_width = clamp(w, 200, 480); }, "side_width");
    window.__memuPrefOverlay = { ...ED.draft.prefs };
    doc.body.classList.add("te-on", "te-picking");
    html().dataset.teDock = ED.ui.dock;
    build();
    bindGlobal(true);
    loop();
    emit("enter");
    return true;
  }
  function finish() {
    bindGlobal(false);
    cancelAnimationFrame(ED.raf);
    clearTimeout(ED.zoneTimer);
    for (const node of doc.querySelectorAll(".te-ui")) node.remove();
    ED.panel = null;
    doc.body.classList.remove("te-on", "te-picking", "te-comparing", "te-alt", "te-dragging");
    delete html().dataset.teDock;
    ED.on = false; ED.sel = null; ED.hover = null; ED.confirm = false; ED.comparing = false;
    saveUi();
    emit("exit");
    window.dispatchEvent(new Event("resize"));
  }
  function cancel() {
    if (!ED.on) return;
    const o = ED.orig;
    window.__memuPrefOverlay = null;
    if (typeof S !== "undefined" && S.theme !== o.theme && typeof setTheme === "function") setTheme(o.theme, false);
    const side = doc.querySelector(".side");
    if (side) { side.classList.toggle("hidden", !o.sidebar); side.style.width = o.sideInline || ""; }
    if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
    syncZones(o.zones, true);
    const had = changes();
    finish();
    if (had) note(L("Изменения отменены — всё как было", "Changes discarded — everything is as it was"));
  }
  async function save() {
    if (!ED.on) return;
    if (ED.comparing) compare(false);
    const patch = {};
    for (const key of APPEAR_KEYS) if (!same(ED.draft.prefs[key], ED.orig.prefs[key])) patch[key] = clone(ED.draft.prefs[key]);
    if (ED.draft.theme !== ED.orig.theme) patch.theme = ED.draft.theme;
    if (ED.draft.sidebar !== ED.orig.sidebar) patch.sidebar_visible = ED.draft.sidebar;
    window.__memuPrefOverlay = null;
    const count = changes();
    if (Object.keys(patch).length && typeof savePreferences === "function") savePreferences(patch);
    if (typeof applyAppearancePrefs === "function") applyAppearancePrefs();
    finish();
    note(count ? L("Оформление сохранено", "Appearance saved") : L("Изменений не было", "Nothing to save"));
  }
  function requestCancel() {
    const n = changes();
    if (n >= CONFIRM_AT && !ED.confirm) { showConfirm(n); return; }
    cancel();
  }

  const inEditor = (node) => !!(node && node.closest && node.closest(".te-ui, .te-pop"));
  function matchEl(node) {
    for (let n = node; n && n.nodeType === 1 && n !== html(); n = n.parentElement) {
      if (inEditor(n)) return null;
      for (const el of ELEMENTS) { try { if (n.matches(el.sel)) return { el, node: n }; } catch (_) {  } }
    }
    return null;
  }
  function visibleNode(el) {
    for (const node of doc.querySelectorAll(el.sel)) { const r = node.getBoundingClientRect(); if (r.width > 1 && r.height > 1 && !inEditor(node)) return node; }
    return null;
  }
  function select(id, node) {
    const el = EL[id];
    if (!el) return;
    ED.sel = { id, node: node || visibleNode(el) };
    ED.tab = "element";
    render();
  }
  const blocking = (e) => ED.on && ED.picking && !ED.comparing && !ED.confirm && !e.altKey && !inEditor(e.target)
    && !(e.target.closest && e.target.closest("#panel-back.visible, .memu-tooltip, #startup-cover"));
  function onPointerMove(e) {
    if (!ED.on) return;
    ED.hover = blocking(e) ? matchEl(e.target) : null;
  }
  function onBlock(e) {
    if (!blocking(e)) return;
    if (e.type === "wheel") return;
    e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
    if (e.type === "click") {
      const m = matchEl(e.target);
      if (m) select(m.el.id, m.node);
    }
  }
  function onKey(e) {
    if (!ED.on || (window.MemuHotkeys && MemuHotkeys.recording)) return;
    const typing = e.target && e.target.closest && e.target.closest("input:not([type=range]):not([type=color]), textarea, select, [contenteditable='true'], [contenteditable='']");
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Escape") {
      if (window.MemuLayers && MemuLayers.isOpen()) return;
      if (doc.querySelector("#panel-back.visible")) return;
      e.preventDefault(); e.stopImmediatePropagation();
      if (window.MemuPanelsLayout && MemuPanelsLayout.editing()) { MemuPanelsLayout.exit(); return; }
      if (ED.confirm) { cancel(); return; }
      requestCancel();
      return;
    }
    if (ED.confirm) return;
    if (mod && !e.altKey && e.code === "KeyZ" && !typing) { e.preventDefault(); e.stopImmediatePropagation(); if (e.shiftKey) redo(); else undo(); return; }
    if (mod && !e.altKey && e.code === "KeyY" && !typing) { e.preventDefault(); e.stopImmediatePropagation(); redo(); return; }
    if (mod && !e.altKey && e.code === "KeyS") { e.preventDefault(); e.stopImmediatePropagation(); save(); return; }
    if (!mod && !typing && e.code === "Backslash") { e.preventDefault(); compare(true); return; }
    if (e.key === "Alt") doc.body.classList.add("te-alt");
  }
  function onKeyUp(e) {
    if (!ED.on) return;
    if (e.code === "Backslash") compare(false);
    if (e.key === "Alt") doc.body.classList.remove("te-alt");
  }
  function onBlur() { if (ED.on) { compare(false); doc.body.classList.remove("te-alt"); } }
  const BLOCK_EVENTS = ["pointerdown", "mousedown", "mouseup", "pointerup", "click", "dblclick", "contextmenu", "auxclick", "dragstart"];
  function bindGlobal(on) {
    const fn = on ? "addEventListener" : "removeEventListener";
    doc[fn]("pointermove", onPointerMove, true);
    for (const type of BLOCK_EVENTS) doc[fn](type, onBlock, true);
    window[fn]("keydown", onKey, true);
    window[fn]("keyup", onKeyUp, true);
    window[fn]("blur", onBlur);
    window[fn]("resize", placePanel);
  }

  function box(cls) {
    let node = doc.querySelector("." + cls);
    if (!node) {
      node = doc.createElement("div");
      node.className = `te-ui ${cls}`;
      node.setAttribute("aria-hidden", "true");
      node.innerHTML = '<span class="te-tag"></span>';
      doc.body.appendChild(node);
    }
    return node;
  }
  function placeBox(node, target, label, count) {
    if (!target || !target.isConnected) { node.hidden = true; return; }
    const r = target.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) { node.hidden = true; return; }
    node.hidden = false;
    node.style.transform = `translate(${Math.round(r.left)}px, ${Math.round(r.top)}px)`;
    node.style.width = Math.round(r.width) + "px";
    node.style.height = Math.round(r.height) + "px";
    const tag = node.firstChild;
    const text = label + (count > 1 ? ` · ${count}` : "");
    if (tag.textContent !== text) tag.textContent = text;
    node.classList.toggle("te-tag-in", r.top < 26);
    node.classList.toggle("te-tag-right", r.left > window.innerWidth - 220);
  }
  const countOf = (el) => { try { return doc.querySelectorAll(el.sel).length; } catch (_) { return 0; } };
  function loop() {
    cancelAnimationFrame(ED.raf);
    const tick = () => {
      if (!ED.on) return;
      const hover = box("te-hover"), sel = box("te-selbox");
      const h = ED.hover && (!ED.sel || ED.hover.node !== ED.sel.node) ? ED.hover : null;
      placeBox(hover, h && h.node, h ? h.el[isEn() ? "en" : "ru"] : "", h ? countOf(h.el) : 0);
      if (ED.sel && ED.tab === "element") {
        const el = EL[ED.sel.id];
        if (!ED.sel.node || !ED.sel.node.isConnected) ED.sel.node = visibleNode(el);
        placeBox(sel, ED.comparing ? null : ED.sel.node, el[isEn() ? "en" : "ru"], countOf(el));
      } else sel.hidden = true;
      ED.raf = requestAnimationFrame(tick);
    };
    ED.raf = requestAnimationFrame(tick);
  }

  const SVG = {
    palette: '<path d="M12 3a9 9 0 1 0 0 18c1 0 1.6-.8 1.6-1.6 0-.5-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5C21 6.6 17 3 12 3Z"/><circle cx="7.5" cy="11.5" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15.5" cy="8" r="1"/>',
    pick: '<path d="M4 4l6.5 16 2.2-6.8L19.5 11z"/><path d="m13 13 6 6"/>',
    undo: '<path d="M4 8h9a5 5 0 0 1 0 10H8"/><path d="m8 4-4 4 4 4"/>',
    redo: '<path d="M20 8h-9a5 5 0 0 0 0 10h5"/><path d="m16 4 4 4-4 4"/>',
    compare: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M12 4v16"/><path d="M7 9h2M7 13h2" opacity=".7"/>',
    dockR: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M15 4v16"/>',
    dockL: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/>',
    float: '<rect x="3" y="4" width="18" height="16" rx="2.5" opacity=".55"/><rect x="11" y="9" width="8" height="8" rx="1.5"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    reset: '<path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 4v4h4"/>',
    grip: '<circle cx="9" cy="6" r="1.2"/><circle cx="15" cy="6" r="1.2"/><circle cx="9" cy="12" r="1.2"/><circle cx="15" cy="12" r="1.2"/><circle cx="9" cy="18" r="1.2"/><circle cx="15" cy="18" r="1.2"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18"/><path d="M10.6 5.6A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-3 3.7M6.6 6.7A16.6 16.6 0 0 0 2.5 12S6 18.5 12 18.5a9 9 0 0 0 4.2-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    download: '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m14 6 4 4"/>',
    more: '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
    check: '<path d="m5 12 5 5L20 7"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    brush: '<path d="M14.5 4.5 19.5 9.5 11 18l-5-5z"/><path d="M6 13c-2 0-3 1.5-3 3.5S2 20 2 20s3.5.5 5.5-1.5S9 15 6 13Z"/>',
    layout: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16M9 10h12"/>',
    swatches: '<circle cx="8" cy="8" r="4"/><circle cx="16" cy="8" r="4"/><circle cx="12" cy="15.5" r="4"/>',
    target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>',
    sparkle: '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
    swap: '<path d="M7 7h12l-3-3M17 17H5l3 3"/>',
    code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
  };
  const I = (name, cls = "") => `<svg class="te-i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${SVG[name] || ""}</svg>`;
  const T = (ru, en) => esc(L(ru, en));
  const tbtn = (act, icon, label, extra = "") => `<button type="button" class="te-tbtn" data-act="${act}" aria-label="${esc(label)}" title="${esc(label)}" ${extra}>${I(icon)}</button>`;

  function seg(key, options, value, extra = "") {
    return `<div class="te-seg" role="radiogroup" data-seg="${esc(key)}" ${extra}>${options.map(([v, label]) =>
      `<button type="button" role="radio" aria-checked="${String(v) === String(value)}" data-v="${esc(v)}" data-fk="${esc(key)}:${esc(v)}">${esc(label)}</button>`).join("")}</div>`;
  }
  function slider(key, value, lo, hi, unit, opts = {}) {
    const v = value === null || value === undefined || value === "" ? (opts.def ?? lo) : value;
    return `<div class="te-slide"><input type="range" class="te-range" data-range="${esc(key)}" data-fk="${esc(key)}" min="${lo}" max="${hi}" step="${opts.step || 1}" value="${esc(v)}" aria-label="${esc(opts.label || key)}">
      <output>${esc(opts.fmt ? opts.fmt(v) : (opts.unset && (value === null || value === undefined || value === "" || value === 0) ? opts.unset : `${v}${unit}`))}</output></div>`;
  }
  function toggle(key, on, label) {
    return `<button type="button" class="te-switch" role="switch" aria-checked="${!!on}" data-switch="${esc(key)}" data-fk="sw:${esc(key)}" aria-label="${esc(label)}"><i></i></button>`;
  }

  let probe = null;
  function resolve(expr) {
    if (!expr) return "";
    if (!probe) { probe = doc.createElement("i"); probe.className = "te-ui"; probe.style.cssText = "position:fixed;width:0;height:0;pointer-events:none"; }
    if (!probe.isConnected) doc.body.appendChild(probe);
    probe.style.color = ""; probe.style.color = expr;
    if (!probe.style.color) return "";
    const value = getComputedStyle(probe).color;
    const rgb = value.match(/rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:[, /]+([\d.]+))?/);
    const srgb = value.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?/);
    const parts = rgb ? [+rgb[1], +rgb[2], +rgb[3]] : srgb ? [srgb[1] * 255, srgb[2] * 255, srgb[3] * 255] : null;
    return parts ? "#" + parts.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("") : "";
  }
  const TOKEN_NAMES = {
    "var(--accent)": ["Акцент", "Accent"], "var(--accent-2)": ["Акцент 2", "Accent 2"], "var(--text)": ["Текст", "Text"],
    "var(--muted)": ["Приглушённый", "Muted"], "var(--bg)": ["Фон", "Background"], "var(--bg-deep)": ["Глубокий фон", "Deep background"],
    "var(--panel)": ["Панель", "Panel"], "var(--panel-2)": ["Панель 2", "Panel 2"], "var(--border)": ["Рамка", "Border"],
    "var(--accent-soft)": ["Подложка акцента", "Accent wash"], "var(--danger)": ["Ошибка", "Error"], "var(--ok)": ["Успех", "Success"],
    transparent: ["Прозрачный", "Transparent"],
  };
  function colorLabel(value) {
    if (!value) return L("Как в теме", "From the theme");
    const named = TOKEN_NAMES[value];
    return named ? L(named[0], named[1]) : value.length > 22 ? value.slice(0, 20) + "…" : value;
  }
  function colorBtn(key, value, current) {
    const show = value || current || "transparent";
    return `<button type="button" class="te-color ${value ? "set" : ""}" data-color="${esc(key)}" data-fk="c:${esc(key)}" aria-haspopup="dialog">
      <i style="--c:${esc(show)}"></i><span>${esc(colorLabel(value))}</span></button>`;
  }
  function row(label, control, opts = {}) {
    return `<div class="te-row ${opts.set ? "is-set" : ""} ${opts.cls || ""}" ${opts.attrs || ""}>
      <div class="te-row-l"><span>${esc(label)}</span>${opts.hint ? `<small>${esc(opts.hint)}</small>` : ""}</div>
      <div class="te-row-c">${control}</div>
      ${opts.reset ? `<button type="button" class="te-reset" data-reset="${esc(opts.reset)}" title="${T("Сбросить", "Reset")}" aria-label="${T("Сбросить", "Reset")}" ${opts.set ? "" : "disabled"}>${I("reset")}</button>` : '<span class="te-reset-gap"></span>'}
    </div>`;
  }
  const section = (title, body, opts = {}) => `<section class="te-sec" ${opts.id ? `id="${opts.id}"` : ""}>
      <h4><span>${esc(title)}</span>${opts.aside || ""}</h4>${opts.desc ? `<p class="te-desc">${esc(opts.desc)}</p>` : ""}${body}</section>`;

  const TABS = [["element", "target", "Элемент", "Element"], ["look", "brush", "Оформление", "Look"], ["layout", "layout", "Расположение", "Layout"], ["themes", "swatches", "Темы", "Themes"]];
  function build() {
    for (const node of doc.querySelectorAll(".te-panel")) node.remove();
    const panel = doc.createElement("aside");
    panel.className = "te-ui te-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", L("Редактор тем", "Theme editor"));
    panel.innerHTML = `
      <header class="te-head" data-drag>
        <span class="te-logo">${I("palette")}</span>
        <div class="te-title"><b>${T("Редактор тем", "Theme editor")}</b><small id="te-count"></small></div>
        <div class="te-tools">
          ${tbtn("pick", "pick", L("Выбор элемента мышью (Alt — щёлкать как обычно)", "Pick elements with the mouse (hold Alt to click normally)"), 'aria-pressed="true"')}
          ${tbtn("undo", "undo", L("Отменить — Ctrl+Z", "Undo — Ctrl+Z"))}
          ${tbtn("redo", "redo", L("Повторить — Ctrl+Shift+Z", "Redo — Ctrl+Shift+Z"))}
          ${tbtn("compare", "compare", L("Удерживайте, чтобы увидеть «как было» (или клавишу \\)", "Hold to see “before” (or the \\ key)"), 'aria-pressed="false"')}
          ${tbtn("dock", ED.ui.dock === "left" ? "dockL" : ED.ui.dock === "float" ? "float" : "dockR", L("Расположение редактора", "Where the editor sits"), 'aria-haspopup="menu"')}
          ${tbtn("cancel", "close", L("Закрыть без сохранения — Esc", "Close without saving — Esc"))}
        </div>
      </header>
      <nav class="te-tabs" role="tablist">${TABS.map(([id, icon, ru, en]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${ED.tab === id}">${I(icon)}<span>${T(ru, en)}</span></button>`).join("")}</nav>
      <div class="te-body" id="te-body" role="tabpanel"></div>
      <footer class="te-foot">
        <span class="te-hint"><kbd>Esc</kbd> ${T("отменить всё", "discard all")}</span>
        <button type="button" class="te-btn ghost" data-act="cancel">${T("Отмена", "Cancel")}</button>
        <button type="button" class="te-btn primary" data-act="save">${I("check")}<span>${T("Сохранить", "Save")}</span></button>
      </footer>
      <div class="te-confirm" hidden role="alertdialog" aria-modal="true" aria-labelledby="te-confirm-title"></div>`;
    doc.body.appendChild(panel);
    ED.panel = panel;
    panel.addEventListener("click", onPanelClick);
    panel.addEventListener("input", onPanelInput);
    panel.addEventListener("change", onPanelChange);
    panel.addEventListener("pointerdown", onPanelPointerDown);
    panel.addEventListener("keydown", onPanelKey);
    placePanel();
    render();
    requestAnimationFrame(() => panel.classList.add("te-in"));
    panel.tabIndex = -1;
    setTimeout(() => panel.focus({ preventScroll: true }), 30);
  }
  function placePanel() {
    const panel = ED.panel;
    if (!panel) return;
    const dock = ED.ui.dock;
    html().dataset.teDock = dock;
    panel.dataset.dock = dock;
    if (dock === "float") {

      const vw = window.innerWidth, vh = window.innerHeight;
      const w = panel.offsetWidth || 360;
      const natural = Math.max(200, Math.min(720, vh - 32));
      const minH = Math.min(natural, 380);
      const x = ED.ui.x === null ? vw - w - 24 : ED.ui.x, y = ED.ui.y === null ? 72 : ED.ui.y;
      ED.ui.x = clamp(x, 8, Math.max(8, vw - w - 8));
      ED.ui.y = clamp(y, 8, Math.max(8, vh - minH - 8));
      panel.style.left = ED.ui.x + "px"; panel.style.top = ED.ui.y + "px";
      panel.style.height = Math.max(Math.min(natural, vh - ED.ui.y - 8), Math.min(minH, vh - 16)) + "px";
    } else { panel.style.left = ""; panel.style.top = ""; panel.style.height = ""; }
  }
  function setDock(dock) {
    ED.ui.dock = dock;
    placePanel(); saveUi(); render();
    window.dispatchEvent(new Event("resize"));
  }
  function setTab(tab) {
    ED.tab = tab; saveUi();
    render();
  }
  function updateHead() {
    const panel = ED.panel;
    if (!panel) return;
    const n = changes();
    const count = panel.querySelector("#te-count");
    if (count) count.textContent = n ? L(`Не сохранено: ${n}`, `Unsaved: ${n}`) : L("Нет изменений", "No changes");
    panel.classList.toggle("te-dirty", !!n);
    panel.querySelector('[data-act="undo"]').disabled = !ED.past.length;
    panel.querySelector('[data-act="redo"]').disabled = !ED.future.length;
    panel.querySelector('[data-act="pick"]').setAttribute("aria-pressed", String(ED.picking));
    panel.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === ED.tab)));
    doc.body.classList.toggle("te-picking", ED.picking);
  }
  const plural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many);

  function render() {
    const panel = ED.panel;
    if (!panel) return;
    updateHead();
    const body = panel.querySelector("#te-body");
    const active = doc.activeElement && panel.contains(doc.activeElement) ? doc.activeElement.dataset.fk : "";
    const scroll = body.dataset.pane === ED.tab ? body.scrollTop : 0;
    body.dataset.pane = ED.tab;
    body.innerHTML = ED.tab === "element" ? elementTab() : ED.tab === "look" ? lookTab() : ED.tab === "layout" ? layoutTab() : themesTab();
    body.scrollTop = scroll;
    if (active) { const again = body.querySelector(`[data-fk="${CSS.escape(active)}"]`); if (again) again.focus({ preventScroll: true }); }
    if (ED.tab === "themes") hydrateThemes();
  }

  const groupName = (g) => { const row = GROUPS.find((x) => x[0] === g); return row ? L(row[1], row[2]) : g; };
  const elName = (el) => (isEn() ? el.en : el.ru);
  function computedOf(node, prop) {
    if (!node) return null;
    const cs = getComputedStyle(node);
    switch (prop) {
      case "bg": return cs.backgroundColor;
      case "fg": return cs.color;
      case "border": return parseFloat(cs.borderTopWidth) ? cs.borderTopColor : "transparent";
      case "accent": return cs.getPropertyValue("--accent").trim();
      case "radius": return Math.round(parseFloat(cs.borderTopLeftRadius) || 0);
      case "size": return Math.round(parseFloat(cs.fontSize) || 14);
      case "pad": return Math.round(parseFloat(cs.paddingTop) || 0);
      case "bw": return Math.round(parseFloat(cs.borderTopWidth) || 0);
      case "alpha": case "opacity": return 100;
      case "blur": return 0;
      default: return null;
    }
  }
  function elementValue(id, prop) {
    const spec = look().elements[id] || {};
    return COLOR_PROPS.has(prop) ? (spec[curMode()] || {})[prop] : (spec.all || {})[prop];
  }
  const changedEl = (id) => { const s = look().elements[id]; return !!(s && (s.all || s.dark || s.light)); };

  function elementTab() {
    if (!ED.sel) {
      const groups = GROUPS.map(([g]) => {
        const items = ELEMENTS.filter((e) => e.g === g);
        return `<div class="te-elgroup"><h5>${esc(groupName(g))}</h5><div class="te-elchips">${items.map((e) =>
          `<button type="button" class="te-elchip ${changedEl(e.id) ? "is-set" : ""}" data-el="${e.id}" data-fk="el:${e.id}">${esc(elName(e))}</button>`).join("")}</div></div>`;
      }).join("");
      return `<div class="te-empty">
          <div class="te-empty-art" aria-hidden="true"><span class="a"></span><span class="b"></span><span class="c"></span>${I("pick", "te-empty-cursor")}</div>
          <b>${T("Наведите на любую часть окна и щёлкните", "Point at any part of the window and click")}</b>
          <p>${T("Выбранный элемент подсветится — здесь появятся его цвета, форма и текст. Всё меняется сразу, сохранится только по кнопке «Сохранить». Чтобы нажать кнопку приложения как обычно, удерживайте Alt.",
            "The element lights up and its colours, shape and text appear here. Everything changes live and is kept only when you press Save. Hold Alt to click the app as usual.")}</p>
        </div>
        ${section(L("Или выберите из списка", "Or pick from the list"), groups)}`;
    }
    const el = EL[ED.sel.id];
    const node = ED.sel.node && ED.sel.node.isConnected ? ED.sel.node : visibleNode(el);
    const m = curMode();
    const hasColor = el.props.some((p) => COLOR_PROPS.has(p));
    const rows = el.props.map((prop) => propRow(el, node, prop)).join("");
    const offscreen = !node;
    return `<div class="te-elhead">
        <button type="button" class="te-back" data-act="desel" data-fk="desel">${I("chevron", "te-flip")}<span>${T("Все элементы", "All elements")}</span></button>
        <div class="te-elname"><small>${esc(groupName(el.g))}</small><b>${esc(elName(el))}</b></div>
        <button type="button" class="te-btn ghost small" data-act="reset-el" ${changedEl(el.id) ? "" : "disabled"}>${I("reset")}<span>${T("Сбросить", "Reset")}</span></button>
      </div>
      ${offscreen ? `<div class="te-note">${I("eyeOff")}<span>${T("Сейчас этого элемента нет на экране — изменения применятся, когда он появится.", "This element is not on screen right now — the changes apply when it appears.")}</span>${el.reveal ? `<button type="button" class="te-link" data-act="reveal">${T("Показать", "Show it")}</button>` : ""}</div>` : ""}
      ${hasColor ? `<div class="te-modenote">${I(m === "dark" ? "eye" : "sparkle")}<span>${m === "dark" ? T("Цвета — для тёмной темы. Для светлой они задаются отдельно.", "Colours apply to the dark theme. The light theme keeps its own.") : T("Цвета — для светлой темы. Для тёмной они задаются отдельно.", "Colours apply to the light theme. The dark theme keeps its own.")}</span>
        <button type="button" class="te-link" data-act="mode-swap">${m === "dark" ? T("К светлой", "To light") : T("К тёмной", "To dark")}</button></div>` : ""}
      ${contrastNote(el, node)}
      <div class="te-props">${rows}</div>`;
  }

  const lum = (hex) => { const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  function textOn(node) {
    let n = node;
    while (n && n.nodeType === 1) { const bg = getComputedStyle(n).backgroundColor; if (!/rgba?\(0, 0, 0, 0\)|transparent/.test(bg) && !/\/ 0\)$/.test(bg)) return bg; n = n.parentElement; }
    return "";
  }
  function contrastNote(el, node) {
    if (!node || !el.props.includes("bg") || !elementValue(el.id, "bg")) return "";
    const fg = resolve(getComputedStyle(node).color), bg = resolve(textOn(node));
    if (!fg || !bg) return "";
    const r = ratio(fg, bg);
    if (r >= 4.5) return "";
    return `<div class="te-note warn">${I("eye")}<span>${isEn() ? `Text is hard to read on this fill (${r.toFixed(1)}:1, 4.5:1 is needed).` : `Текст плохо читается на этой заливке (${r.toFixed(1)}:1, нужно от 4.5:1).`}</span>${el.props.includes("fg") ? `<button type="button" class="te-link" data-act="fix-contrast">${T("Подобрать цвет текста", "Fix text colour")}</button>` : ""}</div>`;
  }
  function fixContrast() {
    const el = EL[ED.sel.id], node = ED.sel.node || visibleNode(el);
    if (!node) return;
    const bg = resolve(textOn(node));
    const best = ["var(--on-accent)", "var(--text)", "var(--bg)", "#ffffff", "#111111"].map((c) => [c, resolve(c)]).filter(([, hex]) => hex && bg)
      .sort((a, b) => ratio(b[1], bg) - ratio(a[1], bg))[0];
    if (best) setElementProp("fg", best[0], "", { render: true });
  }
  function propRow(el, node, prop) {
    const [ru, en] = PROP_INFO[prop];
    const value = elementValue(el.id, prop);
    const set = value !== undefined && value !== "";
    const key = "el:" + prop;
    let control;
    if (COLOR_PROPS.has(prop)) control = colorBtn(key, set ? value : "", computedOf(node, prop));
    else if (RANGES[prop]) {
      const [lo, hi, unit] = RANGES[prop];
      const cur = set ? value : computedOf(node, prop);
      control = slider(key, cur ?? lo, lo, hi, unit, { label: L(ru, en), fmt: (v) => (set ? `${v}${unit}` : L("авто", "auto")) });
      if (prop === "alpha" && !elementValue(el.id, "bg")) control = control.replace("<input", '<input title="' + T("Сначала выберите заливку — или двигайте: возьмём текущий цвет", "Pick a fill first — or drag: the current colour is used") + '"');
    } else if (prop === "shadow") control = seg(key, [["", L("Авто", "Auto")], ["none", L("Нет", "None")], ["soft", L("Мягкая", "Soft")], ["medium", L("Средняя", "Medium")], ["strong", L("Сильная", "Strong")], ["glow", L("Свечение", "Glow")]], value || "", 'data-wrap="1"');
    else if (prop === "font") control = seg(key, [["", L("Авто", "Auto")], ["sans", L("Гротеск", "Sans")], ["serif", L("С засечками", "Serif")], ["mono", L("Моно", "Mono")], ["rounded", L("Округлый", "Rounded")]], value || "", 'data-wrap="1"');
    else if (prop === "weight") control = seg(key, [["", L("Авто", "Auto")], ["400", "400"], ["500", "500"], ["600", "600"], ["700", "700"]], value || "");
    else control = "";
    return row(L(ru, en), control, { set, reset: key, cls: RANGES[prop] || ["shadow", "font", "weight"].includes(prop) ? "te-row-wide" : "" });
  }

  const catalog = () => (MA() && MA().catalog) || null;
  const pick2 = (row, key) => (isEn() ? row[key ? key + "_en" : "en"] : row[key ? key + "_ru" : "ru"]) || row.name || row.id || "";
  function variantCards(m) {
    const cat = catalog();
    if (!cat) return `<p class="te-desc">${T("Каталог тем загружается…", "Loading the theme catalogue…")}</p>`;
    const current = ED.draft.prefs[`surface_${m}`] || "";
    const accent = (cat.accents || []).find((a) => a.id === ED.draft.prefs.accent_color) || (cat.accents || [])[0] || {};
    const card = (ref, name, p, custom) => `<button type="button" class="te-variant ${current === ref ? "on" : ""}" data-variant="${esc(ref)}" data-fk="v:${esc(ref)}" aria-pressed="${current === ref}" title="${esc(name)}">
        <span class="te-vmini" style="--v-bg:${esc(p["--bg"] || "")};--v-side:${esc(p["--bg-deep"] || "")};--v-panel:${esc(p["--panel"] || "")};--v-text:${esc(p["--text"] || "")};--v-muted:${esc(p["--muted"] || "")};--v-accent:${esc(p["--accent"] || accent[m] || "")}"><i></i><i></i><i></i><i></i></span>
        <span class="te-vname">${esc(name)}${custom ? `<em>${T("своя", "custom")}</em>` : ""}</span></button>`;
    return `<div class="te-variants">${(cat.variants[m] || []).map((v) => card(v.id, pick2(v), v.preview || {}, false)).join("")}${(cat.custom || []).filter((c) => c.base === m).map((c) => card(c.ref, c.name, c.preview || {}, true)).join("")}</div>`;
  }
  const DESIGN_IDS = ["classic", "aurora", "focus", "grid", "glass", "soft", "terminal", "brutal"];
  function designCards() {
    const cat = catalog();
    const rows = (cat && cat.designs && cat.designs.length ? cat.designs : DESIGN_IDS.map((id) => ({ id, ru: id, en: id })));
    const cur = ED.draft.prefs.ui_design || "classic";
    return `<div class="te-designs">${rows.map((d) => `<button type="button" class="te-design ${cur === d.id ? "on" : ""}" data-design="${esc(d.id)}" data-fk="d:${esc(d.id)}" aria-pressed="${cur === d.id}" title="${esc(pick2(d, "note"))}">
        <span class="design-mini design-mini-${esc(d.id)}" aria-hidden="true"><i class="dm-side"></i><i class="dm-top"></i><i class="dm-msg a"></i><i class="dm-msg b"></i><i class="dm-msg c"></i><i class="dm-input"></i></span>
        <span>${esc(pick2(d))}</span></button>`).join("")}</div>`;
  }
  function accentDots() {
    const cat = catalog();
    const m = curMode(), cur = ED.draft.prefs.accent_color || "terracotta";
    return `<div class="te-accents">${((cat && cat.accents) || []).map((a) => `<button type="button" class="te-accent ${cur === a.id ? "on" : ""}" data-accent="${esc(a.id)}" data-fk="a:${esc(a.id)}" aria-pressed="${cur === a.id}" title="${esc(pick2(a))}" aria-label="${esc(pick2(a))}" style="--c:${esc(a[m])}">${I("check")}</button>`).join("")}</div>`;
  }
  const THEME_TOKENS = [
    ["--bg", "Фон диалога", "Conversation background"], ["--bg-deep", "Фон окна", "Window background"], ["--panel", "Панели и карточки", "Panels and cards"],
    ["--panel-2", "Вторичная поверхность", "Secondary surface"], ["--text", "Текст", "Text"], ["--muted", "Второстепенный текст", "Secondary text"],
    ["--accent", "Акцент", "Accent"], ["--border", "Рамки", "Borders"], ["--side-bg", "Боковая панель", "Sidebar"],
    ["--user-bubble", "Ваши сообщения", "Your messages"], ["--link", "Ссылки", "Links"], ["--code-bg", "Фон кода", "Code background"],
  ];
  function lookTab() {
    const p = ED.draft.prefs, lk = look(), m = curMode(), bg = lk.bg;
    const tokens = lk.tokens[m] || {};
    const bgControls = [];
    if (bg.kind === "gradient") {
      bgControls.push(`<div class="te-bgpresets">${Object.entries(BG_PRESETS).map(([id, [ru, en, css]]) => `<button type="button" class="te-bgp ${bg.preset === id ? "on" : ""}" data-bgpreset="${id}" data-fk="bgp:${id}" aria-pressed="${bg.preset === id}" title="${esc(L(ru, en))}"><i style="background:${esc(css)}"></i><span>${esc(L(ru, en))}</span></button>`).join("")}
        <button type="button" class="te-bgp ${bg.preset === "custom" ? "on" : ""}" data-bgpreset="custom" data-fk="bgp:custom" aria-pressed="${bg.preset === "custom"}"><i style="background:linear-gradient(${Number(bg.angle) || 135}deg, ${esc(bg.c1 || "var(--accent)")}, ${esc(bg.c2 || "var(--bg-deep)")})"></i><span>${T("Свой", "Custom")}</span></button></div>`);
      if (bg.preset === "custom") {
        bgControls.push(row(L("Цвет 1", "Colour 1"), colorBtn("bg:c1", bg.c1, "var(--accent)")));
        bgControls.push(row(L("Цвет 2", "Colour 2"), colorBtn("bg:c2", bg.c2, "var(--bg-deep)")));
        bgControls.push(row(L("Угол", "Angle"), slider("bg:angle", Number(bg.angle) || 135, 0, 360, "°", { label: L("Угол", "Angle") }), { cls: "te-row-wide" }));
      }
    } else if (bg.kind === "solid") bgControls.push(row(L("Цвет", "Colour"), colorBtn("bg:c1", bg.c1, "var(--bg-deep)")));
    else if (bg.kind === "image") {
      bgControls.push(`<div class="te-bgimage">${bg.image ? `<span class="te-bgthumb" style="background-image:url('${esc(bg.image)}')"></span>` : `<span class="te-bgthumb empty">${I("image")}</span>`}
        <button type="button" class="te-btn ghost small" data-act="bg-upload">${I("upload")}<span>${bg.image ? T("Другая картинка", "Another picture") : T("Выбрать картинку", "Choose a picture")}</span></button>
        <input type="file" hidden accept="image/png,image/jpeg,image/webp" data-file="bg"></div>`);
    }
    if (bg.kind) {
      bgControls.push(row(L("Приглушить", "Dim"), slider("bg:dim", Number(bg.dim) || 0, 0, 85, "%", { label: L("Приглушить", "Dim") }), { cls: "te-row-wide", hint: L("ближе к цвету фона темы", "towards the theme background") }));
      bgControls.push(row(L("Размыть", "Blur"), slider("bg:blur", Number(bg.blur) || 0, 0, 40, "px", { label: L("Размыть", "Blur") }), { cls: "te-row-wide" }));
    }
    bgControls.push(row(L("Прозрачность холста", "Canvas transparency"), slider("bg:canvas", Number(bg.canvas) || 0, 0, 70, "%", { label: L("Прозрачность холста", "Canvas transparency") }), { cls: "te-row-wide", hint: L("фон окна просвечивает сквозь диалог", "the window background shows through the conversation") }));
    return section(L("Тема", "Theme"), `${seg("theme", [["dark", L("Тёмная", "Dark")], ["light", L("Светлая", "Light")]], m, 'data-full="1"')}
        <div class="te-subhead">${m === "dark" ? T("Вариант тёмной темы", "Dark theme variant") : T("Вариант светлой темы", "Light theme variant")}</div>${variantCards(m)}`)
      + section(L("Акцентный цвет", "Accent colour"), accentDots())
      + section(L("Дизайн интерфейса", "Interface design"), designCards(), { desc: L("Форма, типографика и компоновка всего окна.", "The shape, typography and layout of the whole window.") })
      + section(L("Фон окна", "Window background"), seg("bg:kind", [["", L("Нет", "None")], ["gradient", L("Градиент", "Gradient")], ["solid", L("Цвет", "Colour")], ["image", L("Картинка", "Picture")]], bg.kind, 'data-full="1"') + bgControls.join(""),
        { aside: resetAside("bg", !same(bg, DEFAULT_BG)) })
      + section(L("Текст", "Text"),
        row(L("Шрифт ответов", "Answer font"), seg("p:app_font", [["serif", L("Книжный", "Book")], ["sans", L("Гротеск", "Sans")], ["mono", L("Моно", "Mono")], ["system", L("Системный", "System")]], p.app_font || "serif", 'data-wrap="2"'), { cls: "te-row-wide" })
        + row(L("Масштаб интерфейса", "Interface scale"), slider("p:font_scale", Number(p.font_scale) || 100, 85, 125, "%", { label: L("Масштаб", "Scale"), step: 5 }), { cls: "te-row-wide", set: Number(p.font_scale || 100) !== 100, reset: "p:font_scale" })
        + row(L("Текст сообщений", "Message text"), seg("p:ui_msg_size", [["sm", L("Мельче", "Smaller")], ["", L("Обычный", "Default")], ["lg", L("Крупнее", "Larger")], ["xl", L("Крупный", "Large")]], p.ui_msg_size || ""), { cls: "te-row-wide" }))
      + section(L("Форма и плотность", "Shape and density"),
        row(L("Скругления", "Corners"), seg("p:ui_radius", [["sharp", L("Острые", "Sharp")], ["", L("Обычные", "Default")], ["round", L("Мягкие", "Round")]], p.ui_radius || ""), { cls: "te-row-wide" })
        + row(L("Плотность", "Density"), seg("p:ui_density", [["compact", L("Компактно", "Compact")], ["", L("Обычно", "Default")], ["spacious", L("Просторно", "Spacious")]], p.ui_density || ""), { cls: "te-row-wide" })
        + row(L("Матовое стекло", "Frosted glass"), seg("p:ui_glass", [["", L("Нет", "Off")], ["light", L("Лёгкое", "Light")], ["strong", L("Сильное", "Strong")]], p.ui_glass || ""), { cls: "te-row-wide", hint: L("меню, диалоги и боковая панель", "menus, dialogs and the sidebar") })
        + row(L("Контрастность", "Contrast"), seg("p:ui_contrast", [["", L("Обычная", "Default")], ["more", L("Повышенная", "Higher")]], p.ui_contrast || ""), { cls: "te-row-wide" }))
      + section(L("Движение", "Motion"),
        row(L("Анимации", "Animations"), seg("p:ui_motion", [["full", L("Все", "All")], ["reduced", L("Меньше", "Fewer")], ["off", L("Нет", "None")]], p.ui_motion || "full"), { cls: "te-row-wide" })
        + row(L("Скорость", "Speed"), seg("p:ui_motion_speed", [["slow", L("Медленнее", "Slower")], ["", L("Обычная", "Default")], ["fast", L("Быстрее", "Faster")]], p.ui_motion_speed || ""), { cls: "te-row-wide" }))
      + section(L("Цвета темы", "Theme colours"), THEME_TOKENS.map(([name, ru, en]) => row(L(ru, en), colorBtn("tok:" + name, tokens[name] || "", `var(${name})`), { set: !!tokens[name], reset: "tok:" + name, hint: name })).join(""),
        { desc: m === "dark" ? L("Поверх выбранной тёмной темы. Сохраните как свою тему во вкладке «Темы», чтобы делиться ею.", "On top of the chosen dark theme. Save it as your own theme on the Themes tab to share it.")
          : L("Поверх выбранной светлой темы. Сохраните как свою тему во вкладке «Темы», чтобы делиться ею.", "On top of the chosen light theme. Save it as your own theme on the Themes tab to share it."),
          aside: resetAside("tokens", Object.keys(tokens).length > 0) });
  }
  const resetAside = (key, on) => `<button type="button" class="te-link" data-reset="${key}" ${on ? "" : "disabled"}>${I("reset")}${T("Сбросить", "Reset")}</button>`;

  function windowMap() {
    const lk = look(), lay = lk.layout, hidden = new Set(lk.toolbar.hidden);
    return `<div class="te-map" aria-hidden="true" data-side="${lay.side_pos}" data-sidemode="${ED.draft.sidebar ? lay.side_mode : "hidden"}" data-topbar="${lay.topbar || "default"}" data-composer="${lay.composer || "default"}" data-bubbles="${lay.bubbles || "default"}" data-ualign="${lay.user_align || "right"}">
        <div class="te-map-side"><i></i><i></i><i></i><i></i></div>
        <div class="te-map-main"><div class="te-map-top">${hidden.has("model") ? "" : "<i></i>"}<b></b></div>
          <div class="te-map-chat"><span class="u"></span><span class="a"></span><span class="a s"></span><span class="u"></span></div>
          <div class="te-map-comp"><i></i></div></div></div>`;
  }
  function toolbarList() {
    const lk = look();
    const order = lk.toolbar.order.length ? lk.toolbar.order.filter((id) => TOOLBAR.some((t) => t.id === id)) : TOOLBAR.map((t) => t.id);
    const full = order.concat(TOOLBAR.map((t) => t.id).filter((id) => !order.includes(id)));
    const hidden = new Set(lk.toolbar.hidden);
    return `<ol class="te-order" data-order>${full.map((id) => {
      const t = TOOLBAR.find((x) => x.id === id);
      const off = hidden.has(id);
      return `<li class="te-order-item ${t.spacer ? "is-spacer" : ""} ${off ? "is-off" : ""}" data-item="${id}" tabindex="0" data-fk="o:${id}" aria-label="${esc(L(t.ru, t.en))}">
        <span class="te-grip" data-grip title="${T("Перетащите (или Alt+↑/↓)", "Drag (or Alt+↑/↓)")}">${I("grip")}</span>
        <span class="te-order-name">${esc(L(t.ru, t.en))}</span>
        ${t.spacer ? `<small>${T("делит панель", "splits the bar")}</small>` : `<button type="button" class="te-eye" data-switch="h:${id}" data-fk="sw:h:${id}" aria-pressed="${!off}" title="${off ? T("Показать", "Show") : T("Скрыть", "Hide")}" aria-label="${off ? T("Показать", "Show") : T("Скрыть", "Hide")}">${I(off ? "eyeOff" : "eye")}</button>`}
      </li>`;
    }).join("")}</ol>`;
  }
  const shown = (id) => !look().toolbar.hidden.includes(id);
  function zonesSection() {
    const P = window.MemuPanelsLayout;
    if (!P || typeof P.zones !== "function") return "";
    const zones = P.zones();
    const rows = zones.map((z) => {
      const meta = P.ZONES[z.id] || {};
      const st = (ED.draft.zones && ED.draft.zones[z.id]) || z;
      const parts = [];
      if ((meta.views || []).length > 1) parts.push(seg(`z:view:${z.id}`, meta.views.map((v) => [v, v === "cards" ? L("Карточки", "Cards") : L("Значки", "Chips")]), st.view));
      if (meta.chats) parts.push(`<label class="te-inline">${toggle(`z:chats:${z.id}`, st.chats, L("И в чатах", "In chats too"))}<span>${T("И в чатах", "In chats too")}</span></label>`);
      const widthRow = meta.width ? slider(`z:width:${z.id}`, st.width || 280, 200, 480, "px", { label: L("Ширина", "Width") }) : "";
      return `<div class="te-zone"><div class="te-zone-h"><b>${esc(z.name)}</b><small>${z.count ? `${z.count} ${isEn() ? (z.count === 1 ? "panel" : "panels") : plural(z.count, "панель", "панели", "панелей")}` : T("пусто", "empty")}</small></div>
        ${parts.length ? `<div class="te-zone-c">${parts.join("")}</div>` : ""}${widthRow}</div>`;
    }).join("");
    return section(L("Панели на главной", "Home panels"), `<div class="te-zones">${rows}</div>
      <button type="button" class="te-btn ghost wide" data-act="panels-edit">${I("layout")}<span>${T("Переставить панели мышью…", "Rearrange panels with the mouse…")}</span></button>`,
      { desc: L("Вид каждого места. Сами панели переносятся в режиме «Изменить расположение».", "How each place looks. The panels themselves move in “Edit layout” mode.") });
  }
  function layoutTab() {
    const lk = look(), lay = lk.layout;
    const sideview = !ED.draft.sidebar ? "hidden" : lay.side_mode;
    const chatw = +lay.chat_px ? "custom" : (ED.draft.prefs.chat_width || "comfortable");
    return `${windowMap()}`
      + section(L("Боковая панель", "Sidebar"),
        row(L("Сторона", "Side"), seg("l:side_pos", [["left", L("Слева", "Left")], ["right", L("Справа", "Right")]], lay.side_pos), { cls: "te-row-wide" })
        + row(L("Вид", "View"), seg("sideview", [["full", L("Полная", "Full")], ["compact", L("Значки", "Icons")], ["hidden", L("Скрыта", "Hidden")]], sideview), { cls: "te-row-wide", hint: sideview === "compact" ? L("раскрывается при наведении", "opens on hover") : "" })
        + (sideview === "full" ? row(L("Ширина", "Width"), slider("l:side_width", +lay.side_width || Math.round(doc.querySelector(".side")?.getBoundingClientRect().width || 264), 200, 480, "px", { label: L("Ширина", "Width") }), { cls: "te-row-wide", set: !!+lay.side_width, reset: "l:side_width" }) : "")
        + `<div class="te-checks">${[["brand", "Логотип", "Logo"], ["modes", "Чат · Work · MemuCode", "Chat · Work · MemuCode"], ["sched", "Планирование", "Schedules"], ["skills", "Скиллы ИИ", "AI skills"], ["status", "Статус Proxy", "Proxy status"]]
          .map(([id, ru, en]) => `<label class="te-inline">${toggle("h:" + id, shown(id), L(ru, en))}<span>${T(ru, en)}</span></label>`).join("")}</div>`)
      + section(L("Верхняя панель", "Top bar"),
        row(L("Стиль", "Style"), seg("l:topbar", [["", L("Обычная", "Default")], ["slim", L("Тонкая", "Slim")], ["bare", L("Без фона", "Bare")], ["card", L("Карточка", "Card")]], lay.topbar), { cls: "te-row-wide" })
        + `<div class="te-subhead">${T("Кнопки — порядок и видимость", "Buttons — order and visibility")}${lk.toolbar.order.length || lk.toolbar.hidden.some((h) => TOOLBAR.some((t) => t.id === h)) ? `<button type="button" class="te-link" data-reset="toolbar">${I("reset")}${T("Сбросить", "Reset")}</button>` : ""}</div>${toolbarList()}`)
      + section(L("Поле ввода", "Message box"),
        row(L("Стиль", "Style"), seg("l:composer", [["", L("Обычное", "Default")], ["float", L("Парящее", "Floating")], ["docked", L("Во всю ширину", "Full width")], ["minimal", L("Линия", "Line")]], lay.composer, 'data-wrap="2"'), { cls: "te-row-wide" })
        + row(L("Ширина", "Width"), seg("cwmode", [["auto", L("Как у диалога", "Like the chat")], ["custom", L("Своя", "Custom")]], +lay.composer_width ? "custom" : "auto"), { cls: "te-row-wide" })
        + (+lay.composer_width ? row("", slider("l:composer_width", +lay.composer_width, 420, 1400, "px", { label: L("Ширина поля", "Box width"), step: 10 }), { cls: "te-row-wide" }) : "")
        + `<div class="te-checks">${[["mic", "Микрофон", "Microphone"], ["live", "Голосовой Live", "Live voice"], ["hint", "Подпись «MemuAI может ошибаться»", "“MemuAI can make mistakes” caption"]]
          .map(([id, ru, en]) => `<label class="te-inline">${toggle("h:" + id, shown(id), L(ru, en))}<span>${T(ru, en)}</span></label>`).join("")}</div>`)
      + section(L("Диалог", "Conversation"),
        row(L("Ширина колонки", "Column width"), seg("chatw", [["comfortable", L("Для чтения", "Comfortable")], ["wide", L("Широкая", "Wide")], ["custom", L("Своя", "Custom")]], chatw), { cls: "te-row-wide" })
        + (chatw === "custom" ? row("", slider("l:chat_px", +lay.chat_px, 520, 1600, "px", { label: L("Ширина колонки", "Column width"), step: 10 }), { cls: "te-row-wide" }) : "")
        + row(L("Сообщения", "Messages"), seg("l:bubbles", [["", L("Пузыри", "Bubbles")], ["plain", L("Без пузырей", "Plain")], ["outline", L("Контур", "Outline")], ["filled", L("Заливка", "Filled")], ["compact", L("Компактно", "Compact")]], lay.bubbles, 'data-wrap="1"'), { cls: "te-row-wide" })
        + row(L("Ваши сообщения", "Your messages"), seg("l:user_align", [["", L("Справа", "Right")], ["left", L("Слева", "Left")]], lay.user_align), { cls: "te-row-wide" }))
      + zonesSection();
  }

  const PRESETS = [
    { id: "minimal", ru: "Минимализм", en: "Minimal", dru: "Только диалог: без пузырей, тихая верхняя панель", den: "Just the conversation: no bubbles, a quiet top bar",
      prefs: { ui_design: "focus", ui_density: "spacious" }, layout: { topbar: "bare", bubbles: "plain", composer: "float" }, sw: ["var(--bg)", "var(--text)", "var(--accent)"] },
    { id: "aurora-glass", ru: "Стекло и сияние", en: "Glass aurora", dru: "Матовые панели над живым градиентом", den: "Frosted panels over a living gradient",
      prefs: { ui_design: "glass", accent_color: "violet", ui_glass: "strong" }, layout: { topbar: "card", composer: "float" }, bg: { kind: "gradient", preset: "aurora", canvas: 18 }, sw: ["#6b5bd6", "#c46fb4", "#1d1a33"] },
    { id: "work", ru: "Рабочий компакт", en: "Work compact", dru: "Больше текста на экране, боковая панель — значки", den: "More on screen, the sidebar as icons",
      prefs: { ui_density: "compact", ui_msg_size: "sm", chat_width: "wide" }, layout: { side_mode: "compact", topbar: "slim", bubbles: "compact" }, sw: ["var(--panel)", "var(--muted)", "var(--accent)"] },
    { id: "terminal", ru: "Консоль", en: "Console", dru: "Моноширинный шрифт, фосфорный акцент", den: "Monospace type, a phosphor accent",
      prefs: { ui_design: "terminal", accent_color: "forest", app_font: "mono" }, layout: { bubbles: "plain", topbar: "slim" }, theme: "dark", sw: ["#0d120e", "#97d49b", "#3c5a3e"] },
    { id: "paper", ru: "Бумага", en: "Paper", dru: "Светлая, книжный шрифт, контурные сообщения", den: "Light, a book typeface, outlined messages",
      prefs: { ui_design: "classic", accent_color: "amber", app_font: "serif" }, layout: { bubbles: "outline", composer: "float" }, theme: "light", sw: ["#fcfcfb", "#875710", "#1f1e1c"] },
    { id: "messenger", ru: "Мессенджер", en: "Messenger", dru: "Яркие пузыри и мягкие формы", den: "Bright bubbles and soft shapes",
      prefs: { ui_design: "soft", accent_color: "blue", ui_radius: "round" }, layout: { bubbles: "filled", composer: "float" }, sw: ["#2360bc", "#90baff", "var(--panel)"] },
    { id: "brutal", ru: "Плакат", en: "Poster", dru: "Жёсткие контуры, плоские цвета", den: "Hard outlines, flat colour",
      prefs: { ui_design: "brutal", accent_color: "crimson", ui_radius: "sharp" }, layout: { topbar: "card" }, sw: ["#b03636", "#1f1e1c", "#f4a09a"] },
    { id: "mirror", ru: "Зеркально", en: "Mirrored", dru: "Боковая панель справа, ваши сообщения слева", den: "Sidebar on the right, your messages on the left",
      prefs: {}, layout: { side_pos: "right", user_align: "left" }, keep: true, sw: ["var(--bg-deep)", "var(--panel)", "var(--accent)"] },
  ];
  const DEFAULT_PREFS = { accent_color: "terracotta", surface_dark: "", surface_light: "", ui_design: "classic", app_font: "serif", font_scale: 100, ui_density: "",
    chat_width: "comfortable", ui_radius: "", ui_glass: "", ui_msg_size: "", ui_contrast: "", ui_motion: "full", ui_motion_speed: "" };

  async function api(path, body) {
    const resp = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      let msg = (isEn() ? data.error_en : data.error) || data.error || `HTTP ${resp.status}`;
      if (isEn() && /[А-Яа-яЁё]/.test(msg)) msg = `The request failed (HTTP ${resp.status})`;
      throw new Error(msg);
    }
    return data;
  }
  const customs = () => ((catalog() && catalog().custom) || []);
  function currentThemeInfo() {
    const m = curMode(), ref = ED.draft.prefs[`surface_${m}`] || "";
    const cat = catalog();
    if (ref.startsWith("custom:")) { const c = customs().find((x) => x.ref === ref); if (c) return { name: c.name, custom: c, preview: c.preview || {} }; }
    const v = cat && (cat.variants[m] || []).find((x) => x.id === ref);
    return { name: v ? pick2(v) : L("По умолчанию", "Default"), custom: null, preview: (v && v.preview) || {} };
  }
  const swatchRow = (p) => `<span class="te-swatches" aria-hidden="true">${["--bg", "--panel", "--panel-2", "--text", "--accent"].map((k) => `<i style="background:${esc(p[k] || `var(${k})`)}"></i>`).join("")}</span>`;
  ED.tlook = true; ED.ilook = true; ED.renaming = ""; ED.deleting = "";

  function themesTab() {
    const info = currentThemeInfo();
    const m = curMode();
    const dirty = changes() > 0;
    const mine = customs();
    const list = mine.length ? `<div class="te-tlist">${mine.map((c) => {
      const active = (ED.draft.prefs[`surface_${c.base}`] || "") === c.ref;
      if (ED.renaming === c.id) {
        return `<div class="te-trow editing" data-tid="${esc(c.id)}">${swatchRow(c.preview || {})}<input class="te-input" data-fk="rename" value="${esc(c.name)}" maxlength="80" aria-label="${T("Новое название", "New name")}">
          <button type="button" class="te-btn small primary" data-act="theme-rename-ok">${T("Готово", "Done")}</button><button type="button" class="te-btn small ghost" data-act="theme-rename-no">${T("Отмена", "Cancel")}</button></div>`;
      }
      if (ED.deleting === c.id) {
        return `<div class="te-trow confirm" data-tid="${esc(c.id)}"><span>${T("Удалить тему «", "Delete “")}${esc(c.name)}${T("» без возможности восстановления?", "” permanently?")}</span>
          <button type="button" class="te-btn small danger" data-act="theme-delete-ok">${T("Удалить", "Delete")}</button><button type="button" class="te-btn small ghost" data-act="theme-delete-no">${T("Отмена", "Cancel")}</button></div>`;
      }
      return `<div class="te-trow ${active ? "on" : ""}" data-tid="${esc(c.id)}">${swatchRow(c.preview || {})}
        <div class="te-tmeta"><b>${esc(c.name)}</b><small>${c.base === "light" ? T("светлая", "light") : T("тёмная", "dark")}${c.author ? " · " + esc(c.author) : ""}</small></div>
        ${active ? `<span class="te-tag-on">${I("check")}${T("Выбрана", "In use")}</span>` : `<button type="button" class="te-btn small ghost" data-act="theme-use">${T("Применить", "Use")}</button>`}
        <button type="button" class="te-tbtn" data-act="theme-more" aria-haspopup="menu" aria-label="${T("Ещё", "More")}" title="${T("Переименовать, дублировать, экспорт, удалить", "Rename, duplicate, export, delete")}">${I("more")}</button></div>`;
    }).join("")}</div>` : `<p class="te-desc">${T("Своих тем пока нет — сохраните текущее оформление или импортируйте тему.", "No custom themes yet — save the current look or import one.")}</p>`;
    return section(L("Сейчас", "Current look"), `<div class="te-current">${swatchRow(info.preview)}<div class="te-tmeta"><b>${esc(info.name)}</b><small>${m === "dark" ? T("тёмная тема", "dark theme") : T("светлая тема", "light theme")}${dirty ? T(" · с изменениями", " · with changes") : ""}</small></div></div>
        <div class="te-saveform">
          <input class="te-input" data-fk="tname" id="te-tname" maxlength="80" placeholder="${T("Название новой темы", "New theme name")}" value="${esc(ED.tname || "")}">
          <button type="button" class="te-btn primary small" data-act="theme-save-new">${I("plus")}<span>${T("Сохранить как тему", "Save as theme")}</span></button>
        </div>
        <label class="te-inline">${toggle("tlook", ED.tlook, L("Вместе с дизайном и расположением", "Include design and layout"))}<span>${T("Вместе с дизайном и расположением", "Include design and layout")}</span></label>
        <div class="te-actions">
          ${info.custom ? `<button type="button" class="te-btn ghost small" data-act="theme-update">${I("check")}<span>${T("Обновить «", "Update “")}${esc(info.custom.name)}${T("»", "”")}</span></button>` : ""}
          <button type="button" class="te-btn ghost small" data-act="export-current">${I("download")}<span>${T("Экспорт в файл", "Export to file")}</span></button>
          <button type="button" class="te-btn ghost small" data-act="code-current">${I("copy")}<span>${T("Копировать код", "Copy share code")}</span></button>
        </div>`, { desc: L("Тема хранит цвета и стили элементов; с флажком — ещё дизайн, шрифт и расположение.", "A theme keeps colours and element styles; with the switch on — the design, font and layout too.") })
      + section(L("Мои темы", "My themes"), list)
      + section(L("Импорт", "Import"), `<div class="te-import">
          <textarea class="te-input te-code" data-fk="icode" id="te-icode" rows="3" spellcheck="false" placeholder="${T("Вставьте код MEMU-THEME-1… или содержимое .css / .json", "Paste a MEMU-THEME-1… code or a .css / .json file")}">${esc(ED.icode || "")}</textarea>
          <div class="te-actions">
            <button type="button" class="te-btn primary small" data-act="import-code">${T("Импортировать", "Import")}</button>
            <button type="button" class="te-btn ghost small" data-act="import-file">${I("upload")}<span>${T("Из файла…", "From a file…")}</span></button>
            <input type="file" hidden data-file="theme" accept=".json,.css,.memutheme,application/json,text/css">
          </div>
          <label class="te-inline">${toggle("ilook", ED.ilook, L("Применить и дизайн с расположением, если они есть", "Also apply its design and layout, if any"))}<span>${T("Применить и дизайн с расположением, если они есть", "Also apply its design and layout, if any")}</span></label></div>`)
      + section(L("Готовые стили", "Presets"), `<div class="te-presets">${PRESETS.map((p) => `<button type="button" class="te-preset" data-preset="${p.id}" data-fk="pr:${p.id}">
          <span class="te-preset-sw" aria-hidden="true">${p.sw.map((c) => `<i style="background:${esc(c)}"></i>`).join("")}</span>
          <b>${esc(L(p.ru, p.en))}</b><small>${esc(L(p.dru, p.den))}</small></button>`).join("")}</div>`, { desc: L("Применяются к черновику — можно отменить или доработать.", "They change the draft — undo or fine-tune them.") })
      + section(L("Сброс", "Reset"), `<button type="button" class="te-btn ghost wide danger-text" data-act="reset-all">${I("reset")}<span>${T("Вернуть оформление по умолчанию", "Back to the default look")}</span></button>`,
        { desc: L("Тема, акцент, дизайн, расположение и стили элементов — как после установки. Можно отменить.", "Theme, accent, design, layout and element styles as after installing. Can be undone.") });
  }
  function hydrateThemes() {
    const input = ED.panel && ED.panel.querySelector('[data-fk="rename"]');
    if (input) { input.focus(); input.select(); }
  }

  function lookForPackage(lk) {
    const out = clone(lk);
    out.elements = {}; out.tokens = { dark: {}, light: {} };
    return out;
  }
  function prefsForPackage(p) {
    const out = {};
    for (const key of ["ui_design", "accent_color", "app_font", "font_scale", "ui_density", "chat_width", "ui_radius", "ui_glass", "ui_msg_size", "ui_contrast"]) out[key] = p[key];
    return out;
  }
  async function basePackage(ref) {
    if (ref && ref.startsWith("custom:")) {
      try { return (await api("/api/pg/theme-editor/package", { id: ref.slice(7) })).package; } catch (_) {  }
    }
    return { tokens: {}, elements: {}, css: "", extends: ref && !ref.startsWith("custom:") ? ref : "" };
  }

  async function currentPackage(name, withLook) {
    const m = curMode(), lk = look();
    const ref = ED.draft.prefs[`surface_${m}`] || "";
    const base = await basePackage(ref);
    const elements = clone(base.elements || {});
    for (const [id, spec] of Object.entries(lk.elements)) {
      const e = elements[id] || {};
      if (spec.all) e.all = { ...(e.all || {}), ...spec.all };
      if (spec[m]) e[m] = { ...(e[m] || {}), ...spec[m] };
      if (Object.keys(e).length) elements[id] = e;
    }
    return {
      format: "memuai-theme", schema: 1, name: name || L("Моя тема", "My theme"), base: m, extends: base.extends || "",
      author: base.author || "", description: base.description || "",
      tokens: { ...(base.tokens || {}), ...(lk.tokens[m] || {}) }, elements, css: base.css || "",
      look: withLook ? lookForPackage(lk) : null, prefs: withLook ? prefsForPackage(ED.draft.prefs) : {},
    };
  }

  function themeCss(pkg) {
    const rows = Object.entries(pkg.tokens || {}).filter(([k, v]) => /^--[a-z][a-z0-9-]*$/.test(k) && safe(v)).map(([k, v]) => `  ${k}: ${v};`);
    let css = rows.length ? `:root {\n${rows.join("\n")}\n}\n` : "";
    const rules = elementsCss(pkg.elements || {}, pkg.base, ":root");
    if (rules) css += `\n/* memu-editor:elements */\n${rules}/* /memu-editor:elements */\n`;
    if (pkg.css) css += `\n${pkg.css.trim()}\n`;
    return css;
  }
  async function saveThemeFile(pkg, id) {
    const result = await api("/api/pg/theme-editor/theme", { id: id || undefined, name: pkg.name, base: pkg.base, extends: pkg.extends, author: pkg.author, description: pkg.description, css: themeCss(pkg), package: pkg });
    if (MA()) await MA().load();
    return result.theme;
  }

  function adoptTheme(theme) {
    change((d) => {
      const m = theme.base;
      d.theme = m;
      d.prefs[`surface_${m}`] = theme.ref;
      d.prefs.ui_look.tokens[m] = {};
      for (const spec of Object.values(d.prefs.ui_look.elements)) delete spec[m];
    }, "", {});
  }
  async function saveAsTheme(update) {
    const info = currentThemeInfo();
    const input = ED.panel.querySelector("#te-tname");
    const name = update ? info.custom.name : (input && input.value.trim()) || L("Моя тема", "My theme");
    try {
      const pkg = await currentPackage(name, ED.tlook);
      const theme = await saveThemeFile(pkg, update ? info.custom.id : "");
      ED.tname = "";
      adoptTheme(theme);
      note(update ? L(`Тема «${theme.name}» обновлена`, `Theme “${theme.name}” updated`) : L(`Тема «${theme.name}» сохранена в «Мои темы»`, `Theme “${theme.name}” saved to “My themes”`));
    } catch (error) { note(error.message, "err"); }
  }
  async function exportPackage(pkg) {
    try {
      const out = (await api("/api/pg/theme-editor/export", { package: pkg })).package;
      const file = `${(pkg.name || "memuai-theme").replace(/[\\/:*?"<>|]+/g, " ").trim() || "memuai-theme"}.memutheme.json`;
      if (typeof downloadTextFile === "function") await downloadTextFile(file, JSON.stringify(out, null, 2), "application/json");
    } catch (error) { note(error.message, "err"); }
  }
  async function copyCode(pkg) {
    try {
      const out = await api("/api/pg/theme-editor/share", { package: pkg });
      if (typeof copyText === "function") await copyText(out.code, false);
      else await navigator.clipboard.writeText(out.code);
      note(out.dropped_image ? L("Код темы скопирован. Картинка фона в код не входит — для неё используйте экспорт в файл.", "Share code copied. The background picture is not in the code — export to a file to keep it.")
        : L("Код темы скопирован — отправьте его, и тему можно будет импортировать", "Share code copied — send it and the theme can be imported"));
    } catch (error) { note(error.message, "err"); }
  }
  async function importText(text) {
    try {
      const pkg = (await api("/api/pg/theme-editor/parse", { text })).package;
      if (!pkg.name) pkg.name = L("Импортированная тема", "Imported theme");
      const theme = await saveThemeFile(pkg, "");
      const withLook = ED.ilook && (pkg.look || Object.keys(pkg.prefs || {}).length);
      change((d) => {
        d.theme = theme.base;
        d.prefs[`surface_${theme.base}`] = theme.ref;
        if (withLook) {
          Object.assign(d.prefs, pkg.prefs || {});
          if (pkg.look) {
            const lk = d.prefs.ui_look;
            lk.layout = { ...DEFAULT_LAYOUT, ...pkg.look.layout };
            lk.toolbar = clone(pkg.look.toolbar);
            lk.bg = { ...DEFAULT_BG, ...pkg.look.bg };
          }
        }
      });
      ED.icode = "";
      render();
      note(L(`Тема «${theme.name}» импортирована и показана — сохраните, чтобы оставить её`, `Theme “${theme.name}” imported and previewed — save to keep it`));
    } catch (error) { note(error.message, "err"); }
  }
  function themeMenu(anchor, id) {
    const theme = customs().find((c) => c.id === id);
    if (!theme || typeof showContextMenu !== "function") return;
    const r = anchor.getBoundingClientRect();
    const pkgOf = async () => (await api("/api/pg/theme-editor/package", { id })).package;
    showContextMenu([
      { label: L("Переименовать", "Rename"), icon: "edit", run: () => { ED.renaming = id; render(); } },
      { label: L("Дублировать", "Duplicate"), icon: "copy", run: async () => {
        try { await api("/api/pg/themes", { name: theme.name + L(" (копия)", " (copy)"), base: theme.base, extends: theme.extends, css: theme.css }); if (MA()) await MA().load(); render(); }
        catch (error) { note(error.message, "err"); }
      } },
      { label: L("Экспорт в файл", "Export to file"), icon: "download", run: async () => { try { exportPackage(await pkgOf()); } catch (error) { note(error.message, "err"); } } },
      { label: L("Копировать код", "Copy share code"), icon: "clipboard", run: async () => { try { copyCode(await pkgOf()); } catch (error) { note(error.message, "err"); } } },
      { sep: true },
      { label: L("Удалить", "Delete"), icon: "trash", danger: true, run: () => { ED.deleting = id; render(); } },
    ], r.left, r.bottom + 4);
    doc.querySelector("#context-menu")?.classList.add("te-pop");
  }
  async function renameTheme(id, name) {
    const theme = customs().find((c) => c.id === id);
    ED.renaming = "";
    if (!theme || !name.trim() || name.trim() === theme.name) { render(); return; }
    try { await api("/api/pg/themes", { id, name: name.trim(), base: theme.base, extends: theme.extends, css: theme.css }); if (MA()) await MA().load(); }
    catch (error) { note(error.message, "err"); }
    render();
  }
  async function deleteTheme(id) {
    const theme = customs().find((c) => c.id === id);
    ED.deleting = "";
    if (!theme) return;
    try {
      await api("/api/pg/themes/delete", { id });
      const key = `surface_${theme.base}`;

      if (ED.orig.prefs[key] === theme.ref) { ED.orig.prefs[key] = ""; if (typeof savePreferences === "function") savePreferences({ [key]: "" }); }
      for (const st of [ED.draft, ...ED.past, ...ED.future]) if (st.prefs[key] === theme.ref) st.prefs[key] = "";
      if (MA()) await MA().load();
      applyState(ED.draft);
      note(L("Тема удалена", "Theme deleted"));
    } catch (error) { note(error.message, "err"); }
    render();
  }
  function applyPreset(id) {
    const p = PRESETS.find((x) => x.id === id);
    if (!p) return;
    change((d) => {
      const lk = d.prefs.ui_look;
      if (!p.keep) { lk.layout = { ...DEFAULT_LAYOUT, side_width: lk.layout.side_width }; lk.bg = { ...DEFAULT_BG }; d.sidebar = true; }
      Object.assign(lk.layout, p.layout || {});
      if (p.bg) Object.assign(lk.bg, p.bg);
      Object.assign(d.prefs, p.prefs || {});
      if (p.theme) d.theme = p.theme;
    });
    note(L(`Стиль «${p.ru}» применён — Ctrl+Z отменит`, `“${p.en}” applied — Ctrl+Z undoes it`));
  }

  function setElementProp(prop, value, key, opts) {
    const id = ED.sel && ED.sel.id;
    if (!id) return;
    change((d) => {
      const lk = d.prefs.ui_look;
      const spec = lk.elements[id] || (lk.elements[id] = {});
      const part = COLOR_PROPS.has(prop) ? d.theme : "all";
      const bucket = spec[part] || (spec[part] = {});
      if (value === "" || value === null || value === undefined) delete bucket[prop]; else bucket[prop] = value;

      if (prop === "alpha" && value !== "" && !(spec[d.theme] || {}).bg) {
        const now = resolve(computedOf(ED.sel.node || visibleNode(EL[id]), "bg"));
        if (now) (spec[d.theme] || (spec[d.theme] = {})).bg = now;
      }
    }, key, opts || { render: !RANGES[prop] });
  }
  function onSeg(key, v) {
    if (key === "theme") return change((d) => { d.theme = v === "light" ? "light" : "dark"; });
    if (key === "sideview") return change((d) => { d.sidebar = v !== "hidden"; if (v !== "hidden") d.prefs.ui_look.layout.side_mode = v; });
    if (key === "chatw") return change((d) => {
      const lay = d.prefs.ui_look.layout;
      if (v === "custom") lay.chat_px = +lay.chat_px || (d.prefs.chat_width === "wide" ? 1080 : 900);
      else { lay.chat_px = 0; d.prefs.chat_width = v; }
    });
    if (key === "cwmode") return change((d) => { const lay = d.prefs.ui_look.layout; lay.composer_width = v === "custom" ? (+lay.composer_width || 720) : 0; });
    if (key === "bg:kind") return change((d) => { d.prefs.ui_look.bg.kind = v; });
    if (key.startsWith("p:")) return change((d) => { d.prefs[key.slice(2)] = key === "p:font_scale" ? Number(v) : v; });
    if (key.startsWith("l:")) return change((d) => { d.prefs.ui_look.layout[key.slice(2)] = v; });
    if (key.startsWith("el:")) return setElementProp(key.slice(3), v, "");
    if (key.startsWith("z:view:")) return change((d) => { const id = key.slice(7); d.zones[id] = { ...(d.zones[id] || {}), view: v }; });
  }
  function onRange(key, raw, input) {
    const v = Number(raw);
    const out = input && input.parentElement.querySelector("output");
    const unit = key === "bg:angle" ? "°" : /alpha|opacity|font_scale|dim|canvas/.test(key) ? "%" : "px";
    if (out) out.textContent = `${v}${unit}`;
    if (key.startsWith("el:")) return setElementProp(key.slice(3), v, key);
    const opts = { render: false };
    if (key.startsWith("l:")) return change((d) => { d.prefs.ui_look.layout[key.slice(2)] = v; }, key, opts);
    if (key.startsWith("p:")) return change((d) => { d.prefs[key.slice(2)] = v; }, key, opts);
    if (key.startsWith("bg:")) return change((d) => { d.prefs.ui_look.bg[key.slice(3)] = v; }, key, opts);
    if (key.startsWith("z:width:")) return change((d) => { const id = key.slice(8); d.zones[id] = { ...(d.zones[id] || {}), width: v }; }, key, opts);
  }
  function onSwitch(key, btn) {
    if (key === "tlook" || key === "ilook") { ED[key] = !ED[key]; btn.setAttribute("aria-checked", String(ED[key])); return; }
    if (key.startsWith("h:")) {
      const id = key.slice(2);
      return change((d) => { const h = d.prefs.ui_look.toolbar.hidden; const i = h.indexOf(id); if (i >= 0) h.splice(i, 1); else h.push(id); });
    }
    if (key.startsWith("z:chats:")) return change((d) => { const id = key.slice(8); const z = d.zones[id] || {}; d.zones[id] = { ...z, chats: !z.chats }; });
  }
  function onReset(key) {
    if (key.startsWith("el:")) return setElementProp(key.slice(3), "", "");
    if (key.startsWith("tok:")) return change((d) => { delete d.prefs.ui_look.tokens[d.theme][key.slice(4)]; });
    if (key === "tokens") return change((d) => { d.prefs.ui_look.tokens[d.theme] = {}; });
    if (key === "bg") return change((d) => { d.prefs.ui_look.bg = { ...DEFAULT_BG }; });
    if (key === "toolbar") return change((d) => { d.prefs.ui_look.toolbar.order = []; d.prefs.ui_look.toolbar.hidden = d.prefs.ui_look.toolbar.hidden.filter((h) => !TOOLBAR.some((t) => t.id === h)); });
    if (key.startsWith("l:")) return change((d) => { d.prefs.ui_look.layout[key.slice(2)] = DEFAULT_LAYOUT[key.slice(2)]; });
    if (key.startsWith("p:")) return change((d) => { d.prefs[key.slice(2)] = DEFAULT_PREFS[key.slice(2)]; });
  }

  function setColor(key, value) {
    const opts = { render: false };
    if (key.startsWith("el:")) setElementProp(key.slice(3), value, key, opts);
    else if (key.startsWith("tok:")) change((d) => { const t = d.prefs.ui_look.tokens[d.theme]; if (value) t[key.slice(4)] = value; else delete t[key.slice(4)]; }, key, opts);
    else if (key.startsWith("bg:")) change((d) => { d.prefs.ui_look.bg[key.slice(3)] = value; }, key, opts);
    patchColor(key);
  }
  function patchColor(key) {
    const btn = ED.panel && ED.panel.querySelector(`[data-color="${CSS.escape(key)}"]`);
    if (!btn) return;
    const value = currentColor(key);
    btn.classList.toggle("set", !!value);
    if (value) btn.querySelector("i").style.setProperty("--c", value);
    btn.querySelector("span").textContent = colorLabel(value);
    const rowNode = btn.closest(".te-row");
    if (rowNode) {
      rowNode.classList.toggle("is-set", !!value);
      const reset = rowNode.querySelector(".te-reset");
      if (reset) reset.disabled = !value;
    }
  }

  function onPanelClick(e) {
    const panel = ED.panel;

    const t = { closest: (sel) => { const n = e.target.closest(sel); return n && panel && panel.contains(n) ? n : null; } };
    const tab = t.closest(".te-tabs [data-tab]");
    if (tab) return setTab(tab.dataset.tab);
    const segBtn = t.closest(".te-seg [data-v]");
    if (segBtn) return onSeg(segBtn.closest("[data-seg]").dataset.seg, segBtn.dataset.v);
    const sw = t.closest("[data-switch]");
    if (sw) return onSwitch(sw.dataset.switch, sw);
    const reset = t.closest("[data-reset]");
    if (reset) return onReset(reset.dataset.reset);
    const color = t.closest("[data-color]");
    if (color) return openColor(color.dataset.color, color);
    const v = t.closest("[data-variant]");
    if (v) return change((d) => { d.prefs[`surface_${d.theme}`] = v.dataset.variant; });
    const a = t.closest("[data-accent]");
    if (a) return change((d) => { d.prefs.accent_color = a.dataset.accent; });
    const ds = t.closest("[data-design]");
    if (ds) return change((d) => { d.prefs.ui_design = ds.dataset.design; });
    const bp = t.closest("[data-bgpreset]");
    if (bp) return change((d) => { d.prefs.ui_look.bg.preset = bp.dataset.bgpreset; });
    const elc = t.closest("[data-el]");
    if (elc) return select(elc.dataset.el);
    const pr = t.closest("[data-preset]");
    if (pr) return applyPreset(pr.dataset.preset);
    const act = t.closest("[data-act]");
    if (!act || act.disabled) return;
    const id = act.closest("[data-tid]") && act.closest("[data-tid]").dataset.tid;
    switch (act.dataset.act) {
      case "pick": ED.picking = !ED.picking; ED.hover = null; updateHead(); break;
      case "undo": undo(); break;
      case "redo": redo(); break;
      case "compare": if (e.detail === 0) compare(!ED.comparing); break;
      case "dock": dockMenu(act); break;
      case "cancel": requestCancel(); break;
      case "save": save(); break;
      case "desel": ED.sel = null; render(); break;
      case "reset-el": change((d) => { delete d.prefs.ui_look.elements[ED.sel.id]; }); break;
      case "fix-contrast": fixContrast(); break;
      case "mode-swap": change((d) => { d.theme = d.theme === "dark" ? "light" : "dark"; }); break;
      case "reveal": { const el = EL[ED.sel.id]; ED.picking = false; updateHead(); el.reveal && el.reveal(); setTimeout(() => { if (ED.sel) ED.sel.node = visibleNode(el); render(); }, 250); break; }
      case "panels-edit": if (window.MemuPanelsLayout) { ED.picking = false; updateHead(); MemuPanelsLayout.enter(); } break;
      case "bg-upload": ED.panel.querySelector('[data-file="bg"]')?.click(); break;
      case "import-file": ED.panel.querySelector('[data-file="theme"]')?.click(); break;
      case "import-code": { const text = ED.panel.querySelector("#te-icode")?.value || ""; importText(text); break; }
      case "theme-save-new": saveAsTheme(false); break;
      case "theme-update": saveAsTheme(true); break;
      case "export-current": currentPackage(L("Моя тема", "My theme"), true).then(exportPackage); break;
      case "code-current": currentPackage(L("Моя тема", "My theme"), true).then(copyCode); break;
      case "theme-use": { const th = customs().find((c) => c.id === id); if (th) change((d) => { d.theme = th.base; d.prefs[`surface_${th.base}`] = th.ref; }); break; }
      case "theme-more": e.stopPropagation(); themeMenu(act, id); break;
      case "theme-rename-ok": renameTheme(id, ED.panel.querySelector('[data-fk="rename"]').value); break;
      case "theme-rename-no": ED.renaming = ""; render(); break;
      case "theme-delete-ok": deleteTheme(id); break;
      case "theme-delete-no": ED.deleting = ""; render(); break;
      case "reset-all": change((d) => { Object.assign(d.prefs, clone(DEFAULT_PREFS)); d.prefs.ui_look = normLook({}); d.sidebar = true; }); break;
      case "confirm-stay": hideConfirm(); break;
      case "confirm-leave": cancel(); break;
      case "confirm-save": save(); break;
      default: break;
    }
  }
  function onPanelInput(e) {
    const t = e.target;
    if (t.dataset.range) onRange(t.dataset.range, t.value, t);
    else if (t.id === "te-tname") ED.tname = t.value;
    else if (t.id === "te-icode") ED.icode = t.value;
  }
  function onPanelChange(e) {
    const t = e.target;
    if (t.dataset.range) { ED.lastKey = ""; render(); return; }
    if (t.dataset.file && t.files && t.files[0]) {
      const file = t.files[0];
      t.value = "";
      if (t.dataset.file === "theme") {
        if (file.size > 4 * 1024 * 1024) { note(L("Файл темы слишком большой", "The theme file is too large"), "err"); return; }
        file.text().then(importText);
      } else uploadBg(file);
    }
  }
  async function uploadBg(file) {
    if (file.size > 6 * 1024 * 1024) { note(L("Картинка больше 6 МБ", "The picture is larger than 6 MB"), "err"); return; }
    try {
      const data = await new Promise((resolveData, reject) => { const r = new FileReader(); r.onload = () => resolveData(r.result); r.onerror = reject; r.readAsDataURL(file); });
      const out = await api("/api/pg/theme-editor/asset", { data });
      change((d) => { d.prefs.ui_look.bg.kind = "image"; d.prefs.ui_look.bg.image = out.url; });
    } catch (error) { note(error.message, "err"); }
  }
  function onPanelKey(e) {
    const item = e.target.closest && e.target.closest(".te-order-item");
    if (item && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
      e.preventDefault();
      moveToolbar(item.dataset.item, e.key === "ArrowUp" ? -1 : 1);
      return;
    }
    const segBtn = e.target.closest && e.target.closest(".te-seg [data-v]");
    if (segBtn && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
      e.preventDefault();
      const all = [...segBtn.parentElement.querySelectorAll("[data-v]")];
      const next = all[(all.indexOf(segBtn) + (e.key === "ArrowRight" ? 1 : -1) + all.length) % all.length];
      onSeg(segBtn.closest("[data-seg]").dataset.seg, next.dataset.v);
    }
    if (e.key === "Enter" && e.target.dataset.fk === "rename") { e.preventDefault(); renameTheme(e.target.closest("[data-tid]").dataset.tid, e.target.value); }
    if (e.key === "Enter" && e.target.id === "te-tname") { e.preventDefault(); saveAsTheme(false); }
  }
  function toolbarOrder() {
    const lk = look();
    const order = lk.toolbar.order.length ? lk.toolbar.order.filter((id) => TOOLBAR.some((t) => t.id === id)) : TOOLBAR.map((t) => t.id);
    return order.concat(TOOLBAR.map((t) => t.id).filter((id) => !order.includes(id)));
  }
  function moveToolbar(id, delta, toIndex) {
    const order = toolbarOrder();
    const from = order.indexOf(id);
    if (from < 0) return;
    order.splice(from, 1);
    const to = toIndex !== undefined ? clamp(toIndex, 0, order.length) : clamp(from + delta, 0, order.length);
    order.splice(to, 0, id);
    change((d) => { d.prefs.ui_look.toolbar.order = order; });
  }

  function onPanelPointerDown(e) {
    const cmp = e.target.closest('[data-act="compare"]');
    if (cmp && e.button === 0) {
      compare(true);
      const up = () => { compare(false); window.removeEventListener("pointerup", up, true); window.removeEventListener("pointercancel", up, true); };
      window.addEventListener("pointerup", up, true); window.addEventListener("pointercancel", up, true);
      return;
    }
    const grip = e.target.closest("[data-grip]");
    if (grip && e.button === 0) return dragRow(e, grip.closest(".te-order-item"));
    const head = e.target.closest("[data-drag]");
    if (head && e.button === 0 && !e.target.closest("button")) return dragPanel(e);
  }
  function dragPanel(e) {
    const panel = ED.panel;
    const start = { x: e.clientX, y: e.clientY };
    const rect = panel.getBoundingClientRect();
    let moving = false;
    const move = (ev) => {
      if (!moving && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) < 5) return;
      if (!moving) {
        moving = true;
        doc.body.classList.add("te-dragging");
        if (ED.ui.dock !== "float") { ED.ui.dock = "float"; ED.ui.x = rect.left; ED.ui.y = rect.top; placePanel(); window.dispatchEvent(new Event("resize")); }
      }
      ED.ui.x = rect.left + ev.clientX - start.x; ED.ui.y = rect.top + ev.clientY - start.y;
      placePanel();
    };
    const up = (ev) => {
      window.removeEventListener("pointermove", move, true); window.removeEventListener("pointerup", up, true);
      doc.body.classList.remove("te-dragging");
      if (!moving) return;

      if (ev.clientX > window.innerWidth - 24) setDock("right");
      else if (ev.clientX < 24) setDock("left");
      else { saveUi(); render(); }
    };
    window.addEventListener("pointermove", move, true); window.addEventListener("pointerup", up, true);
  }
  function dragRow(e, item) {
    e.preventDefault();
    const list = item.parentElement;
    const rows = [...list.children];
    const scroller = list.closest(".te-body") || list;
    const from = rows.indexOf(item);
    const h = item.getBoundingClientRect().height + 4;

    const top0 = list.getBoundingClientRect().top;
    const mids = rows.map((r) => { const b = r.getBoundingClientRect(); return b.top - top0 + b.height / 2; });
    const grab = e.clientY - item.getBoundingClientRect().top;
    let index = from, lastY = e.clientY, raf = 0, moved = false;
    try { item.setPointerCapture(e.pointerId); } catch (_) {  }
    const layout = () => {
      const listTop = list.getBoundingClientRect().top;

      const myTop = clamp(lastY - grab - listTop, -6, list.offsetHeight - item.offsetHeight + 6);
      const centre = myTop + item.offsetHeight / 2;
      item.style.transform = `translateY(${Math.round(myTop - (mids[from] - item.offsetHeight / 2))}px)`;
      let to = 0;
      for (let i = 0; i < rows.length; i++) if (i !== from && centre > mids[i]) to++;
      index = clamp(to, 0, rows.length - 1);
      rows.forEach((r, i) => {
        if (r === item) return;
        const shift = from < index ? (i > from && i <= index ? -h : 0) : (i < from && i >= index ? h : 0);
        r.style.transform = shift ? `translateY(${shift}px)` : "";
      });
    };

    const edge = () => {
      raf = 0;
      const box = scroller.getBoundingClientRect();
      const zone = 44;
      let dy = 0;
      if (lastY < box.top + zone) dy = -Math.ceil((box.top + zone - lastY) / 4);
      else if (lastY > box.bottom - zone) dy = Math.ceil((lastY - (box.bottom - zone)) / 4);

      const lb = list.getBoundingClientRect();
      if (dy < 0 && lb.top >= box.top + 8) dy = 0;
      if (dy > 0 && lb.bottom <= box.bottom - 8) dy = 0;
      if (dy) {
        const before = scroller.scrollTop;
        scroller.scrollTop = before + clamp(dy, -18, 18);
        if (scroller.scrollTop !== before) layout();
        raf = requestAnimationFrame(edge);
      }
    };
    const move = (ev) => {
      lastY = ev.clientY;
      if (!moved && Math.abs(ev.clientY - e.clientY) < 3) return;
      if (!moved) { moved = true; item.classList.add("is-dragging"); doc.body.classList.add("te-dragging"); }
      layout();
      if (!raf) raf = requestAnimationFrame(edge);
    };
    const up = () => {
      window.removeEventListener("pointermove", move, true); window.removeEventListener("pointerup", up, true); window.removeEventListener("pointercancel", up, true);
      cancelAnimationFrame(raf); raf = 0;
      try { item.releasePointerCapture(e.pointerId); } catch (_) {  }
      rows.forEach((r) => { r.style.transform = ""; });
      item.classList.remove("is-dragging"); doc.body.classList.remove("te-dragging");
      if (moved && index !== from) {
        const id = item.dataset.item;
        moveToolbar(id, 0, index);
        requestAnimationFrame(() => { const n = ED.panel && ED.panel.querySelector(`.te-order-item[data-item="${id}"]`); if (n) n.focus({ preventScroll: true }); });
      }
    };
    window.addEventListener("pointermove", move, true); window.addEventListener("pointerup", up, true); window.addEventListener("pointercancel", up, true);
  }

  function popover(anchor, html2, cls, onClose) {
    const pop = doc.createElement("div");
    pop.className = `te-pop ${cls || ""}`;
    pop.innerHTML = html2;
    doc.body.appendChild(pop);
    const close = () => { pop.remove(); anchor.setAttribute("aria-expanded", "false"); if (onClose) onClose(); };
    anchor.setAttribute("aria-expanded", "true");
    if (window.MemuLayers) { MemuLayers.open({ el: pop, anchor, close, kind: "te" }); MemuLayers.place(pop, anchor, { gap: 6, maxHeight: 460 }); }
    else { const r = anchor.getBoundingClientRect(); pop.style.cssText = `position:fixed;left:${r.left}px;top:${r.bottom + 6}px`; }
    return { pop, close: () => (window.MemuLayers ? MemuLayers.close(pop, "api") : close()) };
  }
  function dockMenu(anchor) {
    const items = [["right", "dockR", L("Справа", "Docked right")], ["left", "dockL", L("Слева", "Docked left")], ["float", "float", L("Плавающее окно", "Floating window")]];
    const { pop, close } = popover(anchor, `<div class="te-menu" role="menu">${items.map(([id, icon, label]) => `<button type="button" role="menuitemradio" aria-checked="${ED.ui.dock === id}" data-dock="${id}">${I(icon)}<span>${esc(label)}</span>${ED.ui.dock === id ? I("check", "te-mcheck") : ""}</button>`).join("")}</div>`, "te-pop-menu");
    pop.addEventListener("click", (e) => { const b = e.target.closest("[data-dock]"); if (b) { close(); setDock(b.dataset.dock); } });
    pop.querySelector(`[data-dock="${ED.ui.dock}"]`)?.focus();
    pop.addEventListener("keydown", (e) => { if (window.MemuLayers) MemuLayers.listKeys(pop, "button", e); });
  }
  const TOKEN_CHIPS = ["var(--accent)", "var(--accent-2)", "var(--accent-soft)", "var(--text)", "var(--muted)", "var(--bg)", "var(--bg-deep)", "var(--panel)", "var(--panel-2)", "var(--border)", "var(--danger)", "var(--ok)", "transparent"];
  function openColor(key, anchor) {
    const current = currentColor(key);
    const cat = catalog();
    const m = curMode();
    const hex = resolve(current || anchor.querySelector("i").style.getPropertyValue("--c")) || "#888888";
    const { pop, close } = popover(anchor, `<div class="te-cpop" role="dialog" aria-label="${T("Цвет", "Colour")}">
        <div class="te-cpop-h">${T("Цвета темы", "Theme colours")}<small>${T("меняются вместе с темой", "follow the theme")}</small></div>
        <div class="te-cgrid">${TOKEN_CHIPS.map((c) => `<button type="button" class="te-cchip ${current === c ? "on" : ""}" data-c="${esc(c)}" title="${esc(colorLabel(c))}" aria-label="${esc(colorLabel(c))}"><i style="--c:${esc(c)}"></i></button>`).join("")}</div>
        <div class="te-cpop-h">${T("Палитра", "Palette")}</div>
        <div class="te-cgrid">${((cat && cat.accents) || []).map((a) => `<button type="button" class="te-cchip ${current === a[m] ? "on" : ""}" data-c="${esc(a[m])}" title="${esc(pick2(a))}" aria-label="${esc(pick2(a))}"><i style="--c:${esc(a[m])}"></i></button>`).join("")}</div>
        <div class="te-cpick"><input type="color" value="${esc(hex)}" aria-label="${T("Свой цвет", "Custom colour")}"><input class="te-input" value="${esc(current)}" placeholder="#7c5cff · var(--accent)" spellcheck="false" aria-label="${T("Значение цвета", "Colour value")}"></div>
        <div class="te-cpop-f"><button type="button" class="te-btn ghost small" data-c="">${I("reset")}<span>${T("Как в теме", "From the theme")}</span></button><button type="button" class="te-btn primary small" data-close>${T("Готово", "Done")}</button></div>
      </div>`, "te-pop-color", () => { if (ED.on && ED.tab === "element") setTimeout(render, 0); });
    const [native, text] = pop.querySelectorAll("input");
    pop.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-c]");
      if (chip) { setColor(key, chip.dataset.c); text.value = chip.dataset.c; if (chip.dataset.c && resolve(chip.dataset.c)) native.value = resolve(chip.dataset.c); pop.querySelectorAll(".te-cchip").forEach((x) => x.classList.toggle("on", x === chip)); if (!chip.dataset.c) close(); return; }
      if (e.target.closest("[data-close]")) close();
    });
    native.addEventListener("input", () => { text.value = native.value; setColor(key, native.value); });
    text.addEventListener("input", () => { const v = text.value.trim(); if (!v) return; if (safe(v) && resolve(v)) { setColor(key, v); native.value = resolve(v); } });
    text.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); close(); } });
    setTimeout(() => (pop.querySelector(".te-cchip.on") || pop.querySelector(".te-cchip"))?.focus(), 0);
  }
  function currentColor(key) {
    if (key.startsWith("el:")) return elementValue(ED.sel.id, key.slice(3)) || "";
    if (key.startsWith("tok:")) return look().tokens[curMode()][key.slice(4)] || "";
    if (key.startsWith("bg:")) return look().bg[key.slice(3)] || "";
    return "";
  }

  function showConfirm(n) {
    const box2 = ED.panel.querySelector(".te-confirm");
    ED.confirm = true;
    box2.innerHTML = `<div class="te-confirm-card">
        <b id="te-confirm-title">${T("Выйти без сохранения?", "Leave without saving?")}</b>
        <p>${isEn() ? `${n} changes will be undone and everything goes back to how it was.` : `${n} ${plural(n, "изменение будет отменено", "изменения будут отменены", "изменений будут отменены")} — всё вернётся как было.`}</p>
        <div class="te-confirm-a">
          <button type="button" class="te-btn ghost" data-act="confirm-stay">${T("Продолжить", "Keep editing")}</button>
          <button type="button" class="te-btn ghost" data-act="confirm-save">${T("Сохранить", "Save")}</button>
          <button type="button" class="te-btn danger" data-act="confirm-leave">${T("Отменить всё", "Discard all")}</button>
        </div>
        <small><kbd>Esc</kbd> ${T("ещё раз — выйти без сохранения", "again leaves without saving")}</small></div>`;
    box2.hidden = false;
    setTimeout(() => box2.querySelector('[data-act="confirm-stay"]')?.focus(), 0);
  }
  function hideConfirm() {
    ED.confirm = false;
    const box2 = ED.panel && ED.panel.querySelector(".te-confirm");
    if (box2) { box2.hidden = true; box2.innerHTML = ""; }
  }

  function settingsCard() {
    return `<div class="te-card" data-sx-k="theme editor редактор тем customize кастомизация внешний вид layout расположение">
        <span class="te-card-art" aria-hidden="true">${I("palette")}</span>
        <div class="te-card-text"><b>${T("Редактор тем", "Theme editor")}</b>
          <span>${T("Меняйте всё прямо в окне и сразу видьте разницу: щёлкните по любому элементу — цвета, форма, шрифт; расположение панелей, свои темы и обмен ими. Esc отменяет.",
            "Change anything right in the window and see it at once: click any element for its colours, shape and font; layout, your own themes and sharing them. Esc cancels.")}</span></div>
        <button type="button" class="pbtn" data-theme-action="editor">${T("Открыть редактор", "Open the editor")}</button>
      </div>`;
  }
  function toggleEditor() { if (ED.on) requestCancel(); else open(); }
  window.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || (window.MemuHotkeys && MemuHotkeys.recording)) return;
    const hit = window.MemuHotkeys && typeof MemuHotkeys.match === "function" ? MemuHotkeys.match("theme_editor", e, "Ctrl+Shift+U")
      : (e.ctrlKey && e.shiftKey && !e.altKey && e.code === "KeyU");
    if (!hit) return;
    e.preventDefault();
    toggleEditor();
  });
  function registerExtras() {
    try {
      if (window.MemuHotkeys && typeof MemuHotkeys.register === "function") {
        MemuHotkeys.register([{ id: "theme_editor", keys: ["Ctrl+Shift+U"], ru: "Редактор тем (Esc — отменить)", en: "Theme editor (Esc cancels)", group: "main" }]);
      }
    } catch (_) {}
    try {
      if (window.MemuSearch && typeof MemuSearch.registerProvider === "function") {
        MemuSearch.registerProvider({
          id: "theme-editor", type: "command", label: ["Команды", "Commands"],
          search(q) {
            const title = L("Редактор тем и внешнего вида", "Theme and appearance editor");
            const words = "редактор тем theme editor внешний вид appearance дизайн design цвет color кастомизация customize расположение layout боковая панель sidebar";
            const score = typeof MemuSearch._scoreText === "function" ? MemuSearch._scoreText(q, title, words) : (String(q || "").trim() && (title + " " + words).toLowerCase().includes(String(q).toLowerCase()) ? 0.8 : 0);
            return score ? [{ id: "command:theme-editor", type: "command", title, subtitle: "Ctrl+Shift+U", snippet: "", score: Math.min(1, score * 1.12), keys: "Ctrl+Shift+U", open: { action: "theme-editor" } }] : [];
          },
          open() { if (window.MemuSearch) MemuSearch.close(); setTimeout(() => open(), 60); },
        });
      }
    } catch (_) {}
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", () => setTimeout(registerExtras, 0), { once: true });
  else setTimeout(registerExtras, 0);

  window.MemuThemeEditor = Object.freeze({
    open, cancel, save, undo, redo, compare, requestCancel, select, setTab, settingsCard,
    isOpen: () => ED.on, changes, on: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
    get state() { return ED.on ? { tab: ED.tab, sel: ED.sel && ED.sel.id, picking: ED.picking, dock: ED.ui.dock, changes: changes(), draft: clone(ED.draft), canUndo: ED.past.length > 0, canRedo: ED.future.length > 0, confirm: ED.confirm } : null; },
    ELEMENTS, PRESETS,
  });
})();
