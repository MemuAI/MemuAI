

"use strict";

window.MemuComposite = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = { data: null, cur: null, orig: "", dirty: false, issues: [], sim: null, simBusy: false, sample: null,
              tryRes: null, trying: false, picker: null, collapsed: new Set(), loading: false, simTimer: 0, seq: 0 };
  const errorOf = (e) => (e && e.message ? e.message : String(e || ""));
  const clone = (v) => JSON.parse(JSON.stringify(v));
  const num = (v) => Number(v || 0).toLocaleString(LANG === "en" ? "en-US" : "ru-RU");
  const short = (n) => { n = Number(n) || 0; return n >= 1e6 ? `${+(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n); };

  const KINDS = {
    route: { ru: "Направить", en: "Route", hint: ["Если условия выполнены — отправить запрос в эти модели. Срабатывает первое подходящее правило «Направить».", "When the conditions hold, send the request to these models. The first matching “Route” rule wins."] },
    filter: { ru: "Фильтр", en: "Filter", hint: ["Если условия выполнены — оставить только модели с нужными возможностями.", "When the conditions hold, keep only models with the required capabilities."] },
    prefer: { ru: "Предпочесть", en: "Prefer", hint: ["Если условия выполнены — поставить эти модели в начало очереди.", "When the conditions hold, move these models to the front of the queue."] },
    exclude: { ru: "Исключить", en: "Exclude", hint: ["Если условия выполнены — не использовать эти модели.", "When the conditions hold, do not use these models."] },
    settings: { ru: "Настройки", en: "Settings", hint: ["Если условия выполнены — изменить таймауты, повторы и поводы для перехода к следующей модели.", "When the conditions hold, change timeouts, retries and fallback triggers."] },
  };
  const COND = {
    has_image: ["Есть картинка", "Has an image"], has_audio: ["Есть аудио", "Has audio"], has_file: ["Есть файл", "Has a file"],
    tokens: ["Размер запроса (токенов)", "Request size (tokens)"], needs_tools: ["Нужны инструменты", "Needs tools"],
    tool_choice: ["Выбор инструмента (tool_choice)", "Tool choice (tool_choice)"], json_mode: ["Ответ в JSON", "JSON answer"],
    stream: ["Потоковый ответ", "Streaming answer"], reasoning: ["Запрошены размышления", "Reasoning requested"],
    effort: ["Уровень размышлений", "Reasoning effort"], last_user: ["Последнее сообщение", "Last user message"],
    system: ["Системный промпт", "System prompt"], client: ["Кто вызывает", "Who calls"], api_key: ["Ключ API (метка)", "API key (tag)"],
    user_agent: ["Программа (User-Agent)", "Program (User-Agent)"], endpoint: ["Формат API", "API format"],
    time: ["Время суток", "Time of day"], weekday: ["День недели", "Weekday"], language: ["Язык сообщения", "Message language"],
    max_tokens: ["max_tokens", "max_tokens"], temperature: ["temperature", "temperature"], turns: ["Сообщений в диалоге", "Messages in the chat"],
    random: ["Случайно, % запросов", "Random, % of requests"],
    difficulty: ["Сложность задачи", "Task difficulty"],
  };
  const COND_GROUPS = [
    [["Содержимое", "Content"], ["has_image", "has_audio", "has_file", "tokens", "difficulty", "last_user", "system", "language", "turns"]],
    [["Параметры запроса", "Request options"], ["needs_tools", "tool_choice", "json_mode", "stream", "reasoning", "effort", "max_tokens", "temperature"]],
    [["Кто и когда", "Who and when"], ["client", "api_key", "user_agent", "endpoint", "time", "weekday", "random"]],
  ];
  const OPS = {
    gt: ["больше", "more than"], gte: ["не меньше", "at least"], lt: ["меньше", "less than"], lte: ["не больше", "at most"],
    eq: ["равно", "equals"], ne: ["не равно", "not equal"], contains: ["содержит", "contains"], not_contains: ["не содержит", "does not contain"],
    regex: ["regex совпадает", "matches regex"], not_regex: ["regex не совпадает", "does not match regex"], starts_with: ["начинается с", "starts with"],
    equals: ["равен", "equals"], in: ["одно из", "one of"], not_in: ["ни одно из", "none of"], between: ["с … до", "from … to"],
    not_between: ["вне интервала", "outside"], chance: ["вероятность", "chance"], is: ["", ""],
  };
  const OPTS = {
    tool_choice: { auto: ["auto", "auto"], none: ["none", "none"], required: ["required", "required"], function: ["конкретная функция", "a named function"], unset: ["не задан", "not set"] },
    effort: { off: ["выкл.", "off"], minimal: ["minimal", "minimal"], low: ["low", "low"], medium: ["medium", "medium"], high: ["high", "high"], xhigh: ["xhigh", "xhigh"], max: ["max", "max"], auto: ["auto", "auto"], unset: ["не задан", "not set"] },
    client: { memuai: ["окно чата, MemuCode", "chat window, MemuCode"], api: ["программы через API", "programs via API"], public: ["публичный доступ", "public access"], internal: ["Proxy (расписание, Telegram)", "Proxy (schedule, Telegram)"] },
    endpoint: { openai: ["OpenAI /v1/chat", "OpenAI /v1/chat"], anthropic: ["Anthropic /v1/messages", "Anthropic /v1/messages"], responses: ["/v1/responses", "/v1/responses"], panel: ["панель / окно чата", "panel / chat window"], internal: ["внутренний", "internal"] },
    weekday: { 1: ["Пн", "Mon"], 2: ["Вт", "Tue"], 3: ["Ср", "Wed"], 4: ["Чт", "Thu"], 5: ["Пт", "Fri"], 6: ["Сб", "Sat"], 7: ["Вс", "Sun"] },
    language: { ru: ["русский", "Russian"], en: ["английский", "English"], zh: ["китайский/японский", "Chinese/Japanese"], other: ["другой", "other"] },
    difficulty: { simple: ["простой вопрос", "simple question"], chat: ["болтовня", "small talk"], translation: ["перевод", "translation"],
      summary: ["пересказ", "summary"], creative: ["творческая", "creative"], code: ["код", "code"], complex_code: ["сложный код", "complex code"],
      deep_math: ["сложная математика", "deep maths"], architecture: ["архитектура", "architecture"] },
  };
  const STRAT = {
    fixed: ["По порядку", "In order"], fastest: ["Самая быстрая по замерам", "Fastest by measurements"], cheapest: ["Самая дешёвая", "Cheapest"],
    success: ["Самая надёжная (успешные ответы)", "Most reliable (successful answers)"], round_robin: ["По кругу", "Round-robin"],
    weighted: ["Случайно по весам", "Weighted random"], least_used: ["Меньше всего израсходована", "Least used"], sticky: ["Закрепить за беседой", "Sticky per conversation"],
  };
  const STRAT_HINT = {
    fixed: ["A → B → C: следующая — только если предыдущая не ответила.", "A → B → C: the next one only if the previous failed."],
    fastest: ["Первой идёт модель с лучшим временем за выбранный период (замеры составных моделей и статистика Proxy).", "The model with the best time over the period goes first (composite measurements and Proxy statistics)."],
    cheapest: ["По цене за 1M токенов из каталога; бесплатные и подписки — первыми.", "By catalogue price per 1M tokens; free and subscription models first."],
    success: ["Чаще всего отвечавшая успешно за период.", "The one that answered successfully most often over the period."],
    round_robin: ["Каждый запрос начинает со следующей модели — нагрузка поровну.", "Each request starts with the next model — the load is shared evenly."],
    weighted: ["Первая модель выбирается случайно пропорционально весам.", "The first model is picked at random in proportion to the weights."],
    least_used: ["По прогнозу планировщика квот (запас до сброса), иначе — по расходу токенов/запросов.", "By the quota planner forecast (reserve before reset), otherwise by tokens/requests used."],
    sticky: ["Беседа продолжает идти в ту модель, которая ответила первой.", "A conversation keeps going to the model that answered first."],
  };
  const TRIG = { error: ["любая ошибка", "any error"], 429: ["лимит 429", "429 limit"], "5xx": ["ошибка сервиса 5xx", "service error 5xx"], timeout: ["таймаут", "timeout"],
    empty: ["пустой ответ", "empty answer"], content_filter: ["фильтр контента", "content filter"], context_length: ["не влезло в окно", "context too long"] };

  const FAIL = { ...TRIG, error: ["ошибка запроса", "request error"], cancelled: ["отменён", "cancelled"],
    unavailable: ["модель недоступна (ключ, доступ)", "model unavailable (key, access)"] };
  const FILT = { vision: ["Только со зрением", "Vision only"], tools: ["Только с нативными инструментами", "Native tools only"],
    free_only: ["Только бесплатные", "Free only"], json: ["Поддерживает JSON", "Supports JSON"] };
  const W = (pair) => (Array.isArray(pair) ? L(pair[0], pair[1]) : pair);
  const q = (text) => (LANG === "en" ? `“${text}”` : `«${text}»`);

  const ICO = {
    grip: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    up: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
    x: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    copy: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
    plus: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    eye: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    tool: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/></svg>',
    chev: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
    play: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>',
    layers: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h7v4H4zM13 13h7v4h-7zM13 4h7v4h-7z"/><path d="M11 9h2v8M11 9V6h2"/></svg>',
  };

  const avail = () => (S.data && S.data.available) || [];
  const findModel = (id) => avail().find((m) => m.id.toLowerCase() === String(id || "").toLowerCase());
  function modelName(id) {
    const m = findModel(id);
    if (m && m.composite) return m.name || id;
    if (m && m.name) {

      const twin = avail().some((o) => o !== m && !o.composite && o.name === m.name);
      return twin ? `${m.name} · ${m.account || m.id}` : m.name;
    }
    try { return window.MemuModelNames ? MemuModelNames.pretty(id) : id; } catch (e) { return id; }
  }
  function capsBadges(m) {
    if (!m) return `<span class="cmp-badge warn" title="${esc(L("Модели сейчас нет в Proxy", "The model is not in the Proxy now"))}">${esc(L("нет в Proxy", "not in Proxy"))}</span>`;
    if (m.composite) return `<span class="cmp-badge acc">${esc(L("составная", "composite"))}</span>`;
    const out = [];
    if (m.vision === "yes") out.push(`<span class="cmp-badge ok" title="${esc(L("Видит картинки", "Sees images"))}">${ICO.eye}</span>`);
    else if (m.vision === "unknown") out.push(`<span class="cmp-badge dim" title="${esc(L("Зрение не проверено", "Vision not checked"))}">${ICO.eye}?</span>`);
    if (m.tools) out.push(`<span class="cmp-badge" title="${esc(L("Нативные вызовы инструментов", "Native tool calls"))}">${ICO.tool}</span>`);
    if (m.context) out.push(`<span class="cmp-badge" title="${esc(L("Окно контекста", "Context window"))}">${short(m.context)}</span>`);
    if (m.free) out.push(`<span class="cmp-badge ok">free</span>`);
    else if (m.price) out.push(`<span class="cmp-badge dim" title="${esc(L("$ за 1M токенов: вход / выход", "$ per 1M tokens: in / out"))}">$${+m.price[0].toFixed(2)}/${+m.price[1].toFixed(2)}</span>`);
    return out.join("");
  }
  function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
  function setPath(obj, path, value) {
    const keys = path.split("."); let o = obj;
    for (let i = 0; i < keys.length - 1; i++) { if (o[keys[i]] == null) o[keys[i]] = /^\d+$/.test(keys[i + 1]) ? [] : {}; o = o[keys[i]]; }
    o[keys[keys.length - 1]] = value;
  }
  const ipath = (p) => String(p || "").replace(/\[(\d+)\]/g, ".$1");
  function blankModel() {
    const taken = new Set(((S.data && S.data.models) || []).map((m) => m.id));
    let n = 1; while (taken.has(n === 1 ? "memu/smart" : `memu/smart-${n}`)) n++;
    return { prefix: "memu", name: n === 1 ? "smart" : `smart-${n}`, title: "", description: "", enabled: true,
      default: { candidates: [], strategy: { type: "fixed" }, filters: {} }, rules: [],
      fallback: clone(S.data.schema.fallback_default), auto_filter: true };
  }
  function uniqueName(model) {
    const taken = new Set(((S.data && S.data.models) || []).map((m) => m.id));
    let name = model.name, n = 2;
    while (taken.has(`${model.prefix}/${name}`)) name = `${model.name}-${n++}`;
    model.name = name; model.id = `${model.prefix}/${name}`;
    return model;
  }
  function newRule(kind = "route") {
    const ids = new Set(S.cur.rules.map((r) => r.id)); let n = S.cur.rules.length + 1; while (ids.has(`r${n}`)) n++;
    const rule = { id: `r${n}`, kind, title: "", enabled: true, match: "all", conditions: [] };
    if (kind === "route") Object.assign(rule, { candidates: [], strategy: { type: "fixed" }, filters: {}, fallthrough: true });
    if (kind === "filter") rule.filters = { vision: true };
    if (kind === "prefer" || kind === "exclude") rule.models = [];
    if (kind === "settings") rule.fallback = { first_token_timeout: 30 };
    return rule;
  }
  function defaultCond(type = "has_image") {
    const spec = S.data.schema.conditions[type];
    const value = { bool: true, number: type === "tokens" ? 200000 : type === "temperature" ? 0.7 : type === "turns" ? 20 : 4096, text: "", set: [], time: "09:00-18:00", percent: 50 }[spec.kind];
    const op = spec.kind === "number" ? (type === "tokens" || type === "turns" ? "gt" : "gte") : spec.ops[0];
    return { type, op, value: type === "weekday" ? ["1", "2", "3", "4", "5"] : type === "client" ? ["api"] : value };
  }

  async function load(keepSelection) {
    const root = $("#composite-root");
    if (!root) return;
    S.loading = true;
    try { S.data = await api("/api/admin/composite"); } catch (e) {
      root.innerHTML = `<div class="card"><div class="warn-strip">${esc(errorOf(e))}</div></div>`; S.loading = false; return;
    }
    S.loading = false;
    if (!keepSelection || !S.cur) {
      const first = S.data.models[0];
      if (!S.cur && first && window.innerWidth > 900) select(first.id, true);
    } else if (S.orig && !S.dirty) {
      const fresh = S.data.models.find((m) => m.id === S.orig);
      if (fresh) S.cur = clean(fresh);
    }
    render();
    if (S.cur && !S.sim) scheduleSim(0);
  }
  function clean(model) { const m = clone(model); delete m.summary; delete m.issues; return m; }
  function select(id, quiet) {
    if (S.dirty && !quiet && !confirm(L("Есть несохранённые изменения. Отбросить их?", "There are unsaved changes. Discard them?"))) return false;
    const model = S.data.models.find((m) => m.id === id);
    if (!model) return false;
    S.cur = clean(model); S.orig = model.id; S.dirty = false; S.sim = null; S.tryRes = null; S.issues = model.issues || [];
    if (!quiet) { render(); scheduleSim(0); }
    return true;
  }
  function openNew(model) {
    if (S.dirty && !confirm(L("Есть несохранённые изменения. Отбросить их?", "There are unsaved changes. Discard them?"))) return;
    S.cur = uniqueName(clone(model || blankModel())); S.orig = ""; S.dirty = true; S.sim = null; S.tryRes = null; S.issues = [];
    render(); scheduleSim(0);
    setTimeout(() => $("#composite-root .cmp-editor")?.scrollIntoView({ behavior: "smooth", block: "start" }), 30);
  }
  async function save() {
    syncId();
    try {
      const res = await api("/api/admin/composite/save", { method: "POST", body: { model: S.cur, original_id: S.orig } });
      S.cur = res.model; S.orig = res.model.id; S.dirty = false; S.issues = res.issues || [];
      toast(L("Сохранено — модель появилась в /v1/models и в списках моделей", "Saved — the model is now in /v1/models and in the model lists"), "ok", 3200);
      await load(true);
      scheduleSim(0);
    } catch (e) {
      toast(errorOf(e), "err", 6000);
      scheduleSim(0);
    }
  }
  function syncId() { S.cur.prefix = String(S.cur.prefix || "").trim().toLowerCase(); S.cur.name = String(S.cur.name || "").trim().toLowerCase(); S.cur.id = `${S.cur.prefix}/${S.cur.name}`; }
  function changed(rerender) {
    S.dirty = true; syncId();
    if (rerender) render(); else updateHeadState();
    scheduleSim();
  }
  function updateHeadState() {
    const btn = $("#cmp-save"); if (btn) btn.disabled = !S.dirty;
    const mark = $("#cmp-dirty"); if (mark) mark.hidden = !S.dirty;
    const idEl = $("#cmp-idline"); if (idEl) idEl.textContent = S.cur.id;
  }

  function sampleDefaults() {
    return { text: L("Привет! Объясни, как работает фотосинтез.", "Hi! Explain how photosynthesis works."), system: "", has_image: false, has_audio: false,
      has_file: false, tools: false, json: false, stream: true, reasoning: false, tokens: "", client: "api", endpoint: "", time: "", weekday: "",
      max_tokens: "", temperature: "", turns: 1 };
  }
  function scheduleSim(delay = 350) {
    clearTimeout(S.simTimer);
    S.simTimer = setTimeout(runSim, delay);
  }
  async function runSim() {
    if (!S.cur) return;
    syncId();
    const seq = ++S.seq;
    const sample = { ...S.sample };
    ["tokens", "max_tokens", "temperature", "turns", "weekday"].forEach((k) => { if (sample[k] === "" || sample[k] == null) delete sample[k]; });
    if (!sample.time) delete sample.time;
    if (!sample.endpoint) delete sample.endpoint;
    try {
      const res = await api("/api/admin/composite/simulate", { method: "POST", body: { model: S.cur, sample, original_id: S.orig } });
      if (seq !== S.seq) return;
      S.sim = res; S.issues = res.issues || [];
    } catch (e) { if (seq === S.seq) S.sim = { error: errorOf(e) }; }
    renderSimResult(); renderIssues(); markIssues();
  }
  async function tryReal() {
    if (!S.orig || S.dirty) { toast(L("Сначала сохраните модель", "Save the model first"), "err"); return; }
    S.trying = true; S.tryRes = null; renderTry();
    try {
      S.tryRes = await api("/api/admin/composite/try", { method: "POST", body: { id: S.orig, text: S.sample.text, image: !!S.sample.has_image, stream: !!S.sample.stream } });
    } catch (e) { S.tryRes = { ok: false, error: { message: errorOf(e), message_en: errorOf(e) } }; }
    S.trying = false; renderTry();
    load(true).catch(() => {});
  }

  function render() {
    const root = $("#composite-root");
    if (!root || !S.data) return;
    if (!S.sample) S.sample = sampleDefaults();
    const narrow = window.innerWidth <= 900;
    root.innerHTML = `<div class="cmp-wrap ${S.cur ? "has-editor" : ""} ${narrow && S.cur ? "narrow-edit" : ""}">
      ${listHtml()}
      ${S.cur ? editorHtml() : introHtml()}
    </div>`;
    bind(root);
    markIssues();
    renderSimResult();
    renderTry();
  }
  function listHtml() {
    const models = S.data.models;
    const stats = S.data.stats || {};
    const items = models.map((m) => {
      const st = stats[m.id] || {};
      const fb = st.requests ? Math.round((st.fallbacks || 0) / st.requests * 100) : 0;
      const errs = (m.issues || []).filter((i) => i.level === "error").length;
      return `<div class="cmp-item ${S.orig === m.id ? "active" : ""} ${m.enabled ? "" : "off"}" data-act="select" data-id="${esc(m.id)}" tabindex="0" role="button">
        <div class="cmp-item-top"><span class="cmp-item-title">${esc(m.title || modelName(m.id))}</span>
          <label class="switch" title="${esc(L("Включена", "Enabled"))}" data-stop="1"><input type="checkbox" data-act="toggle" data-id="${esc(m.id)}" ${m.enabled ? "checked" : ""}><span class="slider"></span></label></div>
        <div class="cmp-item-id mono">${esc(m.id)}</div>
        <div class="cmp-item-meta">${errs ? `<span class="cmp-badge err">${esc(L("ошибки", "errors"))}: ${errs}</span>` : ""}
          <span>${esc(L("правил", "rules"))}: ${m.rules.length}</span>
          ${st.requests ? `<span>${esc(L("запросов", "requests"))}: ${num(st.requests)}</span><span>${esc(L("откатов", "fallbacks"))}: ${fb}%</span>` : `<span class="dim">${esc(L("ещё не вызывалась", "not called yet"))}</span>`}
        </div></div>`;
    }).join("");
    return `<aside class="cmp-list">
      <div class="cmp-list-head"><b>${esc(L("Ваши составные модели", "Your composite models"))}</b><span class="dim">${models.length}</span></div>
      <div class="cmp-list-actions">
        <button class="btn primary small" data-act="new">${ICO.plus}${esc(L("Новая", "New"))}</button>
        <button class="btn small" data-act="presets">${esc(L("Из шаблона", "From template"))}</button>
        <button class="btn small" data-act="import">${esc(L("Импорт", "Import"))}</button>
        <button class="btn small" data-act="export-all" ${models.length ? "" : "disabled"}>${esc(L("Экспорт", "Export"))}</button>
        <input type="file" id="cmp-file" accept=".json,application/json" hidden>
      </div>
      <div class="cmp-items">${items || `<div class="cmp-empty-list dim">${esc(L("Пока нет ни одной. Начните с шаблона или создайте свою.", "None yet. Start from a template or build your own."))}</div>`}</div>
    </aside>`;
  }
  function introHtml() {
    return `<main class="cmp-editor cmp-intro">
      <div class="card cmp-hero">
        <div class="cmp-hero-ico">${ICO.layers}</div>
        <h3>${esc(L("Одна модель — много моделей внутри", "One model — many models inside"))}</h3>
        <p class="sub">${esc(L("Составная модель выглядит для программ как обычная (например, memu/smart), а внутри сама выбирает настоящую модель по правилам: картинка → модель со зрением, длинный запрос → большое окно, ошибка или 429 → следующая модель, днём — дешёвая, ночью — лучшая.",
          "A composite model looks like a regular one to programs (e.g. memu/smart), but inside it picks a real model by rules: a picture → a vision model, a long request → a large window, an error or 429 → the next model, cheap by day, best at night."))}</p>
        <div class="cmp-presets">${presetCards()}</div>
      </div></main>`;
  }
  function presetCards() {
    return (S.data.presets || []).map((p) => {
      const ids = [...(p.model.default.candidates || []).map((c) => c.model), ...p.model.rules.flatMap((r) => (r.candidates || []).map((c) => c.model))];
      const uniq = [...new Set(ids)];
      return `<button class="cmp-preset" data-act="preset" data-key="${esc(p.key)}">
        <span class="cmp-preset-title">${esc(L(p.title, p.title_en))}</span>
        <span class="cmp-preset-desc">${esc(L(p.description, p.description_en))}</span>
        <span class="cmp-preset-models">${uniq.length ? uniq.slice(0, 4).map((id) => `<i>${esc(modelName(id))}</i>`).join("") : `<i class="warn">${esc(L("модели выберете сами", "you pick the models"))}</i>`}</span>
      </button>`;
    }).join("");
  }

  function editorHtml() {
    const m = S.cur;
    return `<main class="cmp-editor">
      ${window.innerWidth <= 900 ? `<button class="btn small cmp-back" data-act="back">← ${esc(L("К списку", "Back to the list"))}</button>` : ""}
      <div class="card cmp-head">
        <div class="cmp-head-row">
          <div class="cmp-idfields" data-ipath="name">
            <label class="cmp-field"><span>${esc(L("Префикс", "Prefix"))}</span><input type="text" class="mono" data-bind="prefix" data-ipath="prefix" value="${esc(m.prefix)}" spellcheck="false" maxlength="32"></label>
            <span class="cmp-slash">/</span>
            <label class="cmp-field grow"><span>${esc(L("Название", "Name"))}</span><input type="text" class="mono" data-bind="name" value="${esc(m.name)}" spellcheck="false" maxlength="64"></label>
          </div>
          <label class="cmp-field grow"><span>${esc(L("Имя в списках", "Display name"))}</span><input type="text" data-bind="title" value="${esc(m.title)}" maxlength="80" placeholder="${esc(L("Например: Умная", "For example: Smart"))}"></label>
          <label class="cmp-enabled"><span class="switch"><input type="checkbox" data-bind="enabled" data-type="bool" ${m.enabled ? "checked" : ""}><span class="slider"></span></span>${esc(L("Включена", "Enabled"))}</label>
        </div>
        <label class="cmp-field"><span>${esc(L("Описание", "Description"))}</span><input type="text" data-bind="description" value="${esc(m.description)}" maxlength="600" placeholder="${esc(L("Для чего эта модель — видно в подсказке списка моделей", "What this model is for — shown in model list tooltips"))}"></label>
        <div class="cmp-head-foot">
          <span class="cmp-idline-wrap">${esc(L("ID для программ:", "ID for programs:"))} <code id="cmp-idline">${esc(m.id)}</code></span>
          <span class="cmp-dirty" id="cmp-dirty" ${S.dirty ? "" : "hidden"}>${esc(L("не сохранено", "unsaved"))}</span>
          <span class="fill"></span>
          ${S.orig ? `<button class="btn small" data-act="export-one">${esc(L("Экспорт JSON", "Export JSON"))}</button>
          <button class="btn small" data-act="duplicate">${esc(L("Копия", "Duplicate"))}</button>
          <button class="btn small danger" data-act="delete">${esc(L("Удалить", "Delete"))}</button>` : `<button class="btn small" data-act="discard">${esc(L("Отменить", "Cancel"))}</button>`}
          <button class="btn primary" id="cmp-save" data-act="save" ${S.dirty ? "" : "disabled"}>${esc(L("Сохранить", "Save"))}</button>
        </div>
        <div class="cmp-issues" id="cmp-issues"></div>
      </div>

      <div class="cmp-flow">
        <div class="cmp-node cmp-node-start"><span class="cmp-dot"></span>${esc(L("Запрос к", "Request to"))} <code>${esc(m.id)}</code>
          <span class="dim">— ${esc(L("правила проверяются сверху вниз", "rules are checked top to bottom"))}</span></div>
        <div class="cmp-rules" id="cmp-rules">${m.rules.map((r, i) => ruleHtml(r, i)).join("")}</div>
        <div class="cmp-add-rule">
          <span class="dim">${esc(L("Добавить правило:", "Add a rule:"))}</span>
          ${Object.keys(KINDS).map((k) => `<button class="btn small" data-act="add-rule" data-kind="${k}" title="${esc(W(KINDS[k].hint))}">${ICO.plus}${esc(L(KINDS[k].ru, KINDS[k].en))}</button>`).join("")}
        </div>
        ${defaultHtml()}
        ${fallbackHtml()}
      </div>

      ${simHtml()}
      ${statsHtml()}
    </main>`;
  }

  function condRow(cond, rpath, ci) {
    const spec = S.data.schema.conditions[cond.type] || { kind: "bool", ops: ["is"], options: [] };
    const p = `${rpath}.conditions.${ci}`;
    const typeSel = `<select data-bind="${p}.type" data-rerender="cond" aria-label="${esc(L("Что проверять", "What to check"))}">${COND_GROUPS.map(([g, keys]) =>
      `<optgroup label="${esc(W(g))}">${keys.map((k) => `<option value="${k}" ${k === cond.type ? "selected" : ""}>${esc(W(COND[k]))}</option>`).join("")}</optgroup>`).join("")}</select>`;
    let opSel = "";
    if (spec.kind !== "bool" && spec.ops.length > 1) {
      opSel = `<select data-bind="${p}.op" class="cmp-op">${spec.ops.map((o) => `<option value="${o}" ${o === cond.op ? "selected" : ""}>${esc(W(OPS[o]))}</option>`).join("")}</select>`;
    }
    let val = "";
    if (spec.kind === "bool") {
      val = `<select data-bind="${p}.value" data-type="bool" class="cmp-bool"><option value="true" ${cond.value ? "selected" : ""}>${esc(L("да", "yes"))}</option><option value="false" ${cond.value ? "" : "selected"}>${esc(L("нет", "no"))}</option></select>`;
    } else if (spec.kind === "number") {
      const list = cond.type === "tokens" ? `<span class="cmp-quick">${[32000, 128000, 200000, 1000000].map((n) => `<button type="button" class="chip-btn" data-act="quick" data-path="${p}.value" data-v="${n}">${short(n)}</button>`).join("")}</span>` : "";
      val = `<input type="number" data-bind="${p}.value" data-type="num" value="${esc(cond.value ?? "")}" step="${cond.type === "temperature" ? "0.1" : "1"}" class="cmp-num">${list}`;
    } else if (spec.kind === "percent") {
      val = `<input type="number" data-bind="${p}.value" data-type="num" value="${esc(cond.value ?? "")}" min="0" max="100" class="cmp-num"><span class="dim">%</span>`;
    } else if (spec.kind === "time") {
      const [a, b] = String(cond.value || "09:00-18:00").split("-");
      val = `<span class="cmp-timepair"><input type="time" data-time="${p}.value" data-part="0" value="${esc(a || "")}" aria-label="${esc(L("С", "From"))}"><span class="dim">—</span><input type="time" data-time="${p}.value" data-part="1" value="${esc(b || "")}" aria-label="${esc(L("До", "To"))}"></span>`;
    } else if (spec.kind === "set") {
      const opts = OPTS[cond.type] || {};
      const keys = spec.options.length ? spec.options : Object.keys(opts);
      val = `<span class="cmp-set">${keys.map((k) => `<button type="button" class="chip-btn ${(cond.value || []).includes(String(k)) ? "active" : ""}" data-act="set-toggle" data-path="${p}.value" data-v="${esc(k)}">${esc(opts[k] ? W(opts[k]) : k)}</button>`).join("")}</span>`;
    } else {
      const ph = cond.type === "api_key" ? "public:<id> / key:1a2b3c4d" : cond.op.includes("regex") ? "(?i)code|python" : L("текст", "text");
      val = `<input type="text" data-bind="${p}.value" value="${esc(cond.value || "")}" placeholder="${esc(ph)}" class="cmp-text" spellcheck="false">`;
    }
    return `<div class="cmp-cond" data-ipath="${p}">${typeSel}${opSel}${val}<button class="btn small is-icon cmp-x" data-act="del-cond" data-path="${rpath}" data-i="${ci}" title="${esc(L("Убрать условие", "Remove condition"))}">${ICO.x}</button></div>`;
  }

  function candList(rows, path, strategy) {
    const weighted = strategy && strategy.type === "weighted";
    const items = rows.map((c, i) => {
      const m = findModel(c.model);
      return `<li class="cmp-cand" data-i="${i}" data-ipath="${path}.${i}">
        <button class="cmp-grip" data-grip="cand" title="${esc(L("Перетащите, чтобы изменить порядок (или Alt+↑/↓)", "Drag to reorder (or Alt+↑/↓)"))}" aria-label="${esc(L("Переместить", "Move"))}">${ICO.grip}</button>
        <span class="cmp-cand-no">${i + 1}</span>
        <span class="cmp-cand-name"><b>${esc(modelName(c.model))}</b><span class="mono dim">${esc(c.model)}</span></span>
        <span class="cmp-cand-caps">${capsBadges(m)}</span>
        ${weighted ? `<label class="cmp-weight" title="${esc(L("Вес", "Weight"))}">×<input type="number" min="0" step="1" data-bind="${path}.${i}.weight" data-type="num" value="${esc(c.weight ?? 1)}"></label>` : ""}
        <button class="btn small is-icon" data-act="cand-up" data-path="${path}" data-i="${i}" ${i ? "" : "disabled"} title="${esc(L("Выше", "Up"))}">${ICO.up}</button>
        <button class="btn small is-icon" data-act="cand-down" data-path="${path}" data-i="${i}" ${i < rows.length - 1 ? "" : "disabled"} title="${esc(L("Ниже", "Down"))}">${ICO.down}</button>
        <button class="btn small is-icon cmp-x" data-act="cand-del" data-path="${path}" data-i="${i}" title="${esc(L("Убрать", "Remove"))}">${ICO.x}</button>
      </li>`;
    }).join("");
    return `<ol class="cmp-cands" data-list="${path}" data-ipath="${path}">${items || `<li class="cmp-cands-empty dim">${esc(L("Нет моделей — добавьте хотя бы одну", "No models — add at least one"))}</li>`}</ol>
      <button class="btn small cmp-addmodel" data-act="pick" data-path="${path}">${ICO.plus}${esc(L("Добавить модель", "Add a model"))}</button>`;
  }

  function strategyHtml(st, path) {
    const extra = [];
    const win = (key, opts) => `<label class="cmp-mini">${esc(L("за", "over"))} <select data-bind="${path}.${key}" data-type="num">${opts.map(([v, ru, en]) => `<option value="${v}" ${Number(st[key]) === v ? "selected" : ""}>${esc(L(ru, en))}</option>`).join("")}</select></label>`;
    if (st.type === "fastest") {
      extra.push(`<label class="cmp-mini"><select data-bind="${path}.metric"><option value="ttft" ${st.metric !== "latency" ? "selected" : ""}>${esc(L("до первого токена", "time to first token"))}</option><option value="latency" ${st.metric === "latency" ? "selected" : ""}>${esc(L("время всего ответа", "whole answer time"))}</option></select></label>`);
      extra.push(win("window_min", [[15, "15 минут", "15 minutes"], [60, "час", "an hour"], [180, "3 часа", "3 hours"], [1440, "сутки", "a day"]]));
    }
    if (st.type === "success") extra.push(win("window_min", [[60, "час", "an hour"], [360, "6 часов", "6 hours"], [1440, "сутки", "a day"], [10080, "неделю", "a week"]]));
    if (st.type === "fastest" || st.type === "success") {
      const ex = st.explore ?? 10;
      extra.push(`<label class="cmp-mini" title="${esc(L("Как часто первой пробовать модель без замеров, чтобы узнать её скорость", "How often to try a model without measurements first, to learn its speed"))}">${esc(L("разведка", "exploring"))} <select data-bind="${path}.explore" data-type="num">${[[0, "выкл.", "off"], [5, "5 %", "5 %"], [10, "10 %", "10 %"], [20, "20 %", "20 %"]].map(([v, ru, en]) => `<option value="${v}" ${Number(ex) === v ? "selected" : ""}>${esc(L(ru, en))}</option>`).join("")}</select></label>`);
    }
    if (st.type === "least_used") {
      extra.push(`<label class="cmp-mini"><select data-bind="${path}.by"><option value="tokens" ${st.by !== "requests" ? "selected" : ""}>${esc(L("по токенам", "by tokens"))}</option><option value="requests" ${st.by === "requests" ? "selected" : ""}>${esc(L("по запросам", "by requests"))}</option></select></label>`);
      extra.push(win("window_min", [[60, "час", "an hour"], [1440, "сутки", "a day"], [10080, "неделю", "a week"]]));
    }
    if (st.type === "cheapest") extra.push(`<label class="cmp-mini"><select data-bind="${path}.unknown"><option value="last" ${st.unknown !== "skip" ? "selected" : ""}>${esc(L("цена неизвестна — в конец", "unknown price — last"))}</option><option value="skip" ${st.unknown === "skip" ? "selected" : ""}>${esc(L("цена неизвестна — пропустить", "unknown price — skip"))}</option></select></label>`);
    if (st.type === "sticky") {
      extra.push(`<label class="cmp-mini">${esc(L("сначала", "first"))} <select data-bind="${path}.base">${Object.keys(STRAT).filter((k) => k !== "sticky").map((k) => `<option value="${k}" ${st.base === k ? "selected" : ""}>${esc(W(STRAT[k]))}</option>`).join("")}</select></label>`);
      extra.push(win("ttl_min", [[60, "1 час", "1 hour"], [360, "6 часов", "6 hours"], [1440, "сутки", "a day"], [10080, "неделю", "a week"]]).replace(esc(L("за", "over")), esc(L("помнить", "remember"))));
    }
    return `<div class="cmp-strategy"><span class="cmp-lbl">${esc(L("Порядок", "Order"))}</span>
      <select data-bind="${path}.type" data-rerender="1">${Object.keys(STRAT).map((k) => `<option value="${k}" ${st.type === k ? "selected" : ""}>${esc(W(STRAT[k]))}</option>`).join("")}</select>
      ${extra.join("")}<span class="cmp-hint">${esc(W(STRAT_HINT[st.type] || ["", ""]))}</span></div>`;
  }

  function filtersHtml(f, path) {
    f = f || {};
    return `<div class="cmp-filters" data-ipath="${path}"><span class="cmp-lbl">${esc(L("Только модели", "Only models"))}</span>
      ${Object.keys(FILT).map((k) => `<button type="button" class="chip-btn ${f[k] ? "active" : ""}" data-act="flag" data-path="${path}.${k}">${esc(W(FILT[k]))}</button>`).join("")}
      <label class="cmp-mini">${esc(L("окно от", "window from"))} <input type="number" min="0" step="1000" class="cmp-num" data-bind="${path}.min_context" data-type="num" value="${esc(f.min_context || "")}" placeholder="0"> ${esc(L("токенов", "tokens"))}</label>
    </div>`;
  }

  function modelsChips(list, path) {
    return `<div class="cmp-chips" data-ipath="${path}">${(list || []).map((id, i) => `<span class="cmp-chip">${esc(modelName(id))}<button data-act="chip-del" data-path="${path}" data-i="${i}" aria-label="${esc(L("Убрать", "Remove"))}">${ICO.x}</button></span>`).join("")}
      <button class="btn small" data-act="pick" data-path="${path}" data-mode="ids">${ICO.plus}${esc(L("Модели", "Models"))}</button></div>`;
  }

  function fallbackFields(fb, path, partial) {
    const has = (k) => !partial || Object.prototype.hasOwnProperty.call(fb, k);
    const field = (k, ru, en, attrs, unit) => `<label class="cmp-fb-field ${has(k) ? "" : "unset"}">${partial ? `<input type="checkbox" data-act="fb-own" data-path="${path}" data-k="${k}" ${has(k) ? "checked" : ""} title="${esc(L("Менять в этом правиле", "Change in this rule"))}">` : ""}
      <span>${esc(L(ru, en))}</span><input type="number" ${attrs} data-bind="${path}.${k}" data-type="num" value="${esc(fb[k] ?? "")}" ${has(k) ? "" : "disabled"}>${unit ? `<i>${esc(unit)}</i>` : ""}</label>`;
    const on = fb.on || (partial ? null : []);
    return `<div class="cmp-fb">
      <div class="cmp-fb-row"><span class="cmp-lbl">${esc(L("Перейти к следующей модели при", "Move to the next model on"))}</span>
        ${partial ? `<label class="cmp-own"><input type="checkbox" data-act="fb-own" data-path="${path}" data-k="on" ${on ? "checked" : ""}>${esc(L("менять", "change"))}</label>` : ""}
        ${Object.keys(TRIG).map((k) => `<button type="button" class="chip-btn ${on && on.includes(k) ? "active" : ""}" data-act="trig" data-path="${path}" data-v="${k}" ${on ? "" : "disabled"}>${esc(W(TRIG[k]))}</button>`).join("")}</div>
      <div class="cmp-fb-grid">
        ${field("connect_timeout", "Ждать начала ответа", "Wait for the answer to start", 'min="0" max="600"', L("с", "s"))}
        ${field("first_token_timeout", "Ждать первого слова", "Wait for the first word", 'min="0" max="900"', L("с", "s"))}
        ${field("total_timeout", "Весь ответ не дольше", "Whole answer at most", 'min="0" max="3600"', L("с", "s"))}
        ${field("retries", "Повторов той же модели", "Retries of the same model", 'min="0" max="5"', "")}
        ${field("backoff_ms", "Пауза перед повтором", "Pause before a retry", 'min="0" max="60000" step="100"', L("мс", "ms"))}
        ${field("max_attempts", "Всего попыток", "Attempts in total", 'min="1" max="30"', "")}
        ${field("recovery_attempts", "Продолжений после обрыва", "Continuations after a break", 'min="0" max="5"', "")}
      </div>
      <div class="cmp-fb-row">
        ${has("backoff") ? `<label class="cmp-mini">${esc(L("Пауза", "Pause"))} <select data-bind="${path}.backoff"><option value="exponential" ${fb.backoff !== "fixed" ? "selected" : ""}>${esc(L("растёт ×2", "doubles"))}</option><option value="fixed" ${fb.backoff === "fixed" ? "selected" : ""}>${esc(L("одинаковая", "fixed"))}</option></select></label>` : ""}
        ${has("stream_recovery") ? `<label class="cmp-check" title="${esc(L("Если поток оборвался посреди ответа (сеть, таймаут, 5xx), следующая модель продолжит его с того же места: уже отправленный текст не теряется, повтор на стыке срезается. Незаконченный вызов инструмента не отправляется.", "If a stream breaks in the middle of an answer (network, timeout, 5xx), the next model continues it from the same place: the text already sent is kept, a repeat at the joint is cut. An unfinished tool call is never sent."))}"><input type="checkbox" data-bind="${path}.stream_recovery" data-type="bool" ${fb.stream_recovery !== false ? "checked" : ""}>${esc(L("Обрыв посреди ответа — продолжить следующей моделью", "A break mid-answer — continue with the next model"))}</label>` : ""}
        ${has("context_upgrade") ? `<label class="cmp-check"><input type="checkbox" data-bind="${path}.context_upgrade" data-type="bool" ${fb.context_upgrade !== false ? "checked" : ""}>${esc(L("«Не влезло в окно» — сразу к модели с бо́льшим окном", "“Context too long” — straight to a model with a larger window"))}</label>` : ""}
      </div>
      ${partial ? "" : `<p class="cmp-hint">${esc(L("0 — без ограничения. Таймауты «начала» и «первого слова» считаются до начала ответа. Если поток оборвался уже после первого слова, ответ продолжит следующая модель (когда включено «продолжить»). Обычный (не потоковый) запрос может переключиться в любой момент.",
        "0 — no limit. The “start” and “first word” timeouts count until the answer starts. If a stream breaks after the first word, the next model continues the answer (when “continue” is on). A regular (non-streaming) request can switch at any moment."))}</p>`}
    </div>`;
  }

  function ruleHtml(rule, i) {
    const p = `rules.${i}`;
    const k = KINDS[rule.kind] || KINDS.route;
    const collapsed = S.collapsed.has(rule.id);
    let then = "";
    if (rule.kind === "route") {
      then = `${candList(rule.candidates || [], `${p}.candidates`, rule.strategy)}
        ${strategyHtml(rule.strategy || { type: "fixed" }, `${p}.strategy`)}
        ${filtersHtml(rule.filters, `${p}.filters`)}
        <label class="cmp-check"><input type="checkbox" data-bind="${p}.fallthrough" data-type="bool" ${rule.fallthrough !== false ? "checked" : ""}>${esc(L("Если все модели правила не ответили — продолжить по основному списку", "If every model of the rule failed — continue with the main list"))}</label>`;
    } else if (rule.kind === "filter") {
      then = filtersHtml(rule.filters, `${p}.filters`);
    } else if (rule.kind === "prefer" || rule.kind === "exclude") {
      then = modelsChips(rule.models, `${p}.models`);
    } else {
      then = fallbackFields(rule.fallback || {}, `${p}.fallback`, true);
    }
    const condCount = (rule.conditions || []).length;
    const summary = condCount ? (rule.conditions || []).slice(0, 2).map((c) => W(COND[c.type] || [c.type, c.type])).join(rule.match === "any" ? L(" или ", " or ") : L(" и ", " and ")) + (condCount > 2 ? " …" : "") : L("всегда", "always");
    return `<div class="cmp-rule kind-${rule.kind} ${rule.enabled ? "" : "off"} ${collapsed ? "collapsed" : ""}" data-i="${i}" data-ipath="${p}">
      <div class="cmp-rule-head">
        <button class="cmp-grip" data-grip="rule" title="${esc(L("Перетащите карточку (или Alt+↑/↓)", "Drag the card (or Alt+↑/↓)"))}" aria-label="${esc(L("Переместить правило", "Move the rule"))}">${ICO.grip}</button>
        <span class="cmp-rule-no">${i + 1}</span>
        <select class="cmp-kind" data-act="kind" data-i="${i}" title="${esc(W(k.hint))}">${Object.keys(KINDS).map((key) => `<option value="${key}" ${key === rule.kind ? "selected" : ""}>${esc(L(KINDS[key].ru, KINDS[key].en))}</option>`).join("")}</select>
        <input type="text" class="cmp-rule-title" data-bind="${p}.title" value="${esc(rule.title || "")}" placeholder="${esc(L("Название правила", "Rule name"))}" maxlength="120">
        <span class="cmp-rule-sum dim">${esc(L("если", "if"))}: ${esc(summary)}</span>
        <span class="cmp-rule-acts">
        <label class="switch" title="${esc(L("Правило включено", "Rule enabled"))}"><input type="checkbox" data-bind="${p}.enabled" data-type="bool" data-rerender="1" ${rule.enabled ? "checked" : ""}><span class="slider"></span></label>
        <button class="btn small is-icon" data-act="rule-up" data-i="${i}" ${i ? "" : "disabled"} title="${esc(L("Выше", "Up"))}">${ICO.up}</button>
        <button class="btn small is-icon" data-act="rule-down" data-i="${i}" ${i < S.cur.rules.length - 1 ? "" : "disabled"} title="${esc(L("Ниже", "Down"))}">${ICO.down}</button>
        <button class="btn small is-icon" data-act="rule-dup" data-i="${i}" title="${esc(L("Копия правила", "Duplicate the rule"))}">${ICO.copy}</button>
        <button class="btn small is-icon cmp-x" data-act="rule-del" data-i="${i}" title="${esc(L("Удалить правило", "Delete the rule"))}">${ICO.x}</button>
        <button class="btn small is-icon cmp-collapse" data-act="collapse" data-id="${esc(rule.id)}" title="${esc(collapsed ? L("Развернуть", "Expand") : L("Свернуть", "Collapse"))}">${ICO.chev}</button>
        </span>
      </div>
      <div class="cmp-rule-body">
        <div class="cmp-if"><span class="cmp-kw">${esc(L("ЕСЛИ", "IF"))}</span>
          ${condCount > 1 ? `<select data-bind="${p}.match" class="cmp-match"><option value="all" ${rule.match !== "any" ? "selected" : ""}>${esc(L("выполнены все условия", "all conditions hold"))}</option><option value="any" ${rule.match === "any" ? "selected" : ""}>${esc(L("выполнено любое условие", "any condition holds"))}</option></select>` : ""}
          <div class="cmp-conds">${(rule.conditions || []).map((c, ci) => condRow(c, p, ci)).join("") || `<div class="dim cmp-always">${esc(L("Без условий — правило срабатывает всегда", "No conditions — the rule always applies"))}</div>`}</div>
          <button class="btn small" data-act="add-cond" data-path="${p}">${ICO.plus}${esc(L("Условие", "Condition"))}</button>
        </div>
        <div class="cmp-then"><span class="cmp-kw">${esc(L("ТО", "THEN"))}</span><span class="cmp-then-hint dim">${esc(W(k.hint))}</span>${then}</div>
      </div>
    </div>`;
  }

  function defaultHtml() {
    const d = S.cur.default;
    return `<div class="cmp-rule cmp-default" data-ipath="default">
      <div class="cmp-rule-head"><span class="cmp-rule-no">★</span><b>${esc(L("Основной список", "Main list"))}</b>
        <span class="dim">${esc(L("— если ни одно правило «Направить» не подошло", "— when no “Route” rule matched"))}</span></div>
      <div class="cmp-rule-body"><div class="cmp-then">
        ${candList(d.candidates || [], "default.candidates", d.strategy)}
        ${strategyHtml(d.strategy || { type: "fixed" }, "default.strategy")}
        ${filtersHtml(d.filters, "default.filters")}
      </div></div></div>`;
  }

  function fallbackHtml() {
    return `<div class="cmp-rule cmp-fallback" data-ipath="fallback">
      <div class="cmp-rule-head"><span class="cmp-rule-no">↻</span><b>${esc(L("Запасные пути", "Fallbacks"))}</b>
        <span class="dim">${esc(L("— когда модель не ответила", "— when a model did not answer"))}</span></div>
      <div class="cmp-rule-body"><div class="cmp-then">
        ${fallbackFields(S.cur.fallback || {}, "fallback", false)}
        <label class="cmp-check"><input type="checkbox" data-bind="auto_filter" data-type="bool" ${S.cur.auto_filter !== false ? "checked" : ""}>${esc(L("Сам пропускать модели, которые точно не справятся: картинка — без зрения, инструменты — без нативных вызовов, запрос больше окна", "Skip models that surely cannot cope: a picture — no vision, tools — no native calls, a request larger than the window"))}</label>
        <label class="cmp-mini cmp-diffmodel" title="${esc(L("Условие «Сложность задачи» определяется по тексту мгновенно и бесплатно. Если указать быструю модель, она уточнит только неясные случаи; ответ запоминается для одинаковых сообщений.", "The “Task difficulty” condition is judged from the text instantly and for free. A fast model, if set, settles only the unclear cases; its answer is remembered for identical messages."))}">${esc(L("Уточнять сложность моделью", "Refine difficulty with a model"))}
          <input type="text" list="cmp-models-dl" data-bind="difficulty_model" value="${esc(S.cur.difficulty_model || "")}" placeholder="${esc(L("только эвристики", "heuristics only"))}"></label>
        <datalist id="cmp-models-dl">${avail().map((m) => `<option value="${esc(m.id)}">`).join("")}</datalist>
      </div></div></div>`;
  }

  function simHtml() {
    const s = S.sample;
    const tog = (k, ru, en) => `<button type="button" class="chip-btn ${s[k] ? "active" : ""}" data-act="sim-flag" data-k="${k}">${esc(L(ru, en))}</button>`;
    return `<div class="card cmp-sim" id="cmp-sim">
      <div class="cmp-sec-head"><h3>${esc(L("Проверить", "Test"))}</h3><span class="sub">${esc(L("Опишите пример запроса — сразу видно, какая модель ответит и почему. Ничего не отправляется.", "Describe a sample request — you see at once which model would answer and why. Nothing is sent."))}</span></div>
      <div class="cmp-sim-grid">
        <div class="cmp-sim-in">
          <label class="cmp-field"><span>${esc(L("Сообщение пользователя", "User message"))}</span><textarea rows="3" data-sim="text">${esc(s.text)}</textarea></label>
          <label class="cmp-field"><span>${esc(L("Системный промпт", "System prompt"))}</span><input type="text" data-sim="system" value="${esc(s.system)}" placeholder="${esc(L("необязательно", "optional"))}"></label>
          <div class="cmp-sim-flags">${tog("has_image", "Картинка", "Image")}${tog("has_audio", "Аудио", "Audio")}${tog("has_file", "Файл", "File")}${tog("tools", "Инструменты", "Tools")}${tog("json", "JSON", "JSON")}${tog("stream", "Поток", "Stream")}${tog("reasoning", "Размышления", "Reasoning")}</div>
          <div class="cmp-sim-row">
            <label class="cmp-mini">${esc(L("Токенов", "Tokens"))} <input type="number" min="0" step="1000" class="cmp-num" data-sim="tokens" value="${esc(s.tokens)}" placeholder="${esc(L("авто", "auto"))}"></label>
            <label class="cmp-mini">${esc(L("Кто", "Who"))} <select data-sim="client">${Object.keys(OPTS.client).map((k) => `<option value="${k}" ${s.client === k ? "selected" : ""}>${esc(W(OPTS.client[k]))}</option>`).join("")}</select></label>
            <label class="cmp-mini">API <select data-sim="endpoint"><option value="">${esc(L("по «Кто»", "by “Who”"))}</option>${Object.keys(OPTS.endpoint).map((k) => `<option value="${k}" ${s.endpoint === k ? "selected" : ""}>${esc(W(OPTS.endpoint[k]))}</option>`).join("")}</select></label>
            <label class="cmp-mini">${esc(L("Время", "Time"))} <input type="time" data-sim="time" value="${esc(s.time)}"></label>
            <label class="cmp-mini">${esc(L("День", "Day"))} <select data-sim="weekday"><option value="">${esc(L("сегодня", "today"))}</option>${Object.keys(OPTS.weekday).map((k) => `<option value="${k}" ${String(s.weekday) === k ? "selected" : ""}>${esc(W(OPTS.weekday[k]))}</option>`).join("")}</select></label>
            <label class="cmp-mini">max_tokens <input type="number" min="0" class="cmp-num" data-sim="max_tokens" value="${esc(s.max_tokens)}"></label>
            <label class="cmp-mini">${esc(L("Сообщений", "Messages"))} <input type="number" min="1" class="cmp-num" data-sim="turns" value="${esc(s.turns)}"></label>
          </div>
          <div class="cmp-sim-actions">
            <button class="btn small" data-act="sim-run">${esc(L("Проверить", "Test"))}</button>
            <button class="btn small" data-act="try" ${S.orig && !S.dirty ? "" : "disabled"} title="${esc(L("Один настоящий короткий запрос через сохранённую модель", "One real short request through the saved model"))}">${ICO.play}${esc(L("Отправить настоящий запрос", "Send a real request"))}</button>
          </div>
          <div id="cmp-try"></div>
        </div>
        <div class="cmp-sim-out" id="cmp-sim-out"></div>
      </div></div>`;
  }

  function renderSimResult() {
    const box = $("#cmp-sim-out");
    if (!box) return;
    const r = S.sim;
    if (!r) { box.innerHTML = `<div class="dim">${esc(L("Проверяю…", "Checking…"))}</div>`; return; }
    if (r.error) { box.innerHTML = `<div class="warn-strip">${esc(r.error)}</div>`; return; }
    const first = r.order[0];
    const route = r.route.id === "default" ? L("Основной список", "Main list") : `${L("Правило", "Rule")} ${(S.cur.rules.findIndex((x) => x.id === r.route.id) + 1) || ""} ${q(r.route.title || r.route.id)}`;
    const head = first ? `<div class="cmp-verdict ok"><span class="cmp-verdict-lbl">${esc(L("Ответит", "Answers"))}</span><b>${esc(modelName(first))}</b><span class="mono dim">${esc(first)}</span>
        <span class="cmp-verdict-via">${esc(L("через", "via"))} ${esc(route)}${r.modifiers.length ? ` · ${esc(L("ещё правила", "plus rules"))}: ${r.modifiers.map((m) => esc(m.title || m.id)).join(", ")}` : ""}</span></div>`
      : `<div class="cmp-verdict err"><b>${esc(L("Некуда отправить запрос", "Nowhere to send the request"))}</b><span class="cmp-verdict-via">${esc(route)}</span></div>`;
    const cands = r.candidates.map((c) => {
      const pos = r.order.indexOf(c.model);
      const state = c.excluded ? `<span class="cmp-badge err">${esc(L(c.excluded.ru, c.excluded.en))}</span>` : pos === 0 ? `<span class="cmp-badge ok">${esc(L("первая", "first"))}</span>` : `<span class="cmp-badge">${esc(L("запасная", "fallback"))} ${pos + 1}</span>`;
      return `<li class="${c.excluded ? "ex" : ""}"><span class="cmp-cand-name"><b>${esc(modelName(c.model))}</b><span class="mono dim">${esc(c.model)}</span></span><span class="dim cmp-score">${esc(L(c.score_ru || "", c.score_en || ""))}</span>${state}</li>`;
    }).join("");
    const rules = r.rules.map((x, i) => {
      const mark = x.skipped === "disabled" ? `<span class="cmp-badge dim">${esc(L("выключено", "off"))}</span>` : x.skipped === "after_route" ? `<span class="cmp-badge dim">${esc(L("не проверялось", "not checked"))}</span>`
        : x.matched ? `<span class="cmp-badge ok">✓ ${esc(L("сработало", "matched"))}</span>` : `<span class="cmp-badge">✗ ${esc(L("нет", "no"))}</span>`;
      const conds = (x.conditions || []).map((c) => `<li class="${c.ok ? "ok" : "no"}">${c.ok ? "✓" : "✗"} ${esc(W(COND[c.type] || [c.type, c.type]))}${c.op !== "is" ? ` ${esc(W(OPS[c.op] || [c.op, c.op]))}` : ""} <b>${esc(fmtVal(c))}</b> <span class="dim">(${esc(L("в запросе", "request"))}: ${esc(fmtActual(c))})</span></li>`).join("");
      return `<div class="cmp-trace-rule"><div><span class="cmp-rule-no sm">${i + 1}</span> ${esc(L(KINDS[x.kind].ru, KINDS[x.kind].en))}${x.title ? ` ${esc(q(x.title))}` : ""} ${mark}</div>${conds ? `<ul>${conds}</ul>` : ""}</div>`;
    }).join("");
    const f = r.facts || {};
    const facts = [f.has_image && L("картинка", "image"), f.has_audio && L("аудио", "audio"), f.has_file && L("файл", "file"), f.needs_tools && L("инструменты", "tools"),
      f.json_mode && "JSON", f.stream ? L("поток", "stream") : L("без потока", "no stream"), f.reasoning && L("размышления", "reasoning"),
      `≈${num(f.tokens)} ${L("токенов", "tokens")}`, f.difficulty && `${L("сложность", "difficulty")}: ${W(OPTS.difficulty[f.difficulty] || [f.difficulty, f.difficulty])}`, `${L("язык", "language")}: ${f.language}`, `${f.time}`, W(OPTS.weekday[f.weekday] || ["", ""]), W(OPTS.client[f.client] || [f.client, f.client])].filter(Boolean);
    box.innerHTML = `${head}
      <div class="cmp-facts">${facts.map((x) => `<span>${esc(x)}</span>`).join("")}</div>
      <div class="cmp-out-sec"><b>${esc(L("Очередь моделей", "Model queue"))}</b><ol class="cmp-queue">${cands || `<li class="dim">${esc(L("пусто", "empty"))}</li>`}</ol></div>
      ${rules ? `<div class="cmp-out-sec"><b>${esc(L("Как проверялись правила", "How the rules were checked"))}</b>${rules}</div>` : ""}
      <div class="cmp-out-sec dim cmp-fbline">${esc(L("При сбое", "On failure"))}: ${(r.fallback.on || []).map((x) => esc(W(TRIG[x]))).join(", ") || "—"} · ${esc(L("попыток до", "attempts up to"))} ${r.fallback.max_attempts}${r.fallback.first_token_timeout ? ` · ${esc(L("первое слово за", "first word within"))} ${r.fallback.first_token_timeout} ${esc(L("с", "s"))}` : ""}</div>`;
  }
  function fmtVal(c) {
    if (Array.isArray(c.value)) { const o = OPTS[c.type] || {}; return c.value.map((v) => (o[v] ? W(o[v]) : v)).join(", "); }
    if (typeof c.value === "boolean") return c.value ? L("да", "yes") : L("нет", "no");
    if (c.type === "tokens") return num(c.value);
    return String(c.value ?? "");
  }
  function fmtActual(c) {
    if (typeof c.actual === "boolean") return c.actual ? L("да", "yes") : L("нет", "no");
    if (c.actual == null || c.actual === "") return "—";
    const o = OPTS[c.type] || {};
    if (o[c.actual]) return W(o[c.actual]);
    if (c.type === "tokens") return num(c.actual);
    return String(c.actual);
  }
  function renderTry() {
    const box = $("#cmp-try");
    if (!box) return;
    if (S.trying) { box.innerHTML = `<div class="cmp-try dim">${esc(L("Отправляю…", "Sending…"))}</div>`; return; }
    const r = S.tryRes;
    if (!r) { box.innerHTML = ""; return; }
    const route = r.route || {};
    const steps = (route.attempts || []).map((a) => `<span class="cmp-step ${a.ok ? "ok" : "err"}">${esc(modelName(a.model))}${a.ok ? " ✓" : ` — ${esc(W(FAIL[a.trigger] || [a.trigger || "?", a.trigger || "?"]))}`}<i>${a.ms ? ` ${(a.ms / 1000).toFixed(1)} ${esc(L("с", "s"))}` : ""}</i></span>`).join(`<span class="dim">→</span>`);
    box.innerHTML = `<div class="cmp-try ${r.ok ? "ok" : "err"}">
      <div class="cmp-try-steps">${steps || ""}</div>
      ${r.ok ? `<div class="cmp-try-answer">${esc(r.answer || L("(пустой ответ)", "(empty answer)"))}</div>` : `<div class="warn-strip">${esc(L(r.error?.message || "", r.error?.message_en || r.error?.message || ""))}</div>`}
      <div class="dim">${esc(L("Время", "Time"))}: ${(Number(r.ms || 0) / 1000).toFixed(1)} ${esc(L("с", "s"))}</div></div>`;
  }

  function statsHtml() {
    if (!S.orig) return "";
    const st = (S.data.stats || {})[S.orig];
    if (!st || !st.requests) {
      return `<div class="card cmp-stats"><div class="cmp-sec-head"><h3>${esc(L("Статистика", "Statistics"))}</h3><span class="sub">${esc(L("Здесь появится, какие правила срабатывают, какие модели отвечают и как часто приходится переключаться.", "Here you will see which rules fire, which models answer and how often a fallback is needed."))}</span></div></div>`;
    }
    const rules = Object.entries(st.rules || {}).sort((a, b) => b[1] - a[1]);
    const max = Math.max(1, ...rules.map((x) => x[1]));
    const ruleName = (id) => (id === "default" ? L("Основной список", "Main list") : (S.cur.rules.find((r) => r.id === id)?.title || id));
    const models = Object.entries(st.models || {}).sort((a, b) => b[1].tried - a[1].tried).slice(0, 10);
    const trig = Object.entries(st.triggers || {}).filter(([k]) => k);
    const outcome = (d) => (d.ok ? `<b>${esc(modelName(d.chosen))}</b>` : d.chosen ? `<b class="cmp-err">${esc(modelName(d.chosen))} — ${esc(L("ответ оборвался", "the answer broke off"))}</b>` : `<b class="cmp-err">${esc(L("ошибка", "error"))}</b>`);
    const recent = (st.recent || []).slice(0, 8).map((d) => `<li><span class="dim">${esc(fmtDateTime(d.ts))}</span> ${esc(ruleName(d.rule))} → ${outcome(d)}
      ${(d.attempts || []).length > 1 || !d.ok ? `<span class="dim">(${(d.attempts || []).map((a) => `${esc(modelName(a.model))}${a.ok ? "" : ": " + esc(W(FAIL[a.trigger] || [a.trigger || "?", a.trigger || "?"]))}`).join(" → ")})</span>` : ""}
      <span class="dim">${(Number(d.ms || 0) / 1000).toFixed(1)} ${esc(L("с", "s"))}</span></li>`).join("");
    return `<div class="card cmp-stats">
      <div class="cmp-sec-head"><h3>${esc(L("Статистика", "Statistics"))}</h3><span class="fill"></span><button class="btn small" data-act="stats-reset">${esc(L("Сбросить", "Reset"))}</button></div>
      <div class="cmp-kpis"><div><b>${num(st.requests)}</b><span>${esc(L("запросов", "requests"))}</span></div>
        <div><b>${Math.round(st.ok / st.requests * 100)}%</b><span>${esc(L("успешно", "succeeded"))}</span></div>
        <div><b>${num(st.fallbacks)}</b><span>${esc(L("с переключением", "with a fallback"))}</span></div>
        <div><b>${num(st.failed)}</b><span>${esc(L("ошибок", "errors"))}</span></div></div>
      <div class="cmp-stats-grid">
        <div><b>${esc(L("Какие правила срабатывали", "Which rules fired"))}</b>${rules.map(([id, n]) => `<div class="cmp-bar"><span>${esc(ruleName(id))}</span><i style="width:${Math.max(4, n / max * 100)}%"></i><em>${num(n)}</em></div>`).join("")}</div>
        <div><b>${esc(L("Модели", "Models"))}</b><table class="cmp-table"><tr><th></th><th>${esc(L("попыток", "tries"))}</th><th>${esc(L("ответила", "answered"))}</th><th>${esc(L("сбоев", "failed"))}</th></tr>
          ${models.map(([id, x]) => `<tr><td title="${esc(id)}">${esc(modelName(id))}<span class="mono dim cmp-tid">${esc(id)}</span></td><td>${num(x.tried)}</td><td>${num(x.ok)}</td><td>${num(x.failed)}</td></tr>`).join("")}</table>
          ${trig.length ? `<div class="cmp-trigs">${trig.map(([k, n]) => `<span class="cmp-badge">${esc(W(FAIL[k] || [k, k]))}: ${num(n)}</span>`).join("")}</div>` : ""}</div>
      </div>
      ${recent ? `<div class="cmp-out-sec"><b>${esc(L("Последние запросы", "Recent requests"))}</b><ul class="cmp-recent">${recent}</ul></div>` : ""}
    </div>`;
  }

  function renderIssues() {
    const box = $("#cmp-issues");
    if (!box) return;
    const list = S.issues || [];
    if (!list.length) { box.innerHTML = ""; return; }
    box.innerHTML = list.map((i, n) => `<button class="cmp-issue ${i.level}" data-act="goto-issue" data-n="${n}">${i.level === "error" ? "✗" : "!"} ${esc(L(i.message, i.message_en))}</button>`).join("");
  }
  function markIssues() {
    const root = $("#composite-root");
    if (!root) return;
    root.querySelectorAll(".cmp-bad, .cmp-warnmark").forEach((el) => { el.classList.remove("cmp-bad", "cmp-warnmark"); el.removeAttribute("data-issue"); });
    (S.issues || []).forEach((i) => {
      const el = issueEl(i);
      if (el) { el.classList.add(i.level === "error" ? "cmp-bad" : "cmp-warnmark"); el.setAttribute("data-issue", L(i.message, i.message_en)); }
    });
    const save = $("#cmp-save");
    if (save) save.title = (S.issues || []).some((i) => i.level === "error") ? L("Сначала исправьте ошибки", "Fix the errors first") : "";
  }
  function issueEl(issue) {
    const root = $("#composite-root");
    let p = ipath(issue.path);
    while (p) {
      const el = root.querySelector(`[data-ipath="${CSS.escape(p)}"]`);
      if (el) return el;
      p = p.includes(".") ? p.slice(0, p.lastIndexOf(".")) : "";
    }
    return null;
  }

  function openPicker(anchor, path, mode) {
    closePicker();
    const taken = new Set((mode === "ids" ? getPath(S.cur, path) || [] : (getPath(S.cur, path) || []).map((c) => c.model)).map((x) => x.toLowerCase()));
    const pop = document.createElement("div");
    pop.className = "cmp-picker";
    pop.innerHTML = `<input type="search" placeholder="${esc(L("Поиск: название, id, подключение", "Search: name, id, connection"))}" aria-label="${esc(L("Поиск модели", "Search models"))}">
      <div class="cmp-pick-filters">${[["vision", "со зрением", "vision"], ["tools", "инструменты", "tools"], ["free", "бесплатные", "free"]].map(([k, ru, en]) => `<button type="button" class="chip-btn" data-f="${k}">${esc(L(ru, en))}</button>`).join("")}</div>
      <div class="cmp-pick-list" role="listbox"></div>
      <div class="cmp-pick-foot dim">${esc(L("Нажмите на модель, чтобы добавить; Esc — закрыть", "Click a model to add it; Esc closes"))}</div>`;
    document.body.appendChild(pop);
    const filters = new Set();
    const listEl = pop.querySelector(".cmp-pick-list");
    const input = pop.querySelector("input");
    function draw() {
      const q = input.value.trim().toLowerCase();
      const rows = avail().filter((m) => m.id !== S.cur.id && !(m.composite && m.enabled === false))
        .filter((m) => !q || `${m.id} ${m.name} ${m.account}`.toLowerCase().includes(q))
        .filter((m) => (!filters.has("vision") || m.vision === "yes") && (!filters.has("tools") || m.tools) && (!filters.has("free") || m.free));
      const groups = {};
      rows.forEach((m) => { (groups[m.composite ? L("Составные модели", "Composite models") : m.account] ||= []).push(m); });
      listEl.innerHTML = Object.entries(groups).map(([g, ms]) => `<div class="cmp-pick-group">${esc(g)} <span class="dim">${ms.length}</span></div>` + ms.slice(0, 300).map((m) =>
        `<button type="button" class="cmp-pick-item ${taken.has(m.id.toLowerCase()) ? "taken" : ""}" data-id="${esc(m.id)}" role="option"><span class="cmp-cand-name"><b>${esc(m.name || m.id)}</b><span class="mono dim">${esc(m.id)}</span></span><span class="cmp-cand-caps">${capsBadges(m)}</span>${taken.has(m.id.toLowerCase()) ? '<span class="cmp-tick">✓</span>' : ""}</button>`).join("")).join("")
        || `<div class="dim cmp-pick-empty">${esc(avail().length ? L("Ничего не найдено", "Nothing found") : L("В Proxy пока нет моделей — добавьте аккаунт", "No models in the Proxy yet — add an account"))}</div>`;
    }
    draw();
    input.oninput = draw;
    pop.querySelectorAll("[data-f]").forEach((b) => { b.onclick = () => { b.classList.toggle("active"); filters.has(b.dataset.f) ? filters.delete(b.dataset.f) : filters.add(b.dataset.f); draw(); }; });
    listEl.onclick = (e) => {
      const item = e.target.closest(".cmp-pick-item");
      if (!item) return;
      const id = item.dataset.id;
      const list = getPath(S.cur, path) || [];
      if (taken.has(id.toLowerCase())) {
        const idx = mode === "ids" ? list.findIndex((x) => x.toLowerCase() === id.toLowerCase()) : list.findIndex((c) => c.model.toLowerCase() === id.toLowerCase());
        if (idx >= 0) list.splice(idx, 1);
        taken.delete(id.toLowerCase());
      } else {
        list.push(mode === "ids" ? id : { model: id, weight: 1 });
        taken.add(id.toLowerCase());
      }
      setPath(S.cur, path, list);
      draw();
      S.picker.changed = true;
    };
    const place = () => {
      const r = anchor.getBoundingClientRect();
      const w = Math.min(460, window.innerWidth - 16);
      pop.style.width = w + "px";
      pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
      const below = window.innerHeight - r.bottom;
      if (below > 320 || below > r.top) { pop.style.top = (r.bottom + 6) + "px"; pop.style.maxHeight = Math.max(240, below - 16) + "px"; pop.style.bottom = ""; }
      else { pop.style.bottom = (window.innerHeight - r.top + 6) + "px"; pop.style.top = ""; pop.style.maxHeight = Math.max(240, r.top - 16) + "px"; }
    };
    place();
    S.picker = { el: pop, changed: false, place };
    setTimeout(() => input.focus(), 0);
  }
  function closePicker() {
    if (!S.picker) return;
    const changedNow = S.picker.changed;
    S.picker.el.remove();
    S.picker = null;
    if (changedNow) changed(true);
  }
  document.addEventListener("mousedown", (e) => { if (S.picker && !S.picker.el.contains(e.target) && !e.target.closest("[data-act=pick]")) closePicker(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.picker) { e.preventDefault(); closePicker(); } });
  window.addEventListener("resize", () => { if (S.picker) S.picker.place(); });

  function startDrag(e, grip) {
    const item = grip.closest(grip.dataset.grip === "rule" ? ".cmp-rule" : ".cmp-cand");
    const container = item && item.parentElement;
    if (!item || !container) return;
    e.preventDefault();
    const from = Number(item.dataset.i);
    const sel = grip.dataset.grip === "rule" ? ".cmp-rule" : ".cmp-cand";
    item.classList.add("dragging");
    container.classList.add("drag-on");
    if (grip.dataset.grip === "rule") {

      const before = item.getBoundingClientRect().top;
      container.classList.add("drag-collapse");
      window.scrollBy(0, item.getBoundingClientRect().top - before);
    }
    try { grip.setPointerCapture(e.pointerId); } catch (err) {  }
    let lastY = e.clientY, raf = 0, cancelled = false;
    const autoscroll = () => {
      const edge = 70, h = window.innerHeight;
      let dy = 0;
      if (lastY < edge) dy = -Math.ceil((edge - lastY) / 4);
      else if (lastY > h - edge) dy = Math.ceil((lastY - (h - edge)) / 4);
      if (dy) { window.scrollBy(0, dy); reorder(); }
      raf = requestAnimationFrame(autoscroll);
    };
    const reorder = () => {
      const siblings = Array.from(container.querySelectorAll(":scope > " + sel)).filter((x) => x !== item);
      let before = null;
      for (const sib of siblings) { const r = sib.getBoundingClientRect(); if (lastY < r.top + r.height / 2) { before = sib; break; } }
      if (before) { if (item.nextElementSibling !== before) container.insertBefore(item, before); }
      else if (container.lastElementChild !== item) container.appendChild(item);
    };
    const move = (ev) => { lastY = ev.clientY; reorder(); };
    const key = (ev) => { if (ev.key === "Escape") { cancelled = true; up(); } };
    const up = () => {
      cancelAnimationFrame(raf);
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up);
      document.removeEventListener("keydown", key, true);
      item.classList.remove("dragging");
      container.classList.remove("drag-on", "drag-collapse");
      const order = Array.from(container.querySelectorAll(":scope > " + sel)).map((x) => Number(x.dataset.i));
      const to = order.indexOf(from);
      if (cancelled || to === from) { render(); return; }
      if (grip.dataset.grip === "rule") moveItem(S.cur.rules, from, to);
      else moveItem(getPath(S.cur, container.dataset.list), from, to);
      changed(true);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    grip.addEventListener("pointercancel", up);
    document.addEventListener("keydown", key, true);
    raf = requestAnimationFrame(autoscroll);
  }
  function moveItem(list, from, to) { if (!list || to < 0 || to >= list.length) return; const [x] = list.splice(from, 1); list.splice(to, 0, x); }

  function bind(root) {
    root.onclick = (e) => {
      const el = e.target.closest("[data-act]");
      if (e.target.closest("[data-stop]")) return;
      if (!el || !root.contains(el)) return;
      act(el, e);
    };
    root.onkeydown = (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches(".cmp-item")) { e.preventDefault(); select(e.target.dataset.id); return; }
      const grip = e.target.closest && e.target.closest(".cmp-grip");
      if (grip && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        const d = e.key === "ArrowUp" ? -1 : 1;
        if (grip.dataset.grip === "rule") { const i = Number(grip.closest(".cmp-rule").dataset.i); moveItem(S.cur.rules, i, i + d); }
        else { const li = grip.closest(".cmp-cand"); const i = Number(li.dataset.i); moveItem(getPath(S.cur, li.parentElement.dataset.list), i, i + d); }
        changed(true);
        setTimeout(() => { const all = $$("#composite-root .cmp-grip[data-grip='" + grip.dataset.grip + "']"); (all[Math.max(0, Array.from(all).indexOf(grip) + d)] || all[0])?.focus(); }, 0);
      }
    };
    root.onpointerdown = (e) => { const g = e.target.closest(".cmp-grip"); if (g && e.button === 0) startDrag(e, g); };
    root.oninput = (e) => {
      const el = e.target;
      if (el.dataset.sim !== undefined) {
        S.sample[el.dataset.sim] = el.type === "number" ? (el.value === "" ? "" : Number(el.value)) : el.value;
        scheduleSim(); return;
      }
      if (el.dataset.time) {
        const parts = String(getPath(S.cur, el.dataset.time) || "09:00-18:00").split("-");
        parts[Number(el.dataset.part)] = el.value || "00:00";
        setPath(S.cur, el.dataset.time, `${parts[0] || "00:00"}-${parts[1] || "00:00"}`);
        changed(false); return;
      }
      if (!el.dataset.bind || el.tagName === "SELECT" || el.type === "checkbox") return;
      setPath(S.cur, el.dataset.bind, readVal(el));
      changed(false);
    };
    root.onchange = (e) => {
      const el = e.target;
      if (el.dataset.act === "toggle") return;
      if (el.dataset.act === "kind") { changeKind(Number(el.dataset.i), el.value); return; }
      if (el.dataset.sim !== undefined) { S.sample[el.dataset.sim] = el.value; scheduleSim(0); return; }
      if (!el.dataset.bind || !(el.tagName === "SELECT" || el.type === "checkbox")) return;
      setPath(S.cur, el.dataset.bind, readVal(el));
      if (el.dataset.rerender === "cond") {
        const p = el.dataset.bind.replace(/\.type$/, "");
        setPath(S.cur, p, defaultCond(el.value));
      }
      changed(!!el.dataset.rerender || el.dataset.bind.endsWith(".type") || el.dataset.bind.endsWith("strategy.type"));
    };
    const file = $("#cmp-file");
    if (file) file.onchange = importFile;
    root.querySelectorAll(".cmp-item .switch input").forEach((input) => {
      input.onclick = (e) => e.stopPropagation();
      input.onchange = () => toggleModel(input.dataset.id, input.checked);
    });
  }
  function readVal(el) {
    if (el.dataset.type === "bool") return el.type === "checkbox" ? el.checked : el.value === "true";
    if (el.dataset.type === "num") return el.value === "" ? (el.dataset.bind.endsWith("min_context") ? 0 : null) : Number(el.value);
    return el.value;
  }
  function changeKind(i, kind) {
    const old = S.cur.rules[i];
    const fresh = newRule(kind);
    fresh.id = old.id; fresh.title = old.title; fresh.enabled = old.enabled; fresh.match = old.match; fresh.conditions = old.conditions;
    if (kind === "route" && old.models) fresh.candidates = old.models.map((m) => ({ model: m, weight: 1 }));
    if ((kind === "prefer" || kind === "exclude") && old.candidates) fresh.models = old.candidates.map((c) => c.model);
    if ((kind === "prefer" || kind === "exclude") && old.models) fresh.models = old.models;
    if (kind === "filter" && old.filters) fresh.filters = old.filters;
    S.cur.rules[i] = fresh;
    changed(true);
  }
  async function toggleModel(id, enabled) {
    try { await api("/api/admin/composite/toggle", { method: "POST", body: { id, enabled } }); if (S.orig === id && !S.dirty) S.cur.enabled = enabled; await load(true); }
    catch (e) { toast(errorOf(e), "err"); }
  }
  function act(el, e) {
    const a = el.dataset.act;
    const path = el.dataset.path;
    const i = Number(el.dataset.i);
    switch (a) {
      case "select": if (select(el.dataset.id)) window.scrollTo({ top: 0, behavior: "smooth" }); break;
      case "new": openNew(); break;
      case "back": if (!S.dirty || confirm(L("Есть несохранённые изменения. Отбросить их?", "There are unsaved changes. Discard them?"))) { S.cur = null; S.orig = ""; S.dirty = false; render(); } break;
      case "discard": S.cur = null; S.orig = ""; S.dirty = false; if (S.data.models[0] && window.innerWidth > 900) select(S.data.models[0].id, true); render(); scheduleSim(0); break;
      case "presets": openPresets(); break;
      case "preset": { const p = (S.data.presets || []).find((x) => x.key === el.dataset.key); closeModal?.(); if (p) openNew(clone(p.model)); break; }
      case "import": $("#cmp-file")?.click(); break;
      case "export-all": exportJson(""); break;
      case "export-one": exportJson(S.orig); break;
      case "duplicate": { const copy = clone(S.cur); copy.name = `${copy.name}-copy`; delete copy.created; S.dirty = false; openNew(copy); break; }
      case "delete": confirmDialog(L("Удалить составную модель?", "Delete the composite model?"), L(`«${S.orig}» исчезнет из списков моделей; программы, которые её вызывают, получат ошибку.`, `“${S.orig}” disappears from model lists; programs that call it will get an error.`), async () => {
        await api("/api/admin/composite/delete", { method: "POST", body: { id: S.orig } });
        S.cur = null; S.orig = ""; S.dirty = false; toast(L("Удалено", "Deleted"), "ok", 1500); await load();
      }); break;
      case "save": save(); break;
      case "add-rule": { const r = newRule(el.dataset.kind); S.cur.rules.push(r); changed(true); setTimeout(() => $$("#composite-root .cmp-rule[data-i]").pop()?.scrollIntoView({ behavior: "smooth", block: "center" }), 30); break; }
      case "rule-up": moveItem(S.cur.rules, i, i - 1); changed(true); break;
      case "rule-down": moveItem(S.cur.rules, i, i + 1); changed(true); break;
      case "rule-dup": { const r = clone(S.cur.rules[i]); r.id = newRule().id; r.title = r.title ? `${r.title} (${L("копия", "copy")})` : ""; S.cur.rules.splice(i + 1, 0, r); changed(true); break; }
      case "rule-del": S.cur.rules.splice(i, 1); changed(true); break;
      case "collapse": S.collapsed.has(el.dataset.id) ? S.collapsed.delete(el.dataset.id) : S.collapsed.add(el.dataset.id); el.closest(".cmp-rule").classList.toggle("collapsed"); break;
      case "add-cond": { const rule = getPath(S.cur, path); rule.conditions = rule.conditions || []; rule.conditions.push(defaultCond(rule.conditions.length ? "tokens" : "has_image")); changed(true); break; }
      case "del-cond": getPath(S.cur, path).conditions.splice(i, 1); changed(true); break;
      case "quick": setPath(S.cur, path, Number(el.dataset.v)); changed(true); break;
      case "set-toggle": { const list = getPath(S.cur, path) || []; const v = String(el.dataset.v); const at = list.indexOf(v); at >= 0 ? list.splice(at, 1) : list.push(v); setPath(S.cur, path, list); el.classList.toggle("active"); changed(false); break; }
      case "flag": { const cur = !!getPath(S.cur, path); setPath(S.cur, path, !cur); if (cur) { const parent = getPath(S.cur, path.slice(0, path.lastIndexOf("."))); delete parent[path.slice(path.lastIndexOf(".") + 1)]; } el.classList.toggle("active"); changed(false); break; }
      case "pick": openPicker(el, path, el.dataset.mode || "cands"); break;
      case "cand-up": moveItem(getPath(S.cur, path), i, i - 1); changed(true); break;
      case "cand-down": moveItem(getPath(S.cur, path), i, i + 1); changed(true); break;
      case "cand-del": getPath(S.cur, path).splice(i, 1); changed(true); break;
      case "chip-del": getPath(S.cur, path).splice(i, 1); changed(true); break;
      case "trig": { const fb = getPath(S.cur, path); fb.on = fb.on || []; const at = fb.on.indexOf(el.dataset.v); at >= 0 ? fb.on.splice(at, 1) : fb.on.push(el.dataset.v); el.classList.toggle("active"); changed(false); break; }
      case "fb-own": {
        const fb = getPath(S.cur, path); const k = el.dataset.k;
        if (el.checked) fb[k] = k === "on" ? [...(S.cur.fallback.on || [])] : (S.cur.fallback[k] ?? 0); else delete fb[k];
        changed(true); break;
      }
      case "sim-flag": S.sample[el.dataset.k] = !S.sample[el.dataset.k]; el.classList.toggle("active"); scheduleSim(0); break;
      case "sim-run": scheduleSim(0); break;
      case "try": tryReal(); break;
      case "stats-reset": api("/api/admin/composite/stats/reset", { method: "POST", body: { id: S.orig } }).then(() => load(true)).catch((err) => toast(errorOf(err), "err")); break;
      case "goto-issue": { const issue = S.issues[Number(el.dataset.n)]; const target = issue && issueEl(issue); if (target) { target.scrollIntoView({ behavior: "smooth", block: "center" }); target.classList.add("cmp-flash"); setTimeout(() => target.classList.remove("cmp-flash"), 1200); } break; }
      default: break;
    }
  }

  function openPresets() {
    openModal(`<h3>${esc(L("Шаблоны составных моделей", "Composite model templates"))}</h3>
      <p class="muted">${esc(L("Модели в шаблон подставляются из ваших подключений — после выбора их можно поменять.", "The models are picked from your connections — you can change them after choosing."))}</p>
      <div class="cmp-presets in-modal">${presetCards()}</div>
      <div class="row-end"><button class="btn" id="cmp-pre-close">${esc(L("Закрыть", "Close"))}</button></div>`);
    $("#cmp-pre-close").onclick = closeModal;
    $$(".cmp-presets.in-modal .cmp-preset").forEach((b) => { b.onclick = () => { const p = (S.data.presets || []).find((x) => x.key === b.dataset.key); closeModal(); if (p) openNew(clone(p.model)); }; });
  }
  async function exportJson(id) {
    try {
      const data = await api("/api/admin/composite/export" + (id ? `?id=${encodeURIComponent(id)}` : ""));
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (id ? id.replace("/", "-") : "composite-models") + ".json";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } catch (e) { toast(errorOf(e), "err"); }
  }
  async function importFile(e) {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!f) return;
    let data;
    try { data = JSON.parse(await f.text()); } catch (err) { toast(L("Файл не похож на JSON", "The file is not JSON"), "err"); return; }
    try {
      const res = await api("/api/admin/composite/import", { method: "POST", body: { data, mode: "merge" } });
      toast(L(`Импортировано: ${res.saved.join(", ")}`, `Imported: ${res.saved.join(", ")}`), "ok", 3500);
      await load();
      if (res.saved[0]) select(res.saved[0]);
    } catch (err) { toast(errorOf(err), "err", 7000); }
  }

  function relabel() { if ($("#tab-composite")?.classList.contains("active") && S.data) render(); }

  document.addEventListener("click", (e) => { if (e.target.closest('.nav-item[data-tab="composite"]')) load(true); });
  if (typeof onLangChanged === "function") {
    const previous = onLangChanged;
    // eslint-disable-next-line no-global-assign
    onLangChanged = function () { previous(); relabel(); };
  }
  let lastNarrow = window.innerWidth <= 900;
  window.addEventListener("resize", () => { const n = window.innerWidth <= 900; if (n !== lastNarrow) { lastNarrow = n; relabel(); } });
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });

  return { load, relabel, state: S };
})();
