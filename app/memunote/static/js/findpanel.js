

import * as CM from "../vendor/cm/cm.js";
import { esc, icon, t } from "./core.js";

const FAST_LIMIT = 4 * 1048576;
const CHUNK = 1 << 22;
const OVERLAP = 4096;
const COUNT_CAP = 100000;
const COUNT_BUDGET_MS = 600;

function unquote(s) { return s.replace(/\\([nrt\\])/g, (_, c) => (c === "n" ? "\n" : c === "r" ? "\r" : c === "t" ? "\t" : "\\")); }

export function queryRegExp(q) {
  if (!q || !q.search) return null;
  let src = q.regexp ? q.search : (q.literal ? q.search : unquote(q.search)).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (q.wholeWord) src = `(?<![\\p{L}\\p{N}_])(?:${src})(?![\\p{L}\\p{N}_])`;
  try { return new RegExp(src, "gmu" + (q.caseSensitive ? "" : "i")); } catch { try { return new RegExp(src, "gm" + (q.caseSensitive ? "" : "i")); } catch { return null; } }
}

export function scan(doc, re, from = 0, to = doc.length, limit = Infinity, budget = Infinity, onMatch = null) {
  const out = []; const started = performance.now(); let pos = from; let truncated = false; let found = 0;
  while (pos < to) {
    const end = Math.min(to, pos + CHUNK);
    const slice = doc.sliceString(pos, Math.min(doc.length, end + OVERLAP));
    re.lastIndex = 0; let m;
    while ((m = re.exec(slice))) {
      if (m.index >= end - pos) break;
      if (m[0].length === 0) { re.lastIndex++; if (m.index + pos >= to) break; continue; }
      const hit = { from: pos + m.index, to: pos + m.index + m[0].length, m };
      if (hit.to > to && to < doc.length) break;
      if (onMatch) onMatch(hit); else out.push(hit);
      if (++found >= limit) return { hits: out, truncated: true };
    }
    pos = end;
    if (performance.now() - started > budget) { truncated = pos < to; break; }
  }
  return { hits: out, truncated };
}

function fastNext(state, re, from) {
  const doc = state.doc;
  let r = scan(doc, re, from, doc.length, 1).hits[0];
  if (!r && from > 0) r = scan(doc, re, 0, Math.min(doc.length, from + OVERLAP), 1).hits[0];
  return r || null;
}
function fastPrev(state, re, before) {
  const doc = state.doc;
  const lastIn = (a, b) => { let last = null; scan(doc, re, a, b, Infinity, Infinity, (h) => { last = h; }); return last; };
  for (let end = before; end > 0; end -= CHUNK) {
    const hit = lastIn(Math.max(0, end - CHUNK), end); if (hit) return hit;
  }
  for (let end = doc.length; end > before; end -= CHUNK) {
    const hit = lastIn(Math.max(before, end - CHUNK), end); if (hit) return hit;
  }
  return null;
}
function expandReplace(q, m) {
  const rep = q.literal ? q.replace : unquote(q.replace);
  if (!q.regexp) return rep;
  return rep.replace(/\$(\$|&|\d{1,2})/g, (all, g) => (g === "$" ? "$" : g === "&" ? m[0] : (m[+g] ?? "")));
}

