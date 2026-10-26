

export const ERR = { div0: "#DIV/0!", value: "#VALUE!", ref: "#REF!", name: "#NAME?", na: "#N/A", num: "#NUM!", cycle: "#CYCLE!" };
export class FormulaError { constructor(code) { this.code = code; } toString() { return this.code; } }
export const UNSUPPORTED = { unsupported: true };
const E = (code) => new FormulaError(code);
const isErr = (v) => v instanceof FormulaError;

export function colName(c) { let s = ""; c++; while (c > 0) { const m = (c - 1) % 26; s = String.fromCharCode(65 + m) + s; c = Math.floor((c - 1) / 26); } return s; }
export function colIndex(letters) { let n = 0; for (const ch of letters.toUpperCase()) n = n * 26 + ch.charCodeAt(0) - 64; return n - 1; }
export function cellRef(r, c) { return colName(c) + (r + 1); }
export function parseRef(ref) { const m = /^\$?([A-Za-z]{1,3})\$?(\d{1,7})$/.exec(String(ref).trim()); return m ? { r: +m[2] - 1, c: colIndex(m[1]) } : null; }
export function parseRange(ref) {
  const [a, b] = String(ref).replace(/\$/g, "").split(":"); const p = parseRef(a), q = parseRef(b || a);
  return p && q ? { r1: Math.min(p.r, q.r), c1: Math.min(p.c, q.c), r2: Math.max(p.r, q.r), c2: Math.max(p.c, q.c) } : null;
}

const REF_RE = /(^|[^A-Za-z0-9_.'$])(\$?)([A-Z]{1,3})(\$?)(\d{1,7})(?![A-Za-z0-9_(])/g;
export function shiftFormula(f, dr, dc) {
  return String(f).split(/("(?:[^"]|"")*")/).map((part, i) => (i % 2 ? part : part.replace(REF_RE, (m, pre, ca, col, ra, row) => {
    const c = colIndex(col) + (ca ? 0 : dc), r = +row + (ra ? 0 : dr);
    return pre + (c < 0 || r < 1 ? "#REF!" : `${ca}${colName(c)}${ra}${r}`);
  }))).join("");
}

function tokenize(src) {
  const out = []; let i = 0;
  const sheetRef = /^(?:'((?:[^']|'')+)'|([A-Za-z_\u0400-\u04FF][\w.\u0400-\u04FF]*))!/;
  const area = /^(\$?[A-Za-z]{1,3}\$?\d{1,7}(?::\$?[A-Za-z]{1,3}\$?\d{1,7})?|\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}|\$?\d{1,7}:\$?\d{1,7})(?![\w(])/;
  while (i < src.length) {
    const ch = src[i], rest = src.slice(i);
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '"') { let j = i + 1, s = ""; for (;;) { if (j >= src.length) throw E(ERR.value); if (src[j] === '"') { if (src[j + 1] === '"') { s += '"'; j += 2; continue; } break; } s += src[j++]; } out.push({ t: "str", v: s }); i = j + 1; continue; }
    let m = /^\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|^\.\d+(?:[eE][-+]?\d+)?/.exec(rest);
    if (m && !/^\d{1,7}:\$?\d/.test(rest)) { out.push({ t: "num", v: parseFloat(m[0]) }); i += m[0].length; continue; }
    m = sheetRef.exec(rest);
    if (m) { const a = area.exec(rest.slice(m[0].length)); if (!a) throw E(ERR.ref); out.push({ t: "ref", sheet: (m[1] || m[2]).replace(/''/g, "'"), ref: a[1] }); i += m[0].length + a[0].length; continue; }
    m = area.exec(rest);
    if (m) { out.push({ t: "ref", sheet: null, ref: m[1] }); i += m[0].length; continue; }
    if (ch === "#") { m = /^#(?:DIV\/0!|VALUE!|REF!|NAME\?|N\/A|NUM!|NULL!)/.exec(rest); if (m) { out.push({ t: "err", v: m[0] }); i += m[0].length; continue; } }
    m = /^[A-Za-z_][\w.]*/.exec(rest);
    if (m) { out.push({ t: "id", v: m[0].toUpperCase() }); i += m[0].length; continue; }
    m = /^(<>|<=|>=|[-+*/^&=<>%(),;:])/.exec(rest);
    if (m) { out.push({ t: "op", v: m[0] }); i += m[0].length; continue; }
    throw E(ERR.name);
  }
  return out;
}

