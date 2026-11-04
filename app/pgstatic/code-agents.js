"use strict";
/* MemuCode agents in the MemuAI window — the same agents as MemuCode CLI.
   The «Агенты» tab of the MemuCode dock lists the built-in, personal and project agents
   (.md files shared with the CLI), lets the user create and change them, choose the models
   agents run on (with a remark about each) and allow agents in the current chat. */
const MemuCodeAgents = (() => {
  const en = () => typeof PG_LANG !== "undefined" && PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = {
    users: '<path d="M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1"/><circle cx="9.5" cy="7.5" r="3.5"/><path d="M21 19v-1a4 4 0 0 0-3-3.9M15.5 4.2a3.5 3.5 0 0 1 0 6.6"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    play: '<path d="m8 5 11 7-11 7z"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ""}</svg>`;
  const projectId = () => (S.workspaces && S.workspaces.active_id) || "";
  const SOURCE = () => ({ builtin: t("встроенный", "built-in"), user: t("личный", "personal"), project: t("проект", "project") });
  const COLORS = ["cyan", "magenta", "yellow", "green", "blue", "red"];
  const GROUP_LABEL = () => ({
    read: [t("Чтение и поиск", "Read and search"), t("файлы, поиск, структура, git", "files, search, outline, git")],
    edit: [t("Правка файлов", "Edit files"), t("создание и изменение файлов", "create and change files")],
    run: [t("Команды", "Commands"), t("тесты, сборка, скрипты в песочнице", "tests, builds, scripts in the sandbox")],
    web: [t("Интернет", "Web"), t("поиск и чтение страниц", "search and read pages")],
  });
  let filter = "";
  let host = null;

  const chatModels = () => (S.models || []).filter((m) => typeof chatModelAllowed !== "function" || chatModelAllowed(m.id));
  const modelName = (id) => (typeof modelDisplayName === "function" ? modelDisplayName(id) : id) || id;

  async function rows(force) {
    if (typeof MemuAgents === "undefined") return [];
    return MemuAgents.loadDefs(projectId(), force);
  }

  function chatAllowsAgents() { return (S.chatCaps || {}).agents === "always"; }

  function settingsHtml() {
    const models = (pref("agent_models", []) || []).filter(Boolean);
    const notes = pref("agent_model_notes", {}) || {};
    const count = Math.max(1, Math.min(4, +pref("agent_count", 2) || 2));
    const free = chatModels().filter((m) => !models.includes(m.id));
    return `<section class="ca-settings">
      <label class="ca-line">
        <span class="cd-switch"><input type="checkbox" id="ca-chat-on" ${chatAllowsAgents() ? "checked" : ""}><i></i></span>
        <span><b>${esc(t("Агенты в этом чате без просьбы", "Agents in this chat without asking"))}</b>
          <em>${esc(t("Иначе ИИ запускает агентов, только когда вы просите о них в сообщении.", "Otherwise the AI starts agents only when your message asks for them."))}</em></span>
      </label>
      <div class="ca-line ca-field">
        <span><b>${esc(t("Одновременно", "At once"))}</b><em>${esc(t("Сколько агентов работает параллельно", "How many agents run in parallel"))}</em></span>
        <select class="cd-select" id="ca-count" aria-label="${esc(t("Агентов одновременно", "Agents at once"))}">${[1, 2, 3, 4].map((n) => `<option value="${n}" ${n === count ? "selected" : ""}>${n}</option>`).join("")}</select>
      </div>
      <div class="ca-models">
        <div class="ca-models-head"><b>${esc(t("Модели агентов", "Agent models"))}</b>
          <em>${esc(models.length > 1 ? t("ИИ выбирает модель для каждого агента по вашим заметкам", "The AI picks a model for each agent from your remarks")
            : models.length ? t("Все агенты работают на этой модели", "Every agent runs on this model") : t("Агенты работают на модели чата", "Agents run on the chat model"))}</em></div>
        ${models.map((id) => `<div class="ca-model" data-model="${esc(id)}">
          <span class="ca-model-name" title="${esc(id)}">${esc(modelName(id))}</span>
          ${models.length > 1 ? `<input class="cd-filter ca-note" data-note="${esc(id)}" maxlength="300" value="${esc(notes[id] || "")}" placeholder="${esc(t("чем хороша: быстрая, для поиска…", "good at: fast, for search…"))}">` : ""}
          <button type="button" class="cd-btn icon ghost" data-drop-model="${esc(id)}" title="${esc(t("Убрать", "Remove"))}" aria-label="${esc(t("Убрать", "Remove"))}">${svg("close")}</button>
        </div>`).join("")}
        ${models.length < 4 && free.length ? `<select class="cd-select ca-add-model" id="ca-add-model" aria-label="${esc(t("Добавить модель", "Add a model"))}">
          <option value="">${esc(models.length ? t("+ ещё модель", "+ another model") : t("+ выбрать модель для агентов", "+ choose a model for agents"))}</option>
          ${free.map((m) => `<option value="${esc(m.id)}">${esc(modelName(m.id))}</option>`).join("")}</select>` : ""}
      </div>
    </section>`;
  }

  function rowHtml(row) {
    const desc = MemuAgents.describe(row);
    const meta = [MemuAgents.toolsLabel(row)];
    if (row.model && !/^(small|inherit)$/i.test(row.model)) meta.push(MemuAgents.modelKnown(row.model) ? modelName(row.model) : row.model + " " + t("(нет в MemuAI)", "(not in MemuAI)"));
    const source = row.origin === "claude" ? "Claude Code" : (SOURCE()[row.source] || row.source);
    return `<div class="ca-row" data-agent="${esc(row.name)}" tabindex="0" role="button" aria-label="${esc(row.name)}">
      <span class="ca-dot ${COLORS.includes(row.color) ? "c-" + row.color : ""}"></span>
      <span class="ca-main"><span class="ca-title"><b>${esc(row.name)}</b><small>${esc(source)}${row.overrides ? " · " + esc(t("заменяет встроенного", "replaces the built-in")) : ""}</small></span>
        ${desc ? `<em>${esc(desc)}</em>` : ""}<span class="ca-meta">${esc(meta.join(" · "))}</span></span>
      <button type="button" class="cd-btn icon ghost ca-run" data-run="${esc(row.name)}" title="${esc(t("Поручить задачу этому агенту", "Give this agent a task"))}" aria-label="${esc(t("Поручить задачу", "Give a task"))}">${svg("play")}</button>
    </div>`;
  }

  async function render(target) {
    host = target || host;
    const body = host;
    if (!body) return;
    if (!body.querySelector(".ca-list")) body.innerHTML = `<div class="cd-loading" aria-busy="true">${"<i></i>".repeat(5)}</div>`;
    let list = [];
    try { list = await rows(); } catch (error) { list = []; }
    const q = filter.trim().toLowerCase();
    const shown = list.filter((row) => !q || `${row.name} ${row.description} ${MemuAgents.describe(row)}`.toLowerCase().includes(q));
    const dirs = MemuAgents.defsNow().dirs || {};
    body.innerHTML = `<div class="cd-toolbar">
        <input class="cd-filter" id="ca-filter" type="search" placeholder="${esc(t("Найти агента", "Find an agent"))}" value="${esc(filter)}" aria-label="${esc(t("Найти агента", "Find an agent"))}">
        <button type="button" class="cd-btn primary" id="ca-new">${svg("plus")}${esc(t("Новый агент", "New agent"))}</button>
      </div>
      <div class="cd-scroll ca-list">
        ${settingsHtml()}
        <div class="ca-head">${esc(t("Агенты", "Agents"))} <span>${list.length}</span></div>
        ${shown.map(rowHtml).join("") || `<div class="cd-empty"><b>${esc(t("Ничего не найдено", "Nothing found"))}</b></div>`}
        <p class="ca-foot">${esc(t("Агенты общие с MemuCode CLI: файлы .md в папке личных агентов и в .memucode/agents проекта. Попросите в чате, например «запусти агентов: один ищет, другой пишет тесты», — ИИ раздаст части задачи подходящим агентам.",
          "Agents are shared with MemuCode CLI: .md files in the personal agents folder and in the project's .memucode/agents. Ask in the chat, e.g. “start agents: one searches, one writes the tests”, and the AI hands the parts to the fitting agents."))}
          ${dirs.user ? `<br><span class="ca-path" title="${esc(dirs.user)}">${svg("folder")}${esc(dirs.user)}</span>` : ""}</p>
      </div>`;
    bind(body, list);
  }

  function bind(body, list) {
    const input = body.querySelector("#ca-filter");
    input?.addEventListener("input", () => {
      filter = input.value;
      const at = input.selectionStart;
      render().then(() => { const next = body.querySelector("#ca-filter"); if (next) { next.focus(); next.setSelectionRange(at, at); } });
    });
    body.querySelector("#ca-new")?.addEventListener("click", () => openEditor(null));
    body.querySelector("#ca-chat-on")?.addEventListener("change", (event) => {
      if (typeof MemuTools !== "undefined" && MemuTools.setOn) MemuTools.setOn("agents", event.target.checked);
      else { S.chatCaps = S.chatCaps || {}; if (event.target.checked) S.chatCaps.agents = "always"; else delete S.chatCaps.agents; }
      if (S.messages.length && typeof persistChat === "function") persistChat();
      if (typeof renderCaps === "function") renderCaps();
    });
    body.querySelector("#ca-count")?.addEventListener("change", (event) => { savePreferences({ agent_count: +event.target.value }); toast(t("Сохранено", "Saved")); });
    body.querySelector("#ca-add-model")?.addEventListener("change", (event) => {
      const id = event.target.value;
      if (!id) return;
      const models = [...(pref("agent_models", []) || []).filter(Boolean), id].slice(0, 4);
      savePreferences({ agent_models: models });
      render();
    });
    body.querySelectorAll("[data-drop-model]").forEach((button) => button.addEventListener("click", () => {
      savePreferences({ agent_models: (pref("agent_models", []) || []).filter((id) => id !== button.dataset.dropModel) });
      render();
    }));
    body.querySelectorAll("[data-note]").forEach((field) => field.addEventListener("change", () => {
      const notes = { ...(pref("agent_model_notes", {}) || {}) };
      const text = field.value.trim();
      if (text) notes[field.dataset.note] = text; else delete notes[field.dataset.note];
      savePreferences({ agent_model_notes: notes });
      toast(t("Заметка сохранена", "Remark saved"));
    }));
    body.querySelectorAll(".ca-row").forEach((node) => {
      const row = list.find((item) => item.name === node.dataset.agent);
      const open = (event) => { if (event.target.closest(".ca-run")) return; openEditor(row); };
      node.addEventListener("click", open);
      node.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(event); } });
    });
    body.querySelectorAll("[data-run]").forEach((button) => button.addEventListener("click", (event) => {
      event.stopPropagation();
      const field = document.getElementById("input");
      if (!field) return;
      field.value = t(`Запусти агента «${button.dataset.run}»: `, `Start the ${button.dataset.run} agent: `);
      field.dispatchEvent(new Event("input"));
      field.focus();
      field.setSelectionRange(field.value.length, field.value.length);
    }));
  }

  function groupsHtml(row) {
    const groups = row?.tool_groups || { all: true, groups: ["read", "edit", "run", "web"] };
    const on = new Set(groups.all ? ["read", "edit", "run", "web"] : groups.groups || []);
    return Object.entries(GROUP_LABEL()).map(([id, [label, hint]]) => `<label class="ca-check">
      <input type="checkbox" data-group="${id}" ${on.has(id) ? "checked" : ""}>
      <span><b>${esc(label)}</b><em>${esc(hint)}</em></span></label>`).join("");
  }

  function modelOptions(current) {
    const list = chatModels();
    const cliAlias = /^(small|inherit)$/i.test(current || "");
    const known = !current || cliAlias || list.some((m) => m.id === current);
    const aliasLabel = /^small$/i.test(current || "")
      ? t("Лёгкая модель CLI (small)", "CLI small model")
      : t("Как в CLI (inherit)", "Same as CLI (inherit)");
    return `<option value="">${esc(t("Как у остальных агентов", "Same as the other agents"))}</option>` +
      (cliAlias ? `<option value="${esc(current)}" selected>${esc(aliasLabel)}</option>` : "") +
      (known ? "" : `<option value="${esc(current)}" selected>${esc(current)} ${esc(t("(нет в MemuAI)", "(not in MemuAI)"))}</option>`) +
      list.map((m) => `<option value="${esc(m.id)}" ${m.id === current ? "selected" : ""}>${esc(modelName(m.id))}</option>`).join("");
  }

  function openEditor(row) {
    if (typeof panel !== "function") return;
    const creating = !row;
    const copy = row && !row.editable;
    const hasProject = !!projectId();
    const scope = row && row.editable ? row.source : (hasProject && row?.source === "project" ? "project" : "user");
    const title = creating ? t("Новый агент", "New agent") : copy ? t(`Агент «${row.name}»`, `Agent “${row.name}”`) : t(`Агент «${row.name}»`, `Agent “${row.name}”`);
    const lead = creating
      ? t("Свой агент со своей ролью, инструментами и моделью. Он появится и в MemuCode CLI.", "Your own agent with its role, tools and model. It appears in MemuCode CLI too.")
      : row.builtin ? t("Встроенный агент. Сохранение создаст личную копию с тем же именем: она заменит встроенного и в MemuCode CLI.", "A built-in agent. Saving creates a personal copy with the same name: it replaces the built-in one in MemuCode CLI as well.")
      : row.origin === "claude" ? t("Агент из папки Claude Code. Сохранение создаст копию в MemuCode.", "An agent from a Claude Code folder. Saving creates a copy in MemuCode.")
      : t("Изменения сразу видны и в MemuCode CLI.", "Changes show up in MemuCode CLI right away.");
    const color = row?.color && COLORS.includes(row.color) ? row.color : "";
    panel(`<div class="ca-editor">
      <h2>${esc(title)}</h2><p class="sub">${esc(lead)}</p>
      <div class="ca-form">
        <label class="ca-field-col"><span>${esc(t("Имя", "Name"))}</span>
          <input class="pinput" id="ca-name" maxlength="48" spellcheck="false" placeholder="docs-writer" value="${esc(row?.name || "")}"></label>
        <label class="ca-field-col"><span>${esc(t("Когда его звать", "When to use it"))}</span>
          <input class="pinput" id="ca-desc" maxlength="300" placeholder="${esc(t("Пишет и обновляет документацию по коду", "Writes and updates the docs for the code"))}" value="${esc(row ? (row.builtin && !en() ? MemuAgents.describe(row) : row.description) : "")}"></label>
        <label class="ca-field-col"><span>${esc(t("Инструкции агента", "The agent's instructions"))}</span>
          <textarea class="ptexta" id="ca-prompt" rows="7" placeholder="${esc(t("Ты — технический писатель. Читай код, прежде чем описывать его. В отчёте перечисли изменённые файлы.", "You are a technical writer. Read the code before describing it. List the changed files in your report."))}">${esc(row?.prompt || "")}</textarea></label>
        <div class="ca-grid">
          <label class="ca-field-col"><span>${esc(t("Модель", "Model"))}</span><select class="pinput" id="ca-model">${modelOptions(row?.model || "")}</select></label>
          <label class="ca-field-col"><span>${esc(t("Где хранить", "Keep it"))}</span><select class="pinput" id="ca-scope">
            <option value="user" ${scope === "user" ? "selected" : ""}>${esc(t("Личный — во всех проектах", "Personal — in every project"))}</option>
            <option value="project" ${scope === "project" ? "selected" : ""} ${hasProject ? "" : "disabled"}>${esc(t("В проекте — .memucode/agents", "In the project — .memucode/agents"))}</option>
          </select></label>
        </div>
        <div class="ca-field-col"><span>${esc(t("Инструменты", "Tools"))}</span><div class="ca-checks">${groupsHtml(row)}</div>
          <small class="ca-hint">${esc(t("Правка и команды работают, когда у чата есть доступ к ним (режим «Полный доступ»).", "Edits and commands work when the chat has access to them (Full access)."))}</small></div>
        <div class="ca-field-col"><span>${esc(t("Цвет", "Color"))}</span><div class="ca-colors" role="radiogroup" aria-label="${esc(t("Цвет", "Color"))}">
          <label class="ca-color"><input type="radio" name="ca-color" value="" ${color ? "" : "checked"}><i class="ca-dot"></i></label>
          ${COLORS.map((c) => `<label class="ca-color" title="${c}"><input type="radio" name="ca-color" value="${c}" ${color === c ? "checked" : ""}><i class="ca-dot c-${c}"></i></label>`).join("")}
        </div></div>
      </div>
      <p class="ca-error" id="ca-error" hidden></p>
      <div class="row-end">
        ${row && row.editable ? `<button class="pbtn ghost danger" id="ca-delete">${esc(t("Удалить", "Delete"))}</button><span class="ca-grow"></span>` : ""}
        <button class="pbtn ghost" id="ca-cancel">${esc(t("Отмена", "Cancel"))}</button>
        <button class="pbtn" id="ca-save">${esc(copy ? t("Сохранить копию", "Save a copy") : t("Сохранить", "Save"))}</button>
      </div></div>`);
    const $id = (id) => document.getElementById(id);
    const fail = (message) => { const node = $id("ca-error"); node.textContent = message; node.hidden = false; };
    $id("ca-cancel").onclick = () => closePanel();
    $id("ca-save").onclick = async () => {
      const name = $id("ca-name").value.trim();
      const prompt = $id("ca-prompt").value.trim();
      if (!name) return fail(t("Укажите имя агента.", "Name the agent."));
      if (!prompt) return fail(t("Опишите, что делает агент, — инструкции не могут быть пустыми.", "Describe what the agent does: the instructions cannot be empty."));
      const groups = [...document.querySelectorAll(".ca-checks [data-group]")].filter((box) => box.checked).map((box) => box.dataset.group);
      const all = ["read", "edit", "run", "web"].every((id) => groups.includes(id));
      const table = MemuAgents.defsNow().groups || {};
      const initial = row?.tool_groups?.groups || [];
      const original = row?.tools;
      let tools = all ? null : groups.flatMap((id) => table[id] || []);
      if (Array.isArray(original)) {
        const removed = new Set(initial.filter((id) => !groups.includes(id)).flatMap((id) => table[id] || []).map((tool) => tool.toLowerCase()));
        const kept = original.filter((tool) => !removed.has(tool.toLowerCase()));
        const added = groups.filter((id) => !initial.includes(id)).flatMap((id) => table[id] || []);
        tools = [...new Set([...kept, ...added])];
      }
      if (Array.isArray(tools) && !tools.length) return fail(t("Выберите хотя бы одну группу инструментов.", "Select at least one tool group."));
      const body = { project_id: projectId(), action: "save", agent: {
        name, description: $id("ca-desc").value.trim(), prompt, model: $id("ca-model").value, tools,
        color: (document.querySelector('input[name="ca-color"]:checked') || {}).value || "", scope: $id("ca-scope").value,
        old_name: row && row.editable ? row.name : "", old_scope: row && row.editable ? row.source : "" } };
      $id("ca-save").disabled = true;
      try {
        await api("/api/code/agents", { method: "POST", body });
        MemuAgents.invalidate();
        closePanel();
        toast(t("Агент сохранён", "Agent saved"));
        render();
      } catch (error) {
        $id("ca-save").disabled = false;
        fail(error.message || String(error));
      }
    };
    const remove = $id("ca-delete");
    if (remove) remove.onclick = async () => {
      const ok = typeof askConfirm === "function" ? await askConfirm(t("Удалить агента?", "Delete the agent?"),
        t(`Файл агента «${row.name}» будет удалён — он пропадёт и из MemuCode CLI.`, `The file of “${row.name}” is deleted — it disappears from MemuCode CLI too.`), t("Удалить", "Delete")) : true;
      if (!ok) return;
      try {
        await api("/api/code/agents", { method: "POST", body: { project_id: projectId(), action: "delete", scope: row.source, name: row.name } });
        MemuAgents.invalidate();
        if (document.getElementById("ca-name")) closePanel();
        toast(t("Агент удалён", "Agent deleted"));
        render();
      } catch (error) { toast(error.message || String(error), "err"); }
    };
    setTimeout(() => $id(creating ? "ca-name" : "ca-prompt")?.focus(), 40);
  }

  function runningCount() {
    let n = 0;
    for (const message of S.messages || []) {
      if (message.role === "assistant" && Array.isArray(message.agentJobs)) n += message.agentJobs.filter((job) => job.status === "running").length;
    }
    return n;
  }

  return { render, openEditor, runningCount };
})();
window.MemuCodeAgents = MemuCodeAgents;
