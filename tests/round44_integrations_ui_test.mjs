// Execute the actual settings UI against DOM/API doubles, without a browser.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";

const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"build/audit-js/package.json"));
const {JSDOM}=require("jsdom");
const flush=async()=>{for(let i=0;i<4;i++)await new Promise(resolve=>setTimeout(resolve,0));};
let tests=0;
const test=async(name,run)=>{await run();tests++;console.log("PASS "+name);};
function environment(){
  const dom=new JSDOM('<!doctype html><html><body><main id="test"></main></body></html>',{url:"http://127.0.0.1:8132/",runScripts:"outside-only"});
  dom.window.setInterval=()=>1;dom.window.clearInterval=()=>{};dom.window.PG_LANG="ru";
  dom.window.eval(readFileSync(path.join(root,"app/pgstatic/settings-kit.js"),"utf8"));
  return dom;
}

const browser=environment(),w=browser.window;
const requests=[];
let saved={ok:true,path:"C:\\Users\\Tester\\Downloads\\MemuAI\\MemuAI-Chrome.zip",name:"MemuAI-Chrome.zip",bytes:123};
let browserConfig={connected:false,paired:false,read_pages:true,control:"ask",dfy:false,schedules:false};
let denySetting=false,clipboard="";
Object.defineProperty(w.navigator,"clipboard",{value:{writeText:async text=>{clipboard=text;}}});
w.api=async(url,init)=>{
  requests.push({url,...init});
  if(url.endsWith("/status"))return {...browserConfig};
  if(url.endsWith("/download"))return saved;
  if(url.endsWith("/pairing"))return {code:"ABCDEF123456",expires_in:300};
  if(url.endsWith("/settings")){if(denySetting)throw new Error("settings failed");Object.assign(browserConfig,init.body);return {...browserConfig};}
  if(url.endsWith("/revoke")){browserConfig.paired=false;browserConfig.connected=false;return browserConfig;}
  if(url==="/api/pg/reveal-file")return {ok:true};
  throw new Error("Unexpected request: "+url);
};
w.eval(readFileSync(path.join(root,"app/pgstatic/browser-extension-settings.js"),"utf8"));
w.document.getElementById("test").innerHTML=w.MemuBrowserExtension.listHtml()+w.MemuBrowserExtension.subHtml();
w.MemuBrowserExtension.mount();await flush();
const click=async(action)=>{w.document.querySelector('[data-bext="'+action+'"]').click();await flush();};
await test("Chrome installation is three readable steps with an actual download button",async()=>{
  assert.equal(w.document.querySelectorAll(".bext-steps>li").length,3);
  assert.equal(w.document.querySelectorAll('a[download]').length,0);
  assert.equal(w.document.querySelectorAll('link[href^="/browser-extension-settings.css"]').length,1);
  assert(!w.document.body.textContent.includes("Сопрячь"));
});
await test("Download uses POST and exposes the saved path and reveal action",async()=>{
  await click("download");const req=requests.find(r=>r.url.endsWith("/download"));
  assert.equal(req.method,"POST");assert.equal(req.body.mode,"downloads");
  assert(w.document.getElementById("bext-feedback").textContent.includes(saved.path));
  assert.equal(w.document.querySelector('[data-bext="reveal"]').dataset.path,saved.path);
  await click("reveal");assert.equal(requests.at(-1).body.path,saved.path);
});
await test("Native save cancellation gives feedback without a false success",async()=>{
  saved={ok:false,cancelled:true};await click("save-as");
  assert.equal(requests.at(-1).body.mode,"ask");
  assert(w.document.getElementById("bext-feedback").textContent.includes("отменено"));
  assert.equal(w.document.querySelector('[data-bext="reveal"]'),null);
});
await test("Pairing code and address copy actions use natural connection language",async()=>{
  await click("pair");assert(w.document.getElementById("bext-code").textContent.includes("ABCDEF123456"));
  await click("copy-code");assert.equal(clipboard,"ABCDEF123456");
  await click("copy-address");assert.equal(clipboard,"http://127.0.0.1:8132");
});
await test("A rejected permission change restores the previously saved value",async()=>{
  denySetting=true;const input=w.document.querySelector('[data-bext-key="read_pages"]');
  input.checked=false;input.dispatchEvent(new w.Event("change",{bubbles:true}));await flush();
  assert.equal(input.checked,true);assert(!input.disabled);
  assert(w.document.getElementById("bext-feedback").classList.contains("is-error"));
});

