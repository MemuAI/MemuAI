

function markText(root, query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter((w) => w.length > 0).slice(0, 6);
  if (!words.length) return;
  const re = new RegExp("(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  $$(".t b, .t span, .al, .mm-search-hit b, .mm-search-hit span", root).forEach((node) => {
    if (node.children.length || !re.test(node.textContent)) return;
    re.lastIndex = 0;
    node.innerHTML = esc(node.textContent).replace(re, "<mark>$1</mark>");
  });
}

VIEWS.search = async (el, p) => {
  const f = Object.assign({ q: (p && p.q) || "", source: "", genre: "", liked: false, years: "" }, S.prefs.searchFilters || {}, { q: (p && p.q) || "" });
  const sources = [...new Set(S.tracks.map((x) => x.source || "local"))];
  const genres = [...new Set(S.tracks.flatMap((x) => String(x.genre || "").split(/;|\/|, /).map((g) => g.trim()).filter(Boolean)))].sort((a, b) => a.localeCompare(b)).slice(0, 60);
  el.innerHTML = `<h1 class="mm-h1">${esc(t("gsearch.title"))}</h1>
    <div class="mm-search mm-search-big">${icon("search")}<input class="mm-input" id="gs-q" type="search" value="${esc(f.q)}" placeholder="${esc(t("search.ph"))}" autocomplete="off"></div>
    <div class="mm-toolbar mm-search-filters">
      <button type="button" class="mm-chip" data-liked aria-pressed="${f.liked}">${icon("heart")}${esc(t("filter.liked"))}</button>
      <select class="mm-select" id="gs-src" aria-label="${esc(t("col.source"))}"><option value="">${esc(t("search.any_source"))}</option>${sources.map((s) => `<option value="${esc(s)}"${f.source === s ? " selected" : ""}>${esc(sourceName(s))}</option>`).join("")}</select>
      ${genres.length ? `<select class="mm-select" id="gs-genre" aria-label="${esc(t("ed.genre"))}"><option value="">${esc(t("search.any_genre"))}</option>${genres.map((g) => `<option value="${esc(g)}"${f.genre === g ? " selected" : ""}>${esc(g)}</option>`).join("")}</select>` : ""}
      <select class="mm-select" id="gs-years" aria-label="${esc(t("ed.year"))}"><option value="">${esc(t("search.any_year"))}</option>${["2020-2099", "2010-2019", "2000-2009", "1990-1999", "1980-1989", "0-1979"].map((y) => `<option value="${y}"${f.years === y ? " selected" : ""}>${esc(y === "0-1979" ? "< 1980" : y.startsWith("2020") ? "2020+" : y.replace("-", "–"))}</option>`).join("")}</select>
    </div>
    <div id="gs-body"></div>`;
  const body = el.querySelector("#gs-body"), q = el.querySelector("#gs-q");
  const matches = (x, words) => { const hay = [x.title, x.artist, x.album, x.album_artist, x.genre, x.year].join(" ").toLowerCase(); return words.every((w) => hay.includes(w)); };
  const draw = () => {
    f.q = q.value.trim();
    S.prefs.searchFilters = { source: f.source, genre: f.genre, liked: f.liked, years: f.years }; savePrefs();
    const words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
    const [y0, y1] = f.years ? f.years.split("-").map(Number) : [0, 0];
    const list = S.tracks.filter((x) => (!words.length || matches(x, words)) && (!f.liked || x.liked) && (!f.source || (x.source || "local") === f.source)
      && (!f.genre || String(x.genre || "").toLowerCase().split(/;|\/|, /).map((g) => g.trim()).includes(f.genre.toLowerCase()))
      && (!f.years || (Number(String(x.year).slice(0, 4)) >= y0 && Number(String(x.year).slice(0, 4)) <= y1)));
    const filtered = words.length || f.liked || f.source || f.genre || f.years;
    if (!filtered) { body.innerHTML = emptyState("search", t("search.start"), t("search.start_hint")); return; }
    const artists = words.length ? artistsOf(list).filter((a) => words.every((w) => a.name.toLowerCase().includes(w))).slice(0, 8) : [];
    const albums = words.length ? albumsOf(list.filter((x) => x.album)).filter((a) => words.every((w) => (a.name + " " + a.artist).toLowerCase().includes(w))).slice(0, 8) : [];
    const pls = words.length ? S.playlists.filter((pl) => words.every((w) => pl.name.toLowerCase().includes(w))).slice(0, 6) : [];
    body.innerHTML = `${artists.length || albums.length || pls.length ? `<div class="mm-search-hits">${artists.map((a, i) => `<button type="button" class="mm-search-hit" data-artist="${i}"><span class="mm-art round">${artImg(a.tracks[0])}</span><span><b>${esc(a.name)}</b><span>${esc(t("kind.artist"))}</span></span></button>`).join("")}
      ${albums.map((a, i) => `<button type="button" class="mm-search-hit" data-album="${i}"><span class="mm-art">${artImg(a.tracks[0], "album")}</span><span><b>${esc(a.name)}</b><span>${esc(t("kind.album"))} · ${esc(a.artist)}</span></span></button>`).join("")}
      ${pls.map((pl) => `<button type="button" class="mm-search-hit" data-pl="${esc(pl.id)}"><span class="mm-art">${collage(pl)}</span><span><b>${esc(pl.name)}</b><span>${esc(t("kind.playlist"))}</span></span></button>`).join("")}</div>` : ""}
      <div class="mm-section-head"><h2 class="mm-h2">${esc(t("search.tracks"))}</h2><span class="mm-sub">${esc(plural("count.tracks", list.length))}</span></div><div id="gs-list"></div>
      ${f.q ? `<section class="mm-section"><div class="mm-section-head"><h2 class="mm-h2">${esc(t("search.online"))}</h2><button type="button" class="mm-btn ghost sm" id="gs-online">${icon("search")}${esc(t("search.online_go", { q: f.q }))}</button></div><div id="gs-online-list"></div></section>` : ""}`;
    const host = body.querySelector("#gs-list");
    if (list.length) { trackList(host, list.slice(0, 400), { from: t("gsearch.title"), virtual: false }); markText(host, f.q); }
    else host.innerHTML = `<p class="mm-sub">${esc(t("empty.nothing"))}</p>`;
    markText(body.querySelector(".mm-search-hits") || body, f.q);
    body.onclick = (e) => {
      const a = e.target.closest("[data-artist]"); if (a) { go("artist", { name: artists[Number(a.dataset.artist)].name }); return; }
      const al = e.target.closest("[data-album]"); if (al) { go("album", { key: albums[Number(al.dataset.album)].key }); return; }
      const pl = e.target.closest("[data-pl]"); if (pl) { go("playlist", { id: pl.dataset.pl }); return; }
      if (e.target.closest("#gs-online")) onlineSearch(body.querySelector("#gs-online-list"), f.q);
    };
  };
  q.addEventListener("input", debounce(draw, 140));
  el.querySelector("[data-liked]").onclick = (e) => { f.liked = !f.liked; e.currentTarget.setAttribute("aria-pressed", String(f.liked)); draw(); };
  el.querySelector("#gs-src").onchange = (e) => { f.source = e.target.value; draw(); };
  const g = el.querySelector("#gs-genre"); if (g) g.onchange = (e) => { f.genre = e.target.value; draw(); };
  el.querySelector("#gs-years").onchange = (e) => { f.years = e.target.value; draw(); };
  draw();
  q.focus();
};

