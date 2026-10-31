/* MemuModelSelector behaviour test: the real app/pgstatic/model-selector.js
 * runs in node:vm against a small DOM stub (no jsdom in this VM). This is
 * NOT a browser: layout, paint and real focus are not exercised — the
 * final visual check belongs to the browser agent. What is exercised:
 * provider names (never "custom"), grouping, search, favourites/recent,
 * loading/error states, all 12 presets, create() onChange and
 * mountSelect() two-way sync with a native <select>.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const JS = fs.readFileSync(path.join(ROOT, "app", "pgstatic", "model-selector.js"), "utf8");

/* ---------------- minimal DOM ---------------- */

const VOID = new Set(["input", "br", "hr", "img", "meta", "link", "source", "area", "base", "col", "embed", "track", "wbr"]);

function decodeEntities(s) {
  return String(s).replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
}
function encodeText(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function camelData(name) {
  return name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}

class TextNode {
  constructor(text) { this.nodeType = 3; this.textContent = String(text); this.parentNode = null; }
}

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._attrs = {};
    this.dataset = {};
    this._cls = new Set();
    this._listeners = {};
    this._styleProps = new Map();
    this.style = {
      setProperty: (k, v) => { this._styleProps.set(k, String(v)); },
      getPropertyValue: (k) => this._styleProps.get(k) || "",
    };
    this._ownText = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.tabIndex = 0;
    this.src = "";
    this.clientWidth = 0;
    const self = this;
    this.classList = {
      add(...names) { names.forEach((n) => self._cls.add(n)); },
      remove(...names) { names.forEach((n) => self._cls.delete(n)); },
      toggle(name, force) {
        const on = force === undefined ? !self._cls.has(name) : !!force;
        if (on) self._cls.add(name); else self._cls.delete(name);
        return on;
      },
      contains(name) { return self._cls.has(name); },
    };
  }
  get className() { return [...this._cls].join(" "); }
  set className(v) { this._cls = new Set(String(v || "").split(/\s+/).filter(Boolean)); }
  get textContent() {
    if (!this.children.length) return this._ownText;
    return this.children.map((c) => c.textContent || "").join("");
  }
  set textContent(v) { this._ownText = String(v == null ? "" : v); this.children = []; }
  get innerHTML() { return this.children.map(serialize).join(""); }
  set innerHTML(html) {
    this.children = [];
    this._ownText = "";
    parseHTML(String(html || ""), this);
  }
  setAttribute(name, value) {
    const v = String(value == null ? "" : value);
    this._attrs[name] = v;
    if (name.startsWith("data-")) this.dataset[camelData(name)] = v;
    if (name === "class") this.className = v;
    if (name === "hidden") this.hidden = true;
    if (name === "value") this.value = v;
  }
  getAttribute(name) { return name in this._attrs ? this._attrs[name] : null; }
  hasAttribute(name) { return name in this._attrs; }
  removeAttribute(name) {
    delete this._attrs[name];
    if (name.startsWith("data-")) delete this.dataset[camelData(name)];
    if (name === "hidden") this.hidden = false;
    if (name === "class") this._cls = new Set();
  }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i >= 0) this.children.splice(i, 0, child); else this.children.push(child);
    return child;
  }
  insertAdjacentHTML(position, html) {
    if (position !== "afterbegin") throw new Error("stub: only afterbegin is supported");
    const frag = new El("div");
    parseHTML(String(html), frag);
    const kids = frag.children.slice();
    kids.forEach((k) => { k.parentNode = null; });
    this.children = [...kids, ...this.children];
    kids.forEach((k) => { k.parentNode = this; });
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const list = this._listeners[type] || [];
    const i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    let node = this;
    while (node) {
      const list = (node._listeners && node._listeners[ev.type]) || [];
      for (const fn of list.slice()) {
        fn.call(node, ev);
        if (ev._stopped) return true;
      }
      if (!ev.bubbles || ev._stopped) break;
      node = node.parentNode;
    }
    return !ev.defaultPrevented;
  }
  click() { this.dispatchEvent(new Event("click", { bubbles: true })); }
  focus() { if (globalThis.__stubDocument) globalThis.__stubDocument.activeElement = this; }
  setSelectionRange() {}
  closest(sel) {
    let node = this;
    while (node) {
      if (node.nodeType === 1 && matchesCompound(node, sel)) return node;
      node = node.parentNode;
    }
    return null;
  }
  contains(node) {
    let cur = node;
    while (cur) { if (cur === this) return true; cur = cur.parentNode; }
    return false;
  }
  matches(sel) { return matchesCompound(this, sel); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const parts = sel.trim().split(/\s+/);
    const out = [];
    const walk = (el) => {
      for (const child of el.children) {
        if (child.nodeType === 1) {
          if (matchesChain(child, parts)) out.push(child);
          walk(child);
        }
      }
    };
    walk(this);
    return out;
  }
}

