// MemuNote Photo — tools. Each tool: {id, icon, key, group, cursor, down(ed, p, e), move(ed, p, e), up(ed, p, e),
// overlay(ed, ctx), activate(ed), deactivate(ed), key(ed, e) → handled}. p = document coordinates {x, y}.
import { newCanvas, ctx2d, cloneCanvas, rectUnion, rectClip, isVector, renderVector, DEFAULT_TEXT, DEFAULT_SHAPE, vectorBounds, textBox } from "./doc.js";
import { parseHex, clamp, rgbToHsl, hslToRgb } from "./pixels.js";

const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

// ------------------------------------------------------------------ brush engine (stroke buffer)
/** Stroke on a raster target: dabs accumulate in a buffer (flow), the layer shows backup + buffer × opacity. */
export class Stroke {
  constructor(ed, target, opts) {
    this.ed = ed; this.t = target; this.o = opts; this.doc = ed.doc;
    const c = target.canvas;
    this.backup = cloneCanvas(c);
    this.buf = newCanvas(c.width, c.height); this.bx = ctx2d(this.buf);
    this.box = null; this.last = null; this.residue = 0;
    this.tip = makeTip(opts.size, opts.hardness, opts.color || "#000");
    if (opts.mode === "clone") this.src = cloneCanvas(opts.sourceCanvas || c);
  }
  localPt(p) { return { x: p.x - this.t.x, y: p.y - this.t.y }; }
  dab(p, pressure = 1) {
    const s = Math.max(1, this.o.size * (this.o.pressureSize ? 0.25 + 0.75 * pressure : 1)), r = s / 2, x = this.bx;
    const flow = (this.o.flow ?? 1) * (this.o.pressureOpacity ? pressure : 1);
    x.globalAlpha = clamp(flow, 0.01, 1);
    if (this.o.mode === "clone" && this.o.offset) {
      const d = newCanvas(Math.ceil(s) + 2, Math.ceil(s) + 2), dx = ctx2d(d);
      dx.drawImage(this.src, -(p.x - r + this.o.offset.x) + 1, -(p.y - r + this.o.offset.y) + 1);
      dx.globalCompositeOperation = "destination-in"; dx.drawImage(this.tip, 1, 1, s, s);
      x.drawImage(d, p.x - r - 1, p.y - r - 1);
    } else if (this.o.pencil) {
      x.fillStyle = this.o.color; x.fillRect(Math.round(p.x - r), Math.round(p.y - r), Math.max(1, Math.round(s)), Math.max(1, Math.round(s)));
    } else x.drawImage(this.tip, p.x - r, p.y - r, s, s);
    x.globalAlpha = 1;
    this.box = rectUnion(this.box, { x: p.x - r - 2, y: p.y - r - 2, w: s + 4, h: s + 4 });
    this.dirty = rectUnion(this.dirty, { x: p.x - r - 2, y: p.y - r - 2, w: s + 4, h: s + 4 });
  }
  to(pDoc, pressure = 1) {
    const p = this.localPt(pDoc);
    if (!this.last) { this.dab(p, pressure); this.last = p; this.flush(); return; }
    const spacing = Math.max(0.5, this.o.size * (this.o.spacing ?? 0.12)), d = dist(this.last, p);
    let t = spacing - this.residue;
    while (t <= d) { const k = t / d; this.dab({ x: this.last.x + (p.x - this.last.x) * k, y: this.last.y + (p.y - this.last.y) * k }, pressure); t += spacing; }
    this.residue = d - (t - spacing);
    this.last = p; this.flush();
  }
  flush() {
    const r = this.dirty && rectClip(this.dirty, this.t.canvas.width, this.t.canvas.height); this.dirty = null; if (!r) return;
    const x = ctx2d(this.t.canvas);
    x.save(); x.beginPath(); x.rect(r.x, r.y, r.w, r.h); x.clip();
    x.clearRect(r.x, r.y, r.w, r.h); x.drawImage(this.backup, 0, 0);
    let src = this.buf;
    const sel = this.doc.selCanvas();
    if (sel && !this.t.isMask) {   // clip the stroke to the selection
      const c = this.clipC || (this.clipC = newCanvas(this.buf.width, this.buf.height)), cx = ctx2d(c);
      cx.clearRect(r.x, r.y, r.w, r.h); cx.save(); cx.beginPath(); cx.rect(r.x, r.y, r.w, r.h); cx.clip();
      cx.drawImage(this.buf, 0, 0); cx.globalCompositeOperation = "destination-in"; cx.drawImage(sel, -this.t.x, -this.t.y); cx.restore(); src = c;
    }
    x.globalAlpha = this.o.opacity ?? 1;
    x.globalCompositeOperation = this.o.erase ? "destination-out" : "source-over";
    x.drawImage(src, 0, 0);
    x.restore();
    this.ed.layerChanged(this.t.isMask ? null : { x: r.x + this.t.x, y: r.y + this.t.y, w: r.w, h: r.h });
  }
  end(label) {
    const doc = this.doc;
    doc.begin(label);
    if (this.box) doc.touchFrom(this.t.canvas, this.backup, this.box);
    doc.commit(label);
  }
}
export function makeTip(size, hardness = 1, color = "#000") {
  const s = Math.max(2, Math.ceil(Math.min(size, 512))), c = newCanvas(s, s), x = ctx2d(c), r = s / 2;
  const g = x.createRadialGradient(r, r, 0, r, r, r), [cr, cg, cb] = parseHex(color) || [0, 0, 0];
  const h = clamp(hardness, 0, 1);
  g.addColorStop(0, `rgba(${cr},${cg},${cb},1)`); g.addColorStop(Math.min(0.99, h * 0.98), `rgba(${cr},${cg},${cb},1)`); g.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
  x.fillStyle = g; x.beginPath(); x.arc(r, r, r, 0, Math.PI * 2); x.fill();
  return c;
}

