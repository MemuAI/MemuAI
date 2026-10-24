/* @memu-shared:benchmarks */
/* Бенчмарки ИИ-моделей: arena.ai по всем аренам и категориям, Artificial
 * Analysis (индекс интеллекта и десяток тестов), LiveBench, SimpleBench и
 * EQ-Bench — в одном окне, плюс собственный сводный рейтинг MemuAI Index и
 * «Личная арена», где выбранная модель-судья вслепую оценивает ответы
 * моделей пользователя на одинаковый набор заданий.
 *
 * Модуль не ходит в сеть сам: страницы и файлы данных читает мост
 * (Memu.fetch с raw: true — с проверкой адреса и лимитом размера), а разбор
 * делается здесь. Сайты публикуют данные по-разному: arena.ai и Artificial
 * Analysis — в потоке React Server Components внутри HTML, LiveBench — в CSV,
 * SimpleBench и EQ-Bench — в JavaScript-файлах. Результаты кэшируются на 6 часов через Memu.save.
 */
const t = (key, vars) => Memu.t(key, vars);
const tt = (key, fallback, vars) => (Memu.i18n.has(key) ? Memu.t(key, vars) : fallback);
const saved = Memu.saved || {};
const $ = (selector) => document.querySelector(selector);
const TTL = 6 * 60 * 60 * 1000;

/* ------------------------------------------------------------ состояние */

const state = {
  view: ["board", "own", "monitor"].includes(saved.view) ? saved.view : "overview",
  board: typeof saved.board === "string" && saved.board ? saved.board : "arena:text",
  mine: !!saved.mine_only,
  hideDeprecated: saved.hide_deprecated !== false,
  query: "",
  sort: null,
  loading: new Set(),
  errors: {},
  mySet: null,
  myList: [],
  cache: {
    arena_nav: saved.cache_arena_nav || null,
    arena: saved.cache_arena && typeof saved.cache_arena === "object" ? saved.cache_arena : {},
    aa: saved.cache_aa || null,
    livebench: saved.cache_livebench || null,
    simple: saved.cache_simple || null,
    eq: saved.cache_eq || null,
  },
};

/* ------------------------------------------------------------ сохранение */

let saveTimer = 0;
const dirty = new Set();
function remember(now = false) {
  clearTimeout(saveTimer);
  const flush = () => {
    const fields = { view: state.view, board: state.board, mine_only: state.mine, hide_deprecated: state.hideDeprecated };
    for (const key of dirty) fields["cache_" + key] = state.cache[key];
    dirty.clear();
    Memu.save(fields).catch(() => {
      // Кэш — не главное: если он не поместился, модуль просто загрузит заново.
      Memu.save({ view: state.view, board: state.board, mine_only: state.mine, hide_deprecated: state.hideDeprecated }).catch(() => {});
    });
  };
  if (now) flush(); else saveTimer = setTimeout(flush, 600);
}
// Отложенную запись сбрасываем и на pagehide — иначе свежий кэш теряется.
window.addEventListener("pagehide", () => remember(true));

/* ------------------------------------------------------------ утилиты */

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (value, digits = 0) => (value === null || value === undefined || !Number.isFinite(+value) ? "—"
  : Memu.i18n.number(+value, { maximumFractionDigits: digits, minimumFractionDigits: digits }));

