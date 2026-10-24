/* Shared public benchmark loaders, canonical model names and MemuAI Index maths.
 * Embedded by the module host for Catalog and Benchmarks; never executes remote JS.
 */
window.MemuBenchmarkData = {
  create(Memu, options = {}) {
    const state = options.state || { hideDeprecated: true, cache: {arena_nav:null, arena:{}, aa:null, livebench:null, simple:null, eq:null}, loading:new Set(), errors:{} };
    const notify = options.onChange || (() => {});
    const onDirty = options.onDirty || (() => {});
    const t = (key, vars) => Memu.t(key,vars);
    const tt = (key,fallback,vars) => Memu.i18n.has(key) ? t(key,vars) : fallback;
    const TTL = 6 * 60 * 60 * 1000;
    const esc = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const num = (value,digits=0) => value == null || !Number.isFinite(+value) ? "—" : Memu.i18n.number(+value,{maximumFractionDigits:digits,minimumFractionDigits:digits});
    const compact = value => value >= 1e6 ? `${+(value/1e6).toFixed(2)}M` : value >= 1e3 ? `${Math.round(value/1e3)}K` : String(value || 0);
/** One name for one model across sites: "Claude Opus 5.5 (high)", "claude-opus-5-5-20260901-thinking" → "claude-opus-5.5". */
const MODE_WORDS = new Set(["thinking", "reasoning", "non", "high", "low", "medium", "max", "xhigh", "minimal", "latest", "preview",
  "exp", "experimental", "instruct", "chat", "it", "public", "vertex", "beta", "effort", "agent", "adaptive", "default", "fast",
  "fallback", "extended", "mode", "free"]);
function canon(name) {
  let text = String(name || "").toLowerCase().split("/").pop();
  // "(high)", "(Adaptive Reasoning, Max Effort)" describe a run mode, not a different model.
  text = text.replace(/\([^)]*\)/g, " ").replace(/[\s_:]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
  for (let pass = 0; pass < 4; pass += 1) {
    text = text.replace(/-(?:20\d{2}-?\d{2}-?\d{2}|\d{6,8})$/, "");
    const parts = text.split("-");
    while (parts.length > 1 && (MODE_WORDS.has(parts[parts.length - 1]) || /^\d+k$/.test(parts[parts.length - 1]))) parts.pop();
    text = parts.join("-");
  }
  return text.replace(/(\d)-(?=\d(?:[^0-9]|$))/g, "$1.").replace(/^-|-$/g, "");
}

/** Keep source aliases consistent; ambiguous cross-vendor names are not merged into an index. */
function canonicalOrg(value) {
  const key=String(value||"").toLowerCase().replace(/[^a-z0-9]/g,"");
  return ({googledeepmind:"google",deepmind:"google",metallama:"meta",metaai:"meta",metaplatforms:"meta",mistralai:"mistral",xai:"xai",x:"xai",
    alibaba:"qwen",alibabacloud:"qwen",qwenteam:"qwen",llama:"meta",moonshotai:"moonshot",zhipuai:"zai",zhipu:"zai"})[key]||key;
}

async function fetchText(url) {
  const result = await Memu.fetch({ url, raw: true, limit: 4800000 });
  const text = result && typeof result.text === "string" ? result.text : "";
  if (!text) throw new Error(t("error.empty"));
  if (result.truncated) throw new Error(Memu.lang === "en" ? "The source response was truncated" : "Ответ источника превышает лимит размера");
  return text;
}

/** Text of the React Server Components stream embedded in a Next.js page. */
function rscText(html) {
  const pattern = /self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)/g;
  let out = "";
  let match;
  while ((match = pattern.exec(html))) {
    try { out += JSON.parse(`"${match[1]}"`); } catch (error) { /* обрывок потока */ }
  }
  return out;
}

/** The JSON array or object that starts at the first bracket after `from`. */
function extractJson(text, from, open = "[") {
  const start = text.indexOf(open, Math.max(0, from));
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === "[" || char === "{") depth += 1;
    else if (char === "]" || char === "}") {
      depth -= 1;
      if (depth === 0) {
        try { return JSON.parse(text.slice(start, index + 1)); } catch (error) { return null; }
      }
    }
  }
  return null;
}

const fresh = (entry) => entry && entry.at && Date.now() - entry.at < TTL;

