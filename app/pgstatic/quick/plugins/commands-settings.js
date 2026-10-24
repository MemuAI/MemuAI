/* MemuAI — settings of the Quick Ask «Команды» plugin (Round 22, r22-commands).

   window.MemuCommandsSettings.mount(el, ctx?) renders the whole editor into `el`:
   assistant switches, custom commands (trigger phrases, global hotkey, steps, conditions, window
   layout capture), saved layouts, voice (non-realtime STT/TTS), timers & Pomodoro, services,
   per-action switches and tiers, safety (countdown, rate limits, advanced PowerShell) and the audit log.
   ctx (optional): { en: bool, toast(text, kind) }. Talks only to /api/assistant/* and /api/voice/catalog.
   Works both in the main window (Settings → Quick Ask, through MemuQuick.settingsSection) and in any
   page that loads it. */
(function () {
  "use strict";
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function call(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
    return data;
  }

  const TIER = { safe: ["безопасно", "safe"], confirm: ["с подтверждением", "confirm"], dangerous: ["опасно", "dangerous"] };
  const WEEK = [["Пн", "Mo"], ["Вт", "Tu"], ["Ср", "We"], ["Чт", "Th"], ["Пт", "Fr"], ["Сб", "Sa"], ["Вс", "Su"]];
  const STEP_TYPES = ["action", "wait", "say", "notify", "ask_ai", "layout"];
  const PARAM_RU = { name: "название", level: "уровень, %", delta: "изменение, %", seconds: "секунды", label: "метка", text: "текст",
    url: "ссылка", path: "путь", folder: "папка", page: "страница", query: "запрос", engine: "поисковик", service: "сервис",
    city: "город", when: "когда", amount: "сумма", from: "из", to: "в / куда", key: "клавиша", mute: "без звука", mode: "режим",
    on: "включить", copy: "в буфер", keys: "сочетание", what: "что", pid: "PID", action: "действие", target: "окно (пусто — активное)",
    monitor: "монитор", width: "ширина, %", height: "высота, %", layout: "раскладка", time: "время", date: "дата", work: "работа, мин",
    short_break: "перерыв, мин", long_break: "длинный перерыв, мин", cycles: "циклы", at: "время", in_minutes: "через, мин",
    channel: "канал", delay_minutes: "через сколько минут", id: "id", prompt: "описание", model: "модель", aspect: "формат", all: "все", resume: "продолжить", command: "команда" };
  // Enum values as people read them (the stored value stays the catalog id). Unknown values fall back
  // to the id with "_" → " " so new catalog entries never break the editor.
  const ENUM_RU = { downloads: "Загрузки", documents: "Документы", desktop: "Рабочий стол", pictures: "Изображения", music: "Музыка",
    videos: "Видео", home: "Папка пользователя", appdata: "AppData", temp: "Временные файлы", screenshots: "Снимки экрана", memuai: "MemuAI",
    now: "сейчас", today: "сегодня", tomorrow: "завтра", week: "на неделю", play_pause: "пауза / воспроизведение", next: "следующий",
    prev: "предыдущий", stop: "стоп", dark: "тёмная", light: "светлая", toggle: "переключить", screen: "весь экран", region: "область",
    window: "окно", all: "всё", cpu: "процессор", ram: "память", battery: "батарея", disk: "диск", network: "сеть", uptime: "время работы",
    maximize: "развернуть", minimize: "свернуть", restore: "восстановить", snap_left: "к левому краю", snap_right: "к правому краю",
    snap_top: "вверх", snap_bottom: "вниз", center: "по центру", close: "закрыть", topmost: "поверх всех", untopmost: "не поверх",
    side_by_side: "рядом", grid: "сеткой", stack: "стопкой", cascade: "каскадом", new: "новый", view: "обзор", start: "старт",
    reset: "сброс", lap: "круг", pause: "пауза", resume: "продолжить", skip: "пропустить", stats: "статистика",
    local: "этот компьютер", telegram: "Telegram", both: "компьютер и Telegram", auto: "авто",
    main: "главное окно", new_chat: "новый чат", settings: "настройки", quick_settings: "настройки быстрого доступа", voice: "голос",
    models: "модели", appearance: "оформление", panels: "панели", modules: "модули", integrations: "интеграции", prompts: "промпты",
    remote: "удалённый доступ", work: "работа", docs: "документация", about: "о программе", apps: "приложения",
    display: "дисплей", sound: "звук", wifi: "Wi-Fi", update: "обновления", default_apps: "приложения по умолчанию",
    startup: "автозагрузка", power: "питание", mouse: "мышь", keyboard: "клавиатура", personalization: "персонализация", background: "фон",
    colors: "цвета", themes: "темы", lockscreen: "экран блокировки", taskbar: "панель задач", notifications: "уведомления",
    focus: "фокусировка", privacy: "конфиденциальность", camera: "камера", microphone: "микрофон", printers: "принтеры",
    datetime: "дата и время", language: "язык", storage: "память устройства", nightlight: "ночной свет", airplane: "режим «в самолёте»",
    accounts: "учётные записи", multitasking: "многозадачность", gaming: "игры", clipboard: "буфер обмена", projection: "проецирование",
    accessibility: "спец. возможности", recovery: "восстановление", troubleshoot: "устранение неполадок",
    google: "Google", yandex: "Яндекс", duckduckgo: "DuckDuckGo", bing: "Bing", youtube: "YouTube", youtube_music: "YouTube Music",
    yandex_music: "Яндекс Музыка", spotify: "Spotify", vk: "VK", soundcloud: "SoundCloud", yandex_eda: "Яндекс Еда",
    delivery_club: "Delivery Club", samokat: "Самокат", kuper: "Купер", ubereats: "Uber Eats", doordash: "DoorDash", deliveroo: "Deliveroo",
    wolt: "Wolt", ozon: "Ozon", wildberries: "Wildberries", yandex_market: "Яндекс Маркет", amazon: "Amazon", aliexpress: "AliExpress",
    avito: "Авито", ebay: "eBay", yandex_go: "Яндекс Go", uber: "Uber", bolt: "Bolt", yandex_maps: "Яндекс Карты", google_maps: "Google Maps",
    "2gis": "2ГИС" };
  const ENUM_EN = { play_pause: "play / pause", snap_left: "snap left", snap_right: "snap right", snap_top: "snap top", snap_bottom: "snap bottom",
    untopmost: "not on top", topmost: "always on top", local: "this PC", both: "PC and Telegram", ram: "memory", cpu: "CPU",
    yandex_eda: "Yandex Eda", yandex_music: "Yandex Music", yandex_market: "Yandex Market", yandex_go: "Yandex Go", yandex_maps: "Yandex Maps",
    google_maps: "Google Maps", youtube: "YouTube", youtube_music: "YouTube Music", "2gis": "2GIS", wifi: "Wi-Fi", nightlight: "night light",
    default_apps: "default apps", new_chat: "new chat", quick_settings: "Quick Ask settings", side_by_side: "side by side" };

  function mount(el, ctx = {}) {
    const en = ctx.en !== undefined ? !!ctx.en : (document.documentElement.lang === "en");
    const t = (ru, enText) => (en ? enText : ru);
    const toast = (text, kind) => (typeof ctx.toast === "function" ? ctx.toast(text, kind) : (window.MemuQuick ? window.MemuQuick.toast(text, kind === "err" ? "err" : "") : console.log(text)));
    const L = en ? "en" : "ru";
    const S = { cfg: null, draft: null, editing: -1, voiceCatalog: null, voiceStatus: null, imageModels: [], audit: null, open: { actions: false, audit: false, more: false, models: false }, saveTimer: 0, dlTimer: 0, pendingPatch: {} };

    el.classList.add("mcs-root");
    el.innerHTML = `<div class="mcs-loading">${t("Загрузка…", "Loading…")}</div>`;

    async function load() {
      try {
        S.cfg = await call("/api/assistant/config?lang=" + L);
      } catch (e) {
        el.innerHTML = `<div class="mcs-empty">${t("Команды ассистента недоступны: ", "Assistant commands are unavailable: ")}${esc(e.message)}</div>`;
        return;
      }
      try { S.voiceCatalog = await call("/api/voice/catalog"); } catch (e) { S.voiceCatalog = null; }
      if (ctx.part === "voice" || !ctx.part) await loadVoiceStatus(false);
      try {
        const st = await call("/api/pg/state");
        S.imageModels = st.image_models || [];
        // Round 33 (r33-quick): text models for the AI command router
        S.chatModels = (st.models || []).map((m) => (typeof m === "string" ? m : m && m.id)).filter(Boolean);
      } catch (e) { S.imageModels = []; S.chatModels = []; }
      render();
    }

    /* ---------------------------------------------------------------- helpers */
    const set = () => S.cfg.settings;
    function savePatch(patch) {
      Object.assign(S.pendingPatch, patch);
      clearTimeout(S.saveTimer);
      S.saveTimer = setTimeout(flushSave, 300);
    }
    // Round 24: сохранить немедленно (перед «Проверить» — раньше проверка шла со старыми настройками).
    async function flushSave() {
      clearTimeout(S.saveTimer);
      const body = S.pendingPatch; S.pendingPatch = {};
      if (!Object.keys(body).length) return;
      try {
        const data = await call("/api/assistant/settings", { settings: body }); S.cfg.settings = data.settings;
        if (body.voice && (ctx.part === "voice" || !ctx.part)) { await loadVoiceStatus(false); patchVoiceStatus(); }
      } catch (e) { toast(e.message, "err"); }
    }

    /* ---------------------------------------------------------------- Round 24: голос — источник и готовность */
    async function loadVoiceStatus(withCatalog) {
      try { S.voiceStatus = await call("/api/assistant/voice/status?lang=" + L); } catch (e) { S.voiceStatus = null; }
      if (withCatalog) { try { S.voiceCatalog = await call("/api/voice/catalog"); } catch (e) {} }
    }
    function patchVoiceStatus() {
      const box = el.querySelector("[data-mcs-voice-status]");
      if (box) box.outerHTML = voiceStatusHtml();
      const list = el.querySelector("[data-mcs-voice-models]");
      if (list) list.outerHTML = voiceModelsHtml();
    }
    function voiceStatusRow(kind, st) {
      if (!st) return "";
      const head = kind === "stt" ? t("Распознавание", "Recognition") : t("Озвучка", "Speech");
      const src = st.source === "quick" ? t("свой выбор", "own choice") : t("как в «Голос и Live»", "as in Voice & Live");
      let fix = "";
      if (!st.ready && st.fix === "download" && st.fix_id) {
        fix = st.downloading
          ? `<span class="mcs-vs-prog">${t("Скачивается", "Downloading")} ${Math.round(+st.progress || 0)}%</span>`
          : `<button type="button" class="mcs-btn mcs-primary" data-act="vm-download" data-id="${esc(st.fix_id)}" data-kind="${esc(st.fix_kind || kind)}">${t("Скачать", "Download")}</button>`;
      } else if (!st.ready && st.fix === "open_voice") {
        fix = `<button type="button" class="mcs-btn" data-act="open-voice">${t("Открыть «Голос и Live»", "Open Voice & Live")}</button>`;
      }
      const test = kind === "tts"
        ? `<button type="button" class="mcs-btn" data-act="voice-test" ${st.ready ? "" : "disabled"}>${t("Проверить", "Test")}</button>`
        : `<button type="button" class="mcs-btn" data-act="stt-test" ${st.ready ? "" : "disabled"}>${t("Проверить", "Test")}</button>`;
      return `<div class="mcs-vs-row ${st.ready ? "ok" : "bad"}" data-vs="${kind}">
        <span class="mcs-vs-dot" aria-hidden="true"></span>
        <span class="mcs-vs-main"><span class="mcs-vs-head"><b>${esc(head)}</b><em class="mcs-vs-src">${esc(src)}</em></span>
          <small>${esc(st.label || "")}</small>
          <small class="mcs-vs-state">${st.ready ? t("Готово", "Ready") : esc(st.problem || t("Не настроено", "Not set up"))}</small></span>
        <span class="mcs-tools">${fix}${test}</span></div>`;
    }
    function voiceStatusHtml() {
      const vs = S.voiceStatus;
      if (!vs) return `<div class="mcs-vs" data-mcs-voice-status><p class="mcs-hint">${t("Статус голоса недоступен.", "Voice status is unavailable.")}</p></div>`;
      return `<div class="mcs-vs" data-mcs-voice-status role="status" aria-live="polite">${voiceStatusRow("stt", vs.stt)}${voiceStatusRow("tts", vs.tts)}
        <p class="mcs-hint">${esc(vs.proxy_note || "")}</p></div>`;
    }
    function voiceModelsHtml() {
      const cat = S.voiceCatalog || {};
      const v = (set() && set().voice) || {};
      const rows = (list, kind) => (list || []).map((m) => {
        const running = m.download_status === "running";
        const using = kind === "stt" ? (v.stt_provider === "local" && v.stt_local_model === m.id) : (v.tts_provider === "downloaded" && v.tts_local_model === m.id);
        const state = m.downloaded ? t("скачана", "downloaded") : running ? t("скачивается ", "downloading ") + Math.round(+m.progress || 0) + "%" : m.download_status === "error" ? t("ошибка: ", "error: ") + (m.download_error || "") : t("не скачана", "not downloaded");
        const meta = [m.size || (m.size_mb ? m.size_mb + " MB" : ""), (function (l) { return Array.isArray(l) ? l.slice(0, 4).join(", ") : String(l || ""); })((!en && m.languages_ru) || m.languages)].filter(Boolean).join(" · ");
        return `<div class="mcs-vm ${m.downloaded ? "ok" : ""}${using ? " using" : ""}">
          <span class="mcs-vm-main"><span class="mcs-vs-head"><b>${esc(m.name || m.id)}</b>${using ? `<em class="mcs-vs-src">${t("используется", "in use")}</em>` : ""}</span>
            <small>${esc(state)}${meta ? " · " + esc(meta) : ""}</small>${running ? `<i class="mcs-vm-bar"><i style="width:${Math.max(2, Math.min(100, +m.progress || 0))}%"></i></i>` : ""}</span>
          <span class="mcs-tools">${m.downloaded
            ? `${using ? "" : `<button type="button" class="mcs-btn" data-act="vm-use" data-id="${esc(m.id)}" data-kind="${kind}">${t("Использовать", "Use")}</button>`}<button type="button" class="mcs-btn mcs-danger" data-act="vm-delete" data-id="${esc(m.id)}" data-kind="${kind}" aria-label="${t("Удалить", "Delete")}">✕</button>`
            : running ? `<button type="button" class="mcs-btn" data-act="vm-cancel" data-id="${esc(m.id)}" data-kind="${kind}">${t("Отмена", "Cancel")}</button>`
            : `<button type="button" class="mcs-btn" data-act="vm-download" data-id="${esc(m.id)}" data-kind="${kind}">${t("Скачать", "Download")}</button>`}</span></div>`;
      }).join("");
      return `<details class="mcs-more mcs-vm-list" data-mcs-voice-models ${S.open.models ? "open" : ""}>
        <summary><span>${t("Локальные модели", "Local models")}</span><small>${t("распознавание (faster-whisper) и голоса (Piper) — работают без интернета; те же, что в «Голос и Live»", "recognition (faster-whisper) and voices (Piper) — offline; the same as in Voice & Live")}</small></summary>
        <h4>${t("Распознавание речи", "Speech recognition")}</h4>${rows(cat.stt_models, "stt") || `<p class="mcs-hint">${t("Список недоступен.", "Unavailable.")}</p>`}
        <h4>${t("Голоса", "Voices")}</h4>${rows(cat.tts_models, "tts") || `<p class="mcs-hint">${t("Список недоступен.", "Unavailable.")}</p>`}
      </details>`;
    }
    function pollDownloads() {
      clearTimeout(S.dlTimer);
      const busy = () => [...((S.voiceCatalog || {}).stt_models || []), ...((S.voiceCatalog || {}).tts_models || [])].some((m) => m.download_status === "running");
      S.dlTimer = setTimeout(async () => {
        if (!el.isConnected) return;
        await loadVoiceStatus(true); patchVoiceStatus();
        if (busy()) pollDownloads();
      }, 1500);
    }
    const sw = (key, checked, label, hint = "", sub = "") => `<label class="mcs-row"><span class="mcs-label">${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ""}</span>
      <span class="mcs-switch"><input type="checkbox" data-set="${esc(key)}" ${sub ? `data-sub="${esc(sub)}"` : ""} ${checked ? "checked" : ""}><i></i></span></label>`;
    const select = (key, value, options, label, sub = "") => `<label class="mcs-row"><span class="mcs-label">${esc(label)}</span>
      <select data-set="${esc(key)}" ${sub ? `data-sub="${esc(sub)}"` : ""}>${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const num = (key, value, min, max, label, sub = "", suffix = "") => `<label class="mcs-row"><span class="mcs-label">${esc(label)}</span>
      <span class="mcs-num"><input type="number" data-set="${esc(key)}" ${sub ? `data-sub="${esc(sub)}"` : ""} min="${min}" max="${max}" value="${esc(value)}">${suffix ? `<em>${esc(suffix)}</em>` : ""}</span></label>`;
    const catalogById = () => Object.fromEntries(S.cfg.catalog.map((a) => [a.id, a]));
    /* Round 33 (r33-quick): «Команды через ИИ» — the router mode, its model (empty = the Quick Ask model) and the wait */
    const hinted = (key, value, options, label, hint) => `<label class="mcs-row"><span class="mcs-label">${esc(label)}${hint ? `<small>${esc(hint)}</small>` : ""}</span>
      <select data-set="${esc(key)}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    function routerRows(s) {
      const mode = s.ai_router || "auto";
      const models = (S.chatModels || []).slice();
      if (s.router_model && !models.includes(s.router_model)) models.unshift(s.router_model);
      return hinted("ai_router", mode, [["auto", t("Когда фраза похожа на команду", "When a phrase looks like a command")],
          ["always", t("Для всех коротких фраз", "For every short phrase")], ["off", t("Выключено", "Off")]],
          t("Понимать команды с помощью ИИ", "Understand commands with AI"),
          t("если фраза не распознана мгновенно, быстрая модель за один короткий запрос выбирает действие («закрой инси» → INCY); не команда — обычный ответ", "when a phrase is not recognised instantly, a fast model picks the action in one short request (“close insy” → INCY); not a command — a normal answer"))
        + (mode === "off" ? "" : hinted("router_model", s.router_model || "", [["", t("Модель быстрого доступа", "The Quick Ask model")], ...models.map((m) => [m, m])],
          t("Модель для команд", "Model for commands"), t("лучше быстрая модель без размышлений (flash, mini, haiku)", "a fast non-reasoning model works best (flash, mini, haiku)"))
          + num("router_timeout", s.router_timeout || 4, 1, 15, t("Ждать ответа ИИ не дольше", "Wait for the AI at most"), "", t("с", "s")));
    }

    /* Round 25: render() replaces the whole markup, so a change that re-renders (the voice provider shows other
       rows) lost the keyboard focus and threw the scroll back to the top. The focused data-set control and the
       scroll offsets of the scrolling ancestors are remembered and restored. A pretty-select moves focus to its
       menu (in <body>) and closes it before «change», so the control that last had focus or changed counts too. */
    const controlKey = (node) => (node && node.dataset && node.dataset.set ? { set: node.dataset.set, sub: node.dataset.sub || "" } : null);
    const keyOf = (node) => {
      if (!node || !el.contains(node)) return null;
      const own = controlKey(node);
      if (own) return own;
      const box = node.closest(".mcs-row, label");
      return box && el.contains(box) ? controlKey(box.querySelector("[data-set]")) : null;
    };
    const remember = (node) => { const key = keyOf(node); if (key) S.lastFocus = { ...key, at: performance.now() }; };
    el.addEventListener("focusin", (e) => remember(e.target));
    function snapshotView() {
      const active = document.activeElement;
      let key = keyOf(active);
      const lost = !active || active === document.body || !active.isConnected || !!active.closest(".pretty-select-menu, [role=listbox]");
      if (!key && lost && S.lastFocus && performance.now() - S.lastFocus.at < 1500) key = S.lastFocus;
      const scrolls = [];
      for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
        if (node.scrollTop) scrolls.push([node, node.scrollTop]);
      }
      const page = document.scrollingElement;
      if (page && page.scrollTop) scrolls.push([page, page.scrollTop]);
      return { key, scrolls };
    }
    function restoreView(view) {
      view.scrolls.forEach(([node, top]) => { node.scrollTop = top; });
      if (!view.key) return;
      const control = Array.from(el.querySelectorAll("[data-set]"))
        .find((node) => node.dataset.set === view.key.set && (node.dataset.sub || "") === view.key.sub);
      if (!control) return;
      // An enhanced <select> is hidden behind its own button: focus what the user sees.
      const shown = control.getClientRects().length ? control
        : control.parentElement && control.parentElement.querySelector("button, input:not([type=hidden])");
      if (shown) try { shown.focus({ preventScroll: true }); } catch (e) {}
    }

    /* ---------------------------------------------------------------- render */
    function render() {
      const s = set();
      const v = s.voice || {};
      const p = s.pomodoro || {};
      const stt = (S.voiceCatalog && S.voiceCatalog.stt_models) || [];
      const tts = (S.voiceCatalog && S.voiceCatalog.tts_models) || [];
      const winVoices = (S.voiceCatalog && S.voiceCatalog.windows_voices) || [];
      // Round 23: the editor is split into parts so Settings → Быстрый доступ can show «Команды» and «Голос» as two
      // calm sub-pages (ctx.part = "commands" | "voice"); without ctx.part everything renders as before.
      const SEC = {
        main: `<section class="mcs-sec" data-mcs="main">
          <h3>${t("Команды ассистента", "Assistant commands")}</h3>
          <p class="mcs-desc">${t("Пишите или говорите обычными фразами: «открой телеграм», «громкость 30», «таймер 10 минут», «напомни в телеграме в 15:00…». Частые команды выполняются мгновенно без модели, остальное понимает модель быстрого доступа.",
            "Type or say plain phrases: “open Telegram”, “volume 30”, “timer 10 minutes”, “remind me in Telegram at 3 pm…”. Common commands run instantly without a model; the Quick Ask model handles the rest.")}</p>
          ${S.cfg.platform !== "win32" ? `<div class="mcs-note">${t("Системные действия работают только в Windows-приложении MemuAI; здесь доступны таймеры, напоминания, ссылки и свои команды.", "System actions work only in the MemuAI Windows app; timers, reminders, links and custom commands work here.")}</div>` : ""}
          ${sw("enabled", s.enabled, t("Команды включены", "Commands enabled"))}
          ${sw("local_match", s.local_match, t("Мгновенные команды без модели", "Instant commands without a model"), t("локальное распознавание частых фраз на русском и английском", "local recognition of common Russian and English phrases"))}
          ${sw("model_tools", s.model_tools, t("Модель может выполнять действия", "The model may perform actions"), t("действия — инструмент модели; опасные всё равно ждут вашего подтверждения", "actions are a model tool; dangerous ones still wait for your confirmation"))}
          ${routerRows(s)}
        </section>`,
        custom: `<section class="mcs-sec" data-mcs="custom">
          <div class="mcs-sec-head"><h3>${t("Свои команды", "Custom commands")}</h3>
            <div class="mcs-tools">
              <button type="button" class="mcs-btn mcs-primary" data-act="new">${t("Новая команда", "New command")}</button>
              <button type="button" class="mcs-btn" data-act="template">${t("Шаблон «Доброе утро»", "“Good morning” template")}</button>
              <button type="button" class="mcs-btn" data-act="import">${t("Импорт", "Import")}</button>
              <button type="button" class="mcs-btn" data-act="export">${t("Экспорт", "Export")}</button>
              <input type="file" accept="application/json,.json" data-act="import-file" hidden>
            </div></div>
          <p class="mcs-desc">${t("Фразы-триггеры (несколько, на любом языке, с опечатками) и/или глобальная горячая клавиша запускают цепочку шагов: любые действия, паузы, условия, «спросить ИИ» и раскладку окон по мониторам.",
            "Trigger phrases (several, any language, typo-tolerant) and/or a global hotkey run a chain of steps: any action, waits, conditions, “ask AI” and a window layout across monitors.")}</p>
          ${S.draft ? editorHtml() : commandsListHtml()}
        </section>`,
        layouts: `<section class="mcs-sec" data-mcs="layouts">
          <div class="mcs-sec-head"><h3>${t("Раскладки окон", "Window layouts")}</h3>
            <div class="mcs-tools"><button type="button" class="mcs-btn" data-act="capture-layout">${t("Запомнить текущие окна", "Save the current windows")}</button></div></div>
          <p class="mcs-desc">${t("Приложение, монитор, положение, размер и состояние каждого окна (учитываются разные DPI и мониторы левее/выше основного). Восстановление запускает недостающие программы и ждёт их окна. Фраза: «восстанови раскладку работа».",
            "App, monitor, position, size and state of every window (mixed DPI and monitors left/above the primary one included). Restoring launches missing apps and waits for their windows. Phrase: “restore layout work”.")}</p>
          ${layoutsHtml()}
        </section>`,
        voice: `<section class="mcs-sec" data-mcs="voice">
          <h3>${t("Голос", "Voice")}</h3>
          <p class="mcs-desc">${t("Как MemuAI Live, но без realtime-моделей: распознавание (STT) и озвучка (TTS) обычными моделями. Пустой выбор — как в Настройки → Голос и Live. Удерживайте кнопку микрофона или Ctrl+Space, чтобы говорить; Esc прерывает.",
            "Like MemuAI Live but without realtime models: regular speech-to-text and text-to-speech models. Empty = as in Settings → Voice & Live. Hold the mic button or Ctrl+Space to talk; Esc interrupts.")}</p>
          ${sw("enabled", v.enabled, t("Голос в быстром доступе", "Voice in Quick Ask"), "", "voice")}
          ${sw("auto_speak", v.auto_speak, t("Озвучивать ответы", "Speak answers"), "", "voice")}
          ${sw("send_on_release", v.send_on_release, t("Отправлять сразу после распознавания", "Send right after recognition"), "", "voice")}
          ${sw("hold_to_talk", v.hold_to_talk, t("Удержание кнопки — режим рации", "Hold the button for push-to-talk"), "", "voice")}
          ${select("stt_provider", v.stt_provider, [["", t("Как в настройках «Голос и Live»", "As in Voice & Live settings")], ["local", t("Локальная модель (faster-whisper)", "Local model (faster-whisper)")], ["api", t("API (OpenAI-совместимый)", "API (OpenAI-compatible)")]], t("Распознавание речи", "Speech recognition"), "voice")}
          ${v.stt_provider === "local" ? select("stt_local_model", v.stt_local_model, [["", t("Как в настройках «Голос и Live»", "As in Voice & Live settings")], ...stt.map((m) => [m.id, (m.name || m.id) + (m.downloaded ? "" : t(" — не скачана", " — not downloaded"))])], t("Модель STT", "STT model"), "voice") : ""}
          ${v.stt_provider === "api" ? `<label class="mcs-row"><span class="mcs-label">${t("Модель STT (API)", "STT model (API)")}</span><input type="text" data-set="stt_api_model" data-sub="voice" value="${esc(v.stt_api_model)}" placeholder="gpt-4o-mini-transcribe"></label>` : ""}
          ${select("language", v.language, [["", t("Как в настройках «Голос и Live»", "As in Voice & Live settings")], ["auto", t("Автоопределение", "Auto-detect")], ["ru", "Русский"], ["en", "English"]], t("Язык речи", "Speech language"), "voice")}
          ${sw("stt_postprocess", v.stt_postprocess, t("Постобработка текста моделью", "Post-process the transcript with a model"), t("медленнее; по умолчанию выключено для скорости", "slower; off by default for speed"), "voice")}
          ${select("tts_provider", v.tts_provider, [["", t("Как в настройках «Голос и Live»", "As in Voice & Live settings")], ["windows", t("Голоса Windows", "Windows voices")], ["downloaded", t("Скачанные голоса (Piper и др.)", "Downloaded voices (Piper etc.)")], ["api", "API"], ["local", t("Локальный сервер", "Local server")]], t("Озвучка", "Text to speech"), "voice")}
          ${v.tts_provider === "windows" ? select("tts_voice", v.tts_voice, [["", t("Автоматически по языку", "Automatic by language")], ...winVoices.map((x) => [x.name, x.name + (x.culture ? " (" + x.culture + ")" : "")])], t("Голос", "Voice"), "voice") : ""}
          ${v.tts_provider === "downloaded" ? select("tts_local_model", v.tts_local_model, [["", t("Как в настройках «Голос и Live»", "As in Voice & Live settings")], ...tts.filter((m) => m.downloaded).map((m) => [m.id, m.name || m.id])], t("Голос", "Voice"), "voice") : ""}
          ${v.tts_provider === "api" ? apiVoiceRow(v) : ""}
          <div class="mcs-row"><span class="mcs-label">${t("Проверка", "Test")}</span><button type="button" class="mcs-btn" data-act="voice-test">${t("Озвучить пример", "Speak a sample")}</button></div>
        </section>`,
        timers: `<section class="mcs-sec" data-mcs="timers">
          <h3>${t("Таймеры, будильники, Pomodoro", "Timers, alarms, Pomodoro")}</h3>
          ${select("timer_sound", s.timer_sound, [["chime", t("Колокольчики", "Chime")], ["bell", t("Звонок", "Bell")], ["soft", t("Мягкий", "Soft")], ["digital", t("Цифровой", "Digital")], ["off", t("Без звука", "Silent")]], t("Звук", "Sound"))}
          <label class="mcs-row"><span class="mcs-label">${t("Громкость сигнала", "Ring volume")}</span><span class="mcs-range"><input type="range" min="0" max="100" data-set="timer_volume" value="${esc(s.timer_volume)}"><em>${esc(s.timer_volume)}%</em></span></label>
          ${sw("timer_popup", s.timer_popup, t("Показывать быстрый доступ, когда сработал таймер", "Show Quick Ask when a timer rings"))}
          ${sw("timer_toast", s.timer_toast, t("Уведомление Windows", "Windows notification"))}
          ${num("work", p.work, 1, 180, t("Работа", "Focus"), "pomodoro", t("мин", "min"))}
          ${num("short_break", p.short_break, 1, 60, t("Короткий перерыв", "Short break"), "pomodoro", t("мин", "min"))}
          ${num("long_break", p.long_break, 1, 120, t("Длинный перерыв", "Long break"), "pomodoro", t("мин", "min"))}
          ${num("cycles", p.cycles, 1, 12, t("Циклов до длинного перерыва", "Cycles before a long break"), "pomodoro")}
          ${sw("auto_breaks", p.auto_breaks, t("Перерыв начинается сам", "Breaks start automatically"), "", "pomodoro")}
          ${sw("auto_work", p.auto_work, t("Работа начинается сама после перерыва", "Focus starts automatically after a break"), "", "pomodoro")}
          ${select("reminder_channel", s.reminder_channel, [["local", t("Этот компьютер", "This computer")], ["telegram", "Telegram"], ["both", t("Компьютер и Telegram", "Computer and Telegram")]], t("Напоминания по умолчанию", "Default reminder channel"))}
          <div class="mcs-note ${S.cfg.telegram && S.cfg.telegram.ok ? "ok" : ""}">${S.cfg.telegram && S.cfg.telegram.ok
            ? t("Telegram-бот подключён: «напомни в телеграме в 15:00 …» отправит планировщик MemuAI Proxy (задача видна в «Планировании»).", "The Telegram bot is paired: “remind me in Telegram at 3 pm …” is sent by the MemuAI Proxy scheduler (visible in Planning).")
            : t("Telegram-бот не подключён — напоминания в Telegram недоступны (Настройки → Telegram).", "The Telegram bot is not paired — Telegram reminders are unavailable (Settings → Telegram).")}</div>
        </section>`,
        services: `<section class="mcs-sec" data-mcs="services">
          <h3>${t("Сервисы", "Services")}</h3>
          <p class="mcs-desc">${t("Куда ведут «найди», «включи песню», «закажи роллы», «вызови такси», «маршрут до…». MemuAI только открывает сайт с готовым поиском — заказывать и платить он не умеет и не будет.",
            "Where “search”, “play a song”, “order sushi”, “call a taxi”, “route to…” go. MemuAI only opens the site with the search filled in — it never orders or pays.")}</p>
          ${select("search_engine", s.search_engine, [["google", "Google"], ["yandex", t("Яндекс", "Yandex")], ["duckduckgo", "DuckDuckGo"], ["bing", "Bing"]], t("Поиск", "Search"))}
          ${select("music_service", s.music_service, [["youtube", "YouTube"], ["youtube_music", "YouTube Music"], ["yandex_music", t("Яндекс Музыка", "Yandex Music")], ["spotify", "Spotify"], ["vk", t("VK Музыка", "VK Music")], ["soundcloud", "SoundCloud"]], t("Музыка и видео", "Music and video"))}
          ${select("food_service", s.food_service, [["", t("По языку интерфейса", "By interface language")], ["yandex_eda", t("Яндекс Еда", "Yandex Eda")], ["samokat", t("Самокат", "Samokat")], ["kuper", t("Купер", "Kuper")], ["ubereats", "Uber Eats"], ["doordash", "DoorDash"], ["deliveroo", "Deliveroo"], ["wolt", "Wolt"]], t("Доставка еды", "Food delivery"))}
          ${select("shop_service", s.shop_service, [["", t("По языку интерфейса", "By interface language")], ["ozon", "Ozon"], ["wildberries", "Wildberries"], ["yandex_market", t("Яндекс Маркет", "Yandex Market")], ["amazon", "Amazon"], ["aliexpress", "AliExpress"], ["avito", t("Авито", "Avito")], ["ebay", "eBay"]], t("Покупки", "Shopping"))}
          ${select("taxi_service", s.taxi_service, [["", t("По языку интерфейса", "By interface language")], ["yandex_go", t("Яндекс Go", "Yandex Go")], ["uber", "Uber"], ["bolt", "Bolt"]], t("Такси", "Taxi"))}
          ${select("map_service", s.map_service, [["", t("По языку интерфейса", "By interface language")], ["yandex_maps", t("Яндекс Карты", "Yandex Maps")], ["google_maps", "Google Maps"], ["2gis", t("2ГИС", "2GIS")]], t("Карты", "Maps"))}
          ${select("image_model", s.image_model, [["", S.imageModels.length ? t("Первая модель изображений", "First image model") : t("Нет моделей изображений", "No image models")], ...S.imageModels.map((m) => [m, m])], t("Модель для «нарисуй…»", "Model for “draw…”"))}
        </section>`,
        actions: `<section class="mcs-sec" data-mcs="actions">
          <div class="mcs-sec-head"><h3>${t("Действия и разрешения", "Actions and permissions")}</h3>
            <button type="button" class="mcs-btn mcs-link" data-act="toggle-actions">${S.open.actions ? t("Свернуть", "Collapse") : t("Показать все", "Show all")} (${S.cfg.catalog.length})</button></div>
          <p class="mcs-desc">${t("«Безопасно» — выполняется сразу; «с подтверждением» — кнопка «Выполнить» на карточке; «опасно» (выключение, перезагрузка, сон, выход, PowerShell) — всегда подтверждение и обратный отсчёт с отменой.",
            "“Safe” runs at once; “confirm” needs the Run button on the card; “dangerous” (shutdown, restart, sleep, sign-out, PowerShell) always asks and counts down with a Cancel button.")}</p>
          ${S.open.actions ? actionsHtml() : ""}
        </section>`,
        safety: `<section class="mcs-sec" data-mcs="safety">
          <h3>${t("Безопасность", "Safety")}</h3>
          ${num("confirm_countdown", s.confirm_countdown, 0, 30, t("Обратный отсчёт для опасных действий", "Countdown for dangerous actions"), "", t("с", "s"))}
          ${num("rate_per_minute", s.rate_per_minute, 5, 300, t("Не больше команд в минуту", "Max commands per minute"))}
          ${num("model_rate_per_minute", s.model_rate_per_minute, 1, 120, t("Не больше действий модели в минуту", "Max model actions per minute"))}
          ${sw("advanced_shell", s.advanced_shell, t("Расширенный режим: команды PowerShell", "Advanced mode: PowerShell commands"), t("модель сможет предложить любую команду; каждая выполняется только после вашего подтверждения с отсчётом", "the model may propose any command; each runs only after your confirmation with a countdown"))}
          ${sw("audit", s.audit, t("Журнал выполненных действий", "Log of executed actions"))}
          <div class="mcs-row"><span class="mcs-label">${t("Журнал", "Log")}</span><span class="mcs-tools"><button type="button" class="mcs-btn" data-act="audit">${S.open.audit ? t("Скрыть", "Hide") : t("Показать", "Show")}</button><button type="button" class="mcs-btn" data-act="audit-clear">${t("Очистить", "Clear")}</button></span></div>
          ${S.open.audit ? auditHtml() : ""}
        </section>`,
      };
      const part = ctx.part || "";
      let html;
      if (part === "voice") html = partVoiceHtml(s, v, stt, tts, winVoices);
      else if (part === "commands") {
        html = partCommandsHtml(s) + `<details class="mcs-more" ${S.open.more ? "open" : ""} data-mcs-more>
          <summary><span>${t("Дополнительно", "Advanced")}</span><small>${t("раскладки окон, таймеры, сервисы, разрешения, безопасность", "window layouts, timers, services, permissions, safety")}</small></summary>
          ${SEC.layouts}${SEC.timers}${SEC.services}${SEC.actions}${SEC.safety}</details>`;
      } else html = SEC.main + SEC.custom + SEC.layouts + SEC.voice + SEC.timers + SEC.services + SEC.actions + SEC.safety;
      // Round 24: открытое меню выпадающего списка живёт в <body>; перед перерисовкой закрываем его,
      // иначе оно оставалось «висеть» (баг «Озвучка не закрывается»).
      if (window.MemuLayers) try { window.MemuLayers.closeAll("rerender"); } catch (e) {}
      const view = snapshotView();
      el.innerHTML = `<div class="mcs${part ? " mcs-part-" + part : ""}">${html}</div>`;
      if (typeof window.enhanceSelects === "function" && el.closest(".sx-shell, .settings-body")) try { window.enhanceSelects(el); } catch (e) {}
      restoreView(view);
      const more = el.querySelector("[data-mcs-more]");
      if (more) more.addEventListener("toggle", () => { S.open.more = more.open; });
      if (!el._mcsToggle) { el._mcsToggle = 1; el.addEventListener("toggle", (ev) => { if (ev.target && ev.target.matches && ev.target.matches("[data-mcs-voice-models]")) S.open.models = ev.target.open; }, true); }
    }

    /* Round 23 (Settings → Быстрый доступ → Команды / Голос): calm sub-pages. The sub-page header already explains
       what the page is, so no repeated intro; one primary button; the rest is quiet or behind «Дополнительно».
       Every data-set / data-act stays the same, so the handlers below serve both layouts. */
    function partCommandsHtml(s) {
      const offPlatform = S.cfg.platform !== "win32";
      return `<section class="mcs-sec mcs-card" data-mcs="main">
          ${sw("enabled", s.enabled, t("Команды", "Commands"), t("«открой телеграм», «громкость 30», «таймер 10 минут»", "“open Telegram”, “volume 30”, “timer 10 minutes”"))}
          ${sw("local_match", s.local_match, t("Мгновенно, без модели", "Instant, without a model"), t("частые фразы на русском и английском распознаются на компьютере", "common Russian and English phrases are recognised locally"))}
          ${sw("model_tools", s.model_tools, t("Модель может выполнять действия", "The model may perform actions"), t("опасные всё равно ждут вашего подтверждения", "dangerous ones still wait for your confirmation"))}
          ${routerRows(s)}
          ${offPlatform ? `<p class="mcs-hint">${t("Системные действия (громкость, окна, питание) работают только в приложении MemuAI для Windows. Здесь — таймеры, напоминания, ссылки и свои команды.", "System actions (volume, windows, power) work only in the MemuAI Windows app. Here: timers, reminders, links and custom commands.")}</p>` : ""}
        </section>
        <section class="mcs-sec mcs-group" data-mcs="custom">
          <div class="mcs-group-head"><div><h3>${t("Свои команды", "Custom commands")}</h3>
            <p class="mcs-desc">${t("Фраза или сочетание клавиш запускает цепочку шагов.", "A phrase or a hotkey runs a chain of steps.")}</p></div>
            ${S.draft ? "" : `<button type="button" class="mcs-btn mcs-primary" data-act="new">${t("Новая команда", "New command")}</button>`}</div>
          <div class="mcs-card mcs-card-pad">${S.draft ? editorHtml() : commandsListHtml()}</div>
          ${S.draft ? "" : `<div class="mcs-quiet">
            <button type="button" class="mcs-btn mcs-link" data-act="template">${t("Начать с шаблона «Доброе утро»", "Start from the “Good morning” template")}</button>
            <span class="mcs-dot" aria-hidden="true">·</span>
            <button type="button" class="mcs-btn mcs-link" data-act="import">${t("Импорт", "Import")}</button>
            <button type="button" class="mcs-btn mcs-link" data-act="export">${t("Экспорт", "Export")}</button>
            <input type="file" accept="application/json,.json" data-act="import-file" hidden></div>`}
        </section>`;
    }

    // Round 25: the cloud TTS service (OpenAI, Gemini, ElevenLabs, Azure…) is chosen in Settings → Voice;
    // here only its voice can be overridden, with that service's own voice list (an OpenAI voice sent to
    // Gemini was the reported «Alloy» failure). Empty = the voice from Settings → Voice.
    function apiService() {
      const cat = S.voiceCatalog || {};
      return (cat.tts_api_providers || []).find((p) => p.id === cat.tts_api_provider) || null;
    }
    function apiServiceName() {
      const p = apiService();
      return p && p.id !== "custom" ? ` · ${p.label}` : "";
    }
    function apiVoiceRow(v) {
      const p = apiService();
      const voices = p ? p.voices : [];
      return `<label class="mcs-row"><span class="mcs-label">${t("Голос", "Voice")}<small>${t("пусто — как в Настройки → Голос и Live; сервис, ключ и модель — оттуда же", "empty — as in Settings → Voice & Live; service, key and model come from there too")}</small></span><input type="text" list="mcs-tts-api-voices" data-set="tts_api_voice" data-sub="voice" value="${esc(v.tts_api_voice)}" placeholder="${esc((p && p.default_voice) || "")}"><datalist id="mcs-tts-api-voices">${voices.map((x) => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join("")}</datalist></label>`;
    }

    function partVoiceHtml(s, v, stt, tts, winVoices) {
      const asVoice = t("Как в настройках «Голос и Live»", "As in Voice & Live settings");
      return `<section class="mcs-sec mcs-card" data-mcs="voice">
          ${sw("enabled", v.enabled, t("Голос в быстром доступе", "Voice in Quick Ask"), t("кнопка микрофона в окне и Ctrl+Space", "the mic button in the window and Ctrl+Space"), "voice")}
          ${sw("auto_speak", v.auto_speak, t("Озвучивать ответы", "Speak answers"), "", "voice")}
          ${sw("send_on_release", v.send_on_release, t("Отправлять сразу после распознавания", "Send right after recognition"), "", "voice")}
          ${sw("hold_to_talk", v.hold_to_talk, t("Режим рации", "Push-to-talk"), t("говорите, пока кнопка нажата", "talk while the button is held"), "voice")}
        </section>
        <section class="mcs-sec mcs-group" data-mcs="voice-models">
          <div class="mcs-group-head"><div><h3>${t("Распознавание и озвучка", "Recognition and speech")}</h3>
            <p class="mcs-desc">${t("Обычные модели, не realtime. «Как в настройках «Голос и Live»» — ничего не нужно менять.", "Regular (not realtime) models. “As in Voice & Live settings” needs no changes.")}</p></div></div>
          ${voiceStatusHtml()}
          <div class="mcs-card">
          ${select("stt_provider", v.stt_provider, [["", asVoice], ["local", t("Локальная модель (faster-whisper)", "Local model (faster-whisper)")], ["api", t("API из «Голос и Live» (OpenAI-совместимый)", "API from Voice & Live (OpenAI-compatible)")]], t("Распознавание речи", "Speech recognition"), "voice")}
          ${v.stt_provider === "local" ? select("stt_local_model", v.stt_local_model, [["", asVoice], ...stt.map((m) => [m.id, (m.name || m.id) + (m.downloaded ? "" : t(" — не скачана", " — not downloaded"))])], t("Модель распознавания", "Recognition model"), "voice") : ""}
          ${v.stt_provider === "api" ? `<label class="mcs-row"><span class="mcs-label">${t("Модель распознавания (API)", "Recognition model (API)")}<small>${t("адрес и ключ — из Настройки → Голос и Live", "URL and key come from Settings → Voice & Live")}</small></span><input type="text" list="mcs-stt-api-models" data-set="stt_api_model" data-sub="voice" value="${esc(v.stt_api_model)}" placeholder="gpt-4o-mini-transcribe"><datalist id="mcs-stt-api-models"><option value="gpt-4o-mini-transcribe"><option value="gpt-4o-transcribe"><option value="whisper-1"><option value="whisper-large-v3"><option value="whisper-large-v3-turbo"></datalist></label>` : ""}
          ${select("language", v.language, [["", asVoice], ["auto", t("Автоопределение", "Auto-detect")], ["ru", "Русский"], ["en", "English"]], t("Язык речи", "Speech language"), "voice")}
          ${select("tts_provider", v.tts_provider, [["", asVoice], ["windows", t("Голоса Windows", "Windows voices")], ["downloaded", t("Скачанные голоса (Piper и др.)", "Downloaded voices (Piper etc.)")], ["api", t("Облачный сервис из «Голос и Live»", "Cloud service from Voice & Live") + apiServiceName()], ["local", t("Локальный сервер из «Голос и Live»", "Local server from Voice & Live")]], t("Озвучка", "Text to speech"), "voice")}
          ${v.tts_provider === "windows" ? select("tts_voice", v.tts_voice, [["", t("Автоматически по языку", "Automatic by language")], ...winVoices.map((x) => [x.name, x.name + (x.culture ? " (" + x.culture + ")" : "")])], t("Голос", "Voice"), "voice") : ""}
          ${v.tts_provider === "downloaded" ? select("tts_local_model", v.tts_local_model, [["", tts.some((m) => m.downloaded) ? asVoice : t("Нет скачанных голосов — скачайте ниже", "No downloaded voices — download below")], ...tts.filter((m) => m.downloaded).map((m) => [m.id, m.name || m.id])], t("Голос", "Voice"), "voice") : ""}
          ${v.tts_provider === "api" ? apiVoiceRow(v) : ""}
          ${sw("stt_postprocess", v.stt_postprocess, t("Подправлять текст моделью", "Polish the transcript with a model"), t("медленнее; обычно не нужно", "slower; usually not needed"), "voice")}
          </div>
          ${voiceModelsHtml()}
        </section>`;
    }

    function commandsListHtml() {
      const rows = S.cfg.commands || [];
      if (!rows.length) return ctx.part
        ? `<div class="mcs-empty">${t("Своих команд пока нет. Например: «доброе утро» → открыть почту, погоду и музыку.", "No custom commands yet. E.g. “good morning” → open mail, weather and music.")}</div>`
        : `<div class="mcs-empty">${t("Своих команд пока нет. Начните с шаблона «Доброе утро» или создайте простую привязку: фраза → действие.", "No custom commands yet. Start with the “Good morning” template or make a simple bind: phrase → action.")}</div>`;
      const hk = S.cfg.hotkeys || {};
      return `<div class="mcs-cmds">${rows.map((c, i) => {
        const state = hk[c.id];
        return `<div class="mcs-cmd ${c.enabled ? "" : "off"}">
          <span class="mcs-switch"><input type="checkbox" data-cmd-toggle="${esc(c.id)}" ${c.enabled ? "checked" : ""} aria-label="${t("Включена", "Enabled")}"><i></i></span>
          <div class="mcs-cmd-main"><b>${esc(c.name)}</b>
            <span class="mcs-cmd-meta">${(c.triggers || []).slice(0, 4).map((x) => `<em>«${esc(x)}»</em>`).join(" ")}${c.hotkey ? ` <kbd>${esc(c.hotkey)}</kbd>${state && !state.ok && state.code !== "unsupported" ? ` <span class="mcs-bad">${esc(state.error || t("клавиша занята", "hotkey busy"))}</span>` : ""}` : ""}
            · ${t("шагов", "steps")}: ${c.steps.length}</span></div>
          <button type="button" class="mcs-btn" data-act="run" data-i="${i}">${t("Запустить", "Run")}</button>
          <button type="button" class="mcs-btn" data-act="edit" data-i="${i}">${t("Изменить", "Edit")}</button>
          <button type="button" class="mcs-btn mcs-danger" data-act="delete" data-i="${i}" aria-label="${t("Удалить", "Delete")}">✕</button></div>`;
      }).join("")}</div>`;
    }

    function layoutsHtml() {
      const rows = Object.values(S.cfg.layouts || {});
      if (!rows.length) return `<div class="mcs-empty">${t("Сохранённых раскладок нет.", "No saved layouts.")}</div>`;
      return `<div class="mcs-cmds">${rows.map((l) => `<div class="mcs-cmd"><div class="mcs-cmd-main"><b>${esc(l.name)}</b>
        <span class="mcs-cmd-meta">${t("окон", "windows")}: ${l.windows} · ${esc((l.apps || []).join(", "))}</span></div>
        <button type="button" class="mcs-btn" data-act="layout-restore" data-name="${esc(l.name)}">${t("Восстановить", "Restore")}</button>
        <button type="button" class="mcs-btn mcs-danger" data-act="layout-delete" data-name="${esc(l.name)}" aria-label="${t("Удалить", "Delete")}">✕</button></div>`).join("")}</div>`;
    }

    function actionsHtml() {
      const s = set();
      const groups = {};
      for (const a of S.cfg.catalog) (groups[a.cat] = groups[a.cat] || []).push(a);
      return Object.entries(groups).map(([cat, rows]) => `<div class="mcs-actgroup"><h4>${esc(S.cfg.categories[cat] || cat)}</h4>
        ${rows.map((a) => `<div class="mcs-act"><span class="mcs-switch"><input type="checkbox" data-action-on="${esc(a.id)}" ${s.disabled_actions.includes(a.id) ? "" : "checked"}><i></i></span>
          <span class="mcs-act-name">${esc(a.title)}<small>${esc(a.id)}</small></span>
          <span class="mcs-tier t-${esc(a.tier)}">${esc(TIER[a.tier][en ? 1 : 0])}</span>
          ${a.tier === "safe" ? `<label class="mcs-mini"><input type="checkbox" data-always="${esc(a.id)}" ${s.always_confirm.includes(a.id) ? "checked" : ""}>${t("спрашивать", "ask first")}</label>`
            : a.tier === "confirm" ? `<label class="mcs-mini"><input type="checkbox" data-trust="${esc(a.id)}" ${s.trusted_actions.includes(a.id) ? "checked" : ""}>${t("без вопроса", "don't ask")}</label>` : `<span class="mcs-mini muted">${t("всегда спрашивать", "always asks")}</span>`}
        </div>`).join("")}</div>`).join("");
    }

    function auditHtml() {
      if (!S.audit) { loadAudit(); return `<div class="mcs-empty">${t("Загрузка…", "Loading…")}</div>`; }
      if (!S.audit.length) return `<div class="mcs-empty">${t("Журнал пуст.", "The log is empty.")}</div>`;
      return `<div class="mcs-audit">${S.audit.slice(0, 80).map((r) => `<div class="mcs-audit-row s-${esc(r.status)}"><time>${new Date((r.ts || 0) * 1000).toLocaleString(en ? "en-GB" : "ru-RU")}</time>
        <b>${esc(r.action)}</b><span>${esc(r.status)}</span><em>${esc(r.source)}</em><small>${esc(r.message || "")}</small></div>`).join("")}</div>`;
    }
    async function loadAudit() {
      try { S.audit = (await call("/api/assistant/audit?limit=200")).items || []; } catch (e) { S.audit = []; }
      render();
    }

    /* ---------------------------------------------------------------- command editor */
    function blankStep(type = "action") {
      if (type === "wait") return { type, seconds: 2 };
      if (type === "say" || type === "notify") return { type, text: "" };
      if (type === "ask_ai") return { type, prompt: "", speak: false };
      if (type === "layout") return { type, layout: null, launch: true, timeout: 25 };
      return { type: "action", action: "app.open", params: {} };
    }
    function editorHtml() {
      const d = S.draft;
      const byId = catalogById();
      const actionOptions = (selected) => {
        const groups = {};
        for (const a of S.cfg.catalog) (groups[a.cat] = groups[a.cat] || []).push(a);
        return Object.entries(groups).map(([cat, rows]) => `<optgroup label="${esc(S.cfg.categories[cat] || cat)}">${rows.map((a) => `<option value="${esc(a.id)}" ${a.id === selected ? "selected" : ""}>${esc(a.title)}${a.tier !== "safe" ? " · " + esc(TIER[a.tier][en ? 1 : 0]) : ""}</option>`).join("")}</optgroup>`).join("");
      };
      const PARAM_EN = { delay_minutes: "delay, minutes", in_minutes: "in, minutes", short_break: "short break, min", long_break: "long break, min", work: "focus, min" };
      const pname = (key) => (en ? (PARAM_EN[key] || key.replace(/_/g, " ")) : (PARAM_RU[key] || key));
      const ename = (x) => { x = String(x); return (en ? ENUM_EN[x] : ENUM_RU[x]) || x.replace(/_/g, " "); };
      const paramFields = (step, si) => {
        const a = byId[step.action];
        if (!a) return "";
        return Object.entries(a.params).map(([key, spec]) => {
          const value = step.params && step.params[key] !== undefined ? step.params[key] : "";
          const req = a.required.includes(key) ? " *" : "";
          if (spec.enum) return `<label class="mcs-param"><span>${esc(pname(key))}${req}</span><select data-param="${esc(key)}" data-si="${si}"><option value=""></option>${spec.enum.filter((x) => x !== "").map((x) => `<option value="${esc(x)}" ${String(value) === x ? "selected" : ""}>${esc(ename(x))}</option>`).join("")}</select></label>`;
          if (spec.type === "boolean") return `<label class="mcs-param mcs-param-bool"><input type="checkbox" data-param="${esc(key)}" data-si="${si}" data-kind="bool" ${value === true ? "checked" : ""}><span>${esc(pname(key))}${req}</span></label>`;
          const type = spec.type === "number" || spec.type === "integer" ? "number" : "text";
          return `<label class="mcs-param ${key === "text" || key === "prompt" || key === "command" ? "wide" : ""}"><span>${esc(pname(key))}${req}</span><input type="${type}" data-param="${esc(key)}" data-si="${si}" ${type === "number" ? `data-kind="num" ${spec.minimum !== undefined ? `min="${spec.minimum}"` : ""} ${spec.maximum !== undefined ? `max="${spec.maximum}"` : ""}` : ""} value="${esc(value)}"></label>`;
        }).join("");
      };
      const stepBody = (step, si) => {
        if (step.type === "action") return `<select data-step-action="${si}">${actionOptions(step.action)}</select><div class="mcs-params">${paramFields(step, si)}</div>`;
        if (step.type === "wait") return `<label class="mcs-param"><span>${t("секунд", "seconds")}</span><input type="number" min="0.1" max="600" step="0.1" data-field="seconds" data-si="${si}" value="${esc(step.seconds)}"></label>`;
        if (step.type === "say" || step.type === "notify") return `<label class="mcs-param wide"><span>${t("текст", "text")}</span><input type="text" data-field="text" data-si="${si}" value="${esc(step.text)}"></label>`;
        if (step.type === "ask_ai") return `<label class="mcs-param wide"><span>${t("запрос к модели", "prompt")}</span><textarea data-field="prompt" data-si="${si}" rows="2">${esc(step.prompt)}</textarea></label>
          <label class="mcs-param mcs-param-bool"><input type="checkbox" data-field="speak" data-kind="bool" data-si="${si}" ${step.speak ? "checked" : ""}><span>${t("озвучить ответ", "speak the answer")}</span></label>`;
        if (step.type === "layout") {
          const l = step.layout;
          return `<div class="mcs-layout">${l ? `<span>${t("окон", "windows")}: ${l.windows.length} · ${t("мониторов", "monitors")}: ${l.monitors.length}</span>
            <ul>${l.windows.slice(0, 10).map((w) => `<li>${esc(w.app.name || w.app.exe)} — ${t("монитор", "monitor")} ${w.monitor + 1}, ${w.rect[2] - w.rect[0]}×${w.rect[3] - w.rect[1]}${w.state !== "normal" ? " · " + esc(w.state) : ""}</li>`).join("")}</ul>` : `<span class="mcs-bad">${t("Раскладка ещё не записана", "Layout not captured yet")}</span>`}
            <button type="button" class="mcs-btn mcs-primary" data-act="step-capture" data-si="${si}">${t("Запомнить текущие окна", "Capture the current windows")}</button>
            <label class="mcs-param mcs-param-bool"><input type="checkbox" data-field="launch" data-kind="bool" data-si="${si}" ${step.launch !== false ? "checked" : ""}><span>${t("запускать закрытые программы", "launch closed apps")}</span></label>
            <label class="mcs-param"><span>${t("ждать окна, с", "wait for windows, s")}</span><input type="number" min="3" max="180" data-field="timeout" data-kind="num" data-si="${si}" value="${esc(step.timeout || 25)}"></label></div>`;
        }
        return "";
      };
      const whenHtml = (step, si) => {
        const w = step.when || {};
        const open = !!step._whenOpen || Object.keys(w).length > 0;
        if (!open) return `<button type="button" class="mcs-btn mcs-link" data-act="when-open" data-si="${si}">+ ${t("условие", "condition")}</button>`;
        return `<div class="mcs-when"><span>${t("Только если", "Only if")}:</span>
          <label>${t("с", "from")} <input type="time" data-when="time_from" data-si="${si}" value="${esc(w.time_from || "")}"></label>
          <label>${t("до", "to")} <input type="time" data-when="time_to" data-si="${si}" value="${esc(w.time_to || "")}"></label>
          <span class="mcs-days">${WEEK.map((name, k) => `<label><input type="checkbox" data-when-day="${k + 1}" data-si="${si}" ${(w.weekdays || []).includes(k + 1) ? "checked" : ""}>${name[en ? 1 : 0]}</label>`).join("")}</span>
          <label>${t("запущено", "running")} <input type="text" data-when="app_running" data-si="${si}" value="${esc(w.app_running || "")}" placeholder="${t("приложение", "app")}"></label>
          <label>${t("не запущено", "not running")} <input type="text" data-when="app_not_running" data-si="${si}" value="${esc(w.app_not_running || "")}" placeholder="${t("приложение", "app")}"></label></div>`;
      };
      return `<div class="mcs-editor">
        <div class="mcs-grid">
          <label class="mcs-field"><span>${t("Название", "Name")}</span><input type="text" data-d="name" value="${esc(d.name)}" maxlength="60"></label>
          <label class="mcs-field"><span>${t("Горячая клавиша", "Hotkey")}</span><input type="text" data-d="hotkey" value="${esc(d.hotkey || "")}" placeholder="${t("нажмите сочетание…", "press a combination…")}" readonly data-hotkey="1"></label>
        </div>
        <label class="mcs-field"><span>${t("Фразы-триггеры (по одной в строке; русский и английский; опечатки допустимы)", "Trigger phrases (one per line; any language; typos are fine)")}</span>
          <textarea data-d="triggers" rows="3">${esc((d.triggers || []).join("\n"))}</textarea></label>
        <label class="mcs-field"><span>${t("Описание", "Description")}</span><input type="text" data-d="description" value="${esc(d.description || "")}" maxlength="300"></label>
        ${sw("trusted", d.trusted, t("Не спрашивать подтверждение для шагов «с подтверждением»", "Don't ask before “confirm” steps"), t("опасные шаги (выключение и т. п.) всё равно требуют подтверждения", "dangerous steps (shutdown etc.) always ask"), "draft")}
        <h4>${t("Шаги", "Steps")}</h4>
        <ol class="mcs-steps">${(d.steps || []).map((step, si) => `<li class="mcs-step">
          <div class="mcs-step-head"><select data-step-type="${si}">${STEP_TYPES.map((x) => `<option value="${x}" ${x === step.type ? "selected" : ""}>${esc({ action: t("Действие", "Action"), wait: t("Пауза", "Wait"), say: t("Сказать", "Say"), notify: t("Уведомление", "Notification"), ask_ai: t("Спросить ИИ", "Ask AI"), layout: t("Раскладка окон", "Window layout") }[x])}</option>`).join("")}</select>
            <span class="mcs-step-tools"><button type="button" class="mcs-icon" data-act="step-up" data-si="${si}" aria-label="↑">↑</button><button type="button" class="mcs-icon" data-act="step-down" data-si="${si}" aria-label="↓">↓</button><button type="button" class="mcs-icon mcs-danger" data-act="step-del" data-si="${si}" aria-label="${t("Удалить шаг", "Delete step")}">✕</button></span></div>
          <div class="mcs-step-body">${stepBody(step, si)}</div>${whenHtml(step, si)}</li>`).join("")}</ol>
        <button type="button" class="mcs-btn" data-act="step-add">+ ${t("Шаг", "Step")}</button>
        <div class="mcs-editor-foot"><span class="mcs-err" data-err></span>
          <button type="button" class="mcs-btn" data-act="cancel-edit">${t("Отмена", "Cancel")}</button>
          <button type="button" class="mcs-btn" data-act="test">${t("Проверить", "Test")}</button>
          <button type="button" class="mcs-btn mcs-primary" data-act="save">${t("Сохранить", "Save")}</button></div>
      </div>`;
    }

    function cleanDraft() {
      const d = JSON.parse(JSON.stringify(S.draft));
      d.triggers = String(d.triggersText !== undefined ? d.triggersText : (d.triggers || []).join("\n")).split(/\n+/).map((x) => x.trim()).filter(Boolean);
      delete d.triggersText;
      d.steps = (d.steps || []).map((s) => { const out = { ...s }; delete out._whenOpen; if (out.when && !Object.keys(out.when).length) delete out.when; return out; });
      return d;
    }
    function showError(text) { const e = el.querySelector("[data-err]"); if (e) e.textContent = text || ""; }

    /* ---------------------------------------------------------------- events */
    el.addEventListener("change", (e) => {
      const target = e.target;
      remember(target);
      if (target.dataset.set) {
        const sub = target.dataset.sub;
        let value = target.type === "checkbox" ? target.checked : target.value;
        if (target.type === "number" || target.type === "range") value = Number(value);
        if (sub === "draft") { S.draft[target.dataset.set] = value; return; }
        if (sub) { set()[sub] = { ...set()[sub], [target.dataset.set]: value }; savePatch({ [sub]: { ...(S.pendingPatch[sub] || {}), [target.dataset.set]: value } }); if (sub === "voice" && /provider/.test(target.dataset.set)) render(); }
        else { set()[target.dataset.set] = value; savePatch({ [target.dataset.set]: value }); if (target.dataset.set === "ai_router") render(); }
        if (target.type === "range") { const em = target.parentElement.querySelector("em"); if (em) em.textContent = value + "%"; }
        return;
      }
      if (target.dataset.cmdToggle) {
        call("/api/assistant/commands", { op: "toggle", id: target.dataset.cmdToggle, enabled: target.checked })
          .then((d) => { S.cfg.commands = d.commands; S.cfg.hotkeys = d.hotkeys || S.cfg.hotkeys; render(); }).catch((err) => toast(err.message, "err"));
        return;
      }
      if (target.dataset.actionOn) {
        const s = set(); const id = target.dataset.actionOn;
        s.disabled_actions = target.checked ? s.disabled_actions.filter((x) => x !== id) : [...new Set([...s.disabled_actions, id])];
        savePatch({ disabled_actions: s.disabled_actions }); return;
      }
      if (target.dataset.always) {
        const s = set(); const id = target.dataset.always;
        s.always_confirm = target.checked ? [...new Set([...s.always_confirm, id])] : s.always_confirm.filter((x) => x !== id);
        savePatch({ always_confirm: s.always_confirm }); return;
      }
      if (target.dataset.trust) {
        const s = set(); const id = target.dataset.trust;
        s.trusted_actions = target.checked ? [...new Set([...s.trusted_actions, id])] : s.trusted_actions.filter((x) => x !== id);
        savePatch({ trusted_actions: s.trusted_actions }); return;
      }
      if (!S.draft) return;
      const si = target.dataset.si !== undefined ? +target.dataset.si : -1;
      const step = si >= 0 ? S.draft.steps[si] : null;
      if (target.dataset.stepType !== undefined) { S.draft.steps[+target.dataset.stepType] = blankStep(target.value); render(); return; }
      if (target.dataset.stepAction !== undefined) { S.draft.steps[+target.dataset.stepAction] = { ...S.draft.steps[+target.dataset.stepAction], action: target.value, params: {} }; render(); return; }
      if (step && target.dataset.param) {
        step.params = step.params || {};
        let value = target.dataset.kind === "bool" ? target.checked : target.value;
        if (target.dataset.kind === "num") value = target.value === "" ? "" : Number(target.value);
        if (value === "" || value === null) delete step.params[target.dataset.param]; else step.params[target.dataset.param] = value;
        return;
      }
      if (step && target.dataset.field) { step[target.dataset.field] = target.dataset.kind === "bool" ? target.checked : target.dataset.kind === "num" ? Number(target.value) : target.value; return; }
      if (step && target.dataset.when) { step.when = { ...(step.when || {}) }; if (target.value) step.when[target.dataset.when] = target.value; else delete step.when[target.dataset.when]; return; }
      if (step && target.dataset.whenDay) {
        step.when = { ...(step.when || {}) };
        const day = +target.dataset.whenDay;
        const days = new Set(step.when.weekdays || []);
        if (target.checked) days.add(day); else days.delete(day);
        if (days.size) step.when.weekdays = [...days].sort(); else delete step.when.weekdays;
        return;
      }
      if (target.dataset.d) { if (target.dataset.d === "triggers") S.draft.triggersText = target.value; else S.draft[target.dataset.d] = target.value; }
    });
    el.addEventListener("input", (e) => {
      const target = e.target;
      if (S.draft && target.dataset.d) { if (target.dataset.d === "triggers") S.draft.triggersText = target.value; else S.draft[target.dataset.d] = target.value; }
      if (target.type === "range" && target.dataset.set) { const em = target.parentElement.querySelector("em"); if (em) em.textContent = target.value + "%"; }
    });
    el.addEventListener("keydown", (e) => {
      const target = e.target;
      if (!target.dataset || !target.dataset.hotkey) return;
      if (e.key === "Tab") return;
      e.preventDefault();
      if (e.key === "Backspace" || e.key === "Delete" || e.key === "Escape") { target.value = ""; S.draft.hotkey = ""; return; }
      const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
      if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) { target.value = mods.join("+") + "+…"; return; }
      let key = e.code.replace(/^Key/, "").replace(/^Digit/, "").replace(/^Numpad/, "Num");
      if (/^F\d+$/.test(e.key)) key = e.key;
      const combo = [...mods, key].join("+");
      target.value = combo; S.draft.hotkey = combo;
    });

    el.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn || !el.contains(btn)) return;
      const act = btn.dataset.act;
      const si = btn.dataset.si !== undefined ? +btn.dataset.si : -1;
      try {
        if (act === "new") { S.draft = { name: "", triggers: [], hotkey: "", trusted: false, steps: [blankStep()], enabled: true }; render(); }
        else if (act === "template") { S.draft = JSON.parse(JSON.stringify(S.cfg.template)); S.draft.steps.push(blankStep("layout")); render(); }
        else if (act === "edit") { S.draft = JSON.parse(JSON.stringify(S.cfg.commands[+btn.dataset.i])); render(); }
        else if (act === "cancel-edit") { S.draft = null; render(); }
        else if (act === "delete") {
          const c = S.cfg.commands[+btn.dataset.i];
          if (!window.confirm(t(`Удалить команду «${c.name}»?`, `Delete the command “${c.name}”?`))) return;
          const d = await call("/api/assistant/commands", { op: "delete", id: c.id }); S.cfg.commands = d.commands; render();
        }
        else if (act === "run") {
          const c = S.cfg.commands[+btn.dataset.i];
          const d = await call("/api/assistant/run", { action: "command.run", params: { name: c.name, id: c.id }, lang: L, source: "settings" });
          toast((d.card && (d.card.message || d.card.title)) || "OK", d.card && d.card.status === "error" ? "err" : "");
        }
        else if (act === "save" || act === "test") {
          const command = cleanDraft();
          showError("");
          if (act === "test") {
            const d = await call("/api/assistant/commands", { op: "test", command, lang: L });
            toast(d.steps.map((s) => `${s.status === "done" ? "✓" : s.status === "skipped" ? "–" : "✗"} ${s.title || s.type}`).join("  "), "");
            return;
          }
          const d = await call("/api/assistant/commands", { op: "save", command, lang: L });
          S.cfg.commands = d.commands; S.cfg.hotkeys = d.hotkeys || {}; S.draft = null; render();
          const bad = Object.values(d.hotkeys || {}).find((h) => h && h.ok === false && h.code !== "unsupported");
          toast(bad ? t("Сохранено, но горячая клавиша не назначена: ", "Saved, but the hotkey was not registered: ") + (bad.error || bad.code) : t("Команда сохранена", "Command saved"), bad ? "err" : "");
        }
        else if (act === "step-add") { S.draft.steps.push(blankStep()); render(); }
        else if (act === "step-del") { S.draft.steps.splice(si, 1); render(); }
        else if (act === "step-up" && si > 0) { const s = S.draft.steps; [s[si - 1], s[si]] = [s[si], s[si - 1]]; render(); }
        else if (act === "step-down" && si < S.draft.steps.length - 1) { const s = S.draft.steps; [s[si + 1], s[si]] = [s[si], s[si + 1]]; render(); }
        else if (act === "when-open") { S.draft.steps[si]._whenOpen = true; render(); }
        else if (act === "step-capture") {
          btn.disabled = true; btn.textContent = t("Запоминаю…", "Capturing…");
          const d = await call("/api/assistant/layout", { op: "capture", lang: L, reshow: false });
          S.draft.steps[si].layout = d.layout; render();
        }
        else if (act === "capture-layout") {
          const name = window.prompt(t("Название раскладки:", "Layout name:"), t("работа", "work"));
          if (!name) return;
          await call("/api/assistant/layout", { op: "capture", name, save: true, lang: L, reshow: false });
          S.cfg = await call("/api/assistant/config?lang=" + L); render();
          toast(t("Раскладка сохранена", "Layout saved"), "");
        }
        else if (act === "layout-delete") { await call("/api/assistant/layout", { op: "delete", name: btn.dataset.name }); S.cfg = await call("/api/assistant/config?lang=" + L); render(); }
        else if (act === "layout-restore") { const d = await call("/api/assistant/layout", { op: "restore", name: btn.dataset.name, lang: L }); toast(t(`Окон на месте: ${d.report.placed} из ${d.report.total}`, `${d.report.placed} of ${d.report.total} windows placed`), ""); }
        else if (act === "export") {
          const d = await call("/api/assistant/commands", { op: "export" });
          const blob = new Blob([JSON.stringify(d, null, 2)], { type: "application/json" });
          const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "memuai-quick-commands.json"; document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        }
        else if (act === "import") { el.querySelector('[data-act="import-file"]').click(); }
        else if (act === "toggle-actions") { S.open.actions = !S.open.actions; render(); }
        else if (act === "audit") { S.open.audit = !S.open.audit; if (S.open.audit) S.audit = null; render(); }
        else if (act === "audit-clear") { await call("/api/assistant/audit/clear", {}); S.audit = []; render(); }
        else if (act === "vm-download" || act === "vm-delete" || act === "vm-cancel") {
          const op = act.slice(3);
          if (op === "delete" && !confirm(t("Удалить модель с диска?", "Delete the model from disk?"))) return;
          btn.disabled = true;
          await call("/api/voice/models/" + op, { id: btn.dataset.id, kind: btn.dataset.kind });
          S.open.models = true;
          await loadVoiceStatus(true); patchVoiceStatus(); pollDownloads();
        }
        else if (act === "vm-use") {
          const kind = btn.dataset.kind;
          const patch = kind === "stt" ? { stt_provider: "local", stt_local_model: btn.dataset.id } : { tts_provider: "downloaded", tts_local_model: btn.dataset.id };
          set().voice = { ...set().voice, ...patch };
          savePatch({ voice: { ...(S.pendingPatch.voice || {}), ...patch } });
          await flushSave(); S.open.models = true; render();
        }
        else if (act === "open-voice") {
          if (typeof window.openSettingsPage === "function") window.openSettingsPage("voice");
          else if (window.MemuQuick && window.MemuQuick.api) window.MemuQuick.api("/api/quick/window", { body: { action: "open_settings" } }).catch(() => {});
        }
        else if (act === "stt-test") {
          await flushSave();
          if (!navigator.mediaDevices || !window.MediaRecorder) throw new Error(t("Запись звука недоступна в этом окне", "Recording is unavailable here"));
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          const rec = new MediaRecorder(stream); const chunks = [];
          rec.ondataavailable = (ev) => chunks.push(ev.data);
          const done = new Promise((r) => { rec.onstop = r; });
          btn.disabled = true; const old = btn.textContent; btn.textContent = t("Говорите… 3 с", "Speak… 3 s");
          rec.start(); setTimeout(() => rec.stop(), 3000); await done;
          stream.getTracks().forEach((x) => x.stop());
          btn.textContent = t("Распознаю…", "Recognising…");
          const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
          const audio = await new Promise((r) => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
          try { const out = await call("/api/voice/transcribe", { audio, quick: true, raw: true }); toast(t("Распознано: ", "Recognised: ") + (out.text || "—")); }
          finally { btn.disabled = false; btn.textContent = old; }
        }
        else if (act === "voice-test") {
          await flushSave();
          const res = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: t("Привет! Я быстрый доступ MemuAI.", "Hi! I'm MemuAI Quick Ask."), quick: true }) });
          const out = await res.json().catch(() => ({}));
          if (!res.ok) throw new Error(out.error || "HTTP " + res.status);
          new Audio(out.audio).play().catch(() => {});
        }
      } catch (err) {
        if (S.draft && (act === "save" || act === "test")) showError(err.message);
        else toast(err.message, "err");
        if (act === "step-capture") render();
      }
    });
    el.addEventListener("change", async (e) => {
      if (e.target.dataset.act !== "import-file") return;
      const file = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!file) return;
      try {
        const data = JSON.parse(await file.text());
        const replace = window.confirm(t("Заменить все свои команды импортированными? «Отмена» — добавить к существующим.", "Replace all custom commands with the imported ones? Cancel = add to the existing ones."));
        const d = await call("/api/assistant/commands", { op: "import", commands: data.commands || data, replace });
        S.cfg.commands = d.commands; S.cfg.hotkeys = d.hotkeys || {}; render();
        toast(t(`Импортировано: ${d.imported}`, `Imported: ${d.imported}`) + (d.errors.length ? " · " + t("пропущено: ", "skipped: ") + d.errors.length : ""), d.errors.length ? "err" : "");
      } catch (err) { toast(t("Не удалось импортировать: ", "Import failed: ") + err.message, "err"); }
    });

    load();
    return { reload: load, unmount() { el.innerHTML = ""; } };
  }

  window.MemuCommandsSettings = { mount };
})();
