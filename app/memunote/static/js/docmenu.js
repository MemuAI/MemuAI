// Right-click menus for the document editor, text fields and read-only views.
// The desktop window (pywebview) switches the browser's own context menu off, so without these a right
// click in a DOCX page, in an input or in a preview did nothing at all.
import { ICONS, api, t, toast, showMenu, copyText, kbd, prompt, sleep } from "./core.js";
import { tokenize, checkWord, suggestions, addToDictionary } from "./spell.js";

const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const OWN_ICONS = {
  cut: P('<circle cx="6.5" cy="6.5" r="2.5"/><circle cx="6.5" cy="17.5" r="2.5"/><path d="M8.6 8 19 17.5M8.6 16 19 6.5"/>'),
  paste: P('<path d="M9 4.5H7.5a2 2 0 0 0-2 2V19a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V6.5a2 2 0 0 0-2-2H15"/><rect x="9" y="3" width="6" height="3.5" rx="1"/><path d="M9 11h6M9 14.5h6M9 18h3.5"/>'),
  trash: P('<path d="M4 7h16M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 11v5.5M14 11v5.5"/>'),
  external: P('<path d="M14 4h6v6M20 4l-8.5 8.5"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>'),
  unlink: P('<path d="m9.5 14.5-1.8 1.8a3 3 0 0 1-4.2-4.2L5.8 9.8M14.5 9.5l1.8-1.8a3 3 0 0 1 4.2 4.2l-2.3 2.3"/><path d="M4 4l16 16"/>'),
  resize: P('<path d="M4 9V4h5M20 15v5h-5M4 4l6.5 6.5M20 20l-6.5-6.5"/>'),
  selectAll: P('<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M8.5 9.5h7M8.5 14.5h4.5"/>'),
};
for (const [name, svg] of Object.entries(OWN_ICONS)) if (!ICONS[name]) ICONS[name] = svg;

const PAGE_PX = 624;
const FIELD_TYPES = new Set(["", "text", "search", "url", "email", "tel", "password", "number"]);

// Runs `before` ahead of every action: a click in the menu takes the focus, actions first put the selection back.
function bindAll(items, before) {
  return items.map((item) => {
    if (!item || item === "-" || item.header) return item;
    const out = { ...item };
    if (item.sub) out.sub = bindAll(item.sub, before);
    if (item.run) out.run = () => { before(); return item.run(); };
    if (item.pick) out.pick = (value) => { before(); return item.pick(value); };
    return out;
  });
}
function setSelection(range) { const s = window.getSelection(); s.removeAllRanges(); s.addRange(range); }
function selectNode(node) { const r = document.createRange(); r.selectNode(node); setSelection(r); }
function execClip(cmd) { try { return document.execCommand(cmd); } catch { return false; } }
function done(key = "copied") { toast(t(key), { kind: "ok", ms: 1400 }); }
function asElement(node) { return node instanceof Element ? node : node?.parentElement || null; }

