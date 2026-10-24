// MemuNote (Round 33, item #9) — combined live-preview Markdown editor (Obsidian / Typora style).
//
// The document text is never changed: everything here is CodeMirror decorations over the Markdown syntax tree.
// Formatting is shown rendered (headings, bold/italic, strike, inline code, links, lists, task checkboxes, quotes,
// rules, code blocks, tables, images); the raw Markdown of an element appears only where the caret is:
//   * inline elements (bold, italic, `code`, links, images, ~~strike~~) — when the caret/selection touches THAT element,
//   * line markers (# heading, > quote, - bullet, ---) — when the caret is on that line,
//   * blocks (tables, ``` fences) — when the caret is inside the block.
// Undo, IME, search, AI edits and saving work on the real text as before (saving writes exactly what was typed).
// Inline/line decorations come from a ViewPlugin over the visible ranges only (fast on big files); tables are
// block widgets, which CodeMirror allows only from a StateField.
import { CM } from "./editor.js";
import { assetUrl, api, t } from "./core.js";

const { Decoration, ViewPlugin, WidgetType, EditorView, StateField, syntaxTree } = CM;

export const LIVE_MAX = 3 * 1048576;     // larger Markdown files open in the source mode (decorations cost)

// ------------------------------------------------------------------ helpers
function touches(sel, from, to) {
  for (const r of sel.ranges) if (r.from <= to && r.to >= from) return true;
  return false;
}
function lineTouched(state, sel, pos) {
  const line = state.doc.lineAt(pos);
  return touches(sel, line.from, line.to);
}
const hide = Decoration.replace({});
const lineDeco = (cls) => Decoration.line({ class: cls });
const markDeco = (cls, attrs) => Decoration.mark({ class: cls, attributes: attrs });

export function resolveImage(src, base, sep = "\\") {
  if (!src) return { url: "" };
  if (/^(data:image\/|blob:)/i.test(src)) return { url: src };
  if (/^https?:/i.test(src)) return { remote: src };          // CSP: the page never loads remote pictures by itself
  let path = src;
  try { path = decodeURIComponent(src); } catch { /* keep as typed */ }
  path = path.replace(/^<|>$/g, "").replace(/^file:\/\/\/?/i, "");
  if (!/^([a-zA-Z]:[\\/]|\/|\\\\)/.test(path)) {
    if (!base) return { url: "" };
    path = base + sep + path.replace(/^\.[\\/]/, "").replace(/[\\/]/g, sep);
  }
  return { url: assetUrl(path) };
}

