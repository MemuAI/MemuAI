
import { $, api, assetUrl, rawUrl, esc, icon, t, toast, debounce, dirname, showMenu, prompt, modal, plural } from "./core.js";
import { CM } from "./editor.js";

let _md = null;
export function markdownIt() {
  if (_md) return _md;
  const md = new CM.MarkdownIt({ html: false, linkify: true, typographer: true, breaks: false });

  md.core.ruler.push("mn_lines", (state) => {
    for (const tok of state.tokens) if (tok.map && tok.nesting === 1) tok.attrSet("data-line", String(tok.map[0]));
    const toks = state.tokens;
    for (let i = 2; i < toks.length; i++) {
      if (toks[i].type !== "inline" || toks[i - 1].type !== "paragraph_open" || toks[i - 2].type !== "list_item_open") continue;
      const m = /^\[([ xX])\]\s/.exec(toks[i].content);
      if (!m) continue;
      const checked = m[1] !== " ";
      toks[i - 2].attrSet("data-checked", checked ? "true" : "false");
      toks[i - 2].attrJoin("class", "task");
      const first = toks[i].children?.[0];
      if (first && first.type === "text") first.content = first.content.replace(/^\[([ xX])\]\s/, "");

      for (let j = i - 3; j >= 0; j--) { if (toks[j].type === "bullet_list_open" || toks[j].type === "ordered_list_open") { toks[j].attrJoin("class", "contains-task-list"); break; } }
    }
  });
  const defaultImage = md.renderer.rules.image;
  md.renderer.rules.image = (tokens, idx, options, env, self) => {
    const tok = tokens[idx];
    const src = tok.attrGet("src") || "";
    if (env?.base && src && !/^(https?:|data:|blob:)/i.test(src)) {
      const abs = /^([a-zA-Z]:[\\/]|\/)/.test(src) ? src : env.base + (env.sep || "\\") + src.replace(/^\.\//, "").replace(/\//g, env.sep || "\\");
      tok.attrSet("src", assetUrl(decodeURIComponent(abs)));
    } else if (/^https?:/i.test(src)) {
      tok.attrSet("src", ""); tok.attrSet("data-remote", src); tok.attrSet("alt", (tok.content || "") + " (" + src + ")");
    }
    tok.attrSet("loading", "lazy");
    return defaultImage(tokens, idx, options, env, self);
  };
  md.renderer.rules.table_open = () => '<div class="md-table"><table>';
  md.renderer.rules.table_close = () => "</table></div>";
  _md = md;
  return md;
}
export function renderMarkdown(text, filePath = "", sep = "\\") {
  return markdownIt().render(text, { base: filePath ? dirname(filePath) : "", sep });
}

export class MarkdownPreview {
  constructor(host, getTab) {
    this.host = host; this.getTab = getTab;
    this.el = document.createElement("div"); this.el.className = "md-preview doc-prose"; this.el.tabIndex = 0;
    host.append(this.el);
    this.render = debounce(() => this._render(), 120);
    this.el.addEventListener("click", (e) => {
      const a = e.target.closest("a[href]");
      if (a) { e.preventDefault(); const href = a.getAttribute("href"); if (/^(https?:|mailto:)/i.test(href)) api("external", { url: href }); else if (href.startsWith("#")) this.el.querySelector(`[id="${CSS.escape(href.slice(1))}"]`)?.scrollIntoView(); return; }
      const img = e.target.closest("img[data-remote]");
      if (img) { img.src = img.dataset.remote; img.removeAttribute("data-remote"); }
    });
    this.el.addEventListener("dblclick", (e) => { const b = e.target.closest("[data-line]"); if (b) this.onReveal?.(+b.dataset.line); });
  }
  _render() {
    const tab = this.getTab(); if (!tab) return;
    const text = tab.view ? tab.view.state.doc.toString() : tab.state.doc.toString();
    if (text.length > 4 * 1048576) { this.el.innerHTML = `<p class="muted">${esc(t("preview_too_big"))}</p>`; return; }
    const top = this.el.scrollTop;
    this.el.innerHTML = renderMarkdown(text, tab.path, tab.sep);
    this.el.querySelectorAll("li.task").forEach((li) => li.setAttribute("role", "checkbox"));
    this.el.scrollTop = top;
  }
  syncTo(line) {
    const blocks = this.el.querySelectorAll("[data-line]");
    let best = null;
    for (const b of blocks) { if (+b.dataset.line <= line) best = b; else break; }
    if (best) this.el.scrollTop = best.offsetTop - 8;
    else if (line <= 1) this.el.scrollTop = 0;
  }
  destroy() { this.el.remove(); }
}

export function parseCSV(text, delim) {
  if (!delim) {
    const sample = text.slice(0, 20000).split("\n").slice(0, 20);
    const score = (d) => { const counts = sample.map((l) => l.split(d).length - 1).filter((n) => n > 0); return counts.length ? counts.length * 10 - (Math.max(...counts) - Math.min(...counts)) : 0; };
    delim = [",", ";", "\t", "|"].sort((a, b) => score(b) - score(a))[0];
  }
  const rows = []; let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"' && cell === "") q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(cell); rows.push(row); row = []; cell = ""; if (rows.length > 2000000) break; }
    else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return { rows, delim };
}

export function toCSV(rows, delim = ",", eol = "\n") {
  const q = (c) => { c = c ?? ""; return /["\r\n]/.test(c) || c.includes(delim) || /^\s|\s$/.test(c) ? '"' + c.replace(/"/g, '""') + '"' : c; };
  return rows.map((r) => r.map(q).join(delim)).join(eol);
}

export class CsvTable {
  constructor(host, text, opts = {}) {
    this.host = host; this.opts = opts;
    this.el = document.createElement("div"); this.el.className = "csv-view" + (opts.onEdit ? " editable" : "");
    let rows, delim;
    if (opts.rows) { rows = opts.rows; delim = ","; }
    else ({ rows, delim } = parseCSV(text, opts.delim));
    this.trailingNewline = !opts.rows && /\r?\n$/.test(text || "");
    this.header = rows[0] || []; this.data = rows.slice(1); this.view = this.data; this.delim = delim;
    this.cols = Math.max(this.header.length, 1, ...this.data.slice(0, 1000).map((r) => r.length));
    const editHint = opts.onEdit ? `<span class="muted small csv-edit-hint" title="${esc(t("csv_edit_hint"))}">${esc(t("csv_edit_hint"))}</span>` : "";
    const delimInfo = opts.rows ? (opts.note ? `<span class="muted small">${esc(opts.note)}</span>` : "") : `<span class="muted small">${esc(t("csv_delim"))}: <b>${esc(delim === "\t" ? "Tab" : delim)}</b></span>`;
    this.el.innerHTML = `<div class="csv-bar"><span class="ic">${icon("search")}</span><input class="input sm" placeholder="${esc(t("csv_filter"))}" spellcheck="false"><span class="muted small csv-count"></span>${opts.onEdit ? `<button class="btn ghost sm" data-csv="addrow">${icon("plus")}${esc(t("csv_add_row"))}</button>` : ""}<span class="spacer"></span>${editHint}${delimInfo}</div><div class="csv-scroll"><table class="csv"><thead></thead><tbody></tbody></table><div class="csv-pad"></div></div>`;
    host.append(this.el);
    this.scroll = this.el.querySelector(".csv-scroll"); this.tbody = this.el.querySelector("tbody"); this.pad = this.el.querySelector(".csv-pad");
    this.sort = { col: -1, dir: 1 };
    this.renderHead();
    this.scroll.addEventListener("scroll", () => this.renderRows(), { passive: true });
    this.el.querySelector("input").addEventListener("input", debounce((e) => this.filter(e.target.value), 160));
    if (opts.onEdit) {
      this.el.querySelector("[data-csv=addrow]").onclick = () => this.rowOp(this.data.length - 1, "below", true);
      this.tbody.addEventListener("dblclick", (e) => { const td = e.target.closest("td[data-c]"); if (td) this.editCell(td); });
      this.tbody.addEventListener("click", (e) => { const td = e.target.closest("td"); if (td) this.focusCell(td); });
      this.tbody.addEventListener("contextmenu", (e) => { const tr = e.target.closest("tr[data-r]"); if (!tr) return; e.preventDefault(); this.rowMenu(+tr.dataset.r, { x: e.clientX, y: e.clientY }); });
      this.el.querySelector("thead").addEventListener("contextmenu", (e) => { const th = e.target.closest("th[data-c]"); if (!th) return; e.preventDefault(); this.colMenu(+th.dataset.c, { x: e.clientX, y: e.clientY }); });
      this.el.addEventListener("keydown", (e) => {
        const td = document.activeElement?.closest?.("td[data-c]");
        if (!td || e.target.closest(".csv-cell-edit")) return;
        if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); this.editCell(td); }
        else if (e.key === "Delete") { e.preventDefault(); this.setCell(td, ""); }
      });
    }
    this.renderRows();
  }
  renderHead() {
    const th = Array.from({ length: this.cols }, (_, i) => `<th data-c="${i}"><span>${esc(this.header[i] ?? "")}</span>${this.sort.col === i ? icon(this.sort.dir > 0 ? "up" : "down", "sort") : ""}</th>`).join("");
    this.el.querySelector("thead").innerHTML = `<tr data-r="-1"><th class="rn">#</th>${th}</tr>`;
    this.el.querySelectorAll("th[data-c]").forEach((h) => {
      h.onclick = () => this.sortBy(+h.dataset.c);
      if (this.opts.onEdit) h.ondblclick = (e) => { e.stopPropagation(); this.editHeader(+h.dataset.c, h); };
    });
    this.el.querySelector(".csv-count").textContent = this.view.length.toLocaleString() + " " + plural(this.view.length, t("csv_rows_forms").split("|"));
  }
  sortBy(c) {
    this.sort = { col: c, dir: this.sort.col === c ? -this.sort.dir : 1 };
    const coll = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
    const d = this.sort.dir;
    this.view = this.view.slice().sort((a, b) => {
      const x = a[c] ?? "", y = b[c] ?? ""; const nx = parseFloat(x.replace(",", ".")), ny = parseFloat(y.replace(",", "."));
      return (!isNaN(nx) && !isNaN(ny) && /^[-+\d.,\s]+$/.test(x) && /^[-+\d.,\s]+$/.test(y) ? nx - ny : coll.compare(x, y)) * d;
    });
    this.renderHead(); this.renderRows(true);
  }
  filter(q) {
    this.query = q;
    q = q.trim().toLowerCase();
    this.view = q ? this.data.filter((r) => r.some((c) => (c || "").toLowerCase().includes(q))) : this.data;
    if (this.sort.col >= 0) { const s = this.sort; this.sort = { col: s.col, dir: -s.dir }; this.sortBy(s.col); }
    else { this.renderHead(); this.renderRows(true); }
  }
  renderRows(force) {
    const H = 28, top = this.scroll.scrollTop, height = this.scroll.clientHeight || 600;
    const start = Math.max(0, Math.floor(top / H) - 20), end = Math.min(this.view.length, start + Math.ceil(height / H) + 40);
    if (!force && this._range && this._range[0] === start && this._range[1] === end) return;
    this._range = [start, end];
    this.pad.style.height = Math.max(0, (this.view.length - end) * H) + "px";
    const rows = [];
    rows.push(`<tr class="spacer" style="height:${start * H}px"></tr>`);
    const tab = this.opts.onEdit ? ' tabindex="-1"' : "";
    for (let i = start; i < end; i++) {
      const r = this.view[i];
      rows.push(`<tr data-r="${i}"><td class="rn">${i + 1}</td>${Array.from({ length: this.cols }, (_, c) => `<td data-c="${c}"${tab}>${esc(r[c] ?? "")}</td>`).join("")}</tr>`);
    }
    this.tbody.innerHTML = rows.join("");
  }

  changed() {
    const text = toCSV([this.header, ...this.data], this.delim, "\n") + (this.trailingNewline ? "\n" : "");
    this.opts.onEdit?.(text);
  }
  focusCell(td) { td.focus({ preventScroll: true }); }
  setCell(td, value) {
    const row = this.view[+td.closest("tr").dataset.r], c = +td.dataset.c; if (!row) return;
    if ((row[c] ?? "") === value) return;
    while (row.length <= c) row.push("");
    row[c] = value; td.textContent = value; this.changed();
  }
  editCell(td) {
    const row = this.view[+td.closest("tr").dataset.r]; if (!row) return;
    this.inlineEditor(td, row[+td.dataset.c] ?? "", (v) => this.setCell(td, v));
  }
  editHeader(c, th) {
    this.inlineEditor(th, this.header[c] ?? "", (v) => { while (this.header.length <= c) this.header.push(""); this.header[c] = v; this.renderHead(); this.changed(); });
  }
  inlineEditor(cell, value, done) {
    const r = cell.getBoundingClientRect(), host = this.el.getBoundingClientRect();
    const ta = document.createElement("textarea"); ta.className = "csv-cell-edit"; ta.value = value; ta.spellcheck = false;
    Object.assign(ta.style, { left: r.left - host.left + "px", top: r.top - host.top + "px", width: Math.max(r.width, 160) + "px", minHeight: r.height + "px" });
    this.el.append(ta); ta.focus(); ta.select();
    let closed = false;
    const close = (save) => { if (closed) return; closed = true; const v = ta.value; ta.remove(); if (save) done(v); cell.focus?.({ preventScroll: true }); };
    ta.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); close(false); }
      else if (e.key === "Enter" && !e.shiftKey && !e.altKey) { e.preventDefault(); close(true); }
      else if (e.key === "Tab") { e.preventDefault(); close(true); const next = e.shiftKey ? cell.previousElementSibling : cell.nextElementSibling; if (next?.dataset.c !== undefined) next.focus(); }
    });
    ta.addEventListener("blur", () => close(true));
  }
  rowMenu(i, at) {
    if (i < 0) return;
    showMenu([
      { label: t("csv_row_above"), icon: "up", run: () => this.rowOp(i, "above") },
      { label: t("csv_row_below"), icon: "down", run: () => this.rowOp(i, "below") },
      "-", { label: t("csv_row_delete"), icon: "close", danger: true, run: () => this.rowOp(i, "delete") },
    ], at);
  }
  colMenu(c, at) {
    showMenu([
      { label: t("csv_col_left"), icon: "chevron", run: () => this.colOp(c, "left") },
      { label: t("csv_col_right"), icon: "chevronRight", run: () => this.colOp(c, "right") },
      { label: t("csv_rename_col"), icon: "fileText", run: () => this.editHeader(c, this.el.querySelector(`th[data-c="${c}"]`)) },
      "-", { label: t("csv_col_delete"), icon: "close", danger: true, disabled: this.cols <= 1, run: () => this.colOp(c, "delete") },
    ], at);
  }
  rowOp(i, op, scroll) {
    const row = this.view[i];
    const at = row ? this.data.indexOf(row) : this.data.length - 1;
    if (op === "delete") { if (at >= 0) this.data.splice(at, 1); }
    else this.data.splice(op === "above" ? Math.max(0, at) : at + 1, 0, Array.from({ length: this.cols }, () => ""));
    this.filter(this.query || "");
    this.changed();
    if (scroll) this.scroll.scrollTop = this.scroll.scrollHeight;
  }
  colOp(c, op) {
    const all = [this.header, ...this.data];
    if (op === "delete") { for (const r of all) if (r.length > c) r.splice(c, 1); this.cols = Math.max(1, this.cols - 1); }
    else { const k = op === "left" ? c : c + 1; for (const r of all) { while (r.length < k) r.push(""); r.splice(k, 0, ""); } this.cols++; }
    this.sort = { col: -1, dir: 1 };
    this.renderHead(); this.renderRows(true); this.changed();
  }
  destroy() { this.el.remove(); }
}