// { html, text } or { image } from the system clipboard; null when the page may not read it.
async function readClipboard() {
  try {
    if (navigator.clipboard?.read) {
      for (const item of await navigator.clipboard.read()) {
        const text = item.types.includes("text/plain") ? await (await item.getType("text/plain")).text() : "";
        if (item.types.includes("text/html")) return { html: await (await item.getType("text/html")).text(), text };
        const image = item.types.find((type) => type.startsWith("image/"));
        if (image && !text) return { image: await item.getType(image) };
        if (text) return { text };
      }
      return { text: "" };
    }
  } catch { /* fall back to plain text */ }
  try { return { text: await navigator.clipboard.readText() }; } catch { return null; }
}
function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = reject; r.readAsDataURL(blob); });
}
// The page's CSP keeps fetch() away from data: URLs, so embedded pictures are decoded by hand.
function dataUrlBytes(src) {
  const m = /^data:([\w.+-]+\/[\w.+-]+)?((?:;[\w-]+=[^;,]*)*)(;base64)?,(.*)$/s.exec(String(src || ""));
  if (!m) return null;
  const raw = m[3] ? atob(m[4].replace(/\s+/g, "")) : decodeURIComponent(m[4]);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i) & 255;
  return { bytes, type: m[1] || "application/octet-stream" };
}
async function imageBytes(img) {
  const src = img.getAttribute("src") || "";
  const inline = dataUrlBytes(src);
  if (inline) return inline;
  const res = await fetch(img.src);
  if (!res.ok) throw new Error("HTTP " + res.status);
  const blob = await res.blob();
  return { bytes: new Uint8Array(await blob.arrayBuffer()), type: blob.type || "image/png" };
}
async function pngBlob(img) {
  const data = await imageBytes(img);
  if (data.type === "image/png") return new Blob([data.bytes], { type: "image/png" });
  const pic = new Image(); pic.src = img.currentSrc || img.src; await pic.decode();
  const canvas = document.createElement("canvas"); canvas.width = pic.naturalWidth || 1; canvas.height = pic.naturalHeight || 1;
  canvas.getContext("2d").drawImage(pic, 0, 0);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("png"))), "image/png"));
}
async function copyImage(img, fallback) {
  try {
    const blob = await pngBlob(img);
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    done("cm_img_copied");
  } catch {
    if (fallback?.()) done("cm_img_copied"); else toast(t("cm_img_copy_failed"), { kind: "error" });
  }
}
async function saveImage(img, hooks) {
  try {
    const data = await imageBytes(img);
    const ext = { "image/jpeg": "jpg", "image/svg+xml": "svg", "image/x-icon": "ico" }[data.type] || (data.type.split("/")[1] || "png").replace(/[^\w]/g, "");
    hooks.saveImage(data.bytes, ext, data.type);
  } catch { toast(t("image_failed"), { kind: "error" }); }
}
function imageSaveItems(img, hooks, fallbackCopy) {
  return [
    { label: t("cm_img_copy"), icon: "copy", run: () => copyImage(img, fallbackCopy) },
    ...(hooks.saveImage ? [{ label: t("cm_img_save") + "…", icon: "save", run: () => saveImage(img, hooks) }] : []),
  ];
}
function linkTarget(a) { return a.getAttribute("href") || ""; }
function isExternal(href) { return /^(https?:|mailto:)/i.test(href); }

/* ---------------------------------------------------------------- document editor */