// ------------------------------------------------------------------ widgets
class BulletWidget extends WidgetType {
  constructor(depth) { super(); this.depth = depth; }
  eq(o) { return o.depth === this.depth; }
  toDOM() { const s = document.createElement("span"); s.className = "mn-lm-bullet d" + (this.depth % 3); s.setAttribute("aria-hidden", "true"); return s; }
}
class CheckWidget extends WidgetType {
  constructor(checked) { super(); this.checked = checked; }
  eq(o) { return o.checked === this.checked; }
  toDOM(view) {
    const b = document.createElement("span");
    b.className = "mn-lm-check" + (this.checked ? " on" : "");
    b.setAttribute("role", "checkbox"); b.setAttribute("aria-checked", String(this.checked));
    b.title = t("lm_toggle_task");
    b.addEventListener("mousedown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const pos = view.posAtDOM(b);
      const line = view.state.doc.lineAt(pos);
      const m = /^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\]/.exec(line.text);
      if (!m || view.state.readOnly) return;
      const at = line.from + m[1].length + 1;
      view.dispatch({ changes: { from: at, to: at + 1, insert: m[2] === " " ? "x" : " " }, userEvent: "input.toggle" });
    });
    return b;
  }
  ignoreEvent() { return true; }
}
class HrWidget extends WidgetType {
  eq() { return true; }
  toDOM() { const s = document.createElement("span"); s.className = "mn-lm-hr"; s.setAttribute("aria-hidden", "true"); return s; }
}
class FenceWidget extends WidgetType {
  constructor(lang, open) { super(); this.lang = lang; this.open = open; }
  eq(o) { return o.lang === this.lang && o.open === this.open; }
  toDOM() {
    const s = document.createElement("span"); s.className = "mn-lm-fence" + (this.open ? " open" : " close");
    if (this.open && this.lang) s.textContent = this.lang;
    return s;
  }
}
class ImageWidget extends WidgetType {
  constructor(src, alt, base, sep) { super(); this.src = src; this.alt = alt; this.base = base; this.sep = sep; }
  eq(o) { return o.src === this.src && o.alt === this.alt && o.base === this.base; }
  toDOM() {
    const r = resolveImage(this.src, this.base, this.sep);
    const wrap = document.createElement("span"); wrap.className = "mn-lm-img";
    if (r.url) {
      const img = document.createElement("img"); img.src = r.url; img.alt = this.alt || ""; img.loading = "lazy"; img.draggable = false;
      img.onerror = () => { wrap.classList.add("broken"); wrap.textContent = (this.alt || this.src) + " — " + t("lm_image_missing"); };
      wrap.append(img);
    } else {
      wrap.classList.add("remote"); wrap.textContent = (this.alt ? this.alt + " · " : "") + (r.remote || this.src);
      wrap.title = t("lm_image_remote");
    }
    return wrap;
  }
  ignoreEvent() { return false; }
}
class TableWidget extends WidgetType {
  constructor(text, from) { super(); this.text = text; this.from = from; }
  eq(o) { return o.text === this.text; }
  toDOM(view) {
    const wrap = document.createElement("div"); wrap.className = "mn-lm-table doc-prose";
    wrap.innerHTML = renderTable(this.text);
    wrap.title = t("lm_table_edit");
    wrap.addEventListener("mousedown", (e) => {
      e.preventDefault();
      // caret into the clicked cell's row (or the table start): the raw table appears for editing
      const rows = [...wrap.querySelectorAll("tr")];
      const tr = e.target.closest?.("tr");
      let row = Math.max(0, rows.indexOf(tr));
      if (row > 0) row += 1;     // skip the |---| delimiter line
      const start = view.posAtDOM(wrap);
      const line = view.state.doc.lineAt(Math.min(view.state.doc.length, start));
      const target = view.state.doc.line(Math.min(view.state.doc.lines, line.number + row));
      // …and into the clicked cell: after the (n+1)-th unescaped pipe
      const cell = e.target.closest?.("td,th"), idx = cell ? cell.cellIndex : 0;
      const pipes = []; for (let i = 0; i < target.text.length; i++) if (target.text[i] === "|" && target.text[i - 1] !== "\\") pipes.push(i);
      const lead = /^\s*\|/.test(target.text) ? 0 : -1;
      const at = pipes[idx + lead] ?? -1;
      let pos = at >= 0 ? target.from + at + 1 : target.from;
      while (pos < target.to && view.state.sliceDoc(pos, pos + 1) === " ") pos++;
      view.dispatch({ selection: { anchor: Math.min(target.to, pos) }, scrollIntoView: false });
      view.focus();
    });
    return wrap;
  }
  ignoreEvent() { return false; }
  get estimatedHeight() { return 32 * (this.text.split("\n").length - 1) + 8; }
}

