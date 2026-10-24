/* MemuAI Proxy — панель управления */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

let TOKEN = sessionStorage.getItem("oh_token") || "";
// Старые версии держали долгоживущую админ-сессию в localStorage. Не переносим
// её в более короткоживущее хранилище: при сетевом доступе лучше войти заново.
localStorage.removeItem("oh_token");
let providersMeta = [];
let accountsCache = [];
let providerFilter = "";
let settingsCache = {};
let logsTimer = null;
let modelsFilter = "";
let overviewCache = null;
let modelsCache = [];
let modelsProviderFilter = "";
let modelsStateFilter = "";
let modelsGroup = "none";
let modelsSelected = new Set();
let accountsSelected = new Set();
let accountsSearch = "";
let accountsSort = "order";
let accountsSelectMode = false;
let logsState = { offset: 0, total: 0, expandAll: false, view: "requests" };
let logsExpanded = new Set();
let logsPromptsExpanded = new Set();
let applogTimer = null;
let metricsPeriod = localStorage.getItem("oh_metrics_period") || "day";
let metricsCache = null;

/* ------------------------------ api ------------------------------ */

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (TOKEN) headers["Authorization"] = "Bearer " + TOKEN;
  let resp;
  try {
    resp = await fetch(path, {
      method: options.method || "GET", headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
      credentials: "same-origin",
    });
  } catch (error) {
    if (error && error.name === "AbortError") throw error;
    throw new Error(LANG === "en" ? "Cannot reach MemuAI Proxy" : "Нет связи с MemuAI Proxy");
  }
  let data = null;
  try { data = await resp.json(); } catch (e) { /* пустой ответ */ }
  // Бэкенд отдаёт сообщение сразу на двух языках — берём подходящее.
  const errorText = data ? (LANG === "en" ? (data.error_en || data.error) : data.error) : "";
  if (resp.status === 401) {
    if (!path.endsWith("/login")) {
      TOKEN = "";
      sessionStorage.removeItem("oh_token");
      showLogin(errorText || "");
    }
    throw new Error(errorText || (LANG === "en" ? "Sign-in required" : "Требуется вход"));
  }
  if (!resp.ok) throw new Error(translateMessage(errorText) || `HTTP ${resp.status}`);
  return data;
}

/* ------------------------------ утилиты ------------------------------ */

function toast(message, type = "ok", ms = 3500) {
  const container = $("#toasts");
  const key = `${type}\u0000${String(message)}`;
  const duplicate = Array.from(container.children).find((item) => item.dataset.toastKey === key);
  if (duplicate) {
    clearTimeout(duplicate._fadeTimer); clearTimeout(duplicate._removeTimer);
    duplicate.style.opacity = "1";
    duplicate._fadeTimer = setTimeout(() => { duplicate.style.opacity = "0"; duplicate.style.transition = "opacity .3s"; }, ms - 300);
    duplicate._removeTimer = setTimeout(() => duplicate.remove(), ms);
    return;
  }
  while (container.children.length >= 3) container.firstElementChild.remove();
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.dataset.toastKey = key;
  el.textContent = message;
  if (type === "err" && window.MemuCompanion) {
    const ask = document.createElement("button");
    ask.type = "button";
    ask.className = "toast-ask";
    ask.textContent = "✦ " + t("comp.ask");
    ask.onclick = () => { el.remove(); MemuCompanion.ask("", { source: "toast", message: String(message) }); };
    el.appendChild(ask);
    ms = Math.max(ms, 7000);
  }
  container.appendChild(el);
  el._fadeTimer = setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, ms - 300);
  el._removeTimer = setTimeout(() => el.remove(), ms);
}
let lastModalFocus = null;
let modalCleanup = null;
let modalGeneration = 0;
function cleanupModalWork() {
  if (!modalCleanup) return;
  const cleanup = modalCleanup;
  modalCleanup = null;
  try { cleanup(); } catch (e) { /* очистка не должна ломать закрытие */ }
}
function onModalClose(cleanup) { modalCleanup = cleanup; }
function wireFieldLabels(root = document) {
  Array.from(root.querySelectorAll(".field")).forEach((field, index) => {
    const label = field.querySelector("label");
    const control = field.querySelector("input:not([type='checkbox']), textarea, select");
    if (!label || !control) return;
    if (!control.id) control.id = `field-control-${index}`;
    if (!label.htmlFor) label.htmlFor = control.id;
  });
}
function openModal(html, wide = false) {
  const box = $("#modal-box");
  const alreadyOpen = $("#modal-back").classList.contains("visible");
  cleanupModalWork();
  if (!alreadyOpen) lastModalFocus = document.activeElement;
  modalGeneration += 1;
  box.className = "modal" + (wide ? " wide" : "");
  box.innerHTML = html;
  wireFieldLabels(box);
  const heading = box.querySelector("h1, h2, h3");
  if (heading) {
    heading.id = "modal-title";
    box.removeAttribute("aria-label");
    box.setAttribute("aria-labelledby", heading.id);
  } else {
    box.removeAttribute("aria-labelledby");
    box.setAttribute("aria-label", t("app.name"));
  }
  $("#modal-back").classList.add("visible");
  $("#modal-back").setAttribute("aria-hidden", "false");
  $("#app").inert = true;
  document.body.classList.add("modal-open");
  queueMicrotask(() => (box.querySelector("input, textarea, button, select") || box).focus());
  return modalGeneration;
}
function closeModal() {
  if (!$("#modal-back").classList.contains("visible")) return;
  cleanupModalWork();
  modalGeneration += 1;
  $("#modal-back").classList.remove("visible");
  $("#modal-back").setAttribute("aria-hidden", "true");
  $("#app").inert = false;
  document.body.classList.remove("modal-open");
  const restore = lastModalFocus;
  lastModalFocus = null;
  if (restore && restore.isConnected) queueMicrotask(() => restore.focus());
}
$("#modal-back").addEventListener("click", (e) => { if (e.target.id === "modal-back") closeModal(); });
document.addEventListener("keydown", (e) => {
  if (!$("#modal-back").classList.contains("visible")) return;
  if (e.key === "Escape") { e.preventDefault(); closeModal(); return; }
  if (e.key !== "Tab") return;
  const focusable = $$("#modal-box button:not([disabled]), #modal-box input:not([disabled]), #modal-box textarea:not([disabled]), #modal-box select:not([disabled]), #modal-box [tabindex]:not([tabindex='-1'])");
  if (!focusable.length) { e.preventDefault(); $("#modal-box").focus(); return; }
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
function confirmDialog(title, text, onYes, confirmLabel = t("acc.btn.del")) {
  openModal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>
    <div class="row-end"><button class="btn" id="m-no">${t("acc.cancel")}</button>
    <button class="btn danger" id="m-yes">${esc(confirmLabel)}</button></div>`);
  $("#m-no").onclick = closeModal;
  $("#m-yes").onclick = async () => {
    closeModal();
    try { await onYes(); } catch (error) { toast(error.message || String(error), "err"); }
  };
}
function locale() { return LANG === "en" ? "en-US" : "ru-RU"; }
function fmtTime(ts) { return Number.isFinite(Number(ts)) && Number(ts) > 0 ? new Date(Number(ts) * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"; }
function fmtDateTime(ts) { return Number.isFinite(Number(ts)) && Number(ts) > 0 ? new Date(Number(ts) * 1000).toLocaleString(locale(), { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"; }
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function maskSecret(value) {
  const text = String(value || "");
  return text.length > 9 ? `${text.slice(0, 4)}••••••${text.slice(-4)}` : "••••••••";
}
function isSecretField(field) {
  return field.type === "password" || /(token|cookie|secret|password|credential|api.?key)/i.test(field.key);
}
function providerMeta(id) { return providersMeta.find((p) => p.id === id) || { title: id, icon: "◆", fields: [] }; }

function structuredFieldHelp(value, className = "help") {
  const text = String(value || "").trim();
  if (!text) return "";
  const steps = /^\s*(?:ШАГИ|STEPS):/i.test(text)
    ? text.replace(/^\s*(?:ШАГИ|STEPS):\s*/i, "").split(/\s*\d+[.)]\s*/).filter(Boolean)
    : text.split(/\n+|(?<=[.!?])\s+(?=[A-ZА-ЯЁ])/u).filter(Boolean);
  return `<div class="${esc(className)}">${steps.length > 1
    ? `<ul class="field-help-list">${steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ul>`
    : `<p>${esc(text)}</p>`}</div>`;
}

function accountSetupGuide(providerId) {
  if (providerId === "geminiweb" && window.GeminiWebSetup) return window.GeminiWebSetup.guide(); // Round 37 (r37-gemini)
  const en = LANG === "en";
  const pair = (ru, eng) => en ? eng : ru;
  const guides = {
    genspark: {
      steps: [
        [pair("Войдите в Genspark", "Sign in to Genspark"), pair("Откройте genspark.ai в обычном браузере и войдите в нужный аккаунт.", "Open genspark.ai in your browser and sign in to the account you want.")],
        [pair("Найдите session_id", "Find session_id"), pair("F12 → Application / «Приложение» → Storage / «Хранилище» → Cookies → https://www.genspark.ai или https://genspark.ai. В фильтре таблицы введите session_id.", "F12 → Application → Storage → Cookies → https://www.genspark.ai or https://genspark.ai. Filter the table by session_id.")],
        [pair("Скопируйте Value", "Copy Value"), pair("Выберите строку session_id и скопируйте только её столбец Value / «Значение». Вставьте в поле Cookie; формат session_id=значение также принимается. Не копируйте всю таблицу Cookies.", "Select session_id and copy its Value column. Paste it into Cookie; session_id=value is also accepted. Do not copy the whole Cookies table.")],
        [pair("Добавьте и проверьте", "Add and check"), pair("Сохраните аккаунт и нажмите «Проверить». Если сервер отклоняет сессию, заново войдите на сайт и замените session_id.", "Save the account and choose Check. If the session is rejected, sign in to the website again and replace session_id.")],
      ], example: "session_id=example",
    },
    grok: {
      steps: [
        [pair("Войдите в Grok", "Sign in to Grok"), pair("Откройте grok.com и убедитесь, что чат доступен под нужным аккаунтом.", "Open grok.com and check that the chat works under the account you want.")],
        [pair("Скопируйте sso или Cookie", "Copy sso or Cookie"), pair("F12 → Application → Cookies → https://grok.com: найдите sso и скопируйте Value. Альтернатива: Network → обновить страницу → запрос к grok.com → Headers → Request Headers → Cookie → Copy value.", "F12 → Application → Cookies → https://grok.com: find sso and copy Value. Alternatively: Network → reload → a grok.com request → Headers → Request Headers → Cookie → Copy value.")],
        [pair("Вставьте и проверьте", "Paste and check"), pair("Можно вставить значение sso, пару sso=значение или весь Cookie. Кнопка «Вставить из буфера» также извлекает Cookie из скопированного cURL. Добавьте аккаунт и нажмите «Проверить».", "Paste the sso value, sso=value, or the full Cookie header. Paste from clipboard also extracts Cookie from a copied cURL request. Add the account and choose Check.")],
      ], example: "sso=example",
    },
    kiro: {
      steps: [
        [pair("Выберите способ входа", "Choose how to sign in"), pair("Нажмите «Войти через браузер» для OAuth или «Войти через Builder ID» для AWS Builder ID. Поле JSON при таком входе оставьте пустым.", "Choose Sign in via browser for OAuth, or Sign in via Builder ID for AWS Builder ID. Leave the JSON field empty when using these buttons.")],
        [pair("Завершите вход", "Finish signing in"), pair("Пройдите вход в открывшемся браузере и вернитесь в Proxy. Дождитесь сообщения об успешном подключении. Для device flow введите показанный код на открытой странице.", "Complete sign-in in the browser and return to the Proxy. Wait for the connection success message. For device flow, enter the displayed code on the opened page.")],
        [pair("Либо импортируйте JSON токена", "Or import the token JSON"), pair("Если Kiro уже авторизован, откройте %USERPROFILE%\\.aws\\sso\\cache\\kiro-auth-token.json и вставьте весь JSON в поле токена. Регион обычно us-east-1; собственный endpoint заполняйте только при необходимости.", "If Kiro is already signed in, open %USERPROFILE%\\.aws\\sso\\cache\\kiro-auth-token.json and paste the entire JSON into the token field. The region is usually us-east-1; use a custom endpoint only when needed.")],
      ],
    },
    antigravity: {
      steps: [
        [pair("Войдите через браузер", "Sign in via browser"), pair("Нажмите кнопку входа, выберите нужный Google-аккаунт и завершите OAuth. Токены заполнять вручную не требуется.", "Choose the sign-in button, select the Google account you want, and complete OAuth. You do not need to fill in tokens manually.")],
        [pair("Дождитесь подключения", "Wait for the connection"), pair("Вернитесь в Proxy и дождитесь подтверждения. Project ID обычно определяется при первом запросе.", "Return to the Proxy and wait for confirmation. Project ID is normally discovered on the first request.")],
        [pair("Проверьте доступ", "Check access"), pair("Нажмите «Проверить» на карточке аккаунта. Для ручного импорта нужны действующий access token и, по возможности, refresh token для обновления сессии.", "Choose Check on the account card. Manual import needs a valid access token and, when available, a refresh token to renew the session.")],
      ],
    },
    custom: {
      steps: [
        [pair("Укажите корень API", "Set the API root"), pair("Вставьте Base URL из документации провайдера, обычно https://api.example.com/v1. Не добавляйте /chat/completions. Для известных сервисов можно вернуться к выбору и использовать готовый пресет.", "Use the Base URL from the provider documentation, usually https://api.example.com/v1. Do not append /chat/completions. You can return to the picker and use a preset for a known service.")],
        [pair("Добавьте ключ и префикс", "Add a key and prefix"), pair("API-ключ берётся в кабинете провайдера; для локального API он может быть не нужен. Префикс должен быть уникальным: латинские буквы, цифры, дефис. Модели появятся как префикс-модель.", "Get the API key from the provider dashboard; local APIs may not require one. Choose a unique prefix using Latin letters, digits, and dashes. Models appear as prefix-model.")],
        [pair("Выберите модели", "Choose models"), pair("Оставьте список пустым, чтобы Proxy загрузил /models при проверке, или укажите точные ID через запятую. Image-модели укажите отдельно, если API поддерживает /images/generations.", "Leave the list empty to load /models when checking the account, or enter exact IDs separated by commas. List image models separately when the API supports /images/generations.")],
        [pair("Проверьте подключение", "Check the connection"), pair("Дополнительные заголовки — JSON-объект в двойных кавычках; пул ключей — по одному ключу в строке. Добавьте аккаунт и нажмите «Проверить».", "Extra headers use a JSON object with double quotes; key pools use one key per line. Add the account and choose Check.")],
      ],
    },
  };
  const guide = guides[providerId];
  if (!guide) return "";
  return `<details class="provider-guide" ${["genspark", "grok"].includes(providerId) ? "open" : ""}>
    <summary>${esc(pair("Как подключить аккаунт", "How to connect this account"))}</summary>
    <ol class="provider-guide-steps">${guide.steps.map(([title, body]) => `<li><b>${esc(title)}</b><p>${esc(body)}</p></li>`).join("")}</ol>
    ${guide.note ? `<p class="provider-guide-note">${esc(guide.note)}</p>` : ""}
    ${guide.example ? `<div class="provider-guide-example"><span>${esc(pair("Пример формата (вымышленные значения)", "Format example (dummy values)"))}</span><code>${esc(guide.example)}</code><button type="button" class="btn small provider-guide-copy" data-copy-guide>${esc(pair("Копировать пример", "Copy example"))}</button></div>` : ""}
  </details>`;
}

function accountFieldHelp(providerId, field) {
  if (["geminiweb", "genspark", "grok"].includes(providerId) && /cookies?/.test(field.key)) {
    const hint = LANG === "en" ? "Follow the steps above. Paste the copied value here; import and normalization happen locally." : "Следуйте инструкции выше. Вставьте скопированное значение сюда; импорт и очистка выполняются локально.";
    return structuredFieldHelp(hint, "field-hint");
  }
  return structuredFieldHelp(field.help, "field-hint");
}

function cookieFromCopiedRequest(value) {
  const raw = String(value || "").trim().replace(/(?:\\|\^)\r?\n/g, " ").replace(/\^"/g, '"');
  if (!raw || raw.length > 256000) return "";
  const curlHeader = raw.match(/(?:^|\s)(?:-H|--header)\s+(?:'Cookie\s*:\s*([^']*)'|"Cookie\s*:\s*([^"]*)")/i);
  const curlCookie = raw.match(/(?:^|\s)(?:-b|--cookie)\s+(?:'([^']*)'|"([^"]*)")/i);
  const header = raw.match(/(?:^|\r?\n)\s*cookie\s*:\s*([^\r\n]*)/i);
  const found = curlHeader ? (curlHeader[1] ?? curlHeader[2]) : curlCookie ? (curlCookie[1] ?? curlCookie[2]) : header?.[1];
  if (/^curl\b/i.test(raw) && found === undefined) return "";
  if (curlCookie && !curlHeader && !String(found).includes("=")) return "";
  return String(found ?? raw).trim();
}

function wireMusicAccount(prefix) {
  const config = document.getElementById(prefix + "music_config"), models = document.getElementById(prefix + "music_models");
  if (!config || !models) return;
  const en = LANG === "en", wrapper = document.createElement("details");
  wrapper.id = "proxy-music-setup"; wrapper.className = "proxy-music-setup"; wrapper.open = !!models.value;
  config.closest(".field").before(wrapper);
  wrapper.innerHTML = `<summary><span>♫</span>${en ? "Music generation" : "Генерация музыки"}</summary><p>${en ? "One Proxy account provides music models to Studio and /v1/audio/music. Choose a schema, fill the account key above, and save." : "Один аккаунт Proxy предоставляет музыкальные модели для Studio и /v1/audio/music. Выберите схему, заполните ключ аккаунта выше и сохраните."}</p><div class="proxy-music-preset"><select aria-label="${en ? "Music API schema" : "Схема музыкального API"}"></select><button type="button" class="btn small">${en ? "Apply address and schema" : "Подставить адрес и схему"}</button></div><p class="proxy-music-schema-note"></p><div class="proxy-music-models"></div><details class="proxy-music-advanced"><summary>${en ? "Request template and response paths" : "Шаблон запроса и пути ответа"}</summary><p>${en ? "Placeholders: {{model}}, {{prompt}}, {{lyrics}}, {{style}}, {{duration}}, {{tempo}}, {{instrumental}}. Whole placeholders preserve numbers and booleans. Results: raw audio, URL, base64 or hex; async jobs use poll_url with {id} on the same API origin. Auth: bearer, header with auth_header/auth_prefix, google, anthropic or none; extra account headers are reused. Keep credentials in the account fields." : "Подстановки: {{model}}, {{prompt}}, {{lyrics}}, {{style}}, {{duration}}, {{tempo}}, {{instrumental}}. Целая подстановка сохраняет числа и boolean. Ответ: аудиофайл, URL, base64 или hex; асинхронные задания используют poll_url с {id} на том же API. Авторизация: bearer, header с auth_header/auth_prefix, google, anthropic или none; дополнительные заголовки аккаунта используются повторно. Секреты храните в полях аккаунта."}</p></details>`;
  wrapper.querySelector(".proxy-music-models").appendChild(models.closest(".field"));
  wrapper.querySelector(".proxy-music-advanced").appendChild(config.closest(".field"));
  config.rows = 12;
  const select = wrapper.querySelector("select"), button = wrapper.querySelector("button"), note = wrapper.querySelector(".proxy-music-schema-note");
  const describe = () => {
    const preset = musicPresetsCache.find((item) => item.id === select.value);
    const text = preset?.id === "minimax" ? (en ? "MiniMax documents music API access for existing paying accounts only. Duration and BPM are prompt instructions." : "По документации MiniMax музыкальный API доступен существующим платным аккаунтам. Длительность и BPM задаются как пожелания.")
      : preset?.id === "elevenlabs" ? (en ? "ElevenLabs returns an audio file. Length is explicit; lyrics are included in the prompt. The service sets model access and limits." : "ElevenLabs возвращает аудиофайл. Длительность задаётся явно, текст песни включается в промпт. Доступ к моделям и лимиты определяет сервис.")
      : (en ? "Match the request template and result paths to your API documentation. Unsupported protocols need a dedicated adapter." : "Сопоставьте шаблон и пути результата с документацией своего API. Для других протоколов нужен отдельный адаптер.");
    note.innerHTML = esc(text) + (preset?.docs ? ` <a href="${esc(preset.docs)}" target="_blank" rel="noopener noreferrer">${en ? "API documentation" : "Документация API"} ↗</a>` : "");
  };
  const draw = () => {
    let current = "generic"; try { current = JSON.parse(config.value || "{}").preset || "generic"; } catch (error) { /* preserve the editable draft */ }
    select.innerHTML = musicPresetsCache.map((item) => `<option value="${esc(item.id)}">${esc(item.title)}</option>`).join("");
    select.value = current; describe();
  };
  select.onchange = describe;
  button.onclick = () => {
    const preset = musicPresetsCache.find((item) => item.id === select.value); if (!preset) return;
    config.value = JSON.stringify(preset.config, null, 2);
    const base = document.getElementById(prefix + "base_url"), name = document.getElementById(prefix + "name");
    if (preset.base_url && base) base.value = preset.base_url;
    if (preset.models.length) models.value = preset.models.join(", ");
    if (name && !name.value) name.value = preset.id === "generic" ? "music-api" : preset.id + "-music";
    wrapper.open = true; describe(); config.dispatchEvent(new Event("input", { bubbles: true }));
  };
  if (musicPresetsCache.length) draw();
  else { button.disabled = true; api("/api/admin/music/presets").then((data) => { musicPresetsCache = data.presets || []; if (wrapper.isConnected) { draw(); button.disabled = false; } }).catch((error) => { note.textContent = error.message; }); }
}

function wireAccountGuides(providerId, prefix, accountId = "") {
  $$("#modal-box [data-copy-guide]").forEach((button) => button.onclick = () => copyText(button.closest(".provider-guide-example").querySelector("code").textContent));
  if (providerId === "custom") wireMusicAccount(prefix);
  if (providerId === "geminiweb" && window.GeminiWebSetup) return window.GeminiWebSetup.wire(prefix, accountId); // Round 37 (r37-gemini)
  if (!["genspark", "grok"].includes(providerId)) return;
  const key = providerId === "grok" ? "cookies" : "cookie";
  const field = document.getElementById(prefix + key);
  if (!field) return;
  const tools = document.createElement("div");
  tools.className = "cookie-import-tools";
  const button = document.createElement("button");
  button.type = "button"; button.className = "btn small";
  button.textContent = LANG === "en" ? "Paste from clipboard" : "Вставить из буфера";
  tools.appendChild(button); field.after(tools);
  const normalize = (value) => {
    let cookie = cookieFromCopiedRequest(value);
    if (providerId === "genspark" && cookie) {
      const session = cookie.match(/(?:^|;\s*)session_id=([^;]+)/i);
      if (session) cookie = "session_id=" + session[1].trim();
    }
    return cookie;
  };
  button.onclick = async () => {
    try {
      const cookie = normalize(await navigator.clipboard.readText());
      if (!cookie) return toast(LANG === "en" ? "No Cookie value found in the copied request." : "В скопированном запросе не найдено значение Cookie.", "warn");
      field.value = cookie; field.dispatchEvent(new Event("input", { bubbles: true })); field.focus();
      toast(LANG === "en" ? "Cookie pasted locally. You can check and edit it before saving." : "Cookie вставлена локально. Её можно проверить и изменить перед сохранением.");
    } catch (error) {
      toast(LANG === "en" ? "Clipboard access is unavailable. Paste into the field with Ctrl+V." : "Нет доступа к буферу. Вставьте текст в поле через Ctrl+V.", "warn");
      field.focus();
    }
  };
  field.addEventListener("paste", (event) => {
    const raw = event.clipboardData?.getData("text");
    if (!raw || !/^(?:curl\b|cookie\s*:)/i.test(raw.trim())) return;
    const cookie = normalize(raw);
    if (!cookie) return;
    event.preventDefault(); field.value = cookie; field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/* Бэкенд отдаёт и русские, и английские подписи. Выбираем нужные один раз,
   чтобы в английском интерфейсе не оставалось русских описаний и подсказок. */
function localizeProviders(list) {
  const en = LANG === "en";
  return (list || []).map((p) => ({
    ...p,
    title: en && p.title_en ? p.title_en : p.title,
    description: en && p.description_en ? p.description_en : p.description,
    fields: (p.fields || []).map((f) => ({
      ...f,
      label: en && f.label_en ? f.label_en : f.label,
      help: en && f.help_en ? f.help_en : f.help,
      placeholder: en && f.placeholder_en ? f.placeholder_en : f.placeholder,
    })),
    settings: (p.settings || []).map((f) => ({
      ...f,
      label: en && f.label_en ? f.label_en : f.label,
      help: en && f.help_en ? f.help_en : f.help,
      placeholder: en && f.placeholder_en ? f.placeholder_en : f.placeholder,
    })),
  }));
}

async function fetchProviders(force = false) {
  if (providersMeta.length && !force) return providersMeta;
  const data = await api("/api/admin/providers");
  providersMeta = localizeProviders(data.providers);
  return providersMeta;
}

/* Сообщения провайдеров исторически приходят по-русски. В английском интерфейсе
   переводим известные фразы, чтобы в статусах и логах не было смеси языков. */
/* Готовые фразы идут первыми: целую фразу всегда можно перевести правильно,
   а перевод по отдельным словам оставляет калечные гибриды. */
const MSG_PHRASES = [
  ["оценка по 5-часовым окнам за 7 дней", "estimate from 5-hour windows over 7 days"],
  ["получено с сервера", "received from the server"],
  ["получен с сервера", "received from the server"],
  ["неожиданная ошибка адаптера", "unexpected adapter error"],
  ["соединение с провайдером прервано", "connection to the provider was lost"],
  ["провайдер не начал отвечать за", "the provider did not start answering within"],
  ["провайдер завершил поток без текста", "the provider ended the stream with no text"],
  ["поток провайдера оборвался после начала ответа", "the provider stream broke off after the answer had started"],
  ["нет доступных аккаунтов", "no usable accounts"],
  ["все аккаунты", "every account"],
  ["ошибка сети", "network error"],
  ["ошибка на стороне провайдера", "provider-side error"],
  ["временная ошибка", "temporary error"],
  ["временная сетевая ошибка", "temporary network error"],
  ["проверка пройдена", "check passed"],
  ["ответил успешно", "answered successfully"],
  ["фоновая проверка", "background check"],
  ["кастомное подключение", "custom connection"],
  ["найдено в бандле", "found in the bundle"],
  ["импорт из genspark2api", "imported from genspark2api"],
  ["обновлено — нужна проверка", "updated — needs a check"],
  ["включён — нужна проверка", "enabled — needs a check"],
  ["копия — нужна проверка", "copy — needs a check"],
  ["не удалось", "failed to"],
  ["не авторизован", "is not authorized"],
  ["дневной лимит", "daily limit"],
  ["лимит исчерпан", "quota is used up"],
  ["лимит запросов", "request rate limit"],
  ["повторите попытку", "try again"],
  ["нет связи", "no connection"],
  ["нет ответа", "no response"],
  ["срок действия истёк", "expired"],
  ["срок действия истек", "expired"],
  ["требуется вход", "sign-in required"],
  ["не поддерживается", "not supported"],
  ["кука не авторизована", "the cookie is not authorized"],
  ["кука отклонена", "the cookie was rejected"],
  ["сеть недоступна", "network unavailable"],
  ["обрыв потока", "stream broke off"],
  ["анонимный доступ", "anonymous access"],
  ["нет активного flow", "no active sign-in flow"],
  ["ожидание входа истекло", "the sign-in wait timed out"],
  ["не удалось занять порт", "could not bind port"],
  ["обмен кода не удался", "code exchange failed"],
  ["обновите её в браузере", "refresh it in the browser"],
  ["скопируйте значение", "copy the value"],
  ["перезайдите в", "sign in again to"],
  ["возможно", "possibly"],
  ["ключ принят, но на балансе нет средств", "the key is accepted, but the balance is empty"],
  ["ключ принят, но сейчас исчерпан лимит запросов", "the key is accepted, but its request limit is used up right now"],
  ["адрес отвечает, но список моделей /models недоступен", "the address answers, but the /models list is unavailable"],
  ["ответ /models не разобран", "the /models answer could not be parsed"],
  ["ключ отклонён", "the key was rejected"],
  ["подключено", "connected"],
  ["авторизован", "signed in"],
  ["работает", "works"],
];

/* Отдельные слова переводим только целиком: границы слова не дают превратить
   «сервера» в «servera». Формы одного слова перечислены явно — так в
   английском интерфейсе не остаётся русских окончаний. */
const MSG_WORDS = [
  [["сервер", "сервера", "серверу", "сервером", "сервере"], "server"],
  [["серверы", "серверов", "серверам", "серверами", "серверах"], "servers"],
  [["ошибка", "ошибки", "ошибке", "ошибку", "ошибкой"], "error"],
  [["ошибок", "ошибкам", "ошибками", "ошибках"], "errors"],
  [["аккаунт", "аккаунта", "аккаунту", "аккаунтом", "аккаунте"], "account"],
  [["аккаунты", "аккаунтов", "аккаунтам", "аккаунтами", "аккаунтах"], "accounts"],
  [["кука", "куки", "куку", "кукой", "куке"], "cookie"],
  [["кук", "кукам", "куками", "куках"], "cookies"],
  [["токен", "токена", "токену", "токеном", "токене"], "token"],
  [["токены", "токенов", "токенам"], "tokens"],
  [["модель", "модели", "моделью", "модель"], "model"],
  [["моделей", "моделям", "моделями", "моделях"], "models"],
  [["провайдер", "провайдера", "провайдеру", "провайдером", "провайдере"], "provider"],
  [["провайдеры", "провайдеров", "провайдерам"], "providers"],
  [["запрос", "запроса", "запросу", "запросом", "запросе"], "request"],
  [["запросы", "запросов", "запросам", "запросами"], "requests"],
  [["лимит", "лимита", "лимиту", "лимитом", "лимите"], "limit"],
  [["лимиты", "лимитов", "лимитам"], "limits"],
  [["подписка", "подписки", "подписке", "подписку", "подпиской"], "subscription"],
  [["пауза", "паузы", "паузе", "паузу", "паузой"], "pause"],
  [["проверка", "проверки", "проверке", "проверку", "проверкой"], "check"],
  [["таймаут", "таймаута", "таймауту"], "timeout"],
  [["неверный", "неверная", "неверное", "неверные"], "invalid"],
  [["недействительный", "недействительная", "недействительное"], "invalid"],
  [["истёк", "истек", "истекла", "истёкла", "истекло", "истёкло"], "expired"],
  [["превышен", "превышена", "превышено"], "exceeded"],
  [["недоступен", "недоступна", "недоступно", "недоступны"], "unavailable"],
  [["пустой", "пустая", "пустое", "пусто"], "empty"],
  [["успешно"], "ok"],
  [["работает"], "working"],
  [["выключен", "выключена", "выключено"], "disabled"],
  [["отключена", "отключен", "отключено"], "disabled"],
];

const MSG_RULES = (() => {
  const rules = MSG_PHRASES.map(([from, to]) => [
    new RegExp(from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), to]);
  MSG_WORDS.forEach(([forms, to]) => {
    const sorted = forms.slice().sort((a, b) => b.length - a.length);
    rules.push([new RegExp(`(?<![А-Яа-яЁё])(?:${sorted.join("|")})(?![А-Яа-яЁё])`, "gi"), to]);
  });
  return rules;
})();

function translateMessage(text) {
  const value = String(text ?? "");
  if (LANG !== "en" || !/[А-Яа-яЁё]/.test(value)) return value;
  let out = value;
  MSG_RULES.forEach(([from, to]) => { out = out.replace(from, to); });
  // Предлоги переводим последними: раньше они разрывали бы фразы из таблицы.
  out = out
    .replace(/(?<![А-Яа-яЁё])с(?![А-Яа-яЁё])/gi, "from")
    .replace(/(?<![А-Яа-яЁё])без(?![А-Яа-яЁё])/gi, "without")
    .replace(/(?<![А-Яа-яЁё])для(?![А-Яа-яЁё])/gi, "for")
    .replace(/(?<![А-Яа-яЁё])нет(?![А-Яа-яЁё])/gi, "no")
    .replace(/(?<![А-Яа-яЁё])не(?![А-Яа-яЁё])/gi, "not")
    // «90 c» / «5 мин» — сокращения единиц рядом с числом.
    .replace(/(\d)\s*[сc](?![А-Яа-яЁёa-z])/g, "$1 s")
    .replace(/(\d)\s*мин(?![А-Яа-яЁё])/g, "$1 min")
    .replace(/(\d)\s*ч(?![А-Яа-яЁё])/g, "$1 h")
    // Русское окончание, прилипшее к переведённому слову («authorizedа»):
    // исходный текст никогда не смешивает алфавиты внутри слова, поэтому
    // такой «хвост» всегда остаток подстановки.
    .replace(/([A-Za-z])[А-Яа-яЁё]+/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
  return out;
}

/* Скачивание защищённого файла: fetch с токеном, затем локальная ссылка. */
async function downloadAuthed(path, filename) {
  try {
    const headers = TOKEN ? { Authorization: "Bearer " + TOKEN } : {};
    const resp = await fetch(path, { headers, credentials: "same-origin" });
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = filename;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast(t("common.downloaded"), "ok", 2500);
  } catch (e) { toast(e.message, "err"); }
}

/* Копирование, которое работает везде, где живёт панель: в pywebview/WebView2, в обычном
   браузере и по адресу LAN (http://192.168…), где navigator.clipboard нет вовсе. Clipboard API
   пробуем только в безопасном контексте и не ждём дольше 1,5 с (без фокуса документа обещание
   может не завершиться); запасной путь — скрытая textarea + execCommand("copy"), вставленная в
   открытое модальное окно (иначе ловушка фокуса/inert не даёт её выделить). */
function legacyCopy(value) {
  const host = document.querySelector("#modal-back.visible #modal-box") || document.body;
  const active = document.activeElement;
  const selection = document.getSelection ? document.getSelection() : null;
  const ranges = [];
  if (selection) for (let i = 0; i < selection.rangeCount; i += 1) ranges.push(selection.getRangeAt(i));
  const area = document.createElement("textarea");
  area.value = value;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.tabIndex = -1;
  Object.assign(area.style, { position: "fixed", top: "0", left: "0", width: "1px", height: "1px", padding: "0",
    border: "0", outline: "0", opacity: "0", pointerEvents: "none", contain: "strict" });
  host.appendChild(area);
  let done = false;
  try {
    area.focus({ preventScroll: true });
    area.select();
    area.setSelectionRange(0, value.length);
    done = document.execCommand("copy");
  } catch (err) { done = false; }
  area.remove();
  if (selection) { try { selection.removeAllRanges(); ranges.forEach((r) => selection.addRange(r)); } catch (err) { /* ignore */ } }
  if (active && typeof active.focus === "function" && document.contains(active)) {
    try { active.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
  }
  return done;
}

async function writeClipboard(text) {
  const value = String(text ?? "");
  if (window.isSecureContext && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    try {
      await Promise.race([navigator.clipboard.writeText(value),
        new Promise((_, reject) => setTimeout(() => reject(new Error("clipboard timeout")), 1500))]);
      return true;
    } catch (e) { /* запасной путь ниже */ }
  }
  return legacyCopy(value);
}

async function copyText(text, okMessage) {
  const done = await writeClipboard(text);
  toast(done ? (okMessage || t("home.copied")) : t("common.copyfail"), done ? "ok" : "err", done ? 2000 : 2500);
  return done;
}
const STATUS_BADGE = { ok: "ok", invalid: "invalid", unknown: "unknown", disabled: "disabled" };

/* ------------------------------ вход ------------------------------ */

function showLogin(message = "") {
  closeModal();
  closeNav();
  $("#login-error").textContent = message;
  $("#login-overlay").hidden = false;
  $("#app").inert = true;
  $("#logout-btn").hidden = true;
  if (window.MemuCompanion) MemuCompanion.signedOut();
  queueMicrotask(() => $("#login-password").focus());
}
function hideLogin() {
  $("#login-overlay").hidden = true;
  $("#login-error").textContent = "";
  $("#login-password").value = "";
  $("#app").inert = false;
}
function showApp() {
  hideLogin();
  $("#app").classList.add("visible");
  $("#logout-btn").hidden = !TOKEN;
  loadHome(); loadAccounts();
  if (window.MemuCompanion) MemuCompanion.ready();
}
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#login-submit");
  const password = $("#login-password").value;
  if (!password) return;
  button.disabled = true;
  $("#login-error").textContent = "";
  try {
    const data = await api("/api/admin/login", { method: "POST", body: { password } });
    TOKEN = data.token;
    sessionStorage.setItem("oh_token", TOKEN);
    showApp();
  } catch (error) {
    $("#login-error").textContent = error.message;
    $("#login-password").select();
  } finally {
    button.disabled = false;
  }
});
$("#logout-btn").onclick = async () => {
  try { await api("/api/admin/logout", { method: "POST" }); } catch (e) { /* локально очищаем в любом случае */ }
  TOKEN = "";
  sessionStorage.removeItem("oh_token");
  showLogin();
};

/* ------------------------------ вкладки / адаптив ------------------------------ */

$$(".nav-item[data-tab]").forEach((btn) => {
  btn.onclick = () => {
    $$(".nav-item[data-tab]").forEach((b) => { b.classList.remove("active"); b.removeAttribute("aria-current"); });
    btn.classList.add("active");
    btn.setAttribute("aria-current", "page");
    $$(".tab").forEach((t0) => t0.classList.remove("active"));
    $("#tab-" + btn.dataset.tab).classList.add("active");
    clearInterval(logsTimer);
    clearInterval(applogTimer);
    closeNav();
    if (btn.dataset.tab === "home") { loadHome(); loadMetrics(); }
    if (btn.dataset.tab === "accounts") loadAccounts();
    if (btn.dataset.tab === "models") loadModels();
    if (btn.dataset.tab === "local" && window.MemuLocal) MemuLocal.load();
    if (btn.dataset.tab === "detector" && window.MemuDetector) MemuDetector.load();
    if (btn.dataset.tab === "public" && window.MemuPublic) MemuPublic.load();
    if (btn.dataset.tab === "chat" && window.MemuProxyChat) MemuProxyChat.load();
    if (btn.dataset.tab === "settings") {
      if (window.MemuSettings) MemuSettings.show();
      loadSettings(); loadModels(); loadKeys(); if (window.MemuCliTools) MemuCliTools.load();
    }
    if (btn.dataset.tab === "logs") {
      if (logsState.view === "server") loadApplog(); else loadLogs();
      syncLogsTimer();
    }
  };
});
$(".nav-item.active")?.setAttribute("aria-current", "page");
function closeNav() {
  $("#sidebar").classList.remove("open");
  $("#nav-backdrop").hidden = true;
  $("#burger").setAttribute("aria-expanded", "false");
  $("#burger").setAttribute("aria-label", t("nav.open"));
}
$("#burger").onclick = () => {
  const opened = $("#sidebar").classList.toggle("open");
  $("#nav-backdrop").hidden = !opened;
  $("#burger").setAttribute("aria-expanded", String(opened));
  $("#burger").setAttribute("aria-label", t(opened ? "nav.close" : "nav.open"));
};
$("#nav-backdrop").onclick = closeNav;
window.addEventListener("resize", () => { if (window.innerWidth > 900) closeNav(); });

$$("[data-copy]").forEach((btn) => {
  btn.onclick = async () => {
    const source = $(btn.dataset.copy);
    const value = source.dataset.copyValue || source.textContent.trim();
    copyText(value);
  };
});

/* язык */
function initLang() {
  $("#lang-select").value = LANG;
  $("#lang-select-2").value = LANG;
  const onChange = (e) => {
    setLang(e.target.value);
    $("#lang-select").value = LANG;
    $("#lang-select-2").value = LANG;
  };
  $("#lang-select").onchange = onChange;
  $("#lang-select-2").onchange = onChange;
  applyI18n();
}
function onLangChanged() {
  if ($("#app").classList.contains("visible") && $("#login-overlay").hidden) {
    logsFacetsLoaded = false;
    fetchProviders(true).catch(() => {}).then(() => { loadHome(); loadAccounts(); loadModels(); renderProviderSettings(); });
    loadLogs(true);
    if (logsState.view === "server") loadApplog(true);
  }
  if (window.MemuLocal) MemuLocal.relabel();
  if (window.MemuDetector) MemuDetector.relabel();
  if (window.MemuPublic) MemuPublic.relabel();
  if (window.MemuProxyChat && $("#tab-chat")?.classList.contains("active")) MemuProxyChat.relabel();
  if (window.MemuCompanion) MemuCompanion.relabel();
  if (window.MemuCliTools) MemuCliTools.relabel();
  if (window.MemuModelCaps) MemuModelCaps.relabel();
  renderSearchSettings();
  if (window.MemuSettings) MemuSettings.relabel();
}

/* ------------------------------ главная ------------------------------ */

async function loadHome() {
  try {
    const o = await api("/api/admin/overview");
    overviewCache = o;
    // В overview лежит срез за сутки; на главной показываем выбранный период.
    if (o.metrics) {
      $("#metrics-body").hidden = false;
      $("#metrics-off").hidden = true;
      loadMetrics();
    } else {
      $("#metrics-body").hidden = true;
      $("#metrics-off").hidden = false;
    }
    if (o.version && $("#hero-version")) $("#hero-version").textContent = "v" + o.version;
    $("#endpoint-hint").textContent = `http://${o.host}:${o.port}/v1`;
    $("#hero-endpoint").textContent = `http://${o.host}:${o.port}/v1`;
    const key = o.first_api_key || "";
    $("#hero-key").textContent = key ? maskSecret(key) : t("home.nokey");
    $("#hero-key").dataset.copyValue = key || "";
    const keyCopyButton = document.querySelector('[data-copy="#hero-key"]');
    if (keyCopyButton) keyCopyButton.disabled = !key;
    $("#default-password-strip").hidden = !o.password_is_default;
    $("#proxy-toggle").textContent = o.proxy_enabled ? t("home.stop") : t("home.start");
    $("#proxy-toggle").className = o.proxy_enabled ? "btn" : "btn primary";
    $(".hero")?.classList.toggle("is-off", !o.proxy_enabled);
    $("#hero-dot").style.background = o.proxy_enabled ? "var(--ok)" : "var(--err)";
    $("#hero-dot").style.boxShadow = o.proxy_enabled ? "0 0 10px var(--ok)" : "none";
    $("#hero-title").textContent = o.proxy_enabled ? t("home.title") : t("home.stopped");
    // Round 31: a first-run «С чего начать» card while no account exists at all.
    const accountTotal = Number.isFinite(o.accounts_total) ? o.accounts_total
      : (o.providers || []).reduce((sum, p) => sum + (Number(p.accounts) || 0), 0);
    if ($("#start-card")) $("#start-card").hidden = accountTotal > 0;
    $("#providers-grid").innerHTML = localizeProviders(o.providers).map((p) => `
      <div class="provider-card">
        <div class="p-head">
          <div class="p-icon">${esc(p.icon)}</div>
          <div><div class="p-title">${esc(p.title)}</div>
          <div class="p-sub">${esc(p.description)}</div></div>
        </div>
        <div class="p-nums">${t("home.acc")} <b>${p.accounts_usable}/${p.accounts}</b> · ${t("home.models")} <b>${p.models}</b></div>
      </div>`).join("");
    $("#connect-hint").textContent = t("home.connect.hint");
    $("#connect-sample").textContent =
`from openai import OpenAI

client = OpenAI(
    api_key="<${t("home.key")}>",
    base_url="http://${o.host}:${o.port}/v1",
)

resp = client.chat.completions.create(
    model="${o.sample_model || "genspark-claude-opus-5"}",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True,
)
for chunk in resp:
    print(chunk.choices[0].delta.content or "", end="")`;
  } catch (e) { if (e.message !== "401") toast(e.message, "err"); }
}

function fmtNum(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(locale());
}

function fmtDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const d = Math.floor(total / 86400), h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60), sec = total % 60;
  // "1m 59s" in the Russian panel read as untranslated: the units follow the language.
  const forms = t("metrics.dur").split("|");
  const form = forms[d ? 0 : h ? 1 : m ? 2 : 3];
  return form.replace("{d}", d).replace("{h}", h).replace("{m}", m).replace("{s}", sec);
}

