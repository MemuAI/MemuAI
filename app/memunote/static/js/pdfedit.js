

import { api, rawUrl, esc, icon, ICONS, t, toast, debounce, showMenu, modal, prompt, API, copyText, kbd } from "./core.js";

const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
Object.assign(ICONS, {
  pen: P('<path d="m4 20 4-1L19 8a2.1 2.1 0 0 0-3-3L5 16z"/><path d="m14 7 3 3"/>'),
  cursor: P('<path d="m5 3 14 7-6 2-3 6z"/>'),
  textT: P('<path d="M5 6V4h14v2M12 4v16M9 20h6"/>'),
  marker: P('<path d="m9 15 7-7 3 3-7 7H9z"/><path d="M4 21h9M9 15l-2 2v2h2"/>'),
  ink: P('<path d="M3 17c3-6 5-9 7-9s1 7 3 7 3-5 5-7 3 0 3 0"/>'),
  square: P('<rect x="4" y="5" width="16" height="14" rx="1.5"/>'),
  circle: P('<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>'),
  lineT: P('<path d="M5 19 19 5"/>'),
  arrowT: P('<path d="M5 19 19 5M10 5h9v9"/>'),
  whiteout: P('<rect x="4" y="6" width="16" height="12" rx="1.5"/><path d="M7 9h10M7 12h10M7 15h6" opacity=".35"/>'),
  rotL: P('<path d="M4 4v5h5"/><path d="M4.5 9A8 8 0 1 1 6 16.5"/>'),
  rotR: P('<path d="M20 4v5h-5"/><path d="M19.5 9A8 8 0 1 0 18 16.5"/>'),
  trash: P('<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>'),
  pageAdd: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>'),
  form: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h4M7 14h10M14 9h3"/>'),
  imageAdd: P('<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-8 8"/>'),
  signature: P('<path d="M3 17c2.5 0 3.5-9 6-9 2 0-1 9 1.5 9 1.6 0 2.4-4 4-4 1.2 0 1 2.5 2.5 2.5"/><path d="M3 21h18"/>'),
  stamp: P('<path d="M9 4h6l-1 7h-4z"/><path d="M5 14h14v3H5zM7 20h10"/>'),
  thumbs: P('<rect x="3" y="3" width="7" height="8" rx="1"/><rect x="3" y="13" width="7" height="8" rx="1"/><path d="M14 5h7M14 9h5M14 15h7M14 19h5"/>'),
  merge: P('<path d="M8 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h3M16 3h3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-3M12 8v8M8.5 12H15.5"/>'),
  numbers: P('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M10 17h4M12 17v-4"/>'),
  watermark: P('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 16 8-8" opacity=".55"/><path d="M8 8h3M13 16h3" opacity=".55"/>'),
  extract: P('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 18v-6M9 15l3 3 3-3"/>'),
  dup: P('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
});

const COLORS = ["#e5484d", "#f76b15", "#ffc53d", "#30a46c", "#0090ff", "#8e4ec6", "#1c2024", "#ffffff"];
const HIGHLIGHTS = ["#ffe066", "#a6f4c5", "#a5d8ff", "#ffc9de", "#ffd8a8"];
const TOOLS = [["select", "cursor"], ["text", "textT"], ["highlight", "marker"], ["ink", "ink"], ["rect", "square"],
  ["ellipse", "circle"], ["line", "lineT"], ["arrow", "arrowT"], ["whiteout", "whiteout"]];
const ASCENT = 0.86, LINE_H = 1.22;
const BOX_TYPES = new Set(["image", "stamp", "rect", "ellipse", "highlight", "whiteout"]);
const STAMPS = [["approved", "#2e7d32"], ["checked", "#2e7d32"], ["paid", "#2e7d32"], ["draft", "#1565c0"], ["copy", "#1565c0"], ["confidential", "#c62828"], ["rejected", "#c62828"]];
const A4 = [595.28, 841.89];

let _pdfjs = null, _pdflib = null;
export async function pdfjs() {
  if (_pdfjs) return _pdfjs;

  await import("../vendor/pdfjs/polyfills.mjs?v=r34");
  const mod = await import("../vendor/pdfjs/pdf.min.mjs?v=6.4.299");
  mod.GlobalWorkerOptions.workerSrc = new URL("../vendor/pdfjs/pdf.worker.compat.mjs?v=r34", import.meta.url).href;
  _pdfjs = mod;
  return mod;
}
export async function pdflib() { return (_pdflib ||= await import("../vendor/pdflib/pdflib.js")); }

export function viewport(viewBox, scale, rotation) {
  const [x0, y0, x1, y1] = viewBox;
  const cx = (x1 + x0) / 2, cy = (y1 + y0) / 2;
  let A, B, C, D;
  switch (((rotation % 360) + 360) % 360) {
    case 180: A = -1; B = 0; C = 0; D = 1; break;
    case 90: A = 0; B = 1; C = 1; D = 0; break;
    case 270: A = 0; B = -1; C = -1; D = 0; break;
    default: A = 1; B = 0; C = 0; D = -1;
  }
  let ox, oy, width, height;
  if (A === 0) { ox = Math.abs(cy - y0) * scale; oy = Math.abs(cx - x0) * scale; width = Math.abs(y1 - y0) * scale; height = Math.abs(x1 - x0) * scale; }
  else { ox = Math.abs(cx - x0) * scale; oy = Math.abs(cy - y0) * scale; width = Math.abs(x1 - x0) * scale; height = Math.abs(y1 - y0) * scale; }
  const m = [A * scale, B * scale, C * scale, D * scale, ox - A * scale * cx - C * scale * cy, oy - B * scale * cx - D * scale * cy];
  const det = m[0] * m[3] - m[1] * m[2];
  return {
    width, height, rotation, scale,
    toView: (x, y) => [x * m[0] + y * m[2] + m[4], x * m[1] + y * m[3] + m[5]],
    toPdf: (x, y) => [((x - m[4]) * m[3] - (y - m[5]) * m[2]) / det, ((y - m[5]) * m[0] - (x - m[4]) * m[1]) / det],
  };
}
function hexRgb(hex) { const m = /^#?([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(hex || "") || [0, "00", "00", "00"]; return [parseInt(m[1], 16) / 255, parseInt(m[2], 16) / 255, parseInt(m[3], 16) / 255]; }
const clone = (o) => JSON.parse(JSON.stringify(o));
const uid = () => Math.random().toString(36).slice(2, 9);
function rectOf(a, b) { return { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), w: Math.abs(a[0] - b[0]), h: Math.abs(a[1] - b[1]) }; }

export function drawObject(L, page, o, font, images) {
  const col = (c) => L.rgb(...hexRgb(c));
  const w = Math.max(0.3, o.width || 2);
  switch (o.type) {
    case "highlight":
      page.drawRectangle({ x: o.x, y: o.y, width: o.w, height: o.h, color: col(o.color || "#ffe066"), opacity: 0.4, blendMode: L.BlendMode.Multiply });
      break;
    case "whiteout":
      page.drawRectangle({ x: o.x, y: o.y, width: o.w, height: o.h, color: col(o.color || "#ffffff") });
      break;
    case "rect":
      page.drawRectangle({ x: o.x, y: o.y, width: o.w, height: o.h, borderColor: col(o.color), borderWidth: w });
      break;
    case "ellipse":
      page.drawEllipse({ x: o.x + o.w / 2, y: o.y + o.h / 2, xScale: o.w / 2, yScale: o.h / 2, borderColor: col(o.color), borderWidth: w });
      break;
    case "line": case "arrow": {
      const [x1, y1] = o.p1, [x2, y2] = o.p2;
      page.drawLine({ start: { x: x1, y: y1 }, end: { x: x2, y: y2 }, thickness: w, color: col(o.color), lineCap: L.LineCapStyle.Round });
      if (o.type === "arrow") {
        const ang = Math.atan2(y2 - y1, x2 - x1), len = Math.max(8, w * 4.5);
        for (const d of [Math.PI * 0.85, -Math.PI * 0.85]) {
          page.drawLine({ start: { x: x2, y: y2 }, end: { x: x2 + len * Math.cos(ang + d), y: y2 + len * Math.sin(ang + d) }, thickness: w, color: col(o.color), lineCap: L.LineCapStyle.Round });
        }
      }
      break;
    }
    case "ink": {
      if (!o.points?.length) break;
      const d = o.points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${(-y).toFixed(2)}`).join(" ");
      page.drawSvgPath(o.points.length === 1 ? d + ` L${(o.points[0][0] + 0.01).toFixed(2)} ${(-o.points[0][1]).toFixed(2)}` : d,
        { x: 0, y: 0, borderColor: col(o.color), borderWidth: w, borderLineCap: L.LineCapStyle.Round });
      break;
    }
    case "text": {
      const size = o.size || 14, th = (o.angle || 0) * Math.PI / 180;
      const down = [Math.sin(th), -Math.cos(th)];
      String(o.text || "").split("\n").forEach((line, i) => {
        if (!line) return;
        const k = ASCENT * size + i * LINE_H * size;
        page.drawText(encodable(font, line), { x: o.x + down[0] * k, y: o.y + down[1] * k, size, font, color: col(o.color), rotate: L.degrees(o.angle || 0), opacity: o.opacity ?? 1 });
      });
      break;
    }
    case "image": {
      const img = images?.[o.img];
      if (img) page.drawImage(img, { x: o.x, y: o.y, width: o.w, height: o.h, opacity: o.opacity ?? 1 });
      break;
    }
    case "stamp": {
      const c = col(o.color || "#c62828"), bw = Math.max(1.2, Math.min(o.w, o.h) * 0.06);
      page.drawRectangle({ x: o.x, y: o.y, width: o.w, height: o.h, borderColor: c, borderWidth: bw, borderOpacity: 0.9 });
      const text = encodable(font, String(o.text || ""));
      let size = o.h * 0.5;
      try { const w = font.widthOfTextAtSize(text, size); if (w > o.w * 0.86) size *= (o.w * 0.86) / w; } catch {  }
      let tw = text.length * size * 0.56; try { tw = font.widthOfTextAtSize(text, size); } catch {  }
      page.drawText(text, { x: o.x + (o.w - tw) / 2, y: o.y + (o.h - size * 0.7) / 2, size, font, color: c, opacity: 0.9 });
      break;
    }
  }
}

// Without a Unicode font (the standard Helvetica covers only WinAnsi) Cyrillic is transliterated
// and any other character the font can't encode becomes "?", instead of failing the whole save.
const TRANSLIT = { а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh", з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o", п: "p",
  р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts", ч: "ch", ш: "sh", щ: "shch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya", і: "i", ї: "yi", є: "ye", ґ: "g" };
function canEncode(font, s) { try { font.encodeText(s); return true; } catch { return false; } }
export function encodable(font, s) {
  if (!font || !s || canEncode(font, s)) return s;
  const tr = [...s].map((ch) => { const lo = ch.toLowerCase(), v = TRANSLIT[lo]; if (v == null) return ch; return ch === lo ? v : v.charAt(0).toUpperCase() + (s === s.toUpperCase() ? v.slice(1).toUpperCase() : v.slice(1)); }).join("");
  if (canEncode(font, tr)) return tr;
  return [...tr].map((ch) => (canEncode(font, ch) ? ch : "?")).join("");
}

function dataBytes(url) {
  const b64 = String(url).split(",")[1] || "";
  const bin = atob(b64); const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export async function applyEdits(bytes, model, fontBytes) {
  const L = await pdflib();
  const doc = await L.PDFDocument.load(bytes, { updateMetadata: false });
  doc.registerFontkit(L.fontkit);
  let font = null;
  const needsFont = model.pages.some((p) => p.objs.some((o) => o.type === "text" || o.type === "stamp")) || Object.keys(model.fields || {}).length;
  if (needsFont) {
    if (fontBytes) { try { font = await doc.embedFont(fontBytes, { subset: true }); } catch (e) { console.warn("font", e); } }
    if (!font) font = await doc.embedFont(L.StandardFonts.Helvetica);
  }
  const orig = doc.getPages();

  const names = Object.keys(model.fields || {});
  if (names.length) {
    const form = doc.getForm();
    for (const name of names) {
      const value = model.fields[name];
      let field; try { field = form.getField(name); } catch { continue; }
      try {
        if (field instanceof L.PDFTextField) field.setText(String(value ?? ""));
        else if (field instanceof L.PDFCheckBox) value ? field.check() : field.uncheck();
        else if (field instanceof L.PDFDropdown || field instanceof L.PDFOptionList) { if (value) field.select(String(value)); else field.clear(); }
        else if (field instanceof L.PDFRadioGroup) { if (value) field.select(String(value)); else field.clear(); }
      } catch (e) { console.warn("field", name, e); }
    }
    try { form.updateFieldAppearances(font); } catch (e) { console.warn("appearances", e); }
  }
  // Images are embedded once and shared by every page that shows them.
  const images = {};
  for (const p of model.pages) for (const o of p.objs) {
    if (o.type !== "image" || images[o.img] || !model.images?.[o.img]) continue;
    const url = model.images[o.img];
    try { images[o.img] = /^data:image\/jpe?g/i.test(url) ? await doc.embedJpg(dataBytes(url)) : await doc.embedPng(dataBytes(url)); }
    catch (e) { console.warn("image", e); }
  }
  for (const p of model.pages) if (p.src && !p.ext) {
    const page = orig[p.src - 1];
    for (const o of p.objs) drawObject(L, page, o, font, images);
    if (p.rot) page.setRotation(L.degrees(((page.getRotation().angle + p.rot) % 360 + 360) % 360));
  }
  const order = model.pages.map((p) => (p.ext ? -1 : p.src));
  const unchanged = order.length === orig.length && order.every((s, i) => s === i + 1);
  if (!unchanged) {
    // Pages inserted from other PDFs are copied in one batch per source document.
    const extra = {};
    for (const p of model.pages) if (p.ext && model.extras?.[p.ext]) (extra[p.ext] ||= new Set()).add(p.src - 1);
    const copied = {};
    for (const [key, set] of Object.entries(extra)) {
      const src = await L.PDFDocument.load(model.extras[key], { updateMetadata: false, ignoreEncryption: true });
      const idx = [...set];
      const pages = await doc.copyPages(src, idx);
      copied[key] = Object.fromEntries(idx.map((n, k) => [n, pages[k]]));
    }
    for (let i = doc.getPageCount() - 1; i >= 0; i--) doc.removePage(i);
    const used = new Set(), usedExt = new Set();
    model.pages.forEach((p, i) => {
      if (p.ext) {
        const page = copied[p.ext]?.[p.src - 1];
        const key = p.ext + ":" + p.src;
        if (page && !usedExt.has(key)) {
          usedExt.add(key); doc.insertPage(doc.getPageCount(), page);
          for (const o of p.objs) drawObject(L, page, o, font, images);
          if (p.rot) page.setRotation(L.degrees(((page.getRotation().angle + p.rot) % 360 + 360) % 360));
          return;
        }
      } else if (p.src && !used.has(p.src)) { used.add(p.src); doc.insertPage(doc.getPageCount(), orig[p.src - 1]); return; }
      const size = p.size || A4;
      const page = doc.insertPage(doc.getPageCount(), size);
      for (const o of p.objs) drawObject(L, page, o, font, images);
      if (p.rot) page.setRotation(L.degrees(((p.rot % 360) + 360) % 360));
    });
  }
  return doc.save();
}

// A new PDF with some pages of an existing one (bytes already edited) — "Extract pages".
export async function extractPages(bytes, indexes) {
  const L = await pdflib();
  const src = await L.PDFDocument.load(bytes, { updateMetadata: false });
  const out = await L.PDFDocument.create();
  const pages = await out.copyPages(src, indexes);
  for (const page of pages) out.addPage(page);
  return out.save();
}

// "1-3, 5, 8-" → zero-based page indexes within 1..total (order kept, duplicates dropped).
export function parseRanges(text, total) {
  const out = [];
  for (const part of String(text || "").replace(/\s*([-–])\s*/g, "$1").split(/[,;\s]+/).filter(Boolean)) {
    const m = /^(\d*)[-–](\d*)$/.exec(part) || /^(\d+)$/.exec(part);
    if (!m || (m.length === 3 && !m[1] && !m[2])) return null;
    let a, b;
    if (m.length === 2) a = b = +m[1];
    else { a = m[1] ? +m[1] : 1; b = m[2] ? +m[2] : total; }
    if (!a || !b || a > total || b > total) return null;
    const step = a <= b ? 1 : -1;
    for (let n = a; step > 0 ? n <= b : n >= b; n += step) if (!out.includes(n - 1)) out.push(n - 1);
  }
  return out.length ? out : null;
}

export async function readFields(bytes) {
  const L = await pdflib();
  const doc = await L.PDFDocument.load(bytes, { updateMetadata: false });
  let form; try { form = doc.getForm(); } catch { return []; }
  const out = [];
  for (const f of form.getFields()) {
    const name = f.getName();
    try {
      if (f instanceof L.PDFTextField) out.push({ name, type: "text", value: f.getText() || "", multiline: f.isMultiline(), readOnly: f.isReadOnly() });
      else if (f instanceof L.PDFCheckBox) out.push({ name, type: "check", value: f.isChecked(), readOnly: f.isReadOnly() });
      else if (f instanceof L.PDFDropdown) out.push({ name, type: "select", value: f.getSelected()[0] || "", options: f.getOptions(), readOnly: f.isReadOnly() });
      else if (f instanceof L.PDFOptionList) out.push({ name, type: "select", value: f.getSelected()[0] || "", options: f.getOptions(), readOnly: f.isReadOnly() });
      else if (f instanceof L.PDFRadioGroup) out.push({ name, type: "radio", value: f.getSelected() || "", options: f.getOptions(), readOnly: f.isReadOnly() });
    } catch {  }
  }
  return out;
}

let _font = null;
async function fontBytes() {
  if (_font !== null) return _font;
  try { const r = await fetch(`/api/font?t=${encodeURIComponent(API.token)}`, { headers: { "X-MN-Token": API.token } }); _font = r.ok ? await r.arrayBuffer() : false; }
  catch { _font = false; }
  return _font;
}

export class PdfView {
  constructor(host, tab) {
    this.host = host; this.tab = tab; this.scale = 1.2; this.rendered = new Map();
    this.editing = false; this.tool = "select"; this.color = COLORS[0]; this.hlColor = HIGHLIGHTS[0]; this.width = 2; this.fontSize = 14;
    this.model = null; this.undoStack = []; this.redoStack = []; this.selected = null; this.fields = null; this.fieldValues = {};
    this.images = {}; this.extras = {}; this.clip = null;
    this.el = document.createElement("div"); this.el.className = "pdf-view"; this.el.tabIndex = -1;
    this.el.innerHTML = `<div class="pdf-bar">
      <button class="icon-btn" data-a="prev" title="${esc(t("pdf_prev"))}">${icon("up")}</button>
      <button class="icon-btn" data-a="next" title="${esc(t("pdf_next"))}">${icon("down")}</button>
      <input class="input sm pdf-page" value="1" aria-label="${esc(t("pdf_page"))}"><span class="muted small pdf-total"></span>
      <span class="sep"></span>
      <button class="icon-btn" data-a="out" title="${esc(t("zoom_out"))}">${icon("zoomOut")}</button>
      <span class="small pdf-zoom">120%</span>
      <button class="icon-btn" data-a="in" title="${esc(t("zoom_in"))}">${icon("zoomIn")}</button>
      <button class="btn ghost sm" data-a="fit">${esc(t("pdf_fit"))}</button>
      <button class="icon-btn" data-a="thumbs" title="${esc(t("pdf_thumbs"))}" aria-label="${esc(t("pdf_thumbs"))}">${icon("thumbs")}</button>
      <span class="spacer"></span>
      <button class="btn ghost sm" data-a="export">${icon("export")}${esc(t("pdf_export"))}${icon("chevron", "small")}</button>
      <button class="btn ghost sm ai-only" data-a="ask">${icon("sparkle")}${esc(t("pdf_ask"))}</button>
      <button class="btn sm pdf-edit-toggle" data-a="edit">${icon("pen")}${esc(t("pdf_edit"))}</button>
    </div><div class="pdf-edit-bar" hidden></div><div class="pdf-body"><aside class="pdf-thumbs" hidden></aside><div class="pdf-scroll"><div class="pdf-pages"></div><div class="pdf-loading">${esc(t("loading"))}</div></div><aside class="pdf-fields" hidden></aside></div>`;
    host.append(this.el);
    this.scroll = this.el.querySelector(".pdf-scroll"); this.wrap = this.el.querySelector(".pdf-pages");
    this.el.querySelector(".pdf-bar").addEventListener("click", (e) => { const b = e.target.closest("[data-a]"); if (b) this.action(b.dataset.a, b); });
    this.el.querySelector(".pdf-page").addEventListener("change", (e) => this.goto(+e.target.value));
    this.scroll.addEventListener("scroll", debounce(() => this.updatePage(), 60), { passive: true });
    this.scroll.addEventListener("wheel", (e) => { if (e.ctrlKey) { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); } }, { passive: false });
    this.el.addEventListener("keydown", (e) => this.keys(e));
    this.scroll.addEventListener("contextmenu", (e) => this.contextMenu(e));
    this.bindThumbs();
    this.load();
  }
  get dirty() { return this.savedSnapshot != null ? JSON.stringify(this.snapshot()) !== this.savedSnapshot : this.undoStack.length > 0 && this.changed; }

  async load(data) {
    try {
      const lib = await pdfjs();
      const task = lib.getDocument({ ...(data ? { data } : { url: rawUrl(this.tab.path) }), isEvalSupported: false, enableXfa: false });
      task.onPassword = async (update, reason) => {
        const pw = await modal({ title: t("pdf_password"), body: `<p>${esc(t(reason === 2 ? "pdf_password_wrong" : "pdf_password_text"))}</p><input class="input" type="password" id="pdf-pw" autofocus>`,
          buttons: [{ label: t("cancel"), value: null }, { label: t("ok"), primary: true, value: (b) => b.querySelector("#pdf-pw").value }] });
        if (pw === null) { task.destroy(); return; }
        this.password = pw; update(pw);
      };
      const doc = await task.promise;
      this.doc?.destroy?.();
      this.doc = doc;
      this.info = [];
      for (let n = 1; n <= doc.numPages; n++) this.info.push(null);
      const first = await doc.getPage(1);
      this.info[0] = { view: first.view, rotate: first.rotate };
      this.el.querySelector(".pdf-loading")?.remove();
      if (!this.model) this.resetModel();
      this.el.querySelector(".pdf-total").textContent = "/ " + this.model.pages.length;
      this.layout();
      if (localStorage.getItem("mn_pdf_thumbs") === "1") this.toggleThumbs(true);
      this.onLoaded?.();
    } catch (e) {
      const box = this.el.querySelector(".pdf-loading");
      if (box) box.textContent = t("pdf_failed") + ": " + (e.message || e);
    }
  }
  resetModel() {
    this.model = { pages: Array.from({ length: this.doc.numPages }, (_, i) => ({ id: uid(), src: i + 1, rot: 0, objs: [] })) };
    this.undoStack = []; this.redoStack = []; this.changed = false; this.fieldValues = {}; this.savedSnapshot = null;
  }
  async pageInfo(src) {
    if (!src) return null;
    if (!this.info[src - 1]) { const p = await this.doc.getPage(src); this.info[src - 1] = { view: p.view, rotate: p.rotate }; }
    return this.info[src - 1];
  }

  vpOf(mp, info) {
    if (!mp.src) { const s = mp.size || A4; return viewport([0, 0, s[0], s[1]], this.scale, mp.rot || 0); }
    info = info || this.infoOf(mp) || this.info[0];
    return viewport(info.view, this.scale, ((info.rotate || 0) + (mp.rot || 0)) % 360);
  }

  docOf(mp) { return mp.ext ? this.extras[mp.ext]?.doc : this.doc; }
  infoOf(mp) {
    if (!mp?.src) return null;
    const list = mp.ext ? this.extras[mp.ext]?.info : this.info;
    return list?.[mp.src - 1] || null;
  }
  // Page size in PDF units and the origin of its box (unrotated).
  pageBox(mp) {
    const info = this.infoOf(mp) || (mp.src ? this.info[0] : null);
    if (!info) { const s = mp.size || A4; return { x0: 0, y0: 0, w: s[0], h: s[1] }; }
    const [x0, y0, x1, y1] = info.view;
    return { x0: Math.min(x0, x1), y0: Math.min(y0, y1), w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) };
  }
  layout() {
    const keep = this.scroll.scrollTop;
    this.wrap.innerHTML = ""; this.rendered.clear();
    this.observer?.disconnect();
    this.observer = new IntersectionObserver((entries) => {
      for (const en of entries) { const i = +en.target.dataset.i; if (en.isIntersecting) this.renderPage(i, en.target); else this.unrender(i, en.target); }
    }, { root: this.scroll, rootMargin: "800px 0px" });
    this.model.pages.forEach((mp, i) => {
      const vp = this.vpOf(mp);
      const p = document.createElement("div"); p.className = "pdf-page-box" + (mp.src ? "" : " blank"); p.dataset.i = i; p.dataset.page = i + 1;
      p.style.width = Math.round(vp.width) + "px"; p.style.height = Math.round(vp.height) + "px";
      if (this.editing) p.append(this.pageTools(i));
      this.wrap.append(p); this.observer.observe(p);
    });
    this.el.querySelector(".pdf-zoom").textContent = Math.round(this.scale * 100) + "%";
    this.el.querySelector(".pdf-total").textContent = "/ " + this.model.pages.length;
    this.scroll.scrollTop = keep;
    this.renderThumbs();
  }
  pageTools(i) {
    const n = this.model.pages.length;
    const bar = document.createElement("div"); bar.className = "pdf-page-tools";
    const B = (a, ic, label, dis) => `<button class="icon-btn sm" data-pa="${a}" title="${esc(t(label))}" aria-label="${esc(t(label))}" ${dis ? "disabled" : ""}>${icon(ic)}</button>`;
    bar.innerHTML = `<span class="small muted">${esc(t("pdf_page"))} ${i + 1}</span><span class="spacer"></span>${B("rotL", "rotL", "pdf_rotate_left")}${B("rotR", "rotR", "pdf_rotate_right")}${B("up", "up", "pdf_move_up", i === 0)}${B("down", "down", "pdf_move_down", i === n - 1)}${B("add", "pageAdd", "pdf_insert_blank")}${B("del", "trash", "pdf_delete_page", n <= 1)}`;
    bar.addEventListener("click", (e) => { const b = e.target.closest("[data-pa]"); if (b) this.pageOp(i, b.dataset.pa); });
    return bar;
  }
  async renderPage(i, box) {
    if (this.rendered.has(i)) return;
    this.rendered.set(i, true);
    const mp = this.model.pages[i]; if (!mp) return;
    const tools = box.querySelector(".pdf-page-tools");
    box.querySelectorAll(":scope > :not(.pdf-page-tools)").forEach((x) => x.remove());
    if (mp.src) {
      const doc = this.docOf(mp); if (!doc) return;
      const page = await doc.getPage(mp.src);
      if (!this.rendered.has(i) || this.model.pages[i] !== mp) return;
      (mp.ext ? this.extras[mp.ext].info : this.info)[mp.src - 1] = { view: page.view, rotate: page.rotate };
      const vp = page.getViewport({ scale: this.scale, rotation: (page.rotate + (mp.rot || 0)) % 360 });
      box.style.width = Math.round(vp.width) + "px"; box.style.height = Math.round(vp.height) + "px";
      const ratio = Math.min(window.devicePixelRatio || 1, 2.5);
      const canvas = document.createElement("canvas");
      canvas.width = Math.floor(vp.width * ratio); canvas.height = Math.floor(vp.height * ratio);
      canvas.style.width = Math.floor(vp.width) + "px"; canvas.style.height = Math.floor(vp.height) + "px";
      box.prepend(canvas);
      const task = page.render({ canvasContext: canvas.getContext("2d"), viewport: vp, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null });
      try { await task.promise; } catch { return; }
      try {
        const lib = await pdfjs();
        const layer = document.createElement("div"); layer.className = "textLayer"; canvas.after(layer);
        layer.style.setProperty("--scale-factor", this.scale);
        const tl = new lib.TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport: vp });
        await tl.render();
      } catch (e) {  }
    }
    this.drawOverlay(i, box);
    if (tools) box.append(tools);
  }
  unrender(i, box) {
    if (!this.rendered.has(i)) return;
    if (Math.abs(i + 1 - this.current()) <= 3) return;
    this.rendered.delete(i);
    box.querySelectorAll(":scope > :not(.pdf-page-tools)").forEach((x) => x.remove());
  }
  rerender(i) {
    const box = this.wrap.children[i]; if (!box) return;
    this.rendered.delete(i); this.renderPage(i, box);
  }

  drawOverlay(i, box) {
    box.querySelector(".pdf-ann")?.remove();
    const mp = this.model.pages[i]; if (!mp) return;
    const vp = this.vpOf(mp);
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "pdf-ann tool-" + (this.editing ? this.tool : "view"));
    svg.setAttribute("width", vp.width); svg.setAttribute("height", vp.height);
    const parts = [];
    for (const o of mp.objs) if (!o._hidden) parts.push(this.objSvg(o, vp));
    svg.innerHTML = parts.join("");
    box.append(svg);
    if (this.editing) this.bindOverlay(i, box, svg);
  }
  objSvg(o, vp) {
    const shape = this.objShape(o, vp);
    return this.editing && this.selected?.id === o.id && BOX_TYPES.has(o.type) ? shape + this.handleSvg(o, vp) : shape;
  }
  objShape(o, vp) {
    const sel = this.selected && this.selected.id === o.id ? " selected" : "";
    const sw = Math.max(0.6, (o.width || 2) * vp.scale);
    const box = (o) => { const a = vp.toView(o.x, o.y), b = vp.toView(o.x + o.w, o.y + o.h); return rectOf(a, b); };
    const attrs = `data-id="${o.id}" class="obj${sel}"`;
    switch (o.type) {
      case "highlight": { const r = box(o); return `<rect ${attrs} x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="${esc(o.color)}" fill-opacity=".45" style="mix-blend-mode:multiply"/>`; }
      case "whiteout": { const r = box(o); return `<rect ${attrs} x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="${esc(o.color || "#ffffff")}"/>`; }
      case "rect": { const r = box(o); return `<rect ${attrs} x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="transparent" stroke="${esc(o.color)}" stroke-width="${sw}"/>`; }
      case "ellipse": { const r = box(o); return `<ellipse ${attrs} cx="${r.x + r.w / 2}" cy="${r.y + r.h / 2}" rx="${r.w / 2}" ry="${r.h / 2}" fill="transparent" stroke="${esc(o.color)}" stroke-width="${sw}"/>`; }
      case "line": case "arrow": {
        const a = vp.toView(...o.p1), b = vp.toView(...o.p2);
        let head = "";
        if (o.type === "arrow") {
          const ang = Math.atan2(b[1] - a[1], b[0] - a[0]), len = Math.max(8, (o.width || 2) * 4.5) * vp.scale;
          head = [Math.PI * 0.85, -Math.PI * 0.85].map((d) => `M${b[0]} ${b[1]} L${b[0] + len * Math.cos(ang + d)} ${b[1] + len * Math.sin(ang + d)}`).join(" ");
        }
        return `<path ${attrs} d="M${a[0]} ${a[1]} L${b[0]} ${b[1]} ${head}" fill="none" stroke="${esc(o.color)}" stroke-width="${sw}" stroke-linecap="round"/>`;
      }
      case "ink": {
        const d = o.points.map((p, k) => { const v = vp.toView(...p); return (k ? "L" : "M") + v[0].toFixed(1) + " " + v[1].toFixed(1); }).join(" ");
        return `<path ${attrs} d="${d}${o.points.length === 1 ? " l0.1 0" : ""}" fill="none" stroke="${esc(o.color)}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/>`;
      }
      case "image": {
        const r = box(o); const url = this.images[o.img] || "";
        return `<g ${attrs}><image href="${esc(url)}" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" preserveAspectRatio="none" opacity="${o.opacity ?? 1}"/><rect class="hit" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" fill="transparent"/></g>`;
      }
      case "stamp": {
        const r = box(o); const bw = Math.max(1.2, Math.min(o.w, o.h) * 0.06) * vp.scale;
        const size = Math.min(r.h * 0.5, (r.w * 0.86) / Math.max(1, String(o.text || "").length * 0.6));
        return `<g ${attrs}><rect x="${r.x + bw / 2}" y="${r.y + bw / 2}" width="${Math.max(1, r.w - bw)}" height="${Math.max(1, r.h - bw)}" rx="${bw}" fill="transparent" stroke="${esc(o.color)}" stroke-width="${bw}" stroke-opacity=".9"/><text x="${r.x + r.w / 2}" y="${r.y + r.h / 2}" text-anchor="middle" dominant-baseline="central" font-size="${size}" font-weight="700" fill="${esc(o.color)}" fill-opacity=".9" font-family="Arial, 'DejaVu Sans', 'Segoe UI', sans-serif">${esc(o.text)}</text></g>`;
      }
      case "text": {
        const [ax, ay] = vp.toView(o.x, o.y);
        const size = (o.size || 14) * vp.scale, rot = (vp.rotation - (o.angle || 0) + 360) % 360;
        const lines = String(o.text || "").split("\n");
        const tspans = lines.map((l, k) => `<tspan x="${ax}" y="${ay + ASCENT * size + k * LINE_H * size}">${esc(l) || " "}</tspan>`).join("");
        const wmax = Math.max(...lines.map((l) => l.length)) * size * 0.56, h = lines.length * LINE_H * size;
        return `<g ${attrs} transform="rotate(${rot} ${ax} ${ay})"><rect class="hit" x="${ax - 2}" y="${ay - 2}" width="${wmax + 4}" height="${h + 4}" fill="transparent"/><text font-size="${size}" fill="${esc(o.color)}" fill-opacity="${o.opacity ?? 1}" font-family="Arial, 'DejaVu Sans', 'Segoe UI', sans-serif">${tspans}</text></g>`;
      }
    }
    return "";
  }
  // The corner that looks bottom-right on screen, whatever the page rotation.
  handleCorner(o, vp) {
    const pts = [[o.x, o.y], [o.x + o.w, o.y], [o.x, o.y + o.h], [o.x + o.w, o.y + o.h]];
    const views = pts.map((p) => vp.toView(...p));
    let k = 0; views.forEach((v, j) => { if (v[0] + v[1] > views[k][0] + views[k][1]) k = j; });
    return { k, view: views[k], anchor: pts[3 - k] };
  }
  handleSvg(o, vp) {
    const { view } = this.handleCorner(o, vp);
    return `<rect class="handle" data-h="${o.id}" x="${view[0] - 6}" y="${view[1] - 6}" width="12" height="12" rx="3"/>`;
  }
  redraw(i) { const box = this.wrap.children[i]; if (box && this.rendered.has(i)) this.drawOverlay(i, box); }
  redrawAll() { this.model.pages.forEach((_, i) => this.redraw(i)); }

  bindOverlay(i, box, svg) {
    const mp = this.model.pages[i];
    const local = (e) => { const r = box.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const vp = () => this.vpOf(mp);
    svg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      this.el.focus({ preventScroll: true });
      const tool = this.tool;
      const objEl = e.target.closest(".obj");
      if (tool === "select") {
        const handle = e.target.closest(".handle");
        if (handle) {
          const o = mp.objs.find((x) => x.id === handle.dataset.h); if (!o) return;
          e.preventDefault(); svg.setPointerCapture(e.pointerId);
          const { anchor } = this.handleCorner(o, vp()); const ratio = o.w / Math.max(0.01, o.h);
          const snap = this.snapshot(); let moved = false;
          const move = (ev) => {
            const q = vp().toPdf(...local(ev));
            let dx = q[0] - anchor[0], dy = q[1] - anchor[1];
            if (o.type === "image" && !ev.shiftKey) { const w = Math.max(Math.abs(dx), Math.abs(dy) * ratio); dx = Math.sign(dx || 1) * w; dy = Math.sign(dy || 1) * (w / ratio); }
            const r = rectOf(anchor, [anchor[0] + dx, anchor[1] + dy]);
            if (r.w < 4 || r.h < 4) return;
            Object.assign(o, r); moved = true; this.redraw(i);
          };
          const up = () => { svg.removeEventListener("pointermove", move); if (moved) this.commit(snap); };
          svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up, { once: true });
          return;
        }
        if (!objEl) { this.select(null); return; }
        const o = mp.objs.find((x) => x.id === objEl.dataset.id); if (!o) return;
        this.select({ id: o.id, page: i });
        e.preventDefault(); svg.setPointerCapture(e.pointerId);
        const start = vp().toPdf(...local(e)); let moved = false; let snap = null;
        const move = (ev) => {
          const now = vp().toPdf(...local(ev)); const dx = now[0] - start[0], dy = now[1] - start[1];
          if (!moved && Math.hypot(dx, dy) * this.scale < 3) return;
          if (!moved) { moved = true; snap = this.snapshot(); }
          translate(o, dx - (o._dx || 0), dy - (o._dy || 0)); o._dx = dx; o._dy = dy;
          this.redraw(i);
        };
        const up = () => { svg.removeEventListener("pointermove", move); delete o._dx; delete o._dy; if (moved) this.commit(snap); };
        svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up, { once: true });
        return;
      }
      if (tool === "text") { e.preventDefault(); this.textEditor(i, box, local(e), null); return; }
      e.preventDefault(); svg.setPointerCapture(e.pointerId);
      const a = local(e); let b = a; const pts = [a];
      const NS = "http://www.w3.org/2000/svg";
      const ghost = document.createElementNS(NS, tool === "ink" ? "path" : tool === "ellipse" ? "ellipse" : tool === "line" || tool === "arrow" ? "line" : "rect");
      ghost.setAttribute("class", "ghost"); svg.append(ghost);
      const color = tool === "highlight" ? this.hlColor : tool === "whiteout" ? "#ffffff" : this.color;
      ghost.setAttribute("stroke", color === "#ffffff" ? "#999" : color); ghost.setAttribute("stroke-width", Math.max(1, this.width * this.scale));
      ghost.setAttribute("fill", tool === "highlight" ? color : tool === "whiteout" ? "#fff" : "none");
      if (tool === "highlight") ghost.setAttribute("fill-opacity", ".45");
      const paint = () => {
        const r = rectOf(a, b);
        if (tool === "ink") ghost.setAttribute("d", pts.map((p, k) => (k ? "L" : "M") + p[0] + " " + p[1]).join(" "));
        else if (tool === "ellipse") { ghost.setAttribute("cx", r.x + r.w / 2); ghost.setAttribute("cy", r.y + r.h / 2); ghost.setAttribute("rx", r.w / 2); ghost.setAttribute("ry", r.h / 2); }
        else if (tool === "line" || tool === "arrow") { ghost.setAttribute("x1", a[0]); ghost.setAttribute("y1", a[1]); ghost.setAttribute("x2", b[0]); ghost.setAttribute("y2", b[1]); }
        else { ghost.setAttribute("x", r.x); ghost.setAttribute("y", r.y); ghost.setAttribute("width", r.w); ghost.setAttribute("height", r.h); }
      };
      const move = (ev) => { b = local(ev); if (tool === "ink") { const last = pts[pts.length - 1]; if (Math.hypot(b[0] - last[0], b[1] - last[1]) > 1.5) pts.push(b); } paint(); };
      const up = () => {
        svg.removeEventListener("pointermove", move); ghost.remove();
        const v = vp(); const P1 = v.toPdf(...a), P2 = v.toPdf(...b);
        const big = Math.hypot(b[0] - a[0], b[1] - a[1]) > 4;
        let o = null;
        if (tool === "ink") o = { type: "ink", color, width: this.width, points: pts.map((p) => v.toPdf(...p).map((x) => +x.toFixed(2))) };
        else if ((tool === "line" || tool === "arrow") && big) o = { type: tool, color, width: this.width, p1: P1, p2: P2 };
        else if (big) { const r = rectOf(P1, P2); o = { type: tool, color, width: this.width, ...r }; }
        if (o) { const snap = this.snapshot(); o.id = uid(); mp.objs.push(o); this.commit(snap); this.redraw(i); }
      };
      svg.addEventListener("pointermove", move); svg.addEventListener("pointerup", up, { once: true });
    });
    svg.addEventListener("dblclick", (e) => {
      const objEl = e.target.closest(".obj"); if (!objEl) return;
      const o = mp.objs.find((x) => x.id === objEl.dataset.id);
      if (o?.type === "text") { const [x, y] = this.vpOf(mp).toView(o.x, o.y); this.textEditor(i, box, [x, y], o); }
    });

    box.onpointerup = this.tool === "highlight" ? () => setTimeout(() => this.highlightSelection(i, box), 0) : null;
  }
  highlightSelection(i, box) {
    const sel = window.getSelection(); if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const layer = box.querySelector(".textLayer"); if (!layer || !layer.contains(sel.anchorNode)) return;
    const rb = box.getBoundingClientRect(); const v = this.vpOf(this.model.pages[i]);
    const rects = [...sel.getRangeAt(0).getClientRects()].filter((r) => r.width > 1 && r.height > 1);
    if (!rects.length) return;
    const snap = this.snapshot();
    for (const r of mergeRects(rects)) {
      const P1 = v.toPdf(r.left - rb.left, r.top - rb.top), P2 = v.toPdf(r.right - rb.left, r.bottom - rb.top);
      this.model.pages[i].objs.push({ id: uid(), type: "highlight", color: this.hlColor, ...rectOf(P1, P2) });
    }
    sel.removeAllRanges(); this.commit(snap); this.redraw(i);
  }
  textEditor(i, box, at, existing) {
    box.querySelector(".pdf-text-edit")?.remove();
    const mp = this.model.pages[i];
    const size = existing?.size || this.fontSize;
    const ta = document.createElement("textarea"); ta.className = "pdf-text-edit"; ta.spellcheck = false;
    ta.style.left = at[0] + "px"; ta.style.top = at[1] + "px"; ta.style.fontSize = size * this.scale + "px";
    ta.style.color = existing?.color || this.color; ta.value = existing?.text || ""; ta.placeholder = t("pdf_text_placeholder");
    const fit = () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + 2 + "px"; ta.style.width = "auto"; ta.style.width = Math.min(box.clientWidth - at[0] - 4, Math.max(120, ta.scrollWidth + 12)) + "px"; };
    box.append(ta); fit(); setTimeout(() => ta.focus(), 0);
    if (existing) existing._hidden = true, this.redraw(i);
    let done = false;
    const finish = (keep) => {
      if (done) return; done = true; ta.remove();
      if (existing) delete existing._hidden;
      const text = ta.value.replace(/\s+$/, "");
      const snap = this.snapshot();
      if (existing) {
        if (!keep || text === existing.text) { this.redraw(i); return; }
        if (!text) mp.objs.splice(mp.objs.indexOf(existing), 1); else existing.text = text;
      } else {
        if (!keep || !text) return;
        const v = this.vpOf(mp); const [x, y] = v.toPdf(at[0], at[1]);
        mp.objs.push({ id: uid(), type: "text", text, x, y, size, color: this.color, angle: v.rotation });
      }
      this.commit(snap); this.redraw(i);
    };
    ta.addEventListener("input", fit);
    ta.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Escape") finish(false); if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) finish(true); });
    ta.addEventListener("blur", () => finish(true));
  }

  snapshot() { return { pages: clone(this.model.pages), fields: clone(this.fieldValues) }; }
  commit(before) {
    this.undoStack.push(before); if (this.undoStack.length > 200) this.undoStack.shift();
    this.redoStack = []; this.changed = true; this.onChange?.(); this.paintEditBar();
  }
  restore(state) {
    const order = this.model.pages.map((p) => p.id + ":" + p.rot).join();
    this.model.pages = state.pages; this.fieldValues = state.fields || {};
    this.selected = null;
    if (order !== this.model.pages.map((p) => p.id + ":" + p.rot).join()) this.layout(); else this.redrawAll();
    this.renderFields();
  }
  undo() { const s = this.undoStack.pop(); if (!s) return; this.redoStack.push(this.snapshot()); this.restore(s); this.changed = this.undoStack.length > 0; this.onChange?.(); this.paintEditBar(); }
  redo() { const s = this.redoStack.pop(); if (!s) return; this.undoStack.push(this.snapshot()); this.restore(s); this.changed = true; this.onChange?.(); this.paintEditBar(); }
  select(sel) {
    const prev = this.selected; this.selected = sel;
    if (prev) this.redraw(prev.page); if (sel && (!prev || prev.page !== sel.page)) this.redraw(sel.page);
    this.paintEditBar();
  }
  deleteSelected() {
    const s = this.selected; if (!s) return;
    const mp = this.model.pages[s.page]; const k = mp?.objs.findIndex((o) => o.id === s.id); if (k == null || k < 0) return;
    const snap = this.snapshot(); mp.objs.splice(k, 1); this.selected = null; this.commit(snap); this.redraw(s.page);
  }
  pageOp(i, op) {
    const pages = this.model.pages; const snap = this.snapshot();
    if (op === "rotL" || op === "rotR") pages[i].rot = ((pages[i].rot || 0) + (op === "rotL" ? 270 : 90)) % 360;
    else if (op === "up" && i > 0) [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]];
    else if (op === "down" && i < pages.length - 1) [pages[i + 1], pages[i]] = [pages[i], pages[i + 1]];
    else if (op === "del" && pages.length > 1) pages.splice(i, 1);
    else if (op === "add") {
      const info = pages[i].src ? this.info[pages[i].src - 1] : null;
      const size = info ? [Math.abs(info.view[2] - info.view[0]), Math.abs(info.view[3] - info.view[1])] : (pages[i].size || A4);
      pages.splice(i + 1, 0, { id: uid(), src: 0, rot: 0, objs: [], size });
    } else return;
    this.selected = null; this.commit(snap); this.layout();
    if (op === "up" || op === "down" || op === "add") requestAnimationFrame(() => this.goto(op === "up" ? i : i + 2));
  }

  async setEditing(on) {
    if (on === this.editing) return;
    if (on) {
      if (!this.doc) return;
      try { await this.ensureBytes(); }
      catch (e) { toast(t("pdf_edit_failed") + ": " + (e.message || e), { kind: "error", ms: 8000 }); return; }
    }
    this.editing = on;
    this.el.classList.toggle("editing", on);
    this.el.querySelector(".pdf-edit-toggle").classList.toggle("on", on);
    this.el.querySelector(".pdf-edit-bar").hidden = !on;
    if (!on) { this.select(null); this.el.querySelector(".pdf-fields").hidden = true; }
    this.paintEditBar(); this.layout();
    if (on && this.fields === null) this.loadFields();
  }
  async ensureBytes() {
    if (this.bytes) return this.bytes;
    const res = await fetch(rawUrl(this.tab.path)); if (!res.ok) throw new Error("HTTP " + res.status);
    const bytes = new Uint8Array(await res.arrayBuffer());
    const L = await pdflib();
    try { await L.PDFDocument.load(bytes.slice(), { updateMetadata: false }); }
    catch (e) { if (/encrypt/i.test(String(e?.message || e?.name))) throw new Error(t("pdf_encrypted")); throw e; }
    this.bytes = bytes;
    return bytes;
  }
  async loadFields() {
    try { this.fields = await readFields(this.bytes.slice()); } catch { this.fields = []; }
    this.paintEditBar(); this.renderFields();
  }
  paintEditBar() {
    const bar = this.el.querySelector(".pdf-edit-bar"); if (!bar || !this.editing) return;
    const toolBtn = ([id, ic]) => `<button class="tb-btn ${this.tool === id ? "on" : ""}" data-tool="${id}" title="${esc(t("pdf_tool_" + id))}" aria-label="${esc(t("pdf_tool_" + id))}">${icon(ic)}</button>`;
    const color = this.tool === "highlight" ? this.hlColor : this.color;
    const nf = this.fields?.length || 0;
    bar.innerHTML = `${TOOLS.map(toolBtn).join("")}<span class="tb-sep"></span>
      <button class="tb-btn" data-e="image" title="${esc(t("pdf_tool_image"))}" aria-label="${esc(t("pdf_tool_image"))}">${icon("imageAdd")}</button>
      <button class="tb-btn" data-e="signature" title="${esc(t("pdf_tool_signature"))}" aria-label="${esc(t("pdf_tool_signature"))}">${icon("signature")}</button>
      <button class="tb-btn" data-e="stamp" title="${esc(t("pdf_tool_stamp"))}" aria-label="${esc(t("pdf_tool_stamp"))}">${icon("stamp")}</button>
      <button class="tb-btn" data-e="more" title="${esc(t("pdf_more"))}" aria-label="${esc(t("pdf_more"))}">${icon("more")}</button>
      <span class="tb-sep"></span>
      <button class="tb-btn" data-e="color" title="${esc(t("fmt_color"))}"><span class="swatch" style="background:${esc(color)}"></span></button>
      <label class="pdf-num" title="${esc(t("pdf_width"))}"><span class="ic">${ICONS.lineT}</span><select class="tb-select" data-e="width">${[1, 2, 3, 5, 8].map((w) => `<option ${w === this.width ? "selected" : ""}>${w}</option>`).join("")}</select></label>
      <label class="pdf-num" title="${esc(t("doc_size"))}"><span class="ic">${ICONS.textT}</span><select class="tb-select" data-e="size">${[8, 10, 12, 14, 16, 18, 24, 32, 48].map((s) => `<option ${s === this.fontSize ? "selected" : ""}>${s}</option>`).join("")}</select></label>
      <span class="tb-sep"></span>
      <button class="tb-btn" data-e="undo" title="${esc(t("undo"))} (Ctrl+Z)" ${this.undoStack.length ? "" : "disabled"}>${icon("undo")}</button>
      <button class="tb-btn" data-e="redo" title="${esc(t("redo"))} (Ctrl+Y)" ${this.redoStack.length ? "" : "disabled"}>${icon("redo")}</button>
      <button class="tb-btn" data-e="delete" title="${esc(t("pdf_delete_obj"))} (Del)" ${this.selected ? "" : "disabled"}>${icon("trash")}</button>
      <span class="tb-sep"></span>
      <button class="tb-btn wide ${this.el.querySelector(".pdf-fields").hidden ? "" : "on"}" data-e="fields" ${nf ? "" : "disabled"} title="${esc(nf ? t("pdf_fields_hint") : t("pdf_no_fields"))}">${icon("form")}<span>${esc(t("pdf_fields", { n: nf }))}</span></button>
      <span class="small muted pdf-hint" title="${esc(t("pdf_hint_" + this.tool))}">${esc(t("pdf_hint_" + this.tool))}</span>
      <button class="btn sm primary" data-e="save" ${this.dirty ? "" : "disabled"}>${icon("save")}${esc(t("save"))}</button>`;
    bar.onclick = (e) => {
      const b = e.target.closest("[data-tool],[data-e]"); if (!b || b.disabled) return;
      if (b.dataset.tool) { this.tool = b.dataset.tool; this.select(null); this.paintEditBar(); this.redrawAll(); this.el.classList.toggle("tool-highlight", this.tool === "highlight"); return; }
      const a = b.dataset.e;
      if (a === "color") showMenu((this.tool === "highlight" ? HIGHLIGHTS : COLORS).map((c) => ({ label: c, swatch: c, run: () => { if (this.tool === "highlight") this.hlColor = c; else this.color = c; this.recolorSelected(c); this.paintEditBar(); } })), b);
      if (a === "color") document.querySelectorAll(".menu .mi").forEach((mi, k) => { const c = (this.tool === "highlight" ? HIGHLIGHTS : COLORS)[k]; if (c) mi.querySelector(".mi-ic").innerHTML = `<span class="swatch" style="background:${c}"></span>`; });
      if (a === "undo") this.undo();
      if (a === "redo") this.redo();
      if (a === "delete") this.deleteSelected();
      if (a === "fields") { const f = this.el.querySelector(".pdf-fields"); f.hidden = !f.hidden; this.renderFields(); this.paintEditBar(); }
      if (a === "save") this.onSave?.();
      if (a === "image") this.pickImage();
      if (a === "signature") this.signature();
      if (a === "stamp") this.stampMenu(b);
      if (a === "more") this.moreMenu(b);
    };
    bar.onchange = (e) => {
      const s = e.target.closest("[data-e]"); if (!s) return;
      if (s.dataset.e === "width") { this.width = +s.value; this.restyleSelected({ width: this.width }); }
      if (s.dataset.e === "size") { this.fontSize = +s.value; this.restyleSelected({ size: this.fontSize }); }
    };
  }
  selectedObj() { const s = this.selected; return s ? this.model.pages[s.page]?.objs.find((o) => o.id === s.id) : null; }
  recolorSelected(c) { const o = this.selectedObj(); if (o && o.type !== "whiteout") this.restyleSelected({ color: c }); }
  restyleSelected(patch) {
    const o = this.selectedObj(); if (!o) return;
    if ("size" in patch && o.type !== "text") return;
    if ("width" in patch && ["text", "highlight", "whiteout"].includes(o.type)) return;
    const snap = this.snapshot(); Object.assign(o, patch); this.commit(snap); this.redraw(this.selected.page);
  }
  renderFields() {
    const host = this.el.querySelector(".pdf-fields"); if (!host || host.hidden) return;
    const fields = this.fields || [];
    const val = (f) => (f.name in this.fieldValues ? this.fieldValues[f.name] : f.value);
    host.innerHTML = `<div class="pdf-fields-head"><b>${esc(t("pdf_fields", { n: fields.length }))}</b><button class="icon-btn sm" data-x>${icon("close")}</button></div>` + fields.map((f, k) => {
      const id = "pf-" + k, dis = f.readOnly ? "disabled" : "";
      let control;
      if (f.type === "check") control = `<input type="checkbox" class="switch" id="${id}" data-k="${k}" ${val(f) ? "checked" : ""} ${dis}>`;
      else if (f.type === "select" || f.type === "radio") control = `<select class="input sm" id="${id}" data-k="${k}" ${dis}><option value=""></option>${(f.options || []).map((o) => `<option ${o === val(f) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
      else if (f.multiline) control = `<textarea class="input sm" id="${id}" data-k="${k}" rows="3" ${dis}>${esc(val(f))}</textarea>`;
      else control = `<input class="input sm" id="${id}" data-k="${k}" value="${esc(val(f))}" ${dis}>`;
      return `<label class="pdf-field ${f.type === "check" ? "row" : ""}" for="${id}"><span>${esc(f.name)}</span>${control}</label>`;
    }).join("");
    host.querySelector("[data-x]").onclick = () => { host.hidden = true; this.paintEditBar(); };
    const onChange = (e) => {
      const el = e.target.closest("[data-k]"); if (!el) return;
      const f = fields[+el.dataset.k]; const v = el.type === "checkbox" ? el.checked : el.value;
      const snap = this.snapshot(); this.fieldValues[f.name] = v; this.commit(snap);
    };
    host.onchange = onChange;
  }
  keys(e) {
    if (!this.editing || e.target.closest("input, textarea, select")) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (mod && k === "z" && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); this.undo(); }
    else if (mod && (k === "y" || (k === "z" && e.shiftKey))) { e.preventDefault(); e.stopPropagation(); this.redo(); }
    else if ((e.key === "Delete" || e.key === "Backspace") && this.selected) { e.preventDefault(); this.deleteSelected(); }
    else if (mod && k === "c" && this.selected) { const o = this.selectedObj(); if (o) { this.clip = clone(o); e.preventDefault(); } }
    else if (mod && k === "x" && this.selected) { const o = this.selectedObj(); if (o) { this.clip = clone(o); e.preventDefault(); this.deleteSelected(); } }
    else if (mod && k === "v" && this.clip) { e.preventDefault(); e.stopPropagation(); this.pasteObj(this.clip); }
    else if (mod && k === "d" && this.selected) { e.preventDefault(); e.stopPropagation(); const o = this.selectedObj(); if (o) this.pasteObj(o, this.selected.page); }
    else if (e.key.startsWith("Arrow") && this.selected) { e.preventDefault(); this.nudge(e.key, e.shiftKey ? 10 : 1); }
    else if (e.key === "Escape") { if (this.selected) this.select(null); else if (this.tool !== "select") { this.tool = "select"; this.paintEditBar(); this.redrawAll(); } }
  }

  async save(path, opts = {}) {
    const bytes = await this.ensureBytes();
    const model = this.snapshot();
    const savedSnapshot = JSON.stringify(model);
    const out = await applyEdits(bytes.slice(), this.editModel(model), await fontBytes() || null);
    const q = new URLSearchParams({ path, mtime: String(opts.mtime || 0), force: opts.force ? "1" : "0" });
    const r = await api("save_binary?" + q, new Blob([out], { type: "application/pdf" }), { raw: true, type: "application/pdf" });
    if (r.ok) {
      if (JSON.stringify(this.snapshot()) !== savedSnapshot) {
        this.savedSnapshot = savedSnapshot;
        this.onChange?.(); this.paintEditBar();
        return r;
      }
      this.bytes = null; this.fields = null; this.model = null; this.selected = null;
      for (const x of Object.values(this.extras)) x.doc?.destroy?.();
      this.extras = {}; this.images = {};
      const keepEdit = this.editing;
      await this.load();
      if (keepEdit) { await this.ensureBytes().catch(() => {}); this.loadFields(); this.paintEditBar(); }
    }
    return r;
  }

  // Right click on a page: copy the selected text, object and page actions while editing, zoom and export.
  contextMenu(e) {
    if (e.target.closest("input, textarea, select, [contenteditable]")) return;
    e.preventDefault();
    if (!this.model) return;
    const box = e.target.closest(".pdf-page-box");
    const i = box ? +box.dataset.i : Math.max(0, this.current() - 1);
    const n = this.model.pages.length;
    const sel = window.getSelection();
    const text = sel && !sel.isCollapsed && this.wrap.contains(sel.anchorNode) ? sel.toString().trim() : "";
    const objEl = this.editing && box ? e.target.closest(".obj") : null;
    if (objEl) { const o = this.model.pages[i]?.objs.find((x) => x.id === objEl.dataset.id); if (o) this.select({ id: o.id, page: i }); }
    const hooks = this.menuHooks?.() || {};
    const items = [];
    if (text) {
      items.push({ label: t("copy"), icon: "copy", kbd: kbd("Ctrl+C"), run: () => copyText(text).then(() => toast(t("copied"), { kind: "ok", ms: 1400 })) });
      if (this.editing && box) items.push({ label: t("pdf_tool_highlight"), icon: "marker", run: () => this.highlightSelection(i, box) });
      if (hooks.explain) items.push({ label: t("cm_ask_ai"), icon: "sparkle", run: () => hooks.explain(text) });
      if (hooks.webSearch) items.push({ label: t("ai_web_search_selection"), icon: "globe", run: () => hooks.webSearch(text) });
      items.push("-");
    }
    const obj = this.editing ? this.selectedObj() : null;
    if (obj) items.push(
      { label: t("cut"), icon: "cut", kbd: kbd("Ctrl+X"), run: () => { this.clip = clone(obj); this.deleteSelected(); } },
      { label: t("copy"), icon: "copy", kbd: kbd("Ctrl+C"), run: () => { this.clip = clone(obj); } },
      { label: t("cm_duplicate"), icon: "dup", kbd: kbd("Ctrl+D"), run: () => this.pasteObj(obj, this.selected.page) },
      { label: t("pdf_delete_obj"), icon: "trash", kbd: "Del", danger: true, run: () => this.deleteSelected() },
      "-");
    if (this.editing && this.clip) items.push({ label: t("paste"), icon: "paste", kbd: kbd("Ctrl+V"), run: () => this.pasteObj(this.clip, i) }, "-");
    if (this.editing && box) items.push(
      { header: `${t("pdf_page")} ${i + 1}` },
      { label: t("pdf_rotate_left"), icon: "rotL", run: () => this.pageOp(i, "rotL") },
      { label: t("pdf_rotate_right"), icon: "rotR", run: () => this.pageOp(i, "rotR") },
      { label: t("pdf_move_up"), icon: "up", disabled: i === 0, run: () => this.pageOp(i, "up") },
      { label: t("pdf_move_down"), icon: "down", disabled: i >= n - 1, run: () => this.pageOp(i, "down") },
      { label: t("pdf_insert_blank"), icon: "pageAdd", run: () => this.pageOp(i, "add") },
      { label: t("pdf_delete_page"), icon: "trash", danger: true, disabled: n <= 1, run: () => this.pageOp(i, "del") },
      "-");
    items.push(
      { label: t("zoom_in"), icon: "zoomIn", run: () => this.zoom(1.15) },
      { label: t("zoom_out"), icon: "zoomOut", run: () => this.zoom(1 / 1.15) },
      { label: t("pdf_fit"), icon: "fullscreen", run: () => this.action("fit") },
      { label: t("pdf_thumbs"), icon: "thumbs", checked: !this.el.querySelector(".pdf-thumbs").hidden, run: () => this.toggleThumbs() },
      "-",
      { label: t("pdf_edit"), icon: "pen", checked: this.editing, run: () => this.setEditing(!this.editing) },
      { label: t("pdf_export_text"), icon: "fileText", run: () => this.onExport?.("text") },
      ...(hooks.ask ? [{ label: t("pdf_ask"), icon: "sparkle", run: () => hooks.ask() }] : []),
    );
    showMenu(items, { x: e.clientX, y: e.clientY });
  }
  current() { return +(this.el.querySelector(".pdf-page")?.value || 1); }
  updatePage() {
    const boxes = this.wrap.children; const mid = this.scroll.scrollTop + this.scroll.clientHeight / 3;
    for (const b of boxes) { if (b.offsetTop + b.offsetHeight > mid) { this.el.querySelector(".pdf-page").value = b.dataset.page; break; } }
    this.markThumb();
  }
  goto(n) { const b = this.wrap.children[Math.max(0, Math.min(this.model?.pages.length || 1, n) - 1)]; if (b) this.scroll.scrollTop = b.offsetTop - (this.editing ? 46 : 12); }
  zoom(f) {
    const page = this.current();
    this.scale = Math.max(0.3, Math.min(5, this.scale * f)); this.layout(); requestAnimationFrame(() => this.goto(page));
  }
  action(a, el) {
    if (a === "prev") this.goto(this.current() - 1);
    else if (a === "next") this.goto(this.current() + 1);
    else if (a === "in") this.zoom(1.15);
    else if (a === "out") this.zoom(1 / 1.15);
    else if (a === "fit") { const info = this.info?.[0]; if (!info) return; const w = Math.abs(info.view[2] - info.view[0]); this.scale = Math.max(0.3, (this.scroll.clientWidth - 48) / w); this.layout(); }
    else if (a === "export") showMenu([{ label: t("pdf_export_text"), icon: "fileText", run: () => this.onExport?.("text") }, { label: t("pdf_export_md"), icon: "markdown", run: () => this.onExport?.("markdown") }], el, { alignRight: true });
    else if (a === "text") this.onExtract?.();
    else if (a === "ask") this.onAsk?.();
    else if (a === "edit") this.setEditing(!this.editing);
    else if (a === "thumbs") this.toggleThumbs();
  }

  async text(maxChars = 400000) {
    const parts = [];
    let total = 0;
    for (let n = 1; n <= this.doc.numPages && total < maxChars; n++) {
      const page = await this.doc.getPage(n);
      const content = await page.getTextContent();
      let line = "", lastY = null; const lines = [];
      for (const item of content.items) {
        const y = item.transform?.[5];
        if (lastY !== null && Math.abs(y - lastY) > 2) { lines.push(line); line = ""; }
        line += item.str + (item.hasEOL ? "\n" : ""); lastY = y;
      }
      lines.push(line);
      const pageText = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
      parts.push(`--- ${t("pdf_page")} ${n} ---\n${pageText}`); total += pageText.length;
    }
    return parts.join("\n\n");
  }
  destroy() {
    this.observer?.disconnect(); this.thumbObs?.disconnect(); this.doc?.destroy?.();
    for (const x of Object.values(this.extras)) x.doc?.destroy?.();
    this.el.remove();
  }

  // ---- the model handed to applyEdits: snapshot + images + inserted documents ----
  editModel(model) {
    return { ...model, images: this.images, extras: Object.fromEntries(Object.entries(this.extras).map(([k, x]) => [k, x.bytes.slice()])) };
  }
  async editedBytes() {
    const bytes = await this.ensureBytes();
    if (!this.dirty) return bytes.slice();
    return applyEdits(bytes.slice(), this.editModel(this.snapshot()), await fontBytes() || null);
  }

  // ---- page thumbnails ----
  bindThumbs() {
    const host = this.el.querySelector(".pdf-thumbs");
    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-ta]"); const th = e.target.closest(".pdf-thumb"); if (!th) return;
      const i = +th.dataset.i;
      if (b) { e.stopPropagation(); this.pageOp(i, b.dataset.ta); return; }
      this.goto(i + 1);
    });
    host.addEventListener("dragstart", (e) => { const th = e.target.closest(".pdf-thumb"); if (!th || !this.editing) return e.preventDefault(); this._dragFrom = +th.dataset.i; e.dataTransfer.effectAllowed = "move"; th.classList.add("dragging"); });
    host.addEventListener("dragend", () => { host.querySelectorAll(".dragging, .drop-before, .drop-after").forEach((x) => x.classList.remove("dragging", "drop-before", "drop-after")); });
    host.addEventListener("dragover", (e) => {
      if (this._dragFrom == null) return; const th = e.target.closest(".pdf-thumb"); if (!th) return;
      e.preventDefault(); const r = th.getBoundingClientRect(); const after = e.clientY > r.top + r.height / 2;
      host.querySelectorAll(".drop-before, .drop-after").forEach((x) => x.classList.remove("drop-before", "drop-after"));
      th.classList.add(after ? "drop-after" : "drop-before");
    });
    host.addEventListener("drop", (e) => {
      const th = e.target.closest(".pdf-thumb"); const from = this._dragFrom; this._dragFrom = null;
      if (!th || from == null) return; e.preventDefault();
      const r = th.getBoundingClientRect(); let to = +th.dataset.i + (e.clientY > r.top + r.height / 2 ? 1 : 0);
      if (to > from) to--;
      this.movePage(from, to);
    });
  }
  toggleThumbs(force) {
    const host = this.el.querySelector(".pdf-thumbs");
    const on = force ?? host.hidden; host.hidden = !on;
    this.el.querySelector('[data-a="thumbs"]')?.classList.toggle("on", on);
    localStorage.setItem("mn_pdf_thumbs", on ? "1" : "0");
    this._thumbSig = ""; if (on) this.renderThumbs();
  }
  renderThumbs() {
    const host = this.el.querySelector(".pdf-thumbs"); if (!host || host.hidden || !this.model) return;
    const sig = this.model.pages.map((p) => p.id + ":" + (p.rot || 0)).join() + "|" + this.editing;
    if (sig === this._thumbSig) { this.markThumb(); return; }
    this._thumbSig = sig;
    this.thumbObs?.disconnect();
    this.thumbObs = new IntersectionObserver((entries) => {
      for (const en of entries) if (en.isIntersecting) { this.thumbObs.unobserve(en.target); this.paintThumb(+en.target.dataset.i, en.target); }
    }, { root: host, rootMargin: "400px 0px" });
    host.innerHTML = "";
    const n = this.model.pages.length;
    this.model.pages.forEach((mp, i) => {
      const vp = this.vpOf(mp); const k = 104 / Math.max(1, vp.width);
      const th = document.createElement("div"); th.className = "pdf-thumb"; th.dataset.i = i; th.draggable = this.editing; th.tabIndex = 0;
      th.title = `${t("pdf_page")} ${i + 1}`;
      const B = (a, ic, label, dis) => `<button class="icon-btn sm" data-ta="${a}" title="${esc(t(label))}" aria-label="${esc(t(label))}" ${dis ? "disabled" : ""}>${icon(ic)}</button>`;
      th.innerHTML = `<span class="pdf-thumb-img${mp.src ? "" : " blank"}" style="width:${Math.round(vp.width * k)}px;height:${Math.round(vp.height * k)}px"></span><span class="pdf-thumb-n">${i + 1}</span>`
        + (this.editing ? `<span class="pdf-thumb-tools">${B("rotR", "rotR", "pdf_rotate_right")}${B("del", "trash", "pdf_delete_page", n <= 1)}</span>` : "");
      host.append(th); this.thumbObs.observe(th);
    });
    this.markThumb();
  }
  markThumb() {
    const host = this.el.querySelector(".pdf-thumbs"); if (!host || host.hidden) return;
    const cur = this.current() - 1;
    host.querySelectorAll(".pdf-thumb").forEach((th) => {
      const on = +th.dataset.i === cur; th.classList.toggle("on", on);
      if (on && (th.offsetTop < host.scrollTop || th.offsetTop + th.offsetHeight > host.scrollTop + host.clientHeight)) host.scrollTop = th.offsetTop - host.clientHeight / 3;
    });
  }
  async paintThumb(i, el) {
    const mp = this.model?.pages[i]; const box = el.querySelector(".pdf-thumb-img"); if (!mp?.src || !box) return;
    const doc = this.docOf(mp); if (!doc) return;
    try {
      const page = await doc.getPage(mp.src);
      const rot = (page.rotate + (mp.rot || 0)) % 360;
      const base = page.getViewport({ scale: 1, rotation: rot });
      const vp = page.getViewport({ scale: (104 / base.width) * Math.min(window.devicePixelRatio || 1, 2), rotation: rot });
      const c = document.createElement("canvas"); c.width = Math.floor(vp.width); c.height = Math.floor(vp.height);
      await page.render({ canvasContext: c.getContext("2d"), viewport: vp }).promise;
      box.replaceChildren(c);
    } catch {  }
  }
  movePage(from, to) {
    const pages = this.model.pages;
    if (from === to || from < 0 || to < 0 || from >= pages.length || to >= pages.length) return;
    const snap = this.snapshot(); const [mp] = pages.splice(from, 1); pages.splice(to, 0, mp);
    this.selected = null; this.commit(snap); this.layout(); requestAnimationFrame(() => this.goto(to + 1));
  }

  // ---- objects: paste, duplicate, nudge ----
  pasteObj(src, pageIndex) {
    const i = pageIndex ?? Math.max(0, this.current() - 1); const mp = this.model.pages[i]; if (!mp) return;
    const o = clone(src); o.id = uid(); delete o._hidden; translate(o, 12, -12);
    const snap = this.snapshot(); mp.objs.push(o); this.selected = { id: o.id, page: i }; this.commit(snap); this.redraw(i);
  }
  nudge(key, step) {
    const s = this.selected; const o = this.selectedObj(); if (!o) return;
    const v = this.vpOf(this.model.pages[s.page]);
    const dv = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[key]; if (!dv) return;
    const a = v.toPdf(100, 100), b = v.toPdf(100 + dv[0] * this.scale, 100 + dv[1] * this.scale);
    const snap = this.snapshot(); translate(o, b[0] - a[0], b[1] - a[1]); this.commit(snap); this.redraw(s.page);
  }
  addObject(i, o) {
    const mp = this.model.pages[i]; if (!mp) return;
    const snap = this.snapshot(); o.id = uid(); mp.objs.push(o);
    if (this.tool !== "select") { this.tool = "select"; this.paintEditBar(); this.redrawAll(); }
    this.selected = { id: o.id, page: i }; this.commit(snap); this.redraw(i);
  }

  // ---- images and signatures ----
  async pickImage() {
    const file = await pickFile("image/png,image/jpeg,image/webp,image/gif,image/bmp"); if (!file) return;
    if (file.size > 25 * 1024 * 1024) { toast(t("pdf_image_too_big"), { kind: "error" }); return; }
    try { const { url, w, h } = await imageData(file); this.placeImage(url, w, h); }
    catch (e) { toast(t("pdf_image_failed") + ": " + (e.message || e), { kind: "error" }); }
  }
  placeImage(url, w, h, opts = {}) {
    const i = Math.max(0, this.current() - 1); const mp = this.model.pages[i]; if (!mp) return;
    const pb = this.pageBox(mp);
    let ow = Math.min(pb.w * (opts.maxW || 0.42), w * 0.75), oh = (ow * h) / Math.max(1, w);
    if (oh > pb.h * 0.45) { oh = pb.h * 0.45; ow = (oh * w) / Math.max(1, h); }
    const id = uid(); this.images[id] = url;
    const x = opts.x ?? pb.x0 + (pb.w - ow) / 2, y = opts.y ?? pb.y0 + (pb.h - oh) / 2;
    this.addObject(i, { type: "image", img: id, x, y, w: ow, h: oh });
  }
  async signature() {
    const saved = localStorage.getItem("mn_pdf_sig") || "";
    let pad = null;
    const res = await modal({
      title: t("pdf_sig_title"), wide: true, cls: "pdf-sig",
      body: `<p class="muted small">${esc(t("pdf_sig_hint"))}</p><canvas class="pdf-sig-pad" width="1040" height="360"></canvas>
        <div class="row pdf-sig-row">${["#1c2024", "#1d4ed8", "#0b6e4f"].map((c, k) => `<button class="pdf-sig-ink ${k ? "" : "on"}" data-ink="${c}" style="background:${c}" aria-label="${c}"></button>`).join("")}
        <button class="btn ghost sm" data-sig="clear">${icon("eraser")}${esc(t("pdf_sig_clear"))}</button><span class="spacer"></span>
        ${saved ? `<button class="btn ghost sm" data-sig="saved">${icon("retry")}${esc(t("pdf_sig_use_saved"))}</button>` : ""}
        <label class="check"><input type="checkbox" data-sig="keep" checked>${esc(t("pdf_sig_keep"))}</label></div>`,
      buttons: [{ label: t("cancel"), value: null }, { label: t("pdf_sig_insert"), primary: true, value: (box) => ({ url: pad?.export(), keep: box.querySelector('[data-sig="keep"]').checked }) }],
      onOpen: (box) => {
        pad = signaturePad(box.querySelector(".pdf-sig-pad"));
        box.querySelectorAll("[data-ink]").forEach((b) => (b.onclick = () => { box.querySelectorAll("[data-ink]").forEach((x) => x.classList.toggle("on", x === b)); pad.color = b.dataset.ink; }));
        box.querySelector('[data-sig="clear"]').onclick = () => pad.clear();
        const use = box.querySelector('[data-sig="saved"]'); if (use) use.onclick = () => pad.load(saved);
      },
    });
    if (!res?.url) { if (res) toast(t("pdf_sig_empty")); return; }
    if (res.keep) { try { localStorage.setItem("mn_pdf_sig", res.url); } catch {  } }
    const { w, h } = await imageSize(res.url);
    this.placeImage(res.url, w, h, { maxW: 0.3 });
  }

  // ---- stamps ----
  stampMenu(anchor) {
    const items = STAMPS.map(([key, color]) => ({ label: t("pdf_stamp_" + key), swatch: color, run: () => this.placeStamp(t("pdf_stamp_" + key).toUpperCase(), color) }));
    const today = new Date().toLocaleDateString(document.documentElement.lang === "en" ? "en-GB" : "ru-RU");
    items.push({ label: t("pdf_stamp_date", { date: today }), swatch: "#1565c0", run: () => this.placeStamp(today, "#1565c0") });
    items.push("-", { label: t("pdf_stamp_custom") + "…", icon: "textT", run: async () => { const v = await prompt(t("pdf_tool_stamp"), t("pdf_stamp_text")); if (v?.trim()) this.placeStamp(v.trim().slice(0, 60), this.color); } });
    showMenu(items, anchor);
  }
  placeStamp(text, color) {
    const i = Math.max(0, this.current() - 1); const mp = this.model.pages[i]; if (!mp) return;
    const pb = this.pageBox(mp); const h = 40, w = Math.min(pb.w * 0.6, Math.max(110, text.length * h * 0.36 + 28));
    this.addObject(i, { type: "stamp", text, color, x: pb.x0 + pb.w - w - 36, y: pb.y0 + pb.h - h - 36, w, h });
  }

  // ---- document-wide tools ----
  moreMenu(anchor) {
    showMenu([
      { label: t("pdf_insert_pdf") + "…", icon: "merge", run: () => this.insertPdf() },
      { label: t("pdf_extract_pages") + "…", icon: "extract", run: () => this.extract() },
      "-",
      { label: t("pdf_numbers") + "…", icon: "numbers", run: () => this.pageNumbers() },
      { label: t("pdf_watermark") + "…", icon: "watermark", run: () => this.watermark() },
      { label: t("pdf_clear_auto"), icon: "eraser", disabled: !this.model.pages.some((p) => p.objs.some((o) => o.auto)), run: () => this.clearAuto() },
    ], anchor, { alignRight: true });
  }
  async pageNumbers() {
    const r = await modal({
      title: t("pdf_numbers"),
      body: `<label class="field"><span>${esc(t("pdf_num_format"))}</span><select class="input" id="pn-f"><option value="n">1, 2, 3</option><option value="nN">1 / N</option><option value="page">${esc(t("pdf_num_page_of", { n: 1, total: "N" }))}</option></select></label>
        <label class="field"><span>${esc(t("pdf_num_pos"))}</span><select class="input" id="pn-p"><option value="bc">${esc(t("pdf_pos_bc"))}</option><option value="br">${esc(t("pdf_pos_br"))}</option><option value="tr">${esc(t("pdf_pos_tr"))}</option><option value="tc">${esc(t("pdf_pos_tc"))}</option></select></label>
        <label class="field"><span>${esc(t("pdf_num_start"))}</span><input class="input" id="pn-s" type="number" value="1" min="0" max="9999"></label>`,
      buttons: [{ label: t("cancel"), value: null }, { label: t("ok"), primary: true, value: (b) => ({ f: b.querySelector("#pn-f").value, p: b.querySelector("#pn-p").value, s: +b.querySelector("#pn-s").value || 1 }) }],
    });
    if (!r) return;
    const snap = this.snapshot(); const total = this.model.pages.length; const size = 10;
    this.model.pages.forEach((mp, i) => {
      mp.objs = mp.objs.filter((o) => o.auto !== "num");
      const n = i + r.s, text = r.f === "n" ? String(n) : r.f === "nN" ? `${n} / ${total + r.s - 1}` : t("pdf_num_page_of", { n, total: total + r.s - 1 });
      const pb = this.pageBox(mp); const tw = text.length * size * 0.52, m = 28;
      const x = r.p.endsWith("c") ? pb.x0 + (pb.w - tw) / 2 : pb.x0 + pb.w - m - tw;
      const y = r.p.startsWith("t") ? pb.y0 + pb.h - m : pb.y0 + m + size * LINE_H;
      mp.objs.push({ id: uid(), type: "text", text, x, y, size, color: "#444444", angle: 0, auto: "num" });
    });
    this.commit(snap); this.redrawAll();
  }
  async watermark() {
    const r = await modal({
      title: t("pdf_watermark"),
      body: `<label class="field"><span>${esc(t("pdf_wm_text"))}</span><input class="input" id="wm-t" value="${esc(t("pdf_stamp_confidential").toUpperCase())}" maxlength="60"></label>
        <label class="field"><span>${esc(t("pdf_wm_opacity"))}</span><select class="input" id="wm-o"><option value=".12">12%</option><option value=".2" selected>20%</option><option value=".35">35%</option></select></label>`,
      buttons: [{ label: t("cancel"), value: null }, { label: t("ok"), primary: true, value: (b) => ({ text: b.querySelector("#wm-t").value.trim(), opacity: +b.querySelector("#wm-o").value }) }],
    });
    if (!r?.text) return;
    const snap = this.snapshot();
    for (const mp of this.model.pages) {
      mp.objs = mp.objs.filter((o) => o.auto !== "wm");
      const pb = this.pageBox(mp); const size = Math.min(72, (Math.hypot(pb.w, pb.h) * 0.75) / Math.max(4, r.text.length * 0.6));
      const a = 45, th = (a * Math.PI) / 180, tw = r.text.length * size * 0.6;
      const dir = [Math.cos(th), Math.sin(th)], down = [Math.sin(th), -Math.cos(th)];
      const cx = pb.x0 + pb.w / 2, cy = pb.y0 + pb.h / 2;
      const x = cx - (dir[0] * tw) / 2 - (down[0] * size) / 2, y = cy - (dir[1] * tw) / 2 - (down[1] * size) / 2;
      mp.objs.unshift({ id: uid(), type: "text", text: r.text, x, y, size, color: "#808080", angle: a, opacity: r.opacity, auto: "wm" });
    }
    this.commit(snap); this.redrawAll();
  }
  clearAuto() {
    const snap = this.snapshot();
    for (const mp of this.model.pages) mp.objs = mp.objs.filter((o) => !o.auto);
    this.selected = null; this.commit(snap); this.redrawAll();
  }
  async insertPdf() {
    const file = await pickFile("application/pdf,.pdf"); if (!file) return;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const L = await pdflib();
      try { await L.PDFDocument.load(bytes.slice(), { updateMetadata: false }); }
      catch (e) { throw new Error(/encrypt/i.test(String(e?.message || e)) ? t("pdf_encrypted") : (e.message || String(e))); }
      const lib = await pdfjs();
      const doc = await lib.getDocument({ data: bytes.slice(), isEvalSupported: false, enableXfa: false }).promise;
      const key = "x" + uid(); const info = [];
      for (let n = 1; n <= Math.min(doc.numPages, 2000); n++) { const pg = await doc.getPage(n); info.push({ view: pg.view, rotate: pg.rotate }); }
      this.extras[key] = { bytes, doc, info, name: file.name };
      const at = Math.max(0, this.current());
      const snap = this.snapshot();
      this.model.pages.splice(at, 0, ...info.map((_, k) => ({ id: uid(), src: k + 1, ext: key, rot: 0, objs: [] })));
      this.selected = null; this.commit(snap); this.layout();
      requestAnimationFrame(() => this.goto(at + 1));
      toast(t("pdf_inserted", { n: info.length, name: file.name }), { kind: "ok" });
    } catch (e) { toast(t("pdf_insert_failed") + ": " + (e.message || e), { kind: "error", ms: 8000 }); }
  }
  async extract() {
    const total = this.model.pages.length;
    const v = await prompt(t("pdf_extract_pages"), t("pdf_extract_label", { total }), `1-${total}`, { placeholder: "1-3, 5, 8-", note: t("pdf_extract_note") });
    if (v == null) return;
    const idx = parseRanges(v, total);
    if (!idx) { toast(t("pdf_bad_range"), { kind: "error" }); return; }
    try {
      const out = await extractPages(await this.editedBytes(), idx);
      await this.onSaveCopy?.(out, (this.tab.name || "document.pdf").replace(/\.pdf$/i, "") + ` (${v.replace(/[^\d,\- ]/g, "").trim()}).pdf`);
    } catch (e) { toast(t("pdf_extract_failed") + ": " + (e.message || e), { kind: "error", ms: 8000 }); }
  }
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = document.createElement("input"); input.type = "file"; input.accept = accept; input.style.display = "none";
    input.onchange = () => { resolve(input.files?.[0] || null); input.remove(); };
    input.addEventListener("cancel", () => { resolve(null); input.remove(); });
    document.body.append(input); input.click();
  });
}
function imageSize(url) {
  return new Promise((resolve, reject) => { const img = new Image(); img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight, img }); img.onerror = () => reject(new Error("image")); img.src = url; });
}
// PNG and JPEG go in as they are; other formats are converted to PNG (pdf-lib embeds only those two).
async function imageData(file) {
  const url = await new Promise((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => reject(r.error); r.readAsDataURL(file); });
  const { w, h, img } = await imageSize(url);
  if (/^data:image\/(png|jpe?g);/i.test(url)) return { url, w, h };
  const c = document.createElement("canvas"); c.width = w; c.height = h; c.getContext("2d").drawImage(img, 0, 0);
  return { url: c.toDataURL("image/png"), w, h };
}
function signaturePad(canvas) {
  const ctx = canvas.getContext("2d"); let drawing = false, last = null, empty = true;
  const pad = { color: "#1c2024" };
  const pos = (e) => { const r = canvas.getBoundingClientRect(); return [((e.clientX - r.left) * canvas.width) / r.width, ((e.clientY - r.top) * canvas.height) / r.height]; };
  canvas.addEventListener("pointerdown", (e) => { drawing = true; last = pos(e); canvas.setPointerCapture(e.pointerId); ctx.beginPath(); ctx.arc(last[0], last[1], 2.2, 0, Math.PI * 2); ctx.fillStyle = pad.color; ctx.fill(); empty = false; });
  canvas.addEventListener("pointermove", (e) => {
    if (!drawing) return; const p = pos(e);
    ctx.strokeStyle = pad.color; ctx.lineWidth = 4.4; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(...last); ctx.lineTo(...p); ctx.stroke(); last = p; empty = false;
  });
  const stop = () => { drawing = false; };
  canvas.addEventListener("pointerup", stop); canvas.addEventListener("pointercancel", stop);
  pad.clear = () => { ctx.clearRect(0, 0, canvas.width, canvas.height); empty = true; };
  pad.load = (url) => { const img = new Image(); img.onload = () => { pad.clear(); const k = Math.min(canvas.width / img.width, canvas.height / img.height, 1); ctx.drawImage(img, (canvas.width - img.width * k) / 2, (canvas.height - img.height * k) / 2, img.width * k, img.height * k); empty = false; }; img.src = url; };
  // Trimmed to the ink with a small margin, transparent background.
  pad.export = () => {
    if (empty) return "";
    const { width: W, height: H } = canvas; const data = ctx.getImageData(0, 0, W, H).data;
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (data[(y * W + x) * 4 + 3] > 8) { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
    if (x1 < 0) return "";
    const m = 8; x0 = Math.max(0, x0 - m); y0 = Math.max(0, y0 - m); x1 = Math.min(W - 1, x1 + m); y1 = Math.min(H - 1, y1 + m);
    const out = document.createElement("canvas"); out.width = x1 - x0 + 1; out.height = y1 - y0 + 1;
    out.getContext("2d").drawImage(canvas, x0, y0, out.width, out.height, 0, 0, out.width, out.height);
    return out.toDataURL("image/png");
  };
  return pad;
}

function translate(o, dx, dy) {
  if ("x" in o) { o.x += dx; o.y += dy; }
  if (o.p1) { o.p1 = [o.p1[0] + dx, o.p1[1] + dy]; o.p2 = [o.p2[0] + dx, o.p2[1] + dy]; }
  if (o.points) o.points = o.points.map(([x, y]) => [x + dx, y + dy]);
}
function mergeRects(rects) {

  const out = [];
  for (const r of [...rects].sort((a, b) => a.top - b.top || a.left - b.left)) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.top - r.top) < 3 && Math.abs(last.bottom - r.bottom) < 3 && r.left <= last.right + 4) last.right = Math.max(last.right, r.right);
    else out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
  }
  return out;
}
