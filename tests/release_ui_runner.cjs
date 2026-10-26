const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = process.argv[2] || process.cwd();
const js = fs.readFileSync(path.join(root, 'app/pgstatic/app.js'), 'utf8');
function section(from, to) { const start=js.indexOf(from),end=js.indexOf(to,start+from.length); assert(start>=0 && end>start,from); return js.slice(start,end); }
const writes=[];
const S={currentId:'temp-A',tempChat:true,mode:'chat',messages:[],chatCaps:{},chatSystem:'instructions A',workspaces:{active_id:'project-A',items:[]},workGoal:{enabled:false},activeWorkPlan:null,memory:{enabled:true,facts:[]},chats:[],models:[{id:'model-A'}],imageModels:[],prompts:{prompts:[],disabled_for:[]},preferences:{},profileState:{active_id:'profile-A'}};
const ctx={S,profileSwitching:false,assistantSources:new WeakMap(),PG_LANG:'en',MEM_PROMPT:'base',WORK_TOOLS_PROMPT:'worktools',EDIT_TOOLS_PROMPT:'edittools',CODE_MODE_PROMPT:'code',WORK_EXECUTION_FALLBACK:'workexec',LIVE_PERSONALITIES:[],TOOL_ROUND_LIMIT:12,crypto:require('crypto').webcrypto,DOMException,
  activeChatProfileId:()=>S.profileState.active_id,activeMeme:()=>null,currentProfile:()=>({name:'name',bio:'bio'}),currentModel:()=>S.models[0].id,workAccessMode:()=> 'full',isProjectMode:(mode=S.mode)=>['work','code'].includes(mode),
  api:async(p,options)=>{writes.push({p,...options});return{};},toast:()=>{},memeText:(ru,en)=>en,promptData:String,internalPromptText:()=>'',pref:(key,fallback)=>S.preferences[key]??fallback,getSkills:()=>[],mcpPromptBlock:()=>'',browserSkillModel:()=>'',computerUseSkillModel:()=>'',updateWorkUI:()=>{},parseToolBlocks:text=>({text,calls:[],errors:[]}),bareToolCalls:()=>[],extractForeignSearchCalls:text=>text,renderMessages:()=>{},persistChatSnapshot:(id,messages)=>writes.push({snapshot:id,messages}),askConfirm:async()=>true,setTimeout:()=>0,handlePostGeneration:()=>{}};
