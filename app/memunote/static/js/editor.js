// MemuNote — CodeMirror 6 integration: extensions from settings, languages, Markdown commands,
// text tools, AI ghost-text (inline autocomplete) and split-view synchronisation.
import * as CM from "../vendor/cm/cm.js";
import { t } from "./core.js";
import { findExtension, findKeymap } from "./findpanel.js";

export { CM };
const { EditorView, EditorState, Compartment, StateEffect, StateField, Decoration, WidgetType, keymap, Prec } = CM;

export const configComp = new Compartment();
export const langComp = new Compartment();
export const readOnlyComp = new Compartment();

// ------------------------------------------------------------------ theme (MemuAI tokens via CSS variables)
const baseTheme = EditorView.theme({
  "&": { height: "100%", color: "var(--text)", backgroundColor: "var(--ed-bg)" },
  ".cm-scroller": { fontFamily: "var(--ed-font)", fontSize: "var(--ed-size)", lineHeight: "var(--ed-lh)" },
  ".cm-content": { caretColor: "var(--accent)", padding: "10px 0 40px" },
  ".cm-line": { padding: "0 14px 0 10px" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": { backgroundColor: "var(--sel) !important" },
  ".cm-activeLine": { backgroundColor: "var(--active-line)" },
  ".cm-gutters": { backgroundColor: "var(--ed-bg)", color: "var(--gutter)", border: "none", paddingLeft: "6px" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--text)" },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 10px 0 6px", minWidth: "34px" },
  ".cm-foldGutter .cm-gutterElement": { color: "var(--gutter)", cursor: "pointer", padding: "0 4px" },
  ".cm-foldPlaceholder": { background: "var(--accent-soft)", border: "none", color: "var(--accent)", borderRadius: "4px", padding: "0 6px" },
  ".cm-matchingBracket, &.cm-focused .cm-matchingBracket": { backgroundColor: "var(--accent-soft)", outline: "1px solid color-mix(in srgb, var(--accent) 50%, transparent)", borderRadius: "2px" },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--accent) 16%, transparent)", borderRadius: "2px" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, #f5c84c 34%, transparent)", outline: "1px solid color-mix(in srgb, #f5c84c 60%, transparent)", borderRadius: "2px" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "color-mix(in srgb, var(--accent) 45%, transparent)" },
  ".cm-panels": { backgroundColor: "var(--panel)", color: "var(--text)", borderColor: "var(--border)" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--border)" },
  ".cm-tooltip": { backgroundColor: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: "8px", boxShadow: "var(--shadow-sm)" },
  ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--accent-soft)", color: "var(--text)" },
  ".cm-highlightSpace": { backgroundImage: "radial-gradient(circle at 50% 55%, var(--gutter) 11%, transparent 5%)" },
  ".cm-highlightTab": { backgroundImage: "none", position: "relative" },
  ".cm-trailingSpace": { backgroundColor: "color-mix(in srgb, var(--danger) 18%, transparent)" },
  ".mn-ghost": { color: "var(--muted)", opacity: ".72", fontStyle: "italic", pointerEvents: "none" },
  ".mn-ghost-hint": { marginLeft: "8px", fontSize: "11px", fontStyle: "normal", padding: "0 5px", borderRadius: "4px", border: "1px solid var(--border)", color: "var(--muted)" },
});

