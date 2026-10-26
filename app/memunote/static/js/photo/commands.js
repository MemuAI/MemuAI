

import { esc, t, toast, modal, prompt, confirm, kbd, api, assetUrl } from "../core.js";
import { newCanvas, ctx2d, cloneCanvas, isVector, renderVector, vectorBounds, uidL } from "./doc.js";
import { SAVE_FORMATS, saveDocument, formatOfPath, framesOf, canvasFromB64, canvasToBlob, extOf } from "./io.js";
import { parseHex, histogram, invertMask, colorRange, growMask, curveLut } from "./pixels.js";
import { picon } from "./icons.js";
import { contentBounds } from "./transform.js";
import { TOOLS } from "./tools.js";

const S = (k, min, max, v = 0, step = 1) => ({ k, min, max, v, step });
export const SPECS = {
  brightnessContrast: [S("brightness", -150, 150), S("contrast", -100, 100)],
  levels: "levels", curves: "curves",
  exposure: [S("exposure", -5, 5, 0, 0.05), S("offset", -0.5, 0.5, 0, 0.005), S("gamma", 0.1, 3, 1, 0.01)],
  hueSaturation: [S("hue", -180, 180), S("saturation", -100, 100), S("lightness", -100, 100), { k: "colorize", bool: true, v: false }],
  vibrance: [S("vibrance", -100, 100), S("saturation", -100, 100)],
  colorBalance: [S("cyanRed", -100, 100), S("magentaGreen", -100, 100), S("yellowBlue", -100, 100), { k: "preserve", bool: true, v: true }],
  temperature: [S("temperature", -100, 100), S("tint", -100, 100)],
  blackWhite: [S("red", -200, 300, 40), S("yellow", -200, 300, 60), S("green", -200, 300, 40), S("cyan", -200, 300, 60), S("blue", -200, 300, 20), S("magenta", -200, 300, 80)],
  sepia: [S("amount", 0, 100, 80)], threshold: [S("level", 1, 255, 128)], posterize: [S("levels", 2, 32, 4)],
  replaceColor: [{ k: "from", color: true, v: "fg" }, { k: "to", color: true, v: "bg" }, S("fuzz", 1, 200, 40)],
  gaussianBlur: [S("radius", 0.5, 120, 4, 0.5)], boxBlur: [S("radius", 1, 100, 3)], motionBlur: [S("angle", -180, 180, 0), S("distance", 1, 120, 12)],
  sharpen: [S("amount", 1, 300, 60)], unsharpMask: [S("amount", 1, 500, 100), S("radius", 0.5, 30, 2, 0.1), S("threshold", 0, 255, 0)],
  noise: [S("amount", 1, 100, 12), { k: "mono", bool: true, v: true }], denoise: [S("radius", 1, 3, 1)], pixelate: [S("size", 2, 200, 12)],
  vignette: [S("amount", 0, 100, 50), S("size", 5, 100, 60)], emboss: [S("strength", 0.2, 4, 1, 0.1)], oilPaint: [S("radius", 1, 6, 3), S("levels", 4, 40, 20)],
  invert: null, autoLevels: null, edges: null, grayscale: null,
};
const PREVIEW_MAX = 2_500_000;

function layerForPixels(ed) {
  const l = ed.doc.active;
  if (!l) { toast(t("ph_no_layer")); return null; }
  if (ed.doc.target === "mask") { toast(t("ph_adjust_mask")); return null; }
  if (l.type === "group") { toast(t("ph_group_no_paint")); return null; }
  if (l.locked) { toast(t("ph_layer_locked")); return null; }
  if (isVector(l)) { ed.askRasterize(l); return null; }
  return l;
}
function selMaskFor(ed, l) {
  const d = ed.doc, m = d.selection; if (!m) return null;
  if (l.x === 0 && l.y === 0 && l.canvas.width === d.width && l.canvas.height === d.height) return m;
  const w = l.canvas.width, h = l.canvas.height, out = new Uint8Array(w * h);
  for (let y = 0; y < h; y++) { const dy = y + l.y; if (dy < 0 || dy >= d.height) continue; for (let x = 0; x < w; x++) { const dx = x + l.x; if (dx >= 0 && dx < d.width) out[y * w + x] = m[dy * d.width + dx]; } }
  return out;
}
const describeParams = (name, p) => { const spec = SPECS[name]; if (!Array.isArray(spec)) return ""; return spec.filter((s) => !s.color).map((s) => `${t("ph_p_" + s.k)} ${s.bool ? (p[s.k] ? "✓" : "✗") : Math.round(p[s.k] * 100) / 100}`).join(", "); };

async function runOnLayer(ed, l, name, params, scale = 1) {
  const src = l.canvas, w = Math.max(1, Math.round(src.width * scale)), h = Math.max(1, Math.round(src.height * scale));
  let c = src;
  if (scale !== 1) { c = newCanvas(w, h); const x = ctx2d(c); x.imageSmoothingQuality = "high"; x.drawImage(src, 0, 0, w, h); }
  const img = ctx2d(c, { willReadFrequently: true }).getImageData(0, 0, w, h);
  let mask = selMaskFor(ed, l);
  if (mask && scale !== 1) { const { resizeMask } = await import("./pixels.js"); mask = resizeMask(mask, src.width, src.height, w, h); }
  const p = { ...params }; if (scale !== 1) for (const k of ["radius", "distance", "size"]) if (typeof p[k] === "number" && name !== "vignette") p[k] = Math.max(name === "pixelate" ? 2 : 0.5, p[k] * scale);
  const m2 = mask ? mask.slice() : null;
  const res = await ed.worker({ kind: "op", name, width: w, height: h, buffer: img.data.buffer, params: p, mask: m2?.buffer }, [img.data.buffer, ...(m2 ? [m2.buffer] : [])]);
  const out = newCanvas(w, h); ctx2d(out).putImageData(new ImageData(new Uint8ClampedArray(res.buffer), w, h), 0, 0);
  return out;
}
export async function applyOp(ed, name, params = {}, l = null, label = null) {
  l = l || layerForPixels(ed); if (!l) return false;
  ed.busy(true, t("ph_adj_" + name));
  try {
    const out = await runOnLayer(ed, l, name, params);
    ed.doc.begin(label || t("ph_adj_" + name));
    ed.doc.swap(l, out);
    ed.doc.commit((label || t("ph_adj_" + name)) + (describeParams(name, params) ? ` (${describeParams(name, params)})` : ""));
    ed.lastFilter = { name, params };
    return true;
  } finally { ed.busy(false); }
}

