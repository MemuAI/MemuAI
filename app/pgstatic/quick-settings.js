

(function () {
  "use strict";
  const BOLT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M13.2 2.4 4.6 13.3c-.4.5 0 1.2.6 1.2h5.3l-1.3 7.1c-.1.7.8 1.1 1.2.5l8.6-10.9c.4-.5 0-1.2-.6-1.2h-5.3l1.3-7.1c.1-.7-.8-1.1-1.2-.5z"/></svg>';
  const state = { ctx: null, status: null, capturing: false, probe: null, busy: false, bindController: null,
    captureController: null, refreshToken: 0, personalities: null, pluginsLoaded: false };
  const t = (ctx, ru, en) => (ctx.en ? en : ru);

  const CODE_KEYS = { Space: "Space", Backquote: "`", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]",
    Backslash: "\\", IntlBackslash: "\\", Semicolon: ";", Quote: "'", Comma: ",", Period: ".", Slash: "/", ArrowUp: "Up",
    ArrowDown: "Down", ArrowLeft: "Left", ArrowRight: "Right", Tab: "Tab", Enter: "Enter", Insert: "Insert",
    Delete: "Delete", Home: "Home", End: "End", PageUp: "PageUp", PageDown: "PageDown", Pause: "Pause",
    ScrollLock: "ScrollLock", Escape: "Escape", Backspace: "Backspace", CapsLock: "CapsLock", PrintScreen: "PrintScreen",
    NumLock: "NumLock", ContextMenu: "Menu", NumpadMultiply: "Num*", NumpadAdd: "Num+", NumpadSubtract: "Num-",
    NumpadDecimal: "Num.", NumpadDivide: "Num/" };
  const MOD_CODES = { ControlLeft: "Ctrl", ControlRight: "Ctrl", AltLeft: "Alt", AltRight: "Alt", ShiftLeft: "Shift",
    ShiftRight: "Shift", MetaLeft: "Win", MetaRight: "Win", OSLeft: "Win", OSRight: "Win" };
  const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Win"];
  function keyFromCode(code) {
    code = String(code || "");
    if (/^Key[A-Z]$/.test(code)) return code.slice(3);
    if (/^Digit[0-9]$/.test(code)) return code.slice(5);
    if (/^Numpad[0-9]$/.test(code)) return "Num" + code.slice(6);
    if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) return code;
    return CODE_KEYS[code] || "";
  }

  function hotkeyFromEvent(e, held) {
    const key = keyFromCode(e.code);
    const mods = new Set(held ? Array.from(held) : []);
    if (e.ctrlKey) mods.add("Ctrl");
    if (e.altKey) mods.add("Alt");
    if (e.shiftKey) mods.add("Shift");
    if (e.metaKey) mods.add("Win");
    const list = MOD_ORDER.filter((m) => mods.has(m));
    return { key, text: [...list, key].filter(Boolean).join("+"), mods: list };
  }
  const keycaps = (text, esc) => String(text || "").split("+").filter(Boolean)
    .map((part) => `<kbd>${esc(part)}</kbd>`).join('<span class="qs-plus">+</span>');

  const FALLBACK_KIT = (() => {
    const e = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const at = (n, v) => (v === undefined || v === null || v === "" ? "" : ` ${n}="${e(v)}"`);
    const opts = (o) => (o || []).map((x) => (Array.isArray(x) ? { value: x[0], label: x[1], group: x[2] } : x));
    return {
      esc: e,
      section: (o) => `<section class="sx-section"${at("id", o.id)}${at("data-sx-when", o.when)}><header class="sx-section-head"><h3 class="sx-section-title">${o.title || ""}${o.aside ? `<span class="sx-section-aside">${o.aside}</span>` : ""}</h3>${o.desc ? `<p class="sx-section-desc">${o.desc}</p>` : ""}</header><div class="sx-card">${o.body || ""}</div></section>`,
      row: (o) => `<div class="sx-row p-toggle"${at("id", o.id)}${at("data-sx-when", o.when)}><div class="sx-row-text"><div class="sx-label tl">${o.label || ""}</div>${o.desc ? `<div class="sx-desc td">${o.desc}</div>` : ""}${o.extra || ""}</div>${o.control ? `<div class="sx-control">${o.control}</div>` : ""}</div>`,
      field: (o) => `<div class="sx-field"${at("id", o.id)}${at("data-sx-when", o.when)}>${o.label ? `<label class="sx-field-label tl"${at("for", o.forId)}>${o.label}</label>` : ""}${o.control || ""}${o.help ? `<div class="sx-help td">${o.help}</div>` : ""}</div>`,
      toggle: (id, on, attrs = "") => `<label class="switch sx-switch"><input type="checkbox" role="switch" id="${e(id)}" ${on ? "checked" : ""} ${attrs}><span class="sl"></span></label>`,
      select: (id, options, value, o = {}) => `<select class="pinput compact sx-select" id="${e(id)}" ${o.attrs || ""}>${opts(options).map((x) => `<option value="${e(x.value)}" ${String(x.value) === String(value) ? "selected" : ""}>${e(x.label)}</option>`).join("")}</select>`,
      segmented: (name, options, value, o = {}) => `<div class="sx-seg" role="radiogroup"${at("id", o.id)}>${opts(options).map((x) => `<label class="sx-seg-opt"><input type="radio" name="${e(name)}" value="${e(x.value)}" ${String(x.value) === String(value) ? "checked" : ""}><span>${e(x.label)}</span></label>`).join("")}</div>`,
      input: (id, value, o = {}) => `<input class="pinput sx-input${o.mono ? " sx-mono" : ""}" id="${e(id)}" type="${e(o.type || "text")}" value="${e(value ?? "")}"${at("placeholder", o.placeholder)} ${o.attrs || ""}>`,
      secret: (id, value, o = {}) => `<input class="pinput sx-input sx-mono" id="${e(id)}" type="password" autocomplete="off" spellcheck="false" value="${e(value ?? "")}"${at("placeholder", o.placeholder)} ${o.attrs || ""}>`,
      textarea: (id, value, o = {}) => `<textarea class="ptexta sx-textarea" id="${e(id)}" rows="${e(o.rows || 4)}"${at("placeholder", o.placeholder)} ${o.attrs || ""}>${e(value ?? "")}</textarea>`,
      more: (o) => `<details class="sx-more"${at("id", o.id)}${at("data-sx-when", o.when)}><summary><span>${e(o.title || "Дополнительно")}</span>${o.hint ? `<small>${e(o.hint)}</small>` : ""}</summary><div class="sx-more-body">${o.body || ""}</div></details>`,
      link: (o) => `<button type="button" class="sx-row sx-row--link" data-sx-open="${e(o.sub)}"${at("id", o.id)}><span class="sx-row-text"><span class="sx-label tl">${o.label || ""}</span>${o.desc ? `<span class="sx-desc td">${o.desc}</span>` : ""}</span>${o.value ? `<span class="sx-link-value">${o.value}</span>` : ""}<span class="sx-chev">›</span></button>`,
      sub: (o) => `<div class="sx-sub" data-sx-sub="${e(o.id)}" data-sx-title="${e(o.title || "")}"><div class="sx-sub-head"><div class="sx-sub-title">${e(o.title || "")}</div>${o.desc ? `<p class="sx-sub-desc">${o.desc}</p>` : ""}</div>${o.body || ""}</div>`,
      note: (text, tone = "info") => `<div class="sx-note sx-note--${e(tone)}"><div>${text}</div></div>`,
      chip: (text, tone = "muted", extra = "") => `<span class="sx-chip" data-tone="${e(tone)}"${extra}><i aria-hidden="true"></i>${e(text)}</span>`,
      actions: (html) => `<div class="sx-actions">${html}</div>`,
      badge: (kind) => (kind ? `<span class="sx-badge">${e(kind)}</span>` : ""),
      __fallback: true,
    };
  })();
  const kitOf = (ctx) => (ctx && ctx.kit) || window.MemuSettingsKit || FALLBACK_KIT;

  const POSITIONS = (tr) => [
    ["last", tr("Где оставили", "Where I left it")], ["center", tr("По центру, сверху", "Centered, upper third")],
    ["cursor", tr("У курсора", "Near the cursor")], ["top", tr("У верхнего края", "Top edge")], ["bottom", tr("У нижнего края", "Bottom edge")],
    ["left", tr("Слева", "Left")], ["right", tr("Справа", "Right")], ["top-left", tr("Левый верхний угол", "Top-left corner")],
    ["top-right", tr("Правый верхний угол", "Top-right corner")], ["bottom-left", tr("Левый нижний угол", "Bottom-left corner")],
    ["bottom-right", tr("Правый нижний угол", "Bottom-right corner")],
  ];

  function visionNote(ctx) {
    const { pref } = ctx;
    const tr = (ru, en) => t(ctx, ru, en);
    const models = ctx.models || [];
    const custom = pref("quick_ask_source", "proxy") === "custom";
    const effective = custom ? pref("quick_ask_endpoint_model", "") : (pref("quick_ask_model", "") || pref("default_model", ""));
    const row_ = custom ? null : models.find((m) => m.id === effective);
    const blind = custom ? pref("quick_ask_endpoint_vision", "auto") === "no" : !!(row_ && row_.caps && row_.caps.vision === false);
    return `<p class="qs-vision-hint">${tr("Картинки и снимки экрана видит та же модель, что отвечает: отдельной модели для изображений нет. Если она «только текст», окно скажет об этом.",
      "Pictures and screenshots are seen by the same model that answers — there is no separate image model. If it is text-only, the window says so.")}</p>`
      + (blind ? `<div class="qs-warn" id="qs-blind">${tr("Выбранная модель не видит изображения: снимки экрана с ней работать не будут.", "The selected model cannot see images: screenshots will not work with it.")}</div>` : "");
  }

  function modelOptionsList(ctx, emptyLabel) {
    const models = ctx.models || [];
    return [["", emptyLabel], ...models.map((m) => [m.id, (ctx.modelName ? ctx.modelName(m.id) : m.id) + (m.caps && m.caps.vision === false ? (ctx.en ? " · text only" : " · только текст") : "")])];
  }

  function modelOptions(ctx, chosen, emptyLabel) {
    const { esc } = ctx;
    return modelOptionsList(ctx, emptyLabel).map(([v, l]) => `<option value="${esc(v)}" ${v === chosen ? "selected" : ""}>${esc(l)}</option>`).join("");
  }

  function normPersonalities(data) {
    if (!data || typeof data !== "object") return null;
    const rows = (v) => (Array.isArray(v) ? v.filter((p) => p && typeof p === "object" && p.id != null) : []);
    return { builtin: rows(data.builtin), presets: rows(data.presets) };
  }

  function personalityOptions(ctx) {
    const { esc, pref } = ctx;
    const tr = (ru, en) => t(ctx, ru, en);
    const chosen = pref("quick_ask_personality", "");
    const data = normPersonalities(state.personalities);
    const stored = pref("voice_live_personality_presets", []);
    const builtin = data ? data.builtin : [];
    const presets = data ? data.presets : (Array.isArray(stored) ? stored : []).filter((p) => p && p.id != null)
      .map((p) => ({ id: "preset:" + p.id, name: p.name }));
    const opt = (id, name) => `<option value="${esc(id)}" ${id === chosen ? "selected" : ""}>${esc(name)}</option>`;
    return opt("", tr("Нейтральный помощник", "Neutral assistant"))
      + (builtin.length ? `<optgroup label="${esc(tr("Личности", "Personalities"))}">${builtin.map((p) => opt(p.id, (ctx.en ? p.name_en : p.name_ru) || p.name || String(p.id))).join("")}</optgroup>` : "")
      + (presets.length ? `<optgroup label="${esc(tr("Мои пресеты (общие с MemuAI Live)", "My presets (shared with MemuAI Live)"))}">${presets.map((p) => opt(p.id, p.name || String(p.id))).join("")}</optgroup>` : "")
      + (chosen && !builtin.some((p) => p.id === chosen) && !presets.some((p) => p.id === chosen) ? opt(chosen, chosen) : "");
  }

  const ICONS = {
    bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/></svg>',
    window: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 10h6M7 14h4"/></svg>',
    cmd: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m5 8 4 4-4 4M12 16h7"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>',
    face: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5c1 1.2 2.2 1.8 3.5 1.8s2.5-.6 3.5-1.8M9 10h.01M15 10h.01"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/></svg>',
    text: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 6h14M5 12h9M5 18h6"/><path d="m15 15 2 5 1.2-2.3L20.5 16.5z"/></svg>',
    plug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5"/></svg>',
  };

  function html(ctx) {
    const { pref, esc } = ctx;
    const K = kitOf(ctx);
    const tr = (ru, en) => t(ctx, ru, en);
    const fallback = pref("default_model", "");
    const fallbackName = fallback ? (ctx.modelName ? ctx.modelName(fallback) : fallback) : tr("не выбрана", "not set");
    const hotkey = pref("quick_ask_hotkey", "Alt+Space");
    const source = pref("quick_ask_source", "proxy") === "custom" ? "custom" : "proxy";
    const count = (ctx.models || []).length;
    const epModel = pref("quick_ask_endpoint_model", "");
    const epAdv = pref("quick_ask_endpoint_advanced_model", "");

    const basics = K.section({ id: "quick-basics", title: tr("Основное", "Basics"), icon: ICONS.bolt,
      aside: `<span class="qs-pill" id="qs-pill" data-state="loading" role="status" aria-live="polite"><i></i><span>${tr("Проверяю…", "Checking…")}</span></span>`,
      body:
        K.row({ label: tr("Быстрый доступ", "Quick Ask"), desc: tr("Маленькое окно поверх любой программы: спросили — получили ответ — вернулись к работе.", "A small window above any program: ask, get the answer, get back to work."),
          control: K.toggle("st-qa-enabled", pref("quick_ask_enabled", true)), key: "quick_ask_enabled", def: true, keywords: "quick ask быстрый доступ alt space окно включить" })
      + K.row({ label: tr("Сочетание клавиш", "Hotkey"), when: "st-qa-enabled", keywords: "hotkey горячая клавиша сочетание alt space",
          desc: tr("Нажмите на поле и затем нужные клавиши. Esc — отмена.", "Click the field, then press the keys. Esc cancels."),
          extra: `<div class="qs-hotkey-msg" id="qs-hotkey-msg" role="status" aria-live="polite"></div><div class="qs-alts" id="qs-alts" hidden></div>`,
          control: `<span class="qs-inline"><button type="button" class="qs-capture" id="st-qa-hotkey" data-hotkey="${esc(hotkey)}" aria-label="${tr("Изменить сочетание", "Change hotkey")}">${keycaps(hotkey, esc)}</button>
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-hotkey-default" ${hotkey === "Alt+Space" ? "hidden" : ""} title="${tr("Вернуть Alt+Space", "Back to Alt+Space")}">Alt+Space</button>
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-try">${tr("Открыть", "Open")}</button></span>` })
      + K.row({ label: tr("Откуда модели", "Where models come from"), keywords: "model source endpoint proxy api openai ollama lm studio эндпоинт источник модели свой",
          desc: source === "proxy" ? tr("Как в чатах MemuAI: модели и ключи настраиваются в MemuAI Proxy.", "Like MemuAI chats: models and keys are set up in MemuAI Proxy.")
            : tr("Любой OpenAI-совместимый сервер: Ollama, LM Studio, OpenRouter, OpenAI, свой шлюз.", "Any OpenAI-compatible server: Ollama, LM Studio, OpenRouter, OpenAI, your own gateway."),
          id: "qs-source-row", control: K.segmented("qa-source", [["proxy", tr("MemuAI Proxy", "MemuAI Proxy")], ["custom", tr("Свой эндпоинт", "Own endpoint")]], source, { id: "st-qa-source" }) })

      + `<div class="qs-model-block" id="qs-model-block">`
      + K.row({ label: tr("Модель", "Model"), when: "@qa-source=proxy", keywords: "model модель основная быстрая default",
          desc: count ? tr(`Отвечает на всё. Лучше быстрая и недорогая. Доступно в Proxy: ${count}.`, `Answers everything; a fast, cheap one fits best. Available in Proxy: ${count}.`)
            : tr("В MemuAI Proxy пока нет моделей — добавьте провайдера в Proxy.", "MemuAI Proxy has no models yet — add a provider there."),
          control: K.select("st-qa-model", modelOptionsList(ctx, tr("Как в чатах", "As in chats") + " · " + fallbackName), pref("quick_ask_model", ""), { width: 260 }) })

      + K.field({ label: tr("Адрес эндпоинта", "Endpoint address"), forId: "st-qa-ep-url", when: "@qa-source=custom", keywords: "url base endpoint адрес",
          control: K.input("st-qa-ep-url", pref("quick_ask_endpoint_url", ""), { placeholder: "http://localhost:11434/v1", mono: true, attrs: 'spellcheck="false" autocomplete="off"' }),
          help: tr("Базовый адрес OpenAI-совместимого API, обычно заканчивается на /v1. Запросы идут на …/chat/completions и …/models.", "The base address of an OpenAI-compatible API, usually ending with /v1. Requests go to …/chat/completions and …/models.") })
      + K.field({ label: tr("API-ключ", "API key"), forId: "st-qa-ep-key", when: "@qa-source=custom", keywords: "api key ключ токен",
          control: `<div class="qs-key-line">${K.secret("st-qa-ep-key", "", { placeholder: tr("Не нужен для локальных серверов", "Not needed for local servers") })}
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-ep-key-save">${tr("Сохранить", "Save")}</button>
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-ep-key-clear" hidden>${tr("Удалить", "Remove")}</button></div>`,
          help: `<span id="qs-key-status">${tr("Ключ хранится зашифрованным для вашей учётной записи Windows и никуда не отправляется, кроме этого адреса.", "The key is stored encrypted for your Windows account and sent nowhere but this address.")}</span>` })
      + K.row({ label: tr("Модель", "Model"), when: "@qa-source=custom", id: "qs-ep-model-row", keywords: "model модель endpoint эндпоинт",
          desc: `<span id="qs-ep-models-note">${tr("Выберите из списка сервера или впишите имя.", "Pick from the server list or type a name.")}</span>`,
          control: `<span class="qs-inline">${K.input("st-qa-ep-model", epModel, { placeholder: "llama3.1:8b", mono: true, attrs: 'list="qs-ep-models" spellcheck="false" autocomplete="off"', width: 220 })}
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-ep-refresh" title="${tr("Получить список моделей с сервера", "Fetch the model list from the server")}">${tr("Обновить", "Refresh")}</button></span><datalist id="qs-ep-models"></datalist>` })
      + `<div data-sx-when="@qa-source=custom" class="qs-ep-test">${K.actions(`<span class="qs-test-result" id="qs-ep-result" role="status" aria-live="polite"></span><button type="button" class="pbtn sx-btn-sm" id="st-qa-ep-test">${tr("Проверить подключение", "Test connection")}</button>`)}</div>`
      + `</div>`
      + K.row({ label: tr("Длина ответа", "Answer length"), keywords: "style стиль кратко подробно длина",
          desc: tr("Быстрый доступ отвечает сразу по делу, без вступлений.", "Quick Ask answers straight to the point, no preamble."),
          control: K.segmented("qa-style", [["brief", tr("Кратко", "Brief")], ["balanced", tr("Обычно", "Balanced")], ["detailed", tr("Подробно", "Detailed")]], pref("quick_ask_style", "brief"), { id: "st-qa-style" }),
          key: "quick_ask_style", def: "brief" })
      + K.more({ id: "quick-models-more", title: tr("Умная модель и картинки", "Smart model and images"), hint: tr("для сложных задач", "for hard tasks"), body:
          K.row({ label: tr("Умная модель", "Smart model"), when: "@qa-source=proxy", keywords: "advanced smart умная сложные задачи routing",
            desc: tr("Для длинного кода, рассуждений и разбора страниц. Быстрая модель сама передаёт ей такие вопросы.", "For long code, reasoning and page analysis. The fast model hands such questions over itself."),
            control: K.select("st-qa-advanced", modelOptionsList(ctx, tr("Не нужна", "None")), pref("quick_ask_advanced_model", ""), { width: 260 }) })
        + K.row({ label: tr("Умная модель", "Smart model"), when: "@qa-source=custom", keywords: "advanced smart умная endpoint",
            desc: tr("Модель того же эндпоинта для сложных задач; пусто — не нужна.", "A model of the same endpoint for hard tasks; empty — none."),
            control: K.input("st-qa-ep-advanced", epAdv, { placeholder: tr("не нужна", "none"), mono: true, attrs: 'list="qs-ep-models" spellcheck="false" autocomplete="off"', width: 220 }) })
        + K.row({ label: tr("Кто отвечает", "Who answers"), id: "qs-routing-row", keywords: "routing auto fast smart авто быстро умно alt r",
            desc: tr("Авто — быстрая, сложное уходит умной. В окне — Alt+R.", "Auto — the fast one, hard tasks go to the smart one. Alt+R in the window."),
            control: K.segmented("qa-routing", [["auto", tr("Авто", "Auto")], ["fast", tr("Быстрая", "Fast")], ["advanced", tr("Умная", "Smart")]], pref("quick_ask_routing", "auto"), { id: "st-qa-routing" }),
            extra: "", key: "quick_ask_routing", def: "auto" })
        + K.row({ label: tr("Модель видит картинки", "The model sees images"), when: "@qa-source=custom", keywords: "vision images картинки снимки зрение",
            desc: tr("«Авто» — по названию модели (каталог возможностей MemuAI).", "«Auto» — by the model name (MemuAI capability catalogue)."),
            control: K.select("st-qa-ep-vision", [["auto", tr("Авто", "Auto")], ["yes", tr("Да", "Yes")], ["no", tr("Нет, только текст", "No, text only")]], pref("quick_ask_endpoint_vision", "auto"), { width: 180 }) })
        + `<div class="qs-vision" id="qs-vision-note">${visionNote(ctx)}</div>` }) });

    const behaviour = K.section({ id: "quick-behaviour", title: tr("Поведение", "Behaviour"), icon: ICONS.window, body:
        K.row({ label: tr("Где открывать", "Where it opens"), keywords: "position место позиция где открывать угол край",
          desc: tr("Окно можно перетащить; у края экрана оно прилипает.", "Drag the window anywhere; it docks at screen edges."),
          control: K.select("st-qa-position", POSITIONS(tr), pref("quick_ask_position", "last"), { width: 220 }), key: "quick_ask_position", def: "last" })

      + K.row({ label: tr("Монитор", "Monitor"), keywords: "monitor монитор экран screen display несколько мониторов multi-monitor второй",
          desc: tr("Там же появляются ваши панели и кнопки. Запомненное место применяется на выбранном мониторе.",
            "Your panels and buttons appear there too. The remembered spot is applied on the chosen monitor."),
          control: K.select("st-qa-monitor", [["cursor", tr("С курсором мыши", "With the mouse cursor")], ["window", tr("С активным окном", "With the active window")],
            ["primary", tr("Основной", "Primary")]], pref("quick_ask_monitor", "cursor"), { width: 220 }), key: "quick_ask_monitor", def: "cursor" })

      + K.row({ label: tr("Щелчок в другой программе", "Click in another program"), keywords: "pin закрепить поверх on top blur фокус focus назад behind esc уходит пропадает disappears",
          desc: tr("Окно с панелями и кнопками закрывается только по Esc, повторным сочетанием или кнопкой.",
            "The window with its panels and buttons closes only with Esc, the hotkey again or a button."),
          control: K.select("st-qa-leave", [["stay", tr("Оставаться поверх", "Stay on top")], ["back", tr("Уходить назад", "Step back")]],
            pref("quick_ask_on_leave", "stay"), { width: 220 }), key: "quick_ask_on_leave", def: "stay" })

      + K.row({ label: tr("Держать окно наготове", "Keep the window ready"), keywords: "memory память ram warm тёплое быстрый запуск renderer процесс",
          desc: tr("Скрытое окно занимает отдельный процесс WebView2. «Авто» — мгновенно открывается, а после 30 минут без использования освобождает память.",
            "The hidden window is a separate WebView2 process. “Auto” opens instantly and frees its memory after 30 minutes unused."),
          control: K.select("st-qa-warm", [["auto", tr("Авто", "Auto")], ["always", tr("Всегда (быстрее)", "Always (fastest)")], ["never", tr("Экономить память", "Save memory")]],
            pref("quick_ask_warm", "auto"), { width: 220 }), key: "quick_ask_warm", def: "auto" })
      + K.row({ label: tr("Личность", "Personality"), keywords: "personality личность характер пресет",
          desc: `<span id="qs-persona-desc"></span>`,
          control: `<span class="qs-inline">${`<select class="pinput compact sx-select" id="st-qa-personality" style="margin:0;width:220px">${personalityOptions(ctx)}</select>`}
            <button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-persona-lib" title="${tr("Создать или изменить пресет личности", "Create or edit a personality preset")}">${tr("Библиотека", "Library")}</button></span>` })
      + K.row({ label: tr("Имя помощника", "Assistant name"), keywords: "name имя",
          desc: tr("Как помощник называет себя.", "How the assistant calls itself."),
          control: K.input("st-qa-name", pref("quick_ask_name", ""), { placeholder: "MemuAI", attrs: 'maxlength="80"', width: 220 }) })
      + K.field({ label: tr("Что помощнику знать о вас", "What the assistant should know about you"), forId: "st-qa-instructions", keywords: "instructions инструкции о себе",
          control: K.textarea("st-qa-instructions", pref("quick_ask_instructions", ""), { rows: 3, attrs: 'maxlength="4000"',
            placeholder: tr("Например: я в Москве, валюта — рубли; код — на Python; обращайся на «ты».", "E.g.: I live in Berlin, currency is EUR; code in Python; keep it casual.") }),
          help: `${tr("Добавляется к каждому вопросу.", "Added to every question.")} <span class="qs-count" id="qs-instr-count"></span>` }) });

    const extensions = K.section({ id: "quick-extensions", title: tr("Возможности", "Features"), icon: ICONS.cmd,
      desc: tr("Необязательные возможности. Всё работает и без них.", "Optional features. Everything works without them."),
      body: `<div id="qs-plugin-links">${K.link({ label: tr("Выделенный текст", "Selected text"), sub: "quick-selection", icon: ICONS.text, keywords: "selection выделение выделенный текст перевести исправить ctrl c",
          desc: tr("Перевести, объяснить, исправить выделенное в любой программе", "Translate, explain, fix the selection in any program"), value: pref("quick_ask_selection", true) ? "" : tr("выкл", "off") })}
        ${K.link({ label: tr("Команды", "Commands"), sub: "quick-commands", icon: ICONS.cmd, desc: tr("«Открой телеграм», «громкость 30», свои сценарии", "“Open Telegram”, “volume 30”, your own routines") })}
        ${K.link({ label: tr("Голос", "Voice"), sub: "quick-voice", icon: ICONS.mic, desc: tr("Говорить вместо того, чтобы печатать; озвучка ответов", "Speak instead of typing; spoken answers") })}
        ${K.link({ label: tr("Компаньон", "Companion"), sub: "quick-companion", icon: ICONS.face, desc: tr("Живой персонаж рядом с ответом", "An animated character next to the answer") })}</div>` });

    const advanced = K.section({ id: "quick-advanced", title: tr("Дополнительно", "Advanced"), icon: ICONS.gear, body:
        K.row({ label: tr("Веб-поиск", "Web search"), keywords: "web search поиск интернет",
          desc: tr("Свежие данные из интернета, как в чатах MemuAI.", "Fresh data from the web, like MemuAI chats."), control: K.toggle("st-qa-web", pref("quick_ask_web_search", true)), key: "quick_ask_web_search", def: true })
      + K.row({ label: tr("Страница в браузере", "Browser page"), keywords: "browser page страница вкладка браузер chrome edge firefox",
          desc: tr("Знает, какая вкладка открыта, — можно спросить «перескажи эту страницу».", "Knows which tab is open — ask “summarize this page”."), control: K.toggle("st-qa-browser", pref("quick_ask_browser", true)), key: "quick_ask_browser", def: true })
      + K.more({ id: "quick-advanced-more", title: tr("Ещё", "More"), hint: tr("снимки, история, стекло", "screenshots, history, glass"), body:
          K.row({ label: tr("Снимок экрана по просьбе", "Screenshot on request"), keywords: "screenshot снимок экрана инструмент",
            desc: tr("Модель сама делает снимок, когда вы спрашиваете о том, что на экране. Окна MemuAI на него не попадают.", "The model takes a screenshot when you ask about the screen. MemuAI windows never appear on it."), control: K.toggle("st-qa-shot", pref("quick_ask_screenshot_tool", true)), key: "quick_ask_screenshot_tool", def: true })
        + K.row({ label: tr("Снимок при открытии", "Screenshot on open"), keywords: "auto screenshot снимок открытии",
            desc: tr("Сразу прикрепляет снимок экрана под курсором.", "Attaches a screenshot of the screen under the cursor right away."), control: K.toggle("st-qa-autoshot", pref("quick_ask_auto_screenshot", false)), key: "quick_ask_auto_screenshot", def: false })
        + K.row({ label: tr("История и чаты", "History and chats"), keywords: "history история чаты очистить",
            desc: tr("Хранятся отдельно от чатов MemuAI, для каждого профиля.", "Kept apart from MemuAI chats, per profile."),
            control: `<span class="qs-inline">${K.toggle("st-qa-history", pref("quick_ask_history", true))}<button type="button" class="pbtn ghost sx-btn-sm" id="st-qa-history-clear">${tr("Очистить", "Clear")}</button></span>`, key: "quick_ask_history", def: true })
        + K.row({ label: tr("Эффект стекла", "Glass effect"), keywords: "backdrop acrylic mica стекло акрил слюда",
            desc: tr("Системный фон Windows 11; в Windows 10 — свой.", "The Windows 11 system backdrop; Windows 10 uses its own."),
            control: K.select("st-qa-backdrop", [["auto", tr("Авто", "Auto")], ["acrylic", tr("Акрил", "Acrylic")], ["mica", tr("Слюда", "Mica")], ["off", tr("Выключен", "Off")]], pref("quick_ask_backdrop", "auto"), { width: 160 }), key: "quick_ask_backdrop", def: "auto" }) }) });

    const subs = K.sub({ id: "quick-selection", title: tr("Выделенный текст", "Selected text"), desc: tr("Выделите текст в любой программе и нажмите сочетание быстрого доступа.", "Select text in any program and press the Quick Ask hotkey."),
        body: `<div class="qs-sub-body" id="qs-sub-selection"></div>` })
      + K.sub({ id: "quick-commands", title: tr("Команды", "Commands"), desc: tr("Пишите или говорите обычными фразами — частые команды выполняются мгновенно, без модели.", "Type or say plain phrases — common commands run instantly, without a model."),
        body: `<div class="qs-sub-body" id="qs-sub-commands"></div>` })
      + K.sub({ id: "quick-voice", title: tr("Голос", "Voice"), desc: tr("Удерживайте микрофон или Ctrl+Space и говорите. Esc прерывает.", "Hold the mic or Ctrl+Space and speak. Esc interrupts."),
        body: `<div class="qs-sub-body" id="qs-sub-voice"></div>` })
      + K.sub({ id: "quick-companion", title: tr("Компаньон", "Companion"), desc: tr("Персонажа можно перетащить куда угодно — даже за пределы окна, изменить размер и закрепить.", "Drag the character anywhere — even outside the window, resize and pin it."),
        body: `<div class="qs-sub-body" id="qs-sub-companion"></div>` })
      + `<div id="qs-plugin-subs"></div>`;

    return `<div class="qs-settings" id="qs-settings" data-kit="${K.__fallback ? "fallback" : "sx"}">${basics}${behaviour}${extensions}${advanced}${subs}<div class="qs-plugins" id="qs-plugins" hidden></div></div>`;
  }

  const PILL = {
    loading: ["Проверяю…", "Checking…"], registered: ["Работает", "Active"], off: ["Выключено", "Off"],
    busy: ["Сочетание занято", "Hotkey taken"], failed: ["Не зарегистрировано", "Not registered"],
    unsupported: ["Только в приложении Windows", "Windows app only"], conflict: ["Возможен конфликт", "Possible conflict"],
    capturing: ["Слушаю сочетание…", "Listening…"],
  };

  function renderStatus() {
    const ctx = state.ctx, s = state.status;
    const pill = document.getElementById("qs-pill");
    if (!ctx || !pill) return;
    const tr = (ru, en) => t(ctx, ru, en);
    const enabled = ctx.pref("quick_ask_enabled", true);
    let phase = "loading";
    if (state.capturing) phase = "capturing";
    else if (s) {
      const hk = s.status && s.status.hotkey || {};
      if (!s.status || s.status.native === false) phase = "unsupported";
      else if (!enabled) phase = "off";
      else if (hk.ok) phase = (s.status.conflicts || []).length ? "conflict" : "registered";
      else phase = hk.code === "busy" ? "busy" : (hk.code === "off" ? "off" : "failed");
    }
    pill.dataset.state = phase;
    const hotkey = ctx.pref("quick_ask_hotkey", "Alt+Space");
    pill.querySelector("span").textContent = (PILL[phase] || PILL.failed)[ctx.en ? 1 : 0] + (phase === "registered" ? ` · ${hotkey}` : "");
    const msg = document.getElementById("qs-hotkey-msg");
    const alts = document.getElementById("qs-alts");
    if (!msg) return;
    const lines = [];
    const status = s && s.status || {};
    const hk = status.hotkey || {};
    if (state.probe && state.probe.error) lines.push(`<div class="qs-err">${ctx.esc(state.probe.error)}</div>`);
    if (enabled && status.native && !hk.ok && hk.error && !state.capturing) {
      lines.push(`<div class="qs-err">${ctx.esc(hk.error)}${hk.other_instance ? " " + tr("Похоже, запущена ещё одна копия MemuAI — закройте её.", "Another copy of MemuAI seems to be running — close it.") : ""}</div>`);
    }
    if (hk.rejected_error) lines.push(`<div class="qs-err">${ctx.esc(hk.rejected_error)} ${tr("Оставлено прежнее", "Kept")} ${ctx.esc(hk.hotkey)}.</div>`);
    for (const item of status.conflicts || []) {
      lines.push(`<div class="qs-warn">${tr(`Сейчас запущен ${ctx.esc(item.program)}: по умолчанию он тоже использует ${ctx.esc(item.hotkey)}. Если окно не открывается — отключите это сочетание в ${ctx.esc(item.program)} или выберите другое ниже.`,
        `${ctx.esc(item.program)} is running and uses ${ctx.esc(item.hotkey)} by default too. If the window does not open, turn that shortcut off in ${ctx.esc(item.program)} or choose another below.`)}</div>`);
    }

    const infos = ((state.probe && state.probe.warnings) || status.warnings || []).filter((w) => w && w.text);
    if (infos.length) {
      lines.push(`<details class="qs-why" id="qs-why"${state.whyOpen ? " open" : ""}><summary>${infos.length === 1 && infos[0].code === "system_menu"
        ? tr("Что станет с системным меню Alt+Space?", "What happens to the Alt+Space window menu?")
        : tr("Особенности этого сочетания", "About this combination")}</summary>${infos.map((w) => `<p>${ctx.esc(w.text)}</p>`).join("")}</details>`);
    }
    msg.innerHTML = lines.join("");
    const why = document.getElementById("qs-why");
    if (why) why.addEventListener("toggle", () => { state.whyOpen = why.open; });
    const needAlts = enabled && status.native && !state.capturing && (!hk.ok || (status.conflicts || []).length || hk.rejected_error);
    alts.hidden = !needAlts;
    if (needAlts) {
      const list = (s.alternatives || []).filter((item) => item !== hotkey);
      const probed = state.altProbe || {};
      alts.innerHTML = `<span class="td">${tr("Другие сочетания:", "Other combinations:")}</span>` + list.map((item) => {
        const free = probed[item];
        return `<button type="button" class="qs-alt ${free === false ? "taken" : ""}" data-hotkey="${ctx.esc(item)}" ${free === false ? "disabled" : ""}
          title="${free === false ? tr("Занято другой программой", "Taken by another program") : free ? tr("Свободно", "Free") : ""}">${keycaps(item, ctx.esc)}${free ? '<span class="qs-free">✓</span>' : ""}</button>`;
      }).join("");
      alts.querySelectorAll("[data-hotkey]").forEach((btn) => { btn.onclick = () => applyHotkey(btn.dataset.hotkey); });
      if (!state.altProbe && !state.altProbing) {

        state.altProbing = true;
        ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "probe_alternatives", hotkey: hotkey } })
          .then((result) => {
            state.altProbe = {};
            for (const r of result.alternatives || []) if (r && r.hotkey) state.altProbe[r.hotkey] = !!r.ok;
          }).catch(() => { state.altProbe = {}; })
          .finally(() => { state.altProbing = false; renderStatus(); });
      }
    }
  }

  async function refresh() {
    const ctx = state.ctx;
    if (!ctx) return;
    const token = ++state.refreshToken;
    let status;
    try { status = await ctx.api("/api/quick/config"); }
    catch (e) { status = { status: { native: false, hotkey: {} }, alternatives: [] }; }
    if (token !== state.refreshToken) return;
    state.status = status;
    renderStatus();
  }

  async function applyHotkey(text) {
    const ctx = state.ctx;
    if (!ctx || state.busy) return;
    state.busy = true;
    try {
      const result = await ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "apply", hotkey: text, lang: ctx.en ? "en" : "ru" } });
      state.probe = result.ok ? { warnings: result.warnings || [] } : result;
      if (result.prefs_hotkey) {
        if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_hotkey", result.prefs_hotkey);
        const field = document.getElementById("st-qa-hotkey");
        if (field) { field.dataset.hotkey = result.prefs_hotkey; field.innerHTML = keycaps(result.prefs_hotkey, ctx.esc); }
        const def = document.getElementById("st-qa-hotkey-default");
        if (def) def.hidden = result.prefs_hotkey === "Alt+Space";
      }
      if (result.ok) ctx.toast(t(ctx, "Сочетание сохранено: ", "Hotkey saved: ") + result.hotkey, "ok");
      else if (result.error) ctx.toast(result.error, "err");
    } catch (e) {
      ctx.toast(e.message || String(e), "err");
    } finally {
      state.busy = false;
      state.altProbe = null;
      await refresh();
    }
  }

  function startCapture(field) {
    const ctx = state.ctx;
    if (state.capturing || !ctx) return;
    state.capturing = true;
    state.probe = null;
    const controller = new AbortController();
    state.captureController = controller;
    const held = new Set();
    const pressed = new Set();
    let finished = false;
    field.classList.add("capturing");
    const idle = `<span class="qs-capture-hint">${t(ctx, "Нажмите сочетание…", "Press the combination…")}</span>`;
    field.innerHTML = idle;
    renderStatus();
    ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "suspend", seconds: 20 } }).catch(() => null);
    const native = ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "capture", timeout: 15 } }).catch(() => null);
    native.then((result) => {
      if (finished || !result) return;
      if (result.ok && result.hotkey) stop(result.hotkey);
      else if (result.code === "cancelled") stop("");
      else if (result.code && result.code !== "unsupported" && result.code !== "timeout" && result.error) stop("", result.error);
    });
    const timer = setTimeout(() => stop(""), 16000);
    const paint = () => {
      const mods = MOD_ORDER.filter((m) => held.has(m));
      field.innerHTML = mods.length ? keycaps(mods.join("+"), ctx.esc) + '<span class="qs-plus">+</span><span class="qs-capture-hint">…</span>' : idle;
    };
    function stop(value, error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      state.capturing = false;
      controller.abort();
      if (state.captureController === controller) state.captureController = null;
      field.classList.remove("capturing");
      field.innerHTML = keycaps(field.dataset.hotkey, ctx.esc);
      ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "cancel_capture" } }).catch(() => null);
      if (value && value !== field.dataset.hotkey) {
        applyHotkey(value).finally(() => ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "resume" } }).catch(() => null).then(refresh));
      } else {
        ctx.api("/api/quick/hotkey", { method: "POST", body: { action: "resume" } }).catch(() => null).then(refresh);
        if (error) { state.probe = { error }; }
        renderStatus();
      }
    }
    const finish = (e) => {
      const combo = hotkeyFromEvent(e, held);
      if (!combo.key) return false;
      stop(combo.text);
      return true;
    };
    const onKey = (e) => {
      e.preventDefault(); e.stopPropagation();
      if (MOD_CODES[e.code]) { held.add(MOD_CODES[e.code]); paint(); return; }
      if (e.key === "Escape" && !held.size && !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey) { stop(""); return; }
      pressed.add(e.code);
      finish(e);
    };
    const onUp = (e) => {
      e.preventDefault(); e.stopPropagation();

      if (!MOD_CODES[e.code] && !pressed.has(e.code) && keyFromCode(e.code) && finish(e)) return;
      if (MOD_CODES[e.code]) { held.delete(MOD_CODES[e.code]); paint(); }
    };
    const options = { capture: true, signal: controller.signal };
    document.addEventListener("keydown", onKey, options);
    document.addEventListener("keyup", onUp, options);
    field.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== field) stop(""); }, 120), { signal: controller.signal });
    window.addEventListener("blur", () => { held.clear(); paint(); }, { signal: controller.signal });
  }

  function describePersonality(ctx) {
    const box = document.getElementById("qs-persona-desc");
    if (!box) return;
    const id = ctx.pref("quick_ask_personality", "");
    const data = normPersonalities(state.personalities);
    if (!id) { box.textContent = t(ctx, "Отвечает прямо и по делу, без характера.", "Answers directly, without a persona."); return; }
    const builtin = data && data.builtin.find((p) => p.id === id);
    const preset = data && data.presets.find((p) => p.id === id);
    box.textContent = builtin ? builtin.behavior : preset ? (t(ctx, "Пресет из библиотеки: ", "Library preset: ") + [preset.ai_name, preset.primary, preset.custom].filter(Boolean).join(" · ")) : "";
  }

  async function loadPersonalities(ctx) {
    try { state.personalities = normPersonalities(await ctx.api("/api/quick/personalities")); } catch (e) { return; }
    const select = document.getElementById("st-qa-personality");
    if (select && state.ctx === ctx) { select.innerHTML = personalityOptions(ctx); describePersonality(ctx); }
  }

  const sections = new Map();
  function ensureMemuQuickStub() {

    const existing = window.MemuQuick;
    if (existing && typeof existing.settingsSection === "function" && !existing.__settingsStub) return;
    const noop = () => () => {};
    window.MemuQuick = Object.assign(existing || {}, {
      version: 1, native: false, __settingsStub: true,
      settingsSection(id, render, options = {}) { sections.set(String(id), { render, options }); renderSections(); },
      registerTool: noop, registerPanel: noop, on: noop, off: noop, emit: () => {}, addComposerButton: () => ({ update() {}, remove() {} }),
    });
  }
  async function loadPluginSettings(ctx) {
    if (state.pluginsLoaded) return;
    state.pluginsLoaded = true;
    ensureMemuQuickStub();
    let rows = [];
    try {
      const response = await fetch("/quick/plugins/index.json", { cache: "no-store" });
      const data = response.ok ? await response.json() : null;
      rows = Array.isArray(data) ? data : (data && Array.isArray(data.plugins) ? data.plugins : []);
    } catch (e) { rows = []; }
    const safe = (file, ext) => typeof file === "string" && new RegExp("^[\\w./-]+\\." + ext + "$").test(file) && !file.includes("..");
    for (const row of rows) {
      if (!row || typeof row !== "object" || row.enabled === false) continue;
      const suffix = row.v ? "?v=" + encodeURIComponent(row.v) : "";
      if (safe(row.settings_css, "css")) {
        const link = document.createElement("link");
        link.rel = "stylesheet"; link.href = "/quick/plugins/" + row.settings_css + suffix;
        document.head.appendChild(link);
      }
      if (!safe(row.settings, "js")) continue;
      await new Promise((resolve) => {
        const script = document.createElement("script");
        script.src = "/quick/plugins/" + row.settings + suffix;
        script.onload = resolve; script.onerror = resolve;
        document.body.appendChild(script);
      });

      const id = String(row.id || "");
      const global = id ? window["Memu" + id.charAt(0).toUpperCase() + id.slice(1).replace(/[-_](\w)/g, (_, c) => c.toUpperCase()) + "Settings"] : null;
      if (id && !sections.has(id) && global && typeof global.mount === "function") {
        sections.set(id, { render: (el, ctx) => global.mount(el, ctx), options: { title: row.title || id, order: +row.order || 0 } });
      }
    }
    renderSections();
  }

  const mounted = new WeakSet();
  function renderSections() {
    const ctx = state.ctx;
    if (!ctx) return;
    const tr = (ru, en) => t(ctx, ru, en);
    const K = kitOf(ctx);
    const into = (id, render, extra = {}) => {
      const el = document.getElementById(id);
      if (!el || mounted.has(el)) return;
      mounted.add(el);
      try { render(el, Object.assign({}, ctx, extra)); } catch (e) { el.textContent = String(e && e.message || e); }
    };
    const commands = window.MemuCommandsSettings;
    if (commands && typeof commands.mount === "function") {
      into("qs-sub-commands", (el, c) => commands.mount(el, c), { part: "commands" });
      into("qs-sub-voice", (el, c) => commands.mount(el, c), { part: "voice" });
    }
    const companion = sections.get("companion");
    if (companion) into("qs-sub-companion", companion.render);

    if (window.MemuQuickSelectionSettings) into("qs-sub-selection", (el, c) => window.MemuQuickSelectionSettings.mount(el, c), { kit: K });
    const links = document.getElementById("qs-plugin-links"), subs = document.getElementById("qs-plugin-subs");
    if (!links || !subs) return;
    for (const [id, section] of Array.from(sections.entries()).sort((a, b) => (+a[1].options.order || 0) - (+b[1].options.order || 0))) {
      if (id === "commands" || id === "companion" || document.getElementById("qs-sub-plugin-" + id)) continue;
      const raw = section.options.title;
      const title = typeof raw === "function" ? raw(ctx) : (raw && typeof raw === "object" ? (ctx.en ? raw.en : raw.ru) || raw.en || raw.ru || id : String(raw || id));

      const desc = section.options.desc && typeof section.options.desc === "object" ? (ctx.en ? section.options.desc.en : section.options.desc.ru) : "";
      links.insertAdjacentHTML("beforeend", K.link({ label: K.esc ? K.esc(title) : title, sub: "quick-plugin-" + id, icon: section.options.icon || ICONS.plug, desc: desc ? (K.esc ? K.esc(desc) : desc) : tr("Расширение быстрого доступа", "A Quick Ask extension") }));
      subs.insertAdjacentHTML("beforeend", K.sub({ id: "quick-plugin-" + id, title, body: `<div class="qs-sub-body" id="qs-sub-plugin-${ctx.esc(id)}"></div>` }));
      into("qs-sub-plugin-" + id, section.render);
    }

    if (!commands) for (const sub of ["quick-commands", "quick-voice"]) { const box = document.getElementById(sub === "quick-commands" ? "qs-sub-commands" : "qs-sub-voice"); if (box && !box.childNodes.length) box.innerHTML = K.note(tr("Модуль команд не загружен.", "The commands module is not loaded."), "warn"); }
  }

  function renderKeyStatus(ctx, key) {
    const tr = (ru, en) => t(ctx, ru, en);
    const status = document.getElementById("qs-key-status"), clear = document.getElementById("st-qa-ep-key-clear"), input = document.getElementById("st-qa-ep-key");
    if (!status) return;
    if (key && key.set) {
      status.textContent = key.scheme === "dpapi"
        ? tr(`Ключ сохранён (${key.hint}), зашифрован для вашей учётной записи Windows.`, `Key saved (${key.hint}), encrypted for your Windows account.`)
        : tr(`Ключ сохранён (${key.hint}). Шифрование DPAPI есть только в Windows.`, `Key saved (${key.hint}). DPAPI encryption is Windows-only.`);
      if (input) input.placeholder = tr("Сохранён · введите новый, чтобы заменить", "Saved · type a new one to replace");
    } else {
      status.textContent = tr("Ключ не задан. Для локальных серверов (Ollama, LM Studio) он обычно не нужен.", "No key. Local servers (Ollama, LM Studio) usually need none.");
      if (input) input.placeholder = tr("Не нужен для локальных серверов", "Not needed for local servers");
    }
    if (clear) clear.hidden = !(key && key.set);
  }
  function fillModelList(ids) {
    const list = document.getElementById("qs-ep-models");
    if (!list) return;
    list.innerHTML = (ids || []).slice(0, 400).map((id) => `<option value="${String(id).replace(/"/g, "&quot;")}"></option>`).join("");
  }
  function showResult(ctx, ok, text) {
    const box = document.getElementById("qs-ep-result");
    if (!box) return;
    box.dataset.tone = ok === null ? "muted" : (ok ? "ok" : "err");
    box.textContent = text || "";
  }

  function bind(ctx) {

    if (state.bindController) state.bindController.abort();
    if (state.captureController) state.captureController.abort();
    state.capturing = false;
    const controller = state.bindController = new AbortController();
    const signal = controller.signal;
    state.ctx = ctx;
    const $ = (id) => document.getElementById(id);
    if (!$("qs-settings")) return;
    const listen = (el, type, fn) => { if (el) el.addEventListener(type, fn, { signal }); };
    const save = async (patch) => {
      try {
        await ctx.savePreferences(patch);
        await ctx.api("/api/quick/apply", { method: "POST", body: {} }).catch(() => null);
      } catch (e) { ctx.toast(e.message || String(e), "err"); }
      refresh();
    };
    const bools = { "st-qa-enabled": "quick_ask_enabled", "st-qa-web": "quick_ask_web_search", "st-qa-shot": "quick_ask_screenshot_tool",
      "st-qa-autoshot": "quick_ask_auto_screenshot",
      "st-qa-history": "quick_ask_history", "st-qa-browser": "quick_ask_browser" };
    for (const [id, key] of Object.entries(bools)) {
      const el = $(id);
      listen(el, "change", () => save({ [key]: !!el.checked }));
    }

    listen($("st-qa-model"), "change", (e) => {
      if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_model", e.target.value);
      save({ quick_ask_model: e.target.value });
      const note = $("qs-vision-note");
      if (note) note.innerHTML = visionNote(ctx);
    });
    listen($("st-qa-advanced"), "change", (e) => {
      if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_advanced_model", e.target.value);
      save({ quick_ask_advanced_model: e.target.value });
      const routing = $("qs-routing-row");
      if (routing) routing.hidden = !e.target.value;
    });
    listen($("st-qa-backdrop"), "change", (e) => save({ quick_ask_backdrop: e.target.value }));
    listen($("st-qa-position"), "change", (e) => save({ quick_ask_position: e.target.value }));
    listen($("st-qa-monitor"), "change", (e) => save({ quick_ask_monitor: e.target.value }));
    listen($("st-qa-leave"), "change", (e) => save({ quick_ask_on_leave: e.target.value }));
    listen($("st-qa-warm"), "change", (e) => save({ quick_ask_warm: e.target.value }));
    listen($("st-qa-personality"), "change", (e) => { save({ quick_ask_personality: e.target.value }); describePersonality(ctx); });
    listen($("st-qa-persona-lib"), "click", () => {
      if (typeof window.showLivePersonalitySettings === "function") window.showLivePersonalitySettings("quick");
      else ctx.toast(t(ctx, "Библиотека личностей — в Настройки → Голос и Live → Личность MemuAI Live", "The personality library is in Settings → Voice & Live → MemuAI Live personality"), "warn");
    });
    let textTimer = 0;
    const saveText = () => {
      clearTimeout(textTimer);
      textTimer = setTimeout(() => {
        const patch = {};
        if ($("st-qa-instructions")) patch.quick_ask_instructions = $("st-qa-instructions").value.trim();
        if ($("st-qa-name")) patch.quick_ask_name = $("st-qa-name").value.trim();
        save(patch);
      }, 450);
    };
    const count = () => { const el = $("st-qa-instructions"), out = $("qs-instr-count"); if (el && out) out.textContent = el.value.length ? `${el.value.length} / 4000` : ""; };
    listen($("st-qa-instructions"), "input", () => { count(); saveText(); });
    listen($("st-qa-name"), "input", saveText);
    count();

    for (const [name, key] of [["qa-style", "quick_ask_style"], ["qa-routing", "quick_ask_routing"]]) {
      document.querySelectorAll(`#qs-settings input[name="${name}"]`).forEach((radio) => listen(radio, "change", () => { if (radio.checked) save({ [key]: radio.value }); }));
    }

    const routingVisible = () => {
      const custom = sourceValue() === "custom";
      const adv = custom ? ($("st-qa-ep-advanced") || {}).value : ($("st-qa-advanced") || {}).value;
      const row = $("qs-routing-row");
      if (row) row.hidden = !String(adv || "").trim();
    };
    const sourceValue = () => { const r = document.querySelector('#qs-settings input[name="qa-source"]:checked'); return r ? r.value : "proxy"; };
    const applyWhenFallback = () => {
      if (!document.querySelector('#qs-settings[data-kit="fallback"]')) return;
      const value = sourceValue();
      document.querySelectorAll('#qs-settings [data-sx-when^="@qa-source="]').forEach((el) => { el.hidden = el.dataset.sxWhen !== "@qa-source=" + value; });
      document.querySelectorAll('#qs-settings [data-sx-when="st-qa-enabled"]').forEach((el) => { el.hidden = !$("st-qa-enabled").checked; });
    };
    const sourceDesc = () => {
      const row = $("qs-source-row"); const desc = row && row.querySelector(".sx-desc, .td");
      if (desc) desc.textContent = sourceValue() === "proxy" ? t(ctx, "Как в чатах MemuAI: модели и ключи настраиваются в MemuAI Proxy.", "Like MemuAI chats: models and keys are set up in MemuAI Proxy.")
        : t(ctx, "Любой OpenAI-совместимый сервер: Ollama, LM Studio, OpenRouter, OpenAI, свой шлюз.", "Any OpenAI-compatible server: Ollama, LM Studio, OpenRouter, OpenAI, your own gateway.");
    };
    document.querySelectorAll('#qs-settings input[name="qa-source"]').forEach((radio) => listen(radio, "change", () => {
      if (!radio.checked) return;
      if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_source", radio.value);
      save({ quick_ask_source: radio.value });
      sourceDesc(); applyWhenFallback(); routingVisible();
      const note = $("qs-vision-note"); if (note) note.innerHTML = visionNote(ctx);
      if (radio.value === "custom") loadEndpoint(false);
    }));
    let urlTimer = 0;
    listen($("st-qa-ep-url"), "input", () => { clearTimeout(urlTimer); urlTimer = setTimeout(() => saveUrl(), 700); });
    listen($("st-qa-ep-url"), "change", () => { clearTimeout(urlTimer); saveUrl(); });
    async function saveUrl() {
      const el = $("st-qa-ep-url");
      if (!el) return;
      const value = el.value.trim();
      el.classList.toggle("qs-invalid", !!value && !/^(https?:\/\/)?[\w.-]+(:\d+)?(\/\S*)?$/i.test(value));
      if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_endpoint_url", value);
      await save({ quick_ask_endpoint_url: value });
    }
    let modelTimer = 0;
    const saveEpModels = () => {
      clearTimeout(modelTimer);
      modelTimer = setTimeout(() => {
        const patch = { quick_ask_endpoint_model: ($("st-qa-ep-model") || {}).value || "", quick_ask_endpoint_advanced_model: ($("st-qa-ep-advanced") || {}).value || "" };
        for (const [k, v] of Object.entries(patch)) { patch[k] = String(v).trim(); if (typeof ctx.syncPreference === "function") ctx.syncPreference(k, patch[k]); }
        save(patch); routingVisible();
      }, 400);
    };
    listen($("st-qa-ep-model"), "input", saveEpModels);
    listen($("st-qa-ep-advanced"), "input", saveEpModels);
    listen($("st-qa-ep-vision"), "change", (e) => {
      if (typeof ctx.syncPreference === "function") ctx.syncPreference("quick_ask_endpoint_vision", e.target.value);
      save({ quick_ask_endpoint_vision: e.target.value });
      const note = $("qs-vision-note"); if (note) note.innerHTML = visionNote(ctx);
    });
    const saveKey = async () => {
      const input = $("st-qa-ep-key");
      if (!input || !input.value.trim()) { ctx.toast(t(ctx, "Введите ключ", "Enter a key"), "warn"); return; }
      try {
        const r = await ctx.api("/api/quick/endpoint", { method: "POST", body: { key: input.value.trim() } });
        input.value = "";
        renderKeyStatus(ctx, r.key);
        ctx.toast(t(ctx, "Ключ сохранён", "Key saved"), "ok");
      } catch (e) { ctx.toast(e.message || String(e), "err"); }
    };
    listen($("st-qa-ep-key-save"), "click", saveKey);
    listen($("st-qa-ep-key"), "keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveKey(); } });
    listen($("st-qa-ep-key-clear"), "click", async () => {
      try { const r = await ctx.api("/api/quick/endpoint", { method: "POST", body: { clear_key: true } }); renderKeyStatus(ctx, r.key); ctx.toast(t(ctx, "Ключ удалён", "Key removed"), "ok"); }
      catch (e) { ctx.toast(e.message || String(e), "err"); }
    });
    async function loadEndpoint(fetchList) {
      try {
        const info = await ctx.api("/api/quick/endpoint");
        renderKeyStatus(ctx, info.key);
        if (info.models && info.models.length) fillModelList(info.models.map((m) => m.id));
      } catch (e) {  }
      if (fetchList) refreshModels(true);
    }
    async function refreshModels(quiet) {
      const url = ($("st-qa-ep-url") || {}).value || "";
      if (!url.trim()) { if (!quiet) showResult(ctx, false, t(ctx, "Сначала укажите адрес эндпоинта.", "Enter the endpoint address first.")); return; }
      const typedKey = ($("st-qa-ep-key") || {}).value || "";
      if (!quiet) showResult(ctx, null, t(ctx, "Получаю список моделей…", "Fetching the model list…"));
      try {
        const r = await ctx.api("/api/quick/endpoint/models", { method: "POST", body: { url: url.trim(), key: typedKey.trim() || undefined, lang: ctx.en ? "en" : "ru" } });
        if (r.ok) {
          fillModelList(r.models);
          const note = $("qs-ep-models-note");
          if (note) note.textContent = r.models.length ? t(ctx, `Сервер вернул моделей: ${r.models.length}. Выберите или впишите имя.`, `The server lists ${r.models.length} models. Pick one or type a name.`)
            : t(ctx, "Сервер не вернул ни одной модели — впишите имя вручную.", "The server listed no models — type a name.");
          if (!quiet) showResult(ctx, true, t(ctx, `Список обновлён: ${r.models.length}`, `List updated: ${r.models.length}`));
          const model = $("st-qa-ep-model");
          if (model && !model.value.trim() && r.models.length) { model.value = r.models[0]; saveEpModels(); }
        } else if (!quiet) showResult(ctx, false, r.error || t(ctx, "Не получилось", "Failed"));
      } catch (e) { if (!quiet) showResult(ctx, false, e.message || String(e)); }
    }
    listen($("st-qa-ep-refresh"), "click", () => refreshModels(false));
    listen($("st-qa-ep-test"), "click", async () => {
      const btn = $("st-qa-ep-test");
      const url = ($("st-qa-ep-url") || {}).value || "";
      if (!url.trim()) { showResult(ctx, false, t(ctx, "Сначала укажите адрес эндпоинта.", "Enter the endpoint address first.")); return; }
      btn.disabled = true;
      showResult(ctx, null, t(ctx, "Проверяю…", "Testing…"));
      try {
        const typedKey = ($("st-qa-ep-key") || {}).value || "";
        const r = await ctx.api("/api/quick/endpoint/test", { method: "POST", body: { url: url.trim(), key: typedKey.trim() || undefined,
          model: (($("st-qa-ep-model") || {}).value || "").trim(), lang: ctx.en ? "en" : "ru" } });
        if (r.models && r.models.length) fillModelList(r.models);
        const chat = (r.steps || []).find((x) => x.step === "chat");
        if (r.ok) showResult(ctx, true, chat ? t(ctx, `Работает: модель ответила за ${(r.ms / 1000).toFixed(1)} с.`, `Works: the model answered in ${(r.ms / 1000).toFixed(1)} s.`)
          : t(ctx, `Сервер отвечает, моделей: ${r.models.length}. Укажите модель, чтобы проверить ответ.`, `The server answers, ${r.models.length} models. Set a model to test an answer.`));
        else showResult(ctx, false, r.error || t(ctx, "Не получилось", "Failed"));
      } catch (e) { showResult(ctx, false, e.message || String(e)); }
      finally { btn.disabled = false; }
    });
    applyWhenFallback();
    routingVisible();
    if (sourceValue() === "custom") loadEndpoint(false);
    const field = $("st-qa-hotkey");
    listen(field, "click", () => startCapture(field));
    listen($("st-qa-hotkey-default"), "click", () => applyHotkey("Alt+Space"));
    listen($("st-qa-try"), "click", async () => {
      try {
        const r = await ctx.api("/api/quick/window", { method: "POST", body: { action: "show" } });
        if (!r.ok) ctx.toast(t(ctx, "Окно быстрого доступа есть только в приложении MemuAI для Windows", "The Quick Ask window exists only in the MemuAI Windows app"), "warn");
      } catch (e) { ctx.toast(e.message || String(e), "err"); }
    });
    listen($("st-qa-history-clear"), "click", async () => {
      try {
        await ctx.api("/api/quick/history", { method: "POST", body: { clear: true } });
        await ctx.api("/api/quick/chats", { method: "POST", body: { clear: true } });
        ctx.toast(t(ctx, "История и чаты быстрого доступа очищены", "Quick Ask history and chats cleared"), "ok");
      }
      catch (e) { ctx.toast(e.message || String(e), "err"); }
    });
    state.probe = null;
    state.altProbe = null;
    describePersonality(ctx);
    renderStatus();
    refresh();
    loadPersonalities(ctx);
    renderSections();
    loadPluginSettings(ctx);
  }

  const TAB_ICON = '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 10h6M7 14h4"/><path d="m17 9-1.5 3h3L17 15"/></svg>';
  window.MemuQuickSettings = { html, bind, hotkeyFromEvent, keyFromCode, icon: TAB_ICON, sections,

    __setPersonalities(data) { state.personalities = normPersonalities(data); } };
})();
