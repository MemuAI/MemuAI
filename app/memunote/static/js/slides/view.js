

import { $, $$, api, rawUrl, esc, icon, t, I18N, toast, showMenu, closeMenus, modal, prompt, confirm, debounce, kbd, basename } from "../core.js";
import { openPptx, savePptx, newDeck, makeSlide, duplicateSlide, plainText, uid, sigOf, slideAnims } from "./pptx.js";
import { renderSlide, renderElement, fitTexts, textHtml, isLineGeom, backgroundCss } from "./render.js";
import { cssColor, EMU_PER_PT, EMU_PER_IN, EMU_PER_CM } from "./xml.js";
import { THEMES, LAYOUTS, SIZES } from "./templates.js";
import * as TE from "./textedit.js";
import { SICONS, sic } from "./icons.js";

const clone = (v) => JSON.parse(JSON.stringify(v));
const CM_EMU = EMU_PER_CM;
export const SLIDE_FONTS = ["Segoe UI", "Segoe UI Semibold", "Segoe UI Light", "Calibri", "Calibri Light", "Arial", "Arial Black", "Cambria", "Georgia", "Times New Roman", "Trebuchet MS", "Verdana", "Tahoma", "Consolas", "Courier New", "Comic Sans MS", "Impact", "Garamond", "Palatino Linotype", "Bahnschrift"];
const SIZES_PT = [8, 9, 10, 10.5, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 40, 44, 48, 54, 60, 66, 72, 80, 88, 96];
const SHAPES = [["rect", "sh_rect"], ["roundRect", "sh_roundrect"], ["ellipse", "sh_ellipse"], ["triangle", "sh_triangle"], ["rtTriangle", "sh_rttriangle"], ["diamond", "sh_diamond"],
  ["parallelogram", "sh_parallelogram"], ["trapezoid", "sh_trapezoid"], ["pentagon", "sh_pentagon"], ["hexagon", "sh_hexagon"], ["octagon", "sh_octagon"], ["star5", "sh_star"],
  ["rightArrow", "sh_arrow_r"], ["leftArrow", "sh_arrow_l"], ["upArrow", "sh_arrow_u"], ["downArrow", "sh_arrow_d"], ["leftRightArrow", "sh_arrow_lr"], ["chevron", "sh_chevron"],
  ["homePlate", "sh_homeplate"], ["plus", "sh_plus"], ["heart", "sh_heart"], ["cloud", "sh_cloud"], ["donut", "sh_donut"], ["wedgeRectCallout", "sh_callout"],
  ["snip1Rect", "sh_snip"], ["can", "sh_can"], ["flowChartTerminator", "sh_terminator"], ["line", "sh_line"], ["straightConnector1", "sh_arrowline"]];
const TRANSITIONS = ["none", "fade", "push", "wipe", "cover", "split", "zoom", "dissolve", "circle", "random"];
const ANIMS = ["none", "appear", "fade", "fly", "wipe", "zoom"];
const STD_COLORS = ["#000000", "#404040", "#7F7F7F", "#BFBFBF", "#FFFFFF", "#C00000", "#FF0000", "#FFC000", "#FFFF00", "#92D050", "#00B050", "#00B0F0", "#0070C0", "#002060", "#7030A0"];
const THEME_KEYS = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6"];
const SCHEME_FOR = { lt1: "bg1", dk1: "tx1", lt2: "bg2", dk2: "tx2" };

let CLIP = null;

export function slidesOwnsKey(e) {
  const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
  if (e.key === "F5") return true;
  if (!mod) return false;
  return ["g", "b", "i", "u", "=", "+", "-", "0", "d", "a", "c", "v", "x", "z", "y", "m", "h", "j", "l", "e", "r", "]", "["].includes(k);
}

export class SlidesView {
  constructor(box, tab, hooks = {}) {
    this.box = box; this.tab = tab; this.hooks = hooks;
    this.deck = null; this.cur = 0; this.sel = []; this.slideSel = new Set();
    this.zoom = 0; this.ribbonTab = localStorage.getItem("mn_sl_tab") || "home";
    this.history = []; this.hpos = -1; this.savedPos = 0; this.dirty = false;
    this.editing = null; this.showProps = localStorage.getItem("mn_sl_props") !== "0";
    this.notesH = +localStorage.getItem("mn_sl_notes_h") || 110;
    this.el = document.createElement("div");
    this.el.className = "sl-app";
    this.el.tabIndex = -1;
    box.append(this.el);
    this.el.innerHTML = `<div class="sl-loading"><div class="spinner"></div><span>${esc(t("sl_loading"))}</span></div>`;
    this.thumbQueue = new Set();
    this.refreshThumbSoon = debounce(() => this.refreshThumb(this.cur), 250);
    this.load().catch((e) => { console.error(e); this.el.innerHTML = `<div class="empty-state">${icon("warn", "big")}<h3>${esc(t("sl_open_failed"))}</h3><p>${esc(e.message)}</p></div>`; });
  }

  async load() {
    let deck;
    if (this.tab.path) {
      const res = await fetch(rawUrl(this.tab.path));
      if (!res.ok) throw new Error("HTTP " + res.status);
      const buf = new Uint8Array(await res.arrayBuffer());
      if (/\.odp$/i.test(this.tab.path)) { const { openOdp } = await import("./odp.js"); deck = await openOdp(buf, { lang: I18N.lang }); }
      else deck = await openPptx(buf);
    } else {
      const o = this.tab.newDeck || {};
      deck = await newDeck({ theme: THEMES.find((x) => x.id === o.theme) || THEMES[1], size: o.size || "16:9", lang: I18N.lang, title: o.title || "" });
      if (o.starter && o.starter !== "blank") {
        const { starterSpecs } = await import("./studio.js");
        const { slideFromSpec } = await import("./slides-ai.js");
        const specs = starterSpecs(o.starter,I18N.lang);
        if (specs.length) deck.slides = specs.map(spec => slideFromSpec(deck,spec));
      }
    }
    this.setDeck(deck);

    this.focusSoon();
    if (this.tab.pendingAI) { const job = this.tab.pendingAI; delete this.tab.pendingAI; setTimeout(() => this.ai("generate", typeof job === "object" ? job : undefined), 80); }
  }
  setDeck(deck) {
    this.deck?.destroy?.();
    this.deck = deck; this.cur = 0; this.sel = []; this.slideSel = new Set([0]);
    this.history = []; this.hpos = -1; this.savedPos = 0;
    this.build();
    deck.preloadMedia().then(() => { this.renderAll(); });
    this.snapshot(true);
    this.renderAll();
  }
  text() {
    if (!this.deck) return "";
    return this.deck.slides.map((s, i) => `--- ${t("sl_slide")} ${i + 1}${s.hidden ? " (hidden)" : ""} ---\n` + s.elements.map((e) => elText(e)).filter(Boolean).join("\n") + (s.notes ? `\n[${t("sl_notes")}] ${s.notes}` : "")).join("\n\n");
  }
  get slide() { return this.deck?.slides[this.cur]; }

