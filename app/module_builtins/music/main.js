

const t = (key, vars) => Memu.t(key, vars);
const $ = (selector, root) => (root || document).querySelector(selector);
const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
const esc = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const root = document.documentElement;
const saved = Memu.saved || {};

function fmtTime(sec) {
  sec = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
}
function fmtLong(sec) {
  const min = Math.round((Number(sec) || 0) / 60);
  return min >= 60 ? t("dur.hours", { h: Math.floor(min / 60), m: min % 60 }) : t("dur.minutes", { m: min });
}
function plural(key, count) { return t(key, { count, n: count.toLocaleString(Memu.lang === "en" ? "en-US" : "ru-RU") }); }
function toast(text) {
  const node = document.createElement("div");
  node.className = "mm-toast"; node.textContent = text;
  $("#toasts").appendChild(node);
  setTimeout(() => node.remove(), 3300);
}
function icon(name) { return `<svg><use href="#i-${name}"/></svg>`; }
function debounce(fn, ms) { let id = 0; return (...a) => { clearTimeout(id); id = setTimeout(() => fn(...a), ms); }; }
function isTyping(target) { return !!(target && target.closest && target.closest("input, textarea, select, [contenteditable='true']")); }

const S = {
  view: "home", params: {}, tracks: [], total: 0, loaded: false, playlists: [], jobs: [],
  prefs: Object.assign({ lite: false, viz: true, npStyle: "card", coverAccent: true, lib: { tab: "tracks", sort: "added", filter: "all" } }, saved.prefs || {}),
  byId: new Map(),
};
const VIEWS = {};
const SETTINGS_TABS = [];
let ambient = null;
function savePrefs() { try { Memu.save({ prefs: S.prefs }); } catch (e) {  } }

/* @memu-part:art.js */
/* @memu-part:theme.js */
/* @memu-part:api.js */
/* @memu-part:mock.js */
/* @memu-part:player.js */
/* @memu-part:fx.js */
/* @memu-part:views.js */
/* @memu-part:lyrics-sync.js */
/* @memu-part:np.js */
/* @memu-part:feat-core.js */
/* @memu-part:feat-menu.js */
/* @memu-part:feat-editor.js */
/* @memu-part:feat-smart.js */
/* @memu-part:feat-settings.js */
/* @memu-part:feat-extra.js */
/* @memu-part:feat-online.js */
/* @memu-part:feat-lib.js */
/* @memu-part:integration/settings-widgets.js */

async function loadLibrary() {
  const first = await API.soft("library.list", { sort: "added", limit: 2000, offset: 0 }, { tracks: [], total: 0 });
  let tracks = (first && first.tracks) || [];
  S.total = (first && first.total) || tracks.length;
  while (tracks.length < S.total && tracks.length < 50000) {
    const page = await API.soft("library.list", { sort: "added", limit: 2000, offset: tracks.length }, null);
    if (!page || !page.tracks || !page.tracks.length) break;
    tracks = tracks.concat(page.tracks);
  }
  setTracks(tracks);
  S.loaded = true;
}

function normTrack(x) {
  if (x && typeof x.added_at === "number") x.added_at = x.added_at ? new Date(x.added_at * 1000).toISOString() : "";
  if (x && x.album == null) x.album = "";
  return x;
}
function setTracks(tracks) {
  tracks.forEach(normTrack);
  S.tracks = tracks;
  S.byId = new Map(tracks.map((tr) => [tr.id, tr]));
}
async function loadPlaylists() {
  const list = await API.soft("playlists.list", {}, []);
  S.playlists = (Array.isArray(list) ? list : []).filter((p) => p.id !== "smart:liked").map((p) => Object.assign(p, { name: (Memu.lang === "en" ? p.name_en : p.name_ru) || p.name }));
  renderSidePlaylists();
}
async function refreshAll() { await Promise.all([loadLibrary(), loadPlaylists()]); renderSidePlaylists(); }