function adjustDialog(ed, name) {
  const spec = SPECS[name];
  const l = layerForPixels(ed); if (!l) return;
  if (spec === null || spec === undefined) return applyOp(ed, name, {}, l);
  ed.stage.querySelector(".ph-dlg")?.remove();
  const params = {}; if (Array.isArray(spec)) for (const s of spec) params[s.k] = s.color ? (s.v === "fg" ? ed.fg : s.v === "bg" ? ed.bg : s.v) : s.v;
  const dlg = document.createElement("div"); dlg.className = "ph-dlg";
  const pixels = l.canvas.width * l.canvas.height, scale = pixels > PREVIEW_MAX ? Math.sqrt(PREVIEW_MAX / pixels) : 1;
  let body = "";
  if (spec === "levels") Object.assign(params, { inBlack: 0, gamma: 1, inWhite: 255, outBlack: 0, outWhite: 255, channel: "rgb" });
  if (spec === "curves") Object.assign(params, { points: [[0, 0], [255, 255]], channel: "rgb" });
  if (Array.isArray(spec)) body = spec.map((s) => s.bool ? `<label class="ph-dchk"><input type="checkbox" data-p="${s.k}" ${s.v ? "checked" : ""}> ${esc(t("ph_p_" + s.k))}</label>`
    : s.color ? `<label class="ph-drow"><span>${esc(t("ph_p_" + s.k))}</span><input type="color" data-p="${s.k}" value="${params[s.k]}"></label>`
    : `<label class="ph-drow"><span>${esc(t("ph_p_" + s.k))}</span><input type="range" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.v}" data-p="${s.k}"><input class="ph-num" type="number" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.v}" data-p="${s.k}"></label>`).join("");
  else if (spec === "levels") body = `<div class="ph-drow"><span>${esc(t("ph_channel"))}</span><select class="input xs" data-p="channel"><option value="rgb">RGB</option><option value="r">R</option><option value="g">G</option><option value="b">B</option></select></div><canvas class="ph-histo big" width="256" height="90"></canvas>`
    + [["inBlack", 0, 254, 0, 1], ["gamma", 0.1, 5, 1, 0.01], ["inWhite", 1, 255, 255, 1], ["outBlack", 0, 255, 0, 1], ["outWhite", 0, 255, 255, 1]].map(([k, mn, mx, v, st]) => `<label class="ph-drow"><span>${esc(t("ph_p_" + k))}</span><input type="range" min="${mn}" max="${mx}" step="${st}" value="${v}" data-p="${k}"><input class="ph-num" type="number" min="${mn}" max="${mx}" step="${st}" value="${v}" data-p="${k}"></label>`).join("")
    + `<button class="btn sm" data-auto>${esc(t("ph_auto"))}</button>`;
  else if (spec === "curves") body = `<div class="ph-drow"><span>${esc(t("ph_channel"))}</span><select class="input xs" data-p="channel"><option value="rgb">RGB</option><option value="r">R</option><option value="g">G</option><option value="b">B</option></select><span class="ph-sp"></span><button class="btn sm ghost" data-curve="s">${esc(t("ph_curve_s"))}</button></div><canvas class="ph-curve" width="256" height="256"></canvas><p class="muted small">${esc(t("ph_curve_tip"))}</p>`;
  dlg.innerHTML = `<div class="ph-dlg-head">${picon("sliders")}<b>${esc(t("ph_adj_" + name))}</b>${ed.doc.selection ? `<span class="ph-badge">${esc(t("ph_in_selection"))}</span>` : ""}<span class="ph-sp"></span><button class="icon-btn sm" data-x>${picon("close")}</button></div><div class="ph-dlg-body">${body}</div>
    <div class="ph-dlg-foot"><label class="ph-dchk"><input type="checkbox" data-preview checked> ${esc(t("ph_preview"))}</label><span class="ph-sp"></span><button class="btn sm ghost" data-reset>${esc(t("ph_reset"))}</button><button class="btn sm" data-cancel>${esc(t("ph_cancel"))}</button><button class="btn sm primary" data-ok>${esc(t("ph_ok"))}</button></div>`;
  ed.stage.append(dlg);
  let seq = 0, closed = false;
  const preview = async () => {
    if (closed) return;
    const my = ++seq;
    if (!dlg.querySelector("[data-preview]").checked) { ed.doc.live = null; ed.layerChanged(); return; }
    dlg.classList.add("working");
    try {
      const out = await runOnLayer(ed, l, name, params, scale);
      if (my !== seq || closed) return;
      let c = out; if (scale !== 1) { c = newCanvas(l.canvas.width, l.canvas.height); const x = ctx2d(c); x.imageSmoothingQuality = "high"; x.drawImage(out, 0, 0, c.width, c.height); }
      ed.doc.live = { layerId: l.id, canvas: c, x: l.x, y: l.y }; ed.layerChanged();
    } catch (e) { ed.error(e); } finally { if (my === seq) dlg.classList.remove("working"); }
  };
  let timer = 0; const soon = () => { clearTimeout(timer); timer = setTimeout(preview, pixels > 4e6 ? 140 : 40); };
  dlg.querySelectorAll("[data-p]").forEach((inp) => inp.addEventListener("input", () => {
    const k = inp.dataset.p; const v = inp.type === "checkbox" ? inp.checked : inp.type === "range" || inp.type === "number" ? +inp.value : inp.value;
    params[k] = v; dlg.querySelectorAll(`[data-p="${k}"]`).forEach((x) => { if (x !== inp && x.type !== "checkbox") x.value = v; });
    if (k === "channel" && spec === "curves") { curve?.draw(); } soon();
  }));
  let curve = null;
  if (spec === "levels" || spec === "curves") {
    const src = ed.doc.flatten(), k2 = Math.min(1, 500 / Math.max(src.width, src.height)), sm = newCanvas(Math.max(1, src.width * k2), Math.max(1, src.height * k2)), sx = ctx2d(sm, { willReadFrequently: true }); sx.drawImage(l.canvas, l.x * k2, l.y * k2, l.canvas.width * k2, l.canvas.height * k2);
    const hist = histogram(sx.getImageData(0, 0, sm.width, sm.height));
    const hc = dlg.querySelector(".ph-histo"); if (hc) drawHist(hc, hist.l);
    dlg.querySelector("[data-auto]")?.addEventListener("click", () => { const lo = hist.l.findIndex((v) => v > hist.count * 0.001), hi = 255 - [...hist.l].reverse().findIndex((v) => v > hist.count * 0.001); params.inBlack = Math.max(0, lo); params.inWhite = Math.min(255, Math.max(params.inBlack + 1, hi)); dlg.querySelectorAll("[data-p=inBlack]").forEach((x) => (x.value = params.inBlack)); dlg.querySelectorAll("[data-p=inWhite]").forEach((x) => (x.value = params.inWhite)); soon(); });
    if (spec === "curves") curve = curveEditor(dlg.querySelector(".ph-curve"), params, hist.l, soon);
    dlg.querySelector("[data-curve=s]")?.addEventListener("click", () => { params.points = [[0, 0], [64, 48], [192, 208], [255, 255]]; curve.draw(); soon(); });
  }
  const close = (apply) => {
    if (closed) return; closed = true; clearTimeout(timer); dlg.remove(); document.removeEventListener("keydown", key, true);
    ed.doc.live = null; ed.layerChanged();
    if (apply) applyOp(ed, name, params, l).catch((e) => ed.error(e));
  };
  const key = (e) => { if (!dlg.isConnected) return; if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); } else if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); e.stopPropagation(); close(true); } };
  document.addEventListener("keydown", key, true);
  dlg.querySelector("[data-x]").onclick = () => close(false); dlg.querySelector("[data-cancel]").onclick = () => close(false); dlg.querySelector("[data-ok]").onclick = () => close(true);
  dlg.querySelector("[data-preview]").onchange = preview;
  dlg.querySelector("[data-reset]").onclick = () => { close(false); adjustDialog(ed, name); };

  const head = dlg.querySelector(".ph-dlg-head"); head.addEventListener("pointerdown", (e) => { if (e.target.closest("button")) return; head.setPointerCapture(e.pointerId); const r = dlg.getBoundingClientRect(), sr = ed.stage.getBoundingClientRect(), ox = e.clientX - r.left, oy = e.clientY - r.top; head.onpointermove = (ev) => { dlg.style.left = Math.max(0, ev.clientX - sr.left - ox) + "px"; dlg.style.top = Math.max(0, ev.clientY - sr.top - oy) + "px"; dlg.style.right = "auto"; }; head.onpointerup = () => (head.onpointermove = null); });
  preview();
}
function drawHist(c, arr) {
  const x = c.getContext("2d"), max = Math.max(1, ...arr.slice(2, 254)); x.clearRect(0, 0, c.width, c.height);
  x.fillStyle = getComputedStyle(c).color || "#888"; x.globalAlpha = 0.55; x.beginPath(); x.moveTo(0, c.height);
  for (let i = 0; i < 256; i++) x.lineTo((i / 255) * c.width, c.height - Math.min(c.height, (arr[i] / max) * c.height * 0.95));
  x.lineTo(c.width, c.height); x.fill(); x.globalAlpha = 1;
}
function curveEditor(c, params, hist, changed) {
  const x = c.getContext("2d"), W = c.width;
  const draw = () => {
    x.clearRect(0, 0, W, W); x.strokeStyle = "rgba(128,128,128,.35)"; x.lineWidth = 1;
    for (let i = 1; i < 4; i++) { x.beginPath(); x.moveTo((i * W) / 4, 0); x.lineTo((i * W) / 4, W); x.moveTo(0, (i * W) / 4); x.lineTo(W, (i * W) / 4); x.stroke(); }
    const max = Math.max(1, ...hist.slice(2, 254)); x.fillStyle = "rgba(128,128,128,.25)"; x.beginPath(); x.moveTo(0, W); for (let i = 0; i < 256; i++) x.lineTo(i, W - (hist[i] / max) * W * 0.9); x.lineTo(W, W); x.fill();
    x.strokeStyle = "rgba(128,128,128,.6)"; x.beginPath(); x.moveTo(0, W); x.lineTo(W, 0); x.stroke();
    const lut = curveLut(params.points), col = { r: "#ff5a5a", g: "#3ddc84", b: "#5b8cff" }[params.channel] || getComputedStyle(c).color || "#ddd";
    x.strokeStyle = col; x.lineWidth = 2; x.beginPath(); for (let i = 0; i < 256; i++) { const yy = W - (lut[i] / 255) * W; i ? x.lineTo(i, yy) : x.moveTo(i, yy); } x.stroke();
    x.fillStyle = "#fff"; x.strokeStyle = col; for (const [px, py] of params.points) { x.beginPath(); x.arc(px, W - py, 5, 0, 7); x.fill(); x.stroke(); }
  };
  const pt = (e) => { const r = c.getBoundingClientRect(); return [Math.max(0, Math.min(255, ((e.clientX - r.left) / r.width) * 255)), Math.max(0, Math.min(255, 255 - ((e.clientY - r.top) / r.height) * 255))]; };
  c.addEventListener("pointerdown", (e) => {
    const [px, py] = pt(e); let i = params.points.findIndex(([a, b]) => Math.hypot(a - px, b - py) < 12);
    if (i < 0) { params.points.push([px, py]); params.points.sort((a, b) => a[0] - b[0]); i = params.points.findIndex(([a, b]) => a === px && b === py); }
    c.setPointerCapture(e.pointerId);
    c.onpointermove = (ev) => { const [qx, qy] = pt(ev); const r = c.getBoundingClientRect(); const out = ev.clientY < r.top - 20 || ev.clientY > r.bottom + 20; const p = params.points[i]; if (!p) return; if (out && i > 0 && i < params.points.length - 1) { params.points.splice(i, 1); c.onpointermove = null; } else { const lo = i > 0 ? params.points[i - 1][0] + 1 : 0, hi = i < params.points.length - 1 ? params.points[i + 1][0] - 1 : 255; p[0] = i === 0 ? Math.min(qx, hi) : i === params.points.length - 1 ? Math.max(qx, lo) : Math.max(lo, Math.min(hi, qx)); p[1] = qy; } draw(); changed(); };
    c.onpointerup = () => { c.onpointermove = null; };
    draw(); changed();
  });
  c.addEventListener("dblclick", (e) => { const [px, py] = pt(e); const i = params.points.findIndex(([a, b]) => Math.hypot(a - px, b - py) < 12); if (i > 0 && i < params.points.length - 1) { params.points.splice(i, 1); draw(); changed(); } });
  draw();
  return { draw };
}

