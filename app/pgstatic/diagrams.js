/* MemuAI 3.1 · Round 29 · Diagrams (window.MemuDiagrams).
 *
 * Unlike the sandboxed «memuai-visual» fragments (visuals.js: free HTML+JS written by the model), diagrams
 * are DECLARATIVE: the model writes data, MemuAI draws it.
 *   ```memuai-chart  — a JSON spec: line, bar, hbar, area, pie, donut, scatter (bubble), radar, combo;
 *                      grouped or stacked, smooth lines, units, axis titles. Drawn by the own SVG renderer
 *                      below (no library, synchronous, exportable).
 *   ```mermaid       — schemes: flowchart, sequence, mind map, ER, gantt, timeline, class, state, journey…
 *                      rendered by Mermaid 11 (vendored in /vendor/mermaid, loaded lazily on first use,
 *                      securityLevel "strict", SVG labels so export to PNG works).
 * Every diagram: customize the look (theme, palette, font, background, legend, grid, labels, size, type),
 * export PNG 1x–4x / SVG / CSV / source, copy as image, fullscreen, show the code. An invalid spec shows a
 * friendly error with the code and «Fix with AI». While an answer streams, an unfinished block is a quiet
 * placeholder. The prompt (prompt()) follows the «diagrams_mode» preference: auto / ask / off.
 * Needs tables.js (window.MemuTables.ui: dialogs, menus, saving) loaded first.
 */