function fmtChartLabel(ts, step) {
  const date = new Date(Number(ts) * 1000);
  return step >= 86400
    ? date.toLocaleDateString(locale(), { day: "2-digit", month: "2-digit" })
    : date.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
}

let metricsKind = localStorage.getItem("oh_metrics_kind") || "requests";

function fmtCompact(value) {
  const n = Number(value) || 0;
  if (n >= 1e9) return `${+(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e4) return `${+(n / 1e3).toFixed(1)}K`;
  return fmtNum(n);
}

function fmtMs(ms) {
  const n = Number(ms) || 0;
  if (!n) return "—";
  return n >= 10000 ? `${(n / 1000).toFixed(1)} ${t("st.s")}` : `${fmtNum(n)} ${t("st.ms")}`;
}

function fmtUsd(value, atLeast = false) {
  const n = Number(value) || 0;
  if (!n) return "$0";
  /* An incomplete total is a lower bound: "≥ <$0.0001" said nothing, so a tiny bound keeps two digits too. */
  if (atLeast) return "≥ " + (n < 0.0001 ? "$" + n.toLocaleString(locale(), { maximumSignificantDigits: 2 }) : fmtUsd(n));
  /* Small sums keep two significant digits: a whole row of "<$0.01" hid how far apart models are. */
  if (n < 0.0001) return "<$0.0001";
  if (n < 0.01) return "$" + n.toLocaleString(locale(), { maximumSignificantDigits: 2 });
  return "$" + n.toLocaleString(locale(), { maximumFractionDigits: n < 100 ? 2 : 0 });
}

/* «$3 / $15 за 1M · кэш $0.3 · каталог OpenRouter: anthropic/claude-sonnet-4.5» */
function fmtPrice(price) {
  if (!price) return "";
  const num = (v) => (v === null || v === undefined ? "—" : "$" + (+Number(v).toPrecision(3)).toLocaleString(locale(), { maximumFractionDigits: 4 }));
  const source = price.source === "catalog" ? t("metrics.price.catalog") : price.source === "override" ? t("metrics.price.override")
    : price.source === "local" ? t("metrics.price.local") : t("metrics.price.builtin");
  return `${num(price.input)} / ${num(price.output)} ${t("metrics.price.per")}`
    + (price.cache_read !== null && price.cache_read !== undefined ? ` · ${t("metrics.price.cache")} ${num(price.cache_read)}` : "")
    + ` · ${source}${price.source === "catalog" && price.matched ? ": " + price.matched : ""}`
    + (price.approximate ? ` · ${t("metrics.price.approx")}` : "");
}

/* Ячейка «≈ $» таблицы моделей: сумма, «цена неизвестна» вместо обманчивого $0, пометки подписки и оценки. */
function metricsCostCell(x) {
  if (x.cost_state === "unknown" || x.cost_usd === null || x.cost_usd === undefined) {
    return `<span class="m-cost unknown" title="${esc(t("metrics.cost.unknown.title"))}">${esc(t("metrics.cost.unknown"))}</span>`;
  }
  const tokens = (x.prompt_tokens || 0) + (x.completion_tokens || 0);
  if (!tokens && x.cost_state === "none") return `<span class="dim" title="${esc(t("metrics.cost.notokens"))}">—</span>`;
  const approx = (x.price && x.price.approximate) || x.tokens_estimated || (x.estimated_requests && x.estimated_requests >= (x.ok || 0) / 2);
  const tips = [];
  if (x.price) tips.push(fmtPrice(x.price));
  if (x.cost_state === "billed") tips.push(t("metrics.cost.billed.title"));
  if (x.cost_state === "partial") tips.push(t("metrics.cost.partial"));
  if (x.billing === "subscription") tips.push(t("metrics.billing.sub"));
  if (x.tokens_estimated) tips.push(t("metrics.cost.legacy").replace("{n}", fmtNum(x.tokens_estimated)));
  else if (x.estimated_requests) tips.push(t("metrics.cost.lenest"));
  const chip = x.cost_state === "local" ? `<span class="chip">${esc(t("metrics.billing.local.short"))}</span>`
    : x.cost_state === "billed" ? `<span class="chip ok">${esc(t("metrics.billing.billed.short"))}</span>`
    : x.billing === "subscription" ? `<span class="chip">${esc(t("metrics.billing.sub.short"))}</span>` : "";
  return `<span class="m-cost" title="${esc(tips.join("\n"))}">${approx ? "≈ " : ""}${esc(fmtUsd(x.cost_usd))}${x.cost_state === "partial" ? "+" : ""}</span>${chip ? " " + chip : ""}`;
}

function metricsCostKpi(m) {
  const c = m.cost || {};
  const parts = [];
  if (c.api_equivalent) parts.push(`${t("metrics.cost.equiv")} ${fmtUsd(c.api_equivalent)}`);
  if (c.api_billed) parts.push(`${t("metrics.cost.keys")} ${fmtUsd(c.api_billed)}`);
  const unknown = (c.unknown_models || []).length;
  if (unknown) parts.push(t("metrics.cost.nounknown").replace("{n}", unknown));
  else if (c.legacy_estimated) parts.push(t("metrics.cost.legacy.short"));
  const sub = parts.length ? parts.join(" · ") : t("metrics.cost.hint");
  const title = [t("metrics.cost.title"),
    unknown ? `${t("metrics.cost.unknown.list")}: ${(c.unknown_models || []).join(", ")}` : "",
    c.legacy_estimated ? t("metrics.cost.legacy.title") : "",
    c.approximate ? t("metrics.cost.approx.title") : ""].filter(Boolean).join("\n\n");
  const value = m.has_data && !m.cost_usd && unknown ? "—" : fmtUsd(m.cost_usd, !m.cost_complete);
  return { value, sub, title };
}

/* Δ к предыдущему такому же периоду. better: "up" — рост хорошо, "down" — рост плохо. */
function metricsDelta(change, better = "up", unit = "%") {
  if (change === null || change === undefined || !Number.isFinite(Number(change))) return "";
  const n = Number(change);
  if (Math.abs(n) < 0.05) return `<span class="m-delta flat">±0${unit}</span>`;
  const good = better === "neutral" ? "flat" : ((n > 0) === (better === "up") ? "good" : "bad");
  return `<span class="m-delta ${good}" title="${esc(t("metrics.vs.prev"))}">${n > 0 ? "▲" : "▼"} ${Math.abs(n).toLocaleString(locale(), { maximumFractionDigits: 1 })}${unit}</span>`;
}

function errorLabel(code) {
  const key = "metrics.err." + code;
  const text = t(key);
  return text === key ? code : text;
}

function renderMetricsChart(m) {
  const box = $("#metrics-chart");
  const note = $("#metrics-chart-note");
  if (!box) return;
  const points = m.series || [];
  const step = Number(m.step) || 3600;
  $$("#metrics-chart-kind .seg").forEach((b) => b.classList.toggle("active", b.dataset.kind === metricsKind));
  if (note) note.textContent = points.length ? (step >= 86400 ? t("metrics.chart.days") : t("metrics.chart.hours")) : "";
  const axis = $("#metrics-axis"), xl = $("#metrics-xlabels"), legend = $("#metrics-legend");
  if (!points.length || !m.has_data) {
    box.innerHTML = `<div class="empty">${t("metrics.none")}</div>`;
    axis.innerHTML = ""; xl.innerHTML = ""; legend.innerHTML = "";
    return;
  }
  const value = (p) => metricsKind === "tokens" ? p.tokens || 0 : metricsKind === "latency" ? p.avg_ms || 0
    : metricsKind === "cost" ? p.cost || 0 : p.total;
  const peak = Math.max(metricsKind === "cost" ? 0.0001 : 1, ...points.map(value));
  const fmt = metricsKind === "latency" ? fmtMs : metricsKind === "cost" ? fmtUsd : fmtCompact;
  axis.innerHTML = `<span>${esc(fmt(peak))}</span><span>${esc(fmt(peak / 2))}</span><span>0</span>`;
  box.innerHTML = points.map((p) => {
    const v = value(p);
    const share = v / peak * 100;
    const title = [fmtChartLabel(p.ts, step),
      `${t("metrics.requests")}: ${fmtNum(p.total)}`,
      `${t("metrics.failures")}: ${fmtNum(p.failed)}`,
      `${t("metrics.kind.tokens")}: ${fmtNum(p.tokens || 0)}`,
      `${t("metrics.cost.short")}: ${fmtUsd(p.cost || 0)}`,
      `${t("metrics.avg")}: ${fmtMs(p.avg_ms)}`].join("\n");
    if (!v) return `<div class="spark-col" title="${esc(title)}"><i class="spark-zero"></i></div>`;
    if (metricsKind !== "requests") return `<div class="spark-col" title="${esc(title)}"><i class="spark-ok ${metricsKind}" style="height:${Math.max(2, share)}%"></i></div>`;
    const errShare = p.total ? p.failed / p.total * share : 0;
    return `<div class="spark-col" title="${esc(title)}">
      ${errShare ? `<i class="spark-err" style="height:${Math.max(1.5, errShare)}%"></i>` : ""}
      ${share - errShare > 0 ? `<i class="spark-ok" style="height:${Math.max(2, share - errShare)}%"></i>` : ""}</div>`;
  }).join("");
  const marks = points.length <= 1 ? [0] : [0, Math.floor((points.length - 1) / 2), points.length - 1];
  xl.innerHTML = marks.map((i) => `<span>${esc(fmtChartLabel(points[i].ts, step))}</span>`).join("");
  legend.innerHTML = metricsKind === "requests"
    ? `<span><i class="lg ok"></i>${esc(t("metrics.success"))}</span><span><i class="lg err"></i>${esc(t("metrics.failures"))}</span>`
    : `<span>${esc(t(metricsKind === "tokens" ? "metrics.legend.tokens" : metricsKind === "cost" ? "metrics.legend.cost" : "metrics.legend.latency"))}</span>`;
}

/* Round 40 (r40-proxy): «Сэкономлено кэшем» — a quiet line under the KPI cards, only when something was cached. */
function renderMetricsCache(m) {
  const box = $("#metrics-cache");
  if (!box) return;
  const c = m.cache || {};
  box.hidden = !c.tokens;
  if (!c.tokens) return;
  const unpriced = c.unpriced_tokens ? ` · ${fmtCompact(c.unpriced_tokens)} ${t("metrics.cache.unpriced")}` : "";
  box.title = t("metrics.cache.title");
  box.innerHTML = `<span class="mc-lbl">${esc(t("metrics.cache.saved"))}</span>
    <b class="mc-val ok">${esc(fmtUsd(c.saved_usd || 0, !!c.unpriced_tokens))}</b>
    <span class="dim">${esc(fmtCompact(c.tokens))} ${esc(t("metrics.cache.tokens"))} · ${esc(Number(c.share || 0).toLocaleString(locale(), { maximumFractionDigits: 1 }))}% ${esc(t("metrics.cache.share"))}${esc(unpriced)}</span>`;
}

function renderMetrics(m) {
  if (!m) return;
  metricsCache = m;
  const cmp = m.comparison || null;
  const tokens = (m.prompt_tokens || 0) + (m.completion_tokens || 0);
  const failRate = m.total ? Math.round((m.failed / m.total) * 1000) / 10 : 0;
  const rate = m.has_data ? `${m.success_rate}%` : "—";
  const rateTone = !m.has_data ? "" : m.success_rate >= 95 ? "ok" : m.success_rate >= 80 ? "warn" : "err";
  const cells = [
    { label: t("metrics.requests"), value: fmtNum(m.total), delta: cmp ? metricsDelta(cmp.total_change, "neutral") : "",
      sub: m.failed ? `${fmtNum(m.failed)} ${t("metrics.sub.failed")} · ${failRate}%` : t("metrics.sub.nofail") },
    { label: t("metrics.successrate"), value: rate, tone: rateTone,
      delta: cmp ? metricsDelta(cmp.success_rate_delta, "up", t("metrics.pp")) : "", sub: `${fmtNum(m.ok)} ${t("metrics.sub.ok")}` },
    { label: t("metrics.latency"), value: fmtMs(m.p50_ms), delta: cmp ? metricsDelta(cmp.avg_ms_change, "down") : "",
      sub: `p95 ${fmtMs(m.p95_ms)} · ${t("metrics.sub.avg")} ${fmtMs(m.avg_ms)}` },
    { label: t("metrics.kind.tokens"), value: fmtCompact(tokens), delta: cmp ? metricsDelta(cmp.tokens_change, "neutral") : "",
      sub: `${t("metrics.sub.in")} ${fmtCompact(m.prompt_tokens)} · ${t("metrics.sub.out")} ${fmtCompact(m.completion_tokens)}` },
    { label: t("metrics.cost"), ...metricsCostKpi(m), delta: cmp ? metricsDelta(cmp.cost_change, "neutral") : "" },
    { label: t("metrics.lasthour"), value: fmtNum(m.last_hour), tone: m.last_hour_failed ? "warn" : "",
      sub: m.last_hour_failed ? `${fmtNum(m.last_hour_failed)} ${t("metrics.sub.failed")}` : (m.busiest_hour !== null && m.busiest_hour !== undefined ? `${t("metrics.busiest")} ${String(m.busiest_hour).padStart(2, "0")}:00` : "") },
  ];
  $("#metrics-grid").innerHTML = cells.map((c) => `
    <div class="stat"${c.title ? ` title="${esc(c.title)}"` : ""}><div class="stat-lbl">${esc(c.label)}</div>
      <div class="stat-row"><div class="stat-val ${c.tone || ""}">${esc(c.value)}</div>${c.delta || ""}</div>
      ${c.sub ? `<div class="stat-sub">${esc(c.sub)}</div>` : ""}</div>`).join("");
  renderMetricsCache(m);
  renderMetricsChart(m);

  const models = m.models || [];
  $("#metrics-models").innerHTML = models.length ? `<thead><tr><th>${esc(t("metrics.col.model"))}</th><th>${esc(t("metrics.requests"))}</th><th>${esc(t("metrics.successrate"))}</th><th>${esc(t("metrics.sub.avg"))}</th><th>${esc(t("metrics.kind.tokens"))}</th><th title="${esc(t("metrics.cost.title"))}">${esc(t("metrics.cost.short"))}</th></tr></thead><tbody>`
    + models.map((x) => `<tr><td class="mono m-name" title="${esc(x.model)}">${esc(x.model)}</td>
      <td><div class="m-share"><i style="width:${Math.max(2, Math.min(100, x.share))}%"></i></div>${fmtNum(x.total)} <span class="dim">${x.share}%</span></td>
      <td class="${x.success_rate >= 95 ? "ok" : x.success_rate >= 80 ? "warn" : "err"}">${x.success_rate}%</td>
      <td>${fmtMs(x.avg_ms)}</td>
      <td title="${esc(t("metrics.sub.in"))} ${fmtNum(x.prompt_tokens)}${x.cache_tokens ? ` (${esc(t("metrics.price.cache"))} ${fmtNum(x.cache_tokens)})` : ""} · ${esc(t("metrics.sub.out"))} ${fmtNum(x.completion_tokens)}${x.reasoning_tokens ? ` (${esc(t("metrics.sub.reasoning"))} ${fmtNum(x.reasoning_tokens)})` : ""}${x.tokens_estimated ? "\n" + esc(t("metrics.cost.legacy").replace("{n}", fmtNum(x.tokens_estimated))) : ""}">${x.tokens_estimated ? "≈ " : ""}${fmtCompact((x.prompt_tokens || 0) + (x.completion_tokens || 0))}</td>
      <td class="m-cost-cell">${metricsCostCell(x)}</td></tr>`).join("") + "</tbody>"
      + (m.models_total > models.length ? `<tfoot><tr><td colspan="6" class="dim">${esc(t("metrics.models.more").replace("{n}", m.models_total - models.length))}</td></tr></tfoot>` : "")
    : `<tbody><tr><td class="empty">${t("metrics.none")}</td></tr></tbody>`;

  const providers = (m.providers || []).slice().sort((a, b) => b.total - a.total);
  $("#metrics-providers").innerHTML = providers.length ? providers.map((p) => {
    const r = Number(p.success_rate) || 0;
    return `<div class="usage-row" title="${esc(t("metrics.successrate"))}: ${r}% · ${esc(t("metrics.sub.avg"))}: ${esc(fmtMs(p.avg_ms))}">
      <span class="u-name">${esc(providerMeta(p.provider).title || p.provider)}</span>
      <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p.share || 0)}"><i style="width:${Math.max(0, Math.min(100, p.share || 0))}%"></i></div>
      <span class="u-val mono">${fmtNum(p.total)} · <span class="${r >= 95 ? "ok" : r >= 80 ? "warn" : "err"}">${r}%</span> · ${esc(fmtMs(p.avg_ms))}${p.cost_usd ? ` · <span title="${esc(t(p.billing === "subscription" ? "metrics.billing.sub" : "metrics.cost.title"))}">${esc(fmtUsd(p.cost_usd, !p.cost_complete))}</span>` : ""}</span></div>`;
  }).join("") : `<div class="empty">${t("metrics.none")}</div>`;

  const errors = m.top_errors || [];
  $("#metrics-errors").innerHTML = errors.length
    ? errors.map((e) => `<div class="row-line" title="${esc(e.code || "")}"><span class="chip warn">${fmtNum(e.count)}</span> <span class="mono">${esc(errorLabel(e.code || "unknown"))}</span></div>`).join("")
    : `<div class="empty">${t("metrics.noerrors")}</div>`;

  const hist = m.latency_hist || [];
  const histPeak = Math.max(1, ...hist.map((b) => b.count));
  const histTotal = hist.reduce((sum, b) => sum + b.count, 0);
  let lastBin = -1;
  hist.forEach((b, i) => { if (b.count) lastBin = i; });
  $("#metrics-latency").innerHTML = histTotal ? hist.slice(0, lastBin + 1).map((b, i) => {
    const from = i ? hist[i - 1].le : 0;
    const label = b.le === null ? `> ${fmtMs(from)}` : `≤ ${fmtMs(b.le)}`;
    return `<div class="usage-row lat"><span class="u-name mono">${esc(label)}</span><div class="bar"><i style="width:${b.count / histPeak * 100}%"></i></div><span class="u-val mono">${fmtNum(b.count)} · ${Math.round(b.count / histTotal * 100)}%</span></div>`;
  }).join("")
    : `<div class="empty">${t("metrics.none")}</div>`;

  const all = m.all_time || {};
  const since = m.history_from ? new Date(m.history_from * 1000).toLocaleDateString(locale(), { day: "numeric", month: "short" }) : "—";
  $("#metrics-foot").textContent = `${t("metrics.uptime")}: ${fmtDuration(m.uptime_s)} · ${t("metrics.alltime")}: ${fmtNum(all.total)} (${t("metrics.since")} ${since}, ${t("metrics.keep").replace("{n}", m.keep_days || 35)})`;
}

$$("#metrics-chart-kind [data-kind]").forEach((btn) => btn.onclick = () => {
  metricsKind = btn.dataset.kind;
  localStorage.setItem("oh_metrics_kind", metricsKind);
  if (metricsCache) renderMetricsChart(metricsCache);
});

async function loadMetrics() {
  try { renderMetrics(await api("/api/admin/metrics?period=" + encodeURIComponent(metricsPeriod))); }
  catch (e) { /* статистика необязательна */ }
}

$$("#metrics-period [data-period]").forEach((btn) => btn.onclick = () => {
  metricsPeriod = btn.dataset.period;
  localStorage.setItem("oh_metrics_period", metricsPeriod);
  $$("#metrics-period .seg").forEach((b) => b.classList.toggle("active", b === btn));
  loadMetrics();
});
$$("#metrics-period [data-period]").forEach((btn) =>
  btn.classList.toggle("active", btn.dataset.period === metricsPeriod));

$("#metrics-refresh").onclick = () => loadMetrics();
$("#metrics-reset").onclick = () => confirmDialog(t("home.metrics.reset"), t("metrics.reset.hint"), async () => {
  try { await api("/api/admin/metrics/reset", { method: "POST" }); loadMetrics(); }
  catch (e) { toast(e.message, "err"); }
}, t("home.metrics.reset"));

$("#proxy-restart").onclick = (event) => {
  const button = event.currentTarget;
  confirmDialog(t("home.restart"), t("home.restart.hint"), async () => {
    button.disabled = true;
    try {
      const r = await api("/api/admin/restart", { method: "POST", body: {} });
      toast(t("home.restart.done") + " " + (r.steps || []).join(", "), "ok", 6000);
      await loadHome();
    } catch (e) { toast(e.message, "err", 6000); }
    finally { button.disabled = false; }
  }, t("home.restart"));
};

$("#open-memuai").onclick = async () => {
  try {
    const r = await api("/api/admin/open_memuai", { method: "POST" });
    if (r.ok) toast(t("home.openchat.done"));
  } catch (e) { toast(e.message, "err", 6000); }
};

$("#proxy-toggle").onclick = async (event) => {
  const on = !(overviewCache && overviewCache.proxy_enabled);
  // event.currentTarget is nulled once the handler yields, so keep the node.
  const button = event.currentTarget || $("#proxy-toggle");
  button.disabled = true;
  try {
    await api("/api/admin/settings", { method: "POST", body: { settings: { proxy_enabled: on } } });
    await loadHome();
  } catch (e) { toast(e.message, "err"); }
  finally { button.disabled = false; }
};

/* ------------------------------ аккаунты (карточки) ------------------------------ */

function accStatusText(a) {
  const key = "acc.status." + a.status;
  const translated = t(key);
  return translated === key ? translateMessage(a.status_label || a.status) : translated;
}

function accSuccessRate(a) {
  const st = a.stats || {};
  return st.requests ? Math.round((st.success || 0) / st.requests * 100) : null;
}

function sortAccounts(list) {
  const copy = list.slice();
  const byOrder = (x, y) => (x.order - y.order) || (x.created - y.created);
  if (accountsSort === "status") {
    const rank = { ok: 0, unknown: 1, invalid: 2, disabled: 3 };
    copy.sort((x, y) => (rank[x.status] ?? 9) - (rank[y.status] ?? 9) || byOrder(x, y));
  } else if (accountsSort === "label") {
    copy.sort((x, y) => String(x.label || "").localeCompare(String(y.label || ""), locale()) || byOrder(x, y));
  } else if (accountsSort === "used") {
    copy.sort((x, y) => (y.last_used || 0) - (x.last_used || 0) || byOrder(x, y));
  } else if (accountsSort === "requests") {
    copy.sort((x, y) => ((y.stats || {}).requests || 0) - ((x.stats || {}).requests || 0) || byOrder(x, y));
  } else {
    copy.sort(byOrder);
  }
  return copy;
}

function matchesAccountSearch(a) {
  if (!accountsSearch) return true;
  const needle = accountsSearch.toLowerCase();
  const haystack = [a.label, a.provider, providerMeta(a.provider).title, a.status,
    accStatusText(a), a.status_message, a.note, (a.tags || []).join(" "),
    a.proxy, (a.stats || {}).last_error].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(needle);
}

function syncAccountBulkBar() {
  const bar = $("#acc-bulk-bar");
  if (!bar) return;
  // Панель нужна ровно тогда, когда есть что применять: отдельный режим
  // «выбор нескольких» только мешал — выделять можно всегда.
  bar.hidden = !accountsSelectMode && accountsSelected.size === 0;
  // Round 34: two hint strips stood one under the other — the reorder hint shows while cards can be dragged,
  // the mouse-selection hint while selecting (proxy-r34.css).
  const tab = $("#tab-accounts");
  if (tab) {
    tab.classList.toggle("acc-selecting", !bar.hidden);
    tab.classList.toggle("acc-sorted", accountsSort !== "order");
  }
  $("#acc-bulk-count").textContent = `${accountsSelected.size} ${t("bulk.selected")}`;
  $$("#accounts-grid .acc-card").forEach((card) =>
    card.classList.toggle("selected", accountsSelected.has(card.dataset.accId)));
  if (window.MemuAccR33) MemuAccR33.sync();   // r33: count «N of M», idle state, select-all label, checkboxes
}

/* Выделение мышью: клик по свободному месту карточки, Ctrl — добавить,
   Shift — диапазон от последней выделенной. */
let lastPickedAccount = "";

function visibleAccounts() {
  return sortAccounts(accountsCache.filter(
    (a) => (!providerFilter || a.provider === providerFilter) && matchesAccountSearch(a)));
}

function pickAccount(accountId, event) {
  const visible = visibleAccounts().map((a) => a.id);
  if (event && event.shiftKey && lastPickedAccount) {
    const from = visible.indexOf(lastPickedAccount);
    const to = visible.indexOf(accountId);
    if (from >= 0 && to >= 0) {
      const [start, end] = from <= to ? [from, to] : [to, from];
      visible.slice(start, end + 1).forEach((id) => accountsSelected.add(id));
      syncAccountBulkBar();
      return;
    }
  }
  if (event && (event.ctrlKey || event.metaKey || accountsSelectMode)) {
    if (accountsSelected.has(accountId)) accountsSelected.delete(accountId);
    else accountsSelected.add(accountId);
  } else if (accountsSelected.has(accountId) && accountsSelected.size === 1) {
    accountsSelected.delete(accountId);
  } else {
    accountsSelected.clear();
    accountsSelected.add(accountId);
  }
  lastPickedAccount = accountId;
  syncAccountBulkBar();
}

function closeAccountMenu() {
  const menu = $("#acc-context-menu");
  if (menu) menu.remove();
}

function openAccountMenu(accountId, x, y) {
  closeAccountMenu();
  const account = accountsCache.find((a) => a.id === accountId);
  if (!account) return;
  if (!accountsSelected.has(accountId)) pickAccount(accountId, null);
  const many = accountsSelected.size > 1;
  const count = many ? ` (${accountsSelected.size})` : "";
  const items = [
    ["edit", t("acc.btn.edit"), "✎", many],
    ["usage", t("acc.btn.usage"), "✦", many],
    ["check", t("acc.btn.check") + count, "✓", false],
    [account.enabled ? "disable" : "enable", account.enabled ? t("bulk.disable") + count : t("bulk.enable") + count, "◐", false],
    ["sep", "", "", false],
    ["top", t("acc.move.top"), "⤒", false],
    ["up", t("acc.moveup"), "▲", false],
    ["down", t("acc.movedown"), "▼", false],
    ["bottom", t("acc.move.bottom"), "⤓", false],
    ["sep", "", "", false],
    ["priority_reserve", t("bulk.reserve") + count, "◇", false],
    ["priority_normal", t("bulk.normal") + count, "◆", false],
    ["sep", "", "", false],
    ["delete", t("acc.btn.del") + count, "✕", false],
  ];
  const menu = document.createElement("div");
  menu.id = "acc-context-menu";
  menu.className = "ctx-menu";
  menu.setAttribute("role", "menu");
  menu.innerHTML = items.map(([action, label, glyph, hidden]) => hidden ? ""
    : action === "sep" ? `<div class="ctx-sep"></div>`
    : `<button role="menuitem" data-ctx="${esc(action)}" class="${action === "delete" ? "danger" : ""}">
         <span class="ctx-ico">${glyph}</span>${esc(label)}</button>`).join("");
  document.body.appendChild(menu);
  const box = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, window.innerWidth - box.width - 8) + "px";
  menu.style.top = Math.min(y, window.innerHeight - box.height - 8) + "px";
  $$("#acc-context-menu [data-ctx]").forEach((btn) => btn.onclick = () => {
    const action = btn.dataset.ctx;
    closeAccountMenu();
    runAccountMenuAction(action, accountId);
  });
}

async function runAccountMenuAction(action, accountId) {
  if (["top", "up", "down", "bottom"].includes(action)) {
    await moveSelectedAccounts(action);
    return;
  }
  if (action === "edit") { editAccount(accountId); return; }
  if (action === "usage") { showAccountUsage(accountId); return; }
  await accountsBulk(action);
}

/* Перемещение всей выделенной группы: карточки сохраняют взаимный порядок. */
async function moveSelectedAccounts(where) {
  const list = globalOrder();
  const picked = list.filter((a) => accountsSelected.has(a.id));
  if (!picked.length) return;
  const rest = list.filter((a) => !accountsSelected.has(a.id));
  let next;
  if (where === "top") next = [...picked, ...rest];
  else if (where === "bottom") next = [...rest, ...picked];
  else {
    const delta = where === "up" ? -1 : 1;
    next = list.slice();
    const indexes = picked.map((a) => next.indexOf(a));
    const ordered = delta < 0 ? indexes : indexes.slice().reverse();
    for (const index of ordered) {
      const target = index + delta;
      if (target < 0 || target >= next.length) return;
      if (accountsSelected.has(next[target].id)) continue;
      [next[index], next[target]] = [next[target], next[index]];
    }
  }
  await saveAccountOrder(next);
}

async function accountsBulk(action) {
  const ids = Array.from(accountsSelected);
  if (!ids.length) { toast(t("bulk.nothing"), "warn"); return; }
  const run = async () => {
    try {
      const r = await api("/api/admin/accounts/bulk", { method: "POST", body: { action, ids } });
      const done = r.done ?? r.count ?? 0;
      toast(`${t("bulk.done")}: ${done}${r.failed ? " · ✗ " + r.failed : ""}`, r.failed ? "warn" : "ok", 5000);
      accountsSelected.clear();
      loadAccounts();
      if (action === "delete" && modelsCache.length) loadModels();
    } catch (e) { toast(e.message, "err"); }
  };
  if (action === "delete") {
    confirmDialog(`${t("bulk.delete")} (${ids.length})`, t("acc.del.confirm"), run, t("bulk.delete"));
  } else {
    await run();
  }
}

/* Перетаскивание карточек: порядок = приоритет выбора аккаунта. */

function wireAccountDnd() {
  const grid = $("#accounts-grid");
  if (!grid) return;
  $$("#accounts-grid .acc-card").forEach((card) => {
    // r33 (#19): reordering is pointer-driven (proxy-r33.js) — the native HTML5 drag blocked the mouse
    // wheel on Windows, so the list could not be scrolled while a card was held.
    // Клик по свободному месту карточки выделяет её; кнопки и переключатели
    // продолжают работать как раньше.
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, input, label, a, select, textarea")) return;
      if (window.MemuAccR33 && MemuAccR33.justDragged()) return;
      pickAccount(card.dataset.accId, event);
    });
    card.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openAccountMenu(card.dataset.accId, event.clientX, event.clientY);
    });
  });
}

document.addEventListener("click", (event) => {
  if (!event.target.closest("#acc-context-menu")) closeAccountMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAccountMenu();
  if (!$("#tab-accounts")?.classList.contains("active")) return;
  if ($("#modal-back").classList.contains("visible")) return;
  if (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    visibleAccounts().forEach((a) => accountsSelected.add(a.id));
    syncAccountBulkBar();
  }
  if (event.key === "Delete" && accountsSelected.size) {
    event.preventDefault();
    accountsBulk("delete");
  }
  if ((event.altKey) && (event.key === "ArrowUp" || event.key === "ArrowDown") && accountsSelected.size) {
    event.preventDefault();
    moveSelectedAccounts(event.key === "ArrowUp" ? "up" : "down");
  }
});

/* Порядок общий для всех провайдеров: карточку можно поставить куда угодно.
   Приоритет при этом сохраняется — аккаунт, который стоит выше своего соседа
   по провайдеру, выбирается раньше. */
function globalOrder() {
  return accountsCache.slice().sort((x, y) => (x.order - y.order) || (x.created - y.created));
}

async function saveAccountOrder(list) {
  try {
    await api("/api/admin/accounts/reorder", { method: "POST", body: { ids: list.map((a) => a.id) } });
    toast(t("acc.dnd.saved"), "ok", 2000);
    loadAccounts();
  } catch (e) { toast(e.message, "err"); }
}

async function reorderAccounts(sourceId, targetId, after = false) {
  const list = globalOrder();
  if (list.findIndex((a) => a.id === targetId) < 0) return;
  // Тащим всю выделенную группу, если тянут за одну из выделенных карточек.
  const moving = accountsSelected.has(sourceId) && accountsSelected.size > 1
    ? list.filter((a) => accountsSelected.has(a.id))
    : list.filter((a) => a.id === sourceId);
  if (!moving.length || moving.some((a) => a.id === targetId)) return;
  const rest = list.filter((a) => !moving.includes(a));
  const at = rest.findIndex((a) => a.id === targetId);
  if (at < 0) return;
  rest.splice(at + (after ? 1 : 0), 0, ...moving);
  await saveAccountOrder(rest);
}

async function moveAccount(accountId, delta) {
  const list = globalOrder();
  const index = list.findIndex((a) => a.id === accountId);
  const next = index + delta;
  if (index < 0 || next < 0 || next >= list.length) return;
  [list[index], list[next]] = [list[next], list[index]];
  await saveAccountOrder(list);
}

async function loadAccounts() {
  try {
    await fetchProviders();
    const data = await api("/api/admin/accounts");
    accountsCache = data.accounts;
    syncGensparkModelTools();
    const alive = new Set(accountsCache.map((a) => a.id));
    accountsSelected = new Set(Array.from(accountsSelected).filter((id) => alive.has(id)));

    const used = [...new Set(accountsCache.map((a) => a.provider))];
    $("#provider-filter").innerHTML =
      `<button class="chip-btn ${!providerFilter ? "active" : ""}" data-pf="">${t("acc.filter.all")}</button>` +
      used.map((p) => {
        const count = accountsCache.filter((a) => a.provider === p).length;
        return `<button class="chip-btn ${providerFilter === p ? "active" : ""}" data-pf="${esc(p)}">${esc(providerMeta(p).title)} <span class="dim">${count}</span></button>`;
      }).join("");
    $$("#provider-filter [data-pf]").forEach((b) => b.onclick = () => { providerFilter = b.dataset.pf; loadAccounts(); });

    let accs = accountsCache.filter((a) => !providerFilter || a.provider === providerFilter);
    accs = accs.filter(matchesAccountSearch);
    accs = sortAccounts(accs);
    syncAccountBulkBar();
    // Round 31: the reorder/selection hints mean nothing without accounts — show a real empty state instead.
    $("#tab-accounts").classList.toggle("is-empty", !accountsCache.length);
    if (!accs.length) {
      $("#accounts-grid").innerHTML = accountsCache.length
        ? `<div class="empty" style="grid-column:1/-1">${t("acc.nomatch")}</div>`
        : `<div class="acc-empty" style="grid-column:1/-1">
            <div class="acc-empty-ico" aria-hidden="true">${window.MemuIcons ? MemuIcons.svg("users") : ""}</div>
            <h3>${t("acc.empty.title")}</h3>
            <p>${t("acc.empty.body")}</p>
            <button class="btn primary" type="button" id="acc-empty-add">${t("acc.empty.cta")}</button>
            <p class="dim">${t("acc.empty.fast")}</p>
          </div>`;
      const emptyAdd = $("#acc-empty-add");
      if (emptyAdd) emptyAdd.onclick = () => $("#acc-add-btn").click();
      return;
    }
    // #N — место в общей очереди, #N в подсказке — место среди аккаунтов
    // своего провайдера: именно оно решает, кого пул возьмёт первым.
    const perProvider = {};
    globalOrder().forEach((a, index) => {
      a._pos = index + 1;
      perProvider[a.provider] = (perProvider[a.provider] || 0) + 1;
      a._providerPos = perProvider[a.provider];
    });

    $("#accounts-grid").innerHTML = accs.map((a) => {
      const meta = providerMeta(a.provider);
      // Round 34: a key that answers 429 (daily quota spent) is accepted, but «working» in green hid that it
      // cannot serve requests right now.
      const limitCode = a.status === "ok" ? (String(a.status_message || "").match(/\bHTTP (402|429)\b/) || [])[1] : "";
      const badge = limitCode ? "warn" : (STATUS_BADGE[a.status] || "unknown");
      const statusText = limitCode === "402" ? (LANG === "en" ? "no balance" : "нет средств")
        : limitCode ? (LANG === "en" ? "limit reached" : "лимит исчерпан") : accStatusText(a);
      const fields = Object.entries(a.fields || {})
        .filter(([k, v]) => v && v !== "[REDACTED]" && !["space_id", "project_id", "region", "thread_type", "model_mode"].includes(k))
        .map(([k, v]) => `${esc(k)}: ${esc(String(v).slice(0, 22))}${String(v).length > 22 ? "…" : ""}`)
        .slice(0, 2).join("<br>");
      const st = a.stats || {};
      const rate = accSuccessRate(a);
      return `
      <div class="provider-card acc-card ${a.enabled ? "" : "acc-off"}" data-acc-id="${esc(a.id)}" draggable="false" data-reorder="${accountsSort === "order" ? "true" : "false"}">
        <div class="p-head">
          ${accountsSelectMode ? `<label class="acc-pick"><input type="checkbox" data-select="${esc(a.id)}" ${accountsSelected.has(a.id) ? "checked" : ""} aria-label="${esc(a.label || meta.title)}"></label>` : ""}
          <span class="drag-handle" title="${t("acc.dnd.handle")}" aria-hidden="true">≡</span>
          <div class="p-icon">${esc(meta.icon)}</div>
          <div class="acc-ident">
            <div class="p-title" title="${esc(meta.title)}${a.label ? " · " + esc(a.label) : ""}">${esc(a.label || meta.title)}</div>
            <div class="acc-provider">${esc(meta.title)}</div>
            <div style="margin-top:4px"><span class="badge ${badge}">${esc(statusText)}</span>
              <span class="chip" title="${t("acc.priority.pos")}: #${a._providerPos || 1} ${esc(meta.title)}">#${a._pos || 1}</span>
              ${a.priority === "reserve" ? `<span class="chip">${t("acc.priority.reservechip")}</span>` : ""}
              ${(a.tags || []).map((tag) => `<span class="chip tag">${esc(tag)}</span>`).join("")}</div>
          </div>
          <div class="grow"></div>
          <label class="switch"><span class="sr-only">${esc(meta.title)}: ${a.enabled ? t("common.yes") : t("common.no")}</span><input type="checkbox" data-toggle="${esc(a.id)}" aria-label="${esc(meta.title)}" ${a.enabled ? "checked" : ""}><span class="slider"></span></label>
        </div>
        ${a.status_message ? `<div class="p-sub" style="margin-top:8px">${esc(translateMessage(a.status_message)).slice(0, 220)}</div>` : ""}
        ${a.status === "invalid" ? `<button class="btn small ask-companion" type="button" data-ask-acc="${esc(a.id)}">✦ ${t("comp.ask")}</button>` : ""}
        ${a.note ? `<div class="p-note">${esc(a.note).slice(0, 200)}</div>` : ""}
        <div class="p-sub mono" style="margin-top:6px">${fields || "—"}</div>
        <div class="p-sub">${t("acc.check.time")}: ${fmtDateTime(a.last_checked)} · ${t("acc.lastused")}: ${fmtDateTime(a.last_used)}${a.proxy ? " · " + esc(a.proxy) : ""}</div>
        <div class="p-sub mono">${t("acc.stats")}: ${fmtNum(st.requests || 0)} · ✓ ${rate === null ? "—" : rate + "%"} · ${st.requests ? `${Math.round((st.total_ms || 0) / st.requests)} ${t("st.ms")}` : "—"}</div>
        ${st.last_error ? `<div class="p-sub" style="color:var(--warn)">${t("acc.lasterror")}${st.last_error_at ? ` · ${fmtDateTime(st.last_error_at)}` : ""}: ${esc(translateMessage(String(st.last_error)).slice(0, 160))}</div>` : ""}
        <div class="row" style="margin-top:10px; flex-wrap:wrap">
          ${accountsSort === "order" ? `<button class="btn small" data-up="${esc(a.id)}" title="${t("acc.moveup")}" aria-label="${t("acc.moveup")}">▲</button>
          <button class="btn small" data-down="${esc(a.id)}" title="${t("acc.movedown")}" aria-label="${t("acc.movedown")}">▼</button>` : ""}
          <button class="btn small" data-usage="${esc(a.id)}" title="${t("acc.btn.usage")}" aria-label="${t("acc.btn.usage")}">✦</button>
          <button class="btn small" data-check="${esc(a.id)}" title="${t("acc.btn.check")}" aria-label="${t("acc.btn.check")}">✓</button>
          <button class="btn small" data-edit="${esc(a.id)}" title="${t("acc.btn.edit")}" aria-label="${t("acc.btn.edit")}">✎</button>
          <div class="grow"></div>
          <button class="btn small danger" data-del="${esc(a.id)}" title="${t("acc.btn.del")}" aria-label="${t("acc.btn.del")}">✕</button>
        </div>
      </div>`;
    }).join("");

    wireAccountDnd();
    $$("#accounts-grid [data-select]").forEach((el) => el.onchange = () => {
      if (el.checked) accountsSelected.add(el.dataset.select);
      else accountsSelected.delete(el.dataset.select);
      syncAccountBulkBar();
    });
    $$("#accounts-grid [data-up]").forEach((el) => el.onclick = () => moveAccount(el.dataset.up, -1));
    $$("#accounts-grid [data-down]").forEach((el) => el.onclick = () => moveAccount(el.dataset.down, 1));
    $$("#accounts-grid [data-toggle]").forEach((el) => el.onchange = async () => {
      try { await api(`/api/admin/accounts/${encodeURIComponent(el.dataset.toggle)}`, { method: "PUT", body: { enabled: el.checked } }); loadAccounts(); }
      catch (e) { toast(e.message, "err"); loadAccounts(); }
    });
    $$("#accounts-grid [data-check]").forEach((el) => el.onclick = async () => {
      const old = el.innerHTML;
      el.innerHTML = '<span class="spin">◌</span>'; el.disabled = true;
      try {
        const r = await api(`/api/admin/accounts/${encodeURIComponent(el.dataset.check)}/check`, { method: "POST" });
        toast((r.ok ? "✓ " : "✗ ") + translateMessage(r.message), r.ok ? "ok" : "err", 6000);
      } catch (e) { toast(e.message, "err"); }
      finally { el.innerHTML = old; el.disabled = false; loadAccounts(); }
    });
    $$("#accounts-grid [data-del]").forEach((el) => el.onclick = () => {
      confirmDialog(t("acc.btn.del") + "?", t("acc.del.confirm"), async () => {
        try { await api(`/api/admin/accounts/${encodeURIComponent(el.dataset.del)}`, { method: "DELETE" }); toast(t("acc.deleted")); loadAccounts(); if (modelsCache.length) loadModels(); }
        catch (e) { toast(e.message, "err"); }
      });
    });
    $$("#accounts-grid [data-usage]").forEach((el) => el.onclick = () => showAccountUsage(el.dataset.usage));
    $$("#accounts-grid [data-edit]").forEach((el) => el.onclick = () => editAccount(el.dataset.edit));
  } catch (e) { if (e.message !== "401") toast(e.message, "err"); }
}

async function showAccountUsage(accountId) {
  const generation = openModal(`<h3>${t("usage.title")}</h3><p class="muted">…</p>`, true);
  try {
    const r = await api(`/api/admin/accounts/${encodeURIComponent(accountId)}/usage`);
    if (generation !== modalGeneration) return;
    const u = r.usage;
    if (!u || !u.rows || !u.rows.length) {
      const notice = u && ((LANG === "en" && u.note_en) || u.note || (LANG === "en" && u.notice_en) || u.notice);
      openModal(`<h3>${t("usage.title")}</h3><p class="muted">${esc(notice || t("usage.none"))}</p>
        <div class="row-end"><button class="btn" id="m-close">${t("modal.close")}</button></div>`);
      $("#m-close").onclick = closeModal;
      return;
    }
    openModal(`
      <h3>${t("usage.title")}</h3>
      ${u.subscription ? `<p class="muted">${t("usage.sub")} <b style="color:var(--text)">${esc(u.subscription)}</b>${u.email ? " · " + esc(u.email) : ""}</p>` : ""}
      ${(() => {
        // Предупреждение провайдера (например, Google просит подтвердить аккаунт).
        const notice = (LANG === "en" && u.notice_en) || u.notice || "";
        const link = /^https:\/\//i.test(String(u.notice_url || ""))
          ? ` <a href="${esc(u.notice_url)}" target="_blank" rel="noopener noreferrer">${t("usage.notice.open")}</a>` : "";
        return notice ? `<p class="usage-notice">⚠ ${esc(notice)}${link}</p>` : "";
      })()}
      ${(u.rows || []).map((row) => {
        const used = Number(row.used), limit = Number(row.limit);
        // Без предела (оценка «за 7 дней») полоска не нужна: процента там нет.
        const hasLimit = row.limit !== null && row.limit !== undefined && Number.isFinite(limit) && limit > 0;
        const percent = hasLimit && Number.isFinite(used)
          ? Math.max(0, Math.min(100, Math.round(used / limit * 100))) : 0;
        // Бэкенд отдаёт подписи сразу на двух языках (display/display_en) — как
        // error/error_en в логах; русского варианта нет только у переводимых.
        const display = (LANG === "en" && row.display_en) || row.display || "—";
        const note = (LANG === "en" && row.note_en) || row.note || "";
        const unit = (LANG === "en" && row.unit_en) || row.unit || "";
        return `
        <div class="usage-row">
          <span class="u-name" title="${esc(display)}">${esc(display)}</span>
          ${hasLimit
            ? `<div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div>`
            : `<span class="bar-none"></span>`}
          <span class="u-val">${esc(row.used ?? "—")}${hasLimit ? ` / ${esc(row.limit)}` : ""} ${esc(unit)}</span>
          ${row.reset || note ? `<small class="u-note muted">${row.reset ? `${t("usage.reset")} ${esc(fmtDateTime(row.reset))}` : ""}${row.reset && note ? " · " : ""}${esc(note)}</small>` : ""}
        </div>`;
      }).join("") || `<p class="muted">${t("usage.nodata")}</p>`}
      ${u.next_reset ? `<p class="muted">${t("usage.reset")} ${esc(fmtDateTime(u.next_reset))}</p>` : ""}
      <div class="row-end"><button class="btn" id="m-close">${t("modal.close")}</button></div>`, true);
    $("#m-close").onclick = closeModal;
  } catch (e) {
    if (generation === modalGeneration) {
      openModal(`<h3>${t("usage.title")}</h3><div class="log-error selectable">${esc(e.message)}</div>
        <div class="row-end"><button class="btn" id="m-close">${t("modal.close")}</button></div>`);
      $("#m-close").onclick = closeModal;
    }
  }
}

async function editAccount(accountId) {
  const a = accountsCache.find((x) => x.id === accountId);
  if (!a) return;
  const meta = providerMeta(a.provider);
  // Список аккаунтов приходит с замаскированными секретами. Для формы
  // запрашиваем настоящие значения: ключ и куку надо уметь править на месте.
  let secrets = {};
  let realProxy = "";
  try {
    const revealed = await api(`/api/admin/accounts/${encodeURIComponent(a.id)}/secrets`);
    secrets = revealed.fields || {};
    realProxy = revealed.proxy || "";
  } catch (e) { toast(e.message, "warn", 4000); }
  openModal(`
    <h3>${t("acc.edit.title")}</h3>
    ${accountSetupGuide(a.provider)}
    <div class="field"><label for="m-label">${t("acc.label")}</label><input type="text" id="m-label" value="${esc(a.label)}"></div>
    <div class="field"><label for="m-note">${t("acc.note")}</label><textarea id="m-note" rows="2">${esc(a.note || "")}</textarea>
      <div class="help">${t("acc.note.help")}</div></div>
    <div class="field"><label for="m-tags">${t("acc.tags")}</label><input type="text" id="m-tags" value="${esc((a.tags || []).join(", "))}" placeholder="work, backup">
      <div class="help">${t("acc.tags.help")}</div></div>
    <div class="field"><label for="m-proxy">${t("acc.proxy")}</label>
      <input type="text" id="m-proxy" value="${esc(realProxy)}" placeholder="http://… / socks5://…">
      ${a.proxy ? `<label class="dim" style="display:flex;align-items:center;gap:7px;margin-top:6px"><input type="checkbox" id="m-proxy-clear">${t("acc.proxy.clear")}</label>` : ""}</div>
    <div class="field"><label for="m-priority">${t("acc.priority")}</label>
      <select id="m-priority">
        <option value="normal" ${a.priority !== "reserve" ? "selected" : ""}>${t("acc.priority.normal")}</option>
        <option value="reserve" ${a.priority === "reserve" ? "selected" : ""}>${t("acc.priority.reserve")}</option>
      </select></div>
    ${meta.fields.filter((f) => !(a.provider === "custom" && f.key === "name")).map((f) => {
      const id = `m-f-${esc(f.key)}`;
      // Секреты приходят настоящими: поле сразу заполнено и правится на месте,
      // а показ значения включается глазом.
      const existing = String(secrets[f.key] ?? a.fields?.[f.key] ?? "");
      const secret = isSecretField(f);
      let control;
      if (f.type === "select") {
        control = `<select id="${id}"><option value="">— ${t("acc.edit.keepempty")} —</option>${f.options.map((option) => `<option value="${esc(option)}" ${existing === option ? "selected" : ""}>${esc(psLabel(option))}</option>`).join("")}</select>`;
      } else if (f.type === "textarea") {
        control = `<textarea id="${id}" rows="3" class="${secret ? "secret-field" : ""}" placeholder="${esc(f.placeholder || "")}" autocomplete="off" spellcheck="false">${esc(existing)}</textarea>`;
      } else {
        const type = f.type === "url" ? "url" : "text";
        control = `<input id="${id}" type="${type}" class="${secret ? "secret-field" : ""}" value="${esc(existing)}" placeholder="${esc(f.placeholder || "")}" autocomplete="off" spellcheck="false">`;
      }
      const reveal = secret && existing
        ? `<button type="button" class="btn small reveal-btn" data-reveal="${id}" aria-pressed="false">${t("acc.field.show")}</button>` : "";
      // r33 (#12): «Копировать» lives right next to the key it copies (it used to be a lone card button).
      const copy = secret && existing
        ? `<button type="button" class="btn small reveal-btn field-copy-btn" data-copyfield="${id}" title="${esc(t("acc.btn.copykey"))}">${t("acc.field.copy")}</button>` : "";
      return `<div class="field"><label for="${id}">${esc(f.label)}${reveal ? ` ${reveal}` : ""}${copy ? ` ${copy}` : ""}</label>${control}${accountFieldHelp(a.provider, f)}</div>`;
    }).join("")}
    <div class="row" style="margin-top:6px">
      <button class="btn small" id="m-reset-stats">${t("acc.resetstats")}</button>
    </div>
    <div class="row-end">
      <button class="btn" id="m-cancel">${t("acc.cancel")}</button>
      <button class="btn primary" id="m-save">${t("acc.save")}</button>
    </div>`, true);
  $("#m-cancel").onclick = closeModal;
  wireAccountGuides(a.provider, "m-f-", a.id);
  const patch = async (body, message) => {
    try { await api(`/api/admin/accounts/${encodeURIComponent(a.id)}`, { method: "PUT", body }); toast(message); loadAccounts(); }
    catch (e) { toast(e.message, "err"); }
  };
  $$("#modal-box [data-reveal]").forEach((btn) => btn.onclick = () => {
    const field = document.getElementById(btn.dataset.reveal);
    if (!field) return;
    const shown = field.classList.toggle("revealed");
    btn.textContent = t(shown ? "acc.field.hide" : "acc.field.show");
    btn.setAttribute("aria-pressed", String(shown));
  });
  $$("#modal-box [data-copyfield]").forEach((btn) => btn.onclick = () => {
    const field = document.getElementById(btn.dataset.copyfield);
    const value = field ? String(field.value || "").trim() : "";
    if (!value) { toast(t("acc.copykey.empty"), "warn"); return; }
    copyText(value, `${t("acc.copykey.done")}: ${btn.closest(".field")?.querySelector("label")?.firstChild?.textContent?.trim() || ""}`);
  });
  if ($("#m-reset-stats")) $("#m-reset-stats").onclick = () => patch({ reset_stats: true }, t("acc.statsreset"));
  $("#m-save").onclick = async () => {
    const saveButton = $("#m-save");
    saveButton.disabled = true;
    const fields = {};
    meta.fields.forEach((f) => {
      const inp = document.getElementById("m-f-" + f.key);
      // Поля заполнены настоящими значениями, поэтому отправляем их целиком:
      // иначе правка «стереть лишний хвост в куке» не сохранялась бы.
      if (inp) fields[f.key] = inp.value.trim();
    });
    try {
      const update = {
        label: $("#m-label").value, priority: $("#m-priority").value, fields,
        note: $("#m-note").value,
        tags: $("#m-tags").value.split(",").map((x) => x.trim()).filter(Boolean),
      };
      if ($("#m-proxy-clear")?.checked) update.proxy = "";
      else if ($("#m-proxy").value.trim()) update.proxy = $("#m-proxy").value.trim();
      await api(`/api/admin/accounts/${encodeURIComponent(a.id)}`, { method: "PUT", body: update });
      closeModal(); toast(t("acc.saved")); loadAccounts();
    } catch (e) { toast(e.message, "err"); saveButton.disabled = false; }
  };
}

$("#acc-search").addEventListener("input", (e) => { accountsSearch = e.target.value.trim(); loadAccounts(); });
$("#acc-sort").addEventListener("change", (e) => { accountsSort = e.target.value; loadAccounts(); });
$("#companion-top").addEventListener("click", () => $("#companion-fab").click());
$("#acc-select-mode").addEventListener("click", () => {
  // r33 (#19): a button instead of a switch; leaving the mode (button, Esc, «Отменить выделение») clears the selection.
  accountsSelectMode = !accountsSelectMode;
  if (!accountsSelectMode) accountsSelected.clear();
  loadAccounts();
});
$("#acc-select-all").onclick = () => {
  const visible = sortAccounts(accountsCache.filter((a) => (!providerFilter || a.provider === providerFilter) && matchesAccountSearch(a)));
  const all = visible.every((a) => accountsSelected.has(a.id));
  visible.forEach((a) => { if (all) accountsSelected.delete(a.id); else accountsSelected.add(a.id); });
  loadAccounts();
};
$$("#acc-bulk-bar [data-bulk]").forEach((el) => el.onclick = () => accountsBulk(el.dataset.bulk));
$("#acc-export").onclick = () => downloadAuthed("/api/admin/accounts/export", "memuai-accounts.json");

/* --------- добавление аккаунта --------- */

let presetsCache = [];
let musicPresetsCache = [], legacyMusicAvailable = false;

$("#acc-add-btn").onclick = async () => {
  try { await fetchProviders(); }
  catch (error) { toast(error.message, "err"); return; }
  try { presetsCache = (await api("/api/admin/providers/presets")).presets || []; }
  catch (e) { presetsCache = []; }
  try { const music = await api("/api/admin/music/presets"); musicPresetsCache = music.presets || []; legacyMusicAvailable = !!music.legacy_available; }
  catch (e) { musicPresetsCache = []; legacyMusicAvailable = false; }
  openModal(`
    <h3>${t("acc.pick")}</h3>
    <p class="muted acc-pick-guide">${t("acc.pick.guide")}</p>
    <div class="pick-grid">
      ${/* Round 31: «Собственный API» (any key) first, the music API last — it is the rarest pick. */
        providersMeta.slice().sort((a, b) => (b.id === "custom") - (a.id === "custom")).map((p) => {
        const how = t(`acc.how.${p.id}`);
        return `
        <button class="pick-tile${p.id === "custom" ? " is-suggested" : ""}" type="button" data-pick="${esc(p.id)}">
          <div class="t-ico">${esc(p.icon)}</div>
          <div class="t-name">${esc(p.title)}</div>
          <div class="t-desc">${how !== `acc.how.${p.id}` ? esc(how) : (p.oauth ? t("acc.browser") : t("acc.manual"))}</div>
        </button>`; }).join("")}
      <button class="pick-tile music-account-tile" type="button" id="acc-add-music"><div class="t-ico">♫</div><div class="t-name">${LANG === "en" ? "Music API" : "Музыкальный API"}</div><div class="t-desc">ElevenLabs · MiniMax · REST API</div></button>
    </div>
    ${legacyMusicAvailable ? `<button type="button" class="btn music-import" id="acc-import-music">${LANG === "en" ? "Import saved Studio music connection" : "Перенести сохранённое музыкальное подключение Studio"}</button>` : ""}
    ${presetsCache.length ? (window.MemuCatalogR33 ? `<div id="acc-catalog" class="cat-wrap"></div>` : `
      <h4 class="mini-title" style="margin-top:18px">${t("acc.presets")}</h4>
      <p class="muted" style="margin:0 0 8px">${t("acc.presets.hint")}</p>
      <div class="pick-grid">
        ${presetsCache.map((p) => `
          <button class="pick-tile" type="button" data-preset="${esc(p.id)}">
            <div class="t-ico">${esc(p.icon)}</div>
            <div class="t-name">${esc(p.title)}</div>
            <div class="t-desc">${p.in_use ? t("acc.presets.added") : t("acc.presets.key")}</div>
          </button>`).join("")}
      </div>`) : ""}
    <div class="row-end"><button class="btn" id="m-cancel">${t("acc.cancel")}</button></div>`, true);
  $("#m-cancel").onclick = closeModal;
  $("#acc-add-music").onclick = () => { showAccountForm("custom"); const section = $("#proxy-music-setup"); if (section) { section.closest("details.acc-more")?.setAttribute("open", ""); section.open = true; section.scrollIntoView({ block: "start", behavior: "smooth" }); } };
  if ($("#acc-import-music")) $("#acc-import-music").onclick = async () => {
    const button = $("#acc-import-music"); button.disabled = true;
    try { await api("/api/admin/music/import-studio", { method: "POST", body: {} }); closeModal(); loadAccounts(); toast(LANG === "en" ? "Music connection added to Proxy; the Studio copy is preserved." : "Музыкальное подключение добавлено в Proxy; копия Studio сохранена."); }
    catch (error) { toast(error.message, "err"); button.disabled = false; }
  };
  $$("#modal-box [data-pick]").forEach((tile) => tile.onclick = () => showAccountForm(tile.dataset.pick));
  $$("#modal-box [data-preset]").forEach((tile) => tile.onclick = () => showPresetForm(tile.dataset.preset));
  // r33 (#24): the ready-made connections — top services as tiles, «Show all» opens the A–Z searchable list.
  if (window.MemuCatalogR33 && $("#acc-catalog")) MemuCatalogR33.render($("#acc-catalog"), presetsCache);
};

/* Round 31: «Добавить аккаунт» on the home first-run card opens the same picker on the Accounts tab. */
if ($("#start-add")) $("#start-add").onclick = () => {
  $('.nav-item[data-tab="accounts"]')?.click();
  setTimeout(() => $("#acc-add-btn").click(), 60);
};

/* Пресет — это «Собственный API» с уже заполненным адресом и моделями.
   Пользователю остаётся вставить ключ. */
function showPresetForm(presetId) {
  const preset = presetsCache.find((p) => p.id === presetId);
  if (!preset) return;
  if (window.MemuCatalogR33) { MemuCatalogR33.form(preset); return; }   // r33: params, auth note, models
  const used = new Set(accountsCache.filter((a) => a.provider === "custom")
    .map((a) => String(a.fields?.name || "").toLowerCase()));
  let name = preset.name;
  for (let index = 2; used.has(name.toLowerCase()); index += 1) name = `${preset.name}-${index}`;
  const hint = LANG === "en" ? (preset.hint_en || preset.hint) : preset.hint;
  openModal(`
    <h3><span class="chip"><span class="chip-ico">${esc(preset.icon)}</span> ${esc(preset.title)}</span></h3>
    <p class="muted">${esc(hint || "")}${preset.key_url
      ? ` <a href="${esc(preset.key_url)}" target="_blank" rel="noopener noreferrer">${t("acc.presets.where")}</a>` : ""}</p>
    ${accountSetupGuide("custom")}
    <div class="field"><label for="pf-key">${t("acc.presets.apikey")}</label>
      <input type="text" id="pf-key" class="secret-field" autocomplete="off" spellcheck="false" placeholder="sk-…">
      <button type="button" class="btn small reveal-btn" data-reveal="pf-key" aria-pressed="false">${t("acc.field.show")}</button></div>
    <div class="field"><label for="pf-name">${t("acc.presets.prefix")}</label>
      <input type="text" id="pf-name" value="${esc(name)}">
      <div class="help">${t("acc.presets.prefix.help")}: <code>${esc(name)}-gpt-5.6-sol</code></div></div>
    <div class="field"><label for="pf-url">Base URL</label>
      <input type="text" id="pf-url" value="${esc(preset.base_url)}">
      ${preset.edit_url ? `<div class="help" style="color:var(--warn)">${t("acc.presets.editurl")}</div>` : ""}</div>
    <div class="field"><label for="pf-label">${t("acc.label")}</label>
      <input type="text" id="pf-label" placeholder="${esc(preset.title)}"></div>
    <div class="row-end">
      <button class="btn" id="m-cancel">${t("acc.cancel")}</button>
      <button class="btn primary" id="pf-save">${t("acc.addbtn")}</button>
    </div>`, true);
  $("#m-cancel").onclick = closeModal;
  $$("#modal-box [data-reveal]").forEach((btn) => btn.onclick = () => {
    const field = document.getElementById(btn.dataset.reveal);
    const shown = field.classList.toggle("revealed");
    btn.textContent = t(shown ? "acc.field.hide" : "acc.field.show");
  });
  $("#pf-save").onclick = async () => {
    const button = $("#pf-save");
    const key = $("#pf-key").value.trim();
    if (!key) { toast(t("acc.presets.needkey"), "warn"); return; }
    button.disabled = true;
    try {
      const before = await modelIdsSnapshot();
      const added = await api("/api/admin/accounts", { method: "POST", body: {
        provider: "custom",
        label: $("#pf-label").value.trim() || preset.title,
        fields: {
          name: $("#pf-name").value.trim(), base_url: $("#pf-url").value.trim(),
          api_key: key, models: (preset.models || []).join(", "),
          image_models: (preset.image_models || []).join(", "),
          headers: preset.headers ? JSON.stringify(preset.headers) : "",
        },
      }});
      closeModal(); toast(t("acc.oauth.done")); loadAccounts();
      offerNewModels(before, added && added.account && added.account.id);
    } catch (e) { toast(e.message, "err", 6000); button.disabled = false; }
  };
}

function showAccountForm(providerId) {
  const p = providersMeta.find((x) => x.id === providerId);
  if (!p) return;
  if (p.managed) {
    // Local models are not an account form: they live on their own tab.
    closeModal();
    $('.nav-item[data-tab="local"]')?.click();
    return;
  }
  // Round 31: the Custom API form showed 13 fields at once. The four that connect an API stay on top;
  // the key pool, headers, media models and the account's label/note/tags/proxy/priority fold into «Дополнительно».
  const ESSENTIAL = { custom: ["name", "base_url", "api_key", "models"] };
  const essential = ESSENTIAL[providerId];
  const mainFields = essential ? p.fields.filter((f) => essential.includes(f.key) || f.required) : p.fields;
  const extraFields = essential ? p.fields.filter((f) => !mainFields.includes(f)) : [];
  const fieldHtml = (f) => `
      <div class="field">
        <label for="af-${esc(f.key)}">${esc(f.label)}${f.required ? "" : ` <span class="dim">(${t("acc.optional")})</span>`}</label>
        ${f.type === "textarea"
          ? `<textarea id="af-${esc(f.key)}" rows="3" placeholder="${esc(f.placeholder)}"></textarea>`
          : f.type === "select"
            ? `<select id="af-${esc(f.key)}">${f.options.map((o) => `<option value="${esc(o)}">${esc(psLabel(o))}</option>`).join("")}</select>`
          : `<input type="${["password", "url", "number"].includes(f.type) ? f.type : "text"}" id="af-${esc(f.key)}" placeholder="${esc(f.placeholder)}" ${f.type === "password" ? "autocomplete=\"off\"" : ""}>`}
        ${accountFieldHelp(providerId, f)}
      </div>`;
  openModal(`
    <h3><span class="chip"><span class="chip-ico">${esc(p.icon)}</span> ${esc(p.title)}</span></h3>
    <p class="muted">${esc(p.description)}</p>
    ${accountSetupGuide(providerId)}
    ${mainFields.map(fieldHtml).join("")}
    ${p.oauth ? `
      <div class="row" style="flex-direction:column; align-items:stretch; gap:8px">
        <button class="btn primary" id="af-oauth" style="justify-content:center">${t("acc.login.browser")}</button>
        ${providerId === "kiro" ? `<button class="btn" id="af-oauth-device" style="justify-content:center">${t("acc.login.builder")}</button>` : ""}
      </div>
      <div class="oauth-status" id="af-oauth-status" style="display:none"></div>` : ""}
    <details class="acc-more">
    <summary>${t(essential ? "acc.more" : "acc.more.generic")}</summary>
    ${extraFields.map(fieldHtml).join("")}
    <div class="field" style="margin-top:12px"><label for="af-label">${t("acc.label")}</label>
      <input type="text" id="af-label"></div>
    <div class="field"><label for="af-note">${t("acc.note")}</label>
      <input type="text" id="af-note" placeholder="${t("acc.note.help")}"></div>
    <div class="field"><label for="af-tags">${t("acc.tags")}</label>
      <input type="text" id="af-tags" placeholder="work, backup"></div>
    <div class="field"><label for="af-proxy">${t("acc.proxy")}</label>
      <input type="text" id="af-proxy" placeholder="http://… / socks5://…"></div>
    <div class="field"><label for="af-priority">${t("acc.priority")}</label>
      <select id="af-priority">
        <option value="normal">${t("acc.priority.normal")}</option>
        <option value="reserve">${t("acc.priority.reserve")}</option>
      </select></div>
    </details>
    <div class="row-end">
      <button class="btn" id="m-cancel">${t("acc.cancel")}</button>
      <button class="btn primary" id="af-save">${t("acc.addbtn")}</button>
    </div>`, true);
  $("#m-cancel").onclick = closeModal;
  wireAccountGuides(providerId, "af-");
  // Round 34: several keys pasted into the one-line «API key» field were glued into one invalid key (the
  // browser drops line breaks). The first key stays, the rest go to the key pool under «More».
  const keyInput = $("#af-api_key"), poolInput = $("#af-api_keys");
  if (keyInput && poolInput) keyInput.addEventListener("paste", (event) => {
    const keys = String(event.clipboardData?.getData("text") || "").split(/[\r\n;]+/).map((k) => k.trim()).filter(Boolean);
    if (keys.length < 2) return;
    event.preventDefault();
    keyInput.value = keys[0];
    poolInput.value = [poolInput.value.trim(), ...keys.slice(1)].filter(Boolean).join("\n");
    for (let box = poolInput.closest("details"); box; box = box.parentElement?.closest("details")) box.open = true;
    toast(LANG === "en" ? `${keys.length} keys: the first is the main one, the rest went to the key pool`
      : `Ключей: ${keys.length} — первый основной, остальные добавлены в пул ключей`, "ok");
  });

  const save = async () => {
    const saveButton = $("#af-save");
    const fields = {};
    let missing = "";
    p.fields.forEach((f) => {
      const el = document.getElementById("af-" + f.key);
      fields[f.key] = (el ? el.value.trim() : "") || f.default;
      if (f.required && !fields[f.key] && !missing) missing = f.label;
    });
    if (missing) return toast(`✗ ${missing}`, "warn");
    saveButton.disabled = true;
    try {
      const before = await modelIdsSnapshot();
      const added = await api("/api/admin/accounts", { method: "POST", body: {
        provider: providerId, fields, label: $("#af-label").value,
        proxy: $("#af-proxy").value, priority: $("#af-priority").value,
        note: $("#af-note").value,
        tags: $("#af-tags").value.split(",").map((x) => x.trim()).filter(Boolean),
      }});
      closeModal(); toast(t("acc.oauth.done")); loadAccounts();
      offerNewModels(before, added && added.account && added.account.id);
    } catch (e) { toast(e.message, "err"); saveButton.disabled = false; }
  };
  $("#af-save").onclick = save;

  const statusEl = () => $("#af-oauth-status");
  const watch = (flowId) => {
    const el = statusEl();
    const generation = modalGeneration;
    let stopped = false;
    const before = modelIdsSnapshot();
    const stop = () => { stopped = true; clearInterval(timer); };
    const poll = async () => {
      if (stopped || generation !== modalGeneration || !$("#modal-back").classList.contains("visible")) return stop();
      try {
        const st = await api(`/api/admin/oauth/${encodeURIComponent(providerId)}/status/${encodeURIComponent(flowId)}`);
        if (st.status === "success") {
          stop();
          const accountId = st.account && st.account.id;
          if (accountId) {
            const update = {
              priority: $("#af-priority").value,
              note: $("#af-note").value,
              tags: $("#af-tags").value.split(",").map((x) => x.trim()).filter(Boolean),
            };
            if ($("#af-label").value.trim()) update.label = $("#af-label").value.trim();
            if ($("#af-proxy").value.trim()) update.proxy = $("#af-proxy").value.trim();
            try {
              await api(`/api/admin/accounts/${encodeURIComponent(accountId)}`, { method: "PUT", body: update });
            } catch (error) {
              el.className = "oauth-status err";
              el.textContent = `${t("acc.oauth.done")} ${error.message}`;
              loadAccounts();
              return;
            }
          }
          el.className = "oauth-status ok";
          el.textContent = t("acc.oauth.done");
          setTimeout(async () => {
            if (generation !== modalGeneration) return;
            closeModal(); loadAccounts();
            offerNewModels(await before, accountId);
          }, 900);
        } else if (st.status === "error") {
          stop();
          el.className = "oauth-status err";
          el.textContent = st.message || "error";
        }
      } catch (e) { stop(); el.className = "oauth-status err"; el.textContent = e.message; }
    };
    const timer = setInterval(poll, 1500);
    onModalClose(stop);
  };

  if (p.oauth) {
    const browserBtn = $("#af-oauth");
    if (browserBtn) browserBtn.onclick = async () => {
      const el = statusEl();
      el.style.display = "block"; el.className = "oauth-status";
      el.textContent = t("acc.oauth.opening");
      browserBtn.disabled = true;
      try {
        const r = await api(`/api/admin/oauth/${encodeURIComponent(providerId)}/start`, { method: "POST" });
        el.textContent = t("acc.oauth.wait");
        watch(r.flow_id);
      } catch (e) { el.className = "oauth-status err"; el.textContent = e.message; browserBtn.disabled = false; }
    };
    const deviceBtn = $("#af-oauth-device");
    if (deviceBtn) deviceBtn.onclick = async () => {
      const el = statusEl();
      el.style.display = "block"; el.className = "oauth-status";
      el.textContent = t("acc.oauth.opening");
      deviceBtn.disabled = true;
      try {
        const r = await api(`/api/admin/oauth/kiro/device_start`, { method: "POST" });
        el.innerHTML = `${t("acc.oauth.device")} <b style="font-size:20px; color:var(--text)">${esc(r.user_code)}</b><br><span class="dim">${esc(r.verification_uri)}</span>`;
        watch(r.flow_id);
      } catch (e) { el.className = "oauth-status err"; el.textContent = e.message; deviceBtn.disabled = false; }
    };
  }
}

$("#acc-check-all").onclick = async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  try {
    const r = await api("/api/admin/accounts/check_all", { method: "POST" });
    toast(`✓ ${r.ok} / ✗ ${r.failed} / … ${r.skipped}`, r.failed ? "warn" : "ok", 5000);
  } catch (err) { toast(err.message, "err"); }
  finally { button.disabled = false; loadAccounts(); }
};

/* ------------------------------ настройки ------------------------------ */

let settingsStatusTimer = null;
/* Round 23: the status pill of the Settings page (settings.js) shows saving / saved / error and
   lights up the row that changed. Without settings.js the old fade-in label still works. */
function flashSettingsSaved() {
  if (window.MemuSettings) { MemuSettings.saved(true); return; }
  const el = $("#settings-status");
  if (!el) return;
  el.style.opacity = "1";
  clearTimeout(settingsStatusTimer);
  settingsStatusTimer = setTimeout(() => { el.style.opacity = "0"; }, 1600);
}
async function autoSaveSetting(payload) {
  if (window.MemuSettings) MemuSettings.saving();
  try {
    const r = await api("/api/admin/settings", { method: "POST", body: { settings: payload } });
    if (r.rejected && r.rejected.length) {
      toast("✗ " + r.rejected.join(", "), "warn");
      if (window.MemuSettings) MemuSettings.saved(false, r.rejected.join(", "));
      loadSettings();
    }
    else { settingsCache = r.settings; flashSettingsSaved(); }
  } catch (e) {
    toast(e.message, "err");
    if (window.MemuSettings) MemuSettings.saved(false, e.message);
  }
}
/* Value of a settings control: switches send booleans (provider switches send the provider's own
   pair of strings, e.g. "true"/"false" or "on"/"off", exactly what the old select sent). */
function settingValue(el) {
  if (el.type === "checkbox") return el.dataset.on !== undefined ? (el.checked ? el.dataset.on : el.dataset.off) : el.checked;
  return el.value;
}
function setSettingControl(el, value) {
  if (el.type === "checkbox") el.checked = el.dataset.on !== undefined ? String(value) === el.dataset.on : value === true || value === "true";
  else el.value = value;
}
/* Настройки провайдеров строятся из схемы, которую отдаёт сам провайдер:
   добавили поле в settings_fields() — оно сразу появилось в панели.
   Раунд 23: строки в стиле страницы настроек; провайдеры с аккаунтами — сверху, без аккаунтов —
   под «Дополнительно»; да/нет и on/off — переключатели. */
function providerSettingControl(p, f, value) {
  const id = `ps-${esc(p.id)}-${esc(f.key)}`;
  const attrs = `id="${id}" data-psetting="${esc(p.id)}" data-key="${esc(f.key)}"`;
  const options = (f.options || []).map(String);
  const pair = options.length === 2 && options.includes("true") && options.includes("false") ? ["true", "false"]
    : options.length === 2 && options.includes("on") && options.includes("off") ? ["on", "off"] : null;
  if (f.type === "select" && pair) {
    return `<label class="switch"><input type="checkbox" role="switch" ${attrs} data-on="${pair[0]}" data-off="${pair[1]}" ${value === pair[0] ? "checked" : ""}><span class="slider"></span></label>`;
  }
  if (f.type === "select") {
    return `<select ${attrs}>${options.map((option) => `<option value="${esc(option)}" ${value === option ? "selected" : ""}>${esc(psLabel(option))}</option>`).join("")}</select>`;
  }
  if (f.type === "number") return `<span class="sg-num"><input type="number" ${attrs} value="${esc(value)}"></span>`;
  return `<input type="${f.type === "password" ? "password" : "text"}" ${attrs} value="${esc(value)}" placeholder="${esc(f.placeholder || "")}" spellcheck="false">`;
}
function renderProviderSettings() {
  const box = $("#provider-settings");
  if (!box) return;
  const stored = settingsCache.provider_settings || {};
  const groupOf = (p) => {
    const fields = p.settings || [];
    const cfg = stored[p.id] || {};
    const count = accountsCache.filter((a) => a.provider === p.id).length;
    const rows = fields.map((f) => {
      const value = f.key in cfg ? String(cfg[f.key]) : String(f.default ?? "");
      return `<div class="sg-row" data-k="${esc(`${p.id} ${p.title} ${f.key} ${f.label_en || ""}`)}">
        <div class="sg-text"><label class="sg-label" for="ps-${esc(p.id)}-${esc(f.key)}">${esc(f.label)}</label>${f.help ? `<div class="sg-desc">${esc(f.help)}</div>` : ""}</div>
        <div class="sg-ctl">${providerSettingControl(p, f, value)}</div></div>`;
    }).join("");
    const meta = [count ? `${t("sg.prov.accounts")}: ${count}` : t("set.provider.noacc")];
    if (p.native_search) meta.push(t("sg.prov.native"));
    return `<div class="sg-group sg-prov" data-provider="${esc(p.id)}">
      <h4 class="sg-gtitle">${esc(p.title)}<span class="sg-gmeta">${esc(meta.join(" · "))}</span></h4>
      <div class="sg-card">${rows}</div></div>`;
  };
  const withSettings = providersMeta.filter((p) => (p.settings || []).length);
  const used = withSettings.filter((p) => accountsCache.some((a) => a.provider === p.id));
  const idle = withSettings.filter((p) => !accountsCache.some((a) => a.provider === p.id));
  box.innerHTML = !withSettings.length ? `<div class="empty">${esc(t("sg.prov.none"))}</div>`
    : used.map(groupOf).join("") + (idle.length ? `<details class="sg-more sg-more-wrap"${used.length ? "" : " open"}>
      <summary><span class="sg-more-t">${esc(used.length ? t("sg.more") : t("sg.more.providers"))}</span><span class="sg-more-hint">${esc(used.length ? `${t("sg.more.providers")} · ${idle.length}` : idle.map((p) => p.title).join(", "))}</span></summary>
      ${idle.map(groupOf).join("")}</details>` : "");
  $$("#provider-settings [data-psetting]").forEach((el) => el.addEventListener("change", () => {
    autoSaveSetting({ provider_settings: { [el.dataset.psetting]: { [el.dataset.key]: settingValue(el) } } });
  }));
  if (window.MemuSettings) MemuSettings.sync();
}

function psLabel(option) {
  const key = "set.opt." + String(option);
  const translated = t(key);
  return translated === key ? String(option) : translated;
}

async function loadSettings() {
  try {
    const data = await api("/api/admin/settings");
    settingsCache = data.settings;
    try { await fetchProviders(); } catch (e) { /* метаданные необязательны */ }
    if (!accountsCache.length) { try { accountsCache = (await api("/api/admin/accounts")).accounts || []; } catch (e) { /* счётчики необязательны */ } }
    renderProviderSettings();
    $$("[data-setting]").forEach((el) => {
      if (el.dataset.setting in settingsCache) setSettingControl(el, settingsCache[el.dataset.setting]);
    });
    $$("[data-psetting]").forEach((el) => {
      const cfg = (settingsCache.provider_settings || {})[el.dataset.psetting] || {};
      if (el.dataset.key in cfg) setSettingControl(el, String(cfg[el.dataset.key]));
    });
    loadSearchEngines();
    const keywords = $("#models-keywords");
    if (keywords) keywords.value = (settingsCache.model_hidden_keywords || []).join(", ");
    const o = await api("/api/admin/overview");
    $("#default-provider-select").innerHTML = `<option value="">—</option>` +
      o.prefixes.map((p) => `<option value="${esc(p)}" ${settingsCache.default_provider === p ? "selected" : ""}>${esc(p)}</option>`).join("");
    if ($("#sg-version")) $("#sg-version").textContent = o.version || "—";
    if (window.MemuSettings) MemuSettings.sync();
  } catch (e) { if (e.message !== "401") toast(e.message, "err"); }
}
/* Веб-поиск Proxy: источников много, у каждого свой ключ и адрес — они хранятся по
   источникам, поэтому при смене источника ничего не теряется. */
let searchEngines = [];
function renderSearchSettings() {
  const select = $("#search-engine");
  if (!select || !searchEngines.length) return;
  const en = LANG === "en";
  const current = searchEngines.some((row) => row.id === settingsCache.search_engine) ? settingsCache.search_engine : "auto";
  const groups = [["free", t("set.search.group.free")], ["server", t("set.search.group.server")], ["key", t("set.search.group.key")]];
  select.innerHTML = groups.map(([id, title]) => `<optgroup label="${esc(title)}">${searchEngines.filter((row) => row.group === id)
    .map((row) => `<option value="${esc(row.id)}" ${row.id === current ? "selected" : ""}>${esc(en ? row.label_en : row.label)}</option>`).join("")}</optgroup>`).join("");
  const row = searchEngines.find((item) => item.id === current);
  $("#search-key-field").hidden = !row.key;
  $("#search-url-field").hidden = !row.url;
  $("#search-key-label").textContent = t("set.search.key") + (row.key === "optional" ? " · " + t("set.search.optional") : "");
  $("#search-key").placeholder = row.key_hint || "••••••••";
  $("#search-key").value = (settingsCache.search_keys || {})[current] || "";
  $("#search-url-label").textContent = row.url === "cx" ? t("set.search.cx") : t("set.search.url");
  $("#search-url").placeholder = row.url_hint || "";
  $("#search-url").value = (settingsCache.search_urls || {})[current] || "";
  $("#search-note").textContent = (en ? row.note_en : row.note) + (row.site ? ` ${t("set.search.where")} ${row.site}` : "");
  $("#search-test-result").textContent = "";
}
async function loadSearchEngines() {
  if (!searchEngines.length) {
    try { searchEngines = (await api("/api/admin/search/engines")).engines || []; } catch (e) { searchEngines = []; }
  }
  renderSearchSettings();
}
if ($("#search-engine")) {
  $("#search-engine").addEventListener("change", async (e) => {
    await autoSaveSetting({ search_engine: e.target.value });
    renderSearchSettings();
  });
  $("#search-key").addEventListener("change", (e) => autoSaveSetting({ search_keys: { [$("#search-engine").value]: e.target.value.trim() } }));
  $("#search-url").addEventListener("change", (e) => autoSaveSetting({ search_urls: { [$("#search-engine").value]: e.target.value.trim() } }));
  $("#search-test").addEventListener("click", async () => {
    const out = $("#search-test-result");
    out.textContent = t("set.search.testing");
    try {
      const r = await api("/api/admin/search/test", { method: "POST", body: { query: "weather today" } });
      out.textContent = `✓ ${r.count} · ${r.ms} ${LANG === "en" ? "ms" : "мс"}${r.first ? " · " + r.first.title.slice(0, 60) : ""}`;
    } catch (e) { out.textContent = "✗ " + e.message; }
  });
}

$$("[data-setting]").forEach((el) => el.addEventListener("change", () => {
  if (!el.checkValidity()) {
    el.reportValidity();
    loadSettings();
    return;
  }
  autoSaveSetting({ [el.dataset.setting]: settingValue(el) });
}));
$("#default-provider-select").addEventListener("change", (e) => autoSaveSetting({ default_provider: e.target.value }));

$("#password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#pwd-change");
  const oldPassword = $("#pwd-old").value;
  const newPassword = $("#pwd-new").value;
  if (newPassword.length < 8) {
    toast(LANG === "en" ? "Use at least 8 characters" : "Используйте не менее 8 символов", "warn");
    return;
  }
  button.disabled = true;
  try {
    const result = await api("/api/admin/password", {
      method: "POST", body: { old: oldPassword, new: newPassword },
    });
    $("#pwd-old").value = "";
    $("#pwd-new").value = "";
    $("#default-password-strip").hidden = true;
    TOKEN = "";
    sessionStorage.removeItem("oh_token");
    toast(LANG === "en" ? "Panel password changed" : "Пароль панели изменён");
    if (result.reauth_required) showLogin();
    else showApp();
  } catch (error) {
    toast(error.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("#app-shutdown").onclick = () => {
  confirmDialog(t("set.shutdown"),
    LANG === "en" ? "The API and background tasks will stop." : "API и фоновые задачи будут остановлены.",
    async () => {
    try {
      await api("/api/admin/shutdown", { method: "POST" });
      document.body.innerHTML = "<div style='display:flex;height:100vh;align-items:center;justify-content:center;color:#8b93a8;font-family:sans-serif'>MemuAI Proxy ⏻</div>";
      setTimeout(() => window.close(), 1200);
    } catch (e) { toast(e.message, "err"); }
  }, t("set.shutdown"));
};

/* --------- каталог моделей --------- */

function visibleModels() {
  const needle = modelsFilter.toLowerCase();
  return modelsCache.filter((m) => {
    if (modelsProviderFilter && m.provider !== modelsProviderFilter) return false;
    if (modelsStateFilter === "on" && !m.enabled) return false;
    if (modelsStateFilter === "off" && m.enabled) return false;
    if (window.MemuModelCaps && !MemuModelCaps.passes(m.id)) return false;
    if (!needle) return true;
    const hay = `${m.id || ""} ${proxyModelName(m)} ${m.note || ""} ${m.source || ""}`.toLowerCase();
    return hay.includes(needle) || needle.split(/\s+/).filter(Boolean).every((word) => hay.includes(word));
  });
}

function syncModelsBulkBar() {
  const bar = $("#models-bulk-bar");
  if (!bar) return;
  // Панель всегда на месте: раньше она появлялась над списком при первом выделении и
  // сдвигала его вниз — второй щелчок «по той же модели» попадал уже в другую строку.
  const idle = modelsSelected.size === 0;
  bar.hidden = false;
  bar.classList.toggle("idle", idle);
  $$("#models-bulk-bar [data-mbulk]").forEach((button) => { button.disabled = idle; });
  $("#models-bulk-count").textContent = idle ? t("bulk.models.hint") : `${modelsSelected.size} ${t("bulk.selected")}`;
}

/* Выделение строк каталога мышью — как в проводнике Windows: клик выделяет
   одну строку, Ctrl — добавляет/снимает, Shift — диапазон от последней.
   Управляет тем же Set modelsSelected, что и чекбоксы, поэтому bulk-бар и
   массовые действия работают без изменений. */
let lastPickedModel = "";

function syncModelsSelection() {
  $$("#models-table tr[data-mid]").forEach((tr) => {
    const on = modelsSelected.has(tr.dataset.mid);
    tr.classList.toggle("selected", on);
    const box = tr.querySelector("input[data-msel]");
    if (box) box.checked = on;
  });
  syncModelsBulkBar();
}

function pickModel(modelId, event) {
  // порядок строк берём из DOM: при группировке он отличается от видимого списка
  const visible = $$("#models-table tr[data-mid]").map((tr) => tr.dataset.mid);
  if (event && event.shiftKey && lastPickedModel) {
    const from = visible.indexOf(lastPickedModel);
    const to = visible.indexOf(modelId);
    if (from >= 0 && to >= 0) {
      const [start, end] = from <= to ? [from, to] : [to, from];
      // как в Windows: Shift заменяет выделение диапазоном, Ctrl+Shift добавляет
      if (!event.ctrlKey && !event.metaKey) modelsSelected.clear();
      visible.slice(start, end + 1).forEach((id) => modelsSelected.add(id));
      syncModelsSelection();
      return;
    }
  }
  if (event && (event.ctrlKey || event.metaKey)) {
    if (modelsSelected.has(modelId)) modelsSelected.delete(modelId);
    else modelsSelected.add(modelId);
  } else if (event && modelsSelected.size === 1 && modelsSelected.has(modelId)) {
    // щелчок по единственной выделенной строке снимает выделение — второй раз искать пустое место не нужно
    modelsSelected.clear();
  } else {
    modelsSelected.clear();
    modelsSelected.add(modelId);
  }
  lastPickedModel = modelId;
  syncModelsSelection();
}

/* Выделение рамкой и с клавиатуры — как на рабочем столе Windows: потяните мышью по
   пустому месту или по строкам, и всё, что попало в рамку, выделится (Ctrl — добавить
   к уже выделенному). Ctrl+A — все видимые, Esc — снять, стрелки — двигать выделение
   (Shift — растягивать), Delete — удалить выделенное, Ctrl+C — скопировать их ID. */
const marquee = { active: false, pending: false, x0: 0, y0: 0, base: new Set(), box: null, scrollTimer: 0,
  lastX: 0, lastY: 0, suppressClick: false, scroll0: 0, page0: 0 };

function marqueeRows() {
  return $$("#models-table tr[data-mid]");
}

/* Начало рамки привязано к списку, а не к экрану: когда список прокручивается (у края
   или колесом), точка, с которой начали тянуть, уезжает вместе со строками. Иначе рамка
   теряла всё, что ушло за край, и выделялись только видимые модели. */
function marqueeStartY() {
  const list = $("#models-table");
  return marquee.y0 - ((list ? list.scrollTop : 0) - marquee.scroll0) - (window.scrollY - marquee.page0);
}

function marqueeApply() {
  const startY = marqueeStartY();
  const left = Math.min(marquee.x0, marquee.lastX), right = Math.max(marquee.x0, marquee.lastX);
  const top = Math.min(startY, marquee.lastY), bottom = Math.max(startY, marquee.lastY);
  const picked = new Set(marquee.additive ? marquee.base : []);
  marqueeRows().forEach((tr) => {
    const r = tr.getBoundingClientRect();
    if (r.right >= left && r.left <= right && r.bottom >= top && r.top <= bottom) {
      if (marquee.additive && marquee.base.has(tr.dataset.mid)) picked.delete(tr.dataset.mid);
      else picked.add(tr.dataset.mid);
    }
  });
  modelsSelected = picked;
  syncModelsSelection();
}

function marqueeDraw() {
  if (!marquee.box) {
    marquee.box = document.createElement("div");
    marquee.box.className = "marquee-box";
    document.body.appendChild(marquee.box);
  }
  // рамку рисуем только в пределах списка: её начало может быть уже прокручено за край
  const list = $("#models-table");
  const bounds = list ? list.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
  const startY = Math.max(bounds.top, Math.min(bounds.bottom, marqueeStartY()));
  const left = Math.min(marquee.x0, marquee.lastX), top = Math.min(startY, marquee.lastY);
  Object.assign(marquee.box.style, {
    left: `${left}px`, top: `${top}px`,
    width: `${Math.abs(marquee.lastX - marquee.x0)}px`, height: `${Math.abs(marquee.lastY - startY)}px`,
  });
}

function marqueeScrolled() {
  if (!marquee.active) return;
  marqueeDraw();
  marqueeApply();
}

function marqueeEnd() {
  window.removeEventListener("mousemove", marqueeMove, true);
  window.removeEventListener("mouseup", marqueeEnd, true);
  window.removeEventListener("scroll", marqueeScrolled, true);
  clearInterval(marquee.scrollTimer);
  marquee.scrollTimer = 0;
  if (marquee.box) { marquee.box.remove(); marquee.box = null; }
  document.body.classList.remove("marquee-on");
  if (marquee.active) marquee.suppressClick = true;      // the click that ends a drag must not reselect one row
  marquee.active = marquee.pending = false;
  setTimeout(() => { marquee.suppressClick = false; }, 60);
}

function marqueeMove(event) {
  marquee.lastX = event.clientX;
  marquee.lastY = event.clientY;
  if (!marquee.active) {
    if (Math.hypot(event.clientX - marquee.x0, event.clientY - marquee.y0) < 5) return;
    marquee.active = true;
    document.body.classList.add("marquee-on");
    window.getSelection()?.removeAllRanges();
    // near the edge of the scrolling list the drag keeps scrolling it, like a file manager does
    marquee.scrollTimer = setInterval(() => {
      const list = $("#models-table");
      if (!list || !marquee.active) return;
      const box = list.getBoundingClientRect();
      // чем дальше курсор за краем, тем быстрее листается — как в проводнике
      const over = marquee.lastY > box.bottom - 28 ? marquee.lastY - (box.bottom - 28)
        : marquee.lastY < box.top + 28 ? marquee.lastY - (box.top + 28) : 0;
      const step = Math.sign(over) * Math.min(60, 12 + Math.abs(over) * 0.5);
      if (over) { list.scrollTop += step; marqueeDraw(); marqueeApply(); }
    }, 40);
  }
  event.preventDefault();
  marqueeDraw();
  marqueeApply();
}

(function bindModelsSelection() {
  const list = $("#models-table");
  if (!list) return;
  list.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || event.shiftKey) return;
    // switches, buttons and links keep working exactly as before
    if (event.target.closest("button, input, label, a, select, textarea, .switch, thead")) return;
    if (event.offsetX > list.clientWidth) return;            // the scroll bar
    marquee.pending = true;
    marquee.active = false;
    marquee.additive = event.ctrlKey || event.metaKey;
    marquee.base = new Set(modelsSelected);
    marquee.x0 = marquee.lastX = event.clientX;
    marquee.y0 = marquee.lastY = event.clientY;
    marquee.scroll0 = list.scrollTop;
    marquee.page0 = window.scrollY;
    window.addEventListener("mousemove", marqueeMove, true);
    window.addEventListener("mouseup", marqueeEnd, true);
    window.addEventListener("scroll", marqueeScrolled, true);   // колесо во время протяжки
  });
  // capture: runs before the per-row click that would collapse the selection to one row
  list.addEventListener("click", (event) => {
    if (marquee.suppressClick) { event.stopPropagation(); event.preventDefault(); marquee.suppressClick = false; return; }
    // a click on empty space clears the selection, as on the desktop
    if (!event.target.closest("tr[data-mid], button, input, label, a, select, .model-group-head") && modelsSelected.size) {
      modelsSelected.clear();
      syncModelsSelection();
    }
  }, true);
})();

document.addEventListener("keydown", (event) => {
  const view = $("#models-table");
  if (!view || !view.offsetParent) return;                   // the models tab is not on screen
  const target = event.target;
  if (target && target.closest && target.closest("input, textarea, select, [contenteditable]")) return;
  if (document.querySelector(".modal-back:not([hidden]), #modal:not([hidden])")) return;
  const rows = marqueeRows().map((tr) => tr.dataset.mid);
  if (!rows.length) return;
  const key = event.key;
  if ((event.ctrlKey || event.metaKey) && (key === "a" || key === "A")) {
    event.preventDefault();
    rows.forEach((id) => modelsSelected.add(id));
    syncModelsSelection();
  } else if (key === "Escape" && modelsSelected.size) {
    modelsSelected.clear();
    syncModelsSelection();
  } else if (key === "Delete" && modelsSelected.size) {
    event.preventDefault();
    modelsBulk("delete");
  } else if ((event.ctrlKey || event.metaKey) && (key === "c" || key === "C") && modelsSelected.size
      && !String(window.getSelection() || "")) {
    event.preventDefault();
    const text = rows.filter((id) => modelsSelected.has(id)).join("\n");
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(
      () => toast(`${modelsSelected.size} ${LANG === "en" ? "IDs copied" : "ID скопировано"}`, "ok"), () => {});
  } else if (key === "ArrowDown" || key === "ArrowUp") {
    event.preventDefault();
    const current = lastPickedModel && rows.includes(lastPickedModel) ? rows.indexOf(lastPickedModel) : -1;
    const next = Math.max(0, Math.min(rows.length - 1, current + (key === "ArrowDown" ? 1 : -1)));
    const id = rows[next];
    if (event.shiftKey && current >= 0) {
      const [a, b] = current <= next ? [current, next] : [next, current];
      if (!event.ctrlKey) modelsSelected.clear();
      rows.slice(a, b + 1).forEach((rid) => modelsSelected.add(rid));
      syncModelsSelection();
    } else {
      pickModel(id, null);
    }
    if (!event.shiftKey) lastPickedModel = id;
    document.querySelector(`#models-table tr[data-mid="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "nearest" });
  }
});