// ------------------------------------------------------------------ pixel brushes (blur / sharpen / smudge / dodge / burn / sponge)
class PixelBrush {
  constructor(ed, target, opts) { this.ed = ed; this.t = target; this.o = opts; this.backup = cloneCanvas(target.canvas); this.box = null; this.last = null; this.carry = null; }
  to(pDoc) {
    const p = { x: pDoc.x - this.t.x, y: pDoc.y - this.t.y };
    const step = Math.max(1, this.o.size * 0.2);
    if (!this.last) { this.apply(p); this.last = p; return; }
    const d = dist(this.last, p);
    for (let t = step; t <= d; t += step) { const k = t / d; this.apply({ x: this.last.x + (p.x - this.last.x) * k, y: this.last.y + (p.y - this.last.y) * k }); }
    this.last = p;
  }
  apply(p) {
    const s = Math.max(2, Math.round(this.o.size)), r = s / 2, x0 = Math.round(p.x - r), y0 = Math.round(p.y - r), c = this.t.canvas;
    const rc = rectClip({ x: x0, y: y0, w: s, h: s }, c.width, c.height); if (!rc) return;
    const ctx = ctx2d(c), img = ctx.getImageData(rc.x, rc.y, rc.w, rc.h), d = img.data, w = rc.w, h = rc.h;
    const sel = this.ed.doc.selection, W = this.ed.doc.width;
    const strength = (this.o.strength ?? 50) / 100, hard = this.o.hardness ?? 0.5, mode = this.o.mode;
    const fall = (px, py) => { const dd = Math.hypot(px + rc.x - p.x, py + rc.y - p.y) / r; if (dd >= 1) return 0; return dd <= hard ? 1 : 1 - (dd - hard) / (1 - hard + 1e-6); };
    const src = new Uint8ClampedArray(d);
    if (mode === "smudge" && !this.carry) { this.carry = { data: new Uint8ClampedArray(d), w, h }; }
    for (let yy = 0; yy < h; yy++) for (let xx = 0; xx < w; xx++) {
      let f = fall(xx, yy) * strength; if (!f) continue;
      if (sel) { const dx = xx + rc.x + this.t.x, dy = yy + rc.y + this.t.y; f *= (sel[dy * W + dx] || 0) / 255; if (!f) continue; }
      const i = (yy * w + xx) * 4;
      if (mode === "blur" || mode === "sharpen") {
        let r2 = 0, g2 = 0, b2 = 0, n = 0;
        for (let ky = -1; ky <= 1; ky++) for (let kx = -1; kx <= 1; kx++) { const sx = Math.min(w - 1, Math.max(0, xx + kx)), sy = Math.min(h - 1, Math.max(0, yy + ky)), j = (sy * w + sx) * 4; r2 += src[j]; g2 += src[j + 1]; b2 += src[j + 2]; n++; }
        r2 /= n; g2 /= n; b2 /= n;
        if (mode === "blur") { d[i] += (r2 - d[i]) * f; d[i + 1] += (g2 - d[i + 1]) * f; d[i + 2] += (b2 - d[i + 2]) * f; }
        else { d[i] += (d[i] - r2) * f * 0.8; d[i + 1] += (d[i + 1] - g2) * f * 0.8; d[i + 2] += (d[i + 2] - b2) * f * 0.8; }
      } else if (mode === "dodge" || mode === "burn") {
        const k = mode === "dodge" ? 1 + 0.25 * f : 1 - 0.2 * f;
        const range = this.o.range || "midtones", l = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) / 255;
        const wgt = range === "shadows" ? 1 - l : range === "highlights" ? l : 1 - Math.abs(l - 0.5) * 2;
        const kk = 1 + (k - 1) * Math.max(0.15, wgt);
        d[i] *= kk; d[i + 1] *= kk; d[i + 2] *= kk;
      } else if (mode === "sponge") {
        const [hh, ss, ll] = rgbToHsl(d[i], d[i + 1], d[i + 2]), ns = clamp(ss + (this.o.sponge === "desaturate" ? -12 : 12) * f, 0, 100);
        const o = hslToRgb(hh, ns, ll); d[i] = o[0]; d[i + 1] = o[1]; d[i + 2] = o[2];
      } else if (mode === "smudge") {
        const cw = this.carry.w, ch = this.carry.h, cx = Math.min(cw - 1, xx), cy = Math.min(ch - 1, yy), j = (cy * cw + cx) * 4, cd = this.carry.data;
        for (let k = 0; k < 4; k++) d[i + k] += (cd[j + k] - d[i + k]) * f;
      }
    }
    if (mode === "smudge") this.carry = { data: new Uint8ClampedArray(d), w, h };
    ctx.putImageData(img, rc.x, rc.y);
    this.box = rectUnion(this.box, rc);
    this.ed.layerChanged(this.t.isMask ? null : { x: rc.x + this.t.x, y: rc.y + this.t.y, w: rc.w, h: rc.h });
  }
  end(label) { const doc = this.ed.doc; doc.begin(label); if (this.box) doc.touchFrom(this.t.canvas, this.backup, this.box); doc.commit(label); }
}

// ------------------------------------------------------------------ selection helpers
function selMode(ed, e) {
  if (e.shiftKey && e.altKey) return "intersect";
  if (e.shiftKey) return "add";
  if (e.altKey) return "subtract";
  return ed.opts.selection.mode || "new";
}
export function pathMask(ed, draw, feather = 0) {
  const doc = ed.doc, c = newCanvas(doc.width, doc.height), x = ctx2d(c, { willReadFrequently: true });
  x.fillStyle = "#fff"; draw(x);
  const d = x.getImageData(0, 0, c.width, c.height).data, m = new Uint8Array(doc.width * doc.height);
  for (let p = 0, i = 3; p < m.length; p++, i += 4) m[p] = d[i];
  return feather ? ed.feather(m, feather) : Promise.resolve(m);
}

// ------------------------------------------------------------------ tool definitions
const T = {};
const def = (o) => (T[o.id] = o);