const tok = (v) => `var(--tok-${v})`;
const T = () => CM.tags;
function highlightStyle() {
  const tags = T();
  return CM.HighlightStyle.define([
    { tag: [tags.keyword, tags.modifier, tags.controlKeyword, tags.operatorKeyword, tags.definitionKeyword], color: tok("kw") },
    { tag: [tags.typeName, tags.className, tags.namespace, tags.standard(tags.typeName)], color: tok("type") },
    { tag: [tags.string, tags.special(tags.string), tags.regexp, tags.character, tags.inserted], color: tok("str") },
    { tag: [tags.comment, tags.lineComment, tags.blockComment, tags.docComment], color: tok("com"), fontStyle: "italic" },
    { tag: [tags.number, tags.integer, tags.float], color: tok("num") },
    { tag: [tags.function(tags.variableName), tags.function(tags.propertyName), tags.macroName], color: tok("fn") },
    { tag: [tags.operator, tags.derefOperator, tags.arithmeticOperator, tags.logicOperator, tags.compareOperator], color: tok("op") },
    { tag: [tags.bool, tags.null, tags.atom, tags.literal, tags.self, tags.constant(tags.variableName)], color: tok("lit") },
    { tag: [tags.tagName, tags.angleBracket], color: tok("tag") },
    { tag: [tags.attributeName], color: tok("attr") },
    { tag: [tags.propertyName], color: tok("prop") },
    { tag: [tags.variableName, tags.labelName], color: tok("var") },
    { tag: [tags.meta, tags.annotation, tags.processingInstruction], color: tok("meta") },
    { tag: [tags.punctuation, tags.separator, tags.bracket], color: tok("punct") },
    { tag: tags.deleted, color: "var(--danger)" },
    { tag: tags.invalid, color: "var(--danger)", textDecoration: "underline wavy" },
    { tag: tags.link, color: "var(--accent)", textDecoration: "underline" },
    { tag: tags.url, color: "var(--accent)" },
    { tag: tags.heading1, fontWeight: "750", fontSize: "1.32em", color: "var(--text)" },
    { tag: tags.heading2, fontWeight: "720", fontSize: "1.2em", color: "var(--text)" },
    { tag: tags.heading3, fontWeight: "700", fontSize: "1.1em", color: "var(--text)" },
    { tag: [tags.heading4, tags.heading5, tags.heading6], fontWeight: "700", color: "var(--text)" },
    { tag: tags.strong, fontWeight: "700" },
    { tag: tags.emphasis, fontStyle: "italic" },
    { tag: tags.strikethrough, textDecoration: "line-through" },
    { tag: tags.quote, color: "var(--muted)", fontStyle: "italic" },
    { tag: tags.monospace, color: tok("str"), fontFamily: "var(--mono)" },
    { tag: tags.processingInstruction, color: "var(--muted)" },
    { tag: tags.contentSeparator, color: "var(--muted)" },
  ]);
}
let _hl = null;
const hl = () => (_hl ||= CM.syntaxHighlighting(highlightStyle(), { fallback: true }));

export function phrases() {
  const keys = ["Find", "Replace", "next", "previous", "all", "match case", "regexp", "by word", "replace", "replace all",
    "close", "current match", "replaced $ matches", "replaced match on line $", "on line", "Go to line", "go",
    "Folded lines", "Unfolded lines", "to", "folded code", "unfold", "Fold line", "Unfold line", "Control character"];
  const out = {};
  for (const k of keys) { const v = t("cm:" + k); if (v !== "cm:" + k) out[k] = v; }
  return EditorState.phrases.of(out);
}

