/* MemuAI Mini App — «Панели» (Round 24).
 * Cards come from the server already resolved (telegram_panels.card: the
 * panel's spec blocks with its data substituted), so this file only draws
 * them. Per-panel Telegram updates: on/off, what to send, quiet hours.
 * Loaded before app.js; app.js calls MemuTgPanels.render(view, helpers). */
(function () {
  "use strict";
  const MODES = [["changes", "Только важное", "Important only"], ["every", "Любые изменения", "Every change"], ["digest", "Сводка раз в день", "Daily digest"]];
  const KIND_ICON = { weather: "🌤", news: "📰", rss: "📰", countdown: "⏳", todo: "✅", currency: "💱", crypto: "🪙", stock: "📈",
    sports: "🏆", calendar: "📅", github: "🐙", notes: "📝", system: "🖥", usage: "📊", clock: "🕑", timers: "⏱", proxy: "🛰",
    custom: "✨", web: "🌐" };   // Round 31: AI / page panels had a bare «▪️»
  let H = null;
  let data = null;
  let open = "";

  function when(ts) {
    if (!ts) return "";
    const d = new Date(Number(ts) * 1000);
    if (isNaN(d)) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  function safeUrl(url) { return /^https?:\/\//i.test(String(url || "")) ? String(url) : ""; }

  function cardHtml(row) {
    const esc = H.esc, tr = H.tr, c = row.card || {};
    const parts = [];
    if (c.metric && c.metric.value) {
      const arrow = c.metric.trend === "up" ? "▲" : c.metric.trend === "down" ? "▼" : "";
      parts.push(`<div class="pn-metric"><b>${esc(c.metric.value)}</b><span>${esc(c.metric.unit || "")}</span>${c.metric.delta ? `<em class="pn-${esc(c.metric.trend || "flat")}">${arrow}${esc(c.metric.delta)}</em>` : ""}</div>`);
      if (c.metric.caption) parts.push(`<div class="pn-cap">${esc(c.metric.caption)}</div>`);
    }
    if (c.countdown && c.countdown.days_left != null) {
      parts.push(`<div class="pn-metric"><b>${esc(c.countdown.days_left)}</b><span>${esc(tr("дн.", "days"))}</span></div>${c.countdown.label && !String(c.subtitle || "").includes(c.countdown.label) ? `<div class="pn-cap">${esc(c.countdown.label)}</div>` : ""}`);
    }
    // Round 31: an image block (photo of the day, a webcam) — the card was «No data yet»
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
    return `<section class="pn-card" data-panel="${esc(row.id)}">
      <header><span class="pn-ico">${KIND_ICON[row.kind] || "▫️"}</span><div class="pn-head"><h3>${esc(row.title || row.kind)}</h3>${c.subtitle ? `<small>${esc(c.subtitle)}</small>` : ""}</div>${bell}</header>
      ${parts.join("") || `<div class="pn-cap">${esc(row.status === "building" ? tr("Собирается…", "Building…") : tr("Нет данных", "No data yet"))}</div>`}
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
    const box = view.querySelector("#pn-list"); if (!box || !data) return;
    const esc = H.esc, tr = H.tr;
    if (!data.panels.length) {
      box.innerHTML = `<div class="empty"><div class="empty-art">${H.icon("target")}</div><h3>${esc(tr("Панелей нет", "No panels"))}</h3><p>${esc(tr("Добавьте панели в MemuAI на компьютере — они появятся здесь.", "Add panels in MemuAI on the computer — they show up here."))}</p></div>`;
      return;
    }
    const note = data.telegram_ready ? "" : `<p class="section-note pn-hint">${esc(tr("Уведомления придут, когда бот включён и привязан.", "Updates arrive once the bot is on and paired."))}</p>`;
    box.innerHTML = note + `<div class="pn-grid">${data.panels.map(cardHtml).join("")}</div>`;
    box.querySelectorAll("img.pn-img").forEach((img) => { img.onerror = () => img.remove(); });   // no inline handlers (CSP)
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
  }

  async function change(view, id, patch) {
    H.haptic("select");
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
      data = await H.api("panels.list");
      draw(view);
    } catch (e) {
      if (!quiet) { const box = view.querySelector("#pn-list"); if (box) box.innerHTML = `<div class="empty"><p>${H.esc(e.message)}</p></div>`; }
    }
  }

  function render(view, helpers) {
    H = helpers;
    H.setTop(H.tr("Панели", "Panels"));
    view.innerHTML = `<div id="pn-list"><div class="empty"><div class="typing"><i></i><i></i><i></i></div></div></div>`;
    load(view);
    // Light auto-refresh while the tab is open (panels change at most every few minutes).
    H.timers.panels = setInterval(() => { if (!document.hidden && H.state.tab === "panels" && !H.state.stack.length) load(view, true); }, 60000);
  }

  window.MemuTgPanels = { render, cardHtml: (row, helpers) => { H = helpers || H; data = data || { settings: {} }; return cardHtml(row); } };
})();
