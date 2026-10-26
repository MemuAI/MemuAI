
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"build/audit-js/package.json"));
const {JSDOM}=require("jsdom");
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0));};
{
  const dom=new JSDOM('<html data-theme="light" style="--bg:#fbfaf8"><body></body></html>',{url:"http://offline.local",runScripts:"outside-only"});
  const w=dom.window,calls=[];
  w.pywebview={api:{window_backdrop:async(...args)=>(calls.push(args),{active:"none"})}};
  w.eval(fs.readFileSync(path.join(root,"app/pgstatic/appearance-plus.js"),"utf8"));
  w.MemuAppearancePlus.apply((_,fallback)=>fallback);await flush();
  assert.deepEqual(calls.at(-1),["none",false,"#fbfaf8"]);
  w.document.documentElement.style.setProperty("--bg","#17171a");
  w.document.documentElement.dataset.theme="dark";await flush();
  assert.deepEqual(calls.at(-1),["none",true,"#17171a"],"Opaque material=none updates native frame on theme switch");
  assert(!w.document.documentElement.dataset.backdrop,"Opaque CSS is preserved");
  dom.window.close();
}
{
  const dom=new JSDOM('<html data-module-app="music" data-theme="light" style="--bg:#ffffff"><body><div class="module-window" data-module="music"><div data-win="bar"></div><iframe></iframe></div></body></html>',{url:"http://offline.local",runScripts:"outside-only"});
  const w=dom.window,calls=[];w.CSS={escape:s=>s};
  w.pywebview={api:{appbar_state:async()=>({installed:true,os:"win11",maximized:false,active:true}),appbar_theme:async(...args)=>(calls.push(args),true)}};
  w.eval(fs.readFileSync(path.join(root,"app/pgstatic/module-appbar.js"),"utf8"));await flush();
  assert.deepEqual(calls.at(-1),[false,"#ffffff"]);
  w.MemuModuleAppbar.fromModule("music",{text:"#f4f1f6",background:"#060609",dark:true,caption:"off"});await flush();
  assert.deepEqual(calls.at(-1),[true,"#060609"],"Dark player owns native background on a light host");
  assert.equal(w.document.documentElement.style.getPropertyValue('--module-app-bg'),'#060609','HTML host cannot remain white behind a dark iframe');
  assert.equal(w.document.documentElement.style.colorScheme,'dark');
  w.dispatchEvent(new w.Event("resize"));await flush();
  assert.deepEqual(calls.at(-1),[true,"#060609"],"Resize cannot restore the host's white backing colour");
  w.MemuModuleAppbar.fromModule("music",{text:"#1d1b24",background:"#f5f2f9",dark:false});await flush();
  assert.deepEqual(calls.at(-1),[false,"#f5f2f9"]);
  assert.equal(w.document.documentElement.style.getPropertyValue('--module-app-bg'),'#f5f2f9');
  w.MemuModuleAppbar.fromModule("other",{background:"#ffffff",dark:false});await flush();
  assert.deepEqual(calls.at(-1),[false,"#f5f2f9"],"Other modules do not recolour this window");
  dom.window.close();
}
console.log("Round46 native-theme UI: 6 scenarios passed");
