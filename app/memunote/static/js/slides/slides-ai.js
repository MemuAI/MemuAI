// MemuNote Slides — AI for presentations (through MemuAI Proxy, route /api/slides_ai):
// generate a deck from a topic / outline / document, improve or shorten slide text, suggest a layout,
// speaker notes (one slide / all), translate the deck. Each applied result is one undo step.
import { api, esc, t, I18N, toast, modal, uid as cuid } from "../core.js";
import { AI, refreshStatus } from "../ai.js";
import { makeSlide, plainText, uid } from "./pptx.js";
import { isLineGeom } from "./render.js";

const LANGS = ["ru", "en", "de", "fr", "es", "it", "pt", "uk", "pl", "tr", "zh", "ja", "ko", "ar", "kk"];

/** Stream a slides AI task; resolves with the full text. onDelta(textSoFar). */
export function streamTask(task, input, { onDelta, signal } = {}) {
  const id = cuid();
  const ctrl = new AbortController();
  signal?.addEventListener("abort", () => { api("ai/stop", { id }).catch(() => {}); ctrl.abort(); });
  return (async () => {
    const res = await api("slides_ai", { task, input, id, model: AI.model || undefined }, { stream: true, signal: ctrl.signal });
    if (!res.ok) { let msg = "HTTP " + res.status; try { msg = (await res.json()).error || msg; } catch { /* */ } const e = new Error(msg); e.status = res.status; throw e; }
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "", out = "";
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data:")) continue;
        let ev; try { ev = JSON.parse(chunk.slice(5)); } catch { continue; }
        if (ev.t === "delta") { out += ev.text; onDelta?.(out); }
        else if (ev.t === "error") { const e = new Error(ev.message || "error"); e.kind = ev.kind; e.status = ev.status; throw e; }
      }
    }
    return out;
  })();
}

/** Extract the JSON object from a model answer (code fences, prose, trailing commas, cut-off tails). */
export function parseJsonLoose(text) {
  let s = String(text || "").replace(/^\uFEFF/, "").trim();
  s = s.replace(/<think>[\s\S]*?<\/think>/g, "");
  const fence = /```(?:json)?\s*([\s\S]*?)```/i.exec(s); if (fence) s = fence[1];
  const a = s.indexOf("{"); if (a < 0) throw new Error("no JSON in the answer");
  s = s.slice(a);
  const tryParse = (x) => { try { return JSON.parse(x); } catch { return undefined; } };
  let b = s.lastIndexOf("}");
  let v = b > 0 ? tryParse(s.slice(0, b + 1)) : undefined;
  if (v !== undefined) return v;
  const cleaned = s.slice(0, b + 1).replace(/,\s*([}\]])/g, "$1").replace(/[\u201c\u201d]/g, '"');
  v = tryParse(cleaned); if (v !== undefined) return v;
  // cut-off answer: close open strings / brackets
  let depth = [], inStr = false, esc2 = false, cut = s;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inStr) { if (esc2) esc2 = false; else if (c === "\\") esc2 = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true; else if (c === "{" || c === "[") depth.push(c); else if (c === "}" || c === "]") depth.pop();
  }
  cut = s + (inStr ? '"' : "");
  cut = cut.replace(/,\s*$/, "").replace(/,\s*"[^"]*"\s*:\s*$/, "");
  for (let i = depth.length - 1; i >= 0; i--) cut += depth[i] === "{" ? "}" : "]";
  v = tryParse(cut.replace(/,\s*([}\]])/g, "$1"));
  if (v !== undefined) return v;
  throw new Error("the model answered with invalid JSON");
}

