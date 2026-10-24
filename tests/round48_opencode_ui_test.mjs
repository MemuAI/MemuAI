import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../build/audit-js/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(new URL('../app/static/opencode-setup.js',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0));};
let passed=0;
function setup(lang,accountId,api){
  const dom=new JSDOM('<div class="field"><select id="x-mode"><option>free</option><option>zen</option></select></div><div class="field"><input id="x-api_key"></div>',{runScripts:'outside-only'});
  dom.window.eval(source);dom.window.MemuOpenCodeSetup.mount('x-',{api,lang,accountId});return dom;
}
for(const lang of ['ru','en']){
  const calls=[];
  const dom=setup(lang,'account/a',async(url,options)=>{
    calls.push({url,options});
    if(url.includes('opencode-probe'))return {http_status:429,message:'Публичный Free <b>не личная квота</b>',message_en:'Public Free <b>not personal quota</b>'};
    return {installed:true,supported:true,installing:false,version:'v1.4.3'};
  });
  await flush();const w=dom.window;
  assert.equal(calls.length,1,'opening account settings never probes generation');
  assert.match(w.document.querySelector('.oc-setup').textContent,lang==='ru'?/Вторая запись Free/:/Another Free entry/);
  const probe=w.document.querySelectorAll('.oc-actions')[1].querySelector('button');
  assert.equal(probe.disabled,false);probe.click();await flush();
  assert.equal(calls[1].url,'/api/admin/accounts/account%2Fa/opencode-probe');assert.equal(calls[1].options.body.confirmed,true);
  assert.match(w.document.querySelector('.oc-setup').textContent,/HTTP 429/);assert.equal(w.document.querySelector('.oc-setup b'),null);
  dom.window.close();passed++;
}
{
  let resolve;
  const dom=setup('ru','a',async(url)=>url.includes('opencode-probe')?new Promise(r=>resolve=r):{installed:true,supported:true});
  await flush();dom.window.document.querySelectorAll('.oc-actions')[1].querySelector('button').click();await flush();
  const mode=dom.window.document.querySelector('select');mode.value='zen';mode.dispatchEvent(new dom.window.Event('change'));
  resolve({message:'LATE PRIVATE RESULT',http_status:429});await flush();
  assert.equal(dom.window.document.body.textContent.includes('LATE PRIVATE RESULT'),false);dom.window.close();passed++;
}
{
  const dom=setup('ru','',async()=>({installed:true,supported:true}));await flush();
  assert.equal(dom.window.document.querySelectorAll('.oc-actions')[1].querySelector('button').disabled,true);dom.window.close();passed++;
}
console.log(`PASS ${passed} OpenCode generation diagnostic UI scenarios`);
