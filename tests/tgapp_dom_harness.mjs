// jsdom harness for the Telegram Mini App (music + panels).
// Runs the REAL tgapp JS (app.js / music.js / panels.js / md.js / device.js)
// inside jsdom with fixture data, real timers and synthetic clicks.
// Live-browser note: /opt/meta-chromium/chrome in this VM refuses standalone
// startup ("Hatch WebGL domain policy is required for browser startup"), so
// layout / hit-testing cannot be checked here; the scenarios assert DOM state,
// stacking (z-index), timers, storage and call logs instead, and the visual /
// hit-test pass is left to a browser check by another agent.
// jsdom resolution: JSDOM_DIR env, /tmp/tgdom, or a node_modules next to the repo.
//   npm install jsdom   (in /tmp/tgdom, or set JSDOM_DIR=<dir with node_modules>)
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const TGAPP = path.join(ROOT, "app", "tgapp");

function loadJsdom() {
  const bases = [process.env.JSDOM_DIR, "/tmp/tgdom", path.join(ROOT, "node_modules"), HERE].filter(Boolean);
  for (const base of bases) {
    try {
      const req = createRequire(path.join(base, "x.js"));
      const mod = req("jsdom");
      return mod;
    } catch { /* try next */ }
  }
  return null;
}

const jsdomMod = loadJsdom();
if (!jsdomMod) {
  console.log(JSON.stringify([{ name: "jsdom", pass: true, skipped: true, details: "jsdom not installed; run: mkdir -p /tmp/tgdom && cd /tmp/tgdom && npm init -y && npm install jsdom (or set JSDOM_DIR)" }]));
  process.exit(0);
}
const { JSDOM, requestInterceptor, VirtualConsole } = jsdomMod;

const CSS_FILES = ["app.css", "layout.css", "music.css"];
const html = `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<link rel="stylesheet" href="/tg/app.css"><link rel="stylesheet" href="/tg/layout.css"><link rel="stylesheet" href="/tg/music.css">
</head><body>
<div id="boot" class="boot"><div class="boot-sub" id="boot-sub"></div></div>
<div id="app" class="app" hidden>
<header class="topbar" id="topbar"><div class="topbar-inner"><button class="icon-btn" id="top-back" hidden aria-label="Back"></button><div class="top-title" id="top-title"></div><div class="top-actions" id="top-actions"></div></div></header>
<main class="views" id="views"></main><nav class="tabbar" id="tabbar" role="tablist"></nav></div>
<div id="sheet-root"></div><div id="toast-root" class="toast-root" aria-live="polite"></div>
</body></html>`;

const jsdomErrors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => { const msg = String(e && e.detail || e); if (!/Could not parse CSS|Not implemented: HTMLCanvasElement/.test(msg)) jsdomErrors.push(msg); });

const dom = new JSDOM(html, {
  url: "http://127.0.0.1/",
  pretendToBeVisual: true,
  runScripts: "outside-only",
  resources: {
    interceptors: [requestInterceptor(async (request) => {
      const m = /\/tg\/([^/?#]+)$/.exec(request.url);
      if (m && CSS_FILES.includes(m[1])) {
        const css = await readFile(path.join(TGAPP, m[1]), "utf8");
        return new Response(css, { headers: { "Content-Type": "text/css" } });
      }
      return undefined;
    })],
  },
  virtualConsole: vc,
  beforeParse(window) {
    // jsdom has no CSS.escape. Escape punctuation in fixture IDs used in quoted selectors.
    window.CSS ??= { escape: (value) => String(value).replace(/[^a-z0-9_-]/gi, (ch) => "\\" + ch) };
    window.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, onchange: null, dispatchEvent: () => false });
    const HME = window.HTMLMediaElement;
    Object.defineProperty(HME.prototype, "currentTime", { configurable: true, get() { return this._ct || 0; }, set(v) { this._ct = +v || 0; } });
    Object.defineProperty(HME.prototype, "duration", { configurable: true, get() { return this._dur != null ? this._dur : NaN; } });
    Object.defineProperty(HME.prototype, "paused", { configurable: true, get() { return !this._playing; } });
    Object.defineProperty(HME.prototype, "volume", { configurable: true, get() { return this._vol != null ? this._vol : 1; }, set(v) { this._vol = Math.max(0, Math.min(1, +v || 0)); } });
    HME.prototype.play = function () { this._playing = true; this.dispatchEvent(new window.Event("play")); this.dispatchEvent(new window.Event("playing")); return Promise.resolve(); };
    HME.prototype.pause = function () { this._playing = false; this.dispatchEvent(new window.Event("pause")); };
    if (typeof window.DragEvent === "undefined") {
      window.DragEvent = class extends window.Event {
        constructor(type, init = {}) { super(type, init); this.dataTransfer = init.dataTransfer || null; this.clientX = init.clientX || 0; this.clientY = init.clientY || 0; }
      };
    }
    if (typeof window.DataTransfer === "undefined") {
      window.DataTransfer = class { constructor() { this._data = {}; this.dropEffect = "none"; this.effectAllowed = "all"; this.files = []; this.items = []; this.types = []; } setData(k, v) { this._data[k] = String(v); } getData(k) { return this._data[k] || ""; } clearData() { this._data = {}; } };
    }
    if (window.Element && !window.Element.prototype.scrollTo) {
      window.Element.prototype.scrollTo = function (opts) { if (opts && typeof opts.top === "number") this.scrollTop = opts.top; };
    }
  },
});

const { window } = dom;
const read = (p) => readFile(p, "utf8");
const cssText = (await Promise.all(CSS_FILES.map((f) => read(path.join(TGAPP, f))))).join("\n");
window.__cssText = cssText;
const prelude = await read(path.join(HERE, "tgapp_dom_prelude.js"));
const scripts = ["md.js", "device.js", "panels.js", "music.js", "app.js"];
const bodies = await Promise.all(scripts.map((f) => read(path.join(TGAPP, f))));
const scenarios = await read(path.join(HERE, "tgapp_dom_scenarios.js"));
try {
  window.eval(prelude + "\n;\n" + bodies.join("\n;\n") + "\n;\n" + scenarios);
} catch (e) {
  console.log(JSON.stringify([{ name: "harness: script load", pass: false, details: String((e && e.stack) || e) }]));
  process.exit(2);
}
await new Promise((r) => setTimeout(r, 600));
let results;
try {
  results = await window.__runAll();
} catch (e) {
  results = [{ name: "harness: run", pass: false, details: String((e && e.stack) || e) }];
}
if (jsdomErrors.length) results.push({ name: "jsdom errors", pass: false, details: jsdomErrors.slice(0, 5).join(" ;; ") });
console.log(JSON.stringify(results, null, 2));
process.exit(results.some((r) => !r.pass && !r.skipped) ? 1 : 0);
