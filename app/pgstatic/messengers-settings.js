/* MemuAI — Settings → Messengers: Telegram | Discord | WhatsApp tabs.
   Telegram keeps its own panel (telegram-settings.js, tgapp-settings.js); this file adds the
   Discord and WhatsApp panels. The bots run in MemuAI Proxy (discord_bot.py, whatsapp_bot.py);
   the window reads their state through /api/pg/messengers/*. */
(function () {
  "use strict";
  const PLANE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.6 3.2 2.9 10.4c-1.3.5-1.3 1.3-.2 1.6l4.8 1.5 1.8 5.6c.2.6.1.9.8.9.5 0 .7-.2 1-.5l2.4-2.3 4.9 3.6c.9.5 1.6.2 1.8-.8l3.2-15.2c.3-1.3-.5-1.9-1.8-1.6z"/></svg>';
  const DISCORD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M20.3 4.4A19.6 19.6 0 0 0 15.4 3l-.6 1.2a18.2 18.2 0 0 0-5.5 0L8.6 3a19.6 19.6 0 0 0-4.9 1.4C.6 9-.3 13.6.1 18.1a19.8 19.8 0 0 0 6 3l1.3-2a12.8 12.8 0 0 1-1.9-.9l.4-.3a14.1 14.1 0 0 0 12.1 0l.4.3c-.6.4-1.2.7-1.9.9l1.3 2a19.7 19.7 0 0 0 6-3c.5-5.2-.9-9.7-3.5-13.7zM8 15.3c-1.2 0-2.2-1.1-2.2-2.4S6.8 10.5 8 10.5s2.2 1.1 2.2 2.4-1 2.4-2.2 2.4zm8 0c-1.2 0-2.2-1.1-2.2-2.4s1-2.4 2.2-2.4 2.2 1.1 2.2 2.4-1 2.4-2.2 2.4z"/></svg>';
  const WHATSAPP = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round" d="M4.4 19.6l1.2-3.7a8.1 8.1 0 1 1 2.9 2.8z"/><path fill="currentColor" d="M9.4 8.1c.2-.4.4-.4.7-.4h.5c.2 0 .4.1.5.4l.7 1.6c.1.2 0 .5-.1.6l-.5.6c.6 1.2 1.5 2.1 2.7 2.7l.6-.5c.2-.2.4-.2.6-.1l1.6.7c.3.1.4.3.4.5v.5c0 .3 0 .5-.4.7-.5.3-1.3.5-2.1.3-2.4-.6-4.6-2.8-5.2-5.2-.2-.8 0-1.6.4-2.1z"/></svg>';
  const EYE = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  const COPY = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M16 8V6.5A2.5 2.5 0 0 0 13.5 4h-7A2.5 2.5 0 0 0 4 6.5v7A2.5 2.5 0 0 0 6.5 16H8" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  const LINK = '<svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const TABS = ["telegram", "discord", "whatsapp"];
  const TAB_KEY = "memu.messenger.tab";
  const COMMANDS = ["menu", "new", "chats", "model", "settings", "temp", "regen", "stop", "status", "help"];
  const PHASES = {
    loading: ["Проверяю…", "Checking…"],
    disabled: ["Выключено", "Off"],
    no_token: ["Нужен токен бота", "Bot token needed"],
    no_config: ["Заполните подключение", "Fill in the connection"],
    bad_token: ["Токен отклонён", "Token rejected"],
    offline: ["MemuAI Proxy не запущен", "MemuAI Proxy is not running"],
    starting: ["Запускается…", "Starting…"],
    reconnecting: ["Переподключаюсь…", "Reconnecting…"],
    waiting: ["Ждёт сообщений от Meta", "Waiting for Meta"],
    unpaired: ["Онлайн · ждёт привязки", "Online · waiting to pair"],
    ready: ["Подключено", "Connected"],
    error: ["Ошибка", "Error"],
  };
  const state = { ctx: null, status: null, timer: 0, countdown: 0, codes: {}, refreshSoon: 0 };
  const T = (ru, en) => (state.ctx && state.ctx.en ? en : ru);
  const $ = (id) => document.getElementById(id);
  const esc = (value) => (state.ctx && state.ctx.esc ? state.ctx.esc(value) : String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));

  function current() {
    try { const value = localStorage.getItem(TAB_KEY); return TABS.includes(value) ? value : "telegram"; } catch (e) { return "telegram"; }
  }

  function applyTab() {
    const tab = current();
    document.querySelectorAll("#msg-tabs [data-msg-tab]").forEach((button) => {
      const on = button.dataset.msgTab === tab;
      button.classList.toggle("active", on);
      button.setAttribute("aria-selected", on ? "true" : "false");
      button.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll("#pane-remote .msg-panel").forEach((panel) => { panel.hidden = panel.dataset.messenger !== tab; });
  }

  function select(tab) {
    if (!TABS.includes(tab)) return;
    try { localStorage.setItem(TAB_KEY, tab); } catch (e) { /* private mode */ }
    applyTab();
    if (tab !== "telegram") refresh();
  }

  function toggle(id, checked, pref) {
    return `<label class="switch"><input type="checkbox" id="${id}" data-pref="${pref}" ${checked ? "checked" : ""}><span class="sl"></span></label>`;
  }
  function row(title, hint, control) {
    return `<div class="tg-row"><div class="tg-row-text"><div class="tl">${title}</div>${hint ? `<div class="td">${hint}</div>` : ""}</div><div class="tg-row-control">${control}</div></div>`;
  }
  function selectBox(id, pref, options, value, extra = "") {
    return `<select class="pinput compact" id="${id}" data-pref="${pref}" ${extra}>${options.map(([v, label]) =>
      `<option value="${esc(v)}" ${String(value) === String(v) ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  }
  function secret(id, pref, value, placeholder, label) {
    return `<div class="tg-token-line"><input type="password" class="pinput" id="${id}" data-pref="${pref}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off" spellcheck="false" aria-label="${esc(label)}">
      <button type="button" class="pbtn ghost tg-eye" data-eye="${id}" aria-label="${T("Показать", "Show")}" title="${T("Показать", "Show")}">${EYE}</button></div>`;
  }
  function field(id, label, control) {
    return `<div class="msg-field"><label class="field-label" for="${id}">${label}</label>${control}</div>`;
  }
  function step(num, title, hint, body) {
    return `<div class="tg-step"><span class="tg-step-num">${num}</span><div class="tg-step-body"><div class="tl">${title}</div>${hint ? `<div class="td">${hint}</div>` : ""}${body || ""}</div></div>`;
  }
  function hero(id, mark, title, sub) {
    return `<header class="tg-hero"><span class="tg-mark msg-mark-${id}">${mark}</span><div class="tg-hero-text"><div class="tg-title">${title}</div><div class="tg-sub">${sub}</div></div>
      <span class="tg-pill" id="${id}-pill" data-state="loading" role="status" aria-live="polite"><i></i><span>${T("Проверяю…", "Checking…")}</span></span></header>
      <div class="tg-health" id="${id}-health" hidden></div>`;
  }
  function replies(ctx, platform) {
    const models = (ctx.models || []).map((m) => [m.id, esc(ctx.modelName ? ctx.modelName(m.id) : m.id)]);
    return `<div class="tg-card"><div class="tg-card-title">${T("Ответы", "Replies")}</div>
      ${row(T("Модель по умолчанию", "Default model"), T("В боте можно сменить командой /model", "Can be changed in the bot with /model"),
        selectBox(`st-${platform}-model`, `${platform}_model`, [["", T("Основная модель MemuAI", "Main MemuAI model")], ...models], ctx.pref(`${platform}_model`, ""), 'style="max-width:260px"'))}
      ${row(T("Язык бота", "Bot language"), T("Меню, кнопки и подсказки", "Menus, buttons and hints"),
        selectBox(`st-${platform}-language`, `${platform}_language`, [["ru", "Русский"], ["en", "English"]], ctx.pref(`${platform}_language`, "ru")))}
      <div class="msg-cmds" aria-label="${T("Команды", "Commands")}">${COMMANDS.map((c) => `<code>/${c}</code>`).join("")}</div>
      <div class="td tg-note">${T("Те же действия доступны кнопками под ответами: новый чат, ещё раз, меню. Ответы сохраняются в обычные чаты MemuAI.", "The same actions are buttons under the answers: new chat, regenerate, menu. Answers are saved into your ordinary MemuAI chats.")}</div></div>`;
  }

  function discordHtml(ctx) {
    const pref = ctx.pref;
    return `<section class="tg-settings msg-settings" id="dc-settings" aria-label="Discord">
      ${hero("dc", DISCORD, "Discord", T("Личный бот MemuAI в Discord: чаты, модели и настройки в личных сообщениях — с кнопками, меню и слэш-командами.", "Your private MemuAI bot in Discord: chats, models and settings in direct messages — with buttons, menus and slash commands."))}
      <div class="tg-card tg-connect">
        ${step(1, T("Токен бота", "Bot token"), T('Откройте <a href="https://discord.com/developers/applications" target="_blank" rel="noopener">Discord Developer Portal</a> → New Application → Bot → Reset Token и вставьте токен. Особые разрешения и Message Content Intent не нужны: бот работает в личных сообщениях.', 'Open the <a href="https://discord.com/developers/applications" target="_blank" rel="noopener">Discord Developer Portal</a> → New Application → Bot → Reset Token and paste the token. No special permissions or Message Content Intent are needed: the bot works in direct messages.'),
          `${secret("st-dc-token", "discord_bot_token", pref("discord_bot_token", ""), "MTE…", T("Токен бота Discord", "Discord bot token"))}<div class="tg-bot-line" id="dc-bot-line"></div>`)}
        ${step(2, T("Добавьте бота к себе", "Add the bot to yourself"), T("Чтобы написать боту, добавьте его в свой аккаунт (в портале: Installation → User Install) или на свой сервер — ссылки появятся после проверки токена.", "To message the bot, add it to your account (portal: Installation → User Install) or to a server of yours — the links appear after the token check."),
          `<div class="msg-invite" id="dc-invite"></div>`)}
        ${step(3, T("Привязка", "Pairing"), T("Получите код и отправьте его боту в личные сообщения: <code>/pair КОД</code>. После этого бот отвечает только вам.", "Get a code and send it to the bot in a direct message: <code>/pair CODE</code>. After that the bot answers only you."),
          `<div class="tg-pair-area" id="dc-pair-area"></div>`)}
        ${row(T("Бот Discord", "Discord bot"), T("Работает, пока запущен MemuAI Proxy", "Runs while MemuAI Proxy is running"), toggle("st-dc-enabled", pref("discord_enabled", false), "discord_enabled"))}
        <details class="tg-advanced"><summary>${T("Дополнительно: доступ", "Advanced: access")}</summary>
          ${field("st-dc-allowed", T("Дополнительные пользователи (Discord user id через запятую)", "Additional users (Discord user ids, comma separated)"),
            `<input class="pinput" id="st-dc-allowed" data-pref="discord_allowed_users" data-list="1" value="${esc((pref("discord_allowed_users", []) || []).join(", "))}" placeholder="123456789012345678">`)}
          <div class="td">${T("ID пользователя: Discord → Настройки → Расширенные → Режим разработчика, затем правый клик по профилю → «Копировать ID».", "A user id: Discord → Settings → Advanced → Developer Mode, then right-click a profile → Copy User ID.")}</div>
        </details>
      </div>
      ${replies(ctx, "discord")}
      <div class="tg-actions"><button type="button" class="pbtn ghost" id="dc-test">${T("Проверить подключение", "Test connection")}</button><span class="sub" id="dc-status" role="status" aria-live="polite"></span></div>
    </section>`;
  }

  function whatsappHtml(ctx) {
    const pref = ctx.pref;
    const mode = pref("whatsapp_mode", "cloud") === "waha" ? "waha" : "cloud";
    const seg = [["cloud", T("WhatsApp Cloud API", "WhatsApp Cloud API"), T("официально, номер Meta", "official, a Meta number")],
      ["waha", "WAHA", T("на этом компьютере", "on this computer")]];
    return `<section class="tg-settings msg-settings" id="wa-settings" aria-label="WhatsApp">
      ${hero("wa", WHATSAPP, "WhatsApp", T("Пишите MemuAI в WhatsApp: чаты, модели, поиск и личности — списками и кнопками прямо в переписке.", "Message MemuAI in WhatsApp: chats, models, search and personalities — as lists and buttons right in the conversation."))}
      <div class="tg-card tg-connect">
        <div class="msg-seg-wrap"><div class="msg-seg" id="wa-mode" role="radiogroup" aria-label="${T("Способ подключения", "How to connect")}">
          ${seg.map(([id, title, sub]) => `<button type="button" role="radio" data-wa-mode-btn="${id}" aria-checked="${id === mode}" class="${id === mode ? "active" : ""}"><b>${title}</b><small>${sub}</small></button>`).join("")}
        </div></div>
        <div class="msg-mode" data-wa-mode="cloud" ${mode === "cloud" ? "" : "hidden"}>
          ${step(1, T("Номер в Meta", "A number in Meta"), T('В <a href="https://developers.facebook.com/apps" target="_blank" rel="noopener">Meta for Developers</a> создайте приложение Business → WhatsApp → API Setup. Скопируйте постоянный токен (System User), Phone number ID и App secret (App settings → Basic).', 'In <a href="https://developers.facebook.com/apps" target="_blank" rel="noopener">Meta for Developers</a> create a Business app → WhatsApp → API Setup. Copy a permanent token (System User), the Phone number ID and the App secret (App settings → Basic).'),
            field("st-wa-token", T("Токен доступа", "Access token"), secret("st-wa-token", "whatsapp_access_token", pref("whatsapp_access_token", ""), "EAAG…", T("Токен доступа", "Access token")))
            + field("st-wa-phone", "Phone number ID", `<input class="pinput" id="st-wa-phone" data-pref="whatsapp_phone_id" value="${esc(pref("whatsapp_phone_id", ""))}" placeholder="123456789012345" inputmode="numeric" autocomplete="off">`)
            + field("st-wa-secret", "App secret", secret("st-wa-secret", "whatsapp_app_secret", pref("whatsapp_app_secret", ""), "a1b2c3…", "App secret")))}
          ${step(2, "Webhook", T("MemuAI Proxy принимает сообщения по адресу ниже. Откройте его в интернет по HTTPS (например, <code>cloudflared tunnel --url http://127.0.0.1:ПОРТ</code>) и в WhatsApp → Configuration укажите Callback URL <code>https://ваш-адрес/messengers/whatsapp/webhook</code>, Verify token и подписку <code>messages</code>. Запросы без подписи App secret отклоняются.", "MemuAI Proxy receives messages at the address below. Expose it over HTTPS (for example <code>cloudflared tunnel --url http://127.0.0.1:PORT</code>) and in WhatsApp → Configuration set the Callback URL <code>https://your-address/messengers/whatsapp/webhook</code>, the Verify token and the <code>messages</code> subscription. Requests without the App secret signature are refused."),
            `<div class="msg-copy-line"><code id="wa-webhook">/messengers/whatsapp/webhook</code><button type="button" class="pbtn ghost" data-copy="wa-webhook" title="${T("Копировать", "Copy")}" aria-label="${T("Копировать адрес", "Copy the address")}">${COPY}</button></div>
            <div class="msg-copy-line"><input class="pinput" id="st-wa-verify" value="${esc(pref("whatsapp_verify_token", ""))}" readonly placeholder="${T("Verify token ещё не создан", "No verify token yet")}" aria-label="Verify token">
              <button type="button" class="pbtn ghost" data-copy="st-wa-verify" title="${T("Копировать", "Copy")}" aria-label="${T("Копировать Verify token", "Copy the verify token")}">${COPY}</button>
              <button type="button" class="pbtn ghost" id="wa-verify-new">${pref("whatsapp_verify_token", "") ? T("Новый", "New") : T("Создать", "Create")}</button></div>`)}
          ${step(3, T("Привязка", "Pairing"), T("Получите код и отправьте его на номер бота со своего WhatsApp. После этого бот отвечает только вам.", "Get a code and send it to the bot's number from your WhatsApp. After that the bot answers only you."),
            `<div class="tg-pair-area" id="wa-pair-area"></div>`)}
          <details class="tg-advanced"><summary>${T("Дополнительно: доступ", "Advanced: access")}</summary>
            ${field("st-wa-allowed", T("Дополнительные номера (в международном формате, через запятую)", "Additional numbers (international format, comma separated)"),
              `<input class="pinput" id="st-wa-allowed" data-pref="whatsapp_allowed_users" data-list="1" value="${esc((pref("whatsapp_allowed_users", []) || []).join(", "))}" placeholder="79991234567">`)}
          </details>
        </div>
        <div class="msg-mode" data-wa-mode="waha" ${mode === "waha" ? "" : "hidden"}>
          ${step(1, T("Запустите WAHA", "Start WAHA"), T('WAHA — WhatsApp HTTP API на вашем компьютере: <code>docker run -it -p 3000:3000 devlikeapro/waha</code>. Откройте <code>http://localhost:3000/dashboard</code>, запустите сессию и отсканируйте QR в WhatsApp → Связанные устройства. Наружу ничего не открывается.', 'WAHA is a WhatsApp HTTP API on your computer: <code>docker run -it -p 3000:3000 devlikeapro/waha</code>. Open <code>http://localhost:3000/dashboard</code>, start a session and scan the QR in WhatsApp → Linked devices. Nothing is exposed to the internet.'),
            field("st-wa-waha-url", T("Адрес WAHA", "WAHA address"), `<input class="pinput" id="st-wa-waha-url" data-pref="whatsapp_waha_url" value="${esc(pref("whatsapp_waha_url", "http://127.0.0.1:3000"))}" placeholder="http://127.0.0.1:3000" autocomplete="off" spellcheck="false">`)
            + field("st-wa-waha-key", T("API-ключ (если задан в WAHA)", "API key (if set in WAHA)"), secret("st-wa-waha-key", "whatsapp_waha_api_key", pref("whatsapp_waha_api_key", ""), "WAHA_API_KEY", T("API-ключ WAHA", "WAHA API key")))
            + field("st-wa-waha-session", T("Сессия", "Session"), `<input class="pinput" id="st-wa-waha-session" data-pref="whatsapp_waha_session" value="${esc(pref("whatsapp_waha_session", "default"))}" placeholder="default" autocomplete="off" spellcheck="false">`))}
          ${step(2, T("Чат для MemuAI", "The chat for MemuAI"), T("Номер, в переписке с которым отвечает MemuAI: ваш собственный (чат с самим собой) или ваш личный номер, если WAHA подключён к отдельному номеру-боту. Другие чаты MemuAI не читает.", "The number whose conversation MemuAI answers: your own (the chat with yourself) or your personal number when WAHA runs on a separate bot number. MemuAI does not read other chats."),
            field("st-wa-waha-chat", T("Номер в международном формате", "Number in international format"), `<input class="pinput" id="st-wa-waha-chat" data-pref="whatsapp_waha_chat" value="${esc(pref("whatsapp_waha_chat", ""))}" placeholder="79991234567" autocomplete="off" inputmode="tel">`))}
        </div>
        ${row(T("Бот WhatsApp", "WhatsApp bot"), T("Работает, пока запущен MemuAI Proxy", "Runs while MemuAI Proxy is running"), toggle("st-wa-enabled", pref("whatsapp_enabled", false), "whatsapp_enabled"))}
      </div>
      ${replies(ctx, "whatsapp")}
      <div class="tg-actions"><button type="button" class="pbtn ghost" id="wa-test">${T("Проверить подключение", "Test connection")}</button><span class="sub" id="wa-status" role="status" aria-live="polite"></span></div>
    </section>`;
  }

  function tabs(ctx) {
    state.ctx = ctx;
    const tab = current();
    const items = [["telegram", "Telegram", PLANE], ["discord", "Discord", DISCORD], ["whatsapp", "WhatsApp", WHATSAPP]];
    return `<div class="msg-tabs" id="msg-tabs" role="tablist" aria-label="${T("Мессенджеры", "Messengers")}">${items.map(([id, label, icon]) =>
      `<button type="button" role="tab" class="msg-tab${id === tab ? " active" : ""}" id="msg-tab-${id}" data-msg-tab="${id}" aria-selected="${id === tab}" aria-controls="msg-panel-${id}" tabindex="${id === tab ? 0 : -1}"><span class="msg-tab-ic msg-ic-${id}">${icon}</span><span>${label}</span><i class="msg-tab-dot" data-dot="${id}" aria-hidden="true"></i></button>`).join("")}</div>`;
  }

  function panel(id, inner) {
    return `<div class="msg-panel" id="msg-panel-${id}" data-messenger="${id}" role="tabpanel" aria-labelledby="msg-tab-${id}" ${current() === id ? "" : "hidden"}>${inner}</div>`;
  }

  /** The whole Messengers page: the tab strip, the Telegram panel (given) and the Discord / WhatsApp panels. */
  function wrap(ctx, telegramHtml) {
    state.ctx = ctx;
    return tabs(ctx) + panel("telegram", telegramHtml) + panel("discord", discordHtml(ctx)) + panel("whatsapp", whatsappHtml(ctx));
  }

  // ---------------------------------------------------------------- status

  function phaseOf(platform, row, proxy) {
    const ctx = state.ctx;
    if (!row) return "loading";
    if (!row.enabled) return "disabled";
    if (platform === "discord" && !String(ctx.pref("discord_bot_token", "")).trim()) return "no_token";
    if (platform === "whatsapp") {
      const waha = ctx.pref("whatsapp_mode", "cloud") === "waha";
      const ready = waha ? String(ctx.pref("whatsapp_waha_chat", "")).trim()
        : ctx.pref("whatsapp_access_token", "") && ctx.pref("whatsapp_phone_id", "") && ctx.pref("whatsapp_app_secret", "");
      if (!ready) return "no_config";
    }
    if (!row.alive) return proxy === false ? "offline" : "starting";
    if (row.status === "unauthorized") return "bad_token";
    if (row.status === "error") return "error";
    if (row.status === "reconnecting" || row.status === "connecting") return "reconnecting";
    if (row.status === "waiting" && !(row.owner && row.owner.id)) return "unpaired";
    if (row.status === "waiting") return "waiting";
    return row.owner && row.owner.id ? "ready" : "unpaired";
  }

  function pillState(phase) {
    return { disabled: "disabled", no_token: "no_token", no_config: "no_token", bad_token: "bad_token", offline: "offline", starting: "reconnecting",
      reconnecting: "reconnecting", waiting: "unpaired", unpaired: "unpaired", ready: "ready", error: "stalled", loading: "loading" }[phase] || "loading";
  }

  function render() {
    const ctx = state.ctx, s = state.status;
    if (!ctx || !$("dc-settings")) return false;
    [["dc", "discord"], ["wa", "whatsapp"]].forEach(([short, platform]) => {
      const row = s && s[platform];
      const phase = phaseOf(platform, row, s && s.proxy_online);
      const pill = $(`${short}-pill`);
      if (pill) {
        pill.dataset.state = pillState(phase);
        const name = row && row.bot && row.bot.name ? ` · ${row.bot.name}` : "";
        pill.querySelector("span").textContent = (PHASES[phase] || PHASES.loading)[ctx.en ? 1 : 0] + (phase === "ready" && platform === "discord" ? name : "");
      }
      const dot = document.querySelector(`#msg-tabs [data-dot="${platform}"]`);
      if (dot) dot.dataset.state = phase === "ready" ? "on" : ["bad_token", "error", "offline"].includes(phase) ? "err" : ["unpaired", "starting", "reconnecting", "waiting", "no_token", "no_config"].includes(phase) ? "warn" : "";
      const health = $(`${short}-health`);
      if (health) {
        const text = healthText(platform, phase, row);
        health.hidden = !text;
        health.dataset.tone = ["bad_token", "error", "offline"].includes(phase) ? "err" : "warn";
        health.innerHTML = text ? `<span>${text}</span>` : "";
      }
    });
    const tgDot = document.querySelector('#msg-tabs [data-dot="telegram"]');
    if (tgDot) tgDot.dataset.state = ctx.pref("telegram_enabled", false) ? "on" : "";
    renderDiscordBot();
    renderPair("discord");
    renderPair("whatsapp");
    const hook = $("wa-webhook");
    const local = s && s.whatsapp && s.whatsapp.local_webhook;
    if (hook && local && hook.textContent !== local) hook.textContent = local;
    return true;
  }

  function codeText(code) {
    const ctx = state.ctx;
    const url = esc(ctx.pref("whatsapp_waha_url", "http://127.0.0.1:3000"));
    const session = esc(ctx.pref("whatsapp_waha_session", "default"));
    return {
      waha_unreachable: T(`WAHA не отвечает по адресу ${url}. Запустите его: <code>docker run -it -p 3000:3000 devlikeapro/waha</code> — бот подключится сам.`, `WAHA is not answering at ${url}. Start it: <code>docker run -it -p 3000:3000 devlikeapro/waha</code> — the bot connects by itself.`),
      waha_auth: T("WAHA отклонил API-ключ — проверьте поле «API-ключ».", "WAHA rejected the API key — check the API key field."),
      waha_session: T(`В WAHA нет запущенной сессии «${session}». Откройте панель WAHA, запустите сессию и отсканируйте QR.`, `WAHA has no running session “${session}”. Open the WAHA dashboard, start the session and scan the QR.`),
      network: T("Нет связи с Discord — проверьте интернет или прокси. Бот переподключится сам.", "No connection to Discord — check the internet or the proxy. The bot reconnects by itself."),
      intents: T("Discord не дал боту нужные права. Проверьте приложение в Developer Portal.", "Discord did not grant the bot the access it needs. Check the app in the Developer Portal."),
    }[code] || "";
  }

  function healthText(platform, phase, row) {
    const error = row && row.error ? esc(row.error) : "";
    const known = row && row.code ? codeText(row.code) : "";
    if (known && ["reconnecting", "error"].includes(phase)) return known;
    switch (phase) {
      case "offline": return T("Боты работают внутри MemuAI Proxy — запустите его, и бот подключится сам.", "The bots run inside MemuAI Proxy — start it and the bot connects by itself.");
      case "bad_token": return platform === "discord"
        ? T("Discord отклонил токен. Сбросьте его в Developer Portal (Bot → Reset Token) и вставьте новый — бот подхватит его сам.", "Discord rejected the token. Reset it in the Developer Portal (Bot → Reset Token) and paste the new one — the bot picks it up by itself.")
        : T("WhatsApp отклонил данные подключения.", "WhatsApp rejected the connection details.");
      case "error": return error || T("Бот остановился с ошибкой.", "The bot stopped with an error.");
      case "reconnecting": return error ? `${T("Нет связи", "No connection")}: ${error}` : "";
      case "no_config": return ctx_waha() ? T("Укажите номер чата, в котором MemuAI будет отвечать.", "Enter the number of the chat MemuAI should answer in.")
        : T("Заполните токен доступа, Phone number ID и App secret.", "Fill in the access token, Phone number ID and App secret.");
      case "waiting": return T("Бот готов. Если сообщения не приходят — проверьте туннель HTTPS и подписку webhook на messages.", "The bot is ready. If messages do not arrive, check the HTTPS tunnel and the webhook subscription to messages.");
      default: return "";
    }
  }
  const ctx_waha = () => state.ctx && state.ctx.pref("whatsapp_mode", "cloud") === "waha";

  function renderDiscordBot() {
    const s = state.status && state.status.discord;
    const line = $("dc-bot-line"), invite = $("dc-invite");
    const bot = (s && s.bot) || state.tested || {};
    if (line) {
      if (bot.name) { line.textContent = `${T("Бот", "Bot")}: ${bot.name}`; line.dataset.state = "ok"; }
      else if (s && s.status === "unauthorized") { line.textContent = T("Discord отклонил этот токен.", "Discord rejected this token."); line.dataset.state = "err"; }
      else { line.textContent = ""; line.dataset.state = ""; }
    }
    if (invite) {
      const links = [[bot.user, T("Добавить в мой аккаунт", "Add to my account")], [bot.server, T("Добавить на сервер", "Add to a server")], [bot.profile, T("Открыть бота", "Open the bot")]]
        .filter(([href]) => /^https:\/\/discord\.com\//.test(String(href || "")));
      const html = links.length ? links.map(([href, label]) => `<a class="pbtn ghost msg-link" href="${esc(href)}" target="_blank" rel="noopener">${LINK}<span>${label}</span></a>`).join("")
        : `<span class="td">${T("Сначала проверьте токен.", "Check the token first.")}</span>`;
      if (invite.dataset.html !== html) { invite.innerHTML = html; invite.dataset.html = html; }
    }
  }

  function renderPair(platform) {
    const area = $(platform === "discord" ? "dc-pair-area" : "wa-pair-area");
    if (!area) return;
    const row = state.status && state.status[platform];
    const owner = row && row.owner;
    const code = state.codes[platform] && state.codes[platform].expires * 1000 > Date.now() ? state.codes[platform] : (row && row.pairing) || null;
    let html;
    if (owner && owner.id) {
      const initial = esc(String(owner.name || owner.id || "?").trim().charAt(0).toUpperCase() || "?");
      html = `<div class="tg-owner"><span class="tg-avatar msg-avatar-${platform}">${initial}</span><div class="tg-owner-text"><div class="tl">${esc(owner.name || owner.id)}</div><div class="td">${T("Привязан", "Paired")} · ${esc(owner.id)}</div></div>
        <button type="button" class="pbtn ghost danger" data-unpair="${platform}">${T("Отвязать", "Unpair")}</button></div>`;
    } else if (code && code.code) {
      const left = Math.max(0, Math.round(code.expires - Date.now() / 1000));
      const command = platform === "discord" ? `/pair ${code.code}` : code.code;
      html = `<div class="tg-pair"><div class="tg-pair-info"><div class="tg-code" data-code="${esc(command)}">${esc(code.code)}</div>
        <div class="td">${platform === "discord" ? T("Отправьте боту в личные сообщения", "Send the bot in a direct message") : T("Отправьте на номер бота", "Send to the bot's number")}: <code>${esc(command)}</code> · <span class="tg-countdown">${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}</span></div>
        <div class="tg-pair-buttons"><button type="button" class="pbtn ghost" data-copy-text="${esc(command)}">${COPY}<span>${T("Копировать", "Copy")}</span></button><button type="button" class="pbtn ghost" data-pair="${platform}">${T("Новый код", "New code")}</button></div></div></div>`;
    } else {
      html = `<button type="button" class="pbtn tg-pair-btn" data-pair="${platform}">${T("Получить код привязки", "Get a pairing code")}</button>`;
    }
    if (area.dataset.html !== html) { area.innerHTML = html; area.dataset.html = html; }
  }

  async function refresh() {
    const ctx = state.ctx;
    if (!ctx || !$("dc-settings")) return;
    try {
      state.status = await ctx.api("/api/pg/messengers/status");
    } catch (e) {
      state.status = state.status || null;
    }
    render();
    schedule();
  }

  function schedule() {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      const pane = $("pane-remote");
      if (!pane || !pane.isConnected || !$("dc-settings")) return;
      if (document.hidden || !pane.classList.contains("active") || current() === "telegram") { schedule(); return; }
      refresh();
    }, 4000);
  }

  function stop() {
    clearTimeout(state.timer);
    clearInterval(state.countdown);
  }

  // ---------------------------------------------------------------- binding

  function valueOf(el) {
    if (el.type === "checkbox") return el.checked;
    if (el.dataset.list) return String(el.value || "").split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);
    return String(el.value || "").trim();
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); state.ctx.toast(T("Скопировано", "Copied")); }
    catch (e) { state.ctx.toast(text); }
  }

  async function runTest(platform) {
    const ctx = state.ctx;
    const status = $(platform === "discord" ? "dc-status" : "wa-status");
    if (status) { status.textContent = T("Проверяю…", "Checking…"); status.dataset.state = ""; }
    const body = { platform, lang: ctx.en ? "en" : "ru" };
    if (platform === "discord") body.token = ($("st-dc-token") || {}).value || "";
    else {
      body.mode = ctx.pref("whatsapp_mode", "cloud");
      body.token = ($("st-wa-token") || {}).value || "";
      body.phone_id = ($("st-wa-phone") || {}).value || "";
      body.url = ($("st-wa-waha-url") || {}).value || "";
      body.session = ($("st-wa-waha-session") || {}).value || "";
      body.api_key = ($("st-wa-waha-key") || {}).value || "";
    }
    try {
      const out = await ctx.api("/api/pg/messengers/test", { method: "POST", body });
      let text;
      if (platform === "discord") {
        state.tested = out;
        text = `${T("Токен подходит", "The token works")}: ${out.name}`;
      } else if (out.mode === "waha") {
        text = out.working ? `${T("WAHA работает", "WAHA is working")}${out.phone ? ` · +${out.phone}` : ""}` : `${T("WAHA отвечает, сессия", "WAHA answers, session")}: ${out.state || "?"} — ${T("отсканируйте QR в панели WAHA", "scan the QR in the WAHA dashboard")}`;
      } else {
        text = `${T("Номер найден", "Number found")}: ${out.phone || ""}${out.name ? ` · ${out.name}` : ""}`;
      }
      if (status) { status.textContent = text; status.dataset.state = out.mode === "waha" && !out.working ? "" : "ok"; }
    } catch (e) {
      if (status) { status.textContent = e.message; status.dataset.state = "err"; }
    }
    render();
  }

  async function pair(platform) {
    const ctx = state.ctx;
    if (!ctx.pref(`${platform}_enabled`, false)) ctx.toast(T("Код создан. Включите бота ниже, чтобы он ответил.", "Code created. Turn the bot on below so it can answer."));
    try {
      state.codes[platform] = await ctx.api("/api/pg/messengers/pair", { method: "POST", body: { platform } });
      clearInterval(state.countdown);
      state.countdown = setInterval(() => {
        if (!$("dc-settings")) { clearInterval(state.countdown); return; }
        renderPair("discord"); renderPair("whatsapp");
      }, 1000);
    } catch (e) { ctx.toast(e.message, "err"); }
    render();
  }

  async function unpair(platform) {
    const ctx = state.ctx;
    const name = platform === "discord" ? "Discord" : "WhatsApp";
    if (!window.confirm(T(`Отвязать ${name}? Бот перестанет отвечать, пока вы не привяжете его снова.`, `Unpair ${name}? The bot stops answering until you pair it again.`))) return;
    try { await ctx.api("/api/pg/messengers/unpair", { method: "POST", body: { platform } }); delete state.codes[platform]; }
    catch (e) { ctx.toast(e.message, "err"); }
    refresh();
  }

  function setMode(mode) {
    const ctx = state.ctx;
    document.querySelectorAll("#wa-mode [data-wa-mode-btn]").forEach((b) => { const on = b.dataset.waModeBtn === mode; b.classList.toggle("active", on); b.setAttribute("aria-checked", on ? "true" : "false"); });
    document.querySelectorAll("#wa-settings .msg-mode").forEach((block) => { block.hidden = block.dataset.waMode !== mode; });
    save({ whatsapp_mode: mode });
    if (ctx.patchPrefs) ctx.patchPrefs({ whatsapp_mode: mode });
  }

  function save(updates) {
    const ctx = state.ctx;
    if (ctx.patchPrefs) ctx.patchPrefs(updates);
    ctx.savePreferences(updates);
    clearTimeout(state.refreshSoon);
    state.refreshSoon = setTimeout(refresh, 900);
  }

  function bind(ctx) {
    stop();
    state.ctx = ctx;
    const pane = $("pane-remote");
    if (!pane || !$("msg-tabs")) return;
    const strip = $("msg-tabs");
    strip.onclick = (event) => { const button = event.target.closest("[data-msg-tab]"); if (button) select(button.dataset.msgTab); };
    strip.onkeydown = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = TABS.indexOf(current());
      const next = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
      select(TABS[next]);
      const button = strip.querySelector(`[data-msg-tab="${TABS[next]}"]`);
      if (button) button.focus();
    };
    ["dc-settings", "wa-settings"].forEach((id) => {
      const root = $(id);
      if (!root) return;
      root.addEventListener("change", (event) => {
        const el = event.target;
        if (!el.dataset || !el.dataset.pref) return;
        save({ [el.dataset.pref]: valueOf(el) });
      });
      root.addEventListener("click", (event) => {
        const target = event.target.closest("button, a");
        if (!target) return;
        if (target.dataset.eye) { const input = $(target.dataset.eye); if (input) input.type = input.type === "password" ? "text" : "password"; }
        else if (target.dataset.copy) { const el = $(target.dataset.copy); if (el) copyText(el.value != null && el.tagName === "INPUT" ? el.value : el.textContent); }
        else if (target.dataset.copyText) copyText(target.dataset.copyText);
        else if (target.dataset.pair) pair(target.dataset.pair);
        else if (target.dataset.unpair) unpair(target.dataset.unpair);
        else if (target.dataset.waModeBtn) setMode(target.dataset.waModeBtn);
        else if (target.id === "dc-test") runTest("discord");
        else if (target.id === "wa-test") runTest("whatsapp");
        else if (target.id === "wa-verify-new") newVerifyToken(target);
      });
    });
    applyTab();
    render();
    refresh();
  }

  async function newVerifyToken(button) {
    const ctx = state.ctx;
    if (ctx.pref("whatsapp_verify_token", "") && !window.confirm(T("Создать новый Verify token? Его нужно будет заново вставить в настройки webhook у Meta.", "Create a new verify token? You will have to paste it into the Meta webhook settings again."))) return;
    try {
      const out = await ctx.api("/api/pg/messengers/verify-token", { method: "POST", body: {} });
      const input = $("st-wa-verify");
      if (input) input.value = out.token;
      if (ctx.patchPrefs) ctx.patchPrefs({ whatsapp_verify_token: out.token });
      if (button) button.textContent = T("Новый", "New");
      copyText(out.token);
    } catch (e) { ctx.toast(e.message, "err"); }
  }

  function syncFromPrefs(prefs, changed) {
    if (!prefs || !$("dc-settings")) return;
    document.querySelectorAll("#dc-settings [data-pref], #wa-settings [data-pref]").forEach((el) => {
      const key = el.dataset.pref;
      if ((changed && !changed.has(key)) || el === document.activeElement || !(key in prefs)) return;
      const value = prefs[key];
      if (el.type === "checkbox") el.checked = !!value;
      else el.value = Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);
    });
    if (!changed || changed.has("whatsapp_verify_token")) { const input = $("st-wa-verify"); if (input) input.value = prefs.whatsapp_verify_token || ""; }
    if (!changed || changed.has("whatsapp_mode")) {
      const mode = prefs.whatsapp_mode === "waha" ? "waha" : "cloud";
      document.querySelectorAll("#wa-mode [data-wa-mode-btn]").forEach((b) => { const on = b.dataset.waModeBtn === mode; b.classList.toggle("active", on); b.setAttribute("aria-checked", on ? "true" : "false"); });
      document.querySelectorAll("#wa-settings .msg-mode").forEach((block) => { block.hidden = block.dataset.waMode !== mode; });
    }
    render();
  }

  window.MemuMessengers = { wrap, tabs, bind, select, current, refresh, stop, syncFromPrefs, phaseOf };
})();