function eachLayer(doc, fn) { doc.walk((l) => { if (l.type !== "group") fn(l); }); }
function shiftVector(l, dx, dy, k = 1) {
  if (l.type === "text") { l.text.x = l.text.x * k + dx; l.text.y = l.text.y * k + dy; l.text.size *= k; l.text.stroke *= k; if (l.text.width) l.text.width *= k; }
  else if (l.type === "shape") { const s = l.shape; s.x = s.x * k + dx; s.y = s.y * k + dy; s.w *= k; s.h *= k; s.strokeWidth *= k; s.radius *= k; if (s.points) s.points = s.points.map(([a, b]) => [a * k + dx, b * k + dy]); }
  else if (l.type === "svg") { const s = l.svg; s.x = s.x * k + dx; s.y = s.y * k + dy; s.w *= k; s.h *= k; }
}
function rasterizeLayer(doc, l) { if (!isVector(l)) return; const c = cloneCanvas(l.canvas); doc.swap(l, c); l.type = "raster"; delete l.text; delete l.shape; delete l.svg; }
function scaleCanvas(src, w, h) {

  let c = src, cw = src.width, ch = src.height;
  while (cw / 2 > w && ch / 2 > h) { const n = newCanvas(Math.round(cw / 2), Math.round(ch / 2)), x = ctx2d(n); x.imageSmoothingQuality = "high"; x.drawImage(c, 0, 0, n.width, n.height); c = n; cw = n.width; ch = n.height; }
  const out = newCanvas(w, h), x = ctx2d(out); x.imageSmoothingQuality = "high"; x.drawImage(c, 0, 0, w, h); return out;
}
export function resizeImage(ed, w, h, label = t("ph_image_size")) {
  const doc = ed.doc, kx = w / doc.width, ky = h / doc.height;
  doc.begin(label);
  eachLayer(doc, (l) => {
    if (isVector(l)) { shiftVector(l, 0, 0, Math.sqrt(kx * ky)); if (l.type === "shape") { l.shape.x = l.shape.x / Math.sqrt(kx * ky) * kx; } return; }
    const nw = Math.max(1, Math.round(l.canvas.width * kx)), nh = Math.max(1, Math.round(l.canvas.height * ky));
    doc.swap(l, scaleCanvas(l.canvas, nw, nh)); if (l.mask) doc.swap(l, scaleCanvas(l.mask, nw, nh), "mask");
    l.x = Math.round(l.x * kx); l.y = Math.round(l.y * ky);
  });
  doc.walk((l) => { if (l.type === "group" && l.mask) doc.swap(l, scaleCanvas(l.mask, w, h), "mask"); });
  doc.width = w; doc.height = h; doc.setSelection(null);
  doc.guides = doc.guides.map((g) => ({ ...g, pos: Math.round(g.pos * (g.axis === "x" ? kx : ky)) }));
  eachLayer(doc, (l) => { if (isVector(l)) renderVector(doc, l); });
  doc.commit(`${label} ${w}×${h}`);
  ed.fit();
}
export function canvasSize(ed, w, h, ax = 0.5, ay = 0.5, fill = null) {
  const doc = ed.doc, dx = Math.round((w - doc.width) * ax), dy = Math.round((h - doc.height) * ay);
  doc.begin(t("ph_canvas_size"));
  eachLayer(doc, (l) => { if (isVector(l)) shiftVector(l, dx, dy); else { l.x += dx; l.y += dy; } });
  doc.walk((l) => { if (l.type === "group" && l.mask) { const m = newCanvas(w, h); ctx2d(m).drawImage(l.mask, dx, dy); doc.swap(l, m, "mask"); } });
  doc.width = w; doc.height = h; doc.setSelection(null); doc.guides = doc.guides.map((g) => ({ ...g, pos: g.pos + (g.axis === "x" ? dx : dy) }));
  if (fill) {
    const bottom = doc.layers[0];
    if (bottom && bottom.type === "raster") { const c = newCanvas(w, h), x = ctx2d(c); x.fillStyle = fill; x.fillRect(0, 0, w, h); x.drawImage(bottom.canvas, bottom.x, bottom.y); doc.swap(bottom, c); bottom.x = 0; bottom.y = 0; }
  }
  eachLayer(doc, (l) => { if (isVector(l)) renderVector(doc, l); });
  doc.commit(`${t("ph_canvas_size")} ${w}×${h}`); ed.fit();
}
export function cropTo(ed, r, angle = 0) {
  const doc = ed.doc;
  if (angle) {
    const pre = { w: doc.width, h: doc.height };
    rotateCanvas(ed, -angle, false);
    r = { x: Math.round(r.x + (doc.width - pre.w) / 2), y: Math.round(r.y + (doc.height - pre.h) / 2), w: r.w, h: r.h };
  }
  r = { x: Math.max(0, r.x), y: Math.max(0, r.y), w: Math.min(r.w, doc.width - Math.max(0, r.x)), h: Math.min(r.h, doc.height - Math.max(0, r.y)) };
  if (r.w < 1 || r.h < 1) { if (angle) doc.endGroup(); return; }
  doc.begin(t("ph_tool_crop"));
  eachLayer(doc, (l) => {
    if (isVector(l)) { shiftVector(l, -r.x, -r.y); return; }

    const c = newCanvas(r.w, r.h); ctx2d(c).drawImage(l.canvas, l.x - r.x, l.y - r.y);
    if (l.mask) { const m = newCanvas(r.w, r.h), mx = ctx2d(m); mx.fillStyle = "#fff"; mx.fillRect(0, 0, r.w, r.h); mx.clearRect(l.x - r.x, l.y - r.y, l.mask.width, l.mask.height); mx.drawImage(l.mask, l.x - r.x, l.y - r.y); doc.swap(l, m, "mask"); }
    doc.swap(l, c); l.x = 0; l.y = 0;
  });
  doc.walk((l) => { if (l.type === "group" && l.mask) { const m = newCanvas(r.w, r.h); ctx2d(m).drawImage(l.mask, -r.x, -r.y); doc.swap(l, m, "mask"); } });
  doc.width = r.w; doc.height = r.h; doc.setSelection(null); doc.guides = doc.guides.map((g) => ({ ...g, pos: g.pos - (g.axis === "x" ? r.x : r.y) })).filter((g) => g.pos >= 0 && g.pos <= (g.axis === "x" ? r.w : r.h));
  eachLayer(doc, (l) => { if (isVector(l)) renderVector(doc, l); });
  doc.commit(`${t("ph_tool_crop")} ${r.w}×${r.h}`);
  if (angle) { doc.endGroup(); }
  ed.fit();
}
export function rotateCanvas(ed, deg, fit = true) {
  const doc = ed.doc, a = (deg * Math.PI) / 180, cos = Math.abs(Math.cos(a)), sin = Math.abs(Math.sin(a));
  const w = Math.round(doc.width * cos + doc.height * sin), h = Math.round(doc.width * sin + doc.height * cos);
  ed._preRotate = { w: doc.width, h: doc.height };
  if (!fit) doc.beginGroup(t("ph_tool_crop"));
  doc.begin(t("ph_rotate_canvas"));
  let rasterized = false;
  const rotC = (src, ox, oy) => { const c = newCanvas(w, h), x = ctx2d(c); x.translate(w / 2, h / 2); x.rotate(a); x.translate(-doc.width / 2, -doc.height / 2); x.imageSmoothingQuality = "high"; x.drawImage(src, ox, oy); return c; };
  doc.walk((l) => {
    if (l.type === "group") { if (l.mask) doc.swap(l, rotC(l.mask, 0, 0), "mask"); return; }
    if (isVector(l)) { rasterizeLayer(doc, l); rasterized = true; }
    const c = rotC(l.canvas, l.x, l.y); if (l.mask) doc.swap(l, rotC(l.mask, l.x, l.y), "mask"); doc.swap(l, c); l.x = 0; l.y = 0;
  });
  doc.width = w; doc.height = h; doc.setSelection(null); doc.guides = [];
  doc.commit(`${t("ph_rotate_canvas")} ${deg}°`);
  if (rasterized) toast(t("ph_vectors_rasterized"));
  if (fit) ed.fit();
}
export function flipCanvas(ed, axis) {
  const doc = ed.doc; doc.begin(t(axis === "h" ? "ph_flip_canvas_h" : "ph_flip_canvas_v"));
  const flip = (src, ox, oy) => { const c = newCanvas(doc.width, doc.height), x = ctx2d(c); if (axis === "h") { x.translate(doc.width, 0); x.scale(-1, 1); } else { x.translate(0, doc.height); x.scale(1, -1); } x.drawImage(src, ox, oy); return c; };
  doc.walk((l) => {
    if (l.type === "group") { if (l.mask) doc.swap(l, flip(l.mask, 0, 0), "mask"); return; }
    if (l.type === "shape" || l.type === "svg") { if (l.type === "shape" && !["text"].includes(l.type)) { const s = l.shape; if (axis === "h") { s.x = doc.width - s.x - s.w; if (s.points) s.points = s.points.map(([px, py]) => [doc.width - px, py]); if (s.kind === "line" || s.kind === "arrow") { s.x = doc.width - (doc.width - s.x - s.w); s.x = doc.width - s.x; s.w = -s.w; } } else { s.y = doc.height - s.y - s.h; if (s.points) s.points = s.points.map(([px, py]) => [px, doc.height - py]); if (s.kind === "line" || s.kind === "arrow") { s.y = doc.height - (doc.height - s.y - s.h); s.y = doc.height - s.y; s.h = -s.h; } } renderVector(doc, l); return; } }
    if (isVector(l)) rasterizeLayer(doc, l);
    const c = flip(l.canvas, l.x, l.y); if (l.mask) doc.swap(l, flip(l.mask, l.x, l.y), "mask"); doc.swap(l, c); l.x = 0; l.y = 0;
  });
  if (doc.selection) { const m = doc.selection, w = doc.width, h = doc.height, o = new Uint8Array(m.length); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[y * w + x] = axis === "h" ? m[y * w + (w - 1 - x)] : m[(h - 1 - y) * w + x]; doc.setSelection(o); }
  doc.commit();
}
function transformLayer(ed, kind) {
  const doc = ed.doc, l = layerForPixels(ed); if (!l) return;
  const w = l.canvas.width, h = l.canvas.height, rot = kind === "cw" || kind === "ccw";
  const c = newCanvas(rot ? h : w, rot ? w : h), x = ctx2d(c);
  if (kind === "h") { x.translate(w, 0); x.scale(-1, 1); } else if (kind === "v") { x.translate(0, h); x.scale(1, -1); } else if (kind === "cw") { x.translate(h, 0); x.rotate(Math.PI / 2); } else if (kind === "ccw") { x.translate(0, w); x.rotate(-Math.PI / 2); } else if (kind === "180") { x.translate(w, h); x.rotate(Math.PI); }
  x.drawImage(l.canvas, 0, 0);
  doc.begin(t("ph_xf_" + kind));
  if (doc.selection && (kind === "h" || kind === "v") && l.x === 0 && l.y === 0 && w === doc.width && h === doc.height) {

    const sel = doc.selCanvas(), b = doc.selectionBounds(), part = newCanvas(w, h), px = ctx2d(part);
    px.save(); if (kind === "h") { px.translate(2 * b.x + b.w, 0); px.scale(-1, 1); } else { px.translate(0, 2 * b.y + b.h); px.scale(1, -1); }
    const cut = newCanvas(w, h), cx = ctx2d(cut); cx.drawImage(l.canvas, 0, 0); cx.globalCompositeOperation = "destination-in"; cx.drawImage(sel, 0, 0);
    px.drawImage(cut, 0, 0); px.restore();
    const out = cloneCanvas(l.canvas), ox = ctx2d(out); ox.globalCompositeOperation = "destination-out"; ox.drawImage(sel, 0, 0); ox.globalCompositeOperation = "source-over"; ox.drawImage(part, 0, 0);
    doc.swap(l, out); doc.commit(); return;
  }
  const cxp = l.x + w / 2, cyp = l.y + h / 2;
  doc.swap(l, c); l.x = Math.round(cxp - c.width / 2); l.y = Math.round(cyp - c.height / 2);
  if (l.mask) { const m = newCanvas(c.width, c.height), mx = ctx2d(m); mx.setTransform(x.getTransform()); mx.drawImage(l.mask, 0, 0); doc.swap(l, m, "mask"); }
  doc.commit();
}
function align(ed, how) {
  const doc = ed.doc, l = doc.active; if (!l || l.locked) return;
  const ref = doc.selection ? doc.selectionBounds() : { x: 0, y: 0, w: doc.width, h: doc.height };
  const b = isVector(l) ? vectorBounds(doc, l) : l.type === "group" ? doc.layerBounds(l) : (() => { const cb = contentBounds(l.canvas); return cb ? { x: cb.x + l.x, y: cb.y + l.y, w: cb.w, h: cb.h } : null; })();
  if (!b || !ref) return;
  let dx = 0, dy = 0;
  if (how === "alignLeft") dx = ref.x - b.x; else if (how === "alignRight") dx = ref.x + ref.w - (b.x + b.w); else if (how === "alignHCenter") dx = ref.x + ref.w / 2 - (b.x + b.w / 2);
  else if (how === "alignTop") dy = ref.y - b.y; else if (how === "alignBottom") dy = ref.y + ref.h - (b.y + b.h); else if (how === "alignVCenter") dy = ref.y + ref.h / 2 - (b.y + b.h / 2);
  dx = Math.round(dx); dy = Math.round(dy); if (!dx && !dy) return;
  doc.begin(t("ph_" + how));
  const move = (x) => { if (x.type === "group") x.children.forEach(move); else if (isVector(x)) { shiftVector(x, dx, dy); renderVector(doc, x); } else { x.x += dx; x.y += dy; } };
  move(l); doc.commit();
}

