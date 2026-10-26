

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, $$, esc, tr, icon, haptic, toast, api, openSheet, openStack, actionSheet, confirmDialog } = kit;
    const en = () => S.lang === "en";
    const pad = (n) => String(n).padStart(2, "0");

    function dayStart(d) { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; }
    function whenText(ts) {
      if (!ts) return "";
      const d = new Date(ts * 1000), now = new Date();
      const diff = Math.round((dayStart(d) - dayStart(now)) / 86400000);
      const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      if (diff === 0) return tr(`сегодня в ${time}`, `today at ${time}`);
      if (diff === 1) return tr(`завтра в ${time}`, `tomorrow at ${time}`);
      if (diff === -1) return tr(`вчера в ${time}`, `yesterday at ${time}`);
      const opts = { weekday: "short", day: "numeric", month: "long" };
      if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
      return `${d.toLocaleDateString(en() ? "en-GB" : "ru-RU", opts)} ${tr("в", "at")} ${time}`;
    }
    function plural(n, one, few, many) {
      const a = Math.abs(n) % 100, b = a % 10;
      if (a > 10 && a < 20) return many;
      if (b === 1) return one;
      return b >= 2 && b <= 4 ? few : many;
    }
    function relIn(ts) {
      const s = Math.round(ts - Date.now() / 1000);
      if (Math.abs(s) < 45) return s < -5 ? tr("только что", "just now") : tr("сейчас", "now");
      const a = Math.abs(s);

      const total = Math.max(1, Math.round(a / 60));
      let out;
      if (total < 60) out = tr(`${total} мин`, `${total} min`);
      else if (total < 1440) { const hh = Math.floor(total / 60), m = total % 60; out = tr(`${hh} ч${m ? ` ${m} мин` : ""}`, `${hh} h${m ? ` ${m} min` : ""}`); }
      else {
        const hours = Math.round(a / 3600), dd = Math.floor(hours / 24), hh = hours % 24;
        if (dd < 3 && hh) out = tr(`${dd} ${plural(dd, "день", "дня", "дней")} ${hh} ч`, `${dd} ${dd === 1 ? "day" : "days"} ${hh} h`);
        else { const d = Math.round(a / 86400); out = tr(`${d} ${plural(d, "день", "дня", "дней")}`, `${d} ${d === 1 ? "day" : "days"}`); } }
      return s < 0 ? tr(`${out} назад`, `${out} ago`) : tr(`через ${out}`, `in ${out}`);
    }

    const KINDS = {
      chat: { icon: "sparkle", color: "#8b5cf6", ru: "Ответ модели", en: "Model answer" },
      notify: { icon: "bell", color: "#f59e0b", ru: "Напоминание", en: "Reminder" },
      reminder: { icon: "bell", color: "#f59e0b", ru: "Напоминание", en: "Reminder" },
      dfy: { icon: "globe", color: "#0ea5e9", ru: "Do For You", en: "Do For You" },
      command: { icon: "zap", color: "#10b981", ru: "Команда быстрого доступа", en: "Quick Access command" },
      memucode: { icon: "terminal", color: "#64748b", ru: "MemuCode", en: "MemuCode" },
      flow: { icon: "list", color: "#d9962c", ru: "Шаги", en: "Steps" },
      work: { icon: "work", color: "#ec4899", ru: "Work-проект", en: "Work project" },
    };
    const kindOf = (t) => KINDS[t.mode === "work" ? "work" : t.kind] || KINDS.chat;

    function stepsHtml(steps, limit) {
      const rows = (steps || []).map((r) => (typeof r.text === "string" ? r : { n: "", depth: 0, cat: "app", text: (r.label || r.type || "") + (r.query || r.prompt || r.text ? ": " + String(r.query || r.prompt || r.text).slice(0, 80) : "") }));
      if (!rows.length) return "";
      const shown = limit ? rows.slice(0, limit) : rows;
      return `<ol class="tpl-steps">${shown.map((r) => `<li class="c-${esc(r.cat || "app")}" style="--d:${+r.depth || 0}"><b>${esc(r.n || "")}</b><span>${esc(r.text)}</span></li>`).join("")}
        ${rows.length > shown.length ? `<li class="more"><span>${esc(tr(`ещё шагов: ${rows.length - shown.length}`, `${rows.length - shown.length} more steps`))}</span></li>` : ""}</ol>`;
    }
    function legacyWhen(task) {
      if (task.daily_at) return tr("Каждый день в ", "Every day at ") + task.daily_at;
      if (task.interval_minutes) {
        const m = +task.interval_minutes;
        return m % 60 === 0 ? tr(`Каждые ${m / 60} ч`, `Every ${m / 60} h`) : tr(`Каждые ${m} мин`, `Every ${m} min`);
      }
      return "";
    }
    function statusOf(t) {
      const st = t.state || {};
      if (t.active) return { cls: "run", text: t.active === "queued" ? tr("Запускается…", "Starting…") : tr("Выполняется…", "Running…") };
      if (st.status === "invalid") return { cls: "bad", text: st.reason || tr("Ошибка в правиле", "Invalid rule") };
      if (!t.enabled) return st.status === "finished" ? { cls: "done", text: st.reason || tr("Завершено", "Finished") }
        : { cls: "off", text: tr("На паузе", "Paused") };
      if (st.status === "blocked") return { cls: "wait", text: st.reason || tr("Ждёт условия", "Waiting for a condition") };
      if (typeof t.next_run === "number") return { cls: "on", text: `${tr("Следующий запуск", "Next run")}: ${whenText(t.next_run)} · ${relIn(t.next_run)}` };
      if (t.event_only) return { cls: "on", text: tr("Ждёт события", "Waiting for the event") };
      return { cls: "on", text: tr("Планируется…", "Planning…") };
    }

    const EXAMPLES = () => en() ? [
      "Every weekday at 9:00 send me a short AI news digest",
      "Tomorrow at 15:00 remind me to call the bank",
      "Every 2 hours from 10 to 18 remind me to stretch",
      "On the last day of every month at 20:00 remind me to pay the bills",
    ] : [
      "Каждый будний день в 9:00 присылай короткую сводку новостей об ИИ",
      "Завтра в 15:00 напомни позвонить в банк",
      "Каждые 2 часа с 10 до 18 напоминай размяться",
      "В последний день месяца в 20:00 напомни оплатить счета",
    ];

    kit.pages.schedules = () => openStack({ title: tr("Планирование", "Planning"), build: (view) => {
      view.style.overflowY = "auto";
      let tasks = null, filter = "all", busy = false;
      view.innerHTML = `<div class="tpl-page">
        <div class="tpl-compose">
          <div class="tpl-compose-head">${icon("sparkle")}<span>${esc(tr("Создать словами", "Create from a sentence"))}</span></div>
          <textarea id="tpl-text" rows="2" maxlength="2000" enterkeyhint="send" placeholder="${esc(tr("Например: каждый день в 9:00 присылай прогноз погоды", "For example: every day at 9:00 send me the weather forecast"))}"></textarea>
          <div class="tpl-examples" id="tpl-examples">${EXAMPLES().map((x, i) => `<button class="chip" data-ex="${i}">${esc(x)}</button>`).join("")}</div>
          <button class="btn block" id="tpl-parse">${icon("sparkle")}<span>${esc(tr("Составить задачу", "Draft the task"))}</span></button>
          <p class="tpl-hint">${esc(tr("Модель составит правило, а вы проверите его перед созданием.", "The model drafts the rule; you check it before anything is created."))}</p>
        </div>
        <div id="tpl-list"><div class="empty"><div class="typing"><i></i><i></i><i></i></div></div></div>
      </div>`;
      const field = $("#tpl-text", view);
      const grow = () => { field.style.height = "auto"; field.style.height = Math.min(field.scrollHeight, 160) + "px"; };
      field.oninput = grow;
      field.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); parse(); } };
      $$("[data-ex]", view).forEach((b) => b.onclick = () => { field.value = EXAMPLES()[+b.dataset.ex]; grow(); field.focus(); haptic("select"); });
      $("#tpl-parse", view).onclick = () => parse();

      async function parse() {
        const text = field.value.trim();
        if (!text) { haptic("error"); field.focus(); toast(tr("Опишите, что и когда сделать", "Describe what to do and when"), "err"); return; }
        if (busy) return;
        busy = true;
        const button = $("#tpl-parse", view);
        button.disabled = true;
        button.innerHTML = `<span class="spinner"></span><span>${esc(tr("Составляю…", "Drafting…"))}</span>`;
        try {
          const draft = await api("schedules.parse", { text });
          openPreview(draft, () => { field.value = ""; grow(); });
        } catch (error) { toast(error.message, "err"); }
        finally {
          busy = false; button.disabled = false;
          button.innerHTML = `${icon("sparkle")}<span>${esc(tr("Составить задачу", "Draft the task"))}</span>`;
        }
      }

      function flatSteps(steps, prefix = "", depth = 0) {
        const out = [];
        const titles = { web_search: tr("Веб-поиск", "Web search"), model: tr("Модель", "Model"), decide: tr("Модель решений", "Decision model"), if: tr("Если", "If"),
          foreach: tr("Для каждого", "For each"), only_new: tr("Только новые", "Only new"), memubase_insert: "MemuBase", memubase_query: "MemuBase", telegram: "Telegram",
          notify: tr("Уведомление", "Notification"), ask_user: tr("Спросить меня", "Ask me"), wait: tr("Подождать", "Wait"), set: tr("Переменная", "Variable") };
        (steps || []).forEach((s, i) => {
          const n = `${prefix}${i + 1}`;
          const detail = s.query || s.prompt || s.question || (s.cond && (s.cond.question || s.cond.left)) || s.items || s.text || s.table || "";
          out.push({ n, depth, cat: "app", text: (s.label || titles[s.type] || s.type) + (detail ? ": " + String(detail).replace(/\s+/g, " ").slice(0, 90) : "") });
          const inner = [...(s.then || []), ...(s.else || []), ...(s.body || [])];
          if (inner.length) out.push(...flatSteps(inner, n + ".", depth + 1));
        });
        return out;
      }
      function openPreview(draft, onCreated) {
        const task = Object.assign({}, draft.task || {});
        const k = kindOf(task);
        const next = (draft.next || []).slice(0, 5);
        const body = `<div class="tpl-preview">
          <div class="tpl-summary"><div class="tpl-summary-icon" style="background:${k.color}">${icon(k.icon)}</div>
            <div><div class="tpl-kind">${esc(en() ? k.en : k.ru)}</div><div class="tpl-summary-text">${esc(draft.summary || "")}</div></div></div>
          <div class="field"><label for="tpl-name">${esc(tr("Название", "Name"))}</label><input id="tpl-name" maxlength="120" value="${esc(task.name || "")}"></div>
          ${task.kind === "flow" ? `<div class="section-title">${esc(tr("Шаги", "Steps"))}</div>${stepsHtml(flatSteps(task.steps || []))}`
            : task.kind === "command" ? `<div class="field"><label>${esc(tr("Команда", "Command"))}</label><input id="tpl-cmd" maxlength="120" value="${esc(task.command || "")}"></div>`
            : `<div class="field"><label for="tpl-prompt">${esc(task.kind === "notify" ? tr("Текст напоминания", "Reminder text") : task.kind === "dfy" ? tr("Что сделать в браузере", "What to do in the browser") : tr("Задание", "Task"))}</label>
              <textarea id="tpl-prompt" maxlength="20000" rows="3">${esc(task.prompt || "")}</textarea></div>`}
          <div class="section-title">${esc(next.length ? tr("Ближайшие запуски", "Next runs") : tr("Когда сработает", "When it fires"))}</div>
          <div class="group flat tpl-next">${next.length ? next.map((ts, i) => `<div class="cell flat"><span class="tpl-dot ${i ? "" : "first"}"></span><div class="cell-body"><div class="cell-title">${esc(whenText(ts))}</div></div><span class="cell-value">${esc(relIn(ts))}</span></div>`).join("")
            : (draft.events || []).map((e) => `<div class="cell flat">${icon("zap")}<div class="cell-body"><div class="cell-title">${esc(e)}</div></div></div>`).join("")
              || `<div class="cell flat"><div class="cell-body"><div class="cell-sub">${esc(tr("Нет будущих запусков", "No future runs"))}</div></div></div>`}</div>
          ${(draft.warnings || []).length ? `<div class="tpl-warn">${(draft.warnings || []).map((w) => `<div>${icon("warn")}<span>${esc(w)}</span></div>`).join("")}</div>` : ""}
          <div class="group flat" style="margin-top:14px"><label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Результат в Telegram", "Result in Telegram"))}</div></div>
            <span class="switch"><input type="checkbox" id="tpl-tg" ${task.notify_telegram !== false ? "checked" : ""}><span></span></span></label></div>
          <p class="section-note">${esc(tr("Условия, лимиты и другие тонкие настройки — в «Планировании» MemuAI на компьютере.", "Conditions, limits and other fine tuning are in MemuAI → Planning on the computer."))}</p>
        </div>`;
        openSheet({ title: tr("Проверьте задачу", "Check the task"), body, full: false,
          actions: [{ label: tr("Отмена", "Cancel"), kind: "secondary" },
                    { label: tr("Создать", "Create"), run: async (handle) => {
                      const el = handle.el;
                      task.name = $("#tpl-name", el).value.trim() || task.name;
                      if ($("#tpl-prompt", el)) task.prompt = $("#tpl-prompt", el).value.trim();
                      if ($("#tpl-cmd", el)) task.command = $("#tpl-cmd", el).value.trim();
                      task.notify_telegram = $("#tpl-tg", el).checked;
                      try {
                        const data = await api("schedules.create", { task });
                        tasks = data.tasks || tasks; haptic("success");
                        toast(tr("Задача создана", "Task created"), "ok");
                        onCreated && onCreated(); draw();
                      } catch (error) { toast(error.message, "err"); return true; }
                    } }] });
      }

      function card(t) {
        const k = kindOf(t), s = statusOf(t);
        const summary = t.summary || legacyWhen(t);
        const last = t.last && t.last.text ? `<div class="tpl-last ${t.last.ok === false ? "err" : "ok"}">${icon(t.last.ok === false ? "warn" : "check")}<span>${esc((t.last.at ? relIn(+t.last.at) + " · " : "") + t.last.text.replace(/\s+/g, " ").slice(0, 160))}</span></div>` : "";
        return `<div class="tpl-card ${t.enabled ? "" : "off"}" data-id="${esc(t.id)}">
          <button class="tpl-main" data-open="${esc(t.id)}">
            <div class="tpl-icon" style="background:${t.enabled ? k.color : "var(--card2)"};color:${t.enabled ? "#fff" : "var(--hint)"}">${icon(k.icon)}</div>
            <div class="tpl-body"><div class="tpl-title">${esc(t.name || (t.prompt || "").slice(0, 60) || t.id)}</div>
              ${summary ? `<div class="tpl-rule">${esc(summary)}</div>` : ""}
              ${t.steps ? stepsHtml(t.steps, 5) : ""}
              <div class="tpl-status ${s.cls}"><i></i><span>${esc(s.text)}</span></div>${last}</div>
          </button>
          <div class="tpl-side">
            <span class="switch"><input type="checkbox" data-toggle="${esc(t.id)}" ${t.enabled ? "checked" : ""} aria-label="${esc(t.enabled ? tr("Поставить на паузу", "Pause") : tr("Возобновить", "Resume"))}"><span></span></span>
            <button class="icon-btn" data-run="${esc(t.id)}" aria-label="${esc(tr("Запустить сейчас", "Run now"))}" title="${esc(tr("Запустить сейчас", "Run now"))}">${icon("play")}</button>
          </div></div>`;
      }

      function draw() {
        const list = $("#tpl-list", view);
        if (!list || !tasks) return;
        const on = tasks.filter((t) => t.enabled).length, off = tasks.length - on;
        if (filter === "on" && !on || filter === "off" && !off) filter = "all";
        const shown = tasks.filter((t) => filter === "all" || (filter === "on" ? t.enabled : !t.enabled));
        list.innerHTML = tasks.length ? `
          <div class="segmented tpl-tabs">${[["all", tr("Все", "All"), tasks.length], ["on", tr("Активные", "Active"), on], ["off", tr("На паузе", "Paused"), off]]
            .map(([id, label, n]) => `<button class="${filter === id ? "active" : ""}" data-filter="${id}">${esc(label)}<b>${n}</b></button>`).join("")}</div>
          <div class="tpl-cards">${shown.map(card).join("")}</div>
          <p class="section-note tpl-foot">${esc(tr("Задачи выполняет MemuAI Proxy на компьютере — он должен быть включён. Нажмите на задачу, чтобы запустить, приостановить или удалить её.", "MemuAI Proxy runs the tasks on the computer — it has to be on. Tap a task to run, pause or delete it."))}</p>`
          : `<div class="empty tpl-empty"><div class="empty-art">${icon("clock")}</div><h3>${esc(tr("Пока нет задач", "No tasks yet"))}</h3>
            <p>${esc(tr("Опишите задачу словами выше — например, «каждое утро в 8:30 присылай план дня». Или создайте её в MemuAI на компьютере.", "Describe a task in words above — for example “every morning at 8:30 send me a plan for the day”. Or create it in MemuAI on the computer."))}</p></div>`;
        $$("[data-filter]", list).forEach((b) => b.onclick = () => { filter = b.dataset.filter; haptic("select"); draw(); });
        $$("[data-toggle]", list).forEach((input) => input.onchange = () => toggle(input.dataset.toggle, input.checked, input));
        $$("[data-run]", list).forEach((b) => b.onclick = () => run(b.dataset.run));
        $$("[data-open]", list).forEach((b) => b.onclick = () => menu(tasks.find((t) => t.id === b.dataset.open)));
      }
      async function toggle(id, enabled, input) {
        haptic("select");
        try { tasks = (await api("schedules.toggle", { id, enabled })).tasks || tasks; draw();
          toast(enabled ? tr("Задача возобновлена", "Task resumed") : tr("Задача на паузе", "Task paused"), "ok"); }
        catch (error) { if (input) input.checked = !enabled; toast(error.message, "err"); }
      }
      async function run(id) {
        try { await api("schedules.run", { id }); haptic("success");
          toast(tr("Запущено — результат придёт, когда задача выполнится", "Started — the result arrives when the task is done"), "ok");
          setTimeout(load, 2500); }
        catch (error) { toast(error.message, "err"); }
      }
      function menu(t) {
        if (!t) return;
        const s = statusOf(t);
        actionSheet(t.name || tr("Задача", "Task"), [
          { icon: "play", label: tr("Запустить сейчас", "Run now"), sub: tr("Тестовый запуск, расписание не меняется", "A test run; the schedule stays as it is"), run: () => run(t.id) },
          ...(t.rule ? [{ icon: "list", label: tr("Условия и проверка", "Conditions and check"), sub: tr("Включить, выключить, «НЕ», проверить сейчас", "Switch on/off, «NOT», check now"), run: () => openConds(t) }] : []),
          t.enabled ? { icon: "clock", label: tr("Поставить на паузу", "Pause"), sub: s.text, run: () => toggle(t.id, false) }
            : { icon: "play", label: tr("Возобновить", "Resume"), sub: s.text, run: () => toggle(t.id, true) },
          { icon: "trash", danger: true, label: tr("Удалить", "Delete"), run: async () => {
            if (!await confirmDialog(tr(`Удалить задачу «${t.name}»? История её запусков останется.`, `Delete the task “${t.name}”? Its run history stays.`), { ok: tr("Удалить", "Delete"), danger: true })) return;
            try { tasks = (await api("schedules.delete", { id: t.id })).tasks || []; draw(); toast(tr("Задача удалена", "Task deleted"), "ok"); }
            catch (error) { toast(error.message, "err"); }
          } },
        ]);
      }

      function openConds(t) {
        let data = null, check = null, busyC = false;
        const mark = (ok) => `<i class="tpc-dot ${ok === true ? "ok" : ok === false ? "bad" : "unk"}">${ok === true ? "✓" : ok === false ? "✕" : "?"}</i>`;
        const matchSeg = (path, value) => `<span class="tpc-match"><button class="${value !== "any" ? "on" : ""}" data-c-match="all" data-c-path="${esc(path)}">${esc(tr("все", "all"))}</button><button class="${value === "any" ? "on" : ""}" data-c-match="any" data-c-path="${esc(path)}">${esc(tr("любое", "any"))}</button></span>`;
        const body = () => {
          if (!data) return `<div class="empty"><div class="typing"><i></i><i></i><i></i></div></div>`;
          const rows = data.rows || [];
          const top = rows.filter((r) => !r.depth).length;
          const checkHtml = check ? `<div class="tpc-check ${check.ok ? "ok" : "bad"}"><div class="tpc-verdict">${mark(!!check.ok)}<b>${esc(check.ok ? tr("Сейчас задача запустилась бы", "The task would run now") : (check.reason || tr("Сейчас задача не запустилась бы", "The task would not run now")))}</b></div>
            ${(check.rows || []).map((r) => `<div class="tpc-crow" style="--d:${+r.depth || 0}">${mark(r.ok)}<span><b>${esc((r.not ? tr("НЕ ", "NOT ") : "") + r.text)}</b>${r.detail ? `<small>${esc(r.detail)}</small>` : ""}</span></div>`).join("")}
            ${(check.notes || []).map((n) => `<div class="tpc-crow">${mark(false)}<span><b>${esc(n)}</b></span></div>`).join("")}</div>` : "";
          return `<div class="tpc">
            <p class="tpc-summary">${esc(data.summary || "")}</p>
            ${rows.length ? `${top > 1 ? `<div class="tpc-head"><span>${esc(tr("Должны выполняться", "Must hold"))}</span>${matchSeg("", data.match)}</div>` : ""}
            <div class="group flat tpc-list">${rows.map((r) => `<div class="cell flat tpc-row ${r.off ? "off" : ""}" style="--d:${+r.depth || 0}">
              <div class="cell-body"><div class="cell-title">${esc(r.text)}</div>${r.type === "group" ? `<div class="tpc-sub">${esc(tr("Внутри группы", "Inside the group"))} ${matchSeg(r.path, r.match)}</div>` : ""}</div>
              <button class="tpc-not ${r.not ? "on" : ""}" data-c-not="${esc(r.path)}" aria-pressed="${r.not}" title="${esc(tr("Наоборот: условие должно НЕ выполняться", "Inverted: the condition must NOT hold"))}">${esc(tr("НЕ", "NOT"))}</button>
              <span class="switch"><input type="checkbox" data-c-off="${esc(r.path)}" ${r.off ? "" : "checked"} aria-label="${esc(tr("Условие включено", "Condition on"))}"><span></span></span></div>`).join("")}</div>`
              : `<div class="empty"><p>${esc(tr("У задачи нет условий «Только если» — она запускается по расписанию или событию.", "The task has no “Only if” conditions — it runs on its schedule or event."))}</p></div>`}
            <button class="btn secondary block tpc-go" data-c-check="1">${busyC ? `<span class="spinner"></span>` : icon("check")}<span>${esc(tr("Проверить условия сейчас", "Check the conditions now"))}</span></button>
            ${checkHtml}
            <p class="section-note">${esc(tr("Выключенное условие остаётся в правиле, но не проверяется. Добавлять и менять условия — в «Планировании» MemuAI на компьютере.", "A switched-off condition stays in the rule but is not checked. Add and edit conditions in MemuAI → Planning on the computer."))}</p></div>`;
        };
        const handle = openSheet({ title: t.name || tr("Условия", "Conditions"), body: body(), full: true });
        const host = () => $(".sheet-body", handle.el);
        const paint = () => { const el = host(); if (!el) return; el.innerHTML = body(); bind(el); };
        async function change(payload) {
          try { data = await api("schedules.cond_set", { id: t.id, ...payload }); check = null; haptic("select"); load(); }
          catch (error) { toast(error.message, "err"); }
          paint();
        }
        function bind(el) {
          $$("[data-c-off]", el).forEach((input) => input.onchange = () => change({ path: input.dataset.cOff, off: !input.checked }));
          $$("[data-c-not]", el).forEach((b) => b.onclick = () => change({ path: b.dataset.cNot, not: !b.classList.contains("on") }));
          $$("[data-c-match]", el).forEach((b) => b.onclick = () => { if (!b.classList.contains("on")) change({ path: b.dataset.cPath, match: b.dataset.cMatch }); });
          const go = $("[data-c-check]", el);
          if (go) go.onclick = async () => {
            if (busyC) return;
            busyC = true; paint();
            try { check = await api("schedules.cond_check", { id: t.id }); haptic(check.ok ? "success" : "light"); }
            catch (error) { toast(error.message, "err"); }
            busyC = false; paint();
          };
        }
        api("schedules.conds", { id: t.id }).then((d) => { data = d; paint(); }).catch((error) => { toast(error.message, "err"); handle.close(true); });
      }
      function load() {
        return api("schedules.list").then((data) => { tasks = data.tasks || []; draw(); })
          .catch((error) => { const list = $("#tpl-list", view); if (list && !tasks) list.innerHTML = `<div class="empty"><p>${esc(error.message)}</p></div>`; });
      }
      load();
      const timer = setInterval(() => { if (!view.isConnected) { clearInterval(timer); return; } if (!document.hidden) load(); }, 30000);
    } });
  });
})();
