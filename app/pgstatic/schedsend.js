

"use strict";
(() => {
  if (window.MemuSchedSend) return;

  const MIN_LEAD = 60 * 1000;
  const POLL_MS = 15000;
  const st = {
    items: [], rev: "", summary: { active: 0, by_chat: {}, next_due: 0, notices: 0 },
    timer: 0, poll: 0, watch: 0, busy: new Set(), prompted: new Map(), expanded: false,
    lastChat: null, menu: null, layer: null, noticeShown: new Set(), typingAt: 0, loaded: false,
  };

  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const SVG = {
    calendar: '<svg viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>',
    repeat: '<svg viewBox="0 0 24 24"><path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 0 1 4-4h12"/><path d="M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 0 1-4 4H4"/></svg>',
    wifi: '<svg viewBox="0 0 24 24"><path d="M2 8.8a15 15 0 0 1 20 0"/><path d="M5 12.6a10 10 0 0 1 14 0"/><path d="M8.5 16.3a5 5 0 0 1 7 0"/><circle cx="12" cy="19.6" r=".9"/></svg>',
    sunrise: '<svg viewBox="0 0 24 24"><path d="M12 3v4M5.6 9.6 4.2 8.2M18.4 9.6l1.4-1.4M3 17h18M7 17a5 5 0 0 1 10 0M8 21h8"/></svg>',
    moonset: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    hourglass: '<svg viewBox="0 0 24 24"><path d="M6 3h12M6 21h12M7 3c0 5 5 6 5 9s-5 4-5 9M17 3c0 5-5 6-5 9s5 4 5 9"/></svg>',
    telegram: '<svg viewBox="0 0 24 24"><path d="M21.6 3.2 2.9 10.4c-1.3.5-1.3 1.3-.2 1.6l4.8 1.5 1.8 5.6c.2.6.1.9.8.9.5 0 .7-.2 1-.5l2.4-2.3 4.9 3.6c.9.5 1.6.2 1.8-.8l3.2-15.2c.3-1.3-.5-1.9-1.8-1.6z"/></svg>',
    sendNow: '<svg viewBox="0 0 24 24"><path d="M4 12h12M11 6l6 6-6 6"/><path d="M20 5v14"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/></svg>',
  };
  const ic = (name) => (SVG[name] ? `<span class="ui-icon">${SVG[name]}</span>` : icon(name));

  const pad2 = (n) => String(n).padStart(2, "0");
  function at(dayOffset, h, m = 0, base = new Date()) {
    const d = new Date(base.getFullYear(), base.getMonth(), base.getDate() + dayOffset, h, m, 0, 0);
    return d;
  }
  function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
  function hm(d) { return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }
  function weekdayName(d, long = true) {
    try { return d.toLocaleDateString(en() ? "en-US" : "ru-RU", { weekday: long ? "long" : "short" }); } catch (e) { return ""; }
  }

  function whenLabel(ms, { short = false } = {}) {
    if (!ms) return "";
    const d = new Date(ms), now = new Date();
    const tomorrow = at(1, 0, 0, now), yesterday = at(-1, 0, 0, now);
    let day;
    if (sameDay(d, now)) day = t("сегодня", "today");
    else if (sameDay(d, tomorrow)) day = t("завтра", "tomorrow");
    else if (sameDay(d, yesterday)) day = t("вчера", "yesterday");
    else {
      try {
        day = d.toLocaleDateString(en() ? "en-US" : "ru-RU", { weekday: "short", day: "numeric", month: "short",
          ...(d.getFullYear() !== now.getFullYear() ? { year: "numeric" } : {}) });
      } catch (e) { day = d.toDateString(); }
    }
    return short ? `${day} ${hm(d)}` : `${day} ${t("в", "at")} ${hm(d)}`;
  }
  function relative(ms) {
    const diff = ms - Date.now();
    const abs = Math.abs(diff), min = Math.round(abs / 60000);
    let text;
    if (min < 1) text = t("меньше минуты", "less than a minute");
    else if (min < 60) text = en() ? `${min} min` : `${min} мин`;
    else if (min < 60 * 24) { const h = Math.floor(min / 60), m = min % 60; text = en() ? `${h} h${m ? ` ${m} min` : ""}` : `${h} ч${m ? ` ${m} мин` : ""}`; }
    else { const days = Math.round(min / 1440); text = en() ? `${days} d` : `${days} дн.`; }
    return diff >= 0 ? t(`через ${text}`, `in ${text}`) : t(`${text} назад`, `${text} ago`);
  }
  function localInputValue(ms) {
    const d = new Date(ms);
    return { date: `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`, time: hm(d) };
  }
  function fromInputs(date, time) {
    const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date || ""), tm = /^(\d{1,2}):(\d{2})/.exec(time || "");
    if (!dm || !tm) return 0;
    return new Date(+dm[1], +dm[2] - 1, +dm[3], +tm[1], +tm[2], 0, 0).getTime();
  }

  const WEEKDAYS = [
    ["вс", "воскр", "sun"], ["пн", "понед", "mon"], ["вт", "вторн", "tue"], ["ср", "сред", "wed"],
    ["чт", "четв", "thu"], ["пт", "пятн", "fri"], ["сб", "субб", "sat"],
  ];
  function parseWhen(raw, now = new Date()) {
    let s = String(raw || "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
    if (!s) return 0;
    const unit = (u) => /^(м|мин|минут|минуты|минуту|min|mins|minute|minutes|m)$/.test(u) ? 60000
      : /^(ч|час|часа|часов|h|hr|hrs|hour|hours)$/.test(u) ? 3600000
        : /^(д|дн|день|дня|дней|сут|суток|d|day|days)$/.test(u) ? 86400000
          : /^(нед|неделю|недели|недель|w|week|weeks)$/.test(u) ? 604800000 : 0;
    let m = /^(?:через|in|\+)\s*(\d+(?:[.,]\d+)?)\s*([a-zа-я]+)\.?$/.exec(s);
    if (m && unit(m[2])) return now.getTime() + Math.round(parseFloat(m[1].replace(",", ".")) * unit(m[2]));
    if (/^(?:через час|in an hour|in 1 hour|через 1 час)$/.test(s)) return now.getTime() + 3600000;
    if (/^(?:через полчаса|in half an hour)$/.test(s)) return now.getTime() + 1800000;
    let dayOffset = null, base = null;
    const take = (re) => { const x = re.exec(s); if (x) s = (s.slice(0, x.index) + " " + s.slice(x.index + x[0].length)).trim(); return x; };
    if (take(/(послезавтра|day after tomorrow)/)) dayOffset = 2;
    else if (take(/(завтра|tomorrow)/)) dayOffset = 1;
    else if (take(/(сегодня|today|tonight)/)) dayOffset = 0;
    let x = take(/(\d{4})-(\d{1,2})-(\d{1,2})/);
    if (x) base = new Date(+x[1], +x[2] - 1, +x[3]);
    if (!base) {

      const before = s;
      x = take(/(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?=\s|$)/);
      if (x && (x[3] || /\d/.test(s) || +x[1] > 23 || +x[2] > 59)) {
        const year = x[3] ? (x[3].length === 2 ? 2000 + +x[3] : +x[3]) : now.getFullYear();
        base = new Date(year, +x[2] - 1, +x[1]);
        if (!x[3] && base.getTime() < at(0, 0, 0, now).getTime()) base.setFullYear(year + 1);
      } else if (x) s = before;
    }
    if (!base && dayOffset === null) {
      const word = s.split(/[\s,]+/).find((w) => WEEKDAYS.some((names) => names.some((n) => w.startsWith(n))));
      if (word) {
        const target = WEEKDAYS.findIndex((names) => names.some((n) => word.startsWith(n)));
        s = s.replace(word, " ").trim();
        let delta = (target - now.getDay() + 7) % 7;
        base = at(delta, 0, 0, now);
        base._weekday = true;
      }
    }
    s = s.replace(/^(?:в|во|at|on)\s+|\s+(?:в|at)$/g, "").replace(/\b(?:в|at)\b/g, " ").trim();
    let hour = null, minute = 0;
    x = /(\d{1,2})(?:[:.h](\d{2}))?\s*(am|pm|утра|дня|вечера|ночи)?/.exec(s);
    if (x && (x[2] !== undefined || x[3] || /^\d{1,2}$/.test(s.trim()) || dayOffset !== null || base)) {
      hour = +x[1]; minute = x[2] ? +x[2] : 0;
      const mer = x[3];
      if ((mer === "pm" || mer === "дня" || mer === "вечера") && hour < 12) hour += 12;
      if ((mer === "am" || mer === "ночи" || mer === "утра") && hour === 12) hour = 0;
    } else if (/утр|morning/.test(s)) hour = 9;
    else if (/дн[её]м|afternoon|noon|полдень/.test(s)) hour = /noon|полдень/.test(s) ? 12 : 14;
    else if (/вечер|evening|tonight/.test(s) || /tonight/.test(String(raw).toLowerCase())) hour = 21;
    else if (/ноч|night/.test(s)) hour = 23;
    if (hour === null && (base || dayOffset !== null)) hour = 9;
    if (hour === null || hour > 23 || minute > 59) return 0;
    let d;
    if (base) d = new Date(base.getFullYear(), base.getMonth(), base.getDate(), hour, minute);
    else d = at(dayOffset === null ? 0 : dayOffset, hour, minute, now);
    if (d.getTime() <= now.getTime()) {
      if (base && base._weekday) d = new Date(d.getTime() + 7 * 86400000);
      else if (dayOffset === null && !base) d = at(1, hour, minute, now);
    }
    return d.getTime();
  }

  function presets() {
    const now = new Date();
    const rows = [];
    const inHour = new Date(Math.ceil((now.getTime() + 3600000) / 60000) * 60000);
    rows.push({ id: "1h", icon: "hourglass", label: t("Через час", "In 1 hour"), at: inHour.getTime() });
    rows.push({ id: "3h", icon: "clock", label: t("Через 3 часа", "In 3 hours"), at: Math.ceil((now.getTime() + 3 * 3600000) / 60000) * 60000 });
    const tonight = at(0, 21, 0, now);
    if (tonight.getTime() - now.getTime() > 30 * 60000) rows.push({ id: "tonight", icon: "moonset", label: t("Сегодня вечером", "Tonight"), at: tonight.getTime() });
    else rows.push({ id: "tomorrow-evening", icon: "moonset", label: t("Завтра вечером", "Tomorrow evening"), at: at(1, 21, 0, now).getTime() });
    rows.push({ id: "tomorrow-9", icon: "sunrise", label: t("Завтра утром", "Tomorrow morning"), at: at(1, 9, 0, now).getTime() });
    rows.push({ id: "tomorrow-15", icon: "calendar", label: t("Завтра днём", "Tomorrow afternoon"), at: at(1, 15, 0, now).getTime() });
    const toMonday = (8 - now.getDay()) % 7 || 7;
    if (toMonday > 1) rows.push({ id: "monday", icon: "calendar", label: t("В понедельник утром", "Monday morning"), at: at(toMonday, 9, 0, now).getTime() });
    return rows;
  }

  const forChat = (chatId) => st.items.filter((row) => row.chat_id === chatId && ["scheduled", "sending", "missed", "failed"].includes(row.status));
  async function refresh(force = false) {
    try {
      const data = await api("/api/schedsend/list");
      if (!force && data.rev === st.rev && st.loaded) return;
      st.items = data.items || [];
      st.summary = data.summary || st.summary;
      st.rev = data.rev || "";
      st.loaded = true;
      renderBar(); decorateChats(); armTimer(); showNotices();
    } catch (e) {  }
  }
  async function heartbeat() {
    try {
      const data = await api("/api/schedsend/heartbeat", { method: "POST", body: {} });
      if (data && data.rev !== st.rev) await refresh(true);
    } catch (e) {}
  }

  function proxyOnline() {
    const status = document.getElementById("hub-status");
    return !status || status.dataset.state !== "offline";
  }
  const isOnline = () => navigator.onLine !== false && proxyOnline();

  function composerState() {
    const input = $("#input");
    return {
      text: input ? input.value : "",
      attachments: (typeof attachments !== "undefined" ? attachments : []).slice(),
      imagine: !!(input && input.dataset.imagine),
    };
  }
  function blockedReason() {
    if (S.tempChat) return "temp";
    if (S.mode === "code") return "code";
    return "";
  }
  function capsSnapshot() {
    const caps = {};
    const own = S.chatCaps || {};
    ["deep_research", "visualize"].forEach((key) => { if (own[key]) caps[key] = true; });
    if (typeof effectiveCap === "function") {
      try { caps.web_search = !!effectiveCap("web_search"); caps.thinking = !!effectiveCap("thinking"); } catch (e) {}
    } else {
      if ("web_search" in own) caps.web_search = !!own.web_search;
      if ("thinking" in own) caps.thinking = !!own.thinking;
    }
    if (own.agents === "always") caps.agents = true;
    return caps;
  }

  async function scheduleFromComposer({ trigger = "time", dueAt = 0, repeat = "none", telegram = false } = {}) {
    const reason = blockedReason();
    if (reason === "temp") return toast(t("Во временном чате отложенная отправка недоступна", "Scheduling is not available in a temporary chat"), "warn");
    if (reason === "code") return toast(t("В MemuCode отложенная отправка недоступна", "Scheduling is not available in MemuCode"), "warn");
    const snap = composerState();
    const text = snap.text.replace(/\s+$/, "");
    if (!text.trim() && !snap.attachments.length) return toast(t("Сначала напишите сообщение", "Type a message first"), "warn");
    if (text.trim().startsWith("/")) return toast(t("Команды нельзя запланировать — только обычные сообщения", "Commands cannot be scheduled — only regular messages"), "warn");
    if (snap.attachments.some((a) => a.pending)) return toast(t("Вложение ещё готовится", "An attachment is still being prepared"), "warn");
    if (snap.attachments.some((a) => a.isVideo)) return toast(t("Видео пока нельзя запланировать — отправьте его сразу", "Videos cannot be scheduled yet — send them right away"), "warn");
    if (trigger === "time" && dueAt < Date.now() + MIN_LEAD - 5000) return toast(t("Выберите время хотя бы на минуту позже", "Pick a time at least a minute from now"), "warn");
    const chat = typeof currentChat === "function" ? currentChat() : null;
    const project = isProjectMode(S.mode);
    if (project && !(chat && (chat.messages || []).length)) {
      return toast(t("Начните Work-чат обычным сообщением — потом можно планировать", "Start the Work chat with a regular message first — then you can schedule"), "warn");
    }
    const model = currentModel();
    const item = {
      chat_id: S.currentId, chat_title: (chat && chat.title) || text.trim().replace(/\s+/g, " ").slice(0, 48),
      new_chat: !chat, mode: project ? "work" : "chat",
      project_id: project ? (chat?.project_id || S.workspaces?.active_id || "") : "",
      access_mode: project && typeof workAccessMode === "function" ? workAccessMode() : "",
      text, model, caps: capsSnapshot(), imagine: snap.imagine,
      attachments: snap.attachments.map((a) => ({ name: a.name, data: a.data, size: a.size || 0, type: a.type || "",
        isImg: !!a.isImg || String(a.data || "").startsWith("data:image/") })),
      trigger, due_at: trigger === "time" ? dueAt : Date.now(), repeat: trigger === "time" ? repeat : "none",
      notify_telegram: !!telegram, lang: PG_LANG,
    };
    let saved;
    try { saved = await api("/api/schedsend/create", { method: "POST", body: { item, lang: PG_LANG } }); }
    catch (e) { return toast(e.message || String(e), "err"); }

    const input = $("#input");
    input.value = ""; input.style.height = "auto"; input.dispatchEvent(new Event("input"));
    if (typeof clearAttachments === "function") clearAttachments();
    if (snap.imagine && typeof setImagine === "function") setImagine(false);
    closeMenu();
    await refresh(true);
    st.expanded = true; renderBar();
    const when = trigger === "online" ? t("при подключении", "when online") : whenLabel(saved.item.due_at);
    toast(t("Сообщение будет отправлено ", "The message will be sent ") + when, "ok", {
      label: t("Отменить", "Undo"),
      run: async () => {
        try { await api("/api/schedsend/delete", { method: "POST", body: { id: saved.item.id } }); } catch (e) { return toast(e.message, "err"); }
        input.value = snap.text; input.dispatchEvent(new Event("input"));
        attachments = snap.attachments; renderAttachments();
        if (snap.imagine && typeof setImagine === "function") setImagine(true);
        await refresh(true);
      },
    });
  }

  function closeMenu() {
    if (st.layer && window.MemuLayers) { try { MemuLayers.close(st.layer, "api"); } catch (e) {} }
    st.layer = null;
    if (st.menu) { st.menu.remove(); st.menu = null; }
  }

  function customKbd() {
    let combo = "Ctrl+Shift+Enter";
    try { if (window.MemuHotkeys?.value) combo = MemuHotkeys.label(MemuHotkeys.value("schedule_send", combo)); } catch (e) {}
    if (!combo) return "";
    return `<kbd class="ss-kbd" title="${esc(combo)}">${esc(combo.replace(/(^|\+)Enter$/, "$1↵"))}</kbd>`;
  }

  function openMenu(anchor, { custom = false } = {}) {
    closeMenu();
    const reason = blockedReason();
    const snap = composerState();
    const empty = !snap.text.trim() && !snap.attachments.length;
    const menu = document.createElement("div");
    menu.className = "ss-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", t("Отправить позже", "Send later"));
    const preview = snap.text.trim().replace(/\s+/g, " ").slice(0, 80);
    const telegramDefault = !!pref("schedsend_telegram_default", false);
    const chatCount = forChat(S.currentId).length;
    let body;
    if (reason) {
      body = `<div class="ss-blocked">${ic("info")}<div><b>${reason === "temp" ? t("Временный чат", "Temporary chat") : "MemuCode"}</b>
        <p>${reason === "temp" ? t("Временный чат не сохраняется, поэтому отложить сообщение в нём нельзя.", "A temporary chat is not saved, so messages cannot be scheduled in it.")
          : t("В MemuCode сообщения отправляются сразу.", "MemuCode sends messages right away.")}</p>
        ${reason === "temp" && typeof makeTemporaryPermanent === "function" ? `<button type="button" class="ss-link" data-ss="permanent">${t("Сделать чат постоянным", "Make the chat permanent")}</button>` : ""}</div></div>`;
    } else {
      const rows = presets().map((p) => `<button type="button" class="ss-item" role="menuitem" data-ss="preset" data-at="${p.at}" ${empty ? "disabled" : ""}>
          ${ic(p.icon)}<span class="ss-item-label">${esc(p.label)}</span><span class="ss-item-time">${esc(whenLabel(p.at, { short: true }))}</span></button>`).join("");
      body = `${empty ? `<div class="ss-hint">${ic("info")}<span>${t("Сначала напишите сообщение — потом выберите время.", "Type a message first, then pick a time.")}</span></div>` : ""}
        <div class="ss-list">${rows}
          <button type="button" class="ss-item" role="menuitem" data-ss="custom" ${empty ? "disabled" : ""}>${ic("calendar")}<span class="ss-item-label">${t("Выбрать дату и время…", "Pick date & time…")}</span>${customKbd()}</button>
          <div class="ss-sep"></div>
          <button type="button" class="ss-item" role="menuitem" data-ss="online" ${empty ? "disabled" : ""}>${ic("wifi")}<span class="ss-item-label">${t("Когда появится связь", "When online")}<small>${t("Как только будут интернет и MemuAI Proxy", "As soon as the internet and MemuAI Proxy are available")}</small></span></button>
        </div>
        <div class="ss-custom" hidden>
          <label class="ss-nl">${ic("sparkle")}<input type="text" class="ss-nl-input" placeholder="${esc(t("Например: завтра в 15:00, через 2 часа, в пятницу 9:30", "e.g. tomorrow 3pm, in 2 hours, friday 9:30"))}" aria-label="${esc(t("Когда отправить", "When to send"))}"></label>
          <div class="ss-nl-out" aria-live="polite"></div>
          <div class="ss-dt">
            <label><span>${t("Дата", "Date")}</span><input type="date" class="pinput compact ss-date"></label>
            <label><span>${t("Время", "Time")}</span><input type="time" class="pinput compact ss-time"></label>
          </div>
          <div class="ss-chips">${[[15, "+15 " + t("мин", "min")], [60, "+1 " + t("ч", "h")], [180, "+3 " + t("ч", "h")], [1440, "+1 " + t("день", "day")], [10080, "+1 " + t("неделя", "week")]]
            .map(([min, label]) => `<button type="button" class="ss-chip" data-add="${min}">${esc(label)}</button>`).join("")}</div>
          <label class="ss-row"><span>${ic("repeat")} ${t("Повтор", "Repeat")}</span>
            <select class="pinput compact ss-repeat">
              <option value="none">${t("Один раз", "Once")}</option>
              <option value="daily">${t("Каждый день", "Every day")}</option>
              <option value="weekdays">${t("По будням", "Weekdays")}</option>
              <option value="weekly">${t("Каждую неделю", "Every week")}</option>
            </select></label>
          <div class="ss-custom-foot"><span class="ss-custom-when"></span>
            <button type="button" class="pbtn ss-go">${ic("clock")} ${t("Запланировать", "Schedule")}</button></div>
        </div>
        <label class="ss-tg">${ic("telegram")}<span>${t("Сообщить в Telegram, когда отправится", "Notify in Telegram when sent")}</span>
          <input type="checkbox" class="ss-tg-box" ${telegramDefault ? "checked" : ""}></label>`;
    }
    menu.innerHTML = `<div class="ss-head">${ic("clock")}<div class="ss-head-text"><b>${t("Отправить позже", "Send later")}</b>
        ${preview ? `<span class="ss-preview">${esc(preview)}${snap.attachments.length ? ` · ${icon("attach")}${snap.attachments.length}` : ""}</span>`
          : snap.attachments.length ? `<span class="ss-preview">${icon("attach")} ${snap.attachments.length}</span>` : ""}</div></div>
      ${body}
      <div class="ss-foot">
        ${chatCount ? `<button type="button" class="ss-link" data-ss="bar">${t("В этом чате", "In this chat")}: ${chatCount}</button>` : "<span></span>"}
        <button type="button" class="ss-link" data-ss="all">${t("Все отложенные", "All scheduled")}${st.summary.active ? ` (${st.summary.active})` : ""}</button>
      </div>`;
    document.body.appendChild(menu);
    st.menu = menu;
    const place = () => {

      const r = anchor.getBoundingClientRect(), pad = 8, gap = 8;
      menu.style.maxHeight = "";
      const width = Math.min(360, window.innerWidth - pad * 2);
      menu.style.width = width + "px";

      const natural = Math.ceil(menu.scrollHeight + menu.offsetHeight - menu.clientHeight);
      const above = r.top - gap - pad, below = window.innerHeight - r.bottom - gap - pad;

      if (natural > above && natural > below && r.left - gap - pad >= width) {
        const height = Math.min(natural, window.innerHeight - pad * 2);
        menu.style.maxHeight = height + "px";
        menu.style.left = Math.round(r.left - gap - width) + "px";
        menu.style.top = Math.round(Math.max(pad, Math.min(r.bottom - height, window.innerHeight - pad - height))) + "px";
        menu.dataset.side = "left";
        return;
      }
      const up = above >= Math.min(natural, 420) || above >= below;
      const height = Math.min(natural, Math.max(160, up ? above : below));
      menu.style.maxHeight = height + "px";
      menu.style.left = Math.round(Math.max(pad, Math.min(r.right - width, window.innerWidth - width - pad))) + "px";
      menu.style.top = Math.round(up ? r.top - gap - height : r.bottom + gap) + "px";
      menu.dataset.side = up ? "top" : "bottom";
    };
    place();
    if (window.MemuLayers) st.layer = MemuLayers.open({ el: menu, anchor, kind: "schedsend", focus: false, items: ".ss-item:not([disabled])",
      close: () => { if (st.menu === menu) { menu.remove(); st.menu = null; st.layer = null; } } });
    requestAnimationFrame(() => menu.classList.add("open"));
    const tgBox = menu.querySelector(".ss-tg-box");
    const tg = () => !!(tgBox && tgBox.checked);
    if (tgBox) tgBox.onchange = () => savePreferences({ schedsend_telegram_default: tgBox.checked });
    menu.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-ss]");
      if (!button || button.disabled) return;
      const kind = button.dataset.ss;
      if (kind === "preset") return scheduleFromComposer({ dueAt: +button.dataset.at, telegram: tg() });
      if (kind === "online") return scheduleFromComposer({ trigger: "online", telegram: tg() });
      if (kind === "custom") return showCustom();
      if (kind === "permanent") { closeMenu(); makeTemporaryPermanent(); return; }
      if (kind === "bar") { closeMenu(); st.expanded = true; renderBar(); $("#ss-bar")?.querySelector("button")?.focus(); return; }
      if (kind === "all") { closeMenu(); showAll(); }
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeMenu(); $("#input")?.focus(); return; }
      if (!menu.querySelector(".ss-custom")?.hidden) return;
      if (window.MemuLayers?.listKeys && MemuLayers.listKeys(menu, ".ss-item:not([disabled])", event)) return;
    });
    const first = menu.querySelector(".ss-item:not([disabled])") || menu.querySelector("button");
    first?.focus({ preventScroll: true });

    function showCustom() {
      const box = menu.querySelector(".ss-custom");
      if (!box) return;
      menu.querySelector(".ss-list").hidden = true;
      box.hidden = false;
      menu.classList.add("custom");
      const date = box.querySelector(".ss-date"), time = box.querySelector(".ss-time");
      const nl = box.querySelector(".ss-nl-input"), out = box.querySelector(".ss-nl-out");
      const whenEl = box.querySelector(".ss-custom-when"), go = box.querySelector(".ss-go");
      const start = at(1, 9, 0);
      const set = (ms) => { const v = localInputValue(ms); date.value = v.date; time.value = v.time; update(); };
      const today = localInputValue(Date.now());
      date.min = today.date;
      const update = () => {
        const ms = fromInputs(date.value, time.value);
        const ok = ms >= Date.now() + MIN_LEAD - 5000;
        whenEl.textContent = ms ? `${whenLabel(ms)} · ${relative(ms)}` : "";
        whenEl.classList.toggle("bad", !!ms && !ok);
        go.disabled = !ok;
      };
      set(start.getTime());
      date.oninput = update; time.oninput = update;
      box.querySelectorAll("[data-add]").forEach((chip) => chip.onclick = () => {
        const base = Math.max(Date.now(), fromInputs(date.value, time.value) || Date.now());
        const fromNow = +chip.dataset.add < 1440;
        set(Math.ceil(((fromNow ? Date.now() : base) + chip.dataset.add * 60000) / 60000) * 60000);
      });
      nl.oninput = () => {
        const ms = parseWhen(nl.value);
        out.textContent = nl.value.trim() ? (ms ? `→ ${whenLabel(ms)}` : t("Не понял время — выберите его ниже", "Could not read the time — pick it below")) : "";
        out.classList.toggle("bad", !!nl.value.trim() && !ms);
        if (ms) set(ms);
      };
      nl.onkeydown = (event) => { if (event.key === "Enter") { event.preventDefault(); if (!go.disabled) go.click(); } };
      go.onclick = () => scheduleFromComposer({ dueAt: fromInputs(date.value, time.value),
        repeat: box.querySelector(".ss-repeat").value, telegram: tg() });
      place();
      nl.focus();
    }
    if (custom && !reason && !empty) showCustom();
  }

  function statusInfo(row) {
    if (row.status === "sending") return { cls: "sending", text: t("Отправляется…", "Sending…") };
    if (row.status === "missed") return { cls: "missed", text: t("Время прошло, пока MemuAI был закрыт", "Its time passed while MemuAI was closed") };
    if (row.status === "failed") return { cls: "failed", text: (row.error === "interrupted" ? t("Отправка прервалась", "Sending was interrupted") : row.error) || t("Не отправлено", "Not sent") };
    if (row.trigger === "online") return { cls: "online", text: row.error ? `${t("Ждёт связи", "Waiting for connection")} · ${row.error}` : t("Отправится, когда появится связь", "Will be sent when online") };
    const late = row.due_at < Date.now();
    return { cls: late ? "due" : "", text: late ? t("Отправляется…", "Sending…") : relative(row.due_at) };
  }
  function rowHtml(row, { showChat = false } = {}) {
    const info = statusInfo(row);
    const model = row.model ? (typeof modelDisplayName === "function" ? modelDisplayName(row.model) : row.model) : "";
    const chips = [
      row.attachments?.length ? `<span class="ss-chip-mini" title="${esc(t("Вложения", "Attachments"))}">${icon("attach")}${row.attachments.length}</span>` : "",
      row.caps?.web_search ? `<span class="ss-chip-mini" title="${esc(t("Веб-поиск", "Web search"))}">${icon("globe")}</span>` : "",
      row.caps?.thinking ? `<span class="ss-chip-mini" title="${esc(t("Размышления", "Thinking"))}">${icon("brain")}</span>` : "",
      row.caps?.deep_research ? `<span class="ss-chip-mini" title="${esc(t("Глубокое исследование", "Deep research"))}">${icon("research")}</span>` : "",
      row.imagine ? `<span class="ss-chip-mini" title="${esc(t("Изображение", "Image"))}">${icon("image")}</span>` : "",
      row.repeat && row.repeat !== "none" ? `<span class="ss-chip-mini" title="${esc(repeatLabel(row.repeat))}">${ic("repeat")}</span>` : "",
      row.notify_telegram ? `<span class="ss-chip-mini" title="Telegram">${ic("telegram")}</span>` : "",
      row.mode === "work" ? `<span class="ss-chip-mini" title="Work">${icon("work")}</span>` : "",
      model ? `<span class="ss-chip-mini ss-model" title="${esc(row.model)}">${esc(model)}</span>` : "",
    ].join("");
    const when = row.trigger === "online" ? t("Когда появится связь", "When online") : whenLabel(row.due_at);
    const actions = row.status === "sending" ? `<span class="ss-spinner" aria-hidden="true"></span>`
      : row.status === "missed" ? `<button type="button" class="pbtn ss-small" data-act="approve">${ic("sendNow")} ${t("Отправить", "Send")}</button>
          <button type="button" class="ss-icon-btn" data-act="edit" title="${esc(t("Перенести", "Reschedule"))}" aria-label="${esc(t("Перенести", "Reschedule"))}">${ic("calendar")}</button>
          <button type="button" class="ss-icon-btn danger" data-act="delete" title="${esc(t("Удалить", "Delete"))}" aria-label="${esc(t("Удалить", "Delete"))}">${icon("trash")}</button>`
        : `<button type="button" class="ss-icon-btn" data-act="now" title="${esc(t("Отправить сейчас", "Send now"))}" aria-label="${esc(t("Отправить сейчас", "Send now"))}">${ic("sendNow")}</button>
          <button type="button" class="ss-icon-btn" data-act="edit" title="${esc(t("Изменить", "Edit"))}" aria-label="${esc(t("Изменить", "Edit"))}">${icon("edit")}</button>
          <button type="button" class="ss-icon-btn danger" data-act="delete" title="${esc(t("Отменить отправку", "Cancel"))}" aria-label="${esc(t("Отменить отправку", "Cancel"))}">${icon("trash")}</button>`;
    return `<div class="ss-row-item ${info.cls}" data-id="${esc(row.id)}" tabindex="0">
      <div class="ss-when">${ic(row.trigger === "online" ? "wifi" : row.status === "failed" || row.status === "missed" ? "warn" : "clock")}
        <div><b>${esc(when)}</b><small class="ss-status">${esc(info.text)}</small></div></div>
      <div class="ss-body">${showChat ? `<div class="ss-chat">${icon("chat")} ${esc(row.chat_title || t("Чат", "Chat"))}</div>` : ""}
        <div class="ss-text">${esc(String(row.text || "").slice(0, 400)) || `<i>${t("Только вложения", "Attachments only")}</i>`}</div>
        ${chips ? `<div class="ss-chips-mini">${chips}</div>` : ""}</div>
      <div class="ss-actions">${actions}</div></div>`;
  }
  function repeatLabel(value) {
    return { daily: t("Каждый день", "Every day"), weekdays: t("По будням", "Weekdays"), weekly: t("Каждую неделю", "Every week") }[value] || t("Один раз", "Once");
  }

  function ensureBar() {
    let bar = document.getElementById("ss-bar");
    if (bar) return bar;
    const composer = document.querySelector(".composer-wrap .composer");
    if (!composer) return null;
    bar = document.createElement("div");
    bar.id = "ss-bar";
    bar.className = "ss-bar";
    bar.hidden = true;
    composer.parentElement.insertBefore(bar, composer);
    bar.addEventListener("click", onRowAction);
    bar.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && event.target.classList?.contains("ss-row-item")) { event.preventDefault(); editItem(event.target.dataset.id); }
    });
    return bar;
  }
  function renderBar() {
    const bar = ensureBar();
    if (!bar) return;
    const rows = forChat(S.currentId).sort((a, b) => (a.trigger === "online" ? 0 : a.due_at) - (b.trigger === "online" ? 0 : b.due_at));
    st.lastChat = S.currentId;
    if (!rows.length || S.tempChat) { bar.hidden = true; bar.innerHTML = ""; return; }
    const next = rows.find((row) => row.status === "scheduled" && row.trigger === "time");
    const attention = rows.filter((row) => row.status === "missed" || row.status === "failed").length;
    const count = rows.length;
    const label = en() ? `${count} scheduled message${count === 1 ? "" : "s"}`
      : `${count} ${count % 10 === 1 && count % 100 !== 11 ? "отложенное сообщение" : [2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100) ? "отложенных сообщения" : "отложенных сообщений"}`;
    bar.hidden = false;
    bar.classList.toggle("open", st.expanded);
    bar.classList.toggle("attention", attention > 0);
    bar.innerHTML = `<button type="button" class="ss-bar-head" data-act="toggle" aria-expanded="${st.expanded}">
        ${ic(attention ? "warn" : "clock")}<span class="ss-bar-title">${esc(label)}</span>
        ${attention ? `<span class="ss-badge">${attention} ${t("требует внимания", "need attention")}</span>`
          : next ? `<span class="ss-bar-next">${t("ближайшее", "next")} ${esc(whenLabel(next.due_at))}</span>` : ""}
        <span class="ss-bar-grow"></span><span class="ss-caret" aria-hidden="true">${icon("updown")}</span></button>
      <div class="ss-bar-list" ${st.expanded ? "" : "hidden"}>${rows.map((row) => rowHtml(row)).join("")}
        <div class="ss-bar-foot"><button type="button" class="ss-link" data-act="all">${t("Все отложенные сообщения", "All scheduled messages")}</button>
          <button type="button" class="ss-link" data-act="settings">${icon("settings")} ${t("Настройки", "Settings")}</button></div></div>`;
  }

  async function onRowAction(event) {
    const button = event.target.closest("[data-act]");
    if (!button) return;
    const act = button.dataset.act;
    if (act === "toggle") { st.expanded = !st.expanded; renderBar(); return; }
    if (act === "all") return showAll();
    if (act === "settings") { if (typeof openSettingsPage === "function") { await showSettings(true); openSettingsPage("chat"); requestAnimationFrame(() => document.getElementById("settings-schedsend")?.scrollIntoView({ block: "start" })); } return; }
    if (act === "open") { const id = button.closest("[data-chat-id]")?.dataset.chatId; if (id) { closePanel(); openChat(id); } return; }
    const row = button.closest("[data-id]");
    if (!row) return;
    const id = row.dataset.id;
    button.disabled = true;
    try {
      if (act === "now") await sendNow(id);
      else if (act === "approve") { await api("/api/schedsend/approve", { method: "POST", body: { id, lang: PG_LANG } }); await refresh(true); pump(); }
      else if (act === "edit") await editItem(id);
      else if (act === "delete") await removeItem(id);
    } catch (e) { toast(e.message || String(e), "err"); }
    finally { button.disabled = false; }
  }

  async function sendNow(id) {
    await api("/api/schedsend/send-now", { method: "POST", body: { id, lang: PG_LANG } });
    await refresh(true);
    pump();
  }
  async function removeItem(id) {
    let full = null;
    try { full = (await api(`/api/schedsend/item/${encodeURIComponent(id)}`)).item; } catch (e) {}
    await api("/api/schedsend/delete", { method: "POST", body: { id, lang: PG_LANG } });
    await refresh(true);
    toast(t("Отложенное сообщение удалено", "Scheduled message deleted"), "ok", full && full.status !== "missed" ? {
      label: t("Вернуть", "Undo"),
      run: async () => {
        const restore = { ...full };
        if (restore.trigger === "time" && restore.due_at < Date.now() + MIN_LEAD) restore.due_at = Date.now() + 5 * 60000;
        try { await api("/api/schedsend/create", { method: "POST", body: { item: restore, lang: PG_LANG } }); } catch (e) { return toast(e.message, "err"); }
        await refresh(true);
      },
    } : null);
  }

  async function editItem(id) {
    let item;
    try { item = (await api(`/api/schedsend/item/${encodeURIComponent(id)}`)).item; } catch (e) { return toast(e.message, "err"); }
    const due = item.trigger === "time" && item.due_at > Date.now() + MIN_LEAD ? item.due_at : at(1, 9, 0).getTime();
    const v = localInputValue(due);
    const models = (S.models || []).filter((m) => !(typeof isMusicOnlyModel === "function" && isMusicOnlyModel(m.id)));
    let keep = (item.attachments || []).slice();
    const attachList = () => keep.length ? keep.map((a, index) => `<span class="ss-attach">${a.isImg && a.data ? `<img src="${esc(a.data)}" alt="">` : icon("attach")}
        <span>${esc(a.name)}</span><button type="button" class="ss-icon-btn" data-drop="${index}" aria-label="${esc(t("Убрать", "Remove"))}">${icon("close")}</button></span>`).join("")
      : `<span class="sub">${t("Без вложений", "No attachments")}</span>`;
    panel(`<div class="ss-editor">
      <h2>${ic("clock")} ${t("Отложенное сообщение", "Scheduled message")}</h2>
      <p class="sub">${esc(item.chat_title || "")}${item.status === "missed" ? ` · ${t("время прошло, пока MemuAI был закрыт", "its time passed while MemuAI was closed")}` : ""}${item.status === "failed" && item.error ? ` · ${esc(item.error)}` : ""}</p>
      <label class="field-label" for="ss-ed-text">${t("Текст", "Text")}</label>
      <textarea id="ss-ed-text" class="ptexta" rows="6">${esc(item.text || "")}</textarea>
      <div class="ss-ed-attach" id="ss-ed-attach">${attachList()}</div>
      <div class="ss-ed-grid">
        <div class="sx-seg ss-trigger" role="radiogroup" aria-label="${esc(t("Когда", "When"))}">
          <label class="sx-seg-opt"><input type="radio" name="ss-trigger" value="time" ${item.trigger !== "online" ? "checked" : ""}><span>${ic("calendar")}${t("В указанное время", "At a time")}</span></label>
          <label class="sx-seg-opt"><input type="radio" name="ss-trigger" value="online" ${item.trigger === "online" ? "checked" : ""}><span>${ic("wifi")}${t("Когда появится связь", "When online")}</span></label>
        </div>
        <div class="ss-dt ss-ed-time">
          <label><span>${t("Дата", "Date")}</span><input type="date" class="pinput compact" id="ss-ed-date" value="${v.date}"></label>
          <label><span>${t("Время", "Time")}</span><input type="time" class="pinput compact" id="ss-ed-clock" value="${v.time}"></label>
          <label><span>${t("Повтор", "Repeat")}</span><select class="pinput compact" id="ss-ed-repeat">
            ${["none", "daily", "weekdays", "weekly"].map((r) => `<option value="${r}" ${item.repeat === r ? "selected" : ""}>${esc(repeatLabel(r))}</option>`).join("")}</select></label>
        </div>
        <div class="ss-ed-when" id="ss-ed-when"></div>
        <label class="ss-row"><span>${icon("bot")} ${t("Модель", "Model")}</span>
          <select class="pinput compact" id="ss-ed-model">${models.some((m) => m.id === item.model) || !item.model ? "" : `<option value="${esc(item.model)}" selected>${esc(item.model)}</option>`}
            ${models.map((m) => `<option value="${esc(m.id)}" ${m.id === item.model ? "selected" : ""}>${esc(typeof modelDisplayName === "function" ? modelDisplayName(m.id) : m.id)}</option>`).join("")}</select></label>
        <div class="ss-ed-toggles">
          <label class="ss-check"><input type="checkbox" id="ss-ed-web" ${item.caps?.web_search ? "checked" : ""}> ${icon("globe")} ${t("Веб-поиск", "Web search")}</label>
          <label class="ss-check"><input type="checkbox" id="ss-ed-think" ${item.caps?.thinking ? "checked" : ""}> ${icon("brain")} ${t("Размышления", "Thinking")}</label>
          <label class="ss-check"><input type="checkbox" id="ss-ed-tg" ${item.notify_telegram ? "checked" : ""}> ${ic("telegram")} ${t("Сообщить в Telegram", "Notify in Telegram")}</label>
        </div>
      </div>
      <div class="row-end ss-ed-actions">
        <button class="pbtn ghost danger-text" id="ss-ed-delete">${icon("trash")} ${t("Удалить", "Delete")}</button>
        <span class="ss-bar-grow"></span>
        <button class="pbtn ghost" id="ss-ed-now">${ic("sendNow")} ${t("Отправить сейчас", "Send now")}</button>
        <button class="pbtn" id="ss-ed-save">${icon("check")} ${t("Сохранить", "Save")}</button>
      </div></div>`, "ss-editor-panel");
    const trig = () => document.querySelector("input[name=ss-trigger]:checked")?.value || "time";
    const whenEl = $("#ss-ed-when");
    const sync = () => {
      const online = trig() === "online";
      $(".ss-ed-time").classList.toggle("disabled", online);
      ["#ss-ed-date", "#ss-ed-clock", "#ss-ed-repeat"].forEach((sel) => { $(sel).disabled = online; });
      const ms = fromInputs($("#ss-ed-date").value, $("#ss-ed-clock").value);
      whenEl.textContent = online ? t("Отправится, как только будут интернет и MemuAI Proxy", "Sent as soon as the internet and MemuAI Proxy are available")
        : ms ? `${whenLabel(ms)} · ${relative(ms)}` : "";
      whenEl.classList.toggle("bad", !online && (!ms || ms < Date.now() + MIN_LEAD - 5000));
    };
    document.querySelectorAll("input[name=ss-trigger]").forEach((r) => r.onchange = sync);
    $("#ss-ed-date").oninput = sync; $("#ss-ed-clock").oninput = sync;
    sync();
    $("#ss-ed-attach").onclick = (event) => {
      const drop = event.target.closest("[data-drop]");
      if (!drop) return;
      keep.splice(+drop.dataset.drop, 1);
      $("#ss-ed-attach").innerHTML = attachList();
    };
    const patch = () => {
      const caps = { ...(item.caps || {}), web_search: $("#ss-ed-web").checked, thinking: $("#ss-ed-think").checked };
      const body = { text: $("#ss-ed-text").value, trigger: trig(), repeat: $("#ss-ed-repeat").value, model: $("#ss-ed-model").value,
        caps, notify_telegram: $("#ss-ed-tg").checked };
      if (body.trigger === "time") body.due_at = fromInputs($("#ss-ed-date").value, $("#ss-ed-clock").value);
      if (keep.length !== (item.attachments || []).length) body.attachments = keep;
      return body;
    };
    $("#ss-ed-save").onclick = async () => {
      try { await api("/api/schedsend/update", { method: "POST", body: { id, patch: patch(), lang: PG_LANG } }); }
      catch (e) { return toast(e.message, "err"); }
      closePanel(); await refresh(true); toast(t("Сохранено", "Saved"));
    };
    $("#ss-ed-now").onclick = async () => {
      try {
        const body = patch(); delete body.due_at; body.trigger = "time";
        body.due_at = Date.now() + MIN_LEAD + 1000;
        await api("/api/schedsend/update", { method: "POST", body: { id, patch: body, lang: PG_LANG } });
        closePanel(); await sendNow(id);
      } catch (e) { toast(e.message, "err"); }
    };
    $("#ss-ed-delete").onclick = async () => { closePanel(); try { await removeItem(id); } catch (e) { toast(e.message, "err"); } };
  }

  function showAll() {
    const active = st.items.filter((row) => ["scheduled", "sending", "missed", "failed"].includes(row.status));
    const done = st.items.filter((row) => ["sent", "skipped"].includes(row.status)).sort((a, b) => (b.sent_at || b.updated) - (a.sent_at || a.updated)).slice(0, 30);
    const groups = new Map();
    active.sort((a, b) => (a.trigger === "online" ? 0 : a.due_at) - (b.trigger === "online" ? 0 : b.due_at))
      .forEach((row) => { if (!groups.has(row.chat_id)) groups.set(row.chat_id, []); groups.get(row.chat_id).push(row); });
    const policy = pref("schedsend_missed", "send");
    const html = `<div class="ss-all">
      <h2>${ic("clock")} ${t("Отложенные сообщения", "Scheduled messages")}</h2>
      <p class="sub">${t("Отправляются в нужное время. Обычные чаты отправит MemuAI Proxy, даже если окно MemuAI закрыто; Work-чатам нужно открытое окно.",
        "They are sent at the chosen time. MemuAI Proxy sends regular chats even when the MemuAI window is closed; Work chats need the window open.")}</p>
      ${groups.size ? [...groups.entries()].map(([chatId, rows]) => `<section class="ss-group" data-chat-id="${esc(chatId)}">
          <header><b>${icon("chat")} ${esc(rows[0].chat_title || t("Чат", "Chat"))}</b>
          ${(S.chats || []).some((c) => c.id === chatId) ? `<button type="button" class="ss-link" data-act="open">${t("Открыть чат", "Open chat")}</button>` : ""}</header>
          ${rows.map((row) => rowHtml(row)).join("")}</section>`).join("")
        : `<div class="ss-empty">${ic("clock")}<b>${t("Ничего не запланировано", "Nothing is scheduled")}</b>
          <p>${t("Нажмите правой кнопкой (или удерживайте) кнопку отправки, либо Ctrl+Shift+Enter в поле ввода.", "Right-click (or press and hold) the send button, or press Ctrl+Shift+Enter in the composer.")}</p></div>`}
      ${done.length ? `<details class="ss-history"><summary>${t("Недавно отправленные", "Recently sent")} (${done.length})</summary>
        ${done.map((row) => `<div class="ss-hist-row"><span>${row.status === "skipped" ? ic("warn") : icon("check")}</span>
          <span class="ss-hist-text"><b>${esc(row.chat_title || "")}</b> ${esc(String(row.text || "").slice(0, 120))}</span>
          <small>${row.status === "skipped" ? t("пропущено", "skipped") : whenLabel(row.sent_at || row.updated, { short: true })}${row.late ? ` · ${t("с опозданием", "late")}` : ""}${row.sent_by === "proxy" ? ` · ${t("в фоне", "in background")}` : ""}</small></div>`).join("")}</details>` : ""}
      <div class="ss-all-foot sub">${icon("info")} ${t("Если время пришло, пока MemuAI был закрыт:", "If the time came while MemuAI was closed:")}
        <b>${{ send: t("отправить при запуске", "send on next start"), ask: t("спросить", "ask"), skip: t("пропустить", "skip") }[policy] || policy}</b>
        <button type="button" class="ss-link" data-act="settings">${t("изменить", "change")}</button></div>
    </div>`;
    panel(html, "ss-all-panel");
    $("#panel")?.addEventListener("click", onRowAction);
  }

  function showNotices() {
    const fresh = st.items.filter((row) => row.notice && !st.noticeShown.has(row.id) && row.status !== "missed");
    const missed = st.items.filter((row) => row.status === "missed" && !st.noticeShown.has(row.id));
    if (!fresh.length && !missed.length) return;
    fresh.concat(missed).forEach((row) => st.noticeShown.add(row.id));
    const late = fresh.filter((row) => row.notice_kind === "late").length;
    const background = fresh.filter((row) => row.notice_kind === "background").length;
    const skipped = fresh.filter((row) => row.status === "skipped").length;
    const failed = fresh.filter((row) => row.status === "failed").length;
    const answerErr = fresh.filter((row) => row.notice_kind === "answer_error").length;
    const parts = [];
    if (late) parts.push(en() ? `${late} sent late` : `отправлено с опозданием: ${late}`);
    if (background) parts.push(en() ? `${background} sent in the background` : `отправлено в фоне: ${background}`);
    if (skipped) parts.push(en() ? `${skipped} skipped` : `пропущено: ${skipped}`);
    if (failed) parts.push(en() ? `${failed} not sent` : `не отправлено: ${failed}`);
    if (answerErr) parts.push(en() ? `${answerErr} without an answer` : `без ответа модели: ${answerErr}`);
    if (missed.length) parts.push(en() ? `${missed.length} waiting for your decision` : `ждут решения: ${missed.length}`);
    if (parts.length) toast(t("Отложенные сообщения: ", "Scheduled messages: ") + parts.join(", "), failed || missed.length ? "warn" : "ok",
      { label: t("Показать", "Show"), run: () => showAll() });
    const ack = fresh.map((row) => row.id);
    if (ack.length) api("/api/schedsend/ack", { method: "POST", body: { ids: ack } }).catch(() => {});
  }

  function armTimer() {
    clearTimeout(st.timer);
    const due = st.items.filter((row) => row.status === "scheduled")
      .map((row) => Math.max(row.trigger === "time" ? row.due_at : 0, row.next_attempt || 0));
    if (!due.length) return;
    const wait = Math.max(250, Math.min(2 ** 31 - 1, Math.min(...due) - Date.now() + 150));
    st.timer = setTimeout(pump, wait);
  }
  const modalOpen = () => !!document.querySelector("#panel-back.visible") || !!document.querySelector("#pin-lock-back[style*='flex']");
  function windowIdle() {
    const input = $("#input");
    return !modalOpen() && !(input && input.value.trim()) && !(typeof attachments !== "undefined" && attachments.length)
      && !generationJobs.has(S.currentId) && Date.now() - st.typingAt > 20000;
  }
  function needsWindow(row) {
    return row.mode === "work" || row.imagine || (row.model && typeof isImageModel === "function" && isImageModel(row.model));
  }

  let pumping = false;
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      const now = Date.now();
      const due = st.items.filter((row) => row.status === "scheduled" && !st.busy.has(row.id)
        && (!row.next_attempt || row.next_attempt <= now) && (row.trigger === "online" || row.due_at <= now));
      for (const row of due) {
        if (!isOnline()) break;
        const open = row.chat_id === S.currentId && !S.tempChat;
        if (open) {
          if (generationJobs.has(S.currentId) || modalOpen()) continue;
          await runInWindow(row);
        } else if (!needsWindow(row)) {
          if (row.route === "server") continue;
          st.busy.add(row.id);
          try { await api("/api/schedsend/delegate", { method: "POST", body: { id: row.id, lang: PG_LANG } }); }
          catch (e) { console.warn("schedsend delegate", e); }
          finally { st.busy.delete(row.id); }
        } else if (windowIdle() && (S.chats || []).some((c) => c.id === row.chat_id)) {
          await openChat(row.chat_id);
          if (S.currentId === row.chat_id) {
            toast(t("Отправляю отложенное сообщение в «", "Sending a scheduled message in “") + (row.chat_title || "") + t("»", "”"), "info");
            await runInWindow(row);
          }
        } else if (!(S.chats || []).some((c) => c.id === row.chat_id)) {

          st.busy.add(row.id);
          try {
            const claimed = await api("/api/schedsend/claim", { method: "POST", body: { id: row.id } });
            if (claimed.action === "send") await api("/api/schedsend/complete", { method: "POST", body: { id: row.id, ok: false, error: t("Чат удалён", "The chat was deleted") } });
          } catch (e) {} finally { st.busy.delete(row.id); }
        } else {
          const last = st.prompted.get(row.id) || 0;
          if (Date.now() - last > 5 * 60000) {
            st.prompted.set(row.id, Date.now());
            toast(t("Пора отправить отложенное сообщение в «", "Time to send a scheduled message in “") + (row.chat_title || "") + t("»", "”"), "info", {
              label: t("Открыть и отправить", "Open and send"),
              run: async () => { await openChat(row.chat_id); if (S.currentId === row.chat_id) runInWindow(row); },
            });
          }
        }
      }
      if (due.length) await refresh(true);
    } finally {
      pumping = false;
    }
  }

  async function runInWindow(row) {
    if (st.busy.has(row.id)) return;
    st.busy.add(row.id);
    try {
      const claimed = await api("/api/schedsend/claim", { method: "POST", body: { id: row.id, lang: PG_LANG } });
      if (claimed.action === "ask") { toast(t("Время отложенного сообщения прошло — решите, отправлять ли его", "A scheduled message missed its time — decide whether to send it"), "warn"); st.expanded = true; return; }
      if (claimed.action === "skip") { toast(t("Отложенное сообщение пропущено: его время прошло", "A scheduled message was skipped: its time has passed"), "warn"); return; }
      if (claimed.action !== "send" || !claimed.item) return;
      const item = claimed.item;
      const input = $("#input");
      const saved = { text: input.value, attachments: attachments.slice(), imagine: !!input.dataset.imagine,
        model: currentModel(), caps: { ...(S.chatCaps || {}) }, label: input.dataset.commandLabel || "" };
      const focusHook = window.MemuFocus;
      const before = S.messages.length;
      let error = "";
      try {
        if (item.model && item.model !== saved.model && (S.models || []).some((m) => m.id === item.model)) setCurrentModel(item.model);
        S.chatCaps = { ...(S.chatCaps || {}) };
        for (const [key, value] of Object.entries(item.caps || {})) {
          if (key === "agents") { if (value) S.chatCaps.agents = "always"; continue; }
          S.chatCaps[key] = !!value;
        }
        if (item.imagine && typeof setImagine === "function") setImagine(true); else if (saved.imagine && typeof setImagine === "function") setImagine(false);
        input.value = item.text || "";
        attachments = (item.attachments || []).map((a) => ({ name: a.name, data: a.data, size: a.size, type: a.type, isImg: !!a.isImg }));
        if (typeof renderAttachments === "function") renderAttachments();
        try { window.MemuFocus = null; } catch (e) {}
        await send();
      } catch (e) {
        error = e?.message || String(e);
      } finally {
        try { window.MemuFocus = focusHook; } catch (e) {}
        const sentRow = S.messages.slice(before).find((m) => m.role === "user");
        input.value = saved.text; input.dataset.commandLabel = saved.label;
        input.dispatchEvent(new Event("input"));
        attachments = saved.attachments; if (typeof renderAttachments === "function") renderAttachments();
        if (typeof setImagine === "function") setImagine(saved.imagine);
        if (saved.model && currentModel() !== saved.model) setCurrentModel(saved.model);
        S.chatCaps = saved.caps;
        if (sentRow) {
          sentRow.scheduled = { id: item.id, at: item.due_at, late: !!item.late, trigger: item.trigger };
          if (typeof persistChat === "function") persistChat();
        }
        if (typeof renderCaps === "function") renderCaps();
        if (typeof renderMessages === "function") renderMessages();
        const sent = !!sentRow || S.messages.length > before;
        await api("/api/schedsend/complete", { method: "POST", body: {
          id: item.id, ok: sent && !error, error: error || (sent ? "" : t("Не удалось отправить", "Could not send")),
          retry: !sent && item.trigger === "online", model: currentModel() } }).catch(() => {});
        if (sent && item.late) toast(t("Отложенное сообщение отправлено с опозданием: MemuAI был закрыт в назначенное время", "A scheduled message was sent late: MemuAI was closed at the scheduled time"), "info");
        if (sent && item.notify_telegram) watchAnswer(item, S.currentId);
      }
    } catch (e) {
      console.warn("schedsend", e);
    } finally {
      st.busy.delete(row.id);
    }
  }

  function watchAnswer(item, chatId) {
    const started = Date.now();
    const check = () => {
      if (generationJobs.has(chatId) && Date.now() - started < 30 * 60000) return setTimeout(check, 2000);
      const chat = (S.chats || []).find((c) => c.id === chatId);
      const last = [...(chat?.messages || S.messages || [])].reverse().find((m) => m.role === "assistant");
      api("/api/pg/notify", { method: "POST", body: {
        title: t("MemuAI · Отложенное сообщение отправлено", "MemuAI · Scheduled message sent"),
        text: String(last?.content || item.text || "").slice(0, 3000), source_chat_id: chatId,
        source_chat_title: item.chat_title || "", model: last?.model || item.model || "", kind: "schedule" } }).catch(() => {});
    };
    setTimeout(check, 2000);
  }

  function decorateChats() {
    const byChat = st.summary.by_chat || {};
    document.querySelectorAll(".chat-item[data-chat]").forEach((el) => {
      const count = byChat[el.dataset.chat] || 0;
      let badge = el.querySelector(":scope > .ss-chat-badge");
      if (!count) { badge?.remove(); return; }
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "ss-chat-badge";
        el.appendChild(badge);
      }
      const title = en() ? `Scheduled messages: ${count}` : `Отложенных сообщений: ${count}`;
      if (badge.title !== title) { badge.title = title; badge.innerHTML = ic("clock"); badge.setAttribute("aria-label", title); }
    });
  }

  function settingsHtml(ctx) {
    const { K, L2 } = ctx;
    const policy = pref("schedsend_missed", "send");
    return K.section({ id: "settings-schedsend", title: L2("Отложенная отправка", "Scheduled messages"),
      desc: L2("Правый клик или долгое нажатие на кнопку отправки, либо Ctrl+Shift+Enter, — и сообщение уйдёт в выбранное время.",
        "Right-click or press and hold the send button, or press Ctrl+Shift+Enter, and the message is sent at the time you choose."),
      keywords: "schedule scheduled send later отложенная отправка запланировать позже", body:
      K.row({ label: L2("Если время прошло, пока MemuAI был закрыт", "If the time passed while MemuAI was closed"),
        desc: L2("Отправить при следующем запуске (с уведомлением), спросить вас или пропустить", "Send on next start (with a notice), ask you, or skip it"),
        key: "schedsend_missed", def: "send", keywords: "missed пропущенные опоздание late",
        control: K.select("st-schedsend-missed", [["send", L2("Отправить", "Send")], ["ask", L2("Спросить", "Ask")], ["skip", L2("Пропустить", "Skip")]], policy, { width: 170 }) })
      + K.row({ label: L2("Отправлять, когда окно закрыто", "Send while the window is closed"),
        desc: L2("MemuAI Proxy отправит сообщения обычных чатов в фоне; Work-чатам нужно открытое окно", "MemuAI Proxy sends regular-chat messages in the background; Work chats need the window"),
        key: "schedsend_background", def: true, keywords: "background фон proxy",
        control: K.toggle("st-schedsend-background", pref("schedsend_background", true)) })
      + K.row({ label: L2("Уведомлять об отправке в фоне", "Notify about background sends"),
        desc: L2("Уведомление Windows, когда сообщение ушло без открытого окна или с опозданием", "A Windows notification when a message was sent without the window or late"),
        key: "schedsend_notify", def: true, keywords: "notification уведомление",
        control: K.toggle("st-schedsend-notify", pref("schedsend_notify", true)) })
      + K.row({ label: L2("Список отложенных сообщений", "Scheduled messages list"), desc: L2("Все чаты: изменить, отправить сейчас или отменить", "All chats: edit, send now or cancel"),
        control: `<button type="button" class="pbtn ghost" id="st-schedsend-open">${ic("clock")} ${L2("Открыть", "Open")}${st.summary.active ? ` (${st.summary.active})` : ""}</button>` }) });
  }
  function bindSettings() {
    const missed = document.getElementById("st-schedsend-missed");
    if (missed) missed.onchange = () => savePreferences({ schedsend_missed: missed.value });
    const bg = document.getElementById("st-schedsend-background");
    if (bg) bg.onchange = () => savePreferences({ schedsend_background: bg.checked });
    const notify = document.getElementById("st-schedsend-notify");
    if (notify) notify.onchange = () => savePreferences({ schedsend_notify: notify.checked });
    const open = document.getElementById("st-schedsend-open");
    if (open) open.onclick = () => showAll();
  }

  function bind() {
    const sendBtn = document.getElementById("btn-send");
    if (!sendBtn || sendBtn.dataset.ssBound) return;
    sendBtn.dataset.ssBound = "1";
    sendBtn.setAttribute("aria-haspopup", "menu");
    const tip = t("Правый клик или удержание — отправить позже", "Right-click or hold to send later");
    if (!sendBtn.dataset.ssTip) { sendBtn.dataset.ssTip = tip; }
    sendBtn.addEventListener("contextmenu", (event) => {
      if (sendBtn.classList.contains("is-stop")) return;
      event.preventDefault(); event.stopPropagation();
      openMenu(sendBtn);
    });
    let pressTimer = 0, pressed = false;
    sendBtn.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || sendBtn.classList.contains("is-stop")) return;
      pressed = false;
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => { pressed = true; sendBtn.classList.add("ss-pressed"); openMenu(sendBtn); }, 550);
    });
    const cancelPress = () => { clearTimeout(pressTimer); setTimeout(() => sendBtn.classList.remove("ss-pressed"), 120); };
    ["pointerup", "pointerleave", "pointercancel"].forEach((name) => sendBtn.addEventListener(name, cancelPress));

    sendBtn.addEventListener("click", (event) => {
      if (pressed) { pressed = false; event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    const input = document.getElementById("input");
    if (input) {
      input.addEventListener("keydown", (event) => {
        const custom = window.MemuHotkeys?.match ? MemuHotkeys.match("schedule_send", event, "Ctrl+Shift+Enter")
          : typeof shortcutActive === "function" ? shortcutActive("schedule_send", "Ctrl+Shift+Enter", event)
            : (event.ctrlKey && event.shiftKey && event.key === "Enter");
        if (custom && !event.isComposing) {
          event.preventDefault(); event.stopImmediatePropagation();
          openMenu(document.getElementById("btn-send") || input, { custom: true });
          return;
        }
        st.typingAt = Date.now();
      }, true);
    }
    const side = document.querySelector(".side");
    if (side && "MutationObserver" in window) {
      let queued = false;
      new MutationObserver(() => {
        if (queued) return;
        queued = true;
        requestAnimationFrame(() => { queued = false; decorateChats(); });
      }).observe(side, { childList: true, subtree: true });
    }
  }

  function start() {

    try {
      window.MemuHotkeys?.register?.([{ id: "schedule_send", group: "chat", scope: "chat", keys: ["Ctrl+Shift+Enter"], custom: true,
        ru: "Отправить позже: меню отложенной отправки", en: "Send later: the scheduled-message menu" }]);
    } catch (e) {}
    bind();
    refresh(true);
    heartbeat();
    clearInterval(st.poll);
    st.poll = setInterval(() => { heartbeat(); pump(); }, POLL_MS);
    clearInterval(st.watch);
    st.watch = setInterval(() => { if (st.lastChat !== S.currentId) { st.expanded = false; renderBar(); } }, 400);
    window.addEventListener("online", () => pump());
    window.addEventListener("focus", () => { refresh(true).then(pump); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true).then(pump); });
    window.addEventListener("pagehide", () => {
      try { navigator.sendBeacon && navigator.sendBeacon("/api/schedsend/heartbeat", new Blob([JSON.stringify({ closing: true })], { type: "application/json" })); } catch (e) {}
    });
    setTimeout(pump, 4000);

    setInterval(() => { if (!document.hidden) renderBar(); }, 60000);
  }

  window.MemuSchedSend = Object.freeze({
    open: (anchor, options) => openMenu(anchor || document.getElementById("btn-send"), options),
    showAll, refresh, pump, settingsHtml, bindSettings, parseWhen, whenLabel,
    countFor: (chatId) => forChat(chatId).length,
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
