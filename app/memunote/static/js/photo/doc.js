

export const uidL = () => "l" + Math.random().toString(36).slice(2, 9);
export function newCanvas(w, h) { const c = document.createElement("canvas"); c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h)); return c; }
export const ctx2d = (c, opts) => c.getContext("2d", { willReadFrequently: false, ...(opts || {}) });
export function cloneCanvas(src) { const c = newCanvas(src.width, src.height); ctx2d(c).drawImage(src, 0, 0); return c; }
const CSS_BLEND = (b) => (!b || b === "normal" ? "source-over" : b);
const HISTORY_MAX = 80, HISTORY_BYTES = 1.2e9;

export function rectUnion(a, b) { if (!a) return b; if (!b) return a; const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y); return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y }; }
export function rectClip(r, w, h) { const x = Math.max(0, Math.floor(r.x)), y = Math.max(0, Math.floor(r.y)); const x2 = Math.min(w, Math.ceil(r.x + r.w)), y2 = Math.min(h, Math.ceil(r.y + r.h)); return x2 > x && y2 > y ? { x, y, w: x2 - x, h: y2 - y } : null; }

function copyLayer(l) {
  const o = { ...l };
  if (l.text) o.text = { ...l.text };
  if (l.shape) o.shape = { ...l.shape, points: l.shape.points ? l.shape.points.map((p) => [...p]) : undefined };
  if (l.svg) o.svg = { ...l.svg };
  if (l.children) o.children = l.children.map(copyLayer);
  return o;
}

export class PhotoDoc {
  constructor(width, height) {
    this.width = width; this.height = height;
    this.layers = []; this.activeId = null; this.target = "layer";
    this.selection = null; this.lastSelection = null; this._selCanvas = null;
    this.history = []; this.future = []; this.savedIndex = 0; this.listeners = new Set();
    this.comp = newCanvas(width, height); this.compDirty = true; this.live = null; this.pool = [];
    this.guides = []; this.tx = null; this.group = null; this.version = 0;
  }
  on(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  emit(kind) { this.version++; this.compDirty = true; for (const fn of this.listeners) fn(kind); }

  walk(fn, list = this.layers, parent = null, depth = 0) { for (let i = 0; i < list.length; i++) { const l = list[i]; if (fn(l, list, i, parent, depth) === false) return false; if (l.children && this.walk(fn, l.children, l, depth + 1) === false) return false; } return true; }
  find(id) { let r = null; this.walk((l, list, i, parent) => { if (l.id === id) { r = { layer: l, list, index: i, parent }; return false; } }); return r; }
  get active() { return this.find(this.activeId)?.layer || null; }
  flat() { const out = []; this.walk((l) => out.push(l)); return out; }
  rasterLayers() { return this.flat().filter((l) => l.type !== "group"); }
  makeLayer(o = {}) {
    const type = o.type || "raster";
    const l = { id: uidL(), name: o.name || "Layer", type, x: 0, y: 0, opacity: 1, blend: "normal", visible: true, locked: false, mask: null, maskOn: true, ...o };
    if (type !== "group" && !l.canvas) l.canvas = newCanvas(this.width, this.height);
    if (type === "group" && !l.children) { l.children = []; l.open = true; }
    return l;
  }
  insertLayer(layer, above = this.activeId) {
    const at = above ? this.find(above) : null;
    if (at && at.layer.type === "group" && at.layer.open && above === this.activeId && layer.type !== "group" && false) at.layer.children.push(layer);
    else if (at) at.list.splice(at.index + 1, 0, layer);
    else this.layers.push(layer);
    this.activeId = layer.id; this.target = "layer";
    return layer;
  }
  removeLayer(id) { const f = this.find(id); if (!f) return; f.list.splice(f.index, 1); if (this.activeId === id) { const next = f.list[Math.min(f.index, f.list.length - 1)] || f.parent || this.flat().pop(); this.activeId = next?.id || null; } }
  layerBounds(l) {
    if (l.type === "group") { let r = null; for (const c of l.children) r = rectUnion(r, this.layerBounds(c)); return r || { x: 0, y: 0, w: 0, h: 0 }; }
    return { x: l.x, y: l.y, w: l.canvas.width, h: l.canvas.height };
  }

  setSelection(mask) {
    if (mask && !mask.some((v) => v)) mask = null;
    if (this.selection) this.lastSelection = this.selection;
    this.selection = mask; this._selCanvas = null; this.selVersion = (this.selVersion || 0) + 1;
  }
  selCanvas() {
    if (!this.selection) return null;
    if (this._selCanvas) return this._selCanvas;
    const c = newCanvas(this.width, this.height), x = ctx2d(c), img = x.createImageData(this.width, this.height), m = this.selection, d = img.data;
    for (let p = 0, i = 3; p < m.length; p++, i += 4) d[i] = m[p];
    x.putImageData(img, 0, 0); this._selCanvas = c; return c;
  }
  selectionBounds() {
    const m = this.selection; if (!m) return null;
    const w = this.width, h = this.height; let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) { const row = y * w; let any = false; for (let x = 0; x < w; x++) if (m[row + x]) { any = true; if (x < x0) x0 = x; if (x > x1) x1 = x; } if (any) { if (y < y0) y0 = y; y1 = y; } }
    return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  }

