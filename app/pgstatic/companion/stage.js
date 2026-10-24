/* MemuAI companion stage — the renderer page (/companion/stage).

   Embedded in an <iframe> by companion.js (MemuAI Live, Quick Ask) or opened
   directly as the transparent desktop window (?surface=desktop). It only
   draws; the host owns audio, text and settings and drives it with messages:

   host → stage  {source:"memu-companion", type, ...}
     init     {prefs, model:{id,kind,url,file}, personality, lang, reducedMotion}
     prefs    {prefs}                 model   {model}
     mouth    {w:{aa,ih,ou,ee,oh}}    speak   {on, text}   (text-driven lip sync when no audio)
     emotion  {name, intensity, hold} state   {name: idle|listening|thinking|speaking}
     gesture  {name: wave|nod|think|shake|bounce|surprise}
     look     {x, y, active}  (-1…1, y down; a direction, legacy)   visible {on}
     cursor   {x, y, ox?, oy?}  the host's pointer in THIS stage's CSS px (may be outside it); ox/oy = the stage
              viewport's offset from window.screenX/Y (calibrated from the host's pointer events)
     bubble   {text, final}   (desktop window only; embedded hosts draw their own)
   stage → host  {source:"memu-stage", type, ...}
     ready · loaded {meta} · error {code, message} · bbox {x,y,w,h} · click
     drag {phase:start|move|end, dx, dy} · wheel {delta} · contextlost
   In the desktop window the same messages arrive over BroadcastChannel
   "memu-companion" from the Quick Ask window, and the stage reports its hit
   box to Python (/api/companion/window {action:"hit"}) for click-through.
   Round 25 — gaze: she looks at the cursor measured from her FACE (Core.lookVector), always, also when the
   cursor is over other programs: while visible the stage reads /api/companion/cursor/stream (Windows, physical
   px of GetPhysicalCursorPos) and maps it into its own CSS px; elsewhere the pointer events of the pages.
   The host measures the point from the client area of the window that shows the stage (?host=, see cursorHost),
   so mixed-DPI monitors need no window.screenX math. */
