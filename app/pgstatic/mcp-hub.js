

(function () {
  "use strict";
  const isEn = () => { try { return PG_LANG === "en"; } catch (e) { return (document.documentElement.lang || "ru").startsWith("en"); } };
  const L = (ru, en) => (isEn() ? en : ru);
  const H = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const kit = () => window.MemuSettingsKit;
  const call = (path, opts) => (typeof api === "function" ? api(path, opts) : fetch(path, { method: (opts && opts.method) || "GET",
    headers: { "Content-Type": "application/json" }, body: opts && opts.body ? JSON.stringify(opts.body) : undefined }).then((r) => r.json()));
  const IC = {
    plug: '<svg viewBox="0 0 24 24"><path d="M9 3v5M15 3v5M7 8h10v3a5 5 0 0 1-10 0zM12 16v5"/></svg>',
    check: '<svg viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>',
    gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/></svg>',
    lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
    plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
    ext: '<svg viewBox="0 0 24 24"><path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/></svg>',
  };
  const ico = (n) => `<span class="mcph-ic" aria-hidden="true">${IC[n] || ""}</span>`;
  const st = { data: null, loading: false, open: {}, checking: {}, install: "", filter: "", busy: false, scope: { key: "", at: 0, rows: [] } };

  function listHtml() {
    const K = kit();
    const n = st.data ? st.data.servers.length : null;
    return K.link({ sub: "int-mcp", icon: `<span class="mcph-brand">${IC.plug}</span>`, label: "MCP", id: "mcph-row",
      desc: L("Внешние инструменты: базы данных, Jira, Linear, GitLab, Obsidian, Home Assistant и другие — в чате, у мемесов, в Планировании и Быстром доступе",
        "External tools: databases, Jira, Linear, GitLab, Obsidian, Home Assistant and more — in the chat, for memes, in Planning and Quick Access"),
      value: `<span id="mcph-row-value">${n === null ? "" : H(n ? L(`${n} подключено`, `${n} connected`) : L("Не подключено", "Not connected"))}</span>`,
      keywords: "mcp model context protocol сервер server postgres mysql clickhouse jira linear gitlab obsidian logseq home assistant инструменты tools" });
  }
  function subHtml() {
    return kit().sub({ id: "int-mcp", title: "MCP", desc: L("Подключайте серверы Model Context Protocol: ассистент вызывает их инструменты и читает ресурсы. Серверы запускаются только когда нужны.",
      "Connect Model Context Protocol servers: the assistant calls their tools and reads their resources. Servers start only when needed."),
      body: `<div class="mcph-root" id="mcph-root"><div class="mcph-loading">${L("Загружаю…", "Loading…")}</div></div>` });
  }
  function mount() {
    const row = document.getElementById("mcph-row");
    if (row && !row._mcph) { row._mcph = true; row.addEventListener("click", () => load()); }
    const sub = document.querySelector('.sx-sub[data-sx-sub="int-mcp"]');
    if (sub && !sub.hidden) load();
  }

  async function load() {
    if (st.loading) return;
    st.loading = true;
    try { st.data = await call("/api/mcp/hub?lang=" + (isEn() ? "en" : "ru")); } catch (e) { st.data = st.data || { servers: [], catalog: [], cats: {}, agents: [], error: e.message }; }
    st.loading = false;
    render();
  }

  function chip(text, tone = "") { return `<span class="mcph-chip ${tone}">${H(text)}</span>`; }
  function sw(attr, on, label) { return `<label class="mcph-sw"><span>${H(label)}</span><span class="switch"><input type="checkbox" ${attr} ${on ? "checked" : ""}><span class="sl"></span></span></label>`; }

  function serverCard(s) {
    const d = st.data;
    const item = (d.catalog || []).find((c) => c.id === s.catalog_id);
    const state = s.error ? "err" : s.running ? "live" : "idle";
    const stateText = s.running ? L("работает", "running") : s.enabled ? L("запустится при вызове", "starts on demand") : L("выключен", "off");
    const where = [s.chat && L("чат", "chat"), s.quick && L("Быстрый доступ", "Quick Access"), (s.agents || []).length && L(`мемесы: ${s.agents.length}`, `memes: ${s.agents.length}`)].filter(Boolean);
    const open = !!st.open[s.name];
    const check = st.checking[s.name];
    const mono = item ? `<span class="mcph-mono" style="--c:${H(item.color)}">${H(item.title.slice(0, 1))}</span>` : `<span class="mcph-mono">${IC.plug}</span>`;
    return `<article class="mcph-card ${state}${open ? " open" : ""}" data-mcph-card="${H(s.name)}">
      <header class="mcph-head">
        ${mono}
        <div class="mcph-title"><b>${H(s.name)}</b><small><i class="mcph-dot"></i>${H(stateText)} · ${H(s.type === "stdio" ? (s.command || "") : (s.url || "").replace(/^https?:\/\//, ""))}</small></div>
        <div class="mcph-chips">${s.read_only ? chip(L("только чтение", "read-only"), "ro") : ""}${where.map((w) => chip(w)).join("")}${s.tools.length ? chip(L(`${s.tools.length} инстр.`, `${s.tools.length} tools`), "muted") : ""}</div>
        <button type="button" class="pbtn ghost sx-btn-sm" data-mcph="check" data-n="${H(s.name)}" ${check ? "disabled" : ""}>${check ? L("Проверяю…", "Checking…") : L("Проверить", "Check")}</button>
        <button type="button" class="mcph-iconbtn" data-mcph="toggle-open" data-n="${H(s.name)}" aria-expanded="${open}" title="${L("Настроить", "Settings")}">${IC.gear}</button>
        <span class="switch" title="${L("Включён", "Enabled")}"><input type="checkbox" data-mcph-on="${H(s.name)}" ${s.enabled ? "checked" : ""}><span class="sl"></span></span>
      </header>
      ${s.error ? `<div class="mcph-err">${H(s.error)}</div>` : ""}
      ${s._check ? checkHtml(s._check) : ""}
      ${open ? detailsHtml(s) : ""}
    </article>`;
  }

  function checkHtml(r) {
    if (!r.ok) return `<div class="mcph-err">${H(r.error || L("Не удалось подключиться", "Could not connect"))}</div>`;
    const info = r.server_info || {};
    return `<div class="mcph-ok">${ico("check")}${L("Соединение есть", "Connected")}${info.name ? ` · ${H(info.name)}${info.version ? " " + H(info.version) : ""}` : ""} · ${L(`инструментов: ${r.tools.length}, ресурсов: ${r.resources.length}`, `tools: ${r.tools.length}, resources: ${r.resources.length}`)} · ${r.ms} ${L("мс", "ms")}</div>`;
  }

  function detailsHtml(s) {
    const d = st.data;
    const confirmSel = `<select class="pinput compact" data-mcph-set="confirm" data-n="${H(s.name)}">${[["write", L("только меняющие данные", "only tools that change data")], ["always", L("каждый вызов", "every call")], ["never", L("никогда", "never")]].map(([v, l]) => `<option value="${v}" ${s.confirm === v ? "selected" : ""}>${H(l)}</option>`).join("")}</select>`;
    const agents = (d.agents || []).length ? `<div class="mcph-agents">${d.agents.map((a) => `<label class="mcph-agent"><input type="checkbox" data-mcph-agent="${H(a.id)}" data-n="${H(s.name)}" ${(s.agents || []).includes(a.id) ? "checked" : ""}><span>${H(a.name || a.id)}</span></label>`).join("")}</div>`
      : `<p class="mcph-muted">${L("Мемесов пока нет — создайте их в боковой панели чата.", "No memes yet — create one in the chat sidebar.")}</p>`;
    const env = (s.env || []).map((e) => `<label class="mcph-field"><span>${H(e.key)}${e.secret ? ` ${ico("lock")}` : ""}</span>
      <input class="pinput compact sx-mono" type="${e.secret ? "password" : "text"}" data-mcph-env="${H(e.key)}" data-n="${H(s.name)}" value="${e.secret ? "" : H(e.value)}" autocomplete="off" spellcheck="false" placeholder="${e.secret ? (e.set ? L("сохранено — оставьте пустым, чтобы не менять", "saved — leave empty to keep") : L("не задано", "not set")) : ""}"></label>`).join("");
    const tools = s.tools.length ? `<div class="mcph-tools">${s.tools.map((t) => `<div class="mcph-tool">
        <div><code>${H(t.name)}</code>${t.read_only ? chip(L("чтение", "read"), "ro") : chip(L("запись", "write"), "warn")}<small>${H(t.description || "")}</small></div>
        <select class="pinput compact" data-mcph-rule="${H(t.name)}" data-n="${H(s.name)}">${[["", ({ allow: L("Авто: сразу", "Auto: runs"), confirm: L("Авто: спросить", "Auto: asks"), deny: L("Авто: запрещён", "Auto: blocked") }[t.policy] || L("Авто", "Auto"))], ["allow", L("разрешить", "allow")], ["confirm", L("спрашивать", "ask")], ["deny", L("запретить", "block")]].map(([v, l]) => `<option value="${v}" ${t.rule === v ? "selected" : ""}>${H(l)}</option>`).join("")}</select>
      </div>`).join("")}</div>${s.cached ? `<p class="mcph-muted">${L("Список с последней проверки — сервер сейчас не запущен.", "The list is from the last check — the server is not running now.")}</p>` : ""}`
      : `<p class="mcph-muted">${L("Нажмите «Проверить», чтобы увидеть инструменты.", "Press «Check» to see the tools.")}</p>`;
    const resources = (s.resources || []).length ? `<div class="mcph-res">${s.resources.slice(0, 30).map((r) => `<code title="${H(r.description || "")}">@mcp:${H(s.name)}/${H(r.uri)}</code>`).join("")}</div>` : "";
    return `<div class="mcph-body">
      <section><h4>${L("Разрешения", "Permissions")}</h4>
        ${sw(`data-mcph-flag="read_only" data-n="${H(s.name)}"`, s.read_only, L("Только чтение — инструменты записи запрещены", "Read-only — write tools are blocked"))}
        <label class="mcph-sw"><span>${L("Спрашивать перед вызовом", "Ask before calling")}</span>${confirmSel}</label></section>
      <section><h4>${L("Где доступен", "Where it is available")}</h4>
        <p class="mcph-muted">${L("В Work и MemuCode — всегда, когда сервер включён.", "In Work and MemuCode — always while the server is on.")}</p>
        ${sw(`data-mcph-flag="chat" data-n="${H(s.name)}"`, s.chat, L("Основной чат", "Main chat"))}
        ${sw(`data-mcph-flag="quick" data-n="${H(s.name)}"`, s.quick, L("Быстрый доступ (Alt+Space) — только инструменты без подтверждения", "Quick Access (Alt+Space) — only tools that need no approval"))}
        <div class="mcph-sublabel">${L("Мемесы", "Мемесы (agents)")}</div>${agents}</section>
      ${env ? `<section><h4>${L("Переменные окружения", "Environment")}</h4><div class="mcph-env">${env}</div>
        <div class="mcph-row-actions"><button type="button" class="pbtn sx-btn-sm" data-mcph="save-env" data-n="${H(s.name)}">${L("Сохранить", "Save")}</button>
        <small>${ico("lock")}${L("Секреты хранятся в защищённом хранилище Windows (DPAPI) и не показываются.", "Secrets live in the protected Windows store (DPAPI) and are never shown.")}</small></div></section>` : ""}
      <section><h4>${L("Инструменты", "Tools")}</h4>${tools}${resources ? `<h4>${L("Ресурсы — упоминайте в чате", "Resources — mention them in the chat")}</h4>${resources}` : ""}</section>
      ${(s.logs || []).length ? `<details class="mcph-logs"><summary>${L("Журнал", "Log")}</summary><pre>${H(s.logs.join("\n"))}</pre></details>` : ""}
      <div class="mcph-row-actions end"><button type="button" class="pbtn ghost sx-btn-sm danger" data-mcph="remove" data-n="${H(s.name)}">${ico("trash")}${L("Удалить сервер", "Remove the server")}</button></div>
    </div>`;
  }

  function catalogHtml() {
    const d = st.data;
    const q = st.filter.toLowerCase();
    const items = (d.catalog || []).filter((c) => !q || (c.title + " " + c.ru + " " + c.en + " " + c.cat).toLowerCase().includes(q));
    const order = Object.keys(d.cats || {});
    return `<div class="mcph-cat-head"><h3>${L("Каталог", "Catalogue")}</h3><input class="pinput compact" id="mcph-filter" value="${H(st.filter)}" placeholder="${L("Найти сервер…", "Find a server…")}" autocomplete="off"></div>
      <div class="mcph-grid">${items.sort((a, b) => order.indexOf(a.cat) - order.indexOf(b.cat)).map((c) => `
        <div class="mcph-item${st.install === c.id ? " on" : ""}">
          <div class="mcph-item-top"><span class="mcph-mono" style="--c:${H(c.color)}">${H(c.title.slice(0, 1))}</span>
            <div><b>${H(c.title)}</b><small>${H((d.cats[c.cat] || [c.cat, c.cat])[isEn() ? 1 : 0])} · ${H(c.type === "stdio" ? c.runner : (c.type === "sse" ? "SSE" : "HTTP"))}</small></div>
            <button type="button" class="pbtn ghost sx-btn-sm" data-mcph="install-open" data-id="${H(c.id)}">${st.install === c.id ? L("Свернуть", "Close") : L("Подключить", "Connect")}</button></div>
          <p>${H(isEn() ? c.en : c.ru)}</p>
          ${c.runner_missing ? `<p class="mcph-warn">${L(`Нужен «${c.runner}» — установите ${c.runner === "uvx" ? "uv (astral.sh/uv)" : "Node.js"}.`, `Needs “${c.runner}” — install ${c.runner === "uvx" ? "uv (astral.sh/uv)" : "Node.js"}.`)}</p>` : ""}
          ${st.install === c.id ? installForm(c) : ""}
        </div>`).join("")}</div>`;
  }

  function installForm(c) {
    const fields = (c.fields || []).map((f) => `<label class="mcph-field${(c.fields.length === 1 || String(f.placeholder || "").length > 28) ? " wide" : ""}"><span>${H(isEn() ? f.en : f.ru)}${f.required ? " *" : ""}${f.secret ? ` ${ico("lock")}` : ""}</span>
      <input class="pinput compact sx-mono" data-mcph-inst="${H(f.key)}" type="${f.secret ? "password" : "text"}" value="${H(f.default || "")}" placeholder="${H(f.placeholder || "")}" autocomplete="off" spellcheck="false"></label>`).join("");
    return `<div class="mcph-install" data-mcph-form="${H(c.id)}">
      ${fields || `<p class="mcph-muted">${L("Настраивать нечего.", "Nothing to configure.")}</p>`}
      ${sw('data-mcph-inst-flag="read_only"', true, c.has_read_only ? L("Только чтение (рекомендуется)", "Read-only (recommended)") : L("Только чтение — запретить инструменты записи", "Read-only — block write tools"))}
      ${sw('data-mcph-inst-flag="chat"', false, L("Сразу доступен в основном чате", "Available in the main chat right away"))}
      <div class="mcph-row-actions"><button type="button" class="pbtn sx-btn-sm" data-mcph="install" data-id="${H(c.id)}">${L("Установить и проверить", "Install and check")}</button>
        <a class="mcph-doc" href="${H(c.docs)}" target="_blank" rel="noopener">${ico("ext")}${L("Документация", "Docs")}</a></div>
    </div>`;
  }

  function customHtml() {
    return kit().more({ id: "mcph-custom", title: L("Свой сервер", "Custom server"), hint: L("команда (stdio) или адрес (HTTP / SSE)", "a command (stdio) or a URL (HTTP / SSE)"), body: `<div class="mcph-custom">
      <label class="mcph-field"><span>${L("Имя", "Name")}</span><input class="pinput compact" id="mcph-c-name" placeholder="db-duty" autocomplete="off"></label>
      <label class="mcph-field"><span>${L("Транспорт", "Transport")}</span><select class="pinput compact" id="mcph-c-type"><option value="stdio">stdio</option><option value="http">Streamable HTTP</option><option value="sse">SSE</option></select></label>
      <label class="mcph-field wide"><span>${L("Команда или адрес", "Command or URL")}</span><input class="pinput compact sx-mono" id="mcph-c-cmd" placeholder="npx -y @modelcontextprotocol/server-memory" spellcheck="false" autocomplete="off"></label>
      <label class="mcph-field wide"><span>${L("Токен (для HTTP, необязательно)", "Token (HTTP, optional)")} ${ico("lock")}</span><input class="pinput compact sx-mono" id="mcph-c-token" type="password" autocomplete="off"></label>
      <div class="mcph-row-actions"><button type="button" class="pbtn sx-btn-sm" data-mcph="custom">${L("Добавить и проверить", "Add and check")}</button></div></div>` });
  }

  function render() {
    const root = document.getElementById("mcph-root");
    const value = document.getElementById("mcph-row-value");
    const d = st.data;
    if (value && d) value.textContent = d.servers.length ? L(`${d.servers.length} подключено`, `${d.servers.length} connected`) : L("Не подключено", "Not connected");
    if (!root || !d) return;
    const K = kit();
    root.innerHTML = (d.safe_mode ? K.note(L("Безопасный режим: серверы MCP не запускаются.", "Safe mode: MCP servers are not started."), "warn") : "")
      + (d.error ? K.note(H(d.error), "danger") : "")
      + `<div class="mcph-list">${d.servers.length ? d.servers.map(serverCard).join("") : `<div class="mcph-empty">${ico("plug")}<b>${L("Серверов пока нет", "No servers yet")}</b><span>${L("Выберите сервер в каталоге ниже — он появится здесь.", "Pick a server in the catalogue below — it will appear here.")}</span></div>`}</div>`
      + catalogHtml() + customHtml()
      + `<p class="mcph-foot">${ico("lock")}${L("Опасные вызовы подтверждаются; секреты не попадают в журналы. В чате ресурс упоминается так: @mcp:сервер/ресурс.", "Risky calls are confirmed; secrets never reach the logs. Mention a resource in the chat as @mcp:server/resource.")}</p>`;
    bind(root);
  }

  const server = (name) => (st.data.servers || []).find((s) => s.name === name);
  async function patch(name, body, reload = false) {
    try { await call("/api/mcp/server/" + encodeURIComponent(name), { method: "POST", body }); }
    catch (e) { toastMsg(e.message, "err"); }
    st.scope.at = 0;
    if (reload) await load();
  }
  function toastMsg(text, type = "ok") { try { toast(text, type); } catch (e) {  } }

  async function check(name) {
    st.checking[name] = true; render();
    let r;
    try { r = await call("/api/mcp/server/" + encodeURIComponent(name) + "/check?lang=" + (isEn() ? "en" : "ru"), { method: "POST", body: {} }); }
    catch (e) { r = { ok: false, error: e.message }; }
    delete st.checking[name];
    await load();
    const s = server(name);
    if (s) s._check = r;
    render();
  }

  function bind(root) {
    root.querySelectorAll("[data-mcph]").forEach((b) => b.onclick = async () => {
      const act = b.dataset.mcph, name = b.dataset.n;
      if (act === "check") return check(name);
      if (act === "toggle-open") { st.open[name] = !st.open[name]; return render(); }
      if (act === "install-open") { st.install = st.install === b.dataset.id ? "" : b.dataset.id; return render(); }
      if (act === "remove") {
        const ok = typeof askConfirm === "function" ? await askConfirm(L("Удалить сервер?", "Remove the server?"), L(`«${name}» и его сохранённые секреты будут удалены.`, `“${name}” and its saved secrets will be removed.`), L("Удалить", "Remove")) : confirm(name);
        if (!ok) return;
        await call("/api/mcp/server/" + encodeURIComponent(name), { method: "DELETE" });
        st.scope.at = 0;
        return load();
      }
      if (act === "save-env") {
        const env = {};
        root.querySelectorAll(`[data-mcph-env][data-n="${CSS.escape(name)}"]`).forEach((i) => { if (i.type !== "password" || i.value) env[i.dataset.mcphEnv] = i.value; });
        await patch(name, { env }, true);
        return toastMsg(L("Сохранено", "Saved"));
      }
      if (act === "install") {
        const form = root.querySelector(`[data-mcph-form="${CSS.escape(b.dataset.id)}"]`);
        const values = {};
        form.querySelectorAll("[data-mcph-inst]").forEach((i) => values[i.dataset.mcphInst] = i.value);
        const flag = (k) => !!form.querySelector(`[data-mcph-inst-flag="${k}"]`)?.checked;
        b.disabled = true;
        try {
          const r = await call("/api/mcp/install", { method: "POST", body: { catalog_id: b.dataset.id, values, read_only: flag("read_only"), chat: flag("chat"), lang: isEn() ? "en" : "ru" } });
          st.install = ""; st.open[r.name] = true;
          await check(r.name);
        } catch (e) { b.disabled = false; toastMsg(e.message, "err"); }
        return;
      }
      if (act === "custom") {
        const type = root.querySelector("#mcph-c-type").value, cmd = root.querySelector("#mcph-c-cmd").value.trim();
        try {
          const r = await call("/api/mcp/custom", { method: "POST", body: { name: root.querySelector("#mcph-c-name").value, type,
            [type === "stdio" ? "command" : "url"]: cmd, token: root.querySelector("#mcph-c-token").value, lang: isEn() ? "en" : "ru" } });
          st.open[r.name] = true;
          await check(r.name);
        } catch (e) { toastMsg(e.message, "err"); }
      }
    });
    root.querySelectorAll("[data-mcph-on]").forEach((i) => i.onchange = () => patch(i.dataset.mcphOn, { enabled: i.checked }, true));
    root.querySelectorAll("[data-mcph-flag]").forEach((i) => i.onchange = () => { const s = server(i.dataset.n); if (s) s[i.dataset.mcphFlag] = i.checked; patch(i.dataset.n, { [i.dataset.mcphFlag]: i.checked }, i.dataset.mcphFlag === "read_only"); render(); });
    root.querySelectorAll("[data-mcph-set]").forEach((i) => i.onchange = () => patch(i.dataset.n, { [i.dataset.mcphSet]: i.value }, true));
    root.querySelectorAll("[data-mcph-rule]").forEach((i) => i.onchange = () => patch(i.dataset.n, { tool_rules: { [i.dataset.mcphRule]: i.value || null } }, true));
    root.querySelectorAll("[data-mcph-agent]").forEach((i) => i.onchange = () => {
      const s = server(i.dataset.n); if (!s) return;
      const set = new Set(s.agents || []);
      if (i.checked) set.add(i.dataset.mcphAgent); else set.delete(i.dataset.mcphAgent);
      s.agents = [...set];
      patch(s.name, { agents: s.agents });
    });
    const f = root.querySelector("#mcph-filter");
    if (f) f.oninput = () => { st.filter = f.value; const pos = f.selectionStart; render(); const n = document.getElementById("mcph-filter"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } };
  }

  async function scope(chatState) {
    const agent = chatState && chatState.meme ? String(chatState.meme.id || "") : "";
    const key = "chat|" + agent;
    if (st.scope.key === key && Date.now() - st.scope.at < 20000) return st.scope.rows;
    try { st.scope = { key, at: Date.now(), rows: (await call("/api/mcp/scope?scope=chat&agent=" + encodeURIComponent(agent))).servers || [] }; }
    catch (e) { st.scope = { key, at: Date.now(), rows: [] }; }
    return st.scope.rows;
  }
  async function promptBlock(chatState) {
    const rows = (await scope(chatState)).filter((s) => s.tools.length || s.resources.length);
    if (!rows.length) return "";
    const lines = ["<mcp_tools>", "External MCP servers are connected to this chat. Call a tool with the hidden memuai-tool block using the name mcp_<server>_<tool>, e.g.:",
      '```memuai-tool\n[{"tool":"mcp_' + rows[0].name + "_" + ((rows[0].tools[0] || {}).name || "tool") + '","args":{}}]\n```',
      "Tools marked (asks) are shown to the user for approval by the app. Read-only servers refuse writes — do not try them. Report only real tool output."];
    rows.forEach((s) => {
      lines.push(`Server ${s.name}${s.read_only ? " (read-only)" : ""}${s.description ? " — " + s.description.slice(0, 160) : ""}:`);
      s.tools.slice(0, 40).forEach((t) => lines.push(`  • mcp_${s.name}_${t.name}${t.policy === "confirm" ? " (asks)" : ""}${t.description ? " — " + t.description.slice(0, 200) : ""}`));
      if (s.resources.length) lines.push("  resources the user can attach with @mcp:" + s.name + "/<uri>: " + s.resources.slice(0, 10).map((r) => r.uri).join(", "));
    });
    lines.push("</mcp_tools>");
    return "\n\n" + lines.join("\n");
  }
  function allowed(name, chatState) {
    const agent = chatState && chatState.meme ? String(chatState.meme.id || "") : "";
    if (st.scope.key !== "chat|" + agent) return false;
    const low = String(name).toLowerCase();
    return st.scope.rows.some((s) => low.startsWith("mcp_" + s.name.toLowerCase() + "_"));
  }

  async function run(callRow, ctx = {}) {
    const name = String(callRow.tool || "");
    const body = { tool: name, args: callRow.args || {}, scope: ctx.scope || "chat", agent: ctx.agent || "", access: ctx.access || "", lang: isEn() ? "en" : "ru" };
    let r = await call("/api/mcp/call", { method: "POST", body, signal: ctx.signal });
    if (r && r.needs_confirm) {
      const ok = typeof askConfirm === "function" && await askConfirm(L("Разрешить вызов MCP?", "Allow this MCP call?"),
        `${r.server} → ${r.tool}\n${r.args_preview || "{}"}`, L("Разрешить", "Allow"));
      if (!ok) return { tool: name, error: L("Пользователь не разрешил этот вызов", "The user did not allow this call") };
      r = await call("/api/mcp/call", { method: "POST", body: { ...body, confirmed: true }, signal: ctx.signal });
    }
    return { tool: name, ...r };
  }

  window.MemuMcpHub = { listHtml, subHtml, mount, load, promptBlock, allowed, run };
})();