def({ id: "move", icon: "move", key: "v", group: "move", cursor: "move",
  down(ed, p, e) {
    const doc = ed.doc; let l = doc.active;
    if (ed.opts.move.autoSelect || e.ctrlKey) { const hit = ed.layerAt(p); if (hit) { l = hit; doc.activeId = hit.id; ed.refreshPanels(); } }
    if (!l || l.locked) { if (l?.locked) ed.toast(ed.t("ph_layer_locked")); return; }
    this.drag = { start: p, layer: l, items: [] };
    const collect = (x) => { if (x.type === "group") x.children.forEach(collect); else this.drag.items.push({ l: x, x: x.x, y: x.y, text: x.text && { ...x.text }, shape: x.shape && { ...x.shape, points: x.shape.points?.map((q) => [...q]) }, svg: x.svg && { ...x.svg } }); };
    if (doc.selection && l.type === "raster") {   // move the selected pixels (a floating copy)
      const t = ed.rasterTarget(true); if (!t) { this.drag = null; return; }
      doc.begin(ed.t("ph_move_pixels"));
      const fl = newCanvas(l.canvas.width, l.canvas.height), fx = ctx2d(fl);
      fx.drawImage(l.canvas, 0, 0); fx.globalCompositeOperation = "destination-in"; fx.drawImage(doc.selCanvas(), -l.x, -l.y);
      doc.touch(l.canvas);
      if (!e.altKey) { const lx = ctx2d(l.canvas); lx.globalCompositeOperation = "destination-out"; lx.drawImage(doc.selCanvas(), -l.x, -l.y); lx.globalCompositeOperation = "source-over"; }
      this.drag.float = { canvas: fl, x: l.x, y: l.y }; this.drag.sel = doc.selection;
      doc.live = { layerId: l.id, floating: this.drag.float };
    } else { collect(l); doc.begin(ed.t("ph_tool_move")); }
  },
  move(ed, p, e) {
    const d = this.drag; if (!d) return;
    let dx = Math.round(p.x - d.start.x), dy = Math.round(p.y - d.start.y);
    if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
    d.dx = dx; d.dy = dy;
    if (d.float) { d.float.x = d.layer.x + dx; d.float.y = d.layer.y + dy; ed.doc.compDirty = true; ed.requestRender(); return; }
    for (const it of d.items) moveItem(ed, it, dx, dy);
    ed.layerChanged();
  },
  up(ed) {
    const d = this.drag; this.drag = null; if (!d) return;
    const doc = ed.doc;
    if (d.float) {
      const lx = ctx2d(d.layer.canvas); lx.drawImage(d.float.canvas, d.float.x - d.layer.x, d.float.y - d.layer.y);
      doc.live = null;
      if (d.dx || d.dy) doc.setSelection(shiftMask(d.sel, doc.width, doc.height, d.dx || 0, d.dy || 0));
      doc.commit(); return;
    }
    if (!d.dx && !d.dy) { doc.tx = null; return; }
    doc.commit();
  },
  keyHandler(ed, e) {   // arrows nudge the active layer (Shift ×10)
    const k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
    const l = ed.doc.active; if (!k || !l || l.locked) return false;
    const n = e.shiftKey ? 10 : 1, items = [];
    const collect = (x) => { if (x.type === "group") x.children.forEach(collect); else items.push({ l: x, x: x.x, y: x.y, text: x.text && { ...x.text }, shape: x.shape && { ...x.shape, points: x.shape.points?.map((q) => [...q]) }, svg: x.svg && { ...x.svg } }); };
    collect(l);
    ed.doc.begin(ed.t("ph_nudge")); for (const it of items) moveItem(ed, it, k[0] * n, k[1] * n); ed.doc.commit(); return true;
  } });
function moveItem(ed, it, dx, dy) {
  const l = it.l;
  if (l.type === "text") { l.text.x = it.text.x + dx; l.text.y = it.text.y + dy; renderVector(ed.doc, l); }
  else if (l.type === "shape") { l.shape.x = it.shape.x + dx; l.shape.y = it.shape.y + dy; if (it.shape.points) l.shape.points = it.shape.points.map(([a, b]) => [a + dx, b + dy]); renderVector(ed.doc, l); }
  else if (l.type === "svg") { l.svg.x = it.svg.x + dx; l.svg.y = it.svg.y + dy; renderVector(ed.doc, l); }
  else { l.x = it.x + dx; l.y = it.y + dy; }
}
export function shiftMask(m, w, h, dx, dy) {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) { const sy = y - dy; if (sy < 0 || sy >= h) continue; for (let x = 0; x < w; x++) { const sx = x - dx; if (sx >= 0 && sx < w) out[y * w + x] = m[sy * w + sx]; } }
  return out;
}

function marquee(id, icon, key, shape) {
  def({ id, icon, key, group: "marquee", cursor: "crosshair",
    down(ed, p, e) { this.a = p; this.b = p; this.mode = selMode(ed, e); },
    move(ed, p, e) {
      if (!this.a) return;
      let b = { ...p };
      if (e.shiftKey && !(this.mode === "add" && !e.altKey && false)) { const s = Math.max(Math.abs(b.x - this.a.x), Math.abs(b.y - this.a.y)); if (ed.opts.selection.square || e.shiftKey) { b.x = this.a.x + Math.sign(b.x - this.a.x || 1) * s; b.y = this.a.y + Math.sign(b.y - this.a.y || 1) * s; } }
      this.b = b; ed.requestRender();
    },
    async up(ed) {
      const a = this.a, b = this.b; this.a = null; if (!a) return;
      const r = { x: Math.round(Math.min(a.x, b.x)), y: Math.round(Math.min(a.y, b.y)), w: Math.round(Math.abs(b.x - a.x)), h: Math.round(Math.abs(b.y - a.y)) };
      if (r.w < 2 || r.h < 2) { if (this.mode === "new") ed.select(null, "new", ed.t("ph_deselect")); ed.requestRender(); return; }
      const m = await pathMask(ed, (x) => { x.beginPath(); if (shape === "ellipse") x.ellipse(r.x + r.w / 2, r.y + r.h / 2, r.w / 2, r.h / 2, 0, 0, Math.PI * 2); else x.rect(r.x, r.y, r.w, r.h); x.fill(); }, ed.opts.selection.feather);
      ed.select(m, this.mode, ed.t(shape === "ellipse" ? "ph_tool_ellipse_select" : "ph_tool_rect_select"));
    },
    overlay(ed, x) {
      if (!this.a) return; const A = ed.toScreen(this.a), B = ed.toScreen(this.b);
      x.save(); x.setLineDash([5, 4]); x.strokeStyle = "#fff"; x.lineWidth = 1; x.beginPath();
      if (shape === "ellipse") x.ellipse((A.x + B.x) / 2, (A.y + B.y) / 2, Math.abs(B.x - A.x) / 2, Math.abs(B.y - A.y) / 2, 0, 0, Math.PI * 2); else x.rect(A.x, A.y, B.x - A.x, B.y - A.y);
      x.stroke(); x.strokeStyle = "#000"; x.lineDashOffset = 5; x.stroke(); x.restore();
      ed.hint(`${Math.round(Math.abs(this.b.x - this.a.x))} × ${Math.round(Math.abs(this.b.y - this.a.y))}`);
    } });
}
marquee("marquee", "selRect", "m", "rect");
marquee("ellipse", "selEllipse", "m", "ellipse");