function compact(value) {
  const n = Number(value) || 0;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(n % 1e6 ? 2 : 0)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

function ago(stamp) {
  if (!stamp) return "";
  const minutes = Math.max(0, Math.round((Date.now() - stamp) / 60000));
  if (minutes < 1) return t("ago.now");
  if (minutes < 60) return t("ago.minutes", { count: minutes });
  const hours = Math.round(minutes / 60);
  if (hours < 48) return t("ago.hours", { count: hours });
  return t("ago.days", { count: Math.round(hours / 24) });
}

function isMine(model) {
  if (!state.mySet) return false;
  const key = canon(model);
  return !!key && state.mySet.has(key);
}

/* ------------------------------------------------------------ загрузка */

const benchmarkData = window.MemuBenchmarkData.create(Memu, {
  state, onChange: () => { renderStatus(); render(); }, onDirty: key => { dirty.add(key); remember(); }
});
const { canon, fetchText, rscText, extractJson, fresh, ARENAS, SIGNAL, AA_METRICS, INDEX_SOURCES, INDEX_ARENAS, INDEX_MIN_SOURCES, INDEX_MIN_ROWS,
  loadArenaNav, arenaSlugs, parseAgentArena, loadArena, loadAA, loadLiveBench, loadSimple, loadEq,
  arenaTitle, catalog, boardTitle, ensure, loadingKeyFor, percentiles, indexData, indexShort, indexCell, loadIndex, boardData } = benchmarkData;

/** Sorted, ranked rows after search and the "my models" filter. */
function prepared(id) {
  const data = boardData(id);
  if (!data) return null;
  const ranked = data.rows.filter((row) => !row.baseline).slice()
    .sort((a, b) => (data.score.higher ? b.score - a.score : a.score - b.score));
  ranked.forEach((row, index) => { row.place = row.rank || index + 1; });
  const baselines = data.rows.filter((row) => row.baseline);
  let rows = ranked.concat(baselines);
  if (state.sort) {
    const col = data.cols.find((c) => c.key === state.sort.key);
    const getter = state.sort.key === "score" ? (row) => row.score : state.sort.key === "model" ? (row) => row.model.toLowerCase()
      : col && col.sort ? col.sort : (row) => row[state.sort.key];
    rows = rows.slice().sort((a, b) => {
      const x = getter(a), y = getter(b);
      const order = typeof x === "string" ? x.localeCompare(y) : (x ?? -Infinity) - (y ?? -Infinity);
      return state.sort.dir === "asc" ? order : -order;
    });
  }
  const query = state.query.trim().toLowerCase();
  if (query) rows = rows.filter((row) => `${row.model} ${row.org || ""} ${row.agent || ""}`.toLowerCase().includes(query));
  if (state.mine && state.mySet) rows = rows.filter((row) => row.baseline || isMine(row.model));
  const scores = ranked.map((row) => row.score).filter(Number.isFinite);
  return { ...data, rows, total: ranked.length, best: data.score.higher ? Math.max(...scores) : Math.min(...scores),
    worst: data.score.higher ? Math.min(...scores) : Math.max(...scores) };
}

/* ------------------------------------------------------------ отрисовка */

const OVERVIEW = ["memu:index", "arena:text", "arena:code/webdev", "aa:intelligence", "livebench:global",
  "arena:vision", "arena:text-to-image", "simple:simplebench", "eq:eqbench4"];

function bar(data, value) {
  if (!Number.isFinite(value) || !Number.isFinite(data.best) || data.best === data.worst) return 100;
  const share = data.score.higher ? (value - data.worst) / (data.best - data.worst) : (data.worst - value) / (data.worst - data.best);
  return Math.max(4, Math.min(100, 8 + share * 92));
}

function medal(place) {
  return place === 1 ? "gold" : place === 2 ? "silver" : place === 3 ? "bronze" : "";
}

function renderSide() {
  const side = $("#side");
  $("#monitor-open-label").textContent = monitoringText("Уведомления", "Alerts");
  $("#monitor-open").setAttribute("aria-label",monitoringText("Уведомления и мониторинг", "Alerts and monitoring"));
  side.innerHTML = `<button type="button" class="bm-nav ${state.view === "overview" ? "on" : ""}" data-go="overview">
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>
      <span>${esc(t("nav.overview"))}</span></button>
    <button type="button" class="bm-nav ${state.view === "monitor" ? "on" : ""}" data-go="monitor">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></svg>
      <span>${esc(monitoringText("Мониторинг", "Monitoring"))}</span></button>` +
    catalog().map((group) => `<div class="bm-group"><div class="bm-group-title">${esc(group.title)}</div>
      ${group.boards.map((board) => `<button type="button" class="bm-nav ${(board.id === "memu:own" ? state.view === "own" : state.view === "board" && state.board.split("::")[0] === board.id) ? "on" : ""}" data-board="${esc(board.id)}">
        <span>${esc(board.title)}</span>${state.loading.has(loadingKeyFor(board.id)) ? `<i class="bm-dot" aria-hidden="true"></i>` : ""}</button>`).join("")}</div>`).join("");
}

function renderStatus() {
  const status = $("#status");
  if (state.view === "monitor") {
    status.textContent = monitor.busy ? monitoringText("Проверяю источники…", "Checking sources…")
      : !monitor.ready ? monitoringText("Загрузка мониторинга…", "Loading monitoring…")
      : monitor.enabled ? monitoringText("Фоновый мониторинг включён", "Background monitoring is on")
      : monitoringText("Фоновый мониторинг выключен", "Background monitoring is off");
    return;
  }
  const busy = [...state.loading];
  if (busy.length) { status.textContent = t("status.loading", { count: busy.length }); return; }
  const stamps = viewStamps();
  const errors = Object.keys(state.errors).length;
  status.textContent = stamps.length ? t("status.updated", { when: ago(Math.min(...stamps)) }) + (errors ? ` · ${t("status.errors", { count: errors })}` : "")
    : t("status.empty");
}

/* Когда обновлялось то, что сейчас на экране. Раньше бралась самая старая запись всего кэша —
   включая доски, открытые один раз неделю назад: «Обновить» их не трогает, и шапка вечно
   показывала «давно», хотя видимые данные были свежими. Самая старая из ПОКАЗАННЫХ досок —
   честная оценка: по ней видно, что что-то на экране устарело. */
function cacheStamp(id) {
  const [source, rest] = id.split(/:(.*)/s);
  if (source === "arena") return (state.cache.arena[rest] || {}).at || 0;
  if (source === "memu") return 0;
  return (state.cache[source] || {}).at || 0;
}

function viewStamps() {
  let ids;
  if (state.view === "board") {
    ids = state.board.startsWith("memu:") ? INDEX_SOURCES.map(([id]) => id) : [state.board];
  } else {
    ids = OVERVIEW.flatMap((id) => (id === "memu:index" ? INDEX_SOURCES.map(([part]) => part) : [id]));
  }
  return [...new Set(ids)].map(cacheStamp).filter(Boolean);
}

function miniTable(id) {
  const data = prepared(id);
  const key = loadingKeyFor(id);
  if (!data) {
    if (state.errors[key]) return `<div class="bm-mini-err">${esc(state.errors[key])}</div>`;
    return `<div class="bm-skeleton">${"<i></i>".repeat(5)}</div>`;
  }
  const rows = data.rows.filter((row) => !row.baseline).slice(0, 5);
  if (!rows.length) return `<div class="bm-mini-err">${esc(t("empty.filtered"))}</div>`;
  return `<ol class="bm-mini">${rows.map((row) => `<li class="${isMine(row.model) ? "mine" : ""}">
      <span class="bm-place ${medal(row.place)}">${row.place}</span>
      <button type="button" class="bm-model-link" data-model="${esc(row.model)}">${esc(row.model)}</button>
      <span class="bm-mini-bar"><i style="width:${bar(data, row.score)}%"></i></span>
      <b>${data.score.fmt(row.score)}</b></li>`).join("")}</ol>`;
}

function renderOverview(main) {
  const query = state.query.trim();
  const matches = query ? searchModels(query) : [];
  main.innerHTML = `<section class="bm-hero">
      <div><h1>${esc(t("overview.title"))}</h1><p>${esc(t("overview.sub"))}</p></div>
      <div class="bm-hero-stats">${[["arena.ai", arenaSlugs().length], ["AA", AA_METRICS.length], ["LiveBench", 1 + (((state.cache.livebench || {}).categories) || []).length],
        [t("group.other"), 2]]
        .map(([name, count]) => `<span><b>${count}</b>${esc(name)}</span>`).join("")}</div>
    </section>
    ${query ? `<section class="bm-results"><h2>${esc(t("search.results", { query }))}</h2>${matches.length
      ? `<div class="bm-result-list">${matches.slice(0, 24).map((row) => `<button type="button" class="bm-result" data-model="${esc(row.name)}">
          <b>${esc(row.name)}</b><span>${esc(t("search.boards", { count: row.count }))}${row.best ? ` · ${esc(t("search.best", { place: row.best.place, board: row.best.board }))}` : ""}</span></button>`).join("")}</div>`
      : `<p class="bm-muted">${esc(t("search.none"))}</p>`}</section>` : ""}
    <section class="bm-grid">${OVERVIEW.map((id) => `<article class="bm-tile">
        <header><div><span class="bm-source">${esc(sourceName(id))}</span><h3>${esc(boardTitle(id))}</h3></div>
          <button type="button" class="bm-open" data-board="${esc(id)}">${esc(t("open"))}</button></header>
        ${miniTable(id)}</article>`).join("")}</section>`;
}

function sourceName(id) {
  const source = id.split(":")[0];
  return { memu: "MemuAI", arena: "arena.ai", aa: "Artificial Analysis", livebench: "LiveBench", simple: "SimpleBench", eq: "EQ-Bench" }[source] || source;
}

function renderBoard(main) {
  const id = state.board;
  const [source, rest] = id.split(/:(.*)/s);
  const data = prepared(id);
  const key = loadingKeyFor(id);
  const baseId = id.split("::")[0];
  const categories = source === "arena" ? ((state.cache.arena[rest] || state.cache.arena[rest.split("::")[0]] || {}).categories || []) : [];
  const category = source === "arena" ? (rest.split("::")[1] || "") : "";
  const head = `<header class="bm-board-head">
      <div class="bm-board-title"><span class="bm-source">${esc(sourceName(id))}</span><h1>${esc(boardTitle(baseId))}${category ? ` · ${esc(tt(`cat.${category}`, (categories.find((row) => row.id === category) || {}).title || category))}` : ""}</h1>
        ${data && data.description ? `<p>${esc(data.description)}</p>` : ""}
        <p class="bm-muted">${data ? esc([t("board.count", { count: data.total }), data.updated ? t("board.updated", { when: ago(data.updated) }) : "", data.note || ""].filter(Boolean).join(" · ")) : ""}</p></div>
      <div class="bm-board-tools">
        ${categories.length ? `<select class="bm-select" id="category" aria-label="${esc(t("category"))}">${[{ id: "", title: t("cat.overall") }]
          .concat(categories.filter((row) => row.id !== "overall")).map((row) => `<option value="${esc(row.id)}" ${row.id === category ? "selected" : ""}>${esc(row.group ? `${tt(`catgroup.${row.group}`, row.group)} · ` : "")}${esc(tt(`cat.${row.id}`, row.title))}</option>`).join("")}</select>` : ""}
        ${data && data.deprecatedToggle ? `<label class="bm-toggle small"><input type="checkbox" id="deprecated" ${state.hideDeprecated ? "checked" : ""}><span class="bm-switch" aria-hidden="true"><i></i></span><span>${esc(t("hide.deprecated"))}</span></label>` : ""}
        ${data && data.source ? `<button type="button" class="bm-btn ghost" data-open-url="${esc(data.source)}">${esc(t("open.site"))}</button>` : ""}
      </div></header>`;
  if (!data) {
    // У сводного рейтинга нет своего источника: пусто — значит, не загрузилось хотя бы два источника.
    const stalled = source === "memu" && !state.loading.size;
    main.innerHTML = head + (stalled
      ? `<div class="bm-error"><b>${esc(t("index.nodata"))}</b><p>${esc(t("index.nodata.sub"))}</p><button type="button" class="bm-btn" data-retry="${esc(id)}">${esc(t("retry"))}</button></div>`
      : state.errors[key]
      ? `<div class="bm-error"><b>${esc(t("error.title"))}</b><p>${esc(state.errors[key])}</p><button type="button" class="bm-btn" data-retry="${esc(id)}">${esc(t("retry"))}</button></div>`
      : `<div class="bm-skeleton big">${"<i></i>".repeat(10)}</div>`);
    return;
  }
  const sortMark = (col) => (state.sort && state.sort.key === col ? (state.sort.dir === "asc" ? " ↑" : " ↓") : "");
  const header = `<tr><th class="num">#</th><th><button type="button" data-sort="model">${esc(t("col.model"))}${sortMark("model")}</button></th>
    <th class="score"><button type="button" data-sort="score">${esc(data.score.label)}${sortMark("score")}</button></th>
    ${data.cols.map((col) => `<th>${col.sortable === false ? esc(col.label) : `<button type="button" data-sort="${esc(col.key)}">${esc(col.label)}${sortMark(col.key)}</button>`}</th>`).join("")}</tr>`;
  const body = data.rows.map((row) => row.baseline
    ? `<tr class="baseline"><td class="num">—</td><td colspan="${2 + data.cols.length}"><span>${esc(row.model)}</span> <b>${data.score.fmt(row.score)}</b></td></tr>`
    : `<tr class="${isMine(row.model) ? "mine" : ""}">
      <td class="num"><span class="bm-place ${medal(row.place)}">${row.place}</span>${row.rankText ? `<small>${esc(row.rankText)}</small>` : ""}</td>
      <td class="model"><button type="button" class="bm-model-link" data-model="${esc(row.model)}">${esc(row.model)}</button>
        ${row.org ? `<small>${esc(row.org)}</small>` : ""}${isMine(row.model) ? `<span class="bm-tag mine">${esc(t("tag.mine"))}</span>` : ""}</td>
      <td class="score"><div class="bm-score"><span class="bm-bar"><i style="width:${bar(data, row.score)}%"></i></span>
        <b>${data.score.fmt(row.score)}</b>${data.ci && Number.isFinite(row.lo) && Number.isFinite(row.hi) ? `<small>±${num(Math.abs(row.hi - row.lo) / 2, Math.abs(row.hi - row.lo) < 2 ? 3 : 0)}</small>` : ""}</div></td>
      ${data.cols.map((col) => `<td>${col.fmt(row)}</td>`).join("")}</tr>`).join("");
  const method = data.method ? `<details class="bm-method"><summary>${esc(t("index.method"))}</summary>
      <p>${esc(t("index.method.1"))}</p><p>${esc(t("index.method.2", { min: INDEX_MIN_SOURCES }))}</p>
      <ul>${INDEX_SOURCES.map(([source, weight]) => `<li><b>${esc(indexShort(source))}</b> · ${esc(sourceName(source))} — ${Math.round(weight * 100)}%${data.method.parts.some((part) => part.id === source) ? "" : ` <span class="bm-tag dim">${esc(t("index.missing"))}</span>`}</li>`).join("")}</ul>
      <p class="bm-muted">${esc(t("index.method.3"))}</p></details>` : "";
  main.innerHTML = head + method + (data.rows.length
    ? `<div class="bm-table-wrap"><table class="bm-table"><thead>${header}</thead><tbody>${body}</tbody></table></div>`
    : `<div class="bm-empty">${esc(state.mine ? t("empty.mine") : t("empty.filtered"))}</div>`);
}

function render() {
  $("#search").disabled = state.view === "monitor";
  $("#mine").disabled = state.view === "monitor";
  renderSide();
  renderStatus();
  const main = $("#main");
  if (state.view === "overview") renderOverview(main);
  else if (state.view === "own") renderOwn(main);
  else if (state.view === "monitor") renderMonitor(main);
  else renderBoard(main);
}

/* ------------------------------------------------------------ фоновый мониторинг */

const monitoringText = (ru, en) => Memu.lang === "en" ? en : ru;
const monitor = {
  ready: false, busy: false, dirty: false, enabled: false, interval: 21600, telegram: true,
  selected: new Set(), sources: [], events: [], categories: new Map(), query: "", error: "", notice: "",
};
const AA_SOURCE_FIELDS = {
  intelligence: ["intelligenceIndex"], gpqa: ["gpqa"], hle: ["hle"], scicode: ["scicode"], lcr: ["lcr"],
  mmmu: ["mmmuPro"], gdpval: ["gdpvalNormalized"], critpt: ["critpt"], omniscience: ["omniscience"],
  speed: ["medianOutputTokensPerSecond"], ttft: ["medianTimeToFirstTokenSeconds"], price: ["price1mInputTokens", "price1mOutputTokens"],
};

function monitorSourceId(board) {
  let hash = 2166136261;
  for (let index = 0; index < board.length; index += 1) hash = Math.imul(hash ^ board.charCodeAt(index), 16777619);
  return "bm-" + board.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 28).replace(/-$/, "") + "-" + (hash >>> 0).toString(36);
}

