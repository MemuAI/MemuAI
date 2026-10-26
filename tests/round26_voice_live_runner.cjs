

const fs = require("fs"), vm = require("vm"), assert = require("assert"), path = require("path");
const root = path.resolve(__dirname, "..");

const worklet = fs.readFileSync(path.join(root, "app/pgstatic/realtime-worklet.js"), "utf8");
function resample(rate, target, freq = 300, blocks = 400) {
  let Processor; const out = [];
  const scope = { sampleRate: rate, registerProcessor: (_name, cls) => { Processor = cls; },
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (m) => out.push(m) }; } } };
  new Function(...Object.keys(scope), worklet)(...Object.values(scope));
  const node = new Processor({ processorOptions: { targetRate: target } });
  let n = 0;
  for (let b = 0; b < blocks; b++) {
    const channel = new Float32Array(128);
    for (let i = 0; i < 128; i++, n++) channel[i] = 0.5 * Math.sin(2 * Math.PI * freq * n / rate);
    node.process([[channel]]);
  }
  const pcm = []; for (const m of out) pcm.push(...new Int16Array(m.pcm));
  let step = 0; for (let i = 200; i < pcm.length - 1; i++) step = Math.max(step, Math.abs(pcm[i + 1] - pcm[i]));
  return { step, count: pcm.length + node.fill, expected: blocks * 128 * target / rate };
}
for (const [rate, target] of [[48000, 24000], [44100, 24000], [44100, 16000], [48000, 16000], [16000, 24000]]) {
  const r = resample(rate, target);

  const ideal = 2 * Math.PI * 300 / target * 16383;
  assert(r.step < ideal * 1.05, `${rate}→${target}: block-boundary step ${r.step} > ${ideal.toFixed(0)}`);
  assert(Math.abs(r.count - r.expected) <= 2, `${rate}→${target}: ${r.count} samples, expected ${r.expected}`);
}

const ctx = vm.createContext({ console, Uint8Array, DataView, Set, Map, Date, Math, PG_LANG: "ru",
  pref: (_k, fallback) => fallback, btoa: s => Buffer.from(s, "binary").toString("base64"),
  atob: s => Buffer.from(s, "base64").toString("binary") });
let source = fs.readFileSync(path.join(root, "app/pgstatic/realtime.js"), "utf8");
source = source.replace("start, stop, setMuted, interrupt, sendText, attachOrb",
  "testing: { session, onEvent, recordInput, commit }, start, stop, setMuted, interrupt, sendText, attachOrb");
vm.runInContext(source + "\nglobalThis.live = MemuRealtime.testing; globalThis.api = MemuRealtime;", ctx);
const live = ctx.live, api = ctx.api, rows = new Map(), captions = [], sent = [];
let played = 0;
function reset(protocol = "openai-realtime") {
  rows.clear(); captions.length = 0; sent.length = 0; played = 0;
  Object.assign(live.session, { turns: { user: null, assistant: null }, user: "", assistant: "", found: [], sources: new Set(),
    inputPreroll: [], inputPrerollBytes: 0, providerTurns: new Map(), cancelled: new Set(), dropReply: false,
    inputRate: 24000, outputRate: 24000, info: { protocol, output_rate: 24000 }, state: "listening", nextTime: 0,
    ws: { readyState: 1, send: (m) => sent.push(JSON.parse(m)) }, gain: {},
    hooks: { onTurn: (role, text, extra) => rows.set(extra.turnId, { ...(rows.get(extra.turnId) || {}), role, text, ...extra }),
      onCaption: (role, text) => captions.push([role, text]) },
    player: { currentTime: 0, createBuffer: (_c, count) => ({ duration: count / 24000, getChannelData: () => new Float32Array(count) }),
      createBufferSource: () => { played++; return { connect() {}, start() {}, stop() {} }; } } });
}
ctx.WebSocket = { OPEN: 1 };
const pcm = Buffer.alloc(4800, 1).toString("base64");

reset();
live.onEvent({ type: "audio", data: pcm, turn_id: "a1" });
assert.equal(played, 1);
api.interrupt();
assert.equal(sent.at(-1).type, "interrupt");
live.onEvent({ type: "audio", data: pcm, turn_id: "a1" });
assert.equal(played, 1, "tail of the interrupted reply must not play");
assert.equal(live.session.state, "listening");
live.onEvent({ type: "audio", data: pcm, turn_id: "a2" });
assert.equal(played, 2, "the next reply plays");

live.onEvent({ type: "interrupted" });
live.onEvent({ type: "audio", data: pcm, turn_id: "a2" });
assert.equal(played, 2);

