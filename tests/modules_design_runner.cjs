const fs = require("fs");
const vm = require("vm");

const designPath = process.argv[2];
const reportPath = process.argv[3];
const mode = process.argv[4] || "ok";

function write(report) { fs.writeFileSync(reportPath, JSON.stringify(report), "utf8"); }

function element() {
  const attrs = new Map();
  return {
    value: "", textContent: "", innerHTML: "", hidden: false, srcdoc: "", files: [],
    options: [{ textContent: "Одностраничный сайт" }], style: {}, dataset: {},
    onclick: null, onchange: null, checked: false, disabled: false,
    selectedOptions: [{ textContent: "Одностраничный сайт" }],
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    setAttribute(name, value) { attrs.set(name, String(value)); },
    getAttribute(name) { return attrs.has(name) ? attrs.get(name) : null; },
    removeAttribute(name) { attrs.delete(name); },
    addEventListener() {}, removeEventListener() {}, appendChild() {}, remove() {},
    replaceChildren() {}, click() {}, focus() {}, blur() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1200, height: 800, left: 0, top: 0,
                                       right: 1200, bottom: 800 }; },
  };
}

const nodes = new Map();
const document = {
  body: { addEventListener() {}, appendChild() {} },
  documentElement: { dataset: {}, style: { setProperty() {} } },
  addEventListener() {},
  querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  },
  querySelectorAll() { return []; },
  createElement() { return element(); },
};
const memu = {

  saved: { code: "<!doctype html><title>Made</title><h1>x</h1>", brief: "Кофейня" },
  manifest: { bridge: { instructions: "RULES" } },
  env: { lang: "ru", theme: "dark" },
  models() { return Promise.resolve([{ id: "demo-model" }]); },
  save() { return Promise.resolve({}); },
  request() { return Promise.resolve({ text: "<!doctype html><title>Made</title>" }); },
  handoff() {
    handoffCalled = true;
    if (mode === "fail") {
      return Promise.reject(new Error("Не удалось сохранить пакет дизайна"));
    }
    if (mode === "empty") {

      return Promise.resolve({ files: [], images: [], prompt: "p", readme: "r", paths: [] });
    }
    return Promise.resolve({
      files: [{ name: "index.html", content: "<h1>x</h1>" }], images: [], prompt: "p",
      readme: "r", saved: [{ path: "/tmp/index.html", name: "index.html" }],
      paths: ["/tmp/index.html"],
    });
  },
  copy() { return Promise.resolve({}); },
  download() { return Promise.resolve({ path: "/tmp/x" }); },
  onTheme() { return () => {}; },
  onLanguage() { return () => {}; },
  log() { return Promise.resolve({}); },
};

const tables = JSON.parse(fs.readFileSync(require("path").join(require("path").dirname(designPath), "i18n.json"), "utf8"));
memu.t = (key, vars) => {
  let value = (tables.ru || {})[key];
  if (value === undefined) return key;
  if (value && typeof value === "object") value = value[new Intl.PluralRules("ru-RU").select((vars && vars.count) || 0)] || value.other || "";
  return String(value).replace(/\{(\w+)\}/g, (match, name) => (vars && vars[name] !== undefined ? String(vars[name]) : match));
};
memu.i18n = {
  t: memu.t,
  locales: () => Object.keys(tables),
  get: (key, lang) => (tables[lang] || {})[key],
  number: (value, options) => new Intl.NumberFormat("ru-RU", options).format(value),
  date: (value, options) => new Intl.DateTimeFormat("ru-RU", options).format(new Date(value)),
};
let handoffCalled = false;
const context = {
  console, document, Memu: memu, Promise, Date, JSON, setTimeout, clearTimeout,
  navigator: { clipboard: {} }, FileReader: function () {},
  addEventListener() {}, removeEventListener() {},
  innerWidth: 1200, innerHeight: 800,
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(designPath, "utf8"), context, { filename: designPath });

(async () => {
  const report = { mode };
  await new Promise((resolve) => setTimeout(resolve, 10));
  const button = document.querySelector("#handoff");
  if (!button || typeof button.onclick !== "function") {
    report.fatal = "no handoff button handler";
    write(report);
    return;
  }
  await button.onclick();
  report.status = String(document.querySelector("#design-status").textContent || "");
  report.result = String(document.querySelector("#handoff-result").innerHTML || "");
  report.handoffCalled = handoffCalled;
  report.ready = document.querySelector("#preview").srcdoc.length > 0;
  write(report);
})();