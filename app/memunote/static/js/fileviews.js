// Read-only viewers for audio / video, fonts and e-mail messages (.eml).
import { esc, icon, ICONS, t, api, rawUrl, API, fmtBytes, copyText, toast, debounce } from "./core.js";

const S = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
Object.assign(ICONS, {
  audio: S('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
  video: S('<rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3"/>'),
  font: S('<path d="M4 20 10 4h1l6 16M6.5 14h8"/><path d="M17 20h4"/>'),
  mail: S('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3.5 6.5 8.5 6.5 8.5-6.5"/>'),
  loop: S('<path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/><path d="M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/>'),
  paperclip: S('<path d="m21 11-8.6 8.6a5 5 0 0 1-7-7L14 4a3.4 3.4 0 0 1 4.8 4.8l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4L15.5 7.3"/>'),
});

export const VIDEO_EXT = /\.(mp4|m4v|webm|mov|ogv|mkv)$/i;
const clock = (s) => { if (!isFinite(s)) return ""; s = Math.floor(s); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };

export class MediaView {
  constructor(host, tab) {
    this.tab = tab; const video = VIDEO_EXT.test(tab.path || tab.name);
    this.el = document.createElement("div"); this.el.className = "media-view" + (video ? " is-video" : " is-audio");
    const speeds = [0.5, 0.75, 1, 1.25, 1.5, 2];
    this.el.innerHTML = `<div class="book-bar media-bar">${icon(video ? "video" : "audio")}<b class="book-title">${esc(tab.name)}</b><span class="muted small media-meta">${esc(fmtBytes(tab.size || 0))}</span><span class="spacer"></span>
      <label class="media-speed" title="${esc(t("media_speed"))}"><span class="muted small">${esc(t("media_speed"))}</span><select class="tb-select" data-m="speed">${speeds.map((s) => `<option value="${s}" ${s === 1 ? "selected" : ""}>${s}×</option>`).join("")}</select></label>
      <button class="icon-btn" data-m="loop" title="${esc(t("media_loop"))}" aria-label="${esc(t("media_loop"))}" aria-pressed="false">${icon("loop")}</button></div>
      <div class="media-stage">${video ? `<video controls preload="metadata" playsinline></video>`
        : `<div class="media-card"><span class="media-art">${icon("audio")}</span><b>${esc(tab.name.replace(/\.[^.]+$/, ""))}</b><audio controls preload="metadata"></audio></div>`}
      <p class="muted small media-error" hidden></p></div>`;
    host.append(this.el);
    this.media = this.el.querySelector("video, audio");
    const key = "mn_media_pos:" + (tab.path || tab.name);
    this.media.addEventListener("loadedmetadata", () => {
      const m = this.media, bits = [fmtBytes(tab.size || 0)];
      if (isFinite(m.duration)) bits.push(clock(m.duration));
      if (video && m.videoWidth) bits.push(`${m.videoWidth}×${m.videoHeight}`);
      this.el.querySelector(".media-meta").textContent = bits.join(" · ");
      const pos = +(localStorage.getItem(key) || 0);
      if (pos > 5 && isFinite(m.duration) && pos < m.duration - 5) m.currentTime = pos;
    });
    this.media.addEventListener("timeupdate", debounce(() => { try { localStorage.setItem(key, String(Math.floor(this.media.currentTime))); } catch {  } }, 1000));
    this.media.addEventListener("ended", () => { try { localStorage.removeItem(key); } catch {  } });
    this.media.addEventListener("error", () => {
      const p = this.el.querySelector(".media-error"); p.hidden = false; p.textContent = t("media_unsupported");
    });
    this.el.querySelector(".media-bar").addEventListener("change", (e) => { if (e.target.dataset.m === "speed") this.media.playbackRate = +e.target.value; });
    this.el.querySelector('[data-m="loop"]').onclick = (e) => { const b = e.currentTarget; this.media.loop = !this.media.loop; b.classList.toggle("on", this.media.loop); b.setAttribute("aria-pressed", String(this.media.loop)); };
    this.media.src = rawUrl(tab.path);
  }
  text() { return ""; }
  destroy() { try { this.media.pause(); this.media.removeAttribute("src"); this.media.load(); } catch {  } this.el.remove(); }
}

// ---- fonts ----
// The "name" table of a TrueType / OpenType font: family, style, version, designer, licence.
export function fontNames(buf) {
  try {
    const v = new DataView(buf); const tag = (o) => String.fromCharCode(v.getUint8(o), v.getUint8(o + 1), v.getUint8(o + 2), v.getUint8(o + 3));
    const sig = tag(0); if (!["\x00\x01\x00\x00", "OTTO", "true"].includes(sig)) return null;
    const n = v.getUint16(4); let off = -1;
    for (let i = 0; i < n; i++) { const r = 12 + i * 16; if (tag(r) === "name") { off = v.getUint32(r + 8); break; } }
    if (off < 0) return null;
    const count = v.getUint16(off + 2), strings = off + v.getUint16(off + 4); const found = {};
    for (let i = 0; i < count; i++) {
      const r = off + 6 + i * 12; const pid = v.getUint16(r), lang = v.getUint16(r + 4), id = v.getUint16(r + 6), len = v.getUint16(r + 8), so = v.getUint16(r + 10);
      if (![1, 2, 4, 5, 9, 11, 13].includes(id)) continue;
      let s = "";
      if (pid === 3 || pid === 0) { for (let k = 0; k + 1 < len; k += 2) s += String.fromCharCode(v.getUint16(strings + so + k)); }
      else if (pid === 1) { for (let k = 0; k < len; k++) s += String.fromCharCode(v.getUint8(strings + so + k)); }
      else continue;
      const score = (pid === 3 ? 2 : 1) + (lang === 0x409 || lang === 0 ? 2 : 0);
      if (s && (!found[id] || found[id].score < score)) found[id] = { s: s.slice(0, 400), score };
    }
    const g = (id) => found[id]?.s || "";
    return { family: g(1), style: g(2), full: g(4), version: g(5), designer: g(9), url: g(11), license: g(13) };
  } catch { return null; }
}

const GLYPHS = ["ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ", "абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "0123456789", "!?.,:;-–—()[]{}«»\"'@#№$%&*+=/\\<>|~^_"];
export class FontView {
  constructor(host, tab) {
    this.tab = tab; this.family = "mnfont-" + Math.random().toString(36).slice(2, 9);
    this.el = document.createElement("div"); this.el.className = "font-view";
    const sample = localStorage.getItem("mn_font_sample") || t("font_sample_text");
    this.size = +(localStorage.getItem("mn_font_size") || 40);
    this.el.innerHTML = `<div class="book-bar font-bar">${icon("font")}<b class="book-title">${esc(tab.name)}</b><span class="muted small font-meta">${esc(fmtBytes(tab.size || 0))}</span><span class="spacer"></span>
      <input class="input font-input" data-f="text" value="${esc(sample)}" placeholder="${esc(t("font_sample"))}" aria-label="${esc(t("font_sample"))}" spellcheck="false">
      <label class="font-size" title="${esc(t("font_size"))}"><input type="range" min="10" max="160" value="${this.size}" data-f="size" aria-label="${esc(t("font_size"))}"><span class="muted small">${this.size}px</span></label></div>
      <div class="font-scroll"><div class="font-info muted small"></div><div class="font-hero"></div><div class="font-ladder"></div>
      <h4 class="font-h">${esc(t("font_glyphs"))}</h4><div class="font-glyphs">${GLYPHS.map((row) => `<div class="font-row">${[...row].map((c) => `<span>${esc(c)}</span>`).join("")}</div>`).join("")}</div></div>`;
    host.append(this.el);
    const inp = this.el.querySelector('[data-f="text"]'), rng = this.el.querySelector('[data-f="size"]');
    inp.addEventListener("input", () => { this.paint(); try { localStorage.setItem("mn_font_sample", inp.value); } catch {  } });
    rng.addEventListener("input", () => { this.size = +rng.value; rng.nextElementSibling.textContent = this.size + "px"; this.paint(); try { localStorage.setItem("mn_font_size", String(this.size)); } catch {  } });
    this.paint(); this.load();
  }
  paint() {
    const text = this.el.querySelector('[data-f="text"]').value || t("font_sample_text");
    const hero = this.el.querySelector(".font-hero"); hero.textContent = text; hero.style.fontSize = this.size + "px";
    this.el.querySelector(".font-ladder").innerHTML = [12, 16, 20, 28, 36, 48, 72].map((s) => `<div class="font-step"><span class="muted small">${s}</span><div style="font-size:${s}px">${esc(text)}</div></div>`).join("");
  }
  async load() {
    try {
      const r = await fetch(rawUrl(this.tab.path), { headers: { "X-MN-Token": API.token } }); if (!r.ok) throw new Error("HTTP " + r.status);
      const buf = await r.arrayBuffer();
      const names = fontNames(buf);
      this.face = new FontFace(this.family, buf); await this.face.load(); document.fonts.add(this.face);
      this.el.querySelector(".font-scroll").style.fontFamily = `"${this.family}", var(--font-fallback, sans-serif)`;
      this.el.querySelectorAll(".font-info, .font-h, .font-step > span").forEach((x) => (x.style.fontFamily = "var(--ui-font, system-ui), sans-serif"));
      if (names) {
        const info = [names.full || [names.family, names.style].filter(Boolean).join(" "), names.version, names.designer && t("font_designer", { name: names.designer })].filter(Boolean);
        this.el.querySelector(".font-info").textContent = info.join(" · ");
        if (names.license) this.el.querySelector(".font-info").title = names.license;
      }
    } catch (e) {
      const box = this.el.querySelector(".font-info"); box.textContent = t("font_failed") + (e?.message ? ": " + e.message : ""); box.classList.add("err");
    }
  }
  text() { return ""; }
  destroy() { if (this.face) try { document.fonts.delete(this.face); } catch {  } this.el.remove(); }
}

// ---- e-mail ----
const who = (list) => (list || []).map((a) => (a.name && a.email ? `${a.name} <${a.email}>` : a.name || a.email)).join(", ");
const URL_RE = /\b(https?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]])/g;
export class EmailView {
  constructor(host, tab, hooks = {}) {
    this.tab = tab; this.hooks = hooks; const m = (this.m = tab.email || {});
    this.el = document.createElement("div"); this.el.className = "mail-view";
    let date = m.date || "";
    if (m.date_iso) { const d = new Date(m.date_iso); if (!isNaN(d)) date = d.toLocaleString(document.documentElement.lang === "en" ? "en-GB" : "ru-RU", { dateStyle: "long", timeStyle: "short" }); }
    const row = (label, value) => (value ? `<div class="mail-row"><span class="muted">${esc(label)}</span><span>${esc(value)}</span></div>` : "");
    const att = m.attachments || [];
    this.el.innerHTML = `<div class="book-bar mail-bar">${icon("mail")}<b class="book-title">${esc(tab.name)}</b><span class="spacer"></span>
      <button class="btn ghost sm" data-e="copy">${icon("copy")}${esc(t("mail_copy"))}</button>
      <button class="btn ghost sm" data-e="raw">${icon("code")}${esc(t("mail_raw"))}</button></div>
      <div class="book-scroll mail-scroll"><article class="mail-page">
        <h2 class="mail-subject">${esc(m.subject || t("mail_no_subject"))}</h2>
        <div class="mail-head">${row(t("mail_from"), who(m.from))}${row(t("mail_to"), who(m.to))}${row(t("mail_cc"), who(m.cc))}${row(t("mail_reply_to"), who(m.reply_to))}${row(t("mail_date"), date)}</div>
        ${att.length ? `<div class="mail-att"><span class="muted small">${icon("paperclip")}${esc(t("mail_attachments", { n: att.length }))}</span>${att.map((a) => `<button class="mail-chip" data-i="${a.index}" title="${esc(t("mail_save"))}">${icon("save")}<span>${esc(a.name)}</span><small>${esc(fmtBytes(a.size))}</small></button>`).join("")}</div>` : ""}
        ${m.source === "html" ? `<p class="mail-note small">${icon("info")}${esc(t("mail_html_note"))}</p>` : ""}
        <div class="mail-body"></div>${m.truncated ? `<p class="muted small">${esc(t("mail_truncated"))}</p>` : ""}
      </article></div>`;
    host.append(this.el);
    const body = this.el.querySelector(".mail-body");
    if (!m.text) body.innerHTML = `<p class="muted">${esc(t("mail_no_body"))}</p>`;
    else {
      const parts = String(m.text).split(URL_RE);
      parts.forEach((p, i) => { if (i % 2) { const a = document.createElement("a"); a.href = p; a.textContent = p; a.rel = "noopener noreferrer"; body.append(a); } else body.append(document.createTextNode(p)); });
    }
    body.addEventListener("click", (e) => { const a = e.target.closest("a[href]"); if (a) { e.preventDefault(); api("external", { url: a.href }); } });
    this.el.querySelector(".mail-bar").onclick = (e) => {
      const b = e.target.closest("[data-e]"); if (!b) return;
      if (b.dataset.e === "copy") { copyText(this.text()); toast(t("copied"), { kind: "ok", ms: 1400 }); }
      if (b.dataset.e === "raw") this.hooks.onRaw?.();
    };
    this.el.querySelector(".mail-att")?.addEventListener("click", (e) => { const c = e.target.closest(".mail-chip"); if (c) this.saveAttachment(+c.dataset.i); });
  }
  async saveAttachment(i) {
    try {
      const q = new URLSearchParams({ path: this.tab.path, index: String(i) });
      const r = await fetch("/api/eml_part?" + q, { headers: { "X-MN-Token": API.token } }); if (!r.ok) throw new Error("HTTP " + r.status);
      const name = decodeURIComponent(r.headers.get("X-MN-Name") || "") || this.m.attachments?.[i]?.name || "attachment";
      await this.hooks.onSave?.(new Uint8Array(await r.arrayBuffer()), name);
    } catch (e) { toast(t("save_failed") + ": " + (e.message || e), { kind: "error" }); }
  }
  text(limit = 400000) {
    const m = this.m;
    return [m.subject && `${t("mail_subject")}: ${m.subject}`, m.from?.length && `${t("mail_from")}: ${who(m.from)}`, m.to?.length && `${t("mail_to")}: ${who(m.to)}`,
      m.cc?.length && `${t("mail_cc")}: ${who(m.cc)}`, m.date && `${t("mail_date")}: ${m.date}`, (m.attachments || []).length && `${t("mail_attachments", { n: m.attachments.length })}: ${m.attachments.map((a) => a.name).join(", ")}`,
      "", m.text || ""].filter((x) => x !== 0 && x !== undefined && x !== false).join("\n").slice(0, limit);
  }
  destroy() { this.el.remove(); }
}