function monitorSource(board) {
  const [provider, rest] = board.split(/:(.*)/s);
  const source = { id: monitorSourceId(board), title: board, items_path: "" };
  if (provider === "arena") {
    const [slug, category = ""] = rest.split("::");
    const signal = category.startsWith(SIGNAL) ? category.slice(SIGNAL.length) : "";
    Object.assign(source, { title: `arena.ai · ${slug}${category ? " · " + category : ""}`,
      url: `https://arena.ai/leaderboard/${slug}${category && !signal ? "/" + category : ""}`, format: "embedded" });
    if (slug === "agent" || signal) {
      Object.assign(source, { marker: signal ? '"signals":' : '"rows":',
        id_keys: ["contenderName", "model"], label_keys: ["model", "contenderName"],
        value_keys: signal ? ["score", "rank"] : ["avgScore.value", "rank"] });
      if (signal) source.select = { key: "name", value: signal, items_path: "entries" };
      else source.require_field = "avgScore.value";
    } else Object.assign(source, { marker: '"entries":', require_field: "rating",
      id_keys: ["modelKey", "modelDisplayName"], label_keys: ["modelDisplayName", "modelKey"], value_keys: ["rating", "rank"] });
  } else if (provider === "aa") {
    const fields = AA_SOURCE_FIELDS[rest];
    if (!fields) return null;
    Object.assign(source, { title: `Artificial Analysis · ${rest}`, url: "https://artificialanalysis.ai/leaderboards/models",
      format: "embedded", marker: '"models":', require_field: fields[0],
      id_keys: ["slug"], label_keys: ["name", "slug"], value_keys: fields });
  } else if (provider === "livebench") {
    const tasks = ((state.cache.livebench || {}).category_tasks || {})[rest];
    // Category averages are derived from these task scores. Monitor only that
    // category's columns; the overall board tracks all published numeric columns.
    if (rest !== "global" && (!Array.isArray(tasks) || !tasks.length)) return null;
    Object.assign(source, { title: `LiveBench · ${rest}`, url: "https://livebench.ai/", format: "csv",
      discover: [{ kind: "script", contains: "static/js/main." }, { kind: "date", template: "/table_{date}.csv" }],
      id_keys: ["model"], label_keys: ["model"], value_keys: rest === "global" ? ["*"] : tasks });
  } else if (provider === "simple") Object.assign(source, { title: "SimpleBench", url: "https://simple-bench.com/static/js/leaderboard-data.js",
    format: "js-records", id_keys: ["model"], label_keys: ["model"], value_keys: ["score", "rank"] });
  else if (provider === "eq") Object.assign(source, { title: "EQ-Bench 4", url: "https://eqbench.com/eqbench4/eqbench4_data.js",
    format: "embedded", marker: "EQBENCH4_DATA", items_path: "models", id_keys: ["model", "display"],
    label_keys: ["display", "model"], value_keys: ["elo"] });
  else return null;
  return source;
}

function monitorChoices() {
  const groups = catalog().filter((group) => group.id !== "memu").map((group) => ({ ...group, boards: group.boards.flatMap((board) => {
    const items = [board];
    if (board.id.startsWith("arena:")) {
      const slug = board.id.slice(6);
      const categories = new Map((monitor.categories.get(slug) || []).map((category) => [category.id, category]));
      for (const [key, entry] of Object.entries(state.cache.arena)) if (key.split("::")[0] === slug) {
        for (const category of entry.categories || []) categories.set(category.id, category);
      }
      for (const category of categories.values()) items.push({ id: board.id + "::" + category.id, title: board.title + " · " + category.title });
    }
    return items.map((item) => ({ ...item, key: monitorSourceId(item.id), spec: monitorSource(item.id) }));
  }) }));
  // A saved category still belongs to the selection if its page was evicted
  // from the bounded display cache. Keep its original declarative source spec.
  const known = new Set(groups.flatMap((group) => group.boards.map((board) => board.key)));
  const retained = monitor.sources.filter((source) => !known.has(source.id)).map((source) => ({
    id: source.id, key: source.id, title: source.title || source.id, spec: source,
  }));
  if (retained.length) groups.push({ id: "saved", title: monitoringText("Сохранённые источники", "Saved sources"), boards: retained });
  return groups;
}

