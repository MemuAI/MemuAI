/* MemuAI Proxy — Round 33 (r33-thinking): «Reasoning levels» section of the model card.

   Shows which reasoning levels clients (the chat window, Quick Access, MemuCode, Telegram) offer for the model, how the
   Proxy passes them to the API, what the API rejected earlier — and lets the user set the profile by hand for a
   model the table does not know yet (a new release, an unusual gateway).

   API: GET /api/admin/reasoning/profile?model=, POST /api/admin/reasoning/override, POST /api/admin/reasoning/forget
   (app/reasoning_levels_api.py). model_caps.js calls MemuReasoningCard.placeholder(card) in renderCard. */
"use strict";
(function () {
  const L2 = (ru, en) => (typeof LANG !== "undefined" && LANG === "en" ? en : ru);
  const DEPTHS = ["minimal", "low", "medium", "high", "xhigh", "max"];
  const NAMES = {
    auto: ["Авто", "Auto"], off: ["Выкл.", "Off"], on: ["Вкл.", "On"], minimal: ["Минимальный", "Minimal"],
    low: ["Низкий", "Low"], medium: ["Средний", "Medium"], high: ["Высокий", "High"],
    xhigh: ["Очень высокий", "Extra high"], max: ["Максимальный", "Max"],
  };
  const name = (level) => { const pair = NAMES[level] || [level, level]; return L2(pair[0], pair[1]); };
  const SOURCES = {
    docs: ["по документации провайдера", "from the provider's documentation"],
    provider: ["по правилам этого подключения", "by this connection's rules"],
    guess: ["предположение: модель незнакома", "an assumption: the model is unknown"],
    override: ["задано вручную", "set by hand"],
  };
  const DIALECTS = [
    ["", ["Автоматически", "Automatic"]],
    ["effort", ["reasoning_effort (OpenAI, Gemini, xAI, шлюзы)", "reasoning_effort (OpenAI, Gemini, xAI, gateways)"]],
    ["qwen", ["enable_thinking + thinking_budget (Qwen)", "enable_thinking + thinking_budget (Qwen)"]],
    ["thinking_type", ["thinking.type + reasoning_effort (DeepSeek, GLM, Kimi)", "thinking.type + reasoning_effort (DeepSeek, GLM, Kimi)"]],
    ["openrouter", ["reasoning {effort | enabled} (OpenRouter)", "reasoning {effort | enabled} (OpenRouter)"]],
    ["anthropic", ["thinking.budget_tokens (Anthropic)", "thinking.budget_tokens (Anthropic)"]],
  ];
  const KINDS = [
    ["levels", ["Уровни", "Levels"]], ["toggle", ["Только вкл. / выкл.", "On / off only"]],
    ["fixed", ["Думает сама, выбирать нечего", "Reasons on its own, nothing to choose"]], ["none", ["Без размышлений", "No reasoning"]],
  ];
  const state = { model: "", data: null, editing: false, seq: 0 };

  function style() {
    if (document.getElementById("mrc-style")) return;
    const css = document.createElement("style");
    css.id = "mrc-style";
    css.textContent = `
.mrc-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 8px; }
.mrc-chip { display: inline-flex; align-items: center; height: 24px; padding: 0 9px; border-radius: 999px; font-size: 12px; font-weight: 600;
  color: var(--text); background: color-mix(in srgb, var(--accent) 10%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 26%, transparent); }
.mrc-chip.dim { color: var(--muted); background: transparent; box-shadow: inset 0 0 0 1px var(--line, color-mix(in srgb, var(--muted) 30%, transparent)); }
.mrc-chip.bad { color: var(--err, #e5484d); background: color-mix(in srgb, var(--err, #e5484d) 10%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--err, #e5484d) 30%, transparent); text-decoration: line-through; }
.mrc-lines { margin: 0 0 8px; padding: 0; list-style: none; font-size: 12.5px; color: var(--muted); display: grid; gap: 3px; }
.mrc-lines b { color: var(--text); font-weight: 600; }
.mrc-lines code { font-size: 11.5px; }
.mrc-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.mrc-edit { margin-top: 10px; padding: 10px 12px; border-radius: 10px; background: color-mix(in srgb, var(--muted) 7%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--muted) 18%, transparent); display: grid; gap: 9px; }
.mrc-edit label.mrc-row { display: grid; grid-template-columns: 150px 1fr; align-items: center; gap: 10px; font-size: 12.5px; color: var(--muted); }
.mrc-edit select { min-width: 0; max-width: 100%; }
.mrc-levels { display: flex; flex-wrap: wrap; gap: 6px; }
.mrc-levels label { display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 9px; border-radius: 8px; font-size: 12px; cursor: pointer;
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--muted) 28%, transparent); color: var(--text); }
.mrc-levels label:has(input:checked) { background: color-mix(in srgb, var(--accent) 14%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); }
.mrc-levels input { margin: 0; accent-color: var(--accent); }
.mrc-check { display: inline-flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text); cursor: pointer; }
@media (max-width: 520px) { .mrc-edit label.mrc-row { grid-template-columns: 1fr; gap: 4px; } }`;
    document.head.appendChild(css);
  }

  function placeholder(card) {
    style();
    state.model = card.id;
    const seq = ++state.seq;
    setTimeout(() => load(card.id, seq), 0);
    return `<section class="mcap-sec mrc" data-mrc><h4><i class="mcap-ic" data-i="bulb" aria-hidden="true"></i>${esc(L2("Уровни размышлений", "Reasoning levels"))}</h4>
      <div class="mcap-loading" data-mrc-body>${esc(L2("Загружаю…", "Loading…"))}</div></section>`;
  }

  async function load(model, seq) {
    try {
      const data = await api(`/api/admin/reasoning/profile?model=${encodeURIComponent(model)}`);
      if (seq !== state.seq) return;
      state.data = data;
      render();
    } catch (e) {
      const body = document.querySelector("[data-mrc-body]");
      if (body) body.textContent = e.message || String(e);
    }
  }

  function summaryLine(prof) {
    if (prof.kind === "none") return L2("Модель не рассуждает — в клиентах выбора нет.", "The model does not reason — clients show no control.");
    if (prof.kind === "fixed") return L2("Модель рассуждает сама, глубину выбрать нельзя — в клиентах выбора нет.", "The model reasons on its own; the depth cannot be chosen — clients show no control.");
    if (prof.kind === "toggle") return L2("Размышления можно только включить или выключить.", "Reasoning can only be switched on or off.");
    return prof.can_off ? L2("Можно выбрать уровень или выключить размышления.", "A level can be chosen, or reasoning switched off.")
      : L2("Можно выбрать уровень; выключить размышления нельзя.", "A level can be chosen; reasoning cannot be switched off.");
  }

  function dialectName(value) {
    const row = DIALECTS.find(([key]) => key === value);
    return row ? L2(row[1][0], row[1][1]) : value;
  }

  function render() {
    const body = document.querySelector("[data-mrc-body]");
    const data = state.data;
    if (!body || !data) return;
    body.classList.remove("mcap-loading");
    const prof = data.effective || {};
    const chips = (prof.options || []).map((level) => `<span class="mrc-chip">${esc(name(level))}</span>`).join("")
      || `<span class="mrc-chip dim">${esc(L2("нет выбора", "no control"))}</span>`;
    const rejected = (data.learned?.rejected || []).map((level) => `<span class="mrc-chip bad" title="${esc(L2("API отклонил это значение", "The API rejected this value"))}">${esc(name(level))}</span>`).join("");
    const lines = [
      `<li>${esc(summaryLine(prof))}</li>`,
      prof.default ? `<li>${esc(L2("По умолчанию у провайдера:", "Provider default:"))} <b>${esc(name(prof.default).toLowerCase())}</b></li>` : "",
      `<li>${esc(L2("Источник:", "Source:"))} <b>${esc(L2(...(SOURCES[prof.source] || SOURCES.guess)))}</b>${prof.family ? ` · <code>${esc(prof.family)}</code>` : ""}</li>`,
      data.provider === "custom" && data.dialect ? `<li>${esc(L2("Как передаётся в API:", "Sent to the API as:"))} <b>${esc(dialectName(data.dialect))}</b></li>` : "",
      rejected ? `<li>${esc(L2("API уже отклонил:", "The API rejected:"))} ${rejected}</li>` : "",
    ].join("");
    const own = data.override && Object.keys(data.override).length;
    body.innerHTML = `
      <div class="mrc-chips" aria-label="${esc(L2("Что видят клиенты", "What clients show"))}">${chips}</div>
      <ul class="mrc-lines">${lines}</ul>
      <div class="mrc-actions">
        <button type="button" class="btn small" data-mrc-edit aria-expanded="${state.editing}">${esc(state.editing ? L2("Скрыть настройку", "Hide settings") : L2("Настроить вручную", "Set by hand"))}</button>
        ${own ? `<button type="button" class="btn small" data-mrc-reset>${esc(L2("Вернуть автоматически", "Back to automatic"))}</button>` : ""}
        ${rejected ? `<button type="button" class="btn small" data-mrc-forget>${esc(L2("Забыть отказы API", "Forget the API rejections"))}</button>` : ""}
      </div>
      ${state.editing ? editor(data) : ""}`;
  }

  function editor(data) {
    const base = (data.override && Object.keys(data.override).length) ? { ...data.auto, ...data.override } : data.effective;
    const levels = new Set(base.levels || []);
    const kind = base.kind || "levels";
    const dialect = data.override?.dialect || "";
    return `<div class="mrc-edit" data-mrc-form>
      <label class="mrc-row"><span>${esc(L2("Что умеет модель", "What the model does"))}</span>
        <select class="input" data-mrc-kind>${KINDS.map(([v, l]) => `<option value="${v}" ${v === kind ? "selected" : ""}>${esc(L2(l[0], l[1]))}</option>`).join("")}</select></label>
      <div class="mrc-row" style="display:grid;grid-template-columns:150px 1fr;gap:10px;align-items:center;font-size:12.5px;color:var(--muted)" data-mrc-levels-row ${kind === "levels" ? "" : "hidden"}>
        <span>${esc(L2("Уровни", "Levels"))}</span>
        <div class="mrc-levels">${DEPTHS.map((level) => `<label><input type="checkbox" data-mrc-level="${level}" ${levels.has(level) ? "checked" : ""}>${esc(name(level))}</label>`).join("")}</div></div>
      <label class="mrc-check" data-mrc-off-row ${["levels", "toggle"].includes(kind) ? "" : "hidden"}><input type="checkbox" data-mrc-off ${base.can_off ? "checked" : ""}>${esc(L2("Размышления можно выключить", "Reasoning can be switched off"))}</label>
      <label class="mrc-row" data-mrc-default-row ${["levels", "toggle"].includes(kind) ? "" : "hidden"}><span>${esc(L2("По умолчанию у провайдера", "Provider default"))}</span>
        <select class="input" data-mrc-default>${["", "off", "on", ...DEPTHS].map((v) => `<option value="${v}" ${v === (base.default || "") ? "selected" : ""}>${esc(v ? name(v) : L2("неизвестно", "unknown"))}</option>`).join("")}</select></label>
      ${data.provider === "custom" ? `<label class="mrc-row" data-mrc-dialect-row ${["levels", "toggle"].includes(kind) ? "" : "hidden"}><span>${esc(L2("Как передавать", "How to send"))}</span>
        <select class="input" data-mrc-dialect>${DIALECTS.map(([v, l]) => `<option value="${v}" ${v === dialect ? "selected" : ""}>${esc(L2(l[0], l[1]))}</option>`).join("")}</select></label>` : ""}
      <div class="mrc-actions"><button type="button" class="btn small primary" data-mrc-save>${esc(L2("Сохранить", "Save"))}</button></div>
    </div>`;
  }

  function syncForm(form) {
    const kind = form.querySelector("[data-mrc-kind]").value;
    const pick = ["levels", "toggle"].includes(kind);
    form.querySelector("[data-mrc-levels-row]").hidden = kind !== "levels";
    form.querySelector("[data-mrc-off-row]").hidden = !pick;
    form.querySelector("[data-mrc-default-row]").hidden = !pick;
    const dialectRow = form.querySelector("[data-mrc-dialect-row]");
    if (dialectRow) dialectRow.hidden = !pick;
  }

  async function post(path, body, message) {
    try {
      state.data = await api(path, { method: "POST", body });
      render();
      toast(message, "ok", 1600);
      if (window.MemuModelCaps && typeof window.MemuModelCaps.load === "function") window.MemuModelCaps.load(true);
    } catch (e) { toast(e.message || String(e), "err"); }
  }

  document.addEventListener("click", (event) => {
    if (!event.target.closest || !event.target.closest("[data-mrc]")) return;
    if (event.target.closest("[data-mrc-edit]")) { state.editing = !state.editing; render(); return; }
    if (event.target.closest("[data-mrc-reset]")) {
      state.editing = false;
      post("/api/admin/reasoning/override", { model: state.model, override: null }, L2("Возвращено к автоматическому", "Back to automatic"));
      return;
    }
    if (event.target.closest("[data-mrc-forget]")) {
      post("/api/admin/reasoning/forget", { model: state.model }, L2("Отказы API забыты", "The API rejections are forgotten"));
      return;
    }
    if (event.target.closest("[data-mrc-save]")) {
      const form = document.querySelector("[data-mrc-form]");
      const kind = form.querySelector("[data-mrc-kind]").value;
      const levels = [...form.querySelectorAll("[data-mrc-level]:checked")].map((box) => box.dataset.mrcLevel);
      if (kind === "levels" && !levels.length) { toast(L2("Отметьте хотя бы один уровень", "Tick at least one level"), "warn"); return; }
      const override = { kind, levels: kind === "levels" ? levels : [], can_off: !!form.querySelector("[data-mrc-off]")?.checked,
        default: form.querySelector("[data-mrc-default]")?.value || "" };
      const dialect = form.querySelector("[data-mrc-dialect]")?.value;
      if (dialect) override.dialect = dialect;
      state.editing = false;
      post("/api/admin/reasoning/override", { model: state.model, override }, L2("Сохранено — клиенты покажут эти уровни", "Saved — clients will show these levels"));
    }
  });
  document.addEventListener("change", (event) => {
    const form = event.target.closest && event.target.closest("[data-mrc-form]");
    if (form && event.target.matches("[data-mrc-kind]")) syncForm(form);
  });

  window.MemuReasoningCard = { placeholder };
})();
