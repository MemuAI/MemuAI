
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const root = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(root, "build/audit-js/package.json"));
const { JSDOM } = require("jsdom");
const dom = new JSDOM('<!doctype html><html><body><div id="tab-quota" class="active"><div id="quota-root"></div></div></body></html>', { url: "http://127.0.0.1:8317", runScripts: "outside-only" });
const { window } = dom;
window.LANG = "ru";
window.$ = (selector) => window.document.querySelector(selector);
window.esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
window.toast = () => {};
window.confirm = () => true;
window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
window.HTMLDialogElement.prototype.close = function () { this.open = false; };
const model = (id, overrides = null) => ({ model: id, id: "api-" + id, label: id === "claude-opus-5.5" ? "Claude Opus 5.5" : id, canonical: id,
  default_price: { input: 4, output: 20, cache_read: .2, cache_write: 5, source: "catalog" }, override: overrides });
const models = [model("claude-opus-5.5"), model("gpt-example")];
let budgets = { accounts: [{ id: "a", label: '<script>alert("x")</script>', prefix: "api", provider: "custom", enabled: true,
  balance_usd: 10, remaining_usd: 8, spent_usd: 2, fraction: .8, requests: 15, started_at: 1700000000, models }],
  available: [{ id: "b", label: "Other API", provider: "custom", prefix: "other" }] };
const writes = [];
window.api = async (url, options) => {
  if (url === "/api/admin/quota/planner") return { now: Date.now() / 1000, settings: {}, accounts: [], api_budgets: budgets };
  if (url.endsWith("/models")) return { models };
  if (options?.method === "POST" && url.includes("/accounts/")) { writes.push({ url, body: options.body }); return budgets; }
  if (url.endsWith("/prices/refresh")) return budgets;
  throw new Error("Unexpected API request: " + url);
};
window.eval(fs.readFileSync(path.join(root, "app/static/quota-planner.js"), "utf8"));
await window.MemuQuota.load();
const q = (selector) => window.document.querySelector(selector);
assert.ok(q("#qp-api-add"));
assert.ok(q(".qp-budget-balance").textContent.includes("8"));
assert.equal(q("#quota-root script"), null, "account labels must remain text");
q("[data-budget-edit]").click();
assert.ok(q(".qp-budget-dialog").open);
assert.equal(q("#qp-budget-balance").value, "", "editing tariffs must not reset the balance period");
const first = q(".qp-price-model");
first.querySelector("[data-custom]").click();
assert.equal(first.querySelector('[data-rate="input"]').disabled, false);
first.querySelector('[data-rate="input"]').value = "2";
first.querySelector("[data-caching]").checked = false;
q(".qp-budget-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(writes.length, 1);
assert.equal(Object.hasOwn(writes[0].body, "balance_usd"), false);
assert.equal(writes[0].body.models["claude-opus-5.5"].input, 2);
assert.equal(writes[0].body.models["claude-opus-5.5"].caching, false);
assert.equal(Object.hasOwn(writes[0].body.models, "gpt-example"), false);
assert.equal(q(".qp-budget-dialog"), null);

q("#qp-api-add").click();
q("#qp-budget-select").value = "b";
q("#qp-budget-select").dispatchEvent(new window.Event("change"));
await new Promise((resolve) => setTimeout(resolve, 0));
q("#qp-budget-balance").value = "25.5";
q("#qp-budget-search").value = "opus";
q("#qp-budget-search").dispatchEvent(new window.Event("input"));
q("#qp-budget-discount").value = "50";
q("#qp-apply-discount").click();
assert.equal(q(".qp-price-model").querySelector('[data-rate="input"]').value, "2");
assert.equal(q(".qp-price-model").querySelector('[data-rate="output"]').value, "10");
assert.equal(window.document.querySelectorAll(".qp-price-model")[1].hidden, true);
assert.equal(window.document.querySelectorAll(".qp-price-model")[1].querySelector("[data-custom]").checked, false);
q(".qp-budget-form").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
await new Promise((resolve) => setTimeout(resolve, 0));
assert.equal(writes.length, 2);
assert.equal(writes[1].body.balance_usd, 25.5);
assert.equal(writes[1].body.models["claude-opus-5.5"].cache_read, .1);
assert.equal(Object.hasOwn(writes[1].body.models, "gpt-example"), false);

window.LANG = "en";
window.MemuQuota.render();
assert.equal(q("#qp-api-add").textContent, "Add account");
assert.ok(q(".qp-budget-disclaimer").textContent.includes("not your provider balance"));
dom.window.close();
console.log("PASS: API budget DOM setup, scoped rates, discounts, balance preservation, escaping and language");
