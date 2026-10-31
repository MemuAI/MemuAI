

(function () {
  "use strict";
  const MODES = [["changes", "Только важное", "Important only"], ["every", "Любые изменения", "Every change"], ["digest", "Сводка раз в день", "Daily digest"]];
  const KIND_ICON = { weather: "🌤", news: "📰", rss: "📰", countdown: "⏳", todo: "✅", currency: "💱", crypto: "🪙", stock: "📈",
    sports: "🏆", calendar: "📅", github: "🐙", notes: "📝", system: "🖥", usage: "📊", clock: "🕑", timers: "⏱", proxy: "🛰",
    custom: "✨", web: "🌐" };
  let H = null;
  let data = null;
  let open = "";
  let dragId = "";
  const ORDER_KEY = "memu_tg_panels_order";
  function loadOrder() { try { const v = JSON.parse(localStorage.getItem(ORDER_KEY) || "[]"); return Array.isArray(v) ? v.filter((x) => typeof x === "string") : []; } catch (e) { return []; } }
  function saveOrder(ids) { try { localStorage.setItem(ORDER_KEY, JSON.stringify(ids)); } catch (e) {  } }
  function applyOrder(panels) {
    const order = loadOrder(); if (!order.length) return (panels || []).slice();
    const rank = new Map(order.map((id, i) => [id, i]));
    return (panels || []).slice().sort((a, b) => (rank.has(a.id) ? rank.get(a.id) : 1e9) - (rank.has(b.id) ? rank.get(b.id) : 1e9));
  }
  function zoneParts(zone) { const [tz, label] = String(zone || "").split("|"); return { tz: tz.trim(), label: (label || "").trim() }; }
  function fmtClock(tz, seconds, now) {
    const d = now || new Date();
    const opt = { hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
    if (seconds !== false) opt.second = "2-digit";
    if (tz) opt.timeZone = tz;
    try { return d.toLocaleTimeString(undefined, opt); }
    catch (e) { try { return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }); } catch (e2) { return "--:--"; } }
  }
  function fmtClockDate(tz, now) {
    const d = now || new Date();
    const opt = { weekday: "short", day: "numeric", month: "short" };
    if (tz) opt.timeZone = tz;
    try { return d.toLocaleDateString(undefined, opt); } catch (e) { return ""; }
  }
  function clockHtml(row, esc) {
    const c = row.card || {};
    const raw = (c.clock && typeof c.clock === "object") ? c.clock : {};
    const zones = (Array.isArray(raw.zones) ? raw.zones : []).map((z) => typeof z === "string" ? zoneParts(z) : { tz: String((z && z.tz) || ""), label: String((z && z.label) || "") }).filter((z) => z.tz);
    const showDate = raw.date !== false;
    return `<div class="pn-clock" data-clock="${esc(row.id)}"><b class="pn-clock-time">--:--</b>${showDate ? `<small class="pn-clock-date"></small>` : ""}${zones.length ? `<div class="pn-clock-zones">${zones.map((z) => `<div class="pn-clock-row"><span class="pn-clock-city">${esc(z.label || z.tz.split("/").pop().replace(/_/g, " "))}</span><b class="pn-clock-zone" data-tz="${esc(z.tz)}">--:--</b></div>`).join("")}</div>` : ""}</div>`;
  }
  function tickClock(view) {
    if (!view || !view.isConnected) return;
    const now = new Date();
    view.querySelectorAll(".pn-clock").forEach((el) => {
      const timeEl = el.querySelector(".pn-clock-time"); if (timeEl) timeEl.textContent = fmtClock("", true, now);
      const dateEl = el.querySelector(".pn-clock-date"); if (dateEl) dateEl.textContent = fmtClockDate("", now);
      el.querySelectorAll(".pn-clock-zone").forEach((z) => { z.textContent = fmtClock(z.dataset.tz, true, now); });
    });
  }
  function emptyNote(row, tr) {
    if (row.kind === "timers") return tr("Таймеры и помодоро живут на компьютере — откройте панель в MemuAI.", "Timers and Pomodoro live on the computer — open the panel in MemuAI.");
    if (row.kind === "module") return tr("Эта панель показывает модуль MemuAI — откройте её на компьютере.", "This panel shows a MemuAI module — open it on the computer.");
    if (row.kind === "countdown") return tr("Укажите дату в настройках панели на компьютере.", "Set the date in the panel settings on the computer.");
    return tr("Нет данных", "No data yet");
  }
  function movePanel(view, id, dir) {
    if (!data || !Array.isArray(data.panels)) return;
    const i = data.panels.findIndex((p) => p.id === id); const j = i + dir;
    if (i < 0 || j < 0 || j >= data.panels.length) return;
    const [moved] = data.panels.splice(i, 1); data.panels.splice(j, 0, moved);
    saveOrder(data.panels.map((p) => p.id));
    H.haptic("select"); draw(view);
  }

  function when(ts) {
    if (!ts) return "";
    const d = new Date(Number(ts) * 1000);
    if (isNaN(d)) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function safeUrl(url) { return /^https?:\/\//i.test(String(url || "")) ? String(url) : ""; }

  function cardHtml(row, index, all) {
    const esc = H.esc, tr = H.tr, c = row.card || {};
    const parts = [];
    if (row.kind === "clock") parts.push(clockHtml(row, esc));
    if (c.metric && c.metric.value) {
      const arrow = c.metric.trend === "up" ? "▲" : c.metric.trend === "down" ? "▼" : "";
      parts.push(`<div class="pn-metric"><b>${esc(c.metric.value)}</b><span>${esc(c.metric.unit || "")}</span>${c.metric.delta ? `<em class="pn-${esc(c.metric.trend || "flat")}">${arrow}${esc(c.metric.delta)}</em>` : ""}</div>`);
      if (c.metric.caption) parts.push(`<div class="pn-cap">${esc(c.metric.caption)}</div>`);
    }
    if (c.countdown && c.countdown.days_left != null) {
      parts.push(`<div class="pn-metric"><b>${esc(c.countdown.days_left)}</b><span>${esc(tr("дн.", "days"))}</span></div>${c.countdown.label && !String(c.subtitle || "").includes(c.countdown.label) ? `<div class="pn-cap">${esc(c.countdown.label)}</div>` : ""}`);
    }

    if (c.image && safeUrl(c.image.src)) parts.push(`<img class="pn-img" src="${esc(safeUrl(c.image.src))}" alt="${esc(c.image.caption || "")}" loading="lazy" referrerpolicy="no-referrer" style="display:block;width:100%;max-height:240px;object-fit:cover;border-radius:10px;margin:6px 0">`);
    if ((c.stats || []).length) parts.push(`<div class="pn-stats">${c.stats.slice(0, 8).map((s) => `<div><small>${esc(s.label || "")}</small><b>${esc(s.value || "")}</b></div>`).join("")}</div>`);
    if ((c.items || []).length) parts.push(`<ul class="pn-items">${c.items.slice(0, 8).map((it) => { const u = safeUrl(it.url); const t = esc(it.title); return `<li>${u ? `<a href="${esc(u)}" data-link="${esc(u)}">${t}</a>` : t}${it.subtitle ? `<small>${esc(it.subtitle)}</small>` : ""}</li>`; }).join("")}</ul>`);
    if (row.kind === "todo") {
      const tasks = c.tasks || [];
      parts.push(tasks.length ? `<ul class="pn-tasks">${tasks.slice(0, 12).map((t) => `<li><i></i>${esc(t.title)}${t.due ? `<small>${esc(String(t.due).replace("T", " "))}</small>` : ""}</li>`).join("")}</ul>`
        : `<div class="pn-cap">${esc(tr("Открытых задач нет", "No open tasks"))}</div>`);
    }
    (c.text || []).slice(0, 2).forEach((t) => parts.push(`<p class="pn-text">${esc(String(t).slice(0, 600))}</p>`));
    if (c.error) parts.push(`<div class="pn-warn">⚠️ ${esc(String(c.error).slice(0, 200))}</div>`);
    const sub = row.sub || {};
    const bell = row.eligible ? `<button class="pn-bell ${sub.on ? "on" : ""}" data-bell="${esc(row.id)}" aria-label="${esc(tr("Уведомления в Telegram", "Telegram updates"))}">${sub.on ? "🔔" : "🔕"}</button>` : "";
    const opts = row.eligible && sub.on && open === row.id ? optionsHtml(row) : "";
    const idx = typeof index === "number" ? index : -1;
    const total = Array.isArray(all) ? all.length : -1;
    const drag = `<button type="button" class="pn-drag" draggable="true" data-drag="${esc(row.id)}" aria-label="${esc(tr("Перетащить панель", "Drag the panel"))}" title="${esc(tr("Перетащить", "Drag"))}">⠿</button>`;
    const moves = `<span class="pn-moves"><button type="button" data-move="-1" data-id="${esc(row.id)}" aria-label="${esc(tr("Выше", "Up"))}" ${idx === 0 ? "disabled" : ""}>↑</button><button type="button" data-move="1" data-id="${esc(row.id)}" aria-label="${esc(tr("Ниже", "Down"))}" ${idx >= 0 && idx === total - 1 ? "disabled" : ""}>↓</button></span>`;
    return `<section class="pn-card" data-panel="${esc(row.id)}">
      <header>${drag}<span class="pn-ico">${KIND_ICON[row.kind] || "▫️"}</span><div class="pn-head"><h3>${esc(row.title || row.kind)}</h3>${c.subtitle ? `<small>${esc(c.subtitle)}</small>` : ""}</div>${bell}${moves}</header>
      ${parts.join("") || `<div class="pn-cap">${esc(row.status === "building" ? tr("Собирается…", "Building…") : emptyNote(row, tr))}</div>`}
      <footer><span>${row.refreshing ? esc(tr("Обновляется…", "Refreshing…")) : (c.updated ? esc(tr("Обновлено ", "Updated ") + when(c.updated)) : "")}</span>
        ${row.local ? "" : `<button class="btn small secondary" data-refresh="${esc(row.id)}">${esc(tr("Обновить", "Refresh"))}</button>`}
        ${row.eligible && sub.on ? `<button class="btn small secondary" data-opts="${esc(row.id)}">${esc(open === row.id ? tr("Скрыть", "Hide") : tr("Что присылать", "What to send"))}</button>` : ""}</footer>
      ${opts}
    </section>`;
  }

  function optionsHtml(row) {
    const esc = H.esc, tr = H.tr, sub = row.sub || {};
    return `<div class="pn-opts">
      <div class="pn-seg">${MODES.map(([id, ru, en]) => `<button class="${sub.mode === id ? "on" : ""}" data-mode="${id}" data-id="${esc(row.id)}">${esc(tr(ru, en))}</button>`).join("")}</div>
      ${sub.mode === "digest" ? `<label class="pn-row">${esc(tr("Время сводки", "Digest time"))}<input type="time" data-at="${esc(row.id)}" value="${esc(sub.at || (data.settings || {}).digest_at || "09:00")}"></label>` : ""}
      <label class="pn-row">${esc(tr("В тихие часы бота", "During the bot's quiet hours"))}
        <select data-quiet="${esc(row.id)}"><option value="silent" ${sub.quiet !== "defer" ? "selected" : ""}>${esc(tr("присылать без звука", "send silently"))}</option><option value="defer" ${sub.quiet === "defer" ? "selected" : ""}>${esc(tr("отложить до утра", "postpone"))}</option></select></label>
      <p class="pn-note">${esc(tr("Повторы не приходят; частые изменения собираются в одно сообщение (не чаще раза в " + ((data.settings || {}).min_gap_minutes || 30) + " мин).",
        "No duplicates; frequent changes are merged into one message (at most every " + ((data.settings || {}).min_gap_minutes || 30) + " min)."))}</p>
    </div>`;
  }

  function draw(view) {
    const box = view.querySelector("#pn-list"); if (!box || !data || !Array.isArray(data.panels)) return;
    const esc = H.esc, tr = H.tr;
    if (!data.panels.length) {
      box.innerHTML = `<div class="empty"><div class="empty-art">${H.icon("target")}</div><h3>${esc(tr("Панелей нет", "No panels"))}</h3><p>${esc(tr("Добавьте панели в MemuAI на компьютере — они появятся здесь.", "Add panels in MemuAI on the computer — they show up here."))}</p></div>`;
      return;
    }
    const note = data.telegram_ready ? "" : `<p class="section-note pn-hint">${esc(tr("Уведомления придут, когда бот включён и привязан.", "Updates arrive once the bot is on and paired."))}</p>`;
    box.innerHTML = note + `<div class="pn-grid">${data.panels.map(cardHtml).join("")}</div>`;
    box.querySelectorAll("img.pn-img").forEach((img) => { img.onerror = () => img.remove(); });
    box.querySelectorAll("[data-link]").forEach((a) => a.onclick = (e) => {
      e.preventDefault();
      const tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.openLink) tg.openLink(a.dataset.link); else window.open(a.dataset.link, "_blank", "noopener");
    });
    box.querySelectorAll("[data-bell]").forEach((b) => b.onclick = () => change(view, b.dataset.bell, { on: !b.classList.contains("on") }));
    box.querySelectorAll("[data-opts]").forEach((b) => b.onclick = () => { open = open === b.dataset.opts ? "" : b.dataset.opts; draw(view); });
    box.querySelectorAll("[data-mode]").forEach((b) => b.onclick = () => change(view, b.dataset.id, { mode: b.dataset.mode }));
    box.querySelectorAll("[data-at]").forEach((i) => i.onchange = () => change(view, i.dataset.at, { at: i.value }));
    box.querySelectorAll("[data-quiet]").forEach((s) => s.onchange = () => change(view, s.dataset.quiet, { quiet: s.value }));
    box.querySelectorAll("[data-refresh]").forEach((b) => b.onclick = async () => {
      H.haptic("light"); b.disabled = true;
      try {
        const r = await H.api("panels.refresh", { id: b.dataset.refresh });
        H.toast(r.result === "queued" ? H.tr("Обновляю — MemuAI на компьютере обновит панель за ~20 с", "Refreshing — MemuAI on the computer updates it in ~20 s") : H.tr("Обновлять нечего", "Nothing to refresh"), "ok");
        setTimeout(() => load(view, true), 20000);
      } catch (e) { H.toast(e.message, "err"); }
      finally { b.disabled = false; }
    });
    box.querySelectorAll("[data-move]").forEach((b) => b.onclick = (e) => { e.preventDefault(); movePanel(view, b.dataset.id, +b.dataset.move); });
    const clearDrop = () => { box.querySelectorAll(".pn-drop-before, .pn-drop-after").forEach((el) => el.classList.remove("pn-drop-before", "pn-drop-after")); };
    box.querySelectorAll("[data-drag]").forEach((h) => h.addEventListener("dragstart", (e) => {
      dragId = h.dataset.drag;
      try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", dragId); } catch (err) {  }
      const card = h.closest(".pn-card"); if (card) card.classList.add("pn-dragging");
    }));
    box.querySelectorAll("[data-drag]").forEach((h) => h.addEventListener("dragend", () => { dragId = ""; clearDrop(); box.querySelectorAll(".pn-dragging").forEach((el) => el.classList.remove("pn-dragging")); }));
    // Touch drag: HTML5 drag-and-drop never fires on touch screens, so the
    // handle also reorders with pointer events (mouse keeps the DnD path
    // above). A short tap does nothing; a drag past 8 px moves the card.
    box.querySelectorAll("[data-drag]").forEach((h) => h.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" || (e.button != null && e.button !== 0)) return;
      e.preventDefault();
      const id = h.dataset.drag; const card = h.closest(".pn-card"); if (!card) return;
      const startY = e.clientY; let active = false, target = null, before = true;
      try { h.setPointerCapture(e.pointerId); } catch (err) {  }
      const move = (ev) => {
        if (!active && Math.abs(ev.clientY - startY) < 8) return;
        if (!active) { active = true; dragId = id; card.classList.add("pn-dragging"); }
        clearDrop(); target = null;
        let el = null; try { el = document.elementFromPoint(ev.clientX, ev.clientY); } catch (err) {  }
        const over = el && el.closest ? el.closest(".pn-card") : null;
        if (over && over.dataset.panel !== id) { const r = over.getBoundingClientRect(); before = ev.clientY < r.top + r.height / 2; over.classList.add(before ? "pn-drop-before" : "pn-drop-after"); target = over.dataset.panel; }
      };
      const up = () => {
        h.removeEventListener("pointermove", move); h.removeEventListener("pointerup", up); h.removeEventListener("pointercancel", up);
        if (!active) { dragId = ""; return; }
        card.classList.remove("pn-dragging"); clearDrop();
        const tid = target; dragId = "";
        if (tid && data && Array.isArray(data.panels)) {
          const from = data.panels.findIndex((x) => x.id === id);
          if (from >= 0) { const [moved] = data.panels.splice(from, 1); let at = data.panels.findIndex((x) => x.id === tid); if (!before) at += 1; data.panels.splice(at, 0, moved); saveOrder(data.panels.map((x) => x.id)); H.haptic("select"); draw(view); }
        }
      };
      h.addEventListener("pointermove", move); h.addEventListener("pointerup", up); h.addEventListener("pointercancel", up);
    }));
    let dragOver = null;
    box.querySelectorAll(".pn-card").forEach((card) => {
      card.addEventListener("dragover", (e) => { if (!dragId || card.dataset.panel === dragId) return; e.preventDefault(); const r = card.getBoundingClientRect(); const before = e.clientY < r.top + r.height / 2; clearDrop(); card.classList.add(before ? "pn-drop-before" : "pn-drop-after"); dragOver = { id: card.dataset.panel, before }; try { e.dataTransfer.dropEffect = "move"; } catch (err) {  } });
      card.addEventListener("dragleave", () => { card.classList.remove("pn-drop-before", "pn-drop-after"); });
      card.addEventListener("drop", (e) => { e.preventDefault(); if (!dragId || !data || !Array.isArray(data.panels)) return; const target = dragOver && dragOver.id === card.dataset.panel ? dragOver : { id: card.dataset.panel, before: true }; const from = data.panels.findIndex((p) => p.id === dragId); if (from < 0) return; const [moved] = data.panels.splice(from, 1); let at = data.panels.findIndex((p) => p.id === target.id); if (!target.before) at += 1; data.panels.splice(at, 0, moved); dragId = ""; dragOver = null; saveOrder(data.panels.map((p) => p.id)); draw(view); });
    });
    tickClock(view);
  }

  async function change(view, id, patch) {
    H.haptic("select");
    if (!data || !Array.isArray(data.panels)) { try { await load(view); } catch (e) {  } return; }
    try {
      const r = await H.api("panels.subscribe", Object.assign({ id }, patch));
      const row = data.panels.find((p) => p.id === id); if (row) row.sub = r.sub;
      if (patch.on === true) H.toast(H.tr("Буду присылать обновления в Telegram", "Updates will arrive in Telegram"), "ok");
      if (patch.on === false && open === id) open = "";
      draw(view);
    } catch (e) { H.toast(e.message, "err"); }
  }

  async function load(view, quiet) {
    try {
      const d = await H.api("panels.list");
      data = { settings: {}, panels: [] };
      if (d && typeof d === "object") { if (d.settings && typeof d.settings === "object") data.settings = d.settings; if (Array.isArray(d.panels)) data.panels = applyOrder(d.panels); if (typeof d.telegram_ready === "boolean") data.telegram_ready = d.telegram_ready; }
      draw(view);
    } catch (e) {
      if (!quiet) { const box = view.querySelector("#pn-list"); if (box) { box.innerHTML = `<div class="empty"><p>${H.esc(e.message)}</p><button type="button" data-pn-retry>${H.esc(H.tr("Повторить", "Retry"))}</button></div>`; const rb = box.querySelector("[data-pn-retry]"); if (rb) rb.onclick = () => load(view); } }
    }
  }

  function render(view, helpers) {
    H = helpers;
    H.setTop(H.tr("Панели", "Panels"));
    view.innerHTML = `<div id="pn-list"><div class="empty"><div class="typing"><i></i><i></i><i></i></div></div></div>`;
    load(view);

    H.timers.panels = setInterval(() => { if (!document.hidden && H.state.tab === "panels" && !H.state.stack.length) load(view, true); }, 60000);
    if (H.timers) { clearInterval(H.timers.pnClock); H.timers.pnClock = setInterval(() => { if (!document.hidden && view.isConnected) tickClock(view); }, 1000); }
  }

  window.MemuTgPanels = { render, cardHtml: (row, helpers) => { H = helpers || H; data = data || { settings: {} }; return cardHtml(row); }, formatClock: (tz, seconds, now) => fmtClock(tz, seconds, now), formatClockDate: (tz, now) => fmtClockDate(tz, now), applyOrder, loadOrder };
})();
