/* Custom MemuMusic themes: one shared format for the standalone app and
   the MemuAI module. A theme is a built-in-shaped palette (hue, saturation,
   three accents, light/dark lightness) plus optional explicit surface
   tokens, so it flows through the same tokensFor/accents path as the
   built-in themes. MemuAI-only fields (elements, look, prefs, css) are
   ignored on import instead of being required. */

const CustomThemes = (() => {
  const FORMAT = "memumusic-theme";
  const SCHEMA = 1;
  const MAX_THEMES = 48;
  const TOKEN_NAMES = ["--mm-bg", "--mm-surface", "--mm-text", "--mm-panel", "--mm-panel-2",
    "--mm-line", "--mm-hover", "--mm-muted", "--mm-faint", "--mm-shadow"];
  /* MemuAI token names, mapped onto the MemuMusic ones on import. */
  const MEMU_TOKENS = { "--bg": "--mm-bg", "--panel": "--mm-surface", "--text": "--mm-text",
    "--muted": "--mm-muted", "--border": "--mm-line" };
  /* Extra CSS variables a custom theme may override. The editor exposes
     exactly this list: geometry, fonts, status colours and the cover /
     ambient overlays. Anything else is dropped on import, like unknown
     tokens, so a theme file can never set an arbitrary property. */
  const VAR_NAMES = ["--mm-rk", "--mm-tile", "--mm-row", "--mm-pad-x", "--mm-gap", "--mm-side", "--mm-bar",
    "--mm-font", "--mm-display", "--mm-display-weight", "--mm-title-track",
    "--mm-danger", "--mm-ok", "--mm-cover-opacity", "--mm-cover-blur", "--mm-ambient-opacity"];
  const CSS_MAX = 8000;
  const FALLBACK = { h: 252, s: 30, light: [96.5, 100], dark: [5.5, 9.5, 0.42] };

  function fail(key) { const err = new Error(key); err.key = key; throw err; }
  function cleanText(value, max) {
    return String(value == null ? "" : value).replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, max);
  }
  function isHex(value) { return /^#[0-9a-f]{3}$|^#[0-9a-f]{6}$/i.test(String(value || "").trim()); }
  function normHex(value) {
    const text = String(value || "").trim().toLowerCase();
    if (/^#[0-9a-f]{3}$/.test(text)) return "#" + text.slice(1).split("").map((c) => c + c).join("");
    if (/^#[0-9a-f]{6}$/.test(text)) return text;
    fail("ui.custom.err.color");
    return "";
  }
  function num(value, lo, hi, fallback) {
    if (value == null || value === "") return fallback;
    const n = Number(value);
    if (!Number.isFinite(n)) fail("ui.custom.err.range");
    return Math.max(lo, Math.min(hi, n));
  }
  function hslToHex(h, s, l) {
    h = ((h % 360) + 360) % 360; s /= 100; l /= 100;
    const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return "#" + [f(0), f(8), f(4)].map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("");
  }
  function hexToRgbLocal(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    const n = parseInt(m[1], 16);
    return [n >> 16 & 255, n >> 8 & 255, n & 255];
  }
  function hexToHsl(hex) {
    const rgb = hexToRgbLocal(hex);
    if (!rgb) return null;
    let [r, g, b] = rgb.map((v) => v / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
    if (max === min) return [0, 0, Math.round(l * 100)];
    const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [Math.round(h * 60), Math.round(s * 100), Math.round(l * 100)];
  }
  /* A custom CSS block is the theme author's own code, stored locally and
     applied through one <style> element, but it still has to be harmless:
     no imports, no external resources, no script-like constructs and no
     unbalanced braces. Returns "" when valid, otherwise an i18n key. */
  function validateCss(css) {
    const text = String(css == null ? "" : css);
    if (!text.trim()) return "";
    if (text.length > CSS_MAX) return "ui.custom.err.css";
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text)) return "ui.custom.err.css";
    if (/@import|@charset|url\s*\(|expression\s*\(|javascript:|vbscript:|behavior\s*:|-moz-binding/i.test(text)) return "ui.custom.err.css";
    if (text.includes("<") || text.includes(">")) return "ui.custom.err.css";
    let depth = 0, paren = 0, bracket = 0, quote = "", comment = false, escaped = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i], next = text[i + 1] || "";
      if (comment) { if (c === "*" && next === "/") { comment = false; i++; } continue; }
      if (quote) { if (escaped) escaped = false; else if (c === "\\") escaped = true; else if (c === quote) quote = ""; continue; }
      if (c === "/" && next === "*") { comment = true; i++; continue; }
      if (c === '"' || c === "'") { quote = c; continue; }
      if (c === "{") depth++;
      else if (c === "}") { depth--; if (depth < 0) return "ui.custom.err.css"; }
      else if (c === "(") paren++;
      else if (c === ")") { paren--; if (paren < 0) return "ui.custom.err.css"; }
      else if (c === "[") bracket++;
      else if (c === "]") { bracket--; if (bracket < 0) return "ui.custom.err.css"; }
    }
    if (comment || quote || depth !== 0 || paren !== 0 || bracket !== 0) return "ui.custom.err.css";
    return "";
  }
  function normCss(raw) {
    const text = String(raw == null ? "" : raw).replace(/[\u0000-\u001f\u007f]/g, "").trim().slice(0, CSS_MAX);
    return validateCss(text) ? "" : text;
  }
  function safeFont(value) {
    const text = String(value == null ? "" : value).trim();
    if (!text || text.length > 300) return "";
    if (/[;{}<>\\]/.test(text) || /\/\*|\*\/|url\s*\(|expression|javascript:/i.test(text)) return "";
    if (text.split("(").length !== text.split(")").length) return "";
    return text;
  }
  function pxValue(value, lo, hi) {
    if (value == null || value === "") return "";
    const text = String(value).trim().replace(/px$/i, "");
    const n = Number(text);
    if (!Number.isFinite(n)) return "";
    return Math.max(lo, Math.min(hi, n)) + "px";
  }
  function clampNum(value, lo, hi) {
    if (value == null || value === "") return null;
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    return Math.max(lo, Math.min(hi, n));
  }
  function normVars(raw) {
    const vars = {};
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return vars;
    for (const name of VAR_NAMES) {
      const value = raw[name];
      if (value == null || value === "") continue;
      if (name === "--mm-rk") { const v = clampNum(value, 0.2, 2); if (v != null) vars[name] = String(v); }
      else if (name === "--mm-tile") { const v = pxValue(value, 100, 320); if (v) vars[name] = v; }
      else if (name === "--mm-row") { const v = pxValue(value, 32, 88); if (v) vars[name] = v; }
      else if (name === "--mm-pad-x") { const v = pxValue(value, 8, 80); if (v) vars[name] = v; }
      else if (name === "--mm-gap") { const v = pxValue(value, 4, 48); if (v) vars[name] = v; }
      else if (name === "--mm-side") { const v = pxValue(value, 160, 360); if (v) vars[name] = v; }
      else if (name === "--mm-bar") { const v = pxValue(value, 56, 120); if (v) vars[name] = v; }
      else if (name === "--mm-font" || name === "--mm-display") { const f = safeFont(value); if (f) vars[name] = f; }
      else if (name === "--mm-display-weight") { const v = clampNum(value, 100, 900); if (v != null) vars[name] = String(Math.round(v / 50) * 50); }
      else if (name === "--mm-title-track") {
        const m = /^(-?\d+(?:\.\d+)?)(em|px|%)$/.exec(String(value).trim());
        if (!m) continue;
        const n = Number(m[1]), lim = m[2] === "em" ? 0.2 : m[2] === "px" ? 10 : 20;
        if (Math.abs(n) > lim) continue;
        vars[name] = m[1] + m[2];
      } else if (name === "--mm-danger" || name === "--mm-ok") { const clean = safeToken(value); if (clean && (isHex(clean) || /^rgba?\(/i.test(clean))) vars[name] = clean; }
      else if (name === "--mm-cover-opacity") { const v = clampNum(value, 0, 0.6); if (v != null) vars[name] = String(v); }
      else if (name === "--mm-ambient-opacity") { const v = clampNum(value, 0, 0.8); if (v != null) vars[name] = String(v); }
      else if (name === "--mm-cover-blur") { const v = pxValue(value, 0, 120); if (v) vars[name] = v; }
    }
    return vars;
  }
  function defaultAccents(h, s) {
    return [hslToHex(h, Math.max(45, s), 62), hslToHex(h + 40, Math.max(45, s), 62), hslToHex(h - 40, Math.max(40, s * 0.9), 54)];
  }
  function normAccents(raw, h, s, fromTokens) {
    const list = Array.isArray(raw) ? raw : typeof raw === "string" ? [raw] : [];
    if (list.length && list.some((v) => !isHex(v))) fail("ui.custom.err.color");
    const hexes = list.filter((v) => isHex(v)).map((v) => normHex(v));
    const base = hexes.length ? hexes : (fromTokens || []).filter((v) => isHex(v)).map((v) => normHex(v));
    const fallback = defaultAccents(h, s);
    const out = [];
    for (let i = 0; i < 3; i++) out.push(base[i % base.length] || fallback[i]);
    return out;
  }
  function normLevels(raw, fallback, ranges) {
    const list = Array.isArray(raw) ? raw : [];
    return fallback.map((def, i) => num(list[i], ranges[i][0], ranges[i][1], def));
  }
  function safeToken(value) {
    const text = String(value == null ? "" : value).trim();
    if (!text || text.length > 300) return "";
    if (/[;{}<>"'\\]/.test(text) || /\/\*|\*\/|url\s*\(|expression|javascript:/i.test(text)) return "";
    if (text.split("(").length !== text.split(")").length) return "";
    return text;
  }
  function normTokens(raw, base) {
    const tokens = { dark: {}, light: {} };
    const accents = [];
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { tokens, accents };
    const blocks = raw.dark || raw.light
      ? { dark: raw.dark || {}, light: raw.light || {} }
      : { [base]: raw };
    for (const scheme of ["dark", "light"]) {
      const block = blocks[scheme];
      if (!block || typeof block !== "object") continue;
      for (const [name, value] of Object.entries(block)) {
        if (name === "--accent" || name === "--accent-2") {
          if (isHex(value) && scheme === base) accents[name === "--accent" ? 0 : 1] = normHex(value);
          continue;
        }
        const mapped = TOKEN_NAMES.includes(name) ? name : MEMU_TOKENS[name];
        if (!mapped) continue;
        const clean = safeToken(value);
        if (clean) tokens[scheme][mapped] = clean;
      }
    }
    return { tokens, accents: accents.filter(Boolean) };
  }
  function normId(raw, existing) {
    const text = String(raw || "").trim();
    if (/^custom:[a-z0-9][a-z0-9-]{0,47}$/.test(text)) return text;
    const slug = text.replace(/^custom:/, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
    if (slug) {
      const id = "custom:" + slug;
      if (!existing || !existing.includes(id)) return id;
    }
    return newId(existing);
  }
  function newId(existing) {
    for (let i = 0; i < 20; i++) {
      const id = "custom:" + Date.now().toString(36) + Math.floor(Math.random() * 1296).toString(36).padStart(2, "0") + i;
      if (!existing || !existing.includes(id)) return id;
    }
    return "custom:" + Date.now().toString(36) + Math.floor(Math.random() * 46656).toString(36);
  }

  function normalize(raw, existingIds) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) fail("ui.custom.err.object");
    if (raw.format != null && raw.format !== FORMAT && raw.format !== "memuai-theme") fail("ui.custom.err.format");
    const name = cleanText(raw.name, 80);
    if (!name) fail("ui.custom.err.name");
    const base = raw.base === "light" ? "light" : "dark";
    const h = num(raw.h != null ? raw.h : raw.hue, 0, 359, FALLBACK.h);
    const s = num(raw.s != null ? raw.s : raw.saturation, 0, 100, FALLBACK.s);
    const { tokens, accents } = normTokens(raw.tokens, base);
    /* An invalid custom CSS block is dropped, like an unsafe token: a
       damaged file must not break the theme, and the editor itself
       refuses to save one. */
    const css = normCss(raw.css);
    const ext = cleanText(raw.extends, 40);
    return {
      format: FORMAT, schema: SCHEMA,
      id: normId(raw.id, existingIds),
      name, author: cleanText(raw.author, 80), description: cleanText(raw.description, 200),
      base, extends: ext.startsWith("custom:") ? "" : ext,
      h, s, acc: normAccents(raw.acc != null ? raw.acc : raw.accents, h, s, accents),
      dyn: !!raw.dyn, mono: !!raw.mono,
      light: normLevels(raw.light, FALLBACK.light, [[85, 100], [88, 100]]),
      dark: normLevels(raw.dark, FALLBACK.dark, [[0, 30], [0, 35], [0, 1]]),
      tokens, vars: normVars(raw.vars), css,
    };
  }

  function blankDraft() {
    return { name: "", author: "", description: "", base: "dark", extends: "", h: FALLBACK.h, s: FALLBACK.s,
      acc: defaultAccents(FALLBACK.h, FALLBACK.s), dyn: false, mono: false,
      light: FALLBACK.light.slice(), dark: FALLBACK.dark.slice(), tokens: { dark: {}, light: {} },
      vars: {}, css: "" };
  }
  function draftFrom(builtin, name) {
    const draft = blankDraft();
    draft.name = name || "";
    draft.h = builtin.h != null ? builtin.h : FALLBACK.h;
    draft.s = builtin.s != null ? builtin.s : FALLBACK.s;
    draft.acc = (builtin.acc || defaultAccents(draft.h, draft.s)).slice(0, 3);
    draft.dyn = !!builtin.dyn; draft.mono = !!builtin.mono;
    draft.light = (builtin.light || FALLBACK.light).slice();
    draft.dark = (builtin.dark || FALLBACK.dark).slice();
    draft.extends = builtin.id || "";
    draft.vars = Object.assign({}, builtin.vars || {});
    draft.css = typeof builtin.css === "string" ? builtin.css : "";
    return draft;
  }
  function toBuiltin(theme) {
    const t = theme || {};
    return { id: t.id || "", h: t.h != null ? t.h : FALLBACK.h, s: t.s != null ? t.s : FALLBACK.s,
      acc: (t.acc || defaultAccents(t.h || FALLBACK.h, t.s || FALLBACK.s)).slice(0, 3),
      dyn: !!t.dyn, mono: !!t.mono,
      light: (t.light || FALLBACK.light).slice(), dark: (t.dark || FALLBACK.dark).slice(),
      custom: true, tokens: t.tokens || { dark: {}, light: {} },
      vars: t.vars || {}, css: typeof t.css === "string" ? t.css : "" };
  }
  function applyTokens(base, tokens, scheme) {
    if (!tokens || !tokens[scheme]) return base;
    const over = tokens[scheme];
    let changed = false;
    for (const name of TOKEN_NAMES) if (over[name]) changed = true;
    if (!changed) return base;
    const out = Object.assign({}, base);
    for (const name of TOKEN_NAMES) if (over[name]) out[name] = over[name];
    return out;
  }
  function loadList(raw) {
    if (!Array.isArray(raw)) return [];
    const seen = new Set(), out = [];
    for (const item of raw) {
      try {
        const theme = normalize(item, [...seen]);
        if (seen.has(theme.id)) continue;
        seen.add(theme.id); out.push(theme);
      } catch (e) { /* A damaged stored theme must not break the picker. */ }
      if (out.length >= MAX_THEMES) break;
    }
    return out;
  }
  function upsert(list, raw) {
    const theme = normalize(raw, (list || []).map((x) => x.id));
    const items = (list || []).slice();
    const at = items.findIndex((x) => x.id === theme.id);
    if (at >= 0) items[at] = theme;
    else {
      if (items.length >= MAX_THEMES) fail("ui.custom.err.limit");
      items.push(theme);
    }
    return items;
  }
  function remove(list, id) { return (list || []).filter((x) => x.id !== id); }
  function fallbackId(list, activeId) { return (list || []).some((x) => x.id === activeId) ? activeId : "cover"; }
  function isCustomId(id) { return typeof id === "string" && id.startsWith("custom:"); }
  function parseImport(text) {
    const raw = String(text || "").trim();
    if (!raw) fail("ui.custom.err.json");
    let data;
    try { data = JSON.parse(raw); } catch (e) { fail("ui.custom.err.json"); }
    return normalize(data);
  }
  /* CSS export: every variable the theme really applies, for both
     schemes, so the file can be pasted into any stylesheet and takes
     effect under :root[data-mm-scheme]. The header comment carries the
     full theme (name, palette, levels, tokens, vars, own CSS), which
     makes the CSS import lossless; a hand-written CSS file without it
     still imports from its variables alone. */
  const CSS_META = "memumusic-theme:";
  function exportCss(theme, schemes) {
    const t = normalize(theme);
    const lines = ["/* MemuMusic theme: " + t.name.replace(/\*\//g, "* /") + " */",
      "/* " + CSS_META + " " + JSON.stringify(t).replace(/\*\//g, "*\\/") + " */"];
    const varNames = Object.keys(t.vars);
    if (varNames.length) {
      lines.push(":root {");
      varNames.forEach((name) => lines.push("  " + name + ": " + t.vars[name] + ";"));
      lines.push("}");
    }
    for (const sch of ["dark", "light"]) {
      const map = (schemes && schemes[sch]) || {};
      lines.push(':root[data-mm-scheme="' + sch + '"] {');
      TOKEN_NAMES.forEach((name) => { if (map[name]) lines.push("  " + name + ": " + map[name] + ";"); });
      ["--tint", "--tint-2", "--tint-3"].forEach((name) => { if (map[name]) lines.push("  " + name + ": " + map[name] + ";"); });
      lines.push("}");
    }
    if (t.css) { lines.push(""); lines.push("/* Theme CSS */"); lines.push(t.css); }
    return lines.join("\n") + "\n";
  }
  function cssFileName(theme) { return fileName(theme).replace(/\.json$/, ".css"); }
  function parseDecls(block) {
    const out = {};
    block.split(";").forEach((part) => {
      const m = /^\s*(--[a-z0-9-]+)\s*:\s*([\s\S]*?)\s*$/.exec(part);
      if (!m) return;
      out[m[1]] = m[2].replace(/\s*!important\s*$/i, "").trim();
    });
    return out;
  }
  /* Import a .css theme file: variables from :root blocks (plain ones
     apply to both schemes, scheme-qualified ones override), plus the
     embedded theme description when present. Anything else in the file
     that survives the safety check becomes the theme's own CSS. A file
     with no recognised variable and no description is damaged, not a
     theme, and fails with a readable error instead of silently
     producing a fallback palette. */
  function parseCssImport(text, fallbackName) {
    const raw = String(text || "");
    if (!raw.trim()) fail("ui.custom.err.css_import");
    const cssErr = validateCss(raw);
    if (cssErr) fail(cssErr);
    const meta = /\/\*\s*memumusic-theme:\s*([\s\S]*?)\*\//.exec(raw);
    if (meta) {
      let data;
      try { data = JSON.parse(meta[1]); } catch (e) { fail("ui.custom.err.css_import"); }
      return normalize(data);
    }
    const shared = {}, darkDecl = {}, lightDecl = {};
    let recognised = 0;
    const blockRe = /:root(\[[^\]]*\])?\s*\{([\s\S]*?)\}/g;
    let m;
    while ((m = blockRe.exec(raw)) !== null) {
      const sel = m[1] || "", decls = parseDecls(m[2]);
      const target = /data-mm-scheme\s*=\s*["']?dark/.test(sel) ? darkDecl
        : /data-mm-scheme\s*=\s*["']?light/.test(sel) ? lightDecl : shared;
      for (const name of Object.keys(decls)) {
        if (TOKEN_NAMES.includes(name) || MEMU_TOKENS[name] || VAR_NAMES.includes(name)
          || name === "--tint" || name === "--tint-2" || name === "--tint-3"
          || name === "--accent" || name === "--accent-2") recognised++;
        target[name] = decls[name];
      }
    }
    if (!recognised) fail("ui.custom.err.css_import");
    const merged = (decl) => Object.assign({}, shared, decl);
    const dark = merged(darkDecl), light = merged(lightDecl);
    const { tokens, accents } = normTokens({ dark, light }, "dark");
    const tintOf = (decl, key) => (decl[key] && isHex(decl[key]) ? normHex(decl[key]) : "");
    const tintAcc = [tintOf(dark, "--tint") || tintOf(light, "--tint") || tintOf(shared, "--tint"),
      tintOf(dark, "--tint-2") || tintOf(light, "--tint-2") || tintOf(shared, "--tint-2"),
      tintOf(dark, "--tint-3") || tintOf(light, "--tint-3") || tintOf(shared, "--tint-3")].filter(Boolean);
    const acc = tintAcc.length ? tintAcc : accents;
    const allDecl = Object.assign({}, shared, darkDecl, lightDecl);
    const vars = {};
    VAR_NAMES.forEach((name) => { if (allDecl[name] != null) vars[name] = allDecl[name]; });
    const probe = (acc[0] && isHex(acc[0]) && acc[0])
      || (dark["--mm-bg"] && isHex(dark["--mm-bg"]) && normHex(dark["--mm-bg"])) || "";
    const hsl = probe ? hexToHsl(probe) : null;
    const stem = String(fallbackName || "").replace(/\.[^.]+$/, "").replace(/\.memumusic-theme$/i, "");
    const name = cleanText(stem.replace(/[-_]+/g, " ").trim(), 80) || "CSS theme";
    const rest = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(blockRe, "").trim();
    return normalize({
      name, base: "dark",
      h: hsl ? hsl[0] : FALLBACK.h, s: hsl ? hsl[1] : FALLBACK.s,
      acc, tokens, vars, css: rest ? normCss(rest) : "",
      light: FALLBACK.light.slice(), dark: FALLBACK.dark.slice(),
    });
  }
  /* Dispatch by file name, with a content sniff as a fallback: a .css
     file holding JSON still imports as JSON, and a .json-named CSS file
     still imports as CSS when it is not valid JSON. */
  function parseImportFile(text, fileNameArg) {
    const raw = String(text || "");
    const namedCss = /\.css$/i.test(String(fileNameArg || ""));
    if (namedCss && !/^\s*\{/.test(raw)) return parseCssImport(raw, fileNameArg);
    if (!namedCss && !/^\s*\{/.test(raw)) return parseCssImport(raw, fileNameArg);
    try { return parseImport(raw); }
    catch (e) {
      if (e && e.key === "ui.custom.err.json" && /:root|memumusic-theme:/.test(raw)) return parseCssImport(raw, fileNameArg);
      throw e;
    }
  }
  function toExport(theme) { return normalize(theme); }
  function exportText(theme) { return JSON.stringify(toExport(theme), null, 2) + "\n"; }
  function fileName(theme) {
    const slug = String((theme && theme.name) || "theme").toLowerCase()
      .replace(/[^a-z0-9\u0430-\u044f\u0451]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 48) || "theme";
    return slug + ".memumusic-theme.json";
  }

  return { FORMAT, SCHEMA, MAX_THEMES, TOKEN_NAMES, VAR_NAMES, CSS_MAX, normalize, blankDraft, draftFrom, toBuiltin,
    applyTokens, loadList, upsert, remove, fallbackId, isCustomId, isHex, normHex, hslToHex, hexToHsl,
    validateCss, isSafeToken: (v) => !!safeToken(v), parseImport, parseCssImport, parseImportFile,
    toExport, exportText, fileName, exportCss, cssFileName };
})();

if (typeof module !== "undefined" && module.exports) module.exports = CustomThemes;
if (typeof window !== "undefined") window.CustomThemes = CustomThemes;
