/* MemuAI — Round 29: the new chat export (PDF, Word, HTML, Markdown, JSON, text).
 *
 * MemuExport.open(chat) shows one dialog: format cards, which messages (all / a range / hand-picked),
 * what to include (reasoning, tool steps, images, formulas, code colours, metadata, timestamps, token
 * stats, a table of contents), the look (theme: light / dark / sepia / print / the app's current design,
 * fonts, size, page A4/Letter, orientation, margins, header and footer) and a live preview.
 *
 * Every visual format is built from the window's own renderer — md() with KaTeX, syntax highlighting,
 * tables, visualizations and diagrams — into one self-contained HTML document:
 *   HTML  → saved as is (KaTeX CSS and fonts, images embedded; visualizations stay interactive);
 *   PDF   → printed by a headless Edge on the server (app/chat_export_pdf.py), fallback: print dialog;
 *   DOCX  → converted on the server (app/chat_export.py): real tables, lists, Office Math formulas,
 *           coloured code, images; visualizations are snapshotted by Edge when available.
 * Files go to Downloads/MemuAI (or the native Save dialog) through /api/export/render.
 *
 * Uses app.js globals: S, PG_LANG, $, esc, api, toast, icon, panel, closePanel, md, mdUser, answerHtml,
 * visibleAnswer, hidePrivateProtocols, modelDisplayName, pref, savePreferences, copyText, revealDownload,
 * imageRenderUrl, formatMessageTime.
 */
