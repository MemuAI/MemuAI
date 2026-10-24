// MemuNote — PDF view and editor (Round 31).
// View: pdf.js renders pages lazily (IntersectionObserver), with a selectable text layer.
// Edit ("Редактировать"): annotations drawn into the page (text boxes, highlight, freehand ink, rectangles,
// ellipses, lines/arrows, white-out), form fields, page operations (rotate, delete, move, blank page).
// Nothing is written until Save: pdf-lib (vendor/pdflib) loads the original bytes, draws every object into
// its page (a Unicode TTF from /api/font keeps Cyrillic), applies form values, re-orders the pages and the
// bytes go to /api/save_binary (atomic write, conflict check). Objects are kept in PDF user space of the
// unrotated page, so zoom and rotation never move them; pdf.js only renders.
import { api, rawUrl, esc, icon, ICONS, t, toast, debounce, showMenu, modal, API } from "./core.js";

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
});

const COLORS = ["#e5484d", "#f76b15", "#ffc53d", "#30a46c", "#0090ff", "#8e4ec6", "#1c2024", "#ffffff"];
const HIGHLIGHTS = ["#ffe066", "#a6f4c5", "#a5d8ff", "#ffc9de", "#ffd8a8"];
const TOOLS = [["select", "cursor"], ["text", "textT"], ["highlight", "marker"], ["ink", "ink"], ["rect", "square"],
  ["ellipse", "circle"], ["line", "lineT"], ["arrow", "arrowT"], ["whiteout", "whiteout"]];
const ASCENT = 0.86, LINE_H = 1.22;
const A4 = [595.28, 841.89];

// ------------------------------------------------------------------ libraries (lazy)
let _pdfjs = null, _pdflib = null;
export async function pdfjs() {
  if (_pdfjs) return _pdfjs;
  // Round 34: pdf.js 6 needs APIs newer than Chromium 128 / older WebView2 ("a.toHex is not a function") — the
  // polyfills load before it here, and the worker entry imports them before the vendored worker.
  await import("../vendor/pdfjs/polyfills.mjs?v=r34");
  const mod = await import("../vendor/pdfjs/pdf.min.mjs?v=6.4.299");
  mod.GlobalWorkerOptions.workerSrc = new URL("../vendor/pdfjs/pdf.worker.compat.mjs?v=r34", import.meta.url).href;
  _pdfjs = mod;
  return mod;
}
export async function pdflib() { return (_pdflib ||= await import("../vendor/pdflib/pdflib.js")); }

// ------------------------------------------------------------------ geometry (same transform as pdf.js PageViewport)
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

/** Draw one object into a pdf-lib page (exported for tests). */
export function drawObject(L, page, o, font) {
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
        page.drawText(line, { x: o.x + down[0] * k, y: o.y + down[1] * k, size, font, color: col(o.color), rotate: L.degrees(o.angle || 0) });
      });
      break;
    }
  }
}

/**
 * Apply an edit model to PDF bytes with pdf-lib → new bytes (exported for tests).
 * model: {pages:[{src, rot, objs, size?}], fields:{name: value}}; src = 1-based original page, 0 = new blank page.
 */
export async function applyEdits(bytes, model, fontBytes) {
  const L = await pdflib();
  const doc = await L.PDFDocument.load(bytes, { updateMetadata: false });
  doc.registerFontkit(L.fontkit);
  let font = null;
  const needsFont = model.pages.some((p) => p.objs.some((o) => o.type === "text")) || Object.keys(model.fields || {}).length;
  if (needsFont) {
    if (fontBytes) { try { font = await doc.embedFont(fontBytes, { subset: true }); } catch (e) { console.warn("font", e); } }
    if (!font) font = await doc.embedFont(L.StandardFonts.Helvetica);
  }
  const orig = doc.getPages();
  // form values first (widgets live on the original pages)
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
  for (const p of model.pages) if (p.src) {
    const page = orig[p.src - 1];
    for (const o of p.objs) drawObject(L, page, o, font);
    if (p.rot) page.setRotation(L.degrees(((page.getRotation().angle + p.rot) % 360 + 360) % 360));
  }
  const order = model.pages.map((p) => p.src);
  const unchanged = order.length === orig.length && order.every((s, i) => s === i + 1);
  if (!unchanged) {
    for (let i = doc.getPageCount() - 1; i >= 0; i--) doc.removePage(i);
    const used = new Set();
    model.pages.forEach((p, i) => {
      if (p.src && !used.has(p.src)) { used.add(p.src); doc.insertPage(doc.getPageCount(), orig[p.src - 1]); return; }
      const size = p.size || A4;
      const page = doc.insertPage(doc.getPageCount(), size);
      for (const o of p.objs) drawObject(L, page, o, font);
      if (p.rot) page.setRotation(L.degrees(((p.rot % 360) + 360) % 360));
    });
  }
  return doc.save();
}