  tmp(depth) { let c = this.pool[depth]; if (!c || c.width !== this.width || c.height !== this.height) { c = this.pool[depth] = newCanvas(this.width, this.height); } const x = ctx2d(c); x.setTransform(1, 0, 0, 1, 0, 0); x.globalAlpha = 1; x.globalCompositeOperation = "source-over"; x.clearRect(0, 0, c.width, c.height); return c; }
  sourceOf(l) { return this.live && this.live.layerId === l.id && this.live.canvas ? this.live : { canvas: l.canvas, x: l.x, y: l.y }; }
  drawList(ctx, list, depth, opts = {}) {
    for (const l of list) {
      if (!l.visible && !(opts.only && opts.only === l.id)) continue;
      if (opts.only && !this.containsId(l, opts.only)) continue;
      let src, sx, sy;
      if (l.type === "group") {
        const t = this.tmp(depth + 1); this.drawList(ctx2d(t), l.children, depth + 1, opts.only === l.id ? {} : opts); src = t; sx = 0; sy = 0;
      } else { const s = this.sourceOf(l); src = s.canvas; sx = s.x ?? l.x; sy = s.y ?? l.y; }
      const liveExtra = this.live && this.live.layerId === l.id && this.live.overlay ? this.live.overlay : null;
      if ((l.mask && l.maskOn) || liveExtra) {
        const t = this.tmp(depth + 8), tx = ctx2d(t);
        tx.drawImage(src, sx, sy);
        if (liveExtra) { tx.globalCompositeOperation = liveExtra.op || "source-over"; tx.globalAlpha = liveExtra.alpha ?? 1; tx.drawImage(liveExtra.canvas, liveExtra.x || 0, liveExtra.y || 0); tx.globalAlpha = 1; }
        if (l.mask && l.maskOn) { tx.globalCompositeOperation = "destination-in"; const m = this.live?.maskCanvas && this.live.layerId === l.id ? this.live.maskCanvas : l.mask; tx.drawImage(m, l.type === "group" ? 0 : l.x, l.type === "group" ? 0 : l.y); }
        tx.globalCompositeOperation = "source-over";
        src = t; sx = 0; sy = 0;
      }
      ctx.globalAlpha = l.opacity; ctx.globalCompositeOperation = CSS_BLEND(l.blend);
      ctx.drawImage(src, sx, sy);
      ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
      if (this.live?.floating && this.live.layerId === l.id) { const f = this.live.floating; ctx.globalAlpha = l.opacity; ctx.drawImage(f.canvas, f.x, f.y); ctx.globalAlpha = 1; }
    }
  }
  containsId(l, id) { if (l.id === id) return true; return !!l.children?.some((c) => this.containsId(c, id)); }

  markDirtyRect(r) { if (!this.compDirty && r) this.compRect = rectUnion(this.compRect, r); else this.compDirty = true; }
  composite() {
    if (!this.compDirty && this.comp.width === this.width && this.comp.height === this.height) {
      const r = this.compRect && rectClip(this.compRect, this.width, this.height); this.compRect = null;
      if (r) {
        const x = ctx2d(this.comp); x.save(); x.beginPath(); x.rect(r.x, r.y, r.w, r.h); x.clip(); x.clearRect(r.x, r.y, r.w, r.h);
        this.drawList(x, this.layers, 0); x.restore(); this.compStamp = (this.compStamp || 0) + 1;
      }
      return this.comp;
    }
    this.compRect = null;
    if (this.comp.width !== this.width || this.comp.height !== this.height) this.comp = newCanvas(this.width, this.height);
    const x = ctx2d(this.comp); x.clearRect(0, 0, this.width, this.height);
    this.drawList(x, this.layers, 0);
    this.compDirty = false; this.compStamp = (this.compStamp || 0) + 1;
    return this.comp;
  }