export { PdfView, pdfjs } from "./pdfedit.js";

export class ImageView {
  constructor(host, tab) {
    this.el = document.createElement("div"); this.el.className = "image-view";
    this.el.innerHTML = `<div class="image-stage"><img alt="" src="${rawUrl(tab.path)}"></div><div class="image-info muted small"></div>`;
    host.append(this.el);
    const img = this.el.querySelector("img"); let z = 1;
    img.onload = () => { this.el.querySelector(".image-info").textContent = `${img.naturalWidth} × ${img.naturalHeight}`; };
    this.el.addEventListener("wheel", (e) => { if (!e.ctrlKey) return; e.preventDefault(); z = Math.max(0.1, Math.min(16, z * (e.deltaY < 0 ? 1.1 : 1 / 1.1))); img.style.transform = `scale(${z})`; }, { passive: false });
    img.addEventListener("dblclick", () => { z = 1; img.style.transform = ""; });
  }
  destroy() { this.el.remove(); }
}

export class HtmlPreview {
  constructor(host, getText) {
    this.getText = getText;
    this.el = document.createElement("iframe"); this.el.className = "html-preview"; this.el.setAttribute("sandbox", "");
    this.el.setAttribute("referrerpolicy", "no-referrer");
    host.append(this.el);
    this.render = debounce(() => { this.el.srcdoc = this.getText(); }, 300);
    this.render();
  }
  destroy() { this.el.remove(); }
}

