/* MemuAI · «Рефлексия» (Reflect) and «Время и фокус» — Round 27.

   window.MemuReflect — two settings pages:
     html(ctx)       → the Reflect page body (toolbar, report host, report settings, «Что входит в отчёт»)
     focusHtml(ctx)  → the «Время и фокус» page body (quiet hours, break reminder)
     bind(ctx)       → handlers of both pages (ids are rf-* / fx-*)
     mount(ctx)      → loads the report when the Reflect page is shown (the only moment a report is generated)
   ctx (from app.js): { en, pref, esc, api, toast, savePreferences, kit, models, modelName, defaultModel,
                        openPage(id), discuss(text), openWork(), openSkills(), openAgents() }

   window.MemuFocus — the main-window side of «Время и фокус»: beforeSend() (quiet hours reminder before a
   message goes out; resolves false when the user chose «Позже») and activity pings that bring the break
   reminder. Quick Ask has its own plugin (quick/plugins/focus.js). Statistics and the report are made by
   app/reflect.py; this file only draws them. */
(function () {
  "use strict";

  const PERIODS = [
    ["this_month", "Этот месяц", "This month so far"],
    ["last_month", "Прошлый месяц", "Last month"],
    ["3m", "3 месяца", "Past 3 months"],
    ["6m", "6 месяцев", "Past 6 months"],
    ["12m", "Год", "Past year"],
  ];
  const SKILLS = [
    ["delegation", "Делегирование", "Delegation", "Что отдавать ИИ, а что оставлять себе", "What to hand off, and what to keep"],
    ["description", "Описание", "Description", "Контекст и цели, которые вы даёте", "The context and goals you give"],
    ["discernment", "Оценка", "Discernment", "Как вы взвешиваете ответы", "How you weigh what comes back"],
    ["diligence", "Ответственность", "Diligence", "Как вы распоряжаетесь результатом ИИ", "How you own and represent AI output"],
  ];
  const FLUENCY_URL = "https://anthropic.skilljar.com/ai-fluency-framework-foundations";
  const SVG = {
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
    up: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 11v9H4v-9h3zm0 0 4-8c1.7 0 2.6 1 2.3 2.7L12.8 9H18a2 2 0 0 1 2 2.3l-1.2 7A2 2 0 0 1 16.8 20H7"/></svg>',
    down: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 13V4H4v9h3zm0 0 4 8c1.7 0 2.6-1 2.3-2.7L12.8 15H18a2 2 0 0 0 2-2.3l-1.2-7A2 2 0 0 0 16.8 4H7"/></svg>',
    chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 18.5V6.5A2.5 2.5 0 0 1 7.5 4h9A2.5 2.5 0 0 1 19 6.5v7a2.5 2.5 0 0 1-2.5 2.5H9l-4 2.5z"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    moon: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    cup: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9h13v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V9z"/><path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17M8 3v3M12 3v3"/></svg>',
    heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/></svg>',
    lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></svg>',
    skill: {
      delegation: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12h10M10 6l6 6-6 6"/><path d="M20 5v14"/></svg>',
      description: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6h14M5 11h14M5 16h9"/></svg>',
      discernment: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.5-4.5"/></svg>',
      diligence: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3z"/><path d="m9 12 2 2 4-4"/></svg>',
    },
  };

  let ctx = null;
  let period = "this_month";     // Round 34: the first chip, the month in progress (was «Прошлый месяц» while «Этот месяц» stood first)
  const views = new Map();        // period:lang -> last /api/reflect answer (instant when switching back)
  let pollTimer = 0;
  let loadToken = 0;
  let tipBound = false;

  const en = () => !!(ctx && ctx.en);
  const L = (ru, enText) => (en() ? enText : ru);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const lang = () => (en() ? "en" : "ru");
  const $ = (sel) => document.querySelector(sel);
  const host = () => document.getElementById("rf-report");

  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }
  const convWord = (n) => (en() ? (n === 1 ? "conversation" : "conversations") : plural(n, "разговор", "разговора", "разговоров"));
  const dayWord = (n) => (en() ? (n === 1 ? "day" : "days") : plural(n, "день", "дня", "дней"));

  function duration(minutes) {
    const m = Math.max(0, Math.round(minutes));
    const h = Math.floor(m / 60), r = m % 60;
    if (en()) return h ? `${h} h${r ? " " + r + " min" : ""}` : `${r} min`;
    return h ? `${h} ч${r ? " " + r + " мин" : ""}` : `${r} мин`;
  }
  function weekdayName(index) {
    const monday = new Date(2024, 0, 1 + index);   // 1 Jan 2024 was a Monday
    const name = monday.toLocaleDateString(lang(), { weekday: "long" });
    return name.charAt(0).toUpperCase() + name.slice(1);
  }
  function hourText(h) {
    if (en()) {
      const fmt = (x) => { const v = x % 24; return `${v % 12 || 12} ${v < 12 ? "AM" : "PM"}`; };
      return { main: fmt(h), range: `${fmt(h)} – ${fmt(h + 1)}` };
    }
    const fmt = (x) => `${String(x % 24).padStart(2, "0")}:00`;
    return { main: fmt(h), range: `${fmt(h)} – ${fmt(h + 1)}` };
  }
  function dayLabel(iso, withYear = false) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(lang(), withYear ? { day: "numeric", month: "short", year: "numeric" } : { day: "numeric", month: "short" });
  }
  function ago(ms) {
    const diff = (Date.now() - ms) / 1000;
    const rtf = new Intl.RelativeTimeFormat(lang(), { numeric: "auto" });
    if (diff < 60) return L("только что", "just now");
    if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
    if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
    return rtf.format(-Math.round(diff / 86400), "day");
  }
  function modelName(id) {
    try { return ctx.modelName ? ctx.modelName(id) : id; } catch (e) { return id; }
  }
  function reportModel() {
    const p = ctx.pref;
    const chosen = p("reflect_model", "");
    if (chosen && (ctx.models || []).some((m) => m.id === chosen)) return chosen;
    return (typeof ctx.defaultModel === "function" ? ctx.defaultModel() : ctx.defaultModel) || "";
  }

  /* ============================================================ page markup */

  function html(c) {
    ctx = c;
    const K = c.kit, p = c.pref;
    const enabled = p("reflect_enabled", true) !== false;
    const models = (c.models || []).slice().sort((a, b) => modelName(a.id).localeCompare(modelName(b.id), lang()));
    const fallback = (typeof c.defaultModel === "function" ? c.defaultModel() : c.defaultModel) || "";
    const modelOptions = [["", L("Как в чате", "Same as chat") + (fallback ? ` — ${modelName(fallback)}` : "")]].concat(models.map((m) => [m.id, modelName(m.id)]));
    const periodOptions = PERIODS.map(([id, ru, enText]) => [id, L(ru, enText)]);
    const toolbar = `<div class="rf-toolbar" ${enabled ? "" : "hidden"}>
        ${K.select("rf-period", periodOptions, period, { attrs: `aria-label="${esc(L("Период", "Period"))}"` })}
        <button type="button" class="rf-icon-btn" id="rf-refresh" title="${esc(L("Составить заново", "Regenerate"))}" aria-label="${esc(L("Составить заново", "Regenerate"))}">${SVG.refresh}</button>
        <span class="rf-meta" id="rf-meta" aria-live="polite"></span>
      </div>`;
    const off = `<div class="rf-off" id="rf-off" ${enabled ? "hidden" : ""}>
        <span class="rf-off-icon" aria-hidden="true">${SVG.lock}</span>
        <div><b>${L("Рефлексия выключена", "Reflect is off")}</b>
        <p>${L("MemuAI не составляет отчёты и не читает историю для них. Сохранённые отчёты удалены. Тихие часы и перерывы продолжают работать.", "MemuAI doesn't make reports or read your history for them. Saved reports were deleted. Quiet hours and breaks keep working.")}</p>
        <button type="button" class="pbtn sx-btn-sm" id="rf-turn-on">${L("Включить Рефлексию", "Turn on Reflect")}</button></div>
      </div>`;
    const settings = K.section({ id: "rf-settings", title: L("Настройки отчёта", "Report settings"), body:
      K.row({ label: L("Включить Рефлексию", "Turn on Reflect"), desc: L("Отчёт составляется, только когда вы открываете этот раздел. При выключении сохранённые отчёты удаляются.", "A report is made only when you open this page. Turning it off deletes saved reports."),
        key: "reflect_enabled", def: true, keywords: "reflect рефлексия отчёт report recap итоги включить enable",
        control: K.toggle("rf-enabled", enabled) })
      + K.row({ label: L("Модель для отчёта", "Model for the report"), desc: L("Пишет сводку, темы и наблюдения. Цифры и график MemuAI считает сам, без модели.", "Writes the summary, topics and observations. Numbers and the chart are counted by MemuAI itself."),
        keywords: "model модель reflect рефлексия", when: "rf-enabled",
        control: K.select("rf-model", modelOptions, p("reflect_model", ""), { width: 240 }) })
      + K.row({ label: L("Учитывать Быстрый доступ", "Include Quick Ask"), desc: L("Чаты окна Alt+Space", "Chats from the Alt+Space window"), key: "reflect_include_quick", def: true, when: "rf-enabled",
        keywords: "quick ask быстрый доступ reflect", control: K.toggle("rf-quick", p("reflect_include_quick", true) !== false) })
      + K.row({ label: L("Учитывать Telegram", "Include Telegram"), desc: L("Чаты с ботом MemuAI в Telegram", "Chats with the MemuAI bot in Telegram"), key: "reflect_include_telegram", def: true, when: "rf-enabled",
        keywords: "telegram телеграм reflect", control: K.toggle("rf-telegram", p("reflect_include_telegram", true) !== false) }) });
    const privacy = K.section({ id: "rf-privacy", title: L("Что входит в отчёт", "What's in your report"), card: false, keywords: "privacy приватность конфиденциальность что входит",
      body: K.more({ id: "rf-privacy-more", title: L("Как MemuAI составляет отчёт", "How MemuAI makes the report"), body: `<ul class="rf-privacy-list">
        <li>${L("Берутся сохранённые чаты MemuAI (обычные, Work и MemuCode) и, если включено, Быстрого доступа и Telegram.", "Saved MemuAI chats (regular, Work and MemuCode) and, if enabled, Quick Ask and Telegram are used.")}</li>
        <li>${L("<b>Временные чаты не учитываются никогда</b> — они и не сохраняются.", "<b>Temporary chats are never included</b> — they aren't saved at all.")}</li>
        <li>${L("Вложения, файлы и картинки не читаются — только текст разговоров. Если ИИ пересказал файл, в отчёт может попасть пересказ, но не сам файл.", "Attachments, files and images are not read — only the text of conversations. If the AI summarised a file, the summary may count, never the file.")}</li>
        <li>${L("Модели отправляются только заголовки и короткие выдержки ваших сообщений, а не переписка целиком. Цифры, график и время считаются на компьютере.", "The model only gets titles and short excerpts of your messages, not whole chats. Numbers, the chart and time are counted on your computer.")}</li>
        <li>${L("Личные и тяжёлые темы не отправляются выдержками, никогда не идут первыми и не получают процентов или счётчиков. Если нужно — рядом будут ресурсы поддержки.", "Personal and difficult topics are never sent as excerpts, never lead the report and never get percentages or counts. Where appropriate, support resources are shown.")}</li>
        <li>${L("Отчёт хранится только у вас (папка данных MemuAI) и больше ни для чего не используется. Темы и проценты пишет ИИ — возможны неточности.", "The report stays on your computer (the MemuAI data folder) and isn't used for anything else. Topics and shares are written by AI and can be imprecise.")}</li>
      </ul>` }) });
    return `${off}${toolbar}<div class="rf-report" id="rf-report" aria-busy="false" ${enabled ? "" : "hidden"}></div>${settings}${privacy}`;
  }

  function focusHtml(c) {
    ctx = c;
    const K = c.kit, p = c.pref;
    const days = new Set(p("focus_quiet_days", [0, 1, 2, 3, 4, 5, 6]) || []);
    const dayButtons = [0, 1, 2, 3, 4, 5, 6].map((d) => {
      const name = new Date(2024, 0, 1 + d).toLocaleDateString(lang(), { weekday: "short" });
      return `<button type="button" class="fx-day" data-fx-day="${d}" aria-pressed="${days.has(d)}">${esc(name.replace(".", ""))}</button>`;
    }).join("");
    const minutes = +p("focus_break_minutes", 60) || 60;
    const options = [15, 20, 30, 45, 60, 90, 120, 180, 240];
    if (!options.includes(minutes)) options.push(minutes), options.sort((a, b) => a - b);
    return K.section({ id: "fx-quiet", icon: SVG.moon, title: L("Тихие часы", "Quiet hours"), desc: L("Время, которое вы оставили для другого. Если в эти часы вы откроете Быстрый доступ или отправите сообщение — здесь или в Telegram, — MemuAI мягко напомнит о вашем решении. Продолжить можно всегда.", "Time you set aside for something else. If you open Quick Ask or send a message during it — here or in Telegram — MemuAI gently reminds you. You can always continue."),
        body: K.row({ label: L("Включить тихие часы", "Turn on quiet hours"), key: "focus_quiet_enabled", def: false, keywords: "quiet hours тихие часы focus фокус ночь night",
          control: K.toggle("fx-quiet-on", !!p("focus_quiet_enabled", false)) })
        + K.row({ label: L("Дни", "Days"), desc: L("Окно через полночь относится ко дню, в который начинается", "A window across midnight belongs to the day it starts"), when: "fx-quiet-on", stack: true, keywords: "days дни недели weekdays",
          control: `<div class="fx-days" role="group" aria-label="${esc(L("Дни недели", "Days of the week"))}">${dayButtons}</div>` })
        + K.row({ label: L("Время", "Time"), when: "fx-quiet-on", keywords: "time время начало конец start end",
          control: `<div class="fx-times"><input class="pinput sx-input" type="time" id="fx-quiet-start" value="${esc(p("focus_quiet_start", "22:00"))}" aria-label="${esc(L("Начало", "Start"))}"><span aria-hidden="true">—</span><input class="pinput sx-input" type="time" id="fx-quiet-end" value="${esc(p("focus_quiet_end", "07:00"))}" aria-label="${esc(L("Конец", "End"))}"></div>` })
        + `<div class="fx-status" id="fx-quiet-status" data-sx-when="fx-quiet-on"></div>` })
      + K.section({ id: "fx-break", icon: SVG.cup, title: L("Напоминание о перерыве", "Break reminder"), desc: L("Ненавязчивое уведомление, когда за день вы провели с MemuAI выбранное время — в окне, Быстром доступе и Telegram вместе. Паузы дольше 10 минут не считаются, счёт обнуляется в полночь.", "A quiet nudge once you've spent the chosen time with MemuAI in a day — the window, Quick Ask and Telegram together. Pauses over 10 minutes don't count; the count resets at midnight."),
        body: K.row({ label: L("Напоминать о перерыве", "Remind me to take a break"), key: "focus_break_enabled", def: false, keywords: "break перерыв отдых reminder напоминание",
          control: K.toggle("fx-break-on", !!p("focus_break_enabled", false)) })
        + K.row({ label: L("Через сколько", "After"), desc: L("Времени с MemuAI за день", "Of time with MemuAI in a day"), when: "fx-break-on", key: "focus_break_minutes", def: 60, keywords: "minutes минуты",
          control: K.select("fx-break-minutes", options.map((m) => [m, duration(m)]), minutes, { width: 150 }) }) })
      + K.note(L("Это ваши собственные границы, а не лимит: напоминания всегда можно отклонить. Они работают независимо от Рефлексии.", "These are boundaries you choose, not limits: reminders can always be dismissed. They work independently of Reflect."));
  }

  /* ============================================================ binding */

  function bind(c) {
    ctx = c;
    const save = (patch) => c.savePreferences(patch);
    const on = (id, type, fn) => { const el = document.getElementById(id); if (el) el[type] = fn; };
    on("rf-period", "onchange", (e) => { period = e.target.value; load(); });
    on("rf-refresh", "onclick", () => generate(true));
    on("rf-enabled", "onchange", async (e) => {
      const value = e.target.checked;
      await save({ reflect_enabled: value });
      if (!value) {
        stopPolling(); views.clear();
        try { await c.api("/api/reflect/clear", { method: "POST", body: {} }); } catch (err) {}
      }
      applyEnabled(value);
      if (value) load();
    });
    on("rf-turn-on", "onclick", () => { const el = document.getElementById("rf-enabled"); if (el) { el.checked = true; el.dispatchEvent(new Event("change", { bubbles: true })); } });
    on("rf-model", "onchange", (e) => save({ reflect_model: e.target.value }));
    on("rf-quick", "onchange", (e) => { save({ reflect_include_quick: e.target.checked }).then(() => { views.clear(); load(); }); });
    on("rf-telegram", "onchange", (e) => { save({ reflect_include_telegram: e.target.checked }).then(() => { views.clear(); load(); }); });
    // «Время и фокус»
    on("fx-quiet-on", "onchange", (e) => { save({ focus_quiet_enabled: e.target.checked }).then(focusStatus); });
    document.querySelectorAll("[data-fx-day]").forEach((btn) => btn.onclick = () => {
      btn.setAttribute("aria-pressed", String(btn.getAttribute("aria-pressed") !== "true"));
      const daysOn = [...document.querySelectorAll("[data-fx-day]")].filter((b) => b.getAttribute("aria-pressed") === "true").map((b) => +b.dataset.fxDay);
      save({ focus_quiet_days: daysOn }).then(focusStatus);
    });
    const saveTimes = () => {
      const start = document.getElementById("fx-quiet-start")?.value, end = document.getElementById("fx-quiet-end")?.value;
      if (start && end) save({ focus_quiet_start: start, focus_quiet_end: end }).then(focusStatus);
    };
    on("fx-quiet-start", "onchange", saveTimes);
    on("fx-quiet-end", "onchange", saveTimes);
    on("fx-break-on", "onchange", (e) => save({ focus_break_enabled: e.target.checked }));
    on("fx-break-minutes", "onchange", (e) => save({ focus_break_minutes: +e.target.value }));
    focusStatus();
  }

  function focusStatus() {
    const el = document.getElementById("fx-quiet-status");
    if (!el || !ctx) return;
    const prefs = prefsSnapshot();
    const now = MemuFocus.quietNow(prefs, new Date());
    if (!prefs.focus_quiet_days.length) el.innerHTML = `<span class="fx-dot warn"></span>${esc(L("Не выбран ни один день — тихие часы не действуют", "No days selected — quiet hours are not active"))}`;
    else if (prefs.focus_quiet_start === prefs.focus_quiet_end) el.innerHTML = `<span class="fx-dot warn"></span>${esc(L("Начало и конец совпадают — выберите разное время", "Start and end are the same — pick different times"))}`;
    else if (now) el.innerHTML = `<span class="fx-dot on"></span>${esc(L(`Сейчас тихие часы — до ${now.until}`, `Quiet hours now — until ${now.until}`))}`;
    else el.innerHTML = `<span class="fx-dot"></span>${esc(L(`Каждый выбранный день с ${prefs.focus_quiet_start} до ${prefs.focus_quiet_end}`, `Every selected day from ${prefs.focus_quiet_start} to ${prefs.focus_quiet_end}`))}`;
  }
  function prefsSnapshot() {
    const p = ctx ? ctx.pref : (k, d) => d;
    return { focus_quiet_enabled: !!p("focus_quiet_enabled", false), focus_quiet_days: p("focus_quiet_days", [0, 1, 2, 3, 4, 5, 6]) || [],
      focus_quiet_start: p("focus_quiet_start", "22:00"), focus_quiet_end: p("focus_quiet_end", "07:00") };
  }

  function applyEnabled(value) {
    const off = document.getElementById("rf-off"), bar = document.querySelector(".rf-toolbar"), report = host();
    if (off) off.hidden = value;
    if (bar) bar.hidden = !value;
    if (report) report.hidden = !value;
  }

  /* ============================================================ data flow */

  function key() { return period + ":" + lang(); }
  function stopPolling() { clearTimeout(pollTimer); pollTimer = 0; }

  function mount(c) {
    ctx = c;
    if (ctx.pref("reflect_enabled", true) === false) { applyEnabled(false); return; }
    load();
  }

  async function load() {
    stopPolling();
    const token = ++loadToken;
    const cached = views.get(key());
    if (cached) render(cached); else renderSkeleton();
    let data;
    try {
      data = await ctx.api(`/api/reflect?period=${encodeURIComponent(period)}&lang=${lang()}`);
    } catch (e) {
      if (token === loadToken) renderError(e.message || String(e), load);
      return;
    }
    if (token !== loadToken) return;
    if (data && data.enabled === false) { applyEnabled(false); return; }
    views.set(key(), data);
    render(data);
    if (data.job && data.job.status === "running") poll(token);
    else if (data.state === "ready" && !data.report && !(data.job && data.job.status === "error")) generate(false);
  }

  async function generate(force) {
    if (!ctx) return;
    const data = views.get(key());
    if (data && data.state !== "ready") return;     // nothing to write about: the empty state says so
    const model = reportModel();
    if (!model) { renderError(L("Выберите модель для отчёта в настройках ниже или модель по умолчанию в разделе «Модели».", "Pick a model for the report below, or a default model in Models."), false); return; }
    stopPolling();
    const token = ++loadToken;
    renderBusy({ stage: "reading", done: 0, total: 1 }, force);
    let answer;
    try {
      answer = await ctx.api("/api/reflect/generate", { method: "POST", body: { period, lang: lang(), model } });
    } catch (e) {
      if (token === loadToken) renderError(e.message || String(e), true);
      return;
    }
    if (token !== loadToken) return;
    poll(token, answer && answer.job);
  }

  function poll(token, job) {
    if (job) renderBusy(job);
    pollTimer = setTimeout(async () => {
      if (token !== loadToken) return;
      const q = `period=${encodeURIComponent(period)}&lang=${lang()}`;
      let data;
      try {
        // While the model writes only the job is polled: the full answer re-reads every chat (seconds on a big history).
        const status = await ctx.api(`/api/reflect/status?${q}`);
        if (token !== loadToken) return;
        if (status.job && status.job.status === "running") { renderBusy(status.job); poll(token); return; }
        data = await ctx.api(`/api/reflect?${q}`);
      } catch (e) { if (token === loadToken) renderError(e.message || String(e), true); return; }
      if (token !== loadToken) return;
      views.set(key(), data);
      render(data);
    }, 900);
  }

  /* ============================================================ rendering */

  function setMeta(text) { const el = document.getElementById("rf-meta"); if (el) el.textContent = text || ""; }
  function setBusy(on) {
    const el = host(); if (el) el.setAttribute("aria-busy", String(on));
    const btn = document.getElementById("rf-refresh");
    if (btn) { btn.classList.toggle("busy", on); btn.disabled = on; }
  }

  function renderSkeleton() {
    const el = host(); if (!el) return;
    setBusy(true);
    setMeta("");
    el.innerHTML = `<div class="rf-skel" aria-label="${esc(L("Загрузка", "Loading"))}">
      <i class="rf-sk h"></i><i class="rf-sk p"></i><i class="rf-sk p s"></i>
      <div class="rf-stats">${"<i class='rf-sk tile'></i>".repeat(3)}</div><i class="rf-sk chart"></i></div>`;
  }

  /* Statistics are already known while the model writes: they stay, the narrative parts show a skeleton. */
  function renderBusy(job, force) {
    const el = host(); if (!el) return;
    const data = views.get(key());
    const stage = job.stage === "writing" ? L("Пишу обзор…", "Writing the recap…")
      : job.total > 1 ? L(`Читаю разговоры… ${job.done} из ${job.total - 1}`, `Reading conversations… ${job.done} of ${job.total - 1}`)
      : L("Читаю разговоры…", "Reading conversations…");
    setMeta(stage);
    if (!data || !data.stats) { renderSkeleton(); setMeta(stage); return; }
    setBusy(true);
    const keep = data.report && !force ? data.report : null;
    el.innerHTML = (keep ? intro(keep) : `<div class="rf-progress" role="status"><span class="rf-spin" aria-hidden="true"></span><span>${esc(stage)}</span></div><i class="rf-sk h"></i><i class="rf-sk p"></i><i class="rf-sk p s"></i>`)
      + timeSection(data)
      + (keep ? topicsSection(keep) + skillsSection(keep) : `<section class="rf-sec"><i class="rf-sk h s"></i><i class="rf-sk bar"></i><i class="rf-sk bar"></i><i class="rf-sk bar"></i></section>`)
      + questionCard(data);
    wire(data);
  }

  /* retry: true → write the report again, a function → run it (reload), false → no button. */
  function renderError(message, retry) {
    const el = host(); if (!el) return;
    setBusy(false);
    setMeta("");
    el.innerHTML = `<div class="rf-state rf-error" role="alert"><b>${esc(L("Не получилось", "Something went wrong"))}</b><p>${esc(message)}</p>
      ${retry !== false ? `<button type="button" class="pbtn sx-btn-sm" data-rf-retry>${SVG.refresh}<span>${esc(L("Повторить", "Try again"))}</span></button>` : ""}</div>`;
    el.querySelector("[data-rf-retry]")?.addEventListener("click", () => (typeof retry === "function" ? retry() : generate(true)));
  }

  function render(data) {
    const el = host(); if (!el || !data) return;
    setBusy(false);
    const refresh = document.getElementById("rf-refresh");
    if (refresh) refresh.disabled = data.state !== "ready";
    if (data.state === "empty") {
      setMeta("");
      el.innerHTML = `<div class="rf-state"><span class="rf-state-icon" aria-hidden="true">${SVG.chat}</span><b>${esc(L("За этот период разговоров нет", "No conversations in this period"))}</b>
        <p>${esc(L("Выберите период подлиннее — или вернитесь, когда поговорите с MemuAI. Временные чаты сюда не попадают.", "Pick a longer period — or come back after a few chats with MemuAI. Temporary chats are never included."))}</p></div>`;
      return;
    }
    let top = "";
    if (data.state === "few") {
      setMeta("");
      top = `<div class="rf-state rf-few"><b>${esc(L("Пока мало разговоров для обзора", "Not enough conversations for a recap yet"))}</b>
        <p>${esc(L(`Темы и навыки появятся, когда в периоде будет хотя бы ${data.min_conversations} разговора. Цифры ниже уже настоящие.`, `Topics and skills appear once the period has at least ${data.min_conversations} conversations. The numbers below are already real.`))}</p></div>`;
    } else if (data.job && data.job.status === "error" && !data.report) {
      setMeta("");
      top = `<div class="rf-state rf-error" role="alert"><b>${esc(L("Не удалось составить обзор", "Couldn't write the recap"))}</b><p>${esc(data.job.error)}</p>
        <button type="button" class="pbtn sx-btn-sm" data-rf-retry>${SVG.refresh}<span>${esc(L("Повторить", "Try again"))}</span></button>
        ${data.job.detail ? `<details class="rf-detail"><summary>${esc(L("Подробности", "Details"))}</summary><code>${esc(data.job.detail)}</code></details>` : ""}</div>`;
    } else if (data.report) {
      setMeta(L(`Составлен ${ago(data.generated_at)}`, `Made ${ago(data.generated_at)}`) + (data.model ? ` · ${modelName(data.model)}` : ""));
      top = (data.stale ? `<div class="rf-stale"><span>${esc(L("С тех пор были новые разговоры.", "There have been new conversations since."))}</span><button type="button" class="rf-link" data-rf-retry>${esc(L("Обновить отчёт", "Update the recap"))}</button></div>` : "")
        + (data.job && data.job.status === "error" ? `<div class="rf-stale rf-stale--err" role="alert"><span>${esc(data.job.error)}</span><button type="button" class="rf-link" data-rf-retry>${esc(L("Повторить", "Try again"))}</button></div>` : "")
        + intro(data.report);
    }
    el.innerHTML = top + timeSection(data)
      + (data.report && data.state === "ready" ? topicsSection(data.report) + skillsSection(data.report) : "")
      + (data.support ? supportCard(data.support) : "")
      + questionCard(data)
      + (data.report && data.state === "ready" ? feedbackRow(data) : "");
    wire(data);
  }

  function intro(report) {
    return `<header class="rf-intro"><h2 class="rf-headline">${esc(report.headline)}</h2><p class="rf-summary">${esc(report.summary)}</p></header>`;
  }

  function timeSection(data) {
    const s = data.stats || {};
    const wd = s.most_active_weekday, peak = s.peak_hour;
    const busiest = s.busiest_day;
    const h = peak == null ? null : hourText(peak);
    const t = s.time || {};
    const tiles = [
      [L("Самый активный день", "Most active day"), wd == null ? "—" : weekdayName(wd), busiest ? L(`рекорд — ${dayLabel(busiest.date)}, ${busiest.conversations} ${convWord(busiest.conversations)}`, `record — ${dayLabel(busiest.date)}, ${busiest.conversations} ${convWord(busiest.conversations)}`) : ""],
      [L("Пиковый час", "Peak hour"), h ? h.main : "—", h ? h.range : ""],
      [L("Всего разговоров", "Total conversations"), String(s.conversations || 0), L(`за ${s.active_days || 0} ${dayWord(s.active_days || 0)} из ${s.days || 0}`, `on ${s.active_days || 0} of ${s.days || 0} ${dayWord(s.days || 0)}`)],
    ];
    const help = L(`Оценка по времени сообщений: пауза дольше ${t.idle_gap || 20} минут начинает новую сессию, к каждой добавляется пара минут на чтение ответа.`, `Estimated from message times: a pause over ${t.idle_gap || 20} minutes starts a new session, and each session gets a couple of minutes for reading the last answer.`);
    return `<section class="rf-sec" aria-labelledby="rf-time-h"><h3 class="rf-h" id="rf-time-h">${esc(L("Ваше время с ИИ", "Your time with AI"))}</h3>
      <div class="rf-stats">${tiles.map(([label, value, sub]) => `<div class="rf-tile"><span class="rf-tile-label">${esc(label)}</span><b class="rf-tile-value">${esc(value)}</b>${sub ? `<span class="rf-tile-sub">${esc(sub)}</span>` : ""}</div>`).join("")}</div>
      <div class="rf-timebar"><div class="rf-time-main"><span class="rf-tile-label">${esc(L("Время с ИИ", "Time with AI"))} <span class="rf-help" tabindex="0" role="note" aria-label="${esc(help)}" data-tip="${esc(help)}">?</span></span>
        <b class="rf-time-value">≈ ${esc(duration(t.minutes || 0))}</b></div>
        <span class="rf-time-sub">${esc(L(`${t.sessions || 0} ${plural(t.sessions || 0, "сессия", "сессии", "сессий")} · в среднем ${duration(t.average_session || 0)} · самая долгая ${duration(t.longest_session || 0)}`, `${t.sessions || 0} ${t.sessions === 1 ? "session" : "sessions"} · ${duration(t.average_session || 0)} on average · longest ${duration(t.longest_session || 0)}`))}</span></div>
      ${chart(s.series || [])}
      <button type="button" class="rf-link rf-focus-link" data-rf-open="focus">${SVG.moon}<span>${esc(L("Тихие часы и перерывы", "Set quiet hours and breaks"))}</span>${SVG.arrow}</button>
    </section>`;
  }

  /* Daily bars (weekly beyond ~6 months). SVG scales to the width; labels are HTML so text never stretches. */
  function chart(series) {
    if (!series.length) return "";
    let buckets = series.map((d) => ({ from: d.date, to: d.date, conversations: d.conversations, messages: d.messages, minutes: d.minutes }));
    let weekly = false;
    if (series.length > 190) {
      weekly = true;
      const out = [];
      for (let i = 0; i < series.length; i += 7) {
        const part = series.slice(i, i + 7);
        out.push({ from: part[0].date, to: part[part.length - 1].date, conversations: part.reduce((a, d) => a + d.conversations, 0),
          messages: part.reduce((a, d) => a + d.messages, 0), minutes: part.reduce((a, d) => a + d.minutes, 0) });
      }
      buckets = out;
    }
    const max = Math.max(1, ...buckets.map((b) => b.conversations));
    // A short period must not turn into a few wide blocks: a bar is at most 1/36 of the width, centred in its slot.
    const n = buckets.length, W = 1000, H = 160, slot = W / n, gap = n > 120 ? 1 : n > 60 ? 2 : 4;
    const bw = Math.max(1, Math.min(slot - gap, W / 36));
    const bars = buckets.map((b, i) => {
      const h = b.conversations ? Math.max(4, (b.conversations / max) * (H - 8)) : 2;
      return `<rect class="rf-bar${b.conversations ? "" : " zero"}" x="${(i * slot + (slot - bw) / 2).toFixed(2)}" y="${(H - h).toFixed(2)}" width="${bw.toFixed(2)}" height="${h.toFixed(2)}" rx="${Math.min(3, bw / 2).toFixed(1)}" data-i="${i}"/>`;
    }).join("");
    const label = (i) => esc(dayLabel(buckets[i].from));
    const ticks = n > 2 ? [0, Math.floor((n - 1) / 2), n - 1] : [0, n - 1];
    const sumText = L(`${weekly ? "По неделям" : "По дням"}: разговоры, максимум ${max}`, `${weekly ? "Weekly" : "Daily"} conversations, max ${max}`);
    return `<figure class="rf-chart" data-weekly="${weekly ? 1 : 0}"><figcaption class="rf-chart-cap"><span>${esc(weekly ? L("Активность по неделям", "Weekly activity") : L("Активность по дням", "Daily activity"))}</span><span class="rf-chart-max">${esc(L("макс.", "max"))} ${max}</span></figcaption>
      <div class="rf-chart-plot"><i class="rf-grid" style="bottom:100%"></i><i class="rf-grid" style="bottom:50%"></i>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(sumText)}">${bars}</svg>
      <div class="rf-tip" hidden></div></div>
      <div class="rf-axis">${[...new Set(ticks)].map((i) => {
        // Under its bar when bars are wide; pinned to the edges on long periods (a centred label would stick out).
        const edge = n > 12 && (i === 0 || i === n - 1);
        const style = edge ? (i === 0 ? "left:0;transform:none" : "left:100%;transform:translateX(-100%)") : `left:${((i + 0.5) / n) * 100}%`;
        return `<span style="${style}">${label(i)}</span>`;
      }).join("")}</div>
      <script type="application/json" class="rf-chart-data">${JSON.stringify(buckets).replace(/</g, "\\u003c")}</script></figure>`;
  }

  function topicsSection(report) {
    const topics = report.topics || [];
    if (!topics.length) return "";
    const tones = topics.map((_, i) => `var(--rf-c${(i % 6) + 1})`);
    return `<section class="rf-sec" aria-labelledby="rf-topics-h"><h3 class="rf-h" id="rf-topics-h">${esc(L("На что ушло время", "What you spent time on"))}</h3>
      <div class="rf-stack" role="img" aria-label="${esc(topics.map((t) => `${t.name} ${t.percent}%`).join(", "))}">${topics.map((t, i) => `<i style="flex:${t.percent};background:${tones[i]}"></i>`).join("")}</div>
      <ul class="rf-topics">${topics.map((t, i) => `<li class="rf-topic"><span class="rf-swatch" style="background:${tones[i]}" aria-hidden="true"></span>
        <div class="rf-topic-text"><b>${esc(t.name)}</b>${t.description ? `<span>${esc(t.description)}</span>` : ""}</div><span class="rf-pct">${t.percent}%</span></li>`).join("")}</ul></section>`;
  }

  function actionButton(action) {
    const map = {
      project: [L("Создать Work-проект", "Start a Work project"), "work"],
      prompt: [L("Открыть системные промпты", "Open system prompts"), "prompts"],
      skill: [L("Открыть навыки", "Open skills"), "skills"],
      memory: [L("Открыть память", "Open memory"), "profile"],
      agent: [L("Создать мемеса", "Create an agent"), "agents"],
    };
    const row = map[action];
    return row ? `<button type="button" class="pbtn ghost sx-btn-sm rf-step-btn" data-rf-action="${row[1]}">${esc(row[0])}</button>` : "";
  }

  function skillsSection(report) {
    const skills = report.skills || {};
    return `<section class="rf-sec" aria-labelledby="rf-skills-h"><h3 class="rf-h" id="rf-skills-h">${esc(L("Развитие навыков", "Expanding your skills"))}</h3>
      <p class="rf-sec-desc">${esc(L("Наблюдения по вашим разговорам в рамках модели AI Fluency «4D».", "Observations from your own conversations, framed by the 4D AI Fluency framework."))}</p>
      <div class="rf-skills">${SKILLS.map(([id, ru, enText, dRu, dEn]) => {
        const card = skills[id] || {};
        return `<article class="rf-skill" data-skill="${id}"><header><span class="rf-skill-icon" aria-hidden="true">${SVG.skill[id]}</span><div><b>${esc(L(ru, enText))}</b><span>${esc(L(dRu, dEn))}</span></div></header>
          <p class="rf-obs">${esc(card.observation || "")}</p>
          ${card.next_step ? `<div class="rf-step"><span class="rf-step-label">${esc(L("Следующий шаг", "Next step"))}</span><p>${esc(card.next_step)}</p>${actionButton(card.action)}</div>` : ""}</article>`;
      }).join("")}</div>
      <a class="rf-link" href="${FLUENCY_URL}" target="_blank" rel="noopener noreferrer"><span>${esc(L("Подробнее про AI fluency", "Learn about AI fluency"))}</span>${SVG.arrow}</a></section>`;
  }

  function questionCard(data) {
    const q = data.question;
    if (!q || !q.text) return "";
    return `<section class="rf-question" aria-labelledby="rf-q-h"><span class="rf-q-label" id="rf-q-h">${esc(L("Вопрос для размышления", "Something to reflect on"))}</span>
      <p class="rf-q-text">${esc(q.text)}</p>
      <button type="button" class="pbtn sx-btn-sm" data-rf-discuss>${SVG.chat}<span>${esc(L("Обсудить", "Talk it through"))}</span></button></section>`;
  }

  function supportCard(s) {
    return `<aside class="rf-support" role="note"><span class="rf-support-icon" aria-hidden="true">${SVG.heart}</span><div><b>${esc(s.title)}</b><p>${esc(s.text)}</p>
      <a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer" class="rf-link">${esc(s.link_text)} ${SVG.arrow}</a></div></aside>`;
  }

  function feedbackRow(data) {
    const v = data.feedback || "";
    return `<div class="rf-feedback"><span>${esc(L("Обзор получился точным?", "Does this recap look right?"))}</span>
      <button type="button" class="rf-thumb" data-rf-vote="up" aria-pressed="${v === "up"}" aria-label="${esc(L("Да, точный", "Yes, it does"))}" title="${esc(L("Да, точный", "Yes, it does"))}">${SVG.up}</button>
      <button type="button" class="rf-thumb" data-rf-vote="down" aria-pressed="${v === "down"}" aria-label="${esc(L("Есть неточности", "Something's off"))}" title="${esc(L("Есть неточности", "Something's off"))}">${SVG.down}</button>
      <span class="rf-feedback-note" aria-live="polite">${v ? esc(L("Спасибо — отзыв сохранён на этом компьютере.", "Thanks — saved on this computer.")) : ""}</span></div>`;
  }

  function wire(data) {
    const el = host(); if (!el) return;
    el.querySelectorAll("[data-rf-retry]").forEach((b) => b.addEventListener("click", () => generate(true)));
    el.querySelectorAll("[data-rf-open]").forEach((b) => b.addEventListener("click", () => ctx.openPage(b.dataset.rfOpen)));
    el.querySelector("[data-rf-discuss]")?.addEventListener("click", () => {
      const q = data.question?.text || "";
      ctx.discuss(L(`Хочу поразмышлять над вопросом из Рефлексии: «${q}» Задавай мне вопросы и помоги подумать, а не отвечай за меня.`, `I'd like to reflect on a question from my Reflect recap: "${q}" Ask me questions and help me think rather than answering for me.`));
    });
    el.querySelectorAll("[data-rf-action]").forEach((b) => b.addEventListener("click", () => {
      const a = b.dataset.rfAction;
      if (a === "work") ctx.openWork(); else if (a === "skills") ctx.openSkills(); else if (a === "agents") ctx.openAgents(); else ctx.openPage(a);
    }));
    el.querySelectorAll("[data-rf-vote]").forEach((b) => b.addEventListener("click", async () => {
      const value = b.getAttribute("aria-pressed") === "true" ? "" : b.dataset.rfVote;
      el.querySelectorAll("[data-rf-vote]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.rfVote === value)));
      const note = el.querySelector(".rf-feedback-note");
      try {
        await ctx.api("/api/reflect/feedback", { method: "POST", body: { period, lang: lang(), generated_at: data.generated_at, value } });
        data.feedback = value;
        if (note) note.textContent = value ? L("Спасибо — отзыв сохранён на этом компьютере.", "Thanks — saved on this computer.") : "";
      } catch (e) { ctx.toast(e.message || String(e), "err"); }
    }));
    wireChart(el);
    wireHelp(el);
  }

  function wireChart(root) {
    const fig = root.querySelector(".rf-chart");
    if (!fig) return;
    let buckets = [];
    try { buckets = JSON.parse(fig.querySelector(".rf-chart-data").textContent); } catch (e) { return; }
    const plot = fig.querySelector(".rf-chart-plot"), tip = fig.querySelector(".rf-tip"), svg = fig.querySelector("svg");
    const weekly = fig.dataset.weekly === "1";
    const show = (i, x) => {
      const b = buckets[i]; if (!b) return;
      svg.querySelectorAll(".rf-bar.hover").forEach((r) => r.classList.remove("hover"));
      svg.querySelector(`.rf-bar[data-i="${i}"]`)?.classList.add("hover");
      const when = weekly ? `${dayLabel(b.from)} – ${dayLabel(b.to)}` : dayLabel(b.from, true);
      tip.innerHTML = `<b>${esc(when)}</b><span>${b.conversations} ${esc(convWord(b.conversations))}</span><span>${esc(L("время", "time"))} ≈ ${esc(duration(b.minutes))}</span>`;
      tip.hidden = false;
      const w = plot.clientWidth, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(w - tw, x - tw / 2)) + "px";
    };
    plot.addEventListener("pointermove", (e) => {
      const r = plot.getBoundingClientRect();
      const i = Math.min(buckets.length - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * buckets.length)));
      show(i, e.clientX - r.left);
    });
    plot.addEventListener("pointerleave", () => { tip.hidden = true; svg.querySelectorAll(".rf-bar.hover").forEach((r) => r.classList.remove("hover")); });
    // Keyboard: the plot is focusable, ←/→ walk through the days.
    plot.tabIndex = 0;
    let cur = buckets.length - 1;
    plot.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      cur = Math.max(0, Math.min(buckets.length - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
      show(cur, ((cur + 0.5) / buckets.length) * plot.clientWidth);
    });
    plot.addEventListener("blur", () => { tip.hidden = true; });
  }

  function wireHelp(root) {
    root.querySelectorAll(".rf-help").forEach((el) => {
      el.addEventListener("click", (e) => { e.stopPropagation(); el.classList.toggle("open"); });
      el.addEventListener("blur", () => el.classList.remove("open"));
    });
    if (!tipBound) { tipBound = true; document.addEventListener("click", () => document.querySelectorAll(".rf-help.open").forEach((x) => x.classList.remove("open"))); }
  }

  const ICONS = {
    reflect: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19V9M9.3 19V5M14.6 19v-7M20 19v-4"/><path d="M3 21h18"/></svg>',
    focus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/></svg>',
  };
  window.MemuReflect = { html, focusHtml, bind, mount, periods: PERIODS, icons: ICONS };

  /* ============================================================ MemuFocus (main window) */

  const minutesOf = (hhmm) => { const m = /^(\d{1,2}):(\d{2})$/.exec(String(hhmm || "")); return m ? (+m[1]) * 60 + (+m[2]) : -1; };
  /* Same rule as app/reflect.py quiet_now: a window across midnight belongs to the day it starts on (Monday = 0). */
  function quietNow(prefs, now) {
    if (!prefs || !prefs.focus_quiet_enabled) return null;
    const days = new Set((prefs.focus_quiet_days || []).map(Number));
    const start = minutesOf(prefs.focus_quiet_start), end = minutesOf(prefs.focus_quiet_end);
    if (start < 0 || end < 0 || start === end || !days.size) return null;
    const cur = now.getHours() * 60 + now.getMinutes();
    const today = (now.getDay() + 6) % 7;
    const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const at = (dayOffset, minutes) => new Date(midnight.getFullYear(), midnight.getMonth(), midnight.getDate() + dayOffset, 0, minutes).getTime();
    if (start < end) return days.has(today) && cur >= start && cur < end ? { until: prefs.focus_quiet_end, ends_at: at(0, end) } : null;
    if (cur >= start && days.has(today)) return { until: prefs.focus_quiet_end, ends_at: at(1, end) };
    if (cur < end && days.has((today + 6) % 7)) return { until: prefs.focus_quiet_end, ends_at: at(0, end) };
    return null;
  }

  const ACK = "memu_quiet_ack";
  let lastPing = 0;
  let nudge = null;

  function globalPref(k, d) { try { return typeof window.pref === "function" ? window.pref(k, d) : d; } catch (e) { return d; } }
  function isEn() { try { return typeof PG_LANG !== "undefined" ? PG_LANG === "en" : (document.documentElement.lang || "").startsWith("en"); } catch (e) { return false; } }
  const T = (ru, enText) => (isEn() ? enText : ru);
  function currentPrefs() {
    return { focus_quiet_enabled: !!globalPref("focus_quiet_enabled", false), focus_quiet_days: globalPref("focus_quiet_days", [0, 1, 2, 3, 4, 5, 6]) || [],
      focus_quiet_start: globalPref("focus_quiet_start", "22:00"), focus_quiet_end: globalPref("focus_quiet_end", "07:00") };
  }
  function acknowledged(q) {
    try { return +sessionStorage.getItem(ACK) >= q.ends_at; } catch (e) { return false; }
  }
  function acknowledge(q) { try { sessionStorage.setItem(ACK, String(q.ends_at)); } catch (e) {} }

  function card(kind, title, text, buttons) {
    closeCard();
    const el = document.createElement("div");
    el.className = "fx-card";
    el.dataset.kind = kind;
    el.setAttribute("role", kind === "quiet-send" ? "alertdialog" : "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = `<span class="fx-card-icon" aria-hidden="true">${kind === "break" ? SVG.cup : SVG.moon}</span><div class="fx-card-body"><b>${esc(title)}</b><p>${esc(text)}</p>
      <div class="fx-card-actions">${buttons.map((b, i) => `<button type="button" class="pbtn ${b.primary ? "" : "ghost"} sx-btn-sm" data-i="${i}">${esc(b.label)}</button>`).join("")}</div></div>
      <button type="button" class="fx-card-x" aria-label="${esc(T("Закрыть", "Close"))}">×</button>`;
    document.body.appendChild(el);
    const done = (fn) => { closeCard(); fn && fn(); };
    el.querySelectorAll("[data-i]").forEach((btn) => btn.onclick = () => done(buttons[+btn.dataset.i].run));
    el.querySelector(".fx-card-x").onclick = () => done(buttons.find((b) => b.close)?.run);
    el.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); done(buttons.find((b) => b.close)?.run); } });
    nudge = el;
    if (kind === "quiet-send") requestAnimationFrame(() => el.querySelector(".pbtn")?.focus());
    return el;
  }
  function closeCard() { if (nudge) { nudge.remove(); nudge = null; } }

  /* Before a message is sent: during unacknowledged quiet hours ask once; «Продолжить» covers the rest of the window. */
  function beforeSend() {
    const q = quietNow(currentPrefs(), new Date());
    ping();
    if (!q || acknowledged(q)) return Promise.resolve(true);
    return new Promise((resolve) => {
      card("quiet-send", T("Сейчас ваши тихие часы", "These are your quiet hours"),
        T(`Вы решили оставить время до ${q.until} для другого. Отправить сообщение всё равно?`, `You set aside the time until ${q.until} for something else. Send the message anyway?`), [
          { label: T("Продолжить", "Continue"), primary: true, run: () => { acknowledge(q); resolve(true); } },
          { label: T("Вернусь позже", "Come back later"), close: true, run: () => resolve(false) },
        ]);
    });
  }

  /* Opening MemuAI during quiet hours: a calm reminder (no blocking). */
  function remindOnOpen() {
    const q = quietNow(currentPrefs(), new Date());
    if (!q || acknowledged(q) || nudge) return;
    card("quiet", T("Сейчас ваши тихие часы", "These are your quiet hours"),
      T(`Вы оставили время до ${q.until} для другого. Можно продолжить — это только напоминание.`, `You set aside the time until ${q.until} for something else. You can continue — this is just a reminder.`), [
        { label: T("Продолжить", "Continue"), primary: true, close: true, run: () => acknowledge(q) },
      ]);
  }

  const durationRu = (m) => { const h = Math.floor(m / 60), r = m % 60; return h ? `${h} ч${r ? " " + r + " мин" : ""}` : `${r} мин`; };
  const durationEn = (m) => { const h = Math.floor(m / 60), r = m % 60; return h ? `${h} h${r ? " " + r + " min" : ""}` : `${r} min`; };
  function showBreak(minutes) {
    const spent = isEn() ? durationEn(minutes) : durationRu(minutes);
    card("break", T("Пора сделать перерыв?", "Time for a break?"),
      T(`Сегодня вы уже ${spent} с MemuAI. Встаньте, разомнитесь, посмотрите вдаль — разговор подождёт.`, `You've spent ${spent} with MemuAI today. Stand up, stretch, look into the distance — the chat will wait.`), [
        { label: T("Сделаю перерыв", "Take a break"), primary: true, run: () => post("break") },
        { label: T("Через 10 минут", "In 10 minutes"), run: () => post("snooze") },
        { label: T("Скрыть", "Dismiss"), close: true, run: () => post("dismiss") },
      ]);
  }
  function post(action) { fetch("/api/focus/break", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }).catch(() => {}); }

  /* Interaction pings (at most one a minute) feed the shared daily counter (app/reflect.py FocusTracker). */
  function ping(force) {
    if (!globalPref("focus_break_enabled", false)) return;
    const now = Date.now();
    if (!force && now - lastPing < 60_000) return;
    lastPing = now;
    fetch("/api/focus/activity", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then((r) => r.ok ? r.json() : null)
      .then((data) => { if (data && data.break_due) showBreak(data.minutes_today); })
      .catch(() => {});
  }

  function start() {
    if (!document.getElementById("app") && !document.getElementById("chat")) return;   // only the main window
    ["keydown", "pointerdown", "wheel"].forEach((type) => document.addEventListener(type, () => ping(false), { passive: true, capture: true }));
    // Preferences arrive from the server shortly after start: look once they are surely there.
    [2000, 8000].forEach((ms) => setTimeout(remindOnOpen, ms));
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") setTimeout(remindOnOpen, 400); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();

  window.MemuFocus = { quietNow, beforeSend, remindOnOpen, ping, _showBreak: showBreak };
})();
