/* MemuAI — Round 35: «Входящие» (Inbox) — единый хаб всего, что ждёт пользователя.
   Backend: /api/pg/inbox/* (app/inbox_api.py), store app/inbox.py, producers app/inbox_hooks.py.
   Sidebar button #nav-inbox (inserted after «Планирование») with an unread badge, live via SSE.
   Buttons call POST /api/pg/inbox/item/<id>/action → the owning subsystem's own handler;
   «kind: ui» buttons (open a chat / memory / planning / module / Proxy) are run here.
   window.MemuInbox = { open(), refresh(), post({title, body, source, kind, severity, dedupe_key, actions}) } */
(function () {
  "use strict";
  if (window.MemuInbox) return;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const I = (name) => (typeof icon === "function" ? icon(name) : "");
  const $q = (sel, root = document) => root.querySelector(sel);
  const $$q = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const call = (path, opts) => (typeof api === "function" ? api(path, opts) : fetch(path, {
    method: (opts && opts.method) || "GET", headers: { "Content-Type": "application/json" },
    body: opts && opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  }).then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error(d.error || `HTTP ${r.status}`), { payload: d }); return d; }));

  const GROUPS = () => [
    ["ask", T("Нужен ответ", "Needs a reply"), "chat"],
    ["confirm", T("Подтверждения", "Approvals"), "check"],
    ["notify", T("Уведомления", "Notifications"), "bell"],
    ["done", T("Готово", "Done"), "undo"],
  ];
  const SOURCES = () => ({
    dfy: [T("Сделай за меня", "Do For You"), "globe"],
    computer: [T("Управление ПК", "Computer control"), "monitor"],
    memory: [T("Память", "Memory"), "brain"],
    memucode: ["MemuCode", "terminal"],
    schedule: [T("Планирование", "Planning"), "clock"],
    flows: [T("Планирование", "Planning"), "clock"],
    triggers: [T("Планирование", "Planning"), "clock"],
    models: [T("Новые модели API", "New API models"), "sparkle"],
    modules: [T("Модули", "Modules"), "zap"],
    proxy: ["Proxy", "link"],
    work: ["Work", "work"],
    askuser: [T("Вопрос ИИ", "AI question"), "chat"],
    ask_user: [T("Вопрос", "Question"), "chat"],   // r36: the server posts forms as «ask_user» (the raw id was shown)
    ui: ["MemuAI", "bell"],
  });
  const srcInfo = (s) => SOURCES()[s] || [String(s || "MemuAI"), "bell"];

  const st = {
    items: [], counts: {}, group: "all", source: "all", q: "", sel: "", expanded: new Set(),
    form: null, showDone: false, loading: false, reloadTimer: 0, lastAttention: -1, es: null, menu: "",
  };

  /* ---------------------------------------------------------------- sidebar button + badge */
  function ensureButton() {
    if ($q("#nav-inbox")) return $q("#nav-inbox");
    const anchor = $q("#nav-sched");
    if (!anchor || !anchor.parentNode) return null;
    const btn = document.createElement("button");
    btn.className = "side-btn";
    btn.id = "nav-inbox";
    btn.type = "button";
    btn.innerHTML = `<span class="ico">${I("bell")}</span><span class="ibx-nav-label"></span><span class="ibx-badge" hidden></span>`;
    anchor.parentNode.insertBefore(btn, anchor.nextSibling);
    btn.addEventListener("click", () => open());
    labelButton();
    return btn;
  }
  function labelButton() {
    const btn = $q("#nav-inbox");
    if (!btn) return;
    $q(".ibx-nav-label", btn).textContent = T("Входящие", "Inbox");
    paintBadge();
  }
  function paintBadge() {
    const btn = $q("#nav-inbox");
    if (!btn) return;
    const c = st.counts || {};
    const badge = $q(".ibx-badge", btn);
    const n = Number(c.unread || 0);
    const attention = Number(c.ask || 0) + Number(c.confirm || 0);
    badge.hidden = !n;
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.classList.toggle("warn", !!c.warn && !attention);
    badge.classList.toggle("ask", attention > 0);
    const tip = n ? T(`Входящие: непрочитанных — ${n}`, `Inbox: ${n} unread`) : T("Входящие", "Inbox");
    btn.title = tip;
    btn.setAttribute("aria-label", tip);
  }

  /* ---------------------------------------------------------------- live updates */
  // Round 36 (perf): one SSE connection while the window is visible; it is closed after 30 s in the
  // background (no server loop, no wake-ups) and reopened with a fresh count when the window comes back.
  // The same stream nudges memuai:// links (event «links») — deeplinks.js no longer polls every 4 s.
  function connect() {
    if (st.es || document.visibilityState === "hidden") return;
    if (typeof EventSource === "undefined") { startPoll(); return; }
    const es = window.MemuLive ? MemuLive.stream("/api/pg/inbox/stream") : new EventSource("/api/pg/inbox/stream");   // r40-lag: a WebSocket
    st.es = es;
    es.addEventListener("open", () => { stopPoll(); st.live = true; });
    es.addEventListener("counts", (event) => {
      let data = {};
      try { data = JSON.parse(event.data || "{}"); } catch (e) { return; }
      onCounts(data);
    });
    es.addEventListener("links", () => document.dispatchEvent(new CustomEvent("memu:links")));
    es.onerror = () => {
      // the server restarts with MemuAI; EventSource reconnects itself — a slow visible-only poll meanwhile
      st.live = false;
      startPoll();
    };
  }
  function disconnect() {
    if (st.es) { st.es.close(); st.es = null; }
    st.live = false;
    stopPoll();
  }
  function startPoll() {
    if (st.poll) return;
    st.poll = setInterval(() => {
      if (document.visibilityState === "hidden") return;
      if (st.es && st.es.readyState === 1) { stopPoll(); st.live = true; return; }
      refreshCounts();
      document.dispatchEvent(new CustomEvent("memu:links"));
    }, 15000);
  }
  function stopPoll() { clearInterval(st.poll); st.poll = 0; }
  function onVisibility() {
    clearTimeout(st.hideTimer);
    if (document.visibilityState === "hidden") { st.hideTimer = setTimeout(disconnect, 30000); return; }
    if (!st.es) { refreshCounts(); connect(); }
  }
  async function refreshCounts() {
    try { onCounts(await call("/api/pg/inbox/counts")); } catch (e) { /* offline */ }
  }
  function onCounts(data) {
    const attention = Number(data.ask || 0) + Number(data.confirm || 0);
    const prev = st.lastAttention;
    st.counts = data;
    paintBadge();
    if (isOpen()) scheduleReload();
    // Round 35 (askuser): no toast when the new question is the ask_user form already on screen in this chat
    else if (prev >= 0 && attention > prev && typeof toast === "function" && !document.querySelector("#messages .au-pending")) {
      toast(T("Входящие: новый вопрос или подтверждение", "Inbox: a new question or approval"), "info",
        { label: T("Открыть", "Open"), run: () => open() });
    }
    st.lastAttention = attention;
  }
  function scheduleReload() {
    clearTimeout(st.reloadTimer);
    st.reloadTimer = setTimeout(() => {
      // never redraw under a half-typed answer
      const active = document.activeElement;
      if (active && active.closest && active.closest("#ibx-root .ibx-form, #ibx-root .au-card")) { scheduleReload(); return; }
      load().then(renderList);
    }, 250);
  }

  /* ---------------------------------------------------------------- data */
  async function load() {
    st.loading = true;
    try {
      const params = new URLSearchParams({ status: "all", limit: "400" });
      const data = await call(`/api/pg/inbox?${params}`);
      st.items = data.items || [];
      st.counts = data.counts || {};
      paintBadge();
    } catch (e) {
      st.error = e.message;
    } finally { st.loading = false; }
  }
  const isOpen = () => !!$q("#panel.ibx-panel #ibx-root");

  function visible() {
    const q = st.q.trim().toLowerCase();
    return st.items.filter((it) => {
      if (st.source !== "all" && it.source !== st.source) return false;
      if (st.group !== "all" && it.group !== st.group) return false;
      if (q && !`${it.title} ${it.body} ${srcInfo(it.source)[0]}`.toLowerCase().includes(q)) return false;
      return true;
    });
  }

  /* ---------------------------------------------------------------- render */
  function ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - Number(ts || 0));
    if (s < 45) return T("только что", "just now");
    if (s < 3600) return T(`${Math.round(s / 60)} мин назад`, `${Math.round(s / 60)} min ago`);
    if (s < 86400) return T(`${Math.round(s / 3600)} ч назад`, `${Math.round(s / 3600)} h ago`);
    const d = new Date(ts * 1000);
    return d.toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" }) + " " +
      d.toLocaleTimeString(en() ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit" });
  }
  function until(ts) {
    const d = new Date(ts * 1000);
    const sameDay = d.toDateString() === new Date().toDateString();
    const time = d.toLocaleTimeString(en() ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit" });
    return sameDay ? time : d.toLocaleDateString(en() ? "en-US" : "ru-RU", { day: "numeric", month: "short" }) + " " + time;
  }

  /* opts.stack (r36): open over the current panel (the task builder's test) — closing comes back to it with the draft */
  function open(opts = {}) {
    ensureButton();
    const first = !isOpen();
    if (first) {
      panel(shell(), "ibx-panel", { root: !opts.stack, onClose: () => { st.form = null; st.menu = ""; } });
      bindShell();
    }
    load().then(() => { renderList(); if (first) focusList(); });
    if (typeof recordView === "function") { try { recordView({ type: "panel", name: "inbox" }); } catch (e) { /* optional */ } }
  }

  function shell() {
    return `<div class="ibx-root" id="ibx-root">
      <div class="ibx-top"><div class="ibx-head"><h2>${T("Входящие", "Inbox")}</h2>
        <p class="sub">${T("Всё, что ждёт вас: вопросы и подтверждения, факты памяти, результаты задач и новые модели.", "Everything waiting for you: questions and approvals, memory facts, task results and new models.")}</p></div>
        <div class="ibx-top-actions">
          <button class="pbtn ghost" type="button" data-act="read-all">${I("check")}<span>${T("Прочитать всё", "Mark all read")}</span></button>
        </div></div>
      <div class="ibx-bar">
        <label class="ibx-search">${I("search")}<input id="ibx-q" type="search" autocomplete="off" placeholder="${T("Поиск во входящих", "Search the inbox")}" aria-label="${T("Поиск во входящих", "Search the inbox")}"></label>
        <div class="ibx-tabs" role="tablist" id="ibx-tabs"></div>
      </div>
      <div class="ibx-sources" id="ibx-sources"></div>
      <div class="ibx-list" id="ibx-list" role="listbox" tabindex="0" aria-label="${T("Входящие", "Inbox")}"></div>
      <div class="ibx-keys">${T("Клавиши: ↑↓ — выбор, Enter — раскрыть, A — основное действие, E — убрать, S — отложить, U — прочитано/нет, / — поиск",
        "Keys: ↑↓ select, Enter expand, A main action, E dismiss, S snooze, U read/unread, / search")}</div>
    </div>`;
  }

  function renderTabs() {
    const c = st.counts || {};
    const tabs = [["all", T("Все", "All"), Number(c.open || 0)], ...GROUPS().map(([id, label]) => [id, label, Number(c[id] || 0)])];
    $q("#ibx-tabs").innerHTML = tabs.map(([id, label, n]) =>
      `<button role="tab" type="button" class="${st.group === id ? "on" : ""}" data-group="${id}" aria-selected="${st.group === id}">${H(label)}${n ? `<span>${n}</span>` : ""}</button>`).join("");
    const counts = {};
    st.items.forEach((it) => { if (it.status === "open") counts[it.source] = (counts[it.source] || 0) + 1; });
    const sources = Object.keys(counts);
    if (st.source !== "all" && !sources.includes(st.source)) sources.push(st.source);
    $q("#ibx-sources").innerHTML = sources.length > 1 || st.source !== "all"
      ? [`<button type="button" class="ibx-chip ${st.source === "all" ? "on" : ""}" data-source="all">${T("Все источники", "All sources")}</button>`,
        ...sources.sort().map((s) => `<button type="button" class="ibx-chip ${st.source === s ? "on" : ""}" data-source="${H(s)}">${I(srcInfo(s)[1])}${H(srcInfo(s)[0])}${counts[s] ? `<span>${counts[s]}</span>` : ""}</button>`)].join("")
      : "";
  }

  function renderList() {
    if (!isOpen()) return;
    renderTabs();
    const list = $q("#ibx-list");
    const rows = visible();
    let html = "";
    if (!rows.length) {
      const filtered = st.q || st.source !== "all" || st.group !== "all";
      html = `<div class="ibx-empty">${I(filtered ? "search" : "check")}<b>${filtered ? T("Ничего не найдено", "Nothing found") : T("Всё разобрано", "All caught up")}</b>
        <span>${filtered ? T("Измените поиск или фильтр.", "Change the search or the filter.")
          : T("Здесь появятся вопросы DFY и MemuCode, подтверждения, факты памяти на проверку, результаты задач по расписанию и новые модели API.", "DFY and MemuCode questions, approvals, memory facts to review, scheduled task results and new API models will show up here.")}</span></div>`;
    } else if (st.group === "all") {
      for (const [id, label, ic] of GROUPS()) {
        let part = rows.filter((it) => it.group === id && !it.snoozed);
        if (!part.length) continue;
        if (id === "done") {
          html += `<div class="ibx-sec done"><button type="button" class="ibx-sec-toggle" data-act="toggle-done" aria-expanded="${st.showDone}">${I(ic)}<b>${H(label)}</b><span>${part.length}</span>${I(st.showDone ? "updown" : "arrowDown")}</button>
            <button type="button" class="ibx-link" data-act="clear-done">${T("Очистить", "Clear")}</button></div>`;
          if (!st.showDone) continue;
          part = part.slice(0, 60);
        } else {
          html += `<div class="ibx-sec">${I(ic)}<b>${H(label)}</b><span>${part.length}</span></div>`;
        }
        html += part.map(itemHtml).join("");
      }
      const snoozed = rows.filter((it) => it.snoozed);
      if (snoozed.length) html += `<div class="ibx-sec">${I("clock")}<b>${T("Отложено", "Snoozed")}</b><span>${snoozed.length}</span></div>` + snoozed.map(itemHtml).join("");
    } else {
      html = rows.map(itemHtml).join("");
    }
    list.innerHTML = html;
    if (st.sel && !rows.some((it) => it.id === st.sel)) st.sel = "";
    markSel(false);
    restoreForm();
  }

  function actionBtn(it, a) {
    const style = a.style === "primary" ? "" : a.style === "danger" ? " danger" : " ghost";
    const busy = st.busy === `${it.id}:${a.id}`;
    return `<button type="button" class="pbtn compact${style}" data-run="${H(a.id)}" ${busy ? "disabled" : ""}>${busy ? '<span class="ibx-spin"></span>' : ""}${H(a.label)}</button>`;
  }

  function itemHtml(it) {
    const [srcLabel, srcIcon] = srcInfo(it.source);
    const openItem = st.expanded.has(it.id);
    const done = it.status !== "open";
    const askForm = done ? "" : askFormHtml(it);
    const acts = done || askForm ? [] : (it.actions || []);
    const body = String(it.body || "");
    const dfyCard = openItem && it.source === "dfy" && !done && it.ref && it.ref.task_id
      ? `<div class="ibx-dfy" data-dfy-task="${H(it.ref.task_id)}"></div>` : "";
    const result = done ? `<span class="ibx-result">${I("check")}${H(resultLabel(it))}</span>` : "";
    return `<article class="ibx-item ${it.read ? "" : "unread"} ${openItem ? "open" : ""} ${done ? "is-done" : ""} ${it.snoozed ? "snoozed" : ""} sev-${H(it.severity || "info")} g-${H(it.group)}" data-id="${H(it.id)}" role="option" aria-selected="${st.sel === it.id}" aria-expanded="${openItem}">
      <div class="ibx-ico src-${H(it.source)}">${I(srcIcon)}</div>
      <div class="ibx-main">
        <div class="ibx-meta"><span class="ibx-src">${H(srcLabel)}</span><span class="ibx-dot-sep"></span><span class="ibx-time" title="${H(new Date((it.updated || 0) * 1000).toLocaleString(en() ? "en-US" : "ru-RU"))}">${H(ago(it.updated))}</span>
          ${it.count > 1 ? `<span class="ibx-count" title="${T("Повторов", "Repeats")}">×${Number(it.count)}</span>` : ""}
          ${it.snoozed ? `<span class="ibx-snz">${I("clock")}${T("до ", "until ")}${H(until(it.snooze_until))}</span>` : ""}${result}
          ${done ? "" : `<div class="ibx-tools">
            ${it.read ? "" : `<span class="ibx-unread-dot" title="${T("Не прочитано", "Unread")}"></span>`}
            <div class="ibx-snooze-wrap"><button type="button" class="ibx-icon-btn" data-act="snooze-menu" title="${T("Отложить", "Snooze")}" aria-label="${T("Отложить", "Snooze")}" aria-expanded="${st.menu === it.id}">${I("clock")}</button>
              ${st.menu === it.id ? snoozeMenu() : ""}</div>
            <button type="button" class="ibx-icon-btn" data-act="read" title="${it.read ? T("Отметить непрочитанным", "Mark unread") : T("Отметить прочитанным", "Mark read")}" aria-label="${it.read ? T("Отметить непрочитанным", "Mark unread") : T("Отметить прочитанным", "Mark read")}">${I(it.read ? "unread" : "check")}</button>
            <button type="button" class="ibx-icon-btn" data-act="dismiss" title="${T("Убрать", "Dismiss")}" aria-label="${T("Убрать", "Dismiss")}">${I("close")}</button></div>`}</div>
        <button type="button" class="ibx-title" data-act="toggle">${H(it.title)}</button>
        ${body && !askForm ? `<div class="ibx-body ${openItem ? "full" : ""}">${H(openItem ? body : body.slice(0, 400))}</div>` : ""}
        ${dfyCard}${askForm}
        <div class="ibx-form-slot"></div>
        ${acts.length ? `<div class="ibx-actions"><div class="ibx-btns">${acts.map((a) => actionBtn(it, a)).join("")}</div></div>` : ""}
      </div>
    </article>`;
  }
  /* r36: a «Спросить меня» form of a planned task (no chat behind it) is answered right here — before, the item had
     only «Открыть» (which just expanded the text) and «Пропустить», so the flow could never get a real answer */
  function askFormHtml(it) {
    const ref = it.ref || {};
    if (it.source !== "ask_user" || ref.chat_id || !ref.form_id || !ref.form || !window.MemuAskForm) return "";
    const rec = { id: ref.form_id, form: ref.form, status: "pending" };
    MemuAskForm.mount(rec, { compact: true, onSubmit: async (answers, skipped) => {
      const res = await call(`/api/pg/inbox/item/${encodeURIComponent(it.id)}/action`,
        { method: "POST", body: { action: skipped ? "skip" : "answer", payload: skipped ? {} : { answers } } });
      if (res && res.ok === false && Array.isArray(res.errors)) return { errors: res.errors };
      if (res && res.ok === false) throw new Error(res.error || T("Не получилось", "Failed"));
      await load(); renderList();
    } });
    return `<div class="ibx-ask">${MemuAskForm.html(rec, { compact: true })}</div>`;
  }
  function resultLabel(it) {
    const r = String(it.result || "");
    if (it.status === "dismissed" || r === "dismissed") return T("Убрано", "Dismissed");
    const map = { approve: T("Разрешено", "Allowed"), approve_all: T("Разрешено", "Allowed"), deny: T("Отклонено", "Denied"),
      reject: T("Отклонено", "Rejected"), accept: T("Принято", "Accepted"), edit: T("Изменено и принято", "Edited and accepted"),
      applied: T("Применено", "Applied"), recovered: T("Снова работает", "Working again"), expired: T("Устарело", "Expired"),
      stale: T("Уже неактуально", "No longer pending"), gone: T("Закрыто", "Closed"), ack: T("Прочитано", "Seen"),
      reviewed: T("Проверено", "Reviewed"), answered: T("Отвечено", "Answered"), reply: T("Отвечено", "Answered"),
      code: T("Код отправлен", "Code sent"), decline: T("Отказано", "Declined") };
    if (/^(choice|stuck):/.test(r)) return T("Отвечено", "Answered");
    return map[r] || T("Готово", "Done");
  }
  function snoozeMenu() {
    return `<div class="ibx-menu" role="menu">${[["1h", T("На 1 час", "For 1 hour")], ["3h", T("На 3 часа", "For 3 hours")],
      ["tomorrow", T("До завтра, 9:00", "Until tomorrow 9:00")], ["1w", T("На неделю", "For a week")]].map(([p, l]) =>
      `<button type="button" role="menuitem" data-snooze="${p}">${H(l)}</button>`).join("")}</div>`;
  }

  /* ---------------------------------------------------------------- forms (reply / edit / code) */
  function showForm(it, a) {
    st.form = { id: it.id, action: a.id, value: a.form === "text" && it.source === "memory" ? String((it.ref || {}).text || "") : "" };
    renderList();
    const input = $q(`#ibx-list .ibx-item[data-id="${CSS.escape(it.id)}"] .ibx-form input, #ibx-list .ibx-item[data-id="${CSS.escape(it.id)}"] .ibx-form textarea`);
    if (input) { input.focus(); if (input.select && st.form.value) input.select(); }
  }
  function restoreForm() {
    if (!st.form) return;
    const it = st.items.find((x) => x.id === st.form.id);
    const slot = it && $q(`#ibx-list .ibx-item[data-id="${CSS.escape(it.id)}"] .ibx-form-slot`);
    if (!it || !slot || it.status !== "open") { st.form = null; return; }
    const a = (it.actions || []).find((x) => x.id === st.form.action) || {};
    const code = a.form === "code";
    slot.closest(".ibx-item").classList.add("has-form");
    slot.innerHTML = `<form class="ibx-form">
      ${code ? `<input class="pinput" name="v" inputmode="numeric" autocomplete="one-time-code" maxlength="12" placeholder="${T("Код", "Code")}" aria-label="${T("Код", "Code")}">`
        : `<textarea class="ptexta" name="v" rows="2" maxlength="4000" placeholder="${H(a.placeholder && it.source !== "memory" ? a.placeholder : T("Ваш ответ", "Your answer"))}" aria-label="${H(a.label || T("Ответ", "Answer"))}"></textarea>`}
      <div class="ibx-form-row"><button type="button" class="pbtn compact ghost" data-act="form-cancel">${T("Отмена", "Cancel")}</button>
        <button type="submit" class="pbtn compact">${I("send")}<span>${H(a.label || T("Отправить", "Send"))}</span></button></div></form>`;
    const field = $q("[name=v]", slot);
    field.value = st.form.value || "";
    field.addEventListener("input", () => { if (st.form) st.form.value = field.value; });
    field.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $q("form", slot).requestSubmit(); }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); st.form = null; renderList(); focusList(); }
    });
    $q("form", slot).addEventListener("submit", (e) => {
      e.preventDefault();
      const value = field.value.trim();
      if (!value) { field.focus(); return; }
      const f = st.form; st.form = null;
      run(it, f.action, { text: value });
    });
  }

  /* ---------------------------------------------------------------- actions */
  async function run(it, actionId, payload = {}) {
    const a = (it.actions || []).find((x) => x.id === actionId) || { id: actionId };
    if (a.form && !payload.text) { showForm(it, a); return; }
    if (a.confirm && !payload.confirm) {
      const ok = typeof askConfirm === "function"
        ? await askConfirm(T("Подтвердите действие", "Confirm the action"), `${it.title}\n\n${a.label}`, a.label)
        : window.confirm(`${it.title}\n\n${a.label}`);
      if (!ok) return;
      payload.confirm = true;
    }
    if (a.kind === "ui") { await call(`/api/pg/inbox/read`, { method: "POST", body: { ids: [it.id] } }).catch(() => {}); openTarget(it, a); return; }
    st.busy = `${it.id}:${actionId}`;
    renderList();
    try {
      const res = await call(`/api/pg/inbox/item/${encodeURIComponent(it.id)}/action`, { method: "POST", body: { action: actionId, payload } });
      if (res.need_confirm && !payload.confirm) {
        st.busy = "";
        return run(it, actionId, { ...payload, confirm: true, _forced: true }).catch(() => {});
      }
      if (res.ok === false) { if (typeof toast === "function") toast(res.error || T("Не получилось", "Failed"), "err"); }
      else if (res.open !== undefined) openTarget(it, a);
    } catch (error) {
      if (typeof toast === "function") toast(error.message, "err");
    } finally {
      st.busy = "";
      await load();
      renderList();
    }
  }

  function openTarget(it, a) {
    const ref = it.ref || {};
    const src = it.source;
    if (a && a.url) { window.open(a.url, "_blank", "noopener"); return; }
    if (src === "dfy") {
      const chat = ref.chat_id && typeof S !== "undefined" && (S.chats || []).find((c) => c.id === ref.chat_id);
      if (chat && typeof openChat === "function") { closeIfOpen(); openChat(ref.chat_id); return; }
      st.expanded.add(it.id); renderList();     // the live task card opens right here
      return;
    }
    if (src === "memory" && window.MemuMemory) { window.MemuMemory.open({ tab: "inbox", root: true }); return; }
    if (["schedule", "flows", "triggers"].includes(src)) {
      if (window.MemuPlan && window.MemuPlan.open) window.MemuPlan.open(); else $q("#nav-sched")?.click();
      return;
    }
    if ((src === "models" || src === "modules") && window.MemuModules && ref.module_id) { closeIfOpen(); window.MemuModules.open(ref.module_id); return; }
    if (src === "proxy") {
      call("/api/pg/launch_proxy", { method: "POST" }).then((r) => {
        if (typeof toast === "function") toast(r && r.ok === false ? (r.error || T("Не удалось открыть Proxy", "Could not open Proxy")) : T("Открываю MemuAI Proxy…", "Opening MemuAI Proxy…"), r && r.ok === false ? "err" : "info");
      }).catch((e) => typeof toast === "function" && toast(e.message, "err"));
      return;
    }
    if (ref.chat_id && typeof openChat === "function") { closeIfOpen(); openChat(ref.chat_id); return; }
    st.expanded.add(it.id); renderList();
  }
  function closeIfOpen() { if (isOpen() && typeof closePanel === "function") closePanel(); }

  async function setRead(ids, read = true) {
    await call("/api/pg/inbox/read", { method: "POST", body: { ids, read } }).catch(() => {});
    st.items.forEach((it) => { if (ids.includes(it.id)) it.read = read; });
  }

  /* ---------------------------------------------------------------- events */
  function bindShell() {
    const root = $q("#ibx-root");
    const q = $q("#ibx-q");
    q.addEventListener("input", () => { st.q = q.value; renderList(); });
    q.addEventListener("keydown", (e) => { if (e.key === "ArrowDown") { e.preventDefault(); moveSel(1); focusList(); } });
    q.value = st.q;
    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKey);
  }
  async function onClick(e) {
    const t = e.target;
    const tab = t.closest("[data-group]");
    if (tab) { st.group = tab.dataset.group; if (st.group === "done") st.showDone = true; renderList(); return; }
    const chip = t.closest("[data-source]");
    if (chip) { st.source = chip.dataset.source; renderList(); return; }
    const top = t.closest("[data-act]");
    const art = t.closest(".ibx-item");
    const it = art && st.items.find((x) => x.id === art.dataset.id);
    if (top && !art) {
      const act = top.dataset.act;
      if (act === "read-all") { await call("/api/pg/inbox/read", { method: "POST", body: { all: true } }).catch(() => {}); await load(); renderList(); }
      else if (act === "toggle-done") { st.showDone = !st.showDone; renderList(); }
      else if (act === "clear-done") { await call("/api/pg/inbox/clear-done", { method: "POST", body: {} }).catch(() => {}); await load(); renderList(); }
      return;
    }
    if (!it) return;
    st.sel = it.id;
    const snz = t.closest("[data-snooze]");
    if (snz) { st.menu = ""; await snooze(it, snz.dataset.snooze); return; }
    const runBtn = t.closest("[data-run]");
    if (runBtn) { e.stopPropagation(); run(it, runBtn.dataset.run); return; }
    if (top) {
      const act = top.dataset.act;
      if (act === "toggle") { toggle(it); return; }
      if (act === "snooze-menu") { st.menu = st.menu === it.id ? "" : it.id; renderList(); return; }
      if (act === "read") { await setRead([it.id], !it.read); renderList(); return; }
      if (act === "dismiss") { await dismiss(it); return; }
      if (act === "form-cancel") { st.form = null; renderList(); return; }
      return;
    }
    if (t.closest(".ibx-form, .ibx-dfy, .au-card, a, input, textarea, select")) return;
    toggle(it);
  }
  function toggle(it) {
    if (st.expanded.has(it.id)) st.expanded.delete(it.id);
    else { st.expanded.add(it.id); if (!it.read) setRead([it.id], true); }
    st.sel = it.id;
    renderList();
  }
  async function snooze(it, preset) {
    await call(`/api/pg/inbox/item/${encodeURIComponent(it.id)}/snooze`, { method: "POST", body: { preset } }).catch((e) => typeof toast === "function" && toast(e.message, "err"));
    await load(); renderList();
  }
  async function dismiss(it) {
    const idx = visible().findIndex((x) => x.id === it.id);
    await call(`/api/pg/inbox/item/${encodeURIComponent(it.id)}/dismiss`, { method: "POST", body: {} }).catch(() => {});
    await load();
    const rows = visible().filter((x) => x.status === "open");
    st.sel = (rows[Math.min(idx, rows.length - 1)] || {}).id || "";
    renderList();
  }

  function focusList() { const list = $q("#ibx-list"); if (list) list.focus({ preventScroll: true }); }
  function markSel(scroll = true) {
    $$q("#ibx-list .ibx-item").forEach((el) => {
      const on = el.dataset.id === st.sel;
      el.classList.toggle("sel", on);
      el.setAttribute("aria-selected", String(on));
      if (on && scroll) el.scrollIntoView({ block: "nearest" });
    });
    const list = $q("#ibx-list");
    if (list) { if (st.sel) list.setAttribute("aria-activedescendant", ""); }
  }
  function moveSel(delta) {
    const ids = $$q("#ibx-list .ibx-item").map((el) => el.dataset.id);
    if (!ids.length) return;
    const i = ids.indexOf(st.sel);
    st.sel = ids[i < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, i + delta))];
    markSel(true);
  }
  function onKey(e) {
    const t = e.target;
    if (t.closest && t.closest("input, textarea, select, .ibx-dfy, .au-card, [contenteditable]")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const it = st.items.find((x) => x.id === st.sel);
    const k = e.key.toLowerCase();
    if (k === "arrowdown" || k === "j") { e.preventDefault(); moveSel(1); }
    else if (k === "arrowup" || k === "k") { e.preventDefault(); moveSel(-1); }
    else if (k === "/") { e.preventDefault(); $q("#ibx-q")?.focus(); }
    else if (!it) return;
    else if ((k === "enter" || k === " ") && t.closest("#ibx-list") && !t.closest("button")) { e.preventDefault(); toggle(it); }
    else if (k === "a" || k === "ф") {
      const primary = (it.actions || []).find((a) => a.style === "primary") || (it.actions || [])[0];
      if (primary && it.status === "open") { e.preventDefault(); run(it, primary.id); }
    }
    else if ((k === "e" || k === "у" || k === "delete") && it.status === "open") { e.preventDefault(); dismiss(it); }
    else if ((k === "s" || k === "ы") && it.status === "open") { e.preventDefault(); st.menu = it.id; renderList(); $q(".ibx-menu button")?.focus(); }
    else if (k === "u" || k === "г") { e.preventDefault(); setRead([it.id], !it.read).then(renderList); }
  }

  /* ---------------------------------------------------------------- boot */
  function boot() {
    ensureButton();
    document.addEventListener("click", (e) => {
      if (st.menu && !(e.target.closest && e.target.closest(".ibx-snooze-wrap"))) { st.menu = ""; renderList(); }
    }, { capture: true });
    refreshCounts().then(() => { st.lastAttention = Number(st.counts.ask || 0) + Number(st.counts.confirm || 0); });
    // r36: the live stream opens a few seconds after the window has loaded — the first count comes from
    // refreshCounts() above, and startup (and «network idle») is not held up by a never-ending request
    const later = () => setTimeout(connect, 3000);
    if (document.readyState === "complete") later(); else window.addEventListener("load", later, { once: true });
    document.addEventListener("visibilitychange", onVisibility);
    const relabel = () => { labelButton(); if (isOpen()) { const q = st.q; panel(shell(), "ibx-panel", { root: true }); bindShell(); st.q = q; renderList(); } };
    document.addEventListener("memu:lang", relabel);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else setTimeout(boot, 0);

  window.MemuInbox = {
    open, refresh: () => (isOpen() ? load().then(renderList) : refreshCounts()),
    post: (item) => call("/api/pg/inbox/post", { method: "POST", body: item || {} }),
    get counts() { return { ...st.counts }; },
    get live() { return !!st.live; },
  };
})();
