/* MemuMusic · player.js — две «деки» <audio> (кроссфейд), граф WebAudio
 * (10-полосный эквалайзер → анализатор), очередь, shuffle/repeat, таймер сна,
 * Media Session. Граф создаётся лениво при первом воспроизведении. */
const EQ_BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
const EQ_PRESETS = {
  flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  bass: [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
  vocal: [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
  electronic: [5, 4, 1, 0, -2, 1, 0, 2, 4, 5],
  rock: [4, 3, -1, -2, -1, 1, 3, 4, 4, 4],
  acoustic: [3, 3, 2, 0, 1, 1, 2, 3, 2, 1],
  night: [2, 2, 1, 0, 0, -1, -2, -3, -4, -5],
};

const Player = (() => {
  const decks = [document.getElementById("deck-a"), document.getElementById("deck-b")];
  let active = 0;
  let ctx = null, analyser = null, master = null, deckGains = [], eqNodes = [];
  const listeners = {};
  const st = {
    queue: [], index: -1, order: [], shuffle: false, repeat: "off", volume: .8, muted: false, speed: 1,
    eq: EQ_PRESETS.flat.slice(), eqPreset: "flat", crossfade: 0, playing: false, from: "", sleepAt: 0, sleepEnd: false,
    ab: null,   // feat: A-B повтор {a, b|null}
  };
  /* feat: поведение из серверных настроек (settings.get) — выставляет feat-core.js через configure(). */
  const opts = { normalize: true, gapless: true, resume: true, resumeMin: 20 };
  const norm = [0, 0];            // громкость каждой деки, дБ (нормализация по измеренной громкости трека)
  let normNodes = [], preloaded = null, resumeSaved = 0;
  const dbToLin = (db) => Math.pow(10, (Number(db) || 0) / 20);
  const seekOnLoad = new Map();   // дека → позиция, на которую встать, когда придут метаданные
  let fading = false, sleepTimer = 0, playedReported = "", saveTimer = 0, noCors = false, wantPlay = false;
  const emit = (type, data) => (listeners[type] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });
  const on = (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); };
  const deck = () => decks[active];
  const current = () => st.queue[st.order[st.index]] || null;

  /* Граф WebAudio подключаем только к звуку, пришедшему с CORS: окно модуля — песочница с «чужим»
   * источником, и звук без CORS через граф стал бы тишиной. Без CORS играем напрямую (без EQ и визуализатора). */
  function ensureGraph() {
    if (ctx || noCors || !(window.AudioContext || window.webkitAudioContext)) return;
    if (!decks.some((a) => a.readyState >= 1 && a.hasAttribute("crossorigin"))) return;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      master = ctx.createGain();
      analyser = ctx.createAnalyser(); analyser.fftSize = 2048; analyser.smoothingTimeConstant = .82;
      let node = null;
      eqNodes = EQ_BANDS.map((freq, i) => {
        const f = ctx.createBiquadFilter();
        f.type = i === 0 ? "lowshelf" : i === EQ_BANDS.length - 1 ? "highshelf" : "peaking";
        f.frequency.value = freq; f.Q.value = 1.1; f.gain.value = st.eq[i] || 0;
        if (node) node.connect(f);
        node = f; return f;
      });
      node.connect(master); master.connect(analyser); analyser.connect(ctx.destination);
      normNodes = decks.map(() => ctx.createGain());
      deckGains = decks.map((audio, i) => {
        const g = ctx.createGain();
        ctx.createMediaElementSource(audio).connect(normNodes[i]); normNodes[i].connect(g); g.connect(eqNodes[0]);
        return g;
      });
      applyVolume();
      deckGains.forEach((g, i) => { g.gain.value = i === active ? 1 : 0; });
    } catch (error) {
      console.warn("[music] WebAudio unavailable", error);
      ctx = null; analyser = null;
    }
  }
  function applyVolume() {
    const v = st.muted ? 0 : st.volume;
    // С графом WebAudio нормализация — отдельный узел (можно и усилить тихий трек), без него — только приглушить громкий.
    decks.forEach((a, i) => { a.volume = Math.max(0, Math.min(1, v * v * (ctx ? 1 : Math.min(1, dbToLin(norm[i]))))); });
    normNodes.forEach((g, i) => { g.gain.value = Math.min(4, dbToLin(norm[i])); });
    emit("volume", st);
  }
  function setNorm(audio, track) {
    norm[decks.indexOf(audio)] = opts.normalize && track && typeof track.gain === "number" ? track.gain : 0;
    applyVolume();
  }
  function rebuildOrder(keepCurrent) {
    const cur = st.order[st.index];
    st.order = st.queue.map((_, i) => i);
    if (st.shuffle) {
      for (let i = st.order.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [st.order[i], st.order[j]] = [st.order[j], st.order[i]]; }
      if (keepCurrent && cur !== undefined) { st.order.splice(st.order.indexOf(cur), 1); st.order.unshift(cur); }
    }
    st.index = keepCurrent && cur !== undefined ? st.order.indexOf(cur) : st.index;
  }

  async function load(audio, track) {
    seekOnLoad.delete(audio);   // отложенная перемотка относится только к своему треку
    if (preloaded && decks[preloaded.deck] === audio && preloaded.id !== track.id) preloaded = null;
    setNorm(audio, track);
    const url = await API.call("stream_url", { id: track.id });
    audio.src = typeof url === "string" ? url : (url && url.url) || "";
    audio.playbackRate = st.speed; audio.preservesPitch = true;
  }
  const isLong = (track) => (track && track.duration || 0) >= opts.resumeMin * 60;
  /** feat: следующий трек очереди (с учётом «повторять всё») — для предзагрузки. */
  function nextTrack() {
    let i = st.index + 1;
    if (i >= st.order.length) { if (st.repeat !== "all") return null; i = 0; }
    return st.repeat === "one" ? null : st.queue[st.order[i]] || null;
  }
  async function preloadNext() {
    const track = nextTrack();
    if (!track || (preloaded && preloaded.id === track.id)) return;
    const idx = 1 - active;
    preloaded = { deck: idx, id: track.id };
    await load(decks[idx], track);
    decks[idx].preload = "auto";
  }
  /** A-B повтор: первое нажатие — точка A, второе — B, третье — выключить. */
  function setAB(mode) {
    const cur = deck().currentTime || 0;
    if (mode === "off" || (st.ab && st.ab.b != null && mode !== "a")) st.ab = null;
    else if (!st.ab || mode === "a") st.ab = { a: cur, b: null };
    else st.ab = cur > st.ab.a + .5 ? { a: st.ab.a, b: cur } : { a: cur, b: null };
    emit("ab", st);
  }
  function configure(cfg) {
    Object.assign(opts, cfg || {});
    decks.forEach((a) => { const tr = a === deck() ? current() : null; if (tr) setNorm(a, tr); });
  }
  async function startAt(index, { autoplay = true, position = 0, fade = false } = {}) {
    if (index < 0 || index >= st.order.length) return;
    st.index = index;
    const track = current();
    if (!track) return;
    playedReported = ""; resumeSaved = 0;
    if (st.ab) { st.ab = null; emit("ab", st); }
    if (!position && opts.resume && isLong(track) && track.resume_at > 15 && track.resume_at < (track.duration || 0) - 10) position = track.resume_at;
    emit("track", track);
    const prev = deck();
    if (!(fade && st.crossfade > 0 && ctx) && preloaded && preloaded.id === track.id && decks[preloaded.deck] !== prev) {
      // feat: бесшовный переход — следующий трек уже загружен во вторую деку, переключаемся без паузы на загрузку.
      const nextIdx = preloaded.deck; preloaded = null;
      prev.pause(); active = nextIdx;
      if (ctx) deckGains.forEach((g, i) => { g.gain.cancelScheduledValues(ctx.currentTime); g.gain.value = i === active ? 1 : 0; });
      setNorm(deck(), track);
      try { deck().currentTime = position || 0; } catch (e) { /* metadata not ready yet */ }
      if (autoplay) await play();
      updateSession(track); scheduleSave();
      return;
    }
    if (fade && st.crossfade > 0 && ctx) {
      fading = true;
      const nextIdx = 1 - active, next = decks[nextIdx];
      await load(next, track);
      const now = ctx.currentTime, dur = st.crossfade;
      deckGains[nextIdx].gain.cancelScheduledValues(now); deckGains[nextIdx].gain.setValueAtTime(0, now); deckGains[nextIdx].gain.linearRampToValueAtTime(1, now + dur);
      deckGains[active].gain.cancelScheduledValues(now); deckGains[active].gain.setValueAtTime(deckGains[active].gain.value, now); deckGains[active].gain.linearRampToValueAtTime(0, now + dur);
      active = nextIdx;
      await next.play().catch(() => {});
      setTimeout(() => { prev.pause(); fading = false; }, dur * 1000 + 50);
    } else {
      prev.pause();
      if (ctx) { deckGains.forEach((g, i) => { g.gain.cancelScheduledValues(ctx.currentTime); g.gain.value = i === active ? 1 : 0; }); }
      await load(prev, track);
      if (position) { seekOnLoad.set(prev, position); try { prev.currentTime = position; } catch (e) { /* metadata not ready yet */ } }
      if (autoplay) await play();
    }
    updateSession(track);
    scheduleSave();
  }
  async function play() {
    if (!current()) { if (st.queue.length) return startAt(0); return; }
    wantPlay = true;
    ensureGraph();
    if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {});
    if (!deck().src) return startAt(st.index);
    try { await deck().play(); } catch (error) { if (error && error.name !== "AbortError") emit("error", error); }
  }
  function pause() { wantPlay = false; deck().pause(); }
  function toggle() { st.playing ? pause() : play(); }
  /* r39-integ: the end of the queue is not the end of the music. A queue of one track (a track started from Quick Access /
     the AI, a handed-over or restored queue) made «next» restart the same song and the end of a track stop everything.
     Now the queue is continued by setContinue(fn): fn(current, +1|-1) → tracks to add after / before the current one
     (MemuMusic: radio when «auto radio» is on, otherwise the library in its order). */
  let continuation = null, extending = null;
  function setContinue(fn) { continuation = typeof fn === "function" ? fn : null; }
  async function extend(dir) {
    if (!continuation || !current()) return false;
    if (extending) return extending;
    extending = (async () => {
      let add = [];
      try { add = ((await continuation(current(), dir)) || []).filter((x) => x && x.id && !st.queue.some((q) => q.id === x.id)); } catch (e) { add = []; }
      if (!add.length) return false;
      const base = st.queue.length;
      st.queue.push(...add);
      const idx = add.map((_, k) => base + k);
      if (dir > 0) st.order.splice(st.index + 1, 0, ...idx);
      else { st.order.splice(st.index, 0, ...idx); st.index += idx.length; }
      emit("queue", st); scheduleSave();
      return true;
    })();
    try { return await extending; } finally { extending = null; }
  }
  async function next(auto) {
    if (!st.order.length) return;
    if (auto && st.repeat === "one") { deck().currentTime = 0; play(); return; }
    let i = st.index + 1;
    if (i >= st.order.length && st.repeat !== "all" && await extend(1)) i = st.index + 1;
    if (i >= st.order.length) { if (st.repeat === "all" || !auto) i = 0; else { pause(); emit("ended"); return; } }
    startAt(i, { fade: !!auto });
  }
  async function prev() {
    if (deck().currentTime > 3) { deck().currentTime = 0; return; }
    if (st.index <= 0 && !(await extend(-1))) { deck().currentTime = 0; return; }
    startAt(st.index - 1);
  }
  function seek(sec) { const a = deck(); if (isFinite(a.duration)) a.currentTime = Math.max(0, Math.min(a.duration - .1, sec)); emit("time", times()); }
  function times() { const a = deck(); return { cur: a.currentTime || 0, dur: isFinite(a.duration) ? a.duration : (current() && current().duration) || 0, buf: buffered(a) }; }
  /** r41-ui: the time the listener HEARS, for karaoke lyrics, on every animation frame. audio.currentTime is the decoder's
   * position: it may stand still between media clock updates (then we extrapolate from the last change with the playback
   * rate), and with the WebAudio graph (equalizer, crossfade, visualizer) the sound reaches the speakers
   * baseLatency + outputLatency later (10–40 ms, Bluetooth up to 200+ ms) — that is subtracted. */
  const clk = { m: -1, p: 0, a: null };
  function heard() {
    const a = deck(), m = a.currentTime || 0, now = performance.now();
    let pos = m;
    if (a.paused || a.seeking || a.readyState < 3 || clk.a !== a || Math.abs(m - clk.m) > .5 || m < clk.m) { clk.m = m; clk.p = now; clk.a = a; }
    else if (m !== clk.m) { clk.m = m; clk.p = now; }
    else pos = m + Math.min(.5, (now - clk.p) / 1000) * (a.playbackRate || 1);
    if (ctx && !a.paused) pos -= (ctx.baseLatency || 0) + (ctx.outputLatency || 0);
    return Math.max(0, pos);
  }
  function latency() { return ctx ? (ctx.baseLatency || 0) + (ctx.outputLatency || 0) : 0; }
  function buffered(a) { try { return a.buffered.length ? a.buffered.end(a.buffered.length - 1) : 0; } catch (e) { return 0; } }

  /** Играть список, начиная с трека `startId`. */
  function playList(tracks, startId, from, opts) {
    st.queue = tracks.slice(); st.from = from || "";
    rebuildOrder(false);
    let i = startId ? st.queue.findIndex((t) => t.id === startId) : 0;
    if (i < 0) i = 0;
    if (st.shuffle) { st.order.splice(st.order.indexOf(i), 1); st.order.unshift(i); i = 0; } else i = st.order.indexOf(i);
    startAt(i, opts);
    emit("queue", st);
  }
  function enqueue(tracks, nextUp) {
    const add = [].concat(tracks).filter(Boolean);
    if (!add.length) return;
    const base = st.queue.length;
    st.queue.push(...add);
    const idx = add.map((_, k) => base + k);
    if (nextUp && st.index >= 0) st.order.splice(st.index + 1, 0, ...idx); else st.order.push(...idx);
    if (st.index < 0) startAt(0);
    emit("queue", st); scheduleSave();
  }
  /** Порядок «далее» после перетаскивания: from/to — позиции в order. */
  function moveInQueue(from, to) {
    if (from === to || from <= st.index || to <= st.index) return;
    const [item] = st.order.splice(from, 1);
    st.order.splice(to > from ? to - 1 : to, 0, item);
    emit("queue", st); scheduleSave();
  }
  function removeFromQueue(pos) {
    if (pos === st.index) return;
    st.order.splice(pos, 1);
    if (pos < st.index) st.index--;
    emit("queue", st); scheduleSave();
  }
  function clearQueue() { st.order = st.index >= 0 ? [st.order[st.index]] : []; st.index = st.order.length ? 0 : -1; emit("queue", st); scheduleSave(); }
  function upcoming() { return st.order.slice(st.index + 1).map((i, k) => ({ track: st.queue[i], pos: st.index + 1 + k })); }

  function setShuffle(v) { st.shuffle = v === undefined ? !st.shuffle : !!v; rebuildOrder(true); emit("mode", st); emit("queue", st); scheduleSave(); }
  function cycleRepeat() { st.repeat = { off: "all", all: "one", one: "off" }[st.repeat]; emit("mode", st); scheduleSave(); }
  function setVolume(v) { st.volume = Math.max(0, Math.min(1, v)); st.muted = false; applyVolume(); scheduleSave(); }
  function toggleMute() { st.muted = !st.muted; applyVolume(); }
  function setSpeed(v) { st.speed = v; decks.forEach((a) => { a.playbackRate = v; a.preservesPitch = true; }); emit("mode", st); scheduleSave(); }
  function setEq(values, preset) {
    st.eq = values.slice(); st.eqPreset = preset || "custom";
    eqNodes.forEach((f, i) => { if (ctx) f.gain.setTargetAtTime(st.eq[i] || 0, ctx.currentTime, .05); });
    emit("mode", st); scheduleSave();
  }
  function setCrossfade(sec) { st.crossfade = Math.max(0, Math.min(12, sec)); scheduleSave(); }
  function setSleep(minutes, endOfTrack) {
    clearTimeout(sleepTimer); st.sleepAt = 0; st.sleepEnd = !!endOfTrack;
    if (minutes > 0) { st.sleepAt = Date.now() + minutes * 60000; sleepTimer = setTimeout(() => { fadeOutAndPause(); }, minutes * 60000); }
    emit("sleep", st);
  }
  function fadeOutAndPause() {
    st.sleepAt = 0; st.sleepEnd = false; emit("sleep", st);
    if (!ctx) { pause(); return; }
    const g = deckGains[active].gain, now = ctx.currentTime;
    g.setValueAtTime(g.value, now); g.linearRampToValueAtTime(0, now + 4);
    setTimeout(() => { pause(); g.setValueAtTime(1, ctx.currentTime); }, 4200);
  }

  decks.forEach((audio, i) => {
    audio.addEventListener("play", () => { if (i === active) { st.playing = true; emit("state", st); } });
    audio.addEventListener("pause", () => { if (i === active && !fading) { st.playing = false; emit("state", st); scheduleSave(); } });
    audio.addEventListener("ended", () => {
      if (i !== active) return;
      const done = current();
      if (done && isLong(done) && done.resume_at) { done.resume_at = 0; API.soft("library.resume", { id: done.id, position: 0 }); }
      if (st.sleepEnd) { st.sleepEnd = false; emit("sleep", st); st.playing = false; emit("state", st); return; }
      next(true);
    });
    audio.addEventListener("timeupdate", () => {
      if (i !== active) return;
      const t = times();
      emit("tick", t);
      const track = current();
      if (track && playedReported !== track.id && t.cur > Math.min(30, t.dur * .5)) { playedReported = track.id; API.soft("library.played", { id: track.id, seconds: Math.round(t.cur) }); }
      if (st.ab && st.ab.b != null && t.cur >= st.ab.b) { audio.currentTime = st.ab.a; return; }
      if (track && opts.resume && isLong(track) && Math.abs(t.cur - resumeSaved) >= 15) {
        resumeSaved = t.cur; track.resume_at = t.cur; API.soft("library.resume", { id: track.id, position: Math.floor(t.cur) });
      }
      if (opts.gapless && !(st.crossfade > 0 && ctx) && !st.sleepEnd && t.dur > 12 && t.dur - t.cur < 8 && !fading) preloadNext();
      if (st.crossfade > 0 && ctx && !fading && st.repeat !== "one" && !st.sleepEnd && t.dur > st.crossfade * 2 && t.dur - t.cur <= st.crossfade
          && (st.index + 1 < st.order.length || st.repeat === "all")) next(true);
      if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && t.dur) {
        try { navigator.mediaSession.setPositionState({ duration: t.dur, position: Math.min(t.cur, t.dur), playbackRate: st.speed }); } catch (e) { /* ignore */ }
      }
    });
    audio.addEventListener("loadedmetadata", () => {
      if (seekOnLoad.has(audio)) { try { audio.currentTime = seekOnLoad.get(audio); } catch (e) { /* not seekable */ } seekOnLoad.delete(audio); }
      if (i !== active) return;
      emit("tick", times());
      if (!ctx && st.playing) { ensureGraph(); if (ctx && ctx.state === "suspended") ctx.resume().catch(() => {}); }
    });
    audio.addEventListener("error", () => {
      if (i !== active || !audio.src) return;
      if (!ctx && !noCors && audio.hasAttribute("crossorigin") && /^https?:/i.test(audio.src)) {
        noCors = true;   // сервер не отдал CORS — переигрываем без него, звук идёт мимо графа
        console.warn("[music] stream without CORS: equalizer and visualizer are off");
        decks.forEach((a) => a.removeAttribute("crossorigin"));
        const src = audio.src, at = audio.currentTime || 0;
        audio.src = src; if (at) audio.addEventListener("loadedmetadata", () => { audio.currentTime = at; }, { once: true });
        if (wantPlay) audio.play().catch(() => {});
        return;
      }
      emit("error", audio.error);
    });
  });

  function updateSession(track) {
    if (!("mediaSession" in navigator) || !track) return;
    try {
      navigator.mediaSession.metadata = new MediaMetadata({ title: track.title, artist: track.artist, album: track.album || "",
        artwork: [{ src: track.cover || coverArt(track.id, "", track.title), sizes: "512x512" }] });
    } catch (e) { /* MediaMetadata is optional */ }
  }
  if ("mediaSession" in navigator) {
    const set = (name, fn) => { try { navigator.mediaSession.setActionHandler(name, fn); } catch (e) { /* unsupported action */ } };
    set("play", play); set("pause", pause); set("previoustrack", prev); set("nexttrack", () => next(false));
    set("seekto", (d) => seek(d.seekTime)); set("seekbackward", () => seek(deck().currentTime - 10)); set("seekforward", () => seek(deck().currentTime + 10));
  }

  /* Состояние плеера переживает закрытие окна: очередь (до 300), позиция, режимы. */
  function snapshot() {
    const ids = st.order.map((i) => st.queue[i]).filter(Boolean).slice(0, 300);
    return { queue: ids, index: Math.max(0, Math.min(st.index, ids.length - 1)), position: Math.floor(deck().currentTime || 0), from: st.from,
      volume: st.volume, shuffle: st.shuffle, repeat: st.repeat, speed: st.speed, eq: st.eq, eqPreset: st.eqPreset, crossfade: st.crossfade };
  }
  function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(() => { try { Memu.save({ player: snapshot() }); } catch (e) { /* storage is optional */ } }, 1200); }
  function restore(saved) {
    if (!saved || typeof saved !== "object") return;
    st.volume = typeof saved.volume === "number" ? saved.volume : st.volume;
    st.repeat = ["off", "all", "one"].includes(saved.repeat) ? saved.repeat : "off";
    st.speed = Number(saved.speed) || 1; st.crossfade = Number(saved.crossfade) || 0;
    if (Array.isArray(saved.eq) && saved.eq.length === EQ_BANDS.length) { st.eq = saved.eq.map(Number); st.eqPreset = saved.eqPreset || "custom"; }
    applyVolume();
    if (Array.isArray(saved.queue) && saved.queue.length) {
      st.queue = saved.queue.filter((t) => t && t.id); st.order = st.queue.map((_, i) => i); st.from = saved.from || "";
      st.shuffle = !!saved.shuffle;
      st.index = Math.min(saved.index || 0, st.queue.length - 1);
      startAt(st.index, { autoplay: false, position: saved.position || 0 });
    }
    emit("mode", st); emit("queue", st);
  }
  applyVolume();

  return { st, on, play, pause, toggle, next: () => next(false), prev, seek, setContinue, times, heard, latency, playList, enqueue, moveInQueue, removeFromQueue, clearQueue,
    upcoming, setShuffle, cycleRepeat, setVolume, toggleMute, setSpeed, setEq, setCrossfade, setSleep, restore, current, setAB, configure,
    analyser: () => analyser, audio: deck, snapshot };
})();
