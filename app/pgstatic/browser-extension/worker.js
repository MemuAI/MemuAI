import { pageOperation } from "./page.js";

const ROOT = "/api/pg/browser-extension";
let socket = null, reconnect = null, keepalive = null;
let connecting = false;
const ownedTabs = new Set();
const attached = new Set();
const dfyStarts = new Map(), cancelled = new Set();
const cdpAllowed = new Set(["Runtime.evaluate", "Page.navigate", "Page.captureScreenshot", "Page.getNavigationHistory", "Page.navigateToHistoryEntry",
  "Page.enable", "Page.addScriptToEvaluateOnNewDocument", "Page.setDownloadBehavior", "Input.insertText", "Input.dispatchKeyEvent", "Input.dispatchMouseEvent", "Page.handleJavaScriptDialog"]);

export function localBase(value) {
  const url = new URL(String(value || "http://127.0.0.1:8600"));
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.username || url.password) throw new Error("Нужен локальный HTTP-адрес MemuAI");
  return url.origin;
}

async function configuration() {
  return chrome.storage.local.get({ base: "http://127.0.0.1:8600", token: "" });
}

async function api(path, options = {}) {
  const cfg = await configuration();
  const response = await fetch(localBase(cfg.base) + ROOT + "/bridge/" + path, { ...options, headers: {
    "Content-Type": "application/json", "X-MemuAI-Extension": chrome.runtime.id,
    "Authorization": "Bearer " + cfg.token, ...(options.headers || {}) } });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || `MemuAI HTTP ${response.status}`);
  }
  return response;
}

function send(value) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); }
async function metadata() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab && !tab.incognito) send({ op: "active_tab", value: { tab_id: tab.id, url: tab.url || "", title: tab.title || "" } });
  } catch (_) {  }
}

async function connect() {
  if (connecting || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  connecting = true;
  try {
    const cfg = await configuration();
    if (!cfg.token) return;
    const ws = new WebSocket(localBase(cfg.base).replace(/^http:/, "ws:") + ROOT + "/socket");
    socket = ws;
    ws.onopen = () => ws.send(JSON.stringify({ token: cfg.token }));
    ws.onmessage = async ({ data }) => {
      let command;
      try {
        command = JSON.parse(data);
        if (command.op === "ready") { metadata(); return; }
        if (command.op === "pong") return;
        if (command.op === "cancel" && command.cancel_id) {
          cancelled.add(command.cancel_id);
          if (cancelled.size > 1000) cancelled.delete(cancelled.values().next().value);
          const tab = dfyStarts.get(command.cancel_id);
          if (tab !== undefined) await execute("dfy_stop", { tab_id: tab });
          return;
        }
        if (!command.id) return;
        const value = await execute(command.op, command.args || {}, command.id);
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ id: command.id, value }));
      } catch (error) {
        if (command?.id && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ id: command.id, error: error.message || String(error) }));
      }
    };
    ws.onclose = (event) => {
      if (socket !== ws) return;
      socket = null; clearInterval(keepalive); keepalive = null;

      if (event.code !== 1008) { clearTimeout(reconnect); reconnect = setTimeout(connect, 5000); }
      cleanupTabs();
    };
    ws.onerror = () => ws.close();
    clearInterval(keepalive);
    keepalive = setInterval(() => { send({ op: "ping" }); metadata(); }, 20000);
  } finally { connecting = false; }
}

async function tabFor(args) {
  let tab;
  if (Number.isInteger(Number(args.tab_id)) && args.tab_id !== null && args.tab_id !== undefined && args.tab_id !== "") tab = await chrome.tabs.get(Number(args.tab_id));
  else [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || tab.incognito) throw new Error("Вкладка недоступна");
  return tab;
}
function validateExpectedTabs(tabs, expected) {
  if (expected === undefined) return;
  if (!Array.isArray(expected) || expected.length !== tabs.length || expected.some(row => !row || !Number.isSafeInteger(row.tab_id) || typeof row.url !== "string")) throw new Error("Список вкладок изменился. Повторите действие");
  const urls = new Map(expected.map(row => [row.tab_id, row.url]));
  if (urls.size !== tabs.length || tabs.some(tab => !urls.has(tab.id) || urls.get(tab.id) !== String(tab.url || ""))) throw new Error("Вкладка перешла на другую страницу. Требуется новое подтверждение");
}
function webUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Доступны только HTTP(S)-страницы");
  const host = url.hostname.toLowerCase();
  if (host === "localhost" || host.endsWith(".localhost") || host === "[::1]" || /^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host) || /^169\.254\./.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) throw new Error("Локальные служебные адреса недоступны агенту");
  if (host.startsWith("[")) {
    const address = host.slice(1, -1);
    if (address === "::" || /^(?:fc|fd|fe[89ab])/.test(address) || address.startsWith("::ffff:")) throw new Error("Локальные IPv6-адреса недоступны агенту");
  } else if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
    const parts = host.split(".").map(Number);
    if (parts[0] === 0 || parts[0] >= 224 || (parts[0] === 100 && parts[1] >= 64 && parts[1] <= 127) || (parts[0] === 198 && [18, 19].includes(parts[1]))) throw new Error("Служебные IP-адреса недоступны агенту");
  }
  return url.href;
}
async function read(args) {
  const tab = await tabFor(args);
  webUrl(tab.url);
  const frames = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: pageOperation, args: ["read", args] });
  const main = frames.find((f) => f.frameId === 0)?.result || {};
  let budget = Math.max(1000, Math.min(60000, Number(args.limit) || 24000));
  const sections = [], elements = [];
  for (const frame of frames) {
    if (!frame.result) continue;
    const part = frame.result;
    const text = String(part.text || "").slice(0, budget);
    budget -= text.length;
    if (text) sections.push(frame.frameId ? `[Frame ${frame.frameId}: ${part.url}]\n${text}` : text);
    for (const e of part.elements || []) if (elements.length < 300) elements.push({ ...e, id: `${frame.frameId}:${e.id}` });
  }
  return { ...main, tab_id: tab.id, text: sections.join("\n\n"), elements, frames: frames.length,
    truncated: frames.some((f) => f.result?.truncated) || budget <= 0 };
}