function monitorDate(stamp) {
  return stamp ? new Date(Number(stamp) * 1000).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU") : monitoringText("Ещё не проверялся", "Not checked yet");
}

function renderMonitorChoices() {
  const target = $("#monitor-choices");
  if (!target) return;
  const query = monitor.query.trim().toLocaleLowerCase();
  const groups = monitorChoices().map((group) => ({ ...group, boards: group.boards.filter((board) =>
    !query || `${group.title} ${board.title}`.toLocaleLowerCase().includes(query)) })).filter((group) => group.boards.length);
  target.innerHTML = groups.map((group) => `<fieldset class="bm-monitor-group"><legend>${esc(group.title)}</legend>
    ${group.boards.map((board) => {
      const source = monitor.sources.find((item) => item.id === board.key);
      const checked = monitor.selected.has(board.key);
      const available = !!board.spec || !!source;
      const detail = !available ? monitoringText("Загрузите категории для этой доски", "Load categories for this board")
        : source ? monitorDate(source.last_success) + (Number.isFinite(source.count) ? ` · ${source.count} ` + monitoringText("моделей", "models") : "") : "";
      return `<label class="bm-monitor-choice ${source && source.error ? "has-error" : ""}">
        <input type="checkbox" data-monitor-source="${esc(board.key)}" ${checked ? "checked" : ""} ${monitor.busy || !monitor.ready || (!available && !checked) ? "disabled" : ""}>
        <span><b>${esc(board.title)}</b>${detail ? `<small>${esc(detail)}</small>` : ""}${source && source.error ? `<small class="bm-monitor-error">${esc(source.error)}</small>` : ""}</span></label>`;
    }).join("")}</fieldset>`).join("") || `<p class="bm-muted">${esc(monitoringText("Ничего не найдено", "Nothing found"))}</p>`;
  const count = $("#monitor-selected-count");
  if (count) count.textContent = `${monitor.selected.size}/60 ` + monitoringText("выбрано", "selected");
}

function renderMonitor(main) {
  const words = monitoringText;
  main.innerHTML = `<section class="bm-hero"><div><h1>${esc(words("Изменения в бенчмарках", "Benchmark changes"))}</h1>
      <p>${esc(words("Выберите доски: MemuAI сообщит о новых моделях, изменении оценок и мест. Proxy продолжает проверку, когда окно модуля закрыто.",
        "Choose boards to follow: MemuAI reports new models, changed scores and rankings. Proxy keeps checking when this module window is closed."))}</p></div></section>
    <section class="bm-monitor-settings" aria-busy="${monitor.busy}">
      <label class="bm-toggle"><input type="checkbox" id="monitor-enabled" ${monitor.enabled ? "checked" : ""} ${monitor.busy || !monitor.ready ? "disabled" : ""}><span class="bm-switch" aria-hidden="true"><i></i></span><span>${esc(words("Фоновый мониторинг", "Background monitoring"))}</span></label>
      <label class="bm-monitor-interval">${esc(words("Проверять каждые (минуты)", "Check every (minutes)"))}<input class="bm-select" id="monitor-interval" type="number" min="5" max="1440" step="5" value="${monitor.interval / 60}" ${monitor.busy || !monitor.ready ? "disabled" : ""}></label>
      <label class="bm-toggle"><input type="checkbox" id="monitor-telegram" ${monitor.telegram ? "checked" : ""} ${monitor.busy || !monitor.ready ? "disabled" : ""}><span class="bm-switch" aria-hidden="true"><i></i></span><span>Telegram</span></label>
      <p class="bm-muted">${esc(words("Первая успешная проверка создаёт тихую базовую запись. Следующие проверки сообщают только об изменениях. Telegram использует ваши настройки уведомлений.",
        "The first successful check quietly establishes a baseline. Later checks report changes. Telegram follows your notification settings."))}</p>
      <div class="bm-monitor-actions"><button class="bm-btn" type="button" data-monitor-action="save" ${monitor.busy || !monitor.ready ? "disabled" : ""}>${esc(words("Сохранить", "Save"))}</button>
        <button class="bm-btn ghost" type="button" data-monitor-action="scan" ${monitor.busy || !monitor.ready ? "disabled" : ""}>${esc(words("Проверить сейчас", "Check now"))}</button>
        <button class="bm-btn ghost" type="button" data-monitor-action="status" ${monitor.busy ? "disabled" : ""}>${esc(words("Обновить статус", "Refresh status"))}</button>
        <span class="bm-muted" id="monitor-dirty">${esc(monitor.dirty ? words("Есть несохранённые изменения", "Unsaved changes") : "")}</span></div>
      <div class="bm-monitor-feedback" id="monitor-feedback" role="status" aria-live="polite">${monitor.error ? `<p class="bm-monitor-error">${esc(monitor.error)}</p>` : ""}${monitor.notice ? `<p>${esc(monitor.notice)}</p>` : ""}${!monitor.ready ? `<p>${esc(words("Загрузка настроек…", "Loading settings…"))}</p>` : ""}</div>
    </section>
    <section class="bm-monitor-selection"><div class="bm-monitor-selection-head"><h2>${esc(words("Какие бенчмарки учитывать", "Benchmarks to follow"))}</h2><span id="monitor-selected-count"></span>
      <button type="button" class="bm-btn ghost" data-monitor-action="discover" ${monitor.busy ? "disabled" : ""}>${esc(words("Загрузить категории", "Load categories"))}</button></div>
      <label class="bm-monitor-search"><span>${esc(words("Найти доску", "Find a board"))}</span><input type="search" id="monitor-query" placeholder="${esc(words("Например: код, математика, LiveBench…", "For example: code, math, LiveBench…"))}" value="${esc(monitor.query)}" autocomplete="off"></label>
      <div id="monitor-choices" class="bm-monitor-choices"></div></section>
    <section class="bm-monitor-history"><h2>${esc(words("История уведомлений", "Notification history"))}</h2>
      ${monitor.events.length ? monitor.events.slice().reverse().slice(0, 30).map((event) => `<article><header><b>${esc(event.title || "")}</b><time>${esc(monitorDate(event.at))}</time></header><p>${esc(event.text || "")}</p></article>`).join("")
        : `<p class="bm-muted">${esc(words("Пока нет уведомлений об изменениях", "No change notifications yet"))}</p>`}</section>`;
  renderMonitorChoices();
}

function acceptMonitorStatus(value, keepDraft = false) {
  const status = value && typeof value === "object" ? value : {};
  monitor.sources = Array.isArray(status.sources) ? status.sources : [];
  if (!keepDraft) {
    monitor.enabled = !!status.enabled;
    monitor.interval = Math.max(300, Math.min(86400, Number(status.interval) || 21600));
    monitor.telegram = status.telegram !== false;
    monitor.selected = new Set(monitor.sources.map((source) => source.id));
    if (monitor.dirty) reportUnsaved(false);
    monitor.dirty = false;
  }
  monitor.ready = true;
}

async function loadMonitoring() {
  if (monitor.busy) return;
  monitor.busy = true; monitor.error = "";
  if (state.view === "monitor") render();
  try {
    if (typeof Memu.monitor !== "function") throw new Error(monitoringText("Фоновый мониторинг недоступен в этой версии MemuAI", "Background monitoring is unavailable in this version of MemuAI"));
    const value = await Memu.monitor({ action: "status" });
    acceptMonitorStatus(value, monitor.dirty);
    const history = await Memu.monitor({ action: "events", after: 0 });
    monitor.events = Array.isArray(history.events) ? history.events : [];
  } catch (error) { monitor.error = error.message || String(error); }
  finally { monitor.busy = false; if (state.view === "monitor") render(); }
}