function wordAtPoint(x, y, page) {
  const pos = document.caretRangeFromPoint?.(x, y);
  const node = pos?.startContainer;
  if (!node || node.nodeType !== 3 || !page.contains(node)) return null;
  const w = tokenize(node.data).find((tok) => pos.startOffset >= tok.from && pos.startOffset <= tok.to);
  return w ? { node, from: w.from, to: w.to, word: w.word } : null;
}
function formatItems(doc) {
  const on = (cmd) => { try { return document.queryCommandState(cmd); } catch { return false; } };
  const F = (cmd, label, ic, key) => ({ label: t(label), icon: ic, kbd: key ? kbd(key) : undefined, checked: on(cmd), run: () => doc.exec(cmd) });
  const { colors, marks } = doc.palettes;
  return [
    F("bold", "fmt_bold", "bold", "Ctrl+B"), F("italic", "fmt_italic", "italic", "Ctrl+I"), F("underline", "fmt_underline", "underline", "Ctrl+U"),
    F("strikeThrough", "fmt_strike", "strike"), F("superscript", "fmt_sup", "sup"), F("subscript", "fmt_sub", "sub"),
    "-",
    { label: t("fmt_color"), icon: "color", sub: [{ swatches: colors, pick: (c) => doc.exec("foreColor", c) }] },
    { label: t("fmt_highlight"), icon: "highlight", sub: [{ swatches: marks, noneLabel: t("color_none"), pick: (c) => doc.exec("hiliteColor", c) }] },
    "-",
    { label: t("fmt_clear"), icon: "eraser", run: () => doc.exec("removeFormat") },
  ];
}
function paragraphItems(doc) {
  let block = "p";
  try { block = String(document.queryCommandValue("formatBlock") || "p").toLowerCase(); } catch { /* keep "p" */ }
  const on = (cmd) => { try { return document.queryCommandState(cmd); } catch { return false; } };
  const A = (cmd, label, ic) => ({ label: t(label), icon: ic, checked: on(cmd), run: () => doc.exec(cmd) });
  return [
    ...doc.palettes.blocks.map(([value, key]) => ({ label: t(key), checked: block === value, run: () => doc.exec("formatBlock", value) })),
    "-",
    A("justifyLeft", "align_left", "alignLeft"), A("justifyCenter", "align_center", "alignCenter"), A("justifyRight", "align_right", "alignRight"), A("justifyFull", "align_justify", "alignJustify"),
    "-",
    A("insertUnorderedList", "fmt_ul", "listUl"), A("insertOrderedList", "fmt_ol", "listOl"),
    { label: t("fmt_tasks"), icon: "checklist", run: () => doc.command("tasks") },
    { label: t("indent"), icon: "indent", run: () => doc.exec("indent") },
    { label: t("outdent"), icon: "outdent", run: () => doc.exec("outdent") },
  ];
}
function insertItems(doc) {
  return [
    { label: t("fmt_link") + "…", icon: "link", kbd: kbd("Ctrl+K"), run: () => doc.command("link") },
    { label: t("fmt_table") + "…", icon: "table", run: () => doc.command("table") },
    { label: t("fmt_image") + "…", icon: "image", run: () => doc.command("image") },
    { label: t("fmt_hr"), icon: "hr", run: () => doc.command("insertHorizontalRule") },
    { label: t("fmt_pagebreak"), icon: "pageBreak", run: () => doc.command("pagebreak") },
  ];
}
function resizeImage(doc, img, width) {
  if (!img.isConnected) return;
  const w = Math.max(16, Math.round(width));
  const ratio = img.naturalWidth ? img.naturalHeight / img.naturalWidth : (img.height || 1) / Math.max(1, img.width || 1);
  const copy = img.cloneNode(false);
  copy.setAttribute("width", String(w)); copy.setAttribute("height", String(Math.max(1, Math.round(w * ratio))));
  copy.style.removeProperty("width"); copy.style.removeProperty("height");
  selectNode(img); doc.exec("insertHTML", copy.outerHTML);
}
function replaceImage(doc, img, src) {
  if (!img.isConnected) return;
  const copy = img.cloneNode(false);
  copy.setAttribute("src", src); copy.removeAttribute("height"); copy.style.removeProperty("height");
  selectNode(img); doc.exec("insertHTML", copy.outerHTML);
}
function imageItems(doc, img, hooks) {
  const natural = img.naturalWidth || 0;
  const pct = (n) => ({ label: t("cm_img_pct", { n }), run: () => resizeImage(doc, img, PAGE_PX * n / 100) });
  const align = (cmd, label, ic) => ({ label: t(label), icon: ic, run: () => { selectNode(img); doc.exec(cmd); } });
  return [
    { header: t("fmt_image") },
    { label: t("cm_img_size"), icon: "resize", sub: [pct(25), pct(50), pct(75), { label: t("cm_img_page_width"), run: () => resizeImage(doc, img, PAGE_PX) },
      { label: t("cm_img_original"), disabled: !natural, run: () => resizeImage(doc, img, Math.min(natural, PAGE_PX)) }] },
    { label: t("cm_img_align"), icon: "alignCenter", sub: [align("justifyLeft", "align_left", "alignLeft"), align("justifyCenter", "align_center", "alignCenter"), align("justifyRight", "align_right", "alignRight")] },
    ...(doc.opts.onImage ? [{ label: t("cm_img_replace") + "…", icon: "image", run: () => doc.opts.onImage((url) => replaceImage(doc, img, url)) }] : []),
    ...imageSaveItems(img, hooks, () => { selectNode(img); return execClip("copy"); }),
    { label: t("cm_img_delete"), icon: "trash", danger: true, run: () => { if (img.isConnected) { selectNode(img); doc.exec("delete"); } } },
    "-",
  ];
}
function unlink(doc, a) {
  if (!a.isConnected) return;
  const r = document.createRange(); r.selectNodeContents(a); setSelection(r); doc.exec("unlink");
}
function linkItems(doc, a) {
  const href = linkTarget(a);
  const anchor = href.startsWith("#") && href.length > 1;
  return [
    { label: t("cm_link_open"), icon: "external", disabled: !isExternal(href) && !anchor, run: () => {
      if (isExternal(href)) api("external", { url: href });
      else doc.page.querySelector(`[id="${CSS.escape(href.slice(1))}"], a[name="${CSS.escape(href.slice(1))}"]`)?.scrollIntoView({ block: "center" });
    } },
    { label: t("cm_link_copy"), icon: "copy", disabled: !href, run: () => copyText(href).then(() => done()) },
    { label: t("cm_link_edit") + "…", icon: "link", run: async () => {
      const url = await prompt(t("fmt_link"), t("link_url"), href);
      if (url === null || url === undefined || !a.isConnected) return;
      if (!url.trim()) { unlink(doc, a); return; }
      a.setAttribute("href", url.trim()); doc.changed();
    } },
    { label: t("cm_link_remove"), icon: "unlink", run: () => unlink(doc, a) },
    "-",
  ];
}
function caretInto(cell) {
  if (!cell) return;
  const r = document.createRange(); r.selectNodeContents(cell); r.collapse(true); setSelection(r);
}
function tableOp(doc, cell, op) {
  const table = cell.closest("table");
  if (!table || !cell.isConnected) return;
  const row = cell.parentElement, rows = [...table.rows], ri = rows.indexOf(row), ci = cell.cellIndex;
  const blank = (tag) => { const x = document.createElement(tag); x.innerHTML = "<br>"; return x; };
  const dropTable = () => {
    const next = table.nextElementSibling;
    if (next) { table.remove(); caretInto(next); }
    else { const p = document.createElement("p"); p.innerHTML = "<br>"; table.replaceWith(p); caretInto(p); }
  };
  if (op === "rowAbove" || op === "rowBelow") {
    const fresh = document.createElement("tr");
    for (const c of row.cells) { const x = blank(op === "rowAbove" && c.tagName === "TH" ? "th" : "td"); if (c.colSpan > 1) x.colSpan = c.colSpan; fresh.append(x); }
    const head = row.parentElement.tagName === "THEAD";
    if (op === "rowBelow" && head && !row.nextElementSibling) {
      const body = table.tBodies[0] || table.appendChild(document.createElement("tbody"));
      body.insertBefore(fresh, body.firstChild);
    } else row.parentElement.insertBefore(fresh, op === "rowAbove" ? row : row.nextSibling);
    caretInto(fresh.cells[Math.min(ci, fresh.cells.length - 1)]);
  } else if (op === "colLeft" || op === "colRight") {
    for (const r of rows) {
      const ref = r.cells[Math.min(ci, r.cells.length - 1)];
      const x = blank(ref?.tagName === "TH" ? "th" : "td");
      if (!ref) r.append(x); else r.insertBefore(x, op === "colLeft" ? ref : ref.nextSibling);
    }
  } else if (op === "delRow") {
    if (rows.length <= 1) dropTable();
    else { const next = rows[ri + 1] || rows[ri - 1]; row.remove(); caretInto(next?.cells[Math.min(ci, next.cells.length - 1)]); }
  } else if (op === "delCol") {
    if (Math.max(...rows.map((r) => r.cells.length)) <= 1) dropTable();
    else { for (const r of rows) r.cells[Math.min(ci, r.cells.length - 1)]?.remove(); caretInto(row.cells[Math.max(0, ci - 1)]); }
  } else if (op === "delTable") dropTable();
  doc.changed(); doc.saveRange();
}
function tableItems(doc, cell) {
  const op = (name) => () => tableOp(doc, cell, name);
  return [
    { label: t("cm_row_above"), run: op("rowAbove") }, { label: t("cm_row_below"), run: op("rowBelow") },
    { label: t("cm_col_left"), run: op("colLeft") }, { label: t("cm_col_right"), run: op("colRight") },
    "-",
    { label: t("cm_row_delete"), icon: "trash", run: op("delRow") }, { label: t("cm_col_delete"), icon: "trash", run: op("delCol") },
    { label: t("cm_table_delete"), icon: "trash", danger: true, run: op("delTable") },
  ];
}
function clip(doc, cmd) {
  if (execClip(cmd)) return;
  const text = doc.selectionText(); if (!text) return;
  copyText(text).then(() => { if (cmd === "cut") doc.exec("delete"); });
}
async function pasteRich(doc) {
  const data = await readClipboard();
  if (!data) { toast(t("paste_blocked")); return; }
  if (data.html) {
    try { const r = await api("convert", { html: data.html, to: "html" }); doc.restore(); doc.exec("insertHTML", r.html); return; }
    catch { /* plain text below */ }
  }
  if (data.image) {
    if (data.image.size > 15 * 1048576) { toast(t("image_too_big"), { kind: "error" }); return; }
    const url = await blobToDataUrl(data.image); doc.restore(); doc.exec("insertImage", url); return;
  }
  if (data.text) { doc.restore(); doc.exec("insertText", data.text); }
}
async function pastePlain(doc) {
  let text = "";
  try { text = await navigator.clipboard.readText(); } catch { toast(t("paste_blocked")); return; }
  if (text) { doc.restore(); doc.exec("insertText", text); }
}
function selectAllIn(doc) {
  const r = document.createRange(); r.selectNodeContents(doc.page); setSelection(r); doc.saveRange(); doc.refreshState();
}

