

"use strict";

window.MemuCliTools = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = { models: [], providers: new Map(), status: null, codexSearch: "", codexPicked: new Set(), codexDefault: "" };

  const CLAUDE_SLOTS = [
    ["model", () => L("Основная модель (ANTHROPIC_MODEL)", "Primary model (ANTHROPIC_MODEL)")],
    ["opus", () => L("Слот Opus", "Opus slot")],
    ["sonnet", () => L("Слот Sonnet", "Sonnet slot")],
    ["haiku", () => L("Слот Haiku", "Haiku slot")],
    ["fable", () => L("Слот Fable", "Fable slot")],
    ["subagent", () => L("Субагенты (необязательно)", "Subagents (optional)")],
  ];

  function active() { return $("#tab-settings")?.classList.contains("active"); }

  async function load() {
    const root = $("#cli-tools-root");
    if (!root) return;
    try {
      const [models, status] = await Promise.all([api("/api/admin/models"), api("/api/admin/cli/status")]);
      const enabled = (models.models || []).filter((m) => m.enabled !== false && (m.kind || m.caps?.kind || "chat") === "chat");
      S.models = enabled.map((m) => m.id).sort();
      S.providers = new Map(enabled.map((m) => [m.id, m.provider]));
      S.status = status;
    } catch (error) {
      root.innerHTML = `<div class="warn-strip">${esc(errorOf(error))}</div>`;
      return;
    }
    if (!root.dataset.ready) {
      shell(root);
      root.dataset.ready = "1";
    }
    fillClaudeSelects();
    renderClaudeStatus();
    renderCodexList();
    renderCodexStatus();
  }

  function errorOf(error) { return error && error.message ? error.message : String(error || ""); }

  function shell(root) {
    root.innerHTML = `
      <div class="grid-2" style="align-items:start">
        <div class="card" id="cli-claude-card">
          <h3>Claude Code</h3>
          <p class="sub">${esc(L(
            "Прописывает адрес и ключ прокси в ~/.claude/settings.json и назначает модели прокси на слоты Claude Code. Требуется перезапустить Claude Code после изменений.",
            "Writes the proxy's address and key into ~/.claude/settings.json and maps proxy models onto Claude Code's slots. Restart Claude Code after applying."))}</p>
          <div class="cli-tool-note" id="cli-claude-limit">${esc(L(
            "Через Proxy агентные инструменты работают с моделью «Свой API», если её сервер поддерживает function calling. Свой API напрямую должен поддерживать Anthropic Messages с инструментами. Веб-провайдеры Proxy доступны для чата без инструментов. После применения перезапустите Claude Code.",
            "Through the Proxy, agent tools work with a Custom API model whose server supports function calling. A direct API must support Anthropic Messages with tools. Proxy web providers support chat without tools. Restart Claude Code after applying."))}</div>
          <div id="cli-claude-status" class="cli-status"></div>
          <div class="field"><label for="cli-claude-source">${esc(L("Подключение", "Connection"))}</label>
            <select id="cli-claude-source"><option value="proxy">MemuAI Proxy</option>
              <option value="direct">${esc(L("Свой API напрямую", "Own API directly"))}</option></select></div>
          <div class="grid-2" id="cli-claude-fields"></div>
          <div id="cli-claude-direct" style="display:none">
            <p class="sub">${esc(L("Адрес должен поддерживать Anthropic Messages. Ключ и модель задаются здесь независимо от Proxy; при повторной установке введите ключ ещё раз.",
              "The endpoint must support Anthropic Messages. This key and model are independent of the Proxy; enter the key again when reinstalling."))}</p>
            <div class="field"><label for="cli-claude-url">Base URL</label><input id="cli-claude-url" type="url" placeholder="https://api.example.com/v1"></div>
            <div class="field"><label for="cli-claude-key">API key</label><input id="cli-claude-key" type="password" autocomplete="off"></div>
            <div class="field"><label for="cli-claude-direct-model">${esc(L("ID модели", "Model ID"))}</label><input id="cli-claude-direct-model" type="text" autocomplete="off"></div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn primary small" id="cli-claude-apply" type="button">${esc(L("Установить", "Install"))}</button>
            <button class="btn small danger" id="cli-claude-reset" type="button">${esc(L("Сбросить", "Reset"))}</button>
          </div>
        </div>

        <div class="card" id="cli-codex-card">
          <h3>Codex CLI</h3>
          <p class="sub">${esc(L(
            "Добавляет провайдера memuai и профиль на каждую выбранную модель в ~/.codex/config.toml — переключение через codex --profile <имя>.",
            "Adds a memuai provider and one profile per selected model to ~/.codex/config.toml — switch with codex --profile <name>."))}</p>
          <div class="cli-tool-note">${esc(L(
            "Через Proxy действия Codex требуют модели «Свой API» с function calling. Свой API напрямую должен поддерживать OpenAI Responses с инструментами. Веб-провайдеры Proxy могут отвечать в чате без инструментов. После применения перезапустите Codex CLI.",
            "Through the Proxy, Codex actions require a Custom API model with function calling. A direct API must support OpenAI Responses with tools. Proxy web providers can chat without tools. Restart Codex CLI after applying."))}</div>
          <div id="cli-codex-status" class="cli-status"></div>
          <div class="field"><label for="cli-codex-source">${esc(L("Подключение", "Connection"))}</label>
            <select id="cli-codex-source"><option value="proxy">MemuAI Proxy</option>
              <option value="direct">${esc(L("Свой API напрямую", "Own API directly"))}</option></select></div>
          <div id="cli-codex-proxy-fields">
          <div class="field"><input type="search" id="cli-codex-search" placeholder="${esc(L("Поиск модели…", "Search models…"))}"></div>
          <div class="cli-model-list" id="cli-codex-list"></div>
          <div class="field"><label>${esc(L("Модель по умолчанию (top-level model/model_provider, если это безопасно)",
            "Default model (top-level model/model_provider, when it's safe)"))}</label>
            <select id="cli-codex-default"></select></div>
          </div>
          <div id="cli-codex-direct" style="display:none">
            <p class="sub">${esc(L("Адрес должен поддерживать OpenAI Responses. Ключ и модель задаются здесь независимо от Proxy; при повторной установке введите ключ ещё раз.",
              "The endpoint must support OpenAI Responses. This key and model are independent of the Proxy; enter the key again when reinstalling."))}</p>
            <div class="field"><label for="cli-codex-url">Base URL</label><input id="cli-codex-url" type="url" placeholder="https://api.example.com/v1"></div>
            <div class="field"><label for="cli-codex-key">API key</label><input id="cli-codex-key" type="password" autocomplete="off"></div>
            <div class="field"><label for="cli-codex-model">${esc(L("ID модели", "Model ID"))}</label><input id="cli-codex-model" type="text" autocomplete="off"></div>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn primary small" id="cli-codex-apply" type="button">${esc(L("Установить", "Install"))}</button>
            <button class="btn small danger" id="cli-codex-reset" type="button">${esc(L("Сбросить", "Reset"))}</button>
          </div>
        </div>
      </div>`;

    const fields = $("#cli-claude-fields");
    fields.innerHTML = CLAUDE_SLOTS.map(([slot, label]) => `
      <div class="field"><label for="cli-claude-${slot}">${esc(label())}</label>
        <select id="cli-claude-${slot}" data-slot="${slot}"><option value="">—</option></select></div>`).join("");

    $("#cli-claude-apply").onclick = () => claudeApply();
    $("#cli-claude-reset").onclick = () => claudeReset();
    $("#cli-codex-apply").onclick = () => codexApply();
    $("#cli-codex-reset").onclick = () => codexReset();
    $("#cli-codex-search").oninput = (e) => { S.codexSearch = e.target.value.trim().toLowerCase(); renderCodexList(); };
    $("#cli-claude-source").onchange = () => toggleSource("claude");
    $("#cli-codex-source").onchange = () => toggleSource("codex");
    const claude = S.status?.claude || {};
    const codex = S.status?.codex || {};
    $("#cli-claude-source").value = claude.connection === "direct" ? "direct" : "proxy";
    $("#cli-claude-url").value = claude.connection === "direct" ? claude.env?.ANTHROPIC_BASE_URL || "" : "";
    $("#cli-claude-direct-model").value = claude.connection === "direct" ? claude.env?.ANTHROPIC_MODEL || "" : "";
    $("#cli-codex-source").value = codex.connection === "direct" ? "direct" : "proxy";
    $("#cli-codex-url").value = codex.connection === "direct" ? codex.base_url || "" : "";
    $("#cli-codex-model").value = codex.connection === "direct" ? codex.profiles?.[0]?.model || "" : "";
    toggleSource("claude");
    toggleSource("codex");
  }

  function toggleSource(client) {
    const direct = $(`#cli-${client}-source`)?.value === "direct";
    const proxyFields = $(`#cli-${client}-${client === "claude" ? "fields" : "proxy-fields"}`);
    if (proxyFields) proxyFields.style.display = direct ? "none" : "";
    const directFields = $(`#cli-${client}-direct`);
    if (directFields) directFields.style.display = direct ? "" : "none";
  }

  function fillClaudeSelects() {
    const applied = (S.status && S.status.claude && S.status.claude.env) || {};
    const bySlot = { model: applied.ANTHROPIC_MODEL, opus: applied.ANTHROPIC_DEFAULT_OPUS_MODEL,
      sonnet: applied.ANTHROPIC_DEFAULT_SONNET_MODEL, haiku: applied.ANTHROPIC_DEFAULT_HAIKU_MODEL,
      fable: applied.ANTHROPIC_DEFAULT_FABLE_MODEL, subagent: applied.CLAUDE_CODE_SUBAGENT_MODEL };
    for (const [slot] of CLAUDE_SLOTS) {
      const sel = $(`#cli-claude-${slot}`);
      if (!sel) continue;
      const current = sel.value || bySlot[slot] || "";
      sel.innerHTML = `<option value="">—</option>` + S.models.map((id) =>
        `<option value="${esc(id)}">${esc(id)} · ${esc(S.providers.get(id) === "custom"
          ? L("инструменты*", "tools*") : L("без инструментов", "no tools"))}</option>`).join("");
      if (current && S.models.includes(current)) sel.value = current;
    }
  }

  function renderClaudeStatus() {
    const box = $("#cli-claude-status");
    if (!box) return;
    const st = (S.status && S.status.claude) || {};
    if (!st.installed) {
      box.innerHTML = `<span class="dim">${esc(L("Не установлено", "Not installed"))}</span>`;
      return;
    }
    const env = st.env || {};
    const rows = Object.entries(env).filter(([k]) => k !== "ANTHROPIC_AUTH_TOKEN")
      .map(([k, v]) => `<div><code>${esc(k)}</code> = <code>${esc(v)}</code></div>`).join("");
    box.innerHTML = `<div class="chip ok" style="margin-bottom:6px">${esc(L("Установлено", "Installed"))}</div>
      <div class="mono" style="font-size:12px">${rows}</div>
      <div class="help">${esc(st.path || "")}</div>`;
  }

  function renderCodexList() {
    const box = $("#cli-codex-list");
    if (!box) return;
    const installed = new Set(((S.status && S.status.codex && S.status.codex.profiles) || []).map((p) => p.model));
    if (S.codexPicked.size === 0 && installed.size) installed.forEach((m) => S.codexPicked.add(m));
    const filtered = S.models.filter((id) => !S.codexSearch || id.toLowerCase().includes(S.codexSearch));
    box.innerHTML = filtered.length ? filtered.map((id) => `
      <label class="pick-row"><input type="checkbox" data-model="${esc(id)}" ${S.codexPicked.has(id) ? "checked" : ""}>
        <span class="mono">${esc(id)}</span><span class="cli-tool-badge ${S.providers.get(id) === "custom" ? "capable" : ""}">${esc(S.providers.get(id) === "custom"
          ? L("инструменты*", "tools*") : L("без инструментов", "no tools"))}</span></label>`).join("")
      : `<div class="empty">${esc(L("Ничего не найдено", "No matches"))}</div>`;
    $$('#cli-codex-list input[type="checkbox"]').forEach((cb) => {
      cb.onchange = () => {
        if (cb.checked) S.codexPicked.add(cb.dataset.model); else S.codexPicked.delete(cb.dataset.model);
        fillCodexDefault();
      };
    });
    fillCodexDefault();
  }

  function fillCodexDefault() {
    const sel = $("#cli-codex-default");
    if (!sel) return;
    const picked = Array.from(S.codexPicked);
    const current = sel.value;
    sel.innerHTML = picked.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("")
      || `<option value="">—</option>`;
    if (picked.includes(current)) sel.value = current;
    else if (picked.includes(S.codexDefault)) sel.value = S.codexDefault;
  }

  function renderCodexStatus() {
    const box = $("#cli-codex-status");
    if (!box) return;
    const st = (S.status && S.status.codex) || {};
    if (!st.installed) {
      box.innerHTML = `<span class="dim">${esc(L("Не установлено", "Not installed"))}</span>`;
      return;
    }
    const names = (st.profiles || []).map((p) => `<code>${esc(p.name)}</code> → <code>${esc(p.model)}</code>`).join("<br>");
    box.innerHTML = `<div class="chip ok" style="margin-bottom:6px">${esc(L("Установлено", "Installed"))}</div>
      <div class="mono" style="font-size:12px">${names}</div>
      <div class="help">${esc(st.path || "")}${st.default_set ? "" : " · " + esc(L(
        "top-level model уже задан вручную — доступно только через --profile", "top-level model is already set by hand — only reachable via --profile"))}</div>`;
  }

  async function claudeApply() {
    const selection = {};
    const direct = $("#cli-claude-source")?.value === "direct";
    if (direct) {
      const model = $("#cli-claude-direct-model")?.value.trim();
      if (model) selection.model = model;
    } else {
      for (const [slot] of CLAUDE_SLOTS) {
        const value = $(`#cli-claude-${slot}`)?.value || "";
        if (value) selection[slot] = value;
      }
    }
    if (!Object.keys(selection).length) {
      toast(L("Выберите хотя бы одну модель", "Pick at least one model"), "warn");
      return;
    }
    if (direct && S.status?.claude?.connection === "direct" && S.status.claude.api_key_set
        && !$("#cli-claude-key").value) {
      toast(L("Введите ключ своего API повторно", "Enter your API key again"), "warn");
      return;
    }
    try {
      const body = direct ? { selection, connection: "direct", base_url: $("#cli-claude-url").value.trim(),
        api_key: $("#cli-claude-key").value } : { selection };
      const result = await api("/api/admin/cli/claude/apply", { method: "POST", body });
      toast(L(`Готово: ${result.path}. Перезапустите Claude Code.`, `Done: ${result.path}. Restart Claude Code.`));
      await load();
    } catch (error) { toast(errorOf(error), "warn"); }
  }

  async function claudeReset() {
    try {
      const result = await api("/api/admin/cli/claude/reset", { method: "POST", body: {} });
      toast(result.already ? L("Уже не установлено", "Already not installed") : L("Настройки Claude Code возвращены как было", "Claude Code settings restored"));
      await load();
    } catch (error) { toast(errorOf(error), "warn"); }
  }

  async function codexApply() {
    const direct = $("#cli-codex-source")?.value === "direct";
    const directModel = $("#cli-codex-model")?.value.trim() || "";
    const profiles = direct ? (directModel ? [{ model: directModel, name: directModel }] : [])
      : Array.from(S.codexPicked).map((model) => ({ model, name: model }));
    if (!profiles.length) {
      toast(L("Выберите хотя бы одну модель", "Pick at least one model"), "warn");
      return;
    }
    if (direct && S.status?.codex?.connection === "direct" && S.status.codex.api_key_set
        && !$("#cli-codex-key").value) {
      toast(L("Введите ключ своего API повторно", "Enter your API key again"), "warn");
      return;
    }
    const def = direct ? directModel : $("#cli-codex-default")?.value || "";
    try {
      const body = direct ? { profiles, default: def, connection: "direct",
        base_url: $("#cli-codex-url").value.trim(), api_key: $("#cli-codex-key").value }
        : { profiles, default: def };
      const result = await api("/api/admin/cli/codex/apply", { method: "POST", body });
      const note = LANG === "en" ? result.note_en : result.note;
      toast(L(`Готово: ${result.path}. Перезапустите Codex CLI.`, `Done: ${result.path}. Restart Codex CLI.`) + (note ? " " + note : ""));
      await load();
    } catch (error) { toast(errorOf(error), "warn"); }
  }

  async function codexReset() {
    try {
      const result = await api("/api/admin/cli/codex/reset", { method: "POST", body: {} });
      toast(result.already ? L("Уже не установлено", "Already not installed") : L("config.toml возвращён как было", "config.toml restored"));
      S.codexPicked = new Set();
      await load();
    } catch (error) { toast(errorOf(error), "warn"); }
  }

  function relabel() {
    const root = $("#cli-tools-root");
    if (root) { root.dataset.ready = ""; load(); }
  }

  return { load, relabel, active };
})();