function serialize(node) {
  if (node.nodeType === 3) return encodeText(node.textContent);
  const attrs = Object.entries(node._attrs).map(([k, v]) => ` ${k}="${encodeText(v).replace(/"/g, "&quot;")}"`).join("");
  const tag = node.tagName.toLowerCase();
  if (VOID.has(tag)) return `<${tag}${attrs}>`;
  return `<${tag}${attrs}>${node.children.map(serialize).join("")}</${tag}>`;
}

function matchesCompound(el, sel) {
  const m = sel.match(/^([a-zA-Z][\w-]*)?((?:\.[\w-]+|\[[^\]]+\])*)$/);
  if (!m) return false;
  if (m[1] && el.tagName.toLowerCase() !== m[1].toLowerCase()) return false;
  const rest = m[2] || "";
  const parts = rest.match(/\.[\w-]+|\[[^\]]+\]/g) || [];
  for (const part of parts) {
    if (part.startsWith(".")) {
      if (!el._cls.has(part.slice(1))) return false;
    } else {
      const am = part.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
      if (!am) return false;
      const val = el.getAttribute(am[1]);
      if (am[2] === undefined) { if (val === null) return false; }
      else if (val !== am[2]) return false;
    }
  }
  return true;
}

function matchesChain(el, parts) {
  if (!matchesCompound(el, parts[parts.length - 1])) return false;
  let node = el.parentNode;
  for (let i = parts.length - 2; i >= 0; i--) {
    let found = false;
    while (node) {
      if (node.nodeType === 1 && matchesCompound(node, parts[i])) { found = true; node = node.parentNode; break; }
      node = node.parentNode;
    }
    if (!found) return false;
  }
  return true;
}

function parseHTML(html, parent) {
  const stack = [parent];
  const re = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][\w-]*)([^>]*)>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    if (m[4] !== undefined) {
      const text = decodeEntities(m[4]);
      if (text) stack[stack.length - 1].appendChild(new TextNode(text));
      continue;
    }
    if (m[1] === undefined && m[2] === undefined) continue; // comment
    const tag = m[2].toLowerCase();
    if (m[1] === "/") {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const el = new El(tag);
    const attrRe = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
    let am;
    const rawAttrs = m[3] || "";
    while ((am = attrRe.exec(rawAttrs))) {
      const val = am[2] !== undefined ? am[2] : am[3] !== undefined ? am[3] : am[4] !== undefined ? am[4] : "";
      el.setAttribute(am[1], decodeEntities(val));
    }
    const selfClosing = /\/\s*$/.test(rawAttrs) || VOID.has(tag);
    stack[stack.length - 1].appendChild(el);
    if (!selfClosing) stack.push(el);
  }
}

class Event {
  constructor(type, opts = {}) {
    this.type = type;
    this.bubbles = !!opts.bubbles;
    this.cancelable = !!opts.cancelable;
    this.defaultPrevented = false;
    this._stopped = false;
    Object.assign(this, opts);
    delete this.bubbles;
    this.bubbles = !!opts.bubbles;
    if (opts.key !== undefined) this.key = opts.key;
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this._stopped = true; }
}