// ------------------------------------------------------------------ AI ghost text (inline autocomplete)
export const setGhost = StateEffect.define();
class GhostWidget extends WidgetType {
  constructor(text) { super(); this.text = text; }
  eq(o) { return o.text === this.text; }
  toDOM() {
    const s = document.createElement("span"); s.className = "mn-ghost"; s.textContent = this.text;
    const h = document.createElement("span"); h.className = "mn-ghost-hint"; h.textContent = "Tab"; s.append(h);
    return s;
  }
  ignoreEvent() { return true; }
}
export const ghostField = StateField.define({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(setGhost)) return e.value;
    if (value && (tr.docChanged || tr.selection)) return null;
    return value;
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v ? Decoration.set([Decoration.widget({ widget: new GhostWidget(v.text), side: 1 }).range(v.pos)]) : Decoration.none),
});
const ghostKeys = Prec.highest(keymap.of([
  { key: "Tab", run: (view) => { const g = view.state.field(ghostField, false); if (!g) return false;
    view.dispatch({ changes: { from: g.pos, insert: g.text }, selection: { anchor: g.pos + g.text.length }, effects: setGhost.of(null), userEvent: "input.complete" }); return true; } },
  { key: "Escape", run: (view) => { if (!view.state.field(ghostField, false)) return false; view.dispatch({ effects: setGhost.of(null) }); return true; } },
]));
export function ghostTrigger(onIdle) {
  return CM.ViewPlugin.fromClass(class {
    constructor(view) { this.view = view; this.timer = 0; }
    update(u) {
      if (!u.docChanged) return;
      clearTimeout(this.timer);
      const typed = u.transactions.some((tr) => tr.isUserEvent("input.type"));
      if (!typed) return;
      this.timer = setTimeout(() => onIdle(this.view), 1);
    }
    destroy() { clearTimeout(this.timer); }
  });
}

// ------------------------------------------------------------------ extensions
/** opts: {settings, large, kind, readOnly, onUpdate(update), ghost: fn|null, keys: keymap entries} */
export function buildConfig(opts) {
  const s = opts.settings;
  const large = !!opts.large;
  const ext = [phrases()];
  // Its MatchDecorator re-scans the rest of a changed line with a RegExp on every keystroke: 135 ms per key
  // in a 20 M-char line. Lines that long get no control-character markers (Round 31).
  if (!opts.noWrap) ext.push(CM.highlightSpecialChars());
  if (s.line_numbers) ext.push(CM.lineNumbers());
  if (s.fold_gutter && !large) ext.push(CM.foldGutter({ markerDOM: (open) => { const e = document.createElement("span"); e.className = "mn-fold " + (open ? "open" : ""); e.textContent = open ? "⌄" : "›"; return e; } }));
  if (s.highlight_active_line) ext.push(CM.highlightActiveLine(), CM.highlightActiveLineGutter());
  // Round 31: never wrap a line of 100k+ chars (a 20 M-char single line took 22 s to open wrapped, 1 s unwrapped:
  // CodeMirror renders only the visible part of an unwrapped long line).
  if (s.word_wrap && !opts.noWrap && !(large && opts.size > 64 * 1048576)) ext.push(EditorView.lineWrapping);
  if (s.bracket_matching && !large) ext.push(CM.bracketMatching());
  if (s.auto_close_brackets && !large && opts.kind !== "text") ext.push(CM.closeBrackets());
  if (s.highlight_selection_matches && !large) ext.push(CM.highlightSelectionMatches());
  if (s.show_whitespace) ext.push(CM.highlightWhitespace(), CM.highlightTrailingWhitespace());
  if (s.scroll_past_end) ext.push(CM.scrollPastEnd());
  if (!large) ext.push(hl());
  ext.push(EditorState.tabSize.of(s.tab_size || 4), CM.indentUnit.of(s.indent_with_tabs ? "\t" : " ".repeat(s.tab_size || 4)));
  // Round 33: the built-in checker (spell.js, via opts.extraConfig) replaces the browser's native one — its
  // suggestions were unreachable behind MemuNote's own context menu, and two underlines would overlap.
  ext.push(EditorView.contentAttributes.of({ spellcheck: "false", autocorrect: "off", autocapitalize: "off" }));
  if (s.minimap && !large) {
    ext.push(CM.showMinimap.compute(["doc"], () => ({ create: () => { const dom = document.createElement("div"); return { dom }; }, displayText: "blocks", showOverlay: "mouse-over" })));
  }
  if (opts.ghost && !large) ext.push(ghostTrigger(opts.ghost));
  if (opts.extraConfig) ext.push(opts.extraConfig(opts));   // Round 33: live Markdown, spell checking, code completion (app.js r33Extensions)
  return ext;
}

