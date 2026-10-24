import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../build/audit-js/package.json',import.meta.url));
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(new URL('../app/static/opencode-setup.js',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(r=>setTimeout(r,0));};
let passed=0;
for(const lang of ['ru','en']) {
  const dom=new JSDOM('<div class="field"><select id="af-mode"><option>free</option><option>zen</option><option>go</option><option>gateway</option></select></div><div class="field"><input id="af-api_key"></div>',{runScripts:'outside-only'});
  const w=dom.window;w.eval(source);const calls=[];let installed=false;
  const api=async(url,options)=>{calls.push({url,options});if(options?.body.action==='install')installed=true;return {installed,supported:true,version:'v1.4.3',installing:false};};
  w.MemuOpenCodeSetup.mount('af-',{api,lang});await flush();
  assert.equal(calls.length,1);assert.equal(calls[0].options,undefined,'merely opening setup never downloads');
  assert.match(w.document.querySelector('.oc-setup').textContent,/Node.js/);
  w.document.querySelector('.oc-actions button').click();await flush();
  assert.equal(calls[1].options.body.action,'install');assert.equal(calls[1].options.body.confirmed,true);
  assert.equal(w.document.querySelector('.oc-actions button').disabled,true);passed++;
  const mode=w.document.querySelector('select'),key=w.document.querySelector('input');
  mode.value='zen';mode.dispatchEvent(new w.Event('change'));
  assert.equal(w.document.querySelector('.oc-setup a').href,'https://opencode.ai/auth');key.value='native-private-key';
  mode.value='gateway';mode.dispatchEvent(new w.Event('change'));
  assert.equal(key.value,'');assert.match(w.document.querySelector('.oc-setup').textContent,/10000/);assert.match(w.document.querySelector('.oc-setup').textContent,/8081/);
  key.value='local-gateway-key';mode.value='go';mode.dispatchEvent(new w.Event('change'));assert.equal(key.value,'native-private-key');passed++;
  assert.equal(w.document.querySelector('.oc-setup a').href,'https://opencode.ai/auth');
  mode.value='gateway';mode.dispatchEvent(new w.Event('change'));assert.equal(key.value,'local-gateway-key');passed++;
  for(const a of w.document.querySelectorAll('a'))assert.equal(a.rel,'noopener noreferrer');
  dom.window.close();
}
// Late status must not overwrite the instructions of another mode.
const dom=new JSDOM('<div class="field"><select id="x-mode"><option>free</option><option>go</option></select></div><input id="x-api_key">',{runScripts:'outside-only'});
const w=dom.window;w.eval(source);let resolve;
w.MemuOpenCodeSetup.mount('x-',{api:()=>new Promise(r=>resolve=r),lang:'ru'});
w.document.querySelector('select').value='go';w.document.querySelector('select').dispatchEvent(new w.Event('change'));
resolve({installed:true,supported:true});await flush();assert.equal(w.document.querySelector('.oc-actions'),null);assert.match(w.document.querySelector('.oc-setup').textContent,/подписка/);passed++;
dom.window.close();
console.log(`PASS ${passed} OpenCode setup UI scenarios (RU/EN, install opt-in, key isolation, stale replies)`);