function linesToParas(lines, base = []) {
  const out = [];
  for (const raw of lines) {
    if (raw === undefined || raw === null) continue;
    const str = String(raw);
    const m = /^(\s*)(?:[-•*–]\s+)?(.*)$/.exec(str);
    const lvl = Math.min(4, Math.floor((m[1] || "").replace(/\t/g, "  ").length / 2));
    const text = m[2].trim();
    if (!text) continue;
    const b = base[Math.min(out.length, base.length - 1)] || null;
    const props = b ? Object.fromEntries(Object.entries(b).filter(([k]) => k !== "runs")) : {};
    const runProps = b?.runs?.find((r) => !r.br) ? Object.fromEntries(Object.entries(b.runs.find((r) => !r.br)).filter(([k]) => k !== "t" && k !== "fld" && k !== "_fid")) : {};
    out.push({ ...props, lvl: lvl || undefined, runs: [{ ...runProps, t: text }] });
    if (!out[out.length - 1].lvl) delete out[out.length - 1].lvl;
  }
  return out.length ? out : [{ runs: [{ t: "" }] }];
}
const textItems = (slide) => slide.elements.filter((e) => e.kind === "shape" && e.text && !isLineGeom(e.geom) && plainText(e.text).trim());
const toLines = (text) => plainText(text).split("\n").map((l, i, arr) => l);
function itemText(el) { return el.text.paras.map((p) => "  ".repeat(p.lvl || 0) + p.runs.map((r) => (r.br ? " " : r.t)).join("")).join("\n"); }

function layoutFor(deck, kind) {
  const by = (type) => deck.layouts.find((l) => l.type === type);
  const want = { title: ["title"], section: ["secHead", "title"], content: ["obj", "tx", "twoObj"], two: ["twoObj", "twoTxTwoObj", "obj"], statement: ["tx", "titleOnly", "title"], chart: ["titleOnly", "obj"], table: ["titleOnly", "obj"] }[kind] || ["obj"];
  for (const w of want) { const l = by(w); if (l && (w !== "tx" || l.placeholders.length <= 1 || kind === "content")) return l; }
  return deck.layouts.find((l) => l.placeholders.some((p) => p.type === "title")) || deck.layouts[0];
}
const phOf = (s, types) => s.elements.find((e) => e.ph && types.includes(e.ph.type || "body"));
function bodies(deck, s) { return s.elements.filter((e) => e.ph && ["body", "obj", ""].includes(e.ph.type || "") && e.ph.type !== "title").sort((a, b) => deck.boxOf(s, a).x - deck.boxOf(s, b).x); }