def({ id: "lasso", icon: "lasso", key: "l", group: "lasso", cursor: "crosshair",
  down(ed, p, e) { this.pts = [p]; this.mode = selMode(ed, e); },
  move(ed, p) { if (this.pts && dist(p, this.pts[this.pts.length - 1]) * ed.view.zoom > 2) { this.pts.push(p); ed.requestRender(); } },
  async up(ed) {
    const pts = this.pts; this.pts = null; if (!pts || pts.length < 3) { ed.requestRender(); return; }
    const m = await pathMask(ed, (x) => { x.beginPath(); pts.forEach((q, i) => (i ? x.lineTo(q.x, q.y) : x.moveTo(q.x, q.y))); x.closePath(); x.fill(); }, ed.opts.selection.feather);
    ed.select(m, this.mode, ed.t("ph_tool_lasso"));
  },
  overlay(ed, x) { if (!this.pts) return; drawPolyline(ed, x, this.pts, false); } });

def({ id: "polylasso", icon: "polyLasso", key: "l", group: "lasso", cursor: "crosshair",
  down(ed, p, e) {
    if (!this.pts) { this.pts = [p]; this.mode = selMode(ed, e); this.hover = p; return; }
    const first = this.pts[0];
    if (dist(first, p) * ed.view.zoom < 8 && this.pts.length > 2) return this.finish(ed);
    if (e.detail >= 2 && this.pts.length > 2) return this.finish(ed);
    this.pts.push(p);
  },
  move(ed, p) { if (this.pts) { this.hover = p; ed.requestRender(); } },
  keyHandler(ed, e) {
    if (!this.pts) return false;
    if (e.key === "Enter") { this.finish(ed); return true; }
    if (e.key === "Escape") { this.pts = null; ed.requestRender(); return true; }
    if (e.key === "Backspace") { this.pts.pop(); if (!this.pts.length) this.pts = null; ed.requestRender(); return true; }
    return false;
  },
  deactivate() { this.pts = null; },
  async finish(ed) {
    const pts = this.pts; this.pts = null; if (!pts || pts.length < 3) return;
    const m = await pathMask(ed, (x) => { x.beginPath(); pts.forEach((q, i) => (i ? x.lineTo(q.x, q.y) : x.moveTo(q.x, q.y))); x.closePath(); x.fill(); }, ed.opts.selection.feather);
    ed.select(m, this.mode, ed.t("ph_tool_polylasso"));
  },
  overlay(ed, x) { if (!this.pts) return; drawPolyline(ed, x, [...this.pts, this.hover], false); } });
function drawPolyline(ed, x, pts, close) {
  x.save(); x.beginPath(); pts.forEach((q, i) => { const s = ed.toScreen(q); i ? x.lineTo(s.x, s.y) : x.moveTo(s.x, s.y); }); if (close) x.closePath();
  x.setLineDash([5, 4]); x.strokeStyle = "#fff"; x.lineWidth = 1; x.stroke(); x.strokeStyle = "#000"; x.lineDashOffset = 5; x.stroke(); x.restore();
}

def({ id: "wand", icon: "wand", key: "w", group: "wand", cursor: "crosshair",
  async down(ed, p, e) {
    const mode = selMode(ed, e), o = ed.opts.wand;
    const src = o.sampleAll ? ed.doc.flatten() : ed.layerImageCanvas();
    const img = ctx2d(src).getImageData(0, 0, src.width, src.height);
    const m = await ed.worker({ kind: "wand", width: img.width, height: img.height, buffer: img.data.buffer, opts: { x: p.x, y: p.y, tolerance: o.tolerance, contiguous: o.contiguous, antialias: o.antialias } }, [img.data.buffer]);
    ed.select(new Uint8Array(m.mask), mode, ed.t("ph_tool_wand"));
  } });

def({ id: "quicksel", icon: "quickSel", key: "w", group: "wand", cursor: "brush",
  down(ed, p, e) { this.pts = [p]; this.mode = e.altKey ? "subtract" : ed.doc.selection ? "add" : "new"; },
  move(ed, p) { if (this.pts) { this.pts.push(p); ed.requestRender(); } },
  async up(ed) {
    const pts = this.pts; this.pts = null; if (!pts) return;
    const r = ed.opts.quicksel.size / 2, xs = pts.map((q) => q.x), ys = pts.map((q) => q.y);
    const pad = Math.max(r * 4, 40), box = { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
    const seed = await pathMask(ed, (x) => { x.lineWidth = r * 2; x.lineCap = "round"; x.lineJoin = "round"; x.strokeStyle = "#fff"; x.beginPath(); pts.forEach((q, i) => (i ? x.lineTo(q.x, q.y) : x.moveTo(q.x, q.y))); if (pts.length === 1) x.lineTo(pts[0].x + 0.1, pts[0].y); x.stroke(); });
    ed.busy(true);
    try {
      const m = await ed.segmentBox(box, { seed, wholeBox: false });
      // the stroke itself always belongs to the result
      for (let i = 0; i < m.length; i++) if (seed[i] > 128) m[i] = 255;
      ed.select(m, this.mode, ed.t("ph_tool_quicksel"));
    } finally { ed.busy(false); }
  },
  overlay(ed, x) { if (!this.pts) return; x.save(); x.strokeStyle = "rgba(76,141,255,.6)"; x.lineWidth = ed.opts.quicksel.size * ed.view.zoom; x.lineCap = "round"; x.lineJoin = "round"; x.beginPath(); this.pts.forEach((q, i) => { const s = ed.toScreen(q); i ? x.lineTo(s.x, s.y) : x.moveTo(s.x, s.y); }); x.stroke(); x.restore(); } });

def({ id: "objsel", icon: "objSel", key: "w", group: "wand", cursor: "crosshair",
  down(ed, p, e) { this.a = p; this.b = p; this.mode = selMode(ed, e); },
  move(ed, p) { if (this.a) { this.b = p; ed.requestRender(); } },
  async up(ed) {
    const a = this.a, b = this.b; this.a = null; if (!a) return;
    const box = { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) };
    if (box.w < 6 || box.h < 6) return;
    ed.busy(true); try { const m = await ed.segmentBox(box, {}); ed.select(m, this.mode, ed.t("ph_tool_objsel")); } finally { ed.busy(false); }
  },
  overlay(ed, x) { if (!this.a) return; const A = ed.toScreen(this.a), B = ed.toScreen(this.b); x.save(); x.strokeStyle = "#4c8dff"; x.setLineDash([6, 4]); x.lineWidth = 1.5; x.strokeRect(A.x, A.y, B.x - A.x, B.y - A.y); x.restore(); } });

