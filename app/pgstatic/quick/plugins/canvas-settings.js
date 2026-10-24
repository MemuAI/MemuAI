/* Round 27 (r27-quick-canvas) — Settings → Quick Ask → «Панели и быстрые действия» (plugin "canvas").
   Data: GET /api/quick/canvas; saved through POST /api/quick/canvas/settings (memuai/[profile]/quick_canvas.json —
   not preferences.json: the main window saves whole snapshots of those). Buttons are created in the Quick Ask window
   and (Round 28) arranged ANYWHERE on the screen around it; here — the switches, the list of buttons (delete, forget
   an approval) and import/export. */
(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="2"/><circle cx="17" cy="17" r="3.6"/></svg>';

  MemuQuick.settingsSection("canvas", (body, ctx) => {
    const en = !!ctx.en;
    const tr = (ru, e) => (en ? e : ru);
    const esc = ctx.esc || ((v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = window.MemuSettingsKit || null;
    const state = { data: null, icons: window.MemuCanvasIcons || null };
    const post = (path, payload) => ctx.api(path, { method: "POST", body: { lang: en ? "en" : "ru", ...payload } });
    const row = (o) => K ? K.row(o) : `<div class="qcs-row"><div><b>${o.label}</b>${o.desc ? `<div class="td">${o.desc}</div>` : ""}</div><div>${o.control || ""}</div></div>`;
    const section = (o) => K ? K.section(o) : `<section><h3>${o.title}</h3>${o.desc ? `<p>${o.desc}</p>` : ""}${o.body}</section>`;
    const toggle = (id, on) => K ? K.toggle(id, on) : `<input type="checkbox" id="${id}" ${on ? "checked" : ""}>`;
    const select = (id, options, value) => K ? K.select(id, options, value, { width: 190 }) : `<select id="${id}">${options.map(([v, l]) => `<option value="${v}" ${String(v) === String(value) ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
    const note = (text, tone) => K ? K.note(text, tone) : `<p>${text}</p>`;
    const steps = (n) => (n % 10 === 1 && n % 100 !== 11 ? "шаг" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? "шага" : "шагов");

    function face(a) {
      const ic = a.icon || {};
      const inner = ic.kind === "emoji" ? `<span>${esc(ic.value)}</span>` : (ic.kind === "image" || ic.kind === "app") ? `<img alt="" src="${esc(ic.value)}">`
        : (state.icons ? state.icons.svg(ic.value) : "");
      const bg = a.color ? `background:linear-gradient(140deg, ${a.color}, ${a.color2 || a.color})` : "";
      return `<span class="qcs-face" data-shape="${esc(a.shape)}" style="${bg}">${inner}</span>`;
    }
    function html() {
      const d = state.data, s = d.settings;
      const main = section({ id: "qcs-main", title: tr("Панели и быстрые действия", "Panels and quick actions"),
        keywords: "canvas panels widgets buttons quick actions macros панели виджеты кнопки быстрые действия макросы сценарии",
        desc: tr("Панели MemuAI и ваши кнопки стоят в любом месте экрана вокруг окна быстрого доступа: появляются вместе с ним на том мониторе, где оно открылось, и прячутся вместе с ним. Панели те же, что в MemuAI, и обновляются вместе с ними; расположение на экране своё — для каждого монитора.",
          "The MemuAI panels and your buttons sit anywhere on the screen around the Quick Ask window: they appear with it on the monitor it opened on and hide with it. The panels are the same as in MemuAI and update with them; the screen arrangement is its own — per monitor."),
        body: row({ label: tr("Включено", "Enabled"), control: toggle("qcs-enabled", s.enabled) })
          + row({ label: tr("Панели MemuAI", "MemuAI panels"), control: toggle("qcs-panels", s.panels),
            desc: tr("Погода, задачи, курсы, заметки, таймеры — любые панели MemuAI.", "Weather, to-dos, rates, notes, timers — any MemuAI panel.") })
          + row({ label: tr("Быстрые действия", "Quick actions"), control: toggle("qcs-actions", s.actions),
            desc: tr("Свои круглые кнопки: программы, команды, запросы ИИ, сценарии из нескольких шагов.", "Your own round buttons: apps, commands, AI requests, multi-step routines.") })
          + row({ label: tr("На экране при открытии", "On the screen when Quick Ask opens"), control: select("qcs-screen", [["show", tr("Показывать", "Show")], ["hidden", tr("Скрывать (Ctrl+B)", "Hide (Ctrl+B)")], ["last", tr("Как в прошлый раз", "As last time")]], s.screen || "show"),
            desc: tr("Панели и кнопки появляются вокруг окна, не под ним. Поле вопроса в фокусе в любом случае; Ctrl+B показывает или прячет их.",
              "The panels and buttons appear around the window, never under it. The question field keeps the focus either way; Ctrl+B shows or hides them.") })
          + row({ label: tr("Строка закреплённых под полем", "Pinned strip under the input"), control: toggle("qcs-strip", s.strip),
            desc: tr("Закреплённые кнопки и мини-панели видны, даже когда панели свёрнуты.", "Pinned buttons and mini panels stay visible while the canvas is folded.") })
          + row({ label: tr("Сворачивать при ответе", "Fold when an answer comes"), control: toggle("qcs-collapse", s.collapse_on_answer) })
          + row({ label: tr("Подписи под кнопками", "Captions under the buttons"), control: toggle("qcs-labels", s.labels) })
          + row({ label: tr("Размер сетки", "Grid size"), control: select("qcs-density", [["compact", tr("Компактная", "Compact")], ["normal", tr("Обычная", "Normal")], ["large", tr("Крупная", "Large")]], s.density) }) });
      const list = d.actions.length ? d.actions.map((a) => `<div class="qcs-item">${face(a)}<span class="qcs-name"><b>${esc(a.label || tr("Без подписи", "No label"))}</b>
          <small>${esc([a.steps.length > 1 ? tr(`Сценарий · ${a.steps.length} ${steps(a.steps.length)}`, `Routine · ${a.steps.length} steps`) : (a.tooltip || ""), a.hotkey ? a.hotkey + (a.global ? tr(" — во всех программах", " — in every program") : "") : ""].filter(Boolean).join(" · "))}</small></span>
          ${a.risks && a.risks.length ? `<span class="qcs-chip" data-tone="${a.approved ? "ok" : "warn"}">${esc(a.approved ? tr("Разрешено", "Approved") : tr("Спросит при запуске", "Asks on run"))}</span>` : ""}
          ${a.approved && a.risks && a.risks.length ? `<button type="button" class="pbtn ghost sx-btn-sm" data-qcs-forget="${esc(a.id)}">${esc(tr("Забыть разрешение", "Forget approval"))}</button>` : ""}
          <button type="button" class="pbtn ghost sx-btn-sm" data-qcs-del="${esc(a.id)}">${esc(tr("Удалить", "Delete"))}</button></div>`).join("")
        : `<div class="qcs-empty">${esc(tr("Кнопок пока нет. Откройте быстрый доступ (Alt+Space) → Ctrl+K → «Новая быстрая кнопка».", "No buttons yet. Open Quick Ask (Alt+Space) → Ctrl+K → “New quick button”."))}</div>`;
      const buttons = section({ id: "qcs-buttons", title: tr("Кнопки", "Buttons"), keywords: "buttons import export json кнопки импорт экспорт",
        desc: tr("Создаются и меняются в окне быстрого доступа, а расставляются прямо на экране: Ctrl+E или долгое нажатие на кнопку. Опасные шаги (выключение, PowerShell) подтверждаются при каждом запуске.",
          "Create and edit them in the Quick Ask window and arrange them right on the screen: Ctrl+E or a long press on a button. Dangerous steps (shutdown, PowerShell) are confirmed on every run."),
        body: `<div class="qcs-list">${list}</div><div class="qcs-actions">
          <button type="button" class="pbtn ghost sx-btn-sm" id="qcs-export">${esc(tr("Экспорт в файл", "Export to a file"))}</button>
          <button type="button" class="pbtn ghost sx-btn-sm" id="qcs-import">${esc(tr("Импорт из файла…", "Import from a file…"))}</button>
          <label class="qcs-replace"><input type="checkbox" id="qcs-replace">${esc(tr("заменить текущие", "replace the current ones"))}</label>
          <input type="file" id="qcs-file" accept=".json,application/json" hidden></div>
          ${note(esc(tr("Импортированные кнопки при первом запуске снова спросят разрешение на рискованные шаги.", "Imported buttons ask for approval of risky steps again on their first run.")), "info")}` });
      const keys = section({ id: "qcs-keys", title: tr("Клавиатура", "Keyboard"), keywords: "keyboard shortcuts клавиши сочетания",
        body: `<div class="qcs-keys">${[["Ctrl+B", tr("показать или спрятать панели и кнопки на экране", "show or hide the panels and buttons on the screen")],
          ["Ctrl+E", tr("расставить их на экране (перетаскивание, угол — размер)", "arrange them on the screen (drag, the corner resizes)")],
          [tr("Долгое нажатие", "Long press"), tr("то же — сразу с перетаскиванием", "the same — dragging at once")],
          [tr("Alt+стрелки", "Alt+arrows"), tr("при расстановке — сдвинуть на 8 px (с Shift — на 32)", "while arranging — move by 8 px (with Shift — 32)")],
          ["Tab · Enter", tr("на экране: выбрать и запустить кнопку", "on the screen: pick and run a button")], ["Shift+F10", tr("меню элемента", "item menu")],
          ["Esc", tr("закончить расстановку / закрыть быстрый доступ", "finish arranging / close Quick Ask")]]
          .map(([k, v]) => `<div><kbd>${esc(k)}</kbd><span>${esc(v)}</span></div>`).join("")}</div>` });
      return `<div class="qcs-settings">${main}${buttons}${keys}</div>`;
    }
    async function load() {
      if (!state.icons) {
        await new Promise((resolve) => {
          const s = document.createElement("script");
          s.src = "/quick/plugins/canvas-icons.js?v=1.0-r27"; s.onload = resolve; s.onerror = resolve;
          document.head.appendChild(s);
        });
        state.icons = window.MemuCanvasIcons || null;
      }
      try { state.data = await ctx.api("/api/quick/canvas?lang=" + (en ? "en" : "ru")); }
      catch (e) { body.innerHTML = note(esc(tr("Не удалось загрузить настройки: ", "Could not load the settings: ") + e.message), "warn"); return; }
      draw();
    }
    function draw() {
      const scroller = body.closest(".settings-body");
      const top = scroller ? scroller.scrollTop : 0;
      body.innerHTML = html();
      if (scroller) scroller.scrollTop = top;
      bind();
    }
    async function save(patch) {
      try { const data = await post("/api/quick/canvas/settings", { settings: patch }); state.data.settings = data.settings; draw(); }
      catch (e) { ctx.toast(e.message, "err"); }
    }
    function download(name, text) {
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
    }
    function bind() {
      const $ = (id) => body.querySelector("#" + id);
      const toggles = { "qcs-enabled": "enabled", "qcs-panels": "panels", "qcs-actions": "actions", "qcs-strip": "strip", "qcs-collapse": "collapse_on_answer", "qcs-labels": "labels" };
      for (const [id, key] of Object.entries(toggles)) { const el = $(id); if (el) el.onchange = () => save({ [key]: el.checked }); }
      const screen = $("qcs-screen"); if (screen) screen.onchange = () => save({ screen: screen.value });
      const density = $("qcs-density"); if (density) density.onchange = () => save({ density: density.value });
      body.querySelectorAll("[data-qcs-del]").forEach((b) => { b.onclick = async () => {
        if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = tr("Точно удалить?", "Delete for sure?"); return; }
        try { await ctx.api("/api/quick/canvas/actions/" + encodeURIComponent(b.dataset.qcsDel) + "?lang=" + (en ? "en" : "ru"), { method: "DELETE" }); ctx.toast(tr("Кнопка удалена", "Button deleted"), "ok"); load(); }
        catch (e) { ctx.toast(e.message, "err"); }
      }; });
      body.querySelectorAll("[data-qcs-forget]").forEach((b) => { b.onclick = async () => {
        const a = state.data.actions.find((x) => x.id === b.dataset.qcsForget);
        if (!a) return;
        const row = { ...a }; delete row.risks; delete row.approved;
        try { await post("/api/quick/canvas/actions", { action: row, forget_approval: true }); ctx.toast(tr("Разрешение сброшено", "Approval forgotten"), "ok"); load(); }
        catch (e) { ctx.toast(e.message, "err"); }
      }; });
      const exp = $("qcs-export");
      if (exp) exp.onclick = async () => {
        try { const data = await ctx.api("/api/quick/canvas/export"); download("memuai-quick-actions.json", JSON.stringify(data, null, 2)); }
        catch (e) { ctx.toast(e.message, "err"); }
      };
      const file = $("qcs-file"), imp = $("qcs-import");
      if (imp && file) {
        imp.onclick = () => file.click();
        file.onchange = async () => {
          const f = file.files && file.files[0];
          file.value = "";                    // the same file chosen again must fire «change» again
          if (!f) return;
          if (f.size > 4 * 1024 * 1024) { ctx.toast(tr("Файл больше 4 МБ", "The file is over 4 MB"), "err"); return; }
          let data = null;
          try { data = JSON.parse(await f.text()); } catch (e) { ctx.toast(tr("Это не JSON", "This is not JSON"), "err"); return; }
          try {
            const out = await post("/api/quick/canvas/import", { data, mode: $("qcs-replace").checked ? "replace" : "merge" });
            ctx.toast(tr(`Добавлено кнопок: ${out.added}`, `Added ${out.added} buttons`) + (out.skipped ? tr(`, пропущено: ${out.skipped}`, `, skipped: ${out.skipped}`) : ""), "ok");
            load();
          } catch (e) { ctx.toast(e.message, "err"); }
        };
      }
    }
    body.innerHTML = `<div class="qcs-settings">${note(esc(tr("Загружаю…", "Loading…")), "info")}</div>`;
    load();
  }, { title: { ru: "Панели и быстрые действия", en: "Panels and quick actions" }, order: 15, icon: ICON,
    desc: { ru: "Панели MemuAI и свои кнопки в любом месте экрана, сценарии из шагов", en: "MemuAI panels and your own buttons anywhere on the screen, multi-step routines" } });
})();
