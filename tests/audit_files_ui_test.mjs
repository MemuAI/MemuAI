

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import path from "node:path";
import { createRequire } from "node:module";

const root = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(root, "build/audit-js/package.json"));
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<!doctype html><html><body><div id="panel"></div></body></html>', { url: "http://127.0.0.1:8132/" });
for (const key of ["window", "document", "Node", "HTMLElement", "HTMLTextAreaElement", "HTMLInputElement", "KeyboardEvent", "MouseEvent", "Event", "CustomEvent", "navigator", "location", "localStorage", "sessionStorage", "getComputedStyle"])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.requestAnimationFrame = (fn) => setTimeout(fn, 0);
const js = (name) => pathToFileURL(path.join(root, "app/memufiles/static/js", name)).href;
const { S } = await import(js("core.js"));
const { setLang } = await import(js("i18n.js"));
const ai = await import(js("ai.js"));
S.token = "test-token"; S.home = "C:\\work"; S.platform = "nt";
setLang("en");
let selected = "";
let pendingReader;
const sent = [];
const models = [{ id: "model-1", label: "First model", provider: "alpha" },
  { id: "model-2", label: "Second model", provider: "beta", context: 128000 }];
globalThis.fetch = async (url, init = {}) => {
  if (url.startsWith("/api/ai/status")) return Response.json({ enabled: true, online: true, models, model: selected || "removed-model", default_model: "model-2" });
  if (url === "/api/settings") { selected = JSON.parse(init.body).ai_model; return Response.json({ ok: true }); }
  if (url === "/api/ai/stop") return Response.json({ ok: true });
  if (url === "/api/ai/chat") {
    sent.push(JSON.parse(init.body));
    return { ok: true, body: { getReader() { return { read() { return new Promise((resolve) => { pendingReader = resolve; }); }, cancel: async () => {} }; } } };
  }
  throw new Error("Unexpected request: " + url);
};
const app = { pane: () => ({ folder: S.home, selectedPaths: () => [] }), ops: new Map() };
const panel = document.querySelector("#panel");
const tick = () => new Promise((r) => setTimeout(r, 15));
ai.mount(app, panel);
await tick();
assert.equal(panel.querySelector(".ai-model").textContent, "Second model", "stale model falls back to the valid default");
panel.querySelector(".ai-model").click();
await tick();
const search = document.querySelector(".ai-model-picker input");
search.value = "alpha";
search.dispatchEvent(new Event("input", { bubbles: true }));
assert.equal(document.querySelectorAll(".ai-model-row").length, 1);
document.querySelector(".ai-model-row").click();
await tick();
assert.equal(selected, "model-1");
assert.equal(panel.querySelector(".ai-model").textContent, "First model");
assert.equal(document.querySelector(".modal-back"), null);
const input = panel.querySelector("textarea");
input.value = "question";
input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }));
await tick();
assert.equal(sent.length, 0, "IME Enter must not send a half-composed message");
input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
await tick();
assert.equal(sent.length, 1);
assert.equal(sent[0].model, "model-1");
assert.equal(panel.querySelector(".ai-input .primary").hidden, true);

panel.querySelector(".ai-top .icon-btn").click();
await tick();
const newLog = panel.querySelector(".ai-log");
assert.equal(panel.querySelector(".ai-input .primary").hidden, false);
pendingReader({ done: false, value: new TextEncoder().encode('data: {"t":"delta","text":"OLD ANSWER"}\n\n') });
await tick();
assert.equal(newLog.textContent.includes("OLD ANSWER"), false);
assert.equal(newLog.querySelectorAll(".ai-msg.user").length, 0);
console.log("PASS: model fallback, searchable picker, persistence, IME, busy state, new-chat stream isolation");
dom.window.close();