function flatList(list, out = []) { for (const l of list) { out.push(l); if (l.children) flatList(l.children, out); } return out; }
function mergeInto(doc, upper, lower) {

  const c = newCanvas(doc.width, doc.height), x = ctx2d(c);
  const tmpDoc = { ...doc };
  x.drawImage(doc.flatten(null, { only: lower.id }), 0, 0);
  const up = doc.flatten(null, { only: upper.id });
  x.drawImage(up, 0, 0);
  return c;
}
export const RUN = {

  fit: (ed) => ed.fit(), zoom100: (ed) => ed.setZoom(1), fill: (ed) => ed.fit(true), zoomIn: (ed) => ed.zoomStep(1), zoomOut: (ed) => ed.zoomStep(-1),
  rulers: (ed) => { ed.showRulers = !ed.showRulers; localStorage.setItem("mn_photo_rulers", ed.showRulers ? "1" : "0"); ed.renderRulers(); ed.requestRender(); },
  grid: (ed) => { ed.showGrid = !ed.showGrid; ed.requestRender(); }, guides: (ed) => { ed.showGuides = !ed.showGuides; ed.requestRender(); },
  clearGuides: (ed) => { if (!ed.doc.guides.length) return; ed.doc.begin(t("ph_clear_guides")); ed.doc.guides = []; ed.doc.commit(); },
  async newGuide(ed) { const v = await prompt(t("ph_new_guide"), t("ph_new_guide_hint"), "v 100"); if (!v) return; const m = /^\s*([hv])\s*(-?\d+)/i.exec(v); if (!m) return; ed.doc.begin(t("ph_guide")); ed.doc.guides.push({ axis: m[1].toLowerCase() === "v" ? "x" : "y", pos: +m[2] }); ed.doc.commit(); },
  async gridSize(ed) { const v = await prompt(t("ph_grid_size"), t("ph_px"), String(ed.gridSize)); if (v && +v > 1) { ed.gridSize = Math.round(+v); ed.showGrid = true; ed.requestRender(); } },
  togglePanels: (ed) => ed.toggleSide(),

  undo: (ed) => ed.undo(), redo: (ed) => ed.redo(), stepBack: (ed) => ed.undo(),
  freeTransform: (ed) => ed.xfStart(),
  flipLayerH: (ed) => transformLayer(ed, "h"), flipLayerV: (ed) => transformLayer(ed, "v"), rotateLayerCW: (ed) => transformLayer(ed, "cw"), rotateLayerCCW: (ed) => transformLayer(ed, "ccw"), rotateLayer180: (ed) => transformLayer(ed, "180"),
  alignLeft: (ed) => align(ed, "alignLeft"), alignHCenter: (ed) => align(ed, "alignHCenter"), alignRight: (ed) => align(ed, "alignRight"), alignTop: (ed) => align(ed, "alignTop"), alignVCenter: (ed) => align(ed, "alignVCenter"), alignBottom: (ed) => align(ed, "alignBottom"),
  async copy(ed, merged = false) {
    const doc = ed.doc, src = merged ? doc.flatten() : (() => { const l = doc.active; if (!l || l.type === "group") return doc.flatten(); const c = newCanvas(doc.width, doc.height); ctx2d(c).drawImage(l.canvas, l.x, l.y); return c; })();
    let c = src;
    if (doc.selection) { const b = doc.selectionBounds(); c = newCanvas(b.w, b.h); const x = ctx2d(c); x.drawImage(src, -b.x, -b.y); x.globalCompositeOperation = "destination-in"; x.drawImage(doc.selCanvas(), -b.x, -b.y); ed.clipPos = { x: b.x, y: b.y }; } else ed.clipPos = { x: 0, y: 0 };
    ed.clip = c;
    try { await navigator.clipboard.write([new ClipboardItem({ "image/png": await canvasToBlob(c) })]); toast(t("ph_copied"), { kind: "ok", ms: 1400 }); }
    catch { toast(t("ph_copied_internal"), { ms: 2200 }); }
  },
  copyMerged: (ed) => RUN.copy(ed, true),
  async cut(ed) { await RUN.copy(ed); ed.clearSelection(); },
  async paste(ed) {
    try { const items = await navigator.clipboard.read(); for (const it of items) { const ty = it.types.find((x) => x.startsWith("image/")); if (ty) { ed.placeFile(new File([await it.getType(ty)], t("ph_pasted"), { type: ty }), t("ph_pasted")); return; } } }
    catch {  }
    if (ed.clip) ed.pasteInternal(); else toast(t("ph_clipboard_empty"));
  },
  pasteInPlace(ed) { if (!ed.clip) return RUN.paste(ed); const d = ed.doc; d.begin(t("ph_pasted")); d.insertLayer(d.makeLayer({ name: t("ph_pasted"), canvas: cloneCanvas(ed.clip), x: ed.clipPos?.x || 0, y: ed.clipPos?.y || 0 })); d.commit(); },
  async fillDialog(ed) {
    const r = await modal({ title: t("ph_fill"), body: `<label class="field"><span>${esc(t("ph_contents"))}</span><select class="input" id="pf-c"><option value="fg">${esc(t("ph_fg"))}</option><option value="bg">${esc(t("ph_bg"))}</option><option value="#ffffff">${esc(t("ph_bg_white"))}</option><option value="#000000">${esc(t("ph_bg_black"))}</option><option value="#808080">${esc(t("ph_gray50"))}</option><option value="aware">${esc(t("ph_content_aware"))}</option></select></label><label class="field"><span>${esc(t("ph_opacity"))} %</span><input class="input" id="pf-o" type="number" min="1" max="100" value="100"></label>`,
      buttons: [{ label: t("ph_cancel"), value: null }, { label: t("ph_ok"), primary: true, value: (b) => ({ c: b.querySelector("#pf-c").value, o: +b.querySelector("#pf-o").value }) }] });
    if (!r) return;
    if (r.c === "aware") return RUN.contentAwareFill(ed);
    fillWith(ed, r.c === "fg" ? ed.fg : r.c === "bg" ? ed.bg : r.c, r.o / 100);
  },
  fillFg: (ed) => fillWith(ed, ed.fg, 1), fillBg: (ed) => fillWith(ed, ed.bg, 1),
  async strokeDialog(ed) {
    if (!ed.doc.selection) { toast(t("ph_need_selection")); return; }
    const r = await modal({ title: t("ph_stroke_sel"), body: `<label class="field"><span>${esc(t("ph_stroke_width"))} px</span><input class="input" id="ps-w" type="number" min="1" max="200" value="4"></label><label class="field"><span>${esc(t("ph_color"))}</span><input class="input" id="ps-c" type="color" value="${ed.fg}"></label>`,
      buttons: [{ label: t("ph_cancel"), value: null }, { label: t("ph_ok"), primary: true, value: (b) => ({ w: +b.querySelector("#ps-w").value, c: b.querySelector("#ps-c").value }) }] });
    if (!r) return;
    const t0 = ed.rasterTarget(true); if (!t0) return;
    const doc = ed.doc, m = doc.selection, w = doc.width, h = doc.height, grown = growMask(m, w, h, Math.ceil(r.w / 2)), shrunk = growMask(m, w, h, -Math.floor(r.w / 2));
    const c = newCanvas(w, h), x = ctx2d(c), img = x.createImageData(w, h), col = parseHex(r.c);
    for (let i = 0; i < m.length; i++) { const v = Math.max(0, grown[i] - shrunk[i]); img.data[i * 4] = col[0]; img.data[i * 4 + 1] = col[1]; img.data[i * 4 + 2] = col[2]; img.data[i * 4 + 3] = v; }
    x.putImageData(img, 0, 0);
    doc.begin(t("ph_stroke_sel")); doc.touch(t0.canvas); ctx2d(t0.canvas).drawImage(c, -t0.x, -t0.y); doc.commit();
  },
  clear: (ed) => ed.clearSelection(),
  async contentAwareFill(ed) {
    const doc = ed.doc; if (!doc.selection) { toast(t("ph_need_selection")); return; }
    const t0 = ed.rasterTarget(true); if (!t0 || t0.isMask) return;
    const b = doc.selectionBounds(), pad = Math.max(16, Math.round(Math.max(b.w, b.h) * 0.4));
    const r = { x: Math.max(0, b.x - pad), y: Math.max(0, b.y - pad) }; r.w = Math.min(doc.width, b.x + b.w + pad) - r.x; r.h = Math.min(doc.height, b.y + b.h + pad) - r.y;
    const img = ctx2d(t0.canvas).getImageData(r.x, r.y, r.w, r.h), mask = new Uint8Array(r.w * r.h);
    for (let y = 0; y < r.h; y++) for (let x = 0; x < r.w; x++) mask[y * r.w + x] = doc.selection[(y + r.y) * doc.width + x + r.x] > 20 ? 255 : 0;
    ed.busy(true, t("ph_content_aware"));
    try {
      const res = await ed.worker({ kind: "inpaint", width: r.w, height: r.h, buffer: img.data.buffer, mask: mask.buffer, opts: { noise: 3 } }, [img.data.buffer, mask.buffer]);
      doc.begin(t("ph_content_aware")); doc.touch(t0.canvas, r); ctx2d(t0.canvas).putImageData(new ImageData(new Uint8ClampedArray(res.buffer), r.w, r.h), r.x, r.y); doc.commit();
    } finally { ed.busy(false); }
  },

  adjust: (ed, name) => adjustDialog(ed, name),
  autoTone: (ed) => applyOp(ed, "autoLevels", {}), desaturate: (ed) => applyOp(ed, "grayscale", {}), invert: (ed) => applyOp(ed, "invert", {}),
  lastFilter: (ed) => { if (ed.lastFilter) applyOp(ed, ed.lastFilter.name, ed.lastFilter.params); else toast(t("ph_no_last_filter")); },
  async imageSize(ed) {
    const d = ed.doc;
    const r = await modal({ title: t("ph_image_size"), body: `<div class="ph-form"><label class="field"><span>${esc(t("ph_width"))}</span><input class="input" id="is-w" type="number" min="1" max="30000" value="${d.width}"></label><label class="field"><span>${esc(t("ph_height"))}</span><input class="input" id="is-h" type="number" min="1" max="30000" value="${d.height}"></label><label class="field"><span>%</span><input class="input" id="is-p" type="number" min="1" max="1000" value="100"></label></div><label class="check"><input type="checkbox" id="is-k" checked> ${esc(t("ph_keep_ratio"))}</label>`,
      onOpen: (b) => { const w = b.querySelector("#is-w"), h = b.querySelector("#is-h"), p = b.querySelector("#is-p"), k = b.querySelector("#is-k"); w.oninput = () => { if (k.checked) h.value = Math.round((+w.value * d.height) / d.width); p.value = Math.round((+w.value / d.width) * 100); }; h.oninput = () => { if (k.checked) w.value = Math.round((+h.value * d.width) / d.height); }; p.oninput = () => { w.value = Math.round((d.width * +p.value) / 100); h.value = Math.round((d.height * +p.value) / 100); }; },
      buttons: [{ label: t("ph_cancel"), value: null }, { label: t("ph_ok"), primary: true, value: (b) => ({ w: +b.querySelector("#is-w").value, h: +b.querySelector("#is-h").value }) }] });
    if (!r || r.w < 1 || r.h < 1 || r.w > 30000 || r.h > 30000 || (r.w === d.width && r.h === d.height)) return;
    resizeImage(ed, Math.round(r.w), Math.round(r.h));
  },
  async canvasSizeDialog(ed) {
    const d = ed.doc;
    const r = await modal({ title: t("ph_canvas_size"), body: `<div class="ph-form"><label class="field"><span>${esc(t("ph_width"))}</span><input class="input" id="cs-w" type="number" min="1" max="30000" value="${d.width}"></label><label class="field"><span>${esc(t("ph_height"))}</span><input class="input" id="cs-h" type="number" min="1" max="30000" value="${d.height}"></label></div>
      <div class="field"><span>${esc(t("ph_anchor"))}</span><div class="ph-anchor">${[0, 0.5, 1].flatMap((y) => [0, 0.5, 1].map((x) => `<button type="button" data-ax="${x}" data-ay="${y}" class="${x === 0.5 && y === 0.5 ? "on" : ""}"></button>`)).join("")}</div></div>
      <label class="field"><span>${esc(t("ph_extension_fill"))}</span><select class="input" id="cs-f"><option value="">${esc(t("ph_bg_transparent"))}</option><option value="${ed.bg}">${esc(t("ph_bg"))}</option><option value="#ffffff">${esc(t("ph_bg_white"))}</option><option value="#000000">${esc(t("ph_bg_black"))}</option></select></label>`,
      onOpen: (b) => b.querySelectorAll("[data-ax]").forEach((x) => (x.onclick = () => b.querySelectorAll("[data-ax]").forEach((y) => y.classList.toggle("on", y === x)))),
      buttons: [{ label: t("ph_cancel"), value: null }, { label: t("ph_ok"), primary: true, value: (b) => { const on = b.querySelector("[data-ax].on"); return { w: +b.querySelector("#cs-w").value, h: +b.querySelector("#cs-h").value, ax: +on.dataset.ax, ay: +on.dataset.ay, f: b.querySelector("#cs-f").value }; } }] });
    if (!r || r.w < 1 || r.h < 1 || r.w > 30000 || r.h > 30000) return;
    canvasSize(ed, Math.round(r.w), Math.round(r.h), r.ax, r.ay, r.f || null);
  },
  rotateCW: (ed) => rotateCanvas(ed, 90), rotateCCW: (ed) => rotateCanvas(ed, -90), rotate180: (ed) => rotateCanvas(ed, 180),
  async rotateArbitrary(ed) { const v = await prompt(t("ph_rotate_canvas"), t("ph_angle") + " (°)", "15"); if (v && +v) rotateCanvas(ed, +v); },
  flipCanvasH: (ed) => flipCanvas(ed, "h"), flipCanvasV: (ed) => flipCanvas(ed, "v"),
  cropToSelection: (ed) => { const b = ed.doc.selectionBounds(); if (!b) { toast(t("ph_need_selection")); return; } cropTo(ed, b); },
  trim(ed) { const b = contentBounds(ed.doc.flatten()); if (!b) { toast(t("ph_layer_empty")); return; } if (b.w === ed.doc.width && b.h === ed.doc.height) { toast(t("ph_trim_nothing")); return; } cropTo(ed, b); },
  cropApply: (ed) => TOOLS.crop.apply(ed), cropReset: (ed) => TOOLS.crop.activate(ed),
  flatten(ed) {
    const d = ed.doc; if (d.flat().length <= 1 && d.layers[0]?.type === "raster" && !d.layers[0].mask) return;
    d.begin(t("ph_flatten")); const c = d.flatten(); const l = d.makeLayer({ name: t("ph_layer_background"), canvas: c }); d.layers = [l]; d.activeId = l.id; d.target = "layer"; d.commit();
  },

  newLayer(ed) { const d = ed.doc; d.begin(t("ph_new_layer")); const n = d.flat().filter((l) => l.type !== "group").length + 1; d.insertLayer(d.makeLayer({ name: `${t("ph_layer")} ${n}` })); d.commit(); },
  newGroup(ed) { const d = ed.doc; d.begin(t("ph_new_group")); d.insertLayer(d.makeLayer({ type: "group", name: t("ph_group") })); d.commit(); },
  groupLayers(ed) {
    const d = ed.doc, f = d.find(d.activeId); if (!f) return;
    d.begin(t("ph_group_layers")); const l = f.list.splice(f.index, 1)[0]; const g = d.makeLayer({ type: "group", name: t("ph_group") }); g.children = [l]; f.list.splice(f.index, 0, g); d.activeId = g.id; d.commit();
  },
  ungroup(ed) { const d = ed.doc, f = d.find(d.activeId); if (!f || f.layer.type !== "group") return; d.begin(t("ph_ungroup")); f.list.splice(f.index, 1, ...f.layer.children); d.activeId = f.layer.children[f.layer.children.length - 1]?.id || null; d.commit(); },
  duplicateLayer(ed) {
    const d = ed.doc, f = d.find(d.activeId); if (!f) return;
    const dup = (l) => { const o = { ...l, id: uidL(), name: l.name + " " + t("ph_copy_suffix") }; if (l.canvas) o.canvas = cloneCanvas(l.canvas); if (l.mask) o.mask = cloneCanvas(l.mask); if (l.text) o.text = { ...l.text }; if (l.shape) o.shape = { ...l.shape, points: l.shape.points?.map((p) => [...p]) }; if (l.svg) o.svg = { ...l.svg }; if (l.children) o.children = l.children.map((c) => ({ ...dup(c), name: c.name })); return o; };
    d.begin(t("ph_duplicate_layer")); const c = dup(f.layer); f.list.splice(f.index + 1, 0, c); d.activeId = c.id; d.commit();
  },
  deleteLayer(ed) { const d = ed.doc; if (!d.activeId) return; if (d.flat().length <= 1) { toast(t("ph_last_layer")); return; } d.begin(t("ph_delete_layer")); d.removeLayer(d.activeId); d.target = "layer"; d.commit(); },
  layerViaCopy(ed, cut = false) {
    const d = ed.doc, l = d.active; if (!l || l.type === "group") return;
    const c = newCanvas(d.width, d.height), x = ctx2d(c); x.drawImage(isVector(l) ? l.canvas : l.canvas, l.x, l.y);
    if (d.selection) { x.globalCompositeOperation = "destination-in"; x.drawImage(d.selCanvas(), 0, 0); }
    d.begin(t(cut ? "ph_layer_via_cut" : "ph_layer_via_copy"));
    if (cut && d.selection && l.type === "raster") { d.touch(l.canvas); const lx = ctx2d(l.canvas); lx.globalCompositeOperation = "destination-out"; lx.drawImage(d.selCanvas(), -l.x, -l.y); lx.globalCompositeOperation = "source-over"; }
    d.insertLayer(d.makeLayer({ name: l.name + " " + t("ph_copy_suffix"), canvas: c })); d.commit();
  },
  layerViaCut: (ed) => RUN.layerViaCopy(ed, true),
  mergeDown(ed) {
    const d = ed.doc, f = d.find(d.activeId); if (!f || f.index === 0) { toast(t("ph_nothing_below")); return; }
    const upper = f.layer, lower = f.list[f.index - 1];
    if (lower.locked) { toast(t("ph_layer_locked")); return; }
    d.begin(t("ph_merge_down"));
    const c = newCanvas(d.width, d.height), x = ctx2d(c);
    x.drawImage(d.flatten(null, { only: lower.id }), 0, 0); x.drawImage(d.flatten(null, { only: upper.id }), 0, 0);
    const merged = d.makeLayer({ name: lower.name, canvas: c });
    f.list.splice(f.index - 1, 2, merged); d.activeId = merged.id; d.target = "layer"; d.commit();
  },
  mergeVisible(ed) {
    const d = ed.doc; d.begin(t("ph_merge_visible")); const c = d.flatten(); const hidden = []; d.walk((l, list, i, parent) => { if (!parent && !l.visible) hidden.push(l); });
    const m = d.makeLayer({ name: t("ph_merged"), canvas: c }); d.layers = [...hidden, m]; d.activeId = m.id; d.target = "layer"; d.commit();
  },
  rasterize(ed) { const d = ed.doc, l = d.active; if (!l || !isVector(l)) return; d.begin(t("ph_rasterize")); rasterizeLayer(d, l); d.commit(); ed.refreshPanels(); },
  addMask(ed, how) {
    const d = ed.doc, l = d.active; if (!l) return; if (l.mask) { d.target = "mask"; ed.refreshPanels(); return; }
    how = how || (d.selection ? "selection" : "reveal");
    const w = l.type === "group" ? d.width : l.canvas.width, h = l.type === "group" ? d.height : l.canvas.height, ox = l.type === "group" ? 0 : l.x, oy = l.type === "group" ? 0 : l.y;
    const m = newCanvas(w, h), x = ctx2d(m);
    if (how === "reveal") { x.fillStyle = "#fff"; x.fillRect(0, 0, w, h); }
    else if (how === "selection" && d.selection) { x.drawImage(d.selCanvas(), -ox, -oy); }
    d.begin(t("ph_add_mask")); l.mask = m; l.maskOn = true; d.target = "mask"; if (how === "selection") d.setSelection(null); d.commit();
    toast(t("ph_mask_tip"), { ms: 5000 });
  },
  applyMask(ed) {
    const d = ed.doc, l = d.active; if (!l?.mask || l.type === "group") return;
    if (isVector(l)) rasterizeLayer(d, l);
    d.begin(t("ph_apply_mask")); const c = cloneCanvas(l.canvas), x = ctx2d(c); x.globalCompositeOperation = "destination-in"; x.drawImage(l.mask, 0, 0); d.swap(l, c); l.mask = null; d.target = "layer"; d.commit();
  },
  deleteMask(ed) { const d = ed.doc, l = d.active; if (!l?.mask) return; d.begin(t("ph_delete_mask")); l.mask = null; d.target = "layer"; d.commit(); },
  toggleMask(ed, id) { const d = ed.doc, l = id ? d.find(id)?.layer : d.active; if (!l?.mask) return; d.begin(t("ph_toggle_mask")); l.maskOn = !l.maskOn; d.commit(); },
  invertMaskCmd(ed) { const d = ed.doc, l = d.active; if (!l?.mask) return; d.begin(t("ph_invert_mask")); const c = newCanvas(l.mask.width, l.mask.height), x = ctx2d(c); x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height); x.globalCompositeOperation = "destination-out"; x.drawImage(l.mask, 0, 0); d.swap(l, c, "mask"); d.commit(); },
  selectionFromLayer(ed, id) {
    const d = ed.doc, l = id ? d.find(id)?.layer : d.active; if (!l) return;
    const c = newCanvas(d.width, d.height), x = ctx2d(c, { willReadFrequently: true }); x.drawImage(l.type === "group" ? d.flatten(null, { only: l.id }) : l.canvas, l.type === "group" ? 0 : l.x, l.type === "group" ? 0 : l.y);
    const a = x.getImageData(0, 0, d.width, d.height).data, m = new Uint8Array(d.width * d.height); for (let i = 0; i < m.length; i++) m[i] = a[i * 4 + 3];
    ed.select(m, "new", t("ph_sel_from_layer"));
  },
  setLayerProp(ed, key, value, id) { const d = ed.doc, l = id ? d.find(id)?.layer : d.active; if (!l || l[key] === value) return; d.begin(t("ph_layer_props")); l[key] = value; d.commit(`${t("ph_lp_" + key)}: ${key === "blend" ? t("ph_blend_" + value) : key === "visible" || key === "locked" ? (value ? "✓" : "✗") : value} — ${l.name}`); },
  soloLayer(ed, id) { const d = ed.doc; d.begin(t("ph_visibility")); const solo = d.flat().filter((l) => l.type !== "group" && l.id !== id).some((l) => l.visible); d.walk((l) => { if (l.type !== "group") l.visible = l.id === id || !solo; }); d.commit(); },
  moveLayerTo(ed, fromId, toId, where) {
    const d = ed.doc, from = d.find(fromId), to = d.find(toId); if (!from || !to || d.find(fromId).layer.children && d.containsId(from.layer, toId)) return;
    d.begin(t("ph_reorder"));
    const [l] = from.list.splice(from.index, 1); const tgt = d.find(toId);
    if (where === "into" && tgt.layer.type === "group") { tgt.layer.children.push(l); tgt.layer.open = true; }
    else tgt.list.splice(where === "above" ? tgt.index + 1 : tgt.index, 0, l);
    d.activeId = l.id; d.commit();
  },
  arrange(ed, how) {
    const d = ed.doc, f = d.find(d.activeId); if (!f) return; const n = f.list.length; let to = f.index;
    if (how === "forward") to = Math.min(n - 1, f.index + 1); else if (how === "backward") to = Math.max(0, f.index - 1); else if (how === "front") to = n - 1; else if (how === "back") to = 0;
    if (to === f.index) return; d.begin(t("ph_reorder")); const [l] = f.list.splice(f.index, 1); f.list.splice(to, 0, l); d.commit();
  },
  layerProps(ed) { ed.renameLayer(ed.doc.activeId); },
  newFillLayer(ed) { const d = ed.doc; d.begin(t("ph_fill_layer")); const l = d.makeLayer({ name: t("ph_fill_layer") }); const x = ctx2d(l.canvas); x.fillStyle = ed.fg; x.fillRect(0, 0, d.width, d.height); if (d.selection) { x.globalCompositeOperation = "destination-in"; x.drawImage(d.selCanvas(), 0, 0); } d.insertLayer(l); d.commit(); },

  selectAll: (ed) => { const m = new Uint8Array(ed.doc.width * ed.doc.height).fill(255); ed.select(m, "new", t("ph_select_all")); },
  deselect: (ed) => ed.select(null, "new", t("ph_deselect")),
  reselect: (ed) => { if (ed.doc.lastSelection) ed.select(ed.doc.lastSelection, "new", t("ph_reselect")); },
  inverse: (ed) => { if (!ed.doc.selection) return; ed.select(invertMask(ed.doc.selection), "new", t("ph_inverse")); },
  async colorRangeDialog(ed) {
    const v = await prompt(t("ph_color_range"), t("ph_fuzziness"), "40", { note: t("ph_color_range_note") }); if (v === null) return;
    const img = ctx2d(ed.doc.flatten(), { willReadFrequently: true }).getImageData(0, 0, ed.doc.width, ed.doc.height);
    ed.select(colorRange(img, parseHex(ed.fg), Math.max(1, +v || 40)), "new", t("ph_color_range"));
  },
  async modifySel(ed, kind) {
    if (!ed.doc.selection) { toast(t("ph_need_selection")); return; }
    const v = await prompt(t("ph_sel_" + kind), t("ph_px"), kind === "feather" ? "8" : "4"); if (!v || !(+v > 0)) return;
    const d = ed.doc, n = +v;
    if (kind === "feather") { ed.select(await ed.feather(d.selection.slice(), n), "new", t("ph_sel_feather")); return; }
    const res = await ed.worker({ kind: "mask", width: d.width, height: d.height, buffer: new ArrayBuffer(4), mask: d.selection.slice().buffer, opts: { grow: kind === "grow" ? n : kind === "shrink" ? -n : n } }, []);
    let m = new Uint8Array(res.mask);
    if (kind === "border") { const inner = growMask(d.selection, d.width, d.height, -n); m = m.map((v2, i) => Math.max(0, v2 - inner[i])); }
    ed.select(m, "new", t("ph_sel_" + kind));
  },
  async selectSubject(ed) { ed.busy(true, t("ph_select_subject")); try { const d = ed.doc, m = await ed.segmentBox({ x: d.width * 0.02, y: d.height * 0.02, w: d.width * 0.96, h: d.height * 0.96 }); ed.select(m, "new", t("ph_select_subject")); } finally { ed.busy(false); } },
  aiSelect: (ed) => ed.ai.promptSelect(),

  newImage: (ed) => ed.hooks.newImage?.(), open: (ed) => ed.hooks.open?.(), save: (ed) => ed.hooks.save?.(), saveAs: (ed) => ed.saveAs(false), exportAs: (ed) => ed.saveAs(true),
  async place(ed) {
    const paths = await ed.hooks.fileDialog?.("open", { filters: [t("ph_filter_images") + " (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp;*.svg;*.avif;*.ico)"], multiple: true });
    for (const p of paths || []) { try { const r = await fetch(assetUrl(p)); if (!r.ok) throw new Error("HTTP " + r.status); const b = await r.blob(); await ed.placeFile(new File([b], p.split(/[\\/]/).pop(), { type: b.type })); } catch (e) { toast(t("ph_place_failed") + ": " + e.message, { kind: "error" }); } }
  },
  async importFrames(ed) {
    if (!ed.tab.path) return;
    ed.busy(true, t("ph_frames_import"));
    try {
      const r = await framesOf(ed.tab.path), d = ed.doc;
      d.begin(t("ph_frames_import"));
      const g = d.makeLayer({ type: "group", name: t("ph_frames") });
      for (let i = 0; i < r.frames.length; i++) { const f = r.frames[i], c = await canvasFromB64(f.png); const l = d.makeLayer({ name: f.label || `${t("ph_frame")} ${i + 1}${f.duration ? ` (${f.duration} ms)` : ""}`, canvas: c, visible: i === 0 }); g.children.unshift(l); }
      d.layers.push(g); d.activeId = g.id; d.commit();
      if (r.total > r.frames.length) toast(t("ph_frames_capped", { n: r.frames.length, total: r.total }));
    } finally { ed.busy(false); }
  },
};
function fillWith(ed, color, alpha = 1) {
  const t0 = ed.rasterTarget(true); if (!t0) return;
  const d = ed.doc, c = newCanvas(t0.canvas.width, t0.canvas.height), x = ctx2d(c);
  x.fillStyle = t0.isMask ? (color === "#000000" ? "#000" : "#fff") : color; x.fillRect(0, 0, c.width, c.height);
  if (d.selection) { x.globalCompositeOperation = "destination-in"; x.drawImage(d.selCanvas(), -t0.x, -t0.y); }
  d.begin(t("ph_fill")); d.touch(t0.canvas); const tx = ctx2d(t0.canvas); tx.globalAlpha = alpha;
  if (t0.isMask && color === "#000000") tx.globalCompositeOperation = "destination-out";
  tx.drawImage(c, 0, 0); tx.globalAlpha = 1; tx.globalCompositeOperation = "source-over"; d.commit();
}

