#!/usr/bin/env node

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const zlib = require("zlib");

const BRIDGE_SOURCE = /*@bridge*/null/*@end*/;
const RULES = /*@rules*/null/*@end*/;

function parseArgs(argv) {
  const opts = { lang: "ru", theme: "dark", clickAll: false, script: "", failNetwork: false, mini: false, json: false, target: "", toolbar: "host" };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === "--lang") opts.lang = argv[++i] === "en" ? "en" : "ru";
    else if (a === "--theme") opts.theme = argv[++i] === "light" ? "light" : "dark";
    else if (a === "--click-all") opts.clickAll = true;
    else if (a === "--toolbar") opts.toolbar = argv[++i] === "off" ? "off" : "host";
    else if (a === "--script") opts.script = argv[++i] || "";
    else if (a === "--fail-network") opts.failNetwork = true;
    else if (a === "--mini") opts.mini = true;
    else if (a === "--json") opts.json = true;
    else if (a === "-h" || a === "--help") opts.help = true;
    else if (!opts.target) opts.target = a;
  }
  return opts;
}

function readZip(buffer) {

  const files = {};
  let eocd = -1;
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 66000); i -= 1) {
    if (buffer.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a ZIP archive");
  const count = buffer.readUInt16LE(eocd + 10);
  let offset = buffer.readUInt32LE(eocd + 16);
  for (let n = 0; n < count; n += 1) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) throw new Error("broken ZIP directory");
    const method = buffer.readUInt16LE(offset + 10);
    const size = buffer.readUInt32LE(offset + 20);
    const nameLen = buffer.readUInt16LE(offset + 28), extraLen = buffer.readUInt16LE(offset + 30), commentLen = buffer.readUInt16LE(offset + 32);
    const local = buffer.readUInt32LE(offset + 42);
    const name = buffer.slice(offset + 46, offset + 46 + nameLen).toString("utf8").replace(/\\/g, "/");
    offset += 46 + nameLen + extraLen + commentLen;
    if (name.endsWith("/") || name.includes("__MACOSX")) continue;
    if (name.split("/").includes("..") || name.startsWith("/")) throw new Error("unsafe path in the archive: " + name);
    const lNameLen = buffer.readUInt16LE(local + 26), lExtraLen = buffer.readUInt16LE(local + 28);
    const start = local + 30 + lNameLen + lExtraLen;
    const raw = buffer.slice(start, start + size);
    files[name] = method === 0 ? raw : method === 8 ? zlib.inflateRawSync(raw) : null;
  }
  return files;
}

function fromFiles(files, origin) {
  const byName = {};
  const manifestPaths = Object.keys(files).filter((p) => path.posix.basename(p).toLowerCase() === "manifest.json")
    .sort((a, b) => a.split("/").length - b.split("/").length);
  if (!manifestPaths.length) {
    const pkg = Object.keys(files).find((p) => p.toLowerCase().endsWith(".memu-module.json"));
    if (pkg) return fromPackage(JSON.parse(String(files[pkg])), origin);
    throw new Error("manifest.json is missing");
  }
  const base = path.posix.dirname(manifestPaths[0]);
  const assets = {};
  for (const [p, data] of Object.entries(files)) {
    if (base !== "." && !p.startsWith(base + "/")) continue;
    const rel = base === "." ? p : p.slice(base.length + 1);
    byName[rel.toLowerCase()] = data == null ? "" : String(data).replace(/^\uFEFF/, "");
    if (rel.startsWith("assets/")) assets[rel.slice(7)] = byName[rel.toLowerCase()];
  }
  let manifest;
  try { manifest = JSON.parse(byName["manifest.json"] || "{}"); }
  catch (error) { throw new Error("manifest.json: invalid JSON — " + error.message); }
  let i18n = {};
  if ((byName["i18n.json"] || "").trim()) {
    try { i18n = JSON.parse(byName["i18n.json"]); }
    catch (error) { throw new Error("i18n.json: invalid JSON — " + error.message); }
  }
  return { origin, manifest, html: byName["index.html"] || "", css: byName["style.css"] || "", js: byName["main.js"] || "", i18n, assets };
}

function fromPackage(payload, origin) {
  if (!payload || typeof payload !== "object") throw new Error("the package must be a JSON object");
  const manifest = payload.manifest && typeof payload.manifest === "object" ? payload.manifest : { ...payload };
  const files = payload.files && typeof payload.files === "object" ? payload.files : {};
  let i18n = files["i18n.json"] !== undefined ? files["i18n.json"] : (manifest.i18n || {});
  if (typeof i18n === "string") i18n = i18n.trim() ? JSON.parse(i18n) : {};
  const assets = {};
  for (const [name, data] of Object.entries(files)) if (name.startsWith("assets/")) assets[name.slice(7)] = typeof data === "string" ? data : JSON.stringify(data);
  return { origin, manifest, html: String(files["index.html"] ?? manifest.html ?? ""), css: String(files["style.css"] ?? manifest.css ?? ""),
    js: String(files["main.js"] ?? manifest.js ?? ""), i18n: i18n || {}, assets };
}

function loadPackage(target) {
  const stat = fs.statSync(target);
  if (stat.isDirectory()) {
    const files = {};
    for (const name of fs.readdirSync(target)) {
      const full = path.join(target, name);
      if (fs.statSync(full).isFile()) files[name] = fs.readFileSync(full, "utf8");
    }

    const walk = (dir, rel) => {
      if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) return;
      for (const name of fs.readdirSync(dir)) {
        const full = path.join(dir, name);
        if (fs.statSync(full).isDirectory()) walk(full, rel + name + "/");
        else if (fs.statSync(full).size <= 8 * 1024 * 1024) files[rel + name] = fs.readFileSync(full, "utf8");
      }
    };
    walk(path.join(target, "assets"), "assets/");
    return fromFiles(files, target);
  }
  const buffer = fs.readFileSync(target);
  if (buffer.slice(0, 2).toString() === "PK") return fromFiles(readZip(buffer), path.basename(target));
  if (path.basename(target).toLowerCase() === "manifest.json") return loadPackage(path.dirname(target));
  return fromPackage(JSON.parse(buffer.toString("utf8").replace(/^\uFEFF/, "")), path.basename(target));
}

