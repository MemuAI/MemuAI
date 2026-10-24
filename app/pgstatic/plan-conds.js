/* MemuAI — Round 37: more conditions and options of «Планирование» (window.MemuPlanConds).
   Engine: app/schedule_conds_ext.py. plan.js calls:
     catalog() — extra «Только если» entries; defaults(cond) — fills a new condition; row(cond, p, ctx) — the body of
     calendar / sun / weather / wifi / last_result / group; head(cond, p) — the «НЕ» toggle of every condition row;
     triggers() / newTrigger(id) — the sunrise / sunset trigger; whenBody(t, p, ctx) — sun and monthly (N-th weekday /
     workday) rows; optionsHtml(o) — quiet hours, a daily limit, retries; checkHtml() — «Проверить сейчас»;
     bind(root, ctx) — once per builder (its own data-act="pcx-*" clicks, place search, mode switches).
   Routes: POST /api/plan/conds/check {task}, GET /api/plan/conds/geo?q=. Values go through plan.js data-path. */
(function () {
  "use strict";
  if (window.MemuPlanConds) return;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const $q = (sel, root = document) => root.querySelector(sel);
  const PLACE_KEY = "memuai.plan.place.v1";

  const SVG = {
    "pc-calendar": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/><path d="m9 15 2 2 4-4"/></svg>',
    "pc-sunset": '<svg viewBox="0 0 24 24"><path d="M3 18h18M5.6 14.5A6.5 6.5 0 0 1 18.4 14.5M12 3v4M4.2 7.2l2 2M19.8 7.2l-2 2M8 21h8"/></svg>',
    "pc-weather": '<svg viewBox="0 0 24 24"><path d="M7 18a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6-1.2A4.2 4.2 0 0 1 17 18z"/></svg>',
    "pc-wifi": '<svg viewBox="0 0 24 24"><path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.6 16a5 5 0 0 1 6.8 0"/><circle cx="12" cy="19.2" r="1"/></svg>',
    "pc-history": '<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
    "pc-group": '<svg viewBox="0 0 24 24"><path d="M8 4H5v16h3M16 4h3v16h-3"/><path d="M9 9h6M9 15h6"/></svg>',
    "pc-quiet": '<svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-9.3-5M6 8c0 7-3 9-3 9h13M17.7 13A20 20 0 0 0 18 8M10.3 21a2 2 0 0 0 3.4 0M3 3l18 18"/></svg>',
  };
  try { if (typeof ICONS === "object" && ICONS) for (const [k, v] of Object.entries(SVG)) if (!ICONS[k]) ICONS[k] = v; } catch (e) { /* no global icon set */ }
  const I = (name) => (typeof icon === "function" && (typeof ICONS !== "object" || ICONS[name]) ? icon(name) : `<span class="ui-icon">${SVG[name] || ""}</span>`);

  /* ------------------------------------------------------------------ dictionaries */
  const COUNTRIES = () => [["RU", T("Россия", "Russia")], ["BY", T("Беларусь", "Belarus")], ["KZ", T("Казахстан", "Kazakhstan")],
    ["LT", T("Литва", "Lithuania")], ["LV", T("Латвия", "Latvia")], ["EE", T("Эстония", "Estonia")], ["PL", T("Польша", "Poland")],
    ["DE", T("Германия", "Germany")], ["FR", T("Франция", "France")], ["GB", T("Великобритания", "United Kingdom")], ["US", T("США", "United States")],
    ["UA", T("Украина", "Ukraine")], ["AM", T("Армения", "Armenia")], ["GE", T("Грузия", "Georgia")], ["RS", T("Сербия", "Serbia")],
    ["TR", T("Турция", "Türkiye")], ["IT", T("Италия", "Italy")], ["ES", T("Испания", "Spain")], ["NL", T("Нидерланды", "Netherlands")],
    ["FI", T("Финляндия", "Finland")], ["CA", T("Канада", "Canada")]];
  const WEEKDAYS = () => (en() ? ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    : ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]);
  const MONTHS = () => (en() ? ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    : ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]);
  const NTH = () => [["1", T("1-й", "1st")], ["2", T("2-й", "2nd")], ["3", T("3-й", "3rd")], ["4", T("4-й", "4th")], ["5", T("5-й", "5th")], ["-1", T("последний", "last")]];
  const CAL_MODES = () => [["workday", T("рабочий день", "a workday")], ["day_off", T("нерабочий день (выходной или праздник)", "a day off (weekend or holiday)")],
    ["weekend", T("выходной (сб, вс)", "a weekend")], ["holiday", T("праздничный день", "a public holiday")], ["short_day", T("предпраздничный (сокращённый) день", "a shortened pre-holiday day")],
    ["first_workday_week", T("первый рабочий день недели", "the first workday of the week")], ["last_workday_week", T("последний рабочий день недели", "the last workday of the week")],
    ["first_workday", T("первый рабочий день месяца", "the first workday of the month")],
    ["last_workday", T("последний рабочий день месяца", "the last workday of the month")], ["nth_weekday", T("N-й день недели месяца", "the N-th weekday of the month")],
    ["month_days", T("числа месяца", "days of the month")], ["months", T("месяцы", "months")], ["week_parity", T("чётная / нечётная неделя", "even / odd week")],
    ["date_range", T("период каждый год", "a yearly period")]];
  const COUNTRY_MODES = ["workday", "holiday", "day_off", "short_day", "first_workday", "last_workday", "first_workday_week", "last_workday_week"];
  const METRICS = () => [["temp", T("температура", "temperature"), "°C"], ["feels", T("ощущается как", "feels like"), "°C"],
    ["wind", T("ветер", "wind"), T("км/ч", "km/h")], ["precip_prob", T("вероятность осадков", "chance of rain"), "%"],
    ["cloud", T("облачность", "cloud cover"), "%"], ["humidity", T("влажность", "humidity"), "%"], ["precip", T("осадки за час", "precipitation"), T("мм", "mm")],
    ["rain", T("идёт дождь", "it is raining"), ""], ["snow", T("идёт снег", "it is snowing"), ""], ["clear", T("ясно", "the sky is clear"), ""]];

  function catalog() {
    return [
      { id: "calendar", icon: "pc-calendar", title: T("Календарь: рабочий день, праздник…", "Calendar: workday, holiday…"), desc: T("Праздники страны, N-й вторник, последний рабочий день, месяцы", "Country holidays, the 2nd Tuesday, the last workday, months") },
      { id: "sun", icon: "pc-sunset", title: T("Светло / темно", "Daylight / dark"), desc: T("По восходу и закату в вашем городе", "By sunrise and sunset in your city") },
      { id: "weather", icon: "pc-weather", title: T("Погода", "Weather"), desc: T("Температура, ветер, дождь, снег — Open-Meteo", "Temperature, wind, rain, snow — Open-Meteo") },
      { id: "wifi", icon: "pc-wifi", title: T("Сеть Wi-Fi", "Wi-Fi network"), desc: T("Дома, в офисе — по имени сети", "At home, at the office — by network name") },
      { id: "last_result", icon: "pc-history", title: T("Результат прошлого запуска", "Result of the previous run"), desc: T("Успех, ошибка или текст в ответе — этой или другой задачи", "Success, failure or text in the answer — of this or another task") },
      { id: "group", icon: "pc-group", title: T("Группа «любое из» / «все»", "A group: “any of” / “all of”"), desc: T("Для правил вида «A и (B или C)»", "For rules like “A and (B or C)”") },
    ];
  }
  function lastPlace() { try { const p = JSON.parse(localStorage.getItem(PLACE_KEY) || "null"); return p && Number.isFinite(p.lat) ? p : null; } catch (e) { return null; } }
  function withPlace(obj) { const p = lastPlace(); if (p) Object.assign(obj, { lat: p.lat, lon: p.lon, place: p.place }); return obj; }
  function defaults(cond) {
    if (cond.type === "calendar") Object.assign(cond, { mode: "workday", country: en() ? "US" : "RU" });
    if (cond.type === "sun") withPlace(Object.assign(cond, { value: "night", offset: 0 }));
    if (cond.type === "weather") withPlace(Object.assign(cond, { metric: "rain" }));
    if (cond.type === "wifi") Object.assign(cond, { ssid: "", value: true });
    if (cond.type === "last_result") Object.assign(cond, { task_id: "", status: "ok" });
    if (cond.type === "group") Object.assign(cond, { match: "any", items: [] });
    return cond;
  }

  /* ------------------------------------------------------------------ pieces */
  const optionList = (pairs, value) => pairs.map(([v, l]) => `<option value="${H(v)}" ${String(v) === String(value) ? "selected" : ""}>${H(l)}</option>`).join("");
  /** a select that re-renders its block after plan.js stored the value */
  const rsel = (path, value, pairs, cls = "") => `<select class="pinput compact ${cls}" data-path="${path}" data-pcx-rerender="1">${optionList(pairs, value)}</select>`;
  function placeHtml(p, cond) {
    const has = Number.isFinite(+cond.lat) && cond.lat !== undefined && cond.lat !== "";
    return `<span class="pcx-place" data-pcx-place="${p}">${I("globe")}<input class="pinput compact" data-pcx-geo="${p}" value="${H(cond.place || "")}"
      placeholder="${T("Город", "City")}" autocomplete="off" aria-label="${T("Город", "City")}" spellcheck="false">
      ${has ? `<em class="pln-hint">${(+cond.lat).toFixed(2)}, ${(+cond.lon).toFixed(2)}</em>` : `<em class="pln-hint warn">${T("выберите город из списка", "pick a city from the list")}</em>`}</span>`;
  }
  function head(cond, p) {
    const on = !!cond.not;
    return `<button type="button" class="pcx-not ${on ? "on" : ""}" data-act="pcx-not" data-path="${p}" aria-pressed="${on}"
      title="${T("Наоборот: условие должно НЕ выполняться", "Inverted: the condition must NOT hold")}">${T("НЕ", "NOT")}</button>
      <button type="button" class="pcx-off ${cond.off ? "on" : ""}" data-act="pcx-off" data-path="${p}" aria-pressed="${!!cond.off}"
      title="${T("Выключить: условие останется в правиле, но не будет проверяться", "Switch off: the condition stays in the rule but is not checked")}">${cond.off ? T("выключено", "off") : T("вкл", "on")}</button>`;
  }
  function ddmm(v) { const m = /^(\d{2})-(\d{2})$/.exec(String(v || "")); return m ? `${m[2]}.${m[1]}` : String(v || ""); }

  function row(cond, p, ctx) {
    const { sel, num, txt } = ctx;
    if (cond.type === "calendar") {
      const mode = cond.mode || "workday";
      let extra = "";
      if (COUNTRY_MODES.includes(mode)) extra = `<span>${T("страна", "country")}</span>${rsel(`${p}.country`, cond.country || "RU", COUNTRIES())}`;
      else if (mode === "nth_weekday") extra = `${sel(`${p}.nth`, cond.nth ?? 1, NTH())}${sel(`${p}.weekday`, cond.weekday || 1, WEEKDAYS().map((d, i) => [String(i + 1), d]))}<span>${T("месяца", "of the month")}</span>`;
      else if (mode === "month_days") extra = `${txt(`${p}.days`, Array.isArray(cond.days) ? cond.days.join(", ") : cond.days, T("1-10, 15, -1 (последнее)", "1-10, 15, -1 (last)"), "pcx-days")}`;
      else if (mode === "week_parity") extra = sel(`${p}.value`, cond.value || "even", [["even", T("чётная", "even")], ["odd", T("нечётная", "odd")]]);
      else if (mode === "date_range") extra = `<span>${T("с", "from")}</span>${txt(`${p}.from`, ddmm(cond.from), "01.06", "pcx-dm")}<span>${T("по", "to")}</span>${txt(`${p}.to`, ddmm(cond.to), "31.08", "pcx-dm")}<em class="pln-hint">${T("ДД.ММ, можно через Новый год", "DD.MM, may span New Year")}</em>`;
      const months = mode === "months" ? `<div class="pln-days pcx-months" role="group" aria-label="${T("Месяцы", "Months")}">${MONTHS().map((m, i) => {
        const on = (cond.months || []).includes(i + 1);
        return `<button type="button" class="${on ? "on" : ""}" data-act="pcx-month" data-path="${p}" data-month="${i + 1}" aria-pressed="${on}">${m}</button>`;
      }).join("")}</div>` : "";
      const note = COUNTRY_MODES.includes(mode)
        ? `<p class="pln-explain">${(cond.country || "RU") === "RU" ? T("Россия — по производственному календарю: с переносами выходных, рабочими субботами и сокращёнными днями (обновляется из интернета раз в год).", "Russia — by the production calendar: moved days off, working Saturdays and shortened days (updated online once a year).")
          : T("Праздники — по официальному календарю страны; переносы рабочих дней для этой страны не учитываются.", "Public holidays follow the country's official calendar; moved working days are not counted for this country.")}</p>` : "";
      return `<div class="pln-line"><span>${T("сегодня", "today is")}</span>${rsel(`${p}.mode`, mode, CAL_MODES(), "wide")}${extra}</div>${months}${note}`;
    }
    if (cond.type === "sun") {
      return `<div class="pln-line">${sel(`${p}.value`, cond.value || "night", [["day", T("светло (между восходом и закатом)", "daylight (sunrise to sunset)")], ["night", T("темно (после заката, до восхода)", "dark (after sunset, before sunrise)")]], "wide")}</div>
        <div class="pln-line">${placeHtml(p, cond)}<span>${T("запас", "margin")}</span>${num(`${p}.offset`, cond.offset || 0, -180, 180)}<span>${T("мин", "min")}</span></div>`;
    }
    if (cond.type === "weather") {
      const metric = cond.metric || "rain";
      const meta = METRICS().find((m) => m[0] === metric) || METRICS()[0];
      const numeric = !!meta[2];
      return `<div class="pln-line">${rsel(`${p}.metric`, metric, METRICS().map((m) => [m[0], m[1]]))}
        ${numeric ? `${sel(`${p}.op`, cond.op || "gt", [["gt", T("выше", "above")], ["lt", T("ниже", "below")]])}<input class="pinput compact num" type="number" step="0.5" data-path="${p}.value" data-num="1" value="${H(cond.value ?? "")}"><span>${H(meta[2])}</span>` : ""}</div>
        <div class="pln-line">${placeHtml(p, cond)}<em class="pln-hint">${T("Open-Meteo, обновляется раз в 20 мин", "Open-Meteo, refreshed every 20 min")}</em></div>`;
    }
    if (cond.type === "wifi") {
      return `<div class="pln-line">${sel(`${p}.value`, String(cond.value !== false), [["true", T("подключён к Wi-Fi", "connected to Wi-Fi")], ["false", T("не подключён к Wi-Fi", "not connected to Wi-Fi")]])}
        ${txt(`${p}.ssid`, cond.ssid, T("Имя сети, пусто — любая", "Network name, empty = any"), "wide")}</div>`;
    }
    if (cond.type === "last_result") {
      const tasks = (ctx.tasks ? ctx.tasks() : []).filter((t) => t.id !== ctx.draft().id);
      return `<div class="pln-line">${sel(`${p}.task_id`, cond.task_id || "", [["", T("прошлый запуск этой задачи", "the previous run of this task")], ...tasks.map((t) => [t.id, T("задача «", "task “") + (t.name || t.id) + T("»", "”")])], "wide")}</div>
        <div class="pln-line"><span>${T("был", "was")}</span>${sel(`${p}.status`, cond.status || "any", [["any", T("любым", "any")], ["ok", T("успешным", "successful")], ["fail", T("с ошибкой", "a failure")]])}
          <span>${T("и содержит", "and contains")}</span>${txt(`${p}.contains`, cond.contains, T("текст (необязательно)", "text (optional)"))}</div>
        <div class="pln-line sub"><span>${T("за последние", "within the last")}</span>${num(`${p}.within_hours`, cond.within_hours || "", 0, 2160).replace("<input ", `<input placeholder="—" `)}<span>${T("ч", "h")}</span></div>`;
    }
    if (cond.type === "group") {
      const items = cond.items || [];
      const any = cond.match === "any";
      const subs = items.map((item, j) => {
        const q = `${p}.items.${j}`;
        const meta = ctx.conditions().find((x) => x.id === item.type) || { icon: "info", title: item.type };
        const glue = j ? `<span class="pln-or">${any ? T("или", "or") : T("и", "and")}</span>` : "";
        return `${glue}<div class="pcx-sub"><div class="pln-row-head">${I(meta.icon)}<b>${H(meta.title)}</b>${head(item, q)}
          <button type="button" class="pln-row-x" data-act="pcx-del" data-path="${q}" aria-label="${T("Убрать", "Remove")}" title="${T("Убрать", "Remove")}">${I("close")}</button></div>
          <div class="pln-row-body">${ctx.condInner(item, q)}</div></div>`;
      }).join("");
      return `<div class="pln-match">${T("Внутри группы", "Inside the group")}: <button type="button" class="${any ? "" : "on"}" data-act="pcx-match" data-path="${p}" data-value="all">${T("все", "all")}</button><button type="button" class="${any ? "on" : ""}" data-act="pcx-match" data-path="${p}" data-value="any">${T("любое", "any")}</button></div>
        <div class="pcx-group">${subs || `<p class="pln-explain">${T("Добавьте 2–3 условия: группа выполняется, когда выполнены все (или любое) из них.", "Add 2–3 conditions: the group holds when all (or any) of them hold.")}</p>`}
        <button type="button" class="pln-add-sm" data-act="pcx-add" data-path="${p}">${I("plus")}${T("условие в группу", "condition to the group")}</button></div>`;
    }
    return null;
  }

  /* ------------------------------------------------------------------ triggers */
  function triggers() {
    return [{ id: "sun", icon: "pc-sunset", title: T("Восход / закат", "Sunrise / sunset"), desc: T("Каждый день в момент восхода или заката, можно со сдвигом", "Every day at sunrise or sunset, optionally shifted") }];
  }
  function newTrigger(id) { return id === "sun" ? withPlace({ type: "sun", event: "sunset", offset: 0, days: [] }) : null; }
  function whenBody(t, p, ctx) {
    const { sel, num, timeIn, daysHtml } = ctx;
    if (t.type === "sun") {
      const off = +t.offset || 0;
      return `<div class="pln-line">${sel(`${p}.event`, t.event || "sunset", [["sunrise", T("восход", "sunrise")], ["sunset", T("закат", "sunset")]])}
          <select class="pinput compact" data-pcx-dir="${p}">${optionList([["0", T("ровно в момент", "exactly at")], ["-1", T("раньше на", "earlier by")], ["1", T("позже на", "later by")]], Math.sign(off))}</select>
          ${off ? `<input class="pinput compact num" type="number" min="1" max="180" data-pcx-off="${p}" value="${Math.abs(off)}"><span>${T("мин", "min")}</span>` : ""}</div>
        <div class="pln-line">${placeHtml(p, t)}</div>${daysHtml(`${p}.days`, t.days)}`;
    }
    if (t.type === "monthly") {
      const mode = t.mode || "day";
      const days = [["-1", T("последний день", "last day")], ...Array.from({ length: 31 }, (_, k) => [String(k + 1), String(k + 1)])];
      let fields = "";
      if (mode === "day") fields = `<span>${T("число", "day")}</span>${sel(`${p}.day`, t.day ?? 1, days)}`;
      else if (mode === "nth_weekday") fields = `${sel(`${p}.nth`, t.nth ?? 1, NTH())}${sel(`${p}.weekday`, t.weekday || 1, WEEKDAYS().map((d, i) => [String(i + 1), d]))}`;
      else fields = `${sel(`${p}.nth`, t.nth ?? -1, [["-1", T("последний", "last")], ...Array.from({ length: 10 }, (_, k) => [String(k + 1), en() ? `${k + 1}${["st", "nd", "rd"][k] || "th"}` : `${k + 1}-й`])])}<span>${T("рабочий день", "workday")}</span>${sel(`${p}.country`, t.country || "RU", COUNTRIES())}`;
      return `<div class="pln-line">${rsel(`${p}.mode`, mode, [["day", T("по числу", "by date")], ["nth_weekday", T("по дню недели", "by weekday")], ["workday", T("по рабочим дням", "by workdays")]])}${fields}<span>${T("в", "at")}</span>${timeIn(`${p}.time`, t.time || "09:00")}</div>`;
    }
    return null;
  }

  /* ------------------------------------------------------------------ options («До каких пор» → тонкая настройка) */
  function optionsHtml(o, ctx) {
    const { num, timeIn } = ctx;
    const quiet = !!(o.quiet_from && o.quiet_to);
    return `<div class="pcx-opts">
      <div class="pln-line"><label class="pln-check"><input type="checkbox" data-act="pcx-quiet" ${quiet ? "checked" : ""}><span>${T("Тихие часы", "Quiet hours")}</span></label>
        ${quiet ? `<span>${T("с", "from")}</span>${timeIn("rule.options.quiet_from", o.quiet_from)}<span>${T("до", "to")}</span>${timeIn("rule.options.quiet_to", o.quiet_to)}` : ""}
        <em class="pln-hint">${T("запуск откладывается до их конца", "a run waits until they end")}</em></div>
      <div class="pln-line"><span>${T("Не больше запусков в день", "At most runs a day")}</span>${num("rule.options.max_per_day", o.max_per_day || "", 0, 1000).replace("<input ", `<input placeholder="—" `)}</div>
      <div class="pln-line"><span>${T("При ошибке повторить", "On failure retry")}</span>${num("rule.options.retry_count", o.retry_count || 0, 0, 5)}<span>${T("раз(а), первый через", "time(s), first after")}</span>${num("rule.options.retry_minutes", o.retry_minutes || 5, 1, 1440)}<span>${T("мин", "min")}</span>
        <em class="pln-hint">${T("пауза удваивается", "the pause doubles")}</em></div></div>`;
  }
  const hasOptions = (o) => !!(o.quiet_from || o.max_per_day || o.retry_count);

  /* ------------------------------------------------------------------ «Проверить сейчас» */
  function checkHtml() {
    return `<div class="pcx-check"><button type="button" class="pbtn ghost compact" data-act="pcx-check">${I("check")}${T("Проверить условия сейчас", "Check the conditions now")}</button><div class="pcx-check-host" aria-live="polite"></div></div>`;
  }
  function checkResult(res) {
    const mark = (ok) => (ok === true ? `<i class="pcx-dot ok" aria-label="${T("выполнено", "holds")}">✓</i>` : ok === false ? `<i class="pcx-dot bad" aria-label="${T("не выполнено", "does not hold")}">✕</i>` : `<i class="pcx-dot unk" aria-label="${T("неизвестно", "unknown")}">?</i>`);
    const rows = (res.rows || []).map((r) => `<li class="d${r.depth}">${mark(r.ok)}<span><b>${H(r.text)}</b>${r.detail ? `<small>${H(r.detail)}</small>` : ""}</span></li>`).join("");
    const notes = (res.notes || []).map((n) => `<li>${mark(false)}<span><b>${H(n)}</b></span></li>`).join("");
    const verdict = res.ok ? T("Сейчас задача запустилась бы.", "The task would run now.") : (res.reason || T("Сейчас задача не запустилась бы.", "The task would not run now."));
    return `<div class="pcx-check-box ${res.ok ? "ok" : "bad"}"><div class="pcx-verdict">${mark(!!res.ok)}<b>${H(verdict)}</b></div>${rows || notes ? `<ul>${rows}${notes}</ul>` : ""}</div>`;
  }

  /* ------------------------------------------------------------------ binding */
  function bind(root, ctx) {
    let geoTimer = 0, geoSeq = 0;
    const closeGeo = () => root.querySelectorAll(".pcx-geo-list").forEach((el) => el.remove());
    root.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act^='pcx-']");
      if (!btn || !root.contains(btn)) { if (!e.target.closest(".pcx-place")) closeGeo(); return; }
      const act = btn.dataset.act, path = btn.dataset.path;
      if (act === "pcx-quiet") return;   // a checkbox — handled on "change"
      if (act === "pcx-not") { const c = ctx.get(path); if (c.not) delete c.not; else c.not = true; return ctx.rerender("if"); }
      if (act === "pcx-off") { const c = ctx.get(path); if (c.off) delete c.off; else c.off = true; return ctx.rerender("if"); }
      if (act === "pcx-match") { ctx.get(path).match = btn.dataset.value; return ctx.rerender("if"); }
      if (act === "pcx-month") {
        const c = ctx.get(path), m = +btn.dataset.month, set = new Set(c.months || []);
        if (set.has(m)) { if (set.size > 1) set.delete(m); } else set.add(m);
        c.months = [...set].sort((a, b) => a - b);
        return ctx.rerender("if");
      }
      if (act === "pcx-del") {
        const parts = path.split("."), j = +parts.pop(), list = ctx.get(parts.join("."));
        list.splice(j, 1);
        return ctx.rerender("if");
      }
      if (act === "pcx-add") {
        const group = ctx.get(path);
        return ctx.popMenu(btn, ctx.conditions().filter((c) => c.id !== "group").map((c) => ({ icon: c.icon, title: c.title, desc: c.desc, run: () => {
          group.items = group.items || [];
          group.items.push(ctx.newCondition(c.id));
          ctx.rerender("if");
        } })));
      }
      if (act === "pcx-check") {
        const host = btn.parentElement.querySelector(".pcx-check-host");
        btn.disabled = true;
        host.innerHTML = `<span class="pln-spin"></span>`;
        try { host.innerHTML = checkResult(await ctx.call("/api/plan/conds/check", { task: ctx.serverDraft() })); }
        catch (err) { host.innerHTML = `<div class="pcx-check-box bad"><div class="pcx-verdict"><i class="pcx-dot bad">✕</i><b>${H(err.message || String(err))}</b></div></div>`; }
        btn.disabled = false;
      }
      if (act === "pcx-pick") {
        const p = btn.dataset.path, target = ctx.get(p);
        Object.assign(target, { lat: +btn.dataset.lat, lon: +btn.dataset.lon, place: btn.dataset.place });
        try { localStorage.setItem(PLACE_KEY, JSON.stringify({ lat: +btn.dataset.lat, lon: +btn.dataset.lon, place: btn.dataset.place })); } catch (err) { /* private mode */ }
        closeGeo();
        ctx.rerender(p.startsWith("rule.when") ? "when" : "if");
      }
    });
    root.addEventListener("change", (e) => {
      const el = e.target;
      if (el.dataset.act === "pcx-quiet") {
        const o = ctx.get("rule.options");
        if (el.checked) { o.quiet_from = o.quiet_from || "23:00"; o.quiet_to = o.quiet_to || "07:00"; } else { delete o.quiet_from; delete o.quiet_to; }
        return ctx.rerender("until");
      }
      if (el.dataset.pcxDir !== undefined || el.dataset.pcxOff !== undefined) {
        const p = el.dataset.pcxDir ?? el.dataset.pcxOff, t = ctx.get(p), line = el.closest(".pln-line");
        const dir = +$q("[data-pcx-dir]", line).value, abs = Math.max(1, Math.min(180, +($q("[data-pcx-off]", line)?.value || 30)));
        t.offset = dir * abs;
        return ctx.rerender("when");
      }
      if (el.dataset.pcxRerender) {
        const path = el.dataset.path, base = path.replace(/\.[^.]+$/, ""), c = ctx.get(base);
        if (/\.mode$/.test(path)) fillMode(c);
        if (/\.metric$/.test(path) && ["rain", "snow", "clear"].includes(c.metric)) { delete c.op; delete c.value; }
        else if (/\.metric$/.test(path)) { c.op = c.op || "gt"; if (c.value === undefined || c.value === "") c.value = c.metric === "temp" || c.metric === "feels" ? 25 : c.metric === "wind" ? 30 : 60; }
        ctx.rerender(path.startsWith("rule.when") ? "when" : "if");
      }
    });
    root.addEventListener("input", (e) => {
      const el = e.target;
      if (el.dataset.pcxGeo === undefined) return;
      const p = el.dataset.pcxGeo, q = el.value.trim(), seq = ++geoSeq;
      clearTimeout(geoTimer);
      if (q.length < 2) { closeGeo(); return; }
      geoTimer = setTimeout(async () => {
        let places = [], error = "";
        try { places = (await api(`/api/plan/conds/geo?q=${encodeURIComponent(q)}&lang=${en() ? "en" : "ru"}`)).places || []; } catch (err) { error = err.message || String(err); }
        if (seq !== geoSeq) return;
        closeGeo();
        const box = el.closest(".pcx-place");
        if (!box) return;
        const list = document.createElement("div");
        list.className = "pcx-geo-list";
        list.setAttribute("role", "listbox");
        list.innerHTML = places.length ? places.map((pl) => `<button type="button" role="option" data-act="pcx-pick" data-path="${H(p)}" data-lat="${pl.lat}" data-lon="${pl.lon}" data-place="${H(pl.place)}"><b>${H(pl.place)}</b><small>${H(pl.label)}</small></button>`).join("")
          : `<p>${H(error || T("Ничего не найдено", "Nothing found"))}</p>`;
        box.appendChild(list);
      }, 350);
    });
    root.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && root.querySelector(".pcx-geo-list")) { closeGeo(); e.stopPropagation(); }
      if (e.key === "Enter" && e.target.dataset?.pcxGeo !== undefined) { e.preventDefault(); root.querySelector(".pcx-geo-list [data-act='pcx-pick']")?.click(); }
    }, true);
  }
  function fillMode(c) {
    if (c.type === "monthly") {
      for (const k of ["day", "nth", "weekday", "country"]) delete c[k];
      if (c.mode === "day") { delete c.mode; c.day = 1; } else if (c.mode === "nth_weekday") { c.nth = 1; c.weekday = 1; } else { c.nth = -1; c.country = en() ? "US" : "RU"; }
      return;
    }
    if (c.type !== "calendar") return;
    const keep = { type: c.type, mode: c.mode, ...(c.not ? { not: true } : {}) };
    for (const k of Object.keys(c)) delete c[k];
    Object.assign(c, keep);
    if (COUNTRY_MODES.includes(c.mode)) c.country = en() ? "US" : "RU";
    if (c.mode === "nth_weekday") { c.nth = 1; c.weekday = 2; }
    if (c.mode === "month_days") c.days = "1-10";
    if (c.mode === "months") c.months = [6, 7, 8];
    if (c.mode === "week_parity") c.value = "even";
    if (c.mode === "date_range") { c.from = "01.06"; c.to = "31.08"; }
  }

  window.MemuPlanConds = { catalog, defaults, row, head, triggers, newTrigger, whenBody, optionsHtml, hasOptions, checkHtml, bind, placeHtml, withPlace };
})();