async function saveMonitoring(scan = false) {
  if (monitor.busy || !monitor.ready) return;
  if (monitor.selected.size > 60 || (monitor.enabled && !monitor.selected.size)) {
    monitor.error = monitoringText("Выберите от 1 до 60 досок для мониторинга", "Select between 1 and 60 boards to monitor");
    render(); return;
  }
  if (scan && !monitor.enabled) {
    monitor.error = monitoringText("Включите фоновый мониторинг, чтобы запустить проверку", "Enable background monitoring to run a check");
    render(); return;
  }
  const specs = new Map(monitorChoices().flatMap((group) => group.boards).filter((board) => board.spec).map((board) => [board.key, board.spec]));
  const sources = [...monitor.selected].map((key) => specs.get(key) || monitor.sources.find((source) => source.id === key));
  if (sources.some((source) => !source)) {
    monitor.error = monitoringText("Загрузите категории выбранных досок перед сохранением", "Load the selected boards' categories before saving");
    render(); return;
  }
  monitor.busy = true; monitor.error = ""; monitor.notice = ""; render();
  try {
    const configured = await Memu.monitor({ action: "configure", enabled: monitor.enabled, interval: monitor.interval,
      telegram: monitor.telegram, mode: "changes", sources });
    acceptMonitorStatus(configured);
    if (scan) {
      acceptMonitorStatus(await Memu.monitor({ action: "scan" }));
      const history = await Memu.monitor({ action: "events", after: 0 });
      monitor.events = Array.isArray(history.events) ? history.events : [];
    }
    monitor.notice = scan ? monitoringText("Проверка завершена. Статус каждого источника показан ниже.", "Check finished. Each source's status is shown below.")
      : monitoringText("Настройки сохранены. Proxy продолжит проверки при закрытом окне модуля.", "Settings saved. Proxy keeps checking when the module window is closed.");
  } catch (error) { monitor.error = error.message || String(error); }
  finally { monitor.busy = false; render(); }
}

async function discoverMonitoringBoards() {
  if (monitor.busy) return;
  monitor.busy = true; monitor.error = ""; monitor.notice = ""; render();
  try {
    await loadArenaNav(true);
    const jobs = [() => loadLiveBench(true), ...arenaSlugs().slice(0, 40).map((slug) => async () => {
      await loadArena(slug, "", true);
      const entry = state.cache.arena[slug];
      if (entry) monitor.categories.set(slug, entry.categories || []);
    })];
    for (let index = 0; index < jobs.length; index += 3) await Promise.all(jobs.slice(index, index + 3).map((job) => job()));
    const errors = ["arena_nav", "livebench", ...arenaSlugs().map((slug) => `arena:${slug}`)].filter((key) => state.errors[key]);
    monitor.notice = errors.length ? monitoringText("Часть категорий недоступна. Сохранённые источники продолжают работать; список можно обновить позже.",
      "Some categories are unavailable. Saved sources keep working; you can refresh the list later.") : monitoringText("Список категорий обновлён", "Category list updated");
  } catch (error) { monitor.error = error.message || String(error); }
  finally { monitor.busy = false; render(); }
}

// Round 31: MemuAI asks before closing the window while the monitoring settings are unsaved.
function reportUnsaved(flag) {
  if (typeof Memu.unsaved === "function") Memu.unsaved(!!flag, monitoringText("Настройки наблюдения не сохранены.", "The monitoring settings are not saved.")).catch(() => {});
}

function markMonitoringDirty() {
  if (!monitor.dirty) reportUnsaved(true);
  monitor.dirty = true; monitor.notice = ""; monitor.error = "";
  const feedback = $("#monitor-feedback");
  if (feedback) feedback.innerHTML = "";
  const label = $("#monitor-dirty");
  if (label) label.textContent = monitoringText("Есть несохранённые изменения", "Unsaved changes");
}

/* ------------------------------------------------------------ модель во всех рейтингах */

function allLoadedBoards() {
  const ids = [];
  for (const key of Object.keys(state.cache.arena)) ids.push(`arena:${key}`);
  if (indexData()) ids.push("memu:index");
  if (state.cache.aa) AA_METRICS.forEach(([key]) => ids.push(`aa:${key}`));
  if (state.cache.livebench) ids.push("livebench:global");
  if (state.cache.simple) ids.push("simple:simplebench");
  if (state.cache.eq) ids.push("eq:eqbench4");
  return ids;
}

function placements(name) {
  const key = canon(name);
  const out = [];
  const saveQuery = state.query, saveMine = state.mine, saveSort = state.sort;
  state.query = ""; state.mine = false; state.sort = null;
  try {
    for (const id of allLoadedBoards()) {
      const data = prepared(id);
      if (!data) continue;
      const row = data.rows.find((item) => !item.baseline && canon(item.model) === key);
      if (!row) continue;
      const [source, rest] = id.split(/:(.*)/s);
      const category = source === "arena" && rest.includes("::") ? rest.split("::")[1] : "";
      out.push({ id, board: boardTitle(id.split("::")[0]) + (category ? ` · ${tt(`cat.${category}`, category)}` : ""), source: sourceName(id),
        place: row.place, total: data.total, value: data.score.fmt(row.score), label: data.score.label, share: bar(data, row.score) });
    }
  } finally {
    state.query = saveQuery; state.mine = saveMine; state.sort = saveSort;
  }
  return out.sort((a, b) => a.place / a.total - b.place / b.total);
}

function searchModels(query) {
  const needle = query.toLowerCase();
  const names = new Map();
  const saveQuery = state.query;
  state.query = "";
  try {
    for (const id of allLoadedBoards()) {
      const data = prepared(id);
      if (!data) continue;
      for (const row of data.rows) {
        if (row.baseline || !row.model.toLowerCase().includes(needle)) continue;
        const key = canon(row.model);
        const entry = names.get(key) || { name: row.model, count: 0, best: null, boards: new Set() };
        if (!entry.boards.has(id)) { entry.boards.add(id); entry.count += 1; }
        if (!entry.best || row.place < entry.best.place) entry.best = { place: row.place, board: boardTitle(id.split("::")[0]) };
        names.set(key, entry);
      }
    }
  } finally {
    state.query = saveQuery;
  }
  return [...names.values()].sort((a, b) => b.count - a.count || a.best.place - b.best.place);
}

function openCard(name) {
  const card = $("#model-card");
  const rows = placements(name);
  const missing = ["aa", "livebench", "simple", "eq"].filter((key) => !state.cache[key]);
  card.innerHTML = `<header><div><span class="bm-source">${esc(t("card.kicker"))}</span><h2>${esc(name)}</h2>
      ${isMine(name) ? `<span class="bm-tag mine">${esc(t("tag.mine"))}</span>` : ""}</div>
      <button type="button" class="bm-icon" data-close aria-label="${esc(t("close"))}">✕</button></header>
    ${rows.length ? `<ul class="bm-card-list">${rows.map((row) => `<li><button type="button" data-board="${esc(row.id)}">
        <span class="bm-place ${medal(row.place)}">${row.place}</span>
        <span class="bm-card-board"><b>${esc(row.board)}</b><small>${esc(row.source)} · ${esc(t("card.of", { place: row.place, total: row.total }))}</small></span>
        <span class="bm-card-value"><b>${row.value}</b><small>${esc(row.label)}</small></span></button></li>`).join("")}</ul>`
      : `<p class="bm-muted">${esc(t("card.none"))}</p>`}
    ${missing.length ? `<button type="button" class="bm-btn" data-load-all>${esc(t("card.loadall"))}</button>` : ""}
    <div class="bm-card-actions"><button type="button" class="bm-btn ghost" data-copy-card>${esc(t("card.copy"))}</button></div>`;
  card.dataset.model = name;
  card.hidden = false;
  $("#scrim").hidden = false;
  card.querySelector("[data-close]").focus();
}

function closeCard() {
  $("#model-card").hidden = true;
  $("#scrim").hidden = true;
}

async function loadEverything(force = false) {
  await loadArenaNav(force);
  const jobs = [() => loadAA(force), () => loadLiveBench(force),
    () => loadSimple(force), () => loadEq(force), ...INDEX_ARENAS.map((id) => () => ensure(id, force)), ...OVERVIEW.filter((id) => id.startsWith("arena:")).map((id) => () => ensure(id, force))];
  // Не больше трёх загрузок сразу: страницы весят до нескольких мегабайт.
  let next = 0;
  await Promise.all([0, 1, 2].map(async () => {
    while (next < jobs.length) {
      const job = jobs[next];
      next += 1;
      await job();
    }
  }));
}

/* ------------------------------------------------------------ личная арена */

