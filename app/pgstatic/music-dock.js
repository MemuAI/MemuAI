

(function () {
  "use strict";
  if (window.MemuMusicDock) return;
  const L = (ru, en) => ((typeof PG_LANG !== "undefined" ? PG_LANG : "ru") === "en" ? en : ru);
  const MODULE = "music";
  const PC_TARGETS = ["", "pc", "desktop", "memuai", "dock"];
  const ICONS = {
    play: '<path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="4" height="14" rx="1.3" fill="currentColor" stroke="none"/><rect x="13.5" y="5" width="4" height="14" rx="1.3" fill="currentColor" stroke="none"/>',
    next: '<path d="M6 6.2v11.6a.8.8 0 0 0 1.25.66L15.8 12.7a.8.8 0 0 0 0-1.4L7.25 5.54A.8.8 0 0 0 6 6.2z" fill="currentColor" stroke="none"/><rect x="16.6" y="5.5" width="2.4" height="13" rx="1.1" fill="currentColor" stroke="none"/>',
    prev: '<path d="M18 6.2v11.6a.8.8 0 0 1-1.25.66L8.2 12.7a.8.8 0 0 1 0-1.4l8.55-5.76A.8.8 0 0 1 18 6.2z" fill="currentColor" stroke="none"/><rect x="5" y="5.5" width="2.4" height="13" rx="1.1" fill="currentColor" stroke="none"/>',
    open: '<path d="M14 4h6v6M20 4l-8 8M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
    close: '<path d="M7 7l10 10M17 7 7 17"/>',
    chev: '<path d="m7 14 5-5 5 5"/>',
  };
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

  const st = {
    booted: false, mode: "idle",
    track: null, trackId: "", queue: [], index: -1, playing: false, position: 0, posAt: 0, duration: 0,
    volume: 0.8, shuffle: false, repeat: "off", context: "", device: "", deviceName: "", updatedAt: 0, serverSkew: 0, localSince: 0,
    expanded: false, dismissed: false, moduleOpen: false, moduleMinimized: true, expect: null,
  };
  const meta = new Map();
  let audio = null, el = null, es = null, reportTimer = 0, clockTimer = 0, playedFor = "", cssLoaded = false, announced = { sig: "", now: null };
  let pendingSeek = null, loadGeneration = 0, sourceGeneration = 0;

  async function call(op, args) {
    const res = await fetch("/api/pg/music/call", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ op, args: args || {}, lang: L("ru", "en") }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
    return data.result;
  }
  const soft = (op, args) => call(op, args).catch(() => null);
  async function trackMeta(id) {
    if (!id) return null;
    if (meta.has(id)) return meta.get(id);
    const tr = await soft("library.get", { id });
    if (tr && tr.id) meta.set(id, tr);
    return tr && tr.id ? tr : null;
  }
  function musicEnabled() {
    const mm = window.MemuModules;
    if (!mm) return false;
    const row = (mm.catalog || []).find((item) => item.id === MODULE);
    return !!(row && row.enabled);
  }
  function moduleWindow() {
    const mm = window.MemuModules;
    const ws = mm && typeof mm.windowState === "function" && (mm.openIds || []).includes(MODULE) ? mm.windowState(MODULE) : null;
    return ws;
  }
  const now = () => Date.now() / 1000;
  const position = () => (st.mode === "local" && audio ? (pendingSeek ? pendingSeek.at : audio.currentTime || 0)
    : st.playing ? Math.min(st.duration || 1e9, st.position + (now() - st.posAt) * (st.speed || 1)) : st.position);

  function ensureCss() {
    if (cssLoaded) return;
    cssLoaded = true;
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = "/music-dock.css?v=1.1-r38";
    document.head.appendChild(link);
  }
  function hexHue(hex) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255), hi = Math.max(r, g, b), d = hi - Math.min(r, g, b);
    if (!d) return 0;
    const h = hi === r ? ((g - b) / d) % 6 : hi === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return Math.round(h * 60 + 360) % 360;
  }
  function mount() {
    if (el) return el;
    ensureCss();
    el = document.createElement("section");
    el.id = "memu-music-dock";
    el.className = "mmd";
    el.hidden = true;
    el.setAttribute("aria-label", "MemuMusic");
    el.innerHTML = `
      <div class="mmd-glow" aria-hidden="true"></div>
      <div class="mmd-row">
        <button type="button" class="mmd-art" data-mmd="expand"><i class="mmd-art-fb"></i><img alt="" decoding="async"><span class="mmd-eq" aria-hidden="true"><i></i><i></i><i></i></span></button>
        <button type="button" class="mmd-text" data-mmd="expand"><b class="mmd-title"></b><span class="mmd-sub"></span></button>
        <button type="button" class="mmd-btn mmd-play" data-mmd="toggle"></button>
        <button type="button" class="mmd-btn mmd-next" data-mmd="next">${svg("next")}</button>
      </div>
      <div class="mmd-more">
        <div class="mmd-seek" data-mmd="seek" role="slider" tabindex="0" aria-valuemin="0" aria-valuemax="100"><i class="mmd-fill"></i></div>
        <div class="mmd-times"><span class="mmd-cur">0:00</span><span class="mmd-dur">0:00</span></div>
        <div class="mmd-ctl">
          <button type="button" class="mmd-btn" data-mmd="open">${svg("open")}</button>
          <button type="button" class="mmd-btn" data-mmd="prev">${svg("prev")}</button>
          <button type="button" class="mmd-btn mmd-big" data-mmd="toggle"></button>
          <button type="button" class="mmd-btn" data-mmd="next">${svg("next")}</button>
          <button type="button" class="mmd-btn" data-mmd="close">${svg("close")}</button>
        </div>
      </div>
      <div class="mmd-bar" aria-hidden="true"><i class="mmd-prog"></i></div>
      <button type="button" class="mmd-chev" data-mmd="expand">${svg("chev")}</button>`;

    const narrow = window.matchMedia ? matchMedia("(max-width: 800px)") : { matches: false, addEventListener() {} };
    const place = () => {
      const host = document.querySelector(".side-bottom");
      if (host && !narrow.matches) { if (el.parentElement !== host) host.insertBefore(el, host.firstChild); }
      else if (el.parentElement !== document.body) document.body.appendChild(el);
    };
    place();
    narrow.addEventListener("change", place);
    el.addEventListener("click", onClick);
    el.querySelector("img").addEventListener("error", (e) => { e.target.removeAttribute("src"); el.classList.remove("has-art"); });
    const seek = el.querySelector(".mmd-seek");
    seek.addEventListener("pointerdown", (e) => {
      const r = seek.getBoundingClientRect();
      const k = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      if (st.duration) seekTo(k * st.duration);
    });
    seek.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); seekTo(position() + (e.key === "ArrowLeft" ? -5 : 5)); }
    });
    relabel();
    return el;
  }
  function relabel() {
    if (!el) return;
    const set = (sel, ru, en) => el.querySelectorAll(sel).forEach((b) => { b.title = L(ru, en); b.setAttribute("aria-label", L(ru, en)); });
    set('[data-mmd="next"]', "Следующий трек", "Next track");
    set('[data-mmd="prev"]', "Предыдущий трек", "Previous track");
    set('[data-mmd="open"]', "Открыть MemuMusic", "Open MemuMusic");
    set('[data-mmd="close"]', "Остановить и скрыть", "Stop and hide");
    set(".mmd-chev", st.expanded ? "Свернуть плеер" : "Развернуть плеер", st.expanded ? "Collapse the player" : "Expand the player");
    set(".mmd-seek", "Перемотка", "Seek");
    render();
  }

  function dockMode() {
    const v = window.MemuModInt && MemuModInt.widgetValues ? MemuModInt.widgetValues("main.dock") : null;
    if (!v) return "always";
    return v.enabled === false ? "off" : ["always", "playing", "off"].includes(v.mode) ? v.mode : "always";
  }
  function fmt(sec) { sec = Math.max(0, Math.floor(sec || 0)); return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0"); }
  function subline() {
    const artist = (st.track && st.track.artist) || "";
    if (st.mode === "remote" && st.device === "phone") return L("Играет на телефоне", "Playing on the phone");
    if (st.mode === "remote" && !st.moduleOpen && stale()) return artist || L("MemuMusic закрыт", "MemuMusic is closed");
    return artist;
  }
  function render() {
    if (!el) { announce(); return; }
    const mode = dockMode();
    const show = !!st.trackId && !st.dismissed && musicEnabled() && !(st.moduleOpen && !st.moduleMinimized) && mode !== "off" && (mode !== "playing" || st.playing);
    el.hidden = !show;
    document.documentElement.toggleAttribute("data-music-dock", show);
    announce();
    if (!show) { stopClock(); return; }
    const tr = st.track || {};
    el.querySelector(".mmd-title").textContent = tr.title || L("Трек", "Track");
    el.querySelector(".mmd-sub").textContent = subline();
    el.classList.toggle("is-playing", st.playing);
    el.classList.toggle("is-remote", st.mode === "remote");
    el.classList.toggle("is-phone", st.device === "phone" && st.mode === "remote");
    el.classList.toggle("open", st.expanded);

    const a = /^#[0-9a-f]{6}$/i.test(tr.accent || "") ? tr.accent : "";
    el.style.setProperty("--mmd-a", a || "var(--accent, #a78bfa)");
    el.style.setProperty("--mmd-b", a ? `hsl(${(hexHue(a) + 40) % 360} 72% 58%)` : "color-mix(in oklab, var(--accent, #a78bfa) 62%, #ff5c8a)");
    const img = el.querySelector("img");
    if (tr.cover) { if (img.getAttribute("src") !== tr.cover) img.src = tr.cover; el.classList.add("has-art"); }
    else { img.removeAttribute("src"); el.classList.remove("has-art"); }
    el.querySelectorAll('[data-mmd="toggle"]').forEach((b) => {
      b.innerHTML = svg(st.playing ? "pause" : "play");
      b.title = st.playing ? L("Пауза", "Pause") : L("Играть", "Play");
      b.setAttribute("aria-label", b.title);
    });
    progress();
    st.expanded && !document.hidden ? startClock() : stopClock();
  }

  function announce() {
    const tr = st.track;
    const sig = [st.trackId, st.playing, tr ? tr.id + (tr.title || "") + (tr.cover || "") : "", !!(el && !el.hidden)].join("|");
    if (sig === announced.sig) return;
    announced = { sig, now: { track_id: st.trackId, playing: st.playing, track: tr, dock: !!(el && !el.hidden) } };
    document.dispatchEvent(new CustomEvent("memu-music-state", { detail: announced.now }));
  }

  function progress() {
    if (!el) return;
    const dur = st.duration || (st.track && st.track.duration) || 0;
    const pos = position(), k = dur ? Math.max(0, Math.min(1, pos / dur)) : 0;
    const left = Math.max(0, (dur - pos) / ((st.mode === "local" && audio ? audio.playbackRate : st.speed) || 1));
    el.querySelectorAll(".mmd-prog, .mmd-fill").forEach((bar) => {
      bar.style.animation = "none";
      bar.style.setProperty("--k", k.toFixed(4));
      if (st.playing && dur && left > 0.2) {
        void bar.offsetWidth;
        bar.style.animation = `mmd-progress ${left.toFixed(2)}s linear forwards`;
      }
    });
    el.querySelector(".mmd-seek").setAttribute("aria-valuenow", String(Math.round(k * 100)));
    clock();
  }
  function clock() {
    if (!el || !st.expanded) return;
    const dur = st.duration || (st.track && st.track.duration) || 0;
    el.querySelector(".mmd-cur").textContent = fmt(position());
    el.querySelector(".mmd-dur").textContent = fmt(dur);
  }
  function startClock() { if (!clockTimer) clockTimer = setInterval(clock, 1000); }
  function stopClock() { clearInterval(clockTimer); clockTimer = 0; }
  const stale = () => st.playing && st.updatedAt && now() - st.serverSkew - st.updatedAt > 45;

  function onClick(e) {
    const b = e.target.closest("[data-mmd]");
    if (b) control(b.dataset.mmd);
  }
  function control(act) {
    if (act === "expand") { expand(!st.expanded); return; }
    if (act === "open") { openModule(); return; }
    if (act === "close") { stopAndHide(); return; }
    if (act === "toggle") { toggle(); return; }
    if (act === "next" || act === "prev") { st.mode === "local" ? (act === "next" ? next(false) : prev()) : remote(act); }
  }
  function expand(on) { st.expanded = !!on; relabel(); }
  function openModule() { if (window.MemuModules) window.MemuModules.open(MODULE); }
  function remote(cmd, args) {

    return soft("player.command", Object.assign({ cmd, target: "pc", by: "dock" }, args || {}));
  }
  function toggle() {
    if (st.mode === "local") return audio && !audio.paused ? pause() : play();
    if (st.mode === "remote" && (st.device === "phone" || (!st.moduleOpen && stale()) || (!st.moduleOpen && !st.playing && st.deviceName !== "MemuMusic"))) {
      return takeOver(true);
    }
    if (st.mode === "remote") {

      st.playing = !st.playing; st.expect = { playing: st.playing, at: now(), until: now() + 2.5 }; render();
      return remote(st.playing ? "play" : "pause");
    }
    return takeOver(true);
  }
  function seekTo(sec) {
    if (st.mode === "local" && audio) { if (pendingSeek) pendingSeek.at = Math.max(0, sec); else { try { audio.currentTime = Math.max(0, Math.min((audio.duration || 1e9) - 0.2, sec)); } catch (_) {  } } report(); }
    else if (st.mode === "remote") { st.position = sec; st.posAt = now(); remote("seek", { position: sec }); }
    progress();
  }
  function stopAndHide() {
    if (st.mode === "local") pause();
    else if (st.mode === "remote" && st.playing && st.device !== "phone") remote("pause");
    st.dismissed = true; st.expanded = false;
    render();
  }

  function ensureAudio() {
    if (audio) return audio;
    audio = new Audio();
    audio.preload = "auto";

    audio.addEventListener("play", () => { st.playing = true; render(); report({}); session(); });
    audio.addEventListener("pause", () => { if (st.mode !== "local") return; st.playing = false; render(); report({}); });
    audio.addEventListener("seeked", () => { progress(); });
    audio.addEventListener("loadedmetadata", () => {
      if (st.mode === "local" && pendingSeek && pendingSeek.generation === loadGeneration && sourceGeneration === loadGeneration) {
        try { audio.currentTime = pendingSeek.at; pendingSeek = null; report(); } catch (_) {  }
      }
      st.duration = audio.duration || st.duration; progress();
    });
    audio.addEventListener("ratechange", () => { progress(); report(); });
    audio.addEventListener("waiting", () => { el && el.classList.add("is-wait"); });
    audio.addEventListener("playing", () => { el && el.classList.remove("is-wait"); progress(); });
    audio.addEventListener("timeupdate", () => {

      if (st.trackId && playedFor !== st.trackId && audio.currentTime > Math.min(30, (audio.duration || 60) * 0.5)) {
        playedFor = st.trackId; soft("library.played", { id: st.trackId, seconds: Math.round(audio.currentTime) });
      }
    });
    audio.addEventListener("ended", () => next(true));
    audio.addEventListener("error", () => { if (st.mode === "local" && audio.src) { st.playing = false; render(); } });
    if ("mediaSession" in navigator) {
      const set = (name, fn) => { try { navigator.mediaSession.setActionHandler(name, fn); } catch (_) {  } };
      const local = (fn) => (d) => { if (st.mode === "local") fn(d); };
      set("play", local(play)); set("pause", local(pause));
      set("nexttrack", local(() => next(false))); set("previoustrack", local(prev));
      set("seekto", local((d) => seekTo(d.seekTime)));
    }
    return audio;
  }
  function session() {
    if (!("mediaSession" in navigator) || st.mode !== "local") return;
    const tr = st.track || {};
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: tr.title || "", artist: tr.artist || "", album: tr.album || "",
        artwork: tr.cover ? [{ src: tr.cover, sizes: "512x512" }] : [] });
      navigator.mediaSession.playbackState = st.playing ? "playing" : "paused";
    } catch (_) {  }
  }
  async function loadTrack(id, { at = 0, autoplay = true } = {}) {
    const a = ensureAudio();
    const generation = ++loadGeneration;
    const requested = Number(at);
    pendingSeek = { generation, at: Number.isFinite(requested) ? Math.max(0, requested) : 0 };
    if (st.mode !== "local") st.localSince = now();
    st.mode = "local"; st.trackId = id; playedFor = "";
    const track = await trackMeta(id);
    if (generation !== loadGeneration || st.mode !== "local") return false;
    st.track = track;
    if (!st.track) { st.playing = false; render(); return false; }
    st.duration = st.track.duration || 0;
    const url = await soft("stream_url", { id });
    if (!url || generation !== loadGeneration || st.mode !== "local") return false;
    sourceGeneration = generation;
    a.src = typeof url === "string" ? url : url.url || "";
    a.volume = Math.max(0, Math.min(1, st.volume * st.volume));
    st.dismissed = false;
    render(); session();
    if (autoplay) { try { await a.play(); } catch (_) { if (generation === loadGeneration && st.mode === "local") { st.playing = false; render(); } } } else { st.playing = false; render(); report(); }
    return true;
  }
  function play() { if (!audio || !audio.src) return st.trackId ? loadTrack(st.trackId, { at: st.position }) : null; return audio.play().catch(() => null); }
  function pause() { if (audio) audio.pause(); }
  async function next(auto) {
    if (!st.queue.length) { if (auto) { st.playing = false; render(); report(); } return; }
    if (auto && st.repeat === "one" && audio) { audio.currentTime = 0; play(); return; }
    let i = st.index + 1;

    if (i >= st.queue.length && st.repeat !== "all" && await libraryContinue(1)) i = st.index + 1;
    if (i >= st.queue.length) { if (st.repeat === "all" || !auto) i = 0; else { st.playing = false; render(); report(); return; } }
    st.index = i; loadTrack(st.queue[i]);
  }
  async function prev() {
    if (audio && audio.currentTime > 3) { audio.currentTime = 0; return; }
    if (st.index <= 0 && await libraryContinue(-1)) st.index = 1;
    if (st.index > 0) { st.index -= 1; loadTrack(st.queue[st.index]); } else if (audio) audio.currentTime = 0;
  }

  async function libraryContinue(dir) {
    const rows = ((await soft("library.list", { limit: 2000, sort: "added" })) || {}).tracks || [];
    const ids = rows.map((r) => String(r.id)), at = ids.indexOf(st.trackId);
    if (dir > 0) {
      const tail = (at >= 0 ? ids.slice(at + 1) : ids).filter((id) => !st.queue.includes(id)).slice(0, 200);
      if (!tail.length) return false;
      st.queue = st.queue.concat(tail);
      return true;
    }
    const before = at > 0 ? ids[at - 1] : "";
    if (!before || st.queue.includes(before)) return false;
    st.queue = [before].concat(st.queue); st.index += 1;
    return true;
  }

  async function takeOver(autoplay) {
    if (st.moduleOpen) return;
    const s = await soft("player.state", {});
    if (!s || !s.track_id) return;
    if (s.device === "phone" && s.playing) soft("player.command", { cmd: "handoff", target: "phone", by: "dock" });
    st.queue = Array.isArray(s.queue) && s.queue.length ? s.queue.map(String) : [s.track_id];
    st.index = Math.max(0, st.queue.indexOf(s.track_id));
    if (st.queue[st.index] !== s.track_id) { st.queue.splice(st.index, 0, s.track_id); }
    st.volume = typeof s.volume === "number" ? s.volume : st.volume;
    st.shuffle = !!s.shuffle; st.repeat = s.repeat || "off";
    st.context = (s.context && s.context.name) || "";
    if (s.track) meta.set(s.track_id, s.track);
    await loadTrack(s.track_id, { at: Number(s.position_now || s.position) || 0, autoplay });
  }
  function report(extra) {
    if (st.mode !== "local") return;
    clearTimeout(reportTimer);
    reportTimer = setTimeout(() => sendReport(extra), extra ? 0 : 400);
  }
  function sendReport(extra) {
    clearTimeout(reportTimer);
    if (st.mode !== "local" && !extra) return Promise.resolve();
    const body = Object.assign({ track_id: st.trackId, position: Math.round(position() * 10) / 10, playing: !!(audio && !audio.paused),
      queue: st.queue.slice(0, 1000), index: st.index, volume: st.volume, shuffle: st.shuffle, repeat: st.repeat,
      device: "pc", device_name: "MemuAI", duration: st.duration || 0, context: { name: st.context }, at: Date.now(),
      speed: (audio && audio.playbackRate) || 1 }, extra || {});
    const done = soft("player.update", body);
    if (body.playing) reportTimer = setTimeout(() => sendReport(), 15000);
    return done;
  }

  async function applyCommand(c) {
    const a = c.args || {};
    if (a.by === "dock") return;
    const target = String(c.target || "").toLowerCase();
    if (c.cmd === "handoff" && target === "dock") { if (st.mode === "local") { pause(); st.mode = "remote"; render(); } return; }
    if (st.mode !== "local" && !(c.cmd === "play_track" && canPlayHere())) return;
    if (!PC_TARGETS.includes(target)) return;
    switch (c.cmd) {
      case "play": return play();
      case "pause": case "stop": case "handoff": return pause();
      case "toggle": return toggle();
      case "next": return next(false);
      case "prev": return prev();
      case "seek": return seekTo(Number(a.position ?? a.value) || 0);
      case "volume": st.volume = Math.max(0, Math.min(1, Number(a.value ?? a.volume) || 0)); if (audio) audio.volume = st.volume * st.volume; return report();
      case "shuffle": st.shuffle = a.value === undefined ? !st.shuffle : !!a.value; return report();
      case "repeat": if (["off", "all", "one"].includes(a.value)) st.repeat = a.value; return report();
      case "play_track": case "set_queue": {
        const ids = (Array.isArray(a.queue) && a.queue.length ? a.queue : a.track_id ? [a.track_id] : []).map(String);
        if (!ids.length) return;
        st.queue = ids; st.index = Math.max(0, ids.indexOf(String(a.track_id || ids[0])));
        st.context = (a.context && a.context.name) || (c.from === "phone" ? L("С телефона", "From the phone") : "");
        return loadTrack(ids[st.index], { at: Number(a.position) || 0 });
      }
      default: return;
    }
  }

  function canPlayHere() { return !st.moduleOpen && !(st.deviceName === "MemuMusic" && st.playing && !stale()); }
  async function applyState(s) {
    if (!s) return;
    st.serverSkew = s.server_time ? now() - s.server_time : st.serverSkew;
    st.device = s.device || ""; st.deviceName = s.device_name || ""; st.updatedAt = s.updated_at || 0;
    if (st.mode === "local" && s.device_name === "MemuAI") return;
    if (st.mode === "local" && s.device_name && s.device_name !== "MemuAI" && s.playing && (s.updated_at || 0) + st.serverSkew > st.localSince + 0.5) {

      pause(); st.mode = "remote";
    }
    if (st.mode === "local") return;
    if (!s.track_id) { if (st.mode === "remote") { st.trackId = ""; render(); } return; }
    st.mode = "remote";
    const ex = st.expect;
    if (ex && now() < ex.until && !!s.playing !== ex.playing && !s.pending && (s.updated_at || 0) + st.serverSkew < ex.at) {
      s = Object.assign({}, s, { playing: ex.playing });
    } else if (!ex || now() >= ex.until || !!s.playing === ex.playing) st.expect = null;
    if (s.track_id !== st.trackId) { st.trackId = s.track_id; st.track = s.track || meta.get(s.track_id) || null; if (!st.track) trackMeta(s.track_id).then((tr) => { if (st.trackId === s.track_id) { st.track = tr; render(); } }); }
    if (s.playing && !st.playing) st.dismissed = false;
    st.playing = !!s.playing && !stale();
    st.position = Number(s.position_now ?? s.position) || 0; st.posAt = now();
    st.duration = Number(s.duration) || (st.track && st.track.duration) || 0;
    st.speed = Number(s.speed) || 1;
    st.queue = Array.isArray(s.queue) ? s.queue.map(String) : st.queue; st.index = Number(s.index ?? st.index);
    st.volume = typeof s.volume === "number" ? s.volume : st.volume;
    st.context = (s.context && s.context.name) || "";
    render();
  }
  function listen() {
    if (es || typeof EventSource === "undefined") return;
    es = window.MemuLive ? MemuLive.stream("/api/pg/music/player/events") : new EventSource("/api/pg/music/player/events");
    es.onmessage = (e) => {
      let data = null;
      try { data = JSON.parse(e.data); } catch (_) { return; }
      if (data.event === "command") applyCommand(data.command);
      else if (data.event === "state") applyState(data.state);
    };
  }

  async function onModules() {
    if (!st.booted) { if (musicEnabled() && moduleWindow()) boot(true); return; }
    if (!musicEnabled()) { if (st.mode === "local") pause(); st.trackId = ""; render(); if (es) { es.close(); es = null; } return; }
    listen();
    const ws = moduleWindow(), wasOpen = st.moduleOpen;
    st.moduleOpen = !!ws; st.moduleMinimized = !!(ws && ws.minimized);
    if (st.moduleOpen && !wasOpen && st.mode === "local") {

      const wasPlaying = !!(audio && !audio.paused), at = Math.round(position() * 10) / 10;
      st.mode = "remote";
      if (audio) audio.pause();
      await sendReport({ playing: false, position: at, context: { name: st.context, handoff: wasPlaying ? "play" : "load" } });
      st.position = at; st.posAt = now();
      st.playing = wasPlaying;
    } else if (!st.moduleOpen && wasOpen) {

      setTimeout(async () => {
        if (st.moduleOpen) return;
        const s = await soft("player.state", {});
        if (s && s.track_id && s.device_name === "MemuMusic") {
          if (s.playing) await takeOver(true);
          else { applyState(s); }
        }
      }, 400);
    }
    render();
  }

  async function boot(force) {
    if (st.booted) return;
    if (!musicEnabled()) return;
    const s = await soft("player.state", {});
    if (!force && !(s && s.track_id)) return;
    st.booted = true;
    mount();
    listen();
    const ws = moduleWindow();
    st.moduleOpen = !!ws; st.moduleMinimized = !!(ws && ws.minimized);
    if (s && s.track_id) {
      if (s.track) meta.set(s.track_id, s.track);
      st.mode = "remote";
      await applyState(s);
      if (!st.moduleOpen && !(s.playing && s.device_name !== "MemuAI")) { st.playing = false; st.position = Number(s.position) || 0; render(); }
    }
  }

  window.addEventListener("pagehide", () => {
    if (!st.booted || !moduleWindow() || st.deviceName !== "MemuMusic" || !st.playing) return;
    try {
      fetch("/api/pg/music/call", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ op: "player.update", args: { playing: false, device: "pc", device_name: "MemuMusic",
          track_id: st.trackId, position: Math.round(position() * 10) / 10, at: Date.now() } }) }).catch(() => {});
    } catch (_) {  }
  });
  document.addEventListener("memu-modules-changed", () => { onModules(); });
  document.addEventListener("memu-modint-changed", () => { announced.sig = ""; render(); });
  document.addEventListener("visibilitychange", () => { if (el && !el.hidden) { st.expanded && !document.hidden ? startClock() : stopClock(); if (!document.hidden) progress(); } });
  window.addEventListener("memu-language", relabel);
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1500));

  setTimeout(() => idle(() => boot(false), { timeout: 4000 }), 2500);

  window.MemuMusicDock = {
    state: () => Object.assign({}, st, { track: st.track && { id: st.track.id, title: st.track.title }, position: position(), audio: !!audio, paused: audio ? audio.paused : true }),
    toggle, open: openModule, expand, takeOver,

    boot: (force) => boot(!!force), apply: (c) => applyCommand(c || {}),

    control,
    nowPlaying: () => announced.now,
  };
})();