class MutationObserver {
  constructor() {}
  observe() {}
  disconnect() {}
}

function makeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    clear: () => { map.clear(); },
  };
}

function makeContext({ lang = "ru", memu = null } = {}) {
  const documentElement = new El("html");
  documentElement.lang = lang;
  const documentStub = {
    documentElement,
    activeElement: null,
    _listeners: {},
    createElement: (tag) => new El(tag),
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    removeEventListener(type, fn) {
      const list = this._listeners[type] || [];
      const i = list.indexOf(fn);
      if (i >= 0) list.splice(i, 1);
    },
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
  };
  globalThis.__stubDocument = documentStub;
  const sandbox = {
    document: documentStub,
    navigator: { language: lang === "en" ? "en-US" : "ru-RU" },
    localStorage: makeStorage(),
    MutationObserver,
    Event,
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
  };
  sandbox.window = sandbox;
  if (memu) sandbox.Memu = memu;
  vm.createContext(sandbox);
  vm.runInContext(JS, sandbox, { filename: "model-selector.js" });
  return { sandbox, Sel: sandbox.MemuModelSelector, document: documentStub };
}

const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
// Arrays built inside the vm context carry another realm's prototype,
// so deepStrictEqual rejects them: compare through JSON.
const j = (v) => JSON.parse(JSON.stringify(v));

const ROWS = [
  { id: "acme-gpt-5", model: "gpt-5", name: "GPT-5", provider: "custom", provider_label: "Acme Corp", context: 128000, kind: "chat" },
  { id: "myconn-claude-opus", model: "claude-opus", name: "Claude Opus", provider: "custom", account: "My Connection", context: 200000 },
  { id: "openai-gpt-5", model: "gpt-5", name: "GPT-5", provider: "openai", context: 400000 },
  { id: "genspark-claude-opus-5", model: "claude-opus-5", name: "Claude Opus 5", provider: "custom", provider_label: "", account: "" },
  { id: "img-1", name: "Image Gen", image: true, kind: "image" },
  { id: "gpt-5", model: "gpt-5", name: "GPT-5", provider: "custom" },
];