function compactTokens(value) {
  const n = Number(value) || 0;
  return n >= 1_000_000 ? `${+(n / 1_000_000).toFixed(n % 1_000_000 ? 2 : 0)}M` : n >= 1000 ? `${Math.round(n / 1000)}K` : String(n);
}

/* Where the context window of a model came from (see app/model_info.py). */
function contextSourceLabel(info) {
  if (!info || !info.context) return LANG === "en" ? "Unknown: the client uses its default" : "Неизвестно: клиент возьмёт значение по умолчанию";
  const source = String(info.source || "");
  const where = source === "local" ? (LANG === "en" ? "llama.cpp launch setting" : "настройка запуска llama.cpp")
    : source.startsWith("api") ? (LANG === "en" ? "reported by the provider" : "сообщил провайдер")
    : source === "family" ? (LANG === "en" ? "estimate for the family" : "оценка по семейству")
    : (LANG === "en" ? "known model" : "известная модель") + (info.label ? ` · ${info.label}` : "");
  const output = info.output ? (LANG === "en" ? ` · answer up to ${compactTokens(info.output)}` : ` · ответ до ${compactTokens(info.output)}`) : "";
  return `${LANG === "en" ? "Context" : "Контекст"}: ${Number(info.context).toLocaleString()} — ${where}${output}`;
}

