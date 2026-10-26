

(function () {
  "use strict";
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (text) => String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ESC[ch]);

  const LANG_NAMES = { js: "JavaScript", javascript: "JavaScript", ts: "TypeScript", typescript: "TypeScript", py: "Python",
    python: "Python", sh: "Shell", bash: "Bash", shell: "Shell", ps1: "PowerShell", powershell: "PowerShell", json: "JSON",
    html: "HTML", css: "CSS", sql: "SQL", go: "Go", rs: "Rust", rust: "Rust", java: "Java", c: "C", cpp: "C++",
    cs: "C#", csharp: "C#", kt: "Kotlin", swift: "Swift", rb: "Ruby", php: "PHP", yaml: "YAML", yml: "YAML",
    toml: "TOML", xml: "XML", diff: "Diff", md: "Markdown", markdown: "Markdown", lua: "Lua", tsx: "TSX", jsx: "JSX" };

  const KEYWORDS = new Set(("break case catch class const continue def default del do elif else enum export extends false " +
    "finally fn for from func function go if impl import in interface is lambda let match mod mut new nil none not null " +
    "or package pass private protected pub public raise return self static struct super switch this throw true try type " +
    "typeof use var void while with yield async await None True False and as assert global nonlocal").split(" "));

  function highlight(code, lang) {
    const value = String(code);
    if (value.length > 60000) return esc(value);
    if (/^diff$/i.test(lang)) {
      return value.split("\n").map((line) => {
        const cls = line.startsWith("+") ? "tk-add" : line.startsWith("-") ? "tk-del" : line.startsWith("@@") ? "tk-meta" : "";
        return cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
      }).join("\n");
    }
    const hashComments = /^(py|python|sh|bash|shell|yaml|yml|toml|rb|ps1|powershell|r)$/i.test(lang || "");
    const re = hashComments
      ? /(#[^\n]*)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g
      : /(\/\/[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_$][A-Za-z0-9_$]*)/g;
    let out = "", last = 0, m;
    while ((m = re.exec(value))) {
      out += esc(value.slice(last, m.index));
      if (m[1]) out += `<span class="tk-com">${esc(m[1])}</span>`;
      else if (m[2]) out += `<span class="tk-str">${esc(m[2])}</span>`;
      else if (m[3]) out += `<span class="tk-num">${esc(m[3])}</span>`;
      else if (m[4]) {
        const next = value.charAt(re.lastIndex);
        out += KEYWORDS.has(m[4]) ? `<span class="tk-kw">${esc(m[4])}</span>`
          : next === "(" ? `<span class="tk-fn">${esc(m[4])}</span>` : esc(m[4]);
      }
      last = re.lastIndex;
    }
    return out + esc(value.slice(last));
  }

  function safeUrl(url, image) {
    const value = String(url || "").trim();
    if (image) return /^(https:\/\/|data:image\/(png|jpe?g|gif|webp);base64,)/i.test(value) ? value : "";
    return /^(https?:\/\/|mailto:)/i.test(value) ? value : "";
  }

  const TEX = { alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η", theta: "θ",
    iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", varphi: "φ",
    chi: "χ", psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ",
    Omega: "Ω", cdot: "·", times: "×", div: "÷", pm: "±", mp: "∓", le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠",
    approx: "≈", equiv: "≡", sim: "∼", propto: "∝", infty: "∞", partial: "∂", nabla: "∇", sum: "∑", prod: "∏", int: "∫",
    oint: "∮", to: "→", rightarrow: "→", leftarrow: "←", Rightarrow: "⇒", Leftarrow: "⇐", iff: "⇔", leftrightarrow: "↔",
    in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", cup: "∪", cap: "∩", forall: "∀", exists: "∃", emptyset: "∅",
    cdots: "⋯", ldots: "…", dots: "…", degree: "°", circ: "∘", angle: "∠", perp: "⊥", parallel: "∥", neg: "¬", land: "∧",
    lor: "∨", hbar: "ℏ", ell: "ℓ", quad: " ", qquad: "  ", ",": " ", ";": " ", "!": "", left: "", right: "", displaystyle: "" };
  function texGroup(src, at) {
    if (src.charAt(at) !== "{") return null;
    let depth = 0;
    for (let k = at; k < src.length; k++) {
      if (src[k] === "{") depth++;
      else if (src[k] === "}" && --depth === 0) return [src.slice(at + 1, k), k + 1];
    }
    return null;
  }
  function texArg(src, at) {
    while (src.charAt(at) === " ") at++;
    const group = texGroup(src, at);
    if (group) return group;
    const cmd = src.slice(at).match(/^\\[a-zA-Z]+/);
    if (cmd) return [cmd[0], at + cmd[0].length];
    return at < src.length ? [src.charAt(at), at + 1] : ["", at];
  }
  function mathHtml(src) {
    let out = "", k = 0;
    while (k < src.length) {
      const ch = src[k];
      if (ch === "\\") {
        const m = src.slice(k + 1).match(/^([a-zA-Z]+|.)/);
        const name = m ? m[1] : "";
        k += 1 + name.length;
        if (name === "frac" || name === "dfrac" || name === "tfrac") {
          const a = texArg(src, k), b = texArg(src, a[1]); k = b[1];
          const wrap = (t) => /^[\w.]+$/.test(t) ? mathHtml(t) : "(" + mathHtml(t) + ")";
          out += `${wrap(a[0])}⁄${wrap(b[0])}`;
        } else if (name === "sqrt") {
          const a = texArg(src, k); k = a[1];
          out += "√" + (/^[\w.]$/.test(a[0]) ? mathHtml(a[0]) : `<span class="md-sqrt">${mathHtml(a[0])}</span>`);
        } else if (/^(text|mathrm|mathbf|mathit|operatorname|mathbb|mathcal|boldsymbol)$/.test(name)) {
          const a = texArg(src, k); k = a[1];
          out += name === "mathbf" || name === "boldsymbol" ? `<b>${mathHtml(a[0])}</b>` : /^(text|mathrm|operatorname)$/.test(name) ? `<span class="md-upright">${esc(a[0])}</span>` : mathHtml(a[0]);
        } else if (/^(sin|cos|tan|cot|log|ln|lg|exp|lim|max|min|det|arg|sec|csc|sinh|cosh|tanh|arcsin|arccos|arctan)$/.test(name)) {
          out += `<span class="md-upright">${name}</span>`;
        } else if (Object.prototype.hasOwnProperty.call(TEX, name)) {
          out += esc(TEX[name]);
        } else if (name === "{" || name === "}" || name === "%" || name === "$" || name === "#" || name === "&" || name === "_") {
          out += esc(name);
        } else out += esc("\\" + name);
        continue;
      }
      if (ch === "^" || ch === "_") {
        const a = texArg(src, k + 1); k = a[1];
        const tag = ch === "^" ? "sup" : "sub";
        out += `<${tag}>${mathHtml(a[0])}</${tag}>`;
        continue;
      }
      if (ch === "{" || ch === "}") { k++; continue; }
      out += esc(ch); k++;
    }
    return out;
  }

  function inline(text) {

    const slots = [];
    const keep = (html) => "\u0000" + (slots.push(html) - 1) + "\u0000";
    let s = String(text).replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, ticks, code) => keep(`<code>${esc(code.trim())}</code>`));

    s = s.replace(/\$\$([^$\n]+)\$\$|\\\((.+?)\\\)|\\\[(.+?)\\\]/g, (_, a, b, c) => keep(`<span class="md-math">${mathHtml((a || b || c).trim())}</span>`));
    s = s.replace(/(^|[^\\$\w])\$([^$\s][^$\n]*?[^$\s]|[^$\s])\$(?![\w$])/g, (_, lead, body) => lead + keep(`<span class="md-math">${mathHtml(body)}</span>`));
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, alt, url) => {
      const href = safeUrl(url, true);
      return href ? keep(`<img class="md-img" alt="${esc(alt)}" src="${esc(href)}" loading="lazy">`) : esc(alt);
    });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g, (_, label, url) => {
      const href = safeUrl(url, false);
      return href ? keep(`<a href="${esc(href)}" data-link="1">${inlineRest(label)}</a>`) : esc(label);
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g, (_, lead, url) =>
      lead + keep(`<a href="${esc(url)}" data-link="1">${esc(url)}</a>`));
    s = inlineRest(s);
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[+i]);
  }

  function inlineRest(text) {
    let s = esc(text).replace(/&#39;/g, "'");
    s = s.replace(/\u0000(\d+)\u0000/g, "\u0000$1\u0000");
    s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*([^*\n](?:[^*\n]|\*(?!\*))*?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    return s;
  }

  function splitRow(line) {
    let row = line.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
    return row.split(/(?<!\\)\|/).map((cell) => cell.trim().replace(/\\\|/g, "|"));
  }

  function render(source, options) {
    const opts = options || {};
    const lines = String(source == null ? "" : source).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    let codeIndex = 0;
    const isBlank = (line) => !line || !line.trim();
    const fence = /^\s{0,3}(`{3,}|~{3,})\s*([\w#+.-]*)[^\n]*$/;

    function listBlock(start) {
      const first = lines[start].match(/^(\s*)([-*+]|\d+[.)])\s+/);
      const indent = first[1].length;
      const ordered = /\d/.test(first[2]);
      const startNum = ordered ? parseInt(first[2], 10) : 1;
      const items = [];
      let j = start;
      while (j < lines.length) {
        const line = lines[j];
        const m = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
        if (m && m[1].length === indent && /\d/.test(m[2]) === ordered) {
          items.push([m[3]]);
          j++;
          continue;
        }
        if (isBlank(line)) {
          const next = lines[j + 1];
          if (next != null && (/^\s+/.test(next) && next.match(/^(\s*)/)[1].length > indent ||
              (next.match(/^(\s*)([-*+]|\d+[.)])\s+/) && next.match(/^(\s*)/)[1].length === indent))) { items[items.length - 1].push(""); j++; continue; }
          break;
        }
        if (line.match(/^(\s*)/)[1].length > indent || !/^\s*([-*+]|\d+[.)])\s/.test(line)) {
          if (!items.length) break;
          items[items.length - 1].push(line.replace(new RegExp("^\\s{0," + (indent + 4) + "}"), ""));
          j++;
          continue;
        }
        break;
      }
      const tag = ordered ? "ol" : "ul";
      const html = items.map((rows) => {
        let body = rows.join("\n");
        const task = body.match(/^\[([ xX])\]\s+/);
        let prefix = "";
        if (task) { prefix = `<span class="md-task ${task[1] === " " ? "" : "done"}"></span>`; body = body.slice(task[0].length); }
        const inner = rows.length > 1 ? render(body, opts) : inline(body);
        return `<li>${prefix}${inner}</li>`;
      }).join("");
      out.push(`<${tag}${ordered && startNum !== 1 ? ` start="${startNum}"` : ""}>${html}</${tag}>`);
      return j;
    }

    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) { i++; continue; }
      const f = line.match(fence);
      if (f) {
        const marker = f[1], lang = (f[2] || "").toLowerCase();
        const body = [];
        i++;
        while (i < lines.length && !new RegExp("^\\s{0,3}" + marker[0] + "{" + marker.length + ",}\\s*$").test(lines[i])) { body.push(lines[i]); i++; }
        const closed = i < lines.length;
        i++;
        const code = body.join("\n");
        const id = "c" + (codeIndex++);
        out.push(`<div class="md-code${closed ? "" : " open"}" data-code-id="${id}"><div class="md-code-head"><span>${esc(LANG_NAMES[lang] || lang || "code")}</span>` +
          `<button type="button" class="md-copy" data-copy="${id}">${esc(opts.copyLabel || "Copy")}</button></div>` +
          `<pre><code>${highlight(code, lang)}</code></pre></div>`);
        continue;
      }
      const mOpen = line.match(/^\s*(\$\$|\\\[)\s*(.*)$/);
      if (mOpen && !/^\s*\$\$[^$]+\$\$\s*$/.test(line)) {
        const close = mOpen[1] === "$$" ? "$$" : "\\]";
        const body = [];
        let rest = mOpen[2];
        i++;
        while (true) {
          const at = rest.indexOf(close);
          if (at >= 0) { body.push(rest.slice(0, at)); break; }
          body.push(rest);
          if (i >= lines.length) break;
          rest = lines[i]; i++;
        }
        const tex = body.join(" ").trim();
        if (tex) { out.push(`<div class="md-math md-math-block">${mathHtml(tex)}</div>`); continue; }
      }
      const h = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
      if (h) { const level = Math.min(6, h[1].length + 1); out.push(`<h${level}>${inline(h[2])}</h${level}>`); i++; continue; }
      if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s{0,3}>/.test(line)) {
        const body = [];
        while (i < lines.length && /^\s{0,3}>/.test(lines[i])) { body.push(lines[i].replace(/^\s{0,3}>\s?/, "")); i++; }
        out.push(`<blockquote>${render(body.join("\n"), opts)}</blockquote>`);
        continue;
      }
      if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) { i = listBlock(i); continue; }
      if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        const head = splitRow(line);
        const aligns = splitRow(lines[i + 1]).map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "");
        i += 2;
        const body = [];
        while (i < lines.length && lines[i].includes("|") && !isBlank(lines[i])) { body.push(splitRow(lines[i])); i++; }
        const cell = (tag, text, index) => `<${tag}${aligns[index] ? ` style="text-align:${aligns[index]}"` : ""}>${inline(text || "")}</${tag}>`;
        out.push(`<div class="md-table"><table><thead><tr>${head.map((c, k) => cell("th", c, k)).join("")}</tr></thead><tbody>` +
          body.map((row) => `<tr>${head.map((_, k) => cell("td", row[k], k)).join("")}</tr>`).join("") + "</tbody></table></div>");
        continue;
      }
      const para = [];
      while (i < lines.length && !isBlank(lines[i]) && !fence.test(lines[i]) && !/^\s{0,3}(#{1,6}\s|>|([-*_])(\s*\2){2,}\s*$)/.test(lines[i]) &&
             !(para.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i]))) { para.push(lines[i]); i++; }
      if (!para.length) { para.push(lines[i]); i++; }
      out.push(`<p>${para.map((row) => inline(row.replace(/\s+$/, ""))).join("<br>")}</p>`);
    }
    return out.join("");
  }

  window.MemuMD = { render, esc, highlight };
})();