/** Build a slide from an AI spec (layout + texts + chart/table + notes). */
export function slideFromSpec(deck, spec) {
  let kind = spec.layout || (spec.chart ? "chart" : spec.table ? "table" : spec.left ? "two" : spec.bullets?.length ? "content" : "title");
  let layout = layoutFor(deck, kind);
  // a "statement" layout is often title-only (Statement / Title Only): with supporting text, use a layout that can hold it
  if (kind === "statement" && (spec.bullets?.length || spec.subtitle) && !layout.placeholders.some((p) => ["body", "obj", ""].includes(p.type || ""))) {
    if (spec.bullets?.length > 1) { kind = "content"; layout = layoutFor(deck, kind); }
    else { kind = "section"; layout = layoutFor(deck, kind); spec = { ...spec, subtitle: spec.subtitle || spec.bullets[0] }; }
  }
  const s = makeSlide(deck, layout.path);
  const title = phOf(s, ["title", "ctrTitle"]);
  if (title) title.text.paras = linesToParas([spec.title || ""]);
  const sub = phOf(s, ["subTitle"]);
  const bs = bodies(deck, s);
  if (kind === "title" || kind === "section") {
    const target = sub || bs[0];
    if (target) target.text.paras = linesToParas([spec.subtitle || (spec.bullets || []).join(" · ")]);
    for (const b of bs) if (b !== target) s.elements = s.elements.filter((e) => e !== b);
  } else if (kind === "two") {
    const left = [...(spec.left_title ? [spec.left_title] : []), ...(spec.left || [])], right = [...(spec.right_title ? [spec.right_title] : []), ...(spec.right || [])];
    if (bs[0]) bs[0].text.paras = linesToParas(left.length ? left : spec.bullets || []);
    if (bs[1]) bs[1].text.paras = linesToParas(right);
    else if (bs[0] && right.length) bs[0].text.paras.push(...linesToParas(right));
  } else if (kind === "chart" && spec.chart?.series?.length) {
    const box = { x: deck.w * 0.12, y: deck.h * 0.28, w: deck.w * 0.76, h: deck.h * 0.62 };
    const ch = spec.chart;
    s.elements.push({ id: uid(), kind: "chart", name: "Chart", rot: 0, ...roundBox(box), chart: { type: ["col", "bar", "line", "pie", "area", "doughnut"].includes(ch.type) ? ch.type : "col", cats: (ch.cats || []).map(String), series: ch.series.slice(0, 6).map((x) => ({ name: String(x.name || ""), vals: (x.vals || []).map((v) => Number(v) || 0) })) } });
    for (const b of bs) s.elements = s.elements.filter((e) => e !== b);
  } else if (kind === "table" && Array.isArray(spec.table) && spec.table.length) {
    const rows = spec.table.slice(0, 12).map((r) => (Array.isArray(r) ? r : [r]).slice(0, 8).map(String));
    const cols = Math.max(...rows.map((r) => r.length));
    const w = deck.w * 0.84, rh = 370840;
    s.elements.push({ id: uid(), kind: "table", name: "Table", rot: 0, x: Math.round(deck.w * 0.08), y: Math.round(deck.h * 0.28), w: Math.round(w), h: rh * rows.length,
      table: { cols: Array.from({ length: cols }, () => Math.round(w / cols)), firstRow: true, bandRow: true, style: "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}",
        rows: rows.map((r) => ({ h: rh, cells: Array.from({ length: cols }, (_, j) => ({ text: { paras: [{ runs: [{ t: r[j] ?? "" }] }] } })) })) } });
    for (const b of bs) s.elements = s.elements.filter((e) => e !== b);
  } else if (kind === "statement") {
    if (bs[0]) { if (spec.bullets?.length || spec.subtitle) bs[0].text.paras = linesToParas(spec.bullets?.length ? spec.bullets : [spec.subtitle]); else s.elements = s.elements.filter((e) => e !== bs[0]); }
  } else {
    if (bs[0]) bs[0].text.paras = linesToParas(spec.bullets || []);
    for (const b of bs.slice(1)) s.elements = s.elements.filter((e) => e !== b);
  }
  s.notes = String(spec.notes || "").trim();
  return s;
}
const roundBox = (b) => ({ x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.w), h: Math.round(b.h) });

// ------------------------------------------------------------------ UI
async function ensureOnline() {
  await refreshStatus();
  if (!AI.online) { toast(t("ai_offline"), { kind: "error", ms: 7000 }); return false; }
  return true;
}
function progressModal(title) {
  const ctrl = new AbortController();
  let set = null, close = null, closed = false;
  const p = modal({ title, body: `<div class="sl-ai-progress"><div class="sl-ai-status"></div><div class="bar"><i></i></div><div class="sl-ai-log"></div></div>`, dismiss: false,
    buttons: [{ label: t("sl_ai_stop"), value: "stop" }],
    onOpen: (box, done) => {
      set = (s, log) => { box.querySelector(".sl-ai-status").textContent = s; if (log !== undefined) { const l = box.querySelector(".sl-ai-log"); l.textContent = log; l.scrollTop = l.scrollHeight; } };
      close = () => done("closed");
    } });
  p.then((v) => { closed = true; if (v === "stop" || v === null) ctrl.abort(); });
  return { signal: ctrl.signal, set: (s, log) => set?.(s, log), close: () => { if (!closed) close?.(); }, aborted: () => ctrl.signal.aborted };
}
function fail(e) {
  if (e.name === "AbortError") return;
  const msg = e.message === "no_model" ? t("ai_no_model") : e.message === "ai_disabled" ? t("ai_disabled") : /offline|not reachable/i.test(e.message) ? t("ai_offline") : e.message;
  toast(t("sl_ai_failed") + ": " + msg, { kind: "error", ms: 9000 });
}

