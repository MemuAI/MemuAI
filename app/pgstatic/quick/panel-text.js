/* Round 32 — the «text» look of a panel as real text (Quick Access: the built-in section, the window grid, the screen layer).

   window.MemuPanelText = { parse, render, renderPlain, contextOf, placeholders, defaultText, faceHtml, plainText, FILTERS }
   A panel's own look may carry `text` (app/quick_panel_look.py → clean_text):
     { mode: "flow" | "free", h: box height px (free), halo: auto|off|soft|strong,
       blocks: [{ t: template, x, y, w (% of the box), align, size (%), weight, color (""|muted|accent|up|down|#hex), italic, font }] }
   Without one, defaultText(panel) builds a template from the panel's spec (the same values the card shows).

   Template syntax (one block = one template, several lines allowed):
     {price}  {next_match.opponent}  {items[0].title}  {items[-1].title}  {{price}} (the panel-spec form works too)
     filters: {price|round:0}  {change|signed}  {date|time}  {title|trim:24}  {x|default:"нет"} — see FILTERS
     fallbacks: {title ?? label ?? "—"} — the first one that is set
     lists / conditions: {#items|limit:3}{@n}. {title} — {value}\n{/items}   (inside: fields of the row, {.} = the row itself,
       {@n} 1-based number, {@index}, {@first}/{@last}); a non-list value renders the section once when it is set;
       {^name}…{/name} renders only when name is missing / empty.
     \{ — a literal brace (a brace that is not a valid placeholder stays as typed anyway).
   Values are always escaped (no HTML from a template or from data reaches the page); a missing value renders as «—»
   (dimmed) unless |default gives another text. Everything is pure (no DOM) so node runs it in tests. */
