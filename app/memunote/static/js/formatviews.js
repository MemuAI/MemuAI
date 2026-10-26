

import { esc, icon, t, debounce, showMenu, api } from "./core.js";
import { renderMarkdown } from "./views.js";

export class BookView {
  constructor(host, tab) {
    this.tab = tab; const book = tab.book || {};
    this.el = document.createElement("div"); this.el.className = "book-view";
    const toc = (book.chapters || []).filter((c) => c.title).slice(0, 500);
    this.el.innerHTML = `<div class="book-bar">${icon("doc")}<b class="book-title">${esc(book.title || tab.name)}</b>${book.author ? `<span class="muted small">${esc(book.author)}</span>` : ""}<span class="spacer"></span>
      ${toc.length ? `<button class="btn ghost sm" data-b="toc">${icon("outline")}${esc(t("book_toc"))}</button>` : ""}
      <button class="icon-btn" data-b="smaller" title="${esc(t("zoom_out"))}">${icon("zoomOut")}</button><button class="icon-btn" data-b="bigger" title="${esc(t("zoom_in"))}">${icon("zoomIn")}</button>
      <span class="muted small book-progress"></span></div>
      <div class="book-scroll"><article class="book-page doc-prose">${book.html || `<p class="muted">${esc(t("book_empty"))}</p>`}</article></div>`;
    host.append(this.el);
    this.scroll = this.el.querySelector(".book-scroll"); this.page = this.el.querySelector(".book-page");
    this.size = +(localStorage.getItem("mn_book_size") || 18); this.applySize();
    this.el.querySelector(".book-bar").onclick = (e) => {
      const b = e.target.closest("[data-b]"); if (!b) return;
      if (b.dataset.b === "toc") showMenu(toc.map((c) => ({ label: c.title, run: () => this.page.querySelector(`[id="${CSS.escape(c.id)}"]`)?.scrollIntoView({ block: "start" }) })), b, { alignRight: true, cls: "book-toc" });
      if (b.dataset.b === "bigger") { this.size = Math.min(32, this.size + 1); this.applySize(); }
      if (b.dataset.b === "smaller") { this.size = Math.max(12, this.size - 1); this.applySize(); }
    };
    this.page.addEventListener("click", (e) => { const a = e.target.closest("a[href]"); if (a) { e.preventDefault(); if (/^(https?:|mailto:)/i.test(a.getAttribute("href"))) api("external", { url: a.href }); } });
    const key = "mn_book_pos:" + tab.path;
    this.scroll.addEventListener("scroll", debounce(() => {
      const max = this.scroll.scrollHeight - this.scroll.clientHeight;
      const f = max > 0 ? this.scroll.scrollTop / max : 0;
      this.el.querySelector(".book-progress").textContent = Math.round(f * 100) + "%";
      try { localStorage.setItem(key, String(f)); } catch {  }
    }, 200), { passive: true });
    requestAnimationFrame(() => { const f = +(localStorage.getItem(key) || 0); if (f) this.scroll.scrollTop = f * (this.scroll.scrollHeight - this.scroll.clientHeight); });
  }
  applySize() { this.page.style.fontSize = this.size + "px"; localStorage.setItem("mn_book_size", String(this.size)); }
  text(limit = 400000) { return (this.page.innerText || "").slice(0, limit); }
  destroy() { this.el.remove(); }
}

