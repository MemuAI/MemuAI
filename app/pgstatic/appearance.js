

"use strict";
const MemuAppearance = (() => {
  const densities = Object.freeze([
    { id: "compact", ru: "Компактно", en: "Compact" },
    { id: "", ru: "Обычно", en: "Default" },
    { id: "spacious", ru: "Просторно", en: "Spacious" },
  ]);
  const TOKEN_GROUPS = Object.freeze([
    { id: "surface", ru: "Поверхности", en: "Surfaces" },
    { id: "text", ru: "Текст", en: "Text" },
    { id: "accent", ru: "Акцент", en: "Accent" },
    { id: "state", ru: "Состояния", en: "States" },
    { id: "code", ru: "Блоки кода", en: "Code blocks" },
    { id: "syntax", ru: "Подсветка синтаксиса", en: "Syntax highlighting" },
    { id: "semantic", ru: "Отдельные области", en: "Individual areas" },
  ]);
  const SYNTAX_NAMES = { kw: ["Ключевые слова", "Keywords"], type: ["Типы и классы", "Types and classes"],
    str: ["Строки", "Strings"], com: ["Комментарии", "Comments"], num: ["Числа", "Numbers"],
    fn: ["Функции", "Functions"], op: ["Операторы", "Operators"], lit: ["Литералы (true, null)", "Literals (true, null)"],
    tag: ["HTML-теги", "HTML tags"], attr: ["Атрибуты", "Attributes"], prop: ["Свойства", "Properties"],
    var: ["Переменные", "Variables"], meta: ["Директивы и декораторы", "Directives and decorators"],
    punct: ["Пунктуация", "Punctuation"] };

  let data = null;
  let loading = null;
  let prefFn = null;
  let hooks = null;
  let preview = null;
  let studio = null;

  const root = () => document.documentElement;
  const mode = () => (root().dataset.theme === "light" ? "light" : "dark");
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const pick = (row, key = "") => (en() ? row[key ? key + "_en" : "en"] : row[key ? key + "_ru" : "ru"]) || row.name || "";
  const note = (text, type = "ok") => { try { toast(text, type); } catch (_) {} };
  const cache = (key, value) => {
    try { if (value) localStorage.setItem(key, value); else localStorage.removeItem(key); } catch (_) {}
  };

  async function load() {
    if (loading) return loading;
    loading = (async () => {
      try {
        const response = await fetch("/api/pg/themes");
        if (!response.ok) throw new Error("HTTP " + response.status);
        data = await response.json();
      } catch (error) {
        console.warn("theme catalogue unavailable:", error);
      }
      loading = null;
      return data;
    })();
    return loading;
  }

  const accents = () => (data && data.accents) || [];
  const variants = (m) => (data && data.variants && data.variants[m]) || [];
  const customThemes = (m) => ((data && data.custom) || []).filter((row) => !m || row.base === m);
  const findCustom = (slug) => ((data && data.custom) || []).find((row) => row.id === slug) || null;

  const DESIGN_IDS = ["classic", "aurora", "focus", "grid", "glass", "soft", "terminal", "brutal"];
  const designs = () => (data && Array.isArray(data.designs) && data.designs.length ? data.designs
    : DESIGN_IDS.map((id) => ({ id, ru: id, en: id })));
  const normalizeDesign = (value) => (DESIGN_IDS.includes(value) ? value : "classic");
  const normalizeAccent = (value) => (!data || accents().some((row) => row.id === value) ? (value || "terracotta") : "terracotta");
  const normalizeWidth = (value) => (["comfortable", "wide"].includes(value) ? value : "comfortable");
  const normalizeDensity = (value) => (densities.some((row) => row.id === value) ? value : "");
  function normalizeVariant(m, value) {
    const ref = String(value || "");
    if (ref.startsWith("custom:")) return /^custom:[a-z0-9][a-z0-9-]{0,47}$/.test(ref) ? ref : "";
    if (!data) return /^[a-z0-9-]{0,48}$/.test(ref) ? ref : "";
    return variants(m).some((row) => row.id === ref) ? ref : "";
  }

  function apply(pref) {
    prefFn = pref;
    const r = root();
    r.dataset.accent = normalizeAccent(pref("accent_color", "terracotta"));

    r.dataset.design = window.MEMU_SAFE_MODE && window.MEMU_SAFE_MODE.active ? "classic" : normalizeDesign(pref("ui_design", "classic"));
    if (!(window.MEMU_SAFE_MODE && window.MEMU_SAFE_MODE.active)) cache("memu_design", r.dataset.design === "classic" ? "" : r.dataset.design);
    r.dataset.chatWidth = normalizeWidth(pref("chat_width", "comfortable"));
    const density = normalizeDensity(pref("ui_density", ""));
    if (density) r.dataset.density = density; else delete r.dataset.density;
    applyVariant();
    cache("memu_accent", r.dataset.accent);
    if (window.MemuAppearancePlus) MemuAppearancePlus.apply(pref);
    if (window.MemuLook) MemuLook.apply(pref);
  }

  function applyVariant() {
    const r = root(), m = mode();
    const style = document.getElementById("memu-user-theme");
    const ref = normalizeVariant(m, prefFn ? prefFn(`surface_${m}`, "") : "");
    let surface = "", css = "", custom = "";
    if (preview && preview.mode === m) {
      surface = preview.extends || ""; css = preview.css || ""; custom = "preview";
    } else if (ref.startsWith("custom:")) {
      const theme = findCustom(ref.slice(7));
      if (!data && style) return;
      if (theme && theme.base === m) { surface = theme.extends || ""; css = theme.css || ""; custom = theme.id; }
    } else surface = ref;
    if (surface) r.dataset.surface = surface; else delete r.dataset.surface;
    if (custom) r.dataset.customTheme = custom; else delete r.dataset.customTheme;
    if (style && style.textContent !== css) style.textContent = css;
    r.style.background = "";
    if (preview) return;
    cache(`memu_surface_${m}`, surface);
    cache(`memu_user_theme_${m}`, css.length < 200000 ? css : "");
    const bg = resolvedColor("var(--bg)");
    cache(`memu_bg_${m}`, bg ? rgbToHex(bg) : "");
  }

  let probe = null;
  function resolvedColor(expression) {
    if (!probe) {
      probe = document.createElement("i");
      probe.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;pointer-events:none";
      probe.setAttribute("aria-hidden", "true");
    }
    if (!probe.isConnected) document.body.appendChild(probe);
    probe.style.color = "";
    probe.style.color = expression;
    const value = getComputedStyle(probe).color;
    const rgb = value.match(/rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:[, /]+([\d.]+))?/);
    if (rgb) return [+rgb[1], +rgb[2], +rgb[3], rgb[4] === undefined ? 1 : +rgb[4]];

    const srgb = value.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s*\/\s*([\d.]+))?/);
    return srgb ? [srgb[1] * 255, srgb[2] * 255, srgb[3] * 255, srgb[4] === undefined ? 1 : +srgb[4]] : null;
  }
  const rgbToHex = (rgb) => "#" + rgb.slice(0, 3).map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  function luminance(rgb) {
    const [r, g, b] = rgb.slice(0, 3).map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  function contrast(fg, bg) {
    const a = resolvedColor(fg), b = resolvedColor(bg);
    if (!a || !b) return 0;
    const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
    return (high + 0.05) / (low + 0.05);
  }

  function miniPreview(tokens, accentVar) {
    const v = (name, fallback) => esc(tokens[name] || fallback);
    return `<span class="variant-mini" aria-hidden="true" style="--v-bg:${v("--bg", "#191b1e")};--v-side:${v("--bg-deep", "#131518")};--v-panel:${v("--panel", "#22252a")};--v-panel2:${v("--panel-2", "#2b2f35")};--v-border:${v("--border", "#3c424b")};--v-text:${v("--text", "#f0f1f3")};--v-muted:${v("--muted", "#aeb4be")};--v-accent:${tokens["--accent"] ? esc(tokens["--accent"]) : accentVar}">
      <i class="vm-side"></i><i class="vm-line"></i><i class="vm-line short"></i><i class="vm-bubble"></i><i class="vm-input"></i><i class="vm-dot"></i></span>`;
  }

  function variantGrid(m, pref) {
    const selected = normalizeVariant(m, pref(`surface_${m}`, ""));
    const accentVar = m === "dark" ? "var(--accent-dark)" : "var(--accent-light)";
    const builtins = variants(m).map((row) => `<label class="variant-option" title="${esc([pick(row), pick(row, "note")].filter(Boolean).join(". "))}">
        <input type="radio" name="appearance-variant-${m}" value="${esc(row.id)}" ${selected === row.id ? "checked" : ""}>
        ${miniPreview(row.preview || {}, accentVar)}
        <span class="variant-name">${esc(pick(row))}<span class="appearance-check" aria-hidden="true">✓</span></span>
      </label>`);
    const own = customThemes(m).map((row) => `<label class="variant-option custom" title="${esc([row.name, row.description].filter(Boolean).join(". "))}">
        <input type="radio" name="appearance-variant-${m}" value="${esc(row.ref)}" ${selected === row.ref ? "checked" : ""}>
        ${miniPreview(row.preview || {}, accentVar)}
        <span class="variant-name">${esc(row.name)}<span class="variant-badge">${L("своя", "custom")}</span><span class="appearance-check" aria-hidden="true">✓</span></span>
      </label>`);
    return `<div class="variant-grid">${builtins.concat(own).join("")}</div>`;
  }

  function customList() {
    const rows = customThemes();
    if (!rows.length) {
      return `<div class="theme-empty">${L("Своих тем пока нет. Создайте тему в студии, импортируйте готовый .css-файл или положите его в папку тем.",
        "No custom themes yet. Build one in the studio, import a ready .css file, or drop it into the themes folder.")}</div>`;
    }
    return `<div class="theme-list">${rows.map((row) => {
      const p = row.preview || {};
      const swatches = ["--bg", "--panel", "--panel-2", "--text", "--accent"].filter((key) => p[key])
        .map((key) => `<i style="background:${esc(p[key])}"></i>`).join("");
      const active = (prefFn && prefFn(`surface_${row.base}`, "")) === row.ref;
      return `<div class="theme-row ${active ? "active" : ""}" data-theme-id="${esc(row.id)}">
        <span class="theme-swatches" aria-hidden="true">${swatches}</span>
        <div class="theme-row-main"><b>${esc(row.name)}</b>
          <span>${row.base === "light" ? L("светлая", "light") : L("тёмная", "dark")}${row.extends ? " · " + L("основа", "based on") + " " + esc(row.extends) : ""}${row.author ? " · " + esc(row.author) : ""}</span></div>
        <div class="theme-row-actions">
          ${active ? `<span class="theme-active-mark">${L("Применена", "Active")}</span>`
            : `<button type="button" class="pbtn ghost small" data-theme-action="use" title="${L("Сделать темой для режима", "Use for its mode")}">${L("Применить", "Use")}</button>`}
          <button type="button" class="pbtn ghost small" data-theme-action="edit">${L("Изменить", "Edit")}</button>
          <button type="button" class="iconbtn theme-more" data-theme-action="more" aria-label="${L("Ещё", "More")}" title="${L("Ещё действия", "More actions")}">${typeof icon === "function" ? icon("more") : "⋯"}</button>
        </div>
        <div class="theme-confirm" hidden>${L("Удалить тему без возможности восстановления?", "Delete this theme permanently?")}
          <button type="button" class="pbtn danger small" data-theme-action="delete-yes">${L("Удалить", "Delete")}</button>
          <button type="button" class="pbtn ghost small" data-theme-action="delete-no">${L("Отмена", "Cancel")}</button></div>
      </div>`;
    }).join("")}</div>`;
  }

  function modeMiniStyle(m, pref) {
    const ref = normalizeVariant(m, pref(`surface_${m}`, ""));
    const custom = ref.startsWith("custom:") ? findCustom(ref.slice(7)) : null;
    const row = variants(m).find((item) => item.id === (custom ? custom.extends : ref));
    const p = (custom && custom.preview) || (row && row.preview);
    if (!p) return "";
    return [["background", "--bg"], ["--mini-side", "--bg-deep"], ["--mini-line", "--muted"],
      ["--mini-input", "--panel"], ["--mini-border", "--border"]]
      .filter(([, key]) => p[key]).map(([prop, key]) => `${prop}:${esc(p[key])}`).join(";");
  }

  function sxSection(id, title, desc, body, card = false, extra = "") {
    const K = window.MemuSettingsKit;
    if (K) return K.section({ id, title, desc, body, card, aside: extra });
    return `<fieldset class="appearance-field" id="${id}"><legend>${title}</legend>${body}</fieldset>`;
  }
  function segment(name, options, value, disabled) {
    return `<div class="sx-seg" role="radiogroup">${options.map(([id, label]) => `<label class="sx-seg-opt"><input type="radio" name="${name}" value="${esc(id)}" ${value === id ? "checked" : ""} ${disabled ? "disabled" : ""}><span>${esc(label)}</span></label>`).join("")}</div>`;
  }
  function row(label, desc, control, extra = "") {
    return `<div class="sx-row p-toggle"${extra}><div class="sx-row-text"><div class="sx-label tl">${label}</div>${desc ? `<div class="sx-desc td">${desc}</div>` : ""}</div><div class="sx-control">${control}</div></div>`;
  }

  function render(lang, theme, pref) {
    prefFn = pref;
    const accent = normalizeAccent(pref("accent_color", "terracotta"));
    const width = normalizeWidth(pref("chat_width", "comfortable"));
    const own = customThemes().length;
    const unavailable = !data ? `<p class="appearance-note warn">${L("Каталог тем не загрузился. Встроенные варианты работают, но список и свои темы появятся после перезапуска.",
      "The theme catalogue did not load. Built-in variants still work; the list and custom themes appear after a restart.")}</p>` : "";
    const K = window.MemuSettingsKit;
    const more = (id, title, hint, body, open) => K ? K.more({ id, title, hint, body, open }) : `<details id="${id}" ${open ? "open" : ""}><summary>${title}</summary>${body}</details>`;
    return `<div id="appearance-root">
      ${unavailable}
      ${window.MemuThemeEditor ? MemuThemeEditor.settingsCard() : ""}
      ${sxSection("appearance-sidebar-section", L("Боковая панель", "Sidebar"), L("Настройте разделы и кнопки модулей. Этот пункт всегда доступен здесь.", "Choose sections and module shortcuts. This control is always available here."),
        row(L("Кнопка «Настроить панель»", "Customize sidebar shortcut"), L("Показывать кнопку в самой боковой панели.", "Show the shortcut in the sidebar."), `<label class="switch"><input type="checkbox" role="switch" id="st-sidebar-customize" aria-label="${L("Показывать кнопку настройки панели", "Show the sidebar customization shortcut")}" ${pref("sidebar_customize_visible", true) !== false ? "checked" : ""}><span class="sl"></span></label>`)
        + row(L("Разделы и модули", "Sections and modules"), L("Work, Команда, папки и другие элементы навигации.", "Work, Team, folders and other navigation items."), `<button type="button" class="pbtn ghost" id="st-customize-sidebar">${L("Настроить панель", "Customize sidebar")}</button>`))}
      ${sxSection("appearance-theme-section", L("Тема", "Theme"), L("Светлое или тёмное оформление и его вариант. Всё применяется сразу.", "Light or dark, and its variant. Everything applies instantly."), `
        <div class="theme-options" data-sx-label="${L("Светлая или тёмная тема", "Light or dark theme")}" data-sx-k="${L("светлая тёмная режим", "light dark mode")} theme mode">${["light", "dark"].map((m) => `<label class="theme-option">
          <input type="radio" name="appearance-theme" value="${m}" ${theme === m ? "checked" : ""}>
          <span class="theme-mini theme-mini-${m}" aria-hidden="true" style="${modeMiniStyle(m, pref)}"><i class="theme-mini-side"></i><i class="theme-mini-line"></i><i class="theme-mini-bubble"></i><i class="theme-mini-input"></i></span>
          <span class="theme-option-name">${m === "dark" ? L("Тёмное", "Dark") : L("Светлое", "Light")}<span class="appearance-check" aria-hidden="true">✓</span></span>
        </label>`).join("")}</div>
        <div class="appearance-subhead">${theme === "light" ? L("Вариант светлой темы", "Light theme variant") : L("Вариант тёмной темы", "Dark theme variant")}</div>
        ${variantGrid(theme === "light" ? "light" : "dark", pref)}
        <p class="appearance-note">${theme === "light" ? L("Свой вариант запоминается для каждого режима: переключите режим, чтобы выбрать тёмный.", "Each mode remembers its own variant: switch the mode to pick a dark one.")
          : L("Свой вариант запоминается для каждого режима: переключите режим, чтобы выбрать светлый.", "Each mode remembers its own variant: switch the mode to pick a light one.")}</p>`)}
      ${sxSection("appearance-accent-section", L("Акцентный цвет", "Accent colour"), L("Оттенки подобраны отдельно для светлого и тёмного фона и проходят проверку контраста.", "Shades are tuned separately for light and dark and pass a contrast check."), `
        <div class="accent-options" data-sx-label="${L("Акцентный цвет", "Accent colour")}" data-sx-k="accent акцент цвет color ${esc(accents().map((row) => `${row.ru || ""} ${row.en || ""}`).join(" "))}">${accents().map((row) => `<label class="accent-option" title="${esc(pick(row))}" style="--swatch-light:${esc(row.light)};--swatch-dark:${esc(row.dark)}">
          <input type="radio" name="appearance-accent" value="${esc(row.id)}" ${accent === row.id ? "checked" : ""}>
          <span class="accent-dot" aria-hidden="true"><span>✓</span></span><span>${esc(pick(row))}</span>
        </label>`).join("")}</div>`)}
      ${sxSection("appearance-design-section", L("Дизайн интерфейса", "Interface design"), L("Форма, типографика и компоновка всего окна. Цвета выше работают с любым дизайном.", "The shape, typography and layout of the whole window. The colours above work with every design."), `
        <div class="design-options" data-sx-label="${L("Дизайн интерфейса", "Interface design")}" data-sx-k="design дизайн classic aurora focus grid glass soft terminal brutal material ${esc(designs().map((row) => `${row.ru || ""} ${row.en || ""}`).join(" "))}">${designs().map((row) => `<label class="design-option" data-design-card="${esc(row.id)}" title="${esc(`${pick(row)}. ${pick(row, "note")}`)}">
          <input type="radio" name="appearance-design" value="${esc(row.id)}" ${normalizeDesign(pref("ui_design", "classic")) === row.id ? "checked" : ""}>
          <span class="design-mini design-mini-${esc(row.id)}" aria-hidden="true"><i class="dm-side"></i><i class="dm-top"></i><i class="dm-msg a"></i><i class="dm-msg b"></i><i class="dm-msg c"></i><i class="dm-input"></i></span>
          <span class="design-option-text"><b>${esc(pick(row))}<span class="appearance-check" aria-hidden="true">✓</span></b><small>${esc(pick(row, "note"))}</small></span>
        </label>`).join("")}</div>`)}
      ${sxSection("appearance-layout-section", L("Компоновка", "Layout"), "", `
        ${row(L("Ширина диалога", "Conversation width"), L("«Для чтения» держит строки короткими", "Comfortable keeps lines easy to read"), segment("appearance-width", [["comfortable", L("Для чтения", "Comfortable")], ["wide", L("Широкая", "Wide")]], width), ' data-sx-k="width ширина"')}
        ${row(L("Плотность", "Density"), L("Отступы в диалоге, списках чатов, настройках и поиске", "Spacing in the conversation, chat list, settings and search"), segment("appearance-density", densities.map((item) => [item.id, en() ? item.en : item.ru]), normalizeDensity(pref("ui_density", ""))), ' data-sx-k="density плотность compact spacing компактно просторно отступы"')}
        ${titlebarField(pref)}`, true)}
      ${window.MemuAppearancePlus ? MemuAppearancePlus.html({ L, row, segment, sxSection, pref }) : ""}
      ${sxSection("appearance-custom-section", "", "", more("appearance-custom-more", L("Свои темы", "Custom themes"), own ? L(`${own} шт.`, `${own}`) : L("студия, импорт .css", "studio, .css import"), `
        <div class="theme-toolbar">
          <button type="button" class="pbtn" data-theme-action="create">${L("Создать тему", "Create theme")}</button>
          ${window.MemuThemeEditor ? `<button type="button" class="pbtn ghost" data-theme-action="editor">${L("Редактор тем", "Theme editor")}</button>` : ""}
          <button type="button" class="pbtn ghost" data-theme-action="import">${L("Импорт .css", "Import .css")}</button>
          <button type="button" class="pbtn ghost" data-theme-action="folder">${L("Папка тем", "Themes folder")}</button>
          <button type="button" class="pbtn ghost" data-theme-action="docs">${L("Как это работает", "How it works")}</button>
          <input type="file" id="theme-import-file" accept=".css,.json,text/css,application/json" hidden>
        </div>
        ${customList()}`, own > 0), false)}
    </div>`;
  }

  function titlebarField(pref) {
    const tb = window.MemuTitlebar;
    const st = tb && typeof tb.state === "function" ? tb.state() : null;
    const available = !!(tb && typeof tb.available === "function" && tb.available());
    const value = pref("ui_titlebar", "custom") === "native" ? "native" : "custom";
    let note;
    if (!available) {
      note = L("Работает в приложении MemuAI для Windows 10 (1809 и новее) и Windows 11.",
        "Works in the MemuAI app on Windows 10 (1809 or later) and Windows 11.");
    } else if (st && st.forced) {
      note = L("Сейчас принудительно включена системная строка: MemuAI запущен с флагом --native-titlebar или переменной MEMUAI_NATIVE_TITLEBAR. Уберите их, чтобы вернуть свою.",
        "The system title bar is forced right now: MemuAI was started with --native-titlebar or MEMUAI_NATIVE_TITLEBAR. Remove it to use the custom one.");
    } else if (st && value === "custom" && !st.installed && st.error) {
      note = L("На этой системе свою строку заголовка включить не удалось, поэтому оставлена системная.",
        "The custom title bar could not be enabled on this system, so the system one is kept.");
    } else {
      note = L("Своя: кнопки окна встроены в верхнюю панель, а перетаскивание, прилипание к краям и Alt+Пробел работают как в Windows. Если что-то не так — выберите «Системная» или запустите MemuAI с флагом --native-titlebar.",
        "Custom: the window buttons live in the top bar; dragging, snapping and Alt+Space work like in Windows. If anything goes wrong, choose System or start MemuAI with --native-titlebar.");
    }
    return `<div class="sx-row p-toggle" id="appearance-titlebar" data-sx-k="title bar заголовок окна titlebar кнопки окна"><div class="sx-row-text"><div class="sx-label tl">${L("Строка заголовка окна", "Window title bar")}</div><div class="sx-desc td appearance-note">${note}</div></div>
        <div class="sx-control"><div class="sx-seg" role="radiogroup">${[["custom", L("Своя", "Custom")], ["native", L("Системная", "System")]].map(([id, label]) => `<label class="sx-seg-opt">
          <input type="radio" name="appearance-titlebar" value="${id}" ${value === id ? "checked" : ""} ${available ? "" : "disabled"}><span>${label}</span></label>`).join("")}</div></div>
      </div>`;
  }

  function refreshTitlebarField() {
    const field = document.getElementById("appearance-titlebar");
    if (field && prefFn) field.outerHTML = titlebarField(prefFn);
  }

  function syncTheme(theme) {
    document.querySelectorAll('[name="appearance-theme"]').forEach((input) => { input.checked = input.value === theme; });
    applyVariant();

    if (!studio) rerender();
  }

  function rerender() {
    const host = document.getElementById("appearance-root");
    if (!host || !prefFn) return;
    host.outerHTML = render(en() ? "en" : "ru", mode(), prefFn);
  }

  async function api(path, body) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }

  async function reloadCatalog() {
    data = null;
    await load();
    applyVariant();
    rerender();
  }

  let lastDiskCheck = 0;
  async function refreshFromDisk() {
    if (studio || preview || !prefFn || Date.now() - lastDiskCheck < 1500) return;
    lastDiskCheck = Date.now();
    const ref = String(prefFn(`surface_${mode()}`, "") || "");
    const pane = document.getElementById("appearance-root");
    if (!ref.startsWith("custom:") && !pane) return;
    try {
      const response = await fetch("/api/pg/themes");
      if (!response.ok) return;
      const fresh = await response.json();
      const signature = (rows) => JSON.stringify((rows || []).map((row) => [row.id, row.css, row.name, row.base, row.extends]));
      if (signature(fresh.custom) === signature(data && data.custom)) return;
      data = fresh;
      applyVariant();
      rerender();
    } catch (_) {  }
  }
  window.addEventListener("focus", refreshFromDisk);

  function useVariant(m, ref) {
    if (!hooks) return;
    hooks.save({ [`surface_${m}`]: normalizeVariant(m, ref) });
    if (mode() !== m) {
      const select = document.getElementById("st-theme");
      if (select) { select.value = m; select.dispatchEvent(new Event("change", { bubbles: true })); }
      else hooks.setTheme(m);
    } else applyVariant();
    rerender();
  }

  async function importFile(file) {
    if (!file) return;
    if (file.size > 256 * 1024) { note(L("Файл темы больше 256 КБ", "The theme file is larger than 256 KB"), "err"); return; }
    const text = await file.text();
    let body = { css: text };
    if (/\.json$/i.test(file.name) || /^\s*\{/.test(text)) {
      try {
        const parsed = JSON.parse(text);
        body = { css: String(parsed.css || ""), name: parsed.name, base: parsed.base, extends: parsed.extends,
          author: parsed.author, description: parsed.description };
      } catch (_) { note(L("Не удалось прочитать JSON темы", "Could not read the theme JSON"), "err"); return; }
    }
    if (!body.name && !/@name\s/.test(body.css)) body.name = file.name.replace(/\.(css|json)$/i, "").replace(/\.memu-theme$/i, "");
    try {
      const result = await api("/api/pg/themes", body);
      await reloadCatalog();
      useVariant(result.theme.base, result.theme.ref);
      note(L(`Тема «${result.theme.name}» добавлена и применена`, `Theme “${result.theme.name}” added and applied`));
    } catch (error) { note(error.message, "err"); }
  }

  async function themeAction(action, row) {
    const theme = row ? findCustom(row.dataset.themeId) : null;
    if (action === "create") return openStudio(null);
    if (action === "editor") return window.MemuThemeEditor?.open();
    if (action === "import") return document.getElementById("theme-import-file")?.click();
    if (action === "docs") {
      if (window.MemuDocs && typeof MemuDocs.open === "function") MemuDocs.open("themes");
      return;
    }
    if (action === "folder") {
      try { await api("/api/pg/themes/open-folder", {}); await reloadCatalog(); }
      catch (error) { note(error.message, "err"); }
      return;
    }
    if (!theme) return;
    if (action === "use") return useVariant(theme.base, theme.ref);
    if (action === "edit") return openStudio(theme);
    if (action === "delete-no") { row.querySelector(".theme-confirm").hidden = true; return; }
    if (action === "delete-yes") {
      try {
        await api("/api/pg/themes/delete", { id: theme.id });
        if (prefFn && prefFn(`surface_${theme.base}`, "") === theme.ref) hooks.save({ [`surface_${theme.base}`]: "" });
        await reloadCatalog();
        note(L("Тема удалена", "Theme deleted"));
      } catch (error) { note(error.message, "err"); }
    }
  }

  function moreMenu(event, row) {
    const theme = findCustom(row.dataset.themeId);
    if (!theme || typeof showContextMenu !== "function") return;
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    showContextMenu([
      { label: L("Дублировать", "Duplicate"), icon: "copy", run: async () => {
        try {
          await api("/api/pg/themes", { name: theme.name + L(" (копия)", " (copy)"), base: theme.base,
            extends: theme.extends, css: theme.css });
          await reloadCatalog();
        } catch (error) { note(error.message, "err"); }
      } },
      { label: L("Экспорт в файл", "Export to file"), icon: "download", run: () => {
        if (typeof downloadTextFile === "function") downloadTextFile(`${theme.id}.css`, theme.css, "text/css");
      } },
      { label: L("Копировать CSS", "Copy CSS"), icon: "clipboard", run: () => {
        if (typeof copyText === "function") copyText(theme.css);
      } },
      { sep: true },
      { label: L("Удалить", "Delete"), icon: "trash", danger: true, run: () => {
        const box = row.querySelector(".theme-confirm");
        if (box) box.hidden = false;
      } },
    ], rect.left, rect.bottom + 4);
  }

  const motionSwap = (update, kind) => (window.MemuMotion && typeof window.MemuMotion.swap === "function"
    ? window.MemuMotion.swap(update, kind) : update());

  function bind({ save, setTheme, apply: applyAll }) {
    hooks = { save, setTheme, apply: applyAll };
    const pane = document.getElementById("pane-appearance");
    if (!pane) return;
    if (window.MemuAppearancePlus) MemuAppearancePlus.bind(pane, { save, apply: applyAll });
    pane.addEventListener("change", (event) => {
      const input = event.target;
      if (input.id === "theme-import-file") { importFile(input.files && input.files[0]); input.value = ""; return; }
      if (studio && input.closest(".theme-studio")) return;
      if (input.name === "appearance-theme") {
        setTheme(input.value);
      } else if (input.name === "appearance-variant-dark" || input.name === "appearance-variant-light") {
        useVariant(input.name.endsWith("dark") ? "dark" : "light", input.value);
      } else if (input.name === "appearance-design") {

        motionSwap(() => { save({ ui_design: normalizeDesign(input.value) }); applyAll(); }, "design");
      } else if (input.name === "appearance-accent") {
        motionSwap(() => { save({ accent_color: normalizeAccent(input.value) }); applyAll(); }, "accent");
      } else if (input.name === "appearance-width") {
        save({ chat_width: normalizeWidth(input.value) }); applyAll();
      } else if (input.name === "appearance-density") {
        save({ ui_density: normalizeDensity(input.value) }); applyAll();
      } else if (input.name === "appearance-titlebar") {
        const value = input.value === "native" ? "native" : "custom";
        save({ ui_titlebar: value });

        if (window.MemuTitlebar) window.MemuTitlebar.setMode(value).then(refreshTitlebarField);
      }
    });
    pane.addEventListener("click", (event) => {
      const button = event.target.closest("[data-theme-action]");
      if (!button || !pane.contains(button)) return;
      const row = button.closest(".theme-row");
      if (button.dataset.themeAction === "more") moreMenu(event, row);
      else themeAction(button.dataset.themeAction, row);
    });
    document.addEventListener("memu-titlebar", refreshTitlebarField);
    pane.addEventListener("input", (event) => { if (studio) studioInput(event); });
    pane.addEventListener("click", (event) => { if (studio) studioClick(event); });
    pane.addEventListener("keydown", (event) => { if (studio) studioKey(event); });
  }

  function splitTheme(css) {
    const body = String(css || "").replace(/^\s*\/\*[\s\S]*?\*\/\s*/, (header) => (/@name\s/.test(header) ? "" : header));
    const match = body.match(/(^|\n)\s*:root\s*\{([^{}]*)\}\s*/);
    const tokens = {};
    let rest = body;
    if (match) {
      match[2].replace(/(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);?/g, (_, name, value) => { tokens[name] = value.trim(); return ""; });
      rest = body.slice(0, match.index) + body.slice(match.index + match[0].length);
    }
    return { tokens, rest: rest.replace(/^\s+/, "") };
  }

  function baseTokens(m, extendsId) {
    const row = variants(m).find((item) => item.id === (extendsId || "")) || variants(m)[0];
    return { ...((row && row.tokens) || {}) };
  }

  function studioTokens() {
    const all = (data && data.tokens) || [];
    const semantic = ((data && data.semantic) || []).filter((row) => /^--[a-z0-9-]+$/.test(row.name));
    return all.concat(semantic.map((row) => ({ ...row, group: "semantic" })));
  }

  function composeCss() {
    const lines = Object.entries(studio.values)
      .filter(([, value]) => String(value || "").trim())
      .map(([name, value]) => `  ${name}: ${String(value).trim()};`);
    const tokens = lines.length ? `:root {\n${lines.join("\n")}\n}\n` : "";
    const rest = String(studio.rest || "").trim();
    return tokens + (rest ? (tokens ? "\n" : "") + rest + "\n" : "");
  }

  function pushPreview() {

    if (!studio) return;
    preview = { mode: studio.base, extends: studio.extends, css: composeCss() };
    if (mode() !== studio.base && hooks) hooks.setTheme(studio.base, false);
    applyVariant();
    updateChecks();
  }

  function endPreview() {
    if (!studio && !preview) return;
    clearTimeout(previewTimer);
    const originalMode = studio && studio.originalMode;
    preview = null;
    studio = null;
    const pane = document.getElementById("pane-appearance");
    if (pane) pane.classList.remove("studio-open");
    if (originalMode && mode() !== originalMode && hooks) hooks.setTheme(originalMode, false);
    applyVariant();
  }

  function openStudio(theme) {
    const pane = document.getElementById("pane-appearance");
    const host = document.getElementById("appearance-root");
    if (!pane || !host || !data) return;
    const split = theme ? splitTheme(theme.css) : { tokens: {}, rest: "" };
    studio = {
      id: theme ? theme.id : "",
      name: theme ? theme.name : L("Моя тема", "My theme"),
      base: theme ? theme.base : mode(),
      extends: theme ? theme.extends : normalizeVariant(mode(), prefFn ? prefFn(`surface_${mode()}`, "") : "").replace(/^custom:.*/, ""),
      author: theme ? theme.author : "",
      description: theme ? theme.description : "",
      values: { ...split.tokens },
      rest: split.rest,
      originalMode: mode(),
      dirty: false,
    };
    pane.classList.add("studio-open");
    host.innerHTML = studioHtml();
    pane.closest(".settings-body")?.scrollTo?.({ top: 0 });
    pushPreview();
    host.querySelector("#studio-name")?.focus();
  }

  function tokenRow(row) {
    const base = baseTokens(studio.base, studio.extends);
    const value = studio.values[row.name] || "";
    const shown = value || base[row.name] || "";
    const rgb = resolvedColor(shown || row.fallback || "transparent");
    const hex = rgb ? rgbToHex(rgb) : "#000000";
    const label = row.group === "syntax" ? (SYNTAX_NAMES[row.name.replace("--tok-", "")] || [row.name, row.name])[en() ? 1 : 0]
      : ((en() ? row.en : row.ru) || "");
    const placeholder = base[row.name] || row.fallback || "";
    return `<div class="token-row ${value ? "set" : ""}" data-token="${esc(row.name)}">
      <input type="color" class="token-color" value="${esc(hex)}" aria-label="${esc(row.name)}" title="${L("Выбрать цвет", "Pick a colour")}">
      <div class="token-meta"><code>${esc(row.name)}</code><span>${esc(label)}</span></div>
      <input type="text" class="token-value" value="${esc(value)}" placeholder="${esc(placeholder)}" spellcheck="false" aria-label="${esc(row.name)}">
      <button type="button" class="iconbtn token-reset" data-studio="reset" title="${L("Вернуть значение основы", "Back to the base value")}" ${value ? "" : "disabled"}>↺</button>
    </div>`;
  }

  function studioHtml() {
    const groups = TOKEN_GROUPS.map((group) => {
      const rows = studioTokens().filter((row) => row.group === group.id);
      if (!rows.length) return "";
      const hint = group.id === "semantic" ? `<p class="appearance-note">${L("Необязательно. Пустое поле берёт цвет из основных токенов.",
        "Optional. An empty field inherits from the core tokens.")}</p>` : group.id === "accent" ? `<p class="appearance-note">${L("Оставьте пустым, чтобы тема использовала акцент из настроек.",
        "Leave empty to keep the accent chosen in settings.")}</p>` : "";
      return `<details class="studio-group" ${["surface", "text", "accent"].includes(group.id) ? "open" : ""}>
        <summary>${en() ? group.en : group.ru}<span>${rows.length}</span></summary>${hint}${rows.map(tokenRow).join("")}</details>`;
    }).join("");
    const extendsOptions = variants(studio.base).map((row) => `<option value="${esc(row.id)}" ${row.id === studio.extends ? "selected" : ""}>${esc(pick(row))}</option>`).join("");
    return `<div class="theme-studio">
      <div class="studio-head">
        <button type="button" class="pbtn ghost small" data-studio="back">← ${L("Назад", "Back")}</button>
        <h3>${studio.id ? L("Редактирование темы", "Edit theme") : L("Новая тема", "New theme")}</h3>
        <span class="grow"></span>
        <button type="button" class="pbtn ghost small" data-studio="export">${L("Экспорт", "Export")}</button>
        <button type="button" class="pbtn small" data-studio="save">${L("Сохранить и применить", "Save and apply")}</button>
      </div>
      <p class="appearance-note">${L("Все изменения сразу видны во всём окне. Пока тема не сохранена, её можно отменить кнопкой «Назад».",
        "Changes preview live across the whole window. Until you save, “Back” discards them.")}</p>
      <div class="studio-grid">
        <section class="studio-form">
          <label class="studio-field"><span>${L("Название", "Name")}</span><input class="pinput" id="studio-name" value="${esc(studio.name)}" maxlength="80"></label>
          <div class="studio-pair">
            <div class="studio-field"><span>${L("Режим", "Mode")}</span>
              <div class="width-options">${["dark", "light"].map((m) => `<label class="width-option"><input type="radio" name="studio-base" value="${m}" ${studio.base === m ? "checked" : ""}><span>${m === "dark" ? L("Тёмная", "Dark") : L("Светлая", "Light")}</span></label>`).join("")}</div></div>
            <label class="studio-field"><span>${L("Основа", "Based on")}</span><select class="pinput" id="studio-extends">${extendsOptions}</select></label>
          </div>
          <div class="studio-pair">
            <label class="studio-field"><span>${L("Автор", "Author")}</span><input class="pinput" id="studio-author" value="${esc(studio.author)}" maxlength="80"></label>
            <label class="studio-field"><span>${L("Описание", "Description")}</span><input class="pinput" id="studio-description" value="${esc(studio.description)}" maxlength="200"></label>
          </div>
          ${groups}
          <details class="studio-group" open><summary>${L("Свой CSS", "Custom CSS")}<span>CSS</span></summary>
            <p class="appearance-note">${L("Любые правила для любых элементов: они подключаются последними и перекрывают стили приложения. Список селекторов — в документации, раздел «Темы».",
              "Any rule for any element: it loads last and overrides the application styles. Selectors are listed in the documentation, “Themes”.")}</p>
            <textarea class="studio-css" id="studio-css" spellcheck="false" rows="12" placeholder=".msg-user .bubble { border-radius: 4px; }">${esc(studio.rest)}</textarea>
          </details>
        </section>
        <aside class="studio-side">
          <div class="studio-sample" aria-hidden="true">
            <div class="ss-side"><b>MemuAI</b><i></i><i></i><i class="on"></i></div>
            <div class="ss-main">
              <div class="ss-bubble">${L("Как сделать свою тему?", "How do I make a theme?")}</div>
              <div class="ss-answer">${L("Откройте студию и поменяйте токены.", "Open the studio and change tokens.")} <a>${L("Ссылка", "Link")}</a> <span class="ss-muted">${L("подпись", "caption")}</span></div>
              <pre class="ss-code"><span class="tok-kw">const</span> <span class="tok-var">theme</span> <span class="tok-op">=</span> <span class="tok-fn">load</span><span class="tok-punct">(</span><span class="tok-str">"nord"</span><span class="tok-punct">);</span> <span class="tok-com">// ok</span></pre>
              <div class="ss-input"><span>${L("Сообщение…", "Message…")}</span><b>↑</b></div>
            </div>
          </div>
          <div class="studio-checks" id="studio-checks"></div>
        </aside>
      </div>
    </div>`;
  }

  function updateChecks() {
    const box = document.getElementById("studio-checks");
    if (!box) return;
    const checks = [
      [L("Текст на фоне", "Text on background"), "var(--text)", "var(--bg)"],
      [L("Текст на панели", "Text on panel"), "var(--text)", "var(--panel)"],
      [L("Подписи на фоне", "Captions on background"), "var(--muted)", "var(--bg)"],
      [L("Подписи на панели", "Captions on panel"), "var(--muted)", "var(--panel-2)"],
      [L("Акцент на фоне", "Accent on background"), "var(--accent)", "var(--bg)"],
      [L("Текст на акценте", "Text on accent"), "var(--on-accent)", "var(--accent)"],
      [L("Код", "Code"), "var(--code-text)", "var(--code-bg)"],
      [L("Ошибка", "Error"), "var(--danger)", "var(--panel)"],
    ];
    box.innerHTML = `<b>${L("Читаемость (WCAG)", "Readability (WCAG)")}</b>` + checks.map(([label, fg, bg]) => {
      const value = contrast(fg, bg);
      const level = value >= 7 ? "aaa" : value >= 4.5 ? "aa" : value >= 3 ? "weak" : "fail";
      const text = { aaa: "AAA", aa: "AA", weak: L("слабо", "weak"), fail: L("плохо", "poor") }[level];
      return `<div class="check-row ${level}"><span>${esc(label)}</span><b>${value ? value.toFixed(1) : "—"}:1 · ${text}</b></div>`;
    }).join("");
  }

  let previewTimer = 0;
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(pushPreview, 60);
  }

  function studioInput(event) {
    const target = event.target;
    const row = target.closest(".token-row");
    studio.dirty = true;
    if (row) {
      const name = row.dataset.token;
      if (target.classList.contains("token-color")) {
        studio.values[name] = target.value;
        row.querySelector(".token-value").value = target.value;
      } else if (target.classList.contains("token-value")) {
        const value = target.value.trim();
        if (value && !/[;{}<>]/.test(value)) studio.values[name] = value; else delete studio.values[name];
        const rgb = value && resolvedColor(value);
        if (rgb) row.querySelector(".token-color").value = rgbToHex(rgb);
      }
      row.classList.toggle("set", !!studio.values[name]);
      row.querySelector(".token-reset").disabled = !studio.values[name];
      schedulePreview();
    } else if (target.id === "studio-css") {
      studio.rest = target.value; schedulePreview();
    } else if (target.id === "studio-name") studio.name = target.value;
    else if (target.id === "studio-author") studio.author = target.value;
    else if (target.id === "studio-description") studio.description = target.value;
    else if (target.id === "studio-extends" || target.name === "studio-base") {
      if (target.name === "studio-base") { studio.base = target.value; studio.extends = ""; }
      else studio.extends = target.value;
      const host = document.getElementById("appearance-root");
      pushPreview();
      if (host) { const scroll = host.closest(".settings-body")?.scrollTop; host.innerHTML = studioHtml(); updateChecks();
        if (scroll !== undefined) host.closest(".settings-body").scrollTop = scroll; }
    }
  }

  function studioKey(event) {

    if (event.target.id === "studio-css" && event.key === "Tab" && !event.shiftKey && !event.ctrlKey) {
      event.preventDefault();
      document.execCommand("insertText", false, "  ");
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveStudio();
    }
  }

  async function saveStudio() {
    if (!studio) return;
    const body = { id: studio.id || undefined, name: studio.name.trim() || L("Моя тема", "My theme"),
      base: studio.base, extends: studio.extends, author: studio.author, description: studio.description,
      css: composeCss() };
    try {
      const result = await api("/api/pg/themes", body);
      const originalMode = studio.originalMode;
      preview = null;
      studio = null;
      document.getElementById("pane-appearance")?.classList.remove("studio-open");
      data = null;
      await load();
      hooks.save({ [`surface_${result.theme.base}`]: result.theme.ref });
      if (mode() !== result.theme.base) hooks.setTheme(result.theme.base);
      else if (originalMode !== mode()) hooks.setTheme(mode());
      applyVariant();
      rerender();
      note(L(`Тема «${result.theme.name}» сохранена`, `Theme “${result.theme.name}” saved`));
    } catch (error) { note(error.message, "err"); }
  }

  function studioClick(event) {
    const button = event.target.closest("[data-studio]");
    if (!button) return;
    const action = button.dataset.studio;
    if (action === "back") { endPreview(); rerender(); }
    else if (action === "save") saveStudio();
    else if (action === "export") {
      const header = `/*\n * @name ${studio.name}\n * @base ${studio.base}\n${studio.extends ? ` * @extends ${studio.extends}\n` : ""}${studio.author ? ` * @author ${studio.author}\n` : ""}${studio.description ? ` * @description ${studio.description}\n` : ""} */\n`;
      if (typeof downloadTextFile === "function") downloadTextFile(`${(studio.id || "memuai-theme")}.css`, header + composeCss(), "text/css");
    } else if (action === "reset") {
      const row = button.closest(".token-row");
      delete studio.values[row.dataset.token];
      row.querySelector(".token-value").value = "";
      row.classList.remove("set");
      button.disabled = true;
      pushPreview();
      const rgb = resolvedColor(`var(${row.dataset.token})`);
      if (rgb) row.querySelector(".token-color").value = rgbToHex(rgb);
    }
  }

  return Object.freeze({ densities, load, normalizeAccent, normalizeWidth, normalizeVariant,
    normalizeDensity, apply, applyVariant, render, bind, syncTheme, openStudio, endPreview,
    get catalog() { return data; } });
})();