  flatten(bg = null, opts = {}) {
    const c = newCanvas(this.width, this.height), x = ctx2d(c);
    if (bg) { x.fillStyle = bg; x.fillRect(0, 0, c.width, c.height); }
    const live = this.live; this.live = null;
    this.drawList(x, this.layers, 0, opts);
    this.live = live;
    return c;
  }

  snapshot() {
    return { width: this.width, height: this.height, layers: this.layers.map(copyLayer), activeId: this.activeId, target: this.target,
      selection: this.selection, guides: this.guides.map((g) => ({ ...g })) };
  }
  restore(s) {
    this.width = s.width; this.height = s.height; this.layers = s.layers.map(copyLayer); this.activeId = s.activeId; this.target = s.target || "layer";
    this.selection = s.selection; this._selCanvas = null; this.selVersion = (this.selVersion || 0) + 1; this.guides = s.guides.map((g) => ({ ...g }));
    this.pool = [];
    this.walk((l) => { if (isVector(l)) renderVector(this, l); });
  }

  begin(label) {
    if (this.tx) this.tx.depth++;
    else this.tx = { label, before: this.snapshot(), patches: [], depth: 1 };
    return this.tx;
  }
  touch(canvas, rect = null) {
    if (!this.tx) throw new Error("touch outside a transaction");
    const r = rectClip(rect || { x: 0, y: 0, w: canvas.width, h: canvas.height }, canvas.width, canvas.height);
    if (!r) return;
    for (const p of this.tx.patches) if (p.canvas === canvas && p.r.x <= r.x && p.r.y <= r.y && p.r.x + p.r.w >= r.x + r.w && p.r.y + p.r.h >= r.y + r.h) return;
    this.tx.patches.push({ canvas, r, before: ctx2d(canvas).getImageData(r.x, r.y, r.w, r.h) });
  }

  touchFrom(canvas, backup, rect) {
    const r = rectClip(rect, canvas.width, canvas.height); if (!r || !this.tx) return;
    this.tx.patches.push({ canvas, r, before: ctx2d(backup).getImageData(r.x, r.y, r.w, r.h) });
  }

  swap(layer, canvas, key = "canvas") {
    if (!this.tx) throw new Error("swap outside a transaction");
    this.tx.extra = (this.tx.extra || 0) + canvas.width * canvas.height * 4;
    layer[key] = canvas;
  }
  commit(label) {
    const tx = this.tx; if (!tx) return null;
    if (--tx.depth > 0) return null;
    this.tx = null;
    for (const p of tx.patches) p.after = ctx2d(p.canvas).getImageData(p.r.x, p.r.y, p.r.w, p.r.h);
    const entry = { label: label || tx.label, before: tx.before, after: this.snapshot(), patches: tx.patches, at: Date.now() };
    entry.bytes = tx.patches.reduce((a, p) => a + p.r.w * p.r.h * 8, 0) + (tx.extra || 0) + 2000;
    if (this.group) this.group.entries.push(entry);
    else this.pushEntry(entry);
    this.emit("commit");
    return entry;
  }
  cancel() {
    const tx = this.tx; if (!tx) return; this.tx = null;
    for (let i = tx.patches.length - 1; i >= 0; i--) { const p = tx.patches[i]; ctx2d(p.canvas).putImageData(p.before, p.r.x, p.r.y); }
    this.restore(tx.before); this.emit("cancel");
  }
  pushEntry(entry) {
    this.history.push(entry); this.future = [];
    if (this.savedIndex > this.history.length - 1) this.savedIndex = -1;
    let bytes = this.history.reduce((a, e) => a + e.bytes, 0);
    while (this.history.length > 1 && (this.history.length > HISTORY_MAX || bytes > HISTORY_BYTES)) {
      const old = this.history.shift(); bytes -= old.bytes; this.savedIndex--;
    }
  }

