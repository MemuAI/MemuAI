/* MemuAI Quick Ask · Markdown renderer.
   The same rules as md()/mdInline() of the main window (app.js) — code
   blocks highlighted by the shared MemuSyntax (syntax.js), tables, nested
   lists, quotes, KaTeX math ($…$, $$…$$, \[…\]) — trimmed to what a quick
   answer needs and without app.js's chat-only blocks (visuals, Work buttons).
   Output classes are the main window's (code-block-wrap, code-header, tok-*),
   styled by quick.css. Exposes window.QuickMarkdown { render, inline, esc }. */
(function () {
  "use strict";
  const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  const LANG_NAMES = { js: "JavaScript", javascript: "JavaScript", ts: "TypeScript", typescript: "TypeScript", py: "Python",
    python: "Python", sh: "Shell", bash: "Bash", ps: "PowerShell", ps1: "PowerShell", powershell: "PowerShell", cmd: "CMD",
    bat: "Batch", json: "JSON", html: "HTML", css: "CSS", sql: "SQL", cs: "C#", csharp: "C#", cpp: "C++", c: "C",
    go: "Go", rust: "Rust", rs: "Rust", java: "Java", kotlin: "Kotlin", yaml: "YAML", yml: "YAML", xml: "XML",
    md: "Markdown", markdown: "Markdown", php: "PHP", rb: "Ruby", ruby: "Ruby", swift: "Swift", text: "Text", code: "Code" };

  function inline(text) {
    let s = String(text || "").replace(/\u0000/g, "");
    const codes = [], links = [];
    s = s.replace(/`([^`\n]+)`/g, (_, code) => `\u0000C${codes.push(code) - 1}\u0000`);
    // Round 34: one level of parentheses inside the address (Wikipedia «…/Python_(language)») stays in the link
    s = s.replace(/\[([^\]]+)\]\((https?:(?:[^()\s]|\([^()\s]*\))+)\)/g, (_, label, url) => `\u0000L${links.push([label, url]) - 1}\u0000`);
    s = s.replace(/(^|[\s(>]|&quot;|&#39;|«)(https?:\/\/[^\s<\u0000]+)/g, (_, lead, url) => {
      let tail = "";
      for (;;) {
        const cut = url.match(/(?:&quot;|&#39;|&gt;|&lt;|[.,;:!?)\]»])$/);
        if (!cut) break;
        // Round 34: a closing «)» that has its «(» inside the address belongs to it (…/path_(x))
        if (cut[0] === ")" && (url.match(/\(/g) || []).length >= (url.match(/\)/g) || []).length) break;
        tail = cut[0] + tail;
        url = url.slice(0, -cut[0].length);
      }
      if (!/^https?:\/\/[^/]/.test(url)) return lead + url + tail;
      return `${lead}\u0000L${links.push([url, url, true]) - 1}\u0000${tail}`;
    });
    const format = (value) => value
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = format(s);
    const host = (url) => { try { return new URL(url.replace(/&amp;/g, "&")).host; } catch (e) { return ""; } };
    s = s.replace(/\u0000L(\d+)\u0000/g, (_, i) => `<a href="${links[+i][1]}" target="_blank" rel="noreferrer" title="${host(links[+i][1])}">${links[+i][2] ? links[+i][0] : format(links[+i][0])}</a>`);
    s = s.replace(/\u0000C(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
    return s;
  }

  function highlight(code, lang) {
    if (window.MemuSyntax && (MemuSyntax.supports(lang) || !lang)) {
      try { return MemuSyntax.highlight(code, lang).join("\n"); } catch (e) { /* plain below */ }
    }
    return esc(code);
  }

  /* Round 30: inline math never starts or ends with «$» — «$$$» in a price column was a red KaTeX error. */
  function render(text, { streaming = false, labels = {} } = {}) {
    const maths = [];
    let raw = String(text || "").replace(/\r\n?/g, "\n").replace(/@@(BLOCK|MATH)(\d+)@@/g, "@\u200b@$1$2@@");
    // <think>…</think> of reasoning models is not part of the answer.
    raw = raw.replace(/<think>[\s\S]*?(<\/think>|$)/g, "");
    // Round 29: a table / diagram still being written is a placeholder (tables.js, diagrams.js).
    if (streaming && window.MemuTables) raw = MemuTables.maskPending(raw);
    // Round 34 (r34-quick-design): a list marker still without its text («2», «2.», «-») flashed as a paragraph and the
    // answer jumped 8 px when the item arrived — while streaming it waits for its text.
    if (streaming) raw = raw.replace(/\n[ \t]*(\d{1,3}[.)]?|[-*+])[ \t]*$/, "\n");
    if (streaming && window.MemuDiagrams) raw = MemuDiagrams.maskPending(raw);
    if (streaming) {
      // An unfinished fence still renders as a code block while it streams.
      const fences = (raw.match(/^```/gm) || []).length;
      if (fences % 2) raw += "\n```";
    }
    const mathPass = (part) => part.split(/(`[^`\n]+`)/g).map((piece, j) => j % 2 ? piece : piece.replace(
      /\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|(?<![\\\w$])\$(?=[^\s$])([^\n$]*?[^\s$])\$(?![\d$])/g,
      (_, bracket, block, inl) => `@@MATH${maths.push({ tex: bracket ?? block ?? inl, display: bracket !== undefined || block !== undefined }) - 1}@@`)).join("");
    raw = raw.split(/(```[\s\S]*?```)/g).map((part, i) => i % 2 ? part : mathPass(part)).join("");
    const blocks = [];
    let source = raw.replace(/```([\w.+#-]*)[^\n]*\n?([\s\S]*?)```/g, (_, lang, body, offset, all) => {
      const l = (lang || "code").toLowerCase();
      // Round 34: a fence inside a list item is indented — its lines carried that indent and the closing «   ```» left an
      // empty last line in the block; the fence's own indent is taken off every line.
      const lead = all.slice(all.lastIndexOf("\n", offset - 1) + 1, offset);
      let code = body.replace(/\n[ \t]*$/, "");
      if (lead && !lead.trim()) {
        const cutLead = new RegExp("^[ \\t]{0," + lead.replace(/\t/g, "    ").length + "}");
        code = code.split("\n").map((line) => line.replace(cutLead, "")).join("\n");
      }
      const lang2 = document.documentElement.lang === "en";
      for (const kit of [window.MemuTables, window.MemuDiagrams]) {  // Round 29
        const own = kit && kit.block ? kit.block(l, code, lang2) : "";
        if (own) return `\n@@BLOCK${blocks.push(own) - 1}@@\n`;
      }
      return `\n@@BLOCK${blocks.push(
        `<div class="code-block-wrap" data-lang="${esc(l)}"><div class="code-header"><span>${esc(LANG_NAMES[l] || l)}</span>` +
        `<button type="button" class="code-copy-btn" data-copy-code>${esc(labels.copy || "Copy")}</button></div>` +
        `<pre><code class="language-${esc(l)}">${highlight(code, l)}</code></pre></div>`) - 1}@@\n`;
    });
    source = esc(source);
    source = source.replace(/((?:^[ \t]*\|[^\n]+\|[ \t]*(?:\n|$))+)/gm, (match) => {
      const lines = match.trim().split(/\n/).map((l) => l.trim()).filter(Boolean);
      if (lines.length < 2 || !/^\|(?:\s*:?-+:?\s*\|)+$/.test(lines[1])) return match;
      const parse = (line) => {
        const cells = []; let cell = "", code = false;
        const body = line.slice(1, -1);
        for (let k = 0; k < body.length; k += 1) {
          const ch = body[k];
          if (ch === "\\" && body[k + 1] === "|") { cell += "|"; k += 1; continue; }
          if (ch === "`") code = !code;
          if (ch === "|" && !code) { cells.push(cell.trim()); cell = ""; continue; }
          cell += ch;
        }
        cells.push(cell.trim());
        return cells;
      };
      const head = parse(lines[0]).map((h) => `<th${h.length <= 20 ? ' class="short"' : ""}>${inline(h)}</th>`).join("");   // Round 34: see td.long
      // Round 34 (r34-quick-design-2): a sentence-long cell is marked — in a narrow window its column kept the width of one
      // word, so a comment column wrapped into four lines and every row grew to 100 px (quick-design.css td.long)
      const rows = lines.slice(2).map((r) => `<tr>${parse(r).map((c) => `<td${c.length > 32 ? ' class="long"' : ""}>${inline(c)}</td>`).join("")}</tr>`).join("");
      return `@@BLOCK${blocks.push(`<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`) - 1}@@\n`;
    });
    const out = [];
    let paragraph = [], quotes = [], list = [];
    const flushP = () => { if (paragraph.length) { out.push(`<p>${inline(paragraph.join("<br>"))}</p>`); paragraph = []; } };
    const flushQ = () => { if (quotes.length) { out.push(`<blockquote>${inline(quotes.join("<br>"))}</blockquote>`); quotes = []; } };
    const build = (items, from) => {
      const first = items[from];
      let html = `<${first.type}${first.type === "ol" && first.num !== 1 ? ` start="${first.num}"` : ""}>`;
      let i = from;
      while (i < items.length && items[i].indent >= first.indent) {
        if (items[i].indent > first.indent) {
          const [sub, next] = build(items, i);
          html = html.endsWith("</li>") ? html.slice(0, -5) + sub + "</li>" : html + `<li>${sub}</li>`;
          i = next; continue;
        }
        if (items[i].type !== first.type) break;
        // Round 34: task lists «- [ ] …» / «- [x] …» read as boxes, not as brackets
        const task = items[i].text.match(/^\[([ xX])\]\s+(.*)$/);
        html += task ? `<li class="md-task${task[1] === " " ? "" : " done"}"><span class="md-box" aria-hidden="true">${task[1] === " " ? "☐" : "☑"}</span> ${inline(task[2])}</li>`
          : `<li>${inline(items[i].text)}</li>`;
        i += 1;
      }
      return [html + `</${first.type}>`, i];
    };
    const flushL = () => {
      if (!list.length) return;
      const base = Math.min(...list.map((x) => x.indent));
      const items = list.map((x) => ({ ...x, indent: x.indent - base }));
      for (let i = 0; i < items.length;) { const [html, next] = build(items, i); out.push(html); i = next > i ? next : i + 1; }
      list = [];
    };
    const indentOf = (line) => line.match(/^\s*/)[0].replace(/\t/g, "    ").length;
    for (const line of source.split("\n")) {
      let m;
      if ((m = line.match(/^@@BLOCK(\d+)@@$/))) { flushP(); flushQ(); flushL(); out.push(blocks[+m[1]]); }
      else if ((m = line.match(/^&gt;\s?(.*)$/))) { flushP(); flushL(); quotes.push(m[1] || "&nbsp;"); }
      else if ((m = line.match(/^(#{1,6})\s+(.+)$/))) { flushP(); flushQ(); flushL(); out.push(`<h${Math.min(6, m[1].length + 2)}>${inline(m[2])}</h${Math.min(6, m[1].length + 2)}>`); }
      else if (/^\s*([-*_])\1\1+\s*$/.test(line)) { flushP(); flushQ(); flushL(); out.push("<hr>"); }
      else if ((m = line.match(/^\s*[-*+]\s+(.+)$/))) { flushP(); flushQ(); list.push({ type: "ul", indent: indentOf(line), text: m[1], num: 1 }); }
      else if ((m = line.match(/^\s*(\d+)[.)]\s+(.+)$/))) { flushP(); flushQ(); list.push({ type: "ol", indent: indentOf(line), text: m[2], num: Math.max(1, +m[1] || 1) }); }
      else if (!line.trim()) { flushP(); flushQ(); }
      else { flushQ(); flushL(); paragraph.push(line); }
    }
    flushP(); flushQ(); flushL();
    return out.join("").replace(/@@MATH(\d+)@@/g, (_, i) => {
      const item = maths[+i];
      if (!item) return "";
      if (!window.katex) return `<code class="math-src">${esc(item.tex)}</code>`;
      try { return katex.renderToString(item.tex, { displayMode: item.display, throwOnError: false, strict: false, trust: false }); }
      catch (e) { return `<code>${esc(item.tex)}</code>`; }
    });
  }

  window.QuickMarkdown = { render, inline, esc };
})();
