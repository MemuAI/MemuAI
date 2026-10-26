

import { esc, t, I18N, toast, showMenu, kbd, api } from "../core.js";
import { PhotoDoc, newCanvas, ctx2d, cloneCanvas, isVector, renderVector, vectorBounds, textBox, fontString } from "./doc.js";
import { TOOLS, TOOL_GROUPS, DEFAULT_OPTS } from "./tools.js";
import { openDocument, saveDocument, formatOfPath, SAVE_FORMATS, extOf } from "./io.js";
import { parseHex, toHex, rgbToHsv, hsvToRgb, rgbToHsl, hslToRgb, combineMasks, BLEND_MODES, histogram } from "./pixels.js";
import { PICON, picon } from "./icons.js";
import { COMMANDS } from "./commands.js";
import { PhotoAI } from "./ai.js";
import { TRANSFORM } from "./transform.js";

const FONTS = ["Segoe UI", "Arial", "Calibri", "Cambria", "Georgia", "Times New Roman", "Verdana", "Tahoma", "Trebuchet MS", "Impact", "Comic Sans MS", "Consolas", "Courier New", "Bahnschrift", "Segoe Print", "Segoe Script", "Franklin Gothic Medium", "Palatino Linotype", "Book Antiqua", "Lucida Console", "JetBrains Mono"];
const SWATCHES = ["#000000", "#ffffff", "#7f7f7f", "#c3c3c3", "#e5484d", "#ff8a3d", "#ffd23f", "#3ddc84", "#16a34a", "#22c3ee", "#4c8dff", "#2346c4", "#8b5cf6", "#ec4899", "#7c4a2d", "#f5d0a9"];
const LS = "mn_photo_";

export class PhotoEditor {
  constructor(host, tab, hooks = {}) {
    this.tab = tab; this.hooks = hooks; this.t = t; this.host = host;
    this.opts = loadOpts(); this.toolId = localStorage.getItem(LS + "tool") || "brush"; if (!TOOLS[this.toolId]) this.toolId = "brush";
    this.lastInGroup = {}; this.fg = localStorage.getItem(LS + "fg") || "#000000"; this.bg = localStorage.getItem(LS + "bg") || "#ffffff";
    this.view = { zoom: 1, panX: 0, panY: 0 }; this.dpr = window.devicePixelRatio || 1;
    this.showRulers = localStorage.getItem(LS + "rulers") !== "0"; this.showGrid = false; this.gridSize = 50; this.showGuides = true;
    this.sideTab = localStorage.getItem(LS + "side") || "layers"; this.sideOpen = localStorage.getItem(LS + "side_open") !== "0";
    if (!document.querySelector("link[data-photo-css]")) { const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "photo.css?v=r32"; css.dataset.photoCss = "1"; document.head.append(css); }
    document.querySelector("#toolbar")?.classList.add("photo-hidden");
    this.el = document.createElement("div"); this.el.className = "ph"; this.el.tabIndex = -1;
    host.append(this.el);
    this.buildShell();
    this.ai = new PhotoAI(this);
    this.ready = this.init().catch((e) => this.fail(e));
  }
  get dirty() { return !!this.doc && (this.doc.dirty || !this.tab.path); }

  async init() {
    if (!this.tab.path && this.tab.photoCanvas) {
      const c = this.tab.photoCanvas; delete this.tab.photoCanvas;
      const doc = new PhotoDoc(c.width, c.height), l = doc.makeLayer({ name: t("ph_layer_background"), canvas: c });
      doc.layers.push(l); doc.activeId = l.id; this.setDoc(doc); this.hooks.onDirty?.(true); return;
    }
    if (!this.tab.path) { this.showNewForm(); return; }
    this.busy(true, t("ph_opening"));
    try {
      const { doc, info } = await openDocument(this.tab.path, t);
      this.info = info; this.setDoc(doc);
      if (info.frames > 1) this.offerFrames(info);
      if (info.warning === "psd_flattened") toast(t("ph_psd_flattened"), { ms: 7000 });
    } finally { this.busy(false); }
  }
  fail(e) {
    console.error(e);
    this.stage.innerHTML = `<div class="ph-empty"><div class="ph-empty-card">${picon("warn")}<h3>${esc(t("ph_open_failed"))}</h3><p class="muted">${esc(e?.data?.code === "heic" ? t("ph_heic_hint") : e?.message || String(e))}</p></div></div>`;
  }
  setDoc(doc) {
    this.doc?.listeners.clear();
    this.doc = doc;
    doc.on((kind) => { this.requestRender(); this.refreshPanels(); this.hooks.onDirty?.(doc.dirty); if (kind === "undo" || kind === "redo") this.syncToolState(); });
    this.el.classList.remove("ph-new-mode");
    this.stage.querySelector(".ph-empty")?.remove();
    this.fit(); this.renderTools(); this.renderOptions(); this.refreshPanels(); this.requestRender(); this.renderRulers();
    TOOLS[this.toolId]?.activate?.(this);
    this.hooks.onDirty?.(doc.dirty);
  }
  newDocument(w, h, bg) {
    const doc = new PhotoDoc(w, h), l = doc.makeLayer({ name: t(bg === "transparent" ? "ph_layer" : "ph_layer_background") });
    if (bg && bg !== "transparent") { const x = ctx2d(l.canvas); x.fillStyle = bg; x.fillRect(0, 0, w, h); }
    doc.layers.push(l); doc.activeId = l.id; doc.markSaved();
    this.setDoc(doc);
  }
  showNewForm() {
    this.el.classList.add("ph-new-mode");
    const presets = [["1920×1080", 1920, 1080], ["1280×720", 1280, 720], ["1080×1080", 1080, 1080], ["1080×1920", 1080, 1920], ["2480×3508 (A4 300 dpi)", 2480, 3508], ["1024×1024", 1024, 1024], ["800×600", 800, 600], ["512×512", 512, 512]];
    const card = document.createElement("div"); card.className = "ph-empty";
    card.innerHTML = `<form class="ph-empty-card ph-new"><div class="ph-new-head">${picon("image")}<h3>${esc(t("ph_new_title"))}</h3></div>
      <div class="ph-presets">${presets.map(([n, w, h], i) => `<button type="button" class="chip-btn ${i === 0 ? "on" : ""}" data-w="${w}" data-h="${h}">${esc(n)}</button>`).join("")}</div>
      <div class="ph-row"><label>${esc(t("ph_width"))}<input class="input sm" name="w" type="number" min="1" max="30000" value="1920"></label><label>${esc(t("ph_height"))}<input class="input sm" name="h" type="number" min="1" max="30000" value="1080"></label>
      <label>${esc(t("ph_background"))}<select class="input sm" name="bg"><option value="#ffffff">${esc(t("ph_bg_white"))}</option><option value="#000000">${esc(t("ph_bg_black"))}</option><option value="transparent">${esc(t("ph_bg_transparent"))}</option><option value="fg">${esc(t("ph_bg_fg"))}</option></select></label></div>
      <div class="ph-row end"><button type="button" class="btn ghost" data-a="paste">${picon("paste")}${esc(t("ph_new_from_clipboard"))}</button><button type="button" class="btn ghost" data-a="gen">${picon("sparkle")}${esc(t("ph_new_generate"))}</button><button class="btn primary" type="submit">${esc(t("ph_create"))}</button></div></form>`;
    this.stage.append(card);
    const f = card.querySelector("form");
    card.querySelectorAll("[data-w]").forEach((b) => (b.onclick = () => { card.querySelectorAll("[data-w]").forEach((x) => x.classList.toggle("on", x === b)); f.w.value = b.dataset.w; f.h.value = b.dataset.h; }));
    f.onsubmit = (e) => { e.preventDefault(); const w = clampInt(f.w.value, 1, 30000, 1920), h = clampInt(f.h.value, 1, 30000, 1080); this.newDocument(w, h, f.bg.value === "fg" ? this.fg : f.bg.value); };
    card.querySelector("[data-a=paste]").onclick = () => this.pasteAsNew();
    card.querySelector("[data-a=gen]").onclick = () => { this.newDocument(1024, 1024, "#ffffff"); this.ai.openGenerate(true); };
    setTimeout(() => f.querySelector("button[type=submit]")?.focus(), 30);
  }

  buildShell() {
    const menus = [["file", "ph_menu_file"], ["edit", "ph_menu_edit"], ["image", "ph_menu_image"], ["layer", "ph_menu_layer"], ["select", "ph_menu_select"], ["filter", "ph_menu_filter"], ["view", "ph_menu_view"], ["ai", "ph_menu_ai"]];
    this.el.innerHTML = `
      <div class="ph-bar">${menus.map(([id, k]) => `<button class="ph-menu ${id === "ai" ? "ai" : ""}" data-menu="${id}">${id === "ai" ? picon("sparkle") : ""}${esc(t(k))}</button>`).join("")}
        <span class="ph-sp"></span>
        <button class="icon-btn sm" data-a="undo" title="${esc(t("ph_undo"))} (${kbd("Ctrl+Z")})">${picon("undo")}</button><button class="icon-btn sm" data-a="redo" title="${esc(t("ph_redo"))} (${kbd("Ctrl+Shift+Z")})">${picon("redo")}</button>
        <span class="ph-zoom"><button class="icon-btn sm" data-a="zout" title="${esc(t("ph_zoom_out"))}">${picon("minus")}</button><button class="ph-zoom-v" data-a="zmenu" title="${esc(t("ph_zoom"))}">100%</button><button class="icon-btn sm" data-a="zin" title="${esc(t("ph_zoom_in"))}">${picon("plus")}</button></span>
        <button class="icon-btn sm ph-side-toggle" data-a="side" title="${esc(t("ph_panels"))} (Tab)">${picon("panel")}</button></div>
      <div class="ph-opts"></div>
      <div class="ph-main">
        <div class="ph-tools" role="toolbar"></div>
        <div class="ph-stage"><canvas class="ph-view"></canvas><canvas class="ph-ruler x"></canvas><canvas class="ph-ruler y"></canvas><span class="ph-ruler-corner"></span><div class="ph-busy" hidden><span class="spinner"></span><span class="ph-busy-t"></span></div></div>
        <aside class="ph-side"><div class="ph-color"></div><div class="ph-tabs" role="tablist"></div><div class="ph-tabbody"></div></aside>
      </div>
      <div class="ph-status"></div>`;
    this.stage = this.el.querySelector(".ph-stage"); this.canvas = this.el.querySelector(".ph-view"); this.ctx = this.canvas.getContext("2d");
    this.rx = this.el.querySelector(".ph-ruler.x"); this.ry = this.el.querySelector(".ph-ruler.y");
    this.el.querySelectorAll("[data-menu]").forEach((b) => (b.onclick = () => this.openMenu(b.dataset.menu, b)));

    const bar = this.el.querySelector(".ph-bar"); this.barMore = () => bar.classList.toggle("more", bar.scrollLeft + bar.clientWidth < bar.scrollWidth - 2);
    bar.addEventListener("wheel", (e) => { if (bar.scrollWidth <= bar.clientWidth + 1 || e.ctrlKey) return; e.preventDefault(); bar.scrollLeft += e.deltaY || e.deltaX; }, { passive: false });
    bar.addEventListener("scroll", this.barMore, { passive: true });
    this.el.querySelector("[data-a=undo]").onclick = () => this.undo(); this.el.querySelector("[data-a=redo]").onclick = () => this.redo();
    this.el.querySelector("[data-a=zin]").onclick = () => this.zoomStep(1); this.el.querySelector("[data-a=zout]").onclick = () => this.zoomStep(-1);
    this.el.querySelector("[data-a=zmenu]").onclick = (e) => showMenu([[t("ph_fit"), () => this.fit()], ["100%", () => this.setZoom(1)], ["200%", () => this.setZoom(2)], ["50%", () => this.setZoom(0.5)], ["25%", () => this.setZoom(0.25)], [t("ph_fill_window"), () => this.fit(true)]].map(([label, run]) => ({ label, run })), e.currentTarget);
    this.el.querySelector("[data-a=side]").onclick = () => this.toggleSide();
    this.el.classList.toggle("side-closed", !this.sideOpen);
    this.ro = new ResizeObserver(() => this.onResize()); this.ro.observe(this.stage); this.ro.observe(this.el);
    this.bindInput();
    this.renderColor(); this.renderTabs();
  }
  onResize() {
    const r = this.stage.getBoundingClientRect(); if (!r.width) return;
    const narrow = this.el.getBoundingClientRect().width < 860; this.el.classList.toggle("narrow", narrow); this.barMore?.();
    if (narrow && !this.narrowSeen) { this.narrowSeen = true; this.sideOpen = false; this.el.classList.add("side-closed"); }
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(r.width * this.dpr); this.canvas.height = Math.round(r.height * this.dpr);
    this.canvas.style.width = r.width + "px"; this.canvas.style.height = r.height + "px";
    if (!this.fitted && this.doc) this.fit();
    this.requestRender(); this.renderRulers();
  }
  toggleSide(force) { this.sideOpen = force ?? !this.sideOpen; localStorage.setItem(LS + "side_open", this.sideOpen ? "1" : "0"); this.el.classList.toggle("side-closed", !this.sideOpen); setTimeout(() => this.onResize(), 0); }
  destroy() { this.ro?.disconnect(); this.ai?.destroy(); cancelAnimationFrame(this.raf); clearInterval(this.antsTimer); this.worker_?.terminate(); window.removeEventListener("paste", this.onPaste); window.removeEventListener("keyup", this.onKeyUp); this.el.remove(); }
  fail2(msg) { toast(msg, { kind: "error" }); }
  busy(on, text = "") { const b = this.el.querySelector(".ph-busy"); if (!b) return; this.busyN = Math.max(0, (this.busyN || 0) + (on ? 1 : -1)); b.hidden = !this.busyN; if (on) b.querySelector(".ph-busy-t").textContent = text || t("ph_working"); }
  toast(m, o) { toast(m, o); }
  hint(text) { this._hint = text; this.renderStatus(); }