const inflight = new Map();
/** Runs one load at a time per key; a second caller waits for the same load. */
function withLoading(key, job) {
  if (inflight.has(key)) return inflight.get(key);
  state.loading.add(key);
  delete state.errors[key];
  notify();
  const run = (async () => {
    try {
      await job();
    } catch (error) {
      state.errors[key] = error && error.message ? error.message : String(error);
    } finally {
      state.loading.delete(key);
      inflight.delete(key);
      notify();
    }
  })();
  inflight.set(key, run);
  return run;
}

/* ---- arena.ai */

const ARENAS = ["text", "code/webdev", "vision", "document", "search", "text-to-image", "image-edit",
  "text-to-video", "image-to-video", "video-edit", "agent", "code/image-to-webdev"];

async function loadArenaNav(force) {
  if (!force && fresh(state.cache.arena_nav)) return;
  await withLoading("arena_nav", async () => {
    const html = await fetchText("https://arena.ai/leaderboard");
    const found = new Set();
    const pattern = /\/leaderboard\/([a-z0-9-]+(?:\/[a-z0-9-]+)?)(?=["'?#\\])/g;
    let match;
    while ((match = pattern.exec(html))) {
      const slug = match[1];
      if (!/pareto|overview/.test(slug)) found.add(slug);
    }
    const ordered = ARENAS.filter((slug) => found.has(slug)).concat([...found].filter((slug) => !ARENAS.includes(slug)).sort());
    state.cache.arena_nav = { at: Date.now(), slugs: ordered.length ? ordered : ARENAS };
    onDirty("arena_nav");
    
  });
}

function arenaSlugs() {
  return (state.cache.arena_nav && state.cache.arena_nav.slugs) || ARENAS;
}

/* Арена агентов устроена иначе, чем остальные: вместо одной доски с рейтингом Эло — снимок
   («snapshot») со средним приростом по пяти сигналам поведения и отдельная таблица на каждый
   сигнал. Сигналы показываются как категории доски; их id начинаются с «signal-». */
const SIGNAL = "signal-";

function parseAgentArena(text, signal) {
  const at = text.indexOf('"snapshot":{');
  if (at < 0) return null;
  const overall = extractJson(text, text.indexOf('"rows":', at));
  const signals = extractJson(text, text.indexOf('"signals":', at));
  if (!Array.isArray(overall) || !overall.length) return null;
  const categories = (Array.isArray(signals) ? signals : []).filter((row) => row && row.name && Array.isArray(row.entries))
    .map((row) => ({ id: SIGNAL + row.name, title: row.displayName || row.name, group: "", description: row.description || "" }));
  const meta = new Map(overall.map((row) => [row.contenderName, row]));
  let rows;
  let description = (text.match(/"arena":\{"slug":"[^"]*","title":"[^"]*","description":"((?:[^"\\]|\\.)*)"/) || [])[1] || "";
  if (signal) {
    const found = (signals || []).find((row) => row && row.name === signal);
    if (!found) return null;
    description = found.description || description;
    rows = found.entries.map((row) => ({ ...(meta.get(row.contenderName) || {}), ...row, lo: row.ciLower, hi: row.ciUpper }));
  } else {
    rows = overall.map((row) => {
      const value = row.avgScore ? row.avgScore.value : null;
      const ci = row.avgScore ? row.avgScore.ci || 0 : 0;
      return { ...row, score: value, lo: value === null ? null : value - ci, hi: value === null ? null : value + ci,
        rankUpper: row.rankSpread ? row.rankSpread.min : undefined, rankLower: row.rankSpread ? row.rankSpread.max : undefined };
    });
  }
  return {
    description, categories,
    rows: rows.filter((row) => Number.isFinite(row.score)).map((row) => ({
      rank: row.rank, rankUpper: row.rankUpper, rankLower: row.rankLower,
      model: row.model || "", org: row.modelOrganization || "", score: row.score, lo: row.lo, hi: row.hi,
      votes: row.sessions, license: row.license || "", priceIn: row.inputPricePerMillion, priceOut: row.outputPricePerMillion,
      context: row.contextLength, url: row.modelUrl || "",
    })),
  };
}

async function loadArena(slug, category, force) {
  const key = category ? `${slug}::${category}` : slug;
  if (!force && fresh(state.cache.arena[key])) return;
  await withLoading(`arena:${key}`, async () => {
    const signal = category && category.startsWith(SIGNAL) ? category.slice(SIGNAL.length) : "";
    const url = `https://arena.ai/leaderboard/${slug}${category && !signal ? "/" + category : ""}`;
    const text = rscText(await fetchText(url));
    const board = text.indexOf('"leaderboard":{"arenaSlug"');
    const entries = board >= 0 ? extractJson(text, text.indexOf('"entries":', board)) : null;
    if (!Array.isArray(entries) || !entries.length) {
      const agent = parseAgentArena(text, signal);
      if (!agent || !agent.rows.length) throw new Error(t("error.format", { source: "arena.ai" }));
      state.cache.arena[key] = { at: Date.now(), rows: agent.rows, description: agent.description, url,
        categories: agent.categories };
      trimArenaCache();
      onDirty("arena");
      
      return;
    }
    const categories = [];
    const groups = new Set();
    const catPattern = /\["([a-z0-9-]+)",\{"name":"\1","title":"((?:[^"\\]|\\.)*)"(?:,"icon":"(?:[^"\\]|\\.)*")?(?:,"group":"([a-z0-9-]+)")?\}\]/g;
    let match;
    while ((match = catPattern.exec(text))) {
      if (match[3]) groups.add(match[3]);
      if (!categories.some((row) => row.id === match[1])) categories.push({ id: match[1], title: match[2], group: match[3] || "" });
    }
    const description = (text.match(/"arena":\{"slug":"[^"]*","title":"[^"]*","description":"((?:[^"\\]|\\.)*)"/) || [])[1] || "";
    const rows = entries.map((row) => ({
      rank: row.rank, rankUpper: row.rankUpper, rankLower: row.rankLower,
      model: row.modelDisplayName || row.modelKey || "", org: row.modelOrganization || "",
      score: row.rating, lo: row.ratingLower, hi: row.ratingUpper, votes: row.votes,
      license: row.license || "", priceIn: row.inputPricePerMillion, priceOut: row.outputPricePerMillion,
      context: row.contextLength, url: row.modelUrl || "",
    }));
    state.cache.arena[key] = { at: Date.now(), rows, description, url,
      categories: categories.filter((row) => !groups.has(row.id)) };
    trimArenaCache();
    onDirty("arena");
    
  });
}

