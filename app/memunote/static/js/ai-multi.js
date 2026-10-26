
// TabContext: the tab chips with ticks under the scope chips, @tab mentions in the composer, the token budget with

import { esc, t, showMenu } from "./core.js";
import { aic } from "./ai-edit.js";
import { SheetData } from "./sheetgrid.js";
import { cellRef } from "./formula.js";

export const MULTI_BUDGET = 120000;
const TEXT_KINDS = new Set(["text", "markdown", "code", "csv", "docx", "odt", "rtf", "html", "document", "sheet", "notebook", "book", "slides", "pdf"]);
const tokens = (chars) => Math.round(chars / 3.6);

export class TabContext {

  constructor(panel, ctx) { this.panel = panel; this.ctx = ctx; this.picked = new Set(); this.sizes = new Map(); }
  candidates() {
    const active = this.ctx.editor?.()?.tab?.id;
    return (this.ctx.tabs?.() || []).filter((x) => x.id !== active && TEXT_KINDS.has(x.kind));
  }

  share(n) { return Math.max(4000, Math.min(+this.ctx.settings().ai_context_chars || 60000, Math.floor(MULTI_BUDGET / Math.max(1, n)))); }

  html(activeChars = 0) {
    const list = this.candidates();
    for (const id of [...this.picked]) if (!list.some((x) => x.id === id)) this.picked.delete(id);
    if (!list.length) return "";
    const n = this.picked.size, share = this.share(n);
    let used = Math.min(activeChars, +this.ctx.settings().ai_context_chars || 60000);
    const chips = list.map((x) => {
      const on = this.picked.has(x.id); const size = this.sizes.get(x.id);
      const trunc = on && size > share; if (on && size) used += Math.min(size, share);
      const title = trunc ? t("ai_ctx_trunc_hint", { pct: Math.max(1, Math.round(share / size * 100)), kept: share.toLocaleString(), total: size.toLocaleString() }) : x.path || x.name;
      return `<button class="aip-tabchip ${on ? "on" : ""}" data-tabctx="${esc(x.id)}" role="checkbox" aria-checked="${on}" title="${esc(title)}"><span class="box">${on ? aic("check") : ""}</span><span class="l">${esc(x.name)}</span>${trunc ? `<span class="tr">${esc(t("ai_ctx_trunc"))}</span>` : ""}</button>`;
    }).join("");
    const budget = n ? `<span class="aip-tabbudget" title="${esc(t("ai_ctx_budget_hint", { max: MULTI_BUDGET.toLocaleString() }))}">${esc(t("ai_ctx_budget", { n: tokens(used).toLocaleString() }))}</span>` : "";
    return `<div class="aip-tabs" title="${esc(t("ai_ctx_hint"))}"><span class="aip-tabs-l">@ ${esc(t("ai_ctx_tabs"))}</span>${chips}${budget}</div>`;
  }
  toggle(id, on = !this.picked.has(id)) {
    if (on) { this.picked.add(id); if (!this.sizes.has(id)) this.measure(id); } else this.picked.delete(id);
    this.panel.renderScope();
  }
  async measure(id) {
    try { const d = await this.ctx.tabDoc(id); if (d) { this.sizes.set(id, d.text.length); this.panel.renderScope(); } } catch {  }
  }
  /** "@name" in the composer: picks the tab (names with spaces too); returns the text unchanged. */
  mentions(text) {
    const list = this.ctx.tabs?.() || [];
    const low = text.toLowerCase();
    for (const x of list.slice().sort((a, b) => b.name.length - a.name.length)) {
      if (low.includes("@" + x.name.toLowerCase()) || low.includes("@" + x.name.replace(/\.\w+$/, "").toLowerCase())) { if (x.id !== this.ctx.editor?.()?.tab?.id) this.picked.add(x.id); }
    }
  }

  onInput(input) {
    const pos = input.selectionStart, before = input.value.slice(0, pos);
    const m = /(^|\s)@([^\s@]*)$/.exec(before);
    if (!m) { this.menuFor = null; return; }
    if (this.menuFor === m[2]) return;
    this.menuFor = m[2];
    const q = m[2].toLowerCase(); const list = this.candidates().filter((x) => x.name.toLowerCase().includes(q)).slice(0, 12);
    if (!list.length) { if (!this.candidates().length) showMenu([{ header: t("ai_ctx_none") }], input); return; }
    showMenu([{ header: t("ai_ctx_mention") }, ...list.map((x) => ({ label: x.name, hint: this.picked.has(x.id) ? "✓" : "", run: () => {
      const start = pos - m[2].length - 1;
      input.value = input.value.slice(0, start) + "@" + x.name + " " + input.value.slice(pos);
      const caret = start + x.name.length + 2; input.focus(); input.setSelectionRange(caret, caret);
      this.menuFor = null; this.toggle(x.id, true); this.panel.autosize();
    } }))], input);
  }

  async collect() {
    const ids = [...this.picked]; if (!ids.length) return [];
    const share = this.share(ids.length);
    const docs = [];
    for (const id of ids) {
      const d = await this.ctx.tabDoc(id).catch(() => null); if (!d) continue;
      this.sizes.set(id, d.text.length);
      docs.push({ ref: "d" + (docs.length + 1), id, name: d.name, kind: d.kind, full: d.text, text: clip(d.text, share), total_chars: d.text.length, truncated: d.text.length > share, editKind: d.editKind, readOnly: d.readOnly });
    }
    return docs;
  }
}
function clip(text, limit) {
  if (text.length <= limit) return text;
  const head = Math.floor(limit * 0.7), tail = limit - head - 80;
  return text.slice(0, head) + "\n\n[… part of the document omitted …]\n\n" + text.slice(text.length - tail);
}