  beginGroup(label) { if (!this.group) this.group = { label, entries: [], depth: 0 }; this.group.depth++; }
  endGroup() {
    const g = this.group; if (!g || --g.depth > 0) return;
    this.group = null;
    if (!g.entries.length) return;
    const entry = g.entries.length === 1 ? { ...g.entries[0], label: g.label } : { label: g.label, compound: g.entries, before: g.entries[0].before, after: g.entries[g.entries.length - 1].after, bytes: g.entries.reduce((a, e) => a + e.bytes, 0), at: Date.now() };
    this.pushEntry(entry); this.emit("commit");
  }
  undoEntry(e) {
    if (e.compound) { for (let i = e.compound.length - 1; i >= 0; i--) this.undoEntry(e.compound[i]); return; }
    for (let i = e.patches.length - 1; i >= 0; i--) { const p = e.patches[i]; ctx2d(p.canvas).putImageData(p.before, p.r.x, p.r.y); }
    this.restore(e.before);
  }
  redoEntry(e) {
    if (e.compound) { for (const c of e.compound) this.redoEntry(c); return; }
    this.restore(e.after);
    for (const p of e.patches) ctx2d(p.canvas).putImageData(p.after, p.r.x, p.r.y);
  }
  undo() { if (this.tx || !this.history.length) return false; const e = this.history.pop(); this.undoEntry(e); this.future.push(e); this.live = null; this.emit("undo"); return true; }
  redo() { if (this.tx || !this.future.length) return false; const e = this.future.pop(); this.redoEntry(e); this.history.push(e); this.live = null; this.emit("redo"); return true; }

  goto(index) { while (this.history.length > index && this.undo()); while (this.history.length < index && this.future.length && this.redo()); }
  get dirty() { return this.savedIndex !== this.history.length; }
  markSaved() { this.savedIndex = this.history.length; }
  editsSummary() { return this.history.map((e) => e.label); }
}

