/* MemuAI Syntax — подсветка кода для MemuCode и блоков кода в чате.
 *
 * Токенизатор идёт по строкам и помнит состояние между ними: многострочные
 * комментарии, строки в тройных кавычках и шаблонные строки, а в HTML —
 * вложенные <script> и <style>. На выходе — массив строк HTML с классами
 * tok-* (цвета заданы в style.css и совпадают с палитрой VS Code).
 */
"use strict";

const MemuSyntax = (() => {
  const escapeHtml = (value) => String(value).replace(/[&<>"]/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  const span = (cls, text) => (text ? (cls ? `<span class="tok-${cls}">${escapeHtml(text)}</span>` : escapeHtml(text)) : "");
  const words = (text) => new Set(String(text).split(/\s+/).filter(Boolean));

  /* ------------------------------ языки семейства C ------------------------- */

  const JS_KW = words(`break case catch class const continue debugger default delete do else export extends
    finally for from function if import in instanceof let new of return static super switch throw try typeof
    var void while with yield async await get set as satisfies`);
  const TS_KW = words(`abstract declare enum implements interface keyof namespace private protected public
    readonly type infer is asserts override module unique`);
  const JS_LIT = words("true false null undefined NaN Infinity this");
  const JS_TYPES = words(`string number boolean any unknown never object symbol bigint void Array Promise Map Set
    Record Partial Readonly Pick Omit Date RegExp Error JSON Math Object String Number Boolean Symbol console
    window document globalThis`);
  const PY_KW = words(`and as assert async await break class continue def del elif else except finally for from
    global if import in is lambda nonlocal not or pass raise return try while with yield match case`);
  const PY_LIT = words("True False None self cls");
  const PY_BUILTIN = words(`print len range enumerate zip map filter sorted reversed sum min max abs any all open
    isinstance issubclass getattr setattr hasattr super int str float bool list dict set tuple bytes type object
    repr format iter next round input id hash vars dir callable staticmethod classmethod property Exception
    ValueError KeyError TypeError RuntimeError OSError Path`);
  const GO_KW = words(`break case chan const continue default defer else fallthrough for func go goto if import
    interface map package range return select struct switch type var`);
  const RS_KW = words(`as async await break const continue crate dyn else enum extern fn for if impl in let loop
    match mod move mut pub ref return static struct trait type unsafe use where while`);
  const C_KW = words(`abstract assert break case catch class const continue default do else enum extends final
    finally for goto if implements import instanceof interface native new package private protected public return
    static super switch synchronized throw throws transient try volatile while using namespace virtual override
    template typename struct union typedef sizeof extern inline operator friend delete fun val var when is object
    companion data sealed init func let guard defer protocol extension internal open lateinit echo function`);
  const C_TYPES = words(`int long short char byte float double boolean bool void string String var auto size_t
    uint int32 int64 u8 u16 u32 u64 i8 i16 i32 i64 f32 f64 usize isize str Vec Option Result Box Self error rune`);
  const C_LIT = words("true false null nil NULL nullptr this self None Some Ok Err");
  const SH_KW = words(`if then else elif fi for in do done while until case esac function return export local
    echo exit set unset source alias cd readonly shift trap`);
  const PS_KW = words(`if else elseif foreach for while do switch function param return try catch finally throw
    begin process end filter`);
  const SQL_KW = words(`select from where and or not insert into values update set delete create table drop alter
    add column primary key foreign references join left right inner outer full on group by order having limit
    offset as distinct union all index view case when then else end is null like in between exists default
    returning with desc asc count sum avg min max`);

  const LANGS = {
    js: { kw: JS_KW, lit: JS_LIT, types: JS_TYPES, line: "//", block: ["/*", "*/"], strings: "\"'`", multi: "`", regex: true },
    ts: { kw: new Set([...JS_KW, ...TS_KW]), lit: JS_LIT, types: JS_TYPES, line: "//", block: ["/*", "*/"], strings: "\"'`", multi: "`", regex: true },
    json: { kw: new Set(), lit: words("true false null"), types: new Set(), block: ["/*", "*/"], line: "//", strings: "\"", keys: true },
    py: { kw: PY_KW, lit: PY_LIT, types: words("int str float bool list dict set tuple bytes object type complex frozenset Any Optional Union List Dict Tuple Callable Iterable Path"), builtins: PY_BUILTIN, line: "#", strings: "\"'", triple: true, decorators: true },
    go: { kw: GO_KW, lit: C_LIT, types: C_TYPES, line: "//", block: ["/*", "*/"], strings: "\"'`", multi: "`" },
    rs: { kw: RS_KW, lit: C_LIT, types: C_TYPES, line: "//", block: ["/*", "*/"], strings: "\"'", macros: true },
    c: { kw: C_KW, lit: C_LIT, types: C_TYPES, line: "//", block: ["/*", "*/"], strings: "\"'", preproc: true, decorators: true },
    sh: { kw: SH_KW, lit: words("true false"), types: new Set(), line: "#", strings: "\"'", vars: true },
    ps: { kw: PS_KW, lit: words("$true $false $null"), types: new Set(), line: "#", strings: "\"'", vars: true, ci: true },
    sql: { kw: SQL_KW, lit: words("null true false"), types: words("int integer text varchar boolean date timestamp real numeric serial bigint"), line: "--", block: ["/*", "*/"], strings: "'\"", ci: true },
    yaml: { kw: new Set(), lit: words("true false null yes no on off ~"), types: new Set(), line: "#", strings: "\"'", keys: true, yamlKeys: true },
    toml: { kw: new Set(), lit: words("true false"), types: new Set(), line: "#", strings: "\"'", keys: true, sections: true },
  };

  const ALIASES = {
    javascript: "js", jsx: "js", mjs: "js", cjs: "js", node: "js",
    typescript: "ts", tsx: "ts", mts: "ts", cts: "ts",
    python: "py", pyw: "py", pyi: "py",
    golang: "go", rust: "rs",
    java: "c", kt: "c", kotlin: "c", cs: "c", csharp: "c", cpp: "c", "c++": "c", cc: "c", h: "c", hpp: "c",
    swift: "c", php: "c", dart: "c", scala: "c", groovy: "c", gradle: "c",
    bash: "sh", shell: "sh", zsh: "sh", console: "sh", bat: "sh", cmd: "sh",
    ps1: "ps", powershell: "ps", psm1: "ps",
    yml: "yaml", ini: "toml", cfg: "toml", conf: "toml", env: "toml", properties: "toml",
    jsonc: "json", json5: "json", webmanifest: "json", map: "json", lock: "json",
    htm: "html", xhtml: "html", xml: "html", svg: "html", vue: "html", svelte: "html", xaml: "html", plist: "html",
    scss: "css", less: "css", sass: "css",
    md: "md", markdown: "md", mdx: "md",
  };

  function langOf(name) {
    const raw = String(name || "").toLowerCase().trim();
    const ext = raw.includes(".") ? raw.split(".").pop() : raw;
    if (raw.endsWith("dockerfile")) return "sh";
    return ALIASES[ext] || ext;
  }

  /* ----------------------------- общий токенизатор -------------------------- */

  function codeLine(line, def, state) {
    let out = "";
    let i = 0;
    const n = line.length;
    let last = "";
    const note = (text) => { if (text.trim()) last = text.trim(); };
    const push = (cls, text) => { out += span(cls, text); note(text); };

    // Продолжение многострочного комментария или строки.
    if (state.mode === "block") {
      const end = line.indexOf(def.block[1]);
      if (end < 0) return span("com", line);
      push("com", line.slice(0, end + def.block[1].length));
      i = end + def.block[1].length;
      state.mode = "";
    } else if (state.mode === "string") {
      const close = findClose(line, 0, state.quote);
      if (close < 0) return span("str", line);
      push("str", line.slice(0, close));
      i = close;
      state.mode = "";
    }
    while (i < n) {
      const rest = line.slice(i);
      const ch = line[i];
      if (/\s/.test(ch)) {
        const m = rest.match(/^\s+/)[0];
        out += m;
        i += m.length;
        continue;
      }
      if (def.line && rest.startsWith(def.line) && !(def.line === "#" && def.vars && line[i - 1] === "$")) {
        push("com", rest);
        break;
      }
      if (def.block && rest.startsWith(def.block[0])) {
        const end = rest.indexOf(def.block[1], def.block[0].length);
        if (end < 0) { push("com", rest); state.mode = "block"; break; }
        push("com", rest.slice(0, end + def.block[1].length));
        i += end + def.block[1].length;
        continue;
      }
      if (def.preproc && ch === "#" && !line.slice(0, i).trim()) { push("meta", rest); break; }
      if (def.sections && ch === "[" && !line.slice(0, i).trim()) {
        const m = rest.match(/^\[[^\]]*\]/);
        if (m) { push("tag", m[0]); i += m[0].length; continue; }
      }
      // Строки: тройные кавычки (Python), шаблонные и обычные.
      if (def.triple && (rest.startsWith('"""') || rest.startsWith("'''"))) {
        const quote = rest.slice(0, 3);
        const close = rest.indexOf(quote, 3);
        if (close < 0) { push("str", rest); state.mode = "string"; state.quote = quote; break; }
        push("str", rest.slice(0, close + 3));
        i += close + 3;
        continue;
      }
      const prefix = def.triple ? rest.match(/^[rRbBuUfF]{1,2}(?=["'])/) : null;
      if (prefix || def.strings.includes(ch)) {
        const start = prefix ? prefix[0].length : 0;
        const quote = rest[start];
        if (def.strings.includes(quote)) {
          if (def.triple && rest.slice(start, start + 3) === quote.repeat(3)) {
            const q3 = quote.repeat(3);
            const close = rest.indexOf(q3, start + 3);
            if (close < 0) { push("str", rest); state.mode = "string"; state.quote = q3; break; }
            push("str", rest.slice(0, close + 3));
            i += close + 3;
            continue;
          }
          const close = findClose(rest, start + 1, quote);
          if (close < 0) {
            if (def.multi && def.multi.includes(quote)) { push("str", rest); state.mode = "string"; state.quote = quote; break; }
            push("str", rest);
            break;
          }
          const text = rest.slice(0, close);
          const after = rest.slice(close);
          const isKey = def.keys && /^\s*:/.test(after);
          push(isKey ? "attr" : "str", text);
          i += close;
          continue;
        }
      }
      // «/» после оператора, скобки или return — начало регулярного выражения.
      if (def.regex && ch === "/" && (!last || /[=(,:;!&|?{}\[\]]$/.test(last) || last === "return" || last === "typeof")) {
        const m = rest.match(/^\/(?![*/])(?:[^/\\\n\[]|\\.|\[(?:[^\]\\]|\\.)*\])+\/[dgimsuyv]*/);
        if (m) { push("str", m[0]); i += m[0].length; continue; }
      }
      if (def.decorators && ch === "@") {
        const m = rest.match(/^@[\w.]+/);
        if (m) { push("meta", m[0]); i += m[0].length; continue; }
      }
      if (def.vars && ch === "$") {
        const m = rest.match(/^\$(?:\{[^}]*\}|\w+|[@#?*!$0-9])/);
        if (m) {
          const low = m[0].toLowerCase();
          push(def.lit.has(low) ? "lit" : "var", m[0]);
          i += m[0].length;
          continue;
        }
      }
      if (def.yamlKeys && !line.slice(0, i).trim().replace(/^-\s*/, "")) {
        const m = rest.match(/^[\w.\/"'][\w .\/"'-]*?(?=\s*:(\s|$))/);
        if (m) { push("attr", m[0]); i += m[0].length; continue; }
      }
      if (def.keys && def.sections && !line.slice(0, i).trim()) {
        const m = rest.match(/^[\w.\-]+(?=\s*=)/);
        if (m) { push("attr", m[0]); i += m[0].length; continue; }
      }
      const num = rest.match(/^(?:0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)(?:n|[fFdDlLuU]{1,3}|[iu](?:8|16|32|64|size)|f32|f64)?\b/)
        || (ch === "." && rest.match(/^\.\d+(?:[eE][+-]?\d+)?\b/));
      if (num && !/[\w$]/.test(line[i - 1] || "")) { push("num", num[0]); i += num[0].length; continue; }
      const word = rest.match(/^[A-Za-z_$][\w$]*!?/);
      if (word) {
        let text = word[0];
        const macro = def.macros && text.endsWith("!");
        if (!macro && text.endsWith("!")) text = text.slice(0, -1);
        const key = def.ci ? text.toLowerCase() : text;
        const next = line.slice(i + text.length);
        const afterDot = /\.\s*$/.test(line.slice(0, i)) && !/\.\.\s*$/.test(line.slice(0, i));
        let cls = "";
        if (macro) cls = "fn";
        else if (def.lit.has(key)) cls = "lit";
        else if (def.kw.has(key) && !afterDot) cls = "kw";
        else if (/^\s*\(/.test(next)) cls = "fn";
        else if (def.types.has(text) || (/^[A-Z][a-z0-9]\w*$/.test(text) && !def.ci)) cls = "type";
        else if (def.builtins && def.builtins.has(text)) cls = "fn";
        else if (afterDot) cls = "prop";
        else if (def.keys && /^\s*:/.test(next) && !def.yamlKeys) cls = "attr";
        // Имя после def/class/function/fn — определение.
        if (cls !== "kw" && cls !== "lit") {
          const before = line.slice(0, i).trimEnd();
          if (/\b(def|function|fn|func)$/.test(before)) cls = "fn";
          else if (/\b(class|interface|struct|enum|trait|type|impl|extends|implements|new)$/.test(before)) cls = "type";
        }
        push(cls, text);
        i += text.length;
        continue;
      }
      const op = rest.match(/^(?:=>|===|!==|==|!=|<=|>=|&&|\|\||\?\?|\?\.|\+\+|--|\*\*|->|::|[+\-*/%=<>!&|^~?:])/);
      if (op) { push("op", op[0]); i += op[0].length; continue; }
      push("punct", ch);
      i += 1;
    }
    return out;
  }

  function findClose(text, from, quote) {
    for (let k = from; k < text.length; k += 1) {
      if (text[k] === "\\") { k += 1; continue; }
      if (text.startsWith(quote, k)) return k + quote.length;
    }
    return -1;
  }

  /* ------------------------------------ CSS --------------------------------- */

  function cssLine(line, state) {
    let out = "";
    let i = 0;
    const n = line.length;
    const push = (cls, text) => { out += span(cls, text); };
    if (state.mode === "block") {
      const end = line.indexOf("*/");
      if (end < 0) return span("com", line);
      push("com", line.slice(0, end + 2));
      i = end + 2;
      state.mode = "";
    }
    while (i < n) {
      const rest = line.slice(i);
      const ch = line[i];
      if (/\s/.test(ch)) { const m = rest.match(/^\s+/)[0]; out += m; i += m.length; continue; }
      if (rest.startsWith("/*")) {
        const end = rest.indexOf("*/", 2);
        if (end < 0) { push("com", rest); state.mode = "block"; break; }
        push("com", rest.slice(0, end + 2)); i += end + 2; continue;
      }
      if (rest.startsWith("//")) { push("com", rest); break; }
      if (ch === "{") { state.depth = (state.depth || 0) + 1; state.prop = true; push("punct", ch); i += 1; continue; }
      if (ch === "}") { state.depth = Math.max(0, (state.depth || 0) - 1); state.prop = false; push("punct", ch); i += 1; continue; }
      if (ch === ";") { state.prop = true; push("punct", ch); i += 1; continue; }
      if (ch === "\"" || ch === "'") {
        const close = findClose(rest, 1, ch);
        const text = close < 0 ? rest : rest.slice(0, close);
        push("str", text); i += text.length; continue;
      }
      if (ch === "@") { const m = rest.match(/^@[\w-]+/); push("kw", m[0]); i += m[0].length; continue; }
      const inside = (state.depth || 0) > 0;
      if (inside && state.prop) {
        const m = rest.match(/^(--[\w-]+|-?[a-zA-Z][\w-]*)(?=\s*:)/);
        if (m) { push("attr", m[0]); i += m[0].length; state.prop = false; continue; }
      }
      if (inside && !state.prop) {
        if (ch === ":") { push("punct", ch); i += 1; continue; }
        let m = rest.match(/^#[\da-fA-F]{3,8}\b/);
        if (m) { push("num", m[0]); i += m[0].length; continue; }
        m = rest.match(/^-?(?:\d+\.?\d*|\.\d+)(?:[a-zA-Z%]+)?/);
        if (m) { push("num", m[0]); i += m[0].length; continue; }
        m = rest.match(/^!important/);
        if (m) { push("kw", m[0]); i += m[0].length; continue; }
        m = rest.match(/^[a-zA-Z-][\w-]*(?=\()/);
        if (m) { push("fn", m[0]); i += m[0].length; continue; }
        m = rest.match(/^--[\w-]+/);
        if (m) { push("var", m[0]); i += m[0].length; continue; }
        m = rest.match(/^[a-zA-Z-][\w-]*/);
        if (m) { push("lit", m[0]); i += m[0].length; continue; }
        push("punct", ch); i += 1; continue;
      }
      // Селекторы
      let m = rest.match(/^[.#][\w-]+/);
      if (m) { push(ch === "." ? "type" : "fn", m[0]); i += m[0].length; continue; }
      m = rest.match(/^::?[\w-]+/);
      if (m) { push("kw", m[0]); i += m[0].length; continue; }
      m = rest.match(/^\[[^\]]*\]/);
      if (m) { push("attr", m[0]); i += m[0].length; continue; }
      m = rest.match(/^[a-zA-Z][\w-]*/);
      if (m) { push(inside && state.prop ? "attr" : "tag", m[0]); i += m[0].length; continue; }
      push("punct", ch); i += 1;
    }
    return out;
  }

  /* ------------------------------------ HTML -------------------------------- */

  function htmlLine(line, state) {
    let out = "";
    let i = 0;
    const n = line.length;
    const push = (cls, text) => { out += span(cls, text); };
    while (i < n) {
      const rest = line.slice(i);
      if (state.mode === "comment") {
        const end = rest.indexOf("-->");
        if (end < 0) { push("com", rest); return out; }
        push("com", rest.slice(0, end + 3)); i += end + 3; state.mode = ""; continue;
      }
      if (state.mode === "script" || state.mode === "style") {
        // Код внутри <script>/<style> — своим языком, до закрывающего тега.
        const closeTag = state.mode === "script" ? /<\/script\s*>/i : /<\/style\s*>/i;
        const found = rest.search(closeTag);
        const chunk = found < 0 ? rest : rest.slice(0, found);
        if (chunk) {
          out += state.mode === "script" ? codeLine(chunk, LANGS.js, state.inner) : cssLine(chunk, state.inner);
        }
        if (found < 0) return out;
        i += found;
        state.mode = "";
        continue;
      }
      if (state.mode === "tag") {
        const m = rest.match(/^\s+/);
        if (m) { out += m[0]; i += m[0].length; continue; }
        if (rest.startsWith("/>") || rest[0] === ">") {
          const close = rest.startsWith("/>") ? "/>" : ">";
          push("punct", close);
          i += close.length;
          state.mode = "";
          if (close === ">" && (state.tag === "script" || state.tag === "style")) {
            state.mode = state.tag;
            state.inner = {};
          }
          continue;
        }
        if (state.attrValue) {
          const quote = rest[0];
          if (quote === "\"" || quote === "'") {
            const close = rest.indexOf(quote, 1);
            if (close < 0) { push("str", rest); state.quote = quote; state.mode = "attr"; return out; }
            push("str", rest.slice(0, close + 1)); i += close + 1;
          } else {
            const v = rest.match(/^[^\s>]+/);
            push("str", v[0]); i += v[0].length;
          }
          state.attrValue = false;
          continue;
        }
        if (rest[0] === "=") { push("punct", "="); i += 1; state.attrValue = true; continue; }
        const attr = rest.match(/^[^\s=>/]+/);
        if (attr) { push("attr", attr[0]); i += attr[0].length; continue; }
        push("punct", rest[0]); i += 1; continue;
      }
      if (state.mode === "attr") {
        const close = rest.indexOf(state.quote);
        if (close < 0) { push("str", rest); return out; }
        push("str", rest.slice(0, close + 1)); i += close + 1;
        state.mode = "tag"; state.attrValue = false; continue;
      }
      if (rest.startsWith("<!--")) {
        const end = rest.indexOf("-->", 4);
        if (end < 0) { push("com", rest); state.mode = "comment"; return out; }
        push("com", rest.slice(0, end + 3)); i += end + 3; continue;
      }
      let m = rest.match(/^<!(?:DOCTYPE|doctype)[^>]*>/);
      if (m) { push("meta", m[0]); i += m[0].length; continue; }
      m = rest.match(/^<\/?([A-Za-z][\w:.-]*)/);
      if (m) {
        push("punct", m[0].startsWith("</") ? "</" : "<");
        push("tag", m[1]);
        i += m[0].length;
        state.mode = "tag";
        state.tag = m[0].startsWith("</") ? "" : m[1].toLowerCase();
        state.attrValue = false;
        continue;
      }
      m = rest.match(/^&[#\w]+;/);
      if (m) { push("num", m[0]); i += m[0].length; continue; }
      m = rest.match(/^\{\{[^}]*\}\}/);
      if (m) { push("var", m[0]); i += m[0].length; continue; }
      m = rest.match(/^[^<&{]+/);
      if (m) { out += escapeHtml(m[0]); i += m[0].length; continue; }
      out += escapeHtml(rest[0]); i += 1;
    }
    return out;
  }

  /* ---------------------------------- Markdown ------------------------------ */

  function mdInline(text) {
    let out = "";
    let i = 0;
    while (i < text.length) {
      const rest = text.slice(i);
      let m = rest.match(/^`[^`]+`/);
      if (m) { out += span("str", m[0]); i += m[0].length; continue; }
      m = rest.match(/^\*\*[^*]+\*\*|^__[^_]+__/);
      if (m) { out += span("kw", m[0]); i += m[0].length; continue; }
      m = rest.match(/^\*[^*\s][^*]*\*|^_[^_\s][^_]*_/);
      if (m) { out += span("type", m[0]); i += m[0].length; continue; }
      m = rest.match(/^!?\[[^\]]*\]\([^)]*\)/);
      if (m) {
        const label = m[0].match(/^!?\[[^\]]*\]/)[0];
        out += span("fn", label) + span("attr", m[0].slice(label.length));
        i += m[0].length;
        continue;
      }
      m = rest.match(/^[^`*_!\[]+/);
      if (m) { out += escapeHtml(m[0]); i += m[0].length; continue; }
      out += escapeHtml(rest[0]); i += 1;
    }
    return out;
  }

  function mdLine(line, state) {
    if (state.fence) {
      if (/^\s*(```|~~~)/.test(line)) { state.fence = null; return span("punct", line); }
      return state.fence.lang ? highlightOne(line, state.fence.lang, state.fence.state) : span("str", line);
    }
    const fence = line.match(/^\s*(```|~~~)\s*([\w+#.-]*)/);
    if (fence) {
      const lang = langOf(fence[2]);
      state.fence = { lang: RENDER[lang] || LANGS[lang] ? lang : "", state: {} };
      return span("punct", line);
    }
    let m = line.match(/^(\s*#{1,6}\s)(.*)$/);
    if (m) return span("tag", m[1]) + `<span class="tok-tag tok-strong">${mdInline(m[2])}</span>`;
    m = line.match(/^(\s*(?:[-*+]|\d+[.)])\s)(.*)$/);
    if (m) return span("kw", m[1]) + mdInline(m[2]);
    m = line.match(/^(\s*>\s?)(.*)$/);
    if (m) return span("com", m[1]) + `<span class="tok-com">${mdInline(m[2])}</span>`;
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) return span("punct", line);
    if (/^\s*\|.*\|\s*$/.test(line)) return line.split("|").map((cell) => mdInline(cell)).join(span("punct", "|"));
    return mdInline(line);
  }

  /* ----------------------------------- общий вход ---------------------------- */

  const RENDER = {
    html: (line, state) => htmlLine(line, state),
    css: (line, state) => cssLine(line, state),
    md: (line, state) => mdLine(line, state),
  };

  function highlightOne(line, lang, state) {
    if (RENDER[lang]) return RENDER[lang](line, state);
    if (LANGS[lang]) return codeLine(line, LANGS[lang], state);
    return escapeHtml(line);
  }

  function guess(text) {
    const head = String(text || "").slice(0, 400).trimStart();
    if (/^<(!doctype|html|\?xml|svg|[a-z]+[\s>])/i.test(head)) return "html";
    if (/^[{[]/.test(head) && /"\s*:/.test(head)) return "json";
    if (/^(def |class |import |from \w+ import)/m.test(head)) return "py";
    if (/^(const |let |function |import |export )/m.test(head)) return "js";
    return "";
  }

  /** Подсветить текст. lang — имя файла, расширение или название языка. */
  function highlight(text, lang) {
    const lines = String(text == null ? "" : text).replace(/\r\n?/g, "\n").split("\n");
    let name = langOf(lang);
    if (!RENDER[name] && !LANGS[name]) name = guess(text);
    const state = {};
    return lines.map((line) => {
      try { return highlightOne(line, name, state); } catch (error) { return escapeHtml(line); }
    });
  }

  /** Подсветить одну строку без памяти о соседних (строки диффа). */
  function highlightLine(line, lang) {
    return highlight(line, lang)[0] || "";
  }

  function supports(lang) {
    const name = langOf(lang);
    return !!(RENDER[name] || LANGS[name]);
  }

  return { highlight, highlightLine, supports, langOf };
})();
window.MemuSyntax = MemuSyntax;