export async function docContextMenu(doc, e, hooks = {}) {
  e.preventDefault();
  const at = { x: e.clientX, y: e.clientY };
  const target = asElement(e.target);
  const page = doc.page;
  const inPage = !!target && page.contains(target);
  const img = inPage ? target.closest("img") : null;
  const link = inPage ? target.closest("a[href]") : null;
  const cell = inPage ? target.closest("td, th") : null;
  const sel = window.getSelection();
  if (img) selectNode(img);
  else if (!inPage && !(sel.rangeCount && page.contains(sel.anchorNode))) { const r = document.createRange(); r.selectNodeContents(page); r.collapse(false); setSelection(r); }
  doc.saveRange();
  const hasSel = !!sel.rangeCount && !sel.isCollapsed && page.contains(sel.anchorNode);
  const editable = page.isContentEditable;

  const items = [];
  if (hooks.aiItems && editable) items.push(...hooks.aiItems(hasSel));
  if (img) items.push(...imageItems(doc, img, hooks));
  if (link && !img) items.push(...linkItems(doc, link));
  if (cell && editable) items.push({ label: t("fmt_table"), icon: "table", sub: tableItems(doc, cell) }, "-");
  items.push(
    { label: t("cut"), icon: "cut", kbd: kbd("Ctrl+X"), disabled: !hasSel || !editable, run: () => clip(doc, "cut") },
    { label: t("copy"), icon: "copy", kbd: kbd("Ctrl+C"), disabled: !hasSel, run: () => clip(doc, "copy") },
    { label: t("paste"), icon: "paste", kbd: kbd("Ctrl+V"), disabled: !editable, run: () => pasteRich(doc) },
    { label: t("cm_paste_plain"), kbd: kbd("Ctrl+Shift+V"), disabled: !editable, run: () => pastePlain(doc) },
    "-",
  );
  if (editable) items.push(
    { label: t("cm_format"), icon: "bold", sub: formatItems(doc) },
    { label: t("cm_paragraph"), icon: "alignLeft", sub: paragraphItems(doc) },
    { label: t("cm_insert"), icon: "plus", sub: insertItems(doc) },
    "-",
  );
  if (hooks.find) items.push({ label: t("find") + "…", icon: "search", kbd: kbd("Ctrl+F"), run: () => hooks.find() });
  if (hooks.webSearch) items.push({ label: t("ai_web_search_selection"), icon: "globe", disabled: !hasSel, run: () => hooks.webSearch(doc.selectionText()) });
  items.push({ label: t("select_all"), icon: "selectAll", kbd: kbd("Ctrl+A"), run: () => selectAllIn(doc) });

  const open = (prefix) => showMenu(bindAll([...prefix, ...items], () => doc.restore()), at, { cls: "doc-menu" });
  const word = hooks.spell && editable && inPage && !img ? wordAtPoint(e.clientX, e.clientY, page) : null;
  const usable = word && (!hasSel || sel.toString() === word.word);
  const bad = usable ? await Promise.race([checkWord(word.word), sleep(250).then(() => false)]) : false;
  if (!bad) { open([]); return; }
  const menu = open([{ header: t("spell_loading") }]);
  const list = await suggestions(word.word);
  if (!menu.isConnected) return;
  const replace = (s) => () => {
    if (!word.node.isConnected || word.node.data.slice(word.from, word.to) !== word.word) return;
    const r = document.createRange(); r.setStart(word.node, word.from); r.setEnd(word.node, word.to); setSelection(r);
    document.execCommand("insertText", false, s);
  };
  open([
    ...(list.length ? list.slice(0, 8).map((s) => ({ label: s, cls: "spell-sugg", run: replace(s) })) : [{ label: t("spell_no_suggestions"), disabled: true }]),
    { label: t("spell_add"), icon: "plus", run: () => addToDictionary(word.word).then(() => toast(t("spell_added", { word: word.word }), { kind: "ok", ms: 1600 })) },
    "-",
  ]);
}

