

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const reduceMotion = () => document.documentElement.dataset.motion === "off" || document.documentElement.dataset.motion === "reduced" ||
    (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const IC = {
    bolt: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.2 2.4 4.6 13.3c-.4.5 0 1.2.6 1.2h5.3l-1.3 7.1c-.1.7.8 1.1 1.2.5l8.6-10.9c.4-.5 0-1.2-.6-1.2h-5.3l1.3-7.1c.1-.7-.8-1.1-1.2-.5z"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 3v8"/><path d="M6.6 6.6a8 8 0 1 0 10.8 0"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>',
    undo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg>',
    timer: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2M9 2h6"/></svg>',
    alarm: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2 1.5M4 4 2 6M20 4l2 2"/></svg>',
    bell: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
    tomato: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="13.5" r="7.5"/><path d="M12 6c-1-2-3-2.5-4-2M12 6c1-2 3-2.5 4-2M12 6V3.5"/></svg>',
    stopwatch: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="14" r="7"/><path d="M12 14V10M10 3h4M18 7l1.5-1.5"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/></svg>',
    play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.9l10-6.5a1 1 0 0 0 0-1.7l-10-6.5A1 1 0 0 0 8 5.5z"/></svg>',
    skip: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M5 6v12l9-6zM15 6h3v12h-3z"/></svg>',
    flag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5 21V4M5 4h11l-2 4 2 4H5"/></svg>',
    reset: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12a8 8 0 1 0 2.3-5.7L4 8.6M4 4v4.6h4.6"/></svg>',
    mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>',
    speaker: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/></svg>',
    speakerOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z"/><path d="m16 9.5 5 5M21 9.5l-5 5"/></svg>',
    open: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  };
  const CAT_ICON = {
    apps: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/></svg>',
    web: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.8 3.9 5.8 3.9 9s-1.3 6.2-3.9 9c-2.6-2.8-3.9-5.8-3.9-9s1.3-6.2 3.9-9z"/></svg>',
    sound: IC.speaker,
    display: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    system: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
    power: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 3v9"/><path d="M6.3 6.8a8 8 0 1 0 11.4 0"/></svg>',
    windows: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M12 4v16M3 9h18"/></svg>',
    time: IC.timer,
    memuai: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6L4.5 10l5.6-1.9z"/></svg>',
    assistant: IC.bolt,
    tools: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8V20h3.2l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z"/></svg>',
  };
  const ACTION_CAT = {};

  const S = {
    settings: null, enabled: true, cards: [], seq: 0, timers: null, serverSkew: 0, open: false,
    pollTimer: 0, tickTimer: 0, bypass: "", busyCommand: false, countdowns: new Map(), seen: new Set(),
  };
  const lang = () => Q.lang();

  let stripEl = null, cardsEl = null;
  Q.registerPanel("commands-timers", (el) => { stripEl = el; el.classList.add("mqc-strip-host"); return { show() {}, hide() {} }; }, { slot: "top", order: 1, hidden: true });
  Q.registerPanel("commands-cards", (el) => { cardsEl = el; el.classList.add("mqc-cards-host"); return {}; }, { slot: "top", order: 2, hidden: true });

  let dfyEl = null;
  Q.registerPanel("commands-dfy", (el) => { dfyEl = el; el.classList.add("mqc-dfy-host"); return {}; }, { slot: "top", order: 3, hidden: true });

  async function loadState() {
    try {
      const data = await Q.api("/api/assistant/state?after=" + S.seq);
      S.settings = data.settings; S.enabled = !!(data.settings && data.settings.enabled);
      applyTimers(data.timers);
      ingestEvents(data.events || [], data.seq);
      Voice.configure(data.settings && data.settings.voice);
    } catch (e) {  S.enabled = false; }
  }
  function ingestEvents(events, seq) {
    if (typeof seq === "number") S.seq = Math.max(S.seq, seq);
    for (const card of events) {
      if (card.seq) S.seq = Math.max(S.seq, card.seq);
      if (card.action === "timer.ring") { refreshTimers(); continue; }
      if (S.seen.has(card.id)) continue;
      pushCard(card);
    }
  }
  async function poll() {
    clearTimeout(S.pollTimer);
    if (!S.open) return;
    try {
      const data = await Q.api("/api/assistant/state?after=" + S.seq);
      applyTimers(data.timers);
      ingestEvents(data.events || [], data.seq);
    } catch (e) {  }
    const active = S.timers && ((S.timers.timers || []).length || (S.timers.ringing || []).length ||
      (S.timers.pomodoro && S.timers.pomodoro.active) || (S.timers.stopwatch && S.timers.stopwatch.running));
    S.pollTimer = setTimeout(poll, active ? 2000 : 4000);
  }
  async function refreshTimers() {
    try { applyTimers((await Q.api("/api/assistant/state?after=" + S.seq)).timers); } catch (e) {  }
  }

  Q.on("open", () => { S.open = true; loadState().then(poll); startTick(); });
  Q.on("close", () => { S.open = false; clearTimeout(S.pollTimer); stopTick(); Voice.stopAll(); });
  Q.on("chat-new", () => { S.cards = []; renderCards(); });
  Q.on("plugins-ready", () => { S.open = true; loadState().then(poll); startTick(); });

  const PAGE_ASK = /(сам+ари|резюм|перескаж|кратко|суть|главное|о ч[её]м|суммариз|summar|tl;?dr|key points|gist)/i;
  const PAGE_NOUN = /(страниц|стать|вкладк|сайт|\b(?:page|article|tab|site)\b)/i;
  Q.on("submit", (e) => {
    const text = String(e.text || "").trim();
    if (S.bypass && S.bypass === text) { S.bypass = ""; return; }
    Voice.stopSpeaking(true);
    if (!S.enabled || !text || e.images || e.selection || e.page || text.length > 300 || text.includes("\n")) return;

    if (PAGE_ASK.test(text) && PAGE_NOUN.test(text)) return;
    if (S.settings && S.settings.local_match === false) return;
    e.preventDefault();
    runText(text);
  });

  function forwardToModel(text) {
    S.bypass = text;
    Q.send(text);
  }

  function dropStaleConfirms() {
    const stale = S.cards.filter((c) => c.status === "confirm" && c.confirm && c.confirm.token);
    if (!stale.length) return;
    for (const card of stale) {
      const cd = S.countdowns.get(card.confirm.token);
      if (cd) { clearInterval(cd.timer); S.countdowns.delete(card.confirm.token); }
      Q.api("/api/assistant/confirm", { body: { token: card.confirm.token, accept: false, lang: lang() } }).catch(() => {});
    }
    S.cards = S.cards.filter((c) => !stale.includes(c));
  }

  async function runText(text) {
    dropStaleConfirms();
    const pending = { id: "p" + Date.now(), status: "running", title: t("Выполняю…", "Working…"), message: text, action: "" };

    const showPending = setTimeout(() => pushCard(pending, true), 280);
    document.documentElement.dataset.qaCmdCheck = "1";
    let data = null;

    const inChat = !!((Q.state && Q.state().turns) || []).length;

    const slow = setTimeout(() => { if (S.cards.some((c) => c.id === pending.id)) { pending.title = t("Разбираю команду…", "Understanding the command…"); renderCards(); } }, 450);
    try { data = await Q.api("/api/assistant/run", { body: { text, lang: lang(), in_chat: inChat } }); }
    catch (e) { data = null; }
    clearTimeout(slow); clearTimeout(showPending);
    delete document.documentElement.dataset.qaCmdCheck;
    removeCard(pending.id);
    if (!data || !data.card) { forwardToModel(text); return; }
    if (data.card.fallback) { forwardToModel(text); return; }
    if (!data.card.id) data.card.id = "c" + Date.now().toString(36);

    if (data.match && data.match.layout) data.card.layout = String(data.match.layout).slice(0, 120);
    if (data.card.status === "confirm") data.card.ask = text;
    pushCard(data.card);
    recordResult(text, data.card);
    afterCard(data.card);
  }

  function loadDfyClient() {
    if (window.MemuDFY) return Promise.resolve(window.MemuDFY);
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/dfy-client.js"; s.async = true;
      s.onload = () => (window.MemuDFY ? resolve(window.MemuDFY) : reject(new Error("MemuDFY missing")));
      s.onerror = () => reject(new Error(t("модуль «Сделай за меня» недоступен", "the Do-For-You module is unavailable")));
      document.head.appendChild(s);
    });
  }
  async function startDfy(card) {
    if (!card || card._dfyStarted) return;
    card._dfyStarted = true;
    const goal = (card.client && card.client.goal) || "";

    if (window.QuickDFY && typeof window.QuickDFY.run === "function") {
      removeCard(card.id);
      try { await window.QuickDFY.run(goal); } catch (e) { Q.toast(String(e.message || e), "err"); }
      return;
    }
    const turns = ((Q.state && Q.state().turns) || []).slice(-6);
    const context = [];
    turns.forEach((x) => { if (x.q) context.push({ role: "user", content: String(x.q).slice(0, 4000) }); if (x.a) context.push({ role: "assistant", content: String(x.a).slice(0, 4000) }); });
    try {
      const D = await loadDfyClient();
      const st = D.status ? await D.status().catch(() => null) : null;
      if (st && st.enabled === false) throw new Error(st.reason || t("«Сделай за меня» выключено в настройках", "Do-For-You is turned off in Settings"));
      if (st && st.ready === false) throw new Error(st.reason || t("агент DFY не готов (модель с Vision не выбрана)", "the DFY agent is not ready (no vision model)"));
      const task = await D.start(goal, context, "quick");
      if (!task || !task.id) throw new Error(t("задача не создана", "the task was not created"));
      removeCard(card.id);
      if (dfyEl) {
        const box = document.createElement("div"); box.className = "mqc-dfy-task"; box.dataset.dfy = task.id;
        dfyEl.prepend(box);
        while (dfyEl.children.length > 2) dfyEl.lastElementChild.remove();
        Q.showPanel("commands-dfy");
        if (D.renderCard) D.renderCard(box, task.id);
        else box.textContent = t("Задача запущена: ", "Task started: ") + goal;
        Q.resize();
      }
    } catch (e) {
      card.status = "error"; card.title = t("Сделай за меня", "Do it for me");
      card.message = t("Не удалось запустить: ", "Could not start: ") + (e.message || e);
      renderCards();
    }
  }

  async function startComputer(card) {

    if (window.QuickComputer) { removeCard(card && card.id); window.QuickComputer.start(card); return; }
    if (!card || card._cuStarted || !dfyEl) return;
    card._cuStarted = true;
    const c = card.client || {};
    const turns = ((Q.state && Q.state().turns) || []).slice(-4).map((x) => (x.q ? "User: " + x.q + "\n" : "") + (x.a ? "Assistant: " + x.a : "")).join("\n");
    const box = document.createElement("div"); box.className = "mqc-dfy-task mqc-cu";
    dfyEl.prepend(box); while (dfyEl.children.length > 2) dfyEl.lastElementChild.remove();
    Q.showPanel("commands-dfy"); removeCard(card.id);
    const draw = (st) => {
      const status = st.status || "starting";
      const logs = (st.logs || []).slice(-4).map((l) => typeof l === "string" ? l : (l.text || l.message || JSON.stringify(l)));
      const pending = st.pending_action;
      const finished = ["done", "error", "cancelled"].includes(status);
      const label = { starting: t("запуск", "starting"), running: t("работает", "running"), waiting_approval: t("ждёт разрешения", "waiting for approval"),
        done: t("готово", "done"), error: t("ошибка", "error"), cancelled: t("отменено", "cancelled") }[status] || status;

      const report = status === "done" ? String(st.report || "") : "";
      const error = String(st.error || "");
      const showError = error && !logs.some((x) => String(x).includes(error.slice(0, 80)));
      const ask = pending ? String(pending.description || pending.action || pending.type || JSON.stringify(pending)).slice(0, 160) : "";
      box.innerHTML = `<article class="mqc-card mqc-${finished ? (status === "done" ? "done" : "error") : "running"}">
        <div class="mqc-card-head"><span class="mqc-ic">${finished ? (status === "done" ? IC.check : IC.x) : IC.bolt}</span>
          <span class="mqc-title">${t("Управление компьютером", "Computer use")} · ${esc(label)}</span></div>
        <div class="mqc-msg">${esc(c.task || "")}</div>
        ${logs.length ? `<ul class="mqc-list">${logs.map((x) => `<li>${esc(String(x).slice(0, 200))}</li>`).join("")}</ul>` : ""}
        ${report ? `<div class="mqc-msg mqc-pre">${esc(report)}</div>` : ""}${showError ? `<div class="mqc-msg">${esc(error)}</div>` : ""}
        <div class="mqc-actions">${pending && !finished ? `<span class="mqc-msg">${esc(ask)}</span>
          <button type="button" class="mqc-btn mqc-btn-primary" data-cu="approve">${t("Разрешить", "Allow")}</button>
          <button type="button" class="mqc-btn" data-cu="reject">${t("Отклонить", "Reject")}</button>` : ""}
          ${finished ? `<button type="button" class="mqc-btn" data-cu="close">${t("Скрыть", "Dismiss")}</button>` : `<button type="button" class="mqc-btn mqc-btn-cancel" data-cu="stop">${t("Стоп", "Stop")}</button>`}</div></article>`;
      Q.resize();
    };
    box.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-cu]"); if (!b) return;
      if (b.dataset.cu === "close") { box.remove(); if (!dfyEl.children.length) Q.hidePanel("commands-dfy"); Q.resize(); return; }
      b.disabled = true;
      try { await Q.api("/api/work/computer/action", { body: { action: b.dataset.cu } }); } catch (e) { Q.toast(e.message || String(e), "err"); }
    });

    const record = (st) => {
      const a = st.status === "done" ? String(st.report || t("Готово", "Done")) : String(st.error || st.status || "");
      if (Q.recordTurn && a.trim()) Q.recordTurn({ q: card.ask || c.task || "", a: (st.status === "done" ? "" : t("Управление компьютером: ", "Computer use: ")) + a });
    };
    draw({ status: "starting" });
    try {
      await Q.api("/api/work/computer/run", { body: { source: "quick", project_id: c.project_id || undefined, confirmed: true, model: c.model, task: c.task,
        context: turns, max_steps: c.max_steps || 12, access_mode: "ask", ai_cursor: true, lang: lang() } });
    } catch (e) { const st = { status: "error", error: e.message || String(e) }; draw(st); record(st); return; }
    for (let i = 0; i < 1800 && box.isConnected; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      let st = null;
      try { st = await Q.api("/api/work/computer/state"); } catch (e) { continue; }
      draw(st);
      if (["done", "error", "cancelled"].includes(st.status)) { record(st); break; }
    }
  }

  function recordResult(text, card) {

    if (!Q.recordTurn || !card || card.client || ["running", "confirm"].includes(card.status)) return;

    const rows = (Array.isArray(card.details) ? card.details : []).filter((row) => Array.isArray(row) && row[1] != null && String(row[1]).trim())
      .slice(0, 8).map((row) => `- ${String(row[0] || "").trim()}: ${String(row[1]).trim()}`);
    const answer = [String(card.answer || card.message || card.title || "").trim(), rows.join("\n")].filter(Boolean).join("\n\n");
    if (answer) Q.recordTurn({ q: text, a: answer });
  }
  function afterCard(card) {
    if (!card) return;
    if (card.client && card.client.type === "dfy" && card.status !== "confirm") { startDfy(card); return; }
    if (card.client && card.client.type === "computer" && card.status !== "confirm") { startComputer(card); return; }
    if (card.speak && Voice.autoSpeak()) Voice.speak(card.speak);
    else if (card.answer && Voice.autoSpeak()) Voice.speak(card.answer);
    if (/^(timer|alarm|reminder|pomodoro|stopwatch)\./.test(card.action || "")) refreshTimers();
  }

  Q.on("tool-ui", (e) => {
    if (!e || e.tool !== "assistant_action" || !e.ui) return;
    pushCard(e.ui);
    if (/^(timer|alarm|reminder|pomodoro|stopwatch)\./.test(e.ui.action || "")) refreshTimers();
  });
  Q.on("answer-done", (e) => {
    if (e && e.text && !e.error && Voice.autoSpeak()) Voice.speak(e.text);

    Q.api("/api/assistant/events?after=" + S.seq).then((d) => ingestEvents(d.events || [], d.seq)).catch(() => {});
  });
  Q.on("answer-start", () => { Voice.stopSpeaking(true); });

  function pushCard(card, transient = false) {
    if (!card || !card.id) return;
    if (!transient) S.seen.add(card.id);
    S.cards = S.cards.filter((c) => c.id !== card.id && c.status !== "running");
    S.cards.unshift(card);
    S.cards = S.cards.slice(0, 4);
    renderCards();
  }
  function removeCard(id) { S.cards = S.cards.filter((c) => c.id !== id); renderCards(); }

  function fmtDuration(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
  }

  function richMessage(card) {
    const safe = esc(card.message);
    if (card.kind !== "youtube" && card.kind !== "text") return safe;
    return safe.replace(/\[([^\]\n]{1,80})\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, url) =>
      `<button type="button" class="mqc-ts" data-mqc="link" data-url="${url}">${label}</button>`)
      .replace(/^\s*(?:\*\*|##\s*)(.+?)(?:\*\*)?$/gm, "<b>$1</b>");
  }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
    try { const a = document.createElement("textarea"); a.value = text; a.style.position = "fixed"; a.style.left = "-9999px"; document.body.appendChild(a); a.select(); const ok = document.execCommand("copy"); a.remove(); return ok; } catch (e) { return false; }
  }

  function cardHtml(card) {
    const status = card.status || "done";
    const icon = status === "done" ? IC.check : status === "confirm" ? IC.warn : status === "running" ? IC.bolt :
      status === "cancelled" ? IC.x : status === "ringing" ? IC.bell : (status === "error" || status === "unsupported" || status === "disabled") ? IC.x : IC.bolt;
    const cat = card.action ? (card.action.split(".")[0]) : "";
    const parts = [];
    parts.push(`<div class="mqc-card-head"><span class="mqc-ic mqc-ic-${esc(status)}">${icon}</span>
      <span class="mqc-title">${esc(card.title || "")}</span>
      ${card.tier && card.tier !== "safe" && status === "confirm" ? `<span class="mqc-tier mqc-tier-${esc(card.tier)}">${card.tier === "dangerous" ? t("опасно", "dangerous") : t("подтвердите", "confirm")}</span>` : ""}
      ${card.source === "model" ? `<span class="mqc-src">${t("модель", "model")}</span>` : card.source === "router" ? `<span class="mqc-src mqc-src-ai" title="${esc(t("Команду понял ИИ", "Understood by AI") + (card.router && card.router.model ? " · " + card.router.model : "") + (card.router && card.router.ms ? " · " + (card.router.ms / 1000).toFixed(1) + " " + t("с", "s") : ""))}">${t("ИИ", "AI")}</span>` : card.source === "hotkey" ? `<span class="mqc-src">${t("клавиша", "hotkey")}</span>` : ""}
      <button type="button" class="mqc-x" data-mqc="close" aria-label="${t("Скрыть", "Dismiss")}">${IC.x}</button></div>`);
    if (card.layout) parts.push(`<div class="mqc-layout">${t("Понял как: «" + esc(card.layout) + "»", "Read as: “" + esc(card.layout) + "”")}</div>`);

    const swatch = /^#[0-9a-f]{6}$/i.test(String(card.swatch || "")) ? `<span class="mqc-swatch" style="background:${card.swatch}" aria-hidden="true"></span>` : "";
    if (card.message && card.kind !== "help" && card.message !== card.title) parts.push(`<div class="mqc-msg${card.kind === "text" || card.kind === "code" || card.kind === "youtube" ? " mqc-pre" : ""}${card.kind === "code" ? " mqc-code" : ""}">${swatch}${richMessage(card)}</div>`);
    if (card.kind === "level" && card.data && typeof card.data.level === "number") {
      parts.push(`<div class="mqc-level" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${card.data.level}"><i style="width:${Math.max(0, Math.min(100, card.data.level))}%"></i></div>`);
    }
    if (Array.isArray(card.details) && card.details.length) {
      parts.push(`<dl class="mqc-details">${card.details.map((row) => `<dt>${esc(row[0])}</dt><dd>${esc(row[1])}</dd>`).join("")}</dl>`);
    }

    if (card.plan && card.plan.next_label && Array.isArray(card.list) && card.list.length) parts.push(`<div class="mqc-plan-label">${esc(card.plan.next_label)}</div>`);
    if (Array.isArray(card.list) && card.list.length) {
      parts.push(`<ul class="mqc-list">${card.list.slice(0, 20).map((row) => `<li>${esc(row)}</li>`).join("")}</ul>`);
    }
    if (Array.isArray(card.warnings) && card.warnings.length) {
      parts.push(`<ul class="mqc-plan-warn">${card.warnings.map((w) => `<li>${IC.warn}<span>${esc(w)}</span></li>`).join("")}</ul>`);
    }
    if (card.kind === "help" && card.data && card.data.groups) {
      parts.push(`<div class="mqc-help">${card.data.groups.map((g) => `<div class="mqc-help-group"><b><span class="mqc-cat">${CAT_ICON[g.id] || ""}</span>${esc(g.title)}</b>${g.examples.map((x) => `<button type="button" class="mqc-chip" data-mqc="try" data-text="${esc(x)}">${esc(x)}</button>`).join("")}</div>`).join("")}
        ${(card.data.commands || []).length ? `<div class="mqc-help-group"><b>${t("Ваши команды", "Your commands")}</b>${card.data.commands.map((x) => `<button type="button" class="mqc-chip" data-mqc="try" data-text="${esc(x)}">${esc(x)}</button>`).join("")}</div>` : ""}</div>`);
    }
    if (Array.isArray(card.steps) && card.steps.length) {
      parts.push(`<ol class="mqc-steps">${card.steps.map((s) => `<li class="mqc-step-${esc(s.status)}"><span>${s.status === "done" ? IC.check : s.status === "skipped" ? "–" : s.status === "partial" ? IC.warn : IC.x}</span><b>${esc(s.title || s.type)}</b> ${esc(s.message || "")}</li>`).join("")}</ol>`);
    }
    if (Array.isArray(card.images) && card.images.length) {
      parts.push(`<div class="mqc-images">${card.images.map((u) => `<img src="${esc(u)}" alt="" loading="lazy" data-mqc="image" data-url="${esc(u)}">`).join("")}</div>`);
    }
    if (card.answer) parts.push(`<div class="mqc-answer">${esc(card.answer)}</div>`);
    const buttons = [];
    if (status === "confirm" && card.confirm) {
      const cd = S.countdowns.get(card.confirm.token);
      if (cd) {
        buttons.push(`<span class="mqc-countdown" aria-live="assertive">${t("Через", "In")} <b>${cd.left}</b> ${t("с", "s")}</span>`);
        buttons.push(`<button type="button" class="mqc-btn mqc-btn-cancel" data-mqc="abort" data-token="${esc(card.confirm.token)}">${t("Отменить", "Cancel")}</button>`);
      } else {
        buttons.push(`<button type="button" class="mqc-btn ${card.confirm.tier === "dangerous" ? "mqc-btn-danger" : "mqc-btn-primary"}" data-mqc="confirm" data-token="${esc(card.confirm.token)}" data-countdown="${+card.confirm.countdown || 0}">${card.confirm.tier === "dangerous" ? t("Да, выполнить", "Yes, do it") : card.kind === "plan" ? t("Создать задачу", "Create the task") : t("Выполнить", "Run")}</button>`);
        if (card.kind === "plan" && card.plan) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="plan-edit">${IC.open}<span>${t("Изменить в MemuAI", "Edit in MemuAI")}</span></button>`);
        buttons.push(`<button type="button" class="mqc-btn" data-mqc="reject" data-token="${esc(card.confirm.token)}">${t("Отмена", "Cancel")}</button>`);
      }
    }

    if (status === "done" && card.next && card.next.action) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="next">${IC.power}<span>${esc(card.next.label || t("Продолжить", "Continue"))}</span></button>`);
    if (status === "done" && card.plan_id) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="plan-open">${IC.open}<span>${t("Открыть «Планирование»", "Open Planning")}</span></button>`);
    if (status === "done" && card.undo) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="undo" data-undo="${esc(card.undo)}">${IC.undo}<span>${t("Отменить", "Undo")}</span></button>`);
    if (status === "done" && card.copy) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="copy">${t("Копировать", "Copy")}</button>`);
    if (status === "done" && card.kind === "youtube" && card.youtube) {
      buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="yt" data-mode="timestamps" data-url="${esc(card.youtube.url)}">${t("Таймкоды", "Timestamps")}</button>`);
      buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="yt" data-mode="detailed" data-url="${esc(card.youtube.url)}">${t("Подробно", "Detailed")}</button>`);
      buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="yt-ask" data-url="${esc(card.youtube.url)}">${t("Спросить по видео", "Ask about it")}</button>`);
    }
    if (card.open_path) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="openpath" data-path="${esc(card.open_path)}">${IC.open}<span>${t("Открыть", "Open")}</span></button>`);
    if (Array.isArray(card.links)) for (const link of card.links.slice(0, 2)) if (link && link.url) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="link" data-url="${esc(link.url)}" title="${esc(link.url)}">${IC.open}<span>${esc((link.label || link.url).replace(/^https?:\/\//, "").slice(0, 34))}</span></button>`);
    if (card.code === "advanced_off" || card.code === "disabled") buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="settings">${IC.gear}<span>${t("Настройки", "Settings")}</span></button>`);
    if ((status === "error" || status === "unsupported") && card.retry_text) buttons.push(`<button type="button" class="mqc-btn mqc-btn-ghost" data-mqc="ask" data-text="${esc(card.retry_text)}">${t("Спросить модель", "Ask the model")}</button>`);
    if (buttons.length) parts.push(`<div class="mqc-actions">${buttons.join("")}</div>`);
    return `<article class="mqc-card mqc-${esc(status)}${cat ? " mqc-cat-" + esc(cat) : ""}" data-id="${esc(card.id)}">${parts.join("")}</article>`;
  }

  function renderCards() {
    if (!cardsEl) return;
    cardsEl.innerHTML = S.cards.length ? `<div class="mqc-cards">${S.cards.map(cardHtml).join("")}</div>` : "";
    if (S.cards.length) Q.showPanel("commands-cards"); else Q.hidePanel("commands-cards");

    if (S.cards.length) document.documentElement.dataset.mqcCards = "1"; else delete document.documentElement.dataset.mqcCards;
    Q.resize();
  }

  document.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-mqc]");
    if (!btn || !btn.closest(".mqc-cards-host, .mqc-strip-host")) return;
    const kind = btn.dataset.mqc;
    const cardEl = btn.closest(".mqc-card");
    const card = cardEl ? S.cards.find((c) => c.id === cardEl.dataset.id) : null;
    if (kind === "close" && card) { removeCard(card.id); return; }
    if (kind === "try") { Q.setInput(btn.dataset.text); return; }
    if (kind === "ask") { forwardToModel(btn.dataset.text); return; }
    if (kind === "settings") { Q.api("/api/quick/window", { body: { action: "open_settings" } }).catch(() => {}); return; }

    if (kind === "plan-edit" || kind === "plan-open") {
      const plan = kind === "plan-edit" && card && card.plan ? { task: card.plan.task } : { open: true };
      Q.api("/api/quick/window", { body: { action: "handoff", handoff: { plan } } }).catch((e) => Q.toast(e.message, "err"));
      if (kind === "plan-edit" && card) removeCard(card.id);
      return;
    }
    if (kind === "link") { runAction("open.url", { url: btn.dataset.url }, true); return; }
    if (kind === "copy" && card) { const ok = await copyText(String(card.copy)); Q.toast(ok ? t("Скопировано", "Copied") : t("Не удалось скопировать", "Copy failed"), ok ? "" : "err"); return; }
    if (kind === "yt") { runAction("youtube.analyze", { url: btn.dataset.url, mode: btn.dataset.mode }); return; }
    if (kind === "yt-ask") { Q.setInput(btn.dataset.url + " "); return; }
    if (kind === "openpath") { runAction("open.path", { path: btn.dataset.path }, true); return; }
    if (kind === "next" && card && card.next) { const next = card.next; card.next = null; removeCard(card.id); runAction(next.action, next.params || {}); return; }
    if (kind === "image") { runAction("open.url", { url: new URL(btn.dataset.url, location.href).href }, true); return; }
    if (kind === "undo") {
      btn.disabled = true;
      try { const data = await Q.api("/api/assistant/undo", { body: { undo: btn.dataset.undo, lang: lang() } }); if (card) card.undo = ""; pushCard(data.card); }
      catch (e) { Q.toast(e.message, "err"); }
      return;
    }
    if (kind === "confirm") {
      const seconds = +btn.dataset.countdown || 0;
      if (seconds > 0) startCountdown(btn.dataset.token, seconds, card);
      else confirmToken(btn.dataset.token, true, card);
      return;
    }
    if (kind === "abort") { const cd = S.countdowns.get(btn.dataset.token); if (cd) { clearInterval(cd.timer); S.countdowns.delete(btn.dataset.token); } confirmToken(btn.dataset.token, false, card); return; }
    if (kind === "reject") { confirmToken(btn.dataset.token, false, card); return; }

    const id = btn.dataset.id || "";
    if (kind === "t-pause") return timerOp({ op: "pause", id });
    if (kind === "t-resume") return timerOp({ op: "resume", id });
    if (kind === "t-cancel") return timerOp({ op: "cancel", id });
    if (kind === "t-dismiss") return timerOp({ op: "dismiss", id });
    if (kind === "t-snooze") return timerOp({ op: "snooze", id, minutes: 5 });
    if (kind === "p-pause") return timerOp({ op: "pomodoro", action: "pause" });
    if (kind === "p-resume") return timerOp({ op: "pomodoro", action: "resume" });
    if (kind === "p-skip") return timerOp({ op: "pomodoro", action: "skip" });
    if (kind === "p-stop") return timerOp({ op: "pomodoro", action: "stop" });
    if (kind === "s-stop") return timerOp({ op: "stopwatch", action: "stop" });
    if (kind === "s-start") return timerOp({ op: "stopwatch", action: "start" });
    if (kind === "s-reset") return timerOp({ op: "stopwatch", action: "reset" });
    if (kind === "s-lap") return timerOp({ op: "stopwatch", action: "lap" });
  });

  function startCountdown(token, seconds, card) {
    const entry = { left: seconds, timer: 0 };
    S.countdowns.set(token, entry);
    renderCards();
    entry.timer = setInterval(() => {
      entry.left -= 1;
      if (entry.left <= 0) {
        clearInterval(entry.timer);
        S.countdowns.delete(token);
        confirmToken(token, true, card);
      } else renderCards();
    }, 1000);
  }

  async function confirmToken(token, accept, card) {
    const before = card ? { status: card.status, title: card.title } : null;
    if (card) { card.status = "running"; card.title = accept ? t("Выполняю…", "Working…") : card.title; renderCards(); }
    try {
      const data = await Q.api("/api/assistant/confirm", { body: { token, accept, lang: lang() } });
      if (card) removeCard(card.id);
      if (card && card.ask && data.card) data.card.ask = card.ask;
      pushCard(data.card);

      if (card && card.ask) recordResult(card.ask, data.card);
      afterCard(data.card);
    } catch (e) {

      if (card && before) { card.status = before.status; card.title = before.title; renderCards(); }
      Q.toast(e.message, "err");
    }
  }

  async function runAction(action, params, quiet = false) {
    try {
      const data = await Q.api("/api/assistant/run", { body: { action, params, lang: lang() } });
      if (!quiet || (data.card && data.card.status !== "done")) pushCard(data.card);
      if (data.card && data.card.client) afterCard(data.card);
      return data.card;
    } catch (e) { Q.toast(e.message, "err"); return null; }
  }

  function applyTimers(snapshot) {
    if (!snapshot) return;
    S.timers = snapshot;
    S.serverSkew = (snapshot.now || Date.now() / 1000) - Date.now() / 1000;
    renderStrip();
  }
  async function timerOp(body) {
    try { const data = await Q.api("/api/assistant/timers", { body }); applyTimers(data.timers); }
    catch (e) { Q.toast(e.message, "err"); }
  }
  const nowSec = () => Date.now() / 1000 + S.serverSkew;
  function startTick() { stopTick(); S.tickTimer = setInterval(renderStripTimes, 500); }
  function stopTick() { clearInterval(S.tickTimer); S.tickTimer = 0; }

  function remainingOf(item) {
    if (item.paused) return item.paused_remaining != null ? item.paused_remaining : item.remaining;
    return Math.max(0, item.ends_at - nowSec());
  }
  function pomoName(phase) {
    return { work: t("Работа", "Focus"), short_break: t("Перерыв", "Break"), long_break: t("Длинный перерыв", "Long break") }[phase] || phase;
  }
  function renderStrip() {
    if (!stripEl) return;
    const snap = S.timers || {};
    const rows = [];
    for (const ring of (snap.ringing || []).slice(-2)) {
      const title = ring.kind === "pomodoro" ? (ring.ended === "work" ? t("Pomodoro: перерыв!", "Pomodoro: break time!") : t("Pomodoro: за работу!", "Pomodoro: back to work!"))
        : ring.kind === "reminder" ? t("Напоминание", "Reminder") : ring.kind === "alarm" ? t("Будильник", "Alarm") : t("Таймер", "Timer");
      const text = ring.kind === "reminder" ? (ring.text || ring.label) : ring.kind === "pomodoro" ? "" : (ring.label || t("Время вышло", "Time is up"));
      rows.push(`<div class="mqc-ring${reduceMotion() ? "" : " mqc-anim"}" role="alert"><span class="mqc-ring-ic">${ring.kind === "pomodoro" ? IC.tomato : IC.bell}</span>
        <div class="mqc-ring-text"><b>${esc(title)}</b>${text ? `<span>${esc(text)}</span>` : ""}${ring.late ? `<small>${t("пока MemuAI был закрыт", "while MemuAI was closed")}</small>` : ""}</div>
        ${ring.kind !== "pomodoro" ? `<button type="button" class="mqc-btn" data-mqc="t-snooze" data-id="${esc(ring.id)}">${t("+5 мин", "+5 min")}</button>` : ""}
        <button type="button" class="mqc-btn mqc-btn-primary" data-mqc="t-dismiss" data-id="${esc(ring.id)}">${t("Стоп", "Stop")}</button></div>`);
    }
    const chips = [];
    for (const item of snap.timers || []) {
      const icon = item.kind === "alarm" ? IC.alarm : item.kind === "reminder" ? IC.bell : IC.timer;
      const label = item.label || (item.kind === "alarm" ? t("Будильник", "Alarm") : item.kind === "reminder" ? t("Напоминание", "Reminder") : t("Таймер", "Timer"));
      const total = item.kind === "timer" ? Math.max(1, item.duration || 1) : 0;
      const value = item.kind === "timer" ? `<b class="mqc-time" data-ends="${item.ends_at}" data-paused="${item.paused ? item.remaining : ""}">${fmtDuration(remainingOf(item))}</b>`
        : `<b class="mqc-time-abs">${new Date(item.ends_at * 1000).toLocaleTimeString(lang() === "en" ? "en-GB" : "ru-RU", { hour: "2-digit", minute: "2-digit" })}</b>`;
      chips.push(`<div class="mqc-chip-timer${item.paused ? " paused" : ""}" data-kind="${esc(item.kind)}" ${total ? `style="--p:${(1 - remainingOf(item) / total).toFixed(3)}" data-total="${total}"` : ""}>
        <span class="mqc-tic">${icon}</span><span class="mqc-tlabel" title="${esc(item.text || label)}">${esc(label)}</span>${value}
        ${item.kind === "timer" ? `<button type="button" class="mqc-mini" data-mqc="${item.paused ? "t-resume" : "t-pause"}" data-id="${esc(item.id)}" aria-label="${item.paused ? t("Продолжить", "Resume") : t("Пауза", "Pause")}">${item.paused ? IC.play : IC.pause}</button>` : ""}
        <button type="button" class="mqc-mini" data-mqc="t-cancel" data-id="${esc(item.id)}" aria-label="${t("Удалить", "Remove")}">${IC.x}</button></div>`);
    }
    const pomo = snap.pomodoro || {};
    if (pomo.active) {
      const waiting = pomo.waiting;
      chips.push(`<div class="mqc-chip-timer mqc-pomo phase-${esc(pomo.phase)}${pomo.paused_remaining != null ? " paused" : ""}" style="--p:${pomo.total ? (1 - (pomo.remaining || 0) / pomo.total).toFixed(3) : 0}" data-total="${pomo.total || 0}">
        <span class="mqc-tic">${IC.tomato}</span><span class="mqc-tlabel">${esc(pomoName(pomo.phase))} · ${pomo.cycle || 1}/${pomo.cycles || 4}</span>
        ${waiting ? `<button type="button" class="mqc-btn mqc-btn-primary mqc-btn-sm" data-mqc="p-resume">${t("Начать", "Start")}</button>`
          : `<b class="mqc-time" data-ends="${pomo.ends_at}" data-paused="${pomo.paused_remaining != null ? pomo.paused_remaining : ""}">${fmtDuration(pomo.paused_remaining != null ? pomo.paused_remaining : Math.max(0, pomo.ends_at - nowSec()))}</b>
             <button type="button" class="mqc-mini" data-mqc="${pomo.paused_remaining != null ? "p-resume" : "p-pause"}" aria-label="${t("Пауза", "Pause")}">${pomo.paused_remaining != null ? IC.play : IC.pause}</button>`}
        <button type="button" class="mqc-mini" data-mqc="p-skip" aria-label="${t("Следующая фаза", "Skip")}">${IC.skip}</button>
        <button type="button" class="mqc-mini" data-mqc="p-stop" aria-label="${t("Остановить", "Stop")}">${IC.x}</button></div>`);
    }
    const sw = snap.stopwatch || {};
    if (sw.running || sw.value > 0) {
      chips.push(`<div class="mqc-chip-timer mqc-sw${sw.running ? "" : " paused"}">
        <span class="mqc-tic">${IC.stopwatch}</span><b class="mqc-time" data-sw-start="${sw.running ? (sw.started_at || 0) : ""}" data-sw-base="${sw.elapsed || 0}">${fmtDuration(sw.value || 0)}</b>
        ${(sw.laps || []).length ? `<span class="mqc-laps">${t("круг", "lap")} ${(sw.laps || []).length}</span>` : ""}
        <button type="button" class="mqc-mini" data-mqc="${sw.running ? "s-lap" : "s-start"}" aria-label="${sw.running ? t("Круг", "Lap") : t("Продолжить", "Resume")}">${sw.running ? IC.flag : IC.play}</button>
        ${sw.running ? `<button type="button" class="mqc-mini" data-mqc="s-stop" aria-label="${t("Стоп", "Stop")}">${IC.pause}</button>` : `<button type="button" class="mqc-mini" data-mqc="s-reset" aria-label="${t("Сбросить", "Reset")}">${IC.reset}</button>`}</div>`);
    }
    if (chips.length) rows.push(`<div class="mqc-strip">${chips.join("")}</div>`);
    stripEl.innerHTML = rows.join("");
    if (rows.length) Q.showPanel("commands-timers"); else Q.hidePanel("commands-timers");
    Q.resize();
  }
  function renderStripTimes() {
    if (!stripEl || !stripEl.isConnected) return;
    let due = false;
    stripEl.querySelectorAll(".mqc-time").forEach((el) => {
      let value;
      if (el.dataset.swStart !== undefined) {
        const start = +el.dataset.swStart || 0;
        value = (+el.dataset.swBase || 0) + (start ? nowSec() - start : 0);
      } else if (el.dataset.paused) value = +el.dataset.paused;
      else { value = Math.max(0, +el.dataset.ends - nowSec()); if (value <= 0.3) due = true; }
      el.textContent = fmtDuration(value);
      const chip = el.closest("[data-total]");
      if (chip && +chip.dataset.total && el.dataset.swStart === undefined) chip.style.setProperty("--p", (1 - value / +chip.dataset.total).toFixed(3));
    });
    if (due) setTimeout(refreshTimers, 700);
  }

  const Voice = (() => {
    const V = { cfg: { enabled: true, auto_speak: false, send_on_release: true, hold_to_talk: true }, rec: null, chunks: [], stream: null,
      recording: false, startedAt: 0, audio: null, ctx: null, analyser: null, source: null, raf: 0, levelValue: 0,
      levelFns: new Set(), speakingText: "", queue: 0, vadTimer: 0, silentSince: 0, hold: false };

    function configure(cfg) {
      if (cfg) V.cfg = { ...V.cfg, ...cfg };
      refreshButtons();
    }
    const autoSpeak = () => !!(V.cfg.enabled && V.cfg.auto_speak);
    async function setAutoSpeak(value) {
      V.cfg.auto_speak = !!value;
      refreshButtons();
      try { await Q.api("/api/assistant/settings", { body: { settings: { voice: { auto_speak: !!value } } } }); } catch (e) {  }
      if (!value) stopSpeaking(true);
    }

    function micError(e) {
      const name = (e && e.name) || "";
      if (name === "NotFoundError" || name === "OverconstrainedError") return t("Микрофон не найден — подключите его или выберите в Настройки → Голос и Live", "No microphone found — plug one in or pick it in Settings → Voice & Live");
      if (name === "NotAllowedError" || name === "SecurityError") return t("Доступ к микрофону запрещён — разрешите его в параметрах Windows → Конфиденциальность → Микрофон", "Microphone access is blocked — allow it in Windows Settings → Privacy → Microphone");
      if (name === "NotReadableError" || name === "AbortError") return t("Микрофон занят другой программой", "The microphone is busy in another program");
      return t("Нет доступа к микрофону: ", "Microphone unavailable: ") + ((e && (e.message || e.name)) || "");
    }
    async function startListening() {
      if (V.recording || !V.cfg.enabled) return;
      stopSpeaking(true);
      if (!navigator.mediaDevices || !window.MediaRecorder) { Q.toast(t("Микрофон недоступен в этом окне", "No microphone in this window"), "err"); return; }
      try {
        V.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      } catch (e) { Q.toast(micError(e), "err", 4000); return; }
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) || "";
      V.chunks = [];
      V.rec = new MediaRecorder(V.stream, mime ? { mimeType: mime } : undefined);
      V.rec.ondataavailable = (e) => { if (e.data && e.data.size) V.chunks.push(e.data); };
      V.rec.onstop = finishListening;
      V.rec.start(250);
      V.recording = true; V.startedAt = performance.now(); V.silentSince = 0;
      watchSilence();
      document.documentElement.dataset.mqcListening = "1";
      Q.emit("listen-start", {});
      refreshButtons();
    }
    function watchSilence() {

      try {
        const ctx = ensureCtx();
        const src = ctx.createMediaStreamSource(V.stream);
        const an = ctx.createAnalyser(); an.fftSize = 1024; src.connect(an);
        const buf = new Float32Array(an.fftSize);
        let heard = false;
        const step = () => {
          if (!V.recording) { try { src.disconnect(); } catch (e) {  } return; }
          an.getFloatTimeDomainData(buf);
          let sum = 0; for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
          const rms = Math.sqrt(sum / buf.length);
          const btn = document.querySelector('[data-plugin="voice"]');
          if (btn) btn.style.setProperty("--mic", Math.min(1, rms * 9).toFixed(3));
          const now = performance.now();
          if (rms > 0.02) { heard = true; V.silentSince = 0; }
          else if (heard && !V.hold) { if (!V.silentSince) V.silentSince = now; if (now - V.silentSince > 1400) { stopListening(); return; } }
          if (now - V.startedAt > 60000) { stopListening(); return; }
          V.vadTimer = requestAnimationFrame(step);
        };
        V.vadTimer = requestAnimationFrame(step);
      } catch (e) {  }
    }
    function stopListening(cancel = false) {
      if (!V.recording) return;
      V.recording = false; V.cancelled = !!cancel;
      cancelAnimationFrame(V.vadTimer);
      try { V.rec.stop(); } catch (e) { finishListening(); }
      if (V.stream) V.stream.getTracks().forEach((track) => track.stop());
      delete document.documentElement.dataset.mqcListening;
      refreshButtons();
    }
    async function finishListening() {
      const chunks = V.chunks; V.chunks = [];
      if (V.cancelled || !chunks.length || performance.now() - V.startedAt < 350) { Q.emit("listen-end", { text: "" }); return; }
      const blob = new Blob(chunks, { type: (V.rec && V.rec.mimeType) || "audio/webm" });
      const data = await new Promise((resolve) => { const r = new FileReader(); r.onload = () => resolve(r.result); r.onerror = () => resolve(""); r.readAsDataURL(blob); });
      if (!data) return;
      document.documentElement.dataset.mqcTranscribing = "1"; refreshButtons();
      try {
        const res = await fetch("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ audio: data, quick: true }) });
        const out = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(out.code === "model_not_downloaded" ? t("Модель распознавания не скачана: Настройки → Голос и Live.", "The speech model is not downloaded: Settings → Voice & Live.") : (out.error || "HTTP " + res.status));
        const text = String(out.text || "").trim();
        Q.emit("listen-end", { text });
        if (!text) { Q.toast(t("Не расслышал", "Didn't catch that"), "", 1800); return; }
        if (V.cfg.send_on_release) Q.send(text); else Q.setInput(text);
      } catch (e) { Q.toast(e.message || String(e), "err", 4000); }
      finally { delete document.documentElement.dataset.mqcTranscribing; refreshButtons(); }
    }

    function ensureCtx() {
      if (!V.ctx) { const AC = window.AudioContext || window.webkitAudioContext; V.ctx = new AC(); }
      if (V.ctx.state === "suspended") V.ctx.resume().catch(() => {});
      return V.ctx;
    }
    async function speak(text) {
      text = String(text || "").trim();
      if (!text || !V.cfg.enabled) return;
      stopSpeaking(true);
      const ticket = ++V.queue;
      document.documentElement.dataset.mqcSpeaking = "loading"; refreshButtons();
      let out;
      try {
        const res = await fetch("/api/voice/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: text.slice(0, 4000), quick: true }) });
        out = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(out.error || "HTTP " + res.status);
      } catch (e) {
        if (ticket === V.queue) { delete document.documentElement.dataset.mqcSpeaking; refreshButtons(); Q.toast(t("Озвучка: ", "Speech: ") + (e.message || e), "err", 3500); }
        return;
      }
      if (ticket !== V.queue) return;
      const audio = new Audio(out.audio);
      V.audio = audio; V.speakingText = text;
      try {
        const ctx = ensureCtx();
        V.source = ctx.createMediaElementSource(audio);
        V.analyser = ctx.createAnalyser(); V.analyser.fftSize = 1024; V.analyser.smoothingTimeConstant = 0.5;
        V.source.connect(V.analyser); V.analyser.connect(ctx.destination);
      } catch (e) { V.analyser = null; }
      audio.onended = () => endSpeaking(false);
      audio.onerror = () => endSpeaking(true);
      try { await audio.play(); } catch (e) { endSpeaking(true); return; }
      document.documentElement.dataset.mqcSpeaking = "1"; refreshButtons();
      Q.emit("speak-start", { text, audio, analyser: V.analyser, level: () => V.levelValue });
      pumpLevel();
    }
    function pumpLevel() {
      cancelAnimationFrame(V.raf);
      if (!V.analyser) return;
      const buf = new Float32Array(V.analyser.fftSize);
      const step = () => {
        if (!V.audio) return;
        V.analyser.getFloatTimeDomainData(buf);
        let sum = 0; for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
        V.levelValue = Math.min(1, Math.sqrt(sum / buf.length) * 4);
        for (const fn of V.levelFns) { try { fn(V.levelValue); } catch (e) {  } }
        Q.emit("speak-level", { level: V.levelValue });
        V.raf = requestAnimationFrame(step);
      };
      V.raf = requestAnimationFrame(step);
    }
    function endSpeaking(interrupted) {
      const text = V.speakingText;
      cancelAnimationFrame(V.raf);
      if (V.source) { try { V.source.disconnect(); } catch (e) {  } }
      V.audio = null; V.source = null; V.analyser = null; V.levelValue = 0; V.speakingText = "";
      for (const fn of V.levelFns) { try { fn(0); } catch (e) {  } }
      delete document.documentElement.dataset.mqcSpeaking; refreshButtons();
      if (text) Q.emit("speak-end", { text, interrupted: !!interrupted });
    }
    function stopSpeaking(interrupted = true) {
      V.queue += 1;
      if (V.audio) { try { V.audio.pause(); } catch (e) {  } endSpeaking(interrupted); }
      else if (document.documentElement.dataset.mqcSpeaking) { delete document.documentElement.dataset.mqcSpeaking; refreshButtons(); }
    }
    function stopAll() { stopListening(true); stopSpeaking(true); }

    let micBtn = null, spkBtn = null;
    function refreshButtons() { if (micBtn) micBtn.update(); if (spkBtn) spkBtn.update(); }
    micBtn = Q.addComposerButton({
      id: "voice", order: 5, icon: IC.mic, label: () => t("Голосовой ввод", "Voice input"),
      title: () => V.recording ? t("Слушаю… (отпустите или нажмите, чтобы закончить)", "Listening… (release or click to finish)")
        : document.documentElement.dataset.mqcTranscribing ? t("Распознаю…", "Transcribing…")
        : t("Голос: нажмите и говорите (удерживайте — рация; Ctrl+Space)", "Voice: click and speak (hold for push-to-talk; Ctrl+Space)"),
      pressed: () => V.recording,
      hidden: () => !V.cfg.enabled,
      onClick: () => { if (V.hold) return; if (V.recording) stopListening(); else startListening(); },
    });
    spkBtn = Q.addComposerButton({
      id: "speak", order: 6, icon: IC.speaker, placement: "menu",
      label: () => document.documentElement.dataset.mqcSpeaking ? t("Остановить озвучку", "Stop speaking") : t("Озвучивать ответы", "Speak answers"),
      title: () => document.documentElement.dataset.mqcSpeaking ? t("Остановить озвучку", "Stop speaking")
        : V.cfg.auto_speak ? t("Озвучивать ответы: вкл", "Speak answers: on") : t("Озвучивать ответы: выкл", "Speak answers: off"),
      pressed: () => !!V.cfg.auto_speak,
      hidden: () => !V.cfg.enabled,
      onClick: () => { if (document.documentElement.dataset.mqcSpeaking) stopSpeaking(true); else setAutoSpeak(!V.cfg.auto_speak); },
    });

    let holdTimer = 0;
    document.addEventListener("pointerdown", (e) => {
      const btn = e.target.closest('[data-plugin="voice"]');
      if (!btn || !V.cfg.hold_to_talk || V.recording) return;
      holdTimer = setTimeout(() => { V.hold = true; startListening(); }, 300);
    });
    document.addEventListener("pointerup", () => {
      clearTimeout(holdTimer);
      if (V.hold) { setTimeout(() => { V.hold = false; }, 50); stopListening(); }
    });

    document.addEventListener("keydown", (e) => {
      if (e.code === "Space" && e.ctrlKey && !e.altKey && !e.repeat && V.cfg.enabled) { e.preventDefault(); V.hold = true; startListening(); }
      else if (e.key === "Escape" && (V.recording || V.audio)) { e.preventDefault(); e.stopPropagation(); stopAll(); }
    }, true);
    document.addEventListener("keyup", (e) => {
      if ((e.code === "Space" || e.key === "Control") && V.hold && V.recording) { V.hold = false; stopListening(); }
    }, true);

    return { configure, speak, stopSpeaking, stopAll, startListening, stopListening, autoSpeak, setAutoSpeak,
      listening: () => V.recording, speaking: () => !!V.audio, level: () => V.levelValue,
      audio: () => V.audio, analyser: () => V.analyser,
      onLevel: (fn) => { V.levelFns.add(fn); return () => V.levelFns.delete(fn); } };
  })();

  window.MemuQuickVoice = {
    speak: (text) => Voice.speak(text), stop: () => Voice.stopSpeaking(true), listen: () => Voice.startListening(),
    listening: Voice.listening, speaking: Voice.speaking, level: Voice.level, audio: Voice.audio, analyser: Voice.analyser,
    onLevel: Voice.onLevel,
  };

  window.MemuQuickCommands = { run: runText, action: runAction, after: afterCard, cards: () => S.cards.slice(), refresh: loadState };

  if (typeof Q.settingsSection === "function") {
    Q.settingsSection("commands", (el, ctx) => (window.MemuCommandsSettings ? window.MemuCommandsSettings.mount(el, ctx || {}) : null),
      { title: { ru: "Команды и голос", en: "Commands and voice" }, script: "commands-settings.js", css: "commands-settings.css" });
  }
})();
