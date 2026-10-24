// Runs the real agents.js (agents of Work and MemuCode in the MemuAI window) with a scripted
// server: when agents are allowed, what the interface remembers about them, and how a report that
// nobody waited for comes back into the conversation. Prints one JSON line for round13_test.py.
const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "pgstatic", "agents.js"), "utf8");

const harness = `
  let PG_LANG = "en";
  const S = { currentId: "c1", chats: [], messages: [] };
  const generationJobs = new Map();
  const calls = { api: [], resumed: [], renders: 0, persisted: 0, toasts: [] };
  let server = { agents: [], finished: [] };
  async function api(url, opts) {
    calls.api.push([url, opts && opts.body ? opts.body.action : "list"]);
    if (opts && opts.body && opts.body.action === "collect") {
      const finished = server.finished; server.finished = [];
      server.agents.forEach((row) => { if (row.status !== "running") row.delivered = true; });
      return { finished };
    }
    if (opts && opts.body && opts.body.action === "cancel") {
      server.agents.forEach((row) => { if (row.status === "running") { row.status = "cancelled"; row.delivered = true; } });
      return { stopped: 1 };
    }
    return { agents: server.agents.map((row) => ({ ...row })) };
  }
  const renderMessages = () => { calls.renders += 1; };
  const persistChatSnapshot = () => { calls.persisted += 1; };
  const resumeAfterAutomation = (kind, report, attempts, src) => calls.resumed.push({ kind, report, sameBubble: src.assistant === S.messages[1] });
  const toast = (text) => calls.toasts.push(text);
  const esc = (value) => String(value);
  const md = (value) => "<md>" + value + "</md>";
  const icon = (name) => "[" + name + "]";
  const stepStatusText = (status) => status;
  const modelDisplayName = (id) => id;
  const step = (row) => JSON.stringify({ title: row.title, meta: row.meta, status: row.status, open: row.open, actions: !!row.actions, body: row.body });
  const setInterval = () => 1, clearInterval = () => {};
  ${source}

  (async () => {
    const out = {};
    const user = (content, extra) => ({ role: "user", content, ...(extra || {}) });
    // ---- when agents are allowed
    out.chat = MemuAgents.allowed([user("use agents for this")], {}, "chat");
    out.workAsked = MemuAgents.allowed([user("split it between two agents")], {}, "work");
    out.codeAskedRu = MemuAgents.allowed([user("сделай это параллельно, раздай агентам")], {}, "code");
    out.workPlain = MemuAgents.allowed([user("fix the failing test in parser.py")], {}, "work");
    out.workSwitch = MemuAgents.allowed([user("fix the failing test")], { agents: "always" }, "work");
    out.chatSwitch = MemuAgents.allowed([user("fix the failing test")], { agents: "always" }, "chat");
    out.followUp = MemuAgents.allowed([user("use agents"), user("ok"), user("continue")], {}, "work");
    out.tooOld = MemuAgents.allowed([user("use agents"), user("a"), user("b"), user("c")], {}, "work");
    out.internalIgnored = MemuAgents.allowed([user("<local-tool_report>the agent tool…", { internal: true }), user("rename it")], {}, "work");
    out.promptFull = MemuAgents.prompt(3, true);
    out.promptAsk = MemuAgents.prompt(2, false);

    // ---- a spawned agent is remembered in the answer that started it
    const assistant = { role: "assistant", content: "", streaming: true };
    S.messages = [user("do the docs and the tests with agents"), assistant];
    S.chats = [{ id: "c1", messages: S.messages }];
    const src = { chatId: "c1", messages: S.messages, mode: "work" };
    MemuAgents.onToolResult(assistant, { tool: "agent_spawn" }, { id: 1, title: "Docs", model: "m", status: "running", can_write: true }, src);
    MemuAgents.onToolResult(assistant, { tool: "agent_spawn" }, { id: 2, title: "Tests", model: "m", status: "running", can_write: true }, src);
    MemuAgents.onToolResult(assistant, { tool: "work_read" }, { path: "a" }, src);
    MemuAgents.onToolResult(assistant, { tool: "agent_spawn" }, { error: "no" }, src);
    out.remembered = assistant.agentJobs.map((job) => job.title);

    // ---- both run; the main model is still busy with its own part: nothing is delivered
    server.agents = [
      { id: 1, title: "Docs", model: "m", status: "running", tools: 2, last: "work_read README.md", seconds: 3, delivered: false },
      { id: 2, title: "Tests", model: "m", status: "running", tools: 1, last: "work_search parse", seconds: 3, delivered: false }];
    await MemuAgents.poll();
    out.liveLast = assistant.agentJobs.map((job) => job.last);
    out.runningCard = JSON.parse(MemuAgents.stepHtml(assistant, 1));
    server.agents[0] = { ...server.agents[0], status: "done", report: "Docs written to README.md", seconds: 9 };
    server.finished = [{ ...server.agents[0], delivered: true }];
    await MemuAgents.poll();
    out.deliveredWhileBusy = calls.resumed.length;

    // ---- the main model's turn is over: the finished report wakes the conversation up
    assistant.streaming = false;
    await MemuAgents.poll();
    out.resumed = calls.resumed.slice();
    out.collectCalls = calls.api.filter((row) => row[1] === "collect").length;
    await MemuAgents.poll();
    out.resumedTwice = calls.resumed.length;

    // ---- Stop cancels what is still running and does not resume anything
    MemuAgents.cancel("c1");
    await new Promise((resolve) => setTimeout(resolve, 10));
    await MemuAgents.poll();
    out.afterStop = { statuses: assistant.agentJobs.map((job) => job.status), resumed: calls.resumed.length };
    out.doneCard = JSON.parse(MemuAgents.stepHtml(assistant, 1));
    out.noCard = MemuAgents.stepHtml({ role: "assistant" }, 0);
    __done(out);
  })().catch((error) => __done({ error: String((error && error.stack) || error) }));
`;
const ctx = { __done: (value) => process.stdout.write(JSON.stringify(value) + "\n"), setTimeout, JSON, Promise, Map, console };
vm.createContext(ctx);
try {
  vm.runInContext(harness, ctx);
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String((error && error.stack) || error) }) + "\n");
}