function parse(src) {
  const toks = tokenize(src); let p = 0;
  const peek = () => toks[p], next = () => toks[p++];
  const isOp = (v) => toks[p]?.t === "op" && toks[p].v === v;
  const BIN = [["=", "<>", "<", ">", "<=", ">="], ["&"], ["+", "-"], ["*", "/"], ["^"]];
  const level = (n) => {
    if (n >= BIN.length) return unary();
    let left = level(n + 1);
    while (toks[p]?.t === "op" && BIN[n].includes(toks[p].v)) { const op = next().v; left = { k: "bin", op, a: left, b: level(n + 1) }; }
    return left;
  };
  const unary = () => {
    if (isOp("-")) { next(); return { k: "neg", a: unary() }; }
    if (isOp("+")) { next(); return unary(); }
    let node = primary();
    while (isOp("%")) { next(); node = { k: "pct", a: node }; }
    return node;
  };
  const primary = () => {
    const tk = next(); if (!tk) throw E(ERR.value);
    if (tk.t === "num") return { k: "num", v: tk.v };
    if (tk.t === "str") return { k: "str", v: tk.v };
    if (tk.t === "err") return { k: "err", v: tk.v };
    if (tk.t === "ref") return { k: "ref", sheet: tk.sheet, ref: tk.ref };
    if (tk.t === "op" && tk.v === "(") { const e = level(0); if (!isOp(")")) throw E(ERR.value); next(); return e; }
    if (tk.t === "id") {
      if (isOp("(")) {
        next(); const args = [];
        if (!isOp(")")) for (;;) { args.push(isOp(",") || isOp(";") || isOp(")") ? { k: "blank" } : level(0)); if (isOp(",") || isOp(";")) { next(); continue; } break; }
        if (!isOp(")")) throw E(ERR.value); next();
        return { k: "fn", name: tk.v.replace(/^_XLFN\./, ""), args };
      }
      if (tk.v === "TRUE" || tk.v === "FALSE") return { k: "bool", v: tk.v === "TRUE" };
      throw UNSUPPORTED;
    }
    throw E(ERR.value);
  };
  const ast = level(0);
  if (p < toks.length) throw E(ERR.value);
  return ast;
}
const astCache = new Map();
function parsed(src) {
  let a = astCache.get(src);
  if (!a) { try { a = { ast: parse(src) }; } catch (e) { a = { fail: e }; } if (astCache.size > 5000) astCache.clear(); astCache.set(src, a); }
  if (a.fail) throw a.fail;
  return a.ast;
}

