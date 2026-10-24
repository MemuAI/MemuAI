/* MemuAI — the skill library window (Round 32).
 *
 * Replaces the old «Библиотека скиллов ИИ» modal of app.js: loads after app.js and takes over the global
 * showSkillsModal() (every entry point — sidebar, profile menu, help menu, MemuSelfDocs — calls it by name).
 * Storage stays app.js's: getSkills()/saveSkills() over localStorage «memu_skills»; this file adds a server
 * backup (/api/pg/skills/library) and the catalog of public skills (/api/pg/skills-catalog*, app/skills_catalog.py).
 *
 * Views (one dialog, no stacking): list (tabs: mine / built-in / catalog) → detail → editor; catalog → catalog detail.
 * Untrusted text (catalog SKILL.md, imported files) is rendered by renderMarkdown() which escapes everything first.
 * Scripts that come with a catalog skill are only listed / previewed — never executed, never added to the chat.
 */
(function () {
  "use strict";
  if (typeof window === "undefined" || typeof getSkills !== "function") return;

  const isEn = () => (typeof PG_LANG !== "undefined" ? PG_LANG : "ru") === "en";
  const L = (ru, en) => (isEn() ? en : ru);
  const h = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const TAB_KEY = "memu_skills_tab";

  /* ------------------------------------------------------------------ icons */
  const P = {
    code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5-3 14"/>',
    bug: '<path d="M8 9a4 4 0 0 1 8 0v5a4 4 0 0 1-8 0z"/><path d="M12 9v9M4 13h4M16 13h4M5 7l3 2M19 7l-3 2M5 19l3-2M19 19l-3-2"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
    layout: '<rect x="3.5" y="4" width="17" height="16" rx="2.5"/><path d="M3.5 9h17M9 9v11"/>',
    chart: '<path d="M4 20V4M4 20h16"/><path d="m8 15 3.5-4 3 2.5L19 8"/>',
    table: '<rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M3.5 14.5h17M9.5 4.5v15"/>',
    doc: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5M8.5 13h7M8.5 16.5h5"/>',
    slides: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M12 16v4M8.5 20h7"/>',
    research: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5.5 5.5"/><path d="M8 10.5h5M10.5 8v5"/>',
    pen: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    compass: '<circle cx="12" cy="12" r="8.5"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
    shield: '<path d="M12 3.5 5 6v5.5c0 4.3 3 7.7 7 9 4-1.3 7-4.7 7-9V6z"/><path d="m9 12 2 2 4-4"/>',
    plan: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3.5 6 1.2 1.2L7 5M3.5 12l1.2 1.2L7 11M3.5 18l1.2 1.2L7 17"/>',
    learn: '<path d="M2.5 9 12 4.5 21.5 9 12 13.5z"/><path d="M6.5 11v4.5c0 1.5 2.5 3 5.5 3s5.5-1.5 5.5-3V11M21.5 9v5"/>',
    image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.8"/><path d="m4.5 18 5-5 3.5 3.5 2.5-2.5 4 4"/>',
    spark: '<path d="M12 3.5 13.8 9l5.7 1.8-5.7 1.8L12 18.5l-1.8-5.9L4.5 10.8 10.2 9z"/><path d="M18.5 15.5l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/>',
    case: '<rect x="3.5" y="7" width="17" height="12.5" rx="2"/><path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7M3.5 12.5h17"/>',
    scale: '<path d="M12 4v16M7 20h10M5 7h14"/><path d="m5 7-2.5 6a2.8 2.8 0 0 0 5 0zM19 7l-2.5 6a2.8 2.8 0 0 0 5 0z"/>',
    mail: '<rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="m4 7 8 6 8-6"/>',
    notes: '<rect x="5" y="3.5" width="14" height="17" rx="2"/><path d="M8.5 8h7M8.5 12h7M8.5 16h4"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
    git: '<circle cx="6.5" cy="6" r="2"/><circle cx="6.5" cy="18" r="2"/><circle cx="17.5" cy="8" r="2"/><path d="M6.5 8v8M17.5 10c0 4-5 3-9.5 6.5"/>',
    list: '<path d="M8.5 6.5h11M8.5 12h11M8.5 17.5h7"/><circle cx="4.5" cy="6.5" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="17.5" r="1"/>',
    db: '<ellipse cx="12" cy="6" rx="7" ry="2.8"/><path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
    book: '<path d="M4.5 5.5A2 2 0 0 1 6.5 3.5H19.5v14H6.5a2 2 0 0 0-2 2z"/><path d="M4.5 19.5a2 2 0 0 0 2 2h13v-4"/>',
    box: '<path d="M12 3.5 20 8v8l-8 4.5L4 16V8z"/><path d="m4 8 8 4.5L20 8M12 12.5v8"/>',
    github: '<path d="M9 19c-4.3 1.4-4.3-2.5-6-3M15 21v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C6.5 2.8 5.4 3.1 5.4 3.1a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 4 9.5c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21"/>',
    star: '<path d="m12 3.8 2.5 5.2 5.7.7-4.2 3.9 1.1 5.6L12 16.4l-5.1 2.8L8 13.6 3.8 9.7l5.7-.7z"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    back: '<path d="M15 5l-7 7 7 7"/>',
    more: '<circle cx="5.5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18.5" cy="12" r="1.4"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    refresh: '<path d="M20 11.5A8 8 0 0 0 6.3 6.3L4 8.5M4 4v4.5h4.5M4 12.5a8 8 0 0 0 13.7 5.2L20 15.5M20 20v-4.5h-4.5"/>',
    warn: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.3"/>',
    external: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2"/><path d="M15.5 8.5V6A2 2 0 0 0 13.5 4H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
    trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5"/>',
    edit: '<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/>',
    upload: '<path d="M12 15.5V4M7 8.5 12 4l5 4.5M4.5 15v3.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    download: '<path d="M12 4v11.5M7 11l5 4.5 5-4.5M4.5 15v3.5A1.5 1.5 0 0 0 6 20h12a1.5 1.5 0 0 0 1.5-1.5V15"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    toggle: '<rect x="2.5" y="7" width="19" height="10" rx="5"/><circle cx="16.5" cy="12" r="3"/>',
    terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15H17"/>',
    file: '<path d="M14 3.5H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5z"/><path d="M14 3.5v5h5"/>',
    folder: '<path d="M3.5 7a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    filter: '<path d="M4 5.5h16l-6.2 7.3V19l-3.6-1.8v-4.4z"/>',
  };
  const svg = (name, cls = "") => `<svg class="sk-i ${cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${P[name] || P.box}</svg>`;

  const ID_ICON = {
    memuai_guide: "book", software_engineering: "code", debugging: "bug", code_review: "eye", web_artifacts: "layout",
    data_analysis: "chart", spreadsheets: "table", documents: "doc", slides: "slides", deep_research: "research",
    writing_editing: "pen", product_ux: "compass", security_review: "shield", project_planning: "plan",
    learning_coach: "learn", visual_direction: "image", prompt_agent_design: "spark", business_analysis: "case",
    legal_review: "scale", memuai_panels: "layout",
  };
  const NAMED_ICON = { mail: "mail", notes: "notes", globe: "globe", git: "git", list: "list", database: "db", sparkle: "spark" };
  const CAT_ICON = { development: "code", engineering: "shield", analysis: "chart", productivity: "plan", research: "research",
    writing: "pen", product: "compass", learning: "learn", creative: "image", ai: "spark", business: "case", legal: "scale", memuai: "book" };
  function iconFor(skill) {
    if (!skill) return "box";
    if (skill.icon && NAMED_ICON[skill.icon]) return NAMED_ICON[skill.icon];
    if (skill.icon && P[skill.icon]) return skill.icon;
    if (ID_ICON[skill.id]) return ID_ICON[skill.id];
    if (skill.source && skill.source.kind === "github") return "github";
    return CAT_ICON[String(skill.category || "").toLowerCase()] || (skill.custom ? "spark" : "box");
  }

  /* ------------------------------------------------------------------ markdown (escaped first) */
  function inline(text) {
    let out = h(text);
    const codes = [];
    out = out.replace(/`([^`\n]+)`/g, (_, code) => `\u0000${codes.push(code) - 1}\u0000`);
    out = out.replace(/\[([^\]\n]{1,300})\]\((https?:\/\/[^\s)]{1,800}|mailto:[^\s)]{1,200})\)/g,
      (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`);
    out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    out = out.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>").replace(/(^|[\s(])_([^_\n]+)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  }
  function renderMarkdown(source, opts = {}) {
    const text = String(source || "").replace(/\r\n?/g, "\n").replace(/^---\n[\s\S]*?\n---\n/, "");
    const lines = text.split("\n");
    const out = [];
    let i = 0, para = [], list = null;
    const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
    const li = (it) => `<li>${inline(it.text)}${it.sub.length ? `<${it.subTag}>${it.sub.map((x) => `<li>${inline(x)}</li>`).join("")}</${it.subTag}>` : ""}</li>`;
    const flushList = () => { if (list) { out.push(`<${list.tag}${list.start > 1 ? ` start="${list.start}"` : ""}>${list.items.map(li).join("")}</${list.tag}>`); list = null; } };
    const flush = () => { flushPara(); flushList(); };
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*(```+|~~~+)\s*([\w+#.-]*)/);
      if (fence) {
        flush();
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        i++;
        out.push(`<pre class="sk-code"${fence[2] ? ` data-lang="${h(fence[2])}"` : ""}><code>${h(body.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()) { flush(); i++; continue; }
      const head = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (head) { flush(); const lvl = Math.min(6, head[1].length + 1); out.push(`<h${lvl}>${inline(head[2])}</h${lvl}>`); i++; continue; }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push("<hr>"); i++; continue; }
      // Built-in playbooks use «WORKFLOW» / «RULES» lines as section titles.
      if (opts.capsHeadings && /^[A-Z][A-Z0-9 &/()'-]{3,60}$/.test(line.trim()) && !/[a-z]/.test(line)) {
        flush(); out.push(`<h4 class="sk-caps">${h(line.trim())}</h4>`); i++; continue;
      }
      if (/^\s*>/.test(line)) {
        flush();
        const quote = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
        out.push(`<blockquote>${inline(quote.join(" "))}</blockquote>`);
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        flush();
        const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const header = cells(line);
        i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(cells(lines[i++]));
        out.push(`<div class="sk-table"><table><thead><tr>${header.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      const bullet = line.match(/^\s*([-*+])\s+(.*)$/);
      const num = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
      if (bullet || num) {
        flushPara();
        const tag = bullet ? "ul" : "ol";
        const indented = /^\s{2,}/.test(line);
        if (list && indented && list.items.length) {
          // A nested item («   - Correctness: …» under «3. Apply the lenses»): it belongs to the previous item.
          const last = list.items[list.items.length - 1];
          last.subTag = last.sub.length ? last.subTag : tag;
          last.sub.push((bullet || num)[2]);
          i++;
          continue;
        }
        if (list && list.tag !== tag) flushList();
        if (!list) list = { tag, items: [], start: num ? +num[1] : 1 };
        list.items.push({ text: (bullet || num)[2], sub: [], subTag: "ul" });
        i++;
        continue;
      }
      if (list && /^\s{2,}\S/.test(line)) {
        const last = list.items[list.items.length - 1];
        if (last.sub.length) last.sub[last.sub.length - 1] += " " + line.trim(); else last.text += " " + line.trim();
        i++;
        continue;
      }
      flushList();
      para.push(line.trim());
      i++;
    }
    flush();
    return out.join("\n");
  }

  /* ------------------------------------------------------------------ data helpers */
  const state = {
    tab: localStorage.getItem(TAB_KEY) || "builtin", q: "", cat: "all", status: "all", view: "list", id: "",
    catalog: null, catalogState: "idle", catalogError: "", csource: "all", csort: "stars", cdetail: null, cfile: null,
    ctab: "doc", extra: {}, exploring: "", draft: null, editTab: "edit", menu: false, codeInstalled: null, scroll: 0,
  };
  // getSkills() parses the whole library from localStorage: one render reads it once (withSkills), not once per card.
  let memoSkills = null;
  const skills = () => memoSkills || getSkills();
  function withSkills(fn) {
    if (memoSkills) return fn();
    memoSkills = getSkills();
    try { return fn(); } finally { memoSkills = null; }
  }
  const builtinIds = () => new Set((typeof BUILTIN_SKILLS !== "undefined" ? BUILTIN_SKILLS : []).map((s) => s.id));
  const isMine = (s) => !!s.custom;
  const ui = (s, field) => (typeof skillUi === "function" ? skillUi(s, field) : s[field]) || "";
  const tokens = (text) => Math.max(1, Math.round(String(text || "").length / (/[\u0400-\u04ff]/.test(String(text || "").slice(0, 400)) ? 3.2 : 4)));
  // «Proprietary. LICENSE.txt has complete terms» → «Proprietary»; SPDX ids stay as they are.
  const shortLicense = (text) => {
    const v = String(text || "").trim();
    if (/^\S{1,24}$/.test(v)) return v;
    const head = v.split(/[.,;(]/)[0].trim();
    return /^\S{1,20}$/.test(head) ? head : L("Своя лицензия", "Custom license");
  };
  const fmtNum = (n) => (n >= 1000 ? (n >= 10000 ? Math.round(n / 1000) : (n / 1000).toFixed(1)) + "k" : String(n || 0));
  const newId = () => "custom_" + (crypto.randomUUID ? crypto.randomUUID().slice(0, 8) : Date.now().toString(36));

  // Agent Skills names are slugs («systematic-debugging»): show them as words.
  function prettyName(name) {
    const text = String(name || "").trim();
    if (!/^[a-z0-9]+(?:[-_][a-z0-9]+)+$|^[a-z][a-z0-9]*$/.test(text)) return text;
    const words = text.split(/[-_]/).map((w) => (/^(ai|api|ui|ux|mcp|pdf|seo|cro|sql|css|html|js|ts|ci|cd|llm|pr|qa|aws|gcp|ios|xlsx|docx|pptx|gif)$/.test(w) ? w.toUpperCase() : w));
    const joined = words.join(" ");
    return joined.charAt(0).toUpperCase() + joined.slice(1);
  }
  // An empty category is shown in the current language (stored labels would stay in the language of the day they were saved).
  function categoryLabel(s) {
    const kind = s.source && s.source.kind;
    return ui(s, "category") || (kind === "github" ? L("Из каталога", "From catalog") : kind === "file" || kind === "url" ? L("Импорт", "Imported")
      : s.custom ? L("Мои", "Mine") : L("Общее", "General"));
  }
  function sourceLabel(s) {
    if (s.source && s.source.kind === "github") return s.source.repo;
    if (s.source && s.source.kind === "file") return L("Из файла", "From a file");
    if (s.source && s.source.kind === "url") return L("По ссылке", "From a link");
    if (s.custom) return L("Мой скилл", "My skill");
    return L("Встроенный", "Built-in");
  }
  function persist(list) {
    saveSkills(list);
  }
  function update(id, patch) {
    const list = skills();
    const item = list.find((s) => s.id === id);
    if (!item) return null;
    Object.assign(item, patch);
    persist(list);
    return item;
  }
  function installedFromCatalog(repo, path) {
    const list = skills();
    if (!list.__gh) {
      list.__gh = new Map();
      list.forEach((s) => { if (s.source && s.source.kind === "github") list.__gh.set(s.source.repo + "\n" + (s.source.path || ""), s); });
    }
    return list.__gh.get(repo + "\n" + (path || ""));
  }

  /* ------------------------------------------------------------------ server backup of the library */
  let backupTimer = 0;
  function scheduleBackup() {
    clearTimeout(backupTimer);
    backupTimer = setTimeout(() => {
      try {
        const list = JSON.parse(localStorage.getItem("memu_skills") || "[]");
        const removed = JSON.parse(localStorage.getItem("memu_skills_removed") || "[]");
        fetch("/api/pg/skills/library", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ skills: list, version: +(localStorage.getItem("memu_skills_version") || 0), removed }) }).catch(() => {});
      } catch (e) { /* storage unavailable */ }
    }, 700);
  }
  const originalSave = window.saveSkills;
  if (typeof originalSave === "function" && !originalSave.__skBackup) {
    const wrapped = function (list) { originalSave(list); scheduleBackup(); };
    wrapped.__skBackup = true;
    window.saveSkills = wrapped;
  }
  // A cleared WebView storage (reinstall, profile reset) must not lose the user's own skills: restore from the backup.
  (async function restoreBackup() {
    try {
      if (localStorage.getItem("memu_skills")) return;
      const resp = await fetch("/api/pg/skills/library");
      if (!resp.ok) return;
      const data = await resp.json();
      // The user may have changed something while the request ran: never overwrite a library saved meanwhile.
      if (!Array.isArray(data.skills) || !data.skills.length || localStorage.getItem("memu_skills")) return;
      localStorage.setItem("memu_skills", JSON.stringify(data.skills));
      localStorage.setItem("memu_skills_version", String(data.version || 0));
      if (Array.isArray(data.removed) && data.removed.length) localStorage.setItem("memu_skills_removed", JSON.stringify(data.removed));
    } catch (e) { /* offline window server: nothing to restore */ }
  })();

  /* ------------------------------------------------------------------ shell */
  function root() { return document.querySelector("#panel.sk-panel"); }

  function open(opts = {}) {
    if (opts.tab) state.tab = opts.tab;
    if (!["mine", "builtin", "catalog"].includes(state.tab)) state.tab = "builtin";
    state.view = opts.view || "list";
    if (opts.id) state.id = opts.id;
    state.menu = false;
    panel(`<div class="sk-shell" data-view="list"></div>`, "sk-panel", { root: true, onClose: () => { state.menu = false; } });
    render();
    // panel() focuses its first control (the close button) in a microtask; the search field is the useful start.
    setTimeout(() => { const host = root(); if (host && state.view === "list" && host.contains(document.activeElement) && document.activeElement.classList.contains("panel-close")) host.querySelector("#sk-q")?.focus({ preventScroll: true }); }, 0);
  }

  function render() { withSkills(renderNow); }
  function renderNow() {
    const host = root();
    if (!host) return;
    const shell = host.querySelector(".sk-shell");
    const view = state.view;
    shell.dataset.view = view;
    const prevScroll = shell.querySelector(".sk-scroll")?.scrollTop || 0;
    if (view === "detail") shell.innerHTML = detailView();
    else if (view === "editor") shell.innerHTML = editorView();
    else if (view === "cdetail") shell.innerHTML = catalogDetailView();
    else if (view === "url") shell.innerHTML = urlView();
    else shell.innerHTML = listView();
    const heading = shell.querySelector("h2");
    if (heading) { heading.id = "panel-heading"; host.setAttribute("aria-labelledby", "panel-heading"); }
    bind(shell);
    if (view === "list" && state.keepScroll) { const sc = shell.querySelector(".sk-scroll"); if (sc) sc.scrollTop = prevScroll; }
    state.keepScroll = false;
  }

  function header() {
    const mine = skills().filter(isMine).length;
    const builtin = skills().filter((s) => !isMine(s)).length;
    const cat = state.catalog ? state.catalog.skills.length + Object.values(state.extra).reduce((n, r) => n + r.skills.length, 0) : 0;
    const tab = (id, label, count) => `<button type="button" role="tab" class="sk-seg-btn${state.tab === id ? " on" : ""}" data-tab="${id}" aria-selected="${state.tab === id}" tabindex="${state.tab === id ? 0 : -1}">
      <span>${label}</span>${count ? `<span class="sk-count">${count}</span>` : ""}</button>`;
    return `<header class="sk-head">
      <div class="sk-title">
        <span class="sk-logo">${svg("spark")}</span>
        <div><h2>${L("Скиллы", "Skills")}</h2><p>${L("Инструкции, которые модель подгружает, когда задача им подходит", "Playbooks the model loads when a task matches them")}</p></div>
      </div>
      <div class="sk-head-actions">
        <button type="button" class="sk-btn primary" data-act="new">${svg("plus")}<span>${L("Новый скилл", "New skill")}</span></button>
        <div class="sk-menu-wrap">
          <button type="button" class="sk-btn icon" data-act="menu" aria-haspopup="menu" aria-expanded="${state.menu}" title="${L("Импорт, экспорт и другое", "Import, export and more")}" aria-label="${L("Ещё", "More")}">${svg("more")}</button>
          ${state.menu ? menuHtml() : ""}
        </div>
      </div>
      <div class="sk-bar">
        <div class="sk-seg" role="tablist" aria-label="${L("Разделы библиотеки", "Library sections")}">
          ${tab("mine", L("Мои", "Mine"), mine)}${tab("builtin", L("Встроенные", "Built-in"), builtin)}${tab("catalog", L("Каталог", "Catalog"), cat)}
        </div>
        <label class="sk-search">${svg("search")}<input type="search" id="sk-q" value="${h(state.q)}" placeholder="${state.tab === "catalog" ? L("Поиск в каталоге", "Search the catalog") : L("Поиск скиллов", "Search skills")}" aria-label="${L("Поиск", "Search")}" autocomplete="off" spellcheck="false"><kbd>/</kbd></label>
      </div>
    </header>`;
  }

  function menuHtml() {
    const removed = removedSkillIds().filter((id) => builtinIds().has(id)).length;
    const item = (act, ico, label, hint = "") => `<button type="button" role="menuitem" data-act="${act}">${svg(ico)}<span>${label}${hint ? `<small>${hint}</small>` : ""}</span></button>`;
    return `<div class="sk-menu" role="menu">
      ${item("import-file", "upload", L("Импорт из файла", "Import from a file"), "SKILL.md, .zip, .json")}
      ${item("import-url", "link", L("Импорт по ссылке", "Import from a link"), L("GitHub или .md", "GitHub or .md"))}
      ${item("export-all", "download", L("Экспорт моих скиллов", "Export my skills"), ".zip (Agent Skills)")}
      <hr>
      ${item("enable-all", "toggle", L("Включить все", "Enable all"))}
      ${item("disable-all", "toggle", L("Выключить все", "Disable all"))}
      ${removed ? item("restore", "refresh", L("Вернуть удалённые встроенные", "Restore removed built-ins"), String(removed)) : ""}
      <hr>
      ${item("code-skills", "terminal", L("Скиллы MemuCode", "MemuCode skills"), L("для агента кода", "for the coding agent"))}
    </div>`;
  }

  /* ------------------------------------------------------------------ list: library */
  function filteredLibrary() {
    const q = state.q.trim().toLowerCase();
    return skills().filter((s) => (state.tab === "mine" ? isMine(s) : !isMine(s)))
      .filter((s) => state.cat === "all" || categoryLabel(s) === state.cat)
      .filter((s) => state.status === "all" || (state.status === "on" ? s.enabled : !s.enabled))
      .filter((s) => !q || [ui(s, "name"), ui(s, "desc"), ui(s, "when"), categoryLabel(s), s.id, sourceLabel(s), s.name, s.desc]
        .join(" ").toLowerCase().includes(q));
  }

  function listView() {
    const emptyMine = state.tab === "mine" && !skills().some(isMine);
    return header() + `<div class="sk-body${emptyMine ? " empty" : ""}">${state.tab === "catalog" ? catalogSide() + catalogMain() : (emptyMine ? "" : librarySide()) + libraryMain()}</div>`;
  }

  function librarySide() {
    const pool = skills().filter((s) => (state.tab === "mine" ? isMine(s) : !isMine(s)));
    const counts = new Map();
    pool.forEach((s) => counts.set(categoryLabel(s), (counts.get(categoryLabel(s)) || 0) + 1));
    const cats = [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    if (state.cat !== "all" && !counts.has(state.cat)) state.cat = "all";
    const chip = (value, label, n) => `<button type="button" class="sk-side-item${state.cat === value ? " on" : ""}" data-cat="${h(value)}" aria-pressed="${state.cat === value}"><span>${h(label)}</span><span class="sk-n">${n}</span></button>`;
    return `<nav class="sk-side" aria-label="${L("Категории", "Categories")}">
      <div class="sk-side-label">${L("Категории", "Categories")}</div>
      ${chip("all", L("Все", "All"), pool.length)}${cats.map(([c, n]) => chip(c, c, n)).join("")}
    </nav>`;
  }

  function libraryMain() {
    const rows = filteredLibrary();
    const pool = skills().filter((s) => (state.tab === "mine" ? isMine(s) : !isMine(s)));
    const on = pool.filter((s) => s.enabled).length;
    const status = (value, label) => `<button type="button" class="sk-chip${state.status === value ? " on" : ""}" data-status="${value}" aria-pressed="${state.status === value}">${label}</button>`;
    const toolbar = !pool.length ? "" : `<div class="sk-toolbar">
      <div class="sk-summary">${pool.length ? L(`Включено ${on} из ${pool.length}`, `${on} of ${pool.length} enabled`) : ""}</div>
      <div class="sk-chips" role="group" aria-label="${L("Состояние", "State")}">${status("all", L("Все", "All"))}${status("on", L("Включённые", "Enabled"))}${status("off", L("Выключенные", "Disabled"))}</div>
    </div>`;
    let body;
    if (!pool.length && state.tab === "mine") body = emptyMine();
    else if (!rows.length) body = emptyState("search", L("Ничего не найдено", "Nothing found"), L("Попробуйте другой запрос или категорию.", "Try another search or category."),
      `<button type="button" class="sk-btn" data-act="clear-filters">${L("Сбросить фильтры", "Clear filters")}</button>`);
    else body = `<div class="sk-grid">${rows.map(cardHtml).join("")}</div>`;
    const note = state.tab === "builtin" ? `<p class="sk-note">${svg("spark")}<span>${L("Держите включёнными хоть все: модель видит только короткий каталог и подгружает полную инструкцию, только когда она нужна.", "Keep as many enabled as you like: the model only sees a short catalogue and loads a full playbook when a task needs it.")}</span></p>` : "";
    return `<main class="sk-main"><div class="sk-scroll">${toolbar}${note}${body}</div></main>`;
  }

  function emptyMine() {
    return emptyState("spark", L("Здесь будут ваши скиллы", "Your skills will live here"),
      L("Создайте свой, добавьте готовый из каталога или импортируйте SKILL.md / .zip — формат Agent Skills из Claude Code и GitHub.",
        "Create your own, add one from the catalog or import a SKILL.md / .zip — the Agent Skills format used by Claude Code and GitHub."),
      `<button type="button" class="sk-btn primary" data-act="new">${svg("plus")}<span>${L("Новый скилл", "New skill")}</span></button>
       <button type="button" class="sk-btn" data-tab="catalog">${svg("github")}<span>${L("Открыть каталог", "Browse the catalog")}</span></button>
       <button type="button" class="sk-btn" data-act="import-file">${svg("upload")}<span>${L("Импорт", "Import")}</span></button>`);
  }

  function emptyState(ico, title, text, actions = "") {
    return `<div class="sk-empty"><span class="sk-empty-ico">${svg(ico)}</span><h3>${h(title)}</h3><p>${h(text)}</p>${actions ? `<div class="sk-empty-actions">${actions}</div>` : ""}</div>`;
  }

  function cardHtml(s) {
    const badges = [];
    if (s.customized) badges.push(`<span class="sk-badge">${L("Изменён", "Edited")}</span>`);
    if (s.source && s.source.kind === "github") {
      const fresh = catalogSkillFor(s.source.repo, s.source.path);
      if (fresh && fresh.sha && s.source.sha && fresh.sha !== s.source.sha) badges.push(`<span class="sk-badge accent">${L("Есть обновление", "Update available")}</span>`);
    }
    if (s.inline) badges.push(`<span class="sk-badge">${L("Всегда в промпте", "Always on")}</span>`);
    return `<article class="sk-card${s.enabled ? "" : " off"}" data-id="${h(s.id)}">
      <button type="button" class="sk-card-main" data-open="${h(s.id)}" aria-label="${h(ui(s, "name"))}">
        <span class="sk-tile">${svg(iconFor(s))}</span>
        <span class="sk-card-text">
          <span class="sk-card-name">${h(ui(s, "name"))}</span>
          <span class="sk-card-meta">${h(categoryLabel(s))}${isMine(s) ? ` · ${h(sourceLabel(s))}` : ""}</span>
        </span>
      </button>
      <label class="sk-switch" title="${s.enabled ? L("Выключить", "Turn off") : L("Включить", "Turn on")}"><input type="checkbox" data-toggle="${h(s.id)}" ${s.enabled ? "checked" : ""} aria-label="${h(L("Включён: ", "Enabled: ") + ui(s, "name"))}"><span></span></label>
      <p class="sk-card-desc">${h(ui(s, "desc") || ui(s, "when") || L("Без описания", "No description"))}</p>
      ${badges.length ? `<div class="sk-badges">${badges.join("")}</div>` : ""}
    </article>`;
  }

  /* ------------------------------------------------------------------ detail: library skill */
  function detailView() {
    const s = skills().find((x) => x.id === state.id);
    if (!s) { state.view = "list"; return listView(); }
    const builtin = !s.custom;
    const src = s.source || {};
    const fresh = src.kind === "github" ? catalogSkillFor(src.repo, src.path) : null;
    const hasUpdate = fresh && fresh.sha && src.sha && fresh.sha !== src.sha;
    const meta = [
      [L("Категория", "Category"), h(categoryLabel(s))],
      [L("Источник", "Source"), src.kind === "github" && src.url ? `<a href="${h(src.url)}" target="_blank" rel="noopener noreferrer">${h(src.repo)}${svg("external", "sm")}</a>` : h(sourceLabel(s))],
      src.license ? [L("Лицензия", "License"), h(src.license)] : null,
      src.installed_at ? [L("Добавлен", "Added"), h(new Date(src.installed_at).toLocaleDateString(isEn() ? "en-GB" : "ru-RU"))] : null,
      [L("Размер", "Size"), L(`≈ ${tokens(s.prompt)} токенов`, `≈ ${tokens(s.prompt)} tokens`)],
      ["ID", `<code>${h(s.id)}</code>`],
    ].filter(Boolean);
    const scripts = Array.isArray(s.files) ? s.files.filter((f) => f.kind === "script" || f.kind === "binary") : [];
    return `<div class="sk-sub">
      <div class="sk-subbar">
        <button type="button" class="sk-btn ghost" data-act="back">${svg("back")}<span>${state.tab === "mine" ? L("Мои скиллы", "My skills") : L("Встроенные", "Built-in")}</span></button>
        <div class="sk-subbar-actions">
          <button type="button" class="sk-btn" data-act="edit">${svg("edit")}<span>${L("Изменить", "Edit")}</span></button>
          <button type="button" class="sk-btn" data-act="duplicate">${svg("copy")}<span>${L("Дублировать", "Duplicate")}</span></button>
          <button type="button" class="sk-btn" data-act="export-one">${svg("download")}<span>SKILL.md</span></button>
          ${builtin && s.customized ? `<button type="button" class="sk-btn" data-act="reset">${svg("refresh")}<span>${L("Вернуть оригинал", "Restore original")}</span></button>` : ""}
          ${s.custom || s.removable ? `<button type="button" class="sk-btn danger icon" data-act="delete" title="${L("Удалить", "Delete")}" aria-label="${L("Удалить", "Delete")}">${svg("trash")}</button>` : ""}
        </div>
      </div>
      <div class="sk-scroll sk-detail" tabindex="-1">
        <section class="sk-hero">
          <span class="sk-tile lg">${svg(iconFor(s))}</span>
          <div class="sk-hero-text">
            <h2>${h(ui(s, "name"))}</h2>
            <p>${h(ui(s, "desc"))}</p>
          </div>
          <label class="sk-switch lg"><input type="checkbox" data-toggle="${h(s.id)}" ${s.enabled ? "checked" : ""} aria-label="${L("Включён", "Enabled")}"><span></span></label>
        </section>
        ${hasUpdate ? `<div class="sk-callout accent">${svg("refresh")}<div><b>${L("В каталоге новая версия", "A newer version is in the catalog")}</b><span>${L("Ваши правки инструкции будут заменены.", "Your edits to the instructions will be replaced.")}</span></div><button type="button" class="sk-btn primary" data-act="update-from-catalog">${L("Обновить", "Update")}</button></div>` : ""}
        ${scripts.length ? `<div class="sk-callout warn">${svg("warn")}<div><b>${L("В исходной папке были скрипты", "The original folder had scripts")}</b><span>${L("В чат добавлены только инструкции; скрипты MemuAI не запускает.", "Only the instructions were added to the chat; MemuAI never runs the scripts.")} ${h(scripts.map((f) => f.path).slice(0, 6).join(", "))}</span></div></div>` : ""}
        <div class="sk-detail-grid">
          <div class="sk-detail-main">
            <h3 class="sk-h">${L("Когда подключается", "When it loads")}</h3>
            <div class="sk-when">${ui(s, "when") ? h(ui(s, "when")) : `<span class="sk-muted">${L("Триггер не задан — модель выбирает по описанию.", "No trigger set — the model decides from the description.")}</span>`}</div>
            <h3 class="sk-h">${L("Инструкции", "Instructions")}</h3>
            <div class="sk-doc">${renderMarkdown(s.prompt, { capsHeadings: true }) || `<p class="sk-muted">${L("Пусто", "Empty")}</p>`}</div>
          </div>
          <aside class="sk-detail-aside">
            <dl class="sk-meta">${meta.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
            ${s.inline ? `<p class="sk-muted small">${L("Этот скилл короткий и всегда стоит в системном промпте.", "This skill is short and always sits in the system prompt.")}</p>` : `<p class="sk-muted small">${L("Модель видит только название и триггер; полный текст приходит, когда задача подходит.", "The model sees only the name and trigger; the full text arrives when a task matches.")}</p>`}
          </aside>
        </div>
      </div>
    </div>`;
  }

  /* ------------------------------------------------------------------ editor */
  function startEdit(id, duplicate = false) {
    const s = id ? skills().find((x) => x.id === id) : null;
    state.draft = s ? {
      id: duplicate ? "" : s.id, builtin: !s.custom && !duplicate,
      name: duplicate ? `${ui(s, "name")} ${L("(копия)", "(copy)")}` : ui(s, "name"), category: ui(s, "category"),
      desc: ui(s, "desc"), when: ui(s, "when"), prompt: s.prompt || "", from: s.id,
    } : { id: "", builtin: false, name: "", category: "", desc: "", when: "", prompt: "" };
    state.editTab = "edit";
    state.view = "editor";
    render();
    setTimeout(() => root()?.querySelector("#sk-f-name")?.focus(), 30);
  }

  function editorView() {
    const d = state.draft || {};
    const cats = [...new Set(skills().map(categoryLabel))].sort();
    return `<div class="sk-sub">
      <div class="sk-subbar">
        <button type="button" class="sk-btn ghost" data-act="cancel-edit">${svg("back")}<span>${L("Отмена", "Cancel")}</span></button>
        <h2 class="sk-subtitle">${d.id ? L("Изменить скилл", "Edit skill") : L("Новый скилл", "New skill")}</h2>
        <div class="sk-subbar-actions">
          <div class="sk-seg small sk-edit-tabs" role="tablist">
            <button type="button" role="tab" class="sk-seg-btn${state.editTab === "edit" ? " on" : ""}" data-etab="edit" aria-selected="${state.editTab === "edit"}">${L("Текст", "Write")}</button>
            <button type="button" role="tab" class="sk-seg-btn${state.editTab === "preview" ? " on" : ""}" data-etab="preview" aria-selected="${state.editTab === "preview"}">${L("Просмотр", "Preview")}</button>
          </div>
          <button type="button" class="sk-btn primary" data-act="save" title="Ctrl+S">${svg("check")}<span>${L("Сохранить", "Save")}</span></button>
        </div>
      </div>
      <div class="sk-editor" data-etab="${state.editTab}">
        <form class="sk-form sk-scroll" autocomplete="off" onsubmit="return false">
          <div class="sk-row2">
            <label class="sk-field"><span>${L("Название", "Name")} <b aria-hidden="true">*</b></span><input id="sk-f-name" maxlength="80" value="${h(d.name)}" placeholder="${L("Например: Ревью SQL-миграций", "e.g. SQL migration review")}" required></label>
            <label class="sk-field"><span>${L("Категория", "Category")}</span><input id="sk-f-cat" maxlength="40" list="sk-cats" value="${h(d.category)}" placeholder="${L("Разработка", "Development")}"><datalist id="sk-cats">${cats.map((c) => `<option value="${h(c)}">`).join("")}</datalist></label>
          </div>
          <label class="sk-field"><span>${L("Описание", "Description")}</span><input id="sk-f-desc" maxlength="400" value="${h(d.desc)}" placeholder="${L("Что делает скилл — одно предложение", "What the skill does — one sentence")}"></label>
          <label class="sk-field"><span>${L("Когда подключать (триггер)", "When to load it (trigger)")}</span><textarea id="sk-f-when" rows="2" maxlength="400" placeholder="${L("Конкретные ситуации и «Не для …», например: «Ревью миграции БД. Не для обычных запросов SQL.»", "Concrete situations plus “Not for …”, e.g. “Reviewing a DB migration. Not for plain SQL queries.”")}">${h(d.when)}</textarea>
            <small>${L("По этой строке модель решает, загружать ли скилл. Коротко и конкретно.", "The model decides whether to load the skill from this line. Keep it short and concrete.")}</small></label>
          <label class="sk-field grow"><span>${L("Инструкции", "Instructions")} <b aria-hidden="true">*</b><em class="sk-tok" id="sk-tok">${L(`≈ ${tokens(d.prompt)} токенов`, `≈ ${tokens(d.prompt)} tokens`)}</em></span>
            <textarea id="sk-f-prompt" class="mono" spellcheck="false" placeholder="${L("Markdown. Цель, порядок работы, правила, формат результата…", "Markdown. Purpose, workflow, rules, output format…")}">${h(d.prompt)}</textarea></label>
          <p class="sk-error" id="sk-f-error" role="alert" hidden></p>
        </form>
        <div class="sk-preview sk-scroll" aria-label="${L("Предпросмотр", "Preview")}">
          <div class="sk-preview-label">${L("Карточка", "Card")}</div>
          <div class="sk-preview-card" id="sk-pv-card"></div>
          <div class="sk-preview-label">${L("Инструкции", "Instructions")}</div>
          <div class="sk-doc" id="sk-pv-doc"></div>
        </div>
      </div>
    </div>`;
  }

  function readDraft(shell) {
    const v = (id) => shell.querySelector(id)?.value ?? "";
    Object.assign(state.draft, { name: v("#sk-f-name"), category: v("#sk-f-cat"), desc: v("#sk-f-desc"), when: v("#sk-f-when"), prompt: v("#sk-f-prompt") });
    return state.draft;
  }
  function paintPreview(shell) {
    if (!state.draft || state.view !== "editor" || !shell.isConnected) return;   // a debounced repaint after Save / Cancel
    const d = readDraft(shell);
    const card = shell.querySelector("#sk-pv-card");
    if (card) card.innerHTML = `<span class="sk-tile">${svg(d.from ? iconFor(skills().find((x) => x.id === d.from)) : "spark")}</span>
      <span class="sk-card-text"><span class="sk-card-name">${h(d.name || L("Без названия", "Untitled"))}</span><span class="sk-card-meta">${h(d.category || L("Мои", "Mine"))}</span></span>
      <p class="sk-card-desc">${h(d.desc || d.when || L("Без описания", "No description"))}</p>`;
    const doc = shell.querySelector("#sk-pv-doc");
    if (doc) doc.innerHTML = renderMarkdown(d.prompt, { capsHeadings: true }) || `<p class="sk-muted">${L("Здесь появится текст инструкции.", "The instructions will appear here.")}</p>`;
    const tok = shell.querySelector("#sk-tok");
    if (tok) tok.textContent = L(`≈ ${tokens(d.prompt)} токенов`, `≈ ${tokens(d.prompt)} tokens`);
  }

  function saveDraft(shell) {
    const d = readDraft(shell);
    const error = shell.querySelector("#sk-f-error");
    const fail = (msg, field) => { error.textContent = msg; error.hidden = false; shell.querySelector(field)?.focus(); };
    if (!d.name.trim()) return fail(L("Укажите название.", "Enter a name."), "#sk-f-name");
    if (d.prompt.trim().length < 10) return fail(L("Напишите инструкции (хотя бы пару предложений).", "Write the instructions (at least a couple of sentences)."), "#sk-f-prompt");
    const list = skills();
    const fields = { name: d.name.trim().slice(0, 120), desc: d.desc.trim().slice(0, 400), when: d.when.trim().slice(0, 400),
      category: d.category.trim().slice(0, 60), prompt: d.prompt.trim().slice(0, 200000) };
    let id = d.id;
    if (id) {
      const item = list.find((x) => x.id === id);
      if (!item) return fail(L("Скилл не найден.", "Skill not found."));
      Object.assign(item, fields);
      if (!item.custom) item.customized = true;
    } else {
      id = newId();
      list.push({ id, ...fields, custom: true, enabled: true, source: { kind: "user", created_at: Date.now() } });
      state.tab = "mine";
      localStorage.setItem(TAB_KEY, "mine");
    }
    persist(list);
    state.id = id;
    state.view = "detail";
    state.draft = null;
    render();
    toast(L("Скилл сохранён", "Skill saved"), "ok");
  }

  /* ------------------------------------------------------------------ catalog */
  // The merged catalog (+ explored repositories) and its repo/path index are rebuilt only when a source changes.
  let catalogMemo = { parts: [], all: [], byKey: new Map() };
  function allCatalogSkills() {
    if (!state.catalog) return [];
    const parts = [state.catalog].concat(Object.values(state.extra));
    if (parts.length !== catalogMemo.parts.length || parts.some((part, i) => part !== catalogMemo.parts[i])) {
      const all = state.catalog.skills.concat(...Object.values(state.extra).map((r) => r.skills.map((s) => ({ ...s, explored: true }))));
      const byKey = new Map();
      all.forEach((s) => { const key = s.repo + "\n" + (s.path || ""); if (!byKey.has(key)) byKey.set(key, s); });
      catalogMemo = { parts, all, byKey };
    }
    return catalogMemo.all;
  }
  function catalogSkillFor(repo, path) {
    if (!state.catalog) return null;
    allCatalogSkills();
    return catalogMemo.byKey.get(repo + "\n" + (path || "")) || null;
  }
  const searchText = new WeakMap();
  function catalogHaystack(s) {
    let text = searchText.get(s);
    if (text === undefined) { text = `${s.name} ${prettyName(s.name)} ${s.description} ${s.repo} ${s.category || ""}`.toLowerCase(); searchText.set(s, text); }
    return text;
  }

  // Stale-while-revalidate: the server answers at once with the saved catalog (or what a first load has so far) and
  // refreshes in the background; a long-poll (?since=version) brings each finished source — no timers.
  const CATALOG_URL = "/api/pg/skills-catalog";
  let loadSeq = 0;
  function repaintCatalog() {
    if (root() && state.view === "list" && state.tab === "catalog") { state.keepScroll = true; render(); }
  }
  async function loadCatalog(refresh = false) {
    if (state.refreshing && !refresh) return;
    const seq = ++loadSeq;
    if (!state.catalog) state.catalogState = "loading";
    state.catalogError = "";
    state.refreshing = true;
    repaintCatalog();
    try {
      let data = await api(CATALOG_URL + "?swr=1" + (refresh ? "&refresh=1" : ""));
      for (let round = 0; round < 60 && seq === loadSeq; round++) {
        if (data.skills.length || !data.refreshing) { state.catalog = data; state.catalogState = "ok"; }
        state.refreshing = !!data.refreshing;
        state.catalogReset = (data.rate && data.rate.limited_until) || 0;
        if (!data.refreshing) break;
        repaintCatalog();
        data = await api(CATALOG_URL + "?swr=1&since=" + encodeURIComponent(data.version));
      }
      if (seq !== loadSeq) return;
      const c = state.catalog;
      if (c && !c.skills.length && c.state !== "ok") {
        state.catalogState = "error";
        state.catalogError = c.state === "partial" ? "error" : c.state;
        state.catalogMessage = (c.errors && c.errors[0] && c.errors[0].message) || "";
        state.catalog = null;
      }
    } catch (error) {
      if (seq !== loadSeq) return;
      state.catalogState = state.catalog ? "ok" : "error";
      state.catalogError = (error.payload && error.payload.state) || "error";
      state.catalogMessage = error.message;
      state.catalogReset = error.payload && error.payload.reset;
      if (state.catalog) toast(catalogErrorText(state.catalogError, error.message), "warn", 6000);
    }
    if (seq !== loadSeq) return;
    state.refreshing = false;
    repaintCatalog();
  }

  async function exploreRepo(repo) {
    state.exploring = repo;
    render();
    try {
      const data = await api("/api/pg/skills-catalog/repo?repo=" + encodeURIComponent(repo));
      state.extra[repo] = data;
      state.csource = repo;
      if (!data.skills.length) toast(L("В этом репозитории нет папок со SKILL.md", "This repository has no SKILL.md folders"), "warn");
    } catch (error) {
      toast(catalogErrorText(error.payload && error.payload.state, error.message), "err", 6000);
    }
    state.exploring = "";
    if (root()) render();
  }

  function catalogErrorText(code, message = "") {
    if (code === "offline") return L("Нет связи с GitHub. Проверьте интернет или прокси.", "Can't reach GitHub. Check the internet connection or proxy.");
    if (code === "rate_limited") {
      const reset = state.catalogReset ? new Date(state.catalogReset * 1000).toLocaleTimeString(isEn() ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
      const why = L(" Без токена GitHub даёт 60 запросов в час; уже загруженное остаётся в кэше.", " Without a token GitHub allows 60 requests an hour; what was loaded stays cached.");
      return (reset ? L("GitHub временно ограничил запросы — попробуйте после ", "GitHub is rate-limiting requests — try again after ") + reset + "."
        : L("GitHub временно ограничил запросы.", "GitHub is rate-limiting requests.")) + why;
    }
    if (code === "not_found") return L("Не найдено на GitHub.", "Not found on GitHub.");
    return message || L("Не удалось загрузить каталог.", "Couldn't load the catalog.");
  }

  function catalogSide() {
    const c = state.catalog;
    const all = allCatalogSkills();
    const item = (value, label, n, extra = "") => `<button type="button" class="sk-side-item${state.csource === value ? " on" : ""}" data-csource="${h(value)}" aria-pressed="${state.csource === value}"><span>${label}</span>${extra}<span class="sk-n">${n}</span></button>`;
    const sources = c ? c.sources.map((s) => item(s.repo, `${h(isEn() ? s.label_en : s.label_ru)}<small>${h(s.repo)}</small>`, s.count || 0, s.official ? `<span class="sk-dot" title="${L("Официальный", "Official")}"></span>` : "")).join("") : "";
    const explored = Object.values(state.extra).map((r) => item(r.source.repo, `${h(r.source.repo.split("/")[1])}<small>${h(r.source.repo)}</small>`, r.skills.length)).join("");
    return `<nav class="sk-side" aria-label="${L("Источники", "Sources")}">
      <div class="sk-side-label">${L("Источники", "Sources")}</div>
      ${item("all", L("Все источники", "All sources"), all.length)}
      ${item("installed", L("Добавленные", "Added"), skills().filter((s) => s.source && s.source.kind === "github").length)}
      ${sources}${explored}
      ${c && c.repos && c.repos.length ? item("repos", L("Найти ещё на GitHub", "Discover on GitHub"), c.repos.length) : ""}
    </nav>`;
  }

  function sortRows(rows) {
    const by = state.csort;
    return rows.slice().sort((a, b) => by === "name" ? String(a.name).localeCompare(String(b.name))
      : by === "updated" ? String(b.updated || "").localeCompare(String(a.updated || "")) || (b.stars - a.stars)
      : (Number(!!b.official) - Number(!!a.official)) || (b.stars - a.stars) || String(a.name).localeCompare(String(b.name)));
  }

  function catalogMain() {
    if (state.catalogState === "idle") setTimeout(() => loadCatalog(false), 0);
    const c = state.catalog;
    const loading = !c && (state.catalogState === "loading" || state.catalogState === "idle");
    const sort = `<label class="sk-select"><span class="sr-only">${L("Сортировка", "Sort")}</span><select id="sk-csort" aria-label="${L("Сортировка", "Sort")}">
      <option value="stars"${state.csort === "stars" ? " selected" : ""}>${L("По звёздам", "Most stars")}</option>
      <option value="updated"${state.csort === "updated" ? " selected" : ""}>${L("Недавно обновлённые", "Recently updated")}</option>
      <option value="name"${state.csort === "name" ? " selected" : ""}>${L("По названию", "By name")}</option></select></label>`;
    const fetched = c && c.fetched_at ? new Date(c.fetched_at * 1000).toLocaleTimeString(isEn() ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" }) : "";
    let status = "";
    if (c && c.state === "partial") status = `<span class="sk-warn-text">${svg("warn")}${L("Часть источников недоступна — показано то, что удалось загрузить.", "Some sources are unavailable — showing what loaded.")}</span>`;
    else if (c && (c.state === "offline" || c.state === "rate_limited")) status = `<span class="sk-warn-text">${svg("warn")}${h(catalogErrorText(c.state))}</span>`;
    else if (fetched) status = `<span>${L(`GitHub · обновлено в ${fetched}`, `GitHub · updated at ${fetched}`)}</span>`;
    if (c && state.refreshing) status = `<span class="sk-spin" aria-hidden="true"></span><span>${c.complete === false ? L(`Загружено источников: ${c.sources.length} из ${c.sources.length + 1}+…`, `Sources loaded: ${c.sources.length}…`) : L("Показан сохранённый каталог, обновляю…", "Showing the saved catalog, refreshing…")}</span>`;
    const toolbar = `<div class="sk-toolbar">
      <div class="sk-summary" aria-live="polite">${loading ? `<span class="sk-spin" aria-hidden="true"></span>${L("Загружаю каталог с GitHub…", "Loading the catalog from GitHub…")}` : status}</div>
      <div class="sk-tools">${state.csource === "repos" ? "" : sort}<button type="button" class="sk-btn icon" data-act="refresh-catalog" title="${L("Обновить с GitHub", "Refresh from GitHub")}" aria-label="${L("Обновить", "Refresh")}" ${loading || state.refreshing ? "disabled" : ""}>${svg("refresh")}</button></div>
    </div>`;
    if (state.catalogState === "error" && !c) {
      return `<main class="sk-main"><div class="sk-scroll">${toolbar}${emptyState(state.catalogError === "rate_limited" ? "clock" : "globe",
        state.catalogError === "offline" ? L("Каталог недоступен офлайн", "The catalog needs the internet") : L("Не удалось загрузить каталог", "Couldn't load the catalog"),
        catalogErrorText(state.catalogError, state.catalogMessage),
        `<button type="button" class="sk-btn primary" data-act="refresh-catalog">${svg("refresh")}<span>${L("Повторить", "Try again")}</span></button>
         <button type="button" class="sk-btn" data-act="import-url">${svg("link")}<span>${L("Добавить по ссылке", "Add from a link")}</span></button>`)}</div></main>`;
    }
    if (loading && !c) return `<main class="sk-main"><div class="sk-scroll">${toolbar}<div class="sk-grid">${Array.from({ length: 9 }, () => `<div class="sk-card skeleton"><span class="sk-tile"></span><i></i><i></i><i class="short"></i></div>`).join("")}</div></div></main>`;
    if (state.csource === "repos") return `<main class="sk-main"><div class="sk-scroll">${toolbar}${reposHtml()}</div></main>`;
    const q = state.q.trim().toLowerCase();
    let rows = allCatalogSkills();
    if (state.csource === "installed") rows = rows.filter((s) => installedFromCatalog(s.repo, s.path));
    else if (state.csource !== "all") rows = rows.filter((s) => s.repo === state.csource);
    if (q) rows = rows.filter((s) => catalogHaystack(s).includes(q));
    rows = sortRows(rows);
    vgrid.rows = rows;
    const intro = state.csource === "all" && !q ? `<p class="sk-note">${svg("github")}<span>${L("Открытые скиллы в формате Agent Skills (SKILL.md) из репозиториев GitHub. Добавленный скилл попадает в «Мои»; скрипты из папок скиллов MemuAI не запускает.", "Open skills in the Agent Skills format (SKILL.md) from GitHub repositories. An added skill goes to “Mine”; MemuAI never runs scripts from skill folders.")}</span></p>` : "";
    // Thousands of skills: only the rows in view are in the DOM (mountVirtual paints them after the shell is in place).
    const body = rows.length ? `<div class="sk-vgrid" role="list" aria-label="${L("Скиллы каталога", "Catalog skills")}" data-count="${rows.length}"></div>`
      : emptyState("search", L("Ничего не найдено", "Nothing found"), q ? L("Попробуйте другой запрос или «Найти ещё на GitHub».", "Try another search or “Discover on GitHub”.") : L("В этом источнике пока нет скиллов.", "This source has no skills yet."));
    return `<main class="sk-main"><div class="sk-scroll">${toolbar}${intro}${body}</div></main>`;
  }

  /* ------------------------------------------------------------------ virtual catalog grid
   * Cards have a fixed height, so the rows in view are pure arithmetic: no measuring while scrolling, one passive
   * scroll listener coalesced to a frame, ~30 cards in the DOM for any catalog size. Rows are recycled: a row that
   * leaves the view is refilled for the row that enters (text and a few `hidden` flags change, no new elements), so a
   * scroll does almost no style work even with the app's large stylesheet. */
  const VG_MIN = 272, VG_GAP = 12, VG_CARD = 168, VG_OVERSCAN = 3;
  const vgrid = { rows: [], cols: 1, rowH: VG_CARD + VG_GAP, offset: 0, raf: 0, ro: null, live: new Map(), free: [], tagWidth: 200 };

  function cardShell() {
    return `<article class="sk-card cat" role="listitem">
      <button type="button" class="sk-card-main">
        <span class="sk-tile">${svg("github")}</span>
        <span class="sk-card-text"><span class="sk-card-name"></span>
        <span class="sk-card-meta"><span data-f="repo"></span> · ${svg("star", "sm")} <span data-f="stars"></span></span></span>
      </button>
      <div class="sk-card-act">
        <button type="button" class="sk-btn small icon" data-f="add" title="${L("Добавить в библиотеку", "Add to library")}">${svg("plus")}</button>
        <span class="sk-added" data-f="added" title="${L("Уже в библиотеке", "Already in your library")}" role="img" aria-label="${L("Добавлен", "Added")}">${svg("check")}</span>
        <button type="button" class="sk-btn small icon primary" data-f="update" title="${L("Обновить до новой версии", "Update to the new version")}">${svg("refresh")}</button>
      </div>
      <p class="sk-card-desc"></p>
      <div class="sk-card-foot"><div class="sk-badges"><span class="sk-badge accent" data-f="official">${L("Официальный", "Official")}</span><span class="sk-badge warn" data-f="scripts">${svg("warn", "sm")}${L("Скрипты", "Scripts")}</span><span class="sk-badge" data-f="license"></span><span class="sk-badge sk-more" data-f="more"></span></div></div>
    </article>`;
  }

  // Tag widths come from canvas text metrics (no layout reads): tags that do not fit the card's single tag row fold
  // into «+N» with a hint that lists them, so no tag is ever cut.
  const tagCache = new Map();
  let tagCtx = null;
  function tagWidth(text, icon) {
    const key = text + (icon ? "\u0001" : "");
    let width = tagCache.get(key);
    if (width === undefined) {
      if (!tagCtx) { tagCtx = document.createElement("canvas").getContext("2d"); }
      const font = getComputedStyle(root() || document.body).fontFamily;
      tagCtx.font = `560 11px ${font}`;
      width = Math.ceil(tagCtx.measureText(text).width) + 16 + (icon ? 17 : 0);
      tagCache.set(key, width);
    }
    return width;
  }

  function setHidden(el, hide) { if (el.hidden !== hide) el.hidden = hide; }
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }

  function fillCard(card, s, index) {
    const f = card.__f || (card.__f = Object.fromEntries([...card.querySelectorAll("[data-f]")].map((el) => [el.dataset.f, el]).concat(
      [["main", card.querySelector(".sk-card-main")], ["name", card.querySelector(".sk-card-name")], ["desc", card.querySelector(".sk-card-desc")]])));
    if (!s) { setHidden(card, true); return; }
    setHidden(card, false);
    const name = prettyName(s.name);
    card.dataset.cid = s.id;
    f.main.dataset.copen = s.id;
    f.main.dataset.i = index;
    f.main.setAttribute("aria-label", name);
    setText(f.name, name);
    setText(f.repo, s.repo);
    setText(f.stars, fmtNum(s.stars));
    setText(f.desc, s.description || (s.unavailable ? L("Не удалось прочитать SKILL.md", "Couldn't read SKILL.md") : L("Без описания", "No description")));
    const mine = installedFromCatalog(s.repo, s.path);
    const update = !!(mine && mine.source.sha && s.sha && mine.source.sha !== s.sha);
    setHidden(f.add, !!mine); setHidden(f.added, !mine || update); setHidden(f.update, !update);
    f.add.disabled = !!(s.unavailable || s.too_large);
    f.add.dataset.cadd = s.id; f.update.dataset.cupdate = s.id;
    f.add.setAttribute("aria-label", L("Добавить: ", "Add: ") + name);
    f.update.setAttribute("aria-label", L("Обновить: ", "Update: ") + name);
    const license = s.license ? shortLicense(s.license) : "";
    f.scripts.title = s.has_scripts ? L("Скрипты: ", "Scripts: ") + (s.scripts || []).join(", ") : "";
    f.license.title = s.license || "";
    setText(f.license, license);
    const tags = [[f.official, !!s.official, L("Официальный", "Official"), false], [f.scripts, !!s.has_scripts, L("Скрипты", "Scripts"), true],
      [f.license, !!license, license, false]].filter((t) => t[1]);
    const room = vgrid.tagWidth;
    let used = 0, shown = 0;
    const total = tags.reduce((sum, t) => sum + tagWidth(t[2], t[3]) + 5, -5);
    const hidden = [];
    tags.forEach((t, i) => {
      const w = tagWidth(t[2], t[3]);
      const reserve = total <= room ? 0 : tagWidth("+" + (tags.length - i), false) + 5;
      if (!hidden.length && (i === 0 || used + 5 + w + reserve <= room)) { used += (shown ? 5 : 0) + w; shown++; } else hidden.push(t[2]);
    });
    [f.official, f.scripts, f.license].forEach((el) => setHidden(el, !tags.some((t) => t[0] === el) || hidden.includes(
      el === f.official ? L("Официальный", "Official") : el === f.scripts ? L("Скрипты", "Scripts") : license)));
    setHidden(f.more, !hidden.length);
    if (hidden.length) { setText(f.more, "+" + hidden.length); f.more.title = hidden.join(", "); f.more.setAttribute("aria-label", L("Ещё: ", "More: ") + hidden.join(", ")); }
  }

  function mountVirtual(shell) {
    const box = shell.querySelector(".sk-vgrid");
    const scroller = box && box.closest(".sk-scroll");
    if (vgrid.ro) { vgrid.ro.disconnect(); vgrid.ro = null; }
    if (!box || !scroller) return;
    layoutVirtual(box, scroller);
    scroller.addEventListener("scroll", () => {
      if (!vgrid.raf) vgrid.raf = requestAnimationFrame(() => { vgrid.raf = 0; paintWindow(box, scroller); });
    }, { passive: true });
    box.addEventListener("click", onGridClick);
    box.addEventListener("keydown", (event) => onGridKey(event, box, scroller));
    if (typeof ResizeObserver === "function") {
      let width = box.clientWidth;
      vgrid.ro = new ResizeObserver(() => {
        if (!box.isConnected) { vgrid.ro && vgrid.ro.disconnect(); return; }
        if (Math.abs(box.clientWidth - width) < 1) return;
        width = box.clientWidth;
        layoutVirtual(box, scroller);
      });
      vgrid.ro.observe(box);
    }
  }

  function layoutVirtual(box, scroller) {
    const width = box.clientWidth || scroller.clientWidth;
    vgrid.cols = Math.max(1, Math.floor((width + VG_GAP) / (VG_MIN + VG_GAP)));
    vgrid.tagWidth = Math.floor((width - (vgrid.cols - 1) * VG_GAP) / vgrid.cols) - 30;   // card padding + border
    const rows = Math.ceil(vgrid.rows.length / vgrid.cols);
    box.style.height = Math.max(0, rows * vgrid.rowH - VG_GAP) + "px";
    box.style.setProperty("--sk-cols", vgrid.cols);
    box.textContent = "";                // a new column count re-flows every row
    vgrid.live = new Map();
    vgrid.free = [];
    vgrid.offset = box.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
    paintWindow(box, scroller);
  }

  function paintWindow(box, scroller) {
    if (!box.isConnected) return;
    const top = scroller.scrollTop - vgrid.offset;
    const total = Math.ceil(vgrid.rows.length / vgrid.cols);
    const first = Math.max(0, Math.floor(top / vgrid.rowH) - VG_OVERSCAN);
    const last = Math.min(total - 1, Math.floor((top + scroller.clientHeight) / vgrid.rowH) + VG_OVERSCAN);
    for (const [row, el] of vgrid.live) {
      if (row < first || row > last) { vgrid.live.delete(row); vgrid.free.push(el); }
    }
    withSkills(() => {
      for (let row = first; row <= last; row++) {
        if (vgrid.live.has(row)) continue;
        let el = vgrid.free.pop();
        if (!el) {
          el = document.createElement("div");
          el.className = "sk-grid sk-vrow";
          el.innerHTML = Array.from({ length: vgrid.cols }, cardShell).join("");
          box.appendChild(el);
        }
        el.style.transform = `translateY(${row * vgrid.rowH}px)`;
        const from = row * vgrid.cols;
        [...el.children].forEach((card, k) => fillCard(card, vgrid.rows[from + k], from + k));
        vgrid.live.set(row, el);
      }
    });
    // Spare rows park above the list (an inline transform, not display:none — showing them again would restyle them).
    vgrid.free.forEach((el) => { el.style.transform = "translateY(-100000px)"; });
  }

  function onGridClick(event) {
    const add = event.target.closest("[data-cadd], [data-cupdate]");
    if (add) { event.stopPropagation(); add.disabled = true; addFromCatalog(add.dataset.cadd || add.dataset.cupdate); return; }
    const open = event.target.closest("[data-copen]");
    if (open) openCatalogSkill(open.dataset.copen);
  }

  function onGridKey(event, box, scroller) {
    const card = event.target.closest && event.target.closest(".sk-card-main[data-i]");
    if (!card || !["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const i = +card.dataset.i;
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: vgrid.cols, ArrowUp: -vgrid.cols }[event.key];
    const next = event.key === "Home" ? 0 : event.key === "End" ? vgrid.rows.length - 1 : i + step;
    event.preventDefault();
    if (next < 0) { root()?.querySelector("#sk-q")?.focus(); return; }
    if (next >= vgrid.rows.length) return;
    const row = Math.floor(next / vgrid.cols);
    const rowTop = vgrid.offset + row * vgrid.rowH;
    if (rowTop < scroller.scrollTop) scroller.scrollTop = rowTop - 8;
    else if (rowTop + VG_CARD > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = rowTop + VG_CARD - scroller.clientHeight + 8;
    paintWindow(box, scroller);
    box.querySelector(`[data-i="${next}"]`)?.focus({ preventScroll: true });
  }

  function reposHtml() {
    const c = state.catalog;
    const q = state.q.trim().toLowerCase();
    const rows = (c.repos || []).filter((r) => !q || `${r.repo} ${r.description} ${(r.topics || []).join(" ")}`.toLowerCase().includes(q));
    return `<p class="sk-note">${svg("search")}<span>${L("Репозитории с темами agent-skills, claude-skills и ai-skills. «Открыть» ищет в репозитории папки со SKILL.md.", "Repositories tagged agent-skills, claude-skills and ai-skills. “Explore” looks for SKILL.md folders inside.")}</span></p>
      <div class="sk-repos">${rows.map((r) => `<div class="sk-repo">
        <span class="sk-tile">${svg("github")}</span>
        <div class="sk-repo-text"><a href="${h(r.url)}" target="_blank" rel="noopener noreferrer" class="sk-card-name">${h(r.repo)}</a>
          <p>${h(r.description || "")}</p>
          <span class="sk-card-meta">${svg("star", "sm")} ${fmtNum(r.stars)}${r.license ? ` · ${h(r.license)}` : ""}${r.updated ? ` · ${h(String(r.updated).slice(0, 10))}` : ""}</span></div>
        <button type="button" class="sk-btn small" data-explore="${h(r.repo)}" ${state.exploring ? "disabled" : ""}>${state.exploring === r.repo ? `<span class="sk-spin"></span>` : svg("folder")}<span>${state.extra[r.repo] ? L("Открыт", "Opened") : L("Открыть", "Explore")}</span></button>
      </div>`).join("") || emptyState("search", L("Ничего не найдено", "Nothing found"), "")}</div>`;
  }

  async function openCatalogSkill(cid) {
    const s = allCatalogSkills().find((x) => x.id === cid);
    if (!s) return;
    state.cdetail = { summary: s, loading: true, data: null, error: "" };
    state.cfile = null;
    state.ctab = "doc";
    state.view = "cdetail";
    render();
    try {
      const data = await api(`/api/pg/skills-catalog/skill?repo=${encodeURIComponent(s.repo)}&path=${encodeURIComponent(s.path || "")}`);
      if (state.cdetail && state.cdetail.summary.id === cid) Object.assign(state.cdetail, { loading: false, data });
    } catch (error) {
      if (state.cdetail && state.cdetail.summary.id === cid) Object.assign(state.cdetail, { loading: false, error: catalogErrorText(error.payload && error.payload.state, error.message) });
    }
    if (root() && state.view === "cdetail") render();
  }

  const WARN_TEXT = {
    has_scripts: () => L("В папке скилла есть исполняемые скрипты. MemuAI никогда не запускает их сам; в чат попадают только инструкции.", "The skill folder contains executable scripts. MemuAI never runs them by itself; only the instructions go to the chat."),
    has_binaries: () => L("В папке есть двоичные файлы — они не копируются.", "The folder contains binary files — they are never copied."),
    hidden_chars: () => L("Из текста удалены невидимые символы (ими прячут инструкции от человека).", "Invisible characters were removed from the text (they can hide instructions from a reader)."),
    html_removed: () => L("Из текста удалён активный HTML.", "Active HTML was removed from the text."),
    pipe_to_shell: () => L("Инструкция предлагает скачать и сразу выполнить команду (curl … | sh). Прочитайте её внимательно.", "The instructions download and run a command (curl … | sh). Read them carefully."),
    destructive_command: () => L("В тексте есть потенциально разрушительные команды удаления.", "The text contains potentially destructive delete commands."),
    long_base64: () => L("В тексте длинный закодированный блок (base64).", "The text contains a long encoded (base64) block."),
    truncated: () => L("Инструкция очень длинная и обрезана до 200 000 символов.", "The instructions are very long and were cut to 200,000 characters."),
  };

  function catalogDetailView() {
    const cd = state.cdetail;
    if (!cd) { state.view = "list"; return listView(); }
    const s = cd.summary, d = cd.data;
    const mine = installedFromCatalog(s.repo, s.path);
    const update = mine && d && mine.source.sha && d.sha && mine.source.sha !== d.sha;
    const codeDone = (state.codeInstalled || []).some((r) => r.repo === s.repo && (r.path || "") === (s.path || ""));
    const actions = mine
      ? `${update ? `<button type="button" class="sk-btn primary" data-act="cd-update">${svg("refresh")}<span>${L("Обновить", "Update")}</span></button>` : `<span class="sk-added lg">${svg("check")}${L("В библиотеке", "In your library")}</span>`}
         <button type="button" class="sk-btn" data-act="cd-open-mine">${svg("eye")}<span>${L("Открыть", "Open")}</span></button>
         <button type="button" class="sk-btn danger" data-act="cd-remove">${svg("trash")}<span>${L("Удалить", "Remove")}</span></button>`
      : `<button type="button" class="sk-btn primary" data-act="cd-add" ${!d ? "disabled" : ""}>${svg("plus")}<span>${L("Добавить в библиотеку", "Add to library")}</span></button>`;
    const warnings = d ? (d.warnings || []).filter((w) => WARN_TEXT[w]) : [];
    const files = d ? d.files || [] : s.files || [];
    const tab = (id, label, n = "") => `<button type="button" role="tab" class="sk-seg-btn${state.ctab === id ? " on" : ""}" data-ctab="${id}" aria-selected="${state.ctab === id}">${label}${n !== "" ? `<span class="sk-count">${n}</span>` : ""}</button>`;
    let content;
    if (cd.loading) content = `<div class="sk-loading"><span class="sk-spin"></span>${L("Загружаю SKILL.md…", "Loading SKILL.md…")}</div>`;
    else if (cd.error) content = emptyState("warn", L("Не удалось открыть скилл", "Couldn't open the skill"), cd.error);
    else if (state.ctab === "files") content = filesHtml(files, s);
    else content = `<div class="sk-doc">${renderMarkdown(d.body)}</div>`;
    return `<div class="sk-sub">
      <div class="sk-subbar">
        <button type="button" class="sk-btn ghost" data-act="back">${svg("back")}<span>${L("Каталог", "Catalog")}</span></button>
        <div class="sk-subbar-actions">${actions}</div>
      </div>
      <div class="sk-scroll sk-detail" tabindex="-1">
        <section class="sk-hero">
          <span class="sk-tile lg">${svg("github")}</span>
          <div class="sk-hero-text">
            <h2>${h(prettyName((d && d.name) || s.name))}</h2>
            <p>${h((d && d.description) || s.description || "")}</p>
            <div class="sk-hero-meta">
              <a href="${h(s.url)}" target="_blank" rel="noopener noreferrer">${svg("github", "sm")}${h(s.repo)}${s.path ? `/${h(s.path)}` : ""}${svg("external", "sm")}</a>
              <span>${svg("star", "sm")} ${fmtNum(s.stars)}</span>
              ${(d && d.license) || s.license ? `<span>${h(((d && d.license) || s.license).slice(0, 60))}</span>` : ""}
              ${s.updated ? `<span>${L("обновлён", "updated")} ${h(String(s.updated).slice(0, 10))}</span>` : ""}
              ${s.author ? `<span>${L("автор", "by")} ${h(s.author)}</span>` : ""}
            </div>
          </div>
        </section>
        ${warnings.map((w) => `<div class="sk-callout ${w === "has_scripts" || w === "pipe_to_shell" || w === "destructive_command" ? "warn" : ""}">${svg("warn")}<div><span>${h(WARN_TEXT[w]())}</span></div></div>`).join("")}
        ${d && !mine ? `<label class="sk-check"><input type="checkbox" id="sk-cd-code" ${codeDone ? "checked disabled" : ""}><span>${codeDone ? L("Уже установлен для MemuCode", "Already installed for MemuCode") : L("Также установить для MemuCode (папка скилла: SKILL.md и справочные файлы, без скриптов)", "Also install for MemuCode (the skill folder: SKILL.md and reference files, no scripts)")}</span></label>` : ""}
        ${d ? `<div class="sk-seg small" role="tablist">${tab("doc", "SKILL.md")}${tab("files", L("Файлы", "Files"), files.length)}</div>` : ""}
        ${content}
      </div>
    </div>`;
  }

  function filesHtml(files, s) {
    if (!files.length) return emptyState("file", L("Только SKILL.md", "Only SKILL.md"), L("Других файлов в папке скилла нет.", "There are no other files in the skill folder."));
    const kindLabel = { script: L("скрипт", "script"), binary: L("двоичный", "binary"), doc: L("документ", "document"), other: L("файл", "file") };
    const f = state.cfile;
    return `<div class="sk-files">
      <ul class="sk-file-list">${files.map((file) => `<li><button type="button" class="${f && f.path === file.path ? "on" : ""}" data-cfile="${h(file.path)}" ${file.kind === "binary" ? "disabled" : ""}>
        ${svg(file.kind === "script" ? "terminal" : "file")}<span class="sk-file-name">${h(file.path)}</span>
        <span class="sk-badge${file.kind === "script" || file.kind === "binary" ? " warn" : ""}">${kindLabel[file.kind] || kindLabel.other}</span><small>${fmtSize(file.size)}</small></button></li>`).join("")}</ul>
      ${f ? `<div class="sk-file-view">${f.loading ? `<div class="sk-loading"><span class="sk-spin"></span></div>` : f.error ? `<p class="sk-muted">${h(f.error)}</p>`
        : `${f.kind === "script" ? `<div class="sk-callout warn">${svg("warn")}<div><span>${L("Скрипт показан только для чтения. MemuAI его не запускает.", "The script is shown read-only. MemuAI does not run it.")}</span></div></div>` : ""}
           ${/\.md$/i.test(f.path) ? `<div class="sk-doc">${renderMarkdown(f.text)}</div>` : `<pre class="sk-code"><code>${h(f.text)}</code></pre>`}${f.truncated ? `<p class="sk-muted small">${L("Показано начало файла.", "Showing the start of the file.")}</p>` : ""}`}</div>` : ""}
    </div>`;
  }
  const fmtSize = (n) => (n > 1024 * 1024 ? (n / 1048576).toFixed(1) + " MB" : n > 1024 ? Math.round(n / 1024) + " KB" : (n || 0) + " B");

  async function openCatalogFile(path) {
    const s = state.cdetail.summary;
    const full = (s.path ? s.path + "/" : "") + path;
    state.cfile = { path, loading: true };
    render();
    try {
      const data = await api(`/api/pg/skills-catalog/file?repo=${encodeURIComponent(s.repo)}&path=${encodeURIComponent(full)}`);
      state.cfile = { path, ...data, path };
    } catch (error) {
      state.cfile = { path, error: catalogErrorText(error.payload && error.payload.state, error.message) };
    }
    if (root() && state.view === "cdetail") render();
  }

  function librarySkillFromCatalog(detail, summary) {
    return {
      id: newId(), name: prettyName(String(detail.name || summary.name)).slice(0, 120), desc: String(detail.description || "").slice(0, 1024),
      when: String(detail.when || "").slice(0, 400), category: String(detail.category || "").slice(0, 60),
      prompt: String(detail.body || "").slice(0, 200000), custom: true, enabled: true,
      files: (detail.files || []).slice(0, 100).map((f) => ({ path: f.path, kind: f.kind, size: f.size })),
      source: { kind: "github", repo: summary.repo, path: summary.path || "", sha: detail.sha || summary.sha || "", branch: detail.branch || summary.branch || "",
        url: summary.url, license: detail.license || summary.license || "", author: summary.author || "", installed_at: Date.now() },
    };
  }

  async function addFromCatalog(cid, { quiet = false, code = false } = {}) {
    const s = allCatalogSkills().find((x) => x.id === cid) || (state.cdetail && state.cdetail.summary.id === cid ? state.cdetail.summary : null);
    if (!s) return;
    let detail = state.cdetail && state.cdetail.summary.id === cid ? state.cdetail.data : null;
    try {
      if (!detail) detail = await api(`/api/pg/skills-catalog/skill?repo=${encodeURIComponent(s.repo)}&path=${encodeURIComponent(s.path || "")}`);
    } catch (error) {
      toast(catalogErrorText(error.payload && error.payload.state, error.message), "err", 6000);
      return;
    }
    const list = skills();
    const existing = list.find((x) => x.source && x.source.kind === "github" && x.source.repo === s.repo && (x.source.path || "") === (s.path || ""));
    const fresh = librarySkillFromCatalog(detail, s);
    if (existing) Object.assign(existing, { ...fresh, id: existing.id, enabled: existing.enabled });
    else list.push(fresh);
    persist(list);
    if (code) {
      try {
        await api("/api/pg/skills-catalog/install-code", { method: "POST", body: { repo: s.repo, path: s.path || "" } });
        state.codeInstalled = null;
        loadCodeInstalled();
      } catch (error) {
        toast(L("MemuCode: ", "MemuCode: ") + (error.payload && error.payload.state === "exists" ? L("скилл с таким именем уже есть", "a skill with that name already exists") : error.message), "warn", 6000);
      }
    }
    if (!quiet) toast(existing ? L("Скилл обновлён", "Skill updated") : L(`«${fresh.name}» добавлен в «Мои»`, `“${fresh.name}” added to “Mine”`), "ok");
    state.keepScroll = true;
    render();
  }

  async function loadCodeInstalled() {
    try {
      const data = await api("/api/pg/skills/code-installed");
      state.codeInstalled = data.skills || [];
      if (root() && state.view === "cdetail") render();
    } catch (e) { state.codeInstalled = []; }
  }

  /* ------------------------------------------------------------------ import / export */
  function addImported(items, sourceKind, label) {
    const valid = (items || []).filter((x) => x && x.name && (x.prompt || x.body));
    if (!valid.length) { toast(L("В файле не нашлось скилла", "No skill found in the file"), "warn"); return 0; }
    const list = skills();
    const names = new Set(list.map((s) => String(s.name || "").toLowerCase()));
    const fresh = valid.filter((x) => !names.has(String(x.name).toLowerCase())).map((x) => ({
      id: newId(), name: String(x.name).slice(0, 120), desc: String(x.desc || x.description || "").slice(0, 1024),
      when: String(x.when || "").slice(0, 400), category: String(x.category || "").slice(0, 60),
      prompt: String(x.prompt || x.body).slice(0, 200000), custom: true, enabled: true,
      files: Array.isArray(x.files) ? x.files.slice(0, 100) : undefined,
      source: { kind: sourceKind, label: String(label || "").slice(0, 120), license: x.license || "", installed_at: Date.now() },
    }));
    if (!fresh.length) { toast(L("Такие скиллы уже есть в библиотеке", "These skills are already in the library"), "warn"); return 0; }
    persist(list.concat(fresh));
    const scripts = valid.some((x) => x.has_scripts);
    toast(L(`Добавлено скиллов: ${fresh.length}`, `Skills added: ${fresh.length}`) + (scripts ? L(" · скрипты из архива не добавлены", " · scripts from the archive were not added") : ""), "ok", 5000);
    state.tab = "mine";
    localStorage.setItem(TAB_KEY, "mine");
    state.view = "list";
    state.cat = "all";
    if (root()) render();
    return fresh.length;
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function importFile() {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.accept = ".md,.markdown,.txt,.json,.zip,text/markdown,application/zip,application/json";
    input.onchange = async () => {
      const collected = [];
      for (const file of [...(input.files || [])].slice(0, 30)) {
        try {
          if (file.size > 20 * 1024 * 1024) { toast(L(`${file.name}: слишком большой файл`, `${file.name}: the file is too large`), "warn"); continue; }
          if (/\.json$/i.test(file.name)) { collected.push(...skillsFromText(await file.text(), file.name.replace(/\.[^.]+$/, ""))); continue; }
          const body = /\.zip$/i.test(file.name) ? { name: file.name, data_base64: await fileToBase64(file) } : { name: file.name, text: await file.text() };
          const data = await api("/api/pg/skills/parse", { method: "POST", body });
          collected.push(...(data.skills || []).map((x) => ({ ...x, prompt: x.body, desc: x.description })));
        } catch (error) {
          // The window server is unreachable or the text is not a SKILL.md: fall back to the in-page parser for text files.
          if (!/\.zip$/i.test(file.name)) { try { collected.push(...skillsFromText(await file.text(), file.name.replace(/\.[^.]+$/, ""))); continue; } catch (e) {} }
          toast(`${file.name}: ${error.message}`, "err", 6000);
        }
      }
      if (collected.length) addImported(collected, "file", [...input.files].map((f) => f.name).join(", "));
    };
    input.click();
  }

  function urlView() {
    const r = state.urlResult;
    return `<div class="sk-sub">
      <div class="sk-subbar">
        <button type="button" class="sk-btn ghost" data-act="back">${svg("back")}<span>${L("Назад", "Back")}</span></button>
        <h2 class="sk-subtitle">${L("Импорт по ссылке", "Import from a link")}</h2><div class="sk-subbar-actions"></div>
      </div>
      <div class="sk-scroll sk-detail" tabindex="-1">
        <p class="sk-muted">${L("Ссылка на репозиторий или папку GitHub (все SKILL.md внутри), на файл SKILL.md или прямая ссылка на .md / .json.", "A GitHub repository or folder link (every SKILL.md inside), a SKILL.md file, or a direct link to a .md / .json file.")}</p>
        <div class="sk-url-row">
          <label class="sk-search wide">${svg("link")}<input id="sk-url" type="url" value="${h(state.urlValue || "")}" placeholder="https://github.com/anthropics/skills/tree/main/skills/pdf" spellcheck="false" autocomplete="off" aria-label="${L("Ссылка", "Link")}"></label>
          <button type="button" class="sk-btn primary" data-act="url-go" ${state.urlBusy ? "disabled" : ""}>${state.urlBusy ? `<span class="sk-spin"></span>` : svg("search")}<span>${L("Найти", "Find")}</span></button>
        </div>
        ${r && r.error ? `<div class="sk-callout warn">${svg("warn")}<div><span>${h(r.error)}</span></div></div>` : ""}
        ${r && r.skills ? (r.skills.length ? `<div class="sk-url-list">${r.skills.map((s, i) => `<label class="sk-url-item"><input type="checkbox" data-urlpick="${i}" ${installedFromCatalog(s.repo, s.path) ? "disabled" : "checked"}>
            <span><b>${h(prettyName(s.name))}</b><small>${h(s.repo)}${s.path ? "/" + h(s.path) : ""}${s.has_scripts ? ` · ${L("есть скрипты", "has scripts")}` : ""}${installedFromCatalog(s.repo, s.path) ? ` · ${L("уже добавлен", "already added")}` : ""}</small><em>${h(s.description || "")}</em></span></label>`).join("")}</div>
            <div class="sk-row-end"><button type="button" class="sk-btn primary" data-act="url-add">${svg("plus")}<span>${L("Добавить выбранные", "Add selected")}</span></button></div>`
          : emptyState("search", L("Скиллы не найдены", "No skills found"), L("По этой ссылке нет папок со SKILL.md.", "There are no SKILL.md folders at that link."))) : ""}
      </div>
    </div>`;
  }

  async function resolveUrl(shell) {
    const url = String(shell.querySelector("#sk-url")?.value || "").trim();
    state.urlValue = url;
    if (!/^https?:\/\//i.test(url)) { toast(L("Введите http(s)-ссылку", "Enter an http(s) link"), "warn"); return; }
    state.urlBusy = true;
    state.urlResult = null;
    render();
    const github = /^https?:\/\/(www\.)?(github\.com|raw\.githubusercontent\.com)\//i.test(url);
    try {
      if (github && !/\.json$/i.test(url)) {
        const data = await api("/api/pg/skills-catalog/resolve", { method: "POST", body: { url } });
        state.urlResult = { skills: data.skills || [], source: data.source };
      } else {
        const page = await api("/api/pg/tools", { method: "POST", body: { tool: "web_fetch", args: { url, limit: 200000, raw: true } } });
        if (page.error) throw new Error(page.error);
        const base = decodeURIComponent(url.split("/").pop() || "").replace(/\.[^.]+$/, "");
        state.urlBusy = false;
        addImported(skillsFromText(page.raw || page.text || "", base), "url", new URL(url).hostname);
        return;
      }
    } catch (error) {
      state.urlResult = { error: catalogErrorText(error.payload && error.payload.state, error.message) };
    }
    state.urlBusy = false;
    if (root()) render();
  }

  async function addUrlPicks(shell) {
    const picks = [...shell.querySelectorAll("[data-urlpick]:checked:not(:disabled)")].map((box) => state.urlResult.skills[+box.dataset.urlpick]);
    if (!picks.length) { toast(L("Ничего не выбрано", "Nothing selected"), "warn"); return; }
    let added = 0;
    for (const s of picks.slice(0, 40)) {
      try {
        const detail = await api(`/api/pg/skills-catalog/skill?repo=${encodeURIComponent(s.repo)}&path=${encodeURIComponent(s.path || "")}`);
        const list = skills();
        list.push(librarySkillFromCatalog(detail, s));
        persist(list);
        added++;
      } catch (error) { toast(`${s.name}: ${error.message}`, "err", 5000); }
    }
    if (added) {
      toast(L(`Добавлено скиллов: ${added}`, `Skills added: ${added}`), "ok");
      state.tab = "mine"; state.view = "list"; state.urlResult = null;
      localStorage.setItem(TAB_KEY, "mine");
      render();
    }
  }

  async function download(body, fallbackName) {
    try {
      const resp = await fetch("/api/pg/skills/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).error || `HTTP ${resp.status}`);
      const blob = await resp.blob();
      const name = (resp.headers.get("Content-Disposition") || "").match(/filename="([^"]+)"/)?.[1] || fallbackName;
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 4000);
      toast(L(`Сохранено: ${name}`, `Saved: ${name}`), "ok");
    } catch (error) {
      toast(error.message, "err", 6000);
    }
  }
  const exportShape = (s) => ({ name: ui(s, "name"), desc: ui(s, "desc"), when: ui(s, "when"), category: ui(s, "category"), prompt: s.prompt, license: s.source && s.source.license || "" });

  /* ------------------------------------------------------------------ actions */
  function setAll(value) {
    persist(skills().map((s) => ({ ...s, enabled: value })));
    state.keepScroll = true;
    render();
    toast(value ? L("Все скиллы включены", "All skills enabled") : L("Все скиллы выключены", "All skills disabled"));
  }

  function removeSkill(id) {
    const s = skills().find((x) => x.id === id);
    if (!s) return;
    const doIt = () => {
      if (!s.custom && s.removable) localStorage.setItem("memu_skills_removed", JSON.stringify([...new Set(removedSkillIds().concat([id]))]));
      persist(skills().filter((x) => x.id !== id));
      if (s.source && s.source.kind === "github") {
        const installed = (state.codeInstalled || []).find((r) => r.repo === s.source.repo && (r.path || "") === (s.source.path || ""));
        if (installed) api("/api/pg/skills-catalog/uninstall-code", { method: "POST", body: { slug: installed.slug } }).then(() => { state.codeInstalled = null; }).catch(() => {});
      }
      if (state.view === "detail") state.view = "list";
      render();
      toast(L(`«${ui(s, "name")}» удалён`, `“${ui(s, "name")}” deleted`), "ok", 6000, { label: L("Вернуть", "Undo"), run: () => {
        if (!s.custom && s.removable) localStorage.setItem("memu_skills_removed", JSON.stringify(removedSkillIds().filter((x) => x !== id)));
        else persist(skills().concat([s]));
        if (root()) render();
      } });
    };
    doIt();
  }

  // app.js toast(text, type, action | durationMs): an action is {label, run}.
  const rawToast = window.toast;
  function toast(text, type = "ok", ms, action) {
    if (typeof rawToast !== "function") return;
    rawToast(text, type, action && action.label ? action : (ms || null));
  }

  function goBack() {
    if (state.view === "editor") { state.view = state.draft && state.draft.id ? "detail" : "list"; state.draft = null; }
    else if (state.view === "cdetail" || state.view === "url") { state.view = "list"; state.cdetail = null; state.cfile = null; }
    else state.view = "list";
    state.keepScroll = true;
    render();
    const focus = state.view === "list" && state.id ? root()?.querySelector(`[data-open="${CSS.escape(state.id)}"]`) : null;
    (focus || root()?.querySelector("#sk-q"))?.focus({ preventScroll: true });
  }

  function onAction(act, el, shell) {
    if (act !== "menu") state.menu = false;
    switch (act) {
      case "menu": state.menu = !state.menu; render(); if (state.menu) root()?.querySelector(".sk-menu button")?.focus(); return;
      case "new": startEdit(""); return;
      case "edit": startEdit(state.id); return;
      case "duplicate": startEdit(state.id, true); return;
      case "save": saveDraft(shell); return;
      case "cancel-edit": case "back": goBack(); return;
      case "delete": removeSkill(state.id); return;
      case "reset": {
        const list = skills().filter((x) => x.id !== state.id);
        persist(list);  // storedSkills() rebuilds the built-in from the catalogue (keeps its on/off state default)
        render();
        toast(L("Оригинал восстановлен", "Original restored"), "ok");
        return;
      }
      case "export-one": { const s = skills().find((x) => x.id === state.id); if (s) download({ skills: [exportShape(s)], format: "md" }, "SKILL.md"); return; }
      case "export-all": {
        const mine = skills().filter((s) => s.custom || s.customized);
        if (!mine.length) { toast(L("Своих или изменённых скиллов пока нет", "No custom or edited skills yet"), "warn"); render(); return; }
        download({ skills: mine.map(exportShape), format: "zip" }, "memuai-skills.zip");
        render();
        return;
      }
      case "import-file": render(); importFile(); return;
      case "import-url": state.view = "url"; state.urlResult = null; render(); setTimeout(() => root()?.querySelector("#sk-url")?.focus(), 30); return;
      case "url-go": resolveUrl(shell); return;
      case "url-add": addUrlPicks(shell); return;
      case "enable-all": setAll(true); return;
      case "disable-all": setAll(false); return;
      case "restore": localStorage.removeItem("memu_skills_removed"); scheduleBackup(); render(); toast(L("Встроенные скиллы возвращены", "Built-in skills restored"), "ok"); return;
      case "code-skills":
        if (window.MemuCode && typeof MemuCode.openSkills === "function") { closePanel(); MemuCode.openSkills(); }
        else { render(); toast(L("Откройте MemuCode, чтобы настроить его скиллы", "Open MemuCode to manage its skills"), "warn"); }
        return;
      case "clear-filters": state.q = ""; state.cat = "all"; state.status = "all"; render(); return;
      case "refresh-catalog": loadCatalog(true); return;
      case "update-from-catalog": {
        const s = skills().find((x) => x.id === state.id);
        const c = s && catalogSkillFor(s.source.repo, s.source.path);
        if (c) addFromCatalog(c.id).then(() => { state.view = "detail"; render(); });
        return;
      }
      case "cd-add": { const code = !!shell.querySelector("#sk-cd-code:checked:not(:disabled)"); addFromCatalog(state.cdetail.summary.id, { code }); return; }
      case "cd-update": addFromCatalog(state.cdetail.summary.id); return;
      case "cd-remove": { const mine = installedFromCatalog(state.cdetail.summary.repo, state.cdetail.summary.path); if (mine) { removeSkill(mine.id); } return; }
      case "cd-open-mine": { const mine = installedFromCatalog(state.cdetail.summary.repo, state.cdetail.summary.path); if (mine) { state.tab = "mine"; state.id = mine.id; state.view = "detail"; render(); } return; }
      default:
    }
  }

  function bind(shell) {
    shell.querySelectorAll("[data-act]").forEach((el) => el.addEventListener("click", (event) => { event.preventDefault(); onAction(el.dataset.act, el, shell); }));
    shell.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => {
      state.tab = el.dataset.tab; state.cat = "all"; state.view = "list"; state.menu = false;
      localStorage.setItem(TAB_KEY, state.tab);
      render();
      root()?.querySelector(`.sk-seg-btn[data-tab="${state.tab}"]`)?.focus();
    }));
    const seg = shell.querySelector(".sk-head .sk-seg");
    seg?.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const tabs = ["mine", "builtin", "catalog"];
      let i = tabs.indexOf(state.tab);
      i = event.key === "Home" ? 0 : event.key === "End" ? 2 : (i + (event.key === "ArrowRight" ? 1 : 2)) % 3;
      event.preventDefault();
      shell.querySelector(`.sk-seg-btn[data-tab="${tabs[i]}"]`)?.click();
    });
    const q = shell.querySelector("#sk-q");
    if (q) {
      q.addEventListener("input", () => {
        state.q = q.value;
        const pos = q.selectionStart;
        const main = shell.querySelector(".sk-main");
        if (main) {
          const tmp = document.createElement("div");
          tmp.innerHTML = withSkills(() => (state.tab === "catalog" ? catalogMain() : libraryMain()));
          main.replaceWith(tmp.firstElementChild);
          bindMain(shell);
          bindToggles(shell);
        }
        q.focus(); try { q.setSelectionRange(pos, pos); } catch (e) {}
      });
      q.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && q.value) { event.preventDefault(); event.stopPropagation(); q.value = ""; q.dispatchEvent(new Event("input")); }
        if (event.key === "ArrowDown") { const first = shell.querySelector(".sk-card-main"); if (first) { event.preventDefault(); first.focus(); } }
      });
    }
    shell.querySelectorAll("[data-cat]").forEach((el) => el.addEventListener("click", () => { state.cat = el.dataset.cat; render(); }));
    shell.querySelectorAll("[data-csource]").forEach((el) => el.addEventListener("click", () => { state.csource = el.dataset.csource; render(); }));
    bindMain(shell);
    // Editor
    if (state.view === "editor") {
      ["#sk-f-name", "#sk-f-cat", "#sk-f-desc", "#sk-f-when", "#sk-f-prompt"].forEach((sel) => shell.querySelector(sel)?.addEventListener("input", () => {
        shell.querySelector("#sk-f-error").hidden = true;
        clearTimeout(shell.__pv); shell.__pv = setTimeout(() => paintPreview(shell), 120);
      }));
      shell.querySelectorAll("[data-etab]").forEach((el) => el.addEventListener("click", () => {
        readDraft(shell); state.editTab = el.dataset.etab;
        shell.querySelector(".sk-editor").dataset.etab = state.editTab;
        shell.querySelectorAll("[data-etab]").forEach((b) => { b.classList.toggle("on", b === el); b.setAttribute("aria-selected", String(b === el)); });
        paintPreview(shell);
      }));
      const ta = shell.querySelector("#sk-f-prompt");
      ta?.addEventListener("keydown", (event) => {
        if (event.key === "Tab" && !event.shiftKey && !event.ctrlKey && !event.altKey) {
          event.preventDefault();
          const { selectionStart: a, selectionEnd: b, value } = ta;
          ta.value = value.slice(0, a) + "  " + value.slice(b);
          ta.selectionStart = ta.selectionEnd = a + 2;
          ta.dispatchEvent(new Event("input"));
        }
      });
      paintPreview(shell);
    }
    if (state.view === "cdetail") {
      shell.querySelectorAll("[data-ctab]").forEach((el) => el.addEventListener("click", () => { state.ctab = el.dataset.ctab; render(); }));
      shell.querySelectorAll("[data-cfile]").forEach((el) => el.addEventListener("click", () => openCatalogFile(el.dataset.cfile)));
      if (state.codeInstalled === null) loadCodeInstalled();
    }
    if (state.view === "url") shell.querySelector("#sk-url")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); resolveUrl(shell); } });
    bindToggles(shell);
  }

  function bindToggles(shell) {
    shell.querySelectorAll("[data-toggle]").forEach((box) => !box.__skBound && (box.__skBound = true) && box.addEventListener("change", () => {
      const item = update(box.dataset.toggle, { enabled: box.checked });
      shell.querySelectorAll(`[data-toggle="${CSS.escape(box.dataset.toggle)}"]`).forEach((other) => { other.checked = box.checked; });
      box.closest(".sk-card")?.classList.toggle("off", !box.checked);
      const summary = shell.querySelector(".sk-summary");
      if (summary && state.view === "list" && state.tab !== "catalog") {
        const pool = skills().filter((s) => (state.tab === "mine" ? isMine(s) : !isMine(s)));
        summary.textContent = L(`Включено ${pool.filter((s) => s.enabled).length} из ${pool.length}`, `${pool.filter((s) => s.enabled).length} of ${pool.length} enabled`);
      }
      if (item) toast(box.checked ? L(`«${ui(item, "name")}» включён`, `“${ui(item, "name")}” enabled`) : L(`«${ui(item, "name")}» выключен`, `“${ui(item, "name")}” disabled`));
    }));
  }

  function bindMain(shell) {
    shell.querySelectorAll("[data-open]").forEach((el) => el.addEventListener("click", () => { state.id = el.dataset.open; state.view = "detail"; render(); root()?.querySelector(".sk-detail")?.focus({ preventScroll: true }); }));
    shell.querySelectorAll("[data-copen]").forEach((el) => el.addEventListener("click", () => openCatalogSkill(el.dataset.copen)));
    shell.querySelectorAll("[data-cadd]").forEach((el) => el.addEventListener("click", (event) => { event.stopPropagation(); el.disabled = true; addFromCatalog(el.dataset.cadd); }));
    shell.querySelectorAll("[data-cupdate]").forEach((el) => el.addEventListener("click", (event) => { event.stopPropagation(); el.disabled = true; addFromCatalog(el.dataset.cupdate); }));
    shell.querySelectorAll("[data-explore]").forEach((el) => el.addEventListener("click", () => exploreRepo(el.dataset.explore)));
    shell.querySelectorAll("[data-status]").forEach((el) => el.addEventListener("click", () => { state.status = el.dataset.status; state.keepScroll = false; render(); }));
    shell.querySelectorAll("[data-act='clear-filters'], [data-act='refresh-catalog'], [data-act='new'], [data-act='import-file'], [data-act='import-url']").forEach((el) => {
      if (el.closest(".sk-main") && !el.__skBound) { el.__skBound = true; el.addEventListener("click", (event) => { event.preventDefault(); onAction(el.dataset.act, el, shell); }); }
    });
    shell.querySelectorAll(".sk-main [data-tab]").forEach((el) => { if (!el.__skBound) { el.__skBound = true; el.addEventListener("click", () => { state.tab = el.dataset.tab; state.view = "list"; localStorage.setItem(TAB_KEY, state.tab); render(); }); } });
    const sortSel = shell.querySelector("#sk-csort");
    sortSel?.addEventListener("change", () => { state.csort = sortSel.value; render(); });
    // Arrow keys move between cards of the grid (the virtual catalog grid has its own handler).
    shell.querySelector(".sk-grid:not(.sk-vrow)")?.addEventListener("keydown", (event) => {
      if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(event.key) || !event.target.classList.contains("sk-card-main")) return;
      const cards = [...shell.querySelectorAll(".sk-grid .sk-card-main")];
      const i = cards.indexOf(event.target);
      const grid = shell.querySelector(".sk-grid");
      const cols = Math.max(1, getComputedStyle(grid).gridTemplateColumns.split(" ").length);
      const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[event.key];
      const next = cards[i + step];
      if (next) { event.preventDefault(); next.focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); shell.querySelector("#sk-q")?.focus(); }
    });
    mountVirtual(shell);
  }

  // Keyboard: Esc steps back from a sub-view (before the dialog's own Esc closes everything), "/" focuses search,
  // Ctrl+S saves in the editor, Ctrl+N starts a new skill.
  window.addEventListener("keydown", (event) => {
    const host = root();
    if (!host || !document.getElementById("panel-back")?.classList.contains("visible")) return;
    const key = event.key;
    if (key === "Escape") {
      if (state.menu) { event.preventDefault(); event.stopImmediatePropagation(); state.menu = false; render(); host.querySelector('[data-act="menu"]')?.focus(); return; }
      if (state.view !== "list" && !document.getElementById("image-viewer")?.classList.contains("visible")) {
        event.preventDefault(); event.stopImmediatePropagation(); goBack(); return;
      }
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.code === "KeyS" || key === "s" || key === "S") && state.view === "editor") {
      event.preventDefault(); event.stopImmediatePropagation(); saveDraft(host.querySelector(".sk-shell")); return;
    }
    const typing = event.target.closest && event.target.closest("input, textarea, select, [contenteditable]");
    if ((window.MemuKeys ? MemuKeys.latin(event) : event.key) === "/" && !typing && state.view === "list") { event.preventDefault(); event.stopImmediatePropagation(); host.querySelector("#sk-q")?.focus(); }
  }, true);
  document.addEventListener("click", (event) => {
    if (!state.menu) return;
    const host = root();
    if (host && !event.target.closest(".sk-menu-wrap")) { state.menu = false; render(); }
  }, true);

  /* ------------------------------------------------------------------ public */
  function showSkills(opts) { open(opts && typeof opts === "object" && !(opts instanceof Event) ? opts : {}); }
  window.MemuSkillsUI = { open: showSkills, renderMarkdown, iconFor, state, loadCatalog, addImported };
  window.showSkillsModal = function showSkillsModal(opts) { showSkills(opts); };
})();