/** Не больше 14 наборов в кэше: старые категории вытесняются. */
function trimArenaCache() {
  const keys = Object.keys(state.cache.arena).sort((a, b) => (state.cache.arena[b].at || 0) - (state.cache.arena[a].at || 0));
  for (const old of keys.slice(14)) delete state.cache.arena[old];
}

/* ---- Artificial Analysis */

async function loadAA(force) {
  if (!force && fresh(state.cache.aa)) return;
  await withLoading("aa", async () => {
    const text = rscText(await fetchText("https://artificialanalysis.ai/leaderboards/models"));
    // The page carries several "models" arrays; the leaderboard is the one with metrics.
    let models = null;
    for (let at = text.indexOf('"models":[{"slug"'); at >= 0 && !models; at = text.indexOf('"models":[{"slug"', at + 10)) {
      if (!text.slice(at, at + 4000).includes('"intelligenceIndex"')) continue;
      const rows = extractJson(text, at + 9);
      if (Array.isArray(rows) && rows.length) models = rows;
    }
    if (!Array.isArray(models) || !models.length) throw new Error(t("error.format", { source: "Artificial Analysis" }));
    const value = (row, key) => (typeof row[key] === "number" && Number.isFinite(row[key]) ? row[key] : null);
    const rows = models.map((row) => ({
      model: row.name || row.shortName || row.slug, slug: row.slug, org: row.modelCreatorName || "",
      deprecated: !!row.deprecated, reasoning: !!row.isReasoning, open: !!row.isOpenWeights, context: value(row, "contextWindowTokens"),
      m: {
        intelligence: value(row, "intelligenceIndex"), gpqa: value(row, "gpqa"), hle: value(row, "hle"), scicode: value(row, "scicode"),
        lcr: value(row, "lcr"),
        mmmu: value(row, "mmmuPro"), omniscience: value(row, "omniscience"), gdpval: value(row, "gdpvalNormalized"),
        critpt: value(row, "critpt"), speed: value(row, "medianOutputTokensPerSecond"), ttft: value(row, "medianTimeToFirstTokenSeconds"),
        priceIn: value(row, "price1mInputTokens"), priceOut: value(row, "price1mOutputTokens"),
      },
    }));
    state.cache.aa = { at: Date.now(), rows };
    onDirty("aa");
    
  });
}