(function () {
  "use strict";
  const Core = window.MemuCompanionCore;
  const params = new URLSearchParams(location.search);
  const SURFACE = params.get("surface") || "embed";
  const DESKTOP = SURFACE === "desktop";
  // The desktop window draws its own speech bubble at the top: keep that strip free
  // so the bubble (and a raised waving hand) never cover the face.
  const HEADROOM = DESKTOP ? 0.2 : 0;
  // Compatibility mode: skin the model on the CPU. Some software/broken GL
  // drivers (SwiftShader among them) crash the GPU process on GPU skinning;
  // the stage switches to it after repeated context losses, or on ?compat=cpu-skin.
  let CPU_SKIN = params.get("compat") === "cpu-skin";
  try { if (sessionStorage.getItem("memu-stage-cpu-skin") === "1") CPU_SKIN = true; } catch (_) {}
  const canvas = document.getElementById("stage-canvas");
  const statusNode = document.getElementById("stage-status");
  const reduceQuery = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : null;

  const S = {
    prefs: Core.normalizePrefs({}), model: null, lang: "ru", personality: "warm",
    reduced: !!(reduceQuery && reduceQuery.matches), driver: null, loadToken: 0,
    visible: true, raf: 0, lastFrame: 0, time: 0,
    mouth: { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 }, mouthTarget: { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 }, mouthAt: 0,
    speakText: null, speakStart: 0, speaking: false,
    emotion: { name: "neutral", intensity: 0, until: 0 }, state: "idle",
    gesture: null, look: { x: 0, y: 0, active: false, at: 0 }, bbox: null, bboxAt: 0,
    // Round 25: the cursor in stage CSS px, when the page (host or stage) last saw it, the stage viewport's
    // offset from window.screenX/Y, and the global cursor feed (EventSource) while the stage is shown.
    cursor: null, domAt: 0, offset: null, feed: null, feedRetry: 0, feedDown: false,
  };
  document.body.dataset.surface = SURFACE;
  document.body.classList.add("loading");
  if (DESKTOP) document.documentElement.style.background = "transparent";

  /* ------------------------------------------------------------ messaging */
  function post(type, data) {
    const message = Object.assign({ source: "memu-stage", type: type, surface: SURFACE }, data || {});
    if (window.parent && window.parent !== window) {
      try { window.parent.postMessage(message, location.origin); } catch (_) {}
    }
    if (DESKTOP && channel) { try { channel.postMessage(message); } catch (_) {} }
  }
  function status(text) {
    statusNode.hidden = !text;
    statusNode.textContent = text || "";
  }
  const t = (ru, en) => (S.lang === "en" ? en : ru);

  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || !event.data || event.data.source !== "memu-companion") return;
    receive(event.data);
  });
  let channel = null;
  if (DESKTOP && typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("memu-companion");
    channel.onmessage = (event) => {
      const data = event.data;
      S.channelAt = Date.now();
      if (data && data.source === "memu-companion" && (data.target === "desktop" || data.target === "all")) receive(data);
    };
  }
  // Python can also push messages into the desktop window (evaluate_js).
  window.MemuStage = {
    receive: (msg) => receive(Object.assign({ source: "memu-companion" }, msg || {})),
    // The server relay (companion_window.py): a copy of what BroadcastChannel
    // already delivered while it works, so it is used only when the channel is silent.
    relay: (msg) => { if (!S.channelAt || Date.now() - S.channelAt > 10000) receive(Object.assign({ source: "memu-companion" }, msg || {})); },
    // Diagnostics for tests and support: what is loaded and how it is framed.
    info: () => ({ surface: SURFACE, cpuSkin: CPU_SKIN, kind: S.driver && S.driver.meta && S.driver.meta.kind,
      loaded: !!S.driver, bbox: S.bbox, state: S.state, emotion: S.emotion.name, gesture: S.gesture && S.gesture.name,
      frame: S.driver && S.driver.debug ? S.driver.debug() : null }),
  };

  function receive(msg) {
    switch (msg.type) {
      case "init":
        S.lang = msg.lang === "en" ? "en" : "ru";
        S.personality = msg.personality || S.personality;
        if (typeof msg.reducedMotion === "boolean") S.reduced = msg.reducedMotion || !!(reduceQuery && reduceQuery.matches);
        S.prefs = Core.normalizePrefs(msg.prefs || {});
        if (window.MemuStage.labelTools) window.MemuStage.labelTools();
        loadModel(msg.model);
        break;
      case "pinned":
        if (window.MemuStage.setPinned) window.MemuStage.setPinned(!!msg.on);
        break;
      case "prefs":
        S.prefs = Core.normalizePrefs(msg.prefs || {});
        if (typeof msg.reducedMotion === "boolean") S.reduced = msg.reducedMotion || !!(reduceQuery && reduceQuery.matches);
        if (msg.personality) S.personality = msg.personality;
        if (S.driver) S.driver.applyPrefs();
        restartLoop();
        break;
      case "model":
        // A fresh page gives the new model a clean WebGL context (VRM ↔ Live2D); the host re-sends "init".
        if (S.driver) location.reload(); else loadModel(msg.model);
        break;
      case "mouth":
        if (S.prefs.companion_lipsync) { S.mouthTarget = msg.w || S.mouthTarget; S.mouthAt = performance.now(); }
        break;
      case "speak":
        S.speaking = !!msg.on;
        S.speakText = msg.on && msg.text ? Core.textVisemeTrack(msg.text) : null;
        S.speakStart = performance.now();
        if (!msg.on) S.mouthTarget = { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
        break;
      case "emotion":
        if (S.prefs.companion_emotions) {
          const name = Core.normalizeEmotion(msg.name) || "neutral";
          S.emotion = { name: name, intensity: Math.max(0, Math.min(1, msg.intensity == null ? 1 : +msg.intensity)),
            until: performance.now() + (msg.hold || 7000) };
        }
        break;
      case "state":
        S.state = msg.name || "idle";
        if (S.state === "thinking") gesture("think");
        else if (S.gesture && S.gesture.name === "think") S.gesture.end = true;
        break;
      case "gesture": gesture(msg.name); break;
      case "look":
        S.cursor = null;
        S.look = { x: Math.max(-1, Math.min(1, +msg.x || 0)), y: Math.max(-1, Math.min(1, +msg.y || 0)), active: msg.active !== false, at: performance.now() };
        break;
      case "cursor":
        if (isFinite(msg.x) && isFinite(msg.y)) { S.cursor = { x: +msg.x, y: +msg.y }; S.domAt = performance.now(); }
        if (isFinite(msg.ox) && isFinite(msg.oy)) calibrate(+msg.ox, +msg.oy);
        break;
      case "visible":
        S.visible = !!msg.on;
        restartLoop();
        break;
      case "bubble": if (DESKTOP) bubble(msg.text, msg.final); break;
    }
  }

  function gesture(name) {
    if (!name || !S.prefs.companion_gestures) return;
    if (S.reduced && name !== "nod" && name !== "think") return;
    const durations = { wave: 2.4, nod: 1.1, think: 600, shake: 1.2, bounce: 0.9, surprise: 0.9 };
    if (!durations[name]) return;
    S.gesture = { name: name, start: S.time, dur: durations[name], end: false, endAt: 0 };
    // Round 26: the hit box (= the desktop window's region) makes room for the gesture on the next frame,
    // before the hand gets there — not up to 300 ms later with the hand already cut off.
    S.bboxAt = 0;
  }

  let bubbleTimer = 0;
  function bubble(text, final) {
    const node = document.getElementById("stage-bubble");
    if (!S.prefs.companion_bubble || !text) { node.hidden = true; return; }
    const clean = Core.stripTags(text, !final).replace(/\s+/g, " ").trim();
    node.textContent = clean.length > 220 ? "…" + clean.slice(-220) : clean;
    node.hidden = !clean;
    // Round 34: a long answer is trimmed at the top — the height is whole lines, so the window edge never shows
    // half a line (19 % of the window, as before, rounded down to lines; padding 8 + 8)
    const lh = parseFloat(getComputedStyle(node).lineHeight) || 18;
    node.style.maxHeight = (Math.max(2, Math.floor((innerHeight * 0.19 - 16) / lh)) * lh + 16) + "px";
    clearTimeout(bubbleTimer);
    if (final) bubbleTimer = setTimeout(() => { node.hidden = true; reportHit(); }, 6000);
    reportHit();
  }
  // Round 24: the click-through region of the desktop window = model ∪ tools ∪ BUBBLE. Without the bubble's box
  // the region started at the model's left edge and cut the bubble off (R24 screenshot №2).
  function bubbleBox() {
    const node = document.getElementById("stage-bubble");
    if (!node || node.hidden) return null;
    const r = node.getBoundingClientRect();
    return r.width > 2 && r.height > 2 ? { x: r.left, y: r.top, w: r.width, h: r.height } : null;
  }
  let hitTimer = 0;
  function reportHit() {
    if (!DESKTOP || !S.bbox) return;
    clearTimeout(hitTimer);
    hitTimer = setTimeout(() => {
      fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "hit", rect: S.bbox, bubble: bubbleBox(), dpr: devicePixelRatio || 1,
          viewport: { w: innerWidth, h: innerHeight } }) }).catch(() => {});
    }, 30);
  }

  /* ---------------------------------------------------------------- loop */
  function restartLoop() {
    cancelAnimationFrame(S.raf); S.raf = 0;
    const shown = S.visible && S.driver && document.visibilityState !== "hidden";
    if (shown) {
      S.lastFrame = 0;
      S.raf = requestAnimationFrame(frame);
    }
    syncCursorFeed(!!shown && S.prefs.companion_look_at);
  }

  /* --------------------------------------------------- gaze: global cursor */
  // The stage viewport's top-left corner in CSS screen px = window.screenX/Y + the top window's border/title
  // (S.offset, calibrated from pointer events: screenX - clientX) + where the host page shows our <iframe> NOW
  // (read live, so dragging the companion or scrolling the host never leaves a stale origin). Before the first
  // pointer event: a frameless window's estimate (pywebview windows have no borders).
  function frameOffset() {
    try {
      const el = window.frameElement;
      if (el) { const r = el.getBoundingClientRect(); return { x: r.left, y: r.top }; }
    } catch (_) {}
    return { x: 0, y: 0 };
  }
  function calibrate(ox, oy) {
    const f = frameOffset();
    S.offset = { x: ox - f.x, y: oy - f.y };
  }
  function viewportOrigin() {
    let o = S.offset;
    if (!o) {
      let top = window;
      try { if (window.top && window.top.innerWidth) top = window.top; } catch (_) {}
      const side = (top.outerWidth - top.innerWidth) / 2;
      o = { x: side, y: Math.max(0, top.outerHeight - top.innerHeight - side) };
    }
    const f = frameOffset();
    return { x: window.screenX + o.x + f.x, y: window.screenY + o.y + f.y };
  }
  // The window that shows this stage, for the host to measure the cursor from its client area (physical px):
  // "desktop" (own window), "quick" (Quick Access), "main" (MemuAI: Live, settings); "" outside pywebview
  // (a browser tab) — the host then sends screen coordinates and viewportOrigin() places them.
  function cursorHost() {
    if (SURFACE === "desktop") return "desktop";
    try {
      const top = window.top;
      if (top.document.documentElement.dataset.native === "1") return "quick";
      if (!top.pywebview) return "";
      return /^\/quick(\/|$)/.test(top.location.pathname) ? "quick" : "main";
    } catch (_) { return ""; }
  }
  function syncCursorFeed(on) {
    if (!on || S.feedDown || typeof EventSource === "undefined") {
      if (S.feed) { S.feed.close(); S.feed = null; }
      clearTimeout(S.feedRetry); S.feedRetry = 0;
      return;
    }
    if (S.feed || S.feedRetry) return;
    let feed;
    const query = "?hz=" + (S.prefs.companion_low_power ? 15 : 30) + "&host=" + cursorHost();
    try { feed = new EventSource("/api/companion/cursor/stream" + query); } catch (_) { S.feedDown = true; return; }
    S.feed = feed;
    let opened = false;
    feed.onopen = () => { opened = true; };
    feed.onmessage = (event) => {
      // The page's own pointer events are exact; the feed only fills in when the cursor is elsewhere.
      if (performance.now() - S.domAt < 250) return;
      const parts = String(event.data || "").split(",");
      const point = { x: +parts[0], y: +parts[1] };
      if (!isFinite(point.x) || !isFinite(point.y)) return;
      const dpr = window.devicePixelRatio || 1;
      if (parts[2] === "c") {
        // Relative to the hosting window's client area (= the top page's viewport): only the dpr and where
        // the host page shows our <iframe> remain. Exact on monitors with different scales.
        const f = frameOffset();
        S.cursor = Core.screenToClient(point, { x: f.x, y: f.y }, dpr);
      } else {
        S.cursor = Core.screenToClient(point, viewportOrigin(), dpr);
      }
    };
    feed.onerror = () => {
      feed.close();
      if (S.feed === feed) S.feed = null;
      // 404 (not Windows / no user32): never ask again on this page. A dropped stream: retry in a while.
      if (!opened) { S.feedDown = true; return; }
      S.feedRetry = setTimeout(() => { S.feedRetry = 0; restartLoop(); }, 5000);
    };
  }
  // Where she looks: from her face to the cursor (Core.lookVector), refreshed every frame because the face moves.
  function updateGaze() {
    if (!S.prefs.companion_look_at || !S.cursor || !S.driver || !S.driver.face) return;
    const face = S.driver.face();
    if (!face) return;
    const v = Core.lookVector(S.cursor, face, Math.max(220, innerHeight * 0.8));
    S.look = { x: v.x, y: v.y, active: true, at: performance.now() };
  }
  document.addEventListener("visibilitychange", restartLoop);
  if (reduceQuery && reduceQuery.addEventListener) reduceQuery.addEventListener("change", (e) => { S.reduced = e.matches; });

  function frame(now) {
    S.raf = requestAnimationFrame(frame);
    let fps = S.prefs.companion_low_power ? Math.min(24, S.prefs.companion_fps) : S.prefs.companion_fps;
    if (CPU_SKIN) fps = Math.min(fps, 20);
    if (S.lastFrame && now - S.lastFrame < 1000 / fps - 2) return;
    const dt = S.lastFrame ? Math.min(0.1, (now - S.lastFrame) / 1000) : 1 / fps;
    S.lastFrame = now;
    S.time += dt;
    // Mouth: audio-driven weights go stale after 250 ms; text track when speaking without audio.
    let target = S.mouthTarget;
    if (now - S.mouthAt > 250) {
      target = S.speaking && S.speakText && S.prefs.companion_lipsync
        ? Core.sampleTrack(S.speakText, now - S.speakStart) : { aa: 0, ih: 0, ou: 0, ee: 0, oh: 0 };
    }
    S.mouth = Core.smoothVisemes(S.mouth, target, dt);
    if (S.emotion.name !== "neutral" && now > S.emotion.until) S.emotion = { name: "neutral", intensity: 0, until: 0 };
    updateGaze();
    try {
      S.driver.update(dt, S);
      S.driver.render();
    } catch (error) {
      console.error(error);
      fail("render", String(error && error.message || error));
      return;
    }
    if (now - S.bboxAt > 300) { S.bboxAt = now; reportBox(); }
  }

  function reportBox() {
    const rect = S.driver && S.driver.bbox();
    if (!rect) return;
    const old = S.bbox;
    if (old && Math.abs(old.x - rect.x) < 2 && Math.abs(old.y - rect.y) < 2 && Math.abs(old.w - rect.w) < 2 && Math.abs(old.h - rect.h) < 2) return;
    S.bbox = rect;
    post("bbox", rect);
    if (DESKTOP) {
      const drag = document.getElementById("stage-drag");
      Object.assign(drag.style, { left: rect.x + "px", top: rect.y + "px", width: rect.w + "px", height: rect.h + "px" });
      drag.hidden = false;
      reportHit();
    }
  }

  function fail(code, message) {
    cancelAnimationFrame(S.raf); S.raf = 0;
    status(code === "webgl" ? t("WebGL недоступен — компаньон выключен", "WebGL is unavailable — companion disabled") : message);
    post("error", { code: code, message: message });
  }

  /* ------------------------------------------------------------- loading */
  const scripts = {};
  function loadScript(src) {
    if (!scripts[src]) scripts[src] = new Promise((resolve, reject) => {
      const node = document.createElement("script");
      node.src = src; node.async = false;
      node.onload = resolve;
      node.onerror = () => reject(new Error("script " + src));
      document.head.appendChild(node);
    });
    return scripts[src];
  }

  // No probe context here: an extra context is a real cost (and Chromium
  // drops the oldest one when a page holds too many). The renderer itself
  // reports a missing WebGL by throwing, which fail("webgl") handles.
  function webglAvailable() {
    return typeof WebGLRenderingContext !== "undefined" || typeof WebGL2RenderingContext !== "undefined";
  }

  async function loadModel(model) {
    if (!model || !model.url) { fail("no_model", t("Модель не выбрана", "No model selected")); return; }
    const token = ++S.loadToken;
    S.model = model;
    if (!webglAvailable()) { fail("webgl", ""); return; }
    document.body.classList.add("loading");
    status(t("Загружаю модель…", "Loading the model…"));
    let driver = null;
    try {
      if (S.driver) { S.driver.dispose(); S.driver = null; restartLoop(); }
      driver = model.kind === "live2d" ? new Live2DDriver() : new VrmDriver();
      await driver.load(model);
      if (token !== S.loadToken) { driver.dispose(); return; }
      S.driver = driver;
      S.bbox = null;
      document.body.classList.remove("loading");
      status("");
      post("loaded", { meta: driver.meta || {} });
      gesture(DESKTOP || params.get("greet") === "1" ? "wave" : "");
      restartLoop();
    } catch (error) {
      // A half-built driver still holds the canvas' GL context (PIXI / three): free it, also for the next model.
      if (driver && driver !== S.driver) driver.dispose();
      if (token !== S.loadToken) return;
      console.error(error);
      const code = error && error.code || "load";
      fail(code, (error && error.userMessage) || t("Не удалось загрузить модель: ", "Could not load the model: ") + (error && error.message || error));
    }
  }

  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    cancelAnimationFrame(S.raf); S.raf = 0;
    syncCursorFeed(false);
    post("contextlost");
    status(t("Видеокарта сбросила контекст — восстанавливаю…", "The GPU reset the context — restoring…"));
  });
  // Rebuilding every GPU resource by hand is error-prone; a fresh page is
  // reliable and the host re-sends "init" on "ready".
  canvas.addEventListener("webglcontextrestored", () => {
    // Guard against a reload loop on a GPU that keeps resetting.
    let recent = [];
    try { recent = JSON.parse(sessionStorage.getItem("memu-stage-reloads") || "[]").filter((t) => Date.now() - t < 60000); } catch (_) {}
    if (recent.length >= 2) {
      // Third strike: last resort is CPU skinning; if that crashed too, give up.
      if (CPU_SKIN) { fail("webgl", ""); return; }
      try { sessionStorage.setItem("memu-stage-cpu-skin", "1"); sessionStorage.removeItem("memu-stage-reloads"); } catch (_) {}
      location.reload();
      return;
    }
    recent.push(Date.now());
    try { sessionStorage.setItem("memu-stage-reloads", JSON.stringify(recent)); } catch (_) {}
    location.reload();
  });

  /* -------------------------------------------------------- pointer input */
  let dragging = null;
  function insideBox(x, y) {
    const b = S.bbox;
    return !!b && x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h;
  }
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !insideBox(event.clientX, event.clientY)) return;
    canvas.setPointerCapture(event.pointerId);
    dragging = { id: event.pointerId, x: event.screenX, y: event.screenY, moved: false };
    post("drag", { phase: "start", dx: 0, dy: 0 });
  });
  // Inside its own frame the stage knows the cursor exactly; it also calibrates the viewport origin.
  window.addEventListener("pointermove", (event) => {
    S.cursor = { x: event.clientX, y: event.clientY };
    S.domAt = performance.now();
    calibrate(event.screenX - event.clientX - window.screenX, event.screenY - event.clientY - window.screenY);
  }, { passive: true });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging || dragging.id !== event.pointerId) {
      canvas.style.cursor = insideBox(event.clientX, event.clientY) ? "grab" : "";
      return;
    }
    const dx = event.screenX - dragging.x, dy = event.screenY - dragging.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) dragging.moved = true;
    if (dragging.moved) { canvas.style.cursor = "grabbing"; post("drag", { phase: "move", dx: dx, dy: dy }); }
  });
  const endDrag = (event) => {
    if (!dragging || dragging.id !== event.pointerId) return;
    const moved = dragging.moved;
    dragging = null;
    canvas.style.cursor = "";
    post("drag", { phase: "end", dx: 0, dy: 0, moved: moved });
    if (!moved && event.type === "pointerup") {
      post("click");
      gesture("bounce");
      S.emotion = { name: "happy", intensity: 0.8, until: performance.now() + 2500 };
    }
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !DESKTOP) return;
    event.preventDefault();
    post("wheel", { delta: Math.sign(event.deltaY) });
  }, { passive: false });
  window.addEventListener("resize", () => { if (S.driver) S.driver.resize(); S.bbox = null; });

  if (DESKTOP) {
    const tools = document.getElementById("stage-tools");
    tools.hidden = false;
    const win = (body) => fetch("/api/companion/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then((r) => r.json()).catch(() => ({}));
    const announceDock = () => { if (channel) { try { channel.postMessage({ source: "memu-companion", target: "quick", type: "docked" }); } catch (_) {} } };
    tools.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-tool]");
      const tool = button && button.dataset.tool;
      if (!tool) return;
      if (tool === "pin") {
        const on = button.getAttribute("aria-pressed") !== "true";
        const r = await win({ action: "pin", pinned: on });
        setPinned(typeof r.pinned === "boolean" ? r.pinned : on);
        if (channel) { try { channel.postMessage({ source: "memu-companion", target: "quick", type: "pinned", on: typeof r.pinned === "boolean" ? r.pinned : on }); } catch (_) {} }
        return;
      }
      if (tool === "dock") { const r = await win({ action: "dock" }); if (r.docked) announceDock(); return; }
      win({ action: tool === "close" ? "hide" : "scale", step: tool === "bigger" ? 1 : -1 });
    });
    function setPinned(on) {
      const pin = tools.querySelector('[data-tool="pin"]');
      pin.setAttribute("aria-pressed", String(!!on));
      pin.title = on ? t("Закреплён: остаётся, когда быстрый доступ скрыт", "Pinned: stays when Quick Ask hides") : t("Закрепить на рабочем столе", "Pin to the desktop");
      pin.setAttribute("aria-label", pin.title);
    }
    // Round 26: the labels follow the language of the settings, which arrive with "init" — before, the buttons
    // kept the Russian labels of stage.html (and the titles set before "init") in an English interface.
    function labelTools() {
      const labels = {
        dock: t("Вернуть в окно быстрого доступа", "Back into the Quick Ask window"),
        smaller: t("Меньше", "Smaller"), bigger: t("Больше", "Bigger"), close: t("Скрыть", "Hide"),
      };
      for (const [tool, label] of Object.entries(labels)) {
        const button = tools.querySelector(`[data-tool="${tool}"]`);
        button.title = label;
        button.setAttribute("aria-label", label);
      }
      setPinned(tools.querySelector('[data-tool="pin"]').getAttribute("aria-pressed") === "true");
    }
    window.MemuStage.setPinned = setPinned;
    window.MemuStage.labelTools = labelTools;
    labelTools();
    win({ action: "status" }).then((r) => setPinned(!!r.pinned));
    // Round 23: native drag (the window follows the mouse until release); dropped onto Quick Ask → docks back.
    const dragHandle = document.getElementById("stage-drag");
    dragHandle.addEventListener("pointerdown", async (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      dragHandle.classList.add("dragging");
      const r = await win({ action: "drag" });
      dragHandle.classList.remove("dragging");
      if (r.docked) announceDock();
      else if (r.moved === false) {
        // Round 24: a click (no movement) on the desktop companion opens Quick Ask.
        gesture("bounce");
        fetch("/api/quick/window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "show" }) }).catch(() => {});
      }
    });
    dragHandle.addEventListener("wheel", (event) => {
      event.preventDefault();
      win({ action: "scale", step: event.deltaY < 0 ? 1 : -1 });
    }, { passive: false });
    // The desktop window has no host page: fetch the settings and the model itself.
    (async () => {
      try {
        const [prefs, library] = await Promise.all([
          fetch("/api/pg/preferences").then((r) => r.json()),
          fetch("/api/companion/models").then((r) => r.json()),
        ]);
        const p = prefs.preferences || prefs;
        const models = library.models || [];
        const model = models.find((m) => m.id === p.companion_model_id) || models[0];
        receive({ source: "memu-companion", type: "init", prefs: p, model: model, lang: p.language,
          personality: p.voice_live_primary_personality, reducedMotion: p.ui_motion === "off" || p.ui_motion === "reduced" });
      } catch (error) { fail("load", String(error)); }
    })();
  }

  /* ================================================================= VRM */
  // The last bone of the fingers (optional in VRM): with them the frame knows where a waving hand really ends.
  const FINGER_TIPS = ["leftMiddleDistal", "rightMiddleDistal", "leftIndexDistal", "rightIndexDistal", "leftLittleDistal", "rightLittleDistal"];
  class VrmDriver {
    async load(model) {
      await loadScript("/companion/vendor/memu-three-vrm.min.js?v=1");
      const V = window.MemuThreeVRM;
      if (!V) throw new Error("three-vrm bundle missing");
      this.V = V;
      const T = V.THREE;
      const lowPower = S.prefs.companion_low_power;
      let renderer;
      try {
        renderer = new T.WebGLRenderer({ canvas: canvas, alpha: true, antialias: !lowPower,
        premultipliedAlpha: true, powerPreference: lowPower ? "low-power" : "default", preserveDrawingBuffer: false });
      } catch (error) {
        throw Object.assign(new Error("WebGL"), { code: "webgl" });
      }
      renderer.setClearColor(0x000000, 0);
      renderer.outputColorSpace = T.SRGBColorSpace;
      this.renderer = renderer;
      this.scene = new T.Scene();
      this.camera = new T.PerspectiveCamera(24, 1, 0.05, 30);
      const key = new T.DirectionalLight(0xffffff, Math.PI * 0.95);
      key.position.set(0.6, 1.4, 1.6);
      const fill = new T.AmbientLight(0xffffff, 0.55);
      this.scene.add(key, fill);
      this.lookTarget = new T.Object3D();
      // The bundle exports no Quaternion (third_party/NOTICE.md); every Object3D carries one.
      this.Quat = this.lookTarget.quaternion.constructor;
      this.scene.add(this.lookTarget);
      this.applyPrefs();

      const response = await fetch(model.url, { credentials: "same-origin" });
      if (!response.ok) throw Object.assign(new Error("HTTP " + response.status), { code: "missing" });
      const buffer = await response.arrayBuffer();
      const loader = new V.GLTFLoader();
      loader.register((parser) => new V.VRMLoaderPlugin(parser, { autoUpdateHumanBones: true }));
      const gltf = await loader.parseAsync(buffer, "");
      const vrm = gltf.userData.vrm;
      if (!vrm) throw Object.assign(new Error("not a VRM"), { code: "not_vrm" });
      V.VRMUtils.removeUnnecessaryVertices(gltf.scene);
      if (V.VRMUtils.combineSkeletons) V.VRMUtils.combineSkeletons(gltf.scene);
      V.VRMUtils.rotateVRM0(vrm);
      vrm.scene.traverse((node) => { node.frustumCulled = false; });
      this.scene.add(vrm.scene);
      this.vrm = vrm;
      if (vrm.lookAt) vrm.lookAt.target = this.lookTarget;
      // Round 26: every local bone angle below is written in VRM 0.x axes; rot() converts it for 1.0 models.
      this.version = vrm.meta && vrm.meta.metaVersion;
      const em = vrm.expressionManager;
      this.has = (name) => !!(em && em.getExpression(name));
      this.meta = { kind: "vrm", version: vrm.meta && vrm.meta.metaVersion, expressions: em ? em.expressions.map((e) => e.expressionName) : [] };
      this.bones = {};
      for (const name of ["hips", "spine", "chest", "upperChest", "neck", "head", "leftShoulder", "rightShoulder",
        "leftUpperArm", "rightUpperArm", "leftLowerArm", "rightLowerArm", "leftHand", "rightHand", "leftFoot", "rightFoot"]) {
        this.bones[name] = vrm.humanoid.getNormalizedBoneNode(name);
      }
      this.raw = {};
      for (const name of ["head", "hips", "leftHand", "rightHand", "leftLowerArm", "rightLowerArm", "leftFoot", "rightFoot"].concat(FINGER_TIPS)) {
        this.raw[name] = vrm.humanoid.getRawBoneNode(name);
      }
      this.hipsRest = this.bones.hips ? this.bones.hips.position.y : 0;
      vrm.scene.updateMatrixWorld(true);
      this.rest = this.captureRest();
      this.cpuSkinned = CPU_SKIN ? this.prepareCpuSkin(vrm.scene) : null;
      this.weights = {};
      this.blink = { next: 1.5 + Math.random() * 2, t: -1, double: false };
      this.saccade = { x: 0, y: 0, next: 0.8 };
      this.headLook = { x: 0, y: 0 };
      vrm.update(0);
      this.reach = this.measureGestures();
      this.measure();
      this.resize();
    }
    // Round 25: the T-pose rest data the "think" pose aims from (world directions work for VRM 0.x and 1.0 alike).
    captureRest() {
      const T = this.V.THREE, b = this.bones, rest = {};
      for (const [name, child] of [["rightUpperArm", "rightLowerArm"], ["rightLowerArm", "rightHand"]]) {
        if (!b[name] || !b[child]) return null;
        const from = b[name].getWorldPosition(new T.Vector3()), to = b[child].getWorldPosition(new T.Vector3());
        rest[name] = { dir: to.clone().sub(from).normalize(), len: to.distanceTo(from), quat: b[name].getWorldQuaternion(new this.Quat()) };
      }
      return rest;
    }
    // Point `bone` along a WORLD direction, blended over the pose already set this frame by `weight`.
    aim(name, dir, weight) {
      const T = this.V.THREE, bone = this.bones[name], rest = this.rest && this.rest[name];
      if (!bone || !rest || weight <= 0) return;
      bone.parent.updateWorldMatrix(true, false);
      const parent = bone.parent.getWorldQuaternion(new this.Quat());
      const world = new this.Quat().setFromUnitVectors(rest.dir, dir.clone().normalize()).multiply(rest.quat);
      const local = parent.invert().multiply(world);
      bone.quaternion.slerp(local, Math.max(0, Math.min(1, weight)));
    }
    // "think": the right hand comes up to the chin. Before Round 25 the forearm was twisted around its own axis
    // (rotation.y −1.6) and the hand ended up in the twin-tail behind the head — the "what is wrong with her arm" report.
    thinkPose(w) {
      if (!this.rest || !this.raw.head || w <= 0) return;
      const T = this.V.THREE, b = this.bones;
      const side = Math.sign(this.rest.rightUpperArm.dir.x) || -1;
      this.aim("rightUpperArm", new T.Vector3(side * 0.18, -0.82, 0.55), w);
      b.rightUpperArm.updateWorldMatrix(true, false);
      const elbow = b.rightUpperArm.getWorldPosition(new T.Vector3())
        .add(new T.Vector3(side * 0.18, -0.82, 0.55).normalize().multiplyScalar(this.rest.rightUpperArm.len));
      const chin = this.raw.head.getWorldPosition(new T.Vector3()).add(new T.Vector3(side * 0.02, -0.075, 0.13));
      // The forearm points at the chin but stops a hand's length short of it.
      this.aim("rightLowerArm", chin.sub(elbow), w);
      if (b.rightHand) b.rightHand.rotation.z += Core.vrmEuler(this.version, 0, 0, -0.35 * side * w)[2];
    }
    wavePose(up, time) {
      const b = this.bones;
      // Round 25: the elbow stays low and the forearm comes up beside the head — a compact "hi" that needs far
      // less room at the side than the old arm-straight-out wave (which the frame cut off).
      const keep = (bone) => bone.rotation.x * Core.vrmAxisSign(this.version);
      if (b.rightUpperArm) this.rot(b.rightUpperArm, keep(b.rightUpperArm), 0.1 + 0.25 * up, -1.18 + 0.42 * up);
      if (b.rightLowerArm) this.rot(b.rightLowerArm, keep(b.rightLowerArm), -0.35 * (1 - up), 2.05 * up + Math.sin(time * 10) * 0.28 * up);
      if (b.rightHand) this.rot(b.rightHand, keep(b.rightHand), b.rightHand.rotation.y, -Math.sin(time * 11 + 0.6) * 0.15 * up);
    }
    // Round 25: how far our own gestures move the hands (world units, from the body's centre line): the frame
    // makes room for them up front, so a waving hand is never cut by the edge (R25 screenshot).
    // Round 26: also the finger tips (a hand bone is the wrist: the fingers reach ~10 cm past it) and every point's
    // depth — a waving hand is closer to the camera and looks wider in perspective. `points` (world, with the
    // margin for sleeves/fingers in `m`) frame the stage in resize() and widen the hit box while a gesture plays.
    measureGestures() {
      const T = this.V.THREE, v = new T.Vector3();
      const reach = { top: -Infinity, points: [] };
      const tips = FINGER_TIPS.some((name) => this.raw[name]);
      const sample = () => {
        this.vrm.humanoid.update();
        this.vrm.scene.updateMatrixWorld(true);
        for (const name of ["leftHand", "rightHand", "leftLowerArm", "rightLowerArm"].concat(FINGER_TIPS)) {
          if (!this.raw[name]) continue;
          this.raw[name].getWorldPosition(v);
          const m = /Hand$/.test(name) && !tips ? 0.13 : 0.07;
          reach.top = Math.max(reach.top, v.y + m);
          reach.points.push({ x: v.x, y: v.y, z: v.z, m: m });
        }
      };
      this.basePose(0, 1);
      sample();
      // The arm on its way up and down too (Round 26): half-raised, the forearm points out sideways — farther than
      // at the top, where alone it was measured before (the hand went past the edge for a moment).
      for (const up of [0.2, 0.4, 0.6, 0.8, 1]) {
        for (const time of [0, 0.157, 0.314, 0.471]) { this.basePose(time, 1); this.wavePose(up, time); sample(); }
      }
      this.basePose(0, 1); this.thinkPose(1); sample();
      this.basePose(0, 1);
      this.vrm.humanoid.update();
      return reach;
    }
    applyPrefs() {
      if (!this.renderer) return;
      const ratio = Math.min(window.devicePixelRatio || 1, S.prefs.companion_low_power ? 1 : 2);
      this.renderer.setPixelRatio(ratio);
      if (this.vrm) { this.measure(); this.resize(); }
    }
    measure() {
      const T = this.V.THREE;
      const head = new T.Vector3();
      this.raw.head.getWorldPosition(head);
      const box = new T.Box3().setFromObject(this.vrm.scene);
      this.headY = head.y;
      // Hair, ears and hats rise above the head bone; never cut them off (nor a raised hand).
      this.topY = Math.max(head.y + 0.2, Math.min(head.y + 0.6, box.max.y + 0.04));
      if (this.reach && S.prefs.companion_gestures && !S.reduced) this.topY = Math.max(this.topY, Math.min(head.y + 0.6, this.reach.top));
      this.height = Math.max(0.5, box.max.y - Math.min(0, box.min.y));
    }
    resize() {
      const w = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight);
      this.renderer.setSize(w, h, false);
      const cam = this.camera;
      cam.aspect = w / h;
      if (!this.vrm) { cam.updateProjectionMatrix(); return; }
      const full = S.prefs.companion_framing === "full";
      // Bust: from a little above the head to the waist; full: the whole body.
      const top = this.topY;
      const bottom = full ? 0 : this.headY - 0.56;
      const inner = (top - bottom) * 1.04;
      const span = inner / (1 - HEADROOM);
      const fov = cam.fov * Math.PI / 180;
      const tanV = Math.tan(fov / 2), tanH = tanV * cam.aspect;
      // Where the camera aims at distance d: the inner frame's top stays just under the headroom strip, so a
      // frame widened for the hands (Round 26) shows more of her body below instead of empty space above her head.
      // Full body: centred, as before.
      const innerTop = bottom + inner;
      const aim = (d) => (full ? (top + bottom) / 2 - inner / 2 + span / 2 : innerTop - d * tanV * (1 - 2 * HEADROOM));
      // Narrow frames must still fit the shoulders (~0.55 m wide with arms down) and — Round 25 — the hands of
      // our own gestures (a wave, the hand to the chin), measured on this very model in measureGestures();
      // Round 26: in perspective (Core.reachDistance), re-checked after the aim moves.
      const gestures = S.prefs.companion_gestures && !S.reduced && this.reach;
      const distH = (span / 2) / tanV, distW = (full ? 0.75 : 0.62) / 2 / tanH;
      let dist = Math.max(distH, distW);
      if (gestures) for (let i = 0; i < 4; i++) dist = Math.max(dist, Core.reachDistance(this.reach.points, tanH, tanV, aim(dist)));
      this.fitBy = dist > Math.max(distH, distW) + 1e-6 ? "gestures" : distW > distH ? "width" : "height";
      const centerY = aim(dist);
      cam.position.set(0, centerY, dist);
      cam.lookAt(0, centerY, 0);
      cam.updateProjectionMatrix();
    }
    // The point between the eyes in stage CSS px (gaze origin).
    face() {
      if (!this.raw.head) return null;
      const T = this.V.THREE, p = this.raw.head.getWorldPosition(new T.Vector3());
      p.y += 0.07; p.z += 0.08;
      p.project(this.camera);
      return { x: (p.x + 1) / 2 * canvas.clientWidth, y: (1 - p.y) / 2 * canvas.clientHeight };
    }
    debug() {
      const cam = this.camera.position;
      return { headY: this.headY, height: this.height, camera: [cam.x, cam.y, cam.z].map((n) => +n.toFixed(3)), fitBy: this.fitBy };
    }
    // Base pose: arms relaxed down, breathing, sway (absolute every frame: no drift).
    basePose(time, energy) {
      const b = this.bones;
      const breathe = Math.sin(time * 1.55);
      const sway = Math.sin(time * 0.62) * energy;
      const set = (bone, x, y, z) => { if (bone) this.rot(bone, x, y, z); };
      set(b.hips, 0, Math.sin(time * 0.37) * 0.03 * energy, sway * 0.022);
      set(b.spine, breathe * 0.012 + 0.02, Math.sin(time * 0.5 + 1) * 0.025 * energy, -sway * 0.012);
      set(b.chest, breathe * 0.018, 0, 0);
      set(b.upperChest, breathe * 0.01, 0, 0);
      const armSwing = Math.sin(time * 0.8) * 0.025 * energy;
      set(b.leftShoulder, 0, 0, breathe * 0.012);
      set(b.rightShoulder, 0, 0, -breathe * 0.012);
      set(b.leftUpperArm, 0.08, -0.1, 1.18 - armSwing - breathe * 0.01);
      set(b.rightUpperArm, 0.08, 0.1, -1.18 + armSwing + breathe * 0.01);
      set(b.leftLowerArm, 0, 0.35, 0.12);
      set(b.rightLowerArm, 0, -0.35, -0.12);
      set(b.leftHand, 0, 0, 0.08);
      set(b.rightHand, 0, 0, -0.08);
    }
    // A local rotation written in VRM 0.x axes (see Core.vrmEuler).
    rot(bone, x, y, z) { const e = Core.vrmEuler(this.version, x, y, z); bone.rotation.set(e[0], e[1], e[2]); }
    setExpression(name, value) { if (this.has(name)) this.vrm.expressionManager.setValue(name, Math.max(0, Math.min(1, value))); }
    update(dt, st) {
      this.st = st;
      const vrm = this.vrm, b = this.bones, time = st.time;
      const reduce = st.reduced;
      const profile = S.prefs.companion_personality_link ? Core.personalityProfile(st.personality) : { energy: 0.6, smile: 0.1 };
      const energy = (reduce ? 0.25 : 1) * (0.55 + profile.energy * 0.6);
      const speakingLevel = Math.min(1, st.mouth.aa + st.mouth.oh + st.mouth.ee * 0.8 + st.mouth.ih * 0.7 + st.mouth.ou * 0.8);

      this.basePose(time, energy);

      // --- head: idle drift + follow the cursor + a little bob while talking
      let lookX = 0, lookY = 0;
      if (S.prefs.companion_look_at && st.look.active) { lookX = st.look.x; lookY = st.look.y; }
      const k = 1 - Math.exp(-dt * 4);
      this.headLook.x += (lookX - this.headLook.x) * k;
      this.headLook.y += (lookY - this.headLook.y) * k;
      const drift = reduce ? 0 : 1;
      let hx = Core.vrmHeadPitch(this.headLook.y) + Math.sin(time * 0.9) * 0.015 * drift + speakingLevel * Math.sin(time * 9) * 0.02 * drift;
      let hy = this.headLook.x * 0.38 + Math.sin(time * 0.43) * 0.04 * drift * energy;
      let hz = Math.sin(time * 0.71) * 0.025 * drift * energy - this.headLook.x * 0.04;
      // --- gestures layered on top
      const g = st.gesture;
      let emotionBoost = null, bounce = 0;
      if (g) {
        const p = (time - g.start) / g.dur;
        const env = (x) => Math.sin(Math.min(1, Math.max(0, x)) * Math.PI);
        if (g.name === "think") {
          if (!g.end) g.endAt = time;
          const fadeIn = Math.min(1, (time - g.start) / 0.5);
          const fadeOut = g.end ? Math.max(0, 1 - (time - g.endAt) / 0.5) : 1;
          const w = fadeIn * fadeOut;
          hz += 0.13 * w; hx -= 0.07 * w; hy -= 0.08 * w;
          if (!reduce) this.thinkPose(w);
          if (g.end && fadeOut <= 0) st.gesture = null;
        } else if (p >= 1) {
          st.gesture = null;
        } else if (g.name === "nod") {
          hx += Math.sin(p * Math.PI * 4) * 0.13 * env(p) * (reduce ? 0.5 : 1);
        } else if (g.name === "shake") {
          hy += Math.sin(p * Math.PI * 4) * 0.2 * env(p);
        } else if (g.name === "wave") {
          const w = env(Math.min(1, p * 1.6) * 0.5 + (p > 0.6 ? (p - 0.6) * 1.25 : 0));
          const up = Math.min(1, p * 5) * Math.min(1, (1 - p) * 5);
          this.wavePose(up, time);
          hz += 0.06 * w;
          emotionBoost = { name: "happy", v: 0.55 * up };
        } else if (g.name === "bounce") {
          bounce = Math.abs(Math.sin(p * Math.PI * 2)) * 0.025 * env(p);
          hz += Math.sin(p * Math.PI * 2) * 0.06;
        } else if (g.name === "surprise") {
          hx -= 0.12 * env(p);
          emotionBoost = { name: "surprised", v: env(p) };
        }
      }
      if (b.hips && this.hipsRest) b.hips.position.y = this.hipsRest + bounce;
      if (b.neck) this.rot(b.neck, hx * 0.35, hy * 0.35, hz * 0.3);
      if (b.head) this.rot(b.head, hx * 0.65, hy * 0.65, hz * 0.7);

      // --- eyes: follow the cursor with small saccades
      this.saccade.next -= dt;
      if (this.saccade.next <= 0) {
        this.saccade = { x: (Math.random() - 0.5) * 0.12, y: (Math.random() - 0.5) * 0.08, next: 0.6 + Math.random() * 2.2 };
      }
      const cam = this.camera.position;
      const thinkUp = g && g.name === "think" ? 0.25 : 0;
      this.lookTarget.position.set(cam.x + (lookX * 0.9 + this.saccade.x) * cam.z * 0.5,
        cam.y - (lookY * 0.6 - thinkUp + this.saccade.y) * cam.z * 0.4, cam.z);

      // --- expressions: emotion, base smile from personality, mouth, blink
      const target = {};
      const em = st.emotion;
      if (em.name !== "neutral" && em.name !== "thinking") {
        let name = em.name;
        if (!this.has(name)) name = { surprised: "happy", relaxed: "happy" }[name] || name;
        target[name] = em.intensity * (name === "happy" ? 0.85 : 0.8);
      } else if (profile.smile && !reduce) {
        target[this.has("relaxed") ? "relaxed" : "happy"] = profile.smile;
      }
      if (emotionBoost) {
        const n = this.has(emotionBoost.name) ? emotionBoost.name : "happy";
        target[n] = Math.max(target[n] || 0, emotionBoost.v);
      }
      // A closed-eye smile hides the lips: tone emotions down while talking.
      const talking = speakingLevel > 0.05;
      for (const name of ["happy", "sad", "angry", "relaxed", "surprised"]) {
        let v = target[name] || 0;
        if (talking) v *= 0.6;
        const cur = this.weights[name] || 0;
        this.weights[name] = cur + (v - cur) * (1 - Math.exp(-dt * 5));
        this.setExpression(name, this.weights[name]);
      }
      for (const v of Core.VISEMES) this.setExpression(v, st.mouth[v] * 0.95);
      if (!this.has("surprised") && emotionBoost && emotionBoost.name === "surprised") this.setExpression("oh", Math.max(st.mouth.oh, 0.35 * emotionBoost.v));
      // Blink: random interval, sometimes double; skipped while eyes smile shut.
      const bl = this.blink;
      bl.next -= dt;
      let blinkValue = 0;
      if (bl.t >= 0) {
        bl.t += dt;
        const d = 0.16;
        blinkValue = bl.t < d / 2 ? bl.t / (d / 2) : Math.max(0, 1 - (bl.t - d / 2) / (d / 2));
        if (bl.t >= d) { bl.t = -1; if (bl.double) { bl.double = false; bl.next = 0.12; } }
      } else if (bl.next <= 0) {
        bl.t = 0; bl.double = Math.random() < 0.18; bl.next = 2 + Math.random() * 4.5;
      }
      if ((this.weights.happy || 0) > 0.5) blinkValue = 0;
      this.setExpression("blink", blinkValue);
      vrm.update(dt);
      if (this.cpuSkinned) this.cpuSkin();
    }
    prepareCpuSkin(root) {
      const list = [];
      root.traverse((node) => {
        const geometry = node.geometry;
        if (!node.isMesh || !geometry || !geometry.attributes.position) return;
        const morphs = (geometry.morphAttributes && geometry.morphAttributes.position) || [];
        if (!node.isSkinnedMesh && !morphs.length) return;
        const position = geometry.attributes.position;
        list.push({ mesh: node, position: position, base: Float32Array.from(position.array), skinned: !!node.isSkinnedMesh,
          skeleton: node.skeleton, morphs: morphs.map((a) => a.array), relative: geometry.morphTargetsRelative !== false });
        // Rendered as a plain mesh: the shader samples neither the bone nor the morph texture.
        node.isSkinnedMesh = false;
        if (morphs.length) geometry.morphAttributes = {};
      });
      return list;
    }
    cpuSkin() {
      const T = this.V.THREE, v = new T.Vector3();
      const done = new Set();
      const apply = T.SkinnedMesh.prototype.applyBoneTransform;
      for (const item of this.cpuSkinned) {
        if (item.skinned && !done.has(item.skeleton)) { item.skeleton.update(); done.add(item.skeleton); }
        const base = item.base, out = item.position.array, mesh = item.mesh;
        const influences = mesh.morphTargetInfluences || [];
        const active = [];
        for (let k = 0; k < item.morphs.length; k++) if (influences[k] > 0.001) active.push(k);
        for (let i = 0, n = item.position.count; i < n; i++) {
          const j = i * 3;
          let x = base[j], y = base[j + 1], z = base[j + 2];
          for (const k of active) {
            const d = item.morphs[k], w = influences[k];
            if (item.relative) { x += d[j] * w; y += d[j + 1] * w; z += d[j + 2] * w; }
            else { x += (d[j] - base[j]) * w; y += (d[j + 1] - base[j + 1]) * w; z += (d[j + 2] - base[j + 2]) * w; }
          }
          if (item.skinned) { v.set(x, y, z); apply.call(mesh, i, v); x = v.x; y = v.y; z = v.z; }
          out[j] = x; out[j + 1] = y; out[j + 2] = z;
        }
        item.position.needsUpdate = true;
      }
    }
    render() { this.renderer.render(this.scene, this.camera); }
    // The character's box in stage CSS px — the hit box and, on the desktop, the window region (SetWindowRgn
    // clips the drawing too). Round 26: read from the pixels just drawn (called right after render()), not guessed
    // from a few bones: twin-tails, sleeves, fingers and feet reached past the old guess and the region cut them
    // off. While a gesture plays, the places its hands will reach (measureGestures) are added up front.
    bbox() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      const box = this.paintedBox(w, h) || { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
      const g = this.st && this.st.gesture;
      if (g && this.reach && g.name !== "nod" && g.name !== "shake") {
        const v = new this.V.THREE.Vector3();
        for (const p of this.reach.points) {
          for (const dx of [-p.m, p.m]) {
            v.set(p.x + dx, p.y + p.m, p.z).project(this.camera);
            const px = (v.x + 1) / 2 * w, py = (1 - v.y) / 2 * h;
            box.x0 = Math.min(box.x0, px); box.x1 = Math.max(box.x1, px); box.y0 = Math.min(box.y0, py); box.y1 = Math.max(box.y1, py);
          }
        }
      }
      const x0 = Math.max(0, box.x0), y0 = Math.max(0, box.y0), x1 = Math.min(w, box.x1), y1 = Math.min(h, box.y1);
      if (!(x1 - x0 >= 2 && y1 - y0 >= 2)) return null;
      return { x: Math.floor(x0), y: Math.floor(y0), w: Math.ceil(x1 - x0), h: Math.ceil(y1 - y0) };
    }
    // Bounds of the non-transparent pixels of the last frame, in CSS px.
    paintedBox(w, h) {
      const gl = this.renderer.getContext();
      const W = gl.drawingBufferWidth, H = gl.drawingBufferHeight;
      if (!W || !H) return null;
      if (!this.pixels || this.pixels.length !== W * H * 4) this.pixels = new Uint8Array(W * H * 4);
      try { gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, this.pixels); } catch (_) { return null; }
      return Core.alphaBounds(this.pixels, W, H, w / W, h / H);
    }
    dispose() {
      try {
        if (this.vrm) { this.scene.remove(this.vrm.scene); this.V.VRMUtils.deepDispose(this.vrm.scene); }
        if (this.renderer) this.renderer.dispose();
      } catch (_) {}
      this.vrm = null;
    }
  }

  /* ============================================================== Live2D */
  // Live2D runtime files are downloaded by the user (with consent) into the
  // data folder; MemuAI does not ship them — see companion_avatar.py.
  //
  // Round 25 framing: the model's own canvas (originalWidth × originalHeight) says little about where the
  // character is — a waving sleeve or a wand often reaches past it, and the old "bust" scale let the canvas
  // overflow the stage by up to 60 % sideways (cut sleeves in the R25 screenshot). Now the stage measures the
  // VISIBLE drawables (rest pose + an envelope that only grows while motions play) and fits that box with room
  // for raised arms; a growing envelope re-fits smoothly instead of cutting a hand.
  const L2D_PAD = { x: 0.04, top: 0.035, bottom: 0.02 };   // share of the character's height
  const L2D_BUST = 0.58;                                     // bust: the upper 58 % of the character
  class Live2DDriver {
    async load(model) {
      const base = "/api/companion/runtime/files/";
      try {
        await loadScript(base + "live2dcubismcore.min.js");
        await loadScript(base + "pixi.min.js");
        await loadScript(base + "unsafe-eval.min.js");
        await loadScript(base + "cubism4.min.js");
      } catch (_) {
        throw Object.assign(new Error("runtime"), { code: "live2d_runtime",
          userMessage: t("Среда Live2D не установлена — установите её в настройках компаньона", "The Live2D runtime is not installed — install it in the companion settings") });
      }
      const PIXI = window.PIXI;
      if (!PIXI || !PIXI.live2d) throw Object.assign(new Error("pixi-live2d-display missing"), { code: "live2d_runtime" });
      // Read model3.json first: its motion groups decide the idle group, and a Cubism 2 file gets a clear message.
      let settings;
      try {
        const response = await fetch(model.url, { credentials: "same-origin" });
        if (!response.ok) throw Object.assign(new Error("HTTP " + response.status), { code: "missing" });
        settings = await response.json();
      } catch (error) {
        if (error && error.code) throw error;
        throw Object.assign(new Error("model3.json"), { code: "bad_model", userMessage: t("Файл модели Live2D повреждён", "The Live2D model file is damaged") });
      }
      if (!settings || !settings.FileReferences || !settings.FileReferences.Moc) {
        throw Object.assign(new Error("not cubism 3+"), { code: "cubism2",
          userMessage: t("Это модель Cubism 2 — поддерживаются Cubism 3, 4 и 5 (*.model3.json)", "This is a Cubism 2 model — Cubism 3, 4 and 5 (*.model3.json) are supported") });
      }
      const groups = Object.keys(settings.FileReferences.Motions || {});
      // pixi-live2d-display only knows "Idle" (case-sensitive). Many models say "idle" or "Idle_1"; without an
      // idle group a body motion never ends and the pose sticks (an arm left in the air).
      this.idleGroup = groups.find((g) => g === "Idle") || groups.find((g) => /^idle/i.test(g)) || "";
      this.tapGroup = groups.find((g) => /^tap.?body$/i.test(g)) || groups.find((g) => /tap|flick/i.test(g)) || "";
      const lowPower = S.prefs.companion_low_power;
      this.app = new PIXI.Application({ view: canvas, backgroundAlpha: 0, autoStart: false, antialias: !lowPower,
        resolution: Math.min(window.devicePixelRatio || 1, lowPower ? 1 : 2), autoDensity: true,
        width: canvas.clientWidth || 300, height: canvas.clientHeight || 300, powerPreference: lowPower ? "low-power" : "default" });
      const options = { autoInteract: false, autoUpdate: false };
      if (this.idleGroup) options.idleMotionGroup = this.idleGroup;
      const m = await PIXI.live2d.Live2DModel.from(model.url, options);
      this.model = m;
      // pose3.json (which of the alternative arm parts is shown) arrives a moment after the model; measuring
      // before it counted every hidden arm variant as part of the character.
      if (settings.FileReferences.Pose && !m.internalModel.pose) {
        await new Promise((resolve) => {
          const done = () => { clearTimeout(timer); m.off("poseLoaded", done); m.off("poseLoadError", done); resolve(); };
          const timer = setTimeout(done, 2000);
          m.on("poseLoaded", done); m.on("poseLoadError", done);
        });
      }
      this.app.stage.addChild(m);
      const core = m.internalModel.coreModel;
      this.core = core;
      const ids = new Set();
      try { for (let i = 0; i < core._model.parameters.count; i++) ids.add(core._model.parameters.ids[i]); } catch (_) {}
      this.ids = ids;
      const defs = m.internalModel.settings;
      this.expressions = (defs.expressions || []).map((e) => e.Name || e.name).filter(Boolean);
      this.motions = groups;
      // Hit areas are transparent helper meshes: never part of the measured character.
      this.skip = new Set();
      for (const area of defs.hitAreas || []) {
        try { const i = core.getDrawableIndex(area.Id || area.id); if (i >= 0) this.skip.add(i); } catch (_) {}
      }
      this.headArea = ((defs.hitAreas || []).find((h) => /head|face/i.test((h.Name || h.name || "") + " " + (h.Id || h.id || ""))) || {});
      this.meta = { kind: "live2d", expressions: this.expressions, motions: this.motions, idle: this.idleGroup };
      this.state = { mouth: 0, form: 0, emotion: "" };
      m.internalModel.on("beforeModelUpdate", () => this.applyParams());
      // One update so pose3.json (alternative arm parts) and the idle motion are applied before measuring.
      for (let i = 0; i < 3; i++) { m.update(16); m.internalModel.update(16, performance.now()); }
      this.rest = this.measure();
      this.env = this.rest && Object.assign({}, this.rest);
      this.faceLocal = this.measureFace();
      this.measureAt = 0;
      this.fit = null;
      this.resize();
      await this.measureMotions(PIXI);
      this.resize();
    }
    // Round 26: play the motions she can play here (the idle group; the tap group a click starts) once, silently
    // and before she is shown, so the envelope — and the frame — already have room for every reach. Before, the
    // frame grew only after a hand had crossed the edge (cut off for up to a second the first time).
    async measureMotions(PIXI) {
      const m = this.model, mm = m.internalModel.motionManager;
      const defs = (mm && mm.definitions) || {};
      const groups = this.idleGroup ? [this.idleGroup, this.tapGroup].filter(Boolean) : [];
      const config = PIXI.live2d.config || {};
      const sound = config.sound;
      config.sound = false;
      try {
        for (const group of groups) {
          for (let i = 0; i < Math.min(4, (defs[group] || []).length); i++) {
            let started = false;
            try { started = await m.motion(group, i, 3); } catch (_) {}
            for (let k = 0; started && k < 40 && !mm.isFinished(); k++) {
              m.update(150);
              this.grow(this.measure(this.frameCut()));
            }
          }
        }
      } finally {
        config.sound = sound;
        try { mm.stopAllMotions(); } catch (_) {}
      }
    }
    // Bust framing: only what is inside the frame counts (model y of the stage's lower edge); full: everything.
    frameCut() {
      const f = this.fit;
      return S.prefs.companion_framing !== "full" && f ? (Math.max(1, canvas.clientHeight) - f.y) / f.scale : null;
    }
    applyPrefs() { this.resize(); }
    // Bounds of the visible drawables in the model's local px (y down); `cut` limits the horizontal extent to
    // drawables' vertices above that y (bust framing ignores a wide skirt below the frame).
    measure(cut) {
      const core = this.core, im = this.model && this.model.internalModel;
      if (!core || !im) return null;
      const ppu = im.pixelsPerUnit || 1, ow = im.originalWidth / 2, oh = im.originalHeight / 2;
      const lt = im.localTransform;
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      try {
        const count = core.getDrawableCount();
        for (let i = 0; i < count; i++) {
          if (this.skip.has(i) || !core.getDrawableDynamicFlagIsVisible(i) || core.getDrawableOpacity(i) < 0.05) continue;
          const v = core.getDrawableVertices(i);
          for (let k = 0; k + 1 < v.length; k += 2) {
            const y = -v[k + 1] * ppu + oh;
            if (cut != null && y > cut) continue;
            const x = v[k] * ppu + ow;
            if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
          }
        }
      } catch (_) { return null; }
      if (!isFinite(x0) || x1 - x0 < 1 || y1 - y0 < 1) return null;
      // model px → the Live2DModel's local space (layout scale/offset of model3.json)
      return { x0: x0 * lt.a + lt.tx, x1: x1 * lt.a + lt.tx, y0: y0 * lt.d + lt.ty, y1: y1 * lt.d + lt.ty };
    }
    target() {
      const w = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight);
      const m = this.model, rest = this.rest, env = this.env;
      if (!rest || !env) {
        // Nothing measurable (all drawables hidden at start): fit the model canvas.
        return Core.fitBox({ x0: 0, y0: 0, x1: m.internalModel.width, y1: m.internalModel.height }, w, h, { headroom: HEADROOM });
      }
      const H = rest.y1 - rest.y0;
      const k = S.prefs.companion_gestures && !S.reduced ? 1 : 0.5;
      const pad = { x: L2D_PAD.x * H * k, top: L2D_PAD.top * H * k, bottom: L2D_PAD.bottom * H };
      if (S.prefs.companion_framing === "full") return Core.fitBox(env, w, h, { headroom: HEADROOM, pad: pad });
      // Bust: the largest scale at which everything inside the visible height also fits the width. The lower
      // edge may cut the body (that is the framing); the sides never cut a sleeve or a hand.
      const grow = { l: Math.max(0, rest.x0 - env.x0), r: Math.max(0, env.x1 - rest.x1) };
      const top = env.y0 - pad.top, room = h * (1 - HEADROOM);
      const span = (scale) => {
        const ext = this.measure(top + room / scale);
        return ext ? { x0: ext.x0 - grow.l - pad.x, x1: ext.x1 + grow.r + pad.x } : { x0: env.x0 - pad.x, x1: env.x1 + pad.x };
      };
      const fits = (scale) => { const e = span(scale); return (e.x1 - e.x0) * scale <= w; };
      let scale = room / Math.max(1, rest.y0 + H * L2D_BUST - top);
      if (!fits(scale)) {
        let lo = 0, hi = scale;
        for (let i = 0; i < 14; i++) { const mid = (lo + hi) / 2; if (fits(mid)) lo = mid; else hi = mid; }
        scale = Math.max(lo, 1e-6);
      }
      const e = span(scale);
      return { scale: scale, x: w / 2 - ((e.x0 + e.x1) / 2) * scale, y: h - room - top * scale };
    }
    place(fit) {
      const m = this.model;
      m.scale.set(fit.scale);
      m.x = fit.x; m.y = fit.y;
    }
    resize() {
      if (!this.app) return;
      const w = Math.max(1, canvas.clientWidth), h = Math.max(1, canvas.clientHeight);
      this.app.renderer.resize(w, h);
      if (!this.model) return;
      this.fit = this.target();
      this.place(this.fit);
    }
    // Grow the envelope when a motion reaches past it (checked twice a second). In bust framing only what is
    // inside the frame counts (a wand swinging at the hips must not shrink her), and one reach is capped at a
    // fifth of her height so a stray effect mesh cannot make her tiny.
    track(now) {
      if (!this.rest || now - this.measureAt < 500) return;
      this.measureAt = now;
      const cur = this.measure(this.frameCut());
      if (!cur) return;
      this.faceLocal = this.measureFace();
      if (this.grow(cur)) this.goal = this.target();
    }
    grow(cur) {
      if (!cur || !this.rest) return false;
      const rest = this.rest, H = rest.y1 - rest.y0, tol = H * 0.01, cap = H * 0.2;
      const env = this.env;
      let grew = false;
      for (const [key, sign] of [["x0", -1], ["y0", -1], ["x1", 1], ["y1", 1]]) {
        const value = sign < 0 ? Math.max(cur[key], rest[key] - cap) : Math.min(cur[key], rest[key] + cap);
        if ((value - env[key]) * sign > tol) { env[key] = value; grew = true; }
      }
      return grew;
    }
    param(id, value, weight) {
      if (!this.ids.size || this.ids.has(id)) {
        try { this.core.addParameterValueById(id, value, weight == null ? 1 : weight); } catch (_) {}
      }
    }
    setParam(id, value) {
      if (!this.ids.size || this.ids.has(id)) { try { this.core.setParameterValueById(id, value); } catch (_) {} }
    }
    applyParams() {
      const st = this.st;
      if (!st) return;
      const mouth = st.mouth;
      const open = Math.min(1, mouth.aa + mouth.oh * 0.9 + mouth.ou * 0.6 + mouth.ee * 0.6 + mouth.ih * 0.5);
      const form = Math.max(-1, Math.min(1, (mouth.ee + mouth.ih) * 1.2 - (mouth.ou + mouth.oh) * 1.2));
      if (S.prefs.companion_lipsync && open > 0.01) {
        this.setParam("ParamMouthOpenY", open);
        this.setParam("ParamMouthForm", form);
      }
      const e = st.emotion;
      const w = e.intensity || 0;
      if (e.name === "happy" || e.name === "relaxed") { this.param("ParamEyeLSmile", w); this.param("ParamEyeRSmile", w); this.param("ParamMouthForm", 0.5 * w); }
      if (e.name === "sad") { this.param("ParamBrowLY", -0.6 * w); this.param("ParamBrowRY", -0.6 * w); this.param("ParamBrowLForm", -0.8 * w); this.param("ParamBrowRForm", -0.8 * w); }
      if (e.name === "angry") { this.param("ParamBrowLAngle", -0.9 * w); this.param("ParamBrowRAngle", -0.9 * w); this.param("ParamBrowLForm", -1 * w); this.param("ParamBrowRForm", -1 * w); }
      if (e.name === "surprised") { this.param("ParamBrowLY", 0.8 * w); this.param("ParamBrowRY", 0.8 * w); this.param("ParamEyeLOpen", 0.25 * w); this.param("ParamEyeROpen", 0.25 * w); }
      const g = st.gesture;
      if (g) {
        const p = (st.time - g.start) / g.dur, env = Math.sin(Math.min(1, Math.max(0, p)) * Math.PI);
        if (g.name === "nod") this.param("ParamAngleY", Math.sin(p * Math.PI * 4) * -12 * env);
        else if (g.name === "shake") this.param("ParamAngleX", Math.sin(p * Math.PI * 4) * 18 * env);
        else if (g.name === "think") { this.param("ParamAngleZ", 10); this.param("ParamEyeBallY", 0.6); }
        else if (g.name === "bounce" || g.name === "wave") this.param("ParamBodyAngleZ", Math.sin(p * Math.PI * 2) * 6 * env);
        if (g.name !== "think" && p >= 1) st.gesture = null;
        if (g.name === "think" && g.end) st.gesture = null;
      }
      if (!st.reduced) this.param("ParamBodyAngleX", Math.sin(st.time * 0.6) * 3);
    }
    update(dt, st) {
      this.st = st;
      const m = this.model;
      const focus = m.internalModel.focusController;
      const aim = S.prefs.companion_look_at && st.look.active ? Core.live2dFocus(st.look) : { x: 0, y: 0 };
      focus.focus(aim.x, aim.y);
      // Emotion → the model's own expression file when one matches.
      const want = st.emotion.name !== "neutral" ? st.emotion.name : "";
      if (want !== this.state.emotion) {
        this.state.emotion = want;
        const words = { happy: ["happy", "smile", "joy", "fun", "f01"], sad: ["sad", "sorrow", "cry"], angry: ["angry", "mad"],
          surprised: ["surprise", "shock"], relaxed: ["relax", "calm"], thinking: ["think"] }[want] || [];
        const match = this.expressions.find((name) => words.some((w) => name.toLowerCase().includes(w)));
        try { if (match) m.expression(match); else if (!want) m.internalModel.motionManager.expressionManager?.resetExpression(); } catch (_) {}
      }
      // Round 25: only a click on her (the author's "tap body") plays a body motion, and only when the model has
      // an idle group to return to. Nods, shakes, waves and "think" are procedural head/body moves: before, every
      // answer's nod started a random TapBody motion (often a hand to the hair), and on models without "Idle"
      // the arm stayed up for good.
      const g = st.gesture;
      if (g && g !== this.lastGesture) {
        this.lastGesture = g;
        if (g.name === "bounce" && this.tapGroup && this.idleGroup) { try { m.motion(this.tapGroup); } catch (_) {} }
      }
      m.update(dt * 1000);
      const now = performance.now();
      this.track(now);
      if (this.goal && this.fit) {
        const k = 1 - Math.exp(-dt * 6);
        const f = this.fit, g2 = this.goal;
        f.scale += (g2.scale - f.scale) * k; f.x += (g2.x - f.x) * k; f.y += (g2.y - f.y) * k;
        if (Math.abs(g2.scale - f.scale) < 1e-4 * g2.scale) { this.fit = g2; this.goal = null; }
        this.place(this.fit);
      }
    }
    render() { this.app.render(); }
    // The point between the eyes in stage CSS px: the "Head" hit area when the model has one, else the upper
    // eighth of the character.
    face() {
      const m = this.model, p = this.faceLocal;
      return m && p ? { x: m.x + p.x * m.scale.x, y: m.y + p.y * m.scale.y } : null;
    }
    measureFace() {
      const m = this.model, rest = this.rest;
      if (!m || !rest) return null;
      const id = this.headArea.Id || this.headArea.id;
      if (id) {
        try {
          const i = this.core.getDrawableIndex(id);
          const b = i >= 0 ? m.internalModel.getDrawableBounds(i, {}) : null;
          if (b && b.width > 0) {
            const lt = m.internalModel.localTransform;
            return { x: (b.x + b.width / 2) * lt.a + lt.tx, y: (b.y + b.height * 0.55) * lt.d + lt.ty };
          }
        } catch (_) {}
      }
      return { x: (rest.x0 + rest.x1) / 2, y: rest.y0 + (rest.y1 - rest.y0) * 0.13 };
    }
    bbox() {
      const m = this.model;
      if (!m) return null;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      let box = this.measure() || { x0: 0, y0: 0, x1: m.internalModel.width, y1: m.internalModel.height };
      // Round 26: plus everything her motions have reached so far (the envelope), so the desktop region is not
      // a step behind a swinging arm between two reports.
      const env = this.env;
      if (env) box = { x0: Math.min(box.x0, env.x0), y0: Math.min(box.y0, env.y0), x1: Math.max(box.x1, env.x1), y1: Math.max(box.y1, env.y1) };
      const s = m.scale.x;
      const x0 = Math.max(0, m.x + box.x0 * s), y0 = Math.max(0, m.y + box.y0 * s);
      const x1 = Math.min(w, m.x + box.x1 * s), y1 = Math.min(h, m.y + box.y1 * s);
      if (x1 - x0 < 2 || y1 - y0 < 2) return null;
      return { x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0) };
    }
    debug() {
      const f = this.fit;
      return { idle: this.idleGroup, tap: this.tapGroup, rest: this.rest, env: this.env, scale: f && +f.scale.toFixed(4) };
    }
    dispose() { try { this.app && this.app.destroy(false, { children: true, texture: true, baseTexture: true }); } catch (_) {} this.model = null; }
  }

  post("ready");
})();
