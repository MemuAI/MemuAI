/* @memu-shared:benchmarks */

const t = (key, vars) => Memu.t(key, vars);
const saved = Memu.saved || {};
const $ = (selector) => document.querySelector(selector);
const TTL = 6 * 60 * 60 * 1000;
const SOURCE = "https://openrouter.ai/api/v1/models";
const MODELS_DEV = "https://models.dev/api.json?type=all";
const CACHE_SCHEMA = 3;
const MAX_COMPARE = 3;
const PAGE = 60;

const INPUTS = ["text", "image", "file", "audio", "video"];
const OUTPUTS = ["text", "image", "audio"];
const FEATURES = ["tools", "reasoning", "structured", "web"];
const CONTEXTS = [0, 32000, 128000, 256000, 1000000];
const PRICES = ["any", "free", "1", "5", "15"];
const SORTS = ["newest", "price-asc", "price-desc", "context", "name"];

const SOURCES = ["openrouter", "models.dev"];
const DEFAULT_FILTERS = { inputs: [], outputs: [], features: [], context: 0, price: "any", orgs: [], sources: [], hideExpiring: false };
const freshFilters = () => ({ ...DEFAULT_FILTERS, inputs: [], outputs: [], features: [], orgs: [], sources: [] });

function sanitizeFilters(raw) {
  const f = freshFilters();
  if (!raw || typeof raw !== "object") return f;
  const list = (value, allowed) => Array.isArray(value) ? [...new Set(value.filter((item) => typeof item === "string" && (!allowed || allowed.includes(item))))] : [];
  f.inputs = list(raw.inputs, INPUTS); f.outputs = list(raw.outputs, OUTPUTS); f.features = list(raw.features, FEATURES);
  f.orgs = list(raw.orgs).slice(0, 200); f.sources = list(raw.sources, SOURCES);
  f.context = CONTEXTS.includes(Number(raw.context)) ? Number(raw.context) : 0;
  f.price = PRICES.includes(raw.price) ? raw.price : "any";
  f.hideExpiring = raw.hideExpiring === true;
  return f;
}

const state = {
  filters: sanitizeFilters(saved.filters),
  sort: SORTS.includes(saved.sort) ? saved.sort : "newest",
  compare: Array.isArray(saved.compare) ? saved.compare.slice(0, MAX_COMPARE) : [],
  cache: saved.cache && Array.isArray(saved.cache.rows) ? saved.cache : null,
  query: "",
  mine: false,
  mySet: null,
  loading: false,
  error: "",
  shown: PAGE,
  orgQuery: "",
  view: "list",
  layout: saved.layout === "rows" ? "rows" : "cards",
  filtersOpen: false,
};

let saveTimer = 0;
let cacheDirty = false;
let benchmarkDirty = false;
function remember(withCache = false, now = false) {
  clearTimeout(saveTimer);
  if (withCache) cacheDirty = true;
  const flush = () => {
    const fields = { filters: state.filters, sort: state.sort, compare: state.compare, layout: state.layout };
    if (cacheDirty) fields.cache = state.cache;
    if (benchmarkDirty) fields.cache_benchmarks = benchmarkState.cache;
    fields.benchmark_boards = benchmarkBoards;
    cacheDirty = false;
    benchmarkDirty = false;

    Memu.save(fields).catch(() => Memu.save({ filters: state.filters, sort: state.sort, compare: state.compare, layout: state.layout, benchmark_boards: benchmarkBoards }).catch(() => {}));
  };
  if (now) flush(); else saveTimer = setTimeout(flush, 400);
}

window.addEventListener("pagehide", () => remember(false, true));

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (value, digits = 0) => Memu.i18n.number(+value, { maximumFractionDigits: digits, minimumFractionDigits: 0 });

