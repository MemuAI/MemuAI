// MemuNote photo editor: layer styles (shadow, glow, stroke, colour overlay), the border dialog and the
// before / after comparison. Mixed into PhotoEditor (COMPARE) or called from commands.js (fxDialog, frameDialog).
import { esc, t, toast } from "../core.js";
import { newCanvas, ctx2d, FX_KINDS, FX_DEFAULT, hasFx, cloneFx, sanitizeFx } from "./doc.js";
import { picon } from "./icons.js";

const FX_ROWS = {
  shadow: [["color"], ["opacity", 0, 100], ["angle", -180, 180], ["distance", 0, 200], ["size", 0, 150]],
  glow: [["color"], ["opacity", 0, 100], ["size", 1, 150]],
  stroke: [["color"], ["size", 1, 60], ["opacity", 0, 100]],
  overlay: [["color"], ["opacity", 0, 100]],
};
export const FX_PRESETS = {
  soft: { shadow: { on: true, color: "#000000", opacity: 45, angle: 120, distance: 12, size: 26 } },
  sticker: { stroke: { on: true, color: "#ffffff", size: 10, opacity: 100 }, shadow: { on: true, color: "#000000", opacity: 35, angle: 120, distance: 4, size: 10 } },
  neon: { glow: { on: true, color: "#22d3ee", opacity: 95, size: 30 }, stroke: { on: true, color: "#a5f3fc", size: 2, opacity: 100 } },
  outline: { stroke: { on: true, color: "#000000", size: 3, opacity: 100 } },
};
export const fxSummary = (fx) => FX_KINDS.filter((k) => fx?.[k]?.on).map((k) => t("ph_fx_" + k)).join(", ");

function dragHead(ed, dlg) {
  const head = dlg.querySelector(".ph-dlg-head");
  head.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    head.setPointerCapture(e.pointerId);
    const r = dlg.getBoundingClientRect(), sr = ed.stage.getBoundingClientRect(), ox = e.clientX - r.left, oy = e.clientY - r.top;
    head.onpointermove = (ev) => { dlg.style.left = Math.max(0, ev.clientX - sr.left - ox) + "px"; dlg.style.top = Math.max(0, ev.clientY - sr.top - oy) + "px"; dlg.style.right = "auto"; };
    head.onpointerup = () => { head.onpointermove = null; };
  });
}
function dialogKeys(dlg, close) {
  const key = (e) => {
    if (!dlg.isConnected) return document.removeEventListener("keydown", key, true);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(false); }
    else if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") { e.preventDefault(); e.stopPropagation(); close(true); }
  };
  document.addEventListener("keydown", key, true);
  return () => document.removeEventListener("keydown", key, true);
}

