// Runs the Model catalog module (app/module_builtins/catalog/main.js) in a VM
// with a fake DOM and bridge on a small OpenRouter-shaped fixture. Prints one
// JSON line; round10_test.py asserts on it.
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const base = path.join(__dirname, "..", "app", "module_builtins", "catalog");
const src = fs.readFileSync(path.join(base, "..", "benchmarks", "data.js"), "utf8") + "\n" + fs.readFileSync(path.join(base, "main.js"), "utf8");
const dict = JSON.parse(fs.readFileSync(path.join(base, "i18n.json"), "utf8")).ru;
const model = (id, name, created, ctxLen, prompt, completion, inputs, outputs, params, extra = {}) => ({
  id, name, created, context_length: ctxLen, description: `${name} description`,
  architecture: { input_modalities: inputs, output_modalities: outputs, tokenizer: "Other" },
  pricing: { prompt, completion, ...(extra.pricing || {}) },
  top_provider: { context_length: ctxLen, max_completion_tokens: 32000, is_moderated: false },
  supported_parameters: params, knowledge_cutoff: extra.cutoff || null, expiration_date: extra.expires || null,
  hugging_face_id: extra.hf || null,
});
const fixture = JSON.stringify({ data: [
  model("anthropic/claude-opus-5", "Anthropic: Claude Opus 5", 1780000000, 1000000, "0.000005", "0.000025",
    ["text", "image", "file"], ["text"], ["tools", "reasoning", "response_format"], { pricing: { input_cache_read: "0.0000005" }, cutoff: "2026-03" }),
  model("google/gemini-3.5-flash", "Google: Gemini 3.5 Flash", 1785000000, 1048576, "0.0000003", "0.0000025",
    ["text", "image", "audio", "video"], ["text"], ["tools", "structured_outputs"]),
  model("openai/gpt-image-2", "OpenAI: GPT Image 2", 1770000000, 32000, "0.00001", "0.00004", ["text", "image"], ["image", "text"], []),
  model("meta/llama-5-70b:free", "Meta: Llama 5 70B (free)", 1760000000, 131072, "0", "0", ["text"], ["text"], ["tools"], { hf: "meta/llama-5-70b" }),
  model("router/auto", "Router: Auto", 1790000000, 2000000, "-1", "-1", ["text"], ["text"], []),
  model("old/model-1", "Old: Model 1", 1700000000, 8192, "0.000001", "0.000002", ["text"], ["text"], [], { expires: "2026-10-01" }),
] });
const nodes = {};
const el = () => ({ innerHTML: "", textContent: "", hidden: true, dataset: {}, checked: false, scrollTop: 0, value: "",
  classList: { toggle() {} }, setAttribute() {},
  addEventListener() {}, focus() {}, setSelectionRange() {}, querySelector: () => ({ focus() {} }) });
const saves = [];
const ctx = {
  console, setTimeout, clearTimeout, Date, Math, JSON, Promise, Set, Map, Number, String, Array, Object, Infinity,
  window: { addEventListener() {} },
  document: { querySelector: (s) => (nodes[s] ||= el()), addEventListener() {} },
  Memu: {
    saved: {}, lang: "ru",
    t: (k, vars) => { let v = dict[k]; if (v && typeof v === "object") v = v.many; v = v ?? `??${k}`; for (const [a, b] of Object.entries(vars || {})) v = v.replace(`{${a}}`, b); return v; },
    i18n: { number: (n, o) => n.toFixed(o?.maximumFractionDigits ?? 0) },
    save: async (fields) => { saves.push(Object.keys(fields)); },
    fetch: async (body) => { if (body.url !== "https://openrouter.ai/api/v1/models" || !body.raw) throw new Error("unexpected fetch"); return { text: fixture }; },
    models: async () => [{ id: "genspark-claude-opus-5", name: "Claude Opus 5" }, { id: "kiro-claude-sonnet-4.5", name: "Sonnet" }],
    onLanguage() {}, copy: async () => {}, open: async () => {}, ready() {},
  },
};
vm.createContext(ctx);
vm.runInContext(src, ctx);
ctx.__saves = saves;
ctx.__done = (value) => process.stdout.write(JSON.stringify(value) + "\n");
setTimeout(() => vm.runInContext(`
  (async () => {
    const out = {};
    const ids = () => visible().map((r) => r.id);
    out.count = state.cache.rows.length;
    out.newest = ids();
    out.cards = (document.querySelector("#main").innerHTML.match(/class="mc-model /g) || []).length;
    const opus = byId("anthropic/claude-opus-5");
    out.opus = { org: opus.org, name: opus.name, input: opus.price.input, output: opus.price.output, cacheRead: opus.price.cacheRead,
      tools: opus.tools, reasoning: opus.reasoning, structured: opus.structured, context: opus.context };
    out.router = { input: byId("router/auto").price.input, money: money(byId("router/auto").price.input) };
    state.filters.inputs = ["image"]; out.imageInput = ids();
    state.filters.inputs = []; state.filters.outputs = ["image"]; out.imageOutput = ids();
    state.filters.outputs = []; state.filters.price = "free"; out.free = ids();
    state.filters.price = "1"; out.cheap = ids();
    state.filters.price = "any"; state.filters.context = 1000000; out.bigContext = ids();
    state.filters.context = 0; state.filters.features = ["tools", "reasoning"]; out.toolsReasoning = ids();
    state.filters.features = []; state.filters.hideExpiring = true; out.noExpiring = ids().includes("old/model-1");
    state.filters.hideExpiring = false; state.filters.orgs = ["google"]; out.orgs = ids();
    state.filters.orgs = []; state.sort = "price-asc"; out.priceAsc = ids();
    state.sort = "newest"; state.query = "gemini"; out.search = ids();
    state.query = ""; await loadMyModels(); state.mine = true; out.mine = ids();
    state.mine = false; state.compare = ["anthropic/claude-opus-5", "google/gemini-3.5-flash"]; state.view = "compare"; render();
    out.compare = document.querySelector("#main").innerHTML.includes("mc-table");
    openCard("anthropic/claude-opus-5");
    out.card = document.querySelector("#card").innerHTML.includes("https://openrouter.ai/anthropic/claude-opus-5");
    out.missingI18n = (document.querySelector("#main").innerHTML + document.querySelector("#card").innerHTML + document.querySelector("#filters").innerHTML).match(/\\?\\?[a-z0-9._-]+/g) || [];
    await new Promise((resolve) => setTimeout(resolve, 500));
    out.savedCache = __saves.some((keys) => keys.includes("cache"));
    __done(out);
  })().catch((error) => __done({ error: String(error && error.stack || error) }));
`, ctx), 30);