export function createState(doc, opts) {
  const ext = [
    CM.history(), CM.drawSelection(), CM.dropCursor(), CM.rectangularSelection(), CM.crosshairCursor(),
    CM.indentOnInput(), EditorState.allowMultipleSelections.of(true),
    findExtension(),
    ghostField, ghostKeys,
    keymap.of([...(opts.keys || []), ...CM.closeBracketsKeymap, ...CM.defaultKeymap, ...findKeymap, ...CM.searchKeymap, ...CM.historyKeymap,
      ...CM.foldKeymap, ...CM.completionKeymap, CM.indentWithTab]),
    baseTheme,
    configComp.of(buildConfig(opts)),
    langComp.of([]),
    readOnlyComp.of(opts.readOnly ? [EditorState.readOnly.of(true)] : []),
    EditorView.updateListener.of((u) => opts.onUpdate?.(u)),
    EditorView.domEventHandlers(opts.domEvents || {}),
  ];
  if (opts.extra) ext.push(opts.extra);
  return EditorState.create({ doc, extensions: ext, selection: opts.selection });
}

// ------------------------------------------------------------------ languages
export const LANGS = () => CM.languages;
export function languageFor(filename, kind) {
  if (kind === "markdown") return { name: "Markdown", markdown: true };
  if (kind === "log") return null;
  const d = CM.LanguageDescription.matchFilename(CM.languages, filename || "");
  return d ? { name: d.name, desc: d } : null;
}
export async function loadLanguage(info) {
  if (!info) return [];
  if (info.markdown) return CM.markdown({ base: CM.markdownLanguage, codeLanguages: CM.languages, addKeymap: true });
  try { return await info.desc.load(); } catch (e) { console.warn("language load failed", e); return []; }
}
export function languageByName(name) {
  if (!name || name === "Plain text") return null;
  if (name === "Markdown") return { name, markdown: true };
  const d = CM.languages.find((l) => l.name === name);
  return d ? { name: d.name, desc: d } : null;
}

