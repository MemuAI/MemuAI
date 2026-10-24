// MemuNote Photo — free transform (Ctrl+T): move, scale (Shift = keep proportions, Alt = from the centre), rotate
// (drag outside the box), skew / perspective (Ctrl + drag a corner), flip. Affine boxes are drawn with one
// drawImage; perspective uses a homography rendered as a triangle mesh. Mixed into PhotoEditor.prototype.
import { esc, t, toast } from "../core.js";
import { newCanvas, ctx2d, isVector } from "./doc.js";
import { picon } from "./icons.js";

const sub = (a, b) => ({ x: a.x - b.x, y: a.y - b.y }), add = (a, b) => ({ x: a.x + b.x, y: a.y + b.y }), mul = (a, k) => ({ x: a.x * k, y: a.y * k });
const dot = (a, b) => a.x * b.x + a.y * b.y, len = (a) => Math.hypot(a.x, a.y);
const center = (q) => mul(q.reduce((s, p) => add(s, p), { x: 0, y: 0 }), 0.25);
const rot = (p, c, a) => { const s = Math.sin(a), co = Math.cos(a), d = sub(p, c); return { x: c.x + d.x * co - d.y * s, y: c.y + d.x * s + d.y * co }; };
const CORNERS = [[0, 0], [1, 0], [1, 1], [0, 1]];

export function contentBounds(canvas) {
  const w = canvas.width, h = canvas.height, k = Math.min(1, 1024 / Math.max(w, h)), sw = Math.max(1, Math.ceil(w * k)), sh = Math.max(1, Math.ceil(h * k));
  const s = newCanvas(sw, sh), x = ctx2d(s, { willReadFrequently: true }); x.drawImage(canvas, 0, 0, sw, sh);
  const d = x.getImageData(0, 0, sw, sh).data; let x0 = sw, y0 = sh, x1 = -1, y1 = -1;
  for (let yy = 0; yy < sh; yy++) for (let xx = 0; xx < sw; xx++) if (d[(yy * sw + xx) * 4 + 3] > 0) { if (xx < x0) x0 = xx; if (xx > x1) x1 = xx; if (yy < y0) y0 = yy; if (yy > y1) y1 = yy; }
  if (x1 < 0) return null;
  const bx = Math.max(0, Math.floor(x0 / k) - 1), by = Math.max(0, Math.floor(y0 / k) - 1);
  return { x: bx, y: by, w: Math.min(w, Math.ceil((x1 + 1) / k) + 1) - bx, h: Math.min(h, Math.ceil((y1 + 1) / k) + 1) - by };
}

