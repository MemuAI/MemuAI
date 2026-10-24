/* MemuAI Proxy — what every model can do (Round 22).

   Settings → Model catalog shows capability badges for each row, filter chips, and a card with
   every field the Proxy knows about the model (app/model_capabilities.py): value, source and
   confidence of each, the raw JSON of each source, a copy button for the model id and a vision
   probe that runs only on an explicit click (it spends one real request).

   API: GET /api/admin/models/capabilities, GET /api/admin/models/{id}/info,
        POST /api/admin/models/{id}/probe, POST /api/admin/models/catalog/refresh.
   app.js calls window.MemuModelCaps.load() / badges(id) / passes(id) / renderFilters() / open(id). */
"use strict";
(function () {
  Object.assign(I18N.ru, {
    "mcap.col": "Возможности", "mcap.open": "Все сведения о модели", "mcap.filter": "Возможности:",
    "mcap.f.vision": "Зрение", "mcap.f.tools": "Инструменты", "mcap.f.reasoning": "Рассуждения",
    "mcap.f.structured": "JSON", "mcap.f.web": "Веб-поиск", "mcap.f.audio": "Аудио", "mcap.f.image_out": "Рисует",
    "mcap.f.free": "Бесплатные", "mcap.f.open": "Открытые веса", "mcap.f.deprecated": "Устаревшие",
    "mcap.f.clear": "Сбросить",
    "mcap.t.vision": "Зрение", "mcap.t.tools": "Инструменты", "mcap.t.reasoning": "Рассуждения", "mcap.t.structured_output": "JSON-схема",
    "mcap.t.web_search": "Веб-поиск", "mcap.t.audio_input": "Аудио", "mcap.t.video_input": "Видео", "mcap.t.file_input": "Файлы, PDF",
    "mcap.t.image_output": "Рисует", "mcap.t.streaming": "Поток", "mcap.t.open_weights": "Открытые веса", "mcap.t.free": "Бесплатно",
    "mcap.b.vision": "Видит изображения", "mcap.b.tools": "Вызывает инструменты (function calling)",
    "mcap.b.reasoning": "Умеет рассуждать (thinking)", "mcap.b.structured": "Структурированный вывод / JSON",
    "mcap.b.web": "Встроенный веб-поиск", "mcap.b.audio": "Принимает аудио", "mcap.b.video": "Принимает видео",
    "mcap.b.image_out": "Генерирует изображения", "mcap.b.audio_out": "Отвечает аудио", "mcap.b.free": "Бесплатно",
    "mcap.b.open": "Открытые веса", "mcap.b.deprecated": "Устарела или скоро будет снята", "mcap.b.textonly": "Только текст",
    "mcap.guess": "предположение по названию", "mcap.verified": "подтверждено проверкой",
    "mcap.catalog.none": "Каталоги OpenRouter и Models.dev не загружены — часть сведений неизвестна",
    "mcap.catalog.module": "Сведения из кэша модуля «Каталог моделей»",
    "mcap.catalog.at": "Каталоги обновлены", "mcap.catalog.refresh": "⟳ Метаданные",
    "mcap.catalog.refresh.title": "Скачать свежие OpenRouter и Models.dev (≈6 МБ)",
    "mcap.catalog.done": "Метаданные обновлены", "mcap.catalog.partial": "Обновлено частично",
    "mcap.copy": "Скопировать ID", "mcap.copied": "ID скопирован", "mcap.copyjson": "Скопировать JSON",
    "mcap.close": "Закрыть", "mcap.loading": "Собираю сведения…", "mcap.on": "включена", "mcap.off": "выключена",
    "mcap.unknown": "неизвестно", "mcap.yes": "Да", "mcap.no": "Нет", "mcap.missing": "Неизвестно",
    "mcap.alts": "Другие источники", "mcap.matched": "Найдено в каталогах", "mcap.match.exact": "точное совпадение",
    "mcap.match.canonical": "по каноническому имени",
    "mcap.raw": "Сырые данные (JSON)", "mcap.raw.provider": "Ответ провайдера (/models)", "mcap.raw.openrouter": "OpenRouter",
    "mcap.raw.models.dev": "Models.dev", "mcap.raw.local": "Файл GGUF и настройки запуска", "mcap.raw.probe": "Проверка зрения",
    "mcap.raw.size": "символов",
    "mcap.probe.title": "Проверка зрения", "mcap.probe.text": "Отправить модели одну картинку 64×64 одного цвета и спросить, какой это цвет. Ответ подтверждает зрение надёжнее любых каталогов.",
    "mcap.probe.btn": "Проверить", "mcap.probe.again": "Проверить ещё раз", "mcap.probe.forget": "Забыть результат",
    "mcap.probe.confirm": "Проверить зрение модели?",
    "mcap.probe.cost": "Это настоящий запрос через ваш аккаунт: около 100–300 токенов на вход и несколько на выход (для веб-аккаунтов — один запрос из квоты). Результат запомнится, и клиенты Proxy будут знать, что модель видит картинки.",
    "mcap.probe.running": "Отправляю картинку…", "mcap.probe.yes": "Модель видит изображения", "mcap.probe.no": "Модель не видит изображения",
    "mcap.probe.unknown": "Проверка не дала ответа", "mcap.probe.expected": "загадан цвет", "mcap.probe.answer": "ответ",
    "mcap.g.identity": "Модель", "mcap.g.modalities": "Модальности", "mcap.g.features": "Возможности",
    "mcap.g.limits": "Лимиты", "mcap.g.pricing": "Цены (USD)", "mcap.g.dates": "Даты и статус", "mcap.g.weights": "Веса и файл",
    "mcap.g.other": "Прочее",
    "mcap.k.name": "Название", "mcap.k.kind": "Тип", "mcap.k.vendor": "Разработчик", "mcap.k.family": "Семейство",
    "mcap.k.description": "Описание", "mcap.k.huggingface": "Hugging Face", "mcap.k.tokenizer": "Токенизатор",
    "mcap.k.canonical": "Каноническое имя", "mcap.k.input": "Вход", "mcap.k.output": "Выход", "mcap.k.vision": "Видит изображения",
    "mcap.k.audio_input": "Принимает аудио", "mcap.k.video_input": "Принимает видео", "mcap.k.file_input": "Принимает файлы (PDF)",
    "mcap.k.image_output": "Генерирует изображения", "mcap.k.audio_output": "Отвечает аудио",
    "mcap.k.tools": "Вызов инструментов", "mcap.k.structured_output": "Структурированный вывод (JSON Schema)",
    "mcap.k.json_mode": "Режим JSON (response_format)", "mcap.k.reasoning": "Рассуждения (thinking)",
    "mcap.k.reasoning_efforts": "Уровни рассуждения", "mcap.k.reasoning_mandatory": "Рассуждения обязательны",
    "mcap.k.web_search": "Встроенный веб-поиск", "mcap.k.streaming": "Потоковый ответ", "mcap.k.temperature": "Температура",
    "mcap.k.parameters": "Поддерживаемые параметры", "mcap.k.context": "Окно контекста", "mcap.k.max_output": "Максимум ответа",
    "mcap.k.trained_context": "Контекст обучения", "mcap.k.rate_limits": "Ограничения частоты",
    "mcap.k.billing": "Оплата", "mcap.k.free": "Бесплатно", "mcap.k.price_input": "Вход, за 1M токенов",
    "mcap.k.price_output": "Выход, за 1M токенов", "mcap.k.price_cache_read": "Чтение кэша, за 1M",
    "mcap.k.price_cache_write": "Запись кэша, за 1M", "mcap.k.price_reasoning": "Рассуждения, за 1M",
    "mcap.k.price_audio_input": "Аудио на входе, за 1M", "mcap.k.price_image_input": "Картинка на входе, за штуку",
    "mcap.k.price_image_output": "Картинки на выходе, за 1M", "mcap.k.price_web_search": "Веб-поиск, за запрос",
    "mcap.k.price_request": "За запрос", "mcap.k.price_tiers": "Ступени цен",
    "mcap.k.release_date": "Выпуск", "mcap.k.last_updated": "Обновлена", "mcap.k.knowledge_cutoff": "Знания до",
    "mcap.k.expiration_date": "Снимается с", "mcap.k.deprecated": "Устарела", "mcap.k.status": "Статус",
    "mcap.k.open_weights": "Открытые веса", "mcap.k.license": "Лицензия", "mcap.k.quantization": "Квантование",
    "mcap.k.size_label": "Размер", "mcap.k.architecture": "Архитектура", "mcap.k.layers": "Слоёв", "mcap.k.file_size": "Размер файла",
    "mcap.k.projector": "Проектор (mmproj)", "mcap.k.moderated": "Модерация провайдера",
    "mcap.kind.chat": "чат", "mcap.kind.image": "генерация изображений", "mcap.kind.video": "генерация видео",
    "mcap.kind.embedding": "эмбеддинги", "mcap.kind.stt": "распознавание речи", "mcap.kind.tts": "синтез речи",
    "mcap.kind.music": "музыка", "mcap.kind.audio": "аудио",
    "mcap.bill.subscription": "веб-аккаунт или подписка — цены ниже лишь эквивалент API", "mcap.bill.api": "оплата по API",
    "mcap.bill.local": "локально — бесплатно",
    "mcap.src.user": "Вручную", "mcap.src.probe": "Проверка", "mcap.src.local": "Файл GGUF", "mcap.src.provider": "Провайдер", "mcap.src.openrouter": "OpenRouter",
    "mcap.src.models.dev": "Models.dev", "mcap.src.catalog": "Встроенный справочник", "mcap.src.proxy": "Proxy", "mcap.src.heuristic": "По названию",
    "mcap.conf.verified": "проверено", "mcap.conf.high": "высокая уверенность", "mcap.conf.medium": "средняя уверенность", "mcap.conf.low": "догадка",
    "mcap.tokens": "токенов", "mcap.per.image": "за картинку", "mcap.per.request": "за запрос", "mcap.zero": "бесплатно",
    "mcap.mod.text": "текст", "mcap.mod.image": "изображения", "mcap.mod.audio": "аудио", "mcap.mod.video": "видео", "mcap.mod.file": "файлы",
    "mcap.mod.embeddings": "векторы",
  });
  Object.assign(I18N.en, {
    "mcap.col": "Capabilities", "mcap.open": "Everything about this model", "mcap.filter": "Capabilities:",
    "mcap.f.vision": "Vision", "mcap.f.tools": "Tools", "mcap.f.reasoning": "Reasoning",
    "mcap.f.structured": "JSON", "mcap.f.web": "Web search", "mcap.f.audio": "Audio", "mcap.f.image_out": "Draws",
    "mcap.f.free": "Free", "mcap.f.open": "Open weights", "mcap.f.deprecated": "Deprecated",
    "mcap.f.clear": "Clear",
    "mcap.t.vision": "Vision", "mcap.t.tools": "Tools", "mcap.t.reasoning": "Reasoning", "mcap.t.structured_output": "JSON schema",
    "mcap.t.web_search": "Web search", "mcap.t.audio_input": "Audio", "mcap.t.video_input": "Video", "mcap.t.file_input": "Files, PDF",
    "mcap.t.image_output": "Draws", "mcap.t.streaming": "Streaming", "mcap.t.open_weights": "Open weights", "mcap.t.free": "Free",
    "mcap.b.vision": "Sees images", "mcap.b.tools": "Calls tools (function calling)",
    "mcap.b.reasoning": "Can reason (thinking)", "mcap.b.structured": "Structured output / JSON",
    "mcap.b.web": "Built-in web search", "mcap.b.audio": "Accepts audio", "mcap.b.video": "Accepts video",
    "mcap.b.image_out": "Generates images", "mcap.b.audio_out": "Answers with audio", "mcap.b.free": "Free",
    "mcap.b.open": "Open weights", "mcap.b.deprecated": "Deprecated or being retired", "mcap.b.textonly": "Text only",
    "mcap.guess": "a guess from the name", "mcap.verified": "confirmed by a probe",
    "mcap.catalog.none": "OpenRouter and Models.dev catalogs are not downloaded — some facts are unknown",
    "mcap.catalog.module": "Facts from the “Model catalog” module cache",
    "mcap.catalog.at": "Catalogs updated", "mcap.catalog.refresh": "⟳ Metadata",
    "mcap.catalog.refresh.title": "Download fresh OpenRouter and Models.dev (≈6 MB)",
    "mcap.catalog.done": "Metadata updated", "mcap.catalog.partial": "Partly updated",
    "mcap.copy": "Copy ID", "mcap.copied": "ID copied", "mcap.copyjson": "Copy JSON",
    "mcap.close": "Close", "mcap.loading": "Gathering facts…", "mcap.on": "enabled", "mcap.off": "disabled",
    "mcap.unknown": "unknown", "mcap.yes": "Yes", "mcap.no": "No", "mcap.missing": "Unknown",
    "mcap.alts": "Other sources", "mcap.matched": "Found in catalogs", "mcap.match.exact": "exact match",
    "mcap.match.canonical": "by canonical name",
    "mcap.raw": "Raw data (JSON)", "mcap.raw.provider": "Provider response (/models)", "mcap.raw.openrouter": "OpenRouter",
    "mcap.raw.models.dev": "Models.dev", "mcap.raw.local": "GGUF file and launch settings", "mcap.raw.probe": "Vision probe",
    "mcap.raw.size": "characters",
    "mcap.probe.title": "Vision probe", "mcap.probe.text": "Send the model one 64×64 picture of a single colour and ask which colour it is. The answer confirms vision better than any catalog.",
    "mcap.probe.btn": "Probe", "mcap.probe.again": "Probe again", "mcap.probe.forget": "Forget the result",
    "mcap.probe.confirm": "Probe this model's vision?",
    "mcap.probe.cost": "This is a real request through your account: about 100–300 input tokens and a few output tokens (one request from the quota of a web account). The result is remembered, and Proxy clients will know whether the model sees images.",
    "mcap.probe.running": "Sending the picture…", "mcap.probe.yes": "The model sees images", "mcap.probe.no": "The model does not see images",
    "mcap.probe.unknown": "The probe gave no answer", "mcap.probe.expected": "colour shown", "mcap.probe.answer": "answer",
    "mcap.g.identity": "Model", "mcap.g.modalities": "Modalities", "mcap.g.features": "Features",
    "mcap.g.limits": "Limits", "mcap.g.pricing": "Prices (USD)", "mcap.g.dates": "Dates and status", "mcap.g.weights": "Weights and file",
    "mcap.g.other": "Other",
    "mcap.k.name": "Name", "mcap.k.kind": "Type", "mcap.k.vendor": "Developer", "mcap.k.family": "Family",
    "mcap.k.description": "Description", "mcap.k.huggingface": "Hugging Face", "mcap.k.tokenizer": "Tokenizer",
    "mcap.k.canonical": "Canonical name", "mcap.k.input": "Input", "mcap.k.output": "Output", "mcap.k.vision": "Sees images",
    "mcap.k.audio_input": "Accepts audio", "mcap.k.video_input": "Accepts video", "mcap.k.file_input": "Accepts files (PDF)",
    "mcap.k.image_output": "Generates images", "mcap.k.audio_output": "Answers with audio",
    "mcap.k.tools": "Tool calling", "mcap.k.structured_output": "Structured output (JSON Schema)",
    "mcap.k.json_mode": "JSON mode (response_format)", "mcap.k.reasoning": "Reasoning (thinking)",
    "mcap.k.reasoning_efforts": "Reasoning levels", "mcap.k.reasoning_mandatory": "Reasoning is mandatory",
    "mcap.k.web_search": "Built-in web search", "mcap.k.streaming": "Streaming", "mcap.k.temperature": "Temperature",
    "mcap.k.parameters": "Supported parameters", "mcap.k.context": "Context window", "mcap.k.max_output": "Max output",
    "mcap.k.trained_context": "Training context", "mcap.k.rate_limits": "Rate limits",
    "mcap.k.billing": "Billing", "mcap.k.free": "Free", "mcap.k.price_input": "Input, per 1M tokens",
    "mcap.k.price_output": "Output, per 1M tokens", "mcap.k.price_cache_read": "Cache read, per 1M",
    "mcap.k.price_cache_write": "Cache write, per 1M", "mcap.k.price_reasoning": "Reasoning, per 1M",
    "mcap.k.price_audio_input": "Audio input, per 1M", "mcap.k.price_image_input": "Input image, each",
    "mcap.k.price_image_output": "Output images, per 1M", "mcap.k.price_web_search": "Web search, per request",
    "mcap.k.price_request": "Per request", "mcap.k.price_tiers": "Price tiers",
    "mcap.k.release_date": "Released", "mcap.k.last_updated": "Updated", "mcap.k.knowledge_cutoff": "Knowledge cutoff",
    "mcap.k.expiration_date": "Retired on", "mcap.k.deprecated": "Deprecated", "mcap.k.status": "Status",
    "mcap.k.open_weights": "Open weights", "mcap.k.license": "License", "mcap.k.quantization": "Quantization",
    "mcap.k.size_label": "Size", "mcap.k.architecture": "Architecture", "mcap.k.layers": "Layers", "mcap.k.file_size": "File size",
    "mcap.k.projector": "Projector (mmproj)", "mcap.k.moderated": "Provider moderation",
    "mcap.kind.chat": "chat", "mcap.kind.image": "image generation", "mcap.kind.video": "video generation",
    "mcap.kind.embedding": "embeddings", "mcap.kind.stt": "speech recognition", "mcap.kind.tts": "speech synthesis",
    "mcap.kind.music": "music", "mcap.kind.audio": "audio",
    "mcap.bill.subscription": "web account or subscription — prices below are only the API equivalent", "mcap.bill.api": "billed per API use",
    "mcap.bill.local": "local — free",
    "mcap.src.user": "Manual", "mcap.src.probe": "Probe", "mcap.src.local": "GGUF file", "mcap.src.provider": "Provider", "mcap.src.openrouter": "OpenRouter",
    "mcap.src.models.dev": "Models.dev", "mcap.src.catalog": "Built-in reference", "mcap.src.proxy": "Proxy", "mcap.src.heuristic": "From the name",
    "mcap.conf.verified": "verified", "mcap.conf.high": "high confidence", "mcap.conf.medium": "medium confidence", "mcap.conf.low": "a guess",
    "mcap.tokens": "tokens", "mcap.per.image": "per image", "mcap.per.request": "per request", "mcap.zero": "free",
    "mcap.mod.text": "text", "mcap.mod.image": "images", "mcap.mod.audio": "audio", "mcap.mod.video": "video", "mcap.mod.file": "files",
    "mcap.mod.embeddings": "vectors",
  });

  const ic = (name) => `<i class="mcap-ic" data-i="${name}" aria-hidden="true"></i>`;
  const BADGES = [
    { k: "vision", i: "eye" }, { k: "tools", i: "tools" }, { k: "reasoning", i: "bulb" },
    { k: "structured", i: "braces" }, { k: "web", i: "globe" }, { k: "audio", i: "mic" }, { k: "video", i: "video" },
    { k: "image_out", i: "image" }, { k: "audio_out", i: "speaker" }, { k: "free", i: "tag" }, { k: "open", i: "unlock" },
    { k: "deprecated", i: "warn" },
  ];
  const FILTERS = [
    { k: "vision", i: "eye" }, { k: "tools", i: "tools" }, { k: "reasoning", i: "bulb" }, { k: "structured", i: "braces" },
    { k: "web", i: "globe" }, { k: "audio", i: "mic" }, { k: "image_out", i: "image" }, { k: "free", i: "tag" },
    { k: "open", i: "unlock" }, { k: "deprecated", i: "warn" },
  ];
  const KIND_ICON = { image: "image", video: "video", embedding: "grid", stt: "mic", tts: "speaker", music: "music", audio: "speaker" };
  const TILES = [
    { k: "vision", i: "eye" }, { k: "tools", i: "tools" }, { k: "reasoning", i: "bulb" }, { k: "structured_output", i: "braces" },
    { k: "web_search", i: "globe" }, { k: "audio_input", i: "mic" }, { k: "video_input", i: "video" }, { k: "file_input", i: "file" },
    { k: "image_output", i: "image" }, { k: "streaming", i: "bolt" }, { k: "open_weights", i: "unlock" }, { k: "free", i: "tag" },
  ];
  const GROUP_ICON = { identity: "id", modalities: "eye", features: "chip", limits: "lines", pricing: "coins", dates: "calendar", weights: "box", other: "dots" };
  const KEY_FIELDS = {
    identity: ["vendor", "family"], modalities: ["input", "output", "vision"], features: ["tools", "reasoning", "structured_output", "web_search"],
    limits: ["context", "max_output", "rate_limits"], pricing: ["price_input", "price_output"], dates: ["release_date", "knowledge_cutoff"],
    weights: ["open_weights"],
  };

  const S = { map: {}, at: 0, loading: null, catalog: null, filters: new Set(), card: null, openId: "", seq: 0, probing: false };

  function load(force = false) {
    if (S.loading) return S.loading;
    if (!force && Date.now() - S.at < 60000) return Promise.resolve();
    S.loading = api("/api/admin/models/capabilities").then((data) => {
      const before = JSON.stringify(S.map);
      S.map = (data && data.capabilities) || {};
      S.catalog = (data && data.catalog) || null;
      S.at = Date.now();
      if (before !== JSON.stringify(S.map) && typeof renderModelsTable === "function") renderModelsTable();
      else renderFilters();
    }).catch(() => { /* the list still works without badges */ }).finally(() => { S.loading = null; });
    return S.loading;
  }

  function badgeTitle(key, cap) {
    let title = t("mcap.b." + key);
    if (key === "vision" && cap.vision_source) {
      title += ` · ${t("mcap.src." + cap.vision_source)} · ${t("mcap.conf." + (cap.vision_confidence || "low"))}`;
    }
    return title;
  }

  function badges(id) {
    const cap = S.map[id];
    if (!cap) return S.at ? `<span class="mcap-unknown">—</span>` : "";
    const out = [];
    if (cap.kind && cap.kind !== "chat") {
      out.push(`<span class="mcap-b" data-k="kind" title="${esc(t("mcap.k.kind"))}: ${esc(t("mcap.kind." + cap.kind))}">${ic(KIND_ICON[cap.kind] || "dots")}</span>`);
    }
    for (const badge of BADGES) {
      if (cap[badge.k] !== true) continue;
      if (badge.k === "image_out" && cap.kind === "image") continue;
      let cls = "mcap-b";
      if (badge.k === "vision" && cap.vision_confidence === "verified") cls += " is-verified";
      else if (badge.k === "vision" && cap.vision_source === "heuristic") cls += " is-guess";
      out.push(`<span class="${cls}" data-k="${badge.k}" title="${esc(badgeTitle(badge.k, cap))}">${ic(badge.i)}</span>`);
    }
    if (cap.vision === false && (!cap.kind || cap.kind === "chat")) {
      out.push(`<span class="mcap-b" data-k="textonly" title="${esc(badgeTitle("textonly", cap))}${cap.vision_source ? esc(` · ${t("mcap.src." + cap.vision_source)}`) : ""}">${ic("text")}</span>`);
    }
    return `<span class="mcap-badges">${out.join("")}</span>`;
  }

  function passes(id) {
    if (!S.filters.size) return true;
    const cap = S.map[id];
    if (!cap) return false;
    for (const key of S.filters) if (cap[key] !== true) return false;
    return true;
  }

  function catalogLine() {
    const c = S.catalog;
    if (!c) return "";
    if (c.own && c.at) {
      const when = new Date(c.at * 1000).toLocaleString(LANG === "en" ? "en-US" : "ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
      return `${t("mcap.catalog.at")}: ${when}`;
    }
    if (c.module) return t("mcap.catalog.module");
    return t("mcap.catalog.none");
  }

  function renderFilters() {
    const bar = document.getElementById("models-bulk-bar");
    if (!bar) return;
    let box = document.getElementById("mcap-filters");
    if (!box) {
      box = document.createElement("div");
      box.id = "mcap-filters";
      box.className = "mcap-filters";
      bar.parentNode.insertBefore(box, bar);
      box.addEventListener("click", onFilterClick);
    }
    const counts = {};
    const ids = (typeof modelsCache !== "undefined" ? modelsCache : []).map((m) => m.id);
    for (const id of ids) {
      const cap = S.map[id];
      if (!cap) continue;
      for (const f of FILTERS) if (cap[f.k] === true) counts[f.k] = (counts[f.k] || 0) + 1;
    }
    box.innerHTML = `<span class="mcap-flabel">${esc(t("mcap.filter"))}</span>` + FILTERS.map((f) =>
      `<button type="button" class="chip-btn mcap-fchip" data-mcap-filter="${f.k}" aria-pressed="${S.filters.has(f.k)}" ${counts[f.k] ? "" : (S.filters.has(f.k) ? "" : "disabled")}>${ic(f.i)}<span>${esc(t("mcap.f." + f.k))}</span><span class="dim">${counts[f.k] || 0}</span></button>`).join("")
      + (S.filters.size ? `<button type="button" class="chip-btn mcap-fchip" data-mcap-clear="1">${esc(t("mcap.f.clear"))}</button>` : "")
      + `<span class="grow"></span><span class="mcap-catalog"><span>${esc(catalogLine())}</span><button type="button" class="btn small" data-mcap-catalog="1" title="${esc(t("mcap.catalog.refresh.title"))}">${esc(t("mcap.catalog.refresh"))}</button></span>`;
  }

  async function onFilterClick(event) {
    const chip = event.target.closest("[data-mcap-filter]");
    if (chip) {
      const key = chip.dataset.mcapFilter;
      if (S.filters.has(key)) S.filters.delete(key); else S.filters.add(key);
      if (typeof renderModelsTable === "function") renderModelsTable();
      return;
    }
    if (event.target.closest("[data-mcap-clear]")) {
      S.filters.clear();
      if (typeof renderModelsTable === "function") renderModelsTable();
      return;
    }
    const refresh = event.target.closest("[data-mcap-catalog]");
    if (refresh) await refreshCatalog(refresh);
  }

  async function refreshCatalog(button) {
    if (button) button.disabled = true;
    try {
      const result = await api("/api/admin/models/catalog/refresh", { method: "POST" });
      const failed = Object.entries(result.sources || {}).filter(([, v]) => !v.ok);
      toast(failed.length ? `${t("mcap.catalog.partial")}: ${failed.map(([k, v]) => `${k} — ${v.error}`).join("; ")}` : t("mcap.catalog.done"), failed.length ? "warn" : "ok");
    } catch (e) {
      toast(e.message || String(e), "err");
    } finally {
      if (button) button.disabled = false;
    }
    await load(true);
    if (S.openId) open(S.openId, true);
  }

  /* ----------------------------------------------------------------- formatting */

  function fmtNum(n) { return Number(n).toLocaleString(LANG === "en" ? "en-US" : "ru-RU"); }
  function fmtTokens(n) { return `<span class="num">${esc(fmtNum(n))}</span> <span class="sub">${esc(t("mcap.tokens"))} · ${esc(typeof compactTokens === "function" ? compactTokens(n) : n)}</span>`; }
  function fmtMoney(n) {
    const v = Number(n);
    if (v === 0) return `<span class="mcap-yes">${esc(t("mcap.zero"))}</span>`;
    const digits = v >= 100 ? 0 : v >= 1 ? 2 : v >= 0.01 ? 3 : 6;
    return `<span class="num">$${esc(v.toFixed(digits).replace(/(\.\d*?[1-9])0+$/, "$1").replace(/\.0+$/, ""))}</span>`;
  }
  function fmtBytes(n) {
    const v = Number(n) || 0;
    return v >= 1e9 ? `${(v / 1e9).toFixed(2)} GB` : v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB` : `${fmtNum(v)} B`;
  }
  function modChips(list) {
    const icons = { text: "text", image: "eye", audio: "mic", video: "video", file: "file", embeddings: "grid" };
    return (list || []).map((m) => `<span class="chip">${ic(icons[m] || "dots")}${esc(I18N[LANG]["mcap.mod." + m] ? t("mcap.mod." + m) : m)}</span>`).join("");
  }
  function yesNo(v) { return v ? `<span class="mcap-yes">✓ ${esc(t("mcap.yes"))}</span>` : `<span class="mcap-no">✕ ${esc(t("mcap.no"))}</span>`; }

  function jsonHtml(value) {
    let text;
    try { text = JSON.stringify(value, null, 2); } catch (e) { text = String(value); }
    if (text && text.length > 400000) text = text.slice(0, 400000) + "\n…";
    return esc(text).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)/gi,
      (m, str, colon, bool, num) => str ? (colon ? `<span class="mcap-json-k">${str}</span>${colon}` : `<span class="mcap-json-s">${str}</span>`)
        : bool ? `<span class="mcap-json-b">${bool}</span>` : `<span class="mcap-json-n">${num}</span>`);
  }

  function fmtValue(key, value) {
    if (value === null || value === undefined || value === "") return `<span class="mcap-no">${esc(t("mcap.unknown"))}</span>`;
    if (key === "input" || key === "output") return modChips(value);
    if (typeof value === "boolean") return yesNo(value);
    if (["context", "max_output", "trained_context"].includes(key)) return fmtTokens(value);
    if (key === "price_image_input") return `${fmtMoney(value)} <span class="sub">${esc(t("mcap.per.image"))}</span>`;
    if (key === "price_web_search" || key === "price_request") return `${fmtMoney(value)} <span class="sub">${esc(t("mcap.per.request"))}</span>`;
    if (key.startsWith("price_") && key !== "price_tiers") return fmtMoney(value);
    if (key === "file_size") return `<span class="num">${esc(fmtBytes(value))}</span>`;
    if (key === "kind") return esc(t("mcap.kind." + value));
    if (key === "billing") return esc(I18N[LANG]["mcap.bill." + value] ? t("mcap.bill." + value) : value);
    if (key === "huggingface") return `<span class="mono">${esc(value)}</span>`;
    if (key === "parameters" || key === "reasoning_efforts") return (value || []).map((p) => `<span class="chip">${esc(p)}</span>`).join("");
    if (key === "price_tiers") return (value || []).map((tier) => `<div>≥ ${esc(fmtNum(tier.min_prompt_tokens || 0))} ${esc(t("mcap.tokens"))}: ${fmtMoney(tier.input ?? 0)} / ${fmtMoney(tier.output ?? 0)}</div>`).join("");
    if (typeof value === "object") return `<code class="mono">${esc(JSON.stringify(value))}</code>`;
    return esc(String(value));
  }

  function srcHtml(item) {
    if (!item || !item.source) return "";
    const alts = (item.alts || []).map((a) => `${t("mcap.src." + a.source)}: ${typeof a.value === "object" ? JSON.stringify(a.value) : a.value}${a.note ? ` (${a.note})` : ""}`);
    const title = [`${t("mcap.src." + item.source)} — ${t("mcap.conf." + (item.confidence || "low"))}${item.note ? ` · ${item.note}` : ""}`]
      .concat(alts.length ? [`${t("mcap.alts")}:`, ...alts] : []).join("\n");
    return `<span class="mcap-src" data-conf="${esc(item.confidence || "low")}" title="${esc(title)}">${esc(t("mcap.src." + item.source))}${alts.length ? `<span class="alt">+${alts.length}</span>` : ""}</span>`;
  }

  /* ----------------------------------------------------------------- drawer */

  function close() {
    const drawer = document.getElementById("mcap-drawer");
    const back = document.getElementById("mcap-back");
    S.openId = "";
    S.card = null;
    if (!drawer) return;
    drawer.classList.remove("open");
    if (back) back.classList.remove("open");
    const done = () => { drawer.remove(); if (back) back.remove(); };
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || document.documentElement.hasAttribute("data-reduce-motion")) done(); else setTimeout(done, 200);
    if (S.returnFocus && document.contains(S.returnFocus)) S.returnFocus.focus();
  }

  function ensureDrawer() {
    let drawer = document.getElementById("mcap-drawer");
    if (drawer) return drawer;
    const back = document.createElement("div");
    back.id = "mcap-back";
    back.className = "mcap-back";
    back.addEventListener("click", close);
    drawer = document.createElement("aside");
    drawer.id = "mcap-drawer";
    drawer.className = "mcap-drawer";
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-labelledby", "mcap-title");
    drawer.tabIndex = -1;
    drawer.addEventListener("click", onDrawerClick);
    drawer.addEventListener("toggle", onRawToggle, true);
    drawer.addEventListener("change", onDrawerChange);
    document.body.append(back, drawer);
    requestAnimationFrame(() => { back.classList.add("open"); drawer.classList.add("open"); });
    return drawer;
  }

  async function open(id, keepScroll = false) {
    const drawer = ensureDrawer();
    if (!keepScroll) S.returnFocus = document.activeElement;
    const seq = ++S.seq;
    S.openId = id;
    const scroll = keepScroll ? (drawer.querySelector(".mcap-body") || {}).scrollTop || 0 : 0;
    if (!S.card || S.card.id !== id) {
      drawer.innerHTML = `<div class="mcap-head"><h3 id="mcap-title">${(() => { const name = typeof proxyModelName === "function" ? proxyModelName((typeof modelsCache !== "undefined" && modelsCache.find((m) => m.id === id)) || { id }) : id; return name && name !== id ? `${esc(name)}<small class="mcap-title-id mono">${esc(id)}</small>` : esc(id); })()}</h3><button type="button" class="mcap-icon-btn mcap-close" data-mcap-close title="${esc(t("mcap.close"))}" aria-label="${esc(t("mcap.close"))}">${ic("x")}</button></div><div class="mcap-body"><div class="mcap-loading">${esc(t("mcap.loading"))}</div></div>`;
      drawer.focus();
    }
    try {
      const card = await api(`/api/admin/models/${encodeURIComponent(id)}/info`);
      if (seq !== S.seq) return;
      S.card = card;
      renderCard(drawer, card);
      const body = drawer.querySelector(".mcap-body");
      if (body) body.scrollTop = scroll;
      if (!keepScroll) drawer.querySelector("[data-mcap-close]")?.focus();
    } catch (e) {
      if (seq !== S.seq) return;
      const body = drawer.querySelector(".mcap-body");
      if (body) body.innerHTML = `<div class="mcap-loading">${esc(e.message || String(e))}</div>`;
    }
  }

  function probeBlock(card) {
    const f = card.fields || {};
    const probe = (card.raw || {}).probe;
    let result = "";
    if (S.probing) result = `<div class="mcap-probe-res">${esc(t("mcap.probe.running"))}</div>`;
    else if (S.lastProbe && S.lastProbe.id === card.id) result = probeResultHtml(S.lastProbe.result);
    else if (probe) result = probeResultHtml(probe);
    const adapterBlocked = f.vision && f.vision.source === "proxy";
    return `<div class="mcap-probe">${ic("flask")}<div class="grow"><b>${esc(t("mcap.probe.title"))}</b><p>${esc(t("mcap.probe.text"))}</p>${result}</div>
      <div class="mcap-probe-actions"><button type="button" class="btn small primary" data-mcap-probe ${S.probing || adapterBlocked ? "disabled" : ""}>${esc(probe ? t("mcap.probe.again") : t("mcap.probe.btn"))}</button>
      ${probe ? `<button type="button" class="btn small" data-mcap-forget>${esc(t("mcap.probe.forget"))}</button>` : ""}</div></div>`;
  }

  function probeResultHtml(r) {
    if (!r) return "";
    const cls = r.vision === true ? "ok" : r.vision === false ? "bad" : "unk";
    const head = r.vision === true ? t("mcap.probe.yes") : r.vision === false ? t("mcap.probe.no") : t("mcap.probe.unknown");
    const detail = [r.expected ? `${t("mcap.probe.expected")}: ${r.expected}` : "", r.answer ? `${t("mcap.probe.answer")}: «${r.answer}»` : "", r.error || "",
      r.ts ? new Date(r.ts * 1000).toLocaleString(LANG === "en" ? "en-US" : "ru-RU") : ""].filter(Boolean).join(" · ");
    return `<div class="mcap-probe-res ${cls}"><b>${esc(head)}</b>${detail ? ` — <span class="sub">${esc(detail)}</span>` : ""}</div>`;
  }

  /* Round 24: "through this API" (routing) vs "according to OpenRouter / Models.dev" (reference). */
  const SPLIT_KEYS = ["vision", "tools", "reasoning", "context", "max_output"];
  const L2 = (ru, en) => (LANG === "en" ? en : ru);
  function splitVal(key, item) {
    if (!item) return `<span class="mcap-unk">${esc(L2("неизвестно", "unknown"))}</span>`;
    if (typeof item.value === "boolean") return yesNo(item.value);
    return esc(typeof compactTokens === "function" ? compactTokens(item.value) : String(item.value));
  }
  function splitSrc(item) {
    if (!item) return "";
    const names = { "user-override": L2("вручную", "manual"), "api-probe": L2("проверка", "probe"), provider: L2("провайдер", "provider"),
      "openrouter-reference": item.source === "models.dev" ? "Models.dev" : "OpenRouter" };
    return `<small class="mcap-split-src" title="${esc(item.note || "")}">${esc(names[item.kind] || item.source || "")}</small>`;
  }
  function splitBlock(card) {
    if (!card.effective) return "";
    const over = card.override || {};
    const rows = SPLIT_KEYS.map((key) => {
      const eff = card.effective[key], ref = (card.reference || {})[key];
      const differs = eff && ref && eff.value !== ref.value;
      const control = (key === "context" || key === "max_output")
        ? `<input type="number" min="0" class="mcap-ovr-num" data-mcap-ovr="${key}" value="${esc(over[key] || "")}" placeholder="${esc(L2("авто", "auto"))}">`
        : `<select data-mcap-ovr="${key}"><option value="">${esc(L2("авто", "auto"))}</option><option value="true" ${over[key] === true ? "selected" : ""}>${esc(L2("да", "yes"))}</option><option value="false" ${over[key] === false ? "selected" : ""}>${esc(L2("нет", "no"))}</option></select>`;
      return `<tr class="${differs ? "differs" : ""}"><th>${esc(t("mcap.k." + key))}</th><td>${splitVal(key, eff)}${splitSrc(eff)}</td><td>${splitVal(key, ref)}${splitSrc(ref)}</td><td>${control}</td></tr>`;
    }).join("");
    return `<section class="mcap-sec mcap-split"><h4>${ic("lines")}${esc(L2("Через этот API и по данным OpenRouter", "Through this API vs. OpenRouter"))}</h4>
      <p class="mcap-missing">${esc(L2("Маршрутизация и клиенты Proxy (вложения картинок, агент DFY) верят только первому столбцу: провайдеру, проверке и ручному указанию. Каталог OpenRouter / Models.dev — справка о модели вообще: у вашего провайдера её возможности могут отличаться (например, Claude без Vision).",
        "Routing and Proxy clients (image attachments, the DFY agent) trust only the first column: the provider, the probe and your manual setting. The OpenRouter / Models.dev catalog describes the model in general: your provider may differ (e.g. Claude without vision)."))}</p>
      <div class="mcap-split-wrap"><table class="mcap-split-tbl"><thead><tr><th></th><th>${esc(L2("Через этот API", "Through this API"))}</th><th>${esc(L2("По данным OpenRouter", "Per OpenRouter"))}</th><th>${esc(L2("Указать вручную", "Set manually"))}</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
  }

  async function saveOverride(id, key, raw) {
    const value = raw === "" ? null : raw === "true" ? true : raw === "false" ? false : Number(raw) || null;
    try {
      await api(`/api/admin/models/${encodeURIComponent(id)}/override`, { method: "POST", body: { [key]: value } });
      await load(true);
      open(id, true);
      toast(L2("Сохранено", "Saved"), "ok", 1200);
    } catch (e) { toast(e.message || String(e), "err"); }
  }

  function renderCard(drawer, card) {
    const f = card.fields || {};
    // Round 33 (r33d-shots): the title is the same readable name as the catalog row ("Claude Opus 4.6 Thinking"),
    // not the model_info family label ("Claude Opus 4.6–4.8"), which describes a range, not this model.
    let pretty = "";
    try {
      if (typeof proxyModelName === "function") {
        const row = (typeof modelsCache !== "undefined" && modelsCache.find((m) => m.id === card.id)) || { id: card.id };
        pretty = proxyModelName(row);
      }
    } catch (e) { pretty = ""; }
    const name = (pretty && pretty !== card.id && pretty) || (f.name && f.name.value) || card.model || card.id;
    const provider = typeof providerMeta === "function" ? (providerMeta(card.provider).title || card.provider) : card.provider;
    const tiles = TILES.map((tile) => {
      // Round 24: capability tiles follow this API; the catalogue value is only in the comparison below.
      // (unknown through this API → the best other evidence, labelled with its source: OpenRouter, name…)
      const item = (card.effective && card.effective[tile.k]) || f[tile.k];
      const state = !item ? "unknown" : item.value === true ? "yes" : item.value === false ? "no" : "unknown";
      const label = t("mcap.t." + tile.k);
      const sub = !item ? t("mcap.unknown") : `${state === "yes" ? t("mcap.yes") : state === "no" ? t("mcap.no") : t("mcap.unknown")} · ${t("mcap.src." + item.source)}`;
      return `<div class="mcap-tile ${state}" title="${esc(t("mcap.k." + tile.k))} — ${esc(sub)}${item ? esc(" · " + t("mcap.conf." + (item.confidence || "low"))) : ""}">${ic(tile.i)}<div style="min-width:0"><b>${esc(label)}</b><small>${esc(sub)}</small></div></div>`;
    }).join("");
    const matched = Object.entries(card.matched || {}).map(([src, m]) =>
      `<span class="chip" title="${esc(m.match === "exact" ? t("mcap.match.exact") : t("mcap.match.canonical"))}">${esc(t("mcap.src." + src))}: <span class="mono">${esc(m.id)}</span></span>`).join("");
    const sections = (card.groups || []).map((group) => {
      const rows = group.fields.filter((key) => !(group.id === "identity" && key === "name")).map((key) => {
        const item = f[key];
        const long = key === "description";
        return `<div class="mcap-row"><dt>${esc(I18N[LANG]["mcap.k." + key] ? t("mcap.k." + key) : key)}</dt><dd class="${long ? "long" : ""}">${fmtValue(key, item.value)}</dd>${srcHtml(item)}</div>`;
      }).join("");
      const missing = (KEY_FIELDS[group.id] || []).filter((key) => !f[key]).map((key) => t("mcap.k." + key));
      if (!rows && !missing.length) return "";
      return `<section class="mcap-sec"><h4>${ic(GROUP_ICON[group.id] || "dots")}${esc(t("mcap.g." + group.id))}</h4>${rows ? `<dl class="mcap-rows" style="margin:0">${rows}</dl>` : ""}${missing.length ? `<p class="mcap-missing">${esc(t("mcap.missing"))}: ${esc(missing.join(", "))}</p>` : ""}</section>`;
    }).join("");
    // Groups with no known field at all still say what is unknown.
    const shown = new Set((card.groups || []).map((g) => g.id));
    const empty = Object.keys(KEY_FIELDS).filter((g) => !shown.has(g) && g !== "weights").map((g) =>
      `<section class="mcap-sec"><h4>${ic(GROUP_ICON[g])}${esc(t("mcap.g." + g))}</h4><p class="mcap-missing">${esc(t("mcap.missing"))}: ${esc(KEY_FIELDS[g].map((k) => t("mcap.k." + k)).join(", "))}</p></section>`).join("");
    const raws = Object.entries(card.raw || {}).filter(([, v]) => v && (typeof v !== "object" || Object.keys(v).length)).map(([src, value]) => {
      let size = 0;
      try { size = JSON.stringify(value).length; } catch (e) { size = 0; }
      return `<details class="mcap-raw" data-mcap-raw="${esc(src)}"><summary>${esc(I18N[LANG]["mcap.raw." + src] ? t("mcap.raw." + src) : src)}<span class="sub">${esc(fmtNum(size))} ${esc(t("mcap.raw.size"))}</span></summary></details>`;
    }).join("");
    const legend = ["verified", "high", "medium", "low"].map((c) => `<span class="mcap-src" data-conf="${c}">${esc(t("mcap.conf." + c))}</span>`).join("");
    const catalog = card.catalog && !card.catalog.own ? `<p class="mcap-missing">${esc(catalogLine())}</p>` : "";
    drawer.innerHTML = `
      <div class="mcap-head">
        <h3 id="mcap-title">${esc(name)}</h3>
        <button type="button" class="mcap-icon-btn mcap-close" data-mcap-close title="${esc(t("mcap.close"))}" aria-label="${esc(t("mcap.close"))}">${ic("x")}</button>
        <div class="mcap-idrow"><code title="${esc(card.id)}">${esc(card.id)}</code><button type="button" class="mcap-icon-btn" data-mcap-copy title="${esc(t("mcap.copy"))}" aria-label="${esc(t("mcap.copy"))}">${ic("copy")}</button></div>
        <div class="mcap-meta"><span class="chip">${esc(provider)}</span><span class="chip ${card.enabled ? "ok" : "dim"}">${esc(card.enabled ? t("mcap.on") : t("mcap.off"))}</span>${f.kind ? `<span class="chip tag">${esc(t("mcap.kind." + f.kind.value))}</span>` : ""}${f.context ? `<span class="chip">${ic("lines")}${esc(typeof compactTokens === "function" ? compactTokens(f.context.value) : f.context.value)}</span>` : ""}</div>
      </div>
      <div class="mcap-body">
        <div class="mcap-tiles">${tiles}</div>
        ${splitBlock(card)}
        ${window.MemuReasoningCard ? window.MemuReasoningCard.placeholder(card) : ""}
        ${probeBlock(card)}
        ${matched ? `<div class="mcap-matched"><span>${esc(t("mcap.matched"))}:</span>${matched}</div>` : ""}
        ${catalog}
        ${sections}${empty}
        ${raws ? `<section class="mcap-sec"><h4>${ic("braces")}${esc(t("mcap.raw"))}</h4>${raws}</section>` : ""}
        ${window.MemuWebModels ? window.MemuWebModels.cardBlock(card) : ""}
        <div class="mcap-legend">${legend}</div>
      </div>`;
  }

  function onRawToggle(event) {
    const box = event.target;
    if (!box.matches || !box.matches("details[data-mcap-raw]") || !box.open || box.dataset.filled) return;
    const value = ((S.card || {}).raw || {})[box.dataset.mcapRaw];
    box.dataset.filled = "1";
    box.insertAdjacentHTML("beforeend", `<div class="mcap-raw-tools"><button type="button" class="btn small" data-mcap-copyjson="${esc(box.dataset.mcapRaw)}">${esc(t("mcap.copyjson"))}</button></div><pre>${jsonHtml(value)}</pre>`);
  }

  function onDrawerChange(event) {
    const box = event.target.closest && event.target.closest("[data-mcap-ovr]");
    if (box && S.card) saveOverride(S.card.id, box.dataset.mcapOvr, box.value.trim());
  }

  async function onDrawerClick(event) {
    if (event.target.closest("[data-mcap-close]")) return close();
    if (event.target.closest("[data-mcap-copy]") && S.card) return copyText(S.card.id, t("mcap.copied"));
    const copyJson = event.target.closest("[data-mcap-copyjson]");
    if (copyJson && S.card) return copyText(JSON.stringify(((S.card.raw || {})[copyJson.dataset.mcapCopyjson]), null, 2));
    if (event.target.closest("[data-mcap-probe]") && S.card) return confirmProbe(S.card.id);
    if (event.target.closest("[data-mcap-forget]") && S.card) {
      try {
        await api(`/api/admin/models/${encodeURIComponent(S.card.id)}/probe`, { method: "POST", body: { action: "forget" } });
        S.lastProbe = null;
        await load(true);
        open(S.card.id, true);
      } catch (e) { toast(e.message || String(e), "err"); }
    }
  }

  function confirmProbe(id) {
    openModal(`<h3>${esc(t("mcap.probe.confirm"))}</h3><p class="muted mono" style="word-break:break-all">${esc(id)}</p><p class="muted">${esc(t("mcap.probe.cost"))}</p>
      <div class="row-end"><button class="btn" id="m-no">${esc(t("acc.cancel"))}</button><button class="btn primary" id="m-yes">${esc(t("mcap.probe.btn"))}</button></div>`);
    $("#m-no").onclick = closeModal;
    $("#m-yes").onclick = () => { closeModal(); runProbe(id); };
  }

  async function runProbe(id) {
    S.probing = true;
    if (S.card && S.card.id === id) renderCard(document.getElementById("mcap-drawer"), S.card);
    try {
      const result = await api(`/api/admin/models/${encodeURIComponent(id)}/probe`, { method: "POST", body: { confirm: true } });
      S.lastProbe = { id, result };
      toast(result.vision === true ? t("mcap.probe.yes") : result.vision === false ? t("mcap.probe.no") : t("mcap.probe.unknown"),
        result.vision === true ? "ok" : "warn");
    } catch (e) {
      toast(e.message || String(e), "err");
    } finally {
      S.probing = false;
    }
    await load(true);
    if (S.openId === id) open(id, true);
  }

  document.addEventListener("keydown", (event) => {
    if (!S.openId || event.key !== "Escape") return;
    if (document.getElementById("modal-back")?.classList.contains("visible")) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  }, true);

  function relabel() {
    renderFilters();
    const drawer = document.getElementById("mcap-drawer");
    if (drawer && S.card) renderCard(drawer, S.card);
  }

  window.MemuModelCaps = { load, badges, passes, renderFilters, open, close, refreshCatalog, relabel, state: S };
})();
