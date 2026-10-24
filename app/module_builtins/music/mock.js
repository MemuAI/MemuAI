/* MemuMusic · mock.js — демо-библиотека без бэкенда (?mock=1 или нет Memu.music.call).
 * Все артисты и треки вымышлены; звук синтезируется в WAV прямо в окне. */
const MockMusic = (() => {
  const ARTISTS = ["Lumen Park", "Velvet Hours", "Северный Ветер", "Kairo Sun", "Neon Orchard", "Сонная Лиса", "Atlas Bloom", "Midnight Tram", "Mira Vale", "Polar Kites"];
  const WORDS = ["Golden", "Glass", "Static", "Ocean", "Paper", "Velvet", "Silver", "Northern", "Quiet", "Electric", "Lost", "Slow", "Neon", "Dream", "Ember"];
  const NOUNS = ["Hearts", "Lights", "Rivers", "Signals", "Summer", "Satellites", "Gardens", "Echoes", "Motion", "Skyline", "Tides", "Wires", "Cities", "Mornings"];
  const RU = ["Тёплый город", "Ночные огни", "Над крышами", "Последний трамвай", "Мятный рассвет", "Где-то рядом", "Стеклянный дом", "Снег в августе"];
  const GENRES = ["Electronic", "Indie", "Pop", "Lo-Fi", "Rock", "Синти-поп"];
  const SOURCES = ["youtube", "ytmusic", "spotify", "apple", "soundcloud", "local", "yandex", "deezer"];
  const LYRICS = [
    "City lights are calling out my name", "I keep on running through the rain", "Every heartbeat sounds like a song", "We were young and the nights were long",
    "Hold me close until the morning comes", "Under the glow of a thousand suns", "Ты рядом — и мир звучит иначе", "Мы танцуем там, где гаснет свет",
    "Turn it up, let the bassline breathe", "Nothing else but you and me", "Стрелки замерли на без пяти", "Сквозь туман я слышу твой голос",
    "Paint the sky in violet and gold", "Tell me stories that were never told", "Oh-oh, oh-oh", "And we fly, and we fly",
  ];
  const rnd = seeded(37);
  const pick = (list) => list[Math.floor(rnd() * list.length)];
  const now = Date.now();
  const tracks = [];
  const albums = {};
  ARTISTS.forEach((artist, a) => {
    const albumCount = 1 + (a % 2);
    for (let b = 0; b < albumCount; b++) {
      const album = a % 3 === 1 && b === 0 ? pick(RU) : pick(WORDS) + " " + pick(NOUNS);
      const n = 3 + Math.floor(rnd() * 3);
      for (let i = 0; i < n; i++) {
        const id = "m" + (tracks.length + 1);
        const title = (a % 3 === 1 && i % 2) ? pick(RU) : pick(WORDS) + " " + pick(NOUNS);
        const src = SOURCES[(a + i) % SOURCES.length];
        tracks.push({
          id, title, artist, album, duration: 150 + Math.floor(rnd() * 100), source: src,
          source_url: src === "local" ? "" : "https://example.com/" + id, cover: null, liked: rnd() < .28,
          added_at: new Date(now - Math.floor(rnd() * 60) * 86400000).toISOString(), plays: Math.floor(rnd() * 60),
          file_ext: src === "local" ? "mp3" : "m4a", bitrate: 160 + Math.floor(rnd() * 3) * 64,
        });
        albums[artist + "|" + album] = true;
      }
    }
  });
  let playlists = [
    { id: "p1", name: "Ночная дорога", track_ids: tracks.filter((_, i) => i % 3 === 0).map((t) => t.id).slice(0, 14) },
    { id: "p2", name: "Focus Flow", track_ids: tracks.filter((_, i) => i % 4 === 1).map((t) => t.id) },
    { id: "p3", name: "Утро ☀", track_ids: tracks.slice(5, 16).map((t) => t.id) },
  ];
  const jobs = [];
  let settings = { download_dir: "C:\\Users\\you\\Music\\MemuMusic", quality: "best" };
  const recent = tracks.slice().sort((x, y) => y.plays - x.plays).slice(0, 12).reverse();
  const byId = (id) => tracks.find((t) => t.id === id);
  const genreOf = (t) => GENRES[(hash(t.artist) + (t.title.length % 2)) % GENRES.length];
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const plMeta = (p) => {
    const items = p.track_ids.map(byId).filter(Boolean);
    return { id: p.id, name: p.name, cover: null, count: items.length, duration: items.reduce((s, t) => s + t.duration, 0), covers: items.slice(0, 4).map((t) => t.cover), track_ids: p.track_ids.slice() };
  };
  /** Умные плейлисты — как в бэкенде: «Недавно добавленные», «Самые слушаемые», «Любимые». */
  function smartLists() {
    return [["smart:recent", "Недавно добавленные", "Recently added", tracks.slice().sort((x, y) => y.added_at.localeCompare(x.added_at)).slice(0, 25)],
      ["smart:top", "Самые слушаемые", "Most played", tracks.filter((t) => t.plays > 0).sort((x, y) => y.plays - x.plays).slice(0, 25)],
      ["smart:liked", "Любимые", "Liked", tracks.filter((t) => t.liked)]].map(([id, ru, en, items]) => Object.assign(plMeta({ id, name: ru, track_ids: items.map((t) => t.id) }),
      { smart: true, name_ru: ru, name_en: en, cover: (items[0] && items[0].cover) || null, rules: null, hidden: false }));
  }
  function list(args) {
    let out = tracks.slice();
    const q = String(args.q || "").toLowerCase();
    if (q) out = out.filter((t) => (t.title + " " + t.artist + " " + t.album).toLowerCase().includes(q));
    if (args.filter === "liked") out = out.filter((t) => t.liked);
    if (args.filter === "downloaded") out = out.filter((t) => t.source !== "local");
    if (String(args.filter || "").startsWith("folder:")) out = out.filter((t) => args.filter.endsWith("\\" + t.artist));
    if (String(args.filter || "").startsWith("genre:")) out = out.filter((t) => genreOf(t) === args.filter.slice(6));
    const sort = args.sort || "added";
    const cmp = { title: (x, y) => x.title.localeCompare(y.title), artist: (x, y) => x.artist.localeCompare(y.artist) || x.album.localeCompare(y.album),
      plays: (x, y) => y.plays - x.plays, duration: (x, y) => x.duration - y.duration, added: (x, y) => y.added_at.localeCompare(x.added_at) }[sort];
    if (cmp) out.sort(cmp);
    const total = out.length, offset = args.offset || 0;
    return { tracks: clone(out.slice(offset, offset + (args.limit || 5000))), total };
  }
  function groups(key) {
    const map = new Map();
    tracks.forEach((t) => {
      const k = key === "artist" ? t.artist : t.artist + "|" + t.album;
      if (!map.has(k)) map.set(k, { key: k, name: key === "artist" ? t.artist : t.album, artist: t.artist, count: 0, duration: 0, cover: t.cover || null, plays: 0, track_ids: [] });
      const g = map.get(k); g.count++; g.duration += t.duration; g.plays += t.plays; g.track_ids.push(t.id);
    });
    return [...map.values()];
  }
  function stats() {
    const days = [];
    for (let i = 29; i >= 0; i--) {
      const d = new Date(now - i * 86400000);
      days.push({ date: d.toISOString().slice(0, 10), minutes: Math.round(20 + 50 * Math.abs(Math.sin(i * .7)) + rnd() * 40) });
    }
    const top = groups("artist").sort((a, b) => b.plays - a.plays).slice(0, 6).map((g) => ({ name: g.name, plays: g.plays, minutes: Math.round(g.plays * 3.2) }));
    const hours = Array.from({ length: 24 }, (_, h) => Math.round(4 + 30 * Math.max(0, Math.sin((h - 7) / 24 * Math.PI * 2)) + (h > 19 ? 22 : 0) + rnd() * 6));
    return { tracks: tracks.length, minutes: days.reduce((s, d) => s + d.minutes, 0), artists: ARTISTS.length, top_artists: top,
      top_tracks: clone(tracks.slice().sort((a, b) => b.plays - a.plays).slice(0, 8)), recent: clone(recent.slice().reverse()), days, hours };
  }
  function lyrics(id) {
    const t = byId(id); if (!t) return { synced: null, plain: null, source: null };
    if (Number(id.slice(1)) % 7 === 0) return { synced: null, plain: null, source: null };
    const r = seeded(hash(id));
    const synced = [];
    let at = 6;
    while (at < t.duration - 6) { synced.push([Math.round(at * 10) / 10, LYRICS[Math.floor(r() * LYRICS.length)]]); at += 3.2 + r() * 2.6; if (r() < .1) { synced.push([at, ""]); at += 4; } }
    return { synced, plain: synced.map((l) => l[1]).join("\n"), source: "lrclib", source_name: "LRCLIB (demo)", offset: 0, choice: 0,   // r39: варианты для демо
      alts: [{ index: 0, source: "lrclib", name: "LRCLIB", synced: true, words: false, duration: t.duration, title: t.title, artist: t.artist, lines: synced.length },
        { index: 1, source: "genius", name: "Genius", synced: false, words: false, duration: 0, title: t.title, artist: t.artist, lines: synced.length }] };
  }
  function tickJobs() {
    jobs.forEach((j) => {
      if (j.status === "queued") j.status = "running";
      else if (j.status === "running") {
        j.progress = Math.min(1, j.progress + .08 + Math.random() * .12);
        if (j.items) { const k = Math.floor(j.progress * j.items.length); j.items.forEach((x, i) => { x.status = i < k ? (i === 3 && !j.retried ? "error" : "done") : i === k ? "running" : "pending"; x.error = x.status === "error" ? "Видео недоступно в вашей стране" : ""; }); j.done = k; j.failed = j.items.filter((x) => x.status === "error").length; }
        if (j.progress >= 1) {
          j.status = "done";
          if (j.items) j.items.forEach((x) => { if (x.status !== "error") x.status = "done"; });
          const t = { id: "m" + (tracks.length + 1), title: j.title, artist: j.artist || "Unknown", album: j.title, duration: 200, source: j.source,
            source_url: j.url, cover: null, liked: false, added_at: new Date().toISOString(), plays: 0, file_ext: "m4a" };
          tracks.unshift(t); j.track_ids = [t.id];
        }
      }
    });
  }
  /* --- синтез звука: аккорды I–V–vi–IV, бас, бочка; WAV 11 кГц, моно ---------------- */
  const audioUrls = new Map();
  function synth(track) {
    if (audioUrls.has(track.id)) return audioUrls.get(track.id);
    const rate = 11025, seconds = Math.min(track.duration, 240), n = rate * seconds;
    const pcm = new Int16Array(n);
    const r = seeded(hash(track.id));
    const root = 196 * Math.pow(2, Math.floor(r() * 7) / 12);
    const bpm = 84 + Math.floor(r() * 40), beat = 60 / bpm, bar = beat * 4;
    const prog = [[0, 4, 7], [7, 11, 14], [9, 12, 16], [5, 9, 12]];
    const melody = Array.from({ length: 16 }, () => [0, 2, 4, 7, 9, 12][Math.floor(r() * 6)]);
    for (let i = 0; i < n; i++) {
      const t = i / rate, b = Math.floor(t / bar) % 4, inBar = t % bar, inBeat = t % beat;
      const chord = prog[b];
      let v = 0;
      const env = Math.min(1, inBar * 3) * (0.75 + 0.25 * Math.cos(inBar / bar * Math.PI * 2));
      for (const s of chord) v += Math.sin(2 * Math.PI * root * Math.pow(2, s / 12) * t) * .12 * env;
      v += Math.sin(2 * Math.PI * root / 2 * Math.pow(2, chord[0] / 12) * t) * .2 * Math.exp(-inBeat * 3);
      const step = Math.floor(t / (beat / 2)) % 16, inStep = t % (beat / 2);
      if (t > bar * 2) v += Math.sin(2 * Math.PI * root * 2 * Math.pow(2, (melody[step] + chord[0]) / 12) * t) * .08 * Math.exp(-inStep * 6);
      v += Math.sin(2 * Math.PI * (50 + 90 * Math.exp(-inBeat * 30)) * inBeat) * .5 * Math.exp(-inBeat * 9);
      const fade = Math.min(1, t / 2, (seconds - t) / 3);
      pcm[i] = Math.max(-1, Math.min(1, v * fade)) * 26000;
    }
    const buffer = new ArrayBuffer(44 + n * 2), view = new DataView(buffer);
    const str = (o, s) => [...s].forEach((c, k) => view.setUint8(o + k, c.charCodeAt(0)));
    str(0, "RIFF"); view.setUint32(4, 36 + n * 2, true); str(8, "WAVEfmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true); str(36, "data"); view.setUint32(40, n * 2, true);
    new Int16Array(buffer, 44).set(pcm);
    const url = URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
    if (audioUrls.size > 3) { const [k, u] = audioUrls.entries().next().value; URL.revokeObjectURL(u); audioUrls.delete(k); }
    audioUrls.set(track.id, url);
    return url;
  }
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  async function call(op, a) {
    await delay(op.startsWith("library.list") ? 60 : 30);
    switch (op) {
      case "library.list": return list(a);
      case "library.get": return clone(byId(a.id));
      case "library.update": Object.assign(byId(a.id) || {}, a); return clone(byId(a.id));
      case "library.delete": { const i = tracks.findIndex((t) => t.id === a.id); if (i >= 0) tracks.splice(i, 1); playlists.forEach((p) => p.track_ids = p.track_ids.filter((x) => x !== a.id)); return { ok: true }; }
      case "library.like": { const t = byId(a.id); if (t) t.liked = !!a.liked; return { ok: true, liked: !!a.liked }; }
      case "library.played": { const t = byId(a.id); if (t) t.plays++; return { ok: true }; }
      case "library.stats": return stats();
      case "artists.list": return groups("artist");
      case "albums.list": return groups("album");
      case "artist.get": return { name: a.name, tracks: clone(tracks.filter((t) => t.artist === a.name)) };
      case "album.get": return { key: a.key, tracks: clone(tracks.filter((t) => t.artist + "|" + t.album === a.key)) };
      case "playlists.list": return smartLists().concat(playlists.map(plMeta));
      case "playlist.get": { const sm = smartLists().find((x) => x.id === a.id); if (sm) return Object.assign(sm, { tracks: clone(sm.track_ids.map(byId).filter(Boolean)) });
        const p = playlists.find((x) => x.id === a.id); if (!p) throw new Error("not found"); return Object.assign(plMeta(p), { tracks: clone(p.track_ids.map(byId).filter(Boolean)) }); }
      case "playlist.create": { const p = { id: "p" + (Date.now() % 1e7), name: a.name || "Playlist", track_ids: (a.track_ids || []).slice() }; playlists.push(p); return plMeta(p); }
      case "playlist.update": {
        const p = playlists.find((x) => x.id === a.id); if (!p) throw new Error("not found");
        if (a.name) p.name = a.name;
        if (a.order || a.track_ids) p.track_ids = (a.order || a.track_ids).slice();
        if (a.add) p.track_ids.push(...[].concat(a.add).filter((x) => !p.track_ids.includes(x)));
        if (a.remove) p.track_ids = p.track_ids.filter((x) => ![].concat(a.remove).includes(x));
        return plMeta(p);
      }
      case "playlist.delete": playlists = playlists.filter((x) => x.id !== a.id); return { ok: true };
      case "detect": {
        const d = detectLocal(a.url); if (!d) throw new Error("not a link");
        return { source: d.source, kind: d.kind, title: d.kind === "track" ? "Midnight Tram — Glass Rivers" : "Summer Mix 2026", items_estimate: d.kind === "track" ? 1 : 24 };
      }
      case "download.start": {
        const d = detectLocal(a.url) || { source: "web", kind: "track" };
        const j = { job_id: "j" + Date.now(), url: a.url, source: d.source, kind: d.kind, title: d.kind === "track" ? "Glass Rivers" : "Summer Mix 2026", artist: "Midnight Tram", progress: 0, status: "queued", track_ids: [] };
        if (d.kind !== "track") { j.items = ["Glass Rivers", "Paper Planes", "Night Shift", "Golden Hour", "Low Tide", "Slow Motion"].map((title) => ({ title, artist: "Midnight Tram", status: "pending", error: "" })); j.total = j.items.length; }
        jobs.unshift(j); return { job_id: j.job_id };
      }
      case "download.jobs": tickJobs(); return clone(jobs);
      case "download.retry": { const j = jobs.find((x) => x.job_id === a.job_id); if (j) { j.retried = true; j.failed = 0; j.status = "running"; j.progress = .9; } return { job_id: a.job_id, retried: 1 }; }
      case "library.history": return clone(recent.slice().reverse().map((t, i) => ({ at: Date.now() / 1000 - i * 2400, seconds: Math.min(t.duration, 200), device: i % 4 === 1 ? "phone" : "pc", track: t })));
      case "download.cancel": { const j = jobs.find((x) => x.job_id === a.job_id); if (j && j.status !== "done") { j.status = "error"; j.error = "cancelled"; } return { ok: true }; }
      case "search.online": {
        const q = String(a.q || "").trim(); if (!q) return [];
        return Array.from({ length: Math.min(a.limit || 8, 8) }, (_, i) => ({ title: q + (i ? " (" + ["Live", "Remix", "Acoustic", "Extended", "Radio Edit", "Slowed", "Cover"][i - 1] + ")" : ""),
          artist: ARTISTS[(hash(q) + i) % ARTISTS.length], duration: 160 + i * 17, url: "https://music.youtube.com/watch?v=demo" + i, thumb: null, source: i % 3 ? "youtube" : "ytmusic" }));
      }
      case "lyrics.get": return lyrics(a.id);
      /* r41b-online: онлайн-поиск с площадкой, каналом и метками; лист «Информация о треке» */
      case "online.search": {
        const q = String(a.q || "").trim(); if (!q) return { results: [], source: a.source, via: "", error: "" };
        await delay(250);
        const kinds = [["", ["official_audio"]], [" (Official Video)", ["official_video"]], [" (Live at Montreux)", ["live"]], [" (Night Remix)", ["remix"]], [" (Acoustic Cover)", ["cover", "acoustic"]], [" (Slowed + Reverb)", ["slowed"]], [" (Lyric Video)", ["lyric"]], [" (Extended Mix)", ["extended"]]];
        const src = a.source || "ytmusic";
        return { source: src, via: "mock", error: "", results: kinds.slice(0, Math.min(a.limit || 8, 8)).map(([suf, tags], i) => {
          const artist = ARTISTS[(hash(q) + i) % ARTISTS.length], lib = i === 0 ? tracks.find((t) => t.title.toLowerCase().includes(q.toLowerCase())) || tracks[0] : null;
          return { id: "demo" + i, url: (src === "soundcloud" ? "https://soundcloud.com/demo/track-" : src === "youtube" ? "https://www.youtube.com/watch?v=demo" : "https://music.youtube.com/watch?v=demo") + i,
            title: q + (src === "ytmusic" ? suf.replace(/ \((Official Video|Lyric Video)\)/, "") : suf), raw_title: q + suf, artist, album: src === "ytmusic" ? ["Glass Rivers", "Night Shift", "Low Tide"][i % 3] : "",
            duration: 160 + i * 17, thumb: lib ? lib.cover : null, source: src, channel: src !== "youtube" ? artist : i === 0 ? artist + " - Topic" : i === 1 ? artist + "VEVO" : artist, verified: i % 3 !== 2,
            views: 1250000 * (8 - i) * (i + 1), likes: src === "soundcloud" ? 4200 * (8 - i) : 0, published: src === "ytmusic" ? "" : "20" + (14 + i) + "-0" + (1 + i) + "-12",
            explicit: i === 3, preview: src === "soundcloud" && i === 1, tags: src === "soundcloud" && i === 1 ? tags.concat("preview") : src === "ytmusic" && /official_video|lyric/.test(tags[0]) ? ["official_audio"] : tags, in_library: lib ? lib.id : "" };
        }) };
      }
      case "online.details": {
        await delay(600);
        const h = a.hint || {};
        return { url: a.url, source: h.source, title: h.title, artist: h.artist, album: h.album || "Glass Rivers", year: "2024", genre: "Indie Pop", duration: h.duration,
          channel: h.channel, verified: h.verified, followers: 1840000, published: h.published || "2024-03-15", views: h.views, likes: h.likes || 186000,
          description: "Official audio for «" + h.title + "» by " + h.artist + ".\nFrom the album «Glass Rivers» (2024).\n\nListen everywhere: https://example.com/glass-rivers\nProduced by Lumen Park · Mixed at Night Shift Studio · Mastered by Low Tide.\n\nLyrics, credits and tour dates on the official site.",
          formats: h.source === "soundcloud" ? [{ ext: "mp3", codec: "mp3", kbps: 128, khz: 44.1, size: 3900000 }, { ext: "opus", codec: "opus", kbps: 64, khz: 48, size: 1800000 }]
            : [{ ext: "webm", codec: "opus", kbps: 160, khz: 48, size: 4700000 }, { ext: "m4a", codec: "aac", kbps: 129, khz: 44.1, size: 3800000 }, { ext: "webm", codec: "opus", kbps: 70, khz: 48, size: 2100000 }],
          cover: "", thumb: "", explicit: !!h.explicit, meta_source: "itunes", meta_url: "", categories: ["Music"], errors: {}, related: true,
          tags: [], in_library: "" };   // метки уже пришли с результатом поиска
      }
      case "online.related": await delay(400); return ARTISTS.slice(0, 6).map((artist, i) => ({ id: "rel" + i, url: "https://music.youtube.com/watch?v=rel" + i, title: ["Paper Planes", "Night Shift", "Golden Hour", "Low Tide", "Slow Motion", "Neon Bloom"][i], artist, album: "", year: "2023", duration: 190 + i * 11, thumb: null, source: "ytmusic", tags: ["official_audio"], in_library: "" }));
      case "online.lyrics": await delay(300); return { found: true, text: "Glass rivers running through the night\nEvery light a little brighter\nHold the moment, hold it tight\nWe were paper, we were lighter\n\nAnd the city hums along\nTo the rhythm of our song", synced: true, source: "lrclib", instrumental: false };
      /* функции раунда 38 (жанры, папки, дубликаты, правила, обложки) — чтобы демо и ревизор видели настоящие экраны */
      case "library.genres": {
        const g = new Map();
        tracks.forEach((t) => { const name = genreOf(t); const x = g.get(name) || { name, tracks: 0, duration: 0, plays: 0, cover: t.cover, accent: "" }; x.tracks++; x.duration += t.duration; x.plays += t.plays; g.set(name, x); });
        return [...g.values()].sort((x, y) => y.tracks - x.tracks);
      }
      case "library.folders": {
        const g = new Map();
        tracks.forEach((t) => { const path = settings.download_dir + "\\" + t.artist; const x = g.get(path) || { path, name: t.artist, tracks: 0, duration: 0 }; x.tracks++; x.duration += t.duration; g.set(path, x); });
        return [...g.values()];
      }
      case "library.duplicates": return tracks.length > 6 ? [{ key: "dup", tracks: clone([tracks[2], Object.assign({}, tracks[2], { id: tracks[2].id + "b", bitrate: 128, file_ext: "mp3" })]) }] : [];
      case "library.update_many": (a.ids || []).forEach((id) => Object.assign(byId(id) || {}, a.changes || {})); return { ok: true, updated: (a.ids || []).length };
      case "playlist.preview": { const list = tracks.filter((t) => t.liked || t.plays > 30); return { count: list.length, tracks: clone(list.slice(0, 30)) }; }
      case "cover.search": return [];
      case "library.resume": return null;
      case "import.folder": return { job_id: "import" };
      case "settings.get": return clone(settings);
      case "settings.set": Object.assign(settings, a); return clone(settings);
      case "tools.status": return { ytdlp: { ok: true, version: "2026.09.30", path: "yt-dlp (demo)" }, ffmpeg: { ok: false, version: "", path: "" } };
      case "tools.update": await delay(900); return { ok: true, version: "2026.10.12" };
      case "stream_url": { const t = byId(a.id); return t ? synth(t) : ""; }
      case "cover_url": return null;
      default: throw new Error("mock: unknown op " + op);
    }
  }
  /** Заменить демо-библиотеку (скриншоты/ревизор): плейлисты, «недавние» и статистика остаются согласованными с новым списком. */
  function seed(list) {
    tracks.splice(0, tracks.length, ...clone(list));
    const ids = new Set(tracks.map((t) => t.id));
    playlists.forEach((p) => { p.track_ids = p.track_ids.filter((id) => ids.has(id)); });
    recent.splice(0, recent.length, ...tracks.slice().sort((x, y) => y.plays - x.plays).slice(0, 12).reverse());
  }
  return { call, seed };
})();
