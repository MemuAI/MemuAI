

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, esc, tr, icon, haptic, toast, api, actionSheet, openSheet } = kit;
    const MAX_SECONDS = 10 * 60, MAX_UPLOAD_SECONDS = 15 * 60, MAX_BYTES = 20 * 1024 * 1024;
    const AUDIO_RE = /\.(ogg|oga|opus|mp3|m4a|mp4|aac|wav|flac|webm|weba|amr)$/i;
    let active = null;

    const status = () => (S.features && S.features.voice) || { available: false, message: "" };
    const canRecord = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
    function pickMime() {
      const options = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4", "audio/aac"];
      for (const type of options) { try { if (MediaRecorder.isTypeSupported(type)) return type; } catch (e) {  } }
      return "";
    }
    const isAudioFile = (file) => /^audio\//.test(file.type || "") || AUDIO_RE.test(file.name || "");

    function explainUnavailable() {
      const info = status();
      openSheet({ title: tr("Голосовой ввод недоступен", "Voice input is unavailable"),
        body: `<div style="padding:0 20px 8px"><p class="section-note" style="margin:0 0 10px;font-size:15px;color:var(--text)">${esc(info.message || tr("Распознавание речи не настроено на компьютере.", "Speech recognition is not set up on the computer."))}</p>
          <p class="section-note" style="margin:0">${esc(tr("Речь распознаётся локально на вашем компьютере моделью Whisper — без облака. После установки модели откройте Mini App заново.", "Speech is recognised locally on your computer by a Whisper model — no cloud. After installing the model, reopen the Mini App."))}</p></div>`,
        actions: [{ label: tr("Проверить снова", "Check again"), kind: "secondary", run: async () => { await refreshStatus(true); if (status().available) toast(tr("Голосовой ввод готов", "Voice input is ready"), "ok"); else toast(status().message || "", "err"); } },
                  { label: tr("Понятно", "OK"), run: () => {} }] });
    }
    async function refreshStatus(force) {
      if (!force && S.voiceCheckedAt && Date.now() - S.voiceCheckedAt < 30000) return status();
      try { S.features.voice = await api("voice.status"); S.voiceCheckedAt = Date.now(); } catch (e) {  }
      document.querySelectorAll("[data-mic]").forEach(syncButton);
      return status();
    }
    function syncButton(button) {
      const info = status();
      button.classList.toggle("unavailable", !info.available);
      button.title = info.available ? tr("Голосом — нажмите, чтобы записать; удерживайте — загрузить аудиофайл", "Voice — tap to record; hold to upload an audio file")
        : (info.message || tr("Голосовой ввод недоступен", "Voice input is unavailable"));
      button.setAttribute("aria-label", button.title);
    }

    kit.hooks.composerExtras.push((target, view, entry, options = {}) => {
      const row = view.querySelector(".composer-row");
      const send = options.code ? $("#t-send", view) : $("#send-btn", view);
      if (!row || !send) return;
      const button = document.createElement("button");
      button.className = "round-btn mic-btn"; button.type = "button"; button.dataset.mic = "1";
      button.innerHTML = icon("mic");
      row.insertBefore(button, send);
      syncButton(button);
      const ctx = { target, view, code: !!options.code, input: options.input || $("#msg-input", view), button };
      button.onclick = () => {
        if (active) { if (active.ctx === ctx) stop(true); return; }
        if (!status().available) { refreshStatus(true).then((info) => { if (info.available) startOrUpload(ctx); else explainUnavailable(); }); return; }
        startOrUpload(ctx);
      };
      kit.longPress(button, () => {
        if (active) return;
        actionSheet(tr("Голосовой ввод", "Voice input"), [
          { label: tr("Записать голосом", "Record"), icon: "mic", run: () => (status().available ? start(ctx) : explainUnavailable()) },
          { label: tr("Загрузить аудиофайл", "Upload an audio file"), sub: tr("MP3, M4A, OGG, WAV, FLAC, WebM — до 15 минут", "MP3, M4A, OGG, WAV, FLAC, WebM — up to 15 minutes"), icon: "file", run: () => (status().available ? chooseFile(ctx) : explainUnavailable()) },
        ]);
      });
      if (entry && entry.view) entry.voiceCtx = ctx;
      const close = entry && entry.onClose;
      if (entry) entry.onClose = () => { if (active && active.ctx === ctx) stop(false); if (close) close(); };
    });

    function startOrUpload(ctx) { if (canRecord()) start(ctx); else chooseFile(ctx); }

    async function start(ctx) {
      if (active) return;
      let stream;
      try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
      catch (error) {
        const denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
        toast(denied ? tr("Нет доступа к микрофону — разрешите его или загрузите аудиофайл (удерживайте кнопку)", "No microphone access — allow it or upload an audio file (hold the button)")
          : tr("Микрофон недоступен — загрузите аудиофайл", "The microphone is unavailable — upload an audio file"), "err");
        if (!denied) chooseFile(ctx);
        return;
      }
      const mime = pickMime();
      let recorder;
      try { recorder = mime ? new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 32000 }) : new MediaRecorder(stream); }
      catch (error) { stream.getTracks().forEach((t) => t.stop()); toast(tr("Запись не поддерживается — загрузите аудиофайл", "Recording is not supported — upload an audio file"), "err"); chooseFile(ctx); return; }
      const chunks = [];
      active = { ctx, stream, recorder, chunks, started: Date.now(), timer: 0, send: true, meter: null };
      recorder.ondataavailable = (event) => { if (event.data && event.data.size) chunks.push(event.data); };
      recorder.onstop = () => finish(active && active.recorder === recorder ? active : null, recorder.mimeType || mime || "audio/webm");
      recorder.start(1000);
      haptic("medium");
      showBar(ctx);
      try { if (S.tg && S.tg.enableClosingConfirmation) S.tg.enableClosingConfirmation(); } catch (e) {  }
    }
    function showBar(ctx) {
      const box = ctx.view.querySelector(".composer-box");
      ctx.button.classList.add("recording"); ctx.button.innerHTML = icon("stop");
      ctx.button.setAttribute("aria-label", tr("Остановить запись", "Stop recording"));
      const bar = document.createElement("div");
      bar.className = "rec-bar";
      bar.innerHTML = `<button class="round-btn" data-rec-cancel aria-label="${esc(tr("Отменить", "Cancel"))}" title="${esc(tr("Отменить", "Cancel"))}">${icon("trash")}</button><span class="rec-dot"></span><span class="rec-time">0:00</span><canvas class="rec-level" width="160" height="28"></canvas><span class="rec-hint">${esc(tr("Нажмите ■, чтобы закончить", "Tap ■ to finish"))}</span>`;
      box.appendChild(bar);
      bar.querySelector("[data-rec-cancel]").onclick = () => stop(false);
      const time = bar.querySelector(".rec-time");
      active.bar = bar;
      active.timer = setInterval(() => {
        const seconds = Math.floor((Date.now() - active.started) / 1000);
        time.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
        if (seconds >= MAX_SECONDS) { toast(tr("Запись ограничена 10 минутами", "Recordings are limited to 10 minutes")); stop(true); }
      }, 250);
      try {
        const audio = new (window.AudioContext || window.webkitAudioContext)();
        const source = audio.createMediaStreamSource(active.stream);
        const analyser = audio.createAnalyser(); analyser.fftSize = 256;
        source.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        const canvas = bar.querySelector("canvas"), g = canvas.getContext("2d");
        const color = getComputedStyle(document.documentElement).getPropertyValue("--accent") || "#efa58b";
        const levels = new Array(32).fill(0);
        const draw = () => {
          if (!active || active.bar !== bar) { audio.close().catch(() => {}); return; }
          analyser.getByteTimeDomainData(data);
          let peak = 0; for (let i = 0; i < data.length; i++) peak = Math.max(peak, Math.abs(data[i] - 128));
          levels.shift(); levels.push(Math.min(1, peak / 64));
          g.clearRect(0, 0, canvas.width, canvas.height); g.fillStyle = color.trim();
          levels.forEach((v, i) => { const h = Math.max(2, v * canvas.height); g.fillRect(i * 5, (canvas.height - h) / 2, 3, h); });
          active.meter = requestAnimationFrame(draw);
        };
        draw();
      } catch (e) {  }
    }
    function stop(send) {
      if (!active) return;
      active.send = send;
      try { if (active.recorder.state !== "inactive") active.recorder.stop(); else finish(active, active.recorder.mimeType); } catch (e) { finish(active, "audio/webm"); }
    }
    function cleanup(rec) {
      clearInterval(rec.timer);
      if (rec.meter) cancelAnimationFrame(rec.meter);
      rec.stream.getTracks().forEach((t) => t.stop());
      if (rec.bar) rec.bar.remove();
      rec.ctx.button.classList.remove("recording"); rec.ctx.button.innerHTML = icon("mic"); syncButton(rec.ctx.button);
      try { if (S.tg && S.tg.disableClosingConfirmation) S.tg.disableClosingConfirmation(); } catch (e) {  }
    }
    async function finish(rec, mime) {
      if (!rec || rec.done) return;
      rec.done = true;
      active = null;
      cleanup(rec);
      if (!rec.send) { haptic("light"); return; }
      const seconds = (Date.now() - rec.started) / 1000;
      if (seconds < 0.8 || !rec.chunks.length) { toast(tr("Запись слишком короткая", "The recording is too short")); return; }
      const blob = new Blob(rec.chunks, { type: (mime || "audio/webm").split(";")[0] });
      await transcribe(rec.ctx, blob, "voice." + (/mp4|aac/.test(mime) ? "m4a" : /ogg/.test(mime) ? "ogg" : "webm"));
    }

    function chooseFile(ctx) {
      const input = document.createElement("input");
      input.type = "file"; input.accept = "audio/*,.ogg,.oga,.opus,.mp3,.m4a,.aac,.wav,.flac,.webm,.amr";
      input.onchange = () => { const file = input.files && input.files[0]; if (file) transcribeFile(ctx, file); };
      input.click();
    }
    function audioDuration(file) {
      return new Promise((resolve) => {
        let url = "";
        try { url = URL.createObjectURL(file); } catch (e) { resolve(0); return; }
        const audio = document.createElement("audio");
        const done = (value) => { try { URL.revokeObjectURL(url); } catch (e) {  } resolve(value); };
        audio.preload = "metadata";
        audio.onloadedmetadata = () => done(isFinite(audio.duration) ? audio.duration : 0);
        audio.onerror = () => done(0);
        setTimeout(() => done(0), 4000);
        audio.src = url;
      });
    }
    async function transcribeFile(ctx, file) {
      if (!status().available) { explainUnavailable(); return; }
      if (file.size > MAX_BYTES) { toast(tr("Файл больше 20 МБ", "The file is larger than 20 MB"), "err"); return; }
      const seconds = await audioDuration(file);
      if (seconds > MAX_UPLOAD_SECONDS) { toast(tr("Запись длиннее 15 минут — разделите её на части", "The recording is longer than 15 minutes — split it into parts"), "err"); return; }
      await transcribe(ctx, file, file.name || "audio");
    }

    async function transcribe(ctx, blob, name) {
      const button = ctx.button;
      button.classList.add("busy"); button.disabled = true;
      const pending = document.createElement("div");
      pending.className = "rec-bar pending";
      pending.innerHTML = `<div class="spinner"></div><span>${esc(tr("Распознаю речь на компьютере…", "Recognising speech on the computer…"))}</span>`;
      const box = ctx.view.querySelector(".composer-box"); if (box) box.appendChild(pending);
      try {
        const audio = await kit.readDataUrl(blob);
        const data = await api("voice.transcribe", { audio, name });
        const text = String(data.text || "").trim();
        if (!text) throw new Error(tr("Речь не распознана", "No speech was recognised"));
        const input = ctx.input;
        const review = data.review || (input && input.value.trim());
        haptic("success");
        if (review || !input) {
          if (input) {
            input.value = (input.value.trim() ? input.value.replace(/\s+$/, "") + " " : "") + text;
            input.dispatchEvent(new Event("input")); input.focus();
            toast(tr("Проверьте текст и отправьте", "Check the text and send it"), "ok");
          }
        } else if (ctx.code) {
          input.value = text; input.dispatchEvent(new Event("input"));
          const send = $("#t-send", ctx.view); if (send) send.click();
        } else {
          input.value = text; input.dispatchEvent(new Event("input"));
          const send = $("#send-btn", ctx.view); if (send && !send.disabled) send.click();
        }
      } catch (error) {
        if (error.code === "voice_unavailable") { S.features.voice = Object.assign({}, status(), { available: false, message: error.message }); syncButton(button); explainUnavailable(); }
        else toast(error.message || String(error), "err");
      } finally {
        pending.remove(); button.classList.remove("busy"); button.disabled = false;
      }
    }

    window.MemuTgVoice = { isAudioFile, transcribeFile: (C, file) => {
      const ctx = C && C.entry && C.entry.voiceCtx;
      if (ctx) return transcribeFile(ctx, file);
      toast(tr("Голосовой ввод недоступен здесь", "Voice input is not available here"), "err");
      return null;
    }, refreshStatus };
    kit.hooks.afterBoot.push(() => { if (!status().available) setTimeout(() => refreshStatus(true), 4000); });
  });
})();