def({ id: "crop", icon: "crop", key: "c", group: "crop", cursor: "crosshair",
  activate(ed) { this.box = { x: 0, y: 0, w: ed.doc.width, h: ed.doc.height }; ed.requestRender(); ed.renderOptions(); },
  deactivate(ed) { this.box = null; this.drag = null; ed.requestRender(); },
  handleAt(ed, p) {
    const b = this.box, tol = 9 / ed.view.zoom, hs = [];
    for (const [hx, kx] of [[b.x, "l"], [b.x + b.w / 2, ""], [b.x + b.w, "r"]]) for (const [hy, ky] of [[b.y, "t"], [b.y + b.h / 2, ""], [b.y + b.h, "b"]]) if (kx || ky) hs.push({ x: hx, y: hy, k: ky + kx });
    const h = hs.find((q) => Math.abs(q.x - p.x) < tol && Math.abs(q.y - p.y) < tol);
    if (h) return h.k;
    if (p.x > b.x && p.y > b.y && p.x < b.x + b.w && p.y < b.y + b.h) return "move";
    return "new";
  },
  down(ed, p) {
    if (!this.box) this.activate(ed);
    let k = this.handleAt(ed, p); const b = this.box;
    if (k === "move" && b.x <= 0 && b.y <= 0 && b.w >= ed.doc.width && b.h >= ed.doc.height) k = "new";   // untouched full-canvas box: drag draws a new one
    this.drag = { k, start: p, box: { ...b } }; if (k === "new") this.box = { x: p.x, y: p.y, w: 0, h: 0 };
  },
  move(ed, p, e) {
    const d = this.drag; if (!d) { if (this.box) ed.setCursor(cursorFor(this.handleAt(ed, p))); return; }
    const b = { ...d.box }, dx = p.x - d.start.x, dy = p.y - d.start.y;
    if (d.k === "move") { b.x += dx; b.y += dy; }
    else if (d.k === "new") { b.x = Math.min(d.start.x, p.x); b.y = Math.min(d.start.y, p.y); b.w = Math.abs(p.x - d.start.x); b.h = Math.abs(p.y - d.start.y); }
    else { if (d.k.includes("l")) { b.x += dx; b.w -= dx; } if (d.k.includes("r")) b.w += dx; if (d.k.includes("t")) { b.y += dy; b.h -= dy; } if (d.k.includes("b")) b.h += dy; }
    const ratio = ed.cropRatio();
    if (ratio && d.k !== "move") { const hh = b.w / ratio; if (d.k.includes("t")) b.y += b.h - hh; b.h = hh; }
    if (b.w < 0) { b.x += b.w; b.w = -b.w; } if (b.h < 0) { b.y += b.h; b.h = -b.h; }
    this.box = b; ed.requestRender(); ed.hint(`${Math.round(b.w)} × ${Math.round(b.h)}`);
  },
  up() { this.drag = null; },
  keyHandler(ed, e) {
    if (e.key === "Enter") { this.apply(ed); return true; }
    if (e.key === "Escape") { this.activate(ed); return true; }
    return false;
  },
  apply(ed) { if (!this.box) return; const b = this.box; if (b.w < 1 || b.h < 1) return; ed.cropTo({ x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.w), h: Math.round(b.h) }, ed.opts.crop.angle || 0); this.activate(ed); },
  overlay(ed, x) {
    const b = this.box; if (!b) return; const A = ed.toScreen(b), B = ed.toScreen({ x: b.x + b.w, y: b.y + b.h }), W = x.canvas.width / ed.dpr, H = x.canvas.height / ed.dpr;
    x.save(); x.fillStyle = "rgba(0,0,0,.5)"; x.beginPath(); x.rect(0, 0, W, H); x.rect(A.x, A.y, B.x - A.x, B.y - A.y); x.fill("evenodd");
    if (ed.opts.crop.angle) { x.translate((A.x + B.x) / 2, (A.y + B.y) / 2); x.rotate((-ed.opts.crop.angle * Math.PI) / 180); x.translate(-(A.x + B.x) / 2, -(A.y + B.y) / 2); }
    x.strokeStyle = "rgba(255,255,255,.9)"; x.lineWidth = 1; x.strokeRect(A.x + 0.5, A.y + 0.5, B.x - A.x, B.y - A.y);
    x.strokeStyle = "rgba(255,255,255,.35)"; x.beginPath(); for (const k of [1, 2]) { const gx = A.x + ((B.x - A.x) * k) / 3, gy = A.y + ((B.y - A.y) * k) / 3; x.moveTo(gx, A.y); x.lineTo(gx, B.y); x.moveTo(A.x, gy); x.lineTo(B.x, gy); } x.stroke();
    x.fillStyle = "#fff"; for (const hx of [A.x, (A.x + B.x) / 2, B.x]) for (const hy of [A.y, (A.y + B.y) / 2, B.y]) if (hx !== (A.x + B.x) / 2 || hy !== (A.y + B.y) / 2) x.fillRect(hx - 4, hy - 4, 8, 8);
    x.restore();
  } });
function cursorFor(k) { return { move: "move", new: "crosshair", tl: "nwse-resize", br: "nwse-resize", tr: "nesw-resize", bl: "nesw-resize", t: "ns-resize", b: "ns-resize", l: "ew-resize", r: "ew-resize" }[k] || "default"; }

function paintTool(id, icon, key, group, extra = {}) {
  def({ id, icon, key, group, cursor: "brush", ...extra,
    down(ed, p, e) {
      if (e.altKey && id !== "eraser" && id !== "clone") { T.eyedropper.down(ed, p, e); return; }
      const t = ed.rasterTarget(true); if (!t) return;
      const o = ed.opts[id] || ed.opts.brush;
      const color = t.isMask ? (id === "eraser" ? "#000" : (ed.fg === "#000000" || ed.maskPaintBlack ? "#000" : "#fff")) : ed.fg;
      const erase = id === "eraser" ? !t.isMask : t.isMask && color === "#000";
      this.s = new Stroke(ed, t, { size: o.size, hardness: id === "pencil" ? 1 : o.hardness / 100, opacity: o.opacity / 100, flow: (o.flow ?? 100) / 100, color: erase ? "#000" : color, erase, pencil: id === "pencil", spacing: o.spacing ? o.spacing / 100 : 0.12, pressureSize: o.pressure, pressureOpacity: false });
      if (e.shiftKey && this.lastPt && this.lastLayer === t.canvas) { this.s.to(this.lastPt); }
      this.s.to(p, e.pressure || 1);
    },
    move(ed, p, e) { if (this.s) this.s.to(p, e.pressure || 1); },
    up(ed, p) { if (!this.s) return; this.s.end(ed.t("ph_tool_" + id)); this.lastPt = p; this.lastLayer = this.s.t.canvas; this.s = null; } });
}
paintTool("brush", "brush", "b", "brush");
paintTool("pencil", "pencil", "b", "brush");
paintTool("eraser", "eraser", "e", "eraser");

