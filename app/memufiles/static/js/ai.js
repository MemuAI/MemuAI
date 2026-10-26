

import { t } from "./i18n.js";
import { S, api, $, $$, h, esc, icon, fmtSize, fmtNum, itemsWord, baseName, toast, showError, isWin, modal } from "./core.js";
import { renderMarkdown } from "./preview.js";

function setMd(el, text) {
  const s = String(text || "").trim();
  if (!s || !/[*_`#\[>]|^\s*([-+]|\d+\.)\s/m.test(s)) { el.classList.remove("md"); el.textContent = s; return; }
  el.classList.add("md"); el.innerHTML = renderMarkdown(s);
}

const st = { messages: [], log: null, models: [], model: "", status: null, busy: false, reqId: "", selection: [], plans: new Map(), version: 0 };

function cancelRequest() {
  if (st.reqId) api("ai/stop", { id: st.reqId }).catch(() => {});
  if (st.abort) st.abort.abort();
}

function syncBusy() {
  if (!st.ui) return;
  st.ui.send.hidden = st.busy;
  st.ui.stop.hidden = !st.busy;
}

function scope(app) { const p = app.pane(); return (p && p.folder) || (p && p.search && p.search.root) || S.home; }

export function mount(app, body) {
  const wrap = h("div", { class: "ai" });
  const top = h("div", { class: "ai-top" });
  const modelSel = h("button", { class: "btn sm ai-model", title: t("ai.model"), text: t("ai.model"), "aria-haspopup": "dialog" });
  const newChat = h("button", { class: "icon-btn sm", title: t("ai.new_chat"), html: icon("plus") });
  newChat.addEventListener("click", () => {
    cancelRequest(); st.version++; st.busy = false; st.reqId = "";
    st.messages = []; st.log = null; st.plans.clear(); st.lastStep = null;
    mount(app, body);
  });
  top.append(modelSel, newChat);

  const scopeEl = h("div", { class: "ai-scope", html: icon("folder", "sm") + `<span class="sc-label"></span><span class="sc-path"></span>` });
  if (!st.log) { st.log = h("div", { class: "ai-log" }); hello(app); }
  const input = h("textarea", { class: "input", placeholder: t("ai.placeholder"), rows: "3" });
  const send = h("button", { class: "btn primary sm", html: icon("send") + esc(t("ai.send")) });
  const stop = h("button", { class: "btn sm", html: icon("stop") + esc(t("ai.stop")), hidden: true });
  const selInfo = h("span", { class: "muted small", style: { flex: "1", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" } });
  const row2 = h("div", { class: "row2" }, selInfo, stop, send);
  const inputBox = h("div", { class: "ai-input" }, input, row2);
  wrap.append(top, scopeEl, st.log, inputBox);
  body.innerHTML = "";
  body.append(wrap);
  st.ui = { body, input, send, stop, modelSel, scopeEl, selInfo };
  syncBusy();
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(app); }
  });
  send.addEventListener("click", () => ask(app));
  stop.addEventListener("click", cancelRequest);
  modelSel.addEventListener("click", () => modelPicker(app));
  selectionChanged(app);
  loadStatus(app);
  st.log.scrollTop = st.log.scrollHeight;
  setTimeout(() => input.focus(), 30);
}

function hello(app) {
  const box = h("div", {});
  box.innerHTML = `<div class="ai-hello"><img class="logo" src="memufiles.svg" alt=""><b>${esc(t("ai.hello_title"))}</b>${esc(t("ai.hello_text"))}</div>`;
  const sugg = h("div", { class: "ai-suggest" });
  for (const key of ["ai.s1", "ai.s2", "ai.s3", "ai.s4", "ai.s5"]) {
    const b = h("button", { text: t(key) });
    b.addEventListener("click", () => { st.ui.input.value = t(key); ask(app); });
    sugg.append(b);
  }
  box.append(sugg);
  st.log.append(box);
}

async function loadStatus(app, force = false) {
  const sel = st.ui.modelSel;
  try {
    const s = await api("ai/status" + (force ? "?force=1" : ""));
    if (sel !== st.ui.modelSel) return;
    st.status = s;
    st.models = s.models || [];
    if (!s.enabled) { sel.textContent = t("ai.disabled"); return; }
    if (!s.online) { sel.textContent = t("ai.offline"); note(t("ai.offline_hint"), true); return; }
    if (st.offlineNote) { st.offlineNote.remove(); st.offlineNote = null; }
    const chosen = st.model || s.model || s.default_model;
    const model = st.models.find((m) => m.id === chosen) || st.models.find((m) => m.id === s.default_model) || st.models[0];
    st.model = model?.id || "";
    sel.textContent = model ? (model.label || model.id) : t("ai.no_model");
    sel.title = model ? `${model.label || model.id}\n${model.provider || ""}\n${model.id}` : t("ai.model");
  } catch (err) {
    if (sel !== st.ui.modelSel) return;
    st.status = { online: false }; st.models = [];
    sel.textContent = t("ai.offline");
  }
}

async function modelPicker(app) {
  await loadStatus(app);
  const search = h("input", { class: "input", placeholder: t("ai.model_search"), autofocus: true, type: "search" });
  const list = h("div", { class: "ai-model-list", role: "listbox", "aria-label": t("ai.model") });
  const refresh = h("button", { class: "btn sm", text: t("ai.models_refresh") });
  const content = h("div", { class: "ai-model-picker" }, h("div", { class: "row2" }, search, refresh), list);
  const dialog = modal({ title: t("ai.model"), body: content });
  const render = () => {
    list.replaceChildren();
    const words = search.value.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const rows = st.models.filter((m) => words.every((w) => `${m.label || ""} ${m.provider || ""} ${m.id}`.toLocaleLowerCase().includes(w)));
    for (const m of rows) {
      const row = h("button", { class: "ai-model-row" + (st.model === m.id ? " active" : ""), role: "option", "aria-selected": st.model === m.id ? "true" : "false" },
        h("strong", { text: m.label || m.id }), h("small", { text: [m.provider, m.context ? fmtNum(m.context) + " tokens" : "", m.id].filter(Boolean).join(" · ") }));
      row.addEventListener("click", async () => {
        try {
          await api("settings", { ai_model: m.id });
          st.model = m.id; st.ui.modelSel.textContent = m.label || m.id; st.ui.modelSel.title = m.id;
          dialog.close(m.id);
        } catch (err) { showError(err); }
      });
      list.append(row);
    }
    if (!rows.length) list.append(h("p", { class: "muted", text: st.status?.online ? t("ai.models_empty") : t("ai.offline_hint") }));
  };
  search.addEventListener("input", render);
  search.addEventListener("keydown", (e) => { if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); list.querySelector("button")?.focus(); } });
  list.addEventListener("keydown", (e) => {
    if (!["ArrowDown", "ArrowUp"].includes(e.key)) return;
    e.preventDefault();
    const rows = [...list.querySelectorAll("button")], i = rows.indexOf(document.activeElement);
    rows[(i + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length]?.focus();
  });
  refresh.addEventListener("click", async () => { refresh.disabled = true; await loadStatus(app, true); refresh.disabled = false; render(); });
  render();
}

function note(text, err) {
  if (st.offlineNote && st.offlineNote.isConnected) return;
  const el = h("div", { class: "ai-msg bot" + (err ? " err" : ""), text });
  st.offlineNote = el;
  st.log.append(el);
}

export function selectionChanged(app) {
  if (!st.ui || !st.ui.scopeEl.isConnected) return;
  const sc = scope(app);
  const [label] = t("ai.scope", { path: "\u0000" }).split("\u0000");
  st.ui.scopeEl.querySelector(".sc-label").textContent = label.trim();
  st.ui.scopeEl.querySelector(".sc-path").textContent = sc;
  st.ui.scopeEl.title = sc;
  const pane = app.pane();
  st.selection = pane ? pane.selectedPaths() : [];
  st.ui.selInfo.textContent = st.selection.length ? t("ai.with_selection", { n: itemsWord(st.selection.length) }) : "";
}

export function prefill(app, paths) {
  setTimeout(() => {
    if (!st.ui) return;
    st.selection = paths;
    st.ui.input.value = paths.length === 1 ? t("ai.about_one", { name: baseName(paths[0]) }) : t("ai.about_many", { n: itemsWord(paths.length) });
    st.ui.input.focus();
    st.ui.input.setSelectionRange(st.ui.input.value.length, st.ui.input.value.length);
  }, 50);
}

function bubble(role, text) {
  const el = h("div", { class: "ai-msg " + role });
  el.textContent = text;
  st.log.append(el);
  st.log.scrollTop = st.log.scrollHeight;
  return el;
}

const TOOL_LABEL = { find_files: "ai.t_find", list_folder: "ai.t_list", read_file: "ai.t_read", file_info: "ai.t_info", folder_stats: "ai.t_stats", propose_plan: "ai.t_plan", classify_docs: "ai.t_classify" };

async function ask(app) {
  const version = st.version;
  const text = st.ui.input.value.trim();
  if (!text || st.busy) return;
  if (!st.status?.online || !st.model) await loadStatus(app, true);
  if (version !== st.version || st.busy) return;
  if (!st.status?.online) { toast(t("ai.offline_hint"), "err"); return; }
  if (st.status.enabled === false) { toast(t("ai.disabled"), "err"); return; }
  if (!st.model) { toast(t("ai.no_model"), "err"); return; }
  st.ui.input.value = "";
  const sugg = $(".ai-suggest", st.log);
  if (sugg) sugg.remove();
  bubble("user", text);
  st.messages.push({ role: "user", content: text });
  st.busy = true;
  syncBusy();
  let current = h("div", { class: "ai-msg bot", html: '<span class="ai-typing"><i></i><i></i><i></i></span>' });
  st.log.append(current);
  let currentText = "";
  let finalText = "";
  st.reqId = Math.random().toString(36).slice(2);
  st.abort = new AbortController();
  const sc = scope(app);
  try {
    const res = await fetch("/api/ai/chat", { method: "POST", signal: st.abort.signal,
      headers: { "Content-Type": "application/json", "X-MF-Token": S.token },
      body: JSON.stringify({ id: st.reqId, scope: sc, model: st.model, messages: st.messages, selection: st.selection }) });
    if (!res.ok) { let d = {}; try { d = await res.json(); } catch (e) {  } throw Object.assign(new Error(d.error || res.status), { code: d.error }); }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (version !== st.version) { reader.cancel().catch(() => {}); return; }
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!chunk.startsWith("data: ")) continue;
        const ev = JSON.parse(chunk.slice(6));
        if (ev.t === "delta") { currentText += ev.text; current.textContent = currentText; st.log.scrollTop = st.log.scrollHeight; }
        else if (ev.t === "step") {
          if (currentText.trim()) setMd(current, currentText); else current.remove();
          currentText = "";
          const step = h("div", { class: "ai-step", html: icon(ev.tool === "propose_plan" ? "checkAll" : ev.tool === "read_file" ? "file" : "search") + `<span>${esc(t(TOOL_LABEL[ev.tool] || "ai.t_tool"))}</span>` + stepArg(ev) });
          st.log.append(step);
          st.lastStep = step;
          current = h("div", { class: "ai-msg bot", html: '<span class="ai-typing"><i></i><i></i><i></i></span>' });
          st.log.append(current);
        } else if (ev.t === "result") {
          if (st.lastStep && ev.summary) {
            const s = ev.summary;
            const bits = [];
            if (s.error) bits.push("⚠ " + s.error);
            if (s.count !== undefined) bits.push(itemsWord(s.count));
            if (s.files !== undefined) bits.push(t("pv.files") + " " + fmtNum(s.files));
            if (s.total_size) bits.push(fmtSize(s.total_size));
            if (s.chars !== undefined && s.chars !== null) bits.push(t("ai.chars", { n: fmtNum(s.chars) }));
            if (bits.length) st.lastStep.append(h("span", { class: "muted", text: " · " + bits.join(" · ") }));
          }
        } else if (ev.t === "plan") {
          if (currentText.trim()) setMd(current, currentText); else current.remove();
          st.log.append(planCard(app, ev.plan));
          current = h("div", { class: "ai-msg bot", html: '<span class="ai-typing"><i></i><i></i><i></i></span>' });
          st.log.append(current);
          currentText = "";
          st.messages.push({ role: "assistant", content: `[plan ${ev.plan.id}: ${ev.plan.items.length} items]` });
        } else if (ev.t === "final") { finalText = ev.text; setMd(current, ev.text || currentText || "…"); }
        else if (ev.t === "error") { current.classList.add("err"); current.textContent = errorText(ev); }
        else if (ev.t === "done" && !finalText && !current.classList.contains("err")) {
          setMd(current, currentText || (ev.cancelled ? "⏹" : t("ai.empty_answer")));
        }
        st.log.scrollTop = st.log.scrollHeight;
      }
    }
  } catch (err) {
    if (version !== st.version) return;
    if (err.name === "AbortError") { current.textContent = (currentText || "") + " ⏹"; }
    else { current.classList.add("err"); current.textContent = err.code === "no_model" ? t("ai.no_model") : err.code === "ai_disabled" ? t("ai.disabled") : err.message; }
  } finally {
    if (version === st.version) { st.busy = false; st.reqId = ""; st.abort = null; syncBusy(); }
  }
  if (version !== st.version) return;
  if (!current.textContent.trim() || current.querySelector(".ai-typing")) current.remove();
  if (finalText) st.messages.push({ role: "assistant", content: finalText });
  st.log.scrollTop = st.log.scrollHeight;
}

function stepArg(ev) {
  const a = ev.args || {};
  if (ev.tool === "classify_docs") return ` <code>${esc(t("ai.files_n", { n: fmtNum(a.files || 0) }))}</code>`;
  const v = a.path || a.root || a.name || (a.ext ? [].concat(a.ext).join(",") : "") || (a.max_size ? "≤ " + a.max_size : "") || (a.max_chars ? "≤ " + a.max_chars + " " + t("ai.chars_short") : "") || a.title || "";
  return v ? ` <code>${esc(String(v))}</code>` : "";
}
function errorText(ev) {
  if (ev.kind === "offline") return t("ai.offline_hint");
  return t("ai.error") + ": " + (ev.message || "");
}

const OP_LABEL = { trash: "plan.op_trash", delete: "plan.op_delete", move: "plan.op_move", copy: "plan.op_copy", rename: "plan.op_rename", mkdir: "plan.op_mkdir", organize: "plan.op_organize", write: "plan.op_write" };
function planCard(app, plan) {
  const card = h("div", { class: "plan" });
  const state = { checked: new Set(plan.items.map((i) => i.i)), permanent: false, ops: [], done: false };
  st.plans.set(plan.id, { plan, state, card });
  const head = h("div", { class: "plan-head", html: icon("checkAll") + `<b>${esc(plan.title || t("plan.title"))}</b>` });
  const totals = h("div", { class: "plan-totals" });
  const list = h("div", { class: "plan-list" });
  const SHOW = 400;
  const scopeRel = (p) => (p.startsWith(plan.scope) ? p.slice(plan.scope.length).replace(/^[\\/]/, "") || p : p);

  const groups = {};
  const groupHead = (cat, rows) => {
    const cb = h("input", { type: "checkbox" });
    const sync = () => { const on = rows.filter((r) => state.checked.has(r.i)).length; cb.checked = on === rows.length; cb.indeterminate = on > 0 && on < rows.length; };
    const g = groups[cat] = { sync, rows: [] };
    cb.addEventListener("change", () => {
      for (const r of rows) { if (cb.checked) state.checked.add(r.i); else state.checked.delete(r.i); }
      for (const [box, row] of g.rows) { box.checked = cb.checked; row.classList.toggle("off", !cb.checked); }
      renderTotals();
    });
    sync();
    return h("label", { class: "plan-group" }, cb, h("span", { class: "pg-ico", html: icon("folder") }),
      h("span", { class: "pg-name", title: cat, text: cat === "." ? t("plan.org_here") : cat }), h("span", { class: "pg-n", text: fmtNum(rows.length) }));
  };
  const renderList = (limit) => {
    list.innerHTML = "";
    let lastCat = null;
    const byCat = {};
    for (const item of plan.items) if (item.op === "organize") (byCat[item.category] = byCat[item.category] || []).push(item);
    const ordered = plan.items.some((i) => i.op === "organize")
      ? plan.items.filter((i) => i.op !== "organize").concat(Object.keys(byCat).sort((a, b) => a.localeCompare(b)).flatMap((k) => byCat[k])) : plan.items;
    for (const item of ordered.slice(0, limit)) {
      if (item.op === "organize" && item.category !== lastCat) { lastCat = item.category; list.append(groupHead(item.category, byCat[item.category])); }
      if (item.op === "organize") {
        const cb = h("input", { type: "checkbox" });
        cb.checked = state.checked.has(item.i);
        const old = baseName(item.path);
        const row = h("label", { class: "plan-item org" + (cb.checked ? "" : " off"), title: item.path + " → " + item.dest + "/" + item.new_name });

        row.append(cb, h("span", { class: "pi-org" }, h("span", { class: "pi-new", text: item.new_name }),
          h("span", { class: "pi-sub" }, ...(old !== item.new_name ? [h("span", { class: "pi-old", text: old })] : []),
            ...(item.reason ? [h("span", { class: "pi-why", text: item.reason })] : []))));
        const g = groups[item.category];
        g.rows.push([cb, row]);
        cb.addEventListener("change", () => { if (cb.checked) state.checked.add(item.i); else state.checked.delete(item.i); row.classList.toggle("off", !cb.checked); g.sync(); renderTotals(); });
        list.append(row);
        continue;
      }
      const cb = h("input", { type: "checkbox" });
      cb.checked = state.checked.has(item.i);
      const row = h("label", { class: "plan-item" + (cb.checked ? "" : " off") });
      const target = item.op === "rename" ? ` → ${item.new_name}` : (item.op === "move" || item.op === "copy") ? ` → ${scopeRel(item.dest)}` : "";
      row.append(cb, h("span", { class: "pi-op " + item.op, text: t(OP_LABEL[item.op]) }), h("span", { class: "pi-path", title: item.path + target, text: "\u200e" + scopeRel(item.path) + target + "\u200e" }),
        h("span", { class: "pi-size", text: item.is_dir ? "" : (item.chars !== undefined ? t("ai.chars", { n: item.chars }) + " · " : "") + fmtSize(item.size || 0) }));
      cb.addEventListener("change", () => { if (cb.checked) state.checked.add(item.i); else state.checked.delete(item.i); row.classList.toggle("off", !cb.checked); renderTotals(); });
      list.append(row);
      if (item.op === "write" && item.diff) {
        const preview = h("details", { class: "plan-diff" }, h("summary", { text: t("plan.diff") }), h("pre", { text: item.diff }));
        if (item.diff_truncated) preview.append(h("p", { class: "muted", text: t("plan.diff_truncated") }));
        list.append(preview);
      }
    }
    if (plan.items.length > limit) {
      const more = h("button", { class: "btn sm ghost", style: { margin: "6px 12px" }, text: t("plan.show_all", { n: fmtNum(plan.items.length - limit) }) });
      more.addEventListener("click", () => renderList(plan.items.length));
      list.append(more);
    }
  };
  const renderTotals = () => {
    const by = {};
    for (const item of plan.items) if (state.checked.has(item.i)) { const k = item.op === "delete" && !state.permanent ? "trash" : item.op; by[k] = by[k] || { count: 0, size: 0 }; by[k].count++; by[k].size += item.size || 0; }
    totals.innerHTML = Object.entries(by).map(([op, v]) => `<span class="chip">${esc(t(OP_LABEL[op]))}: ${esc(itemsWord(v.count))}${v.size ? " · " + esc(fmtSize(v.size)) : ""}</span>`).join("") ||
      `<span class="muted">${esc(t("plan.nothing"))}</span>`;
    run.disabled = !state.checked.size || state.done;
    run.innerHTML = icon("check") + esc(t("plan.run", { n: fmtNum(state.checked.size) }));
  };
  card.append(head, totals);
  if (plan.rejected && plan.rejected.length) {
    const why = {};
    for (const r of plan.rejected) why[r.why] = (why[r.why] || 0) + 1;
    card.append(h("div", { class: "plan-rej", text: t("plan.rejected", { n: plan.rejected.length }) + ": " + Object.entries(why).map(([k, v]) => `${t("plan.why." + k)} ${v}`).join(", ") }));
  }
  if (plan.has_permanent) {
    const perm = h("input", { type: "checkbox" });
    const warn = h("div", { class: "warn-perm" }, h("label", { class: "check" }, perm, t("plan.permanent")));
    perm.addEventListener("change", () => { state.permanent = perm.checked; renderTotals(); });
    card.append(warn);
  }
  card.append(list);
  const foot = h("div", { class: "plan-foot" });
  const allBox = h("input", { type: "checkbox" }); allBox.checked = true;
  allBox.addEventListener("change", () => { state.checked = allBox.checked ? new Set(plan.items.map((i) => i.i)) : new Set(); renderList(SHOW); renderTotals(); });
  const left = h("div", { class: "left" }, h("label", { class: "check" }, allBox, t("plan.all")));
  const cancel = h("button", { class: "btn sm ghost", text: t("common.cancel") });
  const run = h("button", { class: "btn sm primary" });
  cancel.addEventListener("click", () => { state.done = true; card.classList.add("done"); foot.innerHTML = `<span class="muted small">${esc(t("plan.cancelled"))}</span>`; });
  run.addEventListener("click", async () => {
    if (state.permanent) {
      const { confirmDialog } = await import("./core.js");
      const ok = await confirmDialog({ title: t("del.perm_title"), text: t("plan.perm_confirm", { n: itemsWord(state.checked.size) }), ok: t("del.perm_ok"), danger: true });
      if (!ok) return;
    }
    run.disabled = true;
    try {
      const res = await api("ai/execute", { plan_id: plan.id, selected: [...state.checked], permanent: state.permanent });
      state.done = true;
      state.ops = res.ops;
      card.classList.add("done");
      foot.innerHTML = "";
      const status = h("span", { class: "muted small", text: t("plan.running") });
      foot.append(status);
      state.statusEl = status;
      state.foot = foot;
      if (res.skipped && res.skipped.length) foot.append(h("span", { class: "muted small", text: " · " + t("plan.skipped", { n: res.skipped.length }) }));
      for (const id of res.ops) { const op = app.ops.get(id); if (op) opUpdated(app, op); }
      if (!res.ops.length) status.textContent = t("plan.nothing_done");
    } catch (err) { run.disabled = false; showError(err); }
  });
  foot.append(left, cancel, run);
  card.append(foot);
  renderList(SHOW);
  renderTotals();
  return card;
}

export function opUpdated(app, op) {
  for (const { state } of st.plans.values()) {
    if (!state.ops || !state.ops.includes(op.id) || !state.statusEl) continue;
    const ops = state.ops.map((id) => app.ops.get(id)).filter(Boolean);
    const finished = ops.length === state.ops.length && ops.every((o) => ["done", "error", "cancelled"].includes(o.status));
    const done = ops.reduce((a, o) => a + (o.done_items || 0), 0);
    const errors = ops.reduce((a, o) => a + (o.error_count || 0), 0);
    if (!finished) { state.statusEl.textContent = t("plan.progress", { n: fmtNum(done) }); continue; }
    state.statusEl.textContent = t("plan.finished", { n: fmtNum(done) }) + (errors ? " · " + t("ops.errors", { n: errors }) : "");
    if (!state.undoBtn && ops.some((o) => o.undoable)) {
      const undo = h("button", { class: "btn sm", html: icon("undo") + esc(t("cmd.undo")) });
      undo.addEventListener("click", async () => {
        undo.disabled = true;
        for (const o of ops.slice().reverse()) if (o.undoable) { try { const r = await api("ops/undo", { id: o.id }); app.trackOp(r.op); } catch (err) { showError(err); } }
        state.statusEl.textContent = t("plan.undone");
      });
      state.undoBtn = undo;
      state.foot.append(undo);
    }
  }
}