/* ---------------------------------------------------------------- text fields */

export function isTextField(el) {
  if (!el) return false;
  if (el instanceof HTMLTextAreaElement) return true;
  if (el instanceof HTMLInputElement) return FIELD_TYPES.has((el.getAttribute("type") || "").toLowerCase());
  return el.isContentEditable;
}
export function fieldContextMenu(e, field) {
  e.preventDefault();
  const plain = field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement;
  const editable = plain ? !field.readOnly && !field.disabled : field.isContentEditable;
  const secret = plain && field.type === "password";
  let start = null, end = null, range = null, text = "";
  if (plain) {
    try { start = field.selectionStart; end = field.selectionEnd; } catch { /* number inputs have no selection */ }
    text = start != null && end > start ? field.value.slice(start, end) : "";
  } else {
    const s = window.getSelection();
    if (s.rangeCount && field.contains(s.anchorNode)) { range = s.getRangeAt(0).cloneRange(); text = s.toString(); }
  }
  const back = () => {
    field.focus({ preventScroll: true });
    if (plain && start != null) { try { field.setSelectionRange(start, end); } catch { /* not selectable */ } }
    else if (range) setSelection(range);
  };
  const has = !!text;
  const items = [];
  if (editable) items.push(
    { label: t("undo"), icon: "undo", kbd: kbd("Ctrl+Z"), run: () => document.execCommand("undo") },
    { label: t("redo"), icon: "redo", kbd: kbd("Ctrl+Y"), run: () => document.execCommand("redo") },
    "-",
    { label: t("cut"), icon: "cut", kbd: kbd("Ctrl+X"), disabled: !has || secret, run: () => { if (!execClip("cut")) copyText(text).then(() => document.execCommand("delete")); } },
  );
  items.push({ label: t("copy"), icon: "copy", kbd: kbd("Ctrl+C"), disabled: !has || secret, run: () => { if (!execClip("copy")) copyText(text); } });
  if (editable) items.push({ label: t("paste"), icon: "paste", kbd: kbd("Ctrl+V"), run: async () => {
    let s = "";
    try { s = await navigator.clipboard.readText(); } catch { toast(t("paste_blocked")); return; }
    if (!s) return;
    back();
    document.execCommand("insertText", false, field instanceof HTMLInputElement ? s.replace(/\r?\n/g, " ") : s);
  } });
  items.push("-", { label: t("select_all"), icon: "selectAll", kbd: kbd("Ctrl+A"), run: () => { if (plain) field.select(); else { const r = document.createRange(); r.selectNodeContents(field); setSelection(r); } } });
  showMenu(bindAll(items, back), { x: e.clientX, y: e.clientY });
}

