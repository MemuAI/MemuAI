

(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const t = (ru, en) => Q.t(ru, en);
  const root = document.documentElement;
  const POLL_MS = 120;
  const W = { id: null, timer: 0, phase: "", typed: false, el: null, glow: true };

  async function call(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { const err = new Error(data.error || "HTTP " + res.status); err.code = data.code; throw err; }
    return data;
  }

  const MIC = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></svg>';
  const STOP = '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>';
  const X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';
  Q.registerPanel("wake", (el) => {
    W.el = el;
    el.classList.add("mqw-host");
    el.innerHTML = `<div class="mqw" role="status" aria-live="polite">
        <span class="mqw-ic" aria-hidden="true">${MIC}</span>
        <span class="mqw-bars" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
        <span class="mqw-text"></span>
        <button type="button" class="mqw-btn" data-mqw="finish">${STOP}</button>
        <button type="button" class="mqw-btn" data-mqw="cancel">${X}</button>
      </div>`;
    el.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-mqw]");
      if (btn) finish(btn.dataset.mqw === "cancel");
    });
    return {};
  }, { slot: "top", order: 0, hidden: true });

  function paint(text, level) {
    if (!W.el) return;
    W.el.querySelector(".mqw-text").textContent = text;
    const finishBtn = W.el.querySelector('[data-mqw="finish"]'), cancelBtn = W.el.querySelector('[data-mqw="cancel"]');
    finishBtn.title = t("Закончить и отправить", "Finish and send"); finishBtn.setAttribute("aria-label", finishBtn.title);
    cancelBtn.title = t("Отменить (Esc)", "Cancel (Esc)"); cancelBtn.setAttribute("aria-label", cancelBtn.title);
    finishBtn.hidden = W.phase !== "listening";
    const v = Math.max(0, Math.min(1, (level || 0) * 9));
    W.el.style.setProperty("--mqw-level", v.toFixed(3));
    const mic = document.querySelector('.qa-plugin-btn[data-plugin="voice"]');
    if (mic) mic.style.setProperty("--mic", W.phase === "listening" ? v.toFixed(3) : "0");
  }

  function begin(info) {
    const id = info && info.id;
    if (!id || id === W.id) return;
    if (W.id) cleanup();
    W.id = id; W.phase = "listening"; W.typed = false; W.glow = info.glow !== false;
    if (window.MemuQuickVoice && window.MemuQuickVoice.speaking && window.MemuQuickVoice.speaking()) window.MemuQuickVoice.stop();
    root.dataset.mqwState = "listening";
    root.dataset.mqcListening = "1";
    if (W.glow) root.dataset.mqwGlow = "1";
    Q.showPanel("wake");
    paint(t("Слушаю… говорите", "Listening… go ahead"), 0);
    Q.emit("listen-start", { wake: true });
    clearTimeout(W.timer);
    W.timer = setTimeout(poll, POLL_MS);
  }

  async function poll() {
    if (!W.id) return;
    let out;
    try { out = (await call("/api/wake/session")).session; } catch (e) { out = undefined; }
    if (!W.id) return;
    if (out === undefined) { W.timer = setTimeout(poll, 400); return; }
    if (!out || String(out.id) !== String(W.id)) { cleanup(); return; }
    W.phase = out.phase;
    if (out.phase === "listening") {
      const words = String(out.partial || "").trim();
      if (words && (W.typed || !Q.getInput().trim())) { Q.setInput(words, { focus: false }); W.typed = true; }
      paint(out.heard ? t("Слушаю…", "Listening…") : t("Слушаю… говорите", "Listening… go ahead"), out.level);
      W.timer = setTimeout(poll, POLL_MS);
      return;
    }
    if (out.phase === "captured") { transcribe(out.id); return; }
    if (out.phase === "empty") Q.toast(t("Команды не слышно — скажите фразу ещё раз или нажмите микрофон", "No command heard — say the phrase again or press the mic"), "", 2600);
    end("done");
  }

  async function transcribe(id) {
    root.dataset.mqwState = "transcribing";
    delete root.dataset.mqcListening;
    root.dataset.mqcTranscribing = "1";
    paint(t("Распознаю…", "Transcribing…"), 0);
    let text = "", offline = "", problem = "";
    try {
      const full = (await call("/api/wake/session?audio=1")).session;
      if (!full || String(full.id) !== String(id) || !full.audio) { cleanup(); return; }
      offline = String(full.text || "").trim();
      try {
        const out = await call("/api/voice/transcribe", { audio: full.audio, quick: true });
        text = String(out.text || "").trim();
      } catch (e) {
        problem = e.code === "model_not_downloaded"
          ? t("модель распознавания не скачана", "the speech model is not downloaded") : (e.message || String(e));
      }
    } catch (e) { problem = e.message || String(e); }
    if (String(W.id) !== String(id)) return;
    if (!text && offline) {
      text = offline;
      if (problem) Q.toast(t("Распознано офлайн (Vosk): ", "Recognised offline (Vosk): ") + problem, "", 3200);
    }
    end("done");
    Q.emit("listen-end", { text, wake: true });
    if (!text) {
      if (W.typed) Q.setInput("", { focus: false });
      Q.toast(problem ? t("Не удалось распознать: ", "Could not transcribe: ") + problem : t("Не удалось расслышать", "Didn't catch that"), problem ? "err" : "", 3200);
      return;
    }
    Q.send(text);
  }

  function finish(cancel) {
    if (!W.id) return;
    if (!cancel) {
      if (W.phase !== "listening") return;
      call("/api/wake/session/end", { id: W.id, action: "finish" }).catch(() => {});
      clearTimeout(W.timer); W.timer = setTimeout(poll, 60);
      return;
    }
    if (W.typed) Q.setInput("", { focus: true });
    end("cancel");
  }

  function end(action) {
    const id = W.id;
    if (!id) return;
    call("/api/wake/session/end", { id, action }).catch(() => {});
    if (action === "cancel") Q.emit("listen-end", { text: "", wake: true });
    cleanup();
  }

  function cleanup() {
    clearTimeout(W.timer);
    W.id = null; W.phase = ""; W.typed = false;
    delete root.dataset.mqwState; delete root.dataset.mqwGlow; delete root.dataset.mqcListening; delete root.dataset.mqcTranscribing;
    paint("", 0);
    Q.hidePanel("wake");
  }

  Q.on("wake", begin);
  Q.on("open", (e) => { if (e && e.source === "wake") call("/api/wake/session").then((r) => { if (r.session && r.session.phase === "listening") begin({ id: r.session.id }); }).catch(() => {}); });
  Q.on("close", () => { if (W.id) end("cancel"); });
  Q.on("escape", (e) => { if (W.id) { e.preventDefault(); finish(true); } });

  document.addEventListener("click", (e) => {
    if (!W.id || !e.target.closest('.qa-plugin-btn[data-plugin="voice"]')) return;
    e.preventDefault(); e.stopPropagation();
    finish(false);
  }, true);
  document.addEventListener("pointerdown", (e) => {
    if (W.id && e.target.closest('.qa-plugin-btn[data-plugin="voice"]')) e.stopPropagation();
  }, true);

  const busy = (source, seconds) => call("/api/wake/busy", { source, on: seconds > 0, seconds }).catch(() => {});
  let speakRenew = 0;
  Q.on("speak-start", () => { busy("tts", 90); clearInterval(speakRenew); speakRenew = setInterval(() => busy("tts", 90), 60000); });
  Q.on("speak-end", () => { clearInterval(speakRenew); busy("tts", 1.5); });
  Q.on("listen-start", (e) => { if (!(e && e.wake)) busy("voice", 70); });
  Q.on("listen-end", (e) => { if (!(e && e.wake)) busy("voice", 0); });
})();