/* Round 33 (r33c-models-lists): the readable name every window shows (static/model-names.js — the same rules
   and test vectors as app/model_names.py); the full id stays beside it, smaller and muted. */
function proxyModelName(m) {
  try { if (window.MemuModelNames && m && m.id) return MemuModelNames.pretty(m.id, { prefix: m.prefix || "" }) || m.id; } catch (e) {}
  return (m && m.id) || "";
}
function proxyModelCell(m) {
  const name = proxyModelName(m);
  return name && name !== m.id
    ? `<span class="pm-name">${esc(name)}</span><span class="pm-id mono">${esc(m.id)}</span>`
    : `<span class="pm-name mono">${esc(m.id)}</span>`;
}

/* Round 34: with several «Собственный API» connections every row said the same «Custom API» + «custom
   connection» — the chip now names the connection (OpenRouter, aws9 …) and the stock note is left out. */
const STOCK_MODEL_NOTES = new Set(["кастомное подключение"]);
function modelNote(m) {
  return STOCK_MODEL_NOTES.has(String(m.note || "").trim()) ? "" : translateMessage(m.note || "");
}
function modelSourceChip(m) {
  const kind = providerMeta(m.provider).title;
  const account = m.provider === "custom" && m.account_id ? accountsCache.find((a) => a.id === m.account_id) : null;
  const label = account ? String(account.label || "").split(" · ")[0].trim() : "";
  return label ? `<span class="chip" title="${esc(kind)} · ${esc(account.label)}">${esc(label)}</span>`
    : `<span class="chip">${esc(kind)}</span>`;
}