/** Homography that maps the unit square to quad q (4 points tl, tr, br, bl). */
function homography(q) {
  const [p0, p1, p2, p3] = q, dx1 = p1.x - p2.x, dx2 = p3.x - p2.x, dy1 = p1.y - p2.y, dy2 = p3.y - p2.y;
  const sx = p0.x - p1.x + p2.x - p3.x, sy = p0.y - p1.y + p2.y - p3.y, den = dx1 * dy2 - dx2 * dy1 || 1e-9;
  const g = (sx * dy2 - dx2 * sy) / den, h = (dx1 * sy - sx * dy1) / den;
  const a = p1.x - p0.x + g * p1.x, b = p3.x - p0.x + h * p3.x, c = p0.x, d = p1.y - p0.y + g * p1.y, e = p3.y - p0.y + h * p3.y, f = p0.y;
  return (u, v) => { const w = g * u + h * v + 1; return { x: (a * u + b * v + c) / w, y: (d * u + e * v + f) / w }; };
}
function isParallelogram(q) { const a = add(q[0], q[2]), b = add(q[1], q[3]); return Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5; }
/** Draw *src* (w×h) into ctx mapped onto quad q. */
export function warpDraw(ctx, src, q) {
  const w = src.width, h = src.height;
  ctx.save(); ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
  if (isParallelogram(q)) {
    const u = sub(q[1], q[0]), v = sub(q[3], q[0]);
    ctx.setTransform(...ctxScale(ctx), 0, 0); // reset then apply
    ctx.setTransform(u.x / w, u.y / w, v.x / h, v.y / h, q[0].x, q[0].y);
    ctx.drawImage(src, 0, 0);
    ctx.restore(); return;
  }
  const H = homography(q), N = Math.max(6, Math.min(24, Math.round(Math.max(w, h) / 80)));
  const pts = []; for (let j = 0; j <= N; j++) { pts.push([]); for (let i = 0; i <= N; i++) pts[j].push(H(i / N, j / N)); }
  for (let j = 0; j < N; j++) for (let i = 0; i < N; i++) {
    const s0 = { x: (i / N) * w, y: (j / N) * h }, s1 = { x: ((i + 1) / N) * w, y: (j / N) * h }, s2 = { x: ((i + 1) / N) * w, y: ((j + 1) / N) * h }, s3 = { x: (i / N) * w, y: ((j + 1) / N) * h };
    const d0 = pts[j][i], d1 = pts[j][i + 1], d2 = pts[j + 1][i + 1], d3 = pts[j + 1][i];
    tri(ctx, src, s0, s1, s2, d0, d1, d2); tri(ctx, src, s0, s2, s3, d0, d2, d3);
  }
  ctx.restore();
}
function ctxScale() { return [1, 0, 0, 1]; }
function tri(ctx, img, s0, s1, s2, d0, d1, d2) {
  const c = center([d0, d1, d2, d0]), grow = (p) => { const v = sub(p, c), l = len(v) || 1; return add(p, mul(v, 0.6 / l)); };   // overlap → no seams
  const e0 = grow(d0), e1 = grow(d1), e2 = grow(d2);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.beginPath(); ctx.moveTo(e0.x, e0.y); ctx.lineTo(e1.x, e1.y); ctx.lineTo(e2.x, e2.y); ctx.closePath(); ctx.clip();
  const den = s0.x * (s2.y - s1.y) - s1.x * s2.y + s2.x * s1.y + (s1.x - s2.x) * s0.y || 1e-9;
  const a = -(s0.y * (d2.x - d1.x) - s1.y * d2.x + s2.y * d1.x + (s1.y - s2.y) * d0.x) / den;
  const b = (s1.y * d2.y + s0.y * (d1.y - d2.y) - s2.y * d1.y + (s2.y - s1.y) * d0.y) / den;
  const cc = (s0.x * (d2.x - d1.x) - s1.x * d2.x + s2.x * d1.x + (s1.x - s2.x) * d0.x) / den;
  const d = -(s1.x * d2.y + s0.x * (d1.y - d2.y) - s2.x * d1.y + (s2.x - s1.x) * d0.y) / den;
  const e = (s0.x * (s2.y * d1.x - s1.y * d2.x) + s0.y * (s1.x * d2.x - s2.x * d1.x) + (s2.x * s1.y - s1.x * s2.y) * d0.x) / den;
  const f = (s0.x * (s2.y * d1.y - s1.y * d2.y) + s0.y * (s1.x * d2.y - s2.x * d1.y) + (s2.x * s1.y - s1.x * s2.y) * d0.y) / den;
  ctx.setTransform(a, b, cc, d, e, f); ctx.drawImage(img, 0, 0); ctx.restore();
}

