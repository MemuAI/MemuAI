// MemuNote — AI document editing engine (Round 31): context excerpts for large files, word diff → hunks,
// resolution of the agent's edit operations against the real document, inline diff in CodeMirror
// (red = removed, green = proposed; per-hunk ✓/✕), "Apply all" as ONE undoable transaction,
// live "write here" streaming widget, and the same operations for rich documents (contenteditable).
import { CM } from "./editor.js";
import { esc } from "./core.js";
import { renderMarkdown } from "./views.js";

const { StateField, StateEffect, Decoration, WidgetType, EditorView } = CM;

// ------------------------------------------------------------------ extra icons of the AI panel
const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
export const AICONS = {
  diff: P('<path d="M12 3v18M5 8h4M7 6v4M15 16h4"/><rect x="3" y="3" width="18" height="18" rx="3"/>'),
  bolt: P('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>'),
  collapse: P('<path d="m13 6 6 6-6 6M5 6l6 6-6 6"/>'),
  expand: P('<path d="m11 6-6 6 6 6M19 6l-6 6 6 6"/>'),
  docScope: P('<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5"/><path d="M10 13h6M10 17h6"/>'),
  selScope: P('<path d="M5 7h3M5 12h14M5 17h9"/><path d="M4 9.5h16v5H4z" opacity=".55"/>'),
  section: P('<path d="M6 5h12M6 10h8M6 14h12M6 19h8"/><path d="M3 10v9" />'),
  chat: P('<path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z"/>'),
  edit: P('<path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
  pen: P('<path d="M12 19h8"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
  target: P('<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>'),
  x: P('<path d="m7 7 10 10M17 7 7 17"/>'),
  check: P('<path d="m5 12.5 4.2 4.2L19 7"/>'),
  checks: P('<path d="m2 12.5 4.2 4.2L16 7M12 16.7 21.8 7"/>'),
  undo: P('<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>'),
  locate: P('<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="6"/>'),
  newdoc: P('<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M13 12v6M10 15h6"/>'),
  table: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 4v16"/>'),
  copy: P('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  insert: P('<path d="M12 5v10m-4-4 4 4 4-4"/><path d="M5 19h14"/>'),
  dots: P('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
  history: P('<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>'),
};
export const aic = (name, cls = "") => `<span class="ic ${cls}">${AICONS[name] || ""}</span>`;

export { outlineOf, sectionAt, buildContext, tokenize, diffTokens, buildHunks, diffHtml, locate, resolveEdit, MARKERS_RE } from "./ai-diff.js";
import { outlineOf, sectionAt, MARKERS_RE, escRe, diffTokens, tokenize } from "./ai-diff.js";

// ------------------------------------------------------------------ CodeMirror: inline proposals + streaming
const setHunks = StateEffect.define();      // [{id, from, to, insert}]
const dropHunk = StateEffect.define();      // id
const clearHunks = StateEffect.define();
const streamEff = StateEffect.define();     // {pos, text} | null
const flashEff = StateEffect.define();      // {from, to} | null
const handlers = new Map();                 // hunk id → {accept(), reject()}
const LABELS = { accept: "Accept", reject: "Reject" };
export function setEditLabels(labels) { Object.assign(LABELS, labels); }

class InsWidget extends WidgetType {
  /** text: green proposed text (may be ""), bar: show ✓/✕, block: a full-width box between lines */
  constructor(id, text, bar = true, block = false) { super(); this.id = id; this.text = text; this.bar = bar; this.block = block; }
  eq(o) { return o.id === this.id && o.text === this.text && o.bar === this.bar && o.block === this.block; }
  toDOM() {
    const w = document.createElement(this.block ? "div" : "span"); w.className = this.block ? "mn-ai-ins-block" : "mn-ai-ins-wrap"; w.dataset.hunk = this.id;
    let shown = this.text.length > 6000 ? this.text.slice(0, 6000) + "…" : this.text;
    let tail = "";
    if (!this.block) { const m = /\n+$/.exec(shown); if (m && m.index > 0) { tail = m[0]; shown = shown.slice(0, m.index); } }
    else shown = shown.replace(/^\n+|\n+$/g, "");
    const bar = this.bar ? `<span class="mn-ai-hunk-bar" contenteditable="false"><button class="ok" data-h="accept" title="${esc(LABELS.accept)}">${AICONS.check}</button><button class="no" data-h="reject" title="${esc(LABELS.reject)}">${AICONS.x}</button></span>` : "";
    w.innerHTML = this.block ? `${bar}<div class="mn-ai-ins">${esc(shown)}</div>` : (shown ? `<span class="mn-ai-ins">${esc(shown)}</span>` : "") + bar + (tail ? `<span class="mn-ai-ins">${esc(tail)}</span>` : "");
    w.addEventListener("mousedown", (e) => { const b = e.target.closest("[data-h]"); if (b) { e.preventDefault(); e.stopPropagation(); const h = handlers.get(this.id); if (h) h[b.dataset.h](); } });
    return w;
  }
  ignoreEvent() { return true; }
}
/** Word-level parts of a replace hunk (so a one-word fix in a long sentence shows as one word, not two sentences). */
function subParts(h) {
  if (!h.del || !h.insert || h.del.length + h.insert.length > 20000) return null;
  const parts = diffTokens(tokenize(h.del), tokenize(h.insert), 400);
  if (!parts) return null;
  const changed = parts.filter((p) => p.op !== "=").reduce((n, p) => n + p.text.length, 0);
  if (changed > (h.del.length + h.insert.length) * 0.6) return null;      // mostly rewritten: show old → new whole
  return parts;
}
class StreamWidget extends WidgetType {
  constructor(text) { super(); this.text = text; }
  eq(o) { return o.text === this.text; }
  toDOM() { const s = document.createElement("span"); s.className = "mn-ai-stream"; s.textContent = this.text; const c = document.createElement("span"); c.className = "mn-ai-caret"; s.append(c); return s; }
  ignoreEvent() { return true; }
}

const hunkField = StateField.define({
  create: () => [],
  update(list, tr) {
    if (tr.docChanged && list.length) list = list.map((h) => ({ ...h, from: tr.changes.mapPos(h.from, 1), to: tr.changes.mapPos(h.to, -1) })).map((h) => (h.to < h.from ? { ...h, to: h.from, stale: true } : h));
    for (const e of tr.effects) {
      if (e.is(setHunks)) list = e.value;
      else if (e.is(dropHunk)) list = list.filter((h) => h.id !== e.value);
      else if (e.is(clearHunks)) list = [];
    }
    if (list.length && (tr.docChanged || tr.effects.length)) {
      const doc = tr.state.doc;
      list = list.map((h) => { const pos = Math.min(h.to, doc.length); const line = doc.lineAt(pos); const edge = pos === line.to ? 1 : pos === line.from ? -1 : 0; return h.edge === edge ? h : { ...h, edge }; });
    }
    return list;
  },
  provide: (f) => EditorView.decorations.from(f, (list) => {
    const ranges = [];
    for (const h of [...list].sort((a, b) => a.from - b.from || a.to - b.to)) {
      const parts = h.to > h.from && h.from >= 0 ? (h._parts !== undefined ? h._parts : (h._parts = subParts(h))) : null;
      if (parts && h.to - h.from === h.del.length) {
        let pos = h.from;
        for (const p of parts) {
          if (p.op === "=") pos += p.text.length;
          else if (p.op === "-") { ranges.push(Decoration.mark({ class: "mn-ai-del", attributes: { "data-hunk": h.id } }).range(pos, pos + p.text.length)); pos += p.text.length; }
          else ranges.push(Decoration.widget({ widget: new InsWidget(h.id + ":" + pos, p.text, false), side: 1 }).range(pos));
        }
        ranges.push(Decoration.widget({ widget: new InsWidget(h.id, "", true), side: 2 }).range(h.to));
        continue;
      }
      if (h.to > h.from) ranges.push(Decoration.mark({ class: "mn-ai-del", attributes: { "data-hunk": h.id } }).range(h.from, h.to));
      const multi = !!h.edge && (h.insert.match(/\n/g) || []).length >= 2 && h.insert.replace(/\n/g, "").length > 0;
      ranges.push(Decoration.widget({ widget: new InsWidget(h.id, h.insert, true, multi), side: multi ? h.edge : 1, block: multi }).range(h.to));
    }
    return Decoration.set(ranges, true);
  }),
});
const streamField = StateField.define({
  create: () => null,
  update(v, tr) {
    if (v && tr.docChanged) v = { ...v, pos: tr.changes.mapPos(v.pos, 1) };
    for (const e of tr.effects) if (e.is(streamEff)) v = e.value;
    return v;
  },
  provide: (f) => EditorView.decorations.from(f, (v) => (v ? Decoration.set([Decoration.widget({ widget: new StreamWidget(v.text), side: 1 }).range(v.pos)]) : Decoration.none)),
});
const flashField = StateField.define({
  create: () => null,
  update(v, tr) {
    if (v && tr.docChanged) v = { from: tr.changes.mapPos(v.from, 1), to: tr.changes.mapPos(v.to, -1) };
    for (const e of tr.effects) if (e.is(flashEff)) v = e.value;
    return v;
  },
  provide: (f) => EditorView.decorations.from(f, (v) => (v && v.to > v.from ? Decoration.set([Decoration.mark({ class: "mn-ai-flash" }).range(v.from, v.to)]) : Decoration.none)),
});
const aiTheme = EditorView.baseTheme({});

/** Add the AI fields to a view's current state once (tab states are swapped with setState). */
export function ensureAIFields(view) {
  if (!view || view.state.field(hunkField, false) !== undefined) return;
  view.dispatch({ effects: StateEffect.appendConfig.of([hunkField, streamField, flashField, aiTheme]) });
}
function flash(view, from, to) {
  view.dispatch({ effects: flashEff.of({ from, to }) });
  clearTimeout(flash.t); flash.t = setTimeout(() => { try { view.dispatch({ effects: flashEff.of(null) }); } catch { /* view gone */ } }, 1600);
}

let hunkSeq = 0;
/**
 * One proposal = a set of hunks on one tab. target: {kind: "cm"|"doc"|"new", tabId, view(): EditorView|null,
 * doc(): DocEditor|null, activate(), newTab(text, title, kind)}. onChange(proposal) re-renders the card.
 */
export class Proposal {
  constructor(target, onChange) {
    this.target = target; this.onChange = onChange || (() => {});
    this.hunks = [];          // {id, from, to, insert, del, status: pending|applied|rejected|failed, op, error, label}
    this.applied = null;      // {doc (CM Text before), hunkIds} after Apply all → Undo
    this.snapshot = null;     // rich doc: innerHTML before the first apply
  }
  get pending() { return this.hunks.filter((h) => h.status === "pending"); }
  view() { const v = this.target.view?.(); if (v) ensureAIFields(v); return v; }
  /** Add resolved changes (document offsets as of now). */
  add(changes, meta = {}) {
    const added = [];
    for (const c of changes) {
      const h = { id: "h" + (++hunkSeq), from: c.from, to: c.to, insert: c.insert, del: c.del ?? "", status: "pending", ...meta };
      if (h.virtual) { this.hunks.push(h); added.push(h); continue; }
      if (this.target.kind === "cm") { const v = this.view(); if (v) h.del = v.state.sliceDoc(h.from, h.to); }
      // overlapping hunks cannot all be applied: the later one waits as a conflict
      if (this.hunks.some((o) => o.status === "pending" && o.to > h.from && h.to > o.from && !(o.from === o.to && h.from === h.to))) { h.status = "failed"; h.error = "overlap"; }
      this.hunks.push(h); added.push(h);
    }
    this.sync();
    return added;
  }
  fail(meta) { this.hunks.push({ id: "h" + (++hunkSeq), from: 0, to: 0, insert: "", del: "", status: "failed", ...meta }); this.onChange(this); }
  /** Push pending hunks to the editor decorations (positions come back mapped through edits). */
  sync() {
    if (this.target.kind === "cm") {
      const v = this.view();
      if (v) {
        const others = (v.state.field(hunkField, false) || []).filter((x) => !this.hunks.some((h) => h.id === x.id));
        v.dispatch({ effects: setHunks.of([...others, ...this.pending.filter((h) => !h.virtual).map(({ id, from, to, insert, del }) => ({ id, from, to, insert, del }))]) });
        for (const h of this.pending) handlers.set(h.id, { accept: () => this.accept(h.id), reject: () => this.reject(h.id) });
      }
    }
    this.onChange(this);
  }
  /** Current (mapped) positions from the editor. */
  pull() {
    if (this.target.kind !== "cm") return;
    const v = this.view(); if (!v) return;
    const live = v.state.field(hunkField, false) || [];
    for (const h of this.pending) { if (h.virtual) continue; const x = live.find((y) => y.id === h.id); if (x) { h.from = x.from; h.to = x.to; if (x.stale) { h.status = "failed"; h.error = "stale"; } } }
  }
  scrollTo(id) {
    const h = this.hunks.find((x) => x.id === id); if (!h || h.virtual) return;
    this.target.activate?.();
    const v = this.view(); if (!v) return;
    // Round 40: a proposal for another tab was made while that tab was hidden — show its inline diff now
    if (h.status === "pending" && !(v.state.field(hunkField, false) || []).some((x) => x.id === id)) this.sync();
    this.pull();
    v.dispatch({ effects: EditorView.scrollIntoView(h.from, { y: "center" }) });
    flash(v, h.from, h.status === "applied" ? h.from + h.insert.length : Math.max(h.to, h.from + 1));
  }
  accept(id) { const h = this.hunks.find((x) => x.id === id && x.status === "pending"); if (h) this.applyHunks([h]); }
  reject(id) {
    const h = this.hunks.find((x) => x.id === id && x.status === "pending"); if (!h) return;
    h.status = "rejected"; handlers.delete(id);
    const v = this.target.kind === "cm" && !h.virtual && this.view(); if (v) v.dispatch({ effects: dropHunk.of(id) });
    this.onChange(this);
  }
  rejectAll() { for (const h of this.pending) this.reject(h.id); }
  applyAll() { const list = this.pending; if (list.length) this.applyHunks(list); }
  /** ONE editor transaction for all given hunks → one Ctrl+Z / "Undo" step. */
  applyHunks(list) {
    for (const h of list.filter((x) => x.virtual && x.status === "pending")) { this.target.newTab?.(h.insert, h.title, h.docKind); h.status = "applied"; handlers.delete(h.id); }
    list = list.filter((x) => !x.virtual);
    if (!list.length) { this.onChange(this); return; }
    if (this.target.kind === "doc") return this.applyDoc(list);
    this.target.activate?.();
    const v = this.view(); if (!v) { this.onChange(this); return; }
    if (v.state.readOnly) { this.target.toast?.("read_only"); return; }
    this.pull();
    list = list.filter((h) => h.status === "pending").sort((a, b) => a.from - b.from);
    const ok = []; let lastTo = -1;
    for (const h of list) { if (h.from < lastTo) { h.status = "failed"; h.error = "overlap"; continue; } ok.push(h); lastTo = h.to; }
    if (!ok.length) { this.onChange(this); return; }
    const before = v.state.doc;
    const changes = ok.map((h) => ({ from: h.from, to: h.to, insert: h.insert }));
    const set = v.state.changes(changes);
    const last = ok[ok.length - 1];
    v.dispatch({ changes: set, effects: ok.map((h) => dropHunk.of(h.id)), userEvent: "input.ai", scrollIntoView: true,
      selection: { anchor: set.mapPos(last.to, 1) } });
    const after = v.state.doc;
    for (const h of ok) { h.status = "applied"; handlers.delete(h.id); }
    this.applied = { before, after, ids: ok.map((h) => h.id), set };
    const first = ok[0]; flash(v, set.mapPos(first.from, -1), set.mapPos(last.to, 1));
    this.onChange(this);
  }
  /** Undo the last apply: CodeMirror history (the apply was a single transaction). */
  undo() {
    if (!this.applied) return false;
    if (this.target.kind === "doc") return this.undoDoc();
    this.target.activate?.();
    const v = this.view(); if (!v) return false;
    if (!v.state.doc.eq(this.applied.after)) { this.target.toast?.("ai_undo_changed"); return false; }
    CM.undo(v);
    if (!v.state.doc.eq(this.applied.before)) return false;
    const ids = this.applied.ids; this.applied = null;
    for (const h of this.hunks) if (ids.includes(h.id)) h.status = "pending";
    this.sync();
    return true;
  }
  // ---- rich documents (DocEditor, contenteditable): DOM ranges; Undo = snapshot of the page
  applyDoc(list) {
    const doc = this.target.doc?.(); if (!doc) return;
    this.target.activate?.();
    const snap = doc.page.innerHTML;
    for (const h of list.filter((x) => x.status === "pending")) {
      const ok = docApply(doc, h);
      h.status = ok ? "applied" : "failed"; if (!ok) h.error = "not_found";
    }
    if (list.some((h) => h.status === "applied")) { this.applied = { snapshot: snap, ids: list.filter((h) => h.status === "applied").map((h) => h.id) }; doc.opts.onChange?.(); }
    this.onChange(this);
  }
  undoDoc() {
    const doc = this.target.doc?.(); if (!doc || !this.applied) return false;
    doc.page.innerHTML = this.applied.snapshot; doc.opts.onChange?.();
    const ids = this.applied.ids; this.applied = null;
    for (const h of this.hunks) if (ids.includes(h.id)) h.status = "pending";
    this.onChange(this);
    return true;
  }
  dispose() {
    for (const h of this.hunks) handlers.delete(h.id);
    if (this.target.kind === "cm") { const v = this.target.view?.(); if (v && v.state.field(hunkField, false)) v.dispatch({ effects: setHunks.of((v.state.field(hunkField) || []).filter((x) => !this.hunks.some((h) => h.id === x.id))) }); }
  }
}

/** Live "write here": text streams into a widget at `pos`; commit() inserts it as one undoable change. */
export class StreamWriter {
  constructor(view, pos) { this.view = view; ensureAIFields(view); this.text = ""; view.dispatch({ effects: streamEff.of({ pos, text: "" }) }); }
  push(text) { this.text = text; const v = this.view; const cur = v.state.field(streamField, false); if (!cur) return; v.dispatch({ effects: streamEff.of({ pos: cur.pos, text }) }); }
  commit(text = this.text) {
    const v = this.view; const cur = v.state.field(streamField, false);
    if (!cur) return null;
    if (!text) { v.dispatch({ effects: streamEff.of(null) }); return null; }
    const before = v.state.doc;
    v.dispatch({ changes: { from: cur.pos, insert: text }, effects: streamEff.of(null), userEvent: "input.ai", selection: { anchor: cur.pos + text.length }, scrollIntoView: true });
    flash(v, cur.pos, cur.pos + text.length);
    return { from: cur.pos, to: cur.pos + text.length, before, after: v.state.doc };
  }
  cancel() { try { this.view.dispatch({ effects: streamEff.of(null) }); } catch { /* gone */ } }
}

// ------------------------------------------------------------------ rich document helpers
const looksMarkdown = (s) => /(^|\n)(#{1,6} |[-*] |\d+\. |\|.*\||> )|\*\*|__|`/.test(s);
const mdHtml = (text) => renderMarkdown(text).replace(/ data-line="\d+"/g, "");   // preview line mapping is not document content
export function toDocHtml(text) {
  if (looksMarkdown(text)) return mdHtml(text);
  return text.split(/\n{2,}/).map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
}
function inlineHtml(text) {
  if (/\n/.test(text) || /(^|\n)(#{1,6} |[-*] |\d+\. |\|)/.test(text)) return toDocHtml(text);
  const html = mdHtml(text).trim().replace(/^<p[^>]*>([\s\S]*)<\/p>$/, "$1");
  return html || esc(text);
}
/** Find `needle` in the page's text nodes (whitespace-tolerant) → Range. */
export function docFindRange(page, needle) {
  const nodes = []; let all = "";
  const walker = document.createTreeWalker(page, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) { nodes.push([n, all.length]); all += n.data; }
  const trimmed = String(needle || "").replace(MARKERS_RE, "").trim(); if (!trimmed) return null;
  const re = new RegExp(trimmed.split(/\s+/).map(escRe).join("\\s*"), "u");
  const m = re.exec(all); if (!m) return null;
  const at = (pos, end) => { for (let i = nodes.length - 1; i >= 0; i--) { const [n, off] = nodes[i]; if (off < pos || (off === pos && !end)) return [n, pos - off]; if (off === pos && end && i > 0) continue; } return [nodes[0][0], 0]; };
  const [sn, so] = at(m.index, false), [en, eo] = at(m.index + m[0].length, true);
  const r = document.createRange(); r.setStart(sn, Math.min(so, sn.data.length)); r.setEnd(en, Math.min(eo, en.data.length));
  return r;
}
function docApply(doc, h) {
  const page = doc.page; page.focus();
  const sel = window.getSelection();
  let range = null;
  if (h.op === "insert" && (h.at === "end" || h.at === "start")) {
    const html = toDocHtml(h.insert);
    page.insertAdjacentHTML(h.at === "end" ? "beforeend" : "afterbegin", html); return true;
  }
  if (h.op === "insert" && h.at === "cursor") { range = doc._range || (sel.rangeCount && page.contains(sel.anchorNode) ? sel.getRangeAt(0) : null); if (range) range = range.cloneRange(), range.collapse(false); }
  else if (h.op === "replace_selection" || h.op === "transform") range = doc._range ? doc._range.cloneRange() : null;
  else if (h.op === "insert") { const r = docFindRange(page, h.anchor); if (r) { range = r; range.collapse(h.at === "before"); } }
  else if (h.op === "rewrite_section") { range = docSectionRange(page, h.heading); }
  else if (h.op === "replace_all") { let n = 0, r; while (n < 2000 && (r = docFindRange(page, h.find))) { sel.removeAllRanges(); sel.addRange(r); document.execCommand(h.insert ? "insertText" : "delete", false, h.insert); n++; if (h.insert.includes(h.find)) break; } return n > 0; }
  else range = docFindRange(page, h.find || h.del);
  if (!range) return false;
  sel.removeAllRanges(); sel.addRange(range);
  if (!h.insert) document.execCommand("delete");
  else document.execCommand("insertHTML", false, h.op === "rewrite_section" ? toDocHtml(h.insert) : inlineHtml(h.insert));
  return true;
}
function docSectionRange(page, heading) {
  const want = String(heading || "").replace(/^#+\s*/, "").trim().toLowerCase();
  const hs = [...page.querySelectorAll("h1,h2,h3,h4,h5,h6")];
  const h = hs.find((x) => x.textContent.trim().toLowerCase() === want) || hs.find((x) => x.textContent.trim().toLowerCase().includes(want));
  if (!h) return null;
  const level = +h.tagName[1];
  const r = document.createRange(); r.setStartAfter(h);
  let end = null; for (let n = h.nextElementSibling; n; n = n.nextElementSibling) { if (/^H[1-6]$/.test(n.tagName) && +n.tagName[1] <= level) { end = n; break; } }
  if (end) r.setEndBefore(end); else r.setEndAfter(page.lastChild);
  return r;
}
/** Outline of a rich document (for the agent context). */
export function docOutline(page) {
  return [...page.querySelectorAll("h1,h2,h3,h4")].slice(0, 120).map((h, i) => ({ level: +h.tagName[1], title: h.textContent.trim().slice(0, 160), line: i + 1 })).filter((x) => x.title);
}
