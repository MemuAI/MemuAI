

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => (Q.lang() === "en" ? en : ru);
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STORE = "memu_quick_dfy_tasks";
  const FINAL = new Set(["done", "failed", "cancelled"]);

  const ACTION_RE = new RegExp([
    "^(пожалуйста\\s+)?(забронируй|закажи|купи|оплати|оформи|запиши\\s+меня|зарегистрируй|отправь|отмени|продли|подпишись|отпишись|найди\\s+и\\s+(купи|закажи|забронируй)|заполни|скачай|войди|авторизуйся|сделай\\s+заказ|вызови\\s+такси|арендуй|сними)",
    "^(please\\s+)?(book|order|buy|purchase|pay|reserve|sign\\s+(me\\s+)?up|register|subscribe|unsubscribe|cancel\\s+my|renew|fill\\s+(in|out)|log\\s+in|check\\s+me\\s+in|rent|schedule\\s+an?)\\b",
  ].join("|"), "i");
  function isActionLike(text) {
    const s = String(text || "").trim();
    if (s.length < 6 || s.length > 600) return false;
    if (/^(как|почему|зачем|что|где|когда|сколько|можно\s+ли|how|why|what|where|when|can\s+i|is\s+it)\b/i.test(s)) return false;
    if (/\?\s*$/.test(s)) return false;
    return ACTION_RE.test(s);
  }

  async function json(path, options) {
    const response = await fetch(path, Object.assign({ cache: "no-store" }, options || {}));
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw Object.assign(new Error(data.error || data.detail || ("HTTP " + response.status)), { status: response.status });
    return data;
  }
  const post = (path, body) => json(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const M = () => (window.MemuDFY && typeof window.MemuDFY.start === "function" ? window.MemuDFY : null);
  const net = {
    status: () => (M() && M().status ? M().status() : json("/api/dfy/status")),
    async start(goal, context, target) {
      if (M()) return M().start(goal, context, "quick", { target: target || "" });
      const state = await net.status();
      if ((!target || target === "browser") && state.browser_choice_required) {
        await new Promise((resolve, reject) => {
          const script = document.createElement("script"); script.src = "/dfy-client.js?v=3.8-r46";
          script.onload = resolve; script.onerror = () => reject(new Error(t("Не удалось открыть выбор браузера", "Could not open the browser picker")));
          document.head.append(script);
        });
        if (!M()) throw new Error(t("Выбор браузера недоступен", "Browser picker is unavailable"));
        return M().start(goal, context, "quick", { target: target || "" });
      }
      return post("/api/dfy/tasks", { goal, context, source: "quick", target: target || "" });
    },
    get: (id) => json("/api/dfy/tasks/" + encodeURIComponent(id)),
    answer: (id, payload) => (M() && M().answer ? M().answer(id, payload) : post("/api/dfy/tasks/" + encodeURIComponent(id) + "/answer", payload)),
    cancel: (id) => (M() && M().cancel ? M().cancel(id) : post("/api/dfy/tasks/" + encodeURIComponent(id) + "/cancel", {})),
    watch(id, onUpdate) {
      if (M() && M().watch) return M().watch(id, onUpdate);

      let stopped = false, source = null, timer = 0;
      const poll = async () => {
        if (stopped) return;
        try { const task = await net.get(id); onUpdate(task); if (FINAL.has(task.status)) return; } catch (e) { if (e.status === 404) { onUpdate({ id, gone: true, status: "gone", steps: [] }); return; } }
        timer = setTimeout(poll, 1500);
      };
      if (typeof EventSource !== "undefined") {
        try {
          source = new EventSource("/api/dfy/tasks/" + encodeURIComponent(id) + "/events");
          source.onmessage = (event) => {
            let task = null;
            try { task = JSON.parse(event.data); } catch (_) { return; }
            if (task && typeof task === "object") { onUpdate(task); if (FINAL.has(task.status)) { source.close(); source = null; } }
          };
          source.onerror = () => { if (source) { source.close(); source = null; } if (!stopped) poll(); };
        } catch (_) { poll(); }
      } else poll();
      return () => { stopped = true; clearTimeout(timer); if (source) source.close(); };
    },
  };

  const D = { status: null, statusAt: 0, tasks: new Map(), el: null, panel: null, offer: "", notice: null, visible: true };

  function saved() { try { return JSON.parse(localStorage.getItem(STORE) || "[]").filter((x) => typeof x === "string").slice(-6); } catch (_) { return []; } }
  function remember() { try { localStorage.setItem(STORE, JSON.stringify(Array.from(D.tasks.keys()).slice(-6))); } catch (_) {} }

  async function status(force) {
    if (!force && D.status && Date.now() - D.statusAt < 30000) return D.status;
    try { D.status = await net.status(); } catch (e) { D.status = { enabled: false, ready: false, reason: e.status === 404 ? "missing" : (e.message || "offline") }; }
    D.statusAt = Date.now();
    return D.status;
  }
  function notReadyText(st) {
    if (!st || st.reason === "missing") return t("DFY ещё недоступен в этой версии сервера MemuAI.", "DFY is not available on this MemuAI server yet.");
    if (!st.enabled) return t("«Сделать за меня» выключено — включите в Настройках → Do For You.", "«Do it for me» is off — turn it on in Settings → Do For You.");
    if (!st.model) return t("Нет модели с Vision для DFY: добавьте в MemuAI Proxy модель, которая видит изображения (или включите ей «Зрение» вручную), и выберите её в Настройках → Do For You.",
      "No vision model for DFY: add a model that sees images in MemuAI Proxy (or turn “Vision” on for it manually) and pick it in Settings → Do For You.");
    if (st.model_has_vision === false) return t("Модель DFY не видит изображения — выберите в Настройках → Do For You модель из списка (там только модели с Vision).",
      "The DFY model cannot see images — pick one in Settings → Do For You (only vision models are listed).");
    return st.reason ? String(st.reason) : t("DFY не готов.", "DFY is not ready.");
  }

  function mount(el) {
    D.el = el;
    el.classList.add("qdfy");
    el.addEventListener("click", onClick);
    el.addEventListener("submit", onSubmit);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.matches("input[data-field],input[data-code]") && !e.isComposing) { e.preventDefault(); e.target.form && e.target.form.requestSubmit(); } });
    el.addEventListener("input", (e) => { const F = forms(); if (F) { F.onInput(e); F.onCodeInput(e, (form) => form.requestSubmit()); } });
    return { show() { render(); }, hide() {}, unmount() { D.el = null; } };
  }
  function ensurePanel() {
    if (!D.panel) D.panel = Q.registerPanel("dfy", mount, { slot: "bottom", order: -5, hidden: true });
    return D.panel;
  }
  function needPanel() { return D.tasks.size > 0 || !!D.offer || !!D.notice; }
  function sync() {
    ensurePanel();
    if (needPanel()) D.panel.show(); else D.panel.hide();
    render();
  }

  const STATUS = {
    queued: () => t("В очереди", "Queued"), running: () => t("Выполняю", "Working"), waiting_user: () => t("Нужен ваш ответ", "Needs you"),
    done: () => t("Готово", "Done"), failed: () => t("Не получилось", "Failed"), cancelled: () => t("Отменено", "Cancelled"),
  };

  const forms = () => (window.MemuDFY && window.MemuDFY.forms) || null;
  const CHANNEL = { sms: ["SMS", "SMS"], email: ["письмо", "email"], app: ["приложение", "app"], call: ["звонок", "call"] };

  const deadlines = new Map();
  function deadline(q) {
    if (!deadlines.has(q.id)) { deadlines.set(q.id, Date.now() + (q.expires_in || 600) * 1000); if (deadlines.size > 20) deadlines.delete(deadlines.keys().next().value); }
    return deadlines.get(q.id);
  }

  function takeover() {
    if (window.MemuDFY && window.MemuDFY.takeover) return Promise.resolve(window.MemuDFY.takeover);
    if (window.MemuDFYTakeover) return Promise.resolve(window.MemuDFYTakeover);
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/dfy-takeover.js?v=3.5-r33";
      s.onload = () => resolve(window.MemuDFYTakeover);
      s.onerror = () => reject(new Error(t("Окно перехвата недоступно", "The takeover window is unavailable")));
      document.head.appendChild(s);
    });
  }
  const TAKEOVER_WHY = { captcha: ["Сайт просит пройти проверку «я не робот».", "The site wants an “I'm not a robot” check."],
    login: ["Нужно войти на сайт — это лучше сделать вам.", "Signing in is best done by you."], "2fa": ["Нужно подтвердить вход.", "The sign-in needs a confirmation."] };
  function controlHtml(task) {
    if (FINAL.has(task.status) || task.status === "queued") return "";
    const id = esc(task.id);
    if (task.control === "user") {
      return `<div class="qdfy-control"><span>${esc(t("Управляете вы — ИИ на паузе", "You're in control — the AI is paused"))}</span>
        <button type="button" class="qdfy-btn small" data-qdfy="takeover" data-id="${id}">${esc(t("Открыть окно", "Open window"))}</button>
        <button type="button" class="qdfy-btn small primary" data-qdfy="giveback" data-id="${id}">${esc(t("Вернуть ИИ", "Return to AI"))}</button></div>`;
    }
    const q = task.question;
    if (q && q.kind === "takeover") {
      const why = TAKEOVER_WHY[q.why];
      return `<div class="qdfy-q"><div class="qdfy-q-prompt">${esc(q.prompt || (why ? why[Q.lang() === "en" ? 1 : 0] : t("ИИ просит вас сделать шаг в браузере.", "The AI asks you to do a step in the browser.")))}</div>
        <div class="qdfy-q-actions"><button type="button" class="qdfy-btn primary" data-qdfy="takeover" data-id="${id}">${esc(t("Перехватить управление", "Take control"))}</button>
        <button type="button" class="qdfy-btn" data-qdfy="skip" data-id="${id}">${esc(t("Пропустить", "Skip"))}</button></div></div>`;
    }
    return "";
  }
  function questionHtml(task) {
    const q = task.question;
    if (!q || task.status !== "waiting_user" || q.kind === "takeover" || task.control === "user") return "";
    const id = esc(task.id);

    if (q.kind === "stuck" && window.MemuStuck) return window.MemuStuck.dfyQuestionHtml(task, q, { compact: true, lang: Q.lang() });
    const prompt = `<div class="qdfy-q-prompt">${esc(q.prompt || t("Агенту нужен ответ", "The agent needs an answer"))}</div>`;
    if (q.kind === "confirm") {

      const body = q.machine && window.MemuDFYRemote ? `<div class="qdfy-machine-q">${window.MemuDFYRemote.confirmBody(q)}</div>` : prompt;
      return `<div class="qdfy-q">${body}${q.amount ? `<div class="qdfy-amount">${esc(t("Сумма: ", "Total: ") + q.amount)}</div>` : ""}
        <div class="qdfy-q-actions"><button type="button" class="qdfy-btn primary" data-qdfy="approve" data-id="${id}">${esc(t("Подтвердить", "Approve"))}</button>
        <button type="button" class="qdfy-btn" data-qdfy="deny" data-id="${id}">${esc(t("Отклонить", "Decline"))}</button></div></div>`;
    }
    if (q.kind === "code") {
      const len = +q.length || 0;
      const via = CHANNEL[q.channel] ? CHANNEL[q.channel][Q.lang() === "en" ? 1 : 0] : "";
      const where = [via, q.sent_to].filter(Boolean).join(" → ");
      return `<form class="qdfy-q" data-id="${id}" data-kind="code" data-dfy-code data-len="${len}" data-numeric="${q.numeric === false ? "0" : "1"}" autocomplete="off">${prompt}
        ${where ? `<div class="qdfy-note">${esc(t("Код отправлен: ", "Code sent: ") + where)}</div>` : ""}
        <input class="qdfy-input qdfy-code" data-code type="text" ${q.numeric === false ? "" : 'inputmode="numeric" pattern="[0-9]*"'} autocomplete="one-time-code" maxlength="${len ? len + 4 : 16}"
          placeholder="${len ? "•".repeat(len) : esc(t("Код", "Code"))}" aria-label="${esc(t("Код подтверждения", "Verification code"))}" spellcheck="false">
        <small class="dfy-err qdfy-err" aria-live="polite"></small>
        <div class="qdfy-q-actions wrap"><button type="submit" class="qdfy-btn primary">${esc(t("Отправить код", "Send code"))}</button>
          ${q.can_resend !== false ? `<button type="button" class="qdfy-btn" data-qdfy="resend" data-id="${id}">${esc(t("Не пришёл — ещё раз", "Didn't get it — resend"))}</button>` : ""}
          <button type="button" class="qdfy-btn" data-qdfy="decline-secret" data-id="${id}">${esc(t("Отмена", "Cancel"))}</button></div>
        <div class="qdfy-note" data-dfy-timer data-deadline="${deadline(q)}"></div></form>`;
    }
    const choices = q.kind === "choice" && Array.isArray(q.choices) ? q.choices.slice(0, 8) : [];
    const secret = q.kind === "secret";
    const F = forms();
    const names = Array.isArray(q.fields) && q.fields.length ? q.fields.slice(0, 12).map((f) => (typeof f === "object" && f ? String(f.name || f.id || "") : String(f))).filter(Boolean) : [];
    let inputs;
    if (secret && F) inputs = `<div class="qdfy-fields">${names.map((f) => F.inputHtml(f, { kind: q.secret_kind, cls: "qdfy-input" })).join("")}</div>`;
    else {

      inputs = (secret ? names : ["answer"]).map((name) => {
        const pw = secret && !/^(username|email|phone|first_name|last_name|middle_name|city|country)$/i.test(name);
        return `<input class="qdfy-input" data-field="${esc(name)}" type="${pw ? "password" : "text"}" autocomplete="off" spellcheck="false"
          placeholder="${esc(secret ? name : (choices.length ? t("Или свой вариант", "Or your own answer") : t("Ваш ответ", "Your answer")))}">`;
      }).join("");
    }
    const choiceRow = choices.length ? `<div class="qdfy-q-actions wrap">${choices.map((c) => {
      const value = typeof c === "object" && c ? (c.value ?? c.label) : c, label = typeof c === "object" && c ? (c.label ?? c.value) : c;
      return `<button type="button" class="qdfy-btn" data-qdfy="choice" data-id="${id}" data-value="${esc(value)}">${esc(label)}</button>`;
    }).join("")}</div>` : "";
    return `<form class="qdfy-q" data-id="${id}" data-kind="${secret ? "secret" : "text"}" novalidate>${prompt}${choiceRow}
      ${secret && Array.isArray(q.existing) && q.existing.length ? `<div class="qdfy-q-actions wrap">${q.existing.slice(0, 6).map((x) => `<button type="button" class="qdfy-btn small" data-qdfy="use-secret" data-id="${id}" data-value="${esc(x.id)}">${esc(t("Использовать ", "Use ") + (x.mask || x.id))}</button>`).join("")}</div>` : ""}
      ${inputs}
      <div class="qdfy-q-actions"><button type="submit" class="qdfy-btn primary">${esc(t("Отправить", "Send"))}</button>
      ${secret ? `<button type="button" class="qdfy-btn" data-qdfy="decline-secret" data-id="${id}">${esc(t("Не давать", "Don't provide"))}</button>`
        : `<button type="button" class="qdfy-btn" data-qdfy="skip" data-id="${id}">${esc(t("Не отвечать", "Skip"))}</button>`}
      ${secret ? `<span class="qdfy-lock"><svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg> ${esc(t("ИИ увидит только маску", "the AI sees only a mask"))}</span>` : ""}</div>
      ${secret ? `<label class="qdfy-save"><input type="checkbox" data-qdfy-save checked> ${esc(t("Запомнить в зашифрованном хранилище", "Remember in the encrypted vault"))}</label>` : ""}</form>`;
  }

  const ACTIONS = { navigate: ["Открыл страницу", "Opened a page"], click: ["Нажал", "Clicked"], click_xy: ["Нажал", "Clicked"],
    type: ["Ввёл", "Typed"], select: ["Выбрал", "Selected"], key: ["Клавиша", "Key"], scroll: ["Прокрутил", "Scrolled"],
    back: ["Назад", "Back"], wait: ["Подождал", "Waited"], done: ["Готово", "Done"], fail: ["Не вышло", "Failed"],
    confirm: ["Спрашивает", "Asks"], ask_user: ["Вопрос", "Question"], request_secret: ["Данные", "Data"],
    request_code: ["Код", "Code"], request_takeover: ["Ваш шаг", "Your step"], stuck: ["Пауза", "Paused"], takeover: ["Вы управляли", "You controlled"] };
  function actionWord(action) {
    const row = ACTIONS[String(action || "")];
    return row ? row[Q.lang() === "en" ? 1 : 0] : String(action || "");
  }
  function shotHtml(task, steps, final) {
    if (final || task.control === "user") return "";
    const lastShot = [...steps].reverse().find((s) => s && s.screenshot_url);
    if (!lastShot) return "";
    return `<button type="button" class="qdfy-shot${D.bigShot === task.id ? " big" : ""}" data-qdfy="shot" data-id="${esc(task.id)}" title="${esc(t("Что сейчас видит ИИ — нажмите, чтобы увеличить", "What the AI sees now — click to enlarge"))}"><img alt="" src="${esc(lastShot.screenshot_url)}"></button>`;
  }

  const HEAD_IC = {
    takeover: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m4 3 7 17 2.5-7.5L21 10z"/></svg>',
    cancel: '<svg viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="2.2" fill="currentColor"/></svg>',
  };
  function taskHtml(entry) {
    const task = entry.task || {};
    const steps = Array.isArray(task.steps) ? task.steps : [];
    const last = steps.slice(-4);
    const final = FINAL.has(task.status);
    const label = task.control === "user" ? t("Управляете вы", "You're in control") : (STATUS[task.status] || STATUS.queued)();
    return `<article class="qdfy-card" data-status="${esc(task.status || "queued")}" data-id="${esc(task.id)}">
      <header class="qdfy-head"><span class="qdfy-dot" aria-hidden="true"></span><b class="qdfy-goal" title="${esc(task.goal || "")}">${esc(task.goal || t("Задача", "Task"))}</b>
        ${task.target && window.MemuDFYRemote ? window.MemuDFYRemote.targetChip(task.target) : ""}<span class="qdfy-state">${esc(label)}</span>
        ${final ? `<button type="button" class="qdfy-x" data-qdfy="dismiss" data-id="${esc(task.id)}" title="${esc(t("Убрать", "Dismiss"))}" aria-label="${esc(t("Убрать", "Dismiss"))}">×</button>`
          : `${task.control !== "user" && task.status !== "queued" && !(task.target && !task.target.browser) && !(task.question && task.question.kind === "takeover") ? `<button type="button" class="qdfy-btn small qdfy-hbtn" data-qdfy="takeover" data-id="${esc(task.id)}" title="${esc(t("ИИ встанет на паузу, а вы сами покликаете в его браузере", "The AI pauses and you click in its browser yourself"))}" aria-label="${esc(t("Перехватить", "Take control"))}"><i aria-hidden="true">${HEAD_IC.takeover}</i><span>${esc(t("Перехватить", "Take control"))}</span></button>` : ""}<button type="button" class="qdfy-btn small qdfy-hbtn" data-qdfy="cancel" data-id="${esc(task.id)}" title="${esc(t("Отменить задачу", "Cancel the task"))}" aria-label="${esc(t("Отменить", "Cancel"))}"><i aria-hidden="true">${HEAD_IC.cancel}</i><span>${esc(t("Отменить", "Cancel"))}</span></button>`}</header>
      ${last.length ? `<ol class="qdfy-steps" start="${steps.length - last.length + 1}">${last.map((s) => `<li${s.ok === false ? ' class="bad"' : ""}>${s.detail ? `<em>${esc(String(s.detail).slice(0, 160))}</em>` : `<span>${esc(actionWord(s.action))}</span>`}</li>`).join("")}</ol>` : (final ? "" : `<div class="qdfy-wait">${esc(t("Запускаю агента…", "Starting the agent…"))}</div>`)}
      ${task.target && window.MemuDFYRemote && last.length ? window.MemuDFYRemote.stepExtra(last[last.length - 1], { live: !final, tag: "div" }) : ""}
      ${shotHtml(task, steps, final)}
      ${controlHtml(task)}
      ${questionHtml(task)}
      ${task.status === "done" && task.result ? `<div class="qdfy-result">${esc(String(task.result).slice(0, 1200))}</div>` : ""}
      ${task.status === "failed" && task.error ? `<div class="qdfy-error">${esc(String(task.error).slice(0, 600))}</div>` : ""}
      ${!final ? `<div class="qdfy-foot">${esc(t("Можно скрыть окно — задача продолжится, я сообщу о результате.", "You can hide the window — the task goes on and I will tell you the result."))}</div>` : ""}
    </article>`;
  }
  function render() {
    const el = D.el;
    if (!el) return;

    const typing = {};
    const keyOf = (i) => i.closest("form").dataset.id + ":" + (i.dataset.field || (i.hasAttribute("data-code") ? "code" : ""));
    const FIELDS_SEL = "input[data-field],select[data-field],input[data-code]";
    el.querySelectorAll(FIELDS_SEL).forEach((i) => { if (i.value && i.type !== "checkbox") typing[keyOf(i)] = i.value; });
    const active = document.activeElement;
    const focused = active && el.contains(active) && active.matches && active.matches(FIELDS_SEL) ? keyOf(active) : "";
    const parts = [];
    if (D.offer) {
      parts.push(`<div class="qdfy-offer"><span class="qdfy-bolt" aria-hidden="true">${Q.icons && Q.icons.bolt ? Q.icons.bolt : ""}</span><span>${esc(t("Похоже на действие — могу сделать это за вас", "Looks like an action — I can do it for you"))}</span>
        <button type="button" class="qdfy-btn primary small" data-qdfy="offer">${esc(t("Сделать за меня", "Do it for me"))} <kbd>Ctrl ↵</kbd></button>
        <button type="button" class="qdfy-x" data-qdfy="offer-no" title="${esc(t("Не надо", "No thanks"))}" aria-label="${esc(t("Не надо", "No thanks"))}">×</button></div>`);
    }
    if (D.notice) {
      parts.push(`<div class="qdfy-offer qdfy-notice" role="alert"><span class="qdfy-bolt" aria-hidden="true">${Q.icons && Q.icons.bolt ? Q.icons.bolt : ""}</span><span>${esc(D.notice.text)}</span>
        ${D.notice.settings ? `<button type="button" class="qdfy-btn small" data-qdfy="settings">${esc(t("Настройки DFY", "DFY settings"))}</button>` : ""}
        <button type="button" class="qdfy-x" data-qdfy="notice-no" title="${esc(t("Закрыть", "Close"))}" aria-label="${esc(t("Закрыть", "Close"))}">×</button></div>`);
    }
    for (const entry of D.tasks.values()) parts.push(taskHtml(entry));

    const oldShots = new Map(Array.from(el.querySelectorAll(".qdfy-shot")).map((b) => [b.dataset.id, b.querySelector("img")]));
    el.innerHTML = parts.join("");
    el.querySelectorAll(".qdfy-shot").forEach((b) => {
      const old = oldShots.get(b.dataset.id), fresh = b.querySelector("img");
      if (!old || !fresh) return;
      const next = fresh.getAttribute("src") || "";
      fresh.replaceWith(old);
      if (old.getAttribute("src") === next || old.dataset.loading === next) return;
      old.dataset.loading = next;
      const pre = new Image();
      pre.onload = () => { if (old.dataset.loading === next) { old.src = next; delete old.dataset.loading; Q.resize(); } };
      pre.onerror = () => { if (old.dataset.loading === next) delete old.dataset.loading; };
      pre.src = next;
    });
    el.querySelectorAll(FIELDS_SEL).forEach((i) => {
      const key = keyOf(i);
      if (typing[key]) i.value = typing[key];
      if (focused && key === focused) i.focus();
    });

    for (const entry of D.tasks.values()) {
      const q = entry.task && entry.task.status === "waiting_user" && entry.task.question;
      if (!q || entry.focusedQuestion === q.id) continue;
      entry.focusedQuestion = q.id;
      const box = el.querySelector(`form.qdfy-q[data-id="${CSS.escape(String(entry.task.id))}"]`);
      const typingMain = active && active.id === "qa-input" && String(active.value || "").trim();
      const kept = document.activeElement && el.contains(document.activeElement) && document.activeElement.matches(FIELDS_SEL);
      if (box && !kept && !typingMain) { const first = box.querySelector(FIELDS_SEL); if (first) try { first.focus({ preventScroll: true }); } catch (_) {} }
    }
    for (const entry of D.tasks.values()) {
      const q = entry.task && entry.task.question;
      if (q && entry.answering === q.id) el.querySelectorAll(`.qdfy-card[data-id="${CSS.escape(String(entry.task.id))}"] .qdfy-q button`).forEach((b) => { b.disabled = true; });
    }
    const F = forms();
    clearInterval(D.timer);
    if (F && F.tickTimers(el)) D.timer = setInterval(() => { if (!D.el || !F.tickTimers(D.el)) clearInterval(D.timer); }, 1000);
    Q.resize();
  }

  function contextFromChat() {
    const snap = Q.state ? Q.state() : {};
    const turns = Array.isArray(snap.turns) ? snap.turns : [];
    const out = [];
    for (const turn of turns.slice(-6)) {
      if (turn.q) out.push({ role: "user", content: String(turn.q).slice(0, 4000) });
      if (turn.a) out.push({ role: "assistant", content: String(turn.a).slice(0, 4000) });
    }
    return out;
  }
  async function run(goalText) {
    let goal = String(goalText == null ? Q.getInput() : goalText).trim();
    const context = contextFromChat();
    if (!goal && context.length) goal = context.filter((m) => m.role === "user").slice(-1)[0]?.content || "";
    if (!goal) { Q.toast(t("Напишите, что сделать — например «забронируй столик на двоих на 19:00»", "Type what to do — e.g. «book a table for two at 7 pm»"), "warn", 3200); return null; }
    // Round 30 (r30-dfy-remote): «@server …» or the target picker under the input (dfy-remote.js) runs it on a machine.
    const aimed = window.MemuDFYRemote ? window.MemuDFYRemote.parseCommand(goal) : { goal, target: "" };
    goal = aimed.goal;
    const st = await status(true);

    if (!st || !st.enabled || (st.ready === false && !(aimed.target && st.model))) { D.offer = ""; D.notice = { text: notReadyText(st), settings: !!st && st.reason !== "missing" }; sync(); return null; }
    D.offer = ""; D.notice = null;
    let created;
    try { created = await net.start(goal, context, aimed.target); } catch (e) { if (e.code !== "dfy_cancelled") Q.toast(t("Не удалось запустить DFY: ", "Could not start DFY: ") + (e.message || e), "error", 4200); return null; }
    if (!created || !created.id) { Q.toast(t("Сервер DFY не вернул задачу", "The DFY server returned no task"), "error"); return null; }
    if (goalText == null) Q.setInput("");
    attach(created.id, Object.assign({ id: created.id, goal, status: created.status || "queued", steps: [] }, created));

    const entry = D.tasks.get(created.id);
    if (entry && Q.state) entry.chat = { id: Q.state().chatId, q: goal };
    Q.toast(t("Агент DFY начал работу", "The DFY agent started"));
    return created.id;
  }
  function attach(id, initial) {
    if (D.tasks.has(id)) return;
    const entry = { task: initial || { id, status: "queued", steps: [] }, stop: null, notified: false };
    D.tasks.set(id, entry);
    remember();
    entry.stop = net.watch(id, (task) => update(id, task));
    sync();
  }
  function update(id, task) {
    const entry = D.tasks.get(id);
    if (!entry || !task) return;
    if (task.gone || task.status === "gone") {

      if (entry.stop) { try { entry.stop(); } catch (_) {} }
      D.tasks.delete(id); remember(); sync();
      return;
    }
    const was = entry.task && entry.task.status;
    entry.task = Object.assign({}, entry.task, task, { id });

    try { if (window.MemuDFYTakeover && window.MemuDFYTakeover.sync) window.MemuDFYTakeover.sync(entry.task); } catch (_) {}
    if (task.status === "waiting_user" && was !== "waiting_user") announce(entry, "question");
    if (FINAL.has(task.status) && !entry.notified) { entry.notified = true; announce(entry, task.status); record(entry); if (entry.stop) { try { entry.stop(); } catch (_) {} entry.stop = null; } }
    render();
  }
  function record(entry) {
    const chat = entry.chat, task = entry.task || {};
    if (!chat || !Q.recordTurn || !Q.state || Q.state().chatId !== chat.id) return;
    const a = task.status === "done" ? String(task.result || t("Готово", "Done"))
      : task.status === "failed" ? t("Не получилось: ", "Failed: ") + String(task.error || "") : t("Отменено.", "Cancelled.");
    Q.recordTurn({ q: chat.q, a: t("Сделать за меня — ", "Do it for me — ") + a.trim() });
  }
  function announce(entry, kind) {
    const goal = String(entry.task.goal || "").slice(0, 80);
    const text = kind === "question" ? t("DFY ждёт вашего ответа: ", "DFY needs your answer: ") + goal
      : kind === "done" ? t("Готово: ", "Done: ") + goal
      : kind === "failed" ? t("DFY не справился: ", "DFY failed: ") + goal : t("Отменено: ", "Cancelled: ") + goal;

    if (D.visible) { if (kind !== "question") Q.toast(text, kind === "failed" ? "error" : "", 3600); return; }

    try {
      if (window.Notification && Notification.permission === "granted") new Notification("MemuAI", { body: text });
      else if (window.Notification && Notification.permission === "default") Notification.requestPermission().then((p) => { if (p === "granted") new Notification("MemuAI", { body: text }); }).catch(() => {});
    } catch (_) {}
    entry.unseen = true;
  }
  async function onClick(event) {
    const btn = event.target.closest("[data-qdfy]");
    if (!btn) return;
    const id = btn.dataset.id, act = btn.dataset.qdfy;
    if (act === "offer") { run(); return; }
    if (act === "notice-no") { D.notice = null; sync(); return; }
    if (act === "settings") {
      D.notice = null; sync();
      Q.api("/api/quick/window", { body: { action: "handoff", handoff: { settings: "dfy" } } }).catch(() => {});
      if (!Q.native) Q.toast(t("Настройки открываются в окне MemuAI", "Settings open in the MemuAI window"));
      return;
    }
    if (act === "offer-no") { D.offer = ""; D.offerDismissed = Q.getInput().trim(); sync(); Q.focus && Q.focus(); return; }
    if (act === "takeover") { const entry = D.tasks.get(id); takeover().then((m) => m.open(id, { compact: true, question: entry && entry.task && entry.task.question })).catch((e) => Q.toast(e.message, "error")); return; }
    if (act === "giveback") { btn.disabled = true; takeover().then((m) => m.giveBack(id)).catch((e) => Q.toast(e.message, "error")).finally(() => { btn.disabled = false; }); return; }
    if (act === "dismiss") { const e = D.tasks.get(id); if (e && e.stop) e.stop(); D.tasks.delete(id); remember(); sync(); return; }
    if (act === "shot") { D.bigShot = D.bigShot === id ? "" : id; render(); return; }
    btn.disabled = true;
    const entry = D.tasks.get(id);
    const asked = entry && entry.task && entry.task.question ? entry.task.question.id : "";
    if (act !== "cancel") {

      if (!asked || (entry && entry.answering === asked)) { btn.disabled = false; return; }
      entry.answering = asked;
      render();
    }
    const net_answer = (payload) => net.answer(id, Object.assign({ question_id: asked || undefined }, payload));
    try {
      if (act === "cancel") update(id, await net.cancel(id).catch(() => ({ status: "cancelled" })) || { status: "cancelled" });
      else if (act === "approve") await net_answer({ approve: true });
      else if (act === "deny") await net_answer({ approve: false });
      else if (act === "choice") await net_answer({ answer: btn.dataset.value || "" });
      else if (act === "use-secret") await net_answer({ secret_ref: btn.dataset.value || "" });
      else if (act === "decline-secret" || act === "skip") await net_answer({ approve: false });
      else if (act === "resend") await net_answer({ resend: true });
    } catch (e) {
      if (entry && entry.answering === asked) entry.answering = "";
      Q.toast(t("Ошибка: ", "Error: ") + (e.message || e), "error");
      render();
    }
    btn.disabled = false;
  }
  async function onSubmit(event) {
    const form = event.target.closest("form.qdfy-q");
    if (!form) return;
    event.preventDefault();
    const id = form.dataset.id, kind = form.dataset.kind, F = forms();
    const entry = D.tasks.get(id), q = (entry && entry.task && entry.task.question) || {};
    const inputs = Array.from(form.querySelectorAll("input[data-field],select[data-field],input[data-code]"));
    let payload;
    if (kind === "code") {
      const input = form.querySelector("[data-code]");
      const code = F ? F.codeValue(form) : String(input.value || "").replace(/\s/g, "");
      if (!code) { input.focus(); return; }
      payload = { code };
    } else if (kind === "secret") {

      let values = {};
      if (F) values = F.collect(form, q.secret_kind);
      else inputs.forEach((i) => { if (i.value) values[i.dataset.field] = i.value; });
      if (!values) { const bad = form.querySelector("[aria-invalid]"); if (bad) bad.focus(); return; }
      if (!Object.keys(values).length) { if (inputs[0]) inputs[0].focus(); return; }
      const save = form.querySelector("[data-qdfy-save]");
      payload = { secret: { kind: q.secret_kind, label: q.label, domain: q.domain, fields: values, save: save ? save.checked : true } };
    } else {
      const answer = inputs.length ? String(inputs[0].value || "").trim() : "";
      if (!answer) return;
      payload = { answer };
    }
    if (entry && q.id && entry.answering === q.id) return;
    if (entry) entry.answering = q.id || "";

    inputs.forEach((i) => { if (i.tagName === "INPUT") i.value = ""; i.disabled = true; });
    try { await net.answer(id, Object.assign({ question_id: q.id || undefined }, payload)); } catch (e) { if (entry) entry.answering = ""; Q.toast(t("Не удалось отправить ответ: ", "Could not send the answer: ") + (e.message || e), "error"); inputs.forEach((i) => { i.disabled = false; }); }
  }

  let offerTimer = 0;
  function watchInput() {
    const input = document.getElementById("qa-input");
    if (!input) return;
    input.addEventListener("input", () => {
      clearTimeout(offerTimer);
      offerTimer = setTimeout(async () => {
        const text = input.value.trim();
        const want = isActionLike(text) && text !== D.offerDismissed;
        if (want === !!D.offer) return;
        if (want) { const st = await status(false); if (!st || !st.enabled || st.ready === false) return; }
        D.offer = want ? text : "";
        sync();
      }, 260);
    });
  }
  Q.on("submit", () => { if (D.offer || D.notice) { D.offer = ""; D.notice = null; sync(); } });
  Q.on("open", () => {
    D.visible = true;
    for (const id of saved()) if (!D.tasks.has(id)) attach(id);
    for (const entry of D.tasks.values()) entry.unseen = false;
    status(true).catch(() => {});
    sync();
  });
  Q.on("close", () => { D.visible = false; });

  Q.on("config", () => { if (D.tasks.size || D.offer || D.notice) render(); });

  Q.on("escape", (e) => {
    const a = document.activeElement;
    if (D.el && a && D.el.contains(a)) { a.blur(); const input = document.getElementById("qa-input"); if (input) input.focus(); e.preventDefault(); return; }
    if (D.offer) { D.offer = ""; D.offerDismissed = Q.getInput().trim(); sync(); e.preventDefault(); }
  });

  window.QuickDFY = { run, isActionLike, attach, status: () => status(true), get tasks() { return Array.from(D.tasks.values()).map((e) => e.task); } };
  watchInput();
  ensurePanel();
  for (const id of saved()) attach(id);
  sync();
})();
