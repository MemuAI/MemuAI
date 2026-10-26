

const MD = (() => {
  let katexReady = null;
  function loadKatex() {
    if (window.katex) return Promise.resolve(window.katex);
    if (!katexReady) {
      katexReady = API.call("katex").then((b) => {
        const style = document.createElement("style");
        style.textContent = b.css;
        document.head.appendChild(style);
        const script = document.createElement("script");
        script.textContent = b.js;
        document.head.appendChild(script);
        return window.katex;
      }).catch((e) => { katexReady = null; throw e; });
    }
    return katexReady;
  }

  const KW = {
    py: "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case self print len range int str float list dict set tuple",
    js: "async await break case catch class const continue default delete do else export extends false finally for function if import in instanceof let new null return super switch this throw true try typeof undefined var void while yield of console",
    c: "auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false class public private protected new delete namespace using std template typename include",
    sql: "select from where and or not insert into values update set delete create table drop alter join left right inner outer on group by order having limit as distinct null is in like between primary key foreign references",
  };
  const ALIAS = { python: "py", py: "py", javascript: "js", js: "js", ts: "js", typescript: "js", java: "c", c: "c", cpp: "c", "c++": "c", cs: "c", go: "c", rust: "c", sql: "sql", "": "py" };
  function highlight(code, lang) {
    const kind = ALIAS[String(lang || "").toLowerCase()] || "c";
    const words = new Set(KW[kind].split(" "));
    const comment = kind === "py" ? "#[^\\n]*" : kind === "sql" ? "--[^\\n]*" : "\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/";
    const re = new RegExp(`(${comment})|("""[\\s\\S]*?"""|'''[\\s\\S]*?'''|"(?:\\\\.|[^"\\\\\\n])*"|'(?:\\\\.|[^'\\\\\\n])*'|\`(?:\\\\.|[^\`\\\\])*\`)|(\\b\\d+(?:\\.\\d+)?\\b)|([A-Za-z_][A-Za-z0-9_]*)(\\s*\\()?`, "g");
    let out = "", last = 0, m;
    while ((m = re.exec(code))) {
      out += esc(code.slice(last, m.index));
      if (m[1]) out += `<span class="tk-com">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="tk-str">${esc(m[2])}</span>`;
      else if (m[3]) out += `<span class="tk-num">${esc(m[3])}</span>`;
      else if (m[4]) {
        const w = m[4], low = kind === "sql" ? w.toLowerCase() : w;
        out += words.has(low) ? `<span class="tk-kw">${esc(w)}</span>` : m[5] ? `<span class="tk-fn">${esc(w)}</span>` : esc(w);
        if (m[5]) out += esc(m[5]);
      }
      last = re.lastIndex;
    }
    return out + esc(code.slice(last));
  }

  const ALLOWED = new Set(["B", "I", "U", "S", "EM", "STRONG", "BR", "DIV", "P", "SPAN", "UL", "OL", "LI", "CODE", "PRE", "SUB", "SUP",
    "TABLE", "THEAD", "TBODY", "TR", "TD", "TH", "IMG", "HR", "BLOCKQUOTE", "H1", "H2", "H3", "H4", "SMALL", "MARK", "FONT"]);
  function sanitize(html) {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    const walk = (node) => {
      [...node.children].forEach((el) => {
        if (!ALLOWED.has(el.tagName)) { el.replaceWith(...el.childNodes); return; }
        [...el.attributes].forEach((a) => {
          const keep = el.tagName === "IMG" && ((a.name === "src" && /^data:image\//i.test(a.value)) || a.name === "alt");
          if (!keep) el.removeAttribute(a.name);
        });
        if (el.tagName === "IMG" && !el.getAttribute("src")) { el.replaceWith(doc.createTextNode("[" + (el.getAttribute("alt") || "img") + "]")); return; }
        walk(el);
      });
    };
    const root = doc.body.firstElementChild;
    for (let pass = 0; pass < 4; pass++) walk(root);
    return root.innerHTML;
  }
  const HTML_RE = /<(br|div|p|b|i|u|img|span|ul|ol|li|table|sub|sup|strong|em|code|pre|font|hr)\b[^>]*>/i;

  function protect(text, store) {
    let s = String(text || "");
    s = s.replace(/```([\w+#-]*)[ \t]*\n?([\s\S]*?)```/g, (_, lang, code) => { store.push({ code: code.replace(/\n$/, ""), lang }); return `\u0000B${store.length - 1}\u0000`; });
    s = s.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (_, a, b) => { store.push({ math: a || b, display: true }); return `\u0000M${store.length - 1}\u0000`; });
    s = s.replace(/\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?![\w$])/g, (_, a, b) => { store.push({ math: a || b, display: false }); return `\u0000M${store.length - 1}\u0000`; });
    s = s.replace(/`([^`\n]+)`/g, (_, code) => { store.push({ inline: code }); return `\u0000C${store.length - 1}\u0000`; });
    return s;
  }
  function restore(html, store) {
    return html.replace(/\u0000([BMC])(\d+)\u0000/g, (_, k, i) => {
      const x = store[+i];
      if (k === "B") return `<pre class="ml-code" data-lang="${esc(x.lang || "")}"><code>${highlight(x.code, x.lang)}</code></pre>`;
      if (k === "C") return `<code>${esc(x.inline)}</code>`;
      return `<span class="ml-math${x.display ? " display" : ""}" data-tex="${esc(x.math)}">${esc(x.math)}</span>`;
    });
  }
  function inline(s) {
    return s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<i>$2</i>")
      .replace(/(^|[\s(])_([^_\n]+)_(?=[\s.,;:!?)]|$)/g, "$1<i>$2</i>").replace(/~~([^~]+)~~/g, "<s>$1</s>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<span class="ml-link" title="$2">$1</span>');
  }
  function blocks(src) {
    const lines = src.split("\n");
    let out = "", list = null, para = [], table = [];
    const flushPara = () => { if (para.length) { out += `<p>${inline(para.join("<br>"))}</p>`; para = []; } };
    const flushList = () => { if (list) { out += `<${list.tag}>${list.items.map((x) => `<li>${inline(x)}</li>`).join("")}</${list.tag}>`; list = null; } };
    const flushTable = () => {
      if (!table.length) return;
      const rows = table.filter((r) => !/^\s*\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => inline(c.trim())));
      out += `<div class="ml-table"><table><thead><tr>${rows[0].map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      table = [];
    };
    for (const raw of lines) {
      const line = raw.replace(/\s+$/, "");
      let m;
      if (/^\s*\|.*\|\s*$/.test(line)) { flushPara(); flushList(); table.push(line); continue; }
      flushTable();
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if (/^\u0000B\d+\u0000$/.test(line.trim())) { flushPara(); flushList(); out += line.trim(); continue; }
      if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flushPara(); flushList(); const lv = Math.min(4, m[1].length + 1); out += `<h${lv}>${inline(m[2])}</h${lv}>`; continue; }
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flushPara(); flushList(); out += "<hr>"; continue; }
      if ((m = line.match(/^&gt;\s?(.*)$/))) { flushPara(); flushList(); out += `<blockquote>${inline(m[1])}</blockquote>`; continue; }
      if ((m = line.match(/^\s*(?:[-*•]|(\d+)[.)])\s+(.*)$/))) {
        flushPara();
        const tag = m[1] ? "ol" : "ul";
        if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
        list.items.push(m[2]);
        continue;
      }
      if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += " " + line.trim(); continue; }
      flushList();
      para.push(line);
    }
    flushPara(); flushList(); flushTable();
    return out;
  }

  function render(text, opts) {
    const o = opts || {};
    let src = String(text || "");
    if (o.cloze) {
      src = src.replace(/\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g, (_, n, answer, hint) => {
        if (+n !== o.cloze.ord) return answer;
        return o.cloze.reveal ? `\u0001${answer}\u0002` : `\u0003${hint || "…"}\u0004`;
      });
    }
    const store = [];
    let html;
    if (HTML_RE.test(src)) html = restore(sanitize(protect(src, store).replace(/\n/g, "<br>")), store);
    else html = restore(blocks(esc(protect(src, store))), store);
    return html.replace(/\u0001([\s\S]*?)\u0002/g, '<mark class="ml-cloze on">$1</mark>')
      .replace(/\u0003([\s\S]*?)\u0004/g, '<mark class="ml-cloze">[$1]</mark>');
  }

  function typeset(root) {
    const nodes = $$(".ml-math:not([data-done])", root);
    if (!nodes.length) return;
    loadKatex().then((katex) => {
      nodes.forEach((el) => {
        el.dataset.done = "1";
        try { katex.render(el.dataset.tex || "", el, { displayMode: el.classList.contains("display"), throwOnError: false, output: "html" }); } catch (e) {  }
      });
    }).catch(() => {  });
  }
  function into(el, text, opts) { el.innerHTML = render(text, opts); typeset(el); return el; }
  function plain(text) { return String(text || "").replace(/<[^>]+>/g, " ").replace(/\{\{c\d+::([\s\S]*?)(?:::[\s\S]*?)?\}\}/g, "$1").replace(/[`*#>$]/g, "").replace(/\s+/g, " ").trim(); }
  return { render, typeset, into, plain, highlight };
})();
