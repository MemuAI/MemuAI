

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const base = path.join(__dirname, "..", "app", "module_builtins", "benchmarks");
const src = fs.readFileSync(path.join(base, "data.js"), "utf8") + "\n" + fs.readFileSync(path.join(base, "main.js"), "utf8");
const dict = JSON.parse(fs.readFileSync(path.join(base, "i18n.json"), "utf8")).ru;

const rsc = (payload) => `<html><body><script>self.__next_f.push([1,${JSON.stringify(payload)}])</script></body></html>`;
const contender = (rank, name, org, value) => ({ rank, contenderName: `contenders/${name.toLowerCase().replace(/ /g, "-")}`, model: name,
  modelOrganization: org, license: "Proprietary", modelUrl: "https://example.com/" + rank, inputPricePerMillion: 4, outputPricePerMillion: 20,
  rankSpread: { min: rank, max: rank + 2 }, avgScore: { value, ci: 0.02, pipelines: 5 }, sessions: 1000 * rank });
const agentRows = [contender(1, "Claude Fable 5.1 (Max)", "Anthropic", 0.1455), contender(2, "Claude Opus 5.5 (High)", "Anthropic", 0.1378),
  contender(3, "Weak Model", "Lab", -0.21)];
const signal = (name, title, scores) => ({ name, displayName: title, description: `About ${title}.`, weight: 1,
  entries: agentRows.map((row, i) => ({ contenderName: row.contenderName, model: row.model, modelOrganization: row.modelOrganization,
    license: row.license, score: scores[i], ciLower: scores[i] - 0.01, ciUpper: scores[i] + 0.01, rank: 0 }))
    .sort((a, b) => b.score - a.score).map((row, i) => ({ ...row, rank: i + 1 })) });
const agentPayload = '44:["$","$L45",null,' + JSON.stringify({
  arena: { slug: "agent", title: "Agent", description: "How well models orchestrate tools.", categories: "$Q46" },
  snapshot: { lastUpdated: "2026-09-30T13:00:00.000Z", modelCount: 3, rows: agentRows,
    signals: [signal("task_outcome_explicit", "Confirmed Success", [0.17, 0.14, -0.2]),
      signal("steering_burden", "Steerability", [0.09, 0.12, -0.13])] },
}) + "]";
const textPayload = '12:' + JSON.stringify({ arena: { slug: "text", title: "Text", description: "Chat arena." },
  leaderboard: { arenaSlug: "text", entries: [
    { rank: 1, rankUpper: 1, rankLower: 2, modelDisplayName: "Alpha 1", modelOrganization: "A", rating: 1500, ratingLower: 1490, ratingUpper: 1510, votes: 9000 },
    { rank: 2, rankUpper: 1, rankLower: 3, modelDisplayName: "Beta 2", modelOrganization: "B", rating: 1480, ratingLower: 1470, ratingUpper: 1490, votes: 8000 }] } });
const pages = {
  "https://arena.ai/leaderboard/agent": process.env.AGENT_PAGE ? fs.readFileSync(process.env.AGENT_PAGE, "utf8") : rsc(agentPayload),
  "https://arena.ai/leaderboard/text": rsc(textPayload),
  "https://arena.ai/leaderboard": '<a href="/leaderboard/text">t</a><a href="/leaderboard/agent">a</a><a href="/leaderboard/agent/pareto">p</a>',
  "https://arena.ai/leaderboard/broken": rsc('1:{"nothing":"here"}'),
};

const nodes = {};
const el = () => ({ innerHTML: "", textContent: "", hidden: true, dataset: {}, checked: false, scrollTop: 0,
  setAttribute() {},
  addEventListener() {}, querySelector: () => ({ focus() {} }) });
