

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || !Q.registerPanel || !Q.addAction) return;
  const root = document.documentElement;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const svg = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
  const IC = {
    region: svg('<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><rect x="8.5" y="8.5" width="7" height="7" rx="1"/>'),
    clip: svg('<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4.5V3h6v1.5M9.5 10h5M9.5 14h5"/>'),
    snip: svg('<path d="M8 6 3 12l5 6M16 6l5 6-5 6M13.5 4l-3 16"/>'),
    ai: svg('<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
    sticker: svg('<path d="M5 4h14a1 1 0 0 1 1 1v9l-6 6H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M14 20v-5a1 1 0 0 1 1-1h5"/>'),
    doc: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'),
    text: svg('<path d="M5 6h14M5 12h14M5 18h9"/>'),
    copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/>'),
    translate: svg('<path d="M4 5h8M8 3v2c0 4-2 7-5 8M6 9c1 2 3 4 6 5"/><path d="m13 21 4-9 4 9M14.5 18h5"/>'),
    eye: svg('<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>'),
    pin: svg('<path d="M9 4h6l-1 5 4 3v2H6v-2l4-3z"/><path d="M12 14v6"/>'),
    trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    edit: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
    pause: svg('<path d="M9 5v14M15 5v14"/>'),
    play: svg('<path d="M7 5v14l11-7z"/>'),
    back: svg('<path d="M15 6l-6 6 6 6"/>'),
    search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>'),
    open: svg('<path d="M14 4h6v6M20 4l-9 9M10 6H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-5"/>'),
    lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
  };
  const DOC_EXT = /\.(pdf|docx|doc|rtf|odt|epub|fb2)$/i;
  const S = {
    settings: {}, snippets: [], native: !!Q.native, stickers: [], loaded: false,
    view: "", query: "", items: [], index: 0, total: 0, clip: { settings: {}, status: {} }, busy: false,
    edit: null,
    inline: [], inlineIndex: 0,
    strip: null,
    copyNext: false,
  };

  async function api(path, body) {
    return Q.api(path, body === undefined ? {} : { body: { lang: Q.lang(), ...body } });
  }
  async function loadConfig() {
    try {
      const data = await api("/api/quick/features?lang=" + Q.lang());
      S.settings = data.settings || {}; S.snippets = data.snippets || []; S.native = !!data.native; S.stickers = data.stickers || [];
      S.loaded = true;
    } catch (e) { S.loaded = false; }
  }
  const on = (key) => S.settings[key] !== false;

  let panelEl = null;
  Q.registerPanel("features", (el) => { panelEl = el; el.classList.add("qx"); return {}; }, { slot: "top", hidden: true, order: -5 });
  function mode() { return S.view ? "view" : (S.inline.length ? "inline" : (S.strip ? "strip" : "")); }
  function render() {
    if (!panelEl) return;
    const m = mode();
    if (m === "view") root.dataset.qxView = S.view; else delete root.dataset.qxView;
    if (m === "inline") root.dataset.qxInline = "1"; else delete root.dataset.qxInline;
    if (!m) { Q.hidePanel("features"); panelEl.innerHTML = ""; Q.resize(); return; }
    panelEl.dataset.mode = m;

    const active = document.activeElement;
    const searching = !!(active && active.id === "qx-q");
    const caret = searching ? active.selectionStart : null;
    panelEl.innerHTML = m === "view" ? viewHtml() : m === "inline" ? inlineHtml() : stripHtml();
    Q.showPanel("features");
    bindPanel(m);
    const q = document.getElementById("qx-q");
    if (q && (searching || !document.activeElement || document.activeElement === document.body || panelEl.contains(active) && !active.isConnected)) {
      q.focus({ preventScroll: true });
      const pos = caret == null ? q.value.length : caret;
      q.setSelectionRange(pos, pos);
    }
    Q.resize();
  }

  function viewTitle() {
    return { clipboard: t("История буфера обмена", "Clipboard history"), snippets: t("Сниппеты", "Snippets"),
      stickers: t("Стикеры", "Stickers"), "snippet-edit": S.edit && S.edit.created ? t("Сниппет", "Snippet") : t("Новый сниппет", "New snippet") }[S.view] || "";
  }
  function head(placeholder, extra = "") {
    return `<div class="qa-view-head qx-head"><button type="button" class="qa-icon qa-back" data-qx="back" title="${esc(t("Назад (Esc)", "Back (Esc)"))}" aria-label="${esc(t("Назад", "Back"))}">${IC.back}</button>
      ${placeholder ? `<label class="qa-search">${IC.search}<input id="qx-q" type="text" spellcheck="false" autocomplete="off" placeholder="${esc(placeholder)}" value="${esc(S.query)}" aria-label="${esc(placeholder)}"></label>`
        : `<b class="qx-title">${esc(viewTitle())}</b>`}${extra}</div>`;
  }
  function empty(icon, title, text, button = "") {
    return `<div class="qx-empty">${icon}<b>${esc(title)}</b><span>${text}</span>${button}</div>`;
  }
  function keysHint(rows) {
    return `<div class="qx-keys">${rows.map(([k, label]) => `<span>${k.map((x) => `<kbd>${esc(x)}</kbd>`).join("")} ${esc(label)}</span>`).join("")}</div>`;
  }
  function ago(ms) {
    const s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 60) return t("только что", "just now");
    if (s < 3600) return t(`${Math.floor(s / 60)} мин назад`, `${Math.floor(s / 60)} min ago`);
    if (s < 86400) return t(`${Math.floor(s / 3600)} ч назад`, `${Math.floor(s / 3600)} h ago`);
    return new Date(ms).toLocaleDateString(Q.lang() === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "short" });
  }
  function rowHtml(i, icon, title, meta, buttons, cls = "") {
    return `<div class="qa-row qx-row ${i === S.index ? "active" : ""} ${cls}" role="option" data-i="${i}" aria-selected="${i === S.index}">
      <span class="ic">${icon}</span><span class="qx-main"><span class="tx">${title}</span>${meta ? `<small>${meta}</small>` : ""}</span>
      <span class="qx-btns">${buttons}</span></div>`;
  }
  const btn = (act, icon, label, i) => `<button type="button" class="qa-icon qx-btn" data-qx="${act}" data-i="${i}" title="${esc(label)}" aria-label="${esc(label)}">${icon}</button>`;

  function viewHtml() {
    if (S.view === "clipboard") return clipboardHtml();
    if (S.view === "snippets") return snippetsHtml();
    if (S.view === "stickers") return stickersHtml();
    if (S.view === "snippet-edit") return editorHtml();
    return "";
  }

  function clipboardHtml() {
    const cs = S.clip.settings || {}, st = S.clip.status || {};
    const paused = !!cs.clipboard_paused;
    const pause = cs.clipboard && S.native ? `<button type="button" class="qa-icon qx-btn ${paused ? "on" : ""}" data-qx="pause" aria-pressed="${paused}" title="${esc(paused ? t("Продолжить запись", "Resume recording") : t("Пауза — не записывать копирования", "Pause — do not record copies"))}">${paused ? IC.play : IC.pause}</button>` : "";
    let body;
    if (!S.native && !S.items.length) {
      body = empty(IC.clip, t("Работает в приложении для Windows", "Works in the Windows app"), esc(t("История буфера обмена ведётся приложением MemuAI для Windows.", "The clipboard history is kept by the MemuAI Windows app.")));
    } else if (!cs.clipboard && !S.items.length) {
      body = empty(IC.lock, t("История буфера обмена выключена", "Clipboard history is off"),
        esc(t("Включите — и всё, что вы копируете, будет под рукой: поиск, вставка обратно, «спросить ИИ». Хранится только на этом компьютере; пароли из менеджеров паролей и похожие на ключи строки не записываются.",
          "Turn it on to keep what you copy at hand: search, paste back, ask AI. Stored only on this computer; copies from password managers and key-like strings are never recorded.")),
        `<button type="button" class="qa-btn accent" data-qx="enable-clip">${esc(t("Включить", "Turn on"))}</button>`);
    } else if (!S.items.length) {
      body = S.query ? empty(IC.search, t("Ничего не нашлось", "Nothing found"), esc(t("Попробуйте другое слово.", "Try another word.")))
        : empty(IC.clip, t("Пока пусто", "Nothing yet"), esc(t("Скопируйте что-нибудь (Ctrl+C) в любой программе — оно появится здесь.", "Copy something (Ctrl+C) in any program — it shows up here.")));
    } else {
      body = `<div class="qx-list" role="listbox">${S.items.map((item, i) => {
        const text = item.text.replace(/\s+/g, " ").trim();

        const kept = item.text.length, size = item.chars > kept ? t(`начало: ${num(kept)} из ${num(item.chars)} зн.`, `start: ${num(kept)} of ${num(item.chars)} chars`) : t(`${item.chars} зн.`, `${item.chars} chars`);
        const meta = [item.app ? esc(item.app.replace(/\.exe$/i, "")) : "", esc(ago(item.t)), esc(size)].filter(Boolean).join(" · ");
        return rowHtml(i, item.pinned ? IC.pin : IC.text, esc(text.slice(0, 220)), meta,
          btn("ask", IC.ai, t("Спросить ИИ (Tab)", "Ask AI (Tab)"), i) + btn(item.pinned ? "unpin" : "pin", IC.pin, item.pinned ? t("Открепить (Ctrl+P)", "Unpin (Ctrl+P)") : t("Закрепить (Ctrl+P)", "Pin (Ctrl+P)"), i)
          + btn("delete", IC.trash, t("Удалить (Shift+Delete)", "Delete (Shift+Delete)"), i), item.pinned ? "pinned" : "");
      }).join("")}</div>`;
    }
    const banner = paused ? `<div class="qx-banner">${IC.pause}<span>${esc(t("Запись на паузе — новые копирования не сохраняются.", "Recording is paused — new copies are not saved."))}</span><button type="button" class="qa-btn" data-qx="pause">${esc(t("Продолжить", "Resume"))}</button></div>` : "";
    const foot = S.items.length ? `<div class="qx-foot">${keysHint([[["↵"], S.native ? t("вставить", "paste") : t("копировать", "copy")], [["Ctrl", "C"], t("копировать", "copy")], [["Tab"], t("спросить ИИ", "ask AI")]])}
      <button type="button" class="qx-link" data-qx="clear">${esc(t("Очистить", "Clear"))}</button></div>` : "";
    return head(t("Поиск в истории буфера…", "Search the clipboard…"), pause) + banner + body + foot;
  }
  async function loadClipboard() {
    try {
      const data = await api("/api/quick/features/clipboard?limit=80&q=" + encodeURIComponent(S.query));
      S.items = data.items || []; S.total = data.total || 0; S.clip = { settings: data.settings || {}, status: data.status || {} }; S.native = !!data.native;
    } catch (e) { S.items = []; Q.toast(e.message, "err"); }
    S.index = Math.min(S.index, Math.max(0, S.items.length - 1));
    render();
  }
  async function clipAction(action, item) {
    if (!item) return;
    if (action === "paste" || action === "copy") {
      let data = null;
      try { data = await api("/api/quick/features/clipboard", { action, id: item.id }); } catch (e) { Q.toast(e.message, "err"); return; }
      if (data && data.native === false) { await copyText(item.text); cutNote(data, item); return; }
      if (data && data.ok) { if (action === "copy") Q.toast(t("Скопировано", "Copied")); else closeView(false); cutNote(data, item); }
      else Q.toast(action === "paste" ? t("Не нашёл программу, в которую вставлять", "No program to paste into") : t("Не удалось скопировать", "Copy failed"), "err");
      return;
    }
    if (action === "ask") { closeView(false); Q.addContext(item.text); Q.toast(t("Текст добавлен к вопросу", "Text attached to the question")); return; }
    try { await api("/api/quick/features/clipboard", { action, id: item.id }); } catch (e) { Q.toast(e.message, "err"); }
    loadClipboard();
  }

  function cutNote(data, item) {
    if (!data || !data.truncated) return;
    Q.toast(t(`Только начало: ${num(item.text.length)} из ${num(item.chars)} знаков — длинные копирования хранятся в истории не целиком`,
      `Only the beginning: ${num(item.text.length)} of ${num(item.chars)} characters — long copies are not kept in full`), "warn", 4200);
  }
  function num(v) { return Number(v || 0).toLocaleString(Q.lang() === "en" ? "en-US" : "ru-RU"); }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); Q.toast(t("Скопировано", "Copied")); return true; }
    catch (e) {
      try { const r = await Q.api("/api/quick/window", { body: { action: "copy", text } }); if (r.ok) { Q.toast(t("Скопировано", "Copied")); return true; } } catch (_) {  }
    }
    Q.toast(t("Не удалось скопировать", "Copy failed"), "err");
    return false;
  }

  function snippetMeta(s) {
    const preview = s.text.replace(/\s+/g, " ").trim().slice(0, 90);
    return (s.kind === "ai" ? `<em class="qx-tag">${esc(t("ИИ", "AI"))}</em> ` : "") + esc(preview);
  }
  function snippetsHtml() {
    const add = `<button type="button" class="qa-icon qx-btn" data-qx="new" title="${esc(t("Новый сниппет (Ctrl+N)", "New snippet (Ctrl+N)"))}" aria-label="${esc(t("Новый сниппет", "New snippet"))}">${IC.plus}</button>`;
    let body;
    if (!S.items.length) {
      body = S.query ? empty(IC.search, t("Ничего не нашлось", "Nothing found"), esc(t("Создайте новый сниппет с этим словом.", "Create a new snippet with this word.")))
        : empty(IC.snip, t("Сниппетов нет", "No snippets"), esc(t("Сниппет — заготовка текста или ИИ-шаблон. Наберите «;» и ключевое слово в поле вопроса.", "A snippet is ready text or an AI template. Type “;” and its keyword in the question field.")),
          `<button type="button" class="qa-btn accent" data-qx="new">${esc(t("Создать сниппет", "Create a snippet"))}</button>`);
    } else {
      body = `<div class="qx-list" role="listbox">${S.items.map((s, i) => rowHtml(i, s.kind === "ai" ? IC.ai : IC.snip,
        `${s.keyword ? `<code class="qx-kw">;${esc(s.keyword)}</code> ` : ""}${esc(s.name)}`, snippetMeta(s),
        btn("edit", IC.edit, t("Изменить (Ctrl+E)", "Edit (Ctrl+E)"), i))).join("")}</div>`;
    }
    const foot = S.items.length ? `<div class="qx-foot">${keysHint([[["↵"], S.native ? t("вставить / выполнить", "paste / run") : t("использовать", "use")], [["Ctrl", "E"], t("изменить", "edit")], [[";"], t("в поле вопроса", "in the question field")]])}</div>` : "";
    return head(t("Поиск сниппетов…", "Search snippets…"), add) + body + foot;
  }
  function filterSnippets() {
    const q = S.query.trim().toLowerCase().replace(/^;/, "");
    S.items = S.snippets.filter((s) => !q || s.keyword.includes(q) || s.name.toLowerCase().includes(q) || s.text.toLowerCase().includes(q));
    S.index = Math.min(S.index, Math.max(0, S.items.length - 1));
  }
  function editorHtml() {
    const s = S.edit || {};
    const vars = ["{date}", "{date+1}", "{time}", "{weekday}", "{clipboard}", "{selection}", "{input}", "{cursor}"];
    const ai = s.kind === "ai";
    return head("", s.created ? `<button type="button" class="qa-icon qx-btn" data-qx="delete-snippet" title="${esc(t("Удалить сниппет", "Delete the snippet"))}" aria-label="${esc(t("Удалить сниппет", "Delete the snippet"))}">${IC.trash}</button>` : "")
      + `<form class="qx-form" id="qx-form" autocomplete="off">
        <div class="qx-grid"><label><span>${esc(t("Название", "Name"))}</span><input id="qx-name" maxlength="80" value="${esc(s.name || "")}" placeholder="${esc(t("Например: Подпись", "E.g.: Signature"))}"></label>
          <label><span>${esc(t("Ключевое слово", "Keyword"))}</span><span class="qx-kwin"><i>;</i><input id="qx-kw" maxlength="32" value="${esc(s.keyword || "")}" placeholder="sig"></span></label></div>
        <div class="qx-seg" role="radiogroup" aria-label="${esc(t("Вид", "Kind"))}">
          <label><input type="radio" name="qx-kind" value="text" ${ai ? "" : "checked"}><span>${IC.snip}${esc(t("Текст", "Text"))}</span></label>
          <label><input type="radio" name="qx-kind" value="ai" ${ai ? "checked" : ""}><span>${IC.ai}${esc(t("ИИ-шаблон", "AI template"))}</span></label></div>
        <textarea id="qx-text" rows="5" placeholder="${esc(ai ? t("Инструкция модели: что сделать с текстом (он прикрепится сам — набранный после ключевого слова, выделенный или из буфера).", "An instruction for the model: what to do with the text (attached automatically — typed after the keyword, selected or from the clipboard).") : t("Текст сниппета. Переменные — кнопками ниже.", "The snippet text. Variables — the buttons below."))}">${esc(s.text || "")}</textarea>
        <div class="qx-vars" ${ai ? "hidden" : ""}>${vars.map((v) => `<button type="button" class="qx-var" data-var="${esc(v)}">${esc(v)}</button>`).join("")}</div>
        <div class="qx-form-foot"><span class="qx-err" id="qx-err" role="alert"></span><button type="button" class="qa-btn" data-qx="cancel">${esc(t("Отмена", "Cancel"))}</button><button type="submit" class="qa-btn accent">${esc(t("Сохранить", "Save"))} <kbd>Ctrl ↵</kbd></button></div>
      </form>`;
  }
  async function saveSnippets(list) {
    const data = await api("/api/quick/features/snippets", { snippets: list });
    S.snippets = data.snippets || [];
  }
  async function submitEditor() {
    const form = document.getElementById("qx-form");
    if (!form) return;
    const val = (id) => document.getElementById(id).value;
    const kind = (form.querySelector("[name=qx-kind]:checked") || {}).value === "ai" ? "ai" : "text";
    const keyword = val("qx-kw").replace(/^;+/, "").replace(/\s+/g, "").toLowerCase();
    const row = { ...(S.edit || {}), name: val("qx-name").trim(), keyword, kind, text: val("qx-text") };
    const err = document.getElementById("qx-err");
    if (!row.text.trim()) { err.textContent = t("Напишите текст сниппета", "Write the snippet text"); document.getElementById("qx-text").focus(); return; }
    if (!row.name && !row.keyword) { err.textContent = t("Нужно название или ключевое слово", "A name or a keyword is needed"); document.getElementById("qx-name").focus(); return; }
    const clash = keyword && S.snippets.find((s) => s.keyword === keyword && s.id !== row.id);
    if (clash) { err.textContent = t(`;${keyword} уже у сниппета «${clash.name}»`, `;${keyword} is taken by “${clash.name}”`); document.getElementById("qx-kw").focus(); return; }
    const list = row.id ? S.snippets.map((s) => (s.id === row.id ? row : s)) : [...S.snippets, { ...row, id: "" }];
    try { await saveSnippets(list); } catch (e) { err.textContent = e.message; return; }
    Q.toast(t("Сниппет сохранён", "Snippet saved"));
    openView("snippets");
  }

  function stickersHtml() {
    let body;
    if (!S.items.length) {
      body = empty(IC.sticker, t("Стикеров нет", "No stickers"), esc(t("Закрепите ответ кнопкой «Стикер» под ним (Ctrl+Shift+P) — он останется поверх окон, даже когда быстрый доступ закрыт.", "Pin an answer with the “Sticker” button under it (Ctrl+Shift+P) — it stays on top of your windows even when Quick Ask is closed.")));
    } else {
      body = `<div class="qx-list" role="listbox">${S.items.map((s, i) => rowHtml(i, IC.sticker, esc(s.title || t("Стикер", "Sticker")),
        `${s.open ? `<em class="qx-tag on">${esc(t("на экране", "on screen"))}</em> ` : ""}${esc(plain(s.text).slice(0, 100))}`,
        (s.open ? btn("hide", IC.eye, t("Убрать с экрана", "Hide from the screen"), i) : btn("show", IC.open, t("Показать", "Show"), i))
        + btn("ask-sticker", IC.ai, t("Продолжить в окне (Tab)", "Continue here (Tab)"), i) + btn("delete-sticker", IC.trash, t("Удалить (Shift+Delete)", "Delete (Shift+Delete)"), i),
        s.color ? "tint-" + s.color : "")).join("")}</div>`;
    }
    const foot = S.items.length ? `<div class="qx-foot">${keysHint([[["↵"], t("показать / убрать", "show / hide")], [["Tab"], t("спросить о нём", "ask about it")]])}</div>` : "";
    return head(t("Поиск стикеров…", "Search stickers…")) + (S.native ? "" : `<div class="qx-banner">${IC.sticker}<span>${esc(t("Плавающие стикеры показываются в приложении MemuAI для Windows; здесь — их список.", "Floating stickers appear in the MemuAI Windows app; this is their list."))}</span></div>`) + body + foot;
  }
  async function loadStickers() {
    try { S.stickers = (await api("/api/quick/features/stickers")).items || []; } catch (e) { S.stickers = []; }
    const q = S.query.trim().toLowerCase();
    S.items = S.stickers.filter((s) => !q || (s.title + " " + s.text).toLowerCase().includes(q));
    S.index = Math.min(S.index, Math.max(0, S.items.length - 1));
    render();
  }
  async function stickerAction(action, s) {
    if (!s) return;
    if (action === "ask-sticker") { closeView(false); Q.addContext(s.text); return; }
    if (!S.native && action === "show") { window.open("/quick/sticker.html?id=" + encodeURIComponent(s.id), "_blank", "width=380,height=300"); }
    const map = { show: "open", hide: "close", "delete-sticker": "delete" };
    try { await api("/api/quick/features/stickers", { action: map[action], id: s.id }); } catch (e) { Q.toast(e.message, "err"); }
    if (action === "delete-sticker") Q.toast(t("Стикер удалён", "Sticker deleted"));
    loadStickers();
  }
  async function pinSticker(turn) {
    const text = String((turn && turn.a) || "").trim();
    if (!text) { Q.toast(t("Нечего закреплять — ответа ещё нет", "Nothing to pin — no answer yet"), "err"); return; }
    if (!on("stickers")) { Q.toast(t("Стикеры выключены: Настройки → Быстрый доступ → Возможности → Буфер, сниппеты и стикеры", "Stickers are off: Settings → Quick Ask → Features → Clipboard, snippets and stickers"), "err", 3600); return; }
    try {
      const data = await api("/api/quick/features/stickers", { action: "create", text, title: String(turn.q || "").replace(/\s+/g, " ").slice(0, 80), chat: Q.state().chatId });
      if (data.native && !data.shown) Q.toast(t("Стикер сохранён, но на экране уже 8 стикеров — закройте какой-нибудь", "Sticker saved, but 8 are already on screen — close one"), "err", 3600);
      else Q.toast(data.native ? t("Ответ закреплён стикером", "The answer is pinned as a sticker") : t("Стикер сохранён — плавающие окна работают в приложении для Windows", "Sticker saved — floating windows work in the Windows app"), "", 3000);
    } catch (e) { Q.toast(e.message, "err"); }
  }

  const plain = (text) => String(text || "").replace(/```[\w-]*|[*_`#>]+/g, "").replace(/\s+/g, " ").trim();
  function currentList() { return S.items; }
  function openView(view) {
    S.view = view; S.query = ""; S.index = 0; S.items = []; S.inline = []; S.edit = view === "snippet-edit" ? S.edit : null;
    if (view === "snippets") filterSnippets();
    render();
    if (view === "clipboard") loadClipboard();
    if (view === "stickers") loadStickers();
    focusView();
  }
  function closeView(refocus = true) {
    if (!S.view) return;
    S.view = ""; S.items = []; S.edit = null;
    render();
    if (refocus) Q.setInput(Q.getInput());
  }
  function focusView() {
    requestAnimationFrame(() => {
      const target = document.getElementById(S.view === "snippet-edit" ? (S.edit && S.edit.name ? "qx-text" : "qx-name") : "qx-q");
      if (target) target.focus();
    });
  }
  function moveIndex(step) {
    const n = currentList().length;
    if (!n) return;
    S.index = Math.max(0, Math.min(n - 1, S.index + step));
    panelEl.querySelectorAll(".qx-row").forEach((row) => { const active = +row.dataset.i === S.index; row.classList.toggle("active", active); row.setAttribute("aria-selected", String(active)); if (active) row.scrollIntoView({ block: "nearest" }); });
  }
  function enter(index = S.index) {
    const item = currentList()[index];
    if (!item) return;
    if (S.view === "clipboard") clipAction(S.native ? "paste" : "copy", item);
    else if (S.view === "snippets") useSnippet(item, "");
    else if (S.view === "stickers") stickerAction(item.open ? "hide" : "show", item);
  }

  function bindPanel(m) {
    panelEl.onclick = (e) => {
      const el = e.target.closest("[data-qx]");
      if (el) {
        e.stopPropagation();
        const act = el.dataset.qx, i = el.dataset.i !== undefined ? +el.dataset.i : S.index;
        const item = currentList()[i];
        if (act === "back") { S.view === "snippet-edit" ? openView("snippets") : closeView(); return; }
        if (act === "cancel") { openView("snippets"); return; }
        if (act === "enable-clip") { saveSettings({ clipboard: true }).then(loadClipboard); return; }
        if (act === "pause") { saveSettings({ clipboard_paused: !(S.clip.settings || {}).clipboard_paused }).then(loadClipboard); return; }
        if (act === "clear") { clearClipboard(el); return; }
        if (act === "new") { S.edit = { kind: "text", keyword: S.query.replace(/^;/, "").trim().split(/\s+/)[0] || "" }; S.view = "snippet-edit"; render(); focusView(); return; }
        if (act === "edit") { S.edit = { ...item }; S.view = "snippet-edit"; render(); focusView(); return; }
        if (act === "delete-snippet") { deleteSnippet(); return; }
        if (S.view === "clipboard") { clipAction(act, item); return; }
        if (S.view === "stickers") { stickerAction(act, item); return; }
        if (act === "strip") { runStrip(+el.dataset.n); return; }
        if (act === "strip-close") { S.strip = null; render(); return; }
        if (act === "inline") { useInline(+el.dataset.n); return; }
        return;
      }
      const row = e.target.closest(".qx-row[data-i]");
      if (row) enter(+row.dataset.i);
    };
    panelEl.onmousemove = (e) => {
      const row = e.target.closest(".qx-row[data-i]");
      if (row && +row.dataset.i !== S.index) { S.index = +row.dataset.i; panelEl.querySelectorAll(".qx-row").forEach((r) => r.classList.toggle("active", r === row)); }
    };
    const q = document.getElementById("qx-q");
    if (q) {
      q.oninput = () => {
        S.query = q.value; S.index = 0;
        if (S.view === "clipboard") { clearTimeout(bindPanel.timer); bindPanel.timer = setTimeout(loadClipboard, 120); }
        else if (S.view === "snippets") { filterSnippets(); render(); }
        else if (S.view === "stickers") { loadStickers(); }
      };
      q.onkeydown = viewKeys;
    }
    const form = document.getElementById("qx-form");
    if (form) {
      form.onsubmit = (e) => { e.preventDefault(); submitEditor(); };
      form.onkeydown = (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.stopPropagation(); submitEditor(); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); openView("snippets"); }
      };
      form.querySelectorAll("[name=qx-kind]").forEach((r) => { r.onchange = () => { S.edit = { ...S.edit, kind: r.value, name: document.getElementById("qx-name").value, keyword: document.getElementById("qx-kw").value, text: document.getElementById("qx-text").value }; render(); }; });
      form.querySelectorAll("[data-var]").forEach((b) => { b.onclick = () => insertVar(b.dataset.var); });
      form.addEventListener("input", () => { const err = document.getElementById("qx-err"); if (err) err.textContent = ""; });
    }
  }

  function insertVar(v) {
    const area = document.getElementById("qx-text");
    const a = area.selectionStart, b = area.selectionEnd;
    area.value = area.value.slice(0, a) + v + area.value.slice(b);
    area.focus(); area.setSelectionRange(a + v.length, a + v.length);
  }
  function viewKeys(e) {
    const item = currentList()[S.index];
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "ArrowDown") { e.preventDefault(); moveIndex(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveIndex(-1); }
    else if (e.key === "Enter") { e.preventDefault(); enter(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeView(); }
    else if (e.key === "Tab" && !e.shiftKey && item && S.view !== "snippets") { e.preventDefault(); S.view === "clipboard" ? clipAction("ask", item) : stickerAction("ask-sticker", item); }
    else if (mod && e.code === "KeyC" && S.view === "clipboard" && item && e.target.selectionStart === e.target.selectionEnd) { e.preventDefault(); clipAction("copy", item); }
    else if (mod && e.code === "KeyP" && S.view === "clipboard" && item) { e.preventDefault(); e.stopPropagation(); clipAction(item.pinned ? "unpin" : "pin", item); }
    else if (e.shiftKey && e.key === "Delete" && item) { e.preventDefault(); S.view === "clipboard" ? clipAction("delete", item) : S.view === "stickers" ? stickerAction("delete-sticker", item) : null; }
    else if (mod && e.code === "KeyE" && S.view === "snippets" && item) { e.preventDefault(); S.edit = { ...item }; S.view = "snippet-edit"; render(); focusView(); }
    else if (mod && e.code === "KeyN" && S.view === "snippets") { e.preventDefault(); e.stopPropagation(); S.edit = { kind: "text", keyword: "" }; S.view = "snippet-edit"; render(); focusView(); }
    else return;
    e.stopPropagation();
  }
  async function clearClipboard(el) {
    if (el.dataset.confirm !== "1") {
      el.dataset.confirm = "1"; el.textContent = t("Точно очистить? Закреплённые останутся", "Clear for sure? Pinned stay"); el.classList.add("danger");
      setTimeout(() => { if (el.isConnected) { el.dataset.confirm = ""; el.textContent = t("Очистить", "Clear"); el.classList.remove("danger"); } }, 4000);
      return;
    }
    try { await api("/api/quick/features/clipboard", { action: "clear" }); Q.toast(t("История очищена", "History cleared")); } catch (e) { Q.toast(e.message, "err"); }
    loadClipboard();
  }
  async function deleteSnippet() {
    const id = S.edit && S.edit.id;
    if (!id) return;
    try { await saveSnippets(S.snippets.filter((s) => s.id !== id)); Q.toast(t("Сниппет удалён", "Snippet deleted")); } catch (e) { Q.toast(e.message, "err"); return; }
    openView("snippets");
  }
  async function saveSettings(patch) {
    try {
      const data = await api("/api/quick/features/settings", { settings: patch });
      S.settings = data.settings || S.settings;
      return data;
    } catch (e) { Q.toast(e.message, "err"); return null; }
  }

  function inlineHtml() {
    return `<div class="qx-inline" role="listbox" aria-label="${esc(t("Сниппеты", "Snippets"))}"><div class="qa-section">${esc(t("Сниппеты", "Snippets"))}</div>${S.inline.map((s, i) => `
      <button type="button" role="option" class="qa-row ${i === S.inlineIndex ? "active" : ""}" data-qx="inline" data-n="${i}" aria-selected="${i === S.inlineIndex}">
        <span class="ic">${s.kind === "ai" ? IC.ai : IC.snip}</span><span class="tx">${s.keyword ? `<code class="qx-kw">;${esc(s.keyword)}</code> ` : ""}${esc(s.name)}</span>
        <span class="sub">${s.kind === "ai" ? esc(t("ИИ-шаблон", "AI template")) : esc(s.text.replace(/\s+/g, " ").slice(0, 40))}</span>
        ${i === S.inlineIndex ? `<span class="keys"><kbd>↵</kbd>${s.kind === "text" ? "<kbd>Tab</kbd>" : ""}</span>` : ""}</button>`).join("")}</div>`;
  }
  function matchInline(text) {
    if (!on("snippets") || !text.startsWith(";") || text.includes("\n")) return [];
    const word = text.slice(1).split(/\s+/)[0].toLowerCase();
    const rest = text.slice(1 + word.length);

    const rows = S.snippets.filter((s) => {
      if (rest) return s.keyword === word;
      if (!word) return true;
      return s.keyword.startsWith(word) || s.name.toLowerCase().split(/\s+/).some((w) => w.startsWith(word)) || s.keyword.includes(word);
    });
    rows.sort((a, b) => (a.keyword === word ? -1 : 0) - (b.keyword === word ? -1 : 0));
    return rows.slice(0, 7);
  }
  function updateInline() {
    if (S.view) return;
    const text = Q.getInput();
    const rows = matchInline(text);
    const changed = rows.map((r) => r.id).join() !== S.inline.map((r) => r.id).join();
    S.inline = rows;
    if (changed) S.inlineIndex = 0;
    render();
  }
  function inputRest() {
    const text = Q.getInput();
    const word = text.slice(1).split(/\s+/)[0];
    return text.slice(1 + word.length).trim();
  }
  function useInline(n) {
    const s = S.inline[n];
    if (!s) return;
    useSnippet(s, inputRest());
  }
  async function useSnippet(s, rest) {
    if (s.kind === "ai") return runAiSnippet(s, rest);
    let data;
    try { data = await api("/api/quick/features/snippets/use", { id: s.id, input: rest, selection: Q.state().selection || "", insert: S.native }); }
    catch (e) { Q.toast(e.message, "err"); return; }
    S.inline = []; S.view = ""; render();
    Q.setInput("", { focus: false });
    if (data.inserted) return;
    if (data.error) { Q.toast(data.error, "err", 3200); return; }
    await copyText(data.text);
  }
  async function snippetToInput(s) {
    let data;
    try { data = await api("/api/quick/features/snippets/use", { id: s.id, input: inputRest(), selection: Q.state().selection || "" }); }
    catch (e) { Q.toast(e.message, "err"); return; }
    S.inline = []; render();
    Q.setInput(data.text);
  }
  async function runAiSnippet(s, rest) {
    const state = Q.state();
    if (state.busy) { Q.toast(t("Дождитесь ответа — или Esc, чтобы остановить", "Wait for the answer — or press Esc to stop"), "", 2600); return; }
    let source = rest;
    if (!source && !state.selection) {
      try { source = (await api("/api/quick/features/snippets/use", { id: s.id })).clipboard || ""; } catch (e) { source = ""; }
      if (!source) { try { source = await navigator.clipboard.readText(); } catch (e) { source = ""; } }
      if (source) Q.toast(t("Взял текст из буфера обмена", "Took the text from the clipboard"));
    }
    if (!source && !state.selection) {
      Q.toast(t(`Напишите текст после ;${s.keyword || "…"} — или выделите/скопируйте его`, `Type the text after ;${s.keyword || "…"} — or select/copy it`), "err", 3600);
      return;
    }
    S.inline = []; S.view = ""; render();
    if (source) Q.addContext(source, { focus: false });
    Q.send(s.name || ";" + s.keyword, { page: false, prompt: s.text });
  }

  const STRIP = () => [
    [IC.text, t("Распознать", "Extract text"), t("Распознай весь текст на изображении и верни только его — строки и порядок как на картинке, без пояснений.", "Extract all the text in the image and return only it — lines and order as in the picture, no comments.")],
    [IC.copy, t("Копировать текст", "Copy text"), t("Распознай весь текст на изображении и верни только его — строки и порядок как на картинке, без пояснений.", "Extract all the text in the image and return only it — lines and order as in the picture, no comments."), "copy"],
    [IC.translate, t("Перевести", "Translate"), t("Переведи текст на изображении на русский (если он уже на русском — на английский). Верни только перевод.", "Translate the text in the image into English (if it is already English — into Russian). Return only the translation.")],
    [IC.eye, t("Объяснить", "Explain"), t("Объясни, что на этом фрагменте экрана: кратко и по делу.", "Explain what is in this part of the screen: briefly and to the point.")],
  ];
  function stripHtml() {
    return `<div class="qx-strip"><span class="qx-strip-title" title="${esc(S.strip.label || "")}">${IC.region}${esc(t("Область", "Region"))}</span>
      ${STRIP().map(([icon, label], i) => `<button type="button" class="qa-act qx-chip" data-qx="strip" data-n="${i}" title="${esc(label)} (Alt+${i + 1})">${icon}<span>${esc(label)}</span></button>`).join("")}
      <button type="button" class="qa-icon qx-btn" data-qx="strip-close" title="${esc(t("Скрыть действия", "Hide the actions"))}" aria-label="${esc(t("Скрыть действия", "Hide the actions"))}">${svg('<path d="M6 6l12 12M18 6 6 18"/>')}</button></div>`;
  }
  function runStrip(n) {
    const row = STRIP()[n];
    if (!row) return false;
    S.strip = null; render();
    S.copyNext = row[3] === "copy";

    Q.send(row[1] + " · " + t("область экрана", "screen region"), { page: false, prompt: row[2] });
    return true;
  }
  async function pickRegion() {
    if (!on("region")) { Q.toast(t("Область экрана выключена: Настройки → Быстрый доступ → Возможности → Буфер, сниппеты и стикеры", "Screen regions are off: Settings → Quick Ask → Features → Clipboard, snippets and stickers"), "err", 3600); return; }
    if (!Q.native) { Q.toast(t("Выделение области экрана работает в приложении MemuAI для Windows", "Screen regions work in the MemuAI Windows app"), "err", 3600); return; }
    if (S.busy) return;
    S.busy = true;
    try {
      const result = await api("/api/quick/features/region", {});
      attachRegion(result);
    } catch (e) { Q.toast(e.message, "err", 3600); }
    finally { S.busy = false; }
  }
  function attachRegion(result) {
    if (!result || result.cancelled || !result.data) return false;
    const label = result.label || `${result.width}×${result.height}`;
    if (!Q.addImage({ data: result.data, width: result.width, height: result.height, label: t("область", "region"), name: "region.jpg" })) return false;
    S.strip = { kind: "region", label };
    S.view = ""; S.inline = [];
    render();
    Q.setInput(Q.getInput());
    return true;
  }

  async function addDocument(file) {

    if (window.MemuQuickBigDocs && await window.MemuQuickBigDocs.take(file)) return;
    if (file.size > 15 * 1024 * 1024) { Q.toast(t("Файл больше 15 МБ — слишком большой", "The file is over 15 MB — too large"), "err", 3600); return; }
    Q.toast(t("Читаю «", "Reading “") + file.name + t("»…", "”…"), "", 6000);
    const data = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(String(r.result || "")); r.onerror = () => resolve(""); r.readAsDataURL(file); });
    if (!data) { Q.toast(t("Не удалось прочитать файл", "Could not read the file"), "err"); return; }
    let doc;
    try { doc = await api("/api/quick/features/document", { name: file.name, data }); }
    catch (e) { Q.toast(e.message, "err", 5000); return; }
    Q.addContext(doc.text, { name: doc.name });
    const size = Number(doc.chars || 0).toLocaleString(Q.lang() === "en" ? "en-US" : "ru-RU");
    Q.toast(doc.truncated ? t(`«${doc.name}»: ${size} знаков — длинный, модель увидит начало и конец`, `“${doc.name}”: ${size} characters — long, the model sees the beginning and the end`)
      : t(`Документ добавлен к вопросу: ${doc.name}`, `Document attached: ${doc.name}`), "", 3600);
  }

  const isDocument = (file) => !!file && !/^image\//.test(file.type || "")
    && (DOC_EXT.test(file.name || "") || /pdf|wordprocessingml/.test(file.type || "") || file.size > 400 * 1024);
  Q.on("file", (e) => {
    if (!on("documents") || !isDocument(e.file)) return;
    e.preventDefault();
    addDocument(e.file);
  });
  Q.on("escape", (e) => {
    if (S.view) { e.preventDefault(); S.view === "snippet-edit" ? openView("snippets") : closeView(); return; }
    if (S.inline.length) { e.preventDefault(); S.inline = []; render(); return; }
  });

  Q.on("answer-start", () => {
    if (S.view && S.view !== "snippet-edit") { S.view = ""; S.items = []; }
    if (S.strip) S.strip = null;
    render();
  });
  Q.on("chat-new", () => { S.strip = null; S.inline = []; if (S.view) S.view = ""; render(); });
  Q.on("close", () => { S.inline = []; if (S.view === "snippet-edit") S.view = ""; render(); });
  Q.on("open", () => { loadConfig().then(() => { if (S.view === "clipboard") loadClipboard(); }); });
  Q.on("answer-done", (e) => {
    if (!S.copyNext) return;
    S.copyNext = false;
    if (e && !e.error && String(e.text || "").trim()) copyText(String(e.text).trim()).then((ok) => { if (ok) Q.toast(t("Распознанный текст скопирован", "The extracted text is copied")); });
  });

  const input = document.getElementById("qa-input");
  if (input) {
    input.addEventListener("input", updateInline);

    document.addEventListener("keydown", (e) => {
      if (e.target !== input || !S.inline.length || S.view || e.isComposing) return;
      const s = S.inline[S.inlineIndex];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { S.inlineIndex = (S.inlineIndex + (e.key === "ArrowDown" ? 1 : -1) + S.inline.length) % S.inline.length; render(); }
      else if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.altKey) useInline(S.inlineIndex);
      else if (e.key === "Tab" && !e.shiftKey && s && s.kind === "text") snippetToInput(s);
      else return;
      e.preventDefault(); e.stopImmediatePropagation();
    }, true);

    input.addEventListener("paste", (e) => {
      const docs = Array.from((e.clipboardData && e.clipboardData.files) || []).filter(isDocument);
      if (!docs.length || !on("documents")) return;
      e.preventDefault();
      docs.forEach(addDocument);
    });
  }
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || S.view) return;
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.shiftKey && !e.altKey && e.code === "KeyX") { e.preventDefault(); pickRegion(); return; }
    if (e.altKey && !mod && !e.shiftKey && e.code === "KeyV") { e.preventDefault(); openView("clipboard"); return; }
    if (mod && e.shiftKey && !e.altKey && e.code === "KeyP") {
      e.preventDefault();
      const last = (Q.state().turns || []).filter((x) => x.done && x.a).pop();
      pinSticker(last);
      return;
    }
    if (S.strip && e.altKey && !mod && /^Digit[1-4]$/.test(e.code)) { if (runStrip(+e.code.slice(5) - 1)) e.preventDefault(); }
  });

  const attach = document.getElementById("qa-attach");
  if (attach && window.MutationObserver) new MutationObserver(() => { if (S.strip && !Q.state().images) { S.strip = null; render(); } }).observe(attach, { childList: true });
  const shot = document.getElementById("qa-shot");
  if (shot) shot.addEventListener("contextmenu", (e) => { e.preventDefault(); pickRegion(); });

  const SEC = () => t("Буфер, сниппеты и стикеры", "Clipboard, snippets and stickers");
  Q.addAction({ id: "region", section: SEC, icon: IC.region, label: () => t("Спросить про область экрана", "Ask about a screen region"), keys: ["Ctrl", "Shift", "X"], sub: () => t("или правый щелчок по камере", "or right-click the camera"), hidden: () => !on("region"), run: pickRegion });
  Q.addAction({ id: "clipboard", section: SEC, icon: IC.clip, label: () => t("История буфера обмена", "Clipboard history"), keys: ["Alt", "V"], run: () => openView("clipboard") });
  Q.addAction({ id: "snippets", section: SEC, icon: IC.snip, label: () => t("Сниппеты", "Snippets"), keys: [";"], sub: () => t("заготовки и ИИ-шаблоны", "texts and AI templates"), hidden: () => !on("snippets"), run: () => openView("snippets") });
  Q.addAction({ id: "stickers", section: SEC, icon: IC.sticker, label: () => t("Стикеры", "Stickers"), hidden: () => !on("stickers"), run: () => openView("stickers") });
  Q.addAction({ id: "sticker-pin", section: SEC, icon: IC.pin, label: () => t("Закрепить ответ стикером", "Pin the answer as a sticker"), keys: ["Ctrl", "Shift", "P"],
    hidden: () => !on("stickers") || !(Q.state().turns || []).some((x) => x.done && x.a), run: () => pinSticker((Q.state().turns || []).filter((x) => x.done && x.a).pop()) });
  Q.addAction({ id: "document", section: SEC, icon: IC.doc, label: () => t("Документ PDF, DOCX или текст…", "A PDF, DOCX or text document…"), sub: () => t("или перетащите в окно", "or drop it into the window"), hidden: () => !on("documents"),
    run: () => { const el = document.getElementById("qa-file-input"); if (el) el.click(); } });
  Q.addAnswerAction({ id: "sticker", icon: IC.sticker, label: () => t("Стикер", "Sticker"), title: () => t("Закрепить ответ стикером поверх окон (Ctrl+Shift+P)", "Pin the answer as an always-on-top sticker (Ctrl+Shift+P)"),
    hidden: () => !on("stickers"), run: (turn) => pinSticker(turn) });

  window.QuickFeatures = {
    open: (view) => { if (["clipboard", "snippets", "stickers"].includes(view)) openView(view); return true; },
    close: () => { closeView(); S.inline = []; S.strip = null; render(); return true; },
    pickRegion, attachRegion,
    regionReady: async () => { try { attachRegion(await api("/api/quick/features/region/result")); } catch (e) {  } return true; },
    state: () => S, reload: loadConfig,
  };
  loadConfig().then(() => Q.resize());
})();
