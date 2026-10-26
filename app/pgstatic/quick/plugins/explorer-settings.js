

(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M8 13h8"/></svg>';
  const CURRENCIES = ["", "RUB", "USD", "EUR", "GBP", "CNY", "KZT", "UAH", "BYN", "TRY", "AED", "AMD", "GEL", "UZS", "KGS", "JPY", "INR", "CHF", "PLN"];

  MemuQuick.settingsSection("explorer", (body, ctx) => {
    const en = !!ctx.en;
    const tr = (ru, e) => (en ? e : ru);
    const esc = ctx.esc || ((v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = window.MemuSettingsKit || null;
    const state = { data: null, editing: null };
    const post = (path, payload) => ctx.api(path, { method: "POST", body: { lang: en ? "en" : "ru", ...payload } });
    const row = (o) => K ? K.row(o) : `<div class="qe-set-row"><div><b>${o.label}</b>${o.desc ? `<div class="td">${o.desc}</div>` : ""}</div><div>${o.control || ""}</div></div>`;
    const section = (o) => K ? K.section(o) : `<section><h3>${o.title}</h3>${o.desc ? `<p>${o.desc}</p>` : ""}${o.body}</section>`;
    const toggle = (id, on) => K ? K.toggle(id, on) : `<input type="checkbox" id="${id}" ${on ? "checked" : ""}>`;
    const input = (id, value, o = {}) => K ? K.input(id, value, o) : `<input id="${id}" value="${esc(value)}" placeholder="${esc(o.placeholder || "")}">`;
    const select = (id, options, value, width = 170) => K ? K.select(id, options, value, { width }) : `<select id="${id}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    const note = (text, tone) => K ? K.note(text, tone) : `<p>${text}</p>`;

    function html() {
      const d = state.data, s = d.settings;
      const nativeNote = d.native ? "" : note(tr("Папка Проводника, файл редактора, документы Office, Windows OCR и пипетка работают в приложении MemuAI для Windows. Здесь они только настраиваются.",
        "The Explorer folder, the editor file, Office documents, Windows OCR and the eyedropper work in the MemuAI Windows app. Here they are only configured."), "info");
      const explorer = section({ id: "qe-set-explorer", title: tr("Проводник и папки", "Explorer and folders"), keywords: "explorer проводник папка файлы folder files rename переименовать дубликаты",
        desc: tr("Alt+Space над окном Проводника: быстрый доступ видит открытую папку и выбранные файлы — читает, ищет, переименовывает, раскладывает по папкам и правит текст.",
          "Alt+Space over a File Explorer window: Quick Ask sees the open folder and the selected files — reads, searches, renames, sorts into folders and edits text."),
        body: row({ label: tr("Папка из Проводника и рабочего стола", "The folder from Explorer and the desktop"), control: toggle("qe-set-explorer-on", s.explorer) })
          + row({ label: tr("Файловые задачи в любом окне", "File tasks from any window"), keywords: "files desktop downloads рабочий стол загрузки создать папку",
            desc: tr("«Создай папку на рабочем столе», «перенеси отчёт из Загрузок в Документы», «открой последний счёт» — быстрый доступ делает это сам, в ваших папках (системные папки и папки MemuAI недоступны).",
              "“Create a folder on the desktop”, “move the report from Downloads to Documents”, “open the latest invoice” — Quick Ask does it itself, in your folders (system and MemuAI folders are off limits)."),
            control: toggle("qe-set-files", s.files) })
          + row({ label: tr("Удалять в Корзину", "Delete to the Recycle Bin"), keywords: "trash корзина удаление recycle",
            desc: tr("Удалённое можно вернуть кнопкой «Отменить» или из Корзины. Выключено — копия хранится в MemuAI (до 512 МБ).",
              "Deleted items can be restored with “Undo” or from the Recycle Bin. Off — MemuAI keeps a copy (up to 512 MB)."),
            control: toggle("qe-set-trash", s.trash) })
          + row({ label: tr("Доступ к файлам", "File access"), keywords: "access доступ ask full подтверждение",
            desc: tr("«С подтверждением» — каждое изменение сначала показывается (diff, таблица переименований). «Полный» — новые файлы, правки и переименования применяются сразу. Удаление и перезапись файла целиком — всегда по кнопке; последнее изменение можно отменить.",
              "“Ask first” — every change is previewed (diff, rename table). “Full” — new files, edits and renames are applied at once. Deleting and overwriting a whole file always wait for a click; the last change can be undone."),
            control: select("qe-set-access", [["ask", tr("С подтверждением", "Ask first")], ["full", tr("Полный", "Full")]], s.access) })
          + row({ label: tr("Скрытые и системные файлы", "Hidden and system files"), desc: tr("Показывать их модели в списках и поиске.", "Show them to the model in listings and search."), control: toggle("qe-set-hidden", s.hidden) })
          + historyHtml(d.history || []) });
      const apps = section({ id: "qe-set-apps", title: tr("Другие программы", "Other programs"), keywords: "vs code notepad++ word excel powerpoint terminal редактор терминал office",
        desc: tr("VS Code, Cursor, Notepad++, Sublime, Блокнот, JetBrains — открытый файл и папка проекта; Word, Excel, PowerPoint — открытый документ (только чтение); терминалы — их папка.",
          "VS Code, Cursor, Notepad++, Sublime, Notepad, JetBrains — the open file and the project folder; Word, Excel, PowerPoint — the open document (read only); terminals — their folder."),
        body: row({ label: tr("Редакторы и терминалы", "Editors and terminals"), control: toggle("qe-set-apps-on", s.apps) })
          + row({ label: tr("Документы Office", "Office documents"), desc: tr("Текст документа, лист Excel или слайды читаются только по вашему вопросу.", "The document text, the Excel sheet or the slides are read only when you ask."), control: toggle("qe-set-office", s.office) }) });
      const presets = section({ id: "qe-set-presets", title: tr("Подсказки для программ", "Per-app prompts"), keywords: "presets prompts пресеты подсказки outlook excel",
        desc: tr("Кнопки, которые появляются в окне, когда оно открыто над нужной программой (пусто — над любой).", "Buttons shown in the window when it is opened over the given program (empty — over any)."),
        body: row({ label: tr("Подсказки для программ", "Per-app prompts"), control: toggle("qe-set-presets-on", s.presets) })
          + `<div class="qe-set-list">${(d.presets || []).map((p) => `<div class="qe-set-item"><span class="qe-set-name"><b>${esc(p.name)}</b><small>${esc((p.apps.length ? p.apps.join(", ") : tr("любая программа", "any program")) + " · " + p.prompt.replace(/\s+/g, " ").slice(0, 80))}</small></span>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qe-edit="${esc(p.id)}">${esc(tr("Изменить", "Edit"))}</button>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qe-del="${esc(p.id)}">${esc(tr("Удалить", "Delete"))}</button></div>`).join("") || `<div class="qe-set-empty">${esc(tr("Подсказок нет.", "No prompts."))}</div>`}</div>`
          + (state.editing ? editorHtml() : `<div class="qe-set-actions"><button type="button" class="pbtn ghost sx-btn-sm" id="qe-set-preset-reset">${esc(tr("Вернуть стандартные", "Restore defaults"))}</button><button type="button" class="pbtn sx-btn-sm" id="qe-set-preset-add">${esc(tr("Добавить", "Add"))}</button></div>`) });
      const tools = section({ id: "qe-set-tools", title: tr("Инструменты окна", "Window tools"), keywords: "currency валюта курс eyedropper пипетка цвет ocr распознать заметки notes загрузки downloads ошибка error",
        desc: tr("Работают без модели (кроме объяснения ошибки). Всё — в Ctrl+K → «Инструменты».", "They work without a model (except explaining an error). All in Ctrl+K → “Tools”."),
        body: row({ label: tr("Валюты под полем ввода", "Currency under the input"), desc: tr("«100 usd в rub», «€50 to $», «курс доллара». Курсы open.er-api.com, обновляются раз в 6 часов.", "“100 usd to eur”, “€50 to $”, “exchange rate usd”. Rates from open.er-api.com, refreshed every 6 hours."), control: toggle("qe-set-currency", s.currency) })
          + row({ label: tr("Валюта по умолчанию", "Default currency"), desc: tr("Во что переводить, если валюта не названа.", "What to convert to when no currency is named."),
            control: select("qe-set-currency-to", CURRENCIES.map((c) => [c, c || tr("Авто (RUB / USD)", "Auto (RUB / USD)")]), s.currency_to, 150) })
          + row({ label: tr("Пипетка", "Eyedropper"), desc: tr("Цвет под указателем при Alt+Space и выбор цвета на экране: HEX, RGB, HSL, CMYK.", "The colour under the pointer at Alt+Space and picking a colour on screen: HEX, RGB, HSL, CMYK."), control: toggle("qe-set-color", s.color) })
          + row({ label: tr("Текст с экрана без ИИ", "Text from the screen without AI"), desc: tr("Область экрана → встроенное распознавание Windows (языки — из Параметры → Время и язык).", "A screen region → Windows' built-in OCR (languages from Settings → Time & language)."), control: toggle("qe-set-ocr", s.ocr) })
          + row({ label: tr("Объяснить ошибку", "Explain the error"), desc: tr("Снимок экрана или выделенный текст ошибки → причина и исправление. Нужна модель с изображениями для снимка.", "A screenshot or the selected error text → cause and fix. A model with images is needed for the screenshot."), control: toggle("qe-set-error", s.error) })
          + row({ label: tr("Заметки в MemuNote", "Notes to MemuNote"), desc: tr("Ctrl+K → «Заметка в MemuNote», «В заметку» под ответом или после распознавания текста: строка с датой и временем в Markdown-файле, который открывается в MemuNote (если он установлен). Фраза «заметка: …» по-прежнему пишет в чат «Заметки».", "Ctrl+K → “Note to MemuNote”, “To note” under an answer or after OCR: a dated line in a Markdown file opened in MemuNote (when installed). The phrase “note: …” still writes into the “Notes” chat."), control: toggle("qe-set-notes", s.notes) })
          + row({ label: tr("Папка заметок", "Notes folder"), desc: tr("Пусто — Документы\\MemuAI Notes.", "Empty — Documents\\MemuAI Notes."), control: input("qe-set-notes-dir", s.notes_dir, { placeholder: "C:\\Users\\…\\Notes", width: 240 }) })
          + row({ label: tr("Последние загрузки", "Latest downloads"), desc: tr("Свежий файл из «Загрузок» — контекстом вопроса в один щелчок.", "A fresh file from Downloads as the question's context in one click."), control: toggle("qe-set-downloads", s.downloads) }) });
      return `<div class="qe-settings">${nativeNote}${explorer}${apps}${presets}${tools}</div>`;
    }
    function historyHtml(items) {
      if (!items.length) return "";
      const fmt = (ts) => { try { return new Date(ts * 1000).toLocaleString(en ? "en-US" : "ru-RU", { dateStyle: "short", timeStyle: "short" }); } catch (e) { return ""; } };
      return row({ label: tr("Недавние изменения", "Recent changes"), desc: tr("Изменения файлов из быстрого доступа (хранятся последние 12, с резервными копиями).", "File changes made from Quick Ask (the last 12 are kept, with backups).") })
        + `<div class="qe-set-list">${items.map((h) => `<div class="qe-set-item"><span class="qe-set-name"><b>${esc(h.summary || tr("Изменения", "Changes"))}</b><small>${esc(`${h.folder_name || ""} · ${fmt(h.t)} · ${h.count}` + (h.undone ? tr(" · отменено", " · undone") : ""))}</small></span>
          ${h.undone ? "" : `<button type="button" class="pbtn ghost sx-btn-sm" data-qe-undo="${esc(h.id)}">${esc(tr("Отменить", "Undo"))}</button>`}</div>`).join("")}</div>`;
    }
    function editorHtml() {
      const p = state.editing;
      return `<div class="qe-set-editor"><div class="qe-set-grid"><label>${esc(tr("Название кнопки", "Button name"))}${input("qe-set-ed-name", p.name || "", { placeholder: tr("Ответ на письмо", "Reply to the email") })}</label>
        <label>${esc(tr("Программы (через запятую)", "Programs (comma-separated)"))}${input("qe-set-ed-apps", (p.apps || []).join(", "), { placeholder: "outlook.exe, excel.exe" })}</label></div>
        <label>${esc(tr("Что сделать (инструкция модели; выделенный текст и контекст программы добавятся сами)", "What to do (an instruction for the model; the selected text and the program context are added automatically)"))}<textarea class="ptexta sx-textarea" id="qe-set-ed-prompt" rows="3">${esc(p.prompt || "")}</textarea></label>
        <div class="qe-set-actions"><span class="qe-set-err" id="qe-set-ed-err"></span><button type="button" class="pbtn ghost sx-btn-sm" id="qe-set-ed-cancel">${esc(tr("Отмена", "Cancel"))}</button><button type="button" class="pbtn sx-btn-sm" id="qe-set-ed-save">${esc(tr("Сохранить", "Save"))}</button></div></div>`;
    }
    async function load() {
      try { state.data = await ctx.api("/api/quick/apps/settings?lang=" + (en ? "en" : "ru")); }
      catch (e) { body.innerHTML = note(esc(tr("Не удалось загрузить настройки: ", "Could not load the settings: ") + e.message), "warn"); return; }
      draw();
    }
    function draw() {
      const scroller = body.closest(".settings-body");
      const top = scroller ? scroller.scrollTop : 0;
      body.innerHTML = html();
      if (scroller) scroller.scrollTop = top;
      bind();
    }
    async function save(patch) {
      try { const data = await post("/api/quick/apps/settings", { settings: patch }); state.data.settings = data.settings; draw(); }
      catch (e) { ctx.toast(e.message, "err"); }
    }
    async function savePresets(list, reset) {
      const data = await post("/api/quick/apps/presets", reset ? { reset: true } : { presets: list });
      state.data.presets = data.presets;
    }
    function bind() {
      const $ = (id) => body.querySelector("#" + id);
      const toggles = { "qe-set-explorer-on": "explorer", "qe-set-files": "files", "qe-set-trash": "trash", "qe-set-hidden": "hidden", "qe-set-apps-on": "apps", "qe-set-office": "office", "qe-set-presets-on": "presets",
        "qe-set-currency": "currency", "qe-set-color": "color", "qe-set-ocr": "ocr", "qe-set-error": "error", "qe-set-notes": "notes", "qe-set-downloads": "downloads" };
      for (const [id, key] of Object.entries(toggles)) { const el = $(id); if (el) el.onchange = () => save({ [key]: el.checked }); }
      const access = $("qe-set-access"); if (access) access.onchange = () => save({ access: access.value });
      const cur = $("qe-set-currency-to"); if (cur) cur.onchange = () => save({ currency_to: cur.value });
      const dir = $("qe-set-notes-dir"); if (dir) dir.onchange = () => save({ notes_dir: dir.value.trim() });
      body.querySelectorAll("[data-qe-undo]").forEach((b) => { b.onclick = async () => {
        try { const r = await post("/api/quick/explorer/undo", { undo: b.dataset.qeUndo }); ctx.toast(r.problems && r.problems.length ? r.problems[0] : tr("Изменения отменены", "Changes undone"), r.problems && r.problems.length ? "err" : "ok"); }
        catch (e) { ctx.toast(e.message, "err"); }
        load();
      }; });
      const add = $("qe-set-preset-add"); if (add) add.onclick = () => { state.editing = { apps: [] }; draw(); const n = $("qe-set-ed-name"); if (n) n.focus(); };
      const reset = $("qe-set-preset-reset"); if (reset) reset.onclick = async () => {
        if (reset.dataset.confirm !== "1") { reset.dataset.confirm = "1"; reset.textContent = tr("Нажмите ещё раз — заменить", "Click again to replace"); return; }
        try { await savePresets(null, true); ctx.toast(tr("Стандартные подсказки возвращены", "Default prompts restored"), "ok"); draw(); } catch (e) { ctx.toast(e.message, "err"); }
      };
      body.querySelectorAll("[data-qe-edit]").forEach((b) => { b.onclick = () => { state.editing = { ...(state.data.presets.find((p) => p.id === b.dataset.qeEdit) || {}) }; draw(); }; });
      body.querySelectorAll("[data-qe-del]").forEach((b) => { b.onclick = async () => {
        try { await savePresets(state.data.presets.filter((p) => p.id !== b.dataset.qeDel)); ctx.toast(tr("Подсказка удалена", "Prompt deleted"), "ok"); draw(); } catch (e) { ctx.toast(e.message, "err"); }
      }; });
      const cancel = $("qe-set-ed-cancel"); if (cancel) cancel.onclick = () => { state.editing = null; draw(); };
      const saveBtn = $("qe-set-ed-save");
      if (saveBtn) saveBtn.onclick = async () => {
        const err = $("qe-set-ed-err");
        const p = { ...state.editing, name: $("qe-set-ed-name").value.trim(), apps: $("qe-set-ed-apps").value.split(/[,;\s]+/).filter(Boolean), prompt: $("qe-set-ed-prompt").value.trim() };
        if (!p.name) { err.textContent = tr("Нужно название", "A name is needed"); return; }
        if (!p.prompt) { err.textContent = tr("Нужна инструкция", "An instruction is needed"); return; }
        const list = p.id ? state.data.presets.map((x) => (x.id === p.id ? p : x)) : [...state.data.presets, p];
        try { await savePresets(list); state.editing = null; ctx.toast(tr("Подсказка сохранена", "Prompt saved"), "ok"); draw(); } catch (e) { err.textContent = e.message; }
      };
    }
    body.innerHTML = `<div class="qe-settings">${note(esc(tr("Загружаю…", "Loading…")), "info")}</div>`;
    load();
  }, { title: { ru: "Проводник, программы и инструменты", en: "Explorer, programs and tools" }, order: 32, icon: ICON,
    desc: { ru: "Папки и файлы, редакторы, Office, валюты, пипетка, OCR, заметки", en: "Folders and files, editors, Office, currency, eyedropper, OCR, notes" } });
})();