async function main() {
  /* ---- provider names: real catalogue names, never "custom" ---- */
  {
    const { Sel } = makeContext();
    assert.equal(Sel.providerName(ROWS[0]), "Acme Corp");
    assert.equal(Sel.providerName(ROWS[1]), "My Connection");
    assert.equal(Sel.providerName(ROWS[2]), "OpenAI");
    assert.equal(Sel.providerName(ROWS[3]), "Genspark");
    assert.equal(Sel.providerName(ROWS[5]), "Gpt");
    assert.equal(Sel.providerName({ provider: "custom" }), "Внешний API");
    for (const row of ROWS) {
      assert.doesNotMatch(Sel.providerName(row), /custom/i, JSON.stringify(row));
    }
    const en = makeContext({ lang: "en" });
    assert.equal(en.Sel.providerName(ROWS[5]), "Gpt");
    assert.equal(en.Sel.providerName({ provider: "custom" }), "External API");
    assert.equal(en.Sel.providerName({ provider: "custom", provider_label: "custom", id: "x-y", model: "y" }), "X");
    assert.equal(Sel.providerName({ provider: "geminiweb", id: "geminiweb-foo", model: "foo" }), "Gemini Web");
    assert.equal(Sel.providerName({ provider: "local", id: "local-foo", model: "foo" }), "Local");
    // The id prefix wins; the raw provider field is only consulted
    // when the prefix itself is a placeholder.
    assert.equal(Sel.providerName({ provider: "openai", id: "custom-x", model: "x" }), "OpenAI");
  }

  /* ---- grouping + search ---- */
  {
    const { Sel } = makeContext();
    const groups = Sel.groupByProvider(ROWS);
    const names = groups.map((g) => g.name);
    assert.ok(names.includes("Acme Corp") && names.includes("Genspark") && names.includes("OpenAI"));
    assert.ok(!names.some((n) => /custom/i.test(n)));
    assert.equal(Sel.filterModels(ROWS, { query: "opus" }).length, 2);
    assert.equal(Sel.filterModels(ROWS, { query: "acme" }).length, 1);
    assert.equal(Sel.filterModels(ROWS, { kind: "image" }).length, 1);
    assert.equal(Sel.filterModels(ROWS, { query: "gpt-5 opus" }).length, 0);
    const hay = Sel.normalize(ROWS)[0];
    assert.ok(hay.providerName && hay.displayName);
  }

  /* ---- favourites / recent storage ---- */
  {
    const { Sel, sandbox } = makeContext();
    const opts = { storageKey: "test:selector" };
    assert.deepEqual(j(Sel.getFavorites(opts)), []);
    Sel.setFavorite("acme-gpt-5", true, opts);
    Sel.setFavorite("openai-gpt-5", true, opts);
    assert.deepEqual(j(Sel.getFavorites(opts)), ["openai-gpt-5", "acme-gpt-5"]);
    Sel.setFavorite("acme-gpt-5", false, opts);
    assert.deepEqual(j(Sel.getFavorites(opts)), ["openai-gpt-5"]);
    for (let i = 0; i < 8; i++) Sel.noteRecent("m" + i, opts);
    assert.equal(j(Sel.getRecent(opts)).length, 6);
    assert.equal(j(Sel.getRecent(opts))[0], "m7");
    // Persisted through localStorage, not only memory.
    const raw = sandbox.localStorage.getItem("test:selector");
    assert.ok(raw && JSON.parse(raw).favorites.includes("openai-gpt-5"));
  }

  /* ---- create(): loading, error + retry, pick, search, favourites ---- */
  {
    const { Sel, document } = makeContext();
    const host = document.createElement("div");
    let calls = 0;
    let failOnce = true;
    const picked = [];
    const api = Sel.create(host, {
      preset: "dropdown",
      storageKey: "test:create",
      load: () => {
        calls++;
        if (failOnce) { failOnce = false; return Promise.reject(new Error("proxy down")); }
        return Promise.resolve(ROWS);
      },
      onChange: (id) => picked.push(id),
    });
    assert.match(host.innerHTML, /Загрузка/);
    await tick(30);
    assert.ok(host.querySelector(".ms-error"), "error state after a failed load");
    assert.equal(calls, 1);
    host.querySelector(".ms-retry").click();
    await tick(30);
    assert.equal(calls, 2);
    assert.equal(host.querySelector(".ms-error"), null);
    assert.ok(host.textContent.includes("Acme Corp"));
    assert.doesNotMatch(host.textContent, /custom/i);
    // Pick a row: onChange fires, recent is recorded.
    const row = host.querySelector('[data-ms-model="myconn-claude-opus"]');
    assert.ok(row, "row exists");
    row.click();
    assert.deepEqual(j(picked), ["myconn-claude-opus"]);
    assert.ok(Sel.getRecent({ storageKey: "test:create" }).includes("myconn-claude-opus"));
    // Star toggle.
    const fav = host.querySelector('[data-ms-fav="openai-gpt-5"]');
    assert.ok(fav, "star exists");
    fav.click();
    assert.ok(Sel.getFavorites({ storageKey: "test:create" }).includes("openai-gpt-5"));
    // Search filters rows.
    const input = host.querySelector(".ms-search-input");
    assert.ok(input, "search box exists");
    input.value = "opus";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const visible = host.querySelectorAll("[data-ms-model]").map((el) => el.dataset.msModel);
    assert.ok(visible.includes("myconn-claude-opus") && visible.includes("genspark-claude-opus-5"));
    assert.ok(!visible.includes("acme-gpt-5"));
    api.destroy();
    assert.equal(host.innerHTML, "");
  }

  /* ---- all 12 presets render, and circle positions its orbit ---- */
  {
    const { Sel, document } = makeContext();
    assert.equal(Sel.PRESETS.length, 12);
    for (const preset of Sel.PRESETS) {
      const host = document.createElement("div");
      Sel.create(host, { preset, models: ROWS, storageKey: "test:preset:" + preset });
      assert.equal(host.getAttribute("data-preset"), preset, preset);
      assert.ok(host.textContent.includes("GPT-5"), preset);
      assert.doesNotMatch(host.textContent, /custom/i, preset);
      if (preset === "circle") {
        const row = host.querySelector(".ms-row");
        assert.ok(row.style.getPropertyValue("--ms-angle"), "circle rows get --ms-angle");
        assert.ok(host.querySelector(".ms-circle-center"), "circle has a centre");
      }
      if (preset === "table") {
        assert.ok(host.querySelector(".ms-table-head"), "table has a header row");
      }
      if (preset === "segmented") {
        assert.ok(host.querySelectorAll("[data-ms-segment]").length >= 3, "segmented has provider tabs");
      }
    }
  }

  /* ---- renderList(): multi-select table with favourites ---- */
  {
    const { Sel, document } = makeContext();
    const host = document.createElement("div");
    const toggles = [];
    const api = Sel.renderList(host, ROWS, {
      preset: "table", multi: true, selected: ["acme-gpt-5"],
      storageKey: "test:list",
      onToggle: (id, selected) => toggles.push([id, selected.slice()]),
    });
    assert.equal(host.getAttribute("data-preset"), "table");
    const first = host.querySelector('[data-ms-model="acme-gpt-5"]');
    assert.ok(first.classList.contains("sel"));
    host.querySelector('[data-ms-model="openai-gpt-5"]').click();
    assert.deepEqual(j(toggles[toggles.length - 1][1].sort()), ["acme-gpt-5", "openai-gpt-5"].sort());
    host.querySelector('[data-ms-model="acme-gpt-5"]').click();
    assert.deepEqual(j(toggles[toggles.length - 1][1]), ["openai-gpt-5"]);
    assert.deepEqual(j(api.getSelected()), ["openai-gpt-5"]);
    api.setSelected(["genspark-claude-opus-5"]);
    assert.ok(host.querySelector('[data-ms-model="genspark-claude-opus-5"]').classList.contains("sel"));
  }

  /* ---- mountSelect(): the <select> stays the source of truth ---- */
  {
    const { Sel, document } = makeContext();
    const parent = document.createElement("div");
    const select = document.createElement("select");
    select.id = "model";
    const mkOpt = (value, text, dataset) => {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = text;
      o.text = text;
      Object.assign(o.dataset, dataset || {});
      return o;
    };
    const g1 = document.createElement("optgroup");
    g1.label = "Acme Corp";
    const o1 = mkOpt("acme-gpt-5", "GPT-5 · acme-gpt-5");
    const o2 = mkOpt("acme-claude", "Claude");
    g1.appendChild(o1); g1.appendChild(o2);
    const g2 = document.createElement("optgroup");
    g2.label = "";
    const o3 = mkOpt("myconn-gpt-5", "GPT-5", { provider: "custom" });
    const oEmpty = mkOpt("", "Выберите модель");
    g2.appendChild(o3); g2.appendChild(oEmpty);
    select._optgroups = [g1, g2];
    select._options = [o1, o2, o3, oEmpty];
    Object.defineProperty(select, "options", { get() { return this._options; } });
    const origQS = select.querySelectorAll.bind(select);
    select.querySelectorAll = (sel) => {
      if (sel === "optgroup") return select._optgroups;
      if (sel === "option") return select._options;
      return origQS(sel);
    };
    select.value = "acme-gpt-5";
    parent.appendChild(select);
    const changes = [];
    select.addEventListener("change", () => changes.push(select.value));
    const handle = Sel.mountSelect(select, { preset: "dropdown", allowEmpty: true, storageKey: "test:mount" });
    assert.ok(handle, "mount returns a handle");
    assert.ok(select.classList.contains("ms-native-hidden"), "native select is hidden");
    const host = parent.children[0];
    assert.ok(host.classList.contains("ms-mount"));
    assert.ok(host.textContent.includes("Acme Corp"), "provider group from optgroup");
    assert.ok(host.textContent.includes("Myconn"), "raw provider 'custom' resolved to the id prefix");
    assert.doesNotMatch(host.textContent, /custom/i);
    // Pick through the picker: the select value changes and change fires.
    host.querySelector('[data-ms-model="myconn-gpt-5"]').click();
    assert.equal(select.value, "myconn-gpt-5");
    assert.deepEqual(j(changes), ["myconn-gpt-5"]);
    assert.ok(Sel.getRecent({ storageKey: "test:mount" }).includes("myconn-gpt-5"));
    // Change the select directly: the picker follows.
    select.value = "acme-claude";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    const triggerText = host.querySelector(".ms-trigger-text");
    assert.ok(triggerText && triggerText.textContent.includes("Claude"), "picker follows the select");
    handle.destroy();
    assert.ok(!select.classList.contains("ms-native-hidden"), "destroy restores the select");
  }


  /* ---- provider names: never "custom", domain fallback ---- */
  {
    const { Sel } = makeContext();
    assert.equal(Sel.providerName({ provider: "custom", id: "custom-gpt-5", model: "gpt-5" }), "Внешний API");
    assert.equal(Sel.providerName({ provider: "custom", id: "custom-x", model: "x", account: "" }), "Внешний API");
    assert.equal(Sel.providerName({ provider: "custom", id: "node-1", base_url: "https://api.acme.example/v1" }), "Acme");
    assert.equal(Sel.providerName({ provider: "openrouter/anthropic", id: "custom-claude-x", model: "claude-x" }), "OpenRouter");
    assert.equal(Sel.providerName({ provider: "custom", provider_label: "Custom API", id: "q-w" }), "Q");
    for (const row of [
      { provider: "custom", id: "custom-gpt-5", model: "gpt-5" },
      { provider: "custom", id: "x-y", model: "y", base_url: "https://api.acme.example/v1" },
    ]) {
      assert.doesNotMatch(Sel.providerName(row), /custom/i, JSON.stringify(row));
    }
    const en = makeContext({ lang: "en" });
    assert.equal(en.Sel.providerName({ provider: "custom", id: "custom-gpt-5", model: "gpt-5" }), "External API");
  }

  /* ---- kinds: catalogue data only ---- */
  {
    const { Sel } = makeContext();
    assert.equal(Sel.kindOf({ kind: "music" }), "music");
    assert.equal(Sel.kindOf({ kind: "text-to-image" }), "image");
    assert.equal(Sel.kindOf({ kind: "chat", caps: { image: true } }), "chat");
    assert.equal(Sel.kindOf({ image: true }), "image");
    assert.equal(Sel.kindOf({ caps: { output_modalities: ["image"] } }), "image");
    assert.equal(Sel.kindOf({ architecture: { output_modalities: ["text", "image"] } }), "chat");
    assert.equal(Sel.kindOf({ caps: { output_modalities: "embeddings" } }), "embedding");
    assert.equal(Sel.kindOf({ stt: true }), "stt");
    assert.equal(Sel.kindOf({ caps: { reasoning: true } }), "chat");
    assert.equal(Sel.kindOf({ kind: "decisions" }), "decisions");
    assert.equal(Sel.kindLabel("image"), "Картинки");
  }

  /* ---- rows carry kind/provider attributes and kind badges ---- */
  {
    const { Sel, document } = makeContext();
    const host = document.createElement("div");
    const rows = [
      { id: "img-9", name: "Pic", kind: "image", provider: "openai", caps: { free: true }, context: 1000 },
      { id: "chat-9", name: "Chat", kind: "chat", provider: "openai", caps: { vision: true, reasoning: true, tools: true, code: true } },
    ];
    Sel.create(host, { preset: "full", models: rows, storageKey: "test:badges" });
    const imgRow = host.querySelector('[data-ms-model="img-9"]');
    assert.equal(imgRow.getAttribute("data-kind"), "image");
    assert.match(imgRow.getAttribute("style") || "", /--ms-prov-h:\d+/);
    assert.match(host.innerHTML, /Картинки/);
    assert.match(host.innerHTML, /бесплатно/);
    assert.match(host.innerHTML, /&lt;\/&gt;/);
  }

  /* ---- shared pins through a fake Memu bridge ---- */
  {
    const bridgeState = { pinned: ["openai-gpt-5"], calls: [] };
    const bridge = {
      pinnedModels: () => Promise.resolve({ pinned: bridgeState.pinned.slice() }),
      setModelPinned: (id, on) => {
        bridgeState.calls.push([id, on]);
        if (on) bridgeState.pinned = [id, ...bridgeState.pinned.filter((x) => x !== id)];
        else bridgeState.pinned = bridgeState.pinned.filter((x) => x !== id);
        return Promise.resolve({ pinned: bridgeState.pinned.slice() });
      },
    };
    const { Sel, document } = makeContext({ memu: bridge });
    const host = document.createElement("div");
    const api = Sel.create(host, { preset: "dropdown", models: ROWS });
    await tick(40);
    assert.ok(host.textContent.includes("Закреплённые"), "pinned section from the bridge");
    assert.deepEqual(j(Sel.getFavorites()), ["openai-gpt-5"]);
    // Star another model: the bridge is written, the cache follows.
    const fav = host.querySelector('[data-ms-fav="acme-gpt-5"]');
    assert.ok(fav, "star exists");
    fav.click();
    await tick(40);
    assert.deepEqual(j(bridgeState.calls[bridgeState.calls.length - 1]), ["acme-gpt-5", true]);
    assert.deepEqual(j(Sel.getFavorites()), ["acme-gpt-5", "openai-gpt-5"]);
    assert.ok(host.textContent.includes("Закреплённые"));
    // A second picker in the same document sees the same shared list.
    const host2 = document.createElement("div");
    Sel.create(host2, { preset: "compact", models: ROWS });
    await tick(40);
    assert.ok(host2.textContent.includes("Закреплённые"));
    // An explicit storageKey keeps its own list, apart from the shared one.
    Sel.setFavorite("zzz-local", true, { storageKey: "test:isolated" });
    assert.ok(j(Sel.getFavorites({ storageKey: "test:isolated" })).includes("zzz-local"));
    assert.ok(!j(Sel.getFavorites()).includes("zzz-local"));
    // Unpin through the second picker: the bridge drops it.
    const fav2 = host2.querySelector('[data-ms-fav="openai-gpt-5"]');
    assert.ok(fav2, "star in second picker");
    fav2.click();
    await tick(40);
    assert.ok(!j(Sel.getFavorites()).includes("openai-gpt-5"));
    assert.deepEqual(j(bridgeState.calls[bridgeState.calls.length - 1]), ["openai-gpt-5", false]);
    api.destroy();
  }

  /* ---- renderList: search box filters rows ---- */
  {
    const { Sel, document } = makeContext();
    const host = document.createElement("div");
    const rows = ROWS.concat([
      { id: "zzz-one", name: "Zed One", provider: "openai" },
      { id: "zzz-two", name: "Zed Two", provider: "openai" },
    ]);
    Sel.renderList(host, rows, { preset: "full", storageKey: "test:rlsearch" });
    const input = host.querySelector(".ms-search-input");
    assert.ok(input, "search box exists in renderList with >6 rows");
    input.value = "opus";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const visible = host.querySelectorAll("[data-ms-model]").map((el) => el.dataset.msModel);
    assert.ok(visible.includes("myconn-claude-opus") && visible.includes("genspark-claude-opus-5"));
    assert.ok(!visible.includes("zzz-one"));
  }

  console.log("model_selector_dom_test: OK");
}

main().catch((err) => { console.error(err); process.exit(1); });
