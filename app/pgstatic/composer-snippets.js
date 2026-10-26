

(function () {
  "use strict";
  if (window.MemuComposerSnippets) return;

  const en = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, enText) => (en() ? enText : ru);
  const h = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const field = () => document.getElementById("input");
  const TTL = 30000, LIMIT = 8;
  const state = { rows: null, at: 0, loading: null, open: false, index: 0, items: [], token: null, form: null };

  const FIELD_RE = /\{\?([^{}|\n]{1,40})(?:\|([^{}\n]{0,120}))?\}/g;
  function fields(text) {
    const out = [], seen = new Set();
    for (const m of String(text || "").matchAll(FIELD_RE)) {
      const name = m[1].trim();
      if (!name || seen.has(name.toLowerCase())) continue;
      seen.add(name.toLowerCase());
      out.push({ name, hint: (m[2] || "").trim() });
    }
    return out;
  }
  function fill(text, values) {
    const map = {};
    for (const [k, v] of Object.entries(values || {})) map[k.trim().toLowerCase()] = v;
    return String(text || "").replace(FIELD_RE, (all, name) => {
      const v = map[name.trim().toLowerCase()];
      return v === undefined ? all : String(v);
    });
  }

  async function load(force) {
    if (!force && state.rows && Date.now() - state.at < TTL) return state.rows;
    if (state.loading) return state.loading;
    state.loading = fetch(`/api/quick/features?lang=${en() ? "en" : "ru"}`).then((r) => (r.ok ? r.json() : {}))
      .then((data) => { state.rows = Array.isArray(data.snippets) ? data.snippets : []; state.at = Date.now(); return state.rows; })
      .catch(() => state.rows || [])
      .finally(() => { state.loading = null; });
    return state.loading;
  }
  async function save(rows) {
    const resp = await fetch("/api/quick/features/snippets", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snippets: rows, lang: en() ? "en" : "ru" }) });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
    state.rows = data.snippets || rows; state.at = Date.now();
    return state.rows;
  }
  function match(rows, word) {
    word = String(word || "").toLowerCase();
    if (!word) return rows.slice(0, LIMIT);
    const scored = [];
    for (const s of rows) {
      const kw = String(s.keyword || ""), name = String(s.name || "").toLowerCase();
      let score = -1;
      if (kw === word) score = 0;
      else if (kw.startsWith(word)) score = 1;
      else if (name.split(/[^\p{L}\p{N}]+/u).some((w) => w && w.startsWith(word))) score = 2;
      else if (kw.includes(word) || name.includes(word)) score = 3;
      if (score >= 0) scored.push([score, s]);
    }
    return scored.sort((a, b) => a[0] - b[0]).slice(0, LIMIT).map((r) => r[1]);
  }

  function tokenAt(el) {
    if (!el || el.selectionStart !== el.selectionEnd) return null;
    const before = el.value.slice(0, el.selectionStart);
    const m = before.match(/(^|\s);([\p{L}\p{N}_-]{0,32})$/u);
    if (!m) return null;
    if (((before.match(/```/g) || []).length % 2) === 1) return null;
    const after = el.value.slice(el.selectionStart);
    if (/^[\p{L}\p{N}_-]/u.test(after)) return null;
    return { start: before.length - m[2].length - 1, end: before.length, word: m[2] };
  }
  function othersOwn() {
    return !!(window.MemuMentions?.owns?.() || (document.getElementById("slash-menu") && !document.getElementById("slash-menu").hidden));
  }

  function menuEl() {
    let menu = document.getElementById("snip-menu");
    if (menu) return menu;
    const slash = document.getElementById("slash-menu");
    if (!slash) return null;
    menu = document.createElement("div");
    menu.id = "snip-menu"; menu.className = "slash-menu snip-menu"; menu.hidden = true;
    menu.setAttribute("role", "listbox"); menu.setAttribute("aria-label", L("Сниппеты", "Snippets"));
    slash.after(menu);
    menu.addEventListener("mousedown", (e) => e.preventDefault());
    menu.addEventListener("click", (e) => {
      const b = e.target.closest("[data-snip], [data-snip-act]");
      if (!b) return;
      if (b.dataset.snipAct === "manage") { close(); manage(); return; }
      choose(+b.dataset.snip);
    });
    return menu;
  }
  function preview(s) {
    if (s.kind === "ai") return L("ИИ-шаблон", "AI template");
    const f = fields(s.text);
    const text = String(s.text || "").replace(/\s+/g, " ").trim();
    return (f.length ? L("Поля: ", "Fields: ") + f.map((x) => x.name).join(", ") + " · " : "") + (text.length > 70 ? text.slice(0, 69) + "…" : text);
  }
  function render() {
    const menu = menuEl();
    if (!menu) return;
    const rows = state.items;
    const head = `<div class="snip-head"><span>${h(L("Сниппеты", "Snippets"))}</span><kbd>↵</kbd><kbd>Tab</kbd><kbd>Esc</kbd></div>`;
    const body = rows.length ? rows.map((s, i) => `<button type="button" role="option" class="${i === state.index ? "active" : ""}" aria-selected="${i === state.index}" data-snip="${i}">
        <b>${s.keyword ? ";" + h(s.keyword) : "·"}</b><span><strong>${h(s.name || s.keyword)}${s.kind === "ai" ? ` <i class="snip-ai">AI</i>` : ""}${fields(s.text).length ? ` <i class="snip-tpl">${h(L("шаблон", "template"))}</i>` : ""}</strong><small>${h(preview(s))}</small></span></button>`).join("")
      : `<div class="snip-empty">${h(state.rows && state.rows.length ? L("Нет сниппета с таким словом", "No snippet with this keyword") : L("Сниппетов пока нет", "No snippets yet"))}</div>`;
    const foot = `<button type="button" class="snip-foot" data-snip-act="manage"><i aria-hidden="true">+</i>${h(L("Сниппеты и шаблоны… (общие с Быстрым доступом)", "Snippets and templates… (shared with Quick Access)"))}</button>`;
    menu.innerHTML = head + body + foot;
    menu.hidden = false;
    state.open = true;
    menu.querySelector("button.active")?.scrollIntoView({ block: "nearest" });
  }
  function close() {
    state.open = false; state.items = []; state.token = null;
    const menu = document.getElementById("snip-menu");
    if (menu) { menu.hidden = true; menu.innerHTML = ""; }
  }
  async function update() {
    const el = field();
    const tok = tokenAt(el);
    if (!tok || othersOwn() || state.form) { if (state.open) close(); return; }
    const rows = await load();
    const now = tokenAt(el);
    if (!now || now.word !== tok.word || othersOwn()) { if (state.open) close(); return; }
    const items = match(rows, now.word);
    if (!items.length && now.word.length > 2 && !state.open) return;
    const same = items.map((s) => s.id).join() === state.items.map((s) => s.id).join();
    state.items = items; state.token = now;
    if (!same) state.index = 0;
    render();
  }

  function putText(el, start, end, text, caretFromEnd) {
    el.focus();
    el.setSelectionRange(start, end);

    let ok = false;
    try { ok = document.execCommand("insertText", false, text); } catch (e) { ok = false; }
    if (!ok) { el.setRangeText(text, start, end, "end"); el.dispatchEvent(new Event("input", { bubbles: true })); }
    const caret = start + text.length - (caretFromEnd || 0);
    el.setSelectionRange(caret, caret);
  }
  async function choose(i) {
    const s = state.items[i], el = field(), tok = state.token || tokenAt(el);
    if (!s || !el || !tok) return;
    close();
    let text = String(s.text || ""), cursor = 0;
    const rest = (el.value.slice(0, tok.start) + el.value.slice(tok.end)).trim();
    if (s.kind === "ai") {
      text = text.trim() + "\n\n";
    } else if (/\{(?:date|time|datetime|weekday|clipboard|selection|input|uuid|cursor)(?:[+-]\d{1,4})?\}|\{\{|\}\}/.test(text)) {
      try {
        const resp = await fetch("/api/quick/features/snippets/use", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: s.id, input: rest, selection: "", lang: en() ? "en" : "ru" }) });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
        text = String(data.text || ""); cursor = +data.cursor || 0;
      } catch (e) { if (typeof toast === "function") toast(e.message, "err"); return; }
    }
    const blanks = fields(text);
    if (!blanks.length) { putText(el, tok.start, tok.end, text, cursor); return; }
    const values = await askFields(blanks, s.name || s.keyword);
    if (!values) { el.focus(); return; }

    const marker = "\u0000c\u0000";
    const marked = cursor ? text.slice(0, text.length - cursor) + marker + text.slice(text.length - cursor) : text;
    const filled = fill(marked, values);
    const at = filled.indexOf(marker);
    const final = filled.replace(marker, "");
    putText(el, tok.start, tok.end, final, at >= 0 ? final.length - at : 0);
  }

  function askFields(blanks, title) {
    return new Promise((resolve) => {
      const slash = document.getElementById("slash-menu");
      if (!slash) { resolve(null); return; }
      state.form?.done(null);
      const form = document.createElement("form");
      form.className = "slash-menu snip-form";
      form.setAttribute("aria-label", L("Заполните шаблон", "Fill in the template"));
      form.innerHTML = `<div class="snip-head"><span>${h(L("Шаблон", "Template"))} · ${h(title || "")}</span><kbd>↵</kbd><kbd>Esc</kbd></div>
        ${blanks.map((b, i) => `<label class="snip-field"><span>${h(b.name)}</span>
          <input type="text" name="f${i}" autocomplete="off" spellcheck="true" placeholder="${h(b.hint || L("Введите значение", "Enter a value"))}"></label>`).join("")}
        <div class="snip-actions"><button type="button" class="pbtn ghost sx-btn-sm" data-snip-cancel>${h(L("Отмена", "Cancel"))}</button>
          <button type="submit" class="pbtn sx-btn-sm">${h(L("Вставить", "Insert"))}</button></div>`;
      slash.after(form);
      const inputs = [...form.querySelectorAll("input")];
      const done = (values) => {
        if (state.form?.el !== form) return;
        state.form = null; form.remove(); resolve(values);
      };
      state.form = { el: form, done };
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const values = {};
        blanks.forEach((b, i) => { values[b.name] = inputs[i].value; });
        done(values);
      });
      form.querySelector("[data-snip-cancel]").addEventListener("click", () => done(null));
      form.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(null); return; }
        if (e.key === "Enter" && !e.shiftKey && e.target.tagName === "INPUT") {
          const at = inputs.indexOf(e.target);
          if (at < inputs.length - 1) { e.preventDefault(); inputs[at + 1].focus(); }
        }
      });
      form.addEventListener("focusout", () => setTimeout(() => { if (state.form?.el === form && !form.contains(document.activeElement) && document.activeElement !== field()) done(null); }, 0));
      inputs[0]?.focus();
    });
  }

  async function manage(editId) {
    if (typeof panel !== "function") return;
    const rows = (await load(true)).slice();
    const draw = () => {
      panel(`<h2>${h(L("Сниппеты и шаблоны", "Snippets and templates"))}</h2>
        <p class="sub">${h(L("Наберите «;слово» в поле ввода — сниппет встанет на его место. Поля {?имя} или {?имя|подсказка} спросят значение перед вставкой. Библиотека общая с Быстрым доступом (Alt+Space).", "Type «;word» in the composer and the snippet takes its place. Blanks {?name} or {?name|hint} ask for a value before inserting. The library is shared with Quick Access (Alt+Space)."))}</p>
        <div class="snip-list">${rows.length ? rows.map((s) => `<div class="snip-row">
            <code>${s.keyword ? ";" + h(s.keyword) : "—"}</code><span><b>${h(s.name)}</b><small>${h(preview(s))}</small></span>
            <button type="button" class="pbtn ghost sx-btn-sm" data-snip-edit="${h(s.id)}">${h(L("Изменить", "Edit"))}</button>
            <button type="button" class="pbtn ghost sx-btn-sm danger" data-snip-del="${h(s.id)}" aria-label="${h(L("Удалить", "Delete"))}">✕</button></div>`).join("")
          : `<div class="snip-empty">${h(L("Сниппетов пока нет", "No snippets yet"))}</div>`}</div>
        <div class="snip-actions"><button type="button" class="pbtn" data-snip-new>+ ${h(L("Новый сниппет", "New snippet"))}</button></div>`, "snip-panel");
      const root = document.getElementById("panel");
      root.querySelector("[data-snip-new]").onclick = () => edit(null);
      root.querySelectorAll("[data-snip-edit]").forEach((b) => { b.onclick = () => edit(rows.find((s) => s.id === b.dataset.snipEdit)); });
      root.querySelectorAll("[data-snip-del]").forEach((b) => { b.onclick = async () => {
        const at = rows.findIndex((s) => s.id === b.dataset.snipDel);
        if (at < 0) return;
        const removed = rows.splice(at, 1);
        try { rows.splice(0, rows.length, ...(await save(rows))); draw(); }
        catch (e) { rows.splice(at, 0, ...removed); if (typeof toast === "function") toast(e.message, "err"); }
      }; });
    };
    const edit = (s) => {
      const cur = s || { id: "", keyword: "", name: "", kind: "text", text: "" };
      const chips = ["{?" + L("имя", "name") + "}", "{?" + L("тема|о чём письмо", "topic|what it is about") + "}", "{date}", "{time}", "{weekday}", "{clipboard}", "{input}", "{cursor}"];
      panel(`<h2>${h(s ? L("Сниппет", "Snippet") : L("Новый сниппет", "New snippet"))}</h2>
        <div class="snip-edit">
          <label><span>${h(L("Ключевое слово", "Keyword"))}</span><input class="pinput" id="snip-kw" maxlength="32" placeholder="${h(L("письмо", "email"))}" value="${h(cur.keyword)}"></label>
          <label><span>${h(L("Название", "Name"))}</span><input class="pinput" id="snip-name" maxlength="80" placeholder="${h(L("Деловое письмо", "Business email"))}" value="${h(cur.name)}"></label>
          <label class="snip-kind"><input type="checkbox" id="snip-ai" ${cur.kind === "ai" ? "checked" : ""}> <span>${h(L("ИИ-шаблон (инструкция модели)", "AI template (an instruction for the model)"))}</span></label>
          <label class="snip-wide"><span>${h(L("Текст", "Text"))}</span><textarea class="pinput" id="snip-text" rows="7" placeholder="${h(L("Здравствуйте, {?имя}! … Поля {?имя} спросят значение при вставке.", "Hello {?name}! … Blanks {?name} ask for a value when inserted."))}">${h(cur.text)}</textarea></label>
          <div class="snip-chips">${chips.map((c) => `<button type="button" data-snip-chip="${h(c)}">${h(c)}</button>`).join("")}</div>
        </div>
        <div class="snip-actions"><button type="button" class="pbtn ghost" data-snip-back>${h(L("Назад", "Back"))}</button><button type="button" class="pbtn" data-snip-save>${h(L("Сохранить", "Save"))}</button></div>`, "snip-panel");
      const root = document.getElementById("panel");
      const area = root.querySelector("#snip-text");
      root.querySelectorAll("[data-snip-chip]").forEach((b) => { b.onclick = () => { area.focus(); area.setRangeText(b.dataset.snipChip, area.selectionStart, area.selectionEnd, "end"); }; });
      root.querySelector("[data-snip-back]").onclick = draw;
      root.querySelector("[data-snip-save]").onclick = async () => {
        const row = { id: cur.id || "s" + Date.now().toString(36), keyword: root.querySelector("#snip-kw").value.trim().replace(/^;/, ""),
          name: root.querySelector("#snip-name").value.trim(), kind: root.querySelector("#snip-ai").checked ? "ai" : "text",
          text: area.value, created: cur.created || Date.now() };
        if (!row.text.trim() || !(row.keyword || row.name)) { if (typeof toast === "function") toast(L("Нужны текст и ключевое слово или название", "A text and a keyword or a name are required"), "warn"); return; }
        if (row.keyword && rows.some((x) => x.id !== row.id && x.keyword === row.keyword.toLowerCase())) { if (typeof toast === "function") toast(L("Это ключевое слово уже занято", "This keyword is already taken"), "warn"); return; }
        const next = rows.slice(); const at = next.findIndex((x) => x.id === row.id);
        if (at >= 0) next[at] = row; else next.push(row);
        try { rows.splice(0, rows.length, ...(await save(next))); if (typeof toast === "function") toast(L("Сниппет сохранён", "Snippet saved"), "ok"); draw(); }
        catch (e) { if (typeof toast === "function") toast(e.message, "err"); }
      };
      root.querySelector(s ? "#snip-text" : "#snip-kw")?.focus();
    };
    if (editId) edit(rows.find((s) => s.id === editId)); else draw();
  }

  function onKey(e) {
    if (!state.open || e.isComposing || e.keyCode === 229) return;
    const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    const n = state.items.length;
    if (e.key === "ArrowDown" && n) { stop(); state.index = (state.index + 1) % n; render(); }
    else if (e.key === "ArrowUp" && n) { stop(); state.index = (state.index - 1 + n) % n; render(); }
    else if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && n) { stop(); choose(state.index); }
    else if (e.key === "Escape") { stop(); close(); }
  }
  function init() {
    const el = field();
    if (!el || el.dataset.snipReady) return;
    el.dataset.snipReady = "1";
    el.addEventListener("keydown", onKey, true);
    el.addEventListener("input", () => { if (state.open || el.value.includes(";")) update(); });
    el.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== field() && !document.getElementById("snip-menu")?.contains(document.activeElement)) close(); }, 150));
    document.addEventListener("memu:chat-open", () => { close(); state.form?.done(null); });
    if (window.MemuSearch?.registerProvider) registerSearch();
  }
  function registerSearch() {
    MemuSearch.registerProvider({
      id: "snippets", type: "snippet", label: ["Сниппеты", "Snippets"],
      icon: '<path d="M8 6 3 12l5 6M16 6l5 6-5 6"/>',
      async search(q, ctx) {
        const query = String(q || "").trim();
        const words = L("сниппеты сниппет шаблоны шаблон snippets templates", "snippets snippet templates template сниппеты шаблоны");
        const rows = await load();
        const out = [];
        for (const s of rows) {
          const score = ctx.score(query, `;${s.keyword} ${s.name} ${s.text}`, words);
          if (score) out.push({ id: "snippet:" + s.id, title: (s.keyword ? ";" + s.keyword + " — " : "") + (s.name || ""), subtitle: preview(s), score: Math.min(1, score), _id: s.id });
        }
        out.push({ id: "snippet:manage", title: L("Сниппеты и шаблоны…", "Snippets and templates…"), subtitle: L("Создать, изменить, удалить", "Create, edit, delete"), score: query ? Math.min(0.5, ctx.score(query, L("сниппеты шаблоны управлять новый", "snippets templates manage new"), words) || 0) : 0.3, _manage: true });
        return out.filter((r) => r.score > 0);
      },
      open(item) {
        MemuSearch.close();
        if (item._manage) { setTimeout(() => manage(), 40); return; }

        setTimeout(async () => {
          const el = field(), s = (state.rows || []).find((x) => x.id === item._id);
          if (!el || !s) return;
          el.focus();
          const pos = el.selectionStart ?? el.value.length;
          const pre = el.value.slice(0, pos), lead = pre && !/\s$/.test(pre) ? " " : "";
          const start = pos + lead.length;
          putText(el, pos, el.selectionEnd ?? pos, lead + ";", 0);
          state.items = [s]; state.index = 0; state.token = { start, end: start + 1, word: "" };
          choose(0);
        }, 40);
      },
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(init, 0), { once: true });
  else setTimeout(init, 0);

  window.MemuComposerSnippets = Object.freeze({ open: () => { const el = field(); if (el) { putText(el, el.selectionStart, el.selectionEnd, (el.selectionStart && !/\s$/.test(el.value.slice(0, el.selectionStart)) ? " " : "") + ";", 0); update(); } },
    fields, fill, manage, _state: state });
})();