export const DEFAULT_TEXT = { text: "", font: "Segoe UI", size: 48, color: "#ffffff", bold: false, italic: false, underline: false, align: "left", lineHeight: 1.25, x: 40, y: 40, width: 0, stroke: 0, strokeColor: "#000000", shadow: false, rotate: 0, letter: 0 };
export function fontString(t) { return `${t.italic ? "italic " : ""}${t.bold ? "700 " : "400 "}${Math.max(1, t.size)}px "${String(t.font || "Segoe UI").replace(/"/g, "")}", "Segoe UI", Arial, sans-serif`; }
export function wrapText(ctx, t) {
  const out = [];
  for (const para of String(t.text || "").split("\n")) {
    if (!t.width) { out.push(para); continue; }
    let line = "";
    for (const word of para.split(/(\s+)/)) {
      const probe = line + word;
      if (line && ctx.measureText(probe).width > t.width) { out.push(line.trimEnd()); line = word.trimStart(); }
      else line = probe;
    }
    out.push(line);
  }
  return out;
}
export function textBox(doc, l) {
  const t = l.text, c = ctx2d(newCanvas(1, 1)); c.font = fontString(t);
  const lines = wrapText(c, t), lh = t.size * t.lineHeight;
  const w = t.width || Math.max(1, ...lines.map((s) => c.measureText(s).width)) + (t.stroke || 0);
  return { x: t.x, y: t.y, w, h: Math.max(lh, lines.length * lh) };
}
export function renderText(doc, l) {
  const t = l.text;
  if (l.canvas.width !== doc.width || l.canvas.height !== doc.height) { l.canvas = newCanvas(doc.width, doc.height); }
  l.x = 0; l.y = 0;
  const x = ctx2d(l.canvas); x.clearRect(0, 0, doc.width, doc.height);
  x.save(); x.font = fontString(t); x.textBaseline = "top"; x.fillStyle = t.color; x.textAlign = t.align;
  const box = textBox(doc, l);
  if (t.rotate) { x.translate(box.x + box.w / 2, box.y + box.h / 2); x.rotate((t.rotate * Math.PI) / 180); x.translate(-(box.x + box.w / 2), -(box.y + box.h / 2)); }
  if (t.letter && "letterSpacing" in x) x.letterSpacing = t.letter + "px";
  if (t.shadow) { x.shadowColor = "rgba(0,0,0,.55)"; x.shadowBlur = Math.max(2, t.size / 10); x.shadowOffsetX = x.shadowOffsetY = Math.max(1, t.size / 24); }
  const lines = wrapText(x, t), lh = t.size * t.lineHeight;
  const ax = t.align === "center" ? box.x + box.w / 2 : t.align === "right" ? box.x + box.w : box.x;
  lines.forEach((s, i) => {
    const y = t.y + i * lh + (lh - t.size) / 2;
    if (t.stroke) { x.lineWidth = t.stroke; x.strokeStyle = t.strokeColor; x.lineJoin = "round"; x.strokeText(s, ax, y); }
    x.fillText(s, ax, y);
    if (t.underline) { const w = x.measureText(s).width, ux = t.align === "center" ? ax - w / 2 : t.align === "right" ? ax - w : ax; x.fillRect(ux, y + t.size * 1.02, w, Math.max(1, t.size / 16)); }
  });
  x.restore();
}
export const DEFAULT_SHAPE = { kind: "rect", x: 0, y: 0, w: 100, h: 100, fill: "#4c8dff", stroke: "", strokeWidth: 0, radius: 0, sides: 5 };
export function shapePath(s) {
  const p = new Path2D(), { x, y, w, h } = s;
  if (s.kind === "rect") { if (s.radius && p.roundRect) p.roundRect(x, y, w, h, Math.min(s.radius, Math.abs(w) / 2, Math.abs(h) / 2)); else p.rect(x, y, w, h); }
  else if (s.kind === "ellipse") p.ellipse(x + w / 2, y + h / 2, Math.abs(w / 2), Math.abs(h / 2), 0, 0, Math.PI * 2);
  else if (s.kind === "line" || s.kind === "arrow") {
    p.moveTo(x, y); p.lineTo(x + w, y + h);
    if (s.kind === "arrow") { const a = Math.atan2(h, w), L = Math.max(10, (s.strokeWidth || 4) * 4); for (const d of [-0.45, 0.45]) { p.moveTo(x + w, y + h); p.lineTo(x + w - L * Math.cos(a + d), y + h - L * Math.sin(a + d)); } }
  } else if (s.kind === "polygon" || s.kind === "star") {
    const n = Math.max(3, s.sides | 0), cx = x + w / 2, cy = y + h / 2, rx = Math.abs(w / 2), ry = Math.abs(h / 2), k = s.kind === "star" ? 2 : 1;
    for (let i = 0; i < n * k; i++) { const a = -Math.PI / 2 + (i * Math.PI * 2) / (n * k), r = s.kind === "star" && i % 2 ? 0.45 : 1; const px = cx + Math.cos(a) * rx * r, py = cy + Math.sin(a) * ry * r; i ? p.lineTo(px, py) : p.moveTo(px, py); }
    p.closePath();
  } else if (s.kind === "path" && s.points?.length) { s.points.forEach(([px, py], i) => (i ? p.lineTo(px, py) : p.moveTo(px, py))); if (s.closed) p.closePath(); }
  return p;
}
export function renderShape(doc, l) {
  const s = l.shape;
  if (l.canvas.width !== doc.width || l.canvas.height !== doc.height) l.canvas = newCanvas(doc.width, doc.height);
  l.x = 0; l.y = 0;
  const x = ctx2d(l.canvas); x.clearRect(0, 0, doc.width, doc.height);
  const path = shapePath(s), open = s.kind === "line" || s.kind === "arrow" || (s.kind === "path" && !s.closed);
  if (s.fill && !open) { x.fillStyle = s.fill; x.fill(path); }
  if ((s.stroke && s.strokeWidth) || open) { x.lineWidth = Math.max(1, s.strokeWidth || 4); x.strokeStyle = s.stroke || s.fill || "#000"; x.lineCap = "round"; x.lineJoin = "round"; x.stroke(path); }
}
export function shapeBounds(s) {
  if (s.kind === "path" && s.points?.length) { const xs = s.points.map((p) => p[0]), ys = s.points.map((p) => p[1]); return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) }; }
  return { x: Math.min(s.x, s.x + s.w), y: Math.min(s.y, s.y + s.h), w: Math.abs(s.w), h: Math.abs(s.h) };
}
export function loadSvgImage(source) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(new Blob([source], { type: "image/svg+xml" }));
    const img = new Image(); img.onload = () => { resolve(img); setTimeout(() => URL.revokeObjectURL(url), 1000); }; img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("SVG")); }; img.src = url;
  });
}
export function renderSvg(doc, l) {
  const s = l.svg;
  if (l.canvas.width !== doc.width || l.canvas.height !== doc.height) l.canvas = newCanvas(doc.width, doc.height);
  l.x = 0; l.y = 0;
  const x = ctx2d(l.canvas); x.clearRect(0, 0, doc.width, doc.height);
  if (s.img) x.drawImage(s.img, s.x, s.y, s.w, s.h);
}
export function renderVector(doc, l) { if (l.type === "text") renderText(doc, l); else if (l.type === "shape") renderShape(doc, l); else if (l.type === "svg") renderSvg(doc, l); }
export function isVector(l) { return l && (l.type === "text" || l.type === "shape" || l.type === "svg"); }
export function vectorBounds(doc, l) { return l.type === "text" ? textBox(doc, l) : l.type === "shape" ? shapeBounds(l.shape) : l.type === "svg" ? { x: l.svg.x, y: l.svg.y, w: l.svg.w, h: l.svg.h } : null; }
