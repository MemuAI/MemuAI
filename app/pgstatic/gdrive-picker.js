/* Round 29 — «Добавить с Google Диска» / «Add from Google Drive» (window.MemuDrivePick).
 * Opened from the composer «+» menu (tools.js). Browses Google Drive through
 * /api/pg/gdrive/* (app/gdrive_picker.py), downloads the chosen files (Google
 * Docs/Sheets/Slides are exported) and hands them to addAttachmentFiles() —
 * exactly like files picked on the disk.
 */
"use strict";
window.MemuDrivePick = (() => {
  const FORMAT_KEY = "memu_gdrive_formats";
  const DEFAULT_FORMATS = { doc: "docx", sheet: "md", slides: "pdf", drawing: "png" };
  const GOOGLE_KIND = {
    "application/vnd.google-apps.document": "doc", "application/vnd.google-apps.spreadsheet": "sheet",
    "application/vnd.google-apps.presentation": "slides", "application/vnd.google-apps.drawing": "drawing",
  };
  let status = null;          // last /status
  let statusAt = 0;
  let state = null;           // dialog state while open
  let pollTimer = 0;

  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const L = (ru, english) => (en() ? english : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const glyph = (name) => (typeof icon === "function" ? icon(name) : "");
  const note = (text, type = "ok", ms) => { try { toast(text, type, ms); } catch (_) {} };
  const $d = (selector) => document.querySelector("#panel " + selector);

  /* ---------------------------------- icons --------------------------------- */
  const DRIVE = `<svg viewBox="0 0 87.3 78" aria-hidden="true"><path d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z" fill="#0066da"/><path d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z" fill="#00ac47"/><path d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.502l5.852 11.5z" fill="#ea4335"/><path d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z" fill="#00832d"/><path d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z" fill="#2684fc"/><path d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z" fill="#ffba00"/></svg>`;
  const PATHS = {
    folder: `<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3.6l2 2.2h7.4A2.5 2.5 0 0 1 21 9.7v7.8a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5z"/>`,
    doc: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 11h8M8 14h8M8 17h5" stroke="var(--gd-ink)" stroke-width="1.6" stroke-linecap="round"/>`,
    sheet: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 10.5h8v7H8zM8 14h8M12 10.5v7" stroke="var(--gd-ink)" stroke-width="1.4" fill="none"/>`,
    slides: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><rect x="8" y="10.5" width="8" height="6" rx="1" stroke="var(--gd-ink)" stroke-width="1.5" fill="none"/>`,
    pdf: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><text x="12" y="17" text-anchor="middle" font-size="5.6" font-weight="800" font-family="Arial, sans-serif" fill="var(--gd-ink)">PDF</text>`,
    image: `<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="9.5" r="1.7" fill="var(--gd-ink)"/><path d="m5.5 17.5 4.5-4.5 3 3 2.5-2.5 3 4" stroke="var(--gd-ink)" stroke-width="1.6" fill="none" stroke-linejoin="round"/>`,
    video: `<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><path d="M10.5 9.3v5.4l4.5-2.7z" fill="var(--gd-ink)"/>`,
    audio: `<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M10 15.5V8.8l5.5-1.3v6.7" stroke="var(--gd-ink)" stroke-width="1.6" fill="none"/><circle cx="8.8" cy="15.6" r="1.6" fill="var(--gd-ink)"/><circle cx="14.3" cy="14.3" r="1.6" fill="var(--gd-ink)"/>`,
    archive: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M11 5v2h2v2h-2v2h2v2h-2v3h2" stroke="var(--gd-ink)" stroke-width="1.4" fill="none"/>`,
    code: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="m10 11-2.3 2.5L10 16M14 11l2.3 2.5L14 16" stroke="var(--gd-ink)" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`,
    text: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M8 11h8M8 14h6M8 17h7" stroke="var(--gd-ink)" stroke-width="1.6" stroke-linecap="round"/>`,
    drawing: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="m8.5 17 1-3.3 5-5 2.3 2.3-5 5z" fill="var(--gd-ink)"/>`,
    form: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><circle cx="9" cy="11.5" r="1" fill="var(--gd-ink)"/><circle cx="9" cy="15" r="1" fill="var(--gd-ink)"/><path d="M11.5 11.5H16M11.5 15H16" stroke="var(--gd-ink)" stroke-width="1.5"/>`,
    file: `<path d="M6 3h8l4 4v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v4h4" stroke="var(--gd-ink)" stroke-width="1.4" fill="none"/>`,
  };
  const NAV = {
    mine: `<path d="M12 3 3.5 17h17z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M7.7 10 12 17M16.3 10 12 17" stroke="currentColor" stroke-width="1.5"/>`,
    recent: `<circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M12 7.5V12l3 2" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>`,
    shared: `<circle cx="9" cy="9" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M3.5 18.5c.8-3 3-4.5 5.5-4.5s4.7 1.5 5.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="16.5" cy="9.5" r="2.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M16 13.5c2.4 0 4 1.3 4.6 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>`,
    starred: `<path d="m12 3.8 2.5 5.2 5.6.7-4.1 3.9 1 5.6-5-2.7-5 2.7 1-5.6-4.1-3.9 5.6-.7z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>`,
  };
  const fileIcon = (kind) => `<svg class="gd-ficon gd-k-${esc(kind)}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[kind] || PATHS.file}</svg>`;
  const navIcon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${NAV[name]}</svg>`;

  /* --------------------------------- helpers -------------------------------- */
  async function api(path, options = {}) {
    const response = await fetch(path, options);
    if (options.raw) return response;
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.error || `HTTP ${response.status}`); error.code = data.code || ""; throw error; }
    return data;
  }
  function size(bytes) {
    const n = Number(bytes) || 0;
    if (!n) return "";
    const units = en() ? ["B", "KB", "MB", "GB"] : ["Б", "КБ", "МБ", "ГБ"];
    let value = n, unit = 0;
    while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
    return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1).replace(".", en() ? "." : ",")} ${units[unit]}`;
  }
  function when(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    const locale = en() ? "en-US" : "ru-RU";
    if (sameDay) return date.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
    return date.toLocaleDateString(locale, { day: "numeric", month: "short", year: date.getFullYear() === now.getFullYear() ? undefined : "numeric" });
  }
  function formats() {
    try { return { ...DEFAULT_FORMATS, ...(JSON.parse(localStorage.getItem(FORMAT_KEY) || "{}") || {}) }; }
    catch (_) { return { ...DEFAULT_FORMATS }; }
  }
  function setFormat(kind, value) {
    const next = { ...formats(), [kind]: value };
    try { localStorage.setItem(FORMAT_KEY, JSON.stringify(next)); } catch (_) {}
  }
  const FORMAT_LABEL = () => ({
    docx: L("Word (.docx)", "Word (.docx)"), pdf: "PDF", md: L("Markdown", "Markdown"), txt: L("Текст (.txt)", "Text (.txt)"),
    xlsx: L("Excel (.xlsx), как файл", "Excel (.xlsx), as a file"), csv: L("CSV (первый лист)", "CSV (first sheet)"),
    pptx: "PowerPoint (.pptx)", png: "PNG", json: "JSON",
  });
  const KIND_LABEL = () => ({ doc: L("Документы", "Docs"), sheet: L("Таблицы", "Sheets"), slides: L("Презентации", "Slides"), drawing: L("Рисунки", "Drawings") });
  const formatFor = (file) => { const kind = GOOGLE_KIND[file.mime]; return kind ? formats()[kind] : ""; };

  /** Status for the «+» menu (sync, cached; refreshed in the background). */
  function menuHint() {
    if (Date.now() - statusAt > 20000) refreshStatus();
    if (!status) return L("Документы, таблицы, PDF и картинки с вашего Диска", "Docs, sheets, PDFs and images from your Drive");
    if (status.state === "ready") return L("Документы, таблицы, PDF и картинки с вашего Диска", "Docs, sheets, PDFs and images from your Drive");
    if (status.state === "need_consent") return L("Нужно разрешение на чтение файлов — один раз", "Needs permission to read files — once");
    return L("Не подключён — подключите в «Интеграциях»", "Not connected — connect it in Integrations");
  }
  async function refreshStatus() {
    statusAt = Date.now();
    try { status = await api("/api/pg/gdrive/status"); } catch (_) { /* the menu keeps the generic hint */ }
    return status;
  }

  /* ---------------------------------- dialog -------------------------------- */
  function shell() {
    return `<div class="gd" data-gd>
      <header class="gd-head">
        <span class="gd-logo">${DRIVE}</span>
        <div class="gd-titles"><h2>${L("Добавить с Google Диска", "Add from Google Drive")}</h2>
          <small class="gd-account" data-gd-account></small></div>
      </header>
      <div class="gd-body" data-gd-body><div class="gd-loading"><span class="gd-spin"></span>${L("Проверяю подключение…", "Checking the connection…")}</div></div>
    </div>`;
  }

  async function open() {
    if (typeof closeFloatingMenus === "function") try { closeFloatingMenus(); } catch (_) {}
    state = { view: "folder", folder: "root", path: [], query: "", kind: "all", order: "", files: [], next: "", selected: new Map(),
      cursor: -1, anchor: -1, loading: false, busy: false, seq: 0 };
    panel(shell(), "gdrive-panel", { onClose: () => { clearInterval(pollTimer); pollTimer = 0; state = null; } });
    await refreshStatus();
    if (!state) return;
    render();
  }

  function render() {
    if (!state) return;
    const body = $d("[data-gd-body]");
    const account = $d("[data-gd-account]");
    if (!body) return;
    if (account) account.textContent = status && status.account && status.account.email ? status.account.email : "";
    if (!status) { body.innerHTML = emptyCard("err", L("Не удалось проверить Google Диск", "Could not check Google Drive"), L("MemuAI не ответил. Попробуйте ещё раз.", "MemuAI did not answer. Try again."), [["retry", L("Повторить", "Retry"), true]]); return bindEmpty(); }
    if (status.state === "ready") return renderBrowser();
    const flow = status.flow || {};
    if (flow.state === "waiting" && flow.purpose === "picker") {
      body.innerHTML = emptyCard("wait", L("Подтвердите доступ в браузере", "Confirm access in your browser"),
        L("Google открыл страницу разрешения. Войдите в тот же аккаунт и нажмите «Разрешить» — окно обновится само.",
          "Google opened the permission page. Sign in to the same account and press «Allow» — this window updates by itself."),
        [["reopen", L("Открыть страницу ещё раз", "Open the page again"), false], ["cancel-auth", L("Отмена", "Cancel"), false]], true);
      startPolling();
      return bindEmpty();
    }
    const error = flow.state === "error" && flow.purpose === "picker" ? (en() ? flow.error_en : flow.error_ru) || "" : "";
    if (status.state === "need_consent") {
      body.innerHTML = emptyCard("key", L("Разрешите MemuAI видеть ваши файлы", "Let MemuAI see your files"),
        L("Резервные копии работают с доступом только к своей папке. Чтобы вы могли выбирать документы, Google спросит одно дополнительное разрешение — <b>просмотр файлов на Google Диске</b> (только чтение: MemuAI ничего не меняет и не удаляет). Это нужно один раз; файлы скачиваются только те, что вы выберете, и только на этот компьютер.",
          "Backups only use access to their own folder. To let you pick documents, Google will ask for one more permission — <b>see your Google Drive files</b> (read-only: MemuAI never changes or deletes anything). It is asked once; only the files you pick are downloaded, and only to this computer."),
        [["authorize", L("Разрешить доступ", "Allow access"), true], ["docs", L("Подробнее", "Learn more"), false]], false, error);
      return bindEmpty();
    }
    body.innerHTML = emptyCard("drive", L("Google Диск не подключён", "Google Drive is not connected"),
      status.state === "no_client"
        ? L("Подключите Google в «Настройки → Интеграции → Google»: там же — пошаговый мастер, если нужен свой OAuth-клиент. Потом здесь можно будет выбирать файлы с Диска.",
          "Connect Google in Settings → Integrations → Google — it also has a step-by-step wizard if you need your own OAuth client. Then you can pick files from Drive here.")
        : L("OAuth-клиент уже указан. Войдите в Google — заодно MemuAI попросит разрешение читать файлы, чтобы вы могли их выбирать.",
          "The OAuth client is already set. Sign in to Google — MemuAI will also ask for permission to read files so you can pick them."),
      status.state === "no_client"
        ? [["sync", L("Открыть «Интеграции»", "Open Integrations"), true], ["docs", L("Как это работает", "How it works"), false]]
        : [["authorize", L("Войти в Google", "Sign in to Google"), true], ["sync", L("Настройки Google", "Google settings"), false]], false, error);
    bindEmpty();
  }

  function emptyCard(kind, title, text, buttons, spinner, error) {
    const art = kind === "drive" ? `<span class="gd-empty-logo">${DRIVE}</span>`
      : kind === "key" ? `<span class="gd-empty-logo key">${DRIVE}<i>${glyph("check")}</i></span>`
        : kind === "wait" ? `<span class="gd-empty-logo">${DRIVE}<span class="gd-spin big"></span></span>` : `<span class="gd-empty-logo">${glyph("info")}</span>`;
    return `<div class="gd-empty">${art}<h3>${esc(title)}</h3><p>${text}</p>
      ${error ? `<div class="gd-error">${glyph("info")}<span>${esc(error)}</span></div>` : ""}
      <div class="gd-empty-actions">${buttons.map(([id, label, primary]) => `<button type="button" class="pbtn ${primary ? "" : "ghost"}" data-gd-act="${id}">${esc(label)}</button>`).join("")}</div>
      ${spinner ? "" : ""}</div>`;
  }

  function bindEmpty() {
    document.querySelectorAll("#panel [data-gd-act]").forEach((button) => { button.onclick = () => act(button.dataset.gdAct, button); });
  }

  async function act(action, button) {
    if (action === "retry") { await refreshStatus(); render(); }
    else if (action === "sync") {
      // Round 30: one Google account — its page is Settings → Integrations → Google.
      if (window.MemuIntegrations) MemuIntegrations.open("int-google");
      else if (typeof showSettings === "function") showSettings(true);
    } else if (action === "docs") {
      if (window.MemuDocs) MemuDocs.open("sync", en() ? "add-files-from-google-drive" : "файлы-с-google-диска-в-чат");
    } else if (action === "authorize") {
      if (button) button.disabled = true;
      try {
        await api("/api/pg/gdrive/authorize", { method: "POST" });   // MemuAI opens Google's page in the default browser
      } catch (error) { note(error.message, "err", 6000); }
      await refreshStatus();
      render();
    } else if (action === "reopen") {
      const url = status && status.flow && status.flow.auth_url;
      if (url) window.open(url, "_blank", "noopener");
    } else if (action === "cancel-auth") {
      await api("/api/pg/gdrive/authorize/cancel", { method: "POST" }).catch(() => {});
      clearInterval(pollTimer); pollTimer = 0;
      await refreshStatus();
      render();
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      if (!state) { clearInterval(pollTimer); pollTimer = 0; return; }
      await refreshStatus();
      const flow = (status && status.flow) || {};
      if (status && (status.state === "ready" || flow.state !== "waiting")) {
        clearInterval(pollTimer); pollTimer = 0;
        if (status.state === "ready") note(L("Доступ к файлам Google Диска выдан", "Google Drive file access granted"));
        render();
      }
    }, 1500);
  }

  /* --------------------------------- browser -------------------------------- */
  function renderBrowser() {
    const body = $d("[data-gd-body]");
    const views = [["folder", "mine", L("Мой диск", "My Drive")], ["recent", "recent", L("Недавние", "Recent")],
      ["shared", "shared", L("Доступные мне", "Shared with me")], ["starred", "starred", L("Помеченные", "Starred")]];
    const kinds = [["all", L("Все", "All")], ["docs", L("Документы", "Docs")], ["sheets", L("Таблицы", "Sheets")],
      ["slides", L("Презентации", "Slides")], ["pdf", "PDF"], ["images", L("Изображения", "Images")]];
    body.innerHTML = `<div class="gd-browser">
      <nav class="gd-nav" aria-label="${esc(L("Разделы Диска", "Drive sections"))}">
        ${views.map(([view, ico, label]) => `<button type="button" class="gd-nav-item" data-gd-view="${view}">${navIcon(ico)}<span>${esc(label)}</span></button>`).join("")}
      </nav>
      <section class="gd-main">
        <div class="gd-tools">
          <label class="gd-search">${glyph("search")}<input type="search" data-gd-q autocomplete="off" spellcheck="false"
            placeholder="${esc(L("Поиск по всему Диску", "Search all of Drive"))}" aria-label="${esc(L("Поиск по Google Диску", "Search Google Drive"))}"></label>
          <div class="gd-kinds" role="group" aria-label="${esc(L("Тип файлов", "File type"))}">
            ${kinds.map(([kind, label]) => `<button type="button" class="gd-chip" data-gd-kind="${kind}">${esc(label)}</button>`).join("")}
          </div>
        </div>
        <div class="gd-crumbs" data-gd-crumbs></div>
        <div class="gd-list-head" aria-hidden="true"><span></span><span>${L("Название", "Name")}</span>
          <button type="button" class="gd-sort" data-gd-sort>${L("Изменён", "Modified")}</button><span>${L("Размер", "Size")}</span></div>
        <div class="gd-list" data-gd-list role="listbox" aria-multiselectable="true" tabindex="0" aria-label="${esc(L("Файлы", "Files"))}"></div>
      </section>
    </div>
    <footer class="gd-foot">
      <div class="gd-picked" data-gd-picked aria-live="polite"></div>
      <div class="gd-formats" data-gd-formats></div>
      <div class="gd-foot-actions">
        <button type="button" class="pbtn ghost" data-gd-cancel>${L("Отмена", "Cancel")}</button>
        <button type="button" class="pbtn" data-gd-attach disabled>${glyph("attach")}<span>${L("Прикрепить", "Attach")}</span></button>
      </div>
    </footer>`;
    bindBrowser();
    load();
  }

  function bindBrowser() {
    document.querySelectorAll("#panel [data-gd-view]").forEach((button) => button.onclick = () => {
      state.view = button.dataset.gdView; state.folder = "root"; state.path = []; state.query = "";
      const input = $d("[data-gd-q]"); if (input) input.value = "";
      load();
    });
    document.querySelectorAll("#panel [data-gd-kind]").forEach((button) => button.onclick = () => { state.kind = button.dataset.gdKind; load(); });
    const sort = $d("[data-gd-sort]");
    if (sort) sort.onclick = () => { state.order = state.order === "modified" ? "" : "modified"; load(); };
    const input = $d("[data-gd-q]");
    let timer = 0;
    input.oninput = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const value = input.value.trim();
        if (value) { if (state.view !== "search") state.back = { view: state.view, folder: state.folder, path: state.path }; state.view = "search"; state.query = value; }
        else if (state.view === "search") { Object.assign(state, state.back || { view: "folder", folder: "root", path: [] }); state.query = ""; }
        load();
      }, 280);
    };
    // Own Enter/Esc: panel() would press «Attach» on Enter in a single text field.
    input.onkeydown = (event) => {
      if (event.key === "Enter") { event.preventDefault(); clearTimeout(timer); input.oninput(); }
      else if (event.key === "Escape" && input.value) { event.preventDefault(); event.stopPropagation(); input.value = ""; input.oninput(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); const list = $d("[data-gd-list]"); list.focus(); move(1, event); }
    };
    $d("[data-gd-cancel]").onclick = () => closePanel("cancel");
    $d("[data-gd-attach]").onclick = attach;
    const list = $d("[data-gd-list]");
    list.addEventListener("click", (event) => {
      const more = event.target.closest("[data-gd-more]");
      if (more) { load(true); return; }
      const crumbish = event.target.closest("[data-gd-open]");
      const row = event.target.closest("[data-gd-row]");
      if (!row) return;
      const index = +row.dataset.gdRow;
      const file = state.files[index];
      if (!file) return;
      if (crumbish && file.kind === "folder") { openFolder(file); return; }
      if (file.kind === "folder" && !event.target.closest(".gd-check")) { openFolder(file); return; }
      if (event.shiftKey && state.anchor >= 0) selectRange(state.anchor, index);
      else toggle(file, index);
      state.cursor = index;
      if (!event.shiftKey) state.anchor = index;
      paintSelection();
    });
    list.addEventListener("dblclick", (event) => {
      const row = event.target.closest("[data-gd-row]");
      const file = row && state.files[+row.dataset.gdRow];
      if (file && file.kind !== "folder" && file.attachable) { state.selected.set(file.id, file); paintSelection(); attach(); }
    });
    list.addEventListener("scroll", () => {
      if (state.next && !state.loading && list.scrollTop + list.clientHeight > list.scrollHeight - 160) load(true);
    }, { passive: true });
    list.addEventListener("keydown", (event) => {
      const key = event.key;
      if (key === "ArrowDown" || key === "ArrowUp") { event.preventDefault(); move(key === "ArrowDown" ? 1 : -1, event); }
      else if (key === " " && state.cursor >= 0) { event.preventDefault(); const file = state.files[state.cursor]; if (file) { toggle(file, state.cursor); paintSelection(); } }
      else if (key === "Enter") {
        event.preventDefault();
        const file = state.files[state.cursor];
        if (file && file.kind === "folder") openFolder(file);
        else if (state.selected.size) attach();
        else if (file) { toggle(file, state.cursor); paintSelection(); }
      } else if (key === "Backspace" && state.view === "folder" && state.path.length) {
        event.preventDefault(); goTo(state.path.length - 2);
      } else if ((event.ctrlKey || event.metaKey) && (key.toLowerCase() === "a" || key.toLowerCase() === "ф")) {
        event.preventDefault();
        state.files.forEach((file) => { if (file.kind !== "folder" && file.attachable && !file.too_big) state.selected.set(file.id, file); });
        paintSelection();
      }
    });
    list.addEventListener("contextmenu", (event) => {
      const row = event.target.closest("[data-gd-row]");
      const file = row && state.files[+row.dataset.gdRow];
      if (!file || typeof showContextMenu !== "function") return;
      event.preventDefault(); event.stopPropagation();
      const items = file.kind === "folder"
        ? [{ label: L("Открыть папку", "Open the folder"), icon: "folder", run: () => openFolder(file) }]
        : [{ label: state.selected.has(file.id) ? L("Снять выбор", "Deselect") : L("Выбрать", "Select"), icon: "check", run: () => { toggle(file, +row.dataset.gdRow); paintSelection(); } },
          { label: L("Прикрепить только этот файл", "Attach only this file"), icon: "attach", run: () => { state.selected.clear(); state.selected.set(file.id, file); paintSelection(); attach(); } }];
      items.push({ label: L("Открыть в Google Диске", "Open in Google Drive"), icon: "external", run: () => {
        const url = file.kind === "folder" ? `https://drive.google.com/drive/folders/${encodeURIComponent(file.target || file.id)}` : `https://drive.google.com/open?id=${encodeURIComponent(file.target || file.id)}`;
        window.open(url, "_blank", "noopener");
      } });
      showContextMenu(items, event.clientX, event.clientY);
    });
  }

  function move(step, event) {
    if (!state.files.length) return;
    const before = state.cursor;
    state.cursor = Math.max(0, Math.min(state.files.length - 1, (state.cursor < 0 ? -1 : state.cursor) + step));
    if (event && event.shiftKey) {
      // Shift+arrows extend the selection from the anchor (or the row we started on) like a file manager.
      if (state.anchor < 0) state.anchor = before >= 0 ? before : state.cursor;
      selectRange(state.anchor, state.cursor);
    } else {
      state.anchor = state.cursor;
    }
    paintSelection();
    const row = document.querySelector(`#panel [data-gd-row="${state.cursor}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  function toggle(file, index) {
    if (file.kind === "folder") return;
    if (!file.attachable || file.too_big) {
      note(file.too_big ? L(`«${file.name}» больше 5 МБ — так много к сообщению не прикрепить`, `“${file.name}” is larger than 5 MB — too big to attach`)
        : L(`«${file.name}» нельзя скачать`, `“${file.name}” cannot be downloaded`), "warn");
      return;
    }
    if (state.selected.has(file.id)) state.selected.delete(file.id);
    else {
      const room = 10 - (typeof attachments !== "undefined" ? attachments.length : 0);
      if (state.selected.size >= room) { note(L(`Не более ${Math.max(0, room)} файлов — в сообщении до 10 вложений`, `Up to ${Math.max(0, room)} files — a message holds up to 10 attachments`), "warn"); return; }
      state.selected.set(file.id, file);
    }
    state.cursor = index;
  }

  function selectRange(from, to) {
    const [a, b] = from < to ? [from, to] : [to, from];
    for (let i = a; i <= b; i += 1) {
      const file = state.files[i];
      if (file && file.kind !== "folder" && file.attachable && !file.too_big) state.selected.set(file.id, file);
    }
  }

  function openFolder(file) {
    state.view = "folder";
    state.folder = file.target || file.id;
    state.path = [...state.path, { id: state.folder, name: file.name }];
    state.query = "";
    const input = $d("[data-gd-q]"); if (input) input.value = "";
    load();
  }
  function goTo(index) {
    state.view = "folder";
    state.path = index < 0 ? [] : state.path.slice(0, index + 1);
    state.folder = index < 0 ? "root" : state.path[index].id;
    load();
  }

  async function load(more) {
    if (!state) return;
    if (more && (!state.next || state.loading)) return;
    const seq = (state.seq += 1);
    state.loading = true;
    if (!more) { state.files = []; state.next = ""; state.cursor = -1; state.anchor = -1; }
    paintChrome();
    paintList(more ? "more" : "loading");
    const params = new URLSearchParams({ view: state.view, folder: state.folder, q: state.query, kind: state.kind, order: state.order });
    if (more) params.set("page", state.next);
    try {
      const data = await api("/api/pg/gdrive/list?" + params.toString());
      if (!state || seq !== state.seq) return;
      state.files = more ? state.files.concat(data.files || []) : (data.files || []);
      state.next = data.next || "";
      state.error = "";
    } catch (error) {
      if (!state || seq !== state.seq) return;
      state.error = error.message;
      if (error.code === "need_consent" || error.code === "auth") { await refreshStatus(); render(); return; }
    } finally {
      if (state && seq === state.seq) state.loading = false;
    }
    paintList();
    paintSelection();
  }

  function paintChrome() {
    document.querySelectorAll("#panel [data-gd-view]").forEach((button) => {
      const on = button.dataset.gdView === state.view;
      button.classList.toggle("active", on);
      button.setAttribute("aria-current", on ? "page" : "false");
    });
    document.querySelectorAll("#panel [data-gd-kind]").forEach((button) => {
      const on = button.dataset.gdKind === state.kind;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", String(on));
    });
    const sort = $d("[data-gd-sort]");
    if (sort) { sort.classList.toggle("active", state.order === "modified"); sort.hidden = state.view === "recent" || state.view === "search"; }
    const crumbs = $d("[data-gd-crumbs]");
    if (!crumbs) return;
    if (state.view === "search") {
      crumbs.innerHTML = `<span class="gd-crumb current">${glyph("search")}${esc(L("Результаты поиска: ", "Search results: ") + state.query)}</span>`;
      return;
    }
    const root = { folder: L("Мой диск", "My Drive"), recent: L("Недавние", "Recent"), shared: L("Доступные мне", "Shared with me"), starred: L("Помеченные", "Starred") }[state.view];
    const parts = [`<button type="button" class="gd-crumb ${state.path.length ? "" : "current"}" data-gd-crumb="-1">${esc(root)}</button>`]
      .concat(state.path.map((item, index) => `<i aria-hidden="true">›</i><button type="button" class="gd-crumb ${index === state.path.length - 1 ? "current" : ""}" data-gd-crumb="${index}">${esc(item.name)}</button>`));
    crumbs.innerHTML = parts.join("");
    crumbs.querySelectorAll("[data-gd-crumb]").forEach((button) => button.onclick = () => {
      const index = +button.dataset.gdCrumb;
      if (state.view !== "folder") { state.path = []; state.folder = "root"; load(); return; }
      if (index !== state.path.length - 1 || index === -1) goTo(index);
    });
  }

  function rowHtml(file, index) {
    const picked = state.selected.has(file.id);
    const disabled = file.kind !== "folder" && (!file.attachable || file.too_big);
    const thumb = file.thumb && file.kind === "image"
      ? `<span class="gd-thumb">${fileIcon("image")}<img src="/api/pg/gdrive/thumb?id=${encodeURIComponent(file.target || file.id)}" alt="" loading="lazy" onerror="this.remove()"></span>`
      : fileIcon(file.kind === "shortcut" ? "file" : file.kind);
    const sub = [file.owner && !file.mine ? file.owner : "", file.google ? L("файл Google", "Google file") : ""].filter(Boolean).join(" · ");
    return `<div class="gd-row ${picked ? "picked" : ""} ${disabled ? "disabled" : ""} ${file.kind === "folder" ? "folder" : ""}" role="option"
        aria-selected="${picked}" data-gd-row="${index}" title="${esc(file.name)}">
      <span class="gd-check" aria-hidden="true">${file.kind === "folder" ? "" : glyph("check")}</span>
      <span class="gd-name">${thumb}
        <span class="gd-name-text"><b>${esc(file.name)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</span>
        ${file.shortcut ? `<span class="gd-tag">${L("ярлык", "shortcut")}</span>` : ""}
        ${file.too_big ? `<span class="gd-tag warn">${L("больше 5 МБ", "over 5 MB")}</span>` : ""}</span>
      <span class="gd-date">${esc(when(state.view === "recent" ? file.viewed || file.modified : file.modified))}</span>
      <span class="gd-size">${file.kind === "folder" ? "" : file.google ? "—" : esc(size(file.size))}</span>
    </div>`;
  }

  function paintList(mode) {
    const list = $d("[data-gd-list]");
    if (!list || !state) return;
    if (mode === "loading") {
      list.innerHTML = Array.from({ length: 7 }, () => `<div class="gd-row skeleton"><span></span><span><i></i></span><span><i></i></span><span><i></i></span></div>`).join("");
      return;
    }
    if (mode === "more") { const tail = list.querySelector("[data-gd-more]"); if (tail) tail.innerHTML = `<span class="gd-spin"></span>`; return; }
    if (state.error) {
      list.innerHTML = `<div class="gd-list-empty error">${glyph("info")}<b>${esc(state.error)}</b><button type="button" class="pbtn ghost" data-gd-reload>${L("Повторить", "Retry")}</button></div>`;
      list.querySelector("[data-gd-reload]").onclick = () => load();
      return;
    }
    if (!state.files.length) {
      const text = state.view === "search" ? L("Ничего не нашлось. Попробуйте другое слово.", "Nothing found. Try another word.")
        : state.view === "starred" ? L("Нет помеченных файлов", "No starred files")
          : state.view === "shared" ? L("С вами пока ничем не поделились", "Nothing is shared with you yet")
            : state.kind !== "all" ? L("Нет файлов этого типа", "No files of this type") : L("Здесь пусто", "This folder is empty");
      list.innerHTML = `<div class="gd-list-empty">${fileIcon("folder")}<b>${esc(text)}</b></div>`;
      return;
    }
    list.innerHTML = state.files.map(rowHtml).join("")
      + (state.next ? `<div class="gd-more" data-gd-more><button type="button" class="pbtn ghost">${L("Показать ещё", "Show more")}</button></div>` : "");
  }

  function paintSelection() {
    if (!state) return;
    document.querySelectorAll("#panel [data-gd-row]").forEach((row) => {
      const file = state.files[+row.dataset.gdRow];
      const on = !!file && state.selected.has(file.id);
      row.classList.toggle("picked", on);
      row.classList.toggle("cursor", +row.dataset.gdRow === state.cursor);
      row.setAttribute("aria-selected", String(on));
    });
    const picked = [...state.selected.values()];
    const total = picked.reduce((sum, file) => sum + (file.google ? 0 : Number(file.size) || 0), 0);
    const counter = $d("[data-gd-picked]");
    if (counter) counter.innerHTML = picked.length
      ? `<b>${L(`Выбрано: ${picked.length}`, `Selected: ${picked.length}`)}</b>${total ? ` · ${esc(size(total))}` : ""}
         <button type="button" class="gd-clear" data-gd-clear>${L("Снять выбор", "Clear")}</button>`
      : `<span>${L("Выберите файлы — можно несколько (Shift, Ctrl+A)", "Pick files — several at once (Shift, Ctrl+A)")}</span>`;
    const clear = $d("[data-gd-clear]");
    if (clear) clear.onclick = () => { state.selected.clear(); paintSelection(); };
    const button = $d("[data-gd-attach]");
    if (button) {
      button.disabled = !picked.length || state.busy;
      button.querySelector("span:not(.ui-icon)").textContent = picked.length ? L(`Прикрепить (${picked.length})`, `Attach (${picked.length})`) : L("Прикрепить", "Attach");
    }
    paintFormats(picked);
  }

  function paintFormats(picked) {
    const box = $d("[data-gd-formats]");
    if (!box) return;
    const kinds = [...new Set(picked.map((file) => GOOGLE_KIND[file.mime]).filter(Boolean))];
    if (!kinds.length) { box.innerHTML = ""; return; }
    const options = { doc: ["docx", "pdf", "md", "txt"], sheet: ["md", "csv", "pdf", "xlsx"], slides: ["pdf", "txt", "pptx"], drawing: ["png", "pdf"] };
    const labels = FORMAT_LABEL();
    const names = KIND_LABEL();
    const current = formats();
    box.innerHTML = kinds.map((kind) => `<label class="gd-format">${fileIcon(kind)}<span>${esc(names[kind])}:</span>
      <select data-gd-format="${kind}" aria-label="${esc(L("Формат: ", "Format: ") + names[kind])}">
        ${options[kind].map((value) => `<option value="${value}" ${current[kind] === value ? "selected" : ""}>${esc(labels[value])}</option>`).join("")}
      </select></label>`).join("");
    box.querySelectorAll("[data-gd-format]").forEach((select) => select.onchange = () => setFormat(select.dataset.gdFormat, select.value));
    if (typeof enhanceSelects === "function") try { enhanceSelects(box); } catch (_) {}
  }

  /* --------------------------------- attaching ------------------------------- */
  async function attach() {
    if (!state || state.busy || !state.selected.size) return;
    const picked = [...state.selected.values()];
    state.busy = true;
    paintSelection();
    const button = $d("[data-gd-attach]");
    const files = [];
    const problems = [];
    let done = 0;
    const progress = () => { if (button) button.querySelector("span:not(.ui-icon)").textContent = L(`Скачиваю ${done}/${picked.length}…`, `Downloading ${done}/${picked.length}…`); };
    progress();
    const queue = picked.slice();
    const worker = async () => {
      while (queue.length && state) {
        const file = queue.shift();
        const row = [...document.querySelectorAll("#panel [data-gd-row]")].find((node) => state.files[+node.dataset.gdRow]?.id === file.id);
        if (row) row.classList.add("downloading");
        try {
          const response = await api("/api/pg/gdrive/download", { method: "POST", raw: true, headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id: file.target || file.id, format: formatFor(file) }) });
          if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || `HTTP ${response.status}`); }
          const blob = await response.blob();
          const name = decodeURIComponent(response.headers.get("X-Memu-Name") || file.name);
          files.push({ order: picked.indexOf(file), file: new File([blob], name, { type: (response.headers.get("Content-Type") || blob.type || "").split(";")[0] }) });
          if (row) { row.classList.remove("downloading"); row.classList.add("done"); }
        } catch (error) {
          problems.push(`${file.name}: ${error.message}`);
          if (row) { row.classList.remove("downloading"); row.classList.add("failed"); }
        }
        done += 1;
        progress();
      }
    };
    await Promise.all([worker(), worker(), worker()]);
    if (!state) return;
    state.busy = false;
    files.sort((a, b) => a.order - b.order);
    let added = 0;
    if (files.length && typeof addAttachmentFiles === "function") added = await addAttachmentFiles(files.map((row) => row.file));
    if (problems.length) note(problems.slice(0, 3).join("\n") + (problems.length > 3 ? `\n…+${problems.length - 3}` : ""), "warn", 8000);
    if (added) {
      note(L(`Прикреплено с Google Диска: ${added}`, `Attached from Google Drive: ${added}`));
      closePanel("choice");
      const input = document.getElementById("input");
      if (input) input.focus();
    } else {
      paintSelection();
    }
  }

  // Warm the status so the «+» menu shows the right hint on first open.
  setTimeout(() => { refreshStatus(); }, 4000);

  return Object.freeze({ open, menuHint, refreshStatus, icon: DRIVE });
})();