"use strict";
(() => {
  if (window.MemuExport) return;
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  /* ------------------------------------------------------------------ catalogues */
  const FORMATS = [
    { id: "pdf", label: "PDF", ext: "pdf", visual: true, desc: () => t("Для чтения и печати", "For reading and printing") },
    { id: "docx", label: "Word", ext: "docx", visual: true, desc: () => t("Редактируемый документ", "An editable document") },
    { id: "html", label: "HTML", ext: "html", visual: true, desc: () => t("Веб-страница, всё внутри", "A web page, all embedded") },
    { id: "md", label: "Markdown", ext: "md", visual: false, desc: () => t("Текст с разметкой", "Text with markup") },
    { id: "json", label: "JSON", ext: "json", visual: false, desc: () => t("Данные сообщений", "Message data") },
    { id: "txt", label: t("Текст", "Text"), ext: "txt", visual: false, desc: () => t("Только текст", "Plain text") },
  ];
  const TOKENS_LIGHT = { kw: "#cf222e", type: "#953800", str: "#0a3069", com: "#6e7781", num: "#0550ae", fn: "#8250df", op: "#cf222e",
    lit: "#0550ae", tag: "#116329", attr: "#0550ae", prop: "#0550ae", var: "#953800", meta: "#6e7781", punct: "#57606a" };
  const TOKENS_DARK = { kw: "#ff7b72", type: "#ffa657", str: "#a5d6ff", com: "#8b949e", num: "#79c0ff", fn: "#d2a8ff", op: "#ff7b72",
    lit: "#79c0ff", tag: "#7ee787", attr: "#79c0ff", prop: "#79c0ff", var: "#ffa657", meta: "#8b949e", punct: "#c9d1d9" };
  const TOKENS_SEPIA = { kw: "#a0412d", type: "#8a5a19", str: "#4f6b2d", com: "#8c7a63", num: "#2f5f8a", fn: "#6d3f8f", op: "#a0412d",
    lit: "#2f5f8a", tag: "#4f6b2d", attr: "#2f5f8a", prop: "#2f5f8a", var: "#8a5a19", meta: "#8c7a63", punct: "#5b4a36" };
  const TOKENS_PRINT = { kw: "#000000", type: "#333333", str: "#333333", com: "#666666", num: "#000000", fn: "#000000", op: "#000000",
    lit: "#000000", tag: "#000000", attr: "#333333", prop: "#333333", var: "#000000", meta: "#666666", punct: "#333333" };
  const THEMES = {
    light: { label: () => t("Светлая", "Light"), bg: "#ffffff", panel: "#f6f8fa", text: "#1f2328", muted: "#656d76", accent: "#b4532f",
      border: "#d0d7de", link: "#0969da", code_bg: "#f6f8fa", code_text: "#1f2328", quote_bg: "#f6f8fa", user_bg: "#f6f2ec",
      user_border: "#b4532f", assistant_border: "#57606a", table_head_bg: "#eef1f4", step_bg: "#f6f8fa", tokens: TOKENS_LIGHT },
    dark: { label: () => t("Тёмная", "Dark"), bg: "#16181c", panel: "#1e2127", text: "#e6e8eb", muted: "#9aa3ad", accent: "#efa58b",
      border: "#343a43", link: "#7fb4ff", code_bg: "#0f1115", code_text: "#e6e8eb", quote_bg: "#1e2127", user_bg: "#23262d",
      user_border: "#efa58b", assistant_border: "#9aa3ad", table_head_bg: "#23272e", step_bg: "#1b1e23", tokens: TOKENS_DARK },
    sepia: { label: () => t("Сепия", "Sepia"), bg: "#f6efe0", panel: "#efe5d0", text: "#3b2f22", muted: "#7a6a55", accent: "#9a4b2a",
      border: "#d9cbb0", link: "#7a3f1f", code_bg: "#efe6d2", code_text: "#3b2f22", quote_bg: "#efe6d2", user_bg: "#efe3cc",
      user_border: "#9a4b2a", assistant_border: "#7a6a55", table_head_bg: "#e8dcc3", step_bg: "#f0e7d4", tokens: TOKENS_SEPIA },
    print: { label: () => t("Для печати", "Print"), bg: "#ffffff", panel: "#ffffff", text: "#000000", muted: "#4a4a4a", accent: "#000000",
      border: "#9a9a9a", link: "#000000", code_bg: "#f3f3f3", code_text: "#000000", quote_bg: "#ffffff", user_bg: "#ffffff",
      user_border: "#000000", assistant_border: "#4a4a4a", table_head_bg: "#e9e9e9", step_bg: "#f6f6f6", tokens: TOKENS_PRINT },
    app: { label: () => t("Как в MemuAI", "MemuAI design") },
  };
  const FONTS = [
    { id: "segoe", label: "Segoe UI", css: '"Segoe UI", "Segoe UI Variable Text", system-ui, -apple-system, Roboto, Arial, sans-serif', docx: "Segoe UI" },
    { id: "calibri", label: "Calibri", css: 'Calibri, Carlito, "Segoe UI", Arial, sans-serif', docx: "Calibri" },
    { id: "arial", label: "Arial", css: "Arial, Helvetica, sans-serif", docx: "Arial" },
    { id: "verdana", label: "Verdana", css: "Verdana, Geneva, sans-serif", docx: "Verdana" },
    { id: "georgia", label: "Georgia", css: "Georgia, Cambria, serif", docx: "Georgia" },
    { id: "cambria", label: "Cambria", css: "Cambria, Georgia, serif", docx: "Cambria" },
    { id: "times", label: "Times New Roman", css: '"Times New Roman", Times, serif', docx: "Times New Roman" },
    { id: "palatino", label: "Palatino", css: '"Palatino Linotype", Palatino, "Book Antiqua", serif', docx: "Palatino Linotype" },
    { id: "app", label: () => t("Шрифт MemuAI", "MemuAI font"), css: "", docx: "" },
  ];
  const MONOS = [
    { id: "consolas", label: "Consolas", css: 'Consolas, "Cascadia Mono", Menlo, monospace', docx: "Consolas" },
    { id: "cascadia", label: "Cascadia Code", css: '"Cascadia Code", "Cascadia Mono", Consolas, monospace', docx: "Cascadia Code" },
    { id: "courier", label: "Courier New", css: '"Courier New", Courier, monospace', docx: "Courier New" },
  ];
  const MARGINS = { narrow: 12.7, normal: 20, wide: 30 };
  const PAGES = { a4: { w: 210, h: 297, label: "A4" }, letter: { w: 215.9, h: 279.4, label: "Letter" } };
  const DEFAULTS = {
    format: "pdf", theme: "light", font: "segoe", mono: "consolas", size: 11, page: "a4", orientation: "portrait",
    margins: "normal", images: true, math: true, code: true, reasoning: false, tools: false, meta: true, timestamps: true,
    stats: false, toc: false, header: "", footer: "", numbers: true, range: "all", save: "downloads",
  };

  /* ------------------------------------------------------------------ state */
  let st = null;     // { chat, rows, opts, selected:Set, from, to, previewTimer, caps, busy }

  function loadOptions() {
    const saved = (typeof pref === "function" && pref("chat_export_options", {})) || {};
    const opts = { ...DEFAULTS, ...saved };
    if (!FORMATS.some((f) => f.id === opts.format)) opts.format = "pdf";
    if (!THEMES[opts.theme]) opts.theme = "light";
    opts.range = "all"; opts.header = ""; opts.footer = "";
    return opts;
  }
  function saveOptions() {
    const { header, footer, range, ...keep } = st.opts;
    try { savePreferences({ chat_export_options: keep }); } catch (e) {}
  }

  function sourceRows(chat) {
    return (chat?.messages || []).map((m, index) => ({ m, index }))
      .filter(({ m }) => !m.internal && (m.role === "user" || m.role === "assistant"));
  }
  // Round 35 (branches): the export holds the ACTIVE branch (chat.messages); say so when other versions exist.
  function otherVersions() {
    try { return typeof MemuBranches !== "undefined" && st?.chat ? MemuBranches.stats({ messages: st.chat.messages || [], branches: st.chat.branches }).versions : 0; } catch (e) { return 0; }
  }
  function chosenRows() {
    const rows = st.rows;
    if (st.opts.range === "from") {
      const from = Math.max(1, Math.min(st.from, rows.length)), to = Math.max(from, Math.min(st.to, rows.length));
      return rows.slice(from - 1, to);
    }
    if (st.opts.range === "selected") return rows.filter((row) => st.selected.has(row.index));
    return rows;
  }
  function preview(text, n = 70) {
    return String(text || "").replace(/```[\s\S]*?```/g, " [code] ").replace(/[#*_`>|$\\]/g, "").replace(/\s+/g, " ").trim().slice(0, n)
      || t("(вложения)", "(attachments)");
  }

  /* ------------------------------------------------------------------ palette & CSS */
  function appPalette() {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fb) => (cs.getPropertyValue(name) || "").trim() || fb;
    const dark = document.documentElement.dataset.theme !== "light";
    const base = dark ? THEMES.dark : THEMES.light;
    const tok = {};
    Object.keys(base.tokens).forEach((key) => { tok[key] = v(`--tok-${key}`, base.tokens[key]); });
    return { ...base, bg: v("--bg", base.bg), panel: v("--panel", base.panel), text: v("--text", base.text), muted: v("--muted", base.muted),
      accent: v("--accent", base.accent), border: v("--border", base.border), link: v("--accent", base.link),
      code_bg: v("--code-bg", base.code_bg), code_text: v("--code-text", base.code_text), quote_bg: v("--panel-2", base.quote_bg),
      user_bg: v("--user-bubble", base.user_bg), user_border: v("--accent", base.user_border), assistant_border: v("--muted", base.assistant_border),
      table_head_bg: v("--panel-2", base.table_head_bg), step_bg: v("--panel-2", base.step_bg), tokens: tok };
  }
  function palette() { return st.opts.theme === "app" ? appPalette() : THEMES[st.opts.theme] || THEMES.light; }
  function fontOf() {
    const f = FONTS.find((x) => x.id === st.opts.font) || FONTS[0];
    if (f.id === "app") {
      const css = (getComputedStyle(document.documentElement).getPropertyValue("--sans") || "").trim() || FONTS[0].css;
      const first = css.split(",")[0].replace(/["']/g, "").trim();
      return { css, docx: /system-ui|-apple/.test(first) || !first ? "Segoe UI" : first };
    }
    return f;
  }
  const monoOf = () => MONOS.find((x) => x.id === st.opts.mono) || MONOS[0];

  /* A colour for the DOCX writer: «#rgb», «#rrggbb» or «rgb(…)» → «rrggbb». */
  function hex(value) {
    const s = String(value || "").trim();
    let m = /^#([0-9a-f]{6})$/i.exec(s); if (m) return m[1];
    m = /^#([0-9a-f]{3})$/i.exec(s); if (m) return m[1].split("").map((c) => c + c).join("");
    m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(s); if (m) return [m[1], m[2], m[3]].map((n) => (+n).toString(16).padStart(2, "0")).join("");
    const probe = document.createElement("i"); probe.style.color = s; document.body.appendChild(probe);
    const rgb = getComputedStyle(probe).color; probe.remove();
    m = /rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(rgb);
    return m ? [m[1], m[2], m[3]].map((n) => (+n).toString(16).padStart(2, "0")).join("") : "000000";
  }
  function docxPalette(p) {
    const out = {}; Object.keys(p).forEach((key) => { if (key !== "tokens" && key !== "label") out[key] = hex(p[key]); });
    out.tokens = {}; Object.keys(p.tokens || {}).forEach((key) => { out.tokens[key] = hex(p.tokens[key]); });
    return out;
  }

  function documentCss(p, target) {
    const o = st.opts;
    const font = fontOf(), mono = monoOf();
    const page = PAGES[o.page] || PAGES.a4;
    const landscape = o.orientation === "landscape";
    const margin = MARGINS[o.margins] || 20;
    const tok = Object.entries(p.tokens || {}).map(([k, v]) => `.tok-${k}{color:${v}}`).join("");
    const paperW = landscape ? page.h : page.w;
    const preview = target === "preview";
    const paged = preview && (o.format === "pdf" || o.format === "docx");
    return `
:root{color-scheme:${st.opts.theme === "dark" || (st.opts.theme === "app" && document.documentElement.dataset.theme !== "light") ? "dark" : "light"}}
@page{size:${o.page === "letter" ? "letter" : "A4"} ${landscape ? "landscape" : "portrait"};background:${p.bg};${target === "print" ? `margin:${margin}mm;` : ""}}
*{box-sizing:border-box}
html{background:${paged ? "#8a8f98" : p.bg};-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;color:${p.text};font-family:${font.css};font-size:${o.size}pt;line-height:1.55;word-wrap:break-word;overflow-wrap:anywhere}
.mx-doc{${paged ? `width:${paperW}mm;min-height:${landscape ? page.w : page.h}mm;margin:18px auto;padding:${margin}mm;background:${p.bg};box-shadow:0 6px 30px rgba(0,0,0,.35);` : target === "html" ? "max-width:860px;margin:0 auto;padding:40px 28px 60px;" : ""}}
a{color:${p.link};text-decoration:${st.opts.theme === "print" ? "underline" : "none"}}
a:hover{text-decoration:underline}
h1,h2,h3,h4,h5,h6{line-height:1.25;margin:1.1em 0 .45em;break-after:avoid;page-break-after:avoid}
h1{font-size:1.75em}h2{font-size:1.4em}h3{font-size:1.2em}h4{font-size:1.05em}
p{margin:.45em 0 .7em}
ul,ol{margin:.4em 0 .8em;padding-left:1.6em}li{margin:.2em 0}li::marker{color:${p.accent}}
.md-task{list-style:none;margin-left:-1.3em}.md-check{color:${p.accent}}
blockquote{margin:.8em 0;padding:.5em 1em;border-left:3px solid ${p.accent};background:${p.quote_bg};color:${p.muted};border-radius:0 6px 6px 0}
hr{border:0;border-top:1px solid ${p.border};margin:1.4em 0}
code{font-family:${mono.css};font-size:.88em;background:${p.code_bg};color:${p.code_text};padding:.1em .35em;border-radius:4px;border:1px solid ${p.border}}
pre{margin:0;white-space:pre-wrap;word-break:break-word}
pre code{display:block;padding:.85em 1em;border:0;background:none;font-size:.86em;line-height:1.5;border-radius:0}
.code-block-wrap{margin:.7em 0 1em;border:1px solid ${p.border};border-radius:8px;background:${p.code_bg};overflow:hidden;break-inside:auto}
.code-header{display:flex;justify-content:space-between;padding:.35em 1em;font:600 .72em ${font.css};letter-spacing:.04em;text-transform:uppercase;color:${p.muted};border-bottom:1px solid ${p.border}}
.code-header button,.code-copy-btn{display:none!important}
${o.code ? tok : ""}.tok-com{font-style:italic}.tok-strong{font-weight:700}
.table-wrap{margin:.8em 0 1.1em;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.94em;break-inside:auto}
thead{display:table-header-group}tr{break-inside:avoid;page-break-inside:avoid}
th,td{border:1px solid ${p.border};padding:.45em .7em;text-align:left;vertical-align:top}
th{background:${p.table_head_bg};font-weight:600}
img{max-width:100%;height:auto;border-radius:6px}
.msg-images,.mx-images{display:flex;flex-wrap:wrap;gap:8px;margin:.5em 0}
.mx-images img{max-height:420px}
.katex{font-size:1.08em}.katex-display{margin:.8em 0;overflow-x:auto;overflow-y:hidden}
.memuai-visual iframe,.mx-visual{width:100%;border:1px solid ${p.border};border-radius:8px;background:transparent;display:block}
.mx-cover{margin-bottom:1.6em;padding-bottom:1em;border-bottom:2px solid ${p.accent}}
.mx-brand{font:700 .72em ${font.css};letter-spacing:.14em;text-transform:uppercase;color:${p.accent}}
.mx-title{font-size:2em;margin:.25em 0 .35em}
.mx-meta{display:flex;flex-wrap:wrap;gap:.25em 1.6em;color:${p.muted};font-size:.86em}.mx-meta p{margin:0}.mx-meta b{color:${p.text};font-weight:600}
.mx-toc{margin:0 0 1.6em;padding:.8em 1.1em;border:1px solid ${p.border};border-radius:8px;background:${p.panel}}
.mx-toc h2{font-size:1em;margin:0 0 .4em}.mx-toc ol{margin:0;padding-left:1.4em;font-size:.92em}.mx-toc a{color:${p.text}}
.mx-msg{margin:0 0 1.1em;break-inside:auto}
.mx-who{display:flex;align-items:baseline;gap:.6em;font-weight:700;font-size:.92em;margin:1.3em 0 .45em;padding-bottom:.25em;border-bottom:1px solid ${p.border};break-after:avoid;page-break-after:avoid}
.mx-who[data-role=user]{color:${p.user_border}}.mx-who[data-role=assistant]{color:${p.assistant_border}}
.mx-who small{font-weight:400;color:${p.muted};font-size:.88em}
.mx-user .mx-content{background:${p.user_bg};border-radius:10px;padding:.55em 1em;${st.opts.theme === "print" ? `border:1px solid ${p.border};` : ""}}
.mx-user .mx-content p:first-child{margin-top:.2em}.mx-user .mx-content p:last-child{margin-bottom:.2em}
.mx-attach{display:inline-block;margin:.15em .4em .15em 0;padding:.15em .6em;border:1px solid ${p.border};border-radius:999px;font-size:.84em;color:${p.muted}}
.mx-step{margin:.4em 0 .8em;padding:.55em .9em;border-left:3px solid ${p.border};background:${p.step_bg};border-radius:0 8px 8px 0;color:${p.muted};font-size:.9em}
.mx-step-title{font-weight:700;color:${p.text};margin-bottom:.25em;font-size:.92em}
.mx-step-body{white-space:pre-wrap}.mx-step-body p{margin:.2em 0}
.mx-tool{margin:.15em 0}.mx-tool code{font-size:.85em}
.mx-stats{color:${p.muted};font-size:.78em;margin-top:.3em}
.mx-note{color:${p.muted};font-style:italic;font-size:.9em}
.mx-end{margin-top:2.4em;padding-top:.8em;border-top:1px solid ${p.border};color:${p.muted};font-size:.8em;text-align:center}
.mention-chip{color:${p.accent}}
.visual-loading,.msg-tools,.msg-tool,button{display:none!important}
@media print{.mx-doc{max-width:none;padding:0;margin:0;box-shadow:none}a{color:${p.link}}}
`;
  }

  /* ------------------------------------------------------------------ building the document */
  function whenText(ms) {
    if (!ms) return "";
    try { return new Date(ms).toLocaleString(en() ? "en-US" : "ru-RU", { dateStyle: "medium", timeStyle: "short" }); } catch (e) { return ""; }
  }
  function tokensOf(text) {
    if (window.MemuContext?.estimateText) { try { return MemuContext.estimateText(String(text || "")); } catch (e) {} }
    return Math.ceil(String(text || "").length / 3.6);
  }
  function compact(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n); }

  function contentFor(m) {
    if (m.role === "user") return typeof mdUser === "function" ? mdUser(m.content || "") : md(m.content || "");
    let text = typeof visibleAnswer === "function" ? visibleAnswer(m.content || "") : String(m.content || "");
    if (typeof MemuProducts !== "undefined" && MemuProducts.stripForText) text = MemuProducts.stripForText(text, en());
    if (!st.opts.images && typeof MemuVisuals !== "undefined") text = MemuVisuals.stripForText(text, en());
    return typeof answerHtml === "function" ? answerHtml(m, text) : md(text);
  }

  function messageHtml(row, number) {
    const { m, index } = row;
    const o = st.opts;
    const user = m.role === "user";
    const who = user ? t("Вы", "You") : (m.model ? `MemuAI · ${esc(typeof modelDisplayName === "function" ? modelDisplayName(m.model) : m.model)}` : "MemuAI");
    const when = o.timestamps && m.createdAt ? `<small>${esc(whenText(m.createdAt))}</small>` : "";
    const parts = [`<section class="mx-msg ${user ? "mx-user" : "mx-assistant"}" id="mx-m${index}" data-index="${index}">`,
      `<div class="mx-who" data-role="${user ? "user" : "assistant"}">${who}${when}</div>`];
    if (!user && o.reasoning && m.reasoning) {
      const text = typeof hidePrivateProtocols === "function" ? hidePrivateProtocols(m.reasoning) : m.reasoning;
      parts.push(`<div class="mx-step mx-reasoning"><div class="mx-step-title">${t("Ход мыслей", "Thought process")}</div><div class="mx-step-body">${esc(text.trim())}</div></div>`);
    }
    if (!user && o.tools && (m.toolHistory?.length || m.commandRuns?.length)) {
      const rows = (m.toolHistory || []).filter((r) => r.note === undefined).map((r) =>
        `<div class="mx-tool"><code>${esc(r.tool || "")}</code> ${esc(typeof toolArgLabel === "function" ? toolArgLabel(r.args) : "")}${r.summary || r.error ? ` — ${esc(r.error || r.summary)}` : ""}</div>`);
      (m.commandRuns || []).forEach((r) => rows.push(`<div class="mx-tool"><code>${esc(r.command || "")}</code>${r.exit_code != null ? ` — exit ${esc(r.exit_code)}` : ""}</div>`));
      if (rows.length) parts.push(`<div class="mx-step mx-tools"><div class="mx-step-title">${t("Инструменты", "Tools")}</div>${rows.join("")}</div>`);
    }
    let body = contentFor(m);
    if (user && m.attachments?.length) {
      const imgs = m.attachments.filter((a) => String(a.data || "").startsWith("data:image") || (a.isVideo && a.poster));
      const files = m.attachments.filter((a) => !imgs.includes(a));
      if (o.images && imgs.length) body = `<div class="mx-images">${imgs.map((a) => `<img src="${esc(a.poster || a.data)}" alt="${esc(a.name || "")}">`).join("")}</div>` + body;
      else if (imgs.length) files.push(...imgs);
      if (files.length) body += `<div>${files.map((a) => `<span class="mx-attach">${t("Файл", "File")}: ${esc(a.name || "file")}</span>`).join("")}</div>`;
    }
    if (!user && m.images?.length) {
      const urls = m.images.filter((u) => u && u !== "__skeleton__");
      if (urls.length) body += o.images ? `<div class="mx-images">${urls.map((u) => `<img src="${esc(u)}" alt="${t("Изображение", "Image")}" data-remote="1">`).join("")}</div>`
        : `<p class="mx-note">[${t("изображений", "images")}: ${urls.length}]</p>`;
    }
    if (m.error && !String(m.content || "").trim()) body += `<p class="mx-note">${esc(m.error)}</p>`;
    parts.push(`<div class="mx-content">${body}</div>`);
    if (o.stats && !user) {
      const stats = [];
      const tokens = (m.ctx && +m.ctx.real) ? null : tokensOf(m.content);
      stats.push(`${t("≈ токенов ответа", "≈ answer tokens")}: ${compact(tokens ?? tokensOf(m.content))}`);
      if (m.ms) stats.push(`${t("время", "time")}: ${(m.ms / 1000).toFixed(1)} ${t("с", "s")}`);
      parts.push(`<div class="mx-stats">${stats.join(" · ")}</div>`);
    }
    parts.push("</section>");
    return parts.join("");
  }

  function coverHtml(rows) {
    const o = st.opts;
    const title = st.chat?.title || t("Чат MemuAI", "MemuAI chat");
    if (!o.meta) return `<header class="mx-cover"><h1 class="mx-title">${esc(title)}</h1></header>`;
    const models = [...new Set(rows.filter((r) => r.m.role === "assistant" && r.m.model).map((r) => r.m.model))]
      .map((id) => (typeof modelDisplayName === "function" ? modelDisplayName(id) : id));
    const first = rows.find((r) => r.m.createdAt)?.m.createdAt, last = [...rows].reverse().find((r) => r.m.createdAt)?.m.createdAt;
    const tokens = rows.reduce((sum, r) => sum + tokensOf(r.m.content), 0);
    const meta = [
      [t("Экспортировано", "Exported"), whenText(Date.now())],
      first ? [t("Период", "Period"), first === last ? whenText(first) : `${whenText(first)} — ${whenText(last)}`] : null,
      [t("Сообщений", "Messages"), String(rows.length)],
      otherVersions() ? [t("Ветка", "Branch"), t(`активная (других версий в чате: ${otherVersions()})`, `active (other versions in the chat: ${otherVersions()})`)] : null,
      models.length ? [models.length > 1 ? t("Модели", "Models") : t("Модель", "Model"), models.join(", ")] : null,
      o.stats ? [t("≈ токенов", "≈ tokens"), compact(tokens)] : null,
      st.chat?.mode === "work" ? ["Work", (S.workspaces?.items || []).find((w) => w.id === st.chat.project_id)?.name || ""] : null,
    ].filter(Boolean);
    return `<header class="mx-cover"><div class="mx-brand">MemuAI</div><h1 class="mx-title">${esc(title)}</h1>
      <div class="mx-meta">${meta.map(([k, v]) => `<p><b>${esc(k)}:</b> ${esc(v)}</p>`).join("")}</div></header>`;
  }
  function tocHtml(rows) {
    const questions = rows.filter((r) => r.m.role === "user");
    if (!st.opts.toc || questions.length < 2) return "";
    return `<nav class="mx-toc"><h2>${t("Содержание", "Contents")}</h2><ol>${questions.map((r) =>
      `<li><a href="#mx-m${r.index}">${esc(preview(r.m.content, 90))}</a></li>`).join("")}</ol></nav>`;
  }

  /* Post-process rendered HTML: code headers, visualizations, diagrams, images, formulas.
     mode: "preview" (fast, nothing fetched) | "html" | "pdf" | "docx". */
  async function finish(html, mode) {
    const host = document.createElement("div");
    host.className = "mx-export-host";
    host.style.cssText = "position:fixed;left:-20000px;top:0;width:800px;visibility:hidden;pointer-events:none;contain:layout";
    host.innerHTML = html;
    document.body.appendChild(host);
    try {
      const o = st.opts;
      const p = palette();
      host.querySelectorAll(".code-block-wrap .code-header").forEach((head) => {
        const label = head.querySelector("span")?.textContent || "";
        head.innerHTML = `<span>${esc(label)}</span>`;
        if (!o.code || label.toLowerCase() === "code") head.remove();
      });
      host.querySelectorAll("button, .msg-tools, .expand-message, script").forEach((node) => node.remove());
      host.querySelectorAll("[onclick]").forEach((node) => node.removeAttribute("onclick"));
      host.querySelectorAll(".collapsed").forEach((node) => node.classList.remove("collapsed"));
      if (!o.code) host.querySelectorAll("[class*='tok-']").forEach((node) => node.removeAttribute("class"));
      if (!o.math) host.querySelectorAll(".katex").forEach((node) => {
        const tex = node.querySelector("annotation")?.textContent || node.textContent;
        const code = document.createElement("code"); code.textContent = tex; node.replaceWith(code);
      });
      host.querySelectorAll("a[href]").forEach((a) => { a.setAttribute("target", "_blank"); a.setAttribute("rel", "noreferrer"); });
      await visuals(host, mode, p);
      await diagrams(host, mode);
      await images(host, mode);
      return host.innerHTML;
    } finally {
      host.remove();
    }
  }

  async function visuals(host, mode, p) {
    const nodes = [...host.querySelectorAll(".memuai-visual[data-visual]")];
    let n = 0;
    for (const node of nodes) {
      if (!st.opts.images) { node.outerHTML = `<p class="mx-note">[${t("визуализация", "visualization")}]</p>`; continue; }
      let srcdoc = "";
      try {
        const probe = document.createElement("div");
        probe.innerHTML = node.outerHTML;
        host.appendChild(probe);
        MemuVisuals.hydrate(probe);
        srcdoc = probe.querySelector("iframe")?.srcdoc || "";
        probe.remove();
      } catch (e) { srcdoc = ""; }
      if (!srcdoc) { node.outerHTML = `<p class="mx-note">[${t("визуализация", "visualization")}]</p>`; continue; }
      // The export theme, not the window's.
      srcdoc = srcdoc.replace(/:root\{color-scheme:[^}]*\}/, `:root{color-scheme:${st.opts.theme === "dark" ? "dark" : "light"};--bg:${p.bg};--panel:${p.panel};--text:${p.text};--muted:${p.muted};--accent:${p.accent};--border:${p.border};--font:${fontOf().css.replace(/"/g, "'")}}`);
      const live = [...document.querySelectorAll("#messages .memuai-visual iframe")].find((f) => f.parentElement?.dataset.visual === node.dataset.visual);
      const height = Math.max(160, Math.min(900, parseInt(live?.style.height || "", 10) || 380));
      n += 1;
      const frame = `<iframe class="mx-visual" sandbox="allow-scripts" title="${esc(t("Визуализация", "Visualization"))}" style="height:${height}px" data-mx-raster="v${n}" data-mx-alt="${esc(t("Визуализация", "Visualization"))}" srcdoc="${esc(srcdoc)}"></iframe>`;
      node.outerHTML = frame;
    }
  }

  async function svgToPng(svg, width) {
    const clone = svg.cloneNode(true);
    const box = svg.getBoundingClientRect();
    const w = Math.max(10, Math.round(width || box.width || +svg.getAttribute("width") || 600));
    const h = Math.max(10, Math.round(box.height || +svg.getAttribute("height") || w * 0.6));
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", w); clone.setAttribute("height", h);
    const data = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(clone));
    const img = new Image();
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = data; });
    const canvas = document.createElement("canvas");
    canvas.width = w * 2; canvas.height = h * 2;
    const ctx = canvas.getContext("2d"); ctx.scale(2, 2); ctx.drawImage(img, 0, 0, w, h);
    return { src: canvas.toDataURL("image/png"), w };
  }

  /* Diagrams and rich tables of the r29-tables-diagrams workstream (window.MemuDiagrams / MemuTables) and any
     other inline SVG: kept as vector SVG in HTML/PDF, turned into PNG for Word. */
  async function diagrams(host, mode) {
    const D = window.MemuDiagrams, T = window.MemuTables;
    try { if (D?.hydrate) await D.hydrate(host); else if (D?.render) await D.render(host); } catch (e) {}
    // MemuTables.hydrate is NOT called here: it adds the interactive toolbar (sort, filter, copy…) that must
    // not end up in a file; exported tables stay plain <table>s (Word turns them into real tables).
    void T;
    host.querySelectorAll(".mt-bar, .mt-menu, .mt-pending").forEach((node) => node.remove());
    const blocks = [...host.querySelectorAll("[data-memu-diagram], .memu-diagram, .mx-diagram")];
    // The export theme, not the window's: hydrate() draws with the app colours (a dark window gave a light PDF
    // near-white chart titles/legends, black grid lines and dark Mermaid label boxes). Redraw as vector SVG.
    const theme = st.opts.theme === "dark" || (st.opts.theme === "app" && document.documentElement.dataset.theme !== "light") ? "dark" : "light";
    if (mode !== "docx") {
      if (!D?.toSVG) return;
      for (const block of blocks) {
        const stage = block.querySelector(".md-stage");
        if (!stage) continue;
        try {
          const scene = await D.exportScene(block, { theme, background: "none", padding: 0 });
          stage.innerHTML = scene.svg;
          const svg = stage.querySelector("svg");
          if (svg) { svg.removeAttribute("height"); svg.style.cssText = `width:100%;height:auto;max-width:${Math.round(scene.width)}px`; }
        } catch (e) { /* keep the app-coloured drawing */ }
      }
      return;
    }
    for (const block of blocks) {
      let png = null;
      try {
        if (D?.toPNG) {
          const out = await D.toPNG(block, { scale: 2, theme });
          png = typeof out === "string" ? { src: out } : out && out.src ? out : null;
        }
      } catch (e) { png = null; }
      if (!png) {
        const svg = block.querySelector("svg");
        if (svg) { try { png = await svgToPng(svg); } catch (e) { png = null; } }
      }
      if (png?.src) block.outerHTML = `<p><img class="mx-block-img" src="${png.src}" ${png.w ? `data-width="${Math.round(png.w)}"` : ""} alt="${esc(t("Диаграмма", "Diagram"))}"></p>`;
    }
    for (const svg of [...host.querySelectorAll(".mx-content svg")]) {
      if (svg.closest(".katex") || svg.closest("img")) continue;
      if (svg.getBoundingClientRect().width < 40) { svg.remove(); continue; }
      try { const png = await svgToPng(svg); svg.outerHTML = `<img class="mx-block-img" src="${png.src}" data-width="${png.w}" alt="">`; }
      catch (e) { svg.remove(); }
    }
  }

  async function toDataUrl(url) {
    if (String(url).startsWith("data:")) return url;
    const via = typeof imageRenderUrl === "function" && /genspark\.ai/.test(url) ? imageRenderUrl(url)
      : /^https?:/i.test(url) ? "/api/pg/media?url=" + encodeURIComponent(url) : url;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const resp = await fetch(via, { signal: controller.signal });
      if (!resp.ok) throw new Error(String(resp.status));
      const blob = await resp.blob();
      return await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(blob); });
    } finally { clearTimeout(timer); }
  }

  async function images(host, mode) {
    const imgs = [...host.querySelectorAll("img")];
    for (const img of imgs) {
      const src = img.getAttribute("src") || "";
      img.removeAttribute("data-preview");
      if (!st.opts.images) { img.outerHTML = `<span class="mx-note">[${esc(img.alt || t("изображение", "image"))}]</span>`; continue; }
      if (mode === "preview") {
        // Round 33 (r33d-media): the sandboxed preview frame cannot load our own files — embed stored pictures.
        if (src.startsWith("/api/pg/media/file/")) { try { img.setAttribute("src", await toDataUrl(src)); } catch (e) {} continue; }
        if (/^https?:/i.test(src) && typeof imageRenderUrl === "function") img.setAttribute("src", imageRenderUrl(src));
        continue;
      }
      if (src.startsWith("data:image/svg")) {
        if (mode === "docx") { try { const out = await svgToPng(await svgFromData(src)); img.setAttribute("src", out.src); } catch (e) {} }
        continue;
      }
      if (src.startsWith("data:")) continue;
      try { img.setAttribute("src", await toDataUrl(src)); }
      catch (e) { img.outerHTML = `<a href="${esc(src)}">${esc(img.alt || src)}</a>`; }
    }
  }
  async function svgFromData(src) {
    const text = src.includes(";base64,") ? atob(src.split(",")[1]) : decodeURIComponent(src.split(",")[1] || "");
    const box = document.createElement("div"); box.innerHTML = text; document.body.appendChild(box);
    const svg = box.querySelector("svg"); setTimeout(() => box.remove(), 0);
    return svg;
  }

  let katexCache = null;
  async function katexCss() {
    if (katexCache === null) {
      try { katexCache = await (await fetch("/api/export/katex.css")).text(); } catch (e) { katexCache = ""; }
    }
    return katexCache;
  }

  async function buildDocument(mode) {
    const rows = chosenRows();
    const article = `${coverHtml(rows)}${tocHtml(rows)}${rows.map((row, i) => messageHtml(row, i + 1)).join("")}
      <footer class="mx-end">${t("Экспортировано из MemuAI", "Exported from MemuAI")} · ${esc(whenText(Date.now()))}</footer>`;
    const body = await finish(`<article class="mx-doc">${article}</article>`, mode);
    const p = palette();
    const target = mode === "preview" ? "preview" : mode === "print" ? "print" : mode === "html" ? "html" : "pdf";
    // The preview frame is sandboxed (opaque origin): KaTeX fonts must come embedded, not as same-origin URLs.
    const katex = mode === "preview" ? `<style>${await katexCss()}</style>` : `<style id="mx-katex">/*MX-KATEX-CSS*/</style>`;
    const title = esc(st.chat?.title || "MemuAI");
    const html = `<!doctype html><html lang="${en() ? "en" : "ru"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="MemuAI"><title>${title}</title>${katex}<style>${documentCss(p, target)}</style></head>
<body class="mx-theme-${esc(st.opts.theme)}">${body}</body></html>`;
    return { html, body, rows };
  }

  /* ------------------------------------------------------------------ text formats */
  function textExport(format) {
    const rows = chosenRows();
    const o = st.opts;
    const title = st.chat?.title || t("Чат MemuAI", "MemuAI chat");
    const clean = (m) => {
      let text = m.role === "assistant" && typeof visibleAnswer === "function" ? visibleAnswer(m.content || "") : String(m.content || "");
      if (typeof MemuVisuals !== "undefined") text = MemuVisuals.stripForText(text, en());
      if (typeof MemuProducts !== "undefined" && MemuProducts.stripForText) text = MemuProducts.stripForText(text, en());
      return text.trim();
    };
    const who = (m) => m.role === "user" ? t("Вы", "You") : (m.model ? `MemuAI (${typeof modelDisplayName === "function" ? modelDisplayName(m.model) : m.model})` : "MemuAI");
    if (format === "json") {
      return JSON.stringify({ title, exported: new Date().toISOString(), app: "MemuAI", chat_id: st.chat?.id || "", mode: st.chat?.mode || "chat",
        branch: otherVersions() ? { exported: "active", other_versions: otherVersions() } : undefined,
        messages: rows.map(({ m }) => ({ role: m.role, content: clean(m), created: m.createdAt ? new Date(m.createdAt).toISOString() : undefined,
          model: m.model || undefined, reasoning: o.reasoning && m.reasoning ? m.reasoning : undefined,
          tools: o.tools && m.toolHistory?.length ? m.toolHistory.filter((r) => r.note === undefined).map((r) => ({ tool: r.tool, ok: r.ok, summary: r.summary || r.error || "" })) : undefined,
          attachments: (m.attachments || []).map((a) => ({ name: a.name, type: a.type, size: a.size, ...(o.images && String(a.data || "").startsWith("data:image") ? { data: a.data } : {}) })),
          images: o.images ? (m.images || []).filter((u) => u && u !== "__skeleton__") : undefined })) }, null, 2);
    }
    const md = format === "md";
    const out = [];
    if (md) {
      out.push(`# ${title}`);
      if (o.meta) out.push(`> ${t("Экспортировано из MemuAI", "Exported from MemuAI")} · ${whenText(Date.now())} · ${t("сообщений", "messages")}: ${rows.length}`
        + (otherVersions() ? ` · ${t("активная ветка, других версий в чате", "active branch, other versions in the chat")}: ${otherVersions()}` : ""));
    } else out.push(title, "=".repeat(Math.min(60, title.length)));
    for (const { m } of rows) {
      const head = `${who(m)}${o.timestamps && m.createdAt ? ` · ${whenText(m.createdAt)}` : ""}`;
      const block = [md ? `## ${head}` : `${head}:`];
      if (o.reasoning && m.reasoning) block.push(md ? `<details><summary>${t("Ход мыслей", "Thought process")}</summary>\n\n${m.reasoning.trim()}\n\n</details>` : `[${t("Ход мыслей", "Thought process")}]\n${m.reasoning.trim()}`);
      block.push(clean(m));
      const atts = (m.attachments || []).map((a) => a.name).filter(Boolean);
      if (atts.length) block.push((md ? "_" : "") + t("Вложения: ", "Attachments: ") + atts.join(", ") + (md ? "_" : ""));
      if (o.images) (m.images || []).filter((u) => u && u !== "__skeleton__" && !u.startsWith("data:")).forEach((u) => block.push(
        u.startsWith("/api/pg/media/") ? `[${t("изображение", "image")}]` : md ? `![](${u})` : u));   // r33d-media: stored files have no public address
      out.push(block.join("\n\n"));
    }
    return out.join(md ? "\n\n---\n\n" : "\n\n");
  }

  /* ------------------------------------------------------------------ PDF header / footer templates */
  function headerFooter() {
    const o = st.opts, p = palette();
    const muted = st.opts.theme === "dark" ? "#9aa3ad" : p.muted;
    const margin = MARGINS[o.margins] || 20;
    const font = fontOf().css.replace(/"/g, "'");
    const head = o.header.trim();
    const foot = o.footer.trim();
    const style = `font-family:${font};font-size:8px;color:${muted};width:100%;padding:0 ${margin}mm;display:flex;justify-content:space-between;gap:12px;`;
    const header = head ? `<div style="${style}"><span>${esc(head)}</span><span>MemuAI</span></div>` : "";
    const footer = (foot || o.numbers) ? `<div style="${style}"><span>${esc(foot)}</span>${o.numbers ? `<span><span class="pageNumber"></span> / <span class="totalPages"></span></span>` : "<span></span>"}</div>` : "";
    return { header, footer };
  }

  /* ------------------------------------------------------------------ dialog */
  function seg(name, options, value) {
    return `<div class="mx-seg" role="radiogroup">${options.map(([v, label]) => `<label class="mx-seg-opt"><input type="radio" name="${name}" value="${esc(v)}" ${String(v) === String(value) ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")}</div>`;
  }
  function sw(id, label, checked, hint = "") {
    return `<label class="mx-switch" ${hint ? `title="${esc(hint)}"` : ""}><input type="checkbox" data-opt="${id}" ${checked ? "checked" : ""}><span class="mx-switch-ui" aria-hidden="true"></span><span>${esc(label)}</span></label>`;
  }
  const FORMAT_ICON = {
    pdf: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M8.5 16.5v-4h1.2a1.2 1.2 0 0 1 0 2.4H8.5M13 12.5v4h.8a2 2 0 0 0 0-4zM17.5 12.5h-1.6v4M15.9 14.5h1.3"/></svg>',
    docx: '<svg viewBox="0 0 24 24"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m8 12 1.3 5 1.7-4 1.7 4 1.3-5"/></svg>',
    html: '<svg viewBox="0 0 24 24"><path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/></svg>',
    md: '<svg viewBox="0 0 24 24"><rect x="2.5" y="5.5" width="19" height="13" rx="2"/><path d="M6 15V9l2.5 3L11 9v6M16 9v6M14 13l2 2 2-2"/></svg>',
    json: '<svg viewBox="0 0 24 24"><path d="M8 4c-2 0-2.5 1-2.5 3s.2 3-2 5c2.2 2 2 3 2 5s.5 3 2.5 3M16 4c2 0 2.5 1 2.5 3s-.2 3 2 5c-2.2 2-2 3-2 5s-.5 3-2.5 3"/></svg>',
    txt: '<svg viewBox="0 0 24 24"><path d="M5 6h14M5 10h14M5 14h10M5 18h7"/></svg>',
  };

  function dialogHtml() {
    const o = st.opts;
    const total = st.rows.length;
    const font = (f) => (typeof f.label === "function" ? f.label() : f.label);
    return `<div class="mx-export">
      <header class="mx-x-head"><h2>${icon("export")} ${t("Экспорт чата", "Export chat")}</h2><p class="sub">${esc(st.chat?.title || "")}${otherVersions() ? ` · ${esc(t("экспортируется активная ветка — другие версии ответов в файл не попадут", "the active branch is exported — other answer versions are not included"))}` : ""}</p></header>
      <div class="mx-x-grid">
        <div class="mx-x-side">
          <section class="mx-x-sec"><h3>${t("Формат", "Format")}</h3>
            <div class="mx-formats">${FORMATS.map((f) => `<button type="button" class="mx-format ${o.format === f.id ? "on" : ""}" data-format="${f.id}" aria-pressed="${o.format === f.id}">
              <span class="mx-format-ico">${FORMAT_ICON[f.id]}</span><b>${esc(f.label)}</b><small>${esc(f.desc())}</small></button>`).join("")}</div></section>

          <section class="mx-x-sec"><h3>${t("Сообщения", "Messages")} <small>${total}</small></h3>
            ${seg("mx-range", [["all", t("Все", "All")], ["from", t("Диапазон", "Range")], ["selected", t("Выбрать", "Pick")]], o.range)}
            <div class="mx-range-from" ${o.range === "from" ? "" : "hidden"}>
              <label>${t("С", "From")} <input type="number" class="pinput compact" id="mx-from" min="1" max="${total}" value="${st.from}"></label>
              <label>${t("по", "to")} <input type="number" class="pinput compact" id="mx-to" min="1" max="${total}" value="${st.to}"></label>
              <div class="mx-range-hint" id="mx-range-hint"></div>
            </div>
            <div class="mx-pick" ${o.range === "selected" ? "" : "hidden"}>
              <div class="mx-pick-tools"><button type="button" class="mx-link" data-pick="all">${t("Все", "All")}</button><button type="button" class="mx-link" data-pick="none">${t("Ничего", "None")}</button>
                <button type="button" class="mx-link" data-pick="user">${t("Только мои", "Mine only")}</button><button type="button" class="mx-link" data-pick="assistant">${t("Только ответы", "Answers only")}</button>
                <span class="mx-pick-count" id="mx-pick-count"></span></div>
              <div class="mx-pick-list">${st.rows.map((row, i) => `<label class="mx-pick-row"><input type="checkbox" data-pick-index="${row.index}" ${st.selected.has(row.index) ? "checked" : ""}>
                <span class="mx-pick-n">${i + 1}</span><span class="mx-pick-who ${row.m.role}">${row.m.role === "user" ? t("Вы", "You") : "AI"}</span><span class="mx-pick-text">${esc(preview(row.m.content, 90))}</span></label>`).join("")}</div>
            </div></section>

          <section class="mx-x-sec"><h3>${t("Что включить", "Include")}</h3>
            <div class="mx-switches">
              ${sw("meta", t("Заголовок и сведения", "Title and details"), o.meta, t("Название, дата, число сообщений, модели", "Title, date, message count, models"))}
              ${sw("timestamps", t("Время сообщений", "Message times"), o.timestamps)}
              ${sw("images", t("Изображения и графики", "Images and charts"), o.images, t("Встраиваются в файл", "Embedded in the file"))}
              ${sw("math", t("Формулы", "Formulas"), o.math, t("KaTeX; в Word — редактируемые формулы", "KaTeX; editable equations in Word"))}
              ${sw("code", t("Подсветка кода", "Code highlighting"), o.code)}
              ${sw("reasoning", t("Ход мыслей", "Reasoning"), o.reasoning)}
              ${sw("tools", t("Шаги инструментов", "Tool steps"), o.tools)}
              ${sw("stats", t("Статистика токенов", "Token stats"), o.stats)}
              ${sw("toc", t("Содержание", "Table of contents"), o.toc, t("Список ваших вопросов со ссылками", "Your questions as linked list"))}
            </div></section>

          <section class="mx-x-sec mx-look" ${FORMATS.find((f) => f.id === o.format)?.visual ? "" : "hidden"}><h3>${t("Оформление", "Look")}</h3>
            <div class="mx-themes">${Object.entries(THEMES).map(([id, th]) => {
              const pal = id === "app" ? appPalette() : th;
              return `<button type="button" class="mx-theme ${o.theme === id ? "on" : ""}" data-theme-id="${id}" aria-pressed="${o.theme === id}" title="${esc(th.label())}">
                <span class="mx-theme-sw" style="background:${esc(pal.bg)};color:${esc(pal.text)};border-color:${esc(pal.border)}"><i style="background:${esc(pal.accent)}"></i><b>Aa</b></span><small>${esc(th.label())}</small></button>`;
            }).join("")}</div>
            <div class="mx-fields">
              <label>${t("Шрифт", "Font")}<select class="pinput compact" data-opt="font" data-no-search>${FONTS.map((f) => `<option value="${f.id}" ${o.font === f.id ? "selected" : ""}>${esc(font(f))}</option>`).join("")}</select></label>
              <label>${t("Код", "Code")}<select class="pinput compact" data-opt="mono">${MONOS.map((f) => `<option value="${f.id}" ${o.mono === f.id ? "selected" : ""}>${esc(f.label)}</option>`).join("")}</select></label>
              <label>${t("Размер", "Size")} <output id="mx-size-out">${o.size} pt</output><input type="range" min="8" max="16" step="0.5" value="${o.size}" data-opt="size"></label>
            </div>
            <div class="mx-page" ${o.format === "html" ? "hidden" : ""}>
              <div class="mx-fields">
                <label>${t("Бумага", "Paper")}${seg("mx-paper", [["a4", "A4"], ["letter", "Letter"]], o.page)}</label>
                <label>${t("Ориентация", "Orientation")}${seg("mx-orient", [["portrait", t("Книжная", "Portrait")], ["landscape", t("Альбомная", "Landscape")]], o.orientation)}</label>
                <label>${t("Поля", "Margins")}${seg("mx-margins", [["narrow", t("Узкие", "Narrow")], ["normal", t("Обычные", "Normal")], ["wide", t("Широкие", "Wide")]], o.margins)}</label>
              </div>
              <div class="mx-fields mx-hf">
                <label>${t("Верхний колонтитул", "Header")}<input class="pinput compact" data-opt="header" value="${esc(o.header)}" placeholder="${esc(st.chat?.title || "")}"></label>
                <label>${t("Нижний колонтитул", "Footer")}<input class="pinput compact" data-opt="footer" value="${esc(o.footer)}" placeholder="${esc(t("Конфиденциально", "Confidential"))}"></label>
              </div>
              ${sw("numbers", t("Номера страниц", "Page numbers"), o.numbers)}
            </div></section>

          <section class="mx-x-sec"><h3>${t("Сохранение", "Saving")}</h3>
            ${seg("mx-save", [["downloads", t("В «Загрузки/MemuAI»", "To Downloads/MemuAI")], ["ask", t("Спросить, куда", "Ask where")]], o.save)}
            <div class="mx-engine" id="mx-engine"></div></section>
        </div>
        <div class="mx-x-preview">
          <div class="mx-preview-bar"><span id="mx-preview-label">${t("Предпросмотр", "Preview")}</span><span class="mx-grow"></span><span id="mx-preview-info" class="mx-preview-info"></span></div>
          <div class="mx-preview-stage"><iframe id="mx-preview" sandbox="allow-scripts" title="${esc(t("Предпросмотр экспорта", "Export preview"))}"></iframe>
            <div class="mx-preview-busy" id="mx-preview-busy" hidden><i></i></div></div>
        </div>
      </div>
      <footer class="mx-x-foot">
        <span class="mx-x-summary" id="mx-summary"></span><span class="mx-grow"></span>
        <button type="button" class="pbtn ghost" id="mx-copy">${icon("copy")} ${t("Копировать", "Copy")}</button>
        <button type="button" class="pbtn ghost" id="mx-cancel">${t("Отмена", "Cancel")}</button>
        <button type="button" class="pbtn" id="mx-go">${icon("download")} <span id="mx-go-label"></span></button>
      </footer></div>`;
  }

  function summaryText() {
    const rows = chosenRows();
    const f = FORMATS.find((x) => x.id === st.opts.format);
    return en() ? `${rows.length} of ${st.rows.length} messages · ${f.label}` : `${rows.length} из ${st.rows.length} сообщений · ${f.label}`;
  }
  function syncDialog() {
    const root = document.querySelector(".mx-export");
    if (!root) return;
    const o = st.opts;
    const f = FORMATS.find((x) => x.id === o.format);
    root.querySelectorAll(".mx-format").forEach((b) => { const on = b.dataset.format === o.format; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    root.querySelectorAll(".mx-theme").forEach((b) => { const on = b.dataset.themeId === o.theme; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    root.querySelector(".mx-look").hidden = !f.visual;
    root.querySelector(".mx-page").hidden = o.format === "html";
    root.querySelector(".mx-range-from").hidden = o.range !== "from";
    root.querySelector(".mx-pick").hidden = o.range !== "selected";
    $("#mx-go-label").textContent = t("Экспортировать в ", "Export to ") + f.label;
    $("#mx-copy").hidden = f.visual;
    $("#mx-summary").textContent = summaryText();
    const count = $("#mx-pick-count"); if (count) count.textContent = en() ? `${st.selected.size} selected` : `выбрано: ${st.selected.size}`;
    const hint = $("#mx-range-hint");
    if (hint && o.range === "from") {
      const rows = st.rows;
      const a = rows[Math.max(0, Math.min(rows.length - 1, st.from - 1))], b = rows[Math.max(0, Math.min(rows.length - 1, st.to - 1))];
      hint.innerHTML = a && b ? `<span>${st.from}: ${esc(preview(a.m.content, 50))}</span><span>${st.to}: ${esc(preview(b.m.content, 50))}</span>` : "";
    }
    $("#mx-go").disabled = !chosenRows().length || st.busy;
    const engine = $("#mx-engine");
    if (engine) engine.innerHTML = o.format === "pdf" && st.caps && st.caps.pdf !== "edge"
      ? `${icon("info")} ${t("Microsoft Edge не найден — PDF сохраняется через окно печати («Сохранить как PDF»).", "Microsoft Edge was not found — the PDF is saved through the print dialog («Save as PDF»).")}`
      : o.format === "docx" ? `${icon("info")} ${t("Формулы станут редактируемыми формулами Word, таблицы — таблицами.", "Formulas become editable Word equations, tables become real tables.")}` : "";
  }

  let previewSeq = 0;
  const pagesLabel = (n) => (en() ? `≈ ${n} page${n === 1 ? "" : "s"}` : `≈ ${n} стр.`);
  window.addEventListener("message", (e) => {
    const frame = $("#mx-preview");
    if (!st || !frame || e.source !== frame.contentWindow || !e.data || e.data.mxSeq !== previewSeq) return;
    const n = Math.max(1, Math.min(9999, Math.round(+e.data.mxPages || 1)));
    const info = $("#mx-preview-info");
    if (info && st.opts.format !== "html" && FORMATS.find((x) => x.id === st.opts.format)?.visual) info.textContent = pagesLabel(n);
  });
  function schedulePreview(delay = 250) {
    clearTimeout(st.previewTimer);
    st.previewTimer = setTimeout(renderPreview, delay);
  }
  async function renderPreview() {
    const frame = $("#mx-preview");
    if (!frame || !st) return;
    const seq = ++previewSeq;
    const busy = $("#mx-preview-busy"); if (busy) busy.hidden = false;
    try {
      const f = FORMATS.find((x) => x.id === st.opts.format);
      if (!chosenRows().length) {
        frame.srcdoc = `<!doctype html><body style="font:14px system-ui;color:#888;display:grid;place-items:center;height:90vh">${esc(t("Не выбрано ни одного сообщения", "No messages selected"))}</body>`;
        return;
      }
      if (!f.visual) {
        const text = textExport(f.id);
        const p = st.opts.theme === "dark" ? THEMES.dark : THEMES.light;
        frame.srcdoc = `<!doctype html><meta charset="utf-8"><body style="margin:0;background:${p.bg};color:${p.text}"><pre style="margin:0;padding:18px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;word-break:break-word">${esc(text.slice(0, 300000))}${text.length > 300000 ? "\n…" : ""}</pre></body>`;
        $("#mx-preview-info").textContent = `${compact(text.length)} ${t("симв.", "chars")}`;
        return;
      }
      const { html } = await buildDocument("preview");
      if (seq !== previewSeq) return;
      const page = PAGES[st.opts.page] || PAGES.a4;
      const paperPx = ((st.opts.orientation === "landscape" ? page.h : page.w) / 25.4) * 96 + 40;
      const zoom = st.opts.format === "html" ? 1 : Math.min(1, (frame.clientWidth - 8) / paperPx);
      // r29d-chat4: the page count used to be characters only (a report with a picture, a table, a chart and a
      // diagram said «≈ 1 page» for ~3 pages). The sandboxed preview now measures its rendered sheet and posts the
      // count back; the character estimate is only shown until that arrives.
      const landscape = st.opts.orientation === "landscape";
      const measure = st.opts.format === "html" ? "" : `<script>(()=>{const W=${(landscape ? page.h : page.w)},H=${(landscape ? page.w : page.h)},M=${MARGINS[st.opts.margins] || 20},S=${seq};
        const send=()=>{const d=document.querySelector(".mx-doc");if(!d)return;const px=96/25.4,scale=d.offsetWidth/(W*px)||1;
        const n=Math.max(1,Math.ceil((d.scrollHeight/scale-2*M*px-1)/((H-2*M)*px)));parent.postMessage({mxPages:n,mxSeq:S},"*");};
        addEventListener("load",()=>{send();setTimeout(send,600);});})();<\/script>`;
      frame.srcdoc = html.replace("<html ", `<html style="zoom:${zoom.toFixed(3)}" `).replace(/<\/body>(?![\s\S]*<\/body>)/, `${measure}</body>`);
      const pages = Math.max(1, Math.round(chosenRows().reduce((s, r) => s + String(r.m.content || "").length, 0) / (2600 * (11 / st.opts.size) ** 2)));
      $("#mx-preview-info").textContent = st.opts.format === "html" ? "" : pagesLabel(pages);
    } catch (e) {
      console.warn("export preview", e);
    } finally {
      if (seq === previewSeq && busy) busy.hidden = true;
    }
  }

  function bindDialog() {
    const root = document.querySelector(".mx-export");
    root.addEventListener("click", (event) => {
      const fmt = event.target.closest("[data-format]");
      if (fmt) { st.opts.format = fmt.dataset.format; syncDialog(); schedulePreview(0); return; }
      const theme = event.target.closest("[data-theme-id]");
      if (theme) { st.opts.theme = theme.dataset.themeId; syncDialog(); schedulePreview(0); return; }
      const pick = event.target.closest("[data-pick]");
      if (pick) {
        const kind = pick.dataset.pick;
        st.selected = new Set(st.rows.filter((r) => kind === "all" || r.m.role === kind).map((r) => r.index));
        if (kind === "none") st.selected.clear();
        root.querySelectorAll("[data-pick-index]").forEach((box) => { box.checked = st.selected.has(+box.dataset.pickIndex); });
        syncDialog(); schedulePreview(); return;
      }
    });
    root.addEventListener("change", (event) => {
      const el = event.target;
      if (el.name === "mx-range") st.opts.range = el.value;
      else if (el.name === "mx-paper") st.opts.page = el.value;
      else if (el.name === "mx-orient") st.opts.orientation = el.value;
      else if (el.name === "mx-margins") st.opts.margins = el.value;
      else if (el.name === "mx-save") st.opts.save = el.value;
      else if (el.dataset.pickIndex) { const i = +el.dataset.pickIndex; if (el.checked) st.selected.add(i); else st.selected.delete(i); }
      else if (el.dataset.opt) {
        const key = el.dataset.opt;
        st.opts[key] = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.value;
      } else if (el.id === "mx-from" || el.id === "mx-to") {
        st.from = Math.max(1, Math.min(st.rows.length, +$("#mx-from").value || 1));
        st.to = Math.max(st.from, Math.min(st.rows.length, +$("#mx-to").value || st.rows.length));
      } else return;
      syncDialog(); schedulePreview();
    });
    root.addEventListener("input", (event) => {
      const el = event.target;
      if (el.type === "range" && el.dataset.opt === "size") { st.opts.size = +el.value; $("#mx-size-out").textContent = `${el.value} pt`; schedulePreview(300); }
      if (el.dataset.opt === "header" || el.dataset.opt === "footer") st.opts[el.dataset.opt] = el.value;
    });
    $("#mx-cancel").onclick = () => closePanel();
    $("#mx-copy").onclick = async () => { await copyText(textExport(st.opts.format)); };
    $("#mx-go").onclick = () => runExport();
    root.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); runExport(); }
    });
    window.addEventListener("resize", onResize);
  }
  const onResize = () => { if (document.querySelector(".mx-export")) schedulePreview(200); else window.removeEventListener("resize", onResize); };

  /* ------------------------------------------------------------------ export */
  function filename() { return (st.chat?.title || t("Чат MemuAI", "MemuAI chat")).replace(/\s+/g, " ").trim().slice(0, 90); }
  function serverOptions(rows) {
    const o = st.opts;
    const p = palette();
    return { title: st.chat?.title || "MemuAI", lang: PG_LANG, page: o.page, orientation: o.orientation, margins: o.margins,
      margin_mm: MARGINS[o.margins] || 20, font: fontOf().docx || "Segoe UI", mono: monoOf().docx, size: o.size,
      palette: docxPalette(p), images: o.images, math: o.math, page_numbers: o.numbers,
      header_text: o.header.trim(), footer_text: o.footer.trim(), viewport: 820, rows: rows.length };
  }

  async function runExport() {
    if (st.busy || !chosenRows().length) return;
    st.busy = true;
    const go = $("#mx-go"); const label = $("#mx-go-label");
    const original = label.textContent;
    go.disabled = true; go.classList.add("busy"); label.textContent = t("Готовлю…", "Preparing…");
    saveOptions();
    const fmt = st.opts.format;
    try {
      let payload;
      if (fmt === "md" || fmt === "json" || fmt === "txt") {
        payload = { format: fmt, text: textExport(fmt) };
      } else if (fmt === "html") {
        const { html } = await buildDocument("html");
        payload = { format: "html", html };
      } else if (fmt === "pdf") {
        const { html, rows } = await buildDocument("pdf");
        const hf = headerFooter();
        payload = { format: "pdf", html, options: { ...serverOptions(rows), header_html: hf.header, footer_html: hf.footer } };
        if (st.caps && st.caps.pdf !== "edge") return printFallback();
      } else {
        const built = await buildDocument("docx");
        const raster = /data-mx-raster=/.test(built.body);
        payload = { format: "docx", body: built.body, html: raster ? built.html : "", raster, options: serverOptions(built.rows) };
      }
      payload.filename = filename();
      payload.save = st.opts.save;
      label.textContent = fmt === "pdf" ? t("Печатаю PDF…", "Printing PDF…") : fmt === "docx" ? t("Собираю документ…", "Building the document…") : t("Сохраняю…", "Saving…");
      let saved;
      try { saved = await api("/api/export/render", { method: "POST", body: payload }); }
      catch (e) {
        if (fmt === "pdf" && e.payload?.fallback === "print") { toast(t("PDF через Edge не получился — открываю окно печати", "PDF through Edge failed — opening the print dialog"), "warn"); return printFallback(); }
        throw e;
      }
      if (saved?.cancelled) return;
      closePanel();
      const path = saved.path || "";
      toast((st.opts.save === "ask" ? t("Сохранено: ", "Saved: ") : t("Сохранено в «Загрузки/MemuAI»: ", "Saved to Downloads/MemuAI: ")) + (saved.name || ""), "ok", path ? {
        label: t("Открыть", "Open"),
        run: () => api("/api/export/open", { method: "POST", body: { path } }).catch(() => (typeof revealDownload === "function" ? revealDownload(path) : null)),
      } : null);
    } catch (e) {
      toast(e.message || String(e), "err");
    } finally {
      st.busy = false;
      if (document.body.contains(go)) { go.disabled = false; go.classList.remove("busy"); label.textContent = original; }
    }
  }

  /* No Edge: the window's own print dialog (WebView2 «Save as PDF»). */
  async function printFallback() {
    const { html } = await buildDocument("print");
    const frame = document.createElement("iframe");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
    frame.srcdoc = html.replace("/*MX-KATEX-CSS*/", await katexCss());
    document.body.appendChild(frame);
    await new Promise((resolve) => { frame.onload = resolve; setTimeout(resolve, 4000); });
    try { await frame.contentDocument?.fonts?.ready; } catch (e) {}
    closePanel();
    try { frame.contentWindow.focus(); frame.contentWindow.print(); }
    catch (e) { toast(e.message || String(e), "err"); }
    setTimeout(() => frame.remove(), 60000);
  }

  /* ------------------------------------------------------------------ open */
  async function open(chat) {
    const target = chat && typeof chat === "object" ? chat : (typeof currentChat === "function" ? currentChat() : null) || { title: "", messages: S.messages };
    const rows = sourceRows(target);
    if (!rows.length) return toast(t("Экспортировать пока нечего", "There is nothing to export yet"), "warn");
    st = { chat: target, rows, opts: loadOptions(), selected: new Set(rows.map((r) => r.index)), from: 1, to: rows.length,
      previewTimer: 0, caps: null, busy: false };
    panel(dialogHtml(), "mx-export-panel");
    bindDialog();
    syncDialog();
    schedulePreview(30);
    try { st.caps = await api("/api/export/capabilities"); syncDialog(); } catch (e) { st.caps = { pdf: "print" }; syncDialog(); }
  }

  /* Export straight from a message's context menu: everything up to / starting from it. */
  function openFrom(chat, index, direction = "from") {
    open(chat).then(() => {
      if (!st) return;
      const pos = st.rows.findIndex((r) => r.index === index) + 1;
      if (pos <= 0) return;
      st.opts.range = "from";
      if (direction === "from") { st.from = pos; st.to = st.rows.length; } else { st.from = 1; st.to = pos; }
      const from = $("#mx-from"), to = $("#mx-to");
      if (from) from.value = st.from; if (to) to.value = st.to;
      document.querySelectorAll("input[name=mx-range]").forEach((r) => { r.checked = r.value === "from"; });
      syncDialog(); schedulePreview(0);
    });
  }

  window.MemuExport = Object.freeze({ open, openFrom, THEMES, FORMATS, _textExport: () => (st ? textExport(st.opts.format) : "") });
})();