function modelRow(m) {
  const unavailable = m.availability?.available === false;
  const cls = [m.enabled && !unavailable ? "" : "row-off", modelsSelected.has(m.id) ? "selected" : ""].filter(Boolean).join(" ");
  // Round 22: значки возможностей и кнопка карточки модели (static/model_caps.js).
  const caps = window.MemuModelCaps ? MemuModelCaps.badges(m.id) : "";
  return `
    <tr class="${cls}" data-mid="${esc(m.id)}">
      <td><input type="checkbox" data-msel="${esc(m.id)}" ${modelsSelected.has(m.id) ? "checked" : ""} aria-label="${esc(m.id)}"></td>
      <td><label class="switch"><input type="checkbox" data-mtoggle="${esc(m.id)}" aria-label="${esc(m.id)}" ${m.enabled ? "checked" : ""}><span class="slider"></span></label></td>
      <td class="pm-cell" title="${esc(m.id)}">${proxyModelCell(m)}${unavailable ? `<span class="pm-note" title="${esc(m.availability.reason || "")}">${LANG === "en" ? "Unavailable for the connected accounts / client" : "Недоступна для подключённых аккаунтов / клиента"}</span>` : ""}${modelNote(m) ? `<span class="pm-note" title="${esc(modelNote(m))}">${esc(modelNote(m))}</span>` : ""}</td>
      <td>${modelSourceChip(m)}</td>
      <td class="mcap-cell">${caps}</td>
      <td class="mono dim" title="${esc(contextSourceLabel(m.context))}">${m.context && m.context.context ? esc(compactTokens(m.context.context)) : "—"}</td>
      <td class="actions"><button class="btn small mcap-info-btn" data-minfo="${esc(m.id)}" title="${esc(t("mcap.open"))}" aria-label="${esc(t("mcap.open"))} ${esc(m.id)}"><i class="mcap-ic" data-i="info" aria-hidden="true"></i></button><button class="btn small danger" data-mdel="${esc(m.id)}" aria-label="${t("acc.btn.del")} ${esc(m.id)}">✕</button></td>
    </tr>`;
}