const BLOCKS = [["p", "doc_style_normal"], ["h1", "doc_style_h1"], ["h2", "doc_style_h2"], ["h3", "doc_style_h3"], ["h4", "doc_style_h4"], ["blockquote", "doc_style_quote"], ["pre", "doc_style_code"]];
const SIZES = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 72];
const FONTS = ["Calibri", "Arial", "Times New Roman", "Georgia", "Segoe UI", "Verdana", "Tahoma", "Cambria", "Garamond", "Consolas", "Courier New", "JetBrains Mono"];
const COLORS = ["#000000", "#434343", "#666666", "#999999", "#c00000", "#e06666", "#ff9900", "#f1c232", "#38761d", "#6aa84f", "#0b5394", "#3d85c6", "#674ea7", "#a64d79", "#ffffff"];
const MARKS = ["#fff2a8", "#ffd8a8", "#ffc9c9", "#d3f9d8", "#c5f6fa", "#d0ebff", "#e5dbff", "#f3d9fa", "transparent"];

export class DocEditor {
  constructor(host, tab, opts = {}) {
    this.tab = tab; this.opts = opts;
    this.el = document.createElement("div"); this.el.className = "doc-view";
    this.el.innerHTML = `<div class="doc-toolbar" role="toolbar">${this.toolbar()}</div><div class="doc-scroll"><div class="doc-page doc-prose" contenteditable="true" spellcheck="${opts.spellcheck ? "true" : "false"}"></div></div>`;
    host.append(this.el);
    this.page = this.el.querySelector(".doc-page");
    this.page.innerHTML = tab.html || "<p><br></p>";
    document.execCommand("defaultParagraphSeparator", false, "p");
    document.execCommand("styleWithCSS", false, true);
    this.page.addEventListener("input", () => { this.opts.onChange?.(); this.refreshState(); });
    this.page.addEventListener("keyup", () => this.refreshState());
    this.page.addEventListener("mouseup", () => this.refreshState());
    this.page.addEventListener("paste", (e) => this.paste(e));
    this.page.addEventListener("click", (e) => {
      const li = e.target.closest("ul.mn-tasks > li, ul.contains-task-list > li");
      if (li && e.offsetX < 4) { li.dataset.checked = li.dataset.checked === "true" ? "false" : "true"; this.opts.onChange?.(); }
      const a = e.target.closest("a[href]");
      if (a && (e.ctrlKey || e.metaKey)) { e.preventDefault(); api("external", { url: a.href }); }
    });
    this.page.addEventListener("keydown", (e) => this.keys(e));
    this.el.querySelector(".doc-toolbar").addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
    this.el.querySelector(".doc-toolbar").addEventListener("click", (e) => { const b = e.target.closest("[data-cmd]"); if (b) this.command(b.dataset.cmd, b); });
    this.el.querySelectorAll(".doc-toolbar select").forEach((s) => s.addEventListener("change", () => this.command(s.dataset.cmd, s)));
    this.setZoom(opts.zoom || 100);
  }
  toolbar() {
    const B = (cmd, ic, label, extra = "") => `<button class="tb-btn" data-cmd="${cmd}" title="${esc(t(label))}" aria-label="${esc(t(label))}" ${extra}>${icon(ic)}</button>`;
    return `${B("undo", "undo", "undo")}${B("redo", "redo", "redo")}<span class="tb-sep"></span>
      <select class="tb-select" data-cmd="block" title="${esc(t("doc_style"))}">${BLOCKS.map(([v, k]) => `<option value="${v}">${esc(t(k))}</option>`).join("")}</select>
      <select class="tb-select font" data-cmd="font" title="${esc(t("doc_font"))}"><option value="">${esc(t("doc_font"))}</option>${FONTS.map((f) => `<option style="font-family:'${f}'">${f}</option>`).join("")}</select>
      <select class="tb-select size" data-cmd="size" title="${esc(t("doc_size"))}"><option value="">${esc(t("doc_size_short"))}</option>${SIZES.map((s) => `<option>${s}</option>`).join("")}</select>
      <span class="tb-sep"></span>${B("bold", "bold", "fmt_bold")}${B("italic", "italic", "fmt_italic")}${B("underline", "underline", "fmt_underline")}${B("strikeThrough", "strike", "fmt_strike")}
      ${B("superscript", "sup", "fmt_sup")}${B("subscript", "sub", "fmt_sub")}${B("color", "color", "fmt_color")}${B("mark", "highlight", "fmt_highlight")}
      <span class="tb-sep"></span>${B("justifyLeft", "alignLeft", "align_left")}${B("justifyCenter", "alignCenter", "align_center")}${B("justifyRight", "alignRight", "align_right")}${B("justifyFull", "alignJustify", "align_justify")}
      <span class="tb-sep"></span>${B("insertUnorderedList", "listUl", "fmt_ul")}${B("insertOrderedList", "listOl", "fmt_ol")}${B("tasks", "checklist", "fmt_tasks")}${B("outdent", "outdent", "outdent")}${B("indent", "indent", "indent")}
      <span class="tb-sep"></span>${B("link", "link", "fmt_link")}${B("table", "table", "fmt_table")}${B("image", "image", "fmt_image")}${B("insertHorizontalRule", "hr", "fmt_hr")}${B("pagebreak", "pageBreak", "fmt_pagebreak")}
      ${B("removeFormat", "eraser", "fmt_clear")}<span class="tb-sep"></span>${B("paper", "sun", "doc_paper")}`;
  }
  exec(cmd, value = null) { this.page.focus(); document.execCommand(cmd, false, value); this.opts.onChange?.(); this.refreshState(); }
  async command(cmd, el) {
    switch (cmd) {
      case "undo": case "redo": this.exec(cmd); break;
      case "block": this.exec("formatBlock", el.value); break;
      case "font": if (el.value) this.exec("fontName", el.value); el.value = ""; break;
      case "size": if (el.value) this.fontSize(+el.value); el.value = ""; break;
      case "color": this.palette(el, COLORS, (c) => this.exec("foreColor", c)); break;
      case "mark": this.palette(el, MARKS, (c) => this.exec("hiliteColor", c)); break;
      case "tasks": this.exec("insertHTML", `<ul class="mn-tasks"><li data-checked="false">${esc(window.getSelection().toString()) || "<br>"}</li></ul>`); break;
      case "link": {
        const sel = window.getSelection(); const range = sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
        const url = await prompt(t("fmt_link"), t("link_url"), "https://");
        if (!url) return;
        if (range) { sel.removeAllRanges(); sel.addRange(range); }
        if (range && range.collapsed) this.exec("insertHTML", `<a href="${esc(url)}">${esc(url)}</a>`); else this.exec("createLink", url);
        break;
      }
      case "table": {
        const spec = await prompt(t("fmt_table"), t("table_size"), "3x3");
        const m = /(\d+)\s*[x×х*]\s*(\d+)/i.exec(spec || ""); if (!m) return;
        const rows = Math.min(100, +m[1]), cols = Math.min(30, +m[2]);
        const head = `<tr>${"<th><br></th>".repeat(cols)}</tr>`; const body = `<tr>${"<td><br></td>".repeat(cols)}</tr>`.repeat(Math.max(1, rows - 1));
        this.exec("insertHTML", `<table><tbody>${head}${body}</tbody></table><p><br></p>`); break;
      }
      case "image": this.opts.onImage?.((dataUrl) => this.exec("insertImage", dataUrl)); break;
      case "pagebreak": this.exec("insertHTML", '<hr class="mn-pagebreak"><p><br></p>'); break;
      case "paper": this.el.classList.toggle("paper-light"); break;
      default: this.exec(cmd);
    }
  }
  palette(anchor, colors, apply) {
    showMenu([{ header: t("fmt_color") }, ...colors.map((c) => ({ label: c === "transparent" ? t("color_none") : c, icon: null, run: () => apply(c), hint: "" }))], anchor);
    document.querySelectorAll(".menu .mi").forEach((mi, i) => { const c = colors[i]; if (c) mi.querySelector(".mi-ic").innerHTML = `<span class="swatch" style="background:${c}"></span>`; });
  }
  fontSize(pt) {
    this.page.focus();
    document.execCommand("styleWithCSS", false, false);
    document.execCommand("fontSize", false, "7");
    this.page.querySelectorAll('font[size="7"]').forEach((f) => { const s = document.createElement("span"); s.style.fontSize = pt + "pt"; s.innerHTML = f.innerHTML; f.replaceWith(s); });
    document.execCommand("styleWithCSS", false, true);
    this.opts.onChange?.();
  }
  keys(e) {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && !e.shiftKey && !e.altKey) {
      const map = { b: "bold", i: "italic", u: "underline", k: "link" };
      if (map[e.key.toLowerCase()]) { e.preventDefault(); this.command(map[e.key.toLowerCase()], null); return; }
    }
    if (mod && e.altKey && /^[1-4]$/.test(e.key)) { e.preventDefault(); this.exec("formatBlock", "h" + e.key); return; }
    if (mod && e.altKey && e.key === "0") { e.preventDefault(); this.exec("formatBlock", "p"); return; }
    if (e.key === "Tab" && this.inList()) { e.preventDefault(); this.exec(e.shiftKey ? "outdent" : "indent"); }
  }
  inList() { const n = window.getSelection()?.anchorNode; return !!(n && (n.nodeType === 1 ? n : n.parentElement)?.closest("li")); }
  async paste(e) {
    const html = e.clipboardData?.getData("text/html");
    if (!html) return;
    e.preventDefault();
    try { const r = await api("convert", { html, to: "html" }); this.exec("insertHTML", r.html); }
    catch { this.exec("insertText", e.clipboardData.getData("text/plain")); }
  }
  refreshState() {
    const tb = this.el.querySelector(".doc-toolbar");
    for (const c of ["bold", "italic", "underline", "strikeThrough", "superscript", "subscript", "insertUnorderedList", "insertOrderedList", "justifyLeft", "justifyCenter", "justifyRight", "justifyFull"]) {
      let on = false; try { on = document.queryCommandState(c); } catch {  }
      tb.querySelector(`[data-cmd="${c}"]`)?.classList.toggle("on", on);
    }
    try { const block = String(document.queryCommandValue("formatBlock") || "p").toLowerCase(); const s = tb.querySelector('[data-cmd="block"]'); if (s) s.value = BLOCKS.some(([v]) => v === block) ? block : "p"; } catch {  }
    this.opts.onSelection?.();
  }
  setZoom(z) { this.page.style.setProperty("--doc-zoom", z / 100); }
  html() { return this.page.innerHTML; }
  text() { return this.page.innerText; }
  selectionText() { return window.getSelection()?.toString() || ""; }
  replaceSelection(html) { this.page.focus(); if (this._range) { const s = window.getSelection(); s.removeAllRanges(); s.addRange(this._range); } document.execCommand("insertHTML", false, html); this.opts.onChange?.(); }
  saveRange() { const s = window.getSelection(); if (s.rangeCount && this.page.contains(s.anchorNode)) this._range = s.getRangeAt(0).cloneRange(); }
  focus() { this.page.focus(); }
  destroy() { this.el.remove(); }
}

export async function docFind(doc) {
  const q = await prompt(t("find"), t("find_text"), doc.selectionText());
  if (!q) return;
  doc.page.focus();
  if (!window.find(q, false, false, true, false, false, false)) toast(t("not_found"));
}
