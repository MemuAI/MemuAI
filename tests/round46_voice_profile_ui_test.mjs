import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
const require = createRequire(new URL('../build/audit-js/package.json', import.meta.url));
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(new URL('../app/pgstatic/voice-settings.js', import.meta.url), 'utf8');
for (const en of [false, true]) {
  const dom = new JSDOM('<body></body>');
  const context = vm.createContext({window:{},document:dom.window.document,URL});
  vm.runInContext(source, context);
  const settings = context.window.MemuVoiceSettings;
  const prefs = {voice_stt_api_url:'https://api.openai.com/v1',voice_stt_api_model:'original-model',voice_stt_api_key:'openai-key',voice_stt_api_profiles:{}};
  const esc = value => String(value ?? '').replaceAll('"','&quot;');
  const K = {field:row=>row.control,row:row=>row.control,note:row=>row,
    secret:(id,value)=>`<input id="${id}" value="${esc(value)}">`,
    select:(id,options,value)=>`<select id="${id}">${options.map(([key,label])=>`<option value="${key}" ${value===key?'selected':''}>${label}</option>`).join('')}</select>`};
  let fail = false, renders = 0;
  const ctx = {en,K,esc,pref:(key,fallback)=>prefs[key] ?? fallback,
    catalog:{stt_api_providers:[{id:'openai',label:'OpenAI',models:['suggested-model'],default_model:'suggested-model',base_url:'https://api.openai.com/v1'},
      {id:'custom',label:'Custom',models:[],base_url:'',default_model:''}]},
    savePreferences:async patch=>{Object.assign(prefs,patch);return fail ? null : prefs;},rerender:draw};
  function draw(){++renders;dom.window.document.body.innerHTML=settings.sttApiHtml(ctx);settings.bindStt(ctx);}
  const field = id => dom.window.document.getElementById('st-stt-'+id);
  draw();

  field('url').value='https://own-gateway.example/v1';
  field('key').value='gateway-key';
  field('model').value='gateway-model';
  await field('url').onchange();
  assert.equal(prefs.voice_stt_api_profiles.openai.url,'https://api.openai.com/v1');
  assert.equal(prefs.voice_stt_api_profiles.openai.key,'openai-key');
  assert.equal(prefs.voice_stt_api_profiles.openai.model,'original-model');
  assert.equal(prefs.voice_stt_api_profiles.custom.model,'gateway-model');
  field('preset').value='openai';await field('preset').onchange();
  assert.equal(field('preset').value,'openai');
  assert.equal(field('url').value,'https://api.openai.com/v1');
  assert.equal(field('key').value,'openai-key');
  assert.equal(field('model').value,'original-model');
  field('preset').value='custom';await field('preset').onchange();
  assert.equal(field('url').value,'https://own-gateway.example/v1');
  assert.equal(field('key').value,'gateway-key');
  assert.equal(field('model').value,'gateway-model');

  fail=true;
  const before=renders;
  field('url').value='https://api.openai.com/v1';
  field('model').value='unsaved-model';
  await field('url').onchange();
  assert.equal(renders,before);
  assert.equal(field('model').value,'unsaved-model');
}
console.log('PASS: RU/EN STT manual URL changes preserve native/custom profiles and pending key/model edits; save failures keep controls intact.');