export const TRANSFORM = {
  xfStart() {
    const doc = this.doc; let l = doc.active;
    if (this.xf) return;
    if (!l) { toast(t("ph_no_layer")); return; }
    if (l.locked) { toast(t("ph_layer_locked")); return; }
    if (l.type === "group") { toast(t("ph_group_no_transform")); return; }
    if (isVector(l)) { this.cmd("rasterize"); l = doc.active; toast(t("ph_rasterized_for_transform")); }
    if (l.x !== 0 || l.y !== 0 || l.canvas.width !== doc.width || l.canvas.height !== doc.height) this.expandLayer(l);
    let src, base, b;
    if (doc.selection) {
      b = doc.selectionBounds(); if (!b) return;
      src = newCanvas(b.w, b.h); const sx = ctx2d(src); sx.drawImage(l.canvas, -b.x, -b.y); sx.globalCompositeOperation = "destination-in"; sx.drawImage(doc.selCanvas(), -b.x, -b.y);
      base = newCanvas(doc.width, doc.height); const bx = ctx2d(base); bx.drawImage(l.canvas, 0, 0); bx.globalCompositeOperation = "destination-out"; bx.drawImage(doc.selCanvas(), 0, 0);
    } else {
      b = contentBounds(l.canvas); if (!b) { toast(t("ph_layer_empty")); return; }
      src = newCanvas(b.w, b.h); ctx2d(src).drawImage(l.canvas, -b.x, -b.y);
      base = null;
    }
    const q = [{ x: b.x, y: b.y }, { x: b.x + b.w, y: b.y }, { x: b.x + b.w, y: b.y + b.h }, { x: b.x, y: b.y + b.h }];
    this.xf = { layer: l, src, base, bounds: b, quad: q, sel: !!doc.selection, preview: newCanvas(doc.width, doc.height) };
    this.xfPreview(); this.renderOptions(); this.requestRender();
  },
  xfPreview() {
    const xf = this.xf, x = ctx2d(xf.preview); x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, xf.preview.width, xf.preview.height);
    if (xf.base) x.drawImage(xf.base, 0, 0);
    warpDraw(x, xf.src, xf.quad);
    this.doc.live = { layerId: xf.layer.id, canvas: xf.preview, x: 0, y: 0 };
    this.doc.compDirty = true; this.requestRender();
  },
  xfHit(p) {
    const q = this.xf.quad, tol = 9 / this.view.zoom;
    for (let i = 0; i < 4; i++) if (len(sub(q[i], p)) < tol) return { kind: "corner", i };
    for (let i = 0; i < 4; i++) { const m = mul(add(q[i], q[(i + 1) % 4]), 0.5); if (len(sub(m, p)) < tol) return { kind: "edge", i }; }
    if (pointInQuad(p, q)) return { kind: "move" };
    return { kind: "rotate" };
  },
  xfDown(p, e) { const h = this.xfHit(p); this.xf.drag = { ...h, start: p, quad: this.xf.quad.map((v) => ({ ...v })), ctrl: e.ctrlKey || e.metaKey }; },
  xfMove(p, e) {
    const xf = this.xf, d = xf.drag;
    if (!d) { const h = this.xfHit(p); this.canvas.style.cursor = h.kind === "move" ? "move" : h.kind === "rotate" ? "alias" : h.kind === "corner" ? (e.ctrlKey ? "crosshair" : "nwse-resize") : "ew-resize"; return; }
    const q0 = d.quad;
    if (d.kind === "move") { const dv = sub(p, d.start); xf.quad = q0.map((v) => add(v, dv)); }
    else if (d.kind === "rotate") {
      const c = center(q0); let a = Math.atan2(p.y - c.y, p.x - c.x) - Math.atan2(d.start.y - c.y, d.start.x - c.x);
      if (e.shiftKey) a = Math.round(a / (Math.PI / 12)) * (Math.PI / 12);
      xf.quad = q0.map((v) => rot(v, c, a));
    } else if (d.kind === "corner" && (d.ctrl || e.ctrlKey)) { xf.quad = q0.map((v) => ({ ...v })); xf.quad[d.i] = { ...p }; }
    else {
      const U = sub(q0[1], q0[0]), V = sub(q0[3], q0[0]), W = len(U) || 1, H = len(V) || 1, u = mul(U, 1 / W), v = mul(V, 1 / H);
      let sx, sy, useX = true, useY = true;
      if (d.kind === "corner") [sx, sy] = CORNERS[d.i];
      else { const e2 = [[0.5, 0], [1, 0.5], [0.5, 1], [0, 0.5]][d.i]; [sx, sy] = e2; useX = e2[0] !== 0.5; useY = e2[1] !== 0.5; }
      const fromCenter = e.altKey, ax = fromCenter ? 0.5 : 1 - sx, ay = fromCenter ? 0.5 : 1 - sy;
      const A = add(q0[0], add(mul(U, ax), mul(V, ay))), dv = sub(p, A);
      let nw = useX ? dot(dv, u) / (sx - ax) : W, nh = useY ? dot(dv, v) / (sy - ay) : H;
      if (e.shiftKey && d.kind === "corner") { const k = Math.max(Math.abs(nw) / W, Math.abs(nh) / H); nw = Math.sign(nw || 1) * W * k; nh = Math.sign(nh || 1) * H * k; }
      xf.quad = CORNERS.map(([jx, jy]) => add(A, add(mul(u, nw * (jx - ax)), mul(v, nh * (jy - ay)))));
    }
    this.xfPreview(); this.xfStatus();
  },
  xfUp() { if (this.xf) this.xf.drag = null; this.renderOptions(); },
  xfStatus() { const q = this.xf.quad, b = this.xf.bounds; this.hint(`${Math.round((len(sub(q[1], q[0])) / b.w) * 100)}% × ${Math.round((len(sub(q[3], q[0])) / b.h) * 100)}%  ${Math.round((Math.atan2(q[1].y - q[0].y, q[1].x - q[0].x) * 180) / Math.PI)}°`); },
  xfOverlay(x) {
    const q = this.xf.quad.map((p) => this.toScreen(p));
    x.save(); x.strokeStyle = "#4c8dff"; x.lineWidth = 1.2; x.beginPath(); q.forEach((p, i) => (i ? x.lineTo(p.x, p.y) : x.moveTo(p.x, p.y))); x.closePath(); x.stroke();
    x.fillStyle = "#fff"; x.strokeStyle = "#4c8dff";
    const hs = [...q, ...q.map((p, i) => mul(add(p, q[(i + 1) % 4]), 0.5))];
    for (const h of hs) { x.fillRect(h.x - 4, h.y - 4, 8, 8); x.strokeRect(h.x - 4, h.y - 4, 8, 8); }
    const c = center(q); x.beginPath(); x.arc(c.x, c.y, 4, 0, Math.PI * 2); x.stroke();
    x.restore();
  },
  xfOptions() {
    const q = this.xf.quad, b = this.xf.bounds;
    const sw = Math.round((len(sub(q[1], q[0])) / b.w) * 1000) / 10, sh = Math.round((len(sub(q[3], q[0])) / b.h) * 1000) / 10, ang = Math.round((Math.atan2(q[1].y - q[0].y, q[1].x - q[0].x) * 1800) / Math.PI) / 10;
    return `<span class="ph-opt-tool">${picon("transform")}<b>${esc(t("ph_free_transform"))}</b></span>
      <label class="ph-opt"><span>W</span><input class="ph-num" type="number" step="0.1" data-xfv="w" value="${sw}"><em>%</em></label><label class="ph-opt"><span>H</span><input class="ph-num" type="number" step="0.1" data-xfv="h" value="${sh}"><em>%</em></label>
      <label class="ph-opt"><span>${esc(t("ph_angle"))}</span><input class="ph-num" type="number" step="0.5" data-xfv="a" value="${ang}"><em>°</em></label>
      <button class="icon-btn sm" data-xf="flipH" title="${esc(t("ph_flip_h"))}">${picon("flipH")}</button><button class="icon-btn sm" data-xf="flipV" title="${esc(t("ph_flip_v"))}">${picon("flipV")}</button><button class="icon-btn sm" data-xf="rot90" title="${esc(t("ph_rotate_90"))}">${picon("rotate")}</button>
      <span class="ph-tip">${esc(t("ph_xf_tip"))}</span><button class="btn sm primary" data-xf="apply">${picon("check")}${esc(t("ph_apply"))}</button><button class="btn sm ghost" data-xf="cancel">${esc(t("ph_cancel"))}</button>`;
  },
  xfAction(a) {
    const xf = this.xf; if (!xf) return;
    const q = xf.quad, c = center(q);
    if (a === "apply") return this.xfApply();
    if (a === "cancel") return this.xfCancel();
    if (a === "flipH") xf.quad = [q[1], q[0], q[3], q[2]];
    else if (a === "flipV") xf.quad = [q[3], q[2], q[1], q[0]];
    else if (a === "rot90") xf.quad = q.map((v) => rot(v, c, Math.PI / 2));
    this.xfPreview(); this.renderOptions();
  },
  xfBindValues() {},
  xfApply() {
    const xf = this.xf; if (!xf) return; this.xf = null;
    const doc = this.doc, l = xf.layer;
    this.doc.live = null;
    const out = newCanvas(doc.width, doc.height), x = ctx2d(out);
    if (xf.base) x.drawImage(xf.base, 0, 0);
    const moved = newCanvas(doc.width, doc.height); warpDraw(ctx2d(moved), xf.src, xf.quad); x.drawImage(moved, 0, 0);
    doc.begin(t("ph_free_transform"));
    doc.swap(l, out);
    if (xf.sel) {
      const d = ctx2d(moved, { willReadFrequently: true }).getImageData(0, 0, doc.width, doc.height).data, m = new Uint8Array(doc.width * doc.height);
      for (let i = 0; i < m.length; i++) m[i] = d[i * 4 + 3] > 0 ? 255 : 0;
      doc.setSelection(m);
    }
    doc.commit();
    this.renderOptions(); this.requestRender(); this.refreshPanels();
  },
  xfCancel() { if (!this.xf) return; this.xf = null; this.doc.live = null; this.doc.compDirty = true; this.renderOptions(); this.requestRender(); },
};
function pointInQuad(p, q) { let inside = false; for (let i = 0, j = 3; i < 4; j = i++) { if ((q[i].y > p.y) !== (q[j].y > p.y) && p.x < ((q[j].x - q[i].x) * (p.y - q[i].y)) / (q[j].y - q[i].y) + q[i].x) inside = !inside; } return inside; }