/** Form fields of PDF bytes: [{name, type, value, options}] (exported for tests). */
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
    } catch { /* an exotic field: skipped */ }
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

// ================================================================== the view
export class PdfView {
  constructor(host, tab) {
    this.host = host; this.tab = tab; this.scale = 1.2; this.rendered = new Map();
    this.editing = false; this.tool = "select"; this.color = COLORS[0]; this.hlColor = HIGHLIGHTS[0]; this.width = 2; this.fontSize = 14;
    this.model = null; this.undoStack = []; this.redoStack = []; this.selected = null; this.fields = null; this.fieldValues = {};
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
      <span class="spacer"></span>
      <button class="btn ghost sm" data-a="export">${icon("export")}${esc(t("pdf_export"))}${icon("chevron", "small")}</button>
      <button class="btn ghost sm ai-only" data-a="ask">${icon("sparkle")}${esc(t("pdf_ask"))}</button>
      <button class="btn sm pdf-edit-toggle" data-a="edit">${icon("pen")}${esc(t("pdf_edit"))}</button>
    </div><div class="pdf-edit-bar" hidden></div><div class="pdf-body"><div class="pdf-scroll"><div class="pdf-pages"></div><div class="pdf-loading">${esc(t("loading"))}</div></div><aside class="pdf-fields" hidden></aside></div>`;
    host.append(this.el);
    this.scroll = this.el.querySelector(".pdf-scroll"); this.wrap = this.el.querySelector(".pdf-pages");
    this.el.querySelector(".pdf-bar").addEventListener("click", (e) => { const b = e.target.closest("[data-a]"); if (b) this.action(b.dataset.a, b); });
    this.el.querySelector(".pdf-page").addEventListener("change", (e) => this.goto(+e.target.value));
    this.scroll.addEventListener("scroll", debounce(() => this.updatePage(), 60), { passive: true });
    this.scroll.addEventListener("wheel", (e) => { if (e.ctrlKey) { e.preventDefault(); this.zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1); } }, { passive: false });
    this.el.addEventListener("keydown", (e) => this.keys(e));
    this.load();
  }
  get dirty() { return this.undoStack.length > 0 && this.changed; }

  // ---------------------------------------------------------------- loading
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
      this.onLoaded?.();
    } catch (e) {
      const box = this.el.querySelector(".pdf-loading");
      if (box) box.textContent = t("pdf_failed") + ": " + (e.message || e);
    }
  }
  resetModel() {
    this.model = { pages: Array.from({ length: this.doc.numPages }, (_, i) => ({ id: uid(), src: i + 1, rot: 0, objs: [] })) };
    this.undoStack = []; this.redoStack = []; this.changed = false; this.fieldValues = {};
  }
  async pageInfo(src) {
    if (!src) return null;
    if (!this.info[src - 1]) { const p = await this.doc.getPage(src); this.info[src - 1] = { view: p.view, rotate: p.rotate }; }
    return this.info[src - 1];
  }
  /** Viewport of model page *mp* at the current scale (rotation = the page's own + the edit). */
  vpOf(mp, info) {
    if (!mp.src) { const s = mp.size || A4; return viewport([0, 0, s[0], s[1]], this.scale, mp.rot || 0); }
    info = info || this.info[mp.src - 1] || this.info[0];
    return viewport(info.view, this.scale, ((info.rotate || 0) + (mp.rot || 0)) % 360);
  }

  // ---------------------------------------------------------------- layout & rendering
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
      const page = await this.doc.getPage(mp.src);
      if (!this.rendered.has(i) || this.model.pages[i] !== mp) return;
      this.info[mp.src - 1] = { view: page.view, rotate: page.rotate };
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
      } catch (e) { /* text layer is optional */ }
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

  // ---------------------------------------------------------------- overlay (objects as SVG)
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
      case "text": {
        const [ax, ay] = vp.toView(o.x, o.y);
        const size = (o.size || 14) * vp.scale, rot = (vp.rotation - (o.angle || 0) + 360) % 360;
        const lines = String(o.text || "").split("\n");
        const tspans = lines.map((l, k) => `<tspan x="${ax}" y="${ay + ASCENT * size + k * LINE_H * size}">${esc(l) || " "}</tspan>`).join("");
        const wmax = Math.max(...lines.map((l) => l.length)) * size * 0.56, h = lines.length * LINE_H * size;
        return `<g ${attrs} transform="rotate(${rot} ${ax} ${ay})"><rect class="hit" x="${ax - 2}" y="${ay - 2}" width="${wmax + 4}" height="${h + 4}" fill="transparent"/><text font-size="${size}" fill="${esc(o.color)}" font-family="Arial, 'DejaVu Sans', 'Segoe UI', sans-serif">${tspans}</text></g>`;
      }
    }
    return "";
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
    // highlight tool: select text in the text layer, the selection becomes highlights
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

  // ---------------------------------------------------------------- model changes & history
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

  // ---------------------------------------------------------------- edit mode UI
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
      <button class="btn sm primary" data-e="save" ${this.changed ? "" : "disabled"}>${icon("save")}${esc(t("save"))}</button>`;
    bar.onclick = (e) => {
      const b = e.target.closest("[data-tool],[data-e]"); if (!b || b.disabled) return;
      if (b.dataset.tool) { this.tool = b.dataset.tool; this.select(null); this.paintEditBar(); this.redrawAll(); this.el.classList.toggle("tool-highlight", this.tool === "highlight"); return; }
      const a = b.dataset.e;
      if (a === "color") showMenu((this.tool === "highlight" ? HIGHLIGHTS : COLORS).map((c) => ({ label: c, run: () => { if (this.tool === "highlight") this.hlColor = c; else this.color = c; this.recolorSelected(c); this.paintEditBar(); } })), b);
      if (a === "color") document.querySelectorAll(".menu .mi").forEach((mi, k) => { const c = (this.tool === "highlight" ? HIGHLIGHTS : COLORS)[k]; if (c) mi.querySelector(".mi-ic").innerHTML = `<span class="swatch" style="background:${c}"></span>`; });
      if (a === "undo") this.undo();
      if (a === "redo") this.redo();
      if (a === "delete") this.deleteSelected();
      if (a === "fields") { const f = this.el.querySelector(".pdf-fields"); f.hidden = !f.hidden; this.renderFields(); this.paintEditBar(); }
      if (a === "save") this.onSave?.();
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
    else if (e.key === "Escape") { if (this.selected) this.select(null); else if (this.tool !== "select") { this.tool = "select"; this.paintEditBar(); this.redrawAll(); } }
  }

  /** Build the edited PDF and write it to *path*: {ok, mtime, size} | {conflict}. */
  async save(path, opts = {}) {
    const bytes = await this.ensureBytes();
    const model = { pages: this.model.pages, fields: this.fieldValues };
    const out = await applyEdits(bytes.slice(), model, await fontBytes() || null);
    const q = new URLSearchParams({ path, mtime: String(opts.mtime || 0), force: opts.force ? "1" : "0" });
    const r = await api("save_binary?" + q, new Blob([out], { type: "application/pdf" }), { raw: true, type: "application/pdf" });
    if (r.ok) {
      this.bytes = null; this.fields = null; this.model = null; this.selected = null;
      const keepEdit = this.editing;
      await this.load();
      if (keepEdit) { await this.ensureBytes().catch(() => {}); this.loadFields(); this.paintEditBar(); }
    }
    return r;
  }

  // ---------------------------------------------------------------- navigation
  current() { return +(this.el.querySelector(".pdf-page")?.value || 1); }
  updatePage() {
    const boxes = this.wrap.children; const mid = this.scroll.scrollTop + this.scroll.clientHeight / 3;
    for (const b of boxes) { if (b.offsetTop + b.offsetHeight > mid) { this.el.querySelector(".pdf-page").value = b.dataset.page; break; } }
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
  }

  // ---------------------------------------------------------------- text (AI context / export)
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
  destroy() { this.observer?.disconnect(); this.doc?.destroy?.(); this.el.remove(); }
}

function translate(o, dx, dy) {
  if ("x" in o) { o.x += dx; o.y += dy; }
  if (o.p1) { o.p1 = [o.p1[0] + dx, o.p1[1] + dy]; o.p2 = [o.p2[0] + dx, o.p2[1] + dy]; }
  if (o.points) o.points = o.points.map(([x, y]) => [x + dx, y + dy]);
}
function mergeRects(rects) {
  // client rects of a selection: one per text run; merge runs on the same line
  const out = [];
  for (const r of [...rects].sort((a, b) => a.top - b.top || a.left - b.left)) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.top - r.top) < 3 && Math.abs(last.bottom - r.bottom) < 3 && r.left <= last.right + 4) last.right = Math.max(last.right, r.right);
    else out.push({ left: r.left, top: r.top, right: r.right, bottom: r.bottom });
  }
  return out;
}