def({ id: "clone", icon: "stamp", key: "s", group: "clone", cursor: "brush",
  down(ed, p, e) {
    if (e.altKey) { this.source = { ...p }; this.offset = null; ed.toast(ed.t("ph_clone_source_set")); return; }
    if (!this.source) { ed.toast(ed.t("ph_clone_need_source")); return; }
    const t = ed.rasterTarget(true); if (!t) return;
    if (!this.offset || !ed.opts.clone.aligned) this.offset = { x: this.source.x - p.x, y: this.source.y - p.y };
    const o = ed.opts.clone;
    const src = ed.opts.clone.sampleAll ? ed.doc.flatten() : null;
    this.s = new Stroke(ed, t, { size: o.size, hardness: o.hardness / 100, opacity: o.opacity / 100, flow: 1, mode: "clone", offset: this.offset, sourceCanvas: src });
    this.s.to(p);
  },
  move(ed, p) { if (this.s) this.s.to(p); this.hover = p; ed.requestRender(); },
  up(ed) { if (!this.s) return; this.s.end(ed.t("ph_tool_clone")); this.s = null; },
  overlay(ed, x) {
    if (!this.source) return; const sp = this.offset && this.hover ? ed.toScreen({ x: this.hover.x + this.offset.x, y: this.hover.y + this.offset.y }) : ed.toScreen(this.source);
    x.save(); x.strokeStyle = "#fff"; x.lineWidth = 1.5; x.beginPath(); x.moveTo(sp.x - 8, sp.y); x.lineTo(sp.x + 8, sp.y); x.moveTo(sp.x, sp.y - 8); x.lineTo(sp.x, sp.y + 8); x.stroke(); x.strokeStyle = "#000"; x.lineWidth = 0.6; x.stroke(); x.restore();
  } });

def({ id: "heal", icon: "heal", key: "j", group: "heal", cursor: "brush",
  down(ed, p) { const t = ed.rasterTarget(true); if (!t || t.isMask) return; this.t = t; this.pts = [p]; ed.requestRender(); },
  move(ed, p) { if (this.pts) { this.pts.push(p); ed.requestRender(); } },
  async up(ed) {
    const pts = this.pts, t = this.t; this.pts = null; if (!pts) return;
    const r = ed.opts.heal.size / 2;
    const xs = pts.map((q) => q.x), ys = pts.map((q) => q.y), pad = r * 3 + 8;
    const box = rectClip({ x: Math.min(...xs) - pad - t.x, y: Math.min(...ys) - pad - t.y, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 }, t.canvas.width, t.canvas.height);
    if (!box) return;
    const mc = newCanvas(box.w, box.h), mx = ctx2d(mc, { willReadFrequently: true });
    mx.lineWidth = r * 2; mx.lineCap = "round"; mx.lineJoin = "round"; mx.strokeStyle = "#fff"; mx.beginPath();
    pts.forEach((q, i) => { const X = q.x - t.x - box.x, Y = q.y - t.y - box.y; i ? mx.lineTo(X, Y) : mx.moveTo(X, Y); }); if (pts.length === 1) mx.lineTo(pts[0].x - t.x - box.x + 0.1, pts[0].y - t.y - box.y); mx.stroke();
    const md = mx.getImageData(0, 0, box.w, box.h).data, mask = new Uint8Array(box.w * box.h); for (let i = 0; i < mask.length; i++) mask[i] = md[i * 4 + 3];
    const ctx = ctx2d(t.canvas), img = ctx.getImageData(box.x, box.y, box.w, box.h);
    ed.busy(true);
    try {
      const res = await ed.worker({ kind: "inpaint", width: box.w, height: box.h, buffer: img.data.buffer, mask: mask.buffer, opts: { noise: 3 } }, [img.data.buffer, mask.buffer]);
      ed.doc.begin(ed.t("ph_tool_heal")); ed.doc.touch(t.canvas, box);
      ctx.putImageData(new ImageData(new Uint8ClampedArray(res.buffer), box.w, box.h), box.x, box.y);
      ed.doc.commit();
    } finally { ed.busy(false); }
  },
  overlay(ed, x) { if (!this.pts) return; x.save(); x.strokeStyle = "rgba(255,80,80,.55)"; x.lineWidth = ed.opts.heal.size * ed.view.zoom; x.lineCap = "round"; x.lineJoin = "round"; x.beginPath(); this.pts.forEach((q, i) => { const s = ed.toScreen(q); i ? x.lineTo(s.x, s.y) : x.moveTo(s.x, s.y); }); x.stroke(); x.restore(); } });

function pixelTool(id, icon, key, group, mode) {
  def({ id, icon, key, group, cursor: "brush",
    down(ed, p) { const t = ed.rasterTarget(true); if (!t || t.isMask) return; const o = ed.opts[id]; this.b = new PixelBrush(ed, t, { size: o.size, strength: o.strength, hardness: o.hardness / 100, mode, range: o.range, sponge: o.sponge }); this.b.to(p); },
    move(ed, p) { this.b?.to(p); },
    up(ed) { if (!this.b) return; this.b.end(ed.t("ph_tool_" + id)); this.b = null; } });
}
pixelTool("blur", "drop", "r", "blur", "blur");
pixelTool("sharpen", "triangle", "r", "blur", "sharpen");
pixelTool("smudge", "finger", "r", "blur", "smudge");
pixelTool("dodge", "dodge", "o", "dodge", "dodge");
pixelTool("burn", "burn", "o", "dodge", "burn");
pixelTool("sponge", "sponge", "o", "dodge", "sponge");