async function generateDialog(view, fromDoc) {
  const docs = (await view.hooks.openDocs?.()) || [];
  const pristine = view.deck.slides.length === 1 && !textItems(view.deck.slides[0]).length && view.deck.slides[0].elements.every((e) => e.ph);
  const r = await modal({ title: t(fromDoc ? "sl_ai_from_doc" : "sl_ai_generate"), wide: true, body: `<div class="sl-ai-form">
      <label class="field"><span>${esc(t(fromDoc ? "sl_ai_doc_goal" : "sl_ai_topic"))}</span><textarea class="input" id="ai-topic" placeholder="${esc(t(fromDoc ? "sl_ai_doc_goal_ph" : "sl_ai_topic_ph"))}" autofocus></textarea></label>
      ${fromDoc ? `<label class="field"><span>${esc(t("sl_ai_source"))}</span><select class="input" id="ai-doc">${docs.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")}<option value="__paste">${esc(t("sl_ai_paste"))}</option></select></label>
        <textarea class="input" id="ai-src" placeholder="${esc(t("sl_ai_paste_ph"))}" ${docs.length ? "hidden" : ""}></textarea>` : ""}
      <div class="row2"><label class="field"><span>${esc(t("sl_ai_count"))}</span><select class="input" id="ai-count">${[5, 6, 8, 10, 12, 15, 20].map((n) => `<option ${n === 8 ? "selected" : ""}>${n}</option>`).join("")}</select></label>
        <label class="field"><span>${esc(t("sl_ai_lang"))}</span><select class="input" id="ai-lang">${LANGS.map((l) => `<option value="${l}" ${l === I18N.lang ? "selected" : ""}>${esc(t("lang_" + l) !== "lang_" + l ? t("lang_" + l) : l.toUpperCase())}</option>`).join("")}</select></label>
        <label class="field"><span>${esc(t("sl_ai_tone"))}</span><select class="input" id="ai-tone">${["professional", "friendly", "persuasive", "academic", "simple"].map((x) => `<option value="${x}">${esc(t("sl_tone_" + x))}</option>`).join("")}</select></label></div>
      <label class="field"><span>${esc(t("sl_ai_audience"))}</span><input class="input" id="ai-aud" placeholder="${esc(t("sl_ai_audience_ph"))}"></label>
      <label class="check"><input type="radio" name="ai-where" value="replace" ${pristine ? "checked" : ""}> ${esc(t("sl_ai_replace"))}</label>
      <label class="check"><input type="radio" name="ai-where" value="append" ${pristine ? "" : "checked"}> ${esc(t("sl_ai_append"))}</label></div>`,
    onOpen: (box) => { const sel = box.querySelector("#ai-doc"); if (sel) sel.onchange = () => { box.querySelector("#ai-src").hidden = sel.value !== "__paste"; }; },
    buttons: [{ label: t("cancel"), value: null }, { label: t("sl_ai_create"), primary: true, value: (b) => ({ topic: b.querySelector("#ai-topic").value.trim(), doc: b.querySelector("#ai-doc")?.value, src: b.querySelector("#ai-src")?.value || "",
      count: +b.querySelector("#ai-count").value, lang: b.querySelector("#ai-lang").value, tone: b.querySelector("#ai-tone").value, audience: b.querySelector("#ai-aud").value.trim(),
      where: b.querySelector("input[name=ai-where]:checked").value }) }], enter: false });
  if (!r) return null;
  let source = "";
  if (fromDoc) source = r.doc && r.doc !== "__paste" ? await view.hooks.docText?.(r.doc) || "" : r.src;
  if (!r.topic && !source.trim()) { toast(t("sl_ai_need_topic")); return null; }
  return { topic: r.topic || t("sl_ai_default_goal"), source, count: r.count, lang: r.lang, tone: r.tone, audience: r.audience, where: r.where };
}

export async function runAI(view, action, job) {
  if (!(await ensureOnline())) return;
  const deck = view.deck;
  if (view.editing) view.endEdit();
  try {
    if (action === "generate" || action === "fromdoc") {
      const j = job || await generateDialog(view, action === "fromdoc");
      if (!j) return;
      const pm = progressModal(t("sl_ai_generating"));
      let text;
      try {
        text = await streamTask("generate", j, { signal: pm.signal, onDelta: (s) => pm.set(t("sl_ai_slides_so_far", { n: (s.match(/"layout"\s*:/g) || []).length }), s.slice(-600)) });
      } finally { pm.close(); }
      const data = parseJsonLoose(text);
      const specs = Array.isArray(data.slides) ? data.slides : [];
      if (!specs.length) throw new Error(t("sl_ai_empty"));
      const slides = specs.slice(0, 60).map((sp) => slideFromSpec(deck, sp || {}));
      view.change(() => {
        if (j.where === "replace") { deck.slides = slides; view.cur = 0; }
        else { deck.slides.splice(view.cur + 1, 0, ...slides); view.cur = view.cur + 1; }
        view.slideSel = new Set([view.cur]); view.sel = [];
        slides.forEach((s) => view.touch(s));
      }, { thumbsAll: true });
      view.renderAll();
      toast(t("sl_ai_done_slides", { n: slides.length }), { kind: "ok" });
      return;
    }
    if (action === "improve" || action === "shorten") {
      const s = view.slide, items = textItems(s);
      if (!items.length) { toast(t("sl_ai_no_text")); return; }
      const pm = progressModal(t("sl_ai_working"));
      let text; try { text = await streamTask("rewrite", { mode: action, items: items.map((e) => ({ id: e.id, text: itemText(e) })) }, { signal: pm.signal, onDelta: (x) => pm.set(t("sl_ai_writing"), x.slice(-400)) }); } finally { pm.close(); }
      const data = parseJsonLoose(text);
      const upd = (data.items || []).filter((it) => { const el = items.find((e) => e.id === it.id); return el && typeof it.text === "string" && it.text.trim() && it.text.trim() !== itemText(el).trim(); });
      if (!upd.length) { toast(t("sl_ai_unchanged")); return; }   // the model kept the text as it was: no empty undo step
      view.change(() => { for (const it of upd) { const el = items.find((e) => e.id === it.id); el.text.paras = linesToParas(it.text.split("\n"), el.text.paras); } });
      toast(t("sl_ai_applied"), { kind: "ok", action: { label: t("undo"), run: () => view.undo() } });
      return;
    }
    if (action === "layout") {
      const s = view.slide, items = textItems(s);
      if (!items.length) { toast(t("sl_ai_no_text")); return; }
      const pm = progressModal(t("sl_ai_working"));
      let text; try { text = await streamTask("layout", { lang: I18N.lang, items: items.map((e) => ({ id: e.id, role: e.ph?.type || "text", text: itemText(e) })) }, { signal: pm.signal, onDelta: (x) => pm.set(t("sl_ai_writing"), x.slice(-400)) }); } finally { pm.close(); }
      const spec = parseJsonLoose(text);
      const fresh = slideFromSpec(deck, spec);
      const keep = s.elements.filter((e) => !e.ph && !(e.kind === "shape" && e.text && items.includes(e)));
      view.change(() => { s.layout = fresh.layout; s.elements = [...fresh.elements, ...keep]; });
      toast(spec.reason ? t("sl_ai_layout_done") + ": " + spec.reason : t("sl_ai_layout_done"), { kind: "ok", ms: 6000, action: { label: t("undo"), run: () => view.undo() } });
      return;
    }
    if (action === "notes" || action === "notesall") {
      const idx = action === "notes" ? [view.cur] : deck.slides.map((_, i) => i);
      const slides = idx.map((i) => ({ n: i + 1, text: deck.slides[i].elements.map((e) => (e.text ? plainText(e.text) : "")).filter(Boolean).join("\n").slice(0, 3000) }));
      if (!slides.some((x) => x.text.trim())) { toast(t("sl_ai_no_text")); return; }
      const pm = progressModal(t("sl_ai_writing_notes"));
      const out = [];
      try {
        for (let k = 0; k < slides.length; k += 12) {
          const chunk = slides.slice(k, k + 12);
          const text = await streamTask("notes", { lang: I18N.lang, slides: chunk }, { signal: pm.signal, onDelta: (x) => pm.set(t("sl_ai_progress_n", { n: Math.min(slides.length, k + chunk.length), total: slides.length }), x.slice(-400)) });
          out.push(...(parseJsonLoose(text).notes || []));
        }
      } finally { pm.close(); }
      view.change(() => { for (const n of out) { const s = deck.slides[(+n.n || 0) - 1]; if (s && idx.includes(+n.n - 1) && typeof n.text === "string") { s.notes = n.text.trim(); view.touch(s); } } });
      view.$notes.value = view.slide.notes || "";
      if (view.el.classList.contains("no-notes")) view.cmd("view:notes");
      toast(t("sl_ai_notes_done", { n: out.length }), { kind: "ok" });
      return;
    }
    if (action === "translate") {
      const r = await modal({ title: t("sl_ai_translate"), body: `<label class="field"><span>${esc(t("sl_ai_target_lang"))}</span><select class="input" id="ai-tl">${LANGS.map((l) => `<option value="${l}" ${l === (I18N.lang === "ru" ? "en" : "ru") ? "selected" : ""}>${esc(t("lang_" + l) !== "lang_" + l ? t("lang_" + l) : l.toUpperCase())}</option>`).join("")}</select></label>
        <label class="check"><input type="checkbox" id="ai-tn" checked> ${esc(t("sl_ai_translate_notes"))}</label>`,
        buttons: [{ label: t("cancel"), value: null }, { label: t("sl_ai_translate_go"), primary: true, value: (b) => ({ lang: b.querySelector("#ai-tl").value, notes: b.querySelector("#ai-tn").checked }) }] });
      if (!r) return;
      const items = [];
      deck.slides.forEach((s, si) => {
        const walk = (els) => els.forEach((e) => {
          if (e.kind === "shape" && e.text && plainText(e.text).trim()) items.push({ id: `${si}:${e.id}`, text: itemText(e), el: e, s });
          if (e.kind === "table") e.table.rows.forEach((row, ri) => row.cells.forEach((c, ci) => { if (plainText(c.text).trim()) items.push({ id: `${si}:${e.id}:${ri}:${ci}`, text: plainText(c.text), cell: c, s }); }));
          if (e.kind === "group") walk(e.children || []);
        });
        walk(s.elements);
        if (r.notes && s.notes?.trim()) items.push({ id: `${si}:notes`, text: s.notes, notes: true, s });
      });
      if (!items.length) { toast(t("sl_ai_no_text")); return; }
      const pm = progressModal(t("sl_ai_translating"));
      const results = new Map();
      try {
        let chunk = [], size = 0;
        const flush = async () => {
          if (!chunk.length) return;
          const text = await streamTask("translate", { target: r.lang, items: chunk.map(({ id, text: tx }) => ({ id, text: tx })) }, { signal: pm.signal, onDelta: (x) => pm.set(t("sl_ai_progress_n", { n: results.size + chunk.length, total: items.length }), x.slice(-300)) });
          for (const it of parseJsonLoose(text).items || []) if (typeof it.text === "string") results.set(String(it.id), it.text);
          chunk = []; size = 0;
        };
        for (const it of items) { chunk.push(it); size += it.text.length; if (chunk.length >= 40 || size > 6000) await flush(); }
        await flush();
      } finally { pm.close(); }
      view.change(() => {
        for (const it of items) {
          const tx = results.get(it.id); if (tx === undefined) continue;
          if (it.notes) it.s.notes = tx;
          else if (it.cell) it.cell.text.paras = linesToParas(tx.split("\n"), it.cell.text.paras);
          else it.el.text.paras = linesToParas(tx.split("\n"), it.el.text.paras);
          view.touch(it.s);
        }
      }, { thumbsAll: true });
      view.$notes.value = view.slide.notes || "";
      toast(t("sl_ai_translated", { n: results.size, total: items.length }), { kind: results.size < items.length ? "" : "ok", ms: 5000 });
    }
  } catch (e) { fail(e); }
  finally { if (view.el.isConnected && !view.editing) view.$stage.focus({ preventScroll: true }); }
}
