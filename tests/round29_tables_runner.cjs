/* Round 29 (r29-tables-diagrams): runs pgstatic/tables.js and pgstatic/diagrams.js in node with a tiny DOM shim
   and prints JSON results for tests/round29_tables_test.py and tests/round29_diagrams_test.py (SVG validity is
   checked there with ElementTree). Usage: node tests/round29_tables_runner.cjs */
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..", "app", "pgstatic");
const listeners = {};
const el = () => ({
  style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, removeAttribute() {},
  appendChild() {}, remove() {}, querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
  getContext() {
    return { font: "12px sans", fillStyle: "#000000", measureText(text) { const m = /(\d+(?:\.\d+)?)px/.exec(this.font); return { width: String(text).length * (m ? +m[1] : 12) * 0.56 }; } };
  },
});
const documentShim = {
  readyState: "complete", documentElement: { lang: process.env.MEMU_LANG || "en", dataset: { theme: "dark" }, style: {} },
  createElement: el, getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
  addEventListener(type, fn) { listeners[type] = fn; }, removeEventListener() {}, head: { appendChild() {} }, body: { appendChild() {} },
};
const store = {};
const ctx = {
  console, document: documentShim, navigator: {}, setTimeout, clearTimeout, requestAnimationFrame: (f) => setTimeout(f, 0), cancelAnimationFrame: clearTimeout,
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  getComputedStyle: () => ({ getPropertyValue: (n) => ({ "--bg": "#16181d", "--panel": "#1d2027", "--text": "#e6e8eb", "--muted": "#9aa1ad", "--accent": "#efa58b", "--border": "#2f343e" }[n] || "") }),
  matchMedia: () => ({ matches: false }), MutationObserver: class { observe() {} }, Blob, TextEncoder, URL, Intl, CSS: { escape: (s) => s },
  Event: class { constructor(type) { this.type = type; } }, FileReader: class {},
};
ctx.window = ctx;
vm.createContext(ctx);
for (const file of ["tables.js", "diagrams.js"]) vm.runInContext(fs.readFileSync(path.join(root, file), "utf8"), ctx, { filename: file });
const T = ctx.MemuTables, D = ctx.MemuDiagrams;