def({ id: "bucket", icon: "bucket", key: "g", group: "fill", cursor: "crosshair",
  async down(ed, p) {
    const t = ed.rasterTarget(true); if (!t) return;
    const o = ed.opts.bucket;
    const src = o.sampleAll ? ed.doc.flatten() : t.canvas, ox = o.sampleAll ? 0 : t.x, oy = o.sampleAll ? 0 : t.y;
    const img = ctx2d(src).getImageData(0, 0, src.width, src.height);
    const res = await ed.worker({ kind: "wand", width: img.width, height: img.height, buffer: img.data.buffer, opts: { x: p.x - ox, y: p.y - oy, tolerance: o.tolerance, contiguous: o.contiguous, antialias: true } }, [img.data.buffer]);
    let m = new Uint8Array(res.mask);
    const mc = newCanvas(img.width, img.height), mx = ctx2d(mc), mi = mx.createImageData(img.width, img.height);
    const col = parseHex(t.isMask ? (ed.fg === "#000000" ? "#000000" : "#ffffff") : ed.fg);
    for (let q = 0; q < m.length; q++) { const i = q * 4; mi.data[i] = col[0]; mi.data[i + 1] = col[1]; mi.data[i + 2] = col[2]; mi.data[i + 3] = m[q]; }
    mx.putImageData(mi, 0, 0);
    if (ed.doc.selection) { mx.globalCompositeOperation = "destination-in"; mx.drawImage(ed.doc.selCanvas(), -ox, -oy); }
    ed.doc.begin(ed.t("ph_tool_bucket")); ed.doc.touch(t.canvas);
    const tx = ctx2d(t.canvas); tx.globalAlpha = o.opacity / 100;
    if (t.isMask && ed.fg === "#000000") { tx.globalCompositeOperation = "destination-out"; }
    tx.drawImage(mc, ox - t.x, oy - t.y); tx.globalAlpha = 1; tx.globalCompositeOperation = "source-over";
    ed.doc.commit();
  } });

def({ id: "gradient", icon: "gradient", key: "g", group: "fill", cursor: "crosshair",
  down(ed, p) { const t = ed.rasterTarget(true); if (!t) return; this.t = t; this.a = p; this.b = p; },
  move(ed, p, e) {
    if (!this.a) return;
    let b = p; if (e.shiftKey) { const ang = Math.round(Math.atan2(p.y - this.a.y, p.x - this.a.x) / (Math.PI / 4)) * (Math.PI / 4), d = dist(this.a, p); b = { x: this.a.x + Math.cos(ang) * d, y: this.a.y + Math.sin(ang) * d }; }
    this.b = b; this.preview(ed);
  },
  preview(ed) {
    const t = this.t, c = newCanvas(t.canvas.width, t.canvas.height), x = ctx2d(c);
    x.drawImage(t.canvas, 0, 0); this.paint(ed, x, t);
    ed.doc.live = { layerId: t.layerId, canvas: t.isMask ? t.layer.canvas : c, x: t.layer.x, y: t.layer.y, maskCanvas: t.isMask ? c : null };
    ed.layerChanged();
  },
  paint(ed, x, t) {
    const o = ed.opts.gradient, a = { x: this.a.x - t.x, y: this.a.y - t.y }, b = { x: this.b.x - t.x, y: this.b.y - t.y };
    const g = o.type === "radial" ? x.createRadialGradient(a.x, a.y, 0, a.x, a.y, Math.max(1, dist(a, b))) : x.createLinearGradient(a.x, a.y, b.x, b.y);
    const c1 = t.isMask ? "#ffffff" : ed.fg, c2 = t.isMask ? "rgba(255,255,255,0)" : o.toTransparent ? hexA(ed.fg, 0) : ed.bg;
    if (o.reverse) { g.addColorStop(0, c2); g.addColorStop(1, c1); } else { g.addColorStop(0, c1); g.addColorStop(1, c2); }
    const fill = newCanvas(x.canvas.width, x.canvas.height), fx = ctx2d(fill); fx.fillStyle = g; fx.fillRect(0, 0, fill.width, fill.height);
    if (ed.doc.selection && !t.isMask) { fx.globalCompositeOperation = "destination-in"; fx.drawImage(ed.doc.selCanvas(), -t.x, -t.y); }
    if (t.isMask) x.clearRect(0, 0, x.canvas.width, x.canvas.height);
    x.globalAlpha = o.opacity / 100; x.drawImage(fill, 0, 0); x.globalAlpha = 1;
  },
  up(ed) {
    if (!this.a) return; const t = this.t; const moved = dist(this.a, this.b) > 1;
    ed.doc.live = null;
    if (moved) { ed.doc.begin(ed.t("ph_tool_gradient")); ed.doc.touch(t.canvas); this.paint(ed, ctx2d(t.canvas), t); ed.doc.commit(); }
    this.a = null; ed.layerChanged();
  },
  overlay(ed, x) { if (!this.a) return; const A = ed.toScreen(this.a), B = ed.toScreen(this.b); x.save(); x.strokeStyle = "#fff"; x.lineWidth = 1.5; x.beginPath(); x.moveTo(A.x, A.y); x.lineTo(B.x, B.y); x.stroke(); x.fillStyle = "#fff"; x.beginPath(); x.arc(A.x, A.y, 4, 0, 7); x.arc(B.x, B.y, 4, 0, 7); x.fill(); x.restore(); } });
function hexA(hex, a) { const c = parseHex(hex) || [0, 0, 0]; return `rgba(${c[0]},${c[1]},${c[2]},${a})`; }

def({ id: "eyedropper", icon: "eyedropper", key: "i", group: "eyedropper", cursor: "crosshair",
  down(ed, p, e) { this.pick(ed, p, e); this.on = true; }, move(ed, p, e) { if (this.on) this.pick(ed, p, e); }, up() { this.on = false; },
  pick(ed, p, e) {
    const src = ed.opts.eyedropper.sampleAll === false ? ed.layerImageCanvas() : ed.doc.composite();
    const n = Math.max(1, ed.opts.eyedropper.size || 1), x0 = Math.max(0, Math.round(p.x - (n - 1) / 2)), y0 = Math.max(0, Math.round(p.y - (n - 1) / 2));
    if (x0 >= src.width || y0 >= src.height) return;
    const d = ctx2d(src).getImageData(x0, y0, Math.min(n, src.width - x0), Math.min(n, src.height - y0)).data;
    let r = 0, g = 0, b = 0, a = 0, k = 0; for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; a += d[i + 3]; k++; }
    if (!a) return;
    const hex = "#" + [r, g, b].map((v) => Math.round(v / k).toString(16).padStart(2, "0")).join("");
    if (e.altKey && !(this.id !== "eyedropper")) ed.setColor("bg", hex); else ed.setColor("fg", hex);
  } });

def({ id: "text", icon: "text", key: "t", group: "text", cursor: "text",
  down(ed, p) {
    const hit = ed.layerAt(p, (l) => l.type === "text");
    if (hit) { ed.doc.activeId = hit.id; ed.editText(hit); return; }
    const o = ed.opts.text;
    const l = ed.doc.makeLayer({ type: "text", name: ed.t("ph_layer_text"), text: { ...DEFAULT_TEXT, font: o.font, size: o.size, color: ed.fg, bold: o.bold, italic: o.italic, underline: o.underline, align: o.align, stroke: o.stroke, strokeColor: ed.bg, shadow: o.shadow, x: Math.round(p.x), y: Math.round(p.y - o.size * 0.6) } });
    ed.editText(l, true);
  } });