function go(view, params, opts) {
  opts = opts || {};
  if (!VIEWS[view]) view = "home";
  const same = S.view === view && JSON.stringify(S.params) === JSON.stringify(params || {});
  if (same && !opts.force) return;
  S.view = view; S.params = params || {};
  const app = $("#app");
  app.dataset.view = view;
  const navKey = { album: "library", artist: "library", liked: "library", playlist: "playlists", mix: "home" }[view] || view;
  $$("#nav button").forEach((b) => { if (b.dataset.go === navKey) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  $$("#side-playlists .mm-side-pl").forEach((b) => { if (view === "playlist" && b.dataset.id === S.params.id) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  const render = () => {
    const holder = $("#view");
    const fresh = holder.cloneNode(false);
    fresh.classList.toggle("instant", !!opts.instant);
    holder.replaceWith(fresh);
    $("#main").scrollTop = 0;
    return Promise.resolve(VIEWS[view](fresh, S.params)).catch((error) => {
      console.error(error);
      fresh.innerHTML = emptyState("error", t("error.title"), String(error && error.message || error));
    });
  };

  render();
}
function rerender() { go(S.view, S.params, { force: true, instant: true }); }

function renderSidePlaylists() {
  const box = $("#side-playlists");
  box.innerHTML = (likedHidden() ? "" : `<button type="button" class="mm-side-pl" data-liked="1"><span class="mm-art" style="background:linear-gradient(135deg,#6d4aff,#ff5c8a);display:grid;place-items:center;color:#fff">${icon("heart")}</span><span style="min-width:0"><b>${esc(t("liked.title"))}</b><span>${esc(plural("count.tracks", S.tracks.filter((x) => x.liked).length))}</span></span></button>`)
    + S.playlists.map((p) => `<button type="button" class="mm-side-pl" data-id="${esc(p.id)}"><span class="mm-art">${collage(p)}</span><span style="min-width:0"><b>${esc(p.name)}</b><span>${esc(plural("count.tracks", p.count || 0))}</span></span></button>`).join("");
}
$("#side-playlists").addEventListener("click", (e) => {
  const b = e.target.closest(".mm-side-pl"); if (!b) return;
  if (b.dataset.liked) go("liked"); else go("playlist", { id: b.dataset.id });
});
$("#nav").addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) go(b.dataset.go); });
$("#side-new-playlist").addEventListener("click", () => createPlaylistDialog());

function applyPalette(track) {
  const fromCover = Theme.coverAccent() && track;
  const colors = fromCover ? paletteFor(track) : Theme.accents();
  const bg = Theme.paint(colors);
  if (ambient) ambient.setColors(colors, bg);
  NP.setColors(fromCover ? colors : paletteFor(track || { id: "memu" }));
  Theme.coverBg(track);
  if (track && track.cover && !track.accent) extractPalette(track.cover).then((c) => {
    if (!c) return;
    track.accent = c;
    if (!API.mocked) API.soft("library.update", { id: track.id, accent: c }, null);
    if (Player.current() === track && Theme.coverAccent()) applyPalette(track);
  });
}

function extractPalette(url) {
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      try {
        const c = document.createElement("canvas"); c.width = c.height = 24;
        const g = c.getContext("2d"); g.drawImage(img, 0, 0, 24, 24);
        const d = g.getImageData(0, 0, 24, 24).data;
        let best = null, score = -1;
        for (let i = 0; i < d.length; i += 4) {
          const [, s, l] = rgbToHsl([d[i], d[i + 1], d[i + 2]]);
          const sc = s * (1 - Math.abs(l - 50) / 50);
          if (sc > score) { score = sc; best = [d[i], d[i + 1], d[i + 2]]; }
        }
        resolve(best ? "#" + best.map((v) => v.toString(16).padStart(2, "0")).join("") : null);
      } catch (e) { resolve(null); }
    };
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

function applyCalm() {
  const calm = Theme.ui.motion !== "full" || Fx.reduce.matches;
  root.toggleAttribute("data-calm", calm);
  if (ambient) ambient.kick();
  NP.applyCalm();
}
Fx.reduce.addEventListener && Fx.reduce.addEventListener("change", applyCalm);

function takeLink(text) {
  const url = firstUrl(text) || (detectLocal(text) ? String(text).trim() : "");
  if (!url) return false;
  NP.close();
  go("download", { url }, { force: true });
  return true;
}
document.addEventListener("paste", (e) => {
  if (isTyping(e.target)) return;
  const text = e.clipboardData && e.clipboardData.getData("text");
  if (text && takeLink(text)) { e.preventDefault(); toast(t("drop.got")); }
});
let dragDepth = 0;
let localDrag = false;
const hideLinkDrop = () => { dragDepth = 0; $("#drop").hidden = true; };
document.addEventListener("dragstart", (e) => {

  const reorder = e.target.closest?.('[draggable="true"]');
  if (e.target.closest?.("img") && !reorder) { e.preventDefault(); hideLinkDrop(); return; }
  localDrag = true;
}, true);
document.addEventListener("dragend", () => { localDrag = false; hideLinkDrop(); });
window.addEventListener("blur", () => { localDrag = false; hideLinkDrop(); });
document.addEventListener("drop", () => { hideLinkDrop(); queueMicrotask(() => { localDrag = false; }); });
const externalDrag = (e) => { const types = e.dataTransfer ? [...e.dataTransfer.types] : []; return !localDrag && !types.some(type => type.startsWith("application/x-memu-")) && !types.includes("Files") && (types.includes("text/uri-list") || types.includes("text/plain")); };
document.addEventListener("dragenter", (e) => { if (!externalDrag(e)) return; dragDepth++; $("#drop").hidden = false; });
document.addEventListener("dragleave", (e) => { if (!externalDrag(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $("#drop").hidden = true; } });
document.addEventListener("dragover", (e) => { if (externalDrag(e)) e.preventDefault(); });
document.addEventListener("drop", (e) => {
  if (!externalDrag(e)) return;
  e.preventDefault(); dragDepth = 0; $("#drop").hidden = true;
  const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
  if (!takeLink(text)) toast(t("drop.not_link"));
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (closeOverlays()) return; if (NP.isOpen()) { NP.close(); return; } if (Sel.size()) { Sel.clear(); return; } }
  handleHotkey(e);
});

async function toggleLike(track) {
  const liked = !track.liked;
  track.liked = liked;
  const lib = S.byId.get(track.id); if (lib && lib !== track) lib.liked = liked;
  Player.st.queue.forEach((q) => { if (q.id === track.id) q.liked = liked; });
  $$(`[data-like="${CSS.escape(track.id)}"]`).forEach((b) => b.setAttribute("aria-pressed", String(liked)));
  updateBar();
  renderSidePlaylists();
  try { await API.call("library.like", { id: track.id, liked }); libraryChanged({ playlistsOnly: true, quiet: true }); }
  catch (error) { track.liked = !liked; if (lib) lib.liked = !liked; updateBar(); toast(t("error.like")); }
}

function updateBar() {
  const tr = Player.current();
  $("#bar-title").textContent = tr ? tr.title : t("player.idle");
  $("#bar-artist").textContent = tr ? tr.artist : t("player.idle_hint");
  const img = $("#bar-img");
  if (tr) { img.dataset.fb = coverArt(tr.id, "", tr.title); img.src = tr.cover || img.dataset.fb; } else img.removeAttribute("src");
  const like = $("#bar-like");
  like.hidden = !tr; like.dataset.like = tr ? tr.id : ""; like.setAttribute("aria-pressed", String(!!(tr && tr.liked)));
}
function updatePlayButtons() {
  const playing = Player.st.playing;
  root.toggleAttribute("data-playing", playing);
  $$("#play, [data-act=play]").forEach((b) => {
    b.querySelector("use").setAttribute("href", playing ? "#i-pause" : "#i-play");
    const label = t(playing ? "pause" : "play"); b.title = label; b.setAttribute("aria-label", label);
  });
}
function updateModes() {
  const st = Player.st;
  $$("#shuffle, [data-act=shuffle]").forEach((b) => b.setAttribute("aria-pressed", String(st.shuffle)));
  $$("#repeat, [data-act=repeat]").forEach((b) => {
    b.setAttribute("aria-pressed", String(st.repeat !== "off"));
    b.querySelector("use").setAttribute("href", st.repeat === "one" ? "#i-repeat1" : "#i-repeat");
    b.title = t("repeat." + st.repeat);
  });
  $("#speed-label").textContent = st.speed + "×";
  $("#eq-label").textContent = t("eq.preset." + st.eqPreset);
  const vol = $("#volume"); vol.value = String(st.muted ? 0 : st.volume); vol.style.setProperty("--v", (st.muted ? 0 : st.volume) * 100 + "%");
  $("#mute use").setAttribute("href", st.muted || st.volume === 0 ? "#i-mute" : "#i-volume");
}

function paintTime() {
  const { cur, dur, buf } = Player.times();
  const p = dur ? Math.min(1, cur / dur) : 0, b = dur ? Math.min(1, buf / dur) : 0;
  $$(".mm-seek").forEach((s) => { if (!s.classList.contains("drag")) { s.style.setProperty("--p", p.toFixed(4)); s.style.setProperty("--b", b.toFixed(4)); s.setAttribute("aria-valuenow", String(Math.round(p * 100))); } });
  const c = fmtTime(cur), d = fmtTime(dur);
  if ($("#t-cur").textContent !== c) { $("#t-cur").textContent = c; $("#np-cur").textContent = c; }
  if ($("#t-dur").textContent !== d) { $("#t-dur").textContent = d; $("#np-dur").textContent = d; }
  NP.syncLyrics(cur);
}
function timeLoop() { paintTime(); }
Player.on("tick", () => { if (!Player.st.playing || root.hasAttribute("data-calm")) paintTime(); });
Player.on("time", paintTime);
Player.on("state", () => {
  updatePlayButtons();
  if (Player.st.playing && !root.hasAttribute("data-calm")) Fx.add(timeLoop); else { Fx.remove(timeLoop); paintTime(); }
  if (ambient) ambient.kick();
  NP.onState();
});
Player.on("track", (tr) => {
  updateBar(); applyPalette(tr); NP.onTrack(tr); markPlaying(); paintTime();
  document.title = tr ? tr.title + " · " + tr.artist : "MemuMusic";
});
Player.on("mode", updateModes);
Player.on("volume", updateModes);
Player.on("queue", () => { renderQueue(); });
Player.on("error", () => toast(t("error.play")));
Player.on("sleep", () => NP.onSleep());

function bindSeek(el) {
  let dragging = false;
  const ratio = (e) => { const r = el.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)); };
  el.addEventListener("pointerdown", (e) => { dragging = true; el.classList.add("drag"); el.setPointerCapture(e.pointerId); el.style.setProperty("--p", ratio(e)); });
  el.addEventListener("pointermove", (e) => { if (dragging) el.style.setProperty("--p", ratio(e)); });
  el.addEventListener("pointerup", (e) => { if (!dragging) return; dragging = false; el.classList.remove("drag"); Player.seek(ratio(e) * Player.times().dur); });
  el.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); Player.seek(Player.times().cur + (e.key === "ArrowLeft" ? -5 : 5)); }
  });
}
$$(".mm-seek").forEach(bindSeek);
$("#play").addEventListener("click", () => Player.toggle());
$("#prev").addEventListener("click", () => Player.prev());
$("#next").addEventListener("click", () => Player.next());
$("#shuffle").addEventListener("click", () => Player.setShuffle());
$("#repeat").addEventListener("click", () => Player.cycleRepeat());
$("#mute").addEventListener("click", () => Player.toggleMute());
$("#volume").addEventListener("input", (e) => Player.setVolume(Number(e.target.value)));
$("#bar-like").addEventListener("click", () => { const tr = Player.current(); if (tr) toggleLike(tr); });
$("#bar-cover").addEventListener("click", () => NP.open());
$("#open-lyrics").addEventListener("click", () => NP.open("lyrics"));
$("#open-queue").addEventListener("click", toggleQueueDrawer);
$("#open-eq").addEventListener("click", (e) => openPop("eq", e.currentTarget));
$("#bar").addEventListener("wheel", (e) => { if (e.target.closest("#volume, #mute")) { e.preventDefault(); Player.setVolume(Player.st.volume - Math.sign(e.deltaY) * .05); } }, { passive: false });