const telegram=environment(),t=telegram.window;
let telegramState={
  config:{api_id:0,enabled:false,model:"",allow_writes:false,schedule_writes:false,commands_enabled:false,context_chars:32000,max_media_mb:20,max_parallel:2,commands:[]},
  connected:false,configured:false,has_api_hash:false,has_proxy:false,has_session:false,account:{},flow:{phase:"idle"},last_error:""
};
const telegramRequests=[];let failLogin=false;
t.fetch=async(url,init={})=>{
  const body=init.body?JSON.parse(init.body):null;
  telegramRequests.push(body);
  if(!body)return Response.json(telegramState);
  if(body.action==="settings"){
    telegramState={...telegramState,config:{...telegramState.config,...body},configured:!!(body.api_id&&(body.api_hash||telegramState.has_api_hash)),has_api_hash:!!(body.api_hash||telegramState.has_api_hash)};
    delete telegramState.config.api_hash;return Response.json(telegramState);
  }
  if(body.action==="qr"){
    if(failLogin)return Response.json({error:"Telegram asks you to wait",retry_after:120},{status:429});
    telegramState={...telegramState,flow:{phase:"qr",url:"tg://login?token=TEST"}};return Response.json(telegramState);
  }
  if(body.action==="cancel"){telegramState={...telegramState,flow:{phase:"idle"}};return Response.json(telegramState);}
  throw new Error("Unexpected Telegram action: "+body.action);
};
t.eval(readFileSync(path.join(root,"app/pgstatic/telegram-use.js"),"utf8"));
t.document.getElementById("test").innerHTML=t.MemuTelegramUse.subsHtml();
t.MemuTelegramUse.mount();await flush();
const tgclick=async(action)=>{t.document.querySelector('[data-tgu="'+action+'"]').click();await flush();};
const input=(key,value)=>{const element=t.document.querySelector('[data-tgu-field="'+key+'"]');element.value=value;return element;};
await test("Telegram setup explains credentials, QR requirements, existing apps and website rate limits",async()=>{
  assert.equal(t.document.querySelectorAll(".tgu-setup-step").length,2);
  assert(t.document.body.textContent.includes("Too many attempts"));
  assert(t.document.body.textContent.includes("один API ID на номер"));
  assert(t.document.body.textContent.includes("не указывает точное время ожидания"));
  assert(t.document.querySelector('a[href="https://core.telegram.org/api/qr-login"]'));
});
await test("Missing credentials fail next to the API ID field before making a request",async()=>{
  const before=telegramRequests.length;await tgclick("qr");
  assert.equal(telegramRequests.length,before);
  assert.equal(t.document.querySelector('[data-tgu-field="api_id"]').getAttribute("aria-invalid"),"true");
  assert.equal(t.document.getElementById("tgu-feedback").getAttribute("role"),"alert");
});
await test("Invalid API hash is rejected locally while preserving typed credentials",async()=>{
  input("api_id","12345");input("api_hash","invalid");const before=telegramRequests.length;
  await tgclick("qr");assert.equal(telegramRequests.length,before);
  assert.equal(t.document.querySelector('[data-tgu-field="api_hash"]').getAttribute("aria-invalid"),"true");
  assert.equal(t.document.querySelector('[data-tgu-field="api_id"]').value,"12345");
});
await test("Valid QR setup saves both credentials once then starts QR sign-in",async()=>{
  input("api_hash","a".repeat(32));const before=telegramRequests.length;await tgclick("qr");
  assert.deepEqual(telegramRequests.slice(before).map(r=>r.action),["settings","qr"]);
  assert.equal(telegramRequests[before].api_id,12345);assert.equal(telegramRequests[before].enabled,true);
  assert.equal(t.document.querySelector('[data-tgu-field="api_hash"]').value,"");
  assert(t.document.querySelector("#tgu-auth img").src.includes("/qr.png"));
});
await test("Changing API ID requires a corresponding hash instead of silently using the old one",async()=>{
  input("api_id","67890");const before=telegramRequests.length;await tgclick("qr");
  assert.equal(telegramRequests.length,before);
  assert.equal(t.document.querySelector('[data-tgu-field="api_hash"]').getAttribute("aria-invalid"),"true");
});
await test("Phone number is validated before a login code can be requested",async()=>{
  input("api_id","12345");input("phone","not-a-phone");const before=telegramRequests.length;
  await tgclick("phone");assert.equal(telegramRequests.length,before);
  assert.equal(t.document.querySelector('[data-tgu-field="phone"]').getAttribute("aria-invalid"),"true");
});
await test("Flood wait prevents repeated login requests and gives visible remaining time",async()=>{
  failLogin=true;await tgclick("qr");const after=telegramRequests.length;
  const qr=t.document.querySelector('[data-tgu="qr"]');assert(qr.disabled);
  const remaining=Number(t.document.getElementById("tgu-wait").textContent.match(/\d+/)[0]);
  assert(remaining>0&&remaining<=120);
  assert.equal(t.document.getElementById("tgu-wait").hidden,false);
  qr.click();await flush();assert.equal(telegramRequests.length,after);
});
await test("Adding a command preserves proxy-removal intent and opens the new editor",async()=>{
  const clear=t.document.querySelector('[data-tgu-field="clear_proxy"]');clear.checked=true;
  input("api_hash","b".repeat(32));await tgclick("add");
  assert(t.document.querySelector('[data-tgu-field="clear_proxy"]').checked);
  assert.equal(t.document.querySelector('[data-tgu-field="api_hash"]').value,"b".repeat(32));
  assert(t.document.getElementById("tgu-command-settings").open);
  assert(t.document.querySelector("[data-tgu-command]").open);
});
await test("Malformed allowed user IDs are reported instead of being silently discarded",async()=>{
  const ids=t.document.querySelector('[data-tgu-command] [data-tgu-field="allowed_users"]');
  ids.value="123, @ivan";const before=telegramRequests.length;
  await tgclick("save");assert.equal(telegramRequests.length,before);
  assert.equal(ids.getAttribute("aria-invalid"),"true");
  assert(t.document.getElementById("tgu-feedback").textContent.includes("числовые ID"));
});
browser.window.close();telegram.window.close();
console.log("PASS "+tests+" Round 44 integration UI tests");
