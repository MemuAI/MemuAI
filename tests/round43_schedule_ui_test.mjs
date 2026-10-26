
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(path.resolve("build/audit-js/package.json"));
const { JSDOM } = require("jsdom");
const dom = new JSDOM("<!doctype html><body></body>", { url: "http://127.0.0.1/", runScripts: "outside-only" });
const w = dom.window;
const requests = [], errors = [];
const tools = [{ name: "telegram_use_history", connected: false, description: "Read chat history",
  parameters: { properties: { chat: { type: "string" }, limit: { type: "integer" } } } },
  { name: "chrome_tabs", connected: true, parameters: { properties: {} } }];
w.PG_LANG = "en";
w.S = { models: [{ id: "fake-model" }], workspaces: { items: [] } };
w.currentModel = () => "fake-model";
w.panel = (html) => { w.document.body.innerHTML = html; };
w.icon = () => "";
w.toast = (message, type) => { if (type === "err") errors.push(message); };
w.visibleModels = () => w.S.models;
w.api = async (url, options) => {
  requests.push({ url, body: options?.body });
  if (url === "/api/plan/meta") return { os: "windows", integrations: tools };
  if (url.startsWith("/api/plan/tasks")) return { tasks: [], results: [], active: [] };
  if (url === "/api/plan/preview") return { summary: "Every day", next: [], events: [], warnings: [], rule: options.body.task.rule };
  if (url === "/api/plan/save") return { id: "saved" };
  throw new Error(`Unexpected API ${url}`);
};
w.eval(fs.readFileSync("app/pgstatic/plan-ui.js", "utf8"));
w.eval(fs.readFileSync("app/pgstatic/plan.js", "utf8"));
const pause = (ms = 350) => new Promise((resolve) => setTimeout(resolve, ms));
const draft = { id: "test", name: "Telegram summary", kind: "integration", tool: "telegram_use_history",
  args: { chat: "123", limit: 50 }, rule: { when: [{ type: "daily", times: ["09:00"] }] } };
await w.MemuPlan.openBuilder(draft);
await pause(20);
assert.match(w.document.body.textContent, /disconnected/);
assert.equal(w.document.querySelector('[data-path="args.limit"]').value, "50");
assert.equal(w.document.querySelector('[data-path="args.chat"]').value, "123");

let limit = w.document.querySelector('[data-path="args.limit"]');
limit.value = "25";
limit.dispatchEvent(new w.Event("input", { bubbles: true }));
await pause();
let preview = requests.filter((r) => r.url === "/api/plan/preview").at(-1).body.task;
assert.equal(preview.args.limit, 25);
assert.equal(typeof preview.args.limit, "number");
assert.equal(JSON.parse(w.document.querySelector('[data-path="integration_args"]').value).limit, 25);

limit.value = "";
limit.dispatchEvent(new w.Event("input", { bubbles: true }));
await pause();
preview = requests.filter((r) => r.url === "/api/plan/preview").at(-1).body.task;
assert.ok(!Object.hasOwn(preview.args, "limit"), "An empty optional number must not silently become 0");

let editor = w.document.querySelector('[data-path="integration_args"]');
editor.value = '{"chat": "321", "nested": {"ids": [1, 2]}}';
editor.dispatchEvent(new w.Event("input", { bubbles: true }));
await pause();
preview = requests.filter((r) => r.url === "/api/plan/preview").at(-1).body.task;
assert.equal(JSON.stringify(preview.args.nested.ids), "[1,2]");
assert.equal(w.document.querySelector('[data-path="args.chat"]').value, "321", "JSON and friendly fields must show the same target chat");
assert.ok(!Object.hasOwn(preview, "integration_args"), "UI draft fields must not leak into saved tasks");

editor.value = "{";
editor.dispatchEvent(new w.Event("input", { bubbles: true }));
await pause();
assert.match(w.document.querySelector("#pln-summary").textContent, /invalid JSON/);
assert.ok(w.document.querySelector('[data-act="save"]').disabled);
const before = requests.filter((r) => r.url === "/api/plan/save").length;
w.document.querySelector("#pln-builder").dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", ctrlKey: true, bubbles: true }));
await pause(20);
assert.equal(requests.filter((r) => r.url === "/api/plan/save").length, before, "Ctrl+Enter cannot bypass invalid JSON");

const chooser = w.document.querySelector('[data-path="tool"]');
chooser.value = "chrome_tabs";
chooser.dispatchEvent(new w.Event("change", { bubbles: true }));
await pause(20);
assert.equal(w.document.querySelector('[data-path="integration_args"]').value, "{}");
preview = requests.filter((r) => r.url === "/api/plan/preview").at(-1).body.task;
assert.equal(preview.tool, "chrome_tabs");
assert.equal(Object.keys(preview.args).length, 0, "Switching tools must clear another tool's arguments");
assert.equal(errors.length, 0, "Invalid arguments are explained inline by the builder");
dom.window.close();
console.log("PASS: scheduled integration builder, typed arguments, nested JSON, invalid-JSON/shortcut guard and tool switching");
