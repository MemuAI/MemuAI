

"use strict";

const MemuCode = (() => {
  const store = {
    get(key, fallback) { try { const v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, String(value)); } catch (e) {  } },
  };
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const TAB_IDS = ["file", "diff", "artifact", "terminal", "tasks", "changes", "skills"];
  const savedTab = store.get("memu_code_tab", "") || (() => { try { return sessionStorage.getItem("memu_code_tab"); } catch (e) { return ""; } })();

  const state = {
    loaded: false,
    loadingTree: false,
    tree: [],
    git: {},
    branch: "",
    filter: "",
    collapsed: new Set(),
    collapseFor: "",
    tab: TAB_IDS.includes(savedTab) ? savedTab : "diff",
    file: null,
    artifact: null,
    artifacts: [],
    skills: [],
    skillFilter: "",
    diffMode: store.get("memu_code_diff_mode", "unified") === "split" ? "split" : "unified",
    diffText: "",
    diffStat: "",
    diffRepo: true,
    palette: [],
    paletteIndex: 0,
    explorerHidden: store.get("memu_code_tree_hidden", "0") === "1",

    dockWideHidden: store.get("memu_code_dock_hidden", "0") === "1",
    dockNarrowHidden: true,
    get dockHidden() { return narrow() ? this.dockNarrowHidden : this.dockWideHidden; },
    explorerTab: "files",
    dockWidth: clamp(parseInt(store.get("memu_code_dock_w", "400"), 10) || 400, 280, 900),
    treeHeight: clamp(parseInt(store.get("memu_code_tree_h", "260"), 10) || 260, 150, 900),
    tasks: [],
    tasksKey: "",
    changesCount: 0,
    termHistory: (() => { try { return JSON.parse(store.get("memu_code_term_history", "[]")) || []; } catch (e) { return []; } })(),
    termIndex: -1,
    focusPath: "",
    edit: null,
    editor: null,
    dockExpanded: store.get("memu_code_dock_expanded", "0") === "1",
  };

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const t = (ru, english) => (en() ? english : ru);
  const projectId = () => (S.workspaces && S.workspaces.active_id) || "";
  const session = () => (typeof codeSessionId === "function" ? codeSessionId() : "");
  const access = () => (typeof workAccessMode === "function" ? workAccessMode() : "ask");
  const activeProject = () => ((S.workspaces && S.workspaces.items) || []).find((item) => item.id === projectId());
  const modalOpen = () => !!document.getElementById("panel-back")?.classList.contains("visible");
  const narrow = () => window.matchMedia("(max-width: 980px)").matches;

  const ICONS = {
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    play: '<path d="m8 5 11 7-11 7z"/>',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    dock: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    plug: '<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    diff: '<path d="M6 3v12M18 9v12"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M9 6h4a2 2 0 0 1 2 2v1"/>',
    artifact: '<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
    tasks: '<path d="M4 6h10M4 12h16M4 18h8"/><circle cx="18" cy="6" r="2"/>',
    undo: '<path d="M4 8h9a5 5 0 0 1 0 10H8"/><path d="m8 4-4 4 4 4"/>',
    skills: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    at: '<circle cx="12" cy="12" r="4"/><path d="M16 12v1.5a2.5 2.5 0 0 0 5 0V12a9 9 0 1 0-3.5 7.1"/>',
    reveal: '<path d="M14 4h6v6M20 4l-8 8"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    branch: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="8" r="2.5"/><path d="M6 8.5v7M18 10.5c0 4-5 3-10.5 6"/>',
    download: '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
    back: '<path d="m15 18-6-6 6-6"/>',
    check: '<path d="m5 12 5 5L20 7"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    command: '<path d="m7 8 4 4-4 4M13 16h4"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    chat: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    expand: '<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
    shrink: '<path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ""}</svg>`;
  function fillIcons(root) {
    (root || document).querySelectorAll("[data-cicon]").forEach((node) => {
      if (!node.innerHTML.trim()) node.innerHTML = svg(node.dataset.cicon);
    });
  }

  const FILE_COLORS = {
    js: "#e8c547", mjs: "#e8c547", cjs: "#e8c547", jsx: "#61dafb", ts: "#3b82f6", tsx: "#61dafb",
    py: "#4b8bbe", json: "#d19a66", md: "#8b9bb4", css: "#a78bfa", scss: "#e879a9", html: "#f0764a",
    htm: "#f0764a", svg: "#f59e0b", yml: "#ef6f6c", yaml: "#ef6f6c", toml: "#9ca3af", rs: "#dea584",
    go: "#00add8", java: "#e76f00", c: "#6b8fd6", cpp: "#6b8fd6", h: "#8fa4d6", cs: "#9b6bd6",
    rb: "#cc342d", php: "#8892bf", sh: "#4eaa25", bat: "#4eaa25", ps1: "#3b82f6", sql: "#e38c00",
    txt: "#9ca3af", lock: "#6b7280", env: "#10b981", vue: "#42b883", kt: "#a97bff", swift: "#f05138",
  };
  function fileIcon(name) {
    const ext = String(name || "").split(".").pop().toLowerCase();
    return `<span class="cx-icon" style="--c:${FILE_COLORS[ext] || "var(--muted)"}">${svg("file")}</span>`;
  }

  function loadingView(lines = 6) {
    return `<div class="cd-loading" aria-busy="true" aria-label="${esc(t("Загрузка", "Loading"))}">${"<i></i>".repeat(lines)}</div>`;
  }
  function emptyView({ icon = "file", title = "", text = "", kind = "", action = "" } = {}) {
    return `<div class="cd-empty ${kind}"><span class="cd-empty-icon">${svg(icon)}</span>
      ${title ? `<b>${esc(title)}</b>` : ""}${text ? `<span>${esc(text)}</span>` : ""}${action}</div>`;
  }
  function errorView(message, retryId) {
    return emptyView({ icon: "help", kind: "error", title: t("Не получилось", "Something went wrong"), text: message,
      action: retryId ? `<button type="button" class="cd-btn" id="${retryId}">${svg("refresh")}${esc(t("Повторить", "Retry"))}</button>` : "" });
  }
  function dockBody() { return document.getElementById("code-dock-body"); }

  function askValue(title, text, value, action) {
    return new Promise((resolve) => {
      if (typeof panel !== "function") return resolve(window.prompt(title, value || ""));
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        if (typeof closePanel === "function") closePanel(result === null ? "cancel" : "confirm");
        resolve(result);
      };
      panel(`<h2>${esc(title)}</h2>${text ? `<p class="sub">${esc(text)}</p>` : ""}
        <input class="pinput" id="code-ask-input" spellcheck="false" value="${esc(value || "")}">
        <div class="row-end"><button class="pbtn ghost" id="code-ask-no">${esc(t("Отмена", "Cancel"))}</button>
          <button class="pbtn" id="code-ask-yes">${esc(action || t("Готово", "Done"))}</button></div>`, "", { onClose: () => finish(null) });
      const input = document.getElementById("code-ask-input");
      document.getElementById("code-ask-no").onclick = () => finish(null);
      document.getElementById("code-ask-yes").onclick = () => finish(input.value.trim());
      input.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); finish(input.value.trim()); } };
      setTimeout(() => { input.focus(); input.select(); }, 30);
    });
  }
  async function confirmAction(title, text, action) {
    if (typeof askConfirm !== "function") return true;
    return await askConfirm(title, text, action);
  }

  function skillState() {
    const all = pref("code_skill_state", {}) || {};
    const key = projectId() || "_";
    return (all[key] && Array.isArray(all[key].disabled)) ? all[key].disabled : [];
  }

  function codeSkillsPrompt() {
    const disabled = new Set(skillState());
    const rows = (state.skills || []).filter((row) => row && row.id && !disabled.has(row.id));
    if (!rows.length) return "";
    const lines = ["<code_skills>",
      "MemuCode playbooks. Call code_skill {id} before a task that clearly matches one (not for questions or small edits). Project skills override built-ins."];
    rows.slice(0, 24).forEach((row) => {
      lines.push(`• ${row.id} (${row.source || "builtin"}) — ${row.description || row.title || ""}`);
    });
    lines.push("</code_skills>");
    return "\n\n" + lines.join("\n");
  }
  window.codeSkillsPrompt = codeSkillsPrompt;

  async function post(path, body) {
    return api(path, { method: "POST", body: body || {} });
  }

  function treeSplit() {
    let split = document.getElementById("code-tree-split");
    if (!split) {
      split = document.createElement("div");
      split.id = "code-tree-split";
      split.className = "code-tree-split";
      split.setAttribute("role", "separator");
      split.tabIndex = 0;
      split.setAttribute("aria-orientation", "horizontal");
      split.setAttribute("aria-label", t("Высота дерева файлов", "File tree height"));
      split.title = t("Потяните, чтобы изменить высоту; стрелки — с клавиатуры", "Drag to resize; arrow keys work too");
    }
    return split;
  }

  function syncToggleButtons() {
    const files = document.getElementById("code-explorer-toggle");
    const dock = document.getElementById("code-dock-toggle");
    const collapse = document.getElementById("code-tree-collapse");
    if (files) files.setAttribute("aria-pressed", String(!state.explorerHidden));
    if (dock) dock.setAttribute("aria-pressed", String(!state.dockHidden));
    if (collapse) collapse.setAttribute("aria-expanded", String(!state.explorerHidden));
  }

  function placeExplorer() {
    const top = document.querySelector(".side-top");
    const scroll = document.querySelector(".side-scroll");
    const explorer = document.getElementById("code-explorer");
    const main = document.getElementById("main-content");
    const chat = document.getElementById("chat-view-wrap");
    if (!explorer || !top || !scroll || !main || !chat) return;
    document.documentElement.style.setProperty("--code-dock-w", state.dockWidth + "px");
    document.documentElement.style.setProperty("--code-tree-h", state.treeHeight + "px");
    const split = treeSplit();
    const code = S.mode === "code";
    if (code) {
      if (explorer.parentElement !== top) top.insertBefore(explorer, scroll);
      if (split.parentElement !== top) top.insertBefore(split, scroll);

      explorer.hidden = false;
      split.hidden = !!state.explorerHidden;
    } else {
      split.hidden = true;
      if (split.parentElement) split.remove();
      if (explorer.parentElement !== main) main.insertBefore(explorer, chat);
      explorer.hidden = true;
    }
    document.body.classList.toggle("code-tree-hidden", code && state.explorerHidden);
    document.body.classList.toggle("code-dock-hidden", code && state.dockHidden);
    const dock = document.getElementById("code-dock");
    const resizer = document.getElementById("code-dock-resizer");
    const backdrop = document.getElementById("code-dock-backdrop");
    if (dock) dock.hidden = !code || state.dockHidden;
    if (resizer) resizer.hidden = !code || state.dockHidden;
    if (backdrop) backdrop.hidden = !code || state.dockHidden;
    syncToggleButtons();
    bindResizeHandles();
  }

  function dragHandle(handle, onMove, onDone) {
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.classList.add("dragging");
      document.body.style.userSelect = "none";
      const start = { x: event.clientX, y: event.clientY };
      const move = (ev) => onMove(ev.clientX - start.x, ev.clientY - start.y);
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        handle.classList.remove("dragging");
        document.body.style.userSelect = "";
        onDone();
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    });
  }

  function setTreeHeight(value) {
    const side = document.querySelector(".side");
    const max = Math.max(200, Math.round(((side && side.clientHeight) || 700) * 0.72));
    state.treeHeight = clamp(Math.round(value), 150, max);
    document.documentElement.style.setProperty("--code-tree-h", state.treeHeight + "px");
  }
  function setDockWidth(value) {
    const max = Math.max(340, Math.round(window.innerWidth * 0.6));
    state.dockWidth = clamp(Math.round(value), 280, max);
    document.documentElement.style.setProperty("--code-dock-w", state.dockWidth + "px");
  }

  function bindResizeHandles() {
    const tree = document.getElementById("code-tree-split");
    if (tree && !tree.dataset.bound) {
      tree.dataset.bound = "1";
      let startH = state.treeHeight;
      tree.addEventListener("pointerdown", () => { startH = state.treeHeight; }, true);
      dragHandle(tree, (dx, dy) => setTreeHeight(startH + dy), () => store.set("memu_code_tree_h", state.treeHeight));
      tree.addEventListener("keydown", (event) => {
        if (!["ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        setTreeHeight(state.treeHeight + (event.key === "ArrowDown" ? 24 : -24));
        store.set("memu_code_tree_h", state.treeHeight);
      });
    }
    const dock = document.getElementById("code-dock-resizer");
    if (dock && !dock.dataset.bound) {
      dock.dataset.bound = "1";
      let startW = state.dockWidth;
      dock.addEventListener("pointerdown", () => { startW = state.dockWidth; }, true);
      dragHandle(dock, (dx) => setDockWidth(startW - dx), () => store.set("memu_code_dock_w", state.dockWidth));
      dock.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        setDockWidth(state.dockWidth + (event.key === "ArrowLeft" ? 24 : -24));
        store.set("memu_code_dock_w", state.dockWidth);
      });
      dock.addEventListener("dblclick", () => { setDockWidth(400); store.set("memu_code_dock_w", 400); });
    }
  }

  function setExplorerHidden(hidden) {
    state.explorerHidden = !!hidden;
    store.set("memu_code_tree_hidden", state.explorerHidden ? "1" : "0");
    if (!state.explorerHidden) {

      const side = document.querySelector(".side");
      if (side && side.classList.contains("hidden") && typeof toggleSidebar === "function") toggleSidebar(true);
      if (side && window.innerWidth <= 800) side.classList.add("open");
    }
    placeExplorer();
    if (!state.explorerHidden) setTimeout(() => document.getElementById("code-filter")?.focus({ preventScroll: true }), 30);
  }

  function setDockHidden(hidden) {
    if (narrow()) state.dockNarrowHidden = !!hidden;
    else {
      state.dockWideHidden = !!hidden;
      store.set("memu_code_dock_hidden", state.dockWideHidden ? "1" : "0");
    }
    placeExplorer();
    if (!state.dockHidden) renderDock();
  }

  function fillChrome() {
    const select = document.getElementById("code-project");
    if (select) {
      const items = (S.workspaces && S.workspaces.items) || [];
      const current = projectId();
      select.innerHTML = items.map((item) =>
        `<option value="${esc(item.id)}" ${item.id === current ? "selected" : ""}>${esc(item.name)}</option>`
      ).join("") || `<option value="">${esc(t("Проект не выбран", "No project"))}</option>`;
    }
    const active = activeProject();
    const path = document.getElementById("code-path");
    if (path) {
      path.textContent = active ? active.path : t("Выберите папку проекта", "Choose a project folder");
      path.title = active ? active.path : "";
    }
    const name = document.getElementById("code-explorer-name");
    if (name) name.textContent = active ? active.name : t("не выбран", "none");
    const accessButton = document.getElementById("code-access");
    if (accessButton) {
      const full = access() === "full";
      accessButton.textContent = full ? t("Полный доступ", "Full access") : t("С подтверждением", "Ask");
      accessButton.classList.toggle("danger-active", full);
      accessButton.title = full
        ? t("Полный доступ: правки и команды без подтверждения", "Full access: edits and commands run without asking")
        : t("С подтверждением: каждое изменение файла ждёт подтверждения", "Ask: every file change waits for approval");
    }
    const plan = document.getElementById("code-plan");
    if (plan) {
      plan.classList.toggle("active", !!S.workPlanMode);
      plan.setAttribute("aria-pressed", String(!!S.workPlanMode));
    }
    const guide = document.getElementById("code-guide");
    if (guide) {
      const keys = [
        ["Ctrl+P", t("файлы, команды и скиллы", "files, commands, skills")],
        ["Ctrl+Shift+E", t("дерево проекта", "project tree")],
        ["Ctrl+Shift+D", t("правая панель", "side panel")],
        ["Ctrl+`", t("терминал", "terminal")],
        ["Alt+1…7", t("разделы панели", "panel sections")],
        ["/", t("команды в поле ввода", "commands in the composer")],
      ];
      guide.innerHTML = `<b>${esc(t("MemuCode работает с репозиторием.", "MemuCode works in the repository."))}</b><span>${esc(t(
        "Файлы проекта — слева над чатами, справа — файл, изменения, терминал и откат. Обе панели скрываются кнопками на полосе сверху и возвращаются ими же.",
        "Project files sit on the left above the chats; the right panel holds the file, changes, terminal and undo. Both hide and come back with the buttons on the top bar."))}</span>
        <div class="code-keys">${keys.map(([key, label]) => `<div><span>${esc(label)}</span><kbd>${esc(key)}</kbd></div>`).join("")}</div>`;
    }
    fillIcons(document.getElementById("codebar"));
  }

  let metaBusy = false;
  async function refreshMeta() {
    if (S.mode !== "code" || !projectId() || metaBusy) return;
    metaBusy = true;
    try {
      try {
        const stats = await post("/api/code/context", { project_id: projectId(), session: session() });
        const saved = Number(stats.saved_chars || 0);
        const node = document.getElementById("code-savings");
        if (node) {
          node.textContent = saved ? t(`кэш −${saved.toLocaleString("ru-RU")}`, `cache −${saved.toLocaleString("en-US")}`) : "";
          node.title = t("Столько символов повторных чтений не ушло в контекст", "Characters of repeated reads kept out of the context");
        }
      } catch (e) {  }
      try {
        const sandbox = await post("/api/code/sandbox", { session: session() });
        const node = document.getElementById("code-sandbox-state");
        const active = Number(sandbox.active_processes || 0);
        if (node) {
          node.textContent = active ? t(`песочница · ${active}`, `sandbox · ${active}`) : t("песочница", "sandbox");
          node.classList.toggle("live", active > 0);
          node.title = active
            ? t("Идут процессы проекта — «Стоп» завершит их все", "Project processes are running — Stop ends them all")
            : t("Команды проекта выполняются в изолированной песочнице", "Project commands run in an isolated sandbox");
        }
        const tasks = sandbox.tasks || [];
        const key = JSON.stringify(tasks.map((task) => [task.id, task.running, task.exit_code, (task.stdout || "").length, (task.stderr || "").length]));
        state.tasks = tasks;
        if (key !== state.tasksKey) {
          state.tasksKey = key;
          updateBadges();
          if (state.tab === "tasks" && !state.dockHidden) renderTasks();
        }
      } catch (e) {  }
    } finally {
      metaBusy = false;
    }
  }

  async function refreshTree() {
    const root = document.getElementById("code-tree");
    if (!projectId()) {
      state.tree = [];
      state.git = {};
      state.branch = "";

      state.treeFor = "";
      renderTree();
      return;
    }

    if (state.loadingTree) { state.treeAgain = true; return; }
    state.loadingTree = true;
    const pid = projectId();
    const button = document.getElementById("code-tree-refresh");
    if (button) button.classList.add("spin");
    if (root && !state.tree.length) root.innerHTML = loadingView(8);
    try {
      const data = await post("/api/code/tree", { project_id: pid, session: session() });
      if (pid === projectId()) {
        state.tree = data.tree || [];
        state.git = data.git || {};
        state.branch = data.branch || "";
        state.loaded = true;
        state.treeFor = pid;
        renderTree();
        updateBadges();
      }
    } catch (error) {
      if (root && pid === projectId()) root.innerHTML = `<p class="code-empty">${esc(error.message || t("Дерево недоступно", "The tree is unavailable"))}</p>`;
    } finally {
      state.loadingTree = false;
      if (button) button.classList.remove("spin");
      if (state.treeAgain || pid !== projectId()) {
        state.treeAgain = false;
        if (pid !== projectId()) state.loaded = false;
        refreshTree();
      }
    }
  }

  function resetProjectState() {
    state.loaded = false;
    state.tree = [];
    state.git = {};
    state.branch = "";
    state.skills = [];
    state.file = null;
    state.edit = null;
    state.editor = null;
    state.artifact = null;
    state.diffText = "";
    state.diffStat = "";
    state.changesCount = 0;
    state.treeFor = projectId();
  }

  function resetSessionState() {
    state.artifact = null;
    state.artifacts = [];
    state.changesCount = 0;
    state.termCleared = false;
    state.tasksKey = "";
    state.sessionFor = session();
  }

  function buildNodes(paths) {
    const root = { name: "", path: "", dirs: new Map(), files: [] };
    const needle = state.filter.trim().toLowerCase();
    paths.forEach((rel) => {
      if (needle && !rel.toLowerCase().includes(needle)) return;
      const parts = String(rel).split("/");
      let node = root;
      for (let index = 0; index < parts.length - 1; index += 1) {
        const name = parts[index];
        const dirPath = parts.slice(0, index + 1).join("/");
        if (!node.dirs.has(name)) node.dirs.set(name, { name, path: dirPath, dirs: new Map(), files: [] });
        node = node.dirs.get(name);
      }
      node.files.push({ name: parts[parts.length - 1], path: rel });
    });
    return root;
  }

  function highlightName(name) {
    const needle = state.filter.trim();
    const safe = esc(name);
    if (!needle) return safe;
    const at = name.toLowerCase().indexOf(needle.toLowerCase());
    if (at < 0) return safe;
    return esc(name.slice(0, at)) + "<mark>" + esc(name.slice(at, at + needle.length)) + "</mark>" + esc(name.slice(at + needle.length));
  }

  function gitClass(mark) { return String(mark || "").trim().replace(/[^A-Za-z]/g, "q").slice(0, 2); }

  function gitMark(path) {
    const mark = String(state.git[path] || "").trim();
    if (!mark) return "";
    const labels = { M: t("изменён", "modified"), A: t("добавлен", "added"), D: t("удалён", "deleted"),
      R: t("переименован", "renamed"), "??": t("новый, не в git", "untracked"), U: t("конфликт", "conflict") };
    return `<span class="cx-git ${gitClass(mark)}" title="${esc(labels[mark] || mark)}">${esc(mark === "??" ? "U" : mark.slice(0, 2))}</span>`;
  }

  function dirChanged(path) {
    const prefix = path + "/";
    return Object.keys(state.git).some((key) => key.startsWith(prefix));
  }

  function renderNode(node, depth) {
    const dirs = [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name));
    const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name));
    const open = !!state.filter || !state.collapsed.has(node.path);
    const children = open ? dirs.map((dir) => renderNode(dir, depth + 1)).join("") + files.map((file) => `
      <button type="button" class="cx-row file ${state.file && state.file.path === file.path ? "active" : ""} ${state.git[file.path] ? "git-" + gitClass(state.git[file.path]) : ""}"
        role="treeitem" tabindex="-1" data-path="${esc(file.path)}" style="--depth:${depth + 1}" title="${esc(file.path)}">
        <span class="cx-caret"></span>${fileIcon(file.name)}<span class="cx-name">${highlightName(file.name)}</span>${gitMark(file.path)}
      </button>`).join("") : "";
    if (!node.path) return children;
    return `<div role="group">
      <button type="button" class="cx-row dir" role="treeitem" tabindex="-1" aria-expanded="${open}" data-dir="${esc(node.path)}" style="--depth:${depth}" title="${esc(node.path)}">
        <span class="cx-caret">${svg("chevron")}</span><span class="cx-icon">${svg("folder")}</span><span class="cx-name">${highlightName(node.name)}</span>${dirChanged(node.path) ? '<span class="cx-git M">•</span>' : ""}
      </button>
      ${children}
    </div>`;
  }

  function renderSessions() {
    const root = document.getElementById("code-tree");
    if (!root) return;
    const q = state.filter.trim().toLowerCase();
    const chats = (S.chats || []).filter((chat) => (chat.mode || "chat") === "code"
      && (!projectId() || !chat.project_id || chat.project_id === projectId())
      && (!q || String(chat.title || "").toLowerCase().includes(q)));
    root.innerHTML = chats.map((chat) => {
      const when = chat.updated ? new Date(chat.updated).toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" }) : "";
      return `<button type="button" class="cx-row cx-session ${chat.id === S.currentId ? "active" : ""}" role="treeitem" tabindex="-1" data-session="${esc(chat.id)}" style="--depth:0">
        <span class="cx-icon">${svg("chat")}</span><span class="cx-name">${esc(chat.title || t("Сессия", "Session"))}</span><small>${esc(when)}</small></button>`;
    }).join("") || emptyView({ icon: "chat", title: t("Сессий кода пока нет", "No code sessions yet"),
      text: t("Опишите задачу в поле ввода — сессия появится здесь.", "Describe a task in the composer — the session appears here.") });
    root.querySelectorAll("[data-session]").forEach((button) => {
      button.onclick = () => { if (typeof openChat === "function") openChat(button.dataset.session); };
    });
    focusFirstRow(root);
  }

  function focusFirstRow(root) {
    const rows = [...root.querySelectorAll(".cx-row")];
    const current = rows.find((row) => row.dataset.path === state.focusPath || row.dataset.dir === state.focusPath)
      || rows.find((row) => row.classList.contains("active")) || rows[0];
    if (current) current.tabIndex = 0;
  }

  function renderTree() {
    const root = document.getElementById("code-tree");
    if (!root) return;
    $$("[data-explorer-tab]").forEach((button) => button.setAttribute("aria-selected", String(button.dataset.explorerTab === state.explorerTab)));
    const filter = document.getElementById("code-filter");
    if (filter) filter.placeholder = state.explorerTab === "sessions" ? t("Найти сессию", "Find a session") : t("Найти файл", "Find a file");
    const branch = document.getElementById("code-branch");
    if (branch) {
      branch.hidden = !state.branch;
      branch.innerHTML = state.branch ? `${svg("branch")}<span>${esc(state.branch)}</span>` : "";
      branch.title = state.branch ? t(`Ветка git: ${state.branch}`, `Git branch: ${state.branch}`) : "";
    }
    if (state.collapseFor !== projectId()) {

      state.collapsed = new Set();
      state.tree.forEach((rel) => {
        const parts = String(rel).split("/");
        for (let depth = 2; depth < parts.length; depth += 1) state.collapsed.add(parts.slice(0, depth).join("/"));
      });
      state.collapseFor = projectId();
    }
    if (state.explorerTab === "sessions") return renderSessions();
    if (!projectId()) {
      root.innerHTML = emptyView({ icon: "folder", title: t("Проект не выбран", "No project selected"),
        text: t("Добавьте папку проекта кнопкой «+» на полосе сверху.", "Add a project folder with the + button on the top bar.") });
      return;
    }
    if (!state.tree.length) {
      root.innerHTML = state.loaded ? emptyView({ icon: "folder", title: t("В проекте пока нет файлов", "This project has no files yet") }) : loadingView(8);
      return;
    }
    const html = renderNode(buildNodes(state.tree), 0);
    root.innerHTML = html || emptyView({ icon: "search", title: t("Ничего не найдено", "Nothing found"),
      text: t("Попробуйте часть имени файла или путь.", "Try part of a file name or a path.") });
    focusFirstRow(root);
  }

  function toggleDir(dir, open) {
    const isOpen = !state.collapsed.has(dir);
    const next = open === undefined ? !isOpen : open;
    if (next) state.collapsed.delete(dir); else state.collapsed.add(dir);
    state.focusPath = dir;
    renderTree();
    const row = document.querySelector(`#code-tree [data-dir="${CSS.escape(dir)}"]`);
    if (row) row.focus({ preventScroll: false });
  }

  function onTreeClick(event) {
    const dir = event.target.closest("[data-dir]");
    const file = event.target.closest("[data-path]");
    if (dir) return toggleDir(dir.dataset.dir);
    if (file) {
      state.focusPath = file.dataset.path;
      openFile(file.dataset.path);
    }
  }

  function onTreeKey(event) {
    const rows = [...document.querySelectorAll("#code-tree .cx-row")];
    const current = event.target.closest(".cx-row");
    if (!current || !rows.length) return;
    const index = rows.indexOf(current);
    const move = (next) => {
      if (!next) return;
      rows.forEach((row) => { row.tabIndex = -1; });
      next.tabIndex = 0;
      next.focus();
      state.focusPath = next.dataset.path || next.dataset.dir || "";
    };
    if (event.key === "ArrowDown") { event.preventDefault(); move(rows[index + 1]); }
    else if (event.key === "ArrowUp") { event.preventDefault(); move(rows[index - 1]); }
    else if (event.key === "Home") { event.preventDefault(); move(rows[0]); }
    else if (event.key === "End") { event.preventDefault(); move(rows[rows.length - 1]); }
    else if (event.key === "ArrowRight" && current.dataset.dir) {
      event.preventDefault();
      if (current.getAttribute("aria-expanded") === "false") toggleDir(current.dataset.dir, true);
      else move(rows[index + 1]);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (current.dataset.dir && current.getAttribute("aria-expanded") === "true") toggleDir(current.dataset.dir, false);
      else {
        const parent = current.parentElement && current.parentElement.closest("[role='group']");
        const head = parent && parent.querySelector(":scope > [data-dir]");
        if (head && head !== current) move(head);
      }
    } else if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
      event.preventDefault();
      const rect = current.getBoundingClientRect();
      fileMenu(current.dataset.path || current.dataset.dir, { clientX: rect.left + 24, clientY: rect.bottom, preventDefault() {}, stopPropagation() {} }, !!current.dataset.dir);
    }
  }

  function fileMenu(path, event, isDir) {
    event.preventDefault();
    event.stopPropagation();
    if (!path || typeof showContextMenu !== "function") return;
    const active = activeProject();
    const absolute = active ? `${String(active.path).replace(/[\\/]+$/, "")}\\${String(path).replace(/\//g, "\\")}` : path;
    showContextMenu([
      isDir
        ? { label: state.collapsed.has(path) ? t("Раскрыть папку", "Expand folder") : t("Свернуть папку", "Collapse folder"), icon: "folder", run: () => toggleDir(path) }
        : { label: t("Открыть в панели", "Open in the panel"), icon: "folder", run: () => openFile(path) },
      { label: t("Упомянуть в запросе", "Mention in the prompt"), icon: "text", run: () => insertMention(isDir ? `${String(path).replace(/\/$/, "")}/` : path) },
      ...(!isDir && state.git[path] ? [{ label: t("Изменения этого файла", "Changes in this file"), icon: "branch", run: () => showDiffFor(path) }] : []),
      { label: t("Копировать путь", "Copy path"), icon: "copy", run: () => copyValue(path) },
      { label: t("Показать в проводнике", "Show in File Explorer"), icon: "forward", run: () => revealProjectPath(absolute) },
    ], event.clientX, event.clientY);
  }

  function insertMention(path) {
    const input = document.getElementById("input");
    if (!input) return;
    const mention = `${typeof mentionFor === "function" ? mentionFor(path) : `@(${path})`} `;
    input.value = input.value ? `${input.value.replace(/\s*$/, " ")}${mention}` : mention;
    input.dispatchEvent(new Event("input"));
    input.focus();
    toast(t("Файл упомянут в запросе", "File mentioned in the prompt"));
  }

  async function revealProjectPath(absolute) {
    try { await api("/api/pg/reveal-file", { method: "POST", body: { path: absolute } }); }
    catch (error) { toast(error.message, "err"); }
  }

  function copyValue(text) {
    if (typeof copyText === "function") return copyText(String(text || ""));
    const done = () => toast(t("Скопировано", "Copied"));
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done).catch(() => {});
  }

  function highlightLines(lines, path) {
    if (window.MemuSyntax) {
      try { return MemuSyntax.highlight(lines.join("\n"), path); } catch (error) {  }
    }
    return lines.map((line) => esc(line));
  }

  function highlightRow(text, path) {
    if (window.MemuSyntax) {
      try { return MemuSyntax.highlightLine(text, path); } catch (error) {  }
    }
    return esc(text);
  }

  async function openFile(path, start = 1, hit = 0) {
    if (!path) return;
    if (state.edit && state.edit.path !== path && !(await leaveEditor())) return;
    if (state.dockHidden) setDockHidden(false);
    selectTab("file", false);
    const body = dockBody();
    if (body) body.innerHTML = loadingView(10);

    const seq = (state.fileSeq = (state.fileSeq || 0) + 1);
    try {
      const data = await post("/api/code/file", { project_id: projectId(), path, start_line: start, end_line: start + 399 });
      if (seq !== state.fileSeq) return;
      state.file = { ...data, hit };
      renderFile();
      document.querySelectorAll("#code-tree .cx-row.file").forEach((row) => row.classList.toggle("active", row.dataset.path === data.path));
    } catch (error) {
      if (seq !== state.fileSeq) return;
      if (body && state.tab === "file") {
        body.innerHTML = errorView(error.message, "code-file-retry");
        document.getElementById("code-file-retry")?.addEventListener("click", () => openFile(path, start));
      }
    }
  }

  function renderFile() {
    const body = dockBody();
    if (!body || state.tab !== "file") return;
    if (state.edit) return renderEditor();
    const data = state.file;
    if (!data) {
      body.innerHTML = emptyView({ icon: "file", title: t("Файл не открыт", "No file open"),
        text: t("Выберите файл в дереве слева или найдите его через Ctrl+P.", "Pick a file in the tree on the left or find it with Ctrl+P.") });
      return;
    }
    const lines = String(data.text || "").split("\n");
    const start = Number(data.start_line || 1);
    const colored = highlightLines(lines, data.path || "");
    const parts = String(data.path || "").split("/");
    const name = parts.pop();
    body.innerHTML = `<div class="cd-toolbar">
      <div class="cd-title"><span class="cd-path" title="${esc(data.path || "")}">${parts.length ? `<span>${esc(parts.join("/"))}/</span>` : ""}<b>${esc(name)}</b></span>
        <small>${esc(`${start}–${data.end_line || start} / ${data.total_lines || lines.length}`)}</small></div>
      <button type="button" class="cd-btn" id="code-file-edit" title="${esc(t("Изменить файл прямо здесь — E", "Edit the file right here — E"))}">${svg("edit")}${esc(t("Изменить", "Edit"))}</button>
      <button type="button" class="cd-btn icon" id="code-file-copy" title="${esc(t("Копировать текст файла", "Copy the file text"))}">${svg("copy")}</button>
      <button type="button" class="cd-btn icon" id="code-file-ask" title="${esc(t("Упомянуть файл в запросе (@путь)", "Mention the file in the prompt (@path)"))}">${svg("at")}</button>
      <button type="button" class="cd-btn icon" id="code-file-reveal" title="${esc(t("Показать в проводнике", "Show in File Explorer"))}">${svg("reveal")}</button>
      ${state.git[data.path] ? `<button type="button" class="cd-btn" id="code-file-diff">${svg("diff")}${esc(t("Изменения", "Changes"))}</button>` : ""}
    </div>
    <div class="code-lines" tabindex="0" aria-label="${esc(data.path || "")}">${lines.map((line, index) =>
      `<div class="code-line ${data.hit && start + index === data.hit ? "hit" : ""}"><span>${start + index}</span><code>${colored[index] || ""}</code></div>`).join("")}
      ${start > 1 || data.truncated ? `<div class="cd-foot" style="border:0;background:transparent;justify-content:center">
        ${start > 1 ? `<button type="button" class="cd-btn" id="code-file-prev">${esc(t("Предыдущие 400 строк", "Previous 400 lines"))}</button>` : ""}
        ${data.truncated ? `<button type="button" class="cd-btn" id="code-file-more">${esc(t("Следующие 400 строк", "Next 400 lines"))}</button>` : ""}</div>` : ""}
    </div>`;
    document.getElementById("code-file-edit")?.addEventListener("click", () => startEdit(data.path));
    body.querySelector(".code-lines")?.addEventListener("keydown", (event) => {
      if ((window.MemuKeys ? MemuKeys.latin(event) : event.key).toLowerCase() === "e" && !event.ctrlKey && !event.altKey && !event.metaKey) { event.preventDefault(); startEdit(data.path); }
    });
    body.querySelector(".code-lines")?.addEventListener("dblclick", (event) => {
      const row = event.target.closest(".code-line");
      if (row && event.altKey) startEdit(data.path, Number(row.querySelector("span")?.textContent) || 1);
    });
    document.getElementById("code-file-copy")?.addEventListener("click", () => copyValue(data.text || ""));
    document.getElementById("code-file-ask")?.addEventListener("click", () => insertMention(data.path || ""));
    document.getElementById("code-file-reveal")?.addEventListener("click", () => {
      const active = activeProject();
      if (active) revealProjectPath(`${String(active.path).replace(/[\\/]+$/, "")}\\${String(data.path).replace(/\//g, "\\")}`);
    });
    document.getElementById("code-file-diff")?.addEventListener("click", () => showDiffFor(data.path));
    document.getElementById("code-file-more")?.addEventListener("click", () => openFile(data.path, (data.end_line || start) + 1));
    document.getElementById("code-file-prev")?.addEventListener("click", () => openFile(data.path, Math.max(1, start - 400)));
    const hitRow = body.querySelector(".code-line.hit");
    if (hitRow) hitRow.scrollIntoView({ block: "center" });
  }

  async function startEdit(path, line) {
    if (!path) return;
    if (state.edit && state.edit.path === path) {
      if (line && state.editor) state.editor.goToLine(line);
      else renderEditor();
      return;
    }
    const body = dockBody();
    if (body) body.innerHTML = loadingView(10);
    try {
      const data = await post("/api/code/source", { project_id: projectId(), path });
      state.edit = { path: data.path, hash: data.hash, eol: data.eol, encoding: data.encoding,
        text: data.text, original: data.text, conflict: null, line: line || 0 };
      if (state.dockHidden) setDockHidden(false);
      selectTab("file");
    } catch (error) {
      toast(error.message, "err");
      renderFile();
    }
  }

  function editorDirty() {
    if (!state.edit) return false;
    const text = state.editor ? state.editor.value : state.edit.text;
    return text !== state.edit.original;
  }

  async function leaveEditor() {
    if (!state.edit) return true;
    if (editorDirty()) {
      const ok = await confirmAction(t("Закрыть файл без сохранения?", "Close the file without saving?"),
        t(`В «${state.edit.path}» есть несохранённые правки. Они пропадут.`, `“${state.edit.path}” has unsaved edits. They will be lost.`),
        t("Не сохранять", "Discard"));
      if (!ok) return false;
    }
    state.edit = null;
    state.editor = null;
    return true;
  }

  function renderEditor() {
    const body = dockBody();
    const edit = state.edit;
    if (!body || !edit || state.tab !== "file") return;
    if (state.editor) edit.text = state.editor.value;
    const parts = edit.path.split("/");
    const name = parts.pop();
    const conflict = edit.conflict ? `<div class="ce-conflict" role="alert">
        <b>${esc(t("Файл изменился на диске", "The file changed on disk"))}</b>
        <span>${esc(t("Пока вы правили, его изменил ИИ или другая программа. Выберите, что оставить.", "While you were editing, the AI or another program changed it. Choose what to keep."))}</span>
        <div><button type="button" class="cd-btn danger" id="ce-force">${esc(t("Сохранить мою версию", "Keep my version"))}</button>
        <button type="button" class="cd-btn" id="ce-take-disk">${esc(t("Взять версию с диска", "Take the disk version"))}</button></div></div>` : "";
    body.innerHTML = `<div class="cd-toolbar ce-toolbar">
      <div class="cd-title"><span class="cd-path" title="${esc(edit.path)}">${parts.length ? `<span>${esc(parts.join("/"))}/</span>` : ""}<b>${esc(name)}</b></span>
        <small class="ce-state" id="ce-state"></small></div>
      <button type="button" class="cd-btn icon" id="ce-revert" title="${esc(t("Вернуть как было при открытии", "Revert to the opened version"))}">${svg("undo")}</button>
      <button type="button" class="cd-btn icon" id="ce-close" title="${esc(t("Закрыть редактор — к просмотру", "Close the editor — back to viewing"))}">${svg("close")}</button>
      <button type="button" class="cd-btn primary" id="ce-save" title="${esc(t("Сохранить файл — Ctrl+S", "Save the file — Ctrl+S"))}">${svg("save")}${esc(t("Сохранить", "Save"))}</button>
    </div>${conflict}<div class="ce-host" id="ce-host"></div>`;
    const host = document.getElementById("ce-host");
    state.editor = MemuCodeEditor.create(host, {
      text: edit.text, path: edit.path, eol: edit.eol, encoding: edit.encoding, t,
      onSave: saveEdit,
      onChange: (dirty) => markEditorState(dirty),
    });

    state.editor.markSaved(edit.original);
    markEditorState(editorDirty());
    if (edit.line) {

      const target = edit.line, editor = state.editor;
      edit.line = 0;
      requestAnimationFrame(() => requestAnimationFrame(() => { if (state.editor === editor) editor.goToLine(target); }));
    }
    document.getElementById("ce-save").onclick = () => saveEdit();
    document.getElementById("ce-close").onclick = async () => {
      const path = edit.path;
      if (await leaveEditor()) openFile(path, state.file && state.file.path === path ? state.file.start_line || 1 : 1);
    };
    document.getElementById("ce-revert").onclick = () => state.editor && state.editor.replaceAll(edit.original);
    document.getElementById("ce-force")?.addEventListener("click", () => saveEdit(true));
    document.getElementById("ce-take-disk")?.addEventListener("click", async () => {
      try {
        const data = await post("/api/code/source", { project_id: projectId(), path: edit.path });
        Object.assign(edit, { hash: data.hash, eol: data.eol, encoding: data.encoding, original: data.text, conflict: null });
        edit.text = data.text;
        state.editor = null;
        renderEditor();
        toast(t("Загружена версия с диска", "Loaded the version from disk"));
      } catch (error) { toast(error.message, "err"); }
    });
  }

  function markEditorState(dirty) {
    const node = document.getElementById("ce-state");
    if (node) {
      node.textContent = dirty ? t("● не сохранено", "● unsaved") : t("сохранено", "saved");
      node.classList.toggle("dirty", !!dirty);
    }
    const save = document.getElementById("ce-save");
    if (save) save.disabled = !dirty && !(state.edit && state.edit.conflict);
    const tab = document.getElementById("code-tab-file");
    if (tab) tab.classList.toggle("dirty", !!dirty);
  }

  async function saveEdit(force) {
    const edit = state.edit;
    if (!edit || !state.editor || edit.saving) return;
    const text = state.editor.value;
    if (!force && text === edit.original && !edit.conflict) { toast(t("Изменений нет", "No changes")); return; }
    edit.saving = true;
    try {
      const response = await fetch("/api/code/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_id: projectId(), path: edit.path, text, hash: edit.hash, eol: edit.eol,
          encoding: edit.encoding, session: session(), force: !!force }),
      });
      const data = await response.json().catch(() => ({}));
      if (response.status === 409) {
        edit.conflict = { hash: data.hash };
        edit.text = text;
        renderEditor();
        return;
      }
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
      Object.assign(edit, { hash: data.hash, encoding: data.encoding || edit.encoding, original: text, text, conflict: null });
      state.editor.markSaved(text);
      if (document.getElementById("ce-force")) renderEditor();
      markEditorState(false);
      toast(t(`Сохранено: ${edit.path}`, `Saved: ${edit.path}`));
      state.file = null;
      refreshTree();
      countChanges();
    } catch (error) {
      toast(t("Файл не сохранён: ", "The file was not saved: ") + error.message, "err");
    } finally {
      edit.saving = false;
    }
  }

  function onEditedFileChanged() {
    const edit = state.edit;
    if (!edit) return;
    if (!editorDirty()) {

      post("/api/code/source", { project_id: projectId(), path: edit.path }).then((data) => {
        Object.assign(edit, { hash: data.hash, eol: data.eol, encoding: data.encoding, original: data.text, text: data.text });
        state.editor = null;
        if (state.tab === "file") renderEditor();
      }).catch(() => {});
    } else {
      edit.conflict = { hash: "" };
      if (state.tab === "file") renderEditor();
    }
  }

  function setDockExpanded(expanded) {
    state.dockExpanded = !!expanded;
    store.set("memu_code_dock_expanded", state.dockExpanded ? "1" : "0");
    const dock = document.getElementById("code-dock");
    if (dock) dock.classList.toggle("expanded", state.dockExpanded);
    document.body.classList.toggle("code-dock-expanded", state.dockExpanded);
    const button = document.getElementById("code-dock-expand");
    if (button) {
      button.innerHTML = svg(state.dockExpanded ? "shrink" : "expand");
      const label = state.dockExpanded ? t("Вернуть обычную ширину панели", "Restore the panel width")
        : t("Развернуть панель на всю ширину", "Expand the panel to full width");
      button.dataset.tip = label;
      button.setAttribute("aria-label", label);
      button.setAttribute("aria-pressed", String(state.dockExpanded));
    }
  }

  const TABS = [
    ["file", "file", "Файл", "File"],
    ["diff", "diff", "Изменения", "Changes"],
    ["artifact", "artifact", "Артефакты", "Artifacts"],
    ["terminal", "terminal", "Терминал", "Terminal"],
    ["tasks", "tasks", "Задачи", "Tasks"],
    ["changes", "undo", "Откат", "Undo"],
    ["skills", "skills", "Скиллы", "Skills"],
  ];

  function badgeFor(id) {
    if (id === "diff") { const n = Object.keys(state.git).length; return n ? String(n) : ""; }
    if (id === "tasks") { const n = state.tasks.filter((task) => task.running).length; return n ? String(n) : ""; }
    if (id === "changes") return state.changesCount ? String(state.changesCount) : "";
    if (id === "artifact") return state.artifacts.length ? String(state.artifacts.length) : "";
    return "";
  }

  function updateBadges() {
    document.querySelectorAll("#code-dock-tabs [data-tab]").forEach((button) => {
      const badge = button.querySelector(".cd-badge");
      if (!badge) return;
      badge.textContent = badgeFor(button.dataset.tab);
      badge.classList.toggle("live", button.dataset.tab === "tasks" && !!badge.textContent);
    });
  }

  function renderTabs() {
    const host = document.getElementById("code-dock-tabs");
    if (!host) return;
    host.innerHTML = TABS.map(([id, iconName, ru, english], index) =>
      `<button type="button" role="tab" data-tab="${id}" id="code-tab-${id}" aria-selected="${state.tab === id}" tabindex="${state.tab === id ? 0 : -1}"
        title="${esc(t(ru, english))} — Alt+${index + 1}">${svg(iconName)}<span class="cd-label">${esc(t(ru, english))}</span><span class="cd-badge"></span></button>`
    ).join("");
    updateBadges();
    const body = dockBody();
    if (body) body.setAttribute("aria-labelledby", "code-tab-" + state.tab);
  }

  function selectTab(id, render = true) {
    if (!TAB_IDS.includes(id)) return;
    state.tab = id;
    store.set("memu_code_tab", id);
    renderTabs();
    if (render) renderDock();
  }

  function renderDock() {
    if (S.mode !== "code" || state.dockHidden) return;
    renderTabs();
    const tab = state.tab;
    if (tab === "file") return renderFile();
    if (tab === "diff") return loadDiff();
    if (tab === "artifact") return loadArtifacts();
    if (tab === "terminal") return renderTerminal();
    if (tab === "tasks") { renderTasks(); return refreshMeta(); }
    if (tab === "changes") return renderChanges();
    if (tab === "skills") return renderSkills();
  }

  async function loadDiff(focusPath) {
    const body = dockBody();
    if (!body || state.tab !== "diff") return;
    if (!projectId()) {
      body.innerHTML = emptyView({ icon: "folder", title: t("Проект не выбран", "No project selected"),
        text: t("Изменения показываются для папки проекта.", "Changes are shown for a project folder.") });
      return;
    }
    if (!state.diffText && !body.querySelector(".diff-file")) body.innerHTML = loadingView(8);
    try {
      const data = await post("/api/code/diff", { project_id: projectId() });
      state.diffText = data.diff || "";
      state.diffStat = data.stat || "";
      state.diffRepo = data.repository !== false;
      if (state.tab === "diff") renderDiff(focusPath);
    } catch (error) {
      if (state.tab !== "diff") return;
      body.innerHTML = errorView(error.message, "code-diff-retry");
      document.getElementById("code-diff-retry")?.addEventListener("click", () => loadDiff());
    }
  }

  function showDiffFor(path) {
    if (state.dockHidden) setDockHidden(false);
    selectTab("diff", false);
    loadDiff(path);
  }

  function parseDiff(text) {
    const files = [];
    let file = null;
    let oldLine = 0;
    let newLine = 0;

    let oldLeft = 0;
    let newLeft = 0;

    const unquote = (value) => {
      const text = String(value || "");
      if (!text.startsWith('"')) return text;
      const body = text.replace(/^"|"$/g, "");
      const bytes = [];
      for (let i = 0; i < body.length; i += 1) {
        const ch = body[i];
        if (ch !== "\\") { bytes.push(...new TextEncoder().encode(ch)); continue; }
        const next = body[i + 1];
        if (/[0-7]/.test(next)) { bytes.push(parseInt(body.slice(i + 1, i + 4), 8)); i += 3; continue; }
        const map = { n: 10, t: 9, r: 13, '"': 34, "\\": 92, a: 7, b: 8, f: 12, v: 11 };
        bytes.push(map[next] ?? next.charCodeAt(0)); i += 1;
      }
      return new TextDecoder().decode(new Uint8Array(bytes));
    };
    const start = (path) => {
      file = { path, rows: [], add: 0, del: 0, status: "", binary: false };
      files.push(file);
    };
    String(text || "").split(/\r?\n/).forEach((line) => {
      const inHunk = oldLeft > 0 || newLeft > 0;
      if (!inHunk && line.startsWith("diff --git ")) {

        const quoted = line.lastIndexOf(' "b/');
        const plain = line.lastIndexOf(" b/");
        start(quoted >= 0 ? unquote('"' + line.slice(quoted + 4))
          : plain >= 0 ? line.slice(plain + 3) : line.slice(11));
        return;
      }
      if (!file) start("");
      if (!inHunk) {
        if (/^(index |similarity |rename |old mode|new mode|dissimilarity )/.test(line)) return;
        if (line.startsWith("new file")) { file.status = "new"; return; }
        if (line.startsWith("deleted file")) { file.status = "deleted"; return; }
        if (line.startsWith("Binary files")) { file.binary = true; return; }
        if (line.startsWith("--- ")) return;
        if (line.startsWith("+++ ")) {
          const target = line.slice(4);
          if (target.startsWith('"b/')) file.path = unquote(target).replace(/^b\//, "");
          else if (target.startsWith("b/")) file.path = target.slice(2);
          return;
        }
      }
      const hunk = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
      if (hunk && !inHunk) {
        oldLine = Number(hunk[1]);
        newLine = Number(hunk[3]);
        oldLeft = hunk[2] === undefined ? 1 : Number(hunk[2]);
        newLeft = hunk[4] === undefined ? 1 : Number(hunk[4]);
        file.rows.push({ kind: "hunk", text: line });
        return;
      }
      if (line.startsWith("+")) { file.rows.push({ kind: "add", text: line.slice(1), n: newLine++ }); file.add += 1; newLeft -= 1; return; }
      if (line.startsWith("-")) { file.rows.push({ kind: "del", text: line.slice(1), o: oldLine++ }); file.del += 1; oldLeft -= 1; return; }
      if (line.startsWith("\\")) { file.rows.push({ kind: "note", text: line }); return; }
      if (line.startsWith(" ")) { file.rows.push({ kind: "ctx", text: line.slice(1), o: oldLine++, n: newLine++ }); oldLeft -= 1; newLeft -= 1; }
    });
    return files.filter((item) => item.path || item.rows.length);
  }

  function diffBar(add, del) {
    const total = add + del;
    if (!total) return "";
    const green = Math.round((add / total) * 5);
    return `<span class="diff-bar">${Array.from({ length: 5 }, (_, i) => `<i class="${i < green ? "add" : "del"}"></i>`).join("")}</span>`;
  }

  const MAX_DIFF_ROWS = 1200;

  function unifiedRows(file, limit) {
    return file.rows.slice(0, limit).map((row) => {
      if (row.kind !== "hunk" && row.kind !== "note") row.html = row.html || highlightRow(row.text, file.path);
      if (row.kind === "hunk" || row.kind === "note") return `<div class="diff-row hunk"><span class="ln"></span><span class="ln"></span><span class="sign"></span><code>${esc(row.text)}</code></div>`;
      const sign = row.kind === "add" ? "+" : row.kind === "del" ? "−" : "";
      return `<div class="diff-row ${row.kind}"><span class="ln">${row.o || ""}</span><span class="ln">${row.n || ""}</span><span class="sign">${sign}</span><code>${row.html}</code></div>`;
    }).join("");
  }

  function splitRows(file, limit) {
    const out = [];
    const rows = file.rows.slice(0, limit);
    for (let index = 0; index < rows.length;) {
      const row = rows[index];
      if (row.kind === "hunk" || row.kind === "note") {
        out.push(`<div class="diff-split hunk"><span class="ln"></span><code>${esc(row.text)}</code><span class="ln"></span><code></code></div>`);
        index += 1;
        continue;
      }
      if (row.kind === "ctx") {
        const html = highlightRow(row.text, file.path);
        out.push(`<div class="diff-split"><span class="ln">${row.o}</span><code>${html}</code><span class="ln">${row.n}</span><code>${html}</code></div>`);
        index += 1;
        continue;
      }

      const dels = [];
      const adds = [];
      while (index < rows.length && rows[index].kind === "del") dels.push(rows[index++]);
      while (index < rows.length && rows[index].kind === "add") adds.push(rows[index++]);
      for (let k = 0; k < Math.max(dels.length, adds.length); k += 1) {
        const left = dels[k];
        const right = adds[k];
        out.push(`<div class="diff-split"><span class="ln">${left ? left.o : ""}</span><code class="${left ? "del" : ""}">${left ? highlightRow(left.text, file.path) : ""}</code>
          <span class="ln">${right ? right.n : ""}</span><code class="${right ? "add" : ""}">${right ? highlightRow(right.text, file.path) : ""}</code></div>`);
      }
    }
    return out.join("");
  }

  function renderDiff(focusPath) {
    const body = dockBody();
    if (!body || state.tab !== "diff") return;
    const files = parseDiff(state.diffText);
    const add = files.reduce((sum, file) => sum + file.add, 0);
    const del = files.reduce((sum, file) => sum + file.del, 0);
    const split = state.diffMode === "split";
    const toolbar = `<div class="cd-toolbar">
      <div class="cd-title"><b>${esc(t("Изменения в рабочем дереве", "Working tree changes"))}</b></div>
      <div class="cd-seg" role="group" aria-label="${esc(t("Вид", "View"))}">
        <button type="button" data-diff="unified" aria-pressed="${!split}">${esc(t("Общий", "Unified"))}</button>
        <button type="button" data-diff="split" aria-pressed="${split}">${esc(t("Рядом", "Split"))}</button>
      </div>
      <button type="button" class="cd-btn icon" id="code-diff-refresh" title="${esc(t("Обновить", "Refresh"))}">${svg("refresh")}</button>
      <button type="button" class="cd-btn icon" id="code-diff-copy" title="${esc(t("Копировать дифф", "Copy the diff"))}" ${state.diffText ? "" : "disabled"}>${svg("copy")}</button>
    </div>`;
    if (!state.diffRepo) {
      body.innerHTML = toolbar + emptyView({ icon: "branch", title: t("Здесь нет git", "No git repository here"),
        text: t("Изменения сессии всё равно можно откатить во вкладке «Откат».", "Session edits can still be undone in the Undo tab.") });
    } else if (!files.length) {
      body.innerHTML = toolbar + emptyView({ icon: "check", kind: "ok", title: t("Рабочее дерево чистое", "The working tree is clean"),
        text: t("Незакоммиченных изменений нет.", "There are no uncommitted changes.") });
    } else {
      body.innerHTML = `${toolbar}
        <div class="diff-summary"><span>${esc(t(`Файлов: ${files.length}`, `${files.length} files`))}</span>
          <span class="diff-plus">+${add}</span><span class="diff-minus">−${del}</span>${diffBar(add, del)}</div>
        <div class="cd-scroll">${files.map((file, index) => {
          const hidden = file.rows.length > MAX_DIFF_ROWS;
          const open = !focusPath || focusPath === file.path;
          return `<details class="diff-file" ${open ? "open" : ""} data-diff-file="${esc(file.path)}">
            <summary><span class="cx-caret">${svg("chevron")}</span>${fileIcon(file.path)}<span class="diff-name" title="${esc(file.path)}">${esc(file.path)}</span>
              ${file.status ? `<small class="cd-kind">${esc(file.status === "new" ? t("новый", "new") : t("удалён", "deleted"))}</small>` : ""}
              <span class="diff-plus">+${file.add}</span><span class="diff-minus">−${file.del}</span>
              <button type="button" class="cd-btn ghost icon" data-open-file="${esc(file.path)}" title="${esc(t("Открыть файл", "Open the file"))}">${svg("file")}</button>
            </summary>
            <div class="diff-body">${file.binary ? `<p class="code-empty">${esc(t("Двоичный файл", "Binary file"))}</p>` : (split ? splitRows(file, MAX_DIFF_ROWS) : unifiedRows(file, MAX_DIFF_ROWS))}
              ${hidden ? `<p class="code-empty">${esc(t(`Показаны первые ${MAX_DIFF_ROWS} строк из ${file.rows.length}.`, `Showing the first ${MAX_DIFF_ROWS} of ${file.rows.length} lines.`))}
                <button type="button" class="cd-btn" data-diff-all="${index}">${esc(t("Показать всё", "Show everything"))}</button></p>` : ""}</div>
          </details>`;
        }).join("")}</div>`;
      body.querySelectorAll("[data-open-file]").forEach((button) => {
        button.onclick = (event) => { event.preventDefault(); event.stopPropagation(); openFile(button.dataset.openFile); };
      });
      body.querySelectorAll("[data-diff-all]").forEach((button) => {
        button.onclick = () => {
          const file = files[Number(button.dataset.diffAll)];
          const host = button.closest(".diff-body");
          if (file && host) host.innerHTML = split ? splitRows(file, Infinity) : unifiedRows(file, Infinity);
        };
      });
      if (focusPath) body.querySelector(`[data-diff-file="${CSS.escape(focusPath)}"]`)?.scrollIntoView({ block: "start" });
    }
    body.querySelectorAll("[data-diff]").forEach((button) => {
      button.onclick = () => {
        state.diffMode = button.dataset.diff === "split" ? "split" : "unified";
        store.set("memu_code_diff_mode", state.diffMode);
        renderDiff();
      };
    });
    document.getElementById("code-diff-refresh")?.addEventListener("click", () => loadDiff());
    document.getElementById("code-diff-copy")?.addEventListener("click", () => copyValue(state.diffText || ""));
  }

  function previewDocument(kind, content) {
    const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:; script-src 'unsafe-inline';">`;
    const value = String(content || "");
    if (kind === "svg") return `<!DOCTYPE html><html><head>${csp}</head><body style="margin:0;display:grid;place-items:center;min-height:100vh">${value}</body></html>`;
    if (kind === "html" || (kind === "react" && /<html[\s>]/i.test(value))) {
      if (/<head[\s>]/i.test(value)) return value.replace(/<head[^>]*>/i, (match) => match + csp);
      return `<!DOCTYPE html><html><head>${csp}</head><body>${value}</body></html>`;
    }
    return "";
  }

  async function loadArtifacts() {
    const body = dockBody();
    if (!body || state.tab !== "artifact") return;
    if (state.artifact) return renderArtifact(state.artifact);
    body.innerHTML = loadingView(5);
    try {
      const data = await post("/api/code/artifacts", { project_id: projectId(), session: session(), action: "list", access: access() });
      state.artifacts = data.artifacts || [];
      updateBadges();
      renderArtifactList();
    } catch (error) {
      if (state.tab !== "artifact") return;
      body.innerHTML = errorView(error.message, "code-art-retry");
      document.getElementById("code-art-retry")?.addEventListener("click", loadArtifacts);
    }
  }

  function renderArtifactList() {
    const body = dockBody();
    if (!body || state.tab !== "artifact") return;
    if (state.artifact) return renderArtifact(state.artifact);
    if (!state.artifacts.length) {
      body.innerHTML = emptyView({ icon: "artifact", title: t("Артефактов пока нет", "No artifacts yet"),
        text: t("Страницы, схемы SVG и заметки, которые модель создаёт в этой сессии, появятся здесь.", "Pages, SVG diagrams and notes the model creates in this session appear here.") });
      return;
    }
    const rows = [...state.artifacts].sort((a, b) => (b.updated || 0) - (a.updated || 0));
    body.innerHTML = `<div class="cd-toolbar"><div class="cd-title"><b>${esc(t("Артефакты сессии", "Session artifacts"))}</b><small>${rows.length}</small></div>
      <button type="button" class="cd-btn icon" id="code-art-refresh" title="${esc(t("Обновить", "Refresh"))}">${svg("refresh")}</button></div>
      <div class="cd-scroll"><div class="cd-list">${rows.map((item) => `
        <button type="button" class="cd-card" data-artifact="${esc(item.id)}"><span class="cd-kind">${esc(item.kind || "code")}</span>
          <span class="cd-card-body"><b>${esc(item.title || item.id)}</b><small>v${esc(item.version || 1)}${item.updated ? " · " + esc(new Date(item.updated * 1000).toLocaleTimeString(en() ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit" })) : ""}</small></span>
        </button>`).join("")}</div></div>`;
    body.querySelectorAll("[data-artifact]").forEach((button) => { button.onclick = () => openArtifact(button.dataset.artifact); });
    document.getElementById("code-art-refresh")?.addEventListener("click", () => { state.artifact = null; loadArtifacts(); });
  }

  async function openArtifact(id, seed) {
    if (state.dockHidden) setDockHidden(false);
    selectTab("artifact", false);
    const body = dockBody();
    if (body && !(seed && seed.content)) body.innerHTML = loadingView(6);
    try {
      const data = seed && seed.content ? seed : await post("/api/code/artifacts", {
        project_id: projectId(), session: session(), action: "get", id, access: "ask",
      });
      state.artifact = data.artifact || data;
      if (!state.artifacts.some((item) => item.id === state.artifact.id)) state.artifacts.push(state.artifact);
      updateBadges();
      renderArtifact(state.artifact);
    } catch (error) {
      if (body) body.innerHTML = errorView(error.message);
    }
  }

  function renderArtifact(artifact) {
    const body = dockBody();
    if (!body || state.tab !== "artifact") return;
    const preview = previewDocument(artifact.kind, artifact.content);
    const reactNote = artifact.kind === "react" && !preview
      ? `<p class="code-note">${esc(t("JSX без собственного HTML показывается как код: внешние CDN в предпросмотр не подключаются.", "JSX without its own HTML is shown as code: the preview loads no external CDN."))}</p>` : "";
    body.innerHTML = `<div class="cd-toolbar">
      <button type="button" class="cd-btn ghost icon" id="code-artifact-back" title="${esc(t("К списку", "Back to the list"))}">${svg("back")}</button>
      <div class="cd-title"><b>${esc(artifact.title || "")}</b><small>${esc(artifact.kind || "")} · v${esc(artifact.version || 1)}</small></div>
      <button type="button" class="cd-btn icon" id="code-artifact-copy" title="${esc(t("Копировать", "Copy"))}">${svg("copy")}</button>
      <button type="button" class="cd-btn icon" id="code-artifact-export" title="${esc(t("Скачать файлом", "Download"))}">${svg("download")}</button>
      <button type="button" class="cd-btn" id="code-artifact-publish" title="${esc(t("Записать файлом в проект", "Write into the project"))}">${svg("folder")}${esc(t("В проект", "To project"))}</button>
    </div>
    ${reactNote}
    ${preview ? `<iframe class="code-preview" sandbox="allow-scripts" title="${esc(artifact.title || "artifact")}"></iframe>` : `<pre class="code-source">${highlightLines(String(artifact.content || "").split("\n"),
      { markdown: "md", react: "jsx", data: "json", html: "html", svg: "svg" }[artifact.kind] || artifact.title || "").join("\n")}</pre>`}`;
    const frame = body.querySelector(".code-preview");
    if (frame) frame.srcdoc = preview;
    document.getElementById("code-artifact-back")?.addEventListener("click", () => { state.artifact = null; loadArtifacts(); });
    document.getElementById("code-artifact-copy")?.addEventListener("click", () => copyValue(artifact.content || ""));
    document.getElementById("code-artifact-export")?.addEventListener("click", () => {
      const ext = { markdown: "md", svg: "svg", html: "html", react: "jsx", data: "json" }[artifact.kind] || "txt";
      const name = `${(artifact.title || "artifact").replace(/[^\w.-]+/g, "_")}.${ext}`;
      if (typeof downloadTextFile === "function") downloadTextFile(name, artifact.content || "", "text/plain");
      else copyValue(artifact.content || "");
    });
    document.getElementById("code-artifact-publish")?.addEventListener("click", () => publishArtifact(artifact));
  }

  async function publishArtifact(artifact) {
    if (access() !== "full") {
      toast(t("Запись в проект требует полного доступа", "Writing into the project needs Full access"), "warn");
      return;
    }
    const ext = { markdown: "md", svg: "svg", html: "html", react: "jsx", data: "json" }[artifact.kind] || "txt";
    const suggested = /\.\w+$/.test(artifact.title || "") ? artifact.title : `${(artifact.title || "artifact").replace(/[^\w.-]+/g, "_")}.${ext}`;
    const path = await askValue(t("Записать артефакт в проект", "Write the artifact into the project"),
      t("Путь внутри проекта. Существующий файл будет перезаписан.", "A path inside the project. An existing file is overwritten."), suggested, t("Записать", "Write"));
    if (!path) return;
    try {
      const result = await post("/api/code/artifacts", {
        project_id: projectId(), session: session(), action: "publish", id: artifact.id, path, access: "full",
      });
      toast(`${t("Записано", "Wrote")} ${result.path || path}`);
      refreshTree();
    } catch (error) { toast(error.message, "err"); }
  }

  function colorTerminal(text) {
    return String(text || "").split("\n").map((line) => {
      if (/^\$ /.test(line)) return `<span class="t-cmd">${esc(line)}</span>`;
      if (/^\[exit 0\]$/.test(line)) return `<span class="t-ok">${esc(line)}</span>`;
      if (/^\[exit -?\d+\]$/.test(line)) return `<span class="t-err">${esc(line)}</span>`;
      if (/^\[stopped\]$/.test(line)) return `<span class="t-dim">${esc(line)}</span>`;
      if (/\b(error|ошибка|traceback|failed)\b/i.test(line)) return `<span class="t-err">${esc(line)}</span>`;
      return esc(line);
    }).join("\n");
  }

  function setTerminal(text) {
    const log = document.getElementById("code-term-log");
    if (!log) return;
    const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
    log.innerHTML = text ? colorTerminal(text) : `<span class="t-dim">${esc(t("Команды выполняются в папке проекта, в песочнице без секретов. ↑/↓ — история.", "Commands run in the project folder, in a secret-free sandbox. ↑/↓ — history."))}</span>`;
    if (stick) log.scrollTop = log.scrollHeight;
  }

  async function renderTerminal() {
    const body = dockBody();
    if (!body) return;
    const full = access() === "full";
    body.innerHTML = `<div class="cd-toolbar">
      <div class="cd-title"><b>${esc(t("Терминал проекта", "Project terminal"))}</b><small>${esc(full ? "full access" : t("только чтение", "read-only"))}</small></div>
      <button type="button" class="cd-btn icon" id="code-term-clear" title="${esc(t("Очистить экран", "Clear the screen"))}">${svg("trash")}</button>
      <button type="button" class="cd-btn danger" id="code-term-stop" title="${esc(t("Остановить процессы", "Stop the processes"))}">${svg("stop")}${esc(t("Стоп", "Stop"))}</button>
    </div>
    <pre id="code-term-log" class="code-terminal" tabindex="0" aria-live="polite"></pre>
    <form id="code-term-form" class="code-term-form">
      <span class="code-term-prompt" aria-hidden="true">$</span>
      <input id="code-term-input" spellcheck="false" autocomplete="off" aria-label="${esc(t("Команда", "Command"))}" placeholder="${esc(t("git status, npm test, python -m pytest…", "git status, npm test, python -m pytest…"))}">
      <button type="submit" class="cd-btn primary">${esc(t("Запуск", "Run"))}</button>
    </form>
    ${full ? "" : `<div class="code-term-hint">${esc(t("В режиме «С подтверждением» работают только команды чтения. Остальные требуют полного доступа.", "In Ask mode only read-only commands run. Everything else needs Full access."))}</div>`}`;
    setTerminal("");
    const input = document.getElementById("code-term-input");
    document.getElementById("code-term-stop")?.addEventListener("click", stopSandbox);
    document.getElementById("code-term-clear")?.addEventListener("click", () => { state.termCleared = true; setTerminal(""); input?.focus(); });
    input?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        if (!state.termHistory.length) return;
        event.preventDefault();
        state.termIndex = event.key === "ArrowUp"
          ? Math.min(state.termHistory.length - 1, state.termIndex + 1)
          : Math.max(-1, state.termIndex - 1);
        input.value = state.termIndex >= 0 ? state.termHistory[state.termIndex] : "";
        input.setSelectionRange(input.value.length, input.value.length);
      }
    });
    document.getElementById("code-term-form")?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const command = String(input.value || "").trim();
      if (!command) return;
      input.value = "";
      state.termIndex = -1;
      state.termHistory = [command, ...state.termHistory.filter((item) => item !== command)].slice(0, 50);
      store.set("memu_code_term_history", JSON.stringify(state.termHistory));
      const log = document.getElementById("code-term-log");
      if (log) { log.innerHTML += `\n<span class="t-cmd">$ ${esc(command)}</span>\n<span class="t-dim">…</span>`; log.scrollTop = log.scrollHeight; }
      const submit = event.target.querySelector("button[type=submit]");
      if (submit) submit.disabled = true;
      try {
        const result = await post("/api/code/terminal", {
          project_id: projectId(), session: session(), action: "run", command, access: access(),
        });
        state.termCleared = false;
        setTerminal(result.scrollback || ((result.stdout || "") + (result.stderr || "")));
        refreshMeta();
        if (!/^(git status|git diff|ls|dir|cat|type)\b/i.test(command)) refreshTree();
      } catch (error) {
        if (log) { log.innerHTML += `\n<span class="t-err">${esc(error.message)}</span>\n`; log.scrollTop = log.scrollHeight; }
      } finally {
        if (submit) submit.disabled = false;
        document.getElementById("code-term-input")?.focus();
      }
    });
    try {
      const data = await post("/api/code/terminal", { project_id: projectId(), session: session(), action: "read" });
      if (!state.termCleared) setTerminal(data.scrollback || "");
    } catch (e) {  }
  }

  function renderTasks() {
    const body = dockBody();
    if (!body || state.tab !== "tasks") return;
    const tasks = state.tasks || [];
    if (!tasks.length) {
      body.innerHTML = emptyView({ icon: "tasks", title: t("Фоновых задач нет", "No background tasks"),
        text: t("Долгие команды — сборка, тесты, сервер — модель запускает здесь, не блокируя разговор.", "Long commands — builds, tests, servers — run here without blocking the conversation.") });
      return;
    }
    const scroll = body.querySelector(".cd-scroll");
    const keep = scroll ? scroll.scrollTop : 0;
    body.innerHTML = `<div class="cd-toolbar"><div class="cd-title"><b>${esc(t("Фоновые задачи", "Background tasks"))}</b><small>${tasks.length}</small></div>
      <button type="button" class="cd-btn danger" id="code-tasks-stop">${svg("stop")}${esc(t("Остановить всё", "Stop all"))}</button></div>
      <div class="cd-scroll"><div class="cd-list">${tasks.map((task) => {
        const kind = task.running ? "run" : Number(task.exit_code) === 0 ? "ok" : "fail";
        const label = task.running ? t("идёт", "running") : `exit ${task.exit_code}`;
        const output = ((task.stdout || "") + (task.stderr || "")).slice(-3000);
        return `<article class="code-task">
          <header><span class="cd-dot ${kind}"></span><b title="${esc(task.command || "")}">${esc(task.label || task.command || task.id)}</b><span class="cd-state">${esc(label)}</span>
            ${task.running ? `<button type="button" class="cd-btn" data-cancel="${esc(task.id)}">${esc(t("Отмена", "Cancel"))}</button>` : ""}</header>
          ${output ? `<pre>${esc(output)}</pre>` : ""}
        </article>`;
      }).join("")}</div></div>`;
    const next = body.querySelector(".cd-scroll");
    if (next) next.scrollTop = keep;
    body.querySelectorAll("pre").forEach((pre) => { pre.scrollTop = pre.scrollHeight; });
    document.getElementById("code-tasks-stop")?.addEventListener("click", stopSandbox);
    body.querySelectorAll("[data-cancel]").forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          await post("/api/pg/tools", { tool: "code_task", args: { action: "cancel", id: button.dataset.cancel, project_id: projectId() }, session: session(), access: "full", project_id: projectId() });
          refreshMeta();
        } catch (error) { toast(error.message, "err"); button.disabled = false; }
      };
    });
  }

  async function countChanges() {
    if (!session()) { state.changesCount = 0; updateBadges(); return []; }
    try {
      const entries = (await post("/api/code/checkpoints", { session: session() })).entries || [];
      state.changesCount = entries.length;
      updateBadges();
      return entries;
    } catch (e) { return null; }
  }

  async function renderChanges() {
    const body = dockBody();
    if (!body || state.tab !== "changes") return;
    if (!session()) {
      body.innerHTML = emptyView({ icon: "undo", title: t("Нет сессии", "No session"), text: t("Откройте или начните сессию кода.", "Open or start a code session.") });
      return;
    }
    body.innerHTML = loadingView(5);
    const entries = await countChanges();
    if (state.tab !== "changes") return;
    if (entries === null) {
      body.innerHTML = errorView(t("Точки отката недоступны", "Undo points are unavailable"), "code-undo-retry");
      document.getElementById("code-undo-retry")?.addEventListener("click", renderChanges);
      return;
    }
    if (!entries.length) {
      body.innerHTML = emptyView({ icon: "undo", title: t("Откатывать нечего", "Nothing to undo"),
        text: t("Перед первой правкой каждого файла MemuCode сохраняет его копию — откат работает даже без git.", "Before the first edit of each file MemuCode keeps a copy — undo works even without git.") });
      return;
    }
    body.innerHTML = `<div class="cd-toolbar"><div class="cd-title"><b>${esc(t("Изменённые сессией файлы", "Files changed by this session"))}</b><small>${entries.length}</small></div>
      <button type="button" class="cd-btn ghost" id="code-undo-toggle">${esc(t("Снять все", "Clear all"))}</button></div>
      <div class="cd-scroll">${entries.map((entry) => `<label class="code-change">
        <input type="checkbox" data-rollback="${esc(entry.path)}" checked ${entry.restorable === false ? "disabled" : ""}>
        <span title="${esc(entry.path)}">${esc(entry.path)}</span>
        <small class="${entry.existed ? "modified" : "created"}">${entry.label === "manual_edit" ? esc(t("ваша правка", "your edit")) : entry.existed ? esc(t("изменён", "modified")) : esc(t("создан", "created"))}</small></label>`).join("")}</div>
      <div class="cd-foot">
        <button type="button" class="cd-btn" id="code-rollback-some">${svg("undo")}${esc(t("Откатить выбранные", "Undo selected"))}</button>
        <span style="flex:1"></span>
        <button type="button" class="cd-btn danger" id="code-rollback-all">${esc(t("Откатить всё", "Undo everything"))}</button>
      </div>`;
    const boxes = () => [...body.querySelectorAll("[data-rollback]:not(:disabled)")];
    document.getElementById("code-undo-toggle")?.addEventListener("click", (event) => {
      const all = boxes().every((box) => box.checked);
      boxes().forEach((box) => { box.checked = !all; });
      event.currentTarget.textContent = all ? t("Отметить все", "Select all") : t("Снять все", "Clear all");
    });
    const rollback = async (paths) => {
      const count = paths.length || entries.length;
      const ok = await confirmAction(t("Откатить изменения?", "Undo the changes?"),
        t(`Файлов: ${count}. Изменённые файлы вернутся к состоянию до правок сессии, созданные будут удалены.`,
          `${count} files. Modified files return to their state before the session; created files are deleted.`), t("Откатить", "Undo"));
      if (!ok) return;
      try {
        const result = await post("/api/code/rollback", { session: session(), project_id: projectId(), paths });
        const total = (result.restored || []).length + (result.removed || []).length;
        toast(`${t("Откачено", "Reverted")}: ${total}${(result.failed || []).length ? " · " + t("не удалось", "failed") + ": " + result.failed.length : ""}`,
          (result.failed || []).length ? "warn" : "ok");
        refreshTree();
        renderChanges();
      } catch (error) { toast(error.message, "err"); }
    };
    document.getElementById("code-rollback-all")?.addEventListener("click", () => rollback([]));
    document.getElementById("code-rollback-some")?.addEventListener("click", () => {
      const paths = boxes().filter((box) => box.checked).map((box) => box.dataset.rollback);
      if (!paths.length) return toast(t("Ничего не выбрано", "Nothing selected"), "warn");
      rollback(paths);
    });
  }

  async function loadSkills(force) {
    if ((!state.skills.length || force) && projectId()) {
      const data = await post("/api/code/skills", { project_id: projectId() });
      state.skills = data.skills || [];
    }
    return state.skills;
  }

  async function renderSkills(target) {
    const body = target || dockBody();
    if (!body) return;
    if (!target && state.tab !== "skills") return;
    if (!state.skills.length) body.innerHTML = loadingView(5);
    try { await loadSkills(); } catch (error) {
      body.innerHTML = errorView(error.message, "code-skills-retry");
      document.getElementById("code-skills-retry")?.addEventListener("click", () => renderSkills(target));
      return;
    }
    const disabled = new Set(skillState());
    const q = state.skillFilter.trim().toLowerCase();
    const rows = state.skills.filter((skill) => !q || `${skill.id} ${skill.title} ${skill.description}`.toLowerCase().includes(q));
    const on = state.skills.filter((skill) => !disabled.has(skill.id)).length;
    body.innerHTML = `<div class="cd-toolbar">
      <input class="cd-filter" id="code-skill-filter" type="search" placeholder="${esc(t("Найти скилл", "Find a skill"))}" value="${esc(state.skillFilter)}" aria-label="${esc(t("Найти скилл", "Find a skill"))}">
      <small class="cd-state">${on}/${state.skills.length}</small></div>
      <p class="code-note">${esc(t("Проектные скиллы из .memuai/skills перекрывают встроенные. Выключение действует для этого проекта.",
        "Project skills in .memuai/skills override built-ins. Disabling applies to this project."))}</p>
      <div class="cd-scroll">${rows.map((skill) => `<label class="code-skill">
        <span class="cd-switch"><input type="checkbox" data-skill="${esc(skill.id)}" ${disabled.has(skill.id) ? "" : "checked"}><i></i></span>
        <span><b>${esc(skill.title || skill.id)}</b><em>${esc(skill.description || "")}</em><small>${esc(skill.source || "builtin")} · ${esc(skill.id)}</small></span>
      </label>`).join("") || emptyView({ icon: "skills", title: projectId() ? t("Ничего не найдено", "Nothing found") : t("Проект не выбран", "No project selected") })}</div>`;
    const filter = body.querySelector("#code-skill-filter");
    filter?.addEventListener("input", () => {
      state.skillFilter = filter.value;
      const at = filter.selectionStart;
      renderSkills(target).then(() => {
        const next = body.querySelector("#code-skill-filter");
        if (next) { next.focus(); next.setSelectionRange(at, at); }
      });
    });
    body.querySelectorAll("[data-skill]").forEach((box) => {
      box.onchange = () => {
        const next = new Set(skillState());
        if (box.checked) next.delete(box.dataset.skill);
        else next.add(box.dataset.skill);
        const all = { ...(pref("code_skill_state", {}) || {}) };
        all[projectId() || "_"] = { disabled: [...next] };
        savePreferences({ code_skill_state: all });
        const counter = body.querySelector(".cd-toolbar .cd-state");
        if (counter) counter.textContent = `${state.skills.length - next.size}/${state.skills.length}`;
      };
    });
  }

  function openSkills() {
    if (S.mode === "code") {
      if (state.dockHidden) setDockHidden(false);
      selectTab("skills");
    } else {
      panel(`<h2>${esc(t("Скиллы кода", "Code skills"))}</h2><div id="code-skills-panel" class="code-dock-body" style="max-height:60vh"></div>`);
      renderSkills(document.getElementById("code-skills-panel"));
    }
  }

  async function stopSandbox() {
    try {
      await post("/api/code/sandbox/stop", { session: session() });
      toast(t("Процессы песочницы остановлены", "Sandbox processes stopped"), "warn");
      refreshMeta();
    } catch (error) { toast(error.message, "err"); }
  }

  function actionItems() {
    return [
      { kind: "action", icon: "sidebar", label: state.explorerHidden ? t("Показать файлы проекта", "Show project files") : t("Свернуть файлы проекта", "Collapse project files"), detail: "Ctrl+Shift+E", run: () => setExplorerHidden(!state.explorerHidden) },
      { kind: "action", icon: "dock", label: state.dockHidden ? t("Показать правую панель", "Show the side panel") : t("Скрыть правую панель", "Hide the side panel"), detail: "Ctrl+Shift+D", run: () => setDockHidden(!state.dockHidden) },
      { kind: "action", icon: "terminal", label: t("Открыть терминал", "Open the terminal"), detail: "Ctrl+`", run: () => focusTerminal() },
      { kind: "action", icon: "diff", label: t("Изменения рабочего дерева", "Working tree changes"), detail: "Alt+2", run: () => { if (state.dockHidden) setDockHidden(false); selectTab("diff"); } },
      { kind: "action", icon: "undo", label: t("Откатить правки сессии", "Undo session edits"), detail: "Alt+6", run: () => { if (state.dockHidden) setDockHidden(false); selectTab("changes"); } },
      { kind: "action", icon: "refresh", label: t("Обновить дерево файлов", "Refresh the file tree"), detail: "", run: () => refreshTree() },
      { kind: "action", icon: "stop", label: t("Остановить процессы песочницы", "Stop sandbox processes"), detail: "", run: () => stopSandbox() },
    ];
  }

  function matchScore(text, query) {
    const hay = String(text || "").toLowerCase();
    if (!query) return 1;
    const at = hay.indexOf(query);
    if (at >= 0) return 1000 - at - (hay.length / 100);
    let position = 0;
    for (const char of query) {
      position = hay.indexOf(char, position);
      if (position < 0) return 0;
      position += 1;
    }
    return 100 - hay.length / 100;
  }

  function markMatch(text, query) {
    const value = String(text || "");
    const at = query ? value.toLowerCase().indexOf(query) : -1;
    if (at < 0) return esc(value);
    return esc(value.slice(0, at)) + "<mark>" + esc(value.slice(at, at + query.length)) + "</mark>" + esc(value.slice(at + query.length));
  }

  function paletteItems(raw) {
    let query = String(raw || "").trim().toLowerCase();
    let only = "";
    if (query.startsWith("/") || query.startsWith(">")) { only = "command"; query = query.slice(1).trim(); }
    else if (query.startsWith("#")) { only = "skill"; query = query.slice(1).trim(); }
    const commands = (typeof CODE_SLASH_COMMANDS !== "undefined" ? CODE_SLASH_COMMANDS : []).map((row) => ({
      kind: "command", icon: "command", label: row[0], detail: [row[1], row[2]].map((v) => (typeof cmdText === "function" ? cmdText(v) : v)).filter(Boolean).join(" — "), run: row[0],
    }));
    const skills = state.skills.map((skill) => ({ kind: "skill", icon: "skills", label: skill.id, detail: skill.title || skill.description || "", run: skill.id }));
    const files = state.tree.map((path) => ({ kind: "file", icon: "file", label: path.split("/").pop(), detail: path, run: path }));
    const groups = only === "command" ? [["command", [...actionItems(), ...commands]]]
      : only === "skill" ? [["skill", skills]]
        : [["file", files], ["command", [...actionItems(), ...commands]], ["skill", skills]];
    const out = [];
    groups.forEach(([group, items]) => {
      const scored = items.map((item) => ({ item, score: Math.max(matchScore(item.label, query) * 2, matchScore(item.detail, query)) }))
        .filter((row) => row.score > 0).sort((a, b) => b.score - a.score)
        .slice(0, group === "file" ? (query ? 30 : 8) : 12);
      scored.forEach((row, index) => out.push({ ...row.item, group: index === 0 ? group : "" }));
    });
    state.paletteQuery = query;
    return out.slice(0, 60);
  }

  function renderPalette() {
    const list = document.getElementById("code-palette-list");
    const input = document.getElementById("code-palette-input");
    if (!list || !input) return;
    state.palette = paletteItems(input.value);
    state.paletteIndex = clamp(state.paletteIndex, 0, Math.max(0, state.palette.length - 1));
    const names = { file: t("Файлы", "Files"), command: t("Команды", "Commands"), skill: t("Скиллы", "Skills") };
    const query = state.paletteQuery || "";
    list.innerHTML = state.palette.map((item, index) => `${item.group ? `<div class="cp-group">${esc(names[item.group] || item.group)}</div>` : ""}
      <button type="button" role="option" id="cp-${index}" aria-selected="${index === state.paletteIndex}" class="${index === state.paletteIndex ? "active" : ""}" data-index="${index}">
        <span class="cp-icon">${svg(item.icon)}</span><span class="cp-text"><span>${markMatch(item.label, query)}</span>${item.detail ? `<em>${markMatch(item.detail, query)}</em>` : ""}</span>
        ${item.kind === "file" && state.git[item.run] ? gitMark(item.run) : ""}${item.kind === "action" && item.detail ? `<small>${esc(item.detail)}</small>` : ""}
      </button>`).join("") || emptyView({ icon: "search", title: t("Ничего не найдено", "Nothing matches"), text: t("/ — только команды, # — только скиллы", "/ commands only, # skills only") });
    input.setAttribute("aria-activedescendant", state.palette.length ? `cp-${state.paletteIndex}` : "");
    list.querySelector(".active")?.scrollIntoView({ block: "nearest" });
    list.querySelectorAll("[data-index]").forEach((button) => {
      button.onclick = () => choosePalette(Number(button.dataset.index));
      button.onmousemove = () => {
        const index = Number(button.dataset.index);
        if (index === state.paletteIndex) return;
        state.paletteIndex = index;
        list.querySelectorAll("[data-index]").forEach((node) => {
          const on = Number(node.dataset.index) === index;
          node.classList.toggle("active", on);
          node.setAttribute("aria-selected", String(on));
        });
      };
    });
    fillIcons(document.getElementById("code-palette"));
  }

  function openPalette(prefill = "") {
    const host = document.getElementById("code-palette");
    if (!host || S.mode !== "code") return;
    state.paletteReturn = document.activeElement;
    host.hidden = false;
    const input = document.getElementById("code-palette-input");
    if (input) { input.value = prefill; input.focus(); }
    state.paletteIndex = 0;
    renderPalette();
    if (!state.tree.length && projectId()) refreshTree().then(renderPalette);
  }

  function closePalette() {
    const host = document.getElementById("code-palette");
    if (!host || host.hidden) return;
    host.hidden = true;
    const back = state.paletteReturn;
    if (back && back.isConnected && typeof back.focus === "function") back.focus({ preventScroll: true });
  }

  function choosePalette(index) {
    const item = state.palette[index];
    closePalette();
    if (!item) return;
    if (item.kind === "file") return openFile(item.run);
    if (item.kind === "action") return item.run();
    if (item.kind === "skill") {
      const input = document.getElementById("input");
      if (input) {
        input.value = (en() ? "Use the code skill " : "Используй скилл кода ") + item.run + " ";
        input.dispatchEvent(new Event("input"));
        input.focus();
      }
      return;
    }
    if (typeof handleCommand === "function") handleCommand(item.run);
  }

  function focusTerminal() {
    if (state.dockHidden) setDockHidden(false);
    selectTab("terminal");
    setTimeout(() => document.getElementById("code-term-input")?.focus(), 60);
  }

  async function activate() {
    state.loaded = false;
    await refreshTree();
    try { await loadSkills(true); } catch (e) { state.skills = state.skills || []; }
    countChanges();
    await refreshMeta();
    renderDock();
  }

  function sync() {
    placeExplorer();
    if (S.mode !== "code") { closePalette(); return; }
    fillChrome();
    fillIcons(document.getElementById("code-explorer"));
    fillIcons(document.getElementById("code-dock"));

    if (state.treeFor !== undefined && state.treeFor !== projectId()) {
      if (editorDirty()) toast(t("Несохранённые правки в редакторе закрыты вместе с проектом", "Unsaved editor changes were closed with the project"), "warn");
      resetProjectState();
      resetSessionState();
      activate();
      return;
    }
    if (state.sessionFor !== undefined && state.sessionFor !== session()) {

      resetSessionState();
      renderDock();
      countChanges();
    }
    if (state.sessionFor === undefined) state.sessionFor = session();
    if (!state.loaded) refreshTree();
    else renderTree();
    renderTabs();
  }

  let treeTimer = null;
  function onToolResult(call, result) {
    const name = String(call?.tool || "");
    if (name === "code_artifact" && (result?.id || result?.artifact)) {
      const artifact = result.artifact || result;
      if (artifact && artifact.id) openArtifact(artifact.id, artifact.content ? artifact : null);
    }
    const writes = ["work_write", "work_patch", "work_insert", "work_replace_lines", "work_append", "work_delete", "work_move", "work_mkdir", "code_run", "code_terminal", "code_terminal_run", "code_task"];
    if (writes.includes(name) || /^work_/.test(name) && !/read|list|search|tree/.test(name)) {
      clearTimeout(treeTimer);
      treeTimer = setTimeout(() => { refreshTree(); countChanges(); if (state.tab === "diff") loadDiff(); if (state.tab === "changes") renderChanges(); }, 250);
    }
    if (name === "code_read_cached" || name === "code_context" || result?.saved_chars) refreshMeta();
    if (state.tab === "terminal" && /^code_terminal/.test(name) && result?.scrollback !== undefined) {

      setTerminal(result.scrollback);
    }
    const touched = [result?.path, result?.from, result?.to].filter(Boolean);
    if (state.edit && touched.includes(state.edit.path)) onEditedFileChanged();
    else if (state.tab === "file" && state.file && touched.includes(state.file.path)) openFile(state.file.path, state.file.start_line || 1);
  }

  function bind() {
    fillIcons(document);
    document.getElementById("code-add-project")?.addEventListener("click", () => document.getElementById("work-add-project")?.click());
    document.getElementById("code-access")?.addEventListener("click", () => { if (typeof openWorkAccessChooser === "function") openWorkAccessChooser(); });
    document.getElementById("code-plan")?.addEventListener("click", () => { if (typeof setWorkPlanMode === "function") setWorkPlanMode(!S.workPlanMode); });
    document.getElementById("code-run")?.addEventListener("click", () => { if (typeof handleCommand === "function") handleCommand("/test"); });
    document.getElementById("code-stop")?.addEventListener("click", stopSandbox);
    document.getElementById("code-help")?.addEventListener("click", () => { if (typeof showCodeIntro === "function") showCodeIntro(); });
    document.getElementById("code-mcp-btn")?.addEventListener("click", () => { if (typeof showMcpPanel === "function") showMcpPanel(); });
    document.getElementById("code-palette-btn")?.addEventListener("click", () => openPalette());
    document.getElementById("code-explorer-toggle")?.addEventListener("click", () => setExplorerHidden(!state.explorerHidden));
    document.getElementById("code-dock-toggle")?.addEventListener("click", () => setDockHidden(!state.dockHidden));
    document.getElementById("code-dock-close")?.addEventListener("click", () => setDockHidden(true));
    document.getElementById("code-dock-expand")?.addEventListener("click", () => setDockExpanded(!state.dockExpanded));
    setDockExpanded(state.dockExpanded);

    window.addEventListener("beforeunload", (event) => { if (editorDirty()) { event.preventDefault(); event.returnValue = ""; } });
    document.getElementById("code-dock-backdrop")?.addEventListener("click", () => setDockHidden(true));
    document.getElementById("code-tree-collapse")?.addEventListener("click", () => setExplorerHidden(!state.explorerHidden));
    document.getElementById("code-tree-refresh")?.addEventListener("click", () => { state.loaded = false; refreshTree(); });
    const tree = document.getElementById("code-tree");
    tree?.addEventListener("click", onTreeClick);
    tree?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        const row = event.target.closest(".cx-row");
        if (row) { event.preventDefault(); row.click(); }
        return;
      }
      onTreeKey(event);
    });
    tree?.addEventListener("contextmenu", (event) => {
      const row = event.target.closest("[data-path], [data-dir]");
      if (row) fileMenu(row.dataset.path || row.dataset.dir, event, !!row.dataset.dir);
    });
    document.querySelectorAll("[data-explorer-tab]").forEach((button) => {
      button.addEventListener("click", () => { state.explorerTab = button.dataset.explorerTab; renderTree(); });
    });
    const filter = document.getElementById("code-filter");
    filter?.addEventListener("input", () => { state.filter = filter.value || ""; renderTree(); });
    filter?.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") { event.preventDefault(); document.querySelector("#code-tree .cx-row")?.focus(); }
      if (event.key === "Escape" && filter.value) { event.preventDefault(); event.stopPropagation(); filter.value = ""; state.filter = ""; renderTree(); }
      if (event.key === "Enter") {
        const first = document.querySelector("#code-tree .cx-row.file");
        if (first) { event.preventDefault(); openFile(first.dataset.path); }
      }
    });
    document.getElementById("code-dock-tabs")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-tab]");
      if (button) selectTab(button.dataset.tab);
    });
    document.getElementById("code-dock-tabs")?.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = TAB_IDS.indexOf(state.tab);
      const next = event.key === "Home" ? 0 : event.key === "End" ? TAB_IDS.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + TAB_IDS.length) % TAB_IDS.length;
      selectTab(TAB_IDS[next]);
      document.getElementById("code-tab-" + TAB_IDS[next])?.focus();
    });
    document.getElementById("code-project")?.addEventListener("change", async (event) => {
      if (!event.target.value) return;
      if (typeof canChangeConversation === "function" && !canChangeConversation()) { fillChrome(); return; }

      if (!(await leaveEditor())) { fillChrome(); return; }
      try {
        const data = await api("/api/work/projects/select", { method: "POST", body: { id: event.target.value } });
        S.workspaces = data;
        S.workspaces.active_id = data.active_id;
      } catch (error) { toast(error.message, "err"); fillChrome(); return; }
      resetProjectState();
      if (typeof newChat === "function") newChat(false);
      if (typeof updateWorkUI === "function") updateWorkUI();
      activate();
    });
    document.getElementById("code-palette")?.addEventListener("mousedown", (event) => {
      if (event.target.id === "code-palette") closePalette();
    });
    const paletteInput = document.getElementById("code-palette-input");
    paletteInput?.addEventListener("input", () => { state.paletteIndex = 0; renderPalette(); });
    paletteInput?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closePalette(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); state.paletteIndex = (state.paletteIndex + 1) % Math.max(1, state.palette.length); renderPalette(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); state.paletteIndex = (state.paletteIndex - 1 + state.palette.length) % Math.max(1, state.palette.length); renderPalette(); }
      else if (event.key === "Enter") { event.preventDefault(); choosePalette(state.paletteIndex); }
      else if (event.key === "Tab") event.preventDefault();
    });
    document.addEventListener("keydown", (event) => {
      if (!document.body.classList.contains("code-mode") || modalOpen()) return;
      const ctrl = event.ctrlKey || event.metaKey;

      const HK = window.MemuHotkeys;
      const hit = (id, legacy) => (HK ? HK.match(id, event) : legacy);
      const commands = hit("code_commands", ctrl && !event.altKey && event.shiftKey && event.code === "KeyP");
      if (commands || hit("code_palette", ctrl && !event.altKey && !event.shiftKey && event.code === "KeyP")) {
        event.preventDefault();
        if (document.getElementById("code-palette")?.hidden === false) closePalette();
        else openPalette(commands ? "/" : "");
        return;
      }
      if (hit("code_explorer", ctrl && event.shiftKey && event.code === "KeyE")) { event.preventDefault(); setExplorerHidden(!state.explorerHidden); return; }
      if (hit("code_dock", ctrl && event.shiftKey && event.code === "KeyD")) { event.preventDefault(); setDockHidden(!state.dockHidden); return; }
      if (hit("code_terminal", ctrl && event.code === "Backquote")) { event.preventDefault(); focusTerminal(); return; }
      if (event.altKey && !ctrl && !event.shiftKey && /^Digit[1-7]$/.test(event.code)) {
        event.preventDefault();
        if (state.dockHidden) setDockHidden(false);
        selectTab(TAB_IDS[Number(event.code.slice(-1)) - 1]);
        return;
      }
      if (event.key === "Escape" && narrow() && !state.dockHidden && document.getElementById("code-dock")?.contains(document.activeElement)) {
        event.preventDefault();
        setDockHidden(true);
      }
    });
    window.addEventListener("resize", () => { if (S.mode === "code") setDockWidth(state.dockWidth); });

    window.matchMedia("(max-width: 980px)").addEventListener("change", (event) => {
      if (event.matches) state.dockNarrowHidden = true;
      if (S.mode !== "code") return;
      placeExplorer();
      if (!state.dockHidden) renderDock();
    });
    setInterval(() => { if (S.mode === "code" && !document.hidden) refreshMeta(); }, 4000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();
  return { activate, sync, onToolResult, openSkills, openPalette, refreshTree, setExplorerHidden, setDockHidden, selectTab,
    openFile };
})();
window.MemuCode = MemuCode;