"use strict";
(function () {
  const VERSION = "1.0-r29";
  const MERMAID_URL = "/vendor/mermaid/mermaid.min.js?v=11.17.2";
  const MAX_SOURCE = 60000;
  const isEn = () => (typeof PG_LANG !== "undefined" ? PG_LANG : document.documentElement.lang) === "en";
  const t = (ru, en) => (isEn() ? en : ru);
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const UI = () => (window.MemuTables && window.MemuTables.ui) || null;
  const prefGet = (key, def) => { try { return typeof pref === "function" ? pref(key, def) : def; } catch (e) { return def; } };

  /* ---------------------------------------------------------------- the prompt */
  const PROMPT_CORE = `<diagrams>
MemuAI draws two kinds of fenced blocks as real graphics in the chat (the user sees the picture, not the code):
- \`\`\`memuai-chart — a JSON chart: line, bar, hbar, area, pie, donut, scatter, radar or combo.
- \`\`\`mermaid — a Mermaid 11 scheme: flowchart, sequenceDiagram, mindmap, erDiagram, gantt, timeline,
  classDiagram, stateDiagram-v2, journey, quadrantChart.
WHEN — this matters more than anything else here. Draw one ONLY if:
  (a) the user explicitly asks for a chart, graph, plot, diagram, scheme, flowchart, mind map, timeline, gantt,
      ER/class/sequence diagram or "show it visually"; or
  (b) {AUTO}
NEVER draw one for greetings, small talk, short or factual answers, a single number, simple lists or steps,
opinions, advice, code questions, translations, or when a sentence or a small table says it just as well.
When in doubt, do not draw. At most one per answer unless the user asks for more. Never invent data to
justify a chart; illustrative data only when the user asks for an example, and say that it is illustrative.
Prefer these blocks over memuai-visual for static charts and schemes (memuai-visual is only for interactive
things: sliders, simulations, calculators).
Chart JSON (compact, valid JSON, no comments, numbers as numbers):
{"type":"bar","title":"Revenue by quarter","labels":["Q1","Q2","Q3","Q4"],
 "series":[{"name":"2024","data":[12,15,11,19]},{"name":"2025","data":[14,18,16,22]}],
 "y":{"title":"Revenue","unit":"M$"},"x":{"title":"Quarter"}}
Options: "stacked":true (bar/area), "smooth":true (line/area), "horizontal":true, "subtitle", "source",
"y":{"min","max"}; pie/donut use one series (labels = slices); scatter uses "data":[[x,y],…] or
[{"x":…,"y":…,"r":…,"label":…}] per series with "x":{"title"}; combo sets "type":"bar"|"line" per series.
Mermaid: valid syntax, short ASCII node ids, labels with spaces or punctuation in quotes (A["Label (x)"]),
under ~40 nodes, no click/href/callbacks, no %%{init}%% styling (MemuAI themes it).
Around the block write a short normal explanation with the key takeaway; do not repeat the same data as a
table unless asked, and never mention "the code block" or "the JSON".
</diagrams>`;
  const AUTO_RULE = "the answer contains real numeric series (about 4+ points, or several series to compare) that a chart\n      shows clearly better than words, or a process/structure with many branches that is hard to follow as text.";
  const ASK_RULE = "otherwise do NOT draw. If a chart or scheme would clearly help, finish with one short offer\n      (\"Show this as a chart?\") and draw it only after the user agrees.";
  function mode() {
    const m = String(prefGet("diagrams_mode", "auto") || "auto");
    return ["auto", "ask", "off"].includes(m) ? m : "auto";
  }
  function prompt(forceMode) {
    const m = forceMode || mode();
    if (m === "off") return "";
    return PROMPT_CORE.replace("{AUTO}", m === "ask" ? ASK_RULE : AUTO_RULE);
  }

  /* ---------------------------------------------------------------- theme, palettes, looks */
  function resolveColor(value, fallback) {
    try {
      const ctx = resolveColor.ctx || (resolveColor.ctx = document.createElement("canvas").getContext("2d"));
      ctx.fillStyle = "#010203"; ctx.fillStyle = String(value || "").trim() || fallback;
      return ctx.fillStyle === "#010203" && value !== "#010203" ? fallback : ctx.fillStyle;
    } catch (e) { return fallback; }
  }
  // Round 36 (perf): getComputedStyle(<html>) after any DOM change forces a style pass of the whole chat; a chat
  // with a dozen schemes asked for it once per scheme. The answer is kept until the current task ends.
  let themeMemo = null;
  function appTheme() {
    if (themeMemo) return themeMemo;
    themeMemo = readAppTheme();
    Promise.resolve().then(() => { themeMemo = null; });
    return themeMemo;
  }
  function readAppTheme() {
    const style = getComputedStyle(document.documentElement);
    const read = (name, fallback) => (style.getPropertyValue(name) || "").trim() || fallback;
    const bg = resolveColor(read("--bg", "#ffffff"), "#ffffff");
    const rgb = (bg.match(/[0-9a-f]{2}/gi) || ["ff", "ff", "ff"]).slice(0, 3).map((h) => parseInt(h, 16));
    const dark = bg.startsWith("#") ? (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) < 128 : document.documentElement.dataset.theme === "dark";
    return {
      dark, bg, panel: resolveColor(read("--panel", dark ? "#22252a" : "#ffffff"), dark ? "#22252a" : "#ffffff"),
      text: resolveColor(read("--text", dark ? "#f0f1f3" : "#24282e"), dark ? "#f0f1f3" : "#24282e"),
      muted: resolveColor(read("--muted", dark ? "#aeb4be" : "#606773"), dark ? "#aeb4be" : "#606773"),
      accent: resolveColor(read("--accent", dark ? "#efa58b" : "#a84b30"), dark ? "#efa58b" : "#a84b30"),
      border: resolveColor(read("--border", dark ? "#3c424b" : "#d9dadd"), dark ? "#3c424b" : "#d9dadd"),
      font: read("--sans", '"Segoe UI", system-ui, sans-serif'),
    };
  }
  const PALETTES = {
    memu: { ru: "MemuAI", en: "MemuAI", colors: ["@accent", "#4f86f7", "#22a06b", "#f2a33a", "#8b5cf6", "#e5534b", "#14b8a6", "#ec4899", "#64748b"] },
    vivid: { ru: "Яркая", en: "Vivid", colors: ["#2563eb", "#f97316", "#10b981", "#e11d48", "#8b5cf6", "#eab308", "#06b6d4", "#84cc16"] },
    pastel: { ru: "Пастель", en: "Pastel", colors: ["#8ab4f8", "#f6a6a6", "#86d9a8", "#f7cf6b", "#bfa8f5", "#f4a6cf", "#7fd6e3", "#c2e07f"] },
    ocean: { ru: "Океан", en: "Ocean", colors: ["#0ea5e9", "#0369a1", "#14b8a6", "#6366f1", "#38bdf8", "#0f766e", "#818cf8", "#7dd3fc"] },
    sunset: { ru: "Закат", en: "Sunset", colors: ["#f97316", "#ef4444", "#ec4899", "#f59e0b", "#a855f7", "#fb7185", "#fdba74", "#c026d3"] },
    forest: { ru: "Лес", en: "Forest", colors: ["#15803d", "#65a30d", "#a16207", "#0f766e", "#4d7c0f", "#b45309", "#166534", "#78716c"] },
    safe: { ru: "Для дальтоников", en: "Colorblind-safe", colors: ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#7f7f7f"] },
    mono: { ru: "Монохром", en: "Monochrome", colors: ["@accent", "@accent-75", "@accent-55", "@accent-38", "@accent-24", "@accent-14"] },
    gray: { ru: "Графит", en: "Graphite", colors: ["@text", "#6b7280", "#9ca3af", "#4b5563", "#d1d5db", "#374151"] },
    neon: { ru: "Неон", en: "Neon", colors: ["#22d3ee", "#f472b6", "#a3e635", "#facc15", "#fb7185", "#818cf8", "#34d399"] },
  };
  const LOOKS = {
    auto: { ru: "Как в приложении", en: "Match the app" },
    light: { ru: "Светлая", en: "Light", v: { bg: "#ffffff", text: "#1f2328", muted: "#6b7280", grid: "#e5e7eb", axis: "#9ca3af" } },
    dark: { ru: "Тёмная", en: "Dark", v: { bg: "#16181d", text: "#e6e8eb", muted: "#9aa1ad", grid: "#2b3038", axis: "#4b5260" } },
    paper: { ru: "Бумага", en: "Paper", v: { bg: "#fbf8f1", text: "#3b3428", muted: "#7c6f5a", grid: "#e8dfcc", axis: "#b9ab8f", font: "serif" } },
    midnight: { ru: "Полночь", en: "Midnight", v: { bg: "#0f172a", bg2: "#312e81", text: "#e5e7eb", muted: "#a5b4fc", grid: "#334155", axis: "#475569" } },
    glass: { ru: "Стекло", en: "Glass", v: { bg: "#8b5cf6", bg2: "#06b6d4", text: "#ffffff", muted: "#e0e7ff", grid: "rgba(255,255,255,.22)", axis: "rgba(255,255,255,.45)", palette: "pastel" } },
    terminal: { ru: "Терминал", en: "Terminal", v: { bg: "#0b0f0c", text: "#7ee787", muted: "#3fb950", grid: "#1f3b26", axis: "#2e5a3a", font: "mono", palette: "neon" } },
    print: { ru: "Для печати", en: "Print", v: { bg: "#ffffff", text: "#000000", muted: "#333333", grid: "#cccccc", axis: "#000000", palette: "gray" } },
  };
  const FONTS = [
    ["app", "App", ""],
    ["sans", "Sans", '"Segoe UI", system-ui, Roboto, Arial, sans-serif'],
    ["serif", "Serif", 'Georgia, "Times New Roman", serif'],
    ["mono", "Mono", '"Cascadia Mono", Consolas, "Courier New", monospace'],
    ["condensed", "Condensed", 'Bahnschrift, "Arial Narrow", sans-serif'],
    ["rounded", "Rounded", '"Nunito", "Segoe UI", "Arial Rounded MT Bold", sans-serif'],
    ["hand", "Handwritten", '"Segoe Print", "Comic Sans MS", cursive'],
  ];
  const DEFAULT_STYLE = {
    look: "auto", palette: "memu", font: "app", fontSize: 12, background: "theme", bg1: "#ffffff", bg2: "#eef2ff", gradAngle: 135,
    legend: "auto", grid: true, labels: false, points: true, smooth: null, stacked: null, type: "", width: 0, height: 0,
    lineWidth: 2.2, barRadius: 4, title: "", showTitle: true, radius: 12,
    // mermaid
    mTheme: "auto", mLook: "classic", mCurve: "basis", accent: "",
  };
  const STYLE_KEY = "memu_diagram_style", STYLES_KEY = "memu_diagram_styles";
  function defaultStyle() {
    try { const s = JSON.parse(localStorage.getItem(STYLE_KEY) || "null"); if (s && typeof s === "object") return { ...DEFAULT_STYLE, ...s, title: "", type: "" }; } catch (e) { /* default */ }
    return { ...DEFAULT_STYLE };
  }
  function savedStyles() { try { return JSON.parse(localStorage.getItem(STYLES_KEY) || "{}") || {}; } catch (e) { return {}; } }
  function styleFor(key) { return { ...defaultStyle(), ...(savedStyles()[key] || {}) }; }
  function rememberStyle(key, style) {
    try {
      const all = savedStyles();
      delete all[key];
      all[key] = style;
      const keys = Object.keys(all);
      while (keys.length > 200) delete all[keys.shift()];
      localStorage.setItem(STYLES_KEY, JSON.stringify(all));
    } catch (e) { /* quota */ }
  }
  function mix(hex, amount, base) {
    const p = (c) => { const m = String(c).match(/^#?([0-9a-f]{6})$/i); if (!m) return null; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
    const a = p(hex), b = p(base);
    if (!a || !b) return hex;
    return "#" + a.map((v, i) => Math.round(v * amount + b[i] * (1 - amount)).toString(16).padStart(2, "0")).join("");
  }
  function themeFor(style, forced) {
    const app = appTheme();
    let lookId = style.look || "auto";
    if (forced === "dark" && lookId === "auto") lookId = "dark";
    if (forced === "light" && lookId === "auto") lookId = "light";
    const look = (LOOKS[lookId] || LOOKS.auto).v;
    const base = look ? { ...look } : { bg: app.dark ? app.panel : app.panel, text: app.text, muted: app.muted, grid: mix(app.border, 0.75, app.panel), axis: app.border };
    const font = style.font && style.font !== "app" ? (FONTS.find((f) => f[0] === style.font) || FONTS[0])[2]
      : look?.font ? (FONTS.find((f) => f[0] === look.font) || FONTS[1])[2] : app.font;
    const paletteId = (style.palette === "memu" && look?.palette) ? look.palette : style.palette;
    const accent = style.accent || app.accent;
    const pal = (PALETTES[paletteId] || PALETTES.memu).colors.map((c) => {
      if (c === "@accent") return resolveColor(accent, "#4f46e5");
      if (c === "@text") return base.text;
      const m = c.match(/^@accent-(\d+)$/);
      if (m) return mix(resolveColor(accent, "#4f46e5"), +m[1] / 100, resolveColor(base.bg, "#ffffff"));
      return c;
    });
    let background = "none";
    if (style.background === "theme") background = look ? (look.bg2 ? { gradient: [look.bg, look.bg2, 135] } : look.bg) : "none";
    else if (style.background === "solid") background = style.bg1;
    else if (style.background === "gradient") background = { gradient: [style.bg1, style.bg2, +style.gradAngle || 135] };
    return { ...base, font, palette: pal, background, exportBgSolid: resolveColor(look ? look.bg : app.panel, "#ffffff"), exportBg: look ? (look.bg2 ? { gradient: [look.bg, look.bg2, 135] } : look.bg) : app.panel, dark: look ? isDarkColor(look.bg) : app.dark, accent };
  }
  function isDarkColor(c) {
    const m = String(resolveColor(c, "#ffffff")).match(/^#([0-9a-f]{6})$/i);
    if (!m) return false;
    const n = parseInt(m[1], 16);
    return (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) < 128;
  }

  /* ---------------------------------------------------------------- text measure (canvas, or estimate) */
  let mctx = null;
  function measure(text, size, family, weight = 400) {
    try {
      if (!mctx) mctx = document.createElement("canvas").getContext("2d");
      mctx.font = `${weight} ${size}px ${family}`;
      return mctx.measureText(String(text)).width;
    } catch (e) { return String(text).length * size * 0.56; }
  }

  /* ---------------------------------------------------------------- chart spec */
  const TYPE_ALIASES = { column: "bar", columns: "bar", bars: "bar", barh: "hbar", horizontalbar: "hbar", "horizontal-bar": "hbar", horizontal_bar: "hbar",
    doughnut: "donut", ring: "donut", lines: "line", spline: "line", "stacked-bar": "bar", stackedbar: "bar", stacked_bar: "bar",
    "stacked-area": "area", stackedarea: "area", bubble: "scatter", points: "scatter", spider: "radar", polar: "radar", mixed: "combo", histogram: "bar", pie: "pie" };
  const TYPES = ["line", "bar", "hbar", "area", "pie", "donut", "scatter", "radar", "combo"];
  class SpecError extends Error {}
  // Colors from the model's spec end up in SVG attributes: only plain color syntax (no url(), no expressions).
  const safeColor = (c) => (typeof c === "string" && /^(#[0-9a-f]{3,8}|(?:rgb|hsl)a?\([\d\s.,%+-]+\)|[a-z]{3,24})$/i.test(c.trim()) ? c.trim() : "");
  const toNum = (v) => {
    if (v === null || v === undefined || v === "") return null;
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    const n = window.MemuTables?.parseNumber ? window.MemuTables.parseNumber(v) : Number(String(v).replace(",", "."));
    return n === null || !Number.isFinite(n) ? null : n;
  };
  function parseSpec(source) {
    let raw;
    const text = String(source || "").trim();
    if (!text) throw new SpecError(t("Пустой блок графика", "The chart block is empty"));
    try { raw = JSON.parse(text); }
    catch (e) {
      // forgiving: trailing commas, single quotes, // comments
      try { raw = JSON.parse(text.replace(/\/\/[^\n"]*$/gm, "").replace(/,\s*([}\]])/g, "$1").replace(/'([^'"\n]*)'/g, '"$1"')); }
      catch (e2) { throw new SpecError(t("Ошибка в JSON графика: ", "Invalid chart JSON: ") + String(e.message || e).replace(/^JSON\.parse: /, "")); }
    }
    return normalizeSpec(raw);
  }
  function normalizeSpec(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new SpecError(t("Ожидался JSON-объект графика", "A chart JSON object was expected"));
    const typeRaw = String(raw.type || raw.kind || raw.chart || "").toLowerCase().replace(/\s+/g, "");
    let type = TYPE_ALIASES[typeRaw] || typeRaw || "";
    const stackedHint = /stack/.test(typeRaw);
    let labels = raw.labels || raw.categories || raw.x?.labels || raw.xLabels || raw.x?.categories || null;
    let series = raw.series || raw.datasets || null;
    // {"data": {"columns": [...], "rows": [[...]]}} or a table-like "data"
    const table = raw.data && !Array.isArray(raw.data) && (raw.data.columns || raw.data.headers) ? raw.data : raw.table;
    if (!series && table && Array.isArray(table.rows)) {
      const cols = table.columns || table.headers || [];
      labels = table.rows.map((r) => String(r[0]));
      series = cols.slice(1).map((name, i) => ({ name, data: table.rows.map((r) => r[i + 1]) }));
    }
    // [{"label": "A", "value": 3}, …] (pie style) or a bare list of numbers
    if (!series && Array.isArray(raw.data)) {
      if (raw.data.length && typeof raw.data[0] === "object" && !Array.isArray(raw.data[0]) && ("value" in raw.data[0] || "y" in raw.data[0]) && !("x" in raw.data[0] && type === "scatter")) {
        labels = raw.data.map((d) => String(d.label ?? d.name ?? d.x ?? ""));
        series = [{ name: raw.name || raw.title || "", data: raw.data.map((d) => d.value ?? d.y), colors: raw.data.map((d) => d.color) }];
      } else series = [{ name: raw.name || "", data: raw.data }];
    }
    if (!series && raw.values) series = [{ name: raw.name || "", data: raw.values }];
    if (!Array.isArray(series) || !series.length) throw new SpecError(t("В графике нет данных (series)", "The chart has no data (series)"));
    if (!type) type = series.some((s) => s && (s.type === "line")) && series.some((s) => s && s.type === "bar") ? "combo" : "bar";
    if (!TYPES.includes(type)) throw new SpecError(t(`Неизвестный тип графика «${raw.type}»`, `Unknown chart type "${raw.type}"`));
    if (raw.horizontal && type === "bar") type = "hbar";
    const out = {
      type, title: String(raw.title || ""), subtitle: String(raw.subtitle || raw.description || ""), source: String(raw.source || raw.caption || ""),
      stacked: !!(raw.stacked || raw.stack || stackedHint), smooth: raw.smooth === undefined ? null : !!raw.smooth,
      x: { title: String(raw.x?.title || raw.xTitle || raw.x_label || raw.xLabel || "") , unit: String(raw.x?.unit || "") },
      y: { title: String(raw.y?.title || raw.yTitle || raw.y_label || raw.yLabel || ""), unit: String(raw.y?.unit || raw.unit || ""),
        min: toNum(raw.y?.min), max: toNum(raw.y?.max), log: !!raw.y?.log },
      legend: raw.legend === false ? "none" : typeof raw.legend === "string" ? raw.legend : "",
      labels: [], series: [], points: false,
    };
    if (type === "scatter") {
      out.points = true;
      out.series = series.map((s, i) => {
        const pts = (Array.isArray(s?.data) ? s.data : Array.isArray(s?.points) ? s.points : []).map((p) => Array.isArray(p)
          ? { x: toNum(p[0]), y: toNum(p[1]), r: toNum(p[2]), label: p[3] !== undefined ? String(p[3]) : "" }
          : { x: toNum(p?.x), y: toNum(p?.y), r: toNum(p?.r ?? p?.size), label: String(p?.label ?? p?.name ?? "") }).filter((p) => p.x !== null && p.y !== null);
        return { name: String(s?.name ?? s?.label ?? (t("Ряд ", "Series ") + (i + 1))), points: pts, color: safeColor(s?.color) };
      }).filter((s) => s.points.length);
      if (!out.series.length) throw new SpecError(t("У точечного графика нет точек [x, y]", "The scatter chart has no [x, y] points"));
      return out;
    }
    const maxLen = Math.max(0, ...series.map((s) => (Array.isArray(s?.data) ? s.data.length : Array.isArray(s?.values) ? s.values.length : 0)));
    if (!maxLen) throw new SpecError(t("В рядах графика нет чисел", "The chart series have no numbers"));
    out.labels = Array.from({ length: maxLen }, (_, i) => (Array.isArray(labels) && labels[i] !== undefined ? String(labels[i]) : String(i + 1)));
    out.series = series.map((s, i) => {
      const data = (Array.isArray(s) ? s : Array.isArray(s?.data) ? s.data : s?.values || []).map((v) => toNum(typeof v === "object" && v ? (v.y ?? v.value) : v));
      while (data.length < maxLen) data.push(null);
      return { name: String(s?.name ?? s?.label ?? (series.length > 1 ? t("Ряд ", "Series ") + (i + 1) : "")), data, color: safeColor(s?.color), colors: Array.isArray(s?.colors) ? s.colors.map(safeColor) : null,
        type: s?.type === "line" || s?.type === "area" ? s.type : s?.type === "bar" ? "bar" : "" };
    });
    if (!out.series.some((s) => s.data.some((v) => v !== null))) throw new SpecError(t("В рядах графика нет чисел", "The chart series have no numbers"));
    if ((type === "pie" || type === "donut") && out.series[0].data.some((v) => v !== null && v < 0)) throw new SpecError(t("Круговая диаграмма не показывает отрицательные значения", "A pie chart cannot show negative values"));
    if (out.series.length > 24) out.series.length = 24;
    if (out.labels.length > 400) { out.labels.length = 400; out.series.forEach((s) => (s.data.length = 400)); }
    return out;
  }

  /* ---------------------------------------------------------------- number formatting and ticks */
  function fmtNum(v, unit = "", compact = false) {
    if (v === null || v === undefined || !Number.isFinite(v)) return "—";
    const lang = isEn() ? "en-US" : "ru-RU";
    let s;
    try {
      s = compact && Math.abs(v) >= 10000 ? new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(v)
        : new Intl.NumberFormat(lang, { maximumFractionDigits: Math.abs(v) < 1 && v !== 0 ? 3 : Math.abs(v) < 100 ? 2 : 1 }).format(v);
    } catch (e) { s = String(Math.round(v * 100) / 100); }
    if (!unit) return s;
    if (/^[$€£¥]$/.test(unit)) return (v < 0 ? "-" + unit + s.replace(/^[-−]/, "") : unit + s);
    return unit === "%" ? s + "%" : s + "\u202f" + unit;
  }
  function niceTicks(min, max, count = 5) {
    if (!Number.isFinite(min) || !Number.isFinite(max)) return { ticks: [0, 1], min: 0, max: 1 };
    if (min === max) { const d = Math.abs(min) || 1; min -= d * 0.5; max += d * 0.5; }
    const span = max - min;
    const step0 = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const err = step0 / mag;
    const step = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * mag;
    const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step * 0.5; v += step) ticks.push(Math.abs(v) < step * 1e-9 ? 0 : +v.toPrecision(12));
    return { ticks, min: lo, max: hi };
  }

  /* ---------------------------------------------------------------- SVG chart renderer */
  function monotonePath(pts) {
    if (pts.length < 3) return pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const n = pts.length, dx = [], dy = [], m = [], tg = [];
    for (let i = 0; i < n - 1; i += 1) { dx[i] = pts[i + 1][0] - pts[i][0]; dy[i] = pts[i + 1][1] - pts[i][1]; m[i] = dx[i] ? dy[i] / dx[i] : 0; }
    tg[0] = m[0]; tg[n - 1] = m[n - 2];
    for (let i = 1; i < n - 1; i += 1) tg[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
    for (let i = 0; i < n - 1; i += 1) {
      if (!m[i]) { tg[i] = tg[i + 1] = 0; continue; }
      const a = tg[i] / m[i], b = tg[i + 1] / m[i], h = a * a + b * b;
      if (h > 9) { const k = 3 / Math.sqrt(h); tg[i] = k * a * m[i]; tg[i + 1] = k * b * m[i]; }
    }
    let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < n - 1; i += 1) {
      const h = dx[i] / 3;
      d += ` C${(pts[i][0] + h).toFixed(1)} ${(pts[i][1] + tg[i] * h).toFixed(1)} ${(pts[i + 1][0] - h).toFixed(1)} ${(pts[i + 1][1] - tg[i + 1] * h).toFixed(1)} ${pts[i + 1][0].toFixed(1)} ${pts[i + 1][1].toFixed(1)}`;
    }
    return d;
  }
  function barPath(x, y, w, h, r, up = true) {
    r = Math.max(0, Math.min(r, w / 2, Math.abs(h)));
    if (!r || h === 0) return `M${x} ${y}h${w}v${h}h${-w}z`;
    if (up) return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
    return `M${x} ${y}V${y + h - r}Q${x} ${y + h} ${x + r} ${y + h}H${x + w - r}Q${x + w} ${y + h} ${x + w} ${y + h - r}V${y}Z`;
  }
  function hbarPath(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, h / 2, Math.abs(w)));
    if (!r || w === 0) return `M${x} ${y}h${w}v${h}h${-w}z`;
    if (w > 0) return `M${x} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x}Z`;
    return `M${x} ${y}H${x + w + r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w + r} ${y + h}H${x}Z`;
  }
  function bgMarkup(bg, W, H, id, radius) {
    if (!bg || bg === "none") return { defs: "", rect: "" };
    if (typeof bg === "object" && bg.gradient) {
      const [c1, c2, angle] = bg.gradient;
      const a = ((+angle || 0) - 90) * Math.PI / 180;
      const x1 = 50 - Math.cos(a) * 50, y1 = 50 - Math.sin(a) * 50, x2 = 50 + Math.cos(a) * 50, y2 = 50 + Math.sin(a) * 50;
      return { defs: `<linearGradient id="${id}-bg" x1="${x1.toFixed(1)}%" y1="${y1.toFixed(1)}%" x2="${x2.toFixed(1)}%" y2="${y2.toFixed(1)}%"><stop offset="0" stop-color="${esc(c1)}"/><stop offset="1" stop-color="${esc(c2)}"/></linearGradient>`,
        rect: `<rect width="${W}" height="${H}" rx="${radius}" fill="url(#${id}-bg)"/>` };
    }
    return { defs: "", rect: `<rect width="${W}" height="${H}" rx="${radius}" fill="${esc(bg)}"/>` };
  }

  let uid = 0;
  // spec (normalized or raw) + style -> { svg, width, height }
  function renderChart(specIn, styleIn = {}, opts = {}) {
    const spec = specIn && specIn.series && specIn.type && Array.isArray(specIn.labels) ? specIn : normalizeSpec(specIn);
    const style = { ...DEFAULT_STYLE, ...styleIn };
    const th = opts.theme || themeFor(style, opts.forceTheme);
    const id = "mdc" + (++uid);
    let type = style.type && TYPES.includes(style.type) && compatible(spec.type, style.type) ? style.type : spec.type;
    const hidden = opts.hidden || new Set();
    const fs = Math.max(9, Math.min(20, +style.fontSize || 12));
    const fam = th.font;
    const W = Math.max(280, Math.min(2400, +style.width || +opts.width || 720));
    const pieLike = type === "pie" || type === "donut";
    const H = Math.max(200, Math.min(1800, +style.height || +opts.height || (pieLike || type === "radar" ? 380 : type === "hbar" ? Math.max(300, Math.min(900, 70 + spec.labels.length * (spec.series.length > 2 ? 34 : 26))) : 380)));
    const stacked = style.stacked === null || style.stacked === undefined ? spec.stacked : !!style.stacked;
    const smooth = style.smooth === null || style.smooth === undefined ? (spec.smooth ?? (type === "area")) : !!style.smooth;
    const colorOf = (s, i) => s.color || th.palette[i % th.palette.length];
    const title = style.showTitle === false ? "" : (style.title || spec.title);
    const pad = 16;
    const parts = [], defs = [];
    let top = pad;
    if (title) { parts.push(`<text x="${pad}" y="${top + fs * 1.3}" font-size="${(fs * 1.32).toFixed(1)}" font-weight="650" fill="${esc(th.text)}">${esc(title)}</text>`); top += fs * 1.3 + 8; }
    if (spec.subtitle && style.showTitle !== false) { parts.push(`<text x="${pad}" y="${top + fs}" font-size="${fs}" fill="${esc(th.muted)}">${esc(spec.subtitle)}</text>`); top += fs + 8; }
    let bottom = H - pad;
    if (spec.source) { parts.push(`<text x="${pad}" y="${H - pad + 2}" font-size="${(fs * 0.86).toFixed(1)}" fill="${esc(th.muted)}" opacity=".85">${esc(spec.source)}</text>`); bottom -= fs + 6; }
    // legend
    const legendItems = pieLike ? spec.labels.map((l, i) => ({ name: l, color: spec.series[0].colors?.[i] || th.palette[i % th.palette.length], i, value: spec.series[0].data[i] }))
      : spec.series.map((s, i) => ({ name: s.name, color: colorOf(s, i), i }));
    let legendPos = style.legend && style.legend !== "auto" ? style.legend : (spec.legend || "");
    if (!legendPos || legendPos === "auto") legendPos = pieLike ? "right" : legendItems.length > 1 ? "top" : "none";
    if (legendItems.every((l) => !l.name)) legendPos = "none";
    if (pieLike && W < 460 && legendPos === "right") legendPos = "bottom";
    let left = pad, right = W - pad;
    const legend = [];
    const swatch = (it, x, y) => {
      const off = hidden.has(it.i);
      const lw = Math.ceil(measure(it.name, fs, fam));
      legend.push(`<g class="md-leg" data-series="${it.i}" style="cursor:pointer" opacity="${off ? 0.38 : 1}"><title>${esc(t("Нажмите, чтобы скрыть или показать", "Click to hide or show"))}</title><rect x="${x}" y="${y - fs * 0.78}" width="${fs * 0.9}" height="${fs * 0.9}" rx="${fs * 0.22}" fill="${esc(it.color)}"/><text x="${x + fs * 1.3}" y="${y}" font-size="${fs}" fill="${esc(th.text)}"${off ? ' text-decoration="line-through"' : ""}>${esc(it.name)}</text>${pieLike && it.value !== null && it.value !== undefined ? `<text x="${x + fs * 1.3 + lw + 6}" y="${y}" font-size="${fs}" fill="${esc(th.muted)}">${esc(fmtNum(it.value, spec.y.unit, true))}</text>` : ""}</g>`);
    };
    if (legendPos === "top" || legendPos === "bottom") {
      const rows = [[]]; let x = 0;
      const avail = right - left;
      legendItems.forEach((it) => {
        const w = fs * 1.3 + measure(it.name, fs, fam) + (pieLike ? measure(fmtNum(it.value, spec.y.unit, true), fs, fam) + 6 : 0) + 18;
        if (x + w > avail && rows[rows.length - 1].length) { rows.push([]); x = 0; }
        rows[rows.length - 1].push({ it, x }); x += w;
      });
      const lh = fs * 1.6, total = rows.length * lh;
      const y0 = legendPos === "top" ? top + fs : bottom - total + fs;
      rows.forEach((row, r) => row.forEach(({ it, x: xx }) => swatch(it, left + xx, y0 + r * lh)));
      if (legendPos === "top") top += total + 4; else bottom -= total + 4;
    } else if (legendPos === "right") {
      const lw = Math.min(W * 0.38, Math.max(...legendItems.map((it) => fs * 1.3 + measure(it.name, fs, fam) + (pieLike ? measure(fmtNum(it.value, spec.y.unit, true), fs, fam) + 8 : 0))) + 10);
      const lh = fs * 1.7;
      const max = Math.max(1, Math.floor((bottom - top) / lh));
      const items = legendItems.slice(0, max);
      const y0 = top + Math.max(0, ((bottom - top) - items.length * lh) / 2) + fs;
      items.forEach((it, k) => swatch(it, right - lw, y0 + k * lh));
      right -= lw + 12;
    }
    const plot = { left, right, top: top + 6, bottom };
    const marks = [];
    const tip = (txt) => `<title>${esc(txt)}</title>`;
    const label = (x, y, txt, anchor = "middle", color = th.text) => `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" font-size="${(fs * 0.86).toFixed(1)}" fill="${esc(color)}" text-anchor="${anchor}" font-weight="500">${esc(txt)}</text>`;
    const showLabels = !!style.labels;
    const visible = spec.series.map((s, i) => !hidden.has(i));

    if (pieLike) {
      const s = spec.series[0];
      const vals = s.data.map((v, i) => (hidden.has(i) || v === null ? 0 : Math.max(0, v)));
      const total = vals.reduce((a, b) => a + b, 0) || 1;
      const cx = (plot.left + plot.right) / 2, cy = (plot.top + plot.bottom) / 2;
      const R = Math.max(30, Math.min(plot.right - plot.left, plot.bottom - plot.top) / 2 - 6);
      const r0 = type === "donut" ? R * 0.58 : 0;
      let a0 = -Math.PI / 2;
      vals.forEach((v, i) => {
        if (!v) return;
        const a1 = a0 + (v / total) * Math.PI * 2;
        const large = a1 - a0 > Math.PI ? 1 : 0;
        const p = (a, r) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
        const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R);
        const color = s.colors?.[i] || th.palette[i % th.palette.length];
        let d;
        if (v >= total - 1e-9) d = r0 ? `M${cx - R} ${cy}A${R} ${R} 0 1 1 ${cx + R} ${cy}A${R} ${R} 0 1 1 ${cx - R} ${cy}M${cx - r0} ${cy}A${r0} ${r0} 0 1 0 ${cx + r0} ${cy}A${r0} ${r0} 0 1 0 ${cx - r0} ${cy}Z`
          : `M${cx - R} ${cy}A${R} ${R} 0 1 1 ${cx + R} ${cy}A${R} ${R} 0 1 1 ${cx - R} ${cy}Z`;
        else if (r0) { const [x2, y2] = p(a1, r0), [x3, y3] = p(a0, r0); d = `M${x0} ${y0}A${R} ${R} 0 ${large} 1 ${x1} ${y1}L${x2} ${y2}A${r0} ${r0} 0 ${large} 0 ${x3} ${y3}Z`; }
        else d = `M${cx} ${cy}L${x0} ${y0}A${R} ${R} 0 ${large} 1 ${x1} ${y1}Z`;
        const pct = v / total * 100;
        marks.push(`<path class="md-mark" d="${d}" fill="${esc(color)}" stroke="${esc(th.exportBgSolid || "#ffffff")}" stroke-opacity="${th.dark ? 0.25 : 0.9}" stroke-width="1.5">${tip(`${spec.labels[i]}: ${fmtNum(v, spec.y.unit)} (${pct.toFixed(1)}%)`)}</path>`);
        if (pct >= 4 || showLabels) {
          const mid = (a0 + a1) / 2, rr = r0 ? (R + r0) / 2 : R * 0.64;
          const [lx, ly] = p(mid, rr);
          const txt = showLabels ? fmtNum(v, spec.y.unit, true) : (pct >= 10 ? pct.toFixed(0) : pct.toFixed(1)) + "%";
          marks.push(`<text x="${lx.toFixed(1)}" y="${(ly + fs * 0.35).toFixed(1)}" font-size="${(fs * 0.92).toFixed(1)}" font-weight="600" fill="${isDarkColor(color) ? "#ffffff" : "#1f2328"}" text-anchor="middle" pointer-events="none">${esc(txt)}</text>`);
        }
        a0 = a1;
      });
      if (r0) {
        marks.push(`<text x="${cx}" y="${cy - 2}" font-size="${(fs * 1.5).toFixed(1)}" font-weight="700" fill="${esc(th.text)}" text-anchor="middle">${esc(fmtNum(vals.reduce((a, b) => a + b, 0), spec.y.unit, true))}</text>`);
        marks.push(`<text x="${cx}" y="${cy + fs * 1.15}" font-size="${(fs * 0.86).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="middle">${esc(s.name || t("Всего", "Total"))}</text>`);
      }
    } else if (type === "radar") {
      const n = spec.labels.length;
      const cx = (plot.left + plot.right) / 2, cy = (plot.top + plot.bottom) / 2 + 4;
      const labelW = Math.max(...spec.labels.map((l) => measure(l, fs, fam)));
      const R = Math.max(40, Math.min((plot.right - plot.left) / 2 - Math.min(labelW, 140) - 8, (plot.bottom - plot.top) / 2 - fs * 1.8));
      const all = spec.series.filter((_, i) => visible[i]).flatMap((s) => s.data).filter((v) => v !== null);
      const { ticks, max } = niceTicks(Math.min(0, ...all), Math.max(...all, 1), 4);
      const ang = (i) => -Math.PI / 2 + (i / n) * Math.PI * 2;
      const pt = (i, v) => [cx + Math.cos(ang(i)) * R * (v / (max || 1)), cy + Math.sin(ang(i)) * R * (v / (max || 1))];
      if (style.grid !== false) ticks.filter((v) => v > 0).forEach((v) => {
        parts.push(`<polygon points="${spec.labels.map((_, i) => pt(i, v).map((c) => c.toFixed(1)).join(",")).join(" ")}" fill="none" stroke="${esc(th.grid)}" stroke-width="1"/>`);
        parts.push(`<text x="${cx + 4}" y="${(cy - R * v / max - 3).toFixed(1)}" font-size="${(fs * 0.8).toFixed(1)}" fill="${esc(th.muted)}">${esc(fmtNum(v, spec.y.unit, true))}</text>`);
      });
      spec.labels.forEach((l, i) => {
        const [x, y] = pt(i, max);
        parts.push(`<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${esc(th.grid)}" stroke-width="1"/>`);
        const [lx, ly] = [cx + Math.cos(ang(i)) * (R + 10), cy + Math.sin(ang(i)) * (R + 10)];
        const anchor = Math.abs(Math.cos(ang(i))) < 0.2 ? "middle" : Math.cos(ang(i)) > 0 ? "start" : "end";
        parts.push(`<text x="${lx.toFixed(1)}" y="${(ly + fs * 0.35 + (Math.sin(ang(i)) > 0.9 ? fs * 0.5 : 0)).toFixed(1)}" font-size="${fs}" fill="${esc(th.text)}" text-anchor="${anchor}">${esc(l)}</text>`);
      });
      spec.series.forEach((s, si) => {
        if (!visible[si]) return;
        const c = colorOf(s, si);
        const pts = s.data.map((v, i) => pt(i, v ?? 0));
        marks.push(`<polygon class="md-mark" points="${pts.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ")}" fill="${esc(c)}" fill-opacity=".18" stroke="${esc(c)}" stroke-width="${style.lineWidth}" stroke-linejoin="round">${tip(s.name)}</polygon>`);
        if (style.points !== false) pts.forEach((p, i) => marks.push(`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="3" fill="${esc(c)}">${tip(`${s.name ? s.name + " · " : ""}${spec.labels[i]}: ${fmtNum(s.data[i], spec.y.unit)}`)}</circle>`));
        if (showLabels) pts.forEach((p, i) => marks.push(label(p[0], p[1] - 7, fmtNum(s.data[i], spec.y.unit, true))));
      });
    } else {
      // cartesian: line / bar / area / combo / hbar / scatter
      const horizontal = type === "hbar";
      const scatter = type === "scatter";
      const seriesType = (s) => type === "combo" ? (s.type || "bar") : type === "hbar" ? "bar" : type;
      // value domain
      let vmin = Infinity, vmax = -Infinity, xmin = Infinity, xmax = -Infinity;
      if (scatter) spec.series.forEach((s, i) => visible[i] && s.points.forEach((p) => { vmin = Math.min(vmin, p.y); vmax = Math.max(vmax, p.y); xmin = Math.min(xmin, p.x); xmax = Math.max(xmax, p.x); }));
      else if (stacked) {
        spec.labels.forEach((_, j) => {
          let pos = 0, neg = 0;
          spec.series.forEach((s, i) => { if (!visible[i] || seriesType(s) === "line") return; const v = s.data[j] || 0; if (v >= 0) pos += v; else neg += v; });
          vmax = Math.max(vmax, pos); vmin = Math.min(vmin, neg);
          spec.series.forEach((s, i) => { if (visible[i] && seriesType(s) === "line" && s.data[j] !== null) { vmax = Math.max(vmax, s.data[j]); vmin = Math.min(vmin, s.data[j]); } });
        });
      } else spec.series.forEach((s, i) => visible[i] && s.data.forEach((v) => { if (v !== null) { vmin = Math.min(vmin, v); vmax = Math.max(vmax, v); } }));
      if (!Number.isFinite(vmin)) { vmin = 0; vmax = 1; }
      const hasBars = !scatter && spec.series.some((s, i) => visible[i] && (seriesType(s) === "bar" || seriesType(s) === "area"));
      if (hasBars || (!scatter && vmin > 0 && vmin < (vmax - vmin) * 0.6)) vmin = Math.min(0, vmin);
      if (hasBars) vmax = Math.max(0, vmax);
      if (spec.y.min !== null) vmin = spec.y.min;
      if (spec.y.max !== null) vmax = spec.y.max;
      const vt = niceTicks(vmin, vmax, horizontal ? 5 : Math.max(3, Math.min(7, Math.round((plot.bottom - plot.top) / 55))));
      if (spec.y.min !== null) vt.min = spec.y.min;
      if (spec.y.max !== null) vt.max = spec.y.max;
      vt.ticks = vt.ticks.filter((v) => v >= vt.min - 1e-9 && v <= vt.max + 1e-9);
      const xt = scatter ? niceTicks(xmin, xmax, 6) : null;
      const tickTxt = vt.ticks.map((v) => fmtNum(v, spec.y.unit, true));
      // margins
      const yTitleW = (horizontal ? spec.x.title : spec.y.title) ? fs * 1.5 : 0;
      const xTitleH = (horizontal ? spec.y.title : spec.x.title) ? fs * 1.6 : 0;
      let catLabels = spec.labels;
      if (horizontal) {
        const lw = Math.min((plot.right - plot.left) * 0.38, Math.max(...catLabels.map((l) => measure(l, fs, fam))) + 10);
        plot.left += lw + yTitleW;
        plot.bottom -= fs * 1.6 + xTitleH;
      } else {
        const lw = Math.max(...(scatter ? vt.ticks.map((v) => fmtNum(v, spec.y.unit, true)) : tickTxt).map((s) => measure(s, fs * 0.92, fam))) + 10;
        plot.left += lw + yTitleW;
        // x labels: rotate when crowded
        const band = (plot.right - plot.left) / Math.max(1, catLabels.length);
        const maxLw = scatter ? 0 : Math.max(...catLabels.map((l) => measure(l, fs * 0.92, fam)));
        var rotate = !scatter && maxLw > band * 0.92;
        var every = 1;
        if (rotate) {
          const minStep = fs * 1.25;
          every = Math.max(1, Math.ceil(minStep / band));
        }
        plot.bottom -= (rotate ? Math.min(maxLw, 120) * 0.6 + fs * 1.2 : fs * 1.7) + xTitleH;
      }
      plot.right -= 6;
      const pw = Math.max(20, plot.right - plot.left), ph = Math.max(20, plot.bottom - plot.top);
      const vScale = (v) => (v - vt.min) / ((vt.max - vt.min) || 1);
      const Y = (v) => plot.bottom - vScale(v) * ph;
      const X = (v) => plot.left + vScale(v) * pw;
      // grid + value axis
      vt.ticks.forEach((v, k) => {
        if (horizontal) {
          const x = X(v);
          if (style.grid !== false) parts.push(`<line x1="${x.toFixed(1)}" y1="${plot.top}" x2="${x.toFixed(1)}" y2="${plot.bottom}" stroke="${esc(th.grid)}" stroke-width="1"${v === 0 ? "" : ' stroke-dasharray="3 4"'}/>`);
          parts.push(`<text x="${x.toFixed(1)}" y="${(plot.bottom + fs * 1.3).toFixed(1)}" font-size="${(fs * 0.92).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="middle">${esc(tickTxt[k])}</text>`);
        } else {
          const y = Y(v);
          if (style.grid !== false || v === 0) parts.push(`<line x1="${plot.left}" y1="${y.toFixed(1)}" x2="${plot.right}" y2="${y.toFixed(1)}" stroke="${esc(v === 0 ? th.axis : th.grid)}" stroke-width="1"${v === 0 || !style.grid ? "" : ' stroke-dasharray="3 4"'}/>`);
          parts.push(`<text x="${plot.left - 8}" y="${(y + fs * 0.32).toFixed(1)}" font-size="${(fs * 0.92).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="end">${esc(tickTxt[k])}</text>`);
        }
      });
      // axis titles
      const yTitle = horizontal ? spec.x.title : spec.y.title, xTitle = horizontal ? spec.y.title : spec.x.title;
      if (yTitle) parts.push(`<text transform="translate(${(left + fs * 0.9).toFixed(1)} ${((plot.top + plot.bottom) / 2).toFixed(1)}) rotate(-90)" font-size="${fs}" fill="${esc(th.muted)}" text-anchor="middle">${esc(yTitle)}</text>`);
      if (xTitle) parts.push(`<text x="${((plot.left + plot.right) / 2).toFixed(1)}" y="${(bottom - 2).toFixed(1)}" font-size="${fs}" fill="${esc(th.muted)}" text-anchor="middle">${esc(xTitle)}</text>`);
      if (scatter) {
        const XS = (v) => plot.left + (v - xt.min) / ((xt.max - xt.min) || 1) * pw;
        xt.ticks.forEach((v) => {
          const x = XS(v);
          if (style.grid !== false) parts.push(`<line x1="${x.toFixed(1)}" y1="${plot.top}" x2="${x.toFixed(1)}" y2="${plot.bottom}" stroke="${esc(th.grid)}" stroke-dasharray="3 4"/>`);
          parts.push(`<text x="${x.toFixed(1)}" y="${(plot.bottom + fs * 1.3).toFixed(1)}" font-size="${(fs * 0.92).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="middle">${esc(fmtNum(v, spec.x.unit, true))}</text>`);
        });
        parts.push(`<line x1="${plot.left}" y1="${plot.bottom}" x2="${plot.right}" y2="${plot.bottom}" stroke="${esc(th.axis)}"/>`);
        const rMax = Math.max(0, ...spec.series.flatMap((s) => s.points.map((p) => p.r || 0)));
        spec.series.forEach((s, si) => {
          if (!visible[si]) return;
          const c = colorOf(s, si);
          s.points.forEach((p) => {
            const r = rMax ? 4 + Math.sqrt((p.r || 0) / rMax) * 18 : 4.2;
            marks.push(`<circle class="md-mark" cx="${XS(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="${r.toFixed(1)}" fill="${esc(c)}" fill-opacity="${rMax ? 0.55 : 0.85}" stroke="${esc(c)}" stroke-width="1">${tip(`${p.label ? p.label + " · " : s.name ? s.name + " · " : ""}x ${fmtNum(p.x, spec.x.unit)}, y ${fmtNum(p.y, spec.y.unit)}${p.r ? ", r " + fmtNum(p.r) : ""}`)}</circle>`);
            if (showLabels && p.label) marks.push(label(XS(p.x), Y(p.y) - r - 4, p.label));
          });
        });
      } else {
        const n = catLabels.length;
        const lineOnly = spec.series.every((s, i) => !visible[i] || seriesType(s) === "line" || seriesType(s) === "area") && type !== "combo";
        const band = (horizontal ? ph : pw) / Math.max(1, n);
        const pos = (j) => lineOnly && !horizontal ? (n > 1 ? plot.left + j * (pw / (n - 1)) : plot.left + pw / 2) : (horizontal ? plot.top : plot.left) + band * (j + 0.5);
        // category labels
        catLabels.forEach((l, j) => {
          if (horizontal) {
            const y = pos(j);
            parts.push(`<text x="${plot.left - 8}" y="${(y + fs * 0.33).toFixed(1)}" font-size="${fs}" fill="${esc(th.text)}" text-anchor="end">${esc(l.length > 34 ? l.slice(0, 33) + "…" : l)}</text>`);
          } else {
            if (j % every) return;
            const x = pos(j);
            if (rotate) parts.push(`<text transform="translate(${x.toFixed(1)} ${(plot.bottom + fs * 1.1).toFixed(1)}) rotate(-38)" font-size="${(fs * 0.92).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="end">${esc(l.length > 24 ? l.slice(0, 23) + "…" : l)}</text>`);
            else parts.push(`<text x="${x.toFixed(1)}" y="${(plot.bottom + fs * 1.35).toFixed(1)}" font-size="${(fs * 0.92).toFixed(1)}" fill="${esc(th.muted)}" text-anchor="middle">${esc(l)}</text>`);
          }
        });
        if (horizontal) parts.push(`<line x1="${X(Math.max(vt.min, Math.min(0, vt.max)))}" y1="${plot.top}" x2="${X(Math.max(vt.min, Math.min(0, vt.max)))}" y2="${plot.bottom}" stroke="${esc(th.axis)}"/>`);
        // bars
        const barSeries = spec.series.map((s, i) => ({ s, i })).filter(({ s, i }) => visible[i] && seriesType(s) === "bar");
        if (barSeries.length) {
          const inner = band * (barSeries.length > 1 && !stacked ? 0.78 : 0.62);
          const bw = stacked ? inner : inner / barSeries.length;
          const gap = !stacked && barSeries.length > 1 ? Math.min(3, bw * 0.12) : 0;
          const zero = Math.max(vt.min, Math.min(0, vt.max));
          catLabels.forEach((l, j) => {
            let accPos = 0, accNeg = 0;
            barSeries.forEach(({ s, i }, k) => {
              const v = s.data[j];
              if (v === null) return;
              const c = s.colors?.[j] || colorOf(s, i);
              let from = zero, to = v;
              if (stacked) { if (v >= 0) { from = accPos; accPos += v; to = accPos; } else { from = accNeg; accNeg += v; to = accNeg; } }
              const off = stacked ? -inner / 2 : -inner / 2 + k * bw;
              const tipTxt = `${s.name ? s.name + " · " : ""}${l}: ${fmtNum(v, spec.y.unit)}`;
              const isTop = !stacked || k === barSeries.length - 1 || barSeries.slice(k + 1).every(({ s: s2 }) => s2.data[j] === null || Math.sign(s2.data[j]) !== Math.sign(v));
              const rad = isTop ? +style.barRadius : 0;
              if (horizontal) {
                const x0 = X(Math.max(vt.min, Math.min(vt.max, from))), x1 = X(Math.max(vt.min, Math.min(vt.max, to)));
                const y = pos(j) + off + gap / 2;
                marks.push(`<path class="md-mark" d="${hbarPath(x0, y, x1 - x0, bw - gap, rad)}" fill="${esc(c)}">${tip(tipTxt)}</path>`);
                if (showLabels) marks.push(label(x1 + (v >= 0 ? 5 : -5), y + (bw - gap) / 2 + fs * 0.32, fmtNum(v, spec.y.unit, true), v >= 0 ? "start" : "end", th.muted));
              } else {
                const y0 = Y(Math.max(vt.min, Math.min(vt.max, from))), y1 = Y(Math.max(vt.min, Math.min(vt.max, to)));
                const x = pos(j) + off + gap / 2;
                const up = to >= from;
                marks.push(`<path class="md-mark" d="${barPath(x, Math.min(y0, y1), bw - gap, Math.abs(y1 - y0), rad, up)}" fill="${esc(c)}">${tip(tipTxt)}</path>`);
                if (showLabels && (!stacked || isTop)) marks.push(label(x + (bw - gap) / 2, up ? Math.min(y0, y1) - 5 : Math.max(y0, y1) + fs, fmtNum(stacked ? to : v, spec.y.unit, true), "middle", th.muted));
              }
            });
          });
        }
        // lines and areas
        const accum = catLabels.map(() => 0);
        spec.series.forEach((s, si) => {
          const st = seriesType(s);
          if (!visible[si] || (st !== "line" && st !== "area")) return;
          const c = colorOf(s, si);
          const stackThis = stacked && st === "area";
          const vals = s.data.map((v, j) => (v === null ? null : stackThis ? (accum[j] += v) : v));
          const base = stackThis ? s.data.map((v, j) => (vals[j] === null ? null : vals[j] - v)) : null;
          // split into runs without gaps
          const runs = [[]];
          vals.forEach((v, j) => { if (v === null) { if (runs[runs.length - 1].length) runs.push([]); } else runs[runs.length - 1].push(j); });
          runs.filter((r) => r.length).forEach((run) => {
            const pts = run.map((j) => [pos(j), Y(Math.max(vt.min, Math.min(vt.max, vals[j])))]);
            const d = smooth ? monotonePath(pts) : pts.map((p, k) => (k ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
            if (st === "area") {
              const gid = `${id}-a${si}`;
              defs.push(`<linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${esc(c)}" stop-opacity="${stacked ? 0.55 : 0.32}"/><stop offset="1" stop-color="${esc(c)}" stop-opacity="${stacked ? 0.35 : 0.03}"/></linearGradient>`);
              let floor;
              if (base) {
                const bpts = run.map((j) => [pos(j), Y(Math.max(vt.min, Math.min(vt.max, base[j])))]).reverse();
                floor = (smooth ? monotonePath(bpts) : bpts.map((p, k) => (k ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ")).replace(/^M/, "L");
              } else {
                const zy = Y(Math.max(vt.min, Math.min(0, vt.max)));
                floor = `L${pts[pts.length - 1][0].toFixed(1)} ${zy.toFixed(1)} L${pts[0][0].toFixed(1)} ${zy.toFixed(1)}`;
              }
              marks.push(`<path d="${d} ${floor} Z" fill="url(#${gid})" stroke="none"/>`);
            }
            marks.push(`<path class="md-line" d="${d}" fill="none" stroke="${esc(c)}" stroke-width="${style.lineWidth}" stroke-linejoin="round" stroke-linecap="round">${tip(s.name)}</path>`);
            const showPts = style.points !== false && (run.length <= 40 || run.length === 1);
            run.forEach((j, k) => {
              const [x, y] = pts[k];
              const tipTxt = `${s.name ? s.name + " · " : ""}${catLabels[j]}: ${fmtNum(s.data[j], spec.y.unit)}`;
              marks.push(`<circle class="md-mark md-pt" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${showPts ? 3.4 : 7}" fill="${showPts ? esc(c) : "transparent"}" stroke="${showPts ? esc(th.exportBgSolid || "#ffffff") : "none"}" stroke-width="${showPts ? 1.4 : 0}">${tip(tipTxt)}</circle>`);
              if (showLabels) marks.push(label(x, y - 8, fmtNum(stacked ? vals[j] : s.data[j], spec.y.unit, true), "middle", th.muted));
            });
          });
        });
      }
    }
    const bg = bgMarkup(opts.background !== undefined ? opts.background : th.background, W, H, id, opts.radius ?? (+style.radius || 0));
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" class="md-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="${esc(fam)}" role="img" aria-label="${esc(title || t("График", "Chart"))}">` +
      `<defs>${bg.defs}${defs.join("")}</defs>${bg.rect}<g>${parts.join("")}</g><g>${marks.join("")}</g><g>${legend.join("")}</g></svg>`;
    return { svg, width: W, height: H, spec, type };
  }
  function compatible(from, to) {
    const cat = ["line", "bar", "hbar", "area", "combo", "radar"];
    const one = ["pie", "donut"];
    if (from === to) return true;
    if (from === "scatter" || to === "scatter") return false;
    if (one.includes(to)) return true;            // the first series becomes slices
    if (one.includes(from)) return cat.includes(to);
    return cat.includes(from) && cat.includes(to);
  }
  function specCSV(spec) {
    const T = window.MemuTables;
    let data;
    if (spec.type === "scatter") {
      data = { headers: [t("Ряд", "Series"), spec.x.title || "x", spec.y.title || "y", "r", t("Подпись", "Label")], rows: spec.series.flatMap((s) => s.points.map((p) => [s.name, String(p.x), String(p.y), p.r === null ? "" : String(p.r), p.label || ""])) };
    } else {
      data = { headers: [spec.x.title || t("Категория", "Category"), ...spec.series.map((s, i) => s.name || t("Ряд ", "Series ") + (i + 1))], rows: spec.labels.map((l, j) => [l, ...spec.series.map((s) => (s.data[j] === null ? "" : String(s.data[j])))]) };
    }
    return { data, csv: T ? T.toCSV(data, { bom: true }) : [data.headers, ...data.rows].map((r) => r.map((c) => /[",\n]/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c).join(",")).join("\r\n") };
  }

  /* ---------------------------------------------------------------- mermaid (lazy) */
  let mermaidPromise = null;
  function loadMermaid() {
    if (window.mermaid?.render) return Promise.resolve(window.mermaid);
    if (mermaidPromise) return mermaidPromise;
    mermaidPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = MERMAID_URL; script.async = true;
      script.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error("mermaid")));
      script.onerror = () => { mermaidPromise = null; reject(new Error(t("Не удалось загрузить модуль схем", "Could not load the diagram engine"))); };
      document.head.appendChild(script);
    });
    return mermaidPromise;
  }
  let mermaidQueue = Promise.resolve();
  function mermaidConfig(style, forced) {
    const th = themeFor({ ...style, look: style.look }, forced);
    const app = appTheme();
    const dark = forced ? forced === "dark" : th.dark;
    const theme = style.mTheme && style.mTheme !== "auto" ? style.mTheme : "base";
    const solidBg = typeof th.exportBg === "string" ? resolveColor(th.exportBg, dark ? "#16181d" : "#ffffff") : (dark ? "#16181d" : "#ffffff");
    const accent = resolveColor(style.accent || app.accent, "#4f46e5");
    const vars = theme === "base" ? {
      darkMode: dark, background: solidBg, fontFamily: th.font, fontSize: (+style.fontSize || 12) + 2 + "px",
      primaryColor: mix(accent, dark ? 0.28 : 0.14, solidBg), primaryTextColor: th.text, primaryBorderColor: mix(accent, 0.75, solidBg),
      lineColor: mix(th.text, 0.55, solidBg), secondaryColor: mix(th.palette[1] || accent, dark ? 0.25 : 0.14, solidBg),
      tertiaryColor: mix(th.palette[2] || accent, dark ? 0.22 : 0.12, solidBg), textColor: th.text, mainBkg: mix(accent, dark ? 0.28 : 0.14, solidBg),
      nodeBorder: mix(accent, 0.75, solidBg), clusterBkg: mix(th.text, 0.05, solidBg), clusterBorder: mix(th.text, 0.25, solidBg),
      edgeLabelBackground: solidBg, noteBkgColor: mix("#f2c94c", dark ? 0.25 : 0.3, solidBg), noteTextColor: th.text,
      actorBkg: mix(accent, dark ? 0.28 : 0.14, solidBg), actorBorder: mix(accent, 0.75, solidBg), actorTextColor: th.text, signalColor: th.text, signalTextColor: th.text,
      labelBoxBkgColor: mix(accent, 0.18, solidBg), labelTextColor: th.text, loopTextColor: th.text,
      sectionBkgColor: mix(accent, 0.15, solidBg), altSectionBkgColor: mix(th.text, 0.04, solidBg), taskBkgColor: mix(accent, 0.6, solidBg), taskTextColor: th.text,
      gridColor: mix(th.text, 0.15, solidBg), todayLineColor: "#e5534b",
      pie1: th.palette[0], pie2: th.palette[1], pie3: th.palette[2], pie4: th.palette[3], pie5: th.palette[4], pie6: th.palette[5], pie7: th.palette[6], pie8: th.palette[7],
      cScale0: th.palette[0], cScale1: th.palette[1], cScale2: th.palette[2], cScale3: th.palette[3], cScale4: th.palette[4], cScale5: th.palette[5],
      // mind map / timeline branches past the 6th: without these Mermaid derives near-black fills (unreadable on dark)
      cScale6: th.palette[6 % th.palette.length], cScale7: th.palette[7 % th.palette.length], cScale8: th.palette[8 % th.palette.length],
      cScale9: th.palette[9 % th.palette.length], cScale10: th.palette[10 % th.palette.length], cScale11: th.palette[11 % th.palette.length],
    } : { fontFamily: th.font };
    Object.keys(vars).forEach((k) => vars[k] === undefined && delete vars[k]);
    return {
      startOnLoad: false, securityLevel: "strict", theme, themeVariables: vars, fontFamily: th.font, htmlLabels: false,
      look: style.mLook === "handDrawn" ? "handDrawn" : "classic", deterministicIds: true, maxTextSize: MAX_SOURCE, maxEdges: 1000,
      flowchart: { htmlLabels: false, curve: style.mCurve || "basis", useMaxWidth: false, padding: 12 },
      sequence: { useMaxWidth: false, mirrorActors: false },
      // gantt takes the width of its (detached) parent = the whole window, then gets squeezed into the chat column
      // with 7-px text: draw it at about the column width instead.
      gantt: { useMaxWidth: false, useWidth: 760, fontSize: 12, sectionFontSize: 12, barHeight: 22, barGap: 6, topPadding: 48 }, journey: { useMaxWidth: false },
      class: { htmlLabels: false, useMaxWidth: false }, state: { useMaxWidth: false }, er: { useMaxWidth: false }, mindmap: { useMaxWidth: false },
      timeline: { useMaxWidth: false }, pie: { useMaxWidth: false }, quadrantChart: { useMaxWidth: false },
      suppressErrorRendering: true,
      _bg: th.background, _exportBg: th.exportBg, _dark: dark,
    };
  }
  function sanitizeMermaid(code) {
    // MemuAI themes the scheme itself; init directives could re-enable HTML labels or change the security level.
    return String(code || "").replace(/^\s*%%\{[\s\S]*?\}%%\s*$/gm, "").replace(/^\s*click\s+.*$/gim, "").trim();
  }
  // Models often write flowchart labels with brackets inside, e.g. F[Send code (SMS / app)] — a Mermaid syntax error.
  // When the scheme does not parse, quote such node labels ("…") and try once more.
  const MM_EDGE_AHEAD = String.raw`(?=\s*(?:-->|---|-\.|==>|--\s|--[ox]|<-->|<--|&|:::|;|$))`;
  const MM_SHAPES = [["[", "]", "[\\[(/\\\\\"]"], ["{", "}", "[{\"]"], ["(", ")", "[(\\[\"]"]];
  function repairMermaid(code) {
    const text = String(code || "");
    if (!/^\s*(flowchart|graph)\b/i.test(text.split("\n").find((l) => l.trim() && !l.trim().startsWith("%%")) || "")) return text;
    return text.split("\n").map((line) => {
      if (/^\s*(%%|classDef|class\s|style\s|linkStyle|subgraph|end\s*$|direction)/.test(line)) return line;
      let out = line;
      for (const [open, close, notAfter] of MM_SHAPES) {
        const o = open.replace(/[[\](){}]/g, "\\$&"), c = close.replace(/[[\](){}]/g, "\\$&");
        const rx = new RegExp(`(^|[\\s&>|-])([A-Za-z_][\\w-]*)${o}(?!${notAfter})(.+?)${c}${MM_EDGE_AHEAD}`, "g");
        out = out.replace(rx, (m, pre, id, label) => /[()[\]{}|<>;]/.test(label) && !/^".*"$/.test(label.trim())
          ? `${pre}${id}${open}"${label.replace(/"/g, "#quot;")}"${close}` : m);
      }
      return out;
    }).join("\n");
  }
  async function renderMermaid(code, style, forced) {
    const mermaid = await loadMermaid();
    const cfg = mermaidConfig(style, forced);
    const job = mermaidQueue.then(async () => {
      const { _bg, _exportBg, _dark, ...config } = cfg;
      mermaid.initialize(config);
      let clean = sanitizeMermaid(code);
      try { await mermaid.parse(clean); }
      catch (e) {
        const fixed = repairMermaid(clean);
        let ok = false;
        if (fixed !== clean) { try { await mermaid.parse(fixed); ok = true; } catch (e2) { /* report the original error */ } }
        if (!ok) throw new SpecError(friendlyMermaidError(e));
        clean = fixed;
      }
      const id = "mdm" + (++uid) + "-" + Date.now().toString(36);
      let out;
      try { out = await mermaid.render(id, clean); }
      catch (e) { throw new SpecError(friendlyMermaidError(e)); }
      finally { document.getElementById(id)?.remove(); document.getElementById("d" + id)?.remove(); }
      let svg = String(out.svg || "");
      if (!/^<svg[\s>]/.test(svg.trim())) throw new SpecError(t("Схема не нарисовалась", "The scheme could not be drawn"));
      // explicit size from the viewBox (mermaid writes width="100%" / max-width)
      const vb = svg.match(/viewBox="([-\d.\s]+)"/);
      let w = 600, h = 400;
      if (vb) { const p = vb[1].trim().split(/\s+/).map(Number); w = Math.max(40, Math.ceil(p[2])); h = Math.max(30, Math.ceil(p[3])); }
      svg = svg.replace(/<svg([^>]*)>/, (m0, attrs) => `<svg${attrs.replace(/\swidth="[^"]*"/, "").replace(/\sheight="[^"]*"/, "").replace(/\sstyle="[^"]*"/, "")} width="${w}" height="${h}" class="md-svg md-mermaid-svg">`);
      return { svg, width: w, height: h, background: _bg, exportBg: _exportBg, dark: _dark };
    });
    mermaidQueue = job.catch(() => {});
    return job;
  }
  function friendlyMermaidError(e) {
    const msg = String(e?.message || e?.str || e || "").replace(/\s+at\s.*$/s, "");
    const line = msg.match(/line\s+(\d+)/i);
    const first = msg.split("\n").filter(Boolean).slice(0, 3).join(" ").slice(0, 300);
    return (line ? t(`Ошибка синтаксиса схемы в строке ${line[1]}. `, `Scheme syntax error on line ${line[1]}. `) : t("Ошибка синтаксиса схемы. ", "Scheme syntax error. ")) + first;
  }
  function mermaidKind(code) {
    const first = String(code || "").split("\n").map((l) => l.trim()).find((l) => l && !l.startsWith("%%")) || "";
    const word = first.split(/\s+/)[0].toLowerCase();
    const names = { flowchart: ["Блок-схема", "Flowchart"], graph: ["Блок-схема", "Flowchart"], sequencediagram: ["Диаграмма последовательности", "Sequence diagram"],
      mindmap: ["Интеллект-карта", "Mind map"], erdiagram: ["ER-диаграмма", "ER diagram"], gantt: ["Диаграмма Ганта", "Gantt chart"], timeline: ["Хронология", "Timeline"],
      classdiagram: ["Диаграмма классов", "Class diagram"], "statediagram-v2": ["Диаграмма состояний", "State diagram"], statediagram: ["Диаграмма состояний", "State diagram"],
      journey: ["Путь пользователя", "User journey"], pie: ["Круговая диаграмма", "Pie chart"], quadrantchart: ["Квадранты", "Quadrant chart"], gitgraph: ["Git-граф", "Git graph"],
      "xychart-beta": ["График", "Chart"], "sankey-beta": ["Санкей", "Sankey"], "block-beta": ["Блоки", "Blocks"], requirementdiagram: ["Требования", "Requirements"], c4context: ["C4", "C4"] };
    const n = names[word];
    return n ? t(n[0], n[1]) : t("Схема", "Scheme");
  }

  /* ---------------------------------------------------------------- blocks in messages */
  const LANG_CHART = /^(memuai[-_]?chart|chart-json)$/;
  const LANG_MERMAID = /^(mermaid|mmd)$/;
  function hashStr(s) { let h = 2166136261; for (let i = 0; i < s.length; i += 1) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); }
  function encodeSrc(code) { return encodeURIComponent(code).replace(/'/g, "%27"); }
  function decodeSrc(v) { try { return decodeURIComponent(v || ""); } catch (e) { return ""; } }
  function pending(en, kind) {
    const label = kind === "mermaid" ? (en ? "Drawing the scheme…" : "Рисую схему…") : (en ? "Drawing the chart…" : "Рисую график…");
    return `<div class="memu-diagram-pending" role="status" aria-live="polite"><div class="mdp-art" aria-hidden="true"><i style="--h:42%"></i><i style="--h:70%"></i><i style="--h:55%"></i><i style="--h:88%"></i><i style="--h:63%"></i></div><span>${label}</span></div>`;
  }
  function placeholder(code, lang, en) {
    const kind = LANG_MERMAID.test(lang) ? "mermaid" : "chart";
    if (!String(code || "").trim()) return "";
    if (code.length > MAX_SOURCE) return `<div class="memu-diagram failed" role="note">${en ? "The diagram is too large to show." : "Диаграмма слишком большая, чтобы её показать."}</div>`;
    let ratio = "720 / 380";
    if (kind === "chart") { try { const s = JSON.parse(code); if (s.type === "hbar" || s.horizontal) ratio = ""; } catch (e) { /* drawn later */ } }
    return `<div class="memu-diagram" data-memu-diagram="${kind}" data-src="${encodeSrc(code)}" data-key="${kind}-${hashStr(code)}"><div class="md-stage md-loading"${kind === "chart" && ratio ? ` style="aspect-ratio:${ratio}"` : ""}>${pending(en, kind)}</div></div>`;
  }
  function block(lang, code, en) {
    if (lang === "memuai-diagram-pending") return pending(en, String(code || "").trim() === "mermaid" ? "mermaid" : "chart");
    if (LANG_CHART.test(lang) || LANG_MERMAID.test(lang)) return placeholder(code, lang, en);
    return "";
  }
  // Streaming: the diagram that is still being written (last block of the answer) stays a placeholder.
  function maskPending(text) {
    const value = String(text || "");
    const open = value.match(/```[ \t]*(memuai[-_]?chart|mermaid|mmd)\b(?:(?!```)[\s\S])*$/i);
    if (open) return value.slice(0, open.index) + "```memuai-diagram-pending\n" + (/^m/i.test(open[1]) && !/^memuai/i.test(open[1]) ? "mermaid" : "chart") + "\n```";
    const closed = value.match(/```[ \t]*(memuai[-_]?chart|mermaid|mmd)\b[\s\S]*?```\s*$/i);
    if (closed) return value.slice(0, closed.index) + "```memuai-diagram-pending\n" + (/^memuai/i.test(closed[1]) ? "chart" : "mermaid") + "\n```\n";
    return value;
  }
  function stripForText(text, en) {
    return String(text || "").replace(/```[ \t]*memuai[-_]?chart\b[\s\S]*?(?:```|$)/gi, en ? "[chart]" : "[график]")
      .replace(/```[ \t]*(?:mermaid|mmd)\b[\s\S]*?(?:```|$)/gi, en ? "[diagram]" : "[схема]");
  }

  const cache = new Map();       // key -> { svg, width, height, spec?, error? }
  const inflight = new Map();
  const hiddenSeries = new Map(); // block key -> Set
  function cacheSet(key, value) { cache.set(key, value); if (cache.size > 120) cache.delete(cache.keys().next().value); }
  function sceneKey() { const a = appTheme(); return `${a.dark ? "d" : "l"}${a.accent}${a.panel}`; }

  async function produce(kind, code, style, opts = {}) {
    if (kind === "mermaid") return renderMermaid(code, style, opts.forceTheme);
    const spec = parseSpec(code);
    return renderChart(spec, style, opts);
  }
  function barHtml(kind) {
    const b = (id, icon, label) => `<button type="button" class="mt-btn" data-md="${id}" title="${esc(label)}" aria-label="${esc(label)}">${UI() ? UI().ic(icon) : ""}</button>`;
    return `<div class="md-bar" role="toolbar" aria-label="${esc(t("Действия с диаграммой", "Diagram actions"))}">` +
      b("style", "palette", t("Оформление…", "Customize…")) + b("copy", "copy", t("Копировать картинкой", "Copy as an image")) +
      b("export", "download", t("Скачать", "Download")) + b("code", "code", t("Показать код", "Show the code")) + b("full", "full", t("На весь экран", "Fullscreen")) + "</div>";
  }
  function ensureIcons() {
    const ui = UI();
    if (!ui || ui.IC.palette) return;
    const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
    try {
      ui.IC.palette = P('<circle cx="13.5" cy="6.5" r="1.3"/><circle cx="17.5" cy="10.5" r="1.3"/><circle cx="8.5" cy="7.5" r="1.3"/><circle cx="6.5" cy="12.5" r="1.3"/><path d="M12 2a10 10 0 1 0 0 20c1.1 0 1.8-.8 1.8-1.8 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-1 .8-1.8 1.8-1.8H17a5 5 0 0 0 5-5C22 6 17.5 2 12 2z"/>');
      ui.IC.code = P('<path d="m8 6-6 6 6 6M16 6l6 6-6 6"/>');
      ui.IC.chart = P('<path d="M3.5 3.5v17h17"/><path d="m7.5 15 3.8-4.2 3 2.7 5.2-6"/>');
      ui.IC.wand = P('<path d="m15 4 1 2 2 1-2 1-1 2-1-2-2-1 2-1zM5 19 16 8"/>');
    } catch (e) { /* frozen */ }
  }
  async function draw(host, opts = {}) {
    const kind = host.dataset.memuDiagram;
    const code = decodeSrc(host.dataset.src);
    const key = host.dataset.key || kind + "-" + hashStr(code);
    const style = styleFor(key);
    const hidden = hiddenSeries.get(key) || new Set();
    const ckey = `${key}|${sceneKey()}|${JSON.stringify(style)}|${[...hidden].join(",")}`;
    host.dataset.ready = "1";
    let result = cache.get(ckey);
    if (!result) {
      if (kind === "chart") {
        try { result = renderChart(parseSpec(code), style, { hidden }); /* sync: produce() is async */ } catch (e) { result = { error: e instanceof SpecError ? e.message : t("Не удалось нарисовать график: ", "Could not draw the chart: ") + (e.message || e) }; }
        cacheSet(ckey, result);
      } else {
        let job = inflight.get(ckey);
        if (!job) {
          job = produce(kind, code, style).then((r) => r, (e) => ({ error: e instanceof SpecError ? e.message : (e.message || String(e)) }));
          inflight.set(ckey, job);
          job.then((r) => { cacheSet(ckey, r); inflight.delete(ckey); });
        }
        result = await job;
      }
    }
    if (!host.isConnected && !opts.detached) return;
    paint(host, kind, code, key, result, opts);
  }
  function paint(host, kind, code, key, result, opts) {
    ensureIcons();
    const keepCode = host.querySelector(".md-code") && !host.querySelector(".md-code").hidden;
    if (result.error) {
      host.classList.add("md-failed");
      host.innerHTML = `<div class="md-error" role="note"><div class="md-error-head">${UI() ? UI().ic("chart") : ""}<b>${esc(kind === "mermaid" ? t("Схему не удалось нарисовать", "The scheme could not be drawn") : t("График не удалось нарисовать", "The chart could not be drawn"))}</b></div>
        <div class="md-error-msg">${esc(result.error)}</div>
        <div class="md-error-actions"><button type="button" class="mt-tb" data-md="code">${UI() ? UI().ic("code") : ""}<span>${esc(t("Код", "Code"))}</span></button>${opts.noFix ? "" : `<button type="button" class="mt-tb" data-md="fix">${UI() ? UI().ic("wand") : ""}<span>${esc(t("Исправить с ИИ", "Fix with AI"))}</span></button>`}</div></div>
        <pre class="md-code"${keepCode ? "" : " hidden"}><code>${esc(code)}</code></pre>`;
      bindBlock(host, kind, code, key, result, opts);
      return;
    }
    host.classList.remove("md-failed");
    const label = kind === "mermaid" ? mermaidKind(code) : (result.spec?.title || t("График", "Chart"));
    const bgStyle = kind === "mermaid" ? "" : "";
    host.innerHTML = `<div class="md-stage${kind === "mermaid" ? " md-scheme" : ""}" title="" ${bgStyle}>${result.svg}</div>${barHtml(kind)}<pre class="md-code"${keepCode ? "" : " hidden"}><code>${esc(code)}</code></pre>`;
    host.setAttribute("aria-label", label);
    const svg = host.querySelector(".md-stage svg");
    if (svg && kind === "mermaid" && window.MemuDiagramZoom && !opts.detached) {
      // Round 30: a readable size with pan and zoom inside the card instead of squeezing the text (diagram-zoom.js).
      MemuDiagramZoom.attach(host.querySelector(".md-stage"), { width: result.width, height: result.height, host, full: host.dataset.mdFull === "1", label });
    } else if (svg) {
      svg.removeAttribute("height");
      svg.style.width = "100%"; svg.style.height = "auto";
      if (kind === "mermaid") svg.style.maxWidth = Math.max(result.width, 160) + "px";
    }
    bindBlock(host, kind, code, key, result, opts);
  }
  function bindBlock(host, kind, code, key, result, opts) {
    host.onclick = async (e) => {
      const leg = e.target.closest(".md-leg");
      if (leg && kind === "chart") {
        const set = new Set(hiddenSeries.get(key) || []);
        const i = +leg.dataset.series;
        if (set.has(i)) set.delete(i); else set.add(i);
        const total = result.spec ? (result.type === "pie" || result.type === "donut" ? result.spec.labels.length : result.spec.series.length) : 0;
        if (set.size >= total) return;   // keep at least one visible
        hiddenSeries.set(key, set);
        host.removeAttribute("data-ready");
        draw(host, opts);
        return;
      }
      const b = e.target.closest("[data-md]");
      if (!b) return;
      const what = b.dataset.md;
      const ui = UI();
      try {
        if (what === "code") {
          const pre = host.querySelector(".md-code");
          pre.hidden = !pre.hidden;
          if (!pre.hidden && !pre.querySelector(".md-code-copy")) {
            pre.insertAdjacentHTML("afterbegin", `<button type="button" class="mt-btn md-code-copy" title="${esc(t("Копировать код", "Copy the code"))}">${ui ? ui.ic("copy") : ""}</button>`);
            pre.querySelector(".md-code-copy").onclick = async (ev) => { ev.stopPropagation(); await ui?.copyText(code); ui?.notify(t("Код скопирован", "Code copied")); };
          }
        } else if (what === "fix") fixWithAI(kind, code, result.error, opts);
        else if (what === "copy") { const out = await toPNG(host, { scale: 2 }); await ui.copyImageBlob(out.blob); ui.notify(t("Картинка скопирована", "Image copied")); }
        else if (what === "export") ui.showMenu(b, exportMenu(host, kind, code, result));
        else if (what === "style") openStudio(host);
        else if (what === "full") openFull(host);
      } catch (err) { ui?.notify(err.message || String(err), "warn"); }
    };
  }
  function baseName(kind, code, result) {
    const ui = UI();
    const title = kind === "chart" ? (result?.spec?.title || t("график", "chart")) : mermaidKind(code);
    return ui ? ui.safeName(title) : "diagram";
  }
  function exportMenu(host, kind, code, result) {
    const ui = UI();
    const name = baseName(kind, code, result);
    const items = [{ section: t("Сохранить как", "Save as") }];
    [1, 2, 3, 4].forEach((k) => items.push({ icon: "image", label: `PNG ${k}×`, hint: k === 2 ? t("чётко на любом экране", "crisp on any screen") : "", run: async () => { const out = await toPNG(host, { scale: k }); await ui.saveFile(`${name}.png`, out.blob); } }));
    items.push({ icon: "image", label: "SVG", hint: t("вектор, без потерь", "vector, lossless"), run: async () => ui.saveFile(`${name}.svg`, await toSVG(host), "image/svg+xml") });
    if (kind === "chart" && result?.spec) {
      items.push({ icon: "download", label: "CSV", hint: t("данные для Excel", "data for Excel"), run: () => ui.saveFile(`${name}.csv`, specCSV(result.spec).csv, "text/csv;charset=utf-8") });
      items.push({ icon: "download", label: "XLSX", run: () => ui.saveFile(`${name}.xlsx`, window.MemuTables.toXLSX(specCSV(result.spec).data)) });
      items.push({ icon: "copy", label: t("Данные как таблица", "Data as a table"), hint: "Markdown", run: async () => { await ui.copyText(window.MemuTables.toMarkdown(specCSV(result.spec).data)); ui.notify(t("Скопировано", "Copied")); } });
      items.push({ icon: "download", label: t("Спецификация JSON", "JSON spec"), run: () => ui.saveFile(`${name}.json`, code, "application/json") });
    } else items.push({ icon: "download", label: t("Исходник Mermaid", "Mermaid source"), hint: ".mmd", run: () => ui.saveFile(`${name}.mmd`, code, "text/plain;charset=utf-8") });
    return items;
  }
  function fixWithAI(kind, code, error, opts) {
    const text = kind === "mermaid"
      ? t(`Схема Mermaid выше не нарисовалась (${error}). Пришли исправленную версию целиком, без пояснений про ошибку.`, `The Mermaid scheme above failed to render (${error}). Send the corrected version in full, without explaining the error.`)
      : t(`График выше не нарисовался (${error}). Пришли исправленный блок memuai-chart целиком.`, `The chart above failed to render (${error}). Send the corrected memuai-chart block in full.`);
    if (opts.onFix) { opts.onFix(text); return; }
    const input = document.getElementById("input") || document.getElementById("qa-input");
    if (!input) return;
    const empty = !input.value.trim();
    input.value = empty ? text : input.value.trim() + "\n\n" + text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    const busy = typeof currentChatBusy === "function" && currentChatBusy();
    if (empty && !busy && input.id === "input" && typeof send === "function") { try { send(); } catch (e) { /* the text stays in the box */ } }
  }

  function hydrate(root, isLive, opts = {}) {
    const scope = root || document;
    const exporting = !!(scope.querySelector?.(".mx-content") || scope.closest?.(".mx-content"));
    if (exporting) {
      scope.querySelectorAll(".md-bar, .md-code, .md-error-actions, .memu-diagram-pending").forEach((n) => n.remove());
    }
    const jobs = [];
    scope.querySelectorAll(".memu-diagram[data-src]:not([data-ready])").forEach((host) => {
      if (isLive && isLive(host)) return;
      jobs.push(draw(host, { ...opts, detached: exporting }).catch((e) => console.warn("MemuDiagrams", e)));
    });
    if (exporting && jobs.length) return Promise.all(jobs).then(() => scope.querySelectorAll(".md-bar, .md-code, .md-error-actions").forEach((n) => n.remove()));
    return Promise.all(jobs);
  }
  function refresh(root) {
    (root || document).querySelectorAll(".memu-diagram[data-ready]").forEach((h) => h.removeAttribute("data-ready"));
    return hydrate(root);
  }
  // theme / design / accent changes re-draw every diagram (no hook in setTheme needed)
  let themeTimer = 0;
  try {
    new MutationObserver(() => { clearTimeout(themeTimer); themeTimer = setTimeout(() => { if (document.querySelector(".memu-diagram[data-ready]")) refresh(document); }, 120); })
      .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-design", "style", "class"] });
  } catch (e) { /* old engine */ }

  /* ---------------------------------------------------------------- export helpers (public) */
  function sourceOf(target) {
    if (!target) return null;
    if (typeof target === "string") return { kind: /^\s*\{/.test(target) ? "chart" : "mermaid", code: target, key: "x-" + hashStr(target) };
    if (target.nodeType === 1) {
      const host = target.matches(".memu-diagram, [data-memu-diagram]") ? target : target.closest?.(".memu-diagram") || target.querySelector?.(".memu-diagram");
      if (!host) return { svgEl: target.querySelector?.("svg") || (target.tagName.toLowerCase() === "svg" ? target : null) };
      const code = decodeSrc(host.dataset.src);
      return { kind: host.dataset.memuDiagram, code, key: host.dataset.key || host.dataset.memuDiagram + "-" + hashStr(code), host };
    }
    if (typeof target === "object") return { kind: "chart", spec: target, code: JSON.stringify(target), key: "x-" + hashStr(JSON.stringify(target)) };
    return null;
  }
  // -> { svg, width, height } with a solid/gradient background baked in (for files and clipboard)
  async function exportScene(target, opts = {}) {
    const src = sourceOf(target);
    if (!src) throw new Error(t("Диаграмма не найдена", "Diagram not found"));
    if (src.svgEl) {
      const clone = src.svgEl.cloneNode(true);
      const box = src.svgEl.getBoundingClientRect();
      const w = Math.round(box.width || 600), h = Math.round(box.height || 400);
      clone.setAttribute("xmlns", "http://www.w3.org/2000/svg"); clone.setAttribute("width", w); clone.setAttribute("height", h);
      return { svg: new XMLSerializer().serializeToString(clone), width: w, height: h };
    }
    const style = { ...styleFor(src.key), ...(opts.style || {}) };
    const forced = opts.theme === "dark" || opts.theme === "light" ? opts.theme : undefined;
    if (src.kind === "mermaid") {
      const r = await renderMermaid(src.code, style, forced);
      const pad = Math.max(0, opts.padding ?? 16);
      const W = r.width + pad * 2, H = r.height + pad * 2;
      const bgv = opts.background !== undefined ? opts.background : (style.background === "none" ? "none" : style.background === "theme" ? r.exportBg : style.background === "solid" ? style.bg1 : { gradient: [style.bg1, style.bg2, style.gradAngle] });
      const bg = bgMarkup(bgv, W, H, "mdx" + (++uid), 0);
      const inner = r.svg.replace(/^<svg/, `<svg x="${pad}" y="${pad}"`);
      return { svg: `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs>${bg.defs}</defs>${bg.rect}${inner}</svg>`, width: W, height: H };
    }
    const spec = src.spec ? normalizeSpec(src.spec) : parseSpec(src.code);
    const th = themeFor(style, forced);
    const bgv = opts.background !== undefined ? opts.background : (style.background === "none" ? "none" : th.background === "none" ? th.exportBg : th.background);
    const r = renderChart(spec, style, { theme: th, background: bgv, radius: 0, hidden: src.key ? hiddenSeries.get(src.key) : undefined });
    return { svg: r.svg, width: r.width, height: r.height };
  }
  async function toSVG(target, opts = {}) { return (await exportScene(target, opts)).svg; }
  async function toPNG(target, opts = {}) {
    const scene = await exportScene(target, opts);
    const ui = UI();
    let blob;
    if (ui) blob = await ui.svgToPngBlob(scene.svg, scene.width, scene.height, opts.scale || 2);
    else {
      const img = new Image();
      const url = URL.createObjectURL(new Blob([scene.svg], { type: "image/svg+xml" }));
      await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
      const c = document.createElement("canvas"); const k = opts.scale || 2;
      c.width = scene.width * k; c.height = scene.height * k; c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      blob = await new Promise((res) => c.toBlob(res, "image/png"));
    }
    const src = await new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(blob); });
    return { src, w: scene.width, h: scene.height, blob };
  }
  function toCSV(target) {
    const src = sourceOf(target);
    if (!src || src.kind !== "chart") return "";
    try { return specCSV(src.spec ? normalizeSpec(src.spec) : parseSpec(src.code)).csv; } catch (e) { return ""; }
  }

  /* ---------------------------------------------------------------- fullscreen */
  function openFull(host) {
    const ui = UI();
    const kind = host.dataset.memuDiagram;
    const code = decodeSrc(host.dataset.src);
    const m = ui.modal("md-full", kind === "mermaid" ? mermaidKind(code) : t("График", "Chart"), `<div class="md-full-stage"></div>`, { wide: true });
    const stage = m.el.querySelector(".md-full-stage");
    const clone = host.cloneNode(false);
    clone.removeAttribute("data-ready");
    clone.dataset.mdFull = "1";     // survives a redraw on a theme change
    stage.appendChild(clone);
    // Schemes zoom and pan in the dialog like in the message (diagram-zoom.js), starting with the whole scheme.
    draw(clone, { noFix: true });
  }

  /* ---------------------------------------------------------------- studio (customize the look) */
  function openStudio(host) {
    const ui = UI();
    if (!ui) return;
    const kind = host.dataset.memuDiagram;
    const code = decodeSrc(host.dataset.src);
    const key = host.dataset.key || kind + "-" + hashStr(code);
    let style = styleFor(key);
    let spec = null;
    if (kind === "chart") { try { spec = parseSpec(code); } catch (e) { spec = null; } }
    const en = isEn();
    const opt = (v, label, cur) => `<option value="${esc(v)}"${String(cur) === String(v) ? " selected" : ""}>${esc(label)}</option>`;
    const typeNames = { line: ["Линии", "Line"], bar: ["Столбцы", "Bars"], hbar: ["Горизонтальные", "Horizontal bars"], area: ["Области", "Area"], pie: ["Круговая", "Pie"], donut: ["Кольцо", "Donut"], scatter: ["Точки", "Scatter"], radar: ["Радар", "Radar"], combo: ["Комбинированный", "Combo"] };
    const types = spec ? TYPES.filter((ty) => compatible(spec.type, ty)) : [];
    const colorIn = (k, label) => `<label class="mt-st-color"><span>${label}</span><input type="color" data-ds="${k}"></label>`;
    const chartControls = kind === "chart" ? `
      <div class="mt-st-sec"><b>${t("Тип", "Type")}</b>
        <div class="mt-seg md-types">${types.map((ty) => `<button type="button" data-type="${ty}">${esc(en ? typeNames[ty][1] : typeNames[ty][0])}</button>`).join("")}</div></div>
      <div class="mt-st-sec"><b>${t("Элементы", "Elements")}</b>
        <div class="mt-st-row"><label>${t("Легенда", "Legend")}<select data-ds="legend">${[["auto", t("Авто", "Auto")], ["top", t("Сверху", "Top")], ["bottom", t("Снизу", "Bottom")], ["right", t("Справа", "Right")], ["none", t("Нет", "None")]].map(([v, l]) => opt(v, l, style.legend)).join("")}</select></label>
          <label>${t("Заголовок", "Title")}<input type="text" data-ds="title" placeholder="${esc(spec?.title || "")}" maxlength="120"></label></div>
        <div class="mt-st-row"><label class="mt-check"><input type="checkbox" data-ds="grid"> ${t("Сетка", "Grid")}</label><label class="mt-check"><input type="checkbox" data-ds="labels"> ${t("Подписи значений", "Value labels")}</label>
          <label class="mt-check"><input type="checkbox" data-ds="points"> ${t("Точки", "Points")}</label><label class="mt-check"><input type="checkbox" data-ds="showTitle"> ${t("Заголовок", "Title")}</label></div>
        <div class="mt-st-row"><label>${t("Сглаживание", "Smoothing")}<select data-ds="smooth">${opt("", t("Как в данных", "As in the data"), style.smooth === null ? "" : style.smooth)}${opt("true", t("Плавные линии", "Smooth"), String(style.smooth))}${opt("false", t("Прямые", "Straight"), String(style.smooth))}</select></label>
          <label>${t("Наложение", "Stacking")}<select data-ds="stacked">${opt("", t("Как в данных", "As in the data"), style.stacked === null ? "" : style.stacked)}${opt("true", t("Накопление", "Stacked"), String(style.stacked))}${opt("false", t("Рядом", "Side by side"), String(style.stacked))}</select></label></div>
        <div class="mt-st-row"><label>${t("Толщина линий", "Line width")}<input type="range" min="1" max="5" step="0.2" data-ds="lineWidth"></label><label>${t("Скругление столбцов", "Bar radius")}<input type="range" min="0" max="14" step="1" data-ds="barRadius"></label></div>
      </div>` : `
      <div class="mt-st-sec"><b>${t("Схема", "Scheme")}</b>
        <div class="mt-st-row"><label>${t("Тема Mermaid", "Mermaid theme")}<select data-ds="mTheme">${[["auto", t("Как в приложении", "Match the app")], ["default", "Default"], ["neutral", "Neutral"], ["dark", "Dark"], ["forest", "Forest"]].map(([v, l]) => opt(v, l, style.mTheme)).join("")}</select></label>
          <label>${t("Стиль", "Style")}<select data-ds="mLook">${opt("classic", t("Классический", "Classic"), style.mLook)}${opt("handDrawn", t("От руки", "Hand-drawn"), style.mLook)}</select></label></div>
        <div class="mt-st-row"><label>${t("Линии связей", "Edges")}<select data-ds="mCurve">${[["basis", t("Плавные", "Smooth")], ["linear", t("Прямые", "Straight")], ["step", t("Ступеньки", "Steps")], ["monotoneX", t("Мягкие", "Soft")]].map(([v, l]) => opt(v, l, style.mCurve)).join("")}</select></label>
          ${colorIn("accent", t("Акцент", "Accent"))}</div>
      </div>`;
    const m = ui.modal("mt-studio md-studio", kind === "mermaid" ? t("Оформление схемы", "Scheme look") : t("Оформление графика", "Chart look"), `
      <div class="mt-st-layout">
        <div class="mt-st-preview"><div class="mt-st-canvas md-st-canvas"></div></div>
        <div class="mt-st-side md-dg-side mt-dg-side">
          ${chartControls}
          <div class="mt-st-sec"><b>${t("Тема и цвета", "Theme and colors")}</b>
            <div class="mt-st-row"><label>${t("Тема", "Theme")}<select data-ds="look">${Object.entries(LOOKS).map(([v, l]) => opt(v, en ? l.en : l.ru, style.look)).join("")}</select></label>
              <label>${t("Палитра", "Palette")}<select data-ds="palette">${Object.entries(PALETTES).map(([v, p]) => opt(v, en ? p.en : p.ru, style.palette)).join("")}</select></label></div>
            <div class="md-pal-preview"></div>
            <div class="mt-st-row"><label>${t("Шрифт", "Font")}<select data-ds="font">${FONTS.map(([v, l]) => opt(v, v === "app" ? t("Как в приложении", "Match the app") : l, style.font)).join("")}</select></label>
              <label>${t("Размер текста", "Text size")}<input type="range" min="9" max="18" step="1" data-ds="fontSize"></label></div>
          </div>
          <div class="mt-st-sec"><b>${t("Фон при экспорте", "Background on export")}</b>
            <div class="mt-seg" data-dseg="background"><button type="button" data-v="theme">${t("Темы", "Theme")}</button><button type="button" data-v="none">${t("Прозрачный", "Transparent")}</button><button type="button" data-v="solid">${t("Цвет", "Color")}</button><button type="button" data-v="gradient">${t("Градиент", "Gradient")}</button></div>
            <div class="mt-st-row">${colorIn("bg1", t("Цвет 1", "Color 1"))}<span data-when-bg="gradient">${colorIn("bg2", t("Цвет 2", "Color 2"))}</span><label data-when-bg="gradient">${t("Угол", "Angle")}<input type="range" min="0" max="360" step="5" data-ds="gradAngle"></label></div>
          </div>
          ${kind === "chart" ? `<div class="mt-st-sec"><b>${t("Размер", "Size")}</b>
            <div class="mt-st-row"><label>${t("Ширина", "Width")}<input type="number" min="280" max="2400" step="20" data-ds="width" placeholder="720"></label><label>${t("Высота", "Height")}<input type="number" min="200" max="1800" step="20" data-ds="height" placeholder="${t("авто", "auto")}"></label></div></div>` : ""}
          <div class="mt-st-sec"><b>${t("Экспорт", "Export")}</b>
            <div class="mt-seg" data-dseg="scale"><button type="button" data-v="1">1×</button><button type="button" data-v="2">2×</button><button type="button" data-v="3">3×</button><button type="button" data-v="4">4×</button></div>
            <label class="mt-check"><input type="checkbox" data-ds-default> ${t("Применять ко всем новым диаграммам", "Use for all new diagrams")}</label>
          </div>
        </div>
      </div>
      <div class="mt-ed-actions">
        <button type="button" class="mt-tb ghost" data-da="reset">${t("Сбросить", "Reset")}</button>
        <span class="mt-ed-size" data-da-size></span>
        <button type="button" class="mt-tb" data-da="copy">${ui.ic("copy")}<span>${t("Копировать", "Copy")}</span></button>
        <button type="button" class="mt-tb" data-da="svg">${ui.ic("download")}<span>SVG</span></button>
        <button type="button" class="mt-tb" data-da="png">${ui.ic("download")}<span>PNG</span></button>
        <button type="button" class="mt-tb primary" data-da="apply">${ui.ic("check")}<span>${t("Применить", "Apply")}</span></button>
      </div>`, { wide: true });
    const el = m.el;
    style.scale = style.scale || 2;
    const canvas = el.querySelector(".md-st-canvas");
    function sync() {
      el.querySelectorAll("[data-ds]").forEach((input) => {
        const k = input.dataset.ds, v = style[k];
        if (input.type === "checkbox") input.checked = k === "showTitle" ? v !== false : k === "points" ? v !== false : k === "grid" ? v !== false : !!v;
        else if (input.type === "color") input.value = ui.toHex(k === "accent" ? (v || appTheme().accent) : v);
        else if (input.tagName === "SELECT" && (k === "smooth" || k === "stacked")) input.value = v === null || v === undefined ? "" : String(v);
        else input.value = v === 0 && (k === "width" || k === "height") ? "" : (v ?? "");
      });
      el.querySelectorAll("[data-dseg]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", String(style[seg.dataset.dseg]) === b.dataset.v)));
      el.querySelectorAll(".md-types button").forEach((b) => b.classList.toggle("on", (style.type || spec?.type) === b.dataset.type));
      el.querySelectorAll("[data-when-bg]").forEach((n) => (n.hidden = style.background !== n.dataset.whenBg));
      const pal = themeFor(style).palette;
      el.querySelector(".md-pal-preview").innerHTML = pal.slice(0, 8).map((c) => `<i style="background:${esc(c)}"></i>`).join("");
    }
    let seq = 0;
    async function preview() {
      const my = ++seq;
      try {
        const scene = await exportScene(host, { style, padding: 16 });
        if (my !== seq) return;
        canvas.innerHTML = scene.svg;
        const s = canvas.querySelector("svg");
        s.removeAttribute("height"); s.style.width = "100%"; s.style.height = "auto"; s.style.maxWidth = scene.width + "px";
        canvas.classList.toggle("checker", style.background === "none");
        el.querySelector("[data-da-size]").textContent = `${Math.round(scene.width * style.scale)} × ${Math.round(scene.height * style.scale)} px`;
      } catch (e) {
        if (my === seq) canvas.innerHTML = `<div class="md-error-msg">${esc(e.message || e)}</div>`;
      }
    }
    el.addEventListener("input", (e) => {
      const input = e.target.closest("[data-ds]");
      if (!input) return;
      const k = input.dataset.ds;
      let v = input.type === "checkbox" ? input.checked : input.type === "range" ? +input.value : input.value;
      if (k === "smooth" || k === "stacked") v = input.value === "" ? null : input.value === "true";
      if (k === "width" || k === "height") v = +input.value || 0;
      style[k] = v;
      if (k === "look" || k === "palette") sync();
      preview();
    });
    el.addEventListener("change", (e) => { if (e.target.matches("select[data-ds]")) e.target.dispatchEvent(new Event("input", { bubbles: true })); });
    el.querySelectorAll("[data-dseg]").forEach((seg) => seg.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      style[seg.dataset.dseg] = seg.dataset.dseg === "scale" ? +b.dataset.v : b.dataset.v;
      sync(); preview();
    }));
    el.querySelector(".md-types")?.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-type]");
      if (!b) return;
      style.type = b.dataset.type === spec?.type ? "" : b.dataset.type;
      sync(); preview();
    });
    const persist = () => {
      const { scale, ...rest } = style;
      rememberStyle(key, rest);
      if (el.querySelector("[data-ds-default]").checked) {
        const { title, type, width, height, ...general } = rest;
        try { localStorage.setItem(STYLE_KEY, JSON.stringify(general)); } catch (e) { /* quota */ }
      }
    };
    el.querySelector(".mt-ed-actions").addEventListener("click", async (e) => {
      const b = e.target.closest("[data-da]");
      if (!b) return;
      const act = b.dataset.da;
      try {
        if (act === "reset") { style = { ...DEFAULT_STYLE, scale: style.scale }; sync(); preview(); return; }
        persist();
        const name = baseName(kind, code, { spec });
        if (act === "apply") { m.close(); document.querySelectorAll(`.memu-diagram[data-key="${CSS.escape(key)}"]`).forEach((h) => { h.removeAttribute("data-ready"); draw(h); }); return; }
        b.disabled = true;
        if (act === "svg") await ui.saveFile(`${name}.svg`, await toSVG(host, { style }), "image/svg+xml");
        else {
          const out = await toPNG(host, { style, scale: style.scale });
          if (act === "copy") { await ui.copyImageBlob(out.blob); ui.notify(t("Картинка скопирована", "Image copied")); }
          else await ui.saveFile(`${name}.png`, out.blob);
        }
        document.querySelectorAll(`.memu-diagram[data-key="${CSS.escape(key)}"]`).forEach((h) => { if (!h.closest(".mt-modal")) { h.removeAttribute("data-ready"); draw(h); } });
      } catch (err) { ui.notify(err.message || String(err), "warn"); }
      finally { b.disabled = false; }
    });
    sync(); preview();
    return m;
  }

  /* ---------------------------------------------------------------- settings (Chat & answers) */
  function settingsHtml(ctx) {
    const { K, L2 } = ctx;
    const cur = mode();
    const o = (v, ru, en) => `<option value="${v}"${cur === v ? " selected" : ""}>${L2(ru, en)}</option>`;
    return K.row({ label: L2("Графики и схемы", "Charts and diagrams"),
      desc: L2("Когда ИИ рисует графики (столбцы, линии, круговые…) и схемы (блок-схемы, ER, Гант, интеллект-карты)", "When the AI draws charts (bars, lines, pies…) and schemes (flowcharts, ER, gantt, mind maps)"),
      key: "diagrams_mode", def: "auto", keywords: "chart diagram mermaid график диаграмма схема flowchart блок-схема gantt",
      control: `<select class="pinput compact" id="st-diagrams-mode" style="width:220px;margin:0">${o("auto", "Когда действительно нужно", "Only when really useful")}${o("ask", "Только по просьбе, иначе предложить", "Only on request, otherwise offer")}${o("off", "Выключено", "Off")}</select>` });
  }
  function bindSettings() {
    const sel = document.getElementById("st-diagrams-mode");
    if (sel) sel.onchange = () => { if (typeof savePreferences === "function") savePreferences({ diagrams_mode: sel.value }); if (typeof toast === "function" && typeof pt === "function") toast(pt("saved")); };
  }

  window.MemuDiagrams = Object.freeze({
    VERSION, PALETTES: Object.keys(PALETTES), LOOKS: Object.keys(LOOKS), TYPES: TYPES.slice(),
    prompt, mode, block, placeholder, pending, maskPending, stripForText, hydrate, refresh,
    parseSpec, normalizeSpec, renderChart, renderMermaid, repairMermaid, loadMermaid, toSVG, toPNG, toCSV, exportScene,
    openStudio, openFull, settingsHtml, bindSettings, niceTicks, fmtNum,
  });
})();
