/* MemuAI — Round 37 (plan-2): the gallery of ready-made «Планирование» scenarios (window.MemuPlanTemplates).
   Scenarios use the round-37 conditions: Russia's production calendar, weather (condition and trigger), sunset,
   groups / «НЕ», quiet hours, retries. plan.js calls open(base, pick): base = its classic TEMPLATES() (shown under
   «Классика»), pick(make) opens the builder with make(). Weather / sunset rows take the last city chosen in the
   builder (MemuPlanConds.withPlace); without one the builder asks to pick a city. */
(function () {
  "use strict";
  if (window.MemuPlanTemplates) return;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const I = (name) => (typeof icon === "function" ? icon(name) : "");
  const place = (obj) => (window.MemuPlanConds?.withPlace ? window.MemuPlanConds.withPlace(obj) : obj);
  const country = () => (en() ? "US" : "RU");

  const CATS = () => [["all", T("Все", "All")], ["weather", T("Погода и солнце", "Weather and sun")], ["calendar", T("Календарь", "Calendar")],
    ["reliable", T("Надёжность", "Reliability")], ["classic", T("Классика", "Classic")]];

  /** each scenario: cat, icon, title, desc, chips (what it uses), task (merged over blank()) */
  function scenarios() {
    return [
      { cat: "calendar", icon: "pc-calendar", title: T("Итоги недели в пятницу", "Weekly wrap-up on Friday"),
        desc: T("В последний рабочий день недели в 16:00 — даже если пятница праздничная", "On the last workday of the week at 16:00 — even when Friday is a holiday"),
        chips: [T("последний рабочий день недели", "last workday of the week"), T("производственный календарь", "production calendar")],
        task: { name: T("Итоги недели", "Weekly wrap-up"), prompt: T("Сделай сводку главных новостей ИИ и технологий за неделю ({{date}}): 7 пунктов со ссылками и один вывод.", "Summarize the top AI and tech news of the week ({{date}}): 7 bullet points with links and one takeaway."),
          rule: { when: [{ type: "daily", times: ["16:00"], days: [] }], only_if: [{ type: "calendar", mode: "last_workday_week", country: country() }] } } },
      { cat: "weather", icon: "pc-weather", title: T("Возьми зонт", "Take an umbrella"),
        desc: T("Утром в рабочие дни, если днём обещают дождь или снег", "On workday mornings, if rain or snow is expected"),
        chips: [T("погода", "weather"), T("группа «любое»", "“any” group"), T("рабочие дни", "workdays")],
        task: { name: T("Зонт", "Umbrella"), kind: "notify", prompt: T("Сегодня обещают осадки — возьми зонт ☂", "Rain is expected today — take an umbrella ☂"),
          rule: { when: [{ type: "daily", times: ["07:45"], days: [] }],
            only_if: [{ type: "calendar", mode: "workday", country: country() },
              { type: "group", match: "any", items: [place({ type: "weather", metric: "precip_prob", op: "gt", value: 60 }), place({ type: "weather", metric: "rain" }), place({ type: "weather", metric: "snow" })] }] } } },
      { cat: "weather", icon: "tr-weather", title: T("Начался дождь", "Rain started"),
        desc: T("Сообщить в момент, когда пошёл дождь, — не чаще раза в 3 часа и не ночью", "Tell me the moment rain starts — at most once in 3 hours and not at night"),
        chips: [T("триггер погоды", "weather trigger"), T("тихие часы", "quiet hours")],
        task: { name: T("Дождь", "Rain"), kind: "notify", notify_telegram: true, prompt: T("{{trigger.title}} — закрой окна и сними бельё с балкона.", "{{trigger.title}} — close the windows."),
          rule: { when: [place({ type: "watch", source: "weather", metric: "rain", cooldown: 180 })], options: { quiet_from: "23:00", quiet_to: "07:00" } } } },
      { cat: "weather", icon: "tr-weather", title: T("Заморозки", "Frost warning"),
        desc: T("Когда температура опустится ниже 0 °C — раз в сутки", "When the temperature drops below 0 °C — once a day"),
        chips: [T("триггер погоды", "weather trigger"), T("гистерезис", "hysteresis")],
        task: { name: T("Заморозки", "Frost"), kind: "notify", prompt: T("Похолодало до {{trigger.temp}} °C — укрой растения и проверь шины.", "It got down to {{trigger.temp}} °C — cover the plants and check the tyres."),
          rule: { when: [place({ type: "watch", source: "weather", metric: "temp", op: "lt", value: 0, cooldown: 1440 })] } } },
      { cat: "weather", icon: "pc-sunset", title: T("Вечерний итог дня", "Evening review"),
        desc: T("Через 30 минут после заката, только в будни", "30 minutes after sunset, on workdays only"),
        chips: [T("закат", "sunset"), T("рабочие дни", "workdays")],
        task: { name: T("Итог дня", "Day review"), prompt: T("Задай мне 3 коротких вопроса для вечерней рефлексии о сегодняшнем дне ({{weekday}}) и предложи одну цель на завтра.", "Ask me 3 short questions to reflect on today ({{weekday}}) and suggest one goal for tomorrow."),
          rule: { when: [place({ type: "sun", event: "sunset", offset: 30, days: [] })], only_if: [{ type: "calendar", mode: "workday", country: country() }] } } },
      { cat: "calendar", icon: "pc-calendar", title: T("Отчёт в конце месяца", "Month-end report"),
        desc: T("В последний рабочий день месяца в 15:00", "On the last workday of the month at 15:00"),
        chips: [T("каждый месяц", "monthly"), T("рабочие дни", "workdays")],
        task: { name: T("Отчёт за месяц", "Monthly report"), kind: "notify", prompt: T("Последний рабочий день месяца: закрой отчёты, счета и табель.", "Last workday of the month: close the reports, invoices and timesheet."),
          rule: { when: [{ type: "monthly", mode: "workday", nth: -1, country: country(), time: "15:00" }] } } },
      { cat: "calendar", icon: "pc-calendar", title: T("Перед праздником", "Before a holiday"),
        desc: T("В предпраздничный день в 12:00 — напомнить о планах", "On a pre-holiday day at 12:00 — a reminder about plans"),
        chips: [T("предпраздничный день", "pre-holiday day")],
        task: { name: T("Завтра праздник", "Holiday tomorrow"), kind: "notify", prompt: T("Сегодня сокращённый день, впереди выходные — проверь планы, подарки и поставь автоответ.", "A short day today with days off ahead — check plans and gifts and set an auto-reply."),
          rule: { when: [{ type: "daily", times: ["12:00"], days: [] }], only_if: [{ type: "calendar", mode: "short_day", country: country() }] } } },
      { cat: "calendar", icon: "pc-calendar", title: T("Не в рабочий день", "Not on a workday"),
        desc: T("Идеи на выходной — по субботам, воскресеньям и праздникам в 10:00", "Day-off ideas — on weekends and holidays at 10:00"),
        chips: [T("нерабочий день", "day off")],
        task: { name: T("Идеи на выходной", "Day-off ideas"), prompt: T("Предложи 3 идеи, как провести сегодняшний выходной ({{date}}): одна активная, одна спокойная, одна с друзьями.", "Suggest 3 ideas for today's day off ({{date}}): one active, one calm, one with friends."),
          rule: { when: [{ type: "daily", times: ["10:00"], days: [] }], only_if: [{ type: "calendar", mode: "day_off", country: country() }] } } },
      { cat: "reliable", icon: "retry", title: T("Повтор при ошибке", "Retry on failure"),
        desc: T("Проверка сайта по будням; если не вышло — ещё 3 попытки с паузой 10, 20, 40 мин", "A site check on weekdays; on failure 3 more tries after 10, 20, 40 min"),
        chips: [T("повтор с паузой", "retry with back-off"), T("не больше 4 в день", "at most 4 a day")],
        task: { name: T("Проверить сайт", "Check the site"), kind: "dfy", prompt: T("Открой сайт магазина и проверь статус моего последнего заказа.", "Open the shop website and check the status of my latest order."),
          rule: { when: [{ type: "daily", times: ["10:00"], days: [1, 2, 3, 4, 5] }], options: { retry_count: 3, retry_minutes: 10, max_per_day: 4 } } } },
      { cat: "reliable", icon: "pc-history", title: T("Если прошлый запуск упал", "If the last run failed"),
        desc: T("Каждый час, но только пока прошлый запуск был с ошибкой, и не в выходные", "Every hour, only while the last run failed, not on weekends"),
        chips: [T("результат прошлого запуска", "previous result"), T("НЕ выходной", "NOT a weekend")],
        task: { name: T("Догоняющая проверка", "Catch-up check"), prompt: T("Проверь, работает ли мой сайт, и коротко ответь «ок» или опиши проблему. Прошлый ответ: {{last_result}}", "Check whether my site works and answer “ok” or describe the problem. Last answer: {{last_result}}"),
          rule: { when: [{ type: "interval", every: 60, days: [] }], only_if: [{ type: "last_result", status: "fail" }, { type: "calendar", mode: "weekend", not: true }] } } },
    ];
  }

  function open(base, pick) {
    let cat = "all";
    const list = () => [...scenarios(), ...(base || []).map((b) => ({ cat: "classic", icon: b.icon, title: b.title, desc: b.desc, chips: [], make: b.make }))];
    const ov = document.createElement("div");
    ov.className = "pln-picker ptg-ov";
    const draw = () => {
      const items = list().map((x, i) => ({ ...x, i })).filter((x) => cat === "all" || x.cat === cat);
      ov.innerHTML = `<div class="pln-picker-box ptg-box" role="dialog" aria-modal="true" aria-label="${T("Шаблоны сценариев", "Scenario templates")}">
        <div class="pln-picker-head"><b>${T("Шаблоны сценариев", "Scenario templates")}</b><button class="mini-action" data-x aria-label="${T("Закрыть", "Close")}">${I("close")}</button></div>
        <p class="ptg-lead">${T("Выберите сценарий — он откроется в конструкторе, там можно всё поменять.", "Pick a scenario — it opens in the builder where you can change anything.")}</p>
        <div class="ptg-cats" role="tablist">${CATS().map(([id, label]) => `<button role="tab" class="${id === cat ? "on" : ""}" aria-selected="${id === cat}" data-cat="${id}">${H(label)}</button>`).join("")}</div>
        <div class="ptg-grid">${items.map((x) => `<button class="ptg-card" data-i="${x.i}"><span class="ptg-ic">${I(x.icon)}</span><span class="ptg-txt"><b>${H(x.title)}</b><small>${H(x.desc)}</small>
          ${x.chips.length ? `<span class="ptg-chips">${x.chips.map((c) => `<em>${H(c)}</em>`).join("")}</span>` : ""}</span></button>`).join("")}</div></div>`;
    };
    (document.body).appendChild(ov);
    draw();
    const close = () => ov.remove();
    ov.addEventListener("click", (e) => {
      if (e.target === ov || e.target.closest("[data-x]")) return close();
      const c = e.target.closest("[data-cat]");
      if (c) { cat = c.dataset.cat; draw(); ov.querySelector(`[data-cat="${cat}"]`)?.focus(); return; }
      const card = e.target.closest("[data-i]");
      if (!card) return;
      const x = list()[+card.dataset.i];
      close();
      pick(x.make || (() => x.task));
    });
    ov.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    ov.querySelector(".ptg-card")?.focus();
  }

  window.MemuPlanTemplates = { open, scenarios, count: () => scenarios().length };
})();