  toDoc(e) { const r = this.canvas.getBoundingClientRect(); return { x: (e.clientX - r.left - this.view.panX) / this.view.zoom, y: (e.clientY - r.top - this.view.panY) / this.view.zoom }; }

  clampPan() { const d = this.doc; if (!d) return; const r = this.canvas.getBoundingClientRect(), m = 80, W = d.width * this.view.zoom, H = d.height * this.view.zoom; this.view.panX = Math.min(r.width - Math.min(m, W), Math.max(Math.min(m, W) - W, this.view.panX)); this.view.panY = Math.min(r.height - Math.min(m, H), Math.max(Math.min(m, H) - H, this.view.panY)); }
  toScreen(p) { return { x: p.x * this.view.zoom + this.view.panX, y: p.y * this.view.zoom + this.view.panY }; }
  fit(fill = false) {
    if (!this.doc) return; const r = this.stage.getBoundingClientRect(); if (!r.width) return;
    const pad = this.showRulers ? 40 : 24, kx = (r.width - pad) / this.doc.width, ky = (r.height - pad) / this.doc.height;
    const z = fill ? Math.max(kx, ky) : Math.min(kx, ky, 1);
    this.setZoom(Math.max(0.01, z), true); this.fitted = true;
  }
  setZoom(z, center = true) {
    z = Math.max(0.01, Math.min(64, z)); const r = this.stage.getBoundingClientRect();
    if (center && this.doc) { this.view.zoom = z; this.view.panX = Math.round((r.width - this.doc.width * z) / 2 + (this.showRulers ? 9 : 0)); this.view.panY = Math.round((r.height - this.doc.height * z) / 2 + (this.showRulers ? 9 : 0)); }
    else this.view.zoom = z;
    this.requestRender(); this.renderRulers(); this.renderZoom();
  }
  zoomAt(k, cx, cy) {
    const r = this.canvas.getBoundingClientRect(), x = cx - r.left, y = cy - r.top, z0 = this.view.zoom, z = Math.max(0.01, Math.min(64, z0 * k));
    this.view.panX = x - ((x - this.view.panX) * z) / z0; this.view.panY = y - ((y - this.view.panY) * z) / z0; this.view.zoom = z;
    this.requestRender(); this.renderRulers(); this.renderZoom();
  }
  zoomStep(d) { const steps = [0.02, 0.05, 0.0833, 0.125, 0.1667, 0.25, 0.333, 0.5, 0.667, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 64], z = this.view.zoom; const next = d > 0 ? steps.find((s) => s > z * 1.01) || 64 : [...steps].reverse().find((s) => s < z / 1.01) || 0.02; const r = this.canvas.getBoundingClientRect(); this.zoomAt(next / z, r.left + r.width / 2, r.top + r.height / 2); }
  renderZoom() { const b = this.el.querySelector(".ph-zoom-v"); if (b) b.textContent = (this.view.zoom * 100 >= 10 ? Math.round(this.view.zoom * 100) : (this.view.zoom * 100).toFixed(1)) + "%"; this.renderStatus(); }

  requestRender() { if (this.raf) return; this.raf = requestAnimationFrame(() => { this.raf = 0; this.render(); }); }
  layerChanged(rect) { if (this.doc) { if (rect) this.doc.markDirtyRect(rect); else this.doc.compDirty = true; } this.requestRender(); this.thumbsSoon(); }
  render() {
    const c = this.canvas, x = this.ctx, d = this.doc, dpr = this.dpr;
    x.setTransform(1, 0, 0, 1, 0, 0); x.clearRect(0, 0, c.width, c.height);
    if (!d) return;
    if (this.pendingFrames && this.visible()) this.offerFrames(this.pendingFrames);
    const z = this.view.zoom, X = this.view.panX, Y = this.view.panY, W = d.width * z, H = d.height * z;
    const comp = d.composite();

    const fast = this.down && z < 1, key = `${d.compStamp}|${z}|${X}|${Y}|${c.width}|${c.height}|${dpr}|${fast}`;
    if (this.viewKey !== key || this.viewDoc !== d) {
      const vc = this.viewCache && this.viewCache.width === c.width && this.viewCache.height === c.height ? this.viewCache : (this.viewCache = newCanvas(c.width, c.height));
      const v = ctx2d(vc); v.setTransform(1, 0, 0, 1, 0, 0); v.clearRect(0, 0, vc.width, vc.height); v.setTransform(dpr, 0, 0, dpr, 0, 0);
      v.save(); v.shadowColor = "rgba(0,0,0,.35)"; v.shadowBlur = 14; v.fillStyle = "#808080"; v.fillRect(X, Y, W, H); v.restore();
      v.fillStyle = this.checkerV || (this.checkerV = v.createPattern(checkerTile(), "repeat")); v.save(); v.translate(X, Y); v.fillRect(0, 0, W, H); v.restore();
      v.imageSmoothingEnabled = z < 2; v.imageSmoothingQuality = fast ? "low" : "high";
      v.drawImage(comp, X, Y, W, H);
      this.viewKey = key; this.viewDoc = d;
    }
    x.drawImage(this.viewCache, 0, 0);
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (z >= 12) this.drawPixelGrid(x, X, Y, W, H, z);
    if (this.showGrid) this.drawGrid(x, X, Y, W, H, z);
    if (this.showGuides && d.guides.length) { x.save(); x.strokeStyle = "#22d3ee"; x.lineWidth = 1; x.beginPath(); for (const g of d.guides) { if (g.axis === "x") { const sx = Math.round(X + g.pos * z) + 0.5; x.moveTo(sx, 0); x.lineTo(sx, c.height); } else { const sy = Math.round(Y + g.pos * z) + 0.5; x.moveTo(0, sy); x.lineTo(c.width, sy); } } x.stroke(); x.restore(); }
    this.drawAnts(x);
    if (this.xf) this.xfOverlay(x);
    TOOLS[this.toolId]?.overlay?.(this, x);
    this.drawBrushCursor(x);
  }
  drawPixelGrid(x, X, Y, W, H, z) { x.save(); x.strokeStyle = "rgba(128,128,128,.35)"; x.lineWidth = 1; x.beginPath(); const sw = this.canvas.width / this.dpr, sh = this.canvas.height / this.dpr; for (let i = Math.max(0, Math.floor(-X / z)); i <= this.doc.width && X + i * z <= sw; i++) { const sx = Math.round(X + i * z) + 0.5; x.moveTo(sx, Math.max(0, Y)); x.lineTo(sx, Math.min(sh, Y + H)); } for (let j = Math.max(0, Math.floor(-Y / z)); j <= this.doc.height && Y + j * z <= sh; j++) { const sy = Math.round(Y + j * z) + 0.5; x.moveTo(Math.max(0, X), sy); x.lineTo(Math.min(sw, X + W), sy); } x.stroke(); x.restore(); }
  drawGrid(x, X, Y, W, H, z) {
    const step = this.gridSize; if (step * z < 4) return;
    x.save(); x.strokeStyle = "rgba(76,141,255,.35)"; x.lineWidth = 1; x.beginPath();
    for (let gx = step; gx < this.doc.width; gx += step) { const sx = Math.round(X + gx * z) + 0.5; x.moveTo(sx, Y); x.lineTo(sx, Y + H); }
    for (let gy = step; gy < this.doc.height; gy += step) { const sy = Math.round(Y + gy * z) + 0.5; x.moveTo(X, sy); x.lineTo(X + W, sy); }
    x.stroke(); x.restore();
  }