/* ---- LiveBench */

async function loadLiveBench(force) {
  if (!force && fresh(state.cache.livebench)) return;
  await withLoading("livebench", async () => {
    const index = await fetchText("https://livebench.ai/");
    const script = (index.match(/src="\.?\/?(static\/js\/main\.[a-z0-9]+\.js)"/) || [])[1];
    if (!script) throw new Error(t("error.format", { source: "LiveBench" }));
    const js = await fetchText(`https://livebench.ai/${script}`);
    const dates = [...new Set((js.match(/\["20\d\d-\d\d-\d\d"(?:,"20\d\d-\d\d-\d\d")+\]/g) || [])
      .flatMap((list) => { try { return JSON.parse(list); } catch (error) { return []; } }))].sort().reverse();
    const names = {};
    const namePattern = /"([a-z0-9._:-]+)":\{url:"[^"]*"(?:,huggingface:"[^"]*")?,organization:"([^"]*)",displayName:"([^"]*)"/g;
    let match;
    while ((match = namePattern.exec(js))) names[match[1]] = { org: match[2], name: match[3] };
    const variantPattern = /rawName:"([^"]+)",displayName:"([^"]+)"/g;
    while ((match = variantPattern.exec(js))) if (!names[match[1]]) names[match[1]] = { org: "", name: match[2] };
    let table = "", categories = null, release = "";
    for (const date of dates.slice(0, 5)) {
      const stamp = date.replace(/-/g, "_");
      try {
        const csv = await fetchText(`https://livebench.ai/table_${stamp}.csv`);
        if (!csv.startsWith("model,")) continue;
        categories = JSON.parse(await fetchText(`https://livebench.ai/categories_${stamp}.json`));
        table = csv;
        release = date;
        break;
      } catch (error) { /* эта дата не опубликована — пробуем предыдущую */ }
    }
    if (!table || !categories) throw new Error(t("error.format", { source: "LiveBench" }));
    const lines = table.trim().split(/\r?\n/);
    const header = lines.shift().split(",");
    const rows = lines.map((line) => {
      const cells = line.split(",");
      const scores = {};
      header.forEach((column, index) => { if (index) scores[column] = cells[index] === "" ? null : +cells[index]; });
      const byCategory = {};
      for (const [category, tasks] of Object.entries(categories)) {
        const values = tasks.map((task) => scores[task]).filter((value) => Number.isFinite(value));
        byCategory[category] = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
      }
      const present = Object.values(byCategory).filter((value) => value !== null);
      const info = names[cells[0]] || {};
      return { model: info.name || cells[0], key: cells[0], org: info.org || "", global: present.length ? present.reduce((a, b) => a + b, 0) / present.length : null,
        complete: present.length === Object.keys(categories).length, cat: byCategory };
    });
    state.cache.livebench = { at: Date.now(), release, categories: Object.keys(categories), category_tasks: categories, rows };
    onDirty("livebench");
    
  });
}

/* ---- SimpleBench */

async function loadSimple(force) {
  if (!force && fresh(state.cache.simple)) return;
  await withLoading("simple", async () => {
    const js = await fetchText("https://simple-bench.com/static/js/leaderboard-data.js");
    const pattern = /\{\s*rank:\s*"([^"]*)",\s*model:\s*"([^"]*)",\s*score:\s*"([^"]*)",\s*organization:\s*"([^"]*)",\s*dateAdded:\s*(?:"([^"]*)"|null)\s*\}/g;
    const rows = [];
    let match;
    while ((match = pattern.exec(js))) {
      rows.push({ model: match[2].trim(), score: parseFloat(match[3]), org: match[4].trim(), date: match[5] || "",
        baseline: match[1].trim() === "-" });
    }
    if (!rows.length) throw new Error(t("error.format", { source: "SimpleBench" }));
    state.cache.simple = { at: Date.now(), rows: rows.filter((row) => Number.isFinite(row.score)) };
    onDirty("simple");
    
  });
}

/* ---- EQ-Bench */

