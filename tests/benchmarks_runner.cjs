

const fs = require("fs");
const vm = require("vm");
const path = require("path");

const base = path.join(__dirname, "..", "app", "module_builtins", "benchmarks");
const src = fs.readFileSync(path.join(base, "..", "benchmarks", "data.js"), "utf8") + "\n" + fs.readFileSync(path.join(base, "main.js"), "utf8");
const dict = JSON.parse(fs.readFileSync(path.join(base, "i18n.json"), "utf8")).ru;
const nodes = {};
const el = () => ({ innerHTML: "", textContent: "", hidden: true, dataset: {}, checked: false, scrollTop: 0,
  setAttribute() {},
  addEventListener() {}, querySelector: () => ({ focus() {} }) });
const now = Date.now();
const mk = (names, scores) => names.map((model, i) => ({ model, org: "x", score: scores[i] }));
const names = ["Alpha 1", "Beta 2", "Gamma 3", "Delta 4", "Eps 5", "Zeta 6"];
const saved = {
  view: "board", board: "memu:index",
  cache_arena: {
    text: { at: now, rows: mk(names, [1500, 1480, 1450, 1400, 1300, 1200]), categories: [] },
    "code/webdev": { at: now, rows: mk(["beta-2", "alpha-1", "gamma-3", "delta-4", "eps-5"], [1400, 1390, 1300, 1200, 1100]), categories: [] },
  },
  cache_aa: { at: now, rows: names.map((model, i) => ({ model, org: "x", deprecated: model === "Zeta 6",
    m: { intelligence: 70 - i * 5, gpqa: null, hle: null, scicode: null, lcr: null, mmmu: null, gdpval: null, critpt: null,
      omniscience: null, speed: null, ttft: null, priceIn: null, priceOut: null } })) },
  cache_simple: { at: now, rows: mk(["Gamma 3", "Alpha 1", "Beta 2", "Delta 4", "Solo 9"], [60, 50, 40, 30, 99]) },
};
const requests = [];
const ctx = {
  console, setTimeout, clearTimeout, performance, Date, Math, JSON, Promise, Set, Map, Number, String, Array, Object, Infinity,
  window: { addEventListener() {} },
  document: { querySelector: (s) => (nodes[s] ||= el()), getElementById: (s) => (nodes["#" + s] ||= el()), addEventListener() {} },
  Memu: {
    saved, lang: "ru",
    t: (k, vars) => { let v = dict[k]; if (v && typeof v === "object") v = v.other; v = v ?? `??${k}`; for (const [a, b] of Object.entries(vars || {})) v = v.replace(`{${a}}`, b); return v; },
    i18n: { has: (k) => k in dict, number: (n, o) => n.toFixed(o?.maximumFractionDigits ?? 0) },
    save: async () => {}, fetch: async () => { throw new Error("offline"); },
    models: async () => [{ id: "m-a", name: "A" }, { id: "m-b", name: "B" }, { id: "m-j", name: "J" }],
    request: async (body) => {
      requests.push({ model: body.model, text: body.text });
      if (body.model === "m-j") {

        if (/m-a|m-b/.test(body.text)) throw new Error("judge saw model ids");

        const scores = {};
        for (const match of body.text.matchAll(/### ([A-H])\n([\s\S]*?)(?=\n\n### |$)/g)) scores[match[1]] = /good/.test(match[2]) ? 9 : 3;
        return { text: "Оценки: " + JSON.stringify(scores) };
      }
      if (/ОТВЕТ/.test(body.text)) return { text: body.model === "m-a" ? "Пояснение\nОТВЕТ: 20" : "ОТВЕТ: 10" };
      if (/JSON/.test(body.text)) return { text: body.model === "m-a" ? '["Марс","Венера","Земля","Юпитер","Сатурн"]' : "Вот: Марс, Венера" };
      return { text: body.model === "m-a" ? "a good answer" : "a weak answer" };
    },
    onLanguage() {}, copy: async () => {}, open: async () => {},
  },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.__done = (value) => process.stdout.write(JSON.stringify(value) + "\n");
vm.runInContext(`
  (async () => {
    const index = indexData();
    const rows = index.rows.slice().sort((a, b) => b.score - a.score);
    renderBoard(document.querySelector("#main"));
    const html = document.querySelector("#main").innerHTML;
    state.own.models = ["m-a", "m-b"]; state.own.judge = "m-j";
    state.myList = [{ id: "m-a", name: "A" }, { id: "m-b", name: "B" }, { id: "m-j", name: "J" }];
    await runOwn();
    const run = state.own.runs[0];
    renderOwn(document.querySelector("#main"));
    // A model absent from two boards must not lose to a rival that is on every board and only second.
    const fillers = Array.from({ length: 28 }, (_, i) => "Filler " + i);
    const grid = () => ["Titan A", "Titan B", ...fillers].map((model, i) => ({ model, org: "x", score: 1000 - i * 10 }));
    const twin = () => grid().filter((row) => row.model !== "Titan A");
    state.cache.arena = {
      text: { at: Date.now(), rows: grid(), categories: [] },
      "code/webdev": { at: Date.now(), rows: grid(), categories: [] },
      vision: { at: Date.now(), rows: twin().map((row, i) => ({ ...row, score: 1000 - i * 10 })), categories: [] },
    };
    state.cache.aa = { at: Date.now(), rows: grid().map((row, i) => ({ model: row.model, org: "x", deprecated: false,
      m: { intelligence: 90 - i, gpqa: null, hle: null, scicode: null, lcr: null, mmmu: null, gdpval: null, critpt: null,
        omniscience: null, speed: null, ttft: null, priceIn: null, priceOut: null } })) };
    state.cache.simple = { at: Date.now(), rows: grid().map((row, i) => ({ ...row, score: 90 - i })) };
    state.cache.livebench = { at: Date.now(), release: "", categories: ["Coding"], rows: twin().map((row, i) => ({
      model: row.model, key: row.model, org: "x", global: 90 - i, complete: true, cat: { Coding: 90 - i } })) };
    state.cache.eq = { at: Date.now(), rows: twin().map((row, i) => ({ ...row, score: 1500 - i * 5, scenarios: 10, lo: 0, hi: 0 })) };
    const titans = indexData();
    const ranked = titans.rows.slice().sort((a, b) => b.score - a.score || b.coverage - a.coverage);
    const pick = (name) => titans.rows.find((row) => row.model === name);
    __done({
      titan: { order: ranked.slice(0, 2).map((row) => row.model), scoreA: +pick("Titan A").score.toFixed(2), scoreB: +pick("Titan B").score.toFixed(2),
        countA: pick("Titan A").count, countB: pick("Titan B").count, coverageA: +pick("Titan A").coverage.toFixed(2),
        coverageB: +pick("Titan B").coverage.toFixed(2), sources: titans.parts.length },
      parts: index.parts.map((p) => p.id),
      order: rows.map((r) => r.model),
      scores: Object.fromEntries(rows.map((r) => [r.model, +r.score.toFixed(2)])),
      raw: Object.fromEntries(rows.map((r) => [r.model, +r.raw.toFixed(2)])),
      counts: Object.fromEntries(rows.map((r) => [r.model, r.count])),
      method: html.includes("bm-method"),
      catalog: catalog().map((g) => g.id),
      boards: catalog().flatMap((g) => g.boards.map((b) => b.id)),
      overview: OVERVIEW,
      own: { totals: run.totals, tasks: run.tasks.map((t) => t.id), judgeError: run.judgeError || "",
        judged: run.tasks.filter((t) => !t.auto).length, html: document.querySelector("#main").innerHTML.includes("bm-own-results") },
      missingI18n: (html + document.querySelector("#main").innerHTML).match(/\\?\\?[a-z0-9._-]+/g) || [],
    });
  })().catch((error) => __done({ error: String(error && error.stack || error) }));
`, ctx);