const HOUR = 3600 * 1000;
const now = Date.now();
const fetched = [];
const ctx = {
  console, setTimeout, clearTimeout, performance, Date, Math, JSON, Promise, Set, Map, Number, String, Array, Object, Infinity,
  window: { addEventListener() {} },
  document: { querySelector: (s) => (nodes[s] ||= el()), getElementById: (s) => (nodes["#" + s] ||= el()), addEventListener() {} },
  Memu: {
    saved: { view: "overview", cache_arena: { "search::old-category": { at: now - 200 * HOUR, rows: [], categories: [] } } }, lang: "ru",
    t: (k, vars) => { let v = dict[k]; if (v && typeof v === "object") v = v.other; v = v ?? `??${k}`; for (const [a, b] of Object.entries(vars || {})) v = v.replace(`{${a}}`, b); return v; },
    i18n: { has: (k) => k in dict, number: (n, o) => n.toFixed(o?.maximumFractionDigits ?? 0) },
    save: async () => {},
    fetch: async ({ url }) => { fetched.push(url); if (!(url in pages)) throw new Error("no such page: " + url); return { text: pages[url] }; },
    models: async () => [], request: async () => ({ text: "" }), onLanguage() {}, copy: async () => {}, open: async () => {},
  },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.__done = (value) => process.stdout.write(JSON.stringify(value) + "\n");
vm.runInContext(`
  (async () => {
    const status = () => { renderStatus(); return document.querySelector("#status").textContent; };
    const out = { real: ${process.env.AGENT_PAGE ? "true" : "false"} };
    // Only a board opened a week ago is cached: nothing on screen is that old.
    out.statusOnlyStale = status();
    await loadArena("agent", "", true);
    const agent = state.cache.arena.agent;
    out.agentError = state.errors["arena:agent"] || "";
    out.agent = agent && { count: agent.rows.length, first: agent.rows[0], categories: agent.categories.map((row) => row.id),
      titles: agent.categories.map((row) => row.title), description: agent.description, url: agent.url };
    const data = boardData("arena:agent");
    out.agentScoreText = data && data.score.fmt(agent.rows[0].score);
    out.agentScoreLabel = data && data.score.label;
    out.agentRankText = data && data.rows[0].rankText;
    await loadArena("agent", "signal-steering_burden", true);
    const steer = state.cache.arena["agent::signal-steering_burden"];
    out.signal = steer && { first: steer.rows[0].model, score: steer.rows[0].score, lo: steer.rows[0].lo, votes: steer.rows[0].votes,
      price: steer.rows[0].priceIn, url: steer.url, description: steer.description };
    await loadArena("agent", "signal-no-such", true);
    out.unknownSignal = state.errors["arena:agent::signal-no-such"] || "";
    await loadArena("text", "", true);
    out.text = state.cache.arena.text && { first: state.cache.arena.text.rows[0].model, score: state.cache.arena.text.rows[0].score };
    await loadArena("broken", "", true);
    out.broken = state.errors["arena:broken"] || "";
    await loadArenaNav(true);
    out.nav = arenaSlugs();
    // The header: the oldest of what is SHOWN, not of everything ever cached.
    const hours = (n) => Date.now() - n * ${HOUR};
    state.cache.aa = { at: hours(4), rows: [] }; state.cache.livebench = { at: hours(4), rows: [], categories: [] };
    state.cache.simple = { at: hours(4), rows: [] }; state.cache.eq = { at: hours(4), rows: [] };
    for (const slug of ["text", "code/webdev", "vision", "text-to-image"]) state.cache.arena[slug] = { at: hours(4), rows: [], categories: [] };
    state.view = "overview";
    out.statusOverview = status();
    state.view = "board"; state.board = "arena:agent";
    out.statusBoard = status();
    state.board = "arena:search::old-category";
    out.statusOldBoard = status();
    state.board = "memu:index";
    state.cache.arena.vision.at = hours(30);
    out.statusIndex = status();
    out.fetched = ${JSON.stringify("x")} && null;
    __done(out);
  })().catch((error) => __done({ error: String(error && error.stack || error) }));
`, ctx);
