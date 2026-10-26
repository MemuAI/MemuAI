

import { $, api, esc, icon, t, toast, showMenu, copyText, uid, debounce, palette, countWords, fmtBytes } from "./core.js";
import { renderMarkdown } from "./views.js";
import * as BigDocs from "./bigdocs.js";
import { TabContext, SheetProposal } from "./ai-multi.js";
import { CM } from "./editor.js";
import { aic, buildContext, sectionAt, resolveEdit, buildHunks, diffHtml, MARKERS_RE, Proposal, StreamWriter, ensureAIFields,
  setEditLabels, toDocHtml, docOutline } from "./ai-edit.js";

export const AI = { online: false, models: [], model: "", status: null, checking: false };
const listeners = new Set();
export const onAIStatus = (fn) => { listeners.add(fn); fn(AI); };

export async function refreshStatus(force = false) {
  if (AI.checking) return AI;
  AI.checking = true;
  try {
    const s = await api("ai/status" + (force ? "?force=1" : ""));
    AI.status = s; AI.online = !!s.online; AI.models = s.models || [];
    const pref = window.MN?.settings?.ai_model;
    AI.model = pref && AI.models.some((m) => m.id === pref) ? pref : (s.default_model || AI.models[0]?.id || "");
  } catch { AI.online = false; }
  AI.checking = false;
  listeners.forEach((fn) => fn(AI));
  return AI;
}

export const COMMANDS = [
  { id: "improve", icon: "wand", label: "ai_improve", transform: true },
  { id: "grammar", icon: "check", label: "ai_grammar", transform: true },
  { id: "rewrite", icon: "retry", label: "ai_rewrite", transform: true },
  { id: "shorten", icon: "outdent", label: "ai_shorten", transform: true },
  { id: "expand", icon: "indent", label: "ai_expand", transform: true },
  { id: "simplify", icon: "lines", label: "ai_simplify", transform: true },
  { id: "tone", icon: "sparkle", label: "ai_tone", transform: true, params: ["formal", "casual", "friendly", "professional", "confident", "simple", "academic", "persuasive", "empathetic"] },
  { id: "translate", icon: "translate", label: "ai_translate", transform: true, params: ["en", "ru", "de", "fr", "es", "it", "pt", "uk", "pl", "tr", "zh", "ja", "ko", "ar"] },
  { id: "continue", icon: "play", label: "ai_continue", transform: true, insert: true },
  { id: "summarize", icon: "outline", label: "ai_summarize", transform: false },
  { id: "explain", icon: "info", label: "ai_explain", transform: false },
  { id: "list", icon: "listUl", label: "ai_list", transform: true },
  { id: "table", icon: "table", label: "ai_table", transform: true },
  { id: "format_md", icon: "markdown", label: "ai_format_md", transform: true },
  { id: "titles", icon: "heading", label: "ai_titles", transform: false },
  { id: "outline", icon: "outline", label: "ai_outline", transform: false },
  { id: "todos", icon: "checklist", label: "ai_todos", transform: false },
  { id: "keywords", icon: "search", label: "ai_keywords", transform: false },
  { id: "comment_code", icon: "code", label: "ai_comment_code", transform: true, code: true },
  { id: "fix_code", icon: "code", label: "ai_fix_code", transform: true, code: true },
];

export function stream(body, handlers) {
  const id = uid();
  const ctrl = new AbortController();
  let finished = false;
  (async () => {
    try {
      const res = await api("ai/chat", { ...body, id, model: body.model || AI.model }, { stream: true, signal: ctrl.signal });
      if (!res.ok) { let msg = "HTTP " + res.status; try { const d = await res.json(); msg = d.error || msg; } catch {  } const err = new Error(msg); err.status = res.status; throw err; }
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!chunk.startsWith("data:")) continue;
          let ev; try { ev = JSON.parse(chunk.slice(5)); } catch { continue; }
          if (ev.t === "delta") handlers.delta?.(ev.text);
          else if (ev.t === "reasoning") handlers.reasoning?.(ev.text);
          else if (ev.t === "search") handlers.search?.(ev.data);
          else if (ev.t === "start") handlers.start?.(ev);
          else if (ev.t === "memory") handlers.memory?.(ev);
          else if (ev.t === "error") { finished = true; handlers.error?.(errorText(ev.message, ev.kind), ev.kind, ev.status, ev.message); }
          else if (ev.t === "done") { finished = true; handlers.done?.(ev); }
          else if (ev.t && ev.t.startsWith("edit")) handlers[ev.t]?.(ev);
        }
      }
      if (!finished) handlers.done?.({});
    } catch (e) {
      if (e.name === "AbortError") handlers.done?.({ cancelled: true });
      else handlers.error?.(errorText(e.message, e.status ? "http" : "offline"), e.status ? "http" : "offline", e.status || 0, e.message);
    }
  })();
  return { id, stop: () => { api("ai/stop", { id }).catch(() => {}); ctrl.abort(); } };
}
function errorText(msg, kind) {
  if (msg === "no_model") return t("ai_no_model");
  if (msg === "ai_disabled") return t("ai_disabled");
  if (kind === "offline" || /not reachable|offline/i.test(msg || "")) return t("ai_offline");
  return String(msg || t("ai_error"));
}

(function loadPanelCss() {
  if (document.querySelector('link[data-ai-panel-css]')) return;
  const link = document.createElement("link"); link.rel = "stylesheet"; link.href = "ai-panel.css?v=r40"; link.dataset.aiPanelCss = "1";
  document.head.append(link);
})();

const PRESETS = [
  { id: "summarize", icon: "outline", label: "ai_summarize", hint: "ai_p_summarize_hint", kind: "answer", grid: true },
  { id: "grammar", icon: "check", label: "ai_grammar", hint: "ai_p_grammar_hint", kind: "transform", grid: true },
  { id: "improve", icon: "wand", label: "ai_improve", hint: "ai_p_improve_hint", kind: "transform", grid: true },
  { id: "translate", icon: "translate", label: "ai_translate", hint: "ai_p_translate_hint", kind: "transform", grid: true, params: true },
  { id: "continue", icon: "play", label: "ai_continue", hint: "ai_p_continue_hint", kind: "write", grid: true },
  { id: "table", icon: "table", label: "ai_table", hint: "ai_p_table_hint", kind: "transform", grid: true },
  { id: "rewrite", icon: "retry", label: "ai_rewrite", kind: "transform" },
  { id: "shorten", icon: "outdent", label: "ai_shorten", kind: "transform" },
  { id: "expand", icon: "indent", label: "ai_expand", kind: "transform" },
  { id: "tone", icon: "sparkle", label: "ai_tone", kind: "transform", params: true },
  { id: "outline", icon: "outline", label: "ai_outline", kind: "answer" },
  { id: "todos", icon: "checklist", label: "ai_todos", kind: "answer" },
  { id: "explain", icon: "info", label: "ai_explain", kind: "answer" },
];
const OPS_KNOWN = ["replace", "replace_all", "delete", "insert", "replace_selection", "rewrite_section", "new_document", "transform"];
const SUGGESTIONS = ["ai_sug_1", "ai_sug_2", "ai_sug_3", "ai_sug_4"];
const ls = { get: (k, d) => { try { const v = localStorage.getItem("mn_ai_" + k); return v == null ? d : JSON.parse(v); } catch { return d; } }, set: (k, v) => { try { localStorage.setItem("mn_ai_" + k, JSON.stringify(v)); } catch {  } } };

export function shortModel(id) { const s = String(id || ""); const tail = s.includes("/") ? s.slice(s.lastIndexOf("/") + 1) : s; return tail || s; }
const modelLabel = (id) => { const m = AI.models.find((x) => x.id === id); return m?.label && m.label !== id ? m.label : shortModel(id); };

function errorInfo(msg, kind, status) {
  const s = String(msg || "");
  if (msg === "no_model") return { title: t("ai_no_model"), kind: "model" };
  if (msg === "ai_disabled") return { title: t("ai_disabled"), kind: "disabled" };
  if (kind === "offline" || /not reachable|offline|ECONNREFUSED|Failed to fetch|NetworkError/i.test(s)) return { title: t("ai_err_offline"), text: t("ai_offline_text"), kind: "offline" };
  if (status === 429 || /\b429\b|rate.?limit|too many requests|quota|лимит/i.test(s)) return { title: t("ai_err_429"), text: s, kind: "limit" };
  if (status === 401 || status === 403 || /unauthori[sz]ed|invalid api key|permission|forbidden/i.test(s)) return { title: t("ai_err_auth"), text: s, kind: "auth" };
  if (status === 404 || /model.{0,40}(not found|does not exist|unknown|unsupported|not available)|no available (accounts|model)|нет модели|unknown model/i.test(s)) return { title: t("ai_err_model"), text: s, kind: "model" };
  return { title: t("ai_error"), text: s, kind: "error" };
}

export class ChatPanel {

