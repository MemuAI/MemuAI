/* Round 35 — ask_user: the model asks with a small form instead of a paragraph of questions.

   Two layers:
   • MemuAskForm — a pure renderer of the shared form schema (app/ask_user.py): html(record, opts) + bind(root, record,
     opts). Used by the chat, Quick Access (quick/index.html loads this file too) and the DFY card.
   • MemuAskUser — the chat/Work integration: app.js::runHiddenTools → MemuAskUser.ask(assistant, args, ctx) opens the
     form on the server (/api/pg/ask_user/open → normalized + Inbox «ждёт ответа»), shows it inside the assistant
     message (assistant.askForms[]), waits for «Готово» / «Пропустить — решай сам» / an Inbox answer / Stop, and
     returns the tool result — the same tool-calling loop continues IN THE SAME TURN. After a reload the pending form
     is restored from the message; answering it resumes the turn (resumeAfterAutomation). A message typed in the
     composer while a form waits is taken as the answer (interceptSend). */
(function () {
  "use strict";
  let langHook = null;     // Quick Access / Mini App pass their own language (MemuAskForm.setLang)
  const lang = () => (langHook ? langHook() : typeof PG_LANG !== "undefined" ? PG_LANG : (document.documentElement.lang || "ru")) === "en" ? "en" : "ru";
  const L = (ru, en) => lang() === "en" ? en : ru;
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ICON = {
    ask: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a8.5 8.5 0 0 1-12.4 7.6L4 21l1.4-4.6A8.5 8.5 0 1 1 21 12Z"/><path d="M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.6"/><path d="M12 17h.01"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12.5 4.2 4.2L19 7"/></svg>',
    skip: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 6 6-6 6"/><path d="m13 6 6 6-6 6"/></svg>',
    minus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg>',
  };
  const ico = (name) => `<span class="au-ico">${ICON[name] || ""}</span>`;

  /* ------------------------------------------------------------------ drafts (survive chat re-renders) */
  const drafts = new Map();        // form id → {values:{id: value}, other:{id: text}, otherOn:{id: bool}, errors:{id: code}}
  function draft(record) {
    let d = drafts.get(record.id);
    if (!d) {
      d = { values: {}, other: {}, otherOn: {}, errors: {} };
      for (const f of record.form?.fields || []) {
        if (f.default !== undefined && f.default !== null) {
          if (f.type === "choice" && !(f.options || []).includes(f.default)) { d.otherOn[f.id] = true; d.other[f.id] = String(f.default); }
          else d.values[f.id] = Array.isArray(f.default) ? [...f.default] : f.default;
        } else if (f.type === "multi") d.values[f.id] = [];
      }
      drafts.set(record.id, d);
    }
    return d;
  }
  const isoDay = (offset) => { const t = new Date(); t.setDate(t.getDate() + offset);
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`; };
  function fmtValue(field, value) {
    if (typeof value === "boolean") return value ? L("Да", "Yes") : L("Нет", "No");
    if (Array.isArray(value)) return value.join(", ");
    if (field.type === "date" && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
      const [y, m, d] = String(value).split("-").map(Number);
      try { return new Date(y, m - 1, d).toLocaleDateString(lang() === "en" ? "en-US" : "ru-RU", { day: "numeric", month: "short", year: "numeric" }); } catch (e) { return String(value); }
    }
    if (field.type === "number" && field.unit) return `${value} ${field.unit}`;
    return String(value);
  }
  function summaryOf(record) {
    if (record.status === "skipped") return L("Пропущено — модель решает сама", "Skipped — the model decides");
    if (record.status === "timeout") return L("Нет ответа — модель решила сама", "No answer — the model decided");
    if (record.status === "cancelled") return L("Вопрос отменён", "Question cancelled");
    const answers = record.answers || {};
    const parts = (record.form?.fields || []).filter((f) => answers[f.id] !== undefined)
      .map((f) => ({ label: f.label, value: fmtValue(f, answers[f.id]) }));
    if (answers._reply) parts.push({ label: L("Ответ", "Reply"), value: String(answers._reply).slice(0, 240) });
    return parts;
  }
  const ERR = {
    required: ["Нужен ответ", "An answer is needed"], not_an_option: ["Выберите вариант", "Pick an option"],
    not_a_number: ["Нужно число", "A number is needed"], below_min: ["Слишком мало", "Too small"],
    above_max: ["Слишком много", "Too large"], not_a_date: ["Нужна дата", "A date is needed"],
  };

  /* ------------------------------------------------------------------ renderer */
  function fieldHtml(record, field, d, opts) {
    const fid = esc(field.id), key = `${esc(record.id)}:${fid}`;
    const err = d.errors[field.id];
    const labelId = `au-l-${esc(record.id)}-${fid}`;
    const req = field.required ? `<span class="au-req" aria-hidden="true">*</span>` : "";
    let control = "";
    if (field.type === "choice" || field.type === "bool") {
      const options = field.type === "bool" ? [[true, L("Да", "Yes")], [false, L("Нет", "No")]]
        : (field.options || []).map((o) => [o, o]);
      const value = d.values[field.id];
      const chips = options.map(([v, label], n) => {
        const on = !d.otherOn[field.id] && value === v;
        return `<button type="button" class="au-chip${on ? " on" : ""}" role="radio" aria-checked="${on}" tabindex="${on || (n === 0 && value === undefined && !d.otherOn[field.id]) ? 0 : -1}"
          data-au-chip="${key}" data-au-v="${esc(JSON.stringify(v))}">${on ? ico("check") : ""}<span>${esc(label)}</span></button>`;
      }).join("");
      const other = field.allow_other ? `<button type="button" class="au-chip au-chip-other${d.otherOn[field.id] ? " on" : ""}" role="radio" aria-checked="${!!d.otherOn[field.id]}" tabindex="${d.otherOn[field.id] ? 0 : -1}" data-au-other="${key}">${d.otherOn[field.id] ? ico("check") : ""}<span>${esc(L("Другое…", "Other…"))}</span></button>` : "";
      control = `<div class="au-chips" role="radiogroup" aria-labelledby="${labelId}">${chips}${other}</div>`;
      if (field.allow_other && d.otherOn[field.id]) control += `<input class="au-input au-other-input" data-au-othertext="${key}" maxlength="300" value="${esc(d.other[field.id] || "")}" placeholder="${esc(L("Свой вариант", "Your own option"))}" aria-label="${esc(field.label + " — " + L("свой вариант", "your own option"))}">`;
    } else if (field.type === "multi") {
      const chosen = d.values[field.id] || [];
      control = `<div class="au-chips au-checks" role="group" aria-labelledby="${labelId}">${(field.options || []).map((o) => {
        const on = chosen.includes(o);
        return `<button type="button" class="au-chip au-check${on ? " on" : ""}" role="checkbox" aria-checked="${on}" data-au-multi="${key}" data-au-v="${esc(JSON.stringify(o))}"><span class="au-box">${on ? ico("check") : ""}</span><span>${esc(o)}</span></button>`;
      }).join("")}${field.allow_other ? `<button type="button" class="au-chip au-check au-chip-other${d.otherOn[field.id] ? " on" : ""}" role="checkbox" aria-checked="${!!d.otherOn[field.id]}" data-au-other="${key}"><span class="au-box">${d.otherOn[field.id] ? ico("check") : ""}</span><span>${esc(L("Другое…", "Other…"))}</span></button>` : ""}</div>`;
      if (field.allow_other && d.otherOn[field.id]) control += `<input class="au-input au-other-input" data-au-othertext="${key}" maxlength="300" value="${esc(d.other[field.id] || "")}" placeholder="${esc(L("Свой вариант", "Your own option"))}" aria-label="${esc(field.label + " — " + L("свой вариант", "your own option"))}">`;
    } else if (field.type === "date") {
      const value = d.values[field.id] || "";
      const quick = [[0, L("Сегодня", "Today")], [1, L("Завтра", "Tomorrow")], [7, L("Через неделю", "In a week")]]
        .map(([n, label]) => [isoDay(n), label])
        .filter(([day]) => (!field.min || day >= field.min) && (!field.max || day <= field.max));
      control = `<div class="au-row"><input type="date" class="au-input au-date" data-au-input="${key}" value="${esc(value)}"${field.min ? ` min="${esc(field.min)}"` : ""}${field.max ? ` max="${esc(field.max)}"` : ""} aria-labelledby="${labelId}">
        ${opts.compact ? "" : `<div class="au-chips au-quickdays">${quick.map(([day, label]) => `<button type="button" class="au-chip au-mini${value === day ? " on" : ""}" data-au-day="${key}" data-au-v="${esc(day)}">${esc(label)}</button>`).join("")}</div>`}</div>`;
    } else if (field.type === "number") {
      const value = d.values[field.id];
      control = `<div class="au-num"><button type="button" class="au-step" data-au-step="${key}" data-au-dir="-1" aria-label="${esc(L("Меньше", "Less"))}">${ico("minus")}</button>
        <input type="number" inputmode="decimal" class="au-input au-number" data-au-input="${key}" value="${esc(value ?? "")}"${field.min !== undefined ? ` min="${esc(field.min)}"` : ""}${field.max !== undefined ? ` max="${esc(field.max)}"` : ""}${field.step ? ` step="${esc(field.step)}"` : ' step="any"'} placeholder="${esc(field.placeholder || (field.min !== undefined && field.max !== undefined ? `${field.min}–${field.max}` : ""))}" aria-labelledby="${labelId}">
        <button type="button" class="au-step" data-au-step="${key}" data-au-dir="1" aria-label="${esc(L("Больше", "More"))}">${ico("plus")}</button>
        ${field.unit ? `<span class="au-unit">${esc(field.unit)}</span>` : ""}</div>`;
    } else {
      const value = d.values[field.id] || "";
      control = field.multiline
        ? `<textarea class="au-input au-text" rows="2" data-au-input="${key}" maxlength="2000" placeholder="${esc(field.placeholder || L("Ваш ответ", "Your answer"))}" aria-labelledby="${labelId}">${esc(value)}</textarea>`
        : `<input class="au-input au-text" data-au-input="${key}" maxlength="2000" value="${esc(value)}" placeholder="${esc(field.placeholder || L("Ваш ответ", "Your answer"))}" aria-labelledby="${labelId}">`;
    }
    return `<div class="au-field au-t-${esc(field.type)}${err ? " has-err" : ""}">
      <div class="au-label" id="${labelId}">${esc(field.label)}${req}</div>${control}
      ${err ? `<div class="au-err" role="alert">${esc(L(...(ERR[err] || ERR.required)))}</div>` : ""}</div>`;
  }

  function html(record, opts = {}) {
    if (!record || !record.form) return "";
    const compact = !!opts.compact;
    if (record.status && record.status !== "pending") {
      const s = summaryOf(record);
      const body = Array.isArray(s)
        ? s.map((p) => `<span class="au-ans"><em>${esc(/[?:!.…]$/.test(p.label) ? p.label : p.label + ":")}</em> ${esc(p.value)}</span>`).join("")
        : `<span class="au-ans au-ans-muted">${esc(s)}</span>`;
      return `<div class="au-card au-done au-s-${esc(record.status)}${compact ? " au-compact" : ""}" data-au-form="${esc(record.id)}">
        <span class="au-done-mark">${record.status === "answered" ? ico("check") : ico("skip")}</span>
        <div class="au-done-body"><span class="au-done-q">${esc(record.form.question || L("Уточнение", "Question"))}</span>${body}</div></div>`;
    }
    const d = draft(record);
    const busy = !!d.busy;
    return `<form class="au-card au-pending${compact ? " au-compact" : ""}" data-au-form="${esc(record.id)}" novalidate aria-label="${esc(record.form.question || L("Уточнение", "Question"))}">
      <div class="au-head">${ico("ask")}<div class="au-title">${esc(record.form.question || L("Уточните, пожалуйста", "A quick question"))}</div>
        <span class="au-badge">${esc(L("ждёт ответа", "waiting"))}</span></div>
      <div class="au-fields">${(record.form.fields || []).map((f) => fieldHtml(record, f, d, opts)).join("")}</div>
      ${d.error ? `<div class="au-err au-form-err" role="alert">${esc(d.error)}</div>` : ""}
      <div class="au-foot">
        <button type="submit" class="au-submit" data-au-submit="${esc(record.id)}"${busy ? " disabled" : ""}>${ico("check")}<span>${esc(record.form.submit_label || L("Готово", "Done"))}</span></button>
        <button type="button" class="au-skip" data-au-skip="${esc(record.id)}"${busy ? " disabled" : ""}>${esc(L("Пропустить — решай сам", "Skip — you decide"))}</button>
        ${compact ? "" : `<span class="au-hint">${esc(L("Ctrl+Enter — готово", "Ctrl+Enter — done"))}</span>`}
      </div></form>`;
  }

  /* answers from the draft in the server format */
  function collect(record) {
    const d = draft(record), out = {};
    for (const f of record.form.fields || []) {
      const v = d.values[f.id];
      if (f.type === "multi") {
        const list = [...(v || [])];
        if (d.otherOn[f.id] && String(d.other[f.id] || "").trim()) list.push(String(d.other[f.id]).trim());
        if (list.length) out[f.id] = list;
      } else if ((f.type === "choice") && d.otherOn[f.id]) {
        if (String(d.other[f.id] || "").trim()) out[f.id] = String(d.other[f.id]).trim();
      } else if (v !== undefined && v !== null && v !== "") out[f.id] = v;
    }
    return out;
  }
  function localErrors(record, answers) {
    const errs = {};
    for (const f of record.form.fields || []) {
      const v = answers[f.id];
      if (v === undefined) { if (f.required) errs[f.id] = "required"; continue; }
      if (f.type === "number") {
        const n = Number(String(v).replace(",", "."));
        if (!Number.isFinite(n)) errs[f.id] = "not_a_number";
        else if (f.min !== undefined && n < f.min) errs[f.id] = "below_min";
        else if (f.max !== undefined && n > f.max) errs[f.id] = "above_max";
      }
    }
    return errs;
  }

  /* registry of mounted forms: id → {record, opts, root?} */
  const mounted = new Map();
  function rerender(id) {
    const entry = mounted.get(id);
    if (!entry) return;
    if (entry.opts.rerender) { entry.opts.rerender(); return; }
    const node = document.querySelector(`[data-au-form="${CSS.escape(id)}"]`);
    if (node) { const tmp = document.createElement("div"); tmp.innerHTML = html(entry.record, entry.opts); node.replaceWith(tmp.firstElementChild); }
  }
  function focusKey(sel) {
    requestAnimationFrame(() => { const el = document.querySelector(sel); if (el) el.focus({ preventScroll: true }); });
  }

  async function submit(id, skip = false) {
    const entry = mounted.get(id);
    if (!entry || entry.record.status !== "pending") return;
    const d = draft(entry.record);
    if (d.busy) return;
    let answers = null;
    if (!skip) {
      answers = collect(entry.record);
      d.errors = localErrors(entry.record, answers);
      if (Object.keys(d.errors).length) { rerender(id); return; }
    }
    d.busy = true; d.error = ""; rerender(id);
    try {
      const done = await entry.opts.onSubmit(skip ? null : answers, skip);
      if (done && done.errors) { d.errors = Object.fromEntries(done.errors.map((e) => [e.field, e.error])); d.busy = false; rerender(id); return; }
    } catch (e) {
      d.busy = false; d.error = String(e?.message || e || L("Не удалось отправить", "Could not send")); rerender(id); return;
    }
    d.busy = false;
  }

  function onClick(ev) {
    const t = ev.target.closest("[data-au-chip],[data-au-other],[data-au-multi],[data-au-day],[data-au-step],[data-au-skip]");
    if (!t) return;
    const card = t.closest("[data-au-form]");
    const entry = card && mounted.get(card.dataset.auForm);
    if (!entry || entry.record.status !== "pending") return;
    ev.preventDefault();
    if (t.dataset.auSkip) { submit(entry.record.id, true); return; }
    const key = t.dataset.auChip || t.dataset.auOther || t.dataset.auMulti || t.dataset.auDay || t.dataset.auStep;
    const fid = key.slice(key.indexOf(":") + 1);
    const field = entry.record.form.fields.find((f) => f.id === fid);
    if (!field) return;
    const d = draft(entry.record);
    delete d.errors[fid];
    let focus = "";
    if (t.dataset.auChip) {
      const v = JSON.parse(t.dataset.auV);
      d.otherOn[fid] = false;
      d.values[fid] = d.values[fid] === v && !field.required ? undefined : v;
      focus = `[data-au-chip="${CSS.escape(key)}"][data-au-v="${CSS.escape(t.dataset.auV)}"]`;
    } else if (t.dataset.auOther) {
      d.otherOn[fid] = !d.otherOn[fid];
      if (field.type === "choice" && d.otherOn[fid]) d.values[fid] = undefined;
      focus = d.otherOn[fid] ? `[data-au-othertext="${CSS.escape(key)}"]` : `[data-au-other="${CSS.escape(key)}"]`;
    } else if (t.dataset.auMulti) {
      const v = JSON.parse(t.dataset.auV);
      const list = [...(d.values[fid] || [])];
      const at = list.indexOf(v);
      if (at >= 0) list.splice(at, 1); else list.push(v);
      d.values[fid] = list;
      focus = `[data-au-multi="${CSS.escape(key)}"][data-au-v="${CSS.escape(t.dataset.auV)}"]`;
    } else if (t.dataset.auDay) {
      d.values[fid] = t.dataset.auV;
      focus = `[data-au-day="${CSS.escape(key)}"][data-au-v="${CSS.escape(t.dataset.auV)}"]`;
    } else if (t.dataset.auStep) {
      const step = Number(field.step) || 1;
      const cur = Number(d.values[fid] ?? field.default ?? field.min ?? 0) || 0;
      let next = Math.round((cur + step * Number(t.dataset.auDir)) * 1e6) / 1e6;
      if (field.min !== undefined) next = Math.max(field.min, next);
      if (field.max !== undefined) next = Math.min(field.max, next);
      d.values[fid] = next;
      focus = `[data-au-step="${CSS.escape(key)}"][data-au-dir="${t.dataset.auDir}"]`;
    }
    rerender(entry.record.id);
    if (focus) focusKey(focus);
  }
  function onInput(ev) {
    const t = ev.target;
    const key = t.dataset?.auInput || t.dataset?.auOthertext;
    if (!key) return;
    const card = t.closest("[data-au-form]");
    const entry = card && mounted.get(card.dataset.auForm);
    if (!entry) return;
    const fid = key.slice(key.indexOf(":") + 1);
    const d = draft(entry.record);
    if (t.dataset.auOthertext) d.other[fid] = t.value;
    else d.values[fid] = t.type === "number" ? (t.value === "" ? undefined : t.value) : t.value;
    if (d.errors[fid]) { delete d.errors[fid]; t.closest(".au-field")?.classList.remove("has-err"); t.closest(".au-field")?.querySelector(".au-err")?.remove(); }
    // a picked quick-day chip follows the date input
    if (t.type === "date") t.closest(".au-field")?.querySelectorAll("[data-au-day]").forEach((b) => b.classList.toggle("on", b.dataset.auV === t.value));
  }
  function onKey(ev) {
    const card = ev.target.closest?.("[data-au-form]");
    if (!card) return;
    const entry = mounted.get(card.dataset.auForm);
    if (!entry || entry.record.status !== "pending") return;
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); submit(entry.record.id); return; }
    if (ev.key === "Enter" && ev.target.matches("input.au-input")) { ev.preventDefault(); submit(entry.record.id); return; }
    // radiogroup: arrows move between chips (WAI-ARIA), Space/Enter pick
    const chip = ev.target.closest(".au-chips[role=radiogroup] .au-chip");
    if (chip && ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) {
      ev.preventDefault();
      const list = [...chip.parentElement.querySelectorAll(".au-chip")];
      let i = list.indexOf(chip);
      i = ev.key === "Home" ? 0 : ev.key === "End" ? list.length - 1
        : (i + (ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
      list.forEach((b) => b.tabIndex = -1); list[i].tabIndex = 0; list[i].focus();
    }
  }
  function onSubmitEvent(ev) {
    const card = ev.target.closest?.("[data-au-form]");
    if (!card || !mounted.has(card.dataset.auForm)) return;
    ev.preventDefault();
    submit(card.dataset.auForm);
  }
  let bound = false;
  function bindDocument() {
    if (bound) return; bound = true;
    document.addEventListener("click", onClick);
    document.addEventListener("input", onInput);
    document.addEventListener("change", onInput);
    document.addEventListener("keydown", onKey);
    document.addEventListener("submit", onSubmitEvent);
  }
  /* mount(record, {compact, onSubmit(answers|null, skipped) → Promise<{errors}|any>, rerender?}) */
  function mount(record, opts) { bindDocument(); mounted.set(record.id, { record, opts: opts || {} }); return record; }
  function update(record) { const entry = mounted.get(record.id); if (entry) entry.record = record; }

  window.MemuAskForm = { html, mount, update, collect, summaryOf, fmtValue, drafts, submit, setLang: (fn) => { langHook = typeof fn === "function" ? fn : null; } };

  /* ================================================================== chat / Work integration */
  const waiters = new Map();      // form id → {resolve, reject, assistant, entry}
  /* Round 36: an answer given elsewhere (Входящие, Telegram, another window) is picked up by ONE slow timer that
     runs only while a form waits and the window is visible, plus an instant check when the window regains focus
     (was: a 3 s setInterval per form). */
  let pollTimer = 0;
  async function pollWaiting() {
    pollTimer = 0;
    if (!waiters.size) return;
    if (!document.hidden) {
      for (const [id, waiter] of [...waiters]) {
        try {
          const now = await http(`/api/pg/ask_user/${encodeURIComponent(id)}`);
          if (now.status && now.status !== "pending" && waiters.has(id)) finish(waiter.assistant, waiter.entry, { record: now, result: resultFrom(now) });
        } catch (e) { /* offline for a moment */ }
      }
    }
    armPoll();
  }
  function armPoll(delay = 5000) { if (waiters.size && !pollTimer) pollTimer = setTimeout(pollWaiting, delay); }
  function pollSoon() { if (!waiters.size) return; clearTimeout(pollTimer); pollTimer = 0; armPoll(300); }
  window.addEventListener("focus", pollSoon);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollSoon(); });
  async function http(path, body) {
    const res = await fetch(path, { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body) });
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok && res.status !== 422) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { data, status: res.status });
    return Object.assign(data, { _status: res.status });
  }
  function chatTitle(chatId) {
    try { return String((S.chats || []).find((c) => c.id === chatId)?.title || ""); } catch (e) { return ""; }
  }
  function rerenderChat() { try { if (typeof renderMessages === "function") renderMessages(); } catch (e) {} }
  function persist(chatId, messages) { try { if (typeof persistChatSnapshot === "function") persistChatSnapshot(chatId, messages); } catch (e) {} }

  function entryOf(assistant, id) { return (assistant?.askForms || []).find((f) => f.id === id); }
  function mountChat(assistant, rec, ctx) {
    // Round 36: a chip / step / «busy» change repaints only this form (was: the whole chat via renderMessages);
    // the settled state is still drawn by finish() → rerenderChat().
    mount(rec, { compact: false, onSubmit: (answers, skipped) => answerChat(assistant, rec.id, answers, skipped, ctx) });
  }

  /* settle on the server, update the message; resolve the live waiter or resume the turn after a reload */
  async function answerChat(assistant, id, answers, skipped, ctx = {}) {
    const rec = entryOf(assistant, id);
    if (!rec || rec.status !== "pending") return;
    let data;
    try {
      data = skipped ? await http(`/api/pg/ask_user/${encodeURIComponent(id)}/skip`, {})
        : await http(`/api/pg/ask_user/${encodeURIComponent(id)}/answer`, { answers });
    } catch (e) {
      if (e.status !== 404) throw e;
      // The server forgot the form (data dir moved, a day passed): settle locally with the same contract.
      data = { record: { status: skipped ? "skipped" : "answered", answers: skipped ? null : answers },
        result: { tool: "ask_user", status: skipped ? "skipped" : "answered", answers: skipped ? undefined : answers,
          note: skipped ? "The user skipped the form. Choose sensible defaults, state them briefly and finish the task." : "Use these answers and continue the task now." } };
    }
    if (data._status === 422 && data.errors) return { errors: data.errors };
    finish(assistant, rec, data);
  }
  function finish(assistant, rec, data) {
    const record = data.record || {};
    rec.status = record.status || "answered";
    rec.answers = record.answers || null;
    rec.answeredAt = Date.now();
    drafts.delete(rec.id);
    update(rec);
    const waiter = waiters.get(rec.id);
    if (waiter) {
      waiters.delete(rec.id);
      waiter.resolve(data.result || { tool: "ask_user", status: rec.status, answers: rec.answers });
      rerenderChat();
      return;
    }
    // No live tool loop (page reloaded, app restarted): resume the same turn with the tool result.
    resumeTurn(assistant, rec, data.result || { tool: "ask_user", status: rec.status, answers: rec.answers });
  }
  function resumeTurn(assistant, rec, result) {
    try {
      const chat = (S.chats || []).find((c) => (c.messages || []).includes(assistant));
      const chatId = chat ? chat.id : S.currentId;
      const messages = chat ? chat.messages : S.messages;
      if (assistant.toolCalls && assistant.toolCalls.status !== "done") assistant.toolCalls.status = "done";
      assistant.awaitingUser = false;
      persist(chatId, messages);
      rerenderChat();
      const rest = (assistant._askRest || []).map((c) => ({ tool: c.tool, error: "Not run: the turn was interrupted while waiting for the form. Call it again if it is still needed." }));
      const report = [result, ...rest].map((r) => JSON.stringify(r)).join("\n");
      resumeAfterAutomation("tools", "Tool results:\n" + report, 0, { chatId, messages, assistant });
    } catch (e) { console.warn("ask_user resume failed", e); }
  }

  /* app.js::runHiddenTools → await MemuAskUser.ask(assistant, args, {signal, chatId, messages, mode}) */
  async function ask(assistant, args, ctx = {}) {
    const surface = ctx.mode === "work" || ctx.mode === "code" ? "work" : "chat";
    let rec;
    try {
      rec = await http("/api/pg/ask_user/open", { form: args, surface, lang: lang(), title: chatTitle(ctx.chatId),
        ref: { chat_id: String(ctx.chatId || ""), mode: String(ctx.mode || "chat") } });
    } catch (e) {
      return e.data?.result || { tool: "ask_user", error: String(e.message || e) };
    }
    const entry = { id: rec.id, form: rec.form, status: "pending", answers: null, createdAt: Date.now() };
    (assistant.askForms = assistant.askForms || []).push(entry);
    assistant.awaitingUser = true;
    const progress = assistant.progress;
    assistant.progress = "";
    mountChat(assistant, entry, ctx);
    persist(ctx.chatId, ctx.messages);
    rerenderChat();
    notifyWaiting(entry);
    focusFirst(entry.id);
    try {
      return await new Promise((resolve, reject) => {
        const waiter = { resolve, reject, assistant, entry };
        waiters.set(entry.id, waiter);
        const abort = () => {
          if (!waiters.has(entry.id)) return;
          waiters.delete(entry.id);
          entry.status = "cancelled"; drafts.delete(entry.id);
          http(`/api/pg/ask_user/${encodeURIComponent(entry.id)}/cancel`, {}).catch(() => {});
          reject(new DOMException("aborted", "AbortError"));
        };
        if (ctx.signal) { if (ctx.signal.aborted) return abort(); ctx.signal.addEventListener("abort", abort, { once: true }); }
        armPoll();
      });
    } finally {
      assistant.awaitingUser = false;
      assistant.progress = progress || "";
      persist(ctx.chatId, ctx.messages);
    }
  }
  function resultFrom(record) {
    const status = record.status;
    const note = status === "answered" ? "Use these answers and continue the task now. Do not ask the same again."
      : status === "skipped" ? "The user skipped the form («decide yourself»). Choose sensible defaults, state them in one short clause and finish the task. Do not ask again."
      : status === "timeout" ? "The user did not answer in time. Choose sensible defaults, state them briefly and finish the task."
      : "The form was cancelled. Stop and do not ask again.";
    const out = { tool: "ask_user", status, note };
    if (status === "answered") out.answers = record.answers || {};
    return out;
  }
  function focusFirst(id) {
    setTimeout(() => {
      const card = document.querySelector(`[data-au-form="${CSS.escape(id)}"]`);
      if (!card) return;
      const first = card.querySelector('.au-chip[tabindex="0"], .au-input, .au-chip');
      if (first && !document.activeElement?.closest?.("#composer, textarea#input, .composer")) first.focus({ preventScroll: true });
      card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 60);
  }
  function notifyWaiting(entry) {
    try { if (document.hidden && typeof toast === "function") toast(L("MemuAI ждёт вашего ответа в чате", "MemuAI is waiting for your answer in the chat"), "warn", 6000); } catch (e) {}
  }

  /* app.js::renderMessages → MemuAskUser.html(m, i) */
  function chatHtml(m) {
    if (!m?.askForms?.length) return "";
    return `<div class="au-stack">${m.askForms.map((rec) => {
      if (rec.status === "pending" && !mounted.has(rec.id)) mountChat(m, rec, {});   // restored after a reload
      else if (mounted.has(rec.id)) update(rec);
      return html(rec, {});
    }).join("")}</div>`;
  }
  /* the latest pending form of the open chat (composer text → answer) */
  function pendingInCurrent() {
    try {
      for (let i = (S.messages || []).length - 1; i >= 0; i -= 1) {
        const m = S.messages[i];
        if (m.role === "user" && !m.internal) return null;
        const rec = (m.askForms || []).find((f) => f.status === "pending");
        if (rec) return { m, rec };
      }
    } catch (e) {}
    return null;
  }
  /* app.js::send(): a message typed while a form waits answers it (no new bubble, the turn goes on) */
  function interceptSend(text) {
    const value = String(text || "").trim();
    const found = pendingInCurrent();
    if (!found || !value) return false;
    if (value.startsWith("/")) return false;          // slash commands keep working
    answerChat(found.m, found.rec.id, { _reply: value }, false).catch((e) => { try { toast(String(e.message || e), "err"); } catch (x) {} });
    return true;
  }
  function hasPending() { return !!pendingInCurrent(); }

  /* the prompt block (same text as app/ask_user.py::prompt_section) */
  const PROMPT = `<ask_user>
When a request is genuinely ambiguous and a wrong guess would change the result (which of several real
options, a date, a budget, a size), ask with a small form instead of writing a paragraph of questions:
\`\`\`memuai-tool
[{"tool":"ask_user","args":{"question":"Short title","fields":[
 {"id":"style","type":"choice","label":"Style","options":["Formal","Friendly"],"default":"Friendly","allow_other":true},
 {"id":"date","type":"date","label":"When"}]}}]
\`\`\`
Field types: choice (one chip), multi (checkboxes), text, date (YYYY-MM-DD), number (min, max, unit), bool.
Never end a reply with a list of questions for the user: questions you really need belong in the form.
Rules: ask ONLY what changes the result — never trivial things, never what the user already told you, never
what a sensible default covers (then just use the default and say so). At most 3 fields, short labels,
2–6 options with the likely one as default. Write nothing else in that reply: the form is the reply; the
answers arrive as the tool result and you continue the same answer from them. If the user skipped the form,
decide yourself, name the assumption in one short clause and finish the task. One form per turn is normal;
a second only if the answers opened a genuinely new question.
</ask_user>`;
  function promptBlock() { return PROMPT; }

  window.MemuAskUser = { ask, html: chatHtml, interceptSend, hasPending, promptBlock, resultFrom, _waiters: waiters };
})();
