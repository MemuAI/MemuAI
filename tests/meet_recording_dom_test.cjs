/* MemuMeet recording + autosave behaviour test: the real
 * app/module_builtins/meet/main.js runs in node:vm against a stub
 * document/bridge (no jsdom, no browser in this VM). This is NOT a
 * browser: there is no real microphone, screen picker or speech
 * recognition — the bridge (Memu.record / Memu.transcribe) and the
 * media devices are fakes, and the final live check belongs to the
 * browser agent. What IS proved here, against the real module code:
 *
 *  (a) with display-capture disallowed by Permissions-Policy,
 *      getDisplayMedia is NEVER called — no policy console error is
 *      possible — and the UI explains the block;
 *  (b) with capture allowed, the same code calls getDisplayMedia once,
 *      records microphone + system audio as ONE meeting with TWO
 *      tracks, and stores the microphone track under its own key;
 *  (c) settings autosave: a burst of quick edits writes once, with the
 *      last state; the "Saved" indicator flips; the microphone choice
 *      is enumerated with human labels and persisted;
 *  (d) transcript lines label the microphone ("you") separately from
 *      system audio, only for two-track meetings;
 *  (e) with the shared selector inlined (as the host does), the model
 *      picker groups never show a "custom" provider.
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const MEET_JS = fs.readFileSync(path.join(ROOT, "app", "module_builtins", "meet", "main.js"), "utf8");
const SELECTOR_JS = fs.readFileSync(path.join(ROOT, "app", "pgstatic", "model-selector.js"), "utf8");

const MIC_DATA_URL = "data:audio/webm;base64,QUJD";
const tick = (ms = 10) => new Promise((r) => setTimeout(r, ms));
const j = (v) => JSON.parse(JSON.stringify(v));

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this._attrs = {};
    this.dataset = {};
    this._cls = new Set();
    this._listeners = {};
    this.style = { setProperty() {}, getPropertyValue: () => "" };
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.src = "";
    this.files = [];
    this.tabIndex = 0;
    const self = this;
    this.classList = {
      add(...n) { n.forEach((x) => self._cls.add(x)); },
      remove(...n) { n.forEach((x) => self._cls.delete(x)); },
      toggle(name, force) {
        const on = force === undefined ? !self._cls.has(name) : !!force;
        if (on) self._cls.add(name); else self._cls.delete(name);
        return on;
      },
      contains: (name) => self._cls.has(name),
    };
  }
  get className() { return [...this._cls].join(" "); }
  set className(v) { this._cls = new Set(String(v || "").split(/\s+/).filter(Boolean)); }
  get lastElementChild() { return this.children[this.children.length - 1] || null; }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  getAttribute(k) { return k in this._attrs ? this._attrs[k] : null; }
  removeAttribute(k) { delete this._attrs[k]; }
  appendChild(child) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const i = this.children.indexOf(child);
    if (i >= 0) this.children.splice(i, 1);
    child.parentNode = null;
    return child;
  }
  insertBefore(child, ref) {
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i >= 0) this.children.splice(i, 0, child); else this.children.push(child);
    return child;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    const list = this._listeners[type] || [];
    const i = list.indexOf(fn);
    if (i >= 0) list.splice(i, 1);
  }
  fire(type, event = {}) {
    const ev = { type, target: this, preventDefault() {}, stopPropagation() {}, ...event };
    for (const fn of (this._listeners[type] || []).slice()) fn.call(this, ev);
    return ev;
  }
  click() { this.fire("click"); }
  focus() {}
  scrollIntoView() {}
  querySelector() { return new El("div"); }
  querySelectorAll() { return []; }
}

class EventStub {
  constructor(type, opts = {}) {
    this.type = type;
    this.bubbles = !!opts.bubbles;
    Object.assign(this, opts);
  }
  preventDefault() {}
  stopPropagation() {}
}
class MutationObserverStub {
  observe() {}
  disconnect() {}
}
class FileReaderStub {
  constructor() { this.result = null; this.onload = null; this.onerror = null; }
  readAsDataURL(blob) {
    blob.arrayBuffer().then((buf) => {
      this.result = "data:audio/webm;base64," + Buffer.from(buf).toString("base64");
      if (this.onload) this.onload();
    }).catch(() => { if (this.onerror) this.onerror(); });
  }
}

function makeRadios() {
  const radios = ["microphone", "system", "both", "import"].map((value) => {
    const r = new El("input");
    r.value = value;
    r._checked = value === "microphone";
    Object.defineProperty(r, "checked", {
      get() { return r._checked; },
      set(v) {
        if (v) radios.forEach((other) => { other._checked = false; });
        r._checked = !!v;
      },
    });
    return r;
  });
  return radios;
}

function loadMeet({ policyAllowed, savedSettings = {}, withSelector = false, gumReject = "", origin = "", withAudioContext = false, systemStub = null } = {}) {
  const els = new Map();
  const radios = makeRadios();
  const elFor = (sel) => {
    if (!els.has(sel)) els.set(sel, new El(sel.startsWith("#") ? "div" : "div"));
    return els.get(sel);
  };
  const documentStub = {
    documentElement: { lang: "ru" },
    activeElement: null,
    hidden: false,
    _listeners: {},
    permissionsPolicy: { allowsFeature: (feature) => (feature === "display-capture" ? !!policyAllowed : true) },
    createElement: (tag) => new El(tag),
    getElementById: (id) => elFor("#" + id),
    querySelector(sel) {
      const m = String(sel).match(/^input\[name="source"\](?::checked|\[value="([^"]+)"\])?$/);
      if (m) {
        if (sel.endsWith(":checked")) return radios.find((r) => r.checked) || null;
        if (m[1]) return radios.find((r) => r.value === m[1]) || null;
        return radios[0];
      }
      return elFor(sel);
    },
    querySelectorAll(sel) {
      if (String(sel).startsWith('input[name="source"]')) return radios.slice();
      return [];
    },
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    removeEventListener() {},
  };

  const saves = [];
  const store = new Map();
  const recordStarts = [];
  const systemCalls = [];
  const consoleErrors = [];
  const sandboxConsole = { log: () => {}, warn: () => {}, error: (...a) => { consoleErrors.push(a); } };

  const track = { kind: "audio", stop() { track.stopped = true; } };
  const stream = {
    getAudioTracks: () => [track],
    getVideoTracks: () => [],
    getTracks: () => [track],
  };
  let displayMediaCalls = 0;
  let gumCalls = 0;
  const mediaCalls = [];
  const mediaDevices = {
    getUserMedia: () => {
      gumCalls++;
      mediaCalls.push("gum");
      if (gumReject) {
        const err = new Error("microphone unavailable");
        err.name = gumReject;
        return Promise.reject(err);
      }
      return Promise.resolve(stream);
    },
    getDisplayMedia: () => {
      displayMediaCalls++;
      mediaCalls.push("gdm");
      if (!policyAllowed) {
        const err = new Error("disallowed by permissions policy");
        err.name = "NotAllowedError";
        return Promise.reject(err);
      }
      return Promise.resolve(stream);
    },
    enumerateDevices: () => {
      mediaCalls.push("enumerate");
      return Promise.resolve([
        { kind: "audioinput", deviceId: "mic-2", label: "USB Microphone" },
        { kind: "audioinput", deviceId: "mic-3", label: "" },
        { kind: "videoinput", deviceId: "cam-1", label: "Camera" },
      ]);
    },
  };
  class MediaRecorderStub {
    constructor(s) { this.stream = s; this.state = "inactive"; this.mimeType = "audio/webm"; this.ondataavailable = null; this.onstop = null; }
    start() {
      this.state = "recording";
      setTimeout(() => {
        if (this.ondataavailable) this.ondataavailable({ data: new Blob([new Uint8Array([1, 2, 3, 4])], { type: "audio/webm" }) });
      }, 5);
    }
    stop() { this.state = "inactive"; if (this.onstop) this.onstop(); }
  }

  const Memu = {
    t: (key) => String(key),
    lang: "ru",
    env: { lang: "ru", module: "meet" },
    saved: { settings: savedSettings },
    models: () => Promise.resolve([
      { id: "myconn-gpt-5", model: "gpt-5", name: "GPT-5", provider: "custom", provider_label: "", account: "", kind: "chat" },
      { id: "acme-claude", model: "claude", name: "Claude", provider: "custom", provider_label: "Acme", kind: "chat" },
    ]),
    save: (fields) => { saves.push(j(fields)); return Promise.resolve(); },
    store: {
      get: (key, fallback) => Promise.resolve(store.has(key) ? j(store.get(key)) : (fallback == null ? null : fallback)),
      set: (key, value) => { store.set(key, j(value)); return Promise.resolve(); },
      remove: (key) => { store.delete(key); return Promise.resolve(); },
      list: (prefix) => Promise.resolve([...store.keys()].filter((k) => k.startsWith(prefix)).map((key) => ({ key }))),
      getMany: (keys) => Promise.resolve(Object.fromEntries(keys.map((k) => [k, store.has(k) ? j(store.get(k)) : null]))),
    },
    record: {
      start: (args) => { recordStarts.push(args === undefined ? null : j(args)); return Promise.resolve(); },
      stop: () => Promise.resolve({ audio: MIC_DATA_URL, mime: "audio/webm", ms: 3000 }),
      cancel: () => Promise.resolve(),
      system: systemStub ? (action, opts) => {
        systemCalls.push({ action, opts: opts === undefined ? null : j(opts) });
        return systemStub(action, opts);
      } : undefined,
    },
    transcribe: () => Promise.resolve({ text: "hello" }),
    on: () => {},
    onLanguage: () => {},
    ready: () => {},
    copy: () => Promise.resolve(),
  };

  const sandbox = {
    document: documentStub,
    navigator: { mediaDevices, permissionsPolicy: documentStub.permissionsPolicy, language: "ru-RU" },
    Memu,
    MediaRecorder: MediaRecorderStub,
    FileReader: FileReaderStub,
    Blob, Buffer, atob, btoa,
    Event: EventStub,
    MutationObserver: MutationObserverStub,
    localStorage: {
      _m: new Map(),
      getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
      setItem(k, v) { this._m.set(k, String(v)); },
      removeItem(k) { this._m.delete(k); },
    },
    console: sandboxConsole,
    setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame: (cb) => setTimeout(cb, 16),
  };
  sandbox.window = sandbox;
  if (origin) sandbox.origin = origin;
  if (withAudioContext) {
    sandbox.AudioContext = class {
      constructor() { this.state = "running"; }
      createAnalyser() {
        return {
          fftSize: 512, frequencyBinCount: 4,
          getByteTimeDomainData(arr) { for (let i = 0; i < arr.length; i++) arr[i] = 200; },
        };
      }
      createMediaStreamSource() { return { connect() {} }; }
      close() { return Promise.resolve(); }
    };
  }
  sandbox.addEventListener = () => {};
  sandbox.removeEventListener = () => {};
  vm.createContext(sandbox);
  if (withSelector) vm.runInContext(SELECTOR_JS, sandbox, { filename: "model-selector.js" });
  vm.runInContext(MEET_JS, sandbox, { filename: "meet/main.js" });
  return {
    ctx: sandbox, els, elFor, saves, store, recordStarts, systemCalls, consoleErrors, mediaCalls,
    get displayMediaCalls() { return displayMediaCalls; },
    get gumCalls() { return gumCalls; },
    radios,
  };
}

async function main() {
  /* ---- (c) restore + autosave burst + mic choice ---- */
  {
    const env = loadMeet({ policyAllowed: false, savedSettings: { mic_device: "mic-2", source: "both", summary_lang: "en" } });
    const { ctx } = env;
    await tick(50);
    assert.equal(ctx.settings.mic_device, "mic-2", "saved mic choice is restored");
    assert.equal(ctx.settings.source, "both", "saved source is restored");
    assert.equal(ctx.settings.summary_lang, "en");
    const micSelect = env.elFor("#mic-device");
    assert.ok(micSelect.innerHTML.includes("USB Microphone"), "human mic label");
    assert.ok(micSelect.innerHTML.includes("record.mic_unlabeled"), "unnamed mic is labelled as unlabelled, honestly");
    assert.ok(micSelect.innerHTML.includes("mic-3"), "unnamed mic carries its short device id");
    assert.ok(!micSelect.innerHTML.includes("record.mic_device 2"), "no invented numbered microphone name");
    assert.equal(micSelect.value, "mic-2", "restored mic is selected");
    assert.equal(env.elFor("#mic-permission").textContent, "record.mic_permission_granted", "labels present: permission state is granted");

    // Permission is requested BEFORE enumerateDevices on an explicit refresh.
    const mark = env.mediaCalls.length;
    await ctx.refreshMicDevices(true);
    assert.equal(env.gumCalls, 1, "one permission request on refresh");
    assert.deepEqual(env.mediaCalls.slice(mark), ["gum", "enumerate"], "getUserMedia runs before enumerateDevices");

    ctx.settings.summary_lang = "ru";
    ctx.scheduleSaveSettings();
    ctx.settings.summary_lang = "en";
    ctx.scheduleSaveSettings();
    ctx.settings.keep_audio = false;
    ctx.scheduleSaveSettings();
    assert.equal(env.elFor("#set-status").textContent, "settings.saving", "indicator shows saving at once");
    await tick(600);
    assert.equal(env.saves.length, 1, "a burst of quick edits writes once");
    assert.equal(env.saves[0].settings.summary_lang, "en", "only the last state is written");
    assert.equal(env.saves[0].settings.keep_audio, false);
    assert.equal(env.saves[0].settings.mic_device, "mic-2", "unchanged settings survive the burst");
    assert.equal(env.elFor("#set-status").textContent, "settings.saved");

    micSelect.value = "mic-3";
    micSelect.fire("change");
    assert.equal(ctx.settings.mic_device, "mic-3", "mic choice updates settings");
    await tick(600);
    assert.equal(env.saves.length, 2);
    assert.equal(env.saves[1].settings.mic_device, "mic-3");

    ctx.settings.model = "m-x";
    await ctx.flushSaveSettings();
    assert.equal(env.saves.length, 3, "flush writes immediately, without the debounce");
    assert.equal(env.saves[2].settings.model, "m-x");
    assert.deepEqual(env.consoleErrors, [], "no console errors during autosave");
  }

  /* ---- (a) policy forbids display-capture: never call it ---- */
  {
    const env = loadMeet({ policyAllowed: false, savedSettings: { mic_device: "mic-2" } });
    const { ctx } = env;
    await tick(50);
    assert.deepEqual(j(ctx.displayCaptureAllowed()), { allowed: false, reason: "policy" });
    const stream = await ctx.startSystemStream("Blocked");
    assert.equal(stream, null);
    assert.equal(env.displayMediaCalls, 0, "getDisplayMedia must not be called under a forbidding policy");
    assert.equal(env.elFor("#system-note").textContent, "", "no alarming capture status is shown under a forbidding policy");
    assert.equal(env.elFor("#rec-status").textContent, "", "no alarming capture status is shown under a forbidding policy");

    env.elFor("#check-system").click();
    await tick(30);
    assert.equal(env.displayMediaCalls, 0, "the sound check also stays away from getDisplayMedia");
    assert.equal(env.elFor("#system-check-status").textContent, "", "the disabled system check shows no alarming status");

    // "Both" degrades to microphone-only, still one meeting, no exception.
    await ctx.startBothRecording("Blocked both");
    assert.ok(ctx.recording, "recording started");
    assert.equal(ctx.recording.source, "microphone");
    assert.equal(ctx.recording.micOnlyFallback, true);
    assert.deepEqual(j(env.recordStarts[0]), { deviceId: "mic-2" }, "mic starts with the saved device");
    assert.equal(env.elFor("#rec-live").textContent, "record.recording_mic", "the UI says microphone-only while recording");
    assert.equal(env.elFor("#rec-status").textContent, "", "the mic-only downgrade shows no alarming status");
    await ctx.stopRecording();
    assert.ok(ctx.pending, "pending card after stop");
    assert.equal(ctx.pending.tracks.length, 1, "only the microphone track exists");
    assert.equal(ctx.pending.tracks[0].source, "microphone");
    assert.equal(env.elFor("#pending-tracks-note").hidden, false, "the pending card explains the missing system track");
    assert.ok(env.elFor("#pending-tracks-note").textContent.includes("record.pending_mic_only"));
    assert.ok(!env.elFor("#pending-tracks").innerHTML.includes("record.system_short"), "no false system label on a mic-only recording");
    assert.equal(env.displayMediaCalls, 0);
    assert.deepEqual(env.consoleErrors, [], "no console errors on the blocked path");
  }

  /* ---- (b) capture allowed: one meeting, two tracks, mic key stored ---- */
  {
    const env = loadMeet({ policyAllowed: true, savedSettings: { mic_device: "mic-2" } });
    const { ctx } = env;
    await tick(50);
    assert.deepEqual(j(ctx.displayCaptureAllowed()), { allowed: true, reason: "" });

    ctx.refreshSystemCheckUI();
    assert.equal(env.elFor("#check-system").disabled, false, "system check is offered when capture is available");
    env.elFor("#check-system").click();
    await tick(30);
    assert.equal(env.displayMediaCalls, 1, "sound check calls getDisplayMedia once when allowed");
    assert.equal(env.elFor("#system-check-status").textContent, "record.check_ok");

    await ctx.startBothRecording("Planning");
    assert.ok(ctx.recording);
    assert.equal(ctx.recording.source, "both");
    assert.equal(ctx.recording.micActive, true);
    assert.deepEqual(j(env.recordStarts[0]), { deviceId: "mic-2" });
    assert.equal(env.elFor("#rec-live").textContent, "record.recording_both", "the UI says what is being recorded");

    await tick(30); // let the MediaRecorder stub emit a chunk
    await ctx.stopRecording();
    assert.ok(ctx.pending);
    assert.equal(ctx.pending.source, "both");
    assert.equal(ctx.pending.tracks.length, 2, "microphone + system in one recording");
    assert.deepEqual(j(ctx.pending.tracks.map((t) => t.source)), ["microphone", "system"]);
    assert.ok(env.elFor("#pending-tracks").innerHTML.includes("record.you"));
    assert.ok(env.elFor("#pending-tracks").innerHTML.includes("record.system_short"));

    env.elFor("#pending-save").click();
    await tick(80);
    const meetingKeys = [...env.store.keys()].filter((k) => /^meetings\/[^/]+$/.test(k));
    assert.equal(meetingKeys.length, 1, "exactly one meeting is stored");
    const meeting = env.store.get(meetingKeys[0]);
    assert.equal(meeting.source, "both");
    assert.equal(meeting.tracks.length, 2);
    assert.ok(env.store.has(meetingKeys[0] + "/audio"), "primary (system) audio stored");
    assert.ok(env.store.has(meetingKeys[0] + "/audio-mic"), "microphone track stored under its own key");
    assert.equal(env.store.get(meetingKeys[0] + "/audio-mic").data, MIC_DATA_URL);
    assert.deepEqual(env.consoleErrors, [], "no console errors on the allowed path");
  }

  /* ---- (g) native capture: probe, device list, options, recording ---- */
  {
    const nativeProbe = {
      available: true, web: false,
      native: { available: true, reason: "", backend: "sine", devices: [
        { id: "sine-a", name: "Speakers (Test)", is_default: true },
        { id: "sine-b", name: "Monitor HDMI", is_default: false },
      ] },
    };
    const systemStub = (action) => {
      if (action === "probe") return Promise.resolve(j(nativeProbe));
      if (action === "check") return Promise.resolve({});
      if (action === "start") return Promise.resolve({ started: Date.now(), mode: "native", devices: ["sine-a"] });
      if (action === "stop") return Promise.resolve({ audio: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }), ms: 1000 });
      return Promise.resolve({});
    };
    const env = loadMeet({ policyAllowed: false, systemStub, savedSettings: { source: "system" } });
    const { ctx } = env;
    await tick(50);
    assert.deepEqual(j(ctx.nativeStatus), nativeProbe.native, "probe result is kept");
    assert.equal(ctx.hostSystemAudio, true, "native probe makes capture available");
    assert.equal(ctx.captureAvailable(), true, "system source becomes available through native capture");
    assert.equal(env.displayMediaCalls, 0, "the native path never opens the screen picker");

    assert.equal(env.elFor("#native-block").hidden, false, "device block shows for a system source");
    const list = env.elFor("#native-devices");
    assert.equal(list.children.length, 2, "one row per output device");
    assert.equal(list.children[0].children[0].checked, true, "the default output is checked");
    assert.equal(list.children[0].children[1].textContent, "Speakers (Test) \u00b7 record.native_default");
    assert.equal(list.children[1].children[0].checked, false, "a non-default device starts unchecked");
    assert.equal(env.elFor("#native-mode").value, "auto", "auto mode by default");

    // The sound check goes through the host bridge with the mode and
    // the chosen devices — no getDisplayMedia involved.
    env.elFor("#check-system").click();
    await tick(30);
    assert.equal(env.displayMediaCalls, 0, "native check opens no picker");
    const checkCall = env.systemCalls.find((c) => c.action === "check");
    assert.ok(checkCall, "check reaches the host bridge");
    assert.equal(checkCall.opts.mode, "auto");
    assert.deepEqual(checkCall.opts.devices, [], "empty selection means the default output");
    assert.equal(env.elFor("#system-check-status").textContent, "record.check_ok");

    // Start a native recording: the host bridge receives the options
    // and the recording remembers its mode and devices.
    await ctx.startSystemRecording("Native", false);
    assert.ok(ctx.recording, "native recording started");
    assert.equal(ctx.recording.captureMode, "native");
    assert.deepEqual(j(ctx.recording.captureDevices), ["sine-a"]);
    const startCall = env.systemCalls.find((c) => c.action === "start");
    assert.ok(startCall, "start reaches the host bridge");
    assert.equal(startCall.opts.mode, "auto");
    await ctx.stopRecording();
    assert.ok(ctx.pending, "pending card after a native stop");
    assert.equal(ctx.pending.tracks.length, 1, "one system track");
    assert.equal(ctx.pending.tracks[0].source, "system");
    assert.deepEqual(env.consoleErrors, [], "no console errors on the native path");
  }

  /* ---- (g2) native unavailable: the web fallback is offered ---- */
  {
    const systemStub = (action, opts) => {
      if (action === "probe") return Promise.resolve({ available: true, web: true,
        native: { available: false, reason: "no_backend", backend: "none", devices: [] } });
      if (action === "start") {
        if (opts && opts.mode === "web") return Promise.resolve({ started: Date.now(), mode: "web", devices: [] });
        return Promise.resolve({ reason: "native-unavailable", detail: "no_backend" });
      }
      if (action === "stop") return Promise.resolve({ audio: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }), ms: 1000 });
      return Promise.resolve({});
    };
    const env = loadMeet({ policyAllowed: true, systemStub, savedSettings: { source: "system", capture_mode: "native" } });
    const { ctx } = env;
    await tick(50);
    assert.equal(ctx.hostSystemAudio, true, "web capture still makes the host bridge available");
    assert.equal(ctx.nativeStatus.available, false);
    assert.equal(ctx.effectiveCaptureMode(), "web", "explicit native falls back to web and says so");
    await ctx.startSystemRecording("Fallback", false);
    assert.equal(ctx.recording, null, "a failed native start records nothing");
    assert.equal(env.elFor("#rec-web-fallback").hidden, false, "the web fallback button appears");
    assert.equal(env.elFor("#rec-status").textContent, "record.native_unavailable_fallback", "the failure names the native path");
    // The fallback retries through the host bridge with web mode.
    env.elFor("#rec-web-fallback").click();
    await tick(30);
    const webCall = env.systemCalls.filter((c) => c.action === "start").pop();
    assert.equal(webCall.opts.mode, "web", "the fallback retries with web mode");
    assert.ok(ctx.recording, "web fallback recording started");
    assert.equal(ctx.recording.captureMode, "web");
    await ctx.stopRecording();
    assert.deepEqual(env.consoleErrors, [], "no console errors on the fallback path");
  }

  /* ---- (i) native capture: microphone in the device list, diagnostics ---- */
  {
    const nativeProbe = {
      available: true, web: false,
      native: { available: true, reason: "", backend: "sine",
        sounddevice: { installed: true, portaudio: true },
        devices: [
          { id: "sine-a", name: "Speakers (Test)", kind: "output", is_default: true },
          { id: "sdi-1", name: "USB Microphone", kind: "input", is_default: false },
        ] },
    };
    const systemStub = (action) => {
      if (action === "probe") return Promise.resolve(j(nativeProbe));
      if (action === "check") return Promise.resolve({});
      if (action === "start") return Promise.resolve({ started: Date.now(), mode: "native", devices: ["sine-a"] });
      if (action === "stop") return Promise.resolve({ audio: new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }), ms: 1000 });
      return Promise.resolve({});
    };
    const env = loadMeet({ policyAllowed: false, systemStub, savedSettings: { source: "system", mic_device: "mic-2" } });
    const { ctx } = env;
    // The microphone selector label must exist before the probe
    // renders, so the microphone row is pre-checked by name match.
    // (The stub select has no selectedIndex of its own.)
    env.elFor("#mic-device").options = [{ text: "USB Microphone" }];
    env.elFor("#mic-device").selectedIndex = 0;
    await tick(50);

    const list = env.elFor("#native-devices");
    assert.equal(list.children.length, 2, "one row per native device, output + microphone");
    const micRow = list.children[1];
    assert.equal(micRow.children[1].textContent, "USB Microphone", "microphone name shown");
    assert.equal(micRow.children[2].textContent, "record.native_kind_input", "microphone row carries the kind badge");
    assert.equal(micRow.children[0].checked, true, "the microphone matching the selected mic is pre-checked");
    assert.equal(list.children[0].children[2].textContent, "record.native_kind_output", "output row carries the kind badge");

    // Diagnostics: helper status line and an empty last-error slot.
    assert.ok(env.elFor("#diag-status").textContent.includes("record.diag_ok"), "diagnostics say the helper is available: " + env.elFor("#diag-status").textContent);
    assert.equal(env.elFor("#diag-error").textContent, "record.diag_no_error", "the diagnostics say explicitly that there is no last error");

    // "Check sound" goes through the host bridge in native mode, then
    // the test finishes with a per-device heard/silent summary.
    env.elFor("#diag-check").click();
    await tick(30);
    const checkCall = env.systemCalls.find((c) => c.action === "check");
    assert.ok(checkCall, "the sound test reaches the host bridge");
    assert.equal(checkCall.opts.mode, "native", "the test forces native mode");
    assert.equal(env.elFor("#diag-status").textContent, "record.diag_running", "the test says it is running");
    clearTimeout(ctx.diagTest.timer);
    ctx.finishNativeDiagnostics();
    assert.ok(env.elFor("#diag-status").textContent.includes("record.diag_silent"), "a quiet test reports silence, not a failure: " + env.elFor("#diag-status").textContent);
    assert.deepEqual(env.consoleErrors, [], "no console errors on the diagnostics path");
  }

  /* ---- (d) transcript source labels, only for two-track meetings ---- */
  {
    const env = loadMeet({ policyAllowed: true });
    const { ctx } = env;
    await tick(50);
    const both = ctx.transcriptText([
      { start: 0, text: "hi", speaker: "speaker-1", source: "microphone" },
      { start: 1, text: "yo", speaker: "speaker-1", source: "system" },
    ]);
    assert.ok(both.includes("record.you"), "microphone speech is labelled as the user");
    assert.ok(both.includes("record.system_short"), "system speech is labelled separately");
    ctx.current = { source: "microphone", speaker_names: {} };
    const single = ctx.transcriptText([
      { start: 0, text: "hi", speaker: "speaker-1", source: "microphone" },
    ]);
    assert.ok(single.includes("common.speaker 1"), "single-track meetings keep speaker numbers");
    assert.ok(!single.includes("record.you"));
  }

  /* ---- (f) opaque origin: the gate, the mic sandbox state, no picker ---- */
  {
    const env = loadMeet({ policyAllowed: false, origin: "null", gumReject: "SecurityError" });
    const { ctx } = env;
    await tick(50);
    // The Permissions-Policy API is absent in the stub, exactly as in
    // the real module window: the opaque origin alone must block it.
    assert.deepEqual(j(ctx.displayCaptureAllowed()), { allowed: false, reason: "policy" });
    ctx.refreshSystemCheckUI();
    assert.equal(env.elFor("#check-system").disabled, true, "system check is not offered when capture is blocked");
    assert.ok(env.elFor("#check-system-hint").textContent.includes("record.check_system_unavailable"), "the hint names unavailability neutrally, not a picker");
    env.elFor("#check-system").click();
    await tick(30);
    assert.equal(env.displayMediaCalls, 0, "a disabled system check never calls getDisplayMedia");
    // Permission request fails as SecurityError in the sandbox.
    await ctx.refreshMicDevices(true);
    assert.equal(ctx.micPermission, "sandbox", "a sandboxed refusal is the sandbox state, not a user denial");
    assert.equal(env.elFor("#mic-permission").textContent, "record.mic_permission_sandbox");
    // The microphone check falls back to the bridge clip, without capture.
    env.elFor("#check-mic").click();
    await tick(30);
    assert.equal(env.displayMediaCalls, 0, "the microphone check never calls getDisplayMedia");
    assert.ok(env.recordStarts.length >= 1, "the mic check uses the bridge clip in the sandbox");
    assert.deepEqual(env.consoleErrors, [], "no console errors in the sandboxed scenario");
  }

  /* ---- (g) microphone level check: AnalyserNode, no getDisplayMedia ---- */
  {
    const env = loadMeet({ policyAllowed: true, withAudioContext: true });
    const { ctx } = env;
    await tick(50);
    env.elFor("#level-mic").parentNode = new (Object.getPrototypeOf(env.elFor("#level-mic")).constructor)("div");
    env.elFor("#check-mic").click();
    await tick(60);
    assert.equal(env.gumCalls, 1, "the mic check opens the microphone stream");
    assert.equal(env.displayMediaCalls, 0, "the mic check never calls getDisplayMedia");
    assert.ok(ctx.micCheckStream, "the level stream is held during the check");
    const width = env.elFor("#level-mic").style.width;
    assert.ok(width && width !== "0%", "the meter moves: " + width);
    env.elFor("#check-mic-stop").click();
    await tick(20);
    assert.equal(env.elFor("#mic-check-status").textContent, "record.mic_level_done", "a moving level means the microphone works");
    assert.equal(ctx.micCheckStream, null, "the check stream is stopped");
    assert.deepEqual(env.consoleErrors, [], "no console errors during the level check");
  }

  /* ---- (h) a check status is written exactly once, however often it
   * is re-rendered: the fallback paragraph must not be duplicated. ---- */
  {
    const env = loadMeet({ policyAllowed: false, origin: "null", gumReject: "SecurityError" });
    const { ctx } = env;
    await tick(50);
    // Only the two check-status elements are counted: the system
    // block's hint (#check-system-hint) is a separate explanation of
    // why its button is disabled and may legitimately carry the same
    // wording; the bug was the status itself being written twice.
    const countText = (needle) => ["#mic-check-status", "#system-check-status"]
      .filter((sel) => env.elFor(sel).textContent === needle).length;
    env.elFor("#check-mic").click();
    await tick(30);
    assert.ok(env.recordStarts.length >= 1, "the mic check uses the bridge clip in the sandbox");
    assert.equal(env.elFor("#mic-check-status").textContent, "record.mic_level_unavailable");
    assert.equal(countText("record.mic_level_unavailable"), 1, "the fallback text appears exactly once");
    assert.equal(env.elFor("#system-check-status").textContent, "", "the mic status does not spill into the system block");
    // Repeated renders / repeated status writes must not accumulate copies.
    ctx.setCheckStatus("record.mic_level_unavailable", true);
    ctx.setCheckStatus("record.mic_level_unavailable", true);
    ctx.setStep(2);
    ctx.setStep(1);
    ctx.setStep(2);
    assert.equal(countText("record.mic_level_unavailable"), 1, "re-renders keep exactly one copy");
    // The system side is separate and also single.
    ctx.setCheckStatus("record.system_blocked", false);
    assert.equal(countText("record.system_blocked"), 1, "the system status appears exactly once");
    assert.equal(env.elFor("#system-check-status").textContent, "record.system_blocked");
    assert.equal(env.elFor("#mic-check-status").textContent, "record.mic_level_unavailable", "a system status does not overwrite the mic status");
    assert.deepEqual(env.consoleErrors, [], "no console errors during repeated checks");
  }

  /* ---- (e) provider names with the shared selector inlined ---- */
  {
    const env = loadMeet({ policyAllowed: true, withSelector: true });
    const { ctx } = env;
    await tick(50);
    const select = env.elFor("#set-model");
    const labels = select.children.filter((c) => c.tagName === "OPTGROUP").map((g) => g.label);
    assert.ok(labels.includes("Myconn"), "custom connection shown under its id prefix, got: " + labels.join(", "));
    assert.ok(labels.includes("Acme"), "provider_label is used");
    assert.ok(!labels.some((l) => /^custom$/i.test(l)), "no 'custom' provider group");
  }

  console.log("meet_recording_dom_test: OK");
}

main().catch((err) => { console.error(err); process.exit(1); });