reset("gemini-live");
live.onEvent({ type: "audio", data: pcm });
api.interrupt();
live.onEvent({ type: "audio", data: pcm });
assert.equal(played, 1, "Gemini keeps streaming after a local interrupt: muted");
live.onEvent({ type: "turn_complete" });
live.onEvent({ type: "audio", data: pcm });
assert.equal(played, 2, "a new turn plays again");
reset("gpt-live");
live.onEvent({ type: "audio", data: pcm }); api.interrupt();
live.onEvent({ type: "audio", data: pcm }); assert.equal(played, 1);
live.onEvent({ type: "user_transcript", delta: "Ещё" });
live.onEvent({ type: "audio", data: pcm }); assert.equal(played, 2, "GPT-Live has no turn_complete: user speech re-enables");

reset();
live.recordInput(new Uint8Array(24000).buffer, 0.2);
live.onEvent({ type: "audio", data: pcm, turn_id: "b1" });
live.onEvent({ type: "turn_complete" });
assert.deepEqual([...rows.values()].map(r => r.role), ["assistant"], "noise became a blank user message");

reset();
live.onEvent({ type: "speech_started", turn_id: "u1" });
live.recordInput(new Uint8Array(24000).buffer, 0.2);
live.onEvent({ type: "speech_stopped", turn_id: "u1" });
const heard = [...rows.values()].find(r => r.role === "user");
assert(heard && heard.audio && heard.text === "", "provider-detected speech keeps its clip");

reset();
live.onEvent({ type: "user_transcript", text: "Привет", final: true, turn_id: "u1" });
api.sendText("Тест текстом");
assert.deepEqual(captions.at(-1), ["user", "Тест текстом"]);

const videoCtx = vm.createContext({ PG_LANG: "ru", window: {}, URL, console });
vm.runInContext(fs.readFileSync(path.join(root, "app/pgstatic/video.js"), "utf8") + "\nglobalThis.V = MemuVideo;", videoCtx);
assert.equal(videoCtx.V.noteText({ transcriptNote: "stt_failed", transcriptError: "Неверный ключ API" }),
  "звук не расшифрован: Неверный ключ API");
assert.equal(videoCtx.V.noteText({ transcriptNote: "no_audio" }), "звуковую дорожку прочитать не удалось");
(async () => {
  const fake = { name: "clip.mp4", size: 10, type: "video/mp4", arrayBuffer: async () => new ArrayBuffer(8) };
  const ctxFor = (decode, transcribe) => {
    const c = vm.createContext({ PG_LANG: "en", console, Math, Number, String, Object, Error, Promise, setTimeout, clearTimeout,
      URL: { createObjectURL: () => "blob:x", revokeObjectURL() {} }, voiceInputReady: () => true,
      api: transcribe, btoa: s => Buffer.from(s, "binary").toString("base64"),
      document: { createElement: (tag) => tag === "canvas" ? { getContext: () => ({ drawImage() {} }), toDataURL: () => "data:image/jpeg;base64,AA" }
        : { addEventListener(event, fn) { if (event === "loadeddata" || event === "seeked") setTimeout(fn, 0); }, removeEventListener() {},
            set src(_v) {}, duration: 3, videoWidth: 64, videoHeight: 36, readyState: 4, currentTime: 0, removeAttribute() {}, load() {} } },
      window: { AudioContext: class { decodeAudioData() { return decode(); } close() { return Promise.resolve(); } },
        OfflineAudioContext: class { constructor() {} createBufferSource() { return { connect() {}, start() {} }; } get destination() { return {}; }
          startRendering() { return Promise.resolve({ getChannelData: () => Float32Array.from({ length: 1600 }, (_, i) => Math.sin(i)) }); } } } });
    c.OfflineAudioContext = c.window.OfflineAudioContext;
    vm.runInContext(fs.readFileSync(path.join(root, "app/pgstatic/video.js"), "utf8") + "\nglobalThis.V = MemuVideo;", c);
    return c.V;
  };
  const failing = await ctxFor(() => Promise.resolve({ duration: 3 }), () => Promise.reject(new Error("The API key was rejected"))).prepare(fake);
  assert.equal(failing.transcriptNote, "stt_failed");
  assert.match(failing.transcriptError, /rejected/);
  const silentTrack = await ctxFor(() => Promise.reject(new Error("EncodingError")), () => Promise.resolve({ text: "x" })).prepare(fake);
  assert.equal(silentTrack.transcriptNote, "no_audio");
  console.log("Round26 Live PASS: worklet boundaries 44.1/48/16 kHz, interrupted tail dropped, no blank noise turns, typed caption");
})().catch((error) => { console.error(error); process.exit(1); });
