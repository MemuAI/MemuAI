/* MemuAI — Round 35: data & integration triggers of «Планирование» (window.MemuPlanTriggers).
   A rule trigger {"type":"watch","source":…} (app/schedule_triggers_ext.py): Gmail, Calendar, GitHub, Sheets, Drive,
   Notion, RSS / web pages / webhooks, panel values, Proxy quota and models, folders, computer state, «all of».
   plan.js calls: menuItems(add) — the «Когда» menu entries (a category opens the picker); row(t, i, p, ctx) — the
   builder row of a watch; bind(root, ctx) — once per builder; cardHealth(task) — the health line of a task card.
   Routes: /api/plan/triggers/catalog | check | health | pick. Field changes go through plan.js (data-path). */
(function () {
  "use strict";
  if (window.MemuPlanTriggers) return;
  const en = () => (typeof PG_LANG !== "undefined" && PG_LANG === "en");
  const T = (ru, eng) => (en() ? eng : ru);
  const H = (value) => (typeof esc === "function" ? esc(value) : String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
  const $q = (sel, root = document) => root.querySelector(sel);

  /* ------------------------------------------------------------------ icons (added to the global set) */
  const SVG = {
    "tr-mail": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>',
    "tr-calendar": '<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18M8 14h3"/></svg>',
    "tr-github": '<svg viewBox="0 0 24 24"><path d="M9 19c-4 1.3-4-2-6-2.5M15 21v-3.4a3 3 0 0 0-.8-2.3c2.7-.3 5.6-1.3 5.6-6a4.6 4.6 0 0 0-1.3-3.2 4.3 4.3 0 0 0-.1-3.2s-1-.3-3.3 1.3a11.3 11.3 0 0 0-6 0C6.8 2.6 5.8 2.9 5.8 2.9a4.3 4.3 0 0 0-.1 3.2A4.6 4.6 0 0 0 4.4 9.3c0 4.6 2.9 5.6 5.6 6a3 3 0 0 0-.8 2.3V21"/></svg>',
    "tr-sheet": '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M4 15h16M10 9v12"/></svg>',
    "tr-drive": '<svg viewBox="0 0 24 24"><path d="m8.5 4-6 10.5 3 5.5h13l3-5.5-6-10.5z"/><path d="m8.5 4 6.5 10.5H2.5M15.5 4 9 14.5M5.5 20l3.5-5.5h12"/></svg>',
    "tr-notion": '<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2.5"/><path d="M9 16V8l6 8V8"/></svg>',
    "tr-rss": '<svg viewBox="0 0 24 24"><path d="M5 11a8 8 0 0 1 8 8M5 5a14 14 0 0 1 14 14"/><circle cx="6" cy="18" r="1.4"/></svg>',
    "tr-web": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M7 6.5h.01M10 6.5h.01M8 14l-2 2 2 2M16 14l2 2-2 2"/></svg>',
    "tr-webhook": '<svg viewBox="0 0 24 24"><path d="M10 7a3 3 0 1 1 4.5 2.6L17 14M7.5 20.5A3 3 0 0 1 7 14.6L9.5 10M17 18H11.5"/><circle cx="17.5" cy="17.5" r="2.5"/></svg>',
    "tr-panel": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="8" height="7" rx="1.5"/><rect x="13" y="4" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="7" rx="1.5"/><path d="M13 19h8"/></svg>',
    "tr-quota": '<svg viewBox="0 0 24 24"><path d="M4 18a8 8 0 1 1 16 0"/><path d="m12 18 4-5"/><path d="M4 18h16"/></svg>',
    "tr-model": '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
    "tr-folder": '<svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10v6M9 13h6"/></svg>',
    "tr-system": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4M7 12l2.5-3 2.5 2 2.5-3 2.5 2"/></svg>',
    "tr-weather": '<svg viewBox="0 0 24 24"><path d="M7 15a4 4 0 0 1-.5-8 5.5 5.5 0 0 1 10.6-1.2A4.2 4.2 0 0 1 17 15z"/><path d="M8 18.5 7 21M12 18.5l-1 2.5M16 18.5l-1 2.5"/></svg>',
    "tr-logic": '<svg viewBox="0 0 24 24"><circle cx="9" cy="12" r="5"/><circle cx="15" cy="12" r="5"/></svg>',
    "tr-plug": '<svg viewBox="0 0 24 24"><path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0zM12 16v5"/></svg>',
  };
  try { if (typeof ICONS === "object" && ICONS) for (const [k, v] of Object.entries(SVG)) if (!ICONS[k]) ICONS[k] = v; } catch (e) { /* no global icon set */ }
  const I = (name) => (typeof icon === "function" && (typeof ICONS !== "object" || ICONS[name]) ? icon(name) : `<span class="ui-icon">${SVG[name] || ""}</span>`);
  const ICON = { mail: "tr-mail", calendar: "tr-calendar", github: "tr-github", sheet: "tr-sheet", drive: "tr-drive", notion: "tr-notion",
    rss: "tr-rss", web: "tr-web", webhook: "tr-webhook", panel: "tr-panel", quota: "tr-quota", model: "tr-model", folder: "tr-folder",
    system: "tr-system", weather: "tr-weather", logic: "tr-logic" };

  /* ------------------------------------------------------------------ catalog (titles stay here: RU/EN in one place) */
  const CATS = () => [
    { id: "mail", icon: "tr-mail", title: T("Почта и календарь", "Mail and calendar"), desc: T("Новое письмо, скоро встреча", "A new email, a meeting soon") },
    { id: "github", icon: "tr-github", title: "GitHub", desc: T("Issue, PR, ревью, упавший Actions, релиз", "Issue, PR, review, failed Actions, release") },
    { id: "google", icon: "tr-sheet", title: T("Google Таблицы и Диск", "Google Sheets and Drive"), desc: T("Новая строка, изменилась ячейка, новый файл", "A new row, a cell changed, a new file") },
    { id: "notion", icon: "tr-notion", title: "Notion", desc: T("Страница или база изменилась", "A page or a database changed") },
    { id: "web", icon: "tr-web", title: T("Сайты, RSS и вебхуки", "Websites, RSS and webhooks"), desc: T("Изменилась страница, новая запись, вызов", "A page changed, a new item, a call") },
    { id: "memuai", icon: "tr-panel", title: T("Данные MemuAI", "MemuAI data"), desc: T("Значение на панели, квота, новая модель", "A panel value, quota, a new model") },
    { id: "system", icon: "tr-system", title: T("Компьютер и файлы", "Computer and files"), desc: T("Новый файл в папке, диск, память", "A new file in a folder, disk, memory") },
    { id: "weather", icon: "tr-weather", title: T("Погода", "Weather"), desc: T("Начался дождь, похолодало, сильный ветер", "Rain started, it got cold, strong wind") },
    { id: "logic", icon: "tr-logic", title: T("Составные", "Compound"), desc: T("Несколько событий вместе", "Several events together") },
  ];
  const SOURCES = () => ({
    gmail: { cat: "mail", icon: "mail", service: "gmail", title: T("Новое письмо", "New email"), desc: T("Письмо по поиску Gmail: от банка, с вложением…", "An email matching a Gmail search: from the bank, with an attachment…"), make: () => ({ query: "" }) },
    calendar_before: { cat: "mail", icon: "calendar", service: "google_calendar", title: T("За N минут до встречи", "N minutes before an event"), desc: T("Каждое событие Google Календаря", "Every Google Calendar event"), make: () => ({ minutes: 10, calendar: "primary", skip_all_day: true }) },
    calendar_created: { cat: "mail", icon: "calendar", service: "google_calendar", title: T("Новое событие в календаре", "New calendar event"), desc: T("Вас пригласили или вы добавили встречу", "You were invited or added a meeting"), make: () => ({ calendar: "primary" }) },
    calendar_changed: { cat: "mail", icon: "calendar", service: "google_calendar", title: T("Событие календаря изменено", "Calendar event changed"), desc: T("Перенесли время, сменили место", "The time or place changed"), make: () => ({ calendar: "primary" }) },
    github_issue: { cat: "github", icon: "github", service: "github", title: T("Новый issue", "New issue"), desc: T("В репозитории открыли issue", "An issue opened in a repository"), make: (c) => ({ repo: c?.github_repo || "" }) },
    github_pr: { cat: "github", icon: "github", service: "github", title: T("Новый pull request", "New pull request"), desc: T("Открыли PR", "A PR opened"), make: (c) => ({ repo: c?.github_repo || "" }) },
    github_review: { cat: "github", icon: "github", service: "github", title: T("Меня попросили о ревью", "Review requested from me"), desc: T("Во всех репозиториях или в одном", "In all repositories or one"), make: () => ({ repo: "" }) },
    github_actions: { cat: "github", icon: "github", service: "github", title: T("Упал GitHub Actions", "GitHub Actions failed"), desc: T("Запуск workflow завершился ошибкой", "A workflow run failed"), make: (c) => ({ repo: c?.github_repo || "" }) },
    github_release: { cat: "github", icon: "github", service: "github", title: T("Новый релиз", "New release"), desc: T("Вышла новая версия", "A new version is out"), make: (c) => ({ repo: c?.github_repo || "" }) },
    sheets_row: { cat: "google", icon: "sheet", service: "google_sheets", title: T("Новая строка в таблице", "New row in a sheet"), desc: T("Например, новая заявка из Google Формы", "E.g. a new Google Forms response"), make: () => ({ sheet: "", tab: "" }) },
    sheets_cell: { cat: "google", icon: "sheet", service: "google_sheets", title: T("Ячейка изменилась", "A cell changed"), desc: T("Или перешла порог: больше, меньше, равно", "Or crossed a value: greater, less, equal"), make: () => ({ sheet: "", cell: "A1", op: "changed" }) },
    drive_file: { cat: "google", icon: "drive", service: "google_drive", title: T("Новый файл на Диске", "New file on Drive"), desc: T("В папке или по имени", "In a folder or by name"), make: () => ({ folder: "" }) },
    notion_page: { cat: "notion", icon: "notion", service: "notion", title: T("Страница изменилась", "A page changed"), desc: T("Кто-то отредактировал страницу", "Someone edited the page"), make: () => ({ page: "" }) },
    notion_db: { cat: "notion", icon: "notion", service: "notion", title: T("Запись в базе данных", "A database entry"), desc: T("Новая или изменённая запись", "A new or changed entry"), make: () => ({ database: "", mode: "new" }) },
    rss: { cat: "web", icon: "rss", service: "", title: T("Новая запись RSS", "New RSS item"), desc: T("Блог, новости, YouTube-канал", "A blog, news, a YouTube channel"), make: () => ({ url: "" }) },
    web_page: { cat: "web", icon: "web", service: "", title: T("Страница сайта изменилась", "A web page changed"), desc: T("Целиком или только нужный блок; появился или пропал текст", "Whole or one block; a text appeared or vanished"), make: () => ({ url: "", mode: "changed" }) },
    webhook: { cat: "web", icon: "webhook", service: "", title: T("Вызов вебхука", "A webhook call"), desc: T("Другая программа запускает задачу запросом", "Another program starts the task with a request"), make: () => ({ token: token() }) },
    panel: { cat: "memuai", icon: "panel", service: "", title: T("Значение на панели", "A panel value"), desc: T("Курс, погода, цена… больше, меньше, изменилось", "A rate, weather, a price… greater, less, changed"), make: (c) => { const p = (c?.panels || [])[0]; const f = p ? (p.fields.find((x) => x.numeric) || p.fields[0]) : null; return { panel: p?.id || "", key: f?.key || "", op: f?.numeric ? "gt" : "changed", value: "" }; } },
    quota_low: { cat: "memuai", icon: "quota", service: "", title: T("Квота заканчивается", "Quota runs low"), desc: T("По прогнозу планировщика квот Proxy", "By the Proxy quota planner forecast"), make: () => ({ account: "", level: "runs_out" }) },
    proxy_model: { cat: "memuai", icon: "model", service: "", title: T("Новая модель в Proxy", "New model in the Proxy"), desc: T("Например, появилась новая Claude", "E.g. a new Claude appeared"), make: () => ({ match: "" }) },
    folder_file: { cat: "system", icon: "folder", service: "", title: T("Новый файл в папке", "New file in a folder"), desc: T("Скачали PDF, сохранили скриншот…", "A PDF downloaded, a screenshot saved…"), make: () => ({ path: "", pattern: "" }) },
    system: { cat: "system", icon: "system", service: "", title: T("Состояние компьютера", "Computer state"), desc: T("Мало места на диске, память, процессор, заряд", "Low disk space, memory, CPU, battery"), make: () => ({ metric: "disk_free_pct", op: "lt", value: 10 }) },
    weather: { cat: "weather", icon: "weather", service: "", title: T("Погода изменилась", "The weather changed"), desc: T("Начнётся дождь или снег, температура перейдёт порог — Open-Meteo", "Rain or snow starts, the temperature crosses a value — Open-Meteo"), make: () => { const o = { metric: "rain", cooldown: 180 }; return window.MemuPlanConds?.withPlace ? window.MemuPlanConds.withPlace(o) : o; } },
    all: { cat: "logic", icon: "logic", service: "", title: T("Всё вместе", "All together"), desc: T("Сработали все выбранные события за N минут", "All chosen events happened within N minutes"), make: () => ({ within: 60, items: [{ source: "gmail", query: "" }, { source: "calendar_before", minutes: 10, calendar: "primary" }] }) },
  });
  const SERVICE_TITLE = { gmail: "Gmail", google_calendar: "Google Calendar", google_drive: "Google Drive", google_sheets: "Google Sheets", notion: "Notion", github: "GitHub" };
  const LIST_SOURCES = ["gmail", "github_issue", "github_pr", "github_review", "sheets_row", "drive_file", "notion_db", "rss", "folder_file", "calendar_created", "calendar_changed"];
  const st = { cat: null, catLoading: null, health: {}, healthAt: 0, checks: {}, ctx: null };

  function token() {
    const a = new Uint8Array(18);
    (crypto?.getRandomValues ? crypto.getRandomValues(a) : a.forEach((_, i) => { a[i] = Math.random() * 256; }));
    return btoa(String.fromCharCode(...a)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  async function loadCatalog(force = false) {
    if (st.cat && !force) return st.cat;
    if (!st.catLoading) {
      st.catLoading = api(`/api/plan/triggers/catalog?lang=${en() ? "en" : "ru"}`).then((c) => { st.cat = c; return c; })
        .catch(() => { st.cat = { sources: [], services: {}, panels: [], quota: [] }; return st.cat; })
        .finally(() => { st.catLoading = null; });
    }
    return st.catLoading;
  }
  const connected = (service) => !service || !!(st.cat?.services?.[service]?.connected);
  const metaOf = (source) => SOURCES()[source] || { title: source, icon: "logic", cat: "logic", service: "" };
  function newTrigger(source) {
    const m = metaOf(source);
    return { type: "watch", source, ...(m.make ? m.make(st.cat) : {}) };
  }
  function openIntegrations() {
    try { if (typeof openSettingsPage === "function") { openSettingsPage("integrations"); return; } } catch (e) { /* fall through */ }
    if (typeof toast === "function") toast(T("Откройте Настройки → Интеграции", "Open Settings → Integrations"));
  }

  /* ------------------------------------------------------------------ «Когда» menu + the categorized picker */
  function menuItems(add) {
    const items = [{ sep: T("Интеграции и данные", "Integrations and data") }];
    for (const c of CATS()) items.push({ icon: c.icon, title: c.title, desc: c.desc, run: () => openPicker(c.id, add) });
    return items;
  }
  async function openPicker(catId, add) {
    // Round 35 (finish-c): on <body> — inside the scrolling #panel the overlay scrolled away and left the footer undimmed
    const host = document.body;
    const ov = document.createElement("div");
    ov.className = "pln-picker ptx-picker";
    const draw = () => {
      const src = SOURCES();
      const cats = CATS();
      ov.innerHTML = `<div class="pln-picker-box ptx-picker-box" role="dialog" aria-modal="true" aria-label="${T("Выбор триггера", "Choose a trigger")}">
        <div class="pln-picker-head"><b>${T("Когда запускать: данные и интеграции", "When to run: data and integrations")}</b><button class="mini-action" data-x aria-label="${T("Закрыть", "Close")}">${I("close")}</button></div>
        <div class="ptx-picker-main">
          <nav class="ptx-cats" role="tablist" aria-orientation="vertical">${cats.map((c) => `<button role="tab" class="${c.id === catId ? "on" : ""}" aria-selected="${c.id === catId}" data-cat="${c.id}">${I(c.icon)}<span>${H(c.title)}</span></button>`).join("")}</nav>
          <div class="ptx-srcs" role="tabpanel">${Object.entries(src).filter(([, m]) => m.cat === catId).map(([id, m]) => {
            const off = m.service && !connected(m.service);
            return `<button class="ptx-src" data-src="${id}">${I(ICON[m.icon] || "tr-logic")}<span><b>${H(m.title)}</b><small>${H(m.desc)}</small></span>${off ? `<em class="ptx-badge warn">${T("не подключено", "not connected")}</em>` : ""}</button>`;
          }).join("")}
          ${catId === "memuai" && !(st.cat?.panels || []).length ? `<p class="pln-explain">${I("info")}${T("Для условий по панелям сначала добавьте панель с данными на главный экран.", "For panel conditions add a data panel to the home screen first.")}</p>` : ""}</div>
        </div></div>`;
    };
    host.appendChild(ov);
    if (!st.cat) await loadCatalog();
    draw();
    const close = () => ov.remove();
    ov.addEventListener("click", (e) => {
      if (e.target === ov || e.target.closest("[data-x]")) return close();
      const c = e.target.closest("[data-cat]");
      if (c) { catId = c.dataset.cat; draw(); $q(`[data-cat="${catId}"]`, ov)?.focus(); return; }
      const s = e.target.closest("[data-src]");
      if (s) { close(); add(newTrigger(s.dataset.src)); }
    });
    ov.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); return; }
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && e.target.closest(".ptx-cats")) {
        e.preventDefault();
        const list = [...ov.querySelectorAll("[data-cat]")];
        const i = list.indexOf(e.target.closest("[data-cat]"));
        const next = list[(i + (e.key === "ArrowDown" ? 1 : list.length - 1)) % list.length];
        catId = next.dataset.cat; draw(); $q(`[data-cat="${catId}"]`, ov)?.focus();
      }
    });
    ($q(".ptx-src", ov) || $q("[data-x]", ov))?.focus();
  }

  /* ------------------------------------------------------------------ builder row */
  function opsList(numericOnly) {
    const all = [["gt", T("больше", "greater than")], ["ge", T("не меньше", "at least")], ["lt", T("меньше", "less than")], ["le", T("не больше", "at most")],
      ["eq", T("равно", "equals")], ["ne", T("не равно", "is not")], ["contains", T("содержит", "contains")], ["not_contains", T("не содержит", "does not contain")], ["changed", T("изменилось", "changed")]];
    return numericOnly ? all.filter(([id]) => ["gt", "ge", "lt", "le"].includes(id)) : all;
  }
  function compareHtml(t, p, c, numericOnly = false) {
    const op = t.op || (numericOnly ? "lt" : "gt");
    const numeric = ["gt", "ge", "lt", "le"].includes(op);
    return `<div class="pln-line"><span>${T("если значение", "when the value")}</span>${c.sel(`${p}.op`, op, opsList(numericOnly), "ptx-rerender")}
      ${op !== "changed" ? c.txt(`${p}.value`, t.value ?? "", numeric ? "100" : T("текст", "text"), "short") : ""}</div>
      ${numeric || op === "changed" ? `<div class="pln-line sub"><span>${numeric ? T("снова сработать, только когда значение отойдёт на", "fire again only after the value moves back by") : T("не реагировать на изменения меньше", "ignore changes smaller than")}</span>${c.num(`${p}.hysteresis`, t.hysteresis || "", 0, 1e9).replace("<input ", `<input placeholder="0" `)}<em class="pln-hint">${T("гистерезис", "hysteresis")}</em></div>` : ""}
      ${op !== "changed" ? `<label class="pln-check"><input type="checkbox" data-path="${p}.initial" ${t.initial !== false ? "checked" : ""}><span>${T("сработать сразу, если условие уже выполняется", "fire at once if the condition already holds")}</span></label>` : ""}`;
  }
  const pickBtn = (what, path, parent = "") => `<button type="button" class="pbtn ghost compact" data-ptx-act="pick" data-what="${what}" data-target="${path}" ${parent ? `data-parent-path="${parent}"` : ""}>${I("list")}${T("Выбрать", "Choose")}</button>`;
  function fieldsHtml(t, p, c) {
    const s = t.source;
    const repo = (opt) => `<div class="pln-line"><span>${T("репозиторий", "repository")}</span>${c.txt(`${p}.repo`, t.repo, opt ? T("все (необязательно)", "all (optional)") : "owner/name", "wide")}${pickBtn("github_repos", `${p}.repo`)}</div>`;
    const each = LIST_SOURCES.includes(s) ? `<label class="pln-check"><input type="checkbox" data-path="${p}.each" ${t.each ? "checked" : ""}><span>${T("запускать отдельно для каждого нового элемента", "run separately for every new item")}</span></label>` : "";
    switch (s) {
      case "gmail": return `<div class="pln-line">${c.txt(`${p}.query`, t.query, "from:bank.ru is:unread", "wide")}${pickBtn("gmail_labels", `${p}.query`)}</div>
        <div class="pln-chips">${[["from:", T("от кого", "from")], ["subject:", T("тема", "subject")], ["has:attachment", T("с вложением", "with attachment")], ["is:important", T("важные", "important")], ["is:unread", T("непрочитанные", "unread")]].map(([v, l]) => `<button type="button" class="pln-ex" data-ptx-act="q-add" data-target="${p}.query" data-value="${H(v)}">${H(l)}</button>`).join("")}</div>
        <p class="pln-explain">${T("Поиск как в Gmail. Уже лежащие в почте письма не запускают задачу — только новые.", "Search as in Gmail. Emails already in the mailbox do not start the task — only new ones.")}</p>${each}`;
      case "calendar_before": return `<div class="pln-line"><span>${T("за", "")}</span>${c.num(`${p}.minutes`, t.minutes ?? 10, 0, 1440)}<span>${T("мин до начала каждого события", "min before each event starts")}</span></div>
        <div class="pln-line"><span>${T("календарь", "calendar")}</span>${c.txt(`${p}.calendar`, t.calendar || "primary", "primary", "")}${pickBtn("calendars", `${p}.calendar`)}</div>
        <div class="pln-line"><span>${T("только события со словом", "only events with")}</span>${c.txt(`${p}.match`, t.match, T("необязательно", "optional"), "")}</div>
        <label class="pln-check"><input type="checkbox" data-path="${p}.skip_all_day" ${t.skip_all_day !== false ? "checked" : ""}><span>${T("пропускать события на весь день", "skip all-day events")}</span></label>`;
      case "calendar_created": case "calendar_changed": return `<div class="pln-line"><span>${T("календарь", "calendar")}</span>${c.txt(`${p}.calendar`, t.calendar || "primary", "primary", "")}${pickBtn("calendars", `${p}.calendar`)}</div>
        <div class="pln-line"><span>${T("только со словом", "only with")}</span>${c.txt(`${p}.match`, t.match, T("необязательно", "optional"), "")}</div>${each}`;
      case "github_issue": return `${repo()}<div class="pln-line"><span>${T("метки", "labels")}</span>${c.txt(`${p}.labels`, t.labels, "bug,urgent", "")}<em class="pln-hint">${T("необязательно", "optional")}</em></div>${each}`;
      case "github_pr": case "github_release": return repo() + each;
      case "github_review": return repo(true) + each;
      case "github_actions": return `${repo()}<div class="pln-line"><span>${T("ветка", "branch")}</span>${c.txt(`${p}.branch`, t.branch, "main", "short")}<span>${T("workflow", "workflow")}</span>${c.txt(`${p}.workflow`, t.workflow, T("любой", "any"), "")}</div>`;
      case "sheets_row": case "sheets_cell": {
        const sheet = `<div class="pln-line"><span>${T("таблица", "sheet")}</span>${c.txt(`${p}.sheet`, t.sheet, T("ссылка на Google Таблицу", "Google Sheets link"), "wide")}${pickBtn("sheets", `${p}.sheet`)}</div>`;
        if (s === "sheets_row") return `${sheet}<div class="pln-line"><span>${T("лист", "tab")}</span>${c.txt(`${p}.tab`, t.tab, T("первый", "first"), "")}${pickBtn("sheet_tabs", `${p}.tab`, `${p}.sheet`)}</div>
          <p class="pln-explain">${T("Первая строка считается заголовками — в задаче поля доступны по их именам.", "The first row is treated as headers — fields are available by their names in the task.")}</p>${each}`;
        return `${sheet}<div class="pln-line"><span>${T("ячейка", "cell")}</span>${c.txt(`${p}.cell`, t.cell, "Sheet1!B2", "short")}</div>${compareHtml(t, p, c)}`;
      }
      case "drive_file": return `<div class="pln-line"><span>${T("папка", "folder")}</span>${c.txt(`${p}.folder`, t.folder, T("весь Диск", "the whole Drive"), "wide")}${pickBtn("drive_folders", `${p}.folder`)}</div>
        <div class="pln-line"><span>${T("имя содержит", "name contains")}</span>${c.txt(`${p}.name_has`, t.name_has, T("необязательно", "optional"), "")}</div>${each}`;
      case "notion_page": return `<div class="pln-line">${c.txt(`${p}.page`, t.page, T("ссылка на страницу Notion", "Notion page link"), "wide")}${pickBtn("notion_pages", `${p}.page`)}</div>`;
      case "notion_db": return `<div class="pln-line">${c.txt(`${p}.database`, t.database, T("ссылка на базу данных Notion", "Notion database link"), "wide")}${pickBtn("notion_dbs", `${p}.database`)}</div>
        <div class="pln-line"><span>${T("запускать, когда", "run when")}</span>${c.sel(`${p}.mode`, t.mode || "new", [["new", T("появилась новая запись", "a new entry appears")], ["changed", T("изменилась запись", "an entry changes")], ["any", T("любое из этого", "either")]])}</div>${each}`;
      case "rss": return `<div class="pln-line">${c.txt(`${p}.url`, t.url, "https://example.com/feed.xml", "wide")}</div>
        <div class="pln-line"><span>${T("только со словом", "only with the word")}</span>${c.txt(`${p}.match`, t.match, T("необязательно", "optional"), "")}</div>${each}`;
      case "web_page": return `<div class="pln-line">${c.txt(`${p}.url`, t.url, "https://example.com/product", "wide")}</div>
        <div class="pln-line"><span>${T("только блок", "only the block")}</span>${c.txt(`${p}.selector`, t.selector, "#price, .stock", "")}<em class="pln-hint">${T("CSS-селектор, необязательно", "CSS selector, optional")}</em></div>
        <div class="pln-line"><span>${T("запускать, когда", "run when")}</span>${c.sel(`${p}.mode`, t.mode || "changed", [["changed", T("что-то изменилось", "anything changed")], ["contains", T("появился текст", "a text appears")], ["not_contains", T("пропал текст", "a text disappears")]], "ptx-rerender")}
          ${t.mode && t.mode !== "changed" ? c.txt(`${p}.text`, t.text, T("В наличии", "In stock"), "") : ""}</div>
        ${t.mode && t.mode !== "changed" ? `<label class="pln-check"><input type="checkbox" data-path="${p}.exact" ${t.exact !== false ? "checked" : ""}><span>${T("точное совпадение — целые слова, «В наличии» не сработает на «Нет в наличии»", "exact match — whole words, “In stock” will not match “Not in stock”")}</span></label>` : ""}`;
      case "webhook": {
        const url = (st.cat?.hook_base || "http://127.0.0.1:PORT/hook/plan/") + (t.token || "");
        return `<div class="ptx-hook"><code>${H(url)}</code><button type="button" class="pbtn ghost compact" data-ptx-act="copy" data-value="${H(url)}">${I("copy")}${T("Копировать", "Copy")}</button></div>
          <p class="pln-explain">${I("info")}${T("POST-запрос с JSON (title, text и любые поля) запускает задачу; поля доступны как {{trigger.data.поле}}. Адрес — секрет: не публикуйте его.", "A POST with JSON (title, text and any fields) starts the task; fields are available as {{trigger.data.field}}. The address is a secret: do not publish it.")}</p>
          <button type="button" class="pln-link" data-ptx-act="new-token">${I("retry")}${T("Сменить адрес", "New address")}</button>`;
      }
      case "panel": {
        const panels = st.cat?.panels || [];
        const panel = panels.find((x) => x.id === t.panel);
        const field = panel?.fields.find((f) => f.key === t.key);
        if (!panels.length) return `<div class="pln-note">${I("info")}<span>${T("Нет панелей с данными. Добавьте панель (курс, погода, цена…) на главный экран — и выберите её значение здесь.", "No data panels. Add a panel (a rate, weather, a price…) to the home screen and pick its value here.")}</span></div>`;
        return `<div class="pln-line"><span>${T("панель", "panel")}</span>${c.sel(`${p}.panel`, t.panel || "", [["", T("— выберите —", "— choose —")], ...panels.map((x) => [x.id, x.title])], "wide ptx-rerender")}</div>
          ${panel ? `<div class="pln-line"><span>${T("значение", "value")}</span>${c.sel(`${p}.key`, t.key || "", [["", T("— выберите —", "— choose —")], ...panel.fields.map((f) => [f.key, `${f.key} = ${String(f.value).slice(0, 40)}`])], "wide ptx-rerender")}
            ${field ? `<span class="pln-live on">${T("сейчас", "now")}: ${H(String(field.value).slice(0, 40))}</span>` : ""}</div>${compareHtml(t, p, c)}
            <p class="pln-explain">${T("Значение берётся из панели, когда она обновляется (обновлением панелей занимается окно MemuAI).", "The value comes from the panel when it refreshes (panels are refreshed by the MemuAI window).")}</p>` : ""}`;
      }
      case "quota_low": {
        const acc = st.cat?.quota || [];
        return `<div class="pln-line"><span>${T("аккаунт", "account")}</span>${c.sel(`${p}.account`, t.account || "", [["", T("любой", "any")], ...acc.map((a) => [a.id, a.name])], "wide")}</div>
          <div class="pln-line"><span>${T("когда квота", "when the quota")}</span>${c.sel(`${p}.level`, t.level || "runs_out", [["tight", T("впритык", "is tight")], ["runs_out", T("кончится раньше сброса", "will run out before the reset")], ["exhausted", T("исчерпана", "is exhausted")]])}</div>
          ${acc.length ? "" : `<p class="pln-explain">${I("info")}${T("Прогноз появится, когда Proxy соберёт данные о лимитах (вкладка «Планировщик квот»).", "The forecast appears once the Proxy has limit data (the “Quota planner” tab).")}</p>`}`;
      }
      case "proxy_model": return `<div class="pln-line"><span>${T("только модели, где есть", "only models containing")}</span>${c.txt(`${p}.match`, t.match, "claude", "")}<em class="pln-hint">${T("необязательно", "optional")}</em></div>`;
      case "folder_file": return `<div class="pln-line">${c.txt(`${p}.path`, t.path, T("Папка, например C:\\Users\\Вы\\Downloads", "Folder, e.g. C:\\Users\\You\\Downloads"), "wide")}<button type="button" class="pbtn ghost compact" data-act="pick-folder" data-target="${p}.path">${I("folder")}${T("Выбрать", "Browse")}</button></div>
        <div class="pln-line"><span>${T("только файлы", "only files")}</span>${c.txt(`${p}.pattern`, t.pattern, "*.pdf;*.docx", "short")}<label class="pln-check"><input type="checkbox" data-path="${p}.recursive" ${t.recursive ? "checked" : ""}><span>${T("и во вложенных папках", "and in subfolders")}</span></label></div>
        <p class="pln-explain">${T("Файл попадает в задачу, когда он докачался (размер перестал меняться). Имя и путь — {{trigger.title}}, {{trigger.path}}.", "A file reaches the task once it is fully written (its size stopped changing). Name and path — {{trigger.title}}, {{trigger.path}}.")}</p>${each}`;
      case "system": return `<div class="pln-line"><span>${T("показатель", "metric")}</span>${c.sel(`${p}.metric`, t.metric || "disk_free_pct", [["disk_free_pct", T("свободно на диске, %", "free disk space, %")], ["disk_free_gb", T("свободно на диске, ГБ", "free disk space, GB")], ["ram_pct", T("занято памяти, %", "memory used, %")], ["cpu_pct", T("загрузка процессора, %", "CPU load, %")], ["battery_pct", T("заряд батареи, %", "battery, %")]], "wide ptx-rerender")}
          ${String(t.metric || "disk_free_pct").startsWith("disk") ? c.txt(`${p}.drive`, t.drive, st.cat?.os === "windows" ? "C:\\" : "/", "short") : ""}</div>${compareHtml(t, p, c, true)}`;
      case "weather": return weatherHtml(t, p, c);
      case "all": {
        const items = t.items || [];
        const src = SOURCES();
        const opts = Object.entries(src).filter(([id]) => !["all", "webhook"].includes(id)).map(([id, m]) => [id, m.title]);
        return `<div class="pln-line"><span>${T("все события ниже — в пределах", "all events below — within")}</span>${c.num(`${p}.within`, t.within || 60, 1, 10080)}<span>${T("мин", "min")}</span></div>
          ${items.map((it, j) => `<div class="ptx-sub">${j ? `<span class="pln-or">${T("и", "and")}</span>` : ""}<div class="pln-line">${I(ICON[metaOf(it.source).icon] || "tr-logic")}${c.sel(`${p}.items.${j}.source`, it.source, opts, "wide ptx-source")}
            ${items.length > 2 ? `<button type="button" class="pln-x-sm" data-ptx-act="del-sub" data-path="${p}.items" data-j="${j}" aria-label="${T("Убрать", "Remove")}">${I("close")}</button>` : ""}</div>${fieldsHtml(it, `${p}.items.${j}`, c)}</div>`).join("")}
          ${items.length < 4 ? `<button type="button" class="pln-add-sm" data-ptx-act="add-sub" data-path="${p}.items">${I("plus")}${T("ещё событие", "another event")}</button>` : ""}`;
      }
      default: return "";
    }
  }
  function weatherHtml(t, p, c) {
    const metric = t.metric || "rain";
    const starts = ["rain", "snow", "clear"].includes(metric);
    const units = { temp: "°C", feels: "°C", wind: T("км/ч", "km/h"), precip: T("мм", "mm"), precip_prob: "%", cloud: "%", humidity: "%" };
    if (!starts) {   // a numeric metric needs a threshold — fill a sensible one so the row is valid at once
      t.op = t.op || (metric === "temp" || metric === "feels" ? "lt" : "gt");
      if (t.value === undefined || t.value === "") t.value = { temp: 0, feels: 0, wind: 40, precip: 1, precip_prob: 70, cloud: 80, humidity: 85 }[metric];
    }
    const place = window.MemuPlanConds?.placeHtml ? `<div class="pln-line">${window.MemuPlanConds.placeHtml(p, t)}</div>` : "";
    const cool = [[0, T("без паузы", "no pause")], [60, T("1 ч", "1 h")], [180, T("3 ч", "3 h")], [360, T("6 ч", "6 h")], [720, T("12 ч", "12 h")], [1440, T("сутки", "a day")]];
    return `<div class="pln-line"><span>${T("когда", "when")}</span>${c.sel(`${p}.metric`, metric, [["rain", T("начнётся дождь", "rain starts")], ["snow", T("пойдёт снег", "snow starts")], ["clear", T("прояснится", "the sky clears")],
        ["temp", T("температура", "temperature")], ["feels", T("ощущается как", "feels like")], ["wind", T("ветер", "wind")], ["precip_prob", T("вероятность осадков", "chance of rain")],
        ["precip", T("осадки за час", "precipitation")], ["cloud", T("облачность", "cloud cover")], ["humidity", T("влажность", "humidity")]], "wide ptx-rerender")}
        ${starts ? "" : `${c.sel(`${p}.op`, t.op, [["lt", T("опустится ниже", "drops below")], ["gt", T("поднимется выше", "rises above")]])}${c.txt(`${p}.value`, t.value, "0", "short")}<span>${H(units[metric] || "")}</span>`}</div>
      ${place}
      <div class="pln-line sub"><span>${T("снова — не раньше чем через", "again — not sooner than")}</span>${c.sel(`${p}.cooldown`, String(t.cooldown ?? 180), cool.map(([v, l]) => [String(v), l]))}</div>
      <p class="pln-explain">${T("Срабатывает в момент, когда погода меняется (не каждый раз, пока идёт дождь). Данные Open-Meteo, проверка раз в 20 мин. В задаче: {{trigger.title}}, {{trigger.temp}}.", "Fires at the moment the weather changes (not every time while it rains). Open-Meteo data, checked every 20 min. In the task: {{trigger.title}}, {{trigger.temp}}.")}</p>`;
  }
  const EVERY = [1, 2, 5, 10, 15, 30, 60, 180, 360, 1440];
  function everyLabel(m) { return m < 60 ? T(`${m} мин`, `${m} min`) : m < 1440 ? T(`${m / 60} ч`, `${m / 60} h`) : T("раз в сутки", "once a day"); }
  function commonHtml(t, p, c) {
    const meta = (st.cat?.sources || []).find((x) => x.id === t.source) || { every: 5, min: 1 };
    const every = t.source === "webhook" ? "" : `<div class="pln-line sub"><span>${T("проверять", "check")}</span>${c.sel(`${p}.every`, t.every || "", [["", T(`как обычно (${everyLabel(meta.every)})`, `default (${everyLabel(meta.every)})`)], ...EVERY.filter((m) => m >= meta.min).map((m) => [String(m), T("каждые ", "every ") + everyLabel(m)])])}</div>`;
    const hours = t.hours;
    return `${every}
      <label class="pln-check"><input type="checkbox" data-ptx-act="hours" data-path-x="${p}" ${hours ? "checked" : ""}><span>${T("реагировать только в это время", "react only at these hours")}</span></label>
      ${hours ? `<div class="pln-line"><span>${T("с", "from")}</span>${c.timeIn(`${p}.hours.from`, hours.from || "09:00")}<span>${T("до", "to")}</span>${c.timeIn(`${p}.hours.to`, hours.to || "18:00")}</div>${c.daysHtml(`${p}.hours.days`, hours.days)}
        <label class="pln-check"><input type="checkbox" data-ptx-act="hours-wait" data-path-x="${p}" ${hours.wait ? "checked" : ""}><span>${T("события вне этого времени ждут его начала", "events outside these hours wait for them")}</span></label>
        <p class="pln-explain">${hours.wait ? T("Ничего не теряется: события копятся (до 4 дней) и запускают задачу, когда начнётся это время.", "Nothing is lost: events are kept (up to 4 days) and run the task when these hours begin.")
          : T("События вне этого времени пропускаются.", "Events outside these hours are skipped.")}</p>` : ""}
      <div class="pln-line sub"><span>${T("запустить через", "run after")}</span>${c.num(`${p}.delay`, t.delay || 0, 0, 1440)}<span>${T("мин после события", "min after the event")}</span></div>`;
  }
  function checkHtml(i) {
    const r = st.checks[i];
    if (!r) return "";
    if (r.busy) return `<div class="ptx-result"><span class="pln-spin"></span>${T("Проверяю…", "Checking…")}</div>`;
    if (!r.ok) {
      const conn = r.kind === "connect" || r.kind === "auth";
      return `<div class="ptx-result bad">${I("info")}<div><b>${H(r.error || T("Ошибка", "Error"))}</b>${conn ? `<button type="button" class="pbtn compact" data-ptx-act="connect">${I("tr-plug")}${T("Подключить", "Connect")}</button>` : ""}</div></div>`;
    }
    const fire = r.would_fire === true ? `<span class="pln-live on">${T("условие выполняется сейчас", "the condition holds now")}</span>` : r.would_fire === false ? `<span class="pln-live">${T("сейчас не выполняется", "does not hold now")}</span>` : "";
    return `<div class="ptx-result ok">${I("check")}<div><b>${H(r.summary || T("Источник отвечает", "The source answers"))}</b>${fire}
      ${(r.preview || []).length ? `<ul>${r.preview.map((x) => `<li><span>${H(x.title)}</span>${x.sub ? `<small>${H(x.sub)}</small>` : ""}</li>`).join("")}</ul>` : ""}
      ${r.note ? `<small class="pln-hint warn">${H(r.note)}</small>` : ""}
      <small class="pln-hint">${r.would_fire === true || r.would_fire === false
        ? T("Задача запускается, когда условие начинает выполняться, и снова — только после возврата значения (с учётом гистерезиса).", "The task runs when the condition starts to hold, and again only after the value comes back (respecting the hysteresis).")
        : T("Существующие элементы задачу не запускают — только новые, появившиеся после сохранения.", "Existing items do not start the task — only new ones that appear after saving.")}</small></div></div>`;
  }
  function healthLine(h) {
    if (!h) return "";
    const rel = (ts) => ago(ts);
    if (h.error) return `<span class="ptx-health bad" title="${H(h.error)}">${I("info")}${H(h.error.slice(0, 120))}${h.fails > 1 ? ` · ${T("ошибок подряд", "failures in a row")}: ${h.fails}` : ""}</span>`;
    const parts = [];
    if (h.last_check) parts.push(T("проверено ", "checked ") + rel(h.last_check));
    if (h.fired) parts.push(T(`сработал ${h.fired} раз`, `fired ${h.fired}×`));
    if (h.pending) parts.push(T(`ждут запуска: ${h.pending}`, `waiting to run: ${h.pending}`));
    if (!parts.length) parts.push(T("первая проверка — в ближайшие минуты", "the first check is due within minutes"));
    return `<span class="ptx-health">${I("check")}${H(parts.join(" · "))}</span>`;
  }
  function row(t, i, p, c) {
    st.ctx = c;
    if (!st.cat) { loadCatalog().then(() => c.rerender && c.rerender()); }
    const m = metaOf(t.source);
    const services = t.source === "all" ? [...new Set((t.items || []).map((x) => metaOf(x.source).service).filter(Boolean))] : (m.service ? [m.service] : []);
    const missing = st.cat ? services.filter((x) => !connected(x)) : [];
    const off = missing.length > 0;
    const health = (c.health || []).find((h) => h.index === i);
    const head = off ? `<div class="pln-note warn ptx-conn">${I("tr-plug")}<span>${H(missing.map((x) => SERVICE_TITLE[x] || x).join(", "))} ${missing.length > 1 ? T("не подключены — подключите, чтобы триггер работал.", "are not connected — connect them to make the trigger work.") : T("не подключён — подключите, чтобы триггер работал.", "is not connected — connect it to make the trigger work.")}</span><button type="button" class="pbtn compact" data-ptx-act="connect">${T("Подключить", "Connect")}</button></div>` : "";
    const inner = `${head}${fieldsHtml(t, p, c)}${commonHtml(t, p, c)}
      <div class="ptx-actions"><button type="button" class="pbtn ghost compact" data-ptx-act="check" data-idx="${i}">${I("play")}${T("Проверить сейчас", "Check now")}</button>${health ? healthLine(health) : ""}</div>
      <div class="ptx-check" data-ptx-check="${i}" aria-live="polite">${checkHtml(i)}</div>`;
    return c.rowWrap("when", i, ICON[m.icon] || "tr-logic", m.title, inner, `<span class="pln-chip ptx-cat">${H((CATS().find((x) => x.id === m.cat) || {}).title || "")}</span>`);
  }

  /* ------------------------------------------------------------------ integration picker popup */
  async function pickFrom(anchor, what, target, parentPath, c) {
    const draft = c.draft();
    const parent = parentPath ? c.get(parentPath) : "";
    const pop = document.createElement("div");
    pop.className = "pln-picker ptx-picker";
    pop.innerHTML = `<div class="pln-picker-box" role="dialog" aria-modal="true"><div class="pln-picker-head"><b>${T("Выберите", "Choose")}</b><button class="mini-action" data-x aria-label="${T("Закрыть", "Close")}">${I("close")}</button></div>
      <input class="pinput" data-q placeholder="${T("Поиск…", "Search…")}"><div class="pln-picker-list"><div class="pln-nl-wait"><span class="pln-spin"></span>${T("Загружаю…", "Loading…")}</div></div></div>`;
    document.body.appendChild(pop);   // Round 35 (finish-c): see openPicker
    const close = () => pop.remove();
    let timer = 0;
    const load = async () => {
      const q = $q("[data-q]", pop).value.trim();
      const list = $q(".pln-picker-list", pop);
      try {
        const r = await api(`/api/plan/triggers/pick?what=${encodeURIComponent(what)}&q=${encodeURIComponent(q)}&parent=${encodeURIComponent(parent || "")}&lang=${en() ? "en" : "ru"}`);
        list.innerHTML = (r.items || []).map((x) => `<button data-id="${H(x.id)}">${I("check")}<span><b>${H(x.title)}</b>${x.sub ? `<small>${H(x.sub)}</small>` : ""}</span></button>`).join("")
          || `<div class="pln-empty small">${T("Ничего не найдено", "Nothing found")}</div>`;
      } catch (e) {
        list.innerHTML = `<div class="ptx-result bad">${I("info")}<div><b>${H(e.message || String(e))}</b><button type="button" class="pbtn compact" data-ptx-act="connect">${T("Подключить", "Connect")}</button></div></div>`;
      }
    };
    pop.addEventListener("click", (e) => {
      if (e.target === pop || e.target.closest("[data-x]")) return close();
      if (e.target.closest('[data-ptx-act="connect"]')) { close(); openIntegrations(); return; }
      const b = e.target.closest("button[data-id]");
      if (!b) return;
      let value = b.dataset.id;
      if (what === "gmail_labels") { const cur = String(c.get(target) || "").trim(); value = (cur ? cur + " " : "") + value; }
      c.set(target, value);
      close(); c.rerender();
    });
    pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    $q("[data-q]", pop).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 250); });
    $q("[data-q]", pop).focus();
    void draft;
    await load();
  }

  /* ------------------------------------------------------------------ builder events */
  function bind(root, c) {
    st.ctx = c;
    st.checks = {};
    if (c.taskId) refreshHealth(c);
    // a service connected since the last visit: fresh connection badges for the watches of this task
    if ((c.draft().rule?.when || []).some((t) => t.type === "watch")) loadCatalog(true).then(() => { if (root.isConnected) c.rerender(); });
    root.addEventListener("change", (e) => {
      const el = e.target;
      if (el.dataset.ptxAct === "hours") {
        const t = c.get(el.dataset.pathX);
        if (el.checked) t.hours = { from: "09:00", to: "18:00", days: [1, 2, 3, 4, 5] }; else delete t.hours;
        c.rerender(); return;
      }
      if (el.dataset.ptxAct === "hours-wait") {
        const t = c.get(el.dataset.pathX);
        if (t.hours) { if (el.checked) t.hours.wait = true; else delete t.hours.wait; }
        c.rerender(); return;
      }
      if (el.classList.contains("ptx-source")) {
        const path = el.dataset.path.replace(/\.source$/, "");
        const keys = path.split("."), j = +keys.pop(), list = c.get(keys.join("."));
        const made = newTrigger(el.value); delete made.type;
        list[j] = { source: el.value, ...made };
        c.rerender(); return;
      }
      if (el.classList.contains("ptx-rerender")) {
        if (/\.panel$/.test(el.dataset.path || "")) {
          const t = c.get(el.dataset.path.replace(/\.panel$/, ""));
          const panel = (st.cat?.panels || []).find((x) => x.id === t.panel);
          const f = panel ? (panel.fields.find((x) => x.numeric) || panel.fields[0]) : null;
          t.key = f?.key || ""; t.op = f?.numeric ? "gt" : "changed";
        }
        setTimeout(() => c.rerender(), 0);
      }
    });
    root.addEventListener("click", async (e) => {
      const el = e.target.closest("[data-ptx-act]");
      if (!el || !root.contains(el) || el.tagName === "INPUT") return;
      const act = el.dataset.ptxAct;
      if (act === "connect") return openIntegrations();
      if (act === "copy") { try { await navigator.clipboard.writeText(el.dataset.value); if (typeof toast === "function") toast(T("Скопировано", "Copied")); } catch (err) { /* clipboard denied */ } return; }
      if (act === "pick") return pickFrom(el, el.dataset.what, el.dataset.target, el.dataset.parentPath || "", c);
      if (act === "q-add") { const cur = String(c.get(el.dataset.target) || "").trim(); c.set(el.dataset.target, (cur ? cur + " " : "") + el.dataset.value); c.rerender(); const inp = root.querySelector(`[data-path="${el.dataset.target}"]`); if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); } return; }
      if (act === "new-token") { const r = el.closest(".pln-row"); const t = c.draft().rule.when[+r.dataset.idx]; t.token = token(); c.rerender(); return; }
      if (act === "add-sub") { const list = c.get(el.dataset.path); list.push({ source: "rss", url: "" }); c.rerender(); return; }
      if (act === "del-sub") { const list = c.get(el.dataset.path); list.splice(+el.dataset.j, 1); c.rerender(); return; }
      if (act === "check") {
        const i = +el.dataset.idx;
        const trigger = c.draft().rule.when[i];
        st.checks[i] = { busy: true };
        paintCheck(root, i);
        try {
          st.checks[i] = await api("/api/plan/triggers/check", { method: "POST", body: { trigger, task_id: c.taskId || "", lang: en() ? "en" : "ru" } });
        } catch (err) { st.checks[i] = { ok: false, error: err.message || String(err) }; }
        paintCheck(root, i);
        if (c.taskId) setTimeout(() => refreshHealth(c), 8000);
      }
    });
  }
  function paintCheck(root, i) { const box = root.querySelector(`[data-ptx-check="${i}"]`); if (box) box.innerHTML = checkHtml(i); }
  async function refreshHealth(c) {
    try {
      const r = await api(`/api/plan/triggers/health?lang=${en() ? "en" : "ru"}`);
      c.health = (r.tasks || {})[c.taskId] || [];
      document.querySelectorAll("#pln-builder .ptx-actions").forEach((box) => {
        const rowEl = box.closest(".pln-row"); const h = c.health.find((x) => x.index === +rowEl.dataset.idx);
        box.querySelector(".ptx-health")?.remove();
        if (h) box.insertAdjacentHTML("beforeend", healthLine(h));
      });
    } catch (e) { /* older server */ }
  }

  /* ------------------------------------------------------------------ task card */
  function cardHealth(task) {
    const rows = task.watch_health || [];
    if (!rows.length || task.enabled === false) return "";
    const bad = rows.find((h) => h.error);
    if (bad) {
      const conn = bad.error_kind === "connect" || bad.error_kind === "auth";
      return `<div class="ptx-card-health bad">${I(conn ? "tr-plug" : "info")}<span title="${H(bad.error)}">${H(bad.error.slice(0, 140))}</span>${conn ? `<button type="button" class="pln-link" data-ptx-card="connect">${T("Подключить", "Connect")}</button>` : ""}</div>`;
    }
    const fired = rows.reduce((n, h) => n + (h.fired || 0), 0);
    const last = rows.filter((h) => h.source !== "webhook").map((h) => h.last_check || 0).reduce((a, b) => Math.max(a, b), 0);
    const lastFire = rows.map((h) => h.last_fire || 0).reduce((a, b) => Math.max(a, b), 0);
    const title = rows.find((h) => h.last_fire === lastFire)?.last_title || "";
    const parts = [];
    if (last) parts.push(T("проверено ", "checked ") + ago(last));
    if (fired) parts.push(T(`сработал ${fired} раз`, `fired ${fired}×`) + (title ? ` · ${title}` : ""));
    if (!parts.length) parts.push(rows.every((h) => h.source === "webhook") ? T("ждёт вызова вебхука", "waiting for a webhook call") : T("ждёт первой проверки", "waiting for the first check"));
    return `<div class="ptx-card-health">${I("tr-plug")}<span>${H(parts.join(" · ").slice(0, 200))}</span></div>`;
  }
  function ago(ts) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return T("только что", "just now");
    const m = Math.round(s / 60);
    if (m < 60) return T(`${m} мин назад`, `${m} min ago`);
    const h = Math.round(m / 60);
    if (h < 48) return T(`${h} ч назад`, `${h} h ago`);
    return T(`${Math.round(h / 24)} дн назад`, `${Math.round(h / 24)} d ago`);
  }
  document.addEventListener("click", (e) => { if (e.target.closest("[data-ptx-card=connect]")) { e.stopPropagation(); openIntegrations(); } }, true);

  window.MemuPlanTriggers = { menuItems, openPicker, row, bind, cardHealth, loadCatalog, newTrigger, sources: SOURCES, categories: CATS, icons: SVG };
})();