document.addEventListener("error", (e) => {
  const img = e.target;
  if (img && img.tagName === "IMG" && img.dataset.fb && img.src !== img.dataset.fb) img.src = img.dataset.fb;
}, true);

Memu.onTheme(() => { Theme.apply(); applyPalette(Player.current()); });

if (Memu.on) Memu.on("navigate", (d) => {
  const route = String((d && d.route) || "");
  if (route === "np") setTimeout(() => { if (Player.current()) NP.open(); }, 250);
  else if (route === "download") go("download");
});
Memu.onLanguage(() => {
  renderSidePlaylists(); updateBar(); updateModes(); updatePlayButtons(); NP.relabel(); rerender();
});

let pollTimer = 0;
async function pollJobs() {
  clearTimeout(pollTimer);
  const jobs = await API.soft("download.jobs", {}, null);
  if (Array.isArray(jobs)) {
    const before = new Map(S.jobs.map((j) => [j.job_id, j.status]));
    S.jobs = jobs;
    const finished = jobs.filter((j) => j.status === "done" && before.has(j.job_id) && before.get(j.job_id) !== "done");
    if (finished.length) { await libraryChanged({ now: true, quiet: S.view === "download" }); toast(t("download.done_toast", { title: finished[0].title || "" })); }
    if (typeof onJobsUpdate === "function") onJobsUpdate();
  }
  const active = S.jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  const badge = $("#jobs-badge"); badge.hidden = !active; badge.textContent = String(active);
  if (active && !document.hidden) pollTimer = setTimeout(pollJobs, S.view === "download" ? 1000 : 3000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.jobs.some((j) => j.status === "queued" || j.status === "running")) pollJobs(); });

