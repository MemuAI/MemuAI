

(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><rect x="8.5" y="8.5" width="7" height="7" rx="1"/></svg>';
  const LIMITS = [50, 100, 200, 500, 1000];

  MemuQuick.settingsSection("features", (body, ctx) => {
    const en = !!ctx.en;
    const tr = (ru, e) => (en ? e : ru);
    const esc = ctx.esc || ((v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = window.MemuSettingsKit || null;
    const state = { data: null, editing: null };
    const post = (path, payload) => ctx.api(path, { method: "POST", body: { lang: en ? "en" : "ru", ...payload } });

    const row = (o) => K ? K.row(o) : `<div class="qx-set-row"><div><b>${o.label}</b>${o.desc ? `<div class="td">${o.desc}</div>` : ""}</div><div>${o.control || ""}</div></div>`;
    const section = (o) => K ? K.section(o) : `<section><h3>${o.title}</h3>${o.desc ? `<p>${o.desc}</p>` : ""}${o.body}</section>`;
    const toggle = (id, on) => K ? K.toggle(id, on) : `<input type="checkbox" id="${id}" ${on ? "checked" : ""}>`;
    const input = (id, value, o = {}) => K ? K.input(id, value, o) : `<input id="${id}" value="${esc(value)}" placeholder="${esc(o.placeholder || "")}">`;
    const select = (id, options, value) => K ? K.select(id, options, value, { width: 140 }) : `<select id="${id}">${options.map(([v, l]) => `<option value="${v}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    const note = (text, tone) => K ? K.note(text, tone) : `<p>${text}</p>`;
    const chip = (text, tone) => K ? K.chip(text, tone) : `<span>${esc(text)}</span>`;

    function hotkeyChip(status) {
      if (!status || status.code === "off" || !status.hotkey) return "";
      return status.ok ? chip(tr("Работает", "Active"), "ok") : chip(status.error || tr("Не зарегистрировано", "Not registered"), "warn");
    }
    function html() {
      const d = state.data, s = d.settings, st = d.status || {}, hk = st.hotkeys || {};
      const nativeNote = d.native ? "" : note(tr("Окно выделения, запись буфера обмена, плавающие стикеры и глобальные сочетания работают в приложении MemuAI для Windows. Здесь они только настраиваются.",
        "The region overlay, clipboard recording, floating stickers and global hotkeys work in the MemuAI Windows app. Here they are only configured."), "info");
      const region = section({ id: "qx-set-region", title: tr("Область экрана", "Screen region"), keywords: "region screen ocr область экрана распознать текст скриншот",
        desc: tr("Ctrl+Shift+X в окне или правый щелчок по камере: выделите прямоугольник — и распознайте текст, переведите, объясните или спросите о нём.",
          "Ctrl+Shift+X in the window or right-click the camera: drag a rectangle — then extract the text, translate, explain or ask about it."),
        body: row({ label: tr("Выделение области", "Region selection"), control: toggle("qx-set-region-on", s.region), desc: tr("Нужна модель, которая видит изображения (со «Зрением»).", "Needs a model that sees images (with Vision).") })
          + row({ label: tr("Сочетание из любой программы", "Hotkey from any program"), keywords: "hotkey сочетание клавиш",
            desc: tr("Например Alt+Shift+S. Пусто — только из окна быстрого доступа.", "E.g. Alt+Shift+S. Empty — only from the Quick Ask window.") + " " + hotkeyChip(hk.region),
            control: input("qx-set-region-hk", s.region_hotkey, { placeholder: "Alt+Shift+S", width: 160 }) }) });
      const clip = section({ id: "qx-set-clip", title: tr("История буфера обмена", "Clipboard history"), keywords: "clipboard буфер обмена история ditto win+v",
        desc: tr("Alt+V в окне: поиск по скопированному, вставка обратно в программу, «спросить ИИ». Только текст, только на этом компьютере.",
          "Alt+V in the window: search what you copied, paste it back, ask AI. Text only, only on this computer."),
        body: row({ label: tr("Записывать историю", "Record the history"), control: toggle("qx-set-clip-on", s.clipboard),
            desc: st.clipboard && st.clipboard.running ? chip(st.clipboard.paused ? tr("На паузе", "Paused") : tr("Записывается", "Recording"), st.clipboard.paused ? "warn" : "ok") : "" })
          + row({ label: tr("Пауза", "Pause"), control: toggle("qx-set-clip-pause", s.clipboard_paused), desc: tr("Временно ничего не записывать (например, на время работы с секретами).", "Record nothing for a while (e.g. while handling secrets).") })
          + row({ label: tr("Сколько хранить", "How many to keep"), control: select("qx-set-clip-limit", LIMITS.map((n) => [n, String(n)]), s.clipboard_limit), desc: tr("Закреплённые записи не вытесняются.", "Pinned items are never pushed out.") })
          + row({ label: tr("Пропускать похожее на пароли и ключи", "Skip password- and key-like text"), control: toggle("qx-set-clip-secrets", s.clipboard_skip_secrets),
            desc: tr("API-ключи, токены, закрытые ключи, строки «пароль: …» и одиночные пароли не записываются.", "API keys, tokens, private keys, “password: …” lines and lone passwords are not recorded.") })
          + row({ label: tr("Не записывать из программ", "Never record from apps"), keywords: "ignore exclude исключения программы",
            desc: tr("Через запятую, например: telegram.exe, slack.exe. Менеджеры паролей и всё, что программы помечают «не для истории», не записываются никогда.",
              "Comma-separated, e.g.: telegram.exe, slack.exe. Password managers and anything apps mark “not for history” are never recorded."),
            control: input("qx-set-clip-ignore", (s.clipboard_ignore || []).join(", "), { placeholder: "telegram.exe", width: 220 }) })
          + row({ label: tr("Сочетание из любой программы", "Hotkey from any program"), desc: tr("Например Ctrl+Alt+V.", "E.g. Ctrl+Alt+V.") + " " + hotkeyChip(hk.clipboard),
            control: input("qx-set-clip-hk", s.clipboard_hotkey, { placeholder: "Ctrl+Alt+V", width: 160 }) })
          + row({ label: tr("Очистить историю", "Clear the history"), desc: tr(`Записей: ${d.clipboard_count || 0}. Закреплённые тоже удаляются.`, `Items: ${d.clipboard_count || 0}. Pinned ones are deleted too.`),
            control: `<button type="button" class="pbtn ghost sx-btn-sm" id="qx-set-clip-clear">${esc(tr("Очистить всё", "Clear all"))}</button>` }) });
      const snippets = section({ id: "qx-set-snippets", title: tr("Сниппеты", "Snippets"), keywords: "snippets сниппеты шаблоны заготовки text expansion",
        desc: tr("Наберите «;» и ключевое слово в окне: текст вставится в программу (переменные {date}, {time}, {clipboard}, {cursor}…), ИИ-шаблон обработает набранный, выделенный или скопированный текст.",
          "Type “;” and a keyword in the window: text is pasted into the program (variables {date}, {time}, {clipboard}, {cursor}…), an AI template processes the typed, selected or copied text."),
        body: row({ label: tr("Сниппеты в окне", "Snippets in the window"), control: toggle("qx-set-snip-on", s.snippets) })
          + `<div class="qx-set-list">${(d.snippets || []).map((sn) => `<div class="qx-set-item" data-id="${esc(sn.id)}">
              <span class="qx-set-kw">${sn.keyword ? ";" + esc(sn.keyword) : "—"}</span><span class="qx-set-name"><b>${esc(sn.name)}</b><small>${sn.kind === "ai" ? esc(tr("ИИ-шаблон · ", "AI template · ")) : ""}${esc(sn.text.replace(/\s+/g, " ").slice(0, 90))}</small></span>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qx-edit="${esc(sn.id)}">${esc(tr("Изменить", "Edit"))}</button>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qx-del="${esc(sn.id)}">${esc(tr("Удалить", "Delete"))}</button></div>`).join("")
            || `<div class="qx-set-empty">${esc(tr("Сниппетов нет.", "No snippets."))}</div>`}</div>`
          + (state.editing ? editorHtml() : `<div class="qx-set-actions"><button type="button" class="pbtn sx-btn-sm" id="qx-set-snip-add">${esc(tr("Добавить сниппет", "Add a snippet"))}</button></div>`) });
      const stickers = section({ id: "qx-set-stickers", title: tr("Стикеры", "Stickers"), keywords: "stickers стикеры закрепить ответ поверх окон",
        desc: tr("Кнопка «Стикер» под ответом (Ctrl+Shift+P) закрепляет его маленьким окном поверх всех окон. Открытые стикеры возвращаются после перезапуска.",
          "The “Sticker” button under an answer (Ctrl+Shift+P) pins it as a small window on top of everything. Open stickers come back after a restart."),
        body: row({ label: tr("Стикеры", "Stickers"), control: toggle("qx-set-stick-on", s.stickers) })
          + `<div class="qx-set-list">${(d.stickers || []).map((sk) => `<div class="qx-set-item" data-id="${esc(sk.id)}"><span class="qx-set-name"><b>${esc(sk.title || tr("Стикер", "Sticker"))}</b><small>${sk.open ? esc(tr("на экране · ", "on screen · ")) : ""}${esc(sk.text.replace(/```[\w-]*|[*_`#>]+/g, "").replace(/\s+/g, " ").trim().slice(0, 90))}</small></span>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qx-stick="${esc(sk.id)}" data-open="${sk.open ? 1 : 0}">${esc(sk.open ? tr("Убрать с экрана", "Hide") : tr("Показать", "Show"))}</button>
              <button type="button" class="pbtn ghost sx-btn-sm" data-qx-stick-del="${esc(sk.id)}">${esc(tr("Удалить", "Delete"))}</button></div>`).join("")
            || `<div class="qx-set-empty">${esc(tr("Стикеров нет.", "No stickers."))}</div>`}</div>` });
      const docs = section({ id: "qx-set-docs", title: tr("Документы", "Documents"), keywords: "pdf docx документы файлы перетащить",
        body: row({ label: tr("PDF, DOCX и длинные тексты", "PDF, DOCX and long texts"), control: toggle("qx-set-docs-on", s.documents),
          desc: tr("Перетащите или вставьте файл в окно — его текст станет контекстом вопроса («перескажи», «какие сроки в договоре?»). До 15 МБ; сканы без текстового слоя — через «Область экрана».",
            "Drop or paste a file into the window — its text becomes the question's context (“summarize”, “what are the deadlines?”). Up to 15 MB; scans without a text layer — via “Screen region”.") }) });
      return `<div class="qx-settings">${nativeNote}${region}${clip}${snippets}${stickers}${docs}</div>`;
    }
    function editorHtml() {
      const sn = state.editing;
      return `<div class="qx-set-editor" id="qx-set-editor">
        <div class="qx-set-grid"><label>${esc(tr("Название", "Name"))}${input("qx-set-ed-name", sn.name || "", { placeholder: tr("Подпись", "Signature") })}</label>
          <label>${esc(tr("Ключевое слово (без «;»)", "Keyword (without “;”)"))}${input("qx-set-ed-kw", sn.keyword || "", { placeholder: "sig" })}</label>
          <label>${esc(tr("Вид", "Kind"))}${select("qx-set-ed-kind", [["text", tr("Текст", "Text")], ["ai", tr("ИИ-шаблон", "AI template")]], sn.kind || "text")}</label></div>
        <label>${esc(tr("Текст или инструкция модели", "Text or an instruction for the model"))}<textarea class="ptexta sx-textarea" id="qx-set-ed-text" rows="4">${esc(sn.text || "")}</textarea></label>
        <div class="qx-set-actions"><span class="qx-set-err" id="qx-set-ed-err"></span><button type="button" class="pbtn ghost sx-btn-sm" id="qx-set-ed-cancel">${esc(tr("Отмена", "Cancel"))}</button><button type="button" class="pbtn sx-btn-sm" id="qx-set-ed-save">${esc(tr("Сохранить", "Save"))}</button></div></div>`;
    }
    async function load() {
      try { state.data = await ctx.api("/api/quick/features?lang=" + (en ? "en" : "ru")); }
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
      try {
        const data = await post("/api/quick/features/settings", { settings: patch });
        state.data.settings = data.settings; state.data.status = data.status || state.data.status;
        const failed = Object.values((data.status || {}).hotkeys || {}).find((h) => h && h.ok === false && h.hotkey);
        if (failed) ctx.toast(failed.error || tr("Сочетание не зарегистрировано", "The hotkey is not registered"), "err");
        draw();
      } catch (e) { ctx.toast(e.message, "err"); }
    }
    async function saveSnippets(list) {
      const data = await post("/api/quick/features/snippets", { snippets: list });
      state.data.snippets = data.snippets;
    }
    function bind() {
      const $ = (id) => body.querySelector("#" + id);
      const toggles = { "qx-set-region-on": "region", "qx-set-clip-on": "clipboard", "qx-set-clip-pause": "clipboard_paused", "qx-set-clip-secrets": "clipboard_skip_secrets",
        "qx-set-snip-on": "snippets", "qx-set-stick-on": "stickers", "qx-set-docs-on": "documents" };
      for (const [id, key] of Object.entries(toggles)) { const el = $(id); if (el) el.onchange = () => save({ [key]: el.checked }); }
      const limit = $("qx-set-clip-limit");
      if (limit) limit.onchange = () => save({ clipboard_limit: +limit.value });
      const text = (id, key, transform = (v) => v) => { const el = $(id); if (el) el.onchange = () => save({ [key]: transform(el.value.trim()) }); };
      text("qx-set-region-hk", "region_hotkey"); text("qx-set-clip-hk", "clipboard_hotkey");
      text("qx-set-clip-ignore", "clipboard_ignore", (v) => v.split(/[,;\s]+/).filter(Boolean));
      const clear = $("qx-set-clip-clear");
      if (clear) clear.onclick = async () => {
        if (clear.dataset.confirm !== "1") { clear.dataset.confirm = "1"; clear.textContent = tr("Нажмите ещё раз — удалить всё", "Click again to delete everything"); return; }
        try { await post("/api/quick/features/clipboard", { action: "clear_all" }); ctx.toast(tr("История буфера обмена очищена", "Clipboard history cleared"), "ok"); state.data.clipboard_count = 0; draw(); }
        catch (e) { ctx.toast(e.message, "err"); }
      };
      const add = $("qx-set-snip-add");
      if (add) add.onclick = () => { state.editing = { kind: "text" }; draw(); const n = $("qx-set-ed-name"); if (n) n.focus(); };
      body.querySelectorAll("[data-qx-edit]").forEach((b) => { b.onclick = () => { state.editing = { ...(state.data.snippets.find((s) => s.id === b.dataset.qxEdit) || {}) }; draw(); }; });
      body.querySelectorAll("[data-qx-del]").forEach((b) => { b.onclick = async () => {
        try { await saveSnippets(state.data.snippets.filter((s) => s.id !== b.dataset.qxDel)); ctx.toast(tr("Сниппет удалён", "Snippet deleted"), "ok"); draw(); } catch (e) { ctx.toast(e.message, "err"); }
      }; });
      const cancel = $("qx-set-ed-cancel");
      if (cancel) cancel.onclick = () => { state.editing = null; draw(); };
      const saveBtn = $("qx-set-ed-save");
      if (saveBtn) saveBtn.onclick = async () => {
        const err = $("qx-set-ed-err");
        const row = { ...state.editing, name: $("qx-set-ed-name").value.trim(), keyword: $("qx-set-ed-kw").value.replace(/^;+/, "").replace(/\s+/g, "").toLowerCase(),
          kind: $("qx-set-ed-kind").value === "ai" ? "ai" : "text", text: $("qx-set-ed-text").value };
        if (!row.text.trim()) { err.textContent = tr("Напишите текст", "Write the text"); return; }
        if (!row.name && !row.keyword) { err.textContent = tr("Нужно название или ключевое слово", "A name or a keyword is needed"); return; }
        const clash = row.keyword && state.data.snippets.find((s) => s.keyword === row.keyword && s.id !== row.id);
        if (clash) { err.textContent = tr(`;${row.keyword} уже занято`, `;${row.keyword} is taken`); return; }
        const list = row.id ? state.data.snippets.map((s) => (s.id === row.id ? row : s)) : [...state.data.snippets, row];
        try { await saveSnippets(list); state.editing = null; ctx.toast(tr("Сниппет сохранён", "Snippet saved"), "ok"); draw(); } catch (e) { err.textContent = e.message; }
      };
      body.querySelectorAll("[data-qx-stick]").forEach((b) => { b.onclick = async () => {
        try { await post("/api/quick/features/stickers", { action: b.dataset.open === "1" ? "close" : "open", id: b.dataset.qxStick }); } catch (e) { ctx.toast(e.message, "err"); }
        load();
      }; });
      body.querySelectorAll("[data-qx-stick-del]").forEach((b) => { b.onclick = async () => {
        try { await post("/api/quick/features/stickers", { action: "delete", id: b.dataset.qxStickDel }); } catch (e) { ctx.toast(e.message, "err"); }
        load();
      }; });
    }
    body.innerHTML = `<div class="qx-settings">${note(esc(tr("Загружаю…", "Loading…")), "info")}</div>`;
    load();
  }, { title: { ru: "Буфер, сниппеты и стикеры", en: "Clipboard, snippets and stickers" }, order: 30, icon: ICON,
    desc: { ru: "Область экрана, буфер обмена, сниппеты, стикеры, документы", en: "Screen region, clipboard, snippets, stickers, documents" } });
})();
