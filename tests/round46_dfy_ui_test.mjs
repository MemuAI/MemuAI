
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"build/audit-js/package.json"));
const {JSDOM}=require("jsdom");
const dom=new JSDOM('<html lang="en"><body><button id="origin">Start</button></body></html>',{url:"http://127.0.0.1:8132",runScripts:"outside-only"});
const w=dom.window;w.PG_LANG="en";w.setInterval=()=>1;w.clearInterval=()=>{};
const requests=[];let opted=true,connected=true;
w.fetch=async(url,init={})=>{
  if(url==="/api/dfy/status")return {ok:true,json:async()=>({enabled:true,ready:true,model:"vision",browser_choice_required:opted,browser_choices:[
    {id:"isolated",name:"Separate DFY browser",available:true,reason:""},
    {id:"chrome",name:"My Chrome",available:connected,reason:connected?"":"Open the connected Chrome extension"}]})};
  if(init.method==="POST"){requests.push(JSON.parse(init.body));return {ok:true,json:async()=>({id:"task",status:"queued"})};}
  return {ok:true,json:async()=>({tasks:[]})};
};
w.eval(fs.readFileSync(path.join(root,"app/pgstatic/dfy-client.js"),"utf8"));
const flush=async()=>{for(let i=0;i<4;i++)await new Promise(r=>setTimeout(r,0));};
const start=(source="chat",extra={})=>w.MemuDFY.start("Check an order",[],source,extra);
let running=start();await flush();
assert(w.document.querySelector(".dfy-browser-choice"));assert.equal(requests.length,0,"No task starts before the choice");
w.document.querySelector('[data-browser="chrome"]').click();await running;
assert.equal(requests.at(-1).browser,"chrome");
running=start("quick");await flush();assert(w.document.querySelector(".dfy-browser-choice"),"Next invocation asks again");
w.document.querySelector('[data-browser="isolated"]').click();await running;
assert.equal(requests.at(-1).browser,"isolated");
const before=requests.length;
running=start("link");const cancelled=assert.rejects(running,e=>e.code==="dfy_cancelled");await flush();
w.document.querySelector('[data-cancel]').click();await cancelled;
assert.equal(requests.length,before,"Cancel never posts a task");
running=start("chat",{browser:"chrome",target:"browser"});await flush();
assert(w.document.querySelector(".dfy-browser-choice"),"An agent browser hint cannot bypass a user choice");
w.document.querySelector('[data-browser="isolated"]').click();await running;
assert.equal(requests.at(-1).browser,"isolated");
connected=false;running=start();await flush();
assert(w.document.querySelector('[data-browser="chrome"]').disabled);
assert(w.document.body.textContent.includes("Open the connected Chrome extension"));
w.document.querySelector('[data-browser="isolated"]').click();await running;
await start("schedule",{browser:"chrome"});assert(!w.document.querySelector("dialog"));
await start("chat",{target:"server"});assert(!w.document.querySelector("dialog"));
assert.equal(requests.at(-1).target,"server");
opted=false;await start();assert(!w.document.querySelector("dialog"));
assert(!Object.hasOwn(requests.at(-1),"browser"),"Server uses the isolated default without Chrome opt-in");
dom.window.close();
console.log("Round46 DFY UI: 8 scenarios passed");
