/* MemuAI Deep Research — клиентская часть.
 *
 * Сервер (/api/pg/research, app/deep_research.py) планирует исследование,
 * ищет, читает источники и присылает поток событий. Здесь они собираются в
 * состояние сообщения ассистента (m.research), которое app.js рисует
 * одним шагом дорожки активности, а готовое досье уходит основной модели,
 * и доклад пишется обычным потоковым ответом.
 *
 * Ссылки вида [3] в докладе превращаются в кликабельные сноски на источники.
 */
"use strict";
const MemuResearch = (() => {
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  const PHASES = {
    planning: ["Составляю план исследования", "Planning the research"],
    searching: ["Ищу источники", "Searching the web"],
    reading: ["Читаю источники", "Reading sources"],
    analyzing: ["Выписываю факты", "Extracting evidence"],
    reviewing: ["Проверяю полноту", "Checking coverage"],
    writing: ["Пишу доклад", "Writing the report"],
  };
  const ORDER = ["planning", "searching", "reading", "analyzing", "reviewing", "writing"];

  function phaseLabel(phase) {
    const pair = PHASES[phase] || PHASES.planning;
    return en() ? pair[1] : pair[0];
  }

  function elapsed(r) {
    const end = r.finishedAt || Date.now();
    const seconds = Math.max(0, Math.round((end - (r.startedAt || end)) / 1000));
    const m = Math.floor(seconds / 60), s = seconds % 60;
    return m ? `${m}:${String(s).padStart(2, "0")}` : `${s} ${t("с", "s")}`;
  }

  function apply(r, ev) {
    r.lastEventAt = Date.now();
    switch (ev.type) {
      case "plan":
        r.plan = ev.plan;
        break;
      case "phase":
        r.phase = ev.phase;
        if (ev.round) r.round = ev.round;
        break;
      case "query": {
        const found = r.queries.find((q) => q.query === ev.query && q.round === ev.round);
        const row = { query: ev.query, status: ev.status, results: ev.results || 0, round: ev.round || 1 };
        if (found) Object.assign(found, row); else r.queries.push(row);
        if (r.queries.length > 200) r.queries.splice(0, r.queries.length - 200);
        break;
      }
      case "source": {
        const found = r.sources.find((s) => s.n === ev.n);
        const row = { n: ev.n, url: ev.url, title: ev.title, domain: ev.domain, status: ev.status,
                      relevance: ev.relevance || 0, findings: ev.findings || 0 };
        if (found) Object.assign(found, row); else r.sources.push(row);
        break;
      }
      case "notes": {
        const found = r.sources.find((s) => s.n === ev.n);
        if (found) { found.findings = ev.findings; found.relevance = ev.relevance; }
        break;
      }
      case "review":
        r.review = { coverage: ev.coverage || [], contradictions: ev.contradictions || [], done: !!ev.done,
                     reason: ev.reason || "", follow_up: ev.follow_up || [], round: ev.round };
        break;
      case "stats":
        r.stats = { ...(r.stats || {}), ...ev };
        delete r.stats.type;
        break;
      case "warning":
        r.warnings = [...(r.warnings || []), String(ev.message || "")].slice(-4);
        break;
      default:
        break;
    }
  }

  /* Исследование, которое уже никто не ведёт (окно перезагрузили, MemuAI перезапустили посреди
   * работы): карточка оставалась «выполняется» с вечным счётчиком времени. Теперь — «остановлено»,
   * время — до последнего события. Возвращает true, если состояние изменилось. */
  function settle(r) {
    if (!r || r.status !== "running") return false;
    r.status = "cancelled";
    r.finishedAt = r.lastEventAt || r.startedAt || Date.now();
    return true;
  }

  /* Контекст беседы для планировщика: последние реплики без служебных. */
  function conversationContext(messages, assistant) {
    const rows = [];
    for (const m of messages || []) {
      if (m === assistant || m.internal || !String(m.content || "").trim()) continue;
      rows.push(`${m.role === "user" ? "User" : "Assistant"}: ${String(m.content).slice(0, 1200)}`);
    }
    return rows.slice(-7, -1).join("\n\n").slice(-6000);
  }

  async function run(assistant, { query, model, messages, signal, onUpdate }) {
    const r = assistant.research = {
      status: "running", phase: "planning", round: 0, plan: null, queries: [], sources: [],
      stats: {}, review: null, startedAt: Date.now(), model,
    };
    onUpdate?.();
    const response = await fetch("/api/pg/research", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, model, context: conversationContext(messages, assistant), language: PG_LANG }),
      signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    let result = null;
    let lastPaint = 0;
    for await (const data of readSSE(response.body, { idleMs: 300000 })) {
      if (data === "[DONE]") break;
      let event;
      try { event = JSON.parse(data); } catch (e) { continue; }
      if (event.type === "error") {
        r.stats = event.stats || r.stats;
        throw new Error(event.message || t("Исследование не удалось", "Research failed"));
      }
      if (event.type === "done") { result = event; continue; }
      apply(r, event);
      // Сотни событий за раунд: перерисовываем не чаще 6 раз в секунду.
      const now = performance.now();
      if (now - lastPaint > 160 || event.type === "phase" || event.type === "plan") { lastPaint = now; onUpdate?.(); }
    }
    if (!result) throw new Error(t("Поток исследования оборвался до готового досье", "The research stream ended before the dossier was ready"));
    r.status = "done";
    r.phase = "writing";
    r.finishedAt = Date.now();
    r.plan = result.plan || r.plan;
    r.stats = result.stats || r.stats;
    r.review = result.review && Object.keys(result.review).length ? { ...(r.review || {}), ...result.review } : r.review;
    r.cited = (result.sources || []).map((s) => ({ n: s.n, title: s.title, url: s.url, domain: s.domain, date: s.date }));
    // Рабочие номера источников больше не нужны: оставляем компактную сводку.
    r.sources = r.sources.map(({ n, domain, status, relevance }) => ({ n, domain, status, relevance }));
    onUpdate?.();
    return result;
  }

  /* ------------------------------------------------------------ рендер */
  function favicon(domain) {
    const letter = String(domain || "?").replace(/^www\./, "").charAt(0).toUpperCase() || "?";
    let hash = 0;
    for (const ch of String(domain || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
    return `<span class="rs-fav" style="--h:${hash}">${esc(letter)}</span>`;
  }

  function progress(r) {
    if (r.status !== "running") return 100;
    // Сколько раундов понадобится, заранее неизвестно: каждый следующий
    // раунд немного продвигает полосу, но не доводит её до конца.
    const rounds = Math.max(1, r.round || 1);
    const base = { planning: 4, searching: 12, reading: 30, analyzing: 52, reviewing: 72, writing: 92 }[r.phase] || 4;
    return Math.min(96, base + Math.min(16, (rounds - 1) * 5));
  }

  function statusMark(status) {
    if (status === "done" || status === "read") return `<i class="rs-ok">${icon("check")}</i>`;
    if (status === "error" || status === "failed") return `<i class="rs-bad">${icon("close")}</i>`;
    return `<i class="rs-spin"></i>`;
  }

  function stepHtml(m, i) {
    const r = m.research;
    if (!r) return "";
    const running = r.status === "running";
    const stats = r.stats || {};
    const queries = r.queries || [];
    const read = (r.sources || []).filter((s) => s.status === "read").length;
    const meta = [
      elapsed(r),
      `${stats.queries || queries.length} ${t("запросов", "searches")}`,
      `${r.status === "done" ? (r.cited || []).length : read} ${t("источников", "sources")}`,
    ].join(" · ");
    const title = running ? phaseLabel(r.phase) + (r.round > 1 && ["searching", "reading", "analyzing", "reviewing"].includes(r.phase) ? ` · ${t("раунд", "round")} ${r.round}` : "")
      : r.status === "done" ? t("Глубокое исследование", "Deep research")
      : r.status === "cancelled" ? t("Исследование остановлено", "Research stopped") : t("Исследование не удалось", "Research failed");
    const plan = r.plan;
    const coverage = new Map((r.review?.coverage || []).map((row) => [row.id, row]));
    const planHtml = plan ? `<div class="rs-plan">
        ${plan.title ? `<div class="rs-plan-title">${esc(plan.title)}</div>` : ""}
        ${plan.reasoning ? `<p class="rs-note">${esc(plan.reasoning)}</p>` : ""}
        <ol class="rs-subs">${(plan.sub_questions || []).map((q) => {
          const cov = coverage.get(q.id);
          const state = cov ? cov.status : "";
          return `<li class="${esc(state)}"><span>${esc(q.question)}</span>${state ? `<em title="${esc(cov.note || "")}">${esc(state === "answered" ? t("ответ найден", "answered") : state === "partial" ? t("частично", "partial") : t("пробел", "gap"))}</em>` : ""}</li>`;
        }).join("")}</ol></div>` : `<div class="step-wait"><i></i><i></i><i></i> ${esc(t("Модель оценивает задачу и решает, сколько искать", "The model is scoping the task and deciding how much to search"))}</div>`;
    const shownQueries = running ? queries.slice(-14) : queries.slice(0, 60);
    const queriesHtml = queries.length ? `<div class="rs-block"><div class="rs-label">${esc(t("Поисковые запросы", "Search queries"))} <b>${queries.length}</b></div>
        <div class="rs-queries">${shownQueries.map((q) => `<span class="rs-query is-${esc(q.status)}">${statusMark(q.status)}${esc(q.query)}${q.results ? `<small>${q.results}</small>` : ""}</span>`).join("")}
        ${queries.length > shownQueries.length ? `<span class="rs-query more">+${queries.length - shownQueries.length}</span>` : ""}</div></div>` : "";
    let sourcesHtml = "";
    if (r.status === "done" && (r.cited || []).length) {
      sourcesHtml = `<div class="rs-block"><div class="rs-label">${esc(t("Источники в докладе", "Sources in the report"))} <b>${r.cited.length}</b></div>
        <div class="rs-sources">${r.cited.map((s) => `<a class="rs-source" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer" title="${esc(s.url)}">
          ${favicon(s.domain)}<span><b>${esc(s.title || s.domain)}</b><small>[${s.n}] ${esc(s.domain)}${s.date ? " · " + esc(s.date) : ""}</small></span></a>`).join("")}</div></div>`;
    } else if ((r.sources || []).length) {
      const list = (r.sources || []).slice(-24);
      sourcesHtml = `<div class="rs-block"><div class="rs-label">${esc(t("Источники", "Sources"))} <b>${read}</b>/${r.sources.length}</div>
        <div class="rs-sources compact">${list.map((s) => `<span class="rs-source is-${esc(s.status)}" title="${esc(s.url || s.domain || "")}">
          ${favicon(s.domain)}<span><b>${esc(s.title || s.domain || "")}</b><small>${esc(s.domain || "")}${s.findings ? ` · ${s.findings} ${t("фактов", "facts")}` : ""}</small></span>${statusMark(s.status)}</span>`).join("")}</div></div>`;
    }
    const review = r.review && (r.review.reason || (r.review.contradictions || []).length) ? `<div class="rs-block rs-review">
        ${r.review.reason ? `<p class="rs-note">${icon("info")} ${esc(r.review.reason)}</p>` : ""}
        ${(r.review.contradictions || []).length ? `<div class="rs-label">${esc(t("Противоречия между источниками", "Conflicts between sources"))}</div><ul>${r.review.contradictions.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
      </div>` : "";
    const error = r.error ? `<div class="msg-error">${esc(r.error)}</div>` : "";
    const phases = `<div class="rs-phases">${ORDER.map((phase) => {
      const index = ORDER.indexOf(phase), current = ORDER.indexOf(r.phase);
      const state = r.status === "done" ? "done" : index < current ? "done" : index === current ? (running ? "active" : "done") : "";
      return `<span class="${state}">${esc(phaseLabel(phase).split(" ")[0])}</span>`;
    }).join("")}</div>`;
    const body = `<div class="rs-card">
      <div class="rs-progress"><i style="width:${progress(r)}%"></i></div>
      ${phases}
      ${planHtml}${queriesHtml}${sourcesHtml}${review}${error}
    </div>`;
    return step({
      icon: "research", title: esc(title), meta: esc(meta),
      status: running ? "running" : r.status === "done" ? "done" : r.status === "cancelled" ? "info" : "error",
      open: running || r.status === "error", cls: "step-research", body,
    });
  }

  /* [3] и [2][5] в докладе → сноски-ссылки на источники исследования. */
  function linkCitations(html, m) {
    const cited = m?.research?.cited;
    if (!cited?.length) return html;
    const byNumber = new Map(cited.map((s) => [String(s.n), s]));
    return String(html).split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>|<a\b[\s\S]*?<\/a>)/).map((part, index) => index % 2 ? part
      : part.replace(/\[(\d{1,3})\]/g, (match, n) => {
        const source = byNumber.get(n);
        if (!source) return match;
        return `<a class="cite" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer" title="${esc((source.title || source.domain) + " — " + source.domain)}">${n}</a>`;
      })).join("");
  }

  const REPORT_FALLBACK = "Write the final deep research report from the research dossier in the user's language: title, summary with the direct answer, organized sections, tables where useful, contradictions and limitations, conclusion, and a Sources section. Cite every substantive claim with the dossier's source numbers like [3]. Use only the dossier's evidence for facts and never invent sources.";

  function reportPrompt() {
    const custom = typeof internalPromptText === "function" ? internalPromptText("deep-research-report") : "";
    return custom || REPORT_FALLBACK;
  }

  return { run, apply, settle, stepHtml, linkCitations, reportPrompt, phaseLabel, conversationContext };
})();
