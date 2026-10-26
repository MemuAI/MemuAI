

"use strict";
const MemuVideo = (() => {
  const EXT = /\.(mp4|m4v|webm|mov|mkv|avi|ogv|3gp|mpg|mpeg|wmv)$/i;
  const FRAME_SIDE = 768;
  const TRANSCRIBE_SECONDS = 12 * 60;
  const TRANSCRIBE_MAX_FILE = 400 * 1024 * 1024;
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  function isVideo(file) {
    return String(file?.type || "").startsWith("video/") || EXT.test(String(file?.name || ""));
  }

  function fmt(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }

  function waitFor(target, event, timeout, failEvent = "error") {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { cleanup(); reject(new Error("timeout")); }, timeout);
      const ok = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); reject(new Error("decode")); };
      function cleanup() { clearTimeout(timer); target.removeEventListener(event, ok); target.removeEventListener(failEvent, fail); }
      target.addEventListener(event, ok, { once: true });
      target.addEventListener(failEvent, fail, { once: true });
    });
  }

  async function seek(video, time) {
    if (Math.abs(video.currentTime - time) < 0.01 && video.readyState >= 2) return;
    const done = waitFor(video, "seeked", 10_000);
    video.currentTime = time;
    await done;
  }

  async function openVideo(url) {
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "auto";
    video.playsInline = true;
    video.crossOrigin = "anonymous";
    const loaded = waitFor(video, "loadeddata", 20_000);
    video.src = url;
    await loaded;

    if (!Number.isFinite(video.duration)) {
      const done = waitFor(video, "durationchange", 10_000).catch(() => {});
      video.currentTime = 1e9;
      await done;
      await seek(video, 0).catch(() => {});
    }
    return video;
  }

  async function prepare(file, { maxFrames = 24, transcribe = true, onProgress = () => {} } = {}) {
    const url = URL.createObjectURL(file);
    try {
      let video;
      try {
        video = await openVideo(url);
      } catch (error) {
        throw new Error(t(`${file.name}: этот формат видео не открывается. Подойдут MP4 (H.264), WebM или MOV.`,
          `${file.name}: this video format cannot be opened. MP4 (H.264), WebM or MOV will work.`));
      }
      const duration = Number.isFinite(video.duration) ? video.duration : 0;
      const width = video.videoWidth, height = video.videoHeight;
      if (!width || !height) throw new Error(t(`${file.name}: в файле нет видеодорожки`, `${file.name}: the file has no video track`));

      const count = duration >= 4 ? Math.max(4, Math.min(maxFrames, Math.ceil(duration / 2))) : 1;
      const scale = Math.min(1, FRAME_SIDE / Math.max(width, height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(2, Math.round(width * scale));
      canvas.height = Math.max(2, Math.round(height * scale));
      const context = canvas.getContext("2d");
      const frames = [];
      for (let index = 0; index < count; index += 1) {
        const time = count === 1 ? Math.min(duration / 2, 1) : ((index + 0.5) * duration) / count;
        try { await seek(video, Math.max(0, Math.min(time, Math.max(0, duration - 0.05)))); } catch (error) { continue; }
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        frames.push({ t: Math.round(time * 10) / 10, data: canvas.toDataURL("image/jpeg", 0.78) });
        onProgress(t(`Кадры: ${frames.length} из ${count}`, `Frames: ${frames.length} of ${count}`));
      }
      video.removeAttribute("src");
      video.load();
      if (!frames.length) throw new Error(t(`${file.name}: не удалось извлечь кадры`, `${file.name}: no frames could be extracted`));
      const result = { name: file.name || "video", isVideo: true, frames, duration, width, height, size: file.size,
        poster: frames[Math.min(frames.length - 1, Math.floor(frames.length / 3))].data, transcript: "", transcriptNote: "" };
      if (transcribe) {
        if (typeof voiceInputReady === "function" && !voiceInputReady()) {
          result.transcriptNote = "stt_not_configured";
        } else if (file.size > TRANSCRIBE_MAX_FILE) {
          result.transcriptNote = "too_large";
        } else {
          try {
            onProgress(t("Расшифровываю звук…", "Transcribing the audio…"));
            const speech = await transcribeAudio(file);
            result.transcript = speech.text;
            result.transcriptNote = speech.partial ? "partial" : (speech.text ? "" : "silent");
          } catch (error) {

            result.transcriptNote = error?.code === "decode" ? "no_audio" : "stt_failed";
            if (result.transcriptNote === "stt_failed") result.transcriptError = String(error?.message || error || "").slice(0, 200);
          }
        }
      }
      return result;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function transcribeAudio(file) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context || !window.OfflineAudioContext) throw Object.assign(new Error("no audio api"), { code: "decode" });
    const buffer = await file.arrayBuffer();
    const context = new Context();
    let audio;
    try { audio = await context.decodeAudioData(buffer); }
    catch (error) { throw Object.assign(new Error("decode"), { code: "decode" }); }
    finally { context.close().catch(() => {}); }
    const seconds = Math.min(audio.duration, TRANSCRIBE_SECONDS);
    const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(seconds * 16000)), 16000);
    const source = offline.createBufferSource();
    source.buffer = audio;
    source.connect(offline.destination);
    source.start(0, 0, seconds);
    const rendered = await offline.startRendering();
    const samples = rendered.getChannelData(0);
    let peak = 0;
    for (let i = 0; i < samples.length; i += 97) peak = Math.max(peak, Math.abs(samples[i]));
    if (peak < 0.003) return { text: "", partial: false };
    const wav = encodeWav(samples, 16000);
    const result = await api("/api/voice/transcribe", { method: "POST", body: { audio: "data:audio/wav;base64," + toBase64(wav), raw: true } });
    return { text: String(result?.text || "").trim(), partial: audio.duration > seconds + 0.5 };
  }

  function encodeWav(samples, rate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const text = (offset, value) => { for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i)); };
    text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, "data"); view.setUint32(40, samples.length * 2, true);
    let offset = 44;
    for (let i = 0; i < samples.length; i += 1, offset += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  }

  function toBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  function header(video) {
    return `Video "${String(video.name || "video").replace(/"/g, "'")}": duration ${fmt(video.duration)}, ${video.width}×${video.height}, ` +
      `${(video.frames || []).length} frames sampled evenly with timestamps.`;
  }

  function directParts(video, limit = 16) {
    const frames = video.frames || [];
    const step = frames.length > limit ? frames.length / limit : 1;
    const picked = [];
    for (let i = 0; i < frames.length && picked.length < limit; i += step) picked.push(frames[Math.floor(i)]);
    const parts = [{ type: "text", text: `<video>\n${header(video)}${video.transcript ? `\n<transcript note="speech recognised from the audio track, may contain errors">\n${video.transcript}\n</transcript>` : ""}\n</video>` }];
    for (const frame of picked) {
      parts.push({ type: "text", text: `[frame at ${fmt(frame.t)}]` });
      parts.push({ type: "image_url", image_url: { url: frame.data } });
    }
    return parts;
  }

  function noteText(video) {
    return {
      stt_not_configured: t("звук не расшифрован: распознавание речи не настроено", "audio not transcribed: speech recognition is not set up"),
      too_large: t("звук не расшифрован: файл слишком большой", "audio not transcribed: the file is too large"),
      partial: t("расшифрованы первые 12 минут звука", "the first 12 minutes of audio were transcribed"),
      silent: t("речь в видео не найдена", "no speech found"),
      no_audio: t("звуковую дорожку прочитать не удалось", "the audio track could not be read"),
      stt_failed: t("звук не расшифрован: ", "audio not transcribed: ") + (video.transcriptError || t("ошибка распознавания", "recognition failed")),
    }[video.transcriptNote] || "";
  }

  return { isVideo, prepare, fmt, header, directParts, noteText };
})();