let seq = 0;
export class SheetProposal {

  constructor(target, onChange) { this.target = target; this.onChange = onChange || (() => {}); this.hunks = []; this.applied = null; this.previewSheets = []; }
  get pending() { return this.hunks.filter((h) => h.status === "pending"); }
  view() { return null; }
  model() { return this.target.grid?.() || this.target.data(); }
  add(edit) {
    if (edit.op === "new_document") { this.hunks.push({ id: "s" + (++seq), op: edit.op, virtual: true, title: edit.title, docKind: edit.kind, insert: edit.text, del: "", status: "pending", incomplete: edit.incomplete }); this.onChange(this); return; }
    const model = this.model();
    if (!model.editable) { this.fail({ op: edit.op, error: "read_only" }); return; }
    const plan = model.plan([edit], this.previewSheets);
    if (plan.errors.length) { this.fail(plan.errors[0]); return; }
    this.previewSheets.push(...plan.newSheets);
    this.hunks.push({ id: "s" + (++seq), op: edit.op, edit, status: "pending", insert: "", del: "", incomplete: edit.incomplete,
      where: where(plan, edit), html: previewHtml(model, plan) });
    this.onChange(this);
  }
  fail(meta) { this.hunks.push({ id: "s" + (++seq), insert: "", del: "", status: "failed", ...meta }); this.onChange(this); }
  accept(id) { const h = this.hunks.find((x) => x.id === id && x.status === "pending"); if (h) this.applyHunks([h]); }
  reject(id) { const h = this.hunks.find((x) => x.id === id && x.status === "pending"); if (h) { h.status = "rejected"; this.onChange(this); } }
  rejectAll() { for (const h of this.pending) h.status = "rejected"; this.onChange(this); }
  applyAll() { const list = this.pending; if (list.length) this.applyHunks(list); }

  applyHunks(list) {
    for (const h of list.filter((x) => x.virtual)) { this.target.newTab?.(h.insert, h.title, h.docKind); h.status = "applied"; }
    list = list.filter((x) => !x.virtual && x.status === "pending");
    if (!list.length) { this.onChange(this); return; }
    this.target.activate?.();
    const grid = this.target.grid?.();
    if (!grid) { setTimeout(() => { if (this.target.grid?.()) this.applyHunks(list); }, 60); return; }
    const plan = grid.plan(list.map((h) => h.edit));
    for (const e of plan.errors) { const h = list.find((x) => x.edit.op === e.op && x.status === "pending"); if (h) { h.status = "failed"; h.error = e.error; } }
    const step = grid.applyPlan(plan);
    const ok = list.filter((h) => h.status === "pending");
    for (const h of ok) h.status = "applied";
    if (step) this.applied = { step, ids: ok.map((h) => h.id) };
    this.onChange(this);
  }
  undo() {
    if (!this.applied) return false;
    this.target.activate?.();
    const grid = this.target.grid?.(); if (!grid || !grid.undoStep(this.applied.step)) return false;
    for (const h of this.hunks) if (this.applied.ids.includes(h.id)) h.status = "pending";
    this.applied = null; this.onChange(this);
    return true;
  }
  scrollTo(id) {
    const h = this.hunks.find((x) => x.id === id); if (!h || h.virtual || !h.edit) return;
    this.target.activate?.();
    const grid = this.target.grid?.(); if (!grid) return;
    const plan = grid.plan([h.edit]);
    const cells = plan.cells.filter((x) => grid.sheets.includes(x.sheet));
    if (cells.length) { const i = grid.sheets.indexOf(cells[0].sheet); if (i !== grid.index) grid.show(i); grid.flash(cells.filter((x) => x.sheet === cells[0].sheet)); }
  }
  dispose() {}
}
function where(plan, edit) {
  if (edit.op === "add_sheet") return edit.name;
  const cells = plan.cells; if (!cells.length) return edit.sheet || "";
  const r1 = Math.min(...cells.map((x) => x.r)), r2 = Math.max(...cells.map((x) => x.r)), c1 = Math.min(...cells.map((x) => x.c)), c2 = Math.max(...cells.map((x) => x.c));
  const range = r1 === r2 && c1 === c2 ? cellRef(r1, c1) : `${cellRef(r1, c1)}:${cellRef(r2, c2)}`;
  return `${cells[0].sheet.name}!${range}`;
}
function previewHtml(model, plan) {
  const rows = plan.cells.slice(0, 8).map((x) => {
    const after = model.previewValue(x);
    return `<div class="aip-cell"><code>${esc(cellRef(x.r, x.c))}</code>${x.before !== "" ? `<del>${esc(String(x.before).slice(0, 80))}</del>` : ""}<ins>${esc(String(after).slice(0, 120)) || "∅"}</ins></div>`;
  }).join("");
  const more = plan.cells.length > 8 ? `<div class="aip-cell more">${esc(t("ai_more_cells", { n: plan.cells.length - 8 }))}</div>` : "";
  const sheets = plan.newSheets.map((s) => `<div class="aip-cell new">${aic("table")}<b>${esc(s.name)}</b></div>`).join("");
  return sheets + rows + more || `<div class="aip-cell more">∅</div>`;
}
export function headlessSheet(tab) { return new SheetData(tab); }