(function (root) {
  "use strict";
  const MAX_LOOP = 50, MAX_DEPTH = 4, MAX_OUT = 6000;
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const docLang = () => (typeof document !== "undefined" && document.documentElement && document.documentElement.lang === "en" ? "en" : "ru");
  const PATH_RE = /^(?:\.|@(?:index|n|first|last|root)|[A-Za-z_\u0400-\u04FF][\w\u0400-\u04FF-]*)(?:\.[\w\u0400-\u04FF-]+|\[-?\d+\])*$/;

  function splitTop(s, sep) {
    const out = []; let cur = "", q = "";
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (q) { cur += c; if (c === "\\" && i + 1 < s.length) cur += s[++i]; else if (c === q) q = ""; continue; }
      if (c === '"' || c === "'" || c === "«") { q = c === "«" ? "»" : c; cur += c; continue; }
      if (c === sep) { out.push(cur); cur = ""; continue; }
      cur += c;
    }
    out.push(cur);
    return out;
  }

  function parseExpr(src) {
    const parts = splitTop(src, "|");
    const path = parts[0].trim();
    if (!PATH_RE.test(path)) return null;
    const filters = [];
    for (const p of parts.slice(1)) {
      const m = /^([a-z][a-z0-9_]*)\s*(?::([\s\S]*))?$/i.exec(p.trim());
      if (!m) return null;
      filters.push({ name: m[1].toLowerCase(), args: m[2] == null ? [] : splitArgs(m[2]) });
    }
    return { path, filters };
  }

  function splitArgs(a) {
    const out = []; let cur = "", q = "", quoted = false;
    for (let i = 0; i < a.length; i++) {
      const c = a[i];
      if (q) { if (c === "\\" && i + 1 < a.length) { cur += a[++i]; continue; } if (c === q) { q = ""; continue; } cur += c; continue; }
      if ((c === '"' || c === "'" || c === "«") && !cur.trim()) { q = c === "«" ? "»" : c; quoted = true; cur = ""; continue; }
      if (c === ",") { out.push(quoted ? cur : cur.trim()); cur = ""; quoted = false; continue; }
      if (quoted) continue;
      cur += c;
    }
    out.push(quoted ? cur : cur.trim());
    return out;
  }

  function parseAlts(src) {
    const alts = [];
    let cur = "", q = "";
    for (let i = 0; i < src.length; i++) {
      const c = src[i];
      if (q) { cur += c; if (c === "\\" && i + 1 < src.length) cur += src[++i]; else if (c === q) q = ""; continue; }
      if (c === '"' || c === "'" || c === "«") { q = c === "«" ? "»" : c; cur += c; continue; }
      if (c === "?" && src[i + 1] === "?") { alts.push(cur); cur = ""; i++; continue; }
      cur += c;
    }
    alts.push(cur);
    const out = [];
    for (let k = 0; k < alts.length; k++) {
      const a = alts[k].trim();
      const lit = /^(["'«])([\s\S]*)(["'»])$/.exec(a);
      if (lit && k > 0) { out.push({ lit: lit[2] }); continue; }
      const e = parseExpr(a);
      if (!e) return null;
      out.push(e);
    }
    return out;
  }

  function parse(tpl) {
    const src = String(tpl == null ? "" : tpl);
    const rootNodes = [];
    const stack = [{ children: rootNodes, path: null }];
    const errors = [];
    let text = "";
    const top = () => stack[stack.length - 1];
    const flush = () => { if (text) { top().children.push({ type: "text", v: text }); text = ""; } };
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (c === "\\" && (src[i + 1] === "{" || src[i + 1] === "}")) { text += src[i + 1]; i += 2; continue; }
      if (c !== "{") { text += c; i++; continue; }
      const dbl = src[i + 1] === "{";
      const start = i + (dbl ? 2 : 1);
      const end = src.indexOf(dbl ? "}}" : "}", start);
      const inner = end < 0 ? null : src.slice(start, end);
      if (inner == null || inner.length > 300 || inner.includes("\n") || (!dbl && inner.includes("{"))) { text += c; i++; continue; }
      const body = inner.trim();
      const tagLen = end + (dbl ? 2 : 1) - i;
      const head = body[0];
      if (head === "#" || head === "^") {
        const expr = parseExpr(body.slice(1));
        if (!expr || expr.path.startsWith("@") && expr.path !== "@root") { text += src.slice(i, i + tagLen); i += tagLen; continue; }
        if (stack.length > MAX_DEPTH) { errors.push({ code: "depth", at: i }); text += src.slice(i, i + tagLen); i += tagLen; continue; }
        flush();
        const node = { type: "section", inverted: head === "^", path: expr.path, filters: expr.filters, children: [], src: body };
        top().children.push(node);
        stack.push(node);
        i += tagLen;
        continue;
      }
      if (head === "/") {
        const name = body.slice(1).trim();
        if (stack.length > 1 && (name === top().path || name === "" || top().path.split(/[.[]/)[0] === name)) {
          flush(); stack.pop(); i += tagLen; continue;
        }
        if (PATH_RE.test(name)) { errors.push({ code: "close", name, at: i }); i += tagLen; continue; }
        text += src.slice(i, i + tagLen); i += tagLen; continue;
      }
      const alts = parseAlts(body);
      if (!alts) { text += src.slice(i, i + tagLen); i += tagLen; continue; }
      flush();
      top().children.push({ type: "var", path: alts[0].path, filters: alts[0].filters, alts, src: body });
      i += tagLen;
    }
    flush();
    while (stack.length > 1) { const n = stack.pop(); errors.push({ code: "open", name: n.path }); }
    return { nodes: rootNodes, errors };
  }

  function pathParts(path) {
    const out = [];
    String(path).replace(/\[(-?\d+)\]|([^.[\]]+)/g, (_, idx, key) => { out.push(idx != null ? +idx : key); return ""; });
    return out;
  }
  function dig(value, parts) {
    for (const part of parts) {
      if (value == null) return undefined;
      if (Array.isArray(value) && (typeof part === "number" || /^-?\d+$/.test(part))) {
        const n = +part; value = value[n < 0 ? value.length + n : n];
      } else if (typeof value === "object") value = Object.prototype.hasOwnProperty.call(value, part) ? value[part] : undefined;
      else return undefined;
    }
    return value;
  }

  function lookup(path, scopes) {
    const sc = scopes[scopes.length - 1];
    if (path === ".") return sc.item;
    if (path === "@index") return sc.index;
    if (path === "@n") return sc.index == null ? undefined : sc.index + 1;
    if (path === "@first") return sc.index === 0;
    if (path === "@last") return sc.index != null && sc.index === sc.count - 1;
    const parts = pathParts(path);
    if (parts[0] === "@root") return dig(scopes[0].item, parts.slice(1));
    if (parts[0] === "") parts.shift();
    for (let k = scopes.length - 1; k >= 0; k--) {
      const item = scopes[k].item;
      if (item != null && typeof item === "object" && !Array.isArray(item) && Object.prototype.hasOwnProperty.call(item, parts[0])) return dig(item, parts);
    }
    return undefined;
  }
  const isEmpty = (v) => v == null || v === "" || v === false || (Array.isArray(v) && !v.length)
    || (typeof v === "number" && !Number.isFinite(v)) || (typeof v === "string" && /^\s*(—|-|n\/a|null|undefined)\s*$/i.test(v));

  function toNum(v, lang) {
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    if (typeof v === "boolean" || v == null || typeof v === "object") return null;
    let s = String(v).replace(/[\u00a0\u202f\u2009\s']/g, "").replace(/[−–]/g, "-");
    const m = /[-+]?[\d.,]*\d/.exec(s);
    if (!m) return null;
    s = m[0];
    const lastDot = s.lastIndexOf("."), lastComma = s.lastIndexOf(",");
    if (lastDot >= 0 && lastComma >= 0) {
      s = lastDot > lastComma ? s.replace(/,/g, "") : s.replace(/\./g, "").replace(",", ".");
    } else if (lastComma >= 0) {
      const tail = s.length - lastComma - 1;
      const groups = (s.match(/,/g) || []).length;
      s = groups > 1 || (tail === 3 && lang === "en") ? s.replace(/,/g, "") : s.replace(",", ".");
    } else if ((s.match(/\./g) || []).length > 1) s = s.replace(/\./g, "");
    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  }
  const locale = (lang) => (lang === "en" ? "en-US" : "ru-RU");
  function fmtNum(n, lang, opts = {}) {
    try { return n.toLocaleString(locale(lang), opts); } catch (e) { return String(n); }
  }

  function toDate(v) {
    if (v instanceof Date) return Number.isFinite(v.getTime()) ? v : null;
    if (typeof v === "number" && Number.isFinite(v)) return new Date(v < 1e11 ? v * 1000 : v);
    if (typeof v !== "string" || !v.trim()) return null;
    const s = v.trim();
    if (/^\d{9,13}$/.test(s)) return toDate(+s);
    const hm = /^(\d{1,2}):(\d{2})$/.exec(s);
    if (hm) { const d = new Date(); d.setHours(+hm[1], +hm[2], 0, 0); return d; }
    if (!/\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}|^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|^[A-Za-z]{3},\s+\d{1,2}\s+[A-Za-z]{3}\s+\d{4}|^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}/.test(s)) return null;
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? s + "T00:00:00" : s);
    if (Number.isFinite(d.getTime())) return d;

    const ymd = /(\d{4})-(\d{2})-(\d{2})/.exec(s) || ((m) => (m ? [m[0], m[3], m[2].padStart(2, "0"), m[1].padStart(2, "0")] : null))(/(\d{1,2})[./](\d{1,2})[./](\d{4})/.exec(s));
    if (!ymd) return null;
    const tm = /(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AP]M)?/i.exec(s.slice(s.indexOf(ymd[0]) + ymd[0].length)) || null;
    let hh = tm ? +tm[1] : 0;
    if (tm && tm[4]) { const pm = /p/i.test(tm[4]); if (hh === 12) hh = pm ? 12 : 0; else if (pm) hh += 12; }
    const off = /(?:UTC|GMT)\s*([+\-−])\s*(\d{1,2})(?::?(\d{2}))?/i.exec(s);
    const iso = `${ymd[1]}-${ymd[2]}-${ymd[3]}T${String(hh).padStart(2, "0")}:${tm ? tm[2] : "00"}:${tm && tm[3] ? tm[3] : "00"}`
      + (off ? `${off[1] === "+" ? "+" : "-"}${off[2].padStart(2, "0")}:${off[3] || "00"}` : /\bZ\b|UTC\s*$/.test(s) ? "Z" : "");
    const d2 = new Date(iso);
    return Number.isFinite(d2.getTime()) ? d2 : null;
  }
  function relText(d, lang, now) {
    const diff = Math.round((d.getTime() - (now || Date.now())) / 1000);
    const a = Math.abs(diff);
    const en = lang === "en";
    let n, u;
    if (a < 60) return en ? (diff >= 0 ? "in a moment" : "just now") : (diff >= 0 ? "сейчас" : "только что");
    if (a < 3600) { n = Math.round(a / 60); u = en ? "min" : "мин"; }
    else if (a < 86400) { n = Math.round(a / 3600); u = en ? "h" : "ч"; }
    else { n = Math.round(a / 86400); u = en ? (n === 1 ? "day" : "days") : "дн."; }
    return diff >= 0 ? (en ? `in ${n} ${u}` : `через ${n} ${u}`) : (en ? `${n} ${u} ago` : `${n} ${u} назад`);
  }

  function show(v, lang) {
    if (v == null) return "";
    if (typeof v === "number") return Number.isFinite(v) ? fmtNum(v, lang, { maximumFractionDigits: Math.abs(v) < 1 && v !== 0 ? 6 : 2 }) : "";
    if (typeof v === "boolean") return v ? (lang === "en" ? "yes" : "да") : (lang === "en" ? "no" : "нет");
    if (Array.isArray(v)) return v.slice(0, 20).map((x) => show(x, lang)).filter(Boolean).join(", ");
    if (typeof v === "object") {
      for (const k of ["title", "name", "label", "value", "text"]) if (v[k] != null && typeof v[k] !== "object") return show(v[k], lang);
      return "";
    }
    return String(v);
  }

  const pick = (v, f) => (v == null ? v : f(v));
  const FILTERS = {
    round: (v, a, e) => pick(toNum(v, e.lang), (n) => fmtNum(+n.toFixed(Math.max(0, Math.min(8, +a[0] || 0))), e.lang, { minimumFractionDigits: 0, maximumFractionDigits: Math.max(0, Math.min(8, +a[0] || 0)) })),
    fixed: (v, a, e) => pick(toNum(v, e.lang), (n) => { const d = Math.max(0, Math.min(8, a[0] == null || a[0] === "" ? 2 : +a[0] || 0)); return fmtNum(n, e.lang, { minimumFractionDigits: d, maximumFractionDigits: d }); }),
    num: (v, a, e) => pick(toNum(v, e.lang), (n) => fmtNum(n, e.lang, { maximumFractionDigits: a[0] != null && a[0] !== "" ? Math.max(0, Math.min(8, +a[0] || 0)) : 2 })),
    abs: (v) => (typeof v === "number" ? Math.abs(v) : typeof v === "string" ? v.replace(/^\s*[-+−–]\s*/, "") : v),
    signed: (v, a, e) => {
      const n = toNum(v, e.lang);
      if (n == null) return v;
      const s = typeof v === "number" ? fmtNum(Math.abs(v), e.lang, { maximumFractionDigits: 2 }) : String(v).trim().replace(/^[-+−–]\s*/, "");
      return (n > 0 ? "+" : n < 0 ? "−" : "") + s;
    },
    tone: (v, a, e) => { const n = toNum(v, e.lang); const w = String(v == null ? "" : v).toLowerCase(); e.tone = w === "up" || n > 0 ? "up" : w === "down" || n < 0 ? "down" : ""; return v; },
    pct: (v, a, e) => pick(toNum(v, e.lang), (n) => fmtNum(n, e.lang, { maximumFractionDigits: a[0] != null && a[0] !== "" ? +a[0] || 0 : 1 }) + "%"),
    short: (v, a, e) => pick(toNum(v, e.lang), (n) => {
      const abs = Math.abs(n);
      const u = e.lang === "en" ? ["", "K", "M", "B", "T"] : ["", " тыс.", " млн", " млрд", " трлн"];
      let i = 0, x = n;
      while (Math.abs(x) >= 1000 && i < u.length - 1) { x /= 1000; i++; }
      return fmtNum(+x.toFixed(abs >= 1000 && Math.abs(x) < 10 ? 2 : 1), e.lang, { maximumFractionDigits: 2 }) + u[i];
    }),
    mul: (v, a, e) => pick(toNum(v, e.lang), (n) => n * (toNum(a[0], "en") ?? 1)),
    div: (v, a, e) => pick(toNum(v, e.lang), (n) => { const d = toNum(a[0], "en"); return d ? n / d : n; }),
    plus: (v, a, e) => pick(toNum(v, e.lang), (n) => n + (toNum(a[0], "en") ?? 0)),
    upper: (v, a, e) => pick(v, (x) => show(x, e.lang).toLocaleUpperCase()),
    lower: (v, a, e) => pick(v, (x) => show(x, e.lang).toLocaleLowerCase()),
    cap: (v, a, e) => pick(v, (x) => { const s = show(x, e.lang); return s.charAt(0).toLocaleUpperCase() + s.slice(1); }),
    trim: (v, a, e) => pick(v, (x) => { const s = show(x, e.lang).trim(); const n = Math.max(1, +a[0] || 40); return s.length > n ? s.slice(0, n).replace(/\s+\S*$/, (m) => (m.length < n / 3 ? "" : m)).trimEnd() + "…" : s; }),
    first: (v) => (Array.isArray(v) ? v[0] : typeof v === "string" ? v.split(/[,·\n]/)[0].trim() : v),
    last: (v) => (Array.isArray(v) ? v[v.length - 1] : v),
    count: (v) => (Array.isArray(v) ? v.length : v == null ? 0 : typeof v === "object" ? Object.keys(v).length : String(v).length ? 1 : 0),
    limit: (v, a) => (Array.isArray(v) ? v.slice(0, Math.max(0, +a[0] || 3)) : v),
    join: (v, a, e) => (Array.isArray(v) ? v.map((x) => show(x, e.lang)).filter(Boolean).join(a[0] != null ? a[0] : ", ") : v),
    pluck: (v, a) => (Array.isArray(v) ? v.map((x) => (x && typeof x === "object" ? dig(x, pathParts(a[0] || "title")) : x)) : v),
    date: (v, a, e) => { const d = toDate(v); if (!d) return v; const long = a[0] === "long", full = a[0] === "full";
      return d.toLocaleDateString(e.lang === "en" ? "en-GB" : "ru-RU", full ? { day: "numeric", month: "long", year: "numeric" } : long ? { weekday: "short", day: "numeric", month: "long" } : { day: "numeric", month: "short" }); },
    time: (v, a, e) => { const d = toDate(v); if (!d) return v; return d.toLocaleTimeString(e.lang === "en" ? "en-GB" : "ru-RU", a[0] === "s" ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit" }); },
    datetime: (v, a, e) => { const d = toDate(v); if (!d) return v; return d.toLocaleString(e.lang === "en" ? "en-GB" : "ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); },
    weekday: (v, a, e) => { const d = toDate(v); if (!d) return v; return d.toLocaleDateString(e.lang === "en" ? "en-GB" : "ru-RU", { weekday: a[0] === "long" ? "long" : "short" }); },
    rel: (v, a, e) => { const d = toDate(v); return d ? relText(d, e.lang, e.now) : v; },
    days: (v, a, e) => { const d = toDate(v); if (!d) return v; const n = Math.ceil((d.getTime() - (e.now || Date.now())) / 86400000); return n || 0; },
    default: (v, a) => (isEmpty(v) ? (a[0] != null ? a[0] : "") : v),
    or: (v, a) => (isEmpty(v) ? (a[0] != null ? a[0] : "") : v),
    yesno: (v, a, e) => (isEmpty(v) || v === "0" || v === 0 ? (a[1] != null ? a[1] : (e.lang === "en" ? "no" : "нет")) : (a[0] != null ? a[0] : (e.lang === "en" ? "yes" : "да"))),
    prefix: (v, a) => (isEmpty(v) ? v : (a[0] || "") + v),
    suffix: (v, a, e) => (isEmpty(v) ? v : show(v, e.lang) + (a[0] || "")),
  };
  FILTERS.ago = FILTERS.rel;
  FILTERS.or = FILTERS.default;

  function render(tpl, ctx, opts = {}) {
    const ast = typeof tpl === "string" || tpl == null ? parse(tpl) : tpl;
    const env = { lang: opts.lang || docLang(), now: opts.now || Date.now(), plain: !!opts.plain, missing: opts.missing == null ? "—" : opts.missing, budget: MAX_OUT };
    const out = walk(ast.nodes, [{ item: ctx || {}, index: null, count: 0 }], env, 0);
    return env.plain ? out : out;
  }
  const renderPlain = (tpl, ctx, opts = {}) => render(tpl, ctx, { ...opts, plain: true });
  function piece(text, env, cls) {
    if (env.budget <= 0) return "";
    const s = String(text);
    env.budget -= s.length;
    if (env.plain) return s;
    const h = esc(s).replace(/\n/g, "<br>");
    return cls ? `<span class="${cls}">${h}</span>` : h;
  }
  const FALLBACK = ["default", "or", "yesno", "count"];
  function one(alt, scopes, env) {
    if ("lit" in alt) return { text: alt.lit, missing: alt.lit === "", tone: "" };
    let v = lookup(alt.path, scopes);
    const fenv = { lang: env.lang, now: env.now, tone: "" };
    for (const f of alt.filters) {
      const fn = FILTERS[f.name];
      if (!fn) continue;
      try { v = fn(v, f.args, fenv); } catch (e) {  }
    }
    const fallback = alt.filters.some((f) => FALLBACK.includes(f.name));
    const missing = fallback ? (v == null || v === "") : (isEmpty(v) && v !== 0 && v !== false);
    return { text: missing ? "" : show(v, env.lang), missing, tone: fenv.tone };
  }
  function value(node, scopes, env) {
    let r = { text: "", missing: true, tone: "" };
    for (const alt of node.alts || [node]) { r = one(alt, scopes, env); if (!r.missing) return r; }
    return r;
  }
  function walk(nodes, scopes, env, depth) {
    let out = "";
    for (const n of nodes) {
      if (env.budget <= 0) break;
      if (n.type === "text") { out += piece(n.v, env); continue; }
      if (n.type === "var") {
        const r = value(n, scopes, env);
        if (r.missing) { out += env.missing ? piece(env.missing, env, "pt-miss") : ""; continue; }
        out += piece(r.text, env, env.plain ? "" : ["pt-v", r.tone ? "pt-" + r.tone : ""].filter(Boolean).join(" "));
        continue;
      }

      let v = lookup(n.path, scopes);
      const fenv = { lang: env.lang, now: env.now, tone: "" };
      for (const f of n.filters) { const fn = FILTERS[f.name]; if (fn) { try { v = fn(v, f.args, fenv); } catch (e) {  } } }
      const empty = isEmpty(v);
      if (n.inverted) { if (empty) out += walk(n.children, scopes, env, depth + 1); continue; }
      if (empty || depth >= MAX_DEPTH) continue;
      if (Array.isArray(v)) {
        const rows = v.slice(0, MAX_LOOP);
        rows.forEach((row, i) => { out += walk(n.children, [...scopes, { item: row, index: i, count: rows.length }], env, depth + 1); });
      } else out += walk(n.children, [...scopes, { item: typeof v === "object" ? v : scopes[scopes.length - 1].item, index: null, count: 0 }], env, depth + 1);
    }
    return out;
  }

  const P = () => root.MemuPanels || null;

  function contextOf(panel) {
    if (!panel) return {};
    const M = P();
    let data = {};
    try { data = M && typeof M.dataOf === "function" ? M.dataOf(panel) : { ...(panel.data || {}) }; } catch (e) { data = { ...(panel.data || {}) }; }
    let mini = {}, lines = [];
    try { if (M && M.miniOf) mini = M.miniOf(panel) || {}; } catch (e) { mini = {}; }
    try { if (M && M.linesOf) lines = M.linesOf(panel, 5) || []; } catch (e) { lines = []; }
    const meta = { title: panel.title || "", value: mini.value || "", unit: mini.unit || "", label: mini.label || "", trend: mini.trend || "",
      delta: mini.delta || "", kind: panel.kind || "", updated: panel.last_refresh || null, lines: lines.map((l) => ({ label: l.label || "", value: l.value || "", unit: l.unit || "", trend: l.trend || "" })) };
    const ctx = { ...data, panel: meta };
    for (const k of ["title", "value", "unit", "label", "trend", "delta"]) if (!(k in ctx) || isEmpty(ctx[k])) ctx[k] = meta[k];
    if (!("updated" in ctx)) ctx.updated = meta.updated;
    if (!("now" in ctx)) ctx.now = Date.now();
    return ctx;
  }

  function placeholders(panel, opts = {}) {
    const ctx = contextOf(panel);
    const lang = opts.lang || docLang();
    const out = [];
    const seen = new Set();
    const add = (path, kind, v, extra = {}) => { if (seen.has(path) || out.length >= (opts.max || 90)) return; seen.add(path); out.push({ path, kind, preview: kind === "list" ? "" : show(v, lang).slice(0, 80), ...extra }); };
    const visit = (v, path, depth) => {
      if (v == null || typeof v === "function") return;
      if (Array.isArray(v)) {
        if (!v.length) return;
        const numeric = v.every((x) => typeof x === "number");
        add(path, "list", null, { count: v.length, numeric });
        if (numeric) { add(path + "|last", "value", v[v.length - 1]); return; }
        if (depth < 3) visit(v[0], path + "[0]", depth + 1);
        return;
      }
      if (typeof v === "object") {
        if (depth >= 3) return;
        for (const k of Object.keys(v)) if (!k.startsWith("_") && !(depth === 0 && k === "panel_title") && /^[\w\u0400-\u04FF-]+$/.test(k)) visit(v[k], path ? path + "." + k : k, depth + 1);
        return;
      }
      if (String(v).length > 400) return;
      add(path, "value", v);
    };
    const { panel: meta, now, updated, ...data } = ctx;
    for (const k of ["title", "value", "unit", "label", "trend", "delta"]) if (data[k] === meta[k]) delete data[k];
    visit(data, "", 0);
    for (const k of ["title", "value", "unit", "label", "delta"]) if (!isEmpty(meta[k])) add("panel." + k, "value", meta[k]);
    if (meta.lines.length) { add("panel.lines", "list", null, { count: meta.lines.length }); }
    if (updated) add("updated|rel", "value", relText(toDate(updated) || new Date(), lang));
    add("now|time", "value", FILTERS.time(Date.now(), [], { lang }));
    return out;
  }

  const conv = (s) => String(s == null ? "" : s).replace(/\{\{\s*([^{}]+?)\s*\}\}/g, "{$1}");
  const bindOnly = (s) => { const m = /^\s*\{\{\s*([A-Za-z_][\w.]*)\s*\}\}\s*$/.exec(String(s || "")); return m ? m[1] : ""; };
  const B = (t, o = {}) => ({ t, x: 0, y: 0, w: 100, align: "left", size: 100, weight: 500, color: "", italic: false, font: "", ...o });

  function defaultText(panel, opts = {}) {
    const lang = opts.lang || docLang();
    const tr = (ru, en) => (lang === "en" ? en : ru);
    const spec = (panel && panel.spec) || {};
    const kind = panel ? panel.kind : "";
    const blocks = [];
    const cap = () => B("{panel.title}", { size: 82, color: "muted", weight: 500 });
    const big = (t) => B(t, { size: 190, weight: 650, font: "display" });
    const sub = (t) => B(t, { size: 88, color: "muted" });
    const k = {
      weather: () => [B("{place}", { size: 82, color: "muted" }), big("{temp}{t_unit|default:°}"), sub(tr("{cond} · ощущается {feels}°", "{cond} · feels {feels}°"))],
      currency: () => [cap(), big("{rate} {panel.unit}"), sub("{delta|signed|tone} · {date_label}")],
      crypto: () => [B("{name} · {symbol|upper}", { size: 82, color: "muted" }), big("{price} {vs_sign}"), sub(tr("{delta|signed|tone} за 24 ч", "{delta|signed|tone} in 24h"))],
      stock: () => [B("{symbol} · {name}", { size: 82, color: "muted" }), big("{price} {currency_sign}"), sub("{delta|signed|tone} · {time_label}")],
      countdown: () => [cap(), big(tr("{days_left} дн.", "{days_left} d")), sub("{target_label}")],
      clock: () => [B("{clock_label}", { size: 82, color: "muted" }), big("{clock_now}"), sub("{now|date:long}")],
      timers: () => [B("{timer_label}", { size: 82, color: "muted" }), big("{timer_left}")],
      todo: () => [cap(), big("{open_count}"), sub("{todo_subtitle}")],
      notes: () => [cap(), B("{note_first}", { size: 110, weight: 550 })],
      rss: () => [B("{feed_title ?? panel.title}", { size: 82, color: "muted" }), B("{#items|limit:3}{title|trim:70}\n{/items}", { size: 96, weight: 500 })],
      news: () => [cap(), B("{#items|limit:3}{title|trim:70}\n{/items}", { size: 96, weight: 500 })],
      calendar: () => [cap(), B("{next_time} {next_title}", { size: 120, weight: 650 }), B("{#events|limit:3}{title} {value}\n{/events}", { size: 88, color: "muted" })],
      github: () => [cap(), big("{count}"), B("{#items|limit:2}{title|trim:60}\n{/items}", { size: 88, color: "muted" })],
    };
    if (k[kind] && kind !== "rss") blocks.push(...k[kind]());
    else if (kind === "rss") blocks.push(...k.rss());
    else {

      const all = [];
      const flat = (list) => { for (const b of list || []) { if (b && b.type === "row") flat(b.blocks); else if (b) all.push(b); } };
      flat(spec.blocks);
      const head = all.find((b) => b.type === "title");
      const literalHead = head && head.text && !/\{\{/.test(head.text) ? String(head.text) : "";
      blocks.push(literalHead && literalHead.trim().toLowerCase() !== String(panel.title || "").trim().toLowerCase()
        ? B(conv(literalHead), { size: 82, color: "muted", weight: 500 }) : cap());
      if (head && head.text && /\{\{/.test(head.text)) blocks.push(B(conv(head.text), { size: 104, weight: 600 }));
      const mini = spec.mini || {};
      const metric = all.find((b) => b.type === "metric");
      const mainV = mini.value || (metric && metric.value) || "";
      const mainU = mini.unit != null ? mini.unit : (metric && metric.unit) || "";
      const used = new Set();
      const rest = [];
      if (head && head.subtitle) rest.push(conv(head.subtitle));
      if (mainV) {
        used.add(bindOnly(mainV));
        let t = conv(mainV) + (mainU ? (/^\s*\{\{/.test(mainU) || /^[%°]/.test(mainU) ? "" : " ") + conv(mainU) : "");
        blocks.push(B(t, { size: 170, weight: 650, font: "display" }));
        if (metric && bindOnly(metric.value) === bindOnly(mainV)) {

          const delta = metric.delta ? (bindOnly(metric.delta) ? `{${bindOnly(metric.delta)}|signed|tone}` : conv(metric.delta)) : "";
          const tail = [delta, metric.label ? conv(metric.label) : "", metric.caption ? conv(metric.caption) : ""].filter(Boolean).join(" · ");
          if (tail) rest.unshift(tail);
        }
      }
      for (const b of all) {
        if (rest.length >= 4) break;
        if (b.type === "metric") {
          if (used.has(bindOnly(b.value))) continue;
          used.add(bindOnly(b.value));
          rest.push([b.label ? conv(b.label) + ":" : "", conv(b.value) + (b.unit ? " " + conv(b.unit) : "")].filter(Boolean).join(" "));
        } else if (b.type === "stats" && Array.isArray(b.items)) {
          rest.push(b.items.slice(0, 3).map((it) => [it.label ? conv(it.label) : "", conv(it.value) + (it.unit ? (/^[%°]/.test(it.unit) ? "" : " ") + conv(it.unit) : "")].filter(Boolean).join(" ")).join(" · "));
        } else if (b.type === "stats" && bindOnly(b.items)) {
          rest.push(`{#${bindOnly(b.items)}|limit:3}{label} {value}{^@last} · {/@last}{/${bindOnly(b.items)}}`);
        } else if ((b.type === "list" || b.type === "strip") && bindOnly(b.items)) {
          const key = bindOnly(b.items);
          rest.push(`{#${key}|limit:3}{title ?? label}{#value} — {value}{/value}\n{/${key}}`.replace(/\\n\{\/[^}]+\}$/, (m) => m));
        } else if (b.type === "list" && Array.isArray(b.items)) {
          rest.push(b.items.slice(0, 3).map((it) => conv(typeof it === "object" && it ? it.title : it)).join("\n"));
        } else if (b.type === "countdown" && bindOnly(b.target)) rest.push(`{${bindOnly(b.target)}|rel}`);
        else if ((b.type === "text" || b.type === "badge") && (b.text || b.value)) rest.push(conv(b.text || b.value));
        else if (b.type === "progress" && b.value) rest.push([b.label ? conv(b.label) + ":" : "", conv(b.value) + (b.max ? " / " + conv(b.max) : "")].filter(Boolean).join(" "));
        else if (b.type === "gauge" && b.value) rest.push([b.label ? conv(b.label) + ":" : "", conv(b.value) + (b.unit ? (/^[%°]/.test(b.unit) ? "" : " ") + conv(b.unit) : "")].filter(Boolean).join(" "));
      }
      const lines = rest.filter((x) => x && x.trim());
      if (!mainV && lines.length && blocks.length < 3) blocks.push(B(lines.shift(), { size: 130, weight: 650 }));

      const optional = (l) => { const m = /^[^{]*\{([A-Za-z_][\w.]*)(?:\[[^\]]*\])?[|}\s]/.exec(l); return m && !/\{#/.test(l) ? `{#${m[1]}}${l}\n{/${m[1]}}` : l + "\n"; };
      if (lines.length) blocks.push(B(lines.map(optional).join("").replace(/\n$/, "").replace(/\n(\{\/[^}]+\})$/, "$1"), { size: 90, color: "muted" }));
      if (blocks.length === 1) blocks.push(B("{panel.value} {panel.unit}", { size: 170, weight: 650, font: "display" }));
    }
    if (blocks[0] && blocks[0].size === 82 && blocks.length > 1) blocks[0].cap = true;
    return { mode: "flow", h: 96, halo: "auto", blocks, auto: true };
  }

  const r1p = (v) => Math.round(v * 10) / 10;
  const COLORS = { muted: "var(--mp-muted, var(--muted))", accent: "var(--accent)", up: "var(--mp-up, #3fb27f)", down: "var(--mp-down, #e5534b)" };
  function blockStyle(b, mode) {
    const css = [`--pt-size:${(+b.size || 100) / 100}`, `text-align:${b.align || "left"}`, `font-weight:${+b.weight || 500}`];
    if (b.italic) css.push("font-style:italic");
    const color = COLORS[b.color] || (/^#[0-9a-f]{6}$/i.test(b.color || "") ? b.color : "");
    if (color) css.push(`color:${color}`);

    if (mode === "free") { const x = +b.x || 0; css.push(`left:${x}%`, `top:${+b.y || 0}%`, `min-width:${Math.min(+b.w || 100, 100 - x)}%`, `max-width:${r1p(100 - x)}%`); }
    return css.join(";");
  }

  function faceHtml(panel, look, opts = {}) {
    const text = (look && look.text && Array.isArray(look.text.blocks) && look.text.blocks.length) ? look.text : defaultText(panel, opts);
    const ctx = opts.ctx || contextOf(panel);
    const mode = text.mode === "free" ? "free" : "flow";
    const st = opts.state || "live";
    const lang = opts.lang || docLang();
    const shown = text.auto && opts.titles === false ? text.blocks.filter((b) => !b.cap) : text.blocks;
    const blocksHtml = shown.map((b, i) => {
      const body = render(b.t, ctx, { lang });
      return `<div class="pt-b" data-i="${i}"${b.font ? ` data-font="${esc(b.font)}"` : ""} style="${blockStyle(b, mode)}">${body || "&nbsp;"}</div>`;
    }).join("");
    const plain = shown.map((b) => renderPlain(b.t, ctx, { lang })).join("\n").trim();
    const h = Math.max(24, Math.min(480, +text.h || 96));
    return `<div class="pl-face pl-text pt-face" data-mode="${mode}" data-halo="${esc(text.halo || "auto")}" data-state="${esc(st)}" data-panel-id="${esc(panel && panel.id)}"${text.auto ? ' data-auto="1"' : ""} style="--pt-h:${h}px" title="${esc(plain)}">${blocksHtml}</div>`;
  }

  function plainText(panel, look, opts = {}) {
    const text = (look && look.text && Array.isArray(look.text.blocks) && look.text.blocks.length) ? look.text : defaultText(panel, opts);
    const ctx = contextOf(panel);
    return text.blocks.map((b) => renderPlain(b.t, ctx, { lang: opts.lang || docLang() }).trim()).filter(Boolean).join("\n");
  }

  function lint(tpl, ctx) {
    const ast = parse(tpl);
    const out = ast.errors.map((e) => ({ code: e.code, name: e.name || "" }));
    const scan = (nodes, inLoop) => {
      for (const n of nodes) {
        if (n.type === "text") continue;
        for (const a of n.alts || [n]) for (const f of a.filters || []) if (!FILTERS[f.name]) out.push({ code: "filter", name: f.name });
        const alts = (n.alts || [n]);
        if (n.type === "var" && !inLoop && !n.path.startsWith("@") && n.path !== "." && ctx && alts.length === 1 && lookup(n.path, [{ item: ctx }]) === undefined
          && !(n.filters || []).some((f) => f.name === "default" || f.name === "or")) out.push({ code: "missing", name: n.path });
        if (n.type === "section") scan(n.children, inLoop || !n.inverted);
      }
    };
    scan(ast.nodes, false);
    return out;
  }

  const api = { parse, render, renderPlain, lookup, toNum, toDate, show, contextOf, placeholders, defaultText, faceHtml, plainText, lint, FILTERS, esc };
  root.MemuPanelText = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  try { if (typeof window !== "undefined" && window.dispatchEvent) window.dispatchEvent(new Event("memu-panel-text")); } catch (e) {  }
})(typeof window !== "undefined" ? window : globalThis);
