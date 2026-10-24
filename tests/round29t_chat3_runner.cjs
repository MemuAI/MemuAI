// Round 29 phase 2 (r29c-chat3): runs pgstatic/stuck.js and pgstatic/diagrams.js in node for tests/round29t_chat_test.py.
// Prints one JSON object with the results.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const root = path.resolve(__dirname, "..");
const out = {};

// ---------------------------------------------------------------- stuck.js: Work runs with real-model patterns
function stuckSandbox() {
  const sb = {
    console, Math, JSON, Date, Set, Map, Array, Object, String, Number, Promise, RegExp, Error,
    setTimeout: () => 0, setInterval: () => 0, clearInterval: () => 0, fetch: async () => ({ ok: true, json: async () => ({}) }),
    CustomEvent: class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } },
    document: { documentElement: { lang: "ru" }, hidden: false, addEventListener: () => {}, dispatchEvent: () => true,
      querySelector: () => null, getElementById: () => null },
  };
  sb.S = { preferences: { stuck_sensitivity: "normal" }, currentId: "c1", messages: [] };
  sb.window = sb; sb.addEventListener = () => {};
  vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(root, "app/pgstatic/stuck.js"), "utf8"), sb, { filename: "stuck.js" });
  return sb;
}
{
  // A goal that re-reads one file every turn: tool rounds and plan turns alternate in ONE chat.
  const sb = stuckSandbox(), M = sb.MemuStuck;
  const plan = { status: "active", objective: "wait for READY", steps: [{ id: 1, status: "complete" }, { id: 2, status: "pending" }], completed: 1 };
  let pausedAt = -1, reason = "";
  for (let turn = 0; turn < 12 && pausedAt < 0; turn++) {
    const a = { toolCalls: { status: "pending", calls: [{ tool: "work_read", args: { path: "status.txt" } }] } };
    if (M.workGate(a, { phase: "tools", chatId: "c1" })) { pausedAt = turn; reason = a.toolCalls.stuckPause.reason; break; }
    // the model toggles a step complete/pending every turn (seen with deepseek)
    plan.steps[1].status = turn % 2 ? "complete" : "pending"; plan.completed = turn % 2 ? 2 : 1;
    const b = { content: "status: continue", planUpdates: [{ status: turn % 2 ? "complete" : "pending" }],
      toolHistory: [{ tool: "work_read", args: { path: "status.txt" }, ok: true }], goalProgress: { status: "continue" } };
    if (M.workGate(b, { phase: "plan", chatId: "c1", plan })) { pausedAt = turn; reason = plan.stuckPause.reason; break; }
  }
  out.interleaved = { pausedAt, reason };
  // plan turns alone, a step re-marked complete/pending every turn: the best-so-far marker does not move → paused
  const sb3 = stuckSandbox(), M3 = sb3.MemuStuck;
  const plan3 = { status: "active", objective: "toggle", steps: [{ id: 1, status: "complete" }, { id: 2, status: "pending" }, { id: 3, status: "pending" }], completed: 1 };
  let toggled = -1, toggledReason = "";
  for (let turn = 0; turn < 12; turn++) {
    plan3.steps[2].status = turn % 2 ? "complete" : "pending";
    const b = { content: "continue", planUpdates: [{ status: turn % 2 ? "complete" : "pending" }], goalProgress: { status: "continue" } };
    if (M3.workGate(b, { phase: "plan", chatId: "c3", plan: plan3 })) { toggled = turn; toggledReason = plan3.stuckPause.reason; break; }
  }
  out.toggled = { pausedAt: toggled, reason: toggledReason };
  // a plan that really completes a new step each turn is never paused
  const sb2 = stuckSandbox(), M2 = sb2.MemuStuck;
  const steps = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, status: "pending" }));
  const plan2 = { status: "active", objective: "build", steps, completed: 0 };
  let falsePause = false;
  for (let turn = 0; turn < 9; turn++) {
    const a = { toolCalls: { status: "pending", calls: [{ tool: "work_read", args: { path: `src/f${turn}.py` } }] } };
    falsePause = falsePause || M2.workGate(a, { phase: "tools", chatId: "c2" });
    steps[turn].status = "complete"; plan2.completed = turn + 1;
    const b = { content: "done step", planUpdates: [{ status: "complete" }], toolHistory: [] };
    falsePause = falsePause || M2.workGate(b, { phase: "plan", chatId: "c2", plan: plan2 });
  }
  out.progressing = { falsePause };
}

// ---------------------------------------------------------------- diagrams.js: repairMermaid
{
  const el = () => ({ style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} }, setAttribute() {}, appendChild() {},
    querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
    getContext() { return { measureText: (t) => ({ width: String(t).length * 7 }) }; } });
  const sb = {
    console, document: { readyState: "complete", documentElement: { lang: "en", dataset: {}, style: {} }, createElement: el,
      getElementById() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {},
      head: { appendChild() {} }, body: { appendChild() {} } },
    navigator: {}, setTimeout, clearTimeout, requestAnimationFrame: (f) => setTimeout(f, 0), localStorage: { getItem: () => null, setItem() {} },
    getComputedStyle: () => ({ getPropertyValue: () => "" }), matchMedia: () => ({ matches: false }), MutationObserver: class { observe() {} },
    Blob, TextEncoder, URL, Intl, CSS: { escape: (s) => s }, Event: class {}, FileReader: class {},
  };
  sb.window = sb; vm.createContext(sb);
  vm.runInContext(fs.readFileSync(path.join(root, "app/pgstatic/diagrams.js"), "utf8"), sb, { filename: "diagrams.js" });
  const R = sb.MemuDiagrams.repairMermaid;
  out.mermaid = [
    R("flowchart TD\n    E --> F[Send 2FA code (SMS / authenticator app)]\n    F --> G[User enters code]"),
    R("graph LR\n  A{Is it ok (y/n)?} -- yes --> B(Done (fast))\n  B --> C[(Database)]\n  C --> D[[Sub]] & E((Circle))"),
    R('flowchart TD\n  A["Already (quoted)"] --> B[plain]'),
    R("sequenceDiagram\n  A->>B: hi (x)"),
  ];
}
console.log(JSON.stringify(out));