function onlineToPlaylist(result, anchor) {
  const own = S.playlists.filter((p) => !p.smart && !p.rules);
  const save = async (pid, name) => {
    try { await API.call("playlist.save_online", { url: result.url, playlist_id: pid }); toast(t("search.saving", { title: result.title, name })); pollJobs(); }
    catch (err) { toast(String(err.message || err)); }
  };
  openMenu([{ icon: "plus", label: t("playlist.new"), run: async () => {
    const name = result.artist ? result.artist : t("playlist.default_name", { n: S.playlists.length + 1 });
    const p = await API.call("playlist.create", { name, track_ids: [] });
    await libraryChanged({ now: true, playlistsOnly: true });
    save(p.id, name);
  } }, own.length ? "-" : null].concat(own.map((p) => ({ icon: "playlist", label: p.name, run: () => save(p.id, p.name) }))).filter(Boolean), anchor);
}

const FEAT_LIB_TABS = {
  genres: async (body) => {
    const list = await API.soft("library.genres", {}, []);
    if (!body.isConnected) return;
    if (!list || !list.length) { body.innerHTML = emptyState("music", t("genres.empty"), t("genres.empty_hint")); return; }
    body.innerHTML = `<div class="mm-genres">${list.map((g, i) => { const c = paletteFor({ id: "genre:" + g.name.toLowerCase() }); return `<button type="button" class="mm-genre" data-g="${i}" style="--g1:${c[0]};--g2:${c[1]}"><b>${esc(g.name)}</b><span>${esc(plural("count.tracks", g.tracks))} · ${esc(fmtLong(g.duration))}</span>${g.cover ? `<img alt="" src="${esc(g.cover)}" loading="lazy">` : ""}</button>`; }).join("")}</div>`;
    body.onclick = (e) => { const b = e.target.closest("[data-g]"); if (b) go("genre", { name: list[Number(b.dataset.g)].name }); };
  },
  folders: async (body) => {
    const list = await API.soft("library.folders", {}, []);
    if (!body.isConnected) return;
    if (!list || !list.length) { body.innerHTML = emptyState("music", t("empty.library"), t("empty.library_hint")); return; }
    body.innerHTML = `<div class="mm-folders">${list.map((f, i) => `<button type="button" class="mm-folder" data-f="${i}">${icon("folder")}<span><b>${esc(f.name)}</b><em title="${esc(f.path)}">${esc(f.path)}</em></span><span class="mm-sub">${esc(plural("count.tracks", f.tracks))}</span></button>`).join("")}</div>`;
    body.onclick = (e) => { const b = e.target.closest("[data-f]"); if (b) go("folder", { path: list[Number(b.dataset.f)].path, name: list[Number(b.dataset.f)].name }); };
  },
  history: async (body) => { await historyInto(body, 300); },
};
function libTabs() {
  return ["genres", "folders", "history"].filter((k) => Cfg.get("show_" + k, true) !== false);
}
async function filteredView(el, title, kind, filter, art) {
  el.innerHTML = `<div class="mm-skel" style="height:220px"></div>`;
  const res = await API.soft("library.list", { filter, limit: 5000, sort: "added" }, null);
  if (!el.isConnected) return;
  const list = ((res && res.tracks) || []).map((x) => S.byId.get(x.id) || normTrack(x));
  el.innerHTML = headBlock({ kind, title: `<h1 class="mm-h1">${esc(title)}</h1>`, sub: esc(plural("count.tracks", list.length) + " · " + fmtLong(totalDuration(list))), art: art || artImg(list[0] || { id: title }, "album") }) + `<div id="list"></div>`;
  bindHeadPlay(el, () => list, title);
  if (!list.length) el.querySelector("#list").innerHTML = emptyState("search", t("empty.nothing"), "");
  else trackList(el.querySelector("#list"), list, { from: title, sortView: filter.split(":")[0], sortDefault: "added:desc" });
  return el;
}
VIEWS.genre = async (el, p) => {
  const c = paletteFor({ id: "genre:" + String(p.name).toLowerCase() });
  await filteredView(el, p.name, t("kind.genre"), "genre:" + p.name, `<span class="mm-genre-art" style="--g1:${c[0]};--g2:${c[1]}">${icon("tag")}</span>`);
};
VIEWS.folder = async (el, p) => {
  const host = await filteredView(el, p.name || p.path, t("kind.folder"), "folder:" + p.path, `<span class="mm-genre-art">${icon("folder")}</span>`);
  if (host) host.querySelector(".mm-sub").insertAdjacentHTML("afterend", `<p class="mm-sub mm-path">${esc(p.path)}</p>`);
};

