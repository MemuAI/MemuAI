/* Round 29 — Quick Access over File Explorer and other programs (plugin "explorer", backend app/quick_explorer.py).

   Alt+Space over Explorer / the desktop / an editor / Office / a terminal → GET /api/quick/apps/context?wait=1
   (the native side read the folder, the selection, the file or the document in the background). The panel in
   the top slot shows the chip («Папка: Загрузки · выбрано 3 файла») and one-click actions; while the chip is on,
   every question carries {type:"app", id} (MemuQuick.addRequestContext) and the model works with the folder
   through folder_* tools. A change set (tool-ui "changeset") becomes a card in the bottom slot: diffs, a rename
   table, «Apply» / «Discard», then «Undo». Local views without a model: duplicates and search inside files.
   window.QuickExplorer = {context, setContext, attached, openView}. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || !Q.registerPanel || !Q.addRequestContext) return;
  const root = document.documentElement;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const IC = {
    folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
    code: svg('<path d="M8 6 3 12l5 6M16 6l5 6-5 6"/>'),
    doc: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'),
    term: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M12 15h5"/>'),
    app: svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/>'),
    x: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    list: svg('<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>'),
    info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
    rename: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
    sort: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10v6M9 13l3 3 3-3"/>'),
    wand: svg('<path d="m4 20 10-10M15 4v3M19 8h-3M18 4l-2 2M13 9l2 2"/>'),
    dup: svg('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
    search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    spark: svg('<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/>'),
    bug: svg('<path d="M8 8a4 4 0 0 1 8 0v1H8zM6 13h12M7 9h10v5a5 5 0 0 1-10 0z"/><path d="M4 9l3 1M20 9l-3 1M4 18l3-2M20 18l-3-2"/>'),
    back: svg('<path d="M15 6l-6 6 6 6"/>'),
    open: svg('<path d="M14 4h6v6M20 4l-9 9M10 6H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>'),
    undo: svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>'),
    check: svg('<path d="m5 12 5 5 9-10"/>'),
    lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
    chev: svg('<path d="m9 6 6 6-6 6"/>'),
    trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  };
  const S = {
    ctx: null, on: false, presets: [], settings: {}, native: !!Q.native, loading: false, more: false,
    view: "", ask: null, query: "", results: null, dups: null, busy: false, index: 0,
    sets: new Map(),            // id → public change set
    seq: 0,
  };
  let panel = null, card = null;

  async function api(path, body) { return Q.api(path, body === undefined ? {} : { body: { lang: Q.lang(), ...body } }); }
  const n = (num, one, few, many) => { const a = Math.abs(num) % 100, b = a % 10; return a > 10 && a < 20 ? many : (b > 1 && b < 5 ? few : (b === 1 ? one : many)); };
  const human = (size) => {
    const u = Q.lang() === "en" ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let v = +size || 0, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i += 1; }
    const text = i ? v.toFixed(1) : String(Math.round(v));
    return (Q.lang() === "en" ? text : text.replace(".", ",")) + " " + u[i];
  };

  /* ---------------------------------------------------------- context */
  async function loadContext(wait, quiet = false) {
    const seq = ++S.seq;
    S.loading = !!wait && !quiet;
    if (wait) render();
    try {
      const data = await api("/api/quick/apps/context?wait=" + (wait ? 1 : 0) + "&lang=" + Q.lang());
      if (seq !== S.seq) return;
      S.settings = data.settings || {}; S.native = !!data.native;
      const ctx = data.context && data.context.kind && !["own", "none"].includes(data.context.kind) ? data.context : null;
      if (ctx && (!S.ctx || S.ctx.id !== ctx.id)) { setContext(ctx, data.presets || []); }
      else if (!ctx && wait && !quiet) { S.ctx = null; S.on = false; S.presets = []; }
    } catch (e) { /* offline: no context */ }
    S.loading = false;
    render();
  }
  function setContext(ctx, presets) {
    S.ctx = ctx || null;
    S.on = !!(ctx && (ctx.folder || ctx.file || (ctx.doc && ctx.doc.name)));
    S.presets = Array.isArray(presets) ? presets : S.presets;
    S.more = false;
    if (S.view && S.view !== "ask") closeView(false);
    render();
    Q.emit("app-context", { context: S.ctx, attached: S.on });
  }
  const hasFolder = () => !!(S.ctx && S.ctx.folder);
  const selCount = () => (S.ctx ? S.ctx.selected_count || 0 : 0);
  function chipText(ctx) {
    if (ctx.folder) {
      const label = ctx.kind === "desktop" ? t("Рабочий стол", "Desktop") : (ctx.kind === "terminal" ? t("Папка терминала", "Terminal folder") : t("Папка", "Folder"));
      let text = `${label}: ${ctx.kind === "desktop" ? "" : ctx.folder_name}`.replace(/:\s*$/, "");
      const c = selCount();
      if (c) {
        const dirs = ctx.selected_dirs || 0, files = c - dirs;
        const parts = [];
        if (files) parts.push(t(`${files} ${n(files, "файл", "файла", "файлов")}`, `${files} file${files === 1 ? "" : "s"}`));
        if (dirs) parts.push(t(`${dirs} ${n(dirs, "папка", "папки", "папок")}`, `${dirs} folder${dirs === 1 ? "" : "s"}`));
        text += " · " + t("выбрано ", "selected ") + parts.join(t(" и ", " and "));
      }
      if (ctx.file_name) text = `${t("Файл", "File")}: ${ctx.file_name}` + (ctx.kind === "vscode" || ctx.kind === "jetbrains" ? ` · ${ctx.folder_name}` : "");
      return text;
    }
    if (ctx.file_name) return `${t("Файл", "File")}: ${ctx.file_name}`;
    if (ctx.doc && ctx.doc.name) return `${ctx.app}: ${ctx.doc.name}`;
    return ctx.app || "";
  }
  function chipIcon(ctx) {
    if (ctx.file_name && ["vscode", "editor", "jetbrains"].includes(ctx.kind)) return IC.code;
    if (ctx.kind === "office") return IC.doc;
    if (ctx.kind === "terminal") return IC.term;
    if (ctx.folder) return IC.folder;
    return IC.app;
  }
  function chipTitle(ctx) {
    const lines = [];
    if (ctx.folder) lines.push(ctx.folder);
    if (ctx.selected && ctx.selected.length) lines.push(ctx.selected.slice(0, 12).join("\n") + (ctx.selected.length > 12 ? "\n…" : ""));
    if (ctx.approx) lines.push(t("PowerShell не сообщает о cd — это может быть папка запуска терминала.", "PowerShell does not report cd — this may be the folder the terminal started in."));
    if (ctx.reason === "file_unknown") lines.push(t("Путь к файлу неизвестен — откройте быстрый доступ над его папкой в Проводнике.", "The file path is unknown — open Quick Access over its folder in Explorer."));
    return lines.join("\n");
  }
  function accessBadge(ctx) {
    if (!ctx.folder) return ctx.doc ? `<span class="qe-badge">${esc(t("только чтение", "read only"))}</span>` : "";
    if (!ctx.writable) return `<span class="qe-badge lock" title="${esc(t("Системная папка, корень диска или нет прав на запись: можно читать, но не менять.", "A system folder, a drive root or no write access: read, but no changes."))}">${IC.lock}${esc(t("только чтение", "read only"))}</span>`;
    const full = S.settings.access === "full";
    return `<span class="qe-badge ${full ? "full" : ""}" title="${esc(full ? t("Полный доступ: безопасные изменения применяются сразу, удаление и перезапись — по кнопке.", "Full access: safe changes are applied at once, deletes and overwrites wait for a click.") : t("Спрашивать: каждое изменение сначала показывается.", "Ask: every change is previewed first."))}">${esc(full ? t("полный доступ", "full access") : t("с подтверждением", "ask first"))}</span>`;
  }

  /* ---------------------------------------------------------- actions */
  function sel(ru, en) {
    const c = selCount();
    return c ? t(`выбранные файлы (${c})`, `the selected files (${c})`) : t(ru, en);
  }
  function actionsFor(ctx) {
    const out = [];
    if (!ctx) return out;
    const write = ctx.writable;
    if (ctx.folder && !ctx.file) {
      if (selCount()) out.push({ id: "summarize", icon: IC.list, label: t("Кратко о выбранном", "Summarize selection") });
      out.push({ id: "explain", icon: IC.info, label: ctx.kind === "terminal" ? t("Что это за проект", "What is this project") : t("Объяснить папку", "Explain the folder") });
      if (write) {
        out.push({ id: "rename", icon: IC.rename, label: t("Переименовать", "Rename"), ask: true });
        if (!selCount() || ctx.selected_count > 1) out.push({ id: "sort", icon: IC.sort, label: t("Разложить по папкам", "Sort into folders"), ask: true });
        out.push({ id: "transform", icon: IC.wand, label: t("Преобразовать текст", "Transform text"), ask: true });
        out.push({ id: "create", icon: IC.plus, label: t("Создать файл", "Create a file"), ask: true });
      }
      out.push({ id: "dups", icon: IC.dup, label: t("Дубликаты", "Duplicates") });
      out.push({ id: "search", icon: IC.search, label: t("Поиск в файлах", "Search in files") });
    }
    if (ctx.file || ctx.file_name) {
      out.push({ id: "file-explain", icon: IC.info, label: t("Объяснить файл", "Explain the file") });
      out.push({ id: "file-bugs", icon: IC.bug, label: t("Найти ошибки", "Find bugs") });
      if (ctx.folder && write) out.push({ id: "file-edit", icon: IC.rename, label: t("Изменить файл", "Edit the file"), ask: true });
      if (ctx.folder) out.push({ id: "search", icon: IC.search, label: t("Поиск в проекте", "Search the project") });
    }
    if (ctx.doc && ctx.doc.name && !ctx.doc.off) {
      out.push({ id: "doc-summary", icon: IC.list, label: t("Кратко о документе", "Summarize the document") });
      out.push({ id: "doc-ask", icon: IC.spark, label: t("Спросить о документе", "Ask about the document") });
    }
    for (const p of S.presets || []) out.push({ id: "preset:" + p.id, icon: IC.spark, label: p.name, preset: p });
    return out;
  }
  const ASKS = {
    rename: () => ({ title: t("Как переименовать?", "How to rename?"), placeholder: t("например: по дате в начале, без пробелов, латиницей", "e.g. date first, no spaces, lowercase") }),
    sort: () => ({ title: t("Как разложить?", "How to sort?"), placeholder: t("по типу (по умолчанию), по проекту, по году…", "by type (default), by project, by year…"), optional: true }),
    transform: () => ({ title: t("Что сделать с текстом файлов?", "What to do with the text?"), placeholder: t("переведи на английский · исправь ошибки · CSV → Markdown", "translate to Russian · fix typos · CSV → Markdown") }),
    create: () => ({ title: t("Какой файл создать?", "What file to create?"), placeholder: t("например: README с описанием этой папки", "e.g. a README describing this folder") }),
    "file-edit": () => ({ title: t("Что изменить в файле?", "What to change in the file?"), placeholder: t("например: добавь обработку ошибок", "e.g. add error handling") }),
  };
  function promptFor(id, extra) {
    const ctx = S.ctx || {};
    const target = sel("файлы этой папки", "the files of this folder");
    switch (id) {
      case "summarize": return t(`Кратко перескажи ${target}: по каждому 1–3 предложения (прочитай их через folder_read), затем общий вывод одной строкой.`,
        `Summarize ${target}: 1–3 sentences each (read them with folder_read), then a one-line overall takeaway.`);
      case "explain": return t("Объясни, что это за папка: назначение, структура, ключевые файлы; если это проект — стек и как его запустить. Используй folder_info, folder_list и прочитай 2–4 главных файла.",
        "Explain what this folder is: purpose, structure, key files; if it is a project — the stack and how to run it. Use folder_info, folder_list and read 2–4 key files.");
      case "rename": return t(`Переименуй ${target} по правилу: «${extra}». Сохрани расширения, имена должны быть уникальны и допустимы в Windows. Предложи ВСЕ переименования ОДНИМ вызовом folder_change (op "move"). Ничего не удаляй.`,
        `Rename ${target} by this rule: “${extra}”. Keep extensions; names must be unique and valid on Windows. Propose ALL renames in ONE folder_change call (op "move"). Delete nothing.`);
      case "sort": return t(`Разложи ${selCount() ? "выбранные файлы" : "файлы верхнего уровня этой папки"} по подпапкам${extra ? ` по правилу: «${extra}»` : " по типу (Документы, Изображения, Архивы, Видео, Музыка, Установщики, Таблицы, Прочее — только нужные)"}. Сначала посмотри список (folder_list), потом ОДИН вызов folder_change: mkdir + move. Существующие подпапки не трогай, ничего не удаляй.`,
        `Sort ${selCount() ? "the selected files" : "the top-level files of this folder"} into subfolders${extra ? ` by this rule: “${extra}”` : " by type (Documents, Images, Archives, Video, Music, Installers, Spreadsheets, Other — only the needed ones)"}. Look at the list first (folder_list), then ONE folder_change call: mkdir + move. Leave existing subfolders alone, delete nothing.`);
      case "transform": return t(`Обработай текстовые ${target}: ${extra}. Прочитай каждый (folder_read) и сохрани результат НОВЫМИ файлами рядом (к имени добавь «-new» перед расширением) одним вызовом folder_change; исходники не меняй, если я явно не попросил.`,
        `Process the text of ${target}: ${extra}. Read each (folder_read) and save the result as NEW files next to them (add “-new” before the extension) in one folder_change call; do not modify the originals unless I explicitly asked.`);
      case "create": return t(`Создай в этой папке файл: ${extra}. Если нужно, сначала посмотри папку. Используй folder_change (op "write").`,
        `Create a file in this folder: ${extra}. Look at the folder first if needed. Use folder_change (op "write").`);
      case "file-explain": return t("Объясни этот файл (он в контексте как active_file): назначение, как устроен, главные места. Коротко.",
        "Explain this file (it is in the context as active_file): purpose, how it works, the key parts. Briefly.");
      case "file-bugs": return t("Найди ошибки и слабые места в активном файле. Список: строка — проблема — как исправить. Только реальные проблемы.",
        "Find bugs and weak spots in the active file. A list: line — problem — fix. Real problems only.");
      case "file-edit": return t(`Измени активный файл: ${extra}. Точечные правки через folder_change (op "edit", old_text/new_text), путь — относительно папки проекта.`,
        `Change the active file: ${extra}. Targeted edits with folder_change (op "edit", old_text/new_text), the path relative to the project folder.`);
      case "doc-summary": return t(`Прочитай открытый документ (app_document) и перескажи его: суть, главные пункты, сроки/цифры, если есть.`,
        "Read the open document (app_document) and summarize it: the gist, key points, dates/figures if any.");
      default: return "";
    }
  }
  function runAction(action) {
    if (!action) return;
    if (action.preset) {
      const p = action.preset;
      Q.send(p.name, { prompt: p.prompt, direct: true });
      return;
    }
    switch (action.id) {
      case "dups": openView("dups"); return;
      case "search": openView("search"); return;
      case "doc-ask": Q.setInput("", { focus: true }); Q.toast(t("Спросите о документе — я прочитаю его.", "Ask about the document — I will read it."), "", 2200); return;
      default: break;
    }
    if (action.ask) { S.ask = { id: action.id, ...ASKS[action.id]() }; openView("ask"); return; }
    const prompt = promptFor(action.id);
    if (prompt) Q.send(action.label, { prompt, direct: true });
  }
  function submitAsk(value) {
    const ask = S.ask;
    if (!ask) return;
    const text = String(value || "").trim();
    if (!text && !ask.optional) { const input = panel && panel.querySelector("#qe-ask-input"); if (input) input.focus(); return; }
    const label = (actionsFor(S.ctx).find((a) => a.id === ask.id) || {}).label || ask.title;
    closeView(false);
    Q.send(label + (text ? ": " + text : ""), { prompt: promptFor(ask.id, text), direct: true });
  }

  /* ---------------------------------------------------------- views */
  function openView(view) {
    S.view = view; S.index = 0;
    if (view === "dups") { S.dups = null; loadDups(); }
    if (view === "search") { S.results = null; }
    root.dataset.qeView = view;
    render();
    setTimeout(() => { const input = panel && panel.querySelector(view === "ask" ? "#qe-ask-input" : "#qe-q"); if (input) input.focus(); }, 30);
  }
  function closeView(focus = true) {
    S.view = ""; S.ask = null; S.results = null; S.dups = null;
    delete root.dataset.qeView;
    render();
    if (focus) Q.setInput(Q.getInput(), { focus: true });
  }
  async function loadDups() {
    if (!S.ctx) return;
    S.busy = true; render();
    try { S.dups = await api("/api/quick/explorer/duplicates", { id: S.ctx.id }); S.dups.keep = {}; }
    catch (e) { S.dups = { error: e.message }; }
    S.busy = false; render();
  }
  let searchTimer = 0;
  function search(query) {
    S.query = query;
    clearTimeout(searchTimer);
    if (!query.trim()) { S.results = null; render(); return; }
    searchTimer = setTimeout(async () => {
      S.busy = true; renderView();
      try { S.results = await api("/api/quick/explorer/search", { id: S.ctx.id, query }); }
      catch (e) { S.results = { error: e.message }; }
      S.busy = false; S.index = 0; renderView();
    }, 260);
  }
  async function reveal(path, open) {
    try {
      const r = await api("/api/quick/apps/reveal", { id: S.ctx.id, path, open: !!open });
      if (!r.ok) Q.toast(r.native === false ? t("Работает в приложении MemuAI для Windows", "Works in the MemuAI Windows app") : t("Не удалось открыть", "Could not open"), "err");
    } catch (e) { Q.toast(e.message, "err"); }
  }

  /* ---------------------------------------------------------- render */
  function render() {
    if (!panel) return;
    const ctx = S.ctx;
    const visible = !!(ctx || S.loading);
    panel.hidden = !visible;
    panel.dataset.mode = S.view ? "view" : "chip";
    if (!visible) { panel.innerHTML = ""; Q.resize(); return; }
    if (S.view) { renderView(); return; }
    if (S.loading && !ctx) {
      panel.innerHTML = `<div class="qe-chip pending"><span class="ic">${IC.folder}</span><span class="tx">${esc(t("Смотрю, что открыто…", "Looking at the open window…"))}</span></div>`;
      Q.resize(); return;
    }
    const st = Q.state();
    const idle = !st.turns.length && !st.busy;
    const actions = actionsFor(ctx);
    S.typing = !!Q.getInput().trim();
    const showActions = S.on && actions.length && ((idle && !S.typing) || S.more);
    const chip = (ctx.folder || ctx.file_name || (ctx.doc && ctx.doc.name))
      ? `<div class="qe-chip ${S.on ? "" : "off"}" title="${esc(chipTitle(ctx))}">
          <span class="ic">${chipIcon(ctx)}</span><span class="tx">${esc(chipText(ctx))}</span>${S.on ? accessBadge(ctx) : ""}
          ${S.on ? (!idle && actions.length ? `<button type="button" class="qa-icon qe-more ${S.more ? "on" : ""}" data-qe="more" title="${esc(t("Действия с папкой", "Folder actions"))}" aria-label="${esc(t("Действия", "Actions"))}">${IC.chev}</button>` : "")
            + `<button type="button" class="qa-icon" data-qe="detach" title="${esc(t("Не использовать в вопросах", "Do not use in questions"))}" aria-label="${esc(t("Убрать", "Remove"))}">${IC.x}</button>`
            : `<button type="button" class="qa-btn qe-attach" data-qe="attach">${esc(t("Использовать", "Use"))}</button>`}
        </div>`
      : (S.presets.length ? `<div class="qe-for">${esc(t(`Для ${ctx.app}:`, `For ${ctx.app}:`))}</div>` : "");
    const row = (showActions || (!ctx.folder && !ctx.file_name && !(ctx.doc && ctx.doc.name) && S.presets.length && idle && !S.typing))
      ? `<div class="qe-actions" role="toolbar" aria-label="${esc(t("Действия", "Actions"))}">${actions.map((a, i) => `<button type="button" class="qe-act ${a.preset ? "preset" : ""}" data-qe-act="${i}" title="${esc(a.preset ? a.preset.prompt.slice(0, 200) : a.label)}">${a.icon}<span>${esc(a.label)}</span></button>`).join("")}</div>`
      : "";
    if (!chip && !row) { panel.hidden = true; panel.innerHTML = ""; Q.resize(); return; }
    panel.innerHTML = chip + row;
    bindChip(actions);
    Q.resize();
  }
  function bindChip(actions) {
    panel.querySelectorAll("[data-qe-act]").forEach((b) => { b.onclick = () => runAction(actions[+b.dataset.qeAct]); });
    const d = panel.querySelector('[data-qe="detach"]');
    if (d) d.onclick = () => { S.on = false; render(); Q.setInput(Q.getInput(), { focus: true }); };
    const a = panel.querySelector('[data-qe="attach"]');
    if (a) a.onclick = () => { S.on = true; render(); };
    const m = panel.querySelector('[data-qe="more"]');
    if (m) m.onclick = () => { S.more = !S.more; render(); };
  }
  function head(title, withSearch, placeholder) {
    return `<div class="qa-view-head qe-head"><button type="button" class="qa-icon qa-back" data-qe="back" title="${esc(t("Назад (Esc)", "Back (Esc)"))}" aria-label="${esc(t("Назад", "Back"))}">${IC.back}</button>
      ${withSearch ? `<label class="qa-search">${IC.search}<input id="qe-q" type="text" spellcheck="false" autocomplete="off" placeholder="${esc(placeholder)}" value="${esc(S.query)}" aria-label="${esc(placeholder)}"></label>`
        : `<span class="qe-title">${esc(title)}</span>`}
      ${S.busy ? `<span class="qe-spin" aria-hidden="true"></span>` : ""}</div>`;
  }
  function renderView() {
    if (!panel || !S.view) return;
    let html = "";
    if (S.view === "ask") {
      const ask = S.ask || {};
      html = head(ask.title) + `<form class="qe-ask" id="qe-ask"><div class="qe-ask-ctx">${chipIcon(S.ctx)}<span>${esc(chipText(S.ctx))}</span></div>
        <input id="qe-ask-input" type="text" autocomplete="off" spellcheck="true" placeholder="${esc(ask.placeholder || "")}" aria-label="${esc(ask.title || "")}">
        <div class="qe-ask-foot"><span>${esc(S.settings.access === "full" ? t("Безопасные изменения применятся сразу; удаление и перезапись — по кнопке.", "Safe changes are applied at once; deletes and overwrites wait for a click.") : t("Сначала покажу изменения — применятся только после «Применить».", "I will show the changes first — nothing happens before “Apply”."))}</span>
        <button type="submit" class="qa-btn accent">${esc(t("Продолжить", "Continue"))} <kbd>↵</kbd></button></div></form>`;
    } else if (S.view === "search") {
      const r = S.results;
      let body = "";
      if (!r) body = `<div class="qe-empty">${IC.search}<span>${esc(t("Имена файлов и текст внутри файлов этой папки. Enter — показать в Проводнике, Shift+Enter — открыть, Tab — спросить ИИ.", "File names and text inside files of this folder. Enter — show in Explorer, Shift+Enter — open, Tab — ask AI."))}</span></div>`;
      else if (r.error) body = `<div class="qe-empty">${esc(r.error)}</div>`;
      else {
        const rows = [...(r.names || []).map((p) => ({ path: p.replace(/\/$/, ""), name: true, text: "" })), ...(r.matches || []).map((m) => ({ path: m.path, line: m.line, text: m.text }))];
        S.rows = rows;
        body = rows.length ? `<div class="qe-list" role="listbox">${rows.map((row, i) => `<div class="qa-row qe-row ${i === S.index ? "active" : ""}" role="option" data-i="${i}" aria-selected="${i === S.index}">
            <span class="ic">${row.name ? IC.file : IC.search}</span><span class="qe-main"><span class="tx">${esc(row.path)}${row.line ? `<i>:${row.line}</i>` : ""}</span>${row.text ? `<small>${esc(row.text)}</small>` : ""}</span>
            <span class="qe-btns"><button type="button" class="qa-icon" data-qe-reveal="${i}" title="${esc(t("Показать в Проводнике", "Show in Explorer"))}">${IC.folder}</button><button type="button" class="qa-icon" data-qe-askrow="${i}" title="${esc(t("Спросить ИИ об этом файле", "Ask AI about this file"))}">${IC.spark}</button></span></div>`).join("")}</div>
            <div class="qe-foot">${esc(t(`Найдено: ${rows.length}`, `Found: ${rows.length}`) + (r.truncated ? t(" (показаны первые)", " (first ones shown)") : "") + t(` · просмотрено файлов: ${r.files_scanned}`, ` · files scanned: ${r.files_scanned}`))}</div>`
          : `<div class="qe-empty">${esc(t("Ничего не найдено", "Nothing found"))}</div>`;
      }
      html = head("", true, t(`Поиск в «${S.ctx.folder_name}»`, `Search “${S.ctx.folder_name}”`)) + body;
    } else if (S.view === "dups") {
      const d = S.dups;
      let body;
      if (!d) body = `<div class="qe-empty"><span class="qe-spin"></span><span>${esc(t("Сравниваю файлы по содержимому…", "Comparing files by content…"))}</span></div>`;
      else if (d.error) body = `<div class="qe-empty">${esc(d.error)}</div>`;
      else if (!d.groups.length) body = `<div class="qe-empty">${IC.check}<b>${esc(t("Дубликатов нет", "No duplicates"))}</b><span>${esc(t(`Проверено файлов: ${d.files_scanned}`, `Files checked: ${d.files_scanned}`))}</span></div>`;
      else {
        const extra = d.groups.reduce((sum, g) => sum + g.files.length - 1, 0);
        body = `<div class="qe-banner">${IC.dup}<span>${esc(t(`Групп: ${d.groups.length} · лишних копий: ${extra} · занимают ${d.wasted_h}. Оставляется самый старый файл — щёлкните по другому, чтобы оставить его.`,
          `Groups: ${d.groups.length} · extra copies: ${extra} · ${d.wasted_h_en}. The oldest file is kept — click another to keep it instead.`))}</span></div>
          <div class="qe-list">${d.groups.map((g, gi) => `<div class="qe-group"><div class="qe-group-h">${esc(human(g.size))} × ${g.files.length}</div>${g.files.map((f, fi) => {
            const keep = (d.keep[gi] || 0) === fi;
            return `<button type="button" class="qe-dup ${keep ? "keep" : "drop"}" data-g="${gi}" data-f="${fi}" title="${esc(f)}"><span class="mk">${keep ? IC.check : IC.trash}</span><span class="tx">${esc(f)}</span><em>${esc(keep ? t("оставить", "keep") : t("удалить", "delete"))}</em></button>`;
          }).join("")}</div>`).join("")}</div>
          ${S.ctx.writable ? `<div class="qe-foot"><span>${esc(t("Удалённое можно вернуть кнопкой «Отменить».", "Deleted copies can be restored with “Undo”."))}</span><button type="button" class="qa-btn accent" data-qe="dups-delete">${esc(t(`Удалить лишние (${extra})`, `Delete extras (${extra})`))}</button></div>` : ""}`;
      }
      html = head(t("Дубликаты", "Duplicates")) + body;
    }
    panel.innerHTML = html;
    bindView();
    Q.resize();
  }
  function bindView() {
    const back = panel.querySelector('[data-qe="back"]');
    if (back) back.onclick = () => closeView();
    const form = panel.querySelector("#qe-ask");
    if (form) form.onsubmit = (e) => { e.preventDefault(); submitAsk(panel.querySelector("#qe-ask-input").value); };
    const q = panel.querySelector("#qe-q");
    if (q) {
      q.oninput = () => search(q.value);
      q.onkeydown = (e) => {
        const rows = S.rows || [];
        if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (!rows.length) return; S.index = (S.index + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length; renderView(); const nq = panel.querySelector("#qe-q"); nq.focus(); nq.setSelectionRange(nq.value.length, nq.value.length); scrollActive(); }
        else if (e.key === "Enter" && rows[S.index]) { e.preventDefault(); reveal(rows[S.index].path, e.shiftKey); }
        else if (e.key === "Tab" && rows[S.index]) { e.preventDefault(); askAbout(rows[S.index]); }
      };
    }
    panel.querySelectorAll("[data-qe-reveal]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); reveal(S.rows[+b.dataset.qeReveal].path, false); }; });
    panel.querySelectorAll("[data-qe-askrow]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); askAbout(S.rows[+b.dataset.qeAskrow]); }; });
    panel.querySelectorAll(".qe-row").forEach((r) => { r.onclick = () => { S.index = +r.dataset.i; renderView(); }; r.ondblclick = () => reveal(S.rows[+r.dataset.i].path, true); });
    panel.querySelectorAll(".qe-dup").forEach((b) => { b.onclick = () => { S.dups.keep[+b.dataset.g] = +b.dataset.f; renderView(); }; });
    const del = panel.querySelector('[data-qe="dups-delete"]');
    if (del) del.onclick = async () => {
      const ops = [];
      S.dups.groups.forEach((g, gi) => g.files.forEach((f, fi) => { if ((S.dups.keep[gi] || 0) !== fi) ops.push({ op: "delete", path: f }); }));
      try {
        const r = await api("/api/quick/explorer/changes", { id: S.ctx.id, ops, summary: t(`Удалить лишние копии (${ops.length})`, `Delete extra copies (${ops.length})`) });
        closeView(false);
        showSet(r.set);
      } catch (e) { Q.toast(e.message, "err"); }
    };
  }
  function scrollActive() { const el = panel.querySelector(".qe-row.active"); if (el) el.scrollIntoView({ block: "nearest" }); }
  function askAbout(row) {
    closeView(false);
    Q.setInput(t(`Что в файле ${row.path}? `, `What is in ${row.path}? `), { focus: true });
  }

  /* ---------------------------------------------------------- change sets */
  const OP_LABEL = () => ({ write: t("файл", "file"), edit: t("правка", "edit"), append: t("дописать", "append"), mkdir: t("папка", "folder"),
    move: t("переименовать", "rename"), copy: t("копия", "copy"), delete: t("удалить", "delete"), trash: t("в Корзину", "to Recycle Bin") });
  const STATUS = () => ({ pending: "", applied: t("готово", "done"), failed: t("ошибка", "failed"), invalid: t("отклонено", "rejected"), discarded: t("отменено", "discarded") });
  function showSet(set) {
    if (!set || !set.id) return;
    S.sets.set(set.id, set);
    while (S.sets.size > 4) S.sets.delete(S.sets.keys().next().value);
    renderCard();
  }
  function moveKind(set) {
    const moves = set.ops.filter((o) => o.op === "move");
    if (!moves.length || moves.length < set.ops.filter((o) => o.op !== "mkdir").length) return "";
    const sameDir = moves.every((o) => (o.path.includes("/") ? o.path.replace(/\/[^/]*$/, "") : "") === (o.to.includes("/") ? o.to.replace(/\/[^/]*$/, "") : ""));
    return sameDir ? "rename" : "sort";
  }
  function diffHtml(diff) {
    return `<pre class="qe-diff">${String(diff || "").split("\n").filter((l) => !/^(---|\+\+\+) /.test(l)).slice(0, 160).map((l) =>
      `<span class="${l.startsWith("+") ? "add" : (l.startsWith("-") ? "del" : (l.startsWith("@@") ? "hunk" : ""))}">${esc(l)}</span>`).join("\n")}</pre>`;
  }
  function opsHtml(set) {
    const kind = moveKind(set), labels = OP_LABEL(), status = STATUS();
    if (set.trash) labels.delete = labels.trash;
    const flag = (o) => (o.status !== "pending" ? `<em class="st ${o.status}">${esc(status[o.status] || o.status)}</em>` : (o.risky && !(o.risky === "delete" && o.op === "delete") ? `<em class="st risky">${esc(o.risky === "delete" ? t("удаление", "delete") : t("перезапись", "overwrite"))}</em>` : ""));
    const err = (o) => (o.error ? `<div class="qe-op-err">${esc(o.error)}</div>` : "");
    if (kind === "rename") {
      return `<table class="qe-table"><thead><tr><th>${esc(t("Было", "Before"))}</th><th></th><th>${esc(t("Станет", "After"))}</th><th></th></tr></thead><tbody>${set.ops.map((o) => o.op === "move"
        ? `<tr class="${o.status}"><td title="${esc(o.path)}">${esc(o.path.split("/").pop())}</td><td class="ar">→</td><td title="${esc(o.to)}"><b>${esc(o.to.split("/").pop())}</b></td><td>${flag(o)}</td></tr>${o.error ? `<tr><td colspan="4">${err(o)}</td></tr>` : ""}`
        : `<tr class="${o.status}"><td colspan="3">${esc(labels[o.op] || o.op)}: ${esc(o.path)}</td><td>${flag(o)}</td></tr>`).join("")}</tbody></table>`;
    }
    if (kind === "sort") {
      const groups = new Map();
      for (const o of set.ops) {
        if (o.op !== "move") continue;
        const dir = o.to.includes("/") ? o.to.replace(/\/[^/]*$/, "") : ".";
        if (!groups.has(dir)) groups.set(dir, []);
        groups.get(dir).push(o);
      }
      return `<div class="qe-sort">${Array.from(groups.entries()).map(([dir, ops]) => `<details class="qe-sort-g" ${groups.size <= 3 ? "open" : ""}><summary>${IC.folder}<b>${esc(dir)}/</b><span>${ops.length}</span></summary>
        <ul>${ops.map((o) => `<li class="${o.status}">${esc(o.path)}${o.path.split("/").pop() !== o.to.split("/").pop() ? ` → ${esc(o.to.split("/").pop())}` : ""} ${flag(o)}${err(o)}</li>`).join("")}</ul></details>`).join("")}
        ${set.ops.filter((o) => o.op !== "move" && o.op !== "mkdir").map((o) => `<div class="qe-op ${o.status}">${esc(labels[o.op] || o.op)}: ${esc(o.path)} ${flag(o)}${err(o)}</div>`).join("")}</div>`;
    }
    return `<div class="qe-ops">${set.ops.map((o) => `<details class="qe-op ${o.status}" ${set.ops.length <= 2 && (o.diff || o.preview) ? "open" : ""}><summary><span class="k ${o.op}">${esc(labels[o.op] || o.op)}</span><span class="p" title="${esc(o.path)}">${esc(o.path)}${o.to ? ` → ${esc(o.to)}` : ""}${o.items ? esc(t(` (${o.items} эл.)`, ` (${o.items} items)`)) : ""}</span>${flag(o)}</summary>
      ${err(o)}${o.diff ? diffHtml(o.diff) : (o.preview ? `<pre class="qe-diff new">${esc(o.preview)}</pre>` : "")}</details>`).join("")}</div>`;
  }
  function renderCard() {
    if (!card) return;
    const sets = Array.from(S.sets.values()).filter((s) => !s.hidden);
    card.hidden = !sets.length;
    const last = sets.length ? sets[sets.length - 1].id : "";
    card.innerHTML = sets.map((set) => {
      const pending = set.ops.filter((o) => o.status === "pending").length;
      const applied = set.ops.filter((o) => o.status === "applied").length;
      const risky = set.ops.filter((o) => o.status === "pending" && o.risky).length;
      const head = pending
        ? (applied ? t(`Применено ${applied}, ждут подтверждения: ${pending}`, `${applied} applied, ${pending} waiting for you`) : t(`Изменения в «${set.folder_name}» — ещё не применены`, `Changes in “${set.folder_name}” — not applied yet`))
        : (applied ? t(`Готово: ${applied} ${n(applied, "изменение", "изменения", "изменений")}`, `Done: ${applied} change${applied === 1 ? "" : "s"}`) : t("Изменения отменены", "Changes discarded"));
      // r29d: three change sets in a row pushed the answer out of the window and the newest card past its bottom
      // edge. A finished set that is not the newest collapses to its header (click to open) with «Undo» kept.
      const compact = !pending && set.id !== last && !set.expanded;
      return `<section class="qe-card ${pending ? "pending" : "done"}${compact ? " compact" : ""}" data-set="${esc(set.id)}">
        <header ${!pending && set.id !== last ? `data-qe-toggle="${esc(set.id)}" title="${esc(compact ? t("Показать", "Show") : t("Свернуть", "Collapse"))}"` : ""}><span class="ic">${pending ? IC.wand : IC.check}</span><div><b>${esc(head)}</b>${set.summary ? `<small>${esc(set.summary)}</small>` : ""}</div>
          ${compact && set.undo && !set.undone ? `<button type="button" class="qa-btn qe-undo-sm" data-qe-undo="${esc(set.undo)}" data-set-id="${esc(set.id)}">${IC.undo}${esc(t("Отменить", "Undo"))}</button>` : ""}
          <button type="button" class="qa-icon" data-qe-hide="${esc(set.id)}" aria-label="${esc(t("Скрыть", "Hide"))}" title="${esc(t("Скрыть", "Hide"))}">${IC.x}</button></header>
        ${compact ? "" : `<div class="qe-card-body">${opsHtml(set)}</div>
        <footer>${pending ? `${risky ? `<span class="qe-warn">${esc(set.trash ? t(`Удаление в Корзину/перезапись: ${risky} — можно вернуть «Отменить».`, `To the Recycle Bin/overwrites: ${risky} — restorable with “Undo”.`) : t(`Удаление/перезапись: ${risky} — удалённое можно вернуть «Отменить».`, `Deletes/overwrites: ${risky} — restorable with “Undo”.`))}</span>` : "<span></span>"}
            <button type="button" class="qa-btn" data-qe-discard="${esc(set.id)}">${esc(t("Не применять", "Discard"))}</button>
            <button type="button" class="qa-btn accent" data-qe-apply="${esc(set.id)}">${esc(t(`Применить (${pending})`, `Apply (${pending})`))} <kbd>Alt ↵</kbd></button>`
          : `<span></span>${applied && set.ops.some((o) => o.status === "applied" && o.op !== "delete") ? `<button type="button" class="qa-btn" data-qe-reveal="${esc(set.id)}">${IC.open}${esc(t("Показать", "Show"))}</button>` : ""}${set.undo && !set.undone ? `<button type="button" class="qa-btn" data-qe-undo="${esc(set.undo)}" data-set-id="${esc(set.id)}">${IC.undo}${esc(t("Отменить", "Undo"))}</button>` : ""}`}</footer>`}</section>`;
    }).join("");
    card.querySelectorAll("[data-qe-apply]").forEach((b) => { b.onclick = () => applySet(b.dataset.qeApply); });
    card.querySelectorAll("[data-qe-discard]").forEach((b) => { b.onclick = () => discardSet(b.dataset.qeDiscard); });
    card.querySelectorAll("[data-qe-undo]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); undoSet(b.dataset.qeUndo, b.dataset.setId); }; });
    card.querySelectorAll("[data-qe-reveal]").forEach((b) => { b.onclick = async (e) => {   // Round 30: the result in Explorer
      e.stopPropagation();
      try { const r = await api("/api/quick/files/reveal", { set: b.dataset.qeReveal }); if (!r.ok) Q.toast(t("Не получилось открыть Проводник", "Could not open Explorer"), "err"); }
      catch (err) { Q.toast(err.message, "err"); }
    }; });
    card.querySelectorAll("[data-qe-hide]").forEach((b) => { b.onclick = (e) => { e.stopPropagation(); const s = S.sets.get(b.dataset.qeHide); if (s) s.hidden = true; renderCard(); }; });
    card.querySelectorAll("[data-qe-toggle]").forEach((h) => { h.onclick = () => { const s = S.sets.get(h.dataset.qeToggle); if (s) { s.expanded = !s.expanded; renderCard(); } }; });
    Q.resize();
  }
  async function applySet(id) {
    try {
      const r = await api(`/api/quick/explorer/changes/${encodeURIComponent(id)}/apply`, {});
      showSet(r.set);
      Q.toast(r.failed ? t(`Применено: ${r.applied}, с ошибкой: ${r.failed}`, `Applied: ${r.applied}, failed: ${r.failed}`) : t("Изменения применены", "Changes applied"), r.failed ? "err" : "ok");
      Q.recordTurn && Q.recordTurn({ q: t("Применить изменения", "Apply the changes"), a: t(`Применено изменений: ${r.applied}${r.failed ? `, ошибок: ${r.failed}` : ""}.`, `Changes applied: ${r.applied}${r.failed ? `, failed: ${r.failed}` : ""}.`) });
    } catch (e) { Q.toast(e.message, "err"); }
  }
  async function discardSet(id) {
    try { const r = await api(`/api/quick/explorer/changes/${encodeURIComponent(id)}/discard`, {}); showSet(r.set); }
    catch (e) { const s = S.sets.get(id); if (s) { s.hidden = true; renderCard(); } }
  }
  async function undoSet(undoId, setId) {
    try {
      const r = await api("/api/quick/explorer/undo", { undo: undoId });
      const s = S.sets.get(setId); if (s) { s.undone = true; s.hidden = true; }
      renderCard();
      Q.toast(r.problems && r.problems.length ? t(`Отменено частично: ${r.problems[0]}`, `Partly undone: ${r.problems[0]}`) : t("Изменения отменены", "Changes undone"), r.problems && r.problems.length ? "err" : "ok", 3200);
    } catch (e) { Q.toast(e.message, "err"); }
  }

  /* ---------------------------------------------------------- wiring */
  Q.registerPanel("explorer", (el) => {
    panel = el; el.classList.add("qe"); el.hidden = true;
    return { show: render };
  }, { slot: "top", order: -5, hidden: false });
  Q.registerPanel("explorer-changes", (el) => { card = el; el.classList.add("qe-cards"); el.hidden = true; return {}; }, { slot: "bottom", order: 5 });
  const qaInput = document.getElementById("qa-input");
  if (qaInput) qaInput.addEventListener("input", () => { if (!S.view && S.ctx && !!qaInput.value.trim() !== !!S.typing) render(); });
  // Round 30: a question typed before the program was read (PowerShell takes 1–2 s) waits for it on the server
  // ({pending}) — it used to go without the folder, and the model then answered with a command to type.
  Q.addRequestContext("app", () => (S.on && S.ctx && S.ctx.id ? { type: "app", id: S.ctx.id } : (S.loading && !S.ctx ? { type: "app", pending: true } : null)));

  Q.on("open", (e) => {
    if (e && (e.source === "hotkey" || e.source === "")) {
      S.ctx = null; S.on = false; S.presets = [];        // a new Alt+Space: never show the previous program's context
      loadContext(true);
    } else render();
  });
  Q.on("chat-new", () => { S.more = false; S.sets.clear(); renderCard(); render(); });
  Q.on("answer-start", () => { if (S.view && S.view !== "ask") closeView(false); else render(); });
  Q.on("answer-done", () => render());
  Q.on("tool-ui", (e) => { if (e && e.ui && e.ui.type === "changeset") showSet(e.ui.set); });
  Q.on("escape", (e) => { if (S.view) { e.preventDefault(); closeView(); } });
  window.addEventListener("focus", () => { if (!S.view && !Q.state().busy) loadContext(true, true); });
  document.addEventListener("keydown", (e) => {
    if (e.altKey && e.key === "Enter" && !e.ctrlKey && !e.shiftKey) {
      const pending = Array.from(S.sets.values()).reverse().find((s) => !s.hidden && s.ops.some((o) => o.status === "pending"));
      if (pending) { e.preventDefault(); e.stopPropagation(); applySet(pending.id); }
    }
  }, true);
  Q.addAction && Q.addAction({ id: "explorer-undo", section: t("Папка", "Folder"), icon: IC.undo, label: t("Отменить последние изменения в папке", "Undo the last folder changes"),
    run: async () => { try { const r = await api("/api/quick/explorer/undo", {}); Q.toast(t(`Отменено: ${r.summary || r.undone}`, `Undone: ${r.summary || r.undone}`), "ok"); } catch (e) { Q.toast(e.message, "err"); } } });
  Q.addAction && Q.addAction({ id: "explorer-search", section: t("Папка", "Folder"), icon: IC.search, label: t("Поиск в файлах папки", "Search in folder files"),
    hidden: () => !hasFolder(), run: () => openView("search") });
  Q.addAction && Q.addAction({ id: "explorer-dups", section: t("Папка", "Folder"), icon: IC.dup, label: t("Найти дубликаты", "Find duplicates"),
    hidden: () => !hasFolder(), run: () => openView("dups") });

  window.QuickExplorer = {
    context: () => (S.ctx ? { ...S.ctx } : null), attached: () => S.on,
    setContext: (ctx, presets) => setContext(ctx, presets), openView, reload: () => loadContext(false),
  };
})();