class FindPanel {
  constructor(view) {
    this.view = view;
    this.query = CM.getSearchQuery(view.state);
    this.replaceOpen = !!view.dom.dataset.mnReplace;
    this.dom = document.createElement("div");
    this.dom.className = "mn-find";
    this.dom.setAttribute("role", "search");
    this.dom.innerHTML = `
      <div class="mn-find-row">
        <button class="mn-find-x mn-find-toggle-rep" data-act="toggleReplace" title="${esc(t("find_toggle_replace"))}" aria-label="${esc(t("find_toggle_replace"))}">${icon("chevronRight")}</button>
        <div class="mn-find-field">
          <span class="mn-find-ic">${icon("search")}</span>
          <input class="mn-find-input" name="search" main-field="true" spellcheck="false" autocomplete="off" placeholder="${esc(t("find_placeholder"))}" aria-label="${esc(t("find"))}">
          <button class="mn-find-opt" data-opt="caseSensitive" title="${esc(t("find_case"))} (Alt+C)" aria-label="${esc(t("find_case"))}">Aa</button>
          <button class="mn-find-opt" data-opt="wholeWord" title="${esc(t("find_word"))} (Alt+W)" aria-label="${esc(t("find_word"))}"><u>ab</u></button>
          <button class="mn-find-opt" data-opt="regexp" title="${esc(t("find_regexp"))} (Alt+R)" aria-label="${esc(t("find_regexp"))}">.*</button>
        </div>
        <span class="mn-find-count" aria-live="polite"></span>
        <button class="mn-find-btn icon" data-act="prev" title="${esc(t("find_prev"))} (Shift+Enter)" aria-label="${esc(t("find_prev"))}">${icon("up")}</button>
        <button class="mn-find-btn icon" data-act="next" title="${esc(t("find_next"))} (Enter)" aria-label="${esc(t("find_next"))}">${icon("down")}</button>
        <button class="mn-find-btn" data-act="all" title="${esc(t("find_select_all_hint"))} (Alt+Enter)">${esc(t("find_select_all"))}</button>
        <span class="mn-find-spacer"></span>
        <button class="mn-find-x" data-act="close" title="${esc(t("close"))} (Esc)" aria-label="${esc(t("close"))}">${icon("close")}</button>
      </div>
      <div class="mn-find-row mn-find-rep">
        <span class="mn-find-x-gap"></span>
        <div class="mn-find-field">
          <span class="mn-find-ic">${icon("replace")}</span>
          <input class="mn-find-input" name="replace" spellcheck="false" autocomplete="off" placeholder="${esc(t("replace_placeholder"))}" aria-label="${esc(t("replace"))}">
        </div>
        <button class="mn-find-btn" data-act="replace" title="${esc(t("replace_one"))} (Ctrl+Shift+1)">${esc(t("replace_one"))}</button>
        <button class="mn-find-btn primary" data-act="replaceAll" title="${esc(t("replace_all"))} (Ctrl+Alt+Enter)">${esc(t("replace_all"))}</button>
      </div>`;
    this.search = this.dom.querySelector("[name=search]");
    this.replace = this.dom.querySelector("[name=replace]");
    this.count = this.dom.querySelector(".mn-find-count");
    this.search.value = this.query.search; this.replace.value = this.query.replace;
    this.dom.addEventListener("input", () => this.commit());
    this.dom.addEventListener("keydown", (e) => this.keydown(e));
    this.dom.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.dataset.opt) { this.toggle(b.dataset.opt); return; }
      this.act(b.dataset.act);
    });
    this.syncUI();
  }
  get large() { return this.view.state.doc.length > FAST_LIMIT; }
  mount() { this.search.select(); this.refreshCount(); }
  update(u) {
    for (const tr of u.transactions) for (const e of tr.effects) {
      if (e.is(CM.setSearchQuery) && !e.value.eq(this.query)) { this.query = e.value; this.search.value = this.query.search; this.replace.value = this.query.replace; this.syncUI(); }
    }
    if (u.docChanged || u.selectionSet) this.refreshCountSoon();
  }
  get top() { return true; }
  destroy() { clearTimeout(this.countTimer); }

  syncUI() {
    for (const b of this.dom.querySelectorAll("[data-opt]")) { const on = !!this.query[b.dataset.opt]; b.classList.toggle("on", on); b.setAttribute("aria-pressed", String(on)); }
    this.dom.classList.toggle("with-replace", this.replaceOpen);
    this.dom.querySelector(".mn-find-toggle-rep").classList.toggle("open", this.replaceOpen);
    this.dom.querySelectorAll(".mn-find-rep button, .mn-find-rep input").forEach((el) => (el.tabIndex = this.replaceOpen ? 0 : -1));
    this.dom.querySelectorAll(".mn-find-rep button").forEach((b) => (b.disabled = this.view.state.readOnly));
  }
  commit(opts = {}) {
    const q = new CM.SearchQuery({ search: this.search.value, replace: this.replace.value, caseSensitive: !!(opts.caseSensitive ?? this.query.caseSensitive),
      regexp: !!(opts.regexp ?? this.query.regexp), wholeWord: !!(opts.wholeWord ?? this.query.wholeWord), literal: !!this.query.literal });
    if (!q.eq(this.query)) { this.query = q; this.view.dispatch({ effects: CM.setSearchQuery.of(q) }); }
    this.syncUI(); this.refreshCountSoon();
  }
  toggle(opt) { this.commit({ [opt]: !this.query[opt] }); this.search.focus(); }
  keydown(e) {
    const k = e.key;
    if (k === "Escape") { e.preventDefault(); this.act("close"); return; }
    if (e.altKey && !e.ctrlKey && /^[cwr]$/i.test(k)) { e.preventDefault(); this.toggle({ c: "caseSensitive", w: "wholeWord", r: "regexp" }[k.toLowerCase()]); return; }
    if (k === "Enter" && (e.ctrlKey || e.metaKey) && e.altKey) { e.preventDefault(); this.act("replaceAll"); return; }
    if (k === "Enter" && e.altKey) { e.preventDefault(); this.act("all"); return; }
    if (k === "Enter" && e.target === this.replace && !e.shiftKey) { e.preventDefault(); this.act("replace"); return; }
    if (k === "Enter") { e.preventDefault(); this.act(e.shiftKey ? "prev" : "next"); return; }
    if (k === "F3") { e.preventDefault(); this.act(e.shiftKey ? "prev" : "next"); return; }
    if ((e.ctrlKey || e.metaKey) && k.toLowerCase() === "h") { e.preventDefault(); this.replaceOpen = true; this.syncUI(); this.replace.focus(); this.replace.select(); }
  }
  act(a) {
    const v = this.view;
    if (a === "close") { CM.closeSearchPanel(v); v.focus(); return; }
    if (a === "toggleReplace") { this.replaceOpen = !this.replaceOpen; this.syncUI(); (this.replaceOpen ? this.replace : this.search).focus(); return; }
    if (!this.query.search) { this.search.focus(); return; }
    if (!this.query.valid) { this.flash(); return; }
    if (a === "next" || a === "prev") {
      const ok = this.large ? this.fastMove(a === "next") : (a === "next" ? CM.findNext(v) : CM.findPrevious(v));
      if (!ok) this.flash();
    } else if (a === "all") {
      if (this.large) this.fastSelectAll(); else CM.selectMatches(v);
      v.focus();
    } else if (a === "replace") {
      if (v.state.readOnly) return;
      if (this.large) this.fastReplaceNext(); else CM.replaceNext(v);
    } else if (a === "replaceAll") {
      if (v.state.readOnly) return;
      const n = this.large ? this.fastReplaceAll() : this.replaceAllCount();
      this.count.textContent = n ? t("find_replaced", { n: n.toLocaleString() }) : t("find_none");
      this.count.className = "mn-find-count " + (n ? "ok" : "none");
      clearTimeout(this.countTimer); this.countTimer = setTimeout(() => this.refreshCount(), 2500);
    }
  }
  flash() { this.dom.classList.remove("shake"); void this.dom.offsetWidth; this.dom.classList.add("shake"); }
  replaceAllCount() {
    let n = 0; const cur = this.query.getCursor(this.view.state); while (!cur.next().done) n++;
    if (n) CM.replaceAll(this.view);
    return n;
  }
  select(hit) {
    this.view.dispatch({ selection: { anchor: hit.from, head: hit.to }, effects: CM.EditorView.scrollIntoView(hit.from, { y: "center" }), userEvent: "select.search" });
  }
  fastMove(forward) {
    const re = queryRegExp(this.query); if (!re) return false;
    const sel = this.view.state.selection.main;
    const hit = forward ? fastNext(this.view.state, re, sel.to === sel.from ? sel.to : sel.to) : fastPrev(this.view.state, re, sel.from);
    if (!hit) return false;
    this.select(hit); return true;
  }
  fastSelectAll() {
    const re = queryRegExp(this.query); if (!re) return;
    const { hits } = scan(this.view.state.doc, re, 0, this.view.state.doc.length, 10000);
    if (!hits.length) { this.flash(); return; }
    this.view.dispatch({ selection: CM.EditorSelection.create(hits.map((h) => CM.EditorSelection.range(h.from, h.to))), userEvent: "select.search.matches" });
  }
  fastReplaceNext() {
    const re = queryRegExp(this.query); if (!re) return;
    const st = this.view.state, sel = st.selection.main;
    re.lastIndex = 0;
    const cur = !sel.empty ? scan(st.doc, re, sel.from, sel.to, 1).hits[0] : null;
    if (cur && cur.from === sel.from && cur.to === sel.to) {
      const insert = expandReplace(this.query, cur.m);
      this.view.dispatch({ changes: { from: cur.from, to: cur.to, insert }, selection: { anchor: cur.from + insert.length }, userEvent: "input.replace" });
    }
    this.fastMove(true);
  }
  fastReplaceAll() {
    const re = queryRegExp(this.query); if (!re) return 0;
    const changes = [];
    scan(this.view.state.doc, re, 0, this.view.state.doc.length, Infinity, Infinity, (h) => changes.push({ from: h.from, to: h.to, insert: expandReplace(this.query, h.m) }));
    if (changes.length) this.view.dispatch({ changes, userEvent: "input.replace.all" });
    return changes.length;
  }
  refreshCountSoon() { clearTimeout(this.countTimer); this.countTimer = setTimeout(() => this.refreshCount(), this.large ? 350 : 60); }
  refreshCount() {
    const el = this.count; el.className = "mn-find-count";
    if (!this.query.search) { el.textContent = ""; return; }
    if (!this.query.valid) { el.textContent = t("find_bad_regexp"); el.classList.add("none"); return; }
    const st = this.view.state, sel = st.selection.main;
    let total = 0, index = 0, more = false;
    if (this.large) {
      const re = queryRegExp(this.query); if (!re) { el.textContent = t("find_bad_regexp"); el.classList.add("none"); return; }
      const r = scan(st.doc, re, 0, st.doc.length, COUNT_CAP, COUNT_BUDGET_MS, (h) => { total++; if (h.from === sel.from && h.to === sel.to) index = total; });
      more = r.truncated;
    } else {
      const cur = this.query.getCursor(st); const started = performance.now();
      for (let r = cur.next(); !r.done; r = cur.next()) {
        total++; if (r.value.from === sel.from && r.value.to === sel.to) index = total;
        if (total >= COUNT_CAP || (total % 1000 === 0 && performance.now() - started > COUNT_BUDGET_MS)) { more = true; break; }
      }
    }
    if (!total) { el.textContent = t("find_none"); el.classList.add("none"); return; }
    const n = total.toLocaleString() + (more ? "+" : "");
    el.textContent = index ? t("find_n_of", { i: index.toLocaleString(), n }) : t("find_n", { n });
  }
}

export function findExtension() {
  return CM.search({ top: true, createPanel: (view) => new FindPanel(view) });
}

export const findKeymap = [
  { key: "F3", run: (v) => step(v, true), shift: (v) => step(v, false), preventDefault: true },
  { key: "Mod-g", run: (v) => step(v, true), shift: (v) => step(v, false), preventDefault: true },
];
function step(view, forward) {
  if (view.state.doc.length <= FAST_LIMIT) return forward ? CM.findNext(view) : CM.findPrevious(view);
  const q = CM.getSearchQuery(view.state); const re = q.valid && queryRegExp(q);
  if (!re) { CM.openSearchPanel(view); return true; }
  const sel = view.state.selection.main;
  const hit = forward ? fastNext(view.state, re, sel.to) : fastPrev(view.state, re, sel.from);
  if (hit) view.dispatch({ selection: { anchor: hit.from, head: hit.to }, effects: CM.EditorView.scrollIntoView(hit.from, { y: "center" }), userEvent: "select.search" });
  return true;
}
