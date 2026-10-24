// MemuNote (Round 33, item #9) — built-in spell checking (Russian + English) for the text editor.
//
// The page finds the words of the VISIBLE text, asks the backend (/api/spell/check: Hunspell dictionaries shipped
// with MemuNote, app/memunote/spell.py) about the ones it has not seen yet, and underlines the unknown ones with a
// red wavy line. Code is ignored: in code files only comments are checked; in Markdown fenced/inline code, links,
// URLs, HTML and front matter are skipped; everywhere URLs, e-mails, paths, CamelCase, snake_case, words with
// digits and ALL-CAPS abbreviations are skipped. The word being typed is not flagged until the caret leaves it.
// Right-click a flagged word → suggestions, "Add to dictionary", "Ignore in this document" (app.js editorMenu).
import { CM } from "./editor.js";
import { api } from "./core.js";

const { Decoration, ViewPlugin, StateEffect, StateField, EditorView, syntaxTree } = CM;

const WORD_RE = /[\p{L}\p{M}]+(?:['’\-][\p{L}\p{M}]+)*/gu;
const SKIP_SPAN_RE = /\b(?:https?:\/\/|www\.|ftp:\/\/|file:\/\/)\S+|[\w.+-]+@[\w-]+\.[\w.-]+|(?:[A-Za-z]:)?[\\/][\w .\\/-]*[\\/][\w.-]*|`[^`\n]*`/g;
const status = new Map();      // word → true (bad) | false (ok); shared by every editor
const pending = new Set();
let flushTimer = 0;
const listeners = new Set();

export const setIgnored = StateEffect.define();
export const ignoreField = StateField.define({
  create: () => new Set(),
  update(v, tr) { for (const e of tr.effects) if (e.is(setIgnored)) return new Set(e.value); return v; },
});
const refresh = StateEffect.define();

export function wordSkipped(w) {
  if (w.length < 2 || w.length > 48) return true;
  if (/\d|_/.test(w)) return true;
  if (/^[\p{Lu}\p{M}'’\-]+$/u.test(w) && w.length <= 6) return true;      // NASA, ГОСТ, RU-EN
  if (/\p{Ll}\p{Lu}/u.test(w)) return true;                                // camelCase / iPhone
  if (/[\u0400-\u04FF]/.test(w) && /[A-Za-z]/.test(w)) return true;        // mixed scripts: not a word
  return false;
}

/** Words of `text` (absolute offset `offset`) that should be spell-checked: [{from, to, word}]. */
export function tokenize(text, offset = 0) {
  const skips = [];
  SKIP_SPAN_RE.lastIndex = 0;
  for (let m; (m = SKIP_SPAN_RE.exec(text));) skips.push([m.index, m.index + m[0].length]);
  const out = [];
  let si = 0;
  WORD_RE.lastIndex = 0;
  for (let m; (m = WORD_RE.exec(text));) {
    const s = m.index, e = s + m[0].length;
    while (si < skips.length && skips[si][1] <= s) si++;
    if (si < skips.length && skips[si][0] < e && skips[si][1] > s) continue;
    // a "word" glued to digits/underscore/dot-identifiers (file.txt, x2, foo_bar) is not prose
    const before = text[s - 1] || "", after = text[e] || "";
    if (/[\w@#$\\/]/.test(before) && !/[\p{L}]/u.test(before) || /[\d_$@]/.test(after)) continue;
    if (after === "." && /[\p{L}]/u.test(text[e + 1] || "")) continue;
    let w = m[0].replace(/^['’\-]+|['’\-]+$/g, "");
    if (wordSkipped(w)) continue;
    out.push({ from: offset + s, to: offset + s + w.length, word: w });
  }
  return out;
}

// ------------------------------------------------------------------ which parts of the document are prose
const MD_SKIP = new Set(["FencedCode", "CodeBlock", "InlineCode", "URL", "LinkMark", "HTMLBlock", "HTMLTag", "CommentBlock", "Comment",
  "ProcessingInstructionBlock", "LinkReference", "Autolink", "CodeText", "CodeInfo", "Escape", "Entity"]);
function proseRanges(state, from, to, mode) {
  if (mode === "text") return [[from, to]];
  const tree = syntaxTree(state);
  if (mode === "markdown") {
    const cuts = [];
    tree.iterate({ from, to, enter: (n) => { if (MD_SKIP.has(n.name)) { cuts.push([n.from, n.to]); return false; } if (n.name === "Image") { cuts.push([n.from, n.to]); return false; } } });
    // YAML front matter (--- … --- at the very top)
    const head = state.sliceDoc(0, Math.min(state.doc.length, 4000));
    const fm = /^---\r?\n[\s\S]*?\r?\n(---|\.\.\.)\s*(\r?\n|$)/.exec(head);
    if (fm) cuts.push([0, fm[0].length]);
    // link targets [text](url): the url part
    const seg = state.sliceDoc(from, to);
    for (const m of seg.matchAll(/\]\(([^)\n]*)\)/g)) cuts.push([from + m.index + 1, from + m.index + m[0].length]);
    return subtract([[from, to]], cuts);
  }
  // code: comments only (string literals often hold identifiers, SQL, paths…)
  const keep = [];
  tree.iterate({ from, to, enter: (n) => { if (/comment/i.test(n.name)) { keep.push([Math.max(from, n.from), Math.min(to, n.to)]); return false; } } });
  return keep;
}
function subtract(ranges, cuts) {
  cuts.sort((a, b) => a[0] - b[0]);
  let out = ranges;
  for (const [cf, ct] of cuts) {
    const next = [];
    for (const [f, t] of out) {
      if (ct <= f || cf >= t) { next.push([f, t]); continue; }
      if (cf > f) next.push([f, cf]);
      if (ct < t) next.push([ct, t]);
    }
    out = next;
  }
  return out;
}

// ------------------------------------------------------------------ backend batching
function queue(words) {
  let added = false;
  for (const w of words) if (!status.has(w) && !pending.has(w)) { pending.add(w); added = true; }
  if (added && !flushTimer) flushTimer = setTimeout(flush, 120);
}
async function flush() {
  flushTimer = 0;
  const batch = [...pending].slice(0, 3000);
  if (!batch.length) return;
  try {
    const r = await api("spell/check", { words: batch });
    if (!r.ready) { setTimeout(() => { for (const w of batch) pending.delete(w); queue(batch); }, 700); return; }
    const bad = new Set(r.bad || []);
    for (const w of batch) { pending.delete(w); status.set(w, bad.has(w)); }
  } catch { for (const w of batch) pending.delete(w); return; }
  if (pending.size) flushTimer = setTimeout(flush, 30);
  for (const fn of listeners) fn();
}
export function markKnown(word) { status.set(word, false); for (const fn of listeners) fn(); }
export function isBad(word) { return status.get(word) === true; }

// ------------------------------------------------------------------ the CodeMirror extension
const errMark = Decoration.mark({ class: "mn-spell-err" });

/** mode: "text" | "markdown" | "code" */
export function spellExtension(mode) {
  const plugin = ViewPlugin.fromClass(class {
    constructor(view) {
      this.view = view; this.decorations = Decoration.none; this.timer = 0; this.typingWord = null;
      this.onStatus = () => { if (!this.dead) this.schedule(30); };
      listeners.add(this.onStatus);
      this.schedule(60);
    }
    update(u) {
      if (u.docChanged) {
        this.decorations = this.decorations.map(u.changes);
        const typed = u.transactions.some((tr) => tr.isUserEvent("input.type") || tr.isUserEvent("delete"));
        this.typingWord = typed ? u.state.wordAt(u.state.selection.main.head) || { from: u.state.selection.main.head, to: u.state.selection.main.head } : null;
      } else if (u.selectionSet && this.typingWord) {
        const h = u.state.selection.main.head;
        if (h < this.typingWord.from || h > this.typingWord.to) this.typingWord = null;
      }
      if (u.docChanged || u.viewportChanged || u.selectionSet || u.transactions.some((tr) => tr.effects.some((e) => e.is(setIgnored)))) this.schedule(u.docChanged ? 260 : 80);
    }
    schedule(ms) { clearTimeout(this.timer); this.timer = setTimeout(() => this.compute(), ms); }
    compute() {
      const view = this.view; if (this.dead || !view.dom.isConnected) return;
      const { state } = view;
      const ignored = state.field(ignoreField, false) || new Set();
      const words = [], marks = [];
      for (const { from, to } of view.visibleRanges) {
        for (const [f, t] of proseRanges(state, from, to, mode)) {
          if (t - f > 400000) continue;
          for (const w of tokenize(state.sliceDoc(f, t), f)) {
            words.push(w.word);
            if (status.get(w.word) === true && !ignored.has(w.word)) {
              if (this.typingWord && w.from <= this.typingWord.to && w.to >= this.typingWord.from) continue;
              marks.push(errMark.range(w.from, w.to));
            }
          }
        }
      }
      queue(words);
      this.decorations = Decoration.set(marks, true);
      view.dispatch({ effects: refresh.of(null) });
    }
    destroy() { this.dead = true; clearTimeout(this.timer); listeners.delete(this.onStatus); }
  }, { decorations: (v) => v.decorations });
  return [ignoreField, plugin];
}

/** The flagged word at document position `pos` (or null): {from, to, word}. */
export function spellWordAt(view, pos) {
  const line = view.state.doc.lineAt(pos);
  for (const w of tokenize(line.text, line.from)) {
    if (pos >= w.from && pos <= w.to) return isBad(w.word) && !(view.state.field(ignoreField, false)?.has(w.word)) ? w : null;
  }
  return null;
}
export async function suggestions(word) {
  try { return (await api("spell/suggest", { word })).suggestions || []; } catch { return []; }
}
export async function addToDictionary(word) { await api("spell/add", { word }); markKnown(word); }
export function ignoreInDoc(view, word) {
  const cur = view.state.field(ignoreField, false); if (!cur) return;
  view.dispatch({ effects: setIgnored.of([...cur, word]) });
}
export const _test = { tokenize, wordSkipped, subtract };
/** Start loading the dictionaries in the background (the first load takes a second or two). */
export function warmUp() { api("spell/status").catch(() => {}); }