const Connect = (() => {
  let timer = 0, version = 0, sinceCmd = -1;
  const st = Player.st;
  function state() {
    const tr = Player.current(), tm = Player.times();
    return { track_id: tr ? tr.id : "", position: Math.round(tm.cur * 10) / 10, playing: st.playing,
      queue: st.order.map((i) => st.queue[i] && st.queue[i].id).filter(Boolean).slice(0, 1000), index: st.index, volume: st.volume,
      shuffle: st.shuffle, repeat: st.repeat, device: "pc", device_name: "MemuMusic", duration: tm.dur, context: { name: st.from || "" }, speed: st.speed, at: Date.now() };
  }

  function report(now) { if (API.mocked) return; clearTimeout(timer); timer = setTimeout(send, now === true ? 0 : 400); }
  async function send() { clearTimeout(timer); await API.soft("player.update", state(), null); if (st.playing) timer = setTimeout(send, 15000); }
  async function tracksFor(ids) {
    const out = [];
    for (const id of ids.slice(0, 1000)) { const x = S.byId.get(id) || await API.soft("library.get", { id }, null); if (x && x.id) out.push(x); }
    return out;
  }
  async function apply(c) {
    const a = c.args || {};
    switch (c.cmd) {
      case "play": return Player.play();
      case "pause": case "stop": case "handoff": return Player.pause();
      case "toggle": return Player.toggle();
      case "next": return Player.next();
      case "prev": return Player.prev();
      case "seek": return Player.seek(Number(a.position ?? a.value) || 0);
      case "volume": return Player.setVolume(Number(a.value ?? a.volume));
      case "shuffle": return Player.setShuffle(a.value === undefined ? undefined : !!a.value);
      case "repeat": { const want = String(a.value || ""); for (let i = 0; i < 3 && st.repeat !== want && ["off", "all", "one"].includes(want); i++) Player.cycleRepeat(); return; }
      case "play_track": case "set_queue": {
        const ids = Array.isArray(a.queue) && a.queue.length ? a.queue : a.track_id ? [a.track_id] : [];
        const list = await tracksFor(ids);
        if (!list.length) return;
        Player.playList(list, a.track_id || list[0].id, (a.context && a.context.name) || (c.from === "phone" ? t("connect.phone") : ""));
        if (a.position) setTimeout(() => Player.seek(Number(a.position)), 400);
        return;
      }
      default: return;
    }
  }
  async function listen() {
    const first = await API.soft("player.state", {}, null);
    if (!first) return;
    version = first.version || 0; sinceCmd = first.cmd_seq || 0;
    for (;;) {
      const r = await API.soft("player.wait", { since: version, since_cmd: sinceCmd, timeout: 25 }, null);
      if (!r) { await new Promise((ok) => setTimeout(ok, 10000)); continue; }
      if (r.state && r.state.version) version = r.state.version;
      for (const c of r.commands || []) {
        sinceCmd = Math.max(sinceCmd, c.seq || 0);
        if (c.target && !["pc", "desktop", "memuai"].includes(c.target)) continue;
        try { await apply(c); } catch (e) { console.warn("[music] remote", e); }
      }
    }
  }

  async function adopt() {
    const s = await API.soft("player.state", {}, null);
    if (!s || s.device_name !== "MemuAI" || !s.track_id) return;
    const age = (s.server_time || 0) - (s.updated_at || 0);
    const mode = s.context && s.context.handoff ? s.context.handoff : s.playing && age < 40 ? "play" : "";
    if (!mode || age > 120) return;
    await API.soft("player.command", { cmd: "handoff", target: "dock" }, null);
    const list = await tracksFor(Array.isArray(s.queue) && s.queue.length ? s.queue : [s.track_id]);
    if (!list.length) return;
    if (typeof s.volume === "number") Player.setVolume(s.volume);
    Player.playList(list, s.track_id, (s.context && s.context.name) || "", { autoplay: mode === "play", position: Number(s.position_now || s.position) || 0 });
  }
  if (!API.mocked) {
    const now = () => report(true);
    Player.on("track", now); Player.on("state", now); Player.on("time", now); Player.on("mode", () => report());
  }
  return { async start() { if (API.mocked) return; await adopt(); listen(); }, report };
})();

window.addEventListener("pagehide", () => { try { Memu.save({ player: Player.snapshot(), prefs: S.prefs }); } catch (e) {  } });

async function boot() {
  ambient = Fx.Ambient($("#ambient"), { w: 72, h: 44, strength: .9 });
  applyCalm();
  applyPalette(null);
  Theme.coverBg(null);
  updateBar(); updateModes(); updatePlayButtons();
  go("home", {}, { instant: true });
  await refreshAll();
  const restoreFrom = saved.player;
  if (restoreFrom && Array.isArray(restoreFrom.queue)) {
    restoreFrom.queue = restoreFrom.queue.map((q) => (q && S.byId.get(q.id)) || q).filter((q) => q && q.id);
  }
  Player.restore(restoreFrom);
  rerender();
  pollJobs();
  Connect.start();
  Memu.ready && Memu.ready();
}
window.MemuMusic = { S, Theme, API, Player, go, NP: () => NP, Fx, detectLocal, coverArt, pollJobs, setTracks, rerender, refresh: refreshAll,

  dev: { MockMusic, Sel, editTracks, editPlaylist, smartBuilder, smartManager, toastAction } };
boot();
