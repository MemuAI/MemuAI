

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, $$, esc, md, tr, icon, haptic, toast, api, followJob, openSheet, actionSheet, promptSheet, confirmDialog,
      openStack, closeStack, updateStackTop, setTop, bindRichContent, ago, duration, modelName, longPress, sleep } = kit;
    const CODE = { query: "", catalog: null, catalogAt: 0, openId: "" };
    const LOCAL_ONLY = new Set(["voice", "paste", "copy", "terminal-setup", "vim", "keybindings", "theme", "statusline", "install", "run", "exit", "quit"]);

    const MODES = [
      ["default", "Спрашивать", "Ask first", "Правки и команды — после вашего разрешения", "Edits and commands after your approval"],
      ["acceptEdits", "Принимать правки", "Accept edits", "Правки файлов без вопросов, команды — с разрешением", "File edits without asking, commands with approval"],
      ["plan", "План", "Plan", "Сначала исследовать и согласовать план", "Research and agree on a plan first"],
      ["auto", "Авто", "Auto", "Безопасное — само, рискованное — с вопросом", "Safe steps on its own, risky ones ask"],
      ["bypassPermissions", "Без разрешений", "Bypass", "Всё без вопросов — опасно", "Everything without asking — dangerous"],
    ];
    const modeRow = (id) => MODES.find((row) => row[0] === id) || MODES[0];
    const modeLabel = (id) => tr(modeRow(id)[1], modeRow(id)[2]);

    async function loadSessions() {
      try {
        const data = await api("code.sessions");
        S.codeSessions = data.sessions || []; S.codeCli = data.cli; S.codeDisabled = "";
        kit.renderTabbar();
        return true;
      } catch (error) {
        if (error.code === "code_disabled") S.codeDisabled = error.message;
        return false;
      }
    }
    function renderTab(view) {
      setTop("MemuCode", `<button class="icon-btn" id="code-new" aria-label="${esc(tr("Новая сессия", "New session"))}" title="${esc(tr("Новая сессия", "New session"))}">${icon("plus")}</button>
        <button class="icon-btn" id="code-refresh" aria-label="${esc(tr("Обновить", "Refresh"))}" title="${esc(tr("Обновить", "Refresh"))}">${icon("retry")}</button>`);
      $("#code-refresh").onclick = async () => { haptic("light"); await loadSessions(); drawList(view); };
      $("#code-new").onclick = () => newSession();
      view.innerHTML = `<div class="search">${icon("search")}<input id="code-search" type="search" placeholder="${esc(tr("Поиск сессий", "Search sessions"))}" value="${esc(CODE.query)}" autocomplete="off"></div>
        <div id="code-list"><div class="empty"><div class="typing"><i></i><i></i><i></i></div></div></div>
        <button class="fab" id="code-fab" aria-label="${esc(tr("Новая сессия", "New session"))}">${icon("plus")}</button>`;
      $("#code-search", view).oninput = (event) => { CODE.query = event.target.value; drawList(view); };
      $("#code-fab", view).onclick = () => newSession();
      loadSessions().then(() => drawList(view));
      S.timers.code = setInterval(async () => {
        if (!document.hidden && S.tab === "code" && (!S.stack.length || kit.isSplit())) { await loadSessions(); drawList(view); }
      }, 4000);
    }
    function sessionCell(s) {
      const status = s.asking ? `<span style="color:var(--warn)">${esc(tr("ждёт ответа", "needs you"))}</span>`
        : s.busy ? `<span style="color:var(--accent)">${esc(tr("работает", "working"))}</span>` : "";
      const where = s.live ? (s.host ? tr("с телефона", "from the phone") : tr("в терминале", "in a terminal")) : "";
      return `<button class="chat-cell code-cell ${s.live ? "live" : ""} ${CODE.openId === s.id ? "current" : ""}" data-session="${esc(s.id)}"><div class="avatar sq">${esc((s.project || "?").slice(0, 2).toUpperCase())}</div>
        <div class="chat-main"><div class="chat-top"><div class="chat-title">${esc(s.title || s.project || s.id)}</div><span class="chat-time">${esc(ago((s.updated || 0) * 1000))}</span></div>
        <div class="chat-top"><div class="chat-snippet">${s.live ? '<span class="live-dot"></span> ' : ""}${esc(s.project)}${s.model ? " · " + esc(modelName(s.model)) : ""}${where ? " · " + esc(where) : ""}${status ? " · " + status : ""}</div></div></div></button>`;
    }
    function drawList(view) {
      const box = $("#code-list", view); if (!box) return;

      const fab = $("#code-fab", view); if (fab) fab.classList.toggle("fab-off", !!S.codeDisabled || !(S.codeSessions || []).length);
      if (S.codeDisabled) { box.innerHTML = `<div class="empty"><div class="empty-art">${icon("lock")}</div><h3>${esc(tr("Управление выключено", "Control is off"))}</h3><p>${esc(S.codeDisabled)}</p></div>`; return; }
      const q = CODE.query.trim().toLowerCase();
      const rows = (S.codeSessions || []).filter((s) => !q || `${s.title} ${s.project} ${s.root} ${s.last} ${s.model}`.toLowerCase().includes(q));
      const live = rows.filter((s) => s.live), recent = rows.filter((s) => !s.live);
      if (!S.codeSessions.length) {
        box.innerHTML = `<div class="empty"><div class="empty-art">${icon("terminal")}</div><h3>${esc(tr("Сессий пока нет", "No sessions yet"))}</h3>
          <p>${esc(tr("Начните сессию прямо отсюда — MemuCode запустится на компьютере в выбранной папке. Или запустите memucode в терминале: сессия появится здесь.", "Start a session right here — MemuCode starts on the computer in the folder you pick. Or run memucode in a terminal: the session shows up here."))}</p>
          <button class="btn" id="code-empty-new">${icon("plus")}${esc(tr("Новая сессия", "New session"))}</button></div>`;
        $("#code-empty-new", box).onclick = () => newSession();
        return;
      }
      box.innerHTML = (live.length ? `<div class="section-title" style="margin-top:6px">${esc(tr("Открыты сейчас", "Open now"))}</div><div class="group">${live.map(sessionCell).join("")}</div>` : "") +
        (recent.length ? `<div class="section-title">${esc(tr("Недавние сессии", "Recent sessions"))}</div><div class="group">${recent.slice(0, 100).map(sessionCell).join("")}</div>` : "") +
        (!rows.length ? `<div class="empty"><h3>${esc(tr("Ничего не найдено", "Nothing found"))}</h3></div>` : "") +
        `<p class="section-note">${esc(tr("Любую сессию можно продолжить с телефона: если она не открыта в терминале, MemuCode запустится на компьютере сам — с командами, вопросами о разрешениях и кнопкой «Стоп». Долгое нажатие — переименовать, удалить, файлы.", "Any session can be continued from the phone: if it is not open in a terminal, MemuCode starts on the computer by itself — with commands, permission questions and a Stop button. Long-press to rename, delete or browse files."))}</p>`;
      $$("[data-session]", box).forEach((cell) => {
        cell.onclick = () => { openSession(cell.dataset.session); drawList(view); };
        longPress(cell, () => sessionMenu(cell.dataset.session, () => { loadSessions().then(() => drawList(view)); }));
      });
    }
    function sessionMenu(id, after, T) {
      const s = (S.codeSessions || []).find((x) => x.id === id) || (T && { id, title: T.title, live: !!T.live, host: T.live && T.live.host, rc: T.rc }) || { id };
      const items = [
        { label: tr("Файлы проекта", "Project files"), icon: "folder", run: () => openFiles({ session_id: id, rc: s.rc || "" }, s.project || "") },
        { label: tr("Изменения (git)", "Changes (git)"), icon: "list", run: () => openChanges({ session_id: id, rc: s.rc || "" }) },
        { label: tr("Переименовать", "Rename"), icon: "edit", run: async () => {
          const name = await promptSheet(tr("Название сессии", "Session name"), { value: s.title || "", max: 120 });
          if (!name) return;
          try {
            if (s.live && s.rc) await api("code.command", { rc: s.rc, command: { type: "prompt", text: "/rename " + name } });
            else await api("code.rename", { session_id: id, name });
            toast(tr("Переименовано", "Renamed"), "ok");
            if (T) { T.title = name; updateStackTop(T.entry, { title: name }); }
            if (after) after();
          } catch (error) { toast(error.message, "err"); }
        } },
      ];
      if (s.live && s.host) items.push({ label: tr("Закрыть сессию на компьютере", "Close the session on the computer"), icon: "close", sub: tr("MemuCode, запущенный с телефона, завершится; разговор сохранится", "The phone-started MemuCode exits; the conversation is kept"),
        run: async () => { try { await api("code.close", { rc: s.rc }); toast(tr("Сессия закрыта", "Session closed"), "ok"); if (after) after(); } catch (error) { toast(error.message, "err"); } } });
      if (!s.live) items.push({ label: tr("Удалить", "Delete"), icon: "trash", danger: true, run: async () => {
        if (!(await confirmDialog(tr("Разговор MemuCode будет удалён насовсем (файлы проекта не трогаются).", "The MemuCode conversation is deleted for good (project files are not touched)."), { title: tr("Удалить сессию?", "Delete the session?"), ok: tr("Удалить", "Delete"), danger: true }))) return;
        try { await api("code.delete", { session_id: id }); toast(tr("Сессия удалена", "Session deleted"), "ok"); if (T) closeStack(T.entry); if (after) after(); } catch (error) { toast(error.message, "err"); }
      } });
      actionSheet(s.title || s.project || tr("Сессия", "Session"), items);
    }

    async function catalog(rc, force) {
      if (!force && CODE.catalog && Date.now() - CODE.catalogAt < 60000 && (!rc || CODE.catalogRc === rc)) return CODE.catalog;
      try {
        const data = await api("code.commands", { rc: rc || "" });

        data.commands = (data.commands || []).filter((row) => !LOCAL_ONLY.has(String(row.name || "")));
        CODE.catalog = data; CODE.catalogAt = Date.now(); CODE.catalogRc = rc || "";
      } catch (error) { CODE.catalog = CODE.catalog || { commands: [], models: [] }; }
      return CODE.catalog;
    }
    const commandText = (row) => (S.lang === "en" ? row.en : row.ru) || row.description || "";
    function rankCommands(rows, query) {
      const q = query.toLowerCase();
      const scored = [];
      rows.forEach((row) => {
        const name = String(row.name || "").toLowerCase();
        const aliases = (row.aliases || []).map((x) => String(x).toLowerCase());
        let score = -1;
        if (!q) score = 1;
        else if (name === q || aliases.includes(q)) score = 100;
        else if (name.startsWith(q)) score = 80 - name.length / 100;
        else if (aliases.some((a) => a.startsWith(q))) score = 60;
        else if (name.includes(q)) score = 40;
        else if (commandText(row).toLowerCase().includes(q)) score = 10;
        if (score >= 0) scored.push([score, row]);
      });
      scored.sort((a, b) => b[0] - a[0] || String(a[1].name).localeCompare(String(b[1].name)));
      return scored.map((x) => x[1]);
    }
    function showHelp(T) {
      catalog(T && T.rc).then((data) => {
        const groups = {};
        const names = { general: tr("Основное", "General"), session: tr("Сессия", "Session"), model: tr("Модели", "Models"), context: tr("Контекст и память", "Context and memory"),
          code: tr("Работа с кодом", "Working with code"), agents: tr("Агенты и инструменты", "Agents and tools"), config: tr("Настройки", "Settings"), info: tr("Статистика", "Usage"), custom: tr("Свои команды и скиллы", "Custom commands and skills") };
        (data.commands || []).forEach((row) => { const key = names[row.category] ? row.category : "custom"; (groups[key] = groups[key] || []).push(row); });
        const body = `<p class="section-note" style="margin:0 20px 10px">${esc(tr("Опишите задачу обычным текстом или начните с «/» — подсказки появятся над полем. «#текст» — правило в память, «&задача» — в фоне.", "Describe the task in plain words or start with “/” — suggestions appear above the input. “#text” saves a rule, “&task” runs in the background."))}</p>` +
          Object.keys(names).filter((k) => groups[k]).map((key) => `<div class="section-title">${esc(names[key])}</div><div class="group flat">${groups[key].map((row) =>
            `<button class="cell flat" data-insert="${esc(row.name)}"><div class="cell-body"><div class="cell-title"><code>/${esc(row.name)}</code> ${row.hint ? `<small style="color:var(--hint)">${esc(row.hint)}</small>` : ""}</div><div class="cell-sub">${esc(commandText(row))}</div></div></button>`).join("")}</div>`).join("");
        openSheet({ title: tr("Команды MemuCode", "MemuCode commands"), full: true, body, onMount: (el, handle) => {
          $$("[data-insert]", el).forEach((b) => b.onclick = () => { handle.close(true); if (T) insertCommand(T, b.dataset.insert); });
        } });
      });
    }

    function newSession(prefill = {}) {
      const draft = { root: prefill.root || "", model: "", mode: "default", text: "" };
      const draw = (el) => {
        $("#ns-folder", el).textContent = draft.root || tr("Выберите папку", "Choose a folder");
        $("#ns-folder", el).classList.toggle("muted", !draft.root);
        $("#ns-model", el).textContent = draft.model ? modelName(draft.model) : tr("Как в MemuCode", "MemuCode default");
        $("#ns-mode", el).textContent = modeLabel(draft.mode);
        $("#ns-mode-row .cell-icon", el).style.background = draft.mode === "bypassPermissions" ? "var(--danger)" : "#64748b";
      };
      openSheet({ title: tr("Новая сессия MemuCode", "New MemuCode session"), full: !kit.isSplit(),
        body: `<div class="group flat" style="margin-top:4px">
            <button class="cell flat" id="ns-folder-row"><div class="cell-icon" style="background:#f59e0b">${icon("folder")}</div><div class="cell-body"><div class="cell-title">${esc(tr("Папка проекта", "Project folder"))}</div><div class="cell-sub" id="ns-folder" style="-webkit-line-clamp:2;font-family:var(--font-mono);font-size:12.5px"></div></div><span class="chev">${icon("chev")}</span></button>
            <button class="cell flat" id="ns-model-row"><div class="cell-icon" style="background:#10b981">${icon("model")}</div><div class="cell-body"><div class="cell-title">${esc(tr("Модель", "Model"))}</div></div><div class="cell-value" id="ns-model"></div><span class="chev">${icon("chev")}</span></button>
            <button class="cell flat" id="ns-mode-row"><div class="cell-icon">${icon("shield")}</div><div class="cell-body"><div class="cell-title">${esc(tr("Разрешения", "Permissions"))}</div></div><div class="cell-value" id="ns-mode"></div><span class="chev">${icon("chev")}</span></button></div>
          <div class="field" style="margin-top:14px"><label>${esc(tr("Первая задача (необязательно)", "First task (optional)"))}</label><textarea id="ns-text" rows="4" maxlength="20000" placeholder="${esc(tr("Например: изучи проект и предложи план рефакторинга", "E.g. study the project and suggest a refactoring plan"))}" style="font-family:var(--font-mono);font-size:14px"></textarea></div>
          <div class="section-title">${esc(tr("Недавние проекты", "Recent projects"))}</div><div class="group flat" id="ns-recent"><div class="cell flat"><div class="cell-sub">…</div></div></div>`,
        actions: [{ label: tr("Начать", "Start"), run: async (handle) => {
          draft.text = $("#ns-text").value.trim();
          if (!draft.root) { toast(tr("Выберите папку проекта", "Choose the project folder"), "err"); return true; }
          if (draft.mode === "bypassPermissions" && !(await confirmDialog(tr("MemuCode сможет выполнять любые команды и правки на компьютере без вопросов.", "MemuCode will run any command and edit without asking."), { title: tr("Без разрешений?", "Bypass permissions?"), ok: tr("Включить", "Enable"), danger: true }))) return true;
          const button = handle.el.querySelector("[data-act]");
          if (button) { button.disabled = true; button.textContent = tr("Запускаю MemuCode…", "Starting MemuCode…"); }
          try {
            const started = await api("code.new", { root: draft.root, model: draft.model, mode: draft.mode, confirm: draft.mode === "bypassPermissions" });
            haptic("success");
            handle.close(true);
            await loadSessions();
            const T = openSession(started.session_id, { rc: started.rc, title: draft.root.split(/[\\/]/).filter(Boolean).pop() });
            if (draft.text && T) setTimeout(() => sendText(T, draft.text), 300);
          } catch (error) {
            toast(error.message, "err");
            if (button) { button.disabled = false; button.textContent = tr("Начать", "Start"); }
          }
          return true;
        } }],
        onMount: (el) => {
          draw(el);
          $("#ns-folder-row", el).onclick = () => browseFolders(draft.root, (path) => { draft.root = path; draw(el); });
          $("#ns-model-row", el).onclick = () => pickCodeModel("", draft.model, (id) => { draft.model = id; draw(el); }, true);
          $("#ns-mode-row", el).onclick = () => actionSheet(tr("Разрешения", "Permissions"), MODES.map(([id, ru, en, dru, den]) => ({ label: tr(ru, en), sub: tr(dru, den),
            icon: id === draft.mode ? "check" : id === "bypassPermissions" ? "warn" : "shield", danger: id === "bypassPermissions", run: () => { draft.mode = id; draw(el); } })));
          api("code.projects").then((data) => {
            const box = $("#ns-recent", el); if (!box) return;
            if (data.cli === false) box.insertAdjacentHTML("beforebegin", `<p class="section-note" style="color:var(--danger)">${esc(tr("MemuCode CLI не найден на компьютере — переустановите MemuAI.", "MemuCode CLI was not found on the computer — reinstall MemuAI."))}</p>`);
            const rows = data.projects || [];
            box.innerHTML = rows.map((p) => `<button class="cell flat" data-root="${esc(p.path)}"><div class="cell-body"><div class="cell-title">${esc(p.name)}</div><div class="cell-sub" style="font-family:var(--font-mono);font-size:12px">${esc(p.path)}</div></div><span class="tag">${p.sessions}</span></button>`).join("") ||
              `<div class="cell flat"><div class="cell-sub">${esc(tr("Пока нет — выберите папку выше.", "None yet — choose a folder above."))}</div></div>`;
            $$("[data-root]", box).forEach((b) => b.onclick = () => { draft.root = b.dataset.root; haptic("select"); draw(el); });
          }).catch((error) => { const box = $("#ns-recent", el); if (box) box.innerHTML = `<div class="cell flat"><div class="cell-sub">${esc(error.message)}</div></div>`; });
        } });
    }
    function browseFolders(start, done) {
      let current = start || "";
      const handle = openSheet({ title: tr("Папка проекта", "Project folder"), full: true,
        body: `<div class="crumbs" id="bf-path"></div><div class="group flat" id="bf-list"></div>`,
        actions: [{ label: tr("Новая папка", "New folder"), kind: "secondary", run: async () => {
            if (!current) { toast(tr("Сначала откройте папку, в которой её создать", "Open the folder to create it in first"), "err"); return true; }
            const name = await promptSheet(tr("Имя новой папки", "New folder name"), { max: 120, ok: tr("Создать", "Create") });
            if (!name) return true;
            try { const made = await api("code.mkdir", { parent: current, name }); current = made.path; load(); } catch (error) { toast(error.message, "err"); }
            return true;
          } },
          { label: tr("Выбрать эту папку", "Use this folder"), run: () => { if (!current) { toast(tr("Откройте папку", "Open a folder"), "err"); return true; } done(current); } }],
      });
      const el = handle.el;
      async function load() {
        const list = $("#bf-list", el), path = $("#bf-path", el);
        list.innerHTML = `<div class="cell flat"><div class="typing"><i></i><i></i><i></i></div></div>`;
        let data;
        try { data = await api("code.browse", { path: current }); } catch (error) { list.innerHTML = `<div class="cell flat"><div class="cell-sub">${esc(error.message)}</div></div>`; return; }
        current = data.path || "";
        path.innerHTML = current ? `<button class="chip" data-go="">${icon("folder")}${esc(tr("Начало", "Start"))}</button>${data.parent ? `<button class="chip" data-go="${esc(data.parent)}">↑ ${esc(tr("Выше", "Up"))}</button>` : ""}<span class="crumb-path">${esc(current)}</span>`
          : `<span class="crumb-path">${esc(tr("Недавние проекты, домашняя папка и диски", "Recent projects, home folder and drives"))}</span>`;
        const rows = [];
        if (!current) {
          rows.push({ name: tr("Домашняя папка", "Home folder"), path: data.home, sub: data.home, icon: "user" });
          (data.drives || []).forEach((d) => rows.push({ name: d, path: d, icon: "monitor" }));
          (data.dirs || []).forEach((d) => rows.push({ name: d.name, path: d.path, sub: d.path, icon: "clock" }));
        } else (data.dirs || []).forEach((d) => rows.push({ name: d.name, path: d.path, icon: "folder" }));
        list.innerHTML = rows.map((r) => `<button class="cell flat" data-go="${esc(r.path)}"><div class="cell-icon" style="background:${r.icon === "folder" ? "#f59e0b" : "#64748b"}">${icon(r.icon)}</div><div class="cell-body"><div class="cell-title">${esc(r.name)}</div>${r.sub ? `<div class="cell-sub" style="font-family:var(--font-mono);font-size:12px">${esc(r.sub)}</div>` : ""}</div><span class="chev">${icon("chev")}</span></button>`).join("") ||
          `<div class="cell flat"><div class="cell-sub">${esc(tr("Вложенных папок нет — эту папку можно выбрать.", "No sub-folders — this folder can be chosen."))}</div></div>`;
        $$("[data-go]", el).forEach((b) => b.onclick = () => { current = b.dataset.go; haptic("select"); load(); });
      }
      load();
    }
    function pickCodeModel(rc, current, done, allowEmpty) {
      catalog(rc).then((data) => {
        const ids = (data.models && data.models.length ? data.models : S.models.map((m) => m.id));
        let query = "";
        const draw = (el, handle) => {
          const rows = ids.filter((id) => !query || (id + " " + modelName(id)).toLowerCase().includes(query));
          $("#cm-list", el).innerHTML = (allowEmpty ? `<button class="cell flat" data-model=""><div class="cell-body"><div class="cell-title">${esc(tr("Как в MemuCode", "MemuCode default"))}</div></div>${!current ? `<span class="check">${icon("check")}</span>` : ""}</button>` : "") +
            rows.slice(0, 300).map((id) => `<button class="cell flat" data-model="${esc(id)}"><div class="cell-body"><div class="cell-title">${esc(modelName(id))}</div><div class="tag">${esc(id)}</div></div>${id === current ? `<span class="check">${icon("check")}</span>` : ""}</button>`).join("") ||
            `<div class="empty" style="padding:30px">${esc(tr("Нет моделей", "No models"))}</div>`;
          $$("[data-model]", el).forEach((b) => b.onclick = () => { haptic("select"); handle.close(true); done(b.dataset.model); });
        };
        openSheet({ title: tr("Модель MemuCode", "MemuCode model"), full: ids.length > 8,
          body: `<div class="search">${icon("search")}<input id="cm-q" type="search" placeholder="${esc(tr("Найти модель", "Find a model"))}"></div><div class="group flat" id="cm-list"></div>`,
          onMount: (el, handle) => { draw(el, handle); $("#cm-q", el).oninput = (e) => { query = e.target.value.toLowerCase(); draw(el, handle); }; } });
      });
    }

    function openSession(sessionId, options = {}) {
      kit.primaryStack();
      const meta = (S.codeSessions || []).find((s) => s.id === sessionId) || { id: sessionId };
      const T = { id: sessionId, rc: options.rc || meta.rc || "", cursor: 0, items: [], notes: new Map(), live: null, liveHash: "",
        control: { cancelled: false }, job: null, jobText: "", jobTools: [], starting: false, title: options.title || meta.title || meta.project || sessionId,
        project: meta.project || "", entry: null, slash: { open: false, rows: [], index: 0 }, sentNotes: 0 };
      CODE.openId = sessionId;
      const actions = `<button class="icon-btn" id="t-files" aria-label="${esc(tr("Файлы", "Files"))}" title="${esc(tr("Файлы", "Files"))}">${icon("folder")}</button><button class="icon-btn" id="t-more" aria-label="${esc(tr("Действия", "Actions"))}">${icon("more")}</button>`;
      T.entry = openStack({ title: T.title, sub: meta.project ? meta.project + (meta.model ? " · " + modelName(meta.model) : "") : "", actions,
        build: (view, entry) => {
          view.innerHTML = `<div class="code-shell"><div class="code-main"><div class="term" id="term"><div class="term-inner"><div id="t-items"></div><div id="t-live"></div></div></div>
            <div class="composer code-composer"><div class="composer-inner"><div class="slash-menu" id="t-slash" hidden role="listbox"></div><div class="composer-tools" id="t-tools"></div>
            <div class="composer-row"><div class="composer-box"><button class="round-btn" id="t-cmd" aria-label="${esc(tr("Команды", "Commands"))}" title="${esc(tr("Команды", "Commands"))}"><b class="slash-glyph">/</b></button><textarea id="t-input" rows="1" placeholder="${esc(tr("Задача или /команда", "Task or /command"))}" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="send"></textarea></div>
            <button class="round-btn send-btn" id="t-send" disabled aria-label="${esc(tr("Отправить", "Send"))}">${icon("send")}</button></div></div></div></div>
            <aside class="code-side" id="t-side"></aside></div>`;
          bindComposer(T, view, entry);
        },
        onClose: () => { T.control.cancelled = true; if (CODE.openId === T.id) CODE.openId = ""; },
      });
      $("#t-files").onclick = () => openFiles({ session_id: T.id, rc: T.rc }, T.project);
      $("#t-more").onclick = () => sessionMenu(T.id, () => loadSessions(), T);
      drawTools(T); drawSide(T);
      watch(T);
      catalog(T.rc);
      return T;
    }
    function refreshTop(T) {
      const live = T.live;
      const sub = [T.project || (live && live.root ? live.root.split(/[\\/]/).filter(Boolean).pop() : ""), live && live.model ? modelName(live.model) : ""].filter(Boolean).join(" · ");
      const title = (live && live.title) || T.title;
      if (title !== T.entry.title || sub !== T.entry.sub) updateStackTop(T.entry, { title, sub });
    }
    async function watch(T) {
      let failures = 0;
      while (!T.control.cancelled) {
        try {
          const response = await api("code.watch", { session_id: T.id, rc: T.rc, cursor: T.cursor, live_hash: T.liveHash }, { raw: true });
          const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
          for (;;) {
            const { value, done } = await reader.read(); if (done) break;
            buffer += decoder.decode(value, { stream: true }); let cut;
            while ((cut = buffer.indexOf("\n")) >= 0) {
              const line = buffer.slice(0, cut); buffer = buffer.slice(cut + 1);
              if (!line.trim()) continue; let ev; try { ev = JSON.parse(line); } catch (e) { continue; }
              onEvent(T, ev);
            }
            if (T.control.cancelled) { try { reader.cancel(); } catch (e) {  } return; }
          }
          failures = 0;
        } catch (error) {
          if (T.control.cancelled) return;
          failures++;
          if (error.status === 404 && !T.rc) { toast(error.message, "err"); return; }
          if (failures > 2) {
            try { const data = await api("code.transcript", { session_id: T.id, cursor: T.cursor }); onEvent(T, { type: "items", items: data.items, cursor: data.cursor, reset: data.reset }); onEvent(T, { type: "live", live: data.live }); }
            catch (e) {  }
          }
          await sleep(Math.min(5000, 800 * failures));
        }
      }
    }
    function onEvent(T, ev) {
      if (ev.type === "session") {
        if (ev.id && ev.id !== T.id) { T.id = ev.id; T.items = []; T.cursor = ev.cursor || 0; CODE.openId = ev.id; drawTerm(T); }
      } else if (ev.type === "items") {
        if (ev.reset) T.items = [];
        T.items = T.items.concat(ev.items || []); T.cursor = ev.cursor; drawTerm(T);
      } else if (ev.type === "live") {
        const was = T.live;
        T.live = ev.live || null; T.liveHash = ev.hash || "";
        if (T.live) {
          T.rc = T.live.rc || T.rc;
          (T.live.notes || []).forEach((note) => { if (!T.notes.has(note.seq)) T.notes.set(note.seq, note); });
          if (T.live.notes && T.live.notes.length) drawTerm(T);
          if (T.live.dialog && (!was || !was.dialog || was.dialog.id !== T.live.dialog.id)) haptic("warning");
        }
        if (was && !T.live && T.rc) T.rc = (T.live && T.live.rc) || T.rc;
        refreshTop(T); drawLive(T); drawTools(T); drawSide(T);
      } else if (ev.type === "end") { T.cursor = ev.cursor; T.liveHash = ev.hash; }
    }

    function toolLine(name, display, ok, extra = "", state = "") {
      return `<div class="t-item t-tool ${state || (ok === false ? "err" : "")}"><div class="ic">${state === "run" ? '<div class="live-head"><div class="spinner"></div></div>' : icon(ok === false ? "close" : "check")}</div><div class="tb"><div class="tn">${esc(name)}</div>${display ? `<div class="td">${esc(display)}</div>` : ""}${extra}</div></div>`;
    }
    const noteHtml = (note) => `<div class="t-item t-out"><pre>${esc(note.text)}</pre></div>`;
    function drawTerm(T) {
      const box = $("#t-items", T.entry.view); if (!box) return;
      const term = $("#term", T.entry.view);
      const near = term.scrollHeight - term.scrollTop - term.clientHeight < 200;
      const results = {};
      T.items.forEach((it) => { if (it.kind === "tool") results[it.call] = it; });
      const removed = new Set();
      T.items.forEach((it, index) => {
        if (it.kind === "rewind") { const at = T.items.findIndex((x) => x.id === it.from); if (at >= 0) for (let i = at; i < index; i++) removed.add(i); }
      });
      const notes = Array.from(T.notes.values()).sort((a, b) => a.seq - b.seq);
      let n = 0;
      const flushNotes = (until) => { let out = ""; while (n < notes.length && (until == null || notes[n].ts <= until)) out += noteHtml(notes[n++]); return out; };
      const lastTodos = T.items.map((x) => x.kind).lastIndexOf("todos");
      let html = "";
      T.items.forEach((it, index) => {
        if (removed.has(index)) return;
        if (it.ts) html += flushNotes(it.ts);
        if (it.kind === "user") html += `<div class="t-item t-user"><b>${it.sub === "bash" ? "!" : it.sub === "command" ? "/" : "›"}</b><span>${esc(it.text)}${it.images ? ` · ${icon("image")} ${it.images}` : ""}</span></div>`;
        else if (it.kind === "assistant") html += `<div class="t-item t-assistant"><div class="answer">${md(it.text)}</div>${it.interrupted ? `<div class="t-note">${esc(tr("прервано", "interrupted"))}</div>` : ""}</div>`;
        else if (it.kind === "call") {
          const res = results[it.call];

          if (it.name === "TodoWrite" && res && res.ok !== false && lastTodos >= 0) return;
          const extra = res ? `${res.diff ? `<details><summary>${esc(tr("Изменения", "Changes"))}</summary><pre>${window.MemuMD.highlight(res.diff, "diff")}</pre></details>` : ""}${res.output && !res.diff ? `<details><summary>${esc(tr("Вывод", "Output"))}</summary><pre>${esc(res.output)}</pre></details>` : ""}`
            : `<details><summary>${esc(tr("Вызов", "Call"))}</summary><pre>${esc(it.args)}</pre></details>`;
          html += toolLine(it.name, res ? (res.summary || res.display) : String(it.args || "").slice(0, 140), res ? res.ok : undefined, extra, res ? "" : "run");
        }
        else if (it.kind === "summary") html += `<div class="t-note">${esc(tr("Контекст сжат", "Context compacted"))}</div>`;
        else if (it.kind === "interrupt") html += `<div class="t-note">${esc(tr("Прервано пользователем", "Interrupted by the user"))}</div>`;
        else if (it.kind === "todos" && index === lastTodos && (it.todos || []).length) html += todosHtml(it.todos);
      });
      html += flushNotes(null);
      if (!html) html = `<div class="empty" style="padding:40px 20px"><div class="empty-art">${icon("terminal")}</div><p>${esc(T.live ? tr("Напишите задачу или выберите команду — «/».", "Write a task or pick a command — “/”.") : tr("В этой сессии пока ничего нет.", "Nothing in this session yet."))}</p></div>`;
      box.innerHTML = html;
      bindRichContent(box);
      if (near) term.scrollTop = term.scrollHeight;
    }
    function todosHtml(todos) {
      return `<div class="t-item t-todos">${todos.map((td) => `<div class="${esc(td.status)}">${icon(td.status === "completed" ? "circleCheck" : td.status === "in_progress" ? "dots" : "circle")}<span>${esc(td.content)}</span></div>`).join("")}</div>`;
    }
    function drawLive(T) {
      const box = $("#t-live", T.entry.view); if (!box) return;
      const term = $("#term", T.entry.view);
      const near = term.scrollHeight - term.scrollTop - term.clientHeight < 220;
      const live = T.live;
      let html = "";
      if (T.starting) html += `<div class="live-panel"><div class="live-head"><div class="spinner"></div>${esc(tr("Запускаю MemuCode на компьютере…", "Starting MemuCode on the computer…"))}</div></div>`;
      if (T.job) {
        html += `<div class="live-panel"><div class="live-head"><div class="spinner"></div>${esc(tr("Выполняется на компьютере (фоновый ход)", "Running on the computer (background turn)"))}</div>
          ${T.jobTools.slice(-4).map((x) => toolLine(x.name, x.summary || x.input, x.ok, "", x.done ? "" : "run")).join("")}
          ${T.jobText ? `<div class="live-stream answer">${md(T.jobText)}<span class="cursor"></span></div>` : ""}</div>`;
      }
      if (live) {
        if (live.busy || live.connecting) {
          html += `<div class="live-panel"><div class="live-head"><div class="spinner"></div>${esc(live.status || (live.connecting ? tr("Подключение к моделям…", "Connecting to the models…") : tr("Работает", "Working")))}${live.busy_for ? ` · ${esc(duration(live.busy_for))}` : ""}${live.queue ? ` · ${esc(tr("в очереди", "queued"))}: ${live.queue}` : ""}</div>
            ${(live.tools || []).map((x) => toolLine(x.name, x.display, undefined, x.live ? `<pre class="t-stream">${esc(x.live)}</pre>` : "", "run")).join("")}
            ${live.stream ? `<div class="live-stream answer">${md(live.stream)}<span class="cursor"></span></div>` : live.reasoning ? `<div class="thinking"><div>${esc(tailText(live.reasoning, 600))}</div></div>` : ""}</div>`;
        }
        if (live.dialog) html += askCard(T, live.dialog);
      }
      box.innerHTML = html;
      bindRichContent(box);
      $$(".t-stream", box).forEach((pre) => { pre.scrollTop = pre.scrollHeight; });
      bindAsk(T, box);
      if (near || (live && live.dialog)) term.scrollTop = term.scrollHeight;
    }

    function tailText(text, n) {
      text = String(text || "");
      if (text.length <= n) return text;
      const cut = text.slice(-n);
      const line = cut.indexOf("\n");
      if (line >= 0 && line < n / 3) return cut.slice(line + 1);
      const space = cut.search(/\s/);
      return "…" + (space >= 0 && space < 40 ? cut.slice(space + 1) : cut);
    }
    function unwrapBody(text) {
      const lines = String(text || "").split("\n");
      const out = [];
      for (const line of lines) {
        const prev = out.length ? out[out.length - 1] : "";
        if (prev.trim() && !/^[\s+\-│|>*•#`]/.test(prev) && !/[:;]\s*$/.test(prev) && /^[a-zа-яё(«"]/.test(line) && prev.length >= 24) out[out.length - 1] = prev.replace(/\s+$/, "") + " " + line;
        else out.push(line);
      }
      return out.join("\n");
    }
    function askCard(T, d) {
      const detail = d.detail || null;
      const danger = d.danger || (detail && detail.danger);
      let body = "";
      if (detail) {
        if (detail.reason) body += `<div class="reason">${esc(detail.reason)}</div>`;
        const shown = detail.diff ? `<pre>${window.MemuMD.highlight(detail.diff, "diff")}</pre>` : detail.preview ? `<pre>${esc(detail.preview)}</pre>` : "";
        body += shown;
        body += `<details${shown ? "" : " open"}><summary style="font-size:12.5px;color:var(--accent);cursor:pointer">${esc(tr("Точный вызов инструмента", "Exact tool call"))}</summary><pre>${esc(detail.tool)}(${esc(detail.args)})</pre></details>`;
        if (detail.rule) body += `<div class="meta">${esc(detail.rule)}</div>`;
      } else if (d.body) body += `<pre>${esc(unwrapBody(d.body.slice(-4000)))}</pre>`;
      const kind = (o) => o.cancel || o.value === "no" || o.value === "feedback" || o.text_input ? "neutral"
        : o.value === "always" ? (danger ? "danger" : "secondary")
        : o.value === "yes" || o.index === 0 ? (danger ? "danger-solid" : "") : "secondary";
      let opts;
      if (d.multi) {
        opts = d.options.map((o) => `<label class="cell flat multi-opt"><input type="checkbox" data-pick="${o.index}"><div class="cell-body"><div class="cell-title">${esc(o.label)}</div>${o.description ? `<div class="cell-sub">${esc(o.description)}</div>` : ""}</div></label>`).join("") +
          `<button class="btn" data-multi-ok="1">${esc(tr("Готово", "Done"))}</button><button class="btn neutral" data-esc-ask="1">${esc(tr("Отмена", "Cancel"))}</button>`;
      } else {
        opts = d.options.map((o) => `<button class="btn ${kind(o)}" data-opt="${o.index}">${esc(o.label)}${o.description ? `<small style="display:block;font-weight:500;opacity:.75">${esc(o.description)}</small>` : ""}</button>`).join("") ||
          `<button class="btn neutral" data-esc-ask="1">${esc(tr("Закрыть", "Close"))}</button>`;
      }
      return `<div class="ask ${danger ? "danger" : ""}"><h4>${icon(danger ? "warn" : d.kind === "permission" ? "shield" : "info")}${esc(d.title)}</h4>${body}<div class="opts">${opts}</div></div>`;
    }
    function bindAsk(T, box) {
      const d = T.live && T.live.dialog; if (!d) return;
      $$("[data-opt]", box).forEach((b) => b.onclick = () => answer(T, d, { choice: +b.dataset.opt }));
      $$("[data-esc-ask]", box).forEach((b) => b.onclick = () => interrupt(T));
      const ok = $("[data-multi-ok]", box);
      if (ok) ok.onclick = () => answer(T, d, { choices: $$("[data-pick]", box).filter((x) => x.checked).map((x) => +x.dataset.pick) });
    }
    async function answer(T, d, pick) {
      const command = { type: "answer", dialog: d.id };
      if (pick.choices) command.choices = pick.choices;
      else {
        command.choice = pick.choice;
        const option = d.options[pick.choice];
        if (!option) return;
        if (option.text_input) {
          const text = await promptSheet(option.label, { multiline: true, max: 4000, ok: tr("Отправить", "Send") });
          if (!text) return;
          command.text = text;
        }
        const detail = d.detail || {};
        const approving = option.value !== "no" && option.value !== "feedback" && !option.cancel && d.kind === "permission";
        if (approving && (detail.danger || d.danger || option.value === "always")) {
          const yes = await confirmDialog(option.value === "always" ? tr("MemuCode больше не будет спрашивать о таких действиях в этой сессии.", "MemuCode will stop asking about such actions in this session.")
            : tr("Это опасное действие. Разрешить его на компьютере?", "This is a dangerous action. Allow it on the computer?"), { title: String(d.title || "").slice(0, 60), ok: tr("Разрешить", "Allow"), danger: true });
          if (!yes) return;
          command.confirm = true;
        }
      }
      haptic("medium");
      try { await api("code.command", { rc: T.rc, command }); toast(tr("Ответ отправлен", "Answer sent"), "ok"); }
      catch (error) { toast(error.message, "err"); }
    }
    async function interrupt(T) {
      if (!T.rc) return;
      haptic("rigid");
      try { await api("code.command", { rc: T.rc, command: { type: "interrupt" } }); } catch (error) { toast(error.message, "err"); }
    }

    function drawTools(T) {
      const row = $("#t-tools", T.entry.view); if (!row) return;
      const live = T.live;
      if (live) {
        const mode = live.permission_mode || "default";
        row.innerHTML = `<span class="mode-pill">${'<span class="live-dot"></span>'}${esc(live.host ? tr("С телефона", "Phone session") : tr("Терминал", "Terminal"))}</span>
          <button class="toggle-chip model" id="t-model">${icon("model")}<span>${esc(modelName(live.model) || tr("Модель", "Model"))}</span></button>
          <button class="mode-pill ${mode === "bypassPermissions" ? "warn" : ""}" id="t-mode">${icon(mode === "bypassPermissions" ? "warn" : "shield")}${esc(modeLabel(mode))}</button>
          ${live.busy || live.dialog ? `<button class="toggle-chip" id="t-esc">${icon("stop")}${esc(tr("Стоп", "Stop"))} <kbd>Esc</kbd></button>` : ""}
          ${(live.todos || []).length ? `<button class="toggle-chip" id="t-todos">${icon("list")}${live.todos.filter((x) => x.status === "completed").length}/${live.todos.length}</button>` : ""}`;
      } else {

        row.innerHTML = `<span class="mode-pill" title="${esc(tr("Не запущена — запустится при отправке", "Not running — starts when you send"))}">${icon("clock")}${esc(tr("Запустится при отправке", "Starts when you send"))}</span>
          ${T.job ? `<button class="toggle-chip" id="t-stop">${icon("stop")}${esc(tr("Остановить", "Stop"))}</button>` : `<button class="toggle-chip" id="t-start">${icon("play")}${esc(tr("Запустить", "Start"))}</button>`}`;
      }
      const model = $("#t-model", row); if (model) model.onclick = () => pickCodeModel(T.rc, live.model, (id) => { if (id) sendRaw(T, "/model " + id); });
      const mode = $("#t-mode", row); if (mode) mode.onclick = () => actionSheet(tr("Режим разрешений", "Permission mode"), MODES.map(([id, ru, en, dru, den]) => ({ label: tr(ru, en), sub: tr(dru, den),
        icon: id === (live.permission_mode || "default") ? "check" : id === "bypassPermissions" ? "warn" : "shield", danger: id === "bypassPermissions",
        run: async () => {
          if (id === "bypassPermissions" && !(await confirmDialog(tr("MemuCode сможет выполнять любые команды и правки на компьютере без вопросов.", "MemuCode will run any command and edit without asking."), { title: tr("Без разрешений?", "Bypass permissions?"), ok: tr("Включить", "Enable"), danger: true }))) return;
          sendRaw(T, "/mode " + id);
        } })));
      const stopLive = $("#t-esc", row); if (stopLive) stopLive.onclick = () => interrupt(T);
      const todos = $("#t-todos", row); if (todos) todos.onclick = () => openSheet({ title: tr("Задачи агента", "The agent's todos"), body: `<div style="padding:0 16px">${todosHtml(live.todos)}</div>` });
      const stop = $("#t-stop", row); if (stop) stop.onclick = () => api("jobs.stop", { job: T.job }).catch((e) => toast(e.message, "err"));
      const start = $("#t-start", row); if (start) start.onclick = () => ensureLive(T);
      syncSend(T);
    }
    function drawSide(T) {
      const side = $("#t-side", T.entry.view); if (!side) return;
      if (!document.documentElement.dataset.wide) { side.innerHTML = ""; return; }
      const live = T.live || {};
      side.innerHTML = `<div class="side-section"><div class="side-title">${esc(tr("Сессия", "Session"))}</div>
          <div class="side-row"><span>${esc(tr("Состояние", "State"))}</span><b>${esc(T.live ? (live.busy ? tr("работает", "working") : tr("ждёт задачу", "idle")) : tr("не запущена", "not running"))}</b></div>
          ${live.model ? `<div class="side-row"><span>${esc(tr("Модель", "Model"))}</span><b>${esc(modelName(live.model))}</b></div>` : ""}
          ${live.permission_mode ? `<div class="side-row"><span>${esc(tr("Разрешения", "Permissions"))}</span><b>${esc(modeLabel(live.permission_mode))}</b></div>` : ""}
          ${live.root ? `<div class="side-path">${esc(live.root)}</div>` : ""}</div>
        ${(live.todos || []).length ? `<div class="side-section"><div class="side-title">${esc(tr("Задачи", "Todos"))}</div>${todosHtml(live.todos)}</div>` : ""}
        <div class="side-section"><button class="btn secondary small block" id="side-files">${icon("folder")}${esc(tr("Файлы проекта", "Project files"))}</button>
          <button class="btn secondary small block" id="side-changes" style="margin-top:8px">${icon("list")}${esc(tr("Изменения", "Changes"))}</button>
          <button class="btn secondary small block" id="side-help" style="margin-top:8px">${icon("terminal")}${esc(tr("Команды", "Commands"))}</button></div>`;
      $("#side-files", side).onclick = () => openFiles({ session_id: T.id, rc: T.rc }, T.project);
      $("#side-changes", side).onclick = () => openChanges({ session_id: T.id, rc: T.rc });
      $("#side-help", side).onclick = () => showHelp(T);
    }

    function bindComposer(T, view, entry) {
      const input = $("#t-input", view), send = $("#t-send", view);
      input.addEventListener("input", () => {
        input.style.height = "auto"; input.style.height = Math.min(170, input.scrollHeight) + "px";
        syncSend(T); updateSlash(T);
      });
      input.addEventListener("keydown", (event) => {
        if (T.slash.open) {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); moveSlash(T, event.key === "ArrowDown" ? 1 : -1); return; }
          if (event.key === "Tab") { event.preventDefault(); pickSlash(T, T.slash.index, true); return; }
          if (event.key === "Enter" && !event.shiftKey && !event.isComposing && T.slash.rows.length) {
            const row = T.slash.rows[T.slash.index];

            if (row && row.kind !== "command" && typedToken(T) === row.value) closeSlash(T);
            else { event.preventDefault(); pickSlash(T, T.slash.index); return; }
          }
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeSlash(T); return; }
        }
        if (event.key === "Escape" && T.live && (T.live.busy || T.live.dialog) && !input.value) { event.preventDefault(); interrupt(T); return; }
        if (event.key === "Enter" && !event.shiftKey && !event.isComposing && (event.ctrlKey || event.metaKey || kit.enterSends())) { event.preventDefault(); if (!send.disabled) send.click(); }
      });
      input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) closeSlash(T); }, 180));
      input.addEventListener("click", () => updateSlash(T));
      send.onclick = () => {
        if (!input.value.trim() && T.live && T.live.busy) { interrupt(T); return; }
        const text = input.value.trim(); if (!text) return;
        input.value = ""; input.dispatchEvent(new Event("input"));
        sendText(T, text);
      };
      $("#t-cmd", view).onclick = () => { input.focus(); if (!input.value.startsWith("/")) { input.value = "/" + input.value; } input.dispatchEvent(new Event("input")); };
      kit.hooks.composerExtras.forEach((fn) => { try { fn(T, view, entry, { code: true, input }); } catch (e) { console.error(e); } });
    }
    function syncSend(T) {
      const view = T.entry.view, input = $("#t-input", view), send = $("#t-send", view); if (!input || !send) return;
      const stop = !input.value.trim() && T.live && T.live.busy;
      send.disabled = !stop && !input.value.trim();
      send.classList.toggle("stop", !!stop);
      send.innerHTML = stop ? icon("stop") : icon("send");
      send.setAttribute("aria-label", stop ? tr("Остановить (Esc)", "Stop (Esc)") : tr("Отправить", "Send"));
    }

    const MENTION = /(?:^|[\s(])@("[^"]*|[^\s@"]*)$/;
    let completeSeq = 0, completeTimer = 0;
    function beforeCaret(input) { return input.value.slice(0, input.selectionStart == null ? input.value.length : input.selectionStart); }
    function commandRows(query, data) {
      return rankCommands(data.commands || [], query).slice(0, 40).map((row) => ({ value: "/" + row.name, label: "/" + row.name, hint: row.hint || "",
        meta: commandText(row), kind: "command", row }));
    }
    function localArguments(before, data) {

      const match = /^\/(\S+) (\S*)$/.exec(before); if (!match) return null;
      const name = match[1].toLowerCase(), q = match[2].toLowerCase();
      let rows = [];
      if (name === "model" || name === "small-model") rows = [...new Set([...(data.models || []), ...S.models.map((m) => m.id)])].map((id) => ({ value: id, label: id, meta: modelName(id) !== id ? modelName(id) : "", kind: "model" }));
      else if (name === "permissions" || name === "mode") rows = MODES.map(([id, , , dru, den]) => ({ value: id, label: id, meta: tr(dru, den), kind: "choice" }));
      rows = rows.filter((row) => !q || row.value.toLowerCase().includes(q)).slice(0, 40);
      return { kind: "argument", span: match[2].length, items: rows };
    }
    async function updateSlash(T) {
      const input = $("#t-input", T.entry.view); if (!input) return;
      const before = beforeCaret(input);
      clearTimeout(completeTimer);
      const seq = ++completeSeq;
      if (!before || before.includes("\n")) { closeSlash(T); return; }
      const command = /^\/([^\s]*)$/.exec(before);
      if (command) {
        const data = await catalog(T.rc);
        if (seq !== completeSeq) return;
        showSuggestions(T, { kind: "command", span: before.length, items: commandRows(command[1], data) });
        return;
      }
      if (!(before.startsWith("/") && before.includes(" ")) && !MENTION.test(before)) { closeSlash(T); return; }
      completeTimer = setTimeout(async () => {
        let result;
        try { result = await api("code.complete", { text: before, rc: T.rc || "", session_id: T.id || "" }); }
        catch (error) { result = localArguments(before, CODE.catalog || {}); }
        if (seq !== completeSeq || !result) { if (seq === completeSeq) closeSlash(T); return; }
        showSuggestions(T, result);
      }, 120);
    }
    function showSuggestions(T, result) {
      const items = (result.items || []).slice(0, 40);
      T.slash = { open: items.length > 0, rows: items, index: 0, kind: result.kind || "", span: result.span || 0 };
      drawSlash(T);
    }
    const KIND_ICON = { folder: "folder", file: "file", model: "model", session: "terminal" };
    function drawSlash(T) {
      const menu = $("#t-slash", T.entry.view); if (!menu) return;
      menu.hidden = !T.slash.open;
      if (!T.slash.open) { menu.innerHTML = ""; return; }
      menu.dataset.kind = T.slash.kind;
      menu.innerHTML = T.slash.rows.map((row, i) => `<button class="slash-item ${i === T.slash.index ? "active" : ""} ${row.kind === "command" ? "" : "arg"}" data-slash="${i}" role="option" aria-selected="${i === T.slash.index}">
        ${row.kind !== "command" && KIND_ICON[row.kind] ? `<span class="slash-ico">${icon(KIND_ICON[row.kind])}</span>` : ""}<code>${esc(row.label || row.value)}</code>${row.hint ? `<span class="slash-hint">${esc(row.hint)}</span>` : ""}<span class="slash-desc">${esc(row.meta || "")}</span></button>`).join("")
        + `<div class="slash-keys">${esc(tr("↑ ↓ — выбор · Tab — вставить · Enter — выбрать · Esc — закрыть", "↑ ↓ choose · Tab insert · Enter pick · Esc close"))}</div>`;
      $$("[data-slash]", menu).forEach((b) => {
        b.onmousedown = (event) => event.preventDefault();
        b.onclick = () => pickSlash(T, +b.dataset.slash);
      });
      const active = $(".slash-item.active", menu); if (active) active.scrollIntoView({ block: "nearest" });
    }
    function moveSlash(T, delta) {
      if (!T.slash.rows.length) return;
      T.slash.index = (T.slash.index + delta + T.slash.rows.length) % T.slash.rows.length;
      drawSlash(T);
    }
    function closeSlash(T) { clearTimeout(completeTimer); completeSeq++; if (!T.slash.open) return; T.slash.open = false; drawSlash(T); }
    function pickSlash(T, index, insertOnly) {
      const row = T.slash.rows[index]; if (!row) return;
      haptic("select");
      if (row.kind === "command") { insertCommand(T, row.value.slice(1), row.row, insertOnly); return; }
      const input = $("#t-input", T.entry.view);
      const caret = input.selectionStart == null ? input.value.length : input.selectionStart;
      const head = input.value.slice(0, Math.max(0, caret - T.slash.span)), tail = input.value.slice(caret);

      const open = row.kind === "folder" && !row.final;
      const piece = row.value + (open || tail.startsWith(" ") ? "" : " ");
      input.value = head + piece + tail;
      const at = head.length + piece.length;
      input.focus(); input.setSelectionRange(at, at);
      closeSlash(T);
      input.dispatchEvent(new Event("input"));
    }

    function typedToken(T) {
      const input = $("#t-input", T.entry.view);
      const before = beforeCaret(input);
      return before.slice(before.length - T.slash.span);
    }

    function insertCommand(T, name, row, insertOnly) {
      const input = $("#t-input", T.entry.view);
      closeSlash(T);
      row = row || ((CODE.catalog && CODE.catalog.commands) || []).find((x) => x.name === name) || {};
      const needsArgs = row.hint && !String(row.hint).startsWith("[");
      if (!needsArgs && !insertOnly) { input.value = ""; input.dispatchEvent(new Event("input")); sendText(T, "/" + name); return; }
      input.value = "/" + name + " "; input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
      input.dispatchEvent(new Event("input"));
    }

    async function ensureLive(T) {
      if (T.live && T.rc) return true;
      if (T.starting) { while (T.starting) await sleep(150); return !!T.rc; }
      T.starting = true; drawLive(T);
      try {
        const started = await api("code.open", { session_id: T.id });
        T.rc = started.rc;
        if (started.session_id && started.session_id !== T.id) T.id = started.session_id;
        for (let i = 0; i < 30 && !T.live && !T.control.cancelled; i++) await sleep(200);
        loadSessions();
        return true;
      } catch (error) {
        T.starting = false; drawLive(T);
        if (["no_cli", "host_failed", "host_timeout"].includes(error.code)) {

          T.hostFailed = error.message;
        }
        toast(error.message, "err");
        return false;
      } finally { T.starting = false; drawLive(T); }
    }
    async function sendRaw(T, text) {
      try {
        if (!(await ensureLive(T))) return false;
        await api("code.command", { rc: T.rc, command: { type: "prompt", text } });
        return true;
      } catch (error) { toast(error.message, "err"); return false; }
    }
    async function sendText(T, text) {
      text = String(text || "").trim(); if (!text) return;
      if (/^\/(help|\?|h)\s*$/i.test(text)) { showHelp(T); return; }

      if (/^\/model\s*$/i.test(text)) { pickCodeModel(T.rc, T.live && T.live.model, (id) => { if (id) sendRaw(T, "/model " + id); }); return; }
      if (/^\/(mode|permissions)\s*$/i.test(text) && T.live) { const btn = $("#t-mode", T.entry.view); if (btn) { btn.click(); return; } }
      const local = /^\/([\w-]+)/.exec(text);
      if (local && LOCAL_ONLY.has(local[1].toLowerCase()) && local[1].toLowerCase() !== "run") {
        toast(local[1].toLowerCase() === "exit" || local[1].toLowerCase() === "quit"
          ? tr("Чтобы завершить сессию, откройте «…» → «Закрыть сессию на компьютере».", "To end the session use “…” → “Close the session on the computer”.")
          : tr("Эта команда работает только в терминале на компьютере (микрофон, буфер обмена, вид терминала).", "This command only works in the terminal on the computer (microphone, clipboard, terminal look)."), "err");
        return;
      }
      if (/^!|^\/run(\s|$)/i.test(text)) { toast(tr("«!команда» и /run с телефона не выполняются: попросите агента обычным сообщением — команда пройдёт через разрешения.", "“!command” and /run are not run from the phone: ask the agent in a normal message so it goes through the permission dialog."), "err"); return; }
      haptic("medium");
      if (!T.live) {
        const ok = await ensureLive(T);
        if (!ok) {
          if (T.hostFailed && !text.startsWith("/")) return headless(T, text);
          const input = $("#t-input", T.entry.view); if (input && !input.value) { input.value = text; input.dispatchEvent(new Event("input")); }
          return;
        }
      }
      try {
        await api("code.command", { rc: T.rc, command: { type: "prompt", text } });
        if (T.live && T.live.busy && !text.startsWith("/")) toast(tr("Добавлено в очередь", "Queued"), "ok");
      } catch (error) {
        toast(error.message, "err");
        const input = $("#t-input", T.entry.view); if (input && !input.value) { input.value = text; input.dispatchEvent(new Event("input")); }
      }
    }
    async function headless(T, text) {
      if (T.job) { toast(tr("Подождите окончания хода", "Wait for the turn to finish"), "err"); return; }
      try {
        const data = await api("code.run", { session_id: T.id, text, mode: "default" });
        T.job = data.job; T.jobText = ""; T.jobTools = [];
        drawTools(T); drawLive(T);
        followJob(T.job, 0, (ev) => {
          if (ev.type === "delta") T.jobText += ev.text || "";
          else if (ev.type === "tool_use") T.jobTools.push({ id: ev.id, name: ev.name, input: ev.input });
          else if (ev.type === "tool_result") { const x = T.jobTools.find((t) => t.id === ev.id); if (x) Object.assign(x, { ok: ev.ok, summary: ev.summary, done: true }); }
          else if (ev.type === "error") toast(ev.message, "err");
          else if (ev.type === "result" && ev.is_error) toast(String(ev.result || tr("Ошибка", "Error")).slice(0, 200), "err");
          else if (ev.type === "done" || ev.type === "lost") { T.job = null; T.jobText = ""; T.jobTools = []; drawTools(T); haptic(ev.ok ? "success" : "warning"); }
          drawLive(T);
        }, T.control);
      } catch (error) { toast(error.message, "err"); }
    }

    function openFiles(where, project) {
      const F = { where, path: "", entry: null };
      F.entry = openStack({ title: tr("Файлы", "Files"), sub: project || "",
        actions: `<button class="icon-btn" id="f-changes" aria-label="${esc(tr("Изменения", "Changes"))}" title="${esc(tr("Изменения", "Changes"))}">${icon("list")}</button>`,
        build: (view) => { view.style.overflowY = "auto"; view.innerHTML = `<div class="crumbs" id="f-crumbs"></div><div class="group flat" id="f-list"></div>`; } });
      $("#f-changes").onclick = () => openChanges(where);
      const load = async () => {
        const list = $("#f-list", F.entry.view), crumbs = $("#f-crumbs", F.entry.view); if (!list) return;
        list.innerHTML = `<div class="cell flat"><div class="typing"><i></i><i></i><i></i></div></div>`;
        let data;
        try { data = await api("code.files", Object.assign({ path: F.path }, where)); } catch (error) { list.innerHTML = `<div class="cell flat"><div class="cell-sub">${esc(error.message)}</div></div>`; return; }
        F.path = data.path || "";
        const parts = F.path ? F.path.split("/") : [];
        crumbs.innerHTML = `<button class="chip" data-cd="">${icon("folder")}${esc(data.root.split(/[\\/]/).filter(Boolean).pop() || "/")}</button>` +
          parts.map((part, i) => `<span class="crumb-sep">/</span><button class="chip" data-cd="${esc(parts.slice(0, i + 1).join("/"))}">${esc(part)}</button>`).join("");
        list.innerHTML = (data.entries || []).map((e) => `<button class="cell flat file-row" data-${e.dir ? "dir" : "file"}="${esc((F.path ? F.path + "/" : "") + e.name)}"><div class="cell-icon" style="background:${e.dir ? "#f59e0b" : "#64748b"}">${icon(e.dir ? "folder" : /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(e.name) ? "image" : "file")}</div><div class="cell-body"><div class="cell-title">${esc(e.name)}</div></div><div class="cell-value">${e.dir ? "" : esc(size(e.size))}</div>${e.dir ? `<span class="chev">${icon("chev")}</span>` : ""}</button>`).join("") ||
          `<div class="cell flat"><div class="cell-sub">${esc(tr("Папка пуста", "The folder is empty"))}</div></div>`;
        if (data.truncated) list.insertAdjacentHTML("beforeend", `<div class="cell flat"><div class="cell-sub">${esc(tr("Показаны первые 600 элементов", "The first 600 items are shown"))}</div></div>`);
        $$("[data-cd]", crumbs).forEach((b) => b.onclick = () => { F.path = b.dataset.cd; load(); });
        $$("[data-dir]", list).forEach((b) => b.onclick = () => { F.path = b.dataset.dir; haptic("select"); load(); });
        $$("[data-file]", list).forEach((b) => b.onclick = () => openFile(where, b.dataset.file));
      };
      load();
    }
    const size = (n) => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB";
    const LANGS = { py: "python", js: "javascript", mjs: "javascript", ts: "typescript", tsx: "typescript", jsx: "javascript", json: "json", md: "markdown",
      html: "html", htm: "html", css: "css", sh: "bash", ps1: "powershell", yml: "yaml", yaml: "yaml", toml: "toml", rs: "rust", go: "go", java: "java",
      c: "c", h: "c", cpp: "cpp", cs: "csharp", rb: "ruby", php: "php", sql: "sql", xml: "xml", diff: "diff", patch: "diff" };
    async function openFile(where, path) {
      let data;
      try { data = await api("code.file", Object.assign({ path }, where)); } catch (error) { toast(error.message, "err"); return; }
      openStack({ title: data.name, sub: data.path + " · " + size(data.size),
        actions: data.kind === "text" ? `<button class="icon-btn" id="fv-copy" aria-label="${esc(tr("Копировать", "Copy"))}">${icon("copy")}</button>` : "",
        build: (view) => {
          view.style.overflowY = "auto";
          let body;
          if (data.kind === "image") body = `<div class="file-image"><img src="${esc(data.data)}" alt="${esc(data.name)}"></div>`;
          else if (data.kind === "text") {
            const lang = LANGS[data.lang] || data.lang;
            const highlighted = window.MemuMD.highlight ? window.MemuMD.highlight(data.text, lang) : esc(data.text);
            const lines = data.text.split("\n").length;
            body = `<pre class="file-code"><span class="ln" aria-hidden="true">${Array.from({ length: Math.min(lines, 20000) }, (_, i) => i + 1).join("\n")}</span><code>${highlighted}</code></pre>${data.truncated ? `<p class="section-note">${esc(tr("Показано начало файла (512 КБ)", "Showing the start of the file (512 KB)"))}</p>` : ""}`;
          } else if (data.kind === "large") body = `<div class="empty"><p>${esc(tr("Картинка больше 4 МБ — откройте её на компьютере.", "The image is larger than 4 MB — open it on the computer."))}</p></div>`;
          else body = `<div class="empty"><p>${esc(tr("Двоичный файл — содержимое не показывается.", "A binary file — its content is not shown."))}</p></div>`;
          view.innerHTML = body;
        } });
      const copy = $("#fv-copy"); if (copy) copy.onclick = () => { kit.copyText(data.text); toast(tr("Скопировано", "Copied"), "ok"); };
    }
    async function openChanges(where) {
      let data;
      try { data = await api("code.changes", where); } catch (error) { toast(error.message, "err"); return; }
      const files = data.files || [];
      openSheet({ title: tr("Изменения", "Changes"), full: files.length > 6,
        body: !data.git ? `<p class="section-note" style="margin:0 20px">${esc(tr("Папка не является git-репозиторием (или git не установлен).", "The folder is not a git repository (or git is not installed)."))}</p>`
          : files.length ? `<div class="group flat">${files.map((f) => `<button class="cell flat" data-diff="${esc(f.path)}"><span class="git-st st-${esc(f.status.replace("?", "u"))}">${esc(f.status)}</span><div class="cell-body"><div class="cell-title" style="font-family:var(--font-mono);font-size:13px">${esc(f.path)}</div></div><span class="chev">${icon("chev")}</span></button>`).join("")}</div>`
          : `<p class="section-note" style="margin:0 20px">${esc(tr("Незакоммиченных изменений нет.", "No uncommitted changes."))}</p>`,
        onMount: (el, handle) => $$("[data-diff]", el).forEach((b) => b.onclick = async () => {
          let diff = "";
          try { diff = (await api("code.diff", Object.assign({ path: b.dataset.diff }, where))).diff || ""; } catch (error) { toast(error.message, "err"); return; }
          handle.close(true);
          if (!diff.trim()) { openFile(where, b.dataset.diff); return; }
          openStack({ title: b.dataset.diff.split("/").pop(), sub: tr("Изменения", "Changes"), build: (view) => { view.style.overflowY = "auto"; view.innerHTML = `<pre class="file-code diff"><code>${window.MemuMD.highlight(diff, "diff")}</code></pre>`; } });
        }) });
    }

    window.MemuTgCode = { loadSessions, renderTab, openSession, newSession, catalog, rankCommands };
  });
})();