export function fxDialog(ed, focus) {
  const d = ed.doc, l = d?.active;
  if (!l) { toast(t("ph_no_layer")); return; }
  if (l.locked) { toast(t("ph_layer_locked")); return; }
  ed.stage.querySelector(".ph-dlg")?.remove();
  const old = l.fx ? cloneFx(l.fx) : null;
  const fx = {}; for (const k of FX_KINDS) fx[k] = { ...FX_DEFAULT[k], ...(old?.[k] || {}) };
  if (!hasFx(old)) fx[FX_KINDS.includes(focus) ? focus : "shadow"].on = true;
  else if (FX_KINDS.includes(focus)) fx[focus].on = true;
  const dlg = document.createElement("div"); dlg.className = "ph-dlg ph-fxdlg";
  let closed = false, raf = 0, unkey = null;
  const live = () => { raf = 0; if (closed) return; l.fx = cloneFx(fx); ed.layerChanged(); };
  const soon = () => { if (!raf) raf = requestAnimationFrame(live); };
  const row = (k, [p, mn, mx]) => p === "color"
    ? `<label class="ph-drow"><span>${esc(t("ph_p_color"))}</span><input type="color" data-k="${k}" data-p="color" value="${fx[k].color}"></label>`
    : `<label class="ph-drow"><span>${esc(t("ph_p_" + p))}</span><input type="range" min="${mn}" max="${mx}" step="1" value="${fx[k][p]}" data-k="${k}" data-p="${p}"><input class="ph-num" type="number" min="${mn}" max="${mx}" step="1" value="${fx[k][p]}" data-k="${k}" data-p="${p}"></label>`;
  const sec = (k) => `<div class="ph-fxsec ${fx[k].on ? "on" : ""}" data-sec="${k}"><label class="ph-dchk ph-fxh"><input type="checkbox" data-on="${k}" ${fx[k].on ? "checked" : ""}> <b>${esc(t("ph_fx_" + k))}</b></label><div class="ph-fxrows">${FX_ROWS[k].map((r) => row(k, r)).join("")}</div></div>`;
  const close = (apply) => {
    if (closed) return; closed = true; cancelAnimationFrame(raf); dlg.remove(); unkey?.();
    if (old) l.fx = cloneFx(old); else delete l.fx;
    const next = sanitizeFx(fx);
    if (apply && JSON.stringify(next) !== JSON.stringify(sanitizeFx(old))) {
      d.begin(t("ph_layer_style"));
      if (next) l.fx = next; else delete l.fx;
      d.commit(next ? `${t("ph_layer_style")} (${fxSummary(next)})` : t("ph_fx_clear"));
    }
    ed.layerChanged(); ed.refreshPanels();
  };
  const draw = () => {
    dlg.innerHTML = `<div class="ph-dlg-head">${picon("fx")}<b>${esc(t("ph_layer_style"))}</b><span class="ph-badge" title="${esc(l.name)}">${esc(String(l.name).slice(0, 24))}</span><span class="ph-sp"></span><button class="icon-btn sm" data-x>${picon("close")}</button></div>
      <div class="ph-dlg-body"><div class="ph-fxpresets">${Object.keys(FX_PRESETS).map((p) => `<button class="chip-btn" data-preset="${p}">${esc(t("ph_fx_preset_" + p))}</button>`).join("")}</div>${FX_KINDS.map(sec).join("")}</div>
      <div class="ph-dlg-foot"><button class="btn sm ghost" data-clear>${esc(t("ph_fx_clear"))}</button><span class="ph-sp"></span><button class="btn sm" data-cancel>${esc(t("ph_cancel"))}</button><button class="btn sm primary" data-ok>${esc(t("ph_ok"))}</button></div>`;
    dlg.querySelectorAll("[data-on]").forEach((c) => (c.onchange = () => { fx[c.dataset.on].on = c.checked; c.closest(".ph-fxsec").classList.toggle("on", c.checked); soon(); }));
    dlg.querySelectorAll("[data-p]").forEach((inp) => inp.addEventListener("input", () => {
      const k = inp.dataset.k, p = inp.dataset.p, v = p === "color" ? inp.value : +inp.value;
      if (p !== "color" && !Number.isFinite(v)) return;
      fx[k][p] = v;
      if (!fx[k].on) { fx[k].on = true; const cb = dlg.querySelector(`[data-on="${k}"]`); cb.checked = true; cb.closest(".ph-fxsec").classList.add("on"); }
      dlg.querySelectorAll(`[data-k="${k}"][data-p="${p}"]`).forEach((x) => { if (x !== inp) x.value = v; });
      soon();
    }));
    dlg.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => { const pr = FX_PRESETS[b.dataset.preset]; for (const k of FX_KINDS) fx[k] = { ...FX_DEFAULT[k], ...(pr[k] || {}), on: !!pr[k]?.on }; draw(); soon(); }));
    dlg.querySelector("[data-clear]").onclick = () => { for (const k of FX_KINDS) fx[k].on = false; draw(); soon(); };
    dlg.querySelector("[data-x]").onclick = () => close(false);
    dlg.querySelector("[data-cancel]").onclick = () => close(false);
    dlg.querySelector("[data-ok]").onclick = () => close(true);
    dragHead(ed, dlg);
  };
  draw();
  ed.stage.append(dlg);
  unkey = dialogKeys(dlg, close);
  soon();
  return { close };
}