// ------------------------------------------------------------------ Markdown commands
function selRange(view) { return view.state.selection.main; }
export function mdWrap(view, mark, endMark = mark, placeholder = "") {
  const { state } = view;
  const changes = state.changeByRange((r) => {
    const text = state.sliceDoc(r.from, r.to);
    const before = state.sliceDoc(r.from - mark.length, r.from), after = state.sliceDoc(r.to, r.to + endMark.length);
    if (before === mark && after === endMark) {   // toggle off
      return { changes: [{ from: r.from - mark.length, to: r.from }, { from: r.to, to: r.to + endMark.length }],
        range: CM.EditorSelection.range(r.from - mark.length, r.to - mark.length) };
    }
    const lines = text.includes("\n") ? text.split("\n").filter((l) => l.trim()) : null;
    const marked = (l) => { const c = l.trim(); return c.length >= mark.length + endMark.length && c.startsWith(mark) && c.endsWith(endMark); };
    if (lines?.length > 1 && lines.every(marked)) {   // Round 34: undo the line-by-line wrap below
      const insert = text.split("\n").map((l) => (l.trim() ? l.replace(/^(\s*)(.*?)(\s*)$/, (m, a, c, z) => a + c.slice(mark.length, c.length - endMark.length) + z) : l)).join("\n");
      return { changes: { from: r.from, to: r.to, insert }, range: CM.EditorSelection.range(r.from, r.from + insert.length) };
    }
    if (text.startsWith(mark) && text.endsWith(endMark) && text.length >= mark.length + endMark.length) {
      return { changes: { from: r.from, to: r.to, insert: text.slice(mark.length, text.length - endMark.length) },
        range: CM.EditorSelection.range(r.from, r.to - mark.length - endMark.length) };
    }
    // Round 34: marks hug the text — Ctrl+A → Ctrl+B gave "**hello\n**" (the newline inside, not bold at all), and a
    // selection over several lines is wrapped line by line (inline Markdown cannot span lines/paragraphs).
    if (text.trim()) {
      const lead = text.length - text.trimStart().length, core = text.trim();
      const from = r.from + lead, to = from + core.length;
      const insert = core.includes("\n")
        ? core.split("\n").map((line) => line.replace(/^(\s*)(.*?)(\s*)$/, (m, a, b, c) => (b ? a + mark + b + endMark + c : m))).join("\n")
        : mark + core + endMark;
      return core.includes("\n") ? { changes: { from, to, insert }, range: CM.EditorSelection.range(from, from + insert.length) }
        : { changes: { from, to, insert }, range: CM.EditorSelection.range(from + mark.length, from + mark.length + core.length) };
    }
    const body = text || placeholder;
    return { changes: { from: r.from, to: r.to, insert: mark + body + endMark },
      range: CM.EditorSelection.range(r.from + mark.length, r.from + mark.length + body.length) };
  });
  view.dispatch(state.update(changes, { scrollIntoView: true, userEvent: "input.format" })); view.focus();
}
export function mdLinePrefix(view, prefix, opts = {}) {
  const { state } = view;
  const lines = new Map();
  for (const r of state.selection.ranges) {
    for (let l = state.doc.lineAt(r.from).number; l <= state.doc.lineAt(r.to).number; l++) lines.set(l, state.doc.line(l));
  }
  const all = [...lines.values()];
  const re = opts.re || new RegExp("^\\s*" + prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const allHave = all.every((l) => re.test(l.text));
  const changes = [];
  let n = 1;
  for (const l of all) {
    const m = /^\s*/.exec(l.text)[0];
    const strip = /^(\s*)(#{1,6}\s+|>\s?|[-*+]\s+\[[ xX]\]\s+|[-*+]\s+|\d+[.)]\s+)/.exec(l.text);
    if (allHave) { const mm = re.exec(l.text); changes.push({ from: l.from, to: l.from + mm[0].length, insert: m }); continue; }
    const p = opts.numbered ? `${n++}. ` : prefix;
    if (strip && opts.replace !== false) changes.push({ from: l.from + strip[1].length, to: l.from + strip[0].length, insert: p });
    else changes.push({ from: l.from + m.length, insert: p });
  }
  view.dispatch({ changes, userEvent: "input.format", scrollIntoView: true }); view.focus();
}
export function mdHeading(view, level) {
  const prefix = "#".repeat(level) + " ";
  mdLinePrefix(view, prefix, { re: new RegExp("^#{" + level + "}\\s+") });
}
export function mdInsert(view, text, selectOffset = null) {
  const r = selRange(view);
  const line = view.state.doc.lineAt(r.from);
  const needNl = r.from !== line.from && text.startsWith("\n") === false && /\n/.test(text) ? "\n" : "";
  const insert = needNl + text;
  view.dispatch({ changes: { from: r.from, to: r.to, insert }, selection: { anchor: r.from + (selectOffset ?? insert.length) }, scrollIntoView: true, userEvent: "input" });
  view.focus();
}
export function mdLink(view) {
  const r = selRange(view);
  const text = view.state.sliceDoc(r.from, r.to);
  const isUrl = /^https?:\/\/\S+$/.test(text);
  const insert = isUrl ? `[](${text})` : `[${text || t("md_link_text")}](https://)`;
  const cursor = isUrl ? r.from + 1 : r.from + insert.length - 1 - "https://".length;
  view.dispatch({ changes: { from: r.from, to: r.to, insert }, selection: isUrl ? { anchor: cursor } : { anchor: cursor, head: cursor + 8 }, userEvent: "input" });
  view.focus();
}
export function mdTable(rows, cols, header = true) {
  const head = Array.from({ length: cols }, (_, i) => `${t("md_col")} ${i + 1}`);
  const lines = ["| " + head.join(" | ") + " |", "|" + head.map(() => " --- ").join("|") + "|"];
  for (let r = 0; r < Math.max(1, rows - (header ? 1 : 0)); r++) lines.push("|" + head.map(() => "     ").join("|") + "|");
  return "\n" + lines.join("\n") + "\n";
}
/** Re-align the Markdown table under the cursor (pipes in columns). */
export function mdFormatTable(view) {
  const { state } = view;
  const at = state.doc.lineAt(selRange(view).head);
  const isRow = (l) => /^\s*\|.*\|\s*$/.test(l.text);
  if (!isRow(at)) return false;
  let first = at.number, last = at.number;
  while (first > 1 && isRow(state.doc.line(first - 1))) first--;
  while (last < state.doc.lines && isRow(state.doc.line(last + 1))) last++;
  const rows = [];
  for (let n = first; n <= last; n++) rows.push(splitRow(state.doc.line(n).text));
  const width = Math.max(...rows.map((r) => r.length));
  const sepIdx = rows.findIndex((r) => r.every((c) => /^:?-{1,}:?$/.test(c.trim()) || c.trim() === ""));
  const widths = Array.from({ length: width }, (_, i) => Math.max(3, ...rows.filter((_, j) => j !== sepIdx).map((r) => [...(r[i] || "")].length)));
  const out = rows.map((r, j) => "| " + widths.map((w, i) => {
    const c = (r[i] || "").trim();
    if (j === sepIdx) { const left = c.startsWith(":"), right = c.endsWith(":") && c.length > 1; return (left ? ":" : "-") + "-".repeat(Math.max(1, w - 2)) + (right ? ":" : "-"); }
    return c + " ".repeat(Math.max(0, w - [...c].length));
  }).join(" | ") + " |");
  view.dispatch({ changes: { from: state.doc.line(first).from, to: state.doc.line(last).to, insert: out.join("\n") }, userEvent: "input.format" });
  return true;
}
function splitRow(text) {
  const inner = text.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells = []; let cur = "", esc = false;
  for (const ch of inner) { if (esc) { cur += ch; esc = false; continue; } if (ch === "\\") { cur += ch; esc = true; continue; } if (ch === "|") { cells.push(cur); cur = ""; continue; } cur += ch; }
  cells.push(cur);
  return cells.map((c) => c.trim());
}

// ------------------------------------------------------------------ text tools (Notepad++-style)
function transformLines(view, fn, wholeDocIfEmpty = true) {
  const { state } = view;
  const r = selRange(view);
  let from, to;
  if (r.empty && wholeDocIfEmpty) { from = 0; to = state.doc.length; }
  else { from = state.doc.lineAt(r.from).from; to = state.doc.lineAt(r.to).to; }
  const lines = state.sliceDoc(from, to).split("\n");
  const out = fn(lines).join("\n");
  if (out === state.sliceDoc(from, to)) return;
  view.dispatch({ changes: { from, to, insert: out }, userEvent: "input.tool" });
}
const collator = () => new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
export const TOOLS = {
  sortAsc: (v) => transformLines(v, (l) => l.slice().sort(collator().compare)),
  sortDesc: (v) => transformLines(v, (l) => l.slice().sort(collator().compare).reverse()),
  sortLength: (v) => transformLines(v, (l) => l.slice().sort((a, b) => a.length - b.length)),
  reverse: (v) => transformLines(v, (l) => l.slice().reverse()),
  shuffle: (v) => transformLines(v, (l) => { const a = l.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; }),
  unique: (v) => transformLines(v, (l) => [...new Set(l)]),
  removeEmpty: (v) => transformLines(v, (l) => l.filter((x) => x.trim() !== "")),
  trimTrailing: (v) => transformLines(v, (l) => l.map((x) => x.replace(/[ \t]+$/, ""))),
  trimLeading: (v) => transformLines(v, (l) => l.map((x) => x.replace(/^[ \t]+/, ""))),
  tabsToSpaces: (v, n = 4) => transformLines(v, (l) => l.map((x) => x.replace(/\t/g, " ".repeat(n)))),
  spacesToTabs: (v, n = 4) => transformLines(v, (l) => l.map((x) => x.replace(new RegExp("^( {" + n + "})+"), (m) => "\t".repeat(m.length / n)))),
  join: (v) => { const { state } = v; const r = selRange(v); if (r.empty) return; const text = state.sliceDoc(r.from, r.to).split("\n").map((s, i) => (i ? s.trim() : s.trimEnd())).join(" "); v.dispatch({ changes: { from: r.from, to: r.to, insert: text } }); },
  upper: (v) => caseTool(v, (s) => s.toLocaleUpperCase()),
  lower: (v) => caseTool(v, (s) => s.toLocaleLowerCase()),
  title: (v) => caseTool(v, (s) => s.toLocaleLowerCase().replace(/(^|[\s\-"'(«“])(\p{L})/gu, (m, a, b) => a + b.toLocaleUpperCase())),
  sentence: (v) => caseTool(v, (s) => s.toLocaleLowerCase().replace(/(^\s*|[.!?…]\s+)(\p{L})/gu, (m, a, b) => a + b.toLocaleUpperCase())),
  invert: (v) => caseTool(v, (s) => [...s].map((c) => (c === c.toLocaleUpperCase() ? c.toLocaleLowerCase() : c.toLocaleUpperCase())).join("")),
  number: (v) => transformLines(v, (l) => { const w = String(l.length).length; return l.map((x, i) => String(i + 1).padStart(w, " ") + ". " + x); }),
  base64enc: (v) => selTool(v, (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)))),
  base64dec: (v) => selTool(v, (s) => new TextDecoder().decode(Uint8Array.from(atob(s.trim()), (c) => c.charCodeAt(0)))),
  urlenc: (v) => selTool(v, (s) => encodeURIComponent(s)),
  urldec: (v) => selTool(v, (s) => decodeURIComponent(s)),
};
function caseTool(view, fn) { const { state } = view; view.dispatch(state.changeByRange((r) => { const s = r.empty ? state.wordAt(r.head) || r : r; return { changes: { from: s.from, to: s.to, insert: fn(state.sliceDoc(s.from, s.to)) }, range: CM.EditorSelection.range(s.from, s.to) }; })); }
function selTool(view, fn) { const { state } = view; try { view.dispatch(state.changeByRange((r) => { if (r.empty) return { range: r }; const out = fn(state.sliceDoc(r.from, r.to)); return { changes: { from: r.from, to: r.to, insert: out }, range: CM.EditorSelection.range(r.from, r.from + out.length) }; })); } catch (e) { throw new Error(t("tool_failed")); } }

export function insertDateTime(view) {
  const now = new Date();
  const s = now.toLocaleString(document.documentElement.lang || undefined, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  mdInsert(view, s);
}

// ------------------------------------------------------------------ JSON helpers
export function jsonFormat(text, indent = 2) { return JSON.stringify(JSON.parse(text), null, indent); }
export function jsonError(text) {
  try { JSON.parse(text); return null; }
  catch (e) {
    const m = /position (\d+)/.exec(e.message) || /column (\d+)/.exec(e.message);
    return { message: e.message, pos: m ? +m[1] : -1 };
  }
}

// ------------------------------------------------------------------ split view sync (one document in two panes)
export const syncAnnotation = CM.Transaction ? CM.StateEffect.define() : null;
export function syncDispatch(views, from, to) {
  return (tr) => {
    views[from].update([tr]);
    if (!tr.changes.empty && !tr.annotation(CM.Transaction.remote)) {
      const effects = [];
      views[to].dispatch({ changes: tr.changes, annotations: [CM.Transaction.remote.of(true)], effects });
    }
  };
}
