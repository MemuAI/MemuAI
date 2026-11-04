
(function () {
  "use strict";
  const en = () => { try { return PG_LANG === "en"; } catch (_) { return (document.documentElement.lang || "").startsWith("en"); } };
  const L = (ru, eng) => en() ? eng : ru;
  const errorText = value => { const parts = String(value || "").split(" / "); return parts.length === 2 ? (en() ? parts[1] : parts[0]) : String(value || ""); };
  const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const API = "/api/pg/integrations/telegram-use";
  let state = null, voiceOptions = {}, bound = false, timer = 0, busy = false, qrURL = "", retryAt = 0, lastNotice = "";
  let revision = 0, savedRevision = 0, saveTimer = 0, savePromise = null, quietValidation = false, pendingAccount = "";
  const btn = (action, label) => `<button type="button" class="pbtn ghost" data-tgu="${action}">${esc(label)}</button>`;
  const field = (key, label, value, type = "text", attrs = "") => `<label class="tgu-field"><span>${esc(label)}</span><input class="pinput" data-tgu-field="${key}" type="${type}" value="${esc(value)}" ${attrs}><small class="tgu-field-error" hidden></small></label>`;
  const check = (key, label, value) => `<label class="tgu-check"><input type="checkbox" data-tgu-field="${key}" ${value ? "checked" : ""}> ${esc(label)}</label>`;
  function models(value, key, fallback = L("Основная модель MemuAI", "Main MemuAI model")) {
    const rows = window.MemuModelPicker?.rows?.() || [];
    const list = rows.slice();
    if (value && !list.some(m => m.id === value)) list.push({id:value});
    return `<select class="pinput" data-tgu-field="${key}" data-tgu-model><option value="">${esc(fallback)}</option>${list.map(m => `<option value="${esc(m.id)}" ${m.id === value ? "selected" : ""}>${esc(window.MemuModelPicker?.name?.(m.id) || m.id)}</option>`).join("")}</select>`;
  }
  function command(c, i) {
    return `<details class="tgu-command" data-tgu-command="${i}" data-kind="${esc(c.kind)}"><summary><b>${esc(c.trigger)}</b> · ${esc(c.name)} ${c.enabled ? "" : esc(L("(выключена)", "(disabled)"))}</summary>
      <div class="tgu-grid">${field("name", L("Название", "Name"), c.name)}${field("trigger", L("Команда", "Trigger"), c.trigger)}
      <label class="tgu-field"><span>${L("Действие", "Action")}</span><select class="pinput" data-tgu-field="kind"><option value="question" ${c.kind === "question" ? "selected" : ""}>${L("Ответ ИИ", "AI answer")}</option><option value="summary" ${c.kind === "summary" ? "selected" : ""}>${L("Пересказ переписки", "Summarize a conversation")}</option><option value="voice" ${c.kind === "voice" ? "selected" : ""}>${L("Расшифровка голосового в ответе", "Transcribe replied voice message")}</option></select></label>
      <label class="tgu-field"><span>${L("Куда сохранить", "Delivery")}</span><select class="pinput" data-tgu-field="output">${[["replace",L("Заменить свою команду (чужой — ответить)","Replace own command (reply to others)")],["reply",L("Ответить в Telegram", "Reply in Telegram")],["memuai",L("Новый чат MemuAI", "New MemuAI chat")]].map(([v,t]) => `<option value="${v}" ${c.output === v ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label></div>
      ${check("enabled", L("Команда включена", "Command enabled"), c.enabled)}
      <label class="tgu-field tgu-text-command"><span>${L("Инструкция ИИ · {question} — вопрос пользователя", "AI instruction · {question} is the user's question")}</span><textarea class="pinput" rows="4" data-tgu-field="prompt">${esc(c.prompt)}</textarea></label>
      <label class="tgu-field tgu-text-command"><span>${L("Модель команды", "Command model")}</span>${models(c.model, "model",L("Модель Telegram Use (или основная MemuAI)","Telegram Use model (or main MemuAI model)"))}</label>
      <p class="tgu-note tgu-voice-command">${L("Источник, язык и очистка настраиваются в разделе «Распознавание речи и очистка диктовки» выше. Для обычной расшифровки текстовая модель не нужна.","Configure source, language and cleanup in Speech recognition and transcript cleanup above. Plain transcription needs no chat model.")}</p>
      <div class="tgu-arguments"><h4>${L("Аргументы команды", "Command arguments")}</h4><p class="tgu-note">${L("Аргументы идут по порядку. Последний текст или название чата может содержать пробелы. Подставляйте их в инструкцию: {count}, {chat} или {имя_аргумента}. Для пересказа нужны count и необязательный chat.", "Arguments follow their order. The last text or chat title can contain spaces. Insert them in the instruction with {count}, {chat} or {argument_name}. A summary needs count and an optional chat.")}</p>
      <div data-tgu-arguments>${(c.arguments || []).map((a,j) => argument(a,i,j)).join("")}</div>${btn("arg-add-"+i,L("Добавить аргумент","Add argument"))}</div>
      <div class="tgu-grid">${field("context_messages", L("Сообщений контекста (0–2000)", "Context messages (0–2000)"), c.context_messages, "number", 'min="0" max="2000"')}
      ${field("allowed_users", L("Другие пользователи: числовые ID через запятую", "Other users: numeric IDs, comma separated"), (c.allowed_users || []).join(", "))}
      ${field("allowed_chats", L("Только чаты с ID (пусто — любые свои)", "Only chat IDs (empty: any for yourself)"), (c.allowed_chats || []).join(", "))}
      ${field("max_per_hour", L("Запросов на человека в час", "Requests per person per hour"), c.max_per_hour, "number", 'min="1" max="1000"')}
      ${field("max_per_day", L("Запросов на человека в сутки", "Requests per person per day"), c.max_per_day, "number", 'min="1" max="10000"')}
      ${field("cooldown_seconds", L("Пауза между запросами, с", "Cooldown, seconds"), c.cooldown_seconds, "number", 'min="0" max="86400"')}
      ${field("max_input_chars", L("Максимум символов в вопросе", "Maximum input characters"), c.max_input_chars, "number", 'min="100" max="32000"')}</div>
      <p class="tgu-note">${L("Пустой список других пользователей разрешает команду только вам. В группе другому пользователю нужны оба разрешения: его ID и ID группы. Чужое сообщение не редактируется — ИИ ответит на него.", "An empty user list allows only you. In groups, other users need both their user ID and the group chat ID. Other people's messages are answered, never edited.")}</p>
      ${btn("delete-" + i, L("Удалить команду", "Delete command"))}</details>`;
  }
  function argument(a, i, j) {
    const af = (key,label,value,type="text",attrs="") => field(key,label,value,type,attrs).replaceAll("data-tgu-field", "data-tgu-arg-field");
    return `<fieldset class="tgu-argument" data-tgu-argument="${j}" data-kind="${esc(a.type || "text")}"><legend>${L("Аргумент", "Argument")} ${j+1}</legend><div class="tgu-grid">
      ${af("name",L("Имя для {подстановки}","Name for {placeholder}"),a.name || "")}
      <label class="tgu-field"><span>${L("Тип", "Type")}</span><select class="pinput" data-tgu-arg-field="type">${[["text",L("Текст","Text")],["chat",L("Название чата","Chat title")],["integer",L("Целое число","Integer")],["number",L("Число","Number")],["boolean",L("Да / нет","Yes / no")],["choice",L("Один из вариантов","Choice")]].map(([v,t])=>`<option value="${v}" ${a.type===v?"selected":""}>${esc(t)}</option>`).join("")}</select></label>
      ${af("description",L("Подсказка","Hint"),a.description || "")}${af("default",L("Значение по умолчанию (необязательно)","Default value (optional)"),a.default ?? "")}
      <div class="tgu-arg-bounds">${af("min",L("Минимум","Minimum"),a.min ?? "","number")}</div><div class="tgu-arg-bounds">${af("max",L("Максимум","Maximum"),a.max ?? "","number")}</div>
      <div class="tgu-arg-choices">${af("choices",L("Варианты через запятую","Choices, comma separated"),(a.choices || []).join(", "))}</div></div>
      ${check("required",L("Обязательный аргумент","Required argument"),a.required).replaceAll("data-tgu-field", "data-tgu-arg-field")}
      ${btn("arg-delete-"+i+"-"+j,L("Удалить аргумент","Remove argument"))}</fieldset>`;
  }
  function voiceSettings(c) {
    const options = [...(voiceOptions.stt_models || [])];
    const selected = c.stt_local_model || "";
    if (selected && !options.some(m=>m.id===selected)) options.push({id:selected,name:selected});
    const source = c.stt_source || "voice", inherited = (voiceOptions.sources || {})[source] || {};
    const usingLocal = source === "local" || !!selected;
    const info = selected ? (options.find(m=>m.id===selected)?.name || selected) : inherited.model ? `${inherited.service_name || ""} · ${inherited.model}` : (voiceOptions.inherited_model || "");
    return `<details class="tgu-help tgu-voice" data-tgu-voice-source="${esc(source)}"><summary>${L("Голосовые сообщения · !//voice", "Voice messages · !//voice")}</summary>
      <p class="tgu-note">${L("Команда !//voice распознаёт голосовое, на которое вы ответили. Для распознавания текстовая модель не требуется. Результат придёт туда, куда настроена команда.", "Reply to a voice message with !//voice. Recognition does not require a chat model. The result follows the command's delivery setting.")}</p>
      <div class="tgu-grid"><label class="tgu-field"><span>${L("Источник распознавания","Recognition source")}</span><select class="pinput" data-tgu-field="stt_source">${[["voice",L("Как в «Голос и Live»","Same as Voice & Live")],["quick",L("Как в быстром доступе","Same as Quick Access")],["local",L("Выбрать локальную модель отдельно","Choose a local model separately")],["api",L("Облачный сервис из «Голос и Live»","Cloud service from Voice & Live")]].map(([v,t])=>`<option value="${v}" ${(c.stt_source || "voice")===v?"selected":""}>${esc(t)}</option>`).join("")}</select></label>
      <label class="tgu-field tgu-voice-local" ${usingLocal ? "" : "hidden"}><span>${L("Локальная модель","Local model")}</span><select class="pinput" data-tgu-field="stt_local_model"><option value="">${L("Использовать выбранную в источнике","Use source's selected model")}</option>${options.map(m=>`<option value="${esc(m.id)}" ${m.id===selected?"selected":""}>${esc(m.name || m.id)}${m.downloaded===false?esc(L(" · не скачана"," · not downloaded")):""}</option>`).join("")}</select></label>
      <label class="tgu-field"><span>${L("Язык речи","Speech language")}</span><select class="pinput" data-tgu-field="stt_language">${[["auto",L("Определять автоматически","Detect automatically")],["ru","Русский"],["en","English"],["uk","Українська"],["de","Deutsch"],["fr","Français"],["es","Español"],["it","Italiano"],["pt","Português"],["zh","中文"],["ja","日本語"],["ko","한국어"],["tr","Türkçe"]].map(([v,t])=>`<option value="${v}" ${(c.stt_language || "auto")===v?"selected":""}>${esc(t)}</option>`).join("")}</select></label></div>
      <p class="tgu-note tgu-voice-current" role="status">${esc(info ? L("Сейчас используется: ","Currently uses: ") + info : L("Модель и её настройки берутся из выбранного источника.","The selected source supplies its model and settings."))}</p>
      <p class="tgu-note">${L("Сервисы Gemini, ElevenLabs и другие, их ключи и скачивание локальных моделей настраиваются в «Голос и Live → Распознавание речи». Здесь выбирается источник только для Telegram.", "Configure Gemini, ElevenLabs and other services, API keys and local downloads in Voice & Live → Speech recognition. Choose Telegram's source here.")}</p>
      ${check("voice_cleanup_enabled",L("Очищать диктовку после распознавания","Clean up transcripts after recognition"),c.voice_cleanup_enabled)}
      <div class="tgu-voice-cleanup" ${c.voice_cleanup_enabled ? "" : "hidden"}><label class="tgu-field"><span>${L("Модель очистки","Cleanup model")}</span>${models(c.voice_cleanup_model,"voice_cleanup_model",L("Модель Telegram Use (или основная MemuAI)","Telegram Use model (or main MemuAI model)"))}</label>
      <label class="tgu-field"><span>${L("Инструкция очистки","Cleanup instruction")}</span><textarea class="pinput" rows="5" data-tgu-field="voice_cleanup_prompt" placeholder="${esc(voiceOptions.cleanup_prompt || "")}">${esc(c.voice_cleanup_prompt || "")}</textarea></label>
      <p class="tgu-note">${L("Пустая инструкция исправляет пунктуацию, оговорки и абзацы, сохраняя смысл. При сбое очистки сохраняется исходная расшифровка.", "An empty instruction corrects punctuation, speech slips and paragraphs while preserving meaning. If cleanup fails, the original transcript is kept.")}</p></div></details>`;
  }
  function content() {
    if (!state) return `<p>${L("Проверяю Telegram Use…", "Checking Telegram Use…")}</p>`;
    const c = state.config;
    const section = (number, title, body, done = false) => `<section class="tgu-setup-step"><header><span class="tgu-step-number ${done ? "is-done" : ""}">${done ? "✓" : number}</span><h3>${title}</h3></header><div class="tgu-step-body">${body}</div></section>`;
    return `<div class="tgu-status" id="tgu-status" role="status" aria-live="polite"></div><div id="tgu-feedback" class="tgu-feedback" role="status" aria-live="polite" hidden></div><p id="tgu-wait" class="tgu-note" role="status" hidden></p>
      <p class="tgu-intro">${L("Подключите личный аккаунт, чтобы обсуждать переписку с ИИ, расшифровывать голосовые и запускать свои команды прямо в Telegram.", "Connect your personal account to discuss conversations with AI, transcribe voice messages and run your own commands inside Telegram.")}</p>
      ${section(1, L("Подготовьте данные приложения Telegram", "Prepare your Telegram app credentials"), `
        <p class="tgu-note">${L("Для входа нужны два значения: API ID и API hash. Telegram требует их и при входе по QR. Это данные приложения, а не токен бота и не номер телефона.", "Signing in requires API ID and API hash, including QR sign-in. These identify an app, not a bot token or phone number.")}</p>
        <details class="tgu-help" ${state.configured ? "" : "open"}><summary>${L("Где получить API ID и API hash", "Where to get API ID and API hash")}</summary><ol>
          <li>${L('Откройте <a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer">my.telegram.org/apps</a> в браузере и войдите по своему номеру Telegram. Код приходит в Telegram.', 'Open <a href="https://my.telegram.org/apps" target="_blank" rel="noopener noreferrer">my.telegram.org/apps</a> in your browser and sign in with your Telegram number. The code arrives in Telegram.')}</li>
          <li>${L("Если приложение уже создано, скопируйте его API ID и API hash. Telegram разрешает один API ID на номер: создавать ещё одно приложение не нужно.", "If an app already exists, copy its API ID and API hash. Telegram allows one API ID per number; you do not need another app.")}</li>
          <li>${L('Если открылась форма создания: App title — например «MemuAI Personal»; Short name — имя латиницей без пробелов; Platform — Desktop. Заполните остальные обязательные поля, если сайт их попросит, и нажмите Create application.', 'If you see a creation form: use an App title such as “MemuAI Personal”, a Short name using Latin letters without spaces, and Platform — Desktop. Complete any other fields required by the site, then select Create application.')}</li>
          <li>${L("Перенесите оба значения в поля ниже. После сохранения API hash не отображается; оставьте поле пустым, чтобы сохранить прежний.", "Paste both values below. The saved API hash is never displayed; an empty field keeps the saved value.")}</li>
        </ol></details>
        <details class="tgu-help tgu-help--warning"><summary>${L('Сайт пишет «Too many attempts. Please try again later.»', 'The site says “Too many attempts. Please try again later.”')}</summary>
          <p>${L("Запрос отклонил сайт Telegram ещё до подключения MemuAI. Исправление полей здесь или смена режима входа на QR не снимает это ограничение.", "Telegram's website rejected the request before MemuAI could connect. Editing fields here or switching to QR cannot remove that restriction.")}</p><ul>
          <li>${L("Прекратите повторять создание приложения. Попробуйте позже: сайт не указывает точное время ожидания, и MemuAI не может его определить.", "Stop repeating the app creation request. Try later: the site gives no exact waiting time, and MemuAI cannot determine it.")}</li>
          <li>${L("Если вы создавали приложение раньше, вернитесь в API development tools и используйте его данные.", "If you created an app before, return to API development tools and use its credentials.")}</li>
          <li>${L("Если включён VPN, попробуйте выключить его для входа и создания приложения на my.telegram.org. Это иногда помогает при ограничении общего VPN-адреса, но не снимает серверный лимит Telegram.", "If a VPN is enabled, try disabling it when signing in or creating an app at my.telegram.org. This can help with shared VPN addresses, but cannot remove a Telegram server rate limit.")}</li>
          <li>${L('Если проблема сохраняется, обратитесь в <a href="https://telegram.org/support" target="_blank" rel="noopener noreferrer">поддержку Telegram</a>. Не отправляйте им API hash, пароль или код входа.', 'If the problem persists, contact <a href="https://telegram.org/support" target="_blank" rel="noopener noreferrer">Telegram support</a>. Do not send API hashes, passwords or login codes.')}</li></ul>
          <p>${L("Прокси в настройках ниже используется только соединением MemuAI с Telegram. Он не меняет подключение сайта my.telegram.org в браузере.", "The proxy below is used only by MemuAI's Telegram connection. It does not change how your browser connects to my.telegram.org.")}</p>
        </details>
        <div class="tgu-grid">${field("api_id", "API ID", c.api_id || "", "text", 'inputmode="numeric" autocomplete="off" placeholder="12345678"')}
          ${field("api_hash", "API hash", "", "password", `autocomplete="new-password" spellcheck="false" placeholder="${state.has_api_hash ? L("Сохранён — оставьте пустым", "Saved — leave empty") : L("32 символа с my.telegram.org", "32 characters from my.telegram.org")}"`)}</div>
        <p class="tgu-note"><a href="https://core.telegram.org/api/obtaining_api_id" target="_blank" rel="noopener noreferrer">${L("Инструкция Telegram", "Telegram instructions")}</a> · <a href="https://core.telegram.org/api/qr-login" target="_blank" rel="noopener noreferrer">${L("Почему QR тоже требует API ID", "Why QR also needs an API ID")}</a></p>
      `, state.configured)}
      ${section(2, L("Войдите в свой аккаунт", "Sign in to your account"), `
        <details class="tgu-help" ${state.has_proxy ? "open" : ""}><summary>${L("Настроить прокси", "Configure a proxy")}</summary>
          <p class="tgu-note">${L("Если Telegram недоступен напрямую, вставьте ссылку на рабочий прокси перед входом.", "If Telegram is unavailable directly, paste a working proxy link before signing in.")}</p>
          ${field("proxy", L("Ссылка на прокси", "Proxy link"), "", "password", `autocomplete="new-password" spellcheck="false" placeholder="${state.has_proxy ? L("Сохранён — пустое поле не меняет", "Saved — empty keeps it") : "tg://proxy?server=…&port=…&secret=…"}"`)}
          ${check("clear_proxy", L("Удалить сохранённый прокси", "Remove saved proxy"), false)}
          <p class="tgu-note">${L("Поддерживаются tg://proxy, https://t.me/proxy, tg://socks, SOCKS5, SOCKS4 и HTTP. MTProxy: обычный secret или dd; ee / Fake-TLS пока не поддерживается.", "Supports tg://proxy, https://t.me/proxy, tg://socks, SOCKS5, SOCKS4 and HTTP. MTProxy: standard or dd secrets; ee / Fake-TLS is not supported yet.")}</p>
        </details>
        <p class="tgu-note">${L("Нажмите «Показать QR» — данные выше сохранятся автоматически. На телефоне откройте Telegram → Настройки → Устройства → Подключить устройство.", "Select Show QR to save the credentials above automatically. On your phone, open Telegram → Settings → Devices → Link Desktop Device.")}</p>
        <p class="tgu-note">${L("Если вы в России и прямое соединение с Telegram заблокировано, для генерации QR включите работающий VPN или настройте Telegram-прокси выше. Для сайта my.telegram.org и для подключения Telegram могут потребоваться разные настройки сети.", "If you are in Russia and a direct Telegram connection is blocked, enable a working VPN or configure a Telegram proxy above to generate the QR code. The my.telegram.org website and Telegram sign-in may require different network settings.")}</p>
        <div class="tgu-buttons">${btn("qr", L("Показать QR", "Show QR"))}<span id="tgu-reconnect" ${state.has_session ? "" : "hidden"}>${btn("connect", L("Восстановить соединение", "Reconnect"))}</span></div>
        <div id="tgu-auth"></div>
        <details class="tgu-help"><summary>${L("Войти по номеру телефона вместо QR", "Sign in with a phone number instead")}</summary>
          ${field("phone", L("Номер с кодом страны", "Phone with country code"), "", "tel", 'autocomplete="tel" placeholder="+7 900 123-45-67"')}
          <p class="tgu-note">${L("Нажмите один раз и дождитесь кода в Telegram. SMS может быть недоступна для сторонних клиентов.", "Press once and wait for the code in Telegram. SMS may be unavailable to third-party clients.")}</p>${btn("phone", L("Получить код в Telegram", "Request a Telegram code"))}
        </details>
        <p class="tgu-note">${L("Пароль и код входа не сохраняются. Локальная сессия защищена вашей учётной записью Windows. Отключить её можно здесь или в Telegram → Устройства.", "Passwords and login codes are not saved. Your local session is protected by your Windows account. Disconnect here or in Telegram → Devices.")}</p>
        <div id="tgu-accounts" class="tgu-accounts-box">${accountsHtml()}</div>
      `, state.connected)}
      <section class="tgu-preferences"><h3>${L("После подключения", "After connecting")}</h3>
        ${check("enabled", L("Telegram Use включён", "Enable Telegram Use"), c.enabled)}
        <label class="tgu-field"><span>${L("Модель по умолчанию", "Default model")}</span>${models(c.model, "model")}</label>
        ${check("allow_writes", L("Разрешить отправку, редактирование и другие изменения", "Allow sending, editing and other changes"), c.allow_writes)}
        ${check("schedule_writes", L("Разрешить изменения задачам планирования без подтверждений", "Allow scheduled tasks to make changes without confirmations"), c.schedule_writes)}
        ${check("commands_enabled", L("Обрабатывать команды в Telegram автоматически", "Process Telegram commands automatically"), c.commands_enabled)}
        ${check("web_enabled", L("Разрешить командам веб-поиск и чтение веб-страниц", "Allow commands to search and read web pages"), c.web_enabled !== false)}
        <label class="tgu-field"><span>${L("Дополнительная инструкция ассистента", "Additional assistant instruction")}</span><textarea class="pinput" rows="4" data-tgu-field="system_prompt" placeholder="${esc(L("Например: отвечай кратко и указывай источники", "For example: keep answers concise and cite sources"))}">${esc(c.system_prompt || "")}</textarea></label>
        <p class="tgu-note">${L("Команды используют общего ассистента MemuAI. !//question вопрос — ответ с доступным веб-поиском. !//summary 50 — последние 50 сообщений здесь; !//summary 50 AI Telegram News — пересказ другого чата в текущей переписке. Ответьте !//voice на голосовое для расшифровки.", "Commands use MemuAI's general assistant. !//question question answers with optional web search. !//summary 50 summarizes the last 50 messages here; !//summary 50 AI Telegram News summarizes another chat in your current conversation. Reply !//voice to a voice message to transcribe it.")}</p>
        <div class="tgu-autos"><div class="tgu-autos-text"><b>${L("Автоматизации", "Automations")}</b><p class="tgu-note">${automationsNote(c)}</p></div>
          ${check("automations_enabled", L("Выполнять автоматизации Telegram", "Run Telegram automations"), c.automations_enabled !== false)}
          <div class="tgu-buttons">${btn("open-automations", L("Открыть автоматизации в Планировании", "Open automations in Planning"))}</div></div>
        ${voiceSettings(c)}
        <details class="tgu-help" id="tgu-command-settings"><summary>${L("Команды и ограничения", "Commands and limits")}</summary><div id="tgu-commands">${c.commands.map(command).join("")}</div><div class="tgu-buttons">${btn("add", L("Добавить команду", "Add command"))}</div>
          <div class="tgu-grid">${field("max_media_mb", L("Лимит вложения, МБ (1–50)", "Media limit, MB (1–50)"), c.max_media_mb, "number", 'min="1" max="50"')}
          ${field("context_chars", L("Максимум символов контекста (1000–500000)", "Context character budget (1,000–500,000)"), c.context_chars, "number", 'min="1000" max="500000"')}
          ${field("max_parallel", L("Одновременных команд (1–4)", "Concurrent commands (1–4)"), c.max_parallel, "number", 'min="1" max="4"')}</div>
        </details><p class="tgu-note">${L("Доступны облачные чаты, изображения и видео. Секретные чаты Telegram сторонним клиентам недоступны. Базовое форматирование работает без Premium.", "Cloud chats, images and video are supported. Telegram secret chats are unavailable to third-party clients. Basic formatting works without Premium.")}</p>
      </section><div class="tgu-footer"><span id="tgu-save-status" role="status" aria-live="polite">${L("Изменения сохраняются автоматически", "Changes are saved automatically")}</span><div class="tgu-buttons">${btn("save", L("Сохранить сейчас", "Save now"))}<span id="tgu-disconnect" ${state.has_session ? "" : "hidden"}>${btn("disconnect", L("Отключить аккаунт", "Disconnect account"))}</span></div></div>`;
  }
  async function call(action, data = {}) {
    const response = await fetch(API, action ? {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action, ...data})} : {});
    const result = await response.json();
    if (!response.ok) {
      if (result.retry_after > 0) retryAt = Date.now() + Math.min(86400, result.retry_after) * 1000;
      throw new Error(errorText(result.error) || `HTTP ${response.status}`);
    }
    return result;
  }
  function connection() {
    if (!state) return;
    const status = document.getElementById("tgu-status");
    const account = state.account || {};
    if (status) {
      status.textContent = state.connected ? `${L("Подключено", "Connected")}: ${account.name || account.username || account.id}` : state.has_session ? L("Соединение с Telegram прервано. Нажмите «Восстановить соединение».", "Telegram disconnected. Select Reconnect.") : state.configured ? L("Данные приложения готовы. Войдите по QR или номеру телефона.", "App credentials are ready. Sign in with QR or your phone number.") : L("Начните с шага 1: получите данные приложения Telegram.", "Start at step 1: get your Telegram app credentials.");
      if (state.last_error) status.textContent += " · " + errorText(state.last_error);
      status.classList.toggle("on", !!state.connected);
    }
    document.querySelectorAll("#tgu-root .tgu-step-number").forEach((marker, i) => { const done = i === 0 ? state.configured : state.connected; marker.textContent = done ? "✓" : String(i + 1); marker.classList.toggle("is-done", !!done); });
    for (const id of ["tgu-reconnect", "tgu-disconnect"]) { const el = document.getElementById(id); if (el) el.hidden = !state.has_session; }
    const remaining = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
    const waitNote = document.getElementById("tgu-wait"); if (waitNote) { waitNote.hidden = remaining === 0; waitNote.textContent = remaining ? L("Следующая попытка входа будет доступна через ", "Sign-in will be available again in ") + remaining + L(" с. Telegram ограничил запросы.", " s. Telegram rate-limited requests.") : ""; }
    document.querySelectorAll('#tgu-root [data-tgu="phone"], #tgu-root [data-tgu="qr"], #tgu-root [data-tgu="connect"]').forEach(b => { b.disabled = remaining > 0 || state.connected; b.title = remaining ? L("Telegram просит подождать ", "Telegram requires waiting ") + remaining + L(" с", " s") : ""; });
    const chip = document.getElementById("tgu-value"); if (chip) chip.textContent = state.connected ? (account.name || L("Подключено", "Connected")) : L("Не подключено", "Not connected");
    const auth = document.getElementById("tgu-auth");
    if (!auth || auth.dataset.phase === state.flow.phase && state.flow.phase !== "qr") return;
    const phase = state.flow.phase;
    if (phase === "qr") {
      if (qrURL !== state.flow.url || !auth.querySelector("img")) {
        qrURL = state.flow.url;
        auth.innerHTML = `<img class="tgu-qr" alt="Telegram login QR" src="${API}/qr.png?t=${Date.now()}"><p>${L("Telegram на телефоне → Настройки → Устройства → Подключить устройство. QR обновляется автоматически.", "Telegram on your phone → Settings → Devices → Link Desktop Device. QR refreshes automatically.")}</p>${btn("cancel",L("Отменить вход","Cancel login"))}`;
      }
    } else if (phase === "code") auth.innerHTML = `${field("code", L("Код из Telegram", "Code from Telegram"), "", "text", 'inputmode="numeric" autocomplete="one-time-code"')}${btn("code", L("Подтвердить код", "Confirm code"))}`;
    else if (phase === "password") auth.innerHTML = `${field("password", L("Облачный пароль Telegram (2FA)", "Telegram cloud password (2FA)"), "", "password", 'autocomplete="current-password"')}${btn("password", L("Войти", "Sign in"))}`;
    else auth.innerHTML = phase === "expired" ? `<p>${L("QR истёк. Нажмите «Показать QR», чтобы получить новый.", "QR expired. Select Show QR to get a new one.")}</p>`
      : phase === "duplicate" ? `<p>${L("Этот аккаунт уже подключён — войдите в другой.", "This account is already connected — sign in to another one.")}</p>` : "";
    auth.dataset.phase = phase;
  }
  /* Several Telegram accounts: the main one signs in above, the others here, each with its own QR code. */
  const accountRows = () => (state && Array.isArray(state.accounts) ? state.accounts : []);
  const accountTitle = a => { const me = a.account || {}; return a.label || me.name || (me.username ? "@" + me.username : "") || (a.main ? L("Основной аккаунт", "Main account") : L("Новый аккаунт", "New account")); };
  const accBtn = (action, id, label, primary = false) => `<button type="button" class="pbtn ${primary ? "" : "ghost"}" data-tgu="${action}" data-tgu-acc-id="${esc(id)}">${esc(label)}</button>`;
  const accField = (key, label, type, attrs) => field(key, label, "", type, attrs).replace('data-tgu-field="', 'data-tgu-acc-input="');
  function automationsNote(c) {
    const n = (c.automations || []).length;
    const intro = L("Правила «если … — сделай …»: расшифровать голосовое от нужного человека, пересказать новый пост канала в Избранное, прислать утреннюю сводку чатов.", "“If … then …” rules: transcribe voice messages from a chosen person, summarize new channel posts into Saved Messages, send a morning digest of your chats.");
    return esc(intro) + " " + esc(n ? L(`Сейчас правил: ${n}.`, `Rules now: ${n}.`) : L("Правил пока нет.", "No rules yet."));
  }
  function accountAuthHtml(a) {
    const phase = a.flow?.phase || "idle", id = a.id;
    const cancel = accBtn("acc-cancel", id, L("Отменить вход", "Cancel sign-in"));
    const phone = `<details class="tgu-help"><summary>${L("Войти по номеру телефона вместо QR", "Sign in with a phone number instead")}</summary>${accField("phone", L("Номер с кодом страны", "Phone with country code"), "tel", 'autocomplete="tel" placeholder="+7 900 123-45-67"')}<div class="tgu-buttons">${accBtn("acc-phone", id, L("Получить код в Telegram", "Request a Telegram code"))}</div></details>`;
    if (phase === "qr") return `<img class="tgu-qr" alt="Telegram login QR" data-tgu-acc-qr="${esc(id)}" src="${API}/qr.png?account=${encodeURIComponent(id)}&t=${Date.now()}"><p>${L("Откройте Telegram на телефоне нужного аккаунта → Настройки → Устройства → Подключить устройство. QR обновляется автоматически.", "Open Telegram on the phone of that account → Settings → Devices → Link Desktop Device. The QR refreshes automatically.")}</p>${phone}<div class="tgu-buttons">${cancel}</div>`;
    if (phase === "code") return `${accField("code", L("Код из Telegram", "Code from Telegram"), "text", 'inputmode="numeric" autocomplete="one-time-code"')}<div class="tgu-buttons">${accBtn("acc-code", id, L("Подтвердить код", "Confirm code"), true)}${cancel}</div>`;
    if (phase === "password") return `${accField("password", L("Облачный пароль Telegram (2FA)", "Telegram cloud password (2FA)"), "password", 'autocomplete="current-password"')}<div class="tgu-buttons">${accBtn("acc-password", id, L("Войти", "Sign in"), true)}${cancel}</div>`;
    const why = phase === "duplicate" ? L("Этот аккаунт уже подключён — войдите в другой.", "This account is already connected — sign in to another one.")
      : phase === "expired" ? L("QR истёк — покажите новый.", "The QR expired — show a new one.") : L("Покажите QR-код и отсканируйте его телефоном с нужным аккаунтом.", "Show the QR code and scan it with the phone of that account.");
    return `<p>${esc(why)}</p>${phone}<div class="tgu-buttons">${accBtn("acc-qr", id, L("Показать QR", "Show QR"), true)}${cancel}</div>`;
  }
  function accountsHtml() {
    const rows = accountRows();
    if (!state || !state.configured || !rows.some(a => a.has_session)) return "";
    const head = `<div class="tgu-accounts-head"><h4>${L("Аккаунты Telegram", "Telegram accounts")}</h4><p class="tgu-note">${rows.length > 1
      ? L("У каждого аккаунта свои команды !// и свои автоматизации. Название видно только в MemuAI.", "Each account has its own !// commands and automations. The label is shown only in MemuAI.")
      : L("Подключите ещё один аккаунт — например, рабочий. Команды и автоматизации будут работать в каждом, MemuAI отвечает от имени того аккаунта, где пришло сообщение.", "Connect one more account, e.g. a work one. Commands and automations work in each; MemuAI replies as the account that received the message.")}</p></div>`;
    const list = rows.length > 1 || pendingAccount ? `<div class="tgu-acc-list">${rows.map(a => {
      const me = a.account || {}, name = accountTitle(a), signing = ["qr", "code", "password"].includes(a.flow?.phase);
      const stateText = a.connected ? L("подключён", "connected") : a.has_session ? L("нет связи", "offline") : signing ? L("выполняется вход…", "signing in…") : L("вход не выполнен", "signed out");
      const letter = (String(name).match(/[\p{L}\p{N}]/u) || ["#"])[0].toUpperCase();
      const tools = a.main ? "" : a.has_session ? accBtn("acc-disconnect", a.id, L("Отключить", "Disconnect"))
        : a.id === pendingAccount ? "" : `${accBtn("acc-signin", a.id, L("Войти", "Sign in"), true)}${accBtn("acc-disconnect", a.id, L("Убрать", "Remove"))}`;
      return `<div class="tgu-acc ${a.connected ? "is-on" : ""}" data-tgu-acc-row="${esc(a.id)}"><span class="tgu-acc-ava" aria-hidden="true">${esc(letter)}</span>
        <div class="tgu-acc-main"><b>${esc(name)}</b><small>${[me.username ? "@" + esc(me.username) : "", a.main ? esc(L("основной", "main")) : "", `<span class="tgu-acc-state ${a.connected ? "on" : ""}">${esc(stateText)}</span>`].filter(Boolean).join(" · ")}</small>
          ${a.last_error && !a.connected && !signing ? `<small class="tgu-acc-error">${esc(errorText(a.last_error))}</small>` : ""}</div>
        <div class="tgu-acc-tools">${a.has_session ? `<label class="tgu-check" title="${esc(L("Обрабатывать команды !// в этом аккаунте", "Handle !// commands in this account"))}"><input type="checkbox" data-tgu-acc-commands="${esc(a.id)}" ${a.commands !== false ? "checked" : ""}> ${L("Команды !//", "!// commands")}</label>` : ""}${tools}</div>
        ${a.has_session || a.main ? `<label class="tgu-acc-label"><span>${L("Название в MemuAI", "Label in MemuAI")}</span><input class="pinput" data-tgu-acc-label="${esc(a.id)}" value="${esc(a.label || "")}" maxlength="80" placeholder="${esc(me.name || L("Например: Рабочий", "For example: Work"))}"></label>` : ""}
        ${a.id === pendingAccount && !a.connected ? `<div class="tgu-acc-auth">${accountAuthHtml(a)}</div>` : ""}</div>`;
    }).join("")}</div>` : "";
    const add = rows.length >= 10 || pendingAccount || !state.has_session ? "" : `<div class="tgu-buttons">${btn("acc-add", L("Добавить аккаунт", "Add an account"))}</div>`;
    return `${head}${list}${add}`;
  }
  let accountsView = "", accountQr = "";
  function paintAccounts(force = false) {
    const box = document.getElementById("tgu-accounts"); if (!box || !state) return;
    const pending = accountRows().find(a => a.id === pendingAccount);
    if (pendingAccount && (!pending || pending.connected)) {
      if (pending?.connected) notice(L("Аккаунт подключён: ", "Account connected: ") + accountTitle(pending));
      pendingAccount = "";
    }
    const view = JSON.stringify([state.configured, state.has_session, pendingAccount, accountRows().map(a => [a.id, a.label, a.connected, a.has_session, a.flow?.phase, a.account?.name, a.account?.username, a.commands, a.last_error])]);
    const url = pending?.flow?.phase === "qr" ? pending.flow.url || "" : "";
    const typing = box.contains(document.activeElement) && document.activeElement.matches("input, textarea");
    if (force || (view !== accountsView && !typing)) { accountsView = view; accountQr = url; box.innerHTML = accountsHtml(); return; }
    if (url && url !== accountQr) {
      accountQr = url;
      const img = box.querySelector(`img[data-tgu-acc-qr="${CSS.escape(pendingAccount)}"]`);
      if (img) img.src = `${API}/qr.png?account=${encodeURIComponent(pendingAccount)}&t=${Date.now()}`;
    }
  }
  async function accountAct(action, el) {
    if (busy || !state) return;
    const id = el?.dataset?.tguAccId || pendingAccount || "";
    const root = document.getElementById("tgu-root"), box = document.getElementById("tgu-accounts");
    const input = key => box?.querySelector(`[data-tgu-acc-input="${key}"]`);
    busy = true; root?.setAttribute("aria-busy", "true");
    box?.querySelectorAll("[aria-invalid]").forEach(x => x.removeAttribute("aria-invalid"));
    box?.querySelectorAll(".tgu-field-error").forEach(x => { x.hidden = true; });
    let failed = false;
    try {
      if (action === "acc-add") {
        if (savePromise) await savePromise;
        state = await call("add_account");
        pendingAccount = state.pending_account || "";
        if (pendingAccount) state = await call("qr", {account: pendingAccount});
      } else if (action === "acc-signin" || action === "acc-qr") {
        pendingAccount = id;
        state = await call("qr", {account: id});
      } else if (action === "acc-phone") {
        const phone = input("phone");
        if (!/^\+[0-9 ()-]{7,29}$/.test(phone?.value?.trim() || "")) fieldError(phone, L("Введите номер с + и кодом страны, например +7 900 123-45-67.", "Enter a number with + and a country code, e.g. +1 202 555 0100."));
        state = await call("phone", {account: id, phone: phone.value.trim()});
      } else if (action === "acc-code") {
        state = await call("code", {account: id, code: input("code")?.value || ""});
      } else if (action === "acc-password") {
        state = await call("password", {account: id, password: input("password")?.value || ""});
      } else if (action === "acc-cancel") {
        state = await call("cancel", {account: id});
        if (id === pendingAccount) pendingAccount = "";
      } else if (action === "acc-disconnect") {
        const row = accountRows().find(a => a.id === id);
        const ask = typeof askConfirm === "function" ? askConfirm : null;
        if (ask && !(await ask(row?.has_session ? L("Отключить аккаунт?", "Disconnect the account?") : L("Убрать аккаунт?", "Remove the account?"), row ? accountTitle(row) : ""))) return;
        state = await call("disconnect", {account: id});
        if (id === pendingAccount) pendingAccount = "";
        notice(L("Аккаунт отключён. Его автоматизации не будут работать, пока вы снова не войдёте.", "Account disconnected. Its automations stay idle until you sign in again."));
      }
    } catch (error) {
      failed = true; notice(error.message, true);
    } finally { busy = false; root?.removeAttribute("aria-busy"); paintAccounts(!failed); connection(); }
  }
  async function accountEdited(event) {
    const input = event.target.closest?.("[data-tgu-acc-label], [data-tgu-acc-commands]");
    if (!input?.closest("#tgu-root") || !state) return;
    const id = input.dataset.tguAccLabel || input.dataset.tguAccCommands;
    const data = input.dataset.tguAccLabel !== undefined ? {account: id, label: input.value.trim()} : {account: id, commands: input.checked};
    try { state = await call("account_update", data); saveStatus(L("Все изменения сохранены", "All changes saved")); }
    catch (error) { notice(error.message, true); }
    if (!(document.getElementById("tgu-accounts")?.contains(document.activeElement) && document.activeElement.matches("input"))) paintAccounts(true);
  }
  function openAutomations() {
    snapshotForNavigation();
    try { localStorage.setItem("memuai.plan.tab.v1", "telegram"); } catch (_) {}
    window.MemuPlan?.setTab?.("telegram", false);
    if (typeof showSchedule === "function") showSchedule(true);
    else window.MemuPlan?.open?.({tab: "telegram"});
  }
  function render() {
    const root = document.getElementById("tgu-root"); if (!root) return;
    if (!pendingAccount) pendingAccount = (accountRows().find(a => !a.main && !a.has_session && ["qr", "code", "password"].includes(a.flow?.phase)) || {}).id || "";
    root.innerHTML = content(); qrURL = ""; accountsView = ""; connection(); paintAccounts(true); if (lastNotice) notice(lastNotice);
    root.querySelectorAll("[data-tgu-model]").forEach(s => window.MemuModelPicker?.enhanceSelect?.(s));
  }
  function values(root, nested = false) {
    const out = {};
    root.querySelectorAll("[data-tgu-field]").forEach(input => {
      if (!nested && input.closest("[data-tgu-command]")) return;
      const key = input.dataset.tguField;
      out[key] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
    });
    return out;
  }
  function collect() {
    const root = document.getElementById("tgu-root");
    const data = values(root);
    data.commands = Array.from(root.querySelectorAll("[data-tgu-command]")).map(card => {
      const c = values(card, true);
      c.arguments = Array.from(card.querySelectorAll("[data-tgu-argument]")).map(arg => {
        const a = {};
        arg.querySelectorAll("[data-tgu-arg-field]").forEach(input => {
          const key = input.dataset.tguArgField;
          if ((key === "default" || key === "min" || key === "max") && input.value === "") return;
          a[key] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value;
        });
        if (a.type === "choice") a.choices = String(a.choices || "").split(",").map(v=>v.trim()).filter(Boolean);
        else delete a.choices;
        if (!["number","integer"].includes(a.type)) { delete a.min; delete a.max; }
        return a;
      });
      for (const key of ["allowed_users", "allowed_chats"]) {
        const ids = String(c[key] || "").split(/[,\s]+/).filter(Boolean);
        if (ids.some(v => !/^-?\d+$/.test(v))) fieldError(card.querySelector(`[data-tgu-field="${key}"]`), L("Нужны числовые ID через запятую. Имена пользователей здесь не подходят.", "Enter numeric IDs separated by commas, not usernames."));
        c[key] = ids;
      }
      return c;
    });
    if (!data.proxy && !data.clear_proxy) delete data.proxy;
    if (data.clear_proxy) data.proxy = "";
    delete data.clear_proxy;
    for (const key of ["phone", "code", "password"]) delete data[key];
    return data;
  }
  function notice(text, error = false) {
    const el = document.getElementById("tgu-feedback"); if (!el) return;
    el.hidden = false; el.textContent = text; el.classList.toggle("is-error", error); el.setAttribute("role", error ? "alert" : "status");
  }
  function fieldError(input, text) {
    if (input) {
      input.setAttribute("aria-invalid", "true");
      const note = input.closest(".tgu-field")?.querySelector(".tgu-field-error");
      if (note) { note.textContent = text; note.hidden = false; }
      let parent = input.parentElement; while (parent) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
      if (!quietValidation) input.focus();
    }
    throw new Error(text);
  }
  function validate(data, login = false) {
    const root = document.getElementById("tgu-root");
    const get = key => root.querySelector(`[data-tgu-field="${key}"]`);
    const id = String(data.api_id || "").trim();
    if (id && (!/^\d+$/.test(id) || Number(id) < 1 || Number(id) > 2147483647)) fieldError(get("api_id"), L("API ID — целое положительное число с my.telegram.org, без пробелов.", "API ID is a positive integer from my.telegram.org, without spaces."));
    const hash = String(data.api_hash || "").trim();
    if (hash && !/^[0-9a-f]{32}$/i.test(hash)) fieldError(get("api_hash"), L("API hash должен содержать ровно 32 символа: цифры и буквы a–f.", "API hash must contain exactly 32 hexadecimal characters (0–9 and a–f)."));
    if (login || data.enabled || id && Number(id) !== Number(state.config.api_id || 0)) {
      if (!id) fieldError(get("api_id"), L("Вставьте API ID из шага 1. Он нужен и для QR.", "Paste the API ID from step 1. QR requires it too."));
      if (!hash && (!state.has_api_hash || Number(id) !== Number(state.config.api_id))) fieldError(get("api_hash"), L("Вставьте API hash для этого API ID. QR не заменяет эти данные.", "Paste the API hash for this API ID. QR does not replace these credentials."));
    }
    data.api_id = id ? Number(id) : 0;
    if (hash) data.api_hash = hash;
    root.querySelectorAll('input[type="number"][data-tgu-field]').forEach(input => { if (!input.checkValidity()) fieldError(input, L("Проверьте число и допустимый диапазон в подписи.", "Check the number and allowed range shown in its label.")); });
    const triggers = new Set();
    data.commands.forEach((c,i) => {
      const card = root.querySelectorAll("[data-tgu-command]")[i];
      if (!/^!\/\/[A-Za-zА-Яа-яЁё0-9_-]{1,32}$/.test(c.trigger) || triggers.has(c.trigger.toLowerCase())) fieldError(card.querySelector('[data-tgu-field="trigger"]'),L("Команды должны быть уникальными и иметь вид !//name.","Commands must be unique and use the form !//name."));
      triggers.add(c.trigger.toLowerCase());
      const names = new Set();
      c.arguments.forEach((a,j) => {
        const row = card.querySelectorAll("[data-tgu-argument]")[j];
        if (!/^[A-Za-z][A-Za-z0-9_]{0,31}$/.test(a.name) || a.name === "question" || names.has(a.name)) fieldError(row.querySelector('[data-tgu-arg-field="name"]'),L("Укажите уникальное имя латиницей. question зарезервировано.","Use a unique name in Latin letters. question is reserved."));
        names.add(a.name);
        if (a.type === "choice" && !a.choices.length) fieldError(row.querySelector('[data-tgu-arg-field="choices"]'),L("Добавьте хотя бы один вариант.","Add at least one choice."));
        if (a.min != null && a.max != null && a.min > a.max) fieldError(row.querySelector('[data-tgu-arg-field="min"]'),L("Минимум должен быть не больше максимума.","Minimum must not exceed maximum."));
      });
      if (c.kind === "summary" && (!c.arguments.some(a=>a.name === "count" && a.type === "integer" && a.required) || c.arguments.some(a=>a.name === "chat" && a.type !== "chat"))) fieldError(card.querySelector('[data-tgu-field="kind"]'),L("Для пересказа добавьте обязательный целочисленный count и, при необходимости, chat с типом «Название чата».","A summary requires an integer count argument and may include a chat argument of type Chat title."));
    });
  }
  function saveStatus(text, error = false) {
    const el = document.getElementById("tgu-save-status");
    if (el) { el.textContent = text; el.classList.toggle("is-error", error); }
  }
  function dirty() {
    revision++;
    clearTimeout(saveTimer);
    saveStatus(L("Есть несохранённые изменения…", "Unsaved changes…"));
    saveTimer = setTimeout(() => persist().catch(()=>{}), 650);
  }
  async function persist(force = false, captured = null) {
    clearTimeout(saveTimer);
    if (!state || !document.getElementById("tgu-root") && !captured) return;
    if (busy) { saveTimer = setTimeout(() => persist(force,captured).catch(()=>{}),250); return; }
    if (savePromise) {
      await savePromise;
      if (revision > savedRevision || force) return persist(force, captured);
      return;
    }
    if (!force && revision === savedRevision) return;
    let data;
    try {
      quietValidation = !force;
      data = captured || collect();
      if (!captured) validate(data);
    } catch (error) {
      saveStatus(L("Не сохранено: ", "Not saved: ") + error.message, true);
      throw error;
    } finally { quietValidation = false; }
    const seq = revision;
    saveStatus(L("Сохраняю…", "Saving…"));
    savePromise = (async () => {
      try {
        const result = await call("settings", data);
        state = result;

        const root = document.getElementById("tgu-root");
        for (const key of ["api_hash", "proxy"]) {
          const input = root?.querySelector(`[data-tgu-field="${key}"]`);
          if (input && data[key] && input.value === data[key]) input.value = "";
        }
        const clear = root?.querySelector('[data-tgu-field="clear_proxy"]');
        if (clear && data.proxy === "" && clear.checked) clear.checked = false;
        savedRevision = seq;
        saveStatus(revision === seq ? L("Все изменения сохранены", "All changes saved") : L("Есть несохранённые изменения…", "Unsaved changes…"));
        connection();
      } catch (error) {
        saveStatus(L("Не сохранено: ", "Not saved: ") + error.message, true);
        throw error;
      } finally { savePromise = null; }
    })();
    await savePromise;
    if (revision > savedRevision) await persist(false);
  }
  function snapshotForNavigation() {
    if (revision === savedRevision || !document.getElementById("tgu-root")) return;
    try {
      quietValidation = true;
      const data = collect(); validate(data);
      persist(false, data).catch(()=>{});
    } catch (_) {} finally { quietValidation = false; }
  }
  async function act(action) {
    if (busy || !state) return;
    busy = true;
    clearTimeout(saveTimer);
    let sentRevision = revision;
    const root = document.getElementById("tgu-root"); root?.setAttribute("aria-busy", "true");
    root?.querySelectorAll("[aria-invalid]").forEach(input => input.removeAttribute("aria-invalid"));
    root?.querySelectorAll(".tgu-field-error").forEach(el => { el.hidden = true; });
    try {
      if (["qr","phone","connect"].includes(action) && retryAt > Date.now()) throw new Error(L("Telegram ограничил попытки входа. Дождитесь окончания указанного срока.", "Telegram rate-limited sign-in. Wait until the indicated period ends."));
      if (action === "add" || action.startsWith("delete-") || action.startsWith("arg-")) {
        const clearProxy = root.querySelector('[data-tgu-field="clear_proxy"]')?.checked;
        const data = collect(); state.config = {...state.config, ...data};
        if (action === "add") {
          let n = 1; while (state.config.commands.some(c => c.trigger === `!//command${n}`)) n++;
          state.config.commands.push({name:L("Новая команда","New command"),trigger:`!//command${n}`,enabled:true,kind:"question",prompt:"{question}",model:"",context_messages:60,output:"replace",arguments:[],allowed_users:[],allowed_chats:[],max_per_hour:10,max_per_day:50,cooldown_seconds:5,max_input_chars:8000});
        } else if (action.startsWith("arg-add-")) {
          const cmd = state.config.commands[Number(action.slice(8))];
          if (cmd.arguments.length >= 32) throw new Error(L("Доступно до 32 аргументов.","Up to 32 arguments are supported."));
          let n = 1; while (cmd.arguments.some(a=>a.name === `arg${n}`)) n++;
          cmd.arguments.push({name:`arg${n}`,type:"text",required:false});
        } else if (action.startsWith("arg-delete-")) {
          const [i,j] = action.slice(11).split("-").map(Number); state.config.commands[i].arguments.splice(j,1);
        } else state.config.commands.splice(Number(action.slice(7)), 1);

        const hash = data.api_hash || "", proxy = data.proxy || "";
        render(); const hashField = root.querySelector('[data-tgu-field="api_hash"]'); if (hashField) hashField.value = hash;
        const proxyField = root.querySelector('[data-tgu-field="proxy"]'); if (proxyField) proxyField.value = proxy;
        root.querySelector('[data-tgu-field="clear_proxy"]').checked = !!clearProxy;
        root.querySelector("#tgu-command-settings").open = true;
        if (action === "add") { const cards = root.querySelectorAll("[data-tgu-command]"); const card = cards[cards.length - 1]; card.open = true; card.querySelector('[data-tgu-field="name"]')?.focus(); }
        dirty(); return;
      }
      if (["save","qr","phone","connect"].includes(action)) {
        if (savePromise) await savePromise;
        const data = collect(); if (action !== "save") data.enabled = true;
        validate(data, action !== "save");
        if (action === "phone") { const phone = root.querySelector('[data-tgu-field="phone"]'); if (!/^\+[0-9 ()-]{7,29}$/.test(phone?.value?.trim() || "")) fieldError(phone, L("Введите номер с + и кодом страны, например +7 900 123-45-67.", "Enter a number with + and a country code, e.g. +1 202 555-0100.")); }
        if (action !== "save") root.querySelector('[data-tgu-field="enabled"]').checked = true;
        sentRevision = revision;
        state = await call("settings", data);
        savedRevision = sentRevision;
        for (const key of ["api_hash","proxy"]) {
          const input = root.querySelector(`[data-tgu-field="${key}"]`);
          if (input && data[key] && input.value === data[key]) input.value = "";
        }
        clearTimeout(saveTimer);
      }
      if (action !== "save") {
        const data = {};
        const input = root.querySelector(`[data-tgu-field="${action}"]`);
        if (input) data[action] = input.value;
        state = await call(action, data);
      }
      lastNotice = action === "save" ? L("Настройки сохранены.", "Settings saved.") : action === "disconnect" ? L("Аккаунт отключён. Данные API сохранены для следующего входа.", "Account disconnected. API credentials are kept for your next sign-in.") : "";
      if (revision === sentRevision || ["disconnect","cancel"].includes(action)) render();
      else { connection(); if (lastNotice) notice(lastNotice); }
    } catch (error) {
      lastNotice = ""; notice(error.message, true);
    } finally { busy = false; root?.removeAttribute("aria-busy"); connection(); if (revision > savedRevision) { clearTimeout(saveTimer); saveTimer=setTimeout(()=>persist().catch(()=>{}),250); } }
  }
  function listHtml() {
    return window.MemuSettingsKit.link({sub:"int-telegram-use",label:"Telegram Use",icon:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="m21 4-4 17-6-6-4 3 1-6-5-3 18-5Z"/><path d="m8 12 9-5-6 8"/></svg>',desc:L("Личный аккаунт: чаты, команды ИИ, голосовые, изображения, видео и планирование", "Personal account: chats, AI commands, voice, images, video and scheduling"),value:`<span id="tgu-value">${L("Настроить", "Configure")}</span>`,keywords:"telegram use mtproto proxy прокси телеграм личный аккаунт команды voice"});
  }
  function subsHtml() {
    return window.MemuSettingsKit.sub({id:"int-telegram-use",title:"Telegram Use",desc:L("Подключение личного аккаунта Telegram", "Connect your personal Telegram account"),body:`<div id="tgu-root" class="tgu-root">${content()}</div>`});
  }
  function mount() {
    if (!bound) {
      bound = true;
      document.addEventListener("click", event => {
        const b = event.target.closest("[data-tgu]");
        if (!b?.closest("#tgu-root")) { if (!event.target.closest("#tgu-root")) snapshotForNavigation(); return; }
        event.preventDefault();
        if (b.dataset.tgu === "open-automations") return openAutomations();
        if (b.dataset.tgu.startsWith("acc-")) return accountAct(b.dataset.tgu, b);
        act(b.dataset.tgu);
      }, true);
      document.addEventListener("change", accountEdited);
      document.addEventListener("keydown", event => {
        const input = event.target.closest?.("#tgu-root [data-tgu-acc-input]");
        if (!input || event.key !== "Enter") return;
        event.preventDefault(); accountAct({phone: "acc-phone", code: "acc-code", password: "acc-password"}[input.dataset.tguAccInput], input);
      });
      const edited = event => {
        const input = event.target.closest("[data-tgu-field], [data-tgu-arg-field]");
        if (!input?.closest("#tgu-root") || ["phone","code","password"].includes(input.dataset.tguField)) return;
        input.removeAttribute("aria-invalid");
        if (input.dataset.tguArgField === "type") input.closest("[data-tgu-argument]").dataset.kind = input.value;
        if (input.dataset.tguField === "kind") input.closest("[data-tgu-command]").dataset.kind = input.value;
        if (event.type === "change" && input.dataset.tguField === "kind" && input.value === "summary") {
          const card = input.closest("[data-tgu-command]");
          if (!card.querySelector("[data-tgu-argument]")) card.querySelector("[data-tgu-arguments]").innerHTML = [
            {name:"count",type:"integer",required:true,min:1,max:2000}, {name:"chat",type:"chat",required:false}
          ].map((a,j)=>argument(a,Number(card.dataset.tguCommand),j)).join("");
        }
        if (input.dataset.tguField === "stt_source") {
          const voice = input.closest(".tgu-voice"), local = voice?.querySelector('[data-tgu-field="stt_local_model"]');
          if (local && input.value !== "local") local.value = "";
          const row = voice?.querySelector(".tgu-voice-local"); if (row) row.hidden = input.value !== "local";
          const current = (voiceOptions.sources || {})[input.value], note=voice?.querySelector(".tgu-voice-current");
          if (note) note.textContent = current?.model ? L("Сейчас используется: ","Currently uses: ") + `${current.service_name || ""} · ${current.model}` : L("Выберите скачанную модель ниже.","Choose a downloaded model below.");
        }
        if (input.dataset.tguField === "voice_cleanup_enabled") {
          const controls = input.closest(".tgu-voice")?.querySelector(".tgu-voice-cleanup"); if (controls) controls.hidden = !input.checked;
        }
        dirty();
      };
      document.addEventListener("input", edited);
      document.addEventListener("change", edited);
      window.addEventListener("pagehide", () => {
        if (revision === savedRevision || !document.getElementById("tgu-root")) return;
        try { quietValidation = true; const data = collect(); validate(data); fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:"settings",...data}),keepalive:true}).catch(()=>{}); } catch (_) {} finally { quietValidation=false; }
      });
    }
    clearInterval(timer);
    Promise.all([call(), fetch(API+"/voice-options").then(r=>r.ok?r.json():{}).catch(()=>({}))]).then(([result,options]) => { state = result; voiceOptions=options; revision=savedRevision=0; render(); }).catch(error => { const root = document.getElementById("tgu-root"); if (root) root.textContent = error.message; });
    timer = setInterval(async () => {
      const root = document.getElementById("tgu-root"); if (!root) { clearInterval(timer); return; } if (busy || document.hidden || root.closest(".sx-sub")?.hidden) return;
      try { const result = await call(); if (busy) return; state = {...state, connected:result.connected,account:result.account,flow:result.flow,last_error:result.last_error,has_session:result.has_session,configured:result.configured,accounts:result.accounts||state.accounts,automation_stats:result.automation_stats}; connection(); paintAccounts(); } catch (_) {}
    }, 2500);
  }
  window.MemuTelegramUse = {listHtml, subsHtml, mount};
})();
