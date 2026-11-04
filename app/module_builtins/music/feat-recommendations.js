/* Recommendations: tracks similar to yours, taken from the platform's own "related" lists (YouTube Music radio,
 * SoundCloud "recommended"). Seeds are sampled from the library by the chosen basis (liked, most played, recent…),
 * candidates are ranked by how many seeds suggest them and how high, then filtered (library, versions, artists,
 * hidden tracks). The search keeps running when you leave the page; results are kept between sessions. */
(() => {
  const language = () => typeof Memu !== "undefined" ? Memu.lang : App.lang;
  const L = (ru, en) => language() === "en" ? en : ru;
  const button = document.createElement("button");
  button.type = "button"; button.dataset.go = "recommendations";
  button.innerHTML = `${icon("sparkle")}<span>${esc(L("Рекомендации", "Recommendations"))}</span>`;
  $("#nav").insertBefore(button, $("#nav [data-go=settings]"));
  const relabel = () => { button.querySelector("span").textContent = L("Рекомендации", "Recommendations"); };
  if (typeof Memu !== "undefined" && Memu.onLanguage) Memu.onLanguage(relabel);
  if (typeof App !== "undefined" && App.onLanguage) App.onLanguage(relabel);

  const DEFAULTS = { basis: "mix", playlist: "", artist: "", source: "ytmusic", depth: 16, size: 60, perArtist: 2,
    noLibrary: true, noVersions: true, newArtists: false, preferNew: true };
  const BASES = ["mix", "liked", "top", "recent", "added", "library", "playlist", "artist"];
  const SOURCES = ["ytmusic", "soundcloud"];
  const VERSION_TAGS = ["live", "cover", "remix", "slowed", "sped_up", "karaoke", "instrumental"];
  const MAX_KEEP = 200, MAX_SHOWN = 1500, MAX_HIDDEN = 800, MAX_URLS = 600;
  const opt = () => {
    const o = S.prefs.recs && typeof S.prefs.recs === "object" ? S.prefs.recs : (S.prefs.recs = {});
    for (const k in DEFAULTS) if (o[k] === undefined || o[k] === null) o[k] = DEFAULTS[k];
    if (!BASES.includes(o.basis)) o.basis = "mix";
    if (!SOURCES.includes(o.source)) o.source = "ytmusic";
    return o;
  };
  const save = (patch) => { Object.assign(opt(), patch || {}); savePrefs(); };
  const list = (key) => Array.isArray(S.prefs[key]) ? S.prefs[key] : (S.prefs[key] = []);
  const norm = (s) => String(s || "").toLowerCase().replace(/ё/g, "е").replace(/\s*[(\[][^)\]]*[)\]]/g, " ").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const mainArtist = (s) => norm(String(s || "").replace(/\s*-\s*topic$/i, "").split(/\s*(?:,|&|;|\/|\s+feat\.?\s+|\s+ft\.?\s+|\s+x\s+|\s+и\s+)\s*/i)[0]);
  const keyOf = (r) => mainArtist(r.artist || r.channel) + "|" + norm(r.title);
  const basisName = (b) => ({ mix: L("Микс", "Mix"), liked: L("Любимые", "Liked"), top: L("Часто слушаю", "Most played"),
    recent: L("Недавние", "Recent"), added: L("Новое в медиатеке", "Recently added"), library: L("Вся медиатека", "Whole library"),
    playlist: L("Плейлист", "Playlist"), artist: L("Исполнитель", "Artist") })[b] || b;
  const ago = (ts) => {
    const m = Math.max(0, Math.round((Date.now() - ts) / 60000));
    if (m < 1) return L("только что", "just now");
    if (m < 60) return L(`${m} мин назад`, `${m} min ago`);
    const h = Math.round(m / 60);
    if (h < 24) return L(`${h} ч назад`, `${h} h ago`);
    return new Date(ts).toLocaleDateString(language() === "en" ? "en-US" : "ru-RU");
  };

  /* ---------- seeds ---------- */
  const libArtists = () => {
    const counts = new Map();
    for (const tr of S.tracks) { const a = mainArtist(tr.artist); if (a) counts.set(a, (counts.get(a) || 0) + 1); }
    return counts;
  };
  const artistChoices = () => {
    const names = new Map();
    for (const tr of S.tracks) { const a = String(tr.artist || "").trim(); if (!a) continue; const x = names.get(a) || { name: a, n: 0 }; x.n++; names.set(a, x); }
    return [...names.values()].sort((x, y) => y.n - x.n || x.name.localeCompare(y.name)).slice(0, 200);
  };
  async function seedPool(o) {
    const all = S.tracks.slice();
    const pools = {
      liked: all.filter((t) => t.liked),
      top: all.filter((t) => (t.plays || 0) > 0).sort((a, b) => (b.plays || 0) - (a.plays || 0) || (b.last_played || 0) - (a.last_played || 0)).slice(0, 50),
      recent: all.filter((t) => (t.last_played || 0) > 0).sort((a, b) => (b.last_played || 0) - (a.last_played || 0)).slice(0, 50),
      added: all.slice().sort((a, b) => (b.added_at || 0) - (a.added_at || 0)).slice(0, 50),
      library: all,
    };
    const weights = new Map(), add = (tracks, w) => tracks.forEach((t) => { if (t && t.id) weights.set(t.id, { track: t, w: (weights.get(t.id)?.w || 0) + w }); });
    if (o.basis === "mix") { add(pools.liked, 3); add(pools.top, 2.5); add(pools.recent, 2); add(pools.added, 1.2); add(pools.library, 1); }
    else if (o.basis === "playlist") {
      const full = o.playlist ? await API.soft("playlist.get", { id: o.playlist }, null) : null;
      add(((full && (full.tracks || full.items)) || []).map((x) => S.byId.get(x.id) || x), 1);
    } else if (o.basis === "artist") add(all.filter((t) => String(t.artist || "").trim() === o.artist), 1);
    else add(pools[o.basis] || all, 1);
    for (const t of pools.liked) if (weights.has(t.id)) weights.get(t.id).liked = true;
    return [...weights.values()];
  }
  /* Weighted sampling without replacement (A-Res): every refresh takes a different set of seeds. */
  function sample(pool, n) {
    return pool.map((x) => ({ x, k: Math.pow(Math.random(), 1 / Math.max(.01, x.w)) })).sort((a, b) => b.k - a.k).slice(0, n).map((y) => y.x);
  }

  /* ---------- the search ---------- */
  const job = { running: false, token: 0, done: 0, total: 0, found: 0, phase: "", listeners: new Set() };
  const notify = () => job.listeners.forEach((fn) => { try { fn(); } catch (_) {} });
  const withTimeout = (promise, ms, fallback) => { let timer; return Promise.race([promise, new Promise((ok) => { timer = setTimeout(() => ok(fallback), ms); })]).finally(() => clearTimeout(timer)); };
  const urlCache = () => (S.prefs.recsUrls && typeof S.prefs.recsUrls === "object" ? S.prefs.recsUrls : (S.prefs.recsUrls = {}));
  function rememberUrl(key, url) {
    const cache = urlCache();
    delete cache[key]; cache[key] = url;
    const keys = Object.keys(cache);
    for (let i = 0; i < keys.length - MAX_URLS; i++) delete cache[keys[i]];
  }
  async function seedUrl(track, source) {
    const own = String(track.source_url || "");
    if (source === "ytmusic" && /(?:youtube\.com|youtu\.be)/i.test(own)) return own;
    if (source === "soundcloud" && /soundcloud\.com/i.test(own)) return own;
    const ck = source + ":" + track.id;
    if (urlCache()[ck]) return urlCache()[ck];
    const q = `${track.artist || ""} ${track.title || ""}`.trim();
    if (!q) return "";
    const found = await withTimeout(API.soft("online.search", { q, limit: 5, source }, null), 40000, null);
    const rows = (found && found.results) || [];
    const want = norm(track.title), artist = mainArtist(track.artist);
    const best = rows.find((r) => norm(r.title).includes(want) && (!artist || norm(`${r.artist} ${r.channel}`).includes(artist))) || rows.find((r) => norm(r.title).includes(want)) || rows[0];
    if (best && best.url) rememberUrl(ck, best.url);
    return (best && best.url) || "";
  }
  async function refresh() {
    if (job.running) return;
    const o = opt();
    const token = ++job.token;
    const stopped = new Promise((ok) => { job.stop = ok; });
    Object.assign(job, { running: true, cancelled: false, done: 0, total: 0, found: 0, phase: "seeds", error: "" }); notify();
    try {
      const pool = await seedPool(o);
      if (!pool.length) throw new Error(o.basis === "mix" || o.basis === "library" ? L("Добавьте треки в медиатеку, чтобы получить рекомендации.", "Add tracks to your library to get recommendations.") : L("Для выбранной основы нет треков — выберите другую.", "The chosen basis has no tracks — pick another one."));
      const seeds = sample(pool, Math.max(4, Math.min(60, Number(o.depth) || 16)));
      Object.assign(job, { total: seeds.length, phase: "related" }); notify();
      const owned = new Set(S.tracks.map((x) => keyOf(x))), artists = libArtists();
      const hidden = new Set(list("recsHidden")), hiddenArtists = new Set(list("recsHiddenArtists")), shown = new Set(list("recsShown"));
      const cands = new Map(), limit = 25;
      let next = 0;
      const worker = async () => {
        while (next < seeds.length && job.token === token) {
          const seed = seeds[next++];
          try {
            const url = await seedUrl(seed.track, o.source);
            const related = url && job.token === token ? (await withTimeout(API.soft("online.related", { url, limit }, []), 60000, [])) || [] : [];
            related.forEach((r, i) => {
              if (!r || !r.url || !r.title) return;
              const key = keyOf(r);
              if (key === keyOf(seed.track)) return;
              const gain = (seed.liked ? 1.35 : 1) * (1 - i / (limit * 1.6));
              const c = cands.get(key) || { r, key, score: 0, seeds: [] };
              c.score += gain;
              if (!c.seeds.some((s) => s.id === seed.track.id)) c.seeds.push({ id: seed.track.id, title: seed.track.title || "", artist: seed.track.artist || "" });
              if (!c.r.thumb && r.thumb) c.r = Object.assign({}, c.r, { thumb: r.thumb });
              cands.set(key, c);
            });
          } catch (_) {}
          if (job.token !== token) return;
          job.done++; job.found = cands.size; notify();
        }
      };
      /* «Stop» ranks what has been found so far right away; calls still in flight are ignored. */
      await Promise.race([Promise.all(Array.from({ length: Math.min(3, seeds.length) }, worker)), stopped]);
      if (job.token !== token && !job.cancelled) return;
      const perArtist = Number(o.perArtist) || 0, size = Math.max(10, Math.min(MAX_KEEP, Number(o.size) || 60));
      const ranked = [...cands.values()].map((c) => {
        let score = c.score + .25 * (c.seeds.length - 1);
        if (o.preferNew && shown.has(c.key)) score *= .4;
        return Object.assign(c, { final: score });
      }).sort((a, b) => b.final - a.final);
      const picked = [], perCount = new Map();
      for (const c of ranked) {
        const r = c.r, artist = mainArtist(r.artist || r.channel);
        if (hidden.has(c.key) || (artist && hiddenArtists.has(artist))) continue;
        if (o.noLibrary && (r.in_library || owned.has(c.key) || (typeof onlLibTrack === "function" && onlLibTrack(r)))) continue;
        if (o.noVersions && (r.tags || []).some((tag) => VERSION_TAGS.includes(tag))) continue;
        if (o.newArtists && artist && artists.has(artist)) continue;
        if (r.duration && (r.duration < 60 || r.duration > 900)) continue;
        if (perArtist && artist) { const n = perCount.get(artist) || 0; if (n >= perArtist) continue; perCount.set(artist, n + 1); }
        picked.push(Object.assign({}, r, { recSeeds: c.seeds.slice(0, 3), recCount: c.seeds.length }));
        if (picked.length >= size) break;
      }
      if (!picked.length) {
        if (job.cancelled) return;
        throw new Error(cands.size ? L("Все найденные треки отсеяны фильтрами — смягчите их в «Настроить».", "Every track found was filtered out — relax the filters in “Customize”.")
          : L("Похожих треков не нашлось. Проверьте подключение к интернету или выберите другую основу.", "No similar tracks found. Check your connection or pick another basis."));
      }
      const keep = picked.map((r) => {
        const out = {};
        ["id", "url", "title", "artist", "album", "year", "duration", "thumb", "source", "channel", "verified", "views", "published", "explicit", "preview", "tags", "in_library", "recSeeds", "recCount"].forEach((k) => { if (r[k] !== undefined && r[k] !== "") out[k] = r[k]; });
        return out;
      });
      S.prefs.recommendations = keep;
      S.prefs.recsMeta = { at: Date.now(), basis: o.basis, seeds: seeds.length, source: o.source, partial: !!job.cancelled, raw: cands.size };
      S.prefs.recsShown = [...new Set([...list("recsShown"), ...keep.map(keyOf)])].slice(-MAX_SHOWN);
      savePrefs();
    } catch (error) {
      job.error = String((error && error.message) || error);
    } finally {
      if (job.token === token || job.cancelled) { job.running = false; job.cancelled = false; job.phase = ""; notify(); }
    }
  }
  function cancel() {
    if (!job.running) return;
    job.cancelled = true; job.token++;
    if (job.stop) job.stop();
    notify();
  }

  /* ---------- view ---------- */
  const onlineTrack = (r) => (typeof onlLibTrack === "function" && onlLibTrack(r)) || { id: "online:" + (r.id || r.url), title: r.title, artist: r.artist || r.channel || "", album: r.album || "", cover: r.thumb || "", duration: r.duration || 0, online_url: r.url };
  const seg = (name, values, current, labelOf) => `<div class="mm-seg2" role="radiogroup" data-rec-seg="${name}">${values.map((v) => `<button type="button" role="radio" data-v="${esc(String(v))}" aria-checked="${String(current) === String(v)}">${esc(labelOf(v))}</button>`).join("")}</div>`;
  const sw = (name, on) => `<label class="mm-switch"><input type="checkbox" data-rec-sw="${name}" ${on ? "checked" : ""}><i></i></label>`;
  const field = (title, hint, control) => `<div class="mm-rec-field"><div class="mm-rec-label"><b>${esc(title)}</b>${hint ? `<span>${esc(hint)}</span>` : ""}</div>${control}</div>`;

  function tuneHtml(o) {
    const pls = (S.playlists || []).filter((p) => p && p.id);
    const artists = artistChoices();
    const pick = o.basis === "playlist" ? `<select class="mm-select" data-rec-pl aria-label="${esc(L("Плейлист", "Playlist"))}">${pls.length ? pls.map((p) => `<option value="${esc(p.id)}" ${p.id === o.playlist ? "selected" : ""}>${esc(p.name)}</option>`).join("") : `<option value="">${esc(L("Плейлистов пока нет", "No playlists yet"))}</option>`}</select>`
      : o.basis === "artist" ? `<select class="mm-select" data-rec-artist aria-label="${esc(L("Исполнитель", "Artist"))}">${artists.length ? artists.map((a) => `<option value="${esc(a.name)}" ${a.name === o.artist ? "selected" : ""}>${esc(a.name)} · ${a.n}</option>`).join("") : `<option value="">${esc(L("Нет исполнителей", "No artists"))}</option>`}</select>` : "";
    const hiddenN = list("recsHidden").length + list("recsHiddenArtists").length;
    return `<div class="mm-rec-grid">
      ${field(L("На основе", "Based on"), L("Из каких ваших треков брать отправные точки", "Which of your tracks to start from"), `<div class="mm-rec-basis">${seg("basis", BASES, o.basis, basisName)}${pick}</div>`)}
      ${field(L("Источник", "Source"), "", seg("source", SOURCES, o.source, (s) => typeof sourceName === "function" ? sourceName(s) : s))}
      ${field(L("Глубина поиска", "Search depth"), L("Сколько ваших треков взять за основу", "How many of your tracks to use"), seg("depth", [8, 16, 32], o.depth, (v) => v === 8 ? L("Быстро", "Quick") : v === 16 ? L("Обычно", "Normal") : L("Глубоко", "Deep")))}
      ${field(L("Размер подборки", "Mix size"), "", seg("size", [30, 60, 120], o.size, (v) => L(`${v} треков`, `${v} tracks`)))}
      ${field(L("Треков одного исполнителя", "Tracks per artist"), "", seg("perArtist", [1, 2, 3, 0], o.perArtist, (v) => v ? L(`до ${v}`, `up to ${v}`) : L("без ограничений", "no limit")))}
    </div>
    <div class="mm-rec-switches">
      <label class="mm-rec-sw">${sw("noLibrary", o.noLibrary)}<span><b>${esc(L("Без треков из медиатеки", "Skip tracks you have"))}</b></span></label>
      <label class="mm-rec-sw">${sw("noVersions", o.noVersions)}<span><b>${esc(L("Только оригиналы", "Originals only"))}</b><small>${esc(L("без ремиксов, концертных версий, каверов и ускоренных", "no remixes, live versions, covers or sped-up edits"))}</small></span></label>
      <label class="mm-rec-sw">${sw("newArtists", o.newArtists)}<span><b>${esc(L("Только новые исполнители", "New artists only"))}</b><small>${esc(L("которых нет в медиатеке", "not in your library"))}</small></span></label>
      <label class="mm-rec-sw">${sw("preferNew", o.preferNew)}<span><b>${esc(L("Сначала непоказанное", "Unseen first"))}</b><small>${esc(L("уже предложенное опускается ниже", "already suggested tracks rank lower"))}</small></span></label>
    </div>
    <div class="mm-rec-tune-foot"><span>${esc(hiddenN ? L(`Скрыто: ${list("recsHidden").length} треков, ${list("recsHiddenArtists").length} исполнителей`, `Hidden: ${list("recsHidden").length} tracks, ${list("recsHiddenArtists").length} artists`) : L("Скрытых треков нет — нажмите «Не интересно» у трека, чтобы он больше не попадался.", "Nothing hidden — use “Not interested” on a track to stop seeing it."))}</span>${hiddenN ? `<button type="button" class="mm-btn ghost sm" data-rec-unhide>${icon("undo")}${esc(L("Вернуть скрытые", "Unhide all"))}</button>` : ""}</div>`;
  }

  VIEWS.recommendations = async (host) => {
    const o = opt();
    const tuneOpen = !!S.prefs.recsTuneOpen;
    host.innerHTML = `<section class="mm-section mm-rec">
      <header class="mm-rec-head"><h1 class="mm-h1">${esc(L("Рекомендации", "Recommendations"))}</h1>
        <p class="mm-sub">${esc(L("Похожие треки, которые сама платформа предлагает к вашим. Чем больше ваших треков указывают на трек, тем выше он в списке.", "Similar tracks the platform itself suggests for yours. The more of your tracks point to a track, the higher it ranks."))}</p></header>
      <div class="mm-rec-bar">
        <button type="button" class="mm-btn tint" data-rec-run>${icon("refresh")}${esc(L("Обновить подборку", "Refresh recommendations"))}</button>
        <button type="button" class="mm-btn ghost" data-rec-play>${icon("play")}${esc(L("Слушать все", "Play all"))}</button>
        <button type="button" class="mm-btn ghost" data-rec-shuffle>${icon("shuffle")}${esc(L("Перемешать", "Shuffle"))}</button>
        <span class="mm-grow"></span>
        <button type="button" class="mm-chip" data-rec-tune aria-expanded="${tuneOpen}" aria-pressed="${tuneOpen}">${icon("rule")}${esc(L("Настроить", "Customize"))}</button>
      </div>
      <div class="mm-rec-tune" ${tuneOpen ? "" : "hidden"}></div>
      <div class="mm-rec-status" aria-live="polite"><div class="mm-rec-progress" hidden><i></i></div><span data-rec-status></span><button type="button" class="mm-btn ghost sm" data-rec-cancel hidden>${icon("close")}${esc(L("Остановить", "Stop"))}</button></div>
      <div class="mm-rec-filter" role="tablist" hidden></div>
      <div class="mm-online mm-rec-list" data-rec-results></div>
    </section>`;
    const tune = $(".mm-rec-tune", host), listBox = $("[data-rec-results]", host), status = $("[data-rec-status]", host);
    const bar = $(".mm-rec-progress", host), stop = $("[data-rec-cancel]", host), run = $("[data-rec-run]", host), filterBox = $(".mm-rec-filter", host);
    let filter = "all";
    const drawTune = () => { tune.innerHTML = tuneHtml(opt()); };
    if (tuneOpen) drawTune();
    const rows = () => Array.isArray(S.prefs.recommendations) ? S.prefs.recommendations : [];
    const visible = () => {
      const artists = libArtists();
      const all = rows();
      return filter === "new" ? all.filter((r) => !artists.has(mainArtist(r.artist || r.channel))) : filter === "known" ? all.filter((r) => artists.has(mainArtist(r.artist || r.channel))) : all;
    };
    function why(r) {
      const s = r.recSeeds || [];
      if (!s.length) return "";
      const first = `«${s[0].title}»${s[0].artist ? " — " + s[0].artist : ""}`;
      const more = (r.recCount || s.length) - 1;
      return more > 0 ? L(`Похоже на ${first} и ещё ${more}`, `Like ${first} and ${more} more`) : L(`Похоже на ${first}`, `Like ${first}`);
    }
    function drawFilter() {
      const all = rows();
      if (!all.length) { filterBox.hidden = true; return; }
      const artists = libArtists(), fresh = all.filter((r) => !artists.has(mainArtist(r.artist || r.channel))).length;
      filterBox.hidden = false;
      filterBox.innerHTML = [["all", L("Все", "All"), all.length], ["new", L("Новые исполнители", "New artists"), fresh], ["known", L("Знакомые исполнители", "Familiar artists"), all.length - fresh]]
        .map(([k, label, n]) => `<button type="button" role="tab" class="mm-chip" data-f="${k}" aria-selected="${filter === k}" aria-pressed="${filter === k}">${esc(label)}<em>${n}</em></button>`).join("");
    }
    function draw() {
      const shown = visible();
      drawFilter();
      if (!rows().length) {
        listBox.innerHTML = job.running ? "" : `<div class="mm-rec-empty">${icon("sparkle")}<b>${esc(L("Подборки пока нет", "No recommendations yet"))}</b><span>${esc(S.tracks.length ? L("Нажмите «Обновить подборку» — поиск займёт до минуты.", "Press “Refresh recommendations” — it takes up to a minute.") : L("Добавьте треки в медиатеку, чтобы получить рекомендации.", "Add tracks to your library to get recommendations."))}</span></div>`;
        return;
      }
      listBox.innerHTML = shown.map((r, i) => `<div class="mm-rec-item">${onlineRow(r, i).replace('<div class="mm-oacts">', `<div class="mm-oacts">${!(typeof onlLibTrack === "function" && onlLibTrack(r)) ? `<button type="button" class="mm-btn ghost sm" data-play>${icon("play")}${esc(t("play"))}</button>` : ""}<button type="button" class="mm-icon sm" data-hide title="${esc(L("Не интересно", "Not interested"))}" aria-label="${esc(L("Не интересно", "Not interested"))}">${icon("eye-off")}</button>`)}${why(r) ? `<span class="mm-rec-why">${icon("radio")}<span>${esc(why(r))}</span></span>` : ""}</div>`).join("") || `<p class="mm-sub">${esc(L("В этом фильтре ничего нет.", "Nothing in this filter."))}</p>`;
      $$("img[data-fb]", listBox).forEach((img) => { img.onerror = () => { img.onerror = null; img.src = img.dataset.fb; }; });
      $$(".mm-rec-item", listBox).forEach((item) => { const line = $(".mm-rec-why", item), txt = $(".mm-otxt", item); if (line && txt) txt.appendChild(line); });
    }
    function hide(r, artist) {
      if (artist) { const a = mainArtist(r.artist || r.channel); if (a) S.prefs.recsHiddenArtists = [...new Set([...list("recsHiddenArtists"), a])].slice(-MAX_HIDDEN); }
      else S.prefs.recsHidden = [...new Set([...list("recsHidden"), keyOf(r)])].slice(-MAX_HIDDEN);
      const a = mainArtist(r.artist || r.channel), k = keyOf(r);
      S.prefs.recommendations = rows().filter((x) => artist ? mainArtist(x.artist || x.channel) !== a : keyOf(x) !== k);
      savePrefs(); draw(); if (!tune.hidden) drawTune();
      toast(artist ? L(`Исполнитель «${r.artist || r.channel}» больше не будет предлагаться`, `“${r.artist || r.channel}” will no longer be suggested`) : L("Трек скрыт из рекомендаций", "Track hidden from recommendations"));
    }
    function menu(r, anchor) {
      openMenu([
        { icon: "play", label: t("play"), run: () => onlineListen(r, {}) },
        { icon: "queue", label: t("menu.add_queue"), run: () => onlineQueue(r, {}) },
        { icon: "playlist", label: t("search.to_playlist"), run: () => onlineToPlaylist(r, anchor) },
        "-",
        { icon: "radio", label: t("onl.similar"), run: () => onlineSheet(r, {}, "similar") },
        { icon: "lyrics", label: t("onl.find_lyrics"), run: () => onlineSheet(r, {}, "lyrics") },
        { icon: "link", label: t("onl.open", { source: sourceName(r.source) }), run: () => onlineOpen(r) },
        "-",
        { icon: "eye-off", label: L("Не интересно", "Not interested"), run: () => hide(r, false) },
        { icon: "user", label: L("Не предлагать исполнителя", "Don't suggest this artist"), run: () => hide(r, true) },
      ], anchor);
    }
    function playAll(shuffle) {
      const tracks = visible().map(onlineTrack);
      if (!tracks.length) { toast(L("Сначала обновите подборку", "Refresh the recommendations first")); return; }
      if (shuffle) for (let i = tracks.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [tracks[i], tracks[j]] = [tracks[j], tracks[i]]; }
      Player.playList(tracks, tracks[0].id, L("Рекомендации", "Recommendations"));
    }
    function paint() {
      if (!host.isConnected) { job.listeners.delete(paint); return; }
      run.disabled = job.running;
      bar.hidden = stop.hidden = !job.running;
      if (job.running) {
        const pct = job.total ? Math.round(job.done / job.total * 100) : 4;
        bar.firstElementChild.style.width = Math.max(4, pct) + "%";
        status.textContent = job.phase === "seeds" ? L("Выбираем отправные треки…", "Picking seed tracks…")
          : L(`Ищем похожее: ${job.done} из ${job.total} · найдено ${job.found}`, `Finding similar: ${job.done} of ${job.total} · found ${job.found}`);
        if (!rows().length) listBox.innerHTML = Array.from({ length: 4 }, () => '<div class="mm-skel mm-oskel"></div>').join("");
        return;
      }
      const meta = S.prefs.recsMeta;
      status.textContent = job.error ? job.error : rows().length && meta && meta.at
        ? L(`${rows().length} треков · ${basisName(meta.basis)}, ${meta.seeds} отправных · ${ago(meta.at)}${meta.partial ? " · поиск остановлен" : ""}`, `${rows().length} tracks · ${basisName(meta.basis)}, ${meta.seeds} seeds · ${ago(meta.at)}${meta.partial ? " · stopped early" : ""}`)
        : rows().length ? L(`Найдено: ${rows().length}`, `Found: ${rows().length}`) : (meta && meta.at ? L("Похожих треков не нашлось — смягчите фильтры или выберите другую основу.", "No similar tracks found — relax the filters or pick another basis.") : "");
      status.classList.toggle("mm-rec-error", !!job.error);
      draw();
    }
    job.listeners.add(paint);
    host.addEventListener("click", (e) => {
      const t0 = e.target;
      if (t0.closest("[data-rec-run]")) { refresh(); return; }
      if (t0.closest("[data-rec-cancel]")) { cancel(); return; }
      if (t0.closest("[data-rec-play]")) { playAll(false); return; }
      if (t0.closest("[data-rec-shuffle]")) { playAll(true); return; }
      if (t0.closest("[data-rec-unhide]")) { S.prefs.recsHidden = []; S.prefs.recsHiddenArtists = []; savePrefs(); drawTune(); toast(L("Скрытые треки вернутся при следующем обновлении", "Hidden tracks will return on the next refresh")); return; }
      const tb = t0.closest("[data-rec-tune]");
      if (tb) { const open = tune.hidden; tune.hidden = !open; if (open) drawTune(); tb.setAttribute("aria-expanded", String(open)); tb.setAttribute("aria-pressed", String(open)); S.prefs.recsTuneOpen = open; savePrefs(); return; }
      const sb = t0.closest("[data-rec-seg] [data-v]");
      if (sb) {
        const name = sb.closest("[data-rec-seg]").dataset.recSeg, raw = sb.dataset.v;
        const value = /^-?\d+$/.test(raw) ? Number(raw) : raw;
        const patch = { [name]: value };
        if (name === "basis" && value === "playlist" && !opt().playlist) patch.playlist = ((S.playlists || [])[0] || {}).id || "";
        if (name === "basis" && value === "artist" && !opt().artist) patch.artist = (artistChoices()[0] || {}).name || "";
        save(patch); drawTune(); return;
      }
      const fb = t0.closest(".mm-rec-filter [data-f]");
      if (fb) { filter = fb.dataset.f; draw(); return; }
      const row = t0.closest("[data-k]");
      if (!row || !listBox.contains(row)) return;
      const item = visible()[Number(row.dataset.k)]; if (!item) return;
      if (t0.closest("[data-hide]")) hide(item, false);
      else if (t0.closest("[data-play]")) onlineListen(item, {});
      else if (t0.closest("[data-dl]")) onlineDownload(item, {}, () => { const b = $("[data-dl]", row); if (b) b.disabled = true; });
      else if (t0.closest("[data-more]")) menu(item, t0.closest("[data-more]"));
      else onlineSheet(item, {});
    });
    host.addEventListener("change", (e) => {
      const el = e.target;
      if (el.matches("[data-rec-sw]")) save({ [el.dataset.recSw]: el.checked });
      else if (el.matches("[data-rec-pl]")) save({ playlist: el.value });
      else if (el.matches("[data-rec-artist]")) save({ artist: el.value });
    });
    listBox.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches(".mm-orow")) { e.preventDefault(); const r = visible()[Number(e.target.dataset.k)]; if (r) onlineSheet(r, {}); }
    });
    paint();
  };
})();
