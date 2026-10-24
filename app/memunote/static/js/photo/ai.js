// MemuNote Photo — AI: vision chat about the CURRENT image (all edits applied + the edit history as text), describe,
// alt text, captions, OCR, keywords, critique, auto-enhance and natural-language edits mapped to editor operations,
// object location → local refinement (select / remove background / blur background / remove object), image
// generation and AI image editing when the Proxy has an image model. All requests go through /api/photo/* (MemuAI Proxy).
import { esc, t, toast, api, copyText, I18N, API } from "../core.js";
import { renderMarkdown } from "../views.js";
import { newCanvas, ctx2d, DEFAULT_TEXT, renderVector } from "./doc.js";
import { canvasToBlob, blobToDataURL, loadImage, canvasFromImage } from "./io.js";
import { palette as dominant, invertMask, growMask } from "./pixels.js";
import { picon } from "./icons.js";
import { COMMANDS } from "./commands.js";

const LS = "mn_photo_";
const ADJ_NAMES = new Set(["brightnessContrast", "exposure", "levels", "hueSaturation", "vibrance", "temperature", "colorBalance", "curves", "autoLevels", "blackWhite", "sepia", "invert", "threshold", "posterize", "grayscale"]);
const FILTER_NAMES = new Set(["gaussianBlur", "sharpen", "unsharpMask", "noise", "denoise", "pixelate", "vignette", "emboss", "edges", "motionBlur", "oilPaint", "boxBlur"]);

export class PhotoAI {
  constructor(ed) { this.ed = ed; this.msgs = []; this.caps = null; this.mode = localStorage.getItem(LS + "ai_mode") || "ask"; this.busyN = 0; }
  destroy() { this.ctrl?.abort(); }

  // ---------------------------------------------------------------- capabilities
  async loadCaps(force = false) {
    if (this.caps && !force && Date.now() - this.capsAt < 30000) return this.caps;
    try { this.caps = await api("photo/caps" + (force ? "?force=1" : "")); } catch (e) { this.caps = { online: false, error: e.message, image_models: [], vision_models: [], models: [] }; }
    this.capsAt = Date.now(); this.refresh(); return this.caps;
  }
  get model() { const m = localStorage.getItem(LS + "model") || ""; return this.caps?.models?.some((x) => x.id === m) ? m : ""; }