async function exportDialog(ed, exportOnly) {
  const tab = ed.tab, cur = formatOfPath(tab.path || "") || (ed.doc.flat().length > 1 ? "memuimg" : "png");
  const caps = await api("photo/caps").catch(() => ({ codecs: { encoders: [] } }));
  const serverOk = (f) => (caps.codecs?.encoders || []).includes(f);
  const fmts = [["png", "PNG"], ["jpeg", "JPEG"], ["webp", "WebP"], ["avif", "AVIF"], ["gif", "GIF"], ["bmp", "BMP"], ["tiff", "TIFF"], ["ico", "ICO"], ["tga", "TGA"], ["pdf", "PDF"], ["svg", t("ph_fmt_svg")], ["psd", t("ph_fmt_psd")], ["memuimg", t("ph_fmt_memuimg")]]
    .filter(([f]) => SAVE_FORMATS[f].browser || ["svg", "memuimg"].includes(f) || serverOk(f) || f === "psd");
  const r = await modal({ title: t(exportOnly ? "ph_export" : "ph_save_as"), body: `<div class="ph-form"><label class="field"><span>${esc(t("ph_format"))}</span><select class="input" id="ex-f">${fmts.map(([f, l]) => `<option value="${f}" ${f === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>
      <label class="field" id="ex-q-row"><span>${esc(t("ph_quality"))} <b id="ex-qv">${ed.opts.export.quality}</b></span><input type="range" id="ex-q" min="1" max="100" value="${ed.opts.export.quality}"></label>
      <label class="field" id="ex-bg-row"><span>${esc(t("ph_background"))}</span><input class="input" type="color" id="ex-bg" value="#ffffff"></label></div>
      <p class="muted small" id="ex-note"></p>`,
    onOpen: (b) => {
      const f = b.querySelector("#ex-f"), q = b.querySelector("#ex-q");
      const upd = () => { const s = SAVE_FORMATS[f.value]; b.querySelector("#ex-q-row").hidden = !s.quality; b.querySelector("#ex-bg-row").hidden = !!s.alpha; b.querySelector("#ex-note").textContent = t(s.layers ? "ph_fmt_layers_note" : "ph_fmt_flat_note") + (f.value === "svg" ? " " + t("ph_svg_note") : f.value === "ico" ? " " + t("ph_ico_note") : ""); };
      f.onchange = upd; q.oninput = () => (b.querySelector("#ex-qv").textContent = q.value); upd();
    },
    buttons: [{ label: t("ph_cancel"), value: null }, { label: t("ph_continue"), primary: true, value: (b) => ({ f: b.querySelector("#ex-f").value, q: +b.querySelector("#ex-q").value, bg: b.querySelector("#ex-bg").value }) }] });
  if (!r) return false;
  ed.opts.export.quality = r.q; (await import("./editor.js")).saveOpts(ed.opts);
  const spec = SAVE_FORMATS[r.f], base = (tab.path ? tab.path.split(/[\\/]/).pop() : tab.name || "image").replace(/\.[^.]+$/, "");
  const paths = await ed.hooks.fileDialog?.("save", { directory: tab.path ? tab.path.replace(/[\\/][^\\/]*$/, "") : "", filename: base + spec.ext, filters: [`${r.f.toUpperCase()} (*${spec.ext})`], multiple: false });
  let path = paths?.[0]; if (!path) return false;
  if (formatOfPath(path) !== r.f) path += spec.ext;
  ed.busy(true, t("ph_saving"));
  try {
    const res = await saveDocument(ed.doc, path, r.f, { force: true, quality: r.q, background: r.bg });
    if (!res.ok) throw new Error(res.error || "save failed");
    toast(t(exportOnly ? "ph_exported" : "ph_saved", { name: path.split(/[\\/]/).pop() }), { kind: "ok" });
    if (!exportOnly) { ed.doc.markSaved(); ed.info = { ...(ed.info || {}), format: r.f.toUpperCase() }; ed.hooks.afterSaveAs?.(path, res); ed.hooks.onDirty?.(false); }
    return true;
  } catch (e) { toast(t("ph_save_failed") + ": " + e.message, { kind: "error", ms: 9000 }); return false; }
  finally { ed.busy(false); }
}

function item(ed, label, cmd, k, extra = {}) { return { label: t(label), kbd: k ? kbd(k) : "", run: typeof cmd === "function" ? cmd : () => ed.cmd(cmd), ...extra }; }
const ADJ_MENU = ["brightnessContrast", "levels", "curves", "exposure", "-", "vibrance", "hueSaturation", "colorBalance", "temperature", "blackWhite", "sepia", "-", "invert", "posterize", "threshold", "replaceColor"];
const ADJ_KEYS = { levels: "Ctrl+L", curves: "Ctrl+M", hueSaturation: "Ctrl+U", colorBalance: "Ctrl+B", invert: "Ctrl+I" };
function menu(ed, id) {
  const d = ed.doc, has = !!d, l = d?.active, sel = !!d?.selection;
  const dis = (x) => ({ disabled: !has || x });
  if (id === "file") return [item(ed, "ph_new_image", "newImage", ""), item(ed, "ph_open", "open", "Ctrl+O"), "-", item(ed, "ph_save", "save", "Ctrl+S", dis(false)), item(ed, "ph_save_as", "saveAs", "Ctrl+Shift+S", dis(false)), item(ed, "ph_export", "exportAs", "Ctrl+Alt+Shift+S", dis(false)), "-", item(ed, "ph_place", "place", "", dis(false)), item(ed, "ph_frames_import", "importFrames", "", dis(!ed.info?.frames || ed.info.frames < 2)), "-", item(ed, "ph_copy_merged", "copyMerged", "Ctrl+Shift+C", dis(false))];
  if (id === "edit") return [item(ed, "ph_undo", "undo", "Ctrl+Z", dis(!d?.history.length)), item(ed, "ph_redo", "redo", "Ctrl+Shift+Z", dis(!d?.future.length)), "-", item(ed, "ph_cut", "cut", "Ctrl+X", dis(false)), item(ed, "ph_copy", "copy", "Ctrl+C", dis(false)), item(ed, "ph_copy_merged", "copyMerged", "Ctrl+Shift+C", dis(false)), item(ed, "ph_paste", "paste", "Ctrl+V", dis(false)), item(ed, "ph_paste_in_place", "pasteInPlace", "Ctrl+Shift+V", dis(!ed.clip)), item(ed, "ph_clear", "clear", "Del", dis(false)), "-",
    item(ed, "ph_fill", "fillDialog", "Shift+F5", dis(false)), item(ed, "ph_content_aware", "contentAwareFill", "", dis(!sel)), item(ed, "ph_stroke_sel", "strokeDialog", "", dis(!sel)), "-", item(ed, "ph_free_transform", "freeTransform", "Ctrl+T", dis(false)),
    { label: t("ph_transform"), disabled: !has, sub: [item(ed, "ph_xf_h", "flipLayerH"), item(ed, "ph_xf_v", "flipLayerV"), item(ed, "ph_xf_cw", "rotateLayerCW"), item(ed, "ph_xf_ccw", "rotateLayerCCW"), item(ed, "ph_xf_180", "rotateLayer180")] }];
  if (id === "image") return [{ label: t("ph_adjustments"), disabled: !has, sub: ADJ_MENU.map((k) => (k === "-" ? "-" : item(ed, "ph_adj_" + k, () => ed.cmd("adjust", k), ADJ_KEYS[k]))) }, item(ed, "ph_auto_tone", "autoTone", "Ctrl+Shift+Alt+L", dis(false)), item(ed, "ph_desaturate", "desaturate", "Ctrl+Shift+U", dis(false)), "-",
    item(ed, "ph_image_size", "imageSize", "Ctrl+Alt+I", dis(false)), item(ed, "ph_canvas_size", "canvasSizeDialog", "Ctrl+Alt+C", dis(false)),
    { label: t("ph_rotate_canvas"), disabled: !has, sub: [item(ed, "ph_rot_cw", "rotateCW"), item(ed, "ph_rot_ccw", "rotateCCW"), item(ed, "ph_rot_180", "rotate180"), item(ed, "ph_rot_any", "rotateArbitrary"), "-", item(ed, "ph_flip_canvas_h", "flipCanvasH"), item(ed, "ph_flip_canvas_v", "flipCanvasV")] },
    item(ed, "ph_crop_to_sel", "cropToSelection", "", dis(!sel)), item(ed, "ph_trim", "trim", "", dis(false)), "-", item(ed, "ph_flatten", "flatten", "", dis(false))];
  if (id === "layer") return [item(ed, "ph_new_layer", "newLayer", "Ctrl+Shift+N", dis(false)), item(ed, "ph_new_group", "newGroup", "", dis(false)), item(ed, "ph_fill_layer", "newFillLayer", "", dis(false)), item(ed, "ph_duplicate_layer", "duplicateLayer", "", dis(!l)), item(ed, "ph_delete_layer", "deleteLayer", "", dis(!l)), item(ed, "ph_rename_layer", "layerProps", "", dis(!l)), "-",
    item(ed, "ph_layer_via_copy", "layerViaCopy", "Ctrl+J", dis(!l)), item(ed, "ph_layer_via_cut", "layerViaCut", "Ctrl+Shift+J", dis(!l || !sel)), item(ed, "ph_group_layers", "groupLayers", "Ctrl+G", dis(!l)), item(ed, "ph_ungroup", "ungroup", "Ctrl+Shift+G", dis(l?.type !== "group")), "-",
    { label: t("ph_layer_mask"), disabled: !l, sub: [item(ed, "ph_mask_reveal", () => ed.cmd("addMask", "reveal")), item(ed, "ph_mask_hide", () => ed.cmd("addMask", "hide")), item(ed, "ph_mask_selection", () => ed.cmd("addMask", "selection"), "", { disabled: !sel }), "-", item(ed, "ph_toggle_mask", "toggleMask", "", { disabled: !l?.mask }), item(ed, "ph_invert_mask", "invertMaskCmd", "", { disabled: !l?.mask }), item(ed, "ph_apply_mask", "applyMask", "", { disabled: !l?.mask }), item(ed, "ph_delete_mask", "deleteMask", "", { disabled: !l?.mask })] },
    item(ed, "ph_sel_from_layer", "selectionFromLayer", "Ctrl+Click", dis(!l)), item(ed, "ph_rasterize", "rasterize", "", dis(!isVector(l))), "-",
    { label: t("ph_arrange"), disabled: !l, sub: [item(ed, "ph_bring_front", () => ed.cmd("arrange", "front"), "Ctrl+Shift+]"), item(ed, "ph_bring_forward", () => ed.cmd("arrange", "forward"), "Ctrl+]"), item(ed, "ph_send_backward", () => ed.cmd("arrange", "backward"), "Ctrl+["), item(ed, "ph_send_back", () => ed.cmd("arrange", "back"), "Ctrl+Shift+[")] },
    item(ed, "ph_merge_down", "mergeDown", "Ctrl+E", dis(!l)), item(ed, "ph_merge_visible", "mergeVisible", "Ctrl+Shift+E", dis(false)), item(ed, "ph_flatten", "flatten", "", dis(false))];
  if (id === "select") return [item(ed, "ph_select_all", "selectAll", "Ctrl+A", dis(false)), item(ed, "ph_deselect", "deselect", "Ctrl+D", dis(!sel)), item(ed, "ph_reselect", "reselect", "Ctrl+Shift+D", dis(!d?.lastSelection)), item(ed, "ph_inverse", "inverse", "Ctrl+Shift+I", dis(!sel)), "-",
    item(ed, "ph_select_subject", "selectSubject", "", dis(false)), item(ed, "ph_ai_select", "aiSelect", "", dis(false)), item(ed, "ph_color_range", "colorRangeDialog", "", dis(false)), "-",
    ...["grow", "shrink", "feather", "border"].map((k) => item(ed, "ph_sel_" + k, () => ed.cmd("modifySel", k), k === "feather" ? "Shift+F6" : "", dis(!sel)))];
  if (id === "filter") return [item(ed, "ph_last_filter", "lastFilter", "Ctrl+F", dis(!ed.lastFilter)), "-",
    { label: t("ph_f_blur"), disabled: !has, sub: ["gaussianBlur", "boxBlur", "motionBlur"].map((k) => item(ed, "ph_adj_" + k, () => ed.cmd("adjust", k))) },
    { label: t("ph_f_sharpen"), disabled: !has, sub: ["sharpen", "unsharpMask"].map((k) => item(ed, "ph_adj_" + k, () => ed.cmd("adjust", k))) },
    { label: t("ph_f_noise"), disabled: !has, sub: ["noise", "denoise"].map((k) => item(ed, "ph_adj_" + k, () => ed.cmd("adjust", k))) },
    { label: t("ph_f_stylize"), disabled: !has, sub: ["emboss", "edges", "oilPaint", "pixelate", "vignette"].map((k) => item(ed, "ph_adj_" + k, () => ed.cmd("adjust", k))) }];
  if (id === "view") return [item(ed, "ph_zoom_in", "zoomIn", "Ctrl+=", dis(false)), item(ed, "ph_zoom_out", "zoomOut", "Ctrl+-", dis(false)), item(ed, "ph_fit", "fit", "Ctrl+0", dis(false)), item(ed, "ph_zoom_100", "zoom100", "Ctrl+Alt+0", dis(false)), "-",
    item(ed, "ph_rulers", "rulers", "Ctrl+R", { checked: ed.showRulers }), item(ed, "ph_grid", "grid", "Ctrl+'", { checked: ed.showGrid }), item(ed, "ph_grid_size", "gridSize", ""), item(ed, "ph_guides", "guides", "Ctrl+;", { checked: ed.showGuides }), item(ed, "ph_new_guide", "newGuide", "", dis(false)), item(ed, "ph_clear_guides", "clearGuides", "", dis(!d?.guides.length)), "-",
    item(ed, "ph_panels", "togglePanels", "Tab", { checked: ed.sideOpen }), item(ed, "ph_shortcuts", () => showShortcuts(ed), "F1")];
  if (id === "ai") return ed.ai.menuItems();
  return null;
}
function context(ed, p) {
  const sel = !!ed.doc.selection;
  const hit = ed.layerAt(p);
  return [
    ...(hit && hit.id !== ed.doc.activeId ? [{ label: t("ph_select_layer", { name: hit.name }), run: () => { ed.doc.activeId = hit.id; ed.refreshPanels(); } }, "-"] : []),
    item(ed, "ph_undo", "undo", "Ctrl+Z", { disabled: !ed.doc.history.length }),
    "-", item(ed, sel ? "ph_deselect" : "ph_select_all", sel ? "deselect" : "selectAll", sel ? "Ctrl+D" : "Ctrl+A"), ...(sel ? [item(ed, "ph_inverse", "inverse", "Ctrl+Shift+I"), item(ed, "ph_layer_via_copy", "layerViaCopy", "Ctrl+J"), item(ed, "ph_content_aware", "contentAwareFill"), item(ed, "ph_crop_to_sel", "cropToSelection")] : [item(ed, "ph_select_subject", "selectSubject")]),
    "-", item(ed, "ph_copy", "copy", "Ctrl+C"), item(ed, "ph_paste", "paste", "Ctrl+V"), item(ed, "ph_free_transform", "freeTransform", "Ctrl+T"),
    "-", { label: t("ph_ai_ask_here"), icon: "sparkle", run: () => ed.ai.ask(t("ph_ai_what_here", { x: Math.round(p.x), y: Math.round(p.y) })) },
  ];
}
function showShortcuts(ed) {
  const rows = [["V", "ph_tool_move"], ["M", "ph_tool_marquee"], ["L", "ph_tool_lasso"], ["W", "ph_tool_quicksel"], ["C", "ph_tool_crop"], ["I", "ph_tool_eyedropper"], ["J", "ph_tool_heal"], ["B", "ph_tool_brush"], ["S", "ph_tool_clone"], ["E", "ph_tool_eraser"], ["G", "ph_tool_gradient"], ["R", "ph_tool_blur"], ["O", "ph_tool_dodge"], ["T", "ph_tool_text"], ["U", "ph_tool_shape"], ["H / Space", "ph_tool_hand"], ["Z", "ph_tool_zoom"],
    ["Shift+letter", "ph_sc_cycle"], ["[ / ]", "ph_sc_size"], ["{ / }", "ph_sc_hardness"], ["1…0", "ph_sc_opacity"], ["X", "ph_swap_colors"], ["D", "ph_sc_default_colors"], ["Alt+Click", "ph_sc_alt"], ["Shift", "ph_sc_shift"],
    ["Ctrl+Z / Ctrl+Shift+Z", "ph_sc_undo"], ["Ctrl+A / Ctrl+D", "ph_sc_select"], ["Ctrl+Shift+I", "ph_inverse"], ["Ctrl+T", "ph_free_transform"], ["Ctrl+J", "ph_layer_via_copy"], ["Ctrl+E", "ph_merge_down"], ["Ctrl+G", "ph_group_layers"], ["Ctrl+Shift+N", "ph_new_layer"],
    ["Ctrl+L / M / U / B / I", "ph_sc_adjust"], ["Ctrl+Shift+U", "ph_desaturate"], ["Ctrl+Alt+I / C", "ph_sc_sizes"], ["Ctrl+0 / Ctrl+Alt+0", "ph_sc_zoom"], ["Ctrl+R", "ph_rulers"], ["Ctrl+'", "ph_grid"], ["Del", "ph_clear"], ["Shift+F5", "ph_fill"], ["Tab", "ph_panels"], ["Enter / Esc", "ph_sc_commit"]];
  modal({ title: t("ph_shortcuts"), wide: true, body: `<div class="shortcuts">${rows.map(([k, l]) => `<div><kbd>${esc(kbd(k))}</kbd><span>${esc(t(l))}</span></div>`).join("")}</div>`, buttons: [{ label: t("ph_close"), primary: true, value: true }] });
}

function shortcut(ed, e) {
  const mod = e.ctrlKey || e.metaKey, sh = e.shiftKey, alt = e.altKey, lk = globalThis.MemuKeys ? MemuKeys.latin(e) : e.key, k = lk.length === 1 ? lk.toLowerCase() : lk, code = e.code;
  if (e.key === "F1") return () => showShortcuts(ed);
  if (!mod) {
    if (sh && e.key === "F5") return () => ed.cmd("fillDialog");
    if (sh && e.key === "F6") return () => ed.cmd("modifySel", "feather");
    if (e.key === "Enter" && ed.toolId === "crop") return () => ed.cmd("cropApply");
    return null;
  }
  const C = (name, ...a) => () => ed.cmd(name, ...a);
  if (k === "z" && !sh && !alt) return () => ed.undo();
  if ((k === "z" && sh) || (k === "y" && !sh)) return () => ed.redo();
  if (k === "z" && alt) return () => ed.undo();
  if (k === "a" && !sh) return C("selectAll");
  if (k === "d" && !sh) return C("deselect");
  if (k === "d" && sh) return C("reselect");
  if (k === "i" && sh && !alt) return C("inverse");
  if (k === "i" && !sh && !alt) return C("invert");
  if (k === "i" && alt) return C("imageSize");
  if (k === "c" && alt && !sh) return C("canvasSizeDialog");
  if (k === "c" && sh) return C("copyMerged");
  if (k === "c") return C("copy");
  if (k === "x") return C("cut");
  if (k === "v" && sh) return C("pasteInPlace");
  if (k === "v") return null;
  if (k === "t") return C("freeTransform");
  if (k === "j" && sh) return C("layerViaCut");
  if (k === "j") return C("layerViaCopy");
  if (k === "e" && sh) return C("mergeVisible");
  if (k === "e") return C("mergeDown");
  if (k === "g" && sh) return C("ungroup");
  if (k === "g") return C("groupLayers");
  if (k === "n" && sh && !alt) return C("newLayer");
  if (k === "l" && sh && alt) return C("autoTone");
  if (k === "l" && !sh) return C("adjust", "levels");
  if (k === "m" && !sh) return C("adjust", "curves");
  if (k === "u" && sh) return C("desaturate");
  if (k === "u") return C("adjust", "hueSaturation");
  if (k === "b" && !sh) return C("adjust", "colorBalance");
  if (k === "f" && !sh) return C("lastFilter");
  if ((code === "BracketRight" || k === "]") && sh) return C("arrange", "front");
  if (code === "BracketRight" || k === "]") return C("arrange", "forward");
  if ((code === "BracketLeft" || k === "[") && sh) return C("arrange", "back");
  if (code === "BracketLeft" || k === "[") return C("arrange", "backward");
  if (k === "0" && alt) return C("zoom100");
  if (k === "0") return C("fit");
  if (k === "=" || k === "+") return C("zoomIn");
  if (k === "-") return C("zoomOut");
  if (k === "r" && !sh) return C("rulers");
  if (k === "'" || code === "Quote") return C("grid");
  if (k === ";" || code === "Semicolon") return C("guides");
  if (k === "s" && sh && alt) return C("exportAs");
  if (k === "s" && sh) return C("saveAs");
  if (k === "h" && !sh) return () => { ed.showGuides = !ed.showGuides; ed.showGrid = false; ed.requestRender(); };
  return null;
}

export const COMMANDS = { run: RUN, menu, context, shortcut, exportDialog, applyOp, resizeImage, cropTo, rotateCanvas, flipCanvas, adjustDialog };