  constructor(host, ctx) {
    this.host = host; this.ctx = ctx; this.messages = []; this.busy = null; this.proposals = [];
    this.flags = { web: !!ctx.settings().ai_web_search, memory: ctx.settings().ai_memory !== false };
    this.mode = ls.get("mode", "edit"); this.auto = !!ls.get("auto", false); this.scope = "auto";
    setEditLabels({ accept: t("ai_accept_hunk"), reject: t("ai_reject_hunk") });
    host.classList.add("aip");
    host.innerHTML = `
      <header class="aip-head">
        <button class="aip-orb-btn" data-a="expand" title="${esc(t("ai_panel"))}"><span class="aip-orb">${icon("sparkle")}</span></button>
        <div class="aip-id"><b class="aip-title">MemuAI</b>
          <button class="aip-model" title="${esc(t("ai_model"))}"><span class="aip-dot"></span><span class="aip-model-name"></span>${icon("chevron", "small")}</button></div>
        <span class="spacer"></span>
        <button class="icon-btn sm" data-a="new" title="${esc(t("ai_new_chat"))}">${icon("plus")}</button>
        <button class="icon-btn sm" data-a="more" title="${esc(t("ai_more"))}">${aic("dots")}</button>
        <button class="icon-btn sm" data-a="collapse" title="${esc(t("ai_collapse"))}">${aic("collapse")}</button>
        <button class="icon-btn sm" data-a="close" title="${esc(t("close"))}">${icon("close")}</button>
      </header>
      <nav class="aip-rail">
        <button class="icon-btn" data-a="new" title="${esc(t("ai_new_chat"))}">${icon("plus")}</button>
        <span class="aip-rail-sep"></span>
        ${PRESETS.filter((p) => p.grid).map((p) => `<button class="icon-btn" data-p="${p.id}" title="${esc(t(p.label))}">${icon(p.icon)}</button>`).join("")}
        <span class="spacer"></span>
        <span class="aip-rail-badge" hidden></span>
      </nav>
      <div class="aip-log" aria-live="polite"></div>
      <div class="aip-dock">
        <div class="aip-presets" role="toolbar"></div>
        <div class="aip-compose">
          <div class="aip-scope" role="radiogroup" aria-label="${esc(t("ai_scope"))}"></div>
          <textarea rows="1" spellcheck="true"></textarea>
          <div class="aip-bar">
            <div class="aip-seg" role="tablist">
              <button data-m="chat" role="tab" title="${esc(t("ai_mode_chat_hint"))}">${aic("chat")}<span>${esc(t("ai_mode_chat"))}</span></button>
              <button data-m="edit" role="tab" title="${esc(t("ai_mode_edit_hint"))}">${aic("pen")}<span>${esc(t("ai_mode_edit"))}</span></button>
            </div>
            <button class="aip-tog" data-f="auto" title="${esc(t("ai_auto_apply_hint"))}">${aic("bolt")}<span>${esc(t("ai_auto_apply"))}</span></button>
            <span class="spacer"></span>
            <button class="aip-tog icon" data-f="web" title="${esc(t("ai_web_hint"))}">${icon("globe")}</button>
            <button class="aip-tog icon" data-f="memory" title="${esc(t("ai_memory_hint"))}">${icon("brain")}</button>
            <button class="aip-send" title="${esc(t("ai_send"))}">${icon("send")}</button>
          </div>
        </div>
      </div>`;
    this.log = $(".aip-log", host); this.input = $("textarea", host); this.send = $(".aip-send", host);
    host.addEventListener("click", (e) => this.onClick(e));
    $(".aip-model", host).onclick = (e) => this.pickModel(e.currentTarget);
    this.tabCtx = new TabContext(this, ctx);
    this.input.addEventListener("input", () => { this.autosize(); this.tabCtx.onInput(this.input); });
    this.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.submit(); }
      else if (e.key === "Escape" && this.busy) { e.preventDefault(); this.stop(); }
    });
    this.send.onclick = () => (this.busy ? this.stop() : this.submit());

    this.refreshScope = debounce(() => this.renderScope(), 160);
    host._aipOwner = this;
    this._sel = () => { if (this.dead()) return; if (!this.host.hidden) this.refreshScope(); };
    document.addEventListener("selectionchange", this._sel);
    document.addEventListener("keyup", this._sel, true);
    document.addEventListener("pointerup", this._sel, true);
    this.host.addEventListener("pointerenter", this._sel);
    document.addEventListener("input", this._sel, true);
    this.input.addEventListener("focus", this._sel);

    this._sig = "";
    this._poll = setInterval(() => {
      if (this.dead()) return;
      if (this.host.hidden || document.hidden || this.host.classList.contains("collapsed")) return;
      const ed = this.ctx.editor?.(); if (!ed) return;
      const r = ed.view?.state.selection.main;
      const sig = `${ed.tab.id}|${ed.view ? ed.view.state.doc.length : ""}|${r ? r.from + ":" + r.to : ""}`;
      if (sig === this._sig) return;
      this._sig = sig; this.renderScope();
      if (this.host.classList.contains("is-empty") && AI.online) this.renderEmpty();
    }, 900);
    if (ls.get("collapsed", false)) this.setCollapsed(true, true);
    this.renderMode(); this.renderFlags(); this.renderPresets(); this.renderScope(); this.renderEmpty();
    this._statusFn = () => this.renderStatus();
    onAIStatus(this._statusFn);
  }

  dead() {
    if (this.host._aipOwner === this) return false;
    clearInterval(this._poll);
    for (const ev of ["selectionchange", "keyup", "pointerup", "input"]) document.removeEventListener(ev, this._sel, ev !== "selectionchange");
    this.busy?.stop();
    return true;
  }

  onClick(e) {

    if (this.dead()) return;
    const link = e.target.closest("a[href], a[data-url]");
    if (link && this.log.contains(link)) {
      e.preventDefault();
      const href = link.dataset.url || link.getAttribute("href") || "";
      if (/^(https?:|mailto:)/i.test(href)) api("external", { url: href });
      return;
    }
    const a = e.target.closest("[data-a]")?.dataset.a;
    if (a === "new") this.reset();
    else if (a === "close") this.ctx.close?.();
    else if (a === "collapse") this.setCollapsed(true);
    else if (a === "expand") { if (this.host.classList.contains("collapsed")) this.setCollapsed(false); }
    else if (a === "more") this.moreMenu(e.target.closest("[data-a]"));
    const f = e.target.closest("[data-f]")?.dataset.f;
    if (f === "auto") { this.auto = !this.auto; ls.set("auto", this.auto); this.renderFlags(); toast(t(this.auto ? "ai_auto_on" : "ai_auto_off")); }
    else if (f) { this.flags[f] = !this.flags[f]; this.renderFlags(); }
    const m = e.target.closest("[data-m]")?.dataset.m;
    if (m) { this.mode = m; ls.set("mode", m); this.renderMode(); this.input.focus(); }
    const tc = e.target.closest("[data-tabctx]")?.dataset.tabctx;
    if (tc) this.tabCtx.toggle(tc);
    const sc = e.target.closest("[data-scope]")?.dataset.scope;
    if (sc) { this.scope = this.scope === sc ? "auto" : sc; this.renderScope(); }
    const p = e.target.closest("[data-p]");
    if (p) { if (this.host.classList.contains("collapsed")) this.setCollapsed(false); this.preset(p.dataset.p, null, p); }
    const sug = e.target.closest("[data-sug]");
    if (sug) { this.mode = "edit"; ls.set("mode", "edit"); this.renderMode(); this.submit(sug.dataset.text); }
    const code = e.target.closest("[data-code]");
    if (code) this.codeAction(code);
  }
  setCollapsed(on, initial = false) {
    const host = this.host;
    if (on) { if (!initial) this._basis = host.style.flexBasis; host.classList.add("collapsed"); host.style.flexBasis = "52px"; }
    else { host.classList.remove("collapsed"); host.style.flexBasis = this._basis || (localStorage.getItem("mn_ai_w") ? localStorage.getItem("mn_ai_w") + "px" : ""); }
    const gutter = host.previousElementSibling; if (gutter?.classList.contains("ai-gutter")) gutter.classList.toggle("aip-gutter-off", on);
    ls.set("collapsed", on);
    if (!on && !initial) this.input.focus();
  }
  moreMenu(anchor) {
    showMenu([
      { label: t("ai_auto_apply"), icon: "follow", checked: this.auto, run: () => { this.auto = !this.auto; ls.set("auto", this.auto); this.renderFlags(); } },
      { label: t("ai_web"), icon: "globe", checked: this.flags.web, run: () => { this.flags.web = !this.flags.web; this.renderFlags(); } },
      { label: t("ai_memory"), icon: "brain", checked: this.flags.memory, run: () => { this.flags.memory = !this.flags.memory; this.renderFlags(); } },
      "-",
      { label: t("ai_copy_chat"), icon: "copy", disabled: !this.messages.length, run: async () => { await copyText(this.messages.map((m) => (m.role === "user" ? "> " : "") + m.content).join("\n\n")); toast(t("copied"), { kind: "ok" }); } },
      { label: t("ai_new_chat"), icon: "plus", run: () => this.reset() },
      { label: t("ai_model"), icon: "sparkle", run: () => this.pickModel(anchor) },
    ], anchor, { alignRight: true });
  }
  autosize() { this.input.style.height = "auto"; this.input.style.height = Math.min(200, this.input.scrollHeight) + "px"; }
  renderMode() {
    this.host.querySelectorAll("[data-m]").forEach((b) => { b.classList.toggle("on", b.dataset.m === this.mode); b.setAttribute("aria-selected", b.dataset.m === this.mode); });
    this.host.classList.toggle("mode-edit", this.mode === "edit");
    this.input.placeholder = t(this.mode === "edit" ? "ai_ph_edit" : "ai_ph_chat");
  }
  renderFlags() {
    this.host.querySelectorAll("[data-f]").forEach((b) => b.classList.toggle("on", b.dataset.f === "auto" ? this.auto : !!this.flags[b.dataset.f]));
  }
  renderStatus() {
    if (this.dead()) { listeners.delete(this._statusFn); return; }
    const name = AI.online ? (AI.model ? modelLabel(AI.model) : t("ai_no_model_short")) : t("ai_offline_short");
    $(".aip-model-name", this.host).textContent = name;
    $(".aip-model", this.host).title = AI.online ? `${t("ai_model")}: ${AI.model}` : t("ai_offline");
    this.host.classList.toggle("offline", !AI.online);
    if (!this.messages.length) this.renderEmpty();
  }
  renderPresets() {
    $(".aip-presets", this.host).innerHTML = PRESETS.map((p) => `<button class="aip-chip" data-p="${p.id}" title="${esc(t(p.hint || p.label))}">${icon(p.icon)}<span>${esc(t(p.label))}</span>${p.params ? icon("chevron", "small") : ""}</button>`).join("");
  }

  snapshot() {
    const ed = this.ctx.editor?.(); if (!ed) return null;
    const snap = { tabId: ed.tab.id, name: ed.name, kind: ed.kind, readOnly: ed.readOnly, offset: 0, view: ed.view, doc: ed.doc };
    if (ed.view) {
      const st = ed.view.state, r = st.selection.main, len = st.doc.length;
      snap.total = len;
      if (len > 8e6) { const a = Math.max(0, r.from - 300000), b = Math.min(len, r.to + 300000); snap.offset = a; snap.text = st.sliceDoc(a, b); }
      else snap.text = st.doc.toString();
      snap.selFrom = r.from - snap.offset; snap.selTo = r.to - snap.offset; snap.cursor = r.head - snap.offset;
    } else if (ed.doc) {
      ed.doc.saveRange();
      snap.text = ed.doc.text(); snap.total = snap.text.length;
      const sel = ed.doc.selectionText();
      const at = sel ? snap.text.indexOf(sel) : -1;
      snap.selFrom = at >= 0 ? at : snap.text.length; snap.selTo = at >= 0 ? at + sel.length : snap.text.length; snap.cursor = snap.selTo;
      snap.selText = sel;
    } else if (ed.tab.kind === "sheet" && ed.tab.comp?.plan) {
      snap.text = ed.tab.comp.text(); snap.total = snap.text.length; snap.selFrom = snap.selTo = snap.cursor = 0;
      snap.sheet = true; snap.noEdit = !ed.tab.sheetEditable;
    } else { snap.text = ed.text?.() || ""; snap.total = snap.text.length; snap.selFrom = snap.selTo = snap.cursor = 0; snap.noEdit = true; }
    return snap;
  }
  renderScope() {
    const box = $(".aip-scope", this.host); if (!box) return;
    const snap = this.snapshot();
    if (!snap) { box.innerHTML = ""; return; }
    const hasSel = snap.selTo > snap.selFrom;
    const sec = snap.text && snap.text.length < 8e6 ? sectionAt(snap.text, snap.kind, snap.selFrom) : null;
    let eff = this.scope;
    if (eff === "auto") eff = hasSel ? "selection" : "document";
    if (eff === "selection" && !hasSel) eff = "document";
    if (eff === "section" && !sec) eff = "document";
    this.effScope = eff;
    const selLines = hasSel ? snap.text.slice(snap.selFrom, snap.selTo).split("\n").length : 0;
    const chip = (id, ic, label, sub, disabled) => `<button class="aip-ctx ${eff === id ? "on" : ""}" data-scope="${id}" role="radio" aria-checked="${eff === id}" ${disabled ? "disabled" : ""} title="${esc(t(`ai_scope_${id}_hint`))}">${aic(ic)}<span class="l">${esc(label)}</span>${sub ? `<span class="s">${esc(sub)}</span>` : ""}</button>`;
    const docSub = snap.total < 2e5 ? t("ai_words_n", { n: countWords(snap.text) }) : fmtBytes(snap.total);
    box.innerHTML = chip("document", "docScope", snap.name || t("ai_scope_document"), docSub, false)
      + (hasSel || this.scope === "selection" ? chip("selection", "selScope", t("ai_scope_selection"), t("ai_lines_n", { n: selLines }), !hasSel) : "")
      + (sec ? chip("section", "section", "§ " + sec.title.slice(0, 40), "", false) : "");
    BigDocs.bar(this, box, snap);
    box.insertAdjacentHTML("beforeend", this.tabCtx.html(snap.total || 0));
  }
  renderEmpty() {
    const has = this.messages.length || this.log.querySelector(".msg");
    this.host.classList.toggle("is-empty", !has);
    if (has) return;
    if (!AI.online) {
      this.log.innerHTML = `<div class="aip-empty off">
        <div class="aip-hero"><span class="aip-orb big off">${icon("warn")}</span><h3>${esc(t("ai_offline_title"))}</h3><p>${esc(t("ai_offline_text"))}</p>
        <button class="btn sm primary" data-retry>${icon("retry")}${esc(t("retry"))}</button></div></div>`;
      this.log.querySelector("[data-retry]")?.addEventListener("click", (e) => { e.currentTarget.classList.add("spin"); refreshStatus(true); });
      return;
    }
    const snap = this.snapshot();
    const name = snap?.name || "";
    const empty = !snap || !snap.text.trim();
    this.log.innerHTML = `<div class="aip-empty">
      <div class="aip-hero"><span class="aip-orb big">${icon("sparkle")}</span>
        <h3>${esc(empty ? t("ai_hello_empty") : t("ai_hello", { name }))}</h3>
        <p>${esc(t("ai_hello_text"))}</p></div>
      <div class="aip-grid">${PRESETS.filter((p) => p.grid).map((p) => `<button class="aip-card-btn" data-p="${p.id}"><span class="aip-card-ic">${icon(p.icon)}</span><b>${esc(t(p.label))}</b><small>${esc(t(p.hint))}</small></button>`).join("")}</div>
      <div class="aip-suggest"><div class="aip-suggest-t">${esc(t("ai_try"))}</div>${(empty ? ["ai_sug_e1", "ai_sug_e2", "ai_sug_e3"] : SUGGESTIONS).map((k) => `<button class="aip-sug" data-sug data-text="${esc(t(k))}">${aic("pen")}<span>${esc(t(k))}</span>${icon("chevronRight", "small")}</button>`).join("")}</div>
    </div>`;
  }
  pickModel(anchor) {
    if (!AI.online) { refreshStatus(true); return; }

    const items = AI.models.map((m) => ({ label: m.label && m.label !== m.id ? m.label : shortModel(m.id), hint: m.label && m.label !== m.id ? m.id : (m.id !== shortModel(m.id) ? m.id.slice(0, m.id.length - shortModel(m.id).length).replace(/[-/]+$/, "") : (m.family || m.provider)), checked: m.id === AI.model,
      run: () => { AI.model = m.id; this.ctx.saveSetting({ ai_model: m.id }); listeners.forEach((fn) => fn(AI)); } }));
    if (items.length > 14) { palette(items, { placeholder: t("ai_model_search") }); return; }
    showMenu([{ header: t("ai_model") }, ...items], anchor);
  }

  adopt(old) {
    if (!old || old === this) return;
    old.busy?.stop(); old.busy = null;
    this.messages = old.messages || []; this.proposals = old.proposals || [];
    if (old.log?.querySelector(".msg")) {
      this.log.innerHTML = ""; this.host.classList.remove("is-empty");
      this.log.append(...old.log.childNodes);
      this.updateBadge(); this.scrollDown(true);
    }
  }
  reset() {
    this.stop();
    for (const p of this.proposals) p.dispose();
    this.proposals = []; this.messages = []; this.log.innerHTML = ""; this.renderEmpty(); this.updateBadge(); this.input.focus();
  }
  focus(prefill) { if (prefill != null) { this.input.value = prefill; this.autosize(); } if (this.host.classList.contains("collapsed")) this.setCollapsed(false); this.input.focus(); }
  addBubble(role, html) {
    if (!this.log.querySelector(".msg")) { this.log.innerHTML = ""; this.host.classList.remove("is-empty"); }
    const el = document.createElement("div"); el.className = "msg " + role; el.innerHTML = html; this.log.append(el);
    this.scrollDown(true); return el;
  }
  scrollDown(force) { const l = this.log; if (force || l.scrollHeight - l.scrollTop - l.clientHeight < 160) l.scrollTop = l.scrollHeight; }
  userBubble(text, tag) {
    return this.addBubble("user", `<div class="msg-body">${esc(text)}</div>${tag ? `<div class="msg-tag">${tag}</div>` : ""}`);
  }
  scopeTag(scope, snap) {
    if (!snap) return "";
    if (scope === "selection") return `${aic("selScope")}${esc(t("ai_scope_selection"))}`;
    if (scope === "section") { const s = sectionAt(snap.text, snap.kind, snap.selFrom); return `${aic("section")}§ ${esc((s?.title || "").slice(0, 40))}`; }
    return `${aic("docScope")}${esc(snap.name || t("ai_scope_document"))}`;
  }
  assistantBubble() {
    const b = this.addBubble("assistant", `<div class="msg-head"><span class="aip-orb sm">${icon("sparkle")}</span><span class="msg-model"></span><span class="msg-pills"></span></div>
      <div class="msg-body md"><span class="typing"><i></i><i></i><i></i></span></div><div class="msg-extra"></div><div class="msg-sources" hidden></div><div class="msg-actions"></div>`);
    return b;
  }
  setBusy(on) {
    this.send.innerHTML = on ? icon("stop") : icon("send");
    this.send.classList.toggle("stop", on);
    this.send.title = on ? t("ai_stop") + " (Esc)" : t("ai_send");
    this.host.classList.toggle("busy", on);
    if (!on) this.busy = null;
  }
  stop() { if (this.busy) { this.busy.stop(); this.setBusy(false); } }
  guard() {
    if (this.busy) return false;
    if (!AI.online) { toast(t("ai_offline"), { kind: "error" }); refreshStatus(true); return false; }
    return true;
  }
  showError(bubble, message, kind, status, retry) {
    const info = errorInfo(message, kind, status);
    const body = $(".msg-body", bubble);
    requestAnimationFrame(() => this.scrollDown(true));
    body.classList.remove("md");
    body.innerHTML = `<div class="aip-err ${info.kind}"><div class="aip-err-t">${icon("warn")}<b>${esc(info.title)}</b></div>${info.text && info.text !== info.title ? `<div class="aip-err-x">${esc(String(info.text).slice(0, 400))}</div>` : ""}
      <div class="aip-err-a">${retry ? `<button class="btn sm" data-r="retry">${icon("retry")}${esc(t("retry"))}</button>` : ""}${info.kind === "model" || info.kind === "limit" || info.kind === "auth" ? `<button class="btn sm ghost" data-r="model">${icon("sparkle")}${esc(t("ai_pick_model"))}</button>` : ""}${info.kind === "offline" ? `<button class="btn sm ghost" data-r="check">${icon("retry")}${esc(t("ai_check_proxy"))}</button>` : ""}</div></div>`;
    body.onclick = (e) => {
      const r = e.target.closest("[data-r]")?.dataset.r; if (!r) return;
      if (r === "retry") { bubble.remove(); retry?.(); }
      else if (r === "model") this.pickModel(e.target.closest("[data-r]"));
      else if (r === "check") refreshStatus(true).then(() => toast(AI.online ? t("ai_online") : t("ai_offline"), { kind: AI.online ? "ok" : "error" }));
    };
  }
  renderBody(body, text, sources = []) {
    const l = this.log, pinned = l.scrollHeight - l.scrollTop - l.clientHeight < 80;
    body.innerHTML = citeLinks(renderMarkdown(text) || "", sources);
    if (this.bigdocRefs) BigDocs.decorate(body, this.bigdocRefs);
    enhanceCode(body);
    if (pinned) l.scrollTop = l.scrollHeight;
  }

  submit(text) {
    text = (text ?? this.input.value).trim();
    if (!text || !this.guard()) return;
    this.input.value = ""; this.autosize();
    this.tabCtx.mentions(text);
    const go = (docs) => { if (this.mode === "edit") this.runAgent(text, { docs }); else this.runChat(text, docs); };
    if (!this.tabCtx.picked.size) { go([]); return; }
    this.input.placeholder = t("ai_ctx_loading");
    this.tabCtx.collect().then(go, () => go([])).finally(() => this.renderMode());
  }

  docsTag(docs) { return docs?.length ? docs.map((d) => `<span class="msg-doc">${aic("docScope")}${esc(d.name)}${d.truncated ? ` <small>${esc(t("ai_ctx_trunc"))}</small>` : ""}</span>`).join("") : ""; }
  docsBody(docs) { return docs?.length ? docs.map((d) => ({ id: d.ref, name: d.name, kind: d.kind, text: d.text, total_chars: d.total_chars, truncated: d.truncated })) : undefined; }

  ask(action, inputText, extra = {}) {
    if (!this.guard()) return;
    const label = t(COMMANDS.find((c) => c.id === action)?.label || action);
    const snap = this.snapshot();
    this.addBubble("user", `<div class="msg-body">${esc(label)}${inputText ? `<div class="quote">${esc(inputText.slice(0, 400))}${inputText.length > 400 ? "…" : ""}</div>` : ""}</div>`);
    this.messages.push({ role: "user", content: label + (inputText ? "\n\n" + inputText.slice(0, 2000) : "") });
    this.runAnswer({ action, input: inputText, ...extra }, snap);
  }
  contextFor(snap, scope) {
    if (!snap) return { ctx: { name: "", kind: "text", text: "" }, built: null };
    const built = buildContext({ text: snap.text, kind: snap.kind, selFrom: snap.selFrom, selTo: snap.selTo, cursor: snap.cursor, scope, limit: Math.min(+this.ctx.settings().ai_context_chars || 60000, 60000) });
    if (snap.kind === "document" && snap.doc) built.outline = docOutline(snap.doc.page);
    if (snap.offset || snap.total > snap.text.length) { built.truncated = true; built.total_chars = snap.total; }
    const ctx = { name: snap.name, kind: snap.kind, ...built };
    delete ctx.range; delete ctx.section;
    return { ctx, built };
  }
  runChat(text, docs = []) {
    const snap = this.snapshot(); const scope = this.effScope || "auto";
    this.userBubble(text, this.scopeTag(scope, snap) + this.docsTag(docs));
    this.messages.push({ role: "user", content: text });
    this.bigdocRefs = null;
    if (BigDocs.applies(this, snap) && !docs.length) { BigDocs.answer(this, text, snap, AI.model); return; }
    this.runAnswer({ action: "chat", docs: this.docsBody(docs) }, snap, scope);
  }

  runAnswer(req, snap, scope = "auto") {
    const { ctx } = this.contextFor(snap, scope);
    const bubble = this.assistantBubble();
    const body = $(".msg-body", bubble);
    let text = "", sources = [];
    this.setBusy(true);
    const render = debounce(() => { this.renderBody(body, text, sources); this.scrollDown(); }, 40);
    const legacy = { ...ctx, text: String(ctx.text || "").replace(MARKERS_RE, "") };
    this.busy = stream({
      ...req, messages: req.action === "chat" ? this.messages : undefined,
      context: legacy, with_document: true, input: req.input ?? (ctx.selection || undefined),
      web_search: this.flags.web, memory: this.flags.memory, pos: ctx.excerpt_pos,
    }, {
      start: (ev) => this.head(bubble, ev),
      delta: (d) => { text += d; render(); },
      reasoning: () => { if (!text) body.innerHTML = `<span class="typing"><i></i><i></i><i></i></span><span class="muted small"> ${esc(t("ai_thinking"))}</span>`; },
      search: (data) => { sources = this.searchPill(bubble, data, sources); render(); },
      memory: (ev) => { if (ev.added || ev.updated) toast(t("ai_memory_saved"), { kind: "ok" }); },
      error: (msg, kind, status, raw) => { this.setBusy(false); render.cancel(); this.showError(bubble, raw ?? msg, kind, status, () => { this.messages.pop(); this.retryLast(req, snap, scope); }); },
      done: (info) => {
        render.flush(); this.setBusy(false);
        if (!text) { if (!body.querySelector(".aip-err")) body.innerHTML = `<span class="muted">${esc(t(info?.cancelled ? "ai_stopped" : "ai_empty_answer"))}</span>`; return; }
        this.messages.push({ role: "assistant", content: text });
        this.answerActions(bubble, text, info?.cancelled);
      },
    });
  }
  retryLast(req, snap, scope) {
    const lastUser = [...this.messages].reverse().find((m) => m.role === "user");
    if (req.action === "chat" && lastUser) { this.messages.push(lastUser); this.runAnswer(req, this.snapshot() || snap, scope); }
    else this.runAnswer(req, this.snapshot() || snap, scope);
  }
  head(bubble, ev) {
    $(".msg-model", bubble).textContent = modelLabel(ev.model);
    if (ev.memory_facts) $(".msg-pills", bubble).insertAdjacentHTML("beforeend", `<span class="pill" title="${esc(t("ai_memory_used"))}">${icon("brain")}${ev.memory_facts}</span>`);
  }
  searchPill(bubble, data, sources) {
    const q = data?.query || data?.queries?.join(", ");
    const pills = $(".msg-pills", bubble);
    if (q && !pills.querySelector(".pill.web")) pills.insertAdjacentHTML("beforeend", `<span class="pill web" title="${esc(String(q))}">${icon("globe")}${esc(String(q).slice(0, 40))}</span>`);
    const rows = Array.isArray(data?.sources) ? data.sources.filter((x) => x && /^https?:/i.test(String(x.url || ""))) : [];
    if (rows.length) { sources = rows.slice(0, 12); renderSources(bubble, sources); }
    return sources;
  }
  answerActions(bubble, text, cancelled) {
    const bar = $(".msg-actions", bubble);
    const code = /```[\w-]*\n([\s\S]*?)```/.exec(text);
    const payload = () => (code ? code[1] : text);
    bar.innerHTML = `${cancelled ? `<span class="muted small">${esc(t("ai_stopped"))}</span>` : ""}
      <button class="aip-act" data-x="insert" title="${esc(t("ai_insert"))}">${aic("insert")}<span>${esc(t("ai_insert"))}</span></button>
      <button class="aip-act" data-x="replace" title="${esc(t("ai_replace"))}">${icon("replace")}<span>${esc(t("ai_replace"))}</span></button>
      <button class="aip-act" data-x="copy" title="${esc(t("copy"))}">${aic("copy")}</button>
      <button class="aip-act" data-x="tab" title="${esc(t("ai_new_tab"))}">${aic("newdoc")}</button>`;
    bar.onclick = async (e) => {
      const x = e.target.closest("[data-x]")?.dataset.x; if (!x) return;
      if (x === "insert") this.ctx.insert(payload());
      if (x === "replace") this.ctx.replace(payload());
      if (x === "copy") { await copyText(text); toast(t("copied"), { kind: "ok" }); }
      if (x === "tab") this.ctx.newTab(text);
    };
  }
  codeAction(btn) {
    const pre = btn.closest(".aip-code")?.querySelector("pre code"); if (!pre) return;
    const text = pre.textContent.replace(/\n$/, "");
    if (btn.dataset.code === "copy") copyText(text).then(() => toast(t("copied"), { kind: "ok" }));
    else if (btn.dataset.code === "insert") this.ctx.insert(text);
  }

  target(snap) {
    const tabId = snap.tabId;
    const kind = snap.view ? "cm" : snap.doc ? "doc" : "none";
    return {
      kind, tabId,
      view: () => this.ctx.viewFor?.(tabId) || null,
      doc: () => this.ctx.docFor?.(tabId) || null,
      activate: () => this.ctx.activate?.(tabId),
      newTab: (text, title, k) => this.newDocTab(text, title, k),
      toast: (key) => toast(t(key), { kind: "error" }),
    };
  }

  multiTarget(d) {
    const id = d.id;
    return { kind: d.editKind === "doc" ? "doc" : "cm", tabId: id, title: d.name,
      view: () => this.ctx.viewFor?.(id) || null,
      doc: () => this.ctx.docFor?.(id) || (this.ctx.activate?.(id), this.ctx.docFor?.(id)) || null,
      activate: () => this.ctx.activate?.(id),
      newTab: (text, title, k) => this.newDocTab(text, title, k),
      toast: (key) => toast(t(key), { kind: "error" }) };
  }
  sheetTarget(id, title) {
    return { kind: "sheet", tabId: id, title, grid: () => this.ctx.sheetFor?.(id) || null, data: () => this.ctx.sheetData?.(id),
      activate: () => this.ctx.activate?.(id), newTab: (text, name, k) => this.newDocTab(text, name, k) };
  }
  newDocTab(text, title, kind) {
    const ext = { csv: ".csv", markdown: ".md", text: ".txt" }[kind] || "";
    this.ctx.newTab(text, { title: title ? (/\.\w{1,5}$/.test(title) ? title : title + ext) : undefined, kind: kind === "csv" ? "csv" : kind });
  }

  addTo(p, edit, d) {
    if (p instanceof SheetProposal) { p.add(edit); return; }
    if (edit.op === "new_document") { p.add([{ from: 0, to: 0, insert: edit.text }], { op: edit.op, virtual: true, title: edit.title, docKind: edit.kind, incomplete: edit.incomplete }); return; }
    const full = d.full || "", end = full.length;
    const r = resolveEdit(edit, full, { cursor: end, selFrom: end, selTo: end, kind: d.editKind === "doc" ? "markdown" : d.kind });
    if (d.editKind === "doc") {
      if (r.error && edit.op !== "insert") { p.fail({ op: edit.op, error: r.error, find: edit.find || edit.anchor || edit.heading }); return; }
      p.add([{ from: 0, to: 0, insert: edit.text, del: r.changes[0] ? full.slice(r.changes[0].from, r.changes[0].to) : edit.find || "" }],
        { op: edit.op, at: edit.at, find: edit.find, anchor: edit.anchor, heading: edit.heading, incomplete: edit.incomplete, docOnly: true });
      return;
    }
    if (r.error) { p.fail({ op: edit.op, error: r.error, find: edit.find || edit.anchor || edit.heading }); return; }
    p.add(r.changes.map((c) => ({ from: c.from, to: c.to, insert: c.insert, del: full.slice(c.from, c.to) })), { op: edit.op, incomplete: edit.incomplete });
  }
  runAgent(text, opts = {}) {
    const snap = this.snapshot();
    const scope = opts.scope || this.effScope || "auto";
    const docs = opts.docs || [];
    if (!opts.silentUser) this.userBubble(opts.label || text, this.scopeTag(scope, snap) + this.docsTag(docs));
    this.messages.push({ role: "user", content: text });
    const { ctx, built } = this.contextFor(snap, scope);
    const bubble = this.assistantBubble();
    const body = $(".msg-body", bubble), extra = $(".msg-extra", bubble);
    const proposal = snap && !snap.noEdit ? (snap.sheet ? new SheetProposal(this.sheetTarget(snap.tabId, docs.length ? snap.name : ""), (p) => this.renderCard(card, p))
      : new Proposal(this.target(snap), (p) => this.renderCard(card, p))) : null;
    if (proposal && docs.length) proposal.target.title = snap.name;
    const card = document.createElement("div"); card.className = "aip-card"; card.hidden = true; extra.append(card);
    if (proposal) { this.proposals.push(proposal); card._proposal = proposal; }

    const extras = new Map();
    const extraFor = (ref) => {
      if (extras.has(ref)) return extras.get(ref);
      const d = docs.find((x) => x.ref === ref); if (!d) return null;
      const c = document.createElement("div"); c.className = "aip-card"; c.hidden = true; c._live = new Map(); extra.append(c);
      const p = d.readOnly || d.editKind === "none" ? null : d.editKind === "sheet" ? new SheetProposal(this.sheetTarget(d.id, d.name), (q) => this.renderCard(c, q)) : new Proposal(this.multiTarget(d), (q) => this.renderCard(c, q));
      if (p) { this.proposals.push(p); c._proposal = p; }
      const x = { proposal: p, card: c, doc: d }; extras.set(ref, x); return x;
    };
    const SHEET_OPS = ["set_cells", "add_column", "add_sheet"];
    const routeOf = (edit) => {
      if (edit.doc && edit.doc !== "d0") return edit.doc;
      if (!edit.doc && SHEET_OPS.includes(edit.op) && !snap?.sheet) {
        const sheets = docs.filter((d) => d.editKind === "sheet");
        return (sheets.find((d) => edit.sheet && d.full.includes("## Sheet: " + edit.sheet + " (")) || sheets[0])?.ref || "";
      }
      return "";
    };
    const live = new Map();
    card._live = live;
    let prose = "", sources = [];
    const editsLog = [];
    this.setBusy(true);

    let userScrolled = false;
    const markScrolled = (e) => { if (!(e.deltaY > 0)) userScrolled = true; };
    this.log.addEventListener("wheel", markScrolled, { passive: true });
    const follow = () => { if (!userScrolled) this.scrollDown(true); };
    const unfollow = () => this.log.removeEventListener("wheel", markScrolled);
    const render = debounce(() => { if (prose.trim()) this.renderBody(body, cleanProse(prose), sources); this.scrollDown(); }, 40);
    const info = () => { const s = this.snapshot(); return s && s.tabId === snap?.tabId ? s : snap; };
    const onEdit = (edit) => {
      live.delete(edit.index);
      editsLog.push(edit);
      const ref = docs.length ? routeOf(edit) : "";
      if (ref) {
        const x = extraFor(ref);
        if (x?.proposal) { this.addTo(x.proposal, edit, x.doc); x.card.hidden = false; this.renderCard(x.card, x.proposal); }
        else if (proposal) proposal.fail({ op: edit.op, error: x ? "read_only" : "no_doc", find: x ? x.doc.name : edit.doc });
        return;
      }
      if (!proposal) return;
      if (proposal instanceof SheetProposal) { proposal.add(edit); return; }
      if (edit.op === "new_document") { proposal.add([{ from: 0, to: 0, insert: edit.text }], { op: edit.op, virtual: true, title: edit.title, docKind: edit.kind, incomplete: edit.incomplete }); return; }
      const cur = info();
      if (proposal.target.kind === "doc") {

        const r = resolveEdit(edit, cur.text, { cursor: cur.cursor, selFrom: cur.selFrom, selTo: cur.selTo, kind: "markdown" });
        if (r.error && !["insert"].includes(edit.op)) { proposal.fail({ op: edit.op, error: r.error, find: edit.find || edit.anchor || edit.heading }); return; }
        proposal.add([{ from: 0, to: 0, insert: edit.text, del: edit.op === "replace_selection" ? cur.selText || "" : (r.changes[0] ? cur.text.slice(r.changes[0].from, r.changes[0].to) : edit.find || "") }],
          { op: edit.op, at: edit.at, find: edit.find, anchor: edit.anchor, heading: edit.heading, incomplete: edit.incomplete, docOnly: true });
        return;
      }
      const r = resolveEdit(edit, cur.text, { cursor: cur.cursor, selFrom: cur.selFrom, selTo: cur.selTo, kind: snap.kind === "document" ? "markdown" : snap.kind });
      if (r.error) { proposal.fail({ op: edit.op, error: r.error, find: edit.find || edit.anchor || edit.heading }); return; }
      proposal.add(r.changes.map((c) => ({ from: c.from + cur.offset, to: c.to + cur.offset, insert: c.insert })), { op: edit.op, incomplete: edit.incomplete });
    };
    this.busy = stream({
      action: "agent", messages: this.messages, context: ctx, input: built?.selection || "",
      web_search: this.flags.web, memory: this.flags.memory, docs: this.docsBody(docs),
    }, {
      start: (ev) => this.head(bubble, ev),
      delta: (d) => { prose += d; render(); },
      reasoning: () => { if (!prose) body.innerHTML = `<span class="typing"><i></i><i></i><i></i></span><span class="muted small"> ${esc(t("ai_thinking"))}</span>`; },
      search: (data) => { sources = this.searchPill(bubble, data, sources); render(); },
      edit_start: (ev) => { live.set(ev.index, { op: ev.op, chars: 0, preview: "" }); card.hidden = false; this.renderCard(card, proposal); if (!prose.trim()) body.innerHTML = `<span class="muted small aip-writing">${aic("pen")}${esc(t("ai_writing_edits"))}</span>`; },
      edit_progress: (ev) => { const l = live.get(ev.index); if (l) { l.chars = ev.chars; l.preview = ev.preview; this.renderCard(card, proposal); } },
      edit: (ev) => { onEdit(ev); card.hidden = false; this.renderCard(card, proposal); follow(); },
      edit_invalid: (ev) => { live.delete(ev.index); proposal?.fail({ op: "?", error: "invalid" }); },
      memory: (ev) => { if (ev.added || ev.updated) toast(t("ai_memory_saved"), { kind: "ok" }); },
      error: (msg, kind, status, raw) => { this.setBusy(false); render.cancel(); live.clear(); unfollow(); this.showError(bubble, raw ?? msg, kind, status, () => { this.messages.pop(); this.runAgent(text, { ...opts, docs, silentUser: true }); }); if (proposal?.hunks.length) this.renderCard(card, proposal); },
      done: (doneInfo) => {
        render.flush(); this.setBusy(false); live.clear();
        for (const x of extras.values()) {
          if (!x.proposal) continue;
          x.card.hidden = !x.proposal.hunks.length;
          if (this.auto && x.proposal.pending.length) x.proposal.applyAll();
          this.renderCard(x.card, x.proposal);
        }
        const anyHunks = !!proposal?.hunks.length || [...extras.values()].some((x) => x.proposal?.hunks.length);
        if (!prose.trim()) {
          if (anyHunks) body.innerHTML = `<span class="muted small">${esc(t("ai_edits_ready"))}</span>`;
          else if (!body.querySelector(".aip-err")) body.innerHTML = `<span class="muted">${esc(t(doneInfo?.cancelled ? "ai_stopped" : "ai_empty_answer"))}</span>`;
        }
        if (proposal) {
          card.hidden = !proposal.hunks.length;
          if (this.auto && proposal.pending.length) proposal.applyAll();
          this.renderCard(card, proposal);
        }
        const summary = editsLog.map((e) => `[${e.op}${e.doc ? ` doc=${e.doc}` : ""}${e.sheet ? ` ${e.sheet}!${e.range || ""}` : ""}${e.find ? ` «${e.find.slice(0, 60)}»` : ""}${e.text ? ` → «${e.text.slice(0, 80)}${e.text.length > 80 ? "…" : ""}»` : ""}]`).join(" ");
        this.messages.push({ role: "assistant", content: (cleanProse(prose).trim() || "…") + (summary ? `\n\n(Proposed edits: ${summary})` : "") });
        if (prose.trim() && !anyHunks) this.answerActions(bubble, cleanProse(prose), doneInfo?.cancelled);
        else if (doneInfo?.cancelled) $(".msg-actions", bubble).innerHTML = `<span class="muted small">${esc(t("ai_stopped"))}</span>`;
        this.updateBadge(); follow(); unfollow();
      },
    });
  }
  updateBadge() {
    const n = this.proposals.reduce((s, p) => s + p.pending.length, 0);
    const b = $(".aip-rail-badge", this.host); if (b) { b.hidden = !n; b.textContent = n; }
  }

  renderCard(card, p) {
    if (!card) return;
    const live = card._live || new Map();
    const hunks = p ? p.hunks : [];
    if (!hunks.length && !live.size) { card.hidden = true; return; }
    card.hidden = false;
    const pending = hunks.filter((h) => h.status === "pending").length, applied = hunks.filter((h) => h.status === "applied").length;
    const plus = hunks.reduce((n, h) => n + (h.status !== "failed" && h.status !== "rejected" ? countWords(h.insert || "") : 0), 0);
    const minus = hunks.reduce((n, h) => n + (h.status !== "failed" && h.status !== "rejected" ? countWords(h.del || "") : 0), 0);
    const where = (h) => {
      if (h.virtual) return t("ai_new_document");
      if (h.where) return h.where;
      if (h.docOnly || p.target.kind !== "cm") return "";
      const v = p.view(); if (!v) return "";
      try { const pos = Math.min(h.from, v.state.doc.length); return t("ai_line_n", { n: v.state.doc.lineAt(pos).number }); } catch { return ""; }
    };
    const opName = (h) => t(`ai_op_${h.op === "?" ? "invalid" : h.op || "replace"}`);
    const rows = hunks.map((h) => {
      const st = h.status;
      const diff = h.status === "failed" ? `<div class="aip-hunk-err">${icon("warn")}${esc(t(`ai_hunk_${h.error || "invalid"}`))}${h.find ? ` <code>${esc(String(h.find).slice(0, 80))}</code>` : ""}</div>`
        : h.virtual ? `<div class="aip-hunk-diff newdoc"><b>${esc(h.title || t("ai_new_document"))}</b><ins>${esc(h.insert.slice(0, 600))}${h.insert.length > 600 ? "…" : ""}</ins></div>`
        : h.html ? `<div class="aip-hunk-diff sheet">${h.html}</div>`
        : `<div class="aip-hunk-diff">${diffHtml(h.del || "", h.insert || "", 1500)}</div>`;
      const btns = st === "pending" ? `${!h.virtual && (p.target.kind === "cm" || p.target.kind === "sheet") ? `<button class="icon-btn sm" data-h="locate" title="${esc(t("ai_show_in_doc"))}">${aic("locate")}</button>` : ""}
          <button class="aip-hb no" data-h="reject" title="${esc(t("ai_reject_hunk"))}">${aic("x")}</button>
          <button class="aip-hb ok" data-h="accept" title="${esc(t("ai_accept_hunk"))}">${aic("check")}</button>`
        : st === "applied" ? `<span class="aip-st ok">${aic("check")}${esc(t("ai_applied"))}</span>` : st === "rejected" ? `<span class="aip-st">${esc(t("ai_rejected"))}</span>` : "";
      return `<div class="aip-hunk ${st}" data-id="${h.id}"><div class="aip-hunk-top"><span class="aip-op op-${esc(h.op || "replace")}">${esc(opName(h))}</span><span class="aip-where">${esc(where(h))}</span>${h.incomplete ? `<span class="aip-warn-pill">${esc(t("ai_incomplete"))}</span>` : ""}<span class="spacer"></span>${btns}</div>${diff}</div>`;
    }).join("");
    const liveRows = [...live.values()].map((l) => `<div class="aip-hunk live"><div class="aip-hunk-top"><span class="aip-op">${esc(t(`ai_op_${OPS_KNOWN.includes(l.op) ? l.op : "replace"}`))}</span><span class="aip-where">${esc(t("ai_writing_n", { n: l.chars }))}</span><span class="spacer"></span><span class="typing"><i></i><i></i><i></i></span></div>${l.preview ? `<div class="aip-hunk-diff"><ins>${esc(l.preview.slice(-300))}</ins></div>` : ""}</div>`).join("");
    const canUndo = !!p?.applied;
    const tabBtn = p?.target?.title ? `<button class="aip-card-tab" data-h="open" title="${esc(t("ai_open_tab"))}">${aic("docScope")}<span>${esc(p.target.title)}</span></button>` : "";
    card.innerHTML = `<div class="aip-card-head">${aic("diff")}<b>${esc(t("ai_changes"))}</b>${tabBtn}<span class="aip-count">${hunks.filter((h) => h.status !== "failed").length + live.size}</span>
        <span class="spacer"></span>${plus || minus ? `<span class="aip-delta"><span class="p">+${plus}</span> <span class="m">−${minus}</span></span>` : ""}</div>
      <div class="aip-hunks">${rows}${liveRows}</div>
      ${pending || canUndo ? `<div class="aip-card-foot">
        ${pending ? `<button class="btn sm primary" data-h="all">${aic("checks")}${esc(pending > 1 ? t("ai_apply_all_n", { n: pending }) : t("ai_apply"))}</button><button class="btn sm ghost" data-h="none">${esc(t("ai_reject_all"))}</button>` : ""}
        <span class="spacer"></span>${canUndo ? `<button class="btn sm ghost" data-h="undo">${aic("undo")}${esc(t("undo"))}</button>` : ""}</div>`
      : applied ? `<div class="aip-card-foot done">${aic("check")}<span>${esc(t("ai_all_applied"))}</span></div>` : ""}`;
    card.onclick = (e) => {
      const b = e.target.closest("[data-h]"); if (!b || !p) return;
      const id = b.closest(".aip-hunk")?.dataset.id;
      const act = b.dataset.h;
      if (act === "accept") p.accept(id);
      else if (act === "reject") p.reject(id);
      else if (act === "locate") p.scrollTo(id);
      else if (act === "open") { p.target.activate?.(); if (p.pending.length && p.target.kind === "cm") p.sync?.(); }
      else if (act === "all") p.applyAll();
      else if (act === "none") p.rejectAll();
      else if (act === "undo") { if (!p.undo()) toast(t("ai_undo_changed"), { kind: "error" }); }
      this.updateBadge();
    };
    card.querySelectorAll(".aip-hunk.pending .aip-hunk-diff").forEach((d) => d.addEventListener("click", () => p.scrollTo(d.closest(".aip-hunk").dataset.id)));
  }

  preset(id, param, anchor) {
    const p = PRESETS.find((x) => x.id === id) || COMMANDS.find((x) => x.id === id); if (!p) return;
    if (!this.guard()) return;
    if (p.params && !param) {
      const cmd = COMMANDS.find((c) => c.id === id);
      const names = id === "translate" ? (k) => t(`lang_${k}`) : (k) => t(`tone_${k}`);
      showMenu([{ header: t(p.label) }, ...cmd.params.map((x) => ({ label: names(x), run: () => this.preset(id, x) }))], anchor || this.input);
      return;
    }
    const snap = this.snapshot();
    const kind = p.kind || (COMMANDS.find((c) => c.id === id)?.transform ? "transform" : "answer");
    const label = t(p.label) + (param ? " · " + (id === "translate" ? t(`lang_${param}`) : t(`tone_${param}`)) : "");
    if (kind === "answer" || !snap || snap.noEdit) {
      const scope = this.effScope || "auto";
      const { built } = this.contextFor(snap, scope);
      this.userBubble(label, this.scopeTag(built?.scope, snap));
      this.messages.push({ role: "user", content: label });
      this.runAnswer({ action: id, param, input: built?.scope === "document" ? "" : (built?.range ? snap.text.slice(built.range[0], built.range[1]) : "") }, snap, scope);
      return;
    }
    if (kind === "write") { this.runWrite(snap, label); return; }

    const scope = this.effScope || "auto";
    const { built } = this.contextFor(snap, scope);
    let [from, to] = built.range;
    const docScope = built.scope === "document";
    if (snap.doc && !(snap.selTo > snap.selFrom)) { this.runAgent(t(id === "table" ? "ai_agent_table" : "ai_agent_transform", { action: label }), { label, scope: "document" }); return; }
    if (id === "table" && built.scope !== "selection") { this.runAgent(t("ai_agent_table"), { label, scope: built.scope }); return; }
    if (docScope && to - from > 40000) {

      const sec = built.section; if (sec && sec.to - sec.from <= 40000) [from, to] = [sec.from, sec.to];
      else { const a = Math.max(0, snap.selFrom - 6000); [from, to] = [snap.text.lastIndexOf("\n\n", a) + 1 || a, Math.min(snap.text.length, (snap.text.indexOf("\n\n", snap.selFrom + 6000) + 1) || snap.text.length)]; }
      toast(t("ai_scope_narrowed"));
    }
    const source = snap.text.slice(from, to);
    if (!source.trim()) { toast(t("ai_nothing_to_edit"), { kind: "error" }); return; }
    this.userBubble(label, this.scopeTag(built.scope, snap));
    this.messages.push({ role: "user", content: label });
    const bubble = this.assistantBubble();
    const body = $(".msg-body", bubble), extra = $(".msg-extra", bubble);
    const card = document.createElement("div"); card.className = "aip-card"; card.hidden = true; extra.append(card);
    body.innerHTML = `<div class="aip-stream-preview"><span class="aip-writing">${aic("pen")}${esc(t("ai_rewriting"))}</span><div class="aip-stream-text"></div></div>`;
    const previewEl = $(".aip-stream-text", body);
    let out = "";
    this.setBusy(true);
    const show = debounce(() => { previewEl.textContent = out.slice(-1200); previewEl.scrollTop = previewEl.scrollHeight; this.scrollDown(); }, 40);
    const legacy = this.contextFor(snap, "auto").ctx;
    this.busy = stream({ action: id, param, input: source, context: { ...legacy, text: String(legacy.text || "").replace(MARKERS_RE, "") }, pos: legacy.excerpt_pos, with_document: !docScope, memory: this.flags.memory }, {
      start: (ev) => this.head(bubble, ev),
      delta: (d) => { out += d; show(); },
      error: (msg, kind, status, raw) => { this.setBusy(false); show.cancel(); this.showError(bubble, raw ?? msg, kind, status, () => { this.messages.pop(); this.preset(id, param); }); },
      done: (info) => {
        show.flush(); this.setBusy(false);
        let result = cleanup(out);
        if (info?.cancelled) {

          if (!result) { body.innerHTML = `<span class="muted">${esc(t("ai_stopped"))}</span>`; return; }
          this.renderBody(body, result); this.answerActions(bubble, result, true); return;
        }
        if (!result) { body.innerHTML = `<span class="muted">${esc(t("ai_empty_answer"))}</span>`; return; }

        const trail = /\s*$/.exec(source)[0]; if (trail && !result.endsWith(trail)) result = result.replace(/\s*$/, "") + trail;
        const lead = /^\s*/.exec(source)[0]; if (lead && !result.startsWith(lead)) result = lead + result.replace(/^\s*/, "");
        const proposal = new Proposal(this.target(snap), (pp) => this.renderCard(card, pp));
        this.proposals.push(proposal); card._live = new Map();
        if (snap.doc) proposal.add([{ from: 0, to: 0, insert: result, del: source }], { op: "transform", docOnly: true });
        else {
          const hunks = buildHunks(source, result, from + snap.offset);
          if (!hunks.length) { body.innerHTML = `<span class="muted">${esc(t("ai_no_changes"))}</span>`; return; }
          proposal.add(hunks, { op: "transform" });
        }
        const n = proposal.hunks.length;
        body.innerHTML = `<p>${esc(info?.cancelled ? t("ai_stopped_partial") : t("ai_transform_done", { n }))}</p>`;
        if (this.auto) proposal.applyAll();
        this.renderCard(card, proposal);
        this.messages.push({ role: "assistant", content: result.slice(0, 4000) });
        this.updateBadge(); this.scrollDown();
      },
    });
  }

  runWrite(snap, label) {
    this.userBubble(label, `${aic("target")}${esc(t("ai_at_cursor"))}`);
    this.messages.push({ role: "user", content: label });
    const bubble = this.assistantBubble();
    const body = $(".msg-body", bubble);
    body.innerHTML = `<span class="aip-writing live">${aic("pen")}${esc(t("ai_writing_here"))}</span>`;
    let out = "";
    let writer = null;
    const view = snap.view && !snap.readOnly ? snap.view : null;
    if (view) { ensureAIFields(view); const r = view.state.selection.main; writer = new StreamWriter(view, r.to); }
    this.setBusy(true);
    const before = snap.text.slice(Math.max(0, snap.selTo - 6000), snap.selTo);
    const after = snap.text.slice(snap.selTo, snap.selTo + 1500);
    const push = debounce(() => { writer?.push(joinContinuation(before, out)); const w = countWords(out); body.innerHTML = `<span class="aip-writing live">${aic("pen")}${esc(t("ai_writing_here"))} · ${esc(t("ai_words_n", { n: w }))}</span>`; }, 30);
    const legacy = this.contextFor(snap, "auto").ctx;
    this.busy = stream({ action: "continue", before, instruction: after.trim() ? "The text after the cursor (do not repeat it, the continuation must lead into it):\n" + after : "", context: { name: snap.name, kind: legacy.kind }, memory: this.flags.memory }, {
      start: (ev) => this.head(bubble, ev),
      delta: (d) => { out += d; push(); },
      error: (msg, kind, status, raw) => { this.setBusy(false); push.cancel(); writer?.cancel(); this.showError(bubble, raw ?? msg, kind, status, () => { this.messages.pop(); this.runWrite(this.snapshot() || snap, label); }); },
      done: (info) => {
        push.flush(); this.setBusy(false);
        const text = joinContinuation(before, cleanup(out) ? out.replace(/^```[\w-]*\n|\n```\s*$/g, "") : "");
        if (!text.trim()) { writer?.cancel(); body.innerHTML = `<span class="muted">${esc(t(info?.cancelled ? "ai_stopped" : "ai_empty_answer"))}</span>`; return; }
        let res = null;
        if (writer) res = writer.commit(text);
        else if (snap.doc) { snap.doc.replaceSelection(toDocHtml(text)); res = { doc: true }; }
        else this.ctx.insert(text);
        const w = countWords(text);
        body.innerHTML = `<div class="aip-wrote">${aic("check")}<span>${esc(t(info?.cancelled ? "ai_wrote_partial" : "ai_wrote", { n: w }))}</span></div><div class="aip-quote">${esc(text.slice(0, 280))}${text.length > 280 ? "…" : ""}</div>`;
        const bar = $(".msg-actions", bubble);
        bar.innerHTML = `${res && !res.doc ? `<button class="aip-act" data-w="undo">${aic("undo")}<span>${esc(t("undo"))}</span></button>` : ""}<button class="aip-act" data-w="again">${icon("retry")}<span>${esc(t("ai_write_more"))}</span></button><button class="aip-act" data-w="copy">${aic("copy")}</button>`;
        bar.onclick = (e) => {
          const a = e.target.closest("[data-w]")?.dataset.w; if (!a) return;
          if (a === "undo" && res && view) { if (view.state.doc.eq(res.after)) { CM.undo(view); bar.querySelector("[data-w=undo]")?.remove(); body.querySelector(".aip-wrote span").textContent = t("ai_write_undone"); } else toast(t("ai_undo_changed"), { kind: "error" }); }
          if (a === "again") { const s = this.snapshot(); if (s) this.runWrite(s, label); }
          if (a === "copy") copyText(text).then(() => toast(t("copied"), { kind: "ok" }));
        };
        this.messages.push({ role: "assistant", content: text.slice(0, 4000) });
      },
    });
  }
}

