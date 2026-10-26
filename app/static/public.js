

"use strict";

window.MemuPublic = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = { view: null, filter: "", check: null, checking: false, fresh: null, tunnelBusy: false, sample: "curl", poll: 0 };
  const num = (value) => Number(value || 0).toLocaleString(LANG === "en" ? "en-US" : "ru-RU");
  const short = (value) => { const n = Number(value) || 0; return n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}K` : num(n); };
  const usd = (value) => `$${(Number(value) || 0).toFixed(Number(value) >= 1 ? 2 : 4)}`;
  const errorOf = (error) => (error && error.message ? error.message : String(error || ""));
  const hintOf = (key) => key.key_hint || "mpk-…";
  const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString(LANG === "en" ? "en-US" : "ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—");

  async function load() {
    const root = $("#public-root");
    if (!root) return;
    try {
      S.view = await api("/api/admin/public");
    } catch (error) {
      root.innerHTML = `<div class="card"><div class="warn-strip">${esc(errorOf(error))}</div></div>`;
      return;
    }
    render();
  }

  function take(view) {
    S.view = view;
    if (view && view.created && view.created.key) S.fresh = view.created;
  }

  async function save(changes, quiet) {
    try {
      take(await api("/api/admin/public", { method: "POST", body: changes }));
      if (!quiet) toast(L("Сохранено", "Saved"), "ok", 1500);
    } catch (error) { toast(errorOf(error), "err", 6000); }
    render();
  }

  function statusLine() {
    const { settings, status } = S.view;
    if (status.error) return `<span class="pub-status err"><i></i>${esc(L("Не запущен", "Not running"))}: ${esc(L(status.error, status.error_en || status.error))}</span>`;
    if (status.running) return `<span class="pub-status on"><i></i>${esc(L("Работает", "Running"))} · <span class="mono">${esc(status.listening.host)}:${status.listening.port}</span>${status.listening.tls ? " · HTTPS" : ""}</span>`;
    return `<span class="pub-status"><i></i>${esc(settings.enabled ? L("Запускается…", "Starting…") : L("Выключен", "Off"))}</span>`;
  }

  function linkKind(kind) {
    return { tunnel: "Cloudflare Tunnel", address: L("ваш адрес", "your address"), lan: L("в локальной сети", "in the local network"), local: L("с этого компьютера", "from this computer") }[kind] || kind;
  }

  function allLinks() {
    const tunnel = S.view.tunnel || {};
    const out = [];
    if (tunnel.api_base) out.push({ kind: "tunnel", url: tunnel.api_base });
    return out.concat(S.view.links || []);
  }

  function baseUrl() {
    const links = allLinks();
    return (links.find((l) => l.kind === "tunnel") || links.find((l) => l.kind === "address") || links.find((l) => l.kind === "lan") || links[0] || { url: "http://127.0.0.1:8320/v1" }).url;
  }

  function checkLine() {
    const c = S.check;
    if (S.checking) return `<div class="pub-check">${esc(L("Проверяю…", "Checking…"))}</div>`;
    if (!c) return "";
    const rows = [c.local ? `<span class="ok">✓ ${esc(L("Сервер отвечает на этом компьютере", "The server answers on this computer"))}</span>`
      : `<span class="err">✗ ${esc(L("Сервер не отвечает даже локально", "The server does not answer even locally"))}</span>`];
    if (c.outside_ip) rows.push(`${esc(L("Ваш внешний адрес", "Your outside address"))}: <span class="mono">${esc(c.outside_ip)}</span>`);
    if (c.outside === true) rows.push(`<span class="ok">✓ ${esc(L("Порт открыт наружу", "The port is reachable from outside"))}</span>`);
    else if (c.outside === false) rows.push(esc(L("Снаружи проверить не удалось: многие роутеры не пускают компьютер на его же внешний адрес. Надёжнее всего — Cloudflare Tunnel выше.",
      "Could not check from outside: many routers do not let a computer reach its own outside address. Cloudflare Tunnel above is the reliable way.")));
    return `<div class="pub-check">${rows.join("<br>")}</div>`;
  }

  function step(n, title, sub, body, done) {
    return `<section class="pub-step ${done ? "done" : ""}" id="pub-step-${n}"><div class="pub-step-head"><span class="pub-step-n">${done ? "✓" : n}</span>
      <div class="grow"><h3>${esc(title)}</h3>${sub ? `<p class="sub">${esc(sub)}</p>` : ""}</div></div><div class="pub-step-body">${body}</div></section>`;
  }

  const row = (label, desc, control, forId = "") => `<div class="sg-row"><div class="sg-text">${forId
    ? `<label class="sg-label" for="${forId}">${esc(label)}</label>` : `<div class="sg-label">${esc(label)}</div>`}${desc ? `<div class="sg-desc">${esc(desc)}</div>` : ""}</div>
    <div class="sg-ctl">${control}</div></div>`;
  const numField = (id, value) => `<span class="sg-num"><input type="number" id="${id}" min="0" value="${esc(value)}"><span class="sg-unit"></span></span>`;

  function tunnelCard() {
    const tn = S.view.tunnel || { state: "stopped", config: {} };
    const conf = tn.config || {};
    const on = S.view.status.running;
    const stateText = { running: L("Работает", "Running"), starting: L("Запускается…", "Starting…"), stopped: L("Остановлен", "Stopped"), error: L("Ошибка", "Error") }[tn.state] || tn.state;
    const cls = tn.state === "running" ? "on" : tn.state === "error" ? "err" : "";
    const binary = tn.binary
      ? `<span class="dim">cloudflared: <span class="mono">${esc(tn.binary)}</span></span>`
      : `<span class="dim">${esc(L("cloudflared не найден на этом компьютере.", "cloudflared is not installed on this computer."))}</span>
         <button class="btn small" id="pub-tn-download" ${tn.downloading || S.tunnelBusy || !tn.asset ? "disabled" : ""}>${esc(tn.downloading || S.tunnelBusy ? L("Скачиваю…", "Downloading…") : L("Скачать cloudflared", "Download cloudflared"))}</button>`;
    const running = tn.state === "running" || tn.state === "starting";
    return `<div class="pub-tunnel">
      <div class="row pub-tunnel-head"><b class="grow">Cloudflare Tunnel — ${esc(L("бесплатный внешний адрес с HTTPS", "a free outside address with HTTPS"))}</b>
        <span class="pub-status ${cls}"><i></i>${esc(stateText)}</span></div>
      <p class="sub">${esc(L("Не нужно открывать порты на роутере и иметь «белый» IP. Quick Tunnel даёт случайный адрес вида https://….trycloudflare.com — он временный: меняется при каждом запуске, без гарантий доступности (для личного использования и тестов). Постоянный адрес — именованный туннель с вашим токеном Cloudflare.",
        "No router ports and no public IP needed. Quick Tunnel gives a random https://….trycloudflare.com address — it is temporary: it changes on every start and has no uptime guarantee (for personal use and testing). For a permanent address use a named tunnel with your Cloudflare token."))}</p>
      ${tn.url ? `<div class="pub-link pub-link-tunnel"><span class="mono selectable">${esc(tn.api_base)}</span><small>${esc(tn.mode === "named" ? L("постоянный", "permanent") : L("временный адрес", "temporary address"))}</small><button class="btn small" data-pub-copy="${esc(tn.api_base)}">${esc(L("Копировать", "Copy"))}</button></div>` : ""}
      ${tn.error ? `<div class="warn-strip">${esc(L(tn.error, tn.error_en || tn.error))}</div>` : ""}
      <div class="row pub-tunnel-actions">
        <div class="sg-seg pub-seg" role="group"><button type="button" class="${conf.mode !== "named" ? "on" : ""}" data-pub-tnmode="quick">Quick Tunnel</button><button type="button" class="${conf.mode === "named" ? "on" : ""}" data-pub-tnmode="named">${esc(L("Именованный", "Named"))}</button></div>
        <span class="grow"></span>
        ${running ? `<button class="btn small" id="pub-tn-stop" ${S.tunnelBusy ? "disabled" : ""}>${esc(L("Остановить", "Stop"))}</button>`
          : `<button class="btn small primary" id="pub-tn-start" ${!on || !tn.binary || S.tunnelBusy ? "disabled" : ""} title="${esc(on ? "" : L("Сначала включите сервер (шаг 1)", "Turn the server on first (step 1)"))}">${esc(S.tunnelBusy ? L("Запускаю…", "Starting…") : L("Запустить туннель", "Start the tunnel"))}</button>`}
      </div>
      <div class="pub-tunnel-bin">${binary}</div>
      ${conf.mode === "named" ? `<div class="sg-card pub-named">
        ${row(L("Токен туннеля", "Tunnel token"), L("Панель Cloudflare → Networking → Tunnels → Create a tunnel → Create Tunnel: команда установки содержит токен (можно вставить её целиком). Хранится на этом компьютере и не показывается снова.", "Cloudflare dashboard → Networking → Tunnels → Create a tunnel → Create Tunnel: the install command contains the token (you can paste it whole). Kept on this computer and never shown again."),
          `<input type="password" id="pub-tn-token" placeholder="${esc(conf.has_token ? L("сохранён — введите новый, чтобы заменить", "saved — type a new one to replace") : "eyJh…")}" autocomplete="off" spellcheck="false">`, "pub-tn-token")}
        ${row(L("Адрес (hostname)", "Hostname"), L("Тот, что указан у туннеля в Public Hostname, сервис — http://127.0.0.1:порт.", "As set in the tunnel's Public Hostname; service — http://127.0.0.1:port."),
          `<input type="text" id="pub-tn-host" value="${esc(conf.hostname || "")}" placeholder="api.example.com" spellcheck="false">`, "pub-tn-host")}
      </div>` : ""}
      <label class="pick-row pub-autostart"><input type="checkbox" id="pub-tn-auto" ${conf.autostart ? "checked" : ""}><span>${esc(L("Запускать туннель вместе с сервером", "Start the tunnel together with the server"))}</span></label>
      ${tn.log && tn.log.length ? `<details class="sg-more"><summary><span class="sg-more-t">${esc(L("Журнал cloudflared", "cloudflared log"))}</span></summary><pre class="code-block pub-log">${esc(tn.log.join("\n"))}</pre></details>` : ""}
    </div>`;
  }

  function keyUsage(key, today) {
    const usage = key.usage || {};
    const fresh = usage.day === today;
    const month = (today || "").slice(0, 7);
    const dayRequests = fresh ? usage.day_requests : 0, dayTokens = fresh ? usage.day_tokens : 0;
    const monthTokens = usage.month === month ? usage.month_tokens : 0;
    const meter = (used, limit) => (limit ? `<div class="pub-meter ${used >= limit ? "full" : ""}"><i style="width:${Math.min(100, Math.round((used / limit) * 100))}%"></i></div>` : "");
    return `<div class="pub-usage">${esc(L("сегодня", "today"))}: <b>${short(dayRequests)}</b>${key.rpd ? ` / ${short(key.rpd)}` : ""} ${esc(L("запр.", "req."))} · <b>${short(dayTokens)}</b>${key.tpd ? ` / ${short(key.tpd)}` : ""} ${esc(L("ток.", "tok."))}
      ${meter(dayRequests, key.rpd) || meter(dayTokens, key.tpd)}
      ${esc(L("месяц", "month"))}: <b>${short(monthTokens)}</b>${key.tpmonth ? ` / ${short(key.tpmonth)}` : ""} ${esc(L("ток.", "tok."))}${meter(monthTokens, key.tpmonth)}<br>
      ${esc(L("всего", "total"))}: <b>${short(usage.requests)}</b> ${esc(L("запр.", "req."))} · <b>${short(usage.tokens)}</b>${key.budget ? ` / ${short(key.budget)}` : ""} ${esc(L("ток.", "tok."))}${meter(usage.tokens, key.budget)}
      ${usage.cost || key.cost_budget ? ` · <b>${usd(usage.cost)}</b>${key.cost_budget ? ` / ${usd(key.cost_budget)}` : ""}${meter(usage.cost, key.cost_budget)}` : ""}
      ${usage.refused ? ` · ${esc(L("отказов", "refused"))}: ${short(usage.refused)}` : ""}
      <div class="dim">${esc(L("активность", "last used"))}: ${esc(when(usage.last_used))}${usage.last_model ? ` · <span class="mono">${esc(usage.last_model)}</span>` : ""}${usage.last_ip ? ` · ${esc(usage.last_ip)}` : ""}</div></div>`;
  }

  function limitsText(key) {
    const parts = [];
    if (key.rpm) parts.push(`${num(key.rpm)} ${L("в мин", "/min")}`);
    if (key.rpd) parts.push(`${num(key.rpd)} ${L("в день", "/day")}`);
    if (key.tpd) parts.push(`${short(key.tpd)} ${L("ток./день", "tok./day")}`);
    if (key.tpmonth) parts.push(`${short(key.tpmonth)} ${L("ток./мес", "tok./month")}`);
    if (key.budget) parts.push(`${L("всего", "total")} ${short(key.budget)} ${L("ток.", "tok.")}`);
    if (key.cost_budget) parts.push(`${L("бюджет", "budget")} ${usd(key.cost_budget)}`);
    if (key.max_tokens) parts.push(`≤ ${short(key.max_tokens)} ${L("ток./ответ", "tok./answer")}`);
    if (key.concurrency) parts.push(`${key.concurrency} ${L("одновременно", "at once")}`);
    if (key.ips && key.ips.length) parts.push(`IP: ${key.ips.length}`);
    if (key.expires) parts.push(`${L("до", "until")} ${key.expires}`);
    parts.push(key.models.length ? `${L("моделей", "models")}: ${key.models.length}` : L("все модели", "all models"));
    return parts.join(" · ");
  }

  function samples(base, key) {
    const k = key || "mpk-…";
    const model = (S.view.settings.models[0] || "model-id");
    const root = base.replace(/\/v1$/, "");
    return {
      curl: `curl ${base}/chat/completions \\\n  -H "Authorization: Bearer ${k}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "${model}", "messages": [{"role": "user", "content": "Привет!"}]}'`,
      python: `from openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key="${k}")\nanswer = client.chat.completions.create(\n    model="${model}",\n    messages=[{"role": "user", "content": "Привет!"}],\n)\nprint(answer.choices[0].message.content)`,
      node: `import OpenAI from "openai";\n\nconst client = new OpenAI({ baseURL: "${base}", apiKey: "${k}" });\nconst answer = await client.chat.completions.create({\n  model: "${model}",\n  messages: [{ role: "user", content: "Привет!" }],\n});\nconsole.log(answer.choices[0].message.content);`,
      anthropic: `from anthropic import Anthropic\n\nclient = Anthropic(base_url="${root}", api_key="${k}")\nmsg = client.messages.create(model="${model}", max_tokens=512,\n    messages=[{"role": "user", "content": "Привет!"}])\nprint(msg.content[0].text)`,
      models: `curl ${base}/models -H "Authorization: Bearer ${k}"`,
    };
  }

  function render() {
    const root = $("#public-root");
    if (!root || !S.view) return;
    const { settings, keys, catalogue, today, status } = S.view;
    const chosen = new Set(settings.models);
    const needle = S.filter.trim().toLowerCase();

    const modelName = (id) => (typeof proxyModelName === "function" ? proxyModelName({ id }) : id);
    const shown = catalogue.filter((id) => !needle || id.toLowerCase().includes(needle) || modelName(id).toLowerCase().includes(needle));
    const hosts = [["0.0.0.0", L("Все сети этого компьютера (0.0.0.0)", "Every network of this computer (0.0.0.0)")],
      ["127.0.0.1", L("Только этот компьютер (127.0.0.1) — для туннеля или обратного прокси", "This computer only (127.0.0.1) — for a tunnel or a reverse proxy")]];
    if (!hosts.some(([value]) => value === settings.host)) hosts.push([settings.host, settings.host]);
    const links = allLinks();
    const base = baseUrl();
    const sampleKey = S.fresh && S.fresh.key;
    const code = samples(base, sampleKey);
    const tabs = [["curl", "curl"], ["python", "Python (OpenAI)"], ["node", "Node.js (OpenAI)"], ["anthropic", "Anthropic SDK"], ["models", L("Список моделей", "Model list")]];

    const step1 = `<div class="sg-card">
        <div class="sg-row pub-main"><div class="sg-text"><label class="sg-label" for="pub-enabled">${esc(L("Публичный сервер", "Public server"))}</label>
          <div class="sg-desc">${esc(L("Отдельный сервер рядом с обычным: снаружи доступны только /v1/models, /v1/chat/completions, /v1/responses и /v1/messages — ни панели, ни управления, ни ваших аккаунтов. Вход только по ключам из шага 3.",
            "A separate server next to the regular one: from outside it serves only /v1/models, /v1/chat/completions, /v1/responses and /v1/messages — no panel, no management, none of your accounts. Access only with the keys from step 3."))}</div></div>
          <div class="sg-ctl">${statusLine()}<label class="switch" title="${esc(L("Включить", "Enable"))}"><input type="checkbox" role="switch" id="pub-enabled" ${settings.enabled ? "checked" : ""}><span class="slider"></span></label></div></div>
        ${row(L("Где слушать", "Listen on"), L("Для Cloudflare Tunnel достаточно «Только этот компьютер».", "For Cloudflare Tunnel “This computer only” is enough."), `<select id="pub-host">${hosts.map(([value, label]) => `<option value="${esc(value)}" ${value === settings.host ? "selected" : ""}>${esc(label)}</option>`).join("")}</select>`, "pub-host")}
        ${row(L("Порт", "Port"), "", `<span class="sg-num"><input type="number" id="pub-port" min="1" max="65535" value="${settings.port}"><span class="sg-unit"></span></span>`, "pub-port")}
      </div>
      ${settings.enabled && !settings.models.length ? `<div class="warn-strip">${esc(L("Выберите модели в шаге 3 — без них сервер не включится.", "Pick models in step 3 — the server does not start without them."))}</div>` : ""}`;

    const step2 = `${tunnelCard()}
      <div class="pub-links">${links.map((link) => `<div class="pub-link"><span class="mono selectable">${esc(link.url)}</span><small>${esc(linkKind(link.kind))}</small>
        <button class="btn small" data-pub-copy="${esc(link.url)}">${esc(L("Копировать", "Copy"))}</button></div>`).join("")}</div>
      <div class="row" style="margin-top:10px"><button class="btn small" id="pub-check" ${settings.enabled ? "" : "disabled"}>${esc(L("Проверить доступность", "Check reachability"))}</button></div>
      ${checkLine()}
      <details class="sg-more" id="pub-howto" ${S.howtoOpen ? "open" : ""}><summary><span class="sg-more-t">${esc(L("Свой домен, проброс порта, HTTPS", "Own domain, port forwarding, HTTPS"))}</span><span class="sg-more-hint">${esc(L("для опытных", "advanced"))}</span></summary>
        <div class="sg-card">
          ${row(L("Домен или внешний IP", "Domain or outside IP"), L("Только для ссылки, которую вы раздаёте.", "Only for the link you hand out."),
            `<input type="text" id="pub-address" value="${esc(settings.address)}" placeholder="api.example.com" spellcheck="false">`, "pub-address")}
          ${row(L("Файл сертификата (.pem / .crt)", "Certificate file (.pem / .crt)"), "", `<input type="text" id="pub-cert" value="${esc(settings.tls_cert)}" placeholder="C:\\certs\\fullchain.pem" spellcheck="false">`, "pub-cert")}
          ${row(L("Файл ключа сертификата", "Certificate key file"), "", `<input type="text" id="pub-tlskey" value="${esc(settings.tls_key)}" placeholder="C:\\certs\\privkey.pem" spellcheck="false">`, "pub-tlskey")}
          ${row(L("Запросы из браузера (CORS)", "Browser requests (CORS)"), L("Разрешить запросы из браузера с любых сайтов.", "Allow browser requests from any site."),
            `<label class="switch"><input type="checkbox" role="switch" id="pub-cors" ${settings.cors ? "checked" : ""}><span class="slider"></span></label>`, "pub-cors")}
        </div>
        <div class="pub-note">${esc(L("Проброс порта: разрешите Proxy в брандмауэре Windows и пробросьте порт на роутере. Без HTTPS ключи идут по сети открыто. При «сером» адресе (CGNAT) проброс не поможет — используйте туннель.",
          "Port forwarding: allow the Proxy in Windows Firewall and forward the port on your router. Without HTTPS keys travel in the clear. Behind CGNAT forwarding does not work — use a tunnel."))}</div>
      </details>`;

    const keyRows = keys.map((key) => `<tr class="${key.enabled ? "" : "row-off"}">
        <td><b>${esc(key.name || L("Без имени", "Unnamed"))}</b>${key.note ? `<div class="dim">${esc(key.note)}</div>` : ""}<div class="mono dim">${esc(hintOf(key))}</div></td>
        <td class="dim pub-limits-cell">${esc(limitsText(key))}${key.expires && key.expires < today ? ` <span class="badge invalid">${esc(L("истёк", "expired"))}</span>` : ""}</td>
        <td>${keyUsage(key, today)}</td>
        <td class="actions"><label class="switch" title="${esc(L("Включён", "Enabled"))}"><input type="checkbox" data-pub-toggle="${esc(key.id)}" ${key.enabled ? "checked" : ""} aria-label="${esc(L("Включён", "Enabled"))}"><span class="slider"></span></label>
          <button class="btn small" data-pub-edit="${esc(key.id)}">${esc(L("Изменить", "Edit"))}</button>
          <button class="btn small" data-pub-rotate="${esc(key.id)}" title="${esc(L("Выпустить новый ключ вместо этого", "Issue a new key instead of this one"))}">${esc(L("Ротация", "Rotate"))}</button>
          <button class="btn small danger" data-pub-delete="${esc(key.id)}" aria-label="${esc(L("Отозвать", "Revoke"))}" title="${esc(L("Отозвать", "Revoke"))}">✕</button></td>
      </tr>`).join("");

    const step3 = `<div class="pub-sub"><div class="row"><h4 class="grow">${esc(L("Модели, видимые снаружи", "Models offered outside"))}</h4><span class="dim">${chosen.size} ${esc(L("из", "of"))} ${catalogue.length}</span></div>
        <div class="model-pick-bar"><input type="search" id="pub-filter" value="${esc(S.filter)}" placeholder="${esc(L("Поиск моделей…", "Search models…"))}" autocomplete="off">
          <button class="btn small" id="pub-all" type="button">${esc(L("Отметить показанные", "Tick shown"))}</button>
          <button class="btn small" id="pub-none" type="button">${esc(L("Снять показанные", "Untick shown"))}</button></div>
        <div class="model-pick-list pub-models" id="pub-models">${shown.length ? shown.map((id) => `<label class="model-pick-row"><input type="checkbox" data-pub-model="${esc(id)}" ${chosen.has(id) ? "checked" : ""}><span class="pm-cell" title="${esc(id)}">${typeof proxyModelCell === "function" ? proxyModelCell({ id }) : `<span class="pm-name mono">${esc(id)}</span>`}</span></label>`).join("")
          : `<div class="empty">${esc(catalogue.length ? L("Ничего не найдено", "Nothing found") : L("Сначала добавьте аккаунт и включите модели", "Add an account and enable models first"))}</div>`}</div></div>
      <div class="pub-sub"><div class="row"><h4 class="grow">${esc(L("Ключи доступа", "Access keys"))}</h4><button class="btn small primary" id="pub-key-new">＋ ${esc(L("Новый ключ", "New key"))}</button></div>
        <p class="sub">${esc(L("У каждого человека или приложения — свой ключ: модели, лимиты, срок, IP. Ключ виден только при создании — хранится лишь его хэш; потерянный ключ заменяет «Ротация».",
          "Every person or app gets a key of their own: models, limits, expiry, IPs. A key is shown only when created — only its hash is kept; a lost key is replaced with “Rotate”."))}</p>
        ${keys.length ? `<div class="pub-keys-wrap"><table class="tbl pub-keys"><thead><tr><th>${esc(L("Ключ", "Key"))}</th><th>${esc(L("Ограничения", "Limits"))}</th><th>${esc(L("Использование", "Usage"))}</th><th></th></tr></thead><tbody>${keyRows}</tbody></table></div>`
          : `<div class="empty">${esc(L("Ключей пока нет — без ключа сервер никого не пустит.", "No keys yet — without a key the server lets nobody in."))}</div>`}</div>
      <details class="sg-more" id="pub-limits" ${S.limitsOpen ? "open" : ""}><summary><span class="sg-more-t">${esc(L("Общие лимиты сервера", "Server-wide limits"))}</span><span class="sg-more-hint">${esc(L("на всех вместе, поверх лимитов ключей · 0 — без ограничения", "for everyone, on top of each key · 0 — no limit"))}</span></summary>
        <div class="sg-card">
          ${row(L("Запросов в минуту", "Requests per minute"), "", numField("pub-rpm", settings.rpm), "pub-rpm")}
          ${row(L("Одновременных запросов", "Requests at once"), "", numField("pub-concurrency", settings.concurrency), "pub-concurrency")}
          ${row(L("Потолок ответа, токенов", "Answer cap, tokens"), L("Запрос с max_tokens больше этого отклоняется; без max_tokens — подставляется.", "A larger max_tokens is refused; a missing one is set to this."), numField("pub-maxout", settings.max_output_tokens), "pub-maxout")}
        </div></details>`;

    const step4 = `${sampleKey ? `<div class="pub-fresh"><b>${esc(L("Новый ключ — скопируйте сейчас, больше он не покажется:", "New key — copy it now, it will not be shown again:"))}</b>
        <div class="pub-link"><span class="mono selectable">${esc(sampleKey)}</span><button class="btn small primary" data-pub-copy="${esc(sampleKey)}">${esc(L("Копировать", "Copy"))}</button><button class="btn small" id="pub-fresh-hide">${esc(L("Скрыть", "Hide"))}</button></div></div>` : ""}
      <div class="pub-conn"><div><small>Base URL</small><div class="pub-link"><span class="mono selectable">${esc(base)}</span><button class="btn small" data-pub-copy="${esc(base)}">${esc(L("Копировать", "Copy"))}</button></div></div>
        <div><small>API key</small><div class="pub-link"><span class="mono">${esc(sampleKey ? sampleKey.slice(0, 10) + "…" : L("ключ из шага 3", "a key from step 3"))}</span></div></div></div>
      <div class="sg-seg pub-seg pub-tabs" role="tablist">${tabs.map(([id, label]) => `<button type="button" role="tab" class="${S.sample === id ? "on" : ""}" data-pub-sample="${id}">${esc(label)}</button>`).join("")}</div>
      <div class="pub-code"><pre class="code-block selectable" id="pub-code">${esc(code[S.sample] || code.curl)}</pre><button class="btn small pub-code-copy" id="pub-code-copy">${esc(L("Копировать", "Copy"))}</button></div>
      <p class="sub">${esc(L("Подойдёт любой клиент с OpenAI-совместимым API (Cursor, Continue, Open WebUI, LibreChat…): укажите Base URL и ключ. Ошибки — в формате OpenAI: 401 — неверный ключ, 403 — ключ выключен, истёк, IP или модель не разрешены, 429 — лимит (заголовок Retry-After).",
        "Any OpenAI-compatible client works (Cursor, Continue, Open WebUI, LibreChat…): set the Base URL and the key. Errors use the OpenAI format: 401 — wrong key, 403 — key disabled, expired, IP or model not allowed, 429 — a limit (Retry-After header)."))}</p>`;

    root.innerHTML = `<div class="pub-steps">
      ${step(1, L("Включите сервер", "Turn the server on"), "", step1, status.running)}
      ${step(2, L("Адрес для подключения", "The address to connect to"), L("Из интернета — проще всего через бесплатный Cloudflare Tunnel.", "From the internet — easiest with the free Cloudflare Tunnel."), step2, !!(S.view.tunnel && S.view.tunnel.url) || !!settings.address)}
      ${step(3, L("Модели и ключи", "Models and keys"), "", step3, settings.models.length > 0 && keys.some((k) => k.enabled))}
      ${step(4, L("Подключение", "Connect"), L("Готовые примеры с вашим адресом.", "Ready-made examples with your address."), step4, false)}
    </div>`;
    bind();
    schedulePoll();
  }

  function schedulePoll() {
    clearTimeout(S.poll);
    const tn = (S.view && S.view.tunnel) || {};
    if (!$("#tab-public")?.classList.contains("active")) return;
    if (tn.state === "starting" || tn.downloading) S.poll = setTimeout(async () => {
      try { S.view = await api("/api/admin/public"); render(); } catch (e) {  }
    }, 2000);
  }

  function setModels(next) { return save({ models: [...next] }, true); }

  async function tunnel(body, busy) {
    if (busy) { S.tunnelBusy = true; render(); }
    try { take(await api("/api/admin/public/tunnel", { method: "POST", body })); }
    catch (error) { toast(errorOf(error), "err", 7000); }
    S.tunnelBusy = false;
    render();
  }

  function bind() {
    const { settings } = S.view;
    $("#pub-enabled").onchange = (event) => save({ enabled: event.target.checked });
    $("#pub-host").onchange = (event) => save({ host: event.target.value });
    const field = (id, name, convert = (value) => value.trim()) => { const el = $(id); if (el) el.onchange = (event) => save({ [name]: convert(event.target.value) }); };
    const port = $("#pub-port");
    port.onchange = () => {
      const value = Number(port.value);

      if (!Number.isInteger(value) || value < 1 || value > 65535) { port.value = settings.port; toast(L("Порт — число от 1 до 65535", "The port is a number from 1 to 65535"), "err"); return; }
      save({ port: value });
    };
    field("#pub-address", "address");
    field("#pub-cert", "tls_cert");
    field("#pub-tlskey", "tls_key");
    field("#pub-rpm", "rpm", Number);
    field("#pub-concurrency", "concurrency", Number);
    field("#pub-maxout", "max_output_tokens", Number);
    $("#pub-cors").onchange = (event) => save({ cors: event.target.checked });
    $("#pub-howto").ontoggle = (event) => { S.howtoOpen = event.target.open; };
    $("#pub-limits").ontoggle = (event) => { S.limitsOpen = event.target.open; };
    $("#pub-filter").oninput = (event) => {
      S.filter = event.target.value;
      const caret = event.target.selectionStart;
      render();
      const input = $("#pub-filter");
      input.focus();
      input.setSelectionRange(caret, caret);
    };
    const shownIds = () => $$("#pub-models [data-pub-model]").map((box) => box.dataset.pubModel);
    $("#pub-all").onclick = () => setModels(new Set([...settings.models, ...shownIds()]));
    $("#pub-none").onclick = () => { const drop = new Set(shownIds()); setModels(settings.models.filter((id) => !drop.has(id))); };
    $$("#pub-models [data-pub-model]").forEach((box) => box.onchange = () => {
      const next = new Set(settings.models);
      if (box.checked) next.add(box.dataset.pubModel); else next.delete(box.dataset.pubModel);
      setModels(next);
    });
    $$("[data-pub-copy]").forEach((button) => button.onclick = () => copyText(button.dataset.pubCopy, L("Скопировано", "Copied")));
    $("#pub-check").onclick = async () => {
      S.checking = true; render();
      try { S.check = await api("/api/admin/public/check", { method: "POST", body: {} }); } catch (error) { toast(errorOf(error), "err"); }
      S.checking = false; render();
    };

    $$("[data-pub-tnmode]").forEach((button) => button.onclick = () => tunnel({ action: "config", mode: button.dataset.pubTnmode }));
    const start = $("#pub-tn-start"); if (start) start.onclick = () => tunnel({ action: "start" }, true);
    const stop = $("#pub-tn-stop"); if (stop) stop.onclick = () => tunnel({ action: "stop" }, true);
    const auto = $("#pub-tn-auto"); if (auto) auto.onchange = () => tunnel({ action: "config", autostart: auto.checked });
    const token = $("#pub-tn-token"); if (token) token.onchange = () => { if (token.value.trim()) tunnel({ action: "config", token: token.value.trim() }); };
    const host = $("#pub-tn-host"); if (host) host.onchange = () => tunnel({ action: "config", hostname: host.value.trim() });
    const download = $("#pub-tn-download");
    if (download) download.onclick = () => confirmDialog(L("Скачать cloudflared?", "Download cloudflared?"),
      L(`Будет скачан официальный файл ${S.view.tunnel.asset} (~40–55 МБ) с github.com/cloudflare/cloudflared/releases и сохранён в папку данных Proxy.`,
        `The official ${S.view.tunnel.asset} (~40–55 MB) is downloaded from github.com/cloudflare/cloudflared/releases into the Proxy data folder.`),
      () => tunnel({ action: "download", confirm: true }, true), L("Скачать", "Download"));

    $$("[data-pub-sample]").forEach((button) => button.onclick = () => { S.sample = button.dataset.pubSample; render(); });
    $("#pub-code-copy").onclick = () => copyText($("#pub-code").textContent, L("Скопировано", "Copied"));
    const hide = $("#pub-fresh-hide"); if (hide) hide.onclick = () => { S.fresh = null; render(); };

    $("#pub-key-new").onclick = () => keyForm(null);
    $$("[data-pub-edit]").forEach((button) => button.onclick = () => keyForm(S.view.keys.find((key) => key.id === button.dataset.pubEdit)));
    $$("[data-pub-toggle]").forEach((box) => box.onchange = () => keyCall(box.dataset.pubToggle, { enabled: box.checked }));
    $$("[data-pub-rotate]").forEach((button) => button.onclick = () => {
      const key = S.view.keys.find((row) => row.id === button.dataset.pubRotate);
      confirmDialog(L("Ротация ключа?", "Rotate the key?"), `${key.name || hintOf(key)} — ${L("старый ключ перестанет работать сразу, новый покажется один раз", "the old key stops working at once, the new one is shown once")}`,
        async () => {

          S.fresh = null;
          if (await keyCall(key.id, { regenerate: true })) showFresh();
        }, L("Выпустить новый", "Issue a new one"));
    });
    $$("[data-pub-delete]").forEach((button) => button.onclick = () => {
      const key = S.view.keys.find((row) => row.id === button.dataset.pubDelete);
      confirmDialog(L("Отозвать ключ?", "Revoke the key?"), `${key.name || hintOf(key)} — ${L("доступ по нему прекратится сразу", "access with it stops at once")}`,
        () => keyCall(key.id, { delete: true }), L("Отозвать", "Revoke"));
    });
  }

  async function keyCall(id, body) {
    let ok = true;
    try { take(await api(`/api/admin/public/keys/${encodeURIComponent(id)}`, { method: "POST", body })); }
    catch (error) { ok = false; toast(errorOf(error), "err"); }
    render();
    return ok;
  }

  function showFresh() {
    if (!S.fresh) return;
    const key = S.fresh.key;
    openModal(`<h3>${esc(L("Ваш ключ", "Your key"))}</h3>
      <p class="sub">${esc(L("Скопируйте его сейчас и передайте тому, кому он предназначен. Proxy хранит только хэш — показать ключ ещё раз невозможно (потерянный ключ заменяет «Ротация»).",
        "Copy it now and hand it to the person it is for. The Proxy keeps only a hash — the key cannot be shown again (replace a lost key with “Rotate”)."))}</p>
      <pre class="code-block selectable" id="pk-created">${esc(key)}</pre>
      <div class="row-end"><button class="btn" id="pk-copy">${esc(L("Копировать", "Copy"))}</button><button class="btn primary" id="m-close">${esc(L("Готово", "Done"))}</button></div>`);
    $("#pk-copy").onclick = () => copyText(key, L("Скопировано", "Copied"));
    $("#m-close").onclick = closeModal;
  }

  function keyForm(key) {
    const isNew = !key;
    const row = key || { name: "", note: "", rpm: 20, rpd: 0, tpd: 0, tpmonth: 0, budget: 0, cost_budget: 0, max_tokens: 0, concurrency: 0, ips: [], expires: "", models: [] };
    const offered = S.view.settings.models;
    const own = new Set(row.models);
    const nf = (id, label, value, hint = "", attrs = 'min="0"') => `<div class="field"><label for="${id}">${esc(label)}</label><input type="number" id="${id}" ${attrs} value="${esc(value || 0)}">${hint ? `<div class="field-hint">${esc(hint)}</div>` : ""}</div>`;
    openModal(`<h3>${esc(isNew ? L("Новый ключ", "New key") : L("Ключ", "Key") + `: ${row.name || hintOf(row)}`)}</h3>
      <div class="grid-2">
        <div class="field"><label for="pk-name">${esc(L("Кому выдан", "Issued to"))}</label><input type="text" id="pk-name" maxlength="80" value="${esc(row.name)}" placeholder="${esc(L("Имя человека или приложения", "A person or an app"))}"></div>
        <div class="field"><label for="pk-expires">${esc(L("Действует до (включительно)", "Valid until (inclusive)"))}</label><input type="date" id="pk-expires" value="${esc(row.expires)}">
          <div class="field-hint">${esc(L("Пусто — бессрочно.", "Empty — no expiry."))}</div></div>
      </div>
      <h4 class="pk-h">${esc(L("Лимиты", "Limits"))} <span class="dim">${esc(L("0 — без ограничения", "0 — no limit"))}</span></h4>
      <div class="grid-3 pk-grid">
        ${nf("pk-rpm", L("Запросов в минуту", "Requests per minute"), row.rpm)}
        ${nf("pk-rpd", L("Запросов в день", "Requests per day"), row.rpd)}
        ${nf("pk-concurrency", L("Одновременных запросов", "Concurrent requests"), row.concurrency)}
        ${nf("pk-tpd", L("Токенов в день", "Tokens per day"), row.tpd)}
        ${nf("pk-tpmonth", L("Токенов в месяц", "Tokens per month"), row.tpmonth)}
        ${nf("pk-budget", L("Токенов всего", "Tokens in total"), row.budget)}
        ${nf("pk-maxtokens", L("Макс. токенов в ответе", "Max tokens per answer"), row.max_tokens, L("Больший max_tokens отклоняется.", "A larger max_tokens is refused."))}
        ${nf("pk-cost", L("Бюджет, $", "Budget, $"), row.cost_budget, L("Оценка по ценам моделей (вход + выход).", "Estimated from model prices (input + output)."), 'min="0" step="0.01"')}
      </div>
      <p class="field-hint">${esc(L("Токены считаются по ответам провайдера (вход + выход). Дневные лимиты сбрасываются в полночь, месячные — 1-го числа.", "Tokens are counted from the provider's answers (input + output). Daily limits reset at midnight, monthly ones on the 1st."))}</p>
      <div class="field"><label for="pk-ips">${esc(L("Разрешённые IP-адреса", "Allowed IP addresses"))}</label>
        <textarea id="pk-ips" rows="2" spellcheck="false" placeholder="203.0.113.7, 198.51.100.0/24">${esc((row.ips || []).join(", "))}</textarea>
        <div class="field-hint">${esc(L("Через запятую, можно подсети (CIDR). Пусто — с любого адреса. За Cloudflare Tunnel берётся настоящий адрес клиента.", "Comma-separated, subnets (CIDR) allowed. Empty — any address. Behind Cloudflare Tunnel the real client address is used."))}</div></div>
      <div class="field"><label>${esc(L("Модели для этого ключа", "Models for this key"))}</label>
        <label class="pick-row"><input type="checkbox" id="pk-all" ${own.size ? "" : "checked"}><span>${esc(L("Все модели из шага 3", "Every model from step 3"))}</span></label>
        <div class="model-pick-list" id="pk-models" ${own.size ? "" : "hidden"} style="max-height:180px;margin-top:8px">${offered.map((id) => `<label class="model-pick-row"><input type="checkbox" data-pk-model="${esc(id)}" ${own.has(id) ? "checked" : ""}><span class="mono">${esc(id)}</span></label>`).join("")
          || `<div class="empty">${esc(L("В публичном списке пока нет моделей", "The public list has no models yet"))}</div>`}</div></div>
      <div class="field"><label for="pk-note">${esc(L("Заметка", "Note"))}</label><input type="text" id="pk-note" maxlength="300" value="${esc(row.note)}"></div>
      <div class="row-end">${isNew ? "" : `<button class="btn" id="pk-reset">${esc(L("Обнулить статистику", "Reset usage"))}</button>`}
        <span class="grow"></span><button class="btn" id="m-cancel">${esc(L("Отмена", "Cancel"))}</button>
        <button class="btn primary" id="pk-save">${esc(isNew ? L("Создать", "Create") : L("Сохранить", "Save"))}</button></div>`, true);
    $("#m-cancel").onclick = closeModal;
    $("#pk-all").onchange = (event) => { $("#pk-models").hidden = event.target.checked; };
    const int = (id) => Math.max(0, Math.floor(+$(id).value || 0));
    const fields = () => ({
      name: $("#pk-name").value.trim(), note: $("#pk-note").value.trim(), expires: $("#pk-expires").value,
      rpm: int("#pk-rpm"), rpd: int("#pk-rpd"), tpd: int("#pk-tpd"), tpmonth: int("#pk-tpmonth"), budget: int("#pk-budget"),
      max_tokens: int("#pk-maxtokens"), concurrency: int("#pk-concurrency"), cost_budget: Math.max(0, +$("#pk-cost").value || 0),
      ips: $("#pk-ips").value.split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean),
      models: $("#pk-all").checked ? [] : $$("#pk-models [data-pk-model]").filter((box) => box.checked).map((box) => box.dataset.pkModel),
    });
    $("#pk-save").onclick = async () => {
      const body = fields();
      if (!$("#pk-all").checked && !body.models.length) { toast(L("Отметьте хотя бы одну модель или «Все модели»", "Tick at least one model or “Every model”"), "err"); return; }
      try {
        if (isNew) {
          take(await api("/api/admin/public/keys", { method: "POST", body }));
          render();
          showFresh();
        } else {
          if (await keyCall(key.id, body)) closeModal();
        }
      } catch (error) { toast(errorOf(error), "err"); }
    };
    if (!isNew) {
      $("#pk-reset").onclick = async () => { await keyCall(key.id, { reset_usage: true }); closeModal(); toast(L("Статистика обнулена", "Usage reset"), "ok"); };
    }
  }

  return { load, relabel: () => { if ($("#tab-public")?.classList.contains("active")) load(); } };
})();
