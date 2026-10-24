// Round 33 (#20): MemuToolDetails.capture/rowHtml for every kind of tool, RU and EN. Prints JSON for
// tests/round33_chat_test.py (ToolDetailsJs).
"use strict";
const path = require("path");
global.document = { addEventListener() {} };
global.icon = (name) => `<svg data-i="${name}"></svg>`;
global.PG_LANG = "ru";
const T = require(path.join(__dirname, "..", "app", "pgstatic", "tool-details.js"));

const XSS = '<img src=x onerror=alert(1)>';
const cases = {
  web_search: { tool: "web_search", args: { query: "погода Москва" }, ms: 2985,
    result: { query: "погода Москва", count: 6, results: [
      { title: "Гидрометцентр " + XSS, url: "https://meteoinfo.ru/forecasts", snippet: "Прогноз" },
      { title: "Яндекс Погода", url: "javascript:alert(1)", snippet: "x" }] } },
  web_fetch: { tool: "web_fetch", args: { url: "https://example.com/" }, ms: 640,
    result: { url: "https://example.com/", title: "Example Domain", text: "x".repeat(1256), truncated: false } },
  docs_read: { tool: "docs_read", args: { id: "quick-ask" }, ms: 30,
    result: { id: "quick-ask", title: "Быстрый доступ (Alt+Space)", summary: "Помощник поверх всех программ",
      sections: ["Как пользоваться", "Сочетание клавиш"] } },
  docs_search: { tool: "docs_search", args: { query: "orfografiya" }, ms: 12,
    result: { query: "orfografiya", results: [{ id: "chat", title: "Чат", section: "Орфография", anchor: "spell" }] } },
  code_run: { tool: "code_run", args: { command: "npm test" }, ms: 15400,
    result: { command: "npm test", exit_code: 1, stdout: "1 failing", stderr: "Error: boom" } },
  work_write: { tool: "work_write", args: { path: "src/app.py", content: "print(1)" }, ms: 20,
    result: { path: "src/app.py", created: true } },
  work_search: { tool: "work_search", args: { pattern: "TODO" }, ms: 50,
    result: { matches: [{ path: "a.py", line: 3, text: "# TODO fix" }], count: 1 } },
  disk_space: { tool: "disk_space", args: {}, ms: 3, result: { value: { total: 512 * 1024 ** 3, used: 400 * 1024 ** 3, free: 112 * 1024 ** 3 } } },
  project_tree: { tool: "project_tree", args: { project_id: "p1" }, ms: 40, result: { value: ["README.md", "src/app.py", "src/util.py"] } },
  file_exists: { tool: "file_exists", args: { path: "README.md", project_id: "p1" }, ms: 2, result: { value: { exists: true, file: true, directory: false } } },
  file_missing: { tool: "file_exists", args: { path: "nope.txt" }, ms: 2, result: { value: { exists: false, file: false, directory: false } } },
  system_info: { tool: "system_info", args: {}, ms: 2, result: { value: { os: "Windows-11-10.0.26100-SP0", machine: "AMD64", cpu: 8, python: "3.12.6" } } },
  memory_row: null,
  failed: { tool: "web_fetch", args: { url: "https://bad.example/" }, ms: 120, ok: false, error: "HTTP 404 " + XSS, result: {} },
  unknown: { tool: "weird_" + "tool", args: { q: XSS }, ms: 5, result: { title: XSS } },
};

function render(lang) {
  global.PG_LANG = lang;
  const out = {};
  for (const [name, c] of Object.entries(cases)) {
    let row;
    if (name === "memory_row") {
      row = T.memoryRow({ added: [{ text: "Пьёт кофе без сахара" }], updated: [{ text: "Живёт в Москве" }], pending: [1] });
    } else {
      row = { tool: c.tool, args: c.args, ms: c.ms, ok: c.ok !== false, error: c.error || "", summary: "",
        detail: T.capture(c.tool, c.args, c.result) };
    }
    out[name] = { html: T.rowHtml(row, "k-" + name), detail: row.detail, head: T.headline(row) };
  }
  return out;
}
const result = { ru: render("ru"), en: render("en"), legacy: null };
// A row saved before Round 33 has no detail: still a readable title from its arguments.
global.PG_LANG = "en";
result.legacy = T.rowHtml({ tool: "docs_read", args: { id: "quick-ask" }, ok: true, ms: 0 }, "");
process.stdout.write(JSON.stringify(result));
