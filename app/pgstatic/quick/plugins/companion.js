/* Quick Ask plugin: the animated companion (Round 22, r22-companion).

   Uses only the MemuQuick contract: events open/close/submit/answer-start/
   answer-delta/answer-done/speak-start/speak-end, registerPanel (inline
   avatar above the answer) and settingsSection. The renderer itself is the
   shared stage (/companion/*). The separate desktop window
   (app/companion_window.py) is driven over BroadcastChannel "memu-companion"
   (same WebView2 profile) with a server relay as the fallback.

   speak-start may carry any of: {audio: HTMLAudioElement} | {analyser: AnalyserNode}
   | {level: () => number 0…1} — plus {text}. Without one the mouth follows the text.

   Round 23 (r23-quick) — a companion you can carry:
   * drag it inside the window: it settles left / centre / right of its strip (remembered);
   * drag it past the window edge: the transparent desktop window takes over under the cursor
     (/api/companion/window {action:"detach"} runs a native drag until the mouse is released);
     dropping it back onto Quick Ask docks it inline again (also the stage's «в окно» button);
   * resize: the corner grip or Ctrl+wheel (companion_size, 60–200 %);
   * pin: it moves to the desktop at the same spot and stays when Quick Ask hides;
   * where it lives is in companion/state.json["quick"] = {place: inline|desktop, pinned, align}. */
