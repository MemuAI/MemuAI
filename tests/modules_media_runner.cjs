const fs = require("fs");
const vm = require("vm");

const modulesPath = process.argv[2];
const appPath = process.argv[3];
const base = process.argv[4];
const reportPath = process.argv[5];
const providerMessage = process.argv[6] || "provider error";
const nativeFetch = fetch;

function write(report) { fs.writeFileSync(reportPath, JSON.stringify(report), "utf8"); }

function extractFunction(source, token) {
  const start = source.indexOf(token);
  if (start < 0) throw new Error("missing " + token);
  let cursor = start + token.length - 1;
  if (source[cursor] !== "{") throw new Error("token must end with a brace");
  let depth = 1, quote = "", escape = false;
  for (let index = cursor + 1; index < source.length; index += 1) {
    const ch = source[index];
    if (quote) {
      if (escape) { escape = false; continue; }
      if (ch === "\\") { escape = true; continue; }
      if (ch === quote) quote = "";
      continue;
    }
    if (ch === "/" && source[index + 1] === "/") {
      while (index < source.length && source[index] !== "\n") index += 1;
      continue;
    }
    if (ch === "/" && source[index + 1] === "*") {
      index += 2;
      while (index < source.length && !(source[index] === "*" && source[index + 1] === "/")) index += 1;
      index += 1;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") { quote = ch; continue; }
    if (ch === "{") depth += 1;
    else if (ch === "}") { depth -= 1; if (depth === 0) return source.slice(start, index + 1); }
  }
  throw new Error("unclosed " + token);
}

async function main() {
  const appSource = fs.readFileSync(appPath, "utf8");
  const modulesSource = fs.readFileSync(modulesPath, "utf8");
  const messages = [];
  const frame = {
    contentWindow: { postMessage(message) { messages.push(message); } },
    getAttribute() { return "/api/pg/modules/translator/document"; },
    setAttribute() {}, addEventListener() {}, style: {}, dataset: {},
  };
  const nodes = new Map();
  const element = () => ({
    value: "", textContent: "", innerHTML: "", hidden: false, srcdoc: "", files: [],
    style: {}, dataset: {}, onclick: null, onchange: null,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute() {}, addEventListener() {}, click() {}, remove() {}, appendChild() {},
    replaceChildren() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 980, height: 640, left: 40, top: 40,
                                       right: 1020, bottom: 680 }; },
  });
  const document = {
    body: { appendChild() {}, removeChild() {},
            classList: { add() {}, remove() {}, toggle() {} }, addEventListener() {} },
    documentElement: { dataset: {}, style: { setProperty() {} } },
    addEventListener() {}, removeEventListener() {},
    getElementById(id) { return document.querySelector("#" + id); },
    querySelector(selector) {
      if (selector === "#module-frame") return frame;
      if (!selector.startsWith("#")) return null;
      if (!nodes.has(selector)) nodes.set(selector, element());
      return nodes.get(selector);
    },
    querySelectorAll() { return []; },
    createElement() { return element(); },
    readyState: "complete",
  };
  const context = {
    console, document, URL, Blob, TextDecoder, TextEncoder, setTimeout, clearTimeout,
    setInterval() { return 1; }, clearInterval() {},
    navigator: { clipboard: { writeText() { return Promise.resolve(); } } },
    addEventListener() {}, removeEventListener() {},
    innerWidth: 1500, innerHeight: 900,
    fetch(path, options) {
      const target = /^https?:/i.test(String(path)) ? String(path) : base + String(path);
      return nativeFetch(target, options);
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext('var lastSavedDownload = ""; var PG_LANG = "ru"; function toast() {}', context);
  for (const token of [
    "async function api(path, opts = {}) {",
    "async function saveToDownloads(payload) {",
    "function savedToast(saved) {",
    'async function downloadTextFile(name, content, mime = "application/json") {',
  ]) {
    vm.runInContext(extractFunction(appSource, token), context, { filename: appPath });
  }
  if (typeof context.api !== "function" || typeof context.downloadTextFile !== "function") {
    throw new Error("shipped download functions were not installed");
  }
  vm.runInContext(modulesSource, context, { filename: modulesPath });
  const host = context.MemuModules;
  if (!host || typeof host.handleModuleMessage !== "function") {
    throw new Error("the module host did not export handleModuleMessage");
  }

  const report = {};
  const media = host.mediaConsumer;
  report.hasMediaConsumer = typeof media === "function";
  if (typeof media === "function") {
    const ok = media('data: {"status":"queued"}\n\ndata: {"status":"done","urls":["data:image/png;base64,aaa"]}\n\ndata: [DONE]\n');
    report.okUrls = ok.urls.length;
    try { media('data: {"status":"generating"}\n\n'); report.truncatedError = ""; }
    catch (error) { report.truncatedError = error.message; }
    try {
      media('data: {"error": {"message": ' + JSON.stringify(providerMessage) + '}}\n\ndata: [DONE]\n');
      report.providerError = "";
    } catch (error) { report.providerError = error.message; }
    try { media('data: {"status":"error","message":"provider blew up"}\n\n'); report.statusErrorError = ""; }
    catch (error) { report.statusErrorError = error.message; }
    const empty = media('data: {"status":"done","urls":[]}\n\ndata: [DONE]\n');
    report.emptyUrls = empty.urls.length;
  }

  async function call(action, payload) {
    const before = messages.length;
    await host.handleModuleMessage({
      source: frame.contentWindow,
      data: { type: "memu-module", id: action + ":" + before, action, payload },
    });
    if (messages.length === before) throw new Error("no reply for " + action);
    return messages[messages.length - 1];
  }

  await host.refresh();
  await host.open("translator");
  report.opened = host.openId;

  const image = await call("image", { prompt: "" });
  const video = await call("video", { prompt: "cat" });
  report.imageError = image.error || "";
  report.videoError = video.error || "";
  report.imageResult = image.result || null;
  report.videoResult = video.result || null;

  const request = await call("request", { model: "", text: "hi", module_id: "translator" });
  report.requestError = request.error || "";
  report.requestModel = (request.result && request.result.model) || "";

  const handoff = await call("handoff", { prompt: "Cafe terrace", code: "<h1>x</h1>",
                                          images: [] });
  report.handoffError = handoff.error || "";
  report.handoffPaths = (handoff.result && handoff.result.paths) || [];

  write(report);
}

main().catch((error) => { write({ fatal: String((error && error.stack) || error) }); });