function stripCode(js) {

  let out = "", i = 0;
  const regexOk = () => { const t = out.replace(/\s+$/, ""); return !t || /[(,=:[!&|?{};+\-*%<>~^]$/.test(t) || /(?:^|[^\w$])(?:return|typeof|case|do|else|in|of|new|delete|void|throw|yield|await)$/.test(t); };
  while (i < js.length) {
    const ch = js[i], nx = js[i + 1];
    if (ch === "/" && nx === "/") { const e = js.indexOf("\n", i); i = e < 0 ? js.length : e; continue; }
    if (ch === "/" && nx === "*") { const e = js.indexOf("*/", i + 2); const stop = e < 0 ? js.length : e + 2; out += "\n".repeat((js.slice(i, stop).match(/\n/g) || []).length); i = stop; continue; }
    if (ch === "/" && regexOk()) {
      let j = i + 1, cls = false;
      while (j < js.length && js[j] !== "\n") { if (js[j] === "\\") { j += 2; continue; } if (js[j] === "[") cls = true; else if (js[j] === "]") cls = false; else if (js[j] === "/" && !cls) break; j += 1; }
      if (js[j] === "/") { out += "/r/"; i = j + 1; while (/[a-z]/i.test(js[i] || "")) i += 1; continue; }
    }
    if (ch === "\"" || ch === "'" || ch === "`") {
      const q = ch; out += q; i += 1;
      while (i < js.length && js[i] !== q) {
        if (js[i] === "\\") { i += 2; continue; }
        if (q === "`" && js.startsWith("${", i)) { let d = 1, s = i + 2; i += 2; while (i < js.length && d) { if (js[i] === "{") d += 1; else if (js[i] === "}") d -= 1; i += 1; } out += "${" + stripCode(js.slice(s, i - 1)) + "}"; continue; }
        if (q !== "`" && js[i] === "\n") break;
        out += js[i] === "\n" ? "\n" : " "; i += 1;
      }
      out += q; i += 1; continue;
    }
    out += ch; i += 1;
  }
  return out;
}
const lineOf = (text, index) => text.slice(0, index).split("\n").length;

function staticChecks(pkg, lang) {
  const out = [];
  const add = (level, field, ru, en, line) => out.push({ level, field, text: lang === "en" ? en : ru, line: line || 0 });
  const m = pkg.manifest || {};
  if (!m.id) add("error", "manifest", "В manifest.json нет id.", "manifest.json has no id.");
  else if (!new RegExp(RULES.id_pattern).test(m.id) || m.id !== String(m.id).toLowerCase()) add("error", "manifest", "id: латиница в нижнем регистре, цифры и дефис, до 41 символа, с буквы.", "id: lowercase latin, digits and hyphens, up to 41 characters, starting with a letter.");
  if (RULES.builtin_ids.includes(m.id)) add("error", "manifest", "id занят встроенным модулем.", "The id belongs to a built-in module.");
  if (m.permissions !== undefined && !Array.isArray(m.permissions)) add("error", "manifest", "permissions должен быть списком.", "permissions must be a list.");
  if (m.permissions === undefined) add("warn", "manifest", "Нет permissions — модуль получит все права. Перечислите только нужные.", "No permissions — the module gets every permission. List only the needed ones.");
  const granted = Array.isArray(m.permissions) ? m.permissions.filter((p) => RULES.permissions.includes(p)) : RULES.permissions.slice();
  if (Array.isArray(m.permissions)) m.permissions.filter((p) => !RULES.permissions.includes(p)).forEach((p) => add("warn", "manifest", `Неизвестное право «${p}».`, `Unknown permission “${p}”.`));
  const code = stripCode(pkg.js);
  const used = new Set();
  for (const match of code.matchAll(/\bMemu(?:AI)?\.(record\.\w+|toolbar\.\w+|\w+)\s*\(/g)) used.add(match[1]);
  const missing = {};
  for (const method of used) {
    if (/^(on|onTheme|onLanguage|t|ready|call)$/.test(method)) continue;
    if (!(method in RULES.method_permissions)) { add("error", "js", `У моста нет метода Memu.${method}().`, `The bridge has no Memu.${method}() method.`); continue; }
    const need = RULES.method_permissions[method];
    if (need && !granted.includes(need)) (missing[need] = missing[need] || []).push("Memu." + method);
  }
  for (const [perm, list] of Object.entries(missing)) add("error", "manifest", `Нужно право «${perm}» для ${list.join(", ")}.`, `The “${perm}” permission is required by ${list.join(", ")}.`);
  const persist = Array.isArray(m.persist) ? m.persist.map(String) : [];
  for (const chunk of pkg.js.matchAll(/Memu\.save\(\s*\{([^{}]*)\}/g)) {
    for (const entry of chunk[1].split(",")) {
      const key = (entry.trim().match(/^["']?([A-Za-z_$][\w$-]*)["']?\s*(?::|$)/) || [])[1];
      if (key && !persist.includes(key)) add("error", "persist", `Поле «${key}» не сохранится: добавьте его в persist.`, `The “${key}” field will not persist: add it to persist.`);
    }
  }
  const keys = new Set();
  for (const match of pkg.html.matchAll(/data-i18n(?:-[a-z-]+)?\s*=\s*["']([^"']+)["']/g)) keys.add(match[1]);
  for (const match of pkg.js.matchAll(/\bMemu\.(?:t|i18n\.t)\(\s*(["'`])([^"'`$]+)\1\s*[,)]/g)) keys.add(match[2]);
  const locales = Object.keys(pkg.i18n || {});
  if (keys.size && !locales.length) add("error", "i18n", "Код использует ключи перевода, а i18n.json нет: на экране будут видны сами ключи.", "The code uses translation keys but there is no i18n.json: the keys themselves will show.");
  for (const loc of locales) {
    const lost = [...keys].filter((k) => !(k in (pkg.i18n[loc] || {})));
    if (lost.length) add("warn", "i18n", `В «${loc}» нет ключей: ${lost.slice(0, 12).join(", ")}`, `“${loc}” lacks keys: ${lost.slice(0, 12).join(", ")}`);
  }
  if (locales.length && !(locales.includes("ru") && locales.includes("en"))) add("warn", "i18n", "Нужны оба словаря: ru и en.", "Both dictionaries are needed: ru and en.");
  for (const [field, text, rules] of [["js", code, RULES.js_rules], ["js", pkg.js, RULES.js_raw_rules || []], ["html", pkg.html, RULES.html_rules], ["css", pkg.css, RULES.css_rules]]) {
    for (const rule of rules) {
      let re;
      try { re = new RegExp(rule.pattern, "im"); } catch (_) { continue; }
      const hit = re.exec(text);
      if (hit) out.push({ level: rule.level, field, text: lang === "en" ? rule.en : rule.ru, line: lineOf(text, hit.index) });
    }
  }
  if (/<\/\s*script\s*>/i.test(pkg.js)) add("error", "js", "</script> внутри main.js обрывает модуль: пишите <\\/script>.", "</script> inside main.js truncates the module: write <\\/script>.");
  if (/<\/\s*style\s*>/i.test(pkg.css)) add("error", "css", "</style> внутри style.css обрывает модуль.", "</style> inside style.css truncates the module.");
  for (const [name, text] of [["index.html", pkg.html], ["style.css", pkg.css], ["main.js", pkg.js]]) {
    if (text.length > RULES.code_limit) add("error", name, `${name} длиннее ${RULES.code_limit} символов.`, `${name} is longer than ${RULES.code_limit} characters.`);
  }
  try { new vm.Script("window.__memuModuleMain=function(){\n" + pkg.js + "\n};", { filename: "main.js" }); }
  catch (error) { add("error", "js", "Синтаксическая ошибка: " + error.message, "Syntax error: " + error.message); }
  return out;
}

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
class Node_ {
  constructor(doc) { this.ownerDocument = doc; this.parentNode = null; this.childNodes = []; this._listeners = {}; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  addEventListener(type, fn, opts) { if (typeof fn !== "function" && !(fn && fn.handleEvent)) return; (this._listeners[type] = this._listeners[type] || []).push({ fn, once: !!(opts && opts.once), capture: opts === true || !!(opts && opts.capture) }); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((l) => l.fn !== fn); }
  _fire(event, capture) {
    const list = (this._listeners[event.type] || []).filter((l) => l.capture === capture);
    for (const l of list) {
      if (event._stopNow) break;
      if (l.once) this.removeEventListener(event.type, l.fn);
      event.currentTarget = this;
      try { typeof l.fn === "function" ? l.fn.call(this, event) : l.fn.handleEvent(event); } catch (error) { this.ownerDocument._win.__reportError(error); }
    }
    const prop = this["on" + event.type];
    if (!capture && typeof prop === "function") { try { prop.call(this, event); } catch (error) { this.ownerDocument._win.__reportError(error); } }
  }
  dispatchEvent(event) {
    event.target = event.target || this;
    const pathUp = [];
    for (let n = this; n; n = n.parentNode) pathUp.push(n);
    if (this.ownerDocument && this !== this.ownerDocument._win) pathUp.push(this.ownerDocument._win);
    for (let i = pathUp.length - 1; i >= 0 && !event._stop; i -= 1) pathUp[i]._fire(event, true);
    for (let i = 0; i < pathUp.length && !event._stop; i += 1) { pathUp[i]._fire(event, false); if (!event.bubbles) break; }
    return !event.defaultPrevented;
  }
  appendChild(child) { if (child.nodeType === 11) { [...child.childNodes].forEach((c) => this.appendChild(c)); return child; } if (child.parentNode) child.parentNode.removeChild(child); child.parentNode = this; this.childNodes.push(child); return child; }
  append(...items) { items.forEach((i) => this.appendChild(typeof i === "string" ? this.ownerDocument.createTextNode(i) : i)); }
  prepend(...items) { items.reverse().forEach((i) => this.insertBefore(typeof i === "string" ? this.ownerDocument.createTextNode(i) : i, this.childNodes[0] || null)); }
  insertBefore(child, ref) { if (child.parentNode) child.parentNode.removeChild(child); const at = ref ? this.childNodes.indexOf(ref) : -1; child.parentNode = this; if (at < 0) this.childNodes.push(child); else this.childNodes.splice(at, 0, child); return child; }
  removeChild(child) { const at = this.childNodes.indexOf(child); if (at >= 0) this.childNodes.splice(at, 1); child.parentNode = null; return child; }
  replaceChildren(...items) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; this.append(...items); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceWith(node) { if (!this.parentNode) return; this.parentNode.insertBefore(node, this); this.remove(); }
  contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(value) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; if (value !== "" && value != null) this.appendChild(this.ownerDocument.createTextNode(String(value))); }
}
class Text_ extends Node_ {
  constructor(doc, text) { super(doc); this.nodeType = 3; this.data = String(text); }
  get textContent() { return this.data; } set textContent(v) { this.data = String(v); }
  get nodeValue() { return this.data; }
}
class Element_ extends Node_ {
  constructor(doc, tag) { super(doc); this.nodeType = 1; this.tagName = String(tag).toUpperCase(); this.localName = String(tag).toLowerCase(); this._attrs = new Map(); this.style = makeStyle(); this._value = null; this.scrollTop = 0; this.scrollLeft = 0; }
  get nodeName() { return this.tagName; }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  get childElementCount() { return this.children.length; }
  get nextElementSibling() { const s = this.parentNode ? this.parentNode.children : []; return s[s.indexOf(this) + 1] || null; }
  get previousElementSibling() { const s = this.parentNode ? this.parentNode.children : []; return s[s.indexOf(this) - 1] || null; }
  getAttribute(n) { n = String(n).toLowerCase(); return this._attrs.has(n) ? this._attrs.get(n) : null; }
  setAttribute(n, v) { this._attrs.set(String(n).toLowerCase(), String(v)); }
  hasAttribute(n) { return this._attrs.has(String(n).toLowerCase()); }
  removeAttribute(n) { this._attrs.delete(String(n).toLowerCase()); }
  toggleAttribute(n, force) { const on = force === undefined ? !this.hasAttribute(n) : !!force; if (on) this.setAttribute(n, ""); else this.removeAttribute(n); return on; }
  get attributes() { return [...this._attrs].map(([name, value]) => ({ name, value })); }
  get id() { return this.getAttribute("id") || ""; } set id(v) { this.setAttribute("id", v); }
  get className() { return this.getAttribute("class") || ""; } set className(v) { this.setAttribute("class", v); }
  get classList() {
    const el = this; const list = () => el.className.split(/\s+/).filter(Boolean);
    return { add: (...c) => { el.className = [...new Set([...list(), ...c])].join(" "); }, remove: (...c) => { el.className = list().filter((x) => !c.includes(x)).join(" "); },
      toggle: (c, f) => { const has = list().includes(c); const on = f === undefined ? !has : !!f; if (on && !has) el.className = [...list(), c].join(" "); if (!on && has) el.className = list().filter((x) => x !== c).join(" "); return on; },
      contains: (c) => list().includes(c), replace: (a, b) => { el.className = list().map((x) => (x === a ? b : x)).join(" "); }, get length() { return list().length; }, item: (i) => list()[i] || null,
      forEach: (fn) => list().forEach(fn), [Symbol.iterator]: () => list()[Symbol.iterator]() };
  }
  get dataset() {
    const el = this;
    const toAttr = (k) => "data-" + String(k).replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
    return new Proxy({}, { get: (_, k) => (typeof k === "string" ? (el.getAttribute(toAttr(k)) ?? undefined) : undefined),
      set: (_, k, v) => { el.setAttribute(toAttr(k), v); return true; }, deleteProperty: (_, k) => { el.removeAttribute(toAttr(k)); return true; },
      has: (_, k) => el.hasAttribute(toAttr(k)), ownKeys: () => [...el._attrs.keys()].filter((n) => n.startsWith("data-")).map((n) => n.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())),
      getOwnPropertyDescriptor: (_, k) => (el.hasAttribute(toAttr(k)) ? { enumerable: true, configurable: true, value: el.getAttribute(toAttr(k)) } : undefined) });
  }
  get hidden() { return this.hasAttribute("hidden"); } set hidden(v) { this.toggleAttribute("hidden", !!v); }
  get disabled() { return this.hasAttribute("disabled"); } set disabled(v) { this.toggleAttribute("disabled", !!v); }
  get checked() { return this._checked !== undefined ? this._checked : this.hasAttribute("checked"); } set checked(v) { this._checked = !!v; }
  get selected() { return this._selected !== undefined ? this._selected : this.hasAttribute("selected"); } set selected(v) { this._selected = !!v; }
  get type() { return this.getAttribute("type") || (this.localName === "button" ? "submit" : "text"); } set type(v) { this.setAttribute("type", v); }
  get name() { return this.getAttribute("name") || ""; } set name(v) { this.setAttribute("name", v); }
  get placeholder() { return this.getAttribute("placeholder") || ""; } set placeholder(v) { this.setAttribute("placeholder", v); }
  get title() { return this.getAttribute("title") || ""; } set title(v) { this.setAttribute("title", v); }
  get src() { return this.getAttribute("src") || ""; } set src(v) { this.setAttribute("src", v); if (this.localName === "img") setTimeout(() => this.dispatchEvent(new (this.ownerDocument._win.Event)("load")), 0); }
  get href() { return this.getAttribute("href") || ""; } set href(v) { this.setAttribute("href", v); }
  get options() { return this.querySelectorAll("option"); }
  get selectedIndex() { const o = this.options; const i = o.findIndex((x) => x.selected); return i < 0 ? (o.length ? 0 : -1) : i; }
  set selectedIndex(i) { this.options.forEach((o, k) => { o.selected = k === i; }); }
  get value() {
    if (this.localName === "select") { const o = this.options[this.selectedIndex]; return o ? o.value : ""; }
    if (this.localName === "option") return this.hasAttribute("value") ? this.getAttribute("value") : this.textContent;
    if (this.localName === "textarea") return this._value !== null ? this._value : this.textContent;
    if (this._value !== null) return this._value;
    return this.getAttribute("value") || "";
  }
  set value(v) {
    if (this.localName === "select") { const o = this.options; const i = o.findIndex((x) => x.value === String(v)); o.forEach((x, k) => { x.selected = k === i; }); return; }
    this._value = String(v ?? "");
  }
  get valueAsNumber() { return Number(this.value); }
  get files() { return this._files || []; }
  get innerHTML() { return this.childNodes.map(serialize).join(""); }
  set innerHTML(html) { this.childNodes.forEach((c) => { c.parentNode = null; }); this.childNodes = []; parseInto(this, String(html ?? ""), this.ownerDocument); }
  get outerHTML() { return serialize(this); }
  get innerText() { return this.textContent; } set innerText(v) { this.textContent = v; }
  insertAdjacentHTML(where, html) { const box = this.ownerDocument.createElement("div"); box.innerHTML = html; const nodes = [...box.childNodes];
    if (where === "beforeend") nodes.forEach((n) => this.appendChild(n)); else if (where === "afterbegin") nodes.reverse().forEach((n) => this.insertBefore(n, this.childNodes[0] || null));
    else if (where === "beforebegin" && this.parentNode) nodes.forEach((n) => this.parentNode.insertBefore(n, this)); else if (where === "afterend" && this.parentNode) nodes.reverse().forEach((n) => this.parentNode.insertBefore(n, this.nextSibling || null)); }
  get nextSibling() { const s = this.parentNode ? this.parentNode.childNodes : []; return s[s.indexOf(this) + 1] || null; }
  querySelectorAll(sel) { const found = []; const walk = (n) => { for (const c of n.children) { if (matches(c, sel)) found.push(c); walk(c); } }; walk(this); return found; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(tag) { return this.querySelectorAll(tag); }
  getElementsByClassName(c) { return this.querySelectorAll("." + c.split(/\s+/).join(".")); }
  matches(sel) { return matches(this, sel); }
  closest(sel) { for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (matches(n, sel)) return n; return null; }
  click() { if (this.disabled) return; const ev = new (this.ownerDocument._win.MouseEvent)("click", { bubbles: true, cancelable: true }); this.dispatchEvent(ev);
    if (!ev.defaultPrevented && this.localName === "input" && /checkbox|radio/.test(this.type)) { this.checked = !this.checked; this.dispatchEvent(new (this.ownerDocument._win.Event)("change", { bubbles: true })); }
    if (!ev.defaultPrevented && this.localName === "button" && this.type === "submit") { const f = this.closest("form"); if (f) f.dispatchEvent(new (this.ownerDocument._win.Event)("submit", { bubbles: true, cancelable: true })); } }
  focus() { this.ownerDocument.activeElement = this; this.dispatchEvent(new (this.ownerDocument._win.FocusEvent)("focus")); }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = this.ownerDocument.body; }
  select() {} setSelectionRange() {} scrollIntoView() {} scrollTo() {} scrollBy() {} showModal() { this.setAttribute("open", ""); } show() { this.setAttribute("open", ""); } close() { this.removeAttribute("open"); }
  get open() { return this.hasAttribute("open"); } set open(v) { this.toggleAttribute("open", !!v); }
  getBoundingClientRect() { return { x: 0, y: 0, top: 0, left: 0, right: 600, bottom: 400, width: 600, height: 400 }; }
  getClientRects() { return [this.getBoundingClientRect()]; }
  get clientWidth() { return 600; } get clientHeight() { return 400; } get offsetWidth() { return 600; } get offsetHeight() { return 400; }
  get scrollHeight() { return 400; } get scrollWidth() { return 600; } get offsetParent() { return this.parentNode; }
  get isConnected() { return this.ownerDocument.documentElement.contains(this); }
  get isContentEditable() { return this.getAttribute("contenteditable") === "true" || this.getAttribute("contenteditable") === ""; }
  getContext() { return canvasContext(); }
  toDataURL() { return "data:image/png;base64,iVBORw0KGgo="; }
  toBlob(cb) { setTimeout(() => cb(new Blob([""], { type: "image/png" })), 0); }
  play() { return Promise.resolve(); } pause() {} load() {} cloneNode(deep) { const c = this.ownerDocument.createElement(this.localName); this._attrs.forEach((v, k) => c.setAttribute(k, v)); if (deep) c.innerHTML = this.innerHTML; return c; }
  animate() { return { finished: Promise.resolve(), cancel() {}, onfinish: null }; }
  requestFullscreen() { return Promise.resolve(); } attachShadow() { return this; }
}
function makeStyle() {
  const store = {};
  return new Proxy(store, { get: (t, k) => (k === "setProperty" ? (n, v) => { t[n] = v; } : k === "getPropertyValue" ? (n) => t[n] || "" : k === "removeProperty" ? (n) => { delete t[n]; } : (k === "cssText" ? Object.entries(t).map(([a, b]) => `${a}:${b}`).join(";") : (t[k] ?? ""))),
    set: (t, k, v) => { t[k] = v; return true; } });
}
function canvasContext() {
  const fn = () => {};
  return new Proxy({ measureText: (s) => ({ width: String(s).length * 7 }), getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(Math.max(0, w * h * 4)), width: w, height: h }),
    createLinearGradient: () => ({ addColorStop: fn }), createRadialGradient: () => ({ addColorStop: fn }), createPattern: () => ({}), getTransform: () => ({}), isPointInPath: () => false },
  { get: (t, k) => (k in t ? t[k] : fn), set: (t, k, v) => { t[k] = v; return true; } });
}
function serialize(n) {
  if (n.nodeType === 3) return n.data.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  const attrs = [...n._attrs].map(([k, v]) => ` ${k}="${String(v).replace(/"/g, "&quot;")}"`).join("");
  if (VOID.has(n.localName)) return `<${n.localName}${attrs}>`;
  return `<${n.localName}${attrs}>${n.childNodes.map(serialize).join("")}</${n.localName}>`;
}
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: "\u00a0", hellip: "…", mdash: "—", ndash: "–", times: "×", larr: "←", rarr: "→", copy: "©" };
const decode = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => (e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : +e.slice(1)) : (ENTITIES[e.toLowerCase()] ?? m)));
function parseInto(parent, html, doc) {
  const stack = [parent];
  const re = /<!--[\s\S]*?-->|<!doctype[^>]*>|<\/\s*([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>|([^<]+|<)/g;
  let m;
  while ((m = re.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[1]) { const tag = m[1].toLowerCase(); for (let i = stack.length - 1; i > 0; i -= 1) { if (stack[i].localName === tag) { stack.length = i; break; } } continue; }
    if (m[2]) {
      const el = doc.createElement(m[2]);
      for (const a of (m[3] || "").matchAll(/([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) el.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ""));
      top.appendChild(el);
      if (/^(script|style|textarea|title)$/i.test(m[2]) && !m[4]) {
        const end = html.toLowerCase().indexOf("</" + m[2].toLowerCase(), re.lastIndex);
        const body = html.slice(re.lastIndex, end < 0 ? html.length : end);
        if (body) el.appendChild(doc.createTextNode(m[2].toLowerCase() === "textarea" ? decode(body) : body));
        re.lastIndex = end < 0 ? html.length : html.indexOf(">", end) + 1;
        continue;
      }
      if (!VOID.has(el.localName) && !m[4]) stack.push(el);
      continue;
    }
    if (m[5] && !/^<!/.test(m[0])) top.appendChild(doc.createTextNode(decode(m[5])));
  }
}
function matches(el, selector) {
  return String(selector).split(",").some((part) => matchComplex(el, part.trim()));
}
function matchComplex(el, sel) {
  if (!sel) return false;
  const tokens = sel.replace(/\s*>\s*/g, " > ").replace(/\s*~\s*/g, " ~ ").replace(/\s*\+\s*/g, " + ").split(/\s+(?![^[]*\])/).filter(Boolean);
  let i = tokens.length - 1;
  if (!matchCompound(el, tokens[i])) return false;
  let node = el;
  i -= 1;
  while (i >= 0) {
    const comb = tokens[i];
    if (comb === ">") { node = node.parentElement; if (!node || !matchCompound(node, tokens[i - 1])) return false; i -= 2; continue; }
    if (comb === "~" || comb === "+") { let sib = node.previousElementSibling; let ok = false; while (sib) { if (matchCompound(sib, tokens[i - 1])) { ok = true; node = sib; break; } if (comb === "+") break; sib = sib.previousElementSibling; } if (!ok) return false; i -= 2; continue; }
    let up = node.parentElement, ok = false;
    while (up) { if (matchCompound(up, comb)) { ok = true; node = up; break; } up = up.parentElement; }
    if (!ok) return false;
    i -= 1;
  }
  return true;
}
function matchCompound(el, compound) {
  if (!el || el.nodeType !== 1 || !compound) return false;
  const re = /^([a-zA-Z*][\w-]*)|#([\w-]+)|\.([\w-]+)|\[\s*([\w-]+)\s*(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*)))?\s*\]|:(not)\(([^)]*)\)|:([\w-]+)(?:\(([^)]*)\))?/g;
  let m, consumed = 0;
  while ((m = re.exec(compound))) {
    consumed += m[0].length;
    if (m[1] && m[1] !== "*" && el.localName !== m[1].toLowerCase()) return false;
    if (m[2] && el.id !== m[2]) return false;
    if (m[3] && !el.classList.contains(m[3])) return false;
    if (m[4]) {
      const v = el.getAttribute(m[4]); const want = m[6] ?? m[7] ?? m[8];
      if (v === null) return false;
      if (m[5] === "=" && v !== want) return false;
      if (m[5] === "^=" && !v.startsWith(want)) return false;
      if (m[5] === "$=" && !v.endsWith(want)) return false;
      if (m[5] === "*=" && !v.includes(want)) return false;
      if (m[5] === "~=" && !v.split(/\s+/).includes(want)) return false;
    }
    if (m[9] && matches(el, m[10])) return false;
    if (m[11]) {
      const p = m[11];
      if (p === "checked" && !el.checked) return false;
      if (p === "disabled" && !el.disabled) return false;
      if (p === "enabled" && el.disabled) return false;
      if (p === "first-child" && el.previousElementSibling) return false;
      if (p === "last-child" && el.nextElementSibling) return false;
      if (p === "empty" && el.childNodes.length) return false;
    }
  }
  return consumed > 0;
}
function makeMiniWindow(lang) {
  const listeners = {};
  const win = {};
  const doc = new Node_(null);
  doc.ownerDocument = doc; doc.nodeType = 9; doc._win = win;
  doc.createElement = (tag) => new Element_(doc, tag);
  doc.createElementNS = (_, tag) => new Element_(doc, tag);
  doc.createTextNode = (text) => new Text_(doc, text);
  doc.createDocumentFragment = () => { const f = new Node_(doc); f.nodeType = 11; return f; };
  doc.createComment = () => new Text_(doc, "");
  const html = new Element_(doc, "html"); doc.appendChild(html);
  const head = new Element_(doc, "head"); const body = new Element_(doc, "body");
  html.appendChild(head); html.appendChild(body);
  Object.assign(doc, { documentElement: html, head, body, activeElement: body, readyState: "loading", title: "", visibilityState: "visible", hidden: false,
    querySelector: (s) => html.querySelector(s), querySelectorAll: (s) => html.querySelectorAll(s), getElementById: (id) => html.querySelector("#" + String(id).replace(/([^\w-])/g, "\\$1")),
    getElementsByTagName: (t) => html.querySelectorAll(t), getElementsByClassName: (c) => html.getElementsByClassName(c), hasFocus: () => true,
    execCommand: () => false, getSelection: () => ({ toString: () => "", removeAllRanges() {}, addRange() {}, rangeCount: 0 }), createRange: () => ({ selectNodeContents() {}, setStart() {}, setEnd() {}, collapse() {} }) });
  Object.defineProperty(doc, "cookie", { get() { throw new win.DOMException("The document is sandboxed and lacks the 'allow-same-origin' flag.", "SecurityError"); }, set() { throw new win.DOMException("The document is sandboxed and lacks the 'allow-same-origin' flag.", "SecurityError"); } });
  win.document = doc;
  win.addEventListener = (t, fn, o) => Node_.prototype.addEventListener.call(win, t, fn, o);
  win.removeEventListener = (t, fn) => Node_.prototype.removeEventListener.call(win, t, fn);
  win._listeners = listeners;
  win._fire = (e, c) => Node_.prototype._fire.call(win, e, c);
  win.dispatchEvent = (e) => { e.target = e.target || win; win._fire(e, true); win._fire(e, false); return !e.defaultPrevented; };
  win.ownerDocument = doc;
  return win;
}

function makeHost(pkg, opts, report) {
  const m = pkg.manifest || {};
  const granted = Array.isArray(m.permissions) ? m.permissions.filter((p) => RULES.permissions.includes(p)) : RULES.permissions.slice();
  const persist = Array.isArray(m.persist) ? m.persist.map((k) => String(k).trim().slice(0, 40)).filter(Boolean).slice(0, 32) : [];
  const L = (ru, en) => (state.lang === "en" ? en : ru);
  const state = { lang: opts.lang, saved: {}, clipboard: "mock clipboard text", calls: [], jobs: new Map(), store: new Map(), netTrusted: new Set() };
  RULES.store_value_limit = RULES.store_value_limit || 40 * 1024 * 1024;
  const PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
  const WAV = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=";
  const MODELS = [
    { id: "mock-chat-fast", name: "Mock chat (fast)", provider: "mock", image: false, video: false, audio: false, vision: true, thinking: false, web_search: true, context: 128000 },
    { id: "mock-chat-smart", name: "Mock chat (reasoning)", provider: "mock", image: false, video: false, audio: false, vision: true, thinking: true, web_search: true, context: 200000 },
    { id: "mock-image", name: "Mock image", provider: "mock", image: true, video: false, audio: false, vision: false, thinking: false, web_search: false, context: 0 },
    { id: "mock-video", name: "Mock video", provider: "mock", image: false, video: true, audio: false, vision: false, thinking: false, web_search: false, context: 0 },
  ];

  const models = [MODELS[2], MODELS[3], MODELS[0], MODELS[1]];
  const fail = (text) => { throw new Error(text); };
  const needsModel = (body) => { if (!String(body.model || "").trim()) fail(L("Выберите модель", "Choose a model")); };

  const netImportTarget = (input) => {
    const SRC = { chub: ["Chub (chub.ai)", "character"], "chub-lorebook": ["Chub lorebook (chub.ai)", "lorebook"], janitor: ["JanitorAI (jannyai.com)", "character"],
      pygmalion: ["Pygmalion.chat", "character"], aicc: ["AICharacterCards.com", "character"], risu: ["RisuRealm (realm.risuai.net)", "character"],
      perchance: ["Perchance (perchance.org)", "character"], generic: ["Direct link", "character"] };
    const make = (source, id) => ({ source, id, label: SRC[source][0], kind: SRC[source][1] });
    if (/^[A-Za-z0-9][\w.~-]*\/[A-Za-z0-9][\w.~-]*$/.test(input)) return make("chub", input);
    let url;
    try { url = new URL(input); } catch (error) { return null; }
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    const parts = url.pathname.split("/").filter(Boolean);
    if (["chub.ai", "characterhub.org", "venus.chub.ai"].includes(host) && parts.length >= 3) {
      if (parts[0] === "lorebooks") return make("chub-lorebook", "lorebooks/" + parts.slice(1).join("/"));
      return make("chub", parts.slice(1).join("/"));
    }
    if (["janitorai.com", "jannyai.com"].includes(host) && parts.length >= 2) return make("janitor", parts[1]);
    if (host === "pygmalion.chat" && parts.length >= 2) return make("pygmalion", parts[parts.length - 1]);
    if (host === "aicharactercards.com" && parts.length) return make("aicc", parts.join("/"));
    if (host === "realm.risuai.net" && parts[0] === "character" && parts[1]) return make("risu", parts[1]);
    if (host === "perchance.org" && parts.length) return make("perchance", parts.join("/") + (url.search || ""));
    const hosts = (m.net_import && Array.isArray(m.net_import.hosts) ? m.net_import.hosts : []).concat(["files.catbox.moe", "cdn.discordapp.com", "raw.githubusercontent.com"]);
    if (hosts.some((h) => h === host || (h.startsWith("*.") && host.endsWith(h.slice(1))))) return make("generic", url.href);
    return null;
  };
  const answerOf = (body) => `mock answer to: ${String(body.text || "").slice(0, 80)}`;
  async function handle(action, payload, id, post) {
    const need = RULES.action_permission[action];
    if (need && !granted.includes(need)) fail(L(`Модулю не выдано право «${need}»`, `The module lacks the “${need}” permission`));
    const netFail = opts.failNetwork && ["request", "stream", "image", "video", "speak", "transcribe", "music", "search", "fetch", "extract"].includes(action);
    if (netFail) fail(L("Провайдер недоступен (тест --fail-network)", "The provider is unavailable (--fail-network test)"));
    switch (action) {
      case "save": {
        const fields = payload && typeof payload === "object" ? payload : {};
        const keys = Object.keys(fields);
        if (keys.length > 200) fail("too many fields");
        for (const key of keys) {
          const name = key.slice(0, 40);
          if (!persist.includes(name)) { report.dropped.add(name); continue; }
          const size = JSON.stringify(fields[key] ?? null).length;
          if (size > 2000000) { report.warnings.push(`Memu.save: “${name}” is larger than 2 MB and was not written`); continue; }
          state.saved[name] = JSON.parse(JSON.stringify(fields[key] ?? null));
        }
        return { ...state.saved };
      }
      case "models": return models;
      case "routes": return { routes: RULES.routes };
      case "env": return env();
      case "log": if ((payload || {}).level === "error") report.logErrors.push(String(payload.text || "")); else report.logs.push(String(payload.text || "")); return { ok: true };
      case "chat_history": return { id: "module-" + m.id, title: m.title || m.id, messages: [] };
      case "clipboard": state.clipboard = String(payload.text || ""); return { ok: true };
      case "clipboard_read": return { ok: true, text: state.clipboard };
      case "close": report.closed = true; return { ok: true };
      case "toolbar": {

        if (opts.toolbar !== "host") fail(L("Окно не рисует действия модуля", "The window does not draw module actions"));
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (items.length > 16) report.warnings.push(L(`Memu.toolbar: ${items.length} действий, окно покажет первые 16`, `Memu.toolbar: ${items.length} actions, the window shows the first 16`));
        items.forEach((item) => {
          if (!item || item.kind === "separator" || item.sep) return;
          const id = String(item.id || "").replace(/[^\w.:-]/g, "");
          if (!id) report.warnings.push(L("Memu.toolbar: у действия нет id — окно его не покажет", "Memu.toolbar: an action without an id is not shown"));
          else if (!String(item.label || item.title || "").trim()) report.warnings.push(L(`Memu.toolbar: у «${id}» нет label — подсказки и диктора не будет`, `Memu.toolbar: “${id}” has no label — no tooltip, nothing for screen readers`));
          if (Array.isArray(item.menu) && item.menu.length > 40) report.warnings.push(L(`Memu.toolbar: в меню «${id}» больше 40 пунктов`, `Memu.toolbar: the “${id}” menu has more than 40 entries`));
        });
        state.toolbar = { title: String(payload.title || ""), status: String(payload.status || ""), items: items.slice(0, 16) };
        return { ok: true, state: "floating" };
      }
      case "resize": return { ok: true, width: Number(payload.width) || 0, height: Number(payload.height) || 0 };
      case "host":
        if (payload.command === "chat") return { ok: true, model: (payload.models || [payload.model])[0] || "mock-chat-fast" };
        if (payload.command === "module") return { ok: false, reason: "missing" };
        fail(L("Неизвестная команда host", "Unknown host command"));
        break;
      case "request": needsModel(payload); return { text: answerOf(payload), model: payload.model, images: [], documents: [], keep_in_chat: !!payload.keep_in_chat, ...(payload.keep_in_chat ? { chat: "module-" + m.id } : {}) };
      case "stream": {
        needsModel(payload);
        const text = answerOf(payload);
        const job = { cancelled: false };
        state.jobs.set(id, job);
        post({ type: "memu-module-event", id, event: "status", status: "queued" });
        await sleep(5); post({ type: "memu-module-event", id, event: "status", status: "streaming" });
        for (const word of text.split(/(?<= )/)) { if (job.cancelled) fail(L("Остановлено", "Stopped")); await sleep(2); post({ type: "memu-module-event", id, event: "delta", delta: word }); }
        const result = { text, model: payload.model };
        post({ type: "memu-module-event", id, event: "done", result });
        return result;
      }
      case "complete": {

        needsModel(payload);
        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        if (!messages.length) fail(L("Нет сообщений", "No messages"));
        for (const msg of messages) if (!msg || !["system", "user", "assistant"].includes(msg.role)) fail(L("Роль сообщения: system, user или assistant", "Message role must be system, user or assistant"));
        const last = [...messages].reverse().find((msg) => msg.role === "user") || messages[messages.length - 1];
        const lastText = typeof last.content === "string" ? last.content : (Array.isArray(last.content) ? last.content.filter((part) => part && part.type === "text").map((part) => part.text).join(" ") : "");
        const text = `mock answer to: ${String(lastText || "").slice(0, 80)}`;
        const job = { cancelled: false };
        state.jobs.set(id, job);
        const started = Date.now();
        post({ type: "memu-module-event", id, event: "status", status: "streaming" });
        if (payload.params && payload.params.thinking) post({ type: "memu-module-event", id, event: "reasoning", delta: "mock reasoning. " });
        for (const word of text.split(/(?<= )/)) { if (job.cancelled) fail(L("Остановлено", "Stopped")); await sleep(2); post({ type: "memu-module-event", id, event: "delta", delta: word }); }
        const usage = { prompt_tokens: JSON.stringify(messages).length >> 2, completion_tokens: text.split(" ").length, total_tokens: 0 };
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens;
        post({ type: "memu-module-event", id, event: "usage", usage });
        const result = { text, reasoning: payload.params && payload.params.thinking ? "mock reasoning. " : "", usage, finish_reason: "stop", model: payload.model, ms: Date.now() - started };
        post({ type: "memu-module-event", id, event: "done", result });
        return result;
      }
      case "store": {

        const op = String(payload.op || "get");

        const keyOk = (raw) => {
          if (typeof raw !== "string") return false;
          const key = raw.trim();
          return /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(\/[A-Za-z0-9][A-Za-z0-9._-]{0,95}){0,2}$/.test(key)
            && !key.split("/").some((seg) => seg.endsWith(".") || /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(seg));
        };
        const need = (raw) => { const key = typeof raw === "string" ? raw.trim() : raw; if (!keyOk(raw)) fail(L(`Некорректный ключ хранилища ${JSON.stringify(key)}: латиница, цифры, «.», «_», «-», до трёх частей через «/» (например "days/2026-10-10")`,
          `Invalid storage key ${JSON.stringify(key)}: Latin letters, digits, ".", "_", "-", up to three "/" parts (e.g. "days/2026-10-10")`)); return key; };
        if (op === "get") { const key = need(payload.key); return { key, value: state.store.has(key) ? JSON.parse(state.store.get(key)) : null, exists: state.store.has(key) }; }
        if (op === "get_many") { const values = {}; for (const key of (payload.keys || []).slice(0, 500)) if (keyOk(key) && state.store.has(key)) values[key] = JSON.parse(state.store.get(key)); return { values }; }
        if (op === "set") {
          const key = need(payload.key); const text = JSON.stringify(payload.value ?? null);
          if (text.length > RULES.store_value_limit) fail(L("Значение слишком большое", "The value is too large"));
          state.store.set(key, text); return { ok: true, key, bytes: text.length };
        }
        if (op === "remove") { const key = need(payload.key); return { ok: true, removed: state.store.delete(key) }; }
        if (op === "list") { const prefix = String(payload.prefix || ""); return { keys: [...state.store.keys()].filter((key) => key.startsWith(prefix)).sort() }; }
        if (op === "usage") { let bytes = 0; for (const value of state.store.values()) bytes += value.length; return { bytes, keys: state.store.size, limit: 2 * 1024 * 1024 * 1024, value_limit: RULES.store_value_limit }; }
        fail(L("Неизвестная операция хранилища", "Unknown storage operation"));
        break;
      }
      case "asset": {
        const name = String(payload.name || "").replace(/^\/+/, "");
        if (!name || name.split("/").some((seg) => !seg || seg === "." || seg === "..")) fail(L("Некорректное имя файла", "Invalid file name"));
        if (!Object.prototype.hasOwnProperty.call(pkg.assets || {}, name)) fail(L(`Нет файла assets/${name}`, `No file assets/${name}`));
        const text = pkg.assets[name];
        if (/\.json$/i.test(name)) { try { return { name, json: JSON.parse(text) }; } catch (error) { fail(`assets/${name}: invalid JSON — ${error.message}`); } }
        return { name, text };
      }
      case "net_import": {

        const input = String(payload.input || "").trim();
        if (!input) fail(L("Вставьте ссылку или id", "Paste a link or an id"));
        const target = netImportTarget(input);
        if (!target) { const error = new Error(L("Ссылка не распознана или сайт не разрешён", "The link was not recognised or the site is not allowed")); error.code = "unsupported"; throw error; }
        const declared = m.net_import && Array.isArray(m.net_import.sources) ? m.net_import.sources : null;
        if (declared && !declared.includes(target.source)) { const error = new Error(L("Модуль не объявил этот источник", "The module did not declare this source")); error.code = "source_not_allowed"; throw error; }
        const kind = String(payload.kind || "auto");
        if ((kind === "character" || kind === "lorebook") && target.kind !== kind && target.source !== "generic") {
          const error = new Error(target.kind === "lorebook" ? L("Эта ссылка ведёт на лорбук", "This link points to a lorebook") : L("Эта ссылка ведёт на персонажа", "This link points to a character"));
          error.code = "wrong_kind"; throw error;
        }
        if (payload.op === "resolve") return { ok: true, ...target, trusted: state.netTrusted.has(target.source) };
        if (opts.failNetwork) { const error = new Error(L("Сайт недоступен (тест --fail-network)", "The site is unavailable (--fail-network test)")); error.code = "network"; throw error; }

        report.logs.push(`net_import: the person would be asked before downloading from ${target.label}`);
        state.netTrusted.add(target.source);
        const name = "Mock " + (target.kind === "lorebook" ? "lorebook" : "character");
        const data = target.kind === "lorebook"
          ? { entries: { 0: { uid: 0, key: ["mock"], keysecondary: [], comment: "Mock entry", content: "Mock lore.", constant: false, selective: true, order: 100, position: 0, disable: false } } }
          : { spec: "chara_card_v2", spec_version: "2.0", data: { name, description: "A mock card from " + target.label, personality: "", scenario: "", first_mes: "Hello, {{user}}!", mes_example: "", creator_notes: "", system_prompt: "", post_history_instructions: "", alternate_greetings: [], tags: ["mock"], creator: "mock", character_version: "1", extensions: {} } };
        const json = JSON.stringify(data);
        return { ok: true, source: target.source, label: target.label, id: target.id, type: target.kind, name,
          file: { name: name.replace(/ /g, "_") + ".json", mime: "application/json", bytes: Buffer.byteLength(json), base64: Buffer.from(json).toString("base64") },
          avatar: null, page: /^https?:/i.test(input) ? input : "" };
      }
      case "cancel": { const job = state.jobs.get(String(payload.id || "")); if (job) job.cancelled = true; return { ok: true, cancelled: !!job }; }
      case "image": case "video": needsModel(payload); if (!String(payload.prompt || "").trim()) fail(L("Опишите, что сгенерировать", "Describe what to generate")); return { urls: [PNG], ms: 1200 };
      case "speak": if (!String(payload.text || "").trim()) fail(L("Нет текста для озвучки", "No text to speak")); return { audio: WAV, mime: "audio/wav", spoken_text: String(payload.text) };
      case "transcribe": if (!/^data:audio\//.test(String(payload.audio || ""))) fail(L("Нужен аудиофайл data:audio/…", "An audio data URL is required")); return { text: "mock transcript", postprocessed: false, postprocess_error: "" };
      case "record": return payload.action === "stop" ? { audio: WAV, mime: "audio/wav", bytes: 44, ms: 1000 } : payload.action === "cancel" ? { ok: true } : { recording: true, started: Date.now() };
      case "music": if (payload.action === "models" || payload.action === "config") return { models: [{ id: "mock-music", name: "Mock music" }] }; if (payload.action === "cancel") return { ok: true }; return { audio: WAV, mime: "audio/wav", model: payload.model || "mock-music", provider: "mock" };
      case "monitor": return { ok: true, enabled: !!payload.enabled, events: [], sources: [] };
      case "extract": if (!payload.data) fail(L("Нет файла", "No file")); return { name: String(payload.name || "file.txt"), text: "mock document text", chars: 18, truncated: false };
      case "download": if (!payload.text && !payload.url) fail(L("Нечего сохранять", "Nothing to save")); return { path: "C:\\Users\\you\\Downloads\\MemuAI\\" + String(payload.name || "file.txt"), name: String(payload.name || "file.txt"), bytes: String(payload.text || payload.url).length };
      case "handoff": return { prompt: String(payload.prompt || ""), images: (payload.images || []).length, files: (payload.files || []).length, readme: "README.md", saved: true, paths: ["C:\\Users\\you\\Downloads\\MemuAI\\handoff"] };
      case "notify": return { ok: true };
      case "open": if (!/^https?:\/\//i.test(String(payload.url || ""))) fail(L("Можно открыть только адрес http(s)", "Only an http(s) address can be opened")); return { ok: true };
      case "search": return { results: [{ title: "Mock result", url: "https://example.com", snippet: "mock" }], count: 1 };
      case "fetch": return { text: "mock page text", title: "Mock page", url: String(payload.url || "") };
      default: fail(L(`Неизвестное действие модуля: ${action}`, `Unknown module action: ${action}`));
    }
    return null;
  }
  function env() {
    return { module: m.id || "", lang: state.lang, theme: opts.theme, accent_name: "terracotta", accent: "", surface: "", density: "normal", font_scale: 100,
      toolbar: opts.toolbar === "host" ? "host" : "", platform: "win32", host: "MemuAI" };
  }
  return { handle, state, env, granted, persist };
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function manifestPublic(m) {
  return { id: m.id, title: m.title || m.id, title_en: m.title_en || m.title || m.id, version: m.version || "1.0", icon: m.icon || "", window: m.window || {},
    persist: m.persist || [], permissions: Array.isArray(m.permissions) ? m.permissions : RULES.permissions, bridge: { model: "", instructions: "", keep_in_chat: false, thinking: false, web_search: false, ...(m.bridge || {}) },
    builtin: false, default_locale: m.default_locale || "ru" };
}

async function run(pkg, opts) {
  const report = { errors: [], warnings: [], logs: [], logErrors: [], calls: [], dropped: new Set(), untranslated: [], closed: false, dom: "mini" };
  const host = makeHost(pkg, opts, report);
  let JSDOM = null;
  if (!opts.mini) { try { ({ JSDOM } = require("jsdom")); } catch (_) { JSDOM = null; } }
  let win;
  const shell = `<!DOCTYPE html><html lang="${opts.lang}" data-theme="${opts.theme}"${opts.toolbar === "host" ? ' data-host-toolbar="1"' : ""}><head><meta charset="utf-8"><style>${pkg.css.replace(/<\/style/gi, "<\\/style")}</style></head><body>${pkg.html}</body></html>`;
  if (JSDOM) {
    const dom = new JSDOM(shell, { runScripts: "outside-only", pretendToBeVisual: true, url: "about:blank" });
    win = dom.window;
    report.dom = "jsdom";
  } else {
    win = makeMiniWindow(opts.lang);
    parseInto(win.document.body, pkg.html, win.document);
    win.document.documentElement.setAttribute("lang", opts.lang);
    win.document.documentElement.setAttribute("data-theme", opts.theme);
    if (opts.toolbar === "host") win.document.documentElement.setAttribute("data-host-toolbar", "1");
  }

  const reportError = (error) => {
    const text = error && error.stack ? String(error.stack).split("\n").slice(0, 3).join(" | ") : String(error);
    report.errors.push(text.replace(/main\.js:(\d+)/g, (_, n) => "main.js:" + Math.max(1, Number(n) - 1)));
  };
  win.__reportError = reportError;

  const blocked = (what, hint) => { report.errors.push(`${what} is blocked in the module window — ${hint}`); };
  const sec = (name) => () => { const e = new Error(`Failed to read the '${name}' property from 'Window': The document is sandboxed and lacks the 'allow-same-origin' flag.`); e.name = "SecurityError"; throw e; };
  for (const name of ["localStorage", "sessionStorage", "indexedDB"]) { try { Object.defineProperty(win, name, { configurable: true, get: sec(name) }); } catch (_) {  } }
  win.alert = (text) => { blocked("alert()", "draw your own message"); void text; };
  win.confirm = () => { blocked("confirm()", "it returns false; draw your own dialog"); return false; };
  win.prompt = () => { blocked("prompt()", "it returns null; use an input field"); return null; };
  win.open = () => { blocked("window.open()", "use Memu.open({url})"); return null; };
  win.fetch = () => Promise.reject(new TypeError("Failed to fetch: connect-src 'none' — use Memu.fetch / Memu.request"));
  win.XMLHttpRequest = function () { throw new Error("XMLHttpRequest is blocked (connect-src 'none') — use Memu.fetch"); };
  win.WebSocket = function () { throw new Error("WebSocket is blocked (connect-src 'none')"); };
  win.EventSource = function () { throw new Error("EventSource is blocked (connect-src 'none')"); };
  win.Worker = function () { throw new Error("Workers are blocked in the module window"); };
  if (!JSDOM) installMiniGlobals(win, opts);
  try { Object.defineProperty(win.navigator, "clipboard", { configurable: true, get() { return { writeText: () => Promise.reject(new Error("navigator.clipboard is unavailable in the sandbox — use Memu.copy")), readText: () => Promise.reject(new Error("use Memu.paste")) }; } }); } catch (_) {}
  try { Object.defineProperty(win.navigator, "mediaDevices", { configurable: true, get() { return { getUserMedia: () => Promise.reject(new Error("The microphone is unavailable to the module page — use Memu.record")) }; } }); } catch (_) {}

  const post = (data) => setTimeout(() => {
    const ev = new win.MessageEvent("message", { data });
    win.dispatchEvent(ev);
  }, 0);
  const parent = { postMessage: (message) => {
    if (!message || message.type !== "memu-module") { if (message && message.type === "memu-module-key") report.logs.push("Esc forwarded to the host"); return; }
    report.calls.push(message.action + (message.action === "host" ? ":" + (message.payload || {}).command : ""));
    Promise.resolve().then(() => host.handle(message.action, message.payload || {}, message.id, post))
      .then((result) => post({ type: "memu-module-result", id: message.id, result: result === undefined ? null : result, error: "" }),
        (error) => post({ type: "memu-module-result", id: message.id, result: null, error: String(error && error.message || error) }));
  } };
  try { Object.defineProperty(win, "parent", { configurable: true, value: parent }); } catch (_) { win.parent = parent; }

  const bridge = BRIDGE_SOURCE
    .replace("__MEMU_MODULE_ID__", JSON.stringify(String(pkg.manifest.id || "module")))
    .replace("__MEMU_ENV__", JSON.stringify(host.env()))
    .replace("__MEMU_MANIFEST__", JSON.stringify(manifestPublic(pkg.manifest)))
    .replace("__MEMU_SAVED__", JSON.stringify({}))
    .replace("__MEMU_I18N__", JSON.stringify(pkg.i18n || {}));
  const exec = (code, file) => {
    if (JSDOM) { try { win.eval(code); } catch (error) { reportError(error); } return; }
    try { new vm.Script(code, { filename: file }).runInContext(win.__context); } catch (error) { reportError(error); }
  };
  win.addEventListener("error", (event) => { if (event && event.error) reportError(event.error); });
  process.on("unhandledRejection", (reason) => reportError(reason));
  exec(bridge, "memu-bridge.js");
  exec("window.__memuModuleMain=function(){\n" + pkg.js + "\n};", "main.js");
  exec("if (typeof window.__memuModuleMain === 'function') window.__memuModuleMain();", "start.js");
  if (!JSDOM) { win.document.readyState = "interactive"; win.document.dispatchEvent(new win.Event("DOMContentLoaded", { bubbles: true })); win.document.readyState = "complete"; win.dispatchEvent(new win.Event("load")); }
  await idle();

  const other = opts.lang === "en" ? "ru" : "en";
  host.state.lang = other;
  post({ type: "memu-module-host", action: "language", payload: { lang: other } });
  await idle();
  post({ type: "memu-module-host", action: "theme", payload: { theme: opts.theme === "dark" ? "light" : "dark", accent_name: "blue", tokens: {}, lang: other } });
  post({ type: "memu-module-host", action: "host-event", payload: { name: "window", data: { state: "expanded" } } });
  await idle();
  checkTranslations(win, pkg, report, other);
  const t = tester(win, report);
  if (opts.clickAll) {
    for (const b of [...win.document.querySelectorAll("button, [role=button]")]) {
      if (b.disabled) continue;
      try { b.click(); } catch (error) { reportError(error); }
      await idle();
    }

    const bar = host.state.toolbar;
    for (const item of bar ? bar.items : []) {
      if (!item || item.kind === "separator" || item.sep || item.kind === "text" || item.disabled || !item.id) continue;
      const picks = Array.isArray(item.menu) ? item.menu.filter((e) => e && !e.sep && !e.disabled && e.id).map((e) => String(e.id)) : [""];
      for (const value of picks) {
        report.toolbarClicks = (report.toolbarClicks || 0) + 1;
        post({ type: "memu-module-host", action: "host-event", payload: { name: "toolbar", data: { id: String(item.id), value, kind: item.kind || "button" } } });
        await idle();
      }
    }
  }
  if (opts.script) {
    try { const fn = require(path.resolve(opts.script)); await (typeof fn === "function" ? fn : fn.default)(t); }
    catch (error) { reportError(error); }
    await idle();
  }
  report.saved = { ...host.state.saved };
  report.toolbar = host.state.toolbar || null;
  return report;
}
function idle() { return sleep(120); }

function checkTranslations(win, pkg, report, lang) {
  const keys = new Set();
  for (const match of pkg.html.matchAll(/data-i18n(?:-[a-z-]+)?\s*=\s*["']([^"']+)["']/g)) keys.add(match[1]);
  const shown = [...win.document.querySelectorAll("[data-i18n]")].filter((el) => keys.has(el.textContent.trim()) && el.textContent.trim().includes("."));
  for (const el of shown.slice(0, 10)) report.untranslated.push(`${lang}: <${el.localName}> shows the key “${el.textContent.trim()}”`);
}

function tester(win, report) {
  const doc = win.document;
  const need = (sel) => { const el = doc.querySelector(sel); if (!el) throw new Error(`no element matches ${sel}`); return el; };
  return {
    window: win, document: doc, report,
    $: (sel) => doc.querySelector(sel),
    click: (sel) => need(sel).click(),
    type: (sel, text) => { const el = need(sel); el.value = text; el.dispatchEvent(new win.Event("input", { bubbles: true })); el.dispatchEvent(new win.Event("change", { bubbles: true })); },
    key: (sel, key, extra = {}) => { const el = need(sel); el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra })); },
    text: (sel) => need(sel).textContent,
    html: (sel) => need(sel).innerHTML,
    idle, sleep,
    expect: (ok, message) => { if (!ok) report.errors.push("expectation failed: " + (message || "")); },
    calls: () => report.calls.slice(),
  };
}

function installMiniGlobals(win, opts) {
  class Event_ { constructor(type, init = {}) { this.type = type; this.bubbles = !!init.bubbles; this.cancelable = !!init.cancelable; this.defaultPrevented = false; this.detail = init.detail; Object.assign(this, init); }
    preventDefault() { if (this.cancelable !== false) this.defaultPrevented = true; } stopPropagation() { this._stop = true; } stopImmediatePropagation() { this._stop = true; this._stopNow = true; } composedPath() { return []; } }
  class MessageEvent_ extends Event_ { constructor(type, init = {}) { super(type, init); this.data = init.data; this.source = init.source || null; this.origin = "null"; } }
  class KeyboardEvent_ extends Event_ { constructor(type, init = {}) { super(type, { bubbles: true, cancelable: true, ...init }); this.key = init.key || ""; this.code = init.code || ""; this.ctrlKey = !!init.ctrlKey; this.altKey = !!init.altKey; this.shiftKey = !!init.shiftKey; this.metaKey = !!init.metaKey; this.repeat = !!init.repeat; this.isComposing = false; } getModifierState() { return false; } }
  class MouseEvent_ extends Event_ { constructor(type, init = {}) { super(type, init); this.clientX = init.clientX || 0; this.clientY = init.clientY || 0; this.button = init.button || 0; this.ctrlKey = !!init.ctrlKey; this.shiftKey = !!init.shiftKey; this.altKey = !!init.altKey; this.metaKey = !!init.metaKey; } }
  class DOMException_ extends Error { constructor(message, name) { super(message); this.name = name || "Error"; } }
  class Observer { constructor(cb) { this.cb = cb; } observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
  class FileReader_ { readAsDataURL(blob) { setTimeout(() => { this.result = "data:" + (blob && blob.type || "application/octet-stream") + ";base64,bW9jaw=="; this.onload && this.onload({ target: this }); this.onloadend && this.onloadend({ target: this }); }, 1); }
    readAsText(blob) { setTimeout(() => { this.result = "mock file text"; this.onload && this.onload({ target: this }); this.onloadend && this.onloadend({ target: this }); }, 1); }
    readAsArrayBuffer() { setTimeout(() => { this.result = new ArrayBuffer(4); this.onload && this.onload({ target: this }); }, 1); } addEventListener(t, fn) { this["on" + t] = fn; } abort() {} }
  const doc = win.document;
  class Image_ { constructor(w, h) { const el = doc.createElement("img"); if (w) el.setAttribute("width", w); if (h) el.setAttribute("height", h); return el; } }
  class Audio_ { constructor(src) { const el = doc.createElement("audio"); if (src) el.setAttribute("src", src); el.currentTime = 0; el.duration = 1; el.paused = true; el.play = () => { el.paused = false; return Promise.resolve(); }; el.pause = () => { el.paused = true; }; return el; } }
  const tokens = { "--bg": "#1f1f1e", "--panel": "#262624", "--text": "#ececec", "--muted": "#9b9b96", "--accent": "#d97757", "--border": "#3a3a37" };
  Object.assign(win, {
    window: win, self: win, globalThis: win, top: win, frames: win, Event: Event_, CustomEvent: Event_, MessageEvent: MessageEvent_, KeyboardEvent: KeyboardEvent_, MouseEvent: MouseEvent_,
    PointerEvent: MouseEvent_, FocusEvent: Event_, InputEvent: Event_, DragEvent: MouseEvent_, WheelEvent: MouseEvent_, DOMException: DOMException_,
    MutationObserver: Observer, ResizeObserver: Observer, IntersectionObserver: Observer, FileReader: FileReader_, Image: Image_, Audio: Audio_,
    HTMLElement: Element_, Element: Element_, Node: Node_, HTMLInputElement: Element_, HTMLTextAreaElement: Element_, HTMLSelectElement: Element_, HTMLButtonElement: Element_, HTMLCanvasElement: Element_,
    navigator: { language: opts.lang === "en" ? "en-US" : "ru-RU", languages: [opts.lang], userAgent: "MemuAI module test (Node)", platform: "Win32", onLine: true, hardwareConcurrency: 4 },
    location: { href: "about:srcdoc", origin: "null", protocol: "about:", host: "", hostname: "", pathname: "srcdoc", search: "", hash: "", reload() { blocked(); } },
    history: { pushState() {}, replaceState() {}, back() {}, length: 1 }, screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040 },
    innerWidth: Number((opts.size || "760x540").split("x")[0]) || 760, innerHeight: 540, devicePixelRatio: 1, scrollX: 0, scrollY: 0,
    matchMedia: (q) => ({ matches: /dark/.test(q) ? opts.theme === "dark" : false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
    getComputedStyle: () => new Proxy({}, { get: (_, k) => (k === "getPropertyValue" ? (n) => tokens[n] || "" : "") }),
    requestAnimationFrame: (fn) => setTimeout(() => fn(Date.now()), 16), cancelAnimationFrame: (h) => clearTimeout(h), requestIdleCallback: (fn) => setTimeout(() => fn({ timeRemaining: () => 10, didTimeout: false }), 1), cancelIdleCallback: (h) => clearTimeout(h),
    setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask, structuredClone, atob, btoa, console, Intl, URL, URLSearchParams, Blob, TextEncoder, TextDecoder, AbortController,
    crypto: require("crypto").webcrypto, performance: { now: () => Date.now() }, scrollTo() {}, scrollBy() {}, focus() {}, blur() {}, print() {}, getSelection: () => doc.getSelection(),
    CSS: { escape: (s) => String(s).replace(/([^\w-])/g, "\\$1"), supports: () => true }, File: typeof File !== "undefined" ? File : Blob, FormData: class { append() {} },
  });
  function blocked() { win.__reportError(new Error("navigation is blocked in the module window")); }
  if (typeof win.crypto.randomUUID !== "function") win.crypto.randomUUID = () => require("crypto").randomUUID();

  win.__context = vm.createContext(win, { codeGeneration: { strings: false, wasm: false } });
}

function print(report, statics, pkg, opts) {
  const en = opts.lang === "en";
  const lines = [];
  const errorsStatic = statics.filter((p) => p.level === "error");
  lines.push(`${en ? "Module" : "Модуль"} “${pkg.manifest.id || "?"}” — ${(en && pkg.manifest.title_en) || pkg.manifest.title || ""}   [DOM: ${report.dom}]`);
  lines.push("");
  lines.push(en ? "Static checks:" : "Статические проверки:");
  if (!statics.length) lines.push("  ✓ " + (en ? "nothing found" : "замечаний нет"));
  for (const p of statics) lines.push(`  ${p.level === "error" ? "✖" : p.level === "warn" ? "▲" : "·"} [${p.field}${p.line ? ":" + p.line : ""}] ${p.text}`);
  lines.push("");
  lines.push(en ? "Run against the mock MemuAI:" : "Запуск с моком MemuAI:");
  lines.push(`  ${en ? "bridge calls" : "вызовы моста"}: ${report.calls.length ? report.calls.join(", ") : "—"}`);
  if (report.toolbar) lines.push(`  ${en ? "window header" : "шапка окна"}: ${report.toolbar.items.filter((i) => i && i.id).map((i) => i.id).join(", ") || "—"}${report.toolbarClicks ? ` (${en ? "clicked" : "нажато"} ${report.toolbarClicks})` : ""}`);
  else if (opts.toolbar === "host" && /<header\b/i.test(pkg.html)) lines.push("  ▲ " + (en ? "the module paints its own <header> but hands nothing to Memu.toolbar — inside MemuAI that is a second row of buttons under the window's row"
    : "модуль рисует свой <header>, но ничего не отдаёт в Memu.toolbar — в MemuAI это второй ряд кнопок под строкой окна"));
  if (Object.keys(report.saved || {}).length) lines.push(`  ${en ? "saved" : "сохранено"}: ${Object.keys(report.saved).join(", ")}`);
  if (report.dropped.size) lines.push(`  ✖ ${en ? "Memu.save dropped keys missing from persist" : "Memu.save отбросил ключи вне persist"}: ${[...report.dropped].join(", ")}`);
  for (const e of report.errors) lines.push("  ✖ " + e);
  for (const e of report.logErrors) lines.push("  ✖ Memu.log(error): " + e);
  for (const w of report.warnings) lines.push("  ▲ " + w);
  for (const u of report.untranslated) lines.push("  ▲ " + u);
  if (!report.errors.length && !report.logErrors.length) lines.push("  ✓ " + (en ? "no runtime errors" : "ошибок выполнения нет"));
  const failed = errorsStatic.length + report.errors.length + report.logErrors.length + report.dropped.size;
  lines.push("");
  lines.push(failed ? (en ? `RESULT: ${failed} problem(s) to fix` : `ИТОГ: исправить ${failed}`) : (en ? "RESULT: OK — ready to import into MemuAI" : "ИТОГ: всё в порядке — можно импортировать в MemuAI"));
  return { text: lines.join("\n"), failed };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help || !opts.target) { console.log(fs.readFileSync(__filename, "utf8").split("*/")[0]); process.exit(opts.target ? 0 : 2); }
  if (!BRIDGE_SOURCE || !RULES) { console.error("This copy of memu-module-test.js has no bridge inside: download it from MemuAI (Settings → Documentation → Modules → AI pack)."); process.exit(2); }
  let pkg;
  try { pkg = loadPackage(opts.target); } catch (error) { console.error((opts.lang === "en" ? "Cannot read the module: " : "Не удалось прочитать модуль: ") + error.message); process.exit(2); }
  const statics = staticChecks(pkg, opts.lang);
  const report = await run(pkg, opts);
  const { text, failed } = print(report, statics, pkg, opts);
  if (opts.json) console.log(JSON.stringify({ static: statics, calls: report.calls, toolbar: report.toolbar, toolbarClicks: report.toolbarClicks || 0, errors: report.errors, logs: report.logs, logErrors: report.logErrors, dropped: [...report.dropped], untranslated: report.untranslated, saved: report.saved, dom: report.dom, ok: !failed }, null, 2));
  else console.log(text);
  process.exit(failed ? 1 : 0);
}

if (require.main === module) main();
module.exports = { loadPackage, staticChecks, run, readZip };