async function execute(op, args, callId = "") {
  if (op === "tabs") return { tabs: (await chrome.tabs.query({})).filter((t) => !t.incognito).map((t) => ({ tab_id: t.id, title: t.title, url: t.url, active: t.active, window_id: t.windowId, owned: ownedTabs.has(t.id) })) };
  if (op === "read") return read(args);
  if (op === "open") {
    const tab = await chrome.tabs.create({ url: webUrl(args.url), active: args.active === true });
    return { tab_id: tab.id, url: tab.url || args.url };
  }
  if (op === "navigate") {
    const tab = await tabFor(args);
    await chrome.tabs.update(tab.id, { url: webUrl(args.url) });
    return { tab_id: tab.id, url: args.url };
  }
  if (op === "close") {
    if (args.tab_id === null || args.tab_id === undefined) throw new Error("Укажите tab_id для закрытия");
    const tab = await tabFor(args);
    validateExpectedTabs([tab], args._expected_tabs);
    await chrome.tabs.remove(tab.id); ownedTabs.delete(tab.id); attached.delete(tab.id);
    return { ok: true, tab_id: tab.id };
  }
  if (op === "close_tabs") {
    if (!Array.isArray(args.tab_ids) || !args.tab_ids.length || args.tab_ids.length > 1000 || args.tab_ids.some(id => !Number.isSafeInteger(id) || id < 0)) throw new Error("Укажите список tab_ids для закрытия");
    const ids = [...new Set(args.tab_ids)];

    const tabs = await Promise.all(ids.map(id => tabFor({ tab_id: id })));
    validateExpectedTabs(tabs, args._expected_tabs);
    try { await chrome.tabs.remove(ids); }
    catch (error) {
      const remaining = await chrome.tabs.query({});
      const present = new Set(remaining.map(tab => tab.id));
      const closed = tabs.filter(tab => !present.has(tab.id));
      for (const tab of closed) { ownedTabs.delete(tab.id); attached.delete(tab.id); }
      return { ok: false, error: error.message || String(error), closed: closed.map(tab => ({ tab_id: tab.id, title: tab.title || "", url: tab.url || "" })), remaining: ids.filter(id => present.has(id)) };
    }
    for (const id of ids) { ownedTabs.delete(id); attached.delete(id); }
    return { ok: true, closed: tabs.map(tab => ({ tab_id: tab.id, title: tab.title || "", url: tab.url || "" })), count: tabs.length };
  }
  if (op === "screenshot") {
    const tab = await tabFor(args);
    webUrl(tab.url);
    const [active] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
    if (active?.id !== tab.id) throw new Error("Скриншот обычной вкладки доступен, когда она активна. DFY снимает свои фоновые вкладки отдельно");
    const url = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 75 });
    return { data: url.split(",")[1], media_type: "image/jpeg", tab_id: tab.id };
  }
  if (op === "action") {
    const tab = await tabFor(args);
    webUrl(tab.url);
    let frameId = 0, element = args.element;
    if (typeof element === "string" && /^\d+:\d+$/.test(element)) [frameId, element] = element.split(":").map(Number);
    const rows = await chrome.scripting.executeScript({ target: { tabId: tab.id, frameIds: [frameId] }, func: pageOperation, args: ["action", { ...args, element }] });
    return rows[0]?.result || { ok: true };
  }
  if (op === "dfy_start") {
    const connection = socket;
    if (!connection || connection.readyState !== WebSocket.OPEN) throw new Error("MemuAI отключён");
    const tab = await chrome.tabs.create({ url: "about:blank", active: false });
    ownedTabs.add(tab.id);
    if (callId) dfyStarts.set(callId, tab.id);
    try {
      await chrome.storage.session.set({ dfyTabs: [...ownedTabs] });
      if (cancelled.has(callId) || socket !== connection || connection.readyState !== WebSocket.OPEN) throw new Error("Задача отменена или MemuAI отключён");
      await chrome.debugger.attach({ tabId: tab.id }, "1.3");
      attached.add(tab.id);
      if (cancelled.has(callId) || socket !== connection || connection.readyState !== WebSocket.OPEN) throw new Error("Задача отменена или MemuAI отключён");
      await chrome.debugger.sendCommand({ tabId: tab.id }, "Page.enable");
      await chrome.debugger.sendCommand({ tabId: tab.id }, "Page.setDownloadBehavior", { behavior: "deny" });
      return { tab_id: tab.id };
    } catch (error) {
      ownedTabs.delete(tab.id); attached.delete(tab.id);
      dfyStarts.delete(callId); cancelled.delete(callId);
      await chrome.debugger.detach({ tabId: tab.id }).catch(() => {});
      await chrome.tabs.remove(tab.id).catch(() => {});
      await chrome.storage.session.set({ dfyTabs: [...ownedTabs] });
      throw error;
    }
  }
  if (op === "dfy_stop") {
    const id = Number(args.tab_id);
    if (ownedTabs.has(id)) {
      await chrome.debugger.detach({ tabId: id }).catch(() => {}); attached.delete(id);
      await chrome.tabs.remove(id).catch(() => {}); ownedTabs.delete(id);
      await chrome.storage.session.set({ dfyTabs: [...ownedTabs] });
    }

    for (const [key, tab] of dfyStarts) if (tab === id) dfyStarts.delete(key);
    return { ok: true };
  }
  if (op === "cdp") {
    const id = Number(args.tab_id);
    if (!ownedTabs.has(id) || !attached.has(id) || !cdpAllowed.has(args.method)) throw new Error("Команда доступна только в отдельной вкладке DFY");
    const tab = await chrome.tabs.get(id);
    if (tab.url && tab.url !== "about:blank") webUrl(tab.url);
    if (args.method === "Page.navigate") webUrl(args.params?.url);
    return chrome.debugger.sendCommand({ tabId: id }, args.method, args.params || {});
  }
  throw new Error("Неизвестная команда MemuAI");
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {

  if (sender.id !== chrome.runtime.id || sender.tab || !sender.url?.startsWith(chrome.runtime.getURL(""))) return false;
  (async () => {

    if (message.op === "connect") { await connect(); return { ok: true }; }
    if (message.op === "status") return { connected: socket?.readyState === WebSocket.OPEN };
    if (message.op === "local_read") return read(message.args || {});
    if (message.op === "api") {
      if (!/^(models|chats(?:\?id=[\w%.-]+)?|answer|tool)$/.test(message.path)) throw new Error("Недопустимый маршрут");
      return (await api(message.path, message.body ? { method: "POST", body: JSON.stringify(message.body) } : {})).json();
    }
    throw new Error("Неизвестный запрос расширения");
  })().then((value) => respond({ value }), (error) => respond({ error: error.message }));
  return true;
});
chrome.runtime.onInstalled.addListener(() => { chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }); chrome.alarms.create("memu-reconnect", { periodInMinutes: 0.5 }); connect(); });
chrome.runtime.onStartup.addListener(connect);
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "memu-reconnect") connect(); });
chrome.storage.onChanged.addListener((changes, area) => { if (area === "local" && (changes.token || changes.base)) { socket?.close(); socket = null; cleanupTabs().then(connect); } });
chrome.tabs.onActivated.addListener(metadata);
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.url || info.title || info.status === "complete") metadata(); });
chrome.windows.onFocusChanged.addListener(metadata);
chrome.debugger.onDetach.addListener(({ tabId }) => { attached.delete(tabId); });
chrome.debugger.onEvent.addListener(({ tabId }, method, params) => {
  if (ownedTabs.has(tabId) && attached.has(tabId) && method === "Page.javascriptDialogOpening") {
    chrome.debugger.sendCommand({ tabId }, "Page.handleJavaScriptDialog", { accept: params.type === "alert" }).catch(() => {});
  }
});
async function cleanupTabs() {
  const tabs = [...ownedTabs]; ownedTabs.clear(); attached.clear();
  dfyStarts.clear(); cancelled.clear();
  for (const id of tabs) {
    await chrome.debugger.detach({ tabId: id }).catch(() => {});
    await chrome.tabs.remove(id).catch(() => {});
  }
  await chrome.storage.session.set({ dfyTabs: [] });
}

chrome.storage.session.get({ dfyTabs: [] }).then(async ({ dfyTabs }) => {
  for (const id of dfyTabs) { await chrome.debugger.detach({ tabId: id }).catch(() => {}); await chrome.tabs.remove(id).catch(() => {}); }
  await chrome.storage.session.set({ dfyTabs: [] });
  connect();
});