  build() {
    this.el.innerHTML = `
      <div class="sl-ribbon" role="toolbar"><div class="sl-rtabs" role="tablist"></div><div class="sl-rbody"></div></div>
      <div class="sl-main">
        <div class="sl-side" role="listbox" aria-label="${esc(t("sl_slides"))}" tabindex="0"><div class="sl-thumbs"></div>
          <button class="sl-add" data-c="slide:new">${icon("plus")}<span>${esc(t("sl_new_slide"))}</span></button></div>
        <div class="sl-center">
          <div class="sl-stage" tabindex="0"><div class="sl-canvas-wrap"><div class="sl-canvas"></div><div class="sl-overlay"></div></div></div>
          <div class="sl-notes-split" title="${esc(t("sl_notes"))}"></div>
          <div class="sl-notes"><textarea spellcheck="true" placeholder="${esc(t("sl_notes_ph"))}"></textarea></div>
        </div>
        <div class="sl-props" ${this.showProps ? "" : "hidden"}></div>
      </div>
      <div class="sl-status"><span class="sl-st-slide"></span><span class="sl-st-sel"></span><span class="spacer"></span>
        <button class="sl-st-btn" data-c="view:notes" title="${esc(t("sl_notes"))}">${sic("notes")}</button>
        <button class="sl-st-btn" data-c="view:props" title="${esc(t("sl_format_panel"))}">${sic("panel")}</button>
        <button class="sl-st-btn" data-c="show:start" title="${esc(t("sl_show_start"))} (F5)">${icon("play")}</button>
        <button class="sl-st-btn" data-c="zoom:out" title="${esc(t("sl_zoom_out"))}">${icon("zoomOut")}</button>
        <button class="sl-st-zoom" data-c="zoom:fit" title="${esc(t("sl_zoom_fit"))}">100%</button>
        <button class="sl-st-btn" data-c="zoom:in" title="${esc(t("sl_zoom_in"))}">${icon("zoomIn")}</button></div>`;
    this.$ribbon = $(".sl-ribbon", this.el); this.$thumbs = $(".sl-thumbs", this.el); this.$side = $(".sl-side", this.el);
    this.$stage = $(".sl-stage", this.el); this.$canvas = $(".sl-canvas", this.el); this.$overlay = $(".sl-overlay", this.el);

    this.$stage.addEventListener("pointerdown", () => this.el.classList.remove("props-open"), true);
    this.el.addEventListener("keydown", (e) => { if (e.key === "Escape" && this.el.classList.contains("props-open") && e.target.closest?.(".sl-props")) { e.stopPropagation(); this.el.classList.remove("props-open"); this.refocus(); } }, true);
    this.$wrap = $(".sl-canvas-wrap", this.el); this.$props = $(".sl-props", this.el); this.$notes = $(".sl-notes textarea", this.el);
    $(".sl-notes", this.el).style.height = this.notesH + "px";
    if (localStorage.getItem("mn_sl_notes_hidden") === "1") this.el.classList.add("no-notes");
    this.el.addEventListener("click", (e) => { const b = e.target.closest("[data-c]"); if (b && this.el.contains(b) && !b.disabled) { e.preventDefault(); this.cmd(b.dataset.c, b); } });
    this.el.addEventListener("mousedown", (e) => { if (e.target.closest(".sl-ribbon button, .sl-st-btn, .sl-props button")) e.preventDefault(); });
    this.el.addEventListener("keydown", (e) => this.onKey(e));
    this.el.addEventListener("paste", (e) => this.onPaste(e));
    this.el.addEventListener("copy", (e) => { if (!this.editing && !isField(e.target) && this.sel.length) { e.preventDefault(); this.copy(); } });
    this.el.addEventListener("cut", (e) => { if (!this.editing && !isField(e.target) && this.sel.length) { e.preventDefault(); this.copy(); this.deleteSel(); } });
    this.$stage.addEventListener("pointerdown", (e) => this.onStageDown(e));
    this.$stage.addEventListener("dblclick", (e) => this.onDbl(e));
    this.$stage.addEventListener("contextmenu", (e) => this.onCanvasMenu(e));
    this.$stage.addEventListener("wheel", (e) => { if (e.ctrlKey) { e.preventDefault(); this.setZoom(this.scaleFactor() * (e.deltaY < 0 ? 1.1 : 1 / 1.1)); } }, { passive: false });
    this.$stage.addEventListener("dragover", (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); e.stopPropagation(); } });
    this.$stage.addEventListener("drop", (e) => { const f = [...(e.dataTransfer?.files || [])].find((x) => x.type.startsWith("image/")); if (f) { e.preventDefault(); e.stopPropagation(); this.insertImageFile(f); } });
    this.$notes.addEventListener("input", () => { const s = this.slide; if (!s) return; s.notes = this.$notes.value; this.touch(s); this.pushSoon(); });
    this.$notes.addEventListener("blur", () => this.pushNow());
    this.setupSide(); this.setupNotesSplit();
    this.ro = new ResizeObserver(debounce(() => {
      if (!this.el.isConnected || !this.el.offsetWidth) return;
      this.layoutCanvas(); this.drawOverlay();
      if (this._sideW !== this.$side.clientWidth) { this._sideW = this.$side.clientWidth; if (this.deck) this.renderThumbs(); }
    }, 60));
    this.ro.observe(this.$stage);
    this.renderRibbon();
  }
  destroy() { this.ro?.disconnect(); this.io?.disconnect(); this.stopShow?.(); this.deck?.destroy?.(); }

  setupNotesSplit() {
    const bar = $(".sl-notes-split", this.el), notes = $(".sl-notes", this.el);
    bar.addEventListener("pointerdown", (e) => {
      e.preventDefault(); bar.setPointerCapture(e.pointerId);
      const y0 = e.clientY, h0 = notes.offsetHeight;
      const mv = (ev) => { this.notesH = Math.max(48, Math.min(this.el.offsetHeight * 0.6, h0 - (ev.clientY - y0))); notes.style.height = this.notesH + "px"; };
      const up = () => { bar.removeEventListener("pointermove", mv); localStorage.setItem("mn_sl_notes_h", String(Math.round(this.notesH))); };
      bar.addEventListener("pointermove", mv); bar.addEventListener("pointerup", up, { once: true });
    });
  }

  touch(slide) { if (slide) slide._rev = (slide._rev || 0) + 1; }
  snapshot(initial) {
    const d = this.deck;
    const slides = d.slides.map((s) => {
      if (!s._snap || s._snapRev !== (s._rev || 0)) { const { _snap, _snapRev, ...rest } = s; s._snap = JSON.stringify(rest, (k, v) => (k === "_preview" ? undefined : v)); s._snapRev = s._rev || 0; }
      return s._snap;
    });
    const geo = JSON.stringify({ ls: d._layoutScale || null, l: [...d.layouts, ...d.masters].map((l) => [l.placeholders.map((p) => p.box), (l.decor || []).map((e) => [e.x, e.y, e.w, e.h])]) });
    const state = { slides, cur: this.cur, sel: [...this.sel], masters: JSON.stringify(d.masters.map((m) => ({ theme: m.theme, dirty: !!m._themeDirty }))), size: [d.w, d.h], geo };
    if (this.savedPos > this.hpos) this.savedPos = -1;
    this.history = this.history.slice(0, this.hpos + 1);
    this.history.push(state);
    if (this.history.length > 200) { this.history.shift(); this.savedPos--; }
    this.hpos = this.history.length - 1;
    if (initial) this.savedPos = this.hpos;
    this.updateDirty();
  }
  pushNow() { if (this._pushT) { clearTimeout(this._pushT); this._pushT = null; } this.snapshot(); }
  pushSoon() { this.updateDirty(true); clearTimeout(this._pushT); this._pushT = setTimeout(() => { this._pushT = null; this.snapshot(); }, 600); }
  restore(state) {
    const d = this.deck;
    const activeSlideId = this.slide?.id;
    const prevSnaps = d.slides.map((s) => s._snap);
    const previews = new Map(); for (const s of d.slides) for (const e of s.elements) if (e._preview) previews.set(e.id, e._preview);
    d.slides = state.slides.map((j) => { const s = JSON.parse(j); s._snap = j; s._snapRev = s._rev || 0; for (const e of s.elements) if (previews.has(e.id)) e._preview = previews.get(e.id); return s; });
    JSON.parse(state.masters).forEach((m, i) => { if (d.masters[i]) { d.masters[i].theme = m.theme; d.masters[i]._themeDirty = m.dirty; } });
    [d.w, d.h] = state.size;
    if (state.geo) { const g = JSON.parse(state.geo); d._layoutScale = g.ls; [...d.layouts, ...d.masters].forEach((l, i) => { const [boxes, decor] = g.l[i] || [[], []]; l.placeholders.forEach((p, k) => { p.box = boxes[k] ?? p.box; }); (l.decor || []).forEach((e, k) => { if (decor[k]) [e.x, e.y, e.w, e.h] = decor[k]; }); }); }
    let activeIndex = d.slides.findIndex((s) => s.id === activeSlideId);
    if (d.slides.length !== prevSnaps.length) {
      activeIndex = Math.min(state.cur, d.slides.length - 1);
    } else if (activeIndex >= 0 && prevSnaps[activeIndex] === state.slides[activeIndex]) {
      const diffIdx = state.slides.findIndex((snap, idx) => snap !== prevSnaps[idx]);
      if (diffIdx >= 0) activeIndex = diffIdx;
    }
    this.cur = activeIndex >= 0 ? activeIndex : Math.min(state.cur, d.slides.length - 1); this.sel = state.sel.filter((id) => this.slide?.elements.some((e) => e.id === id));
    this.slideSel = new Set([this.cur]);
    this.editing = null;
    this.renderAll();
  }
  undo() { if (this.editing) this.endEdit(); if (this._pushT) this.pushNow(); if (this.hpos <= 0) return; this.hpos--; this.restore(this.history[this.hpos]); this.updateDirty(); }
  redo() { if (this.editing) this.endEdit(); if (this.hpos >= this.history.length - 1) return; this.hpos++; this.restore(this.history[this.hpos]); this.updateDirty(); }
  updateDirty(pending) {
    const dirty = !!pending || this.hpos !== this.savedPos;
    if (dirty !== this.dirty) { this.dirty = dirty; this.hooks.onChange?.(); }
    this.renderRibbonState();
  }

  change(fn, opts = {}) {
    const r = fn();
    this.touch(this.slide);
    if (opts.all) this.deck.slides.forEach((s) => this.touch(s));
    for (const s of opts.slides || []) this.touch(s);
    this.snapshot();
    if (opts.thumbsAll) this.renderThumbs(); else if (!opts.noThumb) this.refreshThumb(this.cur);
    this.renderCanvas(); this.renderProps(); this.renderRibbonState(); this.updateStatus();
    return r;
  }

  renderAll() { if (!this.deck) return; this.renderThumbs(); this.layoutCanvas(); this.renderCanvas(); this.renderProps(); this.renderRibbon(); this.updateStatus(); this.$notes.value = this.slide?.notes || ""; }
  fitScale() {
    const W = this.$stage.clientWidth - 48, H = this.$stage.clientHeight - 40;
    return Math.max(1e-6, Math.min(W / this.deck.w, H / this.deck.h));
  }
  scaleFactor() { return this.zoom || 1; }
  get scale() { return this.fitScale() * (this.zoom || 1); }
  setZoom(z) { this.zoom = Math.max(0.25, Math.min(4, z)); if (Math.abs(this.zoom - 1) < 0.03) this.zoom = 0; this.layoutCanvas(); this.renderCanvas(); }
  layoutCanvas() {
    if (!this.deck || !this.$stage.clientWidth) return;
    const s = this.scale, W = this.deck.w * s, H = this.deck.h * s;
    this.$wrap.style.width = W + "px"; this.$wrap.style.height = H + "px";
    this.$wrap.style.margin = `${Math.max(20, (this.$stage.clientHeight - H) / 2)}px ${Math.max(24, (this.$stage.clientWidth - W) / 2)}px`;
    const z = $(".sl-st-zoom", this.el); if (z) z.textContent = Math.round((this.zoom || 1) * 100) + "%";
    if (this._lastScale !== s) { this._lastScale = s; this.renderCanvas(); }
  }
  renderCanvas() {
    const slide = this.slide; if (!slide) { this.$canvas.innerHTML = ""; return; }
    if (this.editing) return;
    const s = this.scale;
    const node = renderSlide(this.deck, slide, { scale: s, editing: true, slideNo: this.cur + 1, prompt: (el, ph) => promptFor(el, ph) });
    this.$canvas.replaceChildren(node);
    fitTexts(node);
    this.drawOverlay();
    this.$notes.value !== (slide.notes || "") && document.activeElement !== this.$notes && (this.$notes.value = slide.notes || "");
  }
  elNode(id) { return this.$canvas.querySelector(`.sl-slide > .sl-el[data-id="${CSS.escape(id)}"]`); }
  rerenderEl(el) {
    const old = this.elNode(el.id); if (!old) return this.renderCanvas();
    const n = renderElement(this.deck, this.slide, el, this.scale, { editing: true, slideNo: this.cur + 1, prompt: (e, ph) => promptFor(e, ph) });
    old.replaceWith(n); fitTexts(n.parentElement || this.$canvas);
  }

  renderThumbs() {
    const d = this.deck;
    this.io?.disconnect();
    this.io = new IntersectionObserver((entries) => { for (const en of entries) if (en.isIntersecting) { this.paintThumb(+en.target.dataset.i); this.io.unobserve(en.target); } }, { root: this.$side, rootMargin: "300px" });
    const sw = this.$side.clientWidth || 190, tw = Math.max(40, sw - (sw < 110 ? 18 : sw < 150 ? 38 : 52)), th = tw * d.h / d.w;
    this.$thumbs.innerHTML = d.slides.map((s, i) => `<div class="sl-thumb ${i === this.cur ? "cur" : ""} ${this.slideSel.has(i) ? "sel" : ""} ${s.hidden ? "hid" : ""}" data-i="${i}" role="option" aria-selected="${i === this.cur}" draggable="false">
      <span class="sl-tn">${i + 1}${s.transition && s.transition.type !== "none" ? `<i class="sl-tn-fx" title="${esc(t("sl_has_transition"))}">${sic("transition")}</i>` : ""}${slideAnims(s).length ? `<i class="sl-tn-fx" title="${esc(t("sl_has_anim"))}">${sic("anim")}</i>` : ""}</span>
      <div class="sl-tbox" style="width:${tw}px;height:${th}px"></div>${s.hidden ? `<span class="sl-hid-mark">${sic("hide")}</span>` : ""}</div>`).join("");
    for (const el of this.$thumbs.children) this.io.observe(el);
    this.$thumbs.children[this.cur]?.scrollIntoView({ block: "nearest" });
  }
  paintThumb(i) {
    const box = this.$thumbs.children[i]?.querySelector(".sl-tbox"); const s = this.deck.slides[i];
    if (!box || !s) return;
    const sc = box.clientWidth / this.deck.w;
    const node = renderSlide(this.deck, s, { scale: sc, slideNo: i + 1 });
    box.replaceChildren(node); fitTexts(node);
  }
  refreshThumb(i) { const el = this.$thumbs.children[i]; if (el) { this.paintThumb(i); el.classList.toggle("hid", !!this.deck.slides[i]?.hidden); } }
  markThumbs() { [...this.$thumbs.children].forEach((el, i) => { el.classList.toggle("cur", i === this.cur); el.classList.toggle("sel", this.slideSel.has(i)); el.setAttribute("aria-selected", String(i === this.cur)); }); }

  setupSide() {
    const side = this.$side;
    side.addEventListener("pointerdown", (e) => {
      const th = e.target.closest(".sl-thumb"); if (!th || e.button !== 0) return;
      const i = +th.dataset.i;
      if (e.shiftKey) { const a = Math.min(this.cur, i), b = Math.max(this.cur, i); this.slideSel = new Set(Array.from({ length: b - a + 1 }, (_, k) => a + k)); this.markThumbs(); return; }
      if (e.ctrlKey || e.metaKey) { this.slideSel.has(i) && this.slideSel.size > 1 ? this.slideSel.delete(i) : this.slideSel.add(i); this.markThumbs(); return; }
      if (!this.slideSel.has(i) || this.slideSel.size === 1) this.goto(i);

      const y0 = e.clientY; let dragging = false, target = -1;
      const marker = document.createElement("div"); marker.className = "sl-drop";
      const mv = (ev) => {
        if (!dragging && Math.abs(ev.clientY - y0) < 6) return;
        if (!dragging) { dragging = true; side.classList.add("dragging"); this.$thumbs.append(marker); }
        const items = [...this.$thumbs.querySelectorAll(".sl-thumb")];
        target = items.length;
        for (const it of items) { const r = it.getBoundingClientRect(); if (ev.clientY < r.top + r.height / 2) { target = +it.dataset.i; break; } }
        const ref = items[target]; marker.style.top = (ref ? ref.offsetTop - 4 : items[items.length - 1].offsetTop + items[items.length - 1].offsetHeight + 2) + "px";
        const r = side.getBoundingClientRect(); if (ev.clientY < r.top + 30) side.scrollTop -= 12; else if (ev.clientY > r.bottom - 30) side.scrollTop += 12;
      };
      const up = () => {
        document.removeEventListener("pointermove", mv); marker.remove(); side.classList.remove("dragging");
        if (dragging && target >= 0) this.moveSlides([...this.slideSel].sort((a, b) => a - b), target);
        else if (!dragging && (this.slideSel.size > 1) && !(e.ctrlKey || e.shiftKey)) this.goto(i);
      };
      document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up, { once: true });
    });
    side.addEventListener("contextmenu", (e) => {
      const th = e.target.closest(".sl-thumb"); e.preventDefault();
      if (th && !this.slideSel.has(+th.dataset.i)) this.goto(+th.dataset.i);
      this.slideMenu({ x: e.clientX, y: e.clientY }, !!th);
    });
    side.addEventListener("keydown", (e) => {
      if (isField(e.target)) return;
      const k = e.key, mod = e.ctrlKey || e.metaKey;
      if (k === "ArrowDown" || k === "ArrowRight") { e.preventDefault(); this.goto(Math.min(this.deck.slides.length - 1, this.cur + 1)); }
      else if (k === "ArrowUp" || k === "ArrowLeft") { e.preventDefault(); this.goto(Math.max(0, this.cur - 1)); }
      else if (k === "Delete" || k === "Backspace") { e.preventDefault(); e.stopPropagation(); this.deleteSlides(); }
      else if (mod && k.toLowerCase() === "d") { e.preventDefault(); e.stopPropagation(); this.duplicateSlides(); }
      else if (mod && k.toLowerCase() === "c") { e.preventDefault(); e.stopPropagation(); this.copySlides(); }
      else if (mod && k.toLowerCase() === "x") { e.preventDefault(); e.stopPropagation(); this.copySlides(); this.deleteSlides(); }
      else if (mod && k.toLowerCase() === "v") { e.preventDefault(); e.stopPropagation(); this.pasteSlides(); }
      else if (k === "Home") { e.preventDefault(); this.goto(0); }
      else if (k === "End") { e.preventDefault(); this.goto(this.deck.slides.length - 1); }
    });
  }
  slideMenu(at, onSlide) {
    const s = this.slide, many = this.slideSel.size > 1;
    showMenu([
      { label: t("sl_new_slide"), icon: "plus", kbd: "Ctrl+M", run: () => this.cmd("slide:new") },
      onSlide && { label: t("sl_duplicate_slide"), icon: "copy", kbd: "Ctrl+D", run: () => this.duplicateSlides() },
      onSlide && { label: t("sl_copy"), icon: "copy", kbd: "Ctrl+C", run: () => this.copySlides() },
      CLIP?.slides && { label: t("sl_paste"), icon: "insert", kbd: "Ctrl+V", run: () => this.pasteSlides() },
      onSlide && { label: s?.hidden ? t("sl_unhide_slide") : t("sl_hide_slide"), icon: "eye", run: () => this.toggleHidden() },
      onSlide && { label: t("sl_layout"), icon: "outline", sub: this.layoutItems((p) => this.applyLayout(p)) },
      onSlide && !many && { label: t("sl_move_up"), icon: "up", disabled: this.cur === 0, run: () => this.moveSlides([this.cur], this.cur - 1) },
      onSlide && !many && { label: t("sl_move_down"), icon: "down", disabled: this.cur >= this.deck.slides.length - 1, run: () => this.moveSlides([this.cur], this.cur + 2) },
      onSlide && "-",
      onSlide && { label: many ? t("sl_delete_slides", { n: this.slideSel.size }) : t("sl_delete_slide"), icon: "close", danger: true, disabled: this.deck.slides.length <= 1, run: () => this.deleteSlides() },
    ].filter(Boolean), at);
  }

  goto(i, keepSel) {
    if (!this.deck) return;
    i = Math.max(0, Math.min(this.deck.slides.length - 1, i));
    if (this.editing) this.endEdit();
    this.pushIfPending();
    this.cur = i; if (!keepSel) this.slideSel = new Set([i]);
    this.sel = [];
    this.markThumbs();
    this.$thumbs.children[i]?.scrollIntoView({ block: "nearest" });
    this.$notes.value = this.slide.notes || "";
    this.renderCanvas(); this.renderProps(); this.renderRibbonState(); this.updateStatus();
  }
  pushIfPending() { if (this._pushT) this.pushNow(); }
  addSlide(layoutPath, at = this.cur + 1) {
    const d = this.deck;
    const lp = layoutPath || this.defaultNextLayout();
    const s = makeSlide(d, lp);
    d.slides.splice(at, 0, s);
    this.cur = at; this.slideSel = new Set([at]); this.sel = [];
    this.touch(s); this.snapshot(); this.renderAll();
    return s;
  }
  defaultNextLayout() {
    const l = this.deck.layoutOf(this.slide);
    if (l && (l.type === "title" || l.type === "secHead")) return (this.deck.layouts.find((x) => x.type === "obj") || l).path;
    return l?.path || this.deck.layouts[0]?.path;
  }
  async duplicateSlides() {
    const idx = [...this.slideSel].sort((a, b) => a - b);
    const copies = [];
    for (const i of idx) copies.push(await duplicateSlide(this.deck, this.deck.slides[i]));
    const at = idx[idx.length - 1] + 1;
    this.deck.slides.splice(at, 0, ...copies);
    this.cur = at; this.slideSel = new Set(copies.map((_, k) => at + k)); this.sel = [];
    copies.forEach((c) => this.touch(c)); this.snapshot(); this.renderAll();
  }
  deleteSlides() {
    const d = this.deck;
    const idx = [...this.slideSel].sort((a, b) => b - a);
    if (idx.length >= d.slides.length) { toast(t("sl_last_slide")); return; }
    for (const i of idx) d.slides.splice(i, 1);
    this.cur = Math.min(idx[idx.length - 1], d.slides.length - 1); this.slideSel = new Set([this.cur]); this.sel = [];
    this.snapshot(); this.renderAll();
  }
  moveSlides(idx, target) {
    const d = this.deck;
    const moving = idx.map((i) => d.slides[i]);
    const before = d.slides[target];
    const rest = d.slides.filter((s) => !moving.includes(s));
    let at = before ? rest.indexOf(before) : rest.length;
    if (at < 0) at = rest.length;
    rest.splice(at, 0, ...moving);
    if (rest.every((s, i) => s === d.slides[i])) return;
    d.slides = rest;
    this.cur = d.slides.indexOf(moving[0]); this.slideSel = new Set(moving.map((s) => d.slides.indexOf(s)));
    this.snapshot(); this.renderAll();
  }
  toggleHidden() {
    const idx = [...this.slideSel]; const hide = !this.deck.slides[idx[0]].hidden;
    for (const i of idx) { this.deck.slides[i].hidden = hide || undefined; if (!hide) delete this.deck.slides[i].hidden; this.touch(this.deck.slides[i]); }
    this.snapshot(); this.renderThumbs(); this.renderProps(); this.updateStatus();
  }
  copySlides() {
    const idx = [...this.slideSel].sort((a, b) => a - b);
    CLIP = { slides: idx.map((i) => this.deck.slides[i]), deck: this.deck };
    toast(t("sl_copied_slides", { n: idx.length }), { ms: 1400 });
  }
  async pasteSlides() {
    if (!CLIP?.slides) return;
    if (CLIP.deck !== this.deck) { toast(t("sl_paste_other_deck")); return; }
    const copies = []; for (const s of CLIP.slides) copies.push(await duplicateSlide(this.deck, s));
    const at = this.cur + 1; this.deck.slides.splice(at, 0, ...copies);
    this.cur = at; this.slideSel = new Set(copies.map((_, k) => at + k)); this.sel = [];
    copies.forEach((c) => this.touch(c)); this.snapshot(); this.renderAll();
  }
  layoutItems(run) {
    return this.deck.layouts.map((l) => ({ label: layoutName(l), checked: this.slide?.layout === l.path ? true : undefined, run: () => run(l.path) }));
  }
  applyLayout(path) {
    const d = this.deck, idx = [...this.slideSel];
    for (const i of idx) {
      const s = d.slides[i]; if (s.layout === path) continue;
      s.layout = path;

      const fresh = makeSlide(d, path);
      const used = new Set();
      for (const el of s.elements) {
        if (!el.ph) continue;
        const match = fresh.elements.find((f) => !used.has(f) && (f.ph.type || "body") === (el.ph.type || "body") || (!used.has(f) && f.ph.idx && f.ph.idx === el.ph.idx));
        if (match) { used.add(match); el.ph = { ...match.ph }; continue; }
        if (el.xfrmInherited) { const box = d.boxOf({ ...s, layout: this._prevLayout || s.layout }, el); Object.assign(el, box); el.xfrmInherited = false; }
      }
      for (const f of fresh.elements) if (!used.has(f) && !(f.ph.type === "title" && s.elements.some((e) => e.ph?.type === "title" || e.ph?.type === "ctrTitle"))) { f.nvId = 0; s.elements.push(f); }
      this.touch(s);
    }
    this.snapshot(); this.renderAll();
  }

  selected() { const s = this.slide; return s ? this.sel.map((id) => s.elements.find((e) => e.id === id)).filter(Boolean) : []; }
  select(ids) { this.sel = ids; this.drawOverlay(); this.renderProps(); this.renderRibbonState(); this.updateStatus(); }
  toSlide(e) { const r = this.$wrap.getBoundingClientRect(); return { x: (e.clientX - r.left) / this.scale, y: (e.clientY - r.top) / this.scale }; }
  boxOf(el) { return this.deck.boxOf(this.slide, el); }
  materialize(el) { if (el.xfrmInherited) { Object.assign(el, this.boxOf(el)); el.xfrmInherited = false; } }

  onStageDown(e) {
    if (e.button === 2) { const n = e.target.closest(".sl-slide > .sl-el"); if (n && !this.sel.includes(n.dataset.id)) this.select([n.dataset.id]); return; }
    if (e.button !== 0) return;
    if (this.tool) { this.drawNew(e); return; }
    const handle = e.target.closest("[data-h]");
    if (handle) { e.preventDefault(); this.dragHandle(e, handle.dataset.h); return; }
    const inText = this.editing && e.target.closest("[contenteditable=true]");
    if (inText) return;
    if (this.editing) this.endEdit();
    const node = e.target.closest(".sl-slide > .sl-el");
    if (!node || node.classList.contains("sl-decor")) {
      const ovSel = e.target.closest(".sl-selbox");
      if (ovSel) { this.dragMove(e); return; }
      if (!e.shiftKey && !e.ctrlKey) this.select([]);
      this.marquee(e);
      this.$stage.focus({ preventScroll: true });
      return;
    }
    const id = node.dataset.id;
    if (e.shiftKey || e.ctrlKey || e.metaKey) { this.select(this.sel.includes(id) ? this.sel.filter((x) => x !== id) : [...this.sel, id]); this.$stage.focus({ preventScroll: true }); return; }
    if (!this.sel.includes(id)) this.select([id]);
    this.$stage.focus({ preventScroll: true });
    this.dragMove(e);
  }
  marquee(e) {
    const p0 = this.toSlide(e), base = new Set(this.sel);
    const box = document.createElement("div"); box.className = "sl-marquee"; this.$overlay.append(box);
    const s = this.scale;
    const mv = (ev) => {
      const p = this.toSlide(ev);
      const x = Math.min(p.x, p0.x), y = Math.min(p.y, p0.y), w = Math.abs(p.x - p0.x), h = Math.abs(p.y - p0.y);
      Object.assign(box.style, { left: x * s + "px", top: y * s + "px", width: w * s + "px", height: h * s + "px" });
      if (w * s < 3 && h * s < 3) return;
      const hit = this.slide.elements.filter((el) => { const b = this.boxOf(el); return b.x >= x && b.y >= y && b.x + b.w <= x + w && b.y + b.h <= y + h; }).map((el) => el.id);
      this.sel = [...new Set([...base, ...hit])]; this.drawOverlay(true);
    };
    const up = () => { document.removeEventListener("pointermove", mv); box.remove(); this.select(this.sel); };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up, { once: true });
  }
  snapLines(excl) {
    const d = this.deck, xs = [0, d.w / 2, d.w], ys = [0, d.h / 2, d.h];
    for (const el of this.slide.elements) { if (excl.includes(el.id) || el.hidden) continue; const b = this.boxOf(el); xs.push(b.x, b.x + b.w / 2, b.x + b.w); ys.push(b.y, b.y + b.h / 2, b.y + b.h); }
    return { xs, ys };
  }
  snap(vals, lines, thr) {
    let best = null;
    for (const v of vals) for (const l of lines) { const d = l - v; if (Math.abs(d) <= thr && (!best || Math.abs(d) < Math.abs(best.d))) best = { d, at: l }; }
    return best;
  }
  dragMove(e) {
    const els = this.selected(); if (!els.length) return;
    els.forEach((el) => this.materialize(el));
    const p0 = this.toSlide(e), start = els.map((el) => ({ el, x: el.x, y: el.y }));
    const lines = this.snapLines(els.map((x) => x.id));
    const bb0 = bbox(els.map((el) => this.boxOf(el)));
    let moved = false;
    const mv = (ev) => {
      const p = this.toSlide(ev);
      let dx = p.x - p0.x, dy = p.y - p0.y;
      if (!moved && Math.hypot(dx * this.scale, dy * this.scale) < 3) return;
      moved = true;
      if (ev.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      const guides = [];
      if (!ev.altKey) {
        const thr = 6 / this.scale;
        const sx = this.snap([bb0.x + dx, bb0.x + bb0.w / 2 + dx, bb0.x + bb0.w + dx], lines.xs, thr);
        const sy = this.snap([bb0.y + dy, bb0.y + bb0.h / 2 + dy, bb0.y + bb0.h + dy], lines.ys, thr);
        if (sx) { dx += sx.d; guides.push({ x: sx.at }); }
        if (sy) { dy += sy.d; guides.push({ y: sy.at }); }
      }
      for (const st of start) { st.el.x = Math.round(st.x + dx); st.el.y = Math.round(st.y + dy); const n = this.elNode(st.el.id); if (n) { n.style.left = st.el.x * this.scale + "px"; n.style.top = st.el.y * this.scale + "px"; } }
      this.drawOverlay(true, guides);
    };
    const up = () => {
      document.removeEventListener("pointermove", mv);
      if (moved) { this.touch(this.slide); this.snapshot(); this.refreshThumbSoon(); this.renderProps(); }
      this.drawOverlay();
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up, { once: true });
  }
  dragHandle(e, h) {
    const els = this.selected(); if (!els.length) return;
    els.forEach((el) => this.materialize(el));
    const el = els[0];
    const st = { x: el.x, y: el.y, w: el.w, h: el.h, rot: el.rot || 0 };
    const cx = st.x + st.w / 2, cy = st.y + st.h / 2;
    const multi = els.length > 1, bb0 = bbox(els.map((x) => this.boxOf(x))), starts = els.map((x) => ({ el: x, x: x.x, y: x.y, w: x.w, h: x.h }));
    const lines = this.snapLines(els.map((x) => x.id));
    let changed = false;
    const mv = (ev) => {
      const p = this.toSlide(ev); changed = true;
      if (h === "rot") {
        let a = Math.atan2(p.y - cy, p.x - cx) * 180 / Math.PI + 90;
        a = ((a % 360) + 360) % 360;
        if (ev.shiftKey) a = Math.round(a / 15) * 15; else for (const k of [0, 90, 180, 270, 360]) if (Math.abs(a - k) < 3) a = k;
        el.rot = Math.round(a % 360 * 100) / 100;
        const n = this.elNode(el.id); if (n) n.style.transform = `rotate(${el.rot}deg)${el.flipH ? " scaleX(-1)" : ""}${el.flipV ? " scaleY(-1)" : ""}`;
        this.drawOverlay(true, [], `${Math.round(el.rot)}°`);
        return;
      }
      const hx = h.includes("e") ? 1 : h.includes("w") ? -1 : 0, hy = h.includes("s") ? 1 : h.includes("n") ? -1 : 0;
      const keepAspect = ev.shiftKey !== (el.kind === "pic" && hx && hy ? true : false) && hx && hy;
      if (multi) {
        const ax = hx > 0 ? bb0.x : bb0.x + bb0.w, ay = hy > 0 ? bb0.y : bb0.y + bb0.h;
        let nw = hx ? Math.max(1e4, Math.abs(p.x - ax)) : bb0.w, nh = hy ? Math.max(1e4, Math.abs(p.y - ay)) : bb0.h;
        if (keepAspect) { const k = Math.max(nw / bb0.w, nh / bb0.h); nw = bb0.w * k; nh = bb0.h * k; }
        const kx = nw / bb0.w, ky = nh / bb0.h, nx = hx > 0 ? ax : hx < 0 ? ax - nw : bb0.x, ny = hy > 0 ? ay : hy < 0 ? ay - nh : bb0.y;
        for (const s0 of starts) { s0.el.x = Math.round(nx + (s0.x - bb0.x) * kx); s0.el.y = Math.round(ny + (s0.y - bb0.y) * ky); s0.el.w = Math.round(s0.w * kx); s0.el.h = Math.round(s0.h * ky); }
        this.renderCanvasQuick(); this.drawOverlay(true);
        return;
      }
      const th = st.rot * Math.PI / 180, cos = Math.cos(th), sin = Math.sin(th);
      const ax = -hx * st.w / 2, ay = -hy * st.h / 2;
      const Ax = cx + ax * cos - ay * sin, Ay = cy + ax * sin + ay * cos;
      const dx = p.x - Ax, dy = p.y - Ay;
      const lx = dx * cos + dy * sin, ly = -dx * sin + dy * cos;
      const minS = isLineGeom(el.geom) ? 0 : 2e4;
      let nw = hx ? Math.max(minS, hx * lx) : st.w, nh = hy ? Math.max(minS, hy * ly) : st.h;
      if (keepAspect && st.w && st.h) { const k = Math.max(nw / st.w, nh / st.h); nw = st.w * k; nh = st.h * k; }
      const guides = [];
      if (!st.rot && !ev.altKey) {
        const thr = 6 / this.scale;
        if (hx) { const edge = hx > 0 ? Ax + nw : Ax - nw; const sn = this.snap([edge], lines.xs, thr); if (sn) { nw += hx * sn.d; guides.push({ x: sn.at }); } }
        if (hy) { const edge = hy > 0 ? Ay + nh : Ay - nh; const sn = this.snap([edge], lines.ys, thr); if (sn) { nh += hy * sn.d; guides.push({ y: sn.at }); } }
      }
      const ox = hx * nw / 2, oy = hy * nh / 2;
      const Cx = Ax + ox * cos - oy * sin, Cy = Ay + ox * sin + oy * cos;
      el.w = Math.round(nw); el.h = Math.round(nh); el.x = Math.round(Cx - nw / 2); el.y = Math.round(Cy - nh / 2);
      if (el.text?.autofit?.shape && hx && !hy) {  }
      this.renderCanvasQuick(el); this.drawOverlay(true, guides, `${fmtLen(el.w)} × ${fmtLen(el.h)}`);
    };
    const up = () => {
      document.removeEventListener("pointermove", mv);
      if (changed) { this.touch(this.slide); this.snapshot(); this.renderCanvas(); this.refreshThumbSoon(); this.renderProps(); }
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up, { once: true });
  }
  renderCanvasQuick(el) { if (el) this.rerenderEl(el); else this.renderCanvas(); }

  drawOverlay(live, guides = [], label = "") {
    const ov = this.$overlay; if (!ov || !this.deck) return;
    const s = this.scale, els = this.selected();
    let html = "";
    const one = els.length === 1 && !this.editing?.cell;
    for (const el of els) {
      const b = this.boxOf(el);
      const line = isLineGeom(el.geom);
      html += `<div class="sl-selbox ${this.editing?.id === el.id ? "editing" : ""} ${line ? "line" : ""}" style="left:${b.x * s}px;top:${b.y * s}px;width:${b.w * s}px;height:${b.h * s}px;transform:rotate(${el.rot || 0}deg)">`;
      if (one && !this.editing) {
        const hs = line ? (el.flipH !== el.flipV ? ["ne", "sw"] : ["nw", "se"]) : el.kind === "table" ? ["e", "s", "se"] : ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
        for (const h of hs) html += `<i class="sl-h sl-h-${h}" data-h="${h}"></i>`;
        if (!line && el.kind !== "table") html += `<i class="sl-h sl-h-rot" data-h="rot"></i>`;
      }
      html += "</div>";
    }
    if (els.length > 1) { const bb = bbox(els.map((e) => this.boxOf(e))); html += `<div class="sl-selbox multi" style="left:${bb.x * s}px;top:${bb.y * s}px;width:${bb.w * s}px;height:${bb.h * s}px">${live ? "" : ["nw", "ne", "se", "sw"].map((h) => `<i class="sl-h sl-h-${h}" data-h="${h}"></i>`).join("")}</div>`; }
    for (const g of guides) html += g.x !== undefined ? `<div class="sl-guide v" style="left:${g.x * s}px"></div>` : `<div class="sl-guide h" style="top:${g.y * s}px"></div>`;
    if (label && els[0]) { const b = this.boxOf(els[0]); html += `<div class="sl-sizetip" style="left:${(b.x + b.w / 2) * s}px;top:${(b.y + b.h) * s + 10}px">${esc(label)}</div>`; }
    ov.innerHTML = html;
  }

  setTool(tool) {
    this.tool = tool; this.$stage.classList.toggle("drawing", !!tool);
    if (tool) toast(t("sl_draw_hint"), { ms: 2200 });
    this.renderRibbonState();
  }
  drawNew(e) {
    const tool = this.tool; this.setTool(null);
    const p0 = this.toSlide(e);
    const box = document.createElement("div"); box.className = "sl-marquee draw"; this.$overlay.append(box);
    const s = this.scale; let p1 = p0;
    const mv = (ev) => { p1 = this.toSlide(ev); if (ev.shiftKey && !isLineGeom(tool.geom)) { const d = Math.max(Math.abs(p1.x - p0.x), Math.abs(p1.y - p0.y)); p1 = { x: p0.x + Math.sign(p1.x - p0.x || 1) * d, y: p0.y + Math.sign(p1.y - p0.y || 1) * d }; }
      Object.assign(box.style, { left: Math.min(p0.x, p1.x) * s + "px", top: Math.min(p0.y, p1.y) * s + "px", width: Math.abs(p1.x - p0.x) * s + "px", height: Math.abs(p1.y - p0.y) * s + "px" }); };
    const up = () => {
      document.removeEventListener("pointermove", mv); box.remove();
      let w = Math.abs(p1.x - p0.x), h = Math.abs(p1.y - p0.y);
      const tiny = w * s < 6 && h * s < 6;
      const line = isLineGeom(tool.geom);
      if (tiny) { w = tool.kind === "text" ? this.deck.w * 0.3 : line ? this.deck.w * 0.2 : this.deck.w * 0.15; h = tool.kind === "text" ? 0 : line ? 0 : w; }
      const el = this.newEl(tool, { x: tiny ? p0.x : Math.min(p0.x, p1.x), y: tiny ? p0.y : Math.min(p0.y, p1.y), w, h });
      if (line && !tiny) { el.flipH = p1.x < p0.x || undefined; el.flipV = p1.y < p0.y || undefined; }
      this.addElements([el]);
      if (tool.kind === "text") this.startEdit(el);
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up, { once: true });
  }
  newEl(tool, box) {
    const d = this.deck;
    const base = { id: uid(), x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h), rot: 0 };
    if (tool.kind === "text") return { ...base, kind: "shape", geom: "rect", txBox: true, name: `TextBox ${this.slide.elements.length + 1}`, h: Math.max(box.h, 520000), fill: null,
      text: { paras: [{ runs: [{ t: "" }] }], wrap: "square", autofit: { shape: true } } };
    if (isLineGeom(tool.geom)) return { ...base, kind: "shape", geom: tool.geom, name: `Connector ${this.slide.elements.length + 1}`, line: { w: 19050, color: "scheme:tx1", ...(tool.geom === "straightConnector1" ? { tail: "triangle" } : {}) } };
    return { ...base, kind: "shape", geom: tool.geom, name: `${tool.geom} ${this.slide.elements.length + 1}`, fill: { color: "scheme:accent1" }, line: { none: true },
      text: { paras: [{ algn: "ctr", runs: [{ t: "", color: "scheme:bg1" }] }], anchor: "ctr" } };
  }
  addElements(els, opts = {}) {
    const s = this.slide;
    s.elements.push(...els);
    this.touch(s); this.snapshot();
    this.sel = els.map((e) => e.id);
    this.renderCanvas(); this.refreshThumb(this.cur); this.renderProps(); this.renderRibbonState(); this.updateStatus();
    if (!opts.keepFocus) this.$stage.focus({ preventScroll: true });
  }
  async insertImageFile(file) {
    if (file.size > 30 * 1048576) { toast(t("sl_image_too_big"), { kind: "error" }); return; }
    const bytes = new Uint8Array(await file.arrayBuffer());
    await this.insertImageBytes(bytes, file.type || "image/png");
  }
  async insertImageBytes(bytes, type) {
    if (/svg/.test(type)) { const png = await rasterize(bytes, type); if (png) { bytes = png; type = "image/png"; } }
    if (!/^image\/(png|jpeg|gif|bmp)$/.test(type)) { const png = await rasterize(bytes, type); if (!png) { toast(t("sl_image_failed"), { kind: "error" }); return; } bytes = png; type = "image/png"; }
    const key = this.deck.addMedia(bytes, type);
    const url = this.deck.mediaUrlSync(key);
    const img = await new Promise((res) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = url; });
    if (!img) { toast(t("sl_image_failed"), { kind: "error" }); return; }
    const d = this.deck;
    let w = img.naturalWidth * 9525, h = img.naturalHeight * 9525;
    const k = Math.min(1, (d.w * 0.7) / w, (d.h * 0.7) / h); w *= k; h *= k;
    const ph = this.slide.elements.find((e) => e.ph && (e.ph.type === "pic" || (!e.ph.type && e.ph.idx)) && !plainText(e.text).trim() && this.sel.includes(e.id));
    let box = { x: (d.w - w) / 2, y: (d.h - h) / 2, w, h };
    if (ph) { const b = this.boxOf(ph); const kk = Math.min(b.w / w, b.h / h); box = { w: w * kk, h: h * kk, x: b.x + (b.w - w * kk) / 2, y: b.y + (b.h - h * kk) / 2 }; this.slide.elements = this.slide.elements.filter((e) => e !== ph); }
    this.addElements([{ id: uid(), kind: "pic", name: `Picture ${this.slide.elements.length + 1}`, rot: 0, x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.w), h: Math.round(box.h), pic: { media: key } }]);
  }
  async pickImage() {
    const inp = document.createElement("input"); inp.type = "file"; inp.accept = "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml";
    inp.onchange = () => { if (inp.files[0]) this.insertImageFile(inp.files[0]); };
    inp.click();
  }
  insertTable(rows, cols) {
    const d = this.deck, w = d.w * 0.8, rh = 370840, h = rh * rows;
    const cell = () => ({ text: { paras: [{ runs: [{ t: "" }] }] } });
    const el = { id: uid(), kind: "table", name: `Table ${this.slide.elements.length + 1}`, rot: 0, x: Math.round((d.w - w) / 2), y: Math.round(Math.max(d.h * 0.25, (d.h - h) / 2)), w: Math.round(w), h: Math.round(h),
      table: { cols: Array.from({ length: cols }, () => Math.round(w / cols)), firstRow: true, bandRow: true, style: "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}", rows: Array.from({ length: rows }, () => ({ h: rh, cells: Array.from({ length: cols }, cell) })) } };
    this.addElements([el]);
  }
  insertChart(type) {
    const d = this.deck, w = d.w * 0.6, h = d.h * 0.55;
    const ru = I18N.lang === "ru";
    const chart = type === "pie" || type === "doughnut"
      ? { type, cats: ru ? ["Север", "Юг", "Запад", "Восток"] : ["North", "South", "West", "East"], series: [{ name: ru ? "Продажи" : "Sales", vals: [42, 28, 18, 12] }] }
      : { type, cats: ["Q1", "Q2", "Q3", "Q4"], series: [{ name: ru ? "2025" : "2025", vals: [12, 19, 15, 24] }, { name: "2026", vals: [16, 22, 21, 30] }] };
    this.addElements([{ id: uid(), kind: "chart", name: `Chart ${this.slide.elements.length + 1}`, rot: 0, x: Math.round((d.w - w) / 2), y: Math.round(d.h * 0.28), w: Math.round(w), h: Math.round(h), chart }]);
  }

  deleteSel() {
    const ids = new Set(this.sel); if (!ids.size) return;
    this.slide.elements = this.slide.elements.filter((e) => !ids.has(e.id));
    this.sel = []; this.change(() => {});
  }
  nudge(dx, dy) {
    const els = this.selected(); if (!els.length) return;
    els.forEach((el) => { this.materialize(el); el.x += dx; el.y += dy; });
    this.touch(this.slide); this.pushSoon(); this.renderCanvas(); this.refreshThumbSoon(); this.renderProps();
  }
  zorder(op) {
    const s = this.slide, els = this.selected(); if (!els.length) return;
    let list = s.elements.slice();
    const set = new Set(els);
    if (op === "front") list = [...list.filter((e) => !set.has(e)), ...list.filter((e) => set.has(e))];
    else if (op === "back") list = [...list.filter((e) => set.has(e)), ...list.filter((e) => !set.has(e))];
    else if (op === "forward") { for (let i = list.length - 2; i >= 0; i--) if (set.has(list[i]) && !set.has(list[i + 1])) [list[i], list[i + 1]] = [list[i + 1], list[i]]; }
    else if (op === "backward") { for (let i = 1; i < list.length; i++) if (set.has(list[i]) && !set.has(list[i - 1])) [list[i], list[i - 1]] = [list[i - 1], list[i]]; }
    this.change(() => { s.elements = list; });
  }
  align(op) {
    const els = this.selected(); if (!els.length) return;
    els.forEach((el) => this.materialize(el));
    const d = this.deck;
    const ref = els.length === 1 ? { x: 0, y: 0, w: d.w, h: d.h } : bbox(els.map((e) => this.boxOf(e)));
    this.change(() => {
      if (op === "dist-h" || op === "dist-v") {
        if (els.length < 3) return;
        const k = op === "dist-h" ? ["x", "w"] : ["y", "h"];
        const sorted = els.slice().sort((a, b) => a[k[0]] - b[k[0]]);
        const total = sorted.reduce((a, e) => a + e[k[1]], 0), span = ref[k[1]] - total, gap = span / (sorted.length - 1);
        let pos = ref[k[0]]; for (const e of sorted) { e[k[0]] = Math.round(pos); pos += e[k[1]] + gap; }
        return;
      }
      for (const e of els) {
        if (op === "left") e.x = ref.x; if (op === "center") e.x = Math.round(ref.x + (ref.w - e.w) / 2); if (op === "right") e.x = ref.x + ref.w - e.w;
        if (op === "top") e.y = ref.y; if (op === "middle") e.y = Math.round(ref.y + (ref.h - e.h) / 2); if (op === "bottom") e.y = ref.y + ref.h - e.h;
      }
    });
  }
  group() {
    const els = this.selected(); if (els.length < 2) return;
    const s = this.slide;
    els.forEach((el) => this.materialize(el));
    const bb = bbox(els.map((e) => this.boxOf(e)));
    const order = s.elements.filter((e) => els.includes(e));
    const at = s.elements.indexOf(order[order.length - 1]);
    const g = { id: uid(), kind: "group", name: `Group ${s.elements.length + 1}`, x: bb.x, y: bb.y, w: bb.w, h: bb.h, rot: 0, _ch: { ...bb }, children: order.map((e) => { const c = { ...e }; delete c.anim; return c; }) };
    this.change(() => { const rest = s.elements.filter((e) => !els.includes(e)); rest.splice(Math.min(at - (order.length - 1), rest.length), 0, g); s.elements = rest; this.sel = [g.id]; });
  }
  ungroup() {
    const s = this.slide, gs = this.selected().filter((e) => e.kind === "group"); if (!gs.length) return;
    const newSel = [];
    this.change(() => {
      for (const g of gs) {
        const ch = g._ch || { x: g.x, y: g.y, w: g.w, h: g.h }, kx = ch.w ? g.w / ch.w : 1, ky = ch.h ? g.h / ch.h : 1;
        const gcx = g.x + g.w / 2, gcy = g.y + g.h / 2, th = (g.rot || 0) * Math.PI / 180;
        const kids = g.children.map((c) => {
          const n = { ...c, id: uid() };
          if (n.xfrmInherited) return n;
          let x = g.x + (c.x - ch.x) * kx, y = g.y + (c.y - ch.y) * ky; const w = c.w * kx, h = c.h * ky;
          if (th) { const cx = x + w / 2 - gcx, cy = y + h / 2 - gcy; const rx = cx * Math.cos(th) - cy * Math.sin(th), ry = cx * Math.sin(th) + cy * Math.cos(th); x = gcx + rx - w / 2; y = gcy + ry - h / 2; n.rot = ((c.rot || 0) + g.rot) % 360; }
          Object.assign(n, { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) });
          return n;
        });
        const at = s.elements.indexOf(g);
        s.elements.splice(at, 1, ...kids);
        newSel.push(...kids.map((k) => k.id));
      }
      this.sel = newSel;
    });
  }
  flip(axis) { this.change(() => { for (const el of this.selected()) { this.materialize(el); if (axis === "h") el.flipH = !el.flipH || undefined; else el.flipV = !el.flipV || undefined; } }); }
  rotate90(dir) { this.change(() => { for (const el of this.selected()) { this.materialize(el); el.rot = (((el.rot || 0) + dir * 90) % 360 + 360) % 360; } }); }

  async copy() {
    const els = this.selected(); if (!els.length) return;
    const media = {};
    const walk = async (list) => { for (const e of list) { if (e.pic?.media && !media[e.pic.media]) { const md = await this.deck.mediaData(e.pic.media).catch(() => null); if (md) media[e.pic.media] = md; } if (e.children) await walk(e.children); } };
    CLIP = { els: clone(els.map((e) => ({ ...e, _preview: undefined }))), media: null, deck: this.deck, slide: this.slide, text: els.map(elText).filter(Boolean).join("\n") };
    try { await navigator.clipboard?.writeText(CLIP.text || " "); CLIP.stamp = CLIP.text || " "; } catch {  }
    walk(els).then(() => { if (CLIP && CLIP.els) CLIP.media = media; });
  }
  async onPaste(e) {
    if (this.editing || isField(e.target)) { if (this.editing) this.pasteIntoText(e); return; }
    const items = [...(e.clipboardData?.items || [])];
    const img = items.find((i) => i.type.startsWith("image/"));
    const text = e.clipboardData?.getData("text/plain") || "";
    if (CLIP?.els && (text === CLIP.stamp || (!text && !img))) { e.preventDefault(); this.pasteEls(); return; }
    if (img) { e.preventDefault(); const f = img.getAsFile(); if (f) this.insertImageFile(f); return; }
    if (text.trim()) {
      e.preventDefault();
      const el = this.newEl({ kind: "text" }, { x: this.deck.w * 0.1, y: this.deck.h * 0.3, w: this.deck.w * 0.8, h: 0 });
      el.text.paras = text.replace(/\r\n?/g, "\n").split("\n").slice(0, 200).map((l) => ({ runs: [{ t: l }] }));
      this.addElements([el]);
    }
  }
  pasteEls() {
    const c = CLIP; if (!c?.els) return;
    const sameSlide = c.slide === this.slide && c.deck === this.deck;
    const off = sameSlide ? 182880 : 0;
    const prep = (e) => {
      const n = clone(e); n.id = uid(); delete n.anim;
      if (!n.xfrmInherited && off) { n.x += off; n.y += off; }
      if (c.deck !== this.deck) {
        if (n.pic?.media) { const m = c.media?.[n.pic.media]; if (m) n.pic.media = this.deck.addMedia(m.bytes, m.type); else return null; n._o = { ...(n._o || {}), pic: null }; n._sig = ""; }
        if (n.kind === "chart") { n._chartPath = null; n._o = { ...(n._o || {}), chart: null }; n._sig = ""; }
        if (n._xml && /\sr:(embed|link|id|dm|lo|qs|cs|pict)="/.test(n._xml) && n.kind !== "pic" && n.kind !== "chart") { if (n.kind === "raw" || n.kind === "group" || n.geom === "custom") return null; delete n._xml; delete n._o; }
        if (n.children) { n.children = n.children.map(prep).filter(Boolean); delete n._xml; delete n._o; }
        n._src = null;
        if (n.ph) { const b = c.deck.boxOf(c.slide, e); Object.assign(n, b); n.xfrmInherited = false; delete n.ph; }
      }
      return n;
    };
    const els = c.els.map(prep).filter(Boolean);
    if (els.length < c.els.length) toast(t("sl_paste_partial"));
    if (els.length) this.addElements(els);
    if (sameSlide) CLIP.els = CLIP.els.map((e) => ({ ...e, x: e.x + off, y: e.y + off }));
  }
  duplicateSel() { if (!this.selected().length) return; const save = CLIP; this.copy().then(() => { CLIP.slide = this.slide; this.pasteEls(); CLIP = save || CLIP; }); }

  onDbl(e) {
    if (this.editing) return;
    const node = e.target.closest(".sl-slide > .sl-el"); if (!node) return;
    const el = this.slide.elements.find((x) => x.id === node.dataset.id); if (!el) return;
    if (el.kind === "table") { const td = e.target.closest("td"); if (td) this.startEdit(el, { r: +td.dataset.r, c: +td.dataset.c }, e); return; }
    if (el.kind === "chart") { this.editChartData(el); return; }
    if (el.kind === "pic") { this.select([el.id]); this.showProps || this.cmd("view:props"); toast(t("sl_crop_hint")); return; }
    if (el.kind === "group") { toast(t("sl_group_hint")); return; }
    if (el.kind === "shape" && !isLineGeom(el.geom)) this.startEdit(el, null, e);
  }
  startEdit(el, cell, ev) {
    if (this.editing) this.endEdit();
    if (!cell && !el.text) el.text = { paras: [{ runs: [{ t: "" }] }], anchor: "ctr" };
    this.sel = [el.id];
    this.renderCanvas();
    const node = this.elNode(el.id); if (!node) return;
    const target = cell ? node.querySelector(`td[data-r="${cell.r}"][data-c="${cell.c}"]`) : node.querySelector(".sl-text");
    if (!target) return;
    const textObj = cell ? el.table.rows[cell.r].cells[cell.c].text : el.text;
    const styles = cell ? Array.from({ length: 9 }, () => ({ p: { algn: "l" }, r: { sz: 18, color: el.table.firstRow && cell.r === 0 && el.table.style ? "scheme:lt1" : "scheme:tx1", font: "+mn-lt", b: el.table.firstRow && cell.r === 0 && el.table.style ? true : undefined } })) : this.deck.levelStyles(this.slide, el);
    const s = this.scale;
    const ctx = { s, scale: el.text?.autofit?.scale ? el.text.autofit.scale / 100 : 1, edit: true, font: (f) => this.deck.font(f, this.slide), color: (c) => this.deck.color(c, this.slide) };
    target.classList.remove("sl-prompt");
    target.innerHTML = textHtml(textObj, styles, ctx);
    target.contentEditable = "true"; target.spellcheck = true;
    target.classList.add("sl-editing");
    node.classList.add("editing");
    this.editing = { id: el.id, el, cell, target, styles, ctx, before: JSON.stringify(textObj) };
    target.focus({ preventScroll: true });
    if (ev && document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(ev.clientX, ev.clientY); if (r && target.contains(r.startContainer)) { const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); } else TE.setSel(target, { start: TE.allRange(textObj.paras).end, end: TE.allRange(textObj.paras).end }); }
    else if (ev === "all") TE.setSel(target, TE.allRange(textObj.paras));
    else TE.setSel(target, { start: TE.allRange(textObj.paras).end, end: TE.allRange(textObj.paras).end });
    target.addEventListener("input", this._onInput = () => this.onTextInput());
    target.addEventListener("keydown", this._onTKey = (e2) => this.onTextKey(e2));
    document.addEventListener("selectionchange", this._onSelChange = debounce(() => { if (this.editing) this.renderRibbonState(); }, 80));
    this.drawOverlay(); this.renderRibbonState();
  }
  editText() { const e = this.editing; return e ? (e.cell ? e.el.table.rows[e.cell.r].cells[e.cell.c].text : e.el.text) : null; }
  onTextInput() {
    const e = this.editing; if (!e) return;
    const t0 = this.editText();
    t0.paras = TE.domToParas(e.target, t0.paras);
    if (!e.cell && e.el.text?.autofit?.shape) this.growToFit(e);
    if (e.cell) { const tr = e.target.closest("tr"); const need = tr.offsetHeight / this.scale; const row = e.el.table.rows[e.cell.r]; if (need > row.h + 1000) { row.h = Math.round(need); e.el.h = e.el.table.rows.reduce((a, r) => a + r.h, 0); this.drawOverlay(); } }
    this.touch(this.slide);
    this.pushSoon();
    this.refreshThumbSoon();
  }
  growToFit(e) {
    const node = this.elNode(e.el.id); if (!node) return;
    const need = e.target.scrollHeight / this.scale;
    if (Math.abs(need - e.el.h) > 2000) { this.materialize(e.el); e.el.h = Math.round(need); node.style.height = e.el.h * this.scale + "px"; this.drawOverlay(); }
  }
  onTextKey(ev) {
    const mod = ev.ctrlKey || ev.metaKey, k = ev.key.toLowerCase();
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); const id = this.editing.id; this.endEdit(); this.select([id]); this.$stage.focus({ preventScroll: true }); return; }
    if (ev.key === "Tab" && !mod) { ev.preventDefault(); ev.stopPropagation(); if (this.editing.cell) { this.moveCell(ev.shiftKey ? -1 : 1); return; } this.paraCmd({ lvl: ev.shiftKey ? -1 : 1 }); return; }
    if (mod && ["b", "i", "u"].includes(k)) { ev.preventDefault(); ev.stopPropagation(); this.toggleRun({ b: "b", i: "i", u: "u" }[k]); return; }
    if (mod && (k === "z" || k === "y")) { ev.preventDefault(); ev.stopPropagation(); if (k === "z" && !ev.shiftKey) this.undo(); else this.redo(); return; }
    if (mod && k === "e") { ev.preventDefault(); ev.stopPropagation(); this.paraCmd({ algn: "ctr" }); return; }
    if (mod && k === "l") { ev.preventDefault(); ev.stopPropagation(); this.paraCmd({ algn: "l" }); return; }
    if (mod && k === "r") { ev.preventDefault(); ev.stopPropagation(); this.paraCmd({ algn: "r" }); return; }
    if (mod && k === "j") { ev.preventDefault(); ev.stopPropagation(); this.paraCmd({ algn: "just" }); return; }
    if (mod && (k === "]" || k === "[")) { ev.preventDefault(); ev.stopPropagation(); this.fontStep(k === "]" ? 1 : -1); return; }
    if (mod && k === "a") { ev.preventDefault(); ev.stopPropagation(); TE.setSel(this.editing.target, TE.allRange(this.editText().paras)); return; }
    ev.stopPropagation();
  }
  pasteIntoText(ev) {
    ev.preventDefault();
    const text = ev.clipboardData?.getData("text/plain") || ""; if (!text) return;
    const e = this.editing; const t0 = this.editText();
    t0.paras = TE.domToParas(e.target, t0.paras);
    const sel = TE.getSel(e.target) || { ...TE.allRange(t0.paras), collapsed: false };
    const caret = TE.insertText(t0.paras, sel, text);
    this.rerenderEditing({ start: caret, end: caret });
    this.onTextInput();
  }
  rerenderEditing(sel) {
    const e = this.editing; if (!e) return;
    e.target.innerHTML = textHtml(this.editText(), e.styles, e.ctx);
    e.target.focus({ preventScroll: true });
    TE.setSel(e.target, sel);
  }
  moveCell(dir) {
    const e = this.editing; const t = e.el.table;
    let r = e.cell.r, c = e.cell.c + dir;
    if (c >= t.cols.length) { c = 0; r++; } if (c < 0) { c = t.cols.length - 1; r--; }
    if (r >= t.rows.length) { this.endEdit(); this.change(() => { const rh = t.rows[t.rows.length - 1].h; t.rows.push({ h: rh, cells: t.cols.map(() => ({ text: { paras: [{ runs: [{ t: "" }] }] } })) }); e.el.h += rh; }); r = t.rows.length - 1; c = 0; this.startEdit(e.el, { r, c }); return; }
    if (r < 0) return;
    const el = e.el; this.endEdit(); this.startEdit(el, { r, c }, "all");
  }
  endEdit() {
    const e = this.editing; if (!e) return;
    const t0 = this.editText();
    t0.paras = TE.mergeRuns(TE.domToParas(e.target, t0.paras));
    e.target.removeEventListener("input", this._onInput); e.target.removeEventListener("keydown", this._onTKey);
    document.removeEventListener("selectionchange", this._onSelChange);
    this.editing = null;
    if (JSON.stringify(t0) !== e.before) { this.touch(this.slide); this.pushNow(); } else if (this._pushT) this.pushNow();
    this.renderCanvas(); this.refreshThumb(this.cur); this.renderRibbonState(); this.renderProps();
  }

  runTargets() {
    const e = this.editing;
    if (e) {
      const t0 = this.editText();
      t0.paras = TE.domToParas(e.target, t0.paras);
      let sel = TE.getSel(e.target) || TE.allRange(t0.paras);
      const caret = sel.collapsed ? sel : null;
      if (sel.collapsed) sel = TE.wordRange(t0.paras, sel.start);
      if (sel.collapsed) { this._pendingCaret = caret; return { runs: [], paras: [t0.paras[sel.start.p]].filter(Boolean), sel, caret, empty: true }; }
      return { runs: TE.splitRange(t0.paras, sel), paras: TE.parasInRange(t0.paras, sel), sel, caret };
    }
    const runs = [], paras = [];
    for (const el of this.selected()) eachText(el, (txt) => { for (const p of txt.paras) { paras.push(p); if (!p.runs.length) p.runs.push({ t: "" }); runs.push(...p.runs.filter((r) => !r.br)); } });
    return { runs, paras };
  }
  applyRun(props) {
    const tg = this.runTargets();
    if (tg.empty) {
      for (const p of tg.paras) { if (!p.runs.length) p.runs.push({ t: "" }); TE.setRunProps(p.runs.filter((r) => !r.br && !r.t), props); if ("sz" in props) p.endSz = props.sz; }
    }
    TE.setRunProps(tg.runs, props);
    if ("sz" in props) for (const p of tg.paras) if (!this.editing || !p.runs.some((r) => r.t)) { if (props.sz) p.endSz = props.sz; else delete p.endSz; }
    this.afterTextCmd(tg);
  }
  toggleRun(key) {
    const st = this.formatState();
    this.applyRun({ [key]: !st[key] });
  }
  fontStep(dir) {
    const st = this.formatState(), cur = st.sz || 18;
    const next = dir > 0 ? SIZES_PT.find((v) => v > cur + 0.01) || cur + 4 : [...SIZES_PT].reverse().find((v) => v < cur - 0.01) || Math.max(1, cur - 1);
    if (!this.editing) { for (const el of this.selected()) if (el.text?.autofit?.norm) el.text.autofit = { norm: true }; }
    this.applyRun({ sz: next });
  }
  paraCmd(props) {
    const tg = this.runTargets();
    for (const p of tg.paras) {
      if (props.lvl) { p.lvl = Math.max(0, Math.min(8, (p.lvl || 0) + props.lvl)) || undefined; if (!p.lvl) delete p.lvl; continue; }
      for (const [k, v] of Object.entries(props)) { if (v === undefined) delete p[k]; else p[k] = clone(v); }
      if ("bullet" in props && props.bullet === undefined) { delete p.marL; delete p.indent; }
    }
    this.afterTextCmd(tg);
  }
  afterTextCmd(tg) {
    const e = this.editing;
    if (e) {
      const t0 = this.editText(); TE.mergeRuns(t0.paras);
      this.rerenderEditing(tg.caret ? { start: tg.caret.start, end: tg.caret.end } : tg.sel);
      this.touch(this.slide); this.pushSoon(); this.refreshThumbSoon(); this.renderRibbonState();
      if (!e.cell && e.el.text?.autofit?.shape) this.growToFit(e);
      return;
    }
    if (!this.selected().length) return;
    for (const el of this.selected()) eachText(el, (txt) => TE.mergeRuns(txt.paras));
    this.change(() => {});
  }

  formatState() {
    const e = this.editing;
    let el, para, run;
    if (e) {
      el = e.el;
      const t0 = this.editText();
      const sel = TE.getSel(e.target);
      const paras = TE.domToParas(e.target, t0.paras);
      const pos = sel ? sel.start : { p: 0, o: 0 };
      para = paras[pos.p] || paras[0] || { runs: [] };
      run = TE.propsAt(paras, sel && !sel.collapsed ? { p: pos.p, o: pos.o + 1 } : pos);
    } else {
      el = this.selected().find((x) => x.text || x.kind === "table");
      if (!el) return {};
      const txt = el.text || el.table?.rows[0]?.cells[0]?.text;
      para = txt?.paras?.[0] || { runs: [] };
      run = para.runs.find((r) => !r.br && r.t) || para.runs[0] || {};
    }
    const styles = el.kind === "table" || e?.cell ? null : this.deck.levelStyles(this.slide, el);
    const lv = styles?.[para.lvl || 0] || { p: { algn: "l" }, r: { sz: 18, font: "+mn-lt" } };
    const P = { ...lv.p, ...Object.fromEntries(Object.entries(para).filter(([k, v]) => v !== undefined && k !== "runs")) };
    const R = { ...lv.r, ...Object.fromEntries(Object.entries(run || {}).filter(([, v]) => v !== undefined)) };
    return { b: !!R.b, i: !!R.i, u: !!R.u, s: !!R.s, sz: R.sz, font: this.deck.font(R.font, this.slide), color: R.color, algn: P.algn || "l",
      bullet: P.bullet && P.bullet !== "none" ? (P.bullet.num ? "num" : "bullet") : "none", lnSpc: typeof P.lnSpc === "number" ? P.lnSpc : 100, lvl: para.lvl || 0, base: R.base };
  }

  onKey(e) {
    if (this.showing) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (isField(e.target) || e.target.isContentEditable) {
      if (mod && k === "s") return;
      if (e.target === this.$notes && mod && (k === "z" || k === "y")) return;
      return;
    }
    const inSide = !!e.target.closest(".sl-side");
    if (e.key === "F5") { e.preventDefault(); e.stopPropagation(); this.startShow(e.shiftKey ? this.cur : 0); return; }
    if (mod && k === "z") { e.preventDefault(); e.stopPropagation(); if (e.shiftKey) this.redo(); else this.undo(); return; }
    if (mod && k === "y") { e.preventDefault(); e.stopPropagation(); this.redo(); return; }
    if (mod && k === "m") { e.preventDefault(); e.stopPropagation(); this.addSlide(); return; }
    if (mod && (k === "=" || k === "+")) { e.preventDefault(); e.stopPropagation(); this.setZoom(this.scaleFactor() * 1.15); return; }
    if (mod && k === "-") { e.preventDefault(); e.stopPropagation(); this.setZoom(this.scaleFactor() / 1.15); return; }
    if (mod && k === "0") { e.preventDefault(); e.stopPropagation(); this.setZoom(1); return; }
    if (inSide) return;
    const els = this.selected();
    if (e.key === "PageDown") { e.preventDefault(); this.goto(this.cur + 1); return; }
    if (e.key === "PageUp") { e.preventDefault(); this.goto(this.cur - 1); return; }
    if ((e.key === "Home" || e.key === "End") && !els.length && !mod) { e.preventDefault(); this.goto(e.key === "Home" ? 0 : this.deck.slides.length - 1); return; }
    if (mod && k === "a") { e.preventDefault(); e.stopPropagation(); this.select(this.slide.elements.map((x) => x.id)); return; }
    if (mod && k === "d") { e.preventDefault(); e.stopPropagation(); if (els.length) this.duplicateSel(); else this.duplicateSlides(); return; }
    if (mod && k === "g") { e.preventDefault(); e.stopPropagation(); if (e.shiftKey) this.ungroup(); else this.group(); return; }
    if (mod && ["b", "i", "u"].includes(k) && els.length) { e.preventDefault(); e.stopPropagation(); this.toggleRun(k); return; }
    if (mod && (k === "]" || k === "[") && els.length) { e.preventDefault(); e.stopPropagation(); this.fontStep(k === "]" ? 1 : -1); return; }
    if (mod && k === "e" && els.length) { e.preventDefault(); e.stopPropagation(); this.paraCmd({ algn: "ctr" }); return; }
    if (mod && k === "l" && els.length) { e.preventDefault(); e.stopPropagation(); this.paraCmd({ algn: "l" }); return; }
    if (mod && k === "r" && els.length) { e.preventDefault(); e.stopPropagation(); this.paraCmd({ algn: "r" }); return; }
    if (mod && (k === "c" || k === "x" || k === "v")) return;
    if (!els.length) {
      if (e.key === "ArrowRight" || e.key === "ArrowDown") { e.preventDefault(); this.goto(this.cur + 1); }
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { e.preventDefault(); this.goto(this.cur - 1); }
      else if (e.key === "Tab") { e.preventDefault(); const list = this.slide.elements; if (list.length) this.select([list[e.shiftKey ? list.length - 1 : 0].id]); }
      return;
    }
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); e.stopPropagation(); this.deleteSel(); return; }
    if (e.key === "Escape") { e.preventDefault(); this.select([]); return; }
    if (e.key === "Tab") { e.preventDefault(); const list = this.slide.elements, i = list.indexOf(els[0]); this.select([list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length].id]); return; }
    if (e.key.startsWith("Arrow")) {
      e.preventDefault();
      const step = (e.shiftKey ? 10 : e.altKey ? 0.25 : 1) * (EMU_PER_IN / 72) * (e.ctrlKey ? 0.2 : 1);
      this.nudge(e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0, e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0);
      return;
    }
    if ((e.key === "Enter" || e.key === "F2") && els.length === 1) { e.preventDefault(); const el = els[0]; if (el.kind === "shape" && !isLineGeom(el.geom)) this.startEdit(el, null, "all"); else if (el.kind === "table") this.startEdit(el, { r: 0, c: 0 }, "all"); return; }
    if (els.length === 1 && e.key.length === 1 && !mod && !e.altKey && els[0].kind === "shape" && !isLineGeom(els[0].geom)) {

      e.preventDefault();
      const el = els[0]; const first = el.text?.paras?.[0]?.runs?.find((r) => !r.br) || {};
      const props = Object.fromEntries(Object.entries(first).filter(([k]) => k !== "t"));
      el.text = { ...(el.text || {}), paras: [{ ...(el.text?.paras?.[0] ? Object.fromEntries(Object.entries(el.text.paras[0]).filter(([k]) => k !== "runs")) : {}), runs: [{ ...props, t: e.key }] }] };
      this.touch(this.slide);
      this.startEdit(el);
      this.onTextInput();
    }
  }

  onCanvasMenu(e) {
    if (this.editing && e.target.closest("[contenteditable=true]")) return;
    e.preventDefault();
    const els = this.selected();
    const at = { x: e.clientX, y: e.clientY };
    if (!els.length) {
      showMenu([
        CLIP?.els && { label: t("sl_paste"), icon: "insert", kbd: "Ctrl+V", run: () => this.pasteEls() },
        { label: t("sl_new_slide"), icon: "plus", run: () => this.addSlide() },
        { label: t("sl_layout"), icon: "outline", sub: this.layoutItems((p) => this.applyLayout(p)) },
        { label: t("sl_background"), icon: "palette", run: () => this.backgroundDialog() },
        { label: this.slide.hidden ? t("sl_unhide_slide") : t("sl_hide_slide"), icon: "eye", run: () => this.toggleHidden() },
      ].filter(Boolean), at);
      return;
    }
    const one = els.length === 1 ? els[0] : null;
    showMenu([
      { label: t("sl_cut"), kbd: "Ctrl+X", run: () => { this.copy(); this.deleteSel(); } },
      { label: t("sl_copy"), icon: "copy", kbd: "Ctrl+C", run: () => this.copy() },
      CLIP?.els && { label: t("sl_paste"), icon: "insert", kbd: "Ctrl+V", run: () => this.pasteEls() },
      { label: t("sl_duplicate"), kbd: "Ctrl+D", run: () => this.duplicateSel() },
      "-",
      one?.kind === "shape" && !isLineGeom(one.geom) && { label: t("sl_edit_text"), icon: "fileText", kbd: "F2", run: () => this.startEdit(one, null, "all") },
      one?.kind === "chart" && { label: t("sl_edit_data"), icon: "table", run: () => this.editChartData(one) },
      { label: t("sl_order"), icon: "outline", sub: [
        { label: t("sl_bring_front"), run: () => this.zorder("front") }, { label: t("sl_bring_forward"), run: () => this.zorder("forward") },
        { label: t("sl_send_backward"), run: () => this.zorder("backward") }, { label: t("sl_send_back"), run: () => this.zorder("back") }] },
      els.length > 1 && { label: t("sl_group"), kbd: "Ctrl+G", run: () => this.group() },
      els.some((x) => x.kind === "group") && { label: t("sl_ungroup"), kbd: "Ctrl+Shift+G", run: () => this.ungroup() },
      { label: t("sl_align"), icon: "alignCenter", sub: this.alignItems() },
      { label: t("sl_animation"), icon: "play", sub: ANIMS.map((a) => ({ label: t("sl_anim_" + a), checked: (one?.anim?.type || "none") === a ? true : undefined, run: () => this.setAnim(a) })) },
      "-",
      { label: t("sl_delete"), icon: "close", danger: true, kbd: "Del", run: () => this.deleteSel() },
    ].filter(Boolean), at);
  }
  alignItems() {
    return [["left", "sl_align_left"], ["center", "sl_align_center"], ["right", "sl_align_right"], ["top", "sl_align_top"], ["middle", "sl_align_middle"], ["bottom", "sl_align_bottom"]]
      .map(([op, key]) => ({ label: t(key), run: () => this.align(op) }))
      .concat(["-", { label: t("sl_dist_h"), disabled: this.sel.length < 3, run: () => this.align("dist-h") }, { label: t("sl_dist_v"), disabled: this.sel.length < 3, run: () => this.align("dist-v") }]);
  }

  async cmd(c, btn) {
    const [g, a] = c.split(":");
    const els = this.selected();
    switch (c) {
      case "slide:new": return this.addSlide();
      case "slide:newmenu": return showMenu(this.layoutItems((p) => this.addSlide(p)).map((x) => ({ ...x, checked: undefined })), btn);
      case "slide:layout": return showMenu(this.layoutItems((p) => this.applyLayout(p)), btn);
      case "slide:dup": return this.duplicateSlides();
      case "slide:del": return this.deleteSlides();
      case "slide:hide": return this.toggleHidden();
      case "edit:undo": return this.undo();
      case "edit:redo": return this.redo();
      case "edit:cut": this.copy(); return this.deleteSel();
      case "edit:copy": return this.copy();
      case "edit:paste": return this.pasteEls();
      case "fmt:b": case "fmt:i": case "fmt:u": case "fmt:s": return this.toggleRun(a);
      case "fmt:sup": return this.applyRun({ base: this.formatState().base === "sup" ? undefined : "sup" });
      case "fmt:sub": return this.applyRun({ base: this.formatState().base === "sub" ? undefined : "sub" });
      case "fmt:grow": return this.fontStep(1);
      case "fmt:shrink": return this.fontStep(-1);
      case "fmt:clear": return this.applyRun({ b: undefined, i: undefined, u: undefined, s: undefined, sz: undefined, color: undefined, font: undefined, hl: undefined, base: undefined });
      case "fmt:color": return this.colorMenu(btn, this.formatState().color, (col) => this.applyRun({ color: col }), { auto: true });
      case "fmt:hl": return this.colorMenu(btn, null, (col) => this.applyRun({ hl: col }), { none: true });
      case "para:l": case "para:ctr": case "para:r": case "para:just": return this.paraCmd({ algn: a });
      case "para:bullet": return this.paraCmd({ bullet: this.formatState().bullet === "bullet" ? "none" : { char: "•" } });
      case "para:num": return this.paraCmd({ bullet: this.formatState().bullet === "num" ? "none" : { num: "arabicPeriod", start: 1 } });
      case "para:bulletmenu": return showMenu([["•", "•"], ["–", "–"], ["▪", "▪"], ["➢", "➢"], ["✓", "✓"], ["○", "○"]].map(([ch, l]) => ({ label: l + "  " + t("sl_bullets"), run: () => this.paraCmd({ bullet: { char: ch } }) }))
        .concat(["-", ...[["arabicPeriod", "1. 2. 3."], ["arabicParenR", "1) 2) 3)"], ["alphaLcPeriod", "a. b. c."], ["alphaUcPeriod", "A. B. C."], ["romanUcPeriod", "I. II. III."]].map(([n, l]) => ({ label: l, run: () => this.paraCmd({ bullet: { num: n, start: 1 } }) })),
          "-", { label: t("sl_no_bullets"), run: () => this.paraCmd({ bullet: "none" }) }]), btn);
      case "para:indent": return this.paraCmd({ lvl: 1 });
      case "para:outdent": return this.paraCmd({ lvl: -1 });
      case "para:spacing": return showMenu([80, 90, 100, 115, 150, 200, 250, 300].map((v) => ({ label: (v / 100).toFixed(v % 100 ? (v % 10 ? 2 : 1) : 1), checked: Math.round(this.formatState().lnSpc) === v ? true : undefined, run: () => this.paraCmd({ lnSpc: v }) })), btn);
      case "text:valign": return showMenu([["t", "sl_valign_top"], ["ctr", "sl_valign_middle"], ["b", "sl_valign_bottom"]].map(([v, k]) => ({ label: t(k), run: () => this.change(() => { for (const el of els) if (el.text) el.text.anchor = v; }) })), btn);
      case "ins:text": return this.setTool({ kind: "text" });
      case "ins:shape": return this.shapeMenu(btn);
      case "ins:image": return this.pickImage();
      case "ins:table": return this.tableMenu(btn);
      case "ins:chart": return showMenu([["col", "sl_chart_col"], ["bar", "sl_chart_bar"], ["line", "sl_chart_line"], ["area", "sl_chart_area"], ["pie", "sl_chart_pie"], ["doughnut", "sl_chart_doughnut"]].map(([v, k]) => ({ label: t(k), run: () => this.insertChart(v) })), btn);
      case "ins:line": return this.setTool({ kind: "shape", geom: "line" });
      case "ins:arrow": return this.setTool({ kind: "shape", geom: "straightConnector1" });
      case "ins:slidenum": return this.insertSlideNumber();
      case "arr:menu": return showMenu([
        { header: t("sl_order") }, { label: t("sl_bring_front"), disabled: !els.length, run: () => this.zorder("front") }, { label: t("sl_bring_forward"), disabled: !els.length, run: () => this.zorder("forward") },
        { label: t("sl_send_backward"), disabled: !els.length, run: () => this.zorder("backward") }, { label: t("sl_send_back"), disabled: !els.length, run: () => this.zorder("back") }, "-",
        { label: t("sl_group"), kbd: "Ctrl+G", disabled: els.length < 2, run: () => this.group() }, { label: t("sl_ungroup"), kbd: "Ctrl+Shift+G", disabled: !els.some((x) => x.kind === "group"), run: () => this.ungroup() }, "-",
        { label: t("sl_rotate_right"), disabled: !els.length, run: () => this.rotate90(1) }, { label: t("sl_rotate_left"), disabled: !els.length, run: () => this.rotate90(-1) },
        { label: t("sl_flip_h"), disabled: !els.length, run: () => this.flip("h") }, { label: t("sl_flip_v"), disabled: !els.length, run: () => this.flip("v") }], btn);
      case "arr:align": return showMenu(this.alignItems().map((x) => (typeof x === "object" ? { ...x, disabled: x.disabled || !els.length } : x)), btn);
      case "design:size": return showMenu([["16:9", "sl_size_169"], ["4:3", "sl_size_43"], ["16:10", "sl_size_1610"]].map(([v, k]) => ({ label: t(k), checked: SIZES[v][0] === this.deck.w && SIZES[v][1] === this.deck.h ? true : undefined, run: () => this.setSize(v) })), btn);
      case "design:bg": return this.backgroundDialog();
      case "design:fonts": return this.themeFontsMenu(btn);
      case "design:colors": return this.themeColorsDialog();
      case "tr:all": return this.change(() => { const tr = clone(this.slide.transition || null); for (const s of this.deck.slides) { if (tr) s.transition = clone(tr); else delete s.transition; } }, { all: true, thumbsAll: true });
      case "anim:preview": return this.startShow(this.cur, { once: true });
      case "show:start": return this.startShow(0);
      case "show:current": return this.startShow(this.cur);
      case "show:presenter": localStorage.setItem("mn_sl_presenter", localStorage.getItem("mn_sl_presenter") === "0" ? "1" : "0"); return this.renderRibbon();
      case "show:hidden": return this.toggleHidden();
      case "exp:pptx": return this.exportAs("pptx");
      case "exp:options": return this.exportAs("options");
      case "exp:pdf": return this.exportAs("pdf");
      case "exp:png": return this.exportAs("png");
      case "exp:pngall": return this.exportAs("pngall");
      case "zoom:in": return this.setZoom(this.scaleFactor() * 1.15);
      case "zoom:out": return this.setZoom(this.scaleFactor() / 1.15);
      case "zoom:fit": return this.setZoom(1);
      case "view:props": if (this.el.offsetWidth <= 880) { this.el.classList.toggle("props-open"); this.$props.hidden = false; this.showProps = true; return this.renderProps(); }
        this.showProps = !this.showProps; localStorage.setItem("mn_sl_props", this.showProps ? "1" : "0"); this.$props.hidden = !this.showProps; this.layoutCanvas(); return this.renderProps();
      case "view:notes": { const hidden = this.el.classList.toggle("no-notes"); localStorage.setItem("mn_sl_notes_hidden", hidden ? "1" : "0"); return this.layoutCanvas(); }
      default:
        if (g === "rtab") { this.ribbonTab = a; localStorage.setItem("mn_sl_tab", a); return this.renderRibbon(); }
        if (g === "theme") return this.applyTheme(a);
        if (g === "tr") return this.setTransition(a);
        if (g === "anim") return this.setAnim(a);
        if (g === "ai") return this.ai(a);
        if (g === "studio") return (await import("./studio.js")).studioAction(this,a);
    }
  }
  shapeMenu(btn) {
    const pop = document.createElement("div");
    pop.className = "menu sl-shape-menu";
    pop.innerHTML = `<div class="menu-head">${esc(t("sl_shapes"))}</div><div class="sl-shape-grid">${SHAPES.map(([g, k]) => `<button class="sl-shape-btn" data-g="${g}" title="${esc(t(k))}">${shapeIcon(g)}</button>`).join("")}</div>`;
    popup(pop, btn);
    pop.addEventListener("click", (e) => { const b = e.target.closest("[data-g]"); if (b) { closeMenus(); pop.remove(); this.setTool({ kind: "shape", geom: b.dataset.g }); } });
  }
  tableMenu(btn) {
    const pop = document.createElement("div");
    pop.className = "menu sl-table-menu";
    pop.innerHTML = `<div class="menu-head sl-tm-label">${esc(t("sl_table"))}</div><div class="sl-tgrid">${Array.from({ length: 64 }, (_, i) => `<i data-r="${Math.floor(i / 8) + 1}" data-c="${(i % 8) + 1}"></i>`).join("")}</div>`;
    popup(pop, btn);
    const label = pop.querySelector(".sl-tm-label");
    pop.addEventListener("pointerover", (e) => { const c = e.target.closest("i"); if (!c) return; const r = +c.dataset.r, cc = +c.dataset.c; for (const x of pop.querySelectorAll("i")) x.classList.toggle("on", +x.dataset.r <= r && +x.dataset.c <= cc); label.textContent = `${t("sl_table")}: ${cc} × ${r}`; });
    pop.addEventListener("click", (e) => { const c = e.target.closest("i"); if (c) { pop.remove(); this.insertTable(+c.dataset.r, +c.dataset.c); } });
  }
  colorMenu(btn, current, apply, opts = {}) {
    const th = this.deck.themeOf(this.slide);
    const pop = document.createElement("div");
    pop.className = "menu sl-color-menu";
    const sw = (val, css, title) => `<button class="sl-sw" data-v="${esc(typeof val === "string" ? val : JSON.stringify(val))}" style="background:${css}" title="${esc(title)}"></button>`;
    let html = `<div class="menu-head">${esc(t("sl_theme_colors"))}</div><div class="sl-sw-grid">`;
    for (const k of THEME_KEYS) html += sw("scheme:" + (SCHEME_FOR[k] || k), cssColor("scheme:" + (SCHEME_FOR[k] || k), th), k);
    for (const [mods, name] of [[[{ n: "lumMod", v: 20000 }, { n: "lumOff", v: 80000 }], "80%"], [[{ n: "lumMod", v: 60000 }, { n: "lumOff", v: 40000 }], "40%"], [[{ n: "lumMod", v: 75000 }], "-25%"], [[{ n: "lumMod", v: 50000 }], "-50%"]])
      for (const k of THEME_KEYS) { const v = { c: "scheme:" + (SCHEME_FOR[k] || k), mods }; html += sw(v, cssColor(v, th), `${k} ${name}`); }
    html += `</div><div class="menu-head">${esc(t("sl_std_colors"))}</div><div class="sl-sw-grid std">${STD_COLORS.map((c) => sw(c, c, c)).join("")}</div>`;
    html += `<div class="sl-sw-row">${opts.none ? `<button class="mi" data-none="1"><span class="mi-label">${esc(t("sl_no_color"))}</span></button>` : ""}${opts.auto ? `<button class="mi" data-auto="1"><span class="mi-label">${esc(t("sl_auto_color"))}</span></button>` : ""}
      <label class="mi sl-custom"><span class="mi-label">${esc(t("sl_custom_color"))}</span><input type="color" value="${typeof current === "string" && current.startsWith("#") ? current : "#2F6FEB"}"></label></div>`;
    pop.innerHTML = html;
    popup(pop, btn);
    pop.addEventListener("click", (e) => {
      const b = e.target.closest("[data-v],[data-none],[data-auto]"); if (!b) return;
      pop.remove();
      if (b.dataset.none) apply(null); else if (b.dataset.auto) apply(undefined);
      else { const v = b.dataset.v; apply(v.startsWith("{") ? JSON.parse(v) : v); }
    });
    pop.querySelector("input[type=color]").addEventListener("change", (e) => { pop.remove(); apply(e.target.value.toUpperCase()); });
  }
  insertSlideNumber() {
    const d = this.deck;
    const el = this.newEl({ kind: "text" }, { x: d.w * 0.86, y: d.h * 0.9, w: d.w * 0.1, h: 0 });
    el.text = { paras: [{ algn: "r", runs: [{ t: "‹#›", fld: "slidenum", sz: 12 }] }], autofit: { shape: true } };
    this.addElements([el]);
  }

  applyTheme(id) {
    const th = THEMES.find((x) => x.id === id); if (!th) return;
    this.change(() => {
      for (const m of this.deck.masters) {
        const swap = (m.clrMap.bg1 === "lt1") === !!th.dark;
        const c = { ...th.colors };
        if (swap) { [c.dk1, c.lt1] = [c.lt1, c.dk1]; [c.dk2, c.lt2] = [c.lt2, c.dk2]; }
        m.theme = { ...m.theme, colors: c, fonts: { major: th.fonts[0], minor: th.fonts[1] }, name: th.en };
        m._themeDirty = true;
      }
    }, { all: true, thumbsAll: true });
    this.renderRibbon();
  }
  themeFontsMenu(btn) {
    const pairs = [["Segoe UI Semibold", "Segoe UI"], ["Calibri Light", "Calibri"], ["Georgia", "Calibri"], ["Cambria", "Calibri"], ["Arial Black", "Arial"], ["Trebuchet MS", "Trebuchet MS"], ["Times New Roman", "Arial"], ["Bahnschrift", "Segoe UI"], ["Consolas", "Segoe UI"]];
    const m0 = this.deck.masters[0];
    showMenu(pairs.map(([mj, mn]) => ({ label: `${mj} / ${mn}`, checked: m0?.theme.fonts.major === mj && m0?.theme.fonts.minor === mn ? true : undefined,
      run: () => this.change(() => { for (const m of this.deck.masters) { m.theme = { ...m.theme, fonts: { major: mj, minor: mn } }; m._themeDirty = true; } }, { all: true, thumbsAll: true }) })), btn);
  }
  async themeColorsDialog() {
    const m = this.deck.masters[0]; if (!m) return;
    const keys = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink"];
    const r = await modal({ title: t("sl_theme_colors"), body: `<div class="sl-colors-form">${keys.map((k) => `<label class="sl-cf"><input type="color" data-k="${k}" value="${m.theme.colors[k] || "#000000"}"><span>${esc(t("sl_clr_" + k))}</span></label>`).join("")}</div>`,
      buttons: [{ label: t("cancel"), value: null }, { label: t("ok"), primary: true, value: (b) => Object.fromEntries([...b.querySelectorAll("input")].map((i) => [i.dataset.k, i.value.toUpperCase()])) }] });
    if (!r) return;
    this.change(() => { for (const mm of this.deck.masters) { mm.theme = { ...mm.theme, colors: { ...mm.theme.colors, ...r } }; mm._themeDirty = true; } }, { all: true, thumbsAll: true });
  }
  setSize(key) {
    const [w, h] = SIZES[key]; const d = this.deck;
    if (w === d.w && h === d.h) return;
    const kx = w / d.w, ky = h / d.h, k = Math.min(kx, ky);
    this.change(() => {
      const fit = (el) => { if (el.xfrmInherited) return; const cx = (el.x + el.w / 2) * kx, cy = (el.y + el.h / 2) * ky; el.w = Math.round(el.w * k); el.h = Math.round(el.h * k); el.x = Math.round(cx - el.w / 2); el.y = Math.round(cy - el.h / 2); };
      for (const s of d.slides) s.elements.forEach(fit);
      const stretch = (b) => { b.x = Math.round(b.x * kx); b.w = Math.round(b.w * kx); b.y = Math.round(b.y * ky); b.h = Math.round(b.h * ky); };
      for (const l of [...d.layouts, ...d.masters]) { for (const p of l.placeholders) if (p.box) stretch(p.box); (l.decor || []).forEach(stretch); }
      const ls = d._layoutScale || { kx: 1, ky: 1 }; d._layoutScale = { kx: ls.kx * kx, ky: ls.ky * ky };
      d.w = w; d.h = h; d._sizeChanged = true;
    }, { all: true, thumbsAll: true });
    this.layoutCanvas();
    toast(t("sl_size_note"), { ms: 5000 });
  }
  async backgroundDialog() {
    const s = this.slide, cur = s.bg;
    const th = this.deck.themeOf(s);
    const solid = cur?.color && !cur.ref ? cssColor(cur.color, th) : "#FFFFFF";
    const r = await modal({ title: t("sl_background"), body: `<div class="sl-bg-form">
        <label class="check"><input type="radio" name="bgk" value="inherit" ${!cur ? "checked" : ""}> ${esc(t("sl_bg_layout"))}</label>
        <label class="check"><input type="radio" name="bgk" value="solid" ${cur?.color && !cur.ref ? "checked" : ""}> ${esc(t("sl_bg_solid"))} <input type="color" id="bg-c1" value="${/^#/.test(solid) ? solid : "#FFFFFF"}"></label>
        <label class="check"><input type="radio" name="bgk" value="grad" ${cur?.grad ? "checked" : ""}> ${esc(t("sl_bg_grad"))} <input type="color" id="bg-g1" value="${cur?.grad ? cssColor(cur.grad.stops[0].color, th) : "#2F6FEB"}"> <input type="color" id="bg-g2" value="${cur?.grad ? cssColor(cur.grad.stops[cur.grad.stops.length - 1].color, th) : "#0B1F4D"}"></label>
        <label class="check"><input type="radio" name="bgk" value="image" ${cur?.image ? "checked" : ""}> ${esc(t("sl_bg_image"))} <button class="btn sm" id="bg-pick" type="button">${esc(t("sl_choose"))}</button></label>
        <label class="check"><input type="checkbox" id="bg-all"> ${esc(t("sl_apply_all"))}</label></div>`,
      onOpen: (box) => {
        box.querySelector("#bg-pick").onclick = () => { const inp = document.createElement("input"); inp.type = "file"; inp.accept = "image/*"; inp.onchange = async () => { const f = inp.files[0]; if (!f) return; box._img = { bytes: new Uint8Array(await f.arrayBuffer()), type: f.type }; box.querySelector("input[value=image]").checked = true; }; inp.click(); };
      },
      buttons: [{ label: t("cancel"), value: null }, { label: t("ok"), primary: true, value: (b) => ({ kind: b.querySelector("input[name=bgk]:checked")?.value, c1: b.querySelector("#bg-c1").value, g1: b.querySelector("#bg-g1").value, g2: b.querySelector("#bg-g2").value, all: b.querySelector("#bg-all").checked, img: b._img }) }] });
    if (!r) return;
    let bg;
    if (r.kind === "solid") bg = { color: r.c1.toUpperCase() };
    else if (r.kind === "grad") bg = { grad: { stops: [{ pos: 0, color: r.g1.toUpperCase() }, { pos: 100, color: r.g2.toUpperCase() }], ang: 90 } };
    else if (r.kind === "image") { if (r.img) bg = { image: this.deck.addMedia(r.img.bytes, r.img.type) }; else if (cur?.image) bg = cur; else return; }
    const targets = r.all ? this.deck.slides : [s];
    this.change(() => { for (const sl of targets) { if (bg) sl.bg = clone(bg); else delete sl.bg; this.touch(sl); } }, { thumbsAll: r.all });
  }
  setTransition(type) {
    const s = this.slide;
    this.change(() => { if (type === "none") { if (s.transition?.advTm) s.transition = { type: "none", advTm: s.transition.advTm }; else delete s.transition; } else s.transition = { ...(s.transition || {}), type, dir: ["push", "wipe", "cover"].includes(type) ? s.transition?.dir || "l" : undefined, spd: s.transition?.spd || "med" }; if (s.transition && !s.transition.dir) delete s.transition.dir; }, { thumbsAll: true });
    this.renderRibbon();
  }
  setAnim(type) {
    const els = this.selected(); if (!els.length) { toast(t("sl_select_object")); return; }
    const s = this.slide;
    const maxOrder = Math.max(-1, ...s.elements.map((e) => (e.anim && !e.anim.keep ? e.anim.order ?? 0 : -1)));
    this.change(() => { let o = maxOrder; for (const el of els) { if (type === "none") delete el.anim; else el.anim = { ...(el.anim && !el.anim.keep ? el.anim : { trig: "click", dur: 500, order: ++o }), type, ...(type === "fly" ? { dir: el.anim?.dir || "b" } : {}) }; if (el.anim && type !== "fly") delete el.anim.dir; } renumberAnims(s); }, { thumbsAll: true });
    this.renderRibbon();
  }

  async editChartData(el) {
    const c = el.chart;
    const rows = [["", ...c.series.map(s => s.name)], ...c.cats.map((cat,i) => [cat,...c.series.map(s => s.vals[i] ?? 0)])];
    const r = await modal({ title:t("sl_edit_data"), wide:true,
      body:`<label class="field"><span>${esc(t("sl_chart_title"))}</span><input class="input" id="ch-title" value="${esc(c.title || "")}"></label>
      <label class="field"><span>${esc(t("sl_chart_type"))}</span><select class="input" id="ch-type">${["col","bar","line","area","pie","doughnut"].map(v=>`<option value="${v}" ${c.type===v?"selected":""}>${esc(t("sl_chart_"+v))}</option>`).join("")}</select></label>
      <p class="muted small">${esc(t("sl_chart_grid_hint"))}</p><div class="sl-chart-grid" id="ch-grid"></div>
      <div class="sl-chart-actions"><button type="button" class="btn" id="ch-add-row">${esc(t("sl_chart_add_row"))}</button><button type="button" class="btn" id="ch-add-series">${esc(t("sl_chart_add_series"))}</button></div>`,
      onOpen:box=>{
        const host=box.querySelector("#ch-grid");
        const draw=()=>{host.innerHTML=`<table><tbody>${rows.map((row,i)=>`<tr>${row.map((value,j)=>`<td><input aria-label="${esc(t("sl_chart_cell"))} ${i+1}, ${j+1}" data-row="${i}" data-col="${j}" value="${esc(value)}" ${i>0&&j>0?'inputmode="decimal"':''}>${i===0&&j>0?`<button type="button" data-remove-series="${j}" aria-label="${esc(t("sl_chart_remove_series"))}">×</button>`:""}</td>`).join("")}<td>${i?`<button type="button" data-remove-row="${i}" aria-label="${esc(t("sl_delete"))}">×</button>`:""}</td></tr>`).join("")}</tbody></table>`;};
        host.oninput=e=>{const {row,col}=e.target.dataset;if(row!==undefined){rows[+row][+col]=e.target.value;e.target.setCustomValidity('');}};
        host.onclick=e=>{const b=e.target.closest('[data-remove-row]');if(b&&rows.length>2){rows.splice(+b.dataset.removeRow,1);draw();}const col=e.target.closest('[data-remove-series]');if(col&&rows[0].length>2){rows.forEach(row=>row.splice(+col.dataset.removeSeries,1));draw();}};
        host.onpaste=e=>{if(!e.target.matches('input'))return;const text=e.clipboardData.getData('text/plain');if(!/[\t\n]/.test(text))return;e.preventDefault();const i=+e.target.dataset.row,j=+e.target.dataset.col;
          text.replace(/\r/g,'').replace(/\n+$/,'').split('\n').slice(0,201-i).forEach((line,k)=>{while(rows.length<=i+k)rows.push(Array(rows[0].length).fill(''));line.split('\t').slice(0,21-j).forEach((v,l)=>{rows[i+k][j+l]=v;});});
          const n=Math.max(...rows.map(r=>r.length));rows.forEach(r=>{while(r.length<n)r.push('');});draw();};
        box.querySelector('#ch-add-row').onclick=()=>{if(rows.length<201){rows.push(Array(rows[0].length).fill(''));draw();}};
        box.querySelector('#ch-add-series').onclick=()=>{if(rows[0].length<21){rows.forEach((r,i)=>r.push(i?0:t('sl_chart_add_series')));draw();}};
        draw();
      },
      buttons:[{label:t("cancel"),value:null},{label:t("ok"),primary:true,keepOpen:true,value:box=>{
        for(const input of box.querySelectorAll('#ch-grid input')){
          if(+input.dataset.row>0 && +input.dataset.col>0 && input.value.trim() && !Number.isFinite(Number(input.value.replace(',','.')))){
            input.setCustomValidity(t('sl_chart_bad'));input.reportValidity();input.focus();return;
          }
        }
        return {title:box.querySelector('#ch-title').value,type:box.querySelector('#ch-type').value};
      }}]
    });
    if(!r)return;
    if(rows.slice(1).some(row=>row.slice(1).some(v=>String(v).trim() && !Number.isFinite(Number(String(v).replace(',','.')))))){toast(t('sl_chart_bad'),{kind:'error'});return;}
    const series=rows[0].slice(1).map((name,j)=>({name:String(name),vals:rows.slice(1).map(row=>Number(String(row[j+1]??0).replace(',','.'))||0),...(c.series[j]?.color?{color:c.series[j].color}:{})}));
    this.change(()=>{el.chart={...c,type:r.type,title:r.title || undefined,cats:rows.slice(1).map(row=>String(row[0]??'')),series};delete el.chart.unsupported;});
  }

  async exportAs(kind) {
    const { exportPdf, exportPng, exportPptx, exportOptions } = await import("./export.js");
    try {
      if (kind === "pptx") await exportPptx(this);
      else if (kind === "options") await exportOptions(this);
      else if (kind === "pdf") await exportPdf(this);
      else await exportPng(this, kind === "pngall", this.hooks);
    } catch (e) { console.error(e); toast(t("sl_export_failed") + ": " + e.message, { kind: "error", ms: 8000 }); }
  }
  async startShow(from, opts = {}) {
    if (this.editing) this.endEdit();
    const { Show } = await import("./present.js");
    this.showing = new Show(this, from, { presenter: localStorage.getItem("mn_sl_presenter") !== "0" && !opts.once, once: opts.once, onEnd: (i) => { this.showing = null; if (typeof i === "number" && !opts.once) this.goto(i); this.el.focus({ preventScroll: true }); } });
  }
  async ai(action, job) {
    const mod = await import("./slides-ai.js");
    return mod.runAI(this, action, job);
  }

  async save(path, opts = {}) {
    if (this.editing) this.endEdit();
    this.pushIfPending();
    const savedState = this.history[this.hpos];
    const bytes = await savePptx(this.deck, { ext: (/\.(\w+)$/.exec(path)?.[1] || "pptx").toLowerCase() });
    const q = new URLSearchParams({ path, mtime: String(opts.mtime || 0), force: opts.force ? "1" : "0" });
    const r = await api("save_binary?" + q, new Blob([bytes], { type: "application/vnd.openxmlformats-officedocument.presentationml.presentation" }), { raw: true, type: "application/octet-stream" });
    if (r.ok) { this.savedPos = this.history.indexOf(savedState); this.deck.format = "pptx"; this.deck.isNew = false; this.updateDirty(!!this._pushT); }
    return r;
  }
  async bytes() { if (this.editing) this.endEdit(); return savePptx(this.deck); }

  updateStatus() {
    const st = $(".sl-st-slide", this.el); if (!st || !this.deck) return;
    st.textContent = t("sl_status_slide", { n: this.cur + 1, total: this.deck.slides.length }) + (this.slide?.hidden ? " · " + t("sl_hidden") : "");
    const se = $(".sl-st-sel", this.el); const els = this.selected();
    se.textContent = els.length === 1 ? (els[0].name || t("sl_kind_" + els[0].kind)) : els.length > 1 ? t("sl_n_selected", { n: els.length }) : "";
  }

  renderRibbon() {
    if (!this.$ribbon) return;
    const tabs = [["home", "sl_tab_home"], ["insert", "sl_tab_insert"], ["design", "sl_tab_design"], ["transitions", "sl_tab_transitions"], ["animations", "sl_tab_animations"], ["show", "sl_tab_show"], ["ai", "sl_tab_ai"], ["review", "sl_tab_review"]];
    $(".sl-rtabs", this.el).innerHTML = tabs.map(([id, k]) => `<button class="sl-rtab ${this.ribbonTab === id ? "on" : ""} ${id === "ai" ? "ai" : ""}" role="tab" aria-selected="${this.ribbonTab === id}" data-c="rtab:${id}">${id === "ai" ? icon("sparkle") : ""}${esc(t(k))}</button>`).join("");
    const B = (c, ic, label, extra = "") => `<button class="sl-b" data-c="${c}" title="${esc(label)}" aria-label="${esc(label)}" ${extra}>${ic}</button>`;
    const L = (c, ic, label, extra = "") => `<button class="sl-b wide" data-c="${c}" title="${esc(label)}" ${extra}>${ic}<span>${esc(label)}</span></button>`;
    const D = (c, ic, label) => `<button class="sl-b wide drop" data-c="${c}" title="${esc(label)}">${ic}<span>${esc(label)}</span>${icon("chevron", "small")}</button>`;
    const G = (inner, cls = "") => `<div class="sl-g ${cls}">${inner}</div>`;
    let h = "";
    const tab = this.ribbonTab;
    if (tab === "home") {
      h += G(`<div class="sl-split">${L("slide:new", icon("plus"), t("sl_new_slide"))}<button class="sl-b sl-split-arrow" data-c="slide:newmenu" title="${esc(t("sl_new_slide_layout"))}">${icon("chevron", "small")}</button></div>` + D("slide:layout", sic("layout"), t("sl_layout")));
      h += G(B("edit:undo", icon("undo"), t("undo") + " (Ctrl+Z)") + B("edit:redo", icon("redo"), t("redo") + " (Ctrl+Y)"));
      h += G(`<select class="sl-font" data-f="font" title="${esc(t("sl_font"))}">${SLIDE_FONTS.map((f) => `<option>${esc(f)}</option>`).join("")}</select>`
        + `<input class="sl-size" data-f="size" list="sl-sizes" title="${esc(t("sl_font_size"))}" inputmode="decimal"><datalist id="sl-sizes">${SIZES_PT.map((s) => `<option value="${s}">`).join("")}</datalist>`
        + B("fmt:grow", sic("grow"), t("sl_grow") + " (Ctrl+])") + B("fmt:shrink", sic("shrink"), t("sl_shrink") + " (Ctrl+[)"), "font");
      h += G(B("fmt:b", icon("bold"), t("fmt_bold") + " (Ctrl+B)") + B("fmt:i", icon("italic"), t("fmt_italic") + " (Ctrl+I)") + B("fmt:u", icon("underline"), t("sl_underline") + " (Ctrl+U)") + B("fmt:s", icon("strike"), t("fmt_strike"))
        + B("fmt:sup", icon("sup"), t("sl_superscript")) + B("fmt:sub", icon("sub"), t("sl_subscript"))
        + `<button class="sl-b sl-colorbtn" data-c="fmt:color" title="${esc(t("sl_font_color"))}">${icon("color")}<i class="sl-cbar"></i></button>` + B("fmt:hl", icon("highlight"), t("sl_highlight")) + B("fmt:clear", icon("eraser"), t("sl_clear_fmt")));
      h += G(B("para:bullet", icon("listUl"), t("sl_bullets")) + B("para:num", icon("listOl"), t("sl_numbering")) + B("para:bulletmenu", icon("chevron", "small"), t("sl_bullet_styles"), 'class="sl-b narrow"')
        + B("para:outdent", icon("outdent"), t("sl_outdent") + " (Shift+Tab)") + B("para:indent", icon("indent"), t("sl_indent") + " (Tab)")
        + B("para:l", icon("alignLeft"), t("sl_align_l") + " (Ctrl+L)") + B("para:ctr", icon("alignCenter"), t("sl_align_c") + " (Ctrl+E)") + B("para:r", icon("alignRight"), t("sl_align_r") + " (Ctrl+R)") + B("para:just", icon("alignJustify"), t("sl_align_j") + " (Ctrl+J)")
        + B("para:spacing", sic("spacing"), t("sl_line_spacing")) + B("text:valign", sic("valign"), t("sl_valign")));
      h += G(D("arr:menu", sic("arrange"), t("sl_arrange")) + D("arr:align", icon("alignCenter"), t("sl_align")));
    } else if (tab === "insert") {
      h += G(`<div class="sl-split">${L("slide:new", icon("plus"), t("sl_new_slide"))}<button class="sl-b sl-split-arrow" data-c="slide:newmenu" title="${esc(t("sl_new_slide_layout"))}">${icon("chevron", "small")}</button></div>`);
      h += G(L("ins:text", sic("textbox"), t("sl_textbox")) + D("ins:shape", sic("shapes"), t("sl_shapes")) + L("ins:line", sic("line"), t("sl_line")) + L("ins:arrow", sic("arrow"), t("sl_arrow")));
      h += G(L("ins:image", icon("image"), t("sl_image")) + D("ins:table", icon("table"), t("sl_table")) + D("ins:chart", sic("chart"), t("sl_chart")));
      h += G(L("ins:slidenum", sic("hash"), t("sl_slide_number")));
    } else if (tab === "design") {
      const m0 = this.deck?.masters[0];
      h += G(THEMES.map((th) => `<button class="sl-theme ${m0?.theme.name === th.en ? "on" : ""}" data-c="theme:${th.id}" title="${esc(th[I18N.lang] || th.en)}"><span class="sl-theme-sw" style="background:${th.dark ? th.colors.dk1 : th.colors.lt1};color:${th.dark ? th.colors.lt1 : th.colors.dk1};font-family:'${th.fonts[0]}'">Aa<i style="background:${th.colors.accent1}"></i><i style="background:${th.colors.accent2}"></i><i style="background:${th.colors.accent3}"></i></span><span class="sl-theme-name">${esc(th[I18N.lang] || th.en)}</span></button>`).join(""), "themes");
      h += G(L("studio:themes", icon("palette"), t("sl_theme_gallery")));
      h += G(L("design:colors", icon("palette"), t("sl_theme_colors")) + D("design:fonts", sic("fonts"), t("sl_theme_fonts")) + L("design:bg", sic("bg"), t("sl_background")) + D("design:size", sic("size"), t("sl_slide_size")));
    } else if (tab === "transitions") {
      const tr = this.slide?.transition;
      h += G(TRANSITIONS.map((x) => `<button class="sl-fx ${(tr?.type || "none") === x ? "on" : ""}" data-c="tr:${x}">${sic("tr_" + x)}<span>${esc(t("sl_tr_" + x))}</span></button>`).join(""), "fx");
      h += G(`<label class="sl-lbl">${esc(t("sl_direction"))}<select class="sl-sel" data-f="trdir" ${["push", "wipe", "cover"].includes(tr?.type) ? "" : "disabled"}>${["l", "r", "u", "d"].map((d) => `<option value="${d}" ${tr?.dir === d ? "selected" : ""}>${esc(t("sl_dir_" + d))}</option>`).join("")}</select></label>`
        + `<label class="sl-lbl">${esc(t("sl_speed"))}<select class="sl-sel" data-f="trspd" ${tr && tr.type !== "none" ? "" : "disabled"}>${["slow", "med", "fast"].map((d) => `<option value="${d}" ${(tr?.spd || "med") === d ? "selected" : ""}>${esc(t("sl_spd_" + d))}</option>`).join("")}</select></label>`
        + `<label class="sl-lbl"><input type="checkbox" data-f="tradv" ${tr?.advTm ? "checked" : ""}> ${esc(t("sl_advance_after"))} <input class="sl-num" type="number" min="0" max="600" step="1" data-f="tradvs" value="${tr?.advTm ? Math.round(tr.advTm / 1000) : 5}"> ${esc(t("sl_sec"))}</label>`
        + L("tr:all", sic("all"), t("sl_apply_all")));
    } else if (tab === "animations") {
      const el = this.selected()[0];
      const a = el?.anim && !el.anim.keep ? el.anim : null;
      h += G(ANIMS.map((x) => `<button class="sl-fx ${(a?.type || "none") === x && el ? "on" : ""}" data-c="anim:${x}" ${el ? "" : "disabled"}>${sic("an_" + x)}<span>${esc(t("sl_anim_" + x))}</span></button>`).join(""), "fx");
      h += G(`<label class="sl-lbl">${esc(t("sl_start"))}<select class="sl-sel" data-f="antrig" ${a ? "" : "disabled"}>${["click", "with", "after"].map((d) => `<option value="${d}" ${a?.trig === d ? "selected" : ""}>${esc(t("sl_trig_" + d))}</option>`).join("")}</select></label>`
        + `<label class="sl-lbl">${esc(t("sl_direction"))}<select class="sl-sel" data-f="andir" ${a?.type === "fly" || a?.type === "wipe" ? "" : "disabled"}>${["b", "t", "l", "r"].map((d) => `<option value="${d}" ${a?.dir === d ? "selected" : ""}>${esc(t("sl_from_" + d))}</option>`).join("")}</select></label>`
        + `<label class="sl-lbl">${esc(t("sl_duration"))}<input class="sl-num" type="number" min="0.1" max="20" step="0.1" data-f="andur" value="${a ? (a.dur / 1000).toFixed(1) : "0.5"}" ${a ? "" : "disabled"}> ${esc(t("sl_sec"))}</label>`
        + `<div class="sl-order">${B("anim:earlier", icon("up"), t("sl_move_earlier"), a ? "" : "disabled")}${B("anim:later", icon("down"), t("sl_move_later"), a ? "" : "disabled")}</div>`
        + L("anim:preview", icon("play"), t("sl_preview")));
    } else if (tab === "show") {
      const pres = localStorage.getItem("mn_sl_presenter") !== "0";
      h += G(L("show:start", icon("play"), t("sl_show_start") + " (F5)") + L("show:current", sic("playcur"), t("sl_show_current") + " (Shift+F5)"));
      h += G(`<button class="sl-b wide ${pres ? "on" : ""}" data-c="show:presenter">${sic("presenter")}<span>${esc(t("sl_presenter_view"))}</span></button>` + `<button class="sl-b wide ${this.slide?.hidden ? "on" : ""}" data-c="show:hidden">${sic("hide")}<span>${esc(t("sl_hide_slide"))}</span></button>`);
      h += G(L("exp:pptx", icon("doc"), t("sl_export_pptx")) + L("exp:options", icon("settings"), t("sl_export_options")));
      h += G(L("exp:pdf", icon("pdf"), t("sl_export_pdf")) + L("exp:png", icon("image"), t("sl_export_png")) + L("exp:pngall", icon("image"), t("sl_export_png_all")));
    } else if (tab === "review") {
      h += G(L("studio:audit", icon("check"), t("sl_preflight")) + L("studio:coach", icon("sparkle"), t("sl_ai_review")));
      h += G(L("studio:revise", icon("wand"), t("sl_ai_custom")));
    } else if (tab === "ai") {
      h += G(L("studio:objects", icon("wand"), t("sl_ai_objects")));
      h += G(L("studio:revise", icon("wand"), t("sl_ai_custom")) + L("studio:coach", icon("check"), t("sl_ai_review")));
      h += G(L("ai:generate", icon("sparkle"), t("sl_ai_generate")) + L("ai:fromdoc", icon("doc"), t("sl_ai_from_doc")));
      h += G(L("ai:improve", icon("wand"), t("sl_ai_improve")) + L("ai:shorten", sic("shrink"), t("sl_ai_shorten")) + L("ai:layout", sic("layout"), t("sl_ai_layout")));
      h += G(L("ai:notes", sic("notes"), t("sl_ai_notes")) + L("ai:notesall", sic("notes"), t("sl_ai_notes_all")) + L("ai:translate", icon("translate"), t("sl_ai_translate")));
    }
    $(".sl-rbody", this.el).innerHTML = h;
    this.bindRibbonFields();
    this.renderRibbonState();
  }
  bindRibbonFields() {
    const body = $(".sl-rbody", this.el);
    for (const f of body.querySelectorAll("[data-f]")) {
      f.addEventListener("change", () => this.onField(f.dataset.f, f));
      if (f.dataset.f === "size") f.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); this.onField("size", f); this.refocus(); } });
    }
    body.querySelector("[data-c='anim:earlier']")?.addEventListener("click", (e) => { e.stopPropagation(); this.moveAnim(-1); });
    body.querySelector("[data-c='anim:later']")?.addEventListener("click", (e) => { e.stopPropagation(); this.moveAnim(1); });
  }

  focusSoon(tries = 45) {
    requestAnimationFrame(() => {
      const a = document.activeElement;
      const idle = !a || a === document.body || a === document.documentElement || !a.getClientRects().length;
      if (!idle && a.closest?.(".modal-back")) { if (tries > 0) this.focusSoon(tries - 1); return; }
      if (!idle) return;
      if (!this.el.getClientRects().length) { if (tries > 0) this.focusSoon(tries - 1); return; }
      this.focusStage();
    });
  }

  focusStage() { const a = document.activeElement; if (!this.$stage || this.showing || !this.el.getClientRects().length || (a && this.el.contains(a) && a !== this.el)) return; this.$stage.focus({ preventScroll: true }); }
  refocus() { if (this.editing) this.editing.target.focus({ preventScroll: true }); else this.$stage.focus({ preventScroll: true }); }
  onField(name, f) {
    const s = this.slide;
    if (name === "font") { this.applyRun({ font: f.value }); this.refocus(); return; }
    if (name === "size") { const v = parseFloat(String(f.value).replace(",", ".")); if (v > 0 && v < 4000) { if (!this.editing) for (const el of this.selected()) if (el.text?.autofit?.norm) el.text.autofit = { norm: true }; this.applyRun({ sz: Math.round(v * 2) / 2 }); } this.refocus(); return; }
    if (name === "trdir" || name === "trspd") { this.change(() => { if (s.transition) s.transition[name === "trdir" ? "dir" : "spd"] = f.value; }); return; }
    if (name === "tradv" || name === "tradvs") {
      const on = $(".sl-rbody [data-f=tradv]", this.el).checked, sec = +$(".sl-rbody [data-f=tradvs]", this.el).value || 0;
      this.change(() => { if (on) s.transition = { ...(s.transition || { type: "none" }), advTm: Math.round(sec * 1000) }; else if (s.transition) { delete s.transition.advTm; if (s.transition.type === "none") delete s.transition; } });
      return;
    }
    const el = this.selected()[0];
    if (!el?.anim) return;
    if (name === "antrig") this.change(() => { el.anim.trig = f.value; });
    if (name === "andir") this.change(() => { el.anim.dir = f.value; });
    if (name === "andur") this.change(() => { el.anim.dur = Math.round(Math.max(0.05, +f.value || 0.5) * 1000); });
  }
  moveAnim(dir) {
    const s = this.slide, el = this.selected()[0]; if (!el?.anim) return;
    const list = slideAnims(s).map((x) => x.el);
    const i = list.indexOf(el), j = i + dir; if (j < 0 || j >= list.length) return;
    this.change(() => { [list[i], list[j]] = [list[j], list[i]]; list.forEach((e, k) => { e.anim.order = k; }); });
    this.renderRibbon();
  }
  renderRibbonState() {
    if (!this.deck || !this.$ribbon) return;
    const body = $(".sl-rbody", this.el); if (!body) return;
    const els = this.selected(), hasText = !!this.editing || els.some((e) => e.text || e.kind === "table");
    const st = hasText ? this.formatState() : {};
    const on = (c, v) => body.querySelector(`[data-c="${c}"]`)?.classList.toggle("on", !!v);
    on("fmt:b", st.b); on("fmt:i", st.i); on("fmt:u", st.u); on("fmt:s", st.s); on("fmt:sup", st.base === "sup"); on("fmt:sub", st.base === "sub");
    for (const a of ["l", "ctr", "r", "just"]) on("para:" + a, hasText && st.algn === a);
    on("para:bullet", st.bullet === "bullet"); on("para:num", st.bullet === "num");
    for (const b of body.querySelectorAll('[data-c^="fmt:"],[data-c^="para:"],[data-c="text:valign"]')) b.disabled = !hasText;
    const font = body.querySelector("[data-f=font]");
    if (font) { font.disabled = !hasText; if (st.font && ![...font.options].some((o) => o.value === st.font)) font.insertAdjacentHTML("afterbegin", `<option>${esc(st.font)}</option>`); font.value = st.font || ""; }
    const size = body.querySelector("[data-f=size]"); if (size && document.activeElement !== size) { size.disabled = !hasText; size.value = st.sz ? String(+st.sz.toFixed(1)) : ""; }
    const bar = body.querySelector(".sl-cbar"); if (bar) bar.style.background = st.color ? this.deck.color(st.color, this.slide) : "currentColor";
    const undo = body.querySelector('[data-c="edit:undo"]'); if (undo) undo.disabled = this.hpos <= 0 && !this._pushT;
    const redo = body.querySelector('[data-c="edit:redo"]'); if (redo) redo.disabled = this.hpos >= this.history.length - 1;
    for (const b of body.querySelectorAll('[data-c="arr:menu"],[data-c="arr:align"]')) b.disabled = !els.length;
    for (const b of body.querySelectorAll('[data-c="ins:text"],[data-c="ins:line"],[data-c="ins:arrow"]')) b.classList.toggle("on", !!this.tool && ((this.tool.kind === "text" && b.dataset.c === "ins:text") || (this.tool.geom === "line" && b.dataset.c === "ins:line") || (this.tool.geom === "straightConnector1" && b.dataset.c === "ins:arrow")));
    if (this.ribbonTab === "animations" && this._animSel !== els.map((e) => e.id).join()) { this._animSel = els.map((e) => e.id).join(); this.renderRibbon(); }
  }

  renderProps() {
    const P = this.$props; if (!P || !this.showProps || !this.deck) return;
    if (P.contains(document.activeElement) && document.activeElement.matches("input,select,textarea") && this._propsFor === this.sel.join()) return;
    this._propsFor = this.sel.join();
    const els = this.selected(), el = els.length === 1 ? els[0] : null;
    const u = I18N.lang === "en" ? { k: EMU_PER_IN, n: "in", step: 0.1 } : { k: CM_EMU, n: t("sl_cm"), step: 0.1 };
    const num = (f, v, step = u.step, extra = "") => `<input class="input sl-pn" type="number" step="${step}" data-p="${f}" value="${v}" ${extra}>`;
    const sec = (title, inner) => `<section class="sl-ps"><h4>${esc(title)}</h4>${inner}</section>`;
    let h = "";
    if (!els.length) {
      const s = this.slide;
      h += `<div class="sl-ph">${esc(t("sl_slide"))} ${this.cur + 1}</div>`;
      h += sec(t("sl_layout"), `<button class="btn sm sl-pwide" data-pc="layout">${esc(layoutName(this.deck.layoutOf(s)))} ${icon("chevron", "small")}</button>`);
      h += sec(t("sl_background"), `<div class="sl-bg-prev" style="background:${backgroundCss(this.deck, s)}"></div><button class="btn sm sl-pwide" data-pc="bg">${esc(t("sl_change_bg"))}</button>${s.bg ? `<button class="btn sm ghost sl-pwide" data-pc="bgreset">${esc(t("sl_bg_reset"))}</button>` : ""}`);
      h += sec(t("sl_slide"), `<label class="check"><input type="checkbox" data-p="hidden" ${s.hidden ? "checked" : ""}> ${esc(t("sl_hide_slide"))}</label>`);
      h += `<p class="muted small sl-ptip">${esc(t("sl_props_tip"))}</p>`;
    } else {
      const b = el ? this.boxOf(el) : bbox(els.map((e) => this.boxOf(e)));
      const f = (v) => +(v / u.k).toFixed(2);
      h += `<div class="sl-ph">${esc(el ? (el.name || t("sl_kind_" + el.kind)) : t("sl_n_selected", { n: els.length }))}</div>`;
      h += sec(t("sl_pos_size") + ` (${u.n})`, `<div class="sl-pgrid"><label>X${num("x", f(b.x))}</label><label>Y${num("y", f(b.y))}</label><label>${esc(t("sl_w"))}${num("w", f(b.w))}</label><label>${esc(t("sl_h"))}${num("h", f(b.h))}</label>`
        + (el ? `<label>${esc(t("sl_rotation"))}${num("rot", +(el.rot || 0).toFixed(1), 1)}</label><label class="check sl-lock"><input type="checkbox" data-p="lock" ${this._lock ? "checked" : ""}> ${esc(t("sl_lock_ratio"))}</label>` : "") + `</div>`);
      const shapes = els.filter((e) => e.kind === "shape");
      if (shapes.length) {
        const s0 = shapes[0]; const line = isLineGeom(s0.geom);
        const th = this.deck.themeOf(this.slide);
        const fillCss = s0.fill === null ? "transparent" : s0.fill?.color ? cssColor(s0.fill.color, th) : s0.fill?.grad ? "linear-gradient(90deg,#999,#555)" : "repeating-linear-gradient(45deg,#8883 0 4px,transparent 4px 8px)";
        const lnCss = s0.line?.none ? "transparent" : s0.line?.color ? cssColor(s0.line.color, th) : "repeating-linear-gradient(45deg,#8883 0 4px,transparent 4px 8px)";
        if (!line) h += sec(t("sl_fill"), `<div class="sl-prow"><button class="sl-swatch" data-pc="fill" style="background:${fillCss}" title="${esc(t("sl_fill"))}"></button><button class="btn sm ghost" data-pc="fillnone">${esc(t("sl_no_fill"))}</button><button class="btn sm ghost" data-pc="fillgrad">${esc(t("sl_gradient"))}</button></div>`
          + (!s0.ph && !s0.txBox ? `<label class="sl-pfield">${esc(t("sl_shape"))}<button class="btn sm sl-pwide" data-pc="geom">${shapeIcon(s0.geom || "rect")} ${esc(t((SHAPES.find(([g]) => g === s0.geom) || [0, "sh_rect"])[1]))}</button></label>` : ""));
        h += sec(t("sl_line"), `<div class="sl-prow"><button class="sl-swatch" data-pc="line" style="background:${lnCss}"></button><button class="btn sm ghost" data-pc="linenone">${esc(t("sl_no_line"))}</button></div>`
          + `<div class="sl-pgrid"><label>${esc(t("sl_width_pt"))}${num("lw", +(((s0.line?.w ?? 12700) / EMU_PER_PT)).toFixed(2), 0.25, 'min="0"')}</label>`
          + `<label>${esc(t("sl_dash"))}<select class="input" data-p="dash">${["solid", "dash", "sysDot", "lgDash", "dashDot"].map((d) => `<option value="${d}" ${(s0.line?.dash || "solid") === d ? "selected" : ""}>${esc(t("sl_dash_" + d))}</option>`).join("")}</select></label>`
          + (line ? `<label>${esc(t("sl_arrow_start"))}<select class="input" data-p="head">${["none", "triangle", "stealth", "oval", "arrow"].map((d) => `<option value="${d}" ${(s0.line?.head || "none") === d ? "selected" : ""}>${esc(t("sl_end_" + d))}</option>`).join("")}</select></label><label>${esc(t("sl_arrow_end"))}<select class="input" data-p="tail">${["none", "triangle", "stealth", "oval", "arrow"].map((d) => `<option value="${d}" ${(s0.line?.tail || "none") === d ? "selected" : ""}>${esc(t("sl_end_" + d))}</option>`).join("")}</select></label>` : "") + "</div>");
        if (!line && s0.text) {
          const tx = s0.text;
          h += sec(t("sl_text_box"), `<div class="sl-pgrid"><label>${esc(t("sl_valign"))}<select class="input" data-p="anchor">${[["", "sl_inherit"], ["t", "sl_valign_top"], ["ctr", "sl_valign_middle"], ["b", "sl_valign_bottom"]].map(([v, k]) => `<option value="${v}" ${(tx.anchor || "") === v ? "selected" : ""}>${esc(t(k))}</option>`).join("")}</select></label>`
            + `<label>${esc(t("sl_autofit"))}<select class="input" data-p="autofit">${[["", "sl_fit_none"], ["norm", "sl_fit_shrink"], ["shape", "sl_fit_resize"]].map(([v, k]) => `<option value="${v}" ${(tx.autofit?.norm ? "norm" : tx.autofit?.shape ? "shape" : "") === v ? "selected" : ""}>${esc(t(k))}</option>`).join("")}</select></label>`
            + `<label class="check sl-lock"><input type="checkbox" data-p="wrap" ${tx.wrap === "none" ? "" : "checked"}> ${esc(t("sl_wrap"))}</label></div>`);
        }
      }
      if (el?.kind === "pic") {
        const c = el.pic.crop || { l: 0, t: 0, r: 0, b: 0 };
        h += sec(t("sl_crop"), `<div class="sl-pgrid">${["l", "t", "r", "b"].map((k) => `<label>${esc(t("sl_crop_" + k))} %${num("crop_" + k, +(c[k] || 0).toFixed(1), 1, 'min="0" max="95"')}</label>`).join("")}</div>`
          + `<div class="sl-prow"><button class="btn sm ghost" data-pc="cropreset">${esc(t("sl_reset_crop"))}</button><button class="btn sm ghost" data-pc="picsize">${esc(t("sl_reset_size"))}</button></div>`
          + `<label class="sl-pfield">${esc(t("sl_alt_text"))}<input class="input" data-p="descr" value="${esc(el.descr || "")}"></label>`
          + `<label class="sl-pfield">${esc(t("sl_shape"))}<select class="input" data-p="picgeom">${[["rect", "sh_rect"], ["roundRect", "sh_roundrect"], ["ellipse", "sh_ellipse"]].map(([v, k]) => `<option value="${v}" ${(el.geom || "rect") === v ? "selected" : ""}>${esc(t(k))}</option>`).join("")}</select></label>`);
      }
      if (el?.kind === "table") {
        h += sec(t("sl_table"), `<div class="sl-prow wrap"><button class="btn sm" data-pc="row+">${esc(t("sl_add_row"))}</button><button class="btn sm" data-pc="col+">${esc(t("sl_add_col"))}</button><button class="btn sm ghost" data-pc="row-">${esc(t("sl_del_row"))}</button><button class="btn sm ghost" data-pc="col-">${esc(t("sl_del_col"))}</button></div>`
          + `<label class="check"><input type="checkbox" data-p="firstRow" ${el.table.firstRow ? "checked" : ""}> ${esc(t("sl_header_row"))}</label><label class="check"><input type="checkbox" data-p="bandRow" ${el.table.bandRow ? "checked" : ""}> ${esc(t("sl_banded"))}</label>`
          + (this.editing?.cell ? `<div class="sl-prow"><button class="sl-swatch" data-pc="cellfill" title="${esc(t("sl_cell_fill"))}"></button><span class="muted small">${esc(t("sl_cell_fill"))}</span></div>` : ""));
      }
      if (el?.kind === "chart") h += sec(t("sl_chart"), `<button class="btn sm sl-pwide" data-pc="chartdata">${esc(t("sl_edit_data"))}</button>`);
      if (el?.kind === "raw" || el?.kind === "group") h += `<p class="muted small sl-ptip">${esc(t(el.kind === "group" ? "sl_group_hint" : "sl_raw_hint"))}</p>`;
      if (el) h += sec(t("sl_name"), `<input class="input" data-p="name" value="${esc(el.name || "")}">`);
    }
    P.innerHTML = `<div class="sl-props-in">${h}</div>`;
    for (const inp of P.querySelectorAll("[data-p]")) inp.addEventListener("change", () => this.onProp(inp.dataset.p, inp, u));
    for (const b of P.querySelectorAll("[data-pc]")) b.addEventListener("click", (e) => this.onPropCmd(b.dataset.pc, b));
  }
  onProp(name, inp, u) {
    const els = this.selected(), el = els[0], v = inp.type === "checkbox" ? inp.checked : inp.value;
    const n = parseFloat(String(v).replace(",", "."));
    if (name === "hidden") return this.toggleHidden();
    if (name === "lock") { this._lock = inp.checked; return; }
    this.change(() => {
      if (["x", "y", "w", "h"].includes(name) && Number.isFinite(n)) {
        const emu = Math.round(n * u.k);
        if (els.length === 1) {
          this.materialize(el);
          if (this._lock && (name === "w" || name === "h") && el.w && el.h) { const k = name === "w" ? emu / el.w : emu / el.h; el.w = Math.round(el.w * k); el.h = Math.round(el.h * k); }
          else el[name] = name === "w" || name === "h" ? Math.max(0, emu) : emu;
        } else { const bb = bbox(els.map((e) => this.boxOf(e))); for (const e of els) { this.materialize(e); if (name === "x") e.x += emu - bb.x; if (name === "y") e.y += emu - bb.y; } }
      }
      if (name === "rot" && Number.isFinite(n)) { this.materialize(el); el.rot = ((n % 360) + 360) % 360; }
      if (name === "lw" || name === "dash" || name === "head" || name === "tail") for (const e of els.filter((x) => x.kind === "shape")) {
        const line = { ...(e.line && !e.line.none ? e.line : { color: e.line?.color || "scheme:tx1" }) };
        if (name === "lw") line.w = Math.round(Math.max(0, n || 0) * EMU_PER_PT);
        else if (v === "none" || v === "solid") delete line[name]; else line[name] = v;
        if (!line.color) line.color = "scheme:tx1";
        e.line = line;
      }
      if (name === "anchor") for (const e of els) if (e.text) { if (v) e.text.anchor = v; else delete e.text.anchor; }
      if (name === "autofit") for (const e of els) if (e.text) { if (v === "norm") e.text.autofit = { norm: true }; else if (v === "shape") e.text.autofit = { shape: true }; else e.text.autofit = undefined; if (!e.text.autofit) delete e.text.autofit; }
      if (name === "wrap") for (const e of els) if (e.text) { if (v) delete e.text.wrap; else e.text.wrap = "none"; }
      if (name.startsWith("crop_") && el.pic) { el.pic.crop = { l: 0, t: 0, r: 0, b: 0, ...(el.pic.crop || {}), [name.slice(5)]: Math.max(0, Math.min(95, n || 0)) }; const c = el.pic.crop; if (c.l + c.r > 98) c[name.slice(5)] = 98 - (name.endsWith("l") ? c.r : c.l); if (c.t + c.b > 98) c[name.slice(5)] = 98 - (name.endsWith("t") ? c.b : c.t); if (!c.l && !c.t && !c.r && !c.b) delete el.pic.crop; }
      if (name === "descr") { if (v) el.descr = v; else delete el.descr; }
      if (name === "picgeom") { if (v === "rect") delete el.geom; else el.geom = v; }
      if (name === "firstRow" || name === "bandRow") el.table[name] = !!v;
      if (name === "name") el.name = v;
    });
  }
  async onPropCmd(c, btn) {
    const els = this.selected(), el = els[0];
    if (c === "layout") return this.cmd("slide:layout", btn);
    if (c === "bg") return this.backgroundDialog();
    if (c === "bgreset") return this.change(() => { delete this.slide.bg; });
    if (c === "fill") return this.colorMenu(btn, el?.fill?.color, (col) => this.change(() => { for (const e of els) if (e.kind === "shape") e.fill = col === null ? null : col === undefined ? undefined : { color: col }; }), { none: true });
    if (c === "fillnone") return this.change(() => { for (const e of els) if (e.kind === "shape") e.fill = null; });
    if (c === "fillgrad") return this.change(() => { for (const e of els) if (e.kind === "shape") e.fill = { grad: { stops: [{ pos: 0, color: "scheme:accent1" }, { pos: 100, color: { c: "scheme:accent1", mods: [{ n: "lumMod", v: 50000 }] } }], ang: 90 } }; });
    if (c === "line") return this.colorMenu(btn, el?.line?.color, (col) => this.change(() => { for (const e of els) if (e.kind === "shape") e.line = col === null ? { none: true } : { ...(e.line && !e.line.none ? e.line : {}), color: col || "scheme:tx1" }; }), { none: true });
    if (c === "linenone") return this.change(() => { for (const e of els) if (e.kind === "shape") e.line = { none: true }; });
    if (c === "geom") { const pop = document.createElement("div"); pop.className = "menu sl-shape-menu"; pop.innerHTML = `<div class="sl-shape-grid">${SHAPES.filter(([g]) => !isLineGeom(g)).map(([g, k]) => `<button class="sl-shape-btn" data-g="${g}" title="${esc(t(k))}">${shapeIcon(g)}</button>`).join("")}</div>`; popup(pop, btn); pop.addEventListener("click", (e) => { const b = e.target.closest("[data-g]"); if (b) { pop.remove(); this.change(() => { for (const x of els) if (x.kind === "shape" && !isLineGeom(x.geom)) x.geom = b.dataset.g; }); } }); return; }
    if (c === "cropreset") return this.change(() => { delete el.pic.crop; });
    if (c === "picsize") { const url = await this.deck.mediaUrl(el.pic.media); const img = await new Promise((r) => { const i = new Image(); i.onload = () => r(i); i.onerror = () => r(null); i.src = url; }); if (img) this.change(() => { const k = Math.min(1, this.deck.w * 0.9 / (img.naturalWidth * 9525), this.deck.h * 0.9 / (img.naturalHeight * 9525)); el.w = Math.round(img.naturalWidth * 9525 * k); el.h = Math.round(img.naturalHeight * 9525 * k); delete el.pic.crop; }); return; }
    if (c === "chartdata") return this.editChartData(el);
    if (c === "cellfill" && this.editing?.cell) { const cell = this.editing; return this.colorMenu(btn, null, (col) => { const cc = cell.el.table.rows[cell.cell.r].cells[cell.cell.c]; this.endEdit(); this.change(() => { if (col === undefined) delete cc.fill; else cc.fill = col === null ? null : { color: col }; }); }, { none: true, auto: true }); }
    if (/^(row|col)[+-]$/.test(c)) {
      const tb = el.table, cell = this.editing?.cell || { r: tb.rows.length - 1, c: tb.cols.length - 1 };
      if (this.editing) this.endEdit();
      const blank = () => ({ text: { paras: [{ runs: [{ t: "" }] }] } });
      this.change(() => {
        if (c === "row+") { const rh = tb.rows[cell.r]?.h || 370840; tb.rows.splice(cell.r + 1, 0, { h: rh, cells: tb.cols.map(blank) }); el.h += rh; }
        if (c === "row-" && tb.rows.length > 1) { el.h -= tb.rows[cell.r].h; tb.rows.splice(cell.r, 1); }
        if (c === "col+") { const w = Math.round(el.w / (tb.cols.length + 1)); tb.cols.splice(cell.c + 1, 0, w); const k = el.w / tb.cols.reduce((a, b) => a + b, 0); tb.cols = tb.cols.map((x) => Math.round(x * k)); for (const r of tb.rows) r.cells.splice(cell.c + 1, 0, blank()); }
        if (c === "col-" && tb.cols.length > 1) { tb.cols.splice(cell.c, 1); const k = el.w / tb.cols.reduce((a, b) => a + b, 0); tb.cols = tb.cols.map((x) => Math.round(x * k)); for (const r of tb.rows) r.cells.splice(cell.c, 1); }
        for (const r of tb.rows) for (const cc of r.cells) { delete cc.gridSpan; delete cc.hMerge; }
      });
    }
  }
}

