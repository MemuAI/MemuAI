/* MemuAI Quick Ask · Computer Use (Round 30, r30-quick-core).

   The run itself is the existing runtime (POST /api/work/computer/run, GET …/state, POST …/action, GET …/shot/{n});
   this file is what the user sees while the AI drives the mouse and keyboard:

   • one card in the bottom slot: what the AI is doing right now («Смотрит на экран», «Думает · 12 с», «Нажимает…»),
     step N of M, a strip of screen thumbnails (the dot = where it is about to act), the last steps in words;
   • «Стоп» is always on the card (and Esc in the window — the run's own Esc watcher stops it too);
   • approval: the action in plain words + the thumbnail with the target, «Разрешить» (Enter), «Разрешить всё до конца»,
     «Отклонить» (the AI picks another way; an optional hint goes to the model);
   • while the AI works, the window folds into a compact strip (input, answer and lists hidden — less of the screen
     covered) and stays on top; it unfolds by itself for an approval, a pause or the result («Развернуть» any time);
     the window is also excluded from the model's screenshots (QuickAskManager.hidden_from_capture);
   • the result: report (Markdown), «Копировать», «Скрыть»; the outcome is recorded as a turn of the quick chat.

   window.QuickComputer = { start(card), prompt(), active() }  —  commands.js hands confirmed cards here. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const root = document.documentElement;
  const FINISHED = ["done", "error", "cancelled"];
  const IC = {
    cursor: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m4 3 7 17 2.5-7.5L21 10z"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2.2" fill="currentColor"/></svg>',
    expand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
    collapse: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    skip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17h.01"/></svg>',
    copy: Q.icons.copy, close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  };

  let run = null;          // {task, ask, card, st, timer, compact, userCompact, recorded, startedAt, preview}
  let el = null;

  Q.registerPanel("computer", (host) => { el = host; el.classList.add("qcu-host"); return { show() {}, hide() {} }; }, { slot: "bottom", order: 5, hidden: true });

  const PLAY = '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>';
  const PAUSE = '<svg viewBox="0 0 24 24"><rect x="6.5" y="5.5" width="4" height="13" rx="1.2" fill="currentColor"/><rect x="13.5" y="5.5" width="4" height="13" rx="1.2" fill="currentColor"/></svg>';
  const APP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/></svg>';
  const KEYS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7 14h10"/></svg>';

  function secsOf(st) { return st.phase_started && st.now ? Math.max(0, Math.round(st.now - st.phase_started)) : 0; }
  function phaseText(st) {
    const secs = secsOf(st);
    if (run && run.offline >= 8) return t("Нет связи с MemuAI — переподключаюсь…", "No connection to MemuAI — reconnecting…");
    if (st.user_paused || st.phase === "paused_user") return t("ИИ ждёт, пока вы продолжите", "The AI waits until you resume");
    if (st.status === "waiting_approval" || st.pending_action) return t("Ждёт вашего разрешения", "Waiting for your approval");
    if (st.status === "paused") return t("Пауза — нужно ваше решение", "Paused — your decision is needed");
    return {
      starting: t("Запускается…", "Starting…"),
      screenshot: t("Смотрит на экран…", "Looking at the screen…"),
      thinking: t("Думает, что сделать", "Deciding what to do") + (secs >= 3 ? ` · ${secs} ${t("с", "s")}` : "…"),
      acting: t("Выполняет: ", "Doing: ") + (lastRow(st) ? humane(lastRow(st)).title : ""),
    }[st.phase] || t("Работает…", "Working…");
  }
  /* «нажать в точке (700, 430) на мониторе 1 — "Сохранить"» → title «Сохранить» (the model's own reason, what the user
     understands), detail «нажать в точке (700, 430) на мониторе 1». Without a reason the description is the title. */
  function humane(row) {
    const description = String(row.description || row.action || "");
    const reason = String(row.reason || "").trim() || ((description.match(/ — "(.+)"$/) || [])[1] || "");
    const bare = description.replace(/ — ".*"$/, "");
    if (!reason) return { title: bare, detail: "" };
    return { title: reason.charAt(0).toUpperCase() + reason.slice(1), detail: bare };
  }
  const lastRow = (st) => (st.steps || []).filter((r) => r.description).slice(-1)[0] || null;
  function statusWord(st) {
    return { done: t("Готово", "Done"), error: t("Ошибка", "Error"), cancelled: t("Остановлено", "Stopped") }[st.status] || "";
  }
  function stepIcon(row) {
    return { ok: IC.check, done: IC.check, failed: IC.warn, rejected: IC.skip, pending: IC.cursor, paused: IC.warn, acting: IC.cursor }[row.status] || "";
  }
  function stepLabel(row) {
    return { ok: t("выполнено", "done"), done: t("готово", "done"), failed: t("не получилось", "failed"), rejected: t("отклонено вами", "rejected by you"),
      pending: t("ждёт разрешения", "waiting for approval"), paused: t("пауза", "paused"), thinking: t("думает…", "thinking…"), acting: t("выполняет…", "doing…") }[row.status] || "";
  }
  /* What went wrong, in plain words, and what to do about it (error_code from the runtime, app/cu_grounding.py). */
  function explain(st) {
    const code = st.error_code || "";
    const map = {
      rate_limit: [t("Модель отклонила запрос: превышен лимит (429)", "The model refused the request: rate limit (429)"),
        t("Подождите минуту или выберите другую модель либо ключ в MemuAI Proxy.", "Wait a minute or pick another model or key in MemuAI Proxy.")],
      auth: [t("Ключ или аккаунт модели не принят (401/403)", "The model's key or account was refused (401/403)"),
        t("Проверьте аккаунт модели в MemuAI Proxy.", "Check the model's account in MemuAI Proxy.")],
      vision: [t("Модель не принимает картинки", "The model does not accept images"),
        t("Управлению компьютером нужна модель с отметкой «видит».", "Computer use needs a model marked «vision».")],
      input_blocked: [t("Windows не дал нажать или ввести текст", "Windows refused the click or the typing"),
        t("Обычно это окно, запущенное от имени администратора, или заблокированный экран. Запустите MemuAI от имени администратора или работайте в обычном окне.", "Usually the window runs as administrator or the screen is locked. Run MemuAI as administrator or use a normal window.")],
      json: [t("Модель отвечала не в нужном формате", "The model kept answering in the wrong format"),
        t("Запустите ещё раз или выберите более сильную модель.", "Run it again or pick a stronger model.")],
      stuck: [t("ИИ повторял одно и то же без результата", "The AI kept repeating an action with no effect"),
        t("Уточните задачу или сделайте первый шаг сами и запустите снова.", "Make the task more specific, or do the first step yourself and run again.")],
      timeout: [t("Модель слишком долго не отвечала", "The model took too long to answer"),
        t("Проверьте, что MemuAI Proxy запущен, и попробуйте ещё раз.", "Check that MemuAI Proxy is running and try again.")],
      network: [t("Нет связи с моделью", "No connection to the model"),
        t("Проверьте, что MemuAI Proxy запущен и есть интернет.", "Check that MemuAI Proxy is running and you are online.")],
      not_windows: [t("Управление компьютером работает только в Windows", "Computer use works only on Windows"), ""],
      steps: [t("Закончился лимит шагов", "The step limit was reached"),
        t("Запустите ещё раз — ИИ продолжит с текущего экрана; или разбейте задачу на части.", "Run again — the AI continues from the current screen; or split the task.")],
      rejected: [t("Вы отклонили несколько действий подряд", "You rejected several actions in a row"),
        t("Подскажите в поле под действием, как сделать иначе.", "Tell it what to do instead in the field under the action.")],
      stopped: [t("Остановлено вами", "Stopped by you"), ""],
      escape: [t("Остановлено клавишей Esc", "Stopped with Esc"), ""],
      empty: [t("Модель вернула пустой ответ", "The model returned an empty answer"), t("Запустите ещё раз или выберите другую модель.", "Run again or pick another model.")],
    };
    const row = map[code];
    if (row) return { title: row[0], hint: row[1], raw: st.error && !["stopped", "escape", "steps", "rejected"].includes(code) ? st.error : "" };
    return { title: st.error || statusWord(st), hint: "", raw: "" };
  }
  function duration(st) {
    if (!st.started_at) return "";
    const end = st.finished_at || st.now || st.started_at;
    const secs = Math.max(0, Math.round(end - st.started_at));
    return secs >= 60 ? `${Math.floor(secs / 60)} ${t("мин", "min")} ${secs % 60} ${t("с", "s")}` : `${secs} ${t("с", "s")}`;
  }
  const shotUrl = (row, v, big) => `/api/work/computer/shot/${row.n}?monitor=${row.monitor || 1}${big ? "&size=big" : ""}&t=${encodeURIComponent(v || "")}`;
  function marker(row, big) {
    if (!row || !row.point) return "";
    const tag = big && row.target ? `<span class="qcu-tag${row.point[0] > .62 ? " left" : ""}">${esc(row.target)}</span>` : "";
    return `<i class="qcu-dot" style="left:${(row.point[0] * 100).toFixed(2)}%;top:${(row.point[1] * 100).toFixed(2)}%">${tag}</i>`;
  }
  function thumb(row, big = false, extra = "") {
    if (!row || !(row.shots || []).length) return "";
    return `<button type="button" class="qcu-thumb${big ? " big" : ""}${extra}" data-cu-preview="${row.n}" title="${esc(t("Шаг ", "Step ") + row.n + (row.description ? " · " + row.description : ""))}">
      <img alt="" loading="lazy" src="${shotUrl(row, run && run.st ? run.st.task_id : "", big)}"${big ? ` onerror="if(!this.dataset.f){this.dataset.f=1;this.src=this.src.replace('&size=big','')}"` : ""}>${marker(row, big)}${big ? "" : `<b>${row.n}</b>`}</button>`;
  }
  /* A magnified view of the target: background-position p% aligns point p of the image with point p of the box,
     so the target f lands in the centre when p = (f·Z − ½)/(Z − 1). */
  function lens(row, zoom = 3) {
    if (!row || !row.point || !(row.shots || []).length) return "";
    const pos = (f) => Math.min(100, Math.max(0, ((f * zoom - 0.5) / (zoom - 1)) * 100)).toFixed(2);
    return `<div class="qcu-lens" aria-hidden="true" style="background-image:url('${shotUrl(row, run && run.st ? run.st.task_id : "", true)}');background-size:${zoom * 100}% auto;background-position:${pos(row.point[0])}% ${pos(row.point[1])}%"><i></i></div>`;
  }
  function kindIcon(row) {
    const a = String((row && row.action) || "");
    if (a === "open_app") return APP;
    if (a === "type" || a === "key") return KEYS;
    return IC.cursor;
  }

  /* ---------------------------------------------------------------- render (keyed parts: no flicker, inputs survive) */
  function part(name) { return el.querySelector(`[data-part="${name}"]`); }
  function setPart(name, key, html) {
    const box = part(name);
    if (!box || box.dataset.key === key) return;
    box.dataset.key = key;
    box.innerHTML = html;
    box.hidden = !html;
  }
  function frame() {
    el.innerHTML = `<article class="qcu" role="region" aria-label="${esc(t("Управление компьютером", "Computer use"))}">
      <header class="qcu-head" data-part="head"></header>
      <div class="qcu-progress" data-part="progress"></div>
      <div class="qcu-task" data-part="task"></div>
      <div class="qcu-result" data-part="result" hidden></div>
      <div class="qcu-approve" data-part="approve" hidden></div>
      <div class="qcu-stuck" data-part="stuck" hidden></div>
      <div class="qcu-live" data-part="live" hidden></div>
      <div class="qcu-preview" data-part="preview" hidden></div>
      <ol class="qcu-steps" data-part="steps" hidden></ol>
    </article>`;
  }
  function render() {
    if (!run || !el) return;
    const st = run.st || { status: "starting", phase: "starting", steps: [] };
    const finished = FINISHED.includes(st.status);
    const pending = !finished && st.pending_action;
    const paused = !finished && st.status === "paused" && st.stuck;
    const userPaused = !finished && !!st.user_paused;
    const compact = run.compact && !finished && !pending && !paused;
    root.dataset.cu = finished ? "done" : compact ? "compact" : "full";
    const n = st.step || 0, m = st.max_steps || run.maxSteps || 0;
    const counter = n && m ? `<span class="qcu-count">${esc(t(`Шаг ${n} из ${m}`, `Step ${n} of ${m}`))}</span>` : "";
    const state = finished ? (st.status === "done" ? (st.error_code === "steps" ? "warn" : "ok") : st.status === "cancelled" ? "stopped" : "err")
      : pending || paused ? "ask" : userPaused ? "hold" : "run";
    const article = el.querySelector(".qcu");
    if (article) article.dataset.state = state;
    const okCount = (st.steps || []).filter((r) => r.status === "ok").length;
    const nowText = finished ? [t(`Действий: ${okCount}`, `Actions: ${okCount}`), duration(st)].filter(Boolean).join(" · ") : phaseText(st);
    setPart("head", [state, compact, nowText, counter, userPaused].join("|"), `
      <span class="qcu-state qcu-${state}" aria-hidden="true">${finished ? (state === "ok" ? IC.check : state === "err" || state === "warn" ? IC.warn : IC.stop) : userPaused ? PAUSE : IC.cursor}</span>
      <div class="qcu-titles"><b>${esc(finished ? `${statusWord(st)} · ${t("управление компьютером", "computer use")}` : userPaused ? t("Пауза · управление компьютером", "Paused · computer use") : t("ИИ управляет компьютером", "AI is using your computer"))}</b>
        <span class="qcu-now" aria-live="polite">${esc(nowText)}${!finished && counter ? " · " + counter : ""}</span></div>
      <span class="qcu-head-actions">${finished ? `<button type="button" class="qcu-btn ghost" data-cu="dismiss" title="${esc(t("Скрыть карточку", "Dismiss the card"))}">${IC.close}<span>${esc(t("Скрыть", "Dismiss"))}</span></button>`
        : `${pending || paused ? "" : `<button type="button" class="qcu-btn ${userPaused ? "primary" : "ghost icon"}" data-cu="${userPaused ? "resume" : "pause"}" title="${esc(userPaused ? t("Продолжить", "Resume") : t("Пауза: ИИ закончит текущий шаг и подождёт", "Pause: the AI finishes the current step and waits"))}" aria-label="${esc(userPaused ? t("Продолжить", "Resume") : t("Пауза", "Pause"))}">${userPaused ? PLAY + `<span>${esc(t("Продолжить", "Resume"))}</span>` : PAUSE}</button>
          <button type="button" class="qcu-btn ghost icon" data-cu="fold" title="${esc(compact ? t("Развернуть: шаги и снимки экрана", "Expand: steps and screenshots") : t("Свернуть в компактную полоску", "Fold into a compact strip"))}" aria-label="${esc(compact ? t("Развернуть", "Expand") : t("Свернуть", "Fold"))}">${compact ? IC.expand : IC.collapse}</button>`}
          <button type="button" class="qcu-btn stop" data-cu="stop" title="${esc(t("Остановить управление (Esc)", "Stop control (Esc)"))}">${IC.stop}<span>${esc(t("Стоп", "Stop"))}</span></button>`}</span>`);
    // progress: the step counter + a shimmer while the model thinks
    const pct = finished ? 100 : m ? Math.min(100, Math.round(((n - (st.phase === "acting" ? 0.3 : 0.7)) / m) * 100)) : 4;
    setPart("progress", finished ? "" : `${Math.max(pct, 3)}|${st.phase}|${userPaused}`, finished ? "" : `<i style="width:${Math.max(pct, 3)}%"></i>${st.phase === "thinking" || st.phase === "screenshot" ? '<b class="qcu-shimmer"></b>' : ""}`);
    const modelId = st.model || run.model;
    const askEach = st.ask_each !== false;
    setPart("task", compact ? "" : ["t", run.task, modelId, finished, askEach].join("|"), compact ? "" : `<div class="qcu-task-text"><span class="qcu-label">${esc(t("Задача", "Task"))}</span>${esc(run.task)}</div>
      <div class="qcu-meta">${modelId ? `<span class="qcu-chip" title="${esc(modelId)}">${esc(Q.modelName ? Q.modelName(modelId) : modelId)}</span>` : ""}
        ${finished ? "" : `<label class="qcu-switch" title="${esc(t("Выключите — ИИ сделает всё сам до конца задачи", "Turn off — the AI does the rest of the task on its own"))}"><input type="checkbox" data-cu-ask${askEach ? " checked" : ""}><span class="qcu-switch-ui" aria-hidden="true"></span>${esc(t("Спрашивать каждый шаг", "Ask before each step"))}</label>`}</div>`);
    // approval
    if (pending) {
      const row = (st.steps || []).find((r) => r.n === pending.step) || lastRow(st);
      const words = humane(pending);
      setPart("approve", "a" + (pending.step || "") + (pending.description || ""), `
        <div class="qcu-approve-head"><span class="qcu-kind" aria-hidden="true">${kindIcon(row || pending)}</span><div class="qcu-approve-text">
          <span class="qcu-label">${esc(t(`Шаг ${pending.step || n} · ИИ хочет`, `Step ${pending.step || n} · the AI wants to`))}</span>
          <b>${esc(words.title)}</b>${words.detail ? `<small class="qcu-detail">${esc(words.detail)}</small>` : ""}</div></div>
        ${row && (row.shots || []).length ? `<div class="qcu-approve-view${row.point ? "" : " no-point"}">${thumb(row, true)}${lens(row)}</div>
          ${row.point ? `<small class="qcu-hint">${esc(t("Кружок — точка, куда ИИ нажмёт; справа — увеличено", "The ring is where the AI will click; magnified on the right"))}</small>` : ""}` : ""}
        <div class="qcu-approve-actions">
          <button type="button" class="qcu-btn primary" data-cu="approve">${IC.check}<span>${esc(t("Разрешить", "Allow"))}</span><kbd>↵</kbd></button>
          <button type="button" class="qcu-btn" data-cu="approve_all" title="${esc(t("Больше не спрашивать в этой задаче", "Do not ask again in this task"))}">${esc(t("Разрешить всё до конца", "Allow all until done"))}</button>
          <button type="button" class="qcu-btn ghost" data-cu="reject" title="${esc(t("Пропустить это действие — ИИ попробует иначе", "Skip this action — the AI tries another way"))}">${esc(t("Отклонить", "Reject"))}</button>
        </div>
        <input class="qcu-note" data-cu-note type="text" maxlength="500" placeholder="${esc(t("Не так? Подскажите, что сделать иначе, и нажмите «Отклонить»", "Not like this? Tell it what to do instead and press «Reject»"))}">`);
    } else setPart("approve", "", "");
    // stuck pause (stuck.js handles its buttons)
    setPart("stuck", paused ? "s" + st.stuck.id : "", paused && window.MemuStuck ? MemuStuck.cardHtml(st.stuck, { compact: true, source: "computer", task: st.task_id, area: "computer", lang: Q.lang() }) : "");
    // live view: the latest screen the AI looked at, with where it acts
    const rows = (st.steps || []).filter((r) => (r.shots || []).length);
    const latest = rows.slice(-1)[0];
    const showLive = !finished && !pending && !paused && !compact && latest && !run.preview;
    const lr = latest ? humane(latest) : { title: "", detail: "" };
    setPart("live", showLive ? ["l", latest.n, latest.status, latest.point, latest.description, st.phase].join("|") : "", showLive ? `${thumb(latest, true, " live")}
      ${latest.description ? `<div class="qcu-live-cap"><span class="qcu-kind" aria-hidden="true">${kindIcon(latest)}</span><div><b>${esc(lr.title)}</b>
        ${lr.detail ? `<small>${esc(lr.detail)}</small>` : ""}</div></div>` : `<small class="qcu-hint">${esc(t(`Снимок экрана шага ${latest.n} — ИИ решает, что сделать`, `Screenshot of step ${latest.n} — the AI is deciding what to do`))}</small>`}` : "");
    // preview of a clicked thumbnail
    if (finished && run.preview && !run.previewAfterEnd) run.preview = 0;
    const prev = run.preview && (st.steps || []).find((r) => r.n === run.preview);
    setPart("preview", prev && !compact ? "p" + prev.n : "", prev && !compact ? `${thumb(prev, true)}<div class="qcu-preview-cap"><b>${esc(t("Шаг ", "Step ") + prev.n)}</b> <span>${esc(prev.description || stepLabel(prev))}</span>
      <button type="button" class="qcu-btn ghost icon" data-cu="unpreview" aria-label="${esc(t("Закрыть", "Close"))}">${IC.close}</button></div>` : "");
    // the timeline: every step — screen thumbnail with the target, what and why, how it went
    const steps = (st.steps || []).filter((r) => r.description || r.status === "thinking");
    const open = !finished || run.stepsOpen;
    const stepsKey = ["w", open, steps.map((r) => r.n + r.status + (r.changed === false ? "u" : "") + (r.point || "")).join(",")].join("|");
    setPart("steps", compact || !steps.length ? "" : stepsKey, compact || !steps.length ? "" : (finished ? `<li class="qcu-steps-toggle"><button type="button" class="qcu-btn ghost" data-cu="steps">${esc(open ? t("Скрыть шаги", "Hide the steps") : t(`Показать шаги (${steps.length})`, `Show the steps (${steps.length})`))}</button></li>` : "")
      + (open ? steps.map((r) => {
        const h = humane(r);
        const note = r.status === "failed" ? r.error : r.status === "rejected" && r.error ? t("подсказка: ", "hint: ") + r.error : "";
        const changed = r.status === "ok" && r.changed === false ? `<em class="qcu-flag">${esc(t("экран не изменился", "no visible change"))}</em>` : "";
        return `<li class="qcu-step qcu-step-${esc(r.status)}">
          <span class="qcu-step-n" aria-hidden="true">${(r.status === "thinking" || r.status === "acting") ? '<i class="qcu-spin"></i>' : stepIcon(r) || r.n}</span>
          ${(r.shots || []).length ? thumb(r) : '<span class="qcu-thumb ph"></span>'}
          <div class="qcu-step-body"><span class="qcu-step-text">${esc(r.description ? h.title : t("Смотрит на экран и думает…", "Looking at the screen and thinking…"))}</span>
            <small>${esc([t("Шаг ", "Step ") + r.n, h.detail, note || stepLabel(r)].filter(Boolean).join(" · "))}</small>${changed}</div></li>`;
      }).join("") : ""));
    if (!finished) {
      const list = part("steps");
      if (list && !list.hidden) list.scrollTop = list.scrollHeight;
    }
    // the result
    if (finished) {
      const report = st.status === "done" ? String(st.report || t("Готово", "Done")) : "";
      const body = report ? (window.QuickMarkdown ? QuickMarkdown.render(report) : esc(report)) : "";
      const why = st.status !== "done" || st.error_code === "steps" ? explain(st) : null;
      setPart("result", ["r", st.status, report.length, st.error || "", st.error_code || ""].join("|"), `${why ? `<div class="qcu-explain qcu-explain-${esc(state)}"><b>${esc(why.title)}</b>${why.hint ? `<span>${esc(why.hint)}</span>` : ""}${why.raw && why.raw !== why.title ? `<code>${esc(why.raw.slice(0, 300))}</code>` : ""}</div>` : ""}
        ${body ? `<div class="qcu-report qa-md">${body}</div>` : ""}
        <div class="qcu-result-actions">${report ? `<button type="button" class="qcu-btn" data-cu="copy">${IC.copy}<span>${esc(t("Копировать результат", "Copy the result"))}</span></button>` : ""}
          <button type="button" class="qcu-btn${st.status === "done" && st.error_code !== "steps" ? "" : " primary"}" data-cu="again">${esc(st.error_code === "steps" ? t("Продолжить ещё раз", "Continue again") : t("Запустить ещё раз", "Run again"))}</button></div>`);
    } else setPart("result", "", "");
    Q.resize();
  }

  /* ---------------------------------------------------------------- actions */
  async function act(action, extra = {}) {
    if (action === "approve" || action === "approve_all" || action === "reject") {
      // Round 34: the answer names the step the card showed (an Enter held down / a double click / a card not redrawn
      // yet must not approve the NEXT action), and one step is answered once
      const step = run && run.st && run.st.pending_action ? run.st.pending_action.step : 0;
      if (!step || run.answered === step) return;
      run.answered = step;
      extra = { ...extra, step };
    }
    try { await Q.api("/api/work/computer/action", { body: { action, lang: Q.lang(), ...extra } }); }
    catch (e) { Q.toast(e.message || String(e), "err"); }
    poll(true);
  }
  function onClick(ev) {
    const b = ev.target.closest("[data-cu], [data-cu-preview]");
    if (!b || !run) return;
    if (b.dataset.cuPreview) {
      run.preview = run.preview === +b.dataset.cuPreview ? 0 : +b.dataset.cuPreview;
      run.previewAfterEnd = FINISHED.includes((run.st || {}).status);
      render(); return;
    }
    const what = b.dataset.cu;
    if (what === "stop") { b.disabled = true; act("stop"); return; }
    if (what === "approve" || what === "approve_all") { b.disabled = true; act(what); return; }
    if (what === "reject") { const note = el.querySelector("[data-cu-note]"); b.disabled = true; act("reject", { note: note ? note.value.trim() : "" }); return; }
    if (what === "fold") { run.compact = !run.compact; run.userCompact = run.compact; render(); return; }
    if (what === "pause" || what === "resume") { b.disabled = true; act(what); return; }
    if (what === "steps") { run.stepsOpen = !run.stepsOpen; render(); return; }
    if (what === "unpreview") { run.preview = 0; render(); return; }
    if (what === "copy") { const text = String((run.st && run.st.report) || ""); if (window.QuickCopy) QuickCopy.copyMarkdown(text); return; }
    if (what === "again") {
      // Round 34: a card picked up after reopening the window (_attach) re-attached to the finished run instead of
      // starting a new one — «Запустить ещё раз» did nothing
      const card = run.card;
      const fresh = { ...card, _cuStarted: false, _attach: false, _taskId: "", _visionOk: true };
      if (card._attach) fresh.client = { ...(card.client || {}), task: run.task || (card.client || {}).task };
      close(); start(fresh); return;
    }
    if (what === "dismiss") close();
  }
  function onKey(ev) {
    if (!run || ev.isComposing || ev.repeat) return;
    const st = run.st || {};
    if (ev.key === "Enter" && st.pending_action && !FINISHED.includes(st.status) && !ev.target.closest("textarea, .memu-stuck")) {
      const inNote = ev.target.matches && ev.target.matches("[data-cu-note]");
      ev.preventDefault();
      if (inNote && ev.target.value.trim()) act("reject", { note: ev.target.value.trim() });
      else act("approve");
    }
  }
  function close() {
    if (run) { clearTimeout(run.timer); run = null; }
    delete root.dataset.cu;
    Q.keepOnTop && Q.keepOnTop(false);
    if (el) el.innerHTML = "";
    Q.hidePanel("computer");
    Q.resize();
  }
  function record(st) {
    if (run.recorded || !Q.recordTurn) return;
    run.recorded = true;
    const a = st.status === "done" ? String(st.report || t("Готово", "Done")) : `${t("Управление компьютером", "Computer use")}: ${statusWord(st).toLowerCase()}${st.error ? " — " + st.error : ""}`;
    Q.recordTurn({ q: run.ask || run.task, a, model: (st && st.model) || run.model || "" });
  }

  let lastStatus = "";
  /* Round 34: one poll chain per run. A poll started while another request was in flight (every button press calls
     poll(true)) used to start a second timer chain — each press added one more chain of /state requests. */
  async function poll(now = false) {
    if (!run) return;
    clearTimeout(run.timer);
    const gen = run.pollGen = (run.pollGen || 0) + 1;
    const mine = run;
    if (!now) {
      let st = null;
      // a request that hangs (server busy / asleep) counts as «no answer» instead of stalling the chain forever
      const signal = window.AbortSignal && AbortSignal.timeout ? AbortSignal.timeout(6000) : undefined;
      try { st = await Q.api("/api/work/computer/state", { signal }); } catch (e) { st = null; }
      if (run !== mine || run.pollGen !== gen) return;
      // the server is not answering: say so instead of «Думает…» forever
      const offline = st ? 0 : (run.offline || 0) + 1;
      if ((offline >= 8) !== ((run.offline || 0) >= 8)) { run.offline = offline; render(); }
      run.offline = offline;
      if (st && (!run.taskId || st.task_id === run.taskId || !st.task_id)) {
        const wasAsking = !!(run.st && (run.st.pending_action || run.st.status === "paused"));
        run.st = st;
        if (window.MemuStuck && MemuStuck.automation) MemuStuck.automation("computer", st);
        const asking = !!(st.pending_action || st.status === "paused");
        if (asking && !wasAsking && Q.native) Q.api("/api/quick/window", { body: { action: "active" } }).catch(() => {});
        if (!asking && wasAsking && run.userCompact !== false) run.compact = true;      // back to the strip after answering
        if (FINISHED.includes(st.status)) {
          render(); record(st);
          if (lastStatus !== st.status && !document.hasFocus()) Q.toast(`${t("Управление компьютером", "Computer use")}: ${statusWord(st)}`, st.status === "done" ? "ok" : "err", 3200);
          lastStatus = st.status;
          Q.keepOnTop && Q.keepOnTop(false);
          return;
        }
        render();
      }
    }
    run.timer = setTimeout(() => poll(false), now ? 120 : (run.st && run.st.pending_action ? 1200 : run.offline >= 8 ? 2000 : 700));
  }

  /* Round 31 (r31-quick): the model of the run. Settings → Work names one — that one; otherwise the model of THIS window
     (the one the user picked in the chip), not «choose a vision model in Settings → Work». */
  function runModel(c) {
    const own = (Q.state && Q.state().model) || "";
    if (c.model && c.model_source === "work") return c.model;
    return own || c.model || "";
  }
  /* Before the run: does the model see the screen? Known «no» / a guess from the name → a choice instead of a dead end. */
  function visionGate(card, model) {
    const v = Q.vision ? Q.vision(model) : { sees: true };
    if (!model || v.sees || card._visionOk) return false;
    const others = (Q.models ? Q.models() : []).filter((m) => m.id !== model && m.caps && m.caps.vision === true).slice(0, 4);
    frame();
    el.querySelector(".qcu").classList.add("qcu-gate");
    Q.showPanel("computer");
    root.dataset.cu = "full";
    setPart("head", "gate", `<span class="qcu-state qcu-ask" aria-hidden="true">${IC.warn}</span>
      <div class="qcu-titles"><b>${esc(t("Модель может не видеть экран", "The model may not see the screen"))}</b>
        <span class="qcu-now">${esc(v.guess ? t(`«${Q.modelName(model)}» по названию похожа на «только текст» — это догадка`, `«${Q.modelName(model)}» looks text-only by its name — a guess`)
          : t(`«${Q.modelName(model)}» отмечена как «только текст»`, `«${Q.modelName(model)}» is marked text-only`))}</span></div>
      <span class="qcu-head-actions"><button type="button" class="qcu-btn ghost" data-cu-gate="cancel">${IC.close}<span>${esc(t("Отмена", "Cancel"))}</span></button></span>`);
    setPart("task", "t" + card.client.task, `<span class="qcu-label">${esc(t("Задача", "Task"))}</span>${esc(card.client.task || "")}`);
    setPart("result", "gate", `<div class="qcu-report">${esc(t("Управлению компьютером нужна модель, которая видит картинки: она смотрит на снимки экрана.", "Computer use needs a model that sees images: it looks at screenshots."))}</div>
      <div class="qcu-result-actions">
        <button type="button" class="qcu-btn primary" data-cu-gate="probe">${esc(t("Проверить зрение", "Check vision"))}</button>
        <button type="button" class="qcu-btn" data-cu-gate="force">${esc(t("Запустить всё равно", "Run anyway"))}</button>
        ${others.map((m) => `<button type="button" class="qcu-btn" data-cu-gate="use" data-model="${esc(m.id)}">${IC.check}<span>${esc(t("На ", "On ") + m.name)}</span></button>`).join("")}
        <button type="button" class="qcu-btn ghost" data-cu-gate="pick">${esc(t("Выбрать модель…", "Pick a model…"))}</button></div>`);
    Q.resize();
    const host = el;
    const onGate = async (ev) => {
      const b = ev.target.closest("[data-cu-gate]");
      if (!b) return;
      const what = b.dataset.cuGate;
      const go = (id) => { host.removeEventListener("click", onGate); start({ ...card, _cuStarted: false, _visionOk: true, client: { ...card.client, model: id, model_source: "work" } }); };
      if (what === "cancel") { host.removeEventListener("click", onGate); close(); return; }
      if (what === "force") { Q.forceVision && Q.forceVision(model); go(model); return; }
      if (what === "use") { go(b.dataset.model); return; }
      if (what === "pick") { host.removeEventListener("click", onGate); close(); Q.openPicker && Q.openPicker(); Q.toast(t("Выберите модель с отметкой «видит» и повторите команду", "Pick a model marked «vision» and repeat the command"), "", 4200); return; }
      if (what === "probe" && Q.probeVision) { if (await Q.probeVision(model, b)) go(model); }
    };
    host.addEventListener("click", onGate);
    return true;
  }

  /* card = {client: {type: "computer", task, model, max_steps, project_id?}, ask?} — the confirmed assistant card */
  async function start(card) {
    if (!card || card._cuStarted) return;
    card._cuStarted = true;
    if (run && !FINISHED.includes((run.st || {}).status)) { Q.toast(t("Компьютерный агент уже работает", "The computer agent is already running"), "err"); return; }
    close();
    const c = { ...(card.client || {}) };
    if (!card._attach) {
      c.model = runModel(c);
      if (visionGate({ ...card, client: c }, c.model)) return;
    }
    run = { card, task: String(c.task || ""), model: String(c.model || ""), ask: card.ask || c.task || "", st: null, compact: false, userCompact: null, recorded: false, maxSteps: c.max_steps || 12, preview: 0 };
    lastStatus = "";
    frame();
    Q.showPanel("computer");
    Q.keepOnTop && Q.keepOnTop(true);
    render();
    if (card._attach) { run.taskId = card._taskId || ""; run.compact = true; poll(false); return; }
    const turns = ((Q.state && Q.state().turns) || []).slice(-4).map((x) => (x.q ? "User: " + x.q + "\n" : "") + (x.a ? "Assistant: " + x.a : "")).join("\n");
    try {
      const r = await Q.api("/api/work/computer/run", { body: { source: "quick", project_id: c.project_id || undefined, confirmed: true, model: c.model || undefined, task: c.task,
        context: turns, max_steps: c.max_steps || 12, access_mode: "ask", ai_cursor: true, lang: Q.lang() } });
      if (!run) return;
      run.taskId = r.task_id || "";
      // fold into the strip once the run is under way (an approval or the result unfolds it again)
      setTimeout(() => { if (run && run.userCompact === null) { run.compact = true; render(); } }, 900);
      poll(false);
    } catch (e) {
      if (!run) return;
      run.st = { status: "error", error: e.message || String(e), steps: [] };
      render(); record(run.st);
      Q.keepOnTop && Q.keepOnTop(false);
    }
  }
  /* Ctrl+K / «/компьютер»: the phrase the commands engine understands — the user only types the task. */
  function prompt() {
    Q.setInput(t("Управляй компьютером: ", "Computer use: "));
    Q.toast(t("Опишите задачу и нажмите Enter — каждое действие ИИ спросит разрешения", "Describe the task and press Enter — every action asks for your approval"), "", 3600);
  }

  document.addEventListener("click", (ev) => { if (el && el.contains(ev.target)) onClick(ev); });
  // «Спрашивать каждый шаг» during the run (off = the AI finishes on its own; on again = it asks from the next step)
  document.addEventListener("change", (ev) => {
    if (!el || !run || !ev.target.matches || !ev.target.matches("[data-cu-ask]")) return;
    act("ask_each", { value: !!ev.target.checked });
  });
  document.addEventListener("keydown", (ev) => { if (el && run && (el.contains(ev.target) || ev.target === document.body || ev.target.id === "qa-input")) onKey(ev); });
  // Esc in the window while the AI works = «Стоп» (one level, like a running answer); after the end — the card goes.
  Q.on("escape", (e) => {
    if (!run) return;
    e.preventDefault();
    if (FINISHED.includes((run.st || {}).status)) close(); else act("stop");
  });
  Q.on("config", () => { if (run) render(); });   // the window language may arrive after the card was drawn
  Q.on("chat-new", () => { if (run && FINISHED.includes((run.st || {}).status)) close(); });
  Q.on("answer-start", () => { if (run && FINISHED.includes((run.st || {}).status)) close(); });   // a new question: the card has done its job
  // reopening the window during a run picks the card up again (the run lives on the server)
  Q.on("open", async () => {
    if (run) { poll(false); return; }
    try {
      const st = await Q.api("/api/work/computer/state");
      if (st && st.task_id && !FINISHED.includes(st.status) && st.status !== "idle") {
        start({ client: { type: "computer", task: st.task, max_steps: st.max_steps }, _attach: true, _taskId: st.task_id });
      }
    } catch (e) { /* not running */ }
  });
  Q.addAction && Q.addAction({ id: "computer-use", section: () => t("Чат", "Chat"), icon: IC.cursor, label: () => t("Управление компьютером…", "Computer use…"),
    sub: () => t("ИИ кликает за вас", "the AI clicks for you"), run: prompt });

  window.QuickComputer = { start, prompt, active: () => !!run && !FINISHED.includes((run.st || {}).status) };
})();
