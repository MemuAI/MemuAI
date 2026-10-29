

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || !Q.registerPanel) return;
  const root = document.documentElement;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const svg = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  const IC = {
    search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>'),
    back: svg('<path d="M15 6l-6 6 6 6"/>'),
    folder: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
    image: svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m21 16-5-5-9 9"/>'),
    video: svg('<rect x="3" y="5" width="13" height="14" rx="2.5"/><path d="m16 10 5-3v10l-5-3"/>'),
    audio: svg('<path d="M9 18V6l11-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>'),
    doc: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'),
    text: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 12h6M9 15h6M9 18h3"/>'),
    archive: svg('<rect x="3" y="4" width="18" height="5" rx="1.5"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4"/>'),
    exe: svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01"/>'),
    code: svg('<path d="M8 6 3 12l5 6M16 6l5 6-5 6"/>'),
    file: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'),
    open: svg('<path d="M14 4h6v6M20 4l-9 9M10 6H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>'),
    reveal: svg('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="m10 13 2 2 4-4"/>'),
    copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>'),
    memu: svg('<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M8 13h8M8 16h5"/>'),
    x: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    bolt: svg('<path d="M13 3 5 14h6l-1 7 8-11h-6z"/>'),
    more: svg('<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>'),
    trash: svg('<path d="M3.5 6.5h17M8.5 6.5V4.5h7v2M6 6.5l1 13.5h10l1-13.5"/>'),
  };

  const KINDS = () => [["", t("Всё", "All")], ["folder", t("Папки", "Folders")], ["doc", t("Документы", "Documents")], ["image", t("Картинки", "Images")],
    ["video", t("Видео", "Video")], ["audio", t("Аудио", "Audio")], ["archive", t("Архивы", "Archives")], ["exe", t("Программы", "Programs")], ["code", t("Код", "Code")]];
  const SORTS = () => [["relevance", t("Лучшие", "Best")], ["date", t("Новые", "Newest")], ["size", t("Большие", "Largest")], ["name", t("По имени", "By name")]];
  const TIPS = () => (Q.lang() === "en" ? ["*.pdf", "ext:docx dm:7d", "size:>1gb", "report | invoice", "folder: project", "in:Downloads *.zip", "dm:today"]
    : ["*.pdf", "ext:docx dm:7d", "size:>1gb", "отчёт | report", "folder: проект", "in:Загрузки *.zip", "dm:today"]);
  const PAGE = 60;
  const S = {
    view: false, query: "", kind: "", sort: "relevance", items: [], index: 0, total: 0, capped: false, ms: 0,
    backend: "", building: false, error: "", code: "", loading: false, memufiles: false, dragKey: "", limit: PAGE,
    settings: null, status: null, seq: 0, inflight: false, pending: false, ai: null, aiIndex: -1,
    windows: null, undo: null, renaming: null, keepScroll: false, warmed: false,
  };
  let panelEl = null, aiEl = null;
  Q.registerPanel("fsearch", (el) => { panelEl = el; el.classList.add("fs"); return {}; }, { slot: "top", hidden: true, order: -6 });
  Q.registerPanel("fsearch-ai", (el) => { aiEl = el; el.classList.add("fs-ai"); return {}; }, { slot: "bottom", hidden: true, order: 5 });

  async function loadSettings() {
    try { const st = await Q.api("/api/fsindex/status"); S.status = st; S.settings = st.settings || {}; S.memufiles = !!st.memufiles; S.windows = !!st.windows; }
    catch (e) { S.settings = S.settings || {}; }
  }
  const on = (key) => !S.settings || S.settings[key] !== false;
  const recent = {
    get() { try { return JSON.parse(localStorage.getItem("memu.fsearch.recent") || "[]").slice(0, 6); } catch (e) { return []; } },
    add(q) { q = String(q || "").trim(); if (!q) return; const rows = [q, ...this.get().filter((x) => x !== q)].slice(0, 6); try { localStorage.setItem("memu.fsearch.recent", JSON.stringify(rows)); } catch (e) {  } },
  };

  function size(n) {
    if (n == null || n < 0) return "";
    const u = Q.lang() === "en" ? ["B", "KB", "MB", "GB", "TB"] : ["Б", "КБ", "МБ", "ГБ", "ТБ"];
    let v = n, i = 0;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    const loc = Q.lang() === "en" ? "en-US" : "ru-RU";
    return (i ? v.toLocaleString(loc, { maximumFractionDigits: v < 10 ? 1 : 0 }) : String(v)) + " " + u[i];
  }
  function when(sec) {
    if (!sec) return "";
    const d = new Date(sec * 1000), now = new Date();
    const loc = Q.lang() === "en" ? "en-GB" : "ru-RU";
    if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (d.toDateString() === y.toDateString()) return t("вчера", "yesterday");
    return d.toLocaleDateString(loc, d.getFullYear() === now.getFullYear() ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
  }
  function words(q) {
    return String(q || "").split(/\s+/).filter((w) => w && !/^[!|]/.test(w) && !/^[a-zа-я]+:/i.test(w) && w !== "|")
      .map((w) => w.replace(/["*?]/g, "").replace(/^.*[\\/]/, "")).filter((w) => /[\p{L}\p{N}]/u.test(w));
  }
  function highlight(name, q) {
    const low = name.toLowerCase().replace(/ё/g, "е");
    const marks = [];
    for (const w of words(q)) {
      const i = low.indexOf(w.toLowerCase().replace(/ё/g, "е"));
      if (i >= 0) marks.push([i, i + w.length]);
    }
    if (!marks.length) return esc(name);
    marks.sort((a, b) => a[0] - b[0]);
    let out = "", pos = 0;
    for (const [a, b] of marks) { if (a < pos) continue; out += esc(name.slice(pos, a)) + "<mark>" + esc(name.slice(a, b)) + "</mark>"; pos = b; }
    return out + esc(name.slice(pos));
  }

  function shortDir(dir, limit) {
    const max = limit || Math.max(24, Math.floor(((window.innerWidth || 720) - 190) / 6.6));
    if (dir.length <= max) return dir;
    const sep = dir.includes("\\") ? "\\" : "/";
    const parts = dir.split(sep);
    let head = parts.slice(0, parts[0] === "" ? 2 : 1).join(sep);
    let tail = "";
    for (let i = parts.length - 1; i > 0; i--) {
      const next = parts[i] + (tail ? sep + tail : "");
      if (head.length + next.length + 3 > max) break;
      tail = next;
    }
    if (!tail) return "…" + dir.slice(-(max - 1));
    return head + sep + "…" + sep + tail;
  }
  const kindIcon = (r) => IC[r.kind] || (r.is_dir ? IC.folder : IC.file);
  function backendLabel(b) {
    if (b === "everything") return "Everything";
    if (b === "own") return t("Индекс MemuAI", "MemuAI index");
    return "";
  }

  const query = () => S.query.trim();
  let debounceTimer = 0, buildTimer = 0;
  function schedule(delay = 45) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(run, delay);
  }
  async function run() {
    clearTimeout(debounceTimer);
    const q = query();
    if (!q) { S.seq++; S.items = []; S.total = 0; S.error = ""; S.code = ""; S.loading = false; renderBody(); return; }
    if (S.inflight) { S.pending = true; return; }
    S.inflight = true; S.loading = true;
    const seq = ++S.seq;
    const params = new URLSearchParams({ q, limit: String(S.limit), sort: S.sort, lang: Q.lang() });
    if (S.kind) params.set("kinds", S.kind);
    const key = params.toString();
    let fresh = false;
    try {
      const r = await Q.api("/api/fsindex/search?" + key);
      if (seq === S.seq && q === query()) {
        fresh = true;
        S.items = r.results || []; S.total = r.total || 0; S.capped = !!r.capped; S.ms = r.ms || 0;
        S.backend = r.backend || ""; S.building = !!r.building; S.error = r.error || ""; S.code = r.code || "";
        S.memufiles = !!r.memufiles; S.windows = !!r.windows; S.dragKey = r.drag_key || S.dragKey;
        if (S.index >= S.items.length) S.index = 0;
      }
    } catch (e) {
      if (seq === S.seq) { fresh = true; S.error = e.message; S.code = "net"; S.items = []; }
    }
    S.inflight = false; S.loading = false;
    if (S.pending) { S.pending = false; run(); return; }
    if (fresh || !S.items.length) renderBody();
    clearTimeout(buildTimer);
    if (S.building && S.view) buildTimer = setTimeout(() => { if (S.view && S.building && !S.renaming) run(); }, 1500);
  }

  function chipsHtml() {
    return `<div class="fs-chips" role="toolbar">${KINDS().map(([k, label]) => `<button type="button" class="fs-chip ${S.kind === k ? "on" : ""}" data-fs-kind="${k}" aria-pressed="${S.kind === k}">${esc(label)}</button>`).join("")}
</div>`;
  }
  function rowHtml(r, i, active, compact) {
    const meta = [r.is_dir ? "" : size(r.size), when(r.mtime)].filter(Boolean).join(" · ");
    const btns = `<span class="fs-btns">
      <button type="button" class="qa-icon fs-btn" data-fs-act="open" data-i="${i}" title="${esc(t("Открыть (Enter)", "Open (Enter)"))}" aria-label="${esc(t("Открыть", "Open"))}">${IC.open}</button>
      <button type="button" class="qa-icon fs-btn" data-fs-act="reveal" data-i="${i}" title="${esc(t("Показать в папке (Ctrl+Enter)", "Show in folder (Ctrl+Enter)"))}" aria-label="${esc(t("Показать в папке", "Show in folder"))}">${IC.reveal}</button>
      <button type="button" class="qa-icon fs-btn" data-fs-act="copy" data-i="${i}" title="${esc(t("Копировать путь (Ctrl+Shift+C)", "Copy the path (Ctrl+Shift+C)"))}" aria-label="${esc(t("Копировать путь", "Copy the path"))}">${IC.copy}</button>
      <button type="button" class="qa-icon fs-btn" data-fs-act="menu" data-i="${i}" title="${esc(t("Ещё действия (правая кнопка, Shift+F10)", "More actions (right click, Shift+F10)"))}" aria-label="${esc(t("Ещё действия", "More actions"))}" aria-haspopup="menu">${IC.more}</button></span>`;
    return `<div class="qa-row fs-row ${active ? "active" : ""} k-${esc(r.kind || "file")}" role="option" aria-selected="${active}" data-i="${i}" draggable="${r.is_dir ? "false" : "true"}" title="${esc(r.path)}">
      <span class="ic fs-ic">${kindIcon(r)}</span>
      <span class="fs-main"><span class="tx">${highlight(r.name, compact ? (S.ai && S.ai.query) : S.query)}</span><small>${esc(shortDir(r.dir || r.path))}</small></span>
      <span class="fs-meta">${esc(meta)}</span>${btns}</div>`;
  }
  function statusHtml() {
    if (!query()) return "";
    const parts = [];
    const label = backendLabel(S.backend);
    if (S.items.length) parts.push(S.capped ? t(`${S.items.length}+ найдено`, `${S.items.length}+ found`) : t(`Найдено: ${S.total}`, `Found: ${S.total}`));
    if (label) parts.push(label);
    if (S.ms) parts.push(`${(S.ms < 10 ? Math.round(S.ms * 10) / 10 : Math.round(S.ms)).toLocaleString(Q.lang() === "en" ? "en-US" : "ru-RU")} ${t("мс", "ms")}`);
    const building = S.building ? `<span class="fs-building">${IC.bolt}${esc(t("Индекс ещё строится — результаты дополняются", "The index is still being built — results keep coming"))}</span>` : "";
    const sort = `<label class="fs-sort" title="${esc(t("Порядок", "Order"))}"><select id="fs-sort" aria-label="${esc(t("Порядок", "Order"))}">${SORTS().map(([v, l]) => `<option value="${v}" ${S.sort === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    return `<div class="fs-status"><span class="fs-status-text">${esc(parts.join(" · "))}</span>${building}<span class="fs-grow"></span>${sort}</div>`;
  }
  function emptyHtml() {
    if (!query()) {
      const rec = recent.get();
      return `<div class="fs-empty"><div class="fs-empty-ic">${IC.search}</div><b>${esc(t("Поиск по всему компьютеру", "Search the whole computer"))}</b>
        <span>${esc(t("Начните вводить имя файла или папки. Можно маски и фильтры:", "Start typing a file or folder name. Wildcards and filters work too:"))}</span>
        <div class="fs-tips">${TIPS().map((x) => `<button type="button" class="fs-tip" data-fs-tip="${esc(x)}"><code>${esc(x)}</code></button>`).join("")}</div>
        ${rec.length ? `<div class="fs-recent"><small>${esc(t("Недавние", "Recent"))}</small>${rec.map((x) => `<button type="button" class="fs-tip" data-fs-tip="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}</div>`;
    }
    if (S.code === "indexing") return `<div class="fs-empty"><div class="fs-empty-ic spin">${IC.bolt}</div><b>${esc(t("Строю индекс файлов…", "Building the file index…"))}</b><span>${esc(t("Первый раз это занимает до пары минут, потом поиск мгновенный. С установленным Everything — сразу.", "The first time takes up to a couple of minutes, then search is instant. With Everything installed — right away."))}</span></div>`;
    if (S.code === "disabled") return `<div class="fs-empty"><b>${esc(t("Поиск файлов выключен", "File search is off"))}</b><span>${esc(t("Включите его в Настройки → Быстрый доступ → Поиск файлов на ПК.", "Turn it on in Settings → Quick Access → PC file search."))}</span></div>`;
    if (S.code === "query") return `<div class="fs-empty"><b>${esc(t("Не понял запрос", "Could not read the query"))}</b><span>${esc(S.error)}</span></div>`;
    if (S.error && S.code !== "empty") return `<div class="fs-empty"><b>${esc(t("Поиск не удался", "Search failed"))}</b><span>${esc(S.error)}</span></div>`;
    if (S.loading) return `<div class="fs-empty fs-wait"><span>${esc(t("Ищу…", "Searching…"))}</span></div>`;
    return `<div class="fs-empty"><b>${esc(t("Ничего не найдено", "Nothing found"))}</b><span>${esc(t("Попробуйте часть имени, другую раскладку или маску вроде *отчёт*.", "Try part of the name or a wildcard like *report*."))}</span></div>`;
  }
  function listHtml() {
    if (!S.items.length) return emptyHtml();
    return `<div class="qx-list fs-list" role="listbox" aria-label="${esc(t("Файлы", "Files"))}">${S.items.map((r, i) => rowHtml(r, i, i === S.index, false)).join("")}
        ${S.capped && S.limit < 500 ? `<button type="button" class="qx-link fs-more" data-fs-act="more">${esc(t("Показать больше", "Show more"))}</button>` : ""}</div>`;
  }
  function footHtml() {
    if (S.undo) {
      return `<div class="fs-undo" role="status"><span>${IC.trash}${esc(S.undo.text)}</span><button type="button" class="qx-link" data-fs-act="undo">${esc(t("Отменить", "Undo"))}</button>
        <button type="button" class="qa-icon fs-btn" data-fs-act="undo-x" title="${esc(t("Скрыть", "Hide"))}" aria-label="${esc(t("Скрыть", "Hide"))}">${IC.x}</button></div>`;
    }
    if (!S.items.length) return "";
    return `<div class="qx-foot fs-foot"><div class="qx-keys"><span><kbd>↵</kbd> ${esc(t("открыть", "open"))}</span><span><kbd>Ctrl</kbd><kbd>↵</kbd> ${esc(t("в папке", "in folder"))}</span><span class="fs-wide"><kbd>F2</kbd> ${esc(t("имя", "rename"))}</span><span class="fs-wide"><kbd>Shift</kbd><kbd>F10</kbd> ${esc(t("меню", "menu"))}</span>${S.memufiles ? `<span class="fs-wide"><kbd>Alt</kbd><kbd>↵</kbd> MemuFiles</span>` : ""}</div></div>`;
  }
  function viewHtml() {
    const ph = t("Имя файла, *.txt, ext:pdf size:>5mb, dm:today…", "File name, *.txt, ext:pdf size:>5mb, dm:today…");
    return `<div class="qa-view-head qx-head fs-head"><button type="button" class="qa-icon qa-back" data-fs-act="back" title="${esc(t("Назад (Esc)", "Back (Esc)"))}" aria-label="${esc(t("Назад", "Back"))}">${IC.back}</button>
      <label class="qa-search fs-search">${IC.search}<input id="fs-q" type="text" spellcheck="false" autocomplete="off" placeholder="${esc(ph)}" value="${esc(S.query)}" aria-label="${esc(t("Поиск файлов на компьютере", "Search files on the computer"))}"></label></div>
      <div class="fs-chips-host">${chipsHtml()}</div><div class="fs-status-host">${statusHtml()}</div><div class="fs-body">${listHtml()}</div><div class="fs-foot-host">${footHtml()}</div>`;
  }
  const part = (cls) => panelEl && panelEl.querySelector(cls);
  function render() {
    if (!panelEl) return;
    if (!S.view) { delete root.dataset.fsView; Q.hidePanel("fsearch"); panelEl.innerHTML = ""; Q.resize(); return; }
    root.dataset.fsView = "1";
    panelEl.innerHTML = viewHtml();
    Q.showPanel("fsearch");
    afterList(true);
    Q.resize();
  }

  function renderBody() {
    if (!S.view || !panelEl) return;
    if (!part("#fs-q")) { render(); return; }
    closeMenu();
    S.renaming = null;
    const body = part(".fs-body");
    const old = body.querySelector(".fs-list");
    const keepTop = old && S.keepScroll ? old.scrollTop : 0;
    part(".fs-status-host").innerHTML = statusHtml();
    body.innerHTML = listHtml();
    part(".fs-foot-host").innerHTML = footHtml();
    const list = body.querySelector(".fs-list");
    if (list) list.scrollTop = keepTop;
    S.keepScroll = false;
    afterList(true);
    Q.resize();
  }
  function renderChips() { const host = part(".fs-chips-host"); if (host) host.innerHTML = chipsHtml(); }
  function renderFoot() { const host = part(".fs-foot-host"); if (host) { host.innerHTML = footHtml(); Q.resize(); } }
  function afterList(scroll) {
    const row = panelEl && panelEl.querySelector(".fs-list .fs-row.active");
    if (scroll && row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
  }

  function setActive(i, host = panelEl, isAi = false) {
    if (isAi) S.aiIndex = i; else S.index = i;
    if (!host) return;
    host.querySelectorAll(".fs-row.active").forEach((row) => { row.classList.remove("active"); row.setAttribute("aria-selected", "false"); });
    const row = host.querySelector(`.fs-row[data-i="${i}"]`);
    if (row) { row.classList.add("active"); row.setAttribute("aria-selected", "true"); if (row.scrollIntoView) row.scrollIntoView({ block: "nearest" }); }
  }
  function focusInput() {
    const q = document.getElementById("fs-q");
    if (q && document.activeElement !== q) { q.focus({ preventScroll: true }); try { q.setSelectionRange(q.value.length, q.value.length); } catch (e) {  } }
  }
  function openView(text) {
    S.view = true; S.query = text != null ? String(text) : S.query; S.index = 0; S.limit = PAGE;
    render(); focusInput(); run();
    requestAnimationFrame(focusInput);
    if (!S.settings) loadSettings();

    if (!S.warmed) { S.warmed = true; Q.api("/api/fsindex/warm", { body: {} }).catch(() => { S.warmed = false; }); }
  }
  function closeView() {
    if (!S.view) return;
    if (query()) recent.add(query());
    closeMenu(); closeProps();
    clearTimeout(debounceTimer); clearTimeout(buildTimer);
    S.view = false; S.renaming = null; S.undo = null; render();
    Q.setInput(Q.getInput());
  }

  async function writeClipboard(text) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (e) {
      try { const r = await Q.api("/api/quick/window", { body: { action: "copy", text } }); ok = !!r.ok; } catch (err) { ok = false; }
      if (!ok) {
        const area = document.createElement("textarea"); area.value = text; document.body.appendChild(area); area.select();
        try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
        area.remove();
      }
    }
    return ok;
  }
  async function copyText(text, what) {
    const ok = await writeClipboard(text);
    Q.toast(ok ? (what || t("Путь скопирован", "Path copied")) : t("Не удалось скопировать", "Could not copy"), ok ? "ok" : "err");
    return ok;
  }
  const op = (action, r, extra = {}) => Q.api("/api/fsindex/op", { body: { action, path: r ? r.path : "", lang: Q.lang(), ...extra } });

  async function finish(reply) {
    let res = reply;
    for (let n = 0; res && res.op && !["done", "error", "cancelled"].includes(res.status) && n < 1200; n++) {
      await new Promise((ok) => setTimeout(ok, 600));
      try { res = await Q.api("/api/fsindex/op/" + encodeURIComponent(res.op)); } catch (e) { break; }
    }
    if (res && res.status === "error") throw new Error(((res.errors || [])[0] || {}).error || t("Не получилось", "It didn't work"));
    return res;
  }

  const VIEW = { rows: () => S.items, isAi: false, host: () => panelEl };
  const AI = { rows: () => (S.ai && S.ai.results) || [], isAi: true, host: () => aiEl };
  function rerender(src) { if (src.isAi) renderAi(); else { S.keepScroll = true; renderBody(); } }
  function replaceRow(src, r, row) {
    const rows = src.rows();
    const i = rows.indexOf(r);
    if (i >= 0) rows[i] = row;
    rerender(src);
  }
  function insertRows(src, r, rows) {
    const list = src.rows();
    const i = list.indexOf(r);
    const at = i >= 0 ? i + 1 : list.length;
    list.splice(at, 0, ...rows);
    if (!src.isAi) { S.total += rows.length; S.index = at; } else S.aiIndex = at;
    rerender(src);
  }
  function removeRow(src, r) {
    const list = src.rows();
    const i = list.indexOf(r);
    if (i < 0) return -1;
    list.splice(i, 1);
    if (!src.isAi) { S.total = Math.max(0, S.total - 1); S.index = Math.min(S.index, Math.max(0, list.length - 1)); }
    rerender(src);
    return i;
  }

  async function act(name, r, src = VIEW) {
    if (!r) return;
    if (query() && !src.isAi) recent.add(query());
    try {
      if (name === "copy") return copyText(r.path);
      if (name === "copy-name") return copyText(r.name, t("Имя скопировано", "Name copied"));
      if (name === "menu") return openMenu(r, src, null, true);
      if (name === "rename") return startRename(r, src);
      if (name === "props") return openProps(r, src);
      if (name === "ask") return askAi(r);
      if (name === "clip-copy" || name === "clip-cut") {
        try { await op(name === "clip-copy" ? "clip_copy" : "clip_cut", r); }
        catch (e) { return copyText(r.path, t("Файлы копируются в буфер только в Windows — скопирован путь", "Files go to the clipboard on Windows only — the path was copied")); }
        Q.toast(name === "clip-copy" ? t("Скопировано — вставьте в Проводнике (Ctrl+V)", "Copied — paste it in Explorer (Ctrl+V)")
          : t("Вырезано — вставьте в нужной папке (Ctrl+V)", "Cut — paste it in the folder you need (Ctrl+V)"), "ok", 2600);
        return;
      }
      if (name === "open-with") { await op("open_with", r); return; }
      if (name === "duplicate" || name === "zip") {
        Q.toast(name === "zip" ? t("Сжимаю…", "Compressing…") : t("Копирую…", "Copying…"), "", 30000);
        const res = await finish(await op(name === "zip" ? "compress" : "duplicate", r));
        const rows = (res && res.rows) || [];
        if (rows.length) insertRows(src, r, rows);
        Q.toast(name === "zip" ? t(`Готово: ${rows[0] ? rows[0].name : "ZIP"}`, `Done: ${rows[0] ? rows[0].name : "ZIP"}`)
          : t(`Создана копия: ${rows[0] ? rows[0].name : ""}`, `Copy created: ${rows[0] ? rows[0].name : ""}`), "ok");
        return;
      }
      if (name === "trash") return trashRow(r, src);
      await Q.api("/api/fsindex/open", { body: { path: r.path, reveal: name === "reveal", with: name === "memufiles" ? "memufiles" : undefined, lang: Q.lang() } });
      if (name === "open" || name === "memufiles") Q.hide();
    } catch (e) { Q.toast(e.message, "err", 3200); }
  }

  let undoTimer = 0;
  async function trashRow(r, src) {
    const res = await op("trash", r);
    const at = removeRow(src, r);
    clearTimeout(undoTimer);
    S.undo = { op: res.op, text: t(`«${r.name}» в корзине`, `“${r.name}” moved to the Recycle Bin`), row: r, at, src };
    if (src.isAi || !S.view) Q.toast(S.undo.text, "ok", 3000); else renderFoot();
    undoTimer = setTimeout(() => { S.undo = null; renderFoot(); }, 9000);
  }
  async function undoTrash() {
    const u = S.undo;
    if (!u) return;
    S.undo = null; clearTimeout(undoTimer); renderFoot();
    try {
      const res = await finish(await op("undo", null, { op: u.op }));
      const row = ((res && res.rows) || [])[0] || u.row;
      const list = u.src.rows();
      list.splice(Math.max(0, Math.min(u.at, list.length)), 0, row);
      if (!u.src.isAi) { S.total += 1; S.index = list.indexOf(row); }
      rerender(u.src);
      Q.toast(t("Восстановлено", "Restored"), "ok");
    } catch (e) { Q.toast(e.message, "err", 3200); }
  }

  async function askAi(r) {
    const mentions = window.MemuQuickMentions;
    let attached = false;
    if (!r.is_dir && mentions && typeof mentions.add === "function") {
      try { await op("attach", r); mentions.add({ provider: "pc", ref: r.path, title: r.name, subtitle: r.dir || "", icon: "pc" }); attached = true; } catch (e) { attached = false; }
    }
    closeView();
    if (attached) { Q.setInput(""); Q.toast(t("Файл прикреплён — напишите вопрос о нём", "File attached — type your question about it"), "ok", 2600); }
    else Q.setInput((r.is_dir ? t("Папка: ", "Folder: ") : t("Файл: ", "File: ")) + `"${r.path}"\n`);
  }

  function startRename(r, src) {
    const host = src.host();
    const i = src.rows().indexOf(r);
    const row = host && host.querySelector(`.fs-row[data-i="${i}"]`);
    if (!row) return;
    closeMenu();
    if (!src.isAi) setActive(i); else setActive(i, host, true);
    const tx = row.querySelector(".tx");
    S.renaming = r;
    row.draggable = false;
    row.classList.add("fs-renaming");
    tx.innerHTML = `<input class="fs-rename" type="text" spellcheck="false" autocomplete="off" value="${esc(r.name)}" aria-label="${esc(t("Новое имя", "New name"))}">`;
    const field = tx.querySelector("input");
    field.focus();
    const dot = r.is_dir ? -1 : r.name.lastIndexOf(".");
    field.setSelectionRange(0, dot > 0 ? dot : r.name.length);
    let done = false;
    const finishRename = async (save) => {
      if (done) return;
      done = true;
      const name = field.value.trim();
      S.renaming = null;
      if (!save || !name || name === r.name) { rerender(src); if (!src.isAi) focusInput(); return; }
      try {
        const res = await op("rename", r, { name });
        replaceRow(src, r, res.row);
        Q.toast(t("Переименовано", "Renamed"), "ok");
      } catch (e) {
        Q.toast(e.message, "err", 3600);
        rerender(src);
      }
      if (!src.isAi) focusInput();
    };
    field.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); finishRename(true); }
      else if (e.key === "Escape") { e.preventDefault(); finishRename(false); }
    });
    field.addEventListener("blur", () => finishRename(true));
    field.addEventListener("click", (e) => e.stopPropagation());
    field.addEventListener("dblclick", (e) => e.stopPropagation());
  }

  let propsEl = null;
  function closeProps() { if (propsEl) { propsEl.remove(); propsEl = null; Q.resize(); } }
  function fullDate(ms) {
    if (!ms) return "—";
    return new Date(ms).toLocaleString(Q.lang() === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  async function openProps(r, src) {
    closeProps();
    let info;
    try { info = (await op("properties", r)).info; } catch (e) { Q.toast(e.message, "err", 3200); return; }
    const host = (src.isAi ? aiEl : panelEl);
    if (!host) return;
    const fold = info.folder;
    const sizeText = info.is_dir
      ? (fold ? `${size(fold.size)}${fold.complete ? "" : "+"} · ${t(`файлов: ${fold.files}${fold.complete ? "" : "+"}, папок: ${fold.dirs}`, `${fold.files}${fold.complete ? "" : "+"} files, ${fold.dirs} folders`)}` : "—")
      : `${size(info.size)} (${Number(info.size).toLocaleString(Q.lang() === "en" ? "en-US" : "ru-RU")} ${t("байт", "bytes")})`;
    const attrs = [info.hidden ? t("скрытый", "hidden") : "", info.readonly ? t("только чтение", "read-only") : "", info.system ? t("системный", "system") : "",
      info.is_link ? t("ссылка", "link") : ""].filter(Boolean).join(", ");
    const rowsHtml = [
      [t("Тип", "Type"), info.is_dir ? t("Папка", "Folder") : (info.ext ? info.ext.slice(1).toUpperCase() + " " + t("файл", "file") : t("Файл", "File"))],
      [t("Папка", "Location"), info.parent],
      [t("Размер", "Size"), sizeText],
      info.image ? [t("Изображение", "Image"), `${info.image.width} × ${info.image.height} · ${info.image.format || ""}`] : null,
      [t("Изменён", "Modified"), fullDate(info.modified)],
      info.created ? [t("Создан", "Created"), fullDate(info.created)] : null,
      [t("Открыт", "Accessed"), fullDate(info.accessed)],
      attrs ? [t("Атрибуты", "Attributes"), attrs] : null,
      info.target ? [t("Указывает на", "Target"), info.target] : null,
    ].filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
    propsEl = document.createElement("div");
    propsEl.className = "fs-props";
    propsEl.setAttribute("role", "dialog");
    propsEl.setAttribute("aria-label", t("Свойства", "Properties"));
    propsEl.innerHTML = `<div class="fs-props-head"><span class="ic fs-ic k-${esc(r.kind || "file")}">${kindIcon(r)}</span><b title="${esc(info.path)}">${esc(info.name)}</b>
        <button type="button" class="qa-icon fs-btn" data-fs-props="close" title="${esc(t("Закрыть (Esc)", "Close (Esc)"))}" aria-label="${esc(t("Закрыть", "Close"))}">${IC.x}</button></div>
      <dl>${rowsHtml}</dl>
      <div class="fs-props-foot"><button type="button" class="qx-link" data-fs-props="path">${esc(t("Копировать путь", "Copy the path"))}</button>
        ${info.windows ? `<button type="button" class="qx-link" data-fs-props="win">${esc(t("Свойства Windows…", "Windows properties…"))}</button>` : ""}</div>`;
    host.appendChild(propsEl);
    propsEl.tabIndex = -1;
    propsEl.focus();
    propsEl.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeProps(); if (!src.isAi) focusInput(); } });
    propsEl.addEventListener("click", (e) => {
      const b = e.target.closest("[data-fs-props]");
      if (!b) return;
      if (b.dataset.fsProps === "close") { closeProps(); if (!src.isAi) focusInput(); }
      else if (b.dataset.fsProps === "path") copyText(info.path);
      else if (b.dataset.fsProps === "win") op("shell_properties", r).catch((err) => Q.toast(err.message, "err"));
    });
    Q.resize();
  }

  const MI = {
    "fs-reveal": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="m10 13 2 2 4-4"/>',
    "fs-openwith": '<rect x="3.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5"/><path d="M17 14v6M14 17h6"/>',
    "fs-memu": '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M8 13h8M8 16h5"/>',
    "fs-ai": '<path d="M12 3.5l1.9 4.6 4.6 1.9-4.6 1.9L12 16.5l-1.9-4.6L5.5 10l4.6-1.9z"/><path d="M18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z"/>',
    "fs-cut": '<circle cx="6" cy="17.5" r="2.5"/><circle cx="18" cy="17.5" r="2.5"/><path d="M7.8 15.7 17 4M16.2 15.7 7 4"/>',
    "fs-path": '<path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1"/><path d="M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 0 0 5.6 5.6l1-1"/>',
    "fs-name": '<path d="M4 7V5h16v2M12 5v14M9 19h6"/>',
    "fs-dup": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M14.25 11.5v6M11.25 14.5h6"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
    "fs-zip": '<rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M12 3.5v2M12 7.5v2M12 11.5v2"/><rect x="10.5" y="14.5" width="3" height="3" rx=".8"/>',
    "fs-info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8h.01"/>',
  };
  let menuHandle = null;
  let ctxReady = null;
  function loadCtx() {
    if (window.MemuCtxMenu) return Promise.resolve(window.MemuCtxMenu);
    if (!ctxReady) {
      ctxReady = new Promise((resolve) => {
        const s = document.createElement("script");
        s.src = "/memu-ctxmenu.js?v=4.1"; s.async = true;
        s.onload = () => resolve(window.MemuCtxMenu || null);
        s.onerror = () => { ctxReady = null; resolve(null); };
        document.head.appendChild(s);
      });
    }
    return ctxReady;
  }
  function closeMenu() { if (menuHandle) { try { menuHandle.close("api"); } catch (e) {  } menuHandle = null; } }
  function menuItems(r) {
    const file = !r.is_dir;
    return [
      { type: "head", label: r.name, sub: shortDir(r.dir || "", 44), icon: kindIcon(r) },
      { id: "open", label: t("Открыть", "Open"), icon: "open", hint: "Enter" },
      S.windows && file ? { id: "open-with", label: t("Открыть с помощью…", "Open with…"), icon: "fs-openwith" } : null,
      { id: "reveal", label: t("Показать в папке", "Show in folder"), icon: "fs-reveal", hint: "Ctrl+Enter" },
      S.memufiles ? { id: "memufiles", label: t("Открыть в MemuFiles", "Open in MemuFiles"), icon: "fs-memu", hint: "Alt+Enter" } : null,
      { type: "sep" },
      { id: "ask", label: file ? t("Спросить ИИ о файле", "Ask AI about the file") : t("Спросить ИИ о папке", "Ask AI about the folder"), icon: "fs-ai" },
      { type: "sep" },
      S.windows ? { id: "clip-copy", label: t("Копировать", "Copy"), icon: "copy", hint: "Ctrl+C" } : null,
      S.windows ? { id: "clip-cut", label: t("Вырезать", "Cut"), icon: "fs-cut", hint: "Ctrl+X" } : null,
      { id: "copy", label: t("Копировать путь", "Copy the path"), icon: "fs-path", hint: "Ctrl+Shift+C" },
      { id: "copy-name", label: t("Копировать имя", "Copy the name"), icon: "fs-name" },
      { type: "sep" },
      { id: "rename", label: t("Переименовать", "Rename"), icon: "edit", hint: "F2" },
      { id: "duplicate", label: t("Дублировать", "Duplicate"), icon: "fs-dup" },
      { id: "zip", label: t("Сжать в ZIP", "Compress to ZIP"), icon: "fs-zip" },
      { id: "trash", label: t("Удалить в корзину", "Move to the Recycle Bin"), icon: "trash", hint: "Del", danger: true },
      { type: "sep" },
      { id: "props", label: t("Свойства", "Properties"), icon: "fs-info", hint: "Alt+I" },
    ];
  }
  async function openMenu(r, src, point, keyboard) {
    const M = await loadCtx();
    if (!M || !r) return;
    if (M.ICONS) for (const [k, v] of Object.entries(MI)) if (!M.ICONS[k]) M.ICONS[k] = v;
    closeProps();
    const host = src.host();
    const i = src.rows().indexOf(r);
    if (i < 0) return;
    if (src.isAi) setActive(i, host, true); else setActive(i);
    const row = host && host.querySelector(`.fs-row[data-i="${i}"]`);
    const card = document.getElementById("qa-card");
    const b = card ? card.getBoundingClientRect() : null;
    const opts = {
      items: menuItems(r), keyboard: !!keyboard, owner: row || undefined, className: "fs-menu",
      label: t("Действия с файлом", "File actions"),
      returnFocus: src.isAi ? undefined : (document.getElementById("fs-q") || undefined),
      boundary: b ? { left: b.left + 4, top: b.top + 4, right: b.right - 4, bottom: b.bottom - 4 } : undefined,
      onPick: (id) => { setTimeout(() => act(id, r, src), 0); },
      onClose: () => { menuHandle = null; },
    };
    if (point) { opts.x = point.x; opts.y = point.y; } else if (row) {
      const rr = row.getBoundingClientRect();
      opts.x = Math.min(rr.right - 24, rr.left + 64); opts.y = rr.bottom - 4;
    } else opts.anchor = host;
    menuHandle = M.open(opts);
  }

  function dragStart(e, r) {
    if (!r || r.is_dir || !e.dataTransfer || S.renaming) return;
    const url = `${location.origin}/api/fsindex/drag?k=${encodeURIComponent(S.dragKey)}&path=${encodeURIComponent(r.path)}`;
    try { e.dataTransfer.setData("DownloadURL", `application/octet-stream:${r.name}:${url}`); } catch (err) {  }
    const fileUrl = "file:///" + r.path.replace(/\\/g, "/").replace(/^\/+/, "");
    e.dataTransfer.setData("text/uri-list", fileUrl);
    e.dataTransfer.setData("text/plain", r.path);
    e.dataTransfer.effectAllowed = "copy";
  }

  function bindHost(host, src) {
    if (!host || host.dataset.fsBound) return;
    host.dataset.fsBound = "1";
    const rowOf = (target) => {
      const row = target.closest && target.closest(".fs-row");
      if (!row || !host.contains(row)) return null;
      return { row, i: +row.dataset.i, r: src.rows()[+row.dataset.i] };
    };
    host.addEventListener("click", (e) => {
      const hit = rowOf(e.target);
      if (!hit || e.target.closest(".fs-rename")) return;
      const b = e.target.closest("[data-fs-act]");
      if (b) { e.stopPropagation(); act(b.dataset.fsAct, hit.r, src); return; }
      if (src.isAi) setActive(hit.i, host, true); else setActive(hit.i);
    });
    host.addEventListener("dblclick", (e) => { const hit = rowOf(e.target); if (hit && !e.target.closest("[data-fs-act], .fs-rename")) act("open", hit.r, src); });
    host.addEventListener("dragstart", (e) => { const hit = rowOf(e.target); if (hit) dragStart(e, hit.r); });
    host.addEventListener("contextmenu", (e) => {
      const hit = rowOf(e.target);
      if (!hit || e.target.closest(".fs-rename")) return;
      e.preventDefault(); e.stopPropagation();
      openMenu(hit.r, src, { x: e.clientX, y: e.clientY }, false);
    });
  }
  function panelElBind() {
    if (!panelEl) return;
    bindHost(panelEl, VIEW);
    panelEl.addEventListener("input", (e) => {
      if (e.target.id !== "fs-q") return;
      S.query = e.target.value; S.index = 0; S.limit = PAGE;
      if (!query()) run(); else schedule();
    });
    panelEl.addEventListener("change", (e) => { if (e.target.id === "fs-sort") { S.sort = e.target.value; S.index = 0; run(); focusInput(); } });
    panelEl.addEventListener("click", (e) => {
      const kind = e.target.closest("[data-fs-kind]");
      if (kind) { S.kind = kind.dataset.fsKind; S.index = 0; renderChips(); run(); focusInput(); return; }
      const tip = e.target.closest("[data-fs-tip]");
      if (tip) { S.query = tip.dataset.fsTip; const q = document.getElementById("fs-q"); if (q) q.value = S.query; run(); focusInput(); return; }
      const b = e.target.closest("[data-fs-act]");
      if (b && !b.closest(".fs-row")) {
        if (b.dataset.fsAct === "back") closeView();
        else if (b.dataset.fsAct === "more") { S.limit = Math.min(500, S.limit * 3); S.keepScroll = true; run(); }
        else if (b.dataset.fsAct === "undo") undoTrash();
        else if (b.dataset.fsAct === "undo-x") { S.undo = null; clearTimeout(undoTimer); renderFoot(); focusInput(); }
      }
    });
    panelEl.addEventListener("keydown", (e) => {
      if (e.target.id !== "fs-q" || e.isComposing) return;
      const r = S.items[S.index];
      const mod = e.ctrlKey || e.metaKey;
      const field = e.target;
      const selected = field.selectionStart !== field.selectionEnd;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (S.items.length) setActive((S.index + (e.key === "ArrowDown" ? 1 : -1) + S.items.length) % S.items.length);
      } else if (e.key === "PageDown" || e.key === "PageUp") {
        if (S.items.length) setActive(Math.max(0, Math.min(S.items.length - 1, S.index + (e.key === "PageDown" ? 8 : -8))));
      } else if (e.key === "Enter" && e.altKey && S.memufiles) act("memufiles", r);
      else if (e.key === "Enter" && mod) act("reveal", r);
      else if (e.key === "Enter" && !e.shiftKey) act("open", r);
      else if (mod && e.shiftKey && e.code === "KeyC") act("copy", r);
      else if (r && ((e.shiftKey && e.key === "F10") || e.key === "ContextMenu")) openMenu(r, VIEW, null, true);
      else if (r && e.key === "F2") act("rename", r);
      else if (r && e.altKey && e.code === "KeyI") act("props", r);

      else if (r && e.key === "Delete" && !selected && field.selectionStart === field.value.length) act("trash", r);
      else if (r && mod && !e.shiftKey && !e.altKey && e.code === "KeyC" && !selected) act(S.windows ? "clip-copy" : "copy", r);
      else if (r && mod && !e.shiftKey && !e.altKey && e.code === "KeyX" && !selected && S.windows) act("clip-cut", r);
      else if (e.key === "Escape") { closeView(); }
      else return;
      e.preventDefault(); e.stopPropagation();
    });
  }
  panelElBind();

  window.addEventListener("keydown", (e) => {
    if (!S.view || e.defaultPrevented || !(e.key === "ContextMenu" || (e.shiftKey && e.key === "F10"))) return;
    if (document.activeElement && document.activeElement.id === "fs-q" && S.items[S.index]) {
      e.preventDefault(); e.stopPropagation();
      openMenu(S.items[S.index], VIEW, null, true);
    }
  }, true);

  function renderAi() {
    if (!aiEl) return;
    const card = S.ai;
    if (!card || !card.results || !card.results.length || S.view) { Q.hidePanel("fsearch-ai"); aiEl.innerHTML = ""; Q.resize(); return; }
    if (S.windows == null && S.status) S.windows = !!S.status.windows;
    const head = `<div class="fs-ai-head">${IC.search}<span>${esc(t("Найденные файлы", "Files found"))} · <code>${esc(card.query)}</code></span>
      <button type="button" class="qx-link" data-fs-ai="all">${esc(t("Все результаты", "All results"))}</button>
      <button type="button" class="qa-icon fs-btn" data-fs-ai="close" title="${esc(t("Скрыть", "Hide"))}" aria-label="${esc(t("Скрыть", "Hide"))}">${IC.x}</button></div>`;
    aiEl.innerHTML = head + `<div class="fs-ai-list" role="listbox">${card.results.slice(0, 8).map((r, i) => rowHtml(r, i, i === S.aiIndex, true)).join("")}</div>`;
    bindHost(aiEl, AI);
    aiEl.querySelector('[data-fs-ai="all"]').onclick = () => { const q = card.query; S.ai = null; renderAi(); openView(q); };
    aiEl.querySelector('[data-fs-ai="close"]').onclick = () => { S.ai = null; renderAi(); };
    Q.showPanel("fsearch-ai");
    Q.resize();
  }
  Q.on("tool-ui", (e) => {
    if (!e || !e.ui || e.ui.type !== "fsresults") return;
    S.ai = e.ui; S.aiIndex = -1; renderAi();
  });
  Q.on("chat-new", () => { S.ai = null; renderAi(); if (S.view) closeView(); });
  Q.on("escape", (e) => { if (propsEl) { e.preventDefault(); closeProps(); return; } if (S.view) { e.preventDefault(); closeView(); } });
  Q.on("open", () => { loadSettings(); });
  Q.on("submit", (e) => {

    const text = String((e && e.text) || "");
    if (on("enabled") && on("quick_prefix") && /^\?\S/.test(text)) { e.preventDefault(); Q.setInput(""); openView(text.slice(1)); }
  });

  const input = document.getElementById("qa-input");
  const GLOB = /^(?:\*[^\s*]*\.[\w*]{1,10}|[^\s*?]+\*[^\s]*\.[\w*]{1,10})$/;
  if (input) {
    input.addEventListener("input", () => {
      if (S.view || !on("enabled")) return;
      const v = input.value;
      if (on("quick_prefix") && v.length > 1 && v[0] === "?" && v[1] !== "?" && !v.includes("\n")) { Q.setInput(""); openView(v.slice(1)); return; }
      if (on("quick_glob") && GLOB.test(v) && !Q.state().busy) { Q.setInput(""); openView(v); }
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented) return;
    if (e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.code === "KeyF") {
      if (!on("enabled")) return;
      e.preventDefault();
      if (S.view) { closeView(); return; }

      const v = Q.getInput().trim();
      if (v && !v.includes("\n") && v.length <= 200 && !Q.state().busy) { Q.setInput(""); openView(v); } else openView("");
    }
  });
  if (Q.addAction) {
    Q.addAction({ id: "fsearch", section: () => t("Файлы", "Files"), icon: IC.search, label: () => t("Поиск файлов на ПК", "Search files on this PC"),
      keys: ["Alt", "F"], sub: () => t("мгновенно, как Everything · или «?имя»", "instant, like Everything · or “?name”"), hidden: () => !on("enabled"), run: () => openView("") });
  }

  window.QuickFileSearch = { open: (q) => { openView(q || ""); return true; }, close: () => { closeView(); return true; }, state: () => S, run };
  loadSettings();
})();