vm.createContext(ctx);
vm.runInContext(section('function profileReferenceContext(', '/* Текст внутреннего промпта'),ctx);
vm.runInContext(section('function buildSystemPrompt(', 'window.__m_messages'),ctx);
vm.runInContext(section('function sourceProfileActive(', '/* Часы ответа'),ctx);
vm.runInContext(section('function extractMemFacts(', '/* Разбор скрытых блоков'),ctx);
vm.runInContext(section('function parseWorkBlocks(', 'function previewWorkActions('),ctx);
vm.runInContext(section('async function extractSchedules(', 'function scheduleTiming('),ctx);
const temporary=ctx.captureChatSource();
S.currentId='chat-B';S.tempChat=false;
assert.strictEqual(ctx.extractMemFacts('[MEM: private fact]',temporary),'');
assert.deepStrictEqual(S.memory.facts,[]);
const schedule='```schedule\n'+JSON.stringify({name:'task',prompt:'task prompt',interval_minutes:10,mode:'work'})+'\n```';
(async()=>{
  const tmpAssistant={model:'model-A'}; await ctx.extractSchedules(schedule,tmpAssistant,temporary);assert(!tmpAssistant.scheduleActions);
  S.currentId='chat-A';S.mode='work';S.messages=[{role:'assistant',model:'model-A'}];S.workGoal={enabled:true,status:'active'};S.activeWorkPlan={status:'active',steps:[{id:1,status:'pending'}]};
  const source=ctx.captureChatSource(); source.model='model-A'; source.job={controller:new AbortController()};
  S.currentId='chat-B';S.tempChat=true;S.mode='chat';S.messages=[{role:'assistant',content:'unrelated'}];S.workspaces={active_id:'project-B',items:[]};S.workGoal={enabled:true,status:'active'};S.activeWorkPlan={status:'active',steps:[{id:1,status:'pending'}]};S.chatSystem='instructions B';
  ctx.extractMemFacts('[MEM: persistent fact]',source);assert.deepStrictEqual(S.memory.facts,['persistent fact']);
  const assistant=source.messages[0];await ctx.extractSchedules(schedule,assistant,source);assert.strictEqual(assistant.scheduleActions.tasks[0].project_id,'project-A');assert.strictEqual(assistant.scheduleActions.tasks[0].mode,'work');
  ctx.extractWorkActions('```memuai-work\n{"path":"README.md"}\nA content\n```',assistant,source);assert.strictEqual(assistant.workActions.project_id,'project-A');
  const protocol='```memuai-plan\n{"step":1,"status":"complete","evidence":"verified A"}\n```\n```memuai-goal\n{"status":"complete","summary":"A done"}\n```\n```memuai-command\n{"command":"npm test"}\n```\n```memuai-delete\n{"paths":["old.txt"]}\n```';
  ctx.extractWorkProtocols(protocol,assistant,source);assert.strictEqual(source.activeWorkPlan.steps[0].status,'complete');assert.strictEqual(S.activeWorkPlan.steps[0].status,'pending');assert.strictEqual(source.workGoal.enabled,false);assert.strictEqual(S.workGoal.enabled,true);assert.strictEqual(assistant.workCommand.project_id,'project-A');assert.strictEqual(assistant.workDeletes.project_id,'project-A');
  const prompt=ctx.buildSystemPrompt(source);assert(prompt.includes('instructions A'));assert(!prompt.includes('instructions B'));assert(prompt.includes('project-A'));assert(!prompt.includes('temporary_chat'));
  await ctx.applyWorkActions(0,false,source);const write=writes.find(x=>x.p==='/api/work/write');assert.strictEqual(write.body.project_id,'project-A');assert.strictEqual(write.body.session,'chat-A');assert.strictEqual(S.messages[0].content,'unrelated');
  await ctx.applyWorkDeletes(0,false,source);const del=writes.find(x=>x.p==='/api/work/delete');assert.strictEqual(del.body.project_id,'project-A');assert.strictEqual(del.body.session,'chat-A');
  const before=writes.length;S.profileState.active_id='profile-B';ctx.extractMemFacts('[MEM: old profile fact]',source);await ctx.extractSchedules(schedule,{},source);await ctx.applyWorkActions(0,false,source);assert.strictEqual(writes.length,before);

  class Socket { static OPEN=1;constructor(){this.readyState=0;this.sent=[];this.closed=false;Socket.instances.push(this);}send(s){this.sent.push(JSON.parse(s));}close(){this.closed=true;this.readyState=3;} } Socket.instances=[];
  const media=[];let microphoneResolve;let workletResolve;let delayWorklet=false;
  class Audio { constructor(){this.currentTime=0;this.destination={};this.audioWorklet={addModule:()=>delayWorklet?new Promise(r=>workletResolve=r):Promise.resolve()};}createGain(){return{gain:{value:1},connect:()=>({connect:()=>{}})};}createAnalyser(){return{connect:()=>{},fftSize:0};}createMediaStreamSource(){return{connect:()=>{}};}close(){this.closed=true;return Promise.resolve();} }
  class Node {constructor(){this.port={close(){},postMessage(){}};}connect(){return{connect(){}};}}
  const live={WebSocket:Socket,AudioContext:Audio,AudioWorkletNode:Node,DOMException,location:{protocol:'http:',host:'127.0.0.1:8600'},PG_LANG:'en',pref:(k,f)=>f,setTimeout:fn=>{fn();return 1;},clearInterval:()=>{},setInterval:()=>1,navigator:{mediaDevices:{getUserMedia:()=>new Promise(r=>microphoneResolve=r)}},document:{createElement:()=>({play:async()=>{},srcObject:null})},performance};vm.createContext(live);
  vm.runInContext(fs.readFileSync(path.join(root,'app/pgstatic/realtime.js'),'utf8')+'\nthis.rt=MemuRealtime;',live);
  const rt=live.rt;let errors=0,ready=0;
  await rt.start({onError:()=>errors++});const first=Socket.instances.at(-1);rt.stop();assert(first.closed);first.readyState=1;first.onopen();assert.strictEqual(first.sent.length,0);
  await rt.start({onError:()=>errors++,onReady:()=>ready++});const second=Socket.instances.at(-1);second.readyState=1;second.onopen();const pending=second.onmessage({data:JSON.stringify({type:'ready',input_rate:24000,output_rate:24000,video:'turn'})});rt.stop();const mic={stopped:false,stop(){this.stopped=true;}};microphoneResolve({getTracks:()=>[mic]});await pending;assert(mic.stopped);assert.strictEqual(rt.state,'idle');assert.strictEqual(ready,0);
  await rt.start({});const third=Socket.instances.at(-1);first.onclose();assert(rt.active);assert.strictEqual(rt.state,'connecting');rt.stop();assert(third.closed);
  delayWorklet=true;await rt.start({onReady:()=>ready++});const fourth=Socket.instances.at(-1);fourth.readyState=1;const pendingWorklet=fourth.onmessage({data:JSON.stringify({type:'ready'})});const mic2={stopped:false,stop(){this.stopped=true;}};microphoneResolve({getTracks:()=>[mic2]});await new Promise(r=>setImmediate(r));rt.stop();workletResolve();await pendingWorklet;assert(mic2.stopped);assert.strictEqual(ready,0);assert.strictEqual(errors,0);
  delayWorklet=false;await rt.start({onReady:()=>ready++});const fifth=Socket.instances.at(-1);fifth.readyState=1;const normal=fifth.onmessage({data:JSON.stringify({type:'ready',video:'turn'})});const mic3={stopped:false,stop(){this.stopped=true;}};microphoneResolve({getTracks:()=>[mic3]});await normal;assert.strictEqual(rt.state,'listening');assert.strictEqual(ready,1);
  const camera=rt.setVideo('camera');rt.stop();const track={stopped:false,stop(){this.stopped=true;}};microphoneResolve({getTracks:()=>[track],getVideoTracks:()=>[track]});await camera;assert(track.stopped);assert.strictEqual(rt.video.kind,'');assert(mic3.stopped);
  console.log('PASS: source temp/profile/project, memory/schedules, Work plan/goal/files/deletes, prompt context, Live connecting/microphone/worklet/stale-socket/camera and normal session.');
})().catch(e=>{console.error(e);process.exitCode=1;});
