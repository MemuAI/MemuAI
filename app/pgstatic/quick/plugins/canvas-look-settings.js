/* Round 30 — Settings → Quick Ask → «Вид панелей и встраивание» (plugin "canvas-embed").
   The look of every panel in Quick Access (window grid, screen layer, the built-in section), one panel's own look,
   and the panels built into the Alt+Space window. Data: GET /api/quick/canvas (look / looks / embed), saved through
   POST /api/quick/canvas/look (app/quick_panel_look.py → quick_canvas.json: the profile + Google Drive sync).
   The preview draws the user's real MemuAI panels with pgstatic/quick/panel-look.js. */
(function () {
  "use strict";
  if (!window.MemuQuick || typeof MemuQuick.settingsSection !== "function") return;
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4" width="17" height="16" rx="3"/><path d="M3.5 9h17M8 13h4M8 16h7"/></svg>';
  const STYLES = ["card", "compact", "text", "icon"];

  MemuQuick.settingsSection("canvas-embed", (body, ctx) => {
    const en = !!ctx.en;
    const tr = (ru, e) => (en ? e : ru);
    const esc = ctx.esc || ((v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));
    const K = window.MemuSettingsKit;
    const state = { data: null, panels: [], saveTimer: 0, pending: {} };
    const post = (payload) => ctx.api("/api/quick/canvas/look", { method: "POST", body: { lang: en ? "en" : "ru", ...payload } });
    const styleName = (s) => ({ card: tr("Карточка", "Card"), compact: tr("Компактно", "Compact"), text: tr("Только текст", "Text only"), icon: tr("Иконка + значение", "Icon + value") })[s];
    const themes = () => [["app", tr("Как в MemuAI", "Like MemuAI")], ["auto", tr("Как в Windows (авто)", "Like Windows (auto)")], ["light", tr("Светлая", "Light")], ["dark", tr("Тёмная", "Dark")]];

    function loadScript(src) {
      return new Promise((resolve) => { const s = document.createElement("script"); s.src = src; s.onload = resolve; s.onerror = resolve; document.head.appendChild(s); });
    }
    const css = (href, key) => {
      if (document.querySelector(`link[data-${key}]`)) return;
      const link = document.createElement("link");
      link.rel = "stylesheet"; link.href = href; link.setAttribute("data-" + key, "1");
      document.head.appendChild(link);
    };
    async function ensureLook() {
      css("/quick/panel-look.css?v=1.1-r32", "pl-css");
      css("/quick/panel-text.css?v=1.0-r32", "pt-css");
      css("/quick/panel-text-editor.css?v=1.1-r34", "pte-css");
      // Round 32: the text templates first (panel-look.js would load them itself, but later than the first preview)
      if (!window.MemuPanelText && !document.querySelector("script[data-pt-js]")) await loadScript("/quick/panel-text.js?v=1.0-r32");
      if (!window.MemuPanelLook) await loadScript("/quick/panel-look.js?v=1.1-r32");
      if (!window.MemuPanelTextEditor) await loadScript("/quick/panel-text-editor.js?v=1.0-r32");
    }
    async function loadPanels() {
      if (window.MemuPanels && MemuPanels.state && (MemuPanels.state.panels || []).length) return MemuPanels.state.panels;
      try { const r = await ctx.api("/api/pg/panels"); return (r && r.panels) || []; } catch (e) { return []; }
    }
    const look = () => ({ ...(window.MemuPanelLook ? MemuPanelLook.DEFAULT : {}), ...(state.data.look || {}), ...state.pending });
    const panelTitle = (id) => (state.panels.find((p) => p.id === id) || {}).title || id;
    const onCanvas = () => ((state.data.layout && state.data.layout.items) || []).filter((i) => i.type === "panel").map((i) => i.ref);

    /* ---------- preview: up to three real panels on a desktop-like backdrop */
    function previewHtml() {
      const d = { ...state.data, look: look() };
      const ids = [...new Set([...onCanvas(), ...state.panels.map((p) => p.id)])].slice(0, 3);
      const panels = ids.map((id) => state.panels.find((p) => p.id === id)).filter(Boolean);
      if (!panels.length || !window.MemuPanelLook) return `<div class="qls-preview qls-preview-empty">${esc(tr("Добавьте панель в MemuAI — здесь появится предпросмотр.", "Add a panel in MemuAI — the preview shows up here."))}</div>`;
      return `<div class="qls-preview" aria-label="${esc(tr("Предпросмотр", "Preview"))}">${panels.map((p) => {
        const lk = MemuPanelLook.resolve(d, p.id);
        const face = MemuPanelLook.faceHtml(p, lk, { place: "screen" })
          || (window.MemuPanels && MemuPanels.cardHtml ? MemuPanels.cardHtml(p, "home", { size: "s", static: true, place: "quick" }) : "");
        return `<div class="qls-tile" data-qls-look='${esc(JSON.stringify(lk))}'>${face}</div>`;
      }).join("")}</div>`;
    }
    function paintPreview() {
      const box = body.querySelector("#qls-preview-box");
      if (!box) return;
      box.innerHTML = previewHtml();
      box.querySelectorAll(".qls-tile").forEach((tile) => {
        let lk = {};
        try { lk = JSON.parse(tile.dataset.qlsLook || "{}"); } catch (e) { lk = {}; }
        if (window.MemuPanelLook) MemuPanelLook.apply(tile, lk, { place: "screen" });
      });
    }

    function html() {
      const l = look();
      const accentOn = /^#[0-9a-f]{6}$/i.test(l.accent || "");
      const global = K.section({ id: "qls-look", title: tr("Вид панелей", "Panel look"),
        keywords: "look style view opacity transparency font accent theme вид стиль прозрачность шрифт акцент тема карточка компактно текст иконка",
        desc: tr("Для всех панелей быстрого доступа: на экране вокруг окна, в окне и во встроенной секции. У отдельной панели можно задать свой вид — ниже или правым щелчком по ней.",
          "For every Quick Access panel: on the screen around the window, in the window and in the built-in section. A single panel can have its own look — below, or right-click it."),
        body: `<div id="qls-preview-box"></div>`
          + K.row({ label: tr("Вид", "Look"), control: K.segmented("qls-style", STYLES.map((s) => ({ value: s, label: styleName(s) })), l.style, { label: tr("Вид", "Look") }) })
          + K.row({ label: tr("Прозрачность фона", "Background opacity"), desc: tr("0 % — без фона: только содержимое поверх рабочего стола.", "0 % — no background: just the content over the desktop."),
            control: K.slider("qls-opacity", { min: 0, max: 100, step: 5, value: l.opacity, unit: " %" }) })
          + K.row({ label: tr("Размер шрифта", "Font size"), control: K.slider("qls-font", { min: 80, max: 140, step: 5, value: l.font, unit: " %" }) })
          + K.row({ label: tr("Акцентный цвет", "Accent colour"), desc: tr("Иконки, графики и выделения панелей.", "Icons, charts and highlights of the panels."),
            control: `<span class="qls-accent">${K.select("qls-accent-mode", [["", tr("Как в теме", "From the theme")], ["own", tr("Свой", "Custom")]], accentOn ? "own" : "", { width: 150 })}<input type="color" id="qls-accent" value="${esc(accentOn ? l.accent : "#d97757")}" ${accentOn ? "" : "hidden"} aria-label="${esc(tr("Цвет", "Colour"))}"></span>` })
          + K.row({ label: tr("Тема", "Theme"), desc: tr("«Как в Windows» переключается сама вместе со светлым/тёмным режимом системы.", "“Like Windows” follows the system light/dark mode by itself."),
            control: K.select("qls-theme", themes(), l.theme, { width: 200 }) })
          + K.more({ title: tr("Ещё", "More"), body:
            K.row({ label: tr("Названия панелей", "Panel names"), control: K.toggle("qls-titles", l.titles !== false) })
            + K.row({ label: tr("Строк значений", "Value lines"), desc: tr("Для видов «Компактно» и «Только текст».", "For the “Compact” and “Text only” looks."), control: K.slider("qls-lines", { min: 1, max: 5, step: 1, value: l.lines }) })
            + K.row({ label: tr("Скругление углов", "Corner radius"), control: K.slider("qls-radius", { min: 0, max: 24, step: 1, value: l.radius, unit: " px" }) })
            + K.row({ label: tr("Тень", "Shadow"), control: K.toggle("qls-shadow", l.shadow !== false) })
            + K.row({ label: tr("Приглушать на экране", "Dim on the screen"), desc: tr("Панели вокруг окна становятся полупрозрачными, пока указатель не над ними. 0 % — не приглушать.", "Panels around the window turn translucent until the pointer is over them. 0 % — never."),
              control: K.slider("qls-dim", { min: 0, max: 90, step: 10, value: l.dim, unit: " %" }) }) })
          + `<div class="qls-actions"><button type="button" class="pbtn ghost sx-btn-sm" id="qls-reset-all">${esc(tr("Сбросить вид всех панелей", "Reset the look of all panels"))}</button></div>` });

      const own = state.data.looks || {};
      const ids = [...new Set([...onCanvas(), ...(state.data.embed.items || []).map((e) => e.ref), ...Object.keys(own)])];
      const perPanel = K.section({ id: "qls-own", title: tr("Свой вид отдельных панелей", "A panel's own look"), keywords: "per panel own свой вид отдельной панели",
        desc: tr("Перекрывает общий вид только для этой панели.", "Overrides the common look for this panel only."),
        body: ids.length ? `<div class="qls-list">${ids.map((id) => {
          const o = own[id] || {};
          return `<div class="qls-item" data-qls-panel="${esc(id)}"><span class="qls-name"><b>${esc(panelTitle(id))}</b><small>${esc(Object.keys(o).length ? tr("свой вид", "own look") : tr("как у всех", "like all"))}</small></span>
            ${K.select("qls-own-style-" + id, [["", tr("Вид как у всех", "Look like all")], ...STYLES.map((s) => [s, styleName(s)])], o.style || "", { width: 170, attrs: `data-qls-own="style"` })}
            ${K.select("qls-own-theme-" + id, [["", tr("Тема как у всех", "Theme like all")], ...themes()], o.theme || "", { width: 170, attrs: `data-qls-own="theme"` })}
            <input type="color" class="qls-own-color" data-qls-own="accent" value="${esc(o.accent || "#d97757")}" title="${esc(tr("Свой акцент", "Own accent"))}" aria-label="${esc(tr("Свой акцент", "Own accent"))}">
            <button type="button" class="pbtn ghost sx-btn-sm qls-text-btn" data-qls-text="${esc(id)}" title="${esc(tr("Текст панели: свои слова и значения где угодно", "Panel text: your own words and values anywhere"))}">${esc(o.text ? tr("Текст ✓", "Text ✓") : tr("Текст…", "Text…"))}</button>
            ${Object.keys(o).length ? `<button type="button" class="pbtn ghost sx-btn-sm" data-qls-own-reset="${esc(id)}">${esc(tr("Сбросить", "Reset"))}</button>` : ""}</div>`;
        }).join("")}</div>` : K.note(esc(tr("Панелей в быстром доступе пока нет: добавьте их в окне (Ctrl+B → «Добавить»).", "No panels in Quick Access yet: add them in the window (Ctrl+B → “Add”).")), "info") });

      const e = state.data.embed;
      const known = new Map(e.items.map((x) => [x.ref, x]));
      const order = [...e.items.map((x) => x.ref), ...state.panels.map((p) => p.id).filter((id) => !known.has(id))];
      const embed = K.section({ id: "qls-embed", title: tr("Панели в окне быстрого доступа", "Panels inside the Quick Access window"),
        keywords: "embed built in menu window side column над полем под полем боковая колонка встроить окно",
        desc: tr("Секция прямо в окне Alt+Space — над полем ввода, под ним или колонкой справа. Порядок — стрелками, отметка — показывать.",
          "A section right in the Alt+Space window — above the input, below it or as a column on the right. Order with the arrows; the tick shows a panel."),
        body: K.row({ label: tr("Где", "Where"), control: K.select("qls-embed-place", [["off", tr("Не встраивать", "Off")], ["above", tr("Над полем ввода", "Above the input")], ["below", tr("Под полем ввода", "Below the input")], ["side", tr("Колонка справа", "Column on the right")]], e.place, { width: 200 }) })
          + K.row({ label: tr("Вид в окне", "Look in the window"), control: K.select("qls-embed-style", [["", tr("Как у всех панелей", "Like all panels")], ...STYLES.map((s) => [s, styleName(s)])], e.style, { width: 200 }) })
          + K.row({ label: tr("Сколько показывать", "How many to show"), control: K.slider("qls-embed-max", { min: 1, max: 12, step: 1, value: e.max }) })
          + (order.length ? `<div class="qls-list qls-embed-list">${order.map((id, i) => {
            const row = known.get(id);
            const on = !!row && !row.hidden;
            return `<div class="qls-item" data-qls-embed="${esc(id)}"><label class="qls-check"><input type="checkbox" ${on ? "checked" : ""} data-qls-embed-on="${esc(id)}"><span class="qls-name"><b>${esc(panelTitle(id))}</b></span></label>
              <button type="button" class="pbtn ghost sx-btn-sm qls-mv" data-qls-mv="-1" data-id="${esc(id)}" ${i === 0 ? "disabled" : ""} aria-label="${esc(tr("Выше", "Up"))}" title="${esc(tr("Выше", "Up"))}">↑</button>
              <button type="button" class="pbtn ghost sx-btn-sm qls-mv" data-qls-mv="1" data-id="${esc(id)}" ${i === order.length - 1 ? "disabled" : ""} aria-label="${esc(tr("Ниже", "Down"))}" title="${esc(tr("Ниже", "Down"))}">↓</button></div>`;
          }).join("")}</div>` : K.note(esc(tr("В MemuAI пока нет панелей.", "MemuAI has no panels yet.")), "info")) });
      return `<div class="qls-settings">${global}${embed}${perPanel}</div>`;
    }

    function draw() {
      const scroller = body.closest(".settings-body");
      const top = scroller ? scroller.scrollTop : 0;
      body.innerHTML = html();
      if (scroller) scroller.scrollTop = top;
      paintPreview();
      bind();
    }
    async function save(payload, redraw = true) {
      try {
        const out = await post(payload);
        Object.assign(state.data, { look: out.look, looks: out.looks, embed: out.embed });
        if (redraw) draw(); else paintPreview();
      } catch (e) { ctx.toast(e.message, "err"); }
    }
    function lookSoon(patch) {           // sliders: the preview follows at once, the save after a short pause
      Object.assign(state.pending, patch);
      paintPreview();
      clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(() => { const p = state.pending; state.pending = {}; save({ look: p }, false); }, 350);
    }
    function embedItems(mutator) {
      const known = new Map(state.data.embed.items.map((x) => [x.ref, { ...x }]));
      const order = [...state.data.embed.items.map((x) => x.ref), ...state.panels.map((p) => p.id).filter((id) => !known.has(id))];
      const rows = order.map((id) => known.get(id) || { ref: id, hidden: true });
      mutator(rows);
      return rows;
    }
    function bind() {
      const $ = (id) => body.querySelector("#" + id);
      body.querySelectorAll('input[name="qls-style"]').forEach((r) => { r.onchange = () => save({ look: { style: r.value } }); });
      for (const [id, key] of [["qls-opacity", "opacity"], ["qls-font", "font"], ["qls-lines", "lines"], ["qls-radius", "radius"], ["qls-dim", "dim"]]) {
        const el = $(id); if (el) el.addEventListener("input", () => lookSoon({ [key]: +el.value }));
      }
      for (const [id, key] of [["qls-titles", "titles"], ["qls-shadow", "shadow"]]) { const el = $(id); if (el) el.onchange = () => save({ look: { [key]: el.checked } }, false); }
      const theme = $("qls-theme"); if (theme) theme.onchange = () => save({ look: { theme: theme.value } }, false);
      const mode = $("qls-accent-mode"), color = $("qls-accent");
      if (mode && color) {
        mode.onchange = () => { color.hidden = mode.value !== "own"; save({ look: { accent: mode.value === "own" ? color.value : "" } }, false); };
        color.addEventListener("input", () => lookSoon({ accent: color.value }));
      }
      const resetAll = $("qls-reset-all");
      if (resetAll) resetAll.onclick = () => {
        if (resetAll.dataset.confirm !== "1") { resetAll.dataset.confirm = "1"; resetAll.textContent = tr("Нажмите ещё раз — сбросить", "Click again to reset"); return; }
        save({ reset_all: true });
      };
      body.querySelectorAll("[data-qls-panel]").forEach((rowEl) => {
        const id = rowEl.dataset.qlsPanel;
        rowEl.querySelectorAll("select[data-qls-own]").forEach((sel) => { sel.onchange = () => save({ panel: id, look: { [sel.dataset.qlsOwn]: sel.value || null } }); });
        const c = rowEl.querySelector('input[data-qls-own="accent"]');
        if (c) c.onchange = () => save({ panel: id, look: { accent: c.value } });
      });
      body.querySelectorAll("[data-qls-text]").forEach((b) => { b.onclick = () => openText(b.dataset.qlsText, b); });
      body.querySelectorAll("[data-qls-own-reset]").forEach((b) => { b.onclick = () => save({ panel: b.dataset.qlsOwnReset, reset: true }); });
      const place = $("qls-embed-place"); if (place) place.onchange = () => save({ embed: { place: place.value } });
      const est = $("qls-embed-style"); if (est) est.onchange = () => save({ embed: { style: est.value } });
      const max = $("qls-embed-max"); if (max) max.addEventListener("change", () => save({ embed: { max: +max.value } }, false));
      body.querySelectorAll("[data-qls-embed-on]").forEach((cb) => { cb.onchange = () => {
        const id = cb.dataset.qlsEmbedOn;
        const items = embedItems((rows) => { const r = rows.find((x) => x.ref === id); r.hidden = !cb.checked; });
        const patch = { items };
        if (cb.checked && state.data.embed.place === "off") patch.place = "below";       // ticking a panel turns the section on
        save({ embed: patch });
      }; });
      body.querySelectorAll("[data-qls-mv]").forEach((b) => { b.onclick = () => {
        const id = b.dataset.id, dir = +b.dataset.qlsMv;
        const items = embedItems((rows) => { const i = rows.findIndex((x) => x.ref === id), j = i + dir; if (rows[j]) [rows[i], rows[j]] = [rows[j], rows[i]]; });
        save({ embed: { items } });
      }; });
    }
    /* ---------- Round 32: a panel's text template (panel-text-editor.js) in a dialog */
    function openText(id, opener) {
      const panel = state.panels.find((p) => p.id === id);
      if (!panel || !window.MemuPanelTextEditor || !window.MemuPanelText) { ctx.toast(tr("Панель ещё не загрузилась", "The panel has not loaded yet"), "err"); return; }
      const d = { ...state.data, look: look() };
      const lk = MemuPanelLook.resolve(d, id, { embed: true });
      const side = state.data.embed && state.data.embed.place === "side";
      const modal = document.createElement("div");
      modal.className = "pte-modal";
      modal.innerHTML = `<div class="pte-host" lang="${en ? "en" : "ru"}"></div>`;
      document.body.appendChild(modal);
      const close = () => { modal.remove(); if (opener && opener.isConnected) opener.focus(); };
      const ed = MemuPanelTextEditor.mount(modal.firstElementChild, {
        panel, look: lk, text: ((state.data.looks || {})[id] || {}).text || null, lang: en ? "en" : "ru",
        width: side ? 188 : 248, widths: [[188, tr("Колонка", "Column")], [248, tr("Ряд", "Row")], [340, tr("Широко", "Wide")]],
        backdrop: "wallpaper", place: "screen",
        onSave: async (text) => { await save({ panel: id, look: { style: "text", text } }); close(); },
        onCancel: close,
      });
      modal.addEventListener("mousedown", (e) => { if (e.target === modal) close(); });
      if (ed) setTimeout(() => { const f = modal.querySelector(".pte-stage .pt-b"); if (f) f.focus({ preventScroll: true }); }, 30);
    }

    async function load() {
      try {
        await ensureLook();
        [state.data, state.panels] = await Promise.all([ctx.api("/api/quick/canvas?lang=" + (en ? "en" : "ru")), loadPanels()]);
      } catch (e) { body.innerHTML = K.note(esc(tr("Не удалось загрузить настройки: ", "Could not load the settings: ") + e.message), "warn"); return; }
      draw();
    }
    body.innerHTML = `<div class="qls-settings">${K.note(esc(tr("Загружаю…", "Loading…")), "info")}</div>`;
    load();
  }, { title: { ru: "Вид панелей и встраивание", en: "Panel look and embedding" }, order: 16, icon: ICON,
    desc: { ru: "Карточка, компактно, только текст, иконка; прозрачность, шрифт, акцент, тема; панели прямо в окне", en: "Card, compact, text only, icon; opacity, font, accent, theme; panels right in the window" } });
})();