/* Round 22: список рисуется из кэша — фильтры и значки больше не перезапрашивают /api/admin/models
   на каждую букву. Длинный плоский список дорисовывается порциями (первые строки сразу, остальное
   в простое), обработчики — делегированием на #models-table, поэтому дорисованные строки работают так же. */
const MODELS_FIRST_ROWS = 160;
const MODELS_CHUNK = 220;
let modelsRenderToken = 0;

async function loadModels() {
  try {
    const data = await api("/api/admin/models");
    modelsCache = data.models;
    if (!accountsCache.length && modelsCache.some((m) => m.provider === "custom")) {
      try { accountsCache = (await api("/api/admin/accounts")).accounts || []; } catch (e) { /* the chip falls back to the kind */ }
    }
    const alive = new Set(modelsCache.map((m) => m.id));
    modelsSelected = new Set(Array.from(modelsSelected).filter((id) => alive.has(id)));
    if (window.MemuModelCaps) MemuModelCaps.load();
    renderModelsTable();
  } catch (e) { if (e.message !== "401") toast(e.message, "err"); }
}

function syncGensparkModelTools() {
  // Инструменты каталога Genspark без аккаунта Genspark только возвращали его модели в список.
  const live = accountsCache.some((a) => a.provider === "genspark");
  for (const id of ["#models-gs-config", "#models-gs-discover"]) {
    const button = $(id);
    if (button) button.hidden = !live;
  }
}