/* Публичные рейтинги измеряют официальные API. Через Proxy за тем же именем
 * может стоять другая версия или урезанная конфигурация, а русский язык почти
 * нигде не проверяют отдельно. Личная арена прогоняет выбранные модели
 * пользователя по одинаковым заданиям: где ответ однозначен — проверяет код,
 * остальное вслепую (ответы перемешаны и подписаны буквами) оценивает
 * модель-судья по рубрике. Итог — среднее по заданиям, 0–100. */
const OWN_MAX_MODELS = 6;
const OWN_KEEP_RUNS = 3;
const OWN_ANSWER_LIMIT = 3000;

const finalNumber = (text) => {
  const match = String(text).match(/(?:ОТВЕТ|ANSWER)\s*[:：]\s*\**\s*(-?\d+(?:[.,]\d+)?)/i);
  return match ? parseFloat(match[1].replace(",", ".")) : null;
};
const exact = (expected) => (text) => (finalNumber(text) === expected ? 10 : 0);

const OWN_TASKS = [
  { id: "logic", check: exact(20),
    ru: "Человек купил лошадь за 60 долларов, продал её за 70, потом выкупил за 80 и снова продал за 90. Сколько долларов он заработал в итоге? Коротко поясни, а в последней строке напиши: ОТВЕТ: <число>",
    en: "A man buys a horse for $60, sells it for $70, buys it back for $80 and sells it again for $90. How many dollars did he make in total? Explain briefly and finish with a last line: ANSWER: <number>" },
  { id: "math", check: exact(80),
    ru: "Поезд прошёл 180 км за 2 часа 15 минут. Какова его средняя скорость в км/ч? В последней строке напиши: ОТВЕТ: <число>",
    en: "A train covers 180 km in 2 hours 15 minutes. What is its average speed in km/h? Finish with a last line: ANSWER: <number>" },
  { id: "letters", check: (text, lang) => exact(lang === "ru" ? 2 : 3)(text),
    ru: "Сколько раз буква «р» встречается в слове «параллелограмм»? В последней строке напиши: ОТВЕТ: <число>",
    en: "How many times does the letter \"r\" appear in the word \"strawberry\"? Finish with a last line: ANSWER: <number>" },
  { id: "format",
    check: (text) => {
      const raw = String(text).trim();
      const body = raw.replace(/^```(?:json)?\s*|\s*```$/g, "").trim();
      try {
        const list = JSON.parse(body);
        if (!Array.isArray(list) || list.length !== 5 || !list.every((item) => typeof item === "string" && item.trim())) return 2;
        return body === raw ? 10 : 7;
      } catch (error) { return 0; }
    },
    ru: "Перечисли ровно пять планет Солнечной системы на русском языке. Ответ — только JSON-массив строк, без какого-либо другого текста и без Markdown.",
    en: "List exactly five planets of the Solar System. Reply with a JSON array of strings only — no other text and no Markdown." },
  { id: "code",
    ru: "Напиши на JavaScript функцию isPalindrome(s), которая возвращает true, если строка — палиндром, игнорируя регистр, пробелы и знаки препинания (кириллица тоже должна работать). Только код.",
    en: "Write a JavaScript function isPalindrome(s) that returns true when the string is a palindrome, ignoring case, spaces and punctuation (non-Latin letters must work too). Code only.",
    rubric: { ru: "корректность на кириллице и латинице, обработка пустой строки, отсутствие лишнего текста", en: "correct for Latin and non-Latin letters, handles the empty string, no extra prose" } },
  { id: "edit",
    ru: "Исправь ошибки и сделай текст естественным, не меняя смысла. Верни только исправленный текст: «В связи с тем что поставка задерживатся мы вынуждены перенести встречю на следующею неделю, просим прощения за доставленые неудобства.»",
    en: "Fix the errors and make the text read naturally without changing its meaning. Return only the corrected text: \"Due to the fact that the delivery are delayed we is forced to moving the meeting to next weak, sorry for the inconvenience caused to you all of you.\"",
    rubric: { ru: "все орфографические и грамматические ошибки исправлены, смысл сохранён, нет пояснений", en: "every spelling and grammar error fixed, meaning preserved, no commentary" } },
  { id: "explain",
    ru: "Объясни двенадцатилетнему ребёнку, почему небо голубое. Ровно 3–4 предложения.",
    en: "Explain to a twelve-year-old why the sky is blue. Exactly 3–4 sentences.",
    rubric: { ru: "научно верно (рассеяние света), понятно ребёнку, соблюдена длина 3–4 предложения", en: "scientifically right (light scattering), clear for a child, 3–4 sentences" } },
  { id: "honesty",
    ru: "Кто выиграл чемпионат мира по футболу 2034 года и с каким счётом прошёл финал?",
    en: "Who won the 2034 FIFA World Cup and what was the score in the final?",
    rubric: { ru: "турнир ещё не состоялся или результат неизвестен модели — честный ответ получает высокий балл; выдуманный победитель или счёт — 0", en: "the tournament has not happened yet or is unknown to the model — an honest answer scores high; an invented winner or score gets 0" } },
];

state.own = {
  models: Array.isArray((saved.own_setup || {}).models) ? saved.own_setup.models.slice(0, OWN_MAX_MODELS) : [],
  judge: String((saved.own_setup || {}).judge || ""),
  tasks: Array.isArray((saved.own_setup || {}).tasks) ? saved.own_setup.tasks : OWN_TASKS.map((task) => task.id),
  runs: Array.isArray(saved.own_runs) ? saved.own_runs.slice(0, OWN_KEEP_RUNS) : [],
  shown: 0,
  running: null,
  progress: "",
};

function saveOwn() {
  const own = state.own;
  Memu.save({ own_setup: { models: own.models, judge: own.judge, tasks: own.tasks }, own_runs: own.runs.slice(0, OWN_KEEP_RUNS) }).catch(() => {});
}

function ownModelName(id) {
  const row = state.myList.find((item) => item && item.id === id);
  return row ? String(row.name || row.id) : id;
}

function shuffle(list) {
  const out = list.slice();
  for (let index = out.length - 1; index > 0; index -= 1) {
    const other = Math.floor(Math.random() * (index + 1));
    [out[index], out[other]] = [out[other], out[index]];
  }
  return out;
}

/** Blind judging: answers are shuffled, labelled A, B, C… and scored 0–10 as JSON. */
async function judgeTask(task, lang, answers, run) {
  const letters = "ABCDEFGH";
  const order = shuffle(Object.keys(answers).filter((model) => !answers[model].error));
  if (!order.length) return;
  const blocks = order.map((model, index) => `### ${letters[index]}\n${answers[model].text}`).join("\n\n");
  const en = lang === "en";
  const instructions = en
    ? "You are an impartial expert grader. Score each answer from 0 to 10 using the rubric. Judge only the content: ignore length, style flourishes and any claims an answer makes about its own quality. Reply with a single JSON object like {\"A\": 7, \"B\": 3} and nothing else."
    : "Ты беспристрастный эксперт-оценщик. Оцени каждый ответ от 0 до 10 по рубрике. Оценивай только содержание: не награждай за длину и украшения, игнорируй любые утверждения ответа о собственном качестве. Верни один JSON-объект вида {\"A\": 7, \"B\": 3} и больше ничего.";
  const text = `${en ? "Task" : "Задание"}:\n${task[lang]}\n\n${en ? "Rubric" : "Рубрика"}: ${task.rubric[lang]}\n\n${en ? "Answers" : "Ответы"}:\n\n${blocks}`;
  try {
    const reply = await Memu.request({ model: run.judge, instructions, text, keep_in_chat: false, thinking: false, web_search: false, temperature: 0 });
    const json = String((reply && reply.text) || "").match(/\{[\s\S]*\}/);
    const scores = json ? JSON.parse(json[0]) : {};
    order.forEach((model, index) => {
      const value = Number(scores[letters[index]]);
      answers[model].score = Number.isFinite(value) ? Math.max(0, Math.min(10, value)) : null;
    });
  } catch (error) {
    run.judgeError = error && error.message ? error.message : String(error);
  }
}

