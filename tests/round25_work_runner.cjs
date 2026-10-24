// Round 25 (b3-work-agents): the real agents.js in Node. A card saved as «running» whose agent the
// server no longer knows (MemuAI was restarted) is marked interrupted; an agent spawned while a poll
// was in flight is not. Prints one JSON line for round25_work_test.py.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "app", "pgstatic", "agents.js"), "utf8");

const harness = `
  let PG_LANG = "en";
  const S = { currentId: "c1", chats: [], messages: [] };
  const generationJobs = new Map();
  const calls = { renders: 0, persisted: 0, resumed: 0 };
  let server = { agents: [] };
  let duringFetch = null;
  async function api(url, opts) {
    if (opts && opts.body && opts.body.action === "collect") return { finished: [] };
    const rows = server.agents.map((row) => ({ ...row }));
    if (duringFetch) { const hook = duringFetch; duringFetch = null; hook(); }
    return { agents: rows };
  }
  const renderMessages = () => { calls.renders += 1; };
  const persistChatSnapshot = () => { calls.persisted += 1; };
  const resumeAfterAutomation = () => { calls.resumed += 1; };
  const toast = () => {};
  const esc = (value) => String(value);
  const md = (value) => String(value);
  const stepStatusText = (status) => status;
  const modelDisplayName = (id) => id;
  const step = (row) => JSON.stringify({ status: row.status, actions: !!row.actions, body: row.body });
  const setInterval = () => 1, clearInterval = () => {};
  ${source}

  (async () => {
    const out = {};
    // ---- a chat reopened after a restart: the saved card says «running», the server has no agents
    const stale = { role: "assistant", content: "", agentJobs: [{ id: 1, title: "Docs", status: "running", delivered: false }] };
    S.messages = [{ role: "user", content: "use agents" }, stale];
    MemuAgents.resume("c1", S.messages, "work");
    await MemuAgents.poll();
    out.stale = { status: stale.agentJobs[0].status, delivered: stale.agentJobs[0].delivered,
                  error: stale.agentJobs[0].error, card: JSON.parse(MemuAgents.stepHtml(stale, 1)), resumed: calls.resumed };

    // ---- an agent spawned while a poll request was in flight stays running
    const fresh = { role: "assistant", content: "", agentJobs: [] };
    S.messages = [{ role: "user", content: "use agents" }, fresh];
    MemuAgents.watch("c1", S.messages, "work");
    duringFetch = () => {
      server.agents = [{ id: 1, title: "Tests", status: "running", tools: 0, delivered: false }];
      MemuAgents.onToolResult(fresh, { tool: "agent_spawn" }, { id: 1, title: "Tests", model: "m", can_write: true }, null);
    };
    await MemuAgents.poll();
    out.freshAfterRace = fresh.agentJobs[0].status;
    await MemuAgents.poll();
    out.freshNext = fresh.agentJobs[0].status;
    console.log(JSON.stringify(out));
  })().catch((error) => console.log(JSON.stringify({ error: String(error && error.stack || error) })));
`;

vm.runInNewContext(harness, { console, setTimeout, clearTimeout, Promise });