function renderModelsTable() {
  const view = $("#models-table");
  if (!view) return;
  syncGensparkModelTools();
  const token = ++modelsRenderToken;
  $("#models-count").textContent = `${visibleModels().length} / ${modelsCache.length}`;
  if ($("#sg-nav-models-count")) $("#sg-nav-models-count").textContent = modelsCache.length || "";
  const providers = [...new Set(modelsCache.map((m) => m.provider))].sort();
  $("#models-provider-filter").innerHTML = `<option value="">${t("logs.filter.all")}</option>` +
    providers.map((id) => `<option value="${esc(id)}" ${modelsProviderFilter === id ? "selected" : ""}>${esc(providerMeta(id).title || id)}</option>`).join("");
  if (window.MemuModelCaps) MemuModelCaps.renderFilters();

  const models = visibleModels();
  syncModelsBulkBar();
  if (!models.length) {
    view.innerHTML = `<div class="empty">${t("set.models.none")}</div>`;
    return;
  }
  const head = `<thead><tr><th></th><th>↯</th><th>${LANG === "en" ? "Model" : "Модель"}</th><th>${t("set.models.provider")}</th><th>${esc(t("mcap.col"))}</th><th title="${esc(LANG === "en" ? "Context window, detected automatically" : "Окно контекста, определяется автоматически")}">${LANG === "en" ? "Context" : "Контекст"}</th><th></th></tr></thead>`;
  if (modelsGroup === "provider") {
    const groups = {};
    models.forEach((m) => { (groups[m.provider] = groups[m.provider] || []).push(m); });
    view.innerHTML = Object.keys(groups).sort().map((id) => `
      <div class="model-group">
        <div class="model-group-head">
          <b>${esc(providerMeta(id).title || id)}</b>
          <span class="chip">${groups[id].length}</span>
          <div class="grow"></div>
          <button class="btn small" data-gsel="${esc(id)}">${t("set.models.selectgroup")}</button>
          <button class="btn small" data-grefresh="${esc(id)}">${t("set.models.refreshone")}</button>
        </div>
        <table class="tbl">${head}<tbody>${groups[id].map(modelRow).join("")}</tbody></table>
      </div>`).join("");
    $$("#models-table [data-gsel]").forEach((el) => el.onclick = () => {
      const list = models.filter((m) => m.provider === el.dataset.gsel);
      const all = list.every((m) => modelsSelected.has(m.id));
      list.forEach((m) => { if (all) modelsSelected.delete(m.id); else modelsSelected.add(m.id); });
      renderModelsTable();
    });
    $$("#models-table [data-grefresh]").forEach((el) => el.onclick = () => refreshModels([el.dataset.grefresh], el));
    return;
  }
  view.innerHTML = `<table class="tbl">${head}<tbody>${models.slice(0, MODELS_FIRST_ROWS).map(modelRow).join("")}</tbody></table>`;
  let next = MODELS_FIRST_ROWS;
  const body = view.querySelector("tbody");
  const step = () => {
    if (token !== modelsRenderToken || next >= models.length) return;
    body.insertAdjacentHTML("beforeend", models.slice(next, next + MODELS_CHUNK).map(modelRow).join(""));
    next += MODELS_CHUNK;
    if (next < models.length) setTimeout(step, 16);
  };
  if (next < models.length) setTimeout(step, 0);
}

(function bindModelsTableEvents() {
  const view = $("#models-table");
  if (!view) return;
  view.addEventListener("change", async (event) => {
    const box = event.target.closest("input[data-msel]");
    if (box) {
      if (box.checked) modelsSelected.add(box.dataset.msel);
      else modelsSelected.delete(box.dataset.msel);
      // чекбокс и подсветка строки — одно и то же выделение
      box.closest("tr[data-mid]")?.classList.toggle("selected", box.checked);
      syncModelsBulkBar();
      return;
    }
    const toggle = event.target.closest("input[data-mtoggle]");
    if (toggle) {
      try {
        const m = modelsCache.find((x) => x.id === toggle.dataset.mtoggle);
        if (!m) return;
        await api("/api/admin/models", { method: "POST", body: { prefix: m.prefix, model: m.model, enabled: toggle.checked } });
        loadModels();
      } catch (e) { toast(e.message, "err"); loadModels(); }
    }
  });
  view.addEventListener("click", (event) => {
    const del = event.target.closest("[data-mdel]");
    if (del) {
      confirmDialog(t("acc.btn.del") + "?", del.dataset.mdel, async () => {
        try { await api(`/api/admin/models/${encodeURIComponent(del.dataset.mdel)}`, { method: "DELETE" }); loadModels(); }
        catch (e) { toast(e.message, "err"); }
      });
      return;
    }
    const info = event.target.closest("[data-minfo]");
    if (info) {
      if (window.MemuModelCaps) MemuModelCaps.open(info.dataset.minfo);
      return;
    }
    const tr = event.target.closest("tr[data-mid]");
    // контролы строки работают как раньше: клик по ним не перехватываем
    if (!tr || event.target.closest("button, input, label, a, select, textarea")) return;
    pickModel(tr.dataset.mid, event);
  });
  view.addEventListener("dblclick", (event) => {
    const tr = event.target.closest("tr[data-mid]");
    if (!tr || event.target.closest("button, input, label, a, select, textarea")) return;
    if (window.MemuModelCaps) MemuModelCaps.open(tr.dataset.mid);
  });
  // Shift+клик не должен растягивать текстовое выделение по таблице
  view.addEventListener("mousedown", (event) => {
    if (event.shiftKey && event.target.closest("tr[data-mid]")) event.preventDefault();
  });
})();

/* После добавления аккаунта с большим каталогом (свой API, готовое подключение, провайдер с
   десятками моделей) сразу спрашиваем, какие модели оставить включёнными. Все включены;
   «Выключить все» + поиск позволяют быстро оставить только нужные. Выключенные модели не
   удаляются — они остаются в каталоге, их можно включить позже в «Настройки → Модели». */
const MODEL_PICK_MIN = 30;

async function modelIdsSnapshot() {
  try { return new Set(((await api("/api/admin/models")).models || []).map((m) => m.id)); }
  catch (e) { return null; }
}

async function offerNewModels(before, accountId) {
  // Новый аккаунт сразу опрашивается: его модели появляются в каталоге без похода в
  // «Настройки → Обновить с серверов». Опрос только этого аккаунта, чужие модели не чистятся.
  if (accountId) {
    try {
      const r = await api("/api/admin/models/refresh", { method: "POST", body: { accounts: [accountId], prune: false } });
      if (r.added) toast(`${LANG === "en" ? "Models added" : "Добавлено моделей"}: ${r.added}`, "ok");
    } catch (e) { /* каталог из набора по умолчанию уже есть */ }
    loadModels(); loadHome();
    // Round 34: a new card stayed «not checked» although its key had just been used — check it once.
    api(`/api/admin/accounts/${encodeURIComponent(accountId)}/check`, { method: "POST", body: {} })
      .then(() => loadAccounts()).catch(() => loadAccounts());
  }
  if (!before) return false;
  let data;
  try { data = await api("/api/admin/models"); } catch (e) { return false; }
  const fresh = (data.models || []).filter((m) => !before.has(m.id));
  if (fresh.length < MODEL_PICK_MIN) return false;
  openModelPicker(fresh);
  return true;
}

function openModelPicker(models) {
  const en = LANG === "en";
  const chosen = new Map(models.map((m) => [m.id, !!m.enabled]));
  // Round 34: OpenRouter adds ~460 models, most of them paid — one click keeps the «:free» ones.
  const isFree = (m) => /(:free|[-_]free)$/i.test(String(m.id || ""));
  const freeCount = models.filter(isFree).length;
  let filter = "";
  const shown = () => {
    const needle = filter.trim().toLowerCase();
    return needle ? models.filter((m) => `${m.id} ${proxyModelName(m)} ${m.note || ""}`.toLowerCase().includes(needle)) : models;
  };
  openModal(`<h3>${en ? "Which models should stay on?" : "Какие модели оставить включёнными?"}</h3>
    <p class="sub">${en
      ? `${models.length} models were added, all of them enabled. Turn off the ones you do not need — they are not deleted and can be enabled later on the Model catalog page in the left panel.`
      : `Добавлено моделей: ${models.length}, все включены. Выключите ненужные — они не удаляются, их можно включить позже на странице «Каталог моделей» в панели слева.`}</p>
    <div class="model-pick-bar">
      <input type="search" id="mp-filter" placeholder="${en ? "Search models…" : "Поиск моделей…"}" autocomplete="off">
      <button class="btn small" id="mp-all" type="button">${en ? "Enable all" : "Включить все"}</button>
      <button class="btn small" id="mp-none" type="button">${en ? "Disable all" : "Выключить все"}</button>
      ${freeCount ? `<button class="btn small" id="mp-free" type="button" title="${en ? "Keep only models whose id ends with :free" : "Оставить включёнными только модели с :free в названии"}">${en ? "Only free" : "Только бесплатные"} <span class="dim">${freeCount}</span></button>` : ""}
    </div>
    <div class="model-pick-list" id="mp-list" role="group"></div>
    <div class="row-end" style="align-items:center">
      <span class="dim grow" id="mp-count"></span>
      <button class="btn" id="mp-skip" type="button">${en ? "Keep all on" : "Оставить все"}</button>
      <button class="btn primary" id="mp-save" type="button">${en ? "Save" : "Сохранить"}</button>
    </div>`, true);
  const list = $("#mp-list");
  const count = () => {
    const on = [...chosen.values()].filter(Boolean).length;
    $("#mp-count").textContent = en ? `${on} of ${models.length} enabled` : `Включено ${on} из ${models.length}`;
  };
  const render = () => {
    const rows = shown();
    list.innerHTML = rows.length ? rows.map((m) => `<label class="model-pick-row">
        <input type="checkbox" data-mp="${esc(m.id)}" ${chosen.get(m.id) ? "checked" : ""}>
        <span class="pm-cell">${proxyModelCell(m)}</span>${modelNote(m) ? `<span class="dim">${esc(modelNote(m))}</span>` : ""}
      </label>`).join("") : `<div class="empty">${en ? "Nothing found" : "Ничего не найдено"}</div>`;
    count();
  };
  list.addEventListener("change", (event) => {
    const box = event.target.closest("[data-mp]");
    if (!box) return;
    chosen.set(box.dataset.mp, box.checked);
    count();
  });
  $("#mp-filter").addEventListener("input", (event) => { filter = event.target.value; render(); });
  // Кнопки действуют на то, что сейчас показано: «выключить все», найти нужное, «включить все» найденные.
  $("#mp-all").onclick = () => { shown().forEach((m) => chosen.set(m.id, true)); render(); };
  $("#mp-none").onclick = () => { shown().forEach((m) => chosen.set(m.id, false)); render(); };
  if ($("#mp-free")) $("#mp-free").onclick = () => { models.forEach((m) => chosen.set(m.id, isFree(m))); render(); };
  $("#mp-skip").onclick = () => { closeModal(); loadModels(); };
  $("#mp-save").onclick = async () => {
    const button = $("#mp-save");
    button.disabled = true;
    const off = models.filter((m) => m.enabled && !chosen.get(m.id)).map((m) => m.id);
    const on = models.filter((m) => !m.enabled && chosen.get(m.id)).map((m) => m.id);
    try {
      if (off.length) await api("/api/admin/models/bulk", { method: "POST", body: { action: "disable", ids: off } });
      if (on.length) await api("/api/admin/models/bulk", { method: "POST", body: { action: "enable", ids: on } });
      closeModal();
      toast(en ? `Enabled models: ${[...chosen.values()].filter(Boolean).length}` : `Включено моделей: ${[...chosen.values()].filter(Boolean).length}`, "ok");
      loadModels();
    } catch (e) { toast(e.message, "err"); button.disabled = false; }
  };
  render();
}

async function modelsBulk(action) {
  const ids = Array.from(modelsSelected);
  if (!ids.length) { toast(t("bulk.nothing"), "warn"); return; }
  const run = async () => {
    try {
      const r = await api("/api/admin/models/bulk", { method: "POST", body: { action, ids } });
      toast(`${t("bulk.done")}: ${r.count}`, "ok");
      modelsSelected.clear();
      loadModels();
    } catch (e) { toast(e.message, "err"); }
  };
  if (action === "delete") confirmDialog(`${t("bulk.delete")} (${ids.length})`, t("set.models.delwarn"), run, t("bulk.delete"));
  else await run();
}

async function refreshModels(providers, button) {
  const old = button ? button.textContent : "";
  if (button) { button.disabled = true; button.textContent = "…"; }
  try {
    const r = await api("/api/admin/models/refresh", { method: "POST", body: { providers: providers || [] } });
    const parts = [`+${r.added}`, `${t("set.models.scanned")}: ${r.scanned}`];
    if (r.pruned) parts.push(`−${r.pruned}`);
    if (r.hidden) parts.push(`${t("set.models.hidden")}: ${r.hidden}`);
    toast(parts.join(" · "), r.errors && r.errors.length ? "warn" : "ok", 6000);
    if (r.errors && r.errors.length) {
      openModal(`<h3>${t("set.models.refresh")}</h3><pre class="code-block selectable">${esc(r.errors.map(translateMessage).join("\n"))}</pre>
        <div class="row-end"><button class="btn" id="m-close">${t("modal.close")}</button></div>`, true);
      $("#m-close").onclick = closeModal;
    }
    loadModels();
  } catch (err) { toast(err.message, "err", 6000); }
  finally { if (button) { button.disabled = false; button.textContent = old; } }
}

$$("#models-bulk-bar [data-mbulk]").forEach((el) => el.onclick = () => modelsBulk(el.dataset.mbulk));
$("#model-add").onclick = async () => {
  const button = $("#model-add");
  const prefix = $("#model-new-prefix").value.trim(), model = $("#model-new-model").value.trim();
  if (!prefix || !model) return toast("prefix + model", "warn");
  button.disabled = true;
  try {
    await api("/api/admin/models", { method: "POST", body: { prefix, model } });
    $("#model-new-prefix").value = ""; $("#model-new-model").value = "";
    loadModels();
  } catch (e) { toast(e.message, "err"); }
  finally { button.disabled = false; }
};
let modelsFilterTimer = 0;
$("#models-filter").addEventListener("input", (e) => {
  modelsFilter = e.target.value;
  clearTimeout(modelsFilterTimer);
  modelsFilterTimer = setTimeout(renderModelsTable, 90);
});
$("#models-provider-filter").addEventListener("change", (e) => { modelsProviderFilter = e.target.value; renderModelsTable(); });
$("#models-state-filter").addEventListener("change", (e) => { modelsStateFilter = e.target.value; renderModelsTable(); });
$("#models-group").addEventListener("change", (e) => { modelsGroup = e.target.value; renderModelsTable(); });
/* Ctrl+A и Esc в каталоге моделей — как при выделении файлов в Windows.
   В полях ввода не мешаем: там Ctrl+A выделяет текст, Esc закрывает подсказки. */
document.addEventListener("keydown", (event) => {
  if (!$("#tab-settings")?.classList.contains("active")) return;
  if ($("#modal-back").classList.contains("visible")) return;
  if (document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
    event.preventDefault();
    visibleModels().forEach((m) => modelsSelected.add(m.id));
    syncModelsSelection();
  } else if (event.key === "Escape" && modelsSelected.size) {
    modelsSelected.clear();
    syncModelsSelection();
  }
});
/* Кнопка «Обновить с серверов» сначала спрашивает, чьи каталоги тянуть:
   опрос всех провайдеров сразу долгий, а нужен обычно один. */
async function askRefreshProviders() {
  try { await fetchProviders(); } catch (e) { /* метаданные необязательны */ }
  if (!accountsCache.length) { try { await loadAccounts(); } catch (e) { /* ignore */ } }
  const usable = [...new Set(accountsCache.filter((a) => a.enabled).map((a) => a.provider))].sort();
  if (!usable.length) { toast(t("set.models.norefresh"), "warn", 5000); return; }
  const preset = new Set(modelsProviderFilter ? [modelsProviderFilter] : usable);
  openModal(`
    <h3>${t("set.models.refresh.title")}</h3>
    <p class="muted">${t("set.models.refresh.hint")}</p>
    <div class="pick-list" id="rf-list">
      ${usable.map((id) => {
        const accounts = accountsCache.filter((a) => a.provider === id);
        const live = accounts.filter((a) => a.enabled).length;
        return `<label class="pick-row">
          <input type="checkbox" data-rf="${esc(id)}" ${preset.has(id) ? "checked" : ""}>
          <span class="p-icon">${esc(providerMeta(id).icon)}</span>
          <span class="grow"><b>${esc(providerMeta(id).title || id)}</b>
            <span class="dim">${t("home.acc")} ${live}/${accounts.length}</span></span>
        </label>`;
      }).join("")}
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn small" id="rf-all">${t("bulk.all")}</button>
      <button class="btn small" id="rf-none">${t("set.models.refresh.none")}</button>
    </div>
    <div class="row-end">
      <button class="btn" id="m-cancel">${t("acc.cancel")}</button>
      <button class="btn primary" id="rf-go">${t("set.models.refresh.go")}</button>
    </div>`, true);
  $("#m-cancel").onclick = closeModal;
  const boxes = () => $$("#rf-list [data-rf]");
  $("#rf-all").onclick = () => boxes().forEach((b) => { b.checked = true; });
  $("#rf-none").onclick = () => boxes().forEach((b) => { b.checked = false; });
  $("#rf-go").onclick = async () => {
    const chosen = boxes().filter((b) => b.checked).map((b) => b.dataset.rf);
    if (!chosen.length) { toast(t("set.models.pickprovider"), "warn", 4000); return; }
    closeModal();
    await refreshModels(chosen, $("#models-refresh"));
  };
}
$("#models-refresh").onclick = () => askRefreshProviders();
$("#models-export").onclick = () => downloadAuthed("/api/admin/models/export", "memuai-models.json");
$("#models-import").onclick = () => {
  const input = document.createElement("input");
  input.type = "file"; input.accept = "application/json,.json";
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const r = await api("/api/admin/models/import", { method: "POST", body: { models: payload.models || payload } });
      toast(r.skipped ? `+${r.added} · ${LANG === "en" ? "skipped, no account for the prefix" : "пропущено, нет аккаунта для префикса"}: ${r.skipped}` : `+${r.added}`, r.skipped ? "warn" : "ok", 6000);
      loadModels();
    } catch (e) { toast(e.message, "err", 6000); }
  };
  input.click();
};
function keywordList() {
  return $("#models-keywords").value.split(/[,\n]/).map((x) => x.trim().toLowerCase()).filter(Boolean);
}
$("#models-kw-disable").onclick = async (e) => {
  const words = keywordList();
  if (!words.length) { toast(t("set.models.kwempty"), "warn"); return; }
  const preview = modelsCache.filter((m) => words.some((w) => m.id.toLowerCase().includes(w)));
  confirmDialog(t("set.models.kwdisable"), `${t("set.models.kwmatch")}: ${preview.length}`, async () => {
    try {
      const r = await api("/api/admin/models/hide_keywords", { method: "POST", body: { keywords: words, mode: "disable" } });
      toast(`${t("bulk.done")}: ${r.count}`, "ok");
      loadModels();
    } catch (err) { toast(err.message, "err"); }
  }, t("set.models.kwdisable"));
};
$("#models-kw-save").onclick = async () => {
  const words = keywordList();
  try {
    await api("/api/admin/models/hide_keywords", { method: "POST", body: { keywords: words, mode: "disable", save: true } });
    toast(t("set.models.kwsaved"), "ok");
    loadModels();
  } catch (e) { toast(e.message, "err"); }
};
$("#models-gs-config").onclick = async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  try { const r = await api("/api/admin/models/genspark/refresh_config", { method: "POST" }); toast(t("models.gs.config.done").replace("{n}", r.total), "ok"); }
  catch (err) { toast(err.message, "err"); }
  finally { button.disabled = false; }
};
$("#models-gs-discover").onclick = async (e) => {
  const button = e.currentTarget;
  button.disabled = true;
  const old = button.textContent;
  button.textContent = "…";
  try {
    const r = await api("/api/admin/models/genspark/discover", { method: "POST" });
    toast(t("models.gs.discover.done").replace("{added}", r.added).replace("{found}", r.total_found), "ok", 6000);
    loadModels();
  } catch (err) { toast(err.message, "err", 6000); }
  finally { button.textContent = old; button.disabled = false; }
};

/* --------- ключи --------- */

