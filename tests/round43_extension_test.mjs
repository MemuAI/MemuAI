/* Actual extension functions with Chrome/DOM mocks; no Chrome or desktop control. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import vm from "node:vm";
import { createRequire } from "node:module";
const require = createRequire(new URL("../build/audit-js/package.json", import.meta.url));
const { JSDOM } = require("jsdom");
const root = new URL("../app/pgstatic/browser-extension/", import.meta.url);
const pageSource = await fs.readFile(new URL("page.js", root), "utf8");
const workerSource = await fs.readFile(new URL("worker.js", root), "utf8");
const panelSource = await fs.readFile(new URL("panel.js", root), "utf8");
const panelHtml = await fs.readFile(new URL("panel.html", root), "utf8");
const uiSource = await fs.readFile(new URL("ui.js", root), "utf8");
const markdownSource = await fs.readFile(new URL("markdown.js", root), "utf8");
let count = 0;
async function test(name, body) { await body(); count++; process.stdout.write(`PASS ${name}\n`); }
const tick = () => new Promise((resolve) => setImmediate(resolve));
function dom(html) {
  const document = new JSDOM(html, { url: "https://example.com", runScripts: "outside-only" });
  const win = document.window;
  Object.defineProperty(win.HTMLElement.prototype, "innerText", { get() { return this.textContent; } });
  win.HTMLElement.prototype.getBoundingClientRect = () => ({ x: 0, y: 0, width: 100, height: 30 });
  vm.runInContext(pageSource.replace("export function", "function") + "\nglobalThis.operation = pageOperation;", document.getInternalVMContext());
  return document;
}
function emitter() { const listeners = []; return { addListener(fn) { listeners.push(fn); }, emit(...args) { return listeners.map((fn) => fn(...args)); } }; }
async function worker(savedTabs = []) {
  const records = { removed: [], detached: [], commands: [], created: [], queries: [], sent: [] };
  const config = { base: "http://127.0.0.1:8600", token: "" }, storage = { dfyTabs: savedTabs };
  const tabs = new Map([[1, { id: 1, url: "https://example.com", title: "User tab", windowId: 2, active: true }]]);
  let nextId = 100;
  const chrome = {
    runtime: { id: "a".repeat(32), getURL: (p) => "chrome-extension://" + "a".repeat(32) + "/" + p,
      onMessage: emitter(), onInstalled: emitter(), onStartup: emitter() },
    storage: { local: { get: async (defaults) => ({ ...defaults, ...config }) },
      session: { get: async (defaults) => ({ ...defaults, ...storage }), set: async (rows) => Object.assign(storage, rows) }, onChanged: emitter() },
    tabs: { query: async (args) => { records.queries.push(args); return [...tabs.values()].filter((t) => !args.active || t.active); },
      get: async (id) => { if (!tabs.has(id)) throw new Error("missing tab"); return tabs.get(id); },
      create: async (args) => { const row = { id: nextId++, windowId: 2, ...args }; records.created.push(row); tabs.set(row.id, row); return row; },
      remove: async (id) => { records.removed.push(id); for(const value of Array.isArray(id)?id:[id])tabs.delete(value); }, update: async (id, args) => Object.assign(tabs.get(id), args),
      captureVisibleTab: async () => "data:image/jpeg;base64,YWJj", onActivated: emitter(), onUpdated: emitter() },
    debugger: { attach: async () => {}, detach: async ({ tabId }) => records.detached.push(tabId),
      sendCommand: async (target, method, params) => { records.commands.push({ target, method, params }); return {}; },
      onDetach: emitter(), onEvent: emitter() },
    scripting: { executeScript: async () => [ { frameId: 0, result: { text: "a".repeat(1500), elements: [{ id: 2, text: "Main" }] } },
      { frameId: 4, result: { url: "https://frame.example.com", text: "b".repeat(1500), elements: [{ id: 3, text: "Frame" }] } } ] },
    sidePanel: { setPanelBehavior() {} }, alarms: { create() {}, onAlarm: emitter() }, windows: { onFocusChanged: emitter() }
  };
  class Socket {
    static OPEN = 1; static CONNECTING = 0;
    constructor() { this.readyState = 0; Socket.instances.push(this); }
    send(data) { records.sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; queueMicrotask(() => this.onclose?.({ code: 1000 })); }
    static instances = [];
  }
  const context = vm.createContext({ chrome, WebSocket: Socket, URL, console, setTimeout: () => 1, clearTimeout() {},
    setInterval: () => 2, clearInterval() {}, pageOperation() {}, fetch: async () => { throw new Error("unexpected HTTP"); } });
  const source = workerSource.replace(/^import[^\n]+\n/, "").replace("export function localBase", "function localBase");
  vm.runInContext(source + "\nglobalThis.unit = { execute, read, connect, localBase };", context);
  await tick(); await tick();
  return { unit: context.unit, chrome, records, config, storage, tabs, Socket };
}
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function linkedWorker() {
  const value = await worker();
  value.config.token = "token"; await value.unit.connect();
  value.socket = value.Socket.instances.at(-1);
  value.socket.readyState = value.Socket.OPEN;
  value.socket.onopen();
  value.command = (data) => value.socket.onmessage({ data: JSON.stringify(data) });
  return value;
}
const jsonResponse = (value) => ({ ok: true, status: 200, json: async () => value });
async function panel() {
  const fixture = new JSDOM(panelHtml, { url: "chrome-extension://" + "a".repeat(32) + "/panel.html", runScripts: "outside-only", pretendToBeVisual:true });
  const win = fixture.window;
  const changes = emitter();
  win.setInterval = () => 1; win.clearInterval = () => {};
  win.AbortSignal = AbortSignal;
  win.HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open",""); };
  win.HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  win.chrome = { runtime: { id: "a".repeat(32), openOptionsPage() {}, sendMessage:async()=>({ok:true}) },
    storage: { local: { get: async (defaults) => ({ ...defaults, token: "paired", model: "m" }), set: async () => {} }, onChanged: changes },
    tabs: { query: async () => [{ id: 1, title: "Current page" }] } };
  win.fetch = async (url) => jsonResponse(url.includes("/models") ? { models: [{ id: "m", name: "Model" }] } :
    { chats: [{ id: "A", title: "Chat A" }, { id: "B", title: "Chat B" }], profile_id:"default" });
  const helpers=uiSource.replace(/^export /gm,"")+"\n"+markdownSource.replace(/^import[^\n]+\n/gm,"").replace(/^export /gm,"");
  const source=panelSource.replace(/^import[^\n]+\n/gm,"");
  await vm.runInContext("(async () => {\n" + helpers + "\n" + source + "\nglobalThis.panelUnit = { config: () => ({ ...cfg }), openChat, refresh, setController: (value) => { controller = value; } };\n})()", fixture.getInternalVMContext());
  await tick(); await tick();
  return { fixture, win, changes };
}

await test("DOM read hides passwords and keeps stable action references", () => {
  const fixture = dom('<main>Hello<input id="secret" type="password" value="do-not-export"><input id="input" value="old"><button>Save</button></main>');
  const win = fixture.window, result = win.operation("read", {});
  assert(!JSON.stringify(result).includes("do-not-export"));
  assert.equal(result.elements.find((e) => e.type === "password").value, "[hidden]");
  const input = result.elements.find((e) => e.type === "text");
  let events = 0; win.document.getElementById("input").addEventListener("input", () => events++);
  win.operation("action", { action: "type", element: input.id, text: "new value" });
  assert.equal(win.document.getElementById("input").value, "new value"); assert.equal(events, 1);
  assert.throws(() => win.operation("action", { action: "type", element: result.elements[0].id, text: "unsafe" }));
  win.operation("read", {});
  assert.throws(() => win.operation("action", { action: "type", element: input.id, text: "stale" }));
  fixture.window.close();
});
await test("DOM reads shadow roots and selects by visible option", () => {
  const fixture = dom('<main><select id="select"><option value="1">One</option><option value="2">Two</option></select><div id="host"></div></main>');
  const win = fixture.window;
  win.document.getElementById("host").attachShadow({ mode: "open" }).innerHTML = "<button>Shadow button</button>";
  const result = win.operation("read", {});
  assert(result.elements.some((e) => e.text === "Shadow button"));
  const select = result.elements.find((e) => e.tag === "select");
  win.operation("action", { action: "select", element: select.id, value: "Two" });
  assert.equal(win.document.getElementById("select").value, "2");
  fixture.window.close();
});
await test("worker rejects nonloopback bridge URLs", async () => {
  const { unit } = await worker();
  for (const url of ["https://example.com", "http://127.0.0.1.evil.invalid", "http://user:pass@localhost", "file:///tmp/x"]) assert.throws(() => unit.localBase(url));
  assert.equal(unit.localBase("http://127.0.0.1:8600/a"), "http://127.0.0.1:8600");
});
await test("worker combines iframe IDs and respects read budget", async () => {
  const { unit } = await worker(); const result = await unit.read({ tab_id: 1, limit: 1000 });
  assert(result.elements.some((e) => e.id === "0:2")); assert(result.elements.some((e) => e.id === "4:3"));
  assert.equal(result.text.length, 1000); assert.equal(result.truncated, true);
});
await test("batch close validates all tabs then issues one native close with readable results", async () => {
  const {unit,tabs,records}=await worker();
  tabs.set(2,{id:2,title:"GitHub · Issues",url:"https://github.com/memu/issues",windowId:2});
  const result=await unit.execute("close_tabs",{tab_ids:[1,2,1]});
  assert.equal(records.removed.length,1);
  assert.deepEqual(Array.from(records.removed[0]),[1,2]);
  assert.equal(result.count,2);assert.equal(result.closed[1].title,"GitHub · Issues");
  assert.equal(result.closed[1].url,"https://github.com/memu/issues");
  assert.equal(tabs.size,0);
});
await test("batch close rejects incognito, missing and malformed IDs before any mutation", async () => {
  const {unit,tabs,records}=await worker();
  tabs.set(2,{id:2,title:"Private",url:"https://example.com",incognito:true});
  for(const ids of [[1,2],[1,999],[],["1"],[1,null],[-1]])await assert.rejects(unit.execute("close_tabs",{tab_ids:ids}));
  assert.equal(records.removed.length,0);assert(tabs.has(1));
});
await test("batch close reports actual partial failure instead of claiming every tab closed", async () => {
  const {unit,tabs,chrome}=await worker();tabs.set(2,{id:2,title:"Remaining",url:"https://example.com/2"});
  chrome.tabs.remove=async()=>{tabs.delete(1);throw new Error("Browser closed only the first tab");};
  const result=await unit.execute("close_tabs",{tab_ids:[1,2]});
  assert.equal(result.ok,false);assert.equal(result.closed.length,1);assert.equal(result.closed[0].tab_id,1);
  assert.deepEqual(Array.from(result.remaining),[2]);assert(tabs.has(2));
});
await test("close revalidates the exact URLs shown in approval immediately before native mutation", async () => {
  const {unit,tabs,records}=await worker();tabs.set(2,{id:2,url:"https://github.com/memu",title:"GitHub"});
  await assert.rejects(unit.execute("close_tabs",{tab_ids:[1,2],_expected_tabs:[{tab_id:1,url:"https://example.com"},{tab_id:2,url:"https://github.com/previous"}]}));
  await assert.rejects(unit.execute("close",{tab_id:1,_expected_tabs:[{tab_id:1,url:"https://example.com/old"}]}));
  for(const expected of [[],null,[{tab_id:1}], [{tab_id:1,url:"https://example.com"},{tab_id:1,url:"https://example.com"}]])await assert.rejects(unit.execute("close_tabs",{tab_ids:[1,2],_expected_tabs:expected}));
  assert.equal(records.removed.length,0);
  const result=await unit.execute("close_tabs",{tab_ids:[1,2],_expected_tabs:[{tab_id:1,url:"https://example.com"},{tab_id:2,url:"https://github.com/memu"}]});
  assert.equal(result.ok,true);assert.equal(records.removed.length,1);
});
await test("DFY opens in background and cannot target the user's existing tab", async () => {
  const { unit, records, chrome, config, Socket } = await worker();
  config.token = "token"; await unit.connect();
  const socket = Socket.instances.at(-1); socket.readyState = Socket.OPEN; socket.onopen();
  const tab = await unit.execute("dfy_start", {});
  assert.equal(records.created[0].active, false);
  await assert.rejects(unit.execute("cdp", { tab_id: 1, method: "Runtime.evaluate", params: {} }));
  await unit.execute("cdp", { tab_id: tab.tab_id, method: "Page.addScriptToEvaluateOnNewDocument", params: { source: "void 0" } });
  chrome.debugger.onEvent.emit({ tabId: tab.tab_id }, "Page.javascriptDialogOpening", { type: "confirm" });
  await tick();
  assert(records.commands.some((c) => c.method === "Page.handleJavaScriptDialog" && c.params.accept === false));
  await unit.execute("dfy_stop", { tab_id: tab.tab_id });
  assert.deepEqual(records.removed, [tab.tab_id]);
});
await test("worker startup removes only its previously recorded DFY tabs", async () => {
  const { records, storage } = await worker([777]);
  assert.deepEqual(records.removed, [777]); assert.equal(storage.dfyTabs.length, 0);
});
await test("natural disconnect closes owned DFY tabs while keeping user tabs", async () => {
  const { unit, records, config, Socket, storage } = await worker();
  config.token = "token"; await unit.connect();
  const socket = Socket.instances.at(-1); socket.readyState = Socket.OPEN; socket.onopen();
  const tab = await unit.execute("dfy_start", {});
  socket.close(); await tick(); await tick();
  assert.deepEqual(records.removed, [tab.tab_id]); assert.equal(storage.dfyTabs.length, 0);
});
await test("explicit token removal also closes its DFY tabs", async () => {
  const { unit, records, config, Socket, chrome, storage } = await worker();
  config.token = "token"; await unit.connect();
  const socket = Socket.instances.at(-1); socket.readyState = Socket.OPEN; socket.onopen();
  const tab = await unit.execute("dfy_start", {});
  config.token = "";
  chrome.storage.onChanged.emit({ token: { oldValue: "token", newValue: undefined } }, "local");
  await tick(); await tick();
  assert.deepEqual(records.removed, [tab.tab_id]); assert.equal(storage.dfyTabs.length, 0);
});
await test("opening the panel keeps the existing connection and running DFY tabs", async () => {
  const value=await linkedWorker();
  const tab=await value.unit.execute("dfy_start",{}), sockets=value.Socket.instances.length;
  for(let attempt=0;attempt<2;attempt++){
    const reply=await new Promise(resolve=>value.chrome.runtime.onMessage.emit({op:"connect"},
      {id:value.chrome.runtime.id,url:value.chrome.runtime.getURL("panel.html")},resolve));
    assert.equal(reply.value.ok,true);
  }
  assert.equal(value.Socket.instances.length,sockets);
  assert.equal(value.socket.readyState,value.Socket.OPEN);
  assert.deepEqual(value.records.removed,[]);
  assert(!value.records.detached.includes(tab.tab_id));
  assert(value.storage.dfyTabs.includes(tab.tab_id));
});
await test("worker rejects private IPv6 and mapped IPv4 browser operations", async () => {
  const { unit, records } = await worker();
  for (const address of ["::1", "::", "fc00::1", "fd12:3456::1", "fe80::1", "febf::1", "::ffff:127.0.0.1", "0:0:0:0:0:ffff:a00:1"]) {
    await assert.rejects(unit.execute("open", { url: `http://[${address}]/` }), undefined, address);
  }
  assert.equal(records.created.length, 0);
  await unit.execute("open", { url: "https://[2606:4700:4700::1111]/" });
  assert.equal(records.created.length, 1);
});
await test("cancelling DFY while tabs.create is pending closes the eventual tab", async () => {
  const value = await linkedWorker(), gate = deferred();
  const create = value.chrome.tabs.create;
  value.chrome.tabs.create = async (args) => { await gate.promise; return create(args); };
  const pending = value.command({ id: "late-create", op: "dfy_start", args: {} });
  await tick();
  await value.command({ op: "cancel", cancel_id: "late-create" });
  gate.resolve(); await pending; await tick();
  assert.equal(value.records.created.length, 1);
  const id = value.records.created[0].id;
  assert(value.records.removed.includes(id));
  assert.equal(value.storage.dfyTabs.length, 0);
  assert(value.records.sent.some((row) => row.id === "late-create" && row.error));
  assert(!value.records.commands.some((row) => row.method === "Page.enable"));
});
await test("cancelling DFY during debugger.attach never reports successful startup", async () => {
  const value = await linkedWorker(), gate = deferred(), entered = deferred();
  value.chrome.debugger.attach = async () => { entered.resolve(); await gate.promise; };
  const pending = value.command({ id: "late-attach", op: "dfy_start", args: {} });
  await entered.promise;
  await value.command({ op: "cancel", cancel_id: "late-attach" });
  gate.resolve(); await pending; await tick();
  assert.equal(value.storage.dfyTabs.length, 0);
  assert(value.records.removed.includes(value.records.created[0].id));
  assert(value.records.sent.some((row) => row.id === "late-attach" && row.error));
  assert(!value.records.commands.some((row) => row.method === "Page.enable"));
});
await test("panel token removal clears credentials and aborts active work", async () => {
  const { fixture, win, changes } = await panel();
  let stopped = 0;
  win.panelUnit.setController({ abort() { stopped++; } });
  win.document.getElementById("messages").textContent = "old private history";
  changes.emit({ token: { oldValue: "paired", newValue: undefined } }, "local");
  assert.equal(win.panelUnit.config().token, "");
  assert.equal(stopped, 1);
  assert(!win.document.getElementById("messages").textContent.includes("old private history"));
  assert.equal(win.document.querySelectorAll("#messages .message").length,0);
  assert.equal(win.document.querySelectorAll("#model-list .model-row").length,0);
  fixture.window.close();
});
await test("panel ignores an older chat response after switching chats", async () => {
  const { fixture, win } = await panel(), first = deferred(), second = deferred();
  win.fetch = (url) => url.includes("id=A") ? first.promise : second.promise;
  const older=win.panelUnit.openChat("A"), newer=win.panelUnit.openChat("B");
  second.resolve(jsonResponse({ chat: { id:"B", messages: [{ role: "user", content: "new chat content" }] } }));
  await newer;
  first.resolve(jsonResponse({ chat: { id:"A", messages: [{ role: "user", content: "stale chat content" }] } }));
  await older;
  const content = win.document.getElementById("messages").textContent;
  assert(content.includes("new chat content")); assert(!content.includes("stale chat content"));
  fixture.window.close();
});
await test("late catalogue refresh cannot restore private history after disconnect", async () => {
  const {fixture,win,changes}=await panel(), models=deferred(), chats=deferred();
  win.fetch=url=>url.includes("/models")?models.promise:chats.promise;
  const pending=win.panelUnit.refresh();
  changes.emit({token:{oldValue:"paired",newValue:undefined}},"local");
  models.resolve(jsonResponse({models:[{id:"private-model",name:"Old private model"}]}));
  chats.resolve(jsonResponse({chats:[{id:"private-chat",title:"Old private conversation"}],profile_id:"old"}));
  await pending;await tick();
  assert.equal(win.panelUnit.config().token,"");
  assert.equal(win.document.querySelectorAll("#model-list .model-row").length,0);
  assert.equal(win.document.querySelectorAll("#chat-list .chat-row").length,0);
  assert(!win.document.body.textContent.includes("Old private"));
  fixture.window.close();
});
process.stdout.write(`${count} extension JS checks passed\n`);
