

(function () {
  "use strict";
  const BUILTIN = [
    ["translate", "Перевести", "Translate", "На язык интерфейса; если текст уже на нём — на второй язык", "Into the interface language; text already in it — into the second language"],
    ["explain", "Объяснить", "Explain", "Простыми словами: суть, термины, подтекст", "In simple words: the gist, the terms, the subtext"],
    ["fix", "Исправить", "Fix", "Ошибки, опечатки и неуклюжие фразы; можно заменить выделение", "Mistakes, typos and clumsy phrases; can replace the selection"],
    ["summarize", "Кратко", "Summarize", "Главное в нескольких предложениях", "The main points in a few sentences"],
    ["reply", "Ответить", "Reply", "Готовый ответ на письмо или сообщение", "A ready reply to a letter or message"],
    ["ask", "Спросить", "Ask", "Курсор в поле — спросите что угодно о тексте", "The cursor goes to the field — ask anything about the text"],
  ];
  const DEFAULT_ORDER = BUILTIN.map((b) => b[0]);
  const LANGS = [["en", "Английский", "English"], ["ru", "Русский", "Russian"], ["de", "Немецкий", "German"], ["fr", "Французский", "French"],
    ["es", "Испанский", "Spanish"], ["it", "Итальянский", "Italian"], ["pt", "Португальский", "Portuguese"], ["uk", "Украинский", "Ukrainian"],
    ["pl", "Польский", "Polish"], ["tr", "Турецкий", "Turkish"], ["zh", "Китайский", "Chinese"], ["ja", "Японский", "Japanese"],
    ["ko", "Корейский", "Korean"], ["ar", "Арабский", "Arabic"]];
  const MAX_CUSTOM = 12;
  const SVG = {
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 14 6-6 6 6"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 10 6 6 6-6"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
    del: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>',
  };

  function mount(el, ctx = {}) {
    const en = !!ctx.en;
    const t = (ru, enText) => (en ? enText : ru);
    const esc = ctx.esc || ((s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = ctx.kit || window.MemuSettingsKit;
    const pref = (key, def) => (typeof ctx.pref === "function" ? ctx.pref(key, def) : def);
    const toast = (text, kind) => (typeof ctx.toast === "function" ? ctx.toast(text, kind) : console.log(text));
    const list0 = (key) => (Array.isArray(pref(key, null)) ? pref(key, null) : null);
    const S = {
      enabled: pref("quick_ask_selection", true) !== false,
      clipboard: pref("quick_ask_selection_clipboard", true) !== false,
      second: String(pref("quick_ask_selection_second_lang", "") || ""),
      order: (list0("quick_ask_selection_actions") || DEFAULT_ORDER).slice(),
      custom: (list0("quick_ask_selection_custom") || []).map((c) => ({ ...c })),
      editing: "",
      status: null,
    };
    if (!K) { el.textContent = t("Набор настроек не загружен.", "The settings kit is not loaded."); return { unmount() { el.innerHTML = ""; } }; }
    el.classList.add("qss-root");

    async function save(patch) {
      try {
        if (typeof ctx.syncPreference === "function") for (const [k, v] of Object.entries(patch)) ctx.syncPreference(k, v);
        await ctx.savePreferences(patch);
      } catch (e) { toast(e.message || String(e), "err"); }
    }
    const newId = () => "c_" + Math.random().toString(36).slice(2, 10).padEnd(8, "0");
    const builtin = (id) => BUILTIN.find((x) => x[0] === id);
    const nameOf = (id) => {
      const b = builtin(id);
      if (b) return t(b[1], b[2]);
      const c = S.custom.find((x) => x.id === id);
      return c ? c.name : id;
    };

    function rows() {
      const all = [...DEFAULT_ORDER, ...S.custom.map((c) => c.id)];
      const on = S.order.filter((id) => all.includes(id));
      return [...on.map((id) => ({ id, on: true })), ...all.filter((id) => !on.includes(id)).map((id) => ({ id, on: false }))];
    }

    function itemHtml(row) {
      const pos = row.on ? S.order.indexOf(row.id) : -1;
      const custom = S.custom.find((c) => c.id === row.id);
      const b = builtin(row.id);
      const desc = custom ? custom.prompt : (b ? t(b[3], b[4]) : "");
      const tool = (attr, icon, label, disabled = false, cls = "") => `<button type="button" class="qss-ic ${cls}" ${attr}="${esc(row.id)}" ${disabled ? "disabled" : ""} title="${esc(label)}" aria-label="${esc(label + ": " + nameOf(row.id))}">${icon}</button>`;
      return `<div class="qss-item${row.on ? "" : " off"}" role="listitem" data-id="${esc(row.id)}">
        <span class="qss-num" aria-hidden="true">${row.on && pos < 9 ? pos + 1 : "–"}</span>
        <span class="qss-text"><b>${esc(nameOf(row.id))}</b>${custom ? `<i>${esc(t("своё", "own"))}</i>` : ""}<small title="${esc(desc)}">${esc(desc)}</small></span>
        <span class="qss-tools">
          ${tool("data-up", SVG.up, t("Выше", "Up"), !row.on || pos === 0)}
          ${tool("data-down", SVG.down, t("Ниже", "Down"), !row.on || pos === S.order.length - 1)}
          ${custom ? tool("data-edit", SVG.edit, t("Изменить", "Edit")) + tool("data-del", SVG.del, t("Удалить", "Delete"), false, "danger") : ""}
          ${K.toggle("qss-act-" + row.id, row.on, `data-act="${esc(row.id)}" aria-label="${esc(t("Показывать", "Show") + ": " + nameOf(row.id))}"`)}
        </span></div>`;
    }

    function render() {
      const status = S.status && S.status.available === false
        ? K.note(t("Выделение читается только в приложении MemuAI для Windows — здесь кнопки можно настроить заранее.", "The selection is read only in the MemuAI Windows app — you can set the buttons up here in advance."), "info") : "";
      const general = K.section({ id: "qss-general", title: t("Действия с выделенным текстом", "Actions on selected text"), body:
          K.row({ label: t("Предлагать действия с выделенным", "Offer actions on the selection"), keywords: "selection выделение выделенный текст перевести исправить",
            desc: t("Выделите текст в любой программе и нажмите сочетание быстрого доступа — окно сразу покажет текст и кнопки: перевести, объяснить, исправить…",
              "Select text in any program and press the Quick Ask hotkey — the window shows the text and buttons right away: translate, explain, fix…"),
            control: K.toggle("qss-on", S.enabled), key: "quick_ask_selection", def: true })
        + K.row({ label: t("Копировать через Ctrl+C, если нужно", "Copy with Ctrl+C when needed"), when: "qss-on", keywords: "clipboard буфер обмена ctrl c копировать",
            desc: t("Обычно выделение читается напрямую, без буфера обмена. Если программа его не отдаёт, MemuAI нажмёт Ctrl+C и сразу вернёт ваш буфер обмена как был. В терминалах, удалённых рабочих столах и редакторах кода со встроенным терминалом Ctrl+C не нажимается — там он прерывает программу.",
              "Usually the selection is read directly, without the clipboard. If a program does not expose it, MemuAI presses Ctrl+C and puts your clipboard back right away. Terminals, remote desktops and code editors with a built-in terminal never get Ctrl+C — there it stops the running program."),
            control: K.toggle("qss-clip", S.clipboard), key: "quick_ask_selection_clipboard", def: true })
        + K.row({ label: t("Второй язык перевода", "Second translation language"), when: "qss-on", keywords: "translate перевод язык language",
            desc: t("«Перевести» переводит на язык интерфейса, а текст, который уже на нём, — на этот язык.", "«Translate» goes into the interface language; text that is already in it goes into this language."),
            control: K.select("qss-second", [["", t("Авто — английский", "Auto — Russian")], ...LANGS.filter((l) => l[0] !== (en ? "en" : "ru")).map((l) => [l[0], t(l[1], l[2])])], S.second, { width: 220 }),
            key: "quick_ask_selection_second_lang", def: "" })
        + status });
      const buttons = K.section({ id: "qss-buttons", title: t("Кнопки и порядок", "Buttons and order"), when: "qss-on",
        desc: t("Номер кнопки — клавиша в окне (1–9). Выключенные кнопки не показываются.", "A button's number is its key in the window (1–9). Switched-off buttons are hidden."),
        body: `<div class="qss-list" role="list">${rows().map(itemHtml).join("")}</div>`
          + K.actions(`<button type="button" class="pbtn ghost sx-btn-sm" id="qss-reset-order">${esc(t("Вернуть как было", "Restore defaults"))}</button>`) });
      const editing = S.custom.find((c) => c.id === S.editing) || null;
      const full = !editing && S.custom.length >= MAX_CUSTOM;
      const own = K.section({ id: "qss-own", title: editing ? t("Изменить своё действие", "Edit your action") : t("Своё действие", "Your own action"), when: "qss-on",
        desc: t("Напишите, что сделать с текстом; <code>{text}</code> — место, куда подставится выделенное (без него текст просто прикладывается к просьбе).",
          "Write what to do with the text; <code>{text}</code> is where the selection goes (without it the text is simply attached to the request)."),
        body: full ? K.note(t(`Не больше ${MAX_CUSTOM} своих действий — сначала удалите лишнее.`, `At most ${MAX_CUSTOM} own actions — delete one first.`), "warn") :
          K.field({ label: t("Название кнопки", "Button name"), forId: "qss-name", control: K.input("qss-name", editing ? editing.name : "", { placeholder: t("Официальнее", "More formal"), attrs: 'maxlength="40"' }) })
        + K.field({ label: t("Что сделать", "What to do"), forId: "qss-prompt", control: K.textarea("qss-prompt", editing ? editing.prompt : "", { rows: 3, attrs: 'maxlength="2000"',
            placeholder: t("Перепиши в официально-деловом стиле, сохрани смысл: {text}", "Rewrite in a formal business style, keep the meaning: {text}") }),
            help: `<span id="qss-hint"></span>` })
        + K.row({ label: t("Результат заменяет выделение", "The result replaces the selection"), desc: t("Под ответом появится «Заменить выделение» (Alt+Enter).", "«Replace selection» (Alt+Enter) appears under the answer."),
            control: K.toggle("qss-replace", editing ? !!editing.replace : false) })
        + K.actions(`${editing ? `<button type="button" class="pbtn ghost sx-btn-sm" id="qss-cancel">${esc(t("Отмена", "Cancel"))}</button>` : ""}
            <button type="button" class="pbtn sx-btn-sm" id="qss-save">${esc(editing ? t("Сохранить", "Save") : t("Добавить", "Add"))}</button>`) });
      el.innerHTML = general + buttons + own;
      bind();
      applyWhen();
    }

    function applyWhen() {
      el.querySelectorAll('[data-sx-when="qss-on"]').forEach((node) => { node.hidden = !S.enabled; });
    }

    function bind() {
      const $ = (id) => el.querySelector("#" + id);
      $("qss-on").addEventListener("change", (e) => { S.enabled = !!e.target.checked; applyWhen(); save({ quick_ask_selection: S.enabled }); });
      $("qss-clip").addEventListener("change", (e) => { S.clipboard = !!e.target.checked; save({ quick_ask_selection_clipboard: S.clipboard }); });
      $("qss-second").addEventListener("change", (e) => { S.second = e.target.value; save({ quick_ask_selection_second_lang: S.second }); });
      el.querySelectorAll("[data-act]").forEach((box) => box.addEventListener("change", () => {
        const id = box.dataset.act;
        S.order = box.checked ? [...S.order.filter((x) => x !== id), id] : S.order.filter((x) => x !== id);
        save({ quick_ask_selection_actions: S.order.slice() });
        render();
        const again = el.querySelector(`[data-act="${CSS.escape(id)}"]`);
        if (again) again.focus();
      }));
      const move = (id, step) => {
        const i = S.order.indexOf(id), j = i + step;
        if (i < 0 || j < 0 || j >= S.order.length) return;
        [S.order[i], S.order[j]] = [S.order[j], S.order[i]];
        save({ quick_ask_selection_actions: S.order.slice() });
        render();
        const again = el.querySelector(`[data-${step < 0 ? "up" : "down"}="${CSS.escape(id)}"]`);
        if (again && !again.disabled) again.focus();
      };
      el.querySelectorAll("[data-up]").forEach((b) => { b.onclick = () => move(b.dataset.up, -1); });
      el.querySelectorAll("[data-down]").forEach((b) => { b.onclick = () => move(b.dataset.down, 1); });
      el.querySelectorAll("[data-edit]").forEach((b) => { b.onclick = () => { S.editing = b.dataset.edit; render(); const n = $("qss-name"); if (n) n.focus(); }; });
      el.querySelectorAll("[data-del]").forEach((b) => { b.onclick = () => {
        const id = b.dataset.del, row = S.custom.find((c) => c.id === id);
        if (!row || !window.confirm(t(`Удалить действие «${row.name}»?`, `Delete the action «${row.name}»?`))) return;
        S.custom = S.custom.filter((c) => c.id !== id);
        S.order = S.order.filter((x) => x !== id);
        if (S.editing === id) S.editing = "";
        save({ quick_ask_selection_custom: S.custom.map((c) => ({ ...c })), quick_ask_selection_actions: S.order.slice() });
        render();
      }; });
      $("qss-reset-order").onclick = () => { S.order = [...DEFAULT_ORDER, ...S.custom.map((c) => c.id)]; save({ quick_ask_selection_actions: S.order.slice() }); render(); };
      const cancel = $("qss-cancel");
      if (cancel) cancel.onclick = () => { S.editing = ""; render(); };
      const prompt = $("qss-prompt"), hint = $("qss-hint");
      const hintText = () => {
        if (!prompt || !hint) return;
        hint.textContent = !prompt.value.trim() ? t("Результат приходит обычным ответом и сохраняется в чате.", "The result comes as a normal answer and is kept in the chat.")
          : prompt.value.includes("{text}") ? t("Вместо {text} подставится выделенный текст.", "{text} becomes the selected text.")
            : t("Нет {text} — выделенный текст будет приложен к просьбе целиком.", "No {text} — the selected text is attached to the request as a whole.");
      };
      if (prompt) { prompt.addEventListener("input", hintText); hintText(); }
      const saveBtn = $("qss-save");
      if (saveBtn) saveBtn.onclick = () => {
        const name = ($("qss-name").value || "").replace(/\s+/g, " ").trim().slice(0, 40);
        const text = ($("qss-prompt").value || "").trim().slice(0, 2000);
        if (!name || !text) { toast(t("Укажите название и что сделать с текстом", "Enter a name and what to do with the text"), "warn"); (name ? $("qss-prompt") : $("qss-name")).focus(); return; }
        if (S.custom.some((c) => c.id !== S.editing && c.name.toLowerCase() === name.toLowerCase())) { toast(t("Действие с таким названием уже есть", "An action with this name already exists"), "warn"); return; }
        const replace = !!$("qss-replace").checked;
        const wasEditing = !!S.editing;
        if (wasEditing) S.custom = S.custom.map((c) => (c.id === S.editing ? { ...c, name, prompt: text, replace } : c));
        else {
          const id = newId();
          S.custom.push({ id, name, prompt: text, replace });
          S.order.push(id);
        }
        save({ quick_ask_selection_custom: S.custom.map((c) => ({ ...c })), quick_ask_selection_actions: S.order.slice() });
        toast(wasEditing ? t("Действие сохранено", "Action saved") : t("Действие добавлено — оно уже в окне быстрого доступа", "Action added — it is in the Quick Ask window already"), "ok");
        S.editing = "";
        render();
      };
    }

    render();
    if (typeof ctx.api === "function") {
      ctx.api("/api/quick/selection/status").then((st) => { S.status = st; if (el.isConnected) render(); }).catch(() => {});
    }
    return { unmount() { el.innerHTML = ""; } };
  }

  window.MemuQuickSelectionSettings = { mount };
})();