function joinContinuation(before, text) {
  if (!text) return text;
  if (/\S$/.test(before) && /^[\p{L}\p{N}«“(]/u.test(text) && !/\n$/.test(before)) return " " + text;
  if (/\n\s*$/.test(before)) {
    text = text.replace(/^[ \t]+/, "");

    if (/\S[ \t]*\n$/.test(before) && !/^\n/.test(text) && /^[\p{Lu}«“(#*-]/u.test(text)) text = "\n" + text;
    return text;
  }
  return text;
}

function cleanProse(s) { return String(s || "").replace(/```[\w-]*\s*```/g, "").replace(/\n{3,}/g, "\n\n"); }

function enhanceCode(root) {
  root.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement; if (pre.parentElement?.classList.contains("aip-code")) return;
    const lang = (/language-([\w+#-]+)/.exec(code.className) || [])[1] || "";
    const wrap = document.createElement("div"); wrap.className = "aip-code";
    wrap.innerHTML = `<div class="aip-code-head"><span>${esc(lang || "text")}</span><span class="spacer"></span><button data-code="insert" title="${esc(t("ai_insert"))}">${aic("insert")}</button><button data-code="copy" title="${esc(t("copy"))}">${aic("copy")}</button></div>`;
    pre.replaceWith(wrap); wrap.append(pre);
  });
}

function hostOf(url) { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } }

function citeLinks(html, sources) {
  if (!sources.length) return html;
  return html.split(/(<code[\s\S]*?<\/code>|<pre[\s\S]*?<\/pre>|<[^>]+>)/).map((part) => (part.startsWith("<") ? part
    : part.replace(/\[(\d{1,2})\]/g, (all, n) => { const s = sources[+n - 1]; return s ? `<a class="cite" href="#" data-url="${esc(s.url)}" title="${esc(s.title || s.url)}">${n}</a>` : all; }))).join("");
}
function renderSources(bubble, sources) {
  const box = $(".msg-sources", bubble); if (!box) return;
  box.hidden = false;
  box.innerHTML = `<div class="msg-sources-title">${icon("globe")}${esc(t("ai_sources"))}</div><ol>${sources.map((s) => `<li><a href="#" data-url="${esc(s.url)}" title="${esc(s.url)}"><span class="src-t">${esc(s.title || hostOf(s.url))}</span><span class="src-h">${esc(hostOf(s.url))}</span></a></li>`).join("")}</ol>`;
}

export function inlineCommand(ctx, preset) {
  if (!AI.online) { toast(t("ai_offline"), { kind: "error" }); refreshStatus(true); return; }
  document.querySelector(".ai-inline")?.remove();
  const rect = ctx.anchorRect();
  const box = document.createElement("div"); box.className = "ai-inline";
  const sel = ctx.selection();
  box.innerHTML = `<div class="ai-inline-input">${icon("sparkle")}<input placeholder="${esc(t(sel ? "ai_inline_placeholder_sel" : "ai_inline_placeholder"))}" spellcheck="true"><kbd>Enter</kbd></div>
    <div class="ai-inline-list"></div><div class="ai-inline-result" hidden><div class="ai-result-body md"></div><div class="ai-result-bar"></div></div>`;
  document.body.append(box);
  place(box, rect);
  const input = $("input", box), list = $(".ai-inline-list", box);
  const cmds = COMMANDS.filter((c) => !c.code || ctx.isCode?.()).filter((c) => sel || ["continue", "summarize", "outline", "titles", "todos", "table", "format_md", "keywords"].includes(c.id));
  let filtered = cmds, idx = 0;
  const renderList = () => {
    const q = input.value.trim().toLowerCase();
    filtered = q ? cmds.filter((c) => t(c.label).toLowerCase().includes(q)) : cmds;
    idx = Math.min(idx, Math.max(0, filtered.length - 1));
    list.innerHTML = filtered.map((c, i) => `<div class="pi ${i === idx ? "sel" : ""}" data-i="${i}">${icon(c.icon)}<span class="pi-label">${esc(t(c.label))}</span>${c.params ? icon("chevronRight") : ""}</div>`).join("")
      + (q ? `<div class="pi ${filtered.length === 0 ? "sel" : ""}" data-custom>${icon("wand")}<span class="pi-label">${esc(t("ai_custom_run", { q: input.value.trim() }))}</span></div>` : "");
  };
  const close = () => { box.remove(); document.removeEventListener("pointerdown", outside, true); handle?.stop(); ctx.focus?.(); };
  const outside = (e) => { if (!box.contains(e.target)) close(); };
  setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
  let handle = null;
  const choose = (cmd, param) => {
    if (cmd.params && !param) {
      const names = cmd.id === "translate" ? (k) => t("lang_" + k) : (k) => t("tone_" + k);
      const r = list.querySelector(`[data-i="${filtered.indexOf(cmd)}"]`)?.getBoundingClientRect() || box.getBoundingClientRect();
      showMenu(cmd.params.map((p) => ({ label: names(p), run: () => choose(cmd, p) })), { x: r.right, y: r.top });
      return;
    }
    if (!cmd.transform) { close(); ctx.toChat(cmd.id, sel, param); return; }
    runTransform(cmd, param, "");
  };
  const runTransform = (cmd, param, instruction) => {
    list.hidden = true; $(".ai-inline-input", box).hidden = true;
    const res = $(".ai-inline-result", box); res.hidden = false;
    const body = $(".ai-result-body", res), bar = $(".ai-result-bar", res);
    body.innerHTML = `<span class="typing"><i></i><i></i><i></i></span>`;
    bar.innerHTML = `<span class="muted small">${esc(t(cmd.label))}…</span><span class="spacer"></span><button class="btn sm ghost" data-r="stop">${icon("stop")}${esc(t("ai_stop"))}</button>`;
    let text = "";
    const ctxInfo = ctx.getContext();
    const show = debounce(() => { body.textContent = text; body.scrollTop = body.scrollHeight; }, 30);
    handle = stream({ action: cmd.id, param, instruction, input: sel, before: ctx.before?.(), context: ctxInfo, pos: ctxInfo.pos, memory: ctx.settings().ai_memory !== false }, {
      delta: (d) => { text += d; show(); },
      error: (m) => { body.innerHTML = `<div class="err">${icon("warn")}${esc(m)}</div>`; bar.innerHTML = `<span class="spacer"></span><button class="btn sm" data-r="close">${esc(t("close"))}</button>`; },
      done: (info) => {
        show.flush(); handle = null;
        text = cleanup(text);
        if (!text) { body.innerHTML = `<span class="muted">${esc(t("ai_empty_answer"))}</span>`; }
        const mode = cmd.insert || !sel ? "insert" : "replace";
        bar.innerHTML = `<button class="btn sm primary" data-r="${mode}">${icon(mode === "insert" ? "insert" : "check")}${esc(t(mode === "insert" ? "ai_insert" : "ai_accept"))}</button>
          ${sel && mode !== "insert" ? `<button class="btn sm ghost" data-r="below">${icon("insert")}${esc(t("ai_insert_below"))}</button>` : ""}
          <button class="btn sm ghost" data-r="copy">${icon("copy")}${esc(t("copy"))}</button>
          <span class="spacer"></span>
          <button class="btn sm ghost" data-r="retry">${icon("retry")}${esc(t("retry"))}</button>
          <button class="btn sm ghost" data-r="close">${esc(t("ai_discard"))}</button>`;
        bar.querySelector(".primary")?.focus();
        if (info?.cancelled) bar.insertAdjacentHTML("afterbegin", `<span class="muted small">${esc(t("ai_stopped"))}</span>`);
      },
    });
    bar.onclick = async (e) => {
      const r = e.target.closest("[data-r]")?.dataset.r; if (!r) return;
      if (r === "stop") { handle?.stop(); }
      else if (r === "replace") { ctx.apply(text, "replace"); close(); }
      else if (r === "insert") { ctx.apply(text, "insert"); close(); }
      else if (r === "below") { ctx.apply(text, "below"); close(); }
      else if (r === "copy") { await copyText(text); toast(t("copied"), { kind: "ok" }); }
      else if (r === "retry") runTransform(cmd, param, instruction);
      else if (r === "close") close();
    };
  };
  input.addEventListener("input", () => { idx = 0; renderList(); });
  input.addEventListener("keydown", (e) => {
    const n = filtered.length + (input.value.trim() ? 1 : 0);
    if (e.key === "ArrowDown") { e.preventDefault(); idx = (idx + 1) % Math.max(1, n); renderList(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); idx = (idx - 1 + n) % Math.max(1, n); renderList(); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const q = input.value.trim();
      if (idx < filtered.length && filtered.length) choose(filtered[idx]);
      else if (q) runTransform({ id: "custom", label: "ai_custom", transform: true }, "", q);
    }
  });
  list.addEventListener("click", (e) => {
    const p = e.target.closest(".pi"); if (!p) return;
    if (p.dataset.custom !== undefined) runTransform({ id: "custom", label: "ai_custom", transform: true }, "", input.value.trim());
    else choose(filtered[+p.dataset.i]);
  });
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } });
  renderList();
  if (preset) { const cmd = COMMANDS.find((c) => c.id === preset.id); if (cmd) choose(cmd, preset.param); }
  else input.focus();
}
function cleanup(text) {
  let s = String(text || "").trim();
  const fence = /^```[\w-]*\n([\s\S]*?)\n```$/.exec(s);
  if (fence) s = fence[1];
  return s;
}
function place(box, rect) {
  const W = innerWidth, H = innerHeight, w = Math.min(560, W - 24);
  box.style.width = w + "px";
  let x = Math.max(12, Math.min(rect.left, W - w - 12));
  let y = rect.bottom + 8;
  if (y + 320 > H) y = Math.max(12, rect.top - 330);
  box.style.left = x + "px"; box.style.top = y + "px";
}

let ghostReq = null;

export function ghostTail(before) {
  const line = String(before || "").slice(String(before || "").lastIndexOf("\n") + 1);
  const m = /(?:\S+[ \t]+){0,2}\S+[ \t]*$/u.exec(line);
  return m && m[0].length <= 60 ? m[0] : "";
}

export function stripEcho(text, tail) {
  if (!tail) return null;
  const out = String(text || "").replace(/^[ \t]+/, "");
  const core = tail.replace(/[ \t]+$/, "");
  if (!core || !out.startsWith(core)) return null;
  const rest = out.slice(core.length);
  return core !== tail ? rest.replace(/^[ \t]+/, "") : rest;
}

export function ghostLine(text, code) {
  const s = String(text || "");
  if (code) { const m = /^\r?\n((?:[ \t]*\r?\n)*)([^\r\n]*\S[^\r\n]*)/.exec(s); if (m) return "\n" + m[2]; }
  return s.replace(/\r?\n[\s\S]*$/, "");
}
export function requestGhost(view, ctx, setGhost) {
  ghostReq?.stop(); ghostReq = null;
  if (!AI.online) return;
  const s = ctx.settings();
  if (!s.ai_autocomplete) return;
  const pos = view.state.selection.main.head;
  if (!view.state.selection.main.empty) return;
  const line = view.state.doc.lineAt(pos);
  if (pos !== line.to || line.text.trim().length < 3) return;
  const before = view.state.sliceDoc(Math.max(0, pos - 3000), pos), after = view.state.sliceDoc(pos, pos + 600);
  clearTimeout(requestGhost.timer);
  requestGhost.timer = setTimeout(() => {
    if (view.state.selection.main.head !== pos) return;
    let text = "";
    const docVersion = view.state.doc;
    const tail = ghostTail(before), gc = ctx.getContext(), code = gc.kind === "code" || gc.kind === "json" || gc.kind === "html";
    ghostReq = stream({ action: "autocomplete", before, after, echo: tail, context: { name: gc.name, kind: gc.kind }, max_tokens: 80, temperature: 0.2, memory: false, effort: "off" }, {
      delta: (d) => { text += d; },
      done: () => {
        ghostReq = null;
        const echoed = stripEcho(text.replace(/^["«]/, ""), tail);
        text = ghostLine(echoed ?? text, code);
        if (!code) text = text.replace(/^["«]|["»]$/g, "");
        if (!text.trim() || view.state.doc !== docVersion || view.state.selection.main.head !== pos) return;

        if (echoed === null && ((/[,;:!?)\]»”]$/u.test(before) && /^[\p{L}\p{N}«“(]/u.test(text)) || (/[.…]$/u.test(before) && /^[\p{Lu}«“(]/u.test(text)))) text = " " + text;
        view.dispatch({ effects: setGhost.of({ pos, text }) });
      },
      error: () => { ghostReq = null; },
    });
  }, s.ai_autocomplete_delay || 900);
}

export function aiExcerpt({ text, kind, selFrom, selTo, limit }) {
  const c = buildContext({ text, kind, selFrom, selTo, cursor: selTo, scope: "auto", limit: Math.min(+limit || 60000, 120000) });
  const markerBefore = (c.text.slice(0, c.excerpt_pos).match(MARKERS_RE) || []).join("").length;
  return { text: c.text.replace(MARKERS_RE, ""), pos: c.excerpt_pos - markerBefore };
}