// Markdown table → HTML (GFM pipes, alignment row, escaped pipes, inline formatting kept as text + simple marks)
function splitCells(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out = []; let cur = "", esc = false, code = false;
  for (const ch of s) {
    if (esc) { cur += ch; esc = false; continue; }
    if (ch === "\\") { esc = true; cur += ch; continue; }
    if (ch === "`") code = !code;
    if (ch === "|" && !code) { out.push(cur.trim()); cur = ""; continue; }
    cur += ch;
  }
  out.push(cur.trim());
  return out;
}
const escHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export function inlineHtml(text) {
  let h = escHtml(text.replace(/\\\|/g, "|"));
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => `<strong>${a || b}</strong>`)
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, '<a href="#" data-href="$2">$1</a>');
  return h;
}
export function renderTable(text) {
  const lines = text.split("\n").filter((l) => l.trim());
  if (lines.length < 2) return "";
  const head = splitCells(lines[0]);
  const aligns = splitCells(lines[1]).map((c) => (/^:-+:$/.test(c) ? "center" : /^-+:$/.test(c) ? "right" : /^:-+$/.test(c) ? "left" : ""));
  const al = (i) => (aligns[i] ? ` style="text-align:${aligns[i]}"` : "");
  let h = "<table><thead><tr>" + head.map((c, i) => `<th${al(i)}>${inlineHtml(c)}</th>`).join("") + "</tr></thead><tbody>";
  for (const l of lines.slice(2)) {
    const cells = splitCells(l);
    h += "<tr>" + head.map((_, i) => `<td${al(i)}>${inlineHtml(cells[i] || "")}</td>`).join("") + "</tr>";
  }
  return h + "</tbody></table>";
}