async function loadEq(force) {
  if (!force && fresh(state.cache.eq)) return;
  await withLoading("eq", async () => {
    const page = await fetchText("https://eqbench.com/");
    const script = (page.match(/src="\.?\/?(eqbench4\/eqbench4_data\.js[^"]*)"/) || [])[1] || "eqbench4/eqbench4_data.js";
    const js = await fetchText(`https://eqbench.com/${script}`);
    const data = extractJson(js, js.indexOf("EQBENCH4_DATA"), "{");
    const models = data && Array.isArray(data.models) ? data.models : [];
    const rows = models.map((row) => ({ model: row.display || row.model || "", score: row.elo, lo: row.ci_low, hi: row.ci_high,
      scenarios: row.n_scenarios })).filter((row) => row.model && Number.isFinite(row.score)).sort((a, b) => b.score - a.score);
    if (!rows.length) throw new Error(t("error.format", { source: "EQ-Bench" }));
    state.cache.eq = { at: Date.now(), rows, generated: data.generated_at || "" };
    onDirty("eq");
    
  });
}

/* ------------------------------------------------------------ каталог досок */

const AA_METRICS = [
  ["intelligence", "index", true], ["gpqa", "pct", true], ["hle", "pct", true], ["scicode", "pct", true],
  ["lcr", "pct", true],
  ["mmmu", "pct", true], ["gdpval", "pct", true], ["critpt", "pct", true], ["omniscience", "index", true],
  ["speed", "speed", true], ["ttft", "seconds", false], ["price", "price", false],
];

function arenaTitle(slug) {
  return tt(`arena.${slug.replace(/\//g, "_")}`, slug.split("/").pop().replace(/-/g, " ").replace(/^\w/, (c) => c.toUpperCase()));
}

/** Every board the module can show, grouped by source. */
function catalog() {
  const groups = [
    { id: "memu", title: "MemuAI", boards: [{ id: "memu:index", title: t("index.title") }, { id: "memu:own", title: t("own.title") }] },
    { id: "arena", title: "arena.ai", site: "https://arena.ai/leaderboard",
      boards: arenaSlugs().map((slug) => ({ id: `arena:${slug}`, title: arenaTitle(slug) })) },
    { id: "aa", title: "Artificial Analysis", site: "https://artificialanalysis.ai/leaderboards/models",
      boards: AA_METRICS.map(([key]) => ({ id: `aa:${key}`, title: t(`aa.${key}`) })) },
    { id: "livebench", title: "LiveBench", site: "https://livebench.ai",
      boards: [{ id: "livebench:global", title: t("lb.global") }].concat(((state.cache.livebench || {}).categories || [])
        .map((name) => ({ id: `livebench:${name}`, title: tt(`lb.${name.replace(/\s+/g, "_").toLowerCase()}`, name) }))) },
    { id: "other", title: t("group.other"), boards: [
      { id: "simple:simplebench", title: "SimpleBench", site: "https://simple-bench.com" },
      { id: "eq:eqbench4", title: "EQ-Bench 4", site: "https://eqbench.com" },
    ] },
  ];
  return groups;
}

function boardTitle(id) {
  for (const group of catalog()) {
    const board = group.boards.find((row) => row.id === id);
    if (board) return board.title;
  }
  return id;
}

/** Loads what a board needs; `category` is an arena.ai category id. */
async function ensure(id, force = false) {
  const [source, rest] = id.split(/:(.*)/s);
  if (source === "arena") {
    const [slug, category] = rest.split("::");
    return Promise.all([loadArenaNav(force), loadArena(slug, category, force)]);
  }
  if (source === "memu") return rest === "index" ? loadIndex(force) : null;
  if (source === "aa") return loadAA(force);
  if (source === "livebench") return loadLiveBench(force);
  if (source === "simple") return loadSimple(force);
  if (source === "eq") return loadEq(force);
  return null;
}

function loadingKeyFor(id) {
  const [source, rest] = id.split(/:(.*)/s);
  return source === "arena" ? `arena:${rest}` : source;
}

const pct = (value) => (value === null || value === undefined ? null : value * 100);

/* ------------------------------------------------------------ MemuAI Index */

