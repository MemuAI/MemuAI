// Prompt snapshot of the MemuAI window (app/pgstatic/app.js) for the prompt eval harness.
// Usage: node snapshot.js <tree-root>  → JSON on stdout. Not part of the normal test suite.
// Extracts the static prompt constants and evaluates workRuntimePrompt() with small stubs, so the
// eval sees exactly the text the window would send.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = process.argv[2] || path.resolve(__dirname, "../..");
const src = fs.readFileSync(path.join(root, "app/pgstatic/app.js"), "utf8");

function topLevel(name, opener) {
  const start = src.indexOf(opener);
  if (start < 0) return "";
  // A top-level statement ends at the first line that starts at column 0 after it (or a `}` line for functions).
  const rest = src.slice(start);
  const lines = rest.split("\n");
  const out = [lines[0]];
  if (!opener.startsWith("function") && /[`"'\]];\s*$/.test(lines[0])) return lines[0];
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i];
    out.push(line);
    if (opener.startsWith("function") && line === "}") break;
    if (!opener.startsWith("function") && /^(?:`;|\]\.join\([^)]*\);|.*`;\s*$)/.test(line) && !line.startsWith(" ")) break;
  }
  return out.join("\n");
}

const pieces = ["WEB_TOOLS_PROMPT", "MEM_PROMPT", "WORK_TOOLS_PROMPT", "EDIT_TOOLS_PROMPT", "CODE_MODE_PROMPT", "WORK_EXECUTION_FALLBACK"]
  .map((name) => topLevel(name, `const ${name} = `));
const fn = topLevel("workRuntimePrompt", "function workRuntimePrompt(");

const internal = JSON.parse(process.argv[3] || "{}");
const sandbox = {
  S: { prompts: { internal_prompts: Object.entries(internal).map(([id, text]) => ({ id, text })) } },
  pref: (key, fallback) => fallback,
  internalPromptText: (id) => internal[id] || "",
  window: {},
  promptData: (v) => String(v),
  workAccessMode: () => "full",
  codeSkillsPrompt: () => "",
  mcpPromptBlock: () => "",
  browserSkillModel: () => "vision-model",
  computerUseSkillModel: () => "",
  out: {},
};
const code = pieces.join("\n") + "\n" + fn + `
out.WEB_TOOLS_PROMPT = WEB_TOOLS_PROMPT; out.MEM_PROMPT = MEM_PROMPT; out.WORK_TOOLS_PROMPT = WORK_TOOLS_PROMPT;
out.EDIT_TOOLS_PROMPT = EDIT_TOOLS_PROMPT; out.CODE_MODE_PROMPT = CODE_MODE_PROMPT;
const project = { workspaces: { items: [{ id: "p1", name: "demo" }], active_id: "p1" } };
out.work_runtime = workRuntimePrompt({ ...project, mode: "work" });
out.work_runtime_goal = workRuntimePrompt({ ...project, mode: "work", workGoal: { enabled: true, objective: "GOAL_OBJECTIVE", summary: "GOAL_SUMMARY" } });
out.work_runtime_plan = workRuntimePrompt({ ...project, mode: "work", activeWorkPlan: { steps: "PLAN_STEPS" } });
out.code_runtime = workRuntimePrompt({ ...project, mode: "code" });
`;
const rq = src.match(/<runtime_quality>[\s\S]*?<\/runtime_quality>/);
sandbox.out.runtime_quality = rq ? rq[0].replace(/"\s*\+\s*\n\s*"/g, "") : "";
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
process.stdout.write(JSON.stringify(sandbox.out));