export async function newDeckDialog() {
  const { STARTERS } = await import("./studio.js");
  let theme = localStorage.getItem("mn_sl_theme") || "clean";
  const r = await modal({ title: t("sl_new_title"), wide: true, body: `<div class="sl-new">
      <div class="sl-new-label">${esc(t("sl_new_theme"))}</div>
      <div class="sl-new-themes">${THEMES.map((th) => `<button type="button" class="sl-new-th ${th.id === theme ? "on" : ""}" data-th="${th.id}">
        <span class="sl-new-prev" style="background:${th.dark ? th.colors.dk1 : th.colors.lt1};color:${th.dark ? th.colors.lt1 : th.colors.dk1}">
          <b style="font-family:'${th.fonts[0]}'">${esc(th[I18N.lang] || th.en)}</b><i style="background:${th.colors.accent1}"></i><em style="background:${th.colors.accent2}"></em></span>
        <span class="sl-new-name">${esc(th[I18N.lang] || th.en)}</span></button>`).join("")}</div>
      <div class="sl-new-label">${esc(t("sl_starter"))}</div><div class="sl-starter-grid">${STARTERS.map((item,i)=>`<label class="sl-starter"><input type="radio" name="sl-starter" value="${item.id}" ${i===0?"checked":""}><span><strong>${esc(item[I18N.lang] || item.en)}</strong><small>${esc(item["description_"+I18N.lang] || item.description_en)}</small></span></label>`).join("")}</div>
      <label class="field sl-new-size"><span>${esc(t("sl_new_size"))}</span><select class="input" id="sl-new-size"><option value="16:9">${esc(t("sl_size_169"))}</option><option value="4:3">${esc(t("sl_size_43"))}</option><option value="16:10">${esc(t("sl_size_1610"))}</option></select></label></div>`,
    onOpen: (box) => box.querySelector(".sl-new-themes").addEventListener("click", (e) => { const b = e.target.closest("[data-th]"); if (!b) return; theme = b.dataset.th; box.querySelectorAll(".sl-new-th").forEach((x) => x.classList.toggle("on", x === b)); }),
    buttons: [{ label: t("cancel"), value: null }, { label: t("sl_new_ai"), value: (b) => ({ theme, size: b.querySelector("#sl-new-size").value, ai: "dialog" }) },
      { label: t("sl_new_blank"), primary: true, value: (b) => ({ theme, size: b.querySelector("#sl-new-size").value, starter:b.querySelector('[name="sl-starter"]:checked').value }) }] });
  if (r) localStorage.setItem("mn_sl_theme", r.theme);
  return r;
}

