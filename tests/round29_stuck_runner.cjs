

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..");
const listeners = {};
const sandbox = {
  console, Math, JSON, Date, Set, Map, Array, Object, String, Number, Promise, RegExp, Error,
  setTimeout: () => 0, setInterval: () => 0, clearInterval: () => 0, fetch: async () => ({ ok: true, json: async () => ({}) }),
  CustomEvent: class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } },
  document: {
    documentElement: { lang: "en" }, hidden: false,
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent: () => true, querySelector: () => null, getElementById: () => null,
  },
};
sandbox.S = { preferences: { stuck_sensitivity: sens0() }, currentId: "c1", messages: [] };
function sens0() { return process.argv[2] || "normal"; }
sandbox.window = sandbox;
sandbox.addEventListener = () => {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, "app/pgstatic/stuck.js"), "utf8"), sandbox, { filename: "stuck.js" });
const M = sandbox.MemuStuck;
const data = JSON.parse(fs.readFileSync(path.join(__dirname, "round29_stuck_traces.json"), "utf8"));
const sens = process.argv[2] || "normal";

function expand(trace) {
  if (trace.repeat) return Array.from({ length: trace.repeat.times }, () => trace.repeat.step);
  return trace.steps;
}
for (const trace of data.traces) {
  const d = new M.Detector(sens);
  const verdicts = [];
  for (const st of expand(trace)) {
    const v = d.observe(st.a, { state: st.s, progress: st.p, claimed: !!st.c, filesChanged: st.f || 0,
      modelStuck: !!st.stuck, modelReason: st.stuck ? "model" : "" });
    if (st.e) d.noteResult({ error: st.e });
    verdicts.push(`${v.level}:${v.reason || ""}`);
  }

  console.log(JSON.stringify({ name: trace.name, verdicts }));
}

const card = M.cardHtml({ id: "c1", title: "Т", title_en: "The same action keeps repeating", detail_en: "x", steps: [{ label: "click", count: 3 }] }, { lang: "en", source: "browser" });
console.log(JSON.stringify({ name: "__card__", html: card }));
console.log(JSON.stringify({ name: "__parse__", values: [M.parseDecision({ decision: "hint", hint: "try search" }), M.parseDecision("Сменить подход"),
  M.parseDecision("Stop"), M.parseDecision("click the blue button"), M.parseDecision({ decision: "hint" }), M.parseDecision({ approve: false })] }));

{
  const plan = { status: "active", completed: 1, steps: [{ id: 1, status: "complete" }, { id: 2, status: "pending" }] };
  const stalled = [];
  let paused = "";
  for (let i = 0; i < 6; i++) { stalled.push(M.workGate({ content: "working on it " + i }, { phase: "plan", chatId: "p1", plan })); if (plan.stuckPause) paused = plan.stuckPause.reason; }
  const plan2 = { status: "active", completed: 0, steps: [{ id: 1, status: "pending" }] };
  const busy = [];
  for (let i = 0; i < 8; i++) busy.push(M.workGate({ workActions: { status: "done", files: [{ path: `src/f${i}.py`, content: "x".repeat(i + 1) }] } }, { phase: "plan", chatId: "p2", plan: plan2 }));
  const goal = { enabled: true, status: "active" };
  const goalGate = [];
  let goalReason = "";
  for (let i = 0; i < 6; i++) { goalGate.push(M.workGate({ goalProgress: { status: "continue", summary: "s" + i, nextStep: "n" } }, { phase: "goal", chatId: "g1", goal })); if (goal.stuckPause) goalReason = goal.stuckPause.reason; }
  const tools = [];
  for (let i = 0; i < 5; i++) tools.push(M.workGate({ toolCalls: { status: "pending", calls: [{ tool: "work_read", args: { path: "a.py" } }] } }, { phase: "tools", chatId: "t1" }));
  const polls = [];
  for (let i = 0; i < 8; i++) polls.push(M.workGate({ toolCalls: { status: "pending", calls: [{ tool: "agent_status", args: { id: "a1" } }] } }, { phase: "tools", chatId: "t2" }));
  console.log(JSON.stringify({ name: "__work__", stalled, paused, busy, goalGate, goalReason, tools, polls }));
}
