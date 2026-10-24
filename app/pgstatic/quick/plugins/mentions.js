/* Round 35 (r35-finish-b, zone «mentions») — @-mentions in the Quick Access composer.

   The same providers and routes as the main window (app/mentions.py: /api/mentions/providers|search|resolve):
   «@» in the question opens a compact list under the input (categories, recent picks, found items,
   «Искать в Gmail…» rows; «@чат: договор» filters by category). A pick becomes a chip over the list with
   its token estimate (resolved at once — the preview shows exactly what the model gets).
   Every question carries {type:"mentions", items:[{provider, ref, title, text, …}], earlier:[…]} through
   MemuQuick.addRequestContext; the server (quick_ask_api → mentions.quick_context) turns it into the same
   untrusted <mentioned_context> block as the chat. Items mentioned earlier in this Quick Access conversation
   go along with later questions (earlier="true"), until a new chat.
   Not here: @модель (Quick Access has its own model picker) — the provider is hidden. */
(() => {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const RECENT_KEY = "memu_mentions_recent";      // shared with the main window (same origin)
  const HIDDEN = new Set(["model", "gmail_thread", "calendar_day"]);
  const MAX_CHIPS = 8, MAX_EARLIER = 8;

  const P = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    chat: '<path d="M4 5h16v11H8l-4 4z"/>', memunote: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/>',
    note: '<path d="M5 4h14v12l-4 4H5z"/><path d="M15 20v-4h4M8 9h8M8 13h5"/>', task: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    result: '<path d="M5 12l4 4 10-10"/>', mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    thread: '<path d="M4 6h12v8H8l-4 3z"/><path d="M8 18h8l4 3V10"/>', calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/>',
    day: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M12 13v4"/>', notion: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 16V8l6 8V8"/>',
    github: '<path d="M9 19c-4 1-4-2-6-2m12 4v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1-.3-3.4 1.3a11.6 11.6 0 0 0-6 0C6.8 2.8 5.8 3.1 5.8 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4.4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
    drive: '<path d="M8 4h8l5 9-4 7H7l-4-7z"/><path d="M8 4l5 9h8M3 13h10l-6 7"/>', memory: '<path d="M12 4a5 5 0 0 1 5 5c0 3-2 4-2 6H9c0-2-2-3-2-6a5 5 0 0 1 5-5z"/><path d="M10 19h4"/>',
    panel: '<rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="4" rx="1.5"/><rect x="13" y="10" width="8" height="10" rx="1.5"/><rect x="3" y="13" width="8" height="7" rx="1.5"/>',
    snippet: '<path d="M8 7l-5 5 5 5M16 7l5 5-5 5"/>', skill: '<path d="M12 3l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.4 6.8 19.1l1-5.8L3.5 9.2l5.9-.8z"/>',
    file: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5"/>', pc: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    download: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>', link: '<path d="M10 14a4 4 0 0 0 5.6 0l3-3a4 4 0 0 0-5.6-5.6l-1 1"/><path d="M14 10a4 4 0 0 0-5.6 0l-3 3a4 4 0 0 0 5.6 5.6l1-1"/>',
    clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4h6v3H9z"/>', character: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
    help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.7M12 17h.01"/>', inbox: '<path d="M4 13l2-8h12l2 8v6H4z"/><path d="M4 13h5l1 2h4l1-2h5"/>',
    doc: '<path d="M6 3h9l4 4v14H6z"/>', recent: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>', search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>', at: '<circle cx="12" cy="12" r="4"/><path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>',
  };
  const icon = (name) => P(ICONS[name] || ICONS.doc);

  const S = { providers: [], providersAt: 0, chips: [], earlier: [], rows: [], index: 0, open: false, tok: null,
    controller: null, timer: 0, seq: 0, pending: null, preview: -1, chipsEl: null, menuEl: null };

  const input = () => document.getElementById("qa-input");
  const lang = () => (Q.lang() === "en" ? "en" : "ru");
  const plabel = (p) => (p ? (lang() === "en" ? p.label[1] : p.label[0]) : "");
  const phint = (p) => (p && p.hint ? (lang() === "en" ? p.hint[1] : p.hint[0]) : "");
  const provider = (id) => S.providers.find((p) => p.id === id);
  function tokenEstimate(text) {
    const v = String(text || "");
    const other = (v.match(/[\u0080-\u2fff]/g) || []).length;
    return Math.round((v.length - other) / 4 + other / 2.4) + 1;
  }
  const fmtTokens = (n) => (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k" : String(n || 0));

  async function loadProviders(force = false) {
    if (!force && S.providers.length && Date.now() - S.providersAt < 20000) return S.providers;
    try {
      const data = await Q.api("/api/mentions/providers" + (force ? "?refresh=1" : ""));
      S.providers = (data.providers || []).filter((p) => !HIDDEN.has(p.id));
      S.providersAt = Date.now();
    } catch (e) { /* MemuAI without the module: no menu */ }
    return S.providers;
  }

  /* «@…» before the caret: {start, end, text} — «@чат: договор аренды» (a category allows spaces), «@word». */
  function currentToken() {
    const el = input();
    if (!el || el.selectionStart !== el.selectionEnd) return null;
    const caret = el.selectionStart ?? el.value.length;
    const before = el.value.slice(0, caret);
    let m = before.match(/(?:^|\s)@([\p{L}\w-]{1,24}:[^\n@]{0,80})$/u);
    if (!m) m = before.match(/(?:^|\s)@([^\s@()]*)$/u);
    if (!m) return null;
    return { start: caret - m[1].length - 1, end: caret, text: m[1] };
  }
  function splitCategory(text) {
    const m = String(text || "").match(/^([\p{L}\w-]{1,24}):\s?(.*)$/su);
    if (!m) return { cat: "", q: text };
    const head = m[1].toLowerCase().replace(/ё/g, "е");
    const p = S.providers.find((x) => x.id === head || (x.aliases || []).some((a) => a.toLowerCase().replace(/ё/g, "е") === head));
    return p ? { cat: p.id, q: m[2] } : { cat: "", q: text };
  }

  function recent() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter((r) => r && r.provider && !HIDDEN.has(r.provider)); } catch (e) { return []; }
  }
  function pushRecent(item) {
    let rows = [];
    try { rows = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter((r) => r && r.provider); } catch (e) { rows = []; }
    const key = item.provider + ":" + JSON.stringify(item.ref ?? item.title);
    rows = rows.filter((r) => r.provider + ":" + JSON.stringify(r.ref ?? r.title) !== key);
    rows.unshift({ provider: item.provider, ref: item.ref, title: item.title, subtitle: item.subtitle || "", icon: item.icon || "", at: Date.now() });
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(rows.slice(0, 12))); } catch (e) {}
  }

  /* ------------------------------------------------------------------ panels (in the window's flow — it sizes itself) */
  Q.registerPanel("mentions-chips", (el) => {
    S.chipsEl = el; el.classList.add("qmn-chips-host");
    // a click on a chip keeps the caret in the question (typing right after it went nowhere)
    el.addEventListener("mousedown", (e) => { if (e.target.closest("button") && !e.target.closest(".qmn-preview")) e.preventDefault(); });
    el.addEventListener("click", onChipsClick);
  }, { slot: "top", order: -2, hidden: true });
  Q.registerPanel("mentions-menu", (el) => {
    S.menuEl = el; el.classList.add("qmn-menu-host");
    el.addEventListener("mousedown", (e) => e.preventDefault());       // keep the caret in the input
    el.addEventListener("click", onMenuClick);
  }, { slot: "top", order: -1, hidden: true });

  /* ------------------------------------------------------------------ menu */
  function schedule() {
    clearTimeout(S.timer);
    const tok = currentToken();
    if (!tok) { close(); return; }
    S.tok = tok;
    if (!S.open) render(tok, null, true);
    S.timer = setTimeout(() => runSearch(tok), tok.text ? 160 : 0);
  }
  async function runSearch(tok) {
    await loadProviders();
    if (!S.providers.length) { close(); return; }
    const { cat, q } = splitCategory(tok.text);
    const seq = ++S.seq;
    if (S.controller) S.controller.abort();
    let result = null;
    if (tok.text || cat) {
      const controller = new AbortController();
      S.controller = controller;
      render(tok, null, true);
      try {
        result = await Q.api("/api/mentions/search", { body: { q: tok.text, lang: lang(), mode: "quick", limit: 12 }, signal: controller.signal });
      } catch (e) {
        if (e.name === "AbortError") return;
        result = { items: [], error: e.message || String(e) };
      }
    }
    if (seq !== S.seq) return;
    render(tok, result, false, cat, q);
  }
  function rowsFor(tok, result, loading, cat) {
    const rows = [];
    if (!tok.text) {
      rows.push({ kind: "cats" });
      const rec = recent().slice(0, 4);
      if (rec.length) { rows.push({ section: t("Недавние", "Recent") }); rec.forEach((r) => rows.push({ ...r, kind: "item", recent: true })); }
      return rows;
    }
    if (loading && !result) { rows.push({ kind: "info", text: t("Ищу…", "Searching…") }); return rows; }
    if (!result) return rows;
    if (result.not_connected) {
      const p = result.not_connected;
      rows.push({ kind: "connect", title: t(`${plabel(p)} не подключён`, `${plabel(p)} is not connected`), service: p.service, icon: p.icon });
      return rows;
    }
    if (result.client && (result.category === "clipboard" || cat === "clipboard")) {
      rows.push({ kind: "item", provider: "clipboard", ref: "clipboard", title: t("Текст из буфера обмена", "Text from the clipboard"), icon: "clipboard" });
      return rows;
    }
    const cats = (result.categories || []).filter((c) => !HIDDEN.has(c.id)).slice(0, 4);
    if (cats.length && !result.category) rows.push({ kind: "catrow", cats });
    const items = (result.items || []).filter((r) => !HIDDEN.has(r.provider));
    if (items.length) {
      if (!result.category) rows.push({ section: t("Найдено", "Found") });
      items.forEach((r) => rows.push({ ...r, kind: "item" }));
    }
    (result.remote || []).filter((r) => !HIDDEN.has(r.provider)).slice(0, 5).forEach((r) => rows.push({ ...r, kind: r.disabled ? "connect" : "remote" }));
    if (!items.length && !(result.remote || []).length && !cats.length) {
      rows.push({ kind: "info", text: result.error ? result.error : t("Ничего не нашлось", "Nothing found") });
    }
    return rows;
  }
  const selectable = (r) => r && (r.kind === "item" || r.kind === "remote" || r.kind === "connect");
  function catsHtml(list) {
    return `<div class="qmn-cats">${list.map((p) => `<button type="button" class="qmn-cat${p.connected === false ? " off" : ""}" data-qmn-cat="${esc(p.id)}" title="${esc(phint(p))}">${icon(p.icon)}<span>${esc(plabel(p))}</span></button>`).join("")}</div>`;
  }
  function render(tok, result, loading, cat = "") {
    const el = S.menuEl;
    if (!el) return;
    S.rows = rowsFor(tok, result, loading, cat);
    if (!S.rows.some(selectable)) S.index = -1;
    else if (S.index < 0 || !selectable(S.rows[S.index])) S.index = S.rows.findIndex(selectable);
    const head = cat ? `<div class="qa-section qmn-head">${icon(provider(cat)?.icon || "at")}<span>${esc(plabel(provider(cat)))}</span><small>${esc(t("Backspace — все категории", "Backspace — all categories"))}</small></div>` : "";
    const html = S.rows.map((r, i) => {
      if (r.section) return `<div class="qa-section">${esc(r.section)}</div>`;
      if (r.kind === "cats") return `<div class="qa-section">${esc(t("Что можно упомянуть", "What you can mention"))}</div>` + catsHtml(S.providers.filter((p) => p.available !== false));
      if (r.kind === "catrow") return catsHtml(r.cats.map((c) => provider(c.id) || c));
      if (r.kind === "info") return `<div class="qa-hints qmn-info">${esc(r.text)}</div>`;
      const sub = r.kind === "connect" ? `<span class="qmn-connect" data-qmn-connect="${esc(r.service || r.connect || "")}">${esc(t("Подключить", "Connect"))}</span>`
        : (r.subtitle ? `<span class="qmn-sub">${esc(r.subtitle)}</span>` : "");
      const ic = r.recent ? "recent" : (r.kind === "remote" ? "search" : (r.icon || provider(r.provider)?.icon || "doc"));
      return `<button type="button" role="option" class="qa-row qmn-row ${i === S.index ? "active" : ""}${r.kind === "connect" ? " off" : ""}" data-qmn-i="${i}" aria-selected="${i === S.index}">
        <span class="ic">${icon(ic)}</span><span class="tx">${esc(r.title || "")}</span>${sub}</button>`;
    }).join("");
    el.innerHTML = `<div class="qmn-menu" role="listbox" aria-label="${esc(t("Упоминания", "Mentions"))}">${head}${html}</div>`;
    S.open = true;
    Q.showPanel("mentions-menu");
    const active = el.querySelector(".qmn-row.active");
    if (active) active.scrollIntoView({ block: "nearest" });
    Q.resize();
  }
  function close() {
    clearTimeout(S.timer);
    if (S.controller) { S.controller.abort(); S.controller = null; }
    S.seq += 1;
    if (!S.open) return;
    S.open = false; S.rows = []; S.index = -1;
    if (S.menuEl) S.menuEl.innerHTML = "";
    Q.hidePanel("mentions-menu");
  }
  function move(delta) {
    const n = S.rows.length;
    if (!n) return;
    let i = S.index;
    for (let k = 0; k < n; k += 1) { i = (i + delta + n) % n; if (selectable(S.rows[i])) break; }
    S.index = i;
    S.menuEl.querySelectorAll(".qmn-row").forEach((b) => b.classList.toggle("active", +b.dataset.qmnI === i));
    S.menuEl.querySelector(".qmn-row.active")?.scrollIntoView({ block: "nearest" });
  }
  function replaceToken(text) {
    const el = input(), tok = currentToken() || S.tok;
    if (!el || !tok) return;
    let before = el.value.slice(0, tok.start), after = el.value.slice(tok.end);
    if (!text && /\s$/.test(before) && /^\s/.test(after)) after = after.slice(1);      // «a @x b» → «a b»
    if (!text && !before.trim()) { before = ""; after = after.replace(/^\s+/, ""); }
    el.value = before + text + after;
    const at = (before + text).length;
    el.setSelectionRange(at, at);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  // «@чат: » — the provider's own alias in the window's language (the first label word could be another
  // provider's alias: «Файл MemuNote» → «файл» is the PC files one)
  function alias(p) {
    const en = lang() === "en";
    return (p.aliases || []).find((a) => (en ? /^[a-z]/i.test(a) : /^[а-яё]/i.test(a))) || p.aliases?.[0] || p.id;
  }
  function setCategory(id) {
    const p = provider(id);
    if (p) replaceToken("@" + alias(p) + ": ");
  }
  function onMenuClick(e) {
    const connect = e.target.closest("[data-qmn-connect]");
    if (connect) { e.stopPropagation(); openConnect(); return; }
    const cat = e.target.closest("[data-qmn-cat]");
    if (cat) { setCategory(cat.dataset.qmnCat); input()?.focus(); return; }
    const row = e.target.closest("[data-qmn-i]");
    if (row) choose(+row.dataset.qmnI);
  }
  function openConnect() {
    close();
    // the main window opens on Settings → Integrations (the same handoff the chat buttons use)
    Q.api("/api/quick/window", { body: { action: "handoff", handoff: { settings: "integrations" } } }).catch(() => {});
    Q.toast(t("Подключите сервис в MemuAI → Настройки → Интеграции", "Connect the service in MemuAI → Settings → Integrations"), "", 3600);
  }
  function choose(i) {
    const row = S.rows[i];
    if (!selectable(row)) return;
    if (row.kind === "connect") { openConnect(); return; }
    if (row.kind === "remote" || row.action === "category") {
      const p = provider(row.provider);
      if (p) replaceToken("@" + alias(p) + ": " + (row.query || ""));
      return;
    }
    replaceToken("");
    close();
    addChip(row);
  }

  /* ------------------------------------------------------------------ chips */
  const chipKey = (c) => c.provider + ":" + JSON.stringify(c.ref);
  async function addChip(row) {
    const chip = { provider: row.provider, ref: row.ref, title: row.title || "", subtitle: row.subtitle || "", icon: row.icon || provider(row.provider)?.icon || "doc",
      loading: true, text: "", tokens: 0 };
    if (S.chips.some((c) => chipKey(c) === chipKey(chip))) { renderChips(); return; }
    if (S.chips.length >= MAX_CHIPS) { Q.toast(t(`Не больше ${MAX_CHIPS} упоминаний`, `No more than ${MAX_CHIPS} mentions`), "err"); return; }
    S.chips.push(chip);
    pushRecent(row);
    renderChips();
    await resolveChip(chip);
    renderChips();
  }
  async function resolveChip(chip) {
    chip.loading = true; chip.error = "";
    try {
      if (chip.provider === "clipboard") {
        let text = "";
        try { text = await navigator.clipboard.readText(); } catch (e) { text = ""; }
        if (text) { Object.assign(chip, { text: text.slice(0, 200000), source: t("Буфер обмена", "Clipboard"), title: t("Буфер обмена", "Clipboard") }); chip.tokens = tokenEstimate(chip.text); return; }
      }
      const data = await Q.api("/api/mentions/resolve", { body: { items: [{ provider: chip.provider, ref: chip.ref, title: chip.title }], lang: lang() } });
      const b = (data.blocks || [])[0] || {};
      if (b.error) chip.error = b.error;
      else Object.assign(chip, { title: b.title || chip.title, source: b.source || "", text: b.text || "", tokens: b.tokens || tokenEstimate(b.text),
        truncated: !!b.truncated, note: b.note || "", url: b.url || "", label: b.label || "" });
    } catch (e) {
      chip.error = e.message || String(e);
    } finally {
      chip.loading = false;
    }
  }
  function renderChips() {
    const el = S.chipsEl;
    if (!el) return;
    if (!S.chips.length) { el.innerHTML = ""; S.preview = -1; Q.hidePanel("mentions-chips"); Q.resize(); return; }
    const total = S.chips.reduce((s, c) => s + (c.tokens || 0), 0);
    const chips = S.chips.map((c, i) => {
      const meta = c.loading ? "…" : (c.error ? "!" : "~" + fmtTokens(c.tokens) + (c.truncated ? " ✂" : ""));
      const tip = c.error ? c.error : `${c.source || plabel(provider(c.provider))}${c.note ? " · " + c.note : ""}`;
      return `<span class="qa-chip-sel qmn-chip${c.error ? " err" : ""}${c.loading ? " pending" : ""}${S.preview === i ? " on" : ""}" title="${esc(tip)}">
        <button type="button" class="qmn-chip-main" data-qmn-chip="${i}" aria-label="${esc(t("Показать, что получит модель", "Show what the model gets"))}"><span class="fav">${icon(c.icon)}</span><span class="tl">${esc(c.title)}</span><span class="n">${esc(meta)}</span></button>
        <button type="button" class="qa-icon" data-qmn-x="${i}" aria-label="${esc(t("Убрать упоминание", "Remove the mention"))}" title="${esc(t("Убрать", "Remove"))}">${icon("x")}</button></span>`;
    }).join("");
    let preview = "";
    const p = S.chips[S.preview];
    if (p && !p.loading) {
      preview = `<div class="qmn-preview"><div class="qmn-pv-head"><b>${esc(p.title)}</b><span>${esc(p.error ? t("ошибка", "error") : `${p.source || ""} · ~${fmtTokens(p.tokens)} ${t("токенов", "tokens")}${p.truncated ? " · " + t("обрезано", "truncated") : ""}`)}</span></div>
        <pre>${esc(p.error || (p.text.length > 4000 ? p.text.slice(0, 4000) + "\n…" : p.text))}</pre></div>`;
    }
    const sum = S.chips.length > 1 ? `<span class="qmn-total">~${fmtTokens(total)} ${esc(t("токенов", "tokens"))}</span>` : "";
    el.innerHTML = `<div class="qa-attach qmn-chips">${chips}${sum}</div>${preview}`;
    Q.showPanel("mentions-chips");
    Q.resize();
  }
  function onChipsClick(e) {
    const x = e.target.closest("[data-qmn-x]");
    if (x) { S.chips.splice(+x.dataset.qmnX, 1); S.preview = -1; renderChips(); input()?.focus(); return; }
    const c = e.target.closest("[data-qmn-chip]");
    if (c) { const i = +c.dataset.qmnChip; S.preview = S.preview === i ? -1 : i; renderChips(); input()?.focus(); }
  }

  /* ------------------------------------------------------------------ keyboard (capture: before quick.js) */
  document.addEventListener("keydown", (e) => {
    const el = input();
    if (e.target !== el || e.isComposing) return;
    if (S.open) {
      const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
      if (e.key === "ArrowDown") { stop(); move(1); return; }
      if (e.key === "ArrowUp") { stop(); move(-1); return; }
      if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Tab" && !e.shiftKey)) {
        if (S.index >= 0 && selectable(S.rows[S.index])) { stop(); choose(S.index); return; }
        const tok = currentToken();
        if (e.key === "Tab" || (tok && splitCategory(tok.text).cat)) { stop(); return; }   // «@память: …» found nothing — never sent as a question
        close();         // nothing to pick after a plain «@word»: Enter sends the question as typed
        return;
      }
      if (e.key === "Escape") { stop(); close(); return; }
      if (e.key === "Backspace") {
        const tok = currentToken();
        const sc = tok && splitCategory(tok.text);
        if (sc && sc.cat && !sc.q.trim()) { stop(); replaceToken("@"); return; }
      }
      return;
    }
    if (e.key === "Backspace" && !el.value && S.chips.length && el.selectionStart === 0) {
      e.preventDefault(); e.stopImmediatePropagation();
      S.chips.pop(); S.preview = -1; renderChips();
    }
  }, true);

  function bindInput() {
    const el = input();
    if (!el) return;
    el.addEventListener("input", schedule);
    el.addEventListener("click", () => { if (currentToken()) schedule(); else close(); });
    el.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input()) close(); }, 160));
  }
  bindInput();
  loadProviders();

  /* ------------------------------------------------------------------ the request */
  const wire = (c) => ({ provider: c.provider, ref: c.ref, title: c.title, source: c.source || "", text: c.text || "",
    truncated: !!c.truncated, tokens: c.tokens || 0 });
  // send() emits answer-start BEFORE it builds the request body: the chips leave the composer at once and ride in
  // S.pending until the body takes them (addRequestContext below).
  Q.on("answer-start", () => {
    const ok = S.chips.filter((c) => !c.loading && !c.error);
    S.pending = ok.length || S.earlier.length ? { items: ok.map(wire), earlier: S.earlier.slice() } : null;
    S.pendingChips = S.chips.slice();
    S.chips = []; S.preview = -1; close(); renderChips();
  });
  Q.addRequestContext("mentions", () => {
    const p = S.pending;
    if (!p) return null;
    S.pending = null; S.pendingChips = null;
    S.earlier = [...p.items, ...S.earlier.filter((b) => !p.items.some((x) => chipKey(x) === chipKey(b)))].slice(0, MAX_EARLIER);
    return { type: "mentions", items: p.items, earlier: p.earlier };
  });
  // a question that never reached the model (no model chosen, vision check) gives its chips back
  Q.on("answer-done", () => {
    if (S.pending && S.pendingChips) { S.chips = S.pendingChips; S.pending = null; S.pendingChips = null; renderChips(); }
  });
  Q.on("chat-new", () => { S.earlier = []; });
  Q.on("chat-open", () => { S.earlier = []; });
  window.addEventListener("focus", () => { if (Date.now() - S.providersAt > 20000) loadProviders(true); });

  window.MemuQuickMentions = { add: (row) => addChip(row), count: () => S.chips.length,  // Round 36 (fsearch): add — «Спросить ИИ о файле» from the PC search menu
     chips: () => S.chips.map((c) => ({ ...c })), earlier: () => S.earlier.slice(), close };
})();