/* Сводный рейтинг. У площадок несравнимые шкалы (Elo арены, проценты тестов,
 * индекс AA), поэтому каждая доска сначала превращается в процентиль: какая
 * доля моделей этой доски хуже данной (ничьи делятся пополам). Затем —
 * взвешенное среднее только по доскам, где модель есть. Отсутствие результата
 * не является плохим результатом: покрытие показывается отдельно и не
 * уменьшает балл модели. Для включения нужны хотя бы два источника.
 * Веса: человеческие предпочтения arena.ai — 40 %, независимые тесты — 60 %.
 * Составные части индекса AA (GPQA, HLE и т. д.) отдельно не берутся, чтобы не
 * считать одно и то же дважды. */
const INDEX_SOURCES = [
  ["arena:text", 0.22], ["arena:code/webdev", 0.12], ["arena:vision", 0.06],
  ["aa:intelligence", 0.26], ["livebench:global", 0.18], ["simple:simplebench", 0.10], ["eq:eqbench4", 0.06],
];
const INDEX_ARENAS = INDEX_SOURCES.map(([id]) => id).filter((id) => id.startsWith("arena:"));
const INDEX_MIN_SOURCES = 2;
const INDEX_MIN_ROWS = 5;

/** canon(model) → {p: 0..100, name, org} for one board; the best variant of a model wins. */
function percentiles(id) {
  const data = boardData(id);
  if (!data) return null;
  const rows = data.rows.filter((row) => !row.baseline && Number.isFinite(row.score));
  if (rows.length < INDEX_MIN_ROWS) return null;
  const better = (a, b) => (data.score.higher ? a > b : a < b);
  const out = new Map();
  const organizations = new Map();
  for (const row of rows) {
    const key=canon(row.model),org=canonicalOrg(row.org);
    if(org) { if(!organizations.has(key))organizations.set(key,new Set());organizations.get(key).add(org); }
  }
  for (const row of rows) {
    const key = canon(row.model);
    if (!key || organizations.get(key)?.size>1) continue;
    let below = 0, ties = 0;
    for (const other of rows) {
      if (other.score === row.score) ties += 1;
      else if (better(row.score, other.score)) below += 1;
    }
    const p = ((below + (ties - 1) / 2) / (rows.length - 1)) * 100;
    const prev = out.get(key);
    if (!prev || p > prev.p) out.set(key, { p, name: row.model, org: row.org || "" });
  }
  return out;
}

/** Readable names ("Claude Opus 5") beat slugs ("claude-opus-5-20260101"); of two readable ones the shorter
 * wins — "Claude Opus 5 Max Effort" is a run mode of the same model. */
function nicer(a, b) {
  const spaced = (name) => /\s/.test(name);
  if (spaced(a) !== spaced(b)) return spaced(b) ? b : a;
  return b.length < a.length ? b : a;
}

function indexData() {
  const parts = INDEX_SOURCES.map(([id, weight]) => ({ id, weight, map: percentiles(id) })).filter((part) => part.map);
  if (parts.length < 2) return null;
  const organizations=new Map();
  for(const part of parts)for(const [key,hit] of part.map) {
    const org=canonicalOrg(hit.org);
    if(org) { if(!organizations.has(key))organizations.set(key,new Set());organizations.get(key).add(org); }
  }
  // Модель, у которой на AA все варианты помечены устаревшими, скрывается вместе с ними.
  const gone = new Set();
  if (state.hideDeprecated && state.cache.aa) {
    const alive = new Set();
    for (const row of state.cache.aa.rows) (row.deprecated ? gone : alive).add(canon(row.model));
    for (const key of alive) gone.delete(key);
  }
  const models = new Map();
  for (const part of parts) {
    for (const [key, hit] of part.map) {
      if (gone.has(key) || organizations.get(key)?.size>1) continue;
      const entry = models.get(key) || { key, model: hit.name, org: hit.org, parts: {} };
      entry.model = nicer(entry.model, hit.name);
      if (!entry.org) entry.org = hit.org;
      entry.parts[part.id] = hit.p;
      models.set(key, entry);
    }
  }
  const total = parts.reduce((sum, part) => sum + part.weight, 0);
  const rows = [];
  for (const entry of models.values()) {
    let sum = 0, weight = 0, count = 0;
    for (const part of parts) {
      if (!(part.id in entry.parts)) continue;
      sum += part.weight * entry.parts[part.id];
      weight += part.weight;
      count += 1;
    }
    if (count < INDEX_MIN_SOURCES) continue;
    rows.push({ ...entry, score: sum / weight, raw: sum / weight, coverage: weight / total, count });
  }
  // Equal scores: the model with more evidence stands first (a missing source never lowers a score).
  rows.sort((a, b) => b.score - a.score || b.coverage - a.coverage || a.model.localeCompare(b.model));
  return { rows, parts, total };
}

