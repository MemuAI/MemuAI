

"use strict";
const fs = require("fs");
const path = require("path");
const { JSDOM, ResourceLoader, VirtualConsole } = require("jsdom");

const STATIC = path.resolve(__dirname, "..", "app", "pgstatic");
const problems = [];
const calls = [];
const LANG = process.argv[2] === "en" ? "en" : "ru";

const ctx = { id: "a1x", seq: 1, kind: "explorer", app: "Explorer", exe: "explorer.exe", title: "Downloads", folder: "C:\\Users\\me\\Downloads",
  folder_name: "Downloads", selected: ["report.pdf", "notes.txt", "img"], selected_count: 3, selected_dirs: 1, file: "", file_name: "", doc: null,
  writable: true, approx: false, reason: "", pending: false, color: { hex: "#3366CC", r: 51, g: 102, b: 204, x: 10, y: 20 } };
const changeSet = { id: "cs1", ctx: "a1x", folder_name: "Downloads", summary: "Rename by date", status: "pending", counts: { pending: 2 }, risky: 0, undo: "", mode: "ask",
  ops: [{ n: 0, op: "move", path: "a.txt", to: "2026-01-01 a.txt", status: "pending" }, { n: 1, op: "move", path: "b.txt", to: "2026-01-02 b.txt", status: "pending" }] };

function json(body, status = 200) {
  return { ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body), body: null };
}
function sseBody(events) {
  const enc = new TextEncoder();
  let i = 0;
  return { getReader: () => ({ read: async () => (i < events.length ? { done: false, value: enc.encode("data: " + JSON.stringify(events[i++]) + "\n\n") } : { done: true }) }) };
}
async function fakeFetch(url, init = {}) {
  const u = new URL(url, "http://x");
  const p = u.pathname;
  const body = init.body ? JSON.parse(init.body) : {};
  calls.push([p, body]);
  if (p === "/quick/plugins/index.json") return json(JSON.parse(fs.readFileSync(path.join(STATIC, "quick/plugins/index.json"), "utf8")));
  if (p === "/api/quick/config") return json({ prefs: { theme: "dark", language: LANG, quick_ask_model: "m1" }, status: { native: true, hotkey: { ok: true } }, ui: { onboarded: true }, endpoint: { source: "proxy" } });
  if (p === "/api/pg/state") return json({ models: [{ id: "m1", caps: { vision: true } }] });
  if (p === "/api/quick/apps/context") return json({ context: ctx, settings: { access: "ask", currency: true, color: true, ocr: true, error: true, notes: true, downloads: true, presets: true }, presets: [{ id: "p_1", name: "Preset X", prompt: "Do X", apps: [] }], native: true });
  if (p === "/api/quick/apps/settings") return json({ settings: { currency: true, color: true, notes: true, downloads: true, error: true, ocr: true }, presets: [], native: true, history: [] });
  if (p === "/api/quick/extras/rates") return json({ base: u.searchParams.get("base"), rates: { USD: 1, RUB: 90, EUR: 0.9 }, source: "test", t: 1790000000 });
  if (p === "/api/quick/explorer/duplicates") return json({ groups: [{ size: 12, files: ["a.txt", "copy/a.txt"] }], files_scanned: 5, wasted: 12, wasted_h: "12 Б", wasted_h_en: "12 B" });
  if (p === "/api/quick/explorer/changes") return json({ set: { ...changeSet, id: "cs2", ops: [{ n: 0, op: "delete", path: "copy/a.txt", status: "pending", risky: "delete" }], risky: 1 } });
  if (p.endsWith("/apply")) return json({ applied: 2, failed: 0, set: { ...changeSet, status: "applied", undo: "u20261009120000abcdef", ops: changeSet.ops.map((o) => ({ ...o, status: "applied" })) } });
  if (p === "/api/quick/ask") {
    return { ok: true, status: 200, body: sseBody([{ event: "tool_ui", tool: "folder_change", ui: { type: "changeset", set: changeSet } }, { event: "delta", text: "Proposed." }, { event: "done", model: "m1", tier: "fast" }]) };
  }
  if (p.startsWith("/api/")) return json({ items: [], ok: true });
  return json({}, 404);
}

class Loader extends ResourceLoader {
  fetch(url) {
    const u = new URL(url);
    const file = path.join(STATIC, decodeURIComponent(u.pathname));
    if (!file.startsWith(STATIC) || !fs.existsSync(file) || !/\.(js)$/.test(file)) return Promise.resolve(Buffer.from(""));
    return Promise.resolve(fs.readFileSync(file));
  }
}
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => { if (!/Could not parse CSS|Not implemented/.test(String(e.message))) problems.push("jsdomError: " + e.message); });
vc.on("error", (e) => problems.push("console.error: " + e));

