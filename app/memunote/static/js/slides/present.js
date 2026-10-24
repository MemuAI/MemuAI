// MemuNote Slides — slide show: full screen, transitions, click animations, presenter view
// (current + next slide, notes, timer, clock), keyboard / mouse / wheel navigation.
import { esc, icon, t } from "../core.js";
import { renderSlide, fitTexts } from "./render.js";
import { slideAnims } from "./pptx.js";

const SPD = { fast: 500, med: 750, slow: 1000 };

export class Show {
  constructor(view, from, opts = {}) {
    this.view = view; this.deck = view.deck; this.opts = opts;
    const all = this.deck.slides.map((s, i) => i);
    this.order = opts.once ? [from] : all.filter((i) => !this.deck.slides[i].hidden || i === from);
    this.pos = Math.max(0, this.order.indexOf(from));
    this.step = 0; this.started = Date.now(); this.paused = 0; this.pauseAt = 0;
    this.presenter = !!opts.presenter;
    this.el = document.createElement("div");
    this.el.className = "sl-show" + (this.presenter ? " presenter" : "");
    this.el.tabIndex = 0;
    this.el.innerHTML = `<div class="sl-show-main"><div class="sl-show-stage"></div><div class="sl-show-curtain"></div></div>
      <div class="sl-pv">
        <div class="sl-pv-top"><span class="sl-pv-timer" title="${esc(t("sl_timer"))}">00:00</span><button class="sl-pv-btn" data-a="pause" title="${esc(t("sl_pause_timer"))}">❚❚</button><button class="sl-pv-btn" data-a="reset" title="${esc(t("sl_reset_timer"))}">${icon("retry")}</button><span class="spacer"></span><span class="sl-pv-clock"></span></div>
        <div class="sl-pv-next"><div class="sl-pv-label">${esc(t("sl_next_slide"))}</div><div class="sl-pv-nextbox"></div></div>
        <div class="sl-pv-notes-wrap"><div class="sl-pv-label">${esc(t("sl_notes"))}<span class="spacer"></span><button class="sl-pv-btn" data-a="font-">A−</button><button class="sl-pv-btn" data-a="font+">A+</button></div><div class="sl-pv-notes"></div></div>
        <div class="sl-pv-nav"><button class="sl-pv-btn big" data-a="prev" title="${esc(t("sl_prev"))}">${icon("chevron")}</button><span class="sl-pv-count"></span><button class="sl-pv-btn big" data-a="next" title="${esc(t("sl_next"))}">${icon("chevron")}</button>
          <span class="spacer"></span><button class="sl-pv-btn" data-a="black" title="${esc(t("sl_black"))} (B)">■</button><button class="sl-pv-btn" data-a="pview" title="${esc(t("sl_presenter_view"))}">${icon("split")}</button><button class="sl-pv-btn end" data-a="end">${esc(t("sl_end_show"))}</button></div>
      </div>
      <div class="sl-show-hud"><button data-a="prev" title="${esc(t("sl_prev"))}">${icon("chevron")}</button><span class="sl-hud-count"></span><button data-a="next" title="${esc(t("sl_next"))}">${icon("chevron")}</button><button data-a="pview" title="${esc(t("sl_presenter_view"))}">${icon("split")}</button><button data-a="end" title="${esc(t("sl_end_show"))} (Esc)">${icon("close")}</button></div>`;
    document.body.append(this.el);
    this.stage = this.el.querySelector(".sl-show-stage"); this.main = this.el.querySelector(".sl-show-main");
    this.notesSize = +localStorage.getItem("mn_sl_pv_font") || 18;
    this.onKey = (e) => this.key(e);
    document.addEventListener("keydown", this.onKey, true);
    this.el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]");
      if (b) { e.stopPropagation(); this.action(b.dataset.a); return; }
      if (e.target.closest(".sl-show-main")) this.next();
    });
    this.el.addEventListener("contextmenu", (e) => { e.preventDefault(); this.prev(); });
    this.el.addEventListener("wheel", (e) => { if (this._wheel) return; this._wheel = setTimeout(() => (this._wheel = null), 280); if (e.deltaY > 0) this.next(); else if (e.deltaY < 0) this.prev(); }, { passive: true });
    this.el.addEventListener("pointermove", () => { this.el.classList.add("hud"); clearTimeout(this._hud); this._hud = setTimeout(() => this.el.classList.remove("hud"), 2200); });
    this.ro = new ResizeObserver(() => this.paint(true)); this.ro.observe(this.main);
    this.tick = setInterval(() => this.clock(), 500);
    view.stopShow = () => this.end();
    if (!opts.once && document.fullscreenEnabled && !document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {});
    this.onFs = () => { if (!document.fullscreenElement && this._wasFs && !this.ended) this.end(); this._wasFs = !!document.fullscreenElement; };
    document.addEventListener("fullscreenchange", this.onFs);
    this.el.focus();
    this.paint(true);
    this.clock();
    this.armAuto();
    if (opts.once) this.autoPlay();
  }
  get slide() { return this.deck.slides[this.order[this.pos]]; }
  groups(slide) {
    const list = slideAnims(slide), out = [];
    for (const a of list) { if (a.anim.trig === "click" || !out.length) out.push([]); out[out.length - 1].push(a); }
    return out;
  }
  hiddenSet(slide, step) {
    const g = this.groups(slide), hide = new Set();
    g.forEach((grp, k) => { if (k >= step) for (const a of grp) hide.add(a.el.id); });
    return hide;
  }
  scaleFor(box) { return Math.min(box.clientWidth / this.deck.w, box.clientHeight / this.deck.h); }
  build(slideIdx, step, box) {
    const s = this.deck.slides[slideIdx];
    const node = renderSlide(this.deck, s, { scale: this.scaleFor(box), slideNo: slideIdx + 1, hide: this.hiddenSet(s, step) });
    node.classList.add("sl-show-slide");
    return node;
  }
  paint(instant) {
    if (this.ended) return;
    const node = this.build(this.order[this.pos], this.step, this.main);
    this.stage.replaceChildren(node); fitTexts(node);
    this.stage.style.width = node.style.width; this.stage.style.height = node.style.height;
    this.side();
  }
  side() {
    const n = this.order.length;
    const label = t("sl_status_slide", { n: this.pos + 1, total: n });
    this.el.querySelector(".sl-hud-count").textContent = label;
    this.el.querySelector(".sl-pv-count").textContent = label;
    if (!this.presenter) return;
    const nb = this.el.querySelector(".sl-pv-nextbox");
    const g = this.groups(this.slide);
    if (this.step < g.length) { const node = this.build(this.order[this.pos], this.step + 1, nb); nb.replaceChildren(node); fitTexts(node); }
    else if (this.pos + 1 < n) { const node = this.build(this.order[this.pos + 1], 0, nb); nb.replaceChildren(node); fitTexts(node); }
    else nb.innerHTML = `<div class="sl-pv-end">${esc(t("sl_end_of_show"))}</div>`;
    const notes = this.el.querySelector(".sl-pv-notes");
    notes.style.fontSize = this.notesSize + "px";
    notes.textContent = this.slide.notes || "";
    if (!this.slide.notes) notes.innerHTML = `<span class="muted">${esc(t("sl_no_notes"))}</span>`;
  }
  clock() {
    const now = Date.now(), el = (this.pauseAt || now) - this.started - this.paused;
    const s = Math.max(0, Math.floor(el / 1000));
    const tm = this.el.querySelector(".sl-pv-timer"); if (tm) tm.textContent = `${s >= 3600 ? Math.floor(s / 3600) + ":" : ""}${String(Math.floor(s / 60) % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    const c = this.el.querySelector(".sl-pv-clock"); if (c) c.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  action(a) {
    if (a === "next") this.next(); else if (a === "prev") this.prev(); else if (a === "end") this.end();
    else if (a === "black") this.curtain("black");
    else if (a === "pview") { this.presenter = !this.presenter; this.el.classList.toggle("presenter", this.presenter); localStorage.setItem("mn_sl_presenter", this.presenter ? "1" : "0"); requestAnimationFrame(() => this.paint(true)); }
    else if (a === "pause") { if (this.pauseAt) { this.paused += Date.now() - this.pauseAt; this.pauseAt = 0; } else this.pauseAt = Date.now(); this.clock(); }
    else if (a === "reset") { this.started = Date.now(); this.paused = 0; this.pauseAt = this.pauseAt ? Date.now() : 0; this.clock(); }
    else if (a === "font+" || a === "font-") { this.notesSize = Math.max(11, Math.min(40, this.notesSize + (a === "font+" ? 2 : -2))); localStorage.setItem("mn_sl_pv_font", this.notesSize); this.side(); }
  }
  curtain(kind) {
    const c = this.el.querySelector(".sl-show-curtain");
    const on = c.dataset.k === kind ? "" : kind;
    c.dataset.k = on; c.className = "sl-show-curtain" + (on ? " on " + on : "");
  }
  key(e) {
    if (this.ended) return;
    e.stopPropagation();
    const k = globalThis.MemuKeys ? MemuKeys.latin(e) : e.key;   // r33: B/W/N/P in any keyboard layout
    if (/^\d$/.test(k)) { this.typed = (this.typed || "") + k; clearTimeout(this._typedT); this._typedT = setTimeout(() => (this.typed = ""), 1500); e.preventDefault(); return; }
    if (k === "Enter" && this.typed) { e.preventDefault(); const n = +this.typed - 1; this.typed = ""; const p = this.order.indexOf(n); if (p >= 0) this.go(p, 0); return; }
    const curtainOn = !!this.el.querySelector(".sl-show-curtain.on");
    if (["ArrowRight", "ArrowDown", " ", "Enter", "PageDown", "n", "N"].includes(k)) { e.preventDefault(); if (curtainOn) this.curtain(""); else this.next(); }
    else if (["ArrowLeft", "ArrowUp", "Backspace", "PageUp", "p", "P"].includes(k)) { e.preventDefault(); if (curtainOn) this.curtain(""); else this.prev(); }
    else if (k === "Home") { e.preventDefault(); this.go(0, 0); }
    else if (k === "End") { e.preventDefault(); this.go(this.order.length - 1, 0); }
    else if (k === "b" || k === "B" || k === ".") { e.preventDefault(); this.curtain("black"); }
    else if (k === "w" || k === "W" || k === ",") { e.preventDefault(); this.curtain("white"); }
    else if (k === "Escape" || k === "-") { e.preventDefault(); this.end(); }
    else if (k === "F5" || (e.ctrlKey && k.toLowerCase() === "s")) e.preventDefault();
  }
  next() {
    if (this.busy) { this.finishAnims(); return; }
    const g = this.groups(this.slide);
    if (this.step < g.length) { this.playGroup(g[this.step]); this.step++; this.side(); this.armAuto(); return; }
    if (this.pos + 1 >= this.order.length) {
      if (this.atEnd || this.opts.once) { this.end(); return; }
      this.atEnd = true;
      this.stage.replaceChildren(Object.assign(document.createElement("div"), { className: "sl-show-endcard", textContent: t("sl_end_of_show_click") }));
      this.stage.style.width = this.main.clientWidth + "px"; this.stage.style.height = this.main.clientHeight + "px";
      return;
    }
    this.go(this.pos + 1, 0, true);
  }
  prev() {
    if (this.atEnd) { this.atEnd = false; this.paint(true); return; }
    this.finishAnims();
    if (this.step > 0) { this.step--; this.paint(true); this.armAuto(); return; }
    if (this.pos > 0) this.go(this.pos - 1, this.groups(this.deck.slides[this.order[this.pos - 1]]).length);
  }
  go(pos, step, animate) {
    this.atEnd = false;
    this._trDone?.();   // a transition still running: settle it now so only one slide is left under the next one
    const prevNode = this.stage.lastElementChild;
    this.pos = pos; this.step = step;
    const node = this.build(this.order[pos], step, this.main);
    const tr = animate ? this.slide.transition : null;
    if (tr && tr.type && tr.type !== "none" && prevNode && !matchMedia("(prefers-reduced-motion: reduce)").matches) this.transition(prevNode, node, tr);
    else { this.stage.replaceChildren(node); }
    fitTexts(node);
    this.side(); this.armAuto();
    this.autoGroupsAfter();
  }
  transition(oldN, newN, tr) {
    const dur = tr.dur || SPD[tr.spd || "med"] || 750;
    let type = tr.type; if (type === "random") type = ["fade", "push", "wipe", "cover", "split", "zoom"][Math.floor(Math.random() * 6)];
    oldN.classList.add("sl-tr-old"); newN.classList.add("sl-tr-new");
    this.stage.append(newN);
    const dir = tr.dir || "l", vec = { l: [-1, 0], r: [1, 0], u: [0, -1], d: [0, 1] }[dir] || [-1, 0];
    const W = this.stage.clientWidth, H = this.stage.clientHeight;
    const o = { duration: dur, easing: "cubic-bezier(.4,0,.2,1)", fill: "both" };
    let a;
    if (type === "fade" || type === "dissolve") a = newN.animate([{ opacity: 0 }, { opacity: 1 }], { ...o, easing: type === "dissolve" ? "steps(8)" : o.easing });
    else if (type === "push") { oldN.animate([{ transform: "none" }, { transform: `translate(${vec[0] * W}px,${vec[1] * H}px)` }], o); a = newN.animate([{ transform: `translate(${-vec[0] * W}px,${-vec[1] * H}px)` }, { transform: "none" }], o); }
    else if (type === "cover" || type === "pull") a = newN.animate([{ transform: `translate(${-vec[0] * W}px,${-vec[1] * H}px)` }, { transform: "none" }], o);
    else if (type === "wipe") { const from = { l: "inset(0 0 0 100%)", r: "inset(0 100% 0 0)", u: "inset(100% 0 0 0)", d: "inset(0 0 100% 0)" }[dir] || "inset(0 0 0 100%)"; a = newN.animate([{ clipPath: from }, { clipPath: "inset(0 0 0 0)" }], o); }
    else if (type === "split") a = newN.animate([{ clipPath: "inset(0 50% 0 50%)" }, { clipPath: "inset(0 0 0 0)" }], o);
    else if (type === "circle") a = newN.animate([{ clipPath: "circle(0% at 50% 50%)" }, { clipPath: "circle(75% at 50% 50%)" }], o);
    else if (type === "zoom") a = newN.animate([{ transform: "scale(.75)", opacity: 0 }, { transform: "none", opacity: 1 }], o);
    else a = newN.animate([{ opacity: 0 }, { opacity: 1 }], o);
    this.trAnim = a;
    const done = () => { if (this._trDone !== done) return; this._trDone = null; for (const x of oldN.getAnimations()) x.cancel(); oldN.remove(); newN.classList.remove("sl-tr-new"); this.trAnim = null; a.cancel?.(); };
    this._trDone = done;
    a.onfinish = done;
  }
  playGroup(group) {
    // click group: steps separated by "after"; "with" runs together
    const steps = [];
    for (const a of group) { if (!steps.length || a.anim.trig === "after") steps.push([]); steps[steps.length - 1].push(a); }
    let delay = 0;
    this.busy = true; this.anims = [];
    for (const step of steps) {
      let maxD = 0;
      for (const { el, anim } of step) {
        const node = this.stage.querySelector(`.sl-show-slide:last-child > .sl-el[data-id="${CSS.escape(el.id)}"]`) || this.stage.querySelector(`.sl-el[data-id="${CSS.escape(el.id)}"]`);
        if (!node) continue;
        const dur = anim.type === "appear" ? 1 : anim.dur || 500;
        maxD = Math.max(maxD, dur);
        const base = node.style.transform || "";
        let frames;
        if (anim.type === "fade") frames = [{ opacity: 0 }, { opacity: 1 }];
        else if (anim.type === "fly") { const d = anim.dir || "b"; const off = { b: "translateY(110vh)", t: "translateY(-110vh)", l: "translateX(-110vw)", r: "translateX(110vw)" }[d]; frames = [{ transform: `${off} ${base}` }, { transform: base || "none" }]; }
        else if (anim.type === "wipe") { const d = anim.dir || "l"; const from = { l: "inset(0 100% 0 0)", r: "inset(0 0 0 100%)", b: "inset(100% 0 0 0)", t: "inset(0 0 100% 0)" }[d]; frames = [{ clipPath: from }, { clipPath: "inset(0 0 0 0)" }]; }
        else if (anim.type === "zoom") frames = [{ transform: `${base} scale(0)`, opacity: 0 }, { transform: base || "none", opacity: 1 }];
        else frames = [{ opacity: 1 }, { opacity: 1 }];
        const start = delay;
        const tm = setTimeout(() => { node.style.visibility = "visible"; const an = node.animate(frames, { duration: dur, easing: anim.type === "fly" ? "cubic-bezier(.2,.8,.2,1)" : "ease-out", fill: "backwards" }); this.anims.push(an); }, start);
        this.anims.push({ cancel: () => { clearTimeout(tm); node.style.visibility = "visible"; }, finish: () => { clearTimeout(tm); node.style.visibility = "visible"; } });
      }
      delay += maxD;
    }
    clearTimeout(this._busyT);
    this._busyT = setTimeout(() => { this.busy = false; this.anims = []; }, delay + 30);
  }
  finishAnims() {
    for (const a of this.anims || []) try { a.finish(); } catch { /* finished */ }
    this._trDone?.();
    this.busy = false; this.anims = [];
  }
  /** "with/after previous" groups at the start of a slide (no click before them) play right away. */
  autoGroupsAfter() {
    const g = this.groups(this.slide);
    if (this.step === 0 && g.length && g[0][0].anim.trig !== "click") { setTimeout(() => { if (this.step === 0 && !this.ended) { this.playGroup(g[0]); this.step = 1; this.side(); } }, (this.slide.transition ? (this.slide.transition.dur || 750) : 0) + 50); }
  }
  armAuto() {
    clearTimeout(this._auto);
    const tr = this.slide?.transition;
    if (tr?.advTm !== undefined && !this.opts.once) this._auto = setTimeout(() => { if (!this.ended && !this.el.querySelector(".sl-show-curtain.on")) this.next(); }, Math.max(300, tr.advTm));
  }
  autoPlay() {
    const g = this.groups(this.slide);
    let k = 0;
    const stepIt = () => { if (this.ended) return; if (k < g.length) { this.next(); k++; setTimeout(stepIt, Math.max(700, ...g[k - 1].map((a) => (a.anim.dur || 500) + 300))); } else setTimeout(() => this.end(), 900); };
    setTimeout(stepIt, 500);
  }
  end() {
    if (this.ended) return;
    this.ended = true;
    clearInterval(this.tick); clearTimeout(this._auto); clearTimeout(this._busyT);
    this.ro.disconnect();
    document.removeEventListener("keydown", this.onKey, true);
    document.removeEventListener("fullscreenchange", this.onFs);
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    this.el.remove();
    this.view.stopShow = null;
    this.opts.onEnd?.(this.order[this.pos]);
  }
}
