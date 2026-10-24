/* Runs the REAL Memu bridge from a generated module document against a tiny
 * DOM stub and reports what the translation layer did.
 * Usage: node modules_i18n_runner.cjs <bridge.js> <report.json>
 */
const fs = require("fs");
const vm = require("vm");

const bridge = fs.readFileSync(process.argv[2], "utf8");
const reportPath = process.argv[3];

function node(attrs) {
  const map = new Map(Object.entries(attrs));
  return {
    textContent: "", innerHTML: "", value: "",
    hasAttribute: (name) => map.has(name),
    getAttribute: (name) => (map.has(name) ? map.get(name) : null),
    setAttribute: (name, value) => map.set(name, String(value)),
    attr: (name) => map.get(name),
  };
}
const nodes = {
  title: node({ "data-i18n": "title" }),
  hint: node({ "data-i18n-placeholder": "hint", "data-i18n-title": "hint" }),
  rich: node({ "data-i18n-html": "rich" }),
  onlyRu: node({ "data-i18n": "only_ru" }),
};
const rootAttrs = new Map();
const listeners = [];
const posted = [];
const document = {
  documentElement: { setAttribute: (name, value) => rootAttrs.set(name, String(value)), style: { setProperty() {} } },
  querySelectorAll: () => Object.values(nodes),
};
const context = {
  document, console, JSON, Object, Intl, String, Number, Error, Promise, setTimeout, clearTimeout,
  parent: { postMessage: (message) => posted.push(message) },
  addEventListener: (type, handler) => { if (type === "message") listeners.push(handler); },
};
context.window = context;
vm.createContext(context);
vm.runInContext(bridge, context, { filename: "bridge.js" });
const Memu = context.Memu;
const report = {};
const snap = () => ({
  title: nodes.title.textContent, placeholder: nodes.hint.attr("placeholder"), tooltip: nodes.hint.attr("title"),
  rich: nodes.rich.innerHTML, onlyRu: nodes.onlyRu.textContent, lang: Memu.lang, htmlLang: rootAttrs.get("lang"),
});
const send = (payload) => listeners.forEach((handler) => handler({ data: payload }));

report.initial = snap();
report.plural = [1, 3, 5, 21].map((count) => Memu.t("files", { count }));
report.interpolated = Memu.t("hello", { name: "Алекс" });
report.missing = Memu.t("nope");
report.missingFallback = Memu.t("nope", { fallback: "Запасной {x}", x: 1 });
report.numberRu = Memu.i18n.number(1234.5);
const events = [];
Memu.onLanguage((data) => events.push(data));
send({ type: "memu-module-host", action: "language", payload: { lang: "en" } });
report.english = snap();
report.pluralEn = [1, 3].map((count) => Memu.t("files", { count }));
report.numberEn = Memu.i18n.number(1234.5);
report.events = events.slice();
send({ type: "memu-module-host", action: "language", payload: { lang: "en" } });
report.eventsAfterSame = events.length;
send({ type: "memu-module-host", action: "theme", payload: { lang: "ru", tokens: {} } });
report.viaTheme = snap();
send({ type: "memu-module-host", action: "language", payload: { lang: "\"><script>x</script>" } });
report.hostile = snap();
Memu.i18n.add("ru", { title: "Новый заголовок" });
report.added = snap();
report.getEn = Memu.i18n.get("title", "en");
report.locales = Memu.i18n.locales();
report.has = [Memu.i18n.has("title"), Memu.i18n.has("nope")];
report.version = Memu.version;
fs.writeFileSync(reportPath, JSON.stringify(report), "utf8");