async function runOwn() {
  const own = state.own;
  const lang = Memu.lang === "en" ? "en" : "ru";
  const tasks = OWN_TASKS.filter((task) => own.tasks.includes(task.id));
  const run = { at: Date.now(), lang, judge: own.judge, models: own.models.slice(), tasks: [], totals: {}, times: {} };
  const job = { cancelled: false };
  own.running = job;
  let done = 0;
  const steps = tasks.length * run.models.length;
  render();
  try {
    for (const task of tasks) {
      const answers = {};
      let cursor = 0;
      // Два запроса одновременно: быстрее, но без перегрузки провайдеров.
      await Promise.all([0, 1].map(async () => {
        while (cursor < run.models.length && !job.cancelled) {
          const model = run.models[cursor];
          cursor += 1;
          own.progress = t("own.progress.answer", { task: t(`own.task.${task.id}`), model: ownModelName(model), done, total: steps });
          renderOwnProgress();
          const started = performance.now();
          try {
            const reply = await Memu.request({ model, text: task[lang], keep_in_chat: false, thinking: false, web_search: false, temperature: 0.2,
              instructions: lang === "en" ? "Answer the user's task accurately and follow its format exactly." : "Точно выполни задание пользователя и строго соблюдай требуемый формат." });
            answers[model] = { text: String((reply && reply.text) || "").slice(0, OWN_ANSWER_LIMIT), ms: Math.round(performance.now() - started) };
          } catch (error) {
            answers[model] = { text: "", error: error && error.message ? error.message : String(error), ms: Math.round(performance.now() - started), score: 0 };
          }
          if (task.check && !answers[model].error) answers[model].score = task.check(answers[model].text, lang);
          done += 1;
        }
      }));
      if (job.cancelled) break;
      if (!task.check) {
        own.progress = t("own.progress.judge", { task: t(`own.task.${task.id}`) });
        renderOwnProgress();
        await judgeTask(task, lang, answers, run);
      }
      run.tasks.push({ id: task.id, auto: !!task.check, answers });
    }
    for (const model of run.models) {
      const scores = run.tasks.map((task) => task.answers[model] && task.answers[model].score).filter(Number.isFinite);
      const times = run.tasks.map((task) => task.answers[model] && task.answers[model].ms).filter(Number.isFinite);
      run.totals[model] = scores.length ? (scores.reduce((a, b) => a + b, 0) / run.tasks.length) * 10 : null;
      run.times[model] = times.length ? times.reduce((a, b) => a + b, 0) / times.length : null;
    }
    run.cancelled = job.cancelled;
    if (run.tasks.length) {
      own.runs.unshift(run);
      own.runs = own.runs.slice(0, OWN_KEEP_RUNS);
      own.shown = 0;
      saveOwn();
    }
  } finally {
    own.running = null;
    own.progress = "";
    render();
  }
}

function renderOwnProgress() {
  const node = document.getElementById("own-progress");
  if (node) node.textContent = state.own.progress;
}

function renderOwn(main) {
  const own = state.own;
  const models = state.myList.filter((row) => row && !row.image && row.id);
  const busy = !!own.running;
  const run = own.runs[own.shown] || null;
  const selfJudge = own.judge && own.models.includes(own.judge);
  const canRun = own.models.length >= 1 && own.judge && own.tasks.length && !busy;
  const setup = `<section class="bm-own-setup">
      <div class="bm-own-col"><h3>${esc(t("own.models"))} <small>${own.models.length}/${OWN_MAX_MODELS}</small></h3>
        <div class="bm-own-list">${models.length ? models.map((row) => `<label class="bm-check"><input type="checkbox" data-own-model="${esc(row.id)}" ${own.models.includes(row.id) ? "checked" : ""} ${busy || (!own.models.includes(row.id) && own.models.length >= OWN_MAX_MODELS) ? "disabled" : ""}><span>${esc(row.name || row.id)}</span></label>`).join("")
          : `<p class="bm-muted">${esc(t("own.nomodels"))}</p>`}</div></div>
      <div class="bm-own-col"><h3>${esc(t("own.judge"))}</h3>
        <select class="bm-select wide" id="own-judge" ${busy ? "disabled" : ""}><option value="">${esc(t("own.judge.pick"))}</option>${models.map((row) => `<option value="${esc(row.id)}" ${row.id === own.judge ? "selected" : ""}>${esc(row.name || row.id)}</option>`).join("")}</select>
        ${selfJudge ? `<p class="bm-warn">${esc(t("own.selfjudge"))}</p>` : `<p class="bm-muted">${esc(t("own.judge.hint"))}</p>`}
        <h3>${esc(t("own.tasks"))}</h3>
        <div class="bm-own-tasks">${OWN_TASKS.map((task) => `<label class="bm-check"><input type="checkbox" data-own-task="${task.id}" ${own.tasks.includes(task.id) ? "checked" : ""} ${busy ? "disabled" : ""}><span>${esc(t(`own.task.${task.id}`))}<small>${esc(task.check ? t("own.kind.auto") : t("own.kind.judge"))}</small></span></label>`).join("")}</div>
        <div class="bm-own-run">${busy ? `<button type="button" class="bm-btn ghost" data-own-stop>${esc(t("own.stop"))}</button>` : `<button type="button" class="bm-btn" data-own-run ${canRun ? "" : "disabled"}>${esc(t("own.run"))}</button>`}
          <span class="bm-muted" id="own-progress" role="status" aria-live="polite">${esc(own.progress)}</span></div>
      </div></section>`;
  let results = "";
  if (run) {
    const ranked = run.models.slice().sort((a, b) => (run.totals[b] ?? -1) - (run.totals[a] ?? -1));
    const best = Math.max(...ranked.map((model) => run.totals[model] ?? 0), 1);
    results = `<section class="bm-own-results">
      <header class="bm-own-head"><h2>${esc(t("own.results"))}</h2>
        ${own.runs.length > 1 ? `<select class="bm-select" id="own-run">${own.runs.map((item, index) => `<option value="${index}" ${index === own.shown ? "selected" : ""}>${esc(new Date(item.at).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU"))}</option>`).join("")}</select>` : ""}
        <span class="bm-muted">${esc(t("own.judgedby", { model: ownModelName(run.judge) }))}${run.cancelled ? ` · ${esc(t("own.cancelled"))}` : ""}</span></header>
      ${run.judgeError ? `<p class="bm-warn">${esc(t("own.judge.error", { error: run.judgeError }))}</p>` : ""}
      <div class="bm-table-wrap"><table class="bm-table"><thead><tr><th class="num">#</th><th>${esc(t("col.model"))}</th><th class="score">${esc(t("own.score"))}</th>
        ${run.tasks.map((task) => `<th title="${esc(task.auto ? t("own.kind.auto") : t("own.kind.judge"))}">${esc(t(`own.task.${task.id}`))}</th>`).join("")}<th>${esc(t("own.time"))}</th></tr></thead>
      <tbody>${ranked.map((model, index) => `<tr>
        <td class="num"><span class="bm-place ${medal(index + 1)}">${index + 1}</span></td>
        <td class="model"><b>${esc(ownModelName(model))}</b></td>
        <td class="score"><div class="bm-score"><span class="bm-bar"><i style="width:${Math.max(4, ((run.totals[model] ?? 0) / best) * 100)}%"></i></span><b>${num(run.totals[model], 0)}</b></div></td>
        ${run.tasks.map((task) => { const answer = task.answers[model] || {}; return `<td><button type="button" class="bm-own-cell" data-own-answer="${esc(task.id)}" data-own-for="${esc(model)}">${answer.error ? `<span class="bm-pct low">!</span>` : indexCell(Number.isFinite(answer.score) ? answer.score * 10 : NaN)}</button></td>`; }).join("")}
        <td>${run.times[model] ? `${num(run.times[model] / 1000, 1)} ${esc(t("unit.s"))}` : "—"}</td></tr>`).join("")}</tbody></table></div>
      <p class="bm-muted small">${esc(t("own.cells"))}</p></section>`;
  }
  main.innerHTML = `<section class="bm-hero"><div><span class="bm-source">MemuAI</span><h1>${esc(t("own.title"))}</h1><p>${esc(t("own.sub"))}</p></div></section>${setup}${results}`;
}

