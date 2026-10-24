// MemuNote — Round 40 (r40-note): the spreadsheet editor for .xlsx/.xlsm (.ods stays read-only in the same grid).
// Virtualised grid (rows and columns), formula bar with the name box, cell editing (values and formulas; clicking a
// cell while typing a formula inserts its reference), keyboard navigation, fill handle (series / shifted formulas),
// copy / cut / paste (TSV from and to other apps, formulas kept inside MemuNote), undo / redo, sheet tabs (+ new
// sheet), selection sum / average / count. Formulas are computed by formula.js; a function the engine does not know
// shows the value cached in the file with a marker. Saving sends only the changed cells (/api/sheet_save,
// formats/xlsx_edit.py keeps styles, widths, merges and the other sheets). AI: text() for the context,
// plan()/applyPlan() for the agent's set_cells / add_column / add_sheet edits (one undo step). Styles: sheetgrid.css.
import { esc, icon, t, api, I18N } from "./core.js";
import { Workbook, colName, parseRange, shiftFormula, valueOf, display, cellRef, FUNCTIONS } from "./formula.js";

(function loadCss() {
  if (document.querySelector("link[data-sheetgrid-css]")) return;
  const link = document.createElement("link"); link.rel = "stylesheet"; link.href = "sheetgrid.css?v=r40"; link.dataset.sheetgridCss = "1";
  document.head.append(link);
})();

const ROW_H = 26, HEAD_H = 26, ROWHEAD_W = 52, DEF_W = 104, MIN_ROWS = 60, MIN_COLS = 16, PAD_ROWS = 30, PAD_COLS = 6;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