async function loadKeys() {
  try {
    const data = await api("/api/admin/keys");
    const keys = data.keys;
    if (!keys.length) {
      $("#keys-table").innerHTML = `<div class="sg-keys-empty">${t("sg.keys.none")}</div>`;
      return;
    }
    $("#keys-table").innerHTML = `<table class="tbl sg-keys"><tbody>
      ${keys.map((k) => `
        <tr><td class="mono">${esc(maskSecret(k))}</td>
        <td class="actions">
          <button class="btn small" data-kcopy="${esc(k)}">${t("home.copy")}</button>
          <button class="btn small danger" data-kdel="${esc(k)}" aria-label="${t("acc.btn.del")} ${esc(maskSecret(k))}">✕</button></td></tr>`).join("")}
      </tbody></table>`;
    $$("#keys-table [data-kcopy]").forEach((el) => el.onclick = () => copyText(el.dataset.kcopy, t("home.copied")));
    $$("#keys-table [data-kdel]").forEach((el) => el.onclick = () => {
      confirmDialog(t("acc.btn.del") + "?", maskSecret(el.dataset.kdel), async () => {
        try { await api(`/api/admin/keys/${encodeURIComponent(el.dataset.kdel)}`, { method: "DELETE" }); loadKeys(); }
        catch (e) { toast(e.message, "err"); }
      });
    });
  } catch (e) { if (e.message !== "401") toast(e.message, "err"); }
}
$("#key-generate").onclick = async () => {
  const button = $("#key-generate");
  button.disabled = true;
  try {
    const r = await api("/api/admin/keys", { method: "POST", body: {} });
    let copied = false;
    try { copied = await writeClipboard(r.key); } catch (e) { /* покажем ключ ниже */ }
    if (copied) {
      toast(LANG === "en" ? "Key created and copied" : "Ключ создан и скопирован", "ok", 5000);
    } else {
      openModal(`<h3>${t("set.key.new")}</h3><p class="muted">${LANG === "en" ? "Copy this key now." : "Скопируйте ключ сейчас."}</p><div class="code-block">${esc(r.key)}</div><div class="row-end"><button class="btn" id="m-close">${t("modal.close")}</button></div>`);
      $("#m-close").onclick = closeModal;
    }
    loadKeys();
  } catch (e) { toast(e.message, "err"); }
  finally { button.disabled = false; }
};

/* ------------------------------ логи ------------------------------ */

let logsLoading = false;
let logsFacetsLoaded = false;

function logsQueryString() {
  const params = new URLSearchParams();
  params.set("limit", $("#logs-limit").value || "100");
  params.set("offset", String(logsState.offset));
  const query = $("#logs-search").value.trim();
  if (query) params.set("query", query);
  if ($("#logs-status").value) params.set("status", $("#logs-status").value);
  if ($("#logs-provider").value) params.set("provider", $("#logs-provider").value);
  if ($("#logs-model").value) params.set("model", $("#logs-model").value);
  const period = Number($("#logs-period").value || 0);
  if (period > 0) params.set("since", String(Math.floor(Date.now() / 1000 - period)));
  return params;
}

function stageTitle(step) {
  const key = "logs.step." + step;
  const translated = t(key);
  return translated === key ? step : translated;
}

function prettyPayload(payload) {
  const raw = String(payload ?? "");
  if (!raw) return "";
  try { return JSON.stringify(JSON.parse(raw), null, 2); }
  catch (e) { return raw; }
}

function logPromptText(prompt, includeNotices = true) {
  if (!prompt || !Array.isArray(prompt.messages)) return "";
  const blocks = prompt.messages.filter((message) => message && typeof message === "object").map((message) => {
    const lines = [`[${String(message.role || "unknown")}${message.name ? " (" + String(message.name) + ")" : ""}]`];
    const contentText = (content) => {
      if (typeof content === "string") return content;
      if (content == null) return "";
      if (typeof content.text === "string") return content.text;
      return JSON.stringify(content, null, 2);
    };
    const content = Array.isArray(message.content)
      ? message.content.map(contentText).filter(Boolean).join("\n")
      : contentText(message.content);
    if (content) lines.push(content);
    for (const field of ["tool_call_id", "tool_calls", "function_call"]) {
      if (message[field] != null) {
        lines.push(field + ": " + (typeof message[field] === "string" ? message[field] : JSON.stringify(message[field], null, 2)));
      }
    }
    return lines.join("\n");
  });
  if (Array.isArray(prompt.tools) && prompt.tools.length) {
    blocks.push(t("logs.prompt.tools") + ":\n" + JSON.stringify(prompt.tools, null, 2));
  }
  if (!blocks.length) return "";
  const notices = [];
  if (includeNotices && prompt.truncated) notices.push(t("logs.prompt.truncated"));
  if (includeNotices && prompt.omitted_media) notices.push(t("logs.prompt.omittedmedia"));
  return [...notices, ...blocks].join("\n\n");
}

function logPlainText(l) {
  const lines = [
    `[${fmtDateTime(l.ts)}] ${l.status || "pending"} ${l.provider || "-"} ${l.model || "-"} ${l.ms || 0}ms`,
    l.request_id ? `request_id: ${l.request_id}` : "",
    l.account ? `account: ${l.account}` : "",
    l.error ? `error${l.error_code ? " (" + l.error_code + ")" : ""}: ${LANG === "en" ? (l.error_en || translateMessage(l.error)) : l.error}` : "",
  ].filter(Boolean);
  (l.stages || []).forEach((st, i) => {
    lines.push(`${i + 1}. ${stageTitle(st.step)} @ ${fmtTime(st.ts)}`);
    if (st.payload) lines.push(prettyPayload(st.payload).split("\n").map((x) => "    " + x).join("\n"));
  });
  return lines.join("\n");
}

let logsCache = [];
const logsPromptCache = new Map();

function logRequestKey(log) { return log.request_id || `${log.ts}-${log.model}-${logsCache.indexOf(log)}`; }

function logPromptAvailable(prompt) {
  return !!prompt && prompt.available !== false && (prompt.available === true ||
    (Array.isArray(prompt.messages) && (prompt.messages.some((message) => message && typeof message === "object") ||
      (Array.isArray(prompt.tools) && prompt.tools.length > 0))));
}

function logPromptRow(key) { return logsCache.find((log) => logRequestKey(log) === key); }

function logPromptCanLoad(log) {
  return !!log && logPromptAvailable(log.prompt) && (!!log.request_id || Array.isArray(log.prompt.messages));
}

function renderLogPrompt(key) {
  const row = logPromptRow(key);
  const card = $$("#logs-list .log-card").find((node) => node.dataset.requestId === key);
  if (!row || !card) return;
  const show = card.querySelector("[data-prompt]");
  const copy = card.querySelector("[data-promptcopy]");
  const box = card.querySelector("[id^='prompt-']");
  const open = logsPromptsExpanded.has(key);
  const available = logPromptCanLoad(row);
  const state = available ? logsPromptCache.get(key) : null;
  const loading = !!state?.promise;
  const snapshot = available && (Array.isArray(row.prompt.messages) ? row.prompt : state?.snapshot);
  const unavailable = t(row.prompt && Array.isArray(row.prompt.messages) ? "logs.prompt.empty" : "logs.prompt.unavailable");
  show.disabled = !available && !open;
  copy.disabled = !available || loading;
  show.title = copy.title = available ? "" : unavailable;
  show.textContent = (open ? t("logs.hideprompt") : t("logs.showprompt")) + (loading ? " …" : "");
  copy.textContent = t("logs.copyprompt") + (loading ? " …" : "");
  show.setAttribute("aria-expanded", String(open));
  show.setAttribute("aria-busy", String(loading));
  copy.setAttribute("aria-busy", String(loading));
  box.style.display = open ? "block" : "none";
  if (!open) { box.replaceChildren(); return; }
  const text = snapshot ? logPromptText(snapshot) : "";
  const error = available ? state?.error : unavailable;
  const previous = box.querySelector("pre");
  if (text && previous?.textContent === text) return;
  const node = document.createElement(text ? "pre" : "div");
  node.className = text ? "code-block selectable" : (error ? "log-error selectable" : "dim");
  node.textContent = text || error || "…";
  box.replaceChildren(node);
}

async function getLogPrompt(key) {
  const row = logPromptRow(key);
  if (!logPromptCanLoad(row)) throw new Error(t("logs.prompt.unavailable"));
  if (Array.isArray(row.prompt.messages)) return row.prompt;
  let state = logsPromptCache.get(key);
  if (state?.snapshot) return state.snapshot;
  if (state?.promise) return state.promise;
  state = { error: "" };
  logsPromptCache.set(key, state);
  state.promise = (async () => {
    try {
      const data = await api("/api/admin/logs/prompt?" + new URLSearchParams({ request_id: row.request_id }));
      const current = logPromptRow(key);
      if (logsPromptCache.get(key) !== state || !logPromptCanLoad(current)) {
        throw new Error(t("logs.prompt.unavailable"));
      }
      if (!data?.prompt || !Array.isArray(data.prompt.messages) || !logPromptAvailable(data.prompt)) {
        throw new Error(t("logs.prompt.empty"));
      }
      state.snapshot = data.prompt;
      return state.snapshot;
    } catch (error) {
      if (logsPromptCache.get(key) === state) state.error = error.message || t("logs.prompt.unavailable");
      throw error;
    } finally {
      if (logsPromptCache.get(key) === state) delete state.promise;
      renderLogPrompt(key);
    }
  })();
  renderLogPrompt(key);
  return state.promise;
}

async function loadLogs(quiet = false) {
  if (logsLoading) return;
  logsLoading = true;
  try {
    const params = logsQueryString();
    if (!logsFacetsLoaded) params.set("facets", "true");
    const data = await api("/api/admin/logs?" + params.toString());
    if (data.facets) {
      logsFacetsLoaded = true;
      const keepProvider = $("#logs-provider").value, keepModel = $("#logs-model").value;
      $("#logs-provider").innerHTML = `<option value="">${t("logs.filter.all")}</option>` +
        (data.facets.providers || []).map((id) => `<option value="${esc(id)}" ${keepProvider === id ? "selected" : ""}>${esc(providerMeta(id).title || id)}</option>`).join("");
      $("#logs-model").innerHTML = `<option value="">${t("logs.filter.all")}</option>` +
        (data.facets.models || []).map((id) => `<option value="${esc(id)}" ${keepModel === id ? "selected" : ""}>${esc(id)}</option>`).join("");
    }
    logsState.total = data.total || 0;
    const promptScroll = new Map($$("#logs-list .log-card").map((card) =>
      [card.dataset.requestId, card.querySelector("[id^='prompt-'] pre")?.scrollTop || 0]));
    logsCache = data.logs || [];
    const promptKeys = new Set(logsCache.filter((log) => log.request_id && logPromptCanLoad(log)).map(logRequestKey));
    for (const key of logsPromptCache.keys()) if (!promptKeys.has(key)) logsPromptCache.delete(key);
    const shownFrom = logsState.total ? logsState.offset + 1 : 0;
    const shownTo = logsState.offset + logsCache.length;
    $("#logs-summary").textContent =
      `${t("logs.shown")} ${shownFrom}–${shownTo} ${t("logs.of")} ${logsState.total} · ${t("logs.stored")}: ${data.stored ?? logsState.total}`;
    $("#logs-page").textContent = String(Math.floor(logsState.offset / Math.max(1, Number($("#logs-limit").value || 100))) + 1);
    $("#logs-prev").disabled = logsState.offset <= 0;
    $("#logs-next").disabled = shownTo >= logsState.total;

    if (!logsCache.length) {
      $("#logs-list").innerHTML = `<div class="card"><div class="empty">${t("logs.empty")}</div></div>`;
      return;
    }
    $("#logs-list").innerHTML = logsCache.map((l, idx) => {
      const ok = l.status === 200;
      const pending = !l.status;
      const key = logRequestKey(l);
      const open = logsState.expandAll || logsExpanded.has(key);
      const promptOpen = logsPromptsExpanded.has(key);
      const promptAvailable = logPromptCanLoad(l);
      const promptHint = promptAvailable ? "" : t(l.prompt && Array.isArray(l.prompt.messages) ? "logs.prompt.empty" : "logs.prompt.unavailable");
      return `
      <div class="card log-card ${pending ? "log-pending" : (ok ? "log-ok" : "log-err")}" data-log="${idx}" data-request-id="${esc(key)}">
        <div class="row-line log-head">
          <span class="badge ${pending ? "unknown" : (ok ? "ok" : "invalid")}">${pending ? t("logs.inprogress") : (ok ? t("logs.done") : (esc(l.status) + " " + t("st.fail")))}</span>
          <span class="mono">${esc(l.model || "—")}</span>
          ${l.provider ? `<span class="chip">${esc(providerMeta(l.provider).title || l.provider)}</span>` : ""}
          ${l.account ? `<span class="chip dim" title="${t("st.account")}">${esc(l.account)}</span>` : ""}
          <span class="dim" title="${esc(fmtDateTime(l.ts))}">${fmtTime(l.ts)}</span>
          <span class="dim">${esc(l.ms)} ${t("st.ms")}</span>
          ${l.prompt_tokens || l.completion_tokens ? `<span class="dim mono">${esc(l.prompt_tokens)}/${esc(l.completion_tokens)} ${t("st.tokens")}</span>` : ""}
          ${l.error_code ? `<span class="chip warn">${esc(l.error_code)}</span>` : ""}
          <div class="grow"></div>
          ${!ok && !pending ? `<button class="btn small ask-companion" type="button" data-ask-log="${idx}">✦ ${t("comp.ask")}</button>` : ""}
          <button class="btn small" data-logcopy="${idx}" title="${t("logs.copyone")}" aria-label="${t("logs.copyone")}">⧉</button>
          <button class="btn small" type="button" data-prompt="${idx}" aria-controls="prompt-${idx}" aria-expanded="${promptOpen}" ${promptAvailable || promptOpen ? "" : `disabled title="${esc(promptHint)}"`}>${promptOpen ? t("logs.hideprompt") : t("logs.showprompt")}</button>
          <button class="btn small" type="button" data-promptcopy="${idx}" ${promptAvailable ? "" : `disabled title="${esc(promptHint)}"`}>${t("logs.copyprompt")}</button>
          <button class="btn small" data-stages="${idx}">${open ? t("logs.hidestages") : t("logs.showstages")}</button>
        </div>
        ${l.error ? `<div class="log-error selectable">${esc(LANG === "en" ? (l.error_en || translateMessage(l.error)) : l.error)}</div>` : ""}
        <div class="log-stages log-stage" id="prompt-${idx}" style="display:${promptOpen ? "block" : "none"}"></div>
        <div class="log-stages" id="stages-${idx}" style="display:${open ? "block" : "none"}">
          ${(l.stages || []).map((st, si) => `
            <div class="log-stage">
              <div class="log-stage-head">
                <span class="chip">${si + 1}</span>
                <b>${esc(stageTitle(st.step))}</b>
                <span class="dim">${fmtTime(st.ts)}</span>
                ${st.payload ? `<div class="grow"></div><button class="btn small" data-pcopy="${idx}-${si}" aria-label="${t("logs.copypayload")}">⧉</button>` : ""}
              </div>
              ${st.payload ? `<pre class="code-block selectable" id="payload-${idx}-${si}">${esc(prettyPayload(st.payload))}</pre>` : ""}
            </div>`).join("")}
        </div>
      </div>`;
    }).join("");

    $$("#logs-list [data-stages]").forEach((el) => el.onclick = () => {
      const box = document.getElementById("stages-" + el.dataset.stages);
      const card = el.closest(".log-card");
      const shown = box.style.display !== "none";
      box.style.display = shown ? "none" : "block";
      el.textContent = shown ? t("logs.showstages") : t("logs.hidestages");
      if (shown) logsExpanded.delete(card.dataset.requestId);
      else logsExpanded.add(card.dataset.requestId);
    });
    $$("#logs-list [data-logcopy]").forEach((el) => el.onclick = () =>
      copyText(logPlainText(logsCache[Number(el.dataset.logcopy)]), t("logs.copied")));
    $$("#logs-list [data-prompt]").forEach((el) => el.onclick = () => {
      if (el.disabled) return;
      const key = el.closest(".log-card").dataset.requestId;
      if (logsPromptsExpanded.has(key)) logsPromptsExpanded.delete(key);
      else logsPromptsExpanded.add(key);
      renderLogPrompt(key);
      if (logsPromptsExpanded.has(key) && logPromptCanLoad(logPromptRow(key))) getLogPrompt(key).catch(() => {});
    });
    $$("#logs-list [data-promptcopy]").forEach((el) => el.onclick = async () => {
      if (el.disabled) return;
      const key = el.closest(".log-card").dataset.requestId;
      try {
        const snapshot = await getLogPrompt(key);
        const current = logPromptRow(key);
        if (!logPromptCanLoad(current) || (Array.isArray(current.prompt.messages)
          ? current.prompt !== snapshot : logsPromptCache.get(key)?.snapshot !== snapshot)) {
          throw new Error(t("logs.prompt.unavailable"));
        }
        const text = logPromptText(snapshot, false);
        if (text) await copyText(text, t("logs.copied"));
      } catch (error) { toast(error.message || t("logs.prompt.unavailable"), "err"); }
    });
    $$("#logs-list [data-pcopy]").forEach((el) => el.onclick = () => {
      const node = document.getElementById("payload-" + el.dataset.pcopy);
      if (node) copyText(node.textContent, t("logs.copied"));
    });
    for (const row of logsCache) {
      const key = logRequestKey(row);
      renderLogPrompt(key);
      const pre = $$("#logs-list .log-card").find((card) => card.dataset.requestId === key)?.querySelector("[id^='prompt-'] pre");
      if (pre) pre.scrollTop = promptScroll.get(key) || 0;
      if (logsPromptsExpanded.has(key) && logPromptCanLoad(row) && !Array.isArray(row.prompt.messages) && !logsPromptCache.has(key)) {
        getLogPrompt(key).catch(() => {});
      }
    }
  } catch (e) { if (!quiet && e.message !== "401") toast(e.message, "err"); }
  finally { logsLoading = false; }
}

function syncLogsTimer() {
  clearInterval(logsTimer);
  logsTimer = null;
  clearInterval(applogTimer);
  applogTimer = null;
  if (document.hidden || !$("#tab-logs").classList.contains("active")) return;
  if (logsState.view === "requests") {
    if ($("#logs-auto").checked) logsTimer = setInterval(() => loadLogs(true), 2000);
  } else if ($("#applog-auto").checked) {
    applogTimer = setInterval(() => loadApplog(true), 2500);
  }
}

$$("#logs-view-switch [data-logview]").forEach((el) => el.onclick = () => {
  logsState.view = el.dataset.logview;
  $$("#logs-view-switch .seg").forEach((b) => b.classList.toggle("active", b === el));
  $("#logs-view-requests").hidden = logsState.view !== "requests";
  $("#logs-view-server").hidden = logsState.view !== "server";
  if (logsState.view === "server") loadApplog(false); else loadLogs(false);
  syncLogsTimer();
});

["#logs-search", "#logs-status", "#logs-provider", "#logs-model", "#logs-period", "#logs-limit"]
  .forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
      logsState.offset = 0;
      loadLogs(false);
    });
  });
$("#logs-auto").onchange = syncLogsTimer;
$("#applog-auto").onchange = syncLogsTimer;
$("#logs-wrap").onchange = (e) => $("#logs-list").classList.toggle("nowrap", !e.target.checked);
$("#logs-expand-all").onchange = (e) => { logsState.expandAll = e.target.checked; loadLogs(false); };
$("#logs-prev").onclick = () => {
  logsState.offset = Math.max(0, logsState.offset - Number($("#logs-limit").value || 100));
  loadLogs(false);
};
$("#logs-next").onclick = () => {
  logsState.offset += Number($("#logs-limit").value || 100);
  loadLogs(false);
};
document.addEventListener("visibilitychange", syncLogsTimer);
$("#logs-refresh").onclick = () => { logsFacetsLoaded = false; loadLogs(false); };
$("#logs-copy").onclick = () => copyText(logsCache.map(logPlainText).join("\n\n"), t("logs.copied"));
$("#logs-export-json").onclick = () => {
  const params = logsQueryString(); params.delete("limit"); params.delete("offset");
  downloadAuthed("/api/admin/logs/export?fmt=json&" + params.toString(), "memuai-logs.json");
};
$("#logs-export-txt").onclick = () => {
  const params = logsQueryString(); params.delete("limit"); params.delete("offset");
  downloadAuthed("/api/admin/logs/export?fmt=txt&" + params.toString(), "memuai-logs.txt");
};
$("#logs-clear").onclick = () => {
  confirmDialog(t("logs.clear") + "?", t("logs.clear.hint"), async () => {
    try { await api("/api/admin/logs/clear", { method: "POST" }); logsState.offset = 0; logsExpanded.clear(); logsPromptsExpanded.clear(); logsPromptCache.clear(); loadLogs(); }
    catch (e) { toast(e.message, "err"); }
  }, t("logs.clear"));
};

/* --------- журнал сервера --------- */

let applogCache = [];
async function loadApplog(quiet = false) {
  try {
    const params = new URLSearchParams({ limit: "500" });
    const query = $("#applog-search").value.trim();
    if (query) params.set("query", query);
    if ($("#applog-level").value) params.set("level", $("#applog-level").value);
    const data = await api("/api/admin/applog?" + params.toString());
    applogCache = data.records || [];
    if (!applogCache.length) {
      $("#applog-body").textContent = t("logs.empty");
      return;
    }
    const node = $("#applog-body");
    const atBottom = node.scrollTop + node.clientHeight >= node.scrollHeight - 30;
    node.innerHTML = applogCache.map((r) => `<span class="lvl-${esc(r.level)}">${fmtTime(r.ts)} ${esc(r.level.toUpperCase().padEnd(7))} ${esc(r.logger)}: ${esc(translateMessage(r.message))}</span>`).join("\n");
    if (atBottom) node.scrollTop = node.scrollHeight;
  } catch (e) { if (!quiet && e.message !== "401") toast(e.message, "err"); }
}
["#applog-search", "#applog-level"].forEach((sel) => {
  const el = $(sel);
  if (el) el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => loadApplog(false));
});
$("#applog-refresh").onclick = () => loadApplog(false);
$("#applog-copy").onclick = () => copyText($("#applog-body").textContent, t("logs.copied"));
$("#applog-clear").onclick = () => confirmDialog(t("logs.clear") + "?", t("logs.clear.server"), async () => {
  try { await api("/api/admin/applog/clear", { method: "POST" }); loadApplog(); }
  catch (e) { toast(e.message, "err"); }
}, t("logs.clear"));

/* ------------------------------ старт ------------------------------ */

initLang();
(async function init() {
  applyI18n();
  wireFieldLabels();
  showApp();
})();

/* Перетаскивание аккаунтов: у края прокручиваемой области она листается сама. */
(function dragAutoScroll() {
  let frame = 0, box = null, speed = 0;
  const scrollable = (node) => {
    for (let n = node; n; n = n.parentElement) {
      if (n.nodeType !== 1) continue;
      if (n === document.documentElement || n === document.body) return document.scrollingElement;
      const style = getComputedStyle(n);
      if (/(auto|scroll)/.test(style.overflowY) && n.scrollHeight > n.clientHeight + 2) return n;
    }
    return document.scrollingElement;
  };
  const tick = () => {
    if (!box || !speed) { frame = 0; return; }
    box.scrollTop += speed;
    frame = requestAnimationFrame(tick);
  };
  document.addEventListener("dragover", (event) => {
    const target = scrollable(event.target);
    if (!target) { speed = 0; return; }
    const whole = target === document.scrollingElement;
    const top = whole ? 0 : target.getBoundingClientRect().top;
    const bottom = whole ? window.innerHeight : target.getBoundingClientRect().bottom;
    const edge = Math.min(80, (bottom - top) / 4);
    speed = event.clientY < top + edge ? -Math.ceil((top + edge - event.clientY) / 3)
      : event.clientY > bottom - edge ? Math.ceil((event.clientY - bottom + edge) / 3) : 0;
    box = target;
    if (speed && !frame) frame = requestAnimationFrame(tick);
  }, true);
  const stop = () => { speed = 0; box = null; };
  document.addEventListener("dragend", stop, true);
  document.addEventListener("drop", stop, true);
})();