const src = (v) => (Array.isArray(v) ? v.join("") : String(v ?? ""));
export function parseNotebook(text) {
  const nb = JSON.parse(text);
  if (!nb || !Array.isArray(nb.cells)) throw new Error("not a notebook");
  const lang = nb.metadata?.kernelspec?.language || nb.metadata?.language_info?.name || "python";
  return { nb, lang };
}
export function notebookToMarkdown(text) {
  const { nb, lang } = parseNotebook(text);
  return nb.cells.map((c) => c.cell_type === "markdown" ? src(c.source) : c.cell_type === "code" ? "```" + lang + "\n" + src(c.source) + "\n```" : src(c.source)).join("\n\n") + "\n";
}
export function notebookToScript(text) {
  const { nb, lang } = parseNotebook(text);
  const comment = /^(r|python|julia|ruby|perl|bash|sh)$/i.test(lang) ? "# " : "// ";
  return nb.cells.map((c) => c.cell_type === "code" ? src(c.source) : src(c.source).split("\n").map((l) => comment + l).join("\n")).join("\n\n") + "\n";
}
export class NotebookView {
  constructor(host, getText, opts = {}) {
    this.getText = getText; this.opts = opts;
    this.el = document.createElement("div"); this.el.className = "nb-view";
    host.append(this.el);
    this.render();
  }
  render() {
    let parsed;
    try { parsed = parseNotebook(this.getText()); }
    catch (e) { this.el.innerHTML = `<div class="empty-state">${icon("warn", "big")}<h3>${esc(t("nb_invalid"))}</h3><p class="muted">${esc(e.message)}</p></div>`; return; }
    const { nb, lang } = parsed;
    const cells = nb.cells.map((c, i) => {
      const body = src(c.source);
      if (c.cell_type === "markdown") return `<section class="nb-cell md"><div class="doc-prose">${renderMarkdown(body)}</div></section>`;
      if (c.cell_type !== "code") return `<section class="nb-cell raw"><pre>${esc(body)}</pre></section>`;
      const n = c.execution_count ?? " ";
      const outs = (c.outputs || []).map(outputHtml).join("");
      return `<section class="nb-cell code"><div class="nb-in"><span class="nb-prompt">In [${esc(n)}]:</span><pre class="nb-src"><code>${esc(body)}</code></pre></div>${outs ? `<div class="nb-out">${outs}</div>` : ""}</section>`;
    }).join("");
    const kernel = nb.metadata?.kernelspec?.display_name || lang;
    this.el.innerHTML = `<div class="nb-head muted small">${icon("code")}<span>${esc(kernel)} · ${esc(t("nb_cells", { n: nb.cells.length }))}</span></div>${cells || `<p class="muted pad">${esc(t("nb_empty"))}</p>`}`;
  }
  destroy() { this.el.remove(); }
}
function outputHtml(o) {
  const data = o.data || {};
  if (o.output_type === "stream") return `<pre class="nb-stream ${o.name === "stderr" ? "err" : ""}">${esc(src(o.text))}</pre>`;
  if (o.output_type === "error") return `<pre class="nb-stream err">${esc((o.ename || "") + ": " + (o.evalue || "") + "\n" + stripAnsi((o.traceback || []).join("\n")))}</pre>`;
  for (const type of ["image/png", "image/jpeg", "image/gif"]) {
    if (data[type]) { const b64 = src(data[type]).replace(/\s+/g, ""); if (/^[A-Za-z0-9+/=]+$/.test(b64)) return `<img class="nb-img" alt="" src="data:${type};base64,${b64}">`; }
  }
  if (data["image/svg+xml"]) return `<img class="nb-img" alt="" src="data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(src(data["image/svg+xml"]))))}">`;
  if (data["text/markdown"]) return `<div class="doc-prose">${renderMarkdown(src(data["text/markdown"]))}</div>`;
  if (data["text/plain"]) return `<pre class="nb-stream">${esc(stripAnsi(src(data["text/plain"])))}</pre>`;
  return "";
}
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");

