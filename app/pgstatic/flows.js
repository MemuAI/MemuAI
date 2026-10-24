/* MemuAI — Round 35: «Планирование по-новому» — the step constructor inside a task (plan.js block «Сделать»).
   window.MemuFlows = { mount(host, draft, opts), fromTask(task), summaryHtml(steps), historyHtml(task, host),
                        mountAutomations(host), openTest(draft) }.
   Data model and engine: app/flow_engine.py; routes: app/flow_api.py (/api/plan/flow/*). */
(function () {
  "use strict";
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $q = (sel, root = document) => root.querySelector(sel);
  const $$q = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const call = (path, body) => api(path, { method: "POST", body: { ...(body || {}), lang: en() ? "en" : "ru" } });
  const uid = () => "s" + Math.random().toString(16).slice(2, 9);

  /* ------------------------------------------------------------------ icons (own set: the app set has no split/loop/db) */
  const SVG = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    sparkles: '<path d="M12 3.5l1.7 4.3 4.3 1.7-4.3 1.7L12 15.5l-1.7-4.3L6 9.5l4.3-1.7z"/><path d="M18.5 15l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    split: '<path d="M6 3v6a4 4 0 0 0 4 4h4a4 4 0 0 1 4 4v4"/><path d="M18 3v6a4 4 0 0 1-4 4"/><path d="m15 6 3-3 3 3"/><path d="m3 6 3-3 3 3"/>',
    decide: '<path d="M12 3a6 6 0 0 1 6 6c0 2.3-1.3 3.6-2.6 4.6-.8.6-1.4 1.3-1.4 2.4v.5h-4V16c0-1.1-.6-1.8-1.4-2.4C7.3 12.6 6 11.3 6 9a6 6 0 0 1 6-6z"/><path d="M10 20h4"/>',
    repeat: '<path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/><path d="M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/>',
    variable: '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-.8 2.5-2 3 1.2.5 2 1.5 2 3v2c0 2 1 3 3 3"/><path d="M16 4c2 0 3 1 3 3v2c0 1.5.8 2.5 2 3-1.2.5-2 1.5-2 3v2c0 2-1 3-3 3"/>',
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>',
    clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.3a2.5 2.5 0 0 1 4.8 1c0 1.7-2.4 2.2-2.4 3.7"/><path d="M12 17h.01"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    database: '<ellipse cx="12" cy="5.5" rx="7" ry="2.8"/><path d="M5 5.5v13c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8v-13"/><path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8"/>',
    globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17"/><path d="M12 3.5c2.4 2.4 3.5 5.2 3.5 8.5s-1.1 6.1-3.5 8.5c-2.4-2.4-3.5-5.2-3.5-8.5S9.6 5.9 12 3.5z"/>',
    send: '<path d="M21 3 3 10.5l7 2.5 2.5 7z"/><path d="m10 13 4-4"/>',
    bell: '<path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    play: '<path d="M8 5v14l11-7z"/>',
    zap: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
    tool: '<path d="M14.5 6.5a4 4 0 0 0-5.4 5.1L4 16.7 7.3 20l5.1-5.1a4 4 0 0 0 5.1-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
    code: '<path d="m8 8-4 4 4 4"/><path d="m16 8 4 4-4 4"/><path d="m13.5 5-3 14"/>',
    up: '<path d="m6 15 6-6 6 6"/>', down: '<path d="m6 9 6 6 6-6"/>', trash: '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/>',
    copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>',
    plus: '<path d="M12 5v14M5 12h14"/>', chevron: '<path d="m9 6 6 6-6 6"/>', braces: '<path d="M8 4c-2 0-3 1-3 3v2c0 1.5-.8 2.5-2 3 1.2.5 2 1.5 2 3v2c0 2 1 3 3 3"/><path d="M16 4c2 0 3 1 3 3v2c0 1.5.8 2.5 2 3-1.2.5-2 1.5-2 3v2c0 2-1 3-3 3"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>', flask: '<path d="M9 3h6"/><path d="M10 3v6l-5.5 9.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 15h9"/>',
    history: '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/>', inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5 5h14l2 8v6H3v-6z"/>',
    pause: '<path d="M8 5v14M16 5v14"/>', skip: '<path d="M5 5l9 7-9 7z"/><path d="M18 5v14"/>',
  };
  const ico = (name, cls = "") => `<svg class="flw-i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${SVG[name] || SVG.sparkles}</svg>`;

  /* ------------------------------------------------------------------ dictionaries */
  const TYPES = () => ({
    web_search: { cat: "data", icon: "search", title: T("Веб-поиск", "Web search"), hint: T("Найти в интернете — результат: список {title, url, snippet}", "Search the web — result: a list of {title, url, snippet}") },
    model: { cat: "ai", icon: "sparkles", title: T("Запрос к модели", "Ask a model"), hint: T("Модель выполняет задание; можно попросить ответ в JSON", "The model does the job; can answer in JSON") },
    decide: { cat: "ai", icon: "decide", title: T("Модель решений", "Decision model"), hint: T("Быстрый ответ «да/нет» или выбор варианта", "A quick yes/no or a choice of options") },
    if: { cat: "logic", icon: "split", title: T("Если… иначе", "If… else"), hint: T("Две ветки: «Да» и «Нет»", "Two branches: “Yes” and “No”") },
    foreach: { cat: "logic", icon: "repeat", title: T("Для каждого", "For each"), hint: T("Повторить шаги для каждого элемента списка", "Repeat steps for every item of a list") },
    set: { cat: "logic", icon: "variable", title: T("Переменная", "Variable"), hint: T("Запомнить или преобразовать значение", "Store or transform a value") },
    only_new: { cat: "logic", icon: "filter", title: T("Только новые", "Only new"), hint: T("Оставить элементы, которых не было в прошлых запусках", "Keep items not seen in earlier runs") },
    wait: { cat: "logic", icon: "clock", title: T("Подождать", "Wait"), hint: T("Пауза перед следующим шагом", "A pause before the next step") },
    ask_user: { cat: "logic", icon: "help", title: T("Спросить меня", "Ask me"), hint: T("Форма во «Входящих» и в Telegram; задача ждёт ответа", "A form in the Inbox and Telegram; the task waits for the answer") },
    stop: { cat: "logic", icon: "stop", title: T("Завершить", "Stop"), hint: T("Закончить задачу на этом шаге", "End the task at this step") },
    memubase_insert: { cat: "data", icon: "database", title: T("MemuBase: добавить", "MemuBase: add rows"), hint: T("Добавить строки в таблицу", "Add rows to a table") },
    memubase_query: { cat: "data", icon: "database", title: T("MemuBase: найти", "MemuBase: query"), hint: T("Прочитать строки из таблицы", "Read rows from a table") },
    http: { cat: "data", icon: "globe", title: T("HTTP-запрос", "HTTP request"), hint: T("Запрос к публичному адресу (API, вебхук)", "A request to a public address (API, webhook)") },
    telegram: { cat: "send", icon: "send", title: T("Отправить в Telegram", "Send to Telegram"), hint: T("Сообщение в привязанный чат бота", "A message to the paired bot chat") },
    notify: { cat: "send", icon: "bell", title: T("Уведомление", "Notification"), hint: T("Уведомление Windows и запись во «Входящих»", "A Windows notification and an Inbox item") },
    run_task: { cat: "app", icon: "play", title: T("Запустить задачу", "Run a task"), hint: T("Выполнить другую задачу и взять её результат", "Run another task and take its result") },
    qa_command: { cat: "app", icon: "zap", title: T("Сценарий быстрого доступа", "Quick Access scenario"), hint: T("Ваша команда из Быстрого доступа", "Your Quick Access command") },
    qa_macro: { cat: "app", icon: "grid", title: T("Кнопка-макрос", "Macro button"), hint: T("Быстрое действие с панели", "A quick action button") },
    tool: { cat: "app", icon: "tool", title: T("Действие ассистента", "Assistant action"), hint: T("Громкость, программы, окна, медиа…", "Volume, apps, windows, media…") },
    dfy: { cat: "app", icon: "globe", title: T("Сделай за меня", "Do For You"), hint: T("Агент сделает дело в браузере (нужно окно MemuAI)", "The agent does it in a browser (needs the MemuAI window)") },
    memucode: { cat: "app", icon: "code", title: "MemuCode", hint: T("Агент для кода в папке проекта", "The coding agent in a project folder") },
    mcp_call: { cat: "app", icon: "tool", title: T("Инструмент MCP", "MCP tool"), hint: T("Вызвать инструмент подключённого сервера MCP (база, трекер, заметки…)", "Call a tool of a connected MCP server (database, tracker, notes…)") },
    integration: { cat: "app", icon: "tool", title: "Telegram Use / Chrome", hint: T("Прочитать сообщения или страницу, отправить ответ, управлять вкладками; результат доступен следующим шагам", "Read messages or a page, send a reply, manage tabs; pass the result to the next steps") },
  });
  const CATS = () => [["data", T("Данные", "Data")], ["ai", T("ИИ", "AI")], ["logic", T("Логика", "Logic")], ["send", T("Отправить", "Send")], ["app", T("Приложения", "Apps")]];
  const OPS = () => [["is_true", T("истинно / да", "is true / yes")], ["is_false", T("ложно / нет", "is false / no")], ["not_empty", T("не пусто", "is not empty")], ["empty", T("пусто", "is empty")],
    ["eq", "="], ["ne", "≠"], ["gt", ">"], ["ge", "≥"], ["lt", "<"], ["le", "≤"], ["contains", T("содержит", "contains")], ["not_contains", T("не содержит", "does not contain")], ["matches", T("совпадает с шаблоном", "matches a pattern")]];
  const NO_RIGHT = new Set(["is_true", "is_false", "empty", "not_empty"]);

  const fs = { draft: null, host: null, opts: {}, cat: null, problems: [], numbers: {}, open: new Set(), vtimer: 0, test: null, ac: null };

  /* ------------------------------------------------------------------ tree helpers */
  function* walk(steps) { for (const s of steps || []) { yield s; for (const k of ["then", "else", "body"]) if (Array.isArray(s[k])) yield* walk(s[k]); } }
  function numbering(steps, prefix = "") {
    const out = {};
    (steps || []).forEach((s, i) => {
      const n = `${prefix}${i + 1}`; out[s.id] = n;
      const inner = [...(s.then || []), ...(s.else || []), ...(s.body || [])];
      if (inner.length) Object.assign(out, numbering(inner, n + "."));
    });
    return out;
  }
  function findList(id, steps = fs.draft.steps) {
    for (let i = 0; i < steps.length; i += 1) {
      if (steps[i].id === id) return { list: steps, index: i };
      for (const k of ["then", "else", "body"]) if (Array.isArray(steps[i][k])) { const r = findList(id, steps[i][k]); if (r) return r; }
    }
    return null;
  }
  const stepById = (id) => { for (const s of walk(fs.draft.steps)) if (s.id === id) return s; return null; };
  function isInside(ancestorId, childId) { const a = stepById(ancestorId); if (!a) return false; for (const s of walk([...(a.then || []), ...(a.else || []), ...(a.body || [])])) if (s.id === childId) return true; return false; }
  // after a move the numbers change: {{step3.…}} must keep pointing at the same step
  function rewriteRefs(before, after) {
    const map = {};
    for (const [id, n] of Object.entries(before)) if (after[id] && after[id] !== n) map[n] = after[id];
    if (!Object.keys(map).length) return;
    const re = /\{\{\s*(step|шаг)\s?(\d+(?:\.\d+)*)/gi;
    const fix = (v) => (typeof v === "string" ? v.replace(re, (m, w, n) => (map[n] ? m.replace(n, map[n]) : m)) : Array.isArray(v) ? v.map(fix) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, fix(x)])) : v);
    for (const s of walk(fs.draft.steps)) for (const k of Object.keys(s)) if (!["then", "else", "body", "id", "type"].includes(k)) s[k] = fix(s[k]);
  }
  function defaults(type) {
    const base = { id: uid(), type };
    const D = {
      web_search: { query: "", limit: 8 }, model: { prompt: "", model: "", json: false }, decide: { question: "", context: "", options: [] },
      if: { cond: { mode: "compare", left: "", op: "not_empty", right: "", question: "", context: "" }, then: [], else: [] },
      foreach: { items: "", as: "item", limit: 50, body: [] }, set: { var: "", value: "", transform: "none" }, only_new: { items: "", key: "url", remember: true },
      wait: { seconds: 60 }, ask_user: { question: "", fields: [{ id: "answer", type: "choice", label: "", options: [T("Да", "Yes"), T("Нет", "No")] }], timeout_hours: 12, on_timeout: "default" },
      stop: { message: "", status: "ok" }, memubase_insert: { table: "", rows: "", unique: "" }, memubase_query: { table: "", search: "", where: {}, limit: 50 },
      http: { method: "GET", url: "", headers: {}, body: "" }, telegram: { text: "" }, notify: { title: "", text: "", windows: true, inbox: true },
      run_task: { task_id: "" }, qa_command: { command: "" }, qa_macro: { action_id: "" }, tool: { action: "", params: {} }, dfy: { goal: "", dfy_browser: "" },
      memucode: { project_path: "", prompt: "", permission_mode: "plan", model: "" },
      mcp_call: { server: "", tool: "", args: {}, approved: false },
      integration: { tool: "", args: {} },
    };
    // a new step that follows a list-producing step points at it at once
    return { ...base, ...JSON.parse(JSON.stringify(D[type] || {})) };
  }
  function lastListRef(beforeId) {
    const order = Object.keys(fs.numbers);
    const stop = beforeId ? order.indexOf(beforeId) : order.length;
    for (let i = (stop < 0 ? order.length : stop) - 1; i >= 0; i -= 1) {
      const s = stepById(order[i]);
      if (s && ["web_search", "model", "only_new", "memubase_query"].includes(s.type)) return `{{step${fs.numbers[s.id]}.result}}`;
    }
    return "";
  }

  /* ------------------------------------------------------------------ variables (the picker) */
  function variablesFor(beforeId) {
    const nums = numbering(fs.draft.steps);
    const order = Object.keys(nums);
    const stop = order.indexOf(beforeId);
    const out = [];
    for (const id of order.slice(0, stop < 0 ? order.length : stop)) {
      const s = stepById(id); if (!s || ["stop", "wait"].includes(s.type)) continue;
      const n = nums[id], title = s.label || TYPES()[s.type]?.title || s.type;
      if (s.type === "foreach") {
        if (isInside(id, beforeId)) {
          out.push({ expr: `{{${s.as || "item"}}}`, label: T("текущий элемент цикла", "current loop item"), group: n });
          out.push({ expr: "{{loop.index}}", label: T("номер элемента", "item number"), group: n });
        }
        continue;
      }
      if (s.type === "if" && isInside(id, beforeId)) continue;
      out.push({ expr: `{{step${n}.result}}`, label: `${n}. ${title}`, group: n });
      if (["web_search", "model", "only_new", "memubase_query"].includes(s.type)) {
        out.push({ expr: `{{step${n}.result | count}}`, label: T("сколько элементов", "how many items"), group: n });
        out.push({ expr: `{{step${n}.result | list}}`, label: T("списком", "as a list"), group: n });
      }
      if (s.type === "ask_user") (s.fields || []).forEach((f) => out.push({ expr: `{{step${n}.answers.${f.id}}}`, label: f.label || f.id, group: n }));
      if (s.name) out.push({ expr: `{{${s.name}}}`, label: T("по имени", "by name"), group: n });
    }
    out.push({ expr: "{{trigger.payload}}", label: T("данные триггера", "trigger data"), group: "trigger" });
    out.push({ expr: "{{trigger.type}}", label: T("что запустило задачу", "what started the task"), group: "trigger" });
    out.push({ expr: "{{now.datetime}}", label: T("дата и время", "date and time"), group: "now" });
    return out;
  }
  function insertAt(el, text) {
    const start = el.selectionStart ?? el.value.length, end = el.selectionEnd ?? el.value.length;
    let s = start;
    const before = el.value.slice(0, start);
    const open = before.lastIndexOf("{{");
    if (open >= 0 && before.indexOf("}}", open) < 0) s = open;       // replace the half-typed «{{ste»
    el.value = el.value.slice(0, s) + text + el.value.slice(end);
    el.selectionStart = el.selectionEnd = s + text.length;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.focus();
  }
  function closeAc() { fs.ac?.remove(); fs.ac = null; }
  function openAc(el, query = "") {
    closeAc();
    const sid = el.closest("[data-sid]")?.dataset.sid;
    const q = query.toLowerCase().replace(/^\{\{\s*/, "");
    const vars = variablesFor(sid).filter((v) => !q || v.expr.toLowerCase().includes(q) || v.label.toLowerCase().includes(q));
    if (!vars.length) return;
    const box = document.createElement("div");
    box.className = "flw-ac"; box.setAttribute("role", "listbox");
    box.innerHTML = `<div class="flw-ac-head">${ico("braces")}${T("Переменные — результат предыдущих шагов", "Variables — results of earlier steps")}</div>` +
      vars.slice(0, 40).map((v, i) => `<button type="button" role="option" class="${i === 0 ? "on" : ""}" data-expr="${H(v.expr)}"><code>${H(v.expr)}</code><span>${H(v.label)}</span></button>`).join("");
    document.body.appendChild(box);
    const r = el.getBoundingClientRect();
    const top = Math.min(r.bottom + 4, window.innerHeight - Math.min(320, box.offsetHeight) - 8);
    box.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - box.offsetWidth - 8))}px`;
    box.style.top = `${Math.max(8, top)}px`;
    box.addEventListener("mousedown", (e) => { const b = e.target.closest("[data-expr]"); if (!b) return; e.preventDefault(); insertAt(el, b.dataset.expr); closeAc(); });
    fs.ac = box; fs.ac.target = el;
  }
  function acKey(e) {
    if (!fs.ac || fs.ac.target !== e.target) return false;
    const items = $$q("[data-expr]", fs.ac); const cur = items.findIndex((b) => b.classList.contains("on"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); const next = (cur + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items.forEach((b, i) => b.classList.toggle("on", i === next)); items[next].scrollIntoView({ block: "nearest" }); return true;
    }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertAt(e.target, items[Math.max(0, cur)].dataset.expr); closeAc(); return true; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeAc(); return true; }
    return false;
  }

  /* ------------------------------------------------------------------ fields */
  const tplInput = (sid, key, value, ph = "") => `<div class="flw-tpl"><input class="pinput compact" data-f="${sid}|${key}" data-tpl="1" value="${H(value ?? "")}" placeholder="${H(ph)}" autocomplete="off"><button type="button" class="flw-var" data-fa="vars" title="${T("Вставить переменную", "Insert a variable")}" aria-label="${T("Вставить переменную", "Insert a variable")}">${ico("braces")}</button></div>`;
  const tplArea = (sid, key, value, ph = "", rows = 3) => `<div class="flw-tpl area"><textarea class="ptexta" rows="${rows}" data-f="${sid}|${key}" data-tpl="1" placeholder="${H(ph)}">${H(value ?? "")}</textarea><button type="button" class="flw-var" data-fa="vars" title="${T("Вставить переменную", "Insert a variable")}" aria-label="${T("Вставить переменную", "Insert a variable")}">${ico("braces")}</button></div>`;
  const textIn = (sid, key, value, ph = "", cls = "") => `<input class="pinput compact ${cls}" data-f="${sid}|${key}" value="${H(value ?? "")}" placeholder="${H(ph)}" autocomplete="off">`;
  const numIn = (sid, key, value, min, max) => `<input class="pinput compact num" type="number" data-f="${sid}|${key}" data-k="num" min="${min}" max="${max}" value="${H(value ?? "")}">`;
  const check = (sid, key, value, label) => `<label class="pln-check"><input type="checkbox" data-f="${sid}|${key}" data-k="bool" ${value ? "checked" : ""}><span>${H(label)}</span></label>`;
  const select = (sid, key, value, options, cls = "") => `<select class="pinput compact ${cls}" data-f="${sid}|${key}">${options.map(([v, l]) => `<option value="${H(v)}" ${String(v) === String(value ?? "") ? "selected" : ""}>${H(l)}</option>`).join("")}</select>`;
  const jsonArea = (sid, key, value, ph = "") => `<textarea class="ptexta mono" rows="3" data-f="${sid}|${key}" data-k="json" placeholder="${H(ph)}">${H(value && (typeof value === "object" ? (Object.keys(value).length ? JSON.stringify(value, null, 2) : "") : value) || "")}</textarea>`;
  const row = (label, inner, hint = "") => `<div class="flw-field"><label>${H(label)}</label><div class="flw-field-in">${inner}</div>${hint ? `<small>${H(hint)}</small>` : ""}</div>`;
  function modelOptions(value, empty) {
    const models = (typeof visibleModels === "function" ? visibleModels() : (typeof S !== "undefined" ? S.models || [] : [])).filter((m) => !(typeof isImageModel === "function" && isImageModel(m.id)));
    const opts = [["", empty], ...models.map((m) => [m.id, typeof modelDisplayName === "function" ? modelDisplayName(m.id) : m.id])];
    if (value && !opts.some(([v]) => v === value)) opts.push([value, value]);
    return opts;
  }

  function formHtml(s) {
    const id = s.id, c = fs.cat || {};
    switch (s.type) {
      case "web_search": return row(T("Что искать", "Search for"), tplInput(id, "query", s.query, T("вакансии Python junior удалённо", "remote Python junior jobs"))) + row(T("Результатов", "Results"), numIn(id, "limit", s.limit, 1, 20));
      case "model": return row(T("Задание для модели", "Job for the model"), tplArea(id, "prompt", s.prompt, T("Отбери подходящие вакансии из {{step1.result}}…", "Pick the matching jobs from {{step1.result}}…"), 4)) +
        row(T("Модель", "Model"), select(id, "model", s.model, modelOptions(s.model, T("— модель задачи / по умолчанию —", "— the task's / default model —")), "wide")) +
        `<div class="flw-inline">${check(id, "json", s.json, T("Ответ в JSON (проверяется и исправляется)", "Answer as JSON (validated and repaired)"))}</div>` +
        (s.json ? row(T("JSON-схема (необязательно)", "JSON schema (optional)"), jsonArea(id, "schema", s.schema, '{"type": "array", "items": {"type": "object", "required": ["title", "url"]}}')) : "");
      case "decide": return row(T("Вопрос", "Question"), tplInput(id, "question", s.question, T("Есть новые вакансии?", "Are there new jobs?"))) +
        row(T("Данные для решения", "Data to judge"), tplArea(id, "context", s.context, "{{step2.result}}", 2)) +
        row(T("Варианты ответа", "Answer options"), textIn(id, "options", (s.options || []).join(", "), T("пусто — да / нет", "empty — yes / no"), "wide").replace("data-f=", 'data-k="list" data-f='), T("Через запятую. Без вариантов модель отвечает «да» или «нет».", "Comma-separated. Without options the model answers yes or no.")) +
        row(T("Модель решений", "Decision model"), select(id, "model", s.model, modelOptions(s.model, c.decision_model ? T("из настроек: ", "from Settings: ") + c.decision_model : T("— из настроек / основная —", "— from Settings / main —")), "wide"));
      case "if": {
        const cnd = s.cond || {};
        const modes = `<div class="flw-seg" role="group"><button type="button" class="${cnd.mode !== "decide" ? "on" : ""}" data-fa="cond-mode" data-v="compare">${T("Сравнить значение", "Compare a value")}</button><button type="button" class="${cnd.mode === "decide" ? "on" : ""}" data-fa="cond-mode" data-v="decide">${ico("decide")}${T("Спросить модель решений", "Ask the decision model")}</button></div>`;
        if (cnd.mode === "decide") return modes + row(T("Вопрос", "Question"), tplInput(id, "cond.question", cnd.question, T("Есть новые?", "Anything new?"))) + row(T("Данные", "Data"), tplArea(id, "cond.context", cnd.context, "{{step3.result}}", 2));
        return modes + `<div class="flw-cond">${tplInput(id, "cond.left", cnd.left, "{{step3.result}}")}${select(id, "cond.op", cnd.op, OPS())}${NO_RIGHT.has(cnd.op) ? "" : tplInput(id, "cond.right", cnd.right, T("значение", "value"))}</div>`;
      }
      case "foreach": return row(T("Список", "List"), tplInput(id, "items", s.items, "{{step2.result}}")) + `<div class="flw-inline">${row(T("Имя элемента", "Item name"), textIn(id, "as", s.as, "item", "short"))}${row(T("Не больше", "At most"), numIn(id, "limit", s.limit, 1, 200))}</div>`;
      case "set": return row(T("Имя переменной", "Variable name"), textIn(id, "var", s.var, T("например, вакансии", "e.g. jobs"), "short"), T("Потом: {{vars.имя}}", "Later: {{vars.name}}")) + row(T("Значение", "Value"), tplArea(id, "value", s.value, "{{step1.result}}", 2)) +
        row(T("Преобразовать", "Transform"), select(id, "transform", s.transform, [["none", T("как есть", "as is")], ["json", T("разобрать JSON", "parse JSON")], ["lines", T("в строки", "into lines")], ["count", T("количество", "count")], ["list", T("список с точками", "bullet list")], ["join", T("через запятую", "comma-joined")], ["first", T("первый", "first")], ["last", T("последний", "last")], ["unique", T("без повторов", "unique")], ["number", T("число", "number")]]));
      case "only_new": return row(T("Список", "List"), tplInput(id, "items", s.items, "{{step2.result}}")) + row(T("Поле-ключ", "Key field"), textIn(id, "key", s.key, "url", "short"), T("По нему узнаём, что элемент уже был. Пусто — весь элемент.", "Identifies an item seen before. Empty — the whole item.")) + check(id, "remember", s.remember !== false, T("Запоминать увиденное (после успешного запуска)", "Remember what was seen (after a successful run)"));
      case "wait": return row(T("Сколько ждать", "How long"), `<div class="flw-inline">${numIn(id, "seconds", s.seconds, 1, 604800)}<span class="flw-unit">${T("секунд", "seconds")}</span><span class="flw-chips">${[[60, T("1 мин", "1 min")], [600, T("10 мин", "10 min")], [3600, T("1 ч", "1 h")]].map(([v, l]) => `<button type="button" class="pln-ex" data-fa="set" data-key="seconds" data-v="${v}">${l}</button>`).join("")}</span></div>`, T("Дольше минуты — задача паркуется и продолжится сама, даже после перезапуска.", "Over a minute the run is parked and resumes by itself, even after a restart."));
      case "ask_user": return row(T("Вопрос", "Question"), tplInput(id, "question", s.question, T("Отправить подборку?", "Send the digest?"))) + askFields(s) +
        `<div class="flw-inline">${row(T("Ждать ответа", "Wait for an answer"), `${numIn(id, "timeout_hours", s.timeout_hours, 1, 24)}<span class="flw-unit">${T("ч", "h")}</span>`)}${row(T("Нет ответа", "No answer"), select(id, "on_timeout", s.on_timeout, [["default", T("взять вариант по умолчанию", "use the default")], ["stop", T("остановить задачу", "stop the task")], ["fail", T("считать ошибкой", "treat as an error")]]))}</div>` +
        `<p class="flw-note">${ico("inbox")}${T("Форма появится во «Входящих» и в Telegram, задача встанет на паузу и продолжится сразу после ответа.", "The form appears in the Inbox and in Telegram; the task pauses and continues right after your answer.")}</p>`;
      case "stop": return row(T("Сообщение", "Message"), tplInput(id, "message", s.message, T("Новых вакансий нет", "No new jobs"))) + row(T("Итог", "Outcome"), select(id, "status", s.status, [["ok", T("успешно", "success")], ["fail", T("ошибка", "failure")]]));
      case "memubase_insert": return row(T("Таблица", "Table"), tableIn(id, s.table)) + row(T("Строки", "Rows"), tplInput(id, "rows", s.rows, "{{step3.result}}"), T("Список объектов; поля станут колонками. В цикле пусто — текущий элемент.", "A list of objects; fields become columns. Empty inside a loop — the current item.")) + row(T("Без повторов по полю", "Skip duplicates by field"), textIn(id, "unique", s.unique, "url", "short"));
      case "memubase_query": return row(T("Таблица", "Table"), tableIn(id, s.table)) + row(T("Поиск", "Search"), tplInput(id, "search", s.search, T("текст в любой колонке", "text in any column"))) + row(T("Не больше строк", "At most rows"), numIn(id, "limit", s.limit, 1, 1000));
      case "http": return `<div class="flw-cond">${select(id, "method", s.method, ["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => [m, m]))}${tplInput(id, "url", s.url, "https://api.example.com/items")}</div>` +
        row(T("Заголовки (JSON)", "Headers (JSON)"), jsonArea(id, "headers", s.headers, '{"Accept": "application/json"}')) + (s.method !== "GET" ? row(T("Тело запроса", "Body"), tplArea(id, "body", s.body, '{"text": "{{step2.result}}"}', 2)) : "") +
        `<p class="flw-note">${ico("globe")}${T("Только публичные адреса: локальные и внутренние запрещены. В тесте выполняются только GET.", "Public addresses only: local and internal ones are refused. The test runs GET only.")}</p>`;
      case "telegram": return row(T("Сообщение", "Message"), tplArea(id, "text", s.text, T("Новые вакансии:\n{{step3.result | list}}", "New jobs:\n{{step3.result | list}}"), 3)) + (c.telegram === false ? `<p class="flw-note warn">${ico("send")}${T("Бот Telegram не привязан — Настройки → Telegram.", "The Telegram bot is not paired — Settings → Telegram.")}</p>` : "");
      case "notify": return row(T("Заголовок", "Title"), tplInput(id, "title", s.title, T("Название задачи", "Task name"))) + row(T("Текст", "Text"), tplArea(id, "text", s.text, "", 2)) + `<div class="flw-inline">${check(id, "windows", s.windows !== false, T("Уведомление Windows", "Windows notification"))}${check(id, "inbox", s.inbox !== false, T("Во «Входящие»", "To the Inbox"))}</div>`;
      case "run_task": return row(T("Задача", "Task"), select(id, "task_id", s.task_id, [["", T("— выберите —", "— choose —")], ...(c.tasks || []).filter((t) => t.id !== fs.draft.id).map((t) => [t.id, t.name || t.id])], "wide"));
      case "qa_command": return row(T("Сценарий", "Scenario"), select(id, "command", s.command, [["", T("— выберите —", "— choose —")], ...(c.scenarios || []).map((x) => [x.name, x.name])], "wide"), (c.scenarios || []).length ? "" : T("Своих сценариев пока нет: Настройки → Быстрый доступ → Команды.", "No scenarios yet: Settings → Quick Access → Commands."));
      case "qa_macro": return row(T("Кнопка", "Button"), select(id, "action_id", s.action_id, [["", T("— выберите —", "— choose —")], ...(c.macros || []).map((x) => [x.id, x.name || x.id])], "wide"), (c.macros || []).length ? T("Кнопки, которым нужно окно Быстрого доступа, по расписанию не запускаются.", "Buttons that need the Quick Access window do not run on schedule.") : T("Кнопок пока нет: Быстрый доступ → Панели и быстрые действия.", "No buttons yet: Quick Access → Panels and quick actions."));
      case "tool": return row(T("Действие", "Action"), select(id, "action", s.action, [["", T("— выберите —", "— choose —")], ...(c.actions || []).map((a) => [a.id, a.title + (a.tier === "dangerous" ? " ⚠" : "")])], "wide")) + row(T("Параметры (JSON)", "Parameters (JSON)"), jsonArea(id, "params", s.params, '{"level": 30}'));
      case "dfy": return row(T("Что сделать в браузере", "What to do in the browser"), tplArea(id, "goal", s.goal, T("Открой сайт и проверь статус заказа", "Open the site and check the order status"), 3)) + row(T("Браузер", "Browser"), select(id, "dfy_browser", s.dfy_browser || "", [["", T("Из настроек DFY", "DFY settings default")], ["isolated", T("Отдельный браузер DFY", "Separate DFY browser")], ["chrome", T("Мой Chrome", "My Chrome")]], "wide")) + `<p class="flw-note">${ico("globe")}${T("Выбор браузера не требует ответа при запуске. Chrome требует разрешения DFY и планирования в интеграции.", "The browser choice never asks at run time. Chrome requires DFY and scheduled access in the integration.")}</p>`;
      case "mcp_call": {   // r40-mcp
        const servers = c.mcp || [];
        const srv = servers.find((x) => x.name === s.server);
        const tools = (srv ? srv.tools : servers.flatMap((x) => x.tools)).map((t) => t.name);
        const meta = srv && srv.tools.find((t) => t.name === s.tool);
        return row(T("Сервер", "Server"), select(id, "server", s.server, [["", T("— выберите —", "— choose —")], ...servers.map((x) => [x.name, x.name])], "wide"),
          servers.length ? "" : T("Серверов пока нет: Настройки → Интеграции → MCP.", "No servers yet: Settings → Integrations → MCP."))
          + row(T("Инструмент", "Tool"), `<input class="pinput compact wide" data-f="${id}|tool" value="${H(s.tool || "")}" list="flw-mcp-${H(id)}" placeholder="query" autocomplete="off"><datalist id="flw-mcp-${H(id)}">${tools.map((t) => `<option value="${H(t)}">`).join("")}</datalist>`,
            meta ? (meta.description || "") + (meta.read_only ? " · " + T("только чтение", "read-only") : "") : T("Список инструментов появится после «Проверить соединение» в настройках MCP.", "The tool list appears after «Check the connection» in MCP settings."))
          + row(T("Аргументы (JSON)", "Arguments (JSON)"), jsonArea(id, "args", s.args, '{"sql": "SELECT count(*) FROM orders WHERE day = \'{{vars.day}}\'"}'), T("Можно подставлять переменные {{…}}.", "Variables {{…}} work inside."))
          + check(id, "approved", s.approved, T("Разрешить без подтверждения (инструмент меняет данные)", "Allow without asking (the tool changes data)"))
          + `<p class="flw-note">${ico("tool")}${T("Инструменты только для чтения выполняются и в тестовом запуске; остальные — только при обычном запуске и с галочкой выше.", "Read-only tools run in a test run too; the others only in a real run and with the box above ticked.")}</p>`;
      }
      case "integration": {
        const tools = c.integrations || [];
        const current = tools.find((tool) => tool.name === s.tool);
        const choices = [["", T("— выберите инструмент —", "— choose a tool —")], ...tools.map((tool) => [tool.name,
          tool.name + (tool.connected ? "" : T(" (не подключено)", " (disconnected)"))])];
        if (s.tool && !current) choices.push([s.tool, s.tool]);
        return row(T("Инструмент", "Tool"), select(id, "tool", s.tool, choices, "wide"), current?.description || "")
          + row(T("Аргументы (JSON)", "Arguments (JSON)"), jsonArea(id, "args", s.args, '{"chat": "{{vars.chat}}", "text": "{{step2.result}}"'),
            T("Результат прошлого шага: {{stepN.result}}. Списки и объекты сохраняют свой тип.", "Previous step result: {{stepN.result}}. Lists and objects keep their types."))
          + `<p class="flw-note">${ico("tool")}${T("Интеграция должна быть подключена, чтение разрешено. Изменение данных по расписанию разрешается отдельно. Подтверждений во время запуска нет; в тесте изменение данных только показывается.", "Connect the integration and enable reading. Scheduled writes have a separate permission. Runs do not ask for approval; test runs only preview changes.")}</p>`;
      }
      case "memucode": return row(T("Папка проекта", "Project folder"), textIn(id, "project_path", s.project_path, "C:\\Projects\\app", "wide")) + row(T("Задание", "Task"), tplArea(id, "prompt", s.prompt, "", 3)) + row(T("Права", "Permissions"), select(id, "permission_mode", s.permission_mode, [["plan", T("только чтение и план", "read-only, plan")], ["default", T("ничего не менять без подтверждения", "change nothing that needs approval")], ["acceptEdits", T("можно править файлы", "may edit files")]], "wide"));
      default: return "";
    }
  }
  function tableIn(id, value) {
    const tables = (fs.cat?.tables || []).map((t) => t.name);
    return `${textIn(id, "table", value, T("Вакансии", "Jobs"), "short").replace("<input ", `<input list="flw-tables" `)}<datalist id="flw-tables">${tables.map((t) => `<option value="${H(t)}">`).join("")}</datalist>`;
  }
  function askFields(s) {
    const fields = s.fields || [];
    const types = [["choice", T("один вариант", "one option")], ["multi", T("несколько", "several")], ["bool", T("да / нет", "yes / no")], ["text", T("текст", "text")], ["number", T("число", "number")], ["date", T("дата", "date")]];
    return `<div class="flw-askf">${fields.map((f, i) => `<div class="flw-askf-row" data-fi="${i}">
        <input class="pinput compact" data-af="${i}|label" value="${H(f.label || "")}" placeholder="${T("Подпись поля", "Field label")}">
        <select class="pinput compact" data-af="${i}|type">${types.map(([v, l]) => `<option value="${v}" ${f.type === v ? "selected" : ""}>${l}</option>`).join("")}</select>
        ${["choice", "multi"].includes(f.type) ? `<input class="pinput compact wide" data-af="${i}|options" value="${H((f.options || []).join(", "))}" placeholder="${T("Варианты через запятую", "Options, comma-separated")}">` : ""}
        <button type="button" class="flw-mini" data-fa="askf-del" data-i="${i}" aria-label="${T("Убрать поле", "Remove the field")}">${ico("x")}</button></div>`).join("")}
      ${fields.length < 3 ? `<button type="button" class="pln-link" data-fa="askf-add">${ico("plus")}${T("Поле", "Field")}</button>` : ""}</div>`;
  }

  /* ------------------------------------------------------------------ step cards */
  function shortOf(s) {
    const v = (x) => String(x || "").replace(/\s+/g, " ").trim();
    switch (s.type) {
      case "web_search": return v(s.query); case "model": return (s.json ? "JSON · " : "") + v(s.prompt); case "decide": return v(s.question);
      case "if": return s.cond?.mode === "decide" ? "🤖 " + v(s.cond.question) : `${v(s.cond?.left)} ${(OPS().find((o) => o[0] === s.cond?.op) || ["", ""])[1]} ${NO_RIGHT.has(s.cond?.op) ? "" : v(s.cond?.right)}`;
      case "foreach": return v(s.items); case "set": return s.var ? `${s.var} = ${v(s.value)}` : v(s.value); case "only_new": return v(s.items) + (s.key ? ` · ${s.key}` : "");
      case "wait": return s.seconds >= 3600 ? `${Math.round(s.seconds / 360) / 10} ${T("ч", "h")}` : s.seconds >= 60 ? `${Math.round(s.seconds / 6) / 10} ${T("мин", "min")}` : `${s.seconds} ${T("с", "s")}`;
      case "ask_user": return v(s.question); case "stop": return v(s.message); case "memubase_insert": case "memubase_query": return v(s.table);
      case "http": return `${s.method} ${v(s.url)}`; case "telegram": return v(s.text); case "notify": return v(s.title || s.text);
      case "run_task": return ((fs.cat?.tasks || []).find((t) => t.id === s.task_id) || {}).name || v(s.task_id); case "qa_command": return v(s.command);
      case "qa_macro": return ((fs.cat?.macros || []).find((m) => m.id === s.action_id) || {}).name || v(s.action_id); case "tool": return v(s.action);
      case "dfy": return v(s.goal); case "memucode": return v(s.prompt); case "mcp_call": return v((s.server || "") + (s.tool ? "." + s.tool : "")); case "integration": return v(s.tool); default: return "";
    }
  }
  function stepHtml(s, list, index) {
    const meta = TYPES()[s.type] || { icon: "sparkles", title: s.type, cat: "app" };
    const n = fs.numbers[s.id] || "";
    const open = fs.open.has(s.id);
    const probs = fs.problems.filter((p) => p.step === s.id);
    const level = probs.some((p) => p.level === "error") ? "err" : probs.length ? "warn" : "";
    const container = s.type === "if" || s.type === "foreach";
    const adv = `<details class="flw-adv"><summary>${T("Надёжность и имя", "Reliability and name")}</summary><div class="flw-inline">
        ${row(T("Подпись шага", "Step label"), textIn(s.id, "label", s.label, meta.title, "short"))}
        ${container ? "" : row(T("Имя результата", "Result name"), textIn(s.id, "name", s.name, T("например, вакансии", "e.g. jobs"), "short"))}
        ${container && s.type === "foreach" ? "" : container ? "" : row(T("Повторов при ошибке", "Retries on error"), numIn(s.id, "retries", s.retries || 0, 0, 5))}
        ${container || !s.timeout ? "" : row(T("Предел времени, с", "Time limit, s"), numIn(s.id, "timeout", s.timeout, 5, 21600))}
      </div>${container ? "" : check(s.id, "continue_on_error", s.continue_on_error, T("При ошибке продолжать задачу", "Continue the task on error"))}</details>`;
    const branches = s.type === "if"
      ? `<div class="flw-branches"><div class="flw-branch yes"><div class="flw-branch-head">${ico("check")}${T("Да", "Yes")}</div>${listHtml(s.then, s.id + ":then")}</div>
          <div class="flw-branch no"><div class="flw-branch-head">${ico("x")}${T("Нет", "No")}</div>${listHtml(s.else, s.id + ":else")}</div></div>`
      : s.type === "foreach" ? `<div class="flw-branches loop"><div class="flw-branch body"><div class="flw-branch-head">${ico("repeat")}${T("Для каждого элемента", "For every item")} <code>{{${H(s.as || "item")}}}</code></div>${listHtml(s.body, s.id + ":body")}</div></div>` : "";
    return `<li class="flw-step c-${meta.cat} ${open ? "open" : ""} ${level} ${s.disabled ? "off" : ""} ${container ? "container" : ""}" data-sid="${H(s.id)}">
      <div class="flw-card">
        <div class="flw-step-head" data-fa="toggle" role="button" tabindex="0" aria-expanded="${open}">
          <span class="flw-num">${H(n)}</span><span class="flw-ico">${ico(meta.icon)}</span>
          <span class="flw-title"><b>${H(s.label || meta.title)}</b><small>${H(shortOf(s)) || `<em>${T("не настроено", "not set up")}</em>`}</small></span>
          ${level ? `<span class="flw-badge ${level}" title="${H(probs.map((p) => p.text).join("\n"))}">!</span>` : ""}
          <span class="flw-tools">
            <button type="button" class="flw-mini" data-fa="up" ${index === 0 ? "disabled" : ""} title="${T("Выше", "Move up")}" aria-label="${T("Выше", "Move up")}">${ico("up")}</button>
            <button type="button" class="flw-mini" data-fa="down" ${index === list.length - 1 ? "disabled" : ""} title="${T("Ниже", "Move down")}" aria-label="${T("Ниже", "Move down")}">${ico("down")}</button>
            <button type="button" class="flw-mini" data-fa="dup" title="${T("Копия", "Duplicate")}" aria-label="${T("Копия", "Duplicate")}">${ico("copy")}</button>
            <button type="button" class="flw-mini danger" data-fa="del" title="${T("Удалить", "Delete")}" aria-label="${T("Удалить", "Delete")}">${ico("trash")}</button>
          </span><span class="flw-chev">${ico("chevron")}</span>
        </div>
        ${open ? `<div class="flw-body"><p class="flw-hint">${H(meta.hint)}</p>${formHtml(s)}${probs.map((p) => `<p class="flw-prob ${p.level}">${H(p.text)}</p>`).join("")}${adv}</div>` : ""}
      </div>${branches}</li>`;
  }
  function listHtml(steps, listKey) {
    return `<ol class="flw-list" data-list="${H(listKey)}">${(steps || []).map((s, i) => stepHtml(s, steps, i)).join("")}</ol>
      <button type="button" class="flw-add ${steps && steps.length ? "" : "empty"}" data-fa="add" data-list="${H(listKey)}">${ico("plus")}<span>${steps && steps.length ? T("Добавить шаг", "Add a step") : (listKey.endsWith(":else") ? T("Ничего не делать — или добавьте шаг", "Do nothing — or add a step") : T("Добавить шаг", "Add a step"))}</span></button>`;
  }
  function listOf(key) {
    if (key === "root") return fs.draft.steps;
    const [id, branch] = key.split(":"); const s = stepById(id);
    if (!s) return null; s[branch] = s[branch] || []; return s[branch];
  }

  function render() {
    if (!fs.host || !document.body.contains(fs.host)) return;
    fs.numbers = numbering(fs.draft.steps);
    const total = [...walk(fs.draft.steps)].length;
    fs.host.innerHTML = `<div class="flw" id="flw-root">
      <div class="flw-head"><div><b>${T("Шаги", "Steps")}</b><small>${T("Выполняются сверху вниз. Результат шага доступен дальше: ", "Run top to bottom. A step's result is available below: ")}<code>{{шаг1.результат}}</code></small></div>
        <div class="flw-head-actions"><span class="flw-count">${total}</span>
          <button type="button" class="pbtn ghost compact" data-fa="test" ${total ? "" : "disabled"}>${ico("flask")}${T("Тест шаг за шагом", "Step-by-step test")}</button></div></div>
      ${listHtml(fs.draft.steps, "root")}
      <div id="flw-test"></div>
    </div>`;
    if (typeof enhanceSelects === "function") enhanceSelects(fs.host);
    if (fs.test?.id) renderTest();
  }

  /* ------------------------------------------------------------------ add menu */
  function addMenu(anchor, listKey) {
    document.querySelector(".flw-menu")?.remove();
    const menu = document.createElement("div");
    menu.className = "flw-menu"; menu.setAttribute("role", "menu");
    const depth = listKey === "root" ? 0 : (fs.numbers[listKey.split(":")[0]] || "").split(".").length;
    const types = TYPES();
    const groups = CATS().map(([cat, label]) => {
      const rows = Object.entries(types).filter(([t, m]) => m.cat === cat && !(depth >= 3 && (t === "if" || t === "foreach")));
      return { n: rows.length + 1, html: `<div class="flw-menu-group"><div class="flw-menu-cat">${H(label)}</div>${rows
        .map(([t, m]) => `<button type="button" role="menuitem" class="c-${m.cat}" data-add="${t}">${ico(m.icon)}<span><b>${H(m.title)}</b><small>${H(m.hint)}</small></span></button>`).join("")}</div>` };
    }).filter((g) => g.n > 1);
    // r35-finish-b: 21 types in 2 grid columns were ~1100 px tall (half the menu always hidden behind a scroll) —
    // groups go into up to 3 balanced columns (whole groups, the shortest column first)
    const w = Math.min(780, window.innerWidth - 16);
    const ncol = w >= 700 ? 3 : w >= 460 ? 2 : 1;
    const cols = Array.from({ length: ncol }, () => ({ n: 0, html: "" }));
    groups.map((g, i) => ({ ...g, i })).sort((a, b) => b.n - a.n).forEach((g) => { const c = cols.reduce((a, b) => (b.n < a.n ? b : a)); c.n += g.n; (c.parts = c.parts || []).push(g); });
    cols.forEach((c) => { c.html = (c.parts || []).sort((a, b) => a.i - b.i).map((g) => g.html).join(""); c.first = (c.parts || [{ i: 99 }])[0].i; });
    cols.sort((a, b) => a.first - b.first);
    menu.style.setProperty("--cols", ncol);
    menu.innerHTML = cols.map((c) => `<div class="flw-menu-col">${c.html}</div>`).join("");
    document.body.appendChild(menu);
    menu.style.width = `${w}px`;
    let r = anchor.getBoundingClientRect();
    const need = menu.scrollHeight + 14;
    if (window.innerHeight - r.bottom < need && r.top < need) {
      // fits neither above nor below: scroll the builder so the button sits high and the menu opens below it
      let sc = anchor.parentElement;
      while (sc && sc !== document.body && !(sc.scrollHeight > sc.clientHeight + 4 && /auto|scroll/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
      if (sc && sc !== document.body) { sc.scrollTop += Math.max(0, Math.min(r.bottom + need - window.innerHeight, r.top - 56)); r = anchor.getBoundingClientRect(); }
    }
    menu.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
    const below = window.innerHeight - r.bottom - 12;
    if (below >= 470 || below > r.top) { menu.style.top = `${r.bottom + 6}px`; menu.style.maxHeight = `${Math.max(220, below)}px`; }
    else { menu.style.bottom = `${window.innerHeight - r.top + 6}px`; menu.style.maxHeight = `${Math.max(220, r.top - 12)}px`; }
    const close = (e) => { if (e && menu.contains(e.target)) return; menu.remove(); document.removeEventListener("mousedown", close, true); document.removeEventListener("keydown", esc, true); };
    const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); anchor.focus(); } };
    setTimeout(() => { document.addEventListener("mousedown", close, true); document.addEventListener("keydown", esc, true); }, 0);
    menu.addEventListener("click", (e) => {
      const b = e.target.closest("[data-add]"); if (!b) return;
      const list = listOf(listKey); if (!list) return;
      const step = defaults(b.dataset.add);
      const ref = lastListRef(null);
      if (ref && ["foreach", "only_new", "memubase_insert"].includes(step.type)) step[step.type === "memubase_insert" ? "rows" : "items"] = ref;
      if (ref && step.type === "if") step.cond.left = ref.replace(".result}}", ".result}}");
      if (ref && step.type === "decide") step.context = ref;
      if (ref && step.type === "telegram") step.text = `{{${ref.slice(2, -2)} | list}}`;
      list.push(step);
      fs.open.clear(); fs.open.add(step.id);
      close(); changed(true);
      setTimeout(() => { const el = $q(`[data-sid="${step.id}"] .flw-body input, [data-sid="${step.id}"] .flw-body textarea`, fs.host); el?.focus(); }, 30);
    });
    $q("button", menu)?.focus();
  }

  /* ------------------------------------------------------------------ events */
  function changed(rerender = false) {
    if (rerender) render();
    clearTimeout(fs.vtimer);
    fs.vtimer = setTimeout(validate, 450);
    fs.opts.onChange?.();
  }
  async function validate() {
    try {
      const r = await call("/api/plan/flow/validate", { steps: fs.draft.steps });
      const before = JSON.stringify(fs.problems);
      fs.problems = r.problems || [];
      if (JSON.stringify(fs.problems) !== before) {
        const active = document.activeElement, sel = active && active.dataset?.f, pos = active?.selectionStart;
        render();
        if (sel) { const el = $q(`[data-f="${CSS.escape(sel)}"]`, fs.host); if (el) { el.focus(); try { el.selectionStart = el.selectionEnd = pos; } catch (e) {} } }
      }
    } catch (e) { /* the server explains on save */ }
  }
  function setField(el) {
    const [sid, key] = el.dataset.f.split("|");
    const s = stepById(sid); if (!s) return;
    let v = el.type === "checkbox" ? el.checked : el.value;
    if (el.dataset.k === "num") v = el.value === "" ? 0 : Number(el.value);
    if (el.dataset.k === "list") v = el.value.split(",").map((x) => x.trim()).filter(Boolean);
    if (el.dataset.k === "json") {
      const raw = el.value.trim();
      if (!raw) v = key === "schema" ? null : {};
      else { try { v = JSON.parse(raw); el.classList.remove("bad"); } catch (e) { el.classList.add("bad"); return; } }
    }
    if (key.startsWith("cond.")) { s.cond = s.cond || {}; s.cond[key.slice(5)] = v; } else s[key] = v;
    if (s.type === "integration" && key === "tool") s.args = {};
    if (key === "name" || key === "label") { /* header text only */ }
    const head = el.closest(".flw-step")?.querySelector(":scope > .flw-card .flw-title small");
    if (head) head.textContent = shortOf(s);
    const rer = el.tagName === "SELECT" || el.type === "checkbox" || key === "cond.op" || key === "method";
    changed(rer);
  }
  function bind() {
    const host = fs.host;
    host.addEventListener("input", (e) => {
      const el = e.target;
      if (el.dataset.af) return askField(el);
      if (!el.dataset.f || el.tagName === "SELECT" || el.type === "checkbox") return;
      setField(el);
      if (el.dataset.tpl) {
        const before = el.value.slice(0, el.selectionStart || 0);
        const open = before.lastIndexOf("{{");
        if (open >= 0 && before.indexOf("}}", open) < 0) openAc(el, before.slice(open)); else closeAc();
      }
    });
    host.addEventListener("change", (e) => {
      const el = e.target;
      if (el.dataset.af) return askField(el, true);
      if (el.dataset.f && (el.tagName === "SELECT" || el.type === "checkbox" || el.dataset.k === "json" || el.dataset.k === "num")) setField(el);
    });
    host.addEventListener("keydown", (e) => {
      if (acKey(e)) return;
      if ((e.key === "Enter" || e.key === " ") && e.target.matches(".flw-step-head")) { e.preventDefault(); e.target.click(); }
    });
    host.addEventListener("focusout", (e) => { if (fs.ac && e.target === fs.ac.target) setTimeout(closeAc, 150); });
    host.addEventListener("click", (e) => {
      const el = e.target.closest("[data-fa]");
      if (!el || !host.contains(el)) return;
      e.stopPropagation();
      const act = el.dataset.fa;
      const li = el.closest("[data-sid]"); const sid = li?.dataset.sid;
      if (act === "add") return addMenu(el, el.dataset.list);
      if (act === "test") return startTest(false);
      if (act === "vars") { const input = el.parentElement.querySelector("input, textarea"); input.focus(); return openAc(input, ""); }
      if (act === "toggle") { if (e.target.closest(".flw-tools")) return; if (fs.open.has(sid)) fs.open.delete(sid); else fs.open.add(sid); render(); return; }
      if (act.startsWith("test-")) return testAction(act);
      const s = stepById(sid); if (!s) return;
      const pos = findList(sid);
      if (act === "up" || act === "down") {
        const j = pos.index + (act === "up" ? -1 : 1);
        if (j < 0 || j >= pos.list.length) return;
        const before = numbering(fs.draft.steps);
        [pos.list[pos.index], pos.list[j]] = [pos.list[j], pos.list[pos.index]];
        rewriteRefs(before, numbering(fs.draft.steps));
        changed(true);
        setTimeout(() => $q(`[data-sid="${sid}"] [data-fa="${act}"]`, host)?.focus(), 20);
        return;
      }
      if (act === "dup") {
        const copy = JSON.parse(JSON.stringify(s));
        for (const x of walk([copy])) x.id = uid();
        delete copy.name;
        pos.list.splice(pos.index + 1, 0, copy); changed(true); return;
      }
      if (act === "del") {
        const inner = [...walk([s])].length - 1;
        if (inner && !confirm(T(`Удалить шаг вместе с вложенными (${inner})?`, `Delete the step with its ${inner} nested steps?`))) return;
        const before = numbering(fs.draft.steps);
        pos.list.splice(pos.index, 1);
        rewriteRefs(before, numbering(fs.draft.steps));
        changed(true); return;
      }
      if (act === "cond-mode") { s.cond = s.cond || {}; s.cond.mode = el.dataset.v; changed(true); return; }
      if (act === "set") { s[el.dataset.key] = Number(el.dataset.v); changed(true); return; }
      if (act === "askf-add") { s.fields = [...(s.fields || []), { id: `f${(s.fields || []).length + 1}`, type: "text", label: "" }]; changed(true); return; }
      if (act === "askf-del") { s.fields.splice(+el.dataset.i, 1); changed(true); }
    });
  }
  function askField(el, commit = false) {
    const sid = el.closest("[data-sid]").dataset.sid; const s = stepById(sid);
    const [i, key] = el.dataset.af.split("|");
    const f = s.fields[+i]; if (!f) return;
    if (key === "options") f.options = el.value.split(",").map((x) => x.trim()).filter(Boolean);
    else f[key] = el.value;
    if (key === "label" && (!f.id || /^f\d+$|^answer$/.test(f.id))) f.id = f.id || `f${+i + 1}`;
    changed(commit && key === "type");
  }

  /* ------------------------------------------------------------------ test «шаг за шагом» + run log */
  async function startTest(all) {
    const task = fs.opts.getTask ? fs.opts.getTask() : { ...fs.draft };
    task.steps = fs.draft.steps;
    try {
      const r = await call("/api/plan/flow/test", { task, stepwise: !all });
      fs.test = { id: r.id, run: null };
      renderTest(); pollTest();
      setTimeout(() => $q("#flw-test", fs.host)?.scrollIntoView({ block: "start", behavior: "smooth" }), 60);
    } catch (e) { toast(e.message || String(e), "err"); }
  }
  async function pollTest() {
    const t = fs.test; if (!t) return;
    clearTimeout(t.timer);
    try { t.run = await api(`/api/plan/flow/run?id=${encodeURIComponent(t.id)}`); } catch (e) { return; }
    if (fs.test !== t) return;
    renderTest();
    if (!["done", "failed", "cancelled"].includes(t.run.status)) t.timer = setTimeout(pollTest, t.run.status === "paused" ? 900 : 650);
  }
  function statusText(run) {
    return ({ queued: T("в очереди", "queued"), running: T("выполняется…", "running…"), paused: T("пауза после шага", "paused after a step"), waiting: T("ждёт ответа", "waiting for an answer"),
      done: run?.ok === false ? T("готово с ошибкой", "finished with an error") : T("готово", "finished"), failed: T("ошибка", "failed"), cancelled: T("остановлено", "stopped") })[run?.status] || run?.status || "";
  }
  function logHtml(run, opts = {}) {
    const rows = run?.log || [];
    if (!rows.length) return `<div class="flw-log-empty"><span class="pln-spin"></span>${T("Запускаю…", "Starting…")}</div>`;
    return `<ol class="flw-log">${rows.map((r) => {
      const depth = Math.max(0, (r.depth || 1) - 1);
      // an ask in a test waits inline: its row stays «running» — show it as waiting
      const st = r.status === "running" && r.type === "ask_user" ? "waiting" : (r.status || "running");
      const k = `${r.key || r.n}#${r.iter || ""}`;
      const auto = !!opts.openLast && r === rows[rows.length - 1];
      const open = opts.state?.has(k) ? opts.state.get(k) : auto;
      const mark = { ok: ico("check"), error: ico("x"), simulated: ico("flask"), waiting: ico("pause"), retrying: ico("history"), running: '<span class="pln-spin"></span>' }[st] || ico("check");
      const body = [r.input && opts.full !== false ? `<div class="flw-io"><span>${T("Вход", "Input")}</span><pre>${H(r.input)}</pre></div>` : "",
        r.sent ? `<div class="flw-io"><span>${st === "simulated" ? T("Было бы отправлено", "Would be sent") : T("Отправлено", "Sent")}</span><pre>${H(r.sent)}</pre></div>`
          : r.output ? `<div class="flw-io"><span>${T("Результат", "Result")}</span><pre>${H(r.output)}</pre></div>` : "",
        r.error ? `<div class="flw-io err"><span>${T("Ошибка", "Error")}</span><pre>${H(r.error)}</pre></div>` : ""].join("");
      return `<li class="flw-log-row s-${st}" style="--d:${depth}"><details data-k="${H(k)}" ${auto ? 'data-auto="1"' : ""} ${open ? "open" : ""}>
        <summary><span class="flw-log-mark">${mark}</span><span class="flw-num sm">${H(r.n)}</span><b>${H(r.title)}</b>${r.iter ? `<span class="flw-iter">#${H(r.iter)}</span>` : ""}
          <small>${H(r.short || "")}</small>${r.attempts > 1 ? `<span class="flw-iter">${T("попыток", "attempts")}: ${H(r.attempts)}</span>` : ""}${typeof r.ms === "number" ? `<span class="flw-ms">${r.ms < 1000 ? r.ms + " ms" : (r.ms / 1000).toFixed(1) + " s"}</span>` : ""}</summary>
        ${body || (st === "running" || st === "waiting" ? "" : `<div class="flw-io"><pre>—</pre></div>`)}</details></li>`;
    }).join("")}</ol>`;
  }
  function renderTest() {
    const box = $q("#flw-test", fs.host); if (!box || !fs.test) return;
    const run = fs.test.run;
    const st = run?.status || "queued";
    const live = !["done", "failed", "cancelled"].includes(st);
    const wait = run?.wait || {};
    // r36: the test re-renders on every poll — rows the user opened or closed keep that state
    const state = new Map($$q("details[data-k]", box).filter((d) => d.open !== !!d.dataset.auto).map((d) => [d.dataset.k, d.open]));
    box.innerHTML = `<section class="flw-test s-${H(st)}">
      <header>${ico("flask")}<div><b>${T("Тест шаг за шагом", "Step-by-step test")}</b><small>${T("Поиск, модели и решения выполняются по-настоящему; отправка, запись и действия только показываются.", "Search, models and decisions really run; sending, writing and actions are only shown.")}</small></div>
        <span class="flw-test-status">${H(statusText(run))}</span></header>
      <div class="flw-test-actions">
        ${live ? `<button type="button" class="pbtn ghost compact" data-fa="test-cancel">${ico("stop")}${T("Остановить", "Stop")}</button>` : `<button type="button" class="pbtn ghost compact" data-fa="test-close">${T("Закрыть", "Close")}</button><button type="button" class="pbtn ghost compact" data-fa="test-again">${ico("history")}${T("Ещё раз", "Again")}</button>`}
        ${st === "paused" ? `<button type="button" class="pbtn ghost compact" data-fa="test-all">${ico("skip")}${T("До конца", "Run to the end")}</button><button type="button" class="pbtn compact" data-fa="test-next">${ico("play")}${T("Дальше", "Next")}</button>` : ""}
      </div>
      ${st === "waiting" || (st === "running" && (run?.log || []).some((r) => r.status === "waiting" || (r.status === "running" && r.type === "ask_user"))) ? `<div class="flw-note ask">${ico("help")}<span>${T("Шаг «Спросить меня» ждёт ответа во «Входящих» или в Telegram.", "The “Ask me” step waits for your answer in the Inbox or Telegram.")}</span>${window.MemuInbox?.open ? `<button type="button" class="pln-link" data-fa="test-inbox">${T("Открыть «Входящие»", "Open the Inbox")}</button>` : ""}</div>` : ""}
      ${logHtml(run, { openLast: true, state })}
      ${run?.error ? `<p class="flw-prob error">${H(run.error)}</p>` : ""}</section>`;
    if (wait.kind === "step") $q("[data-fa=test-next]", box)?.focus({ preventScroll: true });
  }
  async function testAction(act) {
    const t = fs.test; if (!t) return;
    if (act === "test-next" || act === "test-all") { await call("/api/plan/flow/continue", { id: t.id, all: act === "test-all" }); setTimeout(pollTest, 250); }
    else if (act === "test-cancel") { await call("/api/plan/flow/cancel", { id: t.id }); setTimeout(pollTest, 300); }
    else if (act === "test-close") { clearTimeout(t.timer); fs.test = null; const box = $q("#flw-test", fs.host); if (box) box.innerHTML = ""; }
    else if (act === "test-again") { clearTimeout(t.timer); startTest(false); }
    else if (act === "test-inbox") window.MemuInbox?.open?.({ stack: true });   // r36: over the builder, not instead of it
  }

  /* ------------------------------------------------------------------ public */
  async function loadCatalog() {
    if (fs.cat && Date.now() - fs.cat._at < 60000) return fs.cat;
    try { fs.cat = await api(`/api/plan/flow/catalog?lang=${en() ? "en" : "ru"}`); fs.cat._at = Date.now(); } catch (e) { fs.cat = fs.cat || { tasks: [], scenarios: [], macros: [], actions: [], tables: [], _at: 0 }; }
    return fs.cat;
  }
  async function fromTask(task) {
    try { return (await call("/api/plan/flow/of", { task })).steps || []; } catch (e) { return []; }
  }
  async function mount(host, draft, opts = {}) {
    if (!host) return;
    fs.host = host; fs.draft = draft; fs.opts = opts; fs.problems = []; fs.test = null;
    if (!Array.isArray(draft.steps) || !draft.steps.length) {
      const legacy = { ...draft, kind: draft.prev_kind || draft.kind };
      draft.steps = draft.prev_kind && String(draft.prompt || draft.command || draft.command_id || "").trim() ? await fromTask(legacy) : [];
      if (!draft.steps.length) draft.steps = [defaults("web_search")];
      fs.open.clear(); fs.open.add(draft.steps[0].id);
    }
    await loadCatalog();
    render();
    if (!host.dataset.flwBound) { bind(); host.dataset.flwBound = "1"; }
    validate();
  }
  function summaryHtml(steps, depth = 0) {
    const nums = numbering(steps);
    const one = (s) => {
      const meta = TYPES()[s.type] || { icon: "sparkles", title: s.type, cat: "app" };
      const inner = s.type === "if" ? `${(s.then || []).length ? `<div class="flw-sum-br yes"><span>${T("Да", "Yes")}</span>${list(s.then)}</div>` : ""}${(s.else || []).length ? `<div class="flw-sum-br no"><span>${T("Нет", "No")}</span>${list(s.else)}</div>` : ""}`
        : s.type === "foreach" ? `<div class="flw-sum-br loop"><span>${T("для каждого", "for each")}</span>${list(s.body)}</div>` : "";
      return `<li class="c-${meta.cat}"><span class="flw-num sm">${H(nums[s.id] || "")}</span>${ico(meta.icon)}<b>${H(s.label || meta.title)}</b><small>${H(shortOf(s))}</small>${inner}</li>`;
    };
    const list = (arr) => `<ol class="flw-sum">${(arr || []).map(one).join("")}</ol>`;
    return `<div class="flw-sum-wrap">${list(steps)}</div>`;
  }
  async function historyHtml(task, host) {
    if (!host) return;
    let runs = [];
    try { runs = (await api(`/api/plan/flow/runs?task_id=${encodeURIComponent(task.id)}&limit=20`)).runs || []; } catch (e) {}
    runs = runs.filter((r) => !r.dry_run || true);
    if (!runs.length) { host.innerHTML = ""; return; }
    host.innerHTML = `<div class="pln-list-head"><b>${T("Запуски по шагам", "Runs step by step")}</b></div>
      <div class="flw-runs">${runs.map((r) => `<details class="flw-run s-${H(r.status)}" data-run="${H(r.id)}"><summary><span class="flw-run-dot"></span><b>${H(statusText(r))}</b>${r.dry_run ? `<span class="pln-chip">${T("тест", "test")}</span>` : ""}
        ${r.trigger && r.trigger !== "manual" ? `<span class="pln-chip">${H(r.trigger)}</span>` : ""}<span class="flw-ms">${H(new Date((r.started || 0) * 1000).toLocaleString(en() ? "en-GB" : "ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}</span></summary><div class="flw-run-body"><span class="pln-spin"></span></div></details>`).join("")}</div>`;
    host.addEventListener("toggle", async (e) => {
      const d = e.target; if (!d.matches?.("details[data-run]") || !d.open || d.dataset.loaded) return;
      d.dataset.loaded = "1";
      try { const run = await api(`/api/plan/flow/run?id=${encodeURIComponent(d.dataset.run)}`); $q(".flw-run-body", d).innerHTML = logHtml(run) + (run.error ? `<p class="flw-prob error">${H(run.error)}</p>` : ""); }
      catch (err) { $q(".flw-run-body", d).textContent = err.message || String(err); }
    }, true);
  }
  async function mountAutomations(host) {
    if (!host) return;
    let items = [];
    try { items = (await api(`/api/plan/flow/automations?lang=${en() ? "en" : "ru"}`)).items || []; } catch (e) { items = []; }
    if (!items.length) { host.innerHTML = ""; return; }
    host.innerHTML = `<details class="flw-autos"><summary>${ico("zap")}<b>${T("Сценарии и кнопки Быстрого доступа", "Quick Access scenarios and buttons")}</b><span class="pln-chip">${items.length}</span>
        <small>${T("те же цепочки шагов; правятся в Быстром доступе", "the same step chains; edited in Quick Access")}</small></summary>
      <div class="flw-autos-list">${items.map((a, i) => `<div class="flw-auto" data-i="${i}"><div class="flw-auto-head">${ico(a.source === "scenario" ? "zap" : "grid")}<b>${H(a.name || a.id)}</b>
          <span class="pln-chip">${a.source === "scenario" ? T("сценарий", "scenario") : T("кнопка", "button")}</span>${a.starts?.phrases?.length ? `<small>«${H(a.starts.phrases[0])}»</small>` : ""}${a.starts?.hotkey ? `<kbd>${H(a.starts.hotkey)}</kbd>` : ""}
          <button type="button" class="pln-link" data-auto-task="${i}">${ico("plus")}${T("Запускать по расписанию", "Run on a schedule")}</button></div>
        ${summaryHtml((a.steps || []).map((s) => ({ ...s, id: s.id || uid() })))}</div>`).join("")}</div></details>`;
    host.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-auto-task]"); if (!b) return;
      const a = items[+b.dataset.autoTask];
      try { const r = await call("/api/plan/flow/as-task", { source: a.source, id: a.id }); window.MemuPlan?.openBuilder?.({ ...r.task, id: uid() }); }
      catch (err) { toast(err.message || String(err), "err"); }
    });
  }

  window.MemuFlows = { mount, fromTask, summaryHtml, historyHtml, mountAutomations, startTest, logHtml, TYPES };
})();
