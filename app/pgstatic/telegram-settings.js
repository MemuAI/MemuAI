

(function () {
  "use strict";
  const PLANE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M21.6 3.2 2.9 10.4c-1.3.5-1.3 1.3-.2 1.6l4.8 1.5 1.8 5.6c.2.6.1.9.8.9.5 0 .7-.2 1-.5l2.4-2.3 4.9 3.6c.9.5 1.6.2 1.8-.8l3.2-15.2c.3-1.3-.5-1.9-1.8-1.6zM8.4 13.2l9.6-6.1c.5-.3.9-.1.5.2l-8.2 7.4-.3 3.4-1.6-4.9z"/></svg>';
  const EYE = '<svg viewBox="0 0 24 24" width="17" height="17" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  let state = { status: null, pairing: null, timer: 0, countdown: 0, ctx: null, busy: false };

  const t = (ctx, ru, en) => (ctx.en ? en : ru);

  function select(id, options, value, extra = "") {
    return `<select class="pinput compact" id="${id}" ${extra}>${options.map(([v, label]) =>
      `<option value="${v}" ${String(value) === String(v) ? "selected" : ""}>${label}</option>`).join("")}</select>`;
  }

  function historyOptions(value) {
    const n = Math.round(+value);
    const list = [20, 40, 60, 100];
    if (n >= 10 && n <= 100 && !list.includes(n)) list.push(n);
    return list.sort((a, b) => a - b).map((x) => [x, x]);
  }

  function toggle(id, checked) {
    return `<label class="switch"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}><span class="sl"></span></label>`;
  }

  function row(title, hint, control) {
    return `<div class="tg-row"><div class="tg-row-text"><div class="tl">${title}</div>${hint ? `<div class="td">${hint}</div>` : ""}</div><div class="tg-row-control">${control}</div></div>`;
  }

  function html(ctx) {
    const { pref, esc } = ctx;
    const tr = (ru, en) => t(ctx, ru, en);
    const models = (ctx.models || []).map((m) => [esc(m.id), esc(ctx.modelName ? ctx.modelName(m.id) : m.id)]);
    const notify = pref("telegram_notify_mode", "off");
    const cats = [
      ["st-tg-n-completion", "telegram_notify_completion", tr("Ответы в MemuAI", "Answers in MemuAI")],
      ["st-tg-n-schedule", "telegram_notify_schedule", tr("Задачи по расписанию", "Scheduled tasks")],
      ["st-tg-n-work", "telegram_notify_work", tr("Work и MemuCode", "Work and MemuCode")],
      ["st-tg-n-modules", "telegram_notify_modules", tr("Модули", "Modules")],
    ];
    return `
<section class="tg-settings" id="tg-settings" aria-label="Telegram">
  <header class="tg-hero">
    <span class="tg-mark">${PLANE}</span>
    <div class="tg-hero-text">
      <div class="tg-title">Telegram</div>
      <div class="tg-sub">${tr("MemuAI в кармане: ваши чаты, модели, голос и уведомления — прямо в Telegram.", "MemuAI in your pocket: your chats, models, voice and notifications — right in Telegram.")}</div>
    </div>
    <span class="tg-pill" id="tg-pill" data-state="loading" role="status" aria-live="polite"><i></i><span>${tr("Проверяю…", "Checking…")}</span></span>
  </header>
  <div class="tg-health" id="tg-health" hidden></div>

  <div class="tg-card tg-connect">
    <div class="tg-step">
      <span class="tg-step-num">1</span>
      <div class="tg-step-body">
        <div class="tl">${tr("Токен бота", "Bot token")}</div>
        <div class="td">${tr('Создайте бота у <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> командой <code>/newbot</code> и вставьте токен.', 'Create a bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> using <code>/newbot</code> and paste the token.')}${window.MemuGuideVideo ? ` ${MemuGuideVideo.button("telegram-bot", { compact: true })}` : ""}</div>
        <div class="tg-token-line">
          <input type="password" class="pinput" id="st-tg-token" value="${esc(pref("telegram_bot_token", ""))}" placeholder="123456789:AA…" autocomplete="off" spellcheck="false">
          <button type="button" class="pbtn ghost tg-eye" id="tg-token-eye" aria-label="${tr("Показать токен", "Show token")}" title="${tr("Показать токен", "Show token")}">${EYE}</button>
        </div>
        <div class="tg-bot-line" id="tg-bot-line"></div>
      </div>
    </div>
    <div class="tg-step">
      <span class="tg-step-num">2</span>
      <div class="tg-step-body">
        <div class="tl">${tr("Привязка Telegram", "Pair Telegram")}</div>
        <div class="td">${tr("Без поиска Chat ID: отсканируйте QR телефоном или откройте ссылку — бот привяжется сам.", "No Chat ID hunting: scan the QR with your phone or open the link — the bot pairs itself.")}</div>
        <div id="tg-pair-area" class="tg-pair-area"></div>
      </div>
    </div>
    ${row(tr("Управление через Telegram", "Telegram control"), tr("Бот работает, пока запущен MemuAI Proxy", "The bot runs while MemuAI Proxy is running"), toggle("st-tg-enabled", pref("telegram_enabled", false)))}
    <details class="tg-advanced">
      <summary>${tr("Дополнительно: Chat ID и доступ", "Advanced: Chat ID and access")}</summary>
      <label class="field-label" for="st-tg-chat">${tr("Привязанный Chat ID (заполняется автоматически)", "Paired Chat ID (filled in automatically)")}</label>
      <input class="pinput" id="st-tg-chat" value="${esc(pref("telegram_chat_id", ""))}" placeholder="123456789" inputmode="numeric">
      <label class="field-label" for="st-tg-allowed">${tr("Дополнительные пользователи (Telegram user id через запятую)", "Additional users (Telegram user ids, comma separated)")}</label>
      <input class="pinput" id="st-tg-allowed" value="${esc((pref("telegram_allowed_users", []) || []).join(", "))}" placeholder="111111111, 222222222">
      <div class="td">${tr("Им доступны ваши чаты и модели в их личном чате с ботом. Уведомления получает только привязанный чат.", "They get your chats and models in their private chat with the bot. Only the paired chat receives notifications.")}</div>
    </details>
  </div>

  <div class="tg-card">
    <div class="tg-card-title">${tr("Ответы", "Replies")}</div>
    ${row(tr("Как печатать ответ", "How replies appear"), tr("Живой черновик — текст появляется по мере генерации", "Live draft — text appears as it is generated"),
      select("st-tg-stream", [["draft", tr("Живой черновик", "Live draft")], ["edit", tr("Правка сообщения", "Message edits")], ["off", tr("Целиком в конце", "Whole at the end")]], pref("telegram_stream_mode", "draft")))}
    ${row(tr("Модель по умолчанию", "Default model"), tr("Можно сменить в боте: /models", "Can be changed in the bot: /models"),
      select("st-tg-model", [["", tr("Основная модель MemuAI", "Main MemuAI model")], ...models], pref("telegram_model", ""), 'style="max-width:260px"'))}
    ${row(tr("Язык бота", "Bot language"), tr("Меню, кнопки и команды", "Menus, buttons and commands"),
      select("st-tg-language", [["ru", "Русский"], ["en", "English"]], pref("telegram_language", "ru")))}
    ${row(tr("Сообщений в контексте ИИ", "Messages in AI context"), tr("Больше истории — больше токенов", "More history uses more tokens"),
      select("st-tg-history", historyOptions(pref("telegram_history_messages", 60)), +pref("telegram_history_messages", 60)))}
    ${row(tr("Распознавать голосовые", "Transcribe voice messages"), tr("Локальная модель Whisper из «Голос и Live» MemuAI (скачивается там же); аудиофайлы тоже", "The local Whisper model from MemuAI Voice & Live (downloaded there); audio files too"), toggle("st-tg-voice-in", pref("telegram_voice_input", true)))}
    ${row(tr("Показывать расшифровку перед отправкой", "Show the transcript before sending"), tr("Бот и Mini App сначала покажут текст, модели он уйдёт по кнопке «Отправить»", "The bot and the Mini App show the text first; it goes to the model after “Send”"), toggle("st-tg-voice-review", pref("telegram_voice_review", false)))}
    ${row(tr("Отвечать голосом", "Reply with voice"), tr("Озвучка выбранным голосом MemuAI", "Read aloud with your MemuAI voice"),
      select("st-tg-voice-reply", [["off", tr("Нет", "No")], ["voice", tr("На голосовые", "To voice messages")], ["always", tr("Всегда", "Always")]], pref("telegram_voice_replies", "off")))}
  </div>

  <div class="tg-card">
    <div class="tg-card-title">${tr("Уведомления", "Notifications")}</div>
    ${row(tr("Присылать уведомления", "Send notifications"), tr("Готовые ответы, задачи и Work — с кнопкой «Ответить»", "Finished answers, tasks and Work — with a Reply button"),
      select("st-tg-notify", [["off", tr("Выключены", "Off")], ["silent", tr("Без звука", "Silent")], ["sound", tr("Со звуком", "With sound")]], notify))}
    <div class="tg-chips" id="tg-cats" role="group" aria-label="${tr("Категории", "Categories")}">
      ${cats.map(([id, key, label]) => `<label class="tg-chip"><input type="checkbox" id="${id}" data-pref="${key}" ${pref(key, true) ? "checked" : ""}><span>${label}</span></label>`).join("")}
    </div>
    <div class="td tg-note">${tr("Модули со своим переключателем Telegram (например, «Новые модели API») присылают оповещения даже при выключенном режиме — тихо.", "Modules with their own Telegram switch (e.g. “New API models”) notify even when this is off — silently.")}</div>
    ${row(tr("Тихие часы", "Quiet hours"), tr("Уведомления приходят без звука", "Notifications arrive silently"),
      `<span class="tg-quiet"><input type="time" class="pinput compact" id="st-tg-quiet-from" value="${esc(pref("telegram_quiet_from", "23:00"))}" aria-label="${tr("С", "From")}"><span>–</span><input type="time" class="pinput compact" id="st-tg-quiet-to" value="${esc(pref("telegram_quiet_to", "08:00"))}" aria-label="${tr("До", "To")}">${toggle("st-tg-quiet", pref("telegram_quiet_hours", false))}</span>`)}
  </div>

  <div class="tg-actions">
    <button type="button" class="pbtn ghost" id="st-tg-test">${tr("Проверить подключение", "Test connection")}</button>
    <button type="button" class="pbtn ghost" id="st-tg-restart" title="${tr("Перезапустить опрос Telegram с новым соединением", "Restart Telegram polling with a fresh connection")}">${tr("Перезапустить бота", "Restart bot")}</button>
    <span class="sub" id="st-tg-status" role="status" aria-live="polite"></span>
  </div>
</section>`;
  }

  const PHASES = {
    loading: ["Проверяю…", "Checking…"],
    disabled: ["Выключено", "Off"],
    no_token: ["Нужен токен бота", "Bot token needed"],
    bad_token: ["Токен отклонён", "Token rejected"],
    offline: ["MemuAI Proxy не запущен", "MemuAI Proxy is not running"],
    stalled: ["Бот не отвечает", "The bot is not responding"],
    reconnecting: ["Переподключаюсь…", "Reconnecting…"],
    conflict: ["Бот запущен в другом месте", "Bot is running elsewhere"],
    unpaired: ["Онлайн · ждёт привязки", "Online · waiting to pair"],
    ready: ["Подключено", "Connected"],
    unknown: ["Статус недоступен", "Status unavailable"],
  };

  function renderStatus() {
    const ctx = state.ctx, s = state.status;
    const pill = document.getElementById("tg-pill");
    if (!ctx || !pill) return false;
    const phase = s ? s.phase : "unknown";
    const label = (PHASES[phase] || PHASES.unknown)[ctx.en ? 1 : 0];
    pill.dataset.state = phase;
    pill.querySelector("span").textContent = label + (phase === "ready" && s.bot && s.bot.username ? ` · @${s.bot.username}` : "");
    const botLine = document.getElementById("tg-bot-line");
    if (botLine) {
      if (s && s.bot && s.bot.username) {
        botLine.innerHTML = `✓ <a href="https://t.me/${encodeURIComponent(s.bot.username)}" target="_blank" rel="noopener">@${ctx.esc(s.bot.username)}</a>${s.bot.name ? ` · ${ctx.esc(s.bot.name)}` : ""}`;
        botLine.dataset.state = "ok";
      } else if (s && (s.token_error === "bad_token" || phase === "bad_token")) {
        botLine.textContent = t(ctx, "Telegram отклонил этот токен — скопируйте его заново у @BotFather.", "Telegram rejected this token — copy it again from @BotFather.");
        botLine.dataset.state = "err";
      } else {
        botLine.textContent = "";
        botLine.dataset.state = "";
      }
    }
    renderHealth();
    renderPairArea();
    return true;
  }

  function healthText(ctx, s) {
    const tr = (ru, en) => t(ctx, ru, en);
    if (!s) return null;
    const reason = s.bot_error ? ctx.esc(s.bot_error) : "";
    switch (s.phase) {
      case "reconnecting":
        return { tone: "warn", html: `${reason || tr("Нет связи с Telegram", "No connection to Telegram")}${s.retry_in != null ? ` · ${tr("повтор через", "retry in")} ${s.retry_in} ${tr("с", "s")}` : ""}`, restart: true };
      case "stalled":
        return { tone: "err", html: tr("MemuAI Proxy запущен, но бот не отвечает. Нажмите «Перезапустить бота» — это займёт несколько секунд.", "MemuAI Proxy is running, but the bot does not respond. Press “Restart bot” — it takes a few seconds."), restart: true };
      case "offline":
        return { tone: "err", html: tr("Бот работает внутри MemuAI Proxy — запустите его.", "The bot runs inside MemuAI Proxy — start it."), launch: true };
      case "conflict":
        return { tone: "warn", html: s.local_conflict
          ? tr("Этот бот уже запущен другой копией MemuAI Proxy на этом компьютере — закройте лишнюю.", "This bot already runs in another MemuAI Proxy on this computer — close the extra one.")
          : tr("Тот же токен использует другая программа или компьютер. Остановите её или создайте отдельного бота.", "Another program or computer uses the same token. Stop it or create a separate bot."), restart: true };
      case "bad_token":
        return { tone: "err", html: tr("Telegram отклонил токен. Скопируйте его заново у @BotFather — бот подхватит новый сам.", "Telegram rejected the token. Copy it again from @BotFather — the bot picks the new one up by itself."), restart: true };
      default:
        return null;
    }
  }

  function renderHealth() {
    const ctx = state.ctx, box = document.getElementById("tg-health");
    if (!ctx || !box) return;
    const view = healthText(ctx, state.status);
    if (!view) { box.hidden = true; box.innerHTML = ""; return; }
    box.hidden = false;
    box.dataset.tone = view.tone;
    box.innerHTML = `<span>${view.html}</span>${view.launch ? `<button type="button" class="pbtn ghost small" id="tg-launch-proxy">${t(ctx, "Запустить Proxy", "Start Proxy")}</button>` : ""}`;
    const launch = document.getElementById("tg-launch-proxy");
    if (launch) launch.onclick = async () => {
      launch.disabled = true;
      try { await ctx.api("/api/pg/launch_proxy", { method: "POST", body: {} }); ctx.toast(t(ctx, "Запускаю MemuAI Proxy…", "Starting MemuAI Proxy…")); }
      catch (e) { ctx.toast(e.message, "err"); }
      setTimeout(refresh, 2500);
    };
  }

  async function restartBot() {
    const ctx = state.ctx, status = document.getElementById("st-tg-status");
    const button = document.getElementById("st-tg-restart");
    if (button) button.disabled = true;
    if (status) { status.textContent = t(ctx, "Перезапускаю бота…", "Restarting the bot…"); status.dataset.state = ""; }
    try {
      const out = await ctx.api("/api/pg/telegram/restart", { method: "POST", body: {} });
      if (out.proxy_online === false && status) {
        status.textContent = t(ctx, "MemuAI Proxy не запущен — бот перезапустится вместе с ним.", "MemuAI Proxy is not running — the bot restarts with it.");
        status.dataset.state = "err";
      } else {
        state.restartAt = Date.now();
        schedule(1500);
      }
    } catch (e) {
      if (status) { status.textContent = e.message; status.dataset.state = "err"; }
    }
    setTimeout(() => { if (button) button.disabled = false; }, 4000);
  }

  function renderPairArea() {
    const ctx = state.ctx, s = state.status || {};
    const area = document.getElementById("tg-pair-area");
    if (!ctx || !area) return;
    const tr = (ru, en) => t(ctx, ru, en);
    const p = state.pairing;
    if (p && !(s.paired && s.owner && s.owner.paired_at && s.owner.paired_at * 1000 > p.createdAt)) {
      const left = Math.max(0, Math.round(p.expires * 1000 - Date.now()) / 1000);
      const mm = Math.floor(left / 60), ss = String(Math.floor(left % 60)).padStart(2, "0");
      const qr = window.MemuQR && p.link ? window.MemuQR.svg(p.link) : "";
      area.innerHTML = `
        <div class="tg-pair">
          ${qr ? `<div class="tg-qr" aria-label="${tr("QR-код для привязки", "Pairing QR code")}">${qr}</div>` : ""}
          <div class="tg-pair-info">
            <div class="td">${tr("Отсканируйте камерой телефона или откройте бота и отправьте код:", "Scan with your phone camera, or open the bot and send the code:")}</div>
            <div class="tg-code" id="tg-code">${ctx.esc(p.code)}</div>
            <div class="tg-pair-buttons">
              ${p.link ? `<a class="pbtn" id="tg-open-link" href="${ctx.esc(p.link)}" target="_blank" rel="noopener">${PLANE}<span>${tr("Открыть в Telegram", "Open in Telegram")}</span></a>` : ""}
              <button type="button" class="pbtn ghost" id="tg-copy-code">${tr("Копировать код", "Copy code")}</button>
              <button type="button" class="pbtn ghost" id="tg-pair-cancel">${tr("Отмена", "Cancel")}</button>
            </div>
            <div class="td tg-countdown">${left > 0 ? tr(`Код действует ещё ${mm}:${ss}`, `The code is valid for ${mm}:${ss}`) : tr("Код истёк — создайте новый.", "The code expired — create a new one.")}</div>
          </div>
        </div>`;
      bindPairButtons();
      return;
    }
    if (p) { state.pairing = null; }
    if (s.paired) {
      const owner = s.owner || {};
      const name = owner.name || (owner.username ? "@" + owner.username : "") || (tr("Chat ID ", "Chat ID ") + (owner.chat_id || ""));
      const initial = (owner.name || owner.username || "T").trim().charAt(0).toUpperCase();
      area.innerHTML = `
        <div class="tg-owner">
          <span class="tg-avatar">${ctx.esc(initial)}</span>
          <div class="tg-owner-text"><div class="tl">${ctx.esc(name)}</div><div class="td">${owner.username && owner.name ? "@" + ctx.esc(owner.username) + " · " : ""}${tr("привязан", "paired")}${owner.paired_at ? " " + new Date(owner.paired_at * 1000).toLocaleDateString(ctx.en ? "en-GB" : "ru-RU") : ""}</div></div>
          <button type="button" class="pbtn ghost" id="tg-repair">${tr("Привязать другой", "Pair another")}</button>
          <button type="button" class="pbtn ghost danger" id="tg-unpair">${tr("Отвязать", "Unpair")}</button>
        </div>`;
    } else {
      area.innerHTML = `<button type="button" class="pbtn tg-pair-btn" id="tg-pair-start" ${s.has_token === false ? "disabled" : ""}>${PLANE}<span>${tr("Подключить Telegram", "Connect Telegram")}</span></button>`;
    }
    bindPairButtons();
  }

  function bindPairButtons() {
    const ctx = state.ctx;
    const byId = (id) => document.getElementById(id);
    const start = byId("tg-pair-start") || byId("tg-repair");
    if (start) start.onclick = () => startPairing();
    const cancel = byId("tg-pair-cancel");
    if (cancel) cancel.onclick = async () => {
      state.pairing = null;
      try { await ctx.api("/api/pg/telegram/pair/cancel", { method: "POST", body: {} }); } catch (e) {}
      refresh();
    };
    const copy = byId("tg-copy-code");
    if (copy) copy.onclick = async () => {
      try { await navigator.clipboard.writeText(state.pairing.raw); ctx.toast(t(ctx, "Код скопирован", "Code copied")); }
      catch (e) { ctx.toast(state.pairing.code); }
    };
    const unpair = byId("tg-unpair");
    if (unpair) unpair.onclick = async () => {
      const ok = window.confirm(t(ctx, "Отвязать Telegram? Бот перестанет отвечать в этом чате, пока вы не привяжете его снова.",
        "Unpair Telegram? The bot stops answering in that chat until you pair it again."));
      if (!ok) return;
      try {
        await ctx.api("/api/pg/telegram/unpair", { method: "POST", body: {} });
        const chat = byId("st-tg-chat"); if (chat) chat.value = "";
        if (ctx.setPref) ctx.setPref({ telegram_chat_id: "" });
      } catch (e) { ctx.toast(e.message, "err"); }
      refresh();
    };
  }

  async function startPairing() {
    const ctx = state.ctx;
    const status = document.getElementById("st-tg-status");
    const token = (document.getElementById("st-tg-token") || {}).value || "";
    if (status) status.textContent = t(ctx, "Создаю код…", "Creating a code…");
    try {
      const out = await ctx.api("/api/pg/telegram/pair", { method: "POST", body: { token: token.trim(), lang: ctx.en ? "en" : "ru" } });
      state.pairing = { code: out.code, raw: out.raw_code, link: out.link, expires: out.expires, createdAt: Date.now() - 2000 };
      const enabled = document.getElementById("st-tg-enabled");
      if (enabled) enabled.checked = true;
      if (ctx.setPref) ctx.setPref({ telegram_enabled: true, telegram_bot_token: token.trim() });
      if (status) status.textContent = "";
      renderPairArea();
      schedule(1500);
    } catch (e) {
      if (status) status.textContent = e.message;
    }
  }

  async function refresh() {
    const ctx = state.ctx;
    if (!ctx || !document.getElementById("tg-settings")) { stop(); return; }
    try {
      state.status = await ctx.api("/api/pg/telegram/status?lang=" + (ctx.en ? "en" : "ru"));
      if (state.pairing && state.status.paired && state.status.owner && state.status.owner.paired_at * 1000 > state.pairing.createdAt) {
        state.pairing = null;
        ctx.toast(t(ctx, "Telegram подключён ✓", "Telegram connected ✓"));
        const chat = document.getElementById("st-tg-chat");
        if (chat) chat.value = state.status.owner.chat_id || "";
        if (ctx.setPref) ctx.setPref({ telegram_chat_id: state.status.owner.chat_id || "", telegram_enabled: true });
      }

      const serverChat = (state.status.owner && state.status.owner.chat_id) || "";
      if (ctx.setPref && serverChat !== String(ctx.pref("telegram_chat_id", "") || "")) {
        ctx.setPref({ telegram_chat_id: serverChat });
        const chat = document.getElementById("st-tg-chat");
        if (chat && document.activeElement !== chat) chat.value = serverChat;
      }
    } catch (e) {
      state.status = state.status || null;
    }
    renderStatus();
    if (state.restartAt && state.status) {
      const status = document.getElementById("st-tg-status");
      if (["ready", "unpaired"].includes(state.status.phase) && state.status.last_ok_age != null && state.status.last_ok_age * 1000 < Date.now() - state.restartAt + 1500) {
        state.restartAt = 0;
        if (status) { status.textContent = t(state.ctx, "✓ Бот перезапущен и на связи", "✓ The bot restarted and is online"); status.dataset.state = "ok"; }
      } else if (Date.now() - state.restartAt > 30000) {
        state.restartAt = 0;
      }
    }
    schedule(state.pairing || state.restartAt ? 1500 : 4000);
  }

  function schedule(ms) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      if (document.hidden) { schedule(ms); return; }
      if (state.pairing) renderPairArea();
      refresh();
    }, ms);
  }

  function stop() {
    clearTimeout(state.timer);
    clearInterval(state.countdown);
  }

  function bind(ctx) {
    stop();
    state.ctx = ctx;
    const $ = (id) => document.getElementById(id);
    if (!$("tg-settings")) return;
    const collect = () => {
      const allowed = ($("st-tg-allowed").value || "").split(/[\s,;]+/).map((x) => x.trim()).filter((x) => /^-?\d{3,20}$/.test(x));
      return {
        telegram_bot_token: $("st-tg-token").value.trim(),
        telegram_chat_id: $("st-tg-chat").value.trim(),
        telegram_enabled: $("st-tg-enabled").checked,
        telegram_notify_mode: $("st-tg-notify").value,
        telegram_notify: $("st-tg-notify").value !== "off",
        telegram_model: $("st-tg-model").value,
        telegram_language: $("st-tg-language").value,
        telegram_history_messages: +$("st-tg-history").value,
        telegram_stream_mode: $("st-tg-stream").value,
        telegram_voice_input: $("st-tg-voice-in").checked,
        telegram_voice_review: $("st-tg-voice-review").checked,
        telegram_voice_replies: $("st-tg-voice-reply").value,
        telegram_quiet_hours: $("st-tg-quiet").checked,
        telegram_quiet_from: $("st-tg-quiet-from").value || "23:00",
        telegram_quiet_to: $("st-tg-quiet-to").value || "08:00",
        telegram_allowed_users: allowed,
        ...Object.fromEntries([...document.querySelectorAll("#tg-cats input[data-pref]")].map((el) => [el.dataset.pref, el.checked])),
      };
    };

    const KEYS = {
      "st-tg-token": ["telegram_bot_token"], "st-tg-chat": ["telegram_chat_id"], "st-tg-enabled": ["telegram_enabled"],
      "st-tg-notify": ["telegram_notify_mode", "telegram_notify"], "st-tg-model": ["telegram_model"],
      "st-tg-language": ["telegram_language"], "st-tg-history": ["telegram_history_messages"],
      "st-tg-stream": ["telegram_stream_mode"], "st-tg-voice-in": ["telegram_voice_input"],
      "st-tg-voice-review": ["telegram_voice_review"],
      "st-tg-voice-reply": ["telegram_voice_replies"], "st-tg-quiet": ["telegram_quiet_hours"],
      "st-tg-quiet-from": ["telegram_quiet_from"], "st-tg-quiet-to": ["telegram_quiet_to"],
      "st-tg-allowed": ["telegram_allowed_users"],
    };
    const save = (event) => {
      const all = collect(), el = event && event.target;
      const keys = el ? (el.dataset.pref ? [el.dataset.pref] : KEYS[el.id]) : null;
      ctx.savePreferences(keys ? Object.fromEntries(keys.map((key) => [key, all[key]])) : all);
      clearTimeout(state.refreshSoon);
      state.refreshSoon = setTimeout(refresh, 700);
    };
    state.collectKeys = KEYS;
    ctx.setPref = (updates) => { if (ctx.patchPrefs) ctx.patchPrefs(updates); };
    document.querySelectorAll("#tg-settings input, #tg-settings select").forEach((el) => {
      if (el.id && el.id.startsWith("st-tg-") || el.dataset.pref) el.addEventListener("change", save);
    });
    $("tg-token-eye").onclick = () => {
      const input = $("st-tg-token");
      input.type = input.type === "password" ? "text" : "password";
    };
    $("st-tg-restart").onclick = restartBot;
    $("st-tg-test").onclick = async () => {
      const status = $("st-tg-status");
      status.textContent = t(ctx, "Проверяю…", "Checking…");
      status.dataset.state = "";
      try {
        const out = await ctx.api("/api/pg/telegram/test", { method: "POST", body: { token: $("st-tg-token").value.trim(), chat_id: $("st-tg-chat").value.trim(), lang: ctx.en ? "en" : "ru" } });
        status.textContent = `✓ @${out.username || out.name}` + (out.sent ? t(ctx, " · сообщение отправлено", " · message sent") : t(ctx, " · привяжите Telegram, чтобы получить проверочное сообщение", " · pair Telegram to receive a test message"));
        status.dataset.state = "ok";
      } catch (e) {
        status.textContent = e.message;
        status.dataset.state = "err";
      }
      refresh();
    };
    renderPairArea();
    refresh();
  }

  function syncFromPrefs(prefs, changed) {
    if (!document.getElementById("tg-settings") || !prefs) return;
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (!el || el === document.activeElement) return;
      if (el.id === "st-tg-history") {
        el.innerHTML = historyOptions(value).map(([v]) => `<option value="${v}">${v}</option>`).join("");
      }
      if (el.type === "checkbox") el.checked = !!value; else el.value = value == null ? "" : String(value);
    };
    const map = {
      telegram_enabled: "st-tg-enabled", telegram_notify_mode: "st-tg-notify", telegram_model: "st-tg-model",
      telegram_language: "st-tg-language", telegram_history_messages: "st-tg-history", telegram_stream_mode: "st-tg-stream",
      telegram_voice_input: "st-tg-voice-in", telegram_voice_replies: "st-tg-voice-reply", telegram_quiet_hours: "st-tg-quiet",
      telegram_voice_review: "st-tg-voice-review",
      telegram_quiet_from: "st-tg-quiet-from", telegram_quiet_to: "st-tg-quiet-to", telegram_chat_id: "st-tg-chat",
    };
    Object.entries(map).forEach(([key, id]) => { if (!changed || changed.has(key)) set(id, prefs[key]); });
    if (!changed || changed.has("telegram_allowed_users")) set("st-tg-allowed", (prefs.telegram_allowed_users || []).join(", "));
    document.querySelectorAll("#tg-cats input[data-pref]").forEach((el) => {
      if ((!changed || changed.has(el.dataset.pref)) && el !== document.activeElement) el.checked = prefs[el.dataset.pref] !== false;
    });
  }

  window.MemuTelegramSettings = { html, bind, syncFromPrefs, _state: state, _render: renderStatus };
})();