def({ id: "shape", icon: "shapes", key: "u", group: "shape", cursor: "crosshair",
  down(ed, p) {
    const o = ed.opts.shape;
    this.a = p;
    if (o.kind === "path") { this.pts = [[p.x, p.y]]; }
    const s = { ...DEFAULT_SHAPE, kind: o.kind, x: p.x, y: p.y, w: 0, h: 0, fill: o.noFill ? "" : ed.fg, stroke: o.strokeWidth ? ed.bg : "", strokeWidth: o.strokeWidth, radius: o.radius, sides: o.sides };
    if (s.kind === "line" || s.kind === "arrow" || s.kind === "path") { s.stroke = ed.fg; s.strokeWidth = Math.max(1, o.strokeWidth || 4); s.fill = s.kind === "path" && !o.noFill ? "" : s.fill; }
    if (s.kind === "path") s.points = this.pts;
    ed.doc.begin(ed.t("ph_tool_shape"));
    this.layer = ed.doc.insertLayer(ed.doc.makeLayer({ type: "shape", name: ed.t("ph_shape_" + o.kind), shape: s }));
    renderVector(ed.doc, this.layer); ed.refreshPanels();
  },
  move(ed, p, e) {
    const l = this.layer; if (!l) return; const s = l.shape;
    if (s.kind === "path") { this.pts.push([p.x, p.y]); s.points = this.pts; }
    else {
      let w = p.x - this.a.x, h = p.y - this.a.y;
      if (e.shiftKey) { if (s.kind === "line" || s.kind === "arrow") { const ang = Math.round(Math.atan2(h, w) / (Math.PI / 4)) * (Math.PI / 4), d = Math.hypot(w, h); w = Math.cos(ang) * d; h = Math.sin(ang) * d; } else { const m = Math.max(Math.abs(w), Math.abs(h)); w = Math.sign(w || 1) * m; h = Math.sign(h || 1) * m; } }
      if (e.altKey && s.kind !== "line" && s.kind !== "arrow") { s.x = this.a.x - w; s.y = this.a.y - h; s.w = w * 2; s.h = h * 2; } else { s.x = this.a.x; s.y = this.a.y; s.w = w; s.h = h; }
    }
    renderVector(ed.doc, l); ed.layerChanged();
  },
  up(ed) {
    const l = this.layer; this.layer = null; if (!l) return;
    const s = l.shape;
    if (s.kind !== "path" && s.kind !== "line" && s.kind !== "arrow") { if (s.w < 0) { s.x += s.w; s.w = -s.w; } if (s.h < 0) { s.y += s.h; s.h = -s.h; } }
    const tiny = s.kind === "path" ? (s.points?.length || 0) < 2 : Math.hypot(s.w, s.h) < 2;
    if (tiny) { ed.doc.cancel(); ed.refreshPanels(); return; }
    renderVector(ed.doc, l); ed.doc.commit(); ed.refreshPanels();
  } });

def({ id: "hand", icon: "hand", key: "h", group: "nav", cursor: "grab",
  down(ed, p, e) { this.s = { x: e.clientX, y: e.clientY, px: ed.view.panX, py: ed.view.panY }; ed.setCursor("grabbing"); },
  move(ed, p, e) { if (!this.s) return; ed.view.panX = this.s.px + e.clientX - this.s.x; ed.view.panY = this.s.py + e.clientY - this.s.y; ed.clampPan(); ed.requestRender(); ed.renderRulers(); },
  up(ed) { this.s = null; ed.setCursor("grab"); } });
def({ id: "zoom", icon: "zoomIn", key: "z", group: "nav", cursor: "zoom-in",
  down(ed, p, e) { ed.zoomAt(e.altKey ? 1 / 1.5 : 1.5, e.clientX, e.clientY); } });

export const TOOLS = T;
// The toolbar: groups (one button each, right-click / long press opens the group like Photoshop's flyouts).
export const TOOL_GROUPS = [
  ["move", ["move"]], ["marquee", ["marquee", "ellipse"]], ["lasso", ["lasso", "polylasso"]], ["wand", ["quicksel", "wand", "objsel"]],
  ["crop", ["crop"]], ["eyedropper", ["eyedropper"]], ["heal", ["heal"]], ["brush", ["brush", "pencil"]], ["clone", ["clone"]],
  ["eraser", ["eraser"]], ["fill", ["gradient", "bucket"]], ["blur", ["blur", "sharpen", "smudge"]], ["dodge", ["dodge", "burn", "sponge"]],
  ["text", ["text"]], ["shape", ["shape"]], ["nav", ["hand", "zoom"]],
];
export const DEFAULT_OPTS = () => ({
  selection: { mode: "new", feather: 0 }, move: { autoSelect: false },
  wand: { tolerance: 32, contiguous: true, antialias: true, sampleAll: false }, quicksel: { size: 30 },
  crop: { ratio: "free", angle: 0 },
  brush: { size: 24, hardness: 80, opacity: 100, flow: 100, pressure: true }, pencil: { size: 4, hardness: 100, opacity: 100, flow: 100 },
  eraser: { size: 40, hardness: 70, opacity: 100, flow: 100 }, clone: { size: 40, hardness: 60, opacity: 100, aligned: true, sampleAll: false },
  heal: { size: 30 }, blur: { size: 40, strength: 50, hardness: 40 }, sharpen: { size: 40, strength: 40, hardness: 40 }, smudge: { size: 30, strength: 60, hardness: 40 },
  dodge: { size: 50, strength: 30, hardness: 30, range: "midtones" }, burn: { size: 50, strength: 30, hardness: 30, range: "midtones" }, sponge: { size: 50, strength: 40, hardness: 30, sponge: "saturate" },
  bucket: { tolerance: 32, contiguous: true, opacity: 100, sampleAll: false }, gradient: { type: "linear", opacity: 100, reverse: false, toTransparent: false },
  eyedropper: { size: 1, sampleAll: true }, text: { font: "Segoe UI", size: 64, bold: false, italic: false, underline: false, align: "left", stroke: 0, shadow: false },
  shape: { kind: "rect", strokeWidth: 0, radius: 0, sides: 5, noFill: false },
});
export { vectorBounds, textBox, isVector };
