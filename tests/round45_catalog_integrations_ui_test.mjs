import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const google = fs.readFileSync(new URL("../app/pgstatic/gdrive-picker.js", import.meta.url), "utf8");
for (const lang of ["ru", "en"]) {
  const context = vm.createContext({window:{}, PG_LANG:lang, setTimeout:()=>0,
    fetch:async()=>({ok:true,json:async()=>({state:"ready",account:{email:"private-account@example.com"}})})});
  vm.runInContext(google, context);
  await context.window.MemuDrivePick.refreshStatus();
  const hint = context.window.MemuDrivePick.menuHint();
  assert(!hint.includes("private-account"));
  assert(!hint.includes("@"));
  assert(hint.includes(lang === "ru" ? "Диска" : "Drive"));
}

const app = fs.readFileSync(new URL("../app/pgstatic/app.js", import.meta.url), "utf8");
const block = app.slice(app.indexOf("let integrationsCache ="), app.indexOf("function renderIntegrationsState()"));
let connected = false;
const context = vm.createContext({window:{}, renderIntegrationsState(){},
  api:async()=>({services:[{service:"telegram_use",title:"Telegram Use",connected,
    tools:["telegram_use_history{chat,limit}"],hint:"Use a resolved chat id"}]})});
vm.runInContext(block, context);
await vm.runInContext("refreshIntegrations()", context);
assert(vm.runInContext("integrationsPromptBlock()", context).includes("Not connected: Telegram Use"));
connected = true;
await vm.runInContext("refreshIntegrations()", context);
const prompt = vm.runInContext("integrationsPromptBlock()", context);
assert(prompt.includes("Connected accounts: Telegram Use"));
assert(prompt.includes("telegram_use_history{chat,limit}"));
assert(!prompt.includes("Not connected: Telegram Use"));

assert(app.includes('!/^<[^>]+_report>/i.test(String(raw || "").trim()))) await refreshIntegrations()'));
console.log("PASS: private composer hint; Telegram login updates actual prompt tool availability in RU / EN.");