function compact(value) {
  const n = Number(value) || 0;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(n % 1e6 ? 2 : 0)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

function perMillion(value) {
  if (value === undefined || value === null || value === "") return undefined;
  const n = Number(value) * 1e6;
  if (!["number", "string"].includes(typeof value) || String(value).trim() === "" || !Number.isFinite(n) || n < 0) return null;
  return n;
}

function money(value) {
  if (value === undefined) return "—";
  if (value === null) return t("price.varies");
  if (value === 0) return t("price.free");
  return `$${num(value, value < 1 ? 3 : 2)}`;
}

function dateText(seconds) {
  if (!seconds) return "—";
  return formatter("date", { day: "numeric", month: "short", year: "numeric" }).format(new Date(seconds * 1000));
}

function canon(name) { return benchmarks.canon(name); }

function isMine(row) {
  return !!state.mySet && state.mySet.has(canon(row.id));
}

const benchmarkState = {
  hideDeprecated: true, loading: new Set(), errors: {},
  cache: {arena_nav:null, arena:{}, aa:null, livebench:null, simple:null, eq:null,
    ...(saved.cache_benchmarks && typeof saved.cache_benchmarks === "object" ? saved.cache_benchmarks : {})},
};
if (!benchmarkState.cache.arena || typeof benchmarkState.cache.arena !== "object" || Array.isArray(benchmarkState.cache.arena)) benchmarkState.cache.arena = {};
const DEFAULT_BENCHMARK_BOARDS = ["memu:index","arena:text","arena:code/webdev","aa:intelligence","livebench:global","simple:simplebench","eq:eqbench4"];
let benchmarkBoards = Array.isArray(saved.benchmark_boards)
  ? saved.benchmark_boards.filter(id=>typeof id === "string").slice(0,24) : DEFAULT_BENCHMARK_BOARDS.slice();
const benchmarkVariants = new Map();
let benchmarkLoaded = false;
const benchmarkMemu = {
  ...Memu,
  t: (key, vars) => Memu.t("bm."+key,vars),
  i18n: { ...Memu.i18n, has:key=>typeof Memu.i18n.has === "function" && Memu.i18n.has("bm."+key) },
};
const benchmarks = window.MemuBenchmarkData.create(benchmarkMemu, {
  state: benchmarkState,

  onChange: () => { scheduleBenchmarkPanels(); },
  onDirty: () => { benchmarkDirty = true; remember(); },
});

function benchmarkOrg(value) {
  return benchmarks.canonicalOrg(value);
}
function benchmarkMatches(model, candidate) {
  const names = new Set([canonCached(model.id),canonCached(model.name)]);
  if (!names.has(canonCached(candidate.model))) return false;
  const org=benchmarkOrg(candidate.org);
  return !org || org===benchmarkOrg(model.org) || org===benchmarkOrg(model.slug);
}

const boardMemo = new Map();
function boardRefs(id) {
  const [source, rest] = String(id).split(/:(.*)/s);
  const cache = benchmarkState.cache || {};
  if (source === "memu") return [benchmarkState.hideDeprecated, ...benchmarks.INDEX_SOURCES.flatMap(([part]) => boardRefs(part))];
  if (source === "arena") { const entry = (cache.arena || {})[rest]; return [entry, entry && entry.rows]; }
  const entry = cache[source];
  return [entry, entry && entry.rows, benchmarkState.hideDeprecated];
}
function boardEntry(id) {
  const refs = [Memu.lang, ...boardRefs(id)];
  const known = boardMemo.get(id);
  if (known && known.refs.length === refs.length && known.refs.every((value, index) => value === refs[index])) return known;
  const data = benchmarks.boardData(id);
  const entry = { refs, data, ranked: [], order: new Map(), byCanon: new Map(), firstAt: new Map(), results: new Map() };
  if (data) {
    entry.ranked = data.rows.filter(row=>!row.baseline&&Number.isFinite(row.score)).slice().sort((a,b)=>data.score.higher?b.score-a.score:a.score-b.score);
    entry.ranked.forEach((row, index) => {
      entry.order.set(row, index);
      const key = benchmarks.canon(row.model);
      const list = entry.byCanon.get(key);
      if (list) list.push(row); else entry.byCanon.set(key, [row]);
      if (!entry.firstAt.has(row.score)) entry.firstAt.set(row.score, index);
    });
  }
  boardMemo.set(id, entry);
  return entry;
}
const canonMemo = new Map();
function canonCached(name) {
  const key = String(name || "");
  let value = canonMemo.get(key);
  if (value === undefined) { value = benchmarks.canon(key); if (canonMemo.size > 20000) canonMemo.clear(); canonMemo.set(key, value); }
  return value;
}
function benchmarkResults(model,id) {
  const entry=boardEntry(id);
  const data=entry.data;
  if(!data)return {data:null,rows:[],selected:null,total:0};
  const choice=benchmarkVariants.get(model.id+"|"+id);
  const memoKey=`${model.id}\u0001${model.name}\u0001${model.org}\u0001${model.slug}\u0001${choice||""}`;
  const cached=entry.results.get(memoKey);
  if(cached)return cached;
  const ranked=entry.ranked;

  const keys=[...new Set([canonCached(model.id),canonCached(model.name)])];
  const candidates=keys.flatMap(key=>entry.byCanon.get(key)||[]);
  const variants=[...new Set(candidates)].filter(row=>benchmarkMatches(model,row)).sort((a,b)=>entry.order.get(a)-entry.order.get(b));
  const normalize=name=>String(name).toLowerCase().split("/").pop().replace(/:free$/,"").replace(/[^a-z0-9.]+/g,"-").replace(/^-|-$/g,"");
  const exact=variants.find(row=>[normalize(model.id),normalize(model.name)].includes(normalize(row.model)));
  const requested=variants.find(row=>row.model===choice);
  const selected=requested||exact||variants[0]||null;
  const place=selected ? 1+entry.firstAt.get(selected.score) : null;
  const result={data,rows:variants,selected,place,total:ranked.length,defaultBest:!!selected&&!requested&&!exact,variantMissing:!!choice&&!requested};
  if(entry.results.size>5000)entry.results.clear();
  entry.results.set(memoKey,result);
  return result;
}

const formatters = new Map();
function formatter(kind, options) {
  const key = kind + "|" + Memu.lang;
  let value = formatters.get(key);
  if (!value) {
    try { value = new Intl.DateTimeFormat(Memu.lang==="en"?"en-US":"ru-RU", options); }
    catch (error) { value = { format: (date) => date.toLocaleString() }; }
    formatters.set(key, value);
  }
  return value;
}
function benchmarkDate(stamp) {
  return stamp ? formatter("bench", {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}).format(new Date(stamp)) : "—";
}
const titleMemo = new Map();
function benchmarkBoardTitle(id) {
  const key = Memu.lang + "|" + id + "|" + Object.keys(benchmarkState.cache.arena || {}).length + "|" + ((benchmarkState.cache.arena_nav || {}).at || 0);
  let value = titleMemo.get(key);
  if (value === undefined) {
    const [base,category]=id.split("::");
    value = benchmarks.boardTitle(base)+(category ? " · "+category : "");
    if (titleMemo.size > 2000) titleMemo.clear();
    titleMemo.set(key, value);
  }
  return value;
}
function benchmarkBoardState(id) {
  if(id==="memu:index")return benchmarkState.loading.size ? t("bench.loading") : Object.keys(benchmarkState.errors).length ? t("bench.partial") : "";
  const key=benchmarks.loadingKeyFor(id);
  if(benchmarkState.loading.has(key))return t("bench.loading");
  if(benchmarkState.errors[key])return t("bench.error",{error:benchmarkState.errors[key]});
  return "";
}
function benchmarkVariantControl(model,id,result) {
  if(!result.selected)return "";
  const variant=esc(result.selected.model);
  const sourceName=id==="memu:index" ? t("bench.index.variants") : variant;
  const missing=result.variantMissing?`<span class="mc-bench-warning">${esc(t("bench.variant.changed"))}</span>`:"";
  if(result.rows.length<2)return `<small class="mc-bench-variant">${sourceName}${missing}</small>`;
  return `<label class="mc-bench-variant"><span>${esc(t("bench.variant"))}</span><select data-benchmark-variant="${esc(model.id)}" data-board="${esc(id)}" aria-label="${esc(t("bench.variant"))}">${result.rows.map(row=>`<option value="${esc(row.model)}" ${row.model===result.selected.model?"selected":""}>${esc(row.model)}</option>`).join("")}</select>${result.defaultBest?`<span>${esc(t("bench.best.variant"))}</span>`:""}${missing}</label>`;
}
function benchmarkValue(model,id) {
  const result=benchmarkResults(model,id);
  if(!result.selected)return `<span class="mc-bench-missing">${esc(result.data ? t("bench.no.result") : benchmarkBoardState(id)||t("bench.not.loaded"))}</span>`;
  return `<b class="mc-bench-score">${esc(result.data.score.fmt(result.selected.score))}</b><small>${esc(t("bench.place",{place:result.place,total:result.total}))}</small>${benchmarkVariantControl(model,id,result)}`;
}
function benchmarkMeta(id,data) {
  const error=benchmarkBoardState(id), stale=data&&data.updated&&Date.now()-data.updated>=TTL;
  return `<div class="mc-bench-meta">${data?.source?`<button type="button" class="mc-link" data-open-url="${esc(data.source)}">${esc(t("bench.source"))} ↗</button>`:""}${data?`<span>${esc(t("bench.updated",{date:benchmarkDate(data.updated)}))}</span>`:""}${data?.note?`<span>${esc(data.note)}</span>`:""}${stale?`<span class="mc-bench-warning">${esc(t("bench.stale"))}</span>`:""}${error?`<span class="mc-bench-warning">${esc(error)}</span>`:""}</div>`;
}
let controlsMemo = { key: "", html: "" };
function benchmarkControls() {
  const key = JSON.stringify([Memu.lang, benchmarkBoards, Object.keys(benchmarkState.cache.arena || {}), (benchmarkState.cache.arena_nav || {}).at || 0,
    ((benchmarkState.cache.livebench || {}).categories || []).length, benchmarkState.loading.size > 0]);
  if (controlsMemo.key !== key) controlsMemo = { key, html: benchmarkControlsHtml() };
  return controlsMemo.html;
}
function benchmarkControlsHtml() {
  const groups=benchmarks.catalog().map(group=>({...group,boards:group.boards.filter(board=>board.id!=="memu:own")})).filter(group=>group.boards.length);
  const extra=Object.entries(benchmarkState.cache.arena).flatMap(([slug,entry])=>(entry.categories||[]).map(category=>({id:`arena:${slug.split("::")[0]}::${category.id}`,title:`${benchmarks.arenaTitle(slug.split("::")[0])} · ${category.title}`})));
  if(extra.length)groups.push({id:"categories",title:t("bench.categories"),boards:extra.filter((board,index,all)=>all.findIndex(row=>row.id===board.id)===index)});
  return `<div class="mc-bench-controls"><div><h3>${esc(t("bench.title"))}</h3><p>${esc(t("bench.explanation"))}</p></div><button type="button" class="mc-btn ghost" data-refresh-benchmarks ${benchmarkState.loading.size?"disabled":""}>${esc(t("bench.refresh"))}</button></div><details class="mc-bench-choices"><summary>${esc(t("bench.boards",{count:benchmarkBoards.length}))}</summary><div>${groups.map(group=>`<fieldset><legend>${esc(group.title)}</legend>${group.boards.map(board=>`<label><input type="checkbox" data-benchmark-board="${esc(board.id)}" ${benchmarkBoards.includes(board.id)?"checked":""}><span>${esc(board.title)}</span></label>`).join("")}</fieldset>`).join("")}</div></details>`;
}
function benchmarkSummary(model) {
  const result=benchmarkResults(model,"memu:index");
  return `<button type="button" class="mc-benchmark-summary" data-open="${esc(model.id)}" data-bench-summary="${esc(model.id)}" data-focus-bench>${result.selected?`<span>MemuAI Index</span><b>${esc(result.data.score.fmt(result.selected.score))}</b><small>${esc(t("bench.coverage",{count:result.selected.count}))}</small>`:`<span>${esc(t("bench.title"))}</span><span aria-hidden="true">↗</span>`}</button>`;
}
let panelsQueued = false;
function scheduleBenchmarkPanels() {
  if (panelsQueued) return;
  panelsQueued = true;
  const run = () => { panelsQueued = false; renderBenchmarkPanels(); refreshSummaries(); };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(run); else setTimeout(run, 0);
}

let summaryData = null;
function refreshSummaries() {
  const entry = boardEntry("memu:index");
  if (entry.data === summaryData) return;
  summaryData = entry.data;
  const main = $("#main");
  if (state.view !== "list" || !main || typeof main.querySelectorAll !== "function") return;
  for (const node of main.querySelectorAll("[data-bench-summary]")) {
    const row = byId(node.dataset.benchSummary);
    if (row) node.outerHTML = benchmarkSummary(row);
  }
}
function renderBenchmarkPanels() {
  const panel=$("#card-benchmarks"), card=$("#card");
  if(panel&&card&&!card.hidden) {
    const model=byId(card.dataset.model);
    if(model) {
      const choicesOpen=!!panel.querySelector("details")?.open;
      panel.innerHTML=benchmarkControls()+`<div class="mc-bench-list">${benchmarkBoards.map(id=>{const result=benchmarkResults(model,id);return `<article data-benchmark-result="${esc(id)}"><div class="mc-bench-row"><div><b>${esc(benchmarkBoardTitle(id))}</b><small>${esc(result.data?.score.higher===false?t("bench.lower"):t("bench.higher"))}</small></div><div class="mc-bench-result">${benchmarkValue(model,id)}</div></div>${benchmarkMeta(id,result.data)}</article>`;}).join("")}</div><p class="mc-bench-note">${esc(t("bench.index.note"))}</p>`;
      panel.querySelector("details").open=choicesOpen;
    }
  }
  if(state.view==="compare")renderBenchmarkCompare();
}
function renderBenchmarkCompare() {
  const panel=$("#compare-benchmarks"); if(!panel)return;
  const choicesOpen=!!panel.querySelector("details")?.open;
  const rows=state.compare.map(byId).filter(Boolean);
  panel.innerHTML=benchmarkControls()+`<p class="mc-bench-scroll-hint">${esc(t("bench.scroll"))}</p><div class="mc-table-wrap" tabindex="0" role="region" aria-label="${esc(t("compare.title"))}"><table class="mc-table mc-bench-table"><thead><tr><th>${esc(t("bench.board"))}</th>${rows.map(row=>`<th>${esc(row.name)}</th>`).join("")}</tr></thead><tbody>${benchmarkBoards.map(id=>{
    const data=benchmarks.boardData(id), results=rows.map(row=>benchmarkResults(row,id));
    const values=results.map(result=>result.selected?.score).filter(Number.isFinite);
    const best=values.length>1?(data.score.higher?Math.max(...values):Math.min(...values)):null;
    return `<tr data-benchmark-result="${esc(id)}"><th><b>${esc(benchmarkBoardTitle(id))}</b><small>${esc(data?.score.higher===false?t("bench.lower"):t("bench.higher"))}</small>${benchmarkMeta(id,data)}</th>${rows.map((row,index)=>`<td class="${best!==null&&results[index].selected?.score===best?"best":""}">${benchmarkValue(row,id)}</td>`).join("")}</tr>`;
  }).join("")}</tbody></table></div><p class="mc-bench-note">${esc(t("bench.compare.note"))} ${esc(t("bench.index.note"))}</p>`;
  panel.querySelector("details").open=choicesOpen;
}
async function loadBenchmarks(force=false) {
  if(!benchmarkLoaded||force) {
    benchmarkLoaded=true;
    await benchmarks.loadIndex(force);
  }
  const indexIds = new Set(benchmarks.INDEX_SOURCES.map(([id])=>id));
  const jobs = benchmarkBoards.filter(id=>id!=="memu:index" && (!indexIds.has(id)||!benchmarks.boardData(id)));
  let next=0;
  await Promise.all([0,1,2].map(async()=>{while(next<jobs.length){const id=jobs[next++];await benchmarks.ensure(id,force);}}));
  renderBenchmarkPanels();

  if(state.view==="list")refreshSummaries();
}

function slim(model) {
  model = model && typeof model === "object" ? model : {};
  const arch = model.architecture || {};
  const pricing = model.pricing || {};
  const top = model.top_provider || {};
  const params = new Set(Array.isArray(model.supported_parameters) ? model.supported_parameters : []);
  const id = String(model.id || "");
  const name = String(model.name || id);
  return {
    id, name: name.includes(": ") ? name.split(": ").slice(1).join(": ") : name,
    org: name.includes(": ") ? name.split(": ")[0] : id.split("/")[0],
    slug: id.split("/")[0],
    created: Number(model.created) || 0,
    context: Number(model.context_length) || Number(top.context_length) || 0,
    maxOut: Number(top.max_completion_tokens) || 0,
    in: (Array.isArray(arch.input_modalities) ? arch.input_modalities : []).filter((x) => typeof x === "string"),
    out: (Array.isArray(arch.output_modalities) ? arch.output_modalities : []).filter((x) => typeof x === "string"),
    price: {
      input: perMillion(pricing.prompt), output: perMillion(pricing.completion),
      cacheRead: perMillion(pricing.input_cache_read), cacheWrite: perMillion(pricing.input_cache_write),
      reasoning: perMillion(pricing.internal_reasoning),
      web: pricing.web_search !== undefined && Number(pricing.web_search) >= 0 ? Number(pricing.web_search) : undefined,
      image: pricing.image !== undefined && Number(pricing.image) >= 0 ? Number(pricing.image) : undefined,
    },
    tools: params.has("tools"), reasoning: params.has("reasoning") || params.has("include_reasoning"),
    structured: params.has("structured_outputs") || params.has("response_format"),
    web: params.has("web_search_options") || pricing.web_search !== undefined,
    params: [...params].slice(0, 40),
    moderated: !!top.is_moderated,
    cutoff: model.knowledge_cutoff ? String(model.knowledge_cutoff) : "",
    expires: model.expiration_date ? String(model.expiration_date) : "",
    hf: model.hugging_face_id ? String(model.hugging_face_id) : "",
    tokenizer: arch.tokenizer ? String(arch.tokenizer) : "",
    description: String(model.description || "").slice(0, 900),
  };
}

function slimModelsDev(provider, model) {
  const id = String(model.canonical_model_id || model.id || "");
  const slug = id.includes("/") ? id.split("/")[0] : provider.id;
  const price = (key) => {
    const value = model.cost?.[key];
    return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : undefined;
  };
  return { id, name: String(model.name || model.id || id), org: slug === provider.id ? provider.name || slug : slug,
    slug, created: Date.parse(model.release_date || "") / 1000 || 0,
    context: Number(model.limit?.context) || 0, maxOut: Number(model.limit?.output) || 0,
    in: (Array.isArray(model.modalities?.input) ? model.modalities.input : []).filter(x => typeof x === "string"),
    out: (Array.isArray(model.modalities?.output) ? model.modalities.output : []).filter(x => typeof x === "string"),
    price: {input:price("input"), output:price("output"), cacheRead:price("cache_read"), cacheWrite:price("cache_write")},
    tools:!!model.tool_call, reasoning:!!model.reasoning, structured:!!model.structured_output, web:false,
    params:[], moderated:false, cutoff:String(model.knowledge || ""), expires:"", hf:"", tokenizer:"",
    description:String(model.description || "").slice(0,900), source:"models.dev", provider:provider.name || provider.id,
    site: "https://models.dev/" };
}

const MD_ORG_ALIASES = { mistral: ["mistral", "mistralai"], alibaba: ["alibaba", "alibaba-cn", "qwen"], zhipuai: ["zhipuai", "zai", "z-ai"],
  meta: ["meta", "meta-llama", "llama"], xai: ["xai", "x-ai"], google: ["google", "google-vertex"], moonshotai: ["moonshotai", "moonshotai-cn"] };

function modelKey(value) {
  let id = String(value || "").toLowerCase().split("/").pop();
  id = id.replace(/:[a-z0-9-]+$/, "")
    .replace(/@.*$/, "")
    .replace(/[-_](20\d{2}-?\d{2}-?\d{2}|\d{4}|\d{6,8})$/, "")
    .replace(/[-_](latest|preview|instruct|it|chat)$/, "");
  return id.replace(/[^a-z0-9]+/g, "");
}

function modelsDevRows(data) {
  const unique = new Map();
  const rank = (row, providerId) => {
    const org = row.slug.toLowerCase();
    if (providerId === org || (MD_ORG_ALIASES[org] || []).includes(providerId)) return 0;
    return 1;
  };
  for (const [providerId, info] of Object.entries(data || {})) {
    if (!info || typeof info !== "object") continue;
    const provider = {...info, id:providerId};
    for (const model of Object.values(info.models || {})) {
      if (!model || typeof model !== "object" || !model.id) continue;
      const canonical = String(model.canonical_model_id || "");

      if (!canonical.includes("/")) continue;
      if (model.status === "deprecated") continue;
      const row = slimModelsDev(provider, model), key = canonical.toLowerCase();
      row.rank = rank(row, providerId);
      const prev = unique.get(key);
      if (!prev || row.rank < prev.rank) unique.set(key, row);
    }
  }
  return [...unique.values()].map(({rank, ...row}) => row);
}

function mergeCatalogs(primary, secondary) {
  const merged = new Map(), byKey = new Map();
  for (const row of primary) {
    const id = row.id.toLowerCase();
    if (merged.has(id)) continue;
    merged.set(id, row);
    const key = modelKey(row.id);
    if (key && !byKey.has(key)) byKey.set(key, row);
  }
  for (const row of secondary) {
    const id = row.id.toLowerCase(), key = modelKey(row.id);
    const twin = merged.get(id) || (key && byKey.get(key));
    if (twin) {
      if (!twin.cutoff && row.cutoff) twin.cutoff = row.cutoff;
      if (!twin.created && row.created) twin.created = row.created;
      continue;
    }
    merged.set(id, row);
    if (key) byKey.set(key, row);
  }
  return [...merged.values()];
}

async function load(force = false) {
  if (!force && state.cache?.schema === CACHE_SCHEMA && !state.cache.partial && Date.now() - state.cache.at < TTL) return;
  state.loading = true;
  state.error = "";
  render();
  try {
    const sources = [{url:SOURCE, name:"OpenRouter", key:"openrouter"}, {url:MODELS_DEV, name:"Models.dev", key:"models.dev"}];
    const results = await Promise.allSettled(sources.map(async source => {
      const result = await Memu.fetch({url:source.url, raw:true, limit:8000000});
      if (!result?.text) throw new Error(t("error.empty"));
      if (result.truncated) throw new Error(t("error.truncated"));
      const data = JSON.parse(result.text);
      const rows = source.key === "openrouter" ? (Array.isArray(data.data) ? data.data : []).map(slim).filter(row=>row.id) : modelsDevRows(data);
      if (!rows.length) throw new Error(t("error.format"));
      return rows.map(row=>({...row, source:source.key}));
    }));
    const errors = [], parts = [];
    results.forEach((result,index)=>{
      const source = sources[index];
      if (result.status === "rejected") errors.push(`${source.name}: ${result.reason?.message || result.reason}`);

      const previous = state.cache?.schema === CACHE_SCHEMA ? state.cache.rows || [] : [];
      parts.push(result.status === "fulfilled" ? result.value : previous.filter(row=>(row.source || "openrouter") === source.key));
    });
    const rows = mergeCatalogs(parts[0], parts[1]);
    if (!rows.length) throw new Error(errors.join("; "));
    state.error = errors.join("; ");
    state.cache = {at:Date.now(), schema:CACHE_SCHEMA, partial:errors.length > 0, rows};
    remember(true);
  } catch (error) {
    state.error = error && error.message ? error.message : String(error);
  } finally {
    state.loading = false;
    render();
  }
}

async function loadMyModels() {
  try {
    const rows = await Memu.models();
    const set = new Set();
    for (const row of Array.isArray(rows) ? rows : []) {
      if (!row || !row.id) continue;
      const id = String(row.id);
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

function priceKey(row) {
  const { input, output } = row.price;
  if (input === null || output === null || input === undefined) return null;
  return (3 * input + (output || 0)) / 4;
}

function matches(row, skip = "") {
  const f = state.filters;
  if (f.inputs.length && !f.inputs.every((kind) => row.in.includes(kind))) return false;
  if (f.outputs.length && !f.outputs.every((kind) => row.out.includes(kind))) return false;
  if (f.features.length && !f.features.every((feature) => row[feature])) return false;
  if (f.context && row.context < f.context) return false;
  if (f.hideExpiring && row.expires) return false;
  if (f.price !== "any") {
    const price = row.price.input;
    if (f.price === "free" ? !(price === 0 && row.price.output === 0) : !(price !== null && price !== undefined && price <= Number(f.price))) return false;
  }
  if (f.sources.length && !f.sources.includes(row.source || "openrouter")) return false;
  if (skip !== "orgs" && f.orgs.length && !f.orgs.includes(row.slug)) return false;
  if (state.mine && !isMine(row)) return false;
  const query = state.query.trim().toLowerCase();
  if (query && !haystack(row).includes(query)) return false;
  return true;
}

const hayMemo = new WeakMap();
function haystack(row) {
  let value = hayMemo.get(row);
  if (value === undefined) { value = `${row.id} ${row.name} ${row.org}`.toLowerCase(); hayMemo.set(row, value); }
  return value;
}

function sorted(rows) {
  const by = {
    newest: (a, b) => b.created - a.created,
    "price-asc": (a, b) => (priceKey(a) ?? Infinity) - (priceKey(b) ?? Infinity),
    "price-desc": (a, b) => (priceKey(b) ?? -Infinity) - (priceKey(a) ?? -Infinity),
    context: (a, b) => b.context - a.context,
    name: (a, b) => a.name.localeCompare(b.name),
  }[state.sort];
  return rows.slice().sort(by);
}

let visibleMemo = { key: "", rows: [] };
function visible() {
  if (!state.cache) return [];

  const key = JSON.stringify([state.filters, state.sort, state.query, state.mine, state.mySet ? state.mySet.size : -1, state.cache.at, state.cache.rows.length]);
  if (visibleMemo.key === key && visibleMemo.source === state.cache.rows) return visibleMemo.rows;
  const rows = sorted(state.cache.rows.filter((row) => matches(row)));
  visibleMemo = { key, source: state.cache.rows, rows };
  return rows;
}

function appendMore() {
  const rows = visible();
  const main = $("#main");
  const grid = main && typeof main.querySelector === "function" ? main.querySelector(".mc-grid") : null;
  const more = main && typeof main.querySelector === "function" ? main.querySelector(".mc-more") : null;
  if (!grid || typeof grid.insertAdjacentHTML !== "function") { state.shown += PAGE; render(); return; }
  const from = state.shown;
  state.shown = Math.min(rows.length, state.shown + PAGE);
  grid.insertAdjacentHTML("beforeend", rows.slice(from, state.shown).map(modelCard).join(""));
  if (more) {
    if (rows.length > state.shown) {
      const button = more.querySelector("[data-more]");
      if (button) button.textContent = t("more", { count: Math.min(PAGE, rows.length - state.shown) });
    } else more.remove();
  }
}
let moreObserver = null;
function watchMore() {
  if (typeof IntersectionObserver !== "function") return;
  const main = $("#main");
  const more = main && typeof main.querySelector === "function" ? main.querySelector(".mc-more") : null;
  if (!moreObserver) moreObserver = new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) appendMore();
  }, { root: main, rootMargin: "600px 0px" });
  moreObserver.disconnect();
  if (more) moreObserver.observe(more);
}

function chip(kind, text, title = "") {
  return `<span class="mc-chip ${kind}"${title ? ` title="${esc(title)}"` : ""}>${esc(text)}</span>`;
}

function badges(row) {
  const out = [];
  if (row.price.input === 0 && row.price.output === 0) out.push(chip("ok", t("price.free")));
  if (isMine(row)) out.push(chip("mine", t("tag.mine")));
  for (const kind of row.in.filter((kind) => kind !== "text")) out.push(chip("", t(`in.${kind}`)));
  for (const kind of row.out.filter((kind) => kind !== "text")) out.push(chip("accent", t(`out.${kind}`)));
  if (row.reasoning) out.push(chip("", t("feature.reasoning")));
  if (row.tools) out.push(chip("", t("feature.tools")));
  if (row.expires) out.push(chip("warn", t("tag.expires"), t("tag.expires.title", { date: row.expires })));
  return out.join("");
}

function renderSort() {
  const select = $("#sort");
  select.innerHTML = SORTS.map((key) => `<option value="${key}" ${key === state.sort ? "selected" : ""}>${esc(t(`sort.${key}`))}</option>`).join("");
}

function checkGroup(title, name, values, selected, label) {
  return `<fieldset class="mc-group"><legend>${esc(title)}</legend>${values.map((value) => `<label class="mc-check">
      <input type="checkbox" data-filter="${name}" value="${esc(value)}" ${selected.includes(value) ? "checked" : ""}><span>${esc(label(value))}</span></label>`).join("")}</fieldset>`;
}

function renderFilters() {
  const f = state.filters;
  const rows = state.cache ? state.cache.rows.filter((row) => matches(row, "orgs")) : [];
  const counts = new Map();
  for (const row of rows) {
    const entry = counts.get(row.slug) || { slug: row.slug, name: row.org, count: 0 };
    entry.count += 1;
    counts.set(row.slug, entry);
  }
  const needle = state.orgQuery.trim().toLowerCase();
  const orgs = [...counts.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .filter((org) => !needle || `${org.slug} ${org.name}`.toLowerCase().includes(needle) || f.orgs.includes(org.slug));
  for (const slug of f.orgs) if (!counts.has(slug)) orgs.unshift({ slug, name: slug, count: 0 });
  const active = f.inputs.length + f.outputs.length + f.features.length + f.orgs.length + f.sources.length + (f.context ? 1 : 0) + (f.price !== "any" ? 1 : 0) + (f.hideExpiring ? 1 : 0);
  $("#filters").innerHTML = `<div class="mc-filters-head"><b>${esc(t("filters.title"))}</b>${active ? `<button type="button" class="mc-link" data-reset>${esc(t("filters.reset", { count: active }))}</button>` : ""}</div>
    ${checkGroup(t("filters.input"), "inputs", INPUTS, f.inputs, (kind) => t(`in.${kind}`))}
    ${checkGroup(t("filters.output"), "outputs", OUTPUTS, f.outputs, (kind) => t(`out.${kind}`))}
    <fieldset class="mc-group"><legend>${esc(t("filters.context"))}</legend>
      <select class="mc-select wide" data-filter-select="context">${CONTEXTS.map((value) => `<option value="${value}" ${value === f.context ? "selected" : ""}>${esc(value ? t("context.min", { size: compact(value) }) : t("context.any"))}</option>`).join("")}</select></fieldset>
    <fieldset class="mc-group"><legend>${esc(t("filters.price"))}</legend>
      <select class="mc-select wide" data-filter-select="price">${PRICES.map((value) => `<option value="${value}" ${value === f.price ? "selected" : ""}>${esc(t(`price.filter.${value}`))}</option>`).join("")}</select></fieldset>
    ${checkGroup(t("filters.features"), "features", FEATURES, f.features, (feature) => t(`feature.${feature}`))}
    ${checkGroup(t("filters.sources"), "sources", SOURCES, f.sources, (source) => source === "openrouter" ? "OpenRouter" : "Models.dev")}
    <label class="mc-check solo"><input type="checkbox" data-filter-flag="hideExpiring" ${f.hideExpiring ? "checked" : ""}><span>${esc(t("filters.hideExpiring"))}</span></label>
    <fieldset class="mc-group"><legend>${esc(t("filters.orgs"))}</legend>
      <input class="mc-input" id="org-search" type="search" value="${esc(state.orgQuery)}" placeholder="${esc(t("filters.orgs.search"))}" aria-label="${esc(t("filters.orgs.search"))}">
      <div class="mc-orgs">${orgs.map((org) => `<label class="mc-check"><input type="checkbox" data-filter="orgs" value="${esc(org.slug)}" ${f.orgs.includes(org.slug) ? "checked" : ""}><span>${esc(org.name)}</span><small>${org.count}</small></label>`).join("")}</div></fieldset>`;
}

function renderStatus() {
  const status = $("#status");
  if (state.loading) status.textContent = t("status.loading");
  else if (state.cache) status.textContent = t("status.count", { count: state.cache.rows.length }) + ` · ${t("status.updated", { when: new Date(state.cache.at).toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU", { hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" }) })}`;
  else status.textContent = state.error ? t("error.title") : "";
}

function orgHue(slug) {
  let hash = 0;
  for (const ch of String(slug || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return hash % 360;
}

function modelCard(row) {
  const selected = state.compare.includes(row.id);
  return `<article class="mc-model ${isMine(row) ? "mine" : ""}" data-model="${esc(row.id)}">
      <div class="mc-model-heading"><span class="mc-avatar" aria-hidden="true" style="--hue:${orgHue(row.slug)}">${esc(row.org.charAt(0).toUpperCase())}</span><button type="button" class="mc-model-open" data-open="${esc(row.id)}">
        <span class="mc-model-org">${esc(row.org)}</span>
        <b class="mc-model-name">${esc(row.name)}</b>
      </button></div>
      ${row.description ? `<p class="mc-model-description">${esc(row.description)}</p>` : `<p class="mc-model-description mc-muted">${esc(row.id)}</p>`}
      <div class="mc-badges">${badges(row)}</div>
      ${benchmarkSummary(row)}
      <dl class="mc-facts">
        <div><dt>${esc(t("fact.context"))}</dt><dd>${row.context ? compact(row.context) : "—"}</dd></div>
        <div><dt>${esc(t("fact.input"))}</dt><dd>${money(row.price.input)}</dd></div>
        <div><dt>${esc(t("fact.output"))}</dt><dd>${money(row.price.output)}</dd></div>
        <div><dt>${esc(t("fact.added"))}</dt><dd>${esc(dateText(row.created))}</dd></div>
      </dl>
      <footer class="mc-model-footer"><label class="mc-compare-toggle"><input type="checkbox" data-compare="${esc(row.id)}" ${selected ? "checked" : ""} ${!selected && state.compare.length >= MAX_COMPARE ? "disabled" : ""}><span>${esc(t("compare.add"))}</span></label><button type="button" class="mc-link mc-details-link" data-open="${esc(row.id)}">${esc(t("card.details"))}<span aria-hidden="true">›</span></button></footer>
    </article>`;
}

const PURPOSES = ["all","reasoning","vision","image","tools","free"];
function purposeActive(key) {
  const f=state.filters;
  if(key==="reasoning")return f.features.includes("reasoning");
  if(key==="vision")return f.inputs.includes("image");
  if(key==="image")return f.outputs.includes("image");
  if(key==="tools")return f.features.includes("tools");
  if(key==="free")return f.price==="free";
  return !f.inputs.length&&!f.outputs.length&&!f.features.length&&f.price==="any";
}
function activeFilterChips() {
  const f=state.filters, chips=[];
  for(const group of ["inputs","outputs","features","orgs","sources"])for(const value of f[group]) {
    const row=group==="orgs"?state.cache?.rows.find(row=>row.slug===value):null;
    const label=group==="orgs"?(row?.org||value):group==="sources"?(value==="openrouter"?"OpenRouter":"Models.dev"):t(`${group==="inputs"?"in":group==="outputs"?"out":"feature"}.${value}`);
    chips.push({group,value,label});
  }
  if(f.context)chips.push({group:"context",value:0,label:t("context.min",{size:compact(f.context)})});
  if(f.price!=="any")chips.push({group:"price",value:"any",label:t(`price.filter.${f.price}`)});
  if(f.hideExpiring)chips.push({group:"hideExpiring",value:false,label:t("filters.hideExpiring")});
  return chips.map(({group,value,label})=>`<button type="button" class="mc-active-filter" data-remove-filter="${esc(group)}" data-filter-value="${esc(value)}">${esc(label)}<span aria-hidden="true">×</span><span class="mc-sr-only">${esc(t("filters.remove"))}</span></button>`).join("");
}

function renderList(main) {
  if (!state.cache) {
    main.innerHTML = state.error
      ? `<div class="mc-error"><b>${esc(t("error.title"))}</b><p>${esc(state.error)}</p><button type="button" class="mc-btn" data-retry>${esc(t("retry"))}</button></div>`
      : `<div class="mc-skeleton">${"<i></i>".repeat(9)}</div>`;
    return;
  }
  const rows = visible();
  main.innerHTML = `<section class="mc-hero"><div><span class="mc-eyebrow">MEMUAI COLLECTION</span><h1>${esc(t("hero.title"))}</h1><p>${esc(t("hero.sub"))}</p></div>
      <div class="mc-hero-stats"><span><b>${rows.length}</b>${esc(t("hero.shown"))}</span><span><b>${new Set(state.cache.rows.map((row) => row.slug)).size}</b>${esc(t("hero.orgs"))}</span>
      <span><b>${state.cache.rows.filter((row) => row.price.input === 0 && row.price.output === 0).length}</b>${esc(t("hero.free"))}</span></div></section>
    <nav class="mc-purposes" aria-label="${esc(t("purpose.label"))}">${PURPOSES.map(key=>`<button type="button" data-purpose="${key}" class="mc-purpose ${purposeActive(key)?"on":""}" aria-pressed="${purposeActive(key)}">${esc(t(`purpose.${key}`))}</button>`).join("")}</nav>
    <div class="mc-results-bar"><div><b>${esc(t("results.count",{count:rows.length}))}</b><span>${esc(t("price.unit"))}</span></div><div class="mc-layout" role="group" aria-label="${esc(t("layout.label"))}"><button type="button" data-layout="cards" aria-pressed="${state.layout==="cards"}" title="${esc(t("layout.cards"))}"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg><span>${esc(t("layout.cards"))}</span></button><button type="button" data-layout="rows" aria-pressed="${state.layout==="rows"}" title="${esc(t("layout.rows"))}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>${esc(t("layout.rows"))}</span></button></div></div>
    <div class="mc-active-filters">${activeFilterChips()}</div>
    ${state.error ? `<p class="mc-warn">${esc(t("error.stale", { error: state.error }))}</p>` : ""}
    ${rows.length ? `<section class="mc-grid ${state.layout==="rows"?"rows":""}">${rows.slice(0, state.shown).map(modelCard).join("")}</section>
      ${rows.length > state.shown ? `<div class="mc-more"><button type="button" class="mc-btn ghost" data-more>${esc(t("more", { count: Math.min(PAGE, rows.length - state.shown) }))}</button></div>` : ""}`
      : `<div class="mc-empty">${esc(state.mine ? t("empty.mine") : t("empty.filtered"))}</div>`}`;
}

let rowIndex = { rows: null, map: new Map() };
function byId(id) {
  if (!state.cache) return null;
  if (rowIndex.rows !== state.cache.rows || rowIndex.size !== state.cache.rows.length) {
    rowIndex = { rows: state.cache.rows, size: state.cache.rows.length, map: new Map() };
    for (const row of state.cache.rows) if (!rowIndex.map.has(row.id)) rowIndex.map.set(row.id, row);
  }
  return rowIndex.map.get(id) || null;
}

function renderCompare(main) {
  const rows = state.compare.map(byId).filter(Boolean);
  if (rows.length < 2) { state.view = "list"; return renderList(main); }
  const line = (label, fn, best) => {

    const values = rows.map((row) => fn(row, false));
    const numeric = values.map((value) => (typeof value === "number" && Number.isFinite(value) ? value : null));
    const cells = rows.map((row) => fn(row, true));
    const available = numeric.filter((value) => value !== null);
    const distinct = new Set(cells.filter((_, index) => numeric[index] !== null));
    const target = best && available.length > 1 && distinct.size > 1 ? (best === "max" ? Math.max(...available) : Math.min(...available)) : null;
    const winningCell = target === null ? null : cells[numeric.indexOf(target)];
    return `<tr><th>${esc(label)}</th>${rows.map((row, index) => `<td class="${winningCell !== null && numeric[index] !== null && cells[index] === winningCell ? "best" : ""}">${cells[index]}</td>`).join("")}</tr>`;
  };
  const yes = (value) => (value ? `<span class="mc-yes">${esc(t("yes"))}</span>` : `<span class="mc-no">—</span>`);
  const priceCell = (key) => (row, html) => (html ? money(row.price[key]) : (typeof row.price[key] === "number" ? row.price[key] : null));
  main.innerHTML = `<header class="mc-compare-head"><button type="button" class="mc-btn ghost" data-back>${esc(t("back"))}</button><h1>${esc(t("compare.title"))}</h1></header>
    <div class="mc-table-wrap"><table class="mc-table"><thead><tr><th></th>${rows.map((row) => `<th><button type="button" class="mc-link" data-open="${esc(row.id)}">${esc(row.name)}</button><small>${esc(row.org)}</small></th>`).join("")}</tr></thead>
    <tbody>
      ${line(t("fact.context"), (row, html) => (html ? (row.context ? compact(row.context) : "—") : row.context || null), "max")}
      ${line(t("fact.maxout"), (row, html) => (html ? (row.maxOut ? compact(row.maxOut) : "—") : row.maxOut || null), "max")}
      ${line(t("fact.input"), priceCell("input"), "min")}
      ${line(t("fact.output"), priceCell("output"), "min")}
      ${line(t("fact.cacheRead"), priceCell("cacheRead"), "min")}
      ${line(t("filters.input"), (row) => esc(row.in.map((kind) => t(`in.${kind}`)).join(", ")))}
      ${line(t("filters.output"), (row) => esc(row.out.map((kind) => t(`out.${kind}`)).join(", ")))}
      ${FEATURES.map((feature) => line(t(`feature.${feature}`), (row) => yes(row[feature]))).join("")}
      ${line(t("fact.cutoff"), (row) => esc(row.cutoff || "—"))}
      ${line(t("fact.added"), (row) => esc(dateText(row.created)))}
      ${line(t("tag.mine"), (row) => yes(isMine(row)))}
    </tbody></table></div><section id="compare-benchmarks" class="mc-bench-section" aria-live="polite"></section>`;
  renderBenchmarkCompare();
}

function renderCompareBar() {
  const bar = $("#compare-bar");
  const rows = state.compare.map(byId).filter(Boolean);
  bar.hidden = !rows.length || state.view === "compare";
  if (bar.hidden) return;
  bar.innerHTML = `<span class="mc-muted">${esc(t("compare.bar", { count: rows.length, max: MAX_COMPARE }))}</span>
    ${rows.map((row) => `<span class="mc-chip">${esc(row.name)}<button type="button" class="mc-x" data-uncompare="${esc(row.id)}" aria-label="${esc(t("compare.remove"))}">✕</button></span>`).join("")}
    <button type="button" class="mc-btn" data-show-compare ${rows.length < 2 ? "disabled" : ""}>${esc(t("compare.show"))}</button>`;
}

function render() {
  $("#app").classList.toggle("filters-open",state.filtersOpen);
  $("#filters-toggle").setAttribute("aria-expanded",String(state.filtersOpen));
  renderStatus();
  renderFilters();
  const main = $("#main");
  if (state.view === "compare") renderCompare(main);
  else { renderList(main); summaryData = boardEntry("memu:index").data; watchMore(); }
  renderCompareBar();
}

function openCard(id) {
  const row = byId(id);
  if (!row) return;
  const card = $("#card");
  const price = row.price;
  const priceRows = [["fact.input", money(price.input)], ["fact.output", money(price.output)],
    ["fact.cacheRead", money(price.cacheRead)], ["fact.cacheWrite", money(price.cacheWrite)], ["fact.reasoningPrice", money(price.reasoning)],
    ["fact.web", price.web !== undefined ? `$${num(price.web, 3)}` : "—"], ["fact.image", price.image !== undefined ? `$${num(price.image * 1000, 2)}` : "—"]]
    .filter(([, value]) => value !== "—");
  card.innerHTML = `<header><span class="mc-avatar" aria-hidden="true" style="--hue:${orgHue(row.slug)}">${esc(row.org.charAt(0).toUpperCase())}</span><div><span class="mc-model-org">${esc(row.org)}</span><h2>${esc(row.name)}</h2><code class="mc-model-id">${esc(row.id)}</code></div>
      <button type="button" class="mc-icon" data-close aria-label="${esc(t("close"))}" title="${esc(t("close"))} · Esc">✕</button></header>
    <div class="mc-sheet-stats">
      <div><span>${esc(t("fact.context"))}</span><b>${row.context ? compact(row.context) : "—"}</b></div>
      <div><span>${esc(t("fact.input"))}</span><b>${money(price.input)}</b></div>
      <div><span>${esc(t("fact.output"))}</span><b>${money(price.output)}</b></div>
      <div><span>${esc(t("fact.maxout"))}</span><b>${row.maxOut ? compact(row.maxOut) : "—"}</b></div>
    </div>
    <div class="mc-badges">${badges(row)}</div>
    ${row.description ? `<p class="mc-desc">${esc(row.description)}</p>` : ""}
    <p class="mc-desc">${esc(t("card.source", {source:row.source === "models.dev" ? `Models.dev · ${row.provider}` : "OpenRouter"}))}</p>
    <section id="card-benchmarks" class="mc-bench-section" aria-live="polite"></section>
    <dl class="mc-facts wide">
      <div><dt>${esc(t("fact.context"))}</dt><dd>${row.context ? num(row.context) : "—"}</dd></div>
      <div><dt>${esc(t("fact.maxout"))}</dt><dd>${row.maxOut ? num(row.maxOut) : "—"}</dd></div>
      <div><dt>${esc(t("fact.added"))}</dt><dd>${esc(dateText(row.created))}</dd></div>
      <div><dt>${esc(t("fact.cutoff"))}</dt><dd>${esc(row.cutoff || "—")}</dd></div>
      <div><dt>${esc(t("filters.input"))}</dt><dd>${esc(row.in.map((kind) => t(`in.${kind}`)).join(", ") || "—")}</dd></div>
      <div><dt>${esc(t("filters.output"))}</dt><dd>${esc(row.out.map((kind) => t(`out.${kind}`)).join(", ") || "—")}</dd></div>
      ${row.tokenizer ? `<div><dt>${esc(t("fact.tokenizer"))}</dt><dd>${esc(row.tokenizer)}</dd></div>` : ""}
      ${row.expires ? `<div><dt>${esc(t("fact.expires"))}</dt><dd>${esc(row.expires)}</dd></div>` : ""}
    </dl>
    <h3>${esc(t("card.prices"))}</h3>
    <dl class="mc-facts wide">${priceRows.map(([key, value]) => `<div><dt>${esc(t(key))}</dt><dd>${value}</dd></div>`).join("")}</dl>
    ${row.params.length ? `<h3>${esc(t("card.params"))}</h3><div class="mc-params">${row.params.map((name) => `<code>${esc(name)}</code>`).join("")}</div>` : ""}
    <div class="mc-card-actions">
      <button type="button" class="mc-btn" data-open-url="${esc(row.site || `https://openrouter.ai/${row.id}`)}">${esc(t("card.site"))}</button>
      ${row.hf ? `<button type="button" class="mc-btn ghost" data-open-url="https://huggingface.co/${esc(row.hf)}">Hugging Face</button>` : ""}
      <button type="button" class="mc-btn ghost" data-copy="${esc(row.id)}">${esc(t("card.copy"))}</button>
    </div>`;
  clearTimeout(closeTimer);
  closeTimer = 0;
  delete card.dataset.closing;
  delete $("#scrim").dataset.closing;
  card.hidden = false;
  card.dataset.model = id;
  $("#scrim").hidden = false;
  renderBenchmarkPanels();
  loadBenchmarks();

  const focusClose = () => { const button = card.querySelector("[data-close]"); if (button && !card.hidden) button.focus({ preventScroll: true }); };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => setTimeout(focusClose, 0)); else focusClose();
}

let closeTimer = 0;
function closeCard() {
  const card = $("#card"), scrim = $("#scrim");
  if (card.hidden) return;
  const quiet = typeof matchMedia !== "function" || matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finish = () => { closeTimer = 0; card.hidden = true; scrim.hidden = true; delete card.dataset.closing; delete scrim.dataset.closing; };
  clearTimeout(closeTimer);
  if (quiet || typeof card.animate !== "function") { finish(); return; }

  card.dataset.closing = "1";
  scrim.dataset.closing = "1";
  closeTimer = setTimeout(finish, 170);
}

document.addEventListener("click", async (event) => {
  const model=event.target.closest(".mc-model");
  if(model&&!event.target.closest("button,input,label,select,a"))return openCard(model.dataset.model);
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.open) return openCard(target.dataset.open);
  if (target.hasAttribute("data-refresh-benchmarks")) return loadBenchmarks(true);
  if (target.hasAttribute("data-close")) return closeCard();
  if (target.hasAttribute("data-retry")) return load(true);
  if (target.hasAttribute("data-more")) { appendMore(); return; }
  if(target.dataset.layout) { state.layout=target.dataset.layout==="rows"?"rows":"cards";remember();render();return; }
  if(target.id==="filters-toggle") { state.filtersOpen=!state.filtersOpen;render();return; }
  if(target.dataset.purpose) {
    const key=target.dataset.purpose;
    state.filters=freshFilters();
    if(key==="reasoning"||key==="tools")state.filters.features=[key];
    else if(key==="vision")state.filters.inputs=["image"];
    else if(key==="image")state.filters.outputs=["image"];
    else if(key==="free")state.filters.price="free";
    state.shown=PAGE;remember();render();$("#main").scrollTop=0;return;
  }
  if(target.dataset.removeFilter) {
    const group=target.dataset.removeFilter,value=target.dataset.filterValue;
    if(Array.isArray(state.filters[group]))state.filters[group]=state.filters[group].filter(item=>item!==value);
    else state.filters[group]=group==="context"?0:group==="hideExpiring"?false:"any";
    state.shown=PAGE;remember();render();return;
  }
  if (target.hasAttribute("data-reset")) {
    state.filters = freshFilters();
    state.shown = PAGE; remember(); render(); return;
  }
  if (target.hasAttribute("data-show-compare")) { closeCard(); state.view = "compare"; render(); loadBenchmarks(); $("#main").scrollTop = 0; return; }
  if (target.hasAttribute("data-back")) { state.view = "list"; render(); return; }
  if (target.dataset.uncompare) {
    state.compare = state.compare.filter((id) => id !== target.dataset.uncompare);
    remember(); render(); return;
  }
  if (target.dataset.openUrl) { Memu.open({ url: target.dataset.openUrl }).catch(() => {}); return; }
  if (target.dataset.copy) {
    Memu.copy(target.dataset.copy).then(() => { target.textContent = t("card.copied"); }).catch(() => {});
  }
});

document.addEventListener("change", (event) => {
  const node = event.target;
  if (node.dataset.benchmarkVariant) {
    benchmarkVariants.set(node.dataset.benchmarkVariant+"|"+node.dataset.board,node.value);
    renderBenchmarkPanels();return;
  } else if (node.dataset.benchmarkBoard) {
    const id=node.dataset.benchmarkBoard;
    benchmarkBoards=benchmarkBoards.filter(board=>board!==id);
    if(node.checked&&benchmarkBoards.length<24)benchmarkBoards.push(id);
    remember();renderBenchmarkPanels();if(node.checked)benchmarks.ensure(id);return;
  } else if (node.dataset.filter) {
    const list = state.filters[node.dataset.filter].filter((value) => value !== node.value);
    if (node.checked) list.push(node.value);
    state.filters[node.dataset.filter] = list;
  } else if (node.dataset.filterSelect === "context") {
    state.filters.context = Number(node.value) || 0;
  } else if (node.dataset.filterSelect === "price") {
    state.filters.price = PRICES.includes(node.value) ? node.value : "any";
  } else if (node.dataset.filterFlag) {
    state.filters[node.dataset.filterFlag] = node.checked;
  } else if (node.dataset.compare) {
    const id = node.dataset.compare;
    state.compare = state.compare.filter((item) => item !== id);
    if (node.checked && state.compare.length < MAX_COMPARE) state.compare.push(id);
    remember();
    render();
    return;
  } else if (node.id === "sort") {
    state.sort = SORTS.includes(node.value) ? node.value : "newest";
  } else {
    return;
  }
  state.shown = PAGE;
  remember();
  render();
});

let searchTimer = 0;
document.addEventListener("input", (event) => {
  if (event.target.id === "search") {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = event.target.value; state.shown = PAGE; render(); }, 120);
  } else if (event.target.id === "org-search") {
    state.orgQuery = event.target.value;
    const caret = event.target.selectionStart;
    renderFilters();
    const field = $("#org-search");
    field.focus();
    field.setSelectionRange(caret, caret);
  }
});

$("#scrim").addEventListener("click", closeCard);
document.addEventListener("keydown", (event) => {

  if (event.key === "Escape" && !$("#card").hidden && !$("#card").dataset.closing) { event.preventDefault(); closeCard(); }
});

$("#mine").addEventListener("change", async (event) => {
  state.mine = event.target.checked;
  if (state.mine && !state.mySet) await loadMyModels();
  state.shown = PAGE;
  render();
});

$("#refresh").addEventListener("click", () => { load(true); loadMyModels().then(render); });

Memu.onLanguage(() => { renderSort(); render(); renderBenchmarkPanels(); });

if (typeof Memu.on === "function") Memu.on("navigate", (data) => {
  const query = String((data && data.query) || "").trim();
  const wanted = String((data && data.model) || "").toLowerCase();
  if (state.view !== "list") state.view = "list";
  state.query = query; state.shown = PAGE;
  const field = $("#search"); if (field) field.value = query;
  render();
  const rows = state.cache ? state.cache.rows : [];
  const tail = wanted.split("/").pop();
  const hit = rows.find((row) => row.id.toLowerCase() === wanted) || (tail ? rows.find((row) => row.id.toLowerCase().split("/").pop() === tail) : null);
  if (hit) openCard(hit.id);
});

renderSort();
render();
loadMyModels().then(render);
load();

if (Memu.toolbar && Memu.toolbar.adopt) Memu.toolbar.adopt({ hide: ".mc-brand", status: "#status", buttons: [{ id: "refresh", el: "#refresh", icon: "refresh" }] });
if (typeof Memu.ready === "function") Memu.ready();
