

(function () {
  "use strict";
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const r1 = (v) => Math.round(v * 10) / 10;
  const SNAP = 2;
  const FILTER_LIST = [
    ["round:0", "Округлить", "Round"], ["fixed:2", "Два знака после запятой", "Two decimals"], ["signed", "Знак: +2,4 / −1,1", "Sign: +2.4 / −1.1"],
    ["tone", "Цвет: зелёный рост, красное падение", "Colour: green up, red down"], ["pct", "Проценты", "Percent"], ["short", "Коротко: 1,2 млн", "Short: 1.2M"],
    ["upper", "ЗАГЛАВНЫМИ", "UPPERCASE"], ["cap", "С заглавной", "Capitalize"], ["trim:30", "Обрезать до 30 знаков", "Cut to 30 characters"],
    ["date", "Дата: 14 окт.", "Date: 14 Oct"], ["date:long", "Дата: пн, 14 октября", "Date: Mon, 14 October"], ["time", "Время: 19:30", "Time: 19:30"],
    ["weekday", "День недели", "Weekday"], ["rel", "Через 3 ч / 2 дн. назад", "In 3 h / 2 days ago"], ["days", "Сколько дней до", "Days until"],
    [['default:"нет данных"', 'default:"no data"'], "Если пусто — свой текст", "If empty — your text"], ["limit:3", "Список: первые 3", "List: first 3"],
    ['join:" · "', "Список в строку", "List as one line"], ["count", "Сколько элементов", "How many items"],
  ];

  function mount(host, opts) {
    const T = window.MemuPanelText;
    if (!host || !T) return null;
    const en = opts.lang === "en";
    const tr = (ru, e) => (en ? e : ru);
    let panel = opts.panel;
    const fresh = () => { const d = T.defaultText(panel, { lang: opts.lang }); delete d.auto; d.blocks.forEach((b) => delete b.cap); return d; };
    const st = {
      text: opts.text && Array.isArray(opts.text.blocks) && opts.text.blocks.length ? JSON.parse(JSON.stringify(opts.text)) : fresh(),
      auto: !(opts.text && opts.text.blocks && opts.text.blocks.length),
      sel: 0, width: opts.width || 240, dirty: false, busy: false, filter: "",
    };
    const look = { ...(opts.look || {}), style: "text" };

    host.innerHTML = "";
    const el = document.createElement("div");
    el.className = "pte";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-label", tr("Текст панели", "Panel text"));
    host.appendChild(el);

    function blockLabel(b, i) {
      const s = String(b.t || "").replace(/\s+/g, " ").trim();
      return s.length > 26 ? s.slice(0, 25) + "…" : s || tr("Блок ", "Block ") + (i + 1);
    }
    function frame() {
      const widths = opts.widths && opts.widths.length > 1 ? `<span class="pte-seg" role="group" aria-label="${esc(tr("Ширина предпросмотра", "Preview width"))}">${opts.widths.map(([w, l]) => `<button type="button" data-pte-width="${w}" aria-pressed="${w === st.width}">${esc(l)}</button>`).join("")}</span>` : "";
      el.innerHTML = `
        <div class="pte-head">
          <div class="pte-title"><b>${esc(tr("Текст панели", "Panel text"))}</b><span>${esc(panel.title || "")}</span></div>
          <span class="pte-seg" role="group" aria-label="${esc(tr("Расстановка", "Layout"))}">
            <button type="button" data-pte-mode="flow">${esc(tr("По строкам", "By lines"))}</button><button type="button" data-pte-mode="free">${esc(tr("Свободно", "Free"))}</button></span>
          ${widths}
        </div>
        <div class="pte-stage-wrap" data-bg="${esc(opts.backdrop || "window")}">
          <div class="pte-cell"><div class="pte-stage-host"></div><i class="pte-hh" title="${esc(tr("Высота панели", "Panel height"))}" aria-hidden="true"></i></div>
        </div>
        <p class="pte-hint"></p>
        <div class="pte-blocks" role="tablist" aria-label="${esc(tr("Блоки текста", "Text blocks"))}"></div>
        <div class="pte-grid">
          <div class="pte-edit">
            <label class="pte-lbl" for="pte-tpl">${esc(tr("Шаблон блока", "Block template"))}</label>
            <textarea id="pte-tpl" class="pte-tpl" rows="3" spellcheck="false" autocomplete="off"></textarea>
            <div class="pte-lint" aria-live="polite"></div>
            <div class="pte-props">
              <label class="pte-prop"><span>${esc(tr("Размер", "Size"))}</span><input type="range" min="50" max="400" step="5" data-pte-prop="size"><output></output></label>
              <label class="pte-prop"><span>${esc(tr("Жирность", "Weight"))}</span><select data-pte-prop="weight">${[300, 400, 500, 600, 700, 800].map((w) => `<option value="${w}">${w}</option>`).join("")}</select></label>
              <label class="pte-prop"><span>${esc(tr("Цвет", "Colour"))}</span><select data-pte-prop="color">
                <option value="">${esc(tr("Текст", "Text"))}</option><option value="muted">${esc(tr("Приглушённый", "Muted"))}</option><option value="accent">${esc(tr("Акцент", "Accent"))}</option>
                <option value="up">${esc(tr("Зелёный", "Green"))}</option><option value="down">${esc(tr("Красный", "Red"))}</option><option value="own">${esc(tr("Свой…", "Custom…"))}</option></select>
                <input type="color" data-pte-prop="hex" aria-label="${esc(tr("Свой цвет", "Custom colour"))}"></label>
              <label class="pte-prop"><span>${esc(tr("Шрифт", "Font"))}</span><select data-pte-prop="font">
                <option value="">${esc(tr("Обычный", "Regular"))}</option><option value="display">${esc(tr("Заголовочный", "Display"))}</option><option value="mono">${esc(tr("Моноширинный", "Monospace"))}</option><option value="serif">${esc(tr("С засечками", "Serif"))}</option></select></label>
              <span class="pte-seg pte-align" role="group" aria-label="${esc(tr("Выравнивание", "Alignment"))}">
                <button type="button" data-pte-align="left" title="${esc(tr("По левому краю", "Left"))}" aria-label="${esc(tr("По левому краю", "Left"))}"><svg viewBox="0 0 16 16"><path d="M2 3h12M2 6.5h8M2 10h12M2 13.5h7"/></svg></button><button type="button" data-pte-align="center" title="${esc(tr("По центру", "Centre"))}" aria-label="${esc(tr("По центру", "Centre"))}"><svg viewBox="0 0 16 16"><path d="M2 3h12M4 6.5h8M2 10h12M4.5 13.5h7"/></svg></button><button type="button" data-pte-align="right" title="${esc(tr("По правому краю", "Right"))}" aria-label="${esc(tr("По правому краю", "Right"))}"><svg viewBox="0 0 16 16"><path d="M2 3h12M6 6.5h8M2 10h12M7 13.5h7"/></svg></button></span>
              <button type="button" class="pte-ital" data-pte-italic aria-pressed="false" title="${esc(tr("Курсив", "Italic"))}" aria-label="${esc(tr("Курсив", "Italic"))}"><i>I</i></button>
            </div>
            <div class="pte-bact">
              <button type="button" data-pte-act="up" title="${esc(tr("Выше", "Up"))}" aria-label="${esc(tr("Выше", "Up"))}">↑</button>
              <button type="button" data-pte-act="down" title="${esc(tr("Ниже", "Down"))}" aria-label="${esc(tr("Ниже", "Down"))}">↓</button>
              <button type="button" data-pte-act="dup">${esc(tr("Копия", "Duplicate"))}</button>
              <button type="button" data-pte-act="del" class="pte-danger">${esc(tr("Удалить блок", "Delete block"))}</button>
              <label class="pte-prop pte-halo"><span>${esc(tr("Обводка для читаемости", "Halo for readability"))}</span><select data-pte-halo>
                <option value="auto">${esc(tr("Авто", "Auto"))}</option><option value="off">${esc(tr("Нет", "None"))}</option><option value="soft">${esc(tr("Мягкая", "Soft"))}</option><option value="strong">${esc(tr("Сильная", "Strong"))}</option></select></label>
            </div>
          </div>
          <div class="pte-picker">
            <div class="pte-lbl">${esc(tr("Значения панели", "Panel values"))} <small>${esc(tr("— щёлкните, чтобы вставить", "— click to insert"))}</small></div>
            <input type="search" class="pte-find" placeholder="${esc(tr("Найти значение", "Find a value"))}" aria-label="${esc(tr("Найти значение", "Find a value"))}">
            <div class="pte-list" role="list"></div>
            <details class="pte-filters"><summary>${esc(tr("Фильтры форматирования", "Formatting filters"))}</summary>
              <div class="pte-flist">${FILTER_LIST.map(([f0, ru, e]) => { const f = Array.isArray(f0) ? f0[en ? 1 : 0] : f0; return `<button type="button" data-pte-filter="${esc(f)}"><code>|${esc(f)}</code><span>${esc(en ? e : ru)}</span></button>`; }).join("")}</div>
              <p class="pte-syntax">${esc(tr("Список: {#items|limit:3}{title}↵{/items} · запасной вариант: {a ?? b ?? \"нет\"} · только если пусто: {^a}…{/a}",
                "List: {#items|limit:3}{title}↵{/items} · fallback: {a ?? b ?? \"none\"} · only when empty: {^a}…{/a}"))}</p>
            </details>
          </div>
        </div>
        <div class="pte-foot">
          <button type="button" class="pte-btn ghost" data-pte-act="add">+ ${esc(tr("Текст", "Text"))}</button>
          <button type="button" class="pte-btn ghost" data-pte-act="auto" title="${esc(tr("Шаблон, который панель собирает сама", "The template the panel builds itself"))}">${esc(tr("Автоматически", "Automatic"))}</button>
          <span class="pte-grow"></span>
          <button type="button" class="pte-btn ghost" data-pte-act="cancel">${esc(tr("Отмена", "Cancel"))}</button>
          <button type="button" class="pte-btn primary" data-pte-act="save">${esc(tr("Сохранить", "Save"))}</button>
        </div>`;
      const cell = el.querySelector(".pte-cell");
      if (window.MemuPanelLook) MemuPanelLook.apply(cell, look, { place: opts.place || "window" });
      cell.dataset.plPlace = opts.place || "window";
      bind();
    }

    const stageHost = () => el.querySelector(".pte-stage-host");
    const stage = () => el.querySelector(".pte-stage-host > .pt-face");
    function paintStage() {
      const holder = stageHost();
      if (!holder) return;
      const cell = el.querySelector(".pte-cell");
      cell.style.width = st.width + "px";
      holder.innerHTML = T.faceHtml(panel, { ...look, text: st.text }, { lang: opts.lang, place: opts.place || "window" });
      const face = stage();
      face.classList.add("pte-stage");
      face.removeAttribute("title");
      face.querySelectorAll(".pt-b").forEach((b, i) => {
        b.tabIndex = 0;
        b.setAttribute("role", "button");
        b.setAttribute("aria-label", tr("Блок ", "Block ") + (i + 1) + ": " + (b.textContent || "").trim().slice(0, 60));
        if (i === st.sel) { b.classList.add("pte-on"); if (st.text.mode === "free") b.insertAdjacentHTML("beforeend", `<i class="pte-rs" title="${esc(tr("Ширина блока", "Block width"))}"></i>`); }
      });
      el.querySelector(".pte-hh").hidden = st.text.mode !== "free";
      el.querySelector(".pte-hint").textContent = st.text.mode === "free"
        ? tr("Перетащите текст в любое место панели. Стрелки сдвигают выбранный блок (с Shift — сильнее), правый край — ширина, нижний край панели — высота.",
          "Drag text anywhere in the panel. Arrow keys nudge the selected block (Shift — more), its right edge sets the width, the panel's bottom edge — the height.")
        : tr("Блоки идут строками. Перетащите любой — и расставляйте свободно, где угодно в панели.",
          "Blocks flow as lines. Drag any of them to place them freely, anywhere in the panel.");
      el.querySelectorAll("[data-pte-mode]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.pteMode === st.text.mode)));
      el.querySelectorAll("[data-pte-width]").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.pteWidth === st.width)));
    }
    function paintBlocks() {
      const box = el.querySelector(".pte-blocks");
      box.innerHTML = st.text.blocks.map((b, i) => `<button type="button" role="tab" data-pte-sel="${i}" aria-selected="${i === st.sel}"><small>${i + 1}</small>${esc(blockLabel(b, i))}</button>`).join("");
    }
    function paintProps() {
      const b = st.text.blocks[st.sel];
      const ta = el.querySelector(".pte-tpl");
      if (!b) return;
      if (document.activeElement !== ta) ta.value = b.t;
      const size = el.querySelector('[data-pte-prop="size"]');
      size.value = b.size; size.nextElementSibling.textContent = b.size + " %";
      el.querySelector('[data-pte-prop="weight"]').value = String(b.weight);
      const own = /^#/.test(b.color || "");
      el.querySelector('[data-pte-prop="color"]').value = own ? "own" : (b.color || "");
      const hex = el.querySelector('[data-pte-prop="hex"]');
      hex.hidden = !own; hex.value = own ? b.color : "#d97757";
      el.querySelector('[data-pte-prop="font"]').value = b.font || "";
      el.querySelectorAll("[data-pte-align]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.pteAlign === (b.align || "left"))));
      el.querySelector("[data-pte-italic]").setAttribute("aria-pressed", String(!!b.italic));
      el.querySelector("[data-pte-halo]").value = st.text.halo || "auto";
      el.querySelector('[data-pte-act="up"]').disabled = st.sel === 0;
      el.querySelector('[data-pte-act="down"]').disabled = st.sel >= st.text.blocks.length - 1;
      el.querySelector('[data-pte-act="del"]').disabled = st.text.blocks.length <= 1;
      el.querySelector('[data-pte-act="add"]').disabled = st.text.blocks.length >= 12;
      paintLint();
    }
    function paintLint() {
      const b = st.text.blocks[st.sel];
      const box = el.querySelector(".pte-lint");
      if (!b) { box.innerHTML = ""; return; }
      const issues = T.lint(b.t, T.contextOf(panel));
      const seen = new Set();
      box.innerHTML = issues.filter((x) => { const k = x.code + x.name; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 3).map((x) => `<span>${esc({
        open: tr(`Нет закрывающего {/${x.name}}`, `Missing the closing {/${x.name}}`),
        close: tr(`Лишнее {/${x.name}}`, `Extra {/${x.name}}`),
        depth: tr("Слишком глубокая вложенность", "Nested too deep"),
        filter: tr(`Неизвестный фильтр «${x.name}»`, `Unknown filter “${x.name}”`),
        missing: tr(`Сейчас нет значения {${x.name}} — будет «—»`, `No value {${x.name}} right now — shows “—”`),
      }[x.code] || x.code)}</span>`).join("");
    }
    function paintPicker() {
      const list = el.querySelector(".pte-list");
      const q = st.filter.trim().toLowerCase();
      const rows = T.placeholders(panel, { lang: opts.lang }).filter((p) => !q || p.path.toLowerCase().includes(q) || String(p.preview).toLowerCase().includes(q));
      list.innerHTML = rows.length ? rows.map((p) => `<button type="button" role="listitem" data-pte-ins="${esc(p.path)}" data-kind="${p.kind}" title="${esc(p.kind === "list" ? tr("Вставить список", "Insert a list") : p.preview)}">
          <code>${esc(p.kind === "list" ? "{#" + p.path + "}" : "{" + p.path + "}")}</code><span>${esc(p.kind === "list" ? tr(`список · ${p.count}`, `list · ${p.count}`) : (p.preview || "—"))}</span></button>`).join("")
        : `<p class="pte-empty">${esc(q ? tr("Ничего не найдено", "Nothing found") : tr("У панели пока нет данных — обновите её.", "The panel has no data yet — refresh it."))}</p>`;
    }
    function paintAll() { paintStage(); paintBlocks(); paintProps(); }
    const changed = () => { st.dirty = true; st.auto = false; };

    const ta = () => el.querySelector(".pte-tpl");
    function insert(str, { filter = false } = {}) {
      const area = ta();
      const b = st.text.blocks[st.sel];
      if (!area || !b) return;
      let start = area.selectionStart ?? area.value.length, end = area.selectionEnd ?? start;
      if (document.activeElement !== area && area.dataset.caret) { start = end = +area.dataset.caret; }
      if (filter) {
        const before = area.value.slice(0, start);
        const open = before.lastIndexOf("{"), shut = before.lastIndexOf("}");
        const close = area.value.indexOf("}", start);
        if (open > shut && close >= 0) start = end = close;
        else if (shut >= 0 && shut === start - 1) start = end = shut;
        else str = "{" + (T.placeholders(panel, {})[0] || { path: "value" }).path + str + "}";
      }
      area.focus();
      area.setRangeText(str, start, end, "end");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    }
    function listSnippet(path) {
      const v = T.lookup(path, [{ item: T.contextOf(panel) }]);
      const row = Array.isArray(v) ? v.find((x) => x != null) : null;
      let field = ".";
      if (row && typeof row === "object") field = ["title", "name", "label", "text", "value"].find((k) => row[k] != null && typeof row[k] !== "object") || Object.keys(row).find((k) => typeof row[k] !== "object") || ".";
      const extra = row && typeof row === "object" && field !== "value" && row.value != null && typeof row.value !== "object" ? " — {value}" : "";
      return `{#${path}|limit:3}{${field}}${extra}\n{/${path}}`;
    }

    function toFree() {
      if (st.text.mode === "free") return;
      const face = stage();
      const box = face.getBoundingClientRect();
      const font = Math.max(.8, Math.min(1.4, (+look.font || 100) / 100));
      const h = Math.max(40, Math.round(box.height / font) + 4);
      face.querySelectorAll(".pt-b").forEach((n) => {

        const r = n.getBoundingClientRect();
        let t = r;
        try { const range = document.createRange(); range.selectNodeContents(n); const rr = range.getBoundingClientRect(); if (rr.width) t = rr; } catch (e) {  }
        const b = st.text.blocks[+n.dataset.i];
        if (!b) return;
        const w = clamp((t.width + 6) / box.width * 100, 5, 100);
        b.x = r1(clamp((t.left - box.left - 2) / box.width * 100, 0, 100 - w));
        b.y = r1(clamp((r.top - box.top) / (h * font) * 100, 0, 95));
        b.w = r1(clamp(w, 5, 100 - b.x));
        if (b.align !== "left") b.align = "left";
      });
      st.text.mode = "free";
      st.text.h = Math.min(480, h);
    }
    function guides(xs, ys) {
      const face = stage();
      if (!face) return;
      face.querySelectorAll(".pte-guide").forEach((g) => g.remove());
      xs.forEach((x) => face.insertAdjacentHTML("beforeend", `<i class="pte-guide" data-ax="x" style="left:${x}%"></i>`));
      ys.forEach((y) => face.insertAdjacentHTML("beforeend", `<i class="pte-guide" data-ax="y" style="top:${y}%"></i>`));
    }
    function onStageDown(e) {
      if (e.button !== 0) return;
      const node = e.target.closest(".pt-b");
      if (!node) return;
      const i = +node.dataset.i;
      const resize = !!e.target.closest(".pte-rs");
      if (st.sel !== i) { st.sel = i; paintStage(); paintBlocks(); paintProps(); }
      const startX = e.clientX, startY = e.clientY;
      let moving = false, base = null;
      e.preventDefault();
      const move = (ev) => {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (!moving) {
          if (Math.abs(dx) + Math.abs(dy) < 4) return;
          moving = true;
          if (st.text.mode !== "free") { toFree(); paintStage(); }
          const b0 = st.text.blocks[st.sel];
          const r = stage().getBoundingClientRect();
          const n0 = stage().querySelector(`.pt-b[data-i="${st.sel}"]`);
          base = { x: b0.x, y: b0.y, w: b0.w, W: r.width, H: r.height, ww: n0 ? Math.min(100, n0.getBoundingClientRect().width / r.width * 100) : b0.w };
          el.classList.add("pte-dragging");
        }
        const b = st.text.blocks[st.sel];
        if (resize) {
          b.w = r1(clamp(base.w + dx / base.W * 100, 5, 100 - b.x));
        } else {
          let x = base.x + dx / base.W * 100, y = base.y + dy / base.H * 100;
          const gx = [], gy = [];
          if (!ev.altKey) {
            if (Math.abs(x) < SNAP) { x = 0; gx.push(0); }
            if (Math.abs(x + base.ww / 2 - 50) < SNAP) { x = 50 - base.ww / 2; gx.push(50); }
            if (Math.abs(x + base.ww - 100) < SNAP) { x = 100 - base.ww; gx.push(100); }
            if (Math.abs(y) < SNAP) { y = 0; gy.push(0); }
          }
          b.x = r1(clamp(x, 0, 100 - Math.min(base.ww, 100))); b.y = r1(clamp(y, 0, 95));
          if (b.x + b.w > 100) b.w = r1(Math.max(5, 100 - b.x));
          const n = stage().querySelector(`.pt-b[data-i="${st.sel}"]`);
          if (n) { n.style.left = b.x + "%"; n.style.top = b.y + "%"; n.style.maxWidth = (100 - b.x) + "%"; }
          guides(gx, gy);
          return;
        }
        const n = stage().querySelector(`.pt-b[data-i="${st.sel}"]`);
        if (n) n.style.minWidth = Math.min(b.w, 100 - b.x) + "%";
      };
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        el.classList.remove("pte-dragging");
        if (stage()) stage().querySelectorAll(".pte-guide").forEach((g) => g.remove());
        if (moving) { changed(); paintStage(); paintProps(); }
        const n = stage() && stage().querySelector(`.pt-b[data-i="${st.sel}"]`);
        if (n) n.focus({ preventScroll: true });
      };

      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
    }
    function onHeightDown(e) {
      if (e.button !== 0 || st.text.mode !== "free") return;
      e.preventDefault();
      const startY = e.clientY, h0 = st.text.h;
      const font = Math.max(.8, Math.min(1.4, (+look.font || 100) / 100));
      const move = (ev) => { st.text.h = Math.round(clamp(h0 + (ev.clientY - startY) / font, 24, 480)); const f = stage(); if (f) f.style.setProperty("--pt-h", st.text.h + "px"); };
      const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); changed(); };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }
    function onStageKey(e) {
      const node = e.target.closest && e.target.closest(".pt-b");
      if (!node) return;
      const i = +node.dataset.i;
      if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); st.sel = i; paintAll(); ta().focus(); return; }
      if (!/^Arrow/.test(e.key)) return;
      e.preventDefault();
      st.sel = i;
      const b = st.text.blocks[i];
      if (st.text.mode !== "free") {
        const j = i + (e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 1);
        if (!st.text.blocks[j]) return;
        [st.text.blocks[i], st.text.blocks[j]] = [st.text.blocks[j], st.text.blocks[i]];
        st.sel = j;
      } else {
        const d = e.shiftKey ? 5 : 1;
        const fr = stage().getBoundingClientRect();
        const ww = Math.min(100, node.getBoundingClientRect().width / (fr.width || 1) * 100);
        if (e.key === "ArrowLeft") b.x = r1(clamp(b.x - d, 0, 100 - ww));
        if (e.key === "ArrowRight") b.x = r1(clamp(b.x + d, 0, Math.max(0, 100 - ww)));
        if (b.x + b.w > 100) b.w = r1(Math.max(5, 100 - b.x));
        if (e.key === "ArrowUp") b.y = r1(clamp(b.y - d, 0, 95));
        if (e.key === "ArrowDown") b.y = r1(clamp(b.y + d, 0, 95));
      }
      changed();
      paintAll();
      const n = stage().querySelector(`.pt-b[data-i="${st.sel}"]`);
      if (n) n.focus({ preventScroll: true });
    }

    function bind() {
      el.querySelector(".pte-stage-host").addEventListener("pointerdown", onStageDown);
      el.querySelector(".pte-stage-host").addEventListener("keydown", onStageKey);
      el.querySelector(".pte-stage-host").addEventListener("dblclick", (e) => { if (e.target.closest(".pt-b")) ta().focus(); });
      el.querySelector(".pte-hh").addEventListener("pointerdown", onHeightDown);
      el.addEventListener("click", onClick);
      const area = ta();
      area.addEventListener("input", () => { const b = st.text.blocks[st.sel]; if (!b) return; b.t = area.value.slice(0, 1000); changed(); paintStage(); paintBlocks(); paintLint(); });
      area.addEventListener("blur", () => { area.dataset.caret = String(area.selectionStart || 0); });
      el.querySelector('[data-pte-prop="size"]').addEventListener("input", (e) => { const b = st.text.blocks[st.sel]; b.size = +e.target.value; e.target.nextElementSibling.textContent = b.size + " %"; changed(); paintStage(); });
      for (const key of ["weight", "font"]) el.querySelector(`[data-pte-prop="${key}"]`).addEventListener("change", (e) => { const b = st.text.blocks[st.sel]; b[key] = key === "weight" ? +e.target.value : e.target.value; changed(); paintStage(); });
      el.querySelector('[data-pte-prop="color"]').addEventListener("change", (e) => {
        const b = st.text.blocks[st.sel]; const hex = el.querySelector('[data-pte-prop="hex"]');
        b.color = e.target.value === "own" ? hex.value : e.target.value; hex.hidden = e.target.value !== "own"; changed(); paintStage();
      });
      el.querySelector('[data-pte-prop="hex"]').addEventListener("input", (e) => { st.text.blocks[st.sel].color = e.target.value; changed(); paintStage(); });
      el.querySelector("[data-pte-halo]").addEventListener("change", (e) => { st.text.halo = e.target.value; changed(); paintStage(); });
      el.querySelector(".pte-find").addEventListener("input", (e) => { st.filter = e.target.value; paintPicker(); });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !e.defaultPrevented) { e.preventDefault(); e.stopPropagation(); cancel(); }
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); save(); }
      });
    }
    function onClick(e) {
      const t = e.target.closest("button");
      if (!t || t.disabled) return;
      const d = t.dataset;
      if (d.pteMode) {
        if (d.pteMode === "free") { if (st.text.mode !== "free") { toFree(); changed(); } }
        else if (st.text.mode !== "flow") { st.text.mode = "flow"; st.text.blocks.forEach((b) => { b.x = 0; b.y = 0; b.w = 100; }); changed(); }
        paintAll(); return;
      }
      if (d.pteWidth) { st.width = +d.pteWidth; paintStage(); return; }
      if (d.pteSel != null) { st.sel = +d.pteSel; paintAll(); return; }
      if (d.pteAlign) { st.text.blocks[st.sel].align = d.pteAlign; changed(); paintStage(); paintProps(); return; }
      if (d.pteItalic != null) { const b = st.text.blocks[st.sel]; b.italic = !b.italic; changed(); paintStage(); paintProps(); return; }
      if (d.pteIns) { insert(t.dataset.kind === "list" ? listSnippet(d.pteIns) : "{" + d.pteIns + "}"); return; }
      if (d.pteFilter) { insert("|" + d.pteFilter, { filter: true }); return; }
      const act = d.pteAct;
      if (!act) return;
      const B = st.text.blocks;
      if (act === "add" && B.length < 12) {
        const last = B[B.length - 1] || {};
        B.push({ t: tr("Текст {value}", "Text {value}"), x: st.text.mode === "free" ? 5 : 0, y: st.text.mode === "free" ? clamp((last.y || 0) + 25, 0, 80) : 0, w: st.text.mode === "free" ? 60 : 100, align: "left", size: 100, weight: 500, color: "", italic: false, font: "" });
        st.sel = B.length - 1; changed(); paintAll(); ta().focus(); ta().select(); return;
      }
      if (act === "dup" && B.length < 12) { const c = { ...B[st.sel] }; if (st.text.mode === "free") c.y = clamp(c.y + 12, 0, 95); B.splice(st.sel + 1, 0, c); st.sel++; changed(); paintAll(); return; }
      if (act === "del" && B.length > 1) { B.splice(st.sel, 1); st.sel = Math.min(st.sel, B.length - 1); changed(); paintAll(); return; }
      if (act === "up" && st.sel > 0) { [B[st.sel - 1], B[st.sel]] = [B[st.sel], B[st.sel - 1]]; st.sel--; changed(); paintAll(); return; }
      if (act === "down" && st.sel < B.length - 1) { [B[st.sel + 1], B[st.sel]] = [B[st.sel], B[st.sel + 1]]; st.sel++; changed(); paintAll(); return; }
      if (act === "auto") { st.text = fresh(); st.sel = 0; st.auto = true; st.dirty = true; paintAll(); return; }
      if (act === "cancel") { cancel(); return; }
      if (act === "save") { save(); }
    }
    async function save() {
      if (st.busy) return;
      st.busy = true;
      const btn = el.querySelector('[data-pte-act="save"]');
      if (btn) btn.disabled = true;
      try {
        const clean = st.auto ? null : { mode: st.text.mode, h: st.text.h, halo: st.text.halo, blocks: st.text.blocks.filter((b) => String(b.t || "").trim()) };
        if (opts.onSave) await opts.onSave(clean && clean.blocks.length ? clean : null);
        st.dirty = false;
      } finally { st.busy = false; if (btn && btn.isConnected) btn.disabled = false; }
    }
    function cancel() { if (opts.onCancel) opts.onCancel(); }

    frame();
    paintAll();
    paintPicker();
    return {
      el,
      destroy() { host.innerHTML = ""; },
      refresh(p) { if (p) panel = p; paintStage(); paintPicker(); },
      state: () => st,
    };
  }

  window.MemuPanelTextEditor = { mount };
})();
