/* MemuMeet native system-audio capture: PCM feeder + SSE parser.
 *
 * Framework-free helpers for the host window (app/pgstatic/modules.js).
 * The native helper streams raw 48 kHz stereo int16 PCM over HTTP; this
 * file turns that byte stream into scheduled Web Audio buffers so the
 * existing MediaRecorder pipeline (Opus/WebM, caption clips, level
 * meter) works exactly as it does for the getDisplayMedia path.
 * Nothing here touches the network or the DOM beyond the AudioContext
 * it is given, so it is unit-tested in node with mocks
 * (tests/meet_native_audio_js_test.cjs).
 */
(function () {
  "use strict";

  var SAMPLE_RATE = 48000;
  var CHANNELS = 2;
  var FRAME_SAMPLES = 960; // 20 ms at 48 kHz
  var CHUNK_MS = 40;
  var MAX_QUEUE_MS = 500; // drop oldest samples past this
  var TARGET_QUEUE_MS = 250;

  /* Linear-interpolation resampler for interleaved stereo Float32Array.
   * The common case (48 kHz in, 48 kHz context) returns the input
   * untouched; anything else is resampled the same way the Python
   * mixer does, so the two sides stay in agreement. */
  function resamplePcm(input, sourceRate, targetRate) {
    if (!input || !input.length) return new Float32Array(0);
    if (sourceRate === targetRate) return input;
    var step = sourceRate / targetRate;
    var inFrames = Math.floor(input.length / CHANNELS);
    var outFrames = Math.max(0, Math.floor((inFrames - 1) / step));
    var out = new Float32Array(outFrames * CHANNELS);
    var position = 0;
    for (var frame = 0; frame < outFrames; frame++) {
      var index = Math.floor(position);
      var frac = position - index;
      var base = index * CHANNELS;
      for (var ch = 0; ch < CHANNELS; ch++) {
        var first = input[base + ch];
        var second = input[base + CHANNELS + ch];
        out[frame * CHANNELS + ch] = first + (second - first) * frac;
      }
      position += step;
    }
    return out;
  }

  /* Incremental Server-Sent Events parser: feed(text) returns the
   * complete events parsed so far as [{type, data}], where data is the
   * raw string after "data:" (JSON is parsed by the caller). */
  function createSseParser() {
    var buffer = "";
    function feed(text) {
      buffer += String(text || "");
      var events = [];
      var end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        var raw = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        var type = "message";
        var data = [];
        raw.split("\n").forEach(function (line) {
          if (line.indexOf("event:") === 0) type = line.slice(6).trim();
          else if (line.indexOf("data:") === 0) data.push(line.slice(5).replace(/^ /, ""));
        });
        if (data.length) events.push({ type: type, data: data.join("\n") });
      }
      return events;
    }
    return { feed: feed };
  }

  /* Schedules int16 PCM frames into an AudioContext with a small
   * lookahead, so a bursty network stream becomes a continuous
   * recording. The timeline never slips: an empty queue schedules
   * silence, an over-full queue drops its oldest samples. The output
   * node is a GainNode the caller connects to an AnalyserNode and a
   * MediaStreamDestination (exactly like the getDisplayMedia path).
   *
   * push(chunk) takes an Int16Array (or ArrayBuffer) of interleaved
   * stereo frames at 48 kHz. Returns the feeder; call stop() to end it.
   * queuedMs() reports the current buffer depth for diagnostics. */
  function createPcmFeeder(audioCtx) {
    var output = audioCtx.createGain();
    var targetRate = audioCtx.sampleRate || SAMPLE_RATE;
    var chunks = []; // Float32Array chunks at targetRate
    var queuedFrames = 0;
    var offset = 0; // read offset (frames) into chunks[0]
    var nextTime = 0;
    var stopped = false;
    var peak = 0;

    function chunkFrames() {
      return Math.max(1, Math.round(targetRate * CHUNK_MS / 1000));
    }
    function takeFrames(frames) {
      var out = new Float32Array(frames * CHANNELS);
      var filled = 0;
      while (filled < frames && chunks.length) {
        var head = chunks[0];
        var headFrames = Math.floor(head.length / CHANNELS) - offset;
        var take = Math.min(frames - filled, headFrames);
        out.set(head.subarray(offset * CHANNELS, (offset + take) * CHANNELS), filled * CHANNELS);
        offset += take;
        filled += take;
        if (offset >= Math.floor(head.length / CHANNELS)) { chunks.shift(); offset = 0; }
      }
      return { data: out, filled: filled };
    }
    function dropOldest(frames) {
      var left = frames;
      while (left > 0 && chunks.length) {
        var head = chunks[0];
        var headFrames = Math.floor(head.length / CHANNELS) - offset;
        var drop = Math.min(left, headFrames);
        offset += drop;
        left -= drop;
        queuedFrames -= drop;
        if (offset >= Math.floor(head.length / CHANNELS)) { chunks.shift(); offset = 0; }
      }
    }
    function schedule() {
      if (stopped) return;
      try {
        if (audioCtx.state === "suspended" && typeof audioCtx.resume === "function") {
          audioCtx.resume().catch(function () {});
        }
      } catch (e) { /* resume is best-effort */ }
      var size = chunkFrames();
      if (!nextTime && queuedFrames < size) return;
      if (!nextTime || nextTime < audioCtx.currentTime) nextTime = audioCtx.currentTime + 0.01;
      var maxDepth = Math.round(targetRate * MAX_QUEUE_MS / 1000);
      if (queuedFrames > maxDepth) dropOldest(queuedFrames - Math.round(targetRate * TARGET_QUEUE_MS / 1000));
      while (nextTime < audioCtx.currentTime + 0.08) {
        var taken = takeFrames(size);
        queuedFrames -= taken.filled;
        var buffer = audioCtx.createBuffer(CHANNELS, size, targetRate);
        for (var ch = 0; ch < CHANNELS; ch++) {
          var channel = buffer.getChannelData(ch);
          for (var i = 0; i < size; i++) channel[i] = taken.data[i * CHANNELS + ch] || 0;
        }
        var source = audioCtx.createBufferSource();
        source.buffer = buffer;
        source.connect(output);
        try { source.start(nextTime); } catch (e) { /* a stale time is harmless */ }
        nextTime += size / targetRate;
      }
    }
    var timer = setInterval(schedule, 20);
    return {
      node: output,
      push: function (chunk) {
        if (stopped) return;
        var raw = chunk instanceof ArrayBuffer ? new Int16Array(chunk) : chunk;
        if (!raw || !raw.length) return;
        var floats = new Float32Array(raw.length);
        var localPeak = 0;
        for (var i = 0; i < raw.length; i++) {
          var value = raw[i] / 32768;
          floats[i] = value;
          var magnitude = Math.abs(value);
          if (magnitude > localPeak) localPeak = magnitude;
        }
        peak = Math.max(peak * 0.9, localPeak);
        var resampled = resamplePcm(floats, SAMPLE_RATE, targetRate);
        if (resampled.length) {
          chunks.push(resampled);
          queuedFrames += Math.floor(resampled.length / CHANNELS);
        }
      },
      peak: function () { return peak; },
      queuedMs: function () { return Math.round(queuedFrames / targetRate * 1000); },
      stop: function () {
        stopped = true;
        clearInterval(timer);
        try { output.disconnect(); } catch (e) {}
      }
    };
  }

  window.MemuNativeAudio = {
    SAMPLE_RATE: SAMPLE_RATE,
    CHANNELS: CHANNELS,
    FRAME_SAMPLES: FRAME_SAMPLES,
    createPcmFeeder: createPcmFeeder,
    createSseParser: createSseParser,
    resamplePcm: resamplePcm
  };
})();