function roundRectPath(x, rx, ry, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  x.moveTo(rx + r, ry); x.arcTo(rx + w, ry, rx + w, ry + h, r); x.arcTo(rx + w, ry + h, rx, ry + h, r); x.arcTo(rx, ry + h, rx, ry, r); x.arcTo(rx, ry, rx + w, ry, r); x.closePath();
}
export function frameCanvas(W, H, size, color, radius) {
  const c = newCanvas(W, H), x = ctx2d(c);
  x.fillStyle = color; x.fillRect(0, 0, W, H);
  x.globalCompositeOperation = "destination-out"; x.beginPath(); roundRectPath(x, size, size, W - 2 * size, H - 2 * size, radius); x.fill();
  x.globalCompositeOperation = "source-over";
  return c;
}
// Border: "outside" enlarges the canvas by the border width on every side, "inside" paints over the edges.
// Either way the border is a separate top layer, so it can be recoloured, hidden or removed.
export function frameDialog(ed, canvasSize) {
  const d = ed.doc; if (!d) return;
  ed.stage.querySelector(".ph-dlg")?.remove();
  const p = { size: Math.max(2, Math.round(Math.min(d.width, d.height) * 0.04)), color: localStorage.getItem("mn_photo_frame_color") || "#ffffff", corner: 0, mode: localStorage.getItem("mn_photo_frame_mode") === "inside" ? "inside" : "outside" };
  const maxSize = Math.max(4, Math.round(Math.max(d.width, d.height) / 3)), maxR = Math.max(4, Math.round(Math.min(d.width, d.height) / 2));
  const dlg = document.createElement("div"); dlg.className = "ph-dlg ph-framedlg";
  const num = (k, mn, mx) => `<label class="ph-drow"><span>${esc(t("ph_p_" + k))}</span><input type="range" min="${mn}" max="${mx}" step="1" value="${p[k]}" data-p="${k}"><input class="ph-num" type="number" min="${mn}" max="${mx}" step="1" value="${p[k]}" data-p="${k}"></label>`;
  dlg.innerHTML = `<div class="ph-dlg-head">${picon("frame")}<b>${esc(t("ph_frame_title"))}</b><span class="ph-sp"></span><button class="icon-btn sm" data-x>${picon("close")}</button></div>
    <div class="ph-dlg-body"><div class="ph-drow"><span>${esc(t("ph_frame_mode"))}</span><select class="input xs" data-p="mode"><option value="outside" ${p.mode === "outside" ? "selected" : ""}>${esc(t("ph_frame_outside"))}</option><option value="inside" ${p.mode === "inside" ? "selected" : ""}>${esc(t("ph_frame_inside"))}</option></select></div>
      ${num("size", 1, maxSize)}<label class="ph-drow"><span>${esc(t("ph_p_color"))}</span><input type="color" data-p="color" value="${p.color}"></label>${num("corner", 0, maxR)}
      <div class="ph-frame-hint muted small"></div></div>
    <div class="ph-dlg-foot"><span class="ph-sp"></span><button class="btn sm" data-cancel>${esc(t("ph_cancel"))}</button><button class="btn sm primary" data-ok>${esc(t("ph_ok"))}</button></div>`;
  const layer = d.makeLayer({ name: t("ph_frame_layer") });
  let closed = false, raf = 0;
  const preview = () => {
    raf = 0; if (closed) return;
    const s = Math.max(1, Math.round(p.size)), hint = dlg.querySelector(".ph-frame-hint");
    if (p.mode === "outside") {
      hint.textContent = t("ph_frame_hint", { from: `${d.width}×${d.height}`, to: `${d.width + 2 * s}×${d.height + 2 * s}` });
      ed.framePreview = { size: s, color: p.color, radius: p.corner };
    } else {
      ed.framePreview = { size: s, color: p.color, radius: p.corner, inside: true };
      hint.textContent = "";
    }
    ed.requestRender();
  };
  const soon = () => { if (!raf) raf = requestAnimationFrame(preview); };
  const close = (apply) => {
    if (closed) return; closed = true; cancelAnimationFrame(raf); dlg.remove(); unkey(); ed.framePreview = null; ed.requestRender();
    if (!apply) return;
    localStorage.setItem("mn_photo_frame_color", p.color); localStorage.setItem("mn_photo_frame_mode", p.mode);
    const s = Math.max(1, Math.round(p.size));
    d.beginGroup(t("ph_frame_title"));
    try {
      if (p.mode === "outside") canvasSize(ed, d.width + 2 * s, d.height + 2 * s, 0.5, 0.5, null);
      d.begin(t("ph_frame_title"));
      layer.canvas = frameCanvas(d.width, d.height, s, p.color, p.corner);
      d.layers.push(layer); d.activeId = layer.id; d.target = "layer";
      d.commit(`${t("ph_frame_title")} ${s} px`);
    } finally { d.endGroup(); }
    ed.refreshPanels();
  };
  const unkey = dialogKeys(dlg, close);
  dlg.querySelectorAll("[data-p]").forEach((inp) => inp.addEventListener("input", () => {
    const k = inp.dataset.p, v = inp.type === "range" || inp.type === "number" ? +inp.value : inp.value;
    if ((inp.type === "number" || inp.type === "range") && !Number.isFinite(v)) return;
    p[k] = v; dlg.querySelectorAll(`[data-p="${k}"]`).forEach((x) => { if (x !== inp) x.value = v; });
    soon();
  }));
  dlg.querySelector("[data-x]").onclick = () => close(false);
  dlg.querySelector("[data-cancel]").onclick = () => close(false);
  dlg.querySelector("[data-ok]").onclick = () => close(true);
  ed.stage.append(dlg); dragHead(ed, dlg); preview();
  return { close };
}

