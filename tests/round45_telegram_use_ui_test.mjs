
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import {createRequire} from "node:module";
const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"build/audit-js/package.json"));
const {JSDOM}=require("jsdom");
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0));};
const dom=new JSDOM('<html lang="en"><body></body></html>',{url:"http://127.0.0.1:8132",runScripts:"outside-only"});
const w=dom.window; w.PG_LANG="en";
w.MemuSettingsKit={link:o=>`<a>${o.label}</a>`,sub:o=>`<section>${o.body}</section>`};
w.MemuModelPicker={rows:()=>[{id:"chat"}],kindOf:()=>"chat",name:id=>id,enhanceSelect:()=>{}};
w.setInterval=()=>1;w.clearInterval=()=>{};
let saveTimer, timerId=0;
w.setTimeout=(fn)=>{saveTimer=fn;return ++timerId;}; w.clearTimeout=()=>{};
let state={connected:true,configured:true,has_api_hash:true,has_proxy:true,has_session:true,account:{name:"Test"},flow:{phase:"connected"},last_error:"",config:{enabled:true,api_id:123,model:"chat",allow_writes:true,schedule_writes:false,commands_enabled:true,context_chars:32000,max_media_mb:20,max_parallel:2,commands:[{name:"question",trigger:"!//question",kind:"question",enabled:true,prompt:"{question}",context_messages:30,output:"replace",allowed_users:[],allowed_chats:[],max_per_hour:10,max_per_day:50,cooldown_seconds:5,max_input_chars:8000,arguments:[]}]}};
const requests=[];
let pending=null,fail=false;
w.fetch=async(url,init={})=>{
  if(url.endsWith("voice-options"))return {ok:true,json:async()=>({stt_models:[{id:"small",name:"Whisper Small",downloaded:true}],cleanup_prompt:"Correct punctuation, preserve meaning."})};
  const body=init.body?JSON.parse(init.body):null;
  if(!body)return {ok:true,json:async()=>structuredClone(state)};
  requests.push(body);
  if(pending){const wait=pending;pending=null;await wait;}
  if(fail)return {ok:false,status:400,json:async()=>({error:"Invalid proxy"})};
  state={...state,config:{...state.config,...body},has_api_hash:!!(body.api_hash||state.has_api_hash)};
  delete state.config.api_hash;delete state.config.proxy;
  return {ok:true,json:async()=>structuredClone(state)};
};
w.eval(fs.readFileSync(path.join(root,"app/pgstatic/telegram-use.js"),"utf8"));
w.document.body.innerHTML=w.MemuTelegramUse.subsHtml();w.MemuTelegramUse.mount();await flush();
const f=key=>w.document.querySelector(`[data-tgu-field="${key}"]`);
const edit=(key,value,type="input")=>{const el=f(key);if(el.type==="checkbox")el.checked=value;else el.value=value;el.dispatchEvent(new w.Event(type,{bubbles:true}));return el;};
const autosave=async()=>{saveTimer?.();await flush();};
const status=()=>w.document.getElementById("tgu-save-status").textContent;
assert(w.document.body.textContent.includes("disabling"),"Website help recommends disabling VPN");
assert(w.document.body.textContent.includes("working VPN or configure a Telegram proxy"),"QR network help accounts for Russia and a working proxy");
assert(f("stt_source"));assert(f("voice_cleanup_model"));assert(f("voice_cleanup_prompt"));
edit("system_prompt","Keep it brief");assert.equal(requests.length,0,"typing is debounced");
await autosave();assert.equal(requests.at(-1).system_prompt,"Keep it brief");assert(status().includes("All changes saved"));
assert.equal(f("system_prompt").value,"Keep it brief","Autosave never replaces the form");

