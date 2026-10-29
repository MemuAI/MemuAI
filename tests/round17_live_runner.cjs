

const fs=require("fs"), vm=require("vm"), assert=require("assert"), path=require("path");
const root=path.resolve(__dirname,".."), app=fs.readFileSync(path.join(root,"app/pgstatic/app.js"),"utf8");
function fn(name) {
  const start=app.search(new RegExp("(?:async )?function "+name+"\\(")); assert(start>=0,name);
  const tail=app.slice(start), next=tail.slice(1).search(/\n(?:async )?function \w+\(/);
  return next<0?tail:tail.slice(0,next+1);
}
const ctx=vm.createContext({Uint8Array,DataView,Map,Set,Date,Math,PG_LANG:"en",pref:(_,fallback)=>fallback,
  btoa:s=>Buffer.from(s,"binary").toString("base64"),atob:s=>Buffer.from(s,"base64").toString("binary")});
let source=fs.readFileSync(path.join(root,"app/pgstatic/realtime.js"),"utf8");
source=source.replace("start, stop, setMuted, interrupt, sendText, attachOrb","testing:{session,onEvent,recordInput,commit}, start, stop, setMuted, interrupt, sendText, attachOrb");
vm.runInContext(source+"\nglobalThis.live=MemuRealtime.testing",ctx);
const live=ctx.live, rows=new Map();
function reset(protocol="gemini-live") {
  rows.clear(); Object.assign(live.session,{turns:{user:null,assistant:null},user:"",assistant:"",found:[],sources:new Set(),inputPreroll:[],inputPrerollBytes:0,providerTurns:new Map(),inputRate:24000,outputRate:24000,info:{protocol,output_rate:24000},state:"listening",nextTime:0,
    hooks:{onTurn:(role,text,extra)=>rows.set(extra.turnId,{...(rows.get(extra.turnId)||{}),role,text,...extra})},gain:{},
    player:{currentTime:0,createBuffer:(_,count)=>({duration:count/24000,getChannelData:()=>new Float32Array(count)}),createBufferSource:()=>({connect(){},start(){},stop(){}})}});
}
function sample(seconds,amplitude=.004){const count=Math.round(seconds*24000),bytes=new Uint8Array(count*2),view=new DataView(bytes.buffer);for(let n=0;n<count;n++)view.setInt16(n*2,Math.round(Math.sin(n*2*Math.PI*440/24000)*amplitude*32767),true);return bytes;}
const bot=sample(.1,.1), bot64=Buffer.from(bot).toString("base64");
function verifyWav(clip,expected,description){assert(clip,description);const bytes=Buffer.from(clip.data.split(",")[1],"base64");assert.equal(bytes.toString("ascii",0,4),"RIFF");assert.equal(bytes.readUInt32LE(24),24000);assert.equal(bytes.readUInt32LE(40),expected.length);assert.deepEqual(bytes.subarray(44),Buffer.from(expected),description);}

reset("openai-realtime"); live.onEvent({type:"speech_started",turn_id:"tiny"});const tiny=sample(.22);
live.recordInput(tiny.buffer,.004);live.onEvent({type:"speech_stopped",turn_id:"tiny"});
live.onEvent({type:"audio",data:bot64,turn_id:"reply"});live.onEvent({type:"user_transcript",text:"Hi",final:true,turn_id:"tiny"});live.onEvent({type:"turn_complete"});
assert.equal(rows.size,2);verifyWav([...rows.values()].find(x=>x.role==="user").audio,tiny,"all 220ms preserved after late transcript");

reset();live.recordInput(tiny.buffer,.004);live.onEvent({type:"audio",data:bot64});assert.equal([...rows.values()].filter(x=>x.role==="user").length,0);
live.onEvent({type:"user_transcript",text:"Hi",final:true});live.onEvent({type:"turn_complete"});
verifyWav([...rows.values()].find(x=>x.role==="user").audio,tiny,"quiet input recovered from frozen pre-answer PCM");
const first=[...rows.values()].find(x=>x.role==="user");
const next=sample(.34,.003);live.recordInput(next.buffer,.003);live.onEvent({type:"user_transcript",delta:"Another greeting"});live.onEvent({type:"audio",data:bot64});live.onEvent({type:"turn_complete"});
const users=[...rows.values()].filter(x=>x.role==="user");assert.equal(users.length,2,"next quiet utterance must not merge into prior caption");assert.notEqual(users[0].turnId,users[1].turnId);verifyWav(users[1].audio,next,"second quiet utterance retains its own audio");assert.equal(users[0].text,first.text);

reset();live.recordInput(sample(10).buffer,.001);assert(live.session.inputPrerollBytes<=24000*2*8,"fallback microphone memory bounded even for oversized worklet fixture");live.onEvent({type:"audio",data:bot64});live.onEvent({type:"turn_complete"});assert.equal([...rows.values()].filter(x=>x.role==="user").length,0,"silence/noise never becomes a blank voice message");

let prefs={voice_live_primary_personality:"romantic",voice_live_secondary_personalities:["witty","calm","coach"],voice_live_custom_personality:"Use dry affectionate humour, no pet names.",voice_live_name:"Astra",voice_live_gender:"female",ai_personality:"pragmatic"};
const pc=vm.createContext({PG_LANG:"ru",pref:(key,fallback)=>key in prefs?prefs[key]:fallback,promptData:s=>String(s),S:{messages:[],chatSystem:"Answer accurately."},Date,
  internalPromptText:()=>"Speak naturally, warmly and concisely.",activeMeme:()=>null,profileReferenceContext:()=>"",systemPromptContext:s=>({...s}),isCodeMode:()=>false,isWorkMode:()=>false,
  memePromptBlock:()=>"",prefLocked:()=>false});
const a=app.indexOf("const LIVE_PERSONALITIES = ["),b=app.indexOf("let voiceCatalog",a);vm.runInContext(app.slice(a,b)+fn("buildLivePersonalityInstructions")+"\n"+fn("buildRealtimeInstructions"),pc);
const persona=pc.buildLivePersonalityInstructions(),realtime=pc.buildRealtimeInstructions();assert(realtime.startsWith(persona));assert(persona.includes("tender, personal and gently playful"));assert(persona.includes("wording, emotional intensity, rhythm, initiative and questions"));assert(persona.includes("dry affectionate humour"));assert(persona.includes("Use feminine self-reference"));assert(persona.includes("PRIMARY BEHAVIOR"));assert(persona.includes("SECONDARY ACCENTS"));assert(!persona.includes("motivational slogans"),"third secondary accent is excluded");assert(!persona.includes("pragmatic"));assert(persona.includes("consenting adults"));assert(persona.includes("Do not announce your personality"));
for(const id of ["warm","therapist","storyteller","challenger","charismatic","witty","coach","teacher","calm","creative","romantic","concise"]){prefs.voice_live_primary_personality=id;const p=pc.buildLivePersonalityInstructions();assert(p.includes('primary="'+id+'"'));assert(p.includes("TONE EXAMPLE"));}
prefs.voice_live_primary_personality="romantic";pc.PG_LANG="en";assert(pc.buildLivePersonalityInstructions().includes("Romantic (18+)"));assert(pc.buildLivePersonalityInstructions().includes("TONE EXAMPLE in English"));
const classic=fn("buildSystemPrompt");assert(classic.includes("${buildLivePersonalityInstructions()}"),"classic STT/chat/TTS uses same behavior builder");

const models=vm.createContext({S:{models:[{id:"song",caps:{music:true,text:false,vision:true}},{id:"speech-chat",caps:{audio:true,text:true,vision:true}},{id:"multimodal-music-chat",caps:{music:true,text:true}}]},pref:(key,fallback)=>key==="hidden_models"?["multimodal-music-chat"]:fallback,isImageModel:()=>false});
vm.runInContext(fn("modelCaps")+"\n"+fn("isMusicOnlyModel")+"\n"+fn("visibleModels")+"\n"+fn("defaultVisionModel"),models);
assert(models.isMusicOnlyModel("song"));assert(!models.isMusicOnlyModel("speech-chat"));assert(!models.isMusicOnlyModel("multimodal-music-chat"));
assert.equal(models.defaultVisionModel(),"speech-chat","music-only models cannot handle a vision fallback");
assert.deepEqual(Array.from(models.visibleModels(),x=>x.id),["song","speech-chat"],"picker keeps specialized models and honors hidden models");
console.log("Round17 Live PASS: quiet short WAV, late transcript, quiet next turn, bounded fallback, no blank audio, 12 behavioral personas, shared classic/native instructions, music-only filtering");
