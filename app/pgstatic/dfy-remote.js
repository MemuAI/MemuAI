/* DFY на машинах (Раунд 30, пункт 1а) — window.MemuDFYRemote.

   Один файл для трёх мест, потому что данные у них одни (список целей и выбранная цель):
   * Настройки → Do For You → «Удалённые машины»: добавить машину (команда установки агента
     одной строкой, скачивание агента, systemd, SSH), статус онлайн/ОС, прокси машины, удалить
     (sectionHtml/bindSection — их зовёт dfy-settings.js);
   * выбор цели (Этот браузер / Этот компьютер / машина) рядом с полем ввода чата и в окне
     быстрого доступа — mount сам находит, где он работает;
   * карточка задачи: чип цели, вывод команды в шаге, подтверждение опасного шага с командой
     (targetChip/stepExtra/confirmHtml зовут dfy-client.js и quick-dfy.js).

   API: /api/dfy/remote/* (app/dfy_remote_api.py) и /api/dfy/proxy?machine=ID (app/dfy_api.py).
   Токены и пароли только на запись: сервер их не возвращает, в DOM они не живут дольше запроса. */
(function () {
  "use strict";
  if (window.MemuDFYRemote) return;

  const en = () => (window.PG_LANG || (window.MemuQuick && window.MemuQuick.lang()) || document.documentElement.lang || "ru").toLowerCase().startsWith("en");
  const T = (ru, e) => (en() ? e : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STORE = "memu_dfy_target";
  const OS_NAME = { linux: "Linux", windows: "Windows", darwin: "macOS" };
  // A small «server» glyph: emoji (🐧) has no glyph in every font (tofu in WebView2 without Segoe UI Emoji).
  const ICON = '<svg class="dfy-ico" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>';

  async function api(path, options) {
    const r = await fetch(path, Object.assign({ cache: "no-store" }, options || {}));
    let data = {};
    try { data = await r.json(); } catch (_) {}
    if (!r.ok) throw Object.assign(new Error(data.error || data.detail || "HTTP " + r.status), { status: r.status });
    return data;
  }
  const send = (method, path, body) => api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  /* --------------------------------------------------------------- state --- */
  const S = { targets: null, at: 0, local: false, loading: null };
  const BROWSER = () => ({ id: "", name: T("Этот браузер", "This browser"), kind: "browser" });

  async function targets(force) {
    if (!force && S.targets && Date.now() - S.at < 20000) return S.targets;
    if (S.loading) return S.loading;
    S.loading = api("/api/dfy/remote/machines").then((data) => {
      S.local = !!data.local_system;
      S.targets = (data.targets || []).filter((row) => row.id !== "local" || S.local);
      S.at = Date.now();
      S.loading = null;
      emit();
      return S.targets;
    }).catch(() => { S.loading = null; if (!S.targets) S.targets = []; return S.targets; });
    return S.loading;
  }
  function known(id) { return (S.targets || []).find((row) => row.id === id) || null; }
  function selected() {
    const id = (() => { try { return localStorage.getItem(STORE) || ""; } catch (_) { return ""; } })();
    return id && known(id) ? id : "";
  }
  function select(id) {
    try { id ? localStorage.setItem(STORE, id) : localStorage.removeItem(STORE); } catch (_) {}
    emit();
  }
  const listeners = new Set();
  function emit() { listeners.forEach((fn) => { try { fn(); } catch (_) {} }); }

  function label(id) {
    if (!id) return BROWSER().name;
    const row = known(id);
    if (row) return row.name;
    return id === "local" ? T("Этот компьютер", "This computer") : id;
  }

  /* «/dfy @сервер поставь nginx» или выбранная цель над полем ввода. */
  function parseCommand(text) {
    const goal = String(text || "").trim();
    const match = goal.match(/^@([\wа-яё .-]{1,60}?)[\s,:]+(.+)$/is);
    if (match) {
      const wanted = match[1].trim().toLowerCase();
      const row = (S.targets || []).find((t) => t.name.toLowerCase() === wanted || t.id.toLowerCase() === wanted)
        || (/^(этот\s*(пк|компьютер)|this\s*(pc|computer)|local)$/i.test(wanted) && S.local ? { id: "local" } : null);
      if (row) return { goal: match[2].trim(), target: row.id };
    }
    return { goal, target: selected() };
  }

  /* --------------------------------------------- карточка задачи (dfy-client) --- */
  function targetChip(target) {
    if (!target || !target.id) return "";
    const os = OS_NAME[String(target.os || "").toLowerCase()] || "";
    const name = target.id === "local" ? T("Этот компьютер", "This computer") : target.name || target.id;
    if (target.browser) {
      return `<span class="dfy-chip dfy-chip--target" title="${esc(T("Браузер DFY запущен на этой машине: её IP и сеть", "DFY's browser runs on this machine: its IP and network") + (os ? " · " + os : ""))}">${ICON}${esc(name + " — " + T("браузер", "browser"))}</span>`;
    }
    return `<span class="dfy-chip dfy-chip--target" title="${esc(T("Задача выполняется на этой машине", "The task runs on this machine") + (os ? " · " + os : ""))}">${ICON}${esc(name)}</span>`;
  }
  function stepExtra(step, opts) {
    const text = String((step && step.output) || "");
    if (!text.trim()) return "";
    const live = opts && opts.live && step.ok !== false;
    const code = step.exit_code;
    const tag = (opts && opts.tag) || "li";
    return `<${tag} class="dfy-out${live ? " live" : ""}"><pre>${esc(text.length > 4000 ? text.slice(-4000) : text)}</pre>
      <div class="dfy-out-foot"><button type="button" class="dfy-more" data-dfy-copy>${esc(T("Копировать вывод", "Copy output"))}</button>
      ${code === undefined || code === null ? "" : `<span class="${code === 0 ? "" : "bad"}">exit ${esc(code)}</span>`}</div></${tag}>`;
  }
  const ACTION_TITLE = {
    shell: ["Выполнить команду", "Run a command"], file_delete: ["Удалить", "Delete"],
    file_write: ["Перезаписать файл", "Overwrite a file"], package: ["Пакет", "Package"],
    process_kill: ["Завершить процесс", "Kill a process"], download: ["Скачать файл", "Download a file"],
  };
  /* Подтверждение шага на машине: что именно, где и почему опасно (команда — отдельным блоком). */
  function confirmBody(q) {
    const title = ACTION_TITLE[q.action] || ["Действие на машине", "Action on the machine"];
    const shell = q.action === "shell" && q.detail ? String(q.detail).replace(/^\$\s*/, "") : "";
    // The server sends «why it is dangerous\n\nthe question»: the title and the command already say the first part.
    const last = String(q.prompt || "").split(/\n\s*\n/).pop();
    const prompt = shell ? last.split("\n").filter((line) => line.trim() && line.trim() !== "$ " + shell && line.trim() !== shell).join("\n") : last;
    return `<div class="dfy-q-head"><b>${esc(en() ? title[1] : title[0])}</b><span class="dfy-chip dfy-chip--target">${esc(q.machine || "")}</span></div>
      ${shell ? `<pre class="dfy-cmd">${esc(shell)}</pre>` : q.detail ? `<p class="dfy-cmd-plain">${esc(q.detail)}</p>` : ""}
      <p class="dfy-q-why">${esc(prompt)}</p>`;
  }
  function confirmHtml(q) {
    return `<div class="dfy-q dfy-q--machine${q.danger ? " danger" : ""}" data-kind="confirm">${confirmBody(q)}
      <div class="dfy-row"><button class="dfy-btn primary" data-dfy="approve">${T("Разрешить", "Allow")}</button>
      <button class="dfy-btn" data-dfy="reject">${T("Не надо", "Don't")}</button></div></div>`;
  }

  /* ------------------------------------------------------- выбор цели в чате --- */
  const PICKER_ID = "dfy-target-picker";
  function pickerHtml() {
    const id = selected();
    const rows = [BROWSER()].concat(S.targets || []);
    const options = rows.map((r) => {
      const offline = r.kind !== "browser" && r.id !== "local" && r.online === false ? " · " + T("офлайн", "offline") : "";
      return `<option value="${esc(r.id)}" ${r.id === id ? "selected" : ""}>${esc(r.name + offline)}</option>`;
    }).join("");
    return `<span class="dfy-target-label">${esc(T("DFY:", "DFY:"))}</span>
      <select aria-label="${esc(T("Где работает DFY", "Where DFY works"))}" title="${esc(T("Где DFY выполняет задачи: в своём браузере или на машине", "Where DFY does the task: in its browser or on a machine"))}">${options}</select>`;
  }
  function mountPicker() {
    const host = document.querySelector(".composer .composer-tools");
    if (!host) return null;
    let box = document.getElementById(PICKER_ID);
    if (!box) {
      box = document.createElement("div");
      box.id = PICKER_ID;
      box.className = "dfy-target";
      box.hidden = true;
      box.addEventListener("change", (e) => { if (e.target.tagName === "SELECT") select(e.target.value); });
      host.appendChild(box);
    }
    const paint = () => {
      box.hidden = !(S.targets || []).length;        // нет машин — ничего лишнего в композере
      if (!box.hidden) box.innerHTML = pickerHtml();
    };
    listeners.add(paint);
    targets().then(paint);
    return paint;
  }

  /* ------------------------------------------------- настройки: раздел машин --- */
  function machineRow(ctx, row) {
    const e = ctx.esc;
    const L = ctx.L;
    const where = row.kind === "ssh" ? `SSH ${row.user ? row.user + "@" : ""}${row.host}${row.port && row.port !== 22 ? ":" + row.port : ""}`
      : `${row.host}:${row.port}`;
    const state = row.online ? `<span class="sx-chip" data-tone="ok">${e(L("онлайн", "online"))}</span>`
      : `<span class="sx-chip">${e(row.seen ? L("не отвечает", "not answering") : L("не проверена", "not checked yet"))}</span>`;
    return `<div class="dfy-machine" data-machine="${e(row.id)}">
      <div class="dfy-machine-main"><div class="sx-label tl"><span class="dfy-machine-name">${ICON}${e(row.name)}</span>${state}</div>
        <div class="sx-desc td">${e(where)}${row.os_label ? " · " + e(row.os_label) : ""}${row.agent_version ? " · " + e(L("агент ", "agent ") + row.agent_version) : ""}${row.roots && row.roots.length ? " · " + e(L("каталоги: ", "directories: ") + row.roots.join(", ")) : ""}</div>
        ${row.error ? `<div class="dfy-machine-err">${e(row.error)}</div>` : ""}</div>
      <div class="dfy-machine-acts">
        <button type="button" class="pbtn ghost" data-dfy-machine="check">${e(L("Проверить", "Check"))}</button>
        <button type="button" class="pbtn ghost" data-dfy-machine="proxy">${e(L("Прокси", "Proxy"))}</button>
        <button type="button" class="pbtn ghost danger" data-dfy-machine="delete">${e(L("Удалить", "Delete"))}</button>
      </div>
      <div class="dfy-machine-panel" hidden></div></div>`;
  }

  function sectionHtml(ctx) {
    const e = ctx.esc;
    const K = ctx.kit;
    const pref = ctx.pref;
    const L = ctx.L;
    return K.section({ id: "dfy-machines", title: L("Удалённые машины", "Remote machines"),
      desc: L("DFY умеет не только браузер: на вашем сервере или другом компьютере он выполняет команды, работает с файлами, ставит пакеты и смотрит процессы — обычным текстом в чате. Агент никогда не трогает сам себя, MemuAI и его данные, а опасные шаги (удаление, перезагрузка, снос пакета) спрашивают вас.",
        "DFY is not only a browser: on your server or another computer it runs commands, works with files, installs packages and looks at processes — from plain chat. The agent never touches itself, MemuAI or its data, and dangerous steps (deleting, rebooting, removing a package) ask you first."),
      body:
        K.row({ label: L("Этот компьютер", "This computer"), key: "dfy_local_system", def: false,
          keywords: "local этот компьютер пк machine команды files файлы shell",
          desc: L("Разрешить DFY выполнять команды и менять файлы на этом ПК. Папка MemuAI, его данные и системные каталоги защищены всегда.",
            "Let DFY run commands and change files on this PC. The MemuAI folder, its data and system directories are always protected."),
          control: K.toggle("st-dfy-local", pref("dfy_local_system", false)) })
        + `<div class="sx-row sx-row--stack" data-sx-k="машины machines сервер server агент agent ssh">
            <div class="sx-label tl">${e(L("Машины", "Machines"))}</div>
            <div id="st-dfy-machines" class="dfy-machines" style="width:100%">${e(L("Загрузка…", "Loading…"))}</div>
            <div class="dfy-machines-foot">
              <button type="button" class="pbtn" id="st-dfy-machine-add">${e(L("Добавить машину", "Add a machine"))}</button>
              <button type="button" class="pbtn ghost" id="st-dfy-machine-refresh">${e(L("Обновить", "Refresh"))}</button></div>
            <div id="st-dfy-machine-form" class="dfy-add" hidden></div>
          </div>` });
  }

  function addFormHtml(ctx, install) {
    const e = ctx.esc;
    const L = ctx.L;
    const one = install.paste || "";
    return `<div class="dfy-add-tabs" role="tablist">
        <button type="button" class="pbtn ghost active" data-dfy-tab="agent" role="tab">${e(L("Агент (рекомендуется)", "Agent (recommended)"))}</button>
        <button type="button" class="pbtn ghost" data-dfy-tab="ssh" role="tab">${e(L("По SSH, без установки", "Over SSH, no install"))}</button></div>
      <div class="dfy-add-pane" data-dfy-pane="agent">
        <ol class="dfy-steps-help">
          <li>${e(L("Выполните на машине одну команду — она создаёт файл агента и запускает его (интернет на машине не нужен):", "Run one command on the machine — it writes the agent file and starts it (the machine needs no internet):"))}
            <div class="dfy-cmd-row"><code>${e(one.slice(0, 48))}…<span class="td"> ${e("(~" + Math.round((+install.size || 0) / 1024) + (en() ? " KB)" : " КБ)"))}</span></code>
              <button type="button" class="pbtn ghost" data-dfy-copy-cmd="paste">${e(L("Копировать команду", "Copy command"))}</button>
              <a class="pbtn ghost" href="/api/dfy/remote/agent.py" download="${e(install.file || "memuai_remote_agent.py")}">${e(L("Скачать агента", "Download the agent"))}</a></div>
            <div class="td">${e(L("Можно и скопировать файл самому: ", "You can also copy the file yourself: "))}<code>${e(install.scp || "")}</code></div></li>
          <li>${e(L("Агент напечатает код подключения MEMU1-… — вставьте его сюда:", "The agent prints a pairing code MEMU1-… — paste it here:"))}
            <div class="dfy-cmd-row"><input class="pinput sx-input" id="st-dfy-code" spellcheck="false" autocomplete="off" placeholder="MEMU1-…" style="flex:1;min-width:200px;margin:0">
              <input class="pinput sx-input" id="st-dfy-code-name" placeholder="${e(L("Имя (необязательно)", "Name (optional)"))}" style="width:170px;margin:0">
              <button type="button" class="pbtn" id="st-dfy-code-add">${e(L("Подключить", "Connect"))}</button></div></li>
          <li>${e(L("Чтобы агент пережил перезагрузку — служба systemd:", "To survive a reboot — a systemd service:"))}
            <div class="dfy-cmd-row"><code>${e((install.systemd || "").slice(0, 64))}…</code>
              <button type="button" class="pbtn ghost" data-dfy-copy-cmd="systemd">${e(L("Копировать", "Copy"))}</button></div></li>
        </ol>
        <div class="td">${e(L("Агент слушает 0.0.0.0:8765. Если машина в интернете, держите его за SSH-туннелем (ssh -L 8765:127.0.0.1:8765 …) или за фаерволом: код подключения содержит токен, которым MemuAI подписывает запросы.",
          "The agent listens on 0.0.0.0:8765. If the machine is on the internet, keep it behind an SSH tunnel (ssh -L 8765:127.0.0.1:8765 …) or a firewall: the pairing code holds the token MemuAI signs its requests with."))}</div>
      </div>
      <div class="dfy-add-pane" data-dfy-pane="ssh" hidden>
        <div class="td">${e(L("Ничего ставить не нужно: MemuAI запускает агента по SSH на один вызов (нужен только python3 на сервере). Вход — по ключу или ssh-agent; пароль SSH хранится в том же шифрованном хранилище, что и секреты DFY.",
          "Nothing to install: MemuAI runs the agent over SSH for a single call (the server only needs python3). Sign-in by key or ssh-agent; an SSH password is kept in the same encrypted store as DFY secrets."))}</div>
        <div class="dfy-cmd-row"><input class="pinput sx-input" id="st-dfy-ssh-host" placeholder="${e(L("Адрес: server.example.com", "Address: server.example.com"))}" spellcheck="false" style="flex:1;min-width:190px;margin:0">
          <input class="pinput sx-input" id="st-dfy-ssh-user" placeholder="${e(L("Пользователь", "User"))}" spellcheck="false" style="width:140px;margin:0">
          <input class="pinput sx-input" id="st-dfy-ssh-port" type="number" min="1" max="65535" placeholder="22" style="width:88px;margin:0"></div>
        <div class="dfy-cmd-row"><input class="pinput sx-input" id="st-dfy-ssh-key" placeholder="${e(L("Путь к ключу (необязательно)", "Key path (optional)"))}" spellcheck="false" style="flex:1;min-width:190px;margin:0">
          <input class="pinput sx-input" id="st-dfy-ssh-pass" type="password" autocomplete="new-password" placeholder="${e(L("Пароль SSH", "SSH password"))}" title="${e(L("Только если входите без ключа", "Only if you sign in without a key"))}" style="width:180px;margin:0">
          <input class="pinput sx-input" id="st-dfy-ssh-name" placeholder="${e(L("Имя", "Name"))}" style="width:140px;margin:0"></div>
        <div class="dfy-cmd-row"><button type="button" class="pbtn" id="st-dfy-ssh-add">${e(L("Добавить", "Add"))}</button>
          <span class="td" id="st-dfy-ssh-note"></span></div>
      </div>`;
  }

  function proxyPanelHtml(ctx, cfg) {
    const e = ctx.esc;
    const L = ctx.L;
    return `<form class="dfy-proxy" autocomplete="off" novalidate>
      <div class="sx-label tl">${e(L("Прокси этой машины", "This machine's proxy"))}</div>
      <div class="sx-desc td">${e(L("Команды, установка пакетов, скачивания и браузер DFY на этой машине идут через прокси (имена разрешает прокси). Другие программы на машине он не трогает. Браузеру на машине нужен прокси без логина.",
        "DFY's commands, package installs, downloads and browser on this machine go through the proxy (names resolved by the proxy). It does not affect other programs on the machine. The browser on the machine needs a proxy without a login."))}</div>
      <div class="dfy-cmd-row">
        <select class="pinput compact sx-select" data-proxy="scheme" style="width:108px;margin:0">
          ${["http", "https", "socks5"].map((s) => `<option value="${s}" ${cfg.scheme === s ? "selected" : ""}>${s.toUpperCase()}</option>`).join("")}
        </select>
        <input class="pinput sx-input" data-proxy="host" value="${e(cfg.host || "")}" spellcheck="false" placeholder="${e(L("Адрес прокси", "Proxy address"))}" style="flex:1;min-width:160px;margin:0">
        <input class="pinput sx-input" data-proxy="port" type="number" min="1" max="65535" value="${e(cfg.port || "")}" placeholder="${e(L("Порт", "Port"))}" style="width:90px;margin:0"></div>
      <div class="dfy-cmd-row">
        <input class="pinput sx-input" data-proxy="user" autocomplete="off" spellcheck="false" placeholder="${e(cfg.auth ? L("Логин сохранён", "Login saved") : L("Логин (если нужен)", "Username (if required)"))}" style="flex:1;min-width:140px;margin:0">
        <input class="pinput sx-input" data-proxy="pass" type="password" autocomplete="new-password" placeholder="${e(cfg.auth ? L("••• сохранён", "••• saved") : L("Пароль", "Password"))}" style="flex:1;min-width:140px;margin:0"></div>
      <div class="dfy-cmd-row"><button type="submit" class="pbtn">${e(L("Сохранить", "Save"))}</button>
        <button type="button" class="pbtn ghost" data-proxy-act="check">${e(L("Проверить с машины", "Check from the machine"))}</button>
        ${cfg.enabled || cfg.host ? `<button type="button" class="pbtn ghost danger" data-proxy-act="off">${e(L("Выключить", "Turn off"))}</button>` : ""}
        <span class="td" role="status" aria-live="polite" data-proxy-result></span></div></form>`;
  }

  function bindSection(ctx) {
    const box = document.getElementById("st-dfy-machines");
    if (!box) return;
    const L = ctx.L;
    const signal = ctx.signal;
    const on = (el, type, fn) => { if (el) el.addEventListener(type, fn, signal ? { signal } : false); };
    const toast = ctx.toast || (() => {});
    const copy = (text) => (window.copyText ? window.copyText(text)
      : navigator.clipboard.writeText(text).then(() => toast(L("Скопировано", "Copied"))).catch(() => toast(L("Не удалось скопировать", "Could not copy"), "err")));
    let rows = [];

    const paint = () => {
      box.innerHTML = rows.length ? rows.map((row) => machineRow(ctx, row)).join("")
        : `<span class="td">${ctx.esc(L("Пока нет машин. Нажмите «Добавить машину»: агент ставится на сервер одной командой, а вариант по SSH работает вообще без установки.",
          "No machines yet. Press “Add a machine”: one command installs the agent on a server, and the SSH option needs no install at all."))}</span>`;
    };
    const load = async () => {
      try { rows = (await api("/api/dfy/remote/machines")).machines || []; }
      catch (err) { box.textContent = err.message; return; }
      paint();
      targets(true);
    };
    load();
    on(document.getElementById("st-dfy-machine-refresh"), "click", load);
    on(document.getElementById("st-dfy-local"), "change", async (e) => {
      await ctx.savePreferences({ dfy_local_system: !!e.target.checked });
      if (window.MemuDFY && window.MemuDFY.refreshStatus) window.MemuDFY.refreshStatus();
      targets(true);
    });

    /* --- добавление --- */
    const form = document.getElementById("st-dfy-machine-form");
    on(document.getElementById("st-dfy-machine-add"), "click", async (ev) => {
      if (!form.hidden) { form.hidden = true; return; }
      form.hidden = false;
      form.innerHTML = `<span class="td">${ctx.esc(L("Загрузка…", "Loading…"))}</span>`;
      let install = {};
      try { install = await api("/api/dfy/remote/install"); } catch (err) { form.textContent = err.message; return; }
      form.innerHTML = addFormHtml(ctx, install);
      form.__install = install;
      const field = form.querySelector("#st-dfy-code");
      if (field) field.focus();
      form.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    on(form, "click", async (e) => {
      const tab = e.target.closest("[data-dfy-tab]");
      if (tab) {
        form.querySelectorAll("[data-dfy-tab]").forEach((b) => b.classList.toggle("active", b === tab));
        form.querySelectorAll("[data-dfy-pane]").forEach((p) => { p.hidden = p.dataset.dfyPane !== tab.dataset.dfyTab; });
        return;
      }
      const cmd = e.target.closest("[data-dfy-copy-cmd]");
      if (cmd) { copy((form.__install || {})[cmd.dataset.dfyCopyCmd] || ""); return; }
      if (e.target.id === "st-dfy-code-add") {
        const code = form.querySelector("#st-dfy-code").value.trim();
        if (!code) { form.querySelector("#st-dfy-code").focus(); return; }
        e.target.disabled = true;
        try {
          const added = await send("POST", "/api/dfy/remote/machines", { code, name: form.querySelector("#st-dfy-code-name").value.trim() });
          toast(added.machine.online ? L("Машина подключена: ", "Machine connected: ") + added.machine.name
            : L("Машина добавлена, но не отвечает — проверьте, что агент запущен", "Machine added but not answering — check that the agent is running"));
          form.hidden = true;
          await load();
        } catch (err) { toast(err.message, "err"); }
        finally { e.target.disabled = false; }
        return;
      }
      if (e.target.id === "st-dfy-ssh-add") {
        const value = (id) => form.querySelector("#st-dfy-ssh-" + id).value.trim();
        if (!value("host")) { form.querySelector("#st-dfy-ssh-host").focus(); return; }
        e.target.disabled = true;
        const note = form.querySelector("#st-dfy-ssh-note");
        note.textContent = L("Проверяю связь…", "Checking the connection…");
        try {
          const body = { kind: "ssh", host: value("host"), user: value("user"), port: +value("port") || 22,
            key_path: value("key"), name: value("name") };
          const pass = form.querySelector("#st-dfy-ssh-pass");
          if (pass.value) body.password = pass.value;
          pass.value = "";
          const added = await send("POST", "/api/dfy/remote/machines", body);
          note.textContent = "";
          toast(added.machine.online ? L("Машина подключена: ", "Machine connected: ") + added.machine.name
            : L("Добавлена; связь не установлена: ", "Added; no connection yet: ") + (added.machine.error || ""));
          form.hidden = true;
          await load();
        } catch (err) { note.textContent = ""; toast(err.message, "err"); }
        finally { e.target.disabled = false; }
      }
    });

    /* --- строки машин --- */
    on(box, "click", async (e) => {
      const proxyAct = e.target.closest("[data-proxy-act]");
      if (proxyAct) return proxyAction(proxyAct);
      const button = e.target.closest("[data-dfy-machine]");
      if (!button) return;
      const host = button.closest(".dfy-machine");
      const id = host.dataset.machine;
      const panel = host.querySelector(".dfy-machine-panel");
      const act = button.dataset.dfyMachine;
      if (act === "delete") {
        const row = rows.find((r) => r.id === id);
        if (!confirm(L("Удалить машину «", "Delete the machine “") + (row ? row.name : id) + L("»? Задачи на ней больше не запустятся.", "”? Tasks will no longer run on it."))) return;
        try { await send("DELETE", "/api/dfy/remote/machines/" + encodeURIComponent(id)); await load(); }
        catch (err) { toast(err.message, "err"); }
        return;
      }
      if (act === "check") {
        button.disabled = true;
        const old = button.textContent;
        button.textContent = L("Проверяю…", "Checking…");
        try {
          const r = await send("POST", `/api/dfy/remote/machines/${encodeURIComponent(id)}/check`);
          toast(r.ok ? L("Отвечает: ", "Answering: ") + ((r.info && (r.info.distro || r.info.os)) || "") : (r.error || L("Нет связи", "No connection")), r.ok ? "ok" : "err");
          await load();
        } catch (err) { toast(err.message, "err"); button.disabled = false; button.textContent = old; }
        return;
      }
      if (act === "proxy") {
        if (!panel.hidden) { panel.hidden = true; panel.innerHTML = ""; return; }
        panel.hidden = false;
        panel.innerHTML = `<span class="td">${ctx.esc(L("Загрузка…", "Loading…"))}</span>`;
        let cfg = {};
        try { cfg = await api("/api/dfy/proxy?machine=" + encodeURIComponent(id)); }
        catch (err) {
          panel.innerHTML = `<span class="td">${ctx.esc(err.status === 404
            ? L("Прокси DFY недоступен в этой версии", "The DFY proxy is unavailable in this build") : err.message)}</span>`;
          return;
        }
        panel.innerHTML = proxyPanelHtml(ctx, cfg);
      }
    });
    const proxyFields = (formEl) => {
      const get = (name) => formEl.querySelector(`[data-proxy="${name}"]`);
      const body = { scheme: get("scheme").value, host: get("host").value.trim(), port: +get("port").value || 0 };
      if (/:\/\/|@/.test(body.host)) { body.address = body.host; delete body.host; delete body.port; delete body.scheme; }
      if (get("user").value.trim()) body.username = get("user").value.trim();
      if (get("pass").value) body.password = get("pass").value;
      get("pass").value = "";
      return body;
    };
    on(box, "submit", async (e) => {
      const formEl = e.target.closest("form.dfy-proxy");
      if (!formEl) return;
      e.preventDefault();
      const id = formEl.closest(".dfy-machine").dataset.machine;
      const body = proxyFields(formEl);
      if (!body.host && !body.address) { formEl.querySelector('[data-proxy="host"]').focus(); return; }
      try {
        const saved = await send("PUT", "/api/dfy/proxy?machine=" + encodeURIComponent(id), { ...body, enabled: true });
        formEl.closest(".dfy-machine-panel").innerHTML = proxyPanelHtml(ctx, saved);
        toast(L("Прокси машины сохранён", "The machine's proxy is saved"));
      } catch (err) { toast(err.message, "err"); }
    });
    async function proxyAction(act) {
      const formEl = act.closest("form.dfy-proxy");
      const id = act.closest(".dfy-machine").dataset.machine;
      const out = formEl.querySelector("[data-proxy-result]");
      if (act.dataset.proxyAct === "off") {
        try { await send("DELETE", "/api/dfy/proxy?machine=" + encodeURIComponent(id)); act.closest(".dfy-machine-panel").innerHTML = proxyPanelHtml(ctx, {}); }
        catch (err) { toast(err.message, "err"); }
        return;
      }
      const body = proxyFields(formEl);
      act.disabled = true;
      out.textContent = L("Проверяю с машины…", "Checking from the machine…");
      try {
        const r = await send("POST", `/api/dfy/remote/machines/${encodeURIComponent(id)}/proxy/check`, body.host || body.address ? body : {});
        out.textContent = r.ok ? L(`Работает: машина выходит с IP ${r.ip} · ${r.ms} мс`, `Works: the machine's external IP is ${r.ip} · ${r.ms} ms`) : "⚠ " + (r.error || L("Не удалось", "Failed"));
      } catch (err) { out.textContent = "⚠ " + err.message; }
      finally { act.disabled = false; }
    }
  }

  /* ------------------------------------------------------------- быстрый доступ --- */
  function mountQuick() {
    const Q = window.MemuQuick;
    if (!Q || !Q.registerPanel) return;
    let host = null;
    const panel = Q.registerPanel("dfy-target", (el) => {
      host = el;
      el.classList.add("qdfy-target");
      el.addEventListener("change", (e) => { if (e.target.tagName === "SELECT") select(e.target.value); });
      return { show() { paint(); }, hide() {}, unmount() { host = null; } };
    }, { slot: "bottom", order: -6, hidden: true });
    const paint = () => {
      if (!(S.targets || []).length) { panel.hide(); return; }
      panel.show();
      if (host) host.innerHTML = pickerHtml();
    };
    listeners.add(paint);
    targets().then(paint);
    if (Q.on) Q.on("open", () => targets(true).then(paint));
  }

  /* --------------------------------------------------------------- загрузка --- */
  function boot() {
    document.addEventListener("click", (e) => {
      const copyButton = e.target.closest("[data-dfy-copy]");
      if (!copyButton) return;
      const box = copyButton.closest(".dfy-out");
      const pre = box && box.querySelector("pre");
      if (pre) (window.copyText ? window.copyText(pre.textContent) : navigator.clipboard.writeText(pre.textContent).catch(() => {}));
    });
    if (window.MemuQuick) mountQuick(); else mountPicker();
  }
  if (document.body) boot(); else document.addEventListener("DOMContentLoaded", boot, { once: true });

  window.MemuDFYRemote = {
    targets, refresh: () => targets(true), selected, select, label, parseCommand,
    targetChip, stepExtra, confirmBody, confirmHtml, sectionHtml, bindSection,
    onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  };
})();