const ORIGINAL_MAX = 60e6;
export const COMPARE = {
  captureOriginal() {
    const d = this.doc; this.original = null;
    if (d && d.width * d.height <= ORIGINAL_MAX) this.original = d.flatten();
  },
  compareToggle(force) {
    if (!this.doc) return;
    const on = force ?? !this.compare;
    if (on && !this.original) { toast(t("ph_compare_none")); return; }
    this.compare = on ? { split: this.compare?.split ?? 0.5 } : null;
    this.compareDrag = false;
    this.el.classList.toggle("ph-comparing", !!on);
    this.el.querySelector("[data-a=compare]")?.classList.toggle("on", !!on);
    this.hint(on ? t("ph_compare_hint") : "");
    this.requestRender();
  },
  compareAt(e) {
    const r = this.canvas.getBoundingClientRect(), X = this.view.panX, W = this.doc.width * this.view.zoom;
    this.compare.split = Math.max(0, Math.min(1, (e.clientX - r.left - X) / Math.max(1, W)));
    this.requestRender();
  },
  drawCompare(x, X, Y, W, H) {
    const o = this.original; if (!o || !this.compare) return;
    const sx = X + W * this.compare.split, sw = this.canvas.width / this.dpr, sh = this.canvas.height / this.dpr;
    x.save(); x.beginPath(); x.rect(X, Y, Math.max(0, sx - X), H); x.clip();
    x.fillStyle = "#ffffff"; x.fillRect(X, Y, W, H);
    let dx = X, dy = Y, dw = W, dh = H;
    if (o.width !== this.doc.width || o.height !== this.doc.height) { x.fillStyle = "#808080"; x.fillRect(X, Y, W, H); const k = Math.min(W / o.width, H / o.height); dw = o.width * k; dh = o.height * k; dx = X + (W - dw) / 2; dy = Y + (H - dh) / 2; }
    x.imageSmoothingEnabled = this.view.zoom < 2; x.imageSmoothingQuality = "high";
    x.drawImage(o, dx, dy, dw, dh);
    x.restore();
    x.save();
    x.strokeStyle = "#ffffff"; x.lineWidth = 2; x.shadowColor = "rgba(0,0,0,.55)"; x.shadowBlur = 4;
    x.beginPath(); x.moveTo(sx, Math.max(0, Y)); x.lineTo(sx, Math.min(sh, Y + H)); x.stroke();
    const cy = Math.max(24, Math.min(sh - 24, Y + H / 2));
    x.fillStyle = "#ffffff"; x.beginPath(); x.arc(sx, cy, 13, 0, Math.PI * 2); x.fill(); x.shadowBlur = 0;
    x.fillStyle = "#3b3b3b"; x.beginPath(); x.moveTo(sx - 3, cy - 5); x.lineTo(sx - 8, cy); x.lineTo(sx - 3, cy + 5); x.fill(); x.beginPath(); x.moveTo(sx + 3, cy - 5); x.lineTo(sx + 8, cy); x.lineTo(sx + 3, cy + 5); x.fill();
    x.font = "600 11px " + (getComputedStyle(this.el).getPropertyValue("--sans") || "sans-serif"); x.textBaseline = "middle";
    const ty = Math.max(14, Math.min(sh - 14, Y + 16)), pill = (text, px, right) => { const w = x.measureText(text).width + 14, lx = right ? px - w : px; x.fillStyle = "rgba(0,0,0,.55)"; x.beginPath(); x.roundRect ? x.roundRect(lx, ty - 10, w, 20, 10) : x.rect(lx, ty - 10, w, 20); x.fill(); x.fillStyle = "#fff"; x.fillText(text, lx + 7, ty); };
    if (sx - X > 60) pill(t("ph_before"), Math.max(X, 0) + 8, false);
    if (X + W - sx > 60) pill(t("ph_after"), Math.min(X + W, sw) - 8, true);
    x.restore();
  },
  drawFramePreview(x, X, Y, W, H) {
    const f = this.framePreview; if (!f || !this.doc) return;
    const z = this.view.zoom, s = f.size * z;
    x.save();
    if (f.inside) { x.fillStyle = f.color; x.beginPath(); x.rect(X, Y, W, H); roundRectPath(x, X + s, Y + s, W - 2 * s, H - 2 * s, f.radius * z); x.fill("evenodd"); }
    else { x.fillStyle = f.color; x.beginPath(); x.rect(X - s, Y - s, W + 2 * s, H + 2 * s); roundRectPath(x, X, Y, W, H, f.radius * z); x.fill("evenodd"); x.setLineDash([4, 4]); x.strokeStyle = "rgba(128,128,128,.8)"; x.strokeRect(X - s + 0.5, Y - s + 0.5, W + 2 * s - 1, H + 2 * s - 1); }
    x.restore();
  },
};