export class JsonTree {
  constructor(host, getText, opts = {}) {
    this.getText = getText; this.opts = opts;
    this.el = document.createElement("div"); this.el.className = "json-tree";
    host.append(this.el);
    this.el.addEventListener("click", (e) => {
      const tg = e.target.closest(".jt-toggle"); if (tg) { tg.parentElement.classList.toggle("closed"); return; }
      const k = e.target.closest("[data-path]"); if (k) this.opts.onPath?.(k.dataset.path);
    });
    this.render();
  }
  render() {
    let value;
    const text = this.getText();
    if (text.length > 16 * 1048576) { this.el.innerHTML = `<p class="muted pad">${esc(t("preview_too_big"))}</p>`; return; }
    try { value = JSON.parse(text); }
    catch (e) {

      try { value = text.split(/\r?\n/).filter((l) => l.trim()).map((l) => JSON.parse(l)); }
      catch { this.el.innerHTML = `<div class="jt-error">${icon("warn")}${esc(t("json_invalid"))}: ${esc(e.message)}</div>`; return; }
    }
    this.count = 0;
    this.el.innerHTML = `<div class="jt-tools"><button class="btn ghost sm" data-jt="open">${esc(t("jt_expand"))}</button><button class="btn ghost sm" data-jt="close">${esc(t("jt_collapse"))}</button></div><ul class="jt-root">${this.node("", value, "$", 0)}</ul>`;
    this.el.querySelector("[data-jt=open]").onclick = () => this.el.querySelectorAll(".jt-branch").forEach((b) => b.classList.remove("closed"));
    this.el.querySelector("[data-jt=close]").onclick = () => this.el.querySelectorAll(".jt-root > li .jt-branch, .jt-root > li > ul .jt-branch").forEach((b) => b.classList.add("closed"));
  }
  node(key, v, path, depth) {
    if (++this.count > 20000) return this.count === 20001 ? `<li class="muted">${esc(t("jt_more"))}</li>` : "";
    const k = key === "" ? "" : `<span class="jt-key" data-path="${esc(path)}">${esc(key)}</span><span class="jt-colon">: </span>`;
    if (v && typeof v === "object") {
      const arr = Array.isArray(v); const entries = arr ? v.map((x, i) => [String(i), x]) : Object.entries(v);
      const summary = arr ? `[${entries.length}]` : `{${entries.length}}`;
      const kids = entries.map(([ck, cv]) => this.node(ck, cv, arr ? `${path}[${ck}]` : `${path}.${/^[A-Za-z_$][\w$]*$/.test(ck) ? ck : JSON.stringify(ck)}`, depth + 1)).join("");
      return `<li class="jt-branch ${depth >= 2 ? "closed" : ""}"><span class="jt-toggle" role="button" aria-label="toggle"></span>${k}<span class="jt-sum">${summary}</span><ul>${kids}</ul></li>`;
    }
    const cls = v === null ? "null" : typeof v;
    const shown = typeof v === "string" ? JSON.stringify(v.length > 2000 ? v.slice(0, 2000) + "…" : v) : String(v);
    return `<li class="jt-leaf">${k}<span class="jt-${cls}">${esc(shown)}</span></li>`;
  }
  destroy() { this.el.remove(); }
}

export function xmlFormat(text, indent = "  ") {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const err = doc.querySelector("parsererror");
  if (err) throw new Error(parserMessage(err));
  const decl = /^\s*<\?xml[^>]*\?>/.exec(text)?.[0].trim();
  const out = [];
  const walk = (node, depth) => {
    const pad = indent.repeat(depth);
    switch (node.nodeType) {
      case 1: {
        const attrs = [...node.attributes].map((a) => ` ${a.name}="${a.value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;")}"`).join("");
        const kids = [...node.childNodes].filter((c) => !(c.nodeType === 3 && !c.nodeValue.trim()));
        if (!kids.length) { out.push(`${pad}<${node.nodeName}${attrs}/>`); return; }
        if (kids.length === 1 && kids[0].nodeType === 3) { out.push(`${pad}<${node.nodeName}${attrs}>${escXml(kids[0].nodeValue.trim())}</${node.nodeName}>`); return; }
        out.push(`${pad}<${node.nodeName}${attrs}>`); kids.forEach((c) => walk(c, depth + 1)); out.push(`${pad}</${node.nodeName}>`); return;
      }
      case 3: out.push(pad + escXml(node.nodeValue.trim())); return;
      case 4: out.push(`${pad}<![CDATA[${node.nodeValue}]]>`); return;
      case 7: out.push(`${pad}<?${node.target} ${node.data}?>`); return;
      case 8: out.push(`${pad}<!--${node.nodeValue}-->`); return;
      case 10: out.push(`<!DOCTYPE ${node.name}${node.publicId ? ` PUBLIC "${node.publicId}"` : ""}${node.systemId ? ` "${node.systemId}"` : ""}>`); return;
      default: [...node.childNodes].forEach((c) => walk(c, depth));
    }
  };
  [...doc.childNodes].forEach((c) => walk(c, 0));
  return (decl ? decl + "\n" : "") + out.join("\n") + "\n";
}
const escXml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
export function xmlError(text) {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const err = doc.querySelector("parsererror");
  return err ? parserMessage(err) : null;
}

function parserMessage(err) {
  const raw = (err.querySelector("div")?.textContent || err.firstChild?.textContent || err.textContent || "XML").trim();
  const m = /line (\d+) at column (\d+):\s*([^\n]*)/i.exec(raw);
  if (m) return t("xml_at", { line: m[1], col: m[2] }) + ": " + m[3].trim();
  return raw.split("\n").filter(Boolean)[0].slice(0, 300);
}
