

(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tr = (ru, en) => Memu.lang === "en" ? en : ru;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const PRESETS = [
    { id: "openrouter", title: "OpenRouter", url: "https://openrouter.ai/api/v1/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "none", families: [] },
    { id: "openai", title: "OpenAI API", url: "https://api.openai.com/v1/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "bearer", families: [] },
    { id: "google", title: "Google / Gemini API", url: "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000", format: "json", items_path: "models", id_keys: ["name"], label_keys: ["displayName", "name"], auth: "google", families: [], pagination: { token_path: "nextPageToken", parameter: "pageToken" } },
    { id: "anthropic", title: "Anthropic / Claude API", url: "https://api.anthropic.com/v1/models?limit=1000", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["display_name", "name", "id"], auth: "anthropic", families: [], pagination: { token_path: "last_id", parameter: "after_id", more_path: "has_more" } },
    { id: "xai", title: "xAI / Grok API", url: "https://api.x.ai/v1/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "bearer", families: [] },
    { id: "mistral", title: "Mistral API", url: "https://api.mistral.ai/v1/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "bearer", families: [] },
    { id: "groq", title: "Groq API", url: "https://api.groq.com/openai/v1/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "bearer", families: [] },
    { id: "deepseek", title: "DeepSeek API", url: "https://api.deepseek.com/models", format: "json", items_path: "data", id_keys: ["id"], label_keys: ["name", "id"], auth: "bearer", families: [] },
  ];
  const state = {
    loaded: false, busy: false, dirty: false, enabled: false, interval: 3600, telegram: true,
    sources: [], events: [], cursor: 0, historyLoaded: false, historyError: "", notice: "", error: "",
    query: String(Memu.saved?.query || ""),
    familyCounts: new Map(), familyNames: new Map(), familiesLoaded: false, familiesBusy: false, familiesError: "", familyQueries: new Map(),
    expanded: new Set(["openrouter"]),

    tab: "", seen: 0, unreadFrom: -1, period: "all", sourceFilter: "all", onlyUnread: false,
    specs: new Map(), specsByTail: new Map(),
  };
  let queryTimer = 0;
  function familyLabel(id) {
    return ({ google: "Google / Gemini", openai: "OpenAI / GPT", anthropic: "Anthropic / Claude", "x-ai": "xAI / Grok", xai: "xAI / Grok", "meta-llama":"Meta / Llama",qwen:"Qwen",mistralai:"Mistral",deepseek:"DeepSeek", "z-ai":"Z.AI / GLM" })[id] || state.familyNames.get(id) || id.charAt(0).toUpperCase()+id.slice(1);
  }
  function date(value) {
    if (!value) return tr("Ещё не проверялось", "Not checked yet");
    const stamp = +value < 1e12 ? +value * 1000 : +value;
    return new Date(stamp).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU");
  }
  function makeSource(spec, selected = true) {
    const runtime = clone(spec);
    delete runtime.api_key;
    const source = { ...clone(spec), auth: spec.auth || "none", selected, apiKey: "", removeKey: false, runtime };
    delete source.api_key;
    return source;
  }
  function applyStatus(data, replaceDraft = false) {
    const rows = Array.isArray(data?.sources) ? data.sources : [];
    if (replaceDraft || !state.loaded) {
      const configured = new Map(rows.map((source) => [source.id, source]));
      const firstConfiguration = !rows.length && !data?.revision && !data?.interval;

      state.enabled = firstConfiguration ? true : !!data?.enabled;
      state.interval = +data?.interval || 3600; state.telegram = data?.telegram !== false;
      state.sources = PRESETS.map((preset) => makeSource({ ...preset, ...(configured.get(preset.id) || {}) }, configured.has(preset.id) || (firstConfiguration && preset.id === "openrouter")));
      for (const source of rows) if (!PRESETS.some((preset) => preset.id === source.id)) state.sources.push(makeSource(source));
      state.loaded = true; state.dirty = false;
    } else {
      for (const source of state.sources) source.runtime = clone(rows.find((row) => row.id === source.id) || {});
    }
  }
  function families(source) {
    return [...new Set([...(source.id === "openrouter" ? state.familyCounts.keys() : []), ...(source.runtime.families_available || []), ...(source.families || [])])].sort((a, b) => familyLabel(a).localeCompare(familyLabel(b), Memu.lang || "ru"));
  }
  function familyChoices(source, index) {
    const all = !(source.families || []).length, query = (state.familyQueries.get(source.id) || "").toLocaleLowerCase();
    return families(source).map(family => `<label data-family-option="${esc(family)}" ${`${family} ${familyLabel(family)}`.toLocaleLowerCase().includes(query) ? "" : "hidden"}><input type="checkbox" data-family="${esc(family)}" data-source-index="${index}" ${all || (source.families || []).includes(family) ? "checked" : ""}><span>${esc(familyLabel(family))}</span>${state.familyCounts.has(family) ? `<small>${state.familyCounts.get(family)}</small>` : ""}</label>`).join("");
  }
  async function discoverFamilies() {
    if (state.familiesBusy) return;
    state.familiesBusy = true; state.familiesError = ""; updateFamilyLists();
    try {
      const result = await Memu.fetch({url:PRESETS[0].url,raw:true,limit:4800000});
      if (result?.truncated) throw new Error(tr("Ответ API превышает допустимый размер", "API response exceeds the size limit"));
      const rows = JSON.parse(result?.text || "{}").data;
      if (!Array.isArray(rows) || !rows.length) throw new Error(tr("API не вернул список моделей", "API returned no model list"));
      const counts = new Map(), names = new Map(), specs = new Map(), tails = new Map();
      const perMillion = (value) => { const n = Number(value); return value === undefined || value === null || value === "" || !Number.isFinite(n) || n < 0 ? null : n * 1e6; };
      for (const row of rows) {
        if (typeof row.id === "string" && row.id) {
          const arch = row.architecture || {}, pricing = row.pricing || {};
          const spec = { context: +row.context_length || +row.top_provider?.context_length || 0, input: perMillion(pricing.prompt), output: perMillion(pricing.completion),
            inputs: (arch.input_modalities || []).filter((x) => typeof x === "string"), outputs: (arch.output_modalities || []).filter((x) => typeof x === "string") };
          specs.set(row.id.toLowerCase(), spec);
          const tail = norm(row.id);
          if (tail && !tails.has(tail)) tails.set(tail, spec);
        }
        const id = typeof row.id === "string" ? row.id : "", slash = id.indexOf("/");
        if (slash <= 0) continue;
        const family = id.slice(0,slash); counts.set(family,(counts.get(family) || 0)+1);
        const title=typeof row.name==="string"&&row.name.includes(": ")?row.name.split(": ")[0]:"";
        if(title&&!names.has(family))names.set(family,title.charAt(0).toUpperCase()+title.slice(1));
      }
      if (!counts.size) throw new Error(tr("Не удалось определить семейства", "Could not identify model families"));
      state.familyCounts = counts; state.familyNames = names; state.familiesLoaded = true;
      state.specs = specs; state.specsByTail = tails; renderFeed();
    } catch (error) { state.familiesError = String(error?.message || error); }
    finally { state.familiesBusy = false; updateFamilyLists(); }
  }
  function updateFamilyLists() {
    for (const list of document.querySelectorAll("[data-family-list]")) {
      const index = +list.dataset.familyList, source = state.sources[index];
      list.innerHTML = familyChoices(source,index);
      const total = document.querySelector(`[data-family-total="${index}"]`);
      if (total) total.textContent = families(source).length;
    }
    for (const note of document.querySelectorAll("[data-family-notice]")) {
      note.textContent = state.familiesError || (state.familiesBusy ? tr("Загружаю все семейства из OpenRouter…", "Loading every family from OpenRouter…") : tr("Список загружается из API автоматически. Число рядом — количество доступных моделей.", "The list updates automatically from the API. Counts show available models."));
      note.classList.toggle("error",!!state.familiesError);
    }
    bindFamilyInputs();
  }
  function statusText(source) {
    const runtime = source.runtime || {};
    if (runtime.error) return String(runtime.error);
    if (!source.selected) return tr("Источник не выбран", "Source not selected");
    if (!runtime.last_success) return tr("Первая проверка создаст исходный список без уведомлений о старых моделях.", "The first check creates a baseline without notifications about existing models.");
    return `${tr("Моделей", "Models")}: ${runtime.count ?? 0} · ${tr("Проверено", "Checked")}: ${date(runtime.last_success)}`;
  }
  function sourceHtml(source, index) {
    const custom = !PRESETS.some((preset) => preset.id === source.id), all = !(source.families || []).length;
    const familyList = families(source), withFamilies = source.id === "openrouter" || familyList.length > 0;
    return `<section class="am-card ${source.selected ? "selected" : ""}" data-source="${index}">
      <header><label class="am-source-toggle"><input type="checkbox" data-select-source="${index}" ${source.selected ? "checked" : ""}><span class="am-source-monogram" aria-hidden="true">${esc(source.title.charAt(0))}</span><span><b>${esc(source.title)}</b><small>${esc(source.id === "openrouter" ? tr("Публичный каталог", "Public catalog") : source.runtime.key_saved ? tr("Ключ сохранён в Proxy", "Key saved in Proxy") : tr("Выберите для наблюдения", "Select to monitor"))}</small></span></label><button type="button" class="am-expand" data-expand-source="${index}" aria-expanded="${state.expanded.has(source.id)}">${tr("Настроить", "Configure")} <span aria-hidden="true">${state.expanded.has(source.id) ? "−" : "+"}</span></button></header>
      <div class="am-source-body" ${state.expanded.has(source.id) ? "" : "hidden"}>
      ${custom ? `<label>${tr("Название", "Name")}<input data-source-field="title" data-source-index="${index}" maxlength="150" value="${esc(source.title)}"></label><label>API URL (HTTPS)<input type="url" data-source-field="url" data-source-index="${index}" value="${esc(source.url)}" placeholder="https://api.example.com/v1/models"></label>` : `<small>${esc(source.url)}</small>`}
      ${custom ? `<label>${tr("Авторизация", "Authentication")}<select data-source-field="auth" data-source-index="${index}">${[ ["none",tr("Без ключа", "No key")], ["bearer","Authorization: Bearer"], ["google","Google: x-goog-api-key"], ["anthropic","Anthropic: x-api-key"] ].map(([value,label])=>`<option value="${value}" ${source.auth === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>` : ""}
      ${source.auth !== "none" ? `<label>API ${tr("ключ", "key")}<input type="password" autocomplete="new-password" spellcheck="false" data-api-key="${index}" placeholder="${esc(source.runtime.key_saved ? tr("Ключ сохранён; оставьте пустым, чтобы сохранить его", "Key saved; leave blank to keep it") : tr("Вставьте ключ этого сервиса", "Paste this service's key"))}"></label>
        <small>${tr("Ключ передаётся только выбранному API и хранится в настройках Proxy.", "The key is sent only to the selected API and stored in Proxy settings.")}</small>
        ${source.runtime.key_saved ? `<label><input type="checkbox" data-remove-key="${index}" ${source.removeKey ? "checked" : ""}> ${tr("Удалить сохранённый ключ", "Remove saved key")}</label>` : ""}` : `<small>${tr("Публичный список: API-ключ не требуется.", "Public list: no API key required.")}</small>`}
      ${withFamilies ? `<details class="am-family-panel" ${source.id === "openrouter" ? "open" : ""}><summary>${tr("Семейства моделей", "Model families")} <span class="am-count" data-family-total="${index}">${familyList.length}</span><small>${all ? tr("Все семейства", "All families") : tr("Выбрано: ", "Selected: ")+(source.families || []).length}</small></summary>
        <div class="am-actions"><label><input type="checkbox" data-all-families="${index}" ${all ? "checked" : ""}> ${tr("Все, включая новые семейства", "All, including new families")}</label></div>
        ${source.id === "openrouter" ? `<div class="am-actions"><button type="button" data-family-preset="gemini-openai" data-source-index="${index}">Gemini + OpenAI</button><button type="button" data-family-preset="claude" data-source-index="${index}">Claude</button></div>` : ""}
        <label class="am-field"><span>${tr("Найти семейство", "Find a family")}</span><input type="search" data-family-query="${index}" value="${esc(state.familyQueries.get(source.id) || "")}" placeholder="${esc(tr("DeepSeek, Qwen, Meta, Mistral…", "DeepSeek, Qwen, Meta, Mistral…"))}"></label>
        <div class="am-families" data-family-list="${index}">${familyChoices(source,index)}</div>
        ${source.id === "openrouter" ? `<div class="am-family-footer"><small data-family-notice></small><button type="button" data-refresh-families>${tr("Обновить семейства", "Refresh families")}</button></div>` : ""}
        <small>${tr("«Все» автоматически включает будущие семейства. При выборе отдельных семейств уведомления фильтруются по ним.", "All automatically includes future families. Selecting individual families filters notifications to them.")}</small></details>` : ""}
      ${custom ? `<details><summary>${tr("Формат ответа API", "API response schema")}</summary>
        <label>${tr("Схема JSON", "JSON schema")}<select data-schema="${index}"><option value="openai">OpenAI: data → id / name</option><option value="google">Google: models → name / displayName</option><option value="root">${tr("Массив в корне JSON", "Array at JSON root")}</option><option value="custom" selected>${tr("Свои поля", "Custom fields")}</option></select></label>
        <label>${tr("Путь к массиву (точками; пусто — корень)", "Array path (dots; empty for root)")}<input data-source-field="items_path" data-source-index="${index}" value="${esc(source.items_path || "")}" placeholder="data"></label>
        <label>${tr("Поля ID, через запятую", "ID fields, comma separated")}<input data-source-field="id_keys" data-source-index="${index}" value="${esc((source.id_keys || ["id"]).join(", "))}"></label>
        <label>${tr("Поля названия, через запятую", "Name fields, comma separated")}<input data-source-field="label_keys" data-source-index="${index}" value="${esc((source.label_keys || ["name","id"]).join(", "))}"></label>
        <small>${tr("Укажите endpoint, возвращающий JSON со списком доступных моделей. Для собственного API выбирайте схему из его документации.", "Use an endpoint that returns a JSON model list. Select your API's schema from its documentation.")}</small></details>` : ""}
      ${custom ? `<button type="button" class="am-remove" data-remove-source="${index}">${tr("Удалить источник", "Remove source")}</button>` : ""}</div>
      <div class="am-status ${source.runtime.error ? "error" : ""}" data-source-status="${index}" role="status">${esc(statusText(source))}</div>
    </section>`;
  }

  const DAY = 86400000;
  function eventTime(event) { const at = +event.at || 0; return at < 1e12 ? at * 1000 : at; }
  function sourceOf(event) { const title = String(event.title || ""); return title.includes(" · ") ? title.split(" · ").slice(1).join(" · ") : title; }
  function parseEvent(event) {
    const source = sourceOf(event), at = eventTime(event), models = [], other = [];
    for (const line of String(event.text || "").split("\n")) {
      const text = line.trim();
      if (!text) continue;
      const added = /^\+\s+(.+?)\s+·\s+(\S.*)$/.exec(text);
      if (added) models.push({ name: added[1], key: added[2], source, at, event: event.id });
      else other.push(text);
    }
    return { event, source, at, models, other };
  }
  function familyOf(key) {
    let id = String(key || "");
    if (id.startsWith("models/")) id = id.slice(7);
    return id.includes("/") ? id.split("/")[0] : "";
  }
  function hue(text) { let h = 0; for (const ch of String(text)) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return h % 360; }
  function norm(value) { return String(value || "").toLowerCase().replace(/^models\//, "").split("/").pop().replace(/:[a-z0-9-]+$/, "").replace(/[^a-z0-9]+/g, ""); }
  function specFor(model) {
    if (!state.specs.size) return null;
    return state.specs.get(String(model.key).toLowerCase()) || state.specsByTail.get(norm(model.key)) || state.specsByTail.get(norm(model.name)) || null;
  }
  function compact(n) { n = +n || 0; return n >= 1e6 ? `${+(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n); }
  function money(value) {
    if (value === null || value === undefined || !Number.isFinite(value)) return "";
    if (value === 0) return tr("бесплатно", "free");
    return "$" + Memu.i18n.number(value, { maximumFractionDigits: value < 1 ? 3 : 2 });
  }
  const MODALITY = { text: ["текст", "text"], image: ["изобр.", "image"], file: ["файлы", "files"], audio: ["аудио", "audio"], video: ["видео", "video"] };
  function modality(kind) { const pair = MODALITY[kind]; return pair ? tr(pair[0], pair[1]) : kind; }
  function dayLabel(stamp) {
    const day = new Date(stamp); day.setHours(0, 0, 0, 0);
    const today = new Date(); today.setHours(0, 0, 0, 0);
    const diff = Math.round((today - day) / DAY);
    if (diff === 0) return tr("Сегодня", "Today");
    if (diff === 1) return tr("Вчера", "Yesterday");
    return day.toLocaleDateString(Memu.lang === "en" ? "en-US" : "ru-RU", { weekday: "long", day: "numeric", month: "long", ...(day.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}) });
  }
  function timeLabel(stamp) { return new Date(stamp).toLocaleTimeString(Memu.lang === "en" ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit" }); }
  function unread(entry) { return +entry.event > state.unreadFrom; }
  function feedEntries() {
    const parsed = state.events.slice().sort((a, b) => +b.id - +a.id).map(parseEvent);
    const now = Date.now(), needle = state.query.trim().toLocaleLowerCase();
    const models = [], notes = [], keys = new Set();
    for (const item of parsed) {
      if (state.period !== "all" && now - item.at > +state.period * DAY) continue;
      if (state.sourceFilter !== "all" && item.source !== state.sourceFilter) continue;
      for (const model of item.models) {

        const id = `${item.source}|${model.key}`;
        if (keys.has(id)) continue;
        keys.add(id);
        if (state.onlyUnread && !unread(model)) continue;
        if (needle && !`${model.name} ${model.key} ${model.source} ${familyLabel(familyOf(model.key) || "")}`.toLocaleLowerCase().includes(needle)) continue;
        models.push(model);
      }
      if (item.other.length && !state.onlyUnread && (!needle || item.other.join(" ").toLocaleLowerCase().includes(needle))) notes.push(item);
    }
    return { models, notes, sources: [...new Set(parsed.map((item) => item.source).filter(Boolean))] };
  }
  function modelHtml(model) {
    const spec = specFor(model), fam = familyOf(model.key), label = fam ? familyLabel(fam) : model.source;
    const specs = [];
    if (spec?.context) specs.push(`<span class="am-spec"><i>${tr("контекст", "context")}</i>${compact(spec.context)}</span>`);
    if (spec && (Number.isFinite(spec.input) || Number.isFinite(spec.output))) {
      specs.push(`<span class="am-spec"><i>${tr("цена 1M", "price 1M")}</i>${esc(money(spec.input) || "—")} → ${esc(money(spec.output) || "—")}</span>`);
    }
    if (spec?.inputs?.length) specs.push(`<span class="am-spec"><i>${tr("вход", "in")}</i>${esc(spec.inputs.map(modality).join(" · "))}</span>`);
    if (spec?.outputs?.some((kind) => kind !== "text")) specs.push(`<span class="am-spec accent"><i>${tr("выход", "out")}</i>${esc(spec.outputs.map(modality).join(" · "))}</span>`);
    return `<article class="am-model ${unread(model) ? "unread" : ""}" data-model-key="${esc(model.key)}">
      <span class="am-mono" style="--hue:${hue(label)}" aria-hidden="true">${esc(String(label || "?").charAt(0).toUpperCase())}</span>
      <div class="am-model-main">
        <div class="am-model-title"><b title="${esc(model.name)}">${esc(model.name.includes(": ") ? model.name.split(": ").slice(1).join(": ") : model.name)}</b>${unread(model) ? `<span class="am-new">${tr("новая", "new")}</span>` : ""}</div>
        <code>${esc(model.key)}</code>
        ${specs.length ? `<div class="am-specs">${specs.join("")}</div>` : state.specs.size ? `<div class="am-specs"><span class="am-spec dim">${tr("Нет в каталоге OpenRouter", "Not in the OpenRouter catalog")}</span></div>` : ""}
      </div>
      <div class="am-model-actions">
        <button type="button" class="am-act" data-try="${esc(model.key)}" data-name="${esc(model.name)}" title="${esc(tr("Открыть новый чат с этой моделью", "Start a new chat with this model"))}">${ICON.chat}<span>${tr("В чат", "Try in chat")}</span></button>
        <button type="button" class="am-act" data-catalog="${esc(model.key)}" data-name="${esc(model.name)}" title="${esc(tr("Открыть в модуле «Каталог моделей»", "Open in the Model catalog module"))}">${ICON.grid}<span>${tr("В каталоге", "In catalog")}</span></button>
      </div></article>`;
  }
  const ICON = {
    chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    grid: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></svg>',
    copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
    bell: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
    radar: '<svg viewBox="0 0 120 120" aria-hidden="true"><circle cx="60" cy="60" r="52"/><circle cx="60" cy="60" r="34"/><circle cx="60" cy="60" r="16"/><path d="M60 60 103 34"/><circle class="dot" cx="86" cy="44" r="4"/><circle class="dot" cx="38" cy="78" r="3"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7"/></svg>',
  };
  function emptyHtml() {
    const selected = state.sources.some((source) => source.selected && source.runtime?.last_success);
    if (state.historyError) return `<div class="am-empty error">${ICON.radar}<b>${tr("Не удалось загрузить ленту", "Could not load the feed")}</b><p>${esc(state.historyError)}</p></div>`;
    if (!state.historyLoaded) return `<div class="am-empty">${ICON.radar}<b>${tr("Загружаю ленту…", "Loading the feed…")}</b></div>`;
    if (state.events.length) return `<div class="am-empty">${ICON.radar}<b>${tr("Ничего не найдено", "Nothing found")}</b><p>${tr("Измените поиск или фильтры.", "Change the search or filters.")}</p><button type="button" data-reset-feed>${tr("Сбросить фильтры", "Reset filters")}</button></div>`;
    if (!state.sources.some((source) => source.selected)) return `<div class="am-empty">${ICON.radar}<b>${tr("Выберите, за чем следить", "Choose what to watch")}</b><p>${tr("Включите OpenRouter или API нужных лабораторий — новые модели появятся здесь, в MemuAI и в Telegram.", "Enable OpenRouter or the labs' APIs you care about — new models will appear here, in MemuAI and in Telegram.")}</p><button type="button" class="primary" data-go-settings>${tr("Настроить источники", "Set up sources")}</button></div>`;
    return `<div class="am-empty">${ICON.radar}<b>${tr("Пока тихо", "All quiet so far")}</b><p>${selected ? tr("Исходный список уже сохранён. Как только в выбранных API появится новая модель, она окажется здесь.", "The baseline is saved. As soon as a selected API lists a new model, it shows up here.") : tr("Первая проверка запомнит текущие модели без уведомлений; дальше сюда попадут только новые.", "The first check records the current models silently; only new ones land here afterwards.")}</p>${state.enabled ? "" : `<p class="am-warn">${tr("Фоновое наблюдение на паузе.", "Background monitoring is paused.")}</p>`}</div>`;
  }
  function renderFeed() {
    const list = $("#am-feed"); if (!list) return;
    const { models, notes, sources } = feedEntries();
    const unreadCount = state.events.reduce((sum, event) => sum + (+event.id > state.seen ? parseEvent(event).models.length : 0), 0);
    const badge = $("#am-unread-badge"); if (badge) { badge.textContent = unreadCount; badge.hidden = !unreadCount; }
    const chips = $("#am-source-chips");
    if (chips) chips.innerHTML = ["all", ...sources].map((name) => `<button type="button" class="am-chip ${state.sourceFilter === name ? "on" : ""}" data-feed-source="${esc(name)}" aria-pressed="${state.sourceFilter === name}">${esc(name === "all" ? tr("Все источники", "All sources") : name)}</button>`).join("");
    const mark = $("#am-mark-seen"); if (mark) mark.disabled = !unreadCount;
    if (!models.length && !notes.length) { list.innerHTML = emptyHtml(); bindFeed(); return; }

    const days = new Map();
    for (const model of models) {
      const dayKey = new Date(model.at).toDateString();
      if (!days.has(dayKey)) days.set(dayKey, { at: model.at, groups: new Map() });
      const fam = familyOf(model.key), group = fam ? familyLabel(fam) : model.source || "—";
      const groups = days.get(dayKey).groups;
      if (!groups.has(group)) groups.set(group, { label: group, source: model.source, at: model.at, rows: [] });
      groups.get(group).rows.push(model);
    }
    list.innerHTML = [...days.values()].map((day) => `<section class="am-day"><h3 class="am-day-head"><span>${esc(dayLabel(day.at))}</span><small>${[...day.groups.values()].reduce((sum, group) => sum + group.rows.length, 0)} ${tr("мод.", "models")}</small></h3>
      ${[...day.groups.values()].map((group) => `<div class="am-group"><header><span class="am-mono sm" style="--hue:${hue(group.label)}" aria-hidden="true">${esc(group.label.charAt(0).toUpperCase())}</span><b>${esc(group.label)}</b><small>${esc(group.source)} · ${esc(timeLabel(group.at))}</small><span class="am-count">${group.rows.length}</span></header>
        <div class="am-models">${group.rows.map(modelHtml).join("")}</div></div>`).join("")}</section>`).join("")
      + (notes.length ? `<section class="am-day"><h3 class="am-day-head"><span>${tr("Другие изменения", "Other changes")}</span></h3>${notes.map((item) => `<div class="am-note"><b>${esc(item.event.title)}</b><small>${esc(date(item.at))}</small><pre>${esc(item.other.join("\n"))}</pre></div>`).join("")}</section>` : "");
    bindFeed();
  }
  function bindFeed() {
    for (const button of document.querySelectorAll("[data-try]")) button.onclick = () => quick("chat", button);
    for (const button of document.querySelectorAll("[data-catalog]")) button.onclick = () => quick("module", button);
    for (const button of document.querySelectorAll("[data-reset-feed]")) button.onclick = () => { state.query = ""; state.sourceFilter = "all"; state.period = "all"; state.onlyUnread = false; render(); };
    for (const button of document.querySelectorAll("[data-go-settings]")) button.onclick = () => setTab("settings");
    for (const button of document.querySelectorAll("[data-feed-source]")) button.onclick = () => { state.sourceFilter = button.dataset.feedSource; renderFeed(); };
  }
  function feedNotice(text, error = false) {
    const node = $("#am-feed-notice"); if (!node) return;
    node.textContent = text; node.classList.toggle("error", error); node.hidden = !text;
    clearTimeout(feedNotice.timer); feedNotice.timer = setTimeout(() => { node.hidden = true; }, 5000);
  }
  async function quick(kind, button) {
    try {
      const result = kind === "chat"
        ? await Memu.call("host", { command: "chat", models: [button.dataset.try, button.dataset.name] })
        : await Memu.call("host", { command: "module", id: "catalog", query: button.dataset.name || button.dataset.catalog, model: button.dataset.catalog });
      if (result && result.ok === false) {
        feedNotice(result.reason === "not_connected"
          ? tr("Этой модели пока нет среди подключённых в MemuAI Proxy. Добавьте аккаунт поставщика в Proxy → Аккаунты.", "This model is not among the models connected in MemuAI Proxy yet. Add the provider's account in Proxy → Accounts.")
          : result.reason === "disabled" ? tr("Включите модуль «Каталог моделей» в настройках → Модули.", "Enable the Model catalog module in Settings → Modules.")
          : String(result.reason || ""), true);
      }
    } catch (error) { feedNotice(String(error?.message || error), true); }
  }
  async function markSeen() {
    const cursor = state.cursor;
    if (!cursor || cursor <= state.seen) return;
    try {
      const data = await Memu.monitor({ action: "seen", cursor });
      state.seen = Math.max(state.seen, +data?.seen || cursor);
    } catch (error) { feedNotice(String(error?.message || error), true); }
    renderFeed();
  }
  function setTab(tab) {
    state.tab = tab === "settings" ? "settings" : "feed";
    for (const button of document.querySelectorAll("[data-tab]")) { const on = button.dataset.tab === state.tab; button.classList.toggle("on", on); button.setAttribute("aria-selected", String(on)); }
    $("#am-feed-pane").hidden = state.tab !== "feed";
    $("#am-settings-pane").hidden = state.tab !== "settings";
    if (state.tab === "feed") autoSeen();
  }

  function autoSeen() {
    clearTimeout(autoSeen.timer);
    autoSeen.timer = setTimeout(() => { if (state.tab === "feed" && !document.hidden && state.cursor > state.seen) markSeen(); }, 1500);
  }
  function headerStatus() {
    const selected = state.sources.filter((source) => source.selected);
    const last = Math.max(0, ...selected.map((source) => +source.runtime?.last_success || 0));
    const parts = [state.enabled ? tr("Наблюдение включено", "Monitoring on") : tr("Наблюдение на паузе", "Monitoring paused"),
      `${selected.length} ${tr("источн.", selected.length === 1 ? "source" : "sources")}`];
    if (last) parts.push(tr("проверено ", "checked ") + date(last));
    return parts.join(" · ");
  }
  function render() {
    const intervals = [[300,tr("5 минут", "5 minutes")],[900,tr("15 минут", "15 minutes")],[3600,tr("1 час", "1 hour")],[21600,tr("6 часов", "6 hours")],[86400,tr("24 часа", "24 hours")]];
    if (!intervals.some(([value]) => value === state.interval)) intervals.push([state.interval,`${Math.round(state.interval/60)} ${tr("мин", "min")}`]);
    const periods = [["7", tr("7 дней", "7 days")], ["30", tr("30 дней", "30 days")], ["all", tr("Всё время", "All time")]];
    $("#app").innerHTML = `<header class="am-top">
        <div class="am-brand"><span class="am-mark" aria-hidden="true">${ICON.bell}</span><div><h1>${tr("Новые модели API", "New API models")}</h1><small class="am-sub"><span class="am-dot ${state.enabled ? "on" : ""}"></span>${esc(headerStatus())}</small></div></div>
        <nav class="am-tabs" role="tablist" aria-label="${esc(tr("Разделы", "Sections"))}">
          <button type="button" role="tab" data-tab="feed">${tr("Лента", "Feed")}<span class="am-badge" id="am-unread-badge" hidden></span></button>
          <button type="button" role="tab" data-tab="settings">${tr("Источники и уведомления", "Sources & alerts")}</button>
        </nav>
        <div class="am-top-actions"><button type="button" id="am-scan-top" title="${esc(tr("Сохранить и проверить выбранные API сейчас", "Save and check the selected APIs now"))}">${ICON.refresh}<span>${tr("Проверить сейчас", "Check now")}</span></button></div>
      </header>
      <section class="am-pane" id="am-feed-pane" role="tabpanel">
        <div class="am-toolbar">
          <label class="am-search">${'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'}<input type="search" id="am-query" value="${esc(state.query)}" placeholder="${esc(tr("Модель, поставщик или ID", "Model, provider or ID"))}" aria-label="${esc(tr("Поиск по ленте", "Search the feed"))}"></label>
          <div class="am-seg" role="group" aria-label="${esc(tr("Период", "Period"))}">${periods.map(([value, label]) => `<button type="button" data-feed-period="${value}" class="${state.period === value ? "on" : ""}" aria-pressed="${state.period === value}">${esc(label)}</button>`).join("")}</div>
          <label class="am-switch"><input type="checkbox" id="am-unread" ${state.onlyUnread ? "checked" : ""}><span aria-hidden="true"><i></i></span>${tr("Только новые", "Unread only")}</label>
          <span class="am-grow"></span>
          <button type="button" id="am-mark-seen">${ICON.check}<span>${tr("Отметить всё просмотренным", "Mark all as seen")}</span></button>
          <button type="button" class="am-icon" id="am-history-refresh" title="${esc(tr("Обновить ленту", "Refresh the feed"))}" aria-label="${esc(tr("Обновить ленту", "Refresh the feed"))}">${ICON.refresh}</button>
        </div>
        <div class="am-chips" id="am-source-chips"></div>
        <p class="am-feed-notice" id="am-feed-notice" role="status" hidden></p>
        <div class="am-feed" id="am-feed" aria-live="polite"></div>
      </section>
      <section class="am-pane" id="am-settings-pane" role="tabpanel" hidden>
      <section class="am-settings"><div class="am-settings-head"><div><h2>${tr("Наблюдение и уведомления", "Monitoring & alerts")}</h2><small>${tr("Proxy проверяет выбранные API в фоне, даже когда модуль закрыт.", "The Proxy checks the selected APIs in the background, even when the module is closed.")}</small></div></div>
        <div class="am-bar">
        <label class="am-switch"><input type="checkbox" id="am-enabled" ${state.enabled ? "checked" : ""}><span aria-hidden="true"><i></i></span>${tr("Фоновое наблюдение", "Background monitoring")}</label>
        <label class="am-inline">${tr("Интервал", "Interval")} <select id="am-interval">${intervals.map(([value,label])=>`<option value="${value}" ${state.interval === value ? "selected" : ""}>${esc(label)}</option>`).join("")}</select></label>
        <label class="am-switch"><input type="checkbox" id="am-telegram" ${state.telegram ? "checked" : ""}><span aria-hidden="true"><i></i></span>${tr("Дублировать в Telegram", "Also send to Telegram")}</label>
      </div><small>${tr("Уведомления появятся в MemuAI; Telegram использует бота из настроек приложения. Первая успешная проверка — исходный список; затем приходят только новые модели. Показанные уведомления не повторяются после перезапуска.", "Notifications appear in MemuAI; Telegram uses the bot configured in app settings. The first successful check is the baseline; later checks notify about new models only. Shown notifications are not repeated after a restart.")}</small>
      <div class="am-actions"><button type="button" class="primary" id="am-save">${tr("Сохранить", "Save")}</button><button type="button" id="am-scan">${tr("Проверить сейчас", "Check now")}</button><button type="button" id="am-refresh">${tr("Обновить статус", "Refresh status")}</button><span id="am-dirty"></span></div>
      <div class="am-status" id="am-notice" role="status" aria-live="polite"></div></section>
      <div class="am-section-head"><h2>${tr("Источники моделей", "Model sources")}</h2><small>${tr("Включите нужный источник и раскройте его настройки", "Enable a source and expand its settings")}</small></div>
      <div class="am-grid" id="am-sources">${state.sources.map(sourceHtml).join("")}</div>
      <section class="am-card am-add-card"><header><div><h2>${tr("Другой API", "Another API")}</h2><small>${tr("Подключите любой публичный HTTPS API, который возвращает JSON со списком моделей. Адрес, авторизация и поля настраиваются отдельно.", "Connect any public HTTPS API that returns a JSON model list. Configure its URL, authentication, and fields separately.")}</small></div><button type="button" id="am-add">＋ ${tr("Добавить источник", "Add source")}</button></header></section>
      </section>`;
    for (const input of document.querySelectorAll("[data-api-key]")) input.value = state.sources[+input.dataset.apiKey].apiKey;
    $("#app").setAttribute("aria-busy", String(state.busy));
    setTab(state.tab);
    renderHistory(); updateControls(); bind(); updateFamilyLists();

    if(Memu.toolbar&&Memu.toolbar.adopt)Memu.toolbar.adopt({hide:".am-brand",status:".am-sub",buttons:[{id:"scan",el:"#am-scan-top",icon:"refresh",showLabel:true}]});
  }
  function updateControls() {
    for (const selector of ["#am-save", "#am-scan", "#am-scan-top", "#am-refresh", "#am-add", "#am-history-refresh"]) { const node = $(selector); if (node) node.disabled = state.busy; }
    const scanDisabled = state.busy || !state.sources.some((source) => source.selected);
    $("#am-scan").disabled = scanDisabled; $("#am-scan-top").disabled = scanDisabled;
    $("#am-scan-top").classList.toggle("busy", state.busy);
    $("#am-dirty").textContent = state.dirty ? tr("Есть несохранённые изменения", "Unsaved changes") : "";

    if (state.reportedDirty !== state.dirty && typeof Memu.unsaved === "function") {
      state.reportedDirty = state.dirty;
      Memu.unsaved(state.dirty, tr("Настройки наблюдения не сохранены.", "The monitoring settings are not saved.")).catch(() => {});
    }
    $("#am-notice").textContent = state.error || state.notice;
    $("#am-notice").classList.toggle("error", !!state.error);
    $("#app").setAttribute("aria-busy", String(state.busy));
    for (const input of document.querySelectorAll(".am-settings input,.am-settings select,.am-card input,.am-card select,.am-card button")) input.disabled = state.busy;
    if (state.tab === "feed" && state.error) feedNotice(state.error, true);
  }
  function dirty() { state.dirty = true; state.error = ""; state.notice = ""; updateControls(); }
  function renderHistory() { renderFeed(); }
  function specs() {
    return state.sources.filter((source)=>source.selected).map((source)=>{
      const {id,title,url,auth,format,items_path,id_keys,label_keys,families,pagination} = source;
      let parsed;
      try { parsed = new URL(url); } catch (_) { throw new Error(tr("Укажите корректный HTTPS URL для ", "Enter a valid HTTPS URL for ")+title); }
      if (parsed.protocol !== "https:" || parsed.username || parsed.password) throw new Error(tr("Нужен HTTPS без логина/пароля в URL: ", "HTTPS without URL credentials required: ")+title);
      if (!id_keys?.length) throw new Error(tr("Укажите хотя бы одно поле ID: ", "Provide at least one ID field: ")+title);
      const result = {id,title:String(title).trim() || id,url,format:format || "json",items_path:items_path || "",id_keys,label_keys:label_keys?.length ? label_keys : id_keys,families:[...(families || [])]};
      if (pagination) result.pagination = clone(pagination);
      if (auth !== "none") result.auth = auth || "bearer";
      if (source.apiKey.trim()) result.api_key = source.apiKey.trim();
      else if (source.removeKey || auth === "none") result.api_key = "";
      return result;
    });
  }
  async function history() {
    try {
      const data = await Memu.monitor({action:"events",after:state.cursor});
      const byId = new Map(state.events.map((event)=>[event.id,event]));
      for (const event of data?.events || []) if (event.module_id === "api-models") byId.set(event.id,event);
      state.events = [...byId.values()].slice(-200);state.cursor = Math.max(state.cursor,+data?.cursor || 0);state.historyLoaded = true;state.historyError = "";

      if (Number.isFinite(+data?.seen)) { state.seen = Math.max(state.seen, +data.seen); if (state.unreadFrom < 0) state.unreadFrom = state.seen; }
      if (state.tab === "feed") autoSeen();
    } catch (error) { state.historyError = tr("Не удалось загрузить историю: ", "Could not load history: ")+String(error?.message || error); }
    renderHistory();
  }
  async function saveDraft() {
    const sources = specs();
    if (state.enabled && !sources.length) throw new Error(tr("Выберите хотя бы один источник", "Select at least one source"));
    const data = await Memu.monitor({action:"configure",enabled:state.enabled,interval:state.interval,mode:"new",telegram:state.telegram,sources});
    applyStatus(data,true);
  }
  async function perform(action) {
    if (state.busy) return;
    state.busy = true;state.error = "";state.notice = action === "scan" ? tr("Проверяю API…", "Checking APIs…") : tr("Загрузка…", "Loading…");updateControls();
    if (state.tab === "feed") feedNotice(state.notice);
    try {
      if (action === "save" || action === "scan") await saveDraft();
      if (action === "scan") {
        const data = await Memu.monitor({action:"scan"}); applyStatus(data,true);
        state.notice = tr("Проверка завершена. Статус каждого источника — ниже.", "Check completed. Each source's status is shown below.");
      } else if (action === "refresh") { applyStatus(await Memu.monitor({action:"status"}),!state.dirty);state.notice = tr("Статус обновлён", "Status refreshed"); }
      else state.notice = tr("Настройки сохранены", "Settings saved");
      render(); await history();
    } catch (error) { state.error = String(error?.message || error); }
    finally { state.busy = false;updateControls(); }
  }
  function bind() {
    $("#am-enabled").onchange = (event)=>{state.enabled=event.target.checked;dirty();};
    $("#am-interval").onchange = (event)=>{state.interval=+event.target.value;dirty();};
    $("#am-telegram").onchange = (event)=>{state.telegram=event.target.checked;dirty();};
    $("#am-save").onclick = ()=>perform("save");$("#am-scan").onclick = ()=>perform("scan");$("#am-refresh").onclick = ()=>perform("refresh");
    $("#am-history-refresh").onclick = ()=>history();
    $("#am-scan-top").onclick = ()=>perform("scan");
    $("#am-mark-seen").onclick = ()=>{ state.unreadFrom = Math.max(state.unreadFrom, state.cursor); markSeen(); };
    $("#am-unread").onchange = (event)=>{ state.onlyUnread = event.target.checked; renderFeed(); };
    for (const button of document.querySelectorAll("[data-tab]")) button.onclick = ()=>setTab(button.dataset.tab);
    for (const button of document.querySelectorAll("[data-feed-period]")) button.onclick = ()=>{
      state.period = button.dataset.feedPeriod;
      for (const other of document.querySelectorAll("[data-feed-period]")) { const on = other === button; other.classList.toggle("on", on); other.setAttribute("aria-pressed", String(on)); }
      renderFeed();
    };
    $("#am-add").onclick = ()=>{
      const id=`custom-${crypto.randomUUID().slice(0,12)}`;state.expanded.add(id);
      state.sources.push(makeSource({id,title:tr("Свой API", "Custom API"),url:"",format:"json",items_path:"data",id_keys:["id"],label_keys:["name","id"],auth:"bearer",families:[]}));
      dirty();render();$("#am-sources .am-card:last-child input[data-source-field='title']")?.focus();
    };
    $("#am-query").oninput = (event)=>{
      state.query=event.target.value;renderHistory();clearTimeout(queryTimer);queryTimer=setTimeout(()=>Memu.save({query:state.query}).catch(()=>{}),400);
    };
    for (const input of document.querySelectorAll("[data-select-source]")) input.onchange=()=>{const source=state.sources[+input.dataset.selectSource];source.selected=input.checked;if(input.checked)state.expanded.add(source.id);dirty();render();};
    for (const button of document.querySelectorAll("[data-expand-source]")) button.onclick=()=>{const id=state.sources[+button.dataset.expandSource].id;if(state.expanded.has(id))state.expanded.delete(id);else state.expanded.add(id);render();};
    for (const button of document.querySelectorAll("[data-refresh-families]")) button.onclick=()=>discoverFamilies();
    for (const input of document.querySelectorAll("[data-family-query]")) input.oninput=()=>{
      const source=state.sources[+input.dataset.familyQuery],query=input.value.toLocaleLowerCase();state.familyQueries.set(source.id,input.value);
      for (const label of document.querySelectorAll(`[data-family-list="${input.dataset.familyQuery}"] [data-family-option]`)) label.hidden=!`${label.dataset.familyOption} ${familyLabel(label.dataset.familyOption)}`.toLocaleLowerCase().includes(query);
    };
    for (const input of document.querySelectorAll("[data-api-key]")) input.oninput=()=>{
      const source=state.sources[+input.dataset.apiKey];source.apiKey=input.value;if(input.value)source.removeKey=false;dirty();
      const removal=$(`[data-remove-key="${input.dataset.apiKey}"]`);if(removal)removal.checked=source.removeKey;
    };
    for (const input of document.querySelectorAll("[data-remove-key]")) input.onchange=()=>{
      const source=state.sources[+input.dataset.removeKey];source.removeKey=input.checked;if(input.checked){source.apiKey="";$(`[data-api-key="${input.dataset.removeKey}"]`).value="";}dirty();
    };
    for (const input of document.querySelectorAll("[data-source-field]")) {
      const change=()=>{const source=state.sources[+input.dataset.sourceIndex],key=input.dataset.sourceField;source[key]=["id_keys","label_keys"].includes(key)?input.value.split(",").map((value)=>value.trim()).filter(Boolean):input.value;dirty();if(key==="auth")render();};
      if(input.tagName==="SELECT")input.onchange=change;else input.oninput=change;
    }
    for (const input of document.querySelectorAll("[data-all-families]")) input.onchange=()=>{
      const source=state.sources[+input.dataset.allFamilies];source.families=input.checked?[]:families(source);dirty();render();
    };
    for (const button of document.querySelectorAll("[data-family-preset]")) button.onclick=()=>{
      state.sources[+button.dataset.sourceIndex].families=button.dataset.familyPreset==="claude"?["anthropic"]:["google","openai"];dirty();render();
    };
    for (const button of document.querySelectorAll("[data-remove-source]")) button.onclick=()=>{state.sources.splice(+button.dataset.removeSource,1);dirty();render();};
    for (const select of document.querySelectorAll("[data-schema]")) select.onchange=()=>{
      const source=state.sources[+select.dataset.schema];
      if(select.value!=="custom"){
        source.items_path=select.value==="root"?"":select.value==="google"?"models":"data";
        source.id_keys=select.value==="google"?["name"]:["id"];source.label_keys=select.value==="google"?["displayName","name"]:["name","id"];
        delete source.pagination;dirty();render();
      }
    };
  }
  function bindFamilyInputs() {
    for (const input of document.querySelectorAll("[data-family]")) input.onchange=()=>{
      const source=state.sources[+input.dataset.sourceIndex];let selected=(source.families || []).length?[...source.families]:families(source);
      selected=input.checked?[...new Set([...selected,input.dataset.family])]:selected.filter((family)=>family!==input.dataset.family);
      if(!selected.length){input.checked=true;state.notice=tr("Оставьте хотя бы одно семейство или выберите «Все».", "Keep at least one family or choose All.");updateControls();return;}
      source.families=selected;dirty();render();
    };
  }
  async function refreshBackground() {
    if(state.busy || document.hidden || !state.loaded)return;
    try {
      const editing=document.activeElement?.matches("input,select,summary"), replace=!state.dirty&&!editing;
      applyStatus(await Memu.monitor({action:"status"}),replace);
      if(replace)render();else for(const element of document.querySelectorAll("[data-source-status]")){
        const source=state.sources[+element.dataset.sourceStatus];element.textContent=statusText(source);element.classList.toggle("error",!!source.runtime.error);
      }
      await history();
    } catch(error){state.error=String(error?.message || error);updateControls();}
  }
  Memu.onLanguage?.(()=>render());

  Memu.on?.("notification", ()=>history());
  window.addEventListener("pagehide",()=>{clearTimeout(queryTimer);for(const source of state.sources)source.apiKey="";});
  (async()=>{
    try{
      const status = await Memu.monitor({action:"status"});
      applyStatus(status,true);

      state.tab = (status?.sources || []).length ? "feed" : "settings";
      render();await history();
    }
    catch(error){state.loaded=true;state.tab="settings";state.sources=PRESETS.map((source,index)=>makeSource(source,index===0));state.error=String(error?.message || error);render();}
    Memu.ready();
    discoverFamilies();
  })();
  setInterval(refreshBackground,30000);

  setInterval(()=>{if(!document.hidden)discoverFamilies();},1800000);
})();
