/* MemuAI — global search («Поиск везде», Round 29, r29-search).

   A command-palette overlay (Ctrl+K, the «search» shortcut, the app menu, Enter in the sidebar
   chat filter). Everything MemuAI keeps is searched as you type:

   * the backend index (app/search_index.py, /api/search): chats (Chat / Work / MemuCode, Telegram
     chats), Work projects and the files of saved projects, memory, prompts, schedules, documentation,
     panels, modules, the Quick Access history — and any source a module registers in Python;
   * client providers (this file): settings (jump to the exact row), commands, AI skills, agents —
     and any provider a module registers here (window.MemuSearch.registerProvider).

   Results are grouped, highlighted, keyboard-driven and previewed on the right; a chat result opens
   the chat scrolled to the exact message with the words highlighted and a small «found in this chat»
   navigator. Nothing here changes the sidebar chat filter (#chat-search keeps filtering the list).

   Public API (window.MemuSearch):
     open(query?, {type?})  close()  toggle()  isOpen()
     registerProvider({id, type, label: [ru, en], icon?: svg, search(q, ctx) → items | Promise<items>,
                       open?(item), preview?(item) → html})
         items: [{id, title, subtitle?, snippet?, score (0..1), ts?, open?: {...}, keywords?}]
     registerAction(name, fn(open, item))   — opener for backend results with open.action = name
     query(q, {types, filters, limit})      — the raw backend search (also used by tests)
*/
(function () {
  "use strict";
  if (window.MemuSearch) return;

  /* ------------------------------------------------------------------ basics */
  const isEn = () => (typeof PG_LANG !== "undefined" ? PG_LANG : localStorage.getItem("memu_lang")) === "en";
  const L = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $q = (sel, root = document) => root.querySelector(sel);
  const $qa = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reducedMotion = () => document.documentElement.dataset.motion === "reduced"
    || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const frame = () => new Promise((resolve) => requestAnimationFrame(() => resolve()));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function http(path, opts = {}) {
    const resp = await fetch(path, {
      method: opts.method || "GET", headers: { "Content-Type": "application/json" },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, signal: opts.signal,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw Object.assign(new Error(data.error || `HTTP ${resp.status}`), { status: resp.status });
    return data;
  }

  const SVG = {
    search: '<path d="m20 20-3.6-3.6"/><circle cx="11" cy="11" r="7"/>',
    chat: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/>',
    work: '<rect x="2.5" y="7" width="19" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    code: '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M13.5 5l-3 14"/>',
    telegram: '<path d="M21.5 4.5 2.8 11.7c-.9.4-.9 1.1 0 1.4l4.8 1.5 1.8 5.6c.2.6.9.8 1.4.3l2.6-2.4 5 3.7c.6.4 1.3.1 1.5-.6L22.9 5.9c.2-1-.5-1.7-1.4-1.4Z"/><path d="m7.6 14.6 10.3-6.5-7.9 7.6"/>',
    project: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    memory: '<path d="M9.5 3.5A3 3 0 0 0 7 8a3 3 0 0 0-1 5.8A3 3 0 0 0 9 18a3 3 0 0 0 3 2V5a2.5 2.5 0 0 0-2.5-1.5ZM14.5 3.5A3 3 0 0 1 17 8a3 3 0 0 1 1 5.8A3 3 0 0 1 15 18a3 3 0 0 1-3 2"/>',
    prompt: '<path d="M4 6h16M4 12h10M4 18h7"/><path d="m17 14 3 3-3 3"/>',
    skill: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/>',
    schedule: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    panel: '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/>',
    module: '<path d="M12 2.8 20.5 7.5v9L12 21.2 3.5 16.5v-9z"/><path d="m3.8 7.6 8.2 4.6 8.2-4.6M12 12.2v9"/>',
    character: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    agent: '<rect x="4" y="9" width="16" height="11" rx="3"/><path d="M12 9V5M9 14h.01M15 14h.01"/><circle cx="12" cy="4" r="1"/>',
    quick: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    doc: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5z"/><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19"/>',
    setting: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    command: '<path d="M9 6V5a3 3 0 1 0-3 3h1m2-2v12m0-12h6M9 18v1a3 3 0 1 1-3-3h1m2 2h6m0 0v1a3 3 0 1 0 3-3h-1m-2 2V6m0 0V5a3 3 0 1 1 3 3h-1"/>',
    recent: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
    filter: '<path d="M3 5h18l-7 8.5V19l-4 2v-7.5z"/>',
    close: '<path d="m6 6 12 12M18 6 6 18"/>',
    enter: '<path d="M20 5v7a3 3 0 0 1-3 3H5"/><path d="m9 11-4 4 4 4"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    pin: '<path d="m14 4 6 6-3 1-4 4-1 5-2-6-6-2 5-1 4-4z"/>',
    up: '<path d="m6 15 6-6 6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
    more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    sparkle: '<path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7z"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 21a7.5 7.5 0 0 1 15 0"/>',
    bot: '<rect x="4" y="9" width="16" height="11" rx="3"/><path d="M12 9V5M9 14h.01M15 14h.01"/>',
    keyboard: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 16h8"/>',
    layout: '<path d="M4 7h16M4 12h16M4 17h10"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  };
  const svg = (name, extra = "") => `<svg viewBox="0 0 24 24" aria-hidden="true" ${extra}>${SVG[name] || name || ""}</svg>`;

  /* ------------------------------------------------------------------ types */
  const TYPES = {
    chat: { icon: "chat", ru: "Чаты", en: "Chats", one: ["Чат", "Chat"] },
    work: { icon: "work", ru: "Work", en: "Work", one: ["Work", "Work"] },
    code: { icon: "code", ru: "MemuCode", en: "MemuCode", one: ["MemuCode", "MemuCode"] },
    project: { icon: "project", ru: "Проекты", en: "Projects", one: ["Проект", "Project"] },
    file: { icon: "file", ru: "Файлы проектов", en: "Project files", one: ["Файл", "File"] },
    memory: { icon: "memory", ru: "Память", en: "Memory", one: ["Память", "Memory"] },
    prompt: { icon: "prompt", ru: "Промпты", en: "Prompts", one: ["Промпт", "Prompt"] },
    skill: { icon: "skill", ru: "Скиллы", en: "Skills", one: ["Скилл", "Skill"] },
    schedule: { icon: "schedule", ru: "Планирование", en: "Schedules", one: ["Задача", "Task"] },
    panel: { icon: "panel", ru: "Панели", en: "Panels", one: ["Панель", "Panel"] },
    module: { icon: "module", ru: "Модули", en: "Modules", one: ["Модуль", "Module"] },
    character: { icon: "character", ru: "Персонажи", en: "Characters", one: ["Персонаж", "Character"] },
    agent: { icon: "agent", ru: "Мемесы", en: "Agents", one: ["Мемес", "Agent"] },
    quick: { icon: "quick", ru: "Быстрый доступ", en: "Quick Access", one: ["Быстрый доступ", "Quick Access"] },
    doc: { icon: "doc", ru: "Документация", en: "Documentation", one: ["Статья", "Article"] },
    setting: { icon: "setting", ru: "Настройки", en: "Settings", one: ["Настройка", "Setting"] },
    command: { icon: "command", ru: "Команды", en: "Commands", one: ["Команда", "Command"] },
  };
  // the type chips, in order; «telegram» is a filter over the chat types
  const CHIPS = ["all", "chat", "work", "code", "telegram", "file", "memory", "prompt", "skill", "schedule",
    "panel", "module", "character", "agent", "quick", "doc", "setting", "command"];
  const CHIP_TYPES = { telegram: ["chat", "work", "code"] };
  const typeLabel = (type) => { const t = TYPES[type]; return t ? (isEn() ? t.en : t.ru) : (state.custom[type] ? pick(state.custom[type].label) : type); };
  const pick = (pair) => (Array.isArray(pair) ? (isEn() ? pair[1] || pair[0] : pair[0] || pair[1]) : String(pair || ""));
  const typeIcon = (type) => (TYPES[type] ? svg(TYPES[type].icon) : (state.custom[type]?.icon ? state.custom[type].icon : svg("sparkle")));

  /* ------------------------------------------------------------------ state */
  const state = {
    root: null, open: false, q: "", chip: "all",
    filters: { date: "", from: "", to: "", role: "", model: "", folder: "", pinned: false },
    data: null, flat: [], active: 0, seq: 0, abort: null, timer: 0, previewTimer: 0, previewSeq: 0,
    previewCache: new Map(), recent: [], meta: null, providers: new Map(), actions: new Map(), custom: {},
    showPreview: true, lastFocus: null, findbar: null, popOpen: false, loading: false, moreIds: null,
  };

  /* ------------------------------------------------------------------ text helpers */
  function norm(text) { return String(text || "").toLowerCase().replace(/ё/g, "е"); }
  const EN_RU = { q: "й", w: "ц", e: "у", r: "к", t: "е", y: "н", u: "г", i: "ш", o: "щ", p: "з", "[": "х", "]": "ъ", a: "ф", s: "ы", d: "в", f: "а", g: "п", h: "р", j: "о", k: "л", l: "д", ";": "ж", "'": "э", z: "я", x: "ч", c: "с", v: "м", b: "и", n: "т", m: "ь", ",": "б", ".": "ю", "`": "ё" };
  const RU_EN = Object.fromEntries(Object.entries(EN_RU).map(([k, v]) => [v, k]));
  function swapLayout(text) {
    const s = String(text).toLowerCase();
    const latin = /[a-z]/.test(s), cyr = /[а-яё]/.test(s);
    if (latin && !cyr) return [...s].map((c) => EN_RU[c] || c).join("");
    if (cyr && !latin) return [...s].map((c) => RU_EN[c] || c).join("");
    return "";
  }
  const wordsOf = (text) => norm(text).match(/[\p{L}\p{N}]+/gu) || [];
  function lev1(a, b) {
    if (a === b) return true;
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    let i = 0; while (i < la && i < lb && a[i] === b[i]) i++;
    if (la === lb) return a.slice(i + 1) === b.slice(i + 1) || (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));
    return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
  }
  /** Client-side scoring for the small client sources: 0 = no match, ~1 = the title starts with the query. */
  function scoreText(query, title, extra = "") {
    const tokens = wordsOf(query);
    if (!tokens.length) return 0;
    const titleN = norm(title), titleW = wordsOf(title), restW = wordsOf(extra), restN = norm(extra);
    let total = 0;
    for (const tok of tokens) {
      let best = 0;
      if (titleW.includes(tok)) best = 1;
      else if (titleW.some((w) => w.startsWith(tok))) best = .85;
      else if (tok.length >= 3 && titleN.includes(tok)) best = .6;
      else if (restW.includes(tok)) best = .55;
      else if (restW.some((w) => w.startsWith(tok))) best = .45;
      else if (tok.length >= 3 && restN.includes(tok)) best = .3;
      else if (tok.length >= 4 && [...titleW, ...restW].some((w) => lev1(tok, w) || lev1(tok, w.slice(0, tok.length)))) best = .28;
      if (!best) return 0;
      total += best;
    }
    let score = total / tokens.length;
    const q = norm(query).trim();
    if (titleN.startsWith(q)) score = Math.min(1, score + .15);
    return score;
  }
  function clientScore(query, title, extra) {
    if (!norm(query).trim()) return .5;          // an empty query inside a section lists everything
    const direct = scoreText(query, title, extra);
    const swapped = swapLayout(query);
    const alt = swapped ? scoreText(swapped, title, extra) * .92 : 0;
    return Math.max(direct, alt);
  }
  /** Highlight ranges for the client sources (and the chat view): every query word as a word start. */
  function wordRegex(words) {
    const parts = [...new Set((words || []).map((w) => norm(w)).filter(Boolean))]
      .map((w) => (w.length > 5 ? w.slice(0, Math.max(4, w.length - 2)) : w))
      .sort((a, b) => b.length - a.length)
      .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!parts.length) return null;
    try { return new RegExp(`(?<![\\p{L}\\p{N}])(?:${parts.join("|")})[\\p{L}\\p{N}]*`, "giu"); } catch (e) { return null; }
  }
  function rangesFor(text, query) {
    const rx = wordRegex([...wordsOf(query), ...wordsOf(swapLayout(query))]);
    if (!rx || !text) return [];
    const folded = norm(text);
    const out = [];
    for (const m of folded.matchAll(rx)) { out.push([m.index, m[0].length]); if (out.length > 40) break; }
    return out;
  }
  /** Text with <mark> at [start, length] ranges, escaped. */
  function hl(text, ranges) {
    const value = String(text || "");
    if (!ranges || !ranges.length) return esc(value);
    let out = "", at = 0;
    for (const [start, length] of ranges.slice().sort((a, b) => a[0] - b[0])) {
      if (start < at || start >= value.length) continue;
      out += esc(value.slice(at, start)) + `<mark>${esc(value.slice(start, start + length))}</mark>`;
      at = start + length;
    }
    return out + esc(value.slice(at));
  }
  function when(ts) {
    const value = +ts || 0;
    if (!value) return "";
    const diff = Date.now() - value;
    const locale = isEn() ? "en" : "ru";
    try {
      const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
      const min = Math.round(diff / 60000);
      if (min < 1) return L("сейчас", "now");
      if (min < 60) return rtf.format(-min, "minute");
      const hours = Math.round(min / 60);
      if (hours < 24) return rtf.format(-hours, "hour");
      const days = Math.round(hours / 24);
      if (days < 7) return rtf.format(-days, "day");
      const date = new Date(value);
      return date.toLocaleDateString(locale, days < 300 ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
    } catch (e) { return new Date(value).toLocaleDateString(); }
  }
  function fullDate(ts) {
    const value = +ts || 0;
    if (!value) return "";
    try { return new Date(value).toLocaleString(isEn() ? "en" : "ru", { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return ""; }
  }
  const modelName = (id) => {
    if (!id) return "";
    try { if (typeof modelDisplayName === "function") return modelDisplayName(id); } catch (e) {}
    return String(id).replace(/^[a-z0-9]+-(?=.)/i, "");
  };

  /* ------------------------------------------------------------------ client providers */
  const SETTINGS_SNAPSHOT_KEY = "memu_search_settings_v1";
  function settingsSnapshot() {
    try {
      const raw = JSON.parse(localStorage.getItem(SETTINGS_SNAPSHOT_KEY) || "null");
      if (raw && raw.lang === (isEn() ? "en" : "ru") && Array.isArray(raw.rows)) return raw.rows;
    } catch (e) {}
    return [];
  }
  function captureSettings() {
    const K = window.MemuSettingsKit;
    if (!K || !document.querySelector("#panel .sx-shell")) return;
    try {
      K.search("a");                      // builds the kit's index of the rendered settings
      const index = K.state?.index || [];
      const rows = [];
      const seen = new Set();
      for (const entry of index) {
        if (!entry || entry.kind === "page" || !entry.label) continue;
        const key = `${entry.page}|${entry.sub || ""}|${entry.label}`;
        if (seen.has(key)) continue;
        seen.add(key);
        rows.push({ p: entry.page, s: entry.sub || "", l: String(entry.label).slice(0, 140), d: String(entry.desc || "").slice(0, 220),
          k: String(entry.keywords || "").slice(0, 200), c: String(entry.context || "").slice(0, 100), t: String(entry.subTitle || "").slice(0, 80), kind: entry.kind });
        if (rows.length >= 1500) break;
      }
      if (rows.length) localStorage.setItem(SETTINGS_SNAPSHOT_KEY, JSON.stringify({ lang: isEn() ? "en" : "ru", at: Date.now(), rows }));
    } catch (e) { /* the snapshot is a convenience */ }
  }
  function settingsPagesSafe() {
    try { return typeof settingsPages === "function" ? settingsPages() : []; } catch (e) { return []; }
  }
  function pageTitle(id) { return settingsPagesSafe().find((p) => p.id === id)?.title || id; }

  function settingsProvider(query) {
    const out = [];
    for (const page of settingsPagesSafe()) {
      const s = clientScore(query, page.title, `${page.desc || ""} ${page.keywords || ""}`);
      if (s) out.push({ id: `setting:page:${page.id}`, type: "setting", title: page.title, subtitle: L("Раздел настроек", "Settings page"),
        snippet: page.desc || "", score: s * 1.02, open: { action: "setting", page: page.id }, keywords: page.keywords });
    }
    for (const row of settingsSnapshot()) {
      const s = clientScore(query, row.l, `${row.d} ${row.k} ${row.c} ${row.t}`);
      if (s) {
        const path = [pageTitle(row.p), row.t, row.c].filter(Boolean).join(" › ");
        out.push({ id: `setting:${row.p}:${row.s}:${row.l}`, type: "setting", title: row.l, subtitle: path, snippet: row.d,
          score: s * .97, open: { action: "setting", page: row.p, sub: row.s, label: row.l } });
      }
    }
    return out;
  }

  function commandList() {
    const has = (name) => typeof window[name] === "function" || (typeof globalThis[name] === "function");
    const call = (fn) => () => { try { fn(); } catch (e) { console.warn("[search] command failed", e); } };
    const rows = [
      ["new-chat", "Новый чат", "New chat", "создать начать create start", "Ctrl+N", () => newChat(false)],
      ["temp-chat", "Временный чат", "Temporary chat", "incognito инкогнито", "Ctrl+Shift+T", () => newChat(true)],
      ["work", "Открыть Work", "Open Work", "проект project files файлы агент", "", () => $q("#nav-work")?.click()],
      ["code", "Открыть MemuCode", "Open MemuCode", "code код программирование coding", "", () => (typeof enterCodeMode === "function" ? enterCodeMode() : $q("#nav-code")?.click())],
      ["settings", "Настройки", "Settings", "preferences параметры options", "Ctrl+,", () => showSettings(true)],
      ["memory", "Память", "Memory", "факты facts remember помнить", "", () => showMemory(true)],
      ["schedule", "Планирование", "Schedules", "задачи tasks reminders напоминания cron", "", () => showSchedule(true)],
      ["skills", "Скиллы ИИ", "AI skills", "skills навыки библиотека library", "", () => showSkillsModal()],
      ["docs", "Документация", "Documentation", "help справка guide руководство docs", "F1", () => (window.MemuDocs ? MemuDocs.open() : null)],
      ["shortcuts", "Горячие клавиши", "Keyboard shortcuts", "hotkeys клавиши keys", "Ctrl+/", () => showShortcutsModal()],
      ["theme", "Сменить тему (светлая/тёмная)", "Switch theme (light/dark)", "тема theme dark light тёмная светлая ночь night", "", () => setTheme(S.theme === "dark" ? "light" : "dark")],
      ["focus", "Режим фокуса", "Focus mode", "фокус focus zen", "Ctrl+Shift+F", () => toggleFocusMode()],
      ["sidebar", "Показать или скрыть боковую панель", "Toggle sidebar", "sidebar боковая панель", "Ctrl+B", () => toggleSidebar()],
      ["export", "Экспортировать текущий чат", "Export current chat", "export экспорт сохранить save markdown", "Ctrl+Shift+S", () => exportCurrentChat()],
      ["models", "Выбрать модель", "Choose a model", "model модель нейросеть llm", "Ctrl+M", () => $q("#model-dd-btn")?.click()],
      ["live", "Голосовой разговор Live", "Live voice call", "voice голос звонок call live", "", () => openLiveCall()],
      ["panels", "Добавить панель", "Add a panel", "panel панель виджет widget", "", () => window.MemuPanels?.openGallery?.()],
      ["design", "Внешний вид и дизайн", "Appearance and design", "дизайн design theme тема внешний вид appearance accent акцент", "", () => openSettingsPage("appearance")],
      ["quick", "Настройки быстрого доступа", "Quick Access settings", "alt space быстрый доступ quick", "", () => openSettingsPage("quick")],
      ["reindex", "Перестроить поисковый индекс", "Rebuild the search index", "search index поиск индекс reindex", "", () => reindex()],
      ["reload", "Обновить интерфейс", "Refresh interface", "reload перезагрузить refresh", "Ctrl+R", () => location.reload()],
    ];
    return rows.map(([id, ru, en, kw, keys, fn]) => ({ id, title: isEn() ? en : ru, other: isEn() ? ru : en, kw, keys, run: call(fn) }))
      .filter((row) => row.id !== "live" || typeof openLiveCall === "function");
  }
  function commandProvider(query) {
    return commandList().map((c) => {
      const s = clientScore(query, c.title, `${c.other} ${c.kw}`);
      return s ? { id: `command:${c.id}`, type: "command", title: c.title, subtitle: c.keys, snippet: "", score: s * .95,
        open: { action: "command", id: c.id }, keys: c.keys } : null;
    }).filter(Boolean);
  }
  function skillsProvider(query) {
    let skills = [];
    try { skills = typeof getSkills === "function" ? getSkills() : []; } catch (e) { skills = []; }
    return skills.map((sk) => {
      // r29d-chat4: built-in skills show their Russian name/description in the Russian UI (skillUi, as the
      // skills library does); the other language still matches, so «debugging» finds «Отладка».
      const ui = (field) => (typeof skillUi === "function" ? skillUi(sk, field) : "") || "";
      const title = ui("name") || sk.name || sk.title || sk.id;
      const desc = ui("desc") || sk.description || sk.desc || "";
      const other = [sk.name, sk.name_ru, sk.desc, sk.desc_ru].filter((v) => v && v !== title && v !== desc).join(" ");
      const s = clientScore(query, title, `${desc} ${other} ${sk.prompt || sk.text || ""}`.slice(0, 3000));
      return s ? { id: `skill:${sk.id}`, type: "skill", title, subtitle: sk.custom ? L("Свой скилл", "Custom skill") : "", snippet: desc,
        score: s * .9, open: { action: "skill", id: sk.id, name: title }, body: sk.prompt || sk.text || "" } : null;
    }).filter(Boolean);
  }
  function agentsProvider(query) {
    let memes = [];
    try { memes = typeof loadMemes === "function" ? loadMemes() : []; } catch (e) { memes = []; }
    return memes.map((m) => {
      const s = clientScore(query, m.name || "", `${m.description || ""} ${m.prompt || ""}`.slice(0, 3000));
      return s ? { id: `agent:${m.id}`, type: "agent", title: m.name || L("Без имени", "Untitled"), snippet: m.description || "",
        score: s * .9, open: { action: "agent", id: m.id }, body: m.prompt || "" } : null;
    }).filter(Boolean);
  }
  const BUILTIN_CLIENT = [
    { id: "settings", type: "setting", search: settingsProvider },
    { id: "commands", type: "command", search: commandProvider },
    { id: "skills", type: "skill", search: skillsProvider },
    { id: "agents", type: "agent", search: agentsProvider },
  ];

  /* ------------------------------------------------------------------ DOM */
  function build() {
    if (state.root) return state.root;
    const root = document.createElement("div");
    root.className = "msx-root";
    root.hidden = true;
    root.innerHTML = `
      <div class="msx-backdrop" data-msx-close></div>
      <section class="msx-panel" role="dialog" aria-modal="true" aria-labelledby="msx-title">
        <h2 id="msx-title" class="msx-sr"></h2>
        <div class="msx-head">
          <span class="msx-glyph">${svg("search")}</span>
          <input class="msx-input" id="msx-input" type="text" autocomplete="off" spellcheck="false" role="combobox"
            aria-autocomplete="list" aria-controls="msx-list" aria-expanded="true">
          <span class="msx-progress" aria-hidden="true"></span>
          <button type="button" class="msx-clear" data-msx-clear hidden>${svg("close")}</button>
          <kbd class="msx-esc" data-msx-close>Esc</kbd>
        </div>
        <div class="msx-parsed" hidden></div>
        <div class="msx-bar">
          <div class="msx-chips" role="tablist"></div>
          <button type="button" class="msx-filter-btn" data-msx-filters aria-haspopup="dialog" aria-expanded="false">${svg("filter")}<span></span><b hidden></b></button>
        </div>
        <div class="msx-active" hidden></div>
        <div class="msx-body">
          <div class="msx-list" id="msx-list" role="listbox"></div>
          <aside class="msx-preview" aria-live="polite"></aside>
        </div>
        <footer class="msx-foot">
          <span class="msx-keys"></span>
          <span class="msx-stats"></span>
          <button type="button" class="msx-menu-btn" data-msx-menu aria-haspopup="menu">${svg("more")}</button>
        </footer>
        <div class="msx-pop msx-filters-pop" hidden></div>
        <div class="msx-pop msx-menu-pop" hidden role="menu"></div>
        <div class="msx-pop msx-more-pop" hidden role="menu"></div>
      </section>`;
    document.body.appendChild(root);
    state.root = root;
    const input = $q(".msx-input", root);
    input.addEventListener("input", () => { state.q = input.value; scheduleSearch(); });
    input.addEventListener("keydown", onKey);
    root.addEventListener("keydown", (event) => {
      // the palette owns the keyboard while it is open (the app must not stop a reply on Esc etc.)
      if (event.target !== input) onKey(event);
      if (event.key === "Escape" || (event.ctrlKey && /^Key[KP]$/.test(event.code))) event.stopPropagation();
    });
    root.addEventListener("click", onClick);
    root.addEventListener("mousemove", (event) => {
      const row = event.target.closest(".msx-item");
      if (!row || state.pointerFrozen) return;
      const index = +row.dataset.i;
      if (index !== state.active) setActive(index, false);
    });
    $q(".msx-list", root).addEventListener("scroll", () => {
      state.pointerFrozen = true;
      clearTimeout(state.freezeTimer);
      state.freezeTimer = setTimeout(() => { state.pointerFrozen = false; }, 140);
    }, { passive: true });
    return root;
  }

  function texts() {
    const root = state.root;
    $q("#msx-title", root).textContent = L("Поиск по MemuAI", "Search MemuAI");
    $q(".msx-input", root).placeholder = L("Искать в чатах, Work, памяти, настройках, документации…", "Search chats, Work, memory, settings, docs…");
    $q(".msx-input", root).setAttribute("aria-label", L("Поиск по MemuAI", "Search MemuAI"));
    $q("[data-msx-clear]", root).setAttribute("aria-label", L("Очистить", "Clear"));
    $q(".msx-esc", root).title = L("Закрыть", "Close");
    $q("[data-msx-filters] span", root).textContent = L("Фильтры", "Filters");
    $q("[data-msx-menu]", root).setAttribute("aria-label", L("Параметры поиска", "Search options"));
    $q("[data-msx-menu]", root).title = L("Параметры поиска", "Search options");
    $q(".msx-keys", root).innerHTML = [
      [`<kbd>↑</kbd><kbd>↓</kbd>`, L("выбор", "select")], [`<kbd>↵</kbd>`, L("открыть", "open")],
      [`<kbd>Tab</kbd>`, L("раздел", "section")],
    ].map(([k, t]) => `<span>${k}<em>${esc(t)}</em></span>`).join("");
  }

  /* ------------------------------------------------------------------ open / close */
  function open(query, opts = {}) {
    build(); texts();
    const root = state.root;
    if (!state.open) {
      state.lastFocus = document.activeElement;
      root.hidden = false;
      root.classList.remove("msx-leaving");
      void root.offsetWidth;
      root.classList.add("msx-visible");
      state.open = true;
      document.documentElement.classList.add("msx-open");
      http("/api/search/touch", { method: "POST", body: {} }).catch(() => {});
      loadMeta(); loadRecent();
    }
    if (opts.type && (CHIPS.includes(opts.type) || state.custom[opts.type])) state.chip = opts.type;
    const input = $q(".msx-input", root);
    if (typeof query === "string") { input.value = query; state.q = query; }
    renderChips(); renderActiveFilters();
    setTimeout(() => { input.focus({ preventScroll: true }); input.select(); }, 0);
    runSearch(true);
  }
  function close(restoreFocus = true) {
    if (!state.open) return;
    state.open = false;
    closePops();
    const root = state.root;
    root.classList.remove("msx-visible");
    root.classList.add("msx-leaving");
    document.documentElement.classList.remove("msx-open");
    setTimeout(() => { if (!state.open) { root.hidden = true; root.classList.remove("msx-leaving"); } }, reducedMotion() ? 0 : 160);
    if (state.abort) state.abort.abort();
    if (restoreFocus && state.lastFocus && state.lastFocus.isConnected) {
      try { state.lastFocus.focus({ preventScroll: true }); } catch (e) {}
    }
  }
  const toggle = (q) => (state.open ? close() : open(q));

  /* ------------------------------------------------------------------ meta / recent */
  async function loadMeta() {
    try { state.meta = await http(`/api/search/meta?lang=${isEn() ? "en" : "ru"}`); } catch (e) { state.meta = null; }
    if (state.open) { renderChips(); if (state.popOpen === "filters") renderFiltersPop(); }
  }
  async function loadRecent() {
    try { const data = await http("/api/search/recent"); state.recent = data.items || []; state.remember = data.remember_recent !== false; } catch (e) { state.recent = []; }
    if (state.open && !state.q.trim() && !hasFilters()) render();
  }
  function rememberQuery(q) {
    const value = String(q || "").trim();
    if (value.length < 2) return;
    http("/api/search/recent", { method: "POST", body: { q: value } }).then((d) => { state.recent = d.items || state.recent; }).catch(() => {});
  }

  /* ------------------------------------------------------------------ filters */
  function hasFilters() {
    const f = state.filters;
    return !!(f.date || f.role || f.model || f.folder || f.pinned);
  }
  function dateRange() {
    const f = state.filters, now = Date.now(), day = 86400000;
    const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
    if (f.date === "today") return [midnight.getTime(), now + 1];
    if (f.date === "week") return [now - 7 * day, now + 1];
    if (f.date === "month") return [now - 31 * day, now + 1];
    if (f.date === "year") return [now - 366 * day, now + 1];
    if (f.date === "custom") {
      const from = f.from ? new Date(f.from + "T00:00:00").getTime() : null;
      const to = f.to ? new Date(f.to + "T23:59:59").getTime() : null;
      return [from, to];
    }
    return [null, null];
  }
  function requestBody() {
    const f = state.filters;
    const [from, to] = dateRange();
    const chip = state.chip;
    let types = [];
    if (chip !== "all") types = CHIP_TYPES[chip] || [chip];
    const filters = {};
    if (from) filters.date_from = from;
    if (to) filters.date_to = to;
    if (f.role) filters.role = f.role;
    if (f.model) filters.model = f.model;
    if (f.folder) filters.folder = f.folder;
    if (f.pinned) filters.pinned = true;
    if (chip === "telegram") filters.telegram = true;
    return { q: state.q, types, filters, limit: 60, per_group: 3, lang: isEn() ? "en" : "ru" };
  }
  const DATE_OPTIONS = () => [["", L("За всё время", "Any time")], ["today", L("Сегодня", "Today")], ["week", L("7 дней", "Last 7 days")],
    ["month", L("30 дней", "Last 30 days")], ["year", L("Год", "Last year")], ["custom", L("Период…", "Custom…")]];
  function renderFiltersPop() {
    const pop = $q(".msx-filters-pop", state.root);
    const f = state.filters, meta = state.meta || {};
    const models = meta.models || [];
    const folders = meta.folders || [];
    const seg = (name, rows, value) => `<div class="msx-seg" role="radiogroup">${rows.map(([v, label]) =>
      `<button type="button" role="radio" aria-checked="${value === v}" class="${value === v ? "on" : ""}" data-msx-set="${name}" data-v="${esc(v)}">${esc(label)}</button>`).join("")}</div>`;
    pop.innerHTML = `
      <div class="msx-pop-head"><b>${esc(L("Фильтры", "Filters"))}</b><button type="button" class="msx-link" data-msx-reset ${hasFilters() ? "" : "disabled"}>${esc(L("Сбросить", "Reset"))}</button></div>
      <label class="msx-field"><span>${svg("calendar")}${esc(L("Дата", "Date"))}</span>${seg("date", DATE_OPTIONS(), f.date)}</label>
      <div class="msx-dates" ${f.date === "custom" ? "" : "hidden"}>
        <label>${esc(L("с", "from"))}<input type="date" data-msx-date="from" value="${esc(f.from)}"></label>
        <label>${esc(L("по", "to"))}<input type="date" data-msx-date="to" value="${esc(f.to)}"></label>
      </div>
      <label class="msx-field"><span>${svg("user")}${esc(L("Автор сообщения", "Message author"))}</span>${seg("role", [["", L("Любой", "Anyone")], ["user", L("Я", "Me")], ["assistant", L("ИИ", "AI")]], f.role)}</label>
      <label class="msx-field"><span>${svg("bot")}${esc(L("Модель", "Model"))}</span>
        <select data-msx-select="model" data-no-search><option value="">${esc(L("Любая", "Any"))}</option>${models.map((m) => `<option value="${esc(m)}" ${f.model === m ? "selected" : ""}>${esc(modelName(m))}</option>`).join("")}</select></label>
      <label class="msx-field"><span>${svg("folder")}${esc(L("Папка", "Folder"))}</span>
        <select data-msx-select="folder" data-no-search><option value="">${esc(L("Любая", "Any"))}</option>${folders.map((x) => `<option value="${esc(x.id)}" ${f.folder === x.id ? "selected" : ""}>${esc(x.name)} · ${esc(x.mode === "code" ? "MemuCode" : x.mode === "work" ? "Work" : L("Чат", "Chat"))}</option>`).join("")}</select></label>
      <label class="msx-check"><input type="checkbox" data-msx-pinned ${f.pinned ? "checked" : ""}><span>${svg("pin")}${esc(L("Только закреплённые чаты", "Pinned chats only"))}</span></label>
      <p class="msx-pop-note">${esc(L("Автор, модель, папка и закрепление относятся к чатам.", "Author, model, folder and pinned apply to chats."))}</p>`;
    pop.querySelectorAll("select").forEach((el) => { el.dataset.pretty = "1"; });
  }
  function renderActiveFilters() {
    const host = $q(".msx-active", state.root);
    const f = state.filters;
    const pills = [];
    if (f.date) {
      const label = f.date === "custom" ? `${f.from || "…"} — ${f.to || "…"}` : DATE_OPTIONS().find((o) => o[0] === f.date)?.[1];
      pills.push(["date", svg("calendar") + esc(label)]);
    }
    if (f.role) pills.push(["role", svg("user") + esc(f.role === "user" ? L("От меня", "From me") : L("От ИИ", "From AI"))]);
    if (f.model) pills.push(["model", svg("bot") + esc(modelName(f.model))]);
    if (f.folder) pills.push(["folder", svg("folder") + esc((state.meta?.folders || []).find((x) => x.id === f.folder)?.name || f.folder)]);
    if (f.pinned) pills.push(["pinned", svg("pin") + esc(L("Закреплённые", "Pinned"))]);
    host.hidden = !pills.length;
    host.innerHTML = pills.map(([key, html]) => `<button type="button" class="msx-pill" data-msx-unset="${key}" title="${esc(L("Убрать фильтр", "Remove filter"))}">${html}${svg("close", 'class="x"')}</button>`).join("")
      + (pills.length > 1 ? `<button type="button" class="msx-link" data-msx-reset>${esc(L("Сбросить всё", "Clear all"))}</button>` : "");
    const btn = $q("[data-msx-filters]", state.root);
    const badge = btn.querySelector("b");
    badge.hidden = !pills.length; badge.textContent = String(pills.length);
    btn.classList.toggle("on", !!pills.length);
  }
  function setFilter(key, value) {
    state.filters[key] = value;
    if (key === "date" && value !== "custom") { state.filters.from = ""; state.filters.to = ""; }
    renderActiveFilters();
    if (state.popOpen === "filters") renderFiltersPop();
    runSearch(true);
  }

  /* ------------------------------------------------------------------ chips */
  // r30: one quiet row — the core sections, or (while searching) only the sections that have results; the rest live under «Ещё»
  const CORE_CHIPS = ["all", "chat", "work", "code", "doc", "setting", "command"];
  function chipList() {
    const counts = {};
    (state.meta?.types || []).forEach((row) => { counts[row.type] = row.count; });
    const present = new Set(Object.keys(counts));
    const client = new Set(["setting", "command", "skill", "agent", ...Object.keys(state.custom)]);
    return CHIPS.filter((id) => id === "all" || id === state.chip || present.has(id) || client.has(id)
      || ["chat", "work", "code"].includes(id) || (id === "telegram" && present.has("chat")))
      .concat(Object.keys(state.custom).filter((t) => !CHIPS.includes(t)));
  }
  function chipCounts() {
    const out = {};
    if (!state.data || state.chip !== "all") return out;
    for (const g of state.data.groups || []) out[g.type] = (out[g.type] || 0) + (g.server ? g.items.length : (g.total || g.items.length));
    return out;
  }
  function chipLabel(id) { return id === "all" ? L("Всё", "All") : id === "telegram" ? "Telegram" : typeLabel(id); }
  function chipIcon(id) { return id === "all" ? svg("layout") : id === "telegram" ? svg("telegram") : typeIcon(id); }
  function visibleChips() {
    const all = chipList();
    const counts = chipCounts();
    const found = Object.keys(counts);
    let shown = found.length ? all.filter((id) => id === "all" || counts[id]) : all.filter((id) => CORE_CHIPS.includes(id));
    if (!shown.includes(state.chip)) shown.push(state.chip);
    shown = all.filter((id) => shown.includes(id));
    return { shown, more: all.filter((id) => !shown.includes(id)), counts };
  }
  function renderChips() {
    const host = $q(".msx-chips", state.root);
    const { shown, counts } = visibleChips();
    state.chipBase = { shown, counts, all: chipList() };
    fitChips(host);
    watchChips(host);
    if (state.popOpen === "more") renderMorePop();
  }
  // r30: wider chips (Terminal's [ brackets ], Brutal) or a narrow window — the last sections fold into «Ещё»
  // instead of starting a second line; «Всё» and the selected section always stay. The row is nowrap (CSS), so
  // nothing can wrap even for a frame; the fit is measured with «Ещё» itself in the row and redone whenever the
  // row's width, the fonts (a design's own font loads late) or the design/density/zoom on <html> change.
  function drawChips(host, shown) {
    const { counts, all } = state.chipBase;
    const more = all.filter((id) => !shown.includes(id));
    state.moreIds = more;
    const html = shown.map((id) => {
      const on = state.chip === id;
      const n = id === "all" ? 0 : counts[id] || 0;
      return `<button type="button" role="tab" aria-selected="${on}" class="msx-chip ${on ? "on" : ""}" data-msx-chip="${esc(id)}" data-type="${esc(id)}"><span>${esc(chipLabel(id))}</span>${n ? `<small>${n}</small>` : ""}</button>`;
    }).join("") + (more.length ? `<button type="button" class="msx-chip msx-more" data-msx-more aria-haspopup="menu" aria-expanded="${state.popOpen === "more"}"><span>${esc(L("Ещё", "More"))}</span>${svg("chevron")}</button>` : "");
    if (host.__msxHtml === html && host.childElementCount) return;   // unchanged — keep the nodes (hover, focus, a click in flight)
    host.__msxHtml = html;
    host.innerHTML = html;
  }
  function chipsOverflow(host) {
    const k = host.children;
    if (!k.length || !host.clientWidth) return false;
    const box = host.getBoundingClientRect();
    const last = k[k.length - 1].getBoundingClientRect();
    // a wrapped row (old CSS / a design override) or a chip past the right edge both count
    return last.right > box.right + 0.5 || k[k.length - 1].offsetTop > k[0].offsetTop + 2;
  }
  function fitChips(host) {
    host = host || (state.root && $q(".msx-chips", state.root));
    if (!host || !state.chipBase) return;
    const focused = host.contains(document.activeElement) ? document.activeElement.dataset.msxChip || (document.activeElement.hasAttribute("data-msx-more") ? "#more" : "") : "";
    const shown = state.chipBase.shown.slice();
    drawChips(host, shown);
    if (host.clientWidth) {            // hidden (closed panel): nothing to measure — the observer refits on show
      while (chipsOverflow(host)) {
        let i = shown.length - 1;
        while (i >= 0 && (shown[i] === "all" || shown[i] === state.chip)) i--;
        if (i < 0) break;
        shown.splice(i, 1);
        drawChips(host, shown);
      }
    }
    if (focused) $q(focused === "#more" ? "[data-msx-more]" : `[data-msx-chip="${CSS.escape(focused)}"]`, host)?.focus({ preventScroll: true });
    if (state.popOpen === "more") renderMorePop();
  }
  function watchChips(host) {
    if (state.chipWatch === host) return;
    state.chipWatch = host;
    let raf = 0, lastW = -1;
    const later = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; if (state.open) fitChips(host); }); };
    if (window.ResizeObserver) new ResizeObserver(() => { const w = host.clientWidth; if (w !== lastW) { lastW = w; later(); } }).observe(host);
    else window.addEventListener("resize", later);
    if (document.fonts) {
      document.fonts.ready.then(later).catch(() => {});
      document.fonts.addEventListener?.("loadingdone", later);
    }
    new MutationObserver(later).observe(document.documentElement, { attributes: true, attributeFilter: ["data-design", "data-density", "data-theme", "data-msg-size", "data-radius", "data-contrast", "class", "lang", "style"] });
  }
  function renderMorePop() {
    const pop = $q(".msx-more-pop", state.root);
    const more = state.moreIds || visibleChips().more;
    pop.innerHTML = more.map((id) => `<button type="button" role="menuitem" class="msx-menu-item" data-msx-chip="${esc(id)}">${chipIcon(id)}<span>${esc(chipLabel(id))}</span></button>`).join("");
    const btn = $q("[data-msx-more]", state.root);
    if (btn) {
      const panel = $q(".msx-panel", state.root).getBoundingClientRect(), r = btn.getBoundingClientRect();
      pop.style.top = `${Math.round(r.bottom - panel.top + 6)}px`;
      pop.style.left = `${Math.round(Math.max(12, Math.min(r.left - panel.left, panel.width - 232)))}px`;
    }
  }
  function setChip(id) {
    state.chip = id;
    if (state.popOpen === "more") closePops();
    renderChips();
    runSearch(true);
  }
  function cycleChip(step) {
    // Tab walks every section, including the ones under «Ещё»
    const ids = chipList();
    const at = ids.indexOf(state.chip);
    setChip(ids[(at + step + ids.length) % ids.length]);
  }

  /* ------------------------------------------------------------------ searching */
  function scheduleSearch() {
    clearTimeout(state.timer);
    $q("[data-msx-clear]", state.root).hidden = !state.q;
    state.timer = setTimeout(() => runSearch(false), state.q.length <= 1 ? 40 : 70);
  }
  async function runSearch(resetActive) {
    if (!state.root) return;
    const seq = ++state.seq;
    const q = state.q.trim();
    $q("[data-msx-clear]", state.root).hidden = !state.q;
    if (!q && !hasFilters() && state.chip === "all") {
      state.data = null;
      if (state.abort) state.abort.abort();
      setLoading(false);
      render(resetActive);
      renderChips();
      return;
    }
    if (state.abort) state.abort.abort();
    const controller = new AbortController();
    state.abort = controller;
    setLoading(true);
    const body = requestBody();
    const clientPromise = runClient(q);
    let server = null;
    const wantServer = state.chip === "all" || !isClientOnly(state.chip);
    try {
      if (wantServer) server = await http("/api/search", { method: "POST", body, signal: controller.signal });
    } catch (e) {
      if (e.name === "AbortError") return;
      server = { error: e.message, items: [], groups: [], total: 0 };
    }
    const client = await clientPromise;
    if (seq !== state.seq) return;
    setLoading(false);
    state.data = merge(server, client, q);
    render(resetActive || true);
    renderChips();
  }
  function isClientOnly(chip) {
    return ["setting", "command", "skill", "agent"].includes(chip) || (state.providers.has(chip) && !(state.meta?.types || []).some((t) => t.type === chip));
  }
  async function runClient(q) {
    if ((!q && state.chip === "all") || hasFilters() || ["chat", "work", "code", "telegram", "file", "project", "memory", "prompt", "schedule", "panel", "module", "quick", "doc"].includes(state.chip)) return [];
    const wanted = state.chip === "all" ? null : state.chip;
    const sources = [...BUILTIN_CLIENT, ...state.providers.values()].filter((p) => !wanted || p.type === wanted);
    const ctx = { lang: isEn() ? "en" : "ru", score: clientScore, ranges: rangesFor };
    const out = [];
    await Promise.all(sources.map(async (p) => {
      try {
        const rows = await Promise.race([Promise.resolve(p.search(q, ctx)), sleep(600).then(() => [])]);
        (rows || []).forEach((row) => { if (row && row.title) out.push({ ...row, type: row.type || p.type, _provider: p }); });
      } catch (e) { /* a broken provider must not break search */ }
    }));
    return out;
  }
  function merge(server, client, q) {
    const groups = [];
    (server?.groups || []).forEach((g, i) => groups.push({ type: g.type, label: Array.isArray(g.label) ? pick(g.label) : typeLabel(g.type),
      items: g.items, rank: 1 - i * .08, server: true }));
    const byType = {};
    client.sort((a, b) => (b.score || 0) - (a.score || 0)).forEach((row) => { (byType[row.type] ||= []).push(row); });
    Object.entries(byType).forEach(([type, rows]) => {
      const limit = state.chip === type ? 60 : 6;
      const items = rows.slice(0, limit).map((row) => ({
        ...row, client: true, title_highlights: row.title_highlights || rangesFor(row.title, q),
        highlights: row.highlights || rangesFor(row.snippet || "", q), meta: row.meta || {}, hits: [],
      }));
      const best = rows[0]?.score || 0;
      // a settings row or a command whose title starts with the query goes first, a weak match last
      const rank = best >= .95 ? 1.05 : best >= .8 ? .86 : best >= .5 ? .55 : .2;
      groups.push({ type, label: typeLabel(type), items, rank, total: rows.length });
    });
    groups.sort((a, b) => b.rank - a.rank);
    return { server, groups, q, total: (server?.total || 0) + client.length };
  }
  function setLoading(on) {
    state.loading = on;
    state.root?.classList.toggle("msx-loading", on);
  }

  /* ------------------------------------------------------------------ rendering */
  function render(resetActive = false) {
    const list = $q(".msx-list", state.root);
    state.flat = [];
    if (resetActive) state.active = 0;
    let html = "";
    const data = state.data;
    renderParsed();
    if (!data) {
      html = emptyState();
    } else if (!data.groups.length) {
      html = noResults();
    } else {
      for (const group of data.groups) {
        const count = group.server ? group.items.length : (group.total || group.items.length);
        html += `<div class="msx-group" role="group" aria-label="${esc(group.label)}">
          <div class="msx-group-h" data-type="${esc(group.type)}"><span>${esc(group.label)}</span><small>${count}</small>
          ${state.chip === "all" && data.groups.length > 1 ? `<button type="button" class="msx-link" data-msx-chip="${esc(group.type)}">${esc(L("Только это", "Only these"))}</button>` : ""}</div>`;
        for (const item of group.items) html += itemHtml(item);
        html += `</div>`;
      }
    }
    list.innerHTML = html;
    state.root.classList.toggle("msx-blank", !state.flat.length);
    if (state.active >= state.flat.length) state.active = Math.max(0, state.flat.length - 1);
    setActive(state.active, true);
    renderStats();
  }
  function register(entry) {
    const i = state.flat.length;
    state.flat.push(entry);
    return i;
  }
  function badges(item) {
    const m = item.meta || {};
    const out = [];
    if (m.pinned) out.push(`<span class="msx-badge" title="${esc(L("Закреплён", "Pinned"))}">${svg("pin")}</span>`);
    if (m.telegram) out.push(`<span class="msx-badge tg" title="Telegram">${svg("telegram")}</span>`);
    if (item.type === "module" && m.enabled === undefined) out.push(`<span class="msx-tag">${esc(L("выключен", "off"))}</span>`);
    return out.join("");
  }
  function roleTag(meta) {
    if (!meta?.role) return "";
    // Round 35 (branches): a hit in a version that is not shown in the chat right now
    const other = meta.branch === "other" ? `<span class="msx-role msx-branch" title="${esc(L("Сообщение из другой версии ответа или ветки — откроется её ветка", "A message from another answer version or branch — its branch opens"))}">${esc(L("другая версия", "other version"))}</span>` : "";
    return other + (meta.role === "user" ? `<span class="msx-role you">${esc(L("Вы", "You"))}</span>`
      : `<span class="msx-role ai">${esc(modelName(meta.model) || L("ИИ", "AI"))}</span>`);
  }
  function itemHtml(item) {
    const i = register({ item, open: item.open, kind: "item" });
    const hits = item.hits || [];
    const firstIsLead = hits.length && hits[0].snippet === item.snippet;
    const sub = [];
    for (const hit of hits.slice(firstIsLead ? 1 : 0, 3)) {
      const j = register({ item, hit, open: hit.open, kind: "hit" });
      sub.push(`<div class="msx-item msx-sub" role="option" id="msx-opt-${j}" data-i="${j}" aria-selected="false">
        <span class="msx-sub-line"></span>
        <div class="msx-main"><div class="msx-snip">${roleTag(hit.meta)}${hl(hit.snippet, hit.highlights)}</div></div>
        <div class="msx-side"><time>${esc(when(hit.ts))}</time></div></div>`);
    }
    const side = [];
    if (item.keys) side.push(`<span class="msx-kbd">${item.keys.split("+").map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span>`);
    else if (item.ts) side.push(`<time title="${esc(fullDate(item.ts))}">${esc(when(item.ts))}</time>`);
    if (item.hit_count > 1) side.push(`<span class="msx-count">${esc(L(`${item.hit_count} совп.`, `${item.hit_count} matches`))}</span>`);
    const lead = firstIsLead ? hits[0] : null;
    const snippetHtml = item.snippet ? `<div class="msx-snip">${lead ? roleTag(lead.meta) : ""}${hl(item.snippet, item.highlights)}</div>` : "";
    const subtitle = item.subtitle && item.type !== "command" ? `<span class="msx-subtitle">${esc(item.subtitle)}</span>` : "";
    const metaLine = item.type === "file" && item.meta?.path ? `<span class="msx-subtitle mono">${esc(item.meta.path)}</span>` : subtitle;
    return `<div class="msx-item" role="option" id="msx-opt-${i}" data-i="${i}" data-type="${esc(item.type)}" aria-selected="false">
      <span class="msx-ico" data-type="${esc(item.type)}">${typeIcon(item.type)}</span>
      <div class="msx-main">
        <div class="msx-title"><span class="msx-t">${hl(item.title, item.title_highlights)}</span>${badges(item)}${metaLine}</div>
        ${snippetHtml}
      </div>
      <div class="msx-side">${side.join("")}</div>
    </div>${sub.join("")}`;
  }
  function renderParsed() {
    const host = $q(".msx-parsed", state.root);
    const server = state.data?.server;
    const parts = [];
    if (server?.layout_switched) parts.push(`<span class="msx-note">${svg("keyboard")}${esc(L("Раскладка исправлена:", "Keyboard layout fixed:"))} <b>${esc(server.layout_switched)}</b> <button type="button" class="msx-link" data-msx-use="${esc(server.layout_switched)}">${esc(L("Искать так", "Use it"))}</button></span>`);
    for (const op of server?.parsed?.operators || []) parts.push(`<span class="msx-op">${esc(op.key)}:<b>${esc(op.value)}</b></span>`);
    for (const ph of server?.parsed?.phrases || []) parts.push(`<span class="msx-op">“${esc(ph)}”</span>`);
    for (const ex of server?.parsed?.excluded || []) parts.push(`<span class="msx-op neg">−${esc(ex)}</span>`);
    if (server?.partial) parts.push(`<span class="msx-note">${esc(L("Не нашлось всех слов сразу — показаны частичные совпадения", "No result has every word — showing partial matches"))}</span>`);
    host.hidden = !parts.length;
    host.innerHTML = parts.join("");
  }
  function renderStats() {
    const host = $q(".msx-stats", state.root);
    const server = state.data?.server;
    if (!state.data) { host.textContent = ""; return; }
    const total = state.data.total || 0;
    const parts = [isEn() ? `${total}${server?.capped ? "+" : ""} result${total === 1 ? "" : "s"}` : `Найдено: ${total}${server?.capped ? "+" : ""}`];
    if (server && typeof server.took_ms === "number") parts.push(`${Math.max(1, Math.round(server.took_ms))} ${L("мс", "ms")}`);
    host.textContent = parts.join(" · ");
  }
  function tipsHtml() {
    const tips = [
      ["in:work", L("только Work", "Work only")], ["from:me", L("мои сообщения", "my messages")],
      ['"точная фраза"', L("точная фраза", "exact phrase"), '"exact phrase"'], ["-слово", L("исключить", "exclude"), "-word"],
      ["model:claude", L("модель", "model")], ["date:week", L("за неделю", "this week")], ["is:pinned", L("закреплённые", "pinned")],
      ["in:docs", L("документация", "documentation")], ["in:telegram", "Telegram"],
    ];
    return `<div class="msx-tips">${tips.map(([ru, label, enText]) => {
      const value = isEn() && enText ? enText : ru;
      return `<button type="button" class="msx-tip" data-msx-insert="${esc(value)}"><code>${esc(value)}</code><span>${esc(label)}</span></button>`;
    }).join("")}</div>`;
  }
  function emptyState() {
    let html = "";
    if (state.recent.length) {
      html += `<div class="msx-group"><div class="msx-group-h"><span>${esc(L("Недавние запросы", "Recent searches"))}</span>
        <button type="button" class="msx-link" data-msx-clear-recent>${esc(L("Очистить", "Clear"))}</button></div>`;
      for (const row of state.recent.slice(0, 6)) {
        const i = register({ kind: "recent", q: row.q });
        html += `<div class="msx-item msx-recent" role="option" id="msx-opt-${i}" data-i="${i}" aria-selected="false">
          <span class="msx-ico" data-type="recent">${svg("recent")}</span>
          <div class="msx-main"><div class="msx-title"><span class="msx-t">${esc(row.q)}</span></div></div>
          <div class="msx-side"><time>${esc(when(row.t))}</time><button type="button" class="msx-x" data-msx-forget="${esc(row.q)}" aria-label="${esc(L("Удалить из недавних", "Remove from recent"))}">${svg("close")}</button></div></div>`;
      }
      html += `</div>`;
    }
    let chats = [];
    try { chats = (S.chats || []).filter((c) => (c.messages || []).length).slice().sort((a, b) => (+b.updated || 0) - (+a.updated || 0)).slice(0, 5); } catch (e) { chats = []; }
    if (chats.length) {
      html += `<div class="msx-group"><div class="msx-group-h"><span>${esc(L("Недавние чаты", "Recent chats"))}</span></div>`;
      for (const c of chats) {
        const mode = c.mode || "chat";
        const type = mode === "work" ? "work" : mode === "code" ? "code" : "chat";
        const item = { type, title: c.title || L("Чат", "Chat"), open: { action: "chat", chat_id: c.id, mode }, meta: { pinned: !!c.pinned, telegram: !!c.telegram }, ts: +c.updated || 0, client: true };
        const i = register({ kind: "item", item, open: item.open });
        html += `<div class="msx-item" role="option" id="msx-opt-${i}" data-i="${i}" aria-selected="false" data-type="${type}">
          <span class="msx-ico" data-type="${type}">${typeIcon(type)}</span>
          <div class="msx-main"><div class="msx-title"><span class="msx-t">${esc(item.title)}</span>${badges(item)}${type !== "chat" ? `<span class="msx-subtitle">${esc(typeLabel(type))}</span>` : ""}</div></div>
          <div class="msx-side"><time>${esc(when(item.ts))}</time></div></div>`;
      }
      html += `</div>`;
    }
    const quick = commandList().filter((c) => ["new-chat", "settings", "memory", "schedule", "docs", "theme"].includes(c.id));
    html += `<div class="msx-group"><div class="msx-group-h"><span>${esc(L("Быстрые действия", "Quick actions"))}</span></div>`;
    for (const c of quick) {
      const item = { type: "command", title: c.title, keys: c.keys, open: { action: "command", id: c.id }, client: true, meta: {} };
      const i = register({ kind: "item", item, open: item.open });
      html += `<div class="msx-item" role="option" id="msx-opt-${i}" data-i="${i}" aria-selected="false" data-type="command">
        <span class="msx-ico" data-type="command">${typeIcon("command")}</span>
        <div class="msx-main"><div class="msx-title"><span class="msx-t">${esc(c.title)}</span></div></div>
        <div class="msx-side">${c.keys ? `<span class="msx-kbd">${c.keys.split("+").map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span>` : ""}</div></div>`;
    }
    html += `</div>`;
    html += `<div class="msx-hello"><div class="msx-hello-h"><b>${esc(L("Ищите всё сразу", "Search everything at once"))}</b></div>
      <p>${esc(L("Чаты, Work и MemuCode, файлы проектов, память, промпты, скиллы, задачи, панели, модули, настройки и документация. Можно ошибиться в раскладке — «ghbdtn» найдёт «привет».",
        "Chats, Work and MemuCode, project files, memory, prompts, skills, tasks, panels, modules, settings and documentation. Typos and the wrong keyboard layout are forgiven."))}</p>${tipsHtml()}</div>`;
    return html;
  }
  function noResults() {
    const server = state.data?.server;
    const q = state.q.trim();
    const suggestion = server?.suggestion;
    return `<div class="msx-empty">
      <div class="msx-empty-art">${svg("search")}</div>
      <b>${q ? esc(L(`Ничего не нашлось по «${q}»`, `Nothing found for “${q}”`)) : esc(L("Ничего не нашлось", "Nothing found"))}</b>
      ${server?.error ? `<p class="msx-err">${esc(L("Поиск недоступен: ", "Search is unavailable: ") + server.error)}</p>` : ""}
      ${suggestion ? `<p>${esc(L("Возможно, вы имели в виду", "Did you mean"))} <button type="button" class="msx-link strong" data-msx-use="${esc(suggestion)}">${esc(suggestion)}</button>?</p>` : ""}
      <div class="msx-empty-actions">
        ${state.chip !== "all" ? `<button type="button" class="msx-btn" data-msx-chip="all">${esc(L("Искать везде", "Search everywhere"))}</button>` : ""}
        ${hasFilters() ? `<button type="button" class="msx-btn" data-msx-reset>${esc(L("Сбросить фильтры", "Clear filters"))}</button>` : ""}
      </div>
      ${tipsHtml()}</div>`;
  }

  /* ------------------------------------------------------------------ selection + preview */
  function setActive(index, scroll = true) {
    const list = $q(".msx-list", state.root);
    if (!state.flat.length) {
      $q(".msx-input", state.root).removeAttribute("aria-activedescendant");
      renderPreview(null);
      return;
    }
    state.active = Math.max(0, Math.min(state.flat.length - 1, index));
    list.querySelectorAll(".msx-item.active").forEach((el) => { el.classList.remove("active"); el.setAttribute("aria-selected", "false"); });
    const row = list.querySelector(`#msx-opt-${state.active}`);
    if (row) {
      row.classList.add("active"); row.setAttribute("aria-selected", "true");
      if (scroll) row.scrollIntoView({ block: "nearest" });
      $q(".msx-input", state.root).setAttribute("aria-activedescendant", row.id);
    }
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(() => renderPreview(state.flat[state.active]), 70);
  }
  function move(step) {
    if (!state.flat.length) return;
    setActive((state.active + step + state.flat.length) % state.flat.length);
  }
  async function renderPreview(entry) {
    const pane = $q(".msx-preview", state.root);
    const seq = ++state.previewSeq;
    if (!entry || entry.kind === "recent" || !entry.item) {
      pane.innerHTML = entry?.kind === "recent"
        ? `<div class="msx-pv-empty">${svg("recent")}<p>${esc(L("Повторить поиск", "Search again"))}: <b>${esc(entry.q)}</b></p></div>`
        : `<div class="msx-pv-empty">${svg("search")}<p>${esc(L("Выберите результат, чтобы увидеть его здесь", "Select a result to preview it here"))}</p></div>`;
      return;
    }
    const item = entry.item;
    const hit = entry.hit;
    const head = previewHead(item, hit);
    if (item.client) { pane.innerHTML = head + clientPreview(item) + previewActions(item, hit); return; }
    const id = hit ? hit.id : (item.hits?.[0]?.id || item.id);
    const key = `${id}\u0000${state.q}`;
    let data = state.previewCache.get(key);
    if (!data) {
      pane.classList.add("msx-pv-loading");
      if (!pane.querySelector(".msx-pv-head")) pane.innerHTML = head + `<div class="msx-pv-skel"><i></i><i></i><i></i></div>`;
      try {
        data = await http(`/api/search/preview?id=${encodeURIComponent(id)}&q=${encodeURIComponent(state.q)}`);
        state.previewCache.set(key, data);
        if (state.previewCache.size > 120) state.previewCache.delete(state.previewCache.keys().next().value);
      } catch (e) { data = null; }
      pane.classList.remove("msx-pv-loading");
    }
    if (seq !== state.previewSeq) return;
    pane.innerHTML = head + serverPreview(item, data) + previewActions(item, hit, data);
    pane.querySelector(".msx-pv-block.hit, .msx-code .hit")?.scrollIntoView({ block: "center" });
  }
  function previewHead(item, hit) {
    const m = item.meta || {};
    const bits = [];
    if (m.project) bits.push(`${svg("project")}${esc(m.project)}`);
    if (m.folder) bits.push(`${svg("folder")}${esc(m.folder)}`);
    if (m.telegram) bits.push(`${svg("telegram")}Telegram`);
    if (m.messages) bits.push(esc(L(`${m.messages} сообщ.`, `${m.messages} messages`)));
    const ts = hit?.ts || item.ts;
    if (ts) bits.push(esc(fullDate(ts)));
    return `<div class="msx-pv-head">
      <div class="msx-pv-kind" data-type="${esc(item.type)}">${typeIcon(item.type)}<span>${esc(TYPES[item.type] ? pick(TYPES[item.type].one) : typeLabel(item.type))}</span></div>
      <h3>${hl(item.title, item.title_highlights)}</h3>
      ${item.subtitle && item.type !== "command" ? `<div class="msx-pv-sub">${esc(item.subtitle)}</div>` : ""}
      ${bits.length ? `<div class="msx-pv-meta">${bits.map((b) => `<span>${b}</span>`).join("")}</div>` : ""}
    </div>`;
  }
  function clientPreview(item) {
    const provider = item._provider;
    if (provider && typeof provider.preview === "function") {
      try { const html = provider.preview(item); if (html) return `<div class="msx-pv-body">${html}</div>`; } catch (e) {}
    }
    const body = [];
    if (item.snippet) body.push(`<p class="msx-pv-text">${hl(item.snippet, item.highlights)}</p>`);
    if (item.body) body.push(`<div class="msx-pv-block"><div class="msx-pv-text pre">${hl(String(item.body).slice(0, 1600), rangesFor(String(item.body).slice(0, 1600), state.q))}</div></div>`);
    if (item.type === "setting") body.push(`<p class="msx-pv-hint">${svg("arrow")}${esc(L("Откроются настройки — нужная строка будет подсвечена.", "Settings will open with this row highlighted."))}</p>`);
    if (item.type === "command" && item.keys) body.push(`<p class="msx-pv-hint">${svg("keyboard")}${esc(L("Сочетание:", "Shortcut:"))} <span class="msx-kbd">${item.keys.split("+").map((k) => `<kbd>${esc(k)}</kbd>`).join("")}</span></p>`);
    return `<div class="msx-pv-body">${body.join("") || `<p class="msx-pv-hint">${esc(L("Нажмите Enter, чтобы открыть.", "Press Enter to open."))}</p>`}</div>`;
  }
  function serverPreview(item, data) {
    if (!data) return `<div class="msx-pv-body"><p class="msx-pv-text">${hl(item.snippet || "", item.highlights)}</p></div>`;
    if (data.code) {
      const rows = data.code.lines.map((line, k) => {
        const no = data.code.start + k;
        return `<div class="msx-code-row ${no === data.code.hit ? "hit" : ""}"><span>${no}</span><code>${hl(line, data.code.highlights?.[k]) || " "}</code></div>`;
      }).join("");
      return `<div class="msx-pv-body"><div class="msx-code">${rows || `<p class="msx-pv-hint">${esc(L("Файл пуст или не текстовый", "The file is empty or not text"))}</p>`}</div></div>`;
    }
    const blocks = (data.blocks || []).map((b, k) => {
      const role = b.role === "user" ? `<span class="msx-role you">${esc(L("Вы", "You"))}</span>` : b.role === "assistant" ? `<span class="msx-role ai">${esc(modelName(b.label) || L("ИИ", "AI"))}</span>` : (b.label ? `<span class="msx-pv-label">${esc(b.label)}</span>` : "");
      const clickable = b.open && b.open.action ? ` data-msx-pv-open="${k}" tabindex="0" role="button"` : "";
      return `<div class="msx-pv-block ${b.hit ? "hit" : ""} ${b.role ? "msg " + esc(b.role) : ""}"${clickable}>
        ${role || b.ts ? `<div class="msx-pv-bh">${role}${b.ts ? `<time>${esc(when(b.ts))}</time>` : ""}</div>` : ""}
        <div class="msx-pv-text pre">${hl(b.text, b.highlights)}</div></div>`;
    }).join("");
    state.previewBlocks = data.blocks || [];
    const pos = data.position && data.messages ? `<div class="msx-pv-pos">${esc(L(`Сообщение ${data.position} из ${data.messages}`, `Message ${data.position} of ${data.messages}`))}</div>` : "";
    return `<div class="msx-pv-body">${pos}${blocks || `<p class="msx-pv-hint">${esc(L("Нет текста для просмотра", "Nothing to preview"))}</p>`}</div>`;
  }
  function secondaryAction(item, hit) {
    if (item.type === "file") return { id: "reveal", label: L("Показать в папке", "Show in folder"), icon: "folder" };
    if (item.type === "command" || item.type === "setting" || item.type === "panel" || item.type === "module" || item.type === "project") return null;
    return { id: "copy", label: L("Копировать текст", "Copy text"), icon: "copy" };
  }
  function previewActions(item, hit) {
    const second = secondaryAction(item, hit);
    const primary = openLabel(item, hit);
    return `<div class="msx-pv-actions">
      <button type="button" class="msx-btn primary" data-msx-open-active>${svg("enter")}<span>${esc(primary)}</span><kbd>↵</kbd></button>
      ${second ? `<button type="button" class="msx-btn ghost" data-msx-second title="Ctrl+Enter">${svg(second.icon)}<span>${esc(second.label)}</span></button>` : ""}
    </div>`;
  }
  function openLabel(item, hit) {
    const o = (hit && hit.open) || item.open || {};
    if (o.action === "chat") return o.index !== undefined ? L("Открыть на сообщении", "Open at the message") : L("Открыть чат", "Open chat");
    if (o.action === "file") return L("Открыть в MemuCode", "Open in MemuCode");
    if (o.action === "quick") return L("Спросить снова", "Ask again");
    if (o.action === "command") return L("Выполнить", "Run");
    if (o.action === "doc") return L("Открыть статью", "Open article");
    if (o.action === "setting") return L("Перейти к настройке", "Go to setting");
    return L("Открыть", "Open");
  }

  /* ------------------------------------------------------------------ events */
  function onKey(event) {
    const input = $q(".msx-input", state.root);
    const key = event.key;
    if (key === "Escape") {
      event.preventDefault(); event.stopPropagation();
      if (state.popOpen) { closePops(); input.focus(); return; }
      if (input.value) { input.value = ""; state.q = ""; runSearch(true); return; }
      close(); return;
    }
    if ((event.ctrlKey || event.metaKey) && event.code === "KeyK") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if ((event.ctrlKey || event.metaKey) && event.code === "KeyP") {
      event.preventDefault(); event.stopPropagation();
      state.showPreview = !state.showPreview;
      state.root.classList.toggle("msx-no-preview", !state.showPreview);
      return;
    }
    if (event.target.closest?.(".msx-pop") && !["ArrowDown", "ArrowUp"].includes(key)) return;
    if (key === "ArrowDown") { event.preventDefault(); move(1); return; }
    if (key === "ArrowUp") { event.preventDefault(); move(-1); return; }
    if (key === "PageDown") { event.preventDefault(); move(8); return; }
    if (key === "PageUp") { event.preventDefault(); move(-8); return; }
    if (key === "Tab") { event.preventDefault(); cycleChip(event.shiftKey ? -1 : 1); return; }
    if (key === "Enter" && !event.isComposing) {
      event.preventDefault();
      const entry = state.flat[state.active];
      if (!entry) return;
      if (event.ctrlKey || event.metaKey) secondary(entry); else activate(entry);
      return;
    }
    if (key === "Backspace" && (event.ctrlKey || !input.value) && event.target === input && !input.value) {
      // an empty field: Backspace removes the last filter, then the section
      const f = state.filters;
      const last = ["pinned", "folder", "model", "role", "date"].find((k) => f[k]);
      if (last) { event.preventDefault(); setFilter(last, last === "pinned" ? false : ""); return; }
      if (state.chip !== "all") { event.preventDefault(); setChip("all"); }
    }
    if (event.altKey && /^Digit[1-9]$/.test(event.code)) {
      event.preventDefault();
      const ids = $qa(".msx-chip", state.root).map((el) => el.dataset.msxChip);
      const id = ids[+event.code.slice(5) - 1];
      if (id) setChip(id);
    }
  }
  function onClick(event) {
    const t = event.target;
    if (t.closest("[data-msx-close]")) { close(); return; }
    if (t.closest("[data-msx-clear]")) { const input = $q(".msx-input", state.root); input.value = ""; state.q = ""; input.focus(); runSearch(true); return; }
    const chip = t.closest("[data-msx-chip]");
    if (chip) { setChip(chip.dataset.msxChip); $q(".msx-input", state.root).focus(); return; }
    if (t.closest("[data-msx-more]")) { togglePop("more"); return; }
    if (t.closest("[data-msx-filters]")) { togglePop("filters"); return; }
    if (t.closest("[data-msx-menu]")) { togglePop("menu"); return; }
    if (t.closest("[data-msx-reset]")) {
      state.filters = { date: "", from: "", to: "", role: "", model: "", folder: "", pinned: false };
      renderActiveFilters(); if (state.popOpen === "filters") renderFiltersPop(); runSearch(true); return;
    }
    const unset = t.closest("[data-msx-unset]");
    if (unset) { const key = unset.dataset.msxUnset; setFilter(key, key === "pinned" ? false : ""); return; }
    const set = t.closest("[data-msx-set]");
    if (set) { setFilter(set.dataset.msxSet, set.dataset.v); return; }
    const use = t.closest("[data-msx-use]");
    if (use) { const input = $q(".msx-input", state.root); input.value = use.dataset.msxUse; state.q = input.value; input.focus(); runSearch(true); return; }
    const insert = t.closest("[data-msx-insert]");
    if (insert) {
      const input = $q(".msx-input", state.root);
      const value = insert.dataset.msxInsert;
      input.value = (input.value.trim() ? input.value.trim() + " " : "") + value + (value.endsWith(":") ? "" : " ");
      state.q = input.value; input.focus(); input.setSelectionRange(input.value.length, input.value.length); runSearch(true); return;
    }
    const forget = t.closest("[data-msx-forget]");
    if (forget) {
      event.stopPropagation();
      http("/api/search/recent", { method: "POST", body: { remove: forget.dataset.msxForget } }).then((d) => { state.recent = d.items || []; render(); }).catch(() => {});
      return;
    }
    if (t.closest("[data-msx-clear-recent]")) {
      http("/api/search/recent", { method: "POST", body: { clear: true } }).then(() => { state.recent = []; render(); }).catch(() => {});
      return;
    }
    if (t.closest("[data-msx-open-active]")) { const entry = state.flat[state.active]; if (entry) activate(entry); return; }
    if (t.closest("[data-msx-second]")) { const entry = state.flat[state.active]; if (entry) secondary(entry); return; }
    const pv = t.closest("[data-msx-pv-open]");
    if (pv) {
      const block = (state.previewBlocks || [])[+pv.dataset.msxPvOpen];
      const entry = state.flat[state.active];
      if (block?.open && entry) activate({ ...entry, open: block.open, hit: { ...(entry.hit || {}), open: block.open } });
      return;
    }
    const menuItem = t.closest("[data-msx-opt]");
    if (menuItem) { menuAction(menuItem.dataset.msxOpt); return; }
    const row = t.closest(".msx-item");
    if (row && !t.closest("button")) { const entry = state.flat[+row.dataset.i]; if (entry) activate(entry); return; }
    if (state.popOpen && !t.closest(".msx-pop")) closePops();
  }
  function bindPopInputs(pop) {
    pop.addEventListener("change", (event) => {
      const t = event.target;
      if (t.matches("[data-msx-select]")) setFilter(t.dataset.msxSelect, t.value);
      else if (t.matches("[data-msx-pinned]")) setFilter("pinned", t.checked);
      else if (t.matches("[data-msx-date]")) { state.filters[t.dataset.msxDate] = t.value; setFilter("date", "custom"); }
      else if (t.matches("[data-msx-opt-toggle]")) menuAction(t.dataset.msxOptToggle, t.checked);
    });
  }
  function togglePop(name) {
    if (state.popOpen === name) { closePops(); return; }
    closePops();
    const pop = $q(`.msx-${name}-pop`, state.root);
    if (!pop.dataset.bound) { bindPopInputs(pop); pop.dataset.bound = "1"; }
    state.popOpen = name;
    if (name === "filters") renderFiltersPop(); else if (name === "more") renderMorePop(); else renderMenuPop();
    pop.hidden = false;
    $q(`[data-msx-${name}]`, state.root)?.setAttribute("aria-expanded", "true");
    pop.querySelector("button, select, input")?.focus({ preventScroll: true });
  }
  function closePops() {
    if (!state.root) return;
    $qa(".msx-pop", state.root).forEach((p) => { p.hidden = true; });
    $qa("[data-msx-filters], [data-msx-menu], [data-msx-more]", state.root).forEach((b) => b.setAttribute("aria-expanded", "false"));
    state.popOpen = false;
  }
  async function renderMenuPop() {
    const pop = $q(".msx-menu-pop", state.root);
    let settings = { remember_recent: true, index_files: true };
    try { settings = await http("/api/search/settings"); } catch (e) {}
    let status = null;
    try { status = await http("/api/search/status"); } catch (e) {}
    pop.innerHTML = `
      <label class="msx-check"><input type="checkbox" data-msx-opt-toggle="remember_recent" ${settings.remember_recent ? "checked" : ""}><span>${esc(L("Запоминать недавние запросы", "Remember recent searches"))}</span></label>
      <label class="msx-check"><input type="checkbox" data-msx-opt-toggle="index_files" ${settings.index_files ? "checked" : ""}><span>${esc(L("Искать в файлах Work-проектов", "Search inside Work project files"))}</span></label>
      <button type="button" class="msx-menu-item" data-msx-opt="reindex">${svg("recent")}${esc(L("Перестроить индекс", "Rebuild the index"))}</button>
      <button type="button" class="msx-menu-item" data-msx-opt="help">${svg("doc")}${esc(L("Как искать — справка", "How to search — help"))}</button>
      <div class="msx-menu-keys">${[[`<kbd>Ctrl</kbd><kbd>↵</kbd>`, L("второе действие (копировать, показать в папке)", "secondary action (copy, show in folder)")],
        [`<kbd>Ctrl</kbd><kbd>P</kbd>`, L("показать или скрыть просмотр", "show or hide the preview")], [`<kbd>Alt</kbd><kbd>1–9</kbd>`, L("раздел по номеру", "section by number")],
        [`<kbd>⌫</kbd>`, L("в пустом поле — снять фильтр", "in an empty field — remove a filter")]].map(([k, t]) => `<span>${k}<em>${esc(t)}</em></span>`).join("")}</div>
      ${status ? `<p class="msx-pop-note">${esc(L(`В индексе: ${status.groups} объектов, ${status.vocabulary} слов`, `Indexed: ${status.groups} items, ${status.vocabulary} words`))}</p>` : ""}`;
  }
  async function menuAction(id, value) {
    if (id === "remember_recent" || id === "index_files") {
      try { await http("/api/search/settings", { method: "POST", body: { [id]: !!value } }); } catch (e) {}
      if (id === "remember_recent" && !value) state.recent = [];
      runSearch(false);
      return;
    }
    if (id === "reindex") { closePops(); await reindex(); return; }
    if (id === "help") { close(false); window.MemuDocs?.open("search"); }
  }
  async function reindex() {
    const note = (text, type = "ok") => { try { toast(text, type); } catch (e) {} };
    try {
      const data = await http("/api/search/reindex", { method: "POST", body: {} });
      note(L(`Индекс перестроен: ${data.groups} объектов за ${Math.round(data.took_ms)} мс`, `Index rebuilt: ${data.groups} items in ${Math.round(data.took_ms)} ms`));
      state.previewCache.clear();
      if (state.open) runSearch(false);
    } catch (e) { note(L("Не удалось перестроить индекс", "Could not rebuild the index"), "err"); }
  }

  /* ------------------------------------------------------------------ opening results */
  function activate(entry) {
    if (entry.kind === "recent") {
      const input = $q(".msx-input", state.root);
      input.value = entry.q; state.q = entry.q; input.focus(); runSearch(true); return;
    }
    const item = entry.item;
    const o = (entry.hit && entry.hit.open) || entry.open || item?.open || {};
    if (state.q.trim()) rememberQuery(state.q);
    const query = state.q;
    const words = state.data?.server?.parsed?.words || wordsOf(query);
    close(false);
    openTarget(o, item, { query, words }).catch((e) => console.warn("[search] open failed", e));
  }
  async function secondary(entry) {
    const item = entry.item;
    if (!item) return activate(entry);
    const second = secondaryAction(item, entry.hit);
    if (!second) return activate(entry);
    if (second.id === "reveal") {
      try { await http("/api/search/reveal", { method: "POST", body: { id: item.id } }); }
      catch (e) { try { toast(L("Не удалось открыть папку", "Could not open the folder"), "err"); } catch (x) {} }
      return;
    }
    let text = entry.hit?.snippet || item.snippet || item.title;
    try {
      const data = state.previewCache.get(`${entry.hit ? entry.hit.id : (item.hits?.[0]?.id || item.id)}\u0000${state.q}`);
      const block = data?.blocks?.find((b) => b.hit) || data?.blocks?.[0];
      if (block?.text) text = block.text;
    } catch (e) {}
    try { if (typeof copyText === "function") copyText(String(text).replace(/^…|…$/g, "")); else await navigator.clipboard.writeText(text); } catch (e) {}
    try { toast(L("Скопировано", "Copied")); } catch (e) {}
  }

  async function openTarget(o, item, ctx) {
    const action = o.action || "";
    const custom = state.actions.get(action);
    if (custom) return custom(o, item, ctx);
    if (item?._provider?.open) return item._provider.open(item, ctx);
    // Opened over a dialog (Ctrl+K works there too): a result that leads elsewhere closes the dialog first.
    if (/^(chat|project|file|panel|module|quick|agent)$/.test(action) && document.getElementById("panel-back")?.classList.contains("visible")
      && typeof closePanel === "function") closePanel();
    switch (action) {
      case "chat": return openChatAt(o, ctx);
      case "project": return openProject(o.project_id);
      case "file": return openFile(o);
      case "memory": await showMemory(true); return flashIn("#panel", item?.snippet || item?.title);
      case "prompt": currentSettingsTab = "prompts"; await showSettings(true); return flashIn("#panel", item?.title);
      case "schedule": await showSchedule(true); return flashIn("#panel", item?.title);
      case "doc": return window.MemuDocs?.open(o.page, o.anchor || "");
      case "panel": return openPanel(o.panel_id, item);
      case "module": return openModule(o.module_id);
      case "quick": return askAgain(o.q);
      case "setting": return openSetting(o);
      case "command": { const c = commandList().find((x) => x.id === o.id); if (c) c.run(); return; }
      case "skill": showSkillsModal(); return flashIn("#panel", o.name);
      case "agent": if (typeof startMemeChat === "function") return startMemeChat(o.id); return;
      default: return undefined;
    }
  }
  async function openProject(id) {
    try {
      if (S.workspaces?.active_id !== id) S.workspaces = await http("/api/work/projects/select", { method: "POST", body: { id } });
    } catch (e) { try { toast(L("Проект недоступен", "The project is not available"), "err"); } catch (x) {} return; }
    $q("#nav-work")?.click();
  }
  async function openFile(o) {
    try {
      if (S.workspaces?.active_id !== o.project_id) S.workspaces = await http("/api/work/projects/select", { method: "POST", body: { id: o.project_id } });
    } catch (e) { try { toast(L("Проект недоступен", "The project is not available"), "err"); } catch (x) {} return; }
    if (S.mode !== "code") { if (typeof enterCodeMode === "function") await enterCodeMode(); }
    for (let i = 0; i < 20 && !(window.MemuCode && MemuCode.openFile); i++) await sleep(50);
    if (window.MemuCode?.openFile) MemuCode.openFile(o.path, Math.max(1, (+o.line || 1) - 5), +o.line || 0);
  }
  async function openPanel(id, item) {
    const card = document.querySelector(`[data-panel-id="${CSS.escape(String(id))}"]`);
    if (card && card.offsetParent) { card.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" }); flashNode(card); return; }
    window.MemuPanels?.openEditor?.(id);
  }
  function openModule(id) {
    const row = (window.MemuModules?.catalog || []).find((m) => m.id === id);
    if (row && row.enabled) return MemuModules.open(id);
    if (typeof openSettingsPage === "function") { currentSettingsTab = "modules"; return showSettings(true); }
  }
  function askAgain(q) {
    const input = $q("#input");
    if (!input) return;
    input.value = q || "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }
  async function openSetting(o) {
    if (typeof currentSettingsTab !== "undefined") currentSettingsTab = o.page;
    const shell = document.querySelector("#panel .sx-shell");
    if (shell && typeof openSettingsPage === "function") openSettingsPage(o.page);
    else await showSettings(true);
    const K = window.MemuSettingsKit;
    if (o.sub && K?.openSub) { try { K.openSub(o.sub); } catch (e) {} }
    if (!o.label) return;
    await frame(); await frame();
    const pane = document.querySelector(`#pane-${CSS.escape(o.page)}`) || document.querySelector("#panel");
    const target = norm(o.label).replace(/\s+/g, " ").trim();
    const labels = $qa(".sx-label, .tl, .settings-title, .sx-section-title, b, label", pane);
    const hit = labels.find((el) => norm(el.textContent).replace(/[✓›]/g, " ").replace(/\s+/g, " ").trim() === target)
      || labels.find((el) => norm(el.textContent).includes(target));
    const row = hit?.closest(".sx-row, .p-toggle, .tg-row, .sx-field, section, .sx-section") || hit;
    if (!row) return;
    let parent = row.parentElement;
    while (parent) { if (parent.tagName === "DETAILS" && !parent.open) parent.open = true; parent = parent.parentElement; }
    row.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
    row.classList.remove("sx-flash"); void row.offsetWidth; row.classList.add("sx-flash");
    setTimeout(() => row.classList.remove("sx-flash"), 2400);
  }
  async function flashIn(selector, text) {
    if (!text) return;
    await frame(); await frame(); await sleep(60);
    const root = document.querySelector(selector);
    if (!root) return;
    const needle = norm(String(text).replace(/^…|…$/g, "")).slice(0, 60).trim();
    if (!needle) return;
    const nodes = $qa("li, .memory-item, .fact, .sched-item, .skill-card, article, .p-toggle, tr, .row, div", root)
      .filter((el) => el.children.length < 12 && norm(el.textContent).includes(needle));
    const node = nodes.sort((a, b) => a.textContent.length - b.textContent.length)[0];
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
    flashNode(node);
  }
  function flashNode(node) {
    node.classList.remove("msx-flash"); void node.offsetWidth; node.classList.add("msx-flash");
    setTimeout(() => node.classList.remove("msx-flash"), 2400);
  }

  /* ---- a chat at the exact message, with the words highlighted and a «found in chat» bar */
  function messageText(m) {
    const content = Array.isArray(m?.content) ? m.content.map((p) => p?.text || "").join(" ") : String(m?.content || "");
    return content;
  }
  async function openChatAt(o, ctx) {
    let chat = (S.chats || []).find((c) => c.id === o.chat_id);
    if (!chat && typeof syncRemoteChats === "function") { try { await syncRemoteChats(); } catch (e) {} chat = (S.chats || []).find((c) => c.id === o.chat_id); }
    if (!chat) { try { toast(L("Чат не найден — возможно, он удалён", "Chat not found — it may have been deleted"), "err"); } catch (e) {} return; }
    if (S.currentId !== o.chat_id) await openChat(o.chat_id);
    if (S.currentId !== o.chat_id) return;
    // Round 35 (branches): a message of another version — make its branch active first
    if (o.message_id && typeof MemuBranches !== "undefined") {
      if (!S.messages.some((m) => m && m.id === o.message_id)) MemuBranches.revealMessage(o.message_id, { scroll: false });
      const at = S.messages.findIndex((m) => m && m.id === o.message_id);
      if (at >= 0) o = { ...o, index: at, created_at: S.messages[at].createdAt };
    }
    const rx = wordRegex(ctx.words?.length ? ctx.words : wordsOf(ctx.query));
    const matches = [];
    (S.messages || []).forEach((m, i) => {
      if (m.internal || !["user", "assistant"].includes(m.role)) return;
      if (rx) { rx.lastIndex = 0; if (rx.test(norm(messageText(m)))) matches.push(i); }
    });
    let index = o.index;
    if (index !== undefined) {
      const m = S.messages[index];
      if (!m || (o.created_at && +m.createdAt !== +o.created_at)) {
        const found = S.messages.findIndex((x) => +x.createdAt === +o.created_at && x.role === o.role);
        index = found >= 0 ? found : Math.min(index, S.messages.length - 1);
      }
    } else if (matches.length) index = matches[0];
    await frame(); await frame();
    if (index !== undefined && index >= 0) await scrollToMessage(index, rx);
    if (rx && matches.length) showFindbar(o.chat_id, ctx.query, matches, index, rx);
  }
  async function scrollToMessage(index, rx) {
    let node = document.querySelector(`#messages [data-msg="${index}"]`);
    if (!node) { await sleep(120); node = document.querySelector(`#messages [data-msg="${index}"]`); }
    if (!node) return;
    node.querySelectorAll(".user-text.collapsed").forEach((el) => el.classList.remove("collapsed"));
    if (rx) markText(node, rx);
    // a long answer: centre the first highlighted word, not the top of the message (the word could stay
    // below the fold of a 2-screen answer)
    // #messages > .msg has content-visibility:auto — an off-screen answer measures as a 280px placeholder, so
    // render this one for real before measuring (and let the browser skip it again later)
    node.style.contentVisibility = "visible";
    setTimeout(() => { node.style.contentVisibility = ""; }, 4000);
    const firstMark = node.querySelector("mark.msx-mark");
    const box = node.closest("#messages")?.parentElement || document.documentElement;
    const tall = node.getBoundingClientRect().height > (box.clientHeight || window.innerHeight) * 0.6;
    const target = tall && firstMark ? firstMark : node;
    // A long smooth scroll through a big chat ends off target (messages above still lay out while it runs):
    // jump when far, glide when near, then correct once after the layout settles.
    const far = Math.abs(target.getBoundingClientRect().top - window.innerHeight / 2) > window.innerHeight * 1.5;
    target.scrollIntoView({ block: "center", behavior: reducedMotion() || far ? "auto" : "smooth" });
    setTimeout(() => {
      if (!target.isConnected) return;
      const r = target.getBoundingClientRect(), view = box.getBoundingClientRect();
      const mid = (view.top + view.bottom) / 2 || window.innerHeight / 2;
      if (Math.abs((r.top + r.bottom) / 2 - mid) > Math.max(120, (view.height || window.innerHeight) * 0.3)) {
        target.scrollIntoView({ block: "center", behavior: "auto" });
      }
    }, far ? 450 : 900);
    node.classList.remove("msx-target"); void node.offsetWidth; node.classList.add("msx-target");
    setTimeout(() => node.classList.remove("msx-target"), 2600);
  }
  function markText(root, rx) {
    root.querySelectorAll("mark.msx-mark").forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
    root.normalize();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement?.closest("button, .msg-actions, .msg-meta, script, style, svg, .katex-mathml") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    let count = 0;
    for (const textNode of nodes) {
      const text = textNode.nodeValue;
      const folded = norm(text);
      if (folded.length !== text.length) continue;
      rx.lastIndex = 0;
      const found = [...folded.matchAll(rx)];
      if (!found.length) continue;
      const frag = document.createDocumentFragment();
      let at = 0;
      for (const m of found) {
        if (m.index > at) frag.appendChild(document.createTextNode(text.slice(at, m.index)));
        const mark = document.createElement("mark");
        mark.className = "msx-mark";
        mark.textContent = text.slice(m.index, m.index + m[0].length);
        frag.appendChild(mark);
        at = m.index + m[0].length;
        if (++count > 300) break;
      }
      if (at < text.length) frag.appendChild(document.createTextNode(text.slice(at)));
      textNode.replaceWith(frag);
      if (count > 300) break;
    }
  }
  function showFindbar(chatId, query, matches, current, rx) {
    hideFindbar();
    const bar = document.createElement("div");
    bar.className = "msx-findbar";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", L("Найдено в этом чате", "Found in this chat"));
    let pos = Math.max(0, matches.indexOf(current));
    const draw = () => {
      bar.innerHTML = `<span class="msx-fb-ico">${svg("search")}</span><span class="msx-fb-q" title="${esc(query)}">${esc(query.trim() || "…")}</span>
        <span class="msx-fb-n">${pos + 1}/${matches.length}</span>
        <button type="button" data-fb="prev" aria-label="${esc(L("Предыдущее", "Previous"))}" title="${esc(L("Предыдущее (Shift+F3)", "Previous (Shift+F3)"))}">${svg("up")}</button>
        <button type="button" data-fb="next" aria-label="${esc(L("Следующее", "Next"))}" title="${esc(L("Следующее (F3)", "Next (F3)"))}">${svg("down")}</button>
        <button type="button" data-fb="search" aria-label="${esc(L("Вернуться к поиску", "Back to search"))}" title="${esc(L("Вернуться к поиску", "Back to search"))}">${svg("arrow")}</button>
        <button type="button" data-fb="close" aria-label="${esc(L("Закрыть", "Close"))}">${svg("close")}</button>`;
    };
    const go = (step) => { pos = (pos + step + matches.length) % matches.length; draw(); scrollToMessage(matches[pos], rx); };
    draw();
    bar.addEventListener("click", (event) => {
      const b = event.target.closest("[data-fb]");
      if (!b) return;
      if (b.dataset.fb === "prev") go(-1);
      else if (b.dataset.fb === "next") go(1);
      else if (b.dataset.fb === "search") { hideFindbar(); open(query); }
      else hideFindbar();
    });
    const onKeyDoc = (event) => {
      if (event.key === "F3") { event.preventDefault(); go(event.shiftKey ? -1 : 1); }
    };
    document.addEventListener("keydown", onKeyDoc);
    const host = document.querySelector("#chat-view-wrap") || document.body;
    host.appendChild(bar);
    place(bar);
    const watch = setInterval(() => {
      if (typeof S === "undefined" || S.currentId !== chatId || !bar.isConnected) hideFindbar();
      else place(bar);
    }, 600);
    state.findbar = { bar, watch, onKeyDoc };
  }
  function place(bar) {
    const box = document.querySelector("#messages")?.parentElement;
    const rect = box?.getBoundingClientRect();
    if (!rect || !rect.width) return;
    bar.style.top = `${Math.max(8, rect.top + 10)}px`;
    bar.style.left = `${rect.left + rect.width / 2}px`;
  }
  function hideFindbar() {
    const fb = state.findbar;
    if (!fb) return;
    clearInterval(fb.watch);
    document.removeEventListener("keydown", fb.onKeyDoc);
    fb.bar.remove();
    document.querySelectorAll("#messages mark.msx-mark").forEach((m) => m.replaceWith(document.createTextNode(m.textContent)));
    state.findbar = null;
  }

  /* ------------------------------------------------------------------ app hooks */
  function hookSidebarFilter() {
    const input = document.getElementById("chat-search");
    if (!input || input.dataset.msx) return;
    input.dataset.msx = "1";
    // the sidebar keeps filtering the chat list; Enter (or the button) takes the query everywhere
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); open(input.value); }
    });
    const label = input.closest("label");
    if (label && !label.querySelector(".msx-everywhere")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "msx-everywhere";
      // The chip and tooltips follow the «search» shortcut (it can be rebound or turned off in Settings → Keyboard shortcuts).
      const set = () => {
        const H = window.MemuHotkeys;
        const combo = H ? H.value("search", "Ctrl+K") : "Ctrl+K";
        const shown = combo ? (H && H.label ? H.label(combo) : combo) : "";
        btn.title = shown ? L(`Искать везде (${shown})`, `Search everywhere (${shown})`) : L("Искать везде", "Search everywhere");
        btn.setAttribute("aria-label", L("Искать везде", "Search everywhere"));
        btn.innerHTML = `<kbd>${shown ? esc(shown.replace(/\+/g, " ")) : "↵"}</kbd>`;
        label.title = L("Фильтр списка чатов. Enter — искать везде", "Filter the chat list. Enter — search everywhere");
      };
      set();
      label.addEventListener("pointerenter", set);
      btn.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); open(input.value); });
      label.appendChild(btn);
      document.addEventListener("memu:lang", set);
    }
  }
  function hookSettingsSnapshot() {
    const K = window.MemuSettingsKit;
    if (!K || K.__msxHooked) return;
    const original = K.pageShown;
    if (typeof original !== "function") return;
    K.pageShown = function (...args) {
      const result = original.apply(this, args);
      clearTimeout(state.snapTimer);
      state.snapTimer = setTimeout(() => (window.requestIdleCallback || setTimeout)(captureSettings), 900);
      return result;
    };
    K.__msxHooked = true;
  }
  function boot() {
    hookSidebarFilter();
    hookSettingsSnapshot();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();

  let refitFrame = 0;
  window.addEventListener("resize", () => { if (!state.open) return; cancelAnimationFrame(refitFrame); refitFrame = requestAnimationFrame(renderChips); });
  /* ------------------------------------------------------------------ public API */
  function registerProvider(provider) {
    if (!provider || !provider.id || typeof provider.search !== "function") return false;
    const type = provider.type || provider.id;
    state.providers.set(provider.id, { ...provider, type });
    if (!TYPES[type]) state.custom[type] = { label: provider.label || [type, type], icon: provider.icon ? `<svg viewBox="0 0 24 24" aria-hidden="true">${provider.icon}</svg>` : "" };
    if (state.open) renderChips();
    return true;
  }
  function registerAction(name, fn) {
    if (name && typeof fn === "function") state.actions.set(String(name), fn);
  }
  async function query(q, opts = {}) {
    return http("/api/search", { method: "POST", body: { q, types: opts.types || [], filters: opts.filters || {}, limit: opts.limit || 30, lang: isEn() ? "en" : "ru" } });
  }

  window.MemuSearch = Object.freeze({
    open, close, toggle, isOpen: () => state.open, registerProvider, registerAction, query, reindex,
    captureSettings, hideFindbar,
    _state: state, _scoreText: clientScore, _hl: hl, _wordRegex: wordRegex, _swapLayout: swapLayout,
  });
})();
