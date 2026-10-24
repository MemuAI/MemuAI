/* MemuAI Live на моделях реального времени (GPT-Live-1, Gemini Live, Grok Voice…).
 *
 * Окно не знает ключа провайдера: оно говорит с процессом MemuAI по
 * WebSocket /api/voice/realtime, а тот держит соединение с провайдером
 * (app/realtime_live.py). Здесь — микрофон (AudioWorklet), воспроизведение
 * ответа с мгновенной остановкой при перебивании, субтитры и сохранение
 * обеих сторон разговора обычными сообщениями чата.
 *
 * Сфера рисуется на canvas и дышит в такт голосу: пользователя, пока он
 * говорит, и модели, пока звучит ответ.
 */
"use strict";
const MemuRealtime = (() => {
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);

  const session = {
    ws: null, state: "idle", muted: false, chatId: "", model: "", info: null,
    capture: null, stream: null, source: null, node: null,
    player: null, gain: null, analyser: null, nextTime: 0, sources: new Set(),
    user: "", assistant: "", inputLevel: 0, outputLevel: 0, raf: 0, closing: false,
    hooks: {}, found: [], searching: false, epoch: 0, inputRate: 24000, outputRate: 24000,
    turns: { user: null, assistant: null }, inputPreroll: [], inputPrerollBytes: 0,
    providerTurns: new Map(), cancelled: new Set(), dropReply: false,
  };
  const AUDIO_TURN_SECONDS = 180, INPUT_PREROLL_SECONDS = 0.28, INPUT_FALLBACK_SECONDS = 8;
  let turnSequence = 0;
  /* Камера или экран: один источник за раз. kind — "camera", "screen" или "" (ничего);
     native — экран снимает сам MemuAI, потому что окно не смогло его захватить. */
  const video = { kind: "", stream: null, element: null, canvas: null, timer: 0, native: false, busy: false, epoch: 0 };
  const FRAME_MS = 1000, FRAME_SIDE = 1024, FRAME_QUALITY = 0.72;

  /* ------------------------------------------------------------ сфера */
  const orb = { canvas: null, ctx: null, colors: null, phase: 0, smooth: 0 };

  function readColors() {
    const probe = document.createElement("span");
    document.body.appendChild(probe);
    const read = (value) => { probe.style.color = ""; probe.style.color = value; return getComputedStyle(probe).color; };
    const colors = {
      a: read("var(--accent)"),
      b: read("var(--accent-2, var(--accent))"),
      c: read("color-mix(in oklab, var(--accent) 50%, var(--accent-2, var(--accent)))"),
      halo: read("color-mix(in srgb, var(--accent) 22%, transparent)"),
      highlight: read("color-mix(in srgb, var(--accent) 35%, white)"),
      rim: read("color-mix(in srgb, var(--accent-2, var(--accent)) 55%, rgba(0,0,0,.35))"),
    };
    const bg = read("var(--panel)").match(/\d+(\.\d+)?/g)?.map(Number) || [0, 0, 0];
    colors.dark = (0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]) < 128;
    probe.remove();
    return colors;
  }

  function drawOrb() {
    const canvas = orb.canvas;
    if (!canvas || !orb.ctx) return;
    const ctx = orb.ctx;
    const size = canvas.width;
    // Обычный Live (без модели реального времени) не даёт уровней звука:
    // сфера изображает состояние мягкой пульсацией.
    const level = session.ws
      ? (session.state === "speaking" ? session.outputLevel : session.state === "listening" ? session.inputLevel : 0)
      : orb.classic === "speaking" ? 0.07 + 0.05 * Math.sin(orb.phase * 9)
      : orb.classic === "listening" ? 0.04 + 0.03 * Math.sin(orb.phase * 13) : 0;
    orb.smooth += (Math.min(1, level * 3.2) - orb.smooth) * 0.18;
    const busy = session.ws ? (session.state === "connecting" || session.state === "thinking") : orb.classic === "working";
    orb.phase += 0.012 + orb.smooth * 0.05 + (busy ? 0.02 : 0);
    ctx.clearRect(0, 0, size, size);
    const cx = size / 2, cy = size / 2;
    const base = size * 0.3 * (1 + orb.smooth * 0.14);
    const layers = [[orb.colors.a, 0, 3, 0.85], [orb.colors.b, 2.1, 4, 0.6], [orb.colors.c, 4.2, 5, 0.45]];
    // На тёмном фоне слои складываются светом, на светлом — просто наслаиваются.
    ctx.globalCompositeOperation = orb.colors.dark ? "screen" : "source-over";
    const glow = ctx.createRadialGradient(cx, cy, base * 0.4, cx, cy, base * 1.6);
    glow.addColorStop(0, orb.colors.halo);
    glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, size, size);
    for (const [color, offset, lobes, alpha] of layers) {
      ctx.beginPath();
      for (let k = 0; k <= 96; k++) {
        const theta = (k / 96) * Math.PI * 2;
        const wobble = Math.sin(theta * lobes + orb.phase * (1.3 + offset * 0.2) + offset) * (0.045 + orb.smooth * 0.11)
          + Math.sin(theta * (lobes + 2) - orb.phase * 0.8 + offset) * 0.025;
        const r = base * (1 + wobble);
        const x = cx + Math.cos(theta) * r, y = cy + Math.sin(theta) * r;
        if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      const gradient = ctx.createRadialGradient(cx - base * 0.28, cy - base * 0.32, base * 0.05, cx, cy, base * 1.2);
      gradient.addColorStop(0, orb.colors.highlight);
      gradient.addColorStop(0.3, color);
      gradient.addColorStop(0.72, color);
      gradient.addColorStop(0.92, orb.colors.rim);
      gradient.addColorStop(1, "rgba(0,0,0,0)");
      ctx.globalAlpha = alpha;
      ctx.fillStyle = gradient;
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }

  function loop() {
    // Уровень выхода берём с анализатора: он честно показывает, что звучит.
    if (session.analyser) {
      const data = new Uint8Array(session.analyser.fftSize);
      session.analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) { const x = (v - 128) / 128; sum += x * x; }
      session.outputLevel = Math.sqrt(sum / data.length);
      if (session.state === "speaking" && session.nextTime && session.player && session.player.currentTime > session.nextTime + 0.25) {
        setState("listening");
      }
    }
    drawOrb();
    session.raf = requestAnimationFrame(loop);
  }

  function attachOrb(canvas) {
    orb.canvas = canvas;
    orb.ctx = canvas ? canvas.getContext("2d") : null;
    orb.colors = readColors();
    if (!session.raf) session.raf = requestAnimationFrame(loop);
  }

  function detachOrb() {
    cancelAnimationFrame(session.raf);
    session.raf = 0;
    if (orb.ctx && orb.canvas) orb.ctx.clearRect(0, 0, orb.canvas.width, orb.canvas.height);
  }

  /* ------------------------------------------------------------ состояние */
  function setState(state, text) {
    session.state = state;
    session.hooks.onState?.(state, text);
  }

  function caption(role, text, final, turn = audioTurn(role)) {
    session.hooks.onCaption?.(role, text, !!final);
    session.hooks.onTranscript?.(role, text, { turnId: turn.id, final: !!final });
  }

  function commit(role, turn = session.turns[role]) {
    if (!turn) return;
    const text = String(turn.text || "").trim();
    if (role === "user" && !turn.bytes) {
      // Provider speech detection can recognise a quiet, short utterance
      // below the local visualisation threshold. Freeze its real microphone
      // samples before playback begins, and publish only once text confirms
      // that this was a spoken turn (not background silence or typed input).
      if (!turn.fallbackChunks) {
        turn.fallbackChunks = session.inputPreroll.map(chunk => chunk.slice());
        session.inputPreroll = []; session.inputPrerollBytes = 0;
      }
      if (text && turn.fallbackChunks.length) {
        for (const chunk of turn.fallbackChunks) appendAudio("user", chunk, true, turn);
        turn.fallbackChunks = [];
      }
    }
    if (role === "user") turn.inputDetected = false;
    turn.closed = true;
    // Без текста реплика пользователя публикуется только если провайдер сам
    // услышал речь (speech_started): громкий фон, клавиатура или эхо выше
    // локального порога иначе превращались в пустые голосовые сообщения.
    if (!text && (!turn.bytes || role === "user" && !turn.heard)) return;
    if (turn.publishedText === text && turn.publishedBytes === turn.bytes) return;
    // Источники поиска относятся к ответу, во время которого их нашли.
    const sources = role === "assistant" ? session.found.splice(0) : [];
    const extra = { turnId: turn?.id || newTurnId() };
    if (sources.length) extra.sources = sources;
    const audio = turn.publishedBytes !== turn.bytes ? wavForTurn(role, turn) : null;
    if (audio) extra.audio = audio;
    turn.publishedText = text; turn.publishedBytes = turn.bytes;
    session.hooks.onTurn?.(role, text, extra);
  }

  function newTurnId() {
    return "live-" + Date.now().toString(36) + "-" + (++turnSequence).toString(36);
  }

  function audioTurn(role, providerId = "", fresh = false) {
    const key = providerId ? role + ":" + providerId : "";
    if (key && session.providerTurns.has(key)) return session.providerTurns.get(key);
    let current = session.turns[role];
    if (fresh || providerId && current?.providerId && current.providerId !== providerId) {
      commit(role, current);
      // Published clips live in the chat; old provider IDs keep only metadata.
      if (current) current.chunks = [];
      current = session.turns[role] = null;
      session[role] = "";
    }
    if (!current) current = session.turns[role] = {
      id: newTurnId(), chunks: [], bytes: 0, voicedBytes: 0, truncated: false,
      text: "", closed: false, providerId: "", inputDetected: false,
      rate: role === "user" ? session.inputRate : session.outputRate,
    };
    if (key) {
      current.providerId = providerId; session.providerTurns.set(key, current);
      while (session.providerTurns.size > 32) session.providerTurns.delete(session.providerTurns.keys().next().value);
    }
    return current;
  }

  function appendAudio(role, bytes, voiced = true, turn = audioTurn(role)) {
    if (!bytes?.byteLength) return;
    const maximum = turn.rate * 2 * AUDIO_TURN_SECONDS;
    const remaining = Math.max(0, maximum - turn.bytes);
    if (remaining < bytes.byteLength) turn.truncated = true;
    if (!remaining) return;
    const copy = bytes.slice(0, remaining - remaining % 2);
    if (!copy.byteLength) return;
    turn.chunks.push(copy);
    turn.bytes += copy.byteLength;
    if (voiced) turn.voicedBytes = turn.bytes;
  }

  function recordInput(pcm, level) {
    const bytes = new Uint8Array(pcm);
    const speaking = Number(level) >= 0.012;
    if (session.turns.user?.closed) {
      if (speaking) {
        // GPT-Live has no completed-turn event. A new locally detected
        // utterance closes the previous saved answer without interrupting
        // the provider's full-duplex audio stream.
        if (session.info?.protocol === "gpt-live") commit("assistant");
        audioTurn("user", "", true);
      }
      else { rememberInput(bytes); return; }
    }
    if (speaking && !session.turns.user?.bytes) {
      appendPreroll(audioTurn("user"));
      session.inputPreroll = []; session.inputPrerollBytes = 0;
    }
    if (speaking || session.turns.user?.bytes || session.turns.user?.inputDetected) {
      appendAudio("user", bytes, speaking);
    } else {
      rememberInput(bytes);
    }
  }

  function rememberInput(bytes) {
    const maximum = session.inputRate * 2 * INPUT_FALLBACK_SECONDS;
    const copy = bytes.slice(Math.max(0, bytes.byteLength - maximum));
    session.inputPreroll.push(copy); session.inputPrerollBytes += copy.byteLength;
    while (session.inputPrerollBytes > maximum && session.inputPreroll.length > 1) {
      session.inputPrerollBytes -= session.inputPreroll.shift().byteLength;
    }
  }

  function appendPreroll(turn) {
    let remaining = Math.round(session.inputRate * 2 * INPUT_PREROLL_SECONDS);
    const chunks = [];
    for (let index = session.inputPreroll.length - 1; index >= 0 && remaining > 0; index--) {
      const chunk = session.inputPreroll[index], take = Math.min(remaining, chunk.byteLength);
      chunks.unshift(chunk.subarray(chunk.byteLength - take)); remaining -= take;
    }
    for (const chunk of chunks) appendAudio("user", chunk, false, turn);
  }

  function wavForTurn(role, turn) {
    let length = turn.bytes;
    if (role === "user" && turn.voicedBytes) length = Math.min(length, turn.voicedBytes + Math.round(turn.rate * 0.3) * 2);
    if (!length) return null;
    const wav = new Uint8Array(44 + length), view = new DataView(wav.buffer);
    const label = (offset, value) => { for (let i = 0; i < value.length; i++) wav[offset + i] = value.charCodeAt(i); };
    label(0, "RIFF"); view.setUint32(4, 36 + length, true); label(8, "WAVE"); label(12, "fmt ");
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, turn.rate, true); view.setUint32(28, turn.rate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); label(36, "data"); view.setUint32(40, length, true);
    let offset = 44;
    for (const chunk of turn.chunks) {
      const part = chunk.subarray(0, Math.min(chunk.byteLength, wav.byteLength - offset));
      wav.set(part, offset); offset += part.byteLength;
      if (offset === wav.byteLength) break;
    }
    return { data: "data:audio/wav;base64," + base64FromPcm(wav.buffer), mime: "audio/wav",
      duration: length / (turn.rate * 2), truncated: turn.truncated };
  }

  /* ------------------------------------------------------------ камера и экран */
  function send(payload) {
    if (session.ws?.readyState !== WebSocket.OPEN) return false;
    session.ws.send(JSON.stringify(payload));
    return true;
  }

  function videoChanged() {
    session.hooks.onVideo?.({ kind: video.kind, native: video.native, stream: video.stream });
  }

  function sendFrame() {
    const element = video.element;
    if (!element || !element.videoWidth || session.ws?.readyState !== WebSocket.OPEN) return;
    // Соединение не успевает: кадр пропускаем, а не копим очередь, иначе модель увидит прошлое.
    if (session.ws.bufferedAmount > 1_500_000) return;
    const scale = Math.min(1, FRAME_SIDE / Math.max(element.videoWidth, element.videoHeight));
    const width = Math.max(2, Math.round(element.videoWidth * scale));
    const height = Math.max(2, Math.round(element.videoHeight * scale));
    const canvas = video.canvas || (video.canvas = document.createElement("canvas"));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    canvas.getContext("2d").drawImage(element, 0, 0, width, height);
    const data = canvas.toDataURL("image/jpeg", FRAME_QUALITY);
    send({ type: "video", mime: "image/jpeg", data: data.slice(data.indexOf(",") + 1) });
  }

  function stopVideo(notify = true) {
    video.epoch += 1;
    video.busy = false;
    clearInterval(video.timer);
    video.timer = 0;
    video.stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    if (video.element) { video.element.srcObject = null; video.element = null; }
    const had = !!video.kind;
    Object.assign(video, { kind: "", stream: null, native: false });
    if (had) send({ type: "video_end" });
    if (notify) videoChanged();
  }

  /** Включает камеру ("camera") или показ экрана ("screen"); тот же вид ещё раз — выключает. */
  async function setVideo(kind) {
    if (video.busy) return;
    if (!session.ws || !session.info?.video) return;
    const same = video.kind === kind;
    stopVideo(false);
    if (same || !kind) { videoChanged(); return; }
    video.busy = true;
    const ws = session.ws, epoch = session.epoch, videoEpoch = video.epoch;
    const current = () => session.ws === ws && session.epoch === epoch && video.epoch === videoEpoch && !session.closing;
    try {
      let stream = null;
      if (kind === "camera") {
        const device = pref("voice_camera_device_id", "");
        const shape = { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 15, max: 30 } };
        try {
          stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { ...(device ? { deviceId: { exact: device } } : {}), ...shape } });
        } catch (error) {
          // The chosen camera was unplugged: the default one rather than no camera at all.
          if (!device || !["OverconstrainedError", "NotFoundError"].includes(error?.name)) throw error;
          stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: shape });
        }
      } else {
        const asked = performance.now();
        try {
          if (!navigator.mediaDevices?.getDisplayMedia) throw new DOMException("no getDisplayMedia", "NotSupportedError");
          stream = await navigator.mediaDevices.getDisplayMedia({ audio: false, video: { frameRate: { ideal: 5, max: 15 } } });
        } catch (error) {
          if (!current()) return;
          // Отмена в окне выбора — решение пользователя, а не сбой. Отличаем по времени:
          // отказ самой оболочки приходит мгновенно, человек так быстро окно не закроет.
          const cancelled = (error?.name === "NotAllowedError" || error?.name === "AbortError")
            && performance.now() - asked > 700 && !/by system/i.test(String(error.message || ""));
          if (cancelled) { videoChanged(); return; }
          // Окно не умеет захват экрана: его снимает сам MemuAI и присылает миниатюру.
          Object.assign(video, { kind: "screen", native: true, stream: null });
          send({ type: "screen", on: true });
          videoChanged();
          return;
        }
      }
      if (!current()) { stream.getTracks().forEach((track) => track.stop()); return; }
      const element = document.createElement("video");
      element.muted = true; element.playsInline = true; element.srcObject = stream;
      await element.play().catch(() => {});
      if (!current()) { stream.getTracks().forEach((track) => track.stop()); element.srcObject = null; return; }
      Object.assign(video, { kind, stream, element, native: false });
      // Пользователь остановил показ кнопкой браузера или отключил камеру.
      stream.getVideoTracks().forEach((track) => { track.onended = () => stopVideo(); });
      video.timer = setInterval(sendFrame, FRAME_MS);
      setTimeout(sendFrame, 150);
      videoChanged();
    } catch (error) {
      if (!current()) return;
      stopVideo(false);
      videoChanged();
      session.hooks.onError?.((kind === "camera" ? t("Камера недоступна: ", "Camera unavailable: ")
        : t("Не удалось показать экран: ", "Screen sharing failed: ")) + (kind === "camera" ? cameraError(error) : (error.message || error.name)), "video");
    } finally {
      if (video.epoch === videoEpoch) video.busy = false;
    }
  }

  /* ------------------------------------------------------------ звук */
  function base64FromPcm(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  function playPcm(b64, turn = audioTurn("assistant")) {
    if (!session.player) return;
    const raw = atob(b64);
    const count = raw.length >> 1;
    if (!count) return;
    const pcm = new Uint8Array(count * 2);
    for (let i = 0; i < pcm.length; i++) pcm[i] = raw.charCodeAt(i);
    appendAudio("assistant", pcm, true, turn);
    // Звук может прийти, пока ждём микрофон после «ready»: session.info ещё
    // пуст, а плеер уже создан на частоте провайдера (session.outputRate).
    // С запасным 24000 ответ 16 кГц звучал бы ускоренно и выше тоном.
    const buffer = session.player.createBuffer(1, count, session.info?.output_rate || session.outputRate || 24000);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < count; i++) {
      let value = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
      if (value >= 0x8000) value -= 0x10000;
      data[i] = value / 0x8000;
    }
    const node = session.player.createBufferSource();
    node.buffer = buffer;
    node.connect(session.gain);
    const now = session.player.currentTime;
    if (session.nextTime < now + 0.03) session.nextTime = now + 0.06;
    node.start(session.nextTime);
    session.nextTime += buffer.duration;
    session.sources.add(node);
    node.onended = () => session.sources.delete(node);
    if (session.state !== "speaking") setState("speaking");
  }

  function stopPlayback() {
    for (const node of session.sources) { try { node.stop(); } catch (e) { /* уже остановлен */ } }
    session.sources.clear();
    session.nextTime = 0;
  }

  /** The microphone chosen in «Голос и Live»; when it is gone (unplugged headset) — the default one,
   *  instead of failing every Live session and dictation with «Requested device not found». */
  async function microphone(audio) {
    if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("getUserMedia", "NotSupportedError");
    const device = pref("voice_input_device_id", "");
    try {
      return await navigator.mediaDevices.getUserMedia({ audio: { ...(device ? { deviceId: { exact: device } } : {}), ...audio } });
    } catch (error) {
      if (!device || !["OverconstrainedError", "NotFoundError"].includes(error?.name)) throw error;
      return navigator.mediaDevices.getUserMedia({ audio });
    }
  }

  /** A microphone failure in the window's language (the browser's own text is English-only). */
  function micError(error) {
    const name = error?.name || "";
    if (name === "NotFoundError" || name === "OverconstrainedError")
      return t("микрофон не найден — подключите его или выберите другой в «Голос и Live»", "no microphone found — connect one or pick another in Voice & Live");
    if (name === "NotAllowedError" || name === "SecurityError")
      return t("нет доступа — разрешите доступ к микрофону в параметрах конфиденциальности Windows", "access denied — allow microphone access in Windows privacy settings");
    if (name === "NotReadableError" || name === "AbortError")
      return t("микрофон занят другой программой или выключен", "the microphone is busy in another app or turned off");
    if (name === "NotSupportedError") return t("запись микрофона недоступна в этом окне", "microphone recording is unavailable in this window");
    return error?.message || name || t("неизвестная ошибка", "unknown error");
  }

  function cameraError(error) {
    const name = error?.name || "";
    if (name === "NotFoundError" || name === "OverconstrainedError") return t("камера не найдена", "no camera found");
    if (name === "NotAllowedError" || name === "SecurityError")
      return t("нет доступа — разрешите доступ к камере в параметрах конфиденциальности Windows", "access denied — allow camera access in Windows privacy settings");
    if (name === "NotReadableError" || name === "AbortError") return t("камера занята другой программой", "the camera is busy in another app");
    return error?.message || name;
  }

  async function startMicrophone(rate, current) {
    const stream = await microphone({
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: pref("voice_noise_suppression", true),
      autoGainControl: pref("voice_auto_gain", true),
    });
    if (!current()) { stream.getTracks().forEach((track) => track.stop()); return false; }
    const capture = new AudioContext();
    session.stream = stream;
    session.capture = capture;
    try {
      await capture.audioWorklet.addModule("/realtime-worklet.js?v=1.1");
      if (!current()) { stream.getTracks().forEach((track) => track.stop()); await capture.close(); return false; }
      session.source = capture.createMediaStreamSource(stream);
      session.node = new AudioWorkletNode(capture, "memu-capture", { processorOptions: { targetRate: rate } });
    } catch (error) {
      stream.getTracks().forEach((track) => track.stop());
      try { await capture.close(); } catch (e) { /* already closed */ }
      throw error;
    }
    session.node.port.onmessage = (event) => {
      if (!current()) return;
      const { pcm, level } = event.data || {};
      session.inputLevel = session.muted ? 0 : (level || 0);
      if (!pcm || session.muted || session.ws.readyState !== WebSocket.OPEN) return;
      recordInput(pcm, level);
      session.ws.send(JSON.stringify({ type: "audio", data: base64FromPcm(pcm) }));
    };
    session.source.connect(session.node);
    // Узел должен быть подключён к выходу, иначе браузер его не тикает; звук глушим.
    const silent = session.capture.createGain();
    silent.gain.value = 0;
    session.node.connect(silent).connect(session.capture.destination);
    return true;
  }

  function startPlayer(rate) {
    session.player = new AudioContext({ sampleRate: rate });
    session.gain = session.player.createGain();
    session.gain.gain.value = Math.max(0, Math.min(1, (+pref("voice_tts_volume", 100) || 0) / 100));
    session.analyser = session.player.createAnalyser();
    session.analyser.fftSize = 512;
    session.gain.connect(session.analyser);
    session.analyser.connect(session.player.destination);
  }

  /* ------------------------------------------------------------ протокол */
  function onEvent(event) {
    switch (event.type) {
      case "ready":
        session.info = event;
        setState("listening", "");
        session.hooks.onReady?.(event);
        break;
      case "audio":
        // Хвост перебитого ответа: провайдер успел отправить его до отмены.
        if (event.turn_id ? session.cancelled.has(event.turn_id) : session.dropReply) break;
        if (!session.turns.user?.closed) commit("user");
        playPcm(event.data, audioTurn("assistant", event.turn_id || "", !event.turn_id && !!session.turns.assistant?.closed));
        break;
      case "user_transcript":
        session.dropReply = false;
        {
          const freshQuietTurn = !event.turn_id && event.delta && session.turns.user?.closed &&
            !!session.turns.user?.publishedText && session.inputPrerollBytes >= session.inputRate * 2 * 0.2;
          if (freshQuietTurn && session.info?.protocol === "gpt-live") commit("assistant");
          const turn = audioTurn("user", event.turn_id || "", freshQuietTurn);
          if (event.delta) turn.text += event.delta;
          else if (typeof event.text === "string") turn.text = event.text;
          if (session.turns.user === turn) session.user = turn.text;
          caption("user", turn.text, event.final, turn);
          if (event.final) commit("user", turn);
        }
        break;
      case "assistant_transcript":
        {
          if (!session.turns.user?.closed) commit("user");
          const turn = audioTurn("assistant", event.turn_id || "", !event.turn_id && !event.final && !!session.turns.assistant?.closed);
          if (event.delta) turn.text += event.delta;
          else if (typeof event.text === "string" && event.text) turn.text = event.text;
          if (session.turns.assistant === turn) session.assistant = turn.text;
          caption("assistant", turn.text, event.final, turn);
          // Transcript completion can precede the last audio chunk. The
          // response boundary publishes one complete, replayable voice clip.
        }
        break;
      case "interrupted":
        cancelReply(false);
        stopPlayback();
        if (session.assistant || session.turns.assistant?.bytes) commit("assistant");
        setState("listening");
        break;
      case "speech_started":
        session.dropReply = false;
        {
          const turn = audioTurn("user", event.turn_id || "", !!session.turns.user?.closed && !event.turn_id);
          if (!turn.bytes) appendPreroll(turn);
          turn.inputDetected = true; turn.heard = true;
          session.inputPreroll = []; session.inputPrerollBytes = 0;
        }
        if (session.state !== "speaking") setState("listening");
        break;
      case "speech_stopped":
        commit("user", audioTurn("user", event.turn_id || ""));
        break;
      case "turn_complete":
        session.dropReply = false;
        if (session.user || session.turns.user?.bytes) commit("user");
        if (session.assistant || session.turns.assistant?.bytes) commit("assistant");
        break;
      case "delegating":
        setState(event.active ? "thinking" : (session.sources.size ? "speaking" : "listening"));
        break;
      case "searching":
        session.searching = !!event.active;
        session.hooks.onSearch?.(session.searching, event.query || "", event.tool || "");
        break;
      case "sources":
        for (const row of event.sources || []) {
          if (row?.url && !session.found.some((item) => item.url === row.url)) session.found.push({ url: row.url, title: row.title || "" });
        }
        break;
      case "search_mode":
        if (session.info) session.info.search = event.search || "";
        session.hooks.onReady?.(session.info);
        break;
      case "preview":
        if (video.native) session.hooks.onPreview?.("data:image/jpeg;base64," + event.data);
        break;
      case "notice":
        session.hooks.onNotice?.(event.message || "");
        break;
      case "error":
        session.hooks.onError?.(event.message || t("Ошибка Live-модели", "Live model error"), event.code || "");
        break;
      case "closed":
        finish(false);
        break;
      default:
        break;
    }
  }

  async function start(hooks) {
    if (session.ws) return;
    session.hooks = hooks || {};
    session.closing = false;
    session.user = session.assistant = "";
    session.turns = { user: null, assistant: null };
    session.providerTurns.clear();
    session.cancelled.clear(); session.dropReply = false;
    session.inputPreroll = []; session.inputPrerollBytes = 0;
    session.muted = false;
    session.found.length = 0;
    session.searching = false;
    setState("connecting", t("Подключаюсь к Live-модели…", "Connecting to the live model…"));
    const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/api/voice/realtime";
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (error) {
      session.hooks.onError?.(error.message || String(error));
      setState("idle");
      return;
    }
    session.ws = ws;
    const epoch = ++session.epoch;
    const current = () => session.ws === ws && session.epoch === epoch && !session.closing;
    ws.onopen = () => { if (current()) ws.send(JSON.stringify({ type: "start", instructions: session.hooks.instructions || "", model: session.hooks.model || "" })); };
    ws.onmessage = async (message) => {
      if (!current()) return;
      let event;
      try { event = JSON.parse(message.data); } catch (e) { return; }
      if (event.type === "ready") {
        try {
          session.inputRate = event.input_rate || 24000;
          session.outputRate = event.output_rate || 24000;
          startPlayer(event.output_rate || 24000);
          if (!await startMicrophone(event.input_rate || 24000, current)) return;
        } catch (error) {
          if (!current()) return;
          session.hooks.onError?.(t("Микрофон недоступен: ", "Microphone unavailable: ") + micError(error));
          stop();
          return;
        }
      }
      if (current()) onEvent(event);
    };
    ws.onerror = () => { if (current()) session.hooks.onError?.(t("Соединение с MemuAI прервалось", "Connection to MemuAI was lost")); };
    ws.onclose = () => { if (session.ws === ws && session.epoch === epoch) finish(false); };
  }

  function finish(sendEnd) {
    if (!session.ws && session.state === "idle") return;
    const ws = session.ws;
    session.epoch += 1;
    stopVideo();
    session.ws = null;
    if (session.user || session.turns.user?.bytes) commit("user");
    if (session.assistant || session.turns.assistant?.bytes) commit("assistant");
    if (ws && ws.readyState === WebSocket.OPEN) {
      try { if (sendEnd) ws.send(JSON.stringify({ type: "end" })); } catch (e) { /* закрыто */ }
      setTimeout(() => { try { ws.close(); } catch (e) { /* закрыто */ } }, sendEnd ? 600 : 0);
    } else if (ws) {
      try { ws.close(); } catch (e) { /* already closed */ }
    }
    stopPlayback();
    try { session.node?.port.close(); } catch (e) { /* нет порта */ }
    session.stream?.getTracks().forEach((track) => track.stop());
    for (const context of [session.capture, session.player]) { try { context?.close(); } catch (e) { /* уже закрыт */ } }
    Object.assign(session, { capture: null, stream: null, source: null, node: null, player: null, gain: null, info: null,
      analyser: null, inputLevel: 0, outputLevel: 0, turns: { user: null, assistant: null }, inputPreroll: [], inputPrerollBytes: 0 });
    setState("idle");
    session.hooks.onClose?.();
  }

  function stop() {
    session.closing = true;
    finish(true);
  }

  function setMuted(muted) {
    session.muted = !!muted;
    if (session.muted) { session.inputPreroll = []; session.inputPrerollBytes = 0; }
    session.node?.port.postMessage({ muted: session.muted });
    if (session.ws?.readyState === WebSocket.OPEN) session.ws.send(JSON.stringify({ type: muted ? "mute" : "unmute" }));
    session.hooks.onMute?.(session.muted);
  }

  /** Запоминает перебитый ответ, чтобы не доигрывать его звук, пришедший после отмены.
   *  Без ID реплик (Gemini, GPT-Live) глушим до конца хода или новой речи пользователя. */
  function cancelReply(byUser) {
    const id = session.turns.assistant?.providerId;
    if (id) {
      session.cancelled.add(id);
      while (session.cancelled.size > 32) session.cancelled.delete(session.cancelled.values().next().value);
    } else if (byUser) session.dropReply = true;
  }

  function interrupt() {
    cancelReply(true);
    stopPlayback();
    if (session.assistant || session.turns.assistant?.bytes) commit("assistant");
    if (session.ws?.readyState === WebSocket.OPEN) session.ws.send(JSON.stringify({ type: "interrupt" }));
    setState("listening");
  }

  function sendText(text) {
    const value = String(text || "").trim();
    if (!value || session.ws?.readyState !== WebSocket.OPEN) return false;
    if (session.assistant || session.turns.assistant?.bytes) commit("assistant");
    session.dropReply = false;
    // Субтитр показывает набранную реплику, а не прошлую произнесённую.
    session.hooks.onCaption?.("user", value, true);
    session.hooks.onTurn?.("user", value, { turnId: newTurnId() });
    session.ws.send(JSON.stringify({ type: "text", text: value }));
    return true;
  }

  function setClassicState(state) { orb.classic = state || ""; }

  return {
    start, stop, setMuted, interrupt, sendText, attachOrb, detachOrb, setClassicState, setVideo, microphone, micError,
    get video() { return { kind: video.kind, native: video.native, stream: video.stream }; },
    get searching() { return session.searching; },
    get active() { return !!session.ws; },
    get state() { return session.state; },
    get muted() { return session.muted; },
    get info() { return session.info; },
    // Round 22: the output analyser drives the companion's lip sync (companion/companion-live.js).
    get analyser() { return session.analyser || null; },
  };
})();
