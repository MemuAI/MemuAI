

(function () {
  "use strict";
  (window.MemuTgPlugins = window.MemuTgPlugins || []).push(function (kit) {
    const { S, $, $$, esc, tr, icon, ICONS, haptic, toast, api, openSheet, openStack, actionSheet, promptSheet, confirmDialog } = kit;
    const P = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;
    const F = (d) => `<svg viewBox="0 0 24 24"><path fill="currentColor" d="${d}"/></svg>`;
    Object.assign(ICONS, {
      music: P('<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>'),
      mmPlay: F("M8 5.1v13.8a1 1 0 0 0 1.5.9l11-6.9a1 1 0 0 0 0-1.8l-11-6.9A1 1 0 0 0 8 5.1z"),
      mmPause: F("M7 4.5h3.2a.8.8 0 0 1 .8.8v13.4a.8.8 0 0 1-.8.8H7a.8.8 0 0 1-.8-.8V5.3a.8.8 0 0 1 .8-.8zm6.8 0H17a.8.8 0 0 1 .8.8v13.4a.8.8 0 0 1-.8.8h-3.2a.8.8 0 0 1-.8-.8V5.3a.8.8 0 0 1 .8-.8z"),
      mmNext: F("M3.5 6.6v10.8a1 1 0 0 0 1.5.86L12 14v3.4a1 1 0 0 0 1.5.86l8.2-5.4a1 1 0 0 0 0-1.72l-8.2-5.4A1 1 0 0 0 12 6.6V10L5 5.74a1 1 0 0 0-1.5.86z"),
      mmPrev: F("M20.5 6.6v10.8a1 1 0 0 1-1.5.86L12 14v3.4a1 1 0 0 1-1.5.86l-8.2-5.4a1 1 0 0 1 0-1.72l8.2-5.4A1 1 0 0 1 12 6.6V10l7-4.26a1 1 0 0 1 1.5.86z"),
      heart: P('<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"/>'),
      heartFill: F("M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z"),
      shuffle: P('<path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>'),
      repeat: P('<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>'),
      repeatOne: P('<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><path d="M11 10h1v4"/>'),
      lyrics: P('<path d="M4 6h10M4 12h16M4 18h12"/>'),
      queue: P('<path d="M3 6h12M3 12h12M3 18h8"/><path d="M17 15v6l5-3z" fill="currentColor"/>'),
      devices: P('<rect x="2" y="4" width="14" height="10" rx="2"/><path d="M6 18h6"/><rect x="17" y="8" width="5" height="12" rx="1.5"/>'),
      phone: P('<rect x="6" y="2.5" width="12" height="19" rx="3"/><path d="M11 18.5h2"/>'),
      link: P('<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>'),
      volume: P('<path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>'),
      chevDown: P('<path d="M6 9l6 6 6-6"/>', 'stroke-width="2.4"'),
      cloudOff: P('<path d="M2 2l20 20M7 7a6 6 0 0 0-1 11h11M22 17a4.5 4.5 0 0 0-5.5-6.7A6 6 0 0 0 10 6.3"/>'),
      saved: P('<circle cx="12" cy="12" r="9"/><path d="M12 7v8M8.5 11.5L12 15l3.5-3.5"/>'),
    });

    const M = {
      sub: "home", home: null, lib: [], libTotal: 0, libQ: "", libSort: "added", libFilter: "", playlists: [],
      queue: [], index: -1, shuffle: false, repeat: "off", mode: "phone",
      pc: null, audio: null, lyrics: null, lyricsFor: "", full: null, offline: {}, pending: { likes: [], plays: [], playlist_adds: [] },
      playStart: 0, playedFor: "", downloading: {}, jobs: [], light: false, libView: "tracks", pcId: "", stable: null,
    };
    const LS = { state: "memu_music_state", offline: "memu_music_offline", pending: "memu_music_pending" };
    const CACHE = "memu-music-v1";
    const reduced = () => M.light || matchMedia("(prefers-reduced-motion: reduce)").matches;
    const load = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key) || "") || fallback; } catch (e) { return fallback; } };
    const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) {  } };
    M.offline = load(LS.offline, {});
    M.pending = Object.assign({ likes: [], plays: [], playlist_adds: [] }, load(LS.pending, {}));
    M.light = !!load("memu_music_light", false);
    M.pcId = load("memu_music_pc", "");

    const fmt = (sec) => { const s = Math.max(0, Math.floor(+sec || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
    const bytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(1)} ${tr("ГБ", "GB")}` : n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e8 ? 0 : 1)} ${tr("МБ", "MB")}` : `${Math.round(n / 1e3)} ${tr("КБ", "KB")}`;
    const plural = (n, one, few, many) => { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many; };
    const tracksWord = (n) => S.lang === "en" ? `${n} ${n === 1 ? "track" : "tracks"}` : `${n} ${plural(n, "трек", "трека", "треков")}`;
    const current = () => M.queue[M.index] || null;
    const byId = new Map();
    const remember = (rows) => { (rows || []).forEach((t) => { if (t && t.id) byId.set(t.id, Object.assign(byId.get(t.id) || {}, t)); }); return rows || []; };
    const hue = (text) => { let h = 0; for (const c of String(text || "")) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
    const accentOf = (t) => (t && /^#[0-9a-f]{6}$/i.test(t.accent || "") ? t.accent : `hsl(${hue((t && (t.album || t.artist || t.title)) || "")} 55% 52%)`);

    const LOOK_ACCENTS = { cover: ["#7c5cff", "#ff5c8a"], midnight: ["#7c5cff", "#ff5c8a"], aurora: ["#22d39b", "#4f8cff"], sunset: ["#ff7a45", "#ff3d77"],
      ocean: ["#1fb2ff", "#3a6bff"], neon: ["#ff2bd6", "#00d9ff"], sakura: ["#ff7aa8", "#b77bff"], moss: ["#7fb54a", "#d0b44c"], sand: ["#c27a34", "#9f4f2c"],
      graphite: ["#e4e4e7", "#a1a1aa"], vinyl: ["#e2523b", "#efa13a"] };
    M.look = load("memu_music_look", {});
    function paintLook() {
      const look = M.look || {}, html = document.documentElement, pair = LOOK_ACCENTS[look.theme];
      if (!look.accent && !pair) { html.classList.remove("mm-look"); return; }
      const light = html.dataset.scheme === "light", t = nowTrack();
      let a1 = look.accent || (pair && pair[0]), a2 = (pair && pair[1]) || a1;
      if (!look.accent && (look.cover || look.theme === "cover") && t && look.theme !== "graphite") a1 = accentOf(t);
      if (look.theme === "graphite" && !look.accent && light) { a1 = "#27272a"; a2 = "#52525b"; }
      const rgb = /^#[0-9a-f]{6}$/i.test(a1) ? [1, 3, 5].map((i) => parseInt(a1.slice(i, i + 2), 16)) : null;
      const bright = rgb ? (rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114) > 150 : false;
      html.style.setProperty("--mm-a1", a1); html.style.setProperty("--mm-a2", a2);
      html.style.setProperty("--mm-on", bright && !light ? "#141416" : "#fff");
      html.classList.add("mm-look");
    }
    function applyLook(look) {
      if (!look || typeof look !== "object") return;
      M.look = { theme: String(look.theme || ""), accent: /^#[0-9a-f]{6}$/i.test(look.accent || "") ? look.accent : "", cover: look.cover ? "1" : "" };
      save("memu_music_look", M.look);
      paintLook();
    }
    paintLook();

    function art(t, cls = "") {
      const style = `--mm-tint:${accentOf(t)}`;
      const letter = esc(((t && (t.title || t.name)) || "♪").trim().charAt(0).toUpperCase() || "♪");
      const src = t && (offlineCover(t.id) || t.cover);
      return `<span class="mm-art ${cls}" style="${style}"><i class="mm-art-ph" aria-hidden="true">${letter}</i>${src ? `<img loading="lazy" decoding="async" alt="" src="${esc(src)}">` : ""}</span>`;
    }
    function mosaic(p, cls = "") {
      const covers = (p.covers && p.covers.length ? p.covers : (p.cover ? [p.cover] : [])).slice(0, 4);
      const style = `--mm-tint:hsl(${hue(p.name)} 50% 50%)`;
      if (covers.length >= 4) return `<span class="mm-art mm-mosaic ${cls}" style="${style}">${covers.map((c) => `<img loading="lazy" alt="" src="${esc(c)}">`).join("")}</span>`;
      return `<span class="mm-art ${cls}" style="${style}"><i class="mm-art-ph">${p.smart ? icon("sparkle") : icon("music")}</i>${covers[0] ? `<img loading="lazy" alt="" src="${esc(covers[0])}">` : ""}</span>`;
    }

    document.addEventListener("error", (e) => { const el = e.target; if (el && el.tagName === "IMG" && el.closest(".mm-art")) el.remove(); }, true);

    const OFF_BASE = "https://memu-music.offline/";
    const pcOf = (id) => (M.offline[id] && M.offline[id].pc) || M.pcId || "pc";
    const offKey = (id, pc = pcOf(id)) => `${OFF_BASE}${pc}/track/${encodeURIComponent(id)}`;
    const offCoverKey = (id, pc = pcOf(id)) => `${OFF_BASE}${pc}/cover/${encodeURIComponent(id)}`;
    const legacyKeys = (id) => [`music/offline/${encodeURIComponent(id)}`, `music/offline-cover/${encodeURIComponent(id)}`];
    function setPcId(id) {
      if (!id || id === M.pcId) return;
      M.pcId = id; save("memu_music_pc", id); M.stable = null;
      migrateOffline();
    }
    async function migrateOffline() {
      const old = Object.keys(M.offline).filter((id) => !M.offline[id].pc);
      if (!old.length || !M.pcId || !("caches" in window)) return;
      try {
        const cache = await caches.open(CACHE);
        for (const id of old) {
          const [track, cover] = legacyKeys(id);
          const a = await cache.match(track); if (a) { await cache.put(offKey(id, M.pcId), a); await cache.delete(track); }
          const c = await cache.match(cover); if (c) { await cache.put(offCoverKey(id, M.pcId), c); await cache.delete(cover); }
          M.offline[id].pc = M.pcId;
        }
        save(LS.offline, M.offline); stableSave();
      } catch (e) {  }
    }

    const STABLE_KEY = "memu_music_saved";
    const tgStore = (kind) => { const tg = S.tg; try { return tg && tg[kind] && tg.isVersionAtLeast(kind === "DeviceStorage" ? "9.0" : "6.9") ? tg[kind] : null; } catch (e) { return null; } };
    const storeGet = (store, key) => new Promise((resolve) => { try { store.getItem(key, (err, value) => resolve(err ? "" : value || "")); } catch (e) { resolve(""); } setTimeout(() => resolve(""), 1500); });
    let stableTimer = null;
    function stableSave() {
      clearTimeout(stableTimer);
      stableTimer = setTimeout(async () => {
        const mine = Object.assign({}, await stableLoad());
        Object.entries(M.offline).forEach(([id, t]) => { if (!M.pcId || t.pc === M.pcId) mine[id] = { title: t.title, artist: t.artist, duration: t.duration, size: t.size }; });
        M.stable = mine;
        const key = M.pcId || "pc"; const dev = tgStore("DeviceStorage"), cloud = tgStore("CloudStorage");
        try {
          if (dev) dev.setItem(`${STABLE_KEY}_${key}`, JSON.stringify(mine), () => {});
          else if (cloud) {
            const ids = Object.keys(mine); while (ids.join(",").length > 4000) ids.pop();
            cloud.setItem(`mm_saved_${key}`, ids.join(","), () => {});
          }
        } catch (e) {  }
      }, 400);
    }
    async function stableLoad() {
      if (M.stable) return M.stable;
      const key = M.pcId || "pc"; let out = {};
      const dev = tgStore("DeviceStorage"), cloud = tgStore("CloudStorage");
      try {
        if (dev) out = JSON.parse((await storeGet(dev, `${STABLE_KEY}_${key}`)) || "{}") || {};
        else if (cloud) (await storeGet(cloud, `mm_saved_${key}`)).split(",").filter(Boolean).forEach((id) => { out[id] = {}; });
      } catch (e) { out = {}; }
      M.stable = out;
      return out;
    }
    async function lostTracks() {
      const stable = await stableLoad();
      return Object.keys(stable).filter((id) => !M.offline[id]);
    }
    const coverUrls = {};
    function offlineCover(id) { return coverUrls[id] || ""; }
    async function warmOfflineCovers() {
      if (!("caches" in window)) return;
      try {
        const cache = await caches.open(CACHE);
        await Promise.all(Object.keys(M.offline).map(async (id) => {
          if (coverUrls[id]) return;
          const hit = await cache.match(offCoverKey(id));
          if (hit) coverUrls[id] = URL.createObjectURL(await hit.blob());
        }));
      } catch (e) {  }
    }
    async function saveOffline(t, onProgress) {
      if (!("caches" in window)) throw new Error(tr("Этот Telegram не даёт сохранять файлы", "This Telegram cannot store files"));
      if (M.downloading[t.id]) return;
      M.downloading[t.id] = { done: 0, total: 0 };
      try {
        const response = await fetch(t.stream);
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        const total = +response.headers.get("content-length") || 0;
        const reader = response.body.getReader(); const parts = []; let done = 0, last = 0;
        for (;;) {
          const { value, done: end } = await reader.read();
          if (end) break;
          parts.push(value); done += value.length; M.downloading[t.id] = { done, total };
          const now = performance.now(); if (now - last > 150) { last = now; onProgress && onProgress(total ? done / total : 0); }
        }
        const type = response.headers.get("content-type") || "audio/mp4";
        const blob = new Blob(parts, { type });
        const cache = await caches.open(CACHE); const pc = M.pcId || "pc";
        await cache.put(offKey(t.id, pc), new Response(blob, { headers: { "Content-Type": type, "Content-Length": String(blob.size) } }));
        if (t.cover && !/^data:/.test(t.cover)) {
          try { const c = await fetch(t.cover); if (c.ok) { const cb = await c.blob(); await cache.put(offCoverKey(t.id, pc), new Response(cb)); coverUrls[t.id] = URL.createObjectURL(cb); } } catch (e) {  }
        }
        const meta = Object.assign({}, t, { size: blob.size, saved_at: Date.now(), pc }); delete meta.stream; delete meta.cover;
        M.offline[t.id] = meta; save(LS.offline, M.offline); stableSave();
        try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (e) {  }
      } finally { delete M.downloading[t.id]; }
    }
    async function dropOffline(id) {
      try { const cache = await caches.open(CACHE); await cache.delete(offKey(id)); await cache.delete(offCoverKey(id)); } catch (e) {  }
      if (coverUrls[id]) { URL.revokeObjectURL(coverUrls[id]); delete coverUrls[id]; }
      delete M.offline[id]; save(LS.offline, M.offline);
      delete (await stableLoad())[id];
      stableSave();
    }
    async function offlineBlobUrl(id) {
      try { const cache = await caches.open(CACHE); const hit = await cache.match(offKey(id)); if (hit) return URL.createObjectURL(await hit.blob()); } catch (e) {  }
      return "";
    }
    async function storageInfo() {
      const used = Object.values(M.offline).reduce((a, t) => a + (+t.size || 0), 0);
      let quota = 0;
      try { if (navigator.storage && navigator.storage.estimate) quota = (await navigator.storage.estimate()).quota || 0; } catch (e) {  }
      return { used, quota, count: Object.keys(M.offline).length };
    }
    async function downloadMany(list, label) {
      const todo = list.filter((t) => !M.offline[t.id]);
      if (!todo.length) { toast(tr("Уже на телефоне", "Already on the phone"), "ok"); return; }
      toast(tr(`Скачиваю на телефон: ${tracksWord(todo.length)}`, `Saving to the phone: ${tracksWord(todo.length)}`));
      let ok = 0;
      for (const t of todo) {
        try { await saveOffline(t, () => paintProgress(t.id)); ok++; paintRow(t.id); } catch (e) { toast(`${t.title}: ${e.message}`, "err"); break; }
      }
      if (ok) { haptic("success"); toast(tr(`На телефоне: ${tracksWord(ok)}${label ? ` · ${label}` : ""}`, `On the phone: ${tracksWord(ok)}${label ? ` · ${label}` : ""}`), "ok"); }
      if (M.sub === "device") drawSub();
    }
    function paintProgress(id) {
      const d = M.downloading[id]; const p = d && d.total ? d.done / d.total : 0;
      $$(`[data-track="${CSS.escape(id)}"] .mm-dl`).forEach((el) => { el.classList.add("busy"); el.style.setProperty("--p", p); });
    }
    function paintRow(id) {
      $$(`[data-track="${CSS.escape(id)}"] .mm-dl`).forEach((el) => { el.classList.remove("busy"); el.classList.toggle("on", !!M.offline[id]); });
    }

    let flushing = false;
    async function flush() {
      const p = M.pending;
      if (flushing || !(p.likes.length || p.plays.length || p.playlist_adds.length)) return;
      flushing = true;
      try {
        await api("music.sync", p);
        M.pending = { likes: [], plays: [], playlist_adds: [] }; save(LS.pending, M.pending);
      } catch (e) {  } finally { flushing = false; }
    }
    function queueSync(kind, row) { M.pending[kind].push(row); if (M.pending[kind].length > 500) M.pending[kind].shift(); save(LS.pending, M.pending); flush(); }
    async function setLike(t, liked) {
      t.liked = liked; remember([t]); haptic(liked ? "success" : "light");
      if (M.offline[t.id]) { M.offline[t.id].liked = liked; save(LS.offline, M.offline); }
      $$(`[data-like="${CSS.escape(t.id)}"]`).forEach((b) => { b.classList.toggle("on", liked); b.innerHTML = icon(liked ? "heartFill" : "heart"); });
      try { await api("music.like", { id: t.id, liked }); } catch (e) { queueSync("likes", { id: t.id, liked, at: Date.now() / 1000 }); }
    }

    function audio() {
      if (M.audio) return M.audio;
      const a = new Audio(); a.preload = "auto"; a.crossOrigin = "anonymous";
      a.addEventListener("timeupdate", onTime);
      a.addEventListener("play", () => { paintPlaying(); mediaSession(); report(true); });
      a.addEventListener("pause", () => { paintPlaying(); persist(); report(true); });
      a.addEventListener("seeked", () => report(true));
      a.addEventListener("ended", () => { countPlay(true); if (M.repeat === "one") { a.currentTime = 0; a.play(); } else next(true); });
      a.addEventListener("loadedmetadata", () => { paintTime(); mediaPosition(); });
      a.addEventListener("waiting", () => document.documentElement.classList.add("mm-buffering"));
      a.addEventListener("playing", () => document.documentElement.classList.remove("mm-buffering"));
      a.addEventListener("error", () => { if (current()) toast(tr("Не удалось открыть трек — компьютер недоступен?", "Could not open the track — is the computer offline?"), "err"); paintPlaying(); });
      M.audio = a;
      return a;
    }
    let lastPersist = 0;
    function onTime() {
      paintTime(); lyricTick(); if (!lyricRaf) lyricLoop();
      const now = Date.now(); if (now - lastPersist > 5000) { lastPersist = now; persist(); mediaPosition(); report(false); }
    }

    let lastReport = 0;
    function reportBody(playing) {
      const t = current(); const a = M.audio;
      const lo = Math.max(0, M.index - 50);
      return { track_id: t.id, position: a.currentTime || 0, playing, duration: isFinite(a.duration) ? a.duration : (t.duration || 0),
        queue: M.queue.slice(lo, lo + 200).map((x) => x.id), index: M.index - lo };
    }
    function report(force) {
      const t = current(); const a = M.audio;
      if (!t || !a || M.mode !== "phone" || S.mmOffline) return;
      const now = Date.now(); if (!force && now - lastReport < 30000) return;
      lastReport = now;
      api("music.report", reportBody(!a.paused)).catch(() => {  });
    }
    function persist() {
      const t = current(); if (!t || M.mode !== "phone") return;
      save(LS.state, { queue: M.queue.slice(0, 300).map(slim), index: M.index, position: M.audio ? M.audio.currentTime : 0, shuffle: M.shuffle, repeat: M.repeat, at: Date.now() });
    }
    const slim = (t) => ({ id: t.id, title: t.title, artist: t.artist, album: t.album, duration: t.duration, cover: t.cover, stream: t.stream, accent: t.accent, liked: t.liked });
    function countPlay(force) {
      const t = current(); const a = M.audio;
      if (!t || !a || M.playedFor === t.id) return;
      if (force || a.currentTime > Math.min(30, (t.duration || 60) / 2)) {
        M.playedFor = t.id;
        queueSync("plays", { id: t.id, seconds: Math.round(a.currentTime), at: Date.now() / 1000 });
      }
    }
    async function playQueue(list, index = 0, { position = 0, shuffle } = {}) {
      if (!list.length) return;
      if (M.mode === "pc") { pcCommand("play_track", { track_id: list[index].id, queue: list.map((t) => t.id) }); return; }
      M.queue = list.slice(); M.index = index;
      if (shuffle !== undefined) M.shuffle = shuffle;
      if (M.shuffle) { const first = M.queue.splice(index, 1)[0]; M.queue.sort(() => Math.random() - 0.5); M.queue.unshift(first); M.index = 0; }
      await loadCurrent(position, true);
    }
    async function loadCurrent(position = 0, autoplay = true) {
      const t = current(); if (!t) return;
      const a = audio(); M.playedFor = ""; M.lyrics = null;
      const off = M.offline[t.id] ? await offlineBlobUrl(t.id) : "";
      if (a._blob) { URL.revokeObjectURL(a._blob); a._blob = ""; }
      if (off) a._blob = off;
      a.src = off || t.stream;
      if (position) a.addEventListener("loadedmetadata", () => { try { a.currentTime = position; } catch (e) {  } }, { once: true });
      paintNow(); mediaSession();
      if (autoplay) { try { await a.play(); } catch (e) { paintPlaying(); } }
      persist();
      if (M.full) loadLyrics();
    }
    function toggle() {
      if (M.mode === "pc") { pcCommand("toggle"); return; }
      const a = audio(); if (!current()) return;
      haptic("light");
      if (a.paused) a.play().catch(() => {}); else a.pause();
    }
    function next(auto = false) {
      if (M.mode === "pc") { pcCommand("next"); return; }
      countPlay(false);
      if (M.index + 1 >= M.queue.length) { if (M.repeat === "all" && M.queue.length) M.index = 0; else { if (auto) { audio().pause(); } return; } }
      else M.index++;
      if (!auto) haptic("light");
      loadCurrent(0, true);
    }
    function prev() {
      if (M.mode === "pc") { pcCommand("prev"); return; }
      const a = audio();
      haptic("light");
      if (a.currentTime > 3 || M.index <= 0) { a.currentTime = 0; return; }
      M.index--; loadCurrent(0, true);
    }
    function seek(sec) {
      if (M.mode === "pc") { pcCommand("seek", { position: sec }); if (M.pc) M.pc.position = sec; paintTime(); return; }
      const a = audio(); if (isFinite(sec)) a.currentTime = sec;
    }
    function playNext(t) { if (M.index < 0) { playQueue([t]); return; } M.queue.splice(M.index + 1, 0, t); toast(tr("Сыграет следующим", "Plays next"), "ok"); }
    function addToQueue(t) { if (M.index < 0) { playQueue([t]); return; } M.queue.push(t); toast(tr("Добавлено в очередь", "Added to the queue"), "ok"); }

    function mediaSession() {
      const t = current(); const ms = navigator.mediaSession;
      if (!ms || !t || M.mode !== "phone") return;
      try {
        const cover = offlineCover(t.id) || t.cover;
        ms.metadata = new MediaMetadata({ title: t.title || "", artist: t.artist || "", album: t.album || "MemuMusic",
          artwork: cover ? [{ src: new URL(cover, location.href).href, sizes: "512x512" }] : [] });
        ms.playbackState = M.audio && !M.audio.paused ? "playing" : "paused";
        if (!ms._memu) {
          ms._memu = true;
          const set = (k, fn) => { try { ms.setActionHandler(k, fn); } catch (e) {  } };
          set("play", () => audio().play()); set("pause", () => audio().pause());
          set("previoustrack", prev); set("nexttrack", () => next());
          set("seekto", (d) => seek(d.seekTime)); set("seekbackward", () => seek(audio().currentTime - 10)); set("seekforward", () => seek(audio().currentTime + 10));
        }
      } catch (e) {  }
    }
    function mediaPosition() {
      const a = M.audio, ms = navigator.mediaSession;
      if (!ms || !a || !isFinite(a.duration) || !ms.setPositionState) return;
      try { ms.setPositionState({ duration: a.duration, position: Math.min(a.currentTime, a.duration), playbackRate: 1 }); } catch (e) {  }
    }

    let pcTimer = null;
    async function pcRefresh() {
      try { M.pc = await api("music.remote", {}); } catch (e) { M.pc = { online: false, available: false }; }
      M.pc.at = Date.now();
      if (M.mode === "pc") { paintNow(); paintPlaying(); paintTime(); }
      paintDeviceBadge();
      return M.pc;
    }
    function pcPolling(on) {
      clearInterval(pcTimer); pcTimer = null;
      if (on) pcTimer = setInterval(() => { if (!document.hidden && (M.mode === "pc" || M.full || (M.sub === "home" && S.tab === "music"))) pcRefresh(); }, 2500);
    }
    async function pcCommand(cmd, extra = {}) {
      haptic("light");
      try { M.pc = Object.assign(await api("music.remote", Object.assign({ cmd }, extra)), { at: Date.now() }); paintNow(); paintPlaying(); paintTime(); }
      catch (e) { toast(e.message, "err"); }
    }
    function pcPosition() {
      const pc = M.pc || {};
      return Math.min(+pc.duration || +(pc.track || {}).duration || 0, (+pc.position || 0) + (pc.playing ? (Date.now() - (pc.at || Date.now())) / 1000 * (+pc.speed || 1) : 0));
    }
    function setMode(mode) {
      if (mode === M.mode) return;
      M.mode = mode;
      if (mode === "pc" && M.audio && !M.audio.paused) M.audio.pause();
      document.documentElement.dataset.mmMode = mode;
      pcPolling(true); if (mode === "pc") pcRefresh();
      paintNow(); paintPlaying(); paintTime();
    }
    async function takeFromPc() {
      try {
        const st = await api("music.handoff", { to: "phone" });
        const queue = remember((st.queue && st.queue.length ? st.queue : [st.track]).filter(Boolean));
        let index = Math.max(0, queue.findIndex((t) => t.id === st.track.id));
        M.mode = "phone"; document.documentElement.dataset.mmMode = "phone";
        M.queue = queue; M.index = index;
        await loadCurrent(+st.position || 0, true);
        haptic("success"); toast(tr("Играет на телефоне", "Playing on the phone"), "ok");
      } catch (e) { toast(e.message, "err"); }
    }
    async function sendToPc() {
      const t = current(); if (!t) return;
      try {
        await api("music.handoff", { to: "pc", track_id: t.id, position: M.audio ? M.audio.currentTime : 0, queue: M.queue.map((x) => x.id) });
        if (M.audio) M.audio.pause();
        setMode("pc"); haptic("success"); toast(tr("Играет на компьютере", "Playing on the computer"), "ok");
      } catch (e) { toast(e.message, "err"); }
    }
    function devicesSheet() {
      const pc = M.pc || {};
      const pcTrack = pc.track ? `${pc.track.title} · ${pc.track.artist || ""}` : tr("ничего не играет", "nothing playing");
      const items = [
        { icon: "phone", label: tr("Этот телефон", "This phone"), sub: tr("Звук из динамика или наушников", "Sound from the speaker or headphones"), checked: M.mode === "phone", run: () => setMode("phone") },
        { icon: "monitor", label: tr("Компьютер · пульт", "Computer · remote"), sub: pc.online ? pcTrack : tr("MemuMusic на компьютере закрыт", "MemuMusic is closed on the computer"), checked: M.mode === "pc", run: () => setMode("pc") },
      ];
      if (pc.online && pc.track && M.mode === "phone") items.push({ icon: "download", label: tr("Продолжить на телефоне", "Continue on the phone"), sub: tr("Забрать трек и позицию с компьютера", "Take the track and position from the computer"), run: takeFromPc });
      if (current() && M.mode === "phone") items.push({ icon: "monitor", label: tr("Продолжить на компьютере", "Continue on the computer"), sub: tr("Компьютер продолжит с этого места", "The computer goes on from this point"), run: sendToPc });
      actionSheet(tr("Где играть", "Play on"), items);
      pcRefresh();
    }
    function paintDeviceBadge() {
      $$(".mm-dev-btn").forEach((b) => b.classList.toggle("live", !!(M.pc && M.pc.online && M.pc.playing)));
      const card = $("#mm-pc-card"); if (card && M.sub === "home") card.outerHTML = pcCardHtml();
      bindPcCard();
    }

    function nowTrack() { return M.mode === "pc" ? (M.pc && M.pc.track) || null : current(); }
    function isPlaying() { return M.mode === "pc" ? !!(M.pc && M.pc.playing) : !!(M.audio && !M.audio.paused); }
    function nowTime() {
      if (M.mode === "pc") { const pc = M.pc || {}; return [pcPosition(), +pc.duration || +((pc.track || {}).duration) || 0]; }
      const a = M.audio; const t = current();
      return [a ? a.currentTime : 0, a && isFinite(a.duration) && a.duration ? a.duration : (t && t.duration) || 0];
    }
    function paintNow() { drawMini(); if (M.full) drawFullTrack(); }
    function paintPlaying() {
      if (!lyricRaf) lyricLoop();
      const playing = isPlaying();
      document.documentElement.classList.toggle("mm-playing", playing);
      $$(".mm-toggle").forEach((b) => { b.innerHTML = icon(playing ? "mmPause" : "mmPlay"); b.setAttribute("aria-label", playing ? tr("Пауза", "Pause") : tr("Играть", "Play")); });
      if (navigator.mediaSession && M.mode === "phone") try { navigator.mediaSession.playbackState = playing ? "playing" : "paused"; } catch (e) {  }
      const t = nowTrack();
      $$(".mm-row.now").forEach((r) => r.classList.remove("now"));
      if (t) $$(`.mm-row[data-track="${CSS.escape(t.id)}"]`).forEach((r) => r.classList.add("now"));
    }
    let scrubbing = false;
    function paintTime() {
      const [pos, dur] = nowTime();
      const p = dur ? Math.min(1, pos / dur) : 0;
      const mini = $("#mm-mini"); if (mini) mini.style.setProperty("--p", p);
      if (!M.full || scrubbing) return;
      const f = M.full;
      f.style.setProperty("--p", p);
      $(".mm-t-pos", f).textContent = fmt(pos);
      $(".mm-t-left", f).textContent = "−" + fmt(Math.max(0, dur - pos));
    }

    function miniEl() {
      let el = $("#mm-mini");
      if (!el) {
        el = document.createElement("div"); el.id = "mm-mini"; el.className = "mm-mini"; el.hidden = true;
        $("#tabbar").after(el);
      }
      return el;
    }
    function drawMini() {
      const t = nowTrack(); const el = miniEl();
      el.hidden = !t;
      $("#app").classList.toggle("mm-has-mini", !!t);
      paintLook();
      if (!t) return;
      el.style.setProperty("--mm-tint", accentOf(t));
      el.innerHTML = `<button class="mm-mini-main" aria-label="${esc(tr("Открыть плеер", "Open the player"))}">${art(t, "mm-mini-art")}
          <span class="mm-mini-text"><b>${esc(t.title || "")}</b><small>${M.mode === "pc" ? `${icon("monitor")}${esc(tr("Компьютер", "Computer"))} · ` : ""}${esc(t.artist || "")}</small></span></button>
        <button class="mm-ib mm-toggle" aria-label="">${icon(isPlaying() ? "mmPause" : "mmPlay")}</button>
        <button class="mm-ib mm-mini-next" aria-label="${esc(tr("Следующий", "Next"))}">${icon("mmNext")}</button><i class="mm-mini-bar"></i>`;
      $(".mm-mini-main", el).onclick = openFull;
      $(".mm-toggle", el).onclick = toggle;
      $(".mm-mini-next", el).onclick = () => next();

      let y0 = null;
      el.ontouchstart = (e) => { y0 = e.touches[0].clientY; };
      el.ontouchend = (e) => { if (y0 != null && y0 - e.changedTouches[0].clientY > 40) openFull(); y0 = null; };
    }

    function openFull() {
      if (M.full || !nowTrack()) return;
      haptic("medium");
      const f = document.createElement("div");
      f.className = "mm-full"; f.setAttribute("role", "dialog");
      f.innerHTML = `<div class="mm-bg"><i class="b1"></i><i class="b2"></i><i class="b3"></i></div>
        <div class="mm-full-in">
          <header class="mm-fh"><button class="mm-ib mm-close" aria-label="${esc(tr("Свернуть", "Collapse"))}">${icon("chevDown")}</button>
            <div class="mm-fh-title"><small></small><b></b></div>
            <button class="mm-ib mm-more" aria-label="${esc(tr("Ещё", "More"))}">${icon("more")}</button></header>
          <div class="mm-stage"><div class="mm-cover-wrap"></div><div class="mm-lyrics" hidden></div><div class="mm-queue" hidden></div></div>
          <div class="mm-meta"><div class="mm-meta-text"><h2></h2><p></p></div><button class="mm-ib mm-like" aria-label="${esc(tr("Нравится", "Like"))}"></button></div>
          <div class="mm-scrub" role="slider" aria-label="${esc(tr("Позиция", "Position"))}"><div class="mm-scrub-track"><i></i></div><div class="mm-times"><span class="mm-t-pos">0:00</span><span class="mm-t-left">−0:00</span></div></div>
          <div class="mm-ctrls">
            <button class="mm-ib mm-shuffle" aria-label="${esc(tr("Перемешать", "Shuffle"))}">${icon("shuffle")}</button>
            <button class="mm-ib mm-big mm-prev" aria-label="${esc(tr("Предыдущий", "Previous"))}">${icon("mmPrev")}</button>
            <button class="mm-ib mm-play mm-toggle" aria-label="">${icon("mmPlay")}</button>
            <button class="mm-ib mm-big mm-next" aria-label="${esc(tr("Следующий", "Next"))}">${icon("mmNext")}</button>
            <button class="mm-ib mm-repeat" aria-label="${esc(tr("Повтор", "Repeat"))}">${icon("repeat")}</button></div>
          <div class="mm-vol" hidden>${icon("volume")}<input type="range" min="0" max="100" step="1" aria-label="${esc(tr("Громкость компьютера", "Computer volume"))}"></div>
          <footer class="mm-ff"><button class="mm-ib mm-tab-lyrics" aria-label="${esc(tr("Текст", "Lyrics"))}">${icon("lyrics")}</button>
            <button class="mm-dev mm-dev-btn">${icon("devices")}<span></span></button>
            <button class="mm-ib mm-tab-queue" aria-label="${esc(tr("Очередь", "Queue"))}">${icon("queue")}</button></footer>
        </div>`;
      document.body.appendChild(f);
      M.full = f;
      document.documentElement.classList.add("mm-full-open");
      requestAnimationFrame(() => f.classList.add("open"));
      const handle = { back: closeFull }; f._handle = handle; kit.pushBack(handle);
      $(".mm-close", f).onclick = closeFull;
      $(".mm-prev", f).onclick = prev; $(".mm-next", f).onclick = () => next();
      $(".mm-toggle", f).onclick = toggle;
      $(".mm-shuffle", f).onclick = () => { if (M.mode === "pc") { pcCommand("shuffle", { value: !(M.pc && M.pc.shuffle) }); return; } M.shuffle = !M.shuffle; haptic("select"); drawFullTrack(); };
      $(".mm-repeat", f).onclick = () => {
        const order = ["off", "all", "one"]; const cur = M.mode === "pc" ? ((M.pc && M.pc.repeat) || "off") : M.repeat;
        const nextMode = order[(order.indexOf(cur) + 1) % 3];
        if (M.mode === "pc") pcCommand("repeat", { value: nextMode }); else { M.repeat = nextMode; haptic("select"); drawFullTrack(); }
      };
      $(".mm-like", f).onclick = () => { const t = nowTrack(); if (t) setLike(byId.get(t.id) || t, !t.liked).then(drawFullTrack); };
      $(".mm-more", f).onclick = () => { const t = nowTrack(); if (t) trackMenu(t); };
      $(".mm-meta p", f).onclick = () => { const t = nowTrack(); if (t && t.artist) { closeFull(); openArtist(t.artist); } };
      $(".mm-dev", f).onclick = devicesSheet;
      $(".mm-tab-lyrics", f).onclick = () => stagePane("lyrics");
      $(".mm-tab-queue", f).onclick = () => stagePane("queue");
      const vol = $(".mm-vol input", f);
      vol.oninput = () => { clearTimeout(vol._t); vol._t = setTimeout(() => pcCommand("volume", { value: +vol.value / 100 }), 120); };
      bindScrub($(".mm-scrub", f));
      bindGestures(f);
      drawFullTrack(); paintPlaying(); paintTime();
      if (M.mode === "pc") pcRefresh();
    }
    function closeFull() {
      const f = M.full; if (!f) return;
      M.full = null; kit.popBack(f._handle);
      document.documentElement.classList.remove("mm-full-open");
      f.classList.remove("open"); f.classList.add("leave");
      setTimeout(() => f.remove(), 380);
    }
    function stagePane(which) {
      const f = M.full; if (!f) return;
      const pane = $(`.mm-${which}`, f); const show = pane.hidden;
      $(".mm-lyrics", f).hidden = true; $(".mm-queue", f).hidden = true;
      pane.hidden = !show;
      f.classList.toggle("pane", show);
      $(".mm-tab-lyrics", f).classList.toggle("on", show && which === "lyrics");
      $(".mm-tab-queue", f).classList.toggle("on", show && which === "queue");
      haptic("select");
      if (show && which === "lyrics") loadLyrics();
      if (show && which === "queue") drawQueue();
    }
    function drawFullTrack() {
      const f = M.full; const t = nowTrack(); if (!f || !t) return;
      const full = byId.get(t.id) || t;
      f.style.setProperty("--mm-tint", accentOf(full));
      paletteFor(full).then((pal) => { if (M.full === f && pal) pal.forEach((c, i) => f.style.setProperty(`--mm-c${i + 1}`, c)); });
      const wrap = $(".mm-cover-wrap", f);
      if (wrap.dataset.id !== t.id) {
        const dir = wrap.dataset.dir || "";
        wrap.dataset.id = t.id; wrap.dataset.dir = "";
        wrap.innerHTML = art(full, "mm-cover " + (dir ? "in-" + dir : ""));
      }
      $(".mm-meta h2", f).textContent = t.title || "";
      $(".mm-meta p", f).textContent = [t.artist, t.album].filter(Boolean).join(" — ");
      const like = $(".mm-like", f); like.classList.toggle("on", !!full.liked); like.innerHTML = icon(full.liked ? "heartFill" : "heart"); like.dataset.like = t.id;
      const pc = M.mode === "pc";
      $(".mm-fh-title small", f).textContent = pc ? tr("ПУЛЬТ · КОМПЬЮТЕР", "REMOTE · COMPUTER") : tr("ИГРАЕТ НА ТЕЛЕФОНЕ", "PLAYING ON THE PHONE");
      $(".mm-fh-title b", f).textContent = pc ? "MemuMusic" : (M.queue.length > 1 ? `${M.index + 1} / ${M.queue.length}` : "MemuMusic");
      const shuffle = pc ? !!(M.pc && M.pc.shuffle) : M.shuffle; const repeat = pc ? ((M.pc && M.pc.repeat) || "off") : M.repeat;
      $(".mm-shuffle", f).classList.toggle("on", shuffle);
      const rb = $(".mm-repeat", f); rb.classList.toggle("on", repeat !== "off"); rb.innerHTML = icon(repeat === "one" ? "repeatOne" : "repeat");
      $(".mm-dev span", f).textContent = pc ? tr("Компьютер", "Computer") : tr("Телефон", "Phone");
      $(".mm-dev", f).classList.toggle("pc", pc);
      const vol = $(".mm-vol", f); vol.hidden = !pc;
      if (pc && M.pc && document.activeElement !== $("input", vol)) $("input", vol).value = Math.round((+M.pc.volume || 0) * 100);
      if (M.lyricsFor !== t.id && !$(".mm-lyrics", f).hidden) loadLyrics();
      if (!$(".mm-queue", f).hidden) drawQueue();
    }
    function bindScrub(el) {
      const track = $(".mm-scrub-track", el);
      const at = (x) => { const r = track.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)); };
      let p = 0;
      const move = (x) => { p = at(x); const [, dur] = nowTime(); M.full.style.setProperty("--p", p); $(".mm-t-pos", M.full).textContent = fmt(p * dur); $(".mm-t-left", M.full).textContent = "−" + fmt(dur - p * dur); };
      el.addEventListener("pointerdown", (e) => { scrubbing = true; el.classList.add("drag"); el.setPointerCapture(e.pointerId); move(e.clientX); haptic("select"); });
      el.addEventListener("pointermove", (e) => { if (scrubbing) move(e.clientX); });
      const end = () => { if (!scrubbing) return; scrubbing = false; el.classList.remove("drag"); const [, dur] = nowTime(); seek(p * dur); haptic("light"); };
      el.addEventListener("pointerup", end); el.addEventListener("pointercancel", end);
    }
    function bindGestures(f) {

      const inner = $(".mm-full-in", f); const stage = $(".mm-stage", f);
      let x0 = null, y0 = null, axis = "", fromStage = false;
      inner.addEventListener("touchstart", (e) => {
        if (e.target.closest(".mm-scrub, .mm-vol, .mm-lyrics, .mm-queue")) { x0 = null; return; }
        x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; axis = ""; fromStage = !!e.target.closest(".mm-stage");
      }, { passive: true });
      inner.addEventListener("touchmove", (e) => {
        if (x0 == null) return;
        const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
        if (!axis && Math.hypot(dx, dy) > 10) axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
        if (axis === "y" && dy > 0) inner.style.transform = `translateY(${dy * 0.9}px)`;
        if (axis === "x" && fromStage) stage.style.transform = `translateX(${dx * 0.6}px) rotate(${dx * 0.01}deg)`;
      }, { passive: true });
      inner.addEventListener("touchend", (e) => {
        if (x0 == null) return;
        const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
        inner.style.transform = ""; stage.style.transform = "";
        if (axis === "y" && dy > 110) closeFull();
        else if (axis === "x" && fromStage && Math.abs(dx) > 70) { $(".mm-cover-wrap", f).dataset.dir = dx < 0 ? "left" : "right"; if (dx < 0) next(); else prev(); }
        x0 = null;
      });
    }

    const palettes = {};
    function paletteFor(t) {
      if (palettes[t.id]) return Promise.resolve(palettes[t.id]);
      const src = offlineCover(t.id) || t.cover;
      const base = accentOf(t);
      if (!src) return Promise.resolve(palettes[t.id] = [base, base, base]);
      return new Promise((resolve) => {
        const img = new Image(); img.decoding = "async";
        img.onload = () => {
          try {
            const c = document.createElement("canvas"); c.width = c.height = 12;
            const g = c.getContext("2d", { willReadFrequently: true }); g.drawImage(img, 0, 0, 12, 12);
            const d = g.getImageData(0, 0, 12, 12).data;
            const pick = (x0, y0) => { let r = 0, gg = 0, b = 0, n = 0; for (let y = y0; y < y0 + 6; y++) for (let x = x0; x < x0 + 6; x++) { const i = (y * 12 + x) * 4; r += d[i]; gg += d[i + 1]; b += d[i + 2]; n++; } return `rgb(${r / n | 0} ${gg / n | 0} ${b / n | 0})`; };
            resolve(palettes[t.id] = [pick(0, 0), pick(6, 6), pick(6, 0)]);
          } catch (e) { resolve(palettes[t.id] = [base, base, base]); }
        };
        img.onerror = () => resolve(palettes[t.id] = [base, base, base]);
        img.src = src;
      });
    }

    async function loadLyrics() {
      const f = M.full; const t = nowTrack(); if (!f || !t) return;
      const box = $(".mm-lyrics", f);
      if (M.lyricsFor === t.id && M.lyrics) return;
      M.lyricsFor = t.id; M.lyrics = null;
      box.innerHTML = `<p class="mm-ly-note">${esc(tr("Ищу текст…", "Looking for the lyrics…"))}</p>`;
      let data = null;
      try { data = await api("music.lyrics", { id: t.id }); } catch (e) { data = null; }
      if (M.lyricsFor !== t.id) return;
      M.lyrics = data || {};
      if (data && Array.isArray(data.synced) && data.synced.length) {
        box.innerHTML = data.synced.map(([sec, line], i) => `<p class="mm-ly" data-i="${i}" data-t="${+sec}">${esc(line || "♪")}</p>`).join("") + `<p class="mm-ly-src">${esc(data.source_name || data.source || "")}</p>`;
        $$(".mm-ly", box).forEach((p) => p.onclick = () => { seek(+p.dataset.t + (+data.offset || 0) + (+data.general_offset || 0)); haptic("select"); });
        lyricTick(true); lyricLoop();
      } else if (data && data.plain) {
        box.innerHTML = `<div class="mm-ly-plain">${esc(data.plain).replace(/\n/g, "<br>")}</div><p class="mm-ly-src">${esc(data.source_name || data.source || "")}</p>`;
      } else box.innerHTML = `<p class="mm-ly-note">${esc(tr("Текста для этого трека нет", "No lyrics for this track"))}</p>`;
    }
    let lyricIndex = -1, lyricRaf = 0;

    function lyricLoop() {
      lyricRaf = 0;
      const f = M.full;
      if (!f || !M.lyrics || !Array.isArray(M.lyrics.synced) || $(".mm-lyrics", f).hidden || document.hidden || !isPlaying()) return;
      lyricTick();
      lyricRaf = requestAnimationFrame(lyricLoop);
    }
    function lyricTick(force) {
      const f = M.full; const ly = M.lyrics;
      if (!f || !ly || !Array.isArray(ly.synced) || $(".mm-lyrics", f).hidden) return;
      const [pos] = nowTime(); let i = -1;
      const at = pos - (+ly.offset || 0) - (+ly.general_offset || 0);
      for (let k = 0; k < ly.synced.length; k++) { if (ly.synced[k][0] <= at) i = k; else break; }
      if (i === lyricIndex && !force) return;
      lyricIndex = i;
      const box = $(".mm-lyrics", f);
      $$(".mm-ly.on, .mm-ly.past", box).forEach((p) => p.classList.remove("on", "past"));
      $$(".mm-ly", box).forEach((p) => { if (+p.dataset.i < i) p.classList.add("past"); });
      const cur = $(`.mm-ly[data-i="${i}"]`, box);
      if (cur) { cur.classList.add("on"); box.scrollTo({ top: cur.offsetTop - box.clientHeight * 0.36, behavior: reduced() ? "auto" : "smooth" }); }
    }

    function drawQueue() {
      const f = M.full; if (!f) return;
      const box = $(".mm-queue", f);
      const list = M.mode === "pc" ? (M.pc && M.pc.queue) || [] : M.queue;
      const at = M.mode === "pc" ? +((M.pc && M.pc.index) || 0) : M.index;
      box.innerHTML = `<h4>${esc(tr("Далее", "Up next"))}</h4>` + (list.length ? list.slice(Math.max(0, at), at + 60).map((t, k) =>
        `<button class="mm-q ${k === 0 ? "now" : ""}" data-qi="${at + k}">${art(t, "mm-q-art")}<span><b>${esc(t.title)}</b><small>${esc(t.artist || "")}</small></span><em>${fmt(t.duration)}</em></button>`).join("")
        : `<p class="mm-ly-note">${esc(tr("Очередь пуста", "The queue is empty"))}</p>`);
      $$(".mm-q", box).forEach((b) => b.onclick = () => {
        const i = +b.dataset.qi; haptic("select");
        if (M.mode === "pc") pcCommand("play_track", { track_id: list[i].id, queue: list.map((x) => x.id) });
        else { M.index = i; loadCurrent(0, true); }
      });
    }

    function trackMenu(t, { playlist } = {}) {
      const full = byId.get(t.id) || t;
      const items = [
        { icon: "play", label: tr("Сыграть следующим", "Play next"), run: () => playNext(full) },
        { icon: "list", label: tr("Добавить в очередь", "Add to the queue"), run: () => addToQueue(full) },
        { icon: full.liked ? "heartFill" : "heart", label: full.liked ? tr("Убрать из любимых", "Remove from liked") : tr("В любимые", "Like"), run: () => setLike(full, !full.liked) },
        { icon: "plus", label: tr("В плейлист…", "Add to a playlist…"), run: () => addToPlaylistSheet([full]) },
        M.offline[t.id] ? { icon: "trash", label: tr("Удалить с телефона", "Remove from the phone"), run: async () => { await dropOffline(t.id); paintRow(t.id); toast(tr("Удалено с телефона", "Removed from the phone")); if (M.sub === "device") drawSub(); } }
          : { icon: "download", label: tr("Скачать на телефон", "Save to the phone"), sub: full.size ? bytes(full.size) : "", run: () => downloadMany([full]) },
        full.artist ? { icon: "user", label: tr("К исполнителю", "Go to the artist"), sub: full.artist, run: () => { closeFull(); openArtist(full.artist); } } : null,
        full.album_key ? { icon: "music", label: tr("К альбому", "Go to the album"), sub: full.album || "", run: () => { closeFull(); openAlbum(full.album_key); } } : null,
        { icon: "monitor", label: tr("Включить на компьютере", "Play on the computer"), run: () => api("music.remote", { cmd: "play_track", track_id: t.id }).then(() => toast(tr("Играет на компьютере", "Playing on the computer"), "ok")).catch((e) => toast(e.message, "err")) },
      ];
      if (playlist && !playlist.smart) items.push({ icon: "trash", danger: true, label: tr("Убрать из плейлиста", "Remove from the playlist"), run: () => api("music.playlist", { action: "update", id: playlist.id, remove: [t.id] }).then(() => { toast(tr("Убрано", "Removed")); if (playlist._reload) playlist._reload(); }).catch((e) => toast(e.message, "err")) });
      actionSheet(t.title || "", items.filter(Boolean));
    }
    async function addToPlaylistSheet(list) {
      if (!M.playlists.length) try { M.playlists = (await api("music.playlists")).playlists || []; } catch (e) {  }
      const items = M.playlists.filter((p) => !p.smart).map((p) => ({ icon: "music", label: p.name, sub: tracksWord(p.count || 0), run: () => addTo(p, list) }));
      items.unshift({ icon: "plus", label: tr("Новый плейлист", "New playlist"), run: async () => {
        const name = await promptSheet(tr("Новый плейлист", "New playlist"), { placeholder: tr("Название", "Name"), ok: tr("Создать", "Create") });
        if (!name) return;
        try { const r = await api("music.playlist", { action: "create", name, track_ids: list.map((t) => t.id) }); M.playlists.unshift(r.playlist); haptic("success"); toast(tr("Плейлист создан", "Playlist created"), "ok"); }
        catch (e) { toast(e.message, "err"); }
      } });
      actionSheet(tr("Добавить в плейлист", "Add to a playlist"), items);
    }
    async function addTo(p, list) {
      const ids = list.map((t) => t.id);
      try { await api("music.playlist", { action: "update", id: p.id, add: ids }); }
      catch (e) { if (e.code !== "offline") { toast(e.message, "err"); return; } queueSync("playlist_adds", { playlist: p.id, ids }); }
      haptic("success"); toast(tr(`Добавлено в «${p.name}»`, `Added to “${p.name}”`), "ok");
    }

    let jobsTimer = null;
    function linkSheet(prefill = "") {
      openSheet({ title: tr("Скачать по ссылке", "Download a link"), body: `
        <p class="mm-sheet-note">${esc(tr("Spotify, YouTube, YouTube Music, Apple Music, SoundCloud, Яндекс Музыка, VK, Deezer… Компьютер скачает трек, альбом или плейлист в библиотеку.", "Spotify, YouTube, YouTube Music, Apple Music, SoundCloud, Deezer, Yandex Music… The computer downloads the track, album or playlist into the library."))}</p>
        <div class="field mm-link-field">${icon("link")}<input id="mm-link" inputmode="url" autocomplete="off" placeholder="https://…" value="${esc(prefill)}"><button class="mm-paste" type="button">${esc(tr("Вставить", "Paste"))}</button></div>
        <div class="mm-detect" id="mm-detect"></div>
        <div class="mm-jobs" id="mm-jobs"></div>`,
      actions: [{ label: tr("Скачать на компьютер", "Download on the computer"), run: async () => {
        const url = $("#mm-link").value.trim();
        if (!/^https?:\/\//i.test(url)) { haptic("error"); toast(tr("Вставьте ссылку http(s)://", "Paste an http(s):// link"), "err"); return true; }
        try { await api("music.download", { url }); haptic("success"); $("#mm-link").value = ""; $("#mm-detect").innerHTML = ""; drawJobs(); }
        catch (e) { toast(e.message, "err"); }
        return true;
      } }],
      onMount: (el) => {
        const input = $("#mm-link", el);
        let timer = null;
        const detect = () => { clearTimeout(timer); timer = setTimeout(async () => {
          const url = input.value.trim(); const box = $("#mm-detect", el);
          if (!/^https?:\/\/\S+\.\S+/.test(url)) { box.innerHTML = ""; return; }
          try { const d = await api("music.detect", { url }); box.innerHTML = `<span class="mm-src mm-src-${esc(d.source || "web")}">${esc(sourceName(d.source))}</span><span>${esc(kindName(d.kind))}${d.title ? ` · ${esc(d.title)}` : ""}${d.items_estimate ? ` · ${esc(tracksWord(d.items_estimate))}` : ""}</span>`; }
          catch (e) { box.innerHTML = `<span class="mm-detect-err">${esc(e.message)}</span>`; }
        }, 350); };
        input.oninput = detect; if (prefill) detect();
        $(".mm-paste", el).onclick = async () => { try { input.value = (await navigator.clipboard.readText()).trim(); detect(); } catch (e) { input.focus(); } };
        drawJobs();
        clearInterval(jobsTimer);
        jobsTimer = setInterval(() => { if (!document.body.contains(el)) { clearInterval(jobsTimer); return; } if (!document.hidden) drawJobs(); }, 1500);
      },
      onClose: () => clearInterval(jobsTimer) });
    }
    const SOURCES = { youtube: "YouTube", ytmusic: "YouTube Music", spotify: "Spotify", apple: "Apple Music", soundcloud: "SoundCloud", deezer: "Deezer",
      yandex: tr("Яндекс Музыка", "Yandex Music"), vk: "VK", bandcamp: "Bandcamp", tidal: "Tidal", amazon: "Amazon Music", local: tr("Файл", "File") };
    const sourceName = (s) => SOURCES[s] || (s ? s[0].toUpperCase() + s.slice(1) : tr("Сайт", "Website"));
    const kindName = (k) => ({ track: tr("Трек", "Track"), album: tr("Альбом", "Album"), playlist: tr("Плейлист", "Playlist"), artist: tr("Исполнитель", "Artist") }[k] || tr("Ссылка", "Link"));
    async function drawJobs() {
      const box = $("#mm-jobs"); if (!box) return;
      try { M.jobs = (await api("music.jobs")).jobs || []; } catch (e) { return; }
      const rows = M.jobs.slice(0, 8);
      box.innerHTML = rows.length ? `<h4>${esc(tr("Загрузки на компьютере", "Downloads on the computer"))}</h4>` + rows.map((j) => {
        const p = Math.round((+j.progress || 0) * 100);
        const st = { queued: tr("в очереди", "queued"), running: `${p}%`, done: tr("готово", "done"), error: tr("ошибка", "error"), cancelled: tr("отменено", "cancelled") }[j.status] || j.status;
        return `<div class="mm-job ${esc(j.status)}" style="--p:${(+j.progress || 0)}"><span class="mm-src mm-src-${esc(j.source || "web")}">${esc(sourceName(j.source))}</span>
          <div class="mm-job-body"><b>${esc(j.title || j.url)}</b><i class="mm-job-bar"></i>${j.error ? `<small class="err">${esc(j.error)}</small>` : ""}</div>
          <em>${esc(st)}</em>${j.status === "running" || j.status === "queued" ? `<button class="mm-ib mm-job-x" data-job="${esc(j.job_id)}" aria-label="${esc(tr("Отменить", "Cancel"))}">${icon("close")}</button>` : ""}</div>`;
      }).join("") : "";
      $$(".mm-job-x", box).forEach((b) => b.onclick = () => api("music.cancel", { job_id: b.dataset.job }).then(drawJobs).catch((e) => toast(e.message, "err")));
      if (rows.some((j) => j.status === "done") && M.sub !== "device") { M.home = null; }
    }

    const SUBS = [["home", "Главная", "Home"], ["library", "Библиотека", "Library"], ["playlists", "Плейлисты", "Playlists"], ["device", "Скачано", "Saved"]];
    let root = null;
    function renderMusicTab(view) {
      root = view;
      kit.setTop(tr("Музыка", "Music"), `<button class="icon-btn" id="mm-link-btn" aria-label="${esc(tr("Скачать по ссылке", "Download a link"))}">${icon("link")}</button><button class="icon-btn mm-dev-btn" id="mm-dev-top" aria-label="${esc(tr("Где играть", "Play on"))}">${icon("devices")}</button>`);
      $("#mm-link-btn").onclick = () => linkSheet();
      $("#mm-dev-top").onclick = devicesSheet;
      view.classList.add("mm-root");
      view.innerHTML = `<div class="mm-seg" role="tablist">${SUBS.map(([id, ru, en]) => `<button role="tab" data-sub="${id}" class="${M.sub === id ? "on" : ""}">${esc(tr(ru, en))}</button>`).join("")}<i class="mm-seg-ink"></i></div><div id="mm-body" class="mm-body"></div>`;
      $$(".mm-seg [data-sub]", view).forEach((b) => b.onclick = () => { if (M.sub === b.dataset.sub) return; M.sub = b.dataset.sub; haptic("select"); paintSeg(); drawSub(); });
      paintSeg(); drawSub();
      if (S.mmOffline) return;
      flush();
      pcPolling(true);
    }
    function paintSeg() {
      if (!root) return;
      const i = SUBS.findIndex((s) => s[0] === M.sub);
      $$(".mm-seg [data-sub]", root).forEach((b) => b.classList.toggle("on", b.dataset.sub === M.sub));
      const seg = $(".mm-seg", root); if (seg) seg.style.setProperty("--i", i);
    }
    function body() { return $("#mm-body", root); }
    function drawSub() {
      if (!root || S.tab !== "music") return;
      const box = body(); if (!box) return;
      box.className = "mm-body mm-in";
      ({ home: drawHome, library: drawLibrary, playlists: drawPlaylists, device: drawDevice }[M.sub] || drawHome)(box);
    }
    function emptyBlock(iconName, title, note, action = "") {
      return `<div class="mm-empty"><div class="mm-empty-art">${icon(iconName)}</div><h3>${esc(title)}</h3><p>${esc(note)}</p>${action}</div>`;
    }
    function skeleton() { return `<div class="mm-skel"><i></i><i></i><i></i></div><div class="mm-skel row"><i></i><i></i><i></i><i></i></div>`; }

    async function drawHome(box) {
      if (!M.home) box.innerHTML = skeleton();
      else paintHome(box);
      try {
        const data = await api("music.home");
        M.home = data; remember(data.liked); remember(data.added); remember((data.stats || {}).recent); remember((data.stats || {}).top_tracks);
        M.playlists = data.playlists || M.playlists;
        if (data.player) { M.pc = Object.assign(data.player, { at: Date.now() }); }
        if (data.pc_id) setPcId(data.pc_id);
        applyLook(data.look);
        if (M.sub === "home" && body() === box) paintHome(box);
      } catch (e) {
        if (M.home) return;
        const off = Object.values(M.offline);
        box.innerHTML = e.code === "music_unavailable"
          ? emptyBlock("music", tr("MemuMusic не установлен на компьютере", "MemuMusic is not available on the computer"), tr("Обновите MemuAI на компьютере — модуль музыки появится в разделе «Модули».", "Update MemuAI on the computer — the music module appears under Modules."))
          : emptyBlock("cloudOff", tr("Компьютер не отвечает", "The computer does not answer"), off.length ? tr("Можно слушать то, что сохранено на телефоне.", "You can listen to what is saved on the phone.") : e.message,
            off.length ? `<button class="btn" id="mm-go-off">${esc(tr("На телефоне", "On the phone"))} · ${off.length}</button>` : "");
        const go = $("#mm-go-off", box); if (go) go.onclick = () => { M.sub = "device"; paintSeg(); drawSub(); };
      }
    }
    function pcCardHtml() {
      const pc = M.pc;
      if (!pc || !pc.online || !pc.track) return `<div id="mm-pc-card" hidden></div>`;
      const t = pc.track;
      return `<div id="mm-pc-card" class="mm-pc" style="--mm-tint:${accentOf(t)}">${art(t, "mm-pc-art")}
        <div class="mm-pc-text"><small><i class="mm-eq ${pc.playing ? "on" : ""}"><b></b><b></b><b></b></i><em>${esc(pc.playing ? tr("Играет на ПК", "Playing on PC") : tr("Пауза на ПК", "Paused on PC"))}</em></small><b>${esc(t.title)}</b><span>${esc(t.artist || "")}</span></div>
        <div class="mm-pc-acts"><button class="mm-ib mm-pc-toggle" aria-label="${esc(tr("Пауза / играть", "Pause / play"))}">${icon(pc.playing ? "mmPause" : "mmPlay")}</button>
        <button class="mm-chip-btn mm-pc-take" aria-label="${esc(tr("Продолжить на телефоне", "Continue on the phone"))}">${icon("phone")}<span>${esc(tr("Сюда", "Here"))}</span></button></div></div>`;
    }
    function bindPcCard() {
      const card = $("#mm-pc-card"); if (!card || card.hidden) return;
      $(".mm-pc-toggle", card).onclick = (e) => { e.stopPropagation(); M.mode === "pc" ? toggle() : pcCommand("toggle"); };
      $(".mm-pc-take", card).onclick = (e) => { e.stopPropagation(); takeFromPc(); };
      card.onclick = () => { setMode("pc"); openFull(); };
    }
    function paintHome(box) {
      const h = M.home || {}; const st = h.stats || {};
      const recent = (st.recent || []).filter(Boolean);
      const saved = load(LS.state, null);
      const cont = saved && saved.queue && saved.queue[saved.index] ? saved.queue[saved.index] : recent[0];
      const hour = new Date().getHours();
      const hello = hour < 5 ? tr("Доброй ночи", "Good night") : hour < 12 ? tr("Доброе утро", "Good morning") : hour < 18 ? tr("Добрый день", "Good afternoon") : tr("Добрый вечер", "Good evening");
      const shelf = (title, rows, kind = "track") => rows.length ? `<section class="mm-sec"><h3>${esc(title)}</h3><div class="mm-shelf">${rows.map((t, i) => kind === "pl"
        ? `<button class="mm-card" data-pl="${esc(t.id)}">${mosaic(t, "mm-card-art")}<b>${esc(t.name)}</b><small>${esc(tracksWord(t.count || 0))}</small></button>`
        : `<button class="mm-card" data-shelf="${esc(title)}" data-i="${i}">${art(t, "mm-card-art")}<b>${esc(t.title)}</b><small>${esc(t.artist || "")}</small></button>`).join("")}</div></section>` : "";
      box.innerHTML = `<div class="mm-hello"><h2>${esc(hello)}</h2><p>${esc(st.tracks ? tr(`${tracksWord(st.tracks)} · ${Math.round(st.minutes || 0)} мин музыки`, `${tracksWord(st.tracks)} · ${Math.round(st.minutes || 0)} min of music`) : tr("Библиотека пока пуста", "The library is empty so far"))}</p></div>
        ${pcCardHtml()}
        ${cont ? `<button class="mm-continue" id="mm-continue" style="--mm-tint:${accentOf(cont)}">${art(cont, "mm-cont-art")}<span class="mm-cont-text"><small>${esc(tr("Продолжить", "Continue"))}</small><b>${esc(cont.title)}</b><span>${esc(cont.artist || "")}${saved && saved.position > 5 ? ` · ${fmt(saved.position)}` : ""}</span></span><span class="mm-cont-play">${icon("mmPlay")}</span></button>` : ""}
        ${(h.liked || []).length ? `<button class="mm-liked-tile" id="mm-liked-tile"><span class="mm-lt-art">${icon("heartFill")}</span><span><b>${esc(tr("Любимые", "Liked"))}</b><small>${esc(tracksWord((h.liked || []).length))}</small></span>${icon("chev")}</button>` : ""}
        ${shelf(tr("Недавно слушали", "Recently played"), recent.slice(0, 16))}
        ${shelf(tr("Плейлисты", "Playlists"), (h.playlists || []).slice(0, 16), "pl")}
        ${shelf(tr("Новое в библиотеке", "New in the library"), (h.added || []).slice(0, 16))}
        ${shelf(tr("Любимые", "Liked"), (h.liked || []).slice(0, 16))}
        ${!st.tracks && !(h.added || []).length ? emptyBlock("music", tr("Здесь будет ваша музыка", "Your music will be here"), tr("Вставьте ссылку на трек или плейлист — компьютер скачает его, и он появится здесь.", "Paste a link to a track or a playlist — the computer downloads it and it shows up here."), `<button class="btn" id="mm-empty-link">${esc(tr("Скачать по ссылке", "Download a link"))}</button>`) : ""}`;
      const shelves = { [tr("Недавно слушали", "Recently played")]: recent, [tr("Новое в библиотеке", "New in the library")]: h.added || [], [tr("Любимые", "Liked")]: h.liked || [] };
      $$("[data-shelf]", box).forEach((b) => { b.onclick = () => { haptic("light"); playQueue(shelves[b.dataset.shelf], +b.dataset.i); }; kit.longPress(b, () => trackMenu(shelves[b.dataset.shelf][+b.dataset.i])); });
      $$("[data-pl]", box).forEach((b) => b.onclick = () => openPlaylist((h.playlists || []).find((p) => String(p.id) === b.dataset.pl)));
      const c = $("#mm-continue", box);
      if (c) c.onclick = () => {
        haptic("medium");
        if (saved && saved.queue && saved.queue[saved.index] && saved.queue[saved.index].id === cont.id) { M.queue = saved.queue; M.index = saved.index; M.shuffle = !!saved.shuffle; M.repeat = saved.repeat || "off"; loadCurrent(saved.position || 0, true); }
        else playQueue(recent, 0);
      };
      const lt = $("#mm-liked-tile", box); if (lt) lt.onclick = () => openList(tr("Любимые", "Liked"), { filter: "liked", sort: "liked" });
      const el = $("#mm-empty-link", box); if (el) el.onclick = () => linkSheet();
      bindPcCard();
    }

    function rowHtml(t, i, _all, { num } = {}) {
      const now = nowTrack();
      return `<div class="mm-row ${num ? "numbered" : ""} ${now && now.id === t.id ? "now" : ""}" data-track="${esc(t.id)}" data-i="${i}">
        ${num ? `<span class="mm-num">${esc(String(num))}</span>` : art(t, "mm-row-art")}<span class="mm-row-text"><b>${esc(t.title || "")}</b><small>${t.liked ? `<i class="mm-mini-heart">${icon("heartFill")}</i>` : ""}${esc(t.artist || "")}${t.duration ? ` · ${fmt(t.duration)}` : ""}</small></span>
        <span class="mm-dl ${M.offline[t.id] ? "on" : ""}" title="${esc(tr("На телефоне", "On the phone"))}">${icon("saved")}</span>
        <button class="mm-ib mm-row-more" aria-label="${esc(tr("Ещё", "More"))}">${icon("more")}</button></div>`;
    }
    function bindRows(box, list, extra = {}) {
      $$(".mm-row", box).forEach((row) => {
        const i = +row.dataset.i;
        row.onclick = (e) => { if (e.target.closest(".mm-row-more")) { trackMenu(list[i], extra); return; } haptic("light"); playQueue(list, i); };
        kit.longPress(row, () => trackMenu(list[i], extra));
      });
      paintPlaying();
    }

    let libTimer = null;
    const LIB_VIEWS = [["tracks", "Треки", "Tracks"], ["artists", "Исполнители", "Artists"], ["albums", "Альбомы", "Albums"]];
    function libViews(box) {
      const html = `<div class="mm-views" role="tablist">${LIB_VIEWS.map(([id, ru, en]) => `<button role="tab" data-v="${id}" class="${M.libView === id ? "on" : ""}">${esc(tr(ru, en))}</button>`).join("")}</div>`;
      return [html, () => $$(".mm-views [data-v]", box).forEach((b) => b.onclick = () => { if (M.libView === b.dataset.v) return; M.libView = b.dataset.v; haptic("select"); drawLibrary(box); })];
    }
    async function drawBrowse(box) {
      const [views, bindViews] = libViews(box);
      const kind = M.libView;
      box.innerHTML = views + `<div id="mm-browse">${skeleton()}</div>`; bindViews();
      let items = [];
      try { items = (await api("music.browse", { kind })).items || []; }
      catch (e) { const out = $("#mm-browse", box); if (out) out.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); return; }
      const out = $("#mm-browse", box); if (!out || M.libView !== kind) return;
      if (!items.length) { out.innerHTML = emptyBlock(kind === "artists" ? "user" : "music", tr("Пока пусто", "Nothing yet"), tr("Исполнители и альбомы появятся, когда в библиотеке будут треки с тегами.", "Artists and albums show up once the library has tagged tracks.")); return; }
      out.innerHTML = kind === "artists"
        ? `<div class="mm-artists">${items.map((a, i) => `<button class="mm-artist" data-a="${i}">${art({ title: a.name, cover: a.cover, accent: a.accent }, "mm-artist-art")}<b>${esc(a.name)}</b><small>${esc(tracksWord(a.tracks || 0))}</small></button>`).join("")}</div>`
        : `<div class="mm-grid">${items.map((a, i) => `<button class="mm-tile" data-al="${i}">${art({ title: a.title, cover: a.cover, accent: a.accent }, "mm-tile-art")}<b>${esc(a.title)}</b><small>${esc([a.artist, a.year].filter(Boolean).join(" · "))}</small></button>`).join("")}</div>`;
      $$("[data-a]", out).forEach((b) => b.onclick = () => openArtist(items[+b.dataset.a].name));
      $$("[data-al]", out).forEach((b) => b.onclick = () => openAlbum(items[+b.dataset.al].key));
    }
    function drawLibrary(box) {
      if (M.libView !== "tracks") { drawBrowse(box); return; }
      const [views, bindViews] = libViews(box);
      const sorts = [["added", "Недавние", "Recent"], ["title", "Название", "Title"], ["artist", "Исполнитель", "Artist"], ["plays", "Часто", "Most played"]];
      const filters = [["", "Все", "All"], ["liked", "Любимые", "Liked"], ["phone", "На телефоне", "On phone"]];
      box.innerHTML = views + `<div class="search mm-search">${icon("search")}<input id="mm-q" type="search" placeholder="${esc(tr("Треки, исполнители, альбомы", "Tracks, artists, albums"))}" value="${esc(M.libQ)}" autocomplete="off" enterkeyhint="search"></div>
        <div class="mm-chips">${filters.map(([id, ru, en]) => `<button class="mm-chip ${M.libFilter === id ? "on" : ""}" data-f="${id}">${esc(tr(ru, en))}</button>`).join("")}<span class="mm-chip-sep"></span>${sorts.map(([id, ru, en]) => `<button class="mm-chip ghost ${M.libSort === id ? "on" : ""}" data-s="${id}">${esc(tr(ru, en))}</button>`).join("")}</div>
        <div class="mm-lib-head" id="mm-lib-head"></div><div id="mm-lib" class="mm-list"></div><div id="mm-online"></div>`;
      bindViews();
      $("#mm-q", box).oninput = (e) => { M.libQ = e.target.value; clearTimeout(libTimer); libTimer = setTimeout(() => loadLibrary(box), 220); };
      $$("[data-f]", box).forEach((b) => b.onclick = () => { M.libFilter = b.dataset.f; haptic("select"); $$("[data-f]", box).forEach((x) => x.classList.toggle("on", x === b)); loadLibrary(box); });
      $$("[data-s]", box).forEach((b) => b.onclick = () => { M.libSort = b.dataset.s; haptic("select"); $$("[data-s]", box).forEach((x) => x.classList.toggle("on", x === b)); loadLibrary(box); });
      loadLibrary(box);
    }
    async function loadLibrary(box) {
      const list = $("#mm-lib", box); if (!list) return;
      let rows, total;
      if (M.libFilter === "phone") {
        const q = M.libQ.trim().toLowerCase();
        rows = Object.values(M.offline).filter((t) => !q || `${t.title} ${t.artist} ${t.album}`.toLowerCase().includes(q));
        rows.forEach((t) => { t.stream = t.stream || ""; });
        total = rows.length;
      } else {
        if (!list.children.length) list.innerHTML = skeleton();
        try {
          const data = await api("music.list", { q: M.libQ.trim(), sort: M.libSort, filter: M.libFilter, limit: 200 });
          rows = remember(data.tracks || []); total = data.total || rows.length;
        } catch (e) { list.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); return; }
      }
      M.lib = rows;
      if (!$("#mm-lib", box)) return;
      $("#mm-lib-head", box).innerHTML = rows.length ? `<span>${esc(tracksWord(total))}</span><span class="mm-lib-acts"><button class="mm-chip-btn" id="mm-lib-shuffle">${icon("shuffle")}${esc(tr("Перемешать", "Shuffle"))}</button><button class="mm-chip-btn accent" id="mm-lib-play">${icon("mmPlay")}${esc(tr("Слушать", "Play"))}</button></span>` : "";
      list.innerHTML = rows.length ? rows.map(rowHtml).join("") : emptyBlock("search", M.libQ ? tr("Ничего не нашлось", "Nothing found") : tr("Пусто", "Empty"), M.libQ ? tr("Можно поискать в интернете и скачать на компьютер.", "You can search online and download it on the computer.") : "");
      bindRows(list, rows);
      const sh = $("#mm-lib-shuffle", box); if (sh) sh.onclick = () => playQueue(rows, Math.floor(Math.random() * rows.length), { shuffle: true });
      const pl = $("#mm-lib-play", box); if (pl) pl.onclick = () => playQueue(rows, 0, { shuffle: false });
      drawOnline(box);
    }
    async function drawOnline(box) {
      const out = $("#mm-online", box); if (!out) return;
      const q = M.libQ.trim();
      if (q.length < 3 || M.libFilter === "phone") { out.innerHTML = ""; return; }
      out.innerHTML = `<button class="mm-online-btn">${icon("globe")}${esc(tr(`Найти «${q}» в интернете`, `Search “${q}” online`))}</button>`;
      $(".mm-online-btn", out).onclick = async () => {
        out.innerHTML = skeleton();
        try {
          const rows = (await api("music.search", { q, limit: 10 })).results || [];
          out.innerHTML = `<h4 class="mm-h4">${esc(tr("В интернете", "Online"))}</h4>` + (rows.length ? rows.map((r, i) => `<div class="mm-row mm-web" data-w="${i}">
            <span class="mm-art mm-row-art" style="--mm-tint:hsl(${hue(r.title)} 50% 50%)"><i class="mm-art-ph">${icon("globe")}</i>${r.thumb && /^https:/.test(r.thumb) ? `<img loading="lazy" alt="" src="${esc(r.thumb)}">` : ""}</span>
            <span class="mm-row-text"><b>${esc(r.title)}</b><small>${esc(r.artist || sourceName(r.source))}${r.duration ? ` · ${fmt(r.duration)}` : ""}</small></span>
            <button class="mm-ib mm-web-dl" aria-label="${esc(tr("Скачать на компьютер", "Download on the computer"))}">${icon("download")}</button></div>`).join("") : `<p class="mm-ly-note">${esc(tr("Ничего не нашлось", "Nothing found"))}</p>`);
          $$(".mm-web", out).forEach((row) => row.onclick = () => { const r = rows[+row.dataset.w]; haptic("medium"); api("music.download", { url: r.url }).then(() => { toast(tr("Компьютер скачивает", "The computer is downloading"), "ok"); linkSheet(); }).catch((e) => toast(e.message, "err")); });
        } catch (e) { out.innerHTML = `<p class="mm-ly-note">${esc(e.message)}</p>`; }
      };
    }
    function openList(title, args) {
      openStack({ title, build: async (view) => {
        view.classList.add("mm-stack");
        view.innerHTML = skeleton();
        try {
          const rows = remember((await api("music.list", Object.assign({ limit: 300 }, args))).tracks || []);
          view.innerHTML = listHeader({ name: title, count: rows.length, covers: rows.slice(0, 4).map((t) => t.cover).filter(Boolean), liked: args.filter === "liked" }, rows) + `<div class="mm-list">${rows.map(rowHtml).join("")}</div>`;
          bindListHeader(view, rows, title); bindRows($(".mm-list", view), rows);
        } catch (e) { view.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); }
      } });
    }

    const minutes = (sec) => `${Math.max(1, Math.round((+sec || 0) / 60))} ${tr("мин", "min")}`;
    function playActs(rows) {
      const off = rows.filter((t) => M.offline[t.id]).length; const all = off && off === rows.length;
      return `<div class="mm-ph-acts"><button class="mm-round mm-ph-dl ${all ? "on" : ""}" aria-label="${esc(tr("Скачать на телефон", "Save to the phone"))}">${icon(all ? "saved" : "download")}</button>
        <button class="mm-pill mm-ph-play">${icon("mmPlay")}${esc(tr("Слушать", "Play"))}</button>
        <button class="mm-pill ghost mm-ph-shuffle">${icon("shuffle")}${esc(tr("Перемешать", "Shuffle"))}</button></div>`;
    }
    function openArtist(name) {
      if (!name) return;
      haptic("light");
      openStack({ title: name, build: async (view) => {
        view.classList.add("mm-stack");
        view.innerHTML = skeleton();
        let a;
        try { a = await api("music.browse", { kind: "artist", name }); }
        catch (e) { view.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); return; }
        const rows = remember(a.tracks || []); const albums = a.albums || [];
        const top = rows.filter((t) => t.plays > 0).sort((x, y) => y.plays - x.plays).slice(0, 5);
        const tint = /^#[0-9a-f]{6}$/i.test(a.accent || "") ? a.accent : `hsl(${hue(a.name)} 55% 52%)`;
        const stats = [tracksWord(rows.length), a.duration ? minutes(a.duration) : "", a.plays ? tr(`${a.plays} ${plural(a.plays, "прослушивание", "прослушивания", "прослушиваний")}`, `${a.plays} ${a.plays === 1 ? "play" : "plays"}`) : ""].filter(Boolean).join(" · ");
        view.innerHTML = `<div class="mm-ah" style="--mm-tint:${tint}"><div class="mm-ah-bg">${a.cover ? `<img alt="" src="${esc(a.cover)}">` : ""}</div>
            ${art({ title: a.name, cover: a.cover, accent: a.accent }, "mm-ah-art")}<small class="mm-ah-kind">${esc(tr("Исполнитель", "Artist"))}</small><h2>${esc(a.name)}</h2><p>${esc(stats)}</p>${playActs(rows)}</div>
          ${top.length ? `<h4 class="mm-h4">${esc(tr("Популярное", "Popular"))}</h4><div class="mm-list" id="mm-ar-top">${top.map((t, i) => rowHtml(t, i)).join("")}</div>` : ""}
          ${albums.length ? `<section class="mm-sec"><h3>${esc(tr("Альбомы", "Albums"))}</h3><div class="mm-shelf">${albums.map((al, i) => `<button class="mm-card" data-al="${i}">${art({ title: al.title, cover: al.cover, accent: al.accent }, "mm-card-art")}<b>${esc(al.title)}</b><small>${esc([al.year, tracksWord(al.tracks || 0)].filter(Boolean).join(" · "))}</small></button>`).join("")}</div></section>` : ""}
          <h4 class="mm-h4">${esc(tr("Все треки", "All tracks"))}</h4><div class="mm-list" id="mm-ar-all">${rows.map(rowHtml).join("")}</div>`;
        bindListHeader(view, rows, a.name);
        const topList = $("#mm-ar-top", view); if (topList) bindRows(topList, top);
        bindRows($("#mm-ar-all", view), rows);
        $$("[data-al]", view).forEach((b) => b.onclick = () => openAlbum(albums[+b.dataset.al].key));
      } });
    }
    function openAlbum(key) {
      if (!key) return;
      haptic("light");
      openStack({ title: tr("Альбом", "Album"), build: async (view) => {
        view.classList.add("mm-stack");
        view.innerHTML = skeleton();
        let al;
        try { al = await api("music.browse", { kind: "album", key }); }
        catch (e) { view.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); return; }
        const rows = remember(al.items || []);
        const tint = /^#[0-9a-f]{6}$/i.test(al.accent || "") ? al.accent : `hsl(${hue(al.title)} 55% 52%)`;
        const meta = [tr("Альбом", "Album"), al.year, tracksWord(rows.length), al.duration ? minutes(al.duration) : ""].filter(Boolean).join(" · ");
        view.innerHTML = `<div class="mm-ph mm-album" style="--mm-tint:${tint}"><div class="mm-ph-bg"></div>${art({ title: al.title, cover: al.cover, accent: al.accent }, "mm-ph-art")}
            <h2>${esc(al.title)}</h2>${al.artist ? `<button class="mm-ah-link" id="mm-al-artist">${esc(al.artist)}</button>` : ""}<p>${esc(meta)}</p>${playActs(rows)}</div>
          <div class="mm-list" id="mm-al-list">${rows.map((t, i) => rowHtml(t, i, null, { num: t.track_no || i + 1 })).join("")}</div>`;
        bindListHeader(view, rows, al.title);
        bindRows($("#mm-al-list", view), rows);
        const link = $("#mm-al-artist", view); if (link) link.onclick = () => openArtist(al.artist);
      } });
    }

    async function drawPlaylists(box) {
      const paint = () => {
        box.innerHTML = `<div class="mm-grid">
          <button class="mm-tile mm-tile-new" id="mm-pl-new"><span class="mm-art mm-tile-art"><i class="mm-art-ph">${icon("plus")}</i></span><b>${esc(tr("Новый плейлист", "New playlist"))}</b><small>&nbsp;</small></button>
          ${M.playlists.map((p) => `<button class="mm-tile" data-pl="${esc(p.id)}">${mosaic(p, "mm-tile-art")}<b>${esc(p.name)}</b><small>${p.smart ? esc(tr("Умный · ", "Smart · ")) : ""}${esc(tracksWord(p.count || 0))}${p.duration ? ` · ${Math.round(p.duration / 60)} ${esc(tr("мин", "min"))}` : ""}</small></button>`).join("")}</div>`;
        $$("[data-pl]", box).forEach((b) => b.onclick = () => openPlaylist(M.playlists.find((p) => String(p.id) === b.dataset.pl)));
        $("#mm-pl-new", box).onclick = async () => {
          const name = await promptSheet(tr("Новый плейлист", "New playlist"), { placeholder: tr("Название", "Name"), ok: tr("Создать", "Create") });
          if (!name) return;
          try { const r = await api("music.playlist", { action: "create", name }); M.playlists.unshift(r.playlist); haptic("success"); paint(); } catch (e) { toast(e.message, "err"); }
        };
      };
      if (!M.playlists.length) box.innerHTML = skeleton(); else paint();
      try { M.playlists = (await api("music.playlists")).playlists || []; if (body() === box && M.sub === "playlists") paint(); }
      catch (e) { if (!M.playlists.length) box.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); }
    }
    function listHeader(p, rows) {
      const dur = rows.reduce((a, t) => a + (+t.duration || 0), 0);
      const off = rows.filter((t) => M.offline[t.id]).length;
      const style = `--mm-tint:${rows[0] ? accentOf(rows[0]) : `hsl(${hue(p.name)} 50% 50%)`}`;
      return `<div class="mm-ph" style="${style}"><div class="mm-ph-bg"></div>${p.liked ? `<span class="mm-art mm-ph-art mm-liked-art"><i class="mm-art-ph">${icon("heartFill")}</i></span>` : mosaic(p, "mm-ph-art")}
        <h2>${esc(p.name)}</h2><p>${esc(tracksWord(rows.length))}${dur ? ` · ${Math.round(dur / 60)} ${esc(tr("мин", "min"))}` : ""}${off ? ` · ${esc(tr("на телефоне", "on phone"))} ${off}/${rows.length}` : ""}</p>
        <div class="mm-ph-acts"><button class="mm-round mm-ph-dl ${off && off === rows.length ? "on" : ""}" aria-label="${esc(tr("Скачать на телефон", "Save to the phone"))}">${icon(off && off === rows.length ? "saved" : "download")}</button>
          <button class="mm-pill mm-ph-play">${icon("mmPlay")}${esc(tr("Слушать", "Play"))}</button>
          <button class="mm-pill ghost mm-ph-shuffle">${icon("shuffle")}${esc(tr("Перемешать", "Shuffle"))}</button>
          ${p.id && !p.smart ? `<button class="mm-round mm-ph-more" aria-label="${esc(tr("Ещё", "More"))}">${icon("more")}</button>` : ""}</div></div>`;
    }
    function bindListHeader(view, rows, name, p) {
      $(".mm-ph-play", view).onclick = () => { haptic("medium"); playQueue(rows, 0, { shuffle: false }); };
      $(".mm-ph-shuffle", view).onclick = () => { haptic("medium"); playQueue(rows, Math.floor(Math.random() * rows.length), { shuffle: true }); };
      $(".mm-ph-dl", view).onclick = () => downloadMany(rows, name).then(() => { const b = $(".mm-ph-dl", view); if (b) { b.classList.add("on"); b.innerHTML = icon("saved"); } });
      const more = $(".mm-ph-more", view);
      if (more && p) more.onclick = () => actionSheet(p.name, [
        { icon: "edit", label: tr("Переименовать", "Rename"), run: async () => { const name2 = await promptSheet(tr("Название", "Name"), { value: p.name }); if (!name2) return; try { await api("music.playlist", { action: "update", id: p.id, name: name2 }); p.name = name2; toast(tr("Сохранено", "Saved"), "ok"); p._reload && p._reload(); } catch (e) { toast(e.message, "err"); } } },
        { icon: "monitor", label: tr("Включить на компьютере", "Play on the computer"), run: () => api("music.remote", { cmd: "play_playlist", playlist_id: p.id }).then(() => toast(tr("Играет на компьютере", "Playing on the computer"), "ok")).catch((e) => toast(e.message, "err")) },
        { icon: "trash", danger: true, label: tr("Удалить плейлист", "Delete the playlist"), run: async () => { if (!(await confirmDialog(tr("Удалить плейлист? Треки останутся в библиотеке.", "Delete the playlist? The tracks stay in the library."), { danger: true, ok: tr("Удалить", "Delete") }))) return; try { await api("music.playlist", { action: "delete", id: p.id }); M.playlists = M.playlists.filter((x) => x.id !== p.id); kit.goBack(); } catch (e) { toast(e.message, "err"); } } },
      ]);
    }
    function openPlaylist(p) {
      if (!p) return;
      haptic("light");
      openStack({ title: p.name, build: (view) => {
        view.classList.add("mm-stack");
        const reload = async () => {
          if (!view.children.length) view.innerHTML = skeleton();
          try {
            const pl = (await api("music.playlist", { id: p.id })).playlist || p;
            const rows = remember(pl.tracks || []);
            Object.assign(p, pl, { _reload: reload });
            view.innerHTML = listHeader(p, rows) + `<div class="mm-list">${rows.map(rowHtml).join("")}</div>` + (rows.length ? "" : emptyBlock("music", tr("Плейлист пуст", "The playlist is empty"), tr("Добавляйте треки через «⋯» → «В плейлист».", "Add tracks with “⋯” → “Add to a playlist”.")));
            bindListHeader(view, rows, p.name, p); bindRows($(".mm-list", view), rows, { playlist: p });
          } catch (e) { view.innerHTML = emptyBlock("cloudOff", tr("Нет связи с компьютером", "No connection to the computer"), e.message); }
        };
        reload();
      } });
    }

    async function drawDevice(box) {
      await warmOfflineCovers();
      const [info, lost] = await Promise.all([storageInfo(), lostTracks()]);
      const rows = Object.values(M.offline).sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
      const share = info.quota ? Math.min(1, info.used / info.quota) : 0;
      box.innerHTML = (S.mmOffline ? `<div class="mm-offline-bar">${icon("cloudOff")}<span>${esc(tr("Без связи с компьютером — играет то, что на телефоне", "No connection to the computer — playing what is on the phone"))}</span></div>` : "") + `<div class="mm-store"><div class="mm-store-top"><span class="mm-store-ico">${icon("phone")}</span><span><b>${esc(bytes(info.used))}</b><small>${esc(tracksWord(info.count))}${info.quota ? ` · ${esc(tr("доступно", "available"))} ${esc(bytes(Math.max(0, info.quota - info.used)))}` : ""}</small></span>
          ${rows.length ? `<button class="mm-chip-btn danger" id="mm-clear">${icon("trash")}${esc(tr("Очистить", "Clear"))}</button>` : ""}</div>
          <div class="mm-store-bar"><i style="width:${Math.max(rows.length ? 2 : 0, share * 100).toFixed(2)}%"></i></div>
          <p>${esc(tr("Треки, сохранённые здесь, играют без связи с компьютером — даже если Mini App открыт без интернета.", "Tracks saved here play without a connection to the computer — even when the Mini App opens with no internet."))}</p></div>
        ${lost.length ? `<div class="mm-restore"><span class="mm-restore-ico">${icon("saved")}</span><div><b>${esc(tr(`Вернуть ${tracksWord(lost.length)}`, `Bring back ${tracksWord(lost.length)}`))}</b>
          <small>${esc(tr("У Mini App новый адрес, и телефон забыл скачанное. Список сохранён — скачаю те же треки снова.", "The Mini App has a new address and the phone lost what was saved. The list is kept — the same tracks download again."))}</small>
          <span class="mm-restore-acts"><button class="mm-chip-btn accent" id="mm-restore">${icon("download")}${esc(tr("Скачать снова", "Download again"))}</button><button class="mm-chip-btn" id="mm-restore-forget">${esc(tr("Забыть", "Forget"))}</button></span></div></div>` : ""}
        ${rows.length ? `<div class="mm-lib-head"><span>${esc(tr("На телефоне", "On the phone"))}</span><span class="mm-lib-acts"><button class="mm-chip-btn accent" id="mm-off-play">${icon("mmPlay")}${esc(tr("Слушать", "Play"))}</button></span></div><div class="mm-list" id="mm-off-list">${rows.map(rowHtml).join("")}</div>`
          : lost.length ? "" : emptyBlock("download", tr("На телефоне пока ничего нет", "Nothing on the phone yet"), tr("Откройте трек или плейлист и нажмите «Скачать на телефон» — он будет играть даже без интернета.", "Open a track or a playlist and tap “Save to the phone” — it plays even without the internet."))}`;
      const clear = $("#mm-clear", box);
      if (clear) clear.onclick = async () => {
        if (!(await confirmDialog(tr("Удалить с телефона все сохранённые треки? В библиотеке на компьютере они останутся.", "Remove every saved track from the phone? They stay in the library on the computer."), { danger: true, ok: tr("Очистить", "Clear") }))) return;
        for (const id of Object.keys(M.offline)) await dropOffline(id);
        try { await caches.delete(CACHE); } catch (e) {  }
        haptic("success"); drawDevice(box);
      };
      const list = $("#mm-off-list", box);
      if (list) { bindRows(list, rows); $("#mm-off-play", box).onclick = () => playQueue(rows, 0); }
      const restore = $("#mm-restore", box);
      if (restore) restore.onclick = async () => {
        restore.disabled = true;
        try {
          const data = await api("music.tracks", { ids: lost });
          if (data.pc_id) setPcId(data.pc_id);
          await downloadMany(remember(data.tracks || []), tr("восстановлено", "restored"));
        } catch (e) { toast(e.message, "err"); restore.disabled = false; }
      };
      const forget = $("#mm-restore-forget", box);
      if (forget) forget.onclick = async () => { const st = await stableLoad(); lost.forEach((id) => delete st[id]); stableSave(); haptic("light"); drawDevice(box); };
    }

    kit.addTab({ id: "music", icon: "music", ru: "Музыка", en: "Music", before: "modules" }, renderMusicTab);
    let inited = false;
    function init() {
      if (inited) return;
      inited = true;
      document.documentElement.dataset.mmMode = M.mode;
      document.documentElement.classList.toggle("mm-light", M.light);
      warmOfflineCovers();
      const saved = load(LS.state, null);
      if (saved && saved.queue && saved.queue.length) { M.queue = saved.queue; M.index = Math.min(saved.index || 0, saved.queue.length - 1); M.shuffle = !!saved.shuffle; M.repeat = saved.repeat || "off"; drawMini(); }
      flush();

      document.addEventListener("visibilitychange", () => { if (!document.hidden) { flush(); if (M.mode === "pc" || M.full) pcRefresh(); } });
      window.addEventListener("online", flush);

      try { const sp = (S.tg && S.tg.initDataUnsafe && S.tg.initDataUnsafe.start_param) || ""; if (/^music_dl$/.test(sp) && !S.mmOffline) linkSheet(); } catch (e) {  }

      window.addEventListener("pagehide", () => {
        const a = M.audio; if (!a || a.paused || !current() || M.mode !== "phone" || !S.token || S.mmOffline) return;
        try { fetch("api/music.report", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json", Authorization: "Bearer " + S.token }, body: JSON.stringify(reportBody(false)) }); } catch (e) {  }
      });

      try { if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "127.0.0.1" || location.hostname === "localhost")) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {}); } catch (e) {  }
    }
    kit.hooks.afterBoot.push(init);

    kit.hooks.offlineBoot.push(() => {
      const count = Object.keys(M.offline).length;
      if (!count) return null;
      return { label: tr(`Слушать скачанное · ${count}`, `Play saved · ${count}`), run: () => {
        S.mmOffline = true; document.documentElement.classList.add("mm-offline-boot");
        $("#app").hidden = false;
        const boot = $("#boot"); if (boot) { boot.classList.add("out"); setTimeout(() => boot.remove(), 400); }
        M.sub = "device"; S.tab = "music";
        kit.renderTabbar(); kit.switchTab("music", { force: true });
        init();
      } };
    });

    kit.hooks.lookRows.push({
      html: () => `<div class="section-title">${esc(tr("Музыка", "Music"))}</div><div class="group flat"><label class="cell flat"><div class="cell-body"><div class="cell-title">${esc(tr("Облегчённые эффекты плеера", "Light player effects"))}</div><div class="cell-sub">${esc(tr("Без живого фона и размытия — меньше нагрузка на телефон", "No live background or blur — less load on the phone"))}</div></div><span class="switch"><input type="checkbox" id="mm-light" ${M.light ? "checked" : ""}><span></span></span></label></div>`,
      bind: (view) => { const box = $("#mm-light", view); if (box) box.onchange = () => { M.light = box.checked; save("memu_music_light", M.light); document.documentElement.classList.toggle("mm-light", M.light); haptic("select"); }; },
    });
    window.MemuMusicTg = { state: M, playQueue, openFull, closeFull, linkSheet, setMode, takeFromPc, sendToPc, saveOffline, dropOffline, storageInfo };
  });
})();
