

(function () {
  "use strict";
  const Core = window.MemuCompanionCore;
  if (!Core) return;
  let modelsCache = null, modelsAt = 0;

  async function fetchModels(force) {
    if (!force && modelsCache && Date.now() - modelsAt < 15000) return modelsCache;
    const response = await fetch("/api/companion/models", { credentials: "same-origin" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    modelsCache = await response.json();
    modelsAt = Date.now();
    return modelsCache;
  }
  function invalidate() { modelsCache = null; }

  function reducedMotion(prefs) {
    const sys = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    return sys || prefs.ui_motion === "off" || prefs.ui_motion === "reduced" || prefs.reduce_motion === true;
  }

  function personalityOf(prefs, surface) {
    if (surface === "quick" && prefs.quick_ask_personality) return String(prefs.quick_ask_personality);
    return String(prefs.voice_live_primary_personality || "warm");
  }

  function create(options) {
    const opts = Object.assign({ surface: "live", lang: "ru", bubble: false, movable: false }, options || {});
    const getPrefs = () => (typeof opts.prefs === "function" ? opts.prefs() : opts.prefs) || {};
    const wrap = document.createElement("div");
    wrap.className = "memu-companion loading";
    wrap.dataset.surface = opts.surface;
    wrap.innerHTML = '<iframe class="memu-companion-frame" title="MemuAI companion" tabindex="-1" allowtransparency="true"></iframe>' +
      '<div class="memu-companion-bubble" hidden></div><div class="memu-companion-note" hidden></div>' +
      (opts.movable ? '<div class="memu-companion-grip" aria-hidden="true"></div>' : "");
    const frame = wrap.querySelector("iframe");
    const bubbleNode = wrap.querySelector(".memu-companion-bubble");
    const noteNode = wrap.querySelector(".memu-companion-note");
    const self = {
      el: wrap, frame: frame, ready: false, loaded: false, visible: true, destroyed: false,
      model: null, audio: null, analyser: null, ctx: null, raf: 0, lastMouth: 0, bubbleTimer: 0,
      lastText: "", emotionTimer: 0, error: "",
    };
    (opts.container || document.body).appendChild(wrap);

    const pending = new Map();
    function send(type, data) {
      if (!self.ready || !frame.contentWindow) {
        if (type !== "mouth" && type !== "look" && type !== "cursor" && type !== "visible") pending.set(type === "gesture" ? "gesture" : type, data);
        return;
      }
      try { frame.contentWindow.postMessage(Object.assign({ source: "memu-companion", type: type }, data || {}), location.origin); } catch (_) {}
    }

    async function resolveModel() {
      const prefs = Core.normalizePrefs(getPrefs());
      try {
        const lib = await fetchModels();
        const models = lib.models || [];
        return models.find((m) => m.id === prefs.companion_model_id) || models.find((m) => m.id === "sample") || models[0] || null;
      } catch (_) {
        return { id: "sample", kind: "vrm", url: "/companion/models/sample/sample.vrm" };
      }
    }

    async function init() {
      const prefs = getPrefs();
      self.model = await resolveModel();
      send("init", { prefs: prefs, model: self.model, lang: opts.lang, personality: personalityOf(prefs, opts.surface),
        reducedMotion: reducedMotion(prefs) });
      send("visible", { on: self.visible });
      for (const [type, data] of pending) send(type, data);
      pending.clear();
    }

    function onMessage(event) {
      if (event.origin !== location.origin || event.source !== frame.contentWindow) return;
      const msg = event.data || {};
      if (msg.source !== "memu-stage") return;
      switch (msg.type) {
        case "ready": self.ready = true; init(); break;
        case "loaded":
          self.loaded = true; self.error = "";
          wrap.classList.remove("loading", "failed");
          note("");
          opts.onEvent && opts.onEvent("loaded", msg.meta);
          break;
        case "error":
          self.error = msg.code || "error";
          wrap.classList.remove("loading");
          wrap.classList.add("failed");
          note(msg.message || "");
          opts.onEvent && opts.onEvent("error", msg);
          break;
        case "contextlost": opts.onEvent && opts.onEvent("contextlost"); break;
        case "click": opts.onEvent && opts.onEvent("click"); break;
        case "bbox": self.bbox = msg; break;
        case "drag": drag(msg); break;
        case "wheel": resizeStep(-msg.delta); break;
      }
    }
    window.addEventListener("message", onMessage);

    function note(text) { noteNode.hidden = !text; noteNode.textContent = text || ""; }

    let dragStart = null;
    function drag(msg) {
      if (!opts.movable) return;
      if (msg.phase === "start") { const r = wrap.getBoundingClientRect(); dragStart = { left: r.left, top: r.top }; wrap.classList.add("dragging"); }
      else if (msg.phase === "move" && dragStart) place(dragStart.left + msg.dx, dragStart.top + msg.dy);
      else if (msg.phase === "end") {
        wrap.classList.remove("dragging");
        if (dragStart && msg.moved && opts.onMove) {
          const r = wrap.getBoundingClientRect();
          opts.onMove({ x: r.left / Math.max(1, innerWidth - r.width), y: r.top / Math.max(1, innerHeight - r.height) });
        }
        dragStart = null;
      }
    }
    function place(left, top) {
      const r = wrap.getBoundingClientRect();
      wrap.style.left = Math.max(0, Math.min(innerWidth - r.width, left)) + "px";
      wrap.style.top = Math.max(0, Math.min(innerHeight - r.height, top)) + "px";
      wrap.style.right = wrap.style.bottom = "auto";
      fitBubble();
    }

    function fitBubble() {
      if (bubbleNode.hidden) return;
      bubbleNode.classList.remove("below");
      bubbleNode.style.setProperty("--bubble-dx", "0px");
      const w = wrap.getBoundingClientRect(), b = bubbleNode.getBoundingClientRect(), m = 8;
      if (b.top < m && innerHeight - w.bottom > b.height + 2 * m) bubbleNode.classList.add("below");
      const dx = b.left < m ? m - b.left : (b.right > innerWidth - m ? innerWidth - m - b.right : 0);
      if (dx) bubbleNode.style.setProperty("--bubble-dx", Math.round(dx) + "px");
    }
    self.placeRelative = function (pos) {
      if (!opts.movable || !pos || typeof pos.x !== "number") return;
      const r = wrap.getBoundingClientRect();
      place(pos.x * Math.max(0, innerWidth - r.width), pos.y * Math.max(0, innerHeight - r.height));
    };
    function resizeStep(direction) {
      const prefs = Core.normalizePrefs(getPrefs());
      const next = Math.max(60, Math.min(200, prefs.companion_size + direction * 10));
      if (next !== prefs.companion_size && opts.onResize) opts.onResize(next);
    }
    if (opts.movable) {
      const grip = wrap.querySelector(".memu-companion-grip");
      grip.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        grip.setPointerCapture(event.pointerId);
        const startY = event.clientY, start = Core.normalizePrefs(getPrefs()).companion_size;
        const baseH = wrap.getBoundingClientRect().height / (start / 100);
        let size = start;
        const move = (e) => {
          size = Math.max(60, Math.min(200, Math.round((start / 100 * baseH + (e.clientY - startY)) / baseH * 100 / 5) * 5));
          wrap.style.setProperty("--companion-scale", String(size / 100));
        };
        const up = () => {
          grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up);
          if (size !== start && opts.onResize) opts.onResize(size);
        };
        grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
      });
      window.addEventListener("resize", onWindowResize);
    }
    function onWindowResize() {
      if (wrap.style.left) { const r = wrap.getBoundingClientRect(); place(r.left, r.top); }
    }

    let lookAt = 0;
    function onPointer(event) {
      if (!self.visible || !Core.normalizePrefs(getPrefs()).companion_look_at) return;
      const now = performance.now();
      if (now - lookAt < 33) return;
      lookAt = now;
      const r = frame.getBoundingClientRect();
      if (!r.width) return;
      send("cursor", {
        x: event.clientX - r.left, y: event.clientY - r.top,
        ox: event.screenX - event.clientX - window.screenX + r.left, oy: event.screenY - event.clientY - window.screenY + r.top,
      });
    }
    document.addEventListener("pointermove", onPointer, { passive: true });

    function audioContext() {
      if (!self.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        self.ctx = new AC();
      }
      if (self.ctx.state === "suspended") self.ctx.resume().catch(() => {});
      return self.ctx;
    }
    self.attachAnalyser = function (node) {
      self.detachAudio();
      if (!node) return;
      self.analyser = node;
      startMouthLoop();
    };
    self.attachAudio = function (element) {
      self.detachAudio();
      if (!element || !Core.normalizePrefs(getPrefs()).companion_lipsync) return;
      self.audio = element;

      const hook = () => {
        if (self.audio !== element || self.analyser) return;
        try {
          const capture = element.captureStream || element.mozCaptureStream;
          const stream = capture && capture.call(element);
          if (!stream || !stream.getAudioTracks().length) return;
          const ctx = audioContext();
          if (!ctx) return;
          const source = ctx.createMediaStreamSource(stream);
          const analyser = ctx.createAnalyser();
          analyser.fftSize = 1024;
          analyser.smoothingTimeConstant = 0.35;
          source.connect(analyser);
          self.analyser = analyser;
          self._source = source;
          startMouthLoop();
        } catch (_) {  }
      };
      element.addEventListener("playing", hook);
      self._unhook = () => element.removeEventListener("playing", hook);
      if (!element.paused) hook();
    };
    self.detachAudio = function () {
      if (self._unhook) { self._unhook(); self._unhook = null; }
      if (self._source) { try { self._source.disconnect(); } catch (_) {} self._source = null; }
      self.audio = null; self.analyser = null;
      cancelAnimationFrame(self.raf); self.raf = 0;
      send("mouth", { w: { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 } });
    };
    let timeBuf = null, freqBuf = null;
    function startMouthLoop() {
      cancelAnimationFrame(self.raf);
      const tick = () => {
        self.raf = requestAnimationFrame(tick);
        const a = self.analyser;
        if (!a || !self.visible) return;
        const now = performance.now();
        const fps = Core.normalizePrefs(getPrefs()).companion_fps;
        if (now - self.lastMouth < 1000 / fps - 2) return;
        self.lastMouth = now;
        if (!timeBuf || timeBuf.length !== a.fftSize) timeBuf = new Uint8Array(a.fftSize);
        if (!freqBuf || freqBuf.length !== a.frequencyBinCount) freqBuf = new Uint8Array(a.frequencyBinCount);
        a.getByteTimeDomainData(timeBuf);
        a.getByteFrequencyData(freqBuf);
        const open = Core.levelToOpenness(Core.rmsFromBytes(timeBuf));
        const bands = Core.bandsFromSpectrum(freqBuf, a.context.sampleRate, a.fftSize);
        send("mouth", { w: Core.visemeWeights(open, bands) });
      };
      self.raf = requestAnimationFrame(tick);
    }

    self.speak = function (on, text) {
      send("speak", { on: !!on, text: on && !self.analyser ? Core.stripTags(String(text || "")).slice(0, 4000) : "" });
      if (on) self.state("speaking");
    };
    self.text = function (value, final) {
      const raw = String(value || "");
      self.lastText = raw;
      const prefs = Core.normalizePrefs(getPrefs());
      if (opts.bubble && prefs.companion_bubble) {
        const clean = Core.stripTags(raw, !final).replace(/\s+/g, " ").trim();
        bubbleNode.textContent = clean.length > 240 ? "…" + clean.slice(-240) : clean;
        bubbleNode.hidden = !clean;
        fitBubble();
        clearTimeout(self.bubbleTimer);
        if (final) self.bubbleTimer = setTimeout(() => { bubbleNode.hidden = true; }, 7000);
      }

      const parsed = Core.parseEmotionTags(raw, { streaming: !final });
      if (parsed.last && prefs.companion_emotions) {
        if (self._lastTag !== parsed.last.emotion + parsed.last.index) {
          self._lastTag = parsed.last.emotion + parsed.last.index;
          self.emotion(parsed.last.emotion, parsed.last.intensity);
        }
      } else if (final && prefs.companion_emotions) {
        const mood = Core.emotionForAnswer(raw);
        if (mood.source !== "none") self.emotion(mood.emotion, mood.intensity);
      }
      if (final) self._lastTag = "";
    };
    self.state = function (name) {
      send("state", { name: name });
      wrap.dataset.state = name || "idle";
    };
    self.emotion = function (name, intensity) {
      if (name === "surprised") send("gesture", { name: "surprise" });
      send("emotion", { name: name, intensity: intensity == null ? 0.9 : intensity, hold: 8000 });
    };
    self.gesture = function (name) { if (name) send("gesture", { name: name }); };

    self.mouth = function (weights) { send("mouth", { w: weights }); };
    self.setVisible = function (on) {
      self.visible = !!on;
      wrap.hidden = !on;
      send("visible", { on: self.visible && document.visibilityState !== "hidden" });
    };
    self.setPrefs = function () {
      const prefs = getPrefs();
      wrap.style.setProperty("--companion-scale", String(Core.normalizePrefs(prefs).companion_size / 100));
      send("prefs", { prefs: prefs, personality: personalityOf(prefs, opts.surface), reducedMotion: reducedMotion(prefs) });
      resolveModel().then((model) => {
        if (model && self.model && (model.id !== self.model.id || model.url !== self.model.url)) {
          self.model = model; self.loaded = false; wrap.classList.add("loading");
          send("model", { model: model });
        }
      });
    };
    self.reload = function () {
      invalidate();
      pending.clear();
      self.ready = false; self.loaded = false;
      wrap.classList.add("loading");
      frame.src = "/companion/stage?surface=" + encodeURIComponent(opts.surface) + "&t=" + Date.now();
    };
    function onVisibility() { send("visible", { on: self.visible && document.visibilityState !== "hidden" }); }
    document.addEventListener("visibilitychange", onVisibility);

    let observer = null;
    if (typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver((entries) => {
        const shown = entries.some((e) => e.isIntersecting);
        send("visible", { on: shown && self.visible && document.visibilityState !== "hidden" });
      });
      observer.observe(wrap);
    }
    self.destroy = function () {
      self.destroyed = true;
      self.detachAudio();
      clearTimeout(self.bubbleTimer);
      window.removeEventListener("message", onMessage);
      window.removeEventListener("resize", onWindowResize);
      document.removeEventListener("pointermove", onPointer);
      document.removeEventListener("visibilitychange", onVisibility);
      if (observer) observer.disconnect();
      if (self.ctx) { try { self.ctx.close(); } catch (_) {} self.ctx = null; }
      frame.src = "about:blank";
      wrap.remove();
    };
    wrap.style.setProperty("--companion-scale", String(Core.normalizePrefs(getPrefs()).companion_size / 100));
    self.reload();
    return self;
  }

  window.MemuCompanion = { create: create, fetchModels: fetchModels, invalidate: invalidate, core: Core };
})();
