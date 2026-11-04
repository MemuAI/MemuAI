/* MemuNativeAudio (app/pgstatic/native-audio.js): PCM feeder + SSE
 * parser, tested in node with mocked AudioContext — no browser, no
 * network. The feeder math (resampling, queue depth, silence padding,
 * drop-oldest) is what keeps a bursty helper stream a continuous
 * recording, so it is tested here rather than trusted. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "app", "pgstatic", "native-audio.js"), "utf8");

function load() {
  const window = {};
  const sandbox = { window, console, setInterval, clearInterval };
  sandbox.window = window;
  vm.createContext(sandbox);
  vm.runInContext(SRC, sandbox, { filename: "native-audio.js" });
  return window.MemuNativeAudio;
}

class FakeBuffer {
  constructor(channels, length, rate) {
    this.numberOfChannels = channels;
    this.length = length;
    this.sampleRate = rate;
    this._data = Array.from({ length: channels }, () => new Float32Array(length));
  }
  getChannelData(ch) { return this._data[ch]; }
}
class FakeSource {
  constructor(ctx) { this.ctx = ctx; this.buffer = null; this.startedAt = null; }
  connect() {}
  start(when) { this.startedAt = when; this.ctx._started.push(this); }
}
class FakeGain {
  constructor() { this.connected = []; }
  connect(node) { this.connected.push(node); }
  disconnect() {}
}
class FakeAudioContext {
  constructor(sampleRate) {
    this.sampleRate = sampleRate || 48000;
    this.currentTime = 0;
    this.state = "running";
    this._started = [];
    this._buffers = [];
  }
  createGain() { return new FakeGain(); }
  createBuffer(channels, length, rate) {
    const buffer = new FakeBuffer(channels, length, rate);
    this._buffers.push(buffer);
    return buffer;
  }
  createBufferSource() { return new FakeSource(this); }
  resume() { this.state = "running"; return Promise.resolve(); }
}

function sinePcm(freq, rate, seconds, amplitude) {
  const frames = Math.floor(rate * seconds);
  const out = new Int16Array(frames * 2);
  for (let i = 0; i < frames; i++) {
    const value = Math.round(amplitude * 32767 * Math.sin(2 * Math.PI * freq * i / rate));
    out[i * 2] = value;
    out[i * 2 + 1] = value;
  }
  return out;
}

async function main() {
  const api = load();
  assert.equal(api.SAMPLE_RATE, 48000);
  assert.equal(api.CHANNELS, 2);

  /* resamplePcm: 48k passthrough is exact; 44.1k -> 48k keeps 440 Hz. */
  {
    const input = sinePcm(440, 44100, 0.2, 0.5);
    const floats = new Float32Array(input.length);
    for (let i = 0; i < input.length; i++) floats[i] = input[i] / 32768;
    const out = api.resamplePcm(floats, 44100, 48000);
    assert.ok(out.length > 19000 && out.length <= 19200, `resampled length ${out.length}`);
    let crossings = 0;
    for (let i = 2; i < out.length; i += 2) if (out[i - 2] < 0 && out[i] >= 0) crossings++;
    assert.ok(crossings >= 80 && crossings <= 96, `crossings ${crossings}`);
    const same = api.resamplePcm(floats, 48000, 48000);
    assert.equal(same, floats, "48k passthrough returns the input");
  }

  /* SSE parser: split events, split data lines, ignores comments. */
  {
    const parser = api.createSseParser();
    let events = parser.feed("event: level\ndata: {\"a\":1}\n\n: ping\n\nevent: device-lost\ndata: {\"device\":\"x\"}\n\n");
    assert.equal(events.length, 2);
    assert.equal(events[0].type, "level");
    assert.equal(events[0].data, "{\"a\":1}");
    assert.equal(events[1].type, "device-lost");
    events = parser.feed("event: lev");
    assert.equal(events.length, 0, "a partial event waits for its end");
    events = parser.feed("el\ndata: {}\n\n");
    assert.equal(events.length, 1);
    assert.equal(events[0].type, "level");
  }

  /* Feeder: continuous scheduling, silence when starved, drop when flooded. */
  {
    const ctx = new FakeAudioContext(48000);
    const feeder = api.createPcmFeeder(ctx);
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.equal(ctx._started.length, 0, "do not schedule silence before the first input");
    feeder.push(sinePcm(440, 48000, 0.5, 0.5));
    assert.ok(feeder.queuedMs() >= 400, `queued ${feeder.queuedMs()}ms`);
    assert.ok(feeder.peak() > 0.4, `peak ${feeder.peak()}`);
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(ctx._started.length >= 2, `scheduled ${ctx._started.length} buffers`);
    // The scheduled buffers are contiguous: no gaps in the timeline.
    const times = ctx._started.map((s) => s.startedAt).sort((a, b) => a - b);
    for (let i = 1; i < times.length; i++) {
      assert.ok(Math.abs(times[i] - times[i - 1] - 0.04) < 0.001,
        `gap ${times[i] - times[i - 1]}`);
    }
    // Flood the queue: the feeder drops oldest samples instead of
    // growing unbounded, and the queue stays near the target depth.
    feeder.push(sinePcm(440, 48000, 2.0, 0.5));
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(feeder.queuedMs() <= 600, `queue after flood ${feeder.queuedMs()}ms`);
    feeder.stop();
    const scheduled = ctx._started.length;
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.equal(ctx._started.length, scheduled, "no scheduling after stop");
  }

  /* Feeder at 44.1k context rate: input is resampled, buffers are 44.1k. */
  {
    const ctx = new FakeAudioContext(44100);
    const feeder = api.createPcmFeeder(ctx);
    feeder.push(sinePcm(440, 48000, 0.3, 0.5));
    await new Promise((resolve) => setTimeout(resolve, 120));
    assert.ok(ctx._buffers.length >= 1, "buffers scheduled at 44.1k");
    assert.equal(ctx._buffers[0].sampleRate, 44100);
    assert.equal(ctx._buffers[0].length, 1764, "40 ms at 44.1k");
    feeder.stop();
  }

  console.log("meet_native_audio_js: ALL PASS");
}

main().catch((error) => { console.error(error); process.exit(1); });
