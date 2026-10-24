"""Real hidden Edge proof for the Proxy prompt viewer; reads current source."""
import asyncio
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
sys.path.insert(0, str(root))
isolated = tempfile.TemporaryDirectory(prefix="memuai-prompt-ui-")
os.environ["APPDATA"] = str(Path(isolated.name) / "appdata")
os.environ["LOCALAPPDATA"] = str(Path(isolated.name) / "localappdata")
os.environ["MEMUAI_DATA_DIR"] = str(Path(isolated.name) / "data")
from app.automation_runtime import EdgeAutomation

source = (root / "app/static/app.js").read_text(encoding="utf-8")
i18n = (root / "app/static/i18n.js").read_text(encoding="utf-8")
log_code = source[source.index("let logsLoading = false;"):source.index("function syncLogsTimer()")]
clear_start = source.index('$("#logs-clear").onclick =')
clear_code = source[clear_start:source.index("/* --------- журнал сервера", clear_start)]
escape_code = next(line for line in source.splitlines() if line.startswith("function esc(s)"))
state_code = "\n".join(line for line in source.splitlines() if line.startswith(("let logsState =", "let logsExpanded =", "let logsPromptsExpanded =")))

script = i18n + "\n" + state_code + "\n" + escape_code + "\n" + r'''
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const fmtDateTime = (value) => String(value);
const fmtTime = (value) => String(value);
const providerMeta = (id) => ({title: id});
const translateMessage = (text) => text;
window.copied = []; window.errors = []; window.promptRequests = []; window.pendingPrompts = new Map();
const copyText = (text) => { window.copied.push(text); return true; };
const toast = (message) => window.errors.push(message);
const confirmDialog = (title, hint, callback) => { window.clearPromise = callback(); };
const api = async (url) => {
  if (url === "/api/admin/logs/clear") { window.fixture = {logs: [], total: 0}; return {}; }
  if (!url.startsWith("/api/admin/logs/prompt?")) return structuredClone(window.fixture);
  const id = new URLSearchParams(url.split("?")[1]).get("request_id");
  window.promptRequests.push(id);
  const delayed = window.pendingPrompts.get(id);
  if (delayed) return await delayed.promise;
  if (!window.snapshots.has(id)) throw new Error("Prompt entry expired");
  return {prompt: structuredClone(window.snapshots.get(id))};
};
''' + log_code + "\n" + clear_code + "\n" + r'''
window.runProof = async () => {
  let checks = 0;
  const check = (condition, message) => { checks++; if (!condition) throw new Error(message); };
  const requestId = 'request-a"<&';
  const injection = '</pre><script>window.promptInjected=true</script><img src=x onerror="window.promptInjected=true">';
  const current = {request_id: requestId, ts: 1, status: 200, model: "demo", provider: "local", ms: 4,
    stages: [{step: "done", ts: 1, payload: '{"stage":"unchanged"}'}],
    prompt: {messages: [
      {role: "system", content: "System prompt " + injection},
      {role: "user", content: [{type: "text", text: "Привет café " + injection}]},
      {role: "assistant", content: null, tool_calls: [{id: "call-1", type: "function", function: {name: "search", arguments: '{"query":"text"}'}}]},
      {role: "tool", name: "search", tool_call_id: "call-1", content: "<tool result>"},
      {role: injection, content: "Untrusted role"}
    ], tools: [{type: "function", function: {name: "search", parameters: {type: "object", properties: {query: {type: "string"}}}}}],
    truncated: true, omitted_media: true, headers: {authorization: "FIXTURE_SECRET_MUST_BE_IGNORED"}},
    credentials: {cookie: "FIXTURE_SECRET_MUST_BE_IGNORED"}};
  const legacy = {request_id: "legacy", ts: 2, status: 200, model: "legacy"};
  const empty = {...legacy, request_id: "empty", prompt: {messages: [], truncated: false, omitted_media: false}};
  const malformed = {...legacy, request_id: "malformed", prompt: {messages: "invalid"}};
  const show = (index) => $(`[data-prompt="${index}"]`);
  const copy = (index) => $(`[data-promptcopy="${index}"]`);
  const box = (index) => $("#prompt-" + index);
  const setRows = async (rows) => { window.fixture = {logs: rows, total: rows.length}; await loadLogs(true); };
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  const delayPrompt = (id) => {
    let resolve, reject;
    const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
    const pending = {promise, resolve, reject}; window.pendingPrompts.set(id, pending); return pending;
  };
  window.snapshots = new Map([[requestId, current.prompt]]);
  LANG = "ru";
  await setRows([current, legacy, empty, malformed]);
  check(show(0).textContent === "Показать промпт", "RU show label");
  check(copy(0).textContent === "Копировать промпт", "RU copy label");
  check(box(0).style.display === "none" && !show(0).disabled && !copy(0).disabled, "initial available collapsed prompt");
  check($('[data-log="0"]').dataset.requestId === requestId, "escaped request identity");
  for (const index of [1,2,3]) {
    check(show(index).disabled && copy(index).disabled && show(index).title.length > 0, "unavailable/empty disabled with explanation");
    show(index).onclick(); copy(index).onclick();
  }
  check(window.copied.length === 0, "disabled controls do not copy");
  check(show(1).title !== show(2).title, "empty versus missing prompt explanation");
  show(0).click();
  check(box(0).style.display === "block" && show(0).textContent === "Скрыть промпт", "show prompt");
  check(show(0).getAttribute("aria-expanded") === "true", "accessible expanded state");
  check($("#stages-0").style.display === "none", "prompt expansion is independent from stages");
  const plain = box(0).textContent.trim();
  check(plain.includes("[system]\nSystem prompt") && plain.includes("[user]\nПривет café"), "readable roles and multipart text");
  check(plain.includes('"tool_calls"') === false && plain.includes('tool_calls: [') && plain.includes('"search"'), "structured tool calls use JSON");
  check(plain.includes("[tool (search)]") && plain.includes("tool_call_id: call-1") && plain.includes("<tool result>"), "tool response context");
  check(plain.includes('Инструменты:\n[') && plain.includes('"parameters"'), "root tool schemas use JSON");
  check(plain.includes("Промпт усечён") && plain.includes("медиа не сохранены"), "RU incomplete snapshot notices");
  check(plain.includes(injection) && !box(0).querySelector("script,img") && !window.promptInjected, "payload stays plain text without HTML execution");
  check(!plain.includes("FIXTURE_SECRET_MUST_BE_IGNORED"), "metadata credentials excluded");
  await copy(0).onclick();
  check(window.copied.at(-1) === logPromptText(current.prompt, false), "copy contains prompt blocks only");
  check(!window.copied.at(-1).includes("Промпт усечён") && !window.copied.at(-1).includes("медиа не сохранены"), "RU copy excludes service notices");
  const longText = "Полный контекст 🐈\n".repeat(100000) + "END_SENTINEL";
  const full = {...current, request_id: "complete-long", prompt: {messages: [{role: "user", content: longText}]}};
  await setRows([full]); await copy(0).onclick();
  check(window.copied.at(-1) === "[user]\n" + longText, "long prompt copies every character including the final sentinel");
  await setRows([current, legacy, empty, malformed]);
  $('[data-pcopy="0-0"]').click();
  check(window.copied.at(-1) === '{\n  "stage": "unchanged"\n}', "existing stage copy preserved");
  $('[data-logcopy="0"]').click();
  check(window.copied.at(-1).includes("request_id: " + requestId) && window.copied.at(-1).includes("stage") && !window.copied.at(-1).includes("System prompt"), "existing entry copy preserved");
  await setRows([legacy, current, empty]);
  check(box(1).style.display === "block" && box(0).style.display === "none", "refresh reorder preserves only matching request_id");
  await setRows([legacy]);
  await setRows([legacy, current]);
  check(box(1).style.display === "block", "paging/filter return preserves expansion");
  LANG = "en"; await setRows([current, legacy]);
  check(show(0).textContent === "Hide prompt" && copy(0).textContent === "Copy prompt", "EN prompt labels");
  check(box(0).textContent.includes("Prompt was truncated") && box(0).textContent.includes("media were not stored"), "EN snapshot notices");
  check(box(0).textContent.includes("Tools:\n["), "EN tool schema label");
  await copy(0).onclick();
  check(!window.copied.at(-1).includes("Prompt was truncated") && !window.copied.at(-1).includes("media were not stored"), "EN copy excludes service notices");
  check(show(1).title.includes("older entry"), "EN legacy explanation");
  show(0).click(); await setRows([legacy, current]);
  check(box(1).style.display === "none" && show(1).textContent === "Show prompt" && show(1).getAttribute("aria-expanded") === "false", "hide state survives refresh");
  logsState.expandAll = true; await setRows([current]);
  check(box(0).style.display === "none" && $("#stages-0").style.display === "block", "expand stages does not disclose prompts");
  const toolsOnly = {...legacy, request_id: "tools-only", prompt: {messages: [], tools: current.prompt.tools}};
  await setRows([toolsOnly]);
  check(!show(0).disabled && !copy(0).disabled, "tools-only snapshot remains available");
  check(window.errors.length === 0, "no load/render errors: " + window.errors.join(","));

  // Normal list records contain only lightweight availability descriptors.
  logsState.expandAll = false; logsPromptsExpanded.clear(); logsPromptCache.clear();
  let formatted = 0;
  const originalFormatter = logPromptText;
  logPromptText = (prompt, ...options) => { formatted++; return originalFormatter(prompt, ...options); };
  const lazy = {...current, prompt: {available: true, truncated: true, omitted_media: true}};
  const largePage = Array.from({length: 100}, (_, i) => ({...lazy, request_id: "large-" + i}));
  for (const row of largePage) window.snapshots.set(row.request_id, {messages: [{role: "user", content: "x".repeat(64000)}]});
  await setRows(largePage); await setRows(largePage);
  check(formatted === 0 && window.promptRequests.length === 0 && !$("#logs-list [id^='prompt-'] pre"), "100 large collapsed prompts do no formatting, fetch or hidden DOM work");
  await setRows([lazy, legacy, empty]);
  await setRows([legacy, lazy, empty]);
  check(window.promptRequests.length === 0 && !$("#logs-list [id^='prompt-'] pre"), "collapsed refresh fetches no prompts and builds no hidden pre DOM");
  show(1).click(); await tick();
  check(window.promptRequests.length === 1 && window.promptRequests[0] === requestId, "show fetches exact encoded request_id once");
  check(box(1).querySelector("pre").textContent.includes("System prompt"), "lazy loaded viewer");
  show(1).click();
  check(!box(1).querySelector("pre"), "hide removes prompt DOM");
  await copy(1).onclick(); show(1).click(); await tick();
  check(window.promptRequests.length === 1, "repeated copy/show uses cached prompt");
  check(window.copied.at(-1) === logPromptText(current.prompt, false), "lazy copy preserves complete prompt without viewer notices");
  const scrollText = "scroll line\n".repeat(1000);
  current.prompt.messages[0].content += "\n" + scrollText;
  logsPromptCache.get(requestId).snapshot.messages[0].content += "\n" + scrollText;
  renderLogPrompt(requestId);
  const pre = box(1).querySelector("pre"); pre.scrollTop = 120;
  check(pre.scrollTop === 120, "scroll fixture has overflow");
  await setRows([lazy, legacy]);
  check(box(0).style.display === "block" && box(0).querySelector("pre").scrollTop === 120, "refresh preserves open prompt scroll by request_id");
  check(window.promptRequests.length === 1, "expanded cached refresh makes no new prompt fetch");
  await setRows([legacy]);
  check(logsPromptCache.size === 0, "page departure prunes prompt cache");
  await setRows([lazy, legacy]); await tick();
  check(window.promptRequests.length === 2 && box(0).querySelector("pre"), "returning previously opened row refetches after pruning");

  // Keep a response pending while the source moves to a different row index.
  logsPromptsExpanded.clear(); await setRows([lazy, legacy]); logsPromptCache.clear();
  const moving = delayPrompt(requestId);
  show(0).click();
  check(show(0).getAttribute("aria-busy") === "true" && copy(0).disabled, "fetch state is visible on controls");
  show(0).click(); show(0).click();
  check(window.promptRequests.length === 3, "show/hide during fetch deduplicates in-flight request");
  await setRows([legacy, lazy]);
  moving.resolve({prompt: structuredClone(current.prompt)}); window.pendingPrompts.delete(requestId); await tick();
  check(!box(0).querySelector("pre") && box(1).querySelector("pre").textContent.includes("System prompt"), "late response renders only in matching request_id after reorder");
  check(show(1).getAttribute("aria-busy") === "false" && !copy(1).disabled, "fetch state clears after response");

  logsPromptsExpanded.clear(); logsPromptCache.clear(); await setRows([lazy, legacy]);
  const copyMoving = delayPrompt(requestId);
  const movingCopy = copy(0).onclick();
  await setRows([legacy, lazy]);
  copyMoving.resolve({prompt: structuredClone(current.prompt)}); window.pendingPrompts.delete(requestId); await movingCopy;
  check(window.copied.at(-1).includes("System prompt") && !box(0).querySelector("pre") && !box(1).querySelector("pre"), "copy after reorder uses source request and keeps collapsed DOM empty");

  // An in-flight copy must not publish a revoked same-id entry or rebuild cache.
  logsPromptsExpanded.clear(); logsPromptCache.clear(); await setRows([lazy]);
  const revoked = delayPrompt(requestId);
  const oldCopies = window.copied.length;
  const copyPending = copy(0).onclick();
  await setRows([{...lazy, prompt: {available: false}}]);
  revoked.resolve({prompt: structuredClone(current.prompt)}); window.pendingPrompts.delete(requestId); await copyPending; await tick();
  check(window.copied.length === oldCopies && logsPromptCache.size === 0 && !box(0).querySelector("pre"), "revoked same-id fetch cannot copy/render/repopulate cache");
  check(show(0).disabled && copy(0).disabled && window.errors.at(-1).includes("Prompt not stored"), "revoked entry shows readable error and disabled controls");

  // Removing the row during a Show request must not resurrect it elsewhere.
  await setRows([lazy]);
  const gone = delayPrompt(requestId); show(0).click();
  await setRows([legacy]);
  gone.resolve({prompt: structuredClone(current.prompt)}); window.pendingPrompts.delete(requestId); await tick();
  check(logsPromptCache.size === 0 && !box(0).querySelector("pre"), "new page discards stale response without recreating cache");

  logsPromptsExpanded.clear(); await setRows([lazy]);
  const oldLifetime = delayPrompt(requestId); show(0).click();
  await setRows([legacy]); window.pendingPrompts.delete(requestId);
  const newSnapshot = structuredClone(current.prompt); newSnapshot.messages[0].content = "New request lifetime";
  window.snapshots.set(requestId, newSnapshot);
  await setRows([lazy]); await tick();
  oldLifetime.resolve({prompt: structuredClone(current.prompt)}); await tick();
  check(box(0).querySelector("pre").textContent.includes("New request lifetime") && !box(0).textContent.includes("System prompt"), "pruned same-id lifetime cannot overwrite replacement cache");
  window.snapshots.set(requestId, current.prompt);

  // Server expiration is readable; an error response never gets copied.
  const expired = {...lazy, request_id: "expired"};
  await setRows([expired]); show(0).click(); await tick();
  check(box(0).textContent === "Prompt entry expired" && !box(0).querySelector("pre"), "expired Show renders readable server error");
  await copy(0).onclick();
  check(window.copied.length === oldCopies && window.errors.at(-1) === "Prompt entry expired", "expired Copy reports error without copying");
  await setRows([empty, {...lazy, request_id: null, prompt: {messages: []}}, {...lazy, request_id: null, prompt: current.prompt}]);
  check(show(0).disabled && copy(0).disabled && show(1).disabled && copy(1).disabled, "empty snapshot fallback stays disabled");
  const beforeFallback = window.promptRequests.length;
  show(2).click(); await copy(2).onclick();
  check(window.promptRequests.length === beforeFallback && box(2).querySelector("pre") && window.copied.at(-1).includes("System prompt"), "full snapshot without id displays/copies without network fetch");
  logsPromptsExpanded.clear(); await setRows([lazy]);
  const clearing = delayPrompt(requestId); show(0).click();
  const copiesBeforeClear = window.copied.length;
  $("#logs-clear").onclick(); await window.clearPromise; await tick();
  clearing.resolve({prompt: structuredClone(current.prompt)}); window.pendingPrompts.delete(requestId); await tick();
  check(logsPromptCache.size === 0 && logsPromptsExpanded.size === 0 && logsCache.length === 0, "actual clear-logs handler clears cache, expansion and rows");
  check(window.copied.length === copiesBeforeClear && !$("#logs-list pre"), "response after clear cannot restore cached prompt or DOM");
  return {passed: true, checks, prompt_fetches: window.promptRequests.length, clipboard_operations: window.copied.length, html_executed: !!window.promptInjected, locales: ["ru", "en"]};
};
'''