/* ---------------------------------------------------------------- read-only views */

const READ_ROOTS = ".md-preview, .book-page, .mail-page, .nb-view, .json-tree, .csv-view, .font-view, .msg, .image-view, .media-view";

// Previews, books, notebooks, mail, AI answers: copy, links and pictures. Returns false when nothing applies.
export function viewContextMenu(e, hooks = {}) {
  const target = asElement(e.target);
  if (!target) return false;
  const sel = window.getSelection();
  const text = sel && !sel.isCollapsed ? sel.toString() : "";
  const link = target.closest("a[href]");
  const img = target.closest("img");
  const root = target.closest(READ_ROOTS);
  const message = target.closest(".msg");
  if (!text && !link && !img && !root) return false;
  const items = [];
  if (link) {
    const href = linkTarget(link);
    const url = link.dataset.url || (href.startsWith("#") ? "" : link.href);
    items.push(
      { label: t("cm_link_open"), icon: "external", run: () => link.click() },
      ...(url ? [{ label: t("cm_link_copy"), icon: "copy", run: () => copyText(url).then(() => done()) }] : []),
      "-",
    );
  }
  if (img && img.complete && img.naturalWidth) items.push(...imageSaveItems(img, hooks), "-");
  if (text) {
    items.push({ label: t("copy"), icon: "copy", kbd: kbd("Ctrl+C"), run: () => { if (!execClip("copy")) copyText(text); } });
    if (hooks.explain) items.push({ label: t("cm_ask_ai"), icon: "sparkle", run: () => hooks.explain(text) });
    if (hooks.webSearch) items.push({ label: t("ai_web_search_selection"), icon: "globe", run: () => hooks.webSearch(text) });
    items.push("-");
  }
  if (message && !text) {
    const body = message.querySelector(".msg-body");
    if (body?.innerText.trim()) items.push({ label: t("cm_copy_message"), icon: "copy", run: () => copyText(body.innerText.trim()).then(() => done()) }, "-");
  }
  const scope = root && !root.matches(".image-view, .media-view") ? root : null;
  if (scope) {
    items.push({ label: t("select_all"), icon: "selectAll", kbd: kbd("Ctrl+A"), run: () => { const r = document.createRange(); r.selectNodeContents(scope); setSelection(r); } });
    if (!message) items.push({ label: t("cm_copy_all"), icon: "copy", run: () => copyText(scope.innerText.trim()).then(() => done()) });
  }
  while (items.length && items[items.length - 1] === "-") items.pop();
  if (!items.length) return false;
  e.preventDefault();
  showMenu(items, { x: e.clientX, y: e.clientY });
  return true;
}