async function historyInto(host, limit) {
  const rows = await API.soft("library.history", { limit }, []);
  if (!host.isConnected) return;
  if (!rows || !rows.length) { host.innerHTML = emptyState("music", t("history.empty"), t("stats.no_history_hint")); return; }
  const locale = Memu.lang === "en" ? "en-US" : "ru-RU";
  const dayOf = (at) => new Date(at * 1000).toLocaleDateString(locale, { weekday: "long", day: "numeric", month: "long" });
  let last = "";
  const tracks = rows.map((r) => S.byId.get(r.track.id) || normTrack(r.track));
  host.innerHTML = `<div class="mm-hist">${rows.map((r, i) => {
    const d = dayOf(r.at); const head = d !== last ? `<h3 class="mm-hist-day">${esc(d)}</h3>` : ""; last = d;
    const x = tracks[i];
    return `${head}<div class="mm-hist-row" data-i="${i}"><span class="mm-art">${artImg(x)}</span><div><b>${esc(x.title)}</b><span>${esc(x.artist || "")}</span></div>
      <em>${esc(r.device === "phone" ? t("history.phone") : "")}</em><time>${esc(new Date(r.at * 1000).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" }))}</time>
      <button type="button" class="mm-icon sm" data-more aria-label="${esc(t("more"))}">${icon("more")}</button></div>`;
  }).join("")}</div>`;
  host.onclick = (e) => {
    const r = e.target.closest("[data-i]"); if (!r) return;
    const x = tracks[Number(r.dataset.i)];
    if (e.target.closest("[data-more]")) { trackMenu(x, e.target.closest("[data-more]")); return; }
    playFrom([...new Map(tracks.map((tr) => [tr.id, tr])).values()], x, t("lib.history"));
  };
  host.oncontextmenu = (e) => { const r = e.target.closest("[data-i]"); if (!r) return; e.preventDefault(); trackMenu(tracks[Number(r.dataset.i)], { x: e.clientX, y: e.clientY }); };
}
VIEWS.history = async (el) => {
  el.innerHTML = `<h1 class="mm-h1">${esc(t("lib.history"))}</h1><p class="mm-sub">${esc(t("history.hint"))}</p><div id="hist"></div>`;
  await historyInto(el.querySelector("#hist"), 1000);
};

VIEWS.dupes = async (el) => {
  el.innerHTML = `<h1 class="mm-h1">${esc(t("dupes.title"))}</h1><p class="mm-sub">${esc(t("dupes.hint"))}</p><div id="dupes"><div class="mm-skel" style="height:120px"></div></div>`;
  const groups = await API.soft("library.duplicates", {}, []);
  const box = el.querySelector("#dupes");
  if (!box || !box.isConnected) return;
  if (!groups || !groups.length) { box.innerHTML = emptyState("music", t("dupes.none"), t("dupes.none_hint")); return; }
  box.innerHTML = `<div class="mm-toolbar"><span class="mm-sub">${esc(t("dupes.found", { n: groups.length }))}</span><span class="mm-grow"></span><button type="button" class="mm-btn tint sm" id="dupes-all">${icon("trash")}${esc(t("dupes.keep_best_all"))}</button></div>
    ${groups.map((g, gi) => `<section class="mm-dupe" data-g="${gi}"><header><b>${esc(g.tracks[0].title)}</b><span>${esc(g.tracks[0].artist || "")}</span><span class="mm-grow"></span><button type="button" class="mm-btn ghost sm" data-keep>${esc(t("dupes.keep_best"))}</button></header>
      ${g.tracks.map((x, i) => `<div class="mm-dupe-row${i === 0 ? " best" : ""}" data-i="${i}"><span class="mm-art">${artImg(x)}</span><div><b>${esc(x.title)}</b><span>${esc([(x.file_ext || "").toUpperCase(), x.bitrate ? x.bitrate + " kbps" : "", fmtTime(x.duration), sourceName(x.source || "local"), plural("count.plays", x.plays || 0)].filter(Boolean).join(" · "))}</span></div>
        ${i === 0 ? `<em class="mm-pill">${esc(t("dupes.best"))}</em>` : ""}<button type="button" class="mm-icon sm" data-play aria-label="${esc(t("play"))}">${icon("play")}</button><button type="button" class="mm-icon sm" data-del aria-label="${esc(t("menu.delete"))}">${icon("trash")}</button></div>`).join("")}</section>`).join("")}`;
  box.onclick = (e) => {
    if (e.target.closest("#dupes-all")) { deleteTracks(groups.flatMap((g) => g.tracks.slice(1)).map((x) => S.byId.get(x.id) || x)); return; }
    const sec = e.target.closest("[data-g]"); if (!sec) return;
    const g = groups[Number(sec.dataset.g)];
    if (e.target.closest("[data-keep]")) { deleteTracks(g.tracks.slice(1).map((x) => S.byId.get(x.id) || x)); return; }
    const r = e.target.closest("[data-i]"); if (!r) return;
    const x = S.byId.get(g.tracks[Number(r.dataset.i)].id) || g.tracks[Number(r.dataset.i)];
    if (e.target.closest("[data-play]")) playFrom(g.tracks, x, t("dupes.title"));
    if (e.target.closest("[data-del]")) deleteTracks([x]);
  };
};

(function addSearchNav() {
  const nav = $("#nav"), home = nav && nav.querySelector('[data-go="home"]');
  if (!home || nav.querySelector('[data-go="search"]')) return;
  home.insertAdjacentHTML("afterend", `<button type="button" data-go="search">${icon("search")}<span data-i18n="gsearch.title">${esc(t("gsearch.title"))}</span></button>`);
})();

window.MemuMusicFeat = { Cfg, Sel, editTracks, editPlaylist, smartBuilder, smartManager, deleteTracks, startRadio, libraryChanged, playlistPicker };
