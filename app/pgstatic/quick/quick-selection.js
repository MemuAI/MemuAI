/* MemuAI Quick Ask · Round 27 — actions on the text selected in any program («Контекстные действия по выделению»).

   The native side reads the selection BEFORE the window shows (app/quick_selection.py: UI Automation, else Ctrl+C
   with the clipboard snapshotted and restored) and passes it in QuickAsk.onShow / QuickAsk.onSelection; quick.js
   shows it as the «Выделено» chip and emits "selection". This module adds, under the chip, ONE row of actions:
   Перевести · Объяснить · Исправить · Кратко · Ответить · Спросить + the user's own actions (Settings → Quick Ask →
   Selected text), in the user's order.

   Keyboard: 1…9 (empty field) or Alt+1…9 run an action; ↓ from the empty field enters the row, ←/→ move, Enter runs,
   ↑ / Esc go back to the field. An action sends ONLY its short label as the question (MemuQuick.send with
   {selectionAction}) — the server builds the instruction (quick_selection.resolve_action), the answer streams and is
   saved in the chat like any other. «Заменить выделение» (Alt+Enter) pastes the answer over the selection in the
   program it came from (POST /api/quick/window {action: "replace_selection"}); «Копировать» is the usual answer button.

   window.QuickSelection = { owns(), actions(), run(id), replace() } — owns() tells quick.js to drop its old
   selection rows from the idle list. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q || typeof Q.registerPanel !== "function" || typeof Q.selection !== "function") return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const I = Q.icons || {};
  const ICON = {
    translate: I.translate, explain: I.ask, fix: I.check, summarize: I.list, ask: I.text || I.ask,
    reply: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z"/></svg>',
    custom: I.spark,
    replace: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h11M4 7l3-3M4 7l3 3"/><path d="M20 17H9m11 0-3-3m3 3-3 3"/></svg>',
  };
  const LANG_TO = {   // «Перевести на …» — the accusative in Russian
    en: ["на английский", "to English"], ru: ["на русский", "to Russian"], de: ["на немецкий", "to German"], fr: ["на французский", "to French"],
    es: ["на испанский", "to Spanish"], it: ["на итальянский", "to Italian"], pt: ["на португальский", "to Portuguese"], uk: ["на украинский", "to Ukrainian"],
    pl: ["на польский", "to Polish"], tr: ["на турецкий", "to Turkish"], zh: ["на китайский", "to Chinese"], ja: ["на японский", "to Japanese"],
    ko: ["на корейский", "to Korean"], ar: ["на арабский", "to Arabic"],
  };
  const BUILTIN = {
    translate: { label: ["Перевести", "Translate"], replace: true },
    explain: { label: ["Объяснить", "Explain"] },
    fix: { label: ["Исправить", "Fix"], replace: true, title: ["Исправить ошибки и улучшить", "Fix mistakes and polish"] },
    summarize: { label: ["Кратко", "Summarize"], title: ["Кратко пересказать", "Summarize briefly"] },
    reply: { label: ["Ответить", "Reply"], title: ["Написать ответ на письмо или сообщение", "Write a reply to the letter or message"] },
    ask: { label: ["Спросить", "Ask"], title: ["Спросить о выделенном — напишите вопрос", "Ask about the selection — type a question"] },
  };
  const DEFAULT_ORDER = ["translate", "explain", "fix", "summarize", "reply", "ask"];
  const FILE_SKIP = new Set(["fix", "reply"]);

  /* Mirror of quick_selection.detect_lang (keep in sync): "ru" | "uk" | "en" | "". */
  function guessLang(text) {
    const sample = String(text || "").slice(0, 4000);
    const count = (re) => (sample.match(re) || []).length;
    const cyr = count(/[А-Яа-яЁёІіЇїЄєҐґЎў]/g), lat = count(/[A-Za-z]/g), ext = count(/[À-ɏ]/g);
    const letters = cyr + lat + ext;
    if (letters < 2) return "";
    if (cyr / letters >= 0.5) return count(/[ІіЇїЄєҐґ]/g) >= Math.max(1, Math.floor(cyr / 60)) ? "uk" : "ru";
    if (lat / letters >= 0.85 && (count(/\b(the|and|is|are|of|to|in|that|it|you|for|with|this|have|be|on|not|was|we|i|my|your)\b/gi) || (letters < 40 && !ext))) return "en";
    return "";
  }
  function translateTarget(text, prefs) {
    const ui = Q.lang() === "en" ? "en" : "ru";
    let second = String(prefs.quick_ask_selection_second_lang || "");
    if (!LANG_TO[second] || second === ui) second = ui === "ru" ? "en" : "ru";
    return guessLang(text) === ui ? second : ui;
  }

  const st = { el: null, panel: null, index: -1, asking: false, text: "", info: null };
  const prefs = () => (Q.state().prefs || {});
  const enabled = () => prefs().quick_ask_selection !== false;

  /* The configured actions, in order: built-ins and the user's own ({id: "c_…", name, prompt, replace}). */
  function actions() {
    const p = prefs();
    const custom = Array.isArray(p.quick_ask_selection_custom) ? p.quick_ask_selection_custom : [];
    const order = Array.isArray(p.quick_ask_selection_actions) ? p.quick_ask_selection_actions : DEFAULT_ORDER;
    const out = [];
    // Round 30: a dropped or opened file is not text in another app — nothing to replace and no letter to reply to
    const file = (st.info || {}).source === "file";
    for (const id of order) {
      if (file && FILE_SKIP.has(id)) continue;
      if (BUILTIN[id]) {
        const def = BUILTIN[id];
        const row = { id, icon: ICON[id], label: t(def.label[0], def.label[1]), title: def.title ? t(def.title[0], def.title[1]) : "", replace: !!def.replace };
        if (id === "translate") {
          row.target = translateTarget(st.text, p);
          const to = LANG_TO[row.target];
          row.label = t(`Перевести ${to[0]}`, `Translate ${to[1]}`);
        }
        out.push(row);
        continue;
      }
      const own = custom.find((c) => c && c.id === id);
      if (own && own.name) out.push({ id, icon: ICON.custom, label: String(own.name), title: String(own.prompt || "").slice(0, 300), replace: !!own.replace, custom: true });
    }
    return out.slice(0, 12);
  }
  const owns = () => enabled() && actions().length > 0;

  const card = () => document.getElementById("qa-card");
  function visible() {
    const c = card();
    if (!st.text || !owns() || !c) return false;
    const view = c.dataset.view || "";
    return c.dataset.busy !== "1" && !["picker", "chats", "menu"].includes(view);
  }

  function render() {
    if (!st.el) return;
    const show = visible();
    if (!show) {
      if (!st.el.hidden) { st.index = -1; st.key = ""; st.panel.hide(); }
      return;
    }
    const list = actions();
    const n = Math.min(list.length, 9);
    // Same row as before (a view/busy flip, a config reload) → keep the DOM: no re-animation, focus kept.
    const key = JSON.stringify([list.map((a) => [a.id, a.label]), st.asking, Q.lang(), (st.info || {}).source || ""]);
    if (key === st.key && !st.el.hidden) return;
    st.key = key;
    st.el.innerHTML = `<div class="qsel-row" role="toolbar" aria-label="${esc(t("Действия с выделенным текстом", "Actions on the selected text"))}">${list.map((a, i) =>
      `<button type="button" class="qsel-act${a.id === "ask" && st.asking ? " on" : ""}" data-i="${i}" tabindex="${i === 0 ? 0 : -1}" title="${esc(a.title || a.label)}${i < 9 ? ` (${i + 1})` : ""}">
        <span class="ic">${a.icon || ICON.custom}</span><span class="tx">${esc(a.label)}</span>${i < 9 ? `<kbd>${i + 1}</kbd>` : ""}</button>`).join("")}</div>
      <div class="qsel-hint">${st.asking
        ? esc((st.info || {}).source === "file" ? t("Напишите вопрос о файле и нажмите Enter", "Type a question about the file and press Enter")
          : ((st.info || {}).source === "text" ? t("Напишите вопрос об этом тексте и нажмите Enter", "Type a question about this text and press Enter")
            : t("Напишите вопрос о выделенном тексте и нажмите Enter", "Type a question about the selected text and press Enter")))
        : t(`<kbd>1</kbd>–<kbd>${n}</kbd> или <kbd>↓</kbd> и <kbd>↵</kbd> — действие · или просто напишите свой вопрос`,
          `<kbd>1</kbd>–<kbd>${n}</kbd> or <kbd>↓</kbd> and <kbd>↵</kbd> — run an action · or just type your question`)}</div>`;
    st.el.querySelectorAll(".qsel-act").forEach((btn) => {
      btn.onclick = () => run(list[+btn.dataset.i]);
      btn.onfocus = () => { st.index = +btn.dataset.i; };
    });
    if (st.el.hidden) st.panel.show();
    if (st.index >= 0) focusButton(st.index);
  }

  function focusButton(i) {
    const buttons = st.el ? Array.from(st.el.querySelectorAll(".qsel-act")) : [];
    if (!buttons.length) return false;
    const k = ((i % buttons.length) + buttons.length) % buttons.length;
    buttons.forEach((b, j) => { b.tabIndex = j === k ? 0 : -1; });
    buttons[k].focus({ preventScroll: true });
    st.index = k;
    return true;
  }
  const input = () => document.getElementById("qa-input");
  function backToInput() {
    st.index = -1;
    const el = input();
    if (el) el.focus({ preventScroll: true });
  }

  function run(action) {
    if (!action) return false;
    const sel = Q.selection();
    if (!sel.text || Q.state().busy) return false;
    if (action.id === "ask") {
      st.asking = true;
      render();
      Q.setInput(input() ? input().value : "", { focus: true });
      return true;
    }
    const typed = (input() && input().value.trim()) || "";
    const info = sel.info || {};
    // Round 27 (review): not after a document / a clipboard item was attached on top — the answer is about more than the selection
    const replace = !!(action.replace && Q.native && info.id && info.editable !== false && !info.attached);
    const spec = { id: action.id, replace, selId: replace ? info.id : "" };
    if (action.target) spec.target = action.target;
    if (typed) spec.note = typed;       // a wish typed before clicking the action («короче», «официальнее»)
    st.asking = false; st.index = -1;
    Q.send(typed ? `${action.label}: ${typed}` : action.label, { selectionAction: spec });
    return true;
  }

  function lastTurn() {
    const turns = Q.state().turns || [];
    return turns.length ? turns[turns.length - 1] : null;
  }
  const canReplace = (turn) => !!(turn && turn.done && !turn.error && String(turn.a || "").trim() && turn.selectionAction && turn.selectionAction.replace);

  async function replace() {
    const turn = lastTurn();
    if (!canReplace(turn) || Q.state().busy) return false;
    const text = String(turn.a || "").trim();
    let r = { ok: false };
    try { r = await Q.api("/api/quick/window", { body: { action: "replace_selection", text, selection_id: turn.selectionAction.selId } }); }
    catch (e) { r = { ok: false, code: "failed" }; }
    if (r.ok) return true;
    let copied = false;
    try { await navigator.clipboard.writeText(text); copied = true; } catch (e) {
      try { copied = !!(await Q.api("/api/quick/window", { body: { action: "copy", text } })).ok; } catch (_) { copied = false; }
    }
    const why = {
      stale: ["Это выделение уже заменено или устарело", "This selection was already replaced or is out of date"],
      gone: ["Окно, где был выделен текст, закрыто", "The window with the selection is closed"],
      input_blocked: ["Программа не приняла вставку (запущена от администратора?)", "The program refused the paste (running as administrator?)"],
      clipboard_busy: ["Буфер обмена занят другой программой", "Another program is using the clipboard"],
      unsupported: ["Замена работает в приложении MemuAI для Windows", "Replacing works in the MemuAI Windows app"],
    }[r.code] || ["Не удалось заменить выделение", "Could not replace the selection"];
    Q.toast(t(why[0], why[1]) + (copied ? t(" — ответ скопирован, вставьте Ctrl+V", " — the answer is copied, paste with Ctrl+V") : ""), "warn", 4200);
    return false;
  }

  /* ------------------------------------------------------------ wiring */
  function sync(data) {
    const text = data ? String(data.text || "") : "";
    if (text !== st.text) { st.asking = false; st.index = -1; }
    st.text = text; st.info = data ? data.info || null : null;
    render();
  }
  st.panel = Q.registerPanel("selection", (el) => { st.el = el; el.classList.add("qsel"); return null; }, { slot: "top", order: -20, hidden: true });
  Q.on("selection", sync);
  Q.on("config", () => render());
  Q.on("open", () => { st.asking = false; sync(Q.selection()); });
  Q.on("chat-new", () => sync(Q.selection()));
  Q.on("chat-open", () => sync(Q.selection()));
  Q.on("answer-start", () => { st.asking = false; sync(Q.selection()); });
  Q.on("escape", (e) => {
    if (st.el && st.el.contains(document.activeElement)) { e.preventDefault(); backToInput(); }
  });
  if (typeof Q.addAnswerAction === "function") {
    Q.addAnswerAction({ id: "selection-replace", primary: true, icon: ICON.replace, label: () => t("Заменить выделение", "Replace selection"),
      title: () => t("Вставить ответ вместо выделенного текста в программе (Alt+Enter)", "Paste the answer over the selected text in the program (Alt+Enter)"),
      hidden: () => !canReplace(lastTurn()), run: () => replace() });
  }
  // The view (menu, picker, chats) and «busy» are attributes of the card: follow them.
  const c = card();
  if (c && window.MutationObserver) new MutationObserver(render).observe(c, { attributes: true, attributeFilter: ["data-view", "data-busy"] });

  const field = input();
  if (field) field.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing || !visible()) return;
    const plain = !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey;
    if (plain && !field.value && !st.asking && /^(Digit|Numpad)[1-9]$/.test(e.code)) {
      const list = actions(), i = +e.code.slice(-1) - 1;
      if (list[i]) { e.preventDefault(); run(list[i]); }
      return;
    }
    if (plain && e.key === "ArrowDown" && !field.value.trim()) { e.preventDefault(); focusButton(0); }
  });
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing) return;
    // Alt+Enter — «Заменить выделение» under a finished answer
    if (e.altKey && !e.ctrlKey && !e.metaKey && e.key === "Enter" && canReplace(lastTurn())) { e.preventDefault(); replace(); return; }
    if (!visible()) return;
    if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-9]$/.test(e.code)) {
      const list = actions(), i = +e.code.slice(5) - 1;
      if (list[i]) { e.preventDefault(); run(list[i]); }
      return;
    }
    if (!st.el || !st.el.contains(document.activeElement)) return;
    const plain = !e.ctrlKey && !e.altKey && !e.metaKey;
    if (plain && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { e.preventDefault(); focusButton(st.index + (e.key === "ArrowRight" ? 1 : -1)); return; }
    if (plain && e.key === "ArrowUp") { e.preventDefault(); backToInput(); return; }
    if (plain && /^(Digit|Numpad)[1-9]$/.test(e.code)) {
      const list = actions(), i = +e.code.slice(-1) - 1;
      if (list[i]) { e.preventDefault(); run(list[i]); }
      return;
    }
    // any other printable key goes to the field (start typing a question right from the row)
    if (plain && e.key.length === 1 && !e.shiftKey && e.key !== " ") { backToInput(); }
  });

  window.QuickSelection = { owns, actions, run: (id) => run(actions().find((a) => a.id === id)), replace, guessLang };
  sync(Q.selection());
})();