export function valueOf(text) {
  if (text == null || text === "") return null;
  if (typeof text !== "string") return text;
  const s = text.trim();
  if (/^[-+]?(\d+([.,]\d+)?|\d{1,3}([ \u00a0]\d{3})+([.,]\d+)?)([eE][-+]?\d+)?%?$/.test(s)) { const n = parseFloat(s.replace(/[ \u00a0]/g, "").replace(",", ".")); return s.endsWith("%") ? n / 100 : n; }
  if (s === "TRUE" || s === "FALSE") return s === "TRUE";
  if (/^#(DIV\/0!|VALUE!|REF!|NAME\?|N\/A|NUM!)$/.test(s)) return E(s);
  return text;
}
const num = (v) => {
  if (isErr(v)) throw v;
  if (v == null) return 0;
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  const x = valueOf(String(v)); if (typeof x === "number") return x;
  throw E(ERR.value);
};
const str = (v) => { if (isErr(v)) throw v; if (v == null) return ""; if (typeof v === "boolean") return v ? "TRUE" : "FALSE"; if (typeof v === "number") return fmtNum(v); return String(v); };
const bool = (v) => { if (isErr(v)) throw v; if (typeof v === "string") { const u = v.toUpperCase(); if (u === "TRUE") return true; if (u === "FALSE") return false; throw E(ERR.value); } return !!v; };
export function fmtNum(n) { if (!isFinite(n)) return ERR.num; if (Number.isInteger(n)) return String(n); const r = +n.toPrecision(15); return String(r); }

export function display(v) { if (v == null) return ""; if (isErr(v)) return v.code; if (typeof v === "boolean") return v ? "TRUE" : "FALSE"; if (typeof v === "number") return fmtNum(v); return String(v); }

class Range { constructor(sheet, r1, c1, r2, c2, ctx) { Object.assign(this, { sheet, r1, c1, r2, c2, ctx }); }
  get rows() { return this.r2 - this.r1 + 1; } get cols() { return this.c2 - this.c1 + 1; }
  at(i, j) { return this.ctx.get(this.sheet, this.r1 + i, this.c1 + j); }
  *values() { for (let r = this.r1; r <= this.r2; r++) for (let c = this.c1; c <= this.c2; c++) yield this.ctx.get(this.sheet, r, c); }
}
function resolveRef(node, ctx) {
  const sheet = node.sheet ?? ctx.sheet;
  if (!ctx.hasSheet(sheet)) throw E(ERR.ref);
  const ref = node.ref.replace(/\$/g, "");
  let r1, c1, r2, c2;
  if (/^[A-Za-z]+:[A-Za-z]+$/.test(ref)) { const [a, b] = ref.split(":"); c1 = colIndex(a); c2 = colIndex(b); r1 = 0; r2 = Math.max(0, ctx.dims(sheet).rows - 1); }
  else if (/^\d+:\d+$/.test(ref)) { const [a, b] = ref.split(":"); r1 = +a - 1; r2 = +b - 1; c1 = 0; c2 = Math.max(0, ctx.dims(sheet).cols - 1); }
  else { const g = parseRange(ref); if (!g) throw E(ERR.ref); ({ r1, c1, r2, c2 } = g); }
  return new Range(sheet, Math.min(r1, r2), Math.min(c1, c2), Math.max(r1, r2), Math.max(c1, c2), ctx);
}
function ev(node, ctx) {
  switch (node.k) {
    case "num": case "str": case "bool": return node.v;
    case "blank": return null;
    case "err": return E(node.v);
    case "ref": { const r = resolveRef(node, ctx); return r.rows === 1 && r.cols === 1 ? r.at(0, 0) : r; }
    case "neg": return -num(scalar(ev(node.a, ctx)));
    case "pct": return num(scalar(ev(node.a, ctx))) / 100;
    case "bin": return binop(node.op, scalar(ev(node.a, ctx)), scalar(ev(node.b, ctx)));
    case "fn": { const f = FN[node.name]; if (!f) throw UNSUPPORTED; return f(node.args, ctx); }
  }
  throw E(ERR.value);
}
const scalar = (v) => (v instanceof Range ? (v.rows === 1 && v.cols === 1 ? v.at(0, 0) : E(ERR.value)) : v);
function binop(op, a, b) {
  if (isErr(a)) return a; if (isErr(b)) return b;
  switch (op) {
    case "+": return num(a) + num(b);
    case "-": return num(a) - num(b);
    case "*": return num(a) * num(b);
    case "/": { const d = num(b); if (d === 0) return E(ERR.div0); return num(a) / d; }
    case "^": return Math.pow(num(a), num(b));
    case "&": return str(a) + str(b);
  }
  const cmp = compare(a, b);
  return { "=": cmp === 0, "<>": cmp !== 0, "<": cmp < 0, ">": cmp > 0, "<=": cmp <= 0, ">=": cmp >= 0 }[op];
}
function compare(a, b) {
  if (a == null) a = typeof b === "string" ? "" : typeof b === "boolean" ? false : 0;
  if (b == null) b = typeof a === "string" ? "" : typeof a === "boolean" ? false : 0;
  const ta = typeof a, tb = typeof b;
  if (ta === "number" && tb === "number") return a === b ? 0 : a < b ? -1 : 1;
  if (ta === "string" && tb === "string") { const x = a.toLowerCase(), y = b.toLowerCase(); return x === y ? 0 : x < y ? -1 : 1; }
  const rank = (t) => ({ number: 0, string: 1, boolean: 2 }[t]);
  if (ta === tb) return a === b ? 0 : a < b ? -1 : 1;
  return rank(ta) - rank(tb);
}

const argv = (args, ctx) => args.map((a) => ev(a, ctx));

function nums(args, ctx) {
  const out = [];
  for (const v of argv(args, ctx)) {
    if (v instanceof Range) { for (const x of v.values()) { if (isErr(x)) throw x; if (typeof x === "number") out.push(x); } }
    else if (v != null) out.push(num(v));
  }
  return out;
}
function flat(v) { if (v instanceof Range) return [...v.values()]; return [v]; }
function criteria(crit) {
  if (typeof crit === "number" || typeof crit === "boolean") return (x) => compare(x, crit) === 0;
  const m = /^(<>|<=|>=|=|<|>)?(.*)$/.exec(str(crit)); const op = m[1] || "=", raw = m[2];
  const val = valueOf(raw);
  if (typeof val === "string" && /[*?]/.test(raw) && (op === "=" || op === "<>")) {
    const re = new RegExp("^" + raw.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$", "i");
    return (x) => (op === "=") === re.test(str(x));
  }
  return (x) => { if (op !== "=" && op !== "<>" && (typeof x !== typeof val || x == null)) return false; const c = compare(x, val ?? ""); return { "=": c === 0, "<>": c !== 0, "<": c < 0, ">": c > 0, "<=": c <= 0, ">=": c >= 0 }[op]; };
}
const one = (args, ctx, i) => scalar(args[i] ? ev(args[i], ctx) : null);
const roundTo = (x, d, mode) => { const f = Math.pow(10, d); const y = Math.abs(x) * f; const r = mode === "up" ? Math.ceil(y - 1e-9) : mode === "down" ? Math.floor(y + 1e-9) : Math.round(y + 1e-9); return Math.sign(x) * r / f; };
function lookupMatch(range, key, exact) {

  let best = -1;
  const n = range.rows > 1 && range.cols === 1 ? range.rows : range.cols > 1 && range.rows === 1 ? range.cols : range.rows;
  const get = (i) => (range.cols === 1 || range.rows > 1 ? range.at(i, 0) : range.at(0, i));
  for (let i = 0; i < n; i++) {
    const v = get(i);
    if (exact) { if (criteria(key)(v)) return i; continue; }
    if (v == null) continue;
    const c = compare(v, key); if (c === 0) return i; if (c < 0) best = i; else break;
  }
  return exact ? -1 : best;
}
const FN = {
  SUM: (a, c) => nums(a, c).reduce((s, x) => s + x, 0),
  PRODUCT: (a, c) => nums(a, c).reduce((s, x) => s * x, 1),
  AVERAGE: (a, c) => { const n = nums(a, c); return n.length ? n.reduce((s, x) => s + x, 0) / n.length : E(ERR.div0); },
  MIN: (a, c) => { const n = nums(a, c); return n.length ? Math.min(...n) : 0; },
  MAX: (a, c) => { const n = nums(a, c); return n.length ? Math.max(...n) : 0; },
  MEDIAN: (a, c) => { const n = nums(a, c).sort((x, y) => x - y); if (!n.length) return E(ERR.num); const m = n.length >> 1; return n.length % 2 ? n[m] : (n[m - 1] + n[m]) / 2; },
  COUNT: (a, c) => argv(a, c).reduce((s, v) => s + flat(v).filter((x) => typeof x === "number").length, 0),
  COUNTA: (a, c) => argv(a, c).reduce((s, v) => s + flat(v).filter((x) => x != null && x !== "").length, 0),
  COUNTBLANK: (a, c) => argv(a, c).reduce((s, v) => s + flat(v).filter((x) => x == null || x === "").length, 0),
  ABS: (a, c) => Math.abs(num(one(a, c, 0))),
  INT: (a, c) => Math.floor(num(one(a, c, 0))),
  SQRT: (a, c) => { const x = num(one(a, c, 0)); return x < 0 ? E(ERR.num) : Math.sqrt(x); },
  POWER: (a, c) => Math.pow(num(one(a, c, 0)), num(one(a, c, 1))),
  MOD: (a, c) => { const x = num(one(a, c, 0)), d = num(one(a, c, 1)); return d === 0 ? E(ERR.div0) : x - d * Math.floor(x / d); },
  ROUND: (a, c) => roundTo(num(one(a, c, 0)), num(one(a, c, 1) ?? 0)),
  ROUNDUP: (a, c) => roundTo(num(one(a, c, 0)), num(one(a, c, 1) ?? 0), "up"),
  ROUNDDOWN: (a, c) => roundTo(num(one(a, c, 0)), num(one(a, c, 1) ?? 0), "down"),
  PI: () => Math.PI,
  IF: (a, c) => (bool(one(a, c, 0)) ? (a[1] ? scalar(ev(a[1], c)) : true) : (a[2] ? scalar(ev(a[2], c)) : false)),
  IFS: (a, c) => { for (let i = 0; i + 1 < a.length; i += 2) if (bool(one(a, c, i))) return scalar(ev(a[i + 1], c)); return E(ERR.na); },
  IFERROR: (a, c) => { try { const v = one(a, c, 0); return isErr(v) ? one(a, c, 1) : v; } catch (e) { if (isErr(e)) return one(a, c, 1); throw e; } },
  IFNA: (a, c) => { const v = one(a, c, 0); return isErr(v) && v.code === ERR.na ? one(a, c, 1) : v; },
  ISBLANK: (a, c) => { const v = one(a, c, 0); return v == null; },
  ISNUMBER: (a, c) => typeof one(a, c, 0) === "number",
  ISTEXT: (a, c) => typeof one(a, c, 0) === "string",
  ISERROR: (a, c) => { try { return isErr(one(a, c, 0)); } catch (e) { if (isErr(e)) return true; throw e; } },
  AND: (a, c) => argv(a, c).flatMap(flat).filter((x) => x != null).every(bool),
  OR: (a, c) => argv(a, c).flatMap(flat).filter((x) => x != null).some(bool),
  NOT: (a, c) => !bool(one(a, c, 0)),
  TRUE: () => true, FALSE: () => false,
  CONCATENATE: (a, c) => argv(a, c).map((v) => str(scalar(v))).join(""),
  CONCAT: (a, c) => argv(a, c).flatMap(flat).map(str).join(""),
  TEXTJOIN: (a, c) => { const sep = str(one(a, c, 0)), skip = bool(one(a, c, 1)); return a.slice(2).flatMap((x) => flat(ev(x, c))).filter((x) => !skip || (x != null && x !== "")).map(str).join(sep); },
  LEN: (a, c) => str(one(a, c, 0)).length,
  UPPER: (a, c) => str(one(a, c, 0)).toUpperCase(),
  LOWER: (a, c) => str(one(a, c, 0)).toLowerCase(),
  PROPER: (a, c) => str(one(a, c, 0)).toLowerCase().replace(/(^|[^\p{L}])(\p{L})/gu, (m, p, l) => p + l.toUpperCase()),
  TRIM: (a, c) => str(one(a, c, 0)).trim().replace(/ {2,}/g, " "),
  LEFT: (a, c) => str(one(a, c, 0)).slice(0, a[1] ? num(one(a, c, 1)) : 1),
  RIGHT: (a, c) => { const s = str(one(a, c, 0)), n = a[1] ? num(one(a, c, 1)) : 1; return n ? s.slice(-n) : ""; },
  MID: (a, c) => { const s = str(one(a, c, 0)), st = num(one(a, c, 1)); return st < 1 ? E(ERR.value) : s.substr(st - 1, num(one(a, c, 2))); },
  SUBSTITUTE: (a, c) => str(one(a, c, 0)).split(str(one(a, c, 1))).join(str(one(a, c, 2))),
  FIND: (a, c) => { const i = str(one(a, c, 1)).indexOf(str(one(a, c, 0)), a[2] ? num(one(a, c, 2)) - 1 : 0); return i < 0 ? E(ERR.value) : i + 1; },
  SEARCH: (a, c) => { const i = str(one(a, c, 1)).toLowerCase().indexOf(str(one(a, c, 0)).toLowerCase(), a[2] ? num(one(a, c, 2)) - 1 : 0); return i < 0 ? E(ERR.value) : i + 1; },
  REPT: (a, c) => str(one(a, c, 0)).repeat(Math.max(0, num(one(a, c, 1)))),
  VALUE: (a, c) => num(one(a, c, 0)),
  TEXT: (a, c) => { const v = num(one(a, c, 0)), f = str(one(a, c, 1)); const d = (/0\.(0+)/.exec(f) || [, ""])[1].length; let s = v.toFixed(d); if (/#,##|0,0/.test(f)) s = s.replace(/\B(?=(\d{3})+(?!\d))/g, ","); return f.includes("%") ? (v * 100).toFixed(d) + "%" : s; },
  SUMIF: (a, c) => { const r = ev(a[0], c), test = criteria(one(a, c, 1)), s = a[2] ? ev(a[2], c) : r; if (!(r instanceof Range)) throw E(ERR.value); let t = 0; for (let i = 0; i < r.rows; i++) for (let j = 0; j < r.cols; j++) if (test(r.at(i, j))) { const v = s instanceof Range ? s.ctx.get(s.sheet, s.r1 + i, s.c1 + j) : s; if (typeof v === "number") t += v; } return t; },
  COUNTIF: (a, c) => { const r = ev(a[0], c), test = criteria(one(a, c, 1)); return flat(r).filter(test).length; },
  AVERAGEIF: (a, c) => { const r = ev(a[0], c), test = criteria(one(a, c, 1)), s = a[2] ? ev(a[2], c) : r; const vals = []; for (let i = 0; i < r.rows; i++) for (let j = 0; j < r.cols; j++) if (test(r.at(i, j))) { const v = s instanceof Range ? s.ctx.get(s.sheet, s.r1 + i, s.c1 + j) : s; if (typeof v === "number") vals.push(v); } return vals.length ? vals.reduce((x, y) => x + y, 0) / vals.length : E(ERR.div0); },
  SUMIFS: (a, c) => { const s = ev(a[0], c); const tests = []; for (let i = 1; i + 1 < a.length; i += 2) tests.push([ev(a[i], c), criteria(one(a, c, i + 1))]); let t = 0; for (let i = 0; i < s.rows; i++) for (let j = 0; j < s.cols; j++) if (tests.every(([r, f]) => f(r.at(i, j)))) { const v = s.at(i, j); if (typeof v === "number") t += v; } return t; },
  COUNTIFS: (a, c) => { const tests = []; for (let i = 0; i + 1 < a.length; i += 2) tests.push([ev(a[i], c), criteria(one(a, c, i + 1))]); const r0 = tests[0][0]; let t = 0; for (let i = 0; i < r0.rows; i++) for (let j = 0; j < r0.cols; j++) if (tests.every(([r, f]) => f(r.at(i, j)))) t++; return t; },
  VLOOKUP: (a, c) => { const key = one(a, c, 0), r = ev(a[1], c), col = num(one(a, c, 2)), exact = a[3] ? !bool(one(a, c, 3)) : false; if (!(r instanceof Range)) throw E(ERR.value); if (col < 1 || col > r.cols) return E(ERR.ref); const first = new Range(r.sheet, r.r1, r.c1, r.r2, r.c1, r.ctx); const i = lookupMatch(first, key, exact); return i < 0 ? E(ERR.na) : r.at(i, col - 1); },
  HLOOKUP: (a, c) => { const key = one(a, c, 0), r = ev(a[1], c), row = num(one(a, c, 2)), exact = a[3] ? !bool(one(a, c, 3)) : false; if (!(r instanceof Range)) throw E(ERR.value); if (row < 1 || row > r.rows) return E(ERR.ref); const first = new Range(r.sheet, r.r1, r.c1, r.r1, r.c2, r.ctx); const i = lookupMatch(first, key, exact); return i < 0 ? E(ERR.na) : r.at(row - 1, i); },
  XLOOKUP: (a, c) => { const key = one(a, c, 0), look = ev(a[1], c), ret = ev(a[2], c); const i = lookupMatch(look, key, true); if (i < 0) return a[3] ? one(a, c, 3) : E(ERR.na); return ret.cols === 1 ? ret.at(i, 0) : ret.at(0, i); },
  MATCH: (a, c) => { const key = one(a, c, 0), r = ev(a[1], c), type = a[2] ? num(one(a, c, 2)) : 1; const i = lookupMatch(r, key, type === 0); return i < 0 ? E(ERR.na) : i + 1; },
  INDEX: (a, c) => { const r = ev(a[0], c); if (!(r instanceof Range)) return r; let i = num(one(a, c, 1) ?? 1), j = a[2] ? num(one(a, c, 2)) : 1; if (r.rows === 1 && !a[2]) { j = i; i = 1; } if (i < 1 || j < 1 || i > r.rows || j > r.cols) return E(ERR.ref); return r.at(i - 1, j - 1); },
  ROWS: (a, c) => { const r = ev(a[0], c); return r instanceof Range ? r.rows : 1; },
  COLUMNS: (a, c) => { const r = ev(a[0], c); return r instanceof Range ? r.cols : 1; },
  ROW: (a, c) => (a[0] ? resolveRef(a[0], c).r1 + 1 : c.row + 1),
  COLUMN: (a, c) => (a[0] ? resolveRef(a[0], c).c1 + 1 : c.col + 1),
  NA: () => E(ERR.na),
};
export const FUNCTIONS = Object.keys(FN).sort();

export class Workbook {
  constructor(sheets) { this.sheets = sheets; this.memo = new Map(); this.stack = new Set(); this.unsupported = new Set(); this.byName = new Map(sheets.map((s) => [s.name.toLowerCase(), s])); }
  reindex() { this.byName = new Map(this.sheets.map((s) => [s.name.toLowerCase(), s])); this.invalidate(); }
  invalidate() { this.memo.clear(); this.unsupported.clear(); }
  sheet(name) { return this.byName.get(String(name).toLowerCase()); }
  raw(sheet, r, c) { return sheet.rows[r]?.[c] ?? ""; }
  formula(sheet, r, c) { return sheet.formulas?.[r + "," + c] || ""; }
  value(name, r, c) {
    const sheet = typeof name === "string" ? this.sheet(name) : name; if (!sheet) return E(ERR.ref);
    const key = sheet.name + "!" + r + "," + c;
    if (this.memo.has(key)) return this.memo.get(key);
    const f = this.formula(sheet, r, c);
    let v;
    if (!f) v = valueOf(this.raw(sheet, r, c));
    else if (this.stack.has(key)) return E(ERR.cycle);
    else {
      this.stack.add(key);
      const ctx = { sheet: sheet.name, row: r, col: c, get: (s, rr, cc) => this.value(s, rr, cc), hasSheet: (s) => !!this.sheet(s), dims: (s) => { const x = this.sheet(s); return { rows: x.rows.length, cols: Math.max(0, ...x.rows.slice(0, 2000).map((row) => row.length)) }; } };
      try { v = scalar(ev(parsed(f), ctx)); if (v instanceof Range) v = E(ERR.value); if (v == null) v = 0; }
      catch (e) { if (isErr(e)) v = e; else { this.unsupported.add(key); v = valueOf(this.raw(sheet, r, c)); } }
      finally { this.stack.delete(key); }
    }
    this.memo.set(key, v);
    return v;
  }
  text(name, r, c) { return display(this.value(name, r, c)); }
  cachedOnly(name, r, c) { const sheet = typeof name === "string" ? this.sheet(name) : name; return this.unsupported.has(sheet.name + "!" + r + "," + c); }
}