export function elText(e) {
  if (e.kind === "table") return e.table.rows.map((r) => r.cells.map((c) => plainText(c.text)).join(" | ")).join("\n");
  if (e.kind === "group") return (e.children || []).map(elText).filter(Boolean).join("\n");
  if (e.kind === "chart") return `[chart: ${e.chart.title || e.chart.type}] ` + e.chart.cats.join(", ");
  return e.text ? plainText(e.text) : "";
}
function eachText(el, fn) {
  if (el.text) fn(el.text);
  else if (el.kind === "shape" && !isLineGeom(el.geom)) { el.text = { paras: [{ runs: [{ t: "" }] }] }; fn(el.text); }
  if (el.kind === "table") for (const r of el.table.rows) for (const c of r.cells) fn(c.text);
  if (el.kind === "group") for (const c of el.children || []) eachText(c, fn);
}
export function bbox(boxes) {
  const x = Math.min(...boxes.map((b) => b.x)), y = Math.min(...boxes.map((b) => b.y));
  return { x, y, w: Math.max(...boxes.map((b) => b.x + b.w)) - x, h: Math.max(...boxes.map((b) => b.y + b.h)) - y };
}
function isField(t) { return /^(INPUT|TEXTAREA|SELECT)$/.test(t?.tagName || ""); }
function fmtLen(emu) { return I18N.lang === "en" ? (emu / EMU_PER_IN).toFixed(2) + " in" : (emu / CM_EMU).toFixed(2) + " " + t("sl_cm"); }
export function layoutName(l) {
  if (!l) return "";
  const def = LAYOUTS.find((d) => d.en === l.name || d.type === l.type);
  return (I18N.lang === "ru" && def && (def.en === l.name)) ? def.ru : l.name || l.type;
}
function promptFor(el, ph) {
  const t0 = el.ph?.type || "body";
  if (I18N.lang !== "ru" && ph?.prompt && !/^Click to edit Master/.test(ph.prompt)) return ph.prompt;
  return t0 === "title" || t0 === "ctrTitle" ? t("sl_ph_title") : t0 === "subTitle" ? t("sl_ph_subtitle") : t0 === "pic" ? t("sl_ph_picture") : t("sl_ph_body");
}
function renumberAnims(slide) { slideAnims(slide).forEach((x, i) => { x.anim.order = i; }); }
function popup(pop, btn) {
  closeMenus();
  document.body.append(pop);
  const r = btn.getBoundingClientRect();
  const x = Math.max(6, Math.min(innerWidth - pop.offsetWidth - 6, r.left)), y = r.bottom + pop.offsetHeight + 6 > innerHeight ? Math.max(6, r.top - pop.offsetHeight - 4) : r.bottom + 4;
  pop.style.left = x + "px"; pop.style.top = y + "px";
  const off = (e) => { if (!pop.contains(e.target)) { pop.remove(); document.removeEventListener("pointerdown", off, true); document.removeEventListener("keydown", esc2, true); } };
  const esc2 = (e) => { if (e.key === "Escape") { e.stopPropagation(); pop.remove(); document.removeEventListener("pointerdown", off, true); document.removeEventListener("keydown", esc2, true); } };
  setTimeout(() => { document.addEventListener("pointerdown", off, true); document.addEventListener("keydown", esc2, true); }, 0);
}
function shapeIcon(g) {
  const { geomPath: gp } = SHAPE_PATHS;
  const line = isLineGeom(g);
  return `<svg viewBox="-2 -2 28 24" class="sl-shape-ic"><path d="${line ? "M2 18 L22 2" : gp(g, 24, 20)}" fill="${line ? "none" : "currentColor"}" fill-opacity=".18" stroke="currentColor" stroke-width="1.6" ${g === "straightConnector1" ? 'marker-end="url(#sl-arrowhead)"' : ""}/>${g === "straightConnector1" ? '<path d="M22 2 l-7 1.5 M22 2 l-1.5 7" stroke="currentColor" stroke-width="1.6" fill="none"/>' : ""}</svg>`;
}
import { geomPath } from "./render.js";
const SHAPE_PATHS = { geomPath };
async function rasterize(bytes, type) {
  try {
    const url = URL.createObjectURL(new Blob([bytes], { type }));
    const img = await new Promise((r, j) => { const i = new Image(); i.onload = () => r(i); i.onerror = j; i.src = url; });
    const w = img.naturalWidth || 800, h = img.naturalHeight || 600, k = Math.min(1, 4096 / Math.max(w, h));
    const c = document.createElement("canvas"); c.width = Math.round(w * k * (type.includes("svg") ? 2 : 1)); c.height = Math.round(h * k * (type.includes("svg") ? 2 : 1));
    c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
    URL.revokeObjectURL(url);
    const blob = await new Promise((r) => c.toBlob(r, "image/png"));
    return new Uint8Array(await blob.arrayBuffer());
  } catch { return null; }
}