/** The workbook model without a view: what the AI panel previews against for a sheet tab that was never shown. */
export class SheetData {
  constructor(tab) {
    this.tab = tab; this.editable = !!tab.sheetEditable;
    this.sheets = (tab.sheets || []).map((s) => normSheet(s));
    if (!this.sheets.length) this.sheets.push(normSheet({ name: "Sheet1", rows: [] }));
    this.wb = new Workbook(this.sheets); this.index = 0;
    this.looks = (tab.looks || []).map(lookCss);       // cell styles of the file: bold / colours / alignment / number format
  }
  get sheet() { return this.sheets[this.index]; }
  dims(sheet = this.sheet) { return { rows: sheet.rows.length, cols: sheet.cols }; }
  raw(r, c, sheet = this.sheet) { return sheet.rows[r]?.[c] ?? ""; }
  formula(r, c, sheet = this.sheet) { return sheet.formulas[r + "," + c] || ""; }
  /** What the formula bar / editor shows: "=FORMULA" or the value as typed. */
  source(r, c, sheet = this.sheet) { const f = this.formula(r, c, sheet); return f ? "=" + f : this.raw(r, c, sheet); }
  /** The workbook as the model sees it: per sheet, a grid with column letters / row numbers, formulas with values. */
  text(limit = 400000) {
    const key = (this.gen || 0) + ":" + limit + ":" + this.sheets.length;      // the AI panel asks often: rebuild only after a change
    if (this._text?.key === key) return this._text.value;
    const value = this.buildText(limit); this._text = { key, value }; return value;
  }
  buildText(limit) {
    const parts = []; let left = limit;
    for (const s of this.sheets) {
      const d = this.dims(s); const cols = Math.min(d.cols, 60);
      const lines = [`## Sheet: ${s.name} (${d.rows} rows × ${d.cols} columns)`, ["", ...range(0, cols - 1).map(colName)].join("\t")];
      for (let r = 0; r < d.rows && left > 0; r++) {
        const row = [String(r + 1)];
        for (let c = 0; c < cols; c++) { const f = this.formula(r, c, s); row.push(f ? `=${f} → ${this.wb.text(s, r, c)}` : this.raw(r, c, s).replace(/[\t\n]/g, " ")); }
        while (row.length > 1 && row[row.length - 1] === "") row.pop();
        const line = row.join("\t"); lines.push(line); left -= line.length + 1;
      }
      if (left <= 0) lines.push("[… the rest of the sheet is omitted …]");
      parts.push(lines.join("\n"));
      if (left <= 0) break;
    }
    return parts.join("\n\n");
  }
  csv(i = this.index) { const s = this.sheets[i]; return (s?.rows || []).map((row, r) => row.map((v, c) => { const x = this.formula(r, c, s) ? this.wb.text(s, r, c) : v; return /[",\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; }).join(",")).join("\n") + "\n"; }
  findSheet(name) { if (!name) return this.sheet; const n = String(name).toLowerCase(); return this.sheets.find((s) => s.name.toLowerCase() === n) || null; }
  /**
   * The agent's spreadsheet edits → a plan: {cells: [{sheet, r, c, before, value}], newSheets: [sheet], errors: []}.
   * Nothing changes until applyPlan(plan) — the AI panel previews it first.
   */
  plan(edits, pendingSheets = []) {
    const plan = { cells: [], newSheets: [], errors: [] };
    const sheetsNow = () => [...this.sheets, ...pendingSheets, ...plan.newSheets];
    const byName = (n) => { if (!n) return this.sheet; const k = String(n).toLowerCase(); return sheetsNow().find((s) => s.name.toLowerCase() === k) || null; };
    const rowsOf = (text) => String(text || "").replace(/\r\n/g, "\n").split("\n").map((l) => l.split("\t"));
    for (const e of edits) {
      if (e.op === "add_sheet") {
        const s = normSheet({ name: uniqueName(e.name, sheetsNow()), rows: [] }); s.isNew = true; plan.newSheets.push(s);
        if (e.text) rowsOf(e.text).forEach((row, i) => row.forEach((v, j) => plan.cells.push({ sheet: s, r: i, c: j, before: "", value: v })));
        continue;
      }
      const s = byName(e.sheet);
      if (!s) { plan.errors.push({ op: e.op, error: "no_sheet", find: e.sheet }); continue; }
      if (e.op === "set_cells") {
        const g = parseRange(e.range); if (!g) { plan.errors.push({ op: e.op, error: "bad_range", find: e.range }); continue; }
        if (e.formula) { for (let r = g.r1; r <= g.r2; r++) for (let c = g.c1; c <= g.c2; c++) plan.cells.push({ sheet: s, r, c, before: this.source(r, c, s), value: "=" + shiftFormula(e.formula.slice(1), r - g.r1, c - g.c1) }); continue; }
        if (e.clear || !e.text) { for (let r = g.r1; r <= Math.min(g.r2, s.rows.length); r++) for (let c = g.c1; c <= g.c2; c++) if (this.source(r, c, s) !== "") plan.cells.push({ sheet: s, r, c, before: this.source(r, c, s), value: "" }); continue; }
        const rows = rowsOf(e.text);
        const single = rows.length === 1 && rows[0].length === 1 && (g.r2 > g.r1 || g.c2 > g.c1);
        if (single) { for (let r = g.r1; r <= g.r2; r++) for (let c = g.c1; c <= g.c2; c++) { const v = rows[0][0]; plan.cells.push({ sheet: s, r, c, before: this.source(r, c, s), value: v.startsWith("=") ? "=" + shiftFormula(v.slice(1), r - g.r1, c - g.c1) : v }); } continue; }
        rows.forEach((row, i) => row.forEach((v, j) => plan.cells.push({ sheet: s, r: g.r1 + i, c: g.c1 + j, before: this.source(g.r1 + i, g.c1 + j, s), value: v.trim() === "" && row.length === 1 ? "" : v })));
      } else if (e.op === "add_column") {
        const c = Math.max(s.cols, ...plan.cells.filter((x) => x.sheet === s).map((x) => x.c + 1));
        let r = 0;
        if (e.header) plan.cells.push({ sheet: s, r: r++, c, before: "", value: e.header });
        else r = 1;
        String(e.text || "").replace(/\r\n/g, "\n").split("\n").filter((v, i, a) => v !== "" || i < a.length - 1).forEach((v) => plan.cells.push({ sheet: s, r: r++, c, before: "", value: v }));
      }
    }
    plan.cells = plan.cells.filter((x) => x.before !== x.value);
    return plan;
  }
  /** Preview text of a plan cell: "B2: 10 → =SUM(…) (= 42)". */
  previewValue(x) {
    if (!String(x.value).startsWith("=")) return x.value;
    const tmp = new Workbook(this.sheets.map((s) => (s === x.sheet ? { ...s, formulas: { ...s.formulas, [x.r + "," + x.c]: x.value.slice(1) } } : s)));
    const v = tmp.text(x.sheet.name, x.r, x.c);
    return v ? `${x.value} → ${v}` : x.value;
  }
}

export class SheetGrid extends SheetData {
  constructor(host, tab, hooks = {}) {
    super(tab); this.hooks = hooks;
    this.sel = { r1: 0, c1: 0, r2: 0, c2: 0, ar: 0, ac: 0 };
    this.undoStack = []; this.redoStack = []; this.dirty = new Map(); this.newSheets = [];
    this.el = document.createElement("div"); this.el.className = "sg" + (this.editable ? " editable" : " readonly");
    this.el.innerHTML = `
      <div class="sg-bar">
        <input class="sg-name" spellcheck="false" aria-label="${esc(t("sg_name_box"))}" title="${esc(t("sg_name_box"))}">
        <span class="sg-fx" title="${esc(t("sg_formula"))}">fx</span>
        <input class="sg-input" spellcheck="false" aria-label="${esc(t("sg_formula"))}" ${this.editable ? "" : "readonly"}>
        ${this.editable ? "" : `<span class="sg-ro" title="${esc(t("sg_read_only_hint"))}">${icon("lock")}${esc(t("sheet_read_only"))}</span>`}
      </div>
      <div class="sg-main">
        <div class="sg-corner" title="${esc(t("sg_select_all"))}"></div>
        <div class="sg-colhead"><div class="sg-colhead-in"></div></div>
        <div class="sg-rowhead"><div class="sg-rowhead-in"></div></div>
        <div class="sg-scroll" tabindex="0"><div class="sg-sizer"><div class="sg-cells"></div><div class="sg-selrect"></div><div class="sg-active"></div><div class="sg-flash" hidden></div>${this.editable ? `<div class="sg-fill" title="${esc(t("sg_fill_hint"))}"></div>` : ""}</div></div>
      </div>
      <div class="sg-foot"><div class="sg-tabs" role="tablist"></div><span class="spacer"></span><span class="sg-stat muted small"></span></div>`;
    host.append(this.el);
    const $ = (s) => this.el.querySelector(s);
    this.scroll = $(".sg-scroll"); this.sizer = $(".sg-sizer"); this.cellsEl = $(".sg-cells");
    this.colIn = $(".sg-colhead-in"); this.rowIn = $(".sg-rowhead-in"); this.selEl = $(".sg-selrect"); this.actEl = $(".sg-active");
    this.fillEl = $(".sg-fill"); this.flashEl = $(".sg-flash");
    this.nameBox = $(".sg-name"); this.input = $(".sg-input"); this.statEl = $(".sg-stat");
    this.scroll.addEventListener("scroll", () => this.onScroll(), { passive: true });
    this.scroll.addEventListener("pointerdown", (e) => this.onPointer(e));
    this.scroll.addEventListener("dblclick", (e) => { const p = this.hit(e); if (p && this.editable) this.startEdit(null, p); });
    this.scroll.addEventListener("keydown", (e) => this.onKey(e));
    this.scroll.addEventListener("copy", (e) => this.onCopy(e, false));
    this.scroll.addEventListener("cut", (e) => this.onCopy(e, true));
    this.scroll.addEventListener("paste", (e) => this.onPaste(e));
    $(".sg-corner").onclick = () => { const d = this.dims(); this.select({ r1: 0, c1: 0, r2: d.rows - 1, c2: d.cols - 1, ar: 0, ac: 0 }); };
    this.colIn.addEventListener("pointerdown", (e) => this.onHeadPointer(e, "col"));
    this.rowIn.addEventListener("pointerdown", (e) => this.onHeadPointer(e, "row"));
    this.nameBox.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.goto(this.nameBox.value); this.scroll.focus(); } if (e.key === "Escape") { this.syncBar(); this.scroll.focus(); } });
    this.input.addEventListener("focus", () => { if (this.editable && !this.editing) this.startEdit(this.input.value, null, "bar"); });
    this.input.addEventListener("input", () => { if (this.editing) { this.editing.value = this.input.value; if (this.editor) this.editor.value = this.input.value; } });
    this.input.addEventListener("keydown", (e) => this.onEditorKey(e));
    $(".sg-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]"); if (b) { this.show(+b.dataset.i); return; }
      if (e.target.closest("[data-add]")) this.addSheet();
    });
    this.ro = new ResizeObserver(() => this.render());
    this.ro.observe(this.scroll);
    this.show(0);
  }
  // ---------------------------------------------------------------- model helpers
  width(c) { return this.sheet.widths[c] || DEF_W; }
  colX(c) { const xs = this.sheet._xs; return c < xs.length ? xs[c] : xs[xs.length - 1] + (c - xs.length + 1) * DEF_W; }
  colAt(x) { const xs = this.sheet._xs; let lo = 0, hi = xs.length - 2; if (x >= xs[xs.length - 1]) return xs.length - 1 + Math.floor((x - xs[xs.length - 1]) / DEF_W); while (lo < hi) { const m = (lo + hi + 1) >> 1; if (xs[m] <= x) lo = m; else hi = m - 1; } return lo; }
  layout() {
    const s = this.sheet; const d = this.dims(s);
    this.vRows = Math.max(MIN_ROWS, d.rows + PAD_ROWS); this.vCols = Math.max(MIN_COLS, d.cols + PAD_COLS);
    const xs = [0]; for (let c = 0; c < this.vCols; c++) xs.push(xs[c] + (s.widths[c] || DEF_W));
    s._xs = xs;
    this.sizer.style.width = xs[this.vCols] + "px"; this.sizer.style.height = this.vRows * ROW_H + "px";
  }
  // ---------------------------------------------------------------- rendering
  show(i) {
    this.commitEdit(); this.index = clamp(i, 0, this.sheets.length - 1);
    this.layout(); this.scroll.scrollTop = 0; this.scroll.scrollLeft = 0; this.last = null;
    this.sel = { r1: 0, c1: 0, r2: 0, c2: 0, ar: 0, ac: 0 };
    this.renderTabs(); this.render(true); this.drawSel();
  }
  renderTabs() {
    const bar = this.el.querySelector(".sg-tabs");
    bar.innerHTML = this.sheets.map((s, i) => `<button class="sg-tab ${i === this.index ? "on" : ""}" data-i="${i}" role="tab" aria-selected="${i === this.index}">${esc(s.name)}${s.truncated ? ` <span class="sg-trunc" title="${esc(t("sheet_truncated"))}">…</span>` : ""}</button>`).join("")
      + (this.editable ? `<button class="sg-tab add" data-add title="${esc(t("sg_add_sheet"))}">${icon("plus")}</button>` : "");
  }
  onScroll() { if (this.raf) return; this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); }); this.colIn.style.transform = `translateX(${-this.scroll.scrollLeft}px)`; this.rowIn.style.transform = `translateY(${-this.scroll.scrollTop}px)`; }
  render(force) {
    if (!this.el.isConnected && !force) return;
    const st = this.scroll.scrollTop, sl = this.scroll.scrollLeft, h = this.scroll.clientHeight || 600, w = this.scroll.clientWidth || 900;
    const r1 = Math.max(0, Math.floor(st / ROW_H) - 4), r2 = Math.min(this.vRows - 1, Math.ceil((st + h) / ROW_H) + 4);
    const c1 = Math.max(0, this.colAt(sl) - 1), c2 = Math.min(this.vCols - 1, this.colAt(sl + w) + 1);
    if (r2 >= this.vRows - 8 || c2 >= this.vCols - 2) { const before = this.vRows + "x" + this.vCols; this.vRows = Math.max(this.vRows, r2 + PAD_ROWS); this.vCols = Math.max(this.vCols, c2 + PAD_COLS); if (before !== this.vRows + "x" + this.vCols) { this.growLayout(); } }
    const key = `${this.index}|${r1}|${r2}|${c1}|${c2}|${this.gen || 0}`;
    this.colIn.style.transform = `translateX(${-sl}px)`; this.rowIn.style.transform = `translateY(${-st}px)`;
    if (!force && key === this.last) return;
    this.last = key;
    const s = this.sheet, wb = this.wb, xs = s._xs; let html = "";
    for (let r = r1; r <= r2; r++) {
      const row = s.rows[r];
      for (let c = c1; c <= c2; c++) {
        const k = r + "," + c, f = s.formulas[k], look = this.looks[s.styles[k]], span = s.spans.get(k);
        if (s.covered.has(k)) continue;
        const empty = !f && (!row || row[c] == null || row[c] === "");
        if (empty && !look?.fill && !span) continue;
        const v = empty ? null : f ? wb.value(s, r, c) : valueOf(row[c]);
        const text = empty ? "" : look?.fmt && typeof v === "number" ? formatNumber(v, look.fmt) : f ? display(v) : row[c];
        const cached = f && wb.cachedOnly(s, r, c);
        const cls = (typeof v === "number" ? " n" : "") + (v && v.code ? " err" : "") + (f ? " f" : "") + (cached ? " cached" : "");
        const size = span ? `width:${this.colX(span.c2 + 1) - xs[c]}px;height:${(span.r2 - span.r1 + 1) * ROW_H}px;line-height:${(span.r2 - span.r1 + 1) * ROW_H - 1}px` : `width:${xs[c + 1] - xs[c]}px`;
        html += `<div class="sg-c${cls}${span ? " merged" : ""}" style="left:${xs[c]}px;top:${r * ROW_H}px;${size}${look ? look.css : ""}"${cached ? ` title="${esc(t("sg_cached"))}"` : ""}>${esc(text)}</div>`;
      }
    }
    this.cellsEl.innerHTML = html;
    let ch = ""; for (let c = c1; c <= c2; c++) ch += `<div class="sg-h${c >= this.sel.c1 && c <= this.sel.c2 ? " on" : ""}" data-c="${c}" style="left:${xs[c]}px;width:${xs[c + 1] - xs[c]}px">${colName(c)}<i class="sg-rz" data-rz="${c}"></i></div>`;
    this.colIn.innerHTML = ch;
    let rh = ""; for (let r = r1; r <= r2; r++) rh += `<div class="sg-rh${r >= this.sel.r1 && r <= this.sel.r2 ? " on" : ""}" data-r="${r}" style="top:${r * ROW_H}px">${r + 1}</div>`;
    this.rowIn.innerHTML = rh;
  }
  growLayout() {
    const s = this.sheet; const xs = [0]; for (let c = 0; c < this.vCols; c++) xs.push(xs[c] + (s.widths[c] || DEF_W)); s._xs = xs;
    this.sizer.style.width = xs[this.vCols] + "px"; this.sizer.style.height = this.vRows * ROW_H + "px";
  }
  refresh() { this.wb.invalidate(); this.gen = (this.gen || 0) + 1; this.render(true); this.syncBar(); this.stat(); }
  drawSel() {
    const { r1, c1, r2, c2, ar, ac } = this.sel;
    const x = this.colX(c1), y = r1 * ROW_H, w = this.colX(c2 + 1) - x, h = (r2 - r1 + 1) * ROW_H;
    Object.assign(this.selEl.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px" });
    this.selEl.classList.toggle("multi", r1 !== r2 || c1 !== c2);
    Object.assign(this.actEl.style, { left: this.colX(ac) + "px", top: ar * ROW_H + "px", width: this.width(ac) + "px", height: ROW_H + "px" });
    if (this.fillEl) Object.assign(this.fillEl.style, { left: x + w - 4 + "px", top: y + h - 4 + "px" });
    this.colIn.querySelectorAll(".sg-h").forEach((el) => el.classList.toggle("on", +el.dataset.c >= c1 && +el.dataset.c <= c2));
    this.rowIn.querySelectorAll(".sg-rh").forEach((el) => el.classList.toggle("on", +el.dataset.r >= r1 && +el.dataset.r <= r2));
    this.syncBar(); this.stat();
  }
  syncBar() {
    const { r1, c1, r2, c2, ar, ac } = this.sel;
    this.nameBox.value = r1 === r2 && c1 === c2 ? cellRef(ar, ac) : `${cellRef(r1, c1)}:${cellRef(r2, c2)}`;
    if (!this.editing) this.input.value = this.source(ar, ac);
  }
  stat() {
    const { r1, c1, r2, c2 } = this.sel;
    if (r1 === r2 && c1 === c2) { this.statEl.textContent = ""; return; }
    let sum = 0, n = 0, cnt = 0; const s = this.sheet;
    const rMax = Math.min(r2, s.rows.length - 1);
    for (let r = r1; r <= rMax; r++) for (let c = c1; c <= Math.min(c2, c1 + 200); c++) { const v = this.wb.value(s, r, c); if (v != null && v !== "") cnt++; if (typeof v === "number") { sum += v; n++; } }
    this.statEl.textContent = n ? `${t("sg_sum")}: ${display(sum)} · ${t("sg_avg")}: ${display(+(sum / n).toPrecision(10))} · ${t("sg_count")}: ${cnt}` : cnt ? `${t("sg_count")}: ${cnt}` : "";
  }
  scrollTo(r, c) {
    const sc = this.scroll, y = r * ROW_H, x = this.colX(c), w = this.width(c);
    if (y < sc.scrollTop) sc.scrollTop = y; else if (y + ROW_H > sc.scrollTop + sc.clientHeight) sc.scrollTop = y + ROW_H - sc.clientHeight;
    if (x < sc.scrollLeft) sc.scrollLeft = x; else if (x + w > sc.scrollLeft + sc.clientWidth) sc.scrollLeft = x + w - sc.clientWidth;
  }
  select(sel, scroll = true) {
    this.sel = { ...sel, r1: Math.min(sel.r1, sel.r2), r2: Math.max(sel.r1, sel.r2), c1: Math.min(sel.c1, sel.c2), c2: Math.max(sel.c1, sel.c2) };
    if (scroll) this.scrollTo(this.sel.focusR ?? sel.ar, this.sel.focusC ?? sel.ac);
    this.drawSel();
  }
  selectCell(r, c, extend) {
    r = Math.max(0, r); c = Math.max(0, c);
    if (extend) { const { ar, ac } = this.sel; this.select({ r1: ar, c1: ac, r2: r, c2: c, ar, ac, focusR: r, focusC: c }); }
    else this.select({ r1: r, c1: c, r2: r, c2: c, ar: r, ac: c });
  }
  goto(text) {
    const s = String(text || "").trim(); const bang = s.lastIndexOf("!");
    if (bang > 0) { const i = this.sheets.findIndex((x) => x.name.toLowerCase() === s.slice(0, bang).replace(/^'|'$/g, "").toLowerCase()); if (i >= 0 && i !== this.index) this.show(i); }
    const g = parseRange(bang > 0 ? s.slice(bang + 1) : s); if (!g) { this.syncBar(); return; }
    this.select({ ...g, ar: g.r1, ac: g.c1 });
  }
  hit(e) {
    const rect = this.scroll.getBoundingClientRect();
    const x = e.clientX - rect.left + this.scroll.scrollLeft, y = e.clientY - rect.top + this.scroll.scrollTop;
    if (e.clientX - rect.left > this.scroll.clientWidth || e.clientY - rect.top > this.scroll.clientHeight) return null;   // scrollbar
    return { r: Math.max(0, Math.floor(y / ROW_H)), c: Math.max(0, this.colAt(x)) };
  }
  // ---------------------------------------------------------------- pointer
  onPointer(e) {
    if (e.button !== 0) return;
    if (e.target === this.fillEl) { this.fillDrag(e); return; }
    const p = this.hit(e); if (!p) return;
    if (this.editing && this.editing.value.startsWith("=") && this.editor && e.target !== this.editor) {   // formula: click inserts a reference
      e.preventDefault(); this.insertRef(p, e); return;
    }
    if (e.target === this.editor) return;
    this.commitEdit();
    e.preventDefault(); this.scroll.focus({ preventScroll: true });
    this.selectCell(p.r, p.c, e.shiftKey);
    const move = (ev) => { const q = this.hit(ev); if (q) { const { ar, ac } = this.sel; this.select({ r1: ar, c1: ac, r2: q.r, c2: q.c, ar, ac, focusR: q.r, focusC: q.c }); } };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  insertRef(p, e) {
    const ed = this.editor || this.input; const v = ed.value;
    const ref = cellRef(p.r, p.c);
    let start = ed.selectionStart ?? v.length, end = ed.selectionEnd ?? v.length;
    if (this.refSpan && this.refSpan.end === start) start = this.refSpan.start;    // a second click replaces the ref just inserted
    const text = e.shiftKey && this.refSpan ? v.slice(this.refSpan.start, this.refSpan.end).split(":")[0] + ":" + ref : ref;
    if (e.shiftKey && this.refSpan) start = this.refSpan.start;
    const nv = v.slice(0, start) + text + v.slice(end);
    ed.value = nv; this.editing.value = nv; this.input.value = nv; if (this.editor) this.editor.value = nv;
    this.refSpan = { start, end: start + text.length };
    ed.focus(); ed.setSelectionRange(start + text.length, start + text.length);
  }
  onHeadPointer(e, kind) {
    const rz = e.target.closest("[data-rz]");
    if (rz && kind === "col") { this.resizeCol(e, +rz.dataset.rz); return; }
    const el = e.target.closest(kind === "col" ? "[data-c]" : "[data-r]"); if (!el) return;
    this.commitEdit(); e.preventDefault();
    const d = this.dims(), lastR = Math.max(d.rows, 1) - 1, lastC = Math.max(d.cols, 1) - 1;
    const pick = (i, from) => (kind === "col" ? { r1: 0, r2: lastR, c1: from, c2: i, ar: 0, ac: from } : { r1: from, r2: i, c1: 0, c2: lastC, ar: from, ac: 0 });
    const i0 = kind === "col" ? +el.dataset.c : +el.dataset.r;
    this.select(pick(i0, i0), false); this.scroll.focus({ preventScroll: true });
    const move = (ev) => { const x = document.elementFromPoint(ev.clientX, ev.clientY)?.closest?.(kind === "col" ? ".sg-h" : ".sg-rh"); if (x) this.select(pick(kind === "col" ? +x.dataset.c : +x.dataset.r, i0), false); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  resizeCol(e, c) {   // view only: column widths of the file are kept as they are
    e.preventDefault(); e.stopPropagation();
    const x0 = e.clientX, w0 = this.width(c);
    const move = (ev) => { this.sheet.widths[c] = clamp(w0 + ev.clientX - x0, 24, 900); this.layout(); this.render(true); this.drawSel(); };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  // ---------------------------------------------------------------- keyboard
  onKey(e) {
    if (this.editing) return;
    const { ar, ac } = this.sel, mod = e.ctrlKey || e.metaKey;
    const ext = e.shiftKey; const head = ext ? { r: this.sel.focusR ?? (this.sel.r1 === ar ? this.sel.r2 : this.sel.r1), c: this.sel.focusC ?? (this.sel.c1 === ac ? this.sel.c2 : this.sel.c1) } : { r: ar, c: ac };
    const move = (dr, dc) => { e.preventDefault(); let r = head.r + dr, c = head.c + dc; if (mod) [r, c] = this.edge(head.r, head.c, dr, dc); this.selectCell(clamp(r, 0, 1048575), clamp(c, 0, 16383), ext); };
    switch (e.key) {
      case "ArrowDown": return move(1, 0);
      case "ArrowUp": return move(-1, 0);
      case "ArrowLeft": return move(0, -1);
      case "ArrowRight": return move(0, 1);
      case "PageDown": return move(Math.floor(this.scroll.clientHeight / ROW_H) - 1, 0);
      case "PageUp": return move(-Math.floor(this.scroll.clientHeight / ROW_H) + 1, 0);
      case "Home": e.preventDefault(); return this.selectCell(mod ? 0 : head.r, 0, ext);
      case "End": { e.preventDefault(); const row = this.sheet.rows[head.r] || []; return this.selectCell(head.r, Math.max(0, row.length - 1), ext); }
      case "Tab": e.preventDefault(); return this.selectCell(ar, Math.max(0, ac + (e.shiftKey ? -1 : 1)));
      case "Enter": e.preventDefault(); return this.selectCell(Math.max(0, ar + (e.shiftKey ? -1 : 1)), ac);   // as in Excel: F2 / typing edits
      case "F2": e.preventDefault(); return this.editable && this.startEdit(null);
      case "Delete": case "Backspace": e.preventDefault(); return this.editable && this.clearSel();
      case "Escape": return;
    }
    if (mod && (e.key === "z" || e.key === "Z" || e.key === "я" || e.key === "Я")) { e.preventDefault(); e.stopPropagation(); return e.shiftKey ? this.redo() : this.undo(); }
    if (mod && (e.key === "y" || e.key === "Y" || e.key === "н")) { e.preventDefault(); e.stopPropagation(); return this.redo(); }
    if (mod && (e.key === "a" || e.key === "ф")) { e.preventDefault(); const d = this.dims(); return this.select({ r1: 0, c1: 0, r2: Math.max(0, d.rows - 1), c2: Math.max(0, d.cols - 1), ar: 0, ac: 0 }, false); }
    if (mod && (e.key === "d" || e.key === "в")) { e.preventDefault(); return this.editable && this.fillDown(); }
    if (!mod && !e.altKey && e.key.length === 1 && this.editable) { e.preventDefault(); this.startEdit(e.key); }
  }
  edge(r, c, dr, dc) {   // Ctrl+arrow: to the end of the data block
    const filled = (rr, cc) => rr >= 0 && cc >= 0 && this.source(rr, cc) !== "";
    const d = this.dims(); const maxR = Math.max(d.rows, 1) - 1, maxC = Math.max(d.cols, 1) - 1;
    let rr = r + dr, cc = c + dc;
    if (filled(r, c) && filled(rr, cc)) { while (filled(rr + dr, cc + dc) && rr + dr <= maxR && cc + dc <= maxC) { rr += dr; cc += dc; } return [rr, cc]; }
    while (rr >= 0 && cc >= 0 && rr <= maxR && cc <= maxC && !filled(rr, cc)) { rr += dr; cc += dc; }
    return [clamp(rr, 0, dr > 0 ? Math.max(maxR, r) : maxR), clamp(cc, 0, dc > 0 ? Math.max(maxC, c) : maxC)];
  }
  // ---------------------------------------------------------------- editing
  startEdit(initial, at = null, where = "cell") {
    if (!this.editable) return;
    if (at) this.selectCell(at.r, at.c);
    const { ar, ac } = this.sel;
    const value = initial != null && where !== "bar" ? initial : this.source(ar, ac);
    this.editing = { r: ar, c: ac, value, sheet: this.sheet };
    this.refSpan = null;
    if (where === "bar") { this.input.value = value; return; }
    const ed = document.createElement("input"); ed.className = "sg-editor"; ed.spellcheck = false; ed.value = value;
    Object.assign(ed.style, { left: this.colX(ac) + "px", top: ar * ROW_H + "px", minWidth: this.width(ac) + "px", height: ROW_H + "px" });
    this.sizer.append(ed); this.editor = ed;
    ed.addEventListener("keydown", (e) => this.onEditorKey(e));
    ed.addEventListener("input", () => { this.editing.value = ed.value; this.input.value = ed.value; this.refSpan = null; this.fit(ed); this.hint(ed); });
    ed.addEventListener("blur", () => setTimeout(() => { if (this.editor === ed && document.activeElement !== this.input && !this.el.contains(document.activeElement)) this.commitEdit(); }, 120));
    this.input.value = value; this.fit(ed);
    ed.focus(); const n = ed.value.length; ed.setSelectionRange(n, n);
  }
  fit(ed) { ed.style.width = Math.max(this.width(this.editing?.c ?? 0), Math.min(600, ed.value.length * 7.6 + 24)) + "px"; }
  hint(ed) {   // function names while typing a formula
    const m = /=.*?([A-Za-z]{2,})$/.exec(ed.value.slice(0, ed.selectionStart));
    const list = m ? FUNCTIONS.filter((f) => f.startsWith(m[1].toUpperCase())).slice(0, 6) : [];
    let tip = this.sizer.querySelector(".sg-tip");
    if (!list.length) { tip?.remove(); return; }
    if (!tip) { tip = document.createElement("div"); tip.className = "sg-tip"; this.sizer.append(tip); }
    tip.style.left = ed.style.left; tip.style.top = parseInt(ed.style.top) + ROW_H + 2 + "px";
    tip.innerHTML = list.map((f, i) => `<button data-fn="${f}" class="${i === 0 ? "on" : ""}">${f}</button>`).join("");
    tip.onmousedown = (e) => { const b = e.target.closest("[data-fn]"); if (!b) return; e.preventDefault(); this.completeFn(ed, b.dataset.fn); };
    this.tipFirst = list[0];
  }
  completeFn(ed, fn) {
    const pos = ed.selectionStart; const before = ed.value.slice(0, pos).replace(/[A-Za-z]+$/, fn + "(");
    ed.value = before + ed.value.slice(pos); this.editing.value = ed.value; this.input.value = ed.value;
    ed.setSelectionRange(before.length, before.length); this.sizer.querySelector(".sg-tip")?.remove(); this.tipFirst = null;
  }
  onEditorKey(e) {
    if (!this.editing) return;
    const tip = this.sizer.querySelector(".sg-tip");
    if (e.key === "Tab" && tip && this.tipFirst && e.target === this.editor) { e.preventDefault(); this.completeFn(this.editor, this.tipFirst); return; }
    if (e.key === "Enter" && !e.altKey) { e.preventDefault(); const { r, c } = this.editing; this.commitEdit(); this.selectCell(Math.max(0, r + (e.shiftKey ? -1 : 1)), c); this.scroll.focus({ preventScroll: true }); }
    else if (e.key === "Tab") { e.preventDefault(); const { r, c } = this.editing; this.commitEdit(); this.selectCell(r, Math.max(0, c + (e.shiftKey ? -1 : 1))); this.scroll.focus({ preventScroll: true }); }
    else if (e.key === "Escape") { e.preventDefault(); this.cancelEdit(); this.scroll.focus({ preventScroll: true }); }
    else if (e.target === this.editor && (e.key === "ArrowUp" || e.key === "ArrowDown") && !this.editing.value.startsWith("=")) { e.preventDefault(); const { r, c } = this.editing; this.commitEdit(); this.selectCell(Math.max(0, r + (e.key === "ArrowUp" ? -1 : 1)), c); this.scroll.focus({ preventScroll: true }); }
    e.stopPropagation();
  }
  cancelEdit() { this.editor?.remove(); this.editor = null; this.editing = null; this.sizer.querySelector(".sg-tip")?.remove(); this.syncBar(); }
  commitEdit() {
    const ed = this.editing; if (!ed) return;
    this.editor?.remove(); this.editor = null; this.editing = null; this.sizer.querySelector(".sg-tip")?.remove();
    const sheet = ed.sheet; if (!this.sheets.includes(sheet)) return;
    let value = ed.value;
    if (value.startsWith("=") && value.length > 1) value = "=" + value.slice(1).replace(/\s+$/, "") + ")".repeat(Math.max(0, (value.match(/\(/g) || []).length - (value.match(/\)/g) || []).length));
    if (value === this.source(ed.r, ed.c, sheet)) { this.syncBar(); return; }
    this.apply([{ sheet, r: ed.r, c: ed.c, value }]);
  }
  /** One undoable step: [{sheet, r, c, value ("=f" | text)}] (+ new sheets). */
  apply(changes, opts = {}) {
    const step = { cells: [], sheets: opts.newSheets || [], label: opts.label };
    for (const ch of changes) {
      const s = ch.sheet; const before = { v: this.raw(ch.r, ch.c, s), f: this.formula(ch.r, ch.c, s) };
      const after = String(ch.value ?? "").startsWith("=") && String(ch.value).length > 1 ? { v: "", f: String(ch.value).slice(1) } : { v: String(ch.value ?? ""), f: "" };
      if (before.v === after.v && before.f === after.f) continue;
      step.cells.push({ sheet: s, r: ch.r, c: ch.c, before, after });
    }
    if (!step.cells.length && !step.sheets.length) return null;
    this.redoStack = []; this.undoStack.push(step); if (this.undoStack.length > 300) this.undoStack.shift();
    this.play(step, "after");
    return step;
  }
  play(step, side) {
    if (side === "after") for (const s of step.sheets) if (!this.sheets.includes(s)) { this.sheets.push(s); this.wb.reindex(); }
    for (const x of step.cells) this.put(x.sheet, x.r, x.c, x[side]);
    if (side === "before") for (const s of step.sheets) { const i = this.sheets.indexOf(s); if (i >= 0) { this.sheets.splice(i, 1); this.wb.reindex(); if (this.index >= this.sheets.length) this.index = this.sheets.length - 1; } }
    this.newSheets = this.sheets.filter((s) => s.isNew);
    if (step.sheets.length) { this.renderTabs(); this.layout(); }
    const cur = step.cells.find((x) => x.sheet === this.sheet);
    if (step.cells.some((x) => x.sheet === this.sheet && (x.r >= this.vRows - 8 || x.c >= this.vCols - 2))) this.layout();
    this.refresh();
    if (cur && side === "before") this.selectCell(cur.r, cur.c);
    this.changed();
  }
  put(sheet, r, c, val) {
    while (sheet.rows.length <= r) sheet.rows.push([]);
    const row = sheet.rows[r]; while (row.length <= c) row.push("");
    row[c] = val.v; const k = r + "," + c;
    if (val.f) sheet.formulas[k] = val.f; else delete sheet.formulas[k];
    while (row.length && row[row.length - 1] === "" && !sheet.formulas[r + "," + (row.length - 1)]) row.pop();
    sheet.cols = Math.max(sheet.cols, c + 1);
    if (!this.dirty.has(sheet)) this.dirty.set(sheet, new Set());
    this.dirty.get(sheet).add(k);
  }
  undo() { const s = this.undoStack.pop(); if (!s) return false; this.redoStack.push(s); this.play(s, "before"); return true; }
  redo() { const s = this.redoStack.pop(); if (!s) return false; this.undoStack.push(s); this.play(s, "after"); return true; }
  get isDirty() { return (this.undoStack[this.undoStack.length - 1] || null) !== (this.savedTop || null); }
  changed() { this.hooks.onChange?.(this.isDirty); }
  clearSel() {
    const { r1, c1, r2, c2 } = this.sel; const out = [];
    for (let r = r1; r <= Math.min(r2, this.sheet.rows.length - 1); r++) for (let c = c1; c <= c2; c++) if (this.source(r, c) !== "") out.push({ sheet: this.sheet, r, c, value: "" });
    this.apply(out);
  }
  addSheet(name) {
    const sheet = normSheet({ name: uniqueName(name || t("sg_sheet_n", { n: this.sheets.length + 1 }), this.sheets), rows: [] }); sheet.isNew = true;
    this.apply([], { newSheets: [sheet] });
    this.show(this.sheets.indexOf(sheet));
    return sheet;
  }
  // ---------------------------------------------------------------- clipboard and fill
  block() { const { r1, c1, r2, c2 } = this.sel; const rows = []; for (let r = r1; r <= r2; r++) { const row = []; for (let c = c1; c <= c2; c++) row.push(this.source(r, c)); rows.push(row); } return rows; }
  onCopy(e, cut) {
    if (this.editing) return;
    e.preventDefault();
    const { r1, c1, r2, c2 } = this.sel; const s = this.sheet;
    const shown = []; for (let r = r1; r <= r2; r++) { const row = []; for (let c = c1; c <= c2; c++) row.push(this.formula(r, c) ? this.wb.text(s, r, c) : this.raw(r, c)); shown.push(row.join("\t")); }
    const text = shown.join("\n");
    e.clipboardData.setData("text/plain", text);
    SheetGrid.clip = { text, rows: this.block(), r: r1, c: c1 };
    if (cut && this.editable) this.clearSel();
  }
  onPaste(e) {
    if (this.editing || !this.editable) return;
    e.preventDefault();
    const text = e.clipboardData.getData("text/plain"); if (!text) return;
    const own = SheetGrid.clip && SheetGrid.clip.text === text.replace(/\r\n/g, "\n").replace(/\n$/, "") ? SheetGrid.clip : null;
    const rows = own ? own.rows : text.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n").map((l) => l.split("\t"));
    const { r1, c1 } = this.sel; const out = [];
    rows.forEach((row, i) => row.forEach((v, j) => {
      let value = v;
      if (own && v.startsWith("=")) value = "=" + shiftFormula(v.slice(1), r1 - own.r, c1 - own.c);
      out.push({ sheet: this.sheet, r: r1 + i, c: c1 + j, value });
    }));
    this.apply(out);
    this.select({ r1, c1, r2: r1 + rows.length - 1, c2: c1 + Math.max(...rows.map((r) => r.length)) - 1, ar: r1, ac: c1 });
  }
  fillDown() { const { r1, c1, r2, c2 } = this.sel; if (r2 > r1) this.fill({ r1, c1, r2: r1, c2 }, { r1, c1, r2, c2 }); }
  fillDrag(e) {
    e.preventDefault(); e.stopPropagation();
    const src = { ...this.sel }; let target = src;
    const ghost = document.createElement("div"); ghost.className = "sg-fillghost"; this.sizer.append(ghost);
    const move = (ev) => {
      const q = this.hit(ev); if (!q) return;
      const down = q.r - src.r2, right = q.c - src.c2;
      target = Math.abs(down) >= Math.abs(right) ? (q.r > src.r2 ? { ...src, r2: q.r } : q.r < src.r1 ? { ...src, r1: q.r } : src) : (q.c > src.c2 ? { ...src, c2: q.c } : q.c < src.c1 ? { ...src, c1: q.c } : src);
      Object.assign(ghost.style, { left: this.colX(target.c1) + "px", top: target.r1 * ROW_H + "px", width: this.colX(target.c2 + 1) - this.colX(target.c1) + "px", height: (target.r2 - target.r1 + 1) * ROW_H + "px" });
    };
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); ghost.remove(); if (target !== src) { this.fill(src, target); this.select({ ...target, ar: src.ar, ac: src.ac }, false); } };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up);
  }
  /** Fill target from src (Excel rules): numbers in steps (2+ cells) or copies, "Item 1" → "Item 2", formulas shifted. */
  fill(src, target) {
    const out = []; const s = this.sheet;
    const vertical = target.r1 !== src.r1 || target.r2 !== src.r2;
    const lines = vertical ? range(src.c1, src.c2) : range(src.r1, src.r2);
    for (const line of lines) {
      const seq = vertical ? range(src.r1, src.r2).map((r) => ({ r, c: line })) : range(src.c1, src.c2).map((c) => ({ r: line, c }));
      const vals = seq.map((p) => this.source(p.r, p.c, s));
      const numsOnly = vals.every((v) => v !== "" && !v.startsWith("=") && typeof valueOf(v) === "number");
      const step = numsOnly && vals.length > 1 ? valueOf(vals[1]) - valueOf(vals[0]) : null;
      const linear = step != null && vals.every((v, i) => Math.abs(valueOf(v) - (valueOf(vals[0]) + i * step)) < 1e-9);
      const cells = vertical ? range(target.r1, target.r2).filter((r) => r < src.r1 || r > src.r2).map((r) => ({ r, c: line })) : range(target.c1, target.c2).filter((c) => c < src.c1 || c > src.c2).map((c) => ({ r: line, c }));
      const n = vals.length;
      for (const p of cells) {
        const off = vertical ? p.r - src.r1 : p.c - src.c1;            // position relative to the source start
        const k = ((off % n) + n) % n; const base = seq[k]; const v = vals[k];
        let value = v;
        if (linear) value = String(+(valueOf(vals[0]) + off * step).toPrecision(15));
        else if (v.startsWith("=")) value = "=" + shiftFormula(v.slice(1), p.r - base.r, p.c - base.c);
        else { const m = /^(.*?)(\d+)$/.exec(v); if (m && n === 1 && typeof valueOf(v) !== "number") value = m[1] + String(+m[2] + off).padStart(m[2].length, "0"); }
        out.push({ sheet: s, r: p.r, c: p.c, value });
      }
    }
    this.apply(out);
  }
  // ---------------------------------------------------------------- saving
  changes() {
    const sheets = [];
    for (const [sheet, keys] of this.dirty) {
      if (!this.sheets.includes(sheet) || !keys.size) continue;
      const cells = {};
      for (const k of keys) {
        const [r, c] = k.split(",").map(Number); const f = this.formula(r, c, sheet);
        cells[cellRef(r, c)] = f ? { f, v: this.wb.text(sheet, r, c) } : { v: this.raw(r, c, sheet) };
      }
      sheets.push({ name: sheet.name, cells });
    }
    return { new_sheets: this.newSheets.map((s) => ({ name: s.name })), sheets };
  }
  async save(path, opts = {}) {
    this.commitEdit();
    const r = await api("sheet_save", { path, target: opts.target, mtime: opts.mtime, force: !!opts.force, changes: this.changes() }, { allowError: true });
    if (!r.ok) { if (r.conflict) return r; throw new Error(r.error || "save failed"); }
    this.dirty.clear();
    for (const s of this.sheets) s.isNew = false;
    this.newSheets = [];
    this.savedTop = this.undoStack[this.undoStack.length - 1] || null;   // undo history stays usable after a save
    // the server read the file back: refresh cached values (formulas keep their computed values)
    for (const fresh of r.sheets || []) { const s = this.sheets.find((x) => x.name === fresh.name); if (s) { s.rows = fresh.rows; s.formulas = fresh.formulas || {}; } }
    this.refresh(); this.changed();
    return r;
  }
  // ---------------------------------------------------------------- AI
  applyPlan(plan) {
    for (const s of plan.newSheets) if (this.sheets.some((x) => x.name.toLowerCase() === s.name.toLowerCase())) s.name = uniqueName(s.name, this.sheets);
    const step = this.apply(plan.cells.map((x) => ({ sheet: x.sheet, r: x.r, c: x.c, value: x.value })), { newSheets: plan.newSheets.filter((s) => !this.sheets.includes(s)), label: "ai" });
    const first = plan.cells[0];
    if (first) { const i = this.sheets.indexOf(first.sheet); if (i >= 0 && i !== this.index) this.show(i); this.flash(plan.cells.filter((x) => x.sheet === first.sheet)); }
    return step;
  }
  undoStep(step) { if (!step || this.undoStack[this.undoStack.length - 1] !== step) return false; return this.undo(); }
  flash(cells) {
    if (!cells.length) return;
    const r1 = Math.min(...cells.map((x) => x.r)), r2 = Math.max(...cells.map((x) => x.r)), c1 = Math.min(...cells.map((x) => x.c)), c2 = Math.max(...cells.map((x) => x.c));
    this.select({ r1, c1, r2, c2, ar: r1, ac: c1 });
    const f = this.flashEl; f.hidden = false;
    Object.assign(f.style, { left: this.colX(c1) + "px", top: r1 * ROW_H + "px", width: this.colX(c2 + 1) - this.colX(c1) + "px", height: (r2 - r1 + 1) * ROW_H + "px" });
    f.classList.remove("go"); void f.offsetWidth; f.classList.add("go");
    clearTimeout(this._flashT); this._flashT = setTimeout(() => { f.hidden = true; }, 1600);
  }
  focusGrid() { this.scroll.focus({ preventScroll: true }); }
  destroy() { this.ro?.disconnect(); cancelAnimationFrame(this.raf); this.el.remove(); }
}

function normSheet(s) {
  const rows = (s.rows || []).map((r) => r.slice());
  const formulas = { ...(s.formulas || {}) };
  const widths = {}; for (const [k, v] of Object.entries(s.widths || {})) widths[+k] = Math.max(24, Math.min(900, +v || DEF_W));
  let cols = 0; for (const r of rows.slice(0, 5000)) cols = Math.max(cols, r.length);
  for (const k of Object.keys(formulas)) cols = Math.max(cols, +k.split(",")[1] + 1);
  // merged areas: the top-left cell spans the area, the others are covered (and count as used columns)
  const spans = new Map(), covered = new Set();
  for (const ref of (s.merges || []).slice(0, 2000)) {
    const g = parseRange(ref); if (!g || (g.r2 - g.r1 + 1) * (g.c2 - g.c1 + 1) > 20000) continue;
    spans.set(g.r1 + "," + g.c1, g); cols = Math.max(cols, g.c2 + 1);
    for (let r = g.r1; r <= g.r2; r++) for (let c = g.c1; c <= g.c2; c++) if (r !== g.r1 || c !== g.c1) covered.add(r + "," + c);
  }
  return { name: s.name || "Sheet1", rows, formulas, widths, cols, truncated: !!s.truncated, merges: s.merges || [], styles: s.styles || {}, spans, covered };
}
function uniqueName(name, sheets) {
  const base = String(name || "Sheet").replace(/[[\]:*?/\\]/g, " ").trim().slice(0, 31) || "Sheet";
  const taken = new Set(sheets.map((s) => s.name.toLowerCase())); let n = 2, out = base;
  while (taken.has(out.toLowerCase())) out = `${base.slice(0, 26)} (${n++})`;
  return out;
}
function lookCss(l) {
  if (!l || !Object.keys(l).length) return null;
  const safe = (c) => (/^#[0-9a-f]{6}$/i.test(c || "") ? c : "");
  let css = "";
  if (l.b) css += ";font-weight:650"; if (l.i) css += ";font-style:italic";
  if (safe(l.fill)) css += `;background:${l.fill}`; if (safe(l.color)) css += `;color:${l.color}`;
  if (l.align) css += `;text-align:${l.align}`;
  return { css, fill: safe(l.fill), fmt: l.fmt || "" };
}
/** A number shown with an Excel format code (first section): decimals, thousands, %, currency / literal text. */
export function formatNumber(n, code) {
  const sec = String(code).split(/;(?=(?:[^"]*"[^"]*")*[^"]*$)/)[n < 0 && /;/.test(code) ? 1 : 0] || code;
  const body = sec.replace(/\[\$([^\]-]*)[^\]]*\]/g, '"$1"').replace(/\[[^\]]*\]/g, "").replace(/_.|\*./g, "");
  const m = /[0#?][0#?,.]*(?:E[+-]0+)?%?/.exec(body.replace(/"[^"]*"/g, (q) => "\u0001".repeat(q.length)));
  if (!m || /E[+-]/.test(m[0])) return display(n);
  const lit = (x) => x.replace(/"([^"]*)"/g, "$1").replace(/\\(.)/g, "$1");
  const prefix = lit(body.slice(0, m.index)), suffix = lit(body.slice(m.index + m[0].length));
  const pat = m[0], pct = pat.endsWith("%") || /%/.test(suffix);
  const dec = (/\.([0#?]+)/.exec(pat) || [, ""])[1].length;
  const value = Math.abs(n) * (pct ? 100 : 1);
  let [int, frac = ""] = value.toFixed(dec).split(".");
  if (/[0#?],[0#?]/.test(pat)) int = int.replace(/\B(?=(\d{3})+(?!\d))/g, I18N.lang === "en" ? "," : "\u00a0");
  const sign = n < 0 && !/;/.test(code) ? "-" : "";
  return sign + prefix + int + (dec ? (I18N.lang === "en" ? "." : ",") + frac : "") + (pct && !/%/.test(suffix) ? "%" : "") + suffix;
}
function range(a, b) { const out = []; for (let i = a; i <= b; i++) out.push(i); return out; }