function indexShort(id) {
  return tt(`index.src.${id.replace(/[:/]/g, "_")}`, boardTitle(id));
}

function indexCell(value) {
  if (!Number.isFinite(value)) return `<span class="bm-pct none">—</span>`;
  const tone = value >= 90 ? "top" : value >= 70 ? "good" : value >= 40 ? "mid" : "low";
  return `<span class="bm-pct ${tone}">${Math.round(value)}</span>`;
}

async function loadIndex(force = false) {
  const jobs = [() => loadArenaNav(force), () => loadAA(force), () => loadLiveBench(force), () => loadSimple(force), () => loadEq(force),
    ...INDEX_ARENAS.map((id) => () => ensure(id, force))];
  let next = 0;
  await Promise.all([0, 1, 2].map(async () => {
    while (next < jobs.length) {
      const job = jobs[next];
      next += 1;
      await job();
    }
  }));
}

/** Rows ready to show: {rank, model, org, score, lo, hi, cols: {...}} plus the board's column spec. */
function boardData(id) {
  const [source, rest] = id.split(/:(.*)/s);
  if (source === "memu") {
    const index = indexData();
    if (!index) return null;
    const stamps = index.parts.map((part) => {
      const [kind, slug] = part.id.split(/:(.*)/s);
      return kind === "arena" ? (state.cache.arena[slug] || {}).at : (state.cache[{ aa: "aa", livebench: "livebench", simple: "simple", eq: "eq" }[kind]] || {}).at;
    }).filter(Boolean);
    return {
      rows: index.rows,
      score: { label: t("index.score"), fmt: (v) => num(v, 1), higher: true },
      cols: [
        { key: "coverage", label: t("index.coverage"), fmt: (row) => `<span class="bm-cover" title="${esc(t("index.coverage.title", { share: Math.round(row.coverage * 100) }))}">${row.count}/${index.parts.length} · ${Math.round(row.coverage * 100)}%</span>`, sort: (row) => row.coverage },
        ...index.parts.map((part) => ({ key: `p:${part.id}`, label: indexShort(part.id), fmt: (row) => indexCell(row.parts[part.id]), sort: (row) => row.parts[part.id] ?? -Infinity })),
      ],
      updated: stamps.length ? Math.min(...stamps) : 0,
      note: t("index.note", { count: index.parts.length, total: INDEX_SOURCES.length }),
      method: index,
    };
  }
  if (source === "arena") {
    const entry = state.cache.arena[rest];
    if (!entry) return null;
    const unit = entry.rows.some((row) => Math.abs(row.score) > 5) ? "elo" : "net";
    return {
      rows: entry.rows.map((row) => ({ ...row, rankText: row.rankUpper && row.rankLower && row.rankUpper !== row.rankLower ? `${row.rankUpper}–${row.rankLower}` : "" })),
      score: { label: unit === "elo" ? t("col.rating") : t("col.net"), fmt: (v) => (unit === "elo" ? num(v) : num(v * 100, 1)), higher: true },
      cols: [
        { key: "votes", label: t("col.votes"), fmt: (row) => num(row.votes) },
        { key: "price", label: t("col.price"), fmt: (row) => (row.priceIn != null ? `$${num(row.priceIn, 2)} / $${num(row.priceOut, 2)}` : "—"), sort: (row) => row.priceIn ?? Infinity },
        { key: "context", label: t("col.context"), fmt: (row) => (row.context ? compact(row.context) : "—") },
        { key: "license", label: t("col.license"), fmt: (row) => esc(row.license || "—") },
      ],
      ci: true, updated: entry.at, source: entry.url, description: entry.description, categories: entry.categories,
    };
  }
  if (source === "aa") {
    const entry = state.cache.aa;
    if (!entry) return null;
    const spec = AA_METRICS.find(([key]) => key === rest) || AA_METRICS[0];
    const [key, kind, higher] = spec;
    const metric = (row) => (key === "price"
      ? (row.m.priceIn != null && row.m.priceOut != null && row.m.priceIn + row.m.priceOut > 0 ? (3 * row.m.priceIn + row.m.priceOut) / 4 : null)
      : row.m[key]);
    const shown = entry.rows.filter((row) => Number.isFinite(metric(row)) && !(state.hideDeprecated && row.deprecated));
    const fmt = { index: (v) => num(v, 1), pct: (v) => `${num(pct(v), 1)}%`, speed: (v) => `${num(v)} t/s`,
      seconds: (v) => `${num(v, 2)} ${t("unit.s")}`, price: (v) => `$${num(v, 2)}` }[kind];
    return {
      rows: shown.map((row) => ({ ...row, score: metric(row) })),
      score: { label: t(`aa.${key}`), fmt, higher },
      cols: [
        { key: "flags", label: "", fmt: (row) => `${row.reasoning ? `<span class="bm-tag">${esc(t("tag.reasoning"))}</span>` : ""}${row.open ? `<span class="bm-tag ok">${esc(t("tag.open"))}</span>` : ""}${row.deprecated ? `<span class="bm-tag dim">${esc(t("tag.deprecated"))}</span>` : ""}`, sortable: false },
        { key: "intel", label: t("aa.intelligence.short"), fmt: (row) => num(row.m.intelligence, 1), sort: (row) => row.m.intelligence ?? -Infinity },
        { key: "speed", label: t("col.speed"), fmt: (row) => (row.m.speed ? `${num(row.m.speed)} t/s` : "—"), sort: (row) => row.m.speed ?? -Infinity },
        { key: "price", label: t("col.price"), fmt: (row) => (row.m.priceIn != null ? `$${num(row.m.priceIn, 2)} / $${num(row.m.priceOut, 2)}` : "—"), sort: (row) => row.m.priceIn ?? Infinity },
        { key: "context", label: t("col.context"), fmt: (row) => (row.context ? compact(row.context) : "—") },
      ],
      updated: entry.at, source: "https://artificialanalysis.ai/leaderboards/models", deprecatedToggle: true,
    };
  }
  if (source === "livebench") {
    const entry = state.cache.livebench;
    if (!entry) return null;
    const value = (row) => (rest === "global" ? row.global : row.cat[rest]);
    return {
      rows: entry.rows.filter((row) => value(row) !== null && value(row) !== undefined).map((row) => ({ ...row, score: value(row) })),
      score: { label: rest === "global" ? t("lb.global") : tt(`lb.${rest.replace(/\s+/g, "_").toLowerCase()}`, rest), fmt: (v) => num(v, 1), higher: true },
      cols: (rest === "global" ? entry.categories.map((name) => ({ key: `c:${name}`, label: tt(`lb.${name.replace(/\s+/g, "_").toLowerCase()}`, name),
        fmt: (row) => num(row.cat[name], 1), sort: (row) => row.cat[name] ?? -Infinity })) : []),
      updated: entry.at, source: "https://livebench.ai", note: entry.release ? t("lb.release", { date: entry.release }) : "",
    };
  }
  if (source === "simple") {
    const entry = state.cache.simple;
    if (!entry) return null;
    return {
      rows: entry.rows,
      score: { label: t("col.score"), fmt: (v) => `${num(v, 1)}%`, higher: true },
      cols: [{ key: "date", label: t("col.added"), fmt: (row) => esc(row.date || "—") }],
      updated: entry.at, source: "https://simple-bench.com",
    };
  }
  if (source === "eq") {
    const entry = state.cache.eq;
    if (!entry) return null;
    return {
      rows: entry.rows,
      score: { label: "Elo", fmt: (v) => num(v), higher: true },
      cols: [{ key: "scenarios", label: t("col.scenarios"), fmt: (row) => num(row.scenarios) }],
      ci: true, updated: entry.at, source: "https://eqbench.com",
    };
  }
  return null;
}


    return { state, canon, canonicalOrg, fetchText, rscText, extractJson, fresh, ARENAS, SIGNAL, AA_METRICS, INDEX_SOURCES, INDEX_ARENAS, INDEX_MIN_SOURCES, INDEX_MIN_ROWS,
      loadArenaNav, arenaSlugs, parseAgentArena, loadArena, loadAA, loadLiveBench, loadSimple, loadEq,
      arenaTitle, catalog, boardTitle, ensure, loadingKeyFor, percentiles, indexData, indexShort, indexCell, loadIndex, boardData };
  }
};
