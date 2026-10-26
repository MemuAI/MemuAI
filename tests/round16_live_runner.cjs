

const fs = require("fs"), vm = require("vm"), assert = require("assert");
const root = require("path").resolve(__dirname, "..");
const app = fs.readFileSync(root + "/app/pgstatic/app.js", "utf8");
function fn(name) {
  const start = app.search(new RegExp("(?:async )?function " + name + "\\("));
  assert(start >= 0, name);
  const tail = app.slice(start), next = tail.slice(1).search(/\n(?:async )?function \w+\(/);
  return next < 0 ? tail : tail.slice(0, next + 1);
}
const ctx = vm.createContext({ console, Uint8Array, DataView, Set, Map, Date, Math,
  PG_LANG: "en", pref: (_key, fallback) => fallback, performance: { now: () => 1000 },
  btoa: value => Buffer.from(value, "binary").toString("base64"),
  atob: value => Buffer.from(value, "base64").toString("binary"),
});
let src = fs.readFileSync(root + "/app/pgstatic/realtime.js", "utf8");
src = src.replace("start, stop, setMuted, interrupt, sendText, attachOrb", "testing: { session, onEvent, recordInput, commit, wavForTurn, audioTurn }, start, stop, setMuted, interrupt, sendText, attachOrb");
vm.runInContext(src + "\nglobalThis.testLive = MemuRealtime.testing;", ctx);
const live = ctx.testLive, messages = new Map(), captions = [];
function reset() {
  Object.assign(live.session, { turns: { user: null, assistant: null }, user: "", assistant: "", found: [], sources: new Set(),
    inputPreroll: [], inputPrerollBytes: 0, providerTurns: new Map(), inputRate: 24000, outputRate: 24000,
    hooks: { onTranscript: (role, text, extra) => captions.push({role,text,...extra}), onTurn: (role, text, extra) => {
      const prior = messages.get(extra.turnId) || {};
      messages.set(extra.turnId, { ...prior, role, text, ...extra });
    } }, state: "listening", nextTime: 0,
    player: { currentTime: 0, createBuffer: (_ch, count) => ({ duration: count / 24000, getChannelData: () => new Float32Array(count) }),
      createBufferSource: () => ({ connect() {}, start() {}, stop() {} }) }, gain: {},
  });
  messages.clear(); captions.length = 0;
}
const pcm = Buffer.alloc(4800, 1).toString("base64");
reset();
live.onEvent({ type: "speech_started", turn_id: "user-1" });
live.recordInput(new Uint8Array(4800).buffer, 0.1);
live.onEvent({ type: "audio", data: pcm, turn_id: "answer-1" });
live.onEvent({ type: "assistant_transcript", delta: "Hello", turn_id: "answer-1" });
live.onEvent({ type: "user_transcript", text: "Hi", final: true, turn_id: "user-1" });
live.onEvent({ type: "assistant_transcript", delta: " there", turn_id: "answer-1" });
live.onEvent({ type: "assistant_transcript", text: "Hello there", final: true, turn_id: "answer-1" });
live.onEvent({ type: "audio", data: pcm, turn_id: "answer-1" });
live.onEvent({ type: "turn_complete" });
assert.equal(messages.size, 2, "late user transcript must not split either role");
const user = [...messages.values()].find(m => m.role === "user"), answer = [...messages.values()].find(m => m.role === "assistant");
assert.equal(user.text, "Hi"); assert(user.audio); assert.equal(answer.text, "Hello there");
assert.equal(Buffer.from(answer.audio.data.split(",")[1], "base64").length, 44 + 9600, "last audio after transcript final belongs to same answer");
assert.equal(new Set(captions.filter(x => x.role === "assistant").map(x => x.turnId)).size, 1);
live.onEvent({ type: "speech_started", turn_id: "user-2" });
live.recordInput(new Uint8Array(4800).buffer, 0.1);
live.onEvent({ type: "user_transcript", text: "Hi corrected", final: true, turn_id: "user-1" });
live.onEvent({ type: "user_transcript", text: "Next", final: true, turn_id: "user-2" });
assert.equal(messages.size, 3); assert.equal(messages.get(user.turnId).text, "Hi corrected");
assert.equal(live.session.turns.user.text, "Next", "late prior item must not steal current input");
reset();
live.recordInput(new Uint8Array(4800).buffer, 0.1);
live.onEvent({ type: "audio", data: pcm });
live.onEvent({ type: "assistant_transcript", delta: "Hello" });
live.onEvent({ type: "user_transcript", text: "Hi", final: true });
live.onEvent({ type: "assistant_transcript", delta: " there" });
live.onEvent({ type: "turn_complete" });
assert.equal(messages.size, 2, "providers without item IDs preserve delayed text");
const oldCount = messages.size; live.onEvent({ type: "turn_complete" }); assert.equal(messages.size, oldCount);
reset(); live.session.info = {protocol:"gpt-live",output_rate:24000};
live.recordInput(new Uint8Array(4800).buffer, 0.1);
live.onEvent({type:"user_transcript",delta:"First"});
live.onEvent({type:"audio",data:pcm});
live.onEvent({type:"assistant_transcript",delta:"First answer"});
live.recordInput(new Uint8Array(4800).buffer, 0.1);
live.onEvent({type:"user_transcript",delta:"Second"});
live.onEvent({type:"audio",data:pcm});
live.onEvent({type:"assistant_transcript",delta:"Second answer"});
live.commit("assistant");
assert.equal(messages.size, 4, "full-duplex GPT-Live replies must not merge without turn-complete events");
assert.deepEqual([...messages.values()].filter(m=>m.role==="assistant").map(m=>m.text),["First answer","Second answer"]);
delete live.session.info;
reset();

live.onEvent({ type: "speech_started" });
live.recordInput(new Uint8Array(24000 * 2 * 181).buffer, 0.2);
live.commit("user");
const capped = [...messages.values()][0]; assert.equal(capped.audio.duration, 180); assert(capped.audio.truncated);

async function ownership() {
  let profile = "p1", sendCount = 0, playCount = 0, apiStep = "", resolver;
  const input = { value: "draft", dispatchEvent() {}, focus() {} };
  const nodes = new Map();
  const node = id => nodes.get(id) || (nodes.set(id, { textContent: "", disabled: false, classList: {add(){},remove(){}} }), nodes.get(id));
  const state = { currentId: "chat1", messages: [{content:"answer",liveResponse:true}], chats: [], tempChat: false };
  const c = vm.createContext({ console, S: state, PG_LANG:"en", profileSwitching:false,
    activeChatProfileId:()=>profile, $: id => id==="#input" ? input : node(id),
    currentModel:()=>"fixture-model",
    blobDataUrl: async () => "data:audio/webm;base64,AA==", voiceStatus(){}, toast(text){throw new Error(text);}, applyVoicePreferencesUI(){},
    send: async()=>{sendCount++;}, pref:(_key,fallback)=>fallback, liveSavedDraft:"draft",
    closeFloatingMenus(){}, isProjectMode:()=>false, voiceAudio:null, voiceAudioUrl:"", voiceSpeakingIndex:-1,
    releaseVoiceAudio(){}, updateSpeakButtons(){}, dataUrlBlob:()=>({}),
    URL:{createObjectURL:()=>"blob:fixture",revokeObjectURL(){}}, Audio:function(){this.play=async()=>{playCount++;};},
    api: async path => {
      if (path.endsWith("transcribe")) return {text:"Speech"};
      if (path.endsWith("clips")) {apiStep="clips"; await new Promise(resolve=>resolver=resolve); return {url:"/api/voice/clips/test.wav"};}
      if (path.endsWith("speak")) return {audio:"data:audio/wav;base64,AA=="};
    }, storeLiveAudio:async()=>{apiStep="store"; await new Promise(resolve=>resolver=resolve);},
  });
  vm.runInContext(fn("finishVoiceRecording") + "\n" + fn("speakMessage"), c);
  const waitFor = async step => {
    for (let i=0; i<100; i++) { if (apiStep===step) return; await new Promise(resolve=>setImmediate(resolve)); }
    throw new Error("Fixture did not reach " + step + "; reached " + apiStep);
  };
  const job = c.finishVoiceRecording({size:10}, "live", {profile:"p1",chat:"chat1"});
  await waitFor("clips");
  state.currentId="chat2"; input.value="new chat"; resolver(); await job;
  assert.equal(sendCount,0); assert.equal(input.value,"new chat"); assert(!state.pendingLiveAudio);
  apiStep=""; state.currentId="chat1";
  const voice = c.speakMessage(0,true);
  await waitFor("store");
  profile="p2"; resolver(); await voice; assert.equal(playCount,0,"stale profile TTS must not start");
  apiStep=""; profile="p1";
  const silent = c.speakMessage(0,true,false);
  await waitFor("store");
  resolver(); await silent; assert.equal(playCount,0,"auto-speak off still saves a playable clip silently");
  input.value="owner draft"; state.currentId="chat2"; apiStep="";
  await c.finishVoiceRecording({size:10},"live",{profile:"p1",chat:"chat1"});
  assert.equal(apiStep,"", "recording from another chat is rejected before STT");
}
ownership().then(()=>console.log("Round16 Live: delayed IDs, final/audio ordering, WAV cap, ownership and silent TTS PASS"))
  .catch(error=>{console.error(error);process.exitCode=1;});
