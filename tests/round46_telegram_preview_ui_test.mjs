import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"build/audit-js/package.json"));
const {JSDOM}=require("jsdom");
const source=fs.readFileSync(path.join(root,"app/pgstatic/integrations-ui.js"),"utf8");
const flush=async()=>{for(let i=0;i<6;i++)await new Promise(resolve=>setTimeout(resolve,0));};
function setup(lang="ru") {
  const dom=new JSDOM('<html><body></body></html>',{url:"http://127.0.0.1:8600",runScripts:"outside-only"});
  const w=dom.window; w.PG_LANG=lang;
  w.panel=(html,cls,options)=>{w.document.body.innerHTML=`<section class="${cls}">${html}</section>`;w.__close=options.onClose;};
  w.closePanel=()=>{w.document.body.replaceChildren();};
  w.eval(source);return {w,dom};
}
let passed=0;
async function check(name,fn){await fn();passed++;console.log("PASS "+name);}
await check("Telegram send shows recipient and formatted multiline content, not JSON",async()=>{
  const {w,dom}=setup();let submitted;
  w.fetch=async(url,options)=>{submitted=JSON.parse(options.body);return {ok:true,json:async()=>({parts:['<b>Привет</b>\n\n<i>Курсив</i> <code>literal \\n</code><pre>print(1)\nprint(2)</pre>']})};};
  const promise=w.MemuIntegrations.confirm("telegram_use_send",{chat:"@ivan",text:"**Привет**\n\n*Курсив*"});
  assert.equal(w.document.querySelector("#gi-confirm-yes").disabled,true);
  await flush();assert.equal(submitted.text,"**Привет**\n\n*Курсив*");
  assert.match(w.document.querySelector(".gi-confirm-rows").textContent,/@ivan/);
  assert.equal(w.document.querySelector("#gi-confirm-body b").textContent,"Привет");
  assert.match(w.document.querySelector("#gi-confirm-body").textContent,/Привет\n\nКурсив/);
  w.document.querySelector("#gi-confirm-yes").click();assert.equal(await promise,true);dom.window.close();
});
await check("Preview sanitizes markup, attributes and active link schemes",async()=>{
  const {w,dom}=setup("en");w.fetch=async()=>({ok:true,json:async()=>({parts:['<b onclick="evil()">safe</b><img src="https://attacker.test"><script>evil()</script><a href="javascript:evil()">bad</a><a href="https://example.test" onmouseover="evil()">good</a>']})});
  const promise=w.MemuIntegrations.confirm("telegram_use_send",{chat:"Ivan",text:"safe"});await flush();
  const body=w.document.querySelector("#gi-confirm-body");assert.equal(body.querySelector("img,script,[onclick],[onmouseover]"),null);
  assert.equal(body.querySelector("a").hasAttribute("href"),false);assert.equal(body.querySelectorAll("a")[1].rel,"noopener noreferrer");
  w.document.querySelector("#gi-confirm-no").click();assert.equal(await promise,false);dom.window.close();
});
await check("Cancel ignores a late preview and does not reopen the confirmation",async()=>{
  const {w,dom}=setup();let resolve;w.fetch=()=>new Promise(done=>resolve=done);
  const promise=w.MemuIntegrations.confirm("telegram_use_send",{chat:"Ivan",text:"Text"});w.document.querySelector("#gi-confirm-no").click();
  assert.equal(await promise,false);resolve({ok:true,json:async()=>({parts:["late"]})});await flush();assert.equal(w.document.body.childElementCount,0);dom.window.close();
});
await check("Preview failures keep send disabled and leave cancellation available",async()=>{
  const {w,dom}=setup();w.fetch=async()=>({ok:false,status:400,json:async()=>({error:"Invalid text"})});
  const promise=w.MemuIntegrations.confirm("telegram_use_send",{chat:"Ivan",text:"Text"});await flush();
  assert.equal(w.document.querySelector("#gi-confirm-yes").disabled,true);assert.match(w.document.querySelector("#gi-preview-status").textContent,/Invalid text/);
  w.document.querySelector("#gi-confirm-no").click();assert.equal(await promise,false);dom.window.close();
});
await check("Caption trimming, multipart messages and non-text operations are explicit",async()=>{
  const {w,dom}=setup("en");let submitted;w.fetch=async(url,options)=>{submitted=JSON.parse(options.body);return {ok:true,json:async()=>({parts:["caption"],truncated:true})};};
  let promise=w.MemuIntegrations.confirm("telegram_use_send_file",{chat:"Ivan",path:"report.pdf",caption:"long caption"});await flush();
  assert.equal(submitted.kind,"caption");assert.match(w.document.querySelector("#gi-preview-status").textContent,/shortened/);
  w.document.querySelector("#gi-confirm-no").click();await promise;
  promise=w.MemuIntegrations.confirm("telegram_use_delete",{chat:"Ivan",message_ids:[1,2],revoke:true});
  assert.equal(w.document.querySelector("#gi-confirm-yes").disabled,false);assert.equal(w.document.querySelector(".gi-confirm").classList.contains("danger"),true);
  w.document.querySelector("#gi-confirm-no").click();await promise;dom.window.close();
});
await check("Telegram has its own badge and whitespace-only file captions need no preview",async()=>{
  const {w,dom}=setup("en");let calls=0;w.fetch=async()=>{calls++;throw new Error("Blank captions must not request preview");};
  const promise=w.MemuIntegrations.confirm("telegram_use_send_file",{chat:"Ivan",path:"report.pdf",caption:" \n\t "});
  assert(w.document.querySelector(".gi-confirm-head .gi-brand svg circle"));
  assert(w.document.querySelector(".gi-confirm-head .gi-brand svg path"));
  assert.equal(w.document.querySelector("#gi-confirm-body"),null);
  assert.equal(w.document.querySelector("#gi-preview-status"),null);
  assert.equal(w.document.querySelector("#gi-confirm-yes").disabled,false);
  assert.equal(calls,0);w.document.querySelector("#gi-confirm-yes").click();assert.equal(await promise,true);dom.window.close();
});
console.log(`${passed} Telegram preview UI checks passed`);