(function () {
  "use strict";
  const Q = window.MemuQuick;
  if (!Q) return;
  const VERSION = "1.4-r34";

  function load(tag, attrs) {
    return new Promise((resolve) => {
      const node = document.createElement(tag);
      Object.assign(node, attrs);
      node.onload = () => resolve(true);
      node.onerror = () => resolve(false);
      (tag === "link" ? document.head : document.body).appendChild(node);
    });
  }
  let libsReady = null;
  function libs() {
    if (!libsReady) libsReady = (async () => {
      load("link", { rel: "stylesheet", href: "/companion/companion.css?v=" + VERSION });
      for (const file of ["companion-core.js", "companion.js", "settings.js"]) {
        if (file === "companion-core.js" && window.MemuCompanionCore) continue;
        if (file === "companion.js" && window.MemuCompanion) continue;
        if (file === "settings.js" && window.MemuCompanionSettings) continue;
        await load("script", { src: "/companion/" + file + "?v=" + VERSION });
      }
      return !!(window.MemuCompanionCore && window.MemuCompanion);
    })();
    return libsReady;
  }

  const P = { prefs: {}, inst: null, panel: null, panelEl: null, answer: "", speaking: false, greetedAt: 0,
    levelRaf: 0, desktopShown: false, channel: null, bcFails: 0,
    place: "inline", pinned: false, align: "center", supported: null, drag: null, detaching: false };
  const en = () => Q.lang() === "en";
  const t = (ru, e) => (en() ? e : ru);

  async function loadPrefs() {
    try {
      const response = await fetch("/api/pg/preferences", { cache: "no-store" });
      if (response.ok) P.prefs = await response.json();
    } catch (_) { /* keep the last */ }
    return P.prefs;
  }
  const C = () => window.MemuCompanionCore.normalizePrefs(P.prefs);
  // mode (Settings) × place (where the user carried it): "inline" + carried out = on the desktop.
  const inlineOn = () => { const c = C(); return c.companion_quick_enabled && (c.companion_quick_mode === "both" || (c.companion_quick_mode === "inline" && P.place !== "desktop")); };
  const desktopOn = () => { const c = C(); return c.companion_quick_enabled && (c.companion_quick_mode !== "inline" || P.place === "desktop"); };
  // the desktop companion hides together with Quick Ask unless pinned (or the setting says «on the desktop»)
  const staysOnDesktop = () => P.pinned || C().companion_quick_mode === "desktop";

  async function loadPlace() {
    try {
      const response = await fetch("/api/companion/state", { cache: "no-store" });
      const state = response.ok ? await response.json() : {};
      const quick = state && typeof state.quick === "object" && state.quick ? state.quick : {};
      P.place = quick.place === "desktop" ? "desktop" : "inline";
      P.pinned = !!quick.pinned;
      P.align = ["left", "center", "right"].includes(quick.align) ? quick.align : "center";
    } catch (_) { /* defaults */ }
  }
  function savePlace(patch) {
    Object.assign(P, patch);
    fetch("/api/companion/state", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quick: { place: P.place, pinned: P.pinned, align: P.align } }) }).catch(() => {});
  }

  /* ------------------------------------------------------------ inline */
  function mount(el) {
    P.panelEl = el;
    el.classList.add("qa-companion");
    el.dataset.align = P.align;
    return {
      show() { if (P.inst) P.inst.setVisible(true); },
      hide() { if (P.inst) P.inst.setVisible(false); },
      unmount() { if (P.inst) P.inst.destroy(); P.inst = null; },
    };
  }
  const ICON = {
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4h6l-1 6 3 3H7l3-3z"/><path d="M12 16v5"/></svg>',
    out: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-8 8"/><path d="M19 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h4"/></svg>',
  };
  function decorate(wrap) {
    if (wrap.querySelector(".qac-tools")) return;
    const tools = document.createElement("div");
    tools.className = "qac-tools";
    tools.innerHTML = `<button type="button" data-qac="pin" aria-pressed="false">${ICON.pin}</button><button type="button" data-qac="out">${ICON.out}</button>`;
    wrap.appendChild(tools);
    const grip = document.createElement("div");
    grip.className = "qac-grip";
    grip.setAttribute("aria-hidden", "true");
    wrap.appendChild(grip);
    const hint = document.createElement("div");
    hint.className = "qac-hint";
    wrap.appendChild(hint);
    tools.addEventListener("click", (event) => {
      const kind = event.target.closest("[data-qac]")?.dataset.qac;
      if (kind === "pin") (P.pinned ? unpinFromDesktop() : pinToDesktop());
      else if (kind === "out") popOut(false);
    });
    // resize by the corner grip: the height follows the mouse (60–200 %)
    grip.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      grip.setPointerCapture(event.pointerId);
      const start = C().companion_size, startY = event.clientY;
      const base = wrap.getBoundingClientRect().height / (start / 100);
      let size = start;
      const move = (e) => {
        size = Math.max(60, Math.min(200, Math.round(((start / 100) * base + (e.clientY - startY)) / base * 20) * 5));
        wrap.style.setProperty("--companion-scale", String(size / 100));
        Q.resize();
      };
      const up = () => {
        grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up);
        if (size !== start) savePref({ companion_size: size });
      };
      grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
    });
    labelTools();
  }
  function labelTools() {
    const wrap = P.inst && P.inst.el;
    if (!wrap) return;
    const pin = wrap.querySelector('[data-qac="pin"]'), out = wrap.querySelector('[data-qac="out"]'), hint = wrap.querySelector(".qac-hint");
    const can = P.supported !== false;
    if (pin) { pin.hidden = !can; pin.title = t("Закрепить на рабочем столе: останется, когда окно скрыто", "Pin to the desktop: stays when the window hides"); }
    if (out) { out.hidden = !can; out.title = t("Вынести на рабочий стол", "Move to the desktop"); }
    if (hint) hint.textContent = can ? t("За край окна — на рабочий стол · Ctrl+колесо — размер", "Past the edge — to the desktop · Ctrl+wheel — size")
      : t("Перетащите влево или вправо · Ctrl+колесо — размер", "Drag left or right · Ctrl+wheel — size");
  }

  /* ---- drag: inside the window → left/centre/right; past the edge → the desktop window */
  function onStageMessage(event) {
    const inst = P.inst;
    if (!inst || event.source !== inst.frame.contentWindow || event.origin !== location.origin) return;
    const msg = event.data || {};
    if (msg.source !== "memu-stage" || msg.type !== "drag") return;
    const wrap = inst.el;
    if (msg.phase === "start") {
      P.drag = { rect: wrap.getBoundingClientRect(), out: false, dx: 0 };
      wrap.classList.add("qac-dragging");
      return;
    }
    if (!P.drag) return;
    if (msg.phase === "move") {
      if (P.drag.out) return;
      const dx = +msg.dx || 0, dy = +msg.dy || 0;
      P.drag.dx = dx;
      wrap.style.transform = `translate(${dx}px, ${dy}px)`;
      const r = P.drag.rect, cx = r.left + r.width / 2 + dx, cy = r.top + r.height / 2 + dy;
      const outside = cx < -8 || cx > innerWidth + 8 || cy < -8 || cy > innerHeight + 8;
      if (outside && P.supported !== false && Q.state().native) { P.drag.out = true; wrap.style.transform = ""; popOut(true); }
      return;
    }
    if (msg.phase === "end") {
      const drag = P.drag;
      P.drag = null;
      wrap.classList.remove("qac-dragging");
      wrap.style.transform = "";
      if (drag.out || !msg.moved) return;
      // dropped inside the window: settle left / centre / right of the strip
      const cx = drag.rect.left + drag.rect.width / 2 + drag.dx;
      const align = cx < innerWidth / 3 ? "left" : (cx > innerWidth * 2 / 3 ? "right" : "center");
      if (align !== P.align) { savePlace({ align }); if (P.panelEl) P.panelEl.dataset.align = align; }
    }
  }
  window.addEventListener("message", onStageMessage);

  async function popOut(dragging) {
    if (P.detaching) return;
    P.detaching = true;
    const wrap = P.inst && P.inst.el;
    const body = { action: "detach" };
    if (!dragging && wrap) {
      const r = wrap.getBoundingClientRect();
      body.screen = { x: window.screenX + r.left, y: window.screenY + r.top, w: r.width, h: r.height };
      body.dpr = window.devicePixelRatio || 1;
    } else body.gx = 0.5, body.gy = 0.4;
    P.place = "desktop";
    // Round 24: the inline strip is only dimmed while the native window takes over — hiding it mid-drag shrank
    // Quick Ask under the cursor and, together with the old hide-on-blur, made «everything disappear».
    if (P.panelEl) P.panelEl.classList.add("qac-leaving");
    let result = {};
    try {
      const response = await fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      result = await response.json();
    } catch (_) { result = {}; }
    P.detaching = false;
    if (P.panelEl) P.panelEl.classList.remove("qac-leaving");
    if (result.ok && !result.docked && result.visible !== false) { if (P.panel) P.panel.hide(); Q.resize(); }
    if (!result.ok || result.docked || result.visible === false) {
      // dropped back onto the window, or the desktop window is not available: stay inline
      if (result.supported === false) { P.supported = false; labelTools(); Q.toast(t("Отдельное окно компаньона есть только в приложении для Windows", "The desktop companion exists only in the Windows app"), "", 3200); }
      savePlace({ place: "inline" });
      P.desktopShown = false;
      ensureInline();
      return false;
    }
    P.desktopShown = true;
    savePlace({ place: "desktop" });
    desktop({ type: "prefs", prefs: P.prefs });
    if (P.speaking) desktop({ type: "speak", on: true, text: P.answer });
    if (P.answer) desktop({ type: "bubble", text: P.answer, final: !P.speaking });
    return true;
  }
  async function unpinFromDesktop() {
    try {
      await fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin", pinned: false }) });
    } catch (_) {}
    P.pinned = false;
    savePlace({ pinned: false });
    Q.toast(t("Компаньон откреплён: скроется вместе с окном", "The companion is unpinned: it hides with the window"));
  }
  async function pinToDesktop() {
    if (P.place !== "desktop") { const ok = await popOut(false); if (!ok) return; }
    try {
      const response = await fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin", pinned: true }) });
      const data = await response.json();
      P.pinned = data.pinned !== false;
    } catch (_) { P.pinned = true; }
    savePlace({ pinned: P.pinned });
    Q.toast(t("Компаньон закреплён на рабочем столе", "The companion is pinned to the desktop"));
  }

  function ensureInline() {
    if (!inlineOn()) {
      if (P.panel) { P.panel.remove(); P.panel = null; P.panelEl = null; }
      if (P.inst) { P.inst.destroy(); P.inst = null; }
      return null;
    }
    if (!P.panel) P.panel = Q.registerPanel("companion", mount, { slot: "top", order: -10 });
    if (!P.inst && P.panelEl) {
      P.inst = MemuCompanion.create({ container: P.panelEl, surface: "quick", lang: Q.lang(), bubble: false,
        prefs: () => P.prefs,
        onEvent: (type) => {
          if (type === "loaded") { P.lostAt = 0; Q.resize(); if (P.greet) { P.greet = false; P.inst.gesture("wave"); } }
          if (type === "contextlost") {
            // Round 24: the stage reloads itself on webglcontextrestored; if the GPU never gives the context
            // back, rebuild the stage so the model is never lost for good.
            P.lostAt = Date.now();
            setTimeout(() => {
              if (!P.lostAt || Date.now() - P.lostAt < 5500 || !P.inst) return;
              P.lostAt = 0;
              try { P.inst.destroy(); } catch (_) {}
              P.inst = null;
              if (P.panelEl) P.panelEl.innerHTML = "";
              ensureInline();
            }, 6000);
          }
          if (type === "error") { P.panelEl && P.panelEl.classList.add("failed"); Q.resize(); }
        },
        onResize: (size) => { savePref({ companion_size: size }); Q.resize(); },
      });
      P.inst.el.classList.add("in-quick");
      decorate(P.inst.el);
    }
    if (P.panelEl) P.panelEl.dataset.align = P.align;
    P.panel.show();
    return P.inst;
  }

  async function savePref(patch) {
    Object.assign(P.prefs, patch);
    try { await fetch("/api/pg/preferences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); } catch (_) {}
    if (P.inst) P.inst.setPrefs();
    desktop({ type: "prefs", prefs: P.prefs });
  }

  /* ----------------------------------------------------------- desktop */
  function channel() {
    if (!P.channel && typeof BroadcastChannel !== "undefined") {
      P.channel = new BroadcastChannel("memu-companion");
      // the desktop stage tells us when it was docked back into this window or pinned/unpinned
      P.channel.addEventListener("message", (event) => {
        const data = event.data || {};
        if (data.source !== "memu-companion" || data.target !== "quick") return;
        if (data.type === "docked") { P.place = "inline"; P.desktopShown = false; if (C().companion_quick_enabled) { ensureInline(); if (P.inst) P.inst.setVisible(true); } Q.resize(); }
        if (data.type === "pinned") P.pinned = !!data.on;
      });
    }
    return P.channel;
  }
  function desktop(msg, relay) {
    if (!desktopOn() || !P.desktopShown) return;
    const message = Object.assign({ source: "memu-companion", target: "desktop" }, msg);
    const ch = channel();
    if (ch) { try { ch.postMessage(message); } catch (_) {} }
    // Low-rate events also go through Python, in case the windows do not share
    // a browser profile; the stage ignores relayed copies while BroadcastChannel works.
    if (relay !== false && msg.type !== "mouth") {
      fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "event", event: msg }) }).catch(() => {});
    }
  }
  async function showDesktop() {
    if (!desktopOn()) { if (P.desktopShown) hideDesktop(); return; }
    try {
      const response = await fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "show" }) });
      const data = await response.json();
      P.desktopShown = !!(data.ok && data.supported);
    } catch (_) { P.desktopShown = false; }
  }
  function hideDesktop() {
    P.desktopShown = false;
    fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "hide" }) }).catch(() => {});
  }

  /* ------------------------------------------------------------ actions */
  function both(fn, msg) {
    if (P.inst) fn(P.inst);
    if (msg) desktop(msg);
  }
  function setState(name) { both((i) => i.state(name), { type: "state", name: name }); }
  function gesture(name) { both((i) => i.gesture(name), { type: "gesture", name: name }); }

  function stopLevel() { cancelAnimationFrame(P.levelRaf); P.levelRaf = 0; }
  // Desktop lip sync: the audio plays here, so the weights are computed here and broadcast.
  function startLevel(source) {
    stopLevel();
    const Core = window.MemuCompanionCore;
    let timeBuf = null, freqBuf = null, last = 0;
    const tick = (now) => {
      P.levelRaf = requestAnimationFrame(tick);
      if (now - last < 1000 / C().companion_fps - 2) return;
      last = now;
      let weights;
      if (source.analyser) {
        const a = source.analyser;
        if (!timeBuf || timeBuf.length !== a.fftSize) timeBuf = new Uint8Array(a.fftSize);
        if (!freqBuf || freqBuf.length !== a.frequencyBinCount) freqBuf = new Uint8Array(a.frequencyBinCount);
        a.getByteTimeDomainData(timeBuf); a.getByteFrequencyData(freqBuf);
        weights = Core.visemeWeights(Core.levelToOpenness(Core.rmsFromBytes(timeBuf)), Core.bandsFromSpectrum(freqBuf, a.context.sampleRate, a.fftSize));
      } else if (source.level) {
        let level = 0;
        try { level = +source.level() || 0; } catch (_) {}
        weights = Core.visemeWeights(Core.levelToOpenness(level), null);
        if (P.inst) P.inst.mouth(weights);
      }
      if (weights) desktop({ type: "mouth", w: weights }, false);
    };
    P.levelRaf = requestAnimationFrame(tick);
  }

  function onSpeakStart(data) {
    data = data || {};
    if (!C().companion_quick_enabled) return;
    P.speaking = true;
    const text = String(data.text || P.answer || "");
    const source = {};
    if (data.analyser && typeof data.analyser.getByteTimeDomainData === "function") source.analyser = data.analyser;
    else if (typeof data.level === "function") source.level = data.level;
    const audio = data.audio || data.element;
    if (P.inst) {
      if (source.analyser) P.inst.attachAnalyser(source.analyser);
      else if (audio && typeof audio.play === "function") P.inst.attachAudio(audio);
      P.inst.speak(true, source.level ? "" : text);
    }
    // The desktop window gets weights from here when we can measure, else the text.
    if (P.desktopShown) {
      if (!source.analyser && audio && P.inst && P.inst.audio === audio) {
        // attachAudio builds its analyser on "playing"; pick it up a moment later.
        setTimeout(() => { if (P.speaking && P.inst && P.inst.analyser) startLevel({ analyser: P.inst.analyser }); }, 400);
      }
      if (source.analyser || source.level) startLevel(source);
      desktop({ type: "speak", on: true, text: source.analyser || source.level ? "" : text });
    } else if (source.level && !P.inst?.analyser) startLevel(source);
    setState("speaking");
    gesture("nod");
  }
  function onSpeakEnd() {
    P.speaking = false;
    stopLevel();
    if (P.inst) { P.inst.speak(false); P.inst.detachAudio(); }
    desktop({ type: "speak", on: false });
    desktop({ type: "mouth", w: { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 } }, false);
    setState("idle");
  }

  async function onOpen() {
    await Promise.all([loadPrefs(), loadPlace()]);
    channel();
    if (P.supported === null) {
      try { const r = await fetch("/api/companion/window", { cache: "no-store" }); P.supported = !!(await r.json()).supported; } catch (_) { P.supported = false; }
      labelTools();
    }
    if (P.supported === false && P.place === "desktop") P.place = "inline";   // no desktop window here (browser/Linux)
    if (!(await libs())) return;
    const fresh = Date.now() - P.greetedAt > 10 * 60 * 1000;
    if (inlineOn()) {
      const inst = ensureInline();
      if (inst) {
        inst.setPrefs();
        inst.setVisible(true);
        if (fresh) { if (inst.loaded) inst.gesture("wave"); else P.greet = true; }
      }
    } else ensureInline();
    await showDesktop();
    if (fresh) { P.greetedAt = Date.now(); desktop({ type: "gesture", name: "wave" }); }
  }

  Q.on("open", () => { onOpen().catch((e) => console.warn("[companion] open failed", e)); });
  Q.on("close", () => {
    if (P.inst) P.inst.setVisible(false);
    if (P.speaking) onSpeakEnd();
    // never hide the desktop window in the middle of carrying it out
    if (P.desktopShown && !staysOnDesktop() && !P.detaching) hideDesktop();
  });
  // Round 24: «Закрепить на рабочем столе / Открепить» in the actions view (Ctrl+K) — no new footer buttons.
  if (typeof Q.addComposerButton === "function") {
    try {
      Q.addComposerButton({ id: "companion-pin", placement: "menu", order: 40, icon: ICON.pin,
        hidden: () => !window.MemuCompanionCore || P.supported === false || !C().companion_quick_enabled,
        label: () => (P.pinned ? t("Открепить компаньона от рабочего стола", "Unpin the companion from the desktop")
          : t("Закрепить компаньона на рабочем столе", "Pin the companion to the desktop")),
        pressed: () => !!P.pinned,
        onClick: () => (P.pinned ? unpinFromDesktop() : pinToDesktop()) });
    } catch (e) { console.warn("[companion] menu button", e); }
  }
  Q.on("submit", () => { P.answer = ""; setState("thinking"); });
  Q.on("answer-start", () => { P.answer = ""; gesture("nod"); setState("thinking"); });
  Q.on("answer-delta", (data) => {
    P.answer += (data && data.text) || "";
    if (P.inst) P.inst.text(P.answer, false);
    desktop({ type: "bubble", text: P.answer, final: false }, false);
  });
  Q.on("answer-done", (data) => {
    const text = (data && data.text) || P.answer;
    P.answer = text;
    if (P.inst) P.inst.text(text, true);
    if (data && data.error) gesture("shake");
    if (P.desktopShown) {
      const mood = window.MemuCompanionCore ? MemuCompanionCore.emotionForAnswer(text) : null;
      if (mood && mood.source !== "none" && C().companion_emotions) desktop({ type: "emotion", name: mood.emotion, intensity: mood.intensity, hold: 8000 });
      desktop({ type: "bubble", text: text, final: true });
    }
    if (!P.speaking) setState("idle");
  });
  Q.on("speak-start", onSpeakStart);
  Q.on("speak-end", onSpeakEnd);

  // Settings live in companion-settings.js (loaded by the main window's Quick Ask pane).

  window.MemuQuickCompanion = {
    get instance() { return P.inst; },
    place: () => ({ place: P.place, pinned: P.pinned, align: P.align, desktop: P.desktopShown }),
    popOut: () => popOut(false), pin: () => pinToDesktop(), unpin: () => unpinFromDesktop(),
    // For screenshots/tests: show the companion with a sample answer.
    async demo() {
      await libs();
      await loadPrefs();
      Object.assign(P.prefs, { companion_quick_enabled: true, companion_quick_mode: "inline" });
      ensureInline();
      Q.setInput(t("Как дела?", "How are you?"), { focus: false });
      const text = t("[emotion:happy] Отлично! Я рядом и готова помочь. Спроси что угодно — отвечу и покажу.", "[emotion:happy] Great! I'm right here and ready to help. Ask me anything.");
      P.inst.text(text, true);
      P.inst.speak(true, text);
      setTimeout(() => P.inst && P.inst.speak(false), 6000);
    },
  };
  // The window may already be open when the plugin loads.
  if (document.visibilityState === "visible") onOpen().catch((e) => console.warn("[companion] open failed", e));
})();