// ------------------------------------------------------------------ inline + line decorations (visible ranges)
function buildDecos(view, opts) {
  const { state } = view;
  const sel = state.selection;
  const focused = view.hasFocus || opts.alwaysCaret;
  const tree = syntaxTree(state);
  const out = [];
  const add = (from, to, deco) => { if (to >= from) out.push(deco.range(from, to)); };
  const lineAdd = (pos, cls) => out.push(lineDeco(cls).range(state.doc.lineAt(pos).from));
  const isActive = (from, to) => focused && touches(sel, from, to);
  const lineActive = (pos) => focused && lineTouched(state, sel, pos);
  const base = opts.getBase?.() || {};
  for (const { from, to } of view.visibleRanges) {
    tree.iterate({
      from, to,
      enter: (node) => {
        const name = node.name;
        // ---- headings
        const hm = /^ATXHeading(\d)$/.exec(name);
        if (hm) {
          lineAdd(node.from, "mn-lm-h mn-lm-h" + hm[1]);
          if (!lineActive(node.from)) {
            const c = node.node.cursor();
            if (c.firstChild()) do {
              if (c.name === "HeaderMark") {
                let end = c.to;
                if (c.from === node.from && state.sliceDoc(end, end + 1) === " ") end++;
                add(c.from === node.from ? c.from : Math.max(node.from, c.from - (state.sliceDoc(c.from - 1, c.from) === " " ? 1 : 0)), end, hide);
              }
            } while (c.nextSibling());
          }
          return;
        }
        const sm = /^SetextHeading(\d)$/.exec(name);
        if (sm) {
          const first = state.doc.lineAt(node.from), last = state.doc.lineAt(node.to);
          lineAdd(first.from, "mn-lm-h mn-lm-h" + sm[1]);
          if (last.number > first.number) {
            const active = isActive(node.from, node.to);
            lineAdd(last.from, active ? "mn-lm-setext-raw" : "mn-lm-setext");
            if (!active) add(last.from, last.to, hide);
          }
          return;
        }
        switch (name) {
          case "Emphasis": case "StrongEmphasis": case "Strikethrough": case "InlineCode": {
            if (name === "Strikethrough") add(node.from, node.to, markDeco("mn-lm-strike"));
            if (name === "InlineCode") add(node.from, node.to, markDeco("mn-lm-icode"));
            if (!isActive(node.from, node.to)) {
              const c = node.node.cursor();
              if (c.firstChild()) do {
                if (c.name === "EmphasisMark" || c.name === "StrikethroughMark" || c.name === "CodeMark") add(c.from, c.to, hide);
              } while (c.nextSibling());
            }
            return name === "InlineCode" ? false : undefined;
          }
          case "Link": {
            const marks = []; let url = null;
            const c = node.node.cursor();
            if (c.firstChild()) do {
              if (c.name === "LinkMark") marks.push({ from: c.from, to: c.to });
              if (c.name === "URL") url = { from: c.from, to: c.to };
            } while (c.nextSibling());
            if (marks.length >= 2 && marks[0].to <= marks[1].from) {
              const href = url ? state.sliceDoc(url.from, url.to) : "";
              add(marks[0].to, marks[1].from, markDeco("mn-lm-link", href ? { "data-href": href, title: href + " · " + t("lm_link_open") } : undefined));
              if (!isActive(node.from, node.to)) { add(node.from, marks[0].to, hide); add(marks[1].from, node.to, hide); }
            }
            return;
          }
          case "Autolink": {
            const inner = state.sliceDoc(node.from, node.to);
            if (!isActive(node.from, node.to) && inner.startsWith("<") && inner.endsWith(">")) {
              add(node.from, node.from + 1, hide); add(node.to - 1, node.to, hide);
              add(node.from + 1, node.to - 1, markDeco("mn-lm-link", { "data-href": inner.slice(1, -1) }));
              return false;
            }
            return;
          }
          case "URL": {
            const parent = node.node.parent?.name;
            if (parent !== "Link" && parent !== "Image" && parent !== "LinkReference") add(node.from, node.to, markDeco("mn-lm-link", { "data-href": state.sliceDoc(node.from, node.to) }));
            return;
          }
          case "Image": {
            if (!isActive(node.from, node.to)) {
              const textv = state.sliceDoc(node.from, node.to);
              const m = /^!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)/.exec(textv);
              if (m && !textv.includes("\n")) { add(node.from, node.to, Decoration.replace({ widget: new ImageWidget(m[2].replace(/^<|>$/g, ""), m[1], base.dir || "", base.sep || "\\") })); return false; }
            }
            return;
          }
          case "Escape": {
            if (!isActive(node.from, node.to)) add(node.from, node.from + 1, hide);
            return;
          }
          case "Blockquote": {
            for (let l = state.doc.lineAt(node.from).number, e = state.doc.lineAt(node.to).number; l <= e; l++) lineAdd(state.doc.line(l).from, "mn-lm-quote");
            return;
          }
          case "QuoteMark": {
            if (!lineActive(node.from)) add(node.from, state.sliceDoc(node.to, node.to + 1) === " " ? node.to + 1 : node.to, hide);
            return;
          }
          case "ListItem": {
            const c = node.node.cursor(); let mark = null, task = null;
            if (c.firstChild()) do {
              if (c.name === "ListMark") mark = { from: c.from, to: c.to };
              if (c.name === "Task") { const t2 = c.node.firstChild; if (t2?.name === "TaskMarker") task = { from: t2.from, to: t2.to }; }
            } while (c.nextSibling());
            if (!mark) return;
            const ordered = node.node.parent?.name === "OrderedList";
            let depth = 0; for (let p = node.node.parent; p; p = p.parent) if (p.name === "ListItem") depth++;
            if (task) {
              const checked = /x/i.test(state.sliceDoc(task.from, task.to));
              if (checked) lineAdd(node.from, "mn-lm-done");
              if (!isActive(mark.from, task.to)) add(ordered ? task.from : mark.from, task.to, Decoration.replace({ widget: new CheckWidget(checked) }));
            } else if (!ordered && !lineActive(node.from)) {
              add(mark.from, mark.to, Decoration.replace({ widget: new BulletWidget(depth) }));
            } else if (ordered) add(mark.from, mark.to, markDeco("mn-lm-olnum"));
            return;
          }
          case "HorizontalRule": {
            if (!lineActive(node.from)) add(node.from, node.to, Decoration.replace({ widget: new HrWidget() }));
            return false;
          }
          case "FencedCode": case "CodeBlock": {
            const first = state.doc.lineAt(node.from), last = state.doc.lineAt(node.to);
            const active = isActive(node.from, node.to);
            for (let l = first.number; l <= last.number; l++) {
              const line = state.doc.line(l);
              out.push(lineDeco("mn-lm-codeblock" + (l === first.number ? " first" : "") + (l === last.number ? " last" : "")).range(line.from));
            }
            if (name === "FencedCode" && !active) {
              const info = node.node.getChild("CodeInfo");
              const lang = info ? state.sliceDoc(info.from, info.to) : "";
              add(first.from, first.to, Decoration.replace({ widget: new FenceWidget(lang, true) }));
              if (last.number > first.number && /^\s*(```|~~~)/.test(last.text)) add(last.from, last.to, Decoration.replace({ widget: new FenceWidget("", false) }));
            }
            return false;
          }
          case "Table": {
            const active = isActive(node.from, node.to);
            if (active) for (let l = state.doc.lineAt(node.from).number, e = state.doc.lineAt(node.to).number; l <= e; l++) lineAdd(state.doc.line(l).from, "mn-lm-table-raw");
            return false;          // the rendered table is a block widget (tableField)
          }
          case "HTMLBlock": case "CommentBlock": case "ProcessingInstructionBlock": return false;
          default: return;
        }
      },
    });
  }
  return Decoration.set(out, true);
}

function livePlugin(opts) {
  return ViewPlugin.fromClass(class {
    constructor(view) { this.decorations = buildDecos(view, opts); }
    update(u) {
      if (u.docChanged || u.viewportChanged || u.selectionSet || u.focusChanged || syntaxTree(u.startState) !== syntaxTree(u.state)) this.decorations = buildDecos(u.view, opts);
    }
  }, { decorations: (v) => v.decorations });
}

// ------------------------------------------------------------------ tables (block widgets, StateField)
function tableDecos(state, focusedRef) {
  const out = [];
  const sel = state.selection;
  syntaxTree(state).iterate({
    enter: (node) => {
      if (node.name === "Table") {
        const from = state.doc.lineAt(node.from).from, to = state.doc.lineAt(node.to).to;
        if (!(focusedRef.focused && touches(sel, from, to))) {
          out.push(Decoration.replace({ widget: new TableWidget(state.sliceDoc(from, to), from), block: true }).range(from, to));
        }
        return false;
      }
      // only containers can hold a table: skip the inside of everything else
      return node.name === "Document" || node.name === "Blockquote" || node.name === "BulletList" || node.name === "OrderedList" || node.name === "ListItem" ? undefined : false;
    },
  });
  return Decoration.set(out, true);
}
const setFocus = CM.StateEffect.define();
function tableField() {
  const ref = { focused: false };
  const field = StateField.define({
    create: (state) => tableDecos(state, ref),
    update(value, tr) {
      let refresh = tr.docChanged || tr.selection || syntaxTree(tr.state) !== syntaxTree(tr.startState);
      for (const e of tr.effects) if (e.is(setFocus)) { ref.focused = e.value; refresh = true; }
      return refresh ? tableDecos(tr.state, ref) : value;
    },
    provide: (f) => EditorView.decorations.from(f),
  });
  const focusWatch = ViewPlugin.fromClass(class {
    update(u) { if (u.focusChanged) { const f = u.view.hasFocus; queueMicrotask(() => u.view.dispatch({ effects: setFocus.of(f) })); } }
  });
  return [field, focusWatch];
}

// ------------------------------------------------------------------ clicks on links
function linkClicks(opts) {
  return EditorView.domEventHandlers({
    mousedown: (e, view) => {
      const a = e.target.closest?.("[data-href]");
      if (!a || !(e.ctrlKey || e.metaKey)) return false;
      e.preventDefault();
      opts.openLink?.(a.getAttribute("data-href"));
      return true;
    },
  });
}

/** opts: {getBase() → {dir, sep}, openLink(href)} */
export function liveMarkdown(opts = {}) {
  return [
    EditorView.editorAttributes.of({ class: "mn-live" }),
    livePlugin(opts),
    tableField(),
    linkClicks(opts),
  ];
}

export const _test = { splitCells, renderTable, inlineHtml, resolveImage };