const hash="a".repeat(32);edit("api_hash",hash);edit("proxy","socks5://localhost:1080");
await autosave();assert.equal(requests.at(-1).api_hash,hash);assert.equal(requests.at(-1).proxy,"socks5://localhost:1080");
assert.equal(f("api_hash").value,"");assert.equal(f("proxy").value,"","Saved secrets are cleared locally");
edit("web_enabled",false,"change");await autosave();assert.equal(requests.at(-1).web_enabled,false);
assert(!Object.hasOwn(requests.at(-1),"proxy"),"Unrelated autosave keeps saved proxy");
assert.equal(requests.at(-1).api_hash,"","Blank secret never overwrites the saved hash");

let release;pending=new Promise(r=>{release=r;});
edit("system_prompt","old request");saveTimer();await flush();
edit("system_prompt","newer edit");release();await flush();
assert.equal(f("system_prompt").value,"newer edit");assert.equal(requests.at(-1).system_prompt,"newer edit","Edits during a save are flushed afterwards");

const before=requests.length;edit("api_hash","short");await autosave();
assert.equal(requests.length,before,"Incomplete credentials are never saved");assert(status().includes("Not saved"));
assert.equal(f("api_hash").getAttribute("aria-invalid"),"true");
edit("api_hash","");await autosave();

w.document.querySelector('[data-tgu="arg-add-0"]').click();await flush();
let arg=w.document.querySelector("[data-tgu-argument]");assert(arg);
let name=arg.querySelector('[data-tgu-arg-field="name"]');name.value="count";name.dispatchEvent(new w.Event("input",{bubbles:true}));
let type=arg.querySelector('[data-tgu-arg-field="type"]');type.value="integer";type.dispatchEvent(new w.Event("change",{bubbles:true}));
assert.equal(arg.dataset.kind,"integer");
let required=arg.querySelector('[data-tgu-arg-field="required"]');required.checked=true;required.dispatchEvent(new w.Event("change",{bubbles:true}));
await autosave();assert.equal(requests.at(-1).commands[0].arguments[0].type,"integer");assert.equal(requests.at(-1).commands[0].arguments[0].required,true);
w.document.querySelector('[data-tgu="arg-delete-0-0"]').click();await flush();await autosave();
assert.equal(requests.at(-1).commands[0].arguments.length,0);

const kind=f("kind");kind.value="summary";kind.dispatchEvent(new w.Event("change",{bubbles:true}));
assert.equal(w.document.querySelectorAll("[data-tgu-argument]").length,2,"Summary action creates its argument schema");
await autosave();assert.equal(requests.at(-1).commands[0].arguments[0].name,"count");

fail=true;edit("system_prompt","retry this");await autosave();assert(status().includes("Invalid proxy"));assert.equal(f("system_prompt").value,"retry this");
fail=false;w.document.querySelector('[data-tgu="save"]').click();await flush();assert.equal(requests.at(-1).system_prompt,"retry this");

state.connected=false;state.config.enabled=false;w.MemuTelegramUse.mount();await flush();
let releaseLogin;pending=new Promise(r=>{releaseLogin=r;});
edit("system_prompt","before login");w.document.querySelector('[data-tgu="qr"]').click();await flush();
assert.equal(requests.at(-1).action,"settings");assert.equal(requests.at(-1).enabled,true);
edit("voice_cleanup_prompt","preserve this concurrent edit");releaseLogin();await flush();
assert.equal(requests.at(-1).action,"qr","QR starts only after the settings flush succeeds");
assert.equal(f("voice_cleanup_prompt").value,"preserve this concurrent edit","Login does not replace pending edits");
await autosave();assert.equal(requests.at(-1).voice_cleanup_prompt,"preserve this concurrent edit");
assert.equal(requests.at(-1).api_hash,"","Concurrent setting edit never resends an old API hash");
assert.equal(requests.at(-1).enabled,true,"Autosave must not disable the account after QR setup enables it");
w.close();console.log("PASS Telegram Use Round45: debounce, pending edits, secrets, validation, retry, arguments, speech settings and VPN guidance");