html = '''<!doctype html><html><head><meta charset="utf-8"><title>Prompt viewer proof</title><style>
.code-block { white-space: pre-wrap; max-height: 300px; overflow: auto; }
</style></head><body>
<input id="logs-search"><select id="logs-limit"><option value="100">100</option></select>
<select id="logs-status"><option value=""></option></select><select id="logs-provider"><option value=""></option></select>
<select id="logs-model"><option value=""></option></select><select id="logs-period"><option value="0">0</option></select>
<div id="logs-summary"></div><div id="logs-page"></div><button id="logs-prev"></button><button id="logs-next"></button>
<button id="logs-clear"></button>
<div id="logs-list"></div><script src="/harness.js"></script></body></html>'''

class Server(http.server.ThreadingHTTPServer):
    def handle_error(self, *args):
        pass

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        content, kind = (script, "text/javascript") if self.path == "/harness.js" else (html, "text/html")
        payload = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass

server = Server(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

async def check():
    edge = EdgeAutomation()
    try:
        await edge.navigate(f"http://127.0.0.1:{server.server_port}/")
        result = await edge.command("Runtime.evaluate", {"expression": "(async () => JSON.stringify(await window.runProof()))()", "awaitPromise": True, "returnByValue": True})
        if result.get("exceptionDetails"):
            raise AssertionError(result["exceptionDetails"])
        proof = json.loads(result["result"]["value"])
        assert proof["passed"], proof
        print(json.dumps(proof, ensure_ascii=True))
        print("PASS: prompt viewer, copy and safe rendering")
    finally:
        await edge.close_browser()

try:
    asyncio.run(check())
finally:
    server.shutdown()
    server.server_close()
    isolated.cleanup()