  drawAnts(x) {
    const d = this.doc; if (!d.selection) { clearInterval(this.antsTimer); this.antsTimer = 0; return; }
    const key = `${d.selVersion}|${this.view.zoom}|${this.view.panX}|${this.view.panY}|${this.canvas.width}|${this.canvas.height}`;
    if (this.antsKey !== key) {
      this.antsKey = key; const sw = Math.ceil(this.canvas.width / this.dpr), sh = Math.ceil(this.canvas.height / this.dpr);
      const m = newCanvas(sw, sh), mx = ctx2d(m, { willReadFrequently: true }); mx.imageSmoothingEnabled = false;
      mx.drawImage(d.selCanvas(), this.view.panX, this.view.panY, d.width * this.view.zoom, d.height * this.view.zoom);
      const a = mx.getImageData(0, 0, sw, sh).data, edges = [];
      const on = (px, py) => px >= 0 && py >= 0 && px < sw && py < sh && a[(py * sw + px) * 4 + 3] >= 128;
      for (let py = 0; py < sh; py++) for (let px = 0; px < sw; px++) if (on(px, py) && (!on(px - 1, py) || !on(px + 1, py) || !on(px, py - 1) || !on(px, py + 1))) edges.push(py * sw + px);
      this.ants = { edges, sw, sh };
    }
    if (!this.antsTimer) this.antsTimer = setInterval(() => { this.antsPhase = ((this.antsPhase || 0) + 1) % 8; this.requestRender(); }, 140);
    const { edges, sw, sh } = this.ants, img = new ImageData(sw, sh), o = img.data, ph = this.antsPhase || 0;
    for (const p of edges) { const px = p % sw, py = (p / sw) | 0, v = ((px + py + ph) >> 2) & 1 ? 255 : 0, i = p * 4; o[i] = o[i + 1] = o[i + 2] = v; o[i + 3] = 255; }
    const oc = this.antsCanvas && this.antsCanvas.width === sw && this.antsCanvas.height === sh ? this.antsCanvas : (this.antsCanvas = newCanvas(sw, sh));
    ctx2d(oc).putImageData(img, 0, 0); x.drawImage(oc, 0, 0);
  }
  drawBrushCursor(x) {
    const tl = TOOLS[this.toolId]; if (!this.hover || tl?.cursor !== "brush") return;
    const o = this.opts[this.toolId]; if (!o?.size) return;
    const s = this.toScreen(this.hover), r = (o.size * this.view.zoom) / 2;
    x.save(); x.beginPath(); x.arc(s.x, s.y, Math.max(1, r), 0, Math.PI * 2); x.strokeStyle = "rgba(0,0,0,.7)"; x.lineWidth = 1.5; x.stroke(); x.strokeStyle = "rgba(255,255,255,.9)"; x.lineWidth = 0.75; x.stroke(); x.restore();
  }
  renderRulers() {
    for (const [c, axis] of [[this.rx, "x"], [this.ry, "y"]]) {
      c.hidden = !this.showRulers; if (!this.showRulers || !this.doc) continue;
      const r = c.getBoundingClientRect(); if (!r.width || !r.height) continue;
      c.width = Math.round(r.width * this.dpr); c.height = Math.round(r.height * this.dpr);
      const x = c.getContext("2d"), z = this.view.zoom, pan = axis === "x" ? this.view.panX - 18 : this.view.panY - 18, len = axis === "x" ? r.width : r.height;
      x.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); x.clearRect(0, 0, r.width, r.height);
      const css = getComputedStyle(this.el); x.fillStyle = css.getPropertyValue("--ph-muted") || "#888"; x.strokeStyle = x.fillStyle; x.font = "9px " + (css.getPropertyValue("--sans") || "sans-serif"); x.lineWidth = 1;
      const steps = [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000]; const step = steps.find((s) => s * z >= 50) || 10000; const minor = step / (step % 5 === 0 ? 5 : 2);
      const v0 = Math.floor(-pan / z / minor) * minor;
      x.beginPath();
      for (let v = v0; (v * z + pan) < len; v += minor) {
        const s = Math.round(v * z + pan) + 0.5, major = Math.abs(v % step) < 1e-6;
        if (axis === "x") { x.moveTo(s, 18); x.lineTo(s, major ? 4 : 13); if (major) x.fillText(String(Math.round(v)), s + 2, 9); }
        else { x.moveTo(18, s); x.lineTo(major ? 4 : 13, s); if (major) { x.save(); x.translate(9, s + 2); x.rotate(-Math.PI / 2); x.textAlign = "right"; x.fillText(String(Math.round(v)), 0, 0); x.restore(); } }
      }
      x.stroke();
      if (this.hover) { x.strokeStyle = "#e5484d"; x.beginPath(); const s = (axis === "x" ? this.hover.x : this.hover.y) * z + pan; if (axis === "x") { x.moveTo(s, 0); x.lineTo(s, 18); } else { x.moveTo(0, s); x.lineTo(18, s); } x.stroke(); }
    }
    this.el.classList.toggle("no-rulers", !this.showRulers);
  }

  bindInput() {
    const cv = this.canvas;
    cv.addEventListener("pointerdown", (e) => this.onDown(e));
    cv.addEventListener("pointermove", (e) => this.onMove(e));
    cv.addEventListener("pointerup", (e) => this.onUp(e));
    cv.addEventListener("pointercancel", (e) => this.onUp(e));
    cv.addEventListener("pointerleave", () => { this.hover = null; this.requestRender(); this.renderStatus(); });
    cv.addEventListener("dblclick", (e) => { if (this.toolId === "hand") this.fit(); else if (this.toolId === "zoom") this.setZoom(1); else if (this.toolId === "move" && this.doc) { const l = this.layerAt(this.toDoc(e), (x) => x.type === "text"); if (l) this.editText(l); } });
    cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey || e.altKey) this.zoomAt(Math.exp(-e.deltaY * (e.deltaMode ? 0.05 : 0.0022)), e.clientX, e.clientY);
      else { this.view.panX -= e.shiftKey ? e.deltaY : e.deltaX; this.view.panY -= e.shiftKey ? 0 : e.deltaY; this.clampPan(); this.requestRender(); this.renderRulers(); }
    }, { passive: false });
    cv.addEventListener("contextmenu", (e) => { e.preventDefault(); if (this.doc) this.contextMenu(e); });

    for (const [c, axis] of [[this.rx, "y"], [this.ry, "x"]]) c.addEventListener("pointerdown", (e) => { if (!this.doc) return; e.preventDefault(); this.guideDrag = { axis, guide: null }; c.setPointerCapture(e.pointerId); });
    for (const c of [this.rx, this.ry]) {
      c.addEventListener("pointermove", (e) => { const g = this.guideDrag; if (!g) return; const p = this.toDoc(e); if (!g.guide) { this.doc.begin(t("ph_guide")); g.guide = { axis: g.axis, pos: 0 }; this.doc.guides.push(g.guide); } g.guide.pos = Math.round(g.axis === "x" ? p.x : p.y); this.requestRender(); });
      c.addEventListener("pointerup", (e) => { const g = this.guideDrag; this.guideDrag = null; if (!g?.guide) return; const r = this.stage.getBoundingClientRect(); if (e.clientX - r.left < 18 || e.clientY - r.top < 18) { this.doc.guides.splice(this.doc.guides.indexOf(g.guide), 1); this.doc.cancel(); } else this.doc.commit(); });
    }
    this.stage.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); e.stopPropagation(); this.stage.classList.add("drop"); } });
    this.stage.addEventListener("dragleave", () => this.stage.classList.remove("drop"));
    this.stage.addEventListener("drop", (e) => { this.stage.classList.remove("drop"); const files = [...(e.dataTransfer?.files || [])].filter((f) => /^image\//.test(f.type) || /\.(png|jpe?g|webp|gif|bmp|svg|avif|ico)$/i.test(f.name)); if (!files.length) return; e.preventDefault(); e.stopPropagation(); for (const f of files) this.placeFile(f); });
    this.onPaste = (e) => { if (!this.visible() || /^(INPUT|TEXTAREA)$/.test(document.activeElement?.tagName || "") || document.activeElement?.isContentEditable) return; const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/")); if (item) { e.preventDefault(); const f = item.getAsFile(); if (!this.doc) this.pasteBlobAsNew(f); else this.placeFile(f, t("ph_pasted")); } else if (this.clip && this.doc) { e.preventDefault(); this.pasteInternal(); } };
    window.addEventListener("paste", this.onPaste);
    this.onKeyUp = (e) => this.handleKey(e); window.addEventListener("keyup", this.onKeyUp);
  }
  visible() { return !!this.el.offsetParent; }
  onDown(e) {
    if (!this.doc || e.button === 1 || (e.button === 0 && this.spaceDown)) { if (this.doc) { this.panDrag = { x: e.clientX, y: e.clientY, px: this.view.panX, py: this.view.panY }; this.canvas.setPointerCapture(e.pointerId); this.setCursor("grabbing"); } return; }
    if (e.button !== 0) return;
    this.el.focus({ preventScroll: true });
    this.canvas.setPointerCapture(e.pointerId);
    const p = this.toDoc(e); this.down = true; this._hint = "";
    if (this.textEdit) { this.finishText(); }
    if (this.xf) { this.xfDown(p, e); return; }
    if (this.toolId === "move" || this.toolId === "marquee") { const g = this.guideAt(e); if (g && this.toolId === "move") { this.doc.begin(t("ph_guide")); this.guideMove = g; return; } }
    try { TOOLS[this.toolId]?.down?.(this, p, e); } catch (err) { this.error(err); }
    this.requestRender();
  }
  onMove(e) {
    if (this.panDrag) { this.view.panX = this.panDrag.px + e.clientX - this.panDrag.x; this.view.panY = this.panDrag.py + e.clientY - this.panDrag.y; this.clampPan(); this.requestRender(); this.renderRulers(); return; }
    if (!this.doc) return;
    const p = this.toDoc(e); this.hover = p;
    if (this.guideMove) { this.guideMove.pos = Math.round(this.guideMove.axis === "x" ? p.x : p.y); this.requestRender(); return; }
    if (this.xf) { this.xfMove(p, e); this.renderStatus(); return; }
    const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [e];
    if (this.down) for (const ev of evs.length ? evs : [e]) { try { TOOLS[this.toolId]?.move?.(this, this.toDoc(ev), ev); } catch (err) { this.error(err); } }
    else { TOOLS[this.toolId]?.move?.(this, p, e); if (this.toolId === "move") this.setCursor(this.guideAt(e) ? (this.guideAt(e).axis === "x" ? "ew-resize" : "ns-resize") : "move"); }
    if (TOOLS[this.toolId]?.cursor === "brush") this.requestRender();
    if (this.showRulers) this.renderRulersSoon();
    this.renderStatus();
  }
  onUp(e) {
    if (this.panDrag) { this.panDrag = null; this.setCursor(); return; }
    if (this.guideMove) { const g = this.guideMove; this.guideMove = null; const r = this.stage.getBoundingClientRect(); if (e.clientX - r.left < 18 || e.clientY - r.top < 18 || g.pos < 0 || g.pos > (g.axis === "x" ? this.doc.width : this.doc.height)) this.doc.guides.splice(this.doc.guides.indexOf(g), 1); this.doc.commit(); return; }
    if (!this.down) return; this.down = false;
    if (this.xf) { this.xfUp(); return; }
    try { const r = TOOLS[this.toolId]?.up?.(this, this.toDoc(e), e); if (r?.catch) r.catch((err) => this.error(err)); } catch (err) { this.error(err); }
    this.requestRender(); this.thumbsSoon();
  }
  guideAt(e) { if (!this.doc?.guides.length || !this.showGuides) return null; const p = this.toDoc(e), tol = 4 / this.view.zoom; return this.doc.guides.find((g) => Math.abs((g.axis === "x" ? p.x : p.y) - g.pos) < tol) || null; }
  renderRulersSoon() { if (this.rrT) return; this.rrT = requestAnimationFrame(() => { this.rrT = 0; this.renderRulers(); }); }
  setCursor(c) { const tl = TOOLS[this.toolId]; const want = c || (this.spaceDown ? "grab" : tl?.cursor === "brush" ? "none" : tl?.cursor || "default"); this.canvas.style.cursor = want; }
  error(err) { console.error(err); toast(t("ph_error") + ": " + (err?.message || err), { kind: "error" }); this.doc?.tx && this.doc.cancel(); }

  handleKey(e) {
    if (!this.visible()) return false;
    const tag = e.target?.tagName, inField = /^(INPUT|TEXTAREA|SELECT)$/.test(tag || "") || e.target?.isContentEditable;
    const mod = e.ctrlKey || e.metaKey, lk = globalThis.MemuKeys ? MemuKeys.latin(e) : e.key, k = lk.length === 1 ? lk.toLowerCase() : lk;
    if (e.type === "keyup") { if (e.key === " ") { this.spaceDown = false; this.setCursor(); } return false; }
    if (inField) {
      if (this.textEdit && e.key === "Escape") { e.preventDefault(); this.finishText(); return true; }
      if (this.textEdit && e.key === "Enter" && mod) { e.preventDefault(); this.finishText(); return true; }
      return false;
    }
    if (!this.doc) return false;
    if (document.querySelector(".ph-dlg, .ph-picker, .menu") && (e.key === "Escape" || e.key === "Enter")) return false;
    const run = (fn) => { e.preventDefault(); e.stopPropagation(); try { const r = fn(); if (r?.catch) r.catch((err) => this.error(err)); } catch (err) { this.error(err); } return true; };
    if (this.xf) { if (e.key === "Enter") return run(() => this.xfApply()); if (e.key === "Escape") return run(() => this.xfCancel()); }
    if (!mod && TOOLS[this.toolId]?.keyHandler?.(this, e)) { e.preventDefault(); e.stopPropagation(); return true; }
    if (e.key === " " && !mod) { if (!this.spaceDown) { this.spaceDown = true; this.setCursor("grab"); } e.preventDefault(); return true; }
    const sc = COMMANDS.shortcut(this, e);
    if (sc) return run(sc);
    if (!mod && !e.altKey) {
      if (e.key === "Tab") return run(() => this.toggleSide());
      if (e.key === "Escape") { if (this.doc.selection && !["polylasso", "crop"].includes(this.toolId)) return run(() => this.select(null, "new", t("ph_deselect"))); return false; }
      if (e.key === "Delete" || e.key === "Backspace") return run(() => this.clearSelection());
      if (k === "x") return run(() => this.swapColors());
      if (k === "d") return run(() => { this.setColor("fg", "#000000"); this.setColor("bg", "#ffffff"); });
      if (k === "[" || k === "]") return run(() => this.brushSize(k === "]" ? 1 : -1));
      if (k === "{" || k === "}") return run(() => this.brushHardness(k === "}" ? 10 : -10));
      if (/^[0-9]$/.test(k) && this.opts[this.toolId]?.opacity !== undefined) return run(() => { this.opts[this.toolId].opacity = k === "0" ? 100 : +k * 10; saveOpts(this.opts); this.renderOptions(); this.hint(`${t("ph_opacity")}: ${this.opts[this.toolId].opacity}%`); });
      const grp = TOOL_GROUPS.find(([, ids]) => ids.some((id) => TOOLS[id].key === k));
      if (grp) return run(() => { const ids = grp[1].filter((id) => TOOLS[id].key === k); const cur = ids.indexOf(this.toolId); this.setTool(e.shiftKey && cur >= 0 ? ids[(cur + 1) % ids.length] : cur >= 0 ? this.toolId : this.lastInGroup[grp[0]] && ids.includes(this.lastInGroup[grp[0]]) ? this.lastInGroup[grp[0]] : ids[0]); });
    }
    return false;
  }

  setTool(id) {
    if (!TOOLS[id] || id === this.toolId && !this.xf) { this.renderTools(); return; }
    if (this.textEdit) this.finishText();
    TOOLS[this.toolId]?.deactivate?.(this);
    this.toolId = id; localStorage.setItem(LS + "tool", id);
    const g = TOOL_GROUPS.find(([, ids]) => ids.includes(id)); if (g) this.lastInGroup[g[0]] = id;
    TOOLS[id].activate?.(this);
    this.renderTools(); this.renderOptions(); this.setCursor(); this.requestRender(); this._hint = ""; this.renderStatus();
  }
  syncToolState() { if (this.toolId === "crop") TOOLS.crop.activate(this); }
  renderTools() {
    const host = this.el.querySelector(".ph-tools"); if (!host) return;
    host.innerHTML = TOOL_GROUPS.map(([g, ids]) => {
      const id = ids.includes(this.toolId) ? this.toolId : this.lastInGroup[g] || ids[0], tl = TOOLS[id];
      return `<button class="ph-tool ${ids.includes(this.toolId) ? "on" : ""} ${ids.length > 1 ? "multi" : ""}" data-tool="${id}" data-group="${g}" title="${esc(t("ph_tool_" + id))} (${tl.key.toUpperCase()})" aria-label="${esc(t("ph_tool_" + id))}">${picon(tl.icon)}</button>`;
    }).join("") + `<div class="ph-tool-colors" title="${esc(t("ph_colors_hint"))}"><button class="ph-sw fg" data-c="fg" style="background:${this.fg}"></button><button class="ph-sw bg" data-c="bg" style="background:${this.bg}"></button><button class="ph-swap" data-a="swap" title="${esc(t("ph_swap_colors"))} (X)">${picon("swap")}</button></div>`;
    host.querySelectorAll("[data-tool]").forEach((b) => {
      b.onclick = () => this.setTool(b.dataset.tool);
      const group = TOOL_GROUPS.find(([g]) => g === b.dataset.group)[1];
      if (group.length > 1) {
        const open = (ev) => { ev.preventDefault(); showMenu(group.map((id) => ({ label: t("ph_tool_" + id), icon: undefined, kbd: TOOLS[id].key.toUpperCase(), checked: id === this.toolId ? true : undefined, run: () => this.setTool(id) })), { x: b.getBoundingClientRect().right + 2, y: b.getBoundingClientRect().top }); };
        b.oncontextmenu = open; let timer; b.onpointerdown = (ev) => { timer = setTimeout(() => open(ev), 450); }; b.onpointerup = b.onpointerleave = () => clearTimeout(timer);
      }
    });
    host.querySelectorAll("[data-c]").forEach((b) => (b.onclick = () => this.pickColor(b.dataset.c, b)));
    host.querySelector("[data-a=swap]").onclick = () => this.swapColors();
  }
  renderOptions() {
    const host = this.el.querySelector(".ph-opts"); if (!host) return;
    const id = this.toolId, o = this.opts[id] || {}, opts = this.opts;
    const num = (key, label, min, max, step = 1, unit = "", obj = o) => `<label class="ph-opt" title="${esc(label)}"><span>${esc(label)}</span><input type="range" min="${min}" max="${max}" step="${step}" value="${obj[key]}" data-k="${key}" data-o="${obj === o ? id : "selection"}"><input class="ph-num" type="number" min="${min}" max="${max}" step="${step}" value="${obj[key]}" data-k="${key}" data-o="${obj === o ? id : "selection"}">${unit ? `<em>${unit}</em>` : ""}</label>`;
    const chk = (key, label, obj = o, oid = id) => `<label class="ph-opt chk"><input type="checkbox" data-k="${key}" data-o="${oid}" ${obj[key] ? "checked" : ""}><span>${esc(label)}</span></label>`;
    const sel = (key, label, options, obj = o, oid = id) => `<label class="ph-opt"><span>${esc(label)}</span><select class="input xs" data-k="${key}" data-o="${oid}">${options.map(([v, l]) => `<option value="${esc(v)}" ${String(obj[key]) === String(v) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
    const selModes = () => `<span class="ph-seg">${[["new", "selNew"], ["add", "selAdd"], ["subtract", "selSub"], ["intersect", "selInt"]].map(([m, ic]) => `<button class="${opts.selection.mode === m ? "on" : ""}" data-selmode="${m}" title="${esc(t("ph_sel_" + m))}">${picon(ic)}</button>`).join("")}</span>`;
    let html = `<span class="ph-opt-tool">${picon(TOOLS[id].icon)}<b>${esc(t("ph_tool_" + id))}</b></span>`;
    if (["brush", "pencil", "eraser"].includes(id)) html += num("size", t("ph_size"), 1, 1000, 1, "px") + (id !== "pencil" ? num("hardness", t("ph_hardness"), 0, 100, 1, "%") : "") + num("opacity", t("ph_opacity"), 1, 100, 1, "%") + (id !== "pencil" ? num("flow", t("ph_flow"), 1, 100, 1, "%") : "") + (id === "brush" ? chk("pressure", t("ph_pressure")) : "");
    else if (id === "clone") html += num("size", t("ph_size"), 1, 1000, 1, "px") + num("hardness", t("ph_hardness"), 0, 100, 1, "%") + num("opacity", t("ph_opacity"), 1, 100, 1, "%") + chk("aligned", t("ph_aligned")) + chk("sampleAll", t("ph_sample_all")) + `<span class="ph-tip">${esc(t("ph_clone_tip"))}</span>`;
    else if (id === "heal") html += num("size", t("ph_size"), 1, 500, 1, "px") + `<span class="ph-tip">${esc(t("ph_heal_tip"))}</span>`;
    else if (["blur", "sharpen", "smudge", "dodge", "burn", "sponge"].includes(id)) html += num("size", t("ph_size"), 1, 1000, 1, "px") + num("strength", t("ph_strength"), 1, 100, 1, "%") + num("hardness", t("ph_hardness"), 0, 100, 1, "%") + (id === "dodge" || id === "burn" ? sel("range", t("ph_range"), [["shadows", t("ph_shadows")], ["midtones", t("ph_midtones")], ["highlights", t("ph_highlights")]]) : "") + (id === "sponge" ? sel("sponge", t("ph_mode"), [["saturate", t("ph_saturate")], ["desaturate", t("ph_desaturate")]]) : "");
    else if (id === "marquee" || id === "ellipse" || id === "lasso" || id === "polylasso") html += selModes() + num("feather", t("ph_feather"), 0, 250, 1, "px", opts.selection) + `<span class="ph-tip">${esc(t(id === "polylasso" ? "ph_polylasso_tip" : "ph_sel_tip"))}</span>`;
    else if (id === "wand") html += selModes() + num("tolerance", t("ph_tolerance"), 0, 255) + chk("contiguous", t("ph_contiguous")) + chk("antialias", t("ph_antialias")) + chk("sampleAll", t("ph_sample_all")) + `<button class="btn sm" data-cmd="selectSubject">${picon("person")}${esc(t("ph_select_subject"))}</button>`;
    else if (id === "quicksel") html += num("size", t("ph_size"), 4, 400, 1, "px") + `<span class="ph-tip">${esc(t("ph_quicksel_tip"))}</span><button class="btn sm" data-cmd="selectSubject">${picon("person")}${esc(t("ph_select_subject"))}</button>`;
    else if (id === "objsel") html += selModes() + `<span class="ph-tip">${esc(t("ph_objsel_tip"))}</span><button class="btn sm" data-cmd="aiSelect">${picon("sparkle")}${esc(t("ph_ai_select"))}</button>`;
    else if (id === "crop") html += sel("ratio", t("ph_ratio"), [["free", t("ph_ratio_free")], ["original", t("ph_ratio_original")], ["1:1", "1:1"], ["4:3", "4:3"], ["3:2", "3:2"], ["16:9", "16:9"], ["9:16", "9:16"], ["4:5", "4:5"]]) + num("angle", t("ph_straighten"), -45, 45, 0.1, "°") + `<button class="btn sm primary" data-cmd="cropApply">${picon("check")}${esc(t("ph_apply"))}</button><button class="btn sm ghost" data-cmd="cropReset">${esc(t("ph_reset"))}</button>`;
    else if (id === "bucket") html += num("tolerance", t("ph_tolerance"), 0, 255) + num("opacity", t("ph_opacity"), 1, 100, 1, "%") + chk("contiguous", t("ph_contiguous")) + chk("sampleAll", t("ph_sample_all"));
    else if (id === "gradient") html += sel("type", t("ph_type"), [["linear", t("ph_linear")], ["radial", t("ph_radial")]]) + num("opacity", t("ph_opacity"), 1, 100, 1, "%") + chk("toTransparent", t("ph_to_transparent")) + chk("reverse", t("ph_reverse"));
    else if (id === "eyedropper") html += sel("size", t("ph_sample_size"), [[1, t("ph_point")], [3, "3×3"], [5, "5×5"], [11, "11×11"]]) + chk("sampleAll", t("ph_sample_all"));
    else if (id === "text") html += sel("font", t("ph_font"), FONTS.map((f) => [f, f])) + num("size", t("ph_size"), 4, 1000, 1, "px") + `<span class="ph-seg">${["bold", "italic", "underline"].map((k) => `<button class="${o[k] ? "on" : ""}" data-toggle="${k}" title="${esc(t("ph_" + k))}">${picon(k)}</button>`).join("")}</span><span class="ph-seg">${["left", "center", "right"].map((a) => `<button class="${o.align === a ? "on" : ""}" data-align="${a}" title="${esc(t("ph_align_" + a))}">${picon("align_" + a)}</button>`).join("")}</span>` + num("stroke", t("ph_outline"), 0, 40, 1, "px") + chk("shadow", t("ph_shadow")) + `<span class="ph-tip">${esc(t("ph_text_tip"))}</span>`;
    else if (id === "shape") html += `<span class="ph-seg">${["rect", "ellipse", "line", "arrow", "polygon", "star", "path"].map((k) => `<button class="${o.kind === k ? "on" : ""}" data-kind="${k}" title="${esc(t("ph_shape_" + k))}">${picon("shape_" + k)}</button>`).join("")}</span>` + num("strokeWidth", t("ph_stroke"), 0, 100, 1, "px") + (o.kind === "rect" ? num("radius", t("ph_radius"), 0, 500, 1, "px") : "") + (o.kind === "polygon" || o.kind === "star" ? num("sides", t("ph_sides"), 3, 24) : "") + chk("noFill", t("ph_no_fill")) + `<span class="ph-tip">${esc(t("ph_shape_tip"))}</span>`;
    else if (id === "move") html += chk("autoSelect", t("ph_auto_select")) + `<span class="ph-sep"></span>` + ["alignLeft", "alignHCenter", "alignRight", "alignTop", "alignVCenter", "alignBottom"].map((c) => `<button class="icon-btn sm" data-cmd="${c}" title="${esc(t("ph_" + c))}">${picon(c)}</button>`).join("") + `<button class="btn sm" data-cmd="freeTransform">${picon("transform")}${esc(t("ph_free_transform"))}</button>`;
    else if (id === "hand" || id === "zoom") html += `<button class="btn sm" data-cmd="fit">${esc(t("ph_fit"))}</button><button class="btn sm" data-cmd="zoom100">100%</button><button class="btn sm" data-cmd="fill">${esc(t("ph_fill_window"))}</button>`;
    if (this.xf) html = this.xfOptions();
    host.innerHTML = html;
    host.querySelectorAll("input[type=range], input.ph-num").forEach((inp) => inp.addEventListener("input", () => {
      const obj = this.opts[inp.dataset.o]; const v = +inp.value; obj[inp.dataset.k] = v;
      host.querySelectorAll(`[data-k="${inp.dataset.k}"][data-o="${inp.dataset.o}"]`).forEach((x) => { if (x !== inp) x.value = v; });
      saveOpts(this.opts); this.requestRender(); if (this.toolId === "crop") this.requestRender(); this.applyTextOpt(inp.dataset.k, v);
    }));
    host.querySelectorAll("input[type=checkbox]").forEach((inp) => (inp.onchange = () => { this.opts[inp.dataset.o][inp.dataset.k] = inp.checked; saveOpts(this.opts); this.applyTextOpt(inp.dataset.k, inp.checked); }));
    host.querySelectorAll("select").forEach((s) => (s.onchange = () => { const v = isNaN(+s.value) ? s.value : +s.value; this.opts[s.dataset.o][s.dataset.k] = v; saveOpts(this.opts); this.applyTextOpt(s.dataset.k, v); if (this.toolId === "crop" && s.dataset.k === "ratio") TOOLS.crop.activate(this); }));
    host.querySelectorAll("[data-selmode]").forEach((b) => (b.onclick = () => { this.opts.selection.mode = b.dataset.selmode; saveOpts(this.opts); this.renderOptions(); }));
    host.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = () => { o[b.dataset.toggle] = !o[b.dataset.toggle]; saveOpts(this.opts); this.applyTextOpt(b.dataset.toggle, o[b.dataset.toggle]); this.renderOptions(); }));
    host.querySelectorAll("[data-align]").forEach((b) => (b.onclick = () => { o.align = b.dataset.align; saveOpts(this.opts); this.applyTextOpt("align", o.align); this.renderOptions(); }));
    host.querySelectorAll("[data-kind]").forEach((b) => (b.onclick = () => { o.kind = b.dataset.kind; saveOpts(this.opts); this.renderOptions(); }));
    host.querySelectorAll("[data-cmd]").forEach((b) => (b.onclick = () => this.cmd(b.dataset.cmd)));
    host.querySelectorAll("[data-xf]").forEach((b) => (b.onclick = () => this.xfAction(b.dataset.xf, b)));
    host.querySelectorAll("[data-xfv]").forEach((inp) => (inp.onchange = () => this.xfSetValue(inp.dataset.xfv, +inp.value)));
  }
  cropTo(r, angle = 0) { COMMANDS.cropTo(this, r, angle); }
  xfSetValue(kind, v) {
    const xf = this.xf; if (!xf || !Number.isFinite(v)) return;
    const q = xf.quad, b = xf.bounds, c = { x: (q[0].x + q[2].x) / 2, y: (q[0].y + q[2].y) / 2 };
    const cur = { w: Math.hypot(q[1].x - q[0].x, q[1].y - q[0].y) / b.w * 100, h: Math.hypot(q[3].x - q[0].x, q[3].y - q[0].y) / b.h * 100, a: Math.atan2(q[1].y - q[0].y, q[1].x - q[0].x) * 180 / Math.PI };
    cur[kind] = v;
    const w = b.w * cur.w / 100, h = b.h * cur.h / 100, a = cur.a * Math.PI / 180, co = Math.cos(a), si = Math.sin(a);
    xf.quad = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]].map(([x, y]) => ({ x: c.x + x * co - y * si, y: c.y + x * si + y * co }));
    this.xfPreview();
  }
  cmd(name, ...args) { const fn = COMMANDS.run[name]; if (!fn) return; try { const r = fn(this, ...args); if (r?.catch) r.catch((err) => this.error(err)); return r; } catch (err) { this.error(err); } }
  cropRatio() {
    const r = this.opts.crop.ratio; if (r === "free") return 0; if (r === "original") return this.doc.width / this.doc.height;
    const [a, b] = String(r).split(":").map(Number); return a && b ? a / b : 0;
  }
  brushSize(d) { const o = this.opts[this.toolId]; if (!o?.size) return; const s = o.size; o.size = Math.max(1, Math.min(1000, Math.round(d > 0 ? (s < 10 ? s + 1 : s * 1.15) : (s <= 10 ? s - 1 : s / 1.15)))); saveOpts(this.opts); this.renderOptions(); this.requestRender(); this.hint(`${t("ph_size")}: ${o.size}px`); }
  brushHardness(d) { const o = this.opts[this.toolId]; if (o?.hardness === undefined) return; o.hardness = Math.max(0, Math.min(100, o.hardness + d)); saveOpts(this.opts); this.renderOptions(); this.hint(`${t("ph_hardness")}: ${o.hardness}%`); }

  rasterTarget(forPaint = false) {
    const doc = this.doc, l = doc.active;
    if (!l) { toast(t("ph_no_layer")); return null; }
    if (l.locked) { toast(t("ph_layer_locked")); return null; }
    if (!l.visible) { toast(t("ph_layer_hidden")); return null; }
    if (doc.target === "mask" && l.mask) return { canvas: l.mask, x: l.type === "group" ? 0 : l.x, y: l.type === "group" ? 0 : l.y, isMask: true, layer: l, layerId: l.id };
    if (l.type === "group") { toast(t("ph_group_no_paint")); return null; }
    if (isVector(l)) { if (forPaint) { this.askRasterize(l); } return null; }
    if (forPaint && (l.x !== 0 || l.y !== 0 || l.canvas.width !== doc.width || l.canvas.height !== doc.height)) this.expandLayer(l);
    return { canvas: l.canvas, x: l.x, y: l.y, isMask: false, layer: l, layerId: l.id };
  }
  expandLayer(l) {
    const doc = this.doc, c = newCanvas(doc.width, doc.height); ctx2d(c).drawImage(l.canvas, l.x, l.y);
    let m = null; if (l.mask) { m = newCanvas(doc.width, doc.height); const mx = ctx2d(m); mx.fillStyle = "#fff"; mx.fillRect(0, 0, doc.width, doc.height); mx.clearRect(l.x, l.y, l.mask.width, l.mask.height); mx.drawImage(l.mask, l.x, l.y); }
    doc.begin(t("ph_layer")); doc.swap(l, c); if (m) doc.swap(l, m, "mask"); l.x = 0; l.y = 0; doc.commit(t("ph_expand_layer"));
  }
  async askRasterize(l) {
    const { confirm } = await import("../core.js");
    if (await confirm(t("ph_rasterize_title"), t("ph_rasterize_text", { name: l.name }), t("ph_rasterize"))) this.cmd("rasterize");
  }
  layerImageCanvas() { const l = this.doc.active; if (!l || l.type === "group") return this.doc.flatten(); if (l.x === 0 && l.y === 0 && l.canvas.width === this.doc.width && l.canvas.height === this.doc.height) return l.canvas; const c = newCanvas(this.doc.width, this.doc.height); ctx2d(c).drawImage(l.canvas, l.x, l.y); return c; }
  layerAt(p, filter = null) {
    const list = this.doc.flat().filter((l) => l.type !== "group" && l.visible).reverse();
    for (const l of list) {
      if (filter && !filter(l)) continue;
      if (isVector(l)) { const b = vectorBounds(this.doc, l); if (b && p.x >= Math.min(b.x, b.x + b.w) - 4 && p.x <= Math.max(b.x, b.x + b.w) + 4 && p.y >= Math.min(b.y, b.y + b.h) - 4 && p.y <= Math.max(b.y, b.y + b.h) + 4) return l; continue; }
      const lx = Math.floor(p.x - l.x), ly = Math.floor(p.y - l.y); if (lx < 0 || ly < 0 || lx >= l.canvas.width || ly >= l.canvas.height) continue;
      if (ctx2d(l.canvas).getImageData(lx, ly, 1, 1).data[3] > 10) return l;
    }
    return null;
  }

  select(mask, mode = "new", label = t("ph_selection")) {
    const doc = this.doc; let m = mask;
    if (mode !== "new" && doc.selection && mask) m = combineMasks(doc.selection, mask, mode);
    else if (mode === "intersect" && !doc.selection) m = null;
    else if (mode === "subtract" && !doc.selection) return;
    if (!m && !doc.selection) return;
    doc.begin(label); doc.setSelection(m); doc.commit(label);
    this.antsKey = ""; this.requestRender(); this.renderStatus();
  }
  async feather(m, r) { const res = await this.worker({ kind: "mask", width: this.doc.width, height: this.doc.height, buffer: new ArrayBuffer(4), mask: m.buffer, opts: { feather: r } }, [m.buffer]); return new Uint8Array(res.mask); }
  clearSelection() {
    const t0 = this.rasterTarget(true); if (!t0) return;
    const doc = this.doc;
    doc.begin(t("ph_clear")); doc.touch(t0.canvas);
    const x = ctx2d(t0.canvas);
    if (doc.selection) { x.globalCompositeOperation = "destination-out"; x.drawImage(doc.selCanvas(), -t0.x, -t0.y); x.globalCompositeOperation = "source-over"; }
    else if (t0.isMask) { x.fillStyle = "#fff"; x.fillRect(0, 0, t0.canvas.width, t0.canvas.height); }
    else x.clearRect(0, 0, t0.canvas.width, t0.canvas.height);
    doc.commit();
  }

  async segmentBox(box, { seed = null } = {}) {
    const doc = this.doc, src = this.opts.wand.sampleAll === false && this.toolId !== "objsel" ? this.layerImageCanvas() : doc.flatten();
    const k = Math.min(1, 700 / Math.max(doc.width, doc.height)), w = Math.max(10, Math.round(doc.width * k)), h = Math.max(10, Math.round(doc.height * k));
    const small = newCanvas(w, h), sx = ctx2d(small, { willReadFrequently: true }); sx.imageSmoothingQuality = "high"; sx.drawImage(src, 0, 0, w, h);
    const img = sx.getImageData(0, 0, w, h);
    let seedSmall = null;
    if (seed) { seedSmall = new Uint8Array(w * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) seedSmall[y * w + x] = seed[Math.min(doc.height - 1, Math.floor(y / k)) * doc.width + Math.min(doc.width - 1, Math.floor(x / k))]; }
    const res = await this.worker({ kind: "segment", width: w, height: h, buffer: img.data.buffer, box: { x: box.x * k, y: box.y * k, w: box.w * k, h: box.h * k }, opts: { seed: seedSmall?.buffer, feather: 0.8 } }, [img.data.buffer, ...(seedSmall ? [seedSmall.buffer] : [])]);
    return this.upscaleMask(new Uint8Array(res.mask), w, h);
  }
  upscaleMask(m, w, h) {
    const doc = this.doc, c = newCanvas(w, h), x = ctx2d(c), img = x.createImageData(w, h);
    for (let i = 0; i < m.length; i++) img.data[i * 4 + 3] = m[i];
    x.putImageData(img, 0, 0);
    const big = newCanvas(doc.width, doc.height), bx = ctx2d(big, { willReadFrequently: true }); bx.imageSmoothingEnabled = true; bx.imageSmoothingQuality = "high"; bx.drawImage(c, 0, 0, doc.width, doc.height);
    const d = bx.getImageData(0, 0, doc.width, doc.height).data, out = new Uint8Array(doc.width * doc.height);
    for (let i = 0; i < out.length; i++) { const v = d[i * 4 + 3]; out[i] = v < 40 ? 0 : v > 215 ? 255 : Math.round((v - 40) * 255 / 175); }
    return out;
  }

  worker(job, transfer = []) {
    if (!this.worker_) {
      try { this.worker_ = new Worker(new URL("./pixel-worker.js", import.meta.url), { type: "module" }); this.jobs = new Map(); this.jobId = 0;
        this.worker_.onmessage = (e) => { const j = this.jobs.get(e.data.id); if (!j) return; this.jobs.delete(e.data.id); e.data.ok ? j.res(e.data) : j.rej(new Error(e.data.error)); };
        this.worker_.onerror = (e) => { for (const j of this.jobs.values()) j.rej(new Error(e.message || "worker")); this.jobs.clear(); };
      } catch (e) { this.worker_ = null; }
    }
    if (!this.worker_) return inlineJob(job);
    const id = ++this.jobId;
    return new Promise((res, rej) => { this.jobs.set(id, { res, rej }); this.worker_.postMessage({ ...job, id }, transfer); });
  }

  setColor(which, hex) {
    if (!parseHex(hex)) return; hex = toHex(...parseHex(hex));
    this[which] = hex; localStorage.setItem(LS + which, hex);
    this.renderTools(); this.renderColor();
    if (which === "fg" && this.textEdit) { this.textEdit.layer.text.color = hex; renderVector(this.doc, this.textEdit.layer); this.layerChanged(); }
  }
  swapColors() { const a = this.fg; this.setColor("fg", this.bg); this.setColor("bg", a); }
  renderColor() {
    const host = this.el.querySelector(".ph-color"); if (!host) return;
    const recent = JSON.parse(localStorage.getItem(LS + "recent") || "[]");
    host.innerHTML = `<div class="ph-color-row"><button class="ph-sw big fg" data-c="fg" style="background:${this.fg}" title="${esc(t("ph_fg"))}"></button><button class="ph-sw big bg" data-c="bg" style="background:${this.bg}" title="${esc(t("ph_bg"))}"></button>
      <input class="input xs ph-hex" value="${this.fg}" spellcheck="false" aria-label="HEX"><button class="icon-btn sm" data-a="pick" title="${esc(t("ph_tool_eyedropper"))} (I)">${picon("eyedropper")}</button></div>
      <div class="ph-swatches">${[...SWATCHES, ...recent.filter((c) => !SWATCHES.includes(c)).slice(0, 8)].map((c) => `<button class="ph-swatch" data-sw="${c}" style="background:${c}" title="${c}"></button>`).join("")}</div>`;
    host.querySelectorAll("[data-c]").forEach((b) => (b.onclick = () => this.pickColor(b.dataset.c, b)));
    host.querySelectorAll("[data-sw]").forEach((b) => { b.onclick = () => this.setColor("fg", b.dataset.sw); b.oncontextmenu = (e) => { e.preventDefault(); this.setColor("bg", b.dataset.sw); }; });
    const hex = host.querySelector(".ph-hex"); hex.onchange = () => { if (parseHex(hex.value)) this.setColor("fg", hex.value); else hex.value = this.fg; };
    host.querySelector("[data-a=pick]").onclick = () => this.setTool("eyedropper");
  }
  rememberColor(hex) { const r = JSON.parse(localStorage.getItem(LS + "recent") || "[]"); localStorage.setItem(LS + "recent", JSON.stringify([hex, ...r.filter((c) => c !== hex)].slice(0, 12))); }
  pickColor(which, anchor) {
    document.querySelector(".ph-picker")?.remove();
    const pop = document.createElement("div"); pop.className = "ph-picker"; document.body.append(pop);
    let [h, s, v] = rgbToHsv(...parseHex(this[which]).slice(0, 3));
    const draw = () => {
      const rgb = hsvToRgb(h, s, v), hex = toHex(...rgb), [hh, ss, ll] = rgbToHsl(...rgb);
      pop.querySelector(".ph-sv").style.background = `linear-gradient(to top,#000,transparent),linear-gradient(to right,#fff,hsl(${h},100%,50%))`;
      pop.querySelector(".ph-sv i").style.cssText = `left:${s}%;top:${100 - v}%`;
      pop.querySelector(".ph-hue i").style.left = (h / 360) * 100 + "%";
      pop.querySelector(".ph-pv").style.background = hex;
      const set = (k, val) => { const el = pop.querySelector(`[data-f=${k}]`); if (document.activeElement !== el) el.value = val; };
      set("hex", hex); set("r", rgb[0]); set("g", rgb[1]); set("b", rgb[2]); set("h", Math.round(hh)); set("s", Math.round(ss)); set("l", Math.round(ll));
      this.setColor(which, hex);
    };
    pop.innerHTML = `<div class="ph-picker-head"><b>${esc(t(which === "fg" ? "ph_fg" : "ph_bg"))}</b><span class="ph-pv"></span></div><div class="ph-sv"><i></i></div><div class="ph-hue"><i></i></div>
      <div class="ph-fields"><label>HEX<input class="input xs" data-f="hex"></label><label>R<input class="input xs" data-f="r" type="number" min="0" max="255"></label><label>G<input class="input xs" data-f="g" type="number" min="0" max="255"></label><label>B<input class="input xs" data-f="b" type="number" min="0" max="255"></label>
      <label>H<input class="input xs" data-f="h" type="number" min="0" max="360"></label><label>S<input class="input xs" data-f="s" type="number" min="0" max="100"></label><label>L<input class="input xs" data-f="l" type="number" min="0" max="100"></label></div>
      <div class="ph-swatches">${SWATCHES.map((c) => `<button class="ph-swatch" data-sw="${c}" style="background:${c}"></button>`).join("")}</div>`;
    const drag = (el, fn) => el.addEventListener("pointerdown", (e) => { el.setPointerCapture(e.pointerId); const mv = (ev) => { const r = el.getBoundingClientRect(); fn(Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (ev.clientY - r.top) / r.height))); draw(); }; mv(e); el.onpointermove = mv; el.onpointerup = () => { el.onpointermove = null; }; });
    drag(pop.querySelector(".ph-sv"), (x, y) => { s = x * 100; v = (1 - y) * 100; });
    drag(pop.querySelector(".ph-hue"), (x) => { h = x * 360; });
    pop.querySelectorAll("[data-f]").forEach((inp) => inp.addEventListener("input", () => {
      const f = (k) => +pop.querySelector(`[data-f=${k}]`).value || 0;
      if (inp.dataset.f === "hex") { const c = parseHex(inp.value); if (!c) return; [h, s, v] = rgbToHsv(c[0], c[1], c[2]); }
      else if ("rgb".includes(inp.dataset.f)) [h, s, v] = rgbToHsv(f("r"), f("g"), f("b"));
      else [h, s, v] = rgbToHsv(...hslToRgb(f("h"), f("s"), f("l")));
      draw();
    }));
    pop.querySelectorAll("[data-sw]").forEach((b) => (b.onclick = () => { [h, s, v] = rgbToHsv(...parseHex(b.dataset.sw).slice(0, 3)); draw(); }));
    const r = anchor.getBoundingClientRect(); pop.style.left = Math.min(innerWidth - 250, Math.max(6, r.left)) + "px"; pop.style.top = Math.min(innerHeight - 330, r.bottom + 6) + "px";
    draw();
    const close = (e) => { if (!pop.contains(e.target) && e.target !== anchor) { pop.remove(); this.rememberColor(this[which]); this.renderColor(); document.removeEventListener("pointerdown", close, true); } };
    setTimeout(() => document.addEventListener("pointerdown", close, true), 0);
  }

  editText(l, isNew = false) {
    if (this.textEdit) this.finishText();
    const doc = this.doc;
    doc.begin(t(isNew ? "ph_layer_text" : "ph_edit_text"));
    if (isNew) { doc.insertLayer(l); renderVector(doc, l); }
    doc.activeId = l.id;
    const ta = document.createElement("textarea"); ta.className = "ph-textedit"; ta.value = l.text.text; ta.spellcheck = false;
    this.stage.append(ta);
    this.textEdit = { layer: l, ta, isNew, before: l.text.text };
    const sync = () => {
      l.text.text = ta.value; renderVector(doc, l); this.layerChanged();
      const b = textBox(doc, l), s = this.toScreen(b), z = this.view.zoom;
      Object.assign(ta.style, { left: s.x - 4 + "px", top: s.y - 4 + "px", width: Math.max(60, b.w * z + 24) + "px", height: Math.max(l.text.size * l.text.lineHeight * z, b.h * z) + 12 + "px", font: fontString({ ...l.text, size: l.text.size * z }), lineHeight: l.text.lineHeight, textAlign: l.text.align });
    };
    ta.addEventListener("input", sync); sync();

    Object.assign(this.opts.text, { font: l.text.font, size: l.text.size, bold: l.text.bold, italic: l.text.italic, underline: l.text.underline, align: l.text.align, stroke: l.text.stroke, shadow: l.text.shadow });
    if (this.toolId !== "text") this.setTool("text"); else this.renderOptions();
    this.refreshPanels();
    setTimeout(() => { ta.focus(); ta.select(); }, 20);
  }
  applyTextOpt(key, v) {
    const te = this.textEdit; const l = te?.layer || (this.doc?.active?.type === "text" && this.toolId === "text" ? this.doc.active : null);
    if (!l || !(key in l.text)) return;
    if (!te) this.doc.begin(t("ph_edit_text"));
    l.text[key] = v; renderVector(this.doc, l); this.layerChanged();
    if (te) te.ta.dispatchEvent(new Event("input")); else this.doc.commit();
  }
  finishText() {
    const te = this.textEdit; if (!te) return; this.textEdit = null;
    te.ta.remove();
    const l = te.layer;
    if (!l.text.text.trim()) { if (te.isNew) { this.doc.cancel(); } else { this.doc.removeLayer(l.id); this.doc.commit(t("ph_delete_layer")); } }
    else if (!te.isNew && te.before === l.text.text) { this.doc.commit(); }
    else { l.name = l.text.text.split("\n")[0].slice(0, 40) || l.name; this.doc.commit(); }
    this.refreshPanels(); this.el.focus({ preventScroll: true });
  }

  renderStatus() {
    const host = this.el.querySelector(".ph-status"); if (!host) return;
    if (!this.doc) { host.innerHTML = ""; return; }
    const d = this.doc, p = this.hover;
    let color = "";
    if (p && p.x >= 0 && p.y >= 0 && p.x < d.width && p.y < d.height && !this.down) {
      const px = ctx2d(d.composite()).getImageData(Math.floor(p.x), Math.floor(p.y), 1, 1).data;
      color = px[3] ? toHex(px[0], px[1], px[2]) : "";
    }
    const sb = d.selection && this.selBoundsCache?.v === d.selVersion ? this.selBoundsCache.b : d.selection ? (this.selBoundsCache = { v: d.selVersion, b: d.selectionBounds() }).b : null;
    host.innerHTML = `<span>${d.width} × ${d.height} px</span><span class="sep"></span><span>${esc(this.info?.format || "")}</span><span class="sep"></span><span>${p ? `X ${Math.floor(p.x)}  Y ${Math.floor(p.y)}` : "&nbsp;"}</span>${color ? `<span class="ph-st-color"><i style="background:${color}"></i>${color}</span>` : ""}${sb ? `<span class="sep"></span><span>${esc(t("ph_selection"))}: ${sb.w} × ${sb.h}</span>` : ""}<span class="ph-sp"></span>${this._hint ? `<span class="ph-st-hint">${esc(this._hint)}</span>` : ""}<span>${Math.round(this.view.zoom * 1000) / 10}%</span>`;
  }

  renderTabs() {
    const tabs = [["layers", "layers", "ph_tab_layers"], ["props", "sliders", "ph_tab_adjust"], ["history", "history", "ph_tab_history"], ["ai", "sparkle", "ph_tab_ai"]];
    const host = this.el.querySelector(".ph-tabs");
    host.innerHTML = tabs.map(([id, ic, k]) => `<button class="ph-tab ${this.sideTab === id ? "on" : ""} ${id === "ai" ? "ai" : ""}" data-tab="${id}" role="tab" aria-selected="${this.sideTab === id}" title="${esc(t(k))}">${picon(ic)}<span>${esc(t(k))}</span></button>`).join("");
    host.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => this.showTab(b.dataset.tab)));
  }
  showTab(id) { this.sideTab = id; localStorage.setItem(LS + "side", id); if (!this.sideOpen) this.toggleSide(true); this.renderTabs(); this.refreshPanels(true); }
  refreshPanels(now = false) {
    if (now) { cancelAnimationFrame(this.panelRaf); this.panelRaf = 0; this._refresh(); return; }
    if (this.panelRaf) return; this.panelRaf = requestAnimationFrame(() => { this.panelRaf = 0; this._refresh(); });
  }
  _refresh() {
    const body = this.el.querySelector(".ph-tabbody"); if (!body) return;
    if (this.sideTab === "ai") { if (!body.querySelector(".pai")) { body.innerHTML = ""; this.ai.mount(body); } else this.ai.refresh(); return; }
    if (!this.doc) { body.innerHTML = `<p class="muted pad">${esc(t("ph_no_document"))}</p>`; return; }
    if (this.sideTab === "layers") this.renderLayers(body);
    else if (this.sideTab === "history") this.renderHistory(body);
    else this.renderProps(body);
    this.renderStatus();
  }
  renderLayers(body) {
    const d = this.doc, a = d.active;
    const rows = [];
    const walk = (list, depth) => { for (let i = list.length - 1; i >= 0; i--) { const l = list[i]; rows.push([l, depth]); if (l.type === "group" && l.open) walk(l.children, depth + 1); } };
    walk(d.layers, 0);
    const blendOpts = BLEND_MODES.map((m) => `<option value="${m}" ${a?.blend === m ? "selected" : ""}>${esc(t("ph_blend_" + m))}</option>`).join("");
    body.innerHTML = `<div class="ph-lprops"><select class="input xs" data-lp="blend" ${a ? "" : "disabled"} title="${esc(t("ph_blend"))}">${blendOpts}</select>
      <label class="ph-op" title="${esc(t("ph_opacity"))}">${picon("opacity")}<input type="range" min="0" max="100" value="${Math.round((a?.opacity ?? 1) * 100)}" data-lp="opacity"><input class="ph-num" type="number" min="0" max="100" value="${Math.round((a?.opacity ?? 1) * 100)}" data-lp="opacity2"><em>%</em></label>
      <button class="icon-btn sm ${a?.locked ? "on" : ""}" data-lp="lock" title="${esc(t("ph_lock"))}">${picon(a?.locked ? "lock" : "unlock")}</button></div>
      <div class="ph-layers" role="listbox">${rows.map(([l, depth]) => `<div class="ph-layer ${l.id === d.activeId ? "on" : ""} ${l.visible ? "" : "hidden-l"}" data-id="${l.id}" draggable="true" role="option" aria-selected="${l.id === d.activeId}" style="--depth:${depth}">
        <button class="ph-eye" data-eye="${l.id}" title="${esc(t("ph_visibility"))}">${picon(l.visible ? "eye" : "eyeOff")}</button>
        ${l.type === "group" ? `<button class="ph-fold" data-fold="${l.id}">${picon(l.open ? "chevDown" : "chevRight")}</button><span class="ph-thumb grp">${picon("folder")}</span>` : `<canvas class="ph-thumb ${d.target === "layer" && l.id === d.activeId && l.mask ? "sel" : ""}" data-thumb="${l.id}" width="40" height="30"></canvas>`}
        ${l.mask ? `<canvas class="ph-thumb mask ${d.target === "mask" && l.id === d.activeId ? "sel" : ""} ${l.maskOn ? "" : "off"}" data-mthumb="${l.id}" width="40" height="30" title="${esc(t("ph_mask_thumb"))}"></canvas>` : ""}
        <span class="ph-lname" title="${esc(l.name)}">${l.type === "text" ? picon("text") : l.type === "shape" ? picon("shapes") : l.type === "svg" ? picon("vector") : ""}${esc(l.name)}</span>
        ${l.locked ? `<span class="ph-lk">${picon("lock")}</span>` : ""}${l.blend !== "normal" ? `<span class="ph-lb" title="${esc(t("ph_blend_" + l.blend))}">${esc(t("ph_blend_" + l.blend).slice(0, 3))}</span>` : ""}</div>`).join("")}</div>
      <div class="ph-lfoot">${[["newLayer", "plus", "ph_new_layer"], ["newGroup", "folder", "ph_new_group"], ["addMask", "mask", "ph_add_mask"], ["duplicateLayer", "copy", "ph_duplicate_layer"], ["mergeDown", "merge", "ph_merge_down"], ["deleteLayer", "trash", "ph_delete_layer"]].map(([c, ic, k]) => `<button class="icon-btn sm" data-cmd="${c}" title="${esc(t(k))}">${picon(ic)}</button>`).join("")}<span class="ph-sp"></span><button class="icon-btn sm" data-a="lmenu" title="${esc(t("ph_menu_layer"))}">${picon("more")}</button></div>`;
    body.querySelector("[data-lp=blend]").onchange = (e) => this.cmd("setLayerProp", "blend", e.target.value);
    const op = body.querySelector("[data-lp=opacity]"), op2 = body.querySelector("[data-lp=opacity2]");
    const opLive = (v) => { if (!a) return; if (!this.opDrag) { this.doc.begin(t("ph_opacity")); this.opDrag = true; } a.opacity = v / 100; this.layerChanged(); };
    op.oninput = () => { op2.value = op.value; opLive(+op.value); }; op2.oninput = () => { op.value = op2.value; opLive(+op2.value); };
    const opEnd = () => { if (this.opDrag) { this.opDrag = false; this.doc.commit(`${t("ph_opacity")} ${Math.round(a.opacity * 100)}%`); } };
    op.onchange = opEnd; op2.onchange = opEnd;
    body.querySelector("[data-lp=lock]").onclick = () => a && this.cmd("setLayerProp", "locked", !a.locked);
    body.querySelectorAll("[data-cmd]").forEach((b) => (b.onclick = () => this.cmd(b.dataset.cmd)));
    body.querySelector("[data-a=lmenu]").onclick = (e) => this.openMenu("layer", e.currentTarget);
    body.querySelectorAll(".ph-layer").forEach((row) => {
      const id = row.dataset.id;
      row.onclick = (e) => {
        if (e.target.closest(".ph-eye,.ph-fold")) return;
        if (e.ctrlKey && row.querySelector("[data-thumb]") && e.target.closest(".ph-thumb")) { this.cmd("selectionFromLayer", id); return; }
        const mask = !!e.target.closest("[data-mthumb]");
        if (mask && e.shiftKey) { this.cmd("toggleMask", id); return; }
        this.doc.activeId = id; this.doc.target = mask ? "mask" : "layer"; this.refreshPanels(); this.renderOptions();
      };
      row.ondblclick = (e) => { if (e.target.closest(".ph-lname")) this.renameLayer(id); else if (this.doc.find(id)?.layer.type === "text") this.editText(this.doc.find(id).layer); };
      row.oncontextmenu = (e) => { e.preventDefault(); this.doc.activeId = id; this.refreshPanels(); this.openMenu("layer", { x: e.clientX, y: e.clientY }); };
      row.ondragstart = (e) => { e.dataTransfer.setData("text/ph-layer", id); row.classList.add("dragging"); };
      row.ondragend = () => row.classList.remove("dragging");
      row.ondragover = (e) => { if (e.dataTransfer.types.includes("text/ph-layer")) { e.preventDefault(); const r = row.getBoundingClientRect(); row.dataset.drop = e.clientY < r.top + r.height / 2 ? "above" : this.doc.find(id)?.layer.type === "group" && e.clientY < r.bottom - r.height / 4 ? "into" : "below"; } };
      row.ondragleave = () => delete row.dataset.drop;
      row.ondrop = (e) => { e.preventDefault(); const from = e.dataTransfer.getData("text/ph-layer"), where = row.dataset.drop; delete row.dataset.drop; if (from && from !== id) this.cmd("moveLayerTo", from, id, where); };
    });
    body.querySelectorAll("[data-eye]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); if (e.altKey) this.cmd("soloLayer", b.dataset.eye); else this.cmd("setLayerProp", "visible", !this.doc.find(b.dataset.eye).layer.visible, b.dataset.eye); }));
    body.querySelectorAll("[data-fold]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); const l = this.doc.find(b.dataset.fold).layer; l.open = !l.open; this.refreshPanels(); }));
    this.drawThumbs();
  }
  thumbsSoon() { clearTimeout(this.thumbT); this.thumbT = setTimeout(() => this.drawThumbs(), 250); }
  drawThumbs() {
    const body = this.el.querySelector(".ph-tabbody"); if (!body || this.sideTab !== "layers" || !this.doc) return;
    const d = this.doc;
    body.querySelectorAll("canvas[data-thumb], canvas[data-mthumb]").forEach((c) => {
      const l = d.find(c.dataset.thumb || c.dataset.mthumb)?.layer; if (!l) return;
      const x = c.getContext("2d"), k = Math.min(c.width / d.width, c.height / d.height), w = d.width * k, h = d.height * k, ox = (c.width - w) / 2, oy = (c.height - h) / 2;
      x.clearRect(0, 0, c.width, c.height);
      if (c.dataset.mthumb) {
        const mx = l.type === "group" ? 0 : l.x, my = l.type === "group" ? 0 : l.y, tc = newCanvas(c.width, c.height), tx = ctx2d(tc);
        tx.drawImage(l.mask, ox + mx * k, oy + my * k, l.mask.width * k, l.mask.height * k); tx.globalCompositeOperation = "source-in"; tx.fillStyle = "#fff"; tx.fillRect(0, 0, tc.width, tc.height);
        x.fillStyle = "#000"; x.fillRect(ox, oy, w, h); x.drawImage(tc, 0, 0); return;
      }
      x.fillStyle = x.createPattern(checkerTile(4), "repeat"); x.fillRect(ox, oy, w, h);
      x.drawImage(l.canvas, ox + l.x * k, oy + l.y * k, l.canvas.width * k, l.canvas.height * k);
    });
  }
  async renameLayer(id) {
    const l = this.doc.find(id)?.layer; if (!l) return;
    const { prompt } = await import("../core.js");
    const v = await prompt(t("ph_rename_layer"), t("ph_name"), l.name); if (!v || v === l.name) return;
    this.doc.begin(t("ph_rename_layer")); l.name = v.slice(0, 120); this.doc.commit();
  }
  renderHistory(body) {
    const d = this.doc, n = d.history.length;
    const items = [[0, t("ph_hist_open"), n === 0], ...d.history.map((e, i) => [i + 1, e.label, i + 1 === n]), ...[...d.future].reverse().map((e, i) => [n + i + 1, e.label, false, true])];
    body.innerHTML = `<div class="ph-hist">${items.map(([i, label, cur, fut]) => `<button class="ph-hrow ${cur ? "on" : ""} ${fut ? "future" : ""}" data-h="${i}">${picon(i === 0 ? "image" : "dot")}<span>${esc(label)}</span></button>`).join("")}</div>
      <div class="ph-lfoot"><span class="muted small">${esc(t("ph_hist_hint"))}</span></div>`;
    body.querySelectorAll("[data-h]").forEach((b) => (b.onclick = () => { if (this.textEdit) this.finishText(); this.doc.goto(+b.dataset.h); }));
    body.querySelector(".ph-hrow.on")?.scrollIntoView({ block: "nearest" });
  }
  renderProps(body) {
    const d = this.doc, l = d.active;
    const adj = [["brightnessContrast", "sun"], ["levels", "levels"], ["curves", "curve"], ["exposure", "exposure"], ["hueSaturation", "hue"], ["vibrance", "vibrance"], ["colorBalance", "balance"], ["temperature", "thermo"], ["blackWhite", "bw"], ["sepia", "sepia"], ["invert", "invert"], ["threshold", "threshold"], ["posterize", "posterize"], ["autoLevels", "auto"], ["replaceColor", "replace"]];
    const flt = [["gaussianBlur", "drop"], ["motionBlur", "motion"], ["unsharpMask", "triangle"], ["sharpen", "triangle"], ["noise", "noise"], ["denoise", "denoise"], ["pixelate", "pixelate"], ["vignette", "vignette"], ["emboss", "emboss"], ["edges", "edges"], ["oilPaint", "brush"]];
    let props = "";
    if (l?.type === "text") props = this.textPropsHtml(l);
    else if (l?.type === "shape") props = this.shapePropsHtml(l);
    body.innerHTML = `${props}<div class="ph-sec"><h4>${esc(t("ph_histogram"))}</h4><canvas class="ph-histo" width="256" height="72"></canvas></div>
      <div class="ph-sec"><h4>${esc(t("ph_adjustments"))}</h4><div class="ph-grid">${adj.map(([k, ic]) => `<button class="ph-gbtn" data-adj="${k}">${picon(ic)}<span>${esc(t("ph_adj_" + k))}</span></button>`).join("")}</div></div>
      <div class="ph-sec"><h4>${esc(t("ph_filters"))}</h4><div class="ph-grid">${flt.map(([k, ic]) => `<button class="ph-gbtn" data-adj="${k}">${picon(ic)}<span>${esc(t("ph_adj_" + k))}</span></button>`).join("")}</div></div>`;
    body.querySelectorAll("[data-adj]").forEach((b) => (b.onclick = () => this.cmd("adjust", b.dataset.adj)));
    this.bindProps(body, l);
    this.drawHistogram(body.querySelector(".ph-histo"));
  }
  textPropsHtml(l) {
    const tx = l.text;
    return `<div class="ph-sec"><h4>${picon("text")} ${esc(t("ph_text_props"))}</h4><textarea class="input ph-ptext" data-tp="text" rows="2">${esc(tx.text)}</textarea>
      <div class="ph-pgrid"><label>${esc(t("ph_font"))}<select class="input xs" data-tp="font">${FONTS.map((f) => `<option ${f === tx.font ? "selected" : ""}>${esc(f)}</option>`).join("")}</select></label>
      <label>${esc(t("ph_size"))}<input class="input xs" type="number" min="4" max="2000" data-tp="size" value="${tx.size}"></label>
      <label>${esc(t("ph_color"))}<input class="input xs" type="color" data-tp="color" value="${tx.color}"></label>
      <label>${esc(t("ph_outline"))}<input class="input xs" type="number" min="0" max="60" data-tp="stroke" value="${tx.stroke}"></label>
      <label>${esc(t("ph_outline_color"))}<input class="input xs" type="color" data-tp="strokeColor" value="${tx.strokeColor}"></label>
      <label>${esc(t("ph_rotate"))}<input class="input xs" type="number" min="-360" max="360" data-tp="rotate" value="${tx.rotate || 0}"></label>
      <label>${esc(t("ph_line_height"))}<input class="input xs" type="number" min="0.6" max="4" step="0.05" data-tp="lineHeight" value="${tx.lineHeight}"></label>
      <label>${esc(t("ph_box_width"))}<input class="input xs" type="number" min="0" max="30000" data-tp="width" value="${tx.width}"></label></div>
      <div class="ph-row">${["bold", "italic", "underline", "shadow"].map((k) => `<label class="ph-opt chk"><input type="checkbox" data-tp="${k}" ${tx[k] ? "checked" : ""}><span>${esc(t("ph_" + k))}</span></label>`).join("")}</div></div>`;
  }
  shapePropsHtml(l) {
    const s = l.shape;
    return `<div class="ph-sec"><h4>${picon("shapes")} ${esc(t("ph_shape_props"))}</h4><div class="ph-pgrid">
      <label>${esc(t("ph_fill"))}<input class="input xs" type="color" data-sp="fill" value="${s.fill || "#000000"}"></label><label class="ph-opt chk"><input type="checkbox" data-sp="nofill" ${s.fill ? "" : "checked"}><span>${esc(t("ph_no_fill"))}</span></label>
      <label>${esc(t("ph_stroke"))}<input class="input xs" type="color" data-sp="stroke" value="${s.stroke || "#000000"}"></label><label>${esc(t("ph_stroke_width"))}<input class="input xs" type="number" min="0" max="200" data-sp="strokeWidth" value="${s.strokeWidth}"></label>
      ${["x", "y", "w", "h"].map((k) => `<label>${k.toUpperCase()}<input class="input xs" type="number" data-sp="${k}" value="${Math.round(s[k])}"></label>`).join("")}
      ${s.kind === "rect" ? `<label>${esc(t("ph_radius"))}<input class="input xs" type="number" min="0" data-sp="radius" value="${s.radius}"></label>` : ""}${s.kind === "polygon" || s.kind === "star" ? `<label>${esc(t("ph_sides"))}<input class="input xs" type="number" min="3" max="24" data-sp="sides" value="${s.sides}"></label>` : ""}</div></div>`;
  }
  bindProps(body, l) {
    const apply = (fn, label) => { this.doc.begin(label); fn(); renderVector(this.doc, l); this.doc.commit(); };
    body.querySelectorAll("[data-tp]").forEach((el) => el.addEventListener("change", () => apply(() => { const k = el.dataset.tp; l.text[k] = el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value; if (k === "text") l.name = el.value.split("\n")[0].slice(0, 40) || l.name; }, t("ph_edit_text"))));
    body.querySelectorAll("[data-sp]").forEach((el) => el.addEventListener("change", () => apply(() => { const k = el.dataset.sp; if (k === "nofill") l.shape.fill = el.checked ? "" : l.shape.fill || "#000000"; else l.shape[k] = el.type === "number" ? +el.value : el.value; }, t("ph_shape_props"))));
  }
  drawHistogram(c) {
    if (!c || !this.doc) return;
    const src = this.doc.composite(), k = Math.min(1, 400 / Math.max(src.width, src.height)), w = Math.max(1, Math.round(src.width * k)), h = Math.max(1, Math.round(src.height * k));
    const s = newCanvas(w, h), sx = ctx2d(s, { willReadFrequently: true }); sx.drawImage(src, 0, 0, w, h);
    const hist = histogram(sx.getImageData(0, 0, w, h)), x = c.getContext("2d"); x.clearRect(0, 0, 256, 72);
    const max = Math.max(1, ...[hist.r, hist.g, hist.b].map((a) => Math.max(...a.slice(2, 254))));
    x.globalCompositeOperation = "screen";
    for (const [arr, col] of [[hist.r, "rgba(255,70,70,.8)"], [hist.g, "rgba(70,220,90,.8)"], [hist.b, "rgba(80,130,255,.85)"]]) { x.fillStyle = col; x.beginPath(); x.moveTo(0, 72); for (let i = 0; i < 256; i++) x.lineTo(i, 72 - Math.min(72, (arr[i] / max) * 70)); x.lineTo(255, 72); x.fill(); }
    x.globalCompositeOperation = "source-over";
  }

  openMenu(id, anchor) { const items = COMMANDS.menu(this, id); if (items) showMenu(items, anchor); }
  contextMenu(e) {
    const p = this.toDoc(e);
    showMenu(COMMANDS.context(this, p), { x: e.clientX, y: e.clientY });
  }

  undo() { if (this.textEdit) this.finishText(); if (this.xf) { this.xfCancel(); return; } this.doc?.undo(); }
  redo() { if (this.textEdit) this.finishText(); this.doc?.redo(); }

  async save(path, o = {}) {
    if (this.textEdit) this.finishText();
    if (this.xf) this.xfApply();
    const fmt = formatOfPath(path) || "png";
    const layered = this.doc.flat().length > 1 || this.doc.flat().some((l) => l.type !== "raster" || l.mask);
    if (layered && !SAVE_FORMATS[fmt]?.layers && !localStorage.getItem(LS + "flat_ok")) {
      toast(t("ph_flatten_notice", { fmt: fmt.toUpperCase() }), { ms: 9000, action: { label: t("ph_dont_show"), run: () => localStorage.setItem(LS + "flat_ok", "1") } });
    }
    this.busy(true, t("ph_saving"));
    try {
      const r = await saveDocument(this.doc, path, fmt, { mtime: o.mtime, force: o.force, quality: this.opts.export?.quality ?? 92 });
      if (r.ok) { this.doc.markSaved(); this.hooks.onDirty?.(false); this.info = { ...(this.info || {}), format: fmt.toUpperCase() }; this.renderStatus(); }
      return r;
    } finally { this.busy(false); }
  }

  async saveAs(exportOnly = false) {
    if (!this.doc) return false;
    const r = await COMMANDS.exportDialog(this, exportOnly);
    return !!r;
  }

  async placeFile(file, name) {
    try {
      let canvas;
      if (/svg/.test(file.type) || /\.svg$/i.test(file.name || "")) { const { loadSvgImage } = await import("./doc.js"); const img = await loadSvgImage(await file.text()); canvas = newCanvas(img.naturalWidth || 512, img.naturalHeight || 512); ctx2d(canvas).drawImage(img, 0, 0, canvas.width, canvas.height); }
      else { const bmp = await createImageBitmap(file); canvas = newCanvas(bmp.width, bmp.height); ctx2d(canvas).drawImage(bmp, 0, 0); bmp.close?.(); }
      this.placeCanvas(canvas, name || file.name || t("ph_layer"));
    } catch (e) { toast(t("ph_place_failed") + ": " + e.message, { kind: "error" }); }
  }
  placeCanvas(canvas, name) {
    const d = this.doc;
    let c = canvas;
    if (c.width > d.width || c.height > d.height) { const k = Math.min(d.width / c.width, d.height / c.height); const s = newCanvas(c.width * k, c.height * k); const x = ctx2d(s); x.imageSmoothingQuality = "high"; x.drawImage(c, 0, 0, s.width, s.height); c = s; }
    d.begin(t("ph_place"));
    const l = d.insertLayer(d.makeLayer({ name: String(name).replace(/\.[^.]+$/, "").slice(0, 60), canvas: c, x: Math.round((d.width - c.width) / 2), y: Math.round((d.height - c.height) / 2) }));
    d.commit(t("ph_place") + ": " + l.name);
    this.setTool("move");
  }
  async pasteAsNew() {
    try {
      const items = await navigator.clipboard.read();
      for (const it of items) { const type = it.types.find((x) => x.startsWith("image/")); if (type) { await this.pasteBlobAsNew(await it.getType(type)); return; } }
      toast(t("ph_clipboard_empty"));
    } catch { toast(t("ph_clipboard_denied")); }
  }
  async pasteBlobAsNew(blob) {
    const bmp = await createImageBitmap(blob), d = new PhotoDoc(bmp.width, bmp.height), l = d.makeLayer({ name: t("ph_layer_background") });
    ctx2d(l.canvas).drawImage(bmp, 0, 0); d.layers.push(l); d.activeId = l.id; this.setDoc(d); d.history.push({ label: t("ph_pasted"), before: d.snapshot(), after: d.snapshot(), patches: [], bytes: 0 }); this.hooks.onDirty?.(true); this.refreshPanels();
  }
  pasteInternal() { if (this.clip) this.placeCanvas(cloneCanvas(this.clip), t("ph_pasted")); }
  async offerFrames(info) {
    if (!this.visible()) { this.pendingFrames = info; return; }
    this.pendingFrames = null;
    toast(t("ph_frames_found", { n: info.frames }), { ms: 10000, action: { label: t("ph_frames_import"), run: () => this.cmd("importFrames") } });
  }
}

Object.assign(PhotoEditor.prototype, TRANSFORM);

function checkerTile(n = 8) { const c = newCanvas(n * 2, n * 2), x = ctx2d(c); x.fillStyle = "#ffffff"; x.fillRect(0, 0, n * 2, n * 2); x.fillStyle = "#d6d6d6"; x.fillRect(0, 0, n, n); x.fillRect(n, n, n, n); return c; }
function clampInt(v, a, b, d) { const n = Math.round(+v); return Number.isFinite(n) ? Math.max(a, Math.min(b, n)) : d; }
function loadOpts() { const base = DEFAULT_OPTS(); try { const saved = JSON.parse(localStorage.getItem(LS + "opts") || "{}"); for (const k of Object.keys(base)) if (saved[k] && typeof saved[k] === "object") Object.assign(base[k], saved[k]); base.export = saved.export || { quality: 92 }; } catch {  } return base; }
function saveOpts(o) { try { localStorage.setItem(LS + "opts", JSON.stringify(o)); } catch {  } }
async function inlineJob(job) {
  const P = await import("./pixels.js");
  const img = { width: job.width, height: job.height, data: new Uint8ClampedArray(job.buffer) };
  if (job.kind === "op") return { buffer: P.runOp(job.name, img, job.params, job.mask ? new Uint8Array(job.mask) : null).data.buffer };
  if (job.kind === "segment") return { mask: P.segment(img, job.box, { ...job.opts, seedMask: job.opts?.seed ? new Uint8Array(job.opts.seed) : null }).buffer };
  if (job.kind === "inpaint") return { buffer: P.inpaint(img, new Uint8Array(job.mask), job.opts).data.buffer };
  if (job.kind === "wand") return { mask: P.magicWand(img, job.opts.x, job.opts.y, job.opts).buffer };
  if (job.kind === "mask") { let m = new Uint8Array(job.mask); if (job.opts.grow) m = P.growMask(m, job.width, job.height, job.opts.grow); if (job.opts.feather) m = P.featherMask(m, job.width, job.height, job.opts.feather); return { mask: m.buffer }; }
  throw new Error("job " + job.kind);
}
export { saveOpts };
