/* MemuAI Proxy — Round 35 (quota): вкладка «Планировщик квот» + значок прогноза в карточках аккаунтов.
   Данные — GET /api/admin/quota/planner (app/quota_planner.py): по каждому аккаунту окна лимитов с историей
   остатка, прогнозом до сброса (линия + полоса 10–90 %), вердиктом, ETA и рекомендуемым темпом. */
"use strict";

window.MemuQuota = (() => {
  const L = (ru, en) => (LANG === "en" ? en : ru);
  const S = { view: null, loading: false, busy: false, badges: null, badgesAt: 0, timer: 0 };
  const loc = () => (LANG === "en" ? "en-US" : "ru-RU");
  const errorOf = (error) => (error && error.message ? error.message : String(error || ""));
  const ST = {
    ok: { ru: "Хватит с запасом", en: "Plenty left", chip: ["Хватит", "Enough"], cls: "ok", ico: "✓" },
    tight: { ru: "Впритык", en: "Cutting it close", chip: ["Впритык", "Tight"], cls: "warn", ico: "◐" },
    runs_out: { ru: "Не хватит до сброса", en: "Runs out before the reset", chip: ["Не хватит", "Runs out"], cls: "err", ico: "⏳" },
    exhausted: { ru: "Лимит исчерпан", en: "Limit used up", chip: ["Исчерпан", "Used up"], cls: "err", ico: "■" },
    insufficient: { ru: "Мало данных", en: "Not enough data", chip: ["Мало данных", "Little data"], cls: "muted", ico: "…" },
    no_reset: { ru: "Сброс неизвестен", en: "Reset time unknown", chip: ["Без сброса", "No reset"], cls: "info", ico: "?" },
  };
  const stOf = (s) => ST[s] || ST.insufficient;

  /* ------------------------------------------------------------ formatting */
  function span(seconds) {
    let s = Math.max(0, Math.round(seconds));
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    if (d) return h ? L(`${d} д ${h} ч`, `${d} d ${h} h`) : L(`${d} д`, `${d} d`);
    if (h) return m ? L(`${h} ч ${m} мин`, `${h} h ${m} min`) : L(`${h} ч`, `${h} h`);
    return L(`${Math.max(1, m)} мин`, `${Math.max(1, m)} min`);
  }
  function clock(ts, now) {
    if (!ts) return "—";
    const d = new Date(ts * 1000), n = new Date((now || Date.now() / 1000) * 1000);
    const hm = d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === n.toDateString()) return hm;
    const tomorrow = new Date(n); tomorrow.setDate(n.getDate() + 1);
    if (d.toDateString() === tomorrow.toDateString()) return L(`завтра ${hm}`, `tomorrow ${hm}`);
    return `${d.toLocaleDateString(loc(), { weekday: "short", day: "numeric", month: "short" })} ${hm}`;
  }
  const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
  const num = (v, digits = 0) => Number(v || 0).toLocaleString(loc(), { maximumFractionDigits: digits });
  const winTitle = (w) => (LANG === "en" ? (w.title_en || w.title) : (w.title || w.title_en)) || "—";
  const unitOf = (w) => (LANG === "en" ? (w.unit_en || w.unit) : (w.unit || w.unit_en)) || "";
  function accTitle(a) {
    const meta = typeof providerMeta === "function" ? providerMeta(a.provider) : { title: a.provider, icon: "◆" };
    return { name: a.label || meta.title, provider: meta.title, icon: meta.icon || "◆" };
  }

  /* ------------------------------------------------------------ verdict text */
  function verdict(w, now) {
    const reset = w.reset;
    const resetTxt = reset ? `${clock(reset, now)}${w.reset_estimated ? L(" (оценка)", " (estimated)") : ""}` : "";
    switch (w.status) {
      case "exhausted":
        return reset ? L(`Лимит исчерпан — сброс в ${resetTxt}, через ${span(reset - now)}`, `Limit used up — resets at ${resetTxt}, in ${span(reset - now)}`)
          : L("Лимит исчерпан; срок сброса провайдер не сообщил", "Limit used up; the provider did not say when it resets");
      case "runs_out": {
        const early = w.eta_early && w.eta_late && Math.abs(w.eta_late - w.eta_early) > 300
          ? L(` (вероятно ${clock(w.eta_early, now)}–${clock(w.eta_late, now)})`, ` (likely ${clock(w.eta_early, now)}–${clock(w.eta_late, now)})`) : "";
        return L(`Закончится примерно в ${clock(w.eta, now)}${early} — за ${span(reset - w.eta)} до сброса`,
          `Runs out at about ${clock(w.eta, now)}${early} — ${span(reset - w.eta)} before the reset`);
      }
      case "tight":
        return L(`Впритык: к сбросу останется ~${pct(w.rem_at_reset)} (от ${pct(w.rem_at_reset_lo)} до ${pct(w.rem_at_reset_hi)})`,
          `Cutting it close: ~${pct(w.rem_at_reset)} left at the reset (${pct(w.rem_at_reset_lo)}–${pct(w.rem_at_reset_hi)})`);
      case "ok":
        return L(`Хватит с запасом: к сбросу останется ~${pct(w.rem_at_reset)} (от ${pct(w.rem_at_reset_lo)} до ${pct(w.rem_at_reset_hi)})`,
          `Plenty left: ~${pct(w.rem_at_reset)} at the reset (${pct(w.rem_at_reset_lo)}–${pct(w.rem_at_reset_hi)})`);
      case "no_reset":
        return w.eta ? L(`Срок сброса неизвестен; при таком темпе закончится примерно в ${clock(w.eta, now)}`, `Reset time unknown; at this pace it runs out at about ${clock(w.eta, now)}`)
          : L("Срок сброса неизвестен; за ближайшие сутки при таком темпе не закончится", "Reset time unknown; at this pace it lasts the next day");
      default:
        return w.points < 3 ? L(`Мало данных: замеров ${w.points}, нужно хотя бы 3 с промежутком — прогноз появится сам`, `Not enough data: ${w.points} sample(s), at least 3 spread over time are needed — the forecast appears by itself`)
          : L("Мало данных: замеры слишком близко друг к другу — прогноз появится через несколько опросов", "Not enough data: the samples are too close together — the forecast appears after a few more polls");
    }
  }

  function paceLine(w) {
    const bits = [];
    // Round 36: with too few samples «now ~41 %/h» was an extrapolation of two readings seconds apart
    const steady = w.status !== "insufficient";
    if (steady && w.rate_now != null) bits.push(L(`сейчас ~${num(w.rate_now)} запр/ч`, `now ~${num(w.rate_now)} req/h`));
    else if (steady && w.rate != null) bits.push(L(`сейчас ~${num(w.rate * 100, 1)} %/ч`, `now ~${num(w.rate * 100, 1)} %/h`));
    let rec = "";
    // «можно до» when there is plenty, «не больше» when the limit is at risk or still unknown
    const free = w.status === "ok";
    const lead = free ? L("можно до", "up to") : L("не больше", "no more than");
    if (w.status === "exhausted") rec = "";
    else if (w.pace_req != null) rec = L(`${lead} ~${num(w.pace_req)} запросов/час`, `${lead} ~${num(w.pace_req)} requests/hour`);
    else if (w.pace_units != null) rec = L(`${lead} ~${num(w.pace_units)} ${unitOf(w)}/час`, `${lead} ~${num(w.pace_units)} ${unitOf(w)}/hour`);
    else if (w.pace_pct != null) rec = L(`${lead} ~${num(w.pace_pct, 1)} % лимита в час`, `${lead} ~${num(w.pace_pct, 1)}% of the limit per hour`);
    if (rec) bits.push(`<b>${esc(rec)}</b>`);
    if (!bits.length) return "";
    return `<div class="qp-pace"><span class="qp-pace-lbl">${esc(L("Темп", "Pace"))}</span>${bits.map((b) => (b.startsWith("<b>") ? b : esc(b))).join(" · ")}</div>`;
  }

  /* ------------------------------------------------------------ chart */
  function chart(w, now) {
    const W = S.chartW || 560, H = W < 400 ? 120 : 132, P = { l: 34, r: 12, t: 12, b: 20 };  // W ≈ real px: text stays 1:1
    const hist = w.history || [], proj = w.projection || [];
    if (!hist.length) return `<div class="qp-chart qp-chart-empty">${esc(L("Замеров ещё нет", "No samples yet"))}</div>`;
    const x0 = Math.min(w.view_from || hist[0][0], hist[0][0]);
    let x1 = Math.max(now, hist[hist.length - 1][0]);
    if (proj.length) x1 = Math.max(x1, proj[proj.length - 1][0]);
    if (w.reset) x1 = Math.max(x1, w.reset);
    if (x1 - x0 < 600) x1 = x0 + 600;
    const X = (t) => P.l + (t - x0) / (x1 - x0) * (W - P.l - P.r);
    const Y = (v) => P.t + (1 - Math.max(0, Math.min(1, v))) * (H - P.t - P.b);
    const line = (pts, i) => pts.map((p, k) => `${k ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[i]).toFixed(1)}`).join("");
    const grid = [0, 0.5, 1].map((v) => `<line class="qp-gl" x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text class="qp-axis" x="${P.l - 6}" y="${Y(v) + 3.5}" text-anchor="end">${v * 100}%</text>`).join("");
    const area = `${line(hist, 1)}L${X(hist[hist.length - 1][0]).toFixed(1)},${Y(0)}L${X(hist[0][0]).toFixed(1)},${Y(0)}Z`;
    let band = "", projLine = "";
    if (proj.length > 1) {
      const top = proj.map((p) => `${X(p[0]).toFixed(1)},${Y(p[3]).toFixed(1)}`);
      const bottom = proj.slice().reverse().map((p) => `${X(p[0]).toFixed(1)},${Y(p[2]).toFixed(1)}`);
      band = `<polygon class="qp-band qp-${stOf(w.status).cls}" points="${top.concat(bottom).join(" ")}"/>`;
      projLine = `<path class="qp-proj qp-${stOf(w.status).cls}" d="${line(proj, 1)}"/>`;
    }
    const ticks = [];
    const span_ = x1 - x0, maxTicks = Math.max(3, Math.min(7, Math.floor(W / (LANG === "en" ? 86 : 70)))),
      stepH = [3600, 7200, 10800, 21600, 43200, 86400, 172800, 345600, 604800].find((st) => span_ / st <= maxTicks) || 604800;
    const tz = new Date().getTimezoneOffset() * 60;  // ticks on local midnights / round local hours
    for (let t = Math.ceil((x0 - tz) / stepH) * stepH + tz; t <= x1; t += stepH) {
      const d = new Date(t * 1000);
      const label = stepH >= 86400 ? d.toLocaleDateString(loc(), { day: "numeric", month: "short" }) : d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
      ticks.push(`<text class="qp-axis" x="${X(t).toFixed(1)}" y="${H - 5}" text-anchor="middle">${esc(label)}</text>`);
    }
    const resets = (w.resets || []).filter((r) => r > x0 && r < now).map((r) => `<line class="qp-reset-past" x1="${X(r)}" x2="${X(r)}" y1="${P.t}" y2="${Y(0)}"/>`).join("");
    const nowMark = `<line class="qp-now" x1="${X(now)}" x2="${X(now)}" y1="${P.t}" y2="${Y(0)}"/>`;
    const resetMark = w.reset ? `<line class="qp-reset" x1="${X(w.reset)}" x2="${X(w.reset)}" y1="${P.t}" y2="${Y(0)}"/><text class="qp-reset-lbl" x="${X(w.reset) - 4}" y="${P.t + 9}" text-anchor="end">${esc(L("сброс", "reset"))} ${esc(clock(w.reset, now))}</text>` : "";
    const etaMark = w.eta && w.status === "runs_out" ? `<circle class="qp-eta" cx="${X(w.eta)}" cy="${Y(0)}" r="4"/>` : "";
    const last = hist[hist.length - 1];
    const dot = `<circle class="qp-last" cx="${X(last[0])}" cy="${Y(last[1])}" r="3.2"/>`;
    const label = L(`График остатка «${winTitle(w)}»: сейчас ${pct(w.remaining)}`, `Remaining of “${winTitle(w)}”: now ${pct(w.remaining)}`);
    return `<svg class="qp-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
      ${grid}${ticks.join("")}${resets}<path class="qp-area" d="${area}"/><path class="qp-hist" d="${line(hist, 1)}"/>${band}${projLine}${nowMark}${resetMark}${etaMark}${dot}</svg>`;
  }

  /* ------------------------------------------------------------ render */
  function windowBlock(w, now) {
    const st = stOf(w.status);
    const resetIn = w.reset ? L(`сброс через ${span(w.reset - now)} · ${clock(w.reset, now)}`, `resets in ${span(w.reset - now)} · ${clock(w.reset, now)}`) : L("срок сброса неизвестен", "reset time unknown");
    const stale = w.stale ? `<span class="qp-stale" title="${esc(L("Последний замер давно: прогноз может быть неточным", "The last sample is old: the forecast may be off"))}">${esc(L("замер", "sample"))} ${esc(clock(w.last, now))}</span>` : "";
    return `<div class="qp-win qp-st-${esc(w.status || "insufficient")}">
      <div class="qp-win-head">
        <div class="qp-win-title">${esc(winTitle(w))}</div>
        <span class="qp-chip qp-${st.cls}">${esc(L(st.chip[0], st.chip[1]))}</span>
      </div>
      <div class="qp-win-nums"><span class="qp-big">${pct(w.remaining)}</span><span class="qp-big-lbl">${esc(L("осталось", "left"))}</span>
        <span class="qp-reset-in">${esc(resetIn)}${w.reset_estimated ? esc(L(" (оценка)", " (est.)")) : ""}</span>${stale}</div>
      ${chart(w, now)}
      <div class="qp-verdict qp-${st.cls}">${esc(verdict(w, now))}</div>
      ${paceLine(w)}
    </div>`;
  }

  function accountCard(a, now) {
    const t = accTitle(a), st = stOf(a.status);
    const prefer = (S.view.prefer || []).includes(a.id);
    return `<div class="card qp-card" data-qp-acc="${esc(a.id)}">
      <div class="qp-card-head">
        <div class="p-icon">${esc(t.icon)}</div>
        <div class="qp-ident"><div class="qp-name">${esc(t.name)}</div><div class="qp-prov">${esc(t.provider)}${a.priority === "reserve" ? " · " + esc(L("резерв", "reserve")) : ""}${a.enabled ? "" : " · " + esc(L("выключен", "off"))}</div></div>
        <div class="grow"></div>
        ${prefer ? `<span class="qp-chip qp-accent" title="${esc(L("Больше всего запаса до сброса", "The most headroom until the reset"))}">★ ${esc(L("брать первым", "use first"))}</span>` : ""}
        <span class="qp-chip qp-${st.cls}">${esc(st.ico)} ${esc(L(st.ru, st.en))}</span>
      </div>
      ${a.error ? `<div class="qp-pollerr">${esc(L("Последний опрос не удался: ", "The last poll failed: "))}${esc(a.error)}</div>` : ""}
      <div class="qp-wins">${a.windows.map((w) => windowBlock(w, now)).join("")}</div>
      <div class="qp-foot">${esc(L(`Запросов за 24 ч: ${num(a.requests_24h)}`, `Requests in 24 h: ${num(a.requests_24h)}`))}${a.score != null ? ` · ${esc(L("запас", "headroom"))} ${Math.round(a.score * 100)}/100` : ""}</div>
    </div>`;
  }

  function summary(v) {
    const c = v.counts || {};
    const pills = ["runs_out", "exhausted", "tight", "ok", "insufficient", "no_reset"].filter((k) => c[k])
      .map((k) => `<span class="qp-sum qp-${stOf(k).cls}"><b>${c[k]}</b> ${esc(L(stOf(k).ru, stOf(k).en))}</span>`).join("");
    const byId = Object.fromEntries((v.accounts || []).map((a) => [a.id, a]));
    const prefer = (v.prefer || []).map((id) => byId[id]).filter(Boolean).map((a) => esc(accTitle(a).name)).join(", ");
    const risky = (v.accounts || []).filter((a) => a.status === "runs_out" || a.status === "exhausted").map((a) => esc(accTitle(a).name));
    const s = v.settings || {};
    let advice = "";
    if (risky.length && prefer) advice = L(`Берегите ${risky.join(", ")} — сейчас лучше брать ${prefer}.`, `Spare ${risky.join(", ")} — better use ${prefer} now.`);
    else if (risky.length) advice = L(`Берегите ${risky.join(", ")}: по прогнозу лимита не хватит до сброса.`, `Spare ${risky.join(", ")}: the forecast says the limit will not last until the reset.`);
    else if (prefer) advice = L(`Больше всего запаса: ${prefer}.`, `The most headroom: ${prefer}.`);
    const polls = [5, 10, 15, 30, 60].map((m) => `<option value="${m}" ${Number(s.poll_minutes) === m ? "selected" : ""}>${esc(L(`каждые ${m} мин`, `every ${m} min`))}</option>`).join("");
    return `<div class="card qp-summary">
      <div class="qp-sum-row">${pills || `<span class="muted">${esc(L("Прогнозов пока нет", "No forecasts yet"))}</span>`}<div class="grow"></div>
        <button class="btn small" id="qp-refresh" ${S.busy ? "disabled" : ""}>${esc(S.busy ? L("Опрашиваю…", "Polling…") : L("⟳ Обновить сейчас", "⟳ Refresh now"))}</button></div>
      ${advice ? `<div class="qp-advice">${advice}</div>` : ""}
      <div class="qp-settings">
        <label class="qp-toggle"><span class="switch"><input type="checkbox" id="qp-save" ${s.save_quota ? "checked" : ""}><span class="slider"></span></span>
          <span><b>${esc(L("Экономить квоту", "Save quota"))}</b><small>${esc(L("Аккаунты, которым по прогнозу не хватит лимита до сброса, берутся в работу последними (порядок внутри основных и резервных сохраняется).", "Accounts forecast to run out before the reset are used last (the order inside main and reserve accounts is kept)."))}</small></span></label>
        <label class="qp-toggle"><span class="switch"><input type="checkbox" id="qp-notify" ${s.notify ? "checked" : ""}><span class="slider"></span></span>
          <span><b>${esc(L("Предупреждать во «Входящих»", "Warn in the Inbox"))}</b><small>${esc(L("Одно сообщение, когда прогноз говорит, что лимит кончится раньше сброса.", "One message when the forecast says a limit runs out before its reset."))}</small></span></label>
        <label class="qp-poll"><span>${esc(L("Опрос лимитов", "Poll limits"))}</span><select id="qp-poll">${polls}</select></label>
      </div>
    </div>`;
  }

  function emptyState(v) {
    const waiting = (v.untracked || []).map((a) => `<li>${esc(accTitle(a).name)} <span class="muted">· ${esc(L("ждём первого замера", "waiting for the first sample"))}</span></li>`).join("");
    return `<div class="card qp-empty">
      <h3>${esc(L("Пока нечего прогнозировать", "Nothing to forecast yet"))}</h3>
      <p>${esc(L("Планировщик знает лимиты аккаунтов Antigravity и Kiro (опрашивает их сам) и API, которые присылают заголовки лимитов (x-ratelimit-*, anthropic-ratelimit-*) — им нужно несколько обычных запросов. Прогноз появляется после 3 замеров с промежутком.", "The planner knows the limits of Antigravity and Kiro accounts (it polls them itself) and of APIs that send limit headers (x-ratelimit-*, anthropic-ratelimit-*) — those need a few normal requests. A forecast appears after 3 samples spread over time."))}</p>
      ${waiting ? `<ul class="qp-wait">${waiting}</ul>` : ""}
    </div>`;
  }

  function render() {
    const root = $("#quota-root");
    if (!root || !S.view) return;
    const v = S.view, now = v.now || Date.now() / 1000;
    const rootW = root.clientWidth || 900;
    const colW = rootW >= 2 * 430 + 14 ? (rootW - 14) / 2 : rootW;
    S.chartW = Math.max(260, Math.min(760, Math.round(colW - 70)));
    const cards = (v.accounts || []).map((a) => accountCard(a, now)).join("");
    root.innerHTML = budgetSection(v.api_budgets || {})
      + `<div class="qp-section-title"><h3>${esc(L("Квоты провайдеров", "Provider quotas"))}</h3><span>${esc(L("Данные провайдеров и прогноз до сброса", "Provider readings and a forecast until reset"))}</span></div>`
      + summary(v) + (cards ? `<div class="qp-grid">${cards}</div>` : emptyState(v))
      + `<p class="qp-note muted">${esc(L("Прогноз — оценка: скорость расхода за последние часы (EWMA) плюс обычный для этого часа суток темп за последние дни; полоса — вероятный разброс. Замеры хранятся только на этом компьютере.", "The forecast is an estimate: the recent spending rate (EWMA) plus the usual pace for this hour of the day over recent days; the band is the likely spread. Samples stay on this computer."))}</p>`;
    $("#qp-refresh").onclick = refresh;
    $("#qp-save").onchange = (e) => saveSettings({ save_quota: e.target.checked });
    $("#qp-notify").onchange = (e) => saveSettings({ notify: e.target.checked });
    $("#qp-poll").onchange = (e) => saveSettings({ poll_minutes: Number(e.target.value) });
    $("#qp-api-add").onclick = () => editBudget();
    $("#qp-prices-refresh").onclick = refreshPrices;
    root.querySelectorAll("[data-budget-edit]").forEach((button) => button.onclick = () => editBudget(button.dataset.budgetEdit));
  }

  const money = (value) => new Intl.NumberFormat(loc(), { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: Math.abs(value || 0) < 0.01 && value ? 6 : 2 }).format(Number(value || 0));
  const dateTime = (stamp) => stamp ? new Date(stamp * 1000).toLocaleString(loc(), { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  const rate = (value) => value == null ? "—" : `$${Number(value).toLocaleString(loc(), { maximumFractionDigits: 6 })}`;

  function budgetSection(data) {
    const accounts = data.accounts || [];
    const cards = accounts.map((account) => {
      const risk = account.remaining_usd <= 0 ? "err" : account.fraction < 0.2 ? "warn" : "ok";
      const unknown = Number(account.unknown_requests || 0);
      const custom = (account.models || []).filter((row) => row.override).length;
      return `<article class="qp-budget" data-budget-account="${esc(account.id)}">
        <div class="qp-budget-heading"><div><h4>${esc(account.label)}</h4><span>${esc(account.prefix || account.provider)}${account.enabled ? "" : esc(L(" · выключен", " · disabled"))}</span></div><button class="btn small" data-budget-edit="${esc(account.id)}">${esc(L("Настроить", "Configure"))}</button></div>
        <div class="qp-budget-balance qp-${risk}">${esc(money(account.remaining_usd))}<span>${esc(L("расчётный остаток", "estimated remaining"))}</span></div>
        <div class="qp-budget-meter" role="meter" aria-label="${esc(L("Остаток баланса", "Balance remaining"))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round((account.fraction || 0) * 100)}"><i class="qp-${risk}" style="width:${Math.round((account.fraction || 0) * 100)}%"></i></div>
        <dl class="qp-budget-ledger"><div><dt>${esc(L("Введено", "Starting balance"))}</dt><dd>${esc(money(account.balance_usd))}</dd></div><div><dt>${esc(L("Учтённый расход", "Observed spend"))}</dt><dd>${esc(money(account.spent_usd))}</dd></div><div><dt>${esc(L("Запросы", "Requests"))}</dt><dd>${esc(num(account.requests))}</dd></div></dl>
        <div class="qp-budget-period">${esc(L("Учёт с ", "Tracking since "))}${esc(dateTime(account.started_at))}${account.until ? `<br>${esc(L("План до ", "Planning until "))}${esc(dateTime(account.until))}` : ""}</div>
        <div class="qp-budget-pacing">${account.daily_allowance_usd != null ? `<span>${esc(L("Чтобы хватило: ", "To stay within balance: "))}<b>${esc(money(account.daily_allowance_usd))}${esc(L(" / день", " / day"))}</b></span>` : ""}${account.daily_spend_usd != null ? `<span>${esc(L("Текущий темп ≈ ", "Current pace ≈ "))}${esc(money(account.daily_spend_usd))}${esc(L(" / день", " / day"))}${account.days_left != null ? esc(L(` · ещё ~${num(account.days_left, 1)} дн.`, ` · ~${num(account.days_left, 1)} days left`)) : ""}</span>` : `<span>${esc(L("Прогноз темпа появится после 3 запросов и часа наблюдений.", "A pace estimate appears after 3 requests and one hour of observation."))}</span>`}</div>
        ${unknown ? `<div class="qp-budget-warning">${esc(L(`Не оценено запросов: ${unknown}. Остаток завышен — задайте цены неизвестных моделей.`, `${unknown} request(s) have no price. Remaining balance is overstated — set prices for unknown models.`))}</div>` : ""}
        <div class="qp-budget-foot">${esc(L(`Моделей: ${(account.models || []).length} · своих тарифов: ${custom}`, `${(account.models || []).length} models · ${custom} custom tariffs`))}</div>
      </article>`;
    }).join("");
    return `<section class="qp-api-section"><div class="qp-section-title"><div><h3>${esc(L("Баланс API", "API balance"))}</h3><p>${esc(L("Ваши тарифы применяются в Proxy, MemuCode и расчётах стоимости.", "Your tariffs apply to Proxy, MemuCode and cost estimates."))}</p></div><div class="qp-actions"><button class="btn small" id="qp-prices-refresh">${esc(L("Обновить цены", "Refresh prices"))}</button><button class="btn primary" id="qp-api-add">${esc(L("Добавить аккаунт", "Add account"))}</button></div></div>
      <div class="qp-budget-disclaimer"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/></svg><p>${esc(L("Это приблизительный расчёт, а не баланс у провайдера. Учитываются только успешные запросы через этот Proxy с момента ввода баланса. Внешние приложения, ошибки после начала ответа, комиссии и расходы на изображения могут не учитываться. Все суммы в USD.", "This is an estimate, not your provider balance. It counts successful requests through this Proxy after you enter a balance. External apps, failures after output starts, fees and image charges may be missing. All amounts are USD."))}</p></div>
      ${cards ? `<div class="qp-budget-grid">${cards}</div>` : `<div class="qp-budget-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 9h18m-6 6h3M7 3v4"/></svg><div><h4>${esc(L("Начните с текущего баланса", "Start with your current balance"))}</h4><p>${esc(L("Выберите уже подключённый аккаунт, укажите остаток и проверьте цены моделей. API-ключ повторно вводить не нужно.", "Choose a connected account, enter its remaining balance and check model prices. You do not need to enter the API key again."))}</p></div></div>`}
      <div class="qp-price-source">${esc(L("Источник базовых цен: OpenRouter и встроенная таблица официальных тарифов. За 1 млн токенов.", "Base prices: OpenRouter and the built-in vendor tariff table. Per 1M tokens."))}${data.catalog_age != null ? ` ${esc(L(`Каталог обновлён ${span(data.catalog_age)} назад.`, `Catalogue updated ${span(data.catalog_age)} ago.`))}` : ""}</div></section>`;
  }

  async function refreshPrices() {
    const button = $("#qp-prices-refresh");
    button.disabled = true; button.textContent = L("Обновляю…", "Refreshing…");
    try {
      S.view.api_budgets = await api("/api/admin/quota/planner/api/prices/refresh", { method: "POST", body: {} });
      render(); toast(L("Цены обновлены; ваши тарифы сохранены", "Prices refreshed; custom tariffs preserved"), "ok");
    } catch (error) { toast(errorOf(error), "err", 6000); button.disabled = false; button.textContent = L("Обновить цены", "Refresh prices"); }
  }

  async function editBudget(accountId = "") {
    if (document.querySelector(".qp-budget-dialog")) return;
    const budgets = S.view.api_budgets || {}, existing = (budgets.accounts || []).find((row) => row.id === accountId);
    const available = budgets.available || [];
    const dialog = document.createElement("dialog");
    dialog.className = "qp-budget-dialog";
    dialog.setAttribute("aria-labelledby", "qp-budget-dialog-title");
    dialog.innerHTML = `<form method="dialog" class="qp-budget-form"><header><div><h3 id="qp-budget-dialog-title">${esc(existing ? L("Баланс и тарифы", "Balance and tariffs") : L("Добавить аккаунт", "Add account"))}</h3><p>${esc(L("Суммы в USD · цены за 1 млн токенов", "Amounts in USD · prices per 1M tokens"))}</p></div><button type="button" class="btn small qp-dialog-close" aria-label="${esc(L("Закрыть", "Close"))}">✕</button></header>
      <div class="qp-budget-form-body"><div class="qp-budget-form-grid"><label>${esc(L("Аккаунт", "Account"))}${existing ? `<div class="qp-account-value">${esc(existing.label)}<small>${esc(existing.prefix)}</small></div>` : `<select id="qp-budget-select" required><option value="">${esc(L("Выберите подключённый аккаунт", "Choose a connected account"))}</option>${available.map((row) => `<option value="${esc(row.id)}">${esc(row.label)} (${esc(row.prefix || row.provider)})</option>`).join("")}</select>`}</label>
      <label>${esc(existing ? L("Обновить текущий баланс, $", "Update current balance, $") : L("Текущий баланс, $", "Current balance, $"))}<input id="qp-budget-balance" type="number" min="0" max="1000000000000" step="any" ${existing ? `placeholder="${esc(L("Оставить прежний период", "Keep current tracking period"))}"` : "required"}><small>${esc(existing ? L("Заполняйте после сверки с провайдером. Начнётся новый период учёта; тарифы сохранятся.", "Enter after checking the provider balance. This starts a new tracking period; tariffs are preserved.") : L("Расход будет учитываться с момента сохранения.", "Spending will be tracked from the moment you save."))}</small></label>
      <label>${esc(L("Распределить баланс до", "Plan balance until"))}<input id="qp-budget-until" type="datetime-local"><small>${esc(L("Необязательно. Покажем допустимый расход в день.", "Optional. Shows the daily spending allowance."))}</small></label></div>
      <div class="qp-model-prices-heading"><div><h4>${esc(L("Тарифы моделей", "Model tariffs"))}</h4><p>${esc(L("Откройте модель, чтобы задать свои цены. Пустой кэш = цена обычного входа.", "Expand a model to set custom rates. Empty cache rate = normal input price."))}</p></div><input type="search" id="qp-budget-search" placeholder="${esc(L("Найти модель…", "Find model…"))}" aria-label="${esc(L("Найти модель", "Find model"))}"></div>
      <div class="qp-discount-row"><label>${esc(L("Скидка от базовых цен, %", "Discount from base prices, %"))}<input id="qp-budget-discount" type="number" min="0" max="100" step="any" value="0"></label><button class="btn small" type="button" id="qp-apply-discount">${esc(L("Применить к найденным", "Apply to filtered models"))}</button></div>
      <div id="qp-budget-models" class="qp-price-list" aria-live="polite"></div><div id="qp-budget-error" class="qp-budget-warning" role="alert" hidden></div></div>
      <footer>${existing ? `<button type="button" class="btn danger" id="qp-budget-remove">${esc(L("Убрать из планировщика", "Remove from planner"))}</button>` : `<span class="muted">${esc(available.length ? L("Ключи остаются в аккаунтах Proxy", "Keys stay in Proxy accounts") : L("Сначала подключите аккаунт во вкладке «Аккаунты»", "Connect an account in Accounts first"))}</span>`}<div class="grow"></div><button type="button" class="btn qp-dialog-close">${esc(L("Отмена", "Cancel"))}</button><button type="submit" class="btn primary" id="qp-budget-save">${esc(L("Сохранить", "Save"))}</button></footer></form>`;
    document.body.appendChild(dialog);
    dialog.showModal();
    const q = (selector) => dialog.querySelector(selector), form = q("form"), close = () => { dialog.close(); dialog.remove(); };
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.querySelectorAll(".qp-dialog-close").forEach((button) => button.onclick = close);
    if (existing?.until) {
      const stamp = new Date(existing.until * 1000);
      stamp.setMinutes(stamp.getMinutes() - stamp.getTimezoneOffset());
      q("#qp-budget-until").value = stamp.toISOString().slice(0, 16);
    }
    let models = [], generation = 0, busy = false;
    const getAccount = () => existing?.id || q("#qp-budget-select")?.value || "";
    const error = (message = "") => { q("#qp-budget-error").textContent = message; q("#qp-budget-error").hidden = !message; };
    const numeric = (value) => value == null ? "" : String(Number(value));
    function renderModels() {
      q("#qp-budget-models").innerHTML = models.length ? models.map((row, index) => {
        const price = row.override || row.default_price || {}, enabled = !!row.override;
        return `<details class="qp-price-model" data-price-index="${index}" data-search="${esc((row.model + " " + row.label + " " + row.canonical).toLowerCase())}"><summary><span><strong>${esc(row.label)}</strong><small>${esc(row.model)}</small></span><span class="qp-price-summary">${rate(price.input)} / ${rate(price.output)}<small>${esc(enabled ? L("свой тариф", "custom rate") : row.default_price ? L("базовая цена", "base price") : L("цена неизвестна", "unknown price"))}</small></span></summary><div class="qp-price-editor"><label class="qp-custom-check"><input type="checkbox" data-custom ${enabled ? "checked" : ""}>${esc(L("Использовать свой тариф", "Use custom tariff"))}</label><div class="qp-price-fields">${[["input", L("Вход, $ / 1M", "Input, $ / 1M")], ["output", L("Выход, $ / 1M", "Output, $ / 1M")], ["cache_read", L("Чтение кэша, $ / 1M", "Cache read, $ / 1M")], ["cache_write", L("Запись кэша, $ / 1M", "Cache write, $ / 1M")]].map(([name, label]) => `<label>${esc(label)}<input type="number" min="0" max="1000000000000" step="any" data-rate="${name}" value="${numeric(price[name])}" ${name.startsWith("cache_") ? "" : "required"} ${enabled ? "" : "disabled"}></label>`).join("")}</div><label class="qp-custom-check"><input type="checkbox" data-caching ${price.caching === false ? "" : "checked"} ${enabled ? "" : "disabled"}>${esc(L("Учитывать отдельную цену кэша", "Use separate cache rates"))}</label><p>${esc(L("Выключено — кэшированные токены считаются по цене входа. Это меняет расчёт стоимости, а не настройки кэша у провайдера.", "When off, cached tokens use the input rate. This changes accounting, not the provider’s caching behavior."))}</p><small>${esc(L("Распознано: ", "Matched: "))}${esc(row.canonical)}${row.default_price ? ` · ${esc(row.default_price.source)}${row.default_price.approximate ? esc(L(" (оценка)", " (estimate)")) : ""}` : ""}${row.default_price?.source === "catalog" ? ` · <a href="https://openrouter.ai/models" target="_blank" rel="noopener">OpenRouter</a>` : ""}</small></div></details>`;
      }).join("") : `<div class="qp-price-empty">${esc(L("В каталоге аккаунта пока нет моделей. Обновите его во вкладке «Модели», затем откройте настройки снова.", "This account has no catalogue models yet. Refresh its catalogue in Models, then reopen these settings."))}</div>`;
      q("#qp-budget-models").querySelectorAll("[data-custom]").forEach((checkbox) => checkbox.onchange = () => { checkbox.closest("details").querySelectorAll("[data-rate],[data-caching]").forEach((input) => input.disabled = !checkbox.checked); });
      filterModels();
    }
    function filterModels() {
      const query = q("#qp-budget-search").value.trim().toLowerCase();
      q("#qp-budget-models").querySelectorAll("[data-search]").forEach((row) => row.hidden = !row.dataset.search.includes(query));
    }
    async function loadModels() {
      const id = getAccount(), current = ++generation;
      error();
      if (!id) { models = []; renderModels(); return; }
      q("#qp-budget-models").textContent = L("Загружаю модели…", "Loading models…");
      q("#qp-budget-save").disabled = true;
      try {
        const result = await api(`/api/admin/quota/planner/api/accounts/${encodeURIComponent(id)}/models`);
        if (!dialog.isConnected || current !== generation) return;
        models = result.models || []; renderModels();
      } catch (failure) { if (current === generation) { models = []; error(errorOf(failure)); } }
      finally { if (current === generation) q("#qp-budget-save").disabled = false; }
    }
    q("#qp-budget-select")?.addEventListener("change", loadModels);
    q("#qp-budget-search").oninput = filterModels;
    q("#qp-apply-discount").onclick = () => {
      const input = q("#qp-budget-discount");
      if (!input.reportValidity()) return;
      const factor = 1 - Number(input.value || 0) / 100;
      q("#qp-budget-models").querySelectorAll("[data-price-index]").forEach((element) => {
        if (element.hidden) return;
        const base = models[Number(element.dataset.priceIndex)].default_price;
        if (!base) return;
        element.querySelector("[data-custom]").checked = true;
        element.querySelectorAll("[data-rate]").forEach((field) => { field.disabled = false; field.value = base[field.dataset.rate] == null ? "" : String(Number((base[field.dataset.rate] * factor).toPrecision(12))); });
        element.querySelector("[data-caching]").disabled = false;
      });
      toast(L("Скидка применена к найденным моделям с известной ценой. Сохраните изменения.", "Discount applied to filtered models with known prices. Save your changes."), "ok");
    };
    form.onsubmit = async (event) => {
      event.preventDefault();
      if (busy || !getAccount()) return;
      const invalid = form.querySelector(":invalid");
      if (invalid) { const details = invalid.closest("details"); if (details) details.open = true; form.reportValidity(); return; }
      const body = { models: {}, until: q("#qp-budget-until").value ? new Date(q("#qp-budget-until").value).getTime() / 1000 : 0 };
      if (q("#qp-budget-balance").value !== "") body.balance_usd = Number(q("#qp-budget-balance").value);
      q("#qp-budget-models").querySelectorAll("[data-price-index]").forEach((element) => {
        const raw = models[Number(element.dataset.priceIndex)].model;
        if (!element.querySelector("[data-custom]").checked) {
          if (models[Number(element.dataset.priceIndex)].override) body.models[raw] = null;
          return;
        }
        const row = { caching: element.querySelector("[data-caching]").checked };
        element.querySelectorAll("[data-rate]").forEach((field) => row[field.dataset.rate] = field.value === "" ? null : Number(field.value));
        body.models[raw] = row;
      });
      busy = true; q("#qp-budget-save").disabled = true; error();
      try {
        S.view.api_budgets = await api(`/api/admin/quota/planner/api/accounts/${encodeURIComponent(getAccount())}`, { method: "POST", body });
        close(); render(); toast(L("Баланс и тарифы сохранены", "Balance and tariffs saved"), "ok");
      } catch (failure) { error(errorOf(failure)); busy = false; q("#qp-budget-save").disabled = false; }
    };
    q("#qp-budget-remove")?.addEventListener("click", async () => {
      if (busy) return;
      if (!confirm(L("Убрать аккаунт из планировщика и удалить его тарифы? Сам аккаунт Proxy останется.", "Remove this account from the planner and delete its tariffs? The Proxy account is kept."))) return;
      busy = true;
      try { S.view.api_budgets = await api(`/api/admin/quota/planner/api/accounts/${encodeURIComponent(existing.id)}`, { method: "DELETE" }); close(); render(); }
      catch (failure) { error(errorOf(failure)); busy = false; }
    });
    if (existing) { models = existing.models || []; renderModels(); } else renderModels();
  }

  async function load() {
    const root = $("#quota-root");
    if (!root || S.loading) return;
    S.loading = true;
    try {
      S.view = await api("/api/admin/quota/planner");
      render();
    } catch (error) {
      root.innerHTML = `<div class="card"><div class="warn-strip">${esc(errorOf(error))}</div></div>`;
    } finally { S.loading = false; }
    clearInterval(S.timer);
    S.timer = setInterval(() => { if (active()) load(); else clearInterval(S.timer); }, 60000);
  }
  const active = () => !!$("#tab-quota")?.classList.contains("active");

  async function refresh() {
    S.busy = true; render();
    try {
      S.view = await api("/api/admin/quota/planner/refresh", { method: "POST", body: {} });
      toast(L("Лимиты обновлены", "Limits refreshed"), "ok", 1500);
    } catch (error) { toast(errorOf(error), "err", 6000); }
    S.busy = false; render();
  }

  async function saveSettings(changes) {
    try {
      const r = await api("/api/admin/quota/planner/settings", { method: "POST", body: changes });
      if (S.view) S.view.settings = r.settings;
      toast(L("Сохранено", "Saved"), "ok", 1200);
    } catch (error) { toast(errorOf(error), "err", 5000); }
    render();
  }

  /* ------------------------------------------------------------ badges in «Аккаунты» */
  async function badges() {
    const grid = $("#accounts-grid");
    if (!grid || !grid.querySelector("[data-acc-id]")) return;
    if (!S.badges || Date.now() - S.badgesAt > 60000) {
      try { S.badges = (await api("/api/admin/quota/planner/badges")).badges || {}; S.badgesAt = Date.now(); } catch (e) { return; }
    }
    const now = Date.now() / 1000;
    grid.querySelectorAll(".acc-card[data-acc-id]").forEach((card) => {
      const b = S.badges[card.dataset.accId];
      const host = card.querySelector(".acc-ident > div:last-child");
      if (!b || !host || host.querySelector(".qp-badge") || b.status === "insufficient") return;
      const st = stOf(b.status);
      let text = L(st.chip[0], st.chip[1]);
      if (b.status === "runs_out" && b.eta) {
        const d = new Date(b.eta * 1000), sameDay = d.toDateString() === new Date(now * 1000).toDateString();
        const when = sameDay ? d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" }) : d.toLocaleDateString(loc(), { day: "numeric", month: "short" });
        text = L(`до ${when}`, `until ${when}`);
      }
      const tip = L(`Планировщик квот: ${st.ru}`, `Quota planner: ${st.en}`) + (b.reset ? L(` · сброс ${clock(b.reset, now)}`, ` · reset ${clock(b.reset, now)}`) : "");
      const el = document.createElement("button");
      el.type = "button";
      el.className = `qp-badge qp-${st.cls}`;
      el.title = tip;
      el.setAttribute("aria-label", tip);
      el.textContent = `${st.ico} ${text}`;
      el.onclick = (event) => { event.stopPropagation(); $('.nav-item[data-tab="quota"]')?.click(); };
      host.appendChild(el);
    });
  }
  function watchAccounts() {
    const grid = $("#accounts-grid");
    if (!grid || grid._qpWatch) return;
    grid._qpWatch = true;
    let pending = 0;
    new MutationObserver(() => { clearTimeout(pending); pending = setTimeout(badges, 120); }).observe(grid, { childList: true });
  }

  function init() {
    const nav = $('.nav-item[data-tab="quota"]');
    if (nav) nav.addEventListener("click", () => load());
    ["#lang-select", "#lang-select-2"].forEach((sel) => $(sel)?.addEventListener("change", () => {
      S.badgesAt = 0;
      if (active()) setTimeout(render, 0);
    }));
    watchAccounts();
    let resizeT = 0;
    window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { if (active() && S.view) render(); }, 200); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();

  return { load, render, badges };
})();
