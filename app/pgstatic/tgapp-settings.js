/* MemuAI · Settings → Remote access → Telegram Mini App (Round 20).
 *
 * Renders into #tgapp-settings. The container is created right after the
 * Telegram bot panel (#tg-settings) inside #pane-remote whenever that pane is
 * (re)rendered, so app.js needs no changes. The Mini App itself runs in
 * MemuAI Proxy (app/tg_webapp.py); this panel only edits memuai/tgapp.json
 * and shows memuai/tgapp-status.json through /api/pg/tgapp.
 */
(function () {
  "use strict";
  const PHONE = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="2.5" width="12" height="19" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M10.5 18.5h3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  const COPY = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';
  const state = { data: null, timer: 0, saving: false, error: "", adv: false };

  const en = () => {
    try { if (window.S && S.preferences && S.preferences.language) return S.preferences.language === "en"; } catch (e) { /* app.js not ready */ }
    return localStorage.getItem("memu_lang") === "en" || document.documentElement.lang === "en";
  };
  const tr = (ru, english) => (en() ? english : ru);
  // Round 27: the tunnel's status texts come in Russian with an English twin (tg_webapp.status_en)
  const statusText = (status, key) => (en() && status[key + "_en"]) || status[key] || "";
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STYLE = `
#tgapp-settings { margin-top: 14px; font-family: var(--sans, inherit); }
.tga-card { border: 1px solid var(--border); border-radius: 18px; padding: 16px; background: linear-gradient(135deg, var(--panel-2), var(--panel)); display: grid; gap: 2px; }
.tga-head { display: flex; align-items: center; gap: 14px; margin-bottom: 6px; }
.tga-mark { width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; flex: none; color: #fff;
  background: linear-gradient(135deg, var(--accent), var(--accent-2, var(--accent))); box-shadow: 0 6px 18px color-mix(in srgb, var(--accent) 28%, transparent); }
.tga-mark svg { width: 24px; height: 24px; }
.tga-title { font-size: 15.5px; font-weight: 700; letter-spacing: -.01em; }
.tga-sub { color: var(--muted); font-size: 12.5px; line-height: 1.45; margin-top: 2px; }
.tga-pill { margin-left: auto; flex: none; display: inline-flex; align-items: center; gap: 7px; padding: 6px 11px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--panel-2); color: var(--muted); font-size: 12px; font-weight: 600; max-width: 46%; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tga-pill i { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex: none; }
.tga-pill[data-tone="ok"] { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 40%, var(--border)); }
.tga-pill[data-tone="ok"] i { background: var(--ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ok) 22%, transparent); }
.tga-pill[data-tone="busy"] { color: var(--accent); } .tga-pill[data-tone="busy"] i { background: var(--accent); animation: tga-pulse 1.4s ease-in-out infinite; }
.tga-pill[data-tone="err"] { color: var(--danger); border-color: color-mix(in srgb, var(--danger) 40%, var(--border)); } .tga-pill[data-tone="err"] i { background: var(--danger); }
.tga-pill[data-tone="warn"] i { background: #d6a03a; }
body.reduce-motion .tga-pill i { animation: none !important; }
@keyframes tga-pulse { 50% { opacity: .35; } }
.tga-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 10px 0; border-top: 1px solid color-mix(in srgb, var(--border) 60%, transparent); }
.tga-row .tl { font-size: 13.5px; font-weight: 650; } .tga-row .td, .tga-note { color: var(--muted); font-size: 12px; line-height: 1.45; margin-top: 2px; }
.tga-row > div:first-child { min-width: 0; }
.tga-ctl { flex: none; display: flex; gap: 8px; align-items: center; }
.tga-ctl select.pinput, .tga-ctl input.pinput { margin: 0; }
.tga-seg { display: inline-flex; padding: 3px; gap: 3px; border-radius: 11px; background: var(--panel-2); border: 1px solid var(--border); }
.tga-seg button { border: 0; background: transparent; color: var(--muted); font-family: inherit; font-size: 12.5px; font-weight: 600; line-height: 1; padding: 8px 12px; border-radius: 8px; cursor: pointer; }
.tga-seg button[aria-pressed="true"] { background: var(--panel); color: var(--text); box-shadow: 0 1px 4px rgba(0,0,0,.18); }
.tga-url { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
.tga-url code { flex: 1; min-width: 0; font-family: var(--mono, monospace); font-size: 12.5px; padding: 8px 10px; border-radius: 10px; background: var(--panel-2);
  border: 1px solid var(--border); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; user-select: all; }
.tga-steps { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: 12.5px; line-height: 1.55; }
.tga-steps b { color: var(--text); font-weight: 650; }
.tga-progress { height: 4px; border-radius: 3px; background: var(--panel-2); overflow: hidden; margin-top: 8px; }
.tga-progress i { display: block; height: 100%; background: var(--accent); transition: width .3s; }
.tga-error { color: var(--danger); font-size: 12.5px; margin-top: 6px; }
.tga-custom { display: grid; gap: 8px; padding: 2px 0 10px; }
.tga-custom .pinput { margin: 0; }
.sx-shell #tgapp-settings .tga-custom { padding: 0 18px 12px; }
#tgapp-settings .tga-ctl .pretty-select { min-width: 132px; }
.tga-actions { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 10px; border-top: 1px solid color-mix(in srgb, var(--border) 60%, transparent); }
.tga-actions .pbtn, .tga-url .pbtn { display: inline-flex; align-items: center; gap: 7px; flex: none; }
`;

  function injectStyle() {
    if (document.getElementById("tgapp-settings-style")) return;
    const style = document.createElement("style");
    style.id = "tgapp-settings-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }

  async function call(method, path, body) {
    const response = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
    return data;
  }

  function statusView(data) {
    const status = (data && data.status) || {};
    const config = (data && data.config) || {};
    const problems = (data && data.problems) || [];
    if (!config.enabled) return { tone: "", text: tr("Выключен", "Off") };
    if (problems.includes("no_token")) return { tone: "warn", text: tr("Нет токена бота", "No bot token") };
    if (problems.includes("no_user")) return { tone: "warn", text: tr("Бот не привязан", "Bot not paired") };
    switch (status.state) {
      case "online": return status.verified === false && status.detail ? { tone: "warn", text: tr("Работает · не проверен", "Online · unverified") } : { tone: "ok", text: tr("Работает", "Online") };
      case "verifying": return { tone: "busy", text: tr("Проверяю адрес…", "Checking the address…") };
      case "dns_pending": return { tone: "busy", text: tr("Ждём DNS…", "Waiting for DNS…") };
      case "failed": return { tone: "err", text: tr("Не запускается", "Cannot start") };
      case "downloading": return { tone: "busy", text: tr("Скачиваю cloudflared…", "Downloading cloudflared…") };
      case "starting": case "connecting": return { tone: "busy", text: tr("Запускаю туннель…", "Starting the tunnel…") };
      case "proxy_offline": return { tone: "warn", text: tr("MemuAI Proxy не запущен", "MemuAI Proxy is not running") };
      case "error": return { tone: "warn", text: tr("Переподключение…", "Reconnecting…") };
      case "blocked": return { tone: "warn", text: tr("Ждёт настройки", "Needs setup") };
      default: return { tone: "busy", text: tr("Запускается…", "Starting…") };
    }
  }

  function render(root) {
    const data = state.data;
    if (!data) { root.innerHTML = `<div class="tga-card"><div class="tga-sub">${tr("Загрузка…", "Loading…")}</div></div>`; return; }
    const config = data.config || {};
    const status = data.status || {};
    const view = statusView(data);
    const download = status.download || {};
    const percent = download.total ? Math.round(100 * download.done / download.total) : 0;
    const url = status.url || "";
    root.innerHTML = `<section class="tga-card" aria-label="Telegram Mini App">
      <div class="tga-head"><span class="tga-mark">${PHONE}</span><div style="min-width:0">
        <div class="tga-title">${tr("Mini App — MemuAI в Telegram", "Mini App — MemuAI in Telegram")}</div>
        <div class="tga-sub">${tr("Полноценный MemuAI в телефоне: чаты, мемесы, модули, настройки и сессии MemuCode. Работает, пока включён компьютер.", "The full MemuAI on your phone: chats, agents, modules, settings and MemuCode sessions. Works while the computer is on.")}</div></div>
        <span class="tga-pill" data-tone="${view.tone}" role="status" aria-live="polite"><i></i><span>${esc(view.text)}</span></span></div>
      <div class="tga-row"><div><div class="tl">${tr("Включить Mini App", "Enable the Mini App")}</div><div class="td">${tr("MemuAI Proxy откроет защищённый адрес для Telegram. Войти смогут только привязанный аккаунт и пользователи из списка доступа бота.", "MemuAI Proxy opens a secure address for Telegram. Only the paired account and users on the bot's access list can sign in.")}</div></div>
        <div class="tga-ctl"><label class="switch"><input type="checkbox" id="tga-enabled" ${config.enabled ? "checked" : ""}><span class="sl"></span></label></div></div>
      <div class="tga-row"><div><div class="tl">${tr("Адрес", "Address")}</div><div class="td">${config.mode === "custom" ? tr("Ваш HTTPS-прокси или домен перенаправляет на локальный порт Mini App.", "Your HTTPS proxy or domain forwards to the Mini App's local port.") : config.mode === "named" ? tr("Именованный Cloudflare Tunnel на вашем домене: постоянный адрес, не зависит от быстрых туннелей.", "A named Cloudflare Tunnel on your domain: a permanent address, independent of quick tunnels.") : tr("Быстрый туннель Cloudflare: без регистрации, адрес меняется при каждом запуске — бот обновит кнопку сам.", "Cloudflare quick tunnel: no sign-up, the address changes on every start — the bot updates its button itself.")}</div></div>
        <div class="tga-ctl"><div class="tga-seg" role="group"><button type="button" data-mode="cloudflare" aria-pressed="${config.mode === "cloudflare" || !config.mode}">${tr("Быстрый", "Quick")}</button><button type="button" data-mode="named" aria-pressed="${config.mode === "named"}">${tr("Свой туннель", "Own tunnel")}</button><button type="button" data-mode="custom" aria-pressed="${config.mode === "custom"}">${tr("Свой адрес", "Own URL")}</button></div></div></div>
      ${config.mode === "named" ? `<div class="tga-custom"><input class="pinput" id="tga-named-url" placeholder="https://memu.example.com" value="${esc(config.named_url || "")}" spellcheck="false" autocomplete="off">
        <input class="pinput" id="tga-named-token" type="password" placeholder="${config.named_token_set ? tr("Токен сохранён — вставьте новый, чтобы заменить", "Token saved — paste a new one to replace") : tr("Токен туннеля (eyJ…)", "Tunnel token (eyJ…)")}" spellcheck="false" autocomplete="off">
        <div class="tga-note">${tr("Постоянный адрес на своём домене: панель Cloudflare → Networking → Tunnels → Create a tunnel → имя → Create Tunnel → скопируйте команду установки (в ней токен); затем вкладка Routes → Add route → Published application: свой поддомен и Service URL", "A permanent address on your domain: Cloudflare dashboard → Networking → Tunnels → Create a tunnel → name → Create Tunnel → copy the install command (it contains the token); then the Routes tab → Add route → Published application: your subdomain and the Service URL")} <code>http://127.0.0.1:${esc(status.port || config.port)}</code>.</div></div>` : ""}
      ${config.mode === "custom" ? `<div class="tga-custom"><div class="tga-note">${tr("ngrok, свой обратный прокси или VPS — любой HTTPS-адрес с действительным сертификатом.", "ngrok, your own reverse proxy or a VPS — any HTTPS address with a valid certificate.")}</div><input class="pinput" id="tga-custom-url" placeholder="https://memu.example.com" value="${esc(config.custom_url || "")}" spellcheck="false" autocomplete="off">
        <div class="tga-note">${tr("Перенаправьте этот адрес на", "Forward this address to")} <code>http://127.0.0.1:${esc(status.port || config.port)}</code>. ${tr("Telegram требует HTTPS с действительным сертификатом.", "Telegram requires HTTPS with a valid certificate.")}</div></div>` : ""}
      ${config.enabled ? `<div class="tga-row" style="display:block"><div class="tl">${tr("Состояние", "Status")}</div>
        ${status.state === "downloading" ? `<div class="td">${tr("Скачиваю cloudflared с GitHub (один раз, ~55 МБ)", "Downloading cloudflared from GitHub (once, ~55 MB)")} · ${percent}%</div><div class="tga-progress"><i style="width:${percent}%"></i></div>` : ""}
        ${url ? `<div class="tga-url"><code title="${esc(url)}">${esc(url)}</code><button type="button" class="pbtn ghost small" id="tga-copy">${COPY}${tr("Копировать", "Copy")}</button></div>
          <ol class="tga-steps"><li>${tr("Откройте своего бота в Telegram.", "Open your bot in Telegram.")}</li><li>${tr("Нажмите кнопку меню <b>MemuAI</b> слева от поля ввода (или «Открыть MemuAI» в сообщении бота).", "Tap the <b>MemuAI</b> menu button left of the input field (or “Open MemuAI” in a bot message).")}</li><li>${(config.web_login !== false ? tr("В обычном браузере адрес попросит подтвердить вход из Telegram (QR-код или /web).", "In a regular browser the address asks you to confirm the sign-in from Telegram (QR code or /web).") : tr("Не открывайте адрес в обычном браузере: вход возможен только из Telegram.", "Do not open the address in a regular browser: sign-in only works from Telegram."))}</li></ol>` :
          `<div class="td">${esc(problemText(data))}</div>`}
        ${(status.state === "error" || status.state === "failed") && status.error ? `<div class="tga-error">${esc(statusText(status, "error"))}</div>` : ""}
        ${status.detail && status.state !== "failed" ? `<div class="tga-note">${esc(statusText(status, "detail"))}</div>` : ""}
        ${status.state === "error" && status.retry_at ? `<div class="tga-note">${tr("Следующая попытка через", "Next attempt in")} ${Math.max(0, Math.round(status.retry_at - Date.now() / 1000))} ${tr("с", "s")}${status.protocol && status.protocol !== "auto" ? " · " + esc(status.protocol.toUpperCase()) : ""}</div>` : ""}
        ${["error", "failed", "dns_pending", "verifying", "starting", "connecting"].includes(status.state) && config.mode !== "custom" ? `<div class="tga-url" style="margin-top:8px"><button type="button" class="pbtn ghost small" id="tga-retry">${tr("Повторить сейчас (новый адрес)", "Retry now (new address)")}</button></div>` : ""}</div>` : ""}
      ${config.mode !== "custom" ? `<details class="tga-row" id="tga-adv" style="display:block" ${state.adv ? "open" : ""}><summary class="tl" style="cursor:pointer">${tr("Если не открывается (VPN, блокировки)", "If it does not open (VPN, blocking)")}</summary>
        <div class="td" style="margin:6px 0 8px">${tr("QUIC (UDP) часто блокируется — «Авто» сам переходит на HTTP/2 после первой неудачи. Если быстрый туннель не работает совсем, используйте «Свой туннель» с доменом или «Свой адрес» (ngrok, VPS).", "QUIC (UDP) is often blocked — “Auto” switches to HTTP/2 after the first failure. If quick tunnels do not work at all, use “Own tunnel” with a domain or “Own URL” (ngrok, VPS).")}</div>
        <div class="tga-ctl" style="flex-wrap:wrap"><label class="td">${tr("Протокол", "Protocol")} <select class="pinput compact" id="tga-protocol">${[["auto", tr("Авто", "Auto")], ["http2", "HTTP/2 (TCP)"], ["quic", "QUIC (UDP)"]].map(([v, l]) => `<option value="${v}" ${config.cf_protocol === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          <label class="td">IP <select class="pinput compact" id="tga-edge">${[["auto", tr("Авто", "Auto")], ["4", "IPv4"], ["6", "IPv6"]].map(([v, l]) => `<option value="${v}" ${String(config.cf_edge_ip) === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div></details>` : ""}
      <div class="tga-row"><div><div class="tl">${tr("Управление MemuCode", "MemuCode control")}</div><div class="td">${tr("Смотреть сессии MemuCode CLI с телефона, отправлять задачи и отвечать на запросы разрешений. Опасные действия требуют отдельного подтверждения.", "Watch MemuCode CLI sessions from the phone, send tasks and answer permission requests. Dangerous actions need a separate confirmation.")}</div></div>
        <div class="tga-ctl"><label class="switch"><input type="checkbox" id="tga-code" ${config.code_control !== false ? "checked" : ""}><span class="sl"></span></label></div></div>
      <div class="tga-row"><div><div class="tl">${tr("Вход из браузера", "Browser sign-in")}</div><div class="td">${tr("Открыть Mini App в обычном браузере (чужой ПК, планшет): вход по QR-коду, который подтверждается в Mini App на телефоне, или по одноразовой ссылке из бота (/web). Без подтверждения из вашего Telegram войти нельзя.", "Open the Mini App in a normal browser (another PC, a tablet): sign in with a QR code confirmed in the Mini App on your phone, or with a one-time link from the bot (/web). Nobody gets in without a confirmation from your Telegram.")}</div></div>
        <div class="tga-ctl"><label class="switch"><input type="checkbox" id="tga-web" ${config.web_login !== false ? "checked" : ""}><span class="sl"></span></label></div></div>
      <div class="tga-row"><div><div class="tl">${tr("Срок входа", "Sign-in lifetime")}</div><div class="td">${tr("Сколько действуют данные запуска Telegram (auth_date).", "How long Telegram launch data (auth_date) stays valid.")}</div></div>
        <div class="tga-ctl"><select class="pinput compact" id="tga-age">${[1, 6, 24, 72, 168].map((h) => `<option value="${h}" ${+config.max_age_hours === h ? "selected" : ""}>${h < 24 ? h + tr(" ч", " h") : (h / 24) + tr(" дн.", " d")}</option>`).join("")}</select></div></div>
      <div class="tga-actions"><button type="button" class="pbtn ghost small" id="tga-reset">${tr("Сбросить сессии", "Reset sessions")}</button>
        <span class="tga-note" style="align-self:center">${tr("Новый секрет — все открытые на телефонах окна потребуют повторного входа.", "A new secret — every Mini App window open on a phone has to sign in again.")}</span></div>
      ${state.error ? `<div class="tga-error">${esc(state.error)}</div>` : ""}
    </section>`;
    bind(root);
    // Round 26: the selects stayed native (no pretty-select like the bot panel above).
    if (typeof window.enhanceSelects === "function") window.enhanceSelects(root);
  }

  function problemText(data) {
    const status = data.status || {};
    const problems = data.problems || [];
    if (problems.includes("no_token")) return tr("Сначала укажите токен бота выше.", "Add the bot token above first.");
    if (problems.includes("no_user")) return tr("Сначала привяжите свой аккаунт Telegram к боту выше.", "Pair your Telegram account with the bot above first.");
    if (status.state === "proxy_offline") return tr("Mini App работает внутри MemuAI Proxy — запустите его.", "The Mini App runs inside MemuAI Proxy — start it.");
    if (status.error === "no_custom_url") return tr("Укажите свой HTTPS-адрес.", "Enter your HTTPS address.");
    if (status.error === "no_named_tunnel") return tr("Укажите адрес и токен своего туннеля.", "Enter your tunnel address and token.");
    if (status.state === "verifying" || status.state === "dns_pending") return (statusText(status, "detail") || tr("Проверяю, что адрес открывается из интернета…", "Checking that the address opens from the internet…")) + " " + tr("Адрес уйдёт в Telegram только после проверки.", "The address goes to Telegram only after the check.");
    if (status.state === "failed") return tr("Туннель не запустился несколько раз подряд.", "The tunnel failed to start several times in a row.");
    return tr("Адрес появится через несколько секунд…", "The address appears in a few seconds…");
  }

  async function save(patch, root) {
    if (state.saving) return;
    state.saving = true; state.error = "";
    try { state.data = await call("POST", "/api/pg/tgapp", patch); }
    catch (error) { state.error = error.message; }
    state.saving = false;
    render(root);
  }

  function bind(root) {
    const enabled = root.querySelector("#tga-enabled");
    if (enabled) enabled.onchange = () => save({ enabled: enabled.checked }, root);
    root.querySelectorAll("[data-mode]").forEach((button) => button.onclick = () => save({ mode: button.dataset.mode }, root));
    const custom = root.querySelector("#tga-custom-url");
    if (custom) custom.onchange = () => save({ custom_url: custom.value.trim() }, root);
    const namedUrl = root.querySelector("#tga-named-url");
    if (namedUrl) namedUrl.onchange = () => save({ named_url: namedUrl.value.trim() }, root);
    const namedToken = root.querySelector("#tga-named-token");
    if (namedToken) namedToken.onchange = () => { if (namedToken.value.trim()) save({ named_token: namedToken.value.trim() }, root); };
    const protocol = root.querySelector("#tga-protocol");
    if (protocol) protocol.onchange = () => save({ cf_protocol: protocol.value }, root);
    const edge = root.querySelector("#tga-edge");
    if (edge) edge.onchange = () => save({ cf_edge_ip: edge.value }, root);
    const adv = root.querySelector("#tga-adv");
    if (adv) adv.ontoggle = () => { state.adv = adv.open; };
    const retry = root.querySelector("#tga-retry");
    if (retry) retry.onclick = () => save({ retry: true }, root);
    const code = root.querySelector("#tga-code");
    if (code) code.onchange = () => save({ code_control: code.checked }, root);
    const web = root.querySelector("#tga-web");
    if (web) web.onchange = () => save({ web_login: web.checked }, root);
    const age = root.querySelector("#tga-age");
    if (age) age.onchange = () => save({ max_age_hours: +age.value }, root);
    const copy = root.querySelector("#tga-copy");
    if (copy) copy.onclick = async () => {
      const url = (state.data.status || {}).url || "";
      try { if (typeof window.copyText === "function") await window.copyText(url, true); else await navigator.clipboard.writeText(url); } catch (e) { /* clipboard denied */ }
    };
    const reset = root.querySelector("#tga-reset");
    if (reset) reset.onclick = async () => {
      if (!window.confirm(tr("Сбросить все сессии Mini App? Телефоны войдут заново при следующем открытии, а браузеры попросят подтвердить вход снова.", "Reset every Mini App session? Phones sign in again next time, and browsers ask to confirm the sign-in again."))) return;
      try { state.data = await call("POST", "/api/pg/tgapp/secret"); state.error = ""; } catch (error) { state.error = error.message; }
      render(root);
    };
  }

  /* What the panel shows, without the heartbeat that changes on every poll. */
  function signature() {
    const data = state.data || {};
    const status = Object.assign({}, data.status || {});
    delete status.heartbeat;
    return JSON.stringify([data.config, status, data.problems, data.cloudflared, state.error, en()]);
  }

  async function refresh(root) {
    try { state.data = await call("GET", "/api/pg/tgapp"); state.error = ""; }
    catch (error) { state.error = error.message; }
    const focused = document.activeElement;
    if (focused && root.contains(focused) && (focused.tagName === "SELECT" || (focused.tagName === "INPUT" && focused.type !== "checkbox"))) return;
    // Round 26: re-rendering every 3 s closed an open picker under the cursor; redraw only on a real change
    // (the retry countdown still ticks).
    if (root.querySelector(".pretty-select.open")) return;
    const sig = signature();
    const status = (state.data && state.data.status) || {};
    if (sig === state.sig && root.firstElementChild && !(status.state === "error" && status.retry_at)) return;
    state.sig = sig;
    render(root);
  }

  function mount() {
    const pane = document.getElementById("pane-remote");
    if (!pane) return;
    let root = document.getElementById("tgapp-settings");
    if (root && pane.contains(root)) return;
    injectStyle();
    root = document.createElement("div");
    root.id = "tgapp-settings";
    const bot = pane.querySelector("#tg-settings");
    if (bot && bot.parentNode) bot.parentNode.insertBefore(root, bot.nextSibling);
    else pane.insertBefore(root, pane.firstChild);
    render(root);
    refresh(root);
  }

  function tick() {
    const root = document.getElementById("tgapp-settings");
    if (!root || !root.isConnected) return;
    const pane = document.getElementById("pane-remote");
    if (pane && !pane.classList.contains("active")) return;
    if (document.hidden) return;
    refresh(root);
  }

  function start() {
    mount();
    new MutationObserver(() => mount()).observe(document.body, { childList: true, subtree: true });
    state.timer = setInterval(tick, 3000);
  }

  window.MemuTgAppSettings = { mount, refresh: () => { const root = document.getElementById("tgapp-settings"); if (root) refresh(root); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