(async () => {
  const out = { tables: {}, diagrams: {} };
  // ---------------- tables
  const md = "## Prices\n\n| Name | Price | Note |\n|:-----|------:|:----:|\n| Apple | 1,20 | `a|b` |\n| Pear \\| x | 3.5 | |\n";
  out.tables.parsed = T.parseMarkdown(md);
  out.tables.markdown = T.toMarkdown(out.tables.parsed);
  out.tables.reparsed = T.parseMarkdown(out.tables.markdown);
  out.tables.csv = T.toCSV({ headers: ["a", "b"], rows: [["x,y", 'say "hi"'], ["multi\nline", " pad "]] });
  out.tables.tsv = T.toTSV({ headers: ["a", "b"], rows: [["1", "tab\there"]] });
  out.tables.json = JSON.parse(T.toJSON({ headers: ["n", "", "flag"], rows: [["12", "x", "true"], ["1.5", "y", "no"]] }));
  out.tables.html = T.toHTML({ headers: ["<b>", "x"], rows: [["&", "1"]] });
  out.tables.excel = T.parseDelimited("Name\tQty\r\nA\t1\r\n\"B\nC\"\t2\r\n");
  out.tables.semicolon = T.parseDelimited("a;b;c\n1;2;3\n");
  out.tables.csvQuoted = T.parseDelimited('a,b\n"x, y","z ""q"""\n');
  out.tables.numbers = ["1 234,5", "$1,234.50", "12%", "−3", "1.2k", "abc", "", "1,5", "2.5 млн", "€10"].map((v) => T.parseNumber(v));
  out.tables.kinds = [T.columnKind([["1"], ["2"], ["x"]], 0), T.columnKind([["1"], ["2"], ["3"], ["4"], ["x"]], 0), T.columnKind([["01.02.2024"], ["2024-05-01"]], 0)];
  const sorted = [["10"], ["9"], ["100"], [""]].sort((a, b) => T.compareValues(a[0], b[0], "number")).map((r) => r[0]);
  out.tables.sorted = sorted;
  // streaming masks
  out.tables.mask = {
    partialRow: T.maskPending("Intro\n\n| A | B |\n|---|---|\n| 1 | 2 |\n| 3 |"),
    headerOnly: T.maskPending("Intro\n\n| A | B"),
    twoRowsNewline: T.maskPending("| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"),
    closedByText: T.maskPending("| A | B |\n|---|---|\n| 1 | 2 |\n\nAfter"),
    closedByBlank: T.maskPending("| A | B |\n|---|---|\n| 1 | 2 |\n\n"),
    inCode: T.maskPending("```\n| a | b |"),
    noTable: T.maskPending("plain | text"),
  };
  out.tables.pendingHtml = T.block("memuai-table-pending", "2 3", true);
  out.tables.pendingOther = T.block("python", "x", true);
  out.tables.svg = T.renderSVG(out.tables.parsed, T.presetStyle("glass", { title: "Prices", caption: "Source: test" })).svg;
  out.tables.svgAll = T.PRESETS.map((p) => T.renderSVG(out.tables.parsed, T.presetStyle(p.id)).svg);
  const xlsx = T.toXLSX({ headers: ["Name", "Qty", "Price"], rows: [["Apple", "3", "1.5"], ["Pear <&>", "10", "2"]], title: "Fruit" });
  out.tables.xlsxB64 = Buffer.from(await xlsx.arrayBuffer()).toString("base64");
  out.tables.presets = T.PRESETS.length;
  // ---------------- diagrams
  const specs = {
    bar: { type: "bar", title: "Revenue", labels: ["Q1", "Q2", "Q3", "Q4"], series: [{ name: "2024", data: [12, 15, 11, 19] }, { name: "2025", data: [14, 18, 16, 22] }], y: { title: "M$", unit: "$" } },
    stacked: { type: "stacked-bar", labels: ["A", "B"], series: [{ name: "x", data: [1, -2] }, { name: "y", data: [3, 4] }] },
    hbar: { type: "barh", labels: ["Long label one", "Two"], series: [{ name: "v", data: [5, 7] }] },
    line: { type: "line", smooth: true, labels: ["1", "2", "3", "4", "5"], series: [{ name: "a", data: [1, 3, null, 2, 5] }] },
    area: { type: "area", stacked: true, labels: ["a", "b", "c"], series: [{ name: "s1", data: [1, 2, 3] }, { name: "s2", data: [2, 2, 2] }] },
    pie: { type: "pie", data: [{ label: "A", value: 30 }, { label: "B", value: 70 }] },
    donut: { type: "doughnut", labels: ["A", "B", "C"], series: [{ name: "Share", data: [1, 1, 2] }], y: { unit: "%" } },
    scatter: { type: "scatter", series: [{ name: "pts", data: [[1, 2], [3, 4, 5, "big"]] }], x: { title: "x" } },
    radar: { type: "radar", labels: ["Speed", "Power", "Range", "Cost"], series: [{ name: "A", data: [3, 4, 2, 5] }, { name: "B", data: [4, 2, 5, 3] }] },
    combo: { type: "combo", labels: ["J", "F", "M"], series: [{ name: "Sales", type: "bar", data: [3, 4, 5] }, { name: "Trend", type: "line", data: [3.2, 4.1, 4.9] }] },
    table: { type: "line", data: { columns: ["Month", "A", "B"], rows: [["Jan", 1, 2], ["Feb", 3, 4]] } },
    many: { type: "bar", labels: Array.from({ length: 40 }, (_, i) => "Category number " + i), series: [{ name: "v", data: Array.from({ length: 40 }, (_, i) => i * 1000) }] },
  };
  out.diagrams.charts = {};
  for (const [name, spec] of Object.entries(specs)) {
    try {
      const parsed = D.parseSpec(JSON.stringify(spec));
      const r = D.renderChart(parsed, { labels: name === "bar", legend: name === "donut" ? "bottom" : "auto" });
      out.diagrams.charts[name] = { ok: true, type: r.type, svg: r.svg, width: r.width, height: r.height, series: parsed.series.length, labels: parsed.labels.length };
    } catch (e) { out.diagrams.charts[name] = { ok: false, error: String(e.message || e) }; }
  }
  out.diagrams.typeSwitch = D.renderChart(D.parseSpec(JSON.stringify(specs.bar)), { type: "pie" }).type;
  out.diagrams.badType = D.renderChart(D.parseSpec(JSON.stringify(specs.bar)), { type: "scatter" }).type;
  out.diagrams.errors = ["", "{oops", "[1,2]", '{"type":"bar","series":[]}', '{"type":"wat","series":[{"data":[1]}]}', '{"type":"pie","labels":["a"],"series":[{"data":[-1]}]}', '{"type":"bar","series":[{"data":["x"]}]}']
    .map((src) => { try { D.parseSpec(src); return "parsed"; } catch (e) { return String(e.message || e); } });
  out.diagrams.lenient = (() => { try { return D.parseSpec("{'type': 'bar', 'labels': ['a','b',], 'series': [{'data': [1,2]}],}").type; } catch (e) { return "fail: " + e.message; } })();
  out.diagrams.evilColor = D.renderChart(D.parseSpec(JSON.stringify({ type: "bar", labels: ["a"], series: [{ name: "x", color: "url(http://evil.example/#a)", data: [1] }] })), {}).svg;
  out.diagrams.csv = D.toCSV(JSON.stringify(specs.bar));
  out.diagrams.prompt = { auto: D.prompt("auto"), ask: D.prompt("ask"), off: D.prompt("off") };
  out.diagrams.mask = {
    open: D.maskPending("Text\n```memuai-chart\n{\"type\":"),
    closedAtEnd: D.maskPending("Text\n```mermaid\ngraph TD\nA-->B\n```\n"),
    closedMiddle: D.maskPending("```mermaid\ngraph TD\nA-->B\n```\nAfter the scheme"),
    none: D.maskPending("just text"),
  };
  out.diagrams.block = {
    chart: D.block("memuai-chart", JSON.stringify(specs.bar), true),
    mermaid: D.block("mermaid", "graph TD\nA-->B", false),
    pending: D.block("memuai-diagram-pending", "mermaid", true),
    other: D.block("js", "1", true),
  };
  out.diagrams.strip = D.stripForText("a\n```memuai-chart\n{}\n```\nb\n```mermaid\ngraph TD\n```", true);
  out.diagrams.ticks = D.niceTicks(0, 97, 5);
  out.diagrams.fmt = [D.fmtNum(1234567, "", true), D.fmtNum(12.5, "%"), D.fmtNum(-3, "$")];
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