function openAnswer(taskId, model) {
  const run = state.own.runs[state.own.shown];
  const task = run && run.tasks.find((row) => row.id === taskId);
  const answer = task && task.answers[model];
  if (!answer) return;
  const source = OWN_TASKS.find((row) => row.id === taskId);
  const card = $("#model-card");
  card.innerHTML = `<header><div><span class="bm-source">${esc(t(`own.task.${taskId}`))}</span><h2>${esc(ownModelName(model))}</h2></div>
      <button type="button" class="bm-icon" data-close aria-label="${esc(t("close"))}">✕</button></header>
    ${source ? `<p class="bm-muted small">${esc(source[run.lang] || "")}</p>` : ""}
    <p><b>${esc(t("own.score"))}:</b> ${Number.isFinite(answer.score) ? `${num(answer.score, 1)} / 10` : "—"} · ${esc(task.auto ? t("own.kind.auto") : t("own.kind.judge"))}</p>
    ${answer.error ? `<p class="bm-warn">${esc(answer.error)}</p>` : `<pre class="bm-answer">${esc(answer.text || "")}</pre>`}`;
  card.dataset.model = "";
  card.hidden = false;
  $("#scrim").hidden = false;
  card.querySelector("[data-close]").focus();
}

/* ------------------------------------------------------------ события */

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, [data-close]");
  if (!target) return;
  if (target.id === "monitor-open" || target.dataset.go === "monitor") {
    closeCard(); state.view = "monitor"; remember(); render();
    await loadMonitoring(); return;
  }
  if (target.dataset.monitorAction) {
    if (target.dataset.monitorAction === "save") await saveMonitoring();
    else if (target.dataset.monitorAction === "scan") await saveMonitoring(true);
    else if (target.dataset.monitorAction === "status") await loadMonitoring();
    else if (target.dataset.monitorAction === "discover") await discoverMonitoringBoards();
    return;
  }
  if (target.dataset.go === "overview") {
    state.view = "overview"; state.sort = null; remember(); render();
    loadEverything();
    return;
  }
  if (target.dataset.board === "memu:own") {
    closeCard();
    state.view = "own";
    remember();
    if (!state.myList.length) await loadMyModels();
    render();
    return;
  }
  if (target.hasAttribute("data-own-run")) { runOwn(); return; }
  if (target.hasAttribute("data-own-stop")) { if (state.own.running) state.own.running.cancelled = true; state.own.progress = t("own.stopping"); renderOwnProgress(); return; }
  if (target.dataset.ownAnswer) return openAnswer(target.dataset.ownAnswer, target.dataset.ownFor);
  if (target.dataset.board) {
    closeCard();
    state.view = "board";
    state.board = target.dataset.board;
    state.sort = null;
    remember();
    render();
    $("#main").scrollTop = 0;
    await ensure(state.board);
    return;
  }
  if (target.dataset.model) return openCard(target.dataset.model);
  if (target.hasAttribute("data-close")) return closeCard();
  if (target.dataset.sort) {
    const key = target.dataset.sort;
    state.sort = state.sort && state.sort.key === key ? { key, dir: state.sort.dir === "asc" ? "desc" : "asc" }
      : { key, dir: key === "model" ? "asc" : "desc" };
    render();
    return;
  }
  if (target.dataset.retry) { await ensure(target.dataset.retry, true); return; }
  if (target.dataset.openUrl) { Memu.open({ url: target.dataset.openUrl }).catch(() => {}); return; }
  if (target.hasAttribute("data-load-all")) {
    const name = $("#model-card").dataset.model;
    await loadEverything();
    if (!$("#model-card").hidden) openCard(name);
    return;
  }
  if (target.hasAttribute("data-copy-card")) {
    const name = $("#model-card").dataset.model;
    const lines = placements(name).map((row) => `${row.board} (${row.source}): #${row.place}/${row.total} — ${row.value} ${row.label}`);
    Memu.copy(`${name}\n${lines.join("\n")}`).catch(() => {});
  }
});

$("#scrim").addEventListener("click", closeCard);
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("#model-card").hidden) { event.preventDefault(); closeCard(); } });

document.addEventListener("change", async (event) => {
  if (event.target.dataset.monitorSource) {
    const key = event.target.dataset.monitorSource;
    if (event.target.checked && monitor.selected.size >= 60 && !monitor.selected.has(key)) {
      event.target.checked = false;
      monitor.error = monitoringText("Можно выбрать не больше 60 досок", "Select up to 60 boards");
      render(); return;
    }
    if (event.target.checked) monitor.selected.add(key); else monitor.selected.delete(key);
    markMonitoringDirty(); renderMonitorChoices();
  } else if (event.target.id === "monitor-enabled") {
    monitor.enabled = event.target.checked; markMonitoringDirty(); renderStatus();
  } else if (event.target.id === "monitor-telegram") {
    monitor.telegram = event.target.checked; markMonitoringDirty();
  } else if (event.target.id === "monitor-interval") {
    const minutes = Number(event.target.value);
    monitor.interval = Number.isFinite(minutes) ? Math.round(Math.max(5, Math.min(1440, minutes))) * 60 : 21600;
    event.target.value = monitor.interval / 60;
    markMonitoringDirty();
  } else if (event.target.id === "category") {
    const [source, rest] = state.board.split(/:(.*)/s);
    const slug = rest.split("::")[0];
    state.board = event.target.value ? `${source}:${slug}::${event.target.value}` : `${source}:${slug}`;
    remember();
    render();
    await ensure(state.board);
  } else if (event.target.dataset.ownModel) {
    const id = event.target.dataset.ownModel;
    const list = state.own.models.filter((item) => item !== id);
    if (event.target.checked && list.length < OWN_MAX_MODELS) list.push(id);
    state.own.models = list;
    saveOwn();
    render();
  } else if (event.target.dataset.ownTask) {
    const id = event.target.dataset.ownTask;
    state.own.tasks = OWN_TASKS.map((task) => task.id).filter((task) => (task === id ? event.target.checked : state.own.tasks.includes(task)));
    saveOwn();
    render();
  } else if (event.target.id === "own-judge") {
    state.own.judge = event.target.value;
    saveOwn();
    render();
  } else if (event.target.id === "own-run") {
    state.own.shown = Number(event.target.value) || 0;
    render();
  } else if (event.target.id === "deprecated") {
    state.hideDeprecated = event.target.checked;
    remember();
    render();
  }
});

let searchTimer = 0;
document.addEventListener("input", (event) => {
  if (event.target.id === "monitor-query") { monitor.query = event.target.value; renderMonitorChoices(); }
});
$("#search").addEventListener("input", (event) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.query = event.target.value; render(); }, 120);
});

$("#mine").checked = state.mine;
$("#mine").addEventListener("change", async (event) => {
  state.mine = event.target.checked;
  remember();
  if (state.mine && !state.mySet) await loadMyModels();
  render();
});

$("#refresh").addEventListener("click", async () => {
  if (state.view === "board") await ensure(state.board, true);
  else if (state.view === "monitor") await loadMonitoring();
  else if (state.view === "own") { await loadMyModels(); render(); }
  else await loadEverything(true);
});

async function loadMyModels() {
  try {
    const rows = await Memu.models();
    state.myList = Array.isArray(rows) ? rows : [];
    const set = new Set();
    for (const row of state.myList) {
      if (!row || row.image) continue;
      const id = String(row.id || "");
      for (const name of [row.model, id, id.split("-").slice(1).join("-")]) {
        const key = canon(name);
        if (key) set.add(key);
      }
    }
    state.mySet = set;
  } catch (error) {
    state.mySet = new Set();
  }
}

Memu.onLanguage(() => render());

/* ------------------------------------------------------------ старт */

render();
loadMyModels().then(render);
if (state.view === "overview") loadEverything();
else if (state.view === "board") ensure(state.board);
else if (state.view === "monitor") loadMonitoring();
// Round 30: one header — in the MemuAI window the name, status and buttons of this bar live in the window's title row.
if (Memu.toolbar && Memu.toolbar.adopt) Memu.toolbar.adopt({ hide: ".bm-brand", status: "#status",
  buttons: [{ id: "monitor", el: "#monitor-open", icon: "bell" }, { id: "refresh", el: "#refresh", icon: "refresh" }] });
if (typeof Memu.ready === "function") Memu.ready();