const html = fs.readFileSync(path.join(STATIC, "quick", "index.html"), "utf8");
const dom = new JSDOM(html, {
  url: "http://127.0.0.1/quick/?native=1", runScripts: "dangerously", resources: new Loader(), virtualConsole: vc, pretendToBeVisual: true,
  beforeParse(win) {
    win.fetch = fakeFetch;
    win.TextDecoder = TextDecoder; win.TextEncoder = TextEncoder;
    win.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
    win.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
    win.IntersectionObserver = win.ResizeObserver;
    win.Element.prototype.scrollIntoView = function () {};
    win.navigator.clipboard = { writeText: async (t) => { win.__copied = t; } };
    win.addEventListener("error", (e) => problems.push("page error: " + (e.error && e.error.stack || e.message)));
    win.addEventListener("unhandledrejection", (e) => problems.push("unhandled: " + (e.reason && e.reason.stack || e.reason)));
  },
});
const win = dom.window;
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const check = (ok, what) => { if (!ok) problems.push(what); };

(async () => {
  for (let i = 0; i < 60 && !(win.QuickExplorer && win.QuickExtras); i++) await wait(50);
  check(!!win.QuickExplorer, "explorer plugin loaded");
  check(!!win.QuickExtras, "extras plugin loaded");
  if (!win.QuickExplorer) { finish(); return; }
  win.QuickAsk.onShow({ fresh: true, maxHeight: 640, source: "hotkey", page: null });
  await wait(300);
  const top = win.document.getElementById("qa-slot-top");
  const chipText = top.textContent;
  check(chipText.includes(LANG === "en" ? "Folder: Downloads" : "Папка: Downloads"), "chip shows the folder: " + chipText.slice(0, 120));
  check(chipText.includes(LANG === "en" ? "selected 2 files and 1 folder" : "выбрано 2 файла и 1 папка"), "chip counts the selection");
  check(chipText.includes("Preset X"), "the per-app preset is offered");
  check(top.querySelectorAll(".qe-act").length >= 8, "one-click actions are shown");
  if (LANG === "en") check(!/[А-Яа-яЁё]/.test(chipText), "no Cyrillic in the English chip: " + chipText);

  win.document.getElementById("qa-input").value = "rename by date";
  win.MemuQuick.send(null, {});
  await wait(400);
  const ask = calls.find((c) => c[0] === "/api/quick/ask");
  check(ask && JSON.stringify(ask[1].contexts) === JSON.stringify([{ type: "app", id: "a1x" }]), "the question carries the app context");
  const bottom = win.document.getElementById("qa-slot-bottom");
  check(!!bottom.querySelector(".qe-card .qe-table"), "the rename change set is a table card");
  check(bottom.textContent.includes("2026-01-01 a.txt"), "the table shows the new name");
  bottom.querySelector("[data-qe-apply]").click();
  await wait(200);
  check(calls.some((c) => c[0] === "/api/quick/explorer/changes/cs1/apply"), "Apply posts to the set");
  check(!!bottom.querySelector("[data-qe-undo]"), "Undo is offered after applying");

  win.MemuQuick.newChat();
  await wait(100);
  const detach = top.querySelector('[data-qe="detach"]');
  check(!!detach, "the chip can be detached");
  if (detach) detach.click();
  win.document.getElementById("qa-input").value = "plain question";
  calls.length = 0;
  win.MemuQuick.send(null, {});
  await wait(200);
  const ask2 = calls.find((c) => c[0] === "/api/quick/ask");
  check(ask2 && Array.isArray(ask2[1].contexts) && ask2[1].contexts.length === 0, "a detached chip sends no context");

  top.querySelector('[data-qe="attach"]') && top.querySelector('[data-qe="attach"]').click();
  win.QuickExplorer.openView("dups");
  await wait(200);
  check(win.document.documentElement.dataset.qeView === "dups", "the duplicates view is open");
  check(top.querySelectorAll(".qe-dup").length === 2, "duplicates are listed");
  top.querySelector('[data-qe="dups-delete"]').click();
  await wait(200);
  check(bottom.textContent.includes("copy/a.txt"), "deleting extras makes a change card");

  const input = win.document.getElementById("qa-input");
  input.value = "100 usd в rub";
  input.dispatchEvent(new win.Event("input"));
  await wait(200);
  check(/9[\s\u00a0\u202f,]?000/.test(top.textContent), "currency converts while typing: " + top.textContent.slice(0, 200));

  win.QuickExtras.openView("color");
  await wait(100);
  check(top.textContent.includes("#3366CC"), "the eyedropper shows the colour under the pointer");
  finish();
})().catch((e) => { problems.push("harness: " + (e.stack || e)); finish(); });

function finish() {
  if (problems.length) { console.log(problems.join("\n")); process.exit(1); }
  console.log("OK");
  process.exit(0);
}
