"use strict";

/* Settings for everyone: the «Главная» page (search in place, common tasks, connections, recent changes)
   and the MemuCode page. app.js renders the shell; this file renders and wires these two pages. */
(() => {
  const isEn = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const RECENT_KEY = "memu.settings.recent";
  const RECENT_MAX = 6;

  const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    palette: P('<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.6-.8 1.6-1.6 0-.5-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-4.1-4-7.6-9-7.6z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="7.5" r="1"/>'),
    bot: P('<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 4v4M9 13h.01M15 13h.01M9.5 16h5"/>'),
    chat: P('<path d="M5 18.5 3.5 21V6a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6.5z"/><path d="M8 9h8M8 13h5"/>'),
    mic: P('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>'),
    bell: P('<path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>'),
    zap: P('<path d="M13 3 5 13.5h6L10.5 21 19 10.5h-6z"/>'),
    send: P('<path d="M21 4 3 11l7 2.5L12.5 21z"/><path d="m10 13.5 4.5-4.5"/>'),
    user: P('<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0"/>'),
    cloud: P('<path d="M7 18.5h10.5a4 4 0 0 0 .6-8 6 6 0 0 0-11.6 1.4A3.4 3.4 0 0 0 7 18.5z"/>'),
    code: P('<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>'),
    globe: P('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.6 5.7 3.6 9s-1.1 6.3-3.6 9c-2.5-2.7-3.6-5.7-3.6-9S9.5 5.7 12 3z"/>'),
    book: P('<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5z"/><path d="M4 20.5A2.5 2.5 0 0 0 6.5 23H20v-5"/>'),
    lang: P('<path d="M4 5h9M8.5 3v2M6 5c.6 3.4 2.9 6.2 6 7.6M11 5c-.7 3.7-3.4 6.8-7 8"/><path d="m13 21 4-9 4 9M14.5 18h5"/>'),
    shield: P('<path d="M12 3 5 6v5.5c0 4.4 3 8.2 7 9.5 4-1.3 7-5.1 7-9.5V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>'),
    clock: P('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    search: P('<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>'),
    layers: P('<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>'),
    terminal: P('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7 9 3 3-3 3M12.5 15H17"/>'),
    check: P('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
    arrow: P('<path d="M9 6l6 6-6 6"/>'),
    file: P('<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5"/>'),
  };

  function readRecent() {
    try {
      const rows = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(rows) ? rows.filter((row) => row && typeof row.page === "string" && typeof row.label === "string").slice(0, RECENT_MAX) : [];
    } catch (e) { return []; }
  }
  function remember(page, label) {
    const text = String(label || "").replace(/\s+/g, " ").trim().slice(0, 90);
    if (!page || !text || page === "home") return;
    const rows = readRecent().filter((row) => !(row.page === page && row.label === text));
    rows.unshift({ page, label: text, t: Date.now() });
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(rows.slice(0, RECENT_MAX))); } catch (e) { /* private mode */ }
  }
  function track(shell) {
    if (!shell || shell.dataset.sxTracked) return;
    shell.dataset.sxTracked = "1";
    shell.addEventListener("change", (event) => {
      const target = event.target;
      if (!target?.matches?.("input, select, textarea") || target.id === "sx-search") return;
      const pane = target.closest(".settings-tab-pane");
      if (!pane || pane.id === "pane-home") return;
      const row = target.closest(".sx-row, .sx-field");
      const label = row?.querySelector(".sx-label, .sx-field-label");
      const text = label ? [...label.childNodes].filter((node) => node.nodeType === 3 || node.nodeName === "SPAN" && !node.className).map((node) => node.textContent).join("").trim() || label.textContent : "";
      if (text) remember(pane.dataset.page || pane.id.replace(/^pane-/, ""), text);
    }, true);
  }
  function ago(ts) {
    const minutes = Math.max(0, Math.round((Date.now() - Number(ts || 0)) / 60000));
    if (minutes < 1) return L("только что", "just now");
    if (minutes < 60) return L(`${minutes} мин назад`, `${minutes} min ago`);
    const hours = Math.round(minutes / 60);
    if (hours < 24) return L(`${hours} ч назад`, `${hours} h ago`);
    const days = Math.round(hours / 24);
    return L(`${days} дн. назад`, `${days} d ago`);
  }

  function messengersOn(ctx) {
    return [["telegram_enabled", "Telegram"], ["discord_enabled", "Discord"], ["whatsapp_enabled", "WhatsApp"]]
      .filter(([key]) => ctx.pref(key, false)).map(([, name]) => name);
  }

  function themeText(ctx) {
    const theme = ctx.pref("theme", "dark");
    const names = { dark: L("Тёмная", "Dark"), light: L("Светлая", "Light"), system: L("Как в системе", "System"), auto: L("Как в системе", "System") };
    return names[theme] || theme;
  }
  function tiles(ctx) {
    const pageTitle = (id) => (ctx.pages.find((p) => p.id === id) || {}).title || id;
    const has = (id) => ctx.pages.some((p) => p.id === id);
    const model = ctx.pref("default_model", "");
    const rows = [
      { page: "appearance", icon: ICONS.palette, title: L("Тема и цвета", "Theme and colours"), desc: L("Тёмная или светлая, акцент, дизайн и шрифт", "Dark or light, accent, design and font"), value: themeText(ctx) },
      { page: "models", icon: ICONS.bot, title: L("Модель по умолчанию", "Default model"), desc: L("Какая нейросеть отвечает в новых чатах", "Which AI answers in new chats"), value: model ? ctx.modelName(model) : L("Выбирается автоматически", "Picked automatically") },
      { page: "chat", icon: ICONS.chat, title: L("Характер ассистента", "Assistant personality"), desc: L("Тон ответов и ваши постоянные инструкции", "Reply tone and your standing instructions"), value: window.MemuPersonalities ? MemuPersonalities.label(ctx.pref("ai_personality", "friendly")) : "" },
      { page: "general", icon: ICONS.lang, title: L("Язык и ввод", "Language and typing"), desc: L("Язык программы, отправка по Enter, орфография", "App language, Enter to send, spell check"), value: isEn() ? "English" : "Русский" },
      { page: "voice", icon: ICONS.mic, title: L("Голос", "Voice"), desc: L("Диктовка, озвучивание и голосовой разговор Live", "Dictation, reading aloud and Live voice chat"), value: ctx.pref("voice_mic_visible", true) ? L("Микрофон в поле ввода", "Mic in the message box") : L("Микрофон скрыт", "Mic hidden") },
      { page: "notifications", icon: ICONS.bell, title: L("Уведомления", "Notifications"), desc: L("Звук и сообщение, когда ответ готов", "Sound and a pop-up when a reply is ready"), value: ctx.pref("notify_windows", true) ? L("Включены", "On") : L("Выключены", "Off") },
      { page: "quick", icon: ICONS.zap, title: L("Быстрый вопрос", "Quick Ask"), desc: L("Маленькое окно по Alt+Space поверх программ", "A small Alt+Space window above any app"), value: "Alt+Space" },
      { page: "remote", icon: ICONS.send, title: L("Telegram, Discord, WhatsApp", "Telegram, Discord, WhatsApp"), desc: L("Пишите MemuAI из мессенджера и получайте уведомления", "Message MemuAI from a messenger and get notifications"), value: messengersOn(ctx).length ? L(`Включено: ${messengersOn(ctx).join(", ")}`, `On: ${messengersOn(ctx).join(", ")}`) : L("Не подключено", "Not connected") },
      { page: "memucode", icon: ICONS.code, title: "MemuCode", desc: L("Окно для работы с кодом: разрешения агента, модель, редактор и терминал", "The coding window: agent permissions, model, editor and terminal"), value: codeLevelText() },
      { page: "profile", icon: ICONS.user, title: L("Профиль и память", "Profile and memory"), desc: L("Как к вам обращаться и что ассистент помнит", "How to address you and what the assistant remembers"), value: ctx.pref("profile_name", "") || "" },
      { page: has("sync") ? "sync" : "transfer", icon: ICONS.cloud, title: L("Резервные копии", "Backups"), desc: L("Копии чатов и настроек в Google Drive или папку", "Copies of chats and settings to Google Drive or a folder"), value: "" },
      { page: "docs", icon: ICONS.book, title: L("Справка", "Help"), desc: L("Подробное руководство по всем функциям", "The full guide to every feature"), value: "" },
    ];
    return rows.filter((row) => has(row.page)).map((row) => ({ ...row, pageTitle: pageTitle(row.page) }));
  }

  const POPULAR = [
    ["тёмная тема", "dark theme"], ["модель по умолчанию", "default model"], ["размер шрифта", "font size"],
    ["горячие клавиши", "shortcuts"], ["веб-поиск", "web search"], ["автозапуск", "startup"], ["экспорт чатов", "export chats"],
  ];

  const HIT_LIMIT = 7;

  function homeHtml(ctx) {
    const tileHtml = tiles(ctx).map((row) => `<button type="button" class="sxh-tile" data-home-go="${esc(row.page)}">
        <span class="sxh-tile-icon" aria-hidden="true">${row.icon}</span>
        <span class="sxh-tile-text"><b>${esc(row.title)}</b><small>${esc(row.desc)}</small>${row.value ? `<em>${esc(row.value)}</em>` : ""}</span>
        <span class="sxh-tile-go" aria-hidden="true">${ICONS.arrow}</span></button>`).join("");
    return `<div class="sxh">
      <section class="sxh-hero">
        <div class="sxh-hero-text"><h3 class="sx-page-title" tabindex="-1">${esc(L("Настройки", "Settings"))}</h3>
          <p>${esc(L("Здесь самое нужное. Всё остальное — в разделах слева или через поиск: напишите своими словами, что хотите поменять.", "The essentials are here. Everything else is in the sections on the left or in search: describe what you want to change in your own words."))}</p></div>
        <div class="sxh-find" data-sx-noindex>
          <label class="sxh-search">${ICONS.search}<input id="sxh-q" type="search" data-sx-search-here autocomplete="off" spellcheck="false" enterkeyhint="go"
            placeholder="${esc(L("Что вы хотите настроить?", "What do you want to change?"))}" aria-label="${esc(L("Поиск по настройкам", "Search settings"))}"
            role="combobox" aria-expanded="false" aria-controls="sxh-results" aria-autocomplete="list"><button type="button" class="sxh-clear" data-home-clear hidden aria-label="${esc(L("Очистить", "Clear"))}" title="${esc(L("Очистить — Esc", "Clear — Esc"))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17"/></svg></button><kbd>Ctrl F</kbd></label>
          <div class="sxh-results" id="sxh-results" role="listbox" aria-label="${esc(L("Результаты поиска", "Search results"))}" hidden></div>
          <div class="sxh-popular" role="list" aria-label="${esc(L("Частые запросы", "Popular searches"))}">${POPULAR.map(([ru, en]) => `<button type="button" role="listitem" class="sxh-chip" data-home-q="${esc(L(ru, en))}">${esc(L(ru, en))}</button>`).join("")}</div>
        </div>
      </section>
      <section class="sx-section" id="settings-home-common" data-sx-section>
        <header class="sx-section-head"><h3 class="sx-section-title"><span>${esc(L("Частые настройки", "Common settings"))}</span></h3></header>
        <div class="sxh-grid">${tileHtml}</div>
      </section>
      <section class="sx-section" id="settings-home-status" data-sx-section>
        <header class="sx-section-head"><h3 class="sx-section-title"><span>${esc(L("Состояние", "Status"))}</span></h3></header>
        <div class="sx-card sxh-status" id="sxh-status">${statusHtml(ctx)}</div>
      </section>
      <section class="sx-section" id="settings-home-recent" data-sx-section>
        <header class="sx-section-head"><h3 class="sx-section-title"><span>${esc(L("Недавно меняли", "Recently changed"))}</span></h3></header>
        <div class="sx-card sxh-recent" id="sxh-recent">${recentHtml(ctx)}</div>
      </section>
    </div>`;
  }

  function statusHtml(ctx) {
    const proxy = document.getElementById("hub-status")?.dataset.state || "";
    const online = proxy === "online" || proxy === "ok" || proxy === "connected";
    const models = (typeof S !== "undefined" && Array.isArray(S.models)) ? S.models.length : 0;
    const item = (icon, title, chip, tone, page) => `<button type="button" class="sxh-state" ${page ? `data-home-go="${esc(page)}"` : "disabled"}><span class="sxh-state-icon" aria-hidden="true">${icon}</span><b>${esc(title)}</b><span class="sx-chip" data-tone="${tone}" title="${esc(chip)}"><i aria-hidden="true"></i><span>${esc(chip)}</span></span></button>`;
    return [
      item(ICONS.shield, "MemuAI Proxy", online ? L("Подключён", "Connected") : proxy ? L("Нет связи", "Offline") : L("Проверяю…", "Checking…"), online ? "ok" : proxy ? "err" : "muted", "general"),
      item(ICONS.bot, L("Модели", "Models"), models ? L(`Доступно: ${models}`, `${models} available`) : L("Нет моделей", "No models"), models ? "ok" : "warn", "models"),
      (() => {
        const on = messengersOn(ctx);
        return item(ICONS.send, L("Мессенджеры", "Messengers"), on.length ? on.join(", ") : L("Выключены", "Off"), on.length ? "ok" : "muted", "remote");
      })(),
      item(ICONS.globe, L("Веб-поиск", "Web search"), ctx.pref("web_search", false) ? L("Всегда", "Always") : L("По кнопке", "On demand"), "muted", "search"),
    ].join("");
  }

  function recentHtml(ctx) {
    const rows = readRecent().filter((row) => ctx.pages.some((p) => p.id === row.page));
    if (!rows.length) return `<div class="sxh-empty">${ICONS.clock}<span>${esc(L("Здесь появятся настройки, которые вы меняли, — чтобы быстро к ним вернуться.", "Settings you change will show up here so you can get back to them quickly."))}</span></div>`;
    return rows.map((row, index) => {
      const page = ctx.pages.find((p) => p.id === row.page);
      return `<button type="button" class="sxh-recent-row" data-home-recent="${index}"><span class="sxh-recent-icon" aria-hidden="true">${page?.icon || ""}</span><span class="sxh-recent-text"><b>${esc(row.label)}</b><small>${esc(page?.title || row.page)} · ${esc(ago(row.t))}</small></span><span class="sxh-tile-go" aria-hidden="true">${ICONS.arrow}</span></button>`;
    }).join("");
  }

  function mount(ctx) {
    const pane = document.getElementById("pane-home");
    if (!pane) return;
    const recentBox = document.getElementById("sxh-recent");
    if (recentBox) recentBox.innerHTML = recentHtml(ctx);
    const refreshStatus = () => { const box = document.getElementById("sxh-status"); if (box?.isConnected) box.innerHTML = statusHtml(ctx); };
    refreshStatus();
    setTimeout(refreshStatus, 900);
    const field = document.getElementById("sxh-q");
    if (field?.value.trim()) heroSearch(ctx, field.value);
    if (pane.dataset.bound) return;
    pane.dataset.bound = "1";
    pane.addEventListener("click", (event) => {
      const go = event.target.closest("[data-home-go]");
      if (go) { ctx.openPage(go.dataset.homeGo); return; }
      const hit = event.target.closest("[data-home-hit]");
      if (hit) { const item = hero.items[+hit.dataset.homeHit]; clearHero(); item?.go(); return; }
      if (event.target.closest("[data-home-all]")) { const q = document.getElementById("sxh-q")?.value || ""; clearHero(); ctx.searchAll(q); return; }
      if (event.target.closest("[data-home-clear]")) { clearHero(); document.getElementById("sxh-q")?.focus(); return; }
      const query = event.target.closest("[data-home-q]");
      if (query) {
        const input = document.getElementById("sxh-q");
        if (input) { input.value = query.dataset.homeQ; heroSearch(ctx, input.value); input.focus(); }
        return;
      }
      const recent = event.target.closest("[data-home-recent]");
      if (recent) {
        const row = readRecent().filter((item) => ctx.pages.some((p) => p.id === item.page))[+recent.dataset.homeRecent];
        if (row) ctx.jump(row.page, row.label);
      }
    });
    pane.addEventListener("input", (event) => {
      if (event.target.id !== "sxh-q") return;
      const value = event.target.value;
      cancelAnimationFrame(hero.frame);
      hero.frame = requestAnimationFrame(() => heroSearch(ctx, value));
    });
    pane.addEventListener("keydown", (event) => {
      if (event.target.id !== "sxh-q") return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        if (!hero.items.length) return;
        event.preventDefault();
        setHeroActive(hero.active + (event.key === "ArrowDown" ? 1 : -1));
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        cancelAnimationFrame(hero.frame);
        if (!hero.items.length && event.target.value.trim()) heroSearch(ctx, event.target.value);
        const item = hero.items[hero.active];
        if (item) { clearHero(); item.go(); }
        return;
      }
      if (event.key === "Escape" && event.target.value) { event.preventDefault(); event.stopPropagation(); clearHero(); }
    });
  }

  /* ---------- search in place on the Home page ---------- */
  const hero = { items: [], active: 0, frame: 0 };
  function heroSearch(ctx, value) {
    const box = document.getElementById("sxh-results");
    const input = document.getElementById("sxh-q");
    const wrap = box?.closest(".sxh-find");
    if (!box || !input || !wrap) return;
    const query = String(value || "").trim();
    wrap.classList.toggle("is-searching", !!query);
    input.closest(".sxh-search")?.querySelector("[data-home-clear]")?.toggleAttribute("hidden", !query);
    const kit = window.MemuSettingsKit;
    if (!query || !kit?.find) {
      hero.items = []; hero.active = 0;
      box.hidden = true; box.innerHTML = "";
      input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant");
      return;
    }
    const found = kit.find(query, HIT_LIMIT);
    hero.items = found.items; hero.active = 0;
    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (!found.items.length) {
      box.innerHTML = `<div class="sxh-results-empty">${ICONS.search}<span><b>${esc(L("Ничего не нашлось", "Nothing found"))}</b>${esc(L("Попробуйте другое слово — например «тема», «голос», «ключ» или «Telegram».", "Try another word — for example “theme”, “voice”, “key” or “Telegram”."))}</span></div>`;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    const more = found.total > found.items.length;
    box.innerHTML = found.items.map((item, i) => `<button type="button" role="option" class="sx-result sxh-hit${i ? "" : " is-active"}" id="sxh-hit-${i}" data-home-hit="${i}" aria-selected="${i === 0}" tabindex="-1">
        <span class="sx-result-icon" aria-hidden="true">${item.icon}</span>
        <span class="sx-result-text"><span class="sx-result-path">${item.pathHtml}</span><b>${item.labelHtml}</b>${item.descHtml ? `<small>${item.descHtml}</small>` : ""}</span>
        <span class="sx-result-go" aria-hidden="true">${ICONS.arrow}</span></button>`).join("")
      + `<div class="sxh-results-foot"><span>${esc(L(`Найдено: ${found.total}`, `${found.total} result${found.total === 1 ? "" : "s"}`))}</span>${more ? `<button type="button" class="sxh-all" data-home-all>${esc(L("Показать все", "Show all"))}${ICONS.arrow}</button>` : ""}<span class="sxh-keys" aria-hidden="true"><kbd>↑</kbd><kbd>↓</kbd> ${esc(L("выбор", "select"))} · <kbd>Enter</kbd> ${esc(L("открыть", "open"))}</span></div>`;
    input.setAttribute("aria-activedescendant", "sxh-hit-0");
  }
  function setHeroActive(index) {
    if (!hero.items.length) return;
    hero.active = (index + hero.items.length) % hero.items.length;
    document.querySelectorAll("#sxh-results .sxh-hit").forEach((node, i) => {
      node.classList.toggle("is-active", i === hero.active);
      node.setAttribute("aria-selected", String(i === hero.active));
    });
    document.getElementById(`sxh-hit-${hero.active}`)?.scrollIntoView({ block: "nearest" });
    document.getElementById("sxh-q")?.setAttribute("aria-activedescendant", `sxh-hit-${hero.active}`);
  }
  function clearHero() {
    const input = document.getElementById("sxh-q");
    if (input) input.value = "";
    heroSearch(null, "");
  }

  /* ---------- MemuCode page ---------- */
  // MemuCode is the coding window of MemuAI. Every value on this page is a
  // MemuAI preference (code_*), read by app.js and memucode.js.
  Object.assign(ICONS, {
    edit: P('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
    ask: P('<circle cx="12" cy="12" r="9"/><path d="M9.7 9.4a2.4 2.4 0 1 1 3.3 2.2c-.6.3-1 .8-1 1.4v.5M12 16.7h.01"/>'),
    sparkle: P('<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/>'),
    plug: P('<path d="M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0zM12 16.5V21"/>'),
    users: P('<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19.5a5.5 5.5 0 0 1 11 0M15.5 5.2a3 3 0 0 1 0 5.6M17.6 19.5a5.4 5.4 0 0 0-2.3-4.4"/>'),
    keyboard: P('<rect x="3" y="6" width="18" height="12" rx="2.5"/><path d="M7 10h.01M11 10h.01M15 10h.01M7.5 14h9"/>'),
    trash: P('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 12.5A1.5 1.5 0 0 0 8.5 21h7a1.5 1.5 0 0 0 1.5-1.5L18 7M9 7V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V7"/>'),
    open: P('<path d="M14 4h6v6M20 4l-8.5 8.5M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>'),
  });
  const appFn = (name) => (typeof window[name] === "function" ? window[name] : null);
  const clampInt = (value, min, max, fallback) => { const n = Math.round(Number(value)); return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback; };
  const codeLevel = () => { const level = appFn("codeAccessLevel")?.(); return level === "edits" || level === "full" ? level : "ask"; };

  function codeLevels() {
    return [
      { id: "ask", icon: ICONS.shield, title: L("С подтверждением", "Ask first"), tag: L("Рекомендуется", "Recommended"), tone: "accent", edits: "ask", commands: "ask",
        text: L("Перед каждой правкой и командой в чате появляется карточка с diff или командой — вы решаете, выполнять ли.", "Before every edit and command, a card with the diff or the command appears in the chat — you decide whether it runs.") },
      { id: "edits", icon: ICONS.edit, title: L("Правки без вопросов", "Auto-edit"), tag: L("Быстрее, команды под контролем", "Faster, commands stay in check"), tone: "muted", edits: "yes", commands: "ask",
        text: L("Файлы проекта меняются сразу — откатить их можно во вкладке «Откат». Команды ждут вашего решения.", "Project files change at once — undo them in the Undo tab. Commands wait for your decision.") },
      { id: "full", icon: ICONS.zap, title: L("Полный доступ", "Full access"), tag: L("Осторожно", "Use with care"), tone: "danger", danger: true, edits: "yes", commands: "yes",
        text: L("Правки и команды выполняются без вопросов. Только для проектов, которым вы доверяете.", "Edits and commands run without asking. Only for projects you trust.") },
    ];
  }
  function levelsHtml() {
    const current = codeLevel();
    const cap = (state, label) => `<li data-cap="${state}"><span aria-hidden="true">${state === "yes" ? ICONS.check : ICONS.ask}</span>${esc(label)}<b>${esc(state === "yes" ? L("сразу", "at once") : L("с вопросом", "asks"))}</b></li>`;
    return `<div class="sxm-levels" role="radiogroup" aria-label="${esc(L("Разрешения агента MemuCode", "MemuCode agent permissions"))}">${codeLevels().map((level) => `
      <label class="sxm-level${level.danger ? " is-danger" : ""}">
        <input type="radio" name="sxm-access" value="${level.id}" ${level.id === current ? "checked" : ""}>
        <span class="sxm-level-head"><span class="sxm-level-icon" aria-hidden="true">${level.icon}</span><span class="sxm-level-name"><b>${esc(level.title)}</b><em data-tone="${level.tone}">${esc(level.tag)}</em></span></span>
        <small>${esc(level.text)}</small>
        <ul class="sxm-caps">${cap("yes", L("Чтение и поиск", "Reading and search"))}${cap(level.edits, L("Правки файлов", "File edits"))}${cap(level.commands, L("Команды", "Commands"))}</ul>
      </label>`).join("")}</div>`;
  }

  function codeModelOptions(ctx) {
    const value = ctx.pref("code_model", "");
    const listed = appFn("orderedVisibleModels")?.() || (typeof S !== "undefined" && Array.isArray(S.models) ? S.models : []);
    const skip = (id) => !!appFn("isImageModel")?.(id) || !!appFn("isMusicOnlyModel")?.(id);
    const ids = listed.map((m) => m && m.id).filter((id) => id && !skip(id));
    const chat = ctx.pref("default_model", "");
    const rows = [["", chat ? L(`Как в чатах · ${ctx.modelName(chat)}`, `Same as chats · ${ctx.modelName(chat)}`) : L("Как в чатах", "Same as chats")], ...ids.map((id) => [id, ctx.modelName(id)])];
    if (value && !ids.includes(value)) rows.push([value, L(`${value} (недоступна)`, `${value} (unavailable)`)]);
    return rows;
  }

  // A small sample diff that follows the font size, line wrapping and diff view.
  function previewHtml(ctx) {
    const size = clampInt(ctx.pref("code_font_size", 13), 11, 18, 13);
    const wrap = !!ctx.pref("code_wrap", false);
    const split = ctx.pref("code_diff_view", "unified") === "split";
    const kw = (word) => `<em>${word}</em>`;
    const head = `${kw("def")} total(items):`;
    const oldLine = `    ${kw("return")} sum(item.price ${kw("for")} item ${kw("in")} items)`;
    const newLine = `    ${kw("return")} sum(item.price * item.count ${kw("for")} item ${kw("in")} items ${kw("if")} item.active ${kw("and not")} item.archived)`;
    const row = (cls, a, b, sign, code) => `<div class="sxm-pv-row ${cls}"><i>${a}</i>${b === null ? "" : `<i>${b}</i>`}<s>${sign}</s><code>${code}</code></div>`;
    const body = split
      ? `<div class="sxm-pv-split"><div>${row("ctx", 1, null, "", head)}${row("del", 2, null, "−", oldLine)}</div><div>${row("ctx", 1, null, "", head)}${row("add", 2, null, "+", newLine)}</div></div>`
      : `${row("ctx", 1, 1, "", head)}${row("del", 2, "", "−", oldLine)}${row("add", "", 2, "+", newLine)}`;
    return `<div class="sxm-pv${wrap ? " is-wrap" : ""}${split ? " is-split" : ""}" id="sxm-preview" style="--sxm-fs:${size}px" aria-hidden="true">
      <div class="sxm-pv-head"><span class="sxm-pv-file">${ICONS.file}billing.py</span><b class="is-add">+1</b><b class="is-del">−1</b><small>${esc(L("Пример", "Preview"))}</small></div>
      <div class="sxm-pv-body">${body}</div></div>`;
  }
  function refreshPreview(ctx) {
    const node = document.getElementById("sxm-preview");
    if (node) node.outerHTML = previewHtml(ctx);
  }

  function historyText() {
    const count = Number(window.MemuCode?.terminalHistorySize?.() || 0);
    return count
      ? L(`Сохранено команд: ${count}. Листаются стрелками ↑ и ↓ в терминале и хранятся только на этом компьютере.`, `${count} saved command${count === 1 ? "" : "s"}. Browse them with ↑ and ↓ in the terminal; they stay on this computer.`)
      : L("Команды, которые вы вводите в терминале MemuCode, листаются стрелками ↑ и ↓. Сейчас история пуста.", "Commands you type in the MemuCode terminal can be browsed with ↑ and ↓. The history is empty now.");
  }

  function memucodeHtml(ctx) {
    const K = window.MemuSettingsKit;
    if (!K) return "";
    const toggle = (id, key, def) => K.toggle(id, !!ctx.pref(key, def));
    const action = (id, label, attrs = "") => `<button type="button" class="pbtn ghost sx-btn-sm" id="${id}" ${attrs}>${esc(label)}</button>`;
    const go = (act, iconSvg, label, desc, keywords, button = L("Открыть", "Open")) => `<div class="sx-row p-toggle sxm-go" data-sx-k="${esc(keywords)}"><span class="sxm-go-icon" aria-hidden="true">${iconSvg}</span>
        <div class="sx-row-text"><div class="sx-label tl">${esc(label)}</div><div class="sx-desc td">${esc(desc)}</div></div>
        <div class="sx-control"><button type="button" class="pbtn ghost sx-btn-sm" data-sxm-act="${act}">${esc(button)}</button></div></div>`;
    const rounds = clampInt(ctx.pref("code_tool_rounds", 20), 6, 50, 20);
    const font = clampInt(ctx.pref("code_font_size", 13), 11, 18, 13);
    const inCode = typeof S !== "undefined" && S.mode === "code";
    return `<div class="sxm">
      ${K.section({ id: "settings-memucode-access", title: L("Разрешения агента", "Agent permissions"), keywords: "memucode разрешения permissions доступ access подтверждение approval ask full полный",
        desc: esc(L("Что агент делает в проекте без вашего подтверждения. Чтение файлов и команды чтения — git status, git diff, ls — выполняются всегда.", "What the agent does in the project without your approval. Reading files and read-only commands — git status, git diff, ls — always run.")),
        body: K.row({ stack: true, cls: "sxm-levels-row", label: esc(L("Уровень доступа", "Access level")), key: "code_access_mode", def: "ask", keywords: "уровень level ask edits full правки команды commands",
            desc: esc(L("Сменить уровень можно и в самом окне — кнопкой разрешений над чатом.", "You can also switch it in the window itself with the permissions button above the chat.")), control: levelsHtml() })
          + `<div class="sx-row sx-row--note">${K.note(esc(L("На карточке подтверждения есть «Разрешить в этой сессии»: такие же действия до конца текущего чата MemuCode пройдут без вопросов. Если вместо ответа написать сообщение, действие отклоняется, а агент получает ваш текст.", "The approval card has “Allow for this session”: the same kind of action then runs without asking until the current MemuCode chat ends. Typing a message instead of answering rejects the action and passes your text to the agent.")))}</div>` })}
      ${K.section({ id: "settings-memucode-agent", title: L("Агент", "Agent"), reset: true, keywords: "memucode агент agent",
        body: K.row({ id: "sxm-model-row", label: esc(L("Модель", "Model")), key: "code_model", def: "", keywords: "model модель llm нейросеть",
            desc: esc(L("Модель для новых сессий MemuCode. В самой сессии её можно сменить в поле ввода.", "The model for new MemuCode sessions. You can switch it in the message box during a session.")),
            control: K.select("sxm-model", codeModelOptions(ctx), ctx.pref("code_model", ""), { width: 280 }) })
          + K.row({ label: esc(L("Сначала план", "Plan first")), key: "code_plan_mode", def: false, keywords: "plan план планирование",
            desc: esc(L("Перед изменениями агент присылает план и ждёт вашего согласия", "Before any change the agent sends a plan and waits for your go-ahead")), control: toggle("sxm-plan", "code_plan_mode", false) })
          + K.row({ label: esc(L("Проверять свою работу", "Verify its work")), key: "code_auto_verify", def: true, keywords: "verify проверка тесты tests lint линтер сборка build",
            desc: esc(L("После правок агент сам запускает тесты, линтер или сборку проекта и исправляет ошибки", "After editing, the agent runs the project's tests, linter or build itself and fixes what fails")), control: toggle("sxm-verify", "code_auto_verify", true) })
          + K.row({ label: esc(L("Правила проекта", "Project rules")), key: "code_project_rules", def: true, keywords: "agents.md claude.md правила rules инструкции instructions",
            desc: esc(L("Если в корне проекта есть AGENTS.md или CLAUDE.md, агент следует этим инструкциям", "When the project root has AGENTS.md or CLAUDE.md, the agent follows those instructions")), control: toggle("sxm-rules", "code_project_rules", true) })
          + K.row({ label: esc(L("Шагов на один запрос", "Steps per request")), key: "code_tool_rounds", def: 20, keywords: "steps шаги limit лимит tools инструменты rounds",
            desc: esc(L("Сколько раз подряд агент вызывает инструменты, прежде чем остановиться и спросить, продолжать ли", "How many tool calls in a row the agent makes before it stops and asks whether to go on")),
            control: K.slider("sxm-rounds", { min: 6, max: 50, step: 1, value: rounds }) }) })}
      ${K.section({ id: "settings-memucode-editor", title: L("Редактор и изменения", "Editor and changes"), reset: true, keywords: "memucode редактор editor diff изменения",
        body: `<div class="sx-row sxm-preview-row">${previewHtml(ctx)}</div>`
          + K.row({ label: esc(L("Размер шрифта кода", "Code font size")), key: "code_font_size", def: 13, keywords: "font шрифт size размер",
            desc: esc(L("Для файлов, изменений и терминала", "For files, changes and the terminal")), control: K.slider("sxm-font", { min: 11, max: 18, step: 1, value: font, unit: " px" }) })
          + K.row({ label: esc(L("Переносить длинные строки", "Wrap long lines")), key: "code_wrap", def: false, keywords: "wrap перенос строки lines",
            desc: esc(L("Вместо горизонтальной прокрутки строка переходит на следующую", "Long lines continue on the next line instead of scrolling sideways")), control: toggle("sxm-wrap", "code_wrap", false) })
          + K.row({ label: esc(L("Вид изменений", "Diff view")), key: "code_diff_view", def: "unified", keywords: "diff изменения рядом построчно split unified side",
            desc: esc(L("Как показывать правки во вкладке «Изменения»", "How edits are shown in the Changes tab")),
            control: K.segmented("sxm-diff", [["unified", L("Построчно", "Unified")], ["split", L("Рядом", "Side by side")]], ctx.pref("code_diff_view", "unified") === "split" ? "split" : "unified", { label: L("Вид изменений", "Diff view") }) })
          + K.row({ label: esc(L("Показывать правки агента", "Follow the agent's edits")), key: "code_follow_edits", def: true, keywords: "follow edits правки открывать open diff",
            desc: esc(L("Когда агент меняет файл, справа сразу открываются его изменения", "When the agent changes a file, its changes open on the right at once")), control: toggle("sxm-follow", "code_follow_edits", true) }) })}
      ${K.section({ id: "settings-memucode-terminal", title: L("Терминал и команды", "Terminal and commands"), keywords: "memucode терминал terminal команды commands sandbox песочница",
        body: K.row({ id: "sxm-history-row", label: esc(L("История команд", "Command history")), keywords: "history история команд commands terminal",
            desc: `<span id="sxm-history-text">${esc(historyText())}</span>`,
            control: action("sxm-history-clear", L("Очистить", "Clear"), Number(window.MemuCode?.terminalHistorySize?.() || 0) ? "" : "disabled") })
          + `<div class="sx-row sx-row--note">${K.note(esc(L("Команды — и агента, и ваши — выполняются в песочнице: в папке проекта, без ключей и токенов из окружения, с ограничением памяти. «Стоп» завершает всё дерево процессов. Опасные системные команды — форматирование диска, выключение, правка реестра — не запускаются ни при каком уровне доступа.", "Commands — the agent's and yours — run in a sandbox: in the project folder, without keys and tokens from the environment, with a memory limit. Stop ends the whole process tree. Dangerous system commands — formatting a disk, shutting down, editing the registry — never run at any access level.")))}</div>` })}
      ${K.section({ id: "settings-memucode-tools", title: L("Инструменты и навыки", "Tools and skills"), keywords: "memucode инструменты tools skills скиллы mcp agents агенты browser браузер",
        body: go("skills", ICONS.sparkle, L("Скиллы", "Skills"), L("Готовые инструкции для типовых задач. Скиллы проекта лежат в .memuai/skills, скиллы библиотеки включаются для MemuCode в окне «Скиллы»", "Ready-made playbooks for common tasks. Project skills live in .memuai/skills; library skills are enabled for MemuCode in the Skills window"), "skills скиллы навыки playbooks")
          + go("mcp", ICONS.plug, L("MCP-серверы", "MCP servers"), L("Инструменты подключённых MCP-серверов доступны агенту MemuCode", "Tools of connected MCP servers are available to the MemuCode agent"), "mcp серверы servers tools")
          + go("agents", ICONS.users, L("Агенты, браузер и управление компьютером", "Agents, browser and computer use"), L("Общие с Work: модель и число агентов, Browser и Computer Use", "Shared with Work: agent model and count, Browser and Computer Use"), "agents агенты browser браузер computer компьютер work")
          + (ctx.pages.some((p) => p.id === "hotkeys") ? go("hotkeys", ICONS.keyboard, L("Горячие клавиши", "Keyboard shortcuts"), L("Ctrl+P — файлы, команды и скиллы, Ctrl+` — терминал, Alt+1…7 — разделы правой панели", "Ctrl+P for files, commands and skills, Ctrl+` for the terminal, Alt+1…7 for the right-panel sections"), "hotkeys горячие клавиши shortcuts ctrl") : "")
          + (inCode ? "" : go("open", ICONS.code, L("Открыть MemuCode", "Open MemuCode"), L("Перейти в окно для работы с кодом", "Go to the coding window"), "open открыть окно window", L("Перейти", "Go"))) })}
    </div>`;
  }

  async function changeAccess(ctx, input) {
    const previous = codeLevel();
    const setLevel = appFn("setCodeAccessLevel");
    const ok = setLevel ? await setLevel(input.value) : (await ctx.savePref("code_access_mode", input.value), true);
    const now = ok ? codeLevel() : previous;
    document.querySelectorAll('#pane-memucode input[name="sxm-access"]').forEach((radio) => { radio.checked = radio.value === now; });
    window.MemuSettingsKit?.refreshModified?.(document.getElementById("pane-memucode"));
  }

  function changeModel(ctx, value) {
    ctx.savePref("code_model", value);
    if (typeof S !== "undefined" && S.mode === "code") appFn("applyDraftModel")?.();
  }

  function mountMemucode(ctx) {
    const pane = document.getElementById("pane-memucode");
    if (!pane) return;
    const history = document.getElementById("sxm-history-text");
    if (history) history.textContent = historyText();
    const clear = document.getElementById("sxm-history-clear");
    if (clear) clear.disabled = !Number(window.MemuCode?.terminalHistorySize?.() || 0);
    if (pane.dataset.bound) return;
    pane.dataset.bound = "1";
    pane.addEventListener("input", (event) => {
      const target = event.target;
      if (target.id === "sxm-font") document.getElementById("sxm-preview")?.style.setProperty("--sxm-fs", clampInt(target.value, 11, 18, 13) + "px");
    });
    pane.addEventListener("change", (event) => {
      const target = event.target;
      if (target.name === "sxm-access") { if (target.checked) changeAccess(ctx, target); return; }
      if (target.name === "sxm-diff") { if (target.checked) { ctx.savePref("code_diff_view", target.value === "split" ? "split" : "unified"); refreshPreview(ctx); window.MemuCode?.applyPrefs?.(); } return; }
      switch (target.id) {
        case "sxm-model": changeModel(ctx, target.value); break;
        case "sxm-plan":
          ctx.savePref("code_plan_mode", target.checked);
          if (typeof S !== "undefined" && S.mode === "code" && !S.messages.length) appFn("setWorkPlanMode")?.(target.checked);
          break;
        case "sxm-verify": ctx.savePref("code_auto_verify", target.checked); break;
        case "sxm-rules": ctx.savePref("code_project_rules", target.checked); break;
        case "sxm-rounds": ctx.savePref("code_tool_rounds", clampInt(target.value, 6, 50, 20)); break;
        case "sxm-font": ctx.savePref("code_font_size", clampInt(target.value, 11, 18, 13)); refreshPreview(ctx); window.MemuCode?.applyPrefs?.(); break;
        case "sxm-wrap": ctx.savePref("code_wrap", target.checked); refreshPreview(ctx); window.MemuCode?.applyPrefs?.(); break;
        case "sxm-follow": ctx.savePref("code_follow_edits", target.checked); break;
        default: break;
      }
    });
    pane.addEventListener("click", async (event) => {
      if (event.target.closest("#sxm-history-clear")) {
        window.MemuCode?.clearTerminalHistory?.();
        mountMemucode(ctx);
        ctx.toast(L("История команд очищена", "Command history cleared"));
        return;
      }
      const act = event.target.closest("[data-sxm-act]")?.dataset.sxmAct;
      if (!act) return;
      if (act === "agents") { ctx.openPage("work"); return; }
      if (act === "hotkeys") {
        ctx.openPage("hotkeys");
        setTimeout(() => document.querySelector('#pane-hotkeys [data-hk-group="code"]')?.scrollIntoView({ block: "start" }), 120);
        return;
      }
      if (act === "skills") { appFn("showSkillsModal")?.(); return; }
      if (act === "mcp") { appFn("showMcpPanel")?.(); return; }
      if (act === "open") { appFn("closePanel")?.(); await appFn("enterCodeMode")?.(); }
    });
  }

  const codeLevelText = () => (appFn("codeAccessLabel") ? window.codeAccessLabel(codeLevel()) : "");
  window.MemuSettingsHome = { homeHtml, mount, track, remember, recent: readRecent, memucodeHtml, mountMemucode, codeLevelText, icons: ICONS };
})();