  // ---------------------------------------------------------------- panel
  mount(host) {
    this.host = host;
    host.innerHTML = `<div class="pai"><div class="pai-status"></div><div class="pai-actions"></div><div class="pai-log" aria-live="polite"></div>
      <form class="pai-input"><div class="pai-modes">${[["ask", "chat", "ph_ai_mode_ask"], ["edit", "wand", "ph_ai_mode_edit"], ["gen", "image", "ph_ai_mode_gen"]].map(([m, ic, k]) => `<button type="button" class="${this.mode === m ? "on" : ""}" data-mode="${m}">${picon(ic)}<span>${esc(t(k))}</span></button>`).join("")}</div>
      <div class="pai-box"><textarea rows="2" class="pai-q" placeholder=""></textarea><button class="icon-btn pai-send" type="submit" title="${esc(t("ph_ai_send"))}">${picon("send")}</button></div></form></div>`;
    this.log = host.querySelector(".pai-log");
    host.querySelectorAll("[data-mode]").forEach((b) => (b.onclick = () => { this.mode = b.dataset.mode; localStorage.setItem(LS + "ai_mode", this.mode); host.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("on", x === b)); this.placeholder(); }));
    const q = host.querySelector(".pai-q");
    q.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); host.querySelector("form").requestSubmit(); } });
    host.querySelector("form").onsubmit = (e) => { e.preventDefault(); const v = q.value.trim(); if (!v) return; q.value = ""; this.submit(v); };
    this.placeholder(); this.renderActions(); this.renderLog(); this.refresh(); this.loadCaps();
  }
  placeholder() { const q = this.host?.querySelector(".pai-q"); if (q) q.placeholder = t({ ask: "ph_ai_ph_ask", edit: "ph_ai_ph_edit", gen: "ph_ai_ph_gen" }[this.mode]); }
  refresh() {
    const s = this.host?.querySelector(".pai-status"); if (!s) return;
    const c = this.caps;
    if (!c) { s.innerHTML = `<span class="muted small">${esc(t("ph_ai_checking"))}</span>`; return; }
    if (!c.online) { s.innerHTML = `<div class="pai-warn">${picon("warn")}<span>${esc(t("ph_ai_offline"))}</span><button class="btn sm ghost" data-a="retry">${esc(t("ph_retry"))}</button></div>`; s.querySelector("[data-a=retry]").onclick = () => this.loadCaps(true); return; }
    const opts = (c.models || []).map((m) => `<option value="${esc(m.id)}" ${m.id === (this.model || c.model) ? "selected" : ""}>${esc(m.label && m.label !== m.id ? `${m.label} · ${m.id}` : m.id)}${m.vision ? " 👁" : ""}</option>`).join("");
    const gen = c.image_models?.length ? `<span class="pai-chip ok">${picon("image")}${esc(t("ph_ai_imagegen_on", { n: c.image_models.length }))}</span>` : `<span class="pai-chip" title="${esc(t("ph_ai_imagegen_how"))}">${picon("image")}${esc(t("ph_ai_imagegen_off"))}</span>`;
    s.innerHTML = `<div class="pai-row"><select class="input xs pai-model" title="${esc(t("ph_ai_model"))}">${opts}</select>${gen}</div>${c.model_has_vision === false && !this.model ? `<div class="pai-warn small">${picon("warn")}<span>${esc(t("ph_ai_no_vision"))}</span></div>` : ""}`;
    s.querySelector(".pai-model").onchange = (e) => { localStorage.setItem(LS + "model", e.target.value); };
  }
  renderActions() {
    const host = this.host?.querySelector(".pai-actions"); if (!host) return;
    const A = [["describe", "eye", "ph_ai_describe"], ["alt", "alt", "ph_ai_alt"], ["caption", "quote", "ph_ai_caption"], ["ocr", "text", "ph_ai_ocr"], ["enhance", "auto", "ph_ai_enhance"], ["critique", "star", "ph_ai_critique"],
      ["removeBg", "cutout", "ph_ai_remove_bg"], ["selectSubject", "person", "ph_ai_select_subject"], ["selectObj", "objSel", "ph_ai_select_obj"], ["blurBg", "drop", "ph_ai_blur_bg"], ["removeObj", "eraser", "ph_ai_remove_obj"], ["tags", "tag", "ph_ai_tags"],
      ["palette", "palette", "ph_ai_palette"], ["prompt", "sparkle", "ph_ai_prompt"], ["generate", "image", "ph_ai_generate"], ["aiEdit", "wand", "ph_ai_edit_image"]];
    host.innerHTML = A.map(([id, ic, k]) => `<button class="pai-act" data-act="${id}" title="${esc(t(k + "_hint"))}">${picon(ic)}<span>${esc(t(k))}</span></button>`).join("");
    host.querySelectorAll("[data-act]").forEach((b) => (b.onclick = () => this.action(b.dataset.act)));
  }
  menuItems() {
    const I = (k, act, extra = {}) => ({ label: t(k), run: () => { this.ed.showTab("ai"); this.action(act); }, ...extra });
    return [I("ph_ai_describe", "describe"), I("ph_ai_ask", "ask"), I("ph_ai_alt", "alt"), I("ph_ai_caption", "caption"), I("ph_ai_ocr", "ocr"), I("ph_ai_tags", "tags"), I("ph_ai_critique", "critique"), "-",
      I("ph_ai_enhance", "enhance"), I("ph_ai_command", "command"), "-", I("ph_ai_remove_bg", "removeBg"), I("ph_ai_select_subject", "selectSubject"), I("ph_ai_select_obj", "selectObj"), I("ph_ai_blur_bg", "blurBg"), I("ph_ai_remove_obj", "removeObj"), "-",
      I("ph_ai_generate", "generate"), I("ph_ai_edit_image", "aiEdit"), I("ph_ai_prompt", "prompt"), I("ph_ai_palette", "palette")];
  }

  // ---------------------------------------------------------------- log
  push(m) { this.msgs.push(m); this.renderLog(); return m; }
  renderLog() {
    if (!this.log) return;
    if (!this.msgs.length) { this.log.innerHTML = `<div class="pai-empty">${picon("sparkle")}<p>${esc(t("ph_ai_intro"))}</p></div>`; return; }
    this.log.innerHTML = this.msgs.map((m, i) => this.msgHtml(m, i)).join("");
    this.log.querySelectorAll("[data-mact]").forEach((b) => (b.onclick = () => this.msgAction(+b.dataset.i, b.dataset.mact, b)));
    this.log.scrollTop = this.log.scrollHeight;
  }
  msgHtml(m, i) {
    if (m.role === "user") return `<div class="pai-msg user"><div class="pai-bubble">${m.image ? `<img class="pai-thumb" src="${m.image}" alt="">` : ""}${esc(m.text)}</div></div>`;
    const buttons = (m.buttons || []).map(([act, ic, k]) => `<button class="btn sm ghost" data-mact="${act}" data-i="${i}">${picon(ic)}${esc(t(k))}</button>`).join("");
    const body = m.error ? `<div class="pai-err">${picon("warn")}<span>${esc(m.error)}</span></div>` : m.pending && !m.text ? `<div class="pai-typing"><i></i><i></i><i></i><span>${esc(m.status || t("ph_ai_thinking"))}</span></div>` : m.html || renderMarkdown(m.text || "");
    const ops = m.ops?.length ? `<ul class="pai-ops">${m.ops.map((o) => `<li>${esc(opLabel(o))}</li>`).join("")}</ul>` : "";
    const imgs = m.images?.length ? `<div class="pai-gen">${m.images.map((src, k) => `<figure><img src="${src}" alt=""><figcaption><button class="btn sm" data-mact="layer:${k}" data-i="${i}">${picon("layers")}${esc(t("ph_ai_as_layer"))}</button><button class="btn sm ghost" data-mact="newtab:${k}" data-i="${i}">${picon("plus")}${esc(t("ph_ai_as_new"))}</button></figcaption></figure>`).join("")}</div>` : "";
    const sw = m.swatches?.length ? `<div class="pai-swatches">${m.swatches.map((c) => `<button style="background:${c}" data-mact="color:${c}" data-i="${i}" title="${c}"></button>`).join("")}</div>` : "";
    return `<div class="pai-msg ai ${m.pending ? "pending" : ""}"><div class="pai-bubble">${body}${sw}${ops}${imgs}${buttons && !m.pending ? `<div class="pai-btns">${buttons}</div>` : ""}${m.model ? `<div class="pai-meta">${esc(m.model)}</div>` : ""}</div></div>`;
  }
  async msgAction(i, act, btn) {
    const m = this.msgs[i]; if (!m) return;
    if (act === "copy") { await copyText(m.text || ""); toast(t("ph_copied"), { kind: "ok", ms: 1200 }); }
    else if (act === "textLayer") this.addTextLayer(m.text);
    else if (act === "textTab") this.ed.hooks.newTextTab?.(m.text, this.ed.tab.name.replace(/\.[^.]+$/, "") + ".txt");
    else if (act === "apply") { await this.applyOps(m.ops, t("ph_ai") + ": " + (m.request || t("ph_ai_enhance"))); m.applied = true; m.buttons = [["undo", "undo", "ph_undo"]]; this.renderLog(); }
    else if (act === "undo") { this.ed.undo(); m.buttons = m.ops?.length ? [["apply", "check", "ph_ai_apply"]] : []; this.renderLog(); }
    else if (act === "stop") this.ctrl?.abort();
    else if (act.startsWith("color:")) this.ed.setColor("fg", act.slice(6));
    else if (act.startsWith("layer:")) { const img = await loadImage(m.images[+act.slice(6)]); this.ed.placeCanvas(canvasFromImage(img), t("ph_ai_generated")); }
    else if (act.startsWith("newtab:")) { const img = await loadImage(m.images[+act.slice(7)]); this.ed.hooks.newImageFromCanvas?.(canvasFromImage(img), t("ph_ai_generated")); }
  }

  // ---------------------------------------------------------------- requests
  /** The current composite (all layers, all edits) as a JPEG/PNG data URL for the vision model. */
  async snapshot(maxSide = 1280) {
    const d = this.ed.doc, k = Math.min(1, maxSide / Math.max(d.width, d.height)), min = 32;
    let w = Math.round(d.width * k), h = Math.round(d.height * k);
    if (Math.min(w, h) < min) { const u = min / Math.min(w, h); w = Math.round(w * u); h = Math.round(h * u); }
    const c = newCanvas(w, h), x = ctx2d(c); x.fillStyle = "#ffffff"; x.fillRect(0, 0, w, h); x.imageSmoothingQuality = "high"; x.drawImage(d.flatten(), 0, 0, w, h);
    const url = await blobToDataURL(await canvasToBlob(c, "image/jpeg", 0.88));
    return { url, w, h };
  }
  context() {
    const d = this.ed.doc;
    return { name: this.ed.tab.name, doc_width: d.width, doc_height: d.height, edits: d.editsSummary().slice(-60), lang: I18N.lang,
      layers: d.flat().map((l) => `${l.name} [${l.type}${l.visible ? "" : ", hidden"}${l.opacity < 1 ? `, ${Math.round(l.opacity * 100)}%` : ""}${l.blend !== "normal" ? ", " + l.blend : ""}${l.mask ? ", mask" : ""}${l.type === "text" ? `: "${String(l.text.text).slice(0, 60)}"` : ""}]`) };
  }
  async ready() {
    if (!this.ed.doc) { toast(t("ph_no_document")); return false; }
    const c = await this.loadCaps();
    if (!c.online) { this.push({ role: "ai", error: t("ph_ai_offline") }); return false; }
    if (c.ai_enabled === false) { this.push({ role: "ai", error: t("ph_ai_disabled") }); return false; }
    return true;
  }
  async stream(action, extra = {}, msg) {
    const snap = await this.snapshot();
    const body = { action, image: snap.url, width: snap.w, height: snap.h, model: this.model, id: "ph" + Date.now().toString(36), ...this.context(), ...extra };
    this.ctrl?.abort(); const ctrl = (this.ctrl = new AbortController());
    msg.pending = true; msg.buttons = [["stop", "stop", "ph_ai_stop"]]; this.renderLog();
    let res;
    try { res = await fetch("/api/photo/ai", { method: "POST", headers: { "Content-Type": "application/json", "X-MN-Token": API.token }, body: JSON.stringify(body), signal: ctrl.signal }); }
    catch (e) { msg.pending = false; msg.error = e.name === "AbortError" ? t("ph_ai_stopped") : t("ph_ai_offline"); this.renderLog(); return null; }
    if (!res.ok) { const j = await res.json().catch(() => ({})); msg.pending = false; msg.error = friendly(j.error || "HTTP " + res.status, res.status); this.renderLog(); return null; }
    const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "", result = null, raf = 0;
    const paint = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; this.renderLog(); }); };
    try {
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let n; while ((n = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, n); buf = buf.slice(n + 2);
          for (const line of chunk.split("\n")) {
            if (!line.startsWith("data:")) continue;
            let ev; try { ev = JSON.parse(line.slice(5)); } catch { continue; }
            if (ev.t === "start") { msg.model = ev.model; if (ev.vision === false) msg.note = t("ph_ai_no_vision"); }
            else if (ev.t === "delta") { msg.text = (msg.text || "") + ev.text; paint(); }
            else if (ev.t === "result") result = ev;
            else if (ev.t === "error") { msg.error = friendly(ev.message, ev.status, ev.kind); }
          }
        }
      }
    } catch (e) { if (e.name === "AbortError") { msg.note = t("ph_ai_stopped"); } else msg.error = e.message; }
    msg.pending = false; msg.buttons = []; this.renderLog();
    return { text: msg.text || "", result };
  }
  async submit(text) {
    if (this.mode === "gen") return this.generate(text);
    if (this.mode === "edit") return this.command(text);
    return this.ask(text);
  }
  async action(id) {
    const map = { describe: () => this.simple("describe", t("ph_ai_describe")), alt: () => this.simple("alt", t("ph_ai_alt")), caption: () => this.simple("caption", t("ph_ai_caption")), ocr: () => this.simple("ocr", t("ph_ai_ocr")),
      tags: () => this.simple("tags", t("ph_ai_tags")), critique: () => this.simple("critique", t("ph_ai_critique")), prompt: () => this.simple("prompt", t("ph_ai_prompt")), enhance: () => this.enhance(),
      removeBg: () => this.removeBackground(), selectSubject: () => this.selectSubject(), selectObj: () => this.promptSelect(), blurBg: () => this.blurBackground(), removeObj: () => this.promptRemove(),
      palette: () => this.palette(), generate: () => this.openGenerate(), aiEdit: () => this.openGenerate(false, true), ask: () => this.focus("ask"), command: () => this.focus("edit") };
    try { await map[id]?.(); } catch (e) { this.ed.error(e); }
  }
  focus(mode) { this.ed.showTab("ai"); if (mode) { this.mode = mode; localStorage.setItem(LS + "ai_mode", mode); this.host?.querySelectorAll("[data-mode]").forEach((x) => x.classList.toggle("on", x.dataset.mode === mode)); this.placeholder(); } setTimeout(() => this.host?.querySelector(".pai-q")?.focus(), 30); }
  async simple(action, label) {
    this.ed.showTab("ai"); if (!(await this.ready())) return;
    this.push({ role: "user", text: label });
    const m = this.push({ role: "ai", text: "" });
    const r = await this.stream(action, {}, m); if (!r) return;
    if (action === "ocr" && /^\s*NO_TEXT\s*$/.test(m.text)) { m.text = t("ph_ai_no_text"); m.buttons = []; }
    else m.buttons = action === "ocr" ? [["copy", "copy", "ph_copy"], ["textLayer", "text", "ph_ai_to_text_layer"], ["textTab", "plus", "ph_ai_to_tab"]] : action === "describe" || action === "critique" ? [["copy", "copy", "ph_copy"], ["textTab", "plus", "ph_ai_to_tab"]] : [["copy", "copy", "ph_copy"]];
    this.renderLog();
  }
  async ask(q) {
    this.ed.showTab("ai"); if (!(await this.ready())) return;
    const history = this.msgs.filter((m) => (m.role === "user" && m.chat) || (m.role === "ai" && m.chat && m.text)).slice(-12).map((m) => ({ role: m.role === "ai" ? "assistant" : "user", text: m.text }));
    this.push({ role: "user", text: q, chat: true });
    const m = this.push({ role: "ai", text: "", chat: true });
    const r = await this.stream("ask", { question: q, history }, m); if (r) { m.buttons = [["copy", "copy", "ph_copy"]]; this.renderLog(); }
  }
  async enhance() {
    this.ed.showTab("ai"); if (!(await this.ready())) return;
    this.push({ role: "user", text: t("ph_ai_enhance") });
    const m = this.push({ role: "ai", text: "", status: t("ph_ai_looking") });
    const r = await this.stream("enhance", {}, m); if (!r) return;
    this.showOps(m, r, t("ph_ai_enhance"), false);
  }
  async command(q) {
    this.ed.showTab("ai"); if (!(await this.ready())) return;
    this.push({ role: "user", text: q });
    const m = this.push({ role: "ai", text: "", status: t("ph_ai_planning") });
    const r = await this.stream("command", { question: q }, m); if (!r) return;
    this.showOps(m, r, q, true);
  }
  showOps(m, r, request, autoApply) {
    const data = r.result?.ok ? r.result.data : null;
    if (!data) { m.text = ""; m.error = t("ph_ai_bad_json"); if (r.result?.raw) m.text = "```\n" + String(r.result.raw).slice(0, 800) + "\n```"; this.renderLog(); return; }
    const ops = sanitizeOps(data.ops);
    m.text = String(data.reply || "").slice(0, 2000) || (ops.length ? "" : t("ph_ai_no_ops")); m.ops = ops; m.request = request;
    m.buttons = ops.length ? [["apply", "check", "ph_ai_apply"]] : [];
    this.renderLog();
    if (autoApply && ops.length) this.msgAction(this.msgs.indexOf(m), "apply");
  }

  // ---------------------------------------------------------------- operations from the model
  async applyOps(ops, label) {
    const ed = this.ed, d = ed.doc;
    d.beginGroup(label.slice(0, 80));
    ed.busy(true, t("ph_ai_applying"));
    try {
      for (const o of ops) {
        try { await this.applyOp(o); } catch (e) { toast(`${opLabel(o)}: ${e.message}`, { kind: "error" }); }
      }
    } finally { ed.busy(false); d.endGroup(); ed.refreshPanels(); }
  }
  boxToDoc(box) {
    const d = this.ed.doc; if (!Array.isArray(box) || box.length < 4) return null;
    let [x0, y0, x1, y1] = box.map(Number); if (![x0, y0, x1, y1].every(Number.isFinite)) return null;
    // the protocol asks for 0..1000; tolerate 0..1 fractions
    const k = Math.max(x0, y0, x1, y1) <= 1.0001 ? 1 : 1000;
    x0 = (Math.min(x0, x1) / k) * d.width; x1 = (Math.max(box[0], box[2]) / k) * d.width; y0 = (Math.min(y0, y1) / k) * d.height; y1 = (Math.max(box[1], box[3]) / k) * d.height;
    const r = { x: Math.max(0, x0), y: Math.max(0, y0) }; r.w = Math.min(d.width, x1) - r.x; r.h = Math.min(d.height, y1) - r.y;
    return r.w > 2 && r.h > 2 ? r : null;
  }
  async applyOp(o) {
    const ed = this.ed, d = ed.doc;
    if (o.op === "adjust" || o.op === "filter") return COMMANDS.applyOp(ed, o.name, o.params || {}, ed.doc.active?.type === "raster" ? ed.doc.active : rasterTargetFor(ed));
    if (o.op === "crop") { const r = this.boxToDoc(o.box); if (r) COMMANDS.cropTo(ed, { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.w), h: Math.round(r.h) }); return; }
    if (o.op === "rotate") { const deg = Number(o.degrees) || 0; if (deg) COMMANDS.rotateCanvas(ed, deg); return; }
    if (o.op === "flip") return COMMANDS.flipCanvas(ed, o.axis === "vertical" ? "v" : "h");
    if (o.op === "resize") { const w = Math.round(o.width), h = Math.round(o.height); if (w > 0 && h > 0 && w <= 30000 && h <= 30000) COMMANDS.resizeImage(ed, w, h); return; }
    if (o.op === "select_all") return ed.cmd("selectAll");
    if (o.op === "deselect") return ed.select(null, "new", t("ph_deselect"));
    if (o.op === "invert_selection") return ed.cmd("inverse");
    if (o.op === "select") { const r = this.boxToDoc(o.box); if (!r) return; const m = await ed.segmentBox(r); ed.select(m, "new", t("ph_ai_select_obj") + (o.target ? `: ${o.target}` : "")); return; }
    if (o.op === "remove_background") return this.removeBackground(this.boxToDoc(o.box), true);
    if (o.op === "blur_background") return this.blurBackground(this.boxToDoc(o.box), Number(o.radius) || 12, true);
    if (o.op === "remove_object") { const r = this.boxToDoc(o.box); if (r) await this.eraseBox(r); return; }
    if (o.op === "text") {
      const r = this.boxToDoc(o.box) || { x: d.width * 0.1, y: d.height * 0.1, w: d.width * 0.8, h: d.height * 0.15 };
      const size = Math.max(8, Math.round(Math.min(r.h * 0.8, (r.w / Math.max(4, String(o.text).length)) * 1.8)));
      d.begin(t("ph_layer_text"));
      const l = d.insertLayer(d.makeLayer({ type: "text", name: String(o.text).slice(0, 40), text: { ...DEFAULT_TEXT, text: String(o.text || "").slice(0, 2000), font: o.font || "Segoe UI", size, color: /^#[0-9a-f]{6}$/i.test(o.color) ? o.color : "#ffffff", bold: o.bold !== false, x: Math.round(r.x), y: Math.round(r.y), stroke: Math.round(size / 18), strokeColor: "#000000", align: "left" } }));
      renderVector(d, l); d.commit(); return;
    }
    if (o.op === "fill") { const c = /^#[0-9a-f]{6}$/i.test(o.color) ? o.color : ed.fg; const prev = ed.fg; ed.fg = c; ed.cmd("fillFg"); ed.fg = prev; return; }
  }

  // ---------------------------------------------------------------- locate → refine locally
  async locate(what) {
    if (!(await this.ready())) return [];
    const m = this.push({ role: "ai", text: "", status: t("ph_ai_locating", { what }) });
    const r = await this.stream("locate", { question: what }, m);
    const objs = r?.result?.ok ? (r.result.data.objects || []) : [];
    const boxes = objs.map((o) => ({ label: o.label, r: this.boxToDoc(o.box) })).filter((o) => o.r);
    m.text = boxes.length ? t("ph_ai_found", { n: boxes.length, what }) : r ? t("ph_ai_not_found", { what }) : "";
    if (!r?.result?.ok && r) m.error = t("ph_ai_bad_json");
    this.renderLog();
    return boxes;
  }
  async subjectMask(box = null) {
    const ed = this.ed, d = ed.doc;
    if (!box && this.caps?.online) { const found = await this.locate(t("ph_ai_main_subject")); box = found[0]?.r || null; }
    if (box) { const pad = 0.06; box = { x: Math.max(0, box.x - box.w * pad), y: Math.max(0, box.y - box.h * pad), w: Math.min(d.width, box.w * (1 + 2 * pad)), h: Math.min(d.height, box.h * (1 + 2 * pad)) }; }
    return ed.segmentBox(box || { x: d.width * 0.02, y: d.height * 0.02, w: d.width * 0.96, h: d.height * 0.96 });
  }
  async promptSelect() {
    const { prompt } = await import("../core.js");
    const what = await prompt(t("ph_ai_select_obj"), t("ph_ai_what_select"), ""); if (!what) return;
    this.ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_select_obj") + ": " + what });
    const boxes = await this.locate(what); if (!boxes.length) return;
    this.ed.busy(true, t("ph_ai_refining"));
    try {
      let m = null; for (const b of boxes.slice(0, 6)) { const mm = await this.ed.segmentBox(b.r); if (!m) m = mm; else for (let i = 0; i < m.length; i++) m[i] = Math.max(m[i], mm[i]); }
      this.ed.select(m, "new", t("ph_ai_select_obj") + ": " + what);
    } finally { this.ed.busy(false); }
  }
  async selectSubject() {
    this.ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_select_subject") });
    this.ed.busy(true, t("ph_ai_refining"));
    try { const m = await this.subjectMask(); this.ed.select(m, "new", t("ph_ai_select_subject")); this.push({ role: "ai", text: t("ph_ai_selected_hint") }); } finally { this.ed.busy(false); }
  }
  async removeBackground(box = null, quiet = false) {
    const ed = this.ed, d = ed.doc;
    if (!quiet) { ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_remove_bg") }); }
    let l = d.active;
    if (!l || l.type !== "raster") l = d.rasterLayers().filter((x) => x.type === "raster" && x.visible).pop();
    if (!l) { toast(t("ph_no_layer")); return; }
    ed.busy(true, t("ph_ai_refining"));
    try {
      const m = await this.subjectMask(box);
      d.beginGroup(t("ph_ai_remove_bg"));
      d.activeId = l.id; ed.select(m, "new", t("ph_ai_select_subject"));
      if (l.mask) { d.begin(t("ph_delete_mask")); l.mask = null; d.commit(); }
      ed.cmd("addMask", "selection");
      d.endGroup();
      if (!quiet) this.push({ role: "ai", text: t("ph_ai_bg_removed") });
    } finally { ed.busy(false); }
  }
  async blurBackground(box = null, radius = 12, quiet = false) {
    const ed = this.ed, d = ed.doc;
    if (!quiet) { ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_blur_bg") }); }
    const l = d.active?.type === "raster" ? d.active : rasterTargetFor(ed); if (!l) { toast(t("ph_no_layer")); return; }
    ed.busy(true, t("ph_ai_refining"));
    try {
      const subject = await this.subjectMask(box);
      const bg = await ed.feather(invertMask(growMask(subject, d.width, d.height, 2)), Math.max(2, Math.round(Math.min(d.width, d.height) / 300)));
      d.beginGroup(t("ph_ai_blur_bg"));
      const keep = d.selection; d.begin(t("ph_selection")); d.setSelection(bg); d.commit();
      await COMMANDS.applyOp(ed, "gaussianBlur", { radius: Math.max(2, radius * Math.max(1, Math.min(d.width, d.height) / 1000)) }, l);
      d.begin(t("ph_selection")); d.setSelection(keep); d.commit();
      d.endGroup();
      if (!quiet) this.push({ role: "ai", text: t("ph_ai_bg_blurred") });
    } finally { ed.busy(false); }
  }
  async promptRemove() {
    const { prompt } = await import("../core.js");
    if (this.ed.doc.selection) { this.ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_remove_obj") }); await this.ed.cmd("contentAwareFill"); this.push({ role: "ai", text: t("ph_ai_removed") }); return; }
    const what = await prompt(t("ph_ai_remove_obj"), t("ph_ai_what_remove"), ""); if (!what) return;
    this.ed.showTab("ai"); this.push({ role: "user", text: t("ph_ai_remove_obj") + ": " + what });
    const boxes = await this.locate(what); if (!boxes.length) return;
    this.ed.doc.beginGroup(t("ph_ai_remove_obj") + ": " + what);
    try { for (const b of boxes.slice(0, 4)) await this.eraseBox(b.r); } finally { this.ed.doc.endGroup(); }
    this.push({ role: "ai", text: t("ph_ai_removed") });
  }
  async eraseBox(r) {
    const ed = this.ed, d = ed.doc;
    const m = growMask(await ed.segmentBox(r), d.width, d.height, Math.max(3, Math.round(Math.min(d.width, d.height) / 200)));
    const keep = d.selection; d.begin(t("ph_selection")); d.setSelection(m); d.commit();
    await COMMANDS.run.contentAwareFill(ed);
    d.begin(t("ph_selection")); d.setSelection(keep); d.commit();
  }
  async palette() {
    this.ed.showTab("ai");
    const d = this.ed.doc, k = Math.min(1, 300 / Math.max(d.width, d.height)), c = newCanvas(Math.max(1, d.width * k), Math.max(1, d.height * k)), x = ctx2d(c, { willReadFrequently: true }); x.drawImage(d.flatten(), 0, 0, c.width, c.height);
    const cols = dominant(x.getImageData(0, 0, c.width, c.height), 6);
    this.push({ role: "user", text: t("ph_ai_palette") });
    const m = this.push({ role: "ai", text: cols.map((h) => "`" + h + "`").join("  "), swatches: cols, buttons: [["copy", "copy", "ph_copy"]] });
    if (this.caps?.online) { m.text = ""; const r = await this.stream("palette_names", { palette: cols }, m); if (r) { m.buttons = [["copy", "copy", "ph_copy"]]; this.renderLog(); } }
  }
  addTextLayer(text) {
    const d = this.ed.doc; d.begin(t("ph_layer_text"));
    const size = Math.max(14, Math.round(Math.min(d.width, d.height) / 24));
    const l = d.insertLayer(d.makeLayer({ type: "text", name: String(text).split("\n")[0].slice(0, 40), text: { ...DEFAULT_TEXT, text: String(text).slice(0, 5000), size, color: this.ed.fg, x: Math.round(d.width * 0.05), y: Math.round(d.height * 0.05), width: Math.round(d.width * 0.9) } }));
    renderVector(d, l); d.commit();
  }

  // ---------------------------------------------------------------- generation
  async openGenerate(fresh = false, edit = false) {
    this.ed.showTab("ai"); await this.loadCaps();
    this.focus("gen"); this.editMode = edit; this.fresh = fresh;
    if (!this.caps?.online) { this.push({ role: "ai", error: t("ph_ai_offline") }); return; }
    if (!this.caps.image_models?.length) { this.push({ role: "ai", html: `<p><b>${esc(t("ph_ai_imagegen_off"))}</b></p><p>${esc(t("ph_ai_imagegen_how"))}</p>` }); return; }
    this.push({ role: "ai", html: `<p>${esc(t(edit ? "ph_ai_edit_intro" : "ph_ai_gen_intro"))}</p><div class="pai-genopts"><select class="input xs" data-g="model">${this.caps.image_models.map((m) => `<option value="${esc(m.id)}" ${m.id === localStorage.getItem(LS + "img_model") ? "selected" : ""}>${esc(m.label && m.label !== m.id ? `${m.label} · ${m.id}` : m.id)}</option>`).join("")}</select><select class="input xs" data-g="aspect">${["auto", "1:1", "16:9", "9:16", "4:3", "3:4"].map((a) => `<option ${a === (localStorage.getItem(LS + "aspect") || "auto") ? "selected" : ""}>${a}</option>`).join("")}</select></div>` });
    this.log.querySelectorAll("[data-g]").forEach((s) => (s.onchange = () => localStorage.setItem(LS + (s.dataset.g === "model" ? "img_model" : "aspect"), s.value)));
  }
  async generate(promptText) {
    this.ed.showTab("ai"); await this.loadCaps();
    if (!this.caps?.online) { this.push({ role: "ai", error: t("ph_ai_offline") }); return; }
    if (!this.caps.image_models?.length) { this.push({ role: "ai", html: `<p><b>${esc(t("ph_ai_imagegen_off"))}</b></p><p>${esc(t("ph_ai_imagegen_how"))}</p>` }); return; }
    const model = localStorage.getItem(LS + "img_model") || this.caps.image_models[0].id, aspect = localStorage.getItem(LS + "aspect") || "auto";
    const images = [];
    let thumb = "";
    if (this.editMode && this.ed.doc) { const s = await this.snapshot(1536); images.push(s.url); thumb = s.url; }
    this.push({ role: "user", text: promptText, image: thumb });
    const m = this.push({ role: "ai", text: "", pending: true, status: t("ph_ai_generating"), model });
    const ctrl = (this.ctrl = new AbortController());
    try {
      const res = await fetch("/api/photo/generate", { method: "POST", headers: { "Content-Type": "application/json", "X-MN-Token": API.token }, body: JSON.stringify({ prompt: promptText, model, aspect, images }), signal: ctrl.signal });
      if (!res.ok) { const j = await res.json().catch(() => ({})); throw new Error(friendly(j.error || "HTTP " + res.status, res.status, j.kind)); }
      const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break; buf += dec.decode(value, { stream: true });
        let n; while ((n = buf.indexOf("\n\n")) >= 0) {
          const line = buf.slice(0, n).replace(/^data:\s*/, ""); buf = buf.slice(n + 2);
          let ev; try { ev = JSON.parse(line); } catch { continue; }
          if (ev.t === "status") { m.status = t("ph_ai_generating") + (ev.ms ? ` ${Math.round(ev.ms / 1000)} s` : ""); this.renderLog(); }
          else if (ev.t === "done") { m.images = ev.images || []; if (!m.images.length) m.error = t("ph_ai_gen_empty"); }
          else if (ev.t === "error") m.error = friendly(ev.message, ev.status);
        }
      }
    } catch (e) { m.error = e.name === "AbortError" ? t("ph_ai_stopped") : e.message; }
    m.pending = false; this.renderLog();
    if (m.images?.length === 1 && this.fresh) { this.fresh = false; const img = await loadImage(m.images[0]); const c = canvasFromImage(img); const { PhotoDoc } = await import("./doc.js"); const d = new PhotoDoc(c.width, c.height); const l = d.makeLayer({ name: t("ph_ai_generated"), canvas: c }); d.layers.push(l); d.activeId = l.id; this.ed.setDoc(d); this.ed.doc.history.push({ label: t("ph_ai_generated") + ": " + promptText.slice(0, 60), before: d.snapshot(), after: d.snapshot(), patches: [], bytes: 0 }); this.ed.hooks.onDirty?.(true); this.ed.refreshPanels(); }
  }
}

function rasterTargetFor(ed) { const l = ed.doc.active; if (l?.type === "raster") return l; const r = ed.doc.rasterLayers().filter((x) => x.type === "raster" && x.visible).pop(); if (r) ed.doc.activeId = r.id; return r || null; }
export function sanitizeOps(ops) {
  if (!Array.isArray(ops)) return [];
  return ops.filter((o) => o && typeof o === "object" && typeof o.op === "string").slice(0, 12).filter((o) => {
    if (o.op === "adjust") return ADJ_NAMES.has(o.name);
    if (o.op === "filter") return FILTER_NAMES.has(o.name);
    return ["crop", "rotate", "flip", "resize", "select", "select_all", "deselect", "invert_selection", "remove_background", "blur_background", "remove_object", "text", "fill"].includes(o.op);
  }).map((o) => ({ ...o, params: o.params && typeof o.params === "object" ? clampParams(o.params) : o.params }));
}
function clampParams(p) { const out = {}; for (const [k, v] of Object.entries(p)) out[k] = typeof v === "number" ? Math.max(-1000, Math.min(1000, v)) : Array.isArray(v) ? v.slice(0, 16) : typeof v === "boolean" || typeof v === "string" ? v : undefined; return out; }
export function opLabel(o) {
  const p = o.params ? Object.entries(o.params).filter(([, v]) => typeof v === "number" || typeof v === "boolean").map(([k, v]) => `${t("ph_p_" + k)} ${typeof v === "boolean" ? (v ? "✓" : "✗") : Math.round(v * 100) / 100}`).join(", ") : "";
  if (o.op === "adjust" || o.op === "filter") return t("ph_adj_" + o.name) + (p ? ` (${p})` : "");
  if (o.op === "text") return `${t("ph_layer_text")}: «${String(o.text).slice(0, 40)}»`;
  if (o.op === "rotate") return `${t("ph_rotate_canvas")} ${o.degrees}°`;
  if (o.op === "resize") return `${t("ph_image_size")} ${o.width}×${o.height}`;
  if (o.op === "select") return `${t("ph_ai_select_obj")}${o.target ? ": " + o.target : ""}`;
  return t("ph_op_" + o.op);
}
function friendly(msg, status, kind) {
  const s = String(msg || "");
  if (kind === "offline" || s === "offline") return t("ph_ai_offline");
  if (s === "no_model") return t("ph_ai_no_model");
  if (s === "no_image_model" || kind === "no_image_model") return t("ph_ai_imagegen_off") + ". " + t("ph_ai_imagegen_how");
  if (s === "ai_disabled") return t("ph_ai_disabled");
  if (status === 429 || /429|rate.?limit|quota/i.test(s)) return t("ph_ai_rate_limit");
  if (status === 404 || /not found|does not exist/i.test(s)) return t("ph_ai_model_missing") + (s ? ` (${s.slice(0, 160)})` : "");
  if (/image|vision|multimodal/i.test(s) && /support|not allowed|invalid/i.test(s)) return t("ph_ai_no_vision") + ` (${s.slice(0, 160)})`;
  return s.slice(0, 400) || t("ph_error");
}
